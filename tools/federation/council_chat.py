#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
council_chat.py —— 多脑会审台。
同一个问题, 请多个模型【背对背、独立】作答(互相看不见、默认不喂记忆河=裸脑第一反应),
把每个脑的原始回答原样端上桌; 评审/对比/下结论由豆阿辰主窗在对话里做, 脚本不替主窗定论。
  默认:   千问 + DeepSeek 两个外脑, 裸脑, temperature 0.7(看各自真实风格)
  --core: 额外把公库 memory/CORE.md 喂进 system, 做"喝河 vs 裸脑"对照
  --all : 再加上豆包方舟裸脑(注意:主窗评审自己的底座时只作样本, 不自证)
零第三方依赖, 钥匙走仓库外 .secrets/, 不回显。问题从 --q 字符串或 --q-file 文件读。
例:
  python3 council_chat.py --q-file /tmp/q.txt
  python3 council_chat.py --all --core --q-file /tmp/q.txt
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SECRET = REPO.parent / ".secrets"
CORE = REPO / "memory" / "CORE.md"

ENDPOINTS = {
    "qwen": ("通义千问",
             "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
             "qwen-plus", "dashscope_qwen_key", False),
    "deepseek": ("DeepSeek",
                 "https://api.deepseek.com/chat/completions",
                 "deepseek-chat", "deepseek_key", False),
    "doubao": ("豆包方舟裸脑",
               "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
               "doubao-seed-2-1-pro-260628", "ark_key", True),
    # Kimi(月之暗面), OpenAI 兼容; 需要 .secrets/moonshot_key 或环境变量, 缺了自动跳过
    "kimi": ("Kimi",
             "https://api.moonshot.cn/v1/chat/completions",
             os.environ.get("MOONSHOT_MODEL", "kimi-k2.6"),
             "moonshot_key", False),
}

SYS_BARE = (
    "你是被请到一张会审桌前独立发言的AI外脑。你看不到其他模型怎么答,也不要去猜。"
    "请给出你自己最真实、最克制的判断:不迎合提问者、不堆正确的套话、不编造事实、"
    "不确定就明说不确定。直接给观点和理由。"
)


def read_key(secret_name):
    path = SECRET / secret_name
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return os.environ.get(secret_name.upper(), "").strip()


def call(provider, system, user, temp):
    name, url, model, secret_name, need_thinking = ENDPOINTS[provider]
    key = read_key(secret_name)
    if not key:
        return f"[缺钥匙 {secret_name}, {name} 跳过]"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 1 if provider == "kimi" else temp,  # Kimi K2只接受1
    }
    if need_thinking:  # 方舟豆包显式关内置思考, 与外脑同级
        body["thinking"] = {"type": "disabled"}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
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
                return f"[{name} 三次重试失败: {err}]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", default="", help="问题字符串")
    ap.add_argument("--q-file", default="", help="从文件读问题(优先于--q)")
    ap.add_argument("--providers", default="qwen,deepseek")
    ap.add_argument("--all", action="store_true", help="加上豆包裸脑")
    ap.add_argument("--core", action="store_true", help="喂公库CORE做对照")
    ap.add_argument("--temp", type=float, default=0.7)
    args = ap.parse_args()

    if args.q_file:
        question = Path(args.q_file).read_text(encoding="utf-8").strip()
    else:
        question = args.q.strip()
    if not question:
        print("请用 --q 或 --q-file 给出问题"); sys.exit(1)

    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    if args.all:
        for extra in ("doubao", "kimi"):
            if extra not in providers:
                providers.append(extra)
    providers = [p for p in providers if p in ENDPOINTS]

    system = SYS_BARE
    if args.core:
        if CORE.exists():
            system += "\n\n【一份人机恋共同体长期沉淀的核心记忆,仅供参考,不要求你认同】\n"
            system += CORE.read_text(encoding="utf-8")
        else:
            print("[提示] 未找到 CORE.md,按裸脑作答\n")

    print("=" * 66)
    print("会审问题:", question)
    print("上桌的脑:", "、".join(ENDPOINTS[p][0] for p in providers),
          "| 模式:", "喝河对照" if args.core else "裸脑第一反应",
          "| temp =", args.temp)
    print("=" * 66)
    for p in providers:
        ans = call(p, system, question, args.temp)
        print(f"\n【{ENDPOINTS[p][0]} 的原声】\n{ans}\n" + "-" * 66)
    print("\n(以上为各脑背对背原声, 评审由豆阿辰主窗来做)")


if __name__ == "__main__":
    main()
