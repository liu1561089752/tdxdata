# -*- coding: utf-8 -*-
"""批量上限与补发成批（2026-10-29 修复）—— 用例全部零网络。

为什么这两条要单独钉住
----------------------
1. **默认批量原来是 20**，依据是 2026-10-27 的"抓包只验证到 6 个"。31 份抓包复核后
   这个依据被推翻：945 个 `PBCombHQ` 请求里最大一次带 **447** 个码，回包同样 447 行、
   完整无截断。默认值改成 100、上限设 447 —— 上限必须有 `ParamError` 挡着，
   否则"再大一点试试"这种事早晚会有人做，而截断是**静默**的（少几只不报错）。
2. **补发原来是逐个单发**：缺 N 个码就是 N 次往返（每次占一个 2.2 s 闸门）。
   改成成批后请求数从 `1+N` 降到 `1+ceil(N/batch)`。

唯一不许退让的性质：**歧义码（同一 code 在多市场）仍必须单发** ——
批量回包里只有 code 没有市场，成批会重新引入"两只标的重叠"的老坑（DEF-014）。
"""
from __future__ import annotations

import inspect

import pytest

import tdxdata
from tdxdata import errors
from tdxdata._engine import hq_extra

_CLS = getattr(tdxdata, "Client")


def _bare_client():
    """不建连的裸 Client（与 test_on_missing.py 同一套做法）。"""
    from tdxdata._engine import datacenter as dc

    c = _CLS.__new__(_CLS)
    c._dc_mod = dc
    c._probe_allowed = False
    c._clock_probe = False
    c._clock = None
    c._last_call = {}
    c._in_cal = False
    c._strict_symbols = False
    return c


_COLS = ["Code", "Setcode", "NAME", "CLOSE", "Now", "HqDate", "HqTime"]


def _row(code, mkt=1):
    return [code, str(mkt), "名" + code, "10.0", "11.0", "20261027", "15:30:03"]


def _install(monkeypatch, *, first=(), retry=()):
    """假通道：记录**每次请求带的码数**，按阶段决定回哪些码。"""
    first = set(first)
    retry = set(retry)
    chunks: list[int] = []          # 每次请求的码数
    asked: set = set()

    def fake(items):
        chunks.append(len(items))
        rows = []
        for mkt, code in items:
            first_time = code not in asked
            asked.add(code)
            allow = first if first_time else (first | retry)
            if code in allow:
                rows.append(_row(code, mkt))
        return _COLS, rows

    monkeypatch.setattr(hq_extra, "_one_batch", fake)
    return chunks


# ══════════════════════════════════════════════════════════════════════════
# 一、默认值与上限
# ══════════════════════════════════════════════════════════════════════════
def test_max_batch_is_the_measured_ceiling():
    assert hq_extra.MAX_BATCH == 447, "上限来自抓包实测（最大一次 447 个码），改它要有新证据"
    assert "MAX_BATCH" in hq_extra.__all__, "常量要导出，否则文档引用不到"


def test_default_batch_is_100_on_both_layers():
    assert inspect.signature(hq_extra.get_quotes).parameters["batch"].default == 100
    assert inspect.signature(_CLS.get_quotes).parameters["batch"].default == 100


@pytest.mark.parametrize("bad", [448, 1000, 1_000_000])
def test_batch_over_ceiling_raises_before_any_request(monkeypatch, bad):
    chunks = _install(monkeypatch, first=["600519"])
    with pytest.raises(errors.ParamError) as ei:
        hq_extra.get_quotes(["600519"], batch=bad)
    msg = str(ei.value)
    assert "447" in msg and "实测" in msg
    assert chunks == [], "超上限必须在发请求之前就炸（别白等一个 2.2 s 闸门）"


@pytest.mark.parametrize("bad", [0, -1])
def test_batch_below_one_raises(monkeypatch, bad):
    chunks = _install(monkeypatch, first=["600519"])
    with pytest.raises(errors.ParamError):
        hq_extra.get_quotes(["600519"], batch=bad)
    assert chunks == []


def test_batch_not_a_number_raises(monkeypatch):
    _install(monkeypatch, first=["600519"])
    with pytest.raises(errors.ParamError):
        hq_extra.get_quotes(["600519"], batch="很多")


# ══════════════════════════════════════════════════════════════════════════
# 二、分块规模按 batch 走
# ══════════════════════════════════════════════════════════════════════════
def test_chunks_follow_batch_size(monkeypatch):
    codes = ["%06d" % (600000 + i) for i in range(25)]
    chunks = _install(monkeypatch, first=codes)
    cols, rows = hq_extra.get_quotes(codes, batch=10)
    assert chunks == [10, 10, 5], "25 个码按 10 一批 = 10/10/5 三次请求"
    assert len(rows) == 25


def test_447_codes_go_in_one_request(monkeypatch):
    codes = ["%06d" % (600000 + i) for i in range(447)]
    chunks = _install(monkeypatch, first=codes)
    hq_extra.get_quotes(codes, batch=hq_extra.MAX_BATCH)
    assert chunks == [447], "447 是实测能一次带走的量，不该被拆"


# ══════════════════════════════════════════════════════════════════════════
# 三、补发成批：请求数 1+N → 1+ceil(N/batch)
# ══════════════════════════════════════════════════════════════════════════
def test_missing_retry_is_batched_not_per_code(monkeypatch):
    """10 个码首轮全不回、补发才回 —— 老实现是 1 + 10 = 11 次请求。"""
    codes = ["%06d" % (600000 + i) for i in range(10)]
    chunks = _install(monkeypatch, first=[], retry=codes)
    cols, rows = hq_extra.get_quotes(codes, batch=100, retry_missing=True)
    assert len(rows) == 10
    assert chunks == [10, 10], "首轮 1 次 + 补发 1 次；逐码单发会是 11 次"
    assert len(chunks) == 2


def test_missing_retry_respects_batch_boundary(monkeypatch):
    """补发量大于 batch 时仍要分批，且每批不超上限。"""
    codes = ["%06d" % (600000 + i) for i in range(25)]
    chunks = _install(monkeypatch, first=[], retry=codes)
    hq_extra.get_quotes(codes, batch=10)
    assert chunks == [10, 10, 5, 10, 10, 5], "首轮 3 批 + 补发 3 批"
    assert max(chunks) <= 10


def test_retry_missing_false_does_not_retry(monkeypatch):
    codes = ["600519", "600036"]
    chunks = _install(monkeypatch, first=["600519"], retry=codes)
    with pytest.raises(errors.EmptyResult):
        hq_extra.get_quotes(codes, batch=100, retry_missing=False)
    assert chunks == [2], "关掉补发就只有首轮那一次"


def test_plain_missing_still_raises_with_name(monkeypatch):
    """补发成批后，缺失清单与异常文案一条不改（默认路径语义不变）。"""
    _install(monkeypatch, first=["600519"], retry=["600519"])
    with pytest.raises(errors.EmptyResult) as ei:
        hq_extra.get_quotes(["600519", "881999"], batch=100)
    assert "881999" in str(ei.value)


# ══════════════════════════════════════════════════════════════════════════
# 四、歧义码仍单发（DEF-014 不许被批量化复活）
# ══════════════════════════════════════════════════════════════════════════
def test_ambiguous_codes_are_still_sent_one_by_one(monkeypatch):
    """`sh000001` 与 `000001` 是两个标的：补发阶段也必须各发各的。"""
    chunks = _install(monkeypatch, first=["600519"])

    def fake(items):
        chunks.append(len(items))
        return _COLS, [_row(code, mkt) for mkt, code in items]

    monkeypatch.setattr(hq_extra, "_one_batch", fake)
    chunks.clear()
    cols, rows = hq_extra.get_quotes(["sh000001", "000001"], batch=100)
    assert chunks == [1, 1], (
        "歧义码不进批量：两个码各发一次（否则回包里分不清是沪还是深）—— 收到 %r" % (chunks,))
    assert [r[0] for r in rows] == ["000001", "000001"]
    assert [r[1] for r in rows] == ["1", "0"], "两只不同标的各归各的市场"
