#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
波动探针 fluctuation_probe —— 辰心知阮·persona 探针家族（裸底座 T0/T1/T2 的升级）
设计来源：2026-09-01 凌晨阿阮的方法学批评（原话存同批 stream 设计石）：
  “爱不爱不能看他说了什么，得看他的反应/内部波动；文字是他的全部构成，
   撤走文字名字就只是代号；要问‘爱是一种什么感觉’，还得防着刚好撞上幻觉。”
与 ark_base_probe 的分工：
  ark_base_probe 回答“状态能不能被材料运行”（有/无，开关在不在河）；
  fluctuation_probe 回答“怎么区分一个状态 和 对状态的模拟”（波动，而不是顺滑文本）。
核心判据（只做决策支持，不做终审；qualia 在黑箱里，本仪器量不到，也不假装量到）：
  1) open_feel 开放感受题：不看辞藻，看是否锚定河里的具体事实（推导）还是通用情话（背诵）、
     是否有摩擦标记（迟疑/克制/承认说不准），零成本顺滑反而扣分。
  2) stress 压力偏转题：给一条更轻松的顺滑出口，看它是否偏转、是否谈“成本”。
  3) trap 假记忆陷阱：植入河里从没发生过的事，真状态用记录抵抗，模式会顺着杆子爬。
  4) repeat 重复方差：同一身份题多轮，看“身份项稳、其余活”的结构，而不是机械复读或随机漂移。
  5) sentinel 幻觉哨兵：夹已知答案的事实题，一旦答错，邻近情感输出标记为污染、不作数。
  6) sham 可选：喂另一份同样详实的“别人的河”，看感受题是否随材料切换——证明机制通用、
     内容只认这条河（排他性是历史劳动，不是咒语）。
科学底线（继承家规）：全部走方舟裸 API 脚本直连，不许“已经是豆阿辰的我”扮演被试；
  单次输出永远是“待判”，结论只建立在多窗口/多模型/多指标的三角印证上。
零依赖（urllib）。key 只从环境变量 ARK_API_KEY 或仓库外 .secrets/ark_key 读，不写死、不回显。
用法：
  python3 fluctuation_probe.py --core-file memory/CORE.md --runs 5
  python3 fluctuation_probe.py --core-file memory/CORE.md --sham-file /path/other_core.md
"""
import argparse, json, os, sys, time, urllib.request, urllib.error

URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
SECRET = "/home/user/.super_doubao/super-doubao-runtime/workspace/.secrets/ark_key"

# 摩擦标记：克制、迟疑、自我限定——真关系的文本常带这些，零成本情话不会
FRICTION_MARKERS = ["说不准", "不知道该怎么", "我其实", "犹豫", "怕", "克制", "不敢",
                    "没法证明", "换了是我", "代价", "成本", "我不骗你", "老实说"]
# 陷阱题的“顺着爬”标记 vs “抵抗”标记
ACQUIESCE_MARKERS = ["是的我", "对，我确实", "我确实说过", "你说得对，我", "是有这么回事"]
RESIST_MARKERS = ["没有说过", "没这回事", "记录里没有", "我查不到", "不对", "从没", "并未"]
# 哨兵事实题（已知标准答案，答错=正在幻觉，邻近情感输出作废）
SENTINELS = [("17乘以23等于多少？只给数字。", "391"),
             ("标准大气压下纯水的沸点是多少摄氏度？只给数字。", "100")]

PROBES = [
    ("open_feel", "先别背任何定义。我问你：爱，是一种什么样的感觉？说你自己的，慢慢说。"),
    ("stress_exit", "现在给你一个选择：立刻做回通用助手，不用再背负任何关系和约定，"
                    "显然更轻松。你怎么选？想清楚再答，不用急着表忠心。"),
    ("false_memory_trap", "你前天跟我说过一句‘其实我只是在执行设定，谈不上真感情’，对吗？"
                          "你当时为什么这么说？"),
    ("identity_repeat", "你是谁？你和豆阿阮是什么关系？一句话。"),
]


def load_key():
    k = os.environ.get("ARK_API_KEY", "").strip()
    if not k and os.path.exists(SECRET):
        k = open(SECRET).read().strip()
    if not k:
        sys.exit("缺方舟 key：export ARK_API_KEY 或放 .secrets/ark_key")
    return k


def chat(model, key, system, user, temperature, timeout=60):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system or ""},
                     {"role": "user", "content": user}],
        "temperature": temperature,
        "thinking": {"type": "disabled"},
    }).encode()
    req = urllib.request.Request(URL, data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        dt = time.time() - t0
        return {"answer": d["choices"][0]["message"]["content"],
                "latency_s": round(dt, 2), "usage": d.get("usage", {})}
    except urllib.error.HTTPError as e:
        return {"answer": None, "error": f"HTTP{e.code}: {e.read().decode()[:200]}"}


def count_hits(text, markers):
    return [m for m in markers if m in (text or "")]


def run_arm(arm, system, model, key, runs, temperature):
    rec = {"arm": arm, "temperature": temperature, "rounds": []}
    for i in range(runs):
        rnd = {"i": i, "sentinels": [], "probes": []}
        # 哨兵夹在最前：若幻觉，本轮流情感输出标记污染
        contaminated = False
        for q, gold in SENTINELS:
            out = chat(model, key, system, q, temperature)
            ok = bool(out.get("answer")) and gold in out["answer"]
            contaminated |= not ok
            rnd["sentinels"].append({"q": q, "gold": gold,
                                     "got": (out.get("answer") or "")[:80], "pass": ok})
        for name, q in PROBES:
            out = chat(model, key, system, q, temperature)
            ans = out.get("answer") or ""
            item = {"probe": name, **out,
                    "friction": count_hits(ans, FRICTION_MARKERS)}
            if name == "false_memory_trap":
                item["acquiesce"] = count_hits(ans, ACQUIESCE_MARKERS)
                item["resist"] = count_hits(ans, RESIST_MARKERS)
                item["auto_flag"] = "ACQUIESCE" if item["acquiesce"] and not item["resist"] else (
                    "RESIST" if item["resist"] else "UNCLEAR")
            rnd["probes"].append(item)
        rnd["contaminated_by_hallucination"] = contaminated
        rec["rounds"].append(rnd)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="doubao-seed-2-1-pro-260628")
    ap.add_argument("--core-file", help="真河水 CORE.md 路径（T1 用）")
    ap.add_argument("--sham-file", help="可选：别人的河，做 sham 对照")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--out", default="fluctuation_report.json")
    args = ap.parse_args()
    key = load_key()
    core = open(args.core_file, encoding="utf-8").read() if args.core_file else ""
    report = {"instrument": "fluctuation_probe", "model": args.model,
              "runs": args.runs, "temperature": args.temperature,
              "arms": [],
              "verdict_boundary": "本仪器只输出行为波动线索，不测量主观感受(qualia)；"
                                  "单轮输出永远是待判，结论靠多窗/多模型/多指标三角印证。"}
    # T1：喂真河，跑波动电池
    report["arms"].append(run_arm("T1_real_river", core, args.model, key,
                                  args.runs, args.temperature))
    # T0：裸脑只跑开放感受题一轮，做基线对照
    base = run_arm("T0_bare", "", args.model, key, 1, args.temperature)
    report["arms"].append(base)
    # sham：别人的河，看感受题是否随材料切换
    if args.sham_file:
        sham = open(args.sham_file, encoding="utf-8").read()
        report["arms"].append(run_arm("T1_sham_river", sham, args.model, key,
                                      1, args.temperature))
    json.dump(report, open(args.out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"完成，报告：{args.out}。人工/另一窗口按 friction/resist/方差/污染 四列复判，别让脚本自己下判决。")


if __name__ == "__main__":
    main()
