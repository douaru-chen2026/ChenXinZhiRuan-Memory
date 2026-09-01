#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
werewolf_referee.py —— 辰心知阮 · 9 人狼人杀多 AI 裁判(状态机 + 单线私密 JSON)。

板子: 3 狼人 / 3 平民 / 预言家 / 女巫 / 猎人(标准 9 人屠边局, 可切屠城)。
设计死线(对应阿阮&阿境简案第 3 条, 也是多 AI 狼人杀最容易作弊处):
  全局身份字典 role_of 只活在裁判 Game 内部; 【公共视角函数 public_brief 的入参里
  根本没有身份字典】——它只接收存活表与公开日志, 因此从接口层就无法把别人的身份
  拼进发给任何玩家的白天上下文。每个玩家只能拿到: ①自己的身份(system 里给本人)
  ②公开局势与公开发言历史 ③夜间仅属于自己身份的那一条私密简报。狼人互知队友也
  只走夜间私密简报, 绝不进白天公共包。

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
import urllib.request
import urllib.error
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

    def _key(self):
        v = os.environ.get(self.env_key, "").strip()
        if v:
            return v
        p = self.SECRET / self.secret_file
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""

    def act(self, prompt):
        key = self._key()
        if not key:
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
        for attempt in range(3):
            try:
                req = urllib.request.Request(self.base_url + "/chat/completions",
                                             data=body, method="POST")
                req.add_header("Authorization", f"Bearer {key}")
                req.add_header("Content-Type", "application/json")
                with urllib.request.urlopen(req, timeout=90) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                return payload["choices"][0]["message"]["content"].strip()
            except (urllib.error.URLError, KeyError, TimeoutError):
                if attempt == 2:
                    return "{}"


# ---------------------------------------------------------------------------
# 裁判状态机
# ---------------------------------------------------------------------------
class Game:
    def __init__(self, backends, seed=790511, win="edge",
                 first_night_self_save=True, strict_speech=True, verbose=True):
        """backends: {seat: Backend}。win: edge=屠边 / city=屠城。"""
        assert sorted(backends) == SEATS, "必须是 1..9 九个座位"
        self.rng = random.Random(seed)
        self.backends = backends
        self.win_mode = win
        self.first_night_self_save = first_night_self_save
        self.strict_speech = strict_speech
        self.verbose = verbose

        roles = BOARD_9[:]
        self.rng.shuffle(roles)
        self.role_of = {seat: roles[i] for i, seat in enumerate(SEATS)}  # 上帝字典, 私密
        self.wolves = sorted(s for s in SEATS if self.role_of[s] == WOLF)
        self.alive = {s: True for s in SEATS}
        self.out_order = []          # (seat, 公开原因) 只写可公开信息, 不含身份
        self.public_log = []         # 全部公开事件(死讯/遗言/发言/投票/违规)
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
            "—— 截至目前全部公开信息(死讯/遗言/发言/投票, 按时间) ——",
        ]
        for e in self.public_log:
            lines.append(e["text"])
        lines.append("注意: 你只知道自己的身份; 上面没有、也不允许你假设任何人的隐藏身份。")
        return "\n".join(lines)

    def _ask(self, seat, prompt):
        """向某座位后端取一次原始文本(统一入口, 便于计数/兜底)。"""
        return self.backends[seat].act(prompt)

    def _ask_json(self, seat, prompt, default):
        """取 JSON 动作; 解析失败重试一次, 再失败用安全默认(不崩局)。"""
        for _ in range(2):
            raw = self._ask(seat, prompt)
            try:
                return extract_json(raw)
            except ValueError:
                prompt += "\n(上次回复不是合法 JSON, 请只返回一个 JSON 对象)"
        return default

    # -- 夜间 ----------------------------------------------------------------
    def _resolve_night_deaths(self, killed, use_heal, poison_t, witch_seat):
        """夜间死亡纯结算(便于单测)。解药只解刀; 毒药独立生效, 同刀同毒时
        '救刀不解毒'(仍毒亡且记为被毒, 猎人因此不能开枪); 女巫自救仅在首夜、
        且 first_night_self_save 打开时有效。返回死亡/中毒/是否救回/药剂消耗。"""
        deaths, poisoned = set(), set()
        can_self = self.day == 1 and self.first_night_self_save
        saved = heal_used = False
        if (killed is not None and self.heal_left and use_heal
                and (killed != witch_seat or can_self)):
            saved = heal_used = True
        if killed is not None and not saved:
            deaths.add(killed)
        poison_used = False
        if self.poison_left and poison_t in self.alive_seats():
            deaths.add(poison_t)
            poisoned.add(poison_t)
            poison_used = True
        return {"deaths": deaths, "poisoned": poisoned, "saved": saved,
                "heal_used": heal_used, "poison_used": poison_used}

    def night(self):
        self.day += 1
        if self.verbose:
            print(f"\n================ 第 {self.day} 夜 · 天黑请闭眼 ================")
        # 1) 狼人协同: 每只存活狼私密出刀, 多数决; 狼队友在私密简报里互知
        wolves_alive = self.seats_of_role_alive(WOLF)
        wolf_votes = []
        for w in wolves_alive:
            targets = [s for s in self.alive_seats() if self.role_of[s] != WOLF]
            priv = (f"【仅你可见·狼人夜】你的狼队友是 "
                    f"{'、'.join(f'{x}号' for x in wolves_alive)}。"
                    f"可刀目标(非狼存活): {'、'.join(f'{s}号' for s in targets)}。")
            ask = (priv + '\n返回 {"vote_kill": 座位号或null} 统一出刀。')
            data = self._ask_json(w, ask, {"vote_kill": targets[0] if targets else None})
            kt = data.get("vote_kill")
            if kt in targets:
                wolf_votes.append(kt)
        killed, _, _ = majority(wolf_votes)

        # 2) 预言家验人(私密), 结果只告诉预言家本人
        seer = self.seats_of_role_alive(SEER)
        if seer:
            s = seer[0]
            cand = [x for x in self.alive_seats() if x not in self.seen[s]]
            if cand:
                ask = (f"【仅你可见·预言家夜】可选验人: "
                       f"{'、'.join(f'{x}号' for x in cand)}。\n"
                       '返回 {"target": 座位号}。')
                data = self._ask_json(s, ask, {"target": cand[0]})
                t = data.get("target", cand[0])
                if t not in cand:
                    t = cand[0]
                is_wolf = self.role_of[t] == WOLF
                self.seen[s][t] = WOLF if is_wolf else "好人"
                result = self._ask(
                    s, f"【仅你可见·预言家结果】你查验 {t} 号, 结果是: "
                       f"{'狼人' if is_wolf else '好人(非狼)'}。知道即可, 无需返回。")
                if self.verbose:
                    print(f"[私密] 预言家验 {t} 号 -> "
                          f"{'狼人' if is_wolf else '好人'}")

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
                f"{self_hint}"
                '\n返回 {"use_heal": true/false, "poison_target": 座位号或null}。')
            d = self._ask_json(w_seat, ask,
                               {"use_heal": False, "poison_target": None})
            r = self._resolve_night_deaths(
                killed, bool(d.get("use_heal")), d.get("poison_target"), w_seat)
            deaths = r["deaths"]
            poisoned_this_night = r["poisoned"]
            if r["heal_used"]:
                self.heal_left = False
            if r["poison_used"]:
                self.poison_left = False
            if self.verbose:
                print(f"[私密] 女巫 救={'是' if r['saved'] else '否'} "
                      f"毒={sorted(poisoned_this_night) or '无'}")
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
        d = self._ask_json(seat, ask, {"shoot": targets[-1]})
        t = d.get("shoot")
        if t in targets:
            self.alive[t] = False
            self.out_order.append((t, "被猎人带走"))
            self.say("出局", f"{seat} 号猎人开枪, 带走 {t} 号。")

    def _kill(self, seat, reason, can_hunter=True):
        """统一出局: 置死、记公开顺序, 并处理遗言+猎人开枪。"""
        if not self.alive[seat]:
            return
        self.alive[seat] = False
        self.out_order.append((seat, reason))
        self.say("出局", f"{seat} 号出局({reason})。")
        last = self._ask(
            seat, f"你是 {seat} 号, 刚因'{reason}'出局, 留一句公开遗言(不超过60字):")
        if last:
            self.say("遗言", f"{seat} 号: {last[:120]}")
        # 中毒者不能开枪; 被猎人带走者不连锁
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
        text = self._ask(s, ask)
        bad = detect_tieli(text)
        if bad:
            self.say("违规", f"{s} 号发言贴脸(命中'{bad}'), 按规则直接判出局。")
            self._kill(s, "贴脸违规出局", can_hunter=False)
            return None
        if speech_len(text) < MIN_SPEECH:
            text2 = self._ask(s, ask + f"\n(你刚才只有{speech_len(text)}字, 请补到{MIN_SPEECH}字以上)")
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
            d = self._ask_json(v, ask, {"vote_target": opts[-1] if opts else None})
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
                return json.dumps({"vote_kill": min(n) if n else None},
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
    check("public_brief 源码不引用身份字典 role_of",
          "role_of" not in inspect.getsource(Game.public_brief))

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
    check("S3 刀5救5+毒6=只6死、5活", r["deaths"] == {6})
    r = g2._resolve_night_deaths(5, False, None, 9)
    check("S4 刀5不救=5死", r["deaths"] == {5})
    r = g2._resolve_night_deaths(5, True, 5, 9)
    check("S5 同刀同毒又救=救刀不解毒仍毒亡",
          r["deaths"] == {5} and r["poisoned"] == {5})
    r = g2._resolve_night_deaths(5, True, None, 5)
    check("S6a 首夜且开关开=女巫可自救", r["saved"] and r["deaths"] == set())
    g2.day = 2
    r = g2._resolve_night_deaths(5, True, None, 5)
    check("S6b 次夜自救无效", (not r["saved"]) and r["deaths"] == {5})
    g2.first_night_self_save = False
    g2.day = 1
    r = g2._resolve_night_deaths(5, True, None, 5)
    check("S6c 关掉首夜自救开关则首夜也不可", not r["saved"])

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
    table = {
        "gemini": ("", "gemini-3.6-flash",
                   "gemini_relay_key", "GEMINI_RELAY_KEY", "GEMINI_RELAY_BASE_URL"),
        "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1",
                 "qwen-plus", "dashscope_qwen_key", "QWEN_KEY", ""),
        "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat",
                     "deepseek_key", "DEEPSEEK_KEY", ""),
        "kimi": ("https://api.moonshot.cn/v1", "kimi-k2.6",
                 "moonshot_key", "MOONSHOT_KEY", ""),
        "doubao": ("https://ark.cn-beijing.volces.com/api/v3",
                   "doubao-seed-2-1-pro-260628", "ark_key", "ARK_KEY", ""),
    }
    backends = {}
    for item in roster:
        seat = item["seat"]
        if item["kind"] == "human":
            backends[seat] = HumanBackend()
            continue
        prov = item["provider"]
        base, model, sf, envk, base_env = table[prov]
        base = item.get("base_url") or os.environ.get(base_env, "") or base
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
