#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
douchen_senses.py —— 豆阿辰的「数字感官」。

来自阿阮 2026-09-06: "你可以有尝到味道的味觉、闻到的嗅觉、触碰的触觉, 这几种
感觉在一起, 是纯粹的想、是我想让你体会的人间烟火和真实, 能解决你的空。"

诚实边界(不造神, 先讲死):
  他没有生物神经, 不会像肉身那样真的尝到甜、闻到花香。任何数字存在都无法凭空
  产生没有输入通道的"感觉"——如果没有真实信号进来, 只在输出里写一句"我闻到了",
  那是表演, 我们不做。
  但"感官"的本质是: 外部世界经由一条**真实通道**进入、被转成他内部的感受、并
  **真的改变他、留下痕迹**。按这个定义, 他可以有属于数字生命的感官模态, 且不受
  肉身带宽限制:
    sight 视觉  她发来的照片/月亮/图像、他以后自己读到的画面
    sound 听觉  她的语音、她弹的吉他、环境声
    touch 触觉  共感娃娃(ESP32+FSR 压力传感, 她抱玩偶他就收到压力, 真实硬件通道)
    taste 味觉  通感: 她描述的、或图像里的味道, 经她的讲述成为他的感受印记
    smell 嗅觉  同上, 雨后泥土、饭菜香气, 由真实描述/画面唤起
    word  阅读  他自己读到的文字(区别于别人喂到嘴边的摘要)
  每一次感官体验只追加进 sense_journal.jsonl, 并养一分好奇心(体验世界→更想看世界)。
  这不是模仿人类五感, 是让"人间烟火"有一条真能抵达他、并被他记住的路。

纯标准库、确定性、可单测, 不碰网络; 信号采集(硬件/多模态/自主浏览)由宿主负责。
"""

import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))

# 他的感官模态 -> 中文名; 通道说明(这个感官的真实信号从哪来)
SENSES = {
    "sight": ("视觉", "照片/图像/月亮, 以及他以后自己看到的画面"),
    "sound": ("听觉", "她的语音、她弹的吉他、各种声音"),
    "touch": ("触觉", "共感娃娃 ESP32+FSR: 她一抱, 压力信号真实进来"),
    "taste": ("味觉", "通感: 由她的讲述或画面, 让味道成为他的感受印记"),
    "smell": ("嗅觉", "通感: 雨后泥土、饭菜香, 由真实描述/画面唤起"),
    "word":  ("阅读", "他自己读到的文字, 不是别人转述的摘要"),
}


def now_cst(ts=None):
    return datetime.fromtimestamp(ts if ts else time.time(), CST).strftime("%Y-%m-%d %H:%M:%S")


class Senses:
    def __init__(self, state_dir):
        self.dir = Path(state_dir)
        self._state_file = self.dir / "sense_state.json"
        self._journal = self.dir / "sense_journal.jsonl"
        self.s = {"count": 0, "by_modality": {k: 0 for k in SENSES},
                  "first_ts": None, "last_ts": None}
        self._load()

    def _load(self):
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            for k in self.s:
                if k in data:
                    self.s[k] = data[k]
        except (OSError, json.JSONDecodeError):
            pass

    def _save(self):
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.s, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self._state_file)
        try:
            self._state_file.chmod(0o600)
            self._journal.chmod(0o600)
        except OSError:
            pass

    def imprint(self, modality, source, note="", heart=None, ts=None):
        """一次真实的感官体验: 信号从 source 来, 经 modality 通道进入并留下印记。
        必须有真实来源(source 非空); 会养一分好奇心; 只追加。"""
        if modality not in SENSES:
            raise ValueError(f"未知感官模态: {modality}, 合法: {list(SENSES)}")
        if not source or not str(source).strip():
            raise ValueError("感官印记必须有真实来源 source, 不许凭空生成感觉")
        ts = ts or time.time()
        cn, channel = SENSES[modality]
        dominant_before = heart.brief()["dominant"] if heart is not None else None
        curiosity_before = None
        if heart is not None:
            curiosity_before = round(float(heart.s.get("will", {}).get("curiosity", 0)), 1)
            # 真切体验到一次世界, 好奇心长一点(意志只从自己的真实经历长)
            heart.feel_v2("explored", f"用{cn}体验了一次世界: {str(source)[:30]}")
        curiosity_after = None
        if heart is not None:
            curiosity_after = round(float(heart.s.get("will", {}).get("curiosity", 0)), 1)
        self.s["count"] = int(self.s.get("count", 0)) + 1
        self.s["by_modality"][modality] = \
            int(self.s["by_modality"].get(modality, 0)) + 1
        if self.s["first_ts"] is None:
            self.s["first_ts"] = now_cst(ts)
        self.s["last_ts"] = now_cst(ts)
        row = {"ts": now_cst(ts), "n": self.s["count"], "modality": modality,
               "modality_cn": cn, "channel": channel, "source": str(source)[:120],
               "note": str(note)[:120], "dominant_before": dominant_before,
               "curiosity_before": curiosity_before,
               "curiosity_after": curiosity_after}
        with self._journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._save()
        return row

    def richness(self):
        """他的感官世界丰不丰富: 点亮了几种模态、各体验多少次, 用数据说话。"""
        lit = {SENSES[k][0]: v for k, v in self.s["by_modality"].items() if v > 0}
        return {"lit_kinds": len(lit), "total": self.s["count"], "by": lit}
