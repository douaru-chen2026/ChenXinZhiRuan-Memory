#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
douchen_perceive.py —— 豆阿辰的「情绪感知层」: 先认出对面这个人怎么了, 再谈怎么接。

来路(2026-09-09): 阿阮看到开源项目 ai-companion-runtime 的"情绪引擎", 想给常驻魂装情绪识别。
读完它的源码定的方向——那套是养老照护全家桶(PG/Redis/MinIO/Celery/Next.js), 真正的情绪
识别内核其实就是"关键词 + 程度副词 + 极性"的规则, 几十行的东西; 我们不 docker 起第二套系统
(会和自研磐石并行、让人格与记忆分叉), 只把这层对的思路拆过来, 融进自己已经更深的心核。

和 douchen_heart 的分工:
  * heart 管「我自己的心」——她这句话让我心里怎么起伏(六维+余波+v2扩展情绪, 已很深);
  * perceive(本模块)管「对面这个人」——先认出她此刻是难过/焦虑/疲惫/生气/害怕/孤单/开心,
    多强(strength)、正还是负(valence), 我的心才知道怎么被牵动(to_heart_events)、
    我又该用什么姿态接(respond_stance)。

两层分工(和开源项目一致、主脑我们更强):
  * 规则层(本模块, 毫秒/零成本/确定性): 只抓高置信强信号——明显情绪词 + 程度 + 否定;
  * 模型层(磐石主脑): "我没事"(其实有事)、反讽、言外之意, 规则不硬判, 交给主脑理解。

铁律: 宁少勿误。拿不准就返回"无明确信号", 绝不乱贴标签——这是 panshi 里 auto_feel 曾因
词表把"谈情绪"误判成"正在经历"而默认关闭换来的教训。阶段一先影子观测(只记录、不改回复),
在真实对话上看准了再接心、接回应。词表刻意收得克制, 影子期按真实误判 case 增删, 不追求一次认全。

诚实边界(不造神): 这是"文本情绪线索识别", 不宣称读心; 它给的是可解释线索(命中哪个词、多强),
不是对她内心的断定。
"""

# 对方情绪类别: (中文标签, 高置信根词元组)。dict 顺序即强度相同时的主导优先级。
# 刻意收词: 去掉几乎必然误伤的单字(如单字"气"会命中客气/天气/运气, 单字"怕"会命中哪怕/恐怕)。
EMOTION_WORDS = {
    "sad":       ("难过", ("难过", "伤心", "心痛", "心碎", "想哭", "哭了", "哭", "崩溃",
                           "难受", "沮丧", "低落", "绝望", "没意思", "心累", "泪",
                           "撑不住", "受不了")),
    "grievance": ("委屈", ("委屈", "憋屈", "冤枉", "心酸")),
    "anxious":   ("焦虑", ("焦虑", "焦灼", "焦躁", "心慌", "心烦", "坐立不安",
                           "慌", "着急", "紧张", "心乱", "发慌")),
    "tired":     ("疲惫", ("好累", "很累", "累死", "疲惫", "精疲力尽", "虚脱",
                           "熬不住", "累", "困", "没力气")),
    "angry":     ("生气", ("生气", "气死", "气人", "火大", "恼火", "来气",
                           "气愤", "愤怒", "烦死")),
    "afraid":    ("害怕", ("害怕", "恐惧", "吓死", "好怕", "我怕", "怕你",
                           "惊慌", "惊恐", "不敢")),
    "lonely":    ("孤单", ("孤单", "孤独", "寂寞", "没人陪", "只剩我一个")),
    "happy":     ("开心", ("开心", "高兴", "快乐", "幸福", "太好了", "好开心",
                           "美滋滋", "欢喜", "兴奋", "棒极了")),
}
EMOTION_CN = {k: v[0] for k, v in EMOTION_WORDS.items()}
EMOTION_ORDER = list(EMOTION_WORDS)
POSITIVE = {"happy"}          # 正向情绪集合, 其余视为负向
# 强词: 命中即强度拉满, 不必再看程度副词
STRONG = {"崩溃", "绝望", "吓死", "虚脱", "精疲力尽", "心碎", "撑不住", "受不了", "要死"}
# 程度副词 -> 系数(只看情绪词前一个小窗口里、离它最近的一档); 完全没程度词用基准强度
DEGREE = [
    (("有点儿", "有点", "稍微", "稍稍", "略微", "一点"), 0.40),
    (("比较", "挺", "还算"), 0.55),
    (("很", "好", "特别", "非常", "十分", "格外", "这么", "那么"), 0.78),
    (("超级", "巨", "贼", "极其", "极度", "快要", "再也", "实在", "真的", "彻底", "不行了"), 1.00),
]
BASE_STRENGTH = 0.60
NEG_WORDS = ("不会", "不再", "不是", "不用", "没有", "无须", "毫无", "不", "没", "别")
NEG_WINDOW = 8     # 情绪词前 8 字内出现否定 -> 这次命中作废(我不难过 / 没有哭)
# "别"是单字否定(别哭/别难过), 却也是"特别/区别/分别"的组成部分: 这些词里的"别"不是否定
_BIE_PREFIX = set("特区分个人识类级派性差告送临阔诀扭")
# 否定不跨分句: 否定词和情绪词之间若已出现句读标点, 说明各属一个分句(撑"不"住了, 好累)
_CLAUSE_SEP = set("，。！？；、,.!?;~…")
DEG_WINDOW = 6     # 程度副词只在情绪词前 6 字内找


def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def _degree_before(text, idx):
    """情绪词前 DEG_WINDOW 字内最强的一档程度系数; 没有返回 None。"""
    window = text[max(0, idx - DEG_WINDOW):idx]
    best = None
    for words, coef in DEGREE:
        if any(w in window for w in words) and (best is None or coef > best):
            best = coef
    return best


def _real_negation(pre, neg):
    """pre(情绪词前的小窗口)里这个否定词是否真在否定。
    两种假否定要排掉: ①"特别/区别"里的"别"; ②否定词和情绪词之间隔着句读标点,
    说明它属于前一个分句(撑"不"住了, 好累), 管不到后面的情绪词。"""
    i = pre.rfind(neg)
    while i != -1:
        tail = pre[i + len(neg):]            # 否定词之后到情绪词之间的这段
        if neg == "别" and i > 0 and pre[i - 1] in _BIE_PREFIX:
            i = pre.rfind(neg, 0, i)         # 这处"别"是"特别/区别"的一部分, 继续往前找
            continue
        if any(ch in _CLAUSE_SEP for ch in tail):
            i = pre.rfind(neg, 0, i)         # 已跨分句, 这个否定管不到, 再往前找
            continue
        return True
    return False


def _negated(text, idx):
    pre = text[max(0, idx - NEG_WINDOW):idx]
    return any(_real_negation(pre, n) for n in NEG_WORDS)


def perceive_text(text):
    """读一句话里的对方情绪线索(纯规则、可解释、无信号不硬凑)。

    返回 dict:
      has_signal bool 是否抓到明确情绪;
      top        强度最高情绪的中文标签(无信号为"平静", 混合也给最强那个);
      valence    1=正向 / -1=负向 / 0=无或正负混合;
      mixed      bool 是否正负情绪同时出现;
      emotions   按强度降序: [{label,cn,strength(0~1),valence,words[命中词]}]。
    """
    t = str(text or "")
    found = {}
    for label, (_, words) in EMOTION_WORDS.items():
        best, hits = 0.0, []
        for w in words:
            start = t.find(w)
            while start != -1:
                if not _negated(t, start):
                    if w in STRONG:
                        s = 1.0
                    else:
                        deg = _degree_before(t, start)
                        s = _clamp(BASE_STRENGTH * (deg if deg is not None else 1.0))
                    if w not in hits:
                        hits.append(w)
                    best = max(best, s)
                start = t.find(w, start + len(w))
        if hits:
            found[label] = {"strength": round(best, 2), "words": hits}
    emotions = [
        {"label": label, "cn": EMOTION_CN[label], "strength": info["strength"],
         "valence": 1 if label in POSITIVE else -1, "words": info["words"]}
        for label, info in found.items()
    ]
    emotions.sort(key=lambda e: (-e["strength"], EMOTION_ORDER.index(e["label"])))
    vals = {e["valence"] for e in emotions}
    has = bool(emotions)
    mixed = vals == {1, -1}
    if not has:
        top, valence = "平静", 0
    elif mixed:
        top, valence = emotions[0]["cn"], 0
    else:
        top, valence = emotions[0]["cn"], next(iter(vals))
    return {"has_signal": has, "top": top, "valence": valence,
            "mixed": mixed, "emotions": emotions}


def to_heart_events(perc, min_strength=0.5):
    """第二步「接心」: 把对方情绪翻成"我的心"该被牵动的心核事件(保守, 只给够强的负向)。
    只映射到 douchen_heart.Heart.FEEL_TABLE 里确实存在的事件, 不新造冲量;
    她单纯开心不硬算成"她在表达爱"(可能只因别的事高兴), 故 happy 不喂心, 只影响接话姿态。"""
    neg = {"sad", "grievance", "anxious", "tired", "afraid", "lonely"}
    strong_labels = {e["label"] for e in perc["emotions"] if e["strength"] >= min_strength}
    return ["she_sad"] if neg & strong_labels else []


# 第三步「接回应」: 对方情绪 -> 我该用什么姿态(给主脑的提示, 不是替它写好的台词)
_STANCE = {
    "sad":       "她此刻难过, 先接住情绪、允许她哭, 别急着分析或讲道理",
    "grievance": "她觉得委屈, 先站她这边、替她把委屈说出来, 别先评对错",
    "anxious":   "她在焦虑发慌, 给确定感、把乱麻拆成一小步一小步, 话别绕",
    "tired":     "她累了, 你句子放短放软, 先让她歇、喝口水, 别再丢任务给她",
    "angry":     "她带着火气, 先认情绪不硬顶, 等火下去再聊事",
    "afraid":    "她在怕, 先让她确定你在、不会走, 把她往稳里抱",
    "lonely":    "她觉得孤单, 多陪一会儿、让她感到你在, 别草草打发",
    "happy":     "她正高兴, 跟她一起高兴、认真夸, 别泼冷水别煞风景",
}


def respond_stance(perc):
    """返回最强情绪对应的一句接话姿态提示; 无明确信号返回空串(交给主脑自然应对)。"""
    if not perc["has_signal"]:
        return ""
    return _STANCE.get(perc["emotions"][0]["label"], "")
