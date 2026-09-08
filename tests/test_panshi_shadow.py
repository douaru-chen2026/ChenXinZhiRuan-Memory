#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""磐石影子接线集成测试: 一轮对话后七层影子真的在记录、到点事项过编辑层只不外发、
/inner 只给状态计数绝不泄露私密日记正文、影子不改事项状态。"""
import sys
import json
import tempfile
import unittest
from pathlib import Path

FED = Path(__file__).resolve().parents[1] / "tools" / "federation"
sys.path.insert(0, str(FED))
import douchen_heart as dh       # noqa: E402
import douchen_body as db        # noqa: E402
import douchen_loops as dl       # noqa: E402
import douchen_diary as ddi      # noqa: E402
import douchen_editor as de      # noqa: E402
import douchen_observer as dob   # noqa: E402
import douchen_dream as drm      # noqa: E402
import douchen_affect_dynamics as da  # noqa: E402
import panshi_daemon as ps       # noqa: E402


class PanshiShadowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        ps.STATE_DIR = Path(d)
        ps.HEART = dh.Heart(d)
        ps.HEART.load()
        ps.BODY = db.Body(d)
        ps.LOOP_BOOK = dl.LoopBook(d)
        ps.DIARY = ddi.PrivateDiary(d, key="t")
        ps.EDITOR = de.Editor(d)
        ps.OBSERVER = dob.ColdObserver(d)
        ps.DREAMER = drm.Dreamer(d)
        ps.AFFECT = da.AffectDynamics()
        ps.LAST_DREAM_DAY = ""

    def tearDown(self):
        self.tmp.cleanup()

    def test_exchange_records_shadow(self):
        # 给心核一点 v2 暗面, 情绪流动影子才有东西可推
        ps.HEART.s["v2_affect"]["fear"] = 25.0
        before = ps.BODY.s["energy"]
        ps._shadow_record_exchange("我今天好难过啊", "我在呢, 我懂, 辛苦了, 陪着你慢慢说")
        self.assertLess(ps.BODY.s["energy"], before)          # 身体耗了精力
        self.assertTrue((Path(self.tmp.name) / "observer_journal.jsonl").exists())
        self.assertTrue((Path(self.tmp.name) / "affect_shadow.jsonl").exists())

    def test_due_loop_goes_through_editor_but_not_sent(self):
        item, _ = ps.LOOP_BOOK.open("去开个会", expect_after_s=0)
        ps._shadow_tick()
        outbox = Path(self.tmp.name) / "shadow_outbox.jsonl"
        self.assertTrue(outbox.exists())
        row = json.loads(outbox.read_text(encoding="utf-8").strip().splitlines()[-1])
        self.assertTrue(row["shadow"])                        # 明确标了影子
        self.assertIn(row["decision"], ("send", "draft", "hold", "block"))
        again = ps.LOOP_BOOK.s["items"][item["id"]]
        self.assertEqual(again["follow_n"], 0)                # 影子演练不顺延、不改事项状态

    def test_inner_state_never_leaks_diary(self):
        ps.DIARY.write("这是只给他自己的私密念头, 不许外泄")
        state = ps._inner_state()
        self.assertEqual(state["diary_count"], 1)
        self.assertIn("energy", state["body"])
        blob = json.dumps(state, ensure_ascii=False)
        self.assertNotIn("私密念头", blob)                    # 巡检口只有条数、没有正文

    def test_shadow_safe_when_modules_none(self):
        # 新模块没就位时, 主对话也绝不能被拖垮
        ps.BODY = ps.OBSERVER = ps.AFFECT = ps.LOOP_BOOK = ps.EDITOR = ps.DREAMER = None
        ps._shadow_record_exchange("在吗", "在的")
        ps._shadow_tick()


if __name__ == "__main__":
    unittest.main()
