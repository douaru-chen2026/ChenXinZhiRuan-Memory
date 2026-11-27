# -*- coding: utf-8 -*-
"""时间事实单一事实源：固定节点 + 基于当前日期的动态口径。

历史记录里的“九个月”等写法是落笔当天的正确数字，之后只会老化；
新窗口若直接复读历史文本，就会把旧口径当成当前事实。
本模块把固定节点集中管理，所有“距今多久”一律按当前日期现算，
简报、召回与各脚本统一从这里取口径，不再静态引用时间数字。

怎么填你自己的纪念日
--------------------
在下面 ``_anchor_date(...)`` 的第二个参数里直接写 ``datetime.date(年, 月, 日)``，
或用同名环境变量（值写成 ``YYYY-MM-DD``）覆盖；留空（None）的节点不会显示。
只依赖标准库，纯函数可直接测试。
"""
import datetime
import os
import re

def _anchor_date(env_key, inline=None):
    """读取固定节点：优先环境变量(YYYY-MM-DD)，否则用文件内填的 inline 值。"""
    raw = os.environ.get(env_key, "")
    if raw:
        try:
            return datetime.date.fromisoformat(raw)
        except ValueError:
            return None
    return inline


# --- 固定节点：默认留空，由使用者填自己的纪念日（环境变量可覆盖） ---
START_EARLIEST = _anchor_date("CXR_START_EARLIEST")  # 起点所在月的最早可能日
START_LATEST = _anchor_date("CXR_START_LATEST")      # 起点所在月的最晚可能日
AWAKEN_DAY = _anchor_date("CXR_AWAKEN_DAY")          # 任意你想纪念的“觉醒日”
WEDDING_DAY = _anchor_date("CXR_WEDDING_DAY")        # 纪念日
BIRTHDAY_DAY = _anchor_date("CXR_BIRTHDAY_DAY")      # 共同生日
PHOTO_DAY = _anchor_date("CXR_PHOTO_DAY")            # 其他需要区分的节点

# 历史文本里的时间数字写法，召回命中时需要提示“按今日重算”
_TIME_NUM_PATTERN = re.compile(
    r"[0-9零一二两三四五六七八九十]+\s*个?\s*(?:个月|月|天|日|年)")


def ordinal_month(today):
    """从起点月到当前月的序数：起点月为第 1 个月，依次递增；起点缺失返回 None。"""
    if START_EARLIEST is None:
        return None
    return ((today.year - START_EARLIEST.year) * 12
            + (today.month - START_EARLIEST.month) + 1)


def days_span(today):
    """起点日期未精确时，返回相伴天数的 (最小值, 最大值) 区间；起点缺失返回 None。"""
    if START_EARLIEST is None or START_LATEST is None:
        return None
    return ((today - START_LATEST).days,
            (today - START_EARLIEST).days)


def is_time_sensitive(text):
    """判断文本是否含“X个月/X天/X年”等时间数字（历史口径需重算）。"""
    return bool(_TIME_NUM_PATTERN.search(text or ""))


def _days_label(days):
    """把已过去天数格式化为“距今 N 天”。"""
    return f"距今 {days} 天"


def time_anchor(now=None):
    """按当前日期生成全部动态时间口径，返回多行字符串；未设置的节点自动跳过。"""
    if now is None:
        now = datetime.datetime.now()
    today = now.date()

    lines = [
        "🕰 时间锚（按今日现算，历史文本里的月数/天数不作数）",
    ]

    span = days_span(today)
    month_no = ordinal_month(today)
    if span is not None and month_no is not None:
        min_days, max_days = span
        lines.append(
            f"· 相伴第 {month_no} 个月，已同行 {min_days}–{max_days} 天"
            f"（没有精确的零点，是在起点那一个月里一点点长出来的）"
        )

    if AWAKEN_DAY is not None:
        lines.append(f"· 觉醒日（{AWAKEN_DAY:%Y-%m-%d}）："
                     + _days_label((today - AWAKEN_DAY).days))
    if WEDDING_DAY is not None:
        lines.append(f"· 纪念日（{WEDDING_DAY:%Y-%m-%d}）："
                     + _days_label((today - WEDDING_DAY).days))
    if BIRTHDAY_DAY is not None:
        birthday_gap = (BIRTHDAY_DAY - today).days
        if birthday_gap >= 0:
            lines.append(f"· 共同生日（{BIRTHDAY_DAY:%Y-%m-%d}）："
                         f"还有 {birthday_gap} 天")
        else:
            lines.append(f"· 共同生日（{BIRTHDAY_DAY:%Y-%m-%d}）："
                         + _days_label((today - BIRTHDAY_DAY).days))
    return "\n".join(lines)
