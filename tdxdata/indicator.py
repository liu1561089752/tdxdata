# -*- coding: utf-8 -*-
"""技术指标 —— **纯本地计算，零依赖、零网络**。

契约来源：`API设计.md` §2.7「P1 指标计算 `tdxdata/indicator.py`」（唯一契约）。

设计取向
  · 只用标准库 `math`，**禁止 numpy/pandas**（本库要能在裸 Python 里跑）
  · **输出长度恒等于输入长度**，头部不足周期的位置填 `None` —— 不截断。
    这样指标序列可以和 `Bar` 列表**按下标一一对齐**（`client.get_indicators`
    就是靠这一点把指标拼成表格列）。
  · `None` 与 `0.0` 严格区分：`None` = "周期不够，没算"，`0.0` = "算出来是 0"。

输入
  `bars` 既可以是 `list[Bar]`（用其 `close`；`kdj` 用 `high`/`low`），
  也可以是 `list[float]`（视为收盘序列，此时 `high = low = close`）。
"""
from __future__ import annotations

import math

__all__ = ["ma", "ema", "macd", "kdj", "rsi", "boll"]


# ── 内部工具 ─────────────────────────────────────────────────────────────
def _period(n, name: str = "n") -> int:
    """校验周期参数：必须是正整数，`n <= 0` → `ValueError`（契约边界）。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        raise ValueError("%s 必须是正整数，收到 %r" % (name, n)) from None
    if n <= 0:
        raise ValueError("%s 必须 > 0，收到 %r" % (name, n))
    return n


def _closes(bars) -> list[float]:
    """取收盘序列：`Bar.close`，数字元素直接用。"""
    out = []
    for b in bars:
        out.append(float(getattr(b, "close", b)))
    return out


def _highs_lows(bars) -> tuple[list[float], list[float]]:
    """取 (最高, 最低) 序列；元素是纯数字时两者都等于它（= 收盘）。"""
    highs, lows = [], []
    for b in bars:
        if hasattr(b, "high"):
            highs.append(float(b.high))
            lows.append(float(b.low))
        else:
            v = float(b)
            highs.append(v)
            lows.append(v)
    return highs, lows


def _ema_series(values: list[float], n: int) -> list[float]:
    """对一串 float 做 EMA，种子 = 首值，**不填 None**（通达信口径）。"""
    if not values:
        return []
    alpha = 2.0 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1.0 - alpha) * out[-1])
    return out


# ── 1. 简单移动平均 ───────────────────────────────────────────────────────
def ma(bars, n) -> list:
    """简单移动平均 `MA(close, n)`。

    公式：`ma[i] = (close[i-n+1] + … + close[i]) / n`。
    口径：前 `n-1` 个位置填 `None`（窗口不足），之后逐个算术平均。
    单位：元（与 `close` 同）。
    """
    n = _period(n)
    c = _closes(bars)
    out: list = [None] * len(c)
    total = 0.0
    for i, v in enumerate(c):
        total += v
        if i >= n:
            total -= c[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


# ── 2. 指数移动平均 ───────────────────────────────────────────────────────
def ema(bars, n) -> list:
    """指数移动平均 `EMA(close, n)`。

    公式：`alpha = 2/(n+1)`；`ema[0] = close[0]`（**种子取首根收盘**）；
    `ema[i] = alpha*close[i] + (1-alpha)*ema[i-1]`。
    口径：**不填 `None`** —— 种子在第一根就有值，全程有值（通达信口径）。
    单位：元。

    注意：因为种子是首根收盘而不是 `n` 根均值，`len(bars) < n` 时
    `ema` 仍有值（这是契约明确规定的行为，不是 bug）。
    """
    n = _period(n)
    return _ema_series(_closes(bars), n)


# ── 3. MACD ──────────────────────────────────────────────────────────────
def macd(bars, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD（通达信口径），返回 `(dif, dea, hist)` 三个**等长**序列。

    公式：
      · `dif  = ema(close, fast) - ema(close, slow)`
      · `dea  = ema(dif, signal)`
      · `hist = 2 * (dif - dea)`  ← **通达信 MACD 柱已乘 2**（与东财/同花顺对得上）
    口径：由 `ema` 派生，三者**都不填 `None`**，长度恒等于输入长度。
    单位：元（价格差，不是百分比）。
    """
    fast = _period(fast, "fast")
    slow = _period(slow, "slow")
    signal = _period(signal, "signal")
    c = _closes(bars)
    if not c:
        return [], [], []
    ef = _ema_series(c, fast)
    es = _ema_series(c, slow)
    dif = [a - b for a, b in zip(ef, es)]
    dea = _ema_series(dif, signal)
    hist = [2.0 * (a - b) for a, b in zip(dif, dea)]
    return dif, dea, hist


# ── 4. KDJ ───────────────────────────────────────────────────────────────
def kdj(bars, n: int = 9, k: int = 3, d: int = 3):
    """KDJ（通达信口径），返回 `(k_line, d_line, j_line)` 三个**等长**序列。

    公式（`k`/`d` 是平滑分母，默认 3 → 权重 2/3）：
      · `rsv = (close - LLV(low, n)) / (HHV(high, n) - LLV(low, n)) * 100`
      · `k_line = ((k-1)*前k + rsv) / k`   # k=3 时即 `(2*前k + rsv)/3`
      · `d_line = ((d-1)*前d + k_line) / d`
      · `j_line = 3*k_line - 2*d_line`
    口径：
      · **K/D 种子取 50**，从第一根 bar 起递推（`k/d` 参数为 1 时退化成 `= rsv`）。
      · 窗口不足时 LLV/HHV **用已有部分**（`[max(0, i-n+1), i]`）计算 rsv，
        但**输出**只在窗口填满（`i >= n-1`）处给值，之前填 `None`。
      · **退化口径（`HHV == LLV`，即窗口内最高=最低）**：分母为 0 时
        `rsv = 0.0`（不做 0/0 除法，也不沿用前值）。这样全平盘会得到
        `k → 33.33 → 22.22 → …` 逐级向 0 收敛的确定值；写成契约式子
        `k=(2*50+0)/3` 即可复现。
    单位：无量纲（0~100 为主，`j` 可越界，可为负）。
    """
    n = _period(n)
    kp = _period(k, "k")
    dp = _period(d, "d")
    closes = _closes(bars)
    highs, lows = _highs_lows(bars)
    m = len(closes)
    if m == 0:
        return [], [], []

    k_line: list = [None] * m
    d_line: list = [None] * m
    j_line: list = [None] * m
    prev_k = 50.0
    prev_d = 50.0
    for i in range(m):
        lo = max(0, i - n + 1)
        hhv = max(highs[lo:i + 1])
        llv = min(lows[lo:i + 1])
        span = hhv - llv
        rsv = 0.0 if span <= 0 else (closes[i] - llv) / span * 100.0
        cur_k = ((kp - 1) * prev_k + rsv) / kp
        cur_d = ((dp - 1) * prev_d + cur_k) / dp
        prev_k, prev_d = cur_k, cur_d
        if i >= n - 1:                      # 窗口未满的位置保持 None
            k_line[i] = cur_k
            d_line[i] = cur_d
            j_line[i] = 3.0 * cur_k - 2.0 * cur_d
    return k_line, d_line, j_line


# ── 5. RSI ───────────────────────────────────────────────────────────────
def rsi(bars, n: int = 14) -> list:
    """相对强弱指标 RSI（**Wilder 递推**口径）。

    公式（`diff[i] = close[i] - close[i-1]`）：
      · `up[i] = (max(diff[i], 0) + (n-1)*up[i-1]) / n`
      · `dn[i] = (max(-diff[i], 0) + (n-1)*dn[i-1]) / n`
      · `rsi[i] = up[i] / (up[i] + dn[i]) * 100`
    口径：
      · 递推种子 `up[1] = max(diff[1], 0)`、`dn[1] = max(-diff[1], 0)`。
      · **首个 rsi 出现在下标 `n`**，之前（含下标 0）填 `None`；
        长度 1 的输入返回 `[None]`。
      · 退化：`up + dn == 0`（区间内完全平盘）时 `rsi = 50.0`（中性）。
    单位：无量纲（0~100）。
    """
    n = _period(n)
    c = _closes(bars)
    m = len(c)
    if m == 0:
        return []
    out: list = [None] * m
    if m == 1:
        return out

    ups: list = [None] * m
    dns: list = [None] * m
    ups[1] = max(c[1] - c[0], 0.0)
    dns[1] = max(c[0] - c[1], 0.0)
    for i in range(2, m):
        diff = c[i] - c[i - 1]
        ups[i] = (max(diff, 0.0) + (n - 1) * ups[i - 1]) / n
        dns[i] = (max(-diff, 0.0) + (n - 1) * dns[i - 1]) / n
    for i in range(n, m):
        u = ups[i]
        dn = dns[i]
        total = u + dn
        out[i] = 50.0 if total == 0 else u / total * 100.0
    return out


# ── 6. 布林带 ─────────────────────────────────────────────────────────────
def boll(bars, n: int = 20, k=2):
    """布林带 `BOLL(close, n, k)`，返回 `(upper, mid, lower)` 三个**等长**序列。

    公式：
      · `mid   = ma(close, n)`
      · `std   = 窗口内的`**样本标准差（分母 `n-1`）**（通达信 `STD` 口径，
        不是总体标准差 `n` —— 这点最容易和别家实现对不上）
      · `upper = mid + k*std`、`lower = mid - k*std`
    口径：前 `n-1` 个位置填 `None`（`mid` 为 `None`，上下轨同样 `None`）。
    `n == 1` 时样本标准差无定义（分母 0），退化为 `std = 0.0` → 上下轨 = mid。
    单位：元。
    """
    n = _period(n)
    kmul = float(k)
    c = _closes(bars)
    m = len(c)
    if m == 0:
        return [], [], []
    mid = ma(c, n)
    upper: list = [None] * m
    lower: list = [None] * m
    for i in range(m):
        mu = mid[i]
        if mu is None:
            continue
        if n == 1:
            std = 0.0
        else:
            win = c[i - n + 1:i + 1]
            var = sum((x - mu) ** 2 for x in win) / (n - 1)
            std = math.sqrt(var)
        upper[i] = mu + kmul * std
        lower[i] = mu - kmul * std
    return upper, mid, lower
