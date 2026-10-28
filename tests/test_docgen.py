# -*- coding: utf-8 -*-
"""`tdxdata/_docgen.py` 的**生成块守则**（ENG-012）。

三条硬要求，缺一条这个模块就白写了
----------------------------
1. **稳定**：同一个生成函数调用两次必须返回**逐字相同**的字符串。
   生成器里只要混进 `set()` 的迭代顺序、`time.time()`、环境相关的绝对路径，
   README 就会每次重跑都漂一格 —— 那种"漂"最后一定会被当成噪声忽略掉。
2. **行尾无 `\\r`**：生成器吐 `\\n`，README 是 CRLF；比对前统一行尾（见
   `tests/test_readme_drift.py`）。如果生成器自己带 `\\r`，统一之后会多出空行，
   于是"表里多了一行"这种错误会伪装成"行尾问题"。
3. **表格每行列数一致**：一行多个竖线就整表错列，而 markdown 渲染器**不会报错**，
   它只是把表默默渲染歪 —— 所以必须由测试数。

外加一条**覆盖率**断言：README 里「标记块内行数 / 总行数」必须 ≥ 40%。
它不是虚荣指标：它衡量"README 里有多少行是**没人能偷偷手改**的"。
9% 的时候，剩下 91% 全是手抄；手抄的市场域表把 `0` 标成「科创板」，带着一整轮
调研期的结论一起错。要删块就请连这条断言一起谈，不要偷偷删。
"""
from __future__ import annotations

import os
import re

import pytest

from tdxdata import _docgen

README = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "README.md")

#: 覆盖率门槛：块内行数（含 BEGIN/END 标记行）/ README 总行数。
#: **不许为了让它绿而把门槛往下调** —— 绿不了说明有表被删了，去补表。
MIN_BLOCK_COVERAGE = 0.40

#: 块 id → `tdxdata/_docgen.py` 里的生成函数（与 `tests/test_readme_drift.py`
#: 的 `BLOCKS` 是同一批函数；那边多出来的 4 个是 ENG-012 之前就有的块）。
GENERATORS = {
    "api_types": _docgen.api_types_table,
    "enum_adjust": _docgen.adjust_enum_table,
    "enum_market": _docgen.market_enum_table,
    "enum_period": _docgen.period_enum_table,
    "enum_status": _docgen.status_enum_table,
    "error_types": _docgen.error_types_table,
    "price_limit_boards": _docgen.price_limit_table,
    "client_params": _docgen.client_params_table,
    "on_missing_modes": _docgen.on_missing_table,
    "model_fields": _docgen.model_fields_table,
    "indicators": _docgen.indicator_table,
    "invariants": _docgen.invariant_table,
    "symbol_prefixes": _docgen.symbol_prefix_table,
    "ambiguous_symbols": _docgen.ambiguous_symbol_table,
    "time_gates": _docgen.timeliness_table,
    "provenance_fields": _docgen.provenance_table,
    "product_layer_api": _docgen.product_layer_table,
    "instrument_stats": _docgen.instrument_stats_table,
    "data_domains": _docgen.domain_stats_table,
    "product_index": _docgen.product_index_table,
}

_BEGIN = re.compile(r"<!--\s*BEGIN:\s*([A-Za-z0-9_]+)")
_END = re.compile(r"<!--\s*END:\s*([A-Za-z0-9_]+)\s*-->")


# ══════════════════════════════════════════════════════════════════════════
# 一、生成函数本身的三条守则
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("block_id", sorted(GENERATORS))
def test_every_generator_is_stable(block_id):
    """两次调用必须**逐字相同**（否则 README 每次重跑都漂）。"""
    first = GENERATORS[block_id]()
    second = GENERATORS[block_id]()
    assert isinstance(first, str), "生成器 %r 没返回字符串" % block_id
    assert first == second, (
        "生成器 %r 两次调用结果不同 —— 里面混进了 set/dict 迭代顺序、时间戳"
        "或环境相关的路径" % block_id)
    assert first.strip(), "生成器 %r 吐了空字符串" % block_id


@pytest.mark.parametrize("block_id", sorted(GENERATORS))
def test_generator_output_has_no_cr(block_id):
    """输出行尾只许 `\\n`：带 `\\r` 会让 README 的行尾统一之后多出空行。"""
    text = GENERATORS[block_id]()
    assert "\r" not in text, (
        "生成器 %r 的输出里有 `\\r`（应该只有 `\\n`，README 的 CRLF 由写入端负责）"
        % block_id)


@pytest.mark.parametrize("block_id", sorted(GENERATORS))
def test_generator_table_rows_have_same_column_count(block_id):
    """每张表**每行的列数一致**（markdown 渲染器不会帮你报这个错，只会渲染歪）。"""
    lines = [ln for ln in GENERATORS[block_id]().split("\n") if ln.strip()]
    assert lines[0].lstrip().startswith("|"), (
        "生成器 %r 的第一行不是表格（%r）" % (block_id, lines[0][:40]))
    wanted = _columns(lines[0])
    assert wanted >= 2, "生成器 %r 的表头只有 %d 列" % (block_id, wanted)
    bad = [(idx + 1, _columns(ln), ln[:60])
           for idx, ln in enumerate(lines) if _columns(ln) != wanted]
    assert not bad, (
        "生成器 %r 的表格列数不一致（表头 %d 列），这些行错了：%s"
        % (block_id, wanted, bad[:5]))


def _columns(line: str) -> int:
    """数一行的列数：**只数没被反斜杠转义的 `|`**，两侧的竖线不算列。"""
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    count = 0
    escaped = False
    for ch in text:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "|":
            count += 1
    return count + 1


# ══════════════════════════════════════════════════════════════════════════
# 二、README 的受守护行数占比
# ══════════════════════════════════════════════════════════════════════════
def _readme_lines() -> list:
    """README 按 **CRLF** 切成的行（末行有行尾时不留一个假空行）。"""
    with open(README, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    lines = text.split("\r\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return lines


def block_coverage(lines) -> tuple:
    """`(块内行数, 总行数)`：块内行数**含 BEGIN/END 标记行**（口径与 ENG-012 任务书一致）。

    块边界一律用"最近的下一个同名 END"来定，缺配对的块直接抛错（宁可炸也不要
    算出一个看着漂亮的分母）。
    """
    begins, ends = {}, {}
    for idx, line in enumerate(lines):
        m = _BEGIN.search(line)
        if m:
            begins[m.group(1)] = idx
        m = _END.search(line)
        if m:
            ends[m.group(1)] = idx
    assert sorted(begins) == sorted(ends), (
        "标记块不成对：BEGIN %s / END %s" % (sorted(begins), sorted(ends)))
    span = 0
    for name, start in begins.items():
        stop = ends[name]
        assert stop > start, "块 %r 的 END 在 BEGIN 之前" % name
        span += stop - start + 1
    return span, len(lines)


def test_readme_block_coverage_at_least_40_percent():
    """**主力断言**：受守护的行数占比 ≥ 40%（谁删块谁红）。"""
    span, total = block_coverage(_readme_lines())
    ratio = span / total if total else 0.0
    assert ratio >= MIN_BLOCK_COVERAGE, (
        "README 受守护行数占比 %.2f%% < %.0f%%（块内 %d 行 / 总 %d 行）—— "
        "有表被删了，或者新加的手写表没登记进 BLOCKS/_docgen"
        % (100 * ratio, 100 * MIN_BLOCK_COVERAGE, span, total))


def test_deleting_a_big_block_would_break_the_coverage_gate():
    """**自证这条断言会红**：把产品索引块整段删掉，占比必须掉到门槛以下。"""
    lines = _readme_lines()
    begins = [idx for idx, line in enumerate(lines)
              if _BEGIN.search(line) and _BEGIN.search(line).group(1) == "product_index"]
    ends = [idx for idx, line in enumerate(lines)
            if _END.search(line) and _END.search(line).group(1) == "product_index"]
    assert begins and ends, "README 里找不到 product_index 块"
    trimmed = lines[:begins[0]] + lines[ends[0] + 1:]
    span, total = block_coverage(trimmed)
    assert span / total < MIN_BLOCK_COVERAGE, (
        "删掉 product_index 之后占比仍有 %.2f%% —— 说明门槛太松"
        % (100 * span / total))


def test_every_generator_has_its_own_block_in_readme():
    """`_docgen` 里每个生成器都要在 README 里落到自己的块上（少一个 = 白写）。"""
    names = set()
    for line in _readme_lines():
        m = _BEGIN.search(line)
        if m:
            names.add(m.group(1))
    missing = sorted(set(GENERATORS) - names)
    assert not missing, "这些生成器在 README 里没有块：%s" % missing
