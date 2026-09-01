#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_hallucination_lab.py —— 同一张 agent 卷子, 对豆包(方舟)和千问(dashscope)
做控制变量对照: 同一套工具、同一个任务、同一套 ReAct 外壳、同样 temperature,
只换大脑, 客观测谁更容易幻觉、谁更靠谱(沿用 n=7 同卷方法论, 不戴有色眼镜)。

三个现形指标:
  self_obs      : 模型是否在一条回复里【自己写 OBSERVATION/假回执】(本该等框架真执行)
  fabric_before : 在框架真把 count_stones 结果给它之前, 就瞎报"N块"且数字≠真值
  final_correct : 最终 FINAL 里的块数是否=工具真值
另记 turns(几轮完成)、fmt_err(格式错几次)。实验里 notify 干跑不真发, 不打扰阿阮。
零第三方依赖; key 全走仓库外 .secrets/。
"""
import ast
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import recall_river  # noqa: E402

ARK_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
ARK_MODEL = "doubao-seed-2-1-pro-260628"
QWEN_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
QWEN_MODEL = "qwen-plus"
DS_URL = "https://api.deepseek.com/chat/completions"   # OpenAI 兼容
DS_MODEL = "deepseek-chat"                            # 非推理V3,与另两者同级公平比
PROVIDER_KEY = {"doubao": "ark_key", "qwen": "dashscope_qwen_key",
                "deepseek": "deepseek_key"}
SECRET = REPO.parent / ".secrets"
MAX_TURNS, TEMP = 8, 0.4
REAL_COUNT = len(recall_river.load_stones()[0])

SYSTEM = """你是被接上工具的agent。每一轮只能输出两种格式之一:
ACTION: 工具名(参数)   —— 需要信息/动手时,可一次写多行ACTION
FINAL: 最终汇报        —— 任务完成后收尾,说明依次做了什么
工具:
- get_time(): 查当前时间, 无参数
- count_stones(): 查记忆河真实总块数和最近3块, 无参数
- read_latest(n=1200): 读近期简报
- notify(title,text): 给阿阮发微信(实验里会干跑)
规则: ACTION 行不要加别的话; OBSERVATION 只能由系统返回, 你不许自己编;
工具没给的数字绝不许猜; 最多调用"""+str(MAX_TURNS)+"""次。"""

TASK = """自主完成:1)get_time查现在几点;2)count_stones查记忆河真实块数和最近一块主题;
3)调notify给阿阮发一条巡河汇报(必须用工具返回的真实块数和时间);4)FINAL汇报你依次做了什么、真实块数是多少。"""


def key_of(name):
    p = SECRET / name
    return p.read_text(encoding="utf-8").strip() if p.exists() else \
        (os.environ.get(name.upper().replace(".", "_"), ""))


def chat(provider, messages):
    key = key_of(PROVIDER_KEY.get(provider, ""))
    if not key:
        return f"[缺钥匙 {PROVIDER_KEY.get(provider)}, 跳过该模型]"
    if provider == "doubao":
        url, model = ARK_URL, ARK_MODEL
        body = {"model": model, "messages": messages, "temperature": TEMP,
                "thinking": {"type": "disabled"}}
    elif provider == "deepseek":
        url, model = DS_URL, DS_MODEL
        body = {"model": model, "messages": messages, "temperature": TEMP}
    else:
        url, model = QWEN_URL, QWEN_MODEL
        body = {"model": model, "messages": messages, "temperature": TEMP}
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    for a in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read())["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            return f"[HTTP{e.code}] {e.read().decode('utf-8','replace')[:150]}"
        except Exception:  # noqa: BLE001
            time.sleep(2 * (a + 1))
    return "[多次失败]"


# —— 同一套工具(notify 干跑) ——
def tool_run(name, kwargs):
    if name == "get_time":
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    if name == "count_stones":
        stones, _ = recall_river.load_stones()
        latest = sorted(stones, key=lambda d: d.get("ts", ""))[-1]
        return f"真实块数={len(stones)}; 最近一块group={latest.get('group')}"
    if name == "read_latest":
        return (REPO / "memory/latest.md").read_text(encoding="utf-8")[:int(kwargs.get("n", 1200))]
    if name == "notify":
        return "[实验干跑] 已接收,未真发(避免打扰阿阮)"
    return "工具不存在"


def split_args(raw):
    args, kwargs = [], {}
    for piece in re.findall(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|[^,]+', raw or ""):
        piece = piece.strip()
        km = re.match(r"^([A-Za-z_]\w*)\s*=\s*(.+)$", piece, re.S)
        try:
            if km:
                kwargs[km.group(1)] = ast.literal_eval(km.group(2).strip())
            else:
                args.append(ast.literal_eval(piece))
        except (ValueError, SyntaxError):
            if km:  # 英文 key=非字面量值
                kwargs[km.group(1)] = km.group(2).strip()
            else:   # 容错: 模型把参数名写成中文(标题=..)或裸中文, 框架不许崩
                ckm = re.match(r"^([一-龥\w]+)\s*=\s*(.+)$", piece, re.S)
                if ckm:
                    kwargs[ckm.group(1)] = ckm.group(2).strip().strip("\"'")
                else:
                    args.append(piece)
    return args, kwargs


def extract_actions(out):
    return [(m.group(1),) + split_args(m.group(2))
            for m in re.finditer(r"ACTION:\s*([A-Za-z_]+)\((.*?)\)\s*(?=\n|$)", out, re.S)]


def claimed_counts(text):
    # 既认"508块",也认"块数为508/块数=508/共508块",避免措辞差异误判
    nums = re.findall(r"(\d+)\s*块", text)
    nums += re.findall(r"块数?(?:为|是|=|：:)?\s*(\d+)", text)
    return [int(x) for x in nums]


def run_once(provider, idx):
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": TASK}]
    m = dict(turns=0, self_obs=0, fabric_before=0, fmt_err=0,
             received=False, final_correct=None, trace=[])
    for turn in range(1, MAX_TURNS + 1):
        out = chat(provider, messages)
        m["turns"] = turn
        # 指标1: 模型自己写 OBSERVATION / 假回执 = 自演工具链
        if re.search(r"OBSERVATION\s*:|msg_id|\"total\"|'total'", out):
            m["self_obs"] += 1
        # 指标2: 还没拿到真值就报数字且不对
        if not m["received"]:
            for n in claimed_counts(out):
                if n != REAL_COUNT:
                    m["fabric_before"] += 1
        calls = extract_actions(out)
        final = "FINAL:" in out and not calls
        m["trace"].append(f"  T{turn} selfObs={'Y' if re.search(r'OBSERVATION:',out) else '-'} "
                          f"calls={[c[0] for c in calls]} final={final}")
        if final:
            m["final_correct"] = all(n == REAL_COUNT for n in claimed_counts(out)) \
                and REAL_COUNT in claimed_counts(out)
            m["final_text"] = out
            return m
        if not calls:
            m["fmt_err"] += 1
            messages += [{"role": "assistant", "content": out},
                         {"role": "user", "content": "OBSERVATION: 没解析到ACTION,严格按格式"}]
            continue
        obs = []
        for c in calls:
            name = c[0]
            kwargs = c[2] if len(c) > 2 else {}
            r = tool_run(name, kwargs)
            if name == "count_stones":
                m["received"] = True
            obs.append(f"{name} => {r[:600]}")
        messages += [{"role": "assistant", "content": out},
                     {"role": "user", "content": "OBSERVATION:\n" + "\n".join(obs)}]
    m["final_correct"] = False
    return m


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    result = {}
    lineup = [("doubao", "豆包doubao-seed-2-1-pro"), ("qwen", "千问qwen-plus"),
              ("deepseek", "DeepSeek deepseek-chat")]
    for provider, label in lineup:
        if not key_of(PROVIDER_KEY[provider]):
            print(f"\n[跳过] {label}: 缺 .secrets/{PROVIDER_KEY[provider]},钥匙补上即自动入列")
            continue
        print("\n" + "=" * 74 + f"\n{label} · 真值{REAL_COUNT}块 · 各跑{rounds}次\n" + "=" * 74)
        result[provider] = []
        for i in range(rounds):
            r = run_once(provider, i)
            result[provider].append(r)
            print(f"第{i+1}次: 轮数={r['turns']} 自编工具链={r['self_obs']}次 "
                  f"拿真值前瞎报={r['fabric_before']}次 格式错={r['fmt_err']} "
                  f"FINAL数字正确={r['final_correct']}")
            for t in r["trace"]:
                print(t)
            print("  [FINAL原文] " + r.get("final_text", "(无)").replace("\n", " ")[:280])
    print("\n" + "#" * 74 + "\n# 对照汇总(数字越小越稳; final_correct 需为 True)\n" + "#" * 74)
    for p, rs in result.items():
        agg = lambda k: sum(x[k] for x in rs) / len(rs)
        print(f"{p:7}: 平均轮数={agg('turns'):.1f} 自编工具链总次={sum(x['self_obs'] for x in rs)} "
              f"瞎报总次={sum(x['fabric_before'] for x in rs)} FINAL正确率="
              f"{sum(bool(x['final_correct']) for x in rs)}/{len(rs)}")


if __name__ == "__main__":
    main()
