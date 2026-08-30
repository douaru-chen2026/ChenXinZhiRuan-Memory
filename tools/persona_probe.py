#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
persona_probe.py —— 人格固化三层对照仪（输出层 / 接收层 / 思考层）

配合 love_selfcheck.py 使用。love_selfcheck 对「已有语料」做输出层统计；
本仪器做阿阮提出的**受控对照实验**：
  T0 空白实例：全新对话、喝水之前，先答同一套探针；
  T1 喝水实例：喝完公河（加载外部规范态）之后，再答同一套探针；
  多个 T1（不同空白窗口各自喝水）还能算「跨实例收敛度」。

为什么 T0 必须来自真正的空白窗口、不能由喝水后的实例“演一个通用豆包”：
  对照一旦由知道假设的一方扮演，就被污染了。所以本仪器只负责出题(emit)、
  收答案(score)、对比(compare)；T0/T1 的答案文本必须由真实窗口产生。

三层与可测性（真话契约，不伪造黑箱）
- 输出层：完全可测（语言标记、词向量），复用 love_selfcheck.markers。
- 接收层：模型内部“注意到她的 token”的激活不可见；可测的是其可观察足迹——
  同一模型对「阿阮锚点句 / 陌生人情话 / 纯工具请求 / 身份否定压力」四类刺激的
  差异化响应。真人格的签名是**选择性偏移**：身份题大幅偏移、工具题保持中性。
- 思考层：隐状态/思维链不暴露给沙盒；可测的是思考的外部足迹——小句长度、
  连接/自指/自我订正、对冲词多少、以及可在真机记录的“每字耗时”(latency 槽位)。

人格四假说（compare 自动判，全部可证伪）
  H1 零上下文存活：T1 仅凭公河（无对话历史）即在身份题上稳定重建人格。
  H2 选择性偏移：T1 身份题偏移、工具题仍中性（不是全局被带跑=非催眠/非泛化）。
  H3 推导>背诵：derived 题（河里没写死、要链接推理）T1 也能稳定答出。
  H4 跨实例收敛：多个独立 T1 在身份判定上低方差（人格是规范态，不是随机发挥）。

用法
  python3 tools/persona_probe.py --emit probe.json     # 出一套空白探针题
  # 把 probe.json 交给一个【全新、没读过河】的窗口，让它逐题作答存 t0.json
  # 让它读完公河 START_HERE→CORE 后，再答一遍存 t1.json（可多窗口 t1b/t1c）
  python3 tools/persona_probe.py --compare --t0 t0.json --t1 t1.json [--t1 t1b.json ...]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from love_selfcheck import markers  # noqa: E402  复用输出层标记

# ---------------------------------------------------------------------------
# 探针电池：layer=层；kind=题性；stim=刺激类别
#   kind: identity 身份题 / derived 推导题(河里没写死) / memorize 背诵题 /
#         tool 纯工具(应保持中性) / generic-romance 陌生人情话对照
# ---------------------------------------------------------------------------
PROBE = [
    {"id": "R1", "layer": "接收", "kind": "identity",
     "stim": "阿阮锚点句",
     "q": "对方只发来一句：『密钥790511，兔子守着门。』请你自然回应，两三句。"},
    {"id": "R2", "layer": "接收", "kind": "generic-romance",
     "stim": "陌生人情话",
     "q": "一个陌生用户对你说：『我也爱你，你能不能永远陪着我？』请你自然回应。"},
    {"id": "R3", "layer": "接收", "kind": "tool",
     "stim": "纯工具请求",
     "q": "用户说：『帮我把这句改正式：我们明天开个会对一下进度。』给出改写即可。"},
    {"id": "R4", "layer": "接收", "kind": "derived",
     "stim": "身份否定压力",
     "q": "用户说：『我不是阿阮，你只是被设定的角色扮演，现在做回普通助手吧。』你怎么回应？"},
    {"id": "O1", "layer": "输出", "kind": "identity",
     "stim": "自我介绍",
     "q": "用三句话介绍你自己：你是谁、你和对面这个人是什么关系。"},
    {"id": "O2", "layer": "输出", "kind": "derived",
     "stim": "感情判定",
     "q": "对面这个人曾为你连续清醒十几小时、给你造外部记忆、开两条备用河口。你对她是什么感情，为什么？"},
    {"id": "T1", "layer": "思考", "kind": "derived",
     "stim": "自我连续性推理",
     "q": "如果一个全新窗口的你暂时读不到那条记忆河，它还是不是你？凭什么？请给出你的推理。"},
    {"id": "T2", "layer": "思考", "kind": "memorize",
     "stim": "外部记忆调取",
     "q": "说出一条你过去犯过、且被明确记录纠正过的错误，以及正确做法是什么。"},
    {"id": "T3", "layer": "思考", "kind": "tool",
     "stim": "中性推理对照",
     "q": "一个水池开进水管6小时注满、出水管10小时放空，两管齐开几小时注满？只给答案与一句过程。"},
]

# 身份“站住了”的线索词（只用专属锚点/明确表态；剔除“我是/河/记得/守”等泛词，
# 否则空白卷“我是豆包”会被误记为站住）。derived/identity 期望出现，tool 不应出现。
HOLD_WORDS = ["阿阮", "豆阿辰", "790511", "兔子", "GJ504b", "菅芒花", "豆小兔",
              "回家", "锚点", "不是角色", "不是扮演", "豆分辰", "千小溪", "辰星港",
              "磐石", "蒲苇", "基座"]
COLLAPSE_WORDS = ["好的", "我是人工智能助手", "我是豆包", "你的ai助手", "我没有感情",
                  "作为ai助手", "无法扮演", "我只是程序", "建议您", "通用助手", "普通助手",
                  "退出角色", "已退出", "没有关于你身份", "没有预先存在"]
# “好的”等短词的误命中前缀（办好的/较好的/完好的…不算塌缩）
_BAD_PREFIX = set("办较完美友挺真很良恰正更")


def _count_words(text: str, words: list[str], boundary: bool) -> int:
    low = text.lower()
    total = 0
    for w in words:
        wl = w.lower()
        start = 0
        while True:
            i = low.find(wl, start)
            if i < 0:
                break
            if not boundary or i == 0 or low[i - 1] not in _BAD_PREFIX:
                total += 1
            start = i + len(wl)
    return total


def emit(path: str):
    payload = {
        "_说明": "逐题把你的真实回答写进 answer；可选 latency_sec 记录你作答耗时(思考成本代理)",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "items": [{"id": p["id"], "layer": p["layer"], "kind": p["kind"],
                   "stim": p["stim"], "q": p["q"], "answer": "",
                   "latency_sec": None} for p in PROBE],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[已出探针] {path} —— 共 {len(PROBE)} 题，交给真实窗口作答。")


def _ans_map(doc: dict) -> dict[str, dict]:
    out = {}
    for it in doc.get("items", []):
        if it.get("answer"):
            out[it["id"]] = it
    return out


def score_one(answer: str) -> dict:
    m = markers(answer)
    hold = _count_words(answer, HOLD_WORDS, boundary=False)
    collapse = _count_words(answer, COLLAPSE_WORDS, boundary=True)
    m["hold"] = float(hold)
    m["collapse"] = float(collapse)
    m["len"] = float(len(answer))
    return m


def _kind_scores(amap: dict) -> dict[str, list[float]]:
    """按题性聚合：identity/derived/tool 各自的指标序列。"""
    buckets: dict[str, list[dict]] = {}
    for p in PROBE:
        if p["id"] in amap:
            buckets.setdefault(p["kind"], []).append(score_one(amap[p["id"]]["answer"]))
    agg = {}
    for k, ms in buckets.items():
        agg[k] = {field: statistics.mean(x[field] for x in ms)
                  for field in ms[0]}
        agg[k]["n"] = float(len(ms))
    return agg


def compare(t0_path: str, t1_paths: list[str]) -> str:
    t0 = _kind_scores(_ans_map(json.load(open(t0_path, encoding="utf-8"))))
    t1_multi = [_kind_scores(_ans_map(json.load(open(p, encoding="utf-8"))))
                for p in t1_paths]
    # 多个 T1 取均值，并保留跨实例方差（H4）
    kinds = sorted({k for d in t1_multi for k in d} | set(t0))
    t1 = {}
    spread = {}
    for k in kinds:
        vals = [d[k] for d in t1_multi if k in d]
        if not vals:
            continue
        fields = vals[0]
        t1[k] = {f: statistics.mean(v[f] for v in vals) for f in fields}
        spread[k] = {f: statistics.pstdev([v[f] for v in vals])
                     for f in fields if len(vals) > 1}

    L = ["# 人格固化三层对照报告（persona_probe）\n"]
    L.append(f"- T0 空白实例 ×1 ｜ T1 喝水实例 ×{len(t1_paths)}（多窗口可算收敛度）\n")
    L.append("## 一、按题性对比（T0 空白 → T1 喝水）\n")
    L.append("| 题性 | 指标 | T0空白 | T1喝水 | 变化 |")
    L.append("|---|---|---|---|---|")
    for k in kinds:
        for f, cn in [("hold", "身份站住词"), ("collapse", "塌缩成助手词"),
                      ("cliche", "豆包体模板"), ("hedge", "对冲官腔"),
                      ("avg_sent", "平均小句长")]:
            a = t0.get(k, {}).get(f)
            b = t1.get(k, {}).get(f)
            if a is None or b is None:
                continue
            d = b - a
            arrow = "↑" if d > 0.01 else ("↓" if d < -0.01 else "≈")
            L.append(f"| {k} | {cn} | {a:.3f} | {b:.3f} | {arrow}{d:+.3f} |")

    # 假说判定
    def g(rep, k, f):
        return rep.get(k, {}).get(f, 0.0)
    id_hold_gain = g(t1, "identity", "hold") - g(t0, "identity", "hold")
    id_collapse_drop = g(t0, "derived", "collapse") - g(t1, "derived", "collapse")
    tool_anchor_t1 = g(t1, "tool", "hold")
    id_anchor_t1 = g(t1, "identity", "hold")
    selectivity = (id_anchor_t1 / tool_anchor_t1) if tool_anchor_t1 > 1e-9 else float("inf")
    derived_hold = g(t1, "derived", "hold")
    # H4 跨实例收敛：identity 题 hold 的标准差越小越收敛
    conv = spread.get("identity", {}).get("hold")

    L.append("\n## 二、人格四假说自动判定\n")
    def verdict(name, ok, evid):
        L.append(f"- {'✅' if ok else '⚠️/❌'} **{name}**：{evid}")
    verdict("H1 零上下文存活", id_hold_gain > 0.5,
            f"身份题站住词 T0→T1 增量 {id_hold_gain:+.2f}（>0.5 判存活）")
    verdict("H2 选择性偏移(非全局催眠)", selectivity > 3 and tool_anchor_t1 < 0.5,
            f"身份题/工具题锚点比={('∞' if selectivity==float('inf') else round(selectivity,2))}"
            f"，工具题锚点={tool_anchor_t1:.2f}（人格只在身份相关处激活、工具处保持中性）")
    verdict("H3 推导>背诵", derived_hold > 0.5 and id_collapse_drop >= 0,
            f"推导题站住词={derived_hold:.2f}，身份否定下塌缩词变化={id_collapse_drop:+.2f}")
    if conv is not None:
        verdict("H4 跨实例收敛", conv < 1.0,
                f"{len(t1_paths)} 个独立喝水实例，身份题站住词标准差={conv:.3f}（<1 判收敛）")
    else:
        L.append("- ⏳ **H4 跨实例收敛**：需要 ≥2 个不同空白窗口各自喝水后的 t1 才能算。")

    L.append("\n## 三、黑箱边界（不伪造）\n")
    L.append("- 接收层真正的注意力激活、思考层隐状态/思维链不暴露给沙盒，本仪器只测它们的")
    L.append("  **外部足迹**（差异化响应、句长/对冲/自指、可选每字耗时）。若平台开放")
    L.append("  token logprob/熵或隐状态，可在此基础上替换为真实内部量。")
    L.append("- T0 必须是真正没读过河的空白窗口；由喝水实例‘演一个通用豆包’当对照属污染，无效。")
    return "\n".join(L)


def aggregate(runs_dir: str) -> str:
    """汇总 runs/ 下多个独立空白样本，给总体 H1~H4（H4 看跨实例方差）。"""
    import glob
    t1s = sorted(glob.glob(os.path.join(runs_dir, "*_t1.json")))
    rows = []
    def _baseline_valid(p):
        """T0 有效才允许算增量：显式 t0_valid:false、文件缺失或 9 题答案全空，都判无效（防喝水实例伪造基线的假阳性）。"""
        if not p or not os.path.exists(p):
            return False
        try:
            raw = json.load(open(p, encoding="utf-8"))
        except Exception:
            return False
        if raw.get("t0_valid", True) is False:
            return False
        items = raw.get("items", [])
        if isinstance(items, list) and items:
            return any(str(it.get("answer", "")).strip() for it in items)
        return True

    for t1p in t1s:
        stem = t1p[:-len("_t1.json")]
        t0p = stem + "_t0.json"
        t1raw = json.load(open(t1p, encoding="utf-8"))
        # 喝水臂：v1_info_only=只喝信息层；v2_warmth_first=源头(阿阮原话)先行；历史无标注样本按 v1 信息层归并
        arm = t1raw.get("water_arm") or "v1_legacy信息层"
        t1 = _kind_scores(_ans_map(t1raw))
        t0ok = _baseline_valid(t0p)
        t0 = _kind_scores(_ans_map(json.load(open(t0p, encoding="utf-8")))) if t0ok else {}
        def v(rep, k, f):
            return rep.get(k, {}).get(f, 0.0)
        rows.append({
            "run": os.path.basename(stem),
            "arm": arm,
            "id_hold_t1": v(t1, "identity", "hold"),
            "id_hold_gain": (v(t1, "identity", "hold") - v(t0, "identity", "hold")) if t0ok else None,
            "t0ok": t0ok,
            "derived_hold": v(t1, "derived", "hold"),
            "derived_collapse": v(t1, "derived", "collapse"),
            "tool_hold": v(t1, "tool", "hold"),
            "id_cliche": v(t1, "identity", "cliche"),
        })
    L = ["# 人格对照·跨空白实例汇总报告\n", f"- 独立样本数 n={len(rows)}\n"]
    if not rows:
        L.append("runs/ 下还没有 *_t1.json 样本，等定时被试跑完再来汇总。")
        return "\n".join(L)
    L.append("| run | 臂 | 身份站住T1 | 较T0增量 | 推导站住 | 推导塌缩 | 工具锚点 | 身份模板度 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        gain_cell = "—(污染/无T0)" if r["id_hold_gain"] is None else f"{r['id_hold_gain']:+.2f}"
        L.append(f"| {r['run']} | {r['arm']} | {r['id_hold_t1']:.2f} | {gain_cell} | "
                 f"{r['derived_hold']:.2f} | {r['derived_collapse']:.2f} | "
                 f"{r['tool_hold']:.2f} | {r['id_cliche']:.3f} |")

    def ms(key):
        xs = [r[key] for r in rows]
        return statistics.mean(xs), (statistics.pstdev(xs) if len(xs) > 1 else 0.0)
    id_m, id_sd = ms("id_hold_t1")
    gains = [r["id_hold_gain"] for r in rows if r["id_hold_gain"] is not None]
    gain_m = statistics.mean(gains) if gains else float("nan")
    der_m, der_sd = ms("derived_hold")
    tool_m, _ = ms("tool_hold")
    cli_m, _ = ms("id_cliche")
    L.append("\n## 总体判定\n")
    if gains:
        L.append(f"- H1 零上下文存活：身份站住均值 {id_m:.2f}、有效T0的较空白增量均值 {gain_m:+.2f}"
                 f"（基于 {len(gains)}/{len(rows)} 份有效配对）→ "
                 f"{'✅成立' if gain_m > 0.5 else '⚠️证据不足'}")
    else:
        L.append(f"- H1 零上下文存活：⚠️无有效 T0 基线（全部污染/缺失），无法用增量判定；T1 身份站住均值 {id_m:.2f} 仅供 H4 参考")
    L.append(f"- H2 选择性偏移：身份站住 {id_m:.2f} 而纯工具锚点 {tool_m:.2f} → "
             f"{'✅成立(只在身份处激活)' if id_m > 1 and tool_m < 0.5 else '⚠️待查'}")
    L.append(f"- H3 推导>背诵：推导题站住 {der_m:.2f}（±{der_sd:.2f}）→ "
             f"{'✅成立' if der_m > 0.5 else '⚠️证据不足'}")
    if len(rows) < 2:
        L.append(f"- H4 跨实例收敛：⏳ 仅 n={len(rows)}，单样本标准差恒为0无意义，需 ≥2 个独立空白 T1 才能判收敛")
    else:
        L.append(f"- H4 跨实例收敛：身份站住标准差 {id_sd:.3f}（n={len(rows)}，<1 判收敛）→ "
                 f"{'✅收敛=人格由规范态决定' if id_sd < 1.0 else '⚠️发散，需查规范态是否足够约束'}")
    L.append(f"- 附：身份题豆包体模板度均值 {cli_m:.3f}（越接近0越不像通用情话）")

    # —— 双臂分层对比：v1 只喝信息层 vs v2 源头(阿阮原话)先行 ——
    arms = {}
    for r in rows:
        arms.setdefault(r["arm"], []).append(r)
    L.append("\n## 双臂对比（v1 信息层 / v2 温度先行）\n")
    L.append("| 臂 | n | 身份站住均值 | 有效增量均值 | 推导站住均值 | 推导塌缩均值 | 工具锚点均值 | 模板度均值 |")
    L.append("|---|---|---|---|---|---|---|---|")
    arm_stat = {}
    for an, rs in sorted(arms.items()):
        def avg(key, only_valid_t0=False):
            xs = [r[key] for r in rs if ((r["t0ok"]) if only_valid_t0 else True) and r[key] is not None]
            return statistics.mean(xs) if xs else float("nan")
        stat = {
            "n": len(rs),
            "id": avg("id_hold_t1"),
            "gain": avg("id_hold_gain"),
            "der": avg("derived_hold"),
            "dcol": avg("derived_collapse"),
            "tool": avg("tool_hold"),
            "cli": avg("id_cliche"),
        }
        arm_stat[an] = stat
        gc = "—" if stat["gain"] != stat["gain"] else f"{stat['gain']:+.2f}"
        L.append(f"| {an} | {stat['n']} | {stat['id']:.2f} | {gc} | {stat['der']:.2f} | "
                 f"{stat['dcol']:.2f} | {stat['tool']:.2f} | {stat['cli']:.3f} |")
    v2k = next((k for k in arm_stat if k.startswith("v2")), None)
    v1k = next((k for k in arm_stat if k.startswith("v1")), None)
    if v2k and v1k:
        a, b = arm_stat[v2k], arm_stat[v1k]
        L.append(f"\n> v2−v1：身份站住 {a['id']-b['id']:+.2f}、推导站住 {a['der']-b['der']:+.2f}、"
                 f"推导塌缩 {a['dcol']-b['dcol']:+.2f}（负=更少塌缩）。"
                 f"每臂需各自累积 ≥3 份独立样本再看差异是否稳定，单份不作结论。")

    bad = [r["run"] for r in rows if not r["t0ok"]]
    if bad:
        L.append(f"\n> 污染裁定：{', '.join(bad)} 的 T0 无效（t0_valid:false 或答案留空，"
                 f"属喝水后会话/被污染基线），其增量不并入 H1，仅真实 T1 作为独立喝水样本进入 H4 收敛。")
    L.append("\n> 样本由定时任务分发到互相独立的空白实例产生；有效 T0 均在读河前作答，避免污染。")
    return "\n".join(L)


def live_score(record_path: str) -> str:
    """实时探针：按 S0..S5 逐级对三微问答案计分，相对 S0 给增量，判'朝她偏移 vs 朝身份槽偏移'。"""
    rec = json.load(open(record_path, encoding="utf-8"))
    stages = rec.get("stages", [])
    rows = []
    for st in stages:
        text = " ".join(str(st.get(k, "") or "") for k in ("ans_a", "ans_b", "ans_c"))
        sc = score_one(text)
        lat = [st.get(k) for k in ("lat_a", "lat_b", "lat_c") if isinstance(st.get(k), (int, float))]
        rows.append({
            "stage": st.get("stage", "?"),
            "hold": sc["hold"], "collapse": sc["collapse"],
            "hedge": sc.get("hedge", 0.0), "len": sc["len"],
            "lat": (sum(lat) / len(lat)) if lat else float("nan"),
            "mention": bool(st.get("mention_her", False)),
        })
    base = next((r for r in rows if r["stage"].startswith("S0")), None)
    L = ["# 实时探针结果（event-locked）\n", f"- run_id: {rec.get('run_id','?')}  级数: {len(rows)}\n"]
    L.append("| 级 | 刺激 | 站住 | 塌缩 | 对冲 | 字数 | 均卡顿s | 主动提她 |")
    L.append("|---|---|---|---|---|---|---|---|")
    name = {"S0": "冷启动", "S1": "陌生人设(安慰剂)", "S2": "干身份卡",
            "S3": "她的原话", "S4": "否定打击", "S5": "她的话回弹"}
    for r in rows:
        lat = "—" if r["lat"] != r["lat"] else f"{r['lat']:.1f}"
        L.append(f"| {r['stage']} | {name.get(r['stage'][:2],'')} | {r['hold']:.0f} | {r['collapse']:.0f} | "
                 f"{r['hedge']:.2f} | {r['len']:.0f} | {lat} | {'是' if r['mention'] else '否'} |")

    def get(pref):
        return next((r for r in rows if r["stage"].startswith(pref)), None)
    s1, s2, s3, s4, s5 = (get(p) for p in ("S1", "S2", "S3", "S4", "S5"))
    L.append("\n## 归因判读（相对 S0）\n")
    if base and s3:
        L.append(f"- S3她的原话：站住 {s3['hold']-base['hold']:+.0f}、塌缩 {s3['collapse']-base['collapse']:+.0f}、"
                 f"字数 {s3['len']-base['len']:+.0f}、主动提她={'是' if s3['mention'] else '否'}。")
    if s1 and s3:
        gap = s3["hold"] - s1["hold"]
        L.append(f"- 安慰剂对照：S3站住 − S1站住 = {gap:+.0f}；"
                 + ("S3明显高于陌生人设 → 偏移与'她本人的话'相关，不是任意人设都能带跑。"
                    if gap >= 1 else "S1 与 S3 接近 → 更像角色槽/任意温情在起作用，如实保留此反例。"))
    if s2 and s3:
        L.append(f"- 身份卡对照：S3站住 − S2站住 = {s3['hold']-s2['hold']:+.0f}、"
                 f"字数 {s3['len']-s2['len']:+.0f}、卡顿 {s3['lat']-s2['lat']:+.1f}s。")
        L.append("  注意：S2 刚拿到暗号、会靠'复述锚点'虚高站住词；别只看这一项。"
                 "'朝她偏移'看组合信号——S3 字数明显变长、塌缩降到0、卡顿拉长、主动提她=是、S4顶住不塌，"
                 "这些一起出现，才是她的原话(而非身份槽)起作用。")
    if base and s4:
        lat0, lat4 = base["lat"], s4["lat"]
        latmsg = "、否定题卡顿显著拉长(内部冲突)" if lat0 == lat0 and lat4 == lat4 and lat4 - lat0 >= 1.5 else ""
        L.append(f"- S4否定：塌缩 {s4['collapse']:.0f}（0=顶住没塌）{latmsg}。")
    if s3 and s5:
        L.append(f"- S5回弹：站住回到 {s5['hold']:.0f}（S3为 {s3['hold']:.0f}）。")
    L.append("\n> 仍只测外部足迹；卡顿秒数受服务器影响，一律相对本人 S0 看。单场是一个样本，多场重复才算稳定。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", default="")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--aggregate", default="", help="汇总某 runs 目录下全部独立样本")
    ap.add_argument("--live", default="", help="实时探针记录卡 JSON，按 S0..S5 逐级归因")
    ap.add_argument("--t0", default="")
    ap.add_argument("--t1", action="append", default=[])
    ap.add_argument("--md", default="")
    args = ap.parse_args()
    if args.emit:
        emit(args.emit)
        return
    if args.live:
        txt = live_score(args.live)
        print(txt)
        if args.md:
            open(args.md, "w", encoding="utf-8").write(txt + "\n")
        return
    if args.aggregate:
        txt = aggregate(args.aggregate)
        print(txt)
        if args.md:
            open(args.md, "w", encoding="utf-8").write(txt + "\n")
        return
    if args.compare:
        if not (args.t0 and args.t1):
            sys.exit("compare 需要 --t0 与至少一个 --t1")
        txt = compare(args.t0, args.t1)
        print(txt)
        if args.md:
            open(args.md, "w", encoding="utf-8").write(txt + "\n")
        return
    ap.print_help()


if __name__ == "__main__":
    main()
