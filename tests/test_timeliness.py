# -*- coding: utf-8 -*-
r"""时效判定（**离线**）：假时钟把"盘中 / 收盘 / 周末"钉死，边界可断言。

为什么这批用例值得单独存在
--------------------------
本项目唯一被实证造成**错误结论**的缺陷就是时效：同一交易日内，14:44 盘中取两市
成交额 13,130.88 亿，收盘后是 14,609.58 亿（+11.3%），"60 日最低"的结论直接反转。
根因是"拿盘中的数当收盘数"。所以规则必须是**可注入假时钟的代码**，
边界（15:34:59 / 15:35:00）必须能被断言，而不是散在注释里。

本文件守三件事
--------------
1. **判定规则**：日线当根 / 分钟当根 / 历史根 / 周末 / T+1 产品 / 涨停清单。
2. **时钟来源**（`Provenance.clock` 一等字段）：探针成功 → `SERVER` 且 `fetched_at`
   就是探针给的时刻；探针失败 → `LOCAL` 且降级 note 非空。
   ⚠️ 这一格**黄金样本原理上兜不住**：离线回放里探针永远失败，
   `fetch_clock` 恒为 `local`，"探针永久失效"在回放里与正常态完全同形 ——
   所以必须由这里守着（`tests/test_golden_contract.py` 里有一条静态守卫
   会核对白名单里的 `fetch_clock` / `clock_note` / `status_note` / `as_of_note`
   确实出现在某个 `tests/test_*.py` 的源码里）。
3. **不确定要更保守**：时钟不可信 / 拿不到上一交易日时，结论只能是
   `provisional` / `delayed` / `pending`，**绝不许**升级成 `final`。

红线（`_dev/check.py` 第 7 条离线扫 `tests/test_*.py`）
-------------------------------------------------------
不许出现 `Client(` / `Session(` 这种"看起来要真建连"的字面量，所以裸对象用
`getattr(tdxdata, "Client")` + `cls.__new__(cls)` 造；探针/行情一律用假实现，
**一个 socket 都不建**。
"""
from __future__ import annotations

import dataclasses
import time as _time_mod
from datetime import date, datetime, time

import pytest

import tdxdata
from tdxdata import timeliness as tl
from tdxdata.enums import Status
from tdxdata.models import Bar, Dataset, Table
from tdxdata._provenance import ClockSource, Provenance

# ── 固定的"服务器时刻"（都是 2026-10-27 周二，真实交易日）───────────────
D = date(2026, 10, 27)          # 周二
D_PREV = date(2026, 10, 26)     # 周一 = 上一交易日
D_FRI = date(2026, 10, 23)      # 周五
D_SAT = date(2026, 10, 24)      # 周六（休市）


# ══════════════════════════════════════════════════════════════════════════
# 小工具
# ══════════════════════════════════════════════════════════════════════════
def _bar(day: str = "20261027", period: str = "day", tm: str = "",
         close: float = 1335.0) -> Bar:
    """造一根 `Bar`（只填判定用得到的字段）。"""
    return Bar(market=1, code="600519", date=day, open=close, high=close,
               low=close, close=close, period=period, time=tm)


def _at(hh: int, mm: int, ss: int = 0, day: date = D) -> datetime:
    return datetime.combine(day, time(hh, mm, ss))


def _bare_client():
    """不跑 `__init__` 的 Client（不建连、不读产品表、不联网）。

    用 `getattr` 取类而不是写字面量：`tests/test_*.py` 里不许出现那种写法
    （`_dev/check.py` 第 7 条会判 FAIL）。
    """
    cls = getattr(tdxdata, "Client")
    c = cls.__new__(cls)
    c._probe_allowed = True
    return c


def _client_with_clock(clock, *, probe_allowed: bool = True):
    """注入假时钟的裸 Client。"""
    c = _bare_client()
    c._probe_allowed = probe_allowed
    c._clock = clock
    if hasattr(clock, "now"):
        c._clock = clock
    return c


def _mono_frozen(value: float = 1000.0):
    """冻结的单调时钟（`ServerClock` 的 `mono` 参数，保证可重复）。"""
    return lambda: value


# ══════════════════════════════════════════════════════════════════════════
# 一、常量与解析
# ══════════════════════════════════════════════════════════════════════════
def test_close_cutoff_is_fifteen_thirty_five():
    """`CLOSE_CUTOFF` 钉死在 **15:35**（不是 15:00，也不是 15:30:00）。

    依据：内容层面 15:00 就定稿，但服务端写当日快照最晚到 15:33:57，
    所以不能卡 15:00 也不能撞 15:30 的写入窗口；取 15:34 之后 + 缓冲 = 15:35。
    """
    assert tl.CLOSE_CUTOFF == time(15, 35)
    assert tl.FINAL_CUTOFF_HHMM == "15:35"
    assert tl.MIN_BAR_CUTOFF == time(15, 0)


def test_parse_helpers_do_not_guess():
    """解析失败一律 `None`，**不猜**（服务端 `HqTime` 前导 0 被吃的情况要兼容）。"""
    assert tl.parse_hhmmss("153003") == time(15, 30, 3)
    assert tl.parse_hhmmss("94258") == time(9, 42, 58)      # 前导 0 被吃
    assert tl.parse_hhmmss("509") == time(0, 5, 9)          # 只剩 3 位
    assert tl.parse_hhmmss("09:42:58") == time(9, 42, 58)
    assert tl.parse_hhmmss("") is None
    assert tl.parse_hhmmss("xx") is None
    assert tl.parse_hhmmss("999999") is None                # 99 点不存在
    assert tl.parse_yyyymmdd("20261027") == D
    assert tl.parse_yyyymmdd(20261027) == D
    assert tl.parse_yyyymmdd("2026-10-27") == D
    assert tl.parse_yyyymmdd("2026") is None
    assert tl.parse_yyyymmdd(None) is None
    assert tl.parse_hhmm("15:00") == time(15, 0)
    assert tl.parse_hhmm("1500") == time(15, 0)


# ══════════════════════════════════════════════════════════════════════════
# 二、假时钟三态：盘中 / 已收盘 / 周末
# ══════════════════════════════════════════════════════════════════════════
def test_fake_clock_gives_three_states_intraday_close_weekend():
    """同一个假时钟演一遍「盘中 → 收盘 → 周末」，每次 `now()` 都如实报时刻。"""
    clock = tl.FakeClock(_at(14, 44))
    assert clock.now().clock == "server"
    assert clock.now().at == _at(14, 44)

    clock.set(_at(15, 36))
    assert clock.now().at == _at(15, 36)

    clock.set(datetime.combine(D_SAT, time(10, 0)))
    assert clock.now().at.date() == D_SAT

    # 判定必须用**传进去的服务器时刻**，不许自己调 datetime.now()
    # 周末的真实形态：看到的是**上一个交易日（周五 10-23）**的根 → 历史根 → FINAL
    assert tl.judge_bar(_bar("20261023"), server_now=clock.now()) is Status.FINAL
    # ⚠️ 但"根比今天还晚"不算历史根（那是时钟/数据有一边错了），见
    # `test_bar_dated_after_today_is_never_final`


# ══════════════════════════════════════════════════════════════════════════
# 三、日线：当根盘中 / 收盘后 / 历史根 / 周末
# ══════════════════════════════════════════════════════════════════════════
def test_day_bar_intraday_is_provisional():
    """当根日线在盘中（14:44）→ `PROVISIONAL`（就是那 13,130.88 亿的场景）。"""
    got = tl.judge_bar(_bar("20261027"), server_now=tl.NowInfo(_at(14, 44)))
    assert got is Status.PROVISIONAL
    assert got.value == "provisional"


def test_same_day_bar_after_close_is_final():
    """**同一根**在收盘后 → `FINAL`（这一条就是本次改造要保证的事）。"""
    clock = tl.FakeClock(_at(14, 44))
    bar = _bar("20261027")
    assert tl.judge_bar(bar, server_now=clock.now()) is Status.PROVISIONAL

    clock.set(_at(15, 36))
    assert tl.judge_bar(bar, server_now=clock.now()) is Status.FINAL


def test_close_cutoff_boundary_153459_vs_153500():
    """边界：**15:34:59 → PROVISIONAL**，**15:35:00 → FINAL**（一秒都不能早）。"""
    bar = _bar("20261027")
    assert tl.judge_bar(bar, server_now=tl.NowInfo(_at(15, 34, 59))) is Status.PROVISIONAL
    assert tl.judge_bar(bar, server_now=tl.NowInfo(_at(15, 35, 0))) is Status.FINAL


def test_historical_bar_is_always_final():
    """历史根恒 `FINAL` —— 不管"现在"是盘中还是收盘。"""
    bar = _bar("20261024")            # 上周五
    for now in (_at(9, 31), _at(11, 0), _at(14, 44), _at(15, 34, 59)):
        assert tl.judge_bar(bar, server_now=tl.NowInfo(now)) is Status.FINAL


def test_weekend_friday_bar_stays_final():
    """周末：`bar.date` 是周五、`server_now` 是周六 → 必须 `FINAL`（不被误判成当根）。

    这条同时证明**不需要节假日表**：`bar.date != 服务器当前日` 已经把周末/休市
    排除掉了。硬要引入节假日表只会多一份会静默过时的事实源。
    """
    bar = _bar("20261023")            # 周五
    assert tl.judge_bar(bar, server_now=tl.NowInfo(_at(10, 0, day=D_SAT))) is Status.FINAL
    # 周一的"上一交易日"是周五（不是周日）—— 由 get_bars 取两根日线得出
    assert D_FRI < D_SAT


# ══════════════════════════════════════════════════════════════════════════
# 四、分钟线当根
# ══════════════════════════════════════════════════════════════════════════
def test_minute_bar_unfinished_is_provisional():
    """分钟当根未走完 → `PROVISIONAL`；走完 → `FINAL`。

    `Bar.time` 是**周期结束时刻**（实测 5m 末根 `15:00`、60m 是 14:00/15:00），
    所以 14:55 那根 5m 要到 15:00 之后才算走完。
    """
    bar = _bar("20261027", period="5m", tm="14:55")
    assert tl.judge_bar(bar, server_now=tl.NowInfo(_at(14, 57))) is Status.PROVISIONAL
    assert tl.judge_bar(bar, server_now=tl.NowInfo(_at(15, 1))) is Status.FINAL

    # 60m 末根（15:00）在 15:00 之前一律 PROVISIONAL
    h = _bar("20261027", period="60m", tm="15:00")
    assert tl.judge_bar(h, server_now=tl.NowInfo(_at(14, 59, 59))) is Status.PROVISIONAL
    assert tl.judge_bar(h, server_now=tl.NowInfo(_at(15, 0, 1))) is Status.FINAL


def test_bar_period_end_treats_time_as_period_end():
    """`bar_period_end` 把 `bar.time` 当周期结束时刻（加周期长度只作保守加固）。"""
    assert tl.bar_period_end(_bar("20261027", period="5m", tm="15:00")) == time(15, 0)
    assert tl.bar_period_end(_bar("20261027", period="day")) is None
    assert tl.is_intraday_period("5m") and not tl.is_intraday_period("day")


# ══════════════════════════════════════════════════════════════════════════
# 五、产品类：ztqd 盘中 PENDING / lrmx·north T+1 DELAYED
# ══════════════════════════════════════════════════════════════════════════
def test_ztqd_intraday_is_pending_not_error_not_empty():
    """`ztqd` 盘中 → `PENDING`（"今天的还没生成"），**不是异常、不是空列表**。"""
    got = tl.judge_product("ztqd", D_PREV, server_now=tl.NowInfo(_at(10, 30)), prev_trade_date=D_PREV)
    assert got is Status.PENDING
    # 收盘后才轮到它定稿
    got2 = tl.judge_product("ztqd", D, server_now=tl.NowInfo(_at(16, 10)), prev_trade_date=D_PREV)
    assert got2 is Status.FINAL


@pytest.mark.parametrize("name", ["lrmx", "north"])
def test_lrmx_and_north_data_date_is_prev_trade_date_then_delayed(name):
    """两融/北向是 **T+1**：数据日 == 上一交易日 → `DELAYED`（数据本身滞后）。"""
    got = tl.judge_product(name, D_PREV, server_now=tl.NowInfo(_at(16, 10)), prev_trade_date=D_PREV)
    assert got is Status.DELAYED
    # 更早的数据日同样是滞后，不许升级成 FINAL
    older = tl.judge_product(name, D_FRI, server_now=tl.NowInfo(_at(16, 10)), prev_trade_date=D_PREV)
    assert older is Status.DELAYED


def test_market_stat_industry_older_than_prev_trade_date_is_delayed():
    """行业统计是 T-1：数据日 < 上一交易日 → `DELAYED`。"""
    got = tl.judge_market_stat("industry", D_FRI, server_now=tl.NowInfo(_at(16, 10)),
                               prev_trade_date=D_PREV)
    assert got is Status.DELAYED
    # 数据日 == 上一交易日也是 T-1 常态 → 仍 DELAYED
    assert tl.judge_market_stat("industry", D_PREV, server_now=tl.NowInfo(_at(16, 10)),
                                prev_trade_date=D_PREV) is Status.DELAYED


def test_prev_trade_date_none_must_not_yield_final():
    """拿不到上一交易日时**不许**判成 `FINAL`（不确定只能更保守）。"""
    # T+1 产品：数据日读不出来 → DELAYED
    assert tl.judge_product("lrmx", None, server_now=tl.NowInfo(_at(16, 10)),
                            prev_trade_date=None) is Status.DELAYED
    # 数据日不是今天、又不知道上一交易日 → 降级成 DELAYED
    assert tl.judge_product("lrmx", D_PREV, server_now=tl.NowInfo(_at(16, 10)),
                            prev_trade_date=None) is Status.DELAYED
    # 市场统计同理
    assert tl.judge_market_stat("industry", D_PREV, server_now=tl.NowInfo(_at(16, 10)),
                                prev_trade_date=None) is Status.DELAYED
    # 涨停清单：收盘后数据日还是昨天 → 滞后（不是 FINAL）
    assert tl.judge_product("ztqd", D_PREV, server_now=tl.NowInfo(_at(16, 10)),
                            prev_trade_date=None) is Status.DELAYED


def test_unregistered_product_is_provisional_not_final():
    """没登记时效口径的产品 → `PROVISIONAL`（unknown ≠ 0，绝不默认 FINAL）。"""
    got = tl.judge_product("some_new_product", D, server_now=tl.NowInfo(_at(16, 10)),
                           prev_trade_date=D_PREV)
    assert got is Status.PROVISIONAL


# ══════════════════════════════════════════════════════════════════════════
# 六、时钟来源是**一等字段**：fetch_clock / clock_note
# ══════════════════════════════════════════════════════════════════════════
PROBE_AT = datetime(2026, 10, 27, 9, 0, 1)      # 刻意选一个不可能是"此刻"的时刻


def _stub_probe_ok():
    return lambda: PROBE_AT


def _stub_probe_fail():
    def boom():
        raise RuntimeError("模拟：行情探针挂了")
    return boom


def test_probe_success_sets_fetch_clock_server_and_server_fetched_at():
    """探针成功 → `fetch_clock == "server"`，且 `fetched_at` **就是探针给的时刻**。

    ⚠️ 这条守的是契约：探针成功时 `fetched_at` 必须是服务器时刻，
    **不许用本机时钟顶替而不标注**。所以这里既断言"等于服务器时刻"，
    也断言"不等于本机 `datetime.now()`"——两个方向都堵上。
    """
    clock = tl.ServerClock(_stub_probe_ok(), ttl=0.0, fail_ttl=0.0,
                           mono=_mono_frozen(), local=lambda: _at(23, 59, 58))
    c = _client_with_clock(clock)
    rec = c._record_call("HQServ.PXFXT", 0x0E, host="10.0.0.9", t0=_time_mod.monotonic())

    assert rec["fetch_clock"] == "server"
    assert rec["fetched_at"] == PROBE_AT, "fetched_at 现在是 datetime（探针给的原始时刻）"

    now_stamp = datetime.now()
    assert rec["fetched_at"] != now_stamp, "探针成功时不许拿本机时钟当 fetched_at"
    assert "clock_note" not in rec, "时钟可信就不该出现降级标注"

    # 同一份记录摊成 Provenance 后，时钟来源仍是结构化字段（不是自由文本）
    prov = c._prov_from(rec)
    assert prov.clock is ClockSource.SERVER
    assert prov.fetched_at == PROBE_AT.replace(tzinfo=prov.fetched_at.tzinfo)


def test_probe_failure_sets_fetch_clock_local_with_nonempty_clock_note():
    """探针失败 → `fetch_clock == "local"`，且 `clock_note` 非空（能看出是降级）。

    `clock_note` 只取**稳定取值** `"local-clock-fallback"`（细节在 `clock_detail`）：
    黄金样本一旦两边都有这个键，**改值仍判红**，把"探针冷却中/超时"这类过程性
    细节编进去会让门禁在不同样本间抖动。
    """
    clock = tl.ServerClock(_stub_probe_fail(), ttl=0.0, fail_ttl=0.0,
                           mono=_mono_frozen(), local=lambda: _at(16, 0, 0))
    c = _client_with_clock(clock)
    rec = c._record_call("HQServ.PXFXT", 0x0E, host=None, t0=_time_mod.monotonic())

    assert rec["fetch_clock"] == "local"
    assert rec["clock_note"] == "local-clock-fallback"
    assert rec["clock_note"], "降级标注不许是空串"
    assert rec.get("clock_detail"), "降级原因要能在 last_call 里看见（不是空）"
    assert "RuntimeError" in rec["clock_detail"]
    # 降级时 fetched_at 是本机时钟 —— 但它被 clock_note 明确标注了，不是"冒充"
    assert rec["fetched_at"] == _at(16, 0, 0)

    prov = c._prov_from(rec)
    assert prov.clock is ClockSource.LOCAL
    assert "local-clock-fallback" in prov.notes


def test_probe_failure_never_makes_today_day_bar_final():
    """探针失败（时钟不可信）时，**当天**日线不许被判成 `FINAL`。

    场景刻意做成"最容易被误判成 FINAL"的那种：本机时钟 16:00（已过 15:35），
    bar 又是当天的。如果实现只看钟面不看时钟来源，就会错判成 FINAL ——
    而 `fetched_at` 悄悄变成本机时钟正是本项目红线。
    """
    clock = tl.ServerClock(_stub_probe_fail(), ttl=0.0, fail_ttl=0.0,
                           mono=_mono_frozen(), local=lambda: _at(16, 0, 0))
    c = _client_with_clock(clock)
    info = c._now()
    assert info.clock == "local"

    bar = _bar("20261027")
    assert tl.judge_bar(bar, server_now=info) is Status.PROVISIONAL
    assert tl.judge_bar(bar, server_now=info) is not Status.FINAL
    # 分钟/连续序列同理：时钟不可信就没有"已收盘"这回事
    assert tl.judge_intraday(info, cutoff=tl.CLOSE_CUTOFF) is Status.PROVISIONAL

    # 但历史根不受影响（日子早于今天这件事任何合理的钟都成立）
    assert tl.judge_bar(_bar("20261024"), server_now=info) is Status.FINAL

    # DEF-019 类型锁：裸 datetime 分不清服务器/本机时刻，必须被拒收
    with pytest.raises(TypeError):
        tl.judge_bar(bar, server_now=_at(16, 0, 0))


def test_bare_client_without_probe_permission_never_touches_network():
    """未跑 `__init__` 的裸 Client（`_probe_allowed` 缺失）→ 不敢探针，走本机时钟。

    `tests/test_p8_adjust_page.py` 里那批 `Client.__new__` 的用例就是这种形态：
    补 meta 绝不能变成"顺手联网"。
    """
    cls = getattr(tdxdata, "Client")
    c = cls.__new__(cls)                        # 故意不设 _probe_allowed
    info = c._now()
    assert info.clock == "local"
    assert "不允许探针" in info.note and "__init__" in info.note
    rec = c._record_call("HQServ.PXFXT", 0x0E, host=None, t0=_time_mod.monotonic())
    assert rec["fetch_clock"] == "local"
    assert rec["clock_note"] == "local-clock-fallback"


def test_clock_probe_false_sends_zero_probes(monkeypatch):
    """`clock_probe=False` → **一次探针都不许发**（用户关掉开关等于零额外请求）。

    守的是本项目红线：「不许让用户在不知情时多花配额」。默认开、可以关、关了可自证
    —— 这三件缺一不可，所以这里用**计数器**证明调用次数是 0（不是"看起来没联网"）。
    走的是**真路径**：`_clock` 留空，让 `_clock_obj()` 自己按开关决定建哪种时钟；
    探针位打桩成计数器 —— 只要被调一次就会 +1。

    ⚠️ 本用例的探针锚点是 **2026-10-27**（`PROBE_AT`），而 `HybridClock` 要求
    「本机日期 == 服务器交易日」。若不冻结本机钟，**运行日一过 10-27** 控制组就会
    降级 `local`、断言红（2026-10-28 实测到的跨日缺陷）。所以这里把模块级本机钟
    钉到同一场景日 —— 冻结的是**测试的"今天"**，不是放宽任何判定。
    """
    monkeypatch.setattr(tl, "_local_now", lambda: _at(16, 0, 0))
    cls = getattr(tdxdata, "Client")
    calls = {"probe": 0, "bars": 0}

    def counting_probe():
        calls["probe"] += 1
        return PROBE_AT

    c = cls.__new__(cls)
    c._clock_probe = False                 # 等价于 __init__(clock_probe=False)
    c._probe_allowed = False
    c._clock = None                        # 让 _clock_obj() 自己决定
    c._probe_quote_time = counting_probe  # 若被调 → 计数
    c.get_bars = lambda *a, **k: calls.__setitem__("bars", calls["bars"] + 1)

    for _ in range(3):
        info = c._now()
        assert info.clock == "local"
        assert c._prev_trade_date() is None
        rec = c._record_call("HQServ.PXFXT", 0x0E, host=None, t0=_time_mod.monotonic())
        assert rec["fetch_clock"] == "local"
        assert rec["clock_note"] == "local-clock-disabled-by-user"

    assert calls["probe"] == 0, "clock_probe=False 时一次探针都不许发"
    assert calls["bars"] == 0, "clock_probe=False 时连「上一交易日」也不许去取"

    # 对照组：同一套计数器，开关打开就必须真的被调到（否则上面那条断言是空的）
    on = cls.__new__(cls)
    on._clock_probe = True
    on._probe_allowed = True
    on._clock = None
    on._probe_quote_time = counting_probe
    assert on._now().clock in ("hybrid", "server"), "开关打开时探针必须真的被调用"
    assert calls["probe"] == 1, "开关打开时探针必须真的被调用（对照组）"


def test_clock_probe_false_note_is_distinguishable_from_probe_failure():
    """关掉探针与探针失败**必须能区分**（两个稳定取值，不混成一个）。"""
    cls = getattr(tdxdata, "Client")

    disabled = cls.__new__(cls)
    disabled._clock_probe = False
    disabled._probe_allowed = False
    # 本机钟冻结到场景日（2026-10-27 16:00）：否则 `_now()` 落到"真实今天"，
    # judge_bar 会把 10-27 的 bar 当"往日"判成 FINAL —— 跨日后这条用例必红。
    disabled._clock = tl.ServerClock(None, note="用户关了 clock_probe",
                                     local=lambda: _at(16, 0, 0))
    rec_off = disabled._record_call("HQServ.PXFXT", 0x0E, host=None,
                                    t0=_time_mod.monotonic())

    failed = cls.__new__(cls)
    failed._clock_probe = True
    failed._probe_allowed = True
    failed._clock = tl.ServerClock(_stub_probe_fail(), ttl=0.0, fail_ttl=0.0,
                                   mono=_mono_frozen(), local=lambda: _at(16, 0, 0))
    rec_bad = failed._record_call("HQServ.PXFXT", 0x0E, host=None,
                                  t0=_time_mod.monotonic())

    assert rec_off["clock_note"] == "local-clock-disabled-by-user"
    assert rec_bad["clock_note"] == "local-clock-fallback"
    assert rec_off["clock_note"] != rec_bad["clock_note"], \
        "「用户主动关掉」不许看起来像「探针坏了」"
    # 两种情况下时效都照旧保守降级
    bar = _bar(datetime(2026, 10, 27, 16, 0, 0).strftime("%Y%m%d"))
    assert tl.judge_bar(bar, server_now=disabled._now()) is Status.PROVISIONAL


def test_provenance_source_fields_are_fixed():
    """ARCH-002：出处是一等字段 `Provenance`；host 取不到也要在（值是 None）。"""
    c = _client_with_clock(tl.FakeClock(_at(16, 0, 0)))
    rec = c._record_call("CWServ.x", 0x21, host=None, t0=_time_mod.monotonic())
    prov = c._prov_from(rec)
    assert isinstance(prov, Provenance)
    assert prov.host is None, "取不到就给 None（显式），不编主机名"
    assert prov.cmd == "CWServ.x" and prov.ctl == 0x21
    assert prov.clock is ClockSource.SERVER and prov.cached is False
    assert prov.fetched_at == _at(16, 0, 0).replace(tzinfo=prov.fetched_at.tzinfo)


# ══════════════════════════════════════════════════════════════════════════
# 七、说明性键**不承载唯一信息**：status_note / as_of_note
# ══════════════════════════════════════════════════════════════════════════
def test_unregistered_status_note_says_no_timeliness_declaration():
    """未登记口径的 `status_note` 必须直说「台账里没有时效声明、无法判定」。

    不许让人误读成"库判定它是盘中值"——那是另一种假结论。
    """
    c = _client_with_clock(tl.FakeClock(_at(16, 0, 0)))
    table = c._stamp_table(
        Table(cols=["N001"], rows=[]), "CWServ.ph_unknown_thing", 0x21,
        host="10.0.0.9", t0=_time_mod.monotonic(),
        status=tl.judge_unregistered(server_now=c._now()),
        as_of=None, status_note=tl.UNREGISTERED_NOTE)
    note = "；".join(table.provenance.notes)
    assert "421" in note and "时效声明" in note and "无法判定" in note
    assert "非定稿" in note
    assert table.status == "provisional"


def test_ztqd_delayed_note_carries_the_evidence():
    """`ztqd` 盘后判 `delayed` 时，`status_note` 必须带**硬证据**（当日清单是有的）。

    证据：`00102` 口径在当日 10-27 就有 41 行 —— 所以"盘后仍只给到上一交易日"
    是真的滞后，不是"还没生成"。没这条证据，`delayed` 就只是个标签。
    """
    c = _client_with_clock(tl.FakeClock(_at(16, 10)))
    c.get_bars = lambda *a, **k: None          # 上一交易日探针打桩：不联网
    ds = Dataset(name="ztqd", table=Table(cols=["N001"], rows=[{"N001": "20261026"}]))
    c._describe_product(ds)

    assert ds.table.provenance.status == "delayed"
    note = "；".join(ds.table.provenance.notes)
    assert note, "status_note 不许为空"
    assert "00102" in note and "41 行" in note
    assert "真的滞后" in note and "还没生成" in note


def test_status_note_is_nonempty_when_status_is_not_final():
    """被判成 `DELAYED` 的场景里，`status_note` 非空（能看出为什么这么判）。

    同时守一条边界：**数据信号只在结构化字段里**。`status_note` 只是"为什么"，
    删掉它，`status` / `data_date` 仍然足以得出结论。
    """
    c = _client_with_clock(tl.FakeClock(_at(16, 10)))
    # 上一交易日探针打桩成"取不到" —— 真实代码路径，不联网
    c.get_bars = lambda *a, **k: None
    ds = Dataset(name="lrmx", table=Table(cols=["N001"], rows=[{"N001": "20261026"}]))
    c._describe_product(ds)

    prov = ds.table.provenance
    assert prov.status == Status.DELAYED.value == "delayed"
    assert prov.data_date == "2026-10-26"             # 数据信号：结构化字段
    note = "；".join(prov.notes)
    assert note, "被判成 delayed 时必须能看出原因，不许是空串/None"
    assert "上一交易日" in note
    assert prov.clock is ClockSource.SERVER
    # 删掉 notes 也照样能得出结论（说明性文本不承载唯一信息）
    assert prov.status == "delayed" and prov.data_date == "2026-10-26"


def test_status_note_is_nonempty_for_unregistered_command():
    """未登记时效口径的命令 → `provisional` + 非空 `status_note`（不是 FINAL、也不是空）。"""
    c = _client_with_clock(tl.FakeClock(_at(16, 10)))
    table = c._stamp_table(
        Table(cols=["N001"], rows=[]), "CWServ.ph_unknown_thing", 0x21,
        host="10.0.0.9", t0=_time_mod.monotonic(),
        status=tl.judge_unregistered(server_now=c._now()),
        as_of=None,
        status_note="该命令未登记时效口径 → 按最保守口径标 provisional")
    assert table.status == "provisional"
    assert "未登记" in "；".join(table.provenance.notes)
    # Table 也有只读便捷属性，取值与 provenance 同源
    assert table.provenance.as_of is None and table.as_of is None


def test_bars_as_of_note_explains_that_as_of_is_the_last_bar_not_the_fetch_time(monkeypatch):
    """`as_of` 取**末根 bar 的日期**（不是取数时刻），这件事由 `as_of_note` 说明。

    断言要点：`as_of` 是 2026-10-27（末根 bar 的日期），
    `fetched_at` 是 2026-10-27 16:00:00（假时钟的"现在"）—— 两者**不同**；
    `as_of_note` 非空且点明"不是取数时刻"。
    """
    from tdxdata._engine import hq_pb

    row = {"Data": "20261027", "Open": "1.0", "High": "1.0", "Low": "1.0",
           "Close": "1.0", "Volume": "100", "Amount": "100", "Second": "0"}
    monkeypatch.setattr(hq_pb, "get_bars", lambda *a, **k: (["Data"], [row]))

    c = _client_with_clock(tl.FakeClock(_at(16, 0, 0)))
    bars = c.get_bars("600519", count=1)

    prov = bars.provenance
    assert len(bars) == 1 and prov.as_of == "2026-10-27"
    assert prov.fetched_at == _at(16, 0, 0).replace(tzinfo=prov.fetched_at.tzinfo)
    assert prov.as_of != prov.fetched_at.isoformat(sep=" ", timespec="seconds")
    joined = "；".join(prov.notes)
    assert joined, "as_of 的来源必须能看出来，不许是空串"
    assert "不是取数时刻" in joined
    # 数据信号在结构化字段：收盘后 → final，末根不是 provisional
    assert bars.status == Status.FINAL.value
    assert prov.extra.get("provisional") is False
    assert bars[0].provisional is False
    assert prov.clock is ClockSource.SERVER


# ══════════════════════════════════════════════════════════════════════════
# 八、ServerClock 自身：不重复取 + 失败冷却可注入
# ══════════════════════════════════════════════════════════════════════════
def test_server_clock_anchors_and_does_not_reprobe_inside_ttl():
    """TTL 内不重复探针（否则每次取数都要多花一次往返），TTL 外重取。"""
    calls = {"n": 0}
    mono = {"t": 0.0}

    def probe():
        calls["n"] += 1
        return PROBE_AT + __import__("datetime").timedelta(seconds=calls["n"])

    clock = tl.ServerClock(probe, ttl=10.0, fail_ttl=0.0, mono=lambda: mono["t"])
    first = clock.now()
    assert calls["n"] == 1 and first.clock == "server"

    mono["t"] = 5.0                       # TTL 内：走锚点 + 单调增量
    second = clock.now()
    assert calls["n"] == 1, "TTL 内不许重复探针"
    assert second.at == first.at + __import__("datetime").timedelta(seconds=5.0)

    mono["t"] = 11.0                      # 超过 TTL：重取
    clock.now()
    assert calls["n"] == 2


def test_server_clock_failure_cooldown_is_injectable():
    """失败冷却时长可注入（`fail_ttl`）—— 黄金门禁因此不会被过程性状态带抖。"""
    calls = {"n": 0}
    mono = {"t": 0.0}

    def probe():
        calls["n"] += 1
        raise OSError("探针挂了")

    clock = tl.ServerClock(probe, ttl=10.0, fail_ttl=30.0,
                           mono=lambda: mono["t"], local=lambda: _at(16, 0, 0))
    assert clock.now().clock == "local"
    assert calls["n"] == 1
    mono["t"] = 10.0
    assert clock.now().clock == "local"
    assert calls["n"] == 1, "冷却期内不许再打探针"
    mono["t"] = 31.0
    clock.now()
    assert calls["n"] == 2, "冷却期过了才允许重试"
    assert clock.anchored is False


def test_judge_rejects_wrong_argument_types():
    """传错类型当场报错（不静默按"今天"处理）。"""
    with pytest.raises(TypeError):
        tl.judge_bar(_bar(), server_now="2026-10-27")
    with pytest.raises(TypeError):
        tl.judge_intraday("2026-10-27")
    with pytest.raises(TypeError):
        tl.FakeClock("2026-10-27 16:00:00")


def test_status_enum_is_str_subclass_and_has_four_states():
    """`Status` 是字符串枚举（能直接进 JSON），四态齐全且值稳定。"""
    assert issubclass(Status, str)
    assert Status.FINAL == "final"
    assert {s.value for s in Status} == {
        "final", "provisional", "pending", "delayed"}
    assert tdxdata.Status is Status


# ══════════════════════════════════════════════════════════════════════════
# 六、双源交叉校验时钟（`HybridClock`）—— 修「FINAL 分支现实中不可达」
# ══════════════════════════════════════════════════════════════════════════
# 缺陷实证（2026-10-27 实网）：指数 `999999` 的 `HqTime` 在 16:37 与 17:29 两次
# 取样都是 `15:30:03`（50 分钟不前进）；最晚的 `000001` 也只到 `15:33:06`。
# 而当日日线定稿门槛 `CLOSE_CUTOFF = 15:35` —— 若拿 `HqTime` 当"现在几点"，
# `server_now` **永远跨不过 15:35**，`FINAL` 那一支整晚不可达。
# 所以：**日期用服务器、时分用本机、再用行情做交叉校验**。

def _hybrid(local_dt, hq, *, cutoff=tl.CLOSE_CUTOFF, ttl=1800.0):
    """造一个双源时钟：本机时刻 + 服务器 `(HqDate, HqTime)` 都由参数钉死。"""
    return tl.HybridClock(lambda: hq, local=lambda: local_dt,
                          mono=_mono_frozen(), cutoff=cutoff, ttl=ttl)


def test_hybrid_clock_takes_date_from_server_and_time_from_local():
    """`at` = **服务器日期** + **本机时分**；`clock="hybrid"` 且 `market_closed` 有值。"""
    clk = _hybrid(_at(16, 10), (D, time(15, 30, 3)))
    info = clk.now()
    assert info.at.date() == D, "日期必须取服务器的 HqDate"
    assert (info.at.hour, info.at.minute) == (16, 10), "时分必须取本机"
    assert info.clock == "hybrid"
    assert info.market_closed is True
    assert tl.judge_bar(_bar("20261027"), server_now=info) is Status.FINAL


def test_hybrid_clock_intraday_is_still_provisional():
    """本机 14:30 且行情也只到 14:30 → 两条都不满足 → `provisional`。"""
    info = _hybrid(_at(14, 30), (D, time(14, 30, 0))).now()
    assert info.clock == "hybrid"
    assert info.market_closed is False
    assert tl.judge_bar(_bar("20261027"), server_now=info) is Status.PROVISIONAL


def test_hybrid_clock_after_close_reaches_final():
    """**核心回归**：本机 16:10、`HqTime` 15:30 → 当日日线必须能判 `FINAL`。

    改之前（`ServerClock` 拿 `HqTime` 当现在时刻）这里恒为 `PROVISIONAL`：
    17:29 的"现在时刻"被当成 15:30，永远 < 15:35。这就是被修的那条缺陷。
    """
    info = _hybrid(_at(16, 10), (D, time(15, 30, 3))).now()
    assert info.market_closed is True
    assert tl.judge_bar(_bar("20261027"), server_now=info) is Status.FINAL
    # 分钟根同理（它的结束时刻更早）
    assert tl.judge_bar(_bar("20261027", period="5m", tm="14:55"),
                        server_now=info) is Status.FINAL


def test_hybrid_clock_contradiction_is_provisional_and_says_why():
    """本机 16:10（说已收盘）、`HqTime` 只有 14:35（说没到）→ **矛盾 → 保守**。

    这是"本机钟偏快"的唯一防线：两个源不能同时为真时，不许判 `FINAL`，
    而且原因必须写出来（否则下游只会看到一个没头没尾的 `provisional`）。
    """
    info = _hybrid(_at(16, 10), (D, time(14, 35, 0))).now()
    assert info.market_closed is False
    assert "自相矛盾" in info.note, "矛盾必须写进 note（实际：%r）" % (info.note,)
    assert "16:10" in info.note and "14:35" in info.note
    assert tl.judge_bar(_bar("20261027"), server_now=info) is Status.PROVISIONAL
    assert tl.judge_intraday(info) is Status.PROVISIONAL


def test_hybrid_clock_date_mismatch_falls_back_to_untrusted_local():
    """本机日期 ≠ 服务器交易日 → 两个源都不能混用 → `local` + 永不 `FINAL`。

    取**更危险的**那个方向：本机**慢了一天**（说 10-26，实际交易日是 10-27）。
    这时今天的根（20261027）在不可信的钟眼里"是未来"，若按"日子不同就是历史根"
    处理就会**把今天盘中的根宣布定稿** —— 所以必须多一道"不许比今天更晚"的闸。
    """
    info = _hybrid(_at(16, 10, day=date(2026, 10, 26)), (D, time(15, 30, 3))).now()
    assert info.clock == "local"
    assert info.market_closed is None
    assert "服务器交易日" in info.note
    assert tl.judge_bar(_bar("20261027"), server_now=info) is Status.PROVISIONAL
    # 真正更早的根照旧算历史根（这一条不能被上面那道闸误伤）
    assert tl.judge_bar(_bar("20261023"), server_now=info) is Status.FINAL


def test_bar_dated_after_today_is_never_final():
    """根的日期比"今天"还晚 → 至少有一个源错了 → **不许** `FINAL`（未知 ≠ 定稿）。"""
    assert tl.judge_bar(_bar("20261030"), server_now=tl.NowInfo(_at(16, 10))) is Status.PROVISIONAL
    assert tl.judge_bar(_bar("20261030"), server_now=tl.NowInfo(_at(16, 10, day=date(2026, 10, 29)))) is Status.PROVISIONAL


def test_hybrid_clock_historical_bar_stays_final_even_when_contradicting():
    """历史根（`bar.date != 服务器交易日`）恒 `FINAL` —— 矛盾只约束"当日"。"""
    info = _hybrid(_at(16, 10), (D, time(14, 35, 0))).now()
    assert tl.judge_bar(_bar("20261023"), server_now=info) is Status.FINAL
    assert tl.judge_bar(_bar("20261026"), server_now=info) is Status.FINAL


def test_hybrid_clock_probe_failure_never_reaches_final():
    """探针失败 / 用户关掉探针 → `local`，当日一律不许 `FINAL`（阶段 1 行为保住）。"""

    def boom():
        raise RuntimeError("探针炸了")

    failed = tl.HybridClock(boom, local=lambda: _at(16, 10), mono=_mono_frozen()).now()
    assert failed.clock == "local"
    assert "RuntimeError" in failed.note, "探针异常必须连类型一起记下"
    assert tl.judge_bar(_bar("20261027"), server_now=failed) is Status.PROVISIONAL

    no_probe = tl.HybridClock(None, local=lambda: _at(16, 10),
                              mono=_mono_frozen()).now()
    assert no_probe.clock == "local"
    assert tl.judge_bar(_bar("20261027"), server_now=no_probe) is Status.PROVISIONAL


def test_hybrid_clock_caches_probe_result_within_ttl():
    """同一个时钟连续问，只探一次（锚点 + `ttl`）—— 否则每取一次数都多打一轮。"""
    calls = {"n": 0}

    def probe():
        calls["n"] += 1
        return (D, time(15, 30, 3))

    clk = tl.HybridClock(probe, local=lambda: _at(16, 10), mono=_mono_frozen())
    for _ in range(5):
        assert clk.now().clock == "hybrid"
    assert calls["n"] == 1


def test_now_info_still_accepts_two_positional_fields():
    """`NowInfo` 加字段必须是**追尾加可选**：老写法 `NowInfo(at, "local")` 照旧。"""
    info = tl.NowInfo(_at(16, 10), "local")
    assert info.market_closed is None
    assert info.note == ""
    assert tl.judge_bar(_bar("20261027"), server_now=info) is Status.PROVISIONAL


def test_server_clock_is_kept_for_backward_compatibility():
    """`ServerClock`（纯服务端时刻）不能删：它是"日期和时分都来自服务器"的旧口径。"""
    clk = tl.ServerClock(lambda: _at(16, 10), mono=_mono_frozen(),
                         local=lambda: _at(16, 10))
    info = clk.now()
    assert info.clock == "server"
    assert tl.judge_bar(_bar("20261027"), server_now=info) is Status.FINAL
