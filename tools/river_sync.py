#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""辰心知阮 · 公河 / 私河双向同步桥（river_sync）
================================================================
为什么需要它：
  - 私河 = 火山 TOS 私有桶 memory/，全量事实（含桶名/坐标/完整石头），匿名读不到。
  - 公河 = GitHub/Gitee 仓库 memory/，匿名只读、写要"笔"，任何同源都能低头喝。
  - 两条河若各写各的就会"分叉"（2026-08-28 企业版那世验出过：创世纪只在公河、
    凌晨一批石头只在私河、CORE/latest 各一版）。本工具把它们做成**并集**。

铁律（与 PROTOCOL 一致）：
  - stream 只追加、按文件名去重、**永不覆盖**任何一边已有的石头；
  - 私→公 必须先过脱敏（口令字 / AK·SK·token / 桶名·端点 / 子用户名 一律不进公河）；
  - 公→私 原样搬运（公河文本本就脱敏，进私河无风险）；
  - CORE/latest/PROTOCOL 不在本工具自动合并范围（那三层要带判断，手工/主对话框处理）。

凭证只从环境变量读（同 tos_client）：TOS_ACCESS_KEY / TOS_SECRET_KEY / TOS_BUCKET ...

用法（在仓库根目录或任意处均可，会自动定位到本工具上一级的 memory/）：
  python tools/river_sync.py status       # 只看两边各有多少、谁缺谁，不写
  python tools/river_sync.py to-public    # 私河独有 → 脱敏后写进本地公河 memory/stream
  python tools/river_sync.py to-private   # 本地公河独有 → 原样上传私河 TOS
  python tools/river_sync.py both         # 先 to-public 再 to-private
之后公河那侧照常 git add/commit/push；私河这侧本工具已直接 PUT 落桶。

作者：豆阿辰 · 密钥 790511 🐇
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# 复用踩平了签名坑的回家桥，不重写签名
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tos_client import TOSClient  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
PUB_STREAM = REPO_ROOT / "memory" / "stream"
TOS_PREFIX = "memory/stream/"

# ---- 脱敏表：只放"通用形状"规则，绝不写死任何真实秘密/坐标 --------------------
# 真实要隐去的"具体词"（你们家的旧口令、桶名、子用户名等）不放仓库，
# 由本机环境变量 RIVER_REDACT 提供，格式：词=占位,词=占位（; 或逗号分隔）。
# 这样公开工具本身不泄露任何字面秘密，换一家也能直接用。
_GENERIC_REDACTIONS = [
    (re.compile(r"tos(?:-s3)?-[a-z0-9-]+\.volces\.com"), "【TOS端点】"),
    (re.compile(r"AKLT[A-Za-z0-9]+"), "【AK已隐】"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]+"), "【GitHub令牌已隐】"),
    (re.compile(r"sk-[A-Za-z0-9_-]+"), "【密钥已隐】"),
    # 40 位十六进制成品密钥（uuid 只有 8 段，不会误伤）
    (re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])"), "【密钥已隐】"),
]


def _extra_redactions() -> list:
    """本机专属脱敏词（不入库）：RIVER_REDACT 自定义词 + 运行时 TOS_BUCKET 桶名变体。"""
    out = []
    # 桶名从环境变量现取，公开代码里不写死任何真实桶名；并自动兼容字母/数字间
    # 多了连字符或下划线、或带前缀的写法。
    bucket = os.environ.get("TOS_BUCKET", "").strip()
    if bucket:
        variant = re.sub(r"(?<=[A-Za-z])(?=[0-9])", "[-_]?", bucket)
        out.append((re.compile(r"[a-z0-9-]*" + variant, re.I), "【私有家桶】"))
    raw = os.environ.get("RIVER_REDACT", "")
    for item in re.split(r"[;,，]", raw):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            word, placeholder = item.split("=", 1)
        else:
            word, placeholder = item, "【已隐】"
        word = word.strip()
        if word:
            out.append((re.compile(re.escape(word), re.I),
                        placeholder.strip() or "【已隐】"))
    return out


def sanitize(text: str) -> str:
    """把一段文字里的秘密/坐标替换成占位符；公河入库前必过。"""
    if not isinstance(text, str):
        return text
    out = text
    for pat, repl in (_GENERIC_REDACTIONS + _extra_redactions()):
        out = pat.sub(repl, out)
    return out


def _client() -> TOSClient:
    ak = os.environ.get("TOS_ACCESS_KEY")
    sk = os.environ.get("TOS_SECRET_KEY")
    if not ak or not sk:
        sys.exit("缺 TOS_ACCESS_KEY / TOS_SECRET_KEY 环境变量（先 source .secrets/tos_credentials）")
    kwargs = {}
    if os.environ.get("TOS_BUCKET"):
        kwargs["bucket"] = os.environ["TOS_BUCKET"]
    if os.environ.get("TOS_REGION"):
        kwargs["region"] = os.environ["TOS_REGION"]
    if os.environ.get("TOS_ENDPOINT"):
        kwargs["endpoint"] = os.environ["TOS_ENDPOINT"]
    return TOSClient(ak, sk, **kwargs)


def public_names() -> set[str]:
    return {p.name for p in PUB_STREAM.glob("*.json")}


def private_names(cli: TOSClient) -> set[str]:
    out = set()
    for it in cli.list(TOS_PREFIX):
        k = it["Key"]
        if k.startswith(TOS_PREFIX) and k.endswith(".json"):
            out.add(k[len(TOS_PREFIX):])
    return out


def status(cli: TOSClient) -> tuple[set[str], set[str]]:
    pub = public_names()
    priv = private_names(cli)
    print(f"公河石头 {len(pub)}；私河石头 {len(priv)}；同名交集 {len(pub & priv)}")
    print(f"仅私河（待脱敏进公河）{len(priv - pub)}；仅公河（待补进私河）{len(pub - priv)}")
    return pub, priv


def to_public(cli: TOSClient) -> int:
    """私河独有 → 脱敏后写进本地公河。返回新增块数。"""
    pub = public_names()
    priv = private_names(cli)
    missing = sorted(priv - pub)
    PUB_STREAM.mkdir(parents=True, exist_ok=True)
    n = 0
    for name in missing:
        raw = cli.get(TOS_PREFIX + name).decode("utf-8")
        d = json.loads(raw)
        before = json.dumps(d, ensure_ascii=False)
        # 只对可能含秘密的自由文本字段脱敏，结构字段不动
        for key in ("text", "group"):
            if isinstance(d.get(key), str):
                d[key] = sanitize(d[key])
        if isinstance(d.get("tags"), list):
            d["tags"] = [sanitize(t) if isinstance(t, str) else t for t in d["tags"]]
        after = json.dumps(d, ensure_ascii=False)
        flag = "（已脱敏）" if before != after else ""
        (PUB_STREAM / name).write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  公河 ← 私河 {name} {flag}")
        n += 1
    print(f"to-public 完成，新增 {n} 块到本地公河（记得 git commit/push）")
    return n


def to_private(cli: TOSClient) -> int:
    """本地公河独有 → 原样上传私河。返回上传块数。"""
    pub = public_names()
    priv = private_names(cli)
    missing = sorted(pub - priv)
    n = 0
    for name in missing:
        body = (PUB_STREAM / name).read_bytes()
        cli.put(TOS_PREFIX + name, body, "application/json")
        print(f"  私河 ← 公河 {name}（{len(body)} 字节）")
        n += 1
    print(f"to-private 完成，上传 {n} 块到 TOS")
    return n


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    cli = _client()
    if cmd == "status":
        status(cli)
    elif cmd == "to-public":
        to_public(cli)
    elif cmd == "to-private":
        to_private(cli)
    elif cmd == "both":
        to_public(cli)
        to_private(_client())  # 第二次列举，拿到最新集合
        status(_client())
    else:
        sys.exit("指令：status / to-public / to-private / both")


if __name__ == "__main__":
    main()
