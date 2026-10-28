# -*- coding: utf-8 -*-
"""空列处理的**两条分界**（阶段 4）—— 本阶段最容易做错的地方。

"空列"有两种，长得一模一样但含义相反
------------------------------------
| 类型 | 例子 | 该怎么办 |
|---|---|---|
| **结构性死列** | 某条命令从来不回这一列（值永远是 None） | **删掉**并记账 `meta["dropped_empty_cols"]` |
| **数据不全** | ztqd 涨停清单的 `N029`/`N025` 盘中全空（当日样本还没长全） | **保留**并标记 `meta["all_null_cols"]` + `all_null_note` |

第一种留着就是"假列"：调用方看到 `cols` 里有 `price` 就以为取到价格了，
用到下游才发现整列 `None`。第二种删掉更糟：等于把"数据不全"这个**信号**藏起来，
用户会以为"今天这些字段本来就没有"。

所以库里有**两个**函数，名字不同、行为相反，本文件守的就是这条分界。
"""
from __future__ import annotations

import pytest

from tdxdata.models import Table
from tdxdata.sections import boards, flow


def _table(cols, rows, **extra):
    return Table(cols=list(cols), rows=[dict(r) for r in rows],
                 cmd="CWServ.test").inherit_provenance(**extra)


# ══════════════════════════════════════════════════════════════════════════
# 一、`_drop_empty_cols`：丢 + 记账 + 行里也要删干净
# ══════════════════════════════════════════════════════════════════════════
def test_drops_only_fully_empty_columns():
    t = _table(["a", "b", "c"], [
        {"a": 1, "b": None, "c": 3},
        {"a": 2, "b": None, "c": None},     # b 全空 → 丢；c 有一个值 → 留
    ])
    out = boards._drop_empty_cols(t)
    assert out.cols == ["a", "c"]
    assert out.provenance.extra["dropped_empty_cols"] == ["b"]
    assert any("100% 为 None" in n for n in out.provenance.notes)


def test_dropped_column_is_gone_from_rows_too():
    """只删表头不删行 = 还能 `row["b"]` 取到一个已"不存在"的列（半删状态）。"""
    t = _table(["a", "b"], [{"a": 1, "b": None}, {"a": 2, "b": None}])
    out = boards._drop_empty_cols(t)
    assert out.cols == ["a"]
    for row in out.rows:
        assert "b" not in row
        with pytest.raises(KeyError):
            row["b"]


def test_no_key_when_nothing_dropped():
    """没丢东西就**别写** meta（否则每个样本的 meta 键集都要白抖一下）。"""
    t = _table(["a", "b"], [{"a": 1, "b": 2}, {"a": 3, "b": 4}])
    out = boards._drop_empty_cols(t)
    assert out.cols == ["a", "b"]
    assert (out.provenance is None
            or "dropped_empty_cols" not in out.provenance.extra)


def test_one_single_value_is_enough_to_keep_the_column():
    t = _table(["a"], [{"a": None}, {"a": None}, {"a": 0}])   # 0 不是空
    out = boards._drop_empty_cols(t)
    assert out.cols == ["a"]
    assert (out.provenance is None
            or "dropped_empty_cols" not in out.provenance.extra)


def test_empty_table_is_left_alone():
    t = _table(["a", "b"], [])
    out = boards._drop_empty_cols(t)
    assert out.cols == ["a", "b"], "没有行就没有『是否全空』可言，不许乱删"


def test_protect_keeps_contract_columns():
    """契约列即使整列为空也**不删**（填 None 而不是让列缺席 —— 本库既有规矩）。"""
    t = _table(["contract", "extra"], [{"contract": None, "extra": None},
                                       {"contract": None, "extra": None}])
    out = boards._drop_empty_cols(t, protect=("contract",))
    assert out.cols == ["contract"], "契约列留着，非契约列删掉"
    assert out.provenance.extra["dropped_empty_cols"] == ["extra"]


# ══════════════════════════════════════════════════════════════════════════
# 二、`_flag_all_null_cols`：**只标不删**（ztqd 那类）
# ══════════════════════════════════════════════════════════════════════════
def test_flag_never_drops_columns():
    t = _table(["N025", "N029", "N002"], [
        {"N025": None, "N029": None, "N002": "00102"},
        {"N025": None, "N029": None, "N002": "00102"},
    ])
    out = boards._flag_all_null_cols(t, product="ztqd")
    assert out.cols == ["N025", "N029", "N002"], "**一列都不许删**"
    assert out.provenance.extra["all_null_cols"] == ["N025", "N029"]
    assert "当日样本不完整" in "；".join(out.provenance.notes)
    assert "dropped_empty_cols" not in out.provenance.extra, "这条路径不该碰 dropped_*"


def test_flag_silent_when_no_all_null_column():
    t = _table(["a"], [{"a": 1}])
    out = boards._flag_all_null_cols(t, product="lrmx")
    assert out.provenance is None
    assert not any("all_null" in n for n in (out.provenance.notes
                                              if out.provenance else ()))


def test_the_two_helpers_disagree_on_purpose():
    """同一张表喂给两个函数：一个删、一个留 —— 这就是分界本身。"""
    t1 = _table(["dead"], [{"dead": None}])
    t2 = _table(["dead"], [{"dead": None}])
    assert boards._drop_empty_cols(t1).cols == []
    assert boards._flag_all_null_cols(t2).cols == ["dead"]


# ══════════════════════════════════════════════════════════════════════════
# 三、作用域：get_boards / market_stat 用「删」，数据中心产品用「标」
# ══════════════════════════════════════════════════════════════════════════
_COLS = ["N001", "N002", "N003"]


class _FakeFlowClient:
    def __init__(self, tables):
        self.tables = dict(tables)
        self.calls: list = []

    def call(self, cmd, ctl, params=None, **_kw):
        self.calls.append((cmd, ctl, params))
        cols, rows = self.tables[cmd]
        return Table(cols=list(cols), rows=[dict(r) for r in rows], cmd=cmd)


def test_market_stat_drops_dead_column():
    """`updown` 里若有一列整列空 → 删掉并记账。"""
    fc = _FakeFlowClient({
        "CWServ.sys_fn_jyr_v3": (["N001"], [{"N001": "2026-10-27"}]),
        "CWServ.ph_datacenter_zdt_zdfx": (
            ["N001", "N002", "N003"],
            [{"N001": "涨停", "N002": "41", "N003": ""},
             {"N001": "跌停", "N002": "7", "N003": ""}],
        ),
    })
    t = flow.get_market_stat(fc, "updown")
    dropped = t.provenance.extra.get("dropped_empty_cols") or []
    assert dropped, "N003 整列为空，应当被删并记账"
    for c in dropped:
        assert c not in t.cols


def test_core_products_keep_columns_via_client_get(monkeypatch):
    """`client.get()` 走的是**标记**路径：列一个都不许少（ztqd 那条红线）。"""
    import tdxdata

    cls = getattr(tdxdata, "Client")
    c = cls.__new__(cls)
    c._products = {"ztqd": {"cmd": "CWServ.ph_tdxdatacenter_ztqd", "ctl": 0x1E,
                            "params": [], "domain": "行情"}}
    c._last_call = {}
    c._in_cal = False
    c._probe_allowed = False
    c._clock_probe = False
    c._clock = None
    c._describe_product = lambda ds: ds           # 时效判定与本用例无关

    fake = Table(cols=["N001", "N025", "N029"],
                 rows=[{"N001": "00102", "N025": None, "N029": None},
                       {"N001": "00102", "N025": None, "N029": None}])
    monkeypatch.setattr(cls, "call", lambda self, *a, **k: fake)

    ds = c.get("ztqd")
    assert ds.table.cols == ["N001", "N025", "N029"], "列必须原样保留"
    assert ds.table.provenance.extra["all_null_cols"] == ["N025", "N029"]
    assert any("样本不完整" in n or "整列为 None" in n
               for n in ds.table.provenance.notes)
    assert "dropped_empty_cols" not in ds.table.provenance.extra


def test_boards_keeps_its_contract_columns(monkeypatch):
    """`get_boards` 的契约列（含 `change_pct`/`now`/`close`）不许被空列守卫删掉。"""
    from tdxdata.sections import boards as b

    class _FC:
        def call(self, cmd, ctl, params=None, **k):
            cols = ["N001", "N002", "N003", "N004", "N005", "N006",
                    "N007", "N008", "N009"]
            rows = [{"N001": "X1001", "N002": "881002", "N003": "煤炭开采",
                     "N004": "1", "N005": "X1001", "N006": "", "N007": "",
                     "N008": "X1001", "N009": "煤炭开采"}]
            return Table(cols=cols, rows=rows, cmd=cmd)

        def board_page(self, offset=0, page_size=44, market=10001):
            return ["Code", "Setcode", "Name", "CLOSE", "NOW", "AMOUNT"], []

    t = b.get_boards(_FC(), "industry")
    for c in ("board_code", "board_name", "board_type", "change_pct", "now", "close"):
        assert c in t.cols, "契约列 %s 不许因为整列空就消失" % c
    # 非契约列（N006/N007 全空 → level1_*）才是守卫该管的
    assert t.provenance.extra.get("dropped_empty_cols") == ["level1_code", "level1_name"]
