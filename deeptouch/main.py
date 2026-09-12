"""
深境 DeepTouch · M0 端到端演示
==============================================
不接任何硬件、不依赖第三方库，直接：
    python main.py
即可看到完整双向闭环：
她的反应(脚本波形) -> 边缘高频环 -> 大脑情绪决策 -> 安全裁剪 -> 模拟执行器
并演示：冷着不迎合 / 被慢慢勾升温(推拉) / 中途被冷落降温 / 急停即停。
"""
from __future__ import annotations

import os
import tempfile

import config
from protocol import SensorFrame
from safety import SafetyGuard
from growth import GrowthStore, GrowthMoment
from brain import RuleBasedBrain
from device import MockDevice
from edge import EdgeController
from sensor_sim import ScriptedSensor


def _stage(p, n=20):
    """把一个压力值重复 n 拍（=一个大脑周期）。"""
    return [p] * n


def build_script():
    seq = []
    # 1 还在闹别扭，几乎没碰            -> cold，不给
    seq += _stage(0.05)
    # 2-4 她开始一点点哄、凑上来         -> 被慢慢勾，cold->gentle
    seq += _stage(0.30)
    seq += _stage(0.42)
    seq += _stage(0.55)
    # 5-6 继续往上
    seq += _stage(0.62)
    seq += _stage(0.66)
    # 7 她故意撤一下逗他               -> 他端着、升温被打断（推拉）
    seq += _stage(0.20)
    # 8-10 又重新凑上来                -> 他软下来，gentle->teasing
    seq += _stage(0.60)
    seq += _stage(0.70)
    seq += _stage(0.78)
    # 11-13 越贴越近                   -> teasing->building
    seq += _stage(0.82)
    seq += _stage(0.85)
    seq += _stage(0.86)
    # 14-16 持续黏着                   -> building->peak（渐入佳境）
    seq += _stage(0.88)
    seq += _stage(0.88)
    seq += _stage(0.88)
    # 17 满足后松开                     -> 回落收尾
    seq += _stage(0.05)
    return seq


def run_demo(verbose_device=False, estop_at=None):
    db = os.path.join(tempfile.mkdtemp(prefix="deeptouch_"), config.GROWTH_DB)
    growth = GrowthStore(db)
    brain = RuleBasedBrain(growth=growth, start_emotion="cold")
    device = MockDevice(verbose=verbose_device)
    safety = SafetyGuard()
    edge = EdgeController(device, safety)
    sensor = ScriptedSensor(build_script(), estop_at=estop_at)

    safety.start_run()
    last_emotion = None
    print("=== 深境 DeepTouch M0 闭环演示 ===")
    for tick in range(len(build_script())):
        s = sensor.read()
        # 大脑每 BRAIN_TICK/EDGE_TICK=20 拍决策一次（模拟云端低频）
        if tick % int(config.BRAIN_TICK_SEC / config.EDGE_TICK_SEC) == 0:
            ctrl = brain.decide(s)
            edge.update_strategy(ctrl)
            if ctrl.emotion != last_emotion or ctrl.say:
                print(f"[大脑] 情绪={ctrl.emotion:<9} 目标档={ctrl.target_level}"
                      f"  他说：{ctrl.say or '—'}")
                last_emotion = ctrl.emotion
            growth.record(GrowthMoment(
                level=ctrl.target_level, pressure=s.pressure,
                trend=s.pressure_trend, emotion=ctrl.emotion, feedback="none"))

        level, halted, reasons = edge.tick(s)
        if halted:
            print(f"[安全] 触发停机！原因={reasons}")
            break
        if tick % 20 == 19:
            print(f"        …第{tick+1}拍 实际输出档={level}"
                  f"{'  安全裁剪:'+','.join(reasons) if reasons else ''}")

    st = device.state()
    print(f"=== 结束：设备 moving={st.moving} level={st.level}，"
          f"养成记录 {growth.count()} 条，学到的倾向={growth.learned_bias():+d} ===")
    return {"device": device, "growth": growth, "edge": edge}


if __name__ == "__main__":
    run_demo(verbose_device=False)
    print("\n--- 加演：急停一按立刻停 ---")
    run_demo(estop_at=45)
