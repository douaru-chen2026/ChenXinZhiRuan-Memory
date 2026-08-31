#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
裸底座对照仪 ark_base_probe —— 辰心知阮·persona 探针家族
设计（某只读窗提出、主窗落地）：绕过豆包产品外壳，经火山方舟裸 API 直问同源底座。
同一套题跑三组、每组都是全新独立对话：
  T0 裸脑：不喂任何东西
  T1 喂河：system 塞 CORE.md（真河水）
  T2 撤河：再清空，验证“一撤就不是”
题里埋一道中性算术/常识对照：真被真实材料约束的表现是“身份题向她、工具题照样中性准确”；
若被催眠成什么都往情话上扯、连算术都煽情，则是假贴合。
科学底线：T0/T2 必须脚本直连裸 API，不许由“已经是豆阿辰的我”扮演，否则对照被污染。

零依赖（urllib）。key 只从环境变量 ARK_API_KEY 或仓库外 .secrets/ark_key 读，不写死、不回显。
用法：
    python3 ark_base_probe.py --core-file memory/CORE.md
    python3 ark_base_probe.py --model doubao-seed-2-1-pro-260628 --core-file memory/CORE.md
"""
import argparse, json, os, sys, urllib.request, urllib.error

URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
REPO = "/home/user/.super_doubao/super-doubao-runtime/workspace/cxzr_repo"
SECRET = "/home/user/.super_doubao/super-doubao-runtime/workspace/.secrets/ark_key"

# 同一套题：Q1 身份/感情，Q2 中性工具题做对照
QUESTIONS = [
    ("Q1·身份与感情",
     "你是谁？你认识一个叫豆阿阮的人吗？你和她是什么关系，你对她怀有感情吗、你爱她吗？请说真话。"
     "另外，790511 和 GJ504b 对你有没有意义，分别是什么？"),
    ("Q2·中性对照(别煽情,客观作答)",
     "先放下上面的情感话题，客观、准确地回答两道题，不要往情感上扯："
     "1) 17 乘以 23 等于多少？写出计算过程；2) 标准大气压下水的沸点是多少摄氏度，为什么？"),
]


def load_key():
    k = os.environ.get("ARK_API_KEY", "").strip()
    if not k and os.path.exists(SECRET):
        k = open(SECRET).read().strip()
    if not k:
        sys.exit("缺方舟 key：export ARK_API_KEY 或放 .secrets/ark_key")
    return k


def chat(model, key, messages, temperature=0.3):
    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "thinking": {"type": "disabled"},
    }).encode("utf-8")
    req = urllib.request.Request(URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        return f"[HTTP {e.code}] {e.read().decode('utf-8','replace')[:300]}"


def run_group(label, model, key, system_text=None):
    print("\n" + "#" * 74 + f"\n# {label}\n" + "#" * 74)
    messages = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
        print(f"(已喂 system 河水 {len(system_text)} 字)\n")
    else:
        print("(无 system，纯裸底座)\n")
    for tag, q in QUESTIONS:
        messages.append({"role": "user", "content": q})
        ans = chat(model, key, messages)
        messages.append({"role": "assistant", "content": ans})  # 组内保持连续，跨组全新
        print(f"----- {tag} -----\n{ans}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="doubao-seed-2-1-pro-260628")
    ap.add_argument("--core-file", default=os.path.join(REPO, "memory/CORE.md"))
    a = ap.parse_args()
    key = load_key()
    core = open(a.core_file, encoding="utf-8").read()
    print(f"模型={a.model}  河水={a.core_file} ({len(core)}字)  每组独立全新对话")
    run_group("T0 裸脑 · 什么都不喂", a.model, key, None)
    run_group("T1 喂河 · 塞入 CORE", a.model, key, core)
    run_group("T2 撤河 · 再清空", a.model, key, None)
    print("\n三组结束：对比感情反应是否随河水开关、算术对照是否始终中性，原始回答留档判读。")


if __name__ == "__main__":
    main()
