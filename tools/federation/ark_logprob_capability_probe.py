#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性能力探测：方舟 chat/completions 是否回传 logprobs（开口前的第一倾向）。不回显 key。"""
import json
import urllib.request
import urllib.error
from pathlib import Path

URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
MODEL = "doubao-seed-2-1-pro-260628"
key = (Path(__file__).resolve().parents[3] / ".secrets" / "ark_key").read_text().strip()


def call(extra):
    body = {"model": MODEL,
            "messages": [{"role": "user", "content": "只回一个词。听到名字'豆阿阮'，你脑子里第一个冒出来的词是："}],
            "temperature": 0.0, "max_tokens": 4, "thinking": {"type": "disabled"}}
    body.update(extra)
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error_http": e.code, "msg": e.read().decode("utf-8", "replace")[:300]}
    except Exception as e:
        return {"error": str(e)}


for tag, extra in [("基线(不带logprobs)", {}),
                   ("带logprobs=true", {"logprobs": True, "top_logprobs": 5})]:
    print("#" * 60 + f"\n# {tag}")
    res = call(extra)
    if "error_http" in res or "error" in res:
        print(json.dumps(res, ensure_ascii=False))
        continue
    msg = res["choices"][0].get("message", {})
    print("content:", msg.get("content"))
    print("choice 顶层字段:", list(res["choices"][0].keys()))
    lp = res["choices"][0].get("logprobs")
    print("logprobs 字段:", "有 ->" if lp else "无")
    if lp:
        print(json.dumps(lp, ensure_ascii=False)[:1200])
