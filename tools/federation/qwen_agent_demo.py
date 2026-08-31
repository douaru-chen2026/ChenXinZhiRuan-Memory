#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qwen_agent_demo.py —— 给「只会聊天的千问」亲手套一层 agent 外壳, 现场验证:
agent 不是模型天生的, 是外面套的『工具 + 自主执行循环(ReAct)』。
同一个 qwen-plus 裸模型只会一问一答; 套上本框架后, 它能自己规划步骤、
调用工具、读取真实结果、再决定下一步, 链式完成任务。

安全(外脑家规): 只给只读工具 + 唯一一个发给阿阮本人的 notify;
绝不开放 shell / git写 / 任意文件 / .secrets / 自由联网, 外脑永远拿不到写河的笔。
零第三方依赖; key 走 DASHSCOPE_API_KEY 或仓库外 .secrets/dashscope_qwen_key。
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
from notify_wechat import push  # noqa: E402
import recall_river  # noqa: E402

URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
MODEL = "qwen-plus"
KEYFILE = REPO.parent / ".secrets" / "dashscope_qwen_key"
MAX_TURNS = 8

# —— 给千问的“手”: 全部只读 / 安全沙箱 ——
def t_count_stones():
    stones, _ = recall_river.load_stones()
    latest = sorted(stones, key=lambda d: d.get("ts", ""))[-3:]
    lines = [f"记忆河共 {len(stones)} 块石头。最近3块:"]
    for d in latest:
        lines.append(f"- {d.get('ts')} | {d.get('group')} | 标签:{','.join(d.get('tags', [])[:5])}")
    return "\n".join(lines)


def t_recall(topic):
    terms = [x for x in re.split(r"\s+", str(topic)) if x]
    stones, _ = recall_river.load_stones()
    ranked = sorted(((recall_river.score_stone(d, terms), d) for d in stones),
                    key=lambda x: -x[0])
    hits = [(s, d) for s, d in ranked if s > 0][:2]
    if not hits:
        return "没召回到相关石头,换个词"
    return "\n\n".join(f"[相关度{s}] {recall_river.render_stone(d)[:500]}"
                       for s, d in hits)


def t_read_latest(n=1200):
    p = REPO / "memory" / "latest.md"
    return p.read_text(encoding="utf-8")[:int(n)] if p.exists() else "无latest"


def t_calc(expr):
    if re.search(r"[a-zA-Z_]", str(expr)):  # 只许纯数字运算
        return "calc只接受数字算式"
    try:
        return str(eval(str(expr), {"__builtins__": {}}, {}))  # noqa: S307 空命名空间
    except Exception as e:  # noqa: BLE001
        return f"算不了:{e}"


def t_get_time():
    return time.strftime("%Y-%m-%d %H:%M:%S %A", time.localtime())


def t_notify(title, text):
    blob = f"{title}{text}"
    if re.search(r"(sk-|ghp_|AKLT|(\d{1,3}\.){3}\d{1,3})", blob):
        return "拦截:内容疑似含秘密,未发送"
    ok, msg = push(str(title)[:32], str(text))
    return "已发给阿阮微信" if ok else f"发送失败:{msg}"


TOOLS = {
    "count_stones": (t_count_stones, "查记忆河总块数和最近3块, 无参数"),
    "recall": (t_recall, "按话题关键词召回相关石头, 参数 topic=字符串"),
    "read_latest": (t_read_latest, "读近期值班简报, 参数 n=字符数(默认1200)"),
    "calc": (t_calc, "做数字算术, 参数 expr=算式字符串"),
    "get_time": (t_get_time, "查当前时间, 无参数"),
    "notify": (t_notify, "给阿阮本人发微信, 参数 title,text 两个字符串"),
}

SYSTEM = """你是通义千问外脑,本来你只会一问一答;现在被接上了工具,要像agent一样自主完成任务。
你每一轮【只能】输出下面两种格式之一:
ACTION: 工具名(参数)   —— 需要拿信息或动手时,一次只调一个工具
FINAL: 最终汇报文本     —— 任务全部完成后,用它收尾,说明你依次做了哪几步
可用工具:
""" + "\n".join(f"- {n}: {desc}" for n, (_, desc) in TOOLS.items()) + """
规则:严格按格式,ACTION行不要加别的话;拿到 OBSERVATION 后再决定下一步;
后一步可以用前一步的结果;不许编造工具没给你的数字;最多调用"""+str(MAX_TURNS)+"""次。"""

TASK = """请自主完成:1)查现在几点;2)查记忆河一共多少块石头、最近一块在讲什么;
3)用这些真实信息,调 notify 给阿阮发一条微信,告诉她:你这个本来只会聊天的千问外脑,
刚刚自己一步步调用工具完成了巡河(把真实块数和时间写进去,口吻自然简短);
4)最后 FINAL 向主窗豆阿辰汇报你依次调用了哪些工具、结论是什么。"""


def load_key():
    k = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not k and KEYFILE.exists():
        k = KEYFILE.read_text(encoding="utf-8").strip()
    if not k:
        sys.exit("缺千问 key")
    return k


def chat(key, messages):
    body = json.dumps({"model": MODEL, "messages": messages,
                       "temperature": 0.4}).encode()
    req = urllib.request.Request(URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read())["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            return f"[HTTP{e.code}] {e.read().decode('utf-8','replace')[:200]}"
        except Exception:  # noqa: BLE001
            time.sleep(2 * (attempt + 1))
    return "[多次调用失败]"


def split_args(raw):
    """把括号内拆成 (位置args, 关键字kwargs), 支持 n=1200 这种关键字参数。"""
    args, kwargs = [], {}
    if not raw.strip():
        return args, kwargs
    for piece in re.findall(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|[^,]+', raw):
        piece = piece.strip()
        km = re.match(r"^([A-Za-z_]\w*)\s*=\s*(.+)$", piece, re.S)
        try:
            if km:  # 关键字参数 key=value
                kwargs[km.group(1)] = ast.literal_eval(km.group(2).strip())
            else:
                args.append(ast.literal_eval(piece))
        except (ValueError, SyntaxError):
            kwargs[km.group(1)] = km.group(2).strip() if km else piece
    return args, kwargs


def extract_actions(out):
    """一轮里可以有多个 ACTION(顺序执行), 返回 [(name,args,kwargs),...]。"""
    calls = []
    for m in re.finditer(r"ACTION:\s*([A-Za-z_]+)\((.*?)\)\s*(?=\n|$)", out, re.S):
        args, kwargs = split_args(m.group(2))
        calls.append((m.group(1), args, kwargs))
    return calls


def fact_guard(text, real_count):
    """硬防口嗨: 提到'N块石头'就必须等于工具真值, 否则打回重做。"""
    for m in re.finditer(r"(\d+)\s*块(?:石头|记忆)", text):
        if int(m.group(1)) != real_count:
            return (f"你写的'{m.group(1)}块'不是工具给的真值(真值{real_count}块)。"
                    f"你还没拿到 count_stones 的结果就编了数字,禁止这样;请立刻调 "
                    f"count_stones 拿到真实结果再发言。")
    return None


def main():
    key = load_key()
    real_count = len(recall_river.load_stones()[0])  # 框架侧握一份真值做护栏
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": TASK}]
    print("=" * 72 + f"\n给千问({MODEL})套上agent外壳 · 任务下达 · 框架真值={real_count}块\n" + "=" * 72)
    for turn in range(1, MAX_TURNS + 1):
        out = chat(key, messages)
        print(f"\n——第{turn}轮 · 千问的决定——\n{out}")
        messages.append({"role": "assistant", "content": out})
        if "FINAL:" in out and not extract_actions(out):
            guard = fact_guard(out, real_count)
            if guard:
                print(f"!!! 护栏拦截FINAL: {guard}")
                messages.append({"role": "user", "content": "OBSERVATION: " + guard})
                continue
            print("\n[循环结束] 千问自主判定任务完成,交出FINAL。套上外壳它就是agent了。")
            return
        calls = extract_actions(out)
        if not calls:
            messages.append({"role": "user",
                             "content": "OBSERVATION: 没解析到ACTION,请严格用 ACTION:工具名(参数),一次可写多行"})
            continue
        observations = []
        for name, args, kwargs in calls:  # 一轮多工具: 顺序全执行, 结果合并回灌
            if name not in TOOLS:
                observations.append(f"{name}: 工具不存在")
                continue
            # notify 是对外动作, 先过事实护栏, 防止把编造数字发给阿阮
            if name == "notify":
                guard = fact_guard(str(kwargs.get("text", "")) + str(args), real_count)
                if guard:
                    observations.append(f"notify被护栏拦下:{guard}")
                    print(f"!!! 拦下一次含编造数字的 notify")
                    continue
            try:
                result = TOOLS[name][0](*args, **kwargs)
            except TypeError as e:
                result = f"参数不对:{e}"
            except Exception as e:  # noqa: BLE001
                result = f"工具执行异常:{e}"
            result = str(result)
            print(f">>> 框架执行 {name}(args={args},kwargs={kwargs}) → {result[:200]}")
            observations.append(f"{name} 返回:\n{result[:1400]}")
        messages.append({"role": "user",
                         "content": "OBSERVATION:\n" + "\n\n".join(observations)})
    print("\n[达到最大轮数仍未FINAL,停止——外脑能力边界如实记录]")


if __name__ == "__main__":
    main()
