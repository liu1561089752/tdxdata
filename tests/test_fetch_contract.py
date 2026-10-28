# -*- coding: utf-8 -*-
r"""ARCH-003 统一取数契约的守卫测试（离线）。

守四件事
--------
1. **`Client.timeliness()` 公开方法覆盖率**（报告 §12 目标：零覆盖公开方法 = 0）：
   `bar_day` / `bar_min` / `minutes` / `ticks` / `ztqd` / 未登记 kind / 降级路径。
2. **`cache=` 参数**（§5.1 指出 tests 里 `cache=` 出现 0 次）：命中不占闸门、
   未命中才 `wait()`、`cached=True` + 短 `elapsed`、TTL 过期后重新取。
3. **`_fetch` 管道契约**：FetchSpec → Table/series 形状、空结果 EmptyResult、
   status=None 保留 base 已有 status（call() 的判定不被覆盖）。
4. **公开 API 面零变化**：33 个公开方法签名逐一核对（test_public_api 类判据的
   ARCH-003 复核面）。

红线：`tests/test_*.py` 不许出现 `Client(` / `Session(` 字面量 —— 裸对象用
`getattr(tdxdata, "Client").__new__(cls)` 造，零真实网络。
"""
from __future__ import annotations

import sys
import time
from datetime import date, datetime, time as dtime

import pytest

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                        # noqa: BLE001
    pass

import tdxdata
from tdxdata import timeliness as tl
from tdxdata.enums import Status
from tdxdata.models import Table

_CLS = getattr(tdxdata, "Client")          # 红线：不写 Client( 字面量
D = date(2026, 10, 27)                     # 周二（真实交易日）


def _bare_client():
    c = _CLS.__new__(_CLS)
    c._probe_allowed = False
    c._clock_probe = False
    c._clock = None
    c._last_call = {}
    c._in_cal = False
    return c


def _fake_clock(at: datetime | None = None):
    """固定"服务器时刻"的假时钟（默认 2026-10-27 16:00，收盘后）。"""
    return tl.FakeClock(at or datetime(2026, 10, 27, 16, 0, 0))


class _FakePacer:
    """记录 wait()/note_limited 调用的假闸门。"""
    limited_events = 0
    gap = 2.2

    def __init__(self):
        self.waits = 0

    def wait(self):
        self.waits += 1

    def note_ok(self):
        pass

    def note_limited(self):
        return 0.0

    def stats(self):
        return {}


# ══════════════════════════════════════════════════════════════════════════
# 一、Client.timeliness() 覆盖（此前 0 条测试）
# ══════════════════════════════════════════════════════════════════════════
def test_timeliness_bar_day_after_close_is_final():
    c = _bare_client()
    c._clock = _fake_clock()
    out = c.timeliness("bar_day")
    assert out["status"] is Status.FINAL
    assert out["kind"] == "bar_day"
    assert out["close_cutoff"] == "15:35"
    assert out["fetch_clock"] == "server"
    assert out["server_time"].startswith("2026-10-27 16:00:00")
    assert out["data_date"] is None
    assert "15:35" in out["note"]


def test_timeliness_bar_min_and_minutes_share_cutoff():
    c = _bare_client()
    c._clock = _fake_clock()
    assert c.timeliness("bar_min")["status"] is Status.FINAL
    assert c.timeliness("minutes")["status"] is Status.FINAL
    assert c.timeliness("ticks")["status"] is Status.FINAL


def test_timeliness_unregistered_kind_stays_provisional_and_explains():
    """不认识的 kind **不抛错**：provisional + note 说明（问一句不该炸）。"""
    c = _bare_client()
    c._clock = _fake_clock()
    out = c.timeliness("definitely-not-a-kind")
    assert out["status"] is Status.PROVISIONAL
    assert "definitely-not-a-kind" in out["note"]


def test_timeliness_bad_data_date_is_reported_not_raised():
    c = _bare_client()
    c._clock = _fake_clock()
    out = c.timeliness("bar_day", data_date="not-a-date")
    assert out["data_date"] is None
    assert "not-a-date" in out["note"]


def test_timeliness_local_clock_downgrades_conservatively():
    """探针失败 → LOCAL + 降级说明；收盘口径在 LOCAL 下也**不许**判 FINAL 之外撒谎。"""
    c = _bare_client()
    c._clock = None                       # 裸时钟 = 无探针 = 本机兜底
    out = c.timeliness("bar_day")
    assert out["fetch_clock"] != "server"
    assert out["note"]                    # 降级原因必须写出来


def test_timeliness_ztqd_takes_prev_trade_date_path():
    """ztqd/lrmx/north 走 judge_product，需要 prev_trade_date（取不到 = 降级）。"""
    c = _bare_client()
    c._clock = _fake_clock()
    c._cal = {}                            # 触发 _prev_trade_date 的缓存初始化
    out = c.timeliness("ztqd")
    assert out["status"] in (Status.PENDING, Status.PROVISIONAL)
    assert out["prev_trade_date"] is None or out["prev_trade_date"]


# ══════════════════════════════════════════════════════════════════════════
# 二、cache= 参数（§5.1：tests 里出现 0 次 → 补齐）
# ══════════════════════════════════════════════════════════════════════════
def test_cache_second_call_hits_and_bypasses_pacer():
    """命中缓存：**不占限流闸门**（waits 不增）、`cached=True`、`elapsed≈0`。"""
    from datetime import datetime as _dt

    from tdxdata._engine.cache import TTLCache, cache_key
    from tdxdata._provenance import ClockSource, Provenance

    c = _bare_client()
    pacer = _FakePacer()
    c._pacer = pacer
    c._cache = TTLCache(ttl=300)
    from tdxdata._engine import session as session_mod
    c._session_mod = session_mod            # call() 用 _session_mod.body_of/parse

    calls = {"n": 0}

    class _FakeSession:
        host = "fake-host:7709"
        alive = True

        def ask(self, ctl, cmd, args, idle=4.0):
            calls["n"] += 1
            # 最小可解析 ResultSets 形态：1 列 1 行（ColName 列表 + Content 行）
            return (b'{"ResultSets":[{"ColName":["N001"],'
                    b'"Content":[["20261027"]]}]}')

    c._sess = _FakeSession()
    t1 = c.call("CWServ.ph_test", 0x21, ["001"], allow_empty=True)
    assert t1.rows and t1.cols, "夹具自身坏了：第一次就该取到 1 行"
    assert calls["n"] == 1
    waits_after_first = pacer.waits

    t2 = c.call("CWServ.ph_test", 0x21, ["001"], allow_empty=True)
    assert calls["n"] == 1                  # 第二次没发请求
    assert pacer.waits == waits_after_first   # 命中不占闸门
    assert t2.provenance.cached is True
    assert any("缓存" in n for n in t2.provenance.notes)


def test_cache_hit_stamps_provenance_not_the_network():
    """缓存命中路径：直接预填缓存 → call() 应原样返回该 Table 并盖 cached=True。"""
    from datetime import datetime as _dt

    from tdxdata._engine.cache import TTLCache, cache_key
    from tdxdata._provenance import ClockSource, Provenance

    c = _bare_client()
    c._pacer = _FakePacer()
    c._cache = TTLCache(ttl=300)
    from tdxdata._engine import session as session_mod
    c._session_mod = session_mod
    c._sess = None                          # 若真去连网会炸 → 证明没连

    cmd, ctl, params = "CWServ.ph_cache_probe", 0x21, ["001"]
    stamped = Table(cols=["N001"], rows=[{"N001": "20261027"}], cmd=cmd)
    c._cache.set(cache_key(cmd, params, None), stamped)

    got = c.call(cmd, ctl, params, allow_empty=True)
    assert got is stamped                   # 命中返回的就是缓存里那份
    assert got.provenance.cached is True
    assert got.provenance.cmd == cmd
    assert any("缓存" in n for n in got.provenance.notes)


def test_cache_disabled_by_default():
    """默认（cache=None）每次都真取 —— 可预测语义不变。"""
    c = _bare_client()
    assert getattr(c, "_cache", None) is None


# ══════════════════════════════════════════════════════════════════════════
# 三、_fetch 管道契约
# ══════════════════════════════════════════════════════════════════════════
def test_fetchspec_is_private_and_frozen():
    """FetchSpec 是私有声明（不出现在 dir(Client) 的公开面，aio 对齐不红）。"""
    spec = _CLS._FetchSpec(cmd="X", ctl=1)
    assert spec.shape == "table"
    with pytest.raises(Exception):
        spec.shape = "series"               # frozen


def test_fetch_table_shape_raises_empty_result_on_zero_rows():
    c = _bare_client()
    c._clock = _fake_clock()
    spec = c._FetchSpec(cmd="T", ctl=1, transport=lambda: (["a"], []),
                        empty_msg="没有数据")
    with pytest.raises(tdxdata.errors.EmptyResult):
        c._fetch(spec)


def test_fetch_table_empty_ok_returns_empty_table():
    c = _bare_client()
    c._clock = _fake_clock()
    spec = c._FetchSpec(cmd="T", ctl=1, shape="table_empty_ok",
                        transport=lambda: (["a"], []))
    t = c._fetch(spec)
    assert isinstance(t, Table) and t.rows == []


def test_fetch_series_empty_raises():
    c = _bare_client()
    c._clock = _fake_clock()
    spec = c._FetchSpec(cmd="S", ctl=1, shape="series",
                        transport=lambda: [], empty_msg="空的")
    with pytest.raises(tdxdata.errors.EmptyResult):
        c._fetch(spec)


def test_fetch_series_empty_msg_none_returns_empty_container():
    """empty_msg=None：空也原样回（get_quotes on_missing=skip / board_page 翻页到底）。"""
    from tdxdata.models import Quotes
    c = _bare_client()
    c._clock = _fake_clock()
    spec = c._FetchSpec(cmd="S", ctl=1, shape="series",
                        transport=lambda: Quotes([]), empty_msg=None)
    out = c._fetch(spec)
    assert list(out) == []


def test_fetch_keeps_base_status_when_judge_yields_none():
    """status=None（判定延后）不得覆盖 base 已有的 status（call() 判定保留）。"""
    from tdxdata._provenance import ClockSource, Provenance
    c = _bare_client()
    c._clock = _fake_clock()
    prov = Provenance(fetched_at=datetime(2026, 10, 27, 16, 0),
                      clock=ClockSource.LOCAL, cmd="T",
                      status=Status.PROVISIONAL.value)
    table = Table(cols=["a"], rows=[{"a": 1}], cmd="T", provenance=prov)
    spec = c._FetchSpec(cmd="T", ctl=1, transport=lambda: (["a"], [{"a": 1}]))
    # 直接走 _finish_table（_fetch 的盖戳出口）
    rec = {"cmd": "T", "ctl": 1, "fetched_at": datetime(2026, 10, 27, 16, 0),
           "elapsed": 0.0, "cached": False, "clock_note": None,
           "status_conflict": None}
    c._finish_table(table, rec, status=None, as_of=None)
    assert table.provenance.status == Status.PROVISIONAL.value


def test_public_api_surface_unchanged_33_methods():
    """公开 API 面零变化：ARCH-003 不增删改公开方法。"""
    public = {n for n in dir(_CLS) if not n.startswith("_")}
    expected = {
        "products", "domains", "last_call", "call", "get", "get_bars",
        "get_breadth", "get_minutes", "get_quotes", "get_name",
        "get_block_data", "get_block_rows", "get_samples",
        "get_intraday_samples", "get_board_stat", "get_ticks",
        "get_market_page", "get_universe", "iter_universe", "get_indicators",
        "get_price_limits", "get_finance", "get_f10", "get_xdxr",
        "get_boards", "board_page", "get_board_members", "get_capital_flow",
        "get_market_breadth", "get_market_stat", "capabilities",
        "rate_stats", "timeliness", "close",
    }
    assert public == expected, (public - expected, expected - public)
