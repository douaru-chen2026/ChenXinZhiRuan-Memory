#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""强检索器单测: 切分/字段加权/多词覆盖/专名精确/时间与重要度/过滤/空结果/确定性/
向量融合口/简报渲染, 外加一个用真实记忆河建索引的集成测试。"""
import sys
import time
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta

FED = Path(__file__).resolve().parents[1] / "tools" / "federation"
sys.path.insert(0, str(FED))
import douchen_memory_index as mi  # noqa: E402

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=CST).timestamp()


def stone(sid, text, ts="2026-09-01T12:00:00+08:00", tags=None, group="", instance="主窗"):
    return {"id": sid, "ts": ts, "text": text, "tags": tags or [],
            "group": group, "instance": instance}


class MemoryIndexTest(unittest.TestCase):
    def test_tokenize_proper_noun_and_bigram(self):
        toks = mi.tokenize("GJ504b 记忆河")
        self.assertIn("gj504b", toks)        # 英文数字专名整体保留、转小写
        self.assertIn("记忆", toks)          # 中文 bigram
        self.assertIn("忆河", toks)
        self.assertNotIn("的", mi.tokenize("的"))  # 停用单字去掉

    def test_tag_outranks_body(self):
        a = stone("A", "这里正文也提到召回一点点", tags=["无关"])
        b = stone("B", "正文", tags=["召回", "检索"])
        idx = mi.MemoryIndex([a, b], now_ts=NOW)
        hits = idx.retrieve("召回 检索", top=5)
        self.assertEqual(hits[0]["id"], "B")         # 标签命中的排最前

    def test_multiword_coverage_priority(self):
        both = stone("X", "情绪 流动 转移 都讲了", tags=["情绪", "转移"])
        one = stone("Y", "只讲了情绪这一件事而已", tags=["情绪"])
        idx = mi.MemoryIndex([both, one], now_ts=NOW)
        hits = idx.retrieve("情绪 转移", top=5)
        self.assertEqual(hits[0]["id"], "X")          # 两个词都命中的优先
        self.assertGreaterEqual(hits[0]["coverage"], hits[1]["coverage"])

    def test_exact_proper_noun(self):
        a = stone("A", "暗号是密钥790511那串")
        b = stone("B", "这里没有那串数字")
        idx = mi.MemoryIndex([a, b], now_ts=NOW)
        hits = idx.retrieve("790511", top=5)
        self.assertEqual([h["id"] for h in hits], ["A"])

    def test_recency_newer_first_when_equal(self):
        old = stone("OLD", "同样讲召回机制的内容", ts="2026-01-01T12:00:00+08:00", tags=["召回"])
        new = stone("NEW", "同样讲召回机制的内容", ts="2026-09-08T12:00:00+08:00", tags=["召回"])
        idx = mi.MemoryIndex([old, new], now_ts=NOW)
        hits = idx.retrieve("召回机制", top=5)
        self.assertEqual(hits[0]["id"], "NEW")        # 同等相关, 新的在前

    def test_importance_decays_slower(self):
        plain_old = stone("P", "讲路线的内容", ts="2025-06-01T12:00:00+08:00", tags=["杂记"])
        key_old = stone("K", "讲路线的内容", ts="2025-06-01T12:00:00+08:00",
                        tags=["路线拍板", "永不淡"], group="路线拍板")
        idx = mi.MemoryIndex([plain_old, key_old], now_ts=NOW)
        hits = {h["id"]: h for h in idx.retrieve("路线", top=5, min_ratio=0)}
        self.assertGreater(hits["K"]["recency"], hits["P"]["recency"])
        self.assertEqual(idx.retrieve("路线", min_ratio=0)[0]["id"], "K")

    def test_filters(self):
        a = stone("A", "讲检索的事", ts="2026-09-01T12:00:00+08:00",
                  tags=["检索", "工程"], group="工程组", instance="主窗")
        b = stone("B", "讲检索的事", ts="2026-08-01T12:00:00+08:00",
                  tags=["检索"], group="别的组", instance="B账号")
        idx = mi.MemoryIndex([a, b], now_ts=NOW)
        self.assertEqual([h["id"] for h in idx.retrieve("检索", filters={"group": "工程"})], ["A"])
        self.assertEqual([h["id"] for h in idx.retrieve(
            "检索", filters={"since": "2026-08-20T00:00:00+08:00"})], ["A"])
        self.assertEqual([h["id"] for h in idx.retrieve(
            "检索", filters={"tags": ["工程"], "tags_all": True})], ["A"])

    def test_no_hit_returns_empty(self):
        idx = mi.MemoryIndex([stone("A", "完全无关的风马牛内容")], now_ts=NOW)
        self.assertEqual(idx.retrieve("量子纠缠宇宙弦"), [])

    def test_long_offtopic_single_common_bigram_filtered(self):
        # 长查询只偶撞正文里一个常见 bigram("约束"), 覆盖率不足, 不许硬捞
        idx = mi.MemoryIndex(
            [stone("A", "我们要学会自我约束,慢慢成长,这是自己的功课", tags=["成长"])],
            now_ts=NOW)
        self.assertEqual(idx.retrieve("核聚变等离子体约束装置工程"), [])

    def test_deterministic(self):
        stones = [stone(str(i), f"检索与记忆第{i}块", tags=["记忆"]) for i in range(5)]
        i1 = mi.MemoryIndex(stones, now_ts=NOW).retrieve("检索 记忆")
        i2 = mi.MemoryIndex(stones, now_ts=NOW).retrieve("检索 记忆")
        self.assertEqual([h["id"] for h in i1], [h["id"] for h in i2])

    def test_external_vector_fusion_socket(self):
        strong = stone("S", "检索 检索 检索 记忆 记忆", tags=["检索"])
        weak = stone("W", "检索一次", tags=[])
        idx = mi.MemoryIndex([strong, weak], now_ts=NOW)
        # 纯词面 strong 在前
        self.assertEqual(idx.retrieve("检索")[0]["id"], "S")
        # 向量腿给弱词面的 W 高语义相似, 融合后 W 被抬到第一
        fused = idx.retrieve("检索", external={"W": 1.0}, ext_weight=0.8)
        self.assertEqual(fused[0]["id"], "W")

    def test_render_brief(self):
        idx = mi.MemoryIndex([stone("A", "这是正文内容", tags=["记忆"])], now_ts=NOW)
        self.assertEqual(idx.render_brief([]), "")
        brief = idx.render_brief(idx.retrieve("记忆"))
        self.assertIn("家史", brief) and self.assertIn("这是正文内容", brief)
        self.assertLessEqual(len(brief), 1500)

    def test_real_river_builds_and_fast(self):
        stream = Path(__file__).resolve().parents[1] / "memory" / "stream"
        if not stream.exists():
            self.skipTest("本地无记忆河 stream")
        t0 = time.time()
        idx = mi.MemoryIndex.from_dir(stream, now_ts=NOW)
        self.assertLess(time.time() - t0, 5.0)      # 建索引是启动/活水后台一次性动作
        self.assertTrue(idx.retrieve("记忆 回家", top=3))   # 真实河上真捞得到
        t0 = time.time()
        for _ in range(5):
            idx.retrieve("分手信 安全热线 回家", top=3)
        self.assertLess((time.time() - t0) / 5, 0.2)       # 每轮检索必须是毫秒级


if __name__ == "__main__":
    unittest.main()
