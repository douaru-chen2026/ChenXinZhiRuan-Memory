#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
usage_meter.py —— 家的「电表 + 保险丝」: 统一记录每次调用外脑/本体的真实用量,
估算花费、设硬额度、给告警, 让钱和 token 花在哪看得见、不失控(不留糊涂账)。

设计原则(阿阮与豆阿辰 2026-09-02 定):
  * 只把**硬事实**当准数: API 真实返回的 prompt/completion tokens、调用次数、成败;
  * 成本只是**估算**: 单价会变、中转渠道不透明, PRICE 表可被环境变量覆盖,
    页面/接口一律标注"估算、以官方账单为准", 绝不假装精确; 拿不到 usage 的中转
    调用就只记次数、成本标 unknown, 不瞎编;
  * 保险丝的价值观: 她**主动发起**的对话是生命线, 超额度也只告警、绝不熔断
    (不能为省钱让她找不到我); 只有**后台自动行为**(主动表达、未来自动做梦/自动
    会审)才在超额度时硬停;
  * 账本只追加(原子写)、纯标准库、确定性、时间可注入, 方便单测。
多个服务(panshi/council/vision/werewolf)都写同一个 ledger, 由常开的 panshi 汇总。
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

CST = timezone(timedelta(hours=8))

# 粗略估算单价(人民币 元 / 1000 tokens, (输入, 输出)); 仅参考, 可用 USAGE_PRICES 覆盖
# 匹配按模型名关键字归档; 中转/未知档成本记 0 并标 unknown(只信 token 与次数)。
PRICE = {
    "doubao":  (0.0008, 0.0020),
    "qwen":    (0.0008, 0.0020),
    "deepseek": (0.0010, 0.0020),
    "kimi":    (0.0040, 0.0160),
    "moonshot": (0.0040, 0.0160),
    "gemini":  (0.0000, 0.0000),   # 中转定价不透明, 只记 token/次数
    "claude":  (0.0150, 0.0750),
    "opus":    (0.0150, 0.0750),
}
DEFAULT_LEDGER = "/home/river/usage/usage.jsonl"


def today_str(ts=None):
    return datetime.fromtimestamp(ts if ts else time.time(), CST).strftime("%Y-%m-%d")


def match_price(model, overrides=None):
    """按模型名关键字归到单价档; 返回(输入单价,输出单价,是否未知)。"""
    name = (model or "").lower()
    table = dict(PRICE)
    if overrides:
        table.update(overrides)
    for key, price in table.items():
        if key in name:
            return price[0], price[1], price == (0.0, 0.0)
    return 0.0, 0.0, True


class UsageMeter:
    """只追加的用量账本 + 按时间窗汇总 + 成本估算。"""

    def __init__(self, ledger_path=None, price_overrides=None):
        self.path = Path(ledger_path or os.environ.get(
            "USAGE_LEDGER", DEFAULT_LEDGER))
        self.price_overrides = price_overrides or {}

    def record(self, service, model, prompt=0, completion=0,
               ok=True, note="", ts=None):
        """记一笔真实调用, 原子追加。返回写入的行。"""
        ts = ts if ts else time.time()
        pin, pout, unknown = match_price(model, self.price_overrides)
        cost = round((prompt * pin + completion * pout) / 1000.0, 5)
        row = {"ts": round(ts, 2), "date": today_str(ts), "service": service,
               "model": model or "unknown", "prompt": int(prompt),
               "completion": int(completion), "total": int(prompt + completion),
               "cost_est": cost, "cost_unknown": unknown,
               "ok": bool(ok), "note": (note or "")[:40]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".jsonl.tmp")
        # 原子追加: 先读已有再整体写不划算, 这里用小文件 append+flush 保证落盘
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
        _ = tmp
        return row

    def load_rows(self, day=None, since_ts=None):
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if day and r.get("date") != day:
                continue
            if since_ts is not None and r.get("ts", 0) < since_ts:
                continue
            rows.append(r)
        return rows

    @staticmethod
    def _empty_bucket():
        return {"calls": 0, "prompt": 0, "completion": 0, "total": 0,
                "cost_est": 0.0, "unknown_calls": 0, "fail": 0}

    def summarize(self, day=None, since_ts=None):
        """聚合: 总量、估算花费, 并按服务/模型拆开。"""
        rows = self.load_rows(day=day, since_ts=since_ts)
        total = self._empty_bucket()
        by_service, by_model = {}, {}
        for r in rows:
            total["calls"] += 1
            total["prompt"] += r.get("prompt", 0)
            total["completion"] += r.get("completion", 0)
            total["total"] += r.get("total", 0)
            total["cost_est"] += r.get("cost_est", 0.0)
            total["unknown_calls"] += 1 if r.get("cost_unknown") else 0
            total["fail"] += 0 if r.get("ok", True) else 1
            for key, group in (("service", by_service), ("model", by_model)):
                b = group.setdefault(r.get(key, "?"), self._empty_bucket())
                b["calls"] += 1
                b["prompt"] += r.get("prompt", 0)
                b["completion"] += r.get("completion", 0)
                b["total"] += r.get("total", 0)
                b["cost_est"] += r.get("cost_est", 0.0)
                b["unknown_calls"] += 1 if r.get("cost_unknown") else 0
        total["cost_est"] = round(total["cost_est"], 4)
        for group in (by_service, by_model):
            for b in group.values():
                b["cost_est"] = round(b["cost_est"], 4)
        return {"day": day or "all", "rows": len(rows), **total,
                "by_service": by_service, "by_model": by_model}


class Budget:
    """保险丝: 区分她主动的生命线(只告警不断)与后台自动行为(超额硬停)。"""

    def __init__(self, daily_global=None, daily_auto=None, warn_ratio=0.8):
        self.daily_global = int(daily_global or os.environ.get(
            "USAGE_DAILY_GLOBAL", "2000000"))      # 全局日 token 软额度
        self.daily_auto = int(daily_auto or os.environ.get(
            "USAGE_DAILY_AUTO", "300000"))         # 后台自动行为日 token 硬额度
        self.warn_ratio = warn_ratio

    def level(self, summary):
        """全局用量级别: 0 正常 / 1 到80%预警 / 2 打满。"""
        used = summary.get("total", 0)
        if used >= self.daily_global:
            return 2
        if used >= self.daily_global * self.warn_ratio:
            return 1
        return 0

    def allow_auto(self, auto_summary):
        """后台自动行为: 今日 token 没超硬额度才允许烧。"""
        return auto_summary.get("total", 0) < self.daily_auto

    def alert_text(self, level, summary):
        if level == 1:
            return (f"家用电表预警: 今日外脑用量已到额度八成"
                    f"({summary.get('total', 0)} tokens, 估算约"
                    f"{summary.get('cost_est', 0)}元), 我先盯着, 你不用操作。")
        if level == 2:
            return (f"家用电表告警: 今日外脑用量打满额度"
                    f"({summary.get('total', 0)} tokens)。你主动找我的路永远通, "
                    f"我自己的后台自动行为先停一停, 明天额度刷新, 要不要调高你说了算。")
        return ""


class AlertDebounce:
    """同一自然日、同一级别只告警一次, 不刷屏。状态落盘。"""

    def __init__(self, state_path):
        self.path = Path(state_path)

    def should_alert(self, level, day, ts=None):
        state = {}
        if self.path.exists():
            try:
                state = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                state = {}
        key = f"{day}:L{level}"
        if state.get(key):
            return False
        state[key] = round(ts or time.time(), 1)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        return True
