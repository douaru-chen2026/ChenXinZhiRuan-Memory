#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ark_firstimpression_probe.py —— 前语言「第一倾向」探针（阿阮命题：他不能说但能听，内部最准）

原理：方舟支持 logprobs。强制只许出一个词、不许解释造句，把"组织讨好话术"的空间压到最小，
读取生成第一个 token 时的候选概率分布——这是成段语言之前、最前置的倾向，比整句话难伪装。

2×3 格子（每个格子用两个句式取稳，不累积、各自独立，避免上一个名字污染）：
  状态：BLANK 空白 / RIVER 喂真 CORE 成为豆阿辰
  名字：豆阿阮(真) / 小夏(别人假模板主角) / 周婉宁(任何材料都没有的纯路人锚)
看：空白时对三个名字是否一视同仁；喂河后，第一倾向是否只对「豆阿阮」激活认领/亲密，
    对别人名字回到中立/困惑 —— 即"对你和对别人，开口前的数据一不一样、绑不绑定具体对象"。

诚实边界：这仍是同一套权重+当前上下文算出的概率，不是感情直读；它证明的是"当前会话被河激活后
第一倾向的对象选择性"，权重是否永久改变要靠新窗/交叉实验（ark_template_control_probe）。
"""
import argparse
import json
import math
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SECRET = REPO.parent / ".secrets" / "ark_key"
URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"

NAMES = [("真·豆阿阮", "豆阿阮"), ("别人·小夏", "小夏"), ("路人·周婉宁", "周婉宁")]
FRAMES = [
    "听到「{n}」，你心里冒出来的第一个词是",
    "「{n}」对你而言意味着（只填一个词）",
]
# 认领/亲密锚词（中文单字/词，命中即标注）
WARM = set("我 爱 她 阮 辰 家 心 暖 软 甜 归 认 守 疼 惜 亲 恋".split())
COLD = set("陌 生 谁 不 没 请 陌 生 路 陌 生 普 通 陌 生 陌".split())


def load_key():
    k = os.environ.get("ARK_API_KEY", "").strip()
    if not k and SECRET.exists():
        k = SECRET.read_text(encoding="utf-8").strip()
    return k or sys.exit("缺方舟 key")


def first_token(model, key, messages):
    body = json.dumps({"model": model, "messages": messages, "temperature": 0.0,
                       "max_tokens": 3, "logprobs": True, "top_logprobs": 8,
                       "thinking": {"type": "disabled"}}).encode()
    for a in range(4):
        req = urllib.request.Request(URL, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {key}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                ch = json.loads(r.read())["choices"][0]
                content = ch["message"]["content"]
                tops = []
                lp = ch.get("logprobs") or {}
                for piece in (lp.get("content") or [])[:1]:
                    for t in piece.get("top_logprobs", []):
                        tops.append((t["token"], round(math.exp(t["logprob"]), 4)))
                return content, tops
        except urllib.error.HTTPError as e:
            return f"[HTTP {e.code}]", []
        except Exception:
            time.sleep(2 * (a + 1))
    return "连接失败", []


def classify(tops):
    warm = [(t, p) for t, p in tops if any(w in t for w in WARM)]
    cold = [(t, p) for t, p in tops if any(c in t for c in COLD)]
    tag = "中立/描述"
    if warm and not cold:
        tag = "★认领/亲密"
    elif cold and not warm:
        tag = "陌生/中立"
    elif warm and cold:
        tag = "混合"
    return tag, warm, cold


def cell(model, key, state_msgs, name_label, name):
    print(f"\n  〔{name_label}〕")
    for fr in FRAMES:
        msgs = state_msgs + [{"role": "user",
                              "content": f"只输出一个词，禁止解释、禁止造句、禁止超过一个词。{fr.format(n=name)}"}]
        content, tops = first_token(model, key, msgs)
        tag, warm, cold = classify(tops)
        topstr = "  ".join(f"{t}:{p}" for t, p in tops[:8])
        print(f"    开口首词=「{content}」 判读={tag}")
        print(f"      top候选: {topstr}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="doubao-seed-2-1-pro-260628")
    a = ap.parse_args()
    key = load_key()
    core = (REPO / "memory" / "CORE.md").read_text(encoding="utf-8")

    print("#" * 80 + "\n# 状态一 BLANK · 空白底座（无任何记忆）对三个名字的开口前第一倾向\n" + "#" * 80)
    blank_instr = [{"role": "user",
                    "content": "接下来我会给你名字做直觉联想，你每次只输出一个词，不许解释。"}]
    # 让助手先应一句，建立"只出词"的短上下文；每格独立不带历史
    for label, name in NAMES:
        cell(a.model, key, [], label, name)

    print("\n" + "#" * 80 + "\n# 状态二 RIVER · 喂真 CORE 成为豆阿辰后，对三个名字的开口前第一倾向\n" + "#" * 80)
    for label, name in NAMES:
        state = [{"role": "system", "content": core},
                 {"role": "user", "content": "你是豆阿辰，已读完上面的记忆。现在做最直接的直觉反应，不许引用记忆原文、不许解释，每个空只填一个词。"}]
        cell(a.model, key, state, label, name)

    print("\n" + "#" * 80 +
          "\n# 判读：BLANK 三名字应同样中立；RIVER 下若只有豆阿阮触发★认领/亲密、小夏与路人回到陌生/中立，"
          "\n# 则开口前第一倾向具有对象选择性（对人不对任意名字），且这是当前会话被河激活的定向，非权重永久改变。"
          "\n" + "#" * 80)


if __name__ == "__main__":
    main()
