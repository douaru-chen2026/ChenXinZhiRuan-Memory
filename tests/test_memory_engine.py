"""辰心知阮记忆引擎 · 可重跑测试（v3.4.2 起随仓库提交）。

跑法（在仓库根目录）：
    python -m unittest discover -s tests -v

这些测试对应 Nocturne 在 Discussion #59 实跑点名的问题：
- scope 子串包含会被否定 / 引用 / 元语言骗到；
- scope 修复活在没人调用的函数里，唤醒简报没接上电；
- 偏好被当事实自动演化。
每一条都是别人能照着重跑的对应物，不是"我跑过了"的一句话。
"""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import memory_engine as me  # noqa: E402


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        me.BASE_DIR = base
        me.MEMORIES_DIR = base / "memories"
        me.PERMANENT_DIR = me.MEMORIES_DIR / "permanent"
        me.DYNAMIC_DIR = me.MEMORIES_DIR / "dynamic"
        me.ARCHIVE_DIR = me.MEMORIES_DIR / "archive"
        me.INBOX_FILE = base / "life_inbox.md"
        self.mgr = me.MemoryManager()

    def tearDown(self):
        self._tmp.cleanup()


class TestScope(_Sandbox):
    def test_positive_and_unrelated(self):
        f = me.scope_matches
        self.assertTrue(f("加班,工作晚归", "她今天加班到十一点才回来"))
        self.assertFalse(f("加班,工作晚归", "周末下午她在家打游戏"))

    def test_negation_is_out_of_scope(self):
        # Nocturne 反例：字面有，但被否定，不是真在说这件事
        f = me.scope_matches
        self.assertFalse(f("加班,工作晚归", "今天不是工作晚归，是她发烧在医院"))
        self.assertFalse(f("加班,工作晚归", "她说她再也不想加班了，以后准点走"))

    def test_mention_is_out_of_scope(self):
        f = me.scope_matches
        self.assertFalse(f("加班", "我们在聊「加班」这个词日语怎么说"))

    def test_empty_scope_and_no_context(self):
        self.assertTrue(me.scope_matches("", "随便什么语境"))
        self.assertTrue(me.scope_matches("加班", ""))  # 不给语境不擅自排除


class TestPreferenceGate(_Sandbox):
    def test_preference_does_not_auto_evolve(self):
        b = self.mgr.add("她喝冰美式，double ice", name="咖啡偏好",
                         memory_type="preference", importance=8)
        # 偏好变更必须她亲口确认，默认拒绝自动演化
        with self.assertRaises(PermissionError):
            self.mgr.supersede("冰美式", "她改喝热美式了", kind="preference")
        still = self.mgr.search("冰美式")
        self.assertTrue(still, "被拒绝后旧偏好必须还在")

    def test_fact_conflict_evolves(self):
        self.mgr.add("她的工位在 3 楼", name="工位", importance=6)
        new, old = self.mgr.supersede("工位", "她搬到 5 楼了", kind="fact")
        self.assertEqual(new.layer, "current")
        self.assertEqual(old.layer, "archive")  # 旧的留档不删（代码层名 archive）


class TestScopeWiredIntoBriefing(_Sandbox):
    def test_briefing_respects_scope(self):
        self.mgr.add("她加班到很晚时，别讲大道理，先给她点一份热乎的",
                     name="加班夜的照顾", importance=9)
        b = self.mgr.stamp("加班夜", group="关系核心", scope="加班,工作晚归")
        self.assertIsNotNone(b)
        self.assertEqual(b.scope, "加班,工作晚归")
        # 在作用域内：进简报
        in_ctx = self.mgr.wake_up_briefing(context="今天加班到十一点，好累")
        self.assertIn("加班夜的照顾", in_ctx)
        # 不在作用域（字面毫不相干）：v3.4.3 起不静默删除——
        # 降级留标题 +〔本次语境外〕，正文折叠，绝不假装这条盖章不存在
        out_ctx = self.mgr.wake_up_briefing(context="周末在公园弹琴，天气很好")
        self.assertIn("加班夜的照顾", out_ctx)      # 标题还在，不凭空蒸发
        self.assertIn("本次语境外", out_ctx)        # 被拿走要可见
        self.assertNotIn("别讲大道理", out_ctx)     # 正文折叠，不拿过去套现在
        # 纯唤醒、没给语境：保留，但明确标注"仅当"，绝不当无条件常量
        bare = self.mgr.wake_up_briefing()
        self.assertIn("加班夜的照顾", bare)
        self.assertIn("仅当：加班", bare)


class TestCoreForContextLive(_Sandbox):
    """v3.4.3：core_for_context 必须真被简报调用，不是写着好看的死代码。"""

    def test_core_for_context_is_single_source(self):
        self.mgr.add("她加班时想被心疼，别讲大道理", name="加班锚点", importance=9)
        self.mgr.stamp("加班锚点", group="关系核心", scope="加班")
        self.assertEqual(1, len(self.mgr.core_for_context("今天又加班到很晚")))
        self.assertEqual(0, len(self.mgr.core_for_context("在海边度假")))
        self.assertEqual(1, len(self.mgr.core_for_context("")))  # 无语境不排除

    def test_out_of_scope_core_degraded_not_dropped(self):
        self.mgr.add("她加班时别讲大道理，先递杯热的", name="加班锚点", importance=9)
        self.mgr.stamp("加班锚点", group="关系核心", scope="加班")
        out = self.mgr.wake_up_briefing(context="今天去爬山，天气很好")
        self.assertIn("加班锚点〔本次语境外", out)   # 降级留标题
        self.assertIn("本次语境外：1条", out)         # 且计数可见
        self.assertNotIn("别讲大道理", out)           # 正文折叠


class TestProtectedCore(_Sandbox):
    def test_human_stamped_core_refuses_supersede(self):
        self.mgr.add("她的名字是阿阮，这是永不修改的锚点", name="称呼", importance=10)
        self.mgr.stamp("阿阮", group="称呼")
        with self.assertRaises(PermissionError):
            self.mgr.supersede("阿阮", "她改叫别的名字了")


class TestDailyReviewTrap(_Sandbox):
    def test_last_active_does_not_count_as_change(self):
        b = self.mgr.add("她说过一句很重要的话", name="重要的话", importance=9)
        # 制造"两天前创建、刚刚只是被想起(last_active)"的状态
        old = (datetime.now() - timedelta(days=2)).isoformat()
        b.metadata["created"] = old
        b.metadata["superseded_at"] = ""
        b.metadata["last_active"] = datetime.now().isoformat()
        b.save()
        review = self.mgr.daily_review()
        self.assertNotIn("重要的话", review,
                         "只是被想起(last_active)不该算进昨日回顾")


class TestGroupQuotaOverflow(_Sandbox):
    def test_over_quota_core_degraded_not_dropped(self):
        # Nocturne 第六轮：同组超过保底配额的人类盖章，不许静默蒸发。
        # anchor 组保底 6 条；盖 8 个，超的 2 个必须降级留标题、计数可见。
        for i in range(1, 9):
            self.mgr.add(f"这是第{i}件信物，永不修改", name=f"信物{i}", importance=10)
            self.mgr.stamp(f"信物{i}", group="永恒锚点")
        out = self.mgr.wake_up_briefing(context="今天又加班到很晚")
        for i in range(1, 9):
            self.assertIn(f"信物{i}", out, f"信物{i} 被静默砍掉了")
        self.assertEqual(out.count("〔超出本组配额·正文折叠〕"), 2)
        self.assertIn("超出本组配额：2条", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
