# -*- coding: utf-8 -*-
r"""容器与模型契约（**离线**）：`Series` 家族、`Bar.provisional`、`Dataset.data_date`。

为什么用 **list 子类** 而不是"新信封类型"
----------------------------------------
语义层要给每次取数挂上 `as_of` / `status` / `fetched_at` / `source` / `elapsed`。
做成 `{"data": [...], "meta": {...}}` 会**破坏**所有既有用法（`len(x)`、`x[0]`、
`for bar in x`、`x[-1].close`、`json.dumps(x)`），而本次改造的底线是"只许扩展"。
所以 meta 挂在**实例属性**上 —— 本文件的第一组断言就是钉这件事：
`isinstance(bars, list)` 必须恒为真，list 的全套行为一个不许变。

本文件守四件事
--------------
1. `Bars` / `Quotes` / `Ticks` / `Minutes` 与 `list` **行为完全一致**（含 `==`）；
2. `.meta` 与 `as_of` / `status` / `fetch_clock` 等只读属性可读；
3. `Bar.provisional` 必须留在**字段列表末尾**（`slots=True` 下位置参数会变，
   插在中间会把既有调用错位 —— 用一个断言把这条钉死）；
4. `Dataset.data_date`：能从首行 `N001` 推就给 `date`，推不出就是 `None`
   —— **绝不拿今天顶替**（本项目红线是 unknown ≠ 0）。

红线（`_dev/check.py` 第 7 条离线扫 `tests/test_*.py`）：不许出现
`Client(` / `Session(` 字面量。本文件**根本不建 Client**，纯模型断言。
"""
from __future__ import annotations

import dataclasses
from datetime import date

import pytest

import tdxdata
from tdxdata.models import (
    Bar, Bars, Dataset, MinutePoint, Minutes, ProductInfo, Quote, Quotes,
    Row, SecurityInfo, Series, Table, Tick, Ticks,
)

D = date(2026, 10, 27)


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


def test_series_accepts_empty_iterable_and_default_meta():
    """空容器可用；不传 meta 时是**空 dict**（不是 None，省得下游到处判空）。"""
    for cls in (Series, Bars, Quotes, Ticks, Minutes):
        got = cls()
        assert isinstance(got, list) and len(got) == 0
        assert got.meta == {}
        assert got.as_of is None and got.status is None
        assert got.fetch_clock is None and got.clock_note is None
        assert cls(meta=None).meta == {}


# ══════════════════════════════════════════════════════════════════════════
# 二、meta 与只读便捷属性
# ══════════════════════════════════════════════════════════════════════════
def test_meta_is_readable_and_convenience_properties_work():
    """`meta` 可读；`as_of` / `status` / `fetched_at` / `source` / `elapsed` 直读 meta。"""
    meta = {
        "as_of": "2026-10-27",
        "status": "final",
        "fetched_at": "2026-10-27 15:36:00",
        "source": {"host": "10.0.0.9", "cmd": "HQServ.PBFXT", "ctl": 14,
                   "clock": "server", "cached": False},
        "elapsed": 2.19,
        "fetch_clock": "server",
    }
    bars = Bars([1, 2], meta=meta)
    assert bars.meta == meta
    assert bars.as_of == "2026-10-27"
    assert bars.status == "final"
    assert bars.fetched_at == "2026-10-27 15:36:00"
    assert bars.source["cmd"] == "HQServ.PBFXT"
    assert bars.elapsed == 2.19
    assert bars.fetch_clock == "server"
    assert bars.clock_note is None

    # meta 是**实例属性**，不同实例互不共享（class-level 默认值会互相污染）
    other = Bars([1, 2])
    assert other.meta == {} and bars.meta is not other.meta
    other.meta["status"] = "provisional"
    assert bars.status == "final", "meta 必须是每个实例自己的 dict"


def test_meta_is_copied_not_aliased():
    """传进去的 dict 会被**复制**一份（外部改原 dict 不该影响容器）。"""
    src = {"status": "final"}
    got = Bars([1], meta=src)
    src["status"] = "provisional"
    assert got.status == "final"


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
def test_table_as_of_and_status_read_meta_without_changing_structure():
    """`Table` 结构不变，只多两个**只读**属性；`meta` 原有键一个不丢。"""
    table = Table(cols=["N001", "N002"], rows=[{"N001": "20261026", "N002": "x"}],
                  meta={"cols_union": ["N001", "N002", "N003"],
                        "as_of": "2026-10-26", "status": "delayed"})
    assert table.as_of == "2026-10-26"
    assert table.status == "delayed"
    assert table.meta["cols_union"] == ["N001", "N002", "N003"], "原有 meta 键必须还在"
    assert table.shape == (1, 2)

    empty = Table(cols=[], rows=[])
    assert empty.as_of is None and empty.status is None


def test_dataset_data_date_from_n001():
    """`N001` 是日期形态 → `date`；两种写法（`YYYYMMDD` / `YYYY-MM-DD`）都认。"""
    for raw in ("20261026", 20261026, "2026-10-26"):
        ds = Dataset(name="lrmx", table=Table(cols=["N001"], rows=[{"N001": raw}]))
        assert ds.data_date == date(2026, 10, 26), raw


def test_dataset_data_date_prefers_meta_then_n001():
    """`meta["data_date"]` 优先于 `N001`（谁知道谁写，最可靠的那份赢）。"""
    ds = Dataset(name="x", table=Table(
        cols=["N001"], rows=[{"N001": "20261026"}],
        meta={"data_date": "2026-10-23"}))
    assert ds.data_date == date(2026, 10, 23)

    # meta 里是垃圾 → 退回 N001
    ds2 = Dataset(name="x", table=Table(
        cols=["N001"], rows=[{"N001": "20261026"}], meta={"data_date": "不是日期"}))
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


def test_series_meta_vocabulary_stays_inside_allowed_keys():
    """meta 里出现的键必须都在「允许集」里 —— 但允许集有**两类**来源，别混。

    ① **盖章词表**（`client.META_KEYS`，11 个）：由 `_set_meta` / 容器 meta 统一写入。
       这类键是**回放时新增**的（老样本没有），所以必须逐个进黄金白名单
       `ALLOWED_ADDED_KEYS`，否则门禁会报"不在允许新增清单内"。
    ② **样本自冻结词表**：`get_indicators` / `get_breadth` / `call` 这类**派生表**
       自己带的 `period` / `series` / `field_map` / `note` / `unavailable` / `cols_union`。
       它们不走 `_set_meta`，而是**跟着样本一起被录进 payload** —— 回放时两边都有、
       值可比，所以**不需要**（也不该）进白名单：进了反而会让"回放期偷偷多出一个键"
       这种真回归不被发现。

    这条测试把两类**分开**断言：①必须 ⊆ 白名单；②必须 ∉ 白名单且与①不重叠。
    """
    import sys
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import golden_runner as gr

    # ① 盖章词表（阶段 1 的 11 个键）
    stamped = {
        "as_of", "status", "provisional", "fetched_at", "source", "elapsed",
        "fetch_clock", "clock_note", "status_note", "as_of_note", "data_date",
    }
    extra = stamped - set(gr.ALLOWED_ADDED_KEYS)
    assert not extra, "盖章词表里这些键不在黄金白名单里：%s" % sorted(extra)

    # ② 样本自冻结词表（派生表专有）：与①不重叠，且**不该**在白名单里
    sample_frozen = {
        "period", "series", "series_name", "field_map", "note", "unavailable",
        "cols_union", "source_note", "why", "missing_fields",
    }
    assert not (sample_frozen & stamped), "两类词表不许重叠，否则归属说不清"
    leaked = sample_frozen & set(gr.ALLOWED_ADDED_KEYS)
    assert not leaked, ("派生表专有的键不该进白名单（它们随样本一起冻结）：%s"
                        % sorted(leaked))

    # 合并起来就是「允许集」：`get_breadth` 会产出的键丢进去必须都通过
    allowed = set(gr.ALLOWED_ADDED_KEYS) | sample_frozen
    produced = stamped | {"period"}
    assert produced <= allowed, sorted(produced - allowed)


def test_source_dict_subkeyset_is_fixed():
    """`meta["source"]` 的子键集**固定为 5 个**（Lead 拍板，不许再长）。

    与上一条同源：`source` 是**来源凭据的容器**。黄金门禁只看"新增键"、
    不看值，所以子键一旦能随便长，"值不参与比对"就会变成偷偷塞数据的后门。
    """
    import sys
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import golden_runner as gr

    from tdxdata.client import META_SOURCE_KEYS

    assert META_SOURCE_KEYS == ("host", "cmd", "ctl", "clock", "cached")

    sample = {"host": None, "cmd": "HQServ.PBFXT", "ctl": 14,
              "clock": "server", "cached": False}
    assert tuple(sample) == META_SOURCE_KEYS
    # `source` 本身在白名单里（它的值不参与比对）→ 子键的形状只能靠这条钉住
    assert "source" in gr.ALLOWED_ADDED_KEYS

    bars = Bars([1], meta={"source": sample})
    assert bars.source == sample
    assert set(bars.source) == set(META_SOURCE_KEYS)
    assert bars.source["host"] is None, "host 取不到也要在（值是 None），不许省键"


# ══════════════════════════════════════════════════════════════════════════
# 八、切片/复制必须**保住类型与 meta**（阶段 1.5）
# ══════════════════════════════════════════════════════════════════════════
# 缺陷形态：`Bars` 是 `list` 子类，`list.__getitem__(slice)` 返回的是**普通 list**
# —— 于是 `bars[-60:]` 一取子集，`status`/`as_of`/`source` 全部悄悄蒸发。
# 而"这批数是不是定稿的"恰恰是切完片之后才要判的事。
def test_slice_keeps_subclass_and_meta():
    """`bars[10:]` 仍是 `Bars`，并且带着**同一份** meta。"""
    meta = {"status": "provisional", "as_of": "2026-10-27 14:44:00",
            "source": {"host": None, "cmd": "HQServ.PBFXT", "ctl": 14,
                       "clock": "hybrid", "cached": False}}
    bars = Bars(list(range(20)), meta=meta)

    tail = bars[10:]
    assert isinstance(tail, Bars), type(tail)
    assert tail == list(range(10, 20))
    assert isinstance(tail, list)                # 老代码 `isinstance(x, list)` 仍然成立
    assert tail.meta == meta, "切片不许丢 meta"
    assert tail.status == "provisional"
    assert tail.as_of == meta["as_of"]
    assert tail.source == meta["source"]


def test_slice_meta_is_a_copy_not_an_alias():
    """切片带的是 meta 的**副本**：改子集不许污染原容器。"""
    bars = Bars([1, 2, 3], meta={"status": "final"})
    tail = bars[1:]
    tail.meta["status"] = "provisional"
    assert bars.meta["status"] == "final", "切片与原容器不许共享同一份 meta dict"
    assert tail.meta["status"] == "provisional"


def test_all_slice_shapes_keep_type_and_meta():
    """步长、反向、越界、空切片 —— 各种切法都得保住类型与 meta。"""
    meta = {"status": "final"}
    for cls in (Series, Bars, Quotes, Ticks, Minutes):
        obj = cls([1, 2, 3, 4], meta=meta)
        for key in (slice(None), slice(1, 3), slice(None, None, 2),
                    slice(None, None, -1), slice(10, 20), slice(0, 0)):
            got = obj[key]
            assert isinstance(got, cls), "%s 的 %r 切片退化成了 %s" % (
                cls.__name__, key, type(got).__name__)
            assert got.meta == meta, "%s 的 %r 切片丢了 meta" % (cls.__name__, key)


def test_indexing_still_returns_the_element():
    """单下标必须照旧返回**元素本身**（不能返回只含一个元素的容器）。"""
    bars = Bars([10, 20, 30], meta={"status": "final"})
    assert bars[0] == 10
    assert not isinstance(bars[0], Series)
    assert bars[-1] == 30


def test_copy_keeps_type_and_meta():
    """`copy()` 也保住类型与 meta（`list.copy()` 会退回普通 `list`）。"""
    meta = {"as_of": "2026-10-27"}
    bars = Bars([1, 2], meta=meta)
    dup = bars.copy()
    assert isinstance(dup, Bars)
    assert dup == bars and dup.meta == meta
    dup.meta["as_of"] = "x"
    assert bars.meta["as_of"] == "2026-10-27"
