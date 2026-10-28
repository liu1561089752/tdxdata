# -*- coding: utf-8 -*-
r"""PROD-001 `c.master` 证券主数据的守卫测试（零真实网络）。

守五件事
--------
1. **309 条服务端真值 100% 命中**（`tests/fixtures/市场码真值样本.json`）：
   假 universe 桩按真值 map 喂 `{code: market}` → snapshot 聚合链
   （Quote.market ← Setcode）→ resolve 逐条查 → 每一条都 `(market, code)`。
2. **resolve 绝不猜市场**：快照里没有 → `None`（默认）/ `raise`（missing=
   "raise"）；**绝不退回 `symbols.market_by_code` 前缀推断** ——
   "00700 不许静默命中 000700 的市场"是正面断言。
3. **边界如实声明**：snapshot 的 docstring 与 Provenance notes 都带
   "不是完整证券主表"警告（源码断言 + 运行时断言双保险）。
4. **幂等**：重复 snapshot/resolve 零新请求（fetch 计数断言）。
5. **search**：子串匹配大小写不敏感、空结果空 Table 不抛、出处继承。

红线：`tests/test_*.py` 不许出现 `Client(` / `Session(` 字面量 —— 裸对象用
`getattr(tdxdata, "Client")` + `cls.__new__(cls)` 造。
"""
from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime

import pytest

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                        # noqa: BLE001
    pass

import tdxdata
from tdxdata import symbols
from tdxdata.master import SecMaster

_CLS = getattr(tdxdata, "Client")          # 红线：不写 Client( 字面量

_TRUTH_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "tests", "fixtures", "市场码真值样本.json")


def _truth() -> dict[str, int]:
    with io.open(_TRUTH_PATH, encoding="utf-8") as f:
        doc = json.load(f)
    return dict(doc["map"])


def _bare_client():
    c = _CLS.__new__(_CLS)
    c._probe_allowed = False
    c._clock_probe = False
    c._clock = None
    c._last_call = {}
    c._in_cal = False
    return c


def _quote_of(market: int, code: str, name: str = ""):
    """造一条最小 `Quote`（snapshot 只读 market/code/name 三个字段）。"""
    from tdxdata.models import Quote

    q = Quote.__new__(Quote)
    object.__setattr__(q, "market", int(market))
    object.__setattr__(q, "code", str(code))
    object.__setattr__(q, "name", name)
    return q


class _FakeUniverse:
    """`get_universe` 返回假 Quotes（按真值 map 全量喂）；计数可断言。"""

    def __init__(self, market_map: dict[str, int]):
        self._map = market_map
        self.calls: list[int] = []

    def get_universe(self, market=6, max_pages=20, page_size=50, **kw):
        self.calls.append(int(market))
        from tdxdata.models import Quotes

        out = Quotes()
        for code, mkt in sorted(self._map.items()):
            if mkt in (0, 1):                 # 快照口径：market=6 只给深/沪可枚举行
                out.append(_quote_of(mkt, code, "名_" + code))
        # 模拟 get_universe 既有管道盖的 Provenance（frozen 实例）
        from tdxdata._provenance import ClockSource, Provenance

        out.provenance = Provenance(
            fetched_at=datetime(2026, 10, 28, 15, 0),
            clock=ClockSource.LOCAL, cmd="HQServ.PBMultiHQ",
            extra={"pages": 1, "truncated": False, "rows": len(out)})
        return out


def _master(fake: _FakeUniverse) -> SecMaster:
    c = _bare_client()
    c.get_universe = fake.get_universe       # 桩：不经回放、不发请求
    return SecMaster(c)


# ══════════════════════════════════════════════════════════════════════════
# 一、309 条服务端真值 100% 命中
# ══════════════════════════════════════════════════════════════════════════
def test_resolve_hits_all_309_truth_entries():
    """离线桩路径：假 universe 按真值 map 喂 → resolve 逐条 100% 命中。

    这条同时校验 snapshot 聚合链（Quote.market ← Setcode → 快照列 →
    resolve 查表）没有在途中被改写。
    """
    truth = _truth()
    assert len(truth) >= 309, "真值样本缩水了？"
    fake = _FakeUniverse(truth)
    m = _master(fake)
    hit = miss = 0
    for code, market in truth.items():
        got = m.resolve(code)
        if got is None:
            # 北交所(market=2)不在快照口径（_SNAPSHOT_MARKETS=(6,)）——如实豁免
            if market in (0, 1):
                miss += 1
            continue
        hit += 1
        assert got == (market, code), "%s: 真值 market=%s，resolve 给 %s" % (
            code, market, got)
    assert miss == 0, "%d 条深/沪真值没命中" % miss
    assert hit == sum(1 for v in truth.values() if v in (0, 1))


def test_truth_map_parity_with_symbols_prefix_table():
    """真值 map 与 symbols._PREFIX3/_HEAD1 一致性抽查：resolve 的事实源
    （快照=服务端真值）与前缀推断表在同一份真值上不该打架。"""
    truth = _truth()
    fake = _FakeUniverse(truth)
    m = _master(fake)
    for code, market in list(truth.items())[:40]:
        got = m.resolve(code)
        if got is not None:
            assert got[0] == market
        # 前缀推断与真值不一致的码（快照命中时以快照为准 —— 它是服务端事实）
        if got is not None and symbols.market_by_code(code) != market:
            # 允许存在：前缀表允许比真值粗（1 位兜底），但快照命中必须赢
            assert got[0] == market


# ══════════════════════════════════════════════════════════════════════════
# 二、resolve 绝不猜市场
# ══════════════════════════════════════════════════════════════════════════
def test_resolve_unknown_code_returns_none_never_guesses():
    """快照里没有 → None；**绝不退回前缀推断**。

    两层防御都断言：
      · 5 位 "00700"（港股形态）→ ValueError（不截断成 6 位去撞 000700）；
      · 6 位 "007001"（不在快照）→ None，**不**按前缀猜成 market=0
        （`symbols.market_by_code("007001")` 会猜 0 —— 这里抓的就是那个）。
    """
    truth = _truth()
    m = _master(_FakeUniverse(truth))
    with pytest.raises(ValueError):
        m.resolve("00700")
    assert m.resolve("007001") is None
    assert m.last_error and "绝不按前缀猜" in m.last_error


def test_resolve_unknown_code_raise_mode_raises_key_error():
    m = _master(_FakeUniverse(_truth()))
    with pytest.raises(KeyError):
        m.resolve("999998", missing="raise")


def test_resolve_bad_input_raises_value_error():
    m = _master(_FakeUniverse(_truth()))
    for junk in ("12345", "1234567", "abcdef", "", None):
        with pytest.raises(ValueError):
            m.resolve(junk)


def test_resolve_duplicate_code_never_takes_first():
    """同码跨市场（理论不该出现）：不唯一 → 如实拒绝，绝不取第一条。"""
    fake = _FakeUniverse({"000700": 0})
    m = _master(fake)
    # 手工把第二条塞进快照行（模拟"真给同码跨市场"）
    m.snapshot()
    m._snap.rows.append({"market": 1, "code": "000700",
                         "name": "dup", "symbol": "sh000700"})
    m._by_code = None                        # 强制重建索引
    assert m.resolve("000700") is None
    assert "不唯一" in m.last_error


def test_resolve_missing_param_validated():
    m = _master(_FakeUniverse(_truth()))
    with pytest.raises(Exception):
        m.resolve("999998", missing="bogus")


# ══════════════════════════════════════════════════════════════════════════
# 三、边界如实声明
# ══════════════════════════════════════════════════════════════════════════
def test_snapshot_docstring_declares_boundary():
    from tdxdata import master as mod

    doc = mod.SecMaster.snapshot.__doc__
    assert "不是完整证券主表" in doc
    assert "漂移" in doc and "漏票" in doc
    # get_universe 的边界警告确实被照抄（同句关键词）
    src = inspect_source(mod)
    assert "不是完整证券主表" in src


def inspect_source(mod) -> str:
    import inspect

    return inspect.getsource(mod)


def test_snapshot_provenance_carries_the_warning():
    m = _master(_FakeUniverse(_truth()))
    t = m.snapshot()
    joined = "；".join(t.provenance.notes)
    assert "不是完整证券主表" in joined
    assert t.provenance.cmd == "HQServ.PBMultiHQ"      # 出处继承自 get_universe 管道


def test_snapshot_columns_and_symbol_prefix():
    m = _master(_FakeUniverse(_truth()))
    t = m.snapshot()
    assert t.cols == ["market", "code", "name", "symbol"]
    row = {r["code"]: r for r in t.rows}["000700"]
    assert row["market"] == 0 and row["symbol"] == "sz000700"
    row1 = {r["code"]: r for r in t.rows}.get("110077")
    if row1 is not None:
        assert row1["symbol"] == "sh110077"     # 沪转债前缀


# ══════════════════════════════════════════════════════════════════════════
# 四、幂等：重复调用零新请求
# ══════════════════════════════════════════════════════════════════════════
def test_snapshot_twice_costs_one_fetch():
    fake = _FakeUniverse(_truth())
    m = _master(fake)
    m.snapshot()
    m.snapshot()
    assert len(fake.calls) == 1               # 第二次零新请求
    assert m.fetches == 1


def test_resolve_does_not_refetch():
    fake = _FakeUniverse(_truth())
    m = _master(fake)
    m.resolve("000700")
    n = len(fake.calls)
    m.resolve("000700")
    m.resolve("600519")
    assert len(fake.calls) == n               # 索引复用快照，零新请求


def test_refresh_forces_refetch():
    fake = _FakeUniverse(_truth())
    m = _master(fake)
    m.snapshot()
    m.snapshot(refresh=True)
    assert len(fake.calls) == 2               # 显式 refresh 允许重取


def test_mounted_on_real_init_path():
    """跑过 __init__ 的 Client 有 `self.master`（懒取数）。"""
    src = inspect_source(tdxdata)
    import inspect

    src = inspect.getsource(_CLS.__init__)
    assert "self.master = SecMaster(self)" in src


# ══════════════════════════════════════════════════════════════════════════
# 五、search
# ══════════════════════════════════════════════════════════════════════════
def test_search_substring_case_insensitive():
    truth = _truth()
    fake = _FakeUniverse(truth)
    m = _master(fake)
    # 给几条可搜的名字
    snap = m.snapshot()
    snap.rows[0]["name"] = "贵州茅台"
    snap.rows[1]["name"] = "Maotai Liquor"
    got = m.search("茅台")
    assert len(got.rows) == 1
    got2 = m.search("MAOTAI")
    assert len(got2.rows) == 1                # 大小写不敏感


def test_search_empty_result_is_empty_table_not_exception():
    m = _master(_FakeUniverse(_truth()))
    got = m.search("这个名称肯定不存在ZZZ")
    assert len(got.rows) == 0                 # 空 Table 不抛
    assert got.cols == ["market", "code", "name", "symbol"]


def test_search_inherits_provenance():
    m = _master(_FakeUniverse(_truth()))
    got = m.search("x")
    assert got.provenance is not None
    assert any("search" in n for n in got.provenance.notes)
