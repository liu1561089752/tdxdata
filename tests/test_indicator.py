# -*- coding: utf-8 -*-
"""`tdxdata/indicator.py` 的**离线**单元测试（零网络、零第三方依赖）。

口径全部来自 `API设计.md` §2.7「P1 指标计算」，每个断言都能手算复现：
  · `ma` / `boll`：期望值死写（自编小样本）
  · `ema` / `macd` / `kdj`：把递推式在测试里再写一遍（种子 50 / 首根收盘）
  · `rsi`：Wilder 递推 n=3 小样本，期望值死写
跑法：python tests/test_indicator.py   或   pytest tests/test_indicator.py -q
"""
from __future__ import annotations

import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tdxdata import Bar                                      # noqa: E402
from tdxdata import indicator as ind                         # noqa: E402

TOL = 1e-9


def _bars(closes, highs=None, lows=None):
    """按收盘（以及可选的高低）造 `list[Bar]`，用于喂给指标函数。"""
    highs = list(closes) if highs is None else list(highs)
    lows = list(closes) if lows is None else list(lows)
    return [Bar(market=1, code="600519", date=str(20260101 + i),
                open=c, high=h, low=l, close=c, vol=1000.0)
            for i, (c, h, l) in enumerate(zip(closes, highs, lows))]


def _eq(got, want, what=""):
    assert abs(got - want) < TOL, "%s 得到 %r，期望 %r" % (what, got, want)


# ── 1. ma：手算对账（期望值死写）────────────────────────────────────────
def test_ma_hand_computed():
    closes = [10.0, 11.0, 12.0, 13.0, 14.0]
    got = ind.ma(closes, 3)
    assert got == [None, None, 11.0, 12.0, 13.0], got
    # 手算：(10+11+12)/3=11，(11+12+13)/3=12，(12+13+14)/3=13
    assert ind.ma(closes, 1) == [10.0, 11.0, 12.0, 13.0, 14.0]
    assert ind.ma(closes, 5) == [None, None, None, None, 12.0]


# ── 2. 每个函数：输出长度 == 输入长度，且头部 None 位置精确 ──────────────
def test_length_equals_input_for_every_function():
    closes = [10.0, 11.5, 11.0, 12.5, 12.0, 13.5, 13.0, 14.5]
    m = len(closes)
    pairs = {
        "ma": ind.ma(closes, 3),
        "ema": ind.ema(closes, 3),
        "rsi": ind.rsi(closes, 3),
    }
    for name, seq in pairs.items():
        assert len(seq) == m, "%s 长度 %d != %d" % (name, len(seq), m)
    for name, seqs in (("macd", ind.macd(closes, fast=2, slow=3, signal=2)),
                       ("kdj", ind.kdj(closes, n=3)),
                       ("boll", ind.boll(closes, n=3))):
        assert len(seqs) == 3, name
        for seq in seqs:
            assert len(seq) == m, "%s 长度 %d != %d" % (name, len(seq), m)


def test_head_none_positions():
    closes = [10.0, 11.5, 11.0, 12.5, 12.0, 13.5]
    n = 3
    head = n - 1
    for seq in (ind.ma(closes, n), ind.rsi(closes, n + 1),
                ind.boll(closes, n)[0], ind.boll(closes, n)[1],
                ind.boll(closes, n)[2], ind.kdj(closes, n)[0],
                ind.kdj(closes, n)[1], ind.kdj(closes, n)[2]):
        assert all(seq[i] is None for i in range(head)), seq
    # ma / boll：前 n-1 个 None，第 n-1 个起有值
    for seq in (ind.ma(closes, n), ind.boll(closes, n)[0], ind.kdj(closes, n)[0]):
        assert seq[head - 1] is None and seq[head] is not None, seq
    # rsi：首个值在下标 n
    r = ind.rsi(closes, n)
    assert r[n - 1] is None and r[n] is not None, r
    # ema / macd：**不填 None**
    assert all(v is not None for v in ind.ema(closes, n))
    for seq in ind.macd(closes, fast=2, slow=3, signal=2):
        assert all(v is not None for v in seq), seq


# ── 3. ema：种子 = 首根收盘，alpha = 2/(n+1) ────────────────────────────
def test_ema_seed_is_first_close_and_recursion():
    closes = [10.0, 11.0, 12.0, 13.0, 14.0]
    n = 3
    alpha = 2.0 / (n + 1)
    got = ind.ema(closes, n)
    _eq(got[0], closes[0], "ema[0] 种子")
    want = closes[0]
    for i in range(1, len(closes)):
        want = alpha * closes[i] + (1.0 - alpha) * want
        _eq(got[i], want, "ema[%d]" % i)
    # 手算校验一根：0.5*11 + 0.5*10 = 10.5；0.5*12 + 0.5*10.5 = 11.25
    _eq(got[1], 10.5, "ema[1]")
    _eq(got[2], 11.25, "ema[2]")


# ── 4. macd：hist == 2*(dif-dea)，dea == ema(dif, signal) ───────────────
def test_macd_hist_is_double_dif_minus_dea():
    closes = [10.0, 11.0, 10.5, 12.0, 11.0, 13.0, 12.5, 14.0]
    dif, dea, hist = ind.macd(closes, fast=2, slow=3, signal=2)
    for i, (d1, d2, h) in enumerate(zip(dif, dea, hist)):
        _eq(h, 2.0 * (d1 - d2), "macd hist[%d] == 2*(dif-dea)" % i)


def test_macd_dif_dea_definitions():
    closes = [10.0, 11.0, 10.5, 12.0, 11.0, 13.0, 12.5, 14.0]
    fast, slow, signal = 2, 3, 2
    dif, dea, _hist = ind.macd(closes, fast=fast, slow=slow, signal=signal)
    ef = ind.ema(closes, fast)
    es = ind.ema(closes, slow)
    for i in range(len(closes)):
        _eq(dif[i], ef[i] - es[i], "dif[%d]" % i)
    want_dea = ind.ema(dif, signal)      # dea 就是 dif 的 EMA（种子 = dif[0]）
    for i in range(len(closes)):
        _eq(dea[i], want_dea[i], "dea[%d]" % i)
    _eq(dea[0], dif[0], "dea 种子")


# ── 5. kdj：首根 K/D 从 50 起算 + 递推；退化（high==low）口径 ────────────
def test_kdj_seed_50_and_recursion():
    # n=2 的小样本，可以完整手算：下标 0 只有窗口 [0]（hhv==llv → rsv=0），
    # 下标 1 窗口 [0,1]：hhv=12, llv=10, close=12 → rsv=100
    closes = [10.0, 12.0, 11.0]
    k, d, j = ind.kdj(closes, n=2)
    # 下标 0 窗口未满 → None
    assert k[0] is None and d[0] is None and j[0] is None
    # **种子 50 作用在下标 0**（窗口未满也参与递推，只是不输出）：
    #   k0 = (2*50 + 0)/3 = 33.333…，d0 = (2*50 + k0)/3 = 44.444…
    want_k0 = (2 * 50.0 + 0.0) / 3
    want_d0 = (2 * 50.0 + want_k0) / 3
    # 下标 1 由种子递推而来：k1 = (2*k0 + 100)/3
    want_k1 = (2 * want_k0 + 100.0) / 3
    want_d1 = (2 * want_d0 + want_k1) / 3
    _eq(k[1], want_k1, "k[1]")
    _eq(d[1], want_d1, "d[1]")
    _eq(j[1], 3 * want_k1 - 2 * want_d1, "j[1]")
    # 下标 2 窗口 [1,2]：hhv=12, llv=11, close=11 → rsv=0
    want_k2 = (2 * want_k1 + 0.0) / 3
    want_d2 = (2 * want_d1 + want_k2) / 3
    _eq(k[2], want_k2, "k[2]")
    _eq(d[2], want_d2, "d[2]")
    _eq(j[2], 3 * want_k2 - 2 * want_d2, "j[2]")


def test_kdj_degenerate_high_equals_low_uses_rsv_zero():
    """窗口内 HHV==LLV（全平盘）时口径：rsv 记 0（不做 0/0），K/D 从 50 逐级收敛。

    契约式子直接给出：k1=(2*50+0)/3=33.333…、k2=(2*33.333…+0)/3=22.222…、
    下标 n-1=2 处 k=14.814…（前两根输出 None）。见 `kdj` docstring。
    """
    closes = [10.0] * 5                      # high == low == close == 10.0
    k, d, j = ind.kdj(closes, n=3)
    assert k[0] is None and k[1] is None and k[2] is not None
    prev_k = prev_d = 50.0
    want = []
    for _ in range(5):
        prev_k = (2 * prev_k + 0.0) / 3
        prev_d = (2 * prev_d + prev_k) / 3
        want.append((prev_k, prev_d))
    for i in (2, 3, 4):
        _eq(k[i], want[i][0], "平盘 k[%d]" % i)
        _eq(d[i], want[i][1], "平盘 d[%d]" % i)
        _eq(j[i], 3 * want[i][0] - 2 * want[i][1], "平盘 j[%d]" % i)
    _eq(k[2], 14.814814814814817, "平盘 k[2] 死写值")
    _eq(d[2], 29.629629629629637, "平盘 d[2] 死写值")


def test_kdj_uses_high_low_not_only_close():
    closes = [10.0, 11.0, 12.0, 13.0, 14.0]
    flat = ind.kdj(_bars(closes), n=3)
    wide = ind.kdj(_bars(closes,
                         highs=[c + 5 for c in closes],
                         lows=[c - 5 for c in closes]), n=3)
    assert flat[0] != wide[0], "kdj 必须读 high/low，不能只用 close"


# ── 6. rsi：Wilder 递推 n=3 手算（期望值死写）──────────────────────────
def test_rsi_wilder_hand_computed():
    """closes=[10, 11, 10.5, 12, 11, 13]，n=3，逐根手算。

    diff: +1, -0.5, +1.5, -1, +2
    up: 1 → (0+2*1)/3=0.6666667 → (1.5+2*0.6666667)/3=0.9444444
        → (0+2*0.9444444)/3=0.6296296 → (2+2*0.6296296)/3=1.0864198
    dn: 0 → 0.1666667 → 0.1111111 → 0.4074074 → 0.2716049
    rsi[3]=89.4736842…, rsi[4]=60.7142857…, rsi[5]=80.0
    """
    closes = [10.0, 11.0, 10.5, 12.0, 11.0, 13.0]
    got = ind.rsi(closes, 3)
    assert len(got) == len(closes)
    assert got[0] is None and got[1] is None and got[2] is None, got
    _eq(got[3], 89.47368421052632, "rsi[3]")
    _eq(got[4], 60.71428571428571, "rsi[4]")
    _eq(got[5], 80.0, "rsi[5]")

    # 同一递推在测试里再写一遍（不依赖被测实现的内部结构）
    ups = [None] * len(closes)
    dns = [None] * len(closes)
    ups[1] = max(closes[1] - closes[0], 0.0)
    dns[1] = max(closes[0] - closes[1], 0.0)
    for i in range(2, len(closes)):
        diff = closes[i] - closes[i - 1]
        ups[i] = (max(diff, 0.0) + 2 * ups[i - 1]) / 3
        dns[i] = (max(-diff, 0.0) + 2 * dns[i - 1]) / 3
    for i in range(3, len(closes)):
        _eq(got[i], ups[i] / (ups[i] + dns[i]) * 100.0, "rsi[%d] 复算" % i)


def test_rsi_flat_market_is_neutral_50():
    got = ind.rsi([20.0] * 6, 3)
    assert got[:3] == [None, None, None], got
    for v in got[3:]:
        _eq(v, 50.0, "平盘 rsi")           # up+dn==0 → 中性 50（docstring 口径）


# ── 7. boll：样本标准差（分母 n-1）手算 ─────────────────────────────────
def test_boll_sample_std_hand_computed():
    """closes=[1,2,3,4,5]，n=5：mid=3，样本方差=(4+1+0+1+4)/4=2.5。

    std=sqrt(2.5)=1.5811388300841898 → upper=3+2*std=6.16227766016838，
    lower=3-2*std=-0.16227766016837952。
    """
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    upper, mid, lower = ind.boll(closes, n=5, k=2)
    assert upper[:4] == [None, None, None, None], upper
    _eq(mid[4], 3.0, "boll mid")
    _eq(upper[4], 6.16227766016838, "boll upper")
    _eq(lower[4], -0.16227766016837952, "boll lower")


def test_boll_is_sample_not_population_std():
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    upper, _mid, lower = ind.boll(closes, n=5, k=2)
    sample_std = math.sqrt(2.5)                       # 分母 n-1 = 4
    pop_std = math.sqrt(2.0)                          # 分母 n   = 5（别家的口径）
    _eq(upper[4], 3.0 + 2 * sample_std, "upper 用样本标准差")
    _eq(lower[4], 3.0 - 2 * sample_std, "lower 用样本标准差")
    assert abs(upper[4] - (3.0 + 2 * pop_std)) > 1e-3, "错用了总体标准差"


def test_boll_n1_degenerate_std_zero():
    _u, mid, _l = ind.boll([1.0, 2.0, 3.0], n=1, k=2)
    _eq(mid[0], 1.0, "n=1 mid")
    upper, mid, lower = ind.boll([1.0, 2.0, 3.0], n=1, k=2)
    _eq(upper[0], 1.0, "n=1 std=0")
    _eq(lower[0], 1.0, "n=1 std=0")


# ── 8. 输入形态：list[float] 与 list[Bar] 等价 ──────────────────────────
def test_accepts_float_list_and_bars_equivalently():
    closes = [10.0, 11.0, 10.5, 12.0, 11.0, 13.0]
    bins = _bars(closes)
    assert ind.ma(closes, 3) == ind.ma(bins, 3)
    assert ind.ema(closes, 3) == ind.ema(bins, 3)
    assert ind.rsi(closes, 3) == ind.rsi(bins, 3)
    for a, b in zip(ind.boll(closes, 3), ind.boll(bins, 3)):
        assert a == b
    for a, b in zip(ind.macd(closes), ind.macd(bins)):
        assert a == b
    # 纯数字样本（int）也要能吃
    assert ind.ma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]


# ── 9. 边界：空输入、n <= 0 ─────────────────────────────────────────────
def test_empty_input_returns_empty_sequence():
    assert ind.ma([], 5) == []
    assert ind.ema([], 5) == []
    assert ind.rsi([], 5) == []
    assert ind.macd([]) == ([], [], [])
    assert ind.kdj([]) == ([], [], [])
    assert ind.boll([]) == ([], [], [])
    # 长度 1 的输入：长度仍是 1
    assert ind.ma([10.0], 5) == [None]
    assert ind.ema([10.0], 5) == [10.0]
    assert ind.rsi([10.0], 5) == [None]


def test_non_positive_period_raises_value_error():
    closes = [1.0, 2.0, 3.0]
    cases = [
        ("ma", lambda n: ind.ma(closes, n)),
        ("ema", lambda n: ind.ema(closes, n)),
        ("rsi", lambda n: ind.rsi(closes, n)),
        ("boll", lambda n: ind.boll(closes, n)),
    ]
    for name, fn in cases:
        for bad in (0, -1, -5):
            try:
                fn(bad)
            except ValueError:
                continue
            raise AssertionError("%s(n=%r) 应抛 ValueError" % (name, bad))
    for kwargs in ({"fast": 0}, {"slow": 0}, {"signal": 0}, {"fast": -2}):
        try:
            ind.macd(closes, **kwargs)
        except ValueError:
            continue
        raise AssertionError("macd(%r) 应抛 ValueError" % (kwargs,))
    for kwargs in ({"n": 0}, {"k": 0}, {"d": 0}, {"n": -9}):
        try:
            ind.kdj(closes, **kwargs)
        except ValueError:
            continue
        raise AssertionError("kdj(%r) 应抛 ValueError" % (kwargs,))


def test_exports_complete():
    want = {"ma", "ema", "macd", "kdj", "rsi", "boll"}
    assert set(ind.__all__) == want, ind.__all__
    missing = [x for x in ind.__all__ if not callable(getattr(ind, x, None))]
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
