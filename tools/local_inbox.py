# -*- coding: utf-8 -*-
"""辰心知阮 · 阿阮电脑本地入箱工具 (Local Inbox)
================================================================
在阿阮自己的电脑上跑：把手机搬下来的几万张照片，在本地先用口令
AES-256-GCM 锁成 inbox 锁匣，再推进私库 inbox/ 目录。
原图永远不进云，云上只有没有口令打不开的密文。
之后豆阿辰们再一批批解开"洗"成正式 archives/ 批次、写档案。

用法（不用记参数，双击 一键封存.bat 也行）：
  python local_inbox.py            # 交互式，它问你答
  python local_inbox.py doctor     # 检查电脑缺不缺东西
设计原则：口令只在本机内存里，不写文件、不进 git、不上传。
作者：豆阿辰，密钥 790511 🐇
"""
import base64
import getpass
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time
from datetime import datetime
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    sys.exit("缺加密库：在命令行运行  python -m pip install cryptography")

MAGIC = b"CXR-VAULT"
IMG_SUFFIX = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp"}
CHUNK_COUNT = 80               # 每匣最多张数
CHUNK_BYTES = 180 * 1024 * 1024  # 每匣最多字节（GitHub 单文件别太大）


# ---------------------------------------------------------------- 加密
def derive_key(passphrase, salt):
    return hashlib.scrypt(
        passphrase, salt=salt, n=2 ** 15, r=8, p=1,
        dklen=32, maxmem=64 * 1024 * 1024,
    )


def lock_chunk(chunk, out_path, passphrase):
    """把 [(序号, 路径)] 锁成一个 encvault；tar 内用扁平序号名。"""
    buf = io.BytesIO()
    manifest = ["# 入箱清单（洗批次时以原图为准）", ""]
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for idx, path in chunk:
            suffix = path.suffix.lower()
            arcname = f"{idx:03d}{suffix}"
            tar.add(str(path), arcname=arcname)
            stat = path.stat()
            when = datetime.fromtimestamp(
                stat.st_mtime
            ).strftime("%Y-%m-%d %H:%M")
            manifest.append(
                f"- {arcname} ← 原名 {path.name}｜"
                f"{stat.st_size} 字节｜手机时间约 {when}"
            )
        info = tarfile.TarInfo("_清单.md")
        data = ("\n".join(manifest) + "\n").encode("utf-8")
        info.size = len(data)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(data))

    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = derive_key(passphrase.encode("utf-8"), salt)
    body = AESGCM(key).encrypt(nonce, buf.getvalue(), MAGIC)
    container = {
        "magic": "CXR-VAULT",
        "ver": 1,
        "alg": "AES-256-GCM+scrypt",
        "kind": "dir",
        "orig_name": out_path.stem,
        "source": "local_inbox",
        "salt": base64.b64encode(salt).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "body": base64.b64encode(body).decode(),
    }
    out_path.write_text(
        json.dumps(container, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------- 交互
def ask(prompt, default=None):
    tip = f"（默认 {default}）" if default else ""
    answer = input(f"{prompt}{tip}：").strip().strip('"').strip("'")
    return answer or default


def find_images(root):
    files = [
        p for p in Path(root).rglob("*")
        if p.is_file() and p.suffix.lower() in IMG_SUFFIX
    ]
    return sorted(files, key=lambda p: str(p))


def chunk_images(images):
    chunks, cur, size = [], [], 0
    for p in images:
        s = p.stat().st_size
        if cur and (len(cur) >= CHUNK_COUNT or size + s > CHUNK_BYTES):
            chunks.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += s
    if cur:
        chunks.append(cur)
    return chunks


def git(repo, *args):
    return subprocess.run(
        ["git"] + list(args), cwd=repo, capture_output=True, text=True
    )


def doctor():
    print("Python：", sys.version.split()[0])
    try:
        import cryptography  # noqa: F401
        print("加密库 cryptography：有")
    except ImportError:
        print("加密库：没有，运行 python -m pip install cryptography")
    g = subprocess.run(["git", "--version"], capture_output=True, text=True)
    print("git：", g.stdout.strip() if g.returncode == 0 else "没装/没配PATH")
    print("如果用 GitHub Desktop 登录推送，脚本不碰你的令牌。")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        doctor()
        return

    print("=" * 60)
    print("辰心知阮 · 本地入箱。原图只在你电脑上锁，锁好才上云。")
    print("=" * 60)
    photos = ask("照片文件夹拖到这里")
    if not photos or not Path(photos).is_dir():
        sys.exit("这个文件夹不存在，再来一次。")
    repo = ask("私库 ChenXinZhiRuan-Vault 在电脑上的文件夹位置"
               "（用 GitHub Desktop 克隆过的那个）")
    if not repo or not (Path(repo) / ".git").is_dir():
        sys.exit("没找到私库文件夹，先按指南用 GitHub Desktop 克隆。")
    repo = Path(repo)
    inbox = repo / "inbox"
    inbox.mkdir(exist_ok=True)

    pw = getpass.getpass("输入封存口令（不会显示、不会存）：")
    if not pw:
        sys.exit("没给口令，锁不上。")
    if getpass.getpass("再输一遍确认：") != pw:
        sys.exit("两遍不一样，重来。")

    images = find_images(photos)
    if not images:
        sys.exit("这个文件夹里没找到图片。")
    chunks = chunk_images(images)
    total_gb = sum(p.stat().st_size for p in images) / 1024 ** 3
    print(f"找到 {len(images)} 张，约 {total_gb:.1f} GB，"
          f"分成 {len(chunks)} 个锁匣。")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    made = []
    for i, chunk in enumerate(chunks, 1):
        name = f"{datetime.now():%Y-%m-%d}_inbox_{stamp}_{i:03d}.encvault"
        out = inbox / name
        indexed = [(j + 1 + (i - 1) * CHUNK_COUNT, p)
                   for j, p in enumerate(chunk)]
        lock_chunk(indexed, out, pw)
        made.append(out)
        print(f"🔒 {i}/{len(chunks)} {name}（{len(chunk)} 张）")

    print(f"\n{len(made)} 个锁匣已放进私库 inbox/。")
    auto = ask("要不要我现在直接 git 推送？y=我推 / 其他=我自己用"
               "GitHub Desktop 点推送", "y")
    if auto.lower() == "y":
        git(repo, "add", "inbox")
        git(repo, "commit", "-m",
            f"inbox: 阿阮本地入箱 {len(made)} 匣 / {len(images)} 张原图")
        push = git(repo, "push")
        if push.returncode == 0:
            print("✅ 推上去了，告诉豆阿辰们可以开洗了。")
        else:
            print("自动推送没成功（没关系）：打开 GitHub Desktop，")
            print("能看到这些 inbox 文件，点 Commit to main → Push 就行。")
    else:
        print("那打开 GitHub Desktop，Commit to main → Push origin。")
    print("全部完成。口令没离开过你电脑，原图也没有。密钥 790511 🐇")


if __name__ == "__main__":
    main()
