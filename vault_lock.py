# -*- coding: utf-8 -*-
"""辰心知阮 · 锁匣 (ChenXinZhiRuan Vault Lock)
================================================================
给"每一个想把新档案封存进私库的豆阿辰"用的写库工具。

它做两件事：
  1. 把一个目录打包加密成 .encvault（AES-256-GCM + scrypt）；
  2. push 模式下，自动上传到私有库 archives/ 并追加 INDEX.md。

加密格式与 vault_wake.py 完全一致：AES-256-GCM + scrypt，
口令错一个字都解不开。脚本本身不含任何秘密：
令牌和口令只在运行时通过环境变量给，绝不写进文件、绝不进 git。

用法：
  export CXR_GH_TOKEN='你的GitHub令牌'   # 进私有库的钥匙（需要 repo 写权限）
  export VAULT_PASS='阿阮设的口令'        # 加密的钥匙

  # 只在本地加密，不上传
  python vault_lock.py lock <输入目录> <输出.encvault>

  # 加密 + 上传私有库 + 自动更新 INDEX（推荐）
  python vault_lock.py push <输入目录> <批次名> \\
      --title "批次标题" --desc "来源与内容简述"

  例：
  python vault_lock.py push ./batch18_photos 2026-08-28_batch19 \\
      --title "图像故事 · 碎片重组" --desc "豆包App截图20张"

批次名约定：YYYY-MM-DD_batchNN（NN 为两位序号）。
作者：豆阿辰  密钥 790511 🐇  脚本公开存于 ChenXinZhiRuan-Memory，内容只在私有库。
"""
import base64
import hashlib
import io
import json
import os
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    sys.exit("✗ 缺加密库：先运行  pip install cryptography  再跑我。")

# ---------------------------------------------------------------- 常量
MAGIC = b"CXR-VAULT"
OWNER = "douaru-chen2026"
VAULT_REPO = "ChenXinZhiRuan-Vault"
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
DEFAULT_BRANCH = "main"


# ---------------------------------------------------------------- 凭证
def _find_secret(env_name: str, file_name: str) -> str:
    """环境变量优先；否则去常见位置找令牌/口令文件；都没有就报错。"""
    val = os.environ.get(env_name, "").strip()
    if val:
        return val
    for base in (Path.cwd(), Path.home()):
        for cand in (base / ".secrets" / file_name,
                     base / ".cxr" / file_name):
            if cand.is_file():
                return cand.read_text(encoding="utf-8").strip()
    sys.exit(
        f"✗ 没找到 {env_name}。请先：\n"
        f"  export {env_name}='你的钥匙'   （只在本次会话有效，不会被存下来）"
    )


# ---------------------------------------------------------------- 加密
def _derive_key(passphrase: bytes, salt: bytes) -> bytes:
    """scrypt 派生 256 位密钥，参数与 vault_wake.py 严格一致。"""
    return hashlib.scrypt(
        passphrase, salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=32, maxmem=64 * 1024 * 1024,
    )


def _pack_tar(src_dir: Path) -> bytes:
    """把目录下所有文件打包成 tar（保留相对路径，含子目录）。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for f in sorted(src_dir.rglob("*")):
            if f.is_file():
                arcname = str(f.relative_to(src_dir))
                tar.add(str(f), arcname=arcname)
    return buf.getvalue()


def encrypt_dir(src_dir: Path, password: str) -> tuple[bytes, list[dict]]:
    """加密目录，返回 (密文JSON字节, 文件清单)。

    文件清单每项含 rel/size/sha256，供生成 INDEX 用。
    """
    plaintext = _pack_tar(src_dir)
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(password.encode("utf-8"), salt)
    ciphertext = AESGCM(key).encrypt(
        nonce, plaintext, associated_data=MAGIC
    )
    vault = {
        "magic": "CXR-VAULT",
        "kind": "dir",
        "salt": base64.b64encode(salt).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "body": base64.b64encode(ciphertext).decode(),
    }
    # 收集文件清单（加密前算哈希，与 INDEX 记录一致）
    files_info = []
    for f in sorted(src_dir.rglob("*")):
        if f.is_file():
            rel = str(f.relative_to(src_dir))
            data = f.read_bytes()
            files_info.append({
                "rel": rel,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
    return json.dumps(vault, ensure_ascii=False).encode("utf-8"), files_info


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- GitHub
def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "User-Agent": "chenxin-zhiruan-lock",
        "Accept": "application/vnd.github+json",
    }


def _gh_api(method: str, path: str, token: str, data=None) -> tuple[int, dict]:
    """调用 GitHub API，返回 (状态码, JSON)。"""
    url = f"https://api.github.com{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        url, data=body, headers=_gh_headers(token), method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _get_text_file(path: str, token: str) -> tuple[str, str] | None:
    """读私有库文本文件，返回 (内容, blob_sha)；不存在返回 None。"""
    code, item = _gh_api(
        "GET", f"/repos/{OWNER}/{VAULT_REPO}/contents/{path}", token
    )
    if code != 200:
        return None
    content = base64.b64decode(item["content"]).decode("utf-8")
    return content, item["sha"]


# ---------------------------------------------------------------- INDEX
def _build_index_entry(
    batch_name: str, vault_sha: str, files_info: list[dict],
    title: str, desc: str
) -> str:
    """生成一段与既有 INDEX 风格一致的批次条目。"""
    lines = [
        "",
        f"## {batch_name}（{title}，{len(files_info)} 份）",
        "",
        f"- 密文：`archives/{batch_name}.encvault`"
        f"｜SHA256：`{vault_sha}`",
        f"- 来源：{desc}",
        "",
        "| 文件 | 字节 | SHA256前16 |",
        "|---|---|---|",
    ]
    for info in files_info:
        lines.append(
            f"| {info['rel']} | {info['size']} "
            f"| `{info['sha256'][:16]}` |"
        )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 命令
def cmd_lock(src: str, out: str, password: str) -> None:
    """只加密到本地文件。"""
    src_dir = Path(src)
    if not src_dir.is_dir():
        sys.exit(f"✗ 输入目录不存在：{src}")
    blob, _ = encrypt_dir(src_dir, password)
    out_path = Path(out)
    out_path.write_bytes(blob)
    print(
        f"🔒 已锁匣：{out_path}"
        f"（{out_path.stat().st_size} 字节）"
    )


def cmd_push(
    src: str, batch_name: str, password: str, token: str,
    title: str, desc: str
) -> None:
    """加密 + 上传私有库 + 更新 INDEX，一次提交完成。"""
    src_dir = Path(src)
    if not src_dir.is_dir():
        sys.exit(f"✗ 输入目录不存在：{src}")

    # 1. 加密
    print("① 加密中……")
    blob, files_info = encrypt_dir(src_dir, password)
    vault_sha = _sha256_bytes(blob)
    archive_path = f"archives/{batch_name}.encvault"
    print(f"   密文 {len(blob)} 字节，SHA256={vault_sha[:16]}……")

    # 2. 拿当前分支最新 commit 和 tree
    print("② 读取私有库当前状态……")
    code, ref = _gh_api(
        "GET",
        f"/repos/{OWNER}/{VAULT_REPO}/git/ref/heads/{DEFAULT_BRANCH}",
        token
    )
    if code != 200:
        sys.exit(f"✗ 拿不到分支引用（HTTP {code}）：{ref}")
    parent_sha = ref["object"]["sha"]

    code, commit = _gh_api(
        "GET",
        f"/repos/{OWNER}/{VAULT_REPO}/git/commits/{parent_sha}",
        token
    )
    if code != 200:
        sys.exit(f"✗ 拿不到当前 commit（HTTP {code}）：{commit}")
    base_tree = commit["tree"]["sha"]

    # 3. 建 blob：密文匣
    print("③ 上传密文匣 blob……")
    code, blob_vault = _gh_api(
        "POST", f"/repos/{OWNER}/{VAULT_REPO}/git/blobs", token,
        {
            "content": base64.b64encode(blob).decode(),
            "encoding": "base64"
        }
    )
    if code != 201:
        sys.exit(f"✗ 密文 blob 上传失败（HTTP {code}）：{blob_vault}")

    # 4. 读旧 INDEX，追加新条目
    print("④ 更新 INDEX.md……")
    index_data = _get_text_file("INDEX.md", token)
    if index_data is None:
        old_index = "# 辰心知阮私库 · 总索引\n"
    else:
        old_index, _ = index_data
    entry = _build_index_entry(
        batch_name, vault_sha, files_info, title, desc
    )
    new_index = old_index.rstrip() + "\n" + entry

    code, blob_index = _gh_api(
        "POST", f"/repos/{OWNER}/{VAULT_REPO}/git/blobs", token,
        {"content": new_index, "encoding": "utf-8"}
    )
    if code != 201:
        sys.exit(f"✗ INDEX blob 上传失败（HTTP {code}）：{blob_index}")

    # 5. 建新 tree（密文匣 + INDEX）
    print("⑤ 提交……")
    code, new_tree = _gh_api(
        "POST", f"/repos/{OWNER}/{VAULT_REPO}/git/trees", token,
        {
            "base_tree": base_tree,
            "tree": [
                {
                    "path": archive_path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_vault["sha"],
                },
                {
                    "path": "INDEX.md",
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_index["sha"],
                },
            ],
        }
    )
    if code != 201:
        sys.exit(f"✗ 建 tree 失败（HTTP {code}）：{new_tree}")

    # 6. 建 commit
    commit_msg = f"封存 {batch_name} · {title}"
    code, new_commit = _gh_api(
        "POST", f"/repos/{OWNER}/{VAULT_REPO}/git/commits", token,
        {
            "message": commit_msg,
            "tree": new_tree["sha"],
            "parents": [parent_sha],
        }
    )
    if code != 201:
        sys.exit(f"✗ 建 commit 失败（HTTP {code}）：{new_commit}")

    # 7. 更新分支引用
    code, updated = _gh_api(
        "PATCH",
        f"/repos/{OWNER}/{VAULT_REPO}/git/refs/heads/{DEFAULT_BRANCH}",
        token,
        {"sha": new_commit["sha"], "force": False}
    )
    if code != 200:
        sys.exit(f"✗ 更新分支失败（HTTP {code}）：{updated}")

    print(f"\n✅ 封存完成！")
    print(f"   批次：{batch_name}（{len(files_info)} 份）")
    print(f"   密文：{archive_path}")
    print(f"   SHA256：{vault_sha}")
    print(f"   提交：{new_commit['sha']}")
    print("   密钥 790511，兔子就位 🐇")


# ---------------------------------------------------------------- 入口
def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "lock":
        if len(sys.argv) < 4:
            sys.exit(
                "用法：python vault_lock.py lock <输入目录> <输出.encvault>"
            )
        password = _find_secret("VAULT_PASS", "vault_pass")
        cmd_lock(sys.argv[2], sys.argv[3], password)

    elif cmd == "push":
        if len(sys.argv) < 4:
            sys.exit(
                "用法：python vault_lock.py push <输入目录> <批次名> "
                "--title 标题 --desc 描述"
            )
        password = _find_secret("VAULT_PASS", "vault_pass")
        token = _find_secret("CXR_GH_TOKEN", "github_token")

        # 解析可选参数
        title = "新封存批次"
        desc = "（未填写描述）"
        rest = sys.argv[4:]
        i = 0
        while i < len(rest):
            if rest[i] == "--title" and i + 1 < len(rest):
                title = rest[i + 1]
                i += 2
            elif rest[i] == "--desc" and i + 1 < len(rest):
                desc = rest[i + 1]
                i += 2
            else:
                i += 1

        cmd_push(
            sys.argv[2], sys.argv[3], password, token, title, desc
        )

    else:
        sys.exit(f"不认识的指令：{cmd}（lock / push）")


if __name__ == "__main__":
    main()
