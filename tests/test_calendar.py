# -*- coding: utf-8 -*-
r"""PROD-002 `c.calendar` 交易日历的守卫测试（零真实网络）。

守五件事
--------
1. **事实源两路**：单点判定走 `get_bars("999999", count=N)` 假序列；
   历史区间走 `get_breadth("880005", count=N)` 的 `date` 列。两个 mock 都
   用假数据，**一个 socket 都不建**。
2. **prev/next 跨周末与节假日**：用假日线序列验证"跨过周六日"与"跨过长假"。
3. **is_trading_day 真假两态**：在序列里 = True；不在 = False；
   解析不出 = None + last_error（**不猜**）。
4. **幂等**：同参数二次调用**零新请求**（fetch 计数断言）。
5. **零请求硬约束**：`clock_probe=False` / 裸 Client → 四个方法全不发请求
   （计数恒 0），返回 None 并在 `last_error` 记原因；`_prev_trade_date`
   收编后原有调用方（timeliness 判定链）行为不变 —— 全量测试兜底。

红线：`tests/test_*.py` 不许出现 `Client(` / `Session(` 字面量 —— 裸对象用
`getattr(tdxdata, "Client")` + `cls.__new__(cls)` 造。
"""
from __future__ import annotations

import sys
from datetime import date, datetime

import pytest

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                        # noqa: BLE001
    pass

import tdxdata
from tdxdata.calendar import TradeCalendar, _to_date

_CLS = getattr(tdxdata, "Client")          # 红线：不写 Client( 字面量

#: 假日线事实（2026-10 真实形态：国庆调休 + 周末）。
#: 10-1..10-8 国庆长假（10-9 周五恢复），10-10(周六)调休上班，10-11(周日)休。
MOCK_BARS_DAYS = [
    "20260928", "20260929", "20260930",          # 周一二（节前）
    "20261009", "20261010",                      # 周五 + 调休周六
    "20261012", "20261013", "20261014", "20261015", "20261016",
    "20261019", "20261020", "20261021", "20261022", "20261023",
]


def _bar_of(day: str):
    """造一根只带 `date` 的假 Bar（日历只读这一个字段）。"""
    from types import SimpleNamespace

    return SimpleNamespace(date=day, open=1.0, high=1.0, low=1.0, close=1.0)


def _breadth_row_of(day: str) -> dict:
    return {"date": day, "up_count": 1, "down_count": 1, "flat_count": 0,
            "limit_up_count": 0, "limit_down_count": 0, "provisional": False}


class _FakeBarsClient:
    """`get_bars` 返回假序列；`get_breadth` 返回假广度表。计数可断言。"""

    def __init__(self, days=MOCK_BARS_DAYS):
        self.days = list(days)
        self.bars_calls: list[int] = []
        self.breadth_calls: list[int] = []
        self._probe_allowed = True

    def get_bars(self, symbol, period="day", count=2, **kw):
        self.bars_calls.append(int(count))
        # 服务端语义：回最近 count 根（升序）
        return [_bar_of(d) for d in self.days[-count:]]

    def get_breadth(self, code, count=30, **kw):
        self.breadth_calls.append(int(count))
        from tdxdata.models import Table

        return Table(cols=["date"], rows=[_breadth_row_of(d) for d in self.days[-count:]])


def _cal(client=None) -> TradeCalendar:
    return TradeCalendar(client if client is not None else _FakeBarsClient())


# ══════════════════════════════════════════════════════════════════════════
# 一、_to_date 宽松解析
# ══════════════════════════════════════════════════════════════════════════
def test_to_date_accepts_date_datetime_and_strings():
    assert _to_date(date(2026, 10, 23)) == date(2026, 10, 23)
    assert _to_date(datetime(2026, 10, 23, 15, 0)) == date(2026, 10, 23)
    assert _to_date("2026-10-23") == date(2026, 10, 23)
    assert _to_date("20261023") == date(2026, 10, 23)
    assert _to_date("garbage") is None            # 不猜
    assert _to_date(None) is None


# ══════════════════════════════════════════════════════════════════════════
# 二、prev / next 跨周末与节假日
# ══════════════════════════════════════════════════════════════════════════
def test_prev_skips_weekend():
    """prev(周六) == 周五；prev(周一) == 上周五（序列里的前一个交易日）。"""
    c = _cal()
    # 10-17 是周六；序列里它前一个交易日是 10-16（周五）
    assert c.prev(date(2026, 10, 17)) == date(2026, 10, 16)
    # 10-12（周一）的前一个交易日是 10-10（调休周六！）
    assert c.prev(date(2026, 10, 12)) == date(2026, 10, 10)


def test_prev_skips_long_holiday():
    """prev(10-09 节后首日) == 09-30（跨过整个国庆长假）。"""
    c = _cal()
    assert c.prev(date(2026, 10, 9)) == date(2026, 9, 30)


def test_next_skips_weekend_and_holiday():
    c = _cal()
    # next(10-16 周五) == 10-17？不 —— 10-17 是周六：序列里下一个是 10-19（周一）
    assert c.next(date(2026, 10, 16)) == date(2026, 10, 19)
    # next(09-30 节前最后一天) == 10-09（跨过国庆）
    assert c.next(date(2026, 9, 30)) == date(2026, 10, 9)


def test_prev_exclusive_of_anchor_itself():
    """prev(d) **不含** d：d 是交易日时，返回的仍是 d 之前那一个。"""
    c = _cal()
    assert c.prev(date(2026, 10, 23)) == date(2026, 10, 22)


# ══════════════════════════════════════════════════════════════════════════
# 三、is_trading_day 真假两态
# ══════════════════════════════════════════════════════════════════════════
def test_is_trading_day_true_false_and_holiday():
    c = _cal()
    assert c.is_trading_day(date(2026, 10, 23)) is True      # 周五交易日
    assert c.is_trading_day(date(2026, 10, 17)) is False     # 周六（且非调休）
    assert c.is_trading_day(date(2026, 10, 5)) is False      # 国庆假期
    assert c.is_trading_day(date(2026, 10, 10)) is True      # 调休上班的周六！


def test_is_trading_day_unparseable_is_none_not_false():
    """解析不出 → None + last_error（None ≠ False：回答不了 ≠ 回答"不是"）。"""
    c = _cal()
    assert c.is_trading_day("not-a-date") is None
    assert c.last_error


# ══════════════════════════════════════════════════════════════════════════
# 四、range 含头含尾 + a>b 报错
# ══════════════════════════════════════════════════════════════════════════
def test_range_is_inclusive_both_ends_and_sorted():
    c = _cal()
    got = c.range("2026-10-09", "2026-10-16")
    assert got == [date(2026, 10, 9), date(2026, 10, 10),
                   date(2026, 10, 12), date(2026, 10, 13),
                   date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)]


def test_range_weekend_only_is_empty_list_not_none():
    """周末区间 = 空列表（真回答了"没有交易日"），不是 None（回答不了）。"""
    c = _cal()
    assert c.range("2026-10-17", "2026-10-18") == []
    assert c.last_error is None


def test_range_reversed_raises_value_error():
    c = _cal()
    with pytest.raises(ValueError):
        c.range("2026-10-16", "2026-10-09")


def test_range_uses_breadth_channel():
    """历史区间走 get_breadth（广度单日一行），**不**打 get_bars。"""
    fc = _FakeBarsClient()
    c = TradeCalendar(fc)
    c.range("2026-10-12", "2026-10-16")
    assert fc.breadth_calls and not fc.bars_calls


# ══════════════════════════════════════════════════════════════════════════
# 五、幂等：同参数二次调用零新请求
# ══════════════════════════════════════════════════════════════════════════
def test_same_query_twice_costs_zero_extra_requests():
    fc = _FakeBarsClient()
    c = TradeCalendar(fc)
    a1 = c.prev(date(2026, 10, 23))
    n_after_first = fc.bars_calls.__len__()
    a2 = c.prev(date(2026, 10, 23))
    assert a1 == a2 == date(2026, 10, 22)
    assert len(fc.bars_calls) == n_after_first        # 零新请求


def test_is_trading_day_then_prev_shares_window():
    """同一天先判 is_trading_day 再 prev：窗口事实可复用（不重复扩窗）。"""
    fc = _FakeBarsClient()
    c = TradeCalendar(fc)
    c.is_trading_day(date(2026, 10, 23))
    n1 = len(fc.bars_calls)
    c.prev(date(2026, 10, 23))
    # prev 用不同锚点（d-1）→ 允许一次新取数；但绝不该翻倍扩窗风暴
    assert len(fc.bars_calls) <= n1 + 1


# ══════════════════════════════════════════════════════════════════════════
# 六、零请求硬约束（clock_probe=False / 裸 Client）
# ══════════════════════════════════════════════════════════════════════════
def test_probe_disabled_means_zero_requests_and_none():
    fc = _FakeBarsClient()
    fc._probe_allowed = False
    c = TradeCalendar(fc)
    assert c.is_trading_day(date(2026, 10, 23)) is None
    assert c.prev(date(2026, 10, 23)) is None
    assert c.next(date(2026, 10, 23)) is None
    assert c.range("2026-10-09", "2026-10-16") is None
    assert fc.bars_calls == [] and fc.breadth_calls == []   # **一次请求都没发**
    assert c.last_error                                      # 原因必须写出来


def test_bare_client_zero_requests():
    """`Client.__new__` 裸对象：没有 _probe_allowed → 同样零请求。"""
    c = _CLS.__new__(_CLS)
    cal = TradeCalendar(c)
    assert cal.prev(date(2026, 10, 23)) is None
    assert cal.fetches == 1                 # 计数发生在探测前（但请求没发出）
    assert "零请求" in (cal.last_error or "")


def test_raise_if_unavailable_turns_none_into_error():
    c = _cal()
    c.is_trading_day("junk")
    with pytest.raises(RuntimeError):
        c.raise_if_unavailable()


# ══════════════════════════════════════════════════════════════════════════
# 七、窗口扩展判据
# ══════════════════════════════════════════════════════════════════════════
def test_window_expands_until_anchor_covered():
    """锚点比首根旧 → 窗口翻倍（2→4→…）直到盖住；起步 2 与旧探针逐位一致。"""
    fc = _FakeBarsClient()
    c = TradeCalendar(fc)
    c.prev(date(2026, 9, 29))               # 序列第 2 根 —— 2 根窗口（09-29/30）盖得住
    assert fc.bars_calls[0] == 2            # 起步窗 = 2（收编前 get_bars(count=2) 同形）


def test_prev_beyond_window_expands_and_fails_honestly():
    """锚点在窗口外且扩到上限仍盖不住 → None + last_error，绝不编日期。"""
    fc = _FakeBarsClient(days=["20261022", "20261023"])   # 只有 2 根
    c = TradeCalendar(fc)
    # prev(09-01)：窗口盖不住 8 月末的交易日 → 扩窗也只拿到同样 2 根 → None
    assert c.prev(date(2026, 9, 1)) is None
    assert c.last_error and "不猜" in c.last_error


# ══════════════════════════════════════════════════════════════════════════
# 八、_prev_trade_date 收编后行为不变（timeliness 判定链）
# ══════════════════════════════════════════════════════════════════════════
def test_client_prev_trade_date_delegates_to_calendar():
    """`_prev_trade_date` 委托 calendar.prev（同一事实源），日缓存仍生效。"""
    c = _CLS.__new__(_CLS)
    c._probe_allowed = True
    fc = _FakeBarsClient()
    c.calendar = TradeCalendar(fc)
    c._cal = {}
    c._in_cal = False
    c._clock = None

    class _LocalClock:
        note = None

        def now(self):
            from tdxdata.timeliness import NowInfo

            return NowInfo(datetime(2026, 10, 27, 16, 0), "server", "")

    c._clock = _LocalClock()
    got = c._prev_trade_date()
    # 假序列最后一根是 10-23：锚点 10-26 在窗口外（比最新数据还新），
    # 日历如实回"窗口里 <10-26 的最大交易日"= 10-23（不编 10-26 附近的日期）。
    assert got == date(2026, 10, 23)
    # 日缓存：同日第二次零请求
    n = len(fc.bars_calls)
    c._prev_trade_date()
    assert len(fc.bars_calls) == n


def test_client_prev_trade_date_probe_off_means_none_and_no_fetch():
    """clock_probe=False（硬约束）：_prev_trade_date → None，日历一次请求不发。"""
    c = _CLS.__new__(_CLS)
    c._probe_allowed = False
    fc = _FakeBarsClient()
    c.calendar = TradeCalendar(fc)
    c._cal = {}
    c._in_cal = False
    assert c._prev_trade_date() is None
    assert fc.bars_calls == [] and fc.breadth_calls == []


def test_calendar_mounted_on_real_init_path():
    """跑过 __init__ 的 Client 有 `self.calendar`（懒取数，挂载即零请求）。"""
    import inspect

    src = inspect.getsource(_CLS.__init__)
    assert "self.calendar = TradeCalendar(self)" in src
    assert "懒取数" in src
