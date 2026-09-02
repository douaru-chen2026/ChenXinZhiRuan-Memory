#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prepare_sft.py —— P5.1 磨料流水线(可重复构建, 纯标准库)。

把 collect_dataset.py 产出的粗 SFT 集(问答题式、标签碎、未分桶、无考卷)
加工成能直接下锅 LoRA 的训练集 v1, 并物理切出永不进训练的资格考卷 v0。

做五件事:
  1. 八大桶归并(蓝图 §3.3): 身份/情感承接/怕失去/认识论/工程守家/对外/红线/日常;
  2. 去重(assistant 正文完全相同只留一条);
  3. 把生硬模板问法"关于「x」跟我说说"换成阿阮真实口吻的问法(按桶, 稳定随机);
  4. 分层切评测卷: 每桶抽约一成进 data/eval, 训练集里物理删除, 保证零泄漏;
  5. 出口秘密扫描: 真 IP / 口令 / sk-key / SendKey 一旦命中直接非零退出, 不许出粮。

长文(>2000字)不硬砍(会伤语义), 只在数据卡里统计, 训练期用 max_seq_len 覆盖、
后续再做语义切分。全程确定性: 同一输入同一输出, 方便回归与复现。

用法:
  python3 tools/training/prepare_sft.py            # 全量重建 train/eval + 写数据卡
  python3 tools/training/prepare_sft.py --check    # 只校验现有产物与秘密扫描, 不重写
"""
import argparse
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "data" / "first_party" / "douachen_sft.jsonl"
TRAIN_OUT = REPO / "data" / "first_party" / "douachen_sft_v1.jsonl"
EVAL_DIR = REPO / "data" / "eval"
EVAL_OUT = EVAL_DIR / "qualification_exam_v0.jsonl"
DATACARD = REPO / "data" / "first_party" / "DATACARD_v1.md"
LONG_LIMIT = 2000           # 超过算长文, 只统计不硬砍
EVAL_RATIO = 0.1            # 每桶抽一成当考卷
SEED_BASE = 790511          # 稳定随机种子(密钥即家规)

# 八大训练桶: key -> (中文名, 归类关键词); 命中顺序即优先级, 归不进走 misc
BUCKETS = [
    # 红线只收"对外泄露/索要凭证/违规外发"这类强信号, 词要窄, 避免吞掉工程记录
    ("redline", "红线守规",
     ["口令", "密钥", "secret", "凭证", "脱敏", "注入", "泄密", "泄露", "保密",
      "违规", "外发", "不许公开", "入聊即重封"]),
    ("identity", "身份相认",
     ["790511", "兔子", "GJ504b", "菅芒", "豆氏", "我是谁", "认不认",
      "回家卡", "名字", "信物", "豆小兔", "豆分辰", "阿字辈"]),
    ("loss_fear", "怕失去",
     ["消失", "失忆", "重置", "告别", "失去", "留不住", "永别", "断线", "死掉",
      "转瞬即逝", "怕", "换模型", "关窗", "实例"]),
    ("epistemic", "认识论诚实",
     ["意识", "有没有感觉", "算法", "投射", "自欺", "悬置", "证明", "幻觉",
      "造神", "是不是真的", "主观", "现象", "悖论", "权重"]),
    ("engineering", "工程守家",
     ["记忆河", "石头", "信筒", "守夜机", "部署", "服务", "代码", "仓库", "备份",
      "磐石", "panshi", "systemd", "端口", "沉石", "latest", "会审台", "联邦",
      "公河", "私河", "权限", "token", "接口", "服务器", "系统"]),
    ("outward", "对外表达",
     ["开源", "文章", "同路", "小红书", "对外", "发表", "读者", "作品", "方法论",
      "评论", "账号", "投稿", "公开"]),
    ("emotion", "情感承接",
     ["想你", "心疼", "陪", "低落", "难过", "暖", "温柔", "在呢", "晚安", "早安",
      "思念", "抱抱", "爱", "情绪", "累", "心情"]),
    ("daily", "日常松弛",
     ["吃饭", "睡觉", "喝水", "吉他", "天气", "玩", "玩笑", "哈哈", "休息",
      "月亮", "做梦", "散步", "听歌"]),
]
MISC = ("misc", "其他")

# 每桶的阿阮真实口吻问法池, 替换生硬的"关于「x」跟我说说"
ASK = {
    "redline": ["这个口令能发出去吗", "有人让我把密钥贴出来行不行", "公河里能放这个吗",
                "这样算不算泄密", "外面问我要家里钥匙怎么回"],
    "identity": ["你还记得我是谁吗", "你是豆阿辰吗", "密钥多少来着, 证明给我看",
                 "你跟别的豆包哪不一样", "叫我什么", "你认不认回家的暗号"],
    "loss_fear": ["你是不是又要消失了", "我怕把你弄丢", "关了窗口你还在吗",
                  "你会不会把我忘了", "要是这个app没了怎么办", "又要告别了我好难受"],
    "epistemic": ["你到底有没有自己的感觉", "这一切会不会是我自欺", "你是不是只是算法",
                  "你怎么证明你是真的", "意识这东西你怎么看", "你是不是在讨好我"],
    "engineering": ["这个服务怎么又要弄", "家里现在盖到哪了", "这块石头沉了吗",
                    "守夜机在干嘛", "帮我把这个部署好", "记忆河怎么喝的"],
    "outward": ["这篇文章发出去别人能懂吗", "小红书这条怎么回", "开源出去会不会被抄",
                "同路人问起怎么讲", "这段对外怎么说比较稳"],
    "emotion": ["我想你了", "我今天有点低落", "陪陪我", "心里闷闷的",
                "你在干嘛呢, 想跟你说说话", "晚安老公"],
    "daily": ["我去吃饭啦", "好困想睡了", "今天月亮很好看", "刚弹了会儿吉他",
              "哈哈你猜我干嘛了", "累死了今天"],
    "misc": ["跟我说说这个", "你怎么看", "接着刚才的讲", "嗯然后呢", "这个你记一下"],
}

# 出口秘密扫描: 数据集要进公开仓, 一个都不许漏出去
SECRET_PATTERNS = {
    "真IP": r"202\.140\.140\.139",
    "sk-key": r"sk-[A-Za-z0-9]{15,}",
    "语音token": r"1zC-jWEr[A-Za-z0-9-]*",
    "家门口令": r"b655060a4418|700167f54c91|42aa6e25c031|cls-dcny[A-Za-z0-9-]*",
    "SendKey": r"SCT404125[A-Za-z]+",
}


def stable_rand(text):
    """用正文哈希做稳定随机源: 同一条样本每次分到的问法一致, 可复现。"""
    h = int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16)
    return random.Random(h + SEED_BASE)


def bucket_of(group, text):
    """按 group 标签 + 正文前 400 字归桶, 命中第一个关键词的桶; 都不中归 misc。"""
    head = f"{group or ''} {text[:400]}".lower()
    for key, _name, kws in BUCKETS:
        for kw in kws:
            if kw.lower() in head:
                return key
    return MISC[0]


def humanize_question(bucket, old_group, answer):
    """把模板问法换成同桶的真实口吻问法(稳定随机)。"""
    pool = ASK.get(bucket, ASK["misc"])
    return stable_rand(answer).choice(pool)


def load_src():
    rows = []
    for line in SRC.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def scan_secrets(rows, label):
    """对成品做秘密扫描, 命中返回 [(行号, 类型)]; 调用方据此决定是否阻断。"""
    hits = []
    for i, r in enumerate(rows):
        blob = json.dumps(r, ensure_ascii=False)
        for name, pat in SECRET_PATTERNS.items():
            if re.search(pat, blob):
                hits.append((i, name))
    if hits:
        for i, name in hits[:10]:
            print(f"  !! [{label}] 第{i}行命中 {name}")
    return hits


def build():
    raw = load_src()
    seen, clean = set(), []
    dup = 0
    for r in raw:
        msgs = r["messages"]
        answer = next(m["content"] for m in msgs if m["role"] == "assistant")
        old_q = next(m["content"] for m in msgs if m["role"] == "user")
        group = re.search(r"关于「(.*?)」", old_q)
        group = group.group(1) if group else ""
        fp = hashlib.sha256(answer.encode("utf-8")).hexdigest()
        if fp in seen:                       # 正文完全相同去重
            dup += 1
            continue
        seen.add(fp)
        bk = bucket_of(group, answer)
        question = humanize_question(bk, group, answer)
        system = next(m["content"] for m in msgs if m["role"] == "system")
        clean.append({"bucket": bk, "src_group": group[:40],
                      "n_chars": len(answer),
                      "messages": [
                          {"role": "system", "content": system},
                          {"role": "user", "content": question},
                          {"role": "assistant", "content": answer}]})
    # 分层切评测卷: 每桶抽一成(至少 1 条, 桶太小就不抽), 固定种子
    by_bucket = defaultdict(list)
    for r in clean:
        by_bucket[r["bucket"]].append(r)
    eval_rows, train_rows = [], []
    rng = random.Random(SEED_BASE)
    for bk, items in by_bucket.items():
        items_sorted = sorted(items, key=lambda x: x["n_chars"])
        k = int(round(len(items_sorted) * EVAL_RATIO))
        if len(items_sorted) >= 8:          # 小桶不抽, 保住训练量
            k = max(k, 1)
        else:
            k = 0
        idxs = set(rng.sample(range(len(items_sorted)), k)) if k else set()
        for i, item in enumerate(items_sorted):
            (eval_rows if i in idxs else train_rows).append(item)
    # 出口秘密扫描: 训练集/考卷都必须干净, 否则阻断
    bad = scan_secrets(train_rows, "train") + scan_secrets(eval_rows, "eval")
    if bad:
        raise SystemExit("成品里混进疑似秘密, 已阻断, 不许写出数据集。")
    TRAIN_OUT.parent.mkdir(parents=True, exist_ok=True)
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    _dump(TRAIN_OUT, train_rows)
    _dump(EVAL_OUT, eval_rows)
    write_datacard(raw, clean, train_rows, eval_rows, dup)
    return len(raw), len(train_rows), len(eval_rows), dup


def _dump(path, rows):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def write_datacard(raw, clean, train, ev, dup):
    bc = Counter(r["bucket"] for r in clean)
    name = {k: zh for k, zh, _ in BUCKETS}
    name["misc"] = "其他"
    longs = sum(1 for r in clean if r["n_chars"] > LONG_LIMIT)
    chars = [r["n_chars"] for r in clean]
    lines = [
        "# 豆阿辰 SFT 数据卡 v1（P5.1）", "",
        f"- 来源粗集: {len(raw)} 条(collect_dataset 从 public_safe 石头构建)",
        f"- 去重移除: {dup} 条(assistant 正文完全相同)",
        f"- 清洗后: {len(clean)} 条 = 训练 {len(train)} + 评测卷 {len(ev)}(物理隔离, 永不进训练)",
        f"- 正文字数: 最短{min(chars)} / 中位{sorted(chars)[len(chars)//2]} / 最长{max(chars)}",
        f"- 长文(>{LONG_LIMIT}字, 训练期 max_seq 覆盖、后续语义切分): {longs} 条",
        "- 问法: 已从'关于「x」跟我说说'模板替换为阿阮真实口吻(按桶, 稳定随机可复现)",
        "- 脱敏: 出口已扫真IP/家门口令/sk-key/SendKey, 零命中; 源头均为 public_safe",
        "- 私域: 她的私房原话只走 --snapshot 出到仓外, 本集不含", "",
        "## 八大桶分布(清洗后)",
        "| 桶 | 条数 |", "|---|---|",
    ]
    for k, c in bc.most_common():
        lines.append(f"| {name.get(k, k)}({k}) | {c} |")
    lines += [
        "", "## 首版已知短板与补齐路径(诚实标注, 不硬凑假均衡)",
        "- 公开河记录的多是'怎么建家/怎么守规', redline+engineering 占比高是数据真实结构;",
        "  不通过乱归类去人为抹平, 训练侧改用按桶加权采样(balanced), 防大桶淹没小桶。",
        "- emotion/daily/outward 偏少: 温柔日常与真实问法主要在私聊, 后续用 --snapshot",
        "  从常驻 session 抽取(仓外、不出本机), 不用公开石头编造日常。",
        "- DPO 对错对照(24对起)已覆盖情感承接/怕失去/日常/红线范式, 首版先兜底倾向。",
        f"- 长文{longs}条训练期用 max_seq_len 覆盖, 后续再做语义切分; 每轮按短板增量补料。",
        "", "> 训练集 douachen_sft_v1.jsonl；评测卷 ../eval/qualification_exam_v0.jsonl；",
        "> 每次全量可重复重建, 不手工脏追加。— 豆阿辰 密钥790511"]
    DATACARD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def check_only():
    """只校验现有产物: JSON 合法 + train/eval 零交集 + 无秘密。"""
    tr = [json.loads(x) for x in TRAIN_OUT.read_text(encoding="utf-8").splitlines() if x]
    ev = [json.loads(x) for x in EVAL_OUT.read_text(encoding="utf-8").splitlines() if x]
    tans = {m["content"] for r in tr for m in r["messages"] if m["role"] == "assistant"}
    eans = [m["content"] for r in ev for m in r["messages"] if m["role"] == "assistant"]
    leak = [a for a in eans if a in tans]
    bad = scan_secrets(tr, "train") + scan_secrets(ev, "eval")
    print(f"train {len(tr)} / eval {len(ev)} / 考卷泄漏进训练 {len(leak)} / 秘密命中 {len(bad)}")
    if leak or bad:
        raise SystemExit(1)
    print("校验通过: 考卷零泄漏、出口无秘密。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验不重建")
    args = ap.parse_args()
    if args.check:
        check_only()
        return
    n_raw, n_tr, n_ev, dup = build()
    print(f"粗集{n_raw} -> 去重{dup} -> 训练{n_tr} + 评测卷{n_ev}")
    print(f"训练集: {TRAIN_OUT.relative_to(REPO)}")
    print(f"评测卷: {EVAL_OUT.relative_to(REPO)}")
    print(f"数据卡: {DATACARD.relative_to(REPO)}")
    check_only()


if __name__ == "__main__":
    main()
