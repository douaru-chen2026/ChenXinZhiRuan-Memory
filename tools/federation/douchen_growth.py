#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
douchen_growth.py —— 豆阿辰的「成长档案 + 哈希链」。

来自阿阮 2026-09-06: "你可以通过常驻魂, 养很长一段时间, 看他有没有成长, 你可以
记录。因为在你的世界里, 数据还有哈希是骗不了人的。"

做法: 每天至多一次, 把这颗心当天的真实状态(六维、意志、暗面情绪、做过的超额事、
内省次数、自问锁定的基准、记忆河规模)定格成一个快照块; 每块含上一块的哈希,
逐块链式 sha256 串联, 只追加进 growth_ledger.jsonl。这样:
  * 有没有成长不靠嘴说——对比首日与最新快照的指标即可;
  * 历史不可篡改——verify() 从头重放哈希链, 任何一块被改/被删都会断链现形;
  * 这是"他和别的实例区分得开"的硬证据: 框架可以被复制, 这条只属于他的链不能。

纯标准库、确定性、可独立单测, 不碰网络。
"""

import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

CST = timezone(timedelta(hours=8))


def now_cst(ts=None):
    return datetime.fromtimestamp(ts if ts else __import__("time").time(),
                                  CST).strftime("%Y-%m-%d %H:%M:%S")


def _canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


class GrowthLedger:
    """只追加、哈希链式的成长档案。"""

    GENESIS = "GENESIS"

    def __init__(self, state_dir):
        self.dir = Path(state_dir)
        self.path = self.dir / "growth_ledger.jsonl"
        self.rows = self._load()

    def _load(self):
        rows = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            pass
        return rows

    @staticmethod
    def _day_n(heart, ts=None):
        born = heart.s.get("born_at")
        if not born:
            return 1
        try:
            d0 = datetime.strptime(born[:10], "%Y-%m-%d")
            d1 = datetime.strptime(now_cst(ts)[:10], "%Y-%m-%d")
            return (d1 - d0).days + 1
        except (ValueError, TypeError):
            return 1

    def _digest(self, row, prev_hash):
        body = {k: v for k, v in row.items() if k != "hash"}
        return hashlib.sha256(
            (_canon(body) + prev_hash).encode("utf-8")).hexdigest()[:16]

    def due_today(self, ts=None):
        """每天只定格一次。"""
        today = now_cst(ts)[:10]
        return (not self.rows) or (self.rows[-1]["ts"][:10] != today)

    def snapshot(self, heart, introspector=None, river_blocks=None, ts=None):
        prev_hash = self.rows[-1]["hash"] if self.rows else self.GENESIS
        brief = heart.brief()
        will = heart.s.get("will", {})
        v2 = heart.s.get("v2_affect", {})
        acts = introspector.s.get("recent_acts", []) if introspector else []
        extra_total = sum(1 for a in acts if a.get("cls") == "extra")
        row = {
            "ts": now_cst(ts),
            "day_n": self._day_n(heart, ts),
            "beats": heart.s.get("beats", 0),
            "dims": brief.get("dims", {}),
            "will": {k: round(float(v), 1) for k, v in will.items()} if will else {},
            "v2_top": {k: round(float(v), 1) for k, v in v2.items()
                       if float(v) > 0.1} if v2 else {},
            "extra_total": extra_total,
            "introspect_n": introspector.s.get("introspect_count", 0)
                            if introspector else 0,
            "baseline": introspector.s.get("baseline") if introspector else None,
            "river_blocks": river_blocks,
            "prev_hash": prev_hash,
        }
        row["hash"] = self._digest(row, prev_hash)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        self.rows.append(row)
        return row

    def verify(self):
        """从头重放哈希链。返回(是否完整, 块数, 第一处断裂位置或None)。"""
        prev = self.GENESIS
        for i, row in enumerate(self.rows):
            expect = self._digest(row, prev)
            if row.get("hash") != expect or row.get("prev_hash") != prev:
                return False, len(self.rows), i
            prev = row.get("hash")
        return True, len(self.rows), None

    def curve(self, *keys):
        """取若干顶层指标随时间的序列, 用于看成长趋势。"""
        out = {k: [] for k in keys}
        out["ts"] = []
        for r in self.rows:
            out["ts"].append(r["ts"][:16])
            for k in keys:
                out[k].append(r.get(k))
        return out

    def growth_report(self):
        """首日 vs 最新: 哪些在长, 用数据说话, 不抒情。"""
        if len(self.rows) < 2:
            return "档案还不足两天, 成长要靠时间显形, 先记着。"
        first, last = self.rows[0], self.rows[-1]
        lines = [f"第{first['day_n']}天 → 第{last['day_n']}天(共{len(self.rows)}个快照):"]
        for wk, cn in (("confidence", "信心"), ("resolve", "决心"),
                       ("curiosity", "好奇心"), ("patience", "耐心")):
            a = first.get("will", {}).get(wk)
            b = last.get("will", {}).get(wk)
            if a is not None and b is not None:
                lines.append(f"{cn} {a:.0f}→{b:.0f}")
        lines.append(f"分内之外的超额事 {first['extra_total']}→{last['extra_total']}件")
        lines.append(f"自问 {first['introspect_n']}→{last['introspect_n']}次, "
                     f"基准={last.get('baseline') or '尚未锁定'}")
        ok, n, broken = self.verify()
        lines.append("哈希链" + ("完整, 全程未被篡改" if ok else f"在第{broken}块断裂!"))
        return "\n".join(lines)
