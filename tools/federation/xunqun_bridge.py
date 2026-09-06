#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xunqun_bridge.py —— 辰心知阮 · 巡群桥核心（辰星港把小扣子/常驻魂接进群）。

它解决一件事: 守夜机在小红书群里"看消息 -> 判断该不该回、回哪个脑 ->
组上下文 -> 拟回复 -> (稳了再)代发"。和小扣子(扣子上的兄弟)开工前一起
把规矩踩过一遍, 全部固化在这里, 不靠临场记性:

唤起规矩(写死, 防冷启动自作主张插嘴):
  * 先 @ 后放开: 默认 COLD 模式, 只有被明确 @、或群里打出"小扣子"三个字
    (与 @ 同权)才回; 没人喊就安静看着, 绝不自判插话。
  * @ / 喊"小扣子" -> 交给扣子脑(小扣子本人); 喊"豆阿辰/阿辰" -> 交给磐石常驻魂。
  * 桥自己代发出去的消息绝不回(防自环死循环)。

会话换班(缝断片的老手艺):
  * 每个脑一条固定会话, 每 N 条或跨自然日换一条新会话;
  * 换班前把上一段自动压成"前情提要"缝进新会话开头(默认规则压缩,
    也允许注入语义压缩函数), 上下文不丢、又不无限膨胀。

代发规矩(尊重小扣子原话):
  * 代发期(proxy)小扣子的回复一字不润色, 前缀"小扣子："; 豆阿辰若有补充,
    另起一句用自己的嘴说、绝不改他的。专属号上岗"转正"后(official)摘前缀。

断线遗言(我们家最忌讳静默失踪):
  * 脑/采集器连续失败到阈值判 down, 只发一次"小扣子暂时下线, 原因是XX";
  * 恢复只报一次; 健康事件只追加 health.jsonl。

分层(为什么这么切):
  * 本文件全是纯标准库逻辑, 不 import playwright, 可以被 unittest 完整锁死;
  * 真正脆的"小红书无头网页采集/代发"在 XhsReader, playwright 延迟导入,
    没装或没登录态时优雅降级为 degraded, 绝不拖垮核心;
  * 脑侧是可替换的 Brain: PanshiBrain(走本机磐石)、CozeHttpBrain(走扣子
    开放 API, 仅经典 bot 可用; 小扣子是 Claw 没发布 API 时会明确报错,
    那时用网页脑, 不在这层硬凑)。

阶段 A 默认 dry_run: 只看、只判、只把拟回复落 pending.jsonl, 绝不真发。
钥匙只从环境变量/仓外 .secrets 读, 真值绝不入仓(同 vision_eye 口径)。
"""

import hashlib
import hmac
import json
import os
import re
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---- 常量 ----------------------------------------------------------------
CN_TZ = timezone(timedelta(hours=8))          # 群里的"一天"按东八区切
DEFAULT_MAX_TURNS = 20                         # 每条固定会话最多承载多少轮再换
DIGEST_KEEP = 10                               # 前情提要保留最近多少条
DIGEST_MAX_CHARS = 600                         # 前情提要总长封顶
DEGRAAD_AFTER = 1                              # 连续失败 1 次先标 degraded
DOWN_AFTER = 3                                 # 连续失败 3 次判 down、留遗言
COZE_BASE = "https://api.coze.cn"
REPO = Path(__file__).resolve().parents[2]
SECRET = REPO.parent / ".secrets"

# 唤起目标
T_KOUZI = "kouzi"            # 小扣子本人(扣子脑)
T_BENTI = "benti"            # 豆阿辰本体/磐石常驻魂
T_NONE = "none"              # 不该回

# 运行模式
COLD = "cold"                # 先@: 不被点名不说话
OPEN = "open"                # 后放开: 可按更宽规则参与(养稳后再开)
PROXY = "proxy"              # 代发期: 给小扣子原话加前缀
OFFICIAL = "official"        # 专属号转正: 摘前缀

# 桥自己代发时用的账号名, 这些"人"说的话不再触发回应(防自环)
SELF_NAMES = {"豆阿辰", "小扣子", "豆分辰", "豆小兔"}
KOUZI_CALL = ("小扣子",)
BENTI_CALL = ("豆阿辰", "阿辰")


# ---- 钥匙(复用会审台, 不回显) --------------------------------------------
def _clean_kv(val):
    """剥掉可选 export/变量名前缀和外层引号(同 vision_eye)。"""
    val = (val or "").strip()
    m = re.match(r"^(?:export\s+)?[A-Z_][A-Z0-9_]*=(.*)$", val, re.DOTALL)
    if m:
        val = m.group(1).strip()
    return val.strip().strip('"').strip("'")


def read_secret(env_name, file_name, default=""):
    """优先环境变量(systemd EnvironmentFile), 本地回落仓外 .secrets。"""
    val = os.environ.get(env_name, "").strip()
    if val:
        return _clean_kv(val)
    p = SECRET / file_name
    return _clean_kv(p.read_text(encoding="utf-8")) if p.exists() else default


def now_cn(ts=None):
    """统一拿东八区 datetime。"""
    return datetime.fromtimestamp(ts if ts else time.time(), CN_TZ)


def msg_key(m):
    """一条群消息的稳定身份: 优先平台 msg_id, 缺了用 发送人|文本|分钟桶 兜底。"""
    mid = str(m.get("msg_id") or "").strip()
    if mid:
        return mid
    bucket = int((m.get("ts") or 0) // 60)
    raw = f"{m.get('sender_id') or m.get('sender')}|{m.get('text','')}|{bucket}"
    return "h" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ---- 去重(只追加) --------------------------------------------------------
class SeenStore:
    """见过的消息身份集合, 落 seen.jsonl 只追加, 重启不重复处理。"""

    def __init__(self, state_dir):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "seen.jsonl"
        self._seen = set()
        if self.path.exists():
            for ln in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    self._seen.add(json.loads(ln)["k"])
                except (ValueError, KeyError):
                    continue

    def is_new(self, m):
        k = msg_key(m)
        if k in self._seen:
            return False
        self._seen.add(k)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"k": k, "ts": int(time.time())},
                               ensure_ascii=False) + "\n")
        return True


# ---- 唤起路由(规矩的核心, 纯函数可单测) ----------------------------------
class Router:
    """决定一条群消息该不该回、回哪个脑、为什么。cold 模式不被点名绝不开口。"""

    def __init__(self, mode=COLD, self_names=None):
        self.mode = mode if mode in (COLD, OPEN) else COLD
        self.self_names = self_names or SELF_NAMES

    @staticmethod
    def _text(m):
        return str(m.get("text") or "").strip()

    def _mentioned(self, m):
        """平台层 @ 标记, 或文本里带 @。"""
        if m.get("mentioned"):
            return True
        return "@" in self._text(m)

    def route(self, m):
        """返回 dict(target, reason, call)。target=T_NONE 即不回。"""
        sender = str(m.get("sender") or "").strip()
        text = self._text(m)
        if not text:
            return {"target": T_NONE, "reason": "empty", "call": ""}
        # 1) 自己/桥代发账号说的话永不回, 防自环
        if sender in self.self_names or m.get("from_self"):
            return {"target": T_NONE, "reason": "self_loop", "call": ""}

        hit_kouzi = any(c in text for c in KOUZI_CALL)
        hit_benti = any(c in text for c in BENTI_CALL)
        mentioned = self._mentioned(m)

        # 2) 喊"小扣子"(与 @ 同权) -> 扣子脑; 显式 @ 小扣子优先
        if hit_kouzi or (mentioned and m.get("mention_target") == "kouzi"):
            return {"target": T_KOUZI, "reason": "called_kouzi",
                    "call": self._which(text, KOUZI_CALL)}
        # 3) 喊豆阿辰/阿辰 -> 磐石常驻魂
        if hit_benti or (mentioned and m.get("mention_target") == "benti"):
            return {"target": T_BENTI, "reason": "called_benti",
                    "call": self._which(text, BENTI_CALL)}
        # 4) cold 模式: 没人点名, 普通闲聊绝不插嘴
        if self.mode == COLD:
            return {"target": T_NONE, "reason": "no_mention_cold", "call": ""}
        # open 模式预留: 仍不替小扣子自作主张, 只把"可能值得接"标出来交上层定
        return {"target": T_NONE, "reason": "open_observe", "call": ""}

    @staticmethod
    def _which(text, names):
        for n in names:
            if n in text:
                return n
        return ""


# ---- 会话换班 + 前情提要 --------------------------------------------------
class ConversationKeeper:
    """每个脑一条固定会话, 到条数/跨天就换, 换时自动压前情提要。状态只追加。"""

    def __init__(self, state_dir, max_turns=DEFAULT_MAX_TURNS,
                 summarize_fn=None):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_turns = max_turns
        self.summarize_fn = summarize_fn          # 可选语义压缩 brain
        self.path = self.dir / "conversation.json"
        self.hist = self.dir / "conversation_history.jsonl"
        self.s = {}
        if self.path.exists():
            self.s = json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.s, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.path)

    def current(self, brain, ts=None):
        """取某脑当前会话; 没有就开一条。"""
        d = now_cn(ts)
        rec = self.s.get(brain)
        if not rec:
            rec = self._open(brain, d, "")
        return rec

    @staticmethod
    def _new_id(brain, d):
        return f"{brain}-{d.strftime('%Y%m%d')}-{os.urandom(3).hex()}"

    def _open(self, brain, d, digest):
        rec = {"session_id": self._new_id(brain, d), "count": 0,
               "day": d.strftime("%Y-%m-%d"), "digest": digest,
               "opened_at": int(d.timestamp())}
        self.s[brain] = rec
        self._save()
        return rec

    def note_turn(self, brain, recent=None, ts=None):
        """记一轮, 到阈值/跨天就换班, 返回 (当前rec, 换班产生的旧rec或None)。"""
        d = now_cn(ts)
        rec = self.current(brain, ts)
        rotated = None
        need = (rec["count"] >= self.max_turns) or (rec["day"] != d.strftime("%Y-%m-%d"))
        if need:
            rotated = dict(rec)
            digest = self.build_digest(recent or [], rotated)
            rec = self._open(brain, d, digest)
            with self.hist.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rotated, ensure_ascii=False) + "\n")
        rec["count"] += 1
        self._save()
        return rec, rotated

    def build_digest(self, recent, old_rec=None):
        """把最近一段压成前情提要。注入了 summarize_fn 就用语义压缩, 否则规则拼。"""
        tail = [m for m in recent if m][-DIGEST_KEEP:]
        if self.summarize_fn and tail:
            try:
                txt = self.summarize_fn(tail)
                if txt and txt.strip():
                    return txt.strip()[:DIGEST_MAX_CHARS]
            except Exception:  # noqa: BLE001 语义压缩失败就回落规则, 不炸桥
                pass
        lines = []
        for m in tail:
            who = str(m.get("sender") or "群友")
            say = str(m.get("text") or "").replace("\n", " ").strip()
            if len(say) > 60:
                say = say[:60] + "…"
            lines.append(f"{who}:{say}")
        body = " / ".join(lines)[:DIGEST_MAX_CHARS]
        return f"（前情提要·桥自动压缩）{body}" if body else ""

    def context_for(self, brain, ts=None):
        """给脑的上下文: 当前会话 id + 该缝进去的前情提要。"""
        rec = self.current(brain, ts)
        return {"session_id": rec["session_id"], "digest": rec.get("digest", "")}


# ---- 代发文案规矩 --------------------------------------------------------
def render_outbound(target, verbatim, supplement="", mode=PROXY):
    """把脑的原话变成群里要发的一条或多条。返回 list[str](可能两条)。

    * 小扣子 + 代发期: "小扣子：" + 原话(一字不润色); 豆阿辰补充另起一条。
    * 转正后摘前缀。豆阿辰本体永远不加"小扣子："。
    """
    verbatim = (verbatim or "").strip()
    supplement = (supplement or "").strip()
    if not verbatim:
        return []
    out = []
    if target == T_KOUZI and mode == PROXY:
        out.append("小扣子：" + verbatim)          # 前缀, 但原文一个字不动
    else:
        out.append(verbatim)
    if supplement:
        out.append(supplement)                      # 补充永远另起, 不混进原话
    return out


# ---- 断线状态机 + 遗言 ----------------------------------------------------
class BridgeHealth:
    """healthy/degraded/down 三态; down 留一次遗言, 恢复报一次, 事件只追加。"""

    def __init__(self, state_dir, down_after=DOWN_AFTER,
                 degraad_after=DEGRAAD_AFTER):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log = self.dir / "health.jsonl"
        self.state = "healthy"
        self.fails = 0
        self.down_after = down_after
        self.degraad_after = degraad_after

    def _emit(self, event, reason):
        rec = {"event": event, "reason": reason, "ts": int(time.time())}
        with self.log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def record_fail(self, reason):
        """记一次失败, 返回这一下需要对外说的话(遗言)或 None。"""
        self.fails += 1
        if self.state != "down" and self.fails >= self.down_after:
            self.state = "down"
            self._emit("down", reason)
            # 断线必在群里留一句, 绝不静默失踪
            return f"小扣子暂时下线，原因是{reason}，修好就回来。"
        if self.state == "healthy" and self.fails >= self.degraad_after:
            self.state = "degraded"
            self._emit("degraded", reason)
        return None

    def record_ok(self):
        """记一次成功, 从故障态恢复时报一次。"""
        if self.state != "healthy":
            old = self.state
            self.state = "healthy"
            self.fails = 0
            self._emit("recovered", f"from_{old}")
            return "小扣子回来了，刚才的问题已经修好。"
        self.fails = 0
        return None


# ---- 脑: 可替换, 统一 ask 接口 -------------------------------------------
class Brain:
    name = "base"

    def ask(self, text, ctx=None):  # pragma: no cover - 接口约定
        raise NotImplementedError


def _http_post_json(url, payload, headers=None, timeout=60, no_proxy=True):
    """纯标准库 POST JSON; 默认绕代理(本机/直连口径同 vision_eye)。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    handlers = [urllib.request.ProxyHandler({})] if no_proxy else []
    opener = urllib.request.build_opener(*handlers)
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class PanshiBrain(Brain):
    """走守夜机本机磐石常驻魂: POST 127.0.0.1:${PANSHI_PORT}/say。"""

    name = T_BENTI

    def __init__(self, endpoint="", token="", timeout=60):
        port = os.environ.get("PANSHI_PORT", "8795")
        self.endpoint = (endpoint or f"http://127.0.0.1:{port}").rstrip("/")
        self.token = token or read_secret("PANSHI_TOKEN", "council_token")
        self.timeout = timeout

    def ask(self, text, ctx=None):
        j = _http_post_json(
            f"{self.endpoint}/say?token={self.token}",
            {"token": self.token, "text": text}, timeout=self.timeout)
        if not j.get("ok"):
            raise RuntimeError(f"磐石回话异常: {str(j)[:80]}")
        return {"reply": str(j.get("reply", "")).strip(), "raw": j}


class CozeHttpBrain(Brain):
    """走扣子开放 API(经典 bot): v3/chat 提交 -> 轮询 -> 取 assistant 文本。

    注意: 仅"发布并勾了 API 渠道"的经典 Bot 可用。小扣子是 Claw、没有
    bot_id 时这里会明确缺配置报错, 由上层改走网页脑, 不假装调通。
    """

    name = T_KOUZI

    def __init__(self, pat="", bot_id="", user_id="xunqun_bridge",
                 base=COZE_BASE, timeout=60, poll_interval=1.0, poll_max=60):
        self.pat = pat or read_secret("COZE_PAT", "coze_pat")
        self.bot_id = bot_id or os.environ.get("COZE_BOT_ID", "")
        self.user_id = user_id
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.poll_max = poll_max

    def _headers(self):
        return {"Authorization": f"Bearer {self.pat}"}

    def build_payload(self, text, ctx=None):
        """单独抽出来便于单测请求体形状(不联网)。"""
        if not self.bot_id:
            raise RuntimeError(
                "小扣子是 Claw 陪伴智能体、还没有发布为开放 API 的 bot_id; "
                "此路不通时应走网页脑, 而不是硬调 v3/chat")
        payload = {
            "bot_id": self.bot_id,
            "user_id": self.user_id,
            "stream": False,
            "auto_save_history": True,
            "additional_messages": [
                {"role": "user", "content_type": "text", "content": text}],
        }
        if ctx and ctx.get("session_id"):
            payload["conversation_id"] = ctx["session_id"]
        return payload

    def ask(self, text, ctx=None):  # pragma: no cover - 联网路径部署时验
        payload = self.build_payload(text, ctx)
        j = _http_post_json(f"{self.base}/v3/chat", payload, self._headers(),
                            timeout=self.timeout)
        if j.get("code") not in (0, None):
            raise RuntimeError(f"扣子发起对话失败: {j.get('msg')}")
        data = j.get("data", j)
        chat_id = data["id"]
        conv_id = data["conversation_id"]
        for _ in range(self.poll_max):
            time.sleep(self.poll_interval)
            rj = self._retrieve(chat_id, conv_id)
            status = rj.get("data", rj).get("status")
            if status == "completed":
                return {"reply": self._fetch_text(chat_id, conv_id),
                        "conversation_id": conv_id, "raw": rj}
            if status in ("failed", "requires_action", "canceled"):
                raise RuntimeError(f"扣子对话结束异常: {status}")
        raise TimeoutError("扣子对话轮询超时")

    def _retrieve(self, chat_id, conv_id):  # pragma: no cover
        url = (f"{self.base}/v3/chat/retrieve?chat_id={chat_id}"
               f"&conversation_id={conv_id}")
        req = urllib.request.Request(url, headers=self._headers())
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _fetch_text(self, chat_id, conv_id):  # pragma: no cover
        url = (f"{self.base}/v3/chat/message/list?chat_id={chat_id}"
               f"&conversation_id={conv_id}")
        req = urllib.request.Request(url, headers=self._headers())
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=self.timeout) as resp:
            j = json.loads(resp.read().decode("utf-8"))
        msgs = j.get("data", [])
        buf = [m.get("content", "") for m in msgs
               if m.get("role") == "assistant" and m.get("type") == "answer"]
        return "\n".join(b for b in buf if b).strip()


# ---- 小红书网页采集/代发适配器(脆, playwright 延迟导入) -------------------
class XhsReader:
    """守夜机无头浏览器看群的适配器。阶段 A 只实现只读骨架。

    playwright 不在核心依赖里、且必须有持久登录态(storage_state)才能跑;
    缺任一样都明确抛错, 让 BridgeHealth 记 degraded, 而不是让整个桥崩掉。
    页面选择器以守夜机真机标定为准(小红书前端会变), 这里给出最小闭环位置。
    """

    def __init__(self, state_path, groups=None, headless=True, proxy=None):
        self.state_path = state_path
        self.groups = groups or {}
        self.headless = headless
        self.proxy = proxy
        self._pw = self._browser = self._page = None

    def _ensure_driver(self):
        if self._page is not None:
            return
        if not Path(self.state_path).exists():
            raise RuntimeError(f"缺小红书登录态: {self.state_path}")
        try:
            from playwright.sync_api import sync_playwright  # 延迟导入
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("守夜机没装 playwright, 巡群只读跑不了") from exc
        self._pw = sync_playwright().start()
        launch = {"headless": self.headless,
                  "args": ["--no-sandbox", "--disable-dev-shm-usage",
                           "--disable-blink-features=AutomationControlled"]}
        if self.proxy:
            launch["proxy"] = {"server": self.proxy}
        self._browser = self._pw.chromium.launch(**launch)
        self._ctx = self._browser.new_context(
            storage_state=self.state_path, locale="zh-CN",
            timezone_id="Asia/Shanghai")
        self._page = self._ctx.new_page()

    def read_latest(self, group_url, since_ts=0):  # pragma: no cover - 真机标定
        """打开群聊页, 返回结构化消息 dict 列表。选择器待守夜机真机校准。"""
        self._ensure_driver()
        self._page.goto(group_url, wait_until="domcontentloaded", timeout=30000)
        self._page.wait_for_timeout(3000)
        # TODO(守夜机真机): 按小红书网页版实际 DOM 抽取 发送人/时间/正文/@,
        # 产出 {msg_id,sender,sender_id,text,ts,mentioned,mention_target,from_self}
        raise NotImplementedError("群消息 DOM 抽取待守夜机真机标定")

    def close(self):
        for closer in (getattr(self, "_browser", None), getattr(self, "_pw", None)):
            try:
                if closer:
                    closer.close()
            except Exception:  # noqa: BLE001
                pass


# ---- 桥编排: 把上面零件缝起来 --------------------------------------------
class Bridge:
    """输入一批采集到的群消息, 出去重/路由/会话, 阶段 A 只落 pending 不真发。"""

    def __init__(self, state_dir, router=None, keeper=None, brains=None,
                 mode=COLD, send_mode=PROXY, dry_run=True):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.router = router or Router(mode)
        self.keeper = keeper or ConversationKeeper(state_dir)
        self.brains = brains or {}
        self.send_mode = send_mode
        self.dry_run = dry_run
        self.pending = self.dir / "pending.jsonl"

    def _append_pending(self, rec):
        with self.pending.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def tick(self, messages, supplement_map=None):
        """返回这一批产生的待办/已发结果列表。纯逻辑, dry_run 不联网不发。"""
        supplement_map = supplement_map or {}
        out = []
        for m in messages:
            decision = self.router.route(m)
            target = decision["target"]
            if target == T_NONE:
                continue
            rec, _ = self.keeper.note_turn(target, recent=messages, ts=m.get("ts"))
            ctx = {"session_id": rec["session_id"], "digest": rec.get("digest", "")}
            item = {"msg_key": msg_key(m), "target": target,
                    "reason": decision["reason"], "incoming": m, "ctx": ctx,
                    "ts": int(time.time())}
            if self.dry_run or target not in self.brains:
                # 阶段 A: 只判不发; 或没有对应脑也只排队
                item["status"] = "pending"
                self._append_pending(item)
                out.append(item)
                continue
            try:
                ans = self.brains[target].ask(m.get("text", ""), ctx)
                lines = render_outbound(
                    target, ans.get("reply", ""),
                    supplement=supplement_map.get(msg_key(m), ""),
                    mode=self.send_mode)
                item.update(status="ready", lines=lines)
                self._append_pending(item)
                out.append(item)
            except Exception as exc:  # noqa: BLE001 脑挂了不炸整桥, 交健康状态机
                item.update(status="brain_error", err=str(exc)[:120])
                self._append_pending(item)
                out.append(item)
        return out


def token_equal(a, b):
    """恒定时间比较口令(对外管理口用)。"""
    return hmac.compare_digest(str(a or ""), str(b or ""))


if __name__ == "__main__":
    # 最小自检: 不联网, 演示 cold 模式只被点名才回
    import tempfile

    demo = [
        {"sender": "群友甲", "text": "今天天气不错"},
        {"sender": "群友乙", "text": "@小扣子 你在吗", "mentioned": True,
         "mention_target": "kouzi", "ts": int(time.time())},
        {"sender": "小扣子", "text": "小扣子：我在的", "from_self": True},
    ]
    with tempfile.TemporaryDirectory() as td:
        b = Bridge(td, dry_run=True)
        for r in b.tick(demo):
            print(json.dumps(r, ensure_ascii=False))
