# -*- coding: utf-8 -*-
r"""恒等式运行时闸门的守卫测试（批次 E，零网络、离线桩数据自造）。

守三件事
--------
1. **每条检查器 ≥3 例**（绿例 + 红例 + None 跳过例）：红例的 violations
   文案必须含**位置索引与具体值**（能定位"哪个元素哪两个数打架"）。
2. **闸门不自动开**：validate 是显式调用；管道不自动跑（源码断言：
   client.py 不 import invariants / 不调 validate）。
3. **5572 不硬编码**：check_breadth_total 的跨日常数性由自校模式 +
   显式 total 两种路径各自验证（5572 只是某一天的值，不是真理）。

红线：`tests/test_*.py` 不许出现 `Client(` / `Session(` 字面量。
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                        # noqa: BLE001
    pass

from tdxdata import invariants as inv
from tdxdata.invariants import Violation, validate


def _bar(**kw) -> SimpleNamespace:
    base = dict(open=10.0, high=10.5, low=9.8, close=10.2,
                vol=1000.0, amount=1_020_000.0)
    base.update(kw)
    return SimpleNamespace(**base)


def _quote(**kw) -> SimpleNamespace:
    base = dict(market=1, code="600519", price=21.18, pre_close=20.50,
                open=20.80, high=21.50, low=20.60, close=None,
                vol=21164.0, amount=4_391_4168.0, raw={"CLOSE": "20.50"})
    base.update(kw)
    return SimpleNamespace(**base)


def _breadth_row(up=1000, down=400, flat=100) -> SimpleNamespace:
    return SimpleNamespace(up_count=up, down_count=down, flat_count=flat)


# ══════════════════════════════════════════════════════════════════════════
# 一、check_ohlc
# ══════════════════════════════════════════════════════════════════════════
def test_ohlc_green_case():
    assert inv.check_ohlc([_bar(), _bar(low=10.0, open=10.0, close=10.5,
                                        high=10.5)]) == []


def test_ohlc_red_case_reports_index_and_values():
    bars = [_bar(), _bar(open=11.0, high=10.5)]      # open > high
    v = inv.check_ohlc(bars)
    assert len(v) == 1 and v[0].index == 1
    assert "11.0" in v[0].detail and "10.5" in v[0].detail
    assert v[0].invariant == "ohlc"


def test_ohlc_low_above_high_envelope():
    v = inv.check_ohlc([_bar(low=10.6, high=10.5)])
    assert len(v) == 1 and "倒挂" in v[0].detail


def test_ohlc_none_is_skipped_not_zero():
    """DEF-011：None = unknown，跳过不猜 —— 不许当 0 判成红。"""
    assert inv.check_ohlc([_bar(open=None, high=None, low=None, close=None),
                           _bar()]) == []
    # Quote 形态：close 缺 → 用 price；四价缺任一 → 整行跳过
    assert inv.check_ohlc([SimpleNamespace(open=0.0, high=0.0, low=0.0,
                                           price=21.18)]) == []


# ══════════════════════════════════════════════════════════════════════════
# 二、check_amount_vol_close
# ══════════════════════════════════════════════════════════════════════════
def test_amount_vol_close_green_case():
    """1000 手 ×100×10.2 = 1,020,000 ≈ amount（同值，误差 0）。"""
    assert inv.check_amount_vol_close([_bar()]) == []


def test_amount_vol_close_red_case():
    b = _bar(vol=1000.0, amount=2_500_000.0, close=10.2)  # 期望 102 万，给 250 万
    v = inv.check_amount_vol_close([b])
    assert len(v) == 1 and v[0].index == 0
    assert "1020000" in v[0].detail.replace(",", "") or "1020000.0" in v[0].detail
    assert v[0].invariant == "amount_vol_close"


def test_amount_vol_close_none_skipped_and_zero_volume_legal():
    assert inv.check_amount_vol_close([_bar(vol=None), _bar(amount=None),
                                       _bar(vol=0.0, amount=0.0)]) == []


def test_amount_vol_close_within_5pct_tol():
    """同刻不同字段取舍：误差 <5% 绿（与 test_hq_extra.py:235 同宽）。"""
    b = _bar(vol=1000.0, close=10.2)                 # 期望 1,020,000
    b.amount = 1_060_000.0                           # 差 3.9% → 绿
    assert inv.check_amount_vol_close([b]) == []
    b.amount = 1_200_000.0                           # 差 17.6% → 红
    assert len(inv.check_amount_vol_close([b])) == 1


# ══════════════════════════════════════════════════════════════════════════
# 三、check_breadth_total（5572 不硬编码）
# ══════════════════════════════════════════════════════════════════════════
def test_breadth_total_explicit_total_green_and_red():
    """显式 total：当日总数传入（2026-10-23 那天是 5572 —— 只是那天的值）。"""
    rows = [_breadth_row(3000, 2000, 572)]           # 和 = 5572
    assert inv.check_breadth_total(rows, total=5572) == []
    rows[0] = _breadth_row(3000, 2000, 571)          # 和 = 5571 ≢ 5572
    v = inv.check_breadth_total(rows, total=5572)
    assert len(v) == 1 and v[0].index == 0
    assert "5571" in v[0].detail and "5572" in v[0].detail


def test_breadth_total_self_consistency_mode_not_hardcoded_5572():
    """自校模式：以首行为基准，**任何**当日常数都行（明天 5573 也绿）。"""
    rows = [_breadth_row(3000, 2000, 573),           # 基准 = 5573
            _breadth_row(2990, 2010, 573),           # 和一致 → 绿
            _breadth_row(3000, 2000, 572)]           # 和 = 5572 ≢ 5573 → 红
    v = inv.check_breadth_total(rows)
    assert len(v) == 1 and v[0].index == 2
    assert "5572" in v[0].detail and "5573" in v[0].detail


def test_breadth_total_empty_and_missing_are_green():
    assert inv.check_breadth_total([]) == []
    assert inv.check_breadth_total([SimpleNamespace(up_count=None, down_count=1,
                                                    flat_count=1)]) == []


# ══════════════════════════════════════════════════════════════════════════
# 四、check_minute_points
# ══════════════════════════════════════════════════════════════════════════
def test_minute_points_green_and_red():
    rows = [{"n_points": 3, "points": [1.0, 2.0, 3.0]},   # 恒等 → 绿
            {"n_points": 5, "points": [1.0, 2.0, 3.0]}]   # 5 ≢ 3 → 红
    assert inv.check_minute_points(rows[:1]) == []
    v = inv.check_minute_points(rows)
    assert len(v) == 1 and v[0].index == 1
    assert "5" in v[0].detail and "3" in v[0].detail
    assert v[0].invariant == "minute_points"


def test_minute_points_truncated_is_legal():
    """本地 count 截尾后 n_points 仍是服务端声明值 —— 声明不因截尾改，绿。"""
    # 截尾行 points 被砍成 2 个但声明 139：这是"本地截尾"形态
    # —— 恒等式判的是「解码层原样」，截尾形态不进本检查器（points 缺失才跳过）。
    # 这里验证 None 跳过：
    assert inv.check_minute_points([{"n_points": 139, "points": None},
                                    {"n_points": None, "points": [1.0]}]) == []


def test_minute_points_accepts_attribute_rows():
    """属性形态（SimpleNamespace）与 dict 形态都能吃。"""
    ns = SimpleNamespace(n_points=2, points=[1.0, 2.0])
    assert inv.check_minute_points([ns]) == []
    ns2 = SimpleNamespace(n_points=2, points=[1.0])
    assert len(inv.check_minute_points([ns2])) == 1


# ══════════════════════════════════════════════════════════════════════════
# 五、check_minute_volume_sum
# ══════════════════════════════════════════════════════════════════════════
def test_minute_volume_sum_green_case():
    """240 点 vol 同单位（手）合计 = 当日 Bar.vol（2026-10-27 归一化后）。"""
    pts = [SimpleNamespace(vol=1.0, amount=10.0) for _ in range(240)]
    bar = SimpleNamespace(vol=240.0, amount=2400.0)
    assert inv.check_minute_volume_sum(pts, bar) == []


def test_minute_volume_sum_red_case_reports_totals():
    pts = [SimpleNamespace(vol=1.0, amount=None) for _ in range(240)]
    bar = SimpleNamespace(vol=480.0, amount=None)     # 合计 240 ≁ 480
    v = inv.check_minute_volume_sum(pts, bar)
    assert len(v) == 1
    assert "240" in v[0].detail and "480" in v[0].detail
    assert v[0].invariant == "minute_volume_sum"


def test_minute_volume_sum_none_bar_or_empty_skipped():
    assert inv.check_minute_volume_sum([], SimpleNamespace(vol=1.0)) == []
    pts = [SimpleNamespace(vol=None, amount=None)]
    assert inv.check_minute_volume_sum(pts, SimpleNamespace(vol=None,
                                                            amount=None)) == []


# ══════════════════════════════════════════════════════════════════════════
# 六、check_pre_close
# ══════════════════════════════════════════════════════════════════════════
def test_pre_close_green_case():
    """PBSample pre_close(3658.0256) ↔ PBCombHQ CLOSE(3658.03)：0.1% 内绿。"""
    q = SimpleNamespace(pre_close=3658.0256, raw={"CLOSE": "3658.03"})
    assert inv.check_pre_close(q) == []


def test_pre_close_red_case():
    q = SimpleNamespace(pre_close=3658.03, raw={"CLOSE": "3700.00"})
    v = inv.check_pre_close(q)
    assert len(v) == 1
    assert "3658.03" in v[0].detail and "3700" in v[0].detail
    assert "1.13%" in v[0].detail
    assert v[0].invariant == "pre_close"


def test_pre_close_missing_either_side_skipped():
    assert inv.check_pre_close(SimpleNamespace(pre_close=None,
                                               raw={"CLOSE": "20.5"})) == []
    assert inv.check_pre_close(SimpleNamespace(pre_close=20.5, raw={})) == []
    assert inv.check_pre_close(SimpleNamespace(pre_close=20.5,
                                               raw=None)) == []


# ══════════════════════════════════════════════════════════════════════════
# 七、validate 分发（方案甲：models.py 零改动）
# ══════════════════════════════════════════════════════════════════════════
def test_validate_dispatches_bars():
    from tdxdata.models import Bars

    bars = Bars([_bar(), _bar(open=11.0, high=10.5)])
    v = validate(bars)
    assert [x.invariant for x in v] == ["ohlc"]      # amount 自洽那根是绿的


def test_validate_dispatches_quotes():
    from tdxdata.models import Quotes

    qs = Quotes([_quote(), _quote(pre_close=20.50, raw={"CLOSE": "21.00"})])
    v = validate(qs)
    assert [x.invariant for x in v] == ["pre_close"]


def test_validate_dispatches_minutes_by_type_name():
    from tdxdata.models import Minutes

    ms = Minutes([SimpleNamespace(n_points=2, points=[1.0])])
    v = validate(ms)
    assert len(v) == 1 and v[0].invariant == "minute_points"


def test_validate_table_without_series_evidence_skips():
    """Table 无类型级标记：无 series 痕迹 → 跳过（能查才查，如实不查）。"""
    from tdxdata.models import Table

    t = Table(cols=["a"], rows=[{"a": 1}])
    assert validate(t) == []


def test_validate_table_with_series_evidence_checks_breadth():
    """provenance.extra 有 series 痕迹的 Table → check_breadth_total。"""
    from tdxdata.models import Table
    from tdxdata._provenance import ClockSource, Provenance
    from datetime import datetime

    t = Table(cols=["up_count", "down_count", "flat_count"],
              rows=[{"up_count": 3000, "down_count": 2000, "flat_count": 572},
                    {"up_count": 3000, "down_count": 2000, "flat_count": 573}])
    t.inherit_provenance(SimpleNamespace(provenance=Provenance(
        fetched_at=datetime(2026, 10, 23, 15, 0), clock=ClockSource.LOCAL,
        cmd="PBSdstat", extra={"series": "880005"})))
    v = validate(t, total=5572)
    assert len(v) == 1 and v[0].invariant == "breadth_total"


def test_validate_empty_and_unknown_types_are_green():
    assert validate([]) == []
    assert validate(None) == []
    assert validate("not a container") == []


# ══════════════════════════════════════════════════════════════════════════
# 八、闸门不自动开（管道零挂接）
# ══════════════════════════════════════════════════════════════════════════
def test_gate_is_not_wired_into_pipeline():
    """闸门是显式调用：client.py 不 import invariants、不调 validate
    （自动跑 = 每次取数多付 O(n) + 误报打断 —— 性能取舍，红线）。"""
    import io
    import os

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = io.open(os.path.join(here, "tdxdata", "client.py"),
                  encoding="utf-8").read()
    assert "invariants" not in src, "client.py 挂了闸门 —— 违反性能取舍拍板"
    assert "validate(" not in src.replace("_validate(", ""), (
        "client.py 出现 validate 调用")


def test_violation_str_is_readable():
    v = Violation("ohlc", 3, "open(11.0) 超出 [low, high]")
    s = str(v)
    assert "ohlc" in s and "#3" in s and "11.0" in s
