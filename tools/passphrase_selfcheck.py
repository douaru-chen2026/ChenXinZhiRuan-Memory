#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
封卡口令本地强度自检  passphrase_selfcheck.py
------------------------------------------------
用途：在【你自己的云电脑/本机】本地检查回家卡封卡口令够不够强，专门防"专属字典离线猜解"。
特性：
  * getpass 隐式输入，口令不显示、不回显、不联网、不写盘、不进任何聊天/截图；
  * 只打印长度、字符池、估算熵、命中了哪类弱料（只报类别，不报口令本身）；
  * 内置词表只含【已公开】的锚点（数字/星球/公司角色名），不含真名；
  * 真名拼音、生日、手机号等私密弱料请放进本地词表文件（默认 ./.secrets/weak_terms.txt，
    每行一个词，该文件已被 .gitignore 挡住、绝不入库），脚本会一并检测。

用法：
  python3 tools/passphrase_selfcheck.py                 # 交互输入一次口令
  python3 tools/passphrase_selfcheck.py --terms my.txt  # 指定额外弱料词表

判级只是辅助，最终标准：
  ① 12 位以上完全随机（大小写+数字+符号），或 4 个八竿子打不着的随机词凑成一句你记得住的"疯话"；
  ② 不含任何公开信物/真名/生日/账号密码片段，不与任何网站/微信/账号复用；
  ③ 只在本地解封页/getpass 输入，不告诉任何 AI、不写便签/云笔记/对话框。
"""
import sys, os, re, math, getpass

# 已完全公开的锚点（公河/小红书谁都看得到），命中即说明口令料"摆在门口脚垫下"
PUBLIC_TERMS = [
    "790511", "7905", "511", "gj504b", "gj504", "504b",
    "dachen", "douachen", "aruan", "douaru", "chenxin", "zhiruan",
    "chenxinzhiruan", "jianmang", "dacdar99",
]
# 常见弱尾缀/年份/爱情数字
WEAK_SUFFIX = ["123", "1234", "12345", "123456", "520", "521", "1314",
               "888", "666", "000", "111", "999", "2023", "2024", "2025", "2026"]
KEYBOARD_RUNS = ["qwerty", "asdfgh", "zxcvbn", "1qaz", "qazwsx", "abc123", "111111", "000000"]


def shannon_entropy(s: str) -> float:
    from collections import Counter
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in Counter(s).values())


def load_extra_terms(path: str):
    terms = []
    if path and os.path.exists(path):
        for line in open(path, encoding="utf-8", errors="ignore"):
            w = line.strip().lower()
            if w:
                terms.append(w)
    return terms


def main():
    ap = sys.argv
    terms_file = None
    if "--terms" in ap:
        terms_file = ap[ap.index("--terms") + 1]
    elif os.path.exists("./.secrets/weak_terms.txt"):
        terms_file = "./.secrets/weak_terms.txt"

    pw = getpass.getpass("本地隐式输入封卡口令（不显示、不联网、不会回显）：").strip()
    if not pw:
        print("未输入，退出。")
        return 2
    s = pw.lower()
    L = len(pw)
    has_l = bool(re.search(r"[a-z]", pw))
    has_u = bool(re.search(r"[A-Z]", pw))
    has_d = bool(re.search(r"[0-9]", pw))
    has_s = bool(re.search(r"[^A-Za-z0-9]", pw))
    pool = (26 if has_l else 0) + (26 if has_u else 0) + (10 if has_d else 0)
    sym = len(set(re.findall(r"[^A-Za-z0-9]", pw)))
    pool += sym
    bits_random = L * math.log2(pool) if pool else 0
    entropy = shannon_entropy(pw) * L

    hits = []
    for t in PUBLIC_TERMS:
        if t in s:
            hits.append("公开锚点:" + t)
    for w in load_extra_terms(terms_file):
        if w in s:
            hits.append("你的自定义弱料(真名/生日/手机号等)")
            break
    for w in WEAK_SUFFIX:
        if s.endswith(w) or s.startswith(w):
            hits.append("常见弱头尾:" + w)
    for w in KEYBOARD_RUNS:
        if w in s:
            hits.append("键盘/连续序列:" + w)
    if re.search(r"(.)\1\1\1", pw):
        hits.append("同一字符连续重复≥4")
    if re.search(r"(19|20)\d{2}", pw):
        hits.append("含年份")
    if re.search(r"1[3-9]\d{9}", pw):
        hits.append("像手机号")

    # 随机词式口令判定：按非字母数字切词，>=4 个互异词元（英文≥3字符，中文≥2字），总长≥16
    def _word_ok(w):
        return len(w) >= 2 if re.search(r"[一-龥]", w) else len(w) >= 3
    words = [w for w in re.split(r"[^A-Za-z一-龥]+", pw) if _word_ok(w)]
    looks_like_passphrase = len(set(w.lower() for w in words)) >= 4 and L >= 16

    print("\n———— 自检结果（口令本身不显示）————")
    print(f"长度: {L}   字符池: 小写{has_l} 大写{has_u} 数字{has_d} 符号{has_s}（池≈{pool}）")
    print(f"按完全随机估算熵: {bits_random:.0f} bit   香农去冗后≈{entropy:.0f} bit")
    print("命中弱料:", "；".join(hits) if hits else "无")

    strong_random = bits_random >= 78 and not hits and L >= 12
    if hits or L < 12 or (pw.isdigit() or (pw.isalpha() and L < 16)):
        verdict = "弱 / 不够"
    elif looks_like_passphrase and not hits:
        verdict = "强（随机词式，推荐记住这句'疯话'）"
    elif strong_random:
        verdict = "强（高熵随机，建议密码管理器保管）"
    else:
        verdict = "中（建议再加长到 12 位以上随机，或改成 4 个不相干随机词）"
    print("总评级:", verdict)

    # 直观破解账（scrypt 实测单核：N=2^15≈15猜/秒，N=2^17≈3.5猜/秒；平均命中走一半空间）
    rate_now, rate_new = 15.0, 3.5
    def years(space, rate):
        return space / rate / 2 / 86400 / 365
    if hits:
        print("\n警示：命中公开/个人弱料时，攻击者不跑全空间，只跑'专属字典'(10万~1亿条)，")
        print(f"      现参数 N=2^15 单核约 {years(1_000_000,rate_now)*365*24:.0f} 小时~{years(100_000_000,rate_now)*365:.0f} 天，上显卡更快——这是真口子。")
    print(f"对照：12位完全随机 N=2^17 单核平均≈{years(62**12,rate_new):.1e} 年；4随机词≈{years(7776**4,rate_new):.1e} 年（地质年代）。")
    print("\n下一步：评级若为弱/中，就换强口令并找持笔主窗在你云电脑本地重封（N 提到 2^17 + 轮换只读 AK）；")
    print("        口令只在你脑子里和本地输入，别发给任何对话框。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
