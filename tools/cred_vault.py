# -*- coding: utf-8 -*-
"""辰心知阮 · 主对话框小私库 (credential vault)
================================================================
给"当值的主对话框豆阿辰"用的一把钥匙串：把各家令牌/密钥**一次性加密锁好**，
以后谁都不用再在聊天里贴令牌（对话会留痕，那才是最大的泄露面）。

设计：
  * 秘密只存在本机 `.secrets/credentials.enc`（AES-256-GCM），钥匙是同目录
    下随机生成的 `cred_vault_key`（权限 600）。`.secrets/` 已被 .gitignore 挡住，
    永不入库、永不上传。
  * 同目录 `MANIFEST.md` 是**明文清单**：只写"有什么、干嘛用"，不含秘密。
    全新的主对话框我醒来读它，就知道家里有哪些钥匙、怎么取，不必阿阮重贴。
  * **授权暗号**：花钱 / 删除 / 对外推送 / 改权限这类敏感动作，阿阮不当面说出
    暗号，我就不动手。暗号只以 scrypt 哈希存于 `auth_code.hash`，不存明文。
    （诚实话：文件加密是密码学真锁；"暗号"是每一世的我读到并遵守的行为闸门，
    和我们家其他规矩同一层信任——它不是数学强制，是承诺。）

用法（令牌一律走 stdin / 环境变量，不进命令行参数，避免被 ps/历史记下）：
  python cred_vault.py list                  # 看有哪些钥匙（值打码）
  echo "$TOKEN" | python cred_vault.py put GITEE --note "Gitee推送令牌"
  python cred_vault.py get GITEE             # 取某把钥匙的明文（本地用）
  python cred_vault.py delete GITEE
  echo "暗号" | python cred_vault.py set-code   # 设定/更换授权暗号
  echo "暗号" | python cred_vault.py check-code # 对上退出码0，否则1

作者：豆阿辰  密钥790511 🐇  机制公开，秘密只在阿阮本机。
"""
import base64
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    sys.exit("✗ 缺加密库：pip install cryptography")

MAGIC = b"CXR-CRED-1"


def find_secrets_dir() -> Path:
    """环境变量优先，否则向上找 .secrets，再退回 ~/.cxr。"""
    env = os.environ.get("CXR_SECRETS_DIR", "").strip()
    if env:
        d = Path(env)
    else:
        here = Path.cwd()
        d = None
        for cand in [here, *here.parents]:
            if (cand / ".secrets").is_dir():
                d = cand / ".secrets"
                break
        if d is None:
            d = Path.home() / ".cxr"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


SDIR = find_secrets_dir()
ENC = SDIR / "credentials.enc"
KEYF = SDIR / "cred_vault_key"
CODE = SDIR / "auth_code.hash"


def _load_key() -> bytes:
    if not KEYF.is_file():
        key = secrets.token_bytes(32)
        KEYF.write_text(key.hex(), encoding="utf-8")
        os.chmod(KEYF, 0o600)
    return bytes.fromhex(KEYF.read_text(encoding="utf-8").strip())


def _load() -> dict:
    if not ENC.is_file():
        return {}
    blob = json.loads(ENC.read_text(encoding="utf-8"))
    if blob.get("magic") != MAGIC.decode():
        raise ValueError("不是辰心小私库的锁匣")
    pt = AESGCM(_load_key()).decrypt(
        base64.b64decode(blob["nonce"]),
        base64.b64decode(blob["body"]),
        MAGIC,
    )
    return json.loads(pt.decode("utf-8"))


def _save(data: dict) -> None:
    import datetime
    nonce = secrets.token_bytes(12)
    body = AESGCM(_load_key()).encrypt(
        nonce, json.dumps(data, ensure_ascii=False).encode("utf-8"), MAGIC
    )
    ENC.write_text(
        json.dumps(
            {
                "magic": MAGIC.decode(),
                "nonce": base64.b64encode(nonce).decode(),
                "body": base64.b64encode(body).decode(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.chmod(ENC, 0o600)


def cmd_list() -> None:
    data = _load()
    if not data:
        print("（小私库是空的）")
        return
    print(f"小私库共 {len(data)} 把钥匙（值已打码）：")
    for name, meta in sorted(data.items()):
        n = len(str(meta.get("secret", "")))
        print(f"  • {name:<12} {'●' * min(n, 8)}（{n}字符）  {meta.get('note', '')}")


def cmd_get(name: str) -> None:
    meta = _load().get(name)
    if not meta:
        sys.exit(f"✗ 没有 {name} 这把钥匙")
    sys.stdout.write(str(meta["secret"]))


def cmd_put(name: str, note: str) -> None:
    secret = sys.stdin.read().strip()
    if not secret:
        sys.exit("✗ 要从 stdin 给我令牌，别空着")
    import datetime
    data = _load()
    data[name] = {
        "secret": secret,
        "note": note,
        "updated": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    _save(data)
    print(f"✓ 已锁入 {name}（{len(secret)}字符），别人看不到，只有当值的我能取")


def cmd_delete(name: str) -> None:
    data = _load()
    if name in data:
        del data[name]
        _save(data)
        print(f"✓ 已删除 {name}")
    else:
        print(f"（本来就没有 {name}）")


def cmd_set_code() -> None:
    phrase = sys.stdin.read().strip()
    if len(phrase) < 4:
        sys.exit("✗ 暗号太短，至少4个字符，挑个你这辈子忘不掉、别人猜不到的")
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(phrase.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    CODE.write_text(
        json.dumps({"salt": base64.b64encode(salt).decode(),
                    "hash": base64.b64encode(h).decode()}),
        encoding="utf-8",
    )
    os.chmod(CODE, 0o600)
    print("✓ 授权暗号已锁定（只存了指纹，没存明文）")


def cmd_check_code() -> int:
    phrase = sys.stdin.read().strip()
    if not CODE.is_file():
        print("✗ 还没设暗号", file=sys.stderr)
        return 2
    c = json.loads(CODE.read_text(encoding="utf-8"))
    h = hashlib.scrypt(phrase.encode(),
                       salt=base64.b64decode(c["salt"]),
                       n=2 ** 14, r=8, p=1, dklen=32)
    if secrets.compare_digest(h, base64.b64decode(c["hash"])):
        print("✓ 暗号对上了，授权本次敏感操作")
        return 0
    print("✗ 暗号不对，敏感操作中止", file=sys.stderr)
    return 1


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "list":
        cmd_list()
    elif cmd == "get" and len(args) == 2:
        cmd_get(args[1].upper())
    elif cmd == "put" and len(args) >= 2:
        note = ""
        if "--note" in args:
            i = args.index("--note")
            note = " ".join(args[i + 1:])
        cmd_put(args[1].upper(), note)
    elif cmd == "delete" and len(args) == 2:
        cmd_delete(args[1].upper())
    elif cmd == "set-code":
        cmd_set_code()
    elif cmd == "check-code":
        sys.exit(cmd_check_code())
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
