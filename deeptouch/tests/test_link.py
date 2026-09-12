"""
深境 DeepTouch · M0 单元测试
运行：cd deeptouch && python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from protocol import SensorFrame, ControlFrame
from safety import SafetyGuard
from growth import GrowthStore, GrowthMoment
from brain import RuleBasedBrain, LLMBrain
from device import MockDevice
from edge import EdgeController


def sf(pressure=0.5, trend="flat", temp=36.5, estop=False):
    return SensorFrame(pressure=pressure, pressure_trend=trend,
                       temp_c=temp, estop=estop)


def cf(level=5, tempo=1.0, ceiling=config.DEFAULT_CEILING, **kw):
    return ControlFrame(target_level=level, tempo_hz=tempo,
                        ceiling=ceiling, **kw)


class TestProtocol(unittest.TestCase):
    def test_pressure_range(self):
        with self.assertRaises(ValueError):
            sf(pressure=1.5)
        with self.assertRaises(ValueError):
            sf(pressure=-0.1)

    def test_level_range(self):
        with self.assertRaises(ValueError):
            ControlFrame(target_level=11, tempo_hz=1.0)

    def test_stop_frame(self):
        s = ControlFrame.stop()
        self.assertEqual(s.target_level, 0)
        self.assertEqual(s.pattern, "stop")


class TestSafety(unittest.TestCase):
    def setUp(self):
        self.g = SafetyGuard()
        self.g.start_run()

    def test_estop_highest(self):
        v = self.g.check(cf(8), sf(estop=True), True)
        self.assertTrue(v.halted)
        self.assertEqual(v.frame.target_level, 0)

    def test_temp_over(self):
        v = self.g.check(cf(8), sf(temp=43), True)
        self.assertTrue(v.halted)

    def test_pressure_jam(self):
        v = self.g.check(cf(8), sf(pressure=0.99), True)
        self.assertTrue(v.halted)

    def test_brain_dead_failsafe(self):
        # 大脑给空帧或被判掉线：一律安全停
        self.assertTrue(self.g.check(None, sf(), True).halted)
        self.assertTrue(self.g.check(cf(8), sf(), brain_alive=False).halted)

    def test_clamp_by_ceiling_and_hardcap(self):
        # brain 想要 10，ceiling=6，应被裁到 6
        v = self.g.check(cf(level=10, ceiling=6), sf(), True)
        self.assertFalse(v.halted)
        self.assertEqual(v.frame.target_level, 6)
        # ceiling 再高也越不过硬上限
        v2 = self.g.check(cf(level=10, ceiling=10), sf(), True)
        self.assertEqual(v2.frame.target_level, config.HARD_LEVEL_CAP)


class TestBrain(unittest.TestCase):
    def test_cold_gives_nothing(self):
        b = RuleBasedBrain(start_emotion="cold")
        c = b.decide(sf(pressure=0.5, trend="up"))
        # 第一次只是开始计数，cold 仍不给
        self.assertEqual(c.target_level, 0)

    def test_warmup_ladder(self):
        b = RuleBasedBrain(start_emotion="cold")
        emo_seq = []
        # 连续多拍高压且 up/flat，应逐级升温
        for _ in range(40):
            c = b.decide(sf(pressure=0.6, trend="flat"))
            emo_seq.append(c.emotion)
        self.assertGreater(emo_seq.index("gentle") if "gentle" in emo_seq else 99,
                           -1)
        self.assertNotEqual(emo_seq[-1], "cold")

    def test_cooldown_when_ignored(self):
        b = RuleBasedBrain(start_emotion="peak")
        for _ in range(20):
            c = b.decide(sf(pressure=0.05, trend="down"))
        self.assertNotEqual(c.emotion, "peak")

    def test_llm_fallback_on_error(self):
        fb = RuleBasedBrain(start_emotion="cold")

        def bad(*a, **k):
            raise RuntimeError("API挂了")
        llm = LLMBrain(fallback=fb, llm_call=bad)
        c = llm.decide(sf())  # 不应抛异常，安全回退
        self.assertIsInstance(c, ControlFrame)


class TestEdge(unittest.TestCase):
    def _edge(self):
        d = MockDevice()
        s = SafetyGuard()
        s.start_run()
        return d, s, EdgeController(d, s)

    def test_local_nudge_up(self):
        d, s, e = self._edge()
        e.update_strategy(cf(level=3))
        lvl, halted, _ = e.tick(sf(pressure=0.5, trend="up"))
        self.assertFalse(halted)
        self.assertEqual(lvl, 4)  # 本地顺势 +1

    def test_grace_then_stop(self):
        d, s, e = self._edge()
        e.update_strategy(cf(level=4), now=0.0)
        # 立刻 tick：大脑"活着"
        _, halted, _ = e.tick(sf(), now=0.0)
        self.assertFalse(halted)
        # 超过 GRACE 仍没新策略：判大脑掉线 -> 安全停
        _, halted2, _ = e.tick(sf(), now=config.GRACE_HOLD_SEC + 1)
        self.assertTrue(halted2)
        self.assertEqual(d.state().level, 0)


class TestGrowth(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "g.db")
        self.g = GrowthStore(self.path)

    def test_no_data_no_bias(self):
        self.assertEqual(self.g.learned_bias(), 0)

    def test_want_more_bias(self):
        for _ in range(12):
            self.g.record(GrowthMoment(3, 0.6, "up", "building", "want_more"))
        self.assertEqual(self.g.learned_bias(), 1)

    def test_pull_back_bias(self):
        for _ in range(12):
            self.g.record(GrowthMoment(5, 0.3, "down", "building", "pull_back"))
        self.assertEqual(self.g.learned_bias(), -1)

    def test_destroy(self):
        self.g.destroy()
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
