# -*- coding: utf-8 -*-
"""DEF-014 / DEF-047：沪深同码不被互相覆盖、varint 负值不再死循环。

两条都是"静默给错数据/静默不返回"的类型，肉眼和类型检查都看不出来：

* `get_quotes(["sh000001", "sz000001"])` 旧实现按**裸 code** 去重 → 后到的行覆盖前面的，
  返回的是**同一行出现两次**（两只完全不同的标的），一个错都不报。
  回包 `COMBHQ_COLS` 里没有 `Setcode` 列，所以只能靠"请求时的 market + 单独发"来归属。
* `pb.write_varint(-1)` 旧实现 `val >>= 7` 是算术右移，`-1 >> 7 == -1` → `while True` 死循环
  （Protobuf 的负数是 64 位补码，编成 10 字节）。
"""
from __future__ import annotations

import pytest

from tdxdata import errors
from tdxdata._engine import hq_extra
from tdxdata._engine import pb

# ══════════════════════════════════════════════════════════════════════════
# 一、DEF-014：跨市场同码
# ══════════════════════════════════════════════════════════════════════════
COLS = ["code", "NOW"]


@pytest.fixture()
def stub_batches(monkeypatch):
    """把 `_one_batch` 换成"按请求的 market 造不同价"的桩，并记录每次调用。"""
    calls = []

    def fake(items):
        calls.append(list(items))
        rows = []
        for mk, code in items:
            # 用 market 造出可区分的值：market=1 → 11.0，market=0 → 22.0
            rows.append([code, "%.1f" % (11.0 if mk == 1 else 22.0)])
        return COLS, rows

    monkeypatch.setattr(hq_extra, "_one_batch", fake)
    return calls


def test_cross_market_same_code_returns_two_distinct_rows(stub_batches):
    cols, rows = hq_extra.get_quotes(["sh000001", "sz000001"])
    assert cols == COLS
    assert len(rows) == 2, "跨市场同码必须返回两行（旧实现只回一行并重复两次）"
    assert rows[0][0] == rows[1][0] == "000001"
    assert rows[0][1] != rows[1][1], "两行取值相同 → 有一路被覆盖了：%r" % (rows,)
    # 歧义码不进批量：两次单发，各带自己的 market
    assert stub_batches == [[(1, "000001")], [(0, "000001")]]


def test_unambiguous_codes_still_go_in_one_batch(stub_batches):
    """没有跨市场同码时，行为必须与旧版一致：一次批量请求，不额外发包。"""
    _cols, rows = hq_extra.get_quotes(["600519", "000002", "300750"])
    assert len(rows) == 3
    assert len(stub_batches) == 1, "不该为普通请求多发：%r" % (stub_batches,)
    assert stub_batches[0] == [(1, "600519"), (0, "000002"), (0, "300750")]


def test_same_pair_twice_is_returned_twice(stub_batches):
    """同一个 (market, code) 要两遍 → 返回两条（旧版也是这样，别在新分支里改掉）。"""
    _cols, rows = hq_extra.get_quotes([("sh", "600519"), ("sh", "600519")])
    assert len(rows) == 2 and rows[0] == rows[1]


def test_ambiguous_missing_still_reported(stub_batches, monkeypatch):
    """跨市场同码里缺一只，仍要在 `EmptyResult` 里点名（不能因为换了 key 就漏报）。"""
    def fake(items):
        return COLS, ([[items[0][1], "11.0"]] if items[0][0] == 1 else [])

    monkeypatch.setattr(hq_extra, "_one_batch", fake)
    with pytest.raises(errors.EmptyResult) as ei:
        hq_extra.get_quotes(["sh000001", "sz000001"])
    assert "000001" in str(ei.value)


def test_batch_key_uses_request_market(stub_batches):
    """批量路径里同一批的 market 必须来自**请求**（回包没有 Setcode 列可查）。"""
    _cols, rows = hq_extra.get_quotes(["sh600519", "600519"])
    # 两个不同码，但一个是沪市 1、一个按默认规则解析 → 行数必须等于请求数
    assert len(rows) == 2


# ══════════════════════════════════════════════════════════════════════════
# 二、DEF-047：varint 负值
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("val,expect_len", [
    (0, 1), (1, 1), (127, 1), (128, 2), (300, 2), (2 ** 63 - 1, 9),
    (-1, 10), (-2, 10), (-(2 ** 63), 10),
])
def test_write_varint_matches_protobuf(val, expect_len):
    """负值按 64 位补码编（protobuf 语义），绝不进入死循环。"""
    buf = pb.write_varint(val)
    assert len(buf) == expect_len
    back, pos = pb.read_varint(buf, 0)
    assert pos == len(buf)
    assert back == (val if val >= 0 else val + (1 << 64))


def test_write_varint_negative_is_exactly_ten_bytes_of_ff_for_minus_one():
    assert pb.write_varint(-1) == b"\xff" * 9 + b"\x01"


def test_write_varint_rejects_out_of_64bit_range():
    with pytest.raises(ValueError):
        pb.write_varint(1 << 64)
    with pytest.raises(ValueError):
        pb.write_varint(-(1 << 64) - 1)


def test_write_varint_roundtrips_field_encoding():
    """一次真实的字段编码往返：负 int 字段能被自己的解码器按 64 位补码读回来。"""
    from tdxdata._engine.hq_pb import _vfield

    buf = _vfield(1, -5)                       # 现场编码一个负 int 字段
    fields = pb.decode_message(buf)
    assert fields == [(1, 0, (1 << 64) - 5)]
