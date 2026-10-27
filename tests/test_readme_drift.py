# -*- coding: utf-8 -*-
"""README 里那几张表**必须逐字等于代码输出**（防漂移）+ 标记块必须成对（防"改一半"）。

为什么要专门有一个测试
------------------
"表格由代码生成"这句话，光把生成函数写出来是不成立的 —— 只有**有人比对**它才成立。
以前 README 的市场域表是手工抄的，于是 `0`/`2` 的语义被标错了整整一轮调研期，
直接导致"北交所在不在 6 里"这种问题被问出来。漂移的文档比没有文档更坏。

所以这里做两件事：
1. **逐块比对内容**：每个 `<!-- BEGIN: xxx -->` 与 `<!-- END: xxx -->` 之间的文字，
   必须与对应生成函数的输出**逐字相同**（行尾统一后比较）；
2. **守标记块的完整性**：每个 BEGIN 都必须有同名 END，END 不许孤立，块不许嵌套/交叠。
   "改一半"的形态是：BEGIN 改了、END 忘了 —— 那会让**下一个块**被整个吞进这一块里，
   所以只比内容还不够，结构也得钉住。

⚠️ 比对前把行尾统一成 `\\n`：README 是 **CRLF**，而生成函数吐的是 `\\n`。
   但**不许**顺手把 README 改成 LF —— 那会把 500 行全变成 diff（本项目踩过一次），
   所以另有 `test_readme_keeps_crlf_line_endings` 专门守行尾。
"""
from __future__ import annotations

import os
import re

import pytest

from tdxdata import capabilities as caps
from tdxdata import series_kinds
from tdxdata.models import Tick

README = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "README.md")

#: 标记块 id → 生成函数。**加一张生成表就往这里加一条**（少加一条 = 那张表没人管）。
BLOCKS = {
    "tick_fields": Tick.as_markdown_table,
    "series_kinds": series_kinds.as_markdown_table,
    "market_domains": caps.market_domains_markdown,
    "capabilities": caps.as_markdown_table,
}

_BEGIN = re.compile(r"<!--\s*BEGIN:\s*([A-Za-z0-9_]+)")
_END = re.compile(r"<!--\s*END:\s*([A-Za-z0-9_]+)\s*-->")


def _readme() -> str:
    """按二进制方式读，**不做行尾转换**（要能看出 CRLF 有没有被破坏）。"""
    with open(README, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _blocks(text: str) -> dict:
    """取出所有标记块的内容（`{name: 内容}`）。结构问题抛 AssertionError。

    这不是"宽松解析"：BEGIN 与 END 的配对关系本身就是契约的一部分，
    所以这里对**残缺/重复/交叠**一律硬失败，而不是"能取到几个算几个"。
    """
    begins = [(m.start(), m.group(1)) for m in _BEGIN.finditer(text)]
    ends = [(m.start(), m.group(1)) for m in _END.finditer(text)]
    assert begins, "README 里一个 `<!-- BEGIN: xxx -->` 都没有？"
    assert len(begins) == len(ends), \
        "BEGIN 有 %d 个、END 有 %d 个：有人把块改了一半" % (len(begins), len(ends))

    out = {}
    for idx, (pos, name) in enumerate(begins):
        assert name not in out, "标记块 %r 出现了两次" % name
        # END 必须紧跟其后、且名字相同 —— 取**最近**的 END 就是为了抓住"漏写 END"
        nxt = ends[idx]
        assert nxt[1] == name, (
            "标记块 %r 的下一个 END 是 %r：说明 %r 的 END 被漏写/写错，"
            "内容会把下一块吞进来" % (name, nxt[1], name))
        assert nxt[0] > pos
        body = text[pos:nxt[0]]
        body = body.split("-->", 1)[1] if "-->" in body else body
        out[name] = body
    return out


# ══════════════════════════════════════════════════════════════════════════
# 一、内容：逐字等于代码输出
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("name", sorted(BLOCKS))
def test_block_content_is_generated_from_code(name):
    """README 的每个标记块 == 对应生成函数的输出（**一个字都不许手改**）。"""
    got = _blocks(_readme()).get(name)
    assert got is not None, "README 里找不到标记块 %r（BEGIN/END 都要有）" % name

    expected = BLOCKS[name]()
    assert expected.strip(), "生成函数 %r 吐了空字符串" % name

    assert got.replace("\r\n", "\n").strip("\n") == expected.strip("\n"), (
        "README 的 %r 块与 `%s()` 的输出不一致 —— "
        "别改 README，去改代码里的注册表，然后重跑生成"
        % (name, getattr(BLOCKS[name], "__qualname__", BLOCKS[name])))


def test_every_registered_generator_actually_has_a_block():
    """反过来也要盯：`BLOCKS` 里登记的每个 id，README 里都必须真有这个块。"""
    present = set(_blocks(_readme()))
    missing = sorted(set(BLOCKS) - present)
    assert not missing, "这些生成器没有对应的 README 标记块：%s" % missing


def test_no_generator_is_declared_but_unused():
    """README 里不许出现 `BLOCKS` 管不到的块（否则它漂了也没人知道）。"""
    present = set(_blocks(_readme()))
    extra = sorted(present - set(BLOCKS))
    assert not extra, (
        "README 有这些标记块，但测试里没有对应的生成函数：%s —— "
        "要么补进 BLOCKS，要么删掉那个块" % extra)


# ══════════════════════════════════════════════════════════════════════════
# 二、结构：BEGIN/END 必须成对、不许交叠、不许空块
# ══════════════════════════════════════════════════════════════════════════
def test_every_begin_has_its_own_end_and_none_is_orphaned():
    """BEGIN 与 END 一一对应；END 不许比 BEGIN 多（孤儿 END 会让块边界静默错位）。"""
    text = _readme()
    begins = [m.group(1) for m in _BEGIN.finditer(text)]
    ends = [m.group(1) for m in _END.finditer(text)]
    assert sorted(begins) == sorted(ends), \
        "BEGIN %s 与 END %s 不是一一对应" % (sorted(begins), sorted(ends))
    assert len(begins) == len(set(begins)), "同一个块名出现了两次 BEGIN"
    assert len(begins) == len(BLOCKS), (
        "README 里的块数（%d）与 BLOCKS（%d）不一致" % (len(begins), len(BLOCKS)))


def test_blocks_do_not_overlap_and_are_not_empty():
    """每块内容非空，且不含别的块的 BEGIN（嵌套＝边界坏了）。"""
    text = _readme()
    blocks = _blocks(text)
    for name, body in blocks.items():
        assert body.strip(), "标记块 %r 是空的" % name
        assert "<!-- BEGIN:" not in body, "标记块 %r 里套了另一个 BEGIN" % name
        assert "<!-- END:" not in body, "标记块 %r 里套了另一个 END" % name


def test_half_edited_marker_is_caught():
    """**自证这条守卫会红**：把某个 END 删掉，解析必须立刻报错（不是静默放过）。"""
    broken = _readme().replace("<!-- END: capabilities -->", "", 1)
    with pytest.raises(AssertionError):
        _blocks(broken)


def test_swapped_end_name_is_caught():
    """**自证**：把 END 的名字写错（BEGIN 对 END 不匹配）也必须报错。"""
    broken = _readme().replace("<!-- END: market_domains -->",
                               "<!-- END: market_domanis -->", 1)
    with pytest.raises(AssertionError):
        _blocks(broken)


# ══════════════════════════════════════════════════════════════════════════
# 三、行尾：README 必须保持 CRLF（防"整文件重写"把 500 行变成 diff）
# ══════════════════════════════════════════════════════════════════════════
def test_readme_keeps_crlf_line_endings():
    """README 是 CRLF 的：`\\r\\n` 数与 `\\n` 相等，且不许有裸 LF。

    这条不是洁癖：一旦混了裸 LF，`git diff` 会把整个文件标成改动，
    复核的人就再也看不出"到底哪几行是真改动"了。
    """
    text = _readme()
    assert text.count("\r\n") > 0, "README 变成了 LF？"
    assert text.count("\r\n") == text.count("\n"), (
        "README 里混了裸 LF：CRLF=%d，LF=%d"
        % (text.count("\r\n"), text.count("\n")))


def test_readme_has_no_bom():
    """UTF-8 **无 BOM**：带 BOM 会让 ```` ```python ```` 之外的解析器读到 \\ufeff。"""
    with open(README, "rb") as fh:
        head = fh.read(3)
    assert head != b"\xef\xbb\xbf", "README 带了 UTF-8 BOM"
