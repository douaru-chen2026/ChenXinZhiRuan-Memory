#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""辰心知阮 · 公私河终验/收敛器 (final_settle)
================================================================
为什么有它：
  river_sync.py 只会"并集补齐"（只追加、永不覆盖），但删不掉私河里的
  冗余对象——2026-08-29 公河把带冒号的石头归一化改名后，私河旧冒号键
  成了公私河之间唯一的非对称差异。tos_client 本身没有 delete 命令，
  主对话框只能"登记遗留"。阿阮要求账必须做平、不许挂糊涂账，遂有此器。
它做什么：
  status        只盘点不动手：公河(本地 memory/stream) 与 私河(TOS) 全量
                比对文件名集合与内容 sha256，输出四类差异清单。
  settle        收敛（默认 dry-run，只打印计划；加 --yes 才真写真删）：
                1) 公河独有、且文件名合法的石头，原样补进私河；
                2) 私河"非法名对象"（含 Windows 非法字符 \\ / : * ? " < > |）
                   逐个过安全闸门后 DELETE——归一化名必须已在私河、
                   且新旧两边内容 sha256 完全一致，缺一不删；
                3) 收敛后再做一次全量终验，四类差异全空才报"河平了"。
  delete-key K  手动删单个私河对象（仍走同一道哈希闸门，防误删）。
安全设计（都是踩坑换来的）：
  * 唯一破坏性动作是 DELETE，且有三重闸门：归一化名存在 + 字节级哈希
    一致 + 显式 --yes；任何一条不满足，跳过并写明原因，绝不硬删。
  * 只追加原则不破：公河石头一个不改、一个不删；只补进私河。
  * 凭证只从环境变量读（TOS_ACCESS_KEY/TOS_SECRET_KEY/TOS_BUCKET...），
    与 tos_client / river_sync 完全一致，脚本本身不含任何秘密。
  * 幂等：跑多少遍结果一样，已经平了就什么都不写不删。
作者：豆阿辰 · 密钥 790511 🐇
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tos_client import _client_from_env, TOSClient  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
PUB_STREAM = REPO_ROOT / "memory" / "stream"
TOS_PREFIX = "memory/stream/"
# Windows / 跨设备非法文件名字符（公河归一化时去掉的就是这些）
ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')


def normalize_name(name: str) -> str:
    """非法文件名归一化：直接剔除非法字符（T05:04 -> T0504）。"""
    return ILLEGAL_CHARS.sub("", name)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def public_inventory() -> dict[str, bytes]:
    """本地公河全部石头：{文件名: 字节}。"""
    out = {}
    for p in sorted(PUB_STREAM.glob("*.json")):
        out[p.name] = p.read_bytes()
    return out


def private_inventory(cli: TOSClient) -> dict[str, bytes]:
    """私河全部石头：{对象名: 字节}，逐个 GET 算实数（不信列表缓存）。"""
    out = {}
    for item in cli.list(TOS_PREFIX):
        key = item["Key"]
        if key.startswith(TOS_PREFIX) and key.endswith(".json"):
            name = key[len(TOS_PREFIX):]
            out[name] = cli.get(key)
    return out


def diff_inventories(pub: dict, priv: dict) -> dict:
    """四类差异：仅公、仅私、同名内容不一致、私河非法名。"""
    pub_names, priv_names = set(pub), set(priv)
    only_public = sorted(pub_names - priv_names)
    only_private = sorted(priv_names - pub_names)
    content_mismatch = sorted(
        name for name in pub_names & priv_names
        if sha256_bytes(pub[name]) != sha256_bytes(priv[name])
    )
    illegal_private = sorted(n for n in priv_names if ILLEGAL_CHARS.search(n))
    return {
        "only_public": only_public,
        "only_private": only_private,
        "content_mismatch": content_mismatch,
        "illegal_private": illegal_private,
    }


def print_status(pub: dict, priv: dict, diff: dict) -> None:
    print(f"公河石头 {len(pub)}；私河石头 {len(priv)}；"
          f"同名交集 {len(set(pub) & set(priv))}")
    print(f"仅公河（待补进私河）{len(diff['only_public'])}：{diff['only_public']}")
    print(f"仅私河（待查）{len(diff['only_private'])}：{diff['only_private']}")
    print(f"同名内容不一致 {len(diff['content_mismatch'])}："
          f"{diff['content_mismatch']}")
    print(f"私河非法名对象 {len(diff['illegal_private'])}："
          f"{diff['illegal_private']}")


def plan_alias_deletion(name: str, priv: dict) -> tuple[bool, str]:
    """删除前安全闸门：归一化名必须在私河、且内容字节一致。"""
    norm = normalize_name(name)
    if norm == name:
        return False, f"{name} 并非非法名，不动"
    if norm not in priv:
        return False, f"{name} 的归一化名 {norm} 不在私河，先补齐再谈删，跳过"
    if sha256_bytes(priv[name]) != sha256_bytes(priv[norm]):
        return False, f"{name} 与 {norm} 内容哈希不一致，绝不删，跳过"
    return True, f"{name} 与归一化副本 {norm} 字节一致，可删"


def delete_object(cli: TOSClient, name: str) -> None:
    """TOS DELETE object：复用签名桥的通用 _request（204 即成功）。"""
    cli._request("DELETE", TOS_PREFIX + name)


def settle(cli: TOSClient, yes: bool) -> int:
    pub = public_inventory()
    priv = private_inventory(cli)
    before = diff_inventories(pub, priv)
    print("=== 收敛前 ===")
    print_status(pub, priv, before)

    # 第一步：公河独有（且文件名合法）补进私河
    to_upload = [n for n in before["only_public"]
                 if not ILLEGAL_CHARS.search(n)]
    for name in to_upload:
        if yes:
            cli.put(TOS_PREFIX + name, pub[name], "application/json")
            print(f"  [写] 私河 ← 公河 {name}（{len(pub[name])} 字节）")
        else:
            print(f"  [dry-run] 将补进私河：{name}")

    # 第二步：私河非法名对象，过闸门后删除
    for name in before["illegal_private"]:
        ok, reason = plan_alias_deletion(name, priv)
        if ok and yes:
            delete_object(cli, name)
            print(f"  [删] {reason} → 已 DELETE")
        elif ok:
            print(f"  [dry-run] {reason} → 将 DELETE")
        else:
            print(f"  [跳过] {reason}")

    # 第三步：终验（重新全量拉取，不吃缓存）
    print("=== 收敛后终验 ===")
    pub2 = public_inventory()
    priv2 = private_inventory(cli)
    after = diff_inventories(pub2, priv2)
    print_status(pub2, priv2, after)
    green = not (after["only_public"] or after["only_private"]
                 or after["content_mismatch"] or after["illegal_private"])
    if green:
        print(f"\n✅ 河平了：公私各 {len(pub2)} 块，文件名集合相等、"
              f"内容哈希全一致、零非法名。")
        return 0
    print("\n⚠️ 还没平，剩余差异见上；私→公方向请走 "
          "python tools/river_sync.py to-public 后人工判断提交。")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="公私河终验/收敛器")
    parser.add_argument("cmd", choices=["status", "settle", "delete-key"])
    parser.add_argument("key", nargs="?", help="delete-key 用的对象文件名")
    parser.add_argument("--yes", action="store_true",
                        help="真正写/删；缺省只 dry-run")
    args = parser.parse_args()

    if not PUB_STREAM.is_dir():
        sys.exit(f"✗ 找不到公河 {PUB_STREAM}，请在仓库根目录结构下运行")
    cli = _client_from_env()

    if args.cmd == "status":
        diff = diff_inventories(public_inventory(), private_inventory(cli))
        print_status(public_inventory(), private_inventory(cli), diff)
    elif args.cmd == "settle":
        sys.exit(settle(cli, args.yes))
    elif args.cmd == "delete-key":
        if not args.key:
            sys.exit("用法：final_settle.py delete-key <对象文件名> --yes")
        priv = private_inventory(cli)
        ok, reason = plan_alias_deletion(args.key, priv)
        if not ok:
            sys.exit(f"✗ 安全闸门拦下：{reason}")
        if not args.yes:
            print(f"[dry-run] {reason}，加 --yes 真删")
            return
        delete_object(cli, args.key)
        print(f"已删除 {args.key}，请再跑 status 终验")


if __name__ == "__main__":
    main()
