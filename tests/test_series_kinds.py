# -*- coding: utf-8 -*-
r"""非价格序列（阶段 2 的破坏性变更）：登记表 + `get_bars` 拒绝 + `get_breadth`。

这一步故意**让以前能跑的脚本报错**
--------------------------------
`get_bars("880005")` 以前会返回"价格"形状的 `Bar`：`close=1317.00`。
可 880005 是**涨跌家数**，`close` 是"上涨家数 1317 家"——把它当点数用，
任何基于价格的结论都会错，而且错得**毫无提示**（数值量级看着还挺像指数）。
库没法从数据本身分辨（同一条 `PBFXT`、同一个响应结构），所以只能按代码登记，
命中就**拒绝**。用"报错"换"正确"。

本文件守 5 件事
--------------
1. 登记表内容**逐项**对得上（代码 / 名称 / 字段映射 / 顺序），且**恰好 4 个**；
2. 4 个代码走 `get_bars` 抛 `NotSupportedError`，文案含名称 + `get_breadth` + 代码；
3. **零网络**：抛错发生在发请求**之前**（打桩计数为 0）——
   否则"拒绝"变成了"先花一次配额再拒绝"；
4. **不误伤**：`880903` / `881007` / `880001` / `600519` / `999999` 照常能取
   （880903 等是**真价格**的板块指数，按前缀一刀切就是另一种错误）；
5. README 那张表与 `series_kinds.as_markdown_table()` **同源**（漂移即红）。

红线（`_dev/check.py` 第 7 条离线扫 `tests/test_*.py`）：不许出现
`Client(` / `Session(` 字面量。本文件用 `getattr(tdxdata, "Client")` + `__new__`，
`hq_pb.get_bars` 一律打桩，**一个 socket 都不建**。
"""
from __future__ import annotations

import os

from datetime import datetime

import pytest

import tdxdata
from tdxdata import errors
from tdxdata import series_kinds as sk
from tdxdata import timeliness as tl
from tdxdata.models import Table

# ── 派单里给的登记内容（**逐项**对照用，故意手抄一遍防止实现偷偷改）──────────
EXPECTED = {
    "880005": (
        "涨跌家数",
        [("open", "down_count"), ("high", "total"),
         ("low", "flat_count"), ("close", "up_count")],
    ),
    "880006": (
        "停板家数",
        [("close", "limit_up_count"), ("open", "limit_down_count")],
    ),
    "880866": ("近期新低", [("close", "value")]),
    "880641": ("历史新低", [("close", "value")]),
}

READ_ME = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "README.md")

ROW1 = {"Data": "20261027", "Open": "1000", "High": "5000", "Low": "100",
        "Close": "1317", "Volume": "100", "Amount": "100", "Second": "0"}
ROW2 = {"Data": "20261024", "Open": "900", "High": "5100", "Low": "80",
        "Close": "1200", "Volume": "100", "Amount": "100", "Second": "0"}


# ══════════════════════════════════════════════════════════════════════════
# 小工具
# ══════════════════════════════════════════════════════════════════════════
def _bare_client():
    """不跑 `__init__` 的 Client（不建连、不联网、不读产品表）。"""
    cls = getattr(tdxdata, "Client")
    return cls.__new__(cls)


def _stub_hq(monkeypatch, *, rows=None, calls=None):
    """把 `hq_pb.get_bars` 换成桩，并（可选）记下调用次数。"""
    from tdxdata._engine import hq_pb

    def fake(code, **kwargs):
        if calls is not None:
            calls.append((code, kwargs))
        return (["Data", "Open", "High", "Low", "Close", "Volume", "Amount",
                 "Second"], list(rows if rows is not None else [ROW1]))

    monkeypatch.setattr(hq_pb, "get_bars", fake)
    return fake


# ══════════════════════════════════════════════════════════════════════════
# 一、登记表内容
# ══════════════════════════════════════════════════════════════════════════
def test_registry_has_exactly_the_four_proven_codes():
    """**恰好 4 个**代码 —— 多登记一个都要先有实证（宁缺勿猜）。"""
    assert set(sk.NON_PRICE_SERIES) == set(EXPECTED) == {
        "880005", "880006", "880866", "880641"}


@pytest.mark.parametrize("code", sorted(EXPECTED))
def test_registry_entry_matches_spec_item_by_item(code):
    """逐项核对：名称、字段映射（**含插入顺序**，它就是输出列顺序）、note 非空。"""
    name, fields = EXPECTED[code]
    spec = sk.get_spec(code)
    assert spec is not None, code
    assert isinstance(spec, sk.SeriesSpec)
    assert spec.code == code
    assert spec.name == name
    assert list(spec.fields.items()) == fields, "%s 的字段映射/顺序对不上" % code
    assert spec.note.strip(), "note 不许为空（它是实证说明，是这张表存在的理由）"
    assert sk.is_non_price(code) and sk.is_non_price(int(code))
    assert sk.is_non_price("sh" + code), "带市场前缀也要认出来"


def test_unregistered_codes_are_not_non_price():
    """未登记的代码一律**不猜**（`get_spec` 给 `None`，不是"默认是价格"）。"""
    for code in ("600519", "999999", "880903", "880952", "881007", "880001"):
        assert sk.get_spec(code) is None
        assert not sk.is_non_price(code)


def test_redirect_text_is_generated_not_hardcoded():
    """指路文案按 spec 现场生成：880005 与派单原文**逐字一致**，880866 另生成一句。"""
    assert sk.render_redirect(sk.get_spec("880005")) == (
        "880005 是「涨跌家数」指标，其 OHLC 是四个家数而非价格。\n"
        "价格序列请用 get_bars；广度序列请用 get_breadth(\"880005\")。")
    # 只有 close 一个字段的序列：句子要变形（不许照抄"OHLC 是四个家数"）
    one = sk.render_redirect(sk.get_spec("880866"))
    assert "close" in one and "OHLC" not in one and "指标点位" in one
    assert "get_breadth(\"880866\")" in one
    # 未登记代码 / 传错类型
    with pytest.raises(TypeError):
        sk.render_redirect("880005")


def test_markdown_table_shape_and_content():
    """`as_markdown_table()`：表头固定、`|` 行数 = 登记数、每行含代码与名称。"""
    text = sk.as_markdown_table()
    lines = [ln for ln in text.split("\n") if ln.strip()]
    assert lines[0] == "| 代码 | 名称 | 字段映射（`Bar` 字段 → 标准名） | 说明 |", lines[0]
    assert lines[1] == "|---|---|---|---|"
    body = lines[2:]
    assert len(body) == len(sk.NON_PRICE_SERIES) == 4
    for code in EXPECTED:
        hit = [ln for ln in body if "`%s`" % code in ln]
        assert len(hit) == 1, code
        assert EXPECTED[code][0] in hit[0], "每一行都要有名称"


# ══════════════════════════════════════════════════════════════════════════
# 二、get_bars 拒绝（含"零网络"硬证据）
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("code", sorted(EXPECTED))
def test_get_bars_rejects_non_price_series(code):
    """4 个代码走 `get_bars` → `NotSupportedError`，文案含名称 + `get_breadth` + 代码。"""
    c = _bare_client()
    with pytest.raises(errors.NotSupportedError) as ei:
        c.get_bars(code)
    msg = str(ei.value)
    assert EXPECTED[code][0] in msg, "文案要点明它到底是什么指标"
    assert "get_breadth" in msg and code in msg
    assert "get_breadth(\"%s\")" % code in msg
    # 继承链没动：仍是 TdxError（调用方现有的 except 不会漏）
    assert isinstance(ei.value, errors.TdxError)


def test_get_bars_rejects_before_any_request(monkeypatch):
    """**零网络**：抛错发生在发请求之前（`hq_pb.get_bars` 调用次数必须是 0）。

    这是"必须在发请求前拦"的硬证据 —— 否则"拒绝"就变成了"先花一次配额再拒绝"，
    对用户的配额是白花的，而且限流闸门还会被无意义地推进一格。
    """
    calls = []
    _stub_hq(monkeypatch, calls=calls)
    c = _bare_client()
    for code in ("880005", "880006", "880866", "880641"):
        with pytest.raises(errors.NotSupportedError):
            c.get_bars(code)
    assert calls == [], "被拒绝的代码一次请求都不许发（实际发了 %d 次）" % len(calls)


def test_get_bars_still_works_for_real_price_series(monkeypatch):
    """**不误伤**：板块指数 / 个股 / 指数照常能取（按前缀一刀切就是新的错误）。"""
    calls = []
    _stub_hq(monkeypatch, calls=calls)
    c = _bare_client()
    for code in ("880903", "881007", "880001", "600519", "999999"):
        bars = c.get_bars(code, count=1)       # 不许抛 NotSupportedError
        assert isinstance(bars, list) and len(bars) == 1
        assert type(bars).__name__ == "Bars"
        assert bars[0].market == tdxdata.parse_symbol(code)[0]
    assert [call[0] for call in calls] == ["880903", "881007", "880001",
                                          "600519", "999999"], "序列必须真的下发取数"
    assert len(calls) == 5


# ══════════════════════════════════════════════════════════════════════════
# 三、get_breadth
# ══════════════════════════════════════════════════════════════════════════
def test_get_breadth_renames_fields_and_keeps_raw(monkeypatch):
    """`get_breadth("880005")`：列名是家数语义，**不再暴露 open/high/low/close**。"""
    _stub_hq(monkeypatch, rows=[ROW1, ROW2])
    c = _bare_client()
    # 钉死"服务器当前时刻"= 2026-10-27 收盘后：
    # 这样 20261027 这根是 FINAL、20261024 是历史根，断言与真实日期无关
    c._clock = tl.FakeClock(datetime(2026, 10, 27, 16, 0, 0))
    t = c.get_breadth("880005", count=2)

    assert isinstance(t, Table)
    assert t.cols == ["date", "series", "series_name",
                      "down_count", "total", "flat_count", "up_count",
                      "provisional"]
    for banned in ("open", "high", "low", "close"):
        assert banned not in t.cols, "`%s` 是价格语义，不许出现在广度表里" % banned
    assert t.cols[-1] == "provisional", \
        "provisional 必须是**最后一列**（以后加数值列也不会把它挤到中间）"
    assert len(t.rows) == 2

    first = t.rows[0]
    assert first["date"] == "20261027"
    assert first["series"] == "880005" and first["series_name"] == "涨跌家数"
    # 映射是**搬家不是换算**：open→down_count、high→total、low→flat_count、close→up_count
    assert first["down_count"] == 1000.0
    assert first["total"] == 5000.0
    assert first["flat_count"] == 100.0
    assert first["up_count"] == 1317.0
    # 原始字段一个不丢
    assert first["raw"]["Close"] == "1317" and first["raw"]["Data"] == "20261027"
    # provisional 逐根带（阶段 1 语义照旧）：16:00 已过 15:35 → 当根也 FINAL
    assert first["provisional"] is False
    assert t.rows[1]["provisional"] is False, "历史根恒 False"
    assert t.meta["status"] == "final"
    assert t.meta["fetch_clock"] == "server"


def test_get_breadth_meta_carries_field_map_and_unavailable(monkeypatch):
    """`meta`：`field_map` 与登记表一致、`unavailable` 指出没定义的字段、阶段 1 的键都在。"""
    _stub_hq(monkeypatch, rows=[ROW1])
    c = _bare_client()
    meta = c.get_breadth("880005", count=1).meta

    assert meta["series"] == "880005" and meta["series_name"] == "涨跌家数"
    assert meta["field_map"] == {"open": "down_count", "high": "total",
                                 "low": "flat_count", "close": "up_count"}
    assert meta["note"] == sk.NON_PRICE_SERIES["880005"].note
    assert meta["unavailable"] == [], "880005 的四个字段都有定义"
    for key in ("as_of", "status", "fetched_at", "source", "elapsed", "fetch_clock"):
        assert key in meta, "阶段 1 的键一个都不许少：%s" % key
    assert meta["as_of"] == "2026-10-27"
    assert meta["fetch_clock"] == "local", "裸 Client 不探针 → 必须是 local 且被标注"
    # `period` 是"数据来自哪个周期"的凭据，保留；`count` 由 len(rows) 可得 → 冗余，删掉
    assert meta["period"] == "day"
    assert "count" not in meta, "`count` 是冗余信息（len(t.rows) 直接可得），多一个键多一处漂移"


def test_get_breadth_880006_marks_undefined_fields_unavailable(monkeypatch):
    """880006 只定义了 open/close → `high`/`low` 必须进 `unavailable`（不当列）。"""
    _stub_hq(monkeypatch, rows=[ROW1])
    c = _bare_client()
    t = c.get_breadth("880006", count=1)
    assert t.cols == ["date", "series", "series_name",
                      "limit_up_count", "limit_down_count", "provisional"]
    assert t.meta["unavailable"] == ["high", "low"]
    assert t.rows[0]["limit_up_count"] == 1317.0     # close → 涨停家数
    assert t.rows[0]["limit_down_count"] == 1000.0   # open → 跌停家数
    for banned in ("open", "high", "low", "close"):
        assert banned not in t.cols


def test_get_breadth_rejects_unregistered_code():
    """未登记代码 → `NotSupportedError`，且**列出已登记的 4 个代码**并指路。"""
    c = _bare_client()
    with pytest.raises(errors.NotSupportedError) as ei:
        c.get_breadth("888888")
    msg = str(ei.value)
    for code in EXPECTED:
        assert code in msg, "文案必须列出已登记代码：%s 缺失" % code
    assert "888888" in msg
    assert "call(" in msg, "要指路原始通道"
    assert "不猜" in msg, "要说明为什么不猜"


def test_get_breadth_raises_empty_result_instead_of_empty_table(monkeypatch):
    """无数据 → `raise errors.EmptyResult`（**绝不**返回 0 行的表冒充"没有数据"）。"""
    _stub_hq(monkeypatch, rows=[])
    c = _bare_client()
    with pytest.raises(errors.EmptyResult):
        c.get_breadth("880005", count=1)


def test_get_breadth_row_keys_cover_cols_plus_raw(monkeypatch):
    """行里 `cols` 一个不缺，另只多一个**非列**辅助键 `raw`（有文档的）。

    `provisional` 已经提升为**正式列**（数据信号，用户要能 `df[df.provisional]`），
    所以它必须出现在 `cols` 里 —— 只剩 `raw` 是"行有、列无"的辅助键。
    """
    _stub_hq(monkeypatch, rows=[ROW1])
    c = _bare_client()
    t = c.get_breadth("880641", count=1)
    assert set(t.cols) <= set(t.rows[0])
    assert set(t.rows[0]) - set(t.cols) == {"raw"}
    assert "provisional" in t.cols, "provisional 是正式列，不是辅助键"
    assert isinstance(t.rows[0]["provisional"], bool)
    assert t.rows[0]["value"] == 1317.0        # close → value（指标点位，不是家数）
    assert t.meta["unavailable"] == ["open", "high", "low"]


# ══════════════════════════════════════════════════════════════════════════
# 四、README 与登记表同源（漂移即红）
# ══════════════════════════════════════════════════════════════════════════
def _readme_block() -> str:
    with open(READ_ME, encoding="utf-8") as fh:
        text = fh.read()
    begin = "<!-- BEGIN: series_kinds"
    end = "<!-- END: series_kinds -->"
    i = text.find(begin)
    j = text.find(end)
    assert i >= 0, "README 里找不到 BEGIN 标记块"
    assert j > i, "README 里的 BEGIN/END 标记块不成对"
    return text[text.index("\n", i) + 1:j].strip("\n")


def test_readme_table_is_generated_from_registry():
    """README 那一节必须是 `as_markdown_table()` 的**逐字**副本（禁手改）。"""
    assert _readme_block() == sk.as_markdown_table(), \
        "README 的表格与登记表漂移了 —— 请用 as_markdown_table() 重新生成，别手改"


def test_readme_block_mentions_rejection_and_the_four_codes():
    """README 这一节还要说清"OHLC 不是价格 / get_bars 会拒绝 / get_breadth 给命名字段"。"""
    with open(READ_ME, encoding="utf-8") as fh:
        text = fh.read()
    i = text.find("## 指标序列")
    assert i >= 0, "README 缺少「指标序列（get_breadth）」一节"
    section = text[i:text.find("<!-- END: series_kinds -->", i)]
    for code in EXPECTED:
        assert code in section, code
    assert "get_bars" in section and "get_breadth" in section
    assert "不是价格" in section or "而非价格" in section
    assert "880903" in section, "要说清不是按 880/881 前缀拒绝（否则读者会以为板块指数也没了）"
