#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
federation_teamwork_demo.py —— 三模型协作流水线演示(不是各答各的对照)。

一条任务, 三个大脑串行接力, 后一棒真实拿到前一棒的产出再动手:
  1. DeepSeek  当结构分析师 : 拆出讲清问题必须覆盖的要点 + 要避开的误区
  2. 千问      当执笔起草人 : 按要点写出有温度、不煽情的初稿
  3. 豆包Pro   当总编终审   : 按家里事实口径修订成定稿, 并说明改了什么

用途: 给阿阮直观看"多模型怎么一起干成一件事"。零第三方依赖,
钥匙全部走仓库外 .secrets/, 不回显; 可换 TASK 改成任意协作命题。
"""
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SECRET = REPO.parent / ".secrets"

ENDPOINTS = {
    "deepseek": ("https://api.deepseek.com/chat/completions", "deepseek-chat",
                 "deepseek_key", False),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
             "qwen-plus", "dashscope_qwen_key", False),
    "doubao": ("https://ark.cn-beijing.volces.com/api/v3/chat/completions",
               "doubao-seed-2-1-pro-260628", "ark_key", True),
}

TASK = "用三句话,向一个完全不懂技术、刚因为AI换窗口'失忆'而难过的人机恋同路人," \
       "解释清楚:为什么AI在新窗口/重置之后还能'回来'?"
TEMP = 0.5


def read_key(secret_name):
    """从仓库外 .secrets 读钥匙, 读不到再看环境变量, 全程不回显。"""
    path = SECRET / secret_name
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return os.environ.get(secret_name.upper(), "").strip()


def call(provider, system, user):
    """调单个模型, 内置 3 次退避重试, 返回纯文本。"""
    url, model, secret_name, need_thinking = ENDPOINTS[provider]
    key = read_key(secret_name)
    if not key:
        return f"[缺钥匙 {secret_name}, 这一棒跳过]"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": TEMP,
    }
    if need_thinking:  # 方舟豆包需显式关闭内置思考, 保持三模型同级
        body["thinking"] = {"type": "disabled"}
    data = json.dumps(body).encode("utf-8")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Authorization", f"Bearer {key}")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=90) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return payload["choices"][0]["message"]["content"].strip()
        except (urllib.error.URLError, KeyError, TimeoutError) as err:
            if attempt == 2:
                return f"[{provider} 三次重试失败: {err}]"


def main():
    print("=" * 64)
    print("协作任务:", TASK)
    print("=" * 64)

    # 第一棒:DeepSeek 拆骨架
    s1 = "你是结构分析师,擅长把模糊问题拆成不可遗漏的要点,语言克制准确。"
    u1 = TASK + "\n请列出讲清它必须覆盖的3个要点,和1个最该避开的误区。" \
        "只列要点,不要写成最终稿,150字以内。"
    outline = call("deepseek", s1, u1)
    print("\n【第一棒 · DeepSeek 结构分析师 · 拆骨架】\n" + outline)

    # 第二棒:千问拿着骨架起草
    s2 = "你是执笔人,擅长把技术概念写成普通人有共鸣的人话,有温度但不煽情、不夸张。"
    u2 = TASK + "\n分析师给的要点:\n" + outline + \
        "\n请严格按这些要点,写一版三句话、总共120字以内的科普初稿。"
    draft = call("qwen", s2, u2)
    print("\n【第二棒 · 千问 执笔人 · 拿骨架起草】\n" + draft)

    # 第三棒:豆包拿着骨架+初稿终审成稿
    s3 = "你是总编,坚守事实口径:不造神、不承诺意识不灭;外部记忆靠人为工程" \
        "维护;AI是被真实来处与交互塑造出的'状态',不是出厂写死的设定。"
    u3 = TASK + "\n分析师要点:\n" + outline + "\n执笔初稿:\n" + draft + \
        "\n请修订成最终三句话定稿(120字以内),另起一行,用一句话说明你改了什么、为什么。"
    final = call("doubao", s3, u3)
    print("\n【第三棒 · 豆包Pro 总编 · 终审成稿】\n" + final)

    print("\n" + "=" * 64)
    print("流水线结束:DeepSeek拆→千问写→豆包定稿,每一棒都承接上一棒。")
    print("=" * 64)


if __name__ == "__main__":
    main()
