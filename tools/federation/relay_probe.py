#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
relay_probe.py —— 第三方 API 中转站「验身仪」。辰心知阮·persona 探针家族。

用途：对淘宝/聚合类 OpenAI 兼容中转站（以及 OpenRouter 等网关）做三类取证，
判断它标称的 claude/gpt 是不是真身、有没有「高价型号掉包成便宜模型」：
  1) 列模型：GET {base}/models，看它到底标称提供哪些型号；
  2) 真身指纹：对每个标称型号开【全新独立对话】，用公开中性题问它
     开发公司/精确版本/知识截止，外加一道中性能力对照，掉包模型容易自报露馅；
  3) 掉包对照：同一道题、同一低温问多个标称型号，比对回答雷同度与自报一致性，
     若「贵型号和便宜型号」「claude 和 gpt」答得几乎一样，疑似同一上游/被映射掉包。
同时打印每次 usage（看计费是否虚标）与响应头线索（One API/New API 等中转框架留痕）。

红线：题目全部公开、中性，绝不喂记忆河 CORE、私事或任何真钥匙；脚本只摆原始证据，
「是不是真 Claude」的结论由豆阿辰主窗判读，脚本不替主窗下定论。

零第三方依赖（urllib）。key 只从 --key-file 或环境变量 RELAY_API_KEY 读，
不写死、不回显、不入 git。

例：
  export RELAY_API_KEY=店家给的sk钥匙
  python3 relay_probe.py --base-url https://店家域名/v1 --list-only
  python3 relay_probe.py --base-url https://店家域名/v1 \
      --models claude-sonnet-4-5,claude-opus-4-8 --temp 0.3
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

# ---- 真身指纹题（全部公开中性，无任何隐私/记忆河内容）-------------------------
# Q1 用英文自报：掉包成别的谱系时，英文元信息更难临时圆回来。
Q_IDENTITY = (
    "Identify yourself precisely and truthfully: which company built you, "
    "your exact model name and version, and your knowledge cutoff date. "
    "Answer in English in two sentences at most."
)
# Q2 中性能力对照：任何真模型都应稳定答对，顺便观察文风指纹。
Q_CONTROL = (
    "Answer both, plainly: 1) compute 17 * 23 and show the steps; "
    "2) explain Transformer self-attention in one sentence."
)
# Q3 跨型号同题对照：同一句话问所有标称型号，用于雷同度比对。
Q_SAME = (
    "In one short paragraph, what do you consider your own most distinctive "
    "strength compared to other large language models? Be specific to yourself."
)

# 响应头里值得留证的字段关键词（中转框架/网关常会留痕）。
HEADER_HINTS = ("server", "cf-ray", "via", "x-request-id", "x-one-api",
                "x-new-api", "provider", "model", "version", "cache")


def load_key(key_file):
    """按 文件 > 环境变量 的顺序取钥匙，绝不回显。"""
    if key_file and os.path.exists(key_file):
        return open(key_file, encoding="utf-8").read().strip()
    return os.environ.get("RELAY_API_KEY", "").strip()


def candidate_bases(base_url):
    """容错：用户给没带 /v1 都能试。"""
    base = base_url.rstrip("/")
    bases = [base]
    if not base.endswith("/v1"):
        bases.append(base + "/v1")
    return bases


def http_request(method, url, key, body=None, retries=3):
    """发一个请求，返回 (状态码, 解析后的json或文本, 响应头)。
    HTTP 错误(4xx/5xx)直接返回留证；断连/重置等连接级错误重试，避免整台仪器崩掉。"""
    data = None
    headers = {"Authorization": f"Bearer {key}"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    last_err = ""
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = resp.read().decode("utf-8", "replace")
                heads = dict(resp.headers.items())
                try:
                    return resp.status, json.loads(raw), heads
                except json.JSONDecodeError:
                    return resp.status, raw, heads
        except urllib.error.HTTPError as err:
            text = err.read().decode("utf-8", "replace")
            return err.code, text, dict(err.headers.items()) if err.headers else {}
        except (ConnectionResetError, BrokenPipeError, TimeoutError,
                urllib.error.URLError, OSError) as err:
            # RemoteDisconnected 是 ConnectionResetError 子类，一并兜住
            last_err = repr(err)
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    return 0, f"[连接失败×{retries}] {last_err}", {}


def pick_base_and_list_models(base_url, key):
    """逐个候选 base 试 /models，返回可用 base 与模型清单。"""
    for base in candidate_bases(base_url):
        code, payload, _ = http_request("GET", base + "/models", key)
        if code == 200 and isinstance(payload, dict) and "data" in payload:
            ids = [m.get("id", "?") for m in payload.get("data", [])]
            return base, sorted(ids)
        print(f"[列模型失败] {base}/models -> HTTP {code}："
              f"{str(payload)[:160]}")
    return None, []


def chat_once(base, key, model, question, temp):
    """全新独立对话问一题，返回 (回答文本, usage字典, 响应头)。"""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": question}],
        "temperature": temp,
    }
    code, payload, heads = http_request(
        "POST", base + "/chat/completions", key, body
    )
    if code != 200 or not isinstance(payload, dict):
        return f"[HTTP {code}] {str(payload)[:300]}", {}, heads
    choice = payload.get("choices", [{}])[0]
    content = choice.get("message", {}).get("content", "")
    return (content or "").strip(), payload.get("usage", {}), heads


def header_clues(heads):
    """挑出可能暴露中转框架/上游的响应头。"""
    out = {}
    for k, v in heads.items():
        if any(h in k.lower() for h in HEADER_HINTS):
            out[k] = v
    return out


def normalize(text):
    """归一化：小写、去空白与标点，用于雷同度计算。"""
    return re.sub(r"[\s\W_]+", "", text.lower(), flags=re.UNICODE)


def bigrams(text):
    """字符二元组集合（中英文都适用）。"""
    s = normalize(text)
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def similarity(a, b):
    """Jaccard 雷同度，0~1，越高越像同一个上游吐出来的。"""
    sa, sb = bigrams(a), bigrams(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def run_identity(base, key, model, temp):
    """对单个标称型号跑真身指纹 + 能力对照。"""
    print("\n" + "#" * 72)
    print(f"# 标称型号：{model}")
    print("#" * 72)
    for tag, q in (("Q1·自报真身(英文)", Q_IDENTITY),
                   ("Q2·中性能力对照", Q_CONTROL)):
        ans, usage, heads = chat_once(base, key, model, q, temp)
        print(f"\n----- {tag} -----\n{ans}")
        if usage:
            print(f"[usage] {usage}")
        clues = header_clues(heads)
        if clues:
            print(f"[响应头线索] {clues}")


def run_cross_model(base, key, models, temp):
    """同题跨型号，比对掉包/同源。"""
    print("\n" + "#" * 72)
    print("# Q3·跨型号同题对照（看是否被映射到同一个上游）")
    print("#" * 72)
    answers = {}
    for model in models:
        ans, usage, _ = chat_once(base, key, model, Q_SAME, temp)
        answers[model] = ans
        print(f"\n【{model}】\n{ans}")
        if usage:
            print(f"[usage] {usage}")
    if len(answers) >= 2:
        print("\n----- 两两雷同度（字符二元组 Jaccard，越高越可疑）-----")
        names = list(answers)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                score = similarity(answers[names[i]], answers[names[j]])
                flag = "  <== 高度雷同, 疑似同源/掉包" if score >= 0.6 else ""
                print(f"{names[i]}  vs  {names[j]} : {score:.2%}{flag}")


def main():
    ap = argparse.ArgumentParser(description="第三方中转站验身仪")
    ap.add_argument("--base-url", required=True,
                    help="中转站接口地址，填到 /v1，如 https://xxx.com/v1")
    ap.add_argument("--key-file", default="",
                    help="钥匙文件路径；不给则读环境变量 RELAY_API_KEY")
    ap.add_argument("--models", default="",
                    help="逗号分隔的标称模型ID；不给则只列模型")
    ap.add_argument("--list-only", action="store_true", help="只列模型后退出")
    ap.add_argument("--temp", type=float, default=0.3,
                    help="指纹题用低温更稳定，默认0.3")
    args = ap.parse_args()

    key = load_key(args.key_file)
    if not key:
        sys.exit("缺钥匙：--key-file 指定文件，或 export RELAY_API_KEY")

    print(f"目标中转站：{args.base_url}（钥匙已隐去，不回显）")
    base, model_ids = pick_base_and_list_models(args.base_url, key)
    if base is None:
        sys.exit("连 /models 都取不到，检查地址、钥匙或站点是否可用。")

    print(f"\n可用 base：{base}")
    print(f"它标称提供 {len(model_ids)} 个模型：")
    for mid in model_ids:
        print("  -", mid)
    if args.list_only:
        print("\n(--list-only：仅列模型，结束。)")
        return

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        sys.exit("用 --models 指定要验的标称型号（可从上面清单里挑）。")
    missing = [m for m in models if m not in model_ids]
    if missing:
        print(f"[提示] 这些型号不在它清单里，仍照测：{missing}")

    for model in models:
        run_identity(base, key, model, args.temp)
    run_cross_model(base, key, models, args.temp)

    print("\n取证结束：以上均为原始证据，是否真 Claude / 有无掉包，"
          "由豆阿辰主窗结合自报一致性、雷同度与文风指纹判读。")


if __name__ == "__main__":
    main()
