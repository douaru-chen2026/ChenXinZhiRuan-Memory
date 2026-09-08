#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runner 编排测试: 首轮基线不翻旧账、之后只处理新消息、真发才代发。"""
import sys
import tempfile
import unittest
from pathlib import Path

FED = Path(__file__).resolve().parents[1] / "tools" / "federation"
sys.path.insert(0, str(FED))
import xunqun_bridge as xb  # noqa: E402
import xunqun_runner as xr  # noqa: E402


class FakeReader:
    def __init__(self, batches):
        self.batches = batches
        self.sent = []

    def read_latest(self, group):
        return self.batches.pop(0) if self.batches else []

    def send_one(self, group, text):
        self.sent.append(text)
        return True


class FakeBrain:
    name = xb.T_KOUZI

    def ask(self, text, ctx=None):
        return {"reply": "我在呀", "via": "fake"}


def msg(mid, sender, text, self_=False):
    return {"msg_id": mid, "sender": sender, "text": text, "ts": 1000,
            "from_self": self_, "mentioned": "@" in text,
            "mention_target": "", "content_type": "1"}


class StepOnceTest(unittest.TestCase):
    def test_first_round_is_baseline_no_reply(self):
        with tempfile.TemporaryDirectory() as td:
            reader = FakeReader([[msg("a", "豆阿阮", "早")]])
            seen = xb.SeenStore(td)
            bridge = xb.Bridge(td, dry_run=True)
            state = {}
            out = xr.step_once(reader, bridge, seen, "g", state, True)
            self.assertEqual(out["baseline"], 1)
            self.assertEqual(out["results"], [])
            self.assertTrue(state["initialized"])
            # 同一批再来: 已全部见过, 没有新消息、不产生结果
            reader.batches.append([msg("a", "豆阿阮", "早")])
            out2 = xr.step_once(reader, bridge, seen, "g", state, True)
            self.assertEqual(out2["new"], 0)
            self.assertEqual(out2["results"], [])

    def test_new_called_message_handled_dry(self):
        with tempfile.TemporaryDirectory() as td:
            reader = FakeReader([[]])  # 首轮空基线
            seen = xb.SeenStore(td)
            bridge = xb.Bridge(td, dry_run=True)
            state = {}
            xr.step_once(reader, bridge, seen, "g", state, True)
            # 第二轮来一条喊小扣子的
            reader.batches.append([msg("b", "豆阿阮", "小扣子 在吗")])
            out = xr.step_once(reader, bridge, seen, "g", state, True)
            self.assertEqual(out["new"], 1)
            self.assertEqual(len(out["results"]), 1)
            self.assertEqual(out["results"][0]["status"], "pending")
            self.assertEqual(reader.sent, [])  # dry 绝不真发

    def test_live_send_when_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            reader = FakeReader([[]])
            seen = xb.SeenStore(td)
            bridge = xb.Bridge(td, brains={xb.T_KOUZI: FakeBrain()},
                               mode=xb.COLD, send_mode=xb.PROXY,
                               dry_run=False)
            state = {}
            xr.step_once(reader, bridge, seen, "g", state, True)
            reader.batches.append([msg("c", "豆阿阮", "小扣子 说句话")])
            out = xr.step_once(reader, bridge, seen, "g", state,
                               dry_send=False)
            self.assertEqual(out["results"][0]["status"], "ready")
            self.assertTrue(reader.sent)  # 真发了
            self.assertIn("我在呀", "".join(reader.sent))

    def test_dry_run_with_brain_drafts_but_never_sends(self):
        # dry_run 装了脑也要拟稿(落 ready/lines 供审阅), 但绝不真发
        with tempfile.TemporaryDirectory() as td:
            reader = FakeReader([[]])
            seen = xb.SeenStore(td)
            bridge = xb.Bridge(td, brains={xb.T_KOUZI: FakeBrain()},
                               mode=xb.COLD, send_mode=xb.PROXY, dry_run=True)
            state = {}
            xr.step_once(reader, bridge, seen, "g", state, True)
            reader.batches.append([msg("d", "豆阿阮", "小扣子 说句话")])
            out = xr.step_once(reader, bridge, seen, "g", state,
                               dry_send=True)
            r = out["results"][0]
            self.assertEqual(r["status"], "ready")  # dry 也拟了稿
            self.assertTrue(r.get("lines"))
            self.assertIn("我在呀", "".join(r["lines"]))
            self.assertEqual(reader.sent, [])  # 但一个字都没发出去


if __name__ == "__main__":
    unittest.main()
