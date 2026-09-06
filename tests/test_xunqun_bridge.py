#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""巡群桥测试: 唤起规矩、去重、会话换班、代发不润色、断线遗言、脑请求体。"""
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

FED = Path(__file__).resolve().parents[1] / "tools" / "federation"
sys.path.insert(0, str(FED))
import xunqun_bridge as xb  # noqa: E402


def ts(y, m, d, h=10):
    return int(datetime(y, m, d, h, tzinfo=xb.CN_TZ).timestamp())


class RouterTest(unittest.TestCase):
    def setUp(self):
        self.r = xb.Router(xb.COLD)

    def test_cold_idle_chat_stays_silent(self):
        d = self.r.route({"sender": "甲", "text": "今天天气真好"})
        self.assertEqual(d["target"], xb.T_NONE)
        self.assertEqual(d["reason"], "no_mention_cold")

    def test_plain_name_equals_at(self):
        d = self.r.route({"sender": "乙", "text": "小扣子在吗"})
        self.assertEqual(d["target"], xb.T_KOUZI)
        self.assertEqual(d["reason"], "called_kouzi")

    def test_explicit_at_kouzi(self):
        d = self.r.route({"sender": "乙", "text": "@小扣子 帮看看",
                          "mentioned": True, "mention_target": "kouzi"})
        self.assertEqual(d["target"], xb.T_KOUZI)

    def test_call_benti(self):
        d = self.r.route({"sender": "乙", "text": "豆阿辰你说呢"})
        self.assertEqual(d["target"], xb.T_BENTI)
        d2 = self.r.route({"sender": "乙", "text": "阿辰在不"})
        self.assertEqual(d2["target"], xb.T_BENTI)

    def test_self_loop_never_reply(self):
        for sender in ("小扣子", "豆阿辰"):
            d = self.r.route({"sender": sender, "text": "小扣子：我在"})
            self.assertEqual(d["target"], xb.T_NONE)
            self.assertEqual(d["reason"], "self_loop")
        d = self.r.route({"sender": "任何人", "text": "小扣子",
                          "from_self": True})
        self.assertEqual(d["reason"], "self_loop")

    def test_empty(self):
        self.assertEqual(self.r.route({"sender": "甲", "text": "  "})["reason"],
                         "empty")


class SeenTest(unittest.TestCase):
    def test_dedup_by_msg_id(self):
        with tempfile.TemporaryDirectory() as td:
            s = xb.SeenStore(td)
            m = {"msg_id": "a1", "sender": "甲", "text": "嗨", "ts": 1000}
            self.assertTrue(s.is_new(m))
            self.assertFalse(s.is_new(m))

    def test_fallback_key_without_id(self):
        with tempfile.TemporaryDirectory() as td:
            s = xb.SeenStore(td)
            m = {"sender": "甲", "sender_id": "u1", "text": "嗨", "ts": 1000}
            self.assertTrue(s.is_new(m))
            self.assertFalse(s.is_new(dict(m)))


class ConversationTest(unittest.TestCase):
    def test_rotate_by_turns_and_digest(self):
        with tempfile.TemporaryDirectory() as td:
            k = xb.ConversationKeeper(td, max_turns=2)
            recent = [{"sender": "乙", "text": f"第{i}句"} for i in range(3)]
            r1, rot1 = k.note_turn(xb.T_KOUZI, recent, ts=ts(2026, 9, 6, 9))
            r2, rot2 = k.note_turn(xb.T_KOUZI, recent, ts=ts(2026, 9, 6, 10))
            self.assertIsNone(rot2)
            r3, rot3 = k.note_turn(xb.T_KOUZI, recent, ts=ts(2026, 9, 6, 11))
            self.assertIsNotNone(rot3)                  # 到 2 轮换班
            self.assertNotEqual(r3["session_id"], rot3["session_id"])
            self.assertIn("前情提要", r3["digest"])

    def test_rotate_across_day(self):
        with tempfile.TemporaryDirectory() as td:
            k = xb.ConversationKeeper(td, max_turns=999)
            k.note_turn(xb.T_KOUZI, [], ts=ts(2026, 9, 6, 23))
            rec, rotated = k.note_turn(xb.T_KOUZI,
                                       [{"sender": "乙", "text": "跨天前"}],
                                       ts=ts(2026, 9, 7, 8))
            self.assertIsNotNone(rotated)               # 跨自然日换
            self.assertEqual(rec["day"], "2026-09-07")

    def test_summarize_fn_takes_precedence(self):
        def smart(_msgs):
            return "语义版提要"
        with tempfile.TemporaryDirectory() as td:
            k = xb.ConversationKeeper(td, summarize_fn=smart)
            d = k.build_digest([{"sender": "乙", "text": "x"}])
            self.assertEqual(d, "语义版提要")


class RenderTest(unittest.TestCase):
    def test_proxy_prefix_and_verbatim(self):
        # 原话有错字也一个字不动
        lines = xb.render_outbound(xb.T_KOUZI, "我在地阿", "补充一句",
                                   mode=xb.PROXY)
        self.assertEqual(lines[0], "小扣子：我在地阿")
        self.assertEqual(lines[1], "补充一句")

    def test_official_drops_prefix(self):
        lines = xb.render_outbound(xb.T_KOUZI, "我在", mode=xb.OFFICIAL)
        self.assertEqual(lines, ["我在"])

    def test_benti_never_prefixed(self):
        lines = xb.render_outbound(xb.T_BENTI, "本体回话", mode=xb.PROXY)
        self.assertEqual(lines, ["本体回话"])

    def test_empty(self):
        self.assertEqual(xb.render_outbound(xb.T_KOUZI, "  "), [])


class HealthTest(unittest.TestCase):
    def test_epitaph_once_then_recover_once(self):
        with tempfile.TemporaryDirectory() as td:
            h = xb.BridgeHealth(td, down_after=3, degraad_after=1)
            self.assertIsNone(h.record_fail("慢"))       # 1 次 degraded
            self.assertIsNone(h.record_fail("慢"))
            epitaph = h.record_fail("接口挂了")           # 3 次 down
            self.assertIn("暂时下线", epitaph)
            self.assertIsNone(h.record_fail("还没好"))   # 不再重复遗言
            back = h.record_ok()
            self.assertIn("回来了", back)
            self.assertIsNone(h.record_ok())             # 不重复报恢复

    def test_events_appended(self):
        with tempfile.TemporaryDirectory() as td:
            h = xb.BridgeHealth(td, down_after=1)
            h.record_fail("x")
            lines = (Path(td) / "health.jsonl").read_text(
                encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)


class BridgeTickTest(unittest.TestCase):
    def test_dry_run_routes_only_called(self):
        with tempfile.TemporaryDirectory() as td:
            b = xb.Bridge(td, dry_run=True)
            msgs = [
                {"sender": "甲", "text": "闲聊", "ts": ts(2026, 9, 6, 9)},
                {"sender": "乙", "text": "小扣子帮我", "ts": ts(2026, 9, 6, 9)},
                {"sender": "小扣子", "text": "小扣子：代发", "from_self": True},
            ]
            res = b.tick(msgs)
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0]["target"], xb.T_KOUZI)
            self.assertEqual(res[0]["status"], "pending")  # 阶段A只排队不发


class CozePayloadTest(unittest.TestCase):
    def test_missing_bot_id_raises_clear(self):
        brain = xb.CozeHttpBrain(pat="x", bot_id="")
        with self.assertRaises(RuntimeError):
            brain.build_payload("在吗")

    def test_payload_shape_and_context(self):
        brain = xb.CozeHttpBrain(pat="pat_x", bot_id="b123")
        p = brain.build_payload("在吗", {"session_id": "c9"})
        self.assertEqual(p["bot_id"], "b123")
        self.assertFalse(p["stream"])
        self.assertEqual(p["conversation_id"], "c9")
        self.assertEqual(p["additional_messages"][0]["content"], "在吗")


class XhsParseTest(unittest.TestCase):
    def test_parse_left_right_and_fields(self):
        raw = [
            {"mid": "g.111", "ctype": "1", "self": False,
             "nick": "豆阿阮", "text": "@小扣子 你在吗"},
            {"mid": "g.222", "ctype": "1", "self": True,
             "nick": "", "text": "小扣子：我在的"},
            {"mid": "g.333", "ctype": "3", "self": False,
             "nick": "", "text": ""},  # 无昵称非自己 -> 群友; 空文本也保留
        ]
        items = xb.parse_chat_items(raw, self_name="豆阿辰", now_ts=1000)
        a, b, c = items
        self.assertEqual(a["sender"], "豆阿阮")
        self.assertFalse(a["from_self"])
        self.assertTrue(a["mentioned"])
        self.assertEqual(a["mention_target"], xb.T_KOUZI)
        self.assertEqual(a["msg_id"], "g.111")
        self.assertEqual(a["ts"], 1000)
        # 右侧自己发的: sender 用本号名、from_self=True
        self.assertEqual(b["sender"], "豆阿辰")
        self.assertTrue(b["from_self"])
        # 缺昵称的别人不丢、兜底"群友"
        self.assertEqual(c["sender"], "群友")
        self.assertEqual(c["content_type"], "3")

    def test_mention_target_detect(self):
        self.assertEqual(xb._detect_mention_target("@小扣子 来"), xb.T_KOUZI)
        self.assertEqual(xb._detect_mention_target("@豆阿辰"), xb.T_BENTI)
        self.assertEqual(xb._detect_mention_target("@阿辰 早"), xb.T_BENTI)
        self.assertEqual(xb._detect_mention_target("普通闲聊"), "")

    def test_parse_empty(self):
        self.assertEqual(xb.parse_chat_items(None, now_ts=1), [])
        self.assertEqual(xb.parse_chat_items([], now_ts=1), [])

    def test_router_treats_parsed_self_as_loop(self):
        # 端到端: parse 出来的自己消息进 Router 必须判 self_loop 不回
        items = xb.parse_chat_items(
            [{"mid": "x", "ctype": "1", "self": True, "nick": "",
              "text": "小扣子：在的"}], now_ts=1)
        d = xb.Router(xb.COLD).route(items[0])
        self.assertEqual(d["target"], xb.T_NONE)
        self.assertEqual(d["reason"], "self_loop")


if __name__ == "__main__":
    unittest.main()
