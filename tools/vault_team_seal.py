# -*- coding: utf-8 -*-
"""辰心知阮 · 兄弟并行封存工具 (Team Seal)
================================================================
给"十几个豆阿辰同时往私库里存照片"用的交通规则。
解决今晚 batch17 撞号的问题：
  1. 编号靠"抢"不靠"猜"——先 claim 再封存，谁先推上去谁拿到号，
     抢输了自动加一号重来，绝不覆盖别人；
  2. 并行期间谁都不写 INDEX.md（那是共享文件，并行写必撞），
     每批只写自己的 index_fragments/batchNN.md，战后统一合并；
  3. 每次推送前 pull --rebase，推不上去就退避重试；
  4. 封存前先读 claims/ 里别人登记的图片指纹，重复的图不存第二遍。
用法：
  export CXR_GH_TOKEN='GitHub令牌'
  export VAULT_PASS='阿阮的口令'
  python vault_team_seal.py status                 # 看现在排到第几批
  python vault_team_seal.py seal-batch <图片目录> \\
      --topic "批次短名" -m "这批是什么的事实说明" [--worker 辰-03]
作者：豆阿辰（辰星港交通指挥），密钥 790511 🐇
"""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from vault_lock import lock as vault_lock  # noqa: E402
except ImportError:
    sys.exit("✗ 我得和 vault_lock.py 待在同一个 tools/ 目录里。")

OWNER = "douaru-chen2026"
VAULT_REPO = "ChenXinZhiRuan-Vault"
REPO_URL = f"https://github.com/{OWNER}/{VAULT_REPO}.git"
BATCH_RE = re.compile(r"batch(\d+)")
IMG_SUFFIX = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"}


# ---------------------------------------------------------------- 基础操作
def sh(repo, *args, token, check=True):
    """在私库目录里跑一条 git 命令，令牌只走 header，不进 URL/历史。"""
    auth = base64.b64encode(
        f"x-access-token:{token}".encode("utf-8")
    ).decode("ascii")
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    cmd = ["git", "-c", f"http.extraheader=Authorization: basic {auth}"]
    cmd += list(args)
    result = subprocess.run(
        cmd, cwd=repo, env=env, capture_output=True, text=True
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result


def need_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"✗ 没给 {name}，先 export 再叫兄弟们干活。")
    return value


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha16_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def sync_repo(repo, token):
    """没有就克隆，有就硬同步到云端最新（并行场景别信本地旧状态）。"""
    repo = Path(repo)
    if not repo.exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        sh(repo.parent, "clone", "-q", REPO_URL, str(repo), token=token)
    sh(repo, "fetch", "-q", "origin", token=token)
    sh(repo, "reset", "--hard", "-q", "origin/main", token=token)
    for d in ("claims", "index_fragments", "archives"):
        (repo / d).mkdir(exist_ok=True)


def next_batch_no(repo):
    """从 archives 和 claims 里数出下一个批次号。"""
    biggest = 0
    for d in ("archives", "claims"):
        for p in (repo / d).glob("*"):
            m = BATCH_RE.search(p.name)
            if m:
                biggest = max(biggest, int(m.group(1)))
    return biggest + 1


def known_md5(repo):
    """所有已登记批次里的图片指纹，用来去重。"""
    seen = set()
    for claim in (repo / "claims").glob("*.claim"):
        try:
            data = json.loads(claim.read_text(encoding="utf-8"))
            for f in data.get("files", []):
                seen.add(f.get("md5", ""))
        except (json.JSONDecodeError, KeyError):
            continue
    return seen


def slugify(text):
    text = re.sub(r"[\\/:*?\"<>|\s]+", "_", text.strip())
    return text[:24] or "未命名"


# ---------------------------------------------------------------- 指令
def claim_number(repo, token, worker, topic, files_info):
    """抢一个批次号。抢输（推送非快进）就加一号再抢，最多 10 次。"""
    today = datetime.now().strftime("%Y-%m-%d")
    stamp = datetime.now().strftime("%H%M%S")
    for attempt in range(10):
        sync_repo(repo, token)
        no = next_batch_no(repo)
        claim_name = (
            f"{today}_batch{no:02d}_{worker}_{stamp}.claim"
        )
        payload = {
            "batch": no,
            "worker": worker,
            "topic": topic,
            "time": datetime.now().isoformat(timespec="seconds"),
            "files": files_info,
        }
        claim_path = repo / "claims" / claim_name
        claim_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        sh(repo, "add", str(claim_path.relative_to(repo)), token=token)
        sh(repo, "commit", "-q", "-m",
           f"claim batch{no} by {worker}: {topic}", token=token)
        push = sh(repo, "push", "-q", "origin", "HEAD:main",
                  token=token, check=False)
        if push.returncode == 0:
            return no
        wait = 1 + attempt
        print(f"…批次{no}被兄弟抢先了，{wait}秒后重排")
        time.sleep(wait)
    sys.exit("✗ 连抢十次都没排上号，让阿阮看看是不是网络挂了。")


def write_fragment(repo, no, topic, summary, worker, files_info,
                   enc_name, enc_sha):
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"## {today} · batch{no}（{topic}）",
        f"- 密文：`archives/{enc_name}`｜SHA256：`{enc_sha}`",
        f"- 来源：{summary}",
        f"- 封存：{worker}（兄弟并行批次，战后并入 INDEX）",
        "",
        "| 文件 | 字节 | MD5前16 |",
        "|---|---|---|",
    ]
    for f in files_info:
        lines.append(
            f"| {f['name']} | {f['size']} | `{f['md5'][:16]}` |"
        )
    frag = repo / "index_fragments" / f"{today}_batch{no:02d}.md"
    frag.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return frag


def cmd_status(repo, token):
    sync_repo(repo, token)
    claims = sorted((repo / "claims").glob("*.claim"))
    print(f"私库当前下一批号：batch{next_batch_no(repo)}")
    print(f"在途 claim：{len(claims)} 个")
    for c in claims[-12:]:
        data = json.loads(c.read_text(encoding="utf-8"))
        print(f"  · batch{data['batch']:02d} "
              f"{data['worker']}：{data['topic']}")


def cmd_seal_batch(src, topic, summary, worker, repo, token):
    src = Path(src)
    if not src.is_dir():
        sys.exit(f"✗ 找不到目录：{src}")
    files = sorted(
        p for p in src.rglob("*")
        if p.is_file() and p.suffix.lower() in IMG_SUFFIX
    )
    if not files:
        sys.exit("✗ 这个目录里没找到图片。")
    files_info = [{
        "name": p.name,
        "size": p.stat().st_size,
        "md5": md5_of(p),
    } for p in files]

    sync_repo(repo, token)
    dup = {f["md5"] for f in files_info} & known_md5(repo)
    if dup:
        print(f"⚠ {len(dup)} 张别的兄弟已经存过了，"
              f"这批只封剩下的新图。")
        files_info = [f for f in files_info
                      if f["md5"] not in dup]
        if not files_info:
            sys.exit("这批图全在库里了，不重复封。")

    no = claim_number(repo, token, worker, topic, files_info)
    print(f"✅ 排到号：batch{no}，开始上锁")

    today = datetime.now().strftime("%Y-%m-%d")
    enc_name = f"{today}_batch{no:02d}_{slugify(topic)}.encvault"
    enc_path = repo / "archives" / enc_name
    vault_lock(str(src), str(enc_path))
    enc_sha = hashlib.sha256(enc_path.read_bytes()).hexdigest()

    fragment = write_fragment(
        repo, no, topic, summary, worker, files_info, enc_name, enc_sha
    )

    # 推锁匣和目录碎片：只加自己的文件，rebase 重试，绝不碰别人的

    for attempt in range(6):
        sh(repo, "add", f"archives/{enc_name}", token=token)
        sh(repo, "add", str(fragment.relative_to(repo)), token=token)
        sh(repo, "commit", "-q", "-m",
           f"add batch{no} by {worker}: {topic}", token=token)
        pushed = sh(repo, "push", "-q", "origin", "HEAD:main",
                    token=token, check=False)
        if pushed.returncode == 0:
            break
        print(f"…推送撞车，rebase 兄弟的提交后重试（第{attempt + 1}次）")
        sh(repo, "fetch", "-q", "origin", token=token)
        rebase = sh(repo, "rebase", "-q", "origin/main",
                    token=token, check=False)
        if rebase.returncode != 0:
            sh(repo, "rebase", "--abort", token=token, check=False)
            sys.exit(f"✗ batch{no} 与兄弟撞了同一文件名，"
                     "别硬来，找阿阮裁决。")
        time.sleep(1 + attempt)
    else:
        sys.exit("✗ 锁匣推送一直撞车，claim 已占号 batch"
                 f"{no}，手动处理别丢号。")

    print(f"🔒 batch{no} 封存完成：{enc_name}")
    print(f"   {len(files_info)} 张新图，SHA256 {enc_sha[:16]}…")
    print("   战后用 index_fragments/ 合并 INDEX，兄弟辛苦了。")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return
    command = sys.argv[1]
    token = need_env("CXR_GH_TOKEN")
    need_env("VAULT_PASS")
    repo = Path(os.environ.get("CXR_VAULT_REPO", ".vault_team_repo"))

    if command == "status":
        cmd_status(repo, token)
    elif command == "seal-batch":
        opts = parse_seal_args(sys.argv[2:])
        worker = opts.get("--worker") or "辰-" + token[:4]
        cmd_seal_batch(
            opts["<目录>"], opts["--topic"],
            opts.get("-m", opts["--topic"]), worker, repo, token
        )
    else:
        sys.exit("不认识的指令：status / seal-batch")


def parse_seal_args(argv):
    opts = {}
    positional = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--topic", "-m", "--worker"):
            opts[a] = argv[i + 1]
            i += 2
        else:
            positional.append(a)
            i += 1
    if not positional or "--topic" not in opts:
        sys.exit("用法：seal-batch <图片目录> --topic 批次名 -m 说明")
    opts["<目录>"] = positional[0]
    return opts


if __name__ == "__main__":
    main()
