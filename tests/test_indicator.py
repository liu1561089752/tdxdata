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
    want = {"ma", "ema", "macd", "kdj", "rsi", "boll",
            "hv", "momentum", "sharpe", "max_drawdown", "beta"}
    assert set(ind.__all__) == want, ind.__all__
    missing = [x for x in ind.__all__ if not callable(getattr(ind, x, None))]
    assert not missing, missing


# ═══════════════ 阶段 C 增补：确定性数学指标（HV/动量/夏普/回撤/Beta）═══════════
# 期望值全部**手算死写**或写出手工推导的闭式（不复用被测实现的中间量）：
#   · momentum / max_drawdown / beta：小样本逐位死写
#   · hv / sharpe：两点样本 std = |a-b|/√2 推导出的闭式
# ── 10. momentum：手算（百分数）──────────────────────────────────────────
def test_momentum_hand_computed():
    closes = [1.0, 2.0, 4.0, 8.0]
    # n=1：(2/1-1)*100=100，(4/2-1)*100=100，(8/4-1)*100=100
    assert ind.momentum(closes, 1) == [None, 100.0, 100.0, 100.0]
    # n=2：(4/1-1)*100=300，(8/2-1)*100=300
    assert ind.momentum(closes, 2) == [None, None, 300.0, 300.0]
    # 横盘 → 0.0（算出来是 0，不是 None）
    assert ind.momentum([5.0, 5.0, 5.0], 1) == [None, 0.0, 0.0]


# ── 11. max_drawdown：手算（百分数，≤ 0）─────────────────────────────────
def test_max_drawdown_hand_computed():
    closes = [10.0, 8.0, 12.0, 6.0]
    got = ind.max_drawdown(closes, 4)
    assert got[:3] == [None, None, None], got
    _eq(got[3], -50.0, "mdd n=4：窗口[10,8,12,6]，峰值 12，末位 6/12-1=-50%")

    got2 = ind.max_drawdown(closes, 2)
    assert got2[0] is None, got2
    _eq(got2[1], -20.0, "mdd i=1 窗口[10,8]：8/10-1=-20%")
    _eq(got2[2], 0.0, "mdd i=2 窗口[8,12]：只创新高 → 0%")
    _eq(got2[3], -50.0, "mdd i=3 窗口[12,6]：6/12-1=-50%")

    # 全程创新高 → 0.0（不是 None）
    assert ind.max_drawdown([1.0, 2.0, 3.0], 2) == [None, 0.0, 0.0]
    # n=1：窗口只有一根，回撤恒 0.0；且从下标 0 起有值
    assert ind.max_drawdown([1.0, 2.0], 1) == [0.0, 0.0]


# ── 12. hv：样本标准差（分母 n-1）× √252 × 100 ──────────────────────────
def test_hv_hand_computed():
    import pytest                       # 局部导入：保持本文件「独立脚本可跑」

    closes = [100.0, 110.0, 99.0, 108.0]
    got = ind.hv(closes, 2)
    assert got[:2] == [None, None], got
    # 手算：r = [ln(110/100), ln(99/110), ln(108/99)] = [ln(11/10), ln(9/10), ln(12/11)]
    #   i=2 窗口 (r1, r2)，两点样本 std = |r1-r2|/√2 = ln(11/9)/√2
    #   hv[2] = ln(11/9)/√2 × √252 × 100 = 100·ln(11/9)·√126 ≈ 225.2522969955066
    assert got[2] == pytest.approx(100.0 * math.log(11.0 / 9.0) * math.sqrt(126.0),
                                   rel=1e-9)
    assert got[2] == pytest.approx(225.25229699550664, rel=1e-9)
    #   i=3 窗口 (r2, r3)，std = |r3-r2|/√2 = ln(40/33)/√2
    #   hv[3] = 100·ln(40/33)·√126 ≈ 215.9369139396112
    assert got[3] == pytest.approx(100.0 * math.log(40.0 / 33.0) * math.sqrt(126.0),
                                   rel=1e-9)
    assert got[3] == pytest.approx(215.93691393961117, rel=1e-9)

    # 平盘 → 波动率 0.0（不是 None）
    assert ind.hv([5.0] * 4, 2) == [None, None, 0.0, 0.0]
    # 年化因子确实是 √252：同样收益序列，hv 与「样本 std × √252 × 100」一致
    assert got[2] > 100.0, "年化+百分数换算后量级应上百"


# ── 13. sharpe：手算（均值/rf/样本 std）──────────────────────────────────
def test_sharpe_hand_computed():
    import pytest

    closes = [100.0, 110.0, 99.0]
    got = ind.sharpe(closes, 2)
    assert got[:2] == [None, None], got
    # r = [0.1, -0.1] → mean = 0，样本 std = √((0.01+0.01)/1) = √0.02
    # sharpe = (0 - 0)/√0.02 × √252 = 0
    # 注意：浮点里 110/100-1 并非精确 0.1，mean 实测 ~5.6e-17，
    # 故结果 ~6.2e-15（**不是 None**，量级为 0）。语义判据是 "0 而非 None"。
    assert got[2] is not None, got
    assert got[2] == pytest.approx(0.0, abs=1e-12), got
    # 用**精确可表示**的收益（±0.5）再验一次：此处 sharpe 恰好是 0.0
    assert ind.sharpe([100.0, 150.0, 75.0], 2)[2] == 0.0
    assert ind.sharpe([100.0, 150.0, 75.0], 2)[2] is not None

    # rf 年化 2.52%（小数 0.0252）：
    #   (0 - 0.0252/252)/√0.02 × √252 = -0.0252/√5.04 ≈ -0.0112249721603218
    rf = 0.0252
    got_rf = ind.sharpe(closes, 2, rf=rf)
    assert got_rf[2] == pytest.approx(-rf / math.sqrt(5.04), rel=1e-9)
    assert got_rf[2] == pytest.approx(-0.011224972160321823, rel=1e-9)
    assert got_rf[2] < 0.0, "有 rf 时同一零均值窗口夏普应为负"

    # 零波动（收益全等）→ None，不用 inf 顶替
    assert ind.sharpe([10.0, 10.0, 10.0], 2) == [None, None, None]


# ── 14. beta：对基准的 OLS 斜率 ──────────────────────────────────────────
def test_beta_hand_computed():
    closes = [1.0, 2.0, 3.0, 4.0]
    same = ind.beta(closes, closes, 2)
    assert same[:2] == [None, None], same
    for i in (2, 3):
        _eq(same[i], 1.0, "beta 对自身（cov=var）必为 1.0，i=%d" % i)

    # 基准收益恰为个股收益的 2 倍 → cov 翻倍、var 翻 4 倍 → beta = 0.5
    # 个股收益 [1.0, 0.5, 1/3]；基准 [1→3→6→10] 收益 [2.0, 1.0, 2/3]
    market = [1.0, 3.0, 6.0, 10.0]
    got = ind.beta(closes, market, 2)
    _eq(got[2], 0.5, "beta i=2（2 倍基准）")
    _eq(got[3], 0.5, "beta i=3（2 倍基准）")

    # 基准恒值 → 市场收益全 0、var=0 → None（不用 inf 顶替）
    assert ind.beta(closes, [7.0] * 4, 2) == [None, None, None, None]

    # 长度不等 → ValueError，且消息说明「调用方负责日历对齐」
    try:
        ind.beta(closes, [1.0, 2.0], 2)
    except ValueError as e:
        assert "日历对齐" in str(e), e
    else:
        raise AssertionError("个股与基准不等长应抛 ValueError")


# ── 15. 新指标统一契约：长度 ≡ 输入长度、头部 None 位精确 ────────────────
def test_new_functions_length_and_head_none():
    closes = [10.0, 11.5, 11.0, 12.5, 12.0, 13.5, 13.0, 14.5]
    m = len(closes)
    market = [3000.0 + 10.0 * i for i in range(m)]
    for n in (2, 3, 5):
        cases = {
            "hv": ind.hv(closes, n),
            "momentum": ind.momentum(closes, n),
            "sharpe": ind.sharpe(closes, n),
            "max_drawdown": ind.max_drawdown(closes, n),
            "beta": ind.beta(closes, market, n),
        }
        for name, seq in cases.items():
            assert len(seq) == m, "%s(n=%d) 长度 %d != %d" % (name, n, len(seq), m)
        # hv / momentum / sharpe / beta：首个有值下标 = n（窗口要 n 个收益）
        for name in ("hv", "momentum", "sharpe", "beta"):
            seq = cases[name]
            assert all(seq[i] is None for i in range(n)), (name, n, seq)
            assert seq[n] is not None, (name, n, seq)
        # max_drawdown：首个有值下标 = n-1（窗口 n 根价格）
        mdd = cases["max_drawdown"]
        assert all(mdd[i] is None for i in range(n - 1)), (n, mdd)
        assert mdd[n - 1] is not None, (n, mdd)


def test_new_functions_accept_bars():
    closes = [10.0, 11.0, 10.5, 12.0, 11.0, 13.0]
    bins = _bars(closes)
    assert ind.hv(closes, 2) == ind.hv(bins, 2)
    assert ind.momentum(closes, 2) == ind.momentum(bins, 2)
    assert ind.sharpe(closes, 2) == ind.sharpe(bins, 2)
    assert ind.max_drawdown(closes, 2) == ind.max_drawdown(bins, 2)
    assert ind.beta(closes, closes, 2) == ind.beta(bins, bins, 2)


def test_new_functions_empty_and_single_input():
    assert ind.hv([], 20) == []
    assert ind.momentum([], 20) == []
    assert ind.sharpe([], 20) == []
    assert ind.max_drawdown([], 20) == []
    assert ind.beta([], [], 20) == []
    # 长度 1：窗口永远填不满 → [None]（长度仍是 1，不截断）
    assert ind.hv([10.0], 2) == [None]
    assert ind.momentum([10.0], 2) == [None]
    assert ind.sharpe([10.0], 2) == [None]
    assert ind.max_drawdown([10.0], 2) == [None]
    assert ind.beta([10.0], [10.0], 2) == [None]


def test_new_functions_invalid_period_raises():
    closes = [1.0, 2.0, 3.0, 4.0]
    cases = [
        ("hv", lambda n: ind.hv(closes, n)),
        ("momentum", lambda n: ind.momentum(closes, n)),
        ("sharpe", lambda n: ind.sharpe(closes, n)),
        ("max_drawdown", lambda n: ind.max_drawdown(closes, n)),
        ("beta", lambda n: ind.beta(closes, closes, n)),
    ]
    for name, fn in cases:
        for bad in (0, -1, -5):
            try:
                fn(bad)
            except ValueError:
                continue
            raise AssertionError("%s(n=%r) 应抛 ValueError" % (name, bad))
    # hv / sharpe / beta：样本口径至少需要 2 个收益 → n==1 也非法
    for name, fn in (("hv", lambda n: ind.hv(closes, n)),
                     ("sharpe", lambda n: ind.sharpe(closes, n)),
                     ("beta", lambda n: ind.beta(closes, closes, n))):
        try:
            fn(1)
        except ValueError:
            continue
        raise AssertionError("%s(n=1) 应抛 ValueError（样本口径需 ≥2 个收益）" % name)


# ── 16. client 接线：_indicator_columns 认识新指标名，默认全集不变 ────────
def test_indicator_columns_supports_new_names():
    from tdxdata.client import _indicator_columns
    import tdxdata.indicator as ind

    cols = _indicator_columns(ind, [1.0, 2.0, 3.0, 4.0],
                              {"hv": {}, "momentum": {"n": 1}})
    names = [c for c, _ in cols]
    assert names == ["hv20", "momentum1"], names
    # 默认全集不变：spec=None 仍是修复前那 6 个指标的展开列
    default_names = [c for c, _ in _indicator_columns(ind, [1.0, 2.0, 3.0, 4.0], None)]
    assert default_names == [
        "ma5", "ma10", "ma20", "ema12", "ema26",
        "dif", "dea", "macd", "k", "d", "j", "rsi14",
        "boll_upper", "boll_mid", "boll_lower",
    ], default_names
    assert "hv20" not in default_names and "momentum20" not in default_names
    # 新指标可点名，且默认参数可被覆盖 / 给多个周期
    many = _indicator_columns(ind, [1.0, 2.0, 3.0, 4.0, 5.0],
                              {"max_drawdown": {"n": [3, 5]}, "sharpe": {"n": 3}})
    assert [c for c, _ in many] == ["max_drawdown3", "max_drawdown5", "sharpe3"], many
    for col, seq in many:
        assert len(seq) == 5, (col, seq)
    # 错误消息里应列出**全部**可点名指标（含阶段 C 新增）
    try:
        _indicator_columns(ind, [1.0, 2.0], {"nosuch": {}})
    except ValueError as e:
        msg = str(e)
        for want in ("hv", "momentum", "sharpe", "max_drawdown"):
            assert want in msg, (want, msg)
    else:
        raise AssertionError("不认识的指标名应抛 ValueError")


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
