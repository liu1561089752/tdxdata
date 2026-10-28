# -*- coding: utf-8 -*-
"""挖掘轮 M8：`ColName 数 ≠ Content 行长`（错位表）的判据测试。

背景（数据见 `notes/12-错位表与NLP家族.md` 与 `reports/eval3/findings/M8-*.md`）：
语料 2529 条表里 **424 条（16.8%）** 列名数与行长不等，涉及 **70 个命令**，
两种形态都真实存在：

* **尾部多**：`CWServ.ph_bigdata_xtld_zdsl` 12 名 / 8 值 —— 多出来的
  `EndDate/AdjustingFactor/AdjustingConst/fq` 是复权附加列，行里没有值。
* **头部多**：`HQServ.hq_nlp_zjlx` 21 名 / 17 值、`CWServ.ph_agf10_cw_xjllb`
  65 名 / 64 值 —— 多出来的 `ans/date/time/total_num`、`isjr` 不是逐行数据。

`zip()` 只按前缀对齐，所以**头部多**的那种会把每个列名整体左移。本文件守住三件事：

1. `Table.ragged` 能如实报出 `(列名数, 行长)`，且正常表为 `None`；
2. `mapping.apply()` 只在窗口**唯一命中**登记形状时才对齐（前缀 → `server-prefix`，
   后缀 → `server-suffix`），歧义/无命中一律**一列都不映射**（fail-closed）；
3. 对齐后的取值必须来自**位置**而不是错位的列名（曾经真的踩过）。

夹具是 4 份**真实回包字节**（从 `F:\\TDX\\raw\\pcap\\` 按 msgid 配对提出，
`datacenter.unwrap()` 之后的那段，即生产里 `Session.ask()` 返回、`session.parse()`
直接吃的东西），`manifest.json` 记着 cmd/ctl/msgid/pcap/sha256/预期形状。
"""
from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from tdxdata._engine import session as _session
from tdxdata.models import Table
from tdxdata.sections import mapping as _mapping

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "ragged"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
ENTRIES = MANIFEST["entries"]

#: 夹具文件 → 产品键（`products.json` 里的键，`mapped=True` 时用它查映射表）
PRODUCT = {
    "CWServ_ph_bigdata_xtld_zdsl.bin": "zdsl",
    "CWServ_ph_agf10_cw_xjllb.bin": "xjllb",
    "HQServ_hq_nlp_zjlx.bin": "hq_nlp_zjlx",
    "HQServ_hq_nlp_app_zbhc.bin": "hq_nlp_app_zbhc",
}


def _table(fname: str) -> Table:
    """夹具字节 → `Table`（走库自己的 `session.parse`，不另造解析）。"""
    blob = (FIXTURES / fname).read_bytes()
    verdict, _n_rows, _n_cols, cols, rows, _primary = _session.parse(blob)[:6]
    assert verdict == "PASS", "%s 解析失败：%s" % (fname, verdict)
    return Table(cols=list(cols), rows=rows, cmd=ENTRIES[fname]["cmd"])


# ── 一、夹具本身 ────────────────────────────────────────────────────────
def test_fixture_bytes_are_intact():
    """sha256 对得上 —— 夹具被改过就必须立刻发现。"""
    for fname, ent in ENTRIES.items():
        raw = (FIXTURES / fname).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == ent["sha256"], fname
        assert len(raw) == ent["resp_len"], fname


@pytest.mark.parametrize("fname", sorted(ENTRIES))
def test_parsed_shape_matches_manifest(fname):
    """解析出来的列名数 / 行长 / 行数与 manifest 逐项一致。"""
    ent = ENTRIES[fname]
    table = _table(fname)
    assert len(table.cols) == ent["n_cols_names"]
    assert len(table.rows) == ent["n_rows"]
    # `Row` 是 dict 子类，键永远是全部列名（尾部补 None），所以长度恒等于列名数；
    # 真实行宽只有 `Table.ragged` 知道 —— 这正是本轮的发现点。
    assert {len(r) for r in table.rows} == {ent["n_cols_names"]}
    assert table.ragged[1] == ent["row_width"]


@pytest.mark.parametrize("fname", sorted(ENTRIES))
def test_table_reports_ragged_shape(fname):
    """`Table.ragged == (列名数, 真实行长)`，`is_ragged` 为真。"""
    ent = ENTRIES[fname]
    table = _table(fname)
    assert table.ragged == (ent["n_cols_names"], ent["row_width"])
    assert table.is_ragged is True


def test_clean_table_is_not_ragged():
    """列名数与行长相等的表：`ragged is None`、`is_ragged is False`。"""
    table = Table(cols=["a", "b"], rows=[["1", "2"], ["3", "4"]])
    assert table.ragged is None
    assert table.is_ragged is False


def test_dict_rows_never_count_as_ragged():
    """行是 dict（已按名字给值）时没有"行长"概念，不该误报。"""
    table = Table(cols=["a", "b"], rows=[{"a": 1, "b": 2}])
    assert table.is_ragged is False


def test_to_dict_does_not_gain_serialization_keys():
    """`to_dict()` 的键集合不因 M8 而变（序列化兼容），只多一个只读属性。"""
    table = _table("CWServ_ph_bigdata_xtld_zdsl.bin")
    assert set(table.to_dict()) == {"cols", "rows", "cmd", "domain", "provenance"}


# ── 二、对齐判据 ────────────────────────────────────────────────────────
def test_head_extra_names_use_suffix_window():
    """`hq_nlp_zjlx`（头部多 4）：后缀窗口命中登记形状 → `server-suffix`，取值来自位置。

    这份回包的第 0 个值 `688825` 才是代码，而列名 `code` 排在第 5 位 ——
    若按前缀 `zip`，`row["code"]` 会拿到 `7.9345`（涨幅）。
    """
    table = _table("HQServ_hq_nlp_zjlx.bin")
    out = _mapping.apply(table, "hq_nlp_zjlx")
    assert out.provenance.extra["cols_align"] == "server-suffix"
    assert out.cols, "后缀对齐后必须能映射出标准字段"
    row = out.rows[0]
    assert row["code"] == "688825"
    assert row["name"] == "长鑫科技"
    assert row["market"] == 1
    assert row["price"] == pytest.approx(42.85)
    assert row["change_pct"] == pytest.approx(7.9345)


def test_tail_extra_names_use_prefix_window():
    """`zdsl`（尾部多 4）：前缀窗口命中登记形状 → `server-prefix`，尾部列名不映射。"""
    table = _table("CWServ_ph_bigdata_xtld_zdsl.bin")
    out = _mapping.apply(table, "zdsl")
    assert out.provenance.extra["cols_align"] == "server-prefix"
    row = out.rows[0]
    assert row["name"] == "凡拓数创"
    assert row["code"] == "301313"
    assert row["date"] == "20261020"
    assert row["open"] == pytest.approx(43.23)
    assert row["high"] == pytest.approx(46.80)
    # 尾部那 4 个复权列没有值 → 不该冒出任何标准字段
    assert all("fq" not in k for k in out.cols)


def test_suffix_mapping_also_covers_cashflow_statement():
    """`xjllb`（65 名 / 64 值，头部多 `isjr`）：同一套后缀判据对 CWServ 报表也成立。"""
    table = _table("CWServ_ph_agf10_cw_xjllb.bin")
    out = _mapping.apply(table, "xjllb")
    assert out.provenance.extra["cols_align"] == "server-suffix"
    assert "report_period" in out.cols
    # `isjr` 不是逐行数据，报告期必须是行首那个值（而不是被 `isjr` 顶掉）
    assert out.rows[0]["report_period"] == "2026中报"


def test_unknown_shape_maps_nothing():
    """窗口都不命中登记形状 → 一列都不映射（fail-closed），原值全留在 `raw`。"""
    table = _table("HQServ_hq_nlp_app_zbhc.bin")           # 该产品键没登记映射表
    out = _mapping.apply(table, "hq_nlp_app_zbhc")
    assert out.cols == []
    assert out.provenance.extra["cols_align"] == "server"
    assert out.rows[0]["raw"], "原始列必须一列不丢"


def test_ambiguous_windows_refuse_to_align():
    """前缀窗口与后缀窗口**都**命中登记形状时不许猜：一列都不映射。

    造法：把 `zdsl` 登记的 8 列形状**接两遍**（16 名 / 8 值）→ 前缀 = 后缀 = 形状，
    切哪边都"像"，但值到底属于哪一边无法证明 → `cols_align` 退回 `server`，
    `COL_GUARD` 自然挡下，`cols == []`。
    """
    shape = list(_mapping.COL_GUARD["zdsl"])
    table = Table(cols=shape + shape,
                  rows=[["凡拓数创", "301313", "20261020", "43.23",
                         "46.80", "41.89", "46.80", "0"]],
                  cmd="synthetic.ambiguous")
    out = _mapping.apply(table, "zdsl")
    assert out.provenance.extra["cols_align"] == "server"
    assert out.cols == []
    assert out.rows[0]["raw"], "原始列一列不丢"


def test_ragged_table_without_registered_shape_keeps_raw_only():
    """没有登记表的未知产品：`apply()` 不该抛错，也不该编字段名。"""
    table = _table("CWServ_ph_bigdata_xtld_zdsl.bin")
    out = _mapping.apply(table, "__no_such_product__")
    assert out.cols == []
    assert len(out.rows) == len(table.rows)


def test_guard_matches_but_row_is_short_is_rejected():
    """**列名与登记形状逐位相等、但行长更短** → 一列都不映射（`cols-guard-ragged`）。

    实证反例 `CWServ.ph_zt_lrzt_lrmx`（9 名 / 6 值）：行首是日期 `2026-04-16`，
    而 `FIELD_MAP["lrmx"]["N001"]` 登记的是 `margin_balance_yuan`。按旧守卫
    （拿"补齐后的行宽"比）会照旧映射，于是用户拿到
    `margin_balance_yuan="2026-04-16"` —— 值没错、名字全错，且毫无提示。
    """
    shape = list(_mapping.COL_GUARD["zdsl"])          # 8 个列名
    table = Table(cols=shape,
                  rows=[["凡拓数创", "301313", "20261020", "43.23", "46.80", "41.89"]],
                  cmd="CWServ.ph_bigdata_xtld_zdsl")
    assert table.ragged == (8, 6)
    out = _mapping.apply(table, "zdsl")
    assert out.provenance.extra["mapping_skipped"] == "cols-guard-ragged"
    assert out.cols == []
    assert out.rows[0]["raw"]["zqdm"] == "301313"


def test_unguarded_product_keeps_tail_missing_convention():
    """**没登记形状**的产品不走 M8 的新守卫 —— 沿用 M3 结论：缺的值在尾部（前缀映射成立）。

    分辨依据：登记了 `COL_GUARD` = 该产品的"正确形状"已被实测锚定，行长对不上就说明
    这不是那张表；没登记形状 = 没有可比的锚点，只能沿用既定的"尾部缺值"口径
    （`tests/test_mapping_products.py::test_rows_shorter_than_cols_are_missing_values_
    not_misaligned` 对 `tjcj` 有实证）。
    """
    assert "tjcj" not in _mapping.COL_GUARD, "前提变了：tjcj 现在有形状登记了"
    table = Table(cols=["zqjc", "zqdm", "N001", "N002", "N003"],
                  rows=[["某股份", "600303", "2026-06-30"]])
    out = _mapping.apply(table, "tjcj")
    assert out.provenance.extra.get("mapping_skipped") is None
    row = out.rows[0]
    assert row["name"] == "某股份"
    assert row["report_period"] == "2026-06-30"
