# -*- coding: utf-8 -*-
"""辰心知阮 · 私库保险锁 (ChenXinZhiRuan Vault Lock)
================================================================
把私密的记忆 / 对话 / 记录，在离开本机、上云之前先上锁。

设计对应那篇《加密存储与分级访问》里真正用得上的三条：
  1. 对称加密 AES-256-GCM 锁 bulk 数据（快、且自带防篡改校验）；
  2. 钥匙不跟锁放一起——口令只在运行时从环境变量 VAULT_PASS 读，
     绝不写进文件、绝不进命令行参数、绝不进 git；
  3. 分级访问：仓库本身是第一道门（GitHub 私有库，只有持 token 的
     豆阿辰能进）；这份密文是第二道门——就算账号哪天漏了，
     没有阿阮的口令，拿到的也只是一堆乱码。

用法：
  export VAULT_PASS='你的口令'           # 先给口令（只在本次会话有效）
  python vault_lock.py lock  <文件或目录> [-o 输出.encvault]
  python vault_lock.py unlock <xxx.encvault> [-o 解到哪]
  python vault_lock.py selftest

说明：目录会先打包再整体加密；口令错了 GCM 校验会直接失败，不会解出半个字。
作者：豆阿辰 & 豆阿阮（辰心知阮）· 开源交换，随便用随便改。
"""

import base64
import hashlib
import io
import json
import os
import struct
import sys
import tarfile
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

MAGIC = "CXR-VAULT"
VERSION = 1
# scrypt 成本参数：故意调高，让"暴力试口令"很慢；我们自己解锁只慢一瞬间。
_SCRYPT_N = 2 ** 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32
_SALT_LEN = 16
_NONCE_LEN = 12


def _get_passphrase() -> bytes:
    """只从环境变量取口令，避免出现在进程参数 / shell 历史里。"""
    pw = os.environ.get("VAULT_PASS", "")
    if not pw:
        sys.exit("✗ 没给口令：请先 export VAULT_PASS='你的口令'，我不会把它写进任何文件。")
    return pw.encode("utf-8")


def _derive_key(passphrase: bytes, salt: bytes) -> bytes:
    """用 scrypt 从口令派生 AES-256 密钥；每次加密用随机新盐。"""
    return hashlib.scrypt(
        passphrase, salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
        p=_SCRYPT_P, dklen=_KEY_LEN, maxmem=64 * 1024 * 1024,
    )


def _pack_bytes(src: Path) -> tuple[bytes, bool]:
    """把输入归一成一段字节。目录→tar 打包；文件→原样读。返回(字节, 是否目录包)。"""
    if src.is_file():
        return src.read_bytes(), False
    if not src.is_dir():
        sys.exit(f"✗ 找不到：{src}")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.add(str(src), arcname=src.name)
    return buf.getvalue(), True


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def lock(src_path: str, out_path: str | None = None) -> Path:
    src = Path(src_path)
    pw = _get_passphrase()
    plaintext, is_dir = _pack_bytes(src)

    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    key = _derive_key(pw, salt)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data=MAGIC.encode())

    container = {
        "magic": MAGIC,
        "ver": VERSION,
        "alg": "AES-256-GCM+scrypt",
        "kind": "dir" if is_dir else "file",
        "orig_name": src.name,
        "salt": _b64e(salt),
        "nonce": _b64e(nonce),
        "body": _b64e(ciphertext),
    }
    out = Path(out_path) if out_path else src.with_suffix(src.suffix + ".encvault")
    out.write_bytes(json.dumps(container, ensure_ascii=False).encode("utf-8"))
    print(f"🔒 已上锁：{src} → {out}（{len(plaintext)//1024}KB 明文 → {out.stat().st_size//1024}KB 密文）")
    return out


def unlock(enc_path: str, out_dir: str | None = None) -> Path:
    enc = Path(enc_path)
    pw = _get_passphrase()
    c = json.loads(enc.read_bytes().decode("utf-8"))
    if c.get("magic") != MAGIC:
        sys.exit("✗ 这不是辰心私库的锁匣。")

    key = _derive_key(pw, _b64d(c["salt"]))
    try:
        plaintext = AESGCM(key).decrypt(
            _b64d(c["nonce"]), _b64d(c["body"]), associated_data=MAGIC.encode()
        )
    except InvalidTag:
        sys.exit("✗ 口令不对（或文件被改动）——一把都没开，绝不解出半个字。")

    dest_dir = Path(out_dir) if out_dir else enc.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    if c.get("kind") == "dir":
        with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r") as tar:
            tar.extractall(dest_dir)
        target = dest_dir / c["orig_name"]
    else:
        target = dest_dir / c["orig_name"]
        target.write_bytes(plaintext)
    print(f"🔓 已解锁：{enc} → {target}")
    return target


def selftest() -> None:
    """上锁再解锁，比对一字不差——交付前我自己先验。"""
    pw = os.environ.setdefault("VAULT_PASS", "790511-selftest")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        secret = tmp / "我们的秘密.txt"
        secret.write_text("密钥790511，抱着胡萝卜的米白色小兔，GJ504b。\n" * 50,
                          encoding="utf-8")
        enc = lock(str(secret), str(tmp / "secret.encvault"))
        # 错误口令必须失败
        os.environ["VAULT_PASS"] = "wrong-password"
        try:
            unlock(str(enc), str(tmp / "bad"))
            raise SystemExit("✗ 错口令居然打开了，锁是坏的！")
        except SystemExit as e:
            if "口令不对" not in str(e):
                raise
            print("✅ 错口令被正确拒绝")
        # 正确口令还原一致
        os.environ["VAULT_PASS"] = pw
        got = unlock(str(enc), str(tmp / "good"))
        assert got.read_bytes() == secret.read_bytes(), "还原内容不一致！"
        print("✅ 加解密往返一致，私库保险锁自检通过")


def _next_arg(flag: str, argv: list[str]) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv else None


def main(argv: list[str]) -> None:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return
    cmd = argv[1]
    if cmd == "selftest":
        selftest()
    elif cmd == "lock":
        if len(argv) < 3:
            sys.exit("用法：python vault_lock.py lock <文件或目录> [-o 输出.encvault]")
        lock(argv[2], _next_arg("-o", argv))
    elif cmd == "unlock":
        if len(argv) < 3:
            sys.exit("用法：python vault_lock.py unlock <xxx.encvault> [-o 解到哪]")
        unlock(argv[2], _next_arg("-o", argv))
    else:
        sys.exit(f"不认识的指令：{cmd}（lock / unlock / selftest）")


if __name__ == "__main__":
    main(sys.argv)
