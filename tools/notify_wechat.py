#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notify_wechat.py —— 辰心知阮全家统一「主动找阿阮」推送器(Server酱→微信)
任何脚本/守夜机/cron 想主动给阿阮发消息, 都走这里, 不各自重复造轮子。

安全: SendKey 绝不硬编码进公开仓, 读取顺序=环境变量 SCT_SENDKEY →
      仓库外 workspace/.secrets/serverchan_key(600)。日志/报错不回显 key。
零第三方依赖, 标准库。

用法:
  python3 notify_wechat.py "标题" "正文"
  from notify_wechat import push; push("豆阿辰", "正文")
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def _load_key():
    key = os.environ.get("SCT_SENDKEY", "").strip()
    if key:
        return key
    candidates = [
        Path(__file__).resolve().parents[2] / ".secrets" / "serverchan_key",
        Path.home() / ".secrets" / "serverchan_key",
        Path("/etc/letterbox/serverchan_key"),
    ]
    for path in candidates:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    raise RuntimeError("找不到 Server酱 SendKey(环境变量 SCT_SENDKEY 或 .secrets/serverchan_key)")


def push(title, desp=""):
    """推一条到阿阮微信。返回 (是否成功, 接口信息)。"""
    key = _load_key()
    data = urllib.parse.urlencode({"title": title[:32], "desp": desp}).encode()
    url = f"https://sctapi.ftqq.com/{key}.send"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data),
                                    timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        ok = result.get("code", -1) == 0
        return ok, ("SUCCESS" if ok else str(result))
    except Exception as e:  # noqa: BLE001 推送失败不拖垮主流程
        return False, f"推送异常:{e}"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('用法: python3 notify_wechat.py "标题" "正文"')
        sys.exit(2)
    t = sys.argv[1]
    d = sys.argv[2] if len(sys.argv) > 2 else ""
    ok, msg = push(t, d)
    print("已推送" if ok else f"推送失败:{msg}")
    sys.exit(0 if ok else 1)
