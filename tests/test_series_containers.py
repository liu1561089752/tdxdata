# -*- coding: utf-8 -*-
r"""容器与模型契约（**离线**）：`Series` 家族、`Bar.provisional`、`Dataset.data_date`。

ARCH-002（破坏性，无兼容期）：旧 `meta` dict 公开面已移除，出处凭据是一等字段
`provenance`（`Provenance` frozen dataclass）。本文件全部断言按新口径重写：
语义访问走 `bars.status` / `bars.as_of` / `bars.clock`；结构化旁证走
`bars.provenance.extra[...]`；说明性文本走 `provenance.notes`。

本文件守四件事
--------------
1. `Bars` / `Quotes` / `Ticks` / `Minutes` 与 `list` **行为完全一致**（含 `==`）；
2. `provenance` 与 `as_of` / `status` / `clock` 等只读属性可读；
3. `Bar.provisional` 必须留在**字段列表末尾**（`slots=True` 下位置参数会变，
   插在中间会把既有调用错位 —— 用一个断言把这条钉死）；
4. `Dataset.data_date`：能从首行 `N001` 推就给 `date`，推不出就是 `None`
   —— **绝不拿今天顶替**（本项目红线是 unknown ≠ 0）。

红线（`_dev/check.py` 第 7 条离线扫 `tests/test_*.py`）：不许出现
`Client(` / `Session(` 字面量。本文件**根本不建 Client**，纯模型断言。
"""
from __future__ import annotations

import dataclasses
from datetime import date, datetime

import pytest

import tdxdata
from tdxdata.models import (
    Bar, Bars, Dataset, MinutePoint, Minutes, ProductInfo, Provenance,
    Quote, Quotes, Row, SecurityInfo, Series, Table, Tick, Ticks,
)
from tdxdata._provenance import ClockSource

D = date(2026, 10, 27)
_AT = datetime(2026, 10, 27, 14, 44)


def _prov(**kw) -> Provenance:
    """测试用出处：clock 可给字符串（转枚举）；其余未知 kw 进 extra。"""
    kw.setdefault("fetched_at", _AT)
    clock = kw.pop("clock", "server")
    if not isinstance(clock, ClockSource):
        clock = ClockSource(clock)
    known = {f.name for f in dataclasses.fields(Provenance)} - {"clock"}
    core = {k: kw.pop(k) for k in list(kw) if k in known}
    extra = kw.pop("extra", {}) | kw
    return Provenance(clock=clock, extra=extra, **core)


# ══════════════════════════════════════════════════════════════════════════
# 一、list 子类：行为必须与 list 一模一样
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("cls", [Series, Bars, Quotes, Ticks, Minutes])
def test_containers_are_real_lists(cls):
    """`isinstance(x, list)` 必须为真 —— 既有 396 条用例大量按 list 用。"""
    got = cls([3, 1, 2])
    assert isinstance(got, list)
    assert isinstance(getattr(tdxdata, cls.__name__), type)
    assert issubclass(cls, list)


def test_bars_behave_exactly_like_list():
    """`len` / 迭代 / 切片 / 索引 / `== [...]` / `in` / `+` 全与 list 一致。"""
    plain = [1, 2, 3, 4, 5]
    bars = Bars(plain)

    assert len(bars) == len(plain) == 5
    assert list(bars) == plain
    assert [x for x in bars] == plain
    assert bars[0] == 1 and bars[-1] == 5
    assert bars[1:3] == [2, 3] == plain[1:3]
    assert bars[::-1] == plain[::-1]
    assert bars == plain, "`== [..]` 必须成立（不是「内容相等但类型不同」）"
    assert plain == bars
    assert 3 in bars and 99 not in bars
    assert bars + [6] == plain + [6]
    assert bars.index(4) == 3
    assert bars.count(1) == 1
    assert bars.copy() == plain
    assert isinstance(bars.copy(), list)
    assert repr(bars).startswith("Bars(")


def test_series_accepts_empty_iterable_and_default_provenance():
    """空容器可用；不传 provenance 时是 **None**（出处缺席 ≠ 编造空凭据）。"""
    for cls in (Series, Bars, Quotes, Ticks, Minutes):
        got = cls()
        assert isinstance(got, list) and len(got) == 0
        assert got.provenance is None
        assert got.as_of is None and got.status is None
        assert got.clock is None and got.notes == ()
        assert cls(provenance=None).provenance is None


# ══════════════════════════════════════════════════════════════════════════
# 二、provenance 与只读便捷属性
# ══════════════════════════════════════════════════════════════════════════
def test_provenance_is_readable_and_convenience_properties_work():
    """`provenance` 可读；`as_of` / `status` / `fetched_at` / `source` / `elapsed` 直读出处。"""
    prov = _prov(as_of="2026-10-27", status="final", elapsed=2.19,
                 host="10.0.0.9", cmd="HQServ.PBFXT", ctl=14,
                 clock="server")
    bars = Bars([1, 2], provenance=prov)
    assert bars.provenance is prov          # frozen：共享即安全
    assert bars.as_of == "2026-10-27"
    assert bars.status == "final"
    assert bars.fetched_at.replace(tzinfo=None) == _AT
    assert bars.source == ("10.0.0.9", "HQServ.PBFXT", 14)
    assert bars.elapsed == 2.19
    assert bars.clock is ClockSource.SERVER
    assert bars.notes == ()

    # 出处是**实例属性**，不同实例互不串（改 = 造新 Provenance，旧对象不变）
    other = Bars([1, 2])
    assert other.provenance is None
    other.provenance = prov._replace_for(status="provisional")
    assert bars.status == "final", "改出处必须造新对象，不许污染旧容器"


def test_provenance_frozen_not_mutated_by_container():
    """出处不可变：任何"修改"都是新实例（frozen dataclass 的类型保证）。"""
    prov = _prov(status="final")
    bars = Bars([1], provenance=prov)
    with pytest.raises(Exception):
        prov.status = "provisional"        # frozen dataclass：当场炸
    assert bars.status == "final"


# ══════════════════════════════════════════════════════════════════════════
# 三、Bar.provisional 必须在**末尾**
# ══════════════════════════════════════════════════════════════════════════
def test_bar_last_field_is_provisional():
    """`Bar` 的最后一个字段必须是 `provisional`。

    `@dataclass(slots=True)` 下加字段会动**位置参数**：插在中间的话，
    任何按位置构造 `Bar(...)` 的代码都会**静默错位**（把 adjust 的值喂给新字段）。
    所以新字段只许加在末尾，这条断言就是防止以后有人"顺手插一行"。
    """
    names = [f.name for f in dataclasses.fields(Bar)]
    assert names[-1] == "provisional", names
    assert names[-3:] == ["raw", "adjust", "provisional"], names

    bar = Bar(market=1, code="600519", date="20261027",
              open=1.0, high=1.0, low=1.0, close=1.0)
    assert bar.provisional is False, "默认必须是 False（历史根/已收盘根）"
    assert bar.adjust == "NONE"
    bar.provisional = True
    assert bar.provisional is True


def test_bar_is_slots_dataclass_and_cannot_grow_attributes():
    """`Bar` 仍是 slots dataclass（乱挂属性要当场报错，不静默变胖）。"""
    bar = Bar(market=1, code="600519", date="20261027",
              open=1.0, high=1.0, low=1.0, close=1.0)
    assert dataclasses.is_dataclass(bar)
    with pytest.raises(AttributeError):
        bar.not_a_field = 1


# ══════════════════════════════════════════════════════════════════════════
# 四、Table / Dataset 的便捷属性
# ══════════════════════════════════════════════════════════════════════════
def test_table_as_of_and_status_read_provenance_without_changing_structure():
    """`Table` 结构不变；`provenance.extra` 原有旁证键一个不丢。"""
    prov = _prov(cols_union=["N001", "N002", "N003"],
                 as_of="2026-10-26", status="delayed")
    table = Table(cols=["N001", "N002"], rows=[{"N001": "20261026", "N002": "x"}],
                  provenance=prov)
    assert table.as_of == "2026-10-26"
    assert table.status == "delayed"
    assert table.provenance.extra["cols_union"] == ["N001", "N002", "N003"], \
        "原有旁证键必须还在"
    assert table.shape == (1, 2)

    empty = Table(cols=[], rows=[])
    assert empty.as_of is None and empty.status is None


def test_dataset_data_date_from_n001():
    """`N001` 是日期形态 → `date`；两种写法（`YYYYMMDD` / `YYYY-MM-DD`）都认。"""
    for raw in ("20261026", 20261026, "2026-10-26"):
        ds = Dataset(name="lrmx", table=Table(cols=["N001"], rows=[{"N001": raw}]))
        assert ds.data_date == date(2026, 10, 26), raw


def test_dataset_data_date_prefers_provenance_then_n001():
    """`provenance.data_date` 优先于 `N001`（谁知道谁写，最可靠的那份赢）。"""
    ds = Dataset(name="x", table=Table(
        cols=["N001"], rows=[{"N001": "20261026"}],
        provenance=_prov(data_date="2026-10-23")))
    assert ds.data_date == date(2026, 10, 23)

    # provenance 里是垃圾 → 退回 N001
    ds2 = Dataset(name="x", table=Table(
        cols=["N001"], rows=[{"N001": "20261026"}],
        provenance=_prov(data_date="不是日期")))
    assert ds2.data_date == date(2026, 10, 26)


def test_dataset_data_date_is_none_when_unparseable_never_today():
    """推不出来就是 `None` —— **绝不许拿今天顶替**（红线：unknown ≠ 0）。"""
    today = date.today()
    cases = [
        Table(cols=["N001"], rows=[]),                       # 没有行
        Table(cols=["N002"], rows=[{"N002": "20261026"}]),   # 没有 N001 列
        Table(cols=["N001"], rows=[{"N001": "行业名称"}]),     # 不是日期
        Table(cols=["N001"], rows=[{"N001": None}]),         # 空值
        Table(cols=["N001"], rows=[{"N001": "2026"}]),       # 只有年份
        Table(cols=["N001"], rows=[{"N001": "20261332"}]),   # 13 月 32 日
    ]
    for table in cases:
        got = Dataset(name="x", table=table).data_date
        assert got is None, "推不出数据日必须给 None，实际 %r（表 %r）" % (got, table.cols)
        assert got != today, "绝不许拿今天顶替"


# ══════════════════════════════════════════════════════════════════════════
# 五、其余模型不受影响（回归型断言）
# ══════════════════════════════════════════════════════════════════════════
def test_other_models_are_untouched():
    """`Row` / `Quote` / `Tick` / `MinutePoint` / `ProductInfo` 的口径不变。"""
    row = Row(["a", "b"], [1, 2])
    assert row["a"] == 1 and row[1] == 2 and isinstance(row, dict)

    q = Quote(market=1, code="600519", price=10.0)
    assert q.bid == [] and q.ask == [] and q.vol is None and q.amount is None
    assert q.symbol == "sh600519"

    t = Tick(market=1, code="600519")
    # 阶段 4：`trade_count` / `bs_flag` 都是「声明式不可用」——
    # 恒为 None，不是填 0；原因见 models.UNPROVIDED_TICK_FIELDS。
    assert t.trade_count is None and t.bs_flag is None, "拿不到就是 None，不填 0"

    m = MinutePoint(code="600519", time="09:30", price=1.0, avg_price=1.0)
    assert m.vol == 0.0

    assert SecurityInfo(market=1, code="600519").symbol == "sh600519"
    info = ProductInfo(name="lhb", cmd="CWServ.x", ctl=0x1F)
    assert info.ctl_hex == "0x1F"


# ══════════════════════════════════════════════════════════════════════════
# 八、切片/复制必须**保住类型与 provenance**（阶段 1.5 → ARCH-002）
# ══════════════════════════════════════════════════════════════════════════
# 缺陷形态：`Bars` 是 `list` 子类，`list.__getitem__(slice)` 返回的是**普通 list**
# —— 于是 `bars[-60:]` 一取子集，`status`/`as_of`/来源全部悄悄蒸发。
# 而"这批数是不是定稿的"恰恰是切完片之后才要判的事。
def test_slice_keeps_subclass_and_provenance():
    """`bars[10:]` 仍是 `Bars`，并且带着**同一份** provenance（frozen，共享安全）。"""
    prov = _prov(status="provisional", as_of="2026-10-27 14:44:00",
                 host=None, cmd="HQServ.PBFXT", ctl=14, clock="hybrid")
    bars = Bars(list(range(20)), provenance=prov)

    tail = bars[10:]
    assert isinstance(tail, Bars), type(tail)
    assert tail == list(range(10, 20))
    assert isinstance(tail, list)                # 老代码 `isinstance(x, list)` 仍然成立
    assert tail.provenance is prov, "切片不许丢 provenance"
    assert tail.status == "provisional"
    assert tail.as_of == prov.as_of
    assert tail.source == (None, "HQServ.PBFXT", 14)


def test_slice_provenance_is_shared_but_frozen():
    """切片与原容器共享同一份 frozen Provenance：谁也改不了它（只能造新实例）。"""
    prov = _prov(status="final")
    bars = Bars([1, 2, 3], provenance=prov)
    tail = bars[1:]
    assert tail.provenance is prov
    with pytest.raises(Exception):
        tail.provenance.status = "provisional"   # frozen：当场炸
    assert bars.status == "final"
    # 要"改"子集的出处 → 造新 Provenance 赋给子集，原容器不受影响
    tail.provenance = prov._replace_for(status="provisional")
    assert bars.status == "final"
    assert tail.status == "provisional"


def test_all_slice_shapes_keep_type_and_provenance():
    """步长、反向、越界、空切片 —— 各种切法都得保住类型与出处。"""
    prov = _prov(status="final")
    for cls in (Series, Bars, Quotes, Ticks, Minutes):
        obj = cls([1, 2, 3, 4], provenance=prov)
        for key in (slice(None), slice(1, 3), slice(None, None, 2),
                    slice(None, None, -1), slice(10, 20), slice(0, 0)):
            got = obj[key]
            assert isinstance(got, cls), "%s 的 %r 切片退化成了 %s" % (
                cls.__name__, key, type(got).__name__)
            assert got.provenance is prov, "%s 的 %r 切片丢了 provenance" % (
                cls.__name__, key)


def test_indexing_still_returns_the_element():
    """单下标必须照旧返回**元素本身**（不能返回只含一个元素的容器）。"""
    bars = Bars([10, 20, 30], provenance=_prov(status="final"))
    assert bars[0] == 10
    assert not isinstance(bars[0], Series)
    assert bars[-1] == 30


def test_copy_keeps_type_and_provenance():
    """`copy()` 也保住类型与出处（`list.copy()` 会退回普通 `list`）。"""
    prov = _prov(as_of="2026-10-27")
    bars = Bars([1, 2], provenance=prov)
    dup = bars.copy()
    assert isinstance(dup, Bars)
    assert dup == bars and dup.provenance is prov
