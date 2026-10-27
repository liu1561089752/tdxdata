# -*- coding: utf-8 -*-
"""`tdxdata/limits.py` 的**离线**单元测试（零网络、零第三方依赖）。

口径来自 `API设计.md` §2.7「P2 涨跌停」。重点在**取整**：
内置 `round()` 是银行家舍入 + 浮点二进制误差，会在"恰好半分"的价格上少算/多算一分钱。
本机 Python 3.14 实测的翻车样本（测试里逐条断言）：
  · `round(9.495, 2)      = 9.49`  → 交易所应为 **9.50**
  · `round(9.45 * 1.1, 2) = 10.39` → 应为 **10.40**
  · `round(9.45 * 0.9, 2) = 8.50`  → 应为 **8.51**
  · `round(10.10 * 0.95, 2)= 9.59` → 应为 **9.60**（主板 ST）
跑法：python tests/test_limits.py   或   pytest tests/test_limits.py -q
"""
from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tdxdata import limits as lim                            # noqa: E402


def _raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc:
        return True
    except Exception as e:                                     # noqa: BLE001
        raise AssertionError("期望 %s，实到 %s: %s" % (exc.__name__, type(e).__name__, e))
    raise AssertionError("%r 应抛 %s" % (fn, exc.__name__))


# ── 1. 板块判定 + 幅度 ───────────────────────────────────────────────────
def test_board_of():
    cases = [
        ("600519", "main"), ("601398", "main"), ("000001", "main"), ("002594", "main"),
        ("300750", "chinext"), ("301001", "chinext"),
        ("688981", "star"), ("689009", "star"),
        ("830799", "bse"), ("920091", "bse"), ("430047", "bse"),
        ("871981", "bse"), ("889999", "bse"),
        ("510300", "fund"), ("159915", "fund"), ("500001", "fund"),
        ("511990", "fund"), ("522000", "fund"), ("560000", "fund"), ("588000", "fund"),
        # 转债 / B 股：2026-10-26 手机通达信实测后新增（原来都落到 unknown 按 10%）
        ("113054", "bond"), ("110077", "bond"), ("111012", "bond"), ("118013", "bond"),
        ("123283", "bond"), ("127045", "bond"), ("128136", "bond"), ("132007", "bond"),
        ("900901", "bshare"), ("200002", "bshare"),
        ("100001", "unknown"),
    ]
    for code, want in cases:
        got = lim.board_of(code)
        assert got == want, "%s → %r，期望 %r" % (code, got, want)


def test_bond_ratio_is_20_percent():
    """沪深可转债现行 ±20%（2026-10-26 手机通达信：113054 昨收 120.384 → 涨停 144.461）。"""
    for code in ("113054", "110077", "123283", "127045"):
        assert abs(lim.ratio_of(code) - 0.20) < 1e-12, code
    up, down = lim.price_limits(120.384, "113054")
    assert (up, down) == (144.461, 96.307), (up, down)


def test_three_decimal_tick_for_fund_bond_and_bshare():
    """3 位小数品种按 0.001 取整（实测：510300→4.596、900901→0.626）。"""
    assert lim.tick_of("510300") == 0.001
    assert lim.tick_of("113054") == 0.001
    assert lim.tick_of("900901") == 0.001
    assert lim.tick_of("600519") == 0.01
    assert lim.tick_of("920207") == 0.01
    assert lim.price_limits(4.178, "510300") == (4.596, 3.760)
    assert lim.price_limits(0.569, "900901") == (0.626, 0.512)


def test_ratio_of_by_board():
    cases = [
        ("600519", 0.10), ("000001", 0.10), ("002594", 0.10),
        ("300750", 0.20), ("688981", 0.20),
        ("830799", 0.30), ("920091", 0.30), ("430047", 0.30),
        ("510300", 0.10), ("159915", 0.10),
        ("100001", 0.10),                       # unknown 按 10%
    ]
    for code, want in cases:
        got = lim.ratio_of(code)
        assert abs(got - want) < 1e-12, "%s → %r，期望 %r" % (code, got, want)


def test_ratio_of_st_only_affects_main_board():
    assert abs(lim.ratio_of("600519", is_st=True) - 0.05) < 1e-12
    assert abs(lim.ratio_of("000001", is_st=True) - 0.05) < 1e-12
    assert abs(lim.ratio_of("300750", is_st=True) - 0.20) < 1e-12   # 创业板 ST 仍 20%
    assert abs(lim.ratio_of("688981", is_st=True) - 0.20) < 1e-12   # 科创板 ST 仍 20%
    assert abs(lim.ratio_of("920091", is_st=True) - 0.30) < 1e-12   # 北交所 ST 仍 30%
    assert abs(lim.ratio_of("510300", is_st=True) - 0.10) < 1e-12   # 基金不受 ST 影响


def test_price_limits_per_board():
    cases = [
        ("600519", (11.00, 9.00)),      # 主板 10%
        ("000001", (11.00, 9.00)),
        ("300750", (12.00, 8.00)),      # 创业板 20%
        ("688981", (12.00, 8.00)),      # 科创板 20%
        ("830799", (13.00, 7.00)),      # 北交所 30%
        ("920091", (13.00, 7.00)),
        ("510300", (11.00, 9.00)),      # 基金 10%
        ("100001", (11.00, 9.00)),      # unknown 10%
    ]
    for code, want in cases:
        got = lim.price_limits(10.0, code)
        assert got == want, "%s 10.00 → %r，期望 %r" % (code, got, want)


def test_price_limits_st():
    assert lim.price_limits(10.0, "600519", is_st=True) == (10.50, 9.50)
    assert lim.price_limits(10.0, "300750", is_st=True) == (12.00, 8.00)
    assert lim.price_limits(10.0, "920091", is_st=True) == (13.00, 7.00)   # 北交所 ST 仍 30%


# ── 2. 取整：ROUND_HALF_UP，绝不用内置 round() ───────────────────────────
def test_rounding_counterexample_up_side():
    """prev_close=10.55、主板 → 涨停 **11.61**（10.55*1.1 = 11.605 的半分舍入）。"""
    up, _down = lim.price_limits(10.55, "600519")
    assert up == 11.61, up
    assert lim.ratio_of("600519") == 0.10


def test_rounding_counterexample_down_side():
    """跌停侧的同类反例：`round(9.495, 2)` 在浮点下给 9.49，正确是 **9.50**。"""
    _up, down = lim.price_limits(10.55, "600519")
    assert down == 9.50, down
    assert round(9.495, 2) != 9.50, "本机 round() 已不再翻车，反例失效需换样本"


def test_builtin_round_would_be_wrong_both_sides():
    """prev_close=9.45 主板：正确 (10.40, 8.51)，内置 round() 会算成 (10.39, 8.50)。"""
    up, down = lim.price_limits(9.45, "600519")
    assert (up, down) == (10.40, 8.51), (up, down)
    bad_up = round(9.45 * 1.1, 2)
    bad_down = round(9.45 * 0.9, 2)
    assert bad_up != up and bad_down != down, (bad_up, bad_down)
    assert bad_up == 10.39 and bad_down == 8.50, (bad_up, bad_down)


def test_builtin_round_would_be_wrong_st_down_side():
    """主板 ST 5%：prev=10.10 → 跌停正确 9.60，`round(10.10*0.95, 2)` 给 9.59。"""
    up, down = lim.price_limits(10.10, "600519", is_st=True)
    assert (up, down) == (10.61, 9.60), (up, down)
    assert round(10.10 * 0.95, 2) != down


def test_rounding_is_half_up_not_bankers():
    """银行家舍入会把 …5 舍到偶数位；交易所一律向远离零的方向进一分。"""
    # 1.005*1.1? 直接挑必然落在半分的组合：涨跌幅 10% 下 4.65 → 5.115
    up, down = lim.price_limits(4.65, "600519")
    assert up == 5.12, up          # 5.115 → 5.12（ROUND_HALF_UP）
    assert down == 4.19, down      # 4.185 → 4.19
    # 3.15*1.1 = 3.465 → 3.47；*0.9 = 2.835 → 2.84
    up2, down2 = lim.price_limits(3.15, "600519")
    assert (up2, down2) == (3.47, 2.84), (up2, down2)


def test_result_respects_instrument_tick():
    """股票 2 位小数；基金/转债/B 股 3 位小数（`tick_of` 口径）。"""
    two = ("600519", "300750", "688981", "920091", "000001")
    three = ("510300", "113054", "900901")
    for prev in (10.0, 10.55, 3.333, 123.456, 0.987, 88.88):
        for code in two:
            up, down = lim.price_limits(prev, code)
            for v in (up, down):
                assert round(v, 2) == v, (prev, code, v)
                assert isinstance(v, float), type(v)
        for code in three:
            up, down = lim.price_limits(prev, code)
            for v in (up, down):
                assert round(v, 3) == v, (prev, code, v)
                assert isinstance(v, float), type(v)


# ── 3. 异常与不变式 ─────────────────────────────────────────────────────
def test_is_new_raises_not_implemented():
    _raises(NotImplementedError, lim.price_limits, 10.0, "600519", is_new=True)
    _raises(NotImplementedError, lim.price_limits, 10.0, "300750", True, True)
    # 默认 is_new=False 正常出价
    assert lim.price_limits(10.0, "600519") == (11.00, 9.00)


def test_bad_prev_close_raises_value_error():
    for bad in (0, 0.0, -1, -0.01):
        _raises(ValueError, lim.price_limits, bad, "600519")
    for bad in (None, "abc", float("nan"), float("inf")):
        _raises(ValueError, lim.price_limits, bad, "600519")


def test_bad_code_raises_value_error():
    for bad in ("", "60051", "6005199", "abcdef", "60051a", "６００５１９", None, "60 519"):
        _raises(ValueError, lim.board_of, bad)
        _raises(ValueError, lim.ratio_of, bad)
        _raises(ValueError, lim.price_limits, 10.0, bad)


def test_invariant_up_above_close_above_down():
    codes = ("600519", "000001", "300750", "688981", "830799", "920091",
             "510300", "100001")
    for prev in (0.50, 1.00, 4.65, 9.99, 10.55, 33.30, 128.88, 1999.00):
        for code in codes:
            for is_st in (False, True):
                up, down = lim.price_limits(prev, code, is_st=is_st)
                assert up > prev > down, (prev, code, is_st, up, down)
                assert up > down


def test_exports_complete():
    assert set(lim.__all__) == {"board_of", "ratio_of", "price_limits", "tick_of"}, lim.__all__
    missing = [x for x in lim.__all__ if not callable(getattr(lim, x, None))]
    assert not missing, missing


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print("  ✅ %s" % name)
        except AssertionError as e:
            bad += 1
            print("  ❌ %s: %s" % (name, e))
        except Exception as e:                                     # noqa: BLE001
            bad += 1
            print("  ❌ %s: %s: %s" % (name, type(e).__name__, e))
    print("\n%d 个用例，失败 %d" % (len(fns), bad))
    sys.exit(1 if bad else 0)
