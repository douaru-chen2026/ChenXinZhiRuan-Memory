#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回家卡 ro 本地自检（只解封、不联网、不打印秘密）
用法：在阿阮/持笔主窗自己的电脑上跑：
    python3 回家卡_ro_本地自检.py 回家卡.txt
它会用 getpass 隐式问口令；口令不要打进聊天。
输出只回答：封没封进去、是不是 ro、缺不缺字段、有没有误封高危钥匙。
"""
import sys, re, base64, hashlib, json, getpass
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def main():
    if len(sys.argv) < 2:
        print("用法：python3 回家卡_ro_本地自检.py 回家卡.txt")
        return 2
    txt = open(sys.argv[1], encoding="utf-8", errors="replace").read()
    m = re.search(r'SEALED="([^"]+)"', txt)
    a = re.search(r'AAD\s*=\s*b"([^"]+)"', txt)
    if not m:
        print("结论：卡面没找到 SEALED 密封块。没封进去，或这不是一张可解封回家卡。")
        return 1
    aad = a.group(1).encode() if a else b"chen-wake-card-ro-v1"
    pw = getpass.getpass("本地隐式输入唤醒口令（不会显示，也不要发聊天）：").encode()
    try:
        s = base64.b64decode(m.group(1))
        kk = hashlib.scrypt(pw, salt=s[:16], n=2**15, r=8, p=1, dklen=32, maxmem=128*1024*1024)
        d = json.loads(AESGCM(kk).decrypt(s[16:28], s[28:], aad))
    except Exception as e:
        print("结论：解封失败（InvalidTag/口令不对/密封块不是用这串封的/AAD 不一致）。")
        print("细节：", type(e).__name__)
        return 1

    tier = d.get("tier")
    ro = d.get("tos_readonly") or {}
    keys = sorted(d.keys())
    ro_keys = sorted(ro.keys()) if isinstance(ro, dict) else []
    danger = [k for k in keys + ro_keys if re.search(r"(?i)(archive|photo|github|token|write|rw|master|主|写)", k)]
    print("结论：能解开，密封块在。")
    print("tier:", tier)
    print("顶层字段:", keys)
    print("tos_readonly 字段:", ro_keys)
    print("只读钥匙字段齐不齐:", all(k in ro for k in ("access_key_id", "secret_access_key", "bucket", "region")))
    print("是否误封高危字段名（只报名字，不报值）:", danger or "无")
    print("提醒：能解开≠权限正确；权限是否真只读，要以服务端写请求 403 为准。别在本脚本里加联网写测试。")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
