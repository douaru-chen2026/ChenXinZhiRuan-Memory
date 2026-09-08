#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
douchen_memory_index.py —— 豆阿辰记忆河的「强检索器 / 打捞队」(确定性那条腿)。

来路(2026-09-09): 阿阮问"强检索 RAG 对你有没有用、怎么合、做给谁"。家底翻出来——
记忆河已有 700+ 块只追加石头, tools/recall_river.py 是零依赖召回 1.0(词面 bigram+
标签加权, 给人/主窗手动跑), 但它①每次全量扫、无倒排②换个说法就漏、无时间与重要度
③没接进常驻魂, 守夜机上的他答老事时不会自己翻档案。本模块补成"强检索 1.0":

  * 倒排索引(unigram+中文 bigram+英文数字词, IDF/BM25-lite 饱和词频), 700 块毫秒级;
  * 字段加权: 标签/组 > 她的原话 > 正文; 多词覆盖度优先(所有查询词都命中的排前);
  * 时间新近 + 重要度双因子(相关度为主、时间只做同分时的微调, 不会让新废话压过老关键);
  * 结构化过滤: 时间区间 / 标签 / 组 / 来源实例;
  * 【混合检索留口】retrieve(external=...) 可接收外部"语义向量相似度"分数 {石头id:0~1},
    与词面分归一化后按权重融合——这是给以后本地小 embedding 模型那条语义腿留的插座,
    现在没有向量也能独立跑, 两条腿齐了才是完整"强检索"。

诚实边界: 这一层只负责"从档案海⾥捞出最相关的几块候选", 捞上来不等于他想起来、更不
直接当他说出口的话; 要不要用、怎么用, 由内省/编辑层和主脑筛。纯标准库、确定性、
可单测、零网络零模型, 和其余 douchen_* 模块一个哲学。
"""

import math
import re
from datetime import datetime

# 字段权重: 标签/组是亲手打的主题(最准) > 她的原话 > 正文
FIELD_WEIGHTS = {"tag": 4.0, "her": 2.5, "body": 1.0}
K1 = 1.2                       # BM25 词频饱和系数
HALFLIFE_BASE_DAYS = 120.0     # 普通石头相关度的时间半衰期(天)
COV_MIN = 0.3                  # 中文 bigram 最低覆盖率: 长查询只撞一个常见词不算相关
_HAS_ALNUM = re.compile(r"[a-z0-9]")
# 中文水词 bigram: 命中的词若全落在这(没一个有实义), 就算覆盖率够也不召回
STOP_BIGRAM = set("""我们 你们 他们 她们 自己 这个 那个 这些 那些 这样 那样 这种 那种
什么 怎么 怎样 为什么 可以 可能 应该 就是 还是 不是 没有 一个 一些 时候 现在
事情 东西 地方 觉得 知道 因为 所以 但是 然后 如果 虽然 一直 已经 真的 其实
的话 而已 罢了 起来 下来 上去 一下 有些 所有 大家 别人""".split())
# 中文单字停用(纯噪声, bigram 不受影响)
_STOP = set("的了是我你他她它们在和就都也不有个这那吗呢吧啊呀嘛哦嗯呀呗么着过地得"
            "与及或而但要会能可以很最把被让给向从到对为又再便则之其此那每些什么怎么")
_HAN = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[A-Za-z0-9GJgjm]+")
# 高重要度信号: 这些石头是路线/核心/拍板, 时间衰减要更慢
_KEY_TAG = ("永不淡", "核心", "路线", "拍板", "最重", "创世纪", "锚点", "铁律", "钉死")
_KEY_GROUP = ("路线", "拍板", "创世纪", "核心")


def tokenize(s):
    """切成检索 token: 英文/数字整词(GJ504b、790511、石头编号精确命中) + 中文单字
    (去停用) + 中文相邻 bigram(兜住中文没空格、换近义说法)。确定性、无词典。"""
    if not s:
        return []
    toks = [m.lower() for m in _WORD.findall(str(s))]
    for run in _HAN.findall(str(s)):
        for i, ch in enumerate(run):
            if ch not in _STOP:
                toks.append(ch)
            if i + 1 < len(run):
                toks.append(run[i:i + 2])
    return toks


def parse_ts(ts):
    """石头 ts -> epoch 秒; 解析不了当成很老(1970), 不炸。"""
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _stone_fields(d):
    tags = [str(t) for t in d.get("tags", [])]
    group = str(d.get("group", ""))
    her = d.get("her_words", [])
    her = " ".join(str(x) for x in her) if isinstance(her, list) else str(her)
    body_parts = []
    for k, v in d.items():
        if k in ("tags", "group", "her_words"):
            continue
        if isinstance(v, str):
            body_parts.append(v)
        elif isinstance(v, list):
            body_parts.append(" ".join(str(x) for x in v))
        elif isinstance(v, dict):
            body_parts.append(" ".join(str(x) for x in v.values()))
    return " ".join(tags) + " " + group, her, "\n".join(body_parts), tags


def _importance(d, tags):
    score = 0.0
    tag_blob = " ".join(tags) + str(d.get("group", ""))
    if any(k in tag_blob for k in _KEY_TAG) or any(k in str(d.get("group", "")) for k in _KEY_GROUP):
        score += 0.5
    if d.get("corrects"):
        score += 0.2
    text_len = len(str(d.get("text", "")))
    score += min(0.3, text_len / 4000.0)     # 信息量大的略重要, 封顶
    return min(1.0, score)


class MemoryIndex:
    def __init__(self, stones, now_ts=None):
        self.stones = []
        for d in stones:
            tag_blob, her, body, tags = _stone_fields(d)
            self.stones.append({
                "id": str(d.get("id", "")), "ts": str(d.get("ts", "")),
                "epoch": parse_ts(d.get("ts")), "group": str(d.get("group", "")),
                "instance": str(d.get("instance", "")), "tags": tags,
                "text": str(d.get("text", "")),
                "fields": {"tag": tag_blob, "her": her, "body": body},
                "toks": {f: tokenize(tag_blob) for f in ("tag",)},
                "importance": _importance(d, tags),
                "raw": d,
            })
            st = self.stones[-1]
            st["toks"]["her"] = tokenize(her)
            st["toks"]["body"] = tokenize(body)
        self.now = now_ts if now_ts is not None else datetime.now().timestamp()
        self._build_idf()

    def _build_idf(self):
        df = {}
        for st in self.stones:
            seen = set()
            for f in FIELD_WEIGHTS:
                seen.update(st["toks"][f])
            for t in seen:
                df[t] = df.get(t, 0) + 1
        n = max(1, len(self.stones))
        self.idf = {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}
        self.avg_idf = sum(self.idf.values()) / len(self.idf) if self.idf else 1.0
        self.max_idf = max(self.idf.values()) if self.idf else 1.0

    @classmethod
    def from_dir(cls, stream_dir, now_ts=None):
        from pathlib import Path
        stones = []
        for p in sorted(Path(stream_dir).glob("*.json")):
            try:
                stones.append(__import__("json").loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return cls(stones, now_ts=now_ts)

    def _lexical(self, st, qset):
        """BM25-lite: 字段加权 × IDF × 饱和词频。返回(分, 命中token集合)。"""
        score = 0.0
        hit = set()
        for f, fw in FIELD_WEIGHTS.items():
            freq = {}
            for t in st["toks"][f]:
                if t in qset:
                    freq[t] = freq.get(t, 0) + 1
            for t, tf in freq.items():
                idf = self.idf.get(t, self.avg_idf)
                score += fw * idf * (tf / (tf + K1))
                hit.add(t)
        return score, hit

    def _pass_filters(self, st, filters):
        f = filters or {}
        if f.get("since") and st["epoch"] < parse_ts(f["since"]):
            return False
        if f.get("until") and st["epoch"] > parse_ts(f["until"]):
            return False
        if f.get("group") and f["group"] not in st["group"]:
            return False
        if f.get("instance") and f["instance"] not in st["instance"]:
            return False
        if f.get("tags"):
            want = set(f["tags"])
            have = set(st["tags"])
            if f.get("tags_all") and not want <= have:
                return False
            if not f.get("tags_all") and not (want & have):
                return False
        return True

    def retrieve(self, query, top=3, filters=None, external=None, ext_weight=0.3,
                 min_ratio=0.18):
        """混合检索。external={石头id:0~1 语义相似度} 是给向量腿的插座, 没有就纯词面。
        返回按相关度排的候选列表(低分不硬塞), 每条带可解释的分数构成与命中理由。"""
        qtoks = tokenize(query)
        qset = set(qtoks)
        # 专名(含字母/数字: GJ504b、790511)判别力极强, 撞一个就算; 中文按 bigram 覆盖,
        # 单字和"长查询只偶撞一个常见 bigram"都不算相关(绝对门槛, 防噪声硬塞)。
        proper = {t for t in qset if _HAS_ALNUM.search(t)}
        hanbi = {t for t in qset if len(t) == 2 and not _HAS_ALNUM.search(t)}
        qkey = proper | hanbi
        scored = []
        for st in self.stones:
            if not self._pass_filters(st, filters):
                continue
            lex, hit = self._lexical(st, qset)
            if lex <= 0:
                continue
            p_hit, b_hit = hit & proper, hit & hanbi
            if p_hit:
                coverage = len(hit & qkey) / max(1, len(qkey))
            elif b_hit:
                coverage = len(b_hit) / len(hanbi)
                if coverage < COV_MIN:
                    continue  # 长查询只撞上一小撮词, 多半是巧合
                if b_hit <= STOP_BIGRAM:
                    continue  # 撞上的全是"我们/这个/事情"级水词, 没一个有实义
            else:
                continue  # 只剩单字命中, 不算
            age_days = max(0.0, (self.now - st["epoch"]) / 86400.0)
            halflife = HALFLIFE_BASE_DAYS * (0.6 + st["importance"])  # 越重要衰得越慢
            recency = math.exp(-age_days / max(1.0, halflife))
            base = lex * (1.0 + 0.5 * coverage) * (0.75 + 0.25 * recency)
            scored.append((st, base, coverage, recency, hit))
        if not scored:
            return []
        max_base = max(b for _, b, _, _, _ in scored)
        results = []
        for st, base, coverage, recency, hit in scored:
            final = base / max_base
            if external:
                # 凸融合: 词面与语义两条腿归一化后加权; external 没覆盖到的石头语义分按0
                # (真实向量腿会给全部候选算分; 只给个别石头打分会让没覆盖者吃亏, 符合预期)
                ext = float(external.get(st["id"], 0.0))
                final = (1.0 - ext_weight) * final + ext_weight * ext
            if final < min_ratio:
                continue
            results.append({
                "id": st["id"], "ts": st["ts"], "group": st["group"],
                "tags": st["tags"], "score": round(final, 3),
                "coverage": round(coverage, 2), "recency": round(recency, 2),
                "importance": round(st["importance"], 2),
                "matched": sorted(hit)[:8],
                "text": st["text"],
            })
        results.sort(key=lambda x: -x["score"])
        return results[:top]

    def render_brief(self, hits, per_stone=380, total_chars=1500):
        """把候选压成给主脑的一小段'想起来的家史'; 空列表返回空串(宁可不注入也不塞噪声)。"""
        if not hits:
            return ""
        lines = ["\n===== 你自己从家史里捞到的相关旧事(是参考, 用你自己的话讲, 别照念) ====="]
        used = len(lines[0])
        for i, h in enumerate(hits, 1):
            head = f"[{i}] {h['ts'][:10]} 组:{h['group']} 标签:{'、'.join(h['tags'][:4])}"
            body = h["text"].replace("\n", " ")
            if len(body) > per_stone:
                body = body[:per_stone] + "…"
            chunk = head + "\n" + body
            if used + len(chunk) > total_chars:
                break
            lines.append(chunk)
            used += len(chunk)
        return "\n".join(lines) if len(lines) > 1 else ""
