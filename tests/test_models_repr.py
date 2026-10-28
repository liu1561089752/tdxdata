# -*- coding: utf-8 -*-
"""`Row` / `Table` / `Dataset` 的 `repr` 截断回归测试（`DEF-022`）—— 纯离线。

被守的红线
----------
1. `repr()` 长度**与数据规模无关**：5000 行 × 8 列的表原来 `repr()` 是
   **单行 42 万字符**，现在必须 < 2000 字符，且带 `…` 与剩余行数提示；
2. `repr()` **不许抛异常**：`None`、10 万字符中文、空容器、自引用都要打得出来；
3. `repr()` 是**只读旁路**：打完 `shape` / `cols` / `rows` / `to_dicts()`
   的结果必须与打之前一模一样（顺手把行截断 = 静默错数据）。

跑法：`python -m pytest tests/test_models_repr.py -q`
     或 `python tests/test_models_repr.py`
"""
from __future__ import annotations

import os
import sys

import pytest

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tdxdata.models import Dataset, Row, Table                 # noqa: E402

COLS = ["N%03d" % i for i in range(1, 9)]                      # 8 列，形如 7615 回包
HUGE_CN = "中" * 100000                                         # 10 万字符中文


def _big_table(rows: int = 5000) -> Table:
    data = [{c: i * 100 + j for j, c in enumerate(COLS)} for i in range(rows)]
    return Table(cols=COLS, rows=data, cmd="HQServ.PBFXT", domain="hq")


# ── 1. Table：5000 行也必须常数级长度 ────────────────────────────────────
def test_5000_row_table_repr_is_bounded():
    t = _big_table(5000)
    before_dicts = t.to_dicts()                                # 快照（含全部 5000 行）

    text = repr(t)

    assert len(text) < 2000, "Table.__repr__ 超长：%d 字符" % len(text)
    assert "Table(rows=5000, cols=8" in text, text[:200]
    assert "…" in text, "必须有截断标记"
    assert "more rows" in text and "4997" in text, "必须给出剩余行数提示：%s" % text[-80:]
    assert "N001" in text, "至少要能看到列名"

    # 契约不变：shape / to_dicts / cols / rows 一个都不许被 repr 影响
    assert t.shape == (5000, 8)
    assert len(t.to_dicts()) == 5000
    assert t.to_dicts() == before_dicts
    assert t.cols == COLS
    assert len(t.rows) == 5000
    assert all(isinstance(r, dict) for r in t.rows[:3])


def test_table_repr_shows_only_first_three_rows():
    t = _big_table(10)
    text = repr(t)
    assert "[0]" in text and "[1]" in text and "[2]" in text
    assert "[3]" not in text, "第 4 行不该出现在摘要里：%s" % text
    assert "…(+7 more rows)" in text


def test_empty_table_repr_is_short_and_safe():
    t = Table(cols=[], rows=[])
    text = repr(t)
    assert text.startswith("Table(rows=0, cols=0)"), text
    assert len(text) < 200
    assert t.shape == (0, 0)
    assert t.to_dicts() == []


# ── 2. Row：单行、≤ 6 个键值、≤ 400 字符 ────────────────────────────────
def test_empty_row_repr():
    row = Row()
    assert repr(row) == "Row{}"
    assert row.to_dict() == {}


def test_row_repr_caps_fields_and_length():
    row = Row(["c%d" % i for i in range(30)], list(range(30)))
    text = repr(row)
    assert len(text) <= 400, "Row.__repr__ 超长：%d 字符" % len(text)
    assert text.startswith("Row{")
    assert "…(+24 fields)" in text, text
    # 只展示前 6 个键值：第 7 个键不该出现
    assert "c5" in text and "c6" not in text, text


def test_row_repr_survives_none_and_huge_chinese():
    row = Row(["a", "b", "c"], [None, HUGE_CN, b"\x00" * 100000])
    text = repr(row)
    assert len(text) <= 400, "Row.__repr__ 超长：%d 字符" % len(text)
    assert "None" in text
    assert "另有 99940 字符" in text, "超长中文必须被截断并注明剩余量：%s" % text


def test_row_repr_survives_unreprable_value():
    class Weird:
        def __repr__(self):                                     # noqa: D401
            raise RuntimeError("boom")

    row = Row(["w"], [Weird()])
    text = repr(row)                                            # 不许抛
    assert "repr 失败" in text and "RuntimeError" in text, text


def test_row_repr_self_reference_does_not_recurse():
    row = Row(["x"], [1])
    row["self"] = row                                           # 自引用（原来 RecursionError）
    text = repr(row)
    assert "自引用" in text, text
    assert len(text) <= 400


# ── 3. Dataset：容器个数 + 名字 + 内嵌表摘要 ────────────────────────────
def test_dataset_repr_is_bounded_and_names_containers():
    ds = Dataset(name="pb_fxt", table=_big_table(5000), params={"code": "600519"})
    before = ds.to_dicts()

    text = repr(ds)

    assert len(text) < 2000, "Dataset.__repr__ 超长：%d 字符" % len(text)
    assert "Dataset(name='pb_fxt'" in text, text[:120]
    assert "容器 1 个=table" in text, "必须指出包含几个容器、叫什么：%s" % text[:160]
    assert "rows=5000" in text and "N001" in text
    assert "…" in text
    # 内嵌表的契约不变
    assert len(ds) == 5000
    assert ds.cols == COLS
    assert ds.to_dicts() == before
    assert len(ds.table.rows) == 5000


def test_dataset_repr_survives_none_params_and_empty_table():
    ds = Dataset(name="empty", table=Table(cols=[], rows=[]), params=None)
    text = repr(ds)
    assert text.startswith("Dataset(name='empty'")
    assert "None" in text
    assert len(text) < 2000


# ── 4. 三种容器的 repr 互不影响、且只读 ─────────────────────────────────
@pytest.mark.parametrize("factory", [
    lambda: Table(cols=[], rows=[]),
    lambda: Table(cols=COLS, rows=[{"N001": None}, {"N001": HUGE_CN}]),
    lambda: Dataset(name="d", table=Table(cols=COLS, rows=[{"N001": 1}])),
])
def test_repr_is_read_only(factory):
    obj = factory()
    snapshot = (obj.to_dicts(), list(obj.cols), len(obj.rows)
                if hasattr(obj, "rows") else len(obj))
    first = repr(obj)
    second = repr(obj)
    assert first == second, "两次 repr 必须一致（不许有隐藏状态）"
    assert (obj.to_dicts(), list(obj.cols), len(obj.rows)
            if hasattr(obj, "rows") else len(obj)) == snapshot


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
