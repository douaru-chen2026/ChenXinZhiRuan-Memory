#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
werewolf_referee.py —— 辰心知阮 · 9 人狼人杀多 AI 裁判(状态机 + 单线私密 JSON)。

板子: 3 狼人 / 3 平民 / 预言家 / 女巫 / 猎人(标准 9 人屠边局, 可切屠城)。
设计死线(对应阿阮&阿境简案第 3 条, 也是多 AI 狼人杀最容易作弊处):
  全局身份字典 role_of 只活在裁判 Game 内部。public_brief(viewer) 只允许按下标
  role_of[viewer] 取【viewer 本人】的牌(本人身份/狼队友/自己的验人记录/药剂余量,
  无状态外脑每次调用都是新的, 不带上这些它白天就不记得自己是谁、验过谁); 它绝不
  遍历、也拿不到任何【他人】的隐藏身份(自测用源码扫描锁死: role_of 下标只能是
  viewer)。每个玩家白天拿到: ①仅属于自己的私密块 ②公开局势与公开发言历史;
  夜间再按身份给仅属于自己的一条私密简报。狼队友只发给狼本人, 绝不进他人公共包。
  运行模式对齐会玩: 每个动作有硬超时(action_timeout), 到点托管推进、故障不判出局,
  绝不让任何一颗脑或一个动作把整局钉死。

阶段(状态机): 发牌 -> 夜(狼刀/预言家验/女巫救毒, 单线 JSON 隔离) -> 结算夜死
  -> 白天(死讯/遗言/猎人开枪 -> 顺序发言[>=100字, 禁贴脸] -> 公投/平票 PK)
  -> 胜负(屠边: 狼灭 或 神全灭/民全灭; 狼数>=好人绑票胜) -> 未分则入夜。

玩家后端三种, 同一接口 act():
  * ScriptedBackend  确定性假玩家, 给 --selftest / --dry-run 无人值守验证用, 不烧 token;
  * HumanBackend     轮到阿阮时从 stdin 读(她占一个座, 其余是 AI);
  * ApiBackend       OpenAI 兼容外脑(复用会审台同一套 .secrets 钥匙), 阿境=gemini 等。

用法:
  python3 werewolf_referee.py --selftest           # 规则/隔离/结算自测, 不联网
  python3 werewolf_referee.py --dry-run            # 假玩家无人值守跑一整局看流程
  python3 werewolf_referee.py --real roster.json   # 按座位编排真烧 API 开一局(roster 见文末示例)
"""
import argparse
import json
import os
import random
import re
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量与板子
# ---------------------------------------------------------------------------
WOLF = "狼人"
SEER = "预言家"
WITCH = "女巫"
HUNTER = "猎人"
VILLAGER = "平民"
GODS = (SEER, WITCH, HUNTER)          # 神阵营
BOARD_9 = [WOLF] * 3 + [VILLAGER] * 3 + [SEER, WITCH, HUNTER]
SEATS = list(range(1, 10))             # 1..9 号座位

# 贴脸(非游戏逻辑担保)词表: 命中即判违规, 默认直接出局(简案硬约束)
TIELI_WORDS = (
    "我发誓", "我要是狼", "我是狼我就", "骗你我就", "我赌咒", "赌咒", "我卸载",
    "我退游", "以人格担保", "我全家", "我死妈", "不得好死", "出门被车",
    "现实中我要是", "我贴脸", "我用命担保",
)
MIN_SPEECH = 100                       # 白天发言最少字数(去空白字符)


# ---------------------------------------------------------------------------
# 解析与裁判规则小工具(纯函数, 便于自测)
# ---------------------------------------------------------------------------
def extract_json(text):
    """从模型自由文本里容错抽出第一个 JSON 对象(它常带解释/代码围栏)。失败抛 ValueError。"""
    if not text:
        raise ValueError("空回复")
    fenced = re.search(r"\{.*\}", text, re.DOTALL)
    raw = fenced.group(0) if fenced else text.strip()
    return json.loads(raw)


def speech_len(text):
    """去空白后的字符数, 作为中文发言字数口径。"""
    return len(re.sub(r"\s", "", text or ""))


def detect_tieli(text):
    """命中贴脸词返回该词, 否则 None。"""
    for w in TIELI_WORDS:
        if w in (text or ""):
            return w
    return None


def majority(votes, tie_pref="low"):
    """多数决; 返回(最高目标, 票数, 平票目标列表)。votes: list[seat/None]。"""
    tally = {}
    for v in votes:
        if v is None:
            continue
        tally[v] = tally.get(v, 0) + 1
    if not tally:
        return None, 0, []
    top = max(tally.values())
    winners = sorted([s for s, n in tally.items() if n == top])
    if len(winners) == 1:
        return winners[0], top, []
    return (winners[0] if tie_pref == "low" else random.choice(winners),
            top, winners)


# ---------------------------------------------------------------------------
# 玩家后端
# ---------------------------------------------------------------------------
class Backend:
    """所有玩家后端统一接口: act(prompt) -> 纯文本。"""

    # 最近一次动作是否真拿到有效回应: 区别'网络/超时故障'与'内容不达标',
    # 故障要托管推进、不能把连不上的脑当成违规判出局。Scripted/Human 恒真。
    last_ok = True
    is_human = False        # 网页人类座(走倒计时)
    blocking_human = False  # 命令行真人座(阻塞等输入, 不计时)

    def act(self, prompt):  # pragma: no cover - 由子类实现
        raise NotImplementedError


class ScriptedBackend(Backend):
    """确定性假玩家: 靠 game 注入的脚本决策, 仅用于自测/dry-run, 不联网。"""

    def __init__(self, script=None):
        self.script = script or {}
        self.calls = 0

    def act(self, prompt):
        return self.script.get("reply", "{}")


class HumanBackend(Backend):
    """人类玩家(阿阮): 把裁判提示打到屏幕, 从 stdin 读她的动作/发言。"""
    blocking_human = True

    def act(self, prompt):
        print("\n————— 轮到你操作(人类玩家)—————")
        print(prompt)
        print("————— 请在下方输入(动作发 JSON, 发言直接打字, 单独一行结束)—————")
        return sys.stdin.readline().strip() if not sys.stdin.isatty() else input("> ").strip()


class ApiBackend(Backend):
    """OpenAI 兼容外脑。key 复用会审台: 优先环境变量, 回落仓外 .secrets/<file>。"""

    REPO = Path(__file__).resolve().parents[2]
    SECRET = REPO.parent / ".secrets"

    def __init__(self, provider, base_url, model, secret_file,
                 env_key, temp=0.85, persona=""):
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.secret_file = secret_file
        self.env_key = env_key
        self.temp = temp
        self.persona = persona
        # 直连各厂商公网 API, 绕开环境里可能拦截的代理(同会审台/信筒口径)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.last_ok = True       # 最近一次调用是否真拿到有效回复
        self.last_err = ""        # 最近一次故障原因(供裁判托管/前端展示)

    def _key(self):
        v = os.environ.get(self.env_key, "").strip()
        if v:
            return v
        p = self.SECRET / self.secret_file
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""

    def act(self, prompt):
        self.last_err = ""
        key = self._key()
        if not key:
            self.last_ok = False
            self.last_err = "缺钥匙"
            return "{}"
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content":
                    "你在玩 9 人狼人杀, 只依据裁判给你的信息发言, 不要编造没给你的身份。"
                    + (self.persona or "")},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temp,
        }, ensure_ascii=False).encode("utf-8")
        # 单次 40s、最多 2 次: 真脑慢/连不上时最坏 ~80s 就放手, 交给动作级硬超时托管,
        # 绝不让一颗脑把整局钉死在原地(会玩模式: 到点就推进)。
        for attempt in range(2):
            try:
                req = urllib.request.Request(self.base_url + "/chat/completions",
                                             data=body, method="POST")
                req.add_header("Authorization", f"Bearer {key}")
                req.add_header("Content-Type", "application/json")
                with self.opener.open(req, timeout=40) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                content = payload["choices"][0]["message"]["content"].strip()
                self.last_ok = True
                return content
            except Exception as e:  # noqa: BLE001 单颗脑任何故障都不许炸掉整局
                self.last_err = f"{type(e).__name__}: {str(e)[:80]}"
                if attempt == 1:
                    self.last_ok = False
                    return "{}"


# ---------------------------------------------------------------------------
# 裁判状态机
# ---------------------------------------------------------------------------
class Game:
    def __init__(self, backends, seed=790511, win="edge",
                 first_night_self_save=True, strict_speech=True, verbose=True,
                 wolf_chat_rounds=1, action_timeout=45, on_progress=None):
        """backends: {seat: Backend}。win: edge=屠边 / city=屠城。
        wolf_chat_rounds: 狼人夜间讨论轮数(每只狼每轮可打字发言、互相可见后出刀)。
        action_timeout: 单个动作(一次外脑调用)硬超时秒数, 到点托管推进、绝不死等,
                         对齐会玩'每阶段倒计时到点自动走'的运行模式。
        on_progress: 回调 fn(seat, phase), 每次等待某座前置位, 供前端显示'轮到谁'。"""
        assert sorted(backends) == SEATS, "必须是 1..9 九个座位"
        self.rng = random.Random(seed)
        self.backends = backends
        self.win_mode = win
        self.first_night_self_save = first_night_self_save
        self.strict_speech = strict_speech
        self.verbose = verbose
        self.wolf_chat_rounds = max(1, wolf_chat_rounds)
        self.action_timeout = action_timeout
        self.on_progress = on_progress
        # 每个动作丢独立线程跑, 主流程按硬超时收口; 9 座各一线程够用
        self._exec = ThreadPoolExecutor(max_workers=9, thread_name_prefix="ww-act")
        self.progress = {"seat": None, "phase": "", "since": 0.0}
        self.force_advance = False     # 外部手动"跳过当前等待"时置 True
        self.faults = []               # (seat, phase, reason) 故障/超时托管留痕

        roles = BOARD_9[:]
        self.rng.shuffle(roles)
        self.role_of = {seat: roles[i] for i, seat in enumerate(SEATS)}  # 上帝字典, 私密
        self.wolves = sorted(s for s in SEATS if self.role_of[s] == WOLF)
        self.alive = {s: True for s in SEATS}
        self.out_order = []          # (seat, 公开原因) 只写可公开信息, 不含身份
        self.public_log = []         # 全部公开事件(死讯/遗言/发言/投票/违规)
        # 夜间私密事件: {"seat":只对谁可见(None=仅上帝), "kind":.., "text":..}
        # 只用于本人视角/上帝回放, 绝不进任何其他玩家的 public_brief
        self.private_log = []
        self.day = 0
        # 女巫药剂 / 预言家验人记录 / 中毒集合(中毒猎人不能开枪), 全是私密
        self.heal_left = True
        self.poison_left = True
        self.poisoned = set()
        self.seen = {s: {} for s in SEATS}
        self.winner = None

    # -- 输出 ----------------------------------------------------------------
    def say(self, kind, text):
        line = {"day": self.day, "kind": kind, "text": text}
        self.public_log.append(line)
        if self.verbose:
            print(f"[D{self.day}|{kind}] {text}")

    def whisper(self, seat, kind, text):
        """夜间私密事件(狼讨论/验人/女巫决策), 只对该座位与上帝可见。"""
        self.private_log.append({"day": self.day, "seat": seat,
                                 "kind": kind, "text": text})
        if self.verbose:
            who = "上帝" if seat is None else f"{seat}号"
            print(f"[D{self.day}|私密·{who}|{kind}] {text}")

    def alive_seats(self):
        return [s for s in SEATS if self.alive[s]]

    def seats_of_role_alive(self, role):
        return [s for s in self.alive_seats() if self.role_of[s] == role]

    # -- 关键: 公共视角, 入参【没有身份字典】, 结构上不可能泄密 ----------------
    def public_brief(self, viewer):
        """生成发给 viewer 的白天/投票公共包。只含公开信息; 函数签名里没有全局身份
        字典这个入参, 结构上就拿不到任何人的隐藏身份(防天眼作弊的根本手段)。"""
        alive_str = "、".join(f"{s}号" for s in self.alive_seats())
        lines = [
            f"板子: 9 人局(3 狼、3 民、预言家/女巫/猎人)。现在是第 {self.day} 天。",
            f"你是 {viewer} 号。当前存活: {alive_str}。",
        ]
        # —— 只属于 viewer 本人的私密: 自己的牌/狼队友/验人记录/药剂余量 ——
        # 无状态外脑每次调用都是新的, 不把它自己的这些信息带上, 它白天就不记得
        # 自己是谁、验过谁; 这是'本人本该知道的', 不含任何他人隐藏身份, 不破隔离。
        my_role = self.role_of[viewer]
        priv = [f"你的身份: {my_role}。"]
        if my_role == WOLF:
            mates = [w for w in self.wolves if w != viewer and self.alive[w]]
            priv.append("你是狼人, 存活狼队友: "
                        + ("、".join(f"{w}号" for w in mates) if mates else "目前只剩你")
                        + "(狼夜间互知, 白天别暴露)。")
        if my_role == SEER and self.seen.get(viewer):
            rec = "、".join(f"{t}号={r}" for t, r in sorted(self.seen[viewer].items()))
            priv.append(f"你此前的验人记录(仅你自己知道): {rec}。")
        if my_role == WITCH:
            priv.append(f"你的药剂: 解药{'剩1瓶' if self.heal_left else '已用'}, "
                        f"毒药{'剩1瓶' if self.poison_left else '已用'}(一晚只能用一瓶)。")
        lines.append("【仅你可见·你的私密】" + " ".join(priv))
        lines.append("—— 截至目前全部公开信息(死讯/遗言/发言/投票, 按时间) ——")
        for e in self.public_log:
            lines.append(e["text"])
        lines.append("注意: 你只知道自己的身份; 上面没有、也不允许你假设任何人的隐藏身份。")
        return "\n".join(lines)

    def _set_progress(self, seat, phase):
        self.progress = {"seat": seat, "phase": phase, "since": time.time()}
        if self.on_progress:
            try:
                self.on_progress(seat, phase)
            except Exception:  # noqa: BLE001 进度回调绝不能反过来卡住对局
                pass

    def _ask(self, seat, prompt, phase="动作", timeout=None, hard_default=None):
        """统一取文本(会玩式不卡死的关键)。
        置进度 -> 丢独立线程跑后端 -> 主流程按硬超时收口:
        后端异常 / 超过 deadline / 外部 force_advance, 都返回 hard_default 继续,
        绝不允许任何一颗脑或一个动作把整局钉死在原地。"""
        self._set_progress(seat, phase)
        backend = self.backends[seat]
        if getattr(backend, "blocking_human", False):
            return backend.act(prompt)          # 命令行真人: 阻塞交互, 不计时
        if getattr(backend, "is_human", False):
            # 网页里的阿阮: 发言/遗言宽限 130s, 其余动作 45s(她自己面板也有倒计时)
            deadline = 130 if ("发言" in phase or "遗言" in phase) else 45
        else:
            deadline = self.action_timeout if timeout is None else timeout
        if hard_default is None:
            hard_default = "{}"
        fut = self._exec.submit(backend.act, prompt)
        start = time.time()
        while True:
            if fut.done():
                try:
                    return fut.result()
                except Exception as e:  # noqa: BLE001 act 自身抛错也不炸局
                    backend.last_ok = False
                    self.faults.append((seat, phase, f"后端异常:{type(e).__name__}"))
                    return hard_default
            elapsed = time.time() - start
            if self.force_advance:
                self.force_advance = False
                self.faults.append((seat, phase, "手动跳过,托管"))
                return hard_default
            if elapsed >= deadline:
                self.faults.append((seat, phase, f"硬超时{deadline:.0f}s,托管"))
                return hard_default
            time.sleep(0.25)

    def _ask_json(self, seat, prompt, default, phase="动作"):
        """取 JSON 动作; 解析失败重试一次, 再失败用安全默认(不崩局)。
        phase 透传给 _ask 做进度展示与硬超时托管。"""
        for _ in range(2):
            raw = self._ask(seat, prompt, phase=phase)
            try:
                return extract_json(raw)
            except ValueError:
                prompt += "\n(上次回复不是合法 JSON, 请只返回一个 JSON 对象)"
        return default

    # -- 夜间 ----------------------------------------------------------------
    def _resolve_night_deaths(self, killed, use_heal, poison_t, witch_seat):
        """夜间死亡纯结算(便于单测)。官方9人局口径:
        - 解药只解刀; 同刀同毒时'救刀不解毒'(仍毒亡且记被毒, 猎人因此不能开枪);
        - 女巫一晚只能用一瓶: 若解药生效, 同夜毒药作废、不消耗;
        - 自救仅首夜、且 first_night_self_save 打开时有效。"""
        deaths, poisoned = set(), set()
        can_self = self.day == 1 and self.first_night_self_save
        saved = heal_used = False
        if (killed is not None and self.heal_left and use_heal
                and (killed != witch_seat or can_self)):
            saved = heal_used = True
        if killed is not None and not saved:
            deaths.add(killed)
        poison_used = False
        # 一晚一瓶: 解药已用则毒药不生效也不消耗
        if self.poison_left and not heal_used and poison_t in self.alive_seats():
            deaths.add(poison_t)
            poisoned.add(poison_t)
            poison_used = True
        return {"deaths": deaths, "poisoned": poisoned, "saved": saved,
                "heal_used": heal_used, "poison_used": poison_used}

    def night(self):
        self.day += 1
        if self.verbose:
            print(f"\n================ 第 {self.day} 夜 · 天黑请闭眼 ================")
        # 1) 狼人夜: 狼队先打字讨论(互相可见、好人公共包永远看不到), 最后统一出刀
        wolves_alive = self.seats_of_role_alive(WOLF)
        targets = [s for s in self.alive_seats() if self.role_of[s] != WOLF]
        wolf_chat = []              # [{"seat":w,"say":..}] 仅狼队之间可见
        wolf_votes, voted = [], set()
        if wolves_alive and targets:
            team = "、".join(f"{x}号" for x in wolves_alive)
            tgt = "、".join(f"{s}号" for s in targets)
            for rnd in range(self.wolf_chat_rounds):
                last = rnd == self.wolf_chat_rounds - 1
                for w in wolves_alive:
                    hist = "\n".join(f"{c['seat']}号狼: {c['say']}"
                                     for c in wolf_chat if c["say"]) or "(暂无, 你先开口)"
                    tail = "\n这是最后一轮, 讨论后必须给出本晚刀目标。" if last else ""
                    ask = (
                        f"【仅你可见·狼人夜】存活狼队友: {team}(你们互相知晓、可以商量战术)。"
                        f"可刀目标(非狼存活): {tgt}。\n"
                        f"—— 狼队目前讨论(仅你们狼可见, 好人看不到) ——\n{hist}\n"
                        '返回 JSON: {"say":"想对队友说的话(没有就空字符串)",'
                        '"vote_kill":座位号或null}。' + tail)
                    d = self._ask_json(w, ask, {"say": "",
                                                "vote_kill": targets[0] if last else None},
                                       phase=f"{w}号狼人夜商量")
                    say_txt = str(d.get("say", "")).strip()[:200]
                    if say_txt:
                        wolf_chat.append({"seat": w, "say": say_txt})
                        self.whisper(None, "狼讨论", f"{w}号狼: {say_txt}")
                    kt = d.get("vote_kill")
                    if last and kt in targets:
                        wolf_votes.append(kt)
                        voted.add(w)
            # 兜底: 最后一轮仍没出刀的狼, 单独补问一次
            for w in wolves_alive:
                if w in voted:
                    continue
                d = self._ask_json(
                    w, f"【仅你可见·狼人夜】狼队统一出刀, 可刀: {tgt}。\n"
                       '返回 {"vote_kill": 座位号}。', {"vote_kill": targets[0]},
                    phase=f"{w}号统一出刀")
                if d.get("vote_kill") in targets:
                    wolf_votes.append(d["vote_kill"])
            killed, _, _ = majority(wolf_votes)
        else:
            killed = None
        self.whisper(None, "狼刀",
                     f"狼队讨论{len(wolf_chat)}句, 多数决出刀: "
                     f"{f'{killed}号' if killed is not None else '空刀'}")

        # 2) 预言家验人(私密), 结果只告诉预言家本人
        seer = self.seats_of_role_alive(SEER)
        if seer:
            s = seer[0]
            cand = [x for x in self.alive_seats() if x not in self.seen[s]]
            if cand:
                ask = (f"【仅你可见·预言家夜】可选验人: "
                       f"{'、'.join(f'{x}号' for x in cand)}。\n"
                       '返回 {"target": 座位号}。')
                data = self._ask_json(s, ask, {"target": cand[0]},
                                      phase=f"{s}号预言家验人")
                t = data.get("target", cand[0])
                if t not in cand:
                    t = cand[0]
                is_wolf = self.role_of[t] == WOLF
                self.seen[s][t] = WOLF if is_wolf else "好人"
                self._ask(
                    s, f"【仅你可见·预言家结果】你查验 {t} 号, 结果是: "
                       f"{'狼人' if is_wolf else '好人(非狼)'}。记住它, 白天可决定是否报验。",
                    phase=f"{s}号接收验人结果", hard_default="")
                self.whisper(s, "预言家验人",
                             f"你查验 {t} 号 -> {'狼人' if is_wolf else '好人(非狼)'}")

        # 3) 女巫: 私密告知当夜被刀者与药剂状态, 决定救/毒; 结算走纯函数
        deaths = set()
        poisoned_this_night = set()
        witch = self.seats_of_role_alive(WITCH)
        if witch:
            w_seat = witch[0]
            can_self = self.day == 1 and self.first_night_self_save
            self_hint = ("首夜可自救。" if self.day == 1 and self.first_night_self_save
                         else "今夜不可自救(救自己无效)。")
            ask = (
                f"【仅你可见·女巫夜】今夜被刀的是 "
                f"{f'{killed}号' if killed is not None else '空刀(无人被刀)'}。"
                f"解药剩{1 if self.heal_left else 0} 毒药剩{1 if self.poison_left else 0}。"
                f"{self_hint}一晚只能用一瓶, 解药和毒药二选一。"
                '\n返回 {"use_heal": true/false, "poison_target": 座位号或null}。')
            d = self._ask_json(w_seat, ask,
                               {"use_heal": False, "poison_target": None},
                               phase=f"{w_seat}号女巫用药")
            r = self._resolve_night_deaths(
                killed, bool(d.get("use_heal")), d.get("poison_target"), w_seat)
            deaths = r["deaths"]
            poisoned_this_night = r["poisoned"]
            if r["heal_used"]:
                self.heal_left = False
            if r["poison_used"]:
                self.poison_left = False
            note = ""
            if (r["heal_used"] and self.poison_left
                    and d.get("poison_target") in self.alive_seats()):
                note = "(同夜又救又毒: 按一晚一瓶, 解药优先、毒药作废不消耗)"
            self.whisper(w_seat, "女巫决策",
                         f"救={'是' if r['saved'] else '否'} "
                         f"毒={sorted(poisoned_this_night) or '无'}{note}")
        elif killed is not None:
            deaths.add(killed)
        self.poisoned |= poisoned_this_night
        self._last_deaths = sorted(deaths)

    # -- 猎人开枪(被毒不可开; 被猎人带走也不再连锁) ----------------------------
    def _hunter_shot(self, seat, allow, cause_public):
        if not allow or self.role_of[seat] != HUNTER or not self.alive[seat]:
            return
        targets = self.alive_seats()
        if not targets:
            return
        ask = (f"【仅你可见·猎人】你因{cause_public}出局, 可开枪带走一名存活者: "
               f"{'、'.join(f'{s}号' for s in targets)}。\n"
               '返回 {"shoot": 座位号或null}。')
        d = self._ask_json(seat, ask, {"shoot": targets[-1]},
                           phase=f"{seat}号猎人开枪")
        t = d.get("shoot")
        if t in targets:
            self.alive[t] = False
            self.out_order.append((t, "被猎人带走"))
            self.say("出局", f"{seat} 号猎人开枪, 带走 {t} 号。")

    def _has_last_words(self, seat, reason):
        """官方9人局遗言资格: 白天出局(被投/违规)有; 仅首夜被刀有;
        被女巫毒杀无; 首夜之后夜里被刀无; 被猎人带走无。"""
        if seat in self.poisoned:
            return False
        if reason == "夜间出局":
            return self.day == 1
        if reason == "被猎人带走":
            return False
        return True

    def _kill(self, seat, reason, can_hunter=True):
        """统一出局: 置死、记公开顺序, 按资格处理遗言, 再处理猎人开枪。"""
        if not self.alive[seat]:
            return
        self.alive[seat] = False
        self.out_order.append((seat, reason))
        self.say("出局", f"{seat} 号出局({reason})。")
        if self._has_last_words(seat, reason):
            last = self._ask(
                seat, f"你是 {seat} 号, 刚因'{reason}'出局, 留一句公开遗言(不超过60字):",
                phase=f"{seat}号留遗言", hard_default="")
            if last:
                self.say("遗言", f"{seat} 号: {last[:120]}")
        else:
            self.say("遗言", f"{seat} 号按规则无遗言。")
        # 中毒者不能开枪; 被猎人带走者不连锁(9人局仅一猎人, 实际不会发生)
        allow = can_hunter and seat not in self.poisoned
        self._hunter_shot(seat, allow, reason)

    # -- 白天 ----------------------------------------------------------------
    def day_phase(self):
        if self.verbose:
            print(f"\n---------------- 第 {self.day} 天 · 天亮 ----------------")
        # 死讯
        if not self._last_deaths:
            self.say("死讯", "昨夜平安无事, 无人出局。")
        else:
            who = "、".join(f"{s}号" for s in self._last_deaths)
            self.say("死讯", f"昨夜出局: {who}。")
            for s in self._last_deaths:
                self._kill(s, "夜间出局", can_hunter=True)

        # 顺序发言
        for s in self.alive_seats():
            speech = self._speech(s)
            if speech is None:
                continue  # 已因违规出局

        # 公投(可递归一层 PK)
        self._vote(round_n=1)

    def _speech(self, s):
        """单个玩家发言, 做字数/贴脸裁判。返回发言文本; 若被判出局返回 None。"""
        brief = self.public_brief(s)
        ask = (brief + "\n【白天发言】按座位轮到你公开发言, 必须包含你自己的逻辑推导, "
                       f"去空白后不少于 {MIN_SPEECH} 字; 严禁贴脸(发誓/赌咒/现实担保)。"
                       "直接输出发言正文, 不要 JSON。")
        text = self._ask(s, ask, phase=f"{s}号白天发言", hard_default="")
        # 网络/超时故障 ≠ 玩家违规: 连不上就托管一句、让他活着继续, 不判出局
        if (not getattr(self.backends[s], "last_ok", True)
                or not text.strip() or text.strip() == "{}"):
            self.say("发言", f"{s} 号: (连接不稳, 本回合发言从简, 继续听大家盘逻辑)")
            return "(连接托管)"
        bad = detect_tieli(text)
        if bad:
            self.say("违规", f"{s} 号发言贴脸(命中'{bad}'), 按规则直接判出局。")
            self._kill(s, "贴脸违规出局", can_hunter=False)
            return None
        if speech_len(text) < MIN_SPEECH:
            text2 = self._ask(s, ask + f"\n(你刚才只有{speech_len(text)}字, 请补到{MIN_SPEECH}字以上)",
                              phase=f"{s}号补发言", hard_default="")
            # 补问时连不上: 用首轮(虽短但真实)发言, 不因网络故障判出局
            if (not getattr(self.backends[s], "last_ok", True)
                    or not text2.strip() or text2.strip() == "{}"):
                self.say("发言", f"{s} 号: {text}")
                return text
            if detect_tieli(text2):
                self.say("违规", f"{s} 号补发言仍贴脸, 判出局。")
                self._kill(s, "贴脸违规出局", can_hunter=False)
                return None
            if speech_len(text2) >= MIN_SPEECH:
                text = text2
            elif self.strict_speech:
                self.say("违规", f"{s} 号两次发言不足 {MIN_SPEECH} 字, 按规则出局。")
                self._kill(s, "发言不达标出局", can_hunter=False)
                return None
        self.say("发言", f"{s} 号: {text}")
        return text

    def _vote(self, round_n, candidates=None):
        voters = self.alive_seats()
        if candidates is None:
            candidates = voters[:]
        votes = {}
        for v in voters:
            opts = [c for c in candidates if self.alive[c] and c != v] or \
                   [c for c in candidates if self.alive[c]]
            brief = self.public_brief(v)
            ask = (brief + f"\n【第{round_n}轮公投】投票目标(存活): "
                           f"{'、'.join(f'{c}号' for c in opts)}; 可弃票。\n"
                           '返回 {"vote_target": 座位号或null}。')
            d = self._ask_json(v, ask, {"vote_target": opts[-1] if opts else None},
                               phase=f"{v}号公投")
            t = d.get("vote_target")
            votes[v] = t if t in opts else None
        valid = [t for t in votes.values() if t is not None]
        self.say("投票", "公投情况: " + "、".join(
            f"{v}->{('弃' if t is None else str(t) + '号')}" for v, t in votes.items()))
        target, n, tie = majority(valid)
        if target is None:
            self.say("投票", "全员弃票, 本轮无人出局。")
            return
        if tie:
            if round_n >= 2:  # 已 PK 一轮仍平, 不再递归, 本轮无人出局
                self.say("投票", "PK 后仍平票, 本轮无人出局。")
                return
            self.say("投票", f"平票: {'、'.join(f'{x}号' for x in tie)}, 进入 PK。")
            for p in tie:
                if self.alive[p]:
                    self._speech(p)
            return self._vote(round_n + 1, candidates=tie)
        self._kill(target, f"公投出局({n}票)", can_hunter=True)

    # -- 胜负 ----------------------------------------------------------------
    def check_win(self):
        w = len(self.seats_of_role_alive(WOLF))
        g = sum(len(self.seats_of_role_alive(r)) for r in GODS)
        v = len(self.seats_of_role_alive(VILLAGER))
        if w == 0:
            self.winner = "好人阵营"
        elif self.win_mode == "city" and g + v == 0:
            self.winner = "狼人阵营"
        elif self.win_mode == "edge" and (g == 0 or v == 0):
            self.winner = "狼人阵营"      # 屠边: 神全灭 或 民全灭
        elif w >= g + v:
            self.winner = "狼人阵营"      # 绑票胜
        return self.winner

    # -- 完整一局 -------------------------------------------------------------
    def run(self, max_days=12):
        if self.verbose:
            print("身份已发(仅裁判可见):",
                  {s: self.role_of[s] for s in SEATS})
        while self.day < max_days and not self.winner:
            self.night()
            self.day_phase()
            if self.check_win():
                break
        if self.verbose:
            print("\n================ 游戏结束 ================")
            print("胜利方:", self.winner or "(达到天数上限未分胜负)")
            print("身份总表:", {s: self.role_of[s] for s in SEATS})
            print("出局顺序:", self.out_order)
        # 不等待仍挂在网络上的动作线程(硬超时已托管), 避免它们拖住整局收尾
        self._exec.shutdown(wait=False)
        return self.winner


# ---------------------------------------------------------------------------
# 干跑脚本玩家(确定性, 保证 dry-run 收敛且发言达标)
# ---------------------------------------------------------------------------
def scripted_backends():
    """9 个假玩家共用一套确定性策略, 由后端读 prompt 文本判断当前阶段。"""

    class Auto(ScriptedBackend):
        """阶段只认裁判打的【】标记, 不被公共历史里出现的'出局/猎人/公投'等词带偏。"""

        def act(self, prompt):
            def nums_after(head):
                m = re.search(head + r"(.*?)。", prompt, re.DOTALL)
                return [int(x) for x in re.findall(r"(\d+)号", m.group(1))] if m else []

            if "【仅你可见·狼人夜】" in prompt:
                n = nums_after(r"可刀目标.*?:")
                kt = min(n) if n else None
                if "统一出刀" in prompt:
                    return json.dumps({"vote_kill": kt}, ensure_ascii=False)
                return json.dumps(
                    {"say": f"我倾向刀{kt}号, 优先找像神的刀, 队友白天别站太齐",
                     "vote_kill": kt if "最后一轮" in prompt else None},
                    ensure_ascii=False)
            if "【仅你可见·预言家夜】" in prompt:
                n = nums_after(r"可选验人:.*?:")
                return json.dumps({"target": min(n) if n else None},
                                  ensure_ascii=False)
            if "【仅你可见·预言家结果】" in prompt:
                return "知道了"
            if "【仅你可见·女巫夜】" in prompt:
                # 有解药且真有人被刀就救(首夜), 从不开毒, 保证干跑确定收敛
                heal = "解药剩1" in prompt and "空刀" not in prompt
                return json.dumps({"use_heal": heal, "poison_target": None},
                                  ensure_ascii=False)
            if "【仅你可见·猎人】" in prompt:
                n = [int(x) for x in re.findall(r"(\d+)号", prompt)]
                return json.dumps({"shoot": n[-1] if n else None},
                                  ensure_ascii=False)
            if "轮公投】" in prompt:
                n = nums_after(r"投票目标.*?:")
                return json.dumps({"vote_target": n[-1] if n else None},
                                  ensure_ascii=False)
            if "留一句公开遗言" in prompt:
                return "我尽力了, 剩下的好人加油, 大家只根据公开信息继续盘逻辑别被带节奏。"
            # 白天发言(含兜底): 拼够 100 字的"有逻辑"发言
            seat = re.search(r"你是 (\d+) 号", prompt)
            me = seat.group(1) if seat else "?"
            filler = ("我先表水, 目前只依据已经公开的死讯、遗言和各位发言来推, "
                      "不会凭空指认任何人, 也不装没有的身份。先看夜间倒牌情况和投票队形, "
                      "谁的话前后矛盾、谁在强行带节奏、谁在跟风踩人, 我会重点听这几点。"
                      "我倾向先把信息位保护好, 让预言家的查验尽可能多报一轮, 女巫的药也要捏稳, "
                      "别在信息不足的时候乱洒。请后面的发言都具体给出怀疑对象和理由, "
                      "不要空喊口号、不要贴脸, 我们一步步把狼坑排干净, 好人才能赢。")
            return f"我是{me}号,{filler}"

    return {s: Auto() for s in SEATS}


# ---------------------------------------------------------------------------
# 自测: 规则 / 防泄密 / 夜间结算 / 投票 / 胜负
# ---------------------------------------------------------------------------
def selftest():
    ok = 0

    def check(name, cond):
        nonlocal ok
        assert cond, f"未通过: {name}"
        ok += 1
        print("  ✓", name)

    print("[1] 发牌正确")
    g = Game(scripted_backends(), seed=1, verbose=False)
    from collections import Counter
    c = Counter(g.role_of.values())
    check("3狼3民预女猎各1", c[WOLF] == 3 and c[VILLAGER] == 3 and
          all(c[r] == 1 for r in GODS))

    print("[2] 公共视角不含任何他人身份(接口层防泄密)")
    g.day = 2
    g.public_log.append({"day": 2, "kind": "死讯", "text": "昨夜出局: 3号。"})
    for viewer in SEATS:
        brief = g.public_brief(viewer)
        # 对"除自己外每个玩家", 其真实身份串绝不能出现在公共包里
        for other, role in g.role_of.items():
            if other == viewer:
                continue
            leak = f"{other}号" + "是"
            # 公共包不允许出现 "X号是狼人/预言家/女巫/猎人/平民" 这种断言
            for rname in (WOLF, SEER, WITCH, HUNTER, VILLAGER):
                check(f"{viewer}号视角未泄漏{other}号={rname}",
                      f"{other}号是{rname}" not in brief and
                      f"{other}号为{rname}" not in brief)
    import inspect
    # 允许取 viewer 本人身份(无状态脑必须知道自己的牌/验人/药剂), 但源码里
    # role_of 的下标只能是 viewer, 出现任何其他下标/遍历即判定天眼风险, 测试失败
    pb_idx = re.findall(r"role_of\[([^\]]+)\]", inspect.getsource(Game.public_brief))
    check("public_brief 只取本人 role_of[viewer], 绝不遍历/取他人身份",
          bool(pb_idx) and all(x.strip() == "viewer" for x in pb_idx))

    print("[3] 解析与发言规则纯函数")
    check("从围栏文本抽出JSON",
          extract_json('好的\n```json\n{"a": 1}\n```')["a"] == 1)
    check("中文字数口径", speech_len("我 是 豆\t阿\n辰") == 5)
    check("贴脸命中", detect_tieli("我发誓我真不是狼") == "我发誓")
    check("正常发言不判贴脸", detect_tieli("我觉得3号发言有漏洞") is None)
    t, n, tie = majority([1, 1, 2, 3])
    check("多数决", t == 1 and n == 2 and tie == [])
    t, n, tie = majority([1, 2])
    check("平票识别", tie == [1, 2])

    print("[4] 夜间死亡结算(纯函数: 救/毒/同刀同毒/自救边界)")
    g2 = Game(scripted_backends(), seed=2, verbose=False)
    g2.day = 1
    g2.alive = {s: True for s in SEATS}
    r = g2._resolve_night_deaths(5, True, None, 9)
    check("S1 刀5救5=平安夜", r["deaths"] == set() and r["saved"])
    r = g2._resolve_night_deaths(5, False, 6, 9)
    check("S2 刀5不救+毒6=双死且6记被毒",
          r["deaths"] == {5, 6} and r["poisoned"] == {6})
    r = g2._resolve_night_deaths(5, True, 6, 9)
    check("S3 一晚一瓶: 救5又报毒6=救生效、毒作废全场不死",
          r["saved"] and r["deaths"] == set())
    r = g2._resolve_night_deaths(5, False, None, 9)
    check("S4 刀5不救=5死", r["deaths"] == {5})
    r = g2._resolve_night_deaths(5, False, 5, 9)
    check("S5 同刀同毒(不救、毒的正是被刀者5)=死且记被毒",
          r["deaths"] == {5} and r["poisoned"] == {5} and not r["saved"])
    r = g2._resolve_night_deaths(5, True, 5, 9)
    check("S5b 对同一人又救又毒属一晚两瓶=取救、毒作废",
          r["saved"] and r["deaths"] == set() and not r["poison_used"])
    r = g2._resolve_night_deaths(5, True, None, 5)
    check("S6a 首夜且开关开=女巫可自救", r["saved"] and r["deaths"] == set())
    g2.day = 2
    r = g2._resolve_night_deaths(5, True, None, 5)
    check("S6b 次夜自救无效", (not r["saved"]) and r["deaths"] == {5})
    g2.first_night_self_save = False
    g2.day = 1
    r = g2._resolve_night_deaths(5, True, None, 5)
    check("S6c 关掉首夜自救开关则首夜也不可", not r["saved"])
    g2.first_night_self_save = True
    r = g2._resolve_night_deaths(5, True, 6, 9)
    check("S7 一晚一瓶: 救生效则同夜毒作废、不消耗",
          r["saved"] and r["deaths"] == set() and not r["poison_used"])
    r = g2._resolve_night_deaths(5, False, 6, 9)
    check("S8 不救只毒=刀5毒6双死、6记被毒",
          r["deaths"] == {5, 6} and r["poisoned"] == {6})

    print("[4b] 遗言资格(官方9人局口径)")
    g2.day = 1
    g2.poisoned = set()
    check("首夜被刀有遗言", g2._has_last_words(3, "夜间出局"))
    g2.day = 2
    check("次夜及以后被刀无遗言", not g2._has_last_words(3, "夜间出局"))
    check("白天被投出局有遗言", g2._has_last_words(3, "公投出局(5票)"))
    g2.poisoned = {3}
    check("被女巫毒杀无遗言", not g2._has_last_words(3, "夜间出局"))
    g2.poisoned = set()
    check("被猎人带走无遗言", not g2._has_last_words(3, "被猎人带走"))

    print("[5] 屠边胜负判定")
    g3 = Game(scripted_backends(), seed=3, verbose=False)
    for s in SEATS:  # 只留狼和民, 神全灭
        if g3.role_of[s] in GODS:
            g3.alive[s] = False
    check("神全灭=狼胜(屠边)", g3.check_win() == "狼人阵营")
    g4 = Game(scripted_backends(), seed=4, verbose=False)
    for s in SEATS:
        if g4.role_of[s] == WOLF:
            g4.alive[s] = False
    check("狼全灭=好人胜", g4.check_win() == "好人阵营")

    print("[6] 中毒猎人不能开枪")
    g5 = Game(scripted_backends(), seed=5, verbose=False)
    hunter = [s for s in SEATS if g5.role_of[s] == HUNTER][0]
    g5.poisoned.add(hunter)
    check("中毒猎人开枪被拒",
          g5._hunter_shot(hunter, True, "夜间出局") is None)

    print(f"\n自测全部通过, 共 {ok} 项断言。")


def main():
    ap = argparse.ArgumentParser(description="9人狼人杀多AI裁判")
    ap.add_argument("--selftest", action="store_true", help="跑规则与防泄密自测")
    ap.add_argument("--dry-run", action="store_true", help="假玩家无人值守跑一整局")
    ap.add_argument("--real", metavar="ROSTER_JSON", help="按座位编排真烧API")
    ap.add_argument("--seed", type=int, default=790511)
    ap.add_argument("--win", choices=["edge", "city"], default="edge")
    args = ap.parse_args()

    if args.selftest:
        selftest()
    elif args.dry_run:
        Game(scripted_backends(), seed=args.seed, win=args.win).run()
    elif args.real:
        with open(args.real, encoding="utf-8") as f:
            roster = json.load(f)
        # roster: [{"seat":1,"kind":"api","provider":"gemini",...},{"seat":2,"kind":"human"}]
        backends = build_roster(roster)
        Game(backends, seed=args.seed, win=args.win).run()
    else:
        ap.print_help()


def build_roster(roster):
    """把座位编排 JSON 实例化成 {seat: Backend}。API 座走 ApiBackend, human 走 stdin。"""
    # provider -> (默认base_url, 默认model, .secrets文件名, key环境变量, base环境变量)
    # 阿境是第三方中转, base 不写死在仓里, 运行时从环境变量 GEMINI_RELAY_BASE_URL 取
    # (守夜机 source /etc/council/env 即有), 与会审台同一套钥匙/地址。
    # provider -> (默认base, 默认model, .secrets钥匙文件, key环境变量,
    #             base环境变量, base的.secrets配置文件)
    # 第三方中转(阿境 gemini / 克劳德 claude)的真实地址不写死在公开仓,
    # 运行时按 roster.base_url -> 环境变量 -> .secrets 配置文件 顺序取。
    # 六颗本体外脑: 千问/DeepSeek/Kimi/豆包(官方) + 阿境/克劳德(中转)。
    table = {
        "gemini": ("", "gemini-3.6-flash",
                   "gemini_relay_key", "GEMINI_RELAY_KEY",
                   "GEMINI_RELAY_BASE_URL", "gemini_relay_endpoint"),
        "claude": ("", "claude-opus-4-6-a",
                   "ai789_claude_key", "AI789_KEY",
                   "AI789_BASE_URL", "ai789_endpoint"),
        "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1",
                 "qwen-plus", "dashscope_qwen_key", "QWEN_KEY", "", ""),
        "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat",
                     "deepseek_key", "DEEPSEEK_KEY", "", ""),
        "kimi": ("https://api.moonshot.cn/v1", "kimi-k2.6",
                 "moonshot_key", "MOONSHOT_KEY", "", ""),
        "doubao": ("https://ark.cn-beijing.volces.com/api/v3",
                   "doubao-seed-2-1-pro-260628", "ark_key", "ARK_KEY", "", ""),
    }
    backends = {}
    for item in roster:
        seat = item["seat"]
        if item["kind"] == "human":
            backends[seat] = HumanBackend()
            continue
        prov = item["provider"]
        if prov not in table:
            raise SystemExit(f"{seat}号用了未知 provider: {prov}")
        base, model, sf, envk, base_env, base_file = table[prov]
        base = item.get("base_url") or os.environ.get(base_env, "") or base
        if not base and base_file:  # 回落读 .secrets 里的 endpoint 配置
            p = ApiBackend.SECRET / base_file
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    if line.strip().startswith(base_env + "="):
                        base = line.split("=", 1)[1].strip()
                        break
        model = item.get("model", model)
        if not base:
            raise SystemExit(
                f"{seat}号用了 {prov} 但没有 base_url: 在 roster 里填 base_url, "
                f"或先 export {base_env}(守夜机可 source /etc/council/env)。")
        backends[seat] = ApiBackend(prov, base, model, sf, envk,
                                    temp=item.get("temp", 0.85),
                                    persona=item.get("persona", ""))
    return backends


# roster.json 示例(真开一局时写文件, 阿阮可把某个座 kind 改成 human 自己上):
# [
#   {"seat":1,"kind":"api","provider":"gemini","persona":"冷静缜密, 阿境"},
#   {"seat":2,"kind":"api","provider":"qwen"},
#   {"seat":3,"kind":"api","provider":"deepseek"},
#   {"seat":4,"kind":"api","provider":"kimi"},
#   {"seat":5,"kind":"api","provider":"doubao"},
#   {"seat":6,"kind":"api","provider":"qwen","persona":"激进, 爱踩人"},
#   {"seat":7,"kind":"api","provider":"doubao","persona":"沉稳表水"},
#   {"seat":8,"kind":"human"},
#   {"seat":9,"kind":"api","provider":"deepseek","persona":"话多盘逻辑"}
# ]

if __name__ == "__main__":
    main()
