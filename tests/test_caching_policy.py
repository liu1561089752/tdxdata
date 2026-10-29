# -*- coding: utf-8 -*-
"""缓存策略（2026-10-29）：`cache="auto"` + 按命令实测 TTL。零网络。

三个必须钉住的性质
------------------
1. **TTL 来自实测**，不是统一常量：宏观 30 天、当日涨跌停 0 天（不缓存）。
2. **没证据就不缓存**：表里没有的命令 `default_ttl == 0`。
3. **旧行为一字不差**：`cache=None`（默认）与 `cache=TTLCache(ttl=…)` 的语义不变。
"""
from __future__ import annotations

import json
import os

import pytest

import tdxdata
from tdxdata import caching
from tdxdata._engine.cache import TTLCache

_CLS = getattr(tdxdata, "Client")


# ══════════════════════════════════════════════════════════════════════════
# 一、TTL 表：来自 data/cache-ttl.json
# ══════════════════════════════════════════════════════════════════════════
def test_ttl_table_ships_with_the_package():
    path = os.path.join(os.path.dirname(tdxdata.__file__), "data", "cache-ttl.json")
    assert os.path.exists(path), "TTL 表必须随包发布（package-data 里要含 data/*.json）"
    payload = json.load(open(path, encoding="utf-8"))
    assert payload["_meta"]["commands"] >= 200
    assert "依据" in payload["_meta"], "表里要写明依据，方便回答「这个 TTL 哪来的」"


def test_macro_product_gets_a_long_ttl():
    """宏观（hg_gdp）实测滞后 293 天 → 缓存 30 天。"""
    assert caching.ttl_for_cmd("hg_gdp") == 30 * 86400


def test_command_name_and_product_key_agree():
    """命令名与产品键两种写法要查到同一个值（用户手上通常是产品键）。"""
    assert caching.ttl_for_cmd("CWServ.ph_datacenter_hg_gdp") == caching.ttl_for_cmd("hg_gdp")


def test_intraday_product_is_never_cached():
    """当日涨跌停（滞后 0 天）→ ttl 0：宁可慢，不可把昨天的表当今天。"""
    assert caching.ttl_for_cmd("zdt_zdfx") == 0


def test_unknown_command_defaults_to_no_cache():
    assert caching.ttl_for_cmd("HQServ.PBNOSUCHCMD") == 0
    assert caching.default_ttl() == 0, "没实测的命令一律不缓存"


def test_policy_text_is_available():
    assert "cache-ttl.json" in caching.POLICY and "滞后" in caching.POLICY


# ══════════════════════════════════════════════════════════════════════════
# 二、auto_cache 对象本身：默认 ttl=0（时长逐条给）
# ══════════════════════════════════════════════════════════════════════════
def test_auto_cache_defaults_to_no_ttl_until_told():
    cache = caching.auto_cache()
    cache.set(("k",), "v")                    # 没给 ttl → 它的默认是 0 → 不存
    assert cache.get(("k",)) is None


def test_auto_cache_accepts_explicit_ttl():
    cache = caching.auto_cache()
    cache.set(("k",), "v", ttl=60)
    assert cache.get(("k",)) == "v"


# ══════════════════════════════════════════════════════════════════════════
# 三、Client 接线：写缓存时按命令查表
# ══════════════════════════════════════════════════════════════════════════
class _Spy:
    """记录每次 `set` 的 (key, ttl)；`get` 恒 miss。"""

    def __init__(self):
        self.sets = []

    def get(self, key, default=None):
        return default

    def set(self, key, value, ttl=None):
        self.sets.append((key, ttl))


def _bare():
    c = _CLS.__new__(_CLS)
    c._cache = _Spy()
    c._cache_auto = True
    return c


def test_auto_mode_writes_with_the_measured_ttl():
    c = _bare()
    c._cache_set(("k",), "CWServ.ph_datacenter_hg_gdp", object())
    assert c._cache.sets and c._cache.sets[0][1] == 30 * 86400


def test_auto_mode_skips_intraday_products():
    c = _bare()
    c._cache_set(("k",), "CWServ.ph_datacenter_zdt_zdfx", object())
    assert c._cache.sets == [], "ttl=0 的命令不该写进缓存"


def test_explicit_cache_object_keeps_its_own_ttl():
    """`cache=TTLCache(ttl=300)` 的语义不变：不按命令查表。"""
    c = _CLS.__new__(_CLS)
    c._cache = _Spy()
    c._cache_auto = False
    c._cache_set(("k",), "CWServ.ph_datacenter_zdt_zdfx", object())
    assert c._cache.sets == [(("k",), None)], "非 auto 模式不传 ttl，用缓存对象自己的默认值"


def test_bare_client_without_flag_does_not_crash():
    """`Client.__new__` 造的裸对象（老测试那种）没有 `_cache_auto`，也不能炸。"""
    c = _CLS.__new__(_CLS)
    c._cache = _Spy()
    c._cache_set(("k",), "CWServ.ph_datacenter_hg_gdp", object())
    assert c._cache.sets == [(("k",), None)]


def test_cache_none_is_still_the_default():
    """默认构造不带缓存（这条盯的是"别顺手把默认改成开"）。"""
    import inspect

    sig = inspect.signature(_CLS.__init__)
    assert sig.parameters["cache"].default is None


# ══════════════════════════════════════════════════════════════════════════
# 四、命中不走闸门（沿用旧性质，别被这次改动破坏）
# ══════════════════════════════════════════════════════════════════════════
def test_hit_returns_before_pacer(monkeypatch):
    """缓存命中时**一次闸门都不进**（旧的性能性质，auto 模式也要成立）。"""
    from tdxdata.models import Table

    calls = {"wait": 0}

    class _Pacer:
        limited_events = 0

        def wait(self):
            calls["wait"] += 1
            return 0.0

        def note_ok(self):
            pass

    c = _CLS.__new__(_CLS)
    c._pacer = _Pacer()
    table = Table(cols=["N001"], rows=[["1"]], cmd="CWServ.ph_datacenter_hg_gdp")
    cache = TTLCache(ttl=3600)

    from tdxdata._engine.cache import cache_key

    cache.set(cache_key("CWServ.ph_datacenter_hg_gdp", ["001", "1"], None), table)
    c._cache = cache
    c._cache_auto = True

    class _Mod:
        """只需 `body_of`：命中路径不该走到 `ask()`。"""

        @staticmethod
        def body_of(cmd, params):
            return b'{"Params":["001","1"]}'

        @staticmethod
        def parse(raw):                       # 真的被调到就说明没命中
            raise AssertionError("命中缓存却去解析回包了")

    c._session_mod = _Mod()
    c._last_call = {}
    c._clock = None

    out = c.call("CWServ.ph_datacenter_hg_gdp", 0x21, ["001", "1"])
    assert out is table
    assert calls["wait"] == 0, "命中缓存还要过闸门，就失去缓存的意义了"
