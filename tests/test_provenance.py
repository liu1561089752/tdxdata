# -*- coding: utf-8 -*-
r"""`Provenance` 一等字段契约（ARCH-002 专项守卫）。

守什么
------
1. **字段即契约**：`fetched_at` / `clock` / `host` / `cmd` / `ctl` / `cached` /
   `elapsed` / `status` / `as_of` / `data_date` / `notes` / `extra` 全部是
   dataclass 字段，语义访问不再需要字符串键。
2. **DEF-018**：`fetched_at` 必须是 `datetime`；naive 一律补 UTC+8（aware 化），
   非 datetime 当场 `TypeError`。时区信息缺失不再是"不知道来源"的借口 ——
   风险由 `clock` 字段如实表达。
3. **DEF-019**：`clock` 必须是 `ClockSource` 枚举实例，字符串/None 一律 `TypeError`
   —— 时钟可信度是类型锁，不许自由字符串表达。
4. **不可变**：frozen dataclass，就地改属性直接炸；只能走 `_replace_for` /
   `with_notes` / `with_extra`。
5. **notes / extra 的收敛纪律**：notes 收成 tuple；extra 里值为 `None` 的键不收。

红线：`tests/test_*.py` 里不许出现 `Client(` / `Session(` 字面量 —— 本文件
不碰网络（只测数据类），用 `getattr` 取类同样不写那种字面量。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                        # noqa: BLE001
    pass

from tdxdata._provenance import ClockSource, Provenance

_AT = datetime(2026, 10, 27, 14, 44)          # naive（会被补 UTC+8）
_TZ8 = timezone(timedelta(hours=8))


# ══════════════════════════════════════════════════════════════════════════
# 一、字段即契约
# ══════════════════════════════════════════════════════════════════════════
def test_provenance_has_the_twelve_contract_fields():
    """字段清单是 API 面：少一个多一个都算契约破坏。"""
    fields = {f for f in Provenance.__dataclass_fields__}
    assert fields == {"fetched_at", "clock", "host", "cmd", "ctl", "cached",
                      "elapsed", "status", "as_of", "data_date", "notes",
                      "extra"}, fields


def test_frozen_immutability():
    """frozen：就地改属性直接炸（出处是**事实记录**，不许事后篡改）。"""
    p = Provenance(fetched_at=_AT, clock=ClockSource.SERVER, cmd="T")
    with pytest.raises(Exception):
        p.status = "final"


def test_clock_source_is_str_enum_with_three_states():
    assert issubclass(ClockSource, str)
    assert {c.value for c in ClockSource} == {"server", "hybrid", "local"}


# ══════════════════════════════════════════════════════════════════════════
# 二、DEF-018：fetched_at 的时区纪律
# ══════════════════════════════════════════════════════════════════════════
def test_def018_naive_datetime_gets_utc8_attached():
    """naive → 补 UTC+8（aware 化），不猜、不拒收、不改钟面值。"""
    p = Provenance(fetched_at=_AT, clock=ClockSource.SERVER)
    assert p.fetched_at.tzinfo is not None
    assert p.fetched_at.utcoffset() == timedelta(hours=8)
    # 钟面值不变（只补时区标签，不改时刻）
    assert p.fetched_at.replace(tzinfo=None) == _AT


def test_def018_aware_datetime_is_kept_as_is():
    at = _AT.replace(tzinfo=_TZ8)
    p = Provenance(fetched_at=at, clock=ClockSource.SERVER)
    assert p.fetched_at is at or p.fetched_at == at
    assert p.fetched_at.utcoffset() == timedelta(hours=8)


def test_def018_non_datetime_is_type_error():
    """字符串/None 冒充时刻 → `TypeError`（DEF-018 类型锁）。"""
    for bad in ("2026-10-27 14:44:00", None, 1729000000, _AT.date()):
        with pytest.raises(TypeError):
            Provenance(fetched_at=bad, clock=ClockSource.SERVER)


# ══════════════════════════════════════════════════════════════════════════
# 三、DEF-019：clock 的枚举类型锁
# ══════════════════════════════════════════════════════════════════════════
def test_def019_string_clock_is_type_error():
    """`clock="server"`（旧写法）必须当场炸 —— 可信度不许用自由字符串表达。"""
    with pytest.raises(TypeError):
        Provenance(fetched_at=_AT, clock="server")


def test_def019_none_and_other_junk_clock_is_type_error():
    for bad in (None, 3, True, ["server"]):
        with pytest.raises(TypeError):
            Provenance(fetched_at=_AT, clock=bad)


def test_def019_replace_for_revalidates_clock():
    """`_replace_for` 是唯一合法的"改"入口，它必须**重过**同一道类型锁。"""
    p = Provenance(fetched_at=_AT, clock=ClockSource.SERVER)
    with pytest.raises(TypeError):
        p._replace_for(clock="local")
    q = p._replace_for(clock=ClockSource.LOCAL)
    assert q.clock is ClockSource.LOCAL and p.clock is ClockSource.SERVER


# ══════════════════════════════════════════════════════════════════════════
# 四、notes / extra 收敛纪律
# ══════════════════════════════════════════════════════════════════════════
def test_notes_coerced_to_tuple_and_falsy_dropped():
    p = Provenance(fetched_at=_AT, clock=ClockSource.SERVER,
                   notes=["a", "b"])
    assert p.notes == ("a", "b")
    more = p.with_notes("c", "", "d")     # with_notes 丢空串
    assert more.notes == ("a", "b", "c", "d")
    # with_notes 返回新实例（frozen）
    assert more is not p


def test_with_extra_drops_none_values():
    p = Provenance(fetched_at=_AT, clock=ClockSource.SERVER)
    q = p.with_extra(pages=3, missing=None, truncated=True)
    assert q.extra == {"pages": 3, "truncated": True}
    # None 值键不收 = "没拿到"与"没这回事"同形，extra 不留噪声键


def test_to_dict_is_json_safe():
    import json

    p = Provenance(fetched_at=_AT, clock=ClockSource.HYBRID, host="h",
                   cmd="C", ctl=17, status="final", as_of="2026-10-27",
                   notes=("n1",), extra={"k": 1})
    d = p.to_dict()
    json.dumps(d, ensure_ascii=False)        # 必须可直接序列化
    assert d["clock"] == "hybrid"
    assert d["fetched_at"] == "2026-10-27 14:44:00+08:00"
    assert d["notes"] == ["n1"]
    assert d["extra"] == {"k": 1}


def test_series_and_table_carry_provenance_first_class():
    """容器/表的一等字段：赋非 Provenance 直接 `TypeError`（旧 dict 面已移除）。"""
    from tdxdata.models import Bar, Bars, Table

    bars = Bars([Bar(market=1, code="600519", date="20261027", open=1, high=1,
                     low=1, close=1, period="day")],
                provenance=Provenance(fetched_at=_AT, clock=ClockSource.SERVER))
    assert bars.provenance is not None
    # 切片共享同一个 frozen 实例（事实记录不复制）
    assert bars[-1:].provenance is bars.provenance

    t = Table(cols=["a"], rows=[{"a": 1}])
    assert t.provenance is None              # 空 出处 = None，不是空 Provenance
    with pytest.raises(TypeError):
        Table(cols=["a"], rows=[{"a": 1}], provenance={"status": "final"})
