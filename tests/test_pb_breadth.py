# -*- coding: utf-8 -*-
r"""`PBSdstat` → `Client.get_market_breadth()`：**离线验收**（零网络、零抓包依赖）。

为什么这条测试值得单独存在
--------------------------
`PBSdstat` 是本库唯一能给"**此刻**全市场多少家在涨"的通道，而它的回包**不是**
「列名 + 字符串行」的表结构（`pb.parse_table()` 认不出来），只能靠专用解码器。
专用解码器一旦写错，症状是**静默错值**（把 `f1` 当 `f2`，把 uint32 当 float32），
不会抛异常 —— 所以必须有真实帧把它钉住。

三块证据，缺一不可
------------------
1. **真实帧**（base64 内嵌，来自 2026-10-23 抓包，`msgid=170` / ctl `0x11`）：
   跑通 `peel → decode → Table` 全链路，并把 5 个基数值钉死
   （1637 / 3775 / 160 / 35 / 15）。
2. **请求字节与抓包逐字节比对**：`0a0228001004` —— 这是"能连上"的关键，
   参数错一个字节服务端就不认。
3. **合成帧**覆盖真实帧覆盖不到的分支：逐分钟形状、字段缺失（必须 `None` 而不是 0）、
   空回包、非法视图。

红线复核点
----------
* **`unknown ≠ 0`**：`test_missing_field_is_none_not_zero` 守着它。
* **`as_of` 拿不到就是 `None`**：`test_meta_declares_why_as_of_is_none` 守着它。
* **未定的 10 个 `uint32` 只进 `raw`**：`test_minute_view_keeps_unknown_series_in_raw`。
"""
from __future__ import annotations

import base64
import struct

import pytest

import tdxdata
from tdxdata import errors
from tdxdata._engine import hq_pb
from tdxdata.models import Table

_CLS = getattr(tdxdata, "Client")          # 红线：测试里不写 `Client(` 字面量

#: 真实应答体（421 B，已剥壳的 protobuf）。
#:
#: 来源：`PCAPdroid_23_10月_13_18_52(2).pcap`，`msgid=170`，ctl `0x11`，
#: `HQServ.PBSdstat` 应答。同一时刻的交叉校验（M6 报告 §1.2）：
#: `PBHQInfo#167` 的 `f8/f5/f7/f28/f29` 与 `PBZST#172` 末行的 `Now/Average`
#: 给出**同一组数** 1637 / 3775 / 160 / 35 / 15。
_SUMMARY_B64 = (
    "EAE6oAMI5QwQvx0YoAEgIygPNccatD86DAAAAAAAAAAAAAAAAEVyAMbQShwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "VSD/A1NdFZIeU2WVZiRTbWUX1FJ124c/U31PVwFTgAEBiAGuApABrgKdAe2bwj+lAaxKHVCtAWzyMT+yAQ0AAAAAAAAABi/L"
    "AsEFuAE+wgEMqgNyFQABAAAAAAAAyAHlDdIBBJYL7QHdAVB+ZT7lAcUO37/tATmFEFH1AZ11OlH9AQgc+lCAAukBiAJMkAIB"
    "mgIMAAEAAAABAgEHKLcBqgILOwcHAgAAAAAAAACwAsgCugIDgAI0xQI6YLC+zQKKpDE/1QILZqJS3QISmspS5QJfPXhS7QLP"
    "v6699QLB0UBS/QL1XGxShQPdHxtSjQMKtRy/lQORWl9SnQMeqodSpQMCozhSrQO21JU7tQN7qKFRvQN9IcVRxQPyzmJRygMM"
    "BAgCAgEBBgELF9EB0APHAdoDD90Kvwq/BMMBPiANCAcIAeUDlaZJUO0DKzALUPADBfgDFYAEP4gEbpAEeQ=="
)

#: 上面那帧的 5 个行业已定的家数（与 `SDSTAT_COLS` 的字段号对应）
EXPECT = {"up_count": 1637, "down_count": 3775, "flat_count": 160,
          "limit_up_count": 35, "limit_down_count": 15}

#: 当日有价证券总数：`up + down + flat` 在 27 帧里**恒等于** 5572（M6 §1.2）
TOTAL = 5572


# ══════════════════════════════════════════════════════════════════════════
# 合帧工具（**不用被测代码**拼字节，避免"自己验自己"）
# ══════════════════════════════════════════════════════════════════════════
def _varint(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _vfield(field: int, value: int) -> bytes:
    """varint 字段（wire 0）。"""
    return _tag(field, 0) + _varint(value)


def _bfield(field: int, payload: bytes) -> bytes:
    """length-delimited 字段（wire 2）。"""
    return _tag(field, 2) + _varint(len(payload)) + payload


def _f32field(field: int, value: float) -> bytes:
    """float32 字段（wire 5）。"""
    return _tag(field, 5) + struct.pack("<f", value)


def _summary_frame(fields) -> bytes:
    """合成形状 A：`field2`=条数 + `field7`=记录体。"""
    rec = b"".join(_vfield(no, val) for no, val in fields)
    return _vfield(2, 1) + _bfield(7, rec)


def _minute_frame(n: int = 3) -> bytes:
    """合成形状 B：`field2`=行数 + 重复 `field6{ field1=10×u32, field2=float32 }`。"""
    body = _vfield(2, n)
    for i in range(n):
        u32 = b"".join(struct.pack("<I", i * 100 + k) for k in range(10))
        row = _bfield(1, u32) + _f32field(2, 10000.0 + i)
        body += _bfield(6, row)
    return body


# ══════════════════════════════════════════════════════════════════════════
# 离线 Client（不建连；`_raw_send` 换成本地字节，`peel`/解码/meta 全是真代码）
# ══════════════════════════════════════════════════════════════════════════
def _client():
    c = _CLS.__new__(_CLS)
    c._probe_allowed = False            # 不许探针联网
    c._clock_probe = False
    c._clock = None
    c._last_call = {}
    c._in_cal = False
    return c


@pytest.fixture()
def wire(monkeypatch):
    """替换**唯一**的出口 `pb_api._raw_send`：返回给定字节（`None` = 无回包）。

    桩打在这个层级而不是 `hq_pb.market_breadth`，是因为 `peel`（剥 `{"Length"}`/
    zlib/base64 套娃）与 `_read_fields` 才是真正容易写错的地方，必须真跑。
    """
    state = {"body": b"", "calls": []}

    def fake(ctl, cmd, args, tries=4):
        state["calls"].append((cmd, ctl, bytes(args)))
        return state["body"]

    monkeypatch.setattr(hq_pb.tdx_pb_api, "_raw_send", fake)
    return state


# ══════════════════════════════════════════════════════════════════════════
# 一、请求字节：与抓包逐字节一致（参数错一字节 → 服务端不认）
# ══════════════════════════════════════════════════════════════════════════
def test_summary_request_bytes_match_the_capture():
    """`field1{5:0}` + `field2=4` == 抓包里的 `0a0228001004`。"""
    assert hq_pb._args_pbsdstat(4) == bytes.fromhex("0a0228001004")


def test_minute_request_bytes_only_differ_in_the_view_code():
    """`view=3` 与 `view=4` 只差最后一个字节 —— 视图码就在 `field2`。"""
    assert hq_pb._args_pbsdstat(3) == bytes.fromhex("0a0228001003")


def test_request_is_sent_with_ctl_0x11(wire):
    """ctl 必须是 `0x11`（抓包实测），命令名是 `HQServ.PBSdstat`。"""
    wire["body"] = base64.b64decode(_SUMMARY_B64)
    _client().get_market_breadth()
    assert wire["calls"][0][:2] == ("HQServ.PBSdstat", 0x11)


# ══════════════════════════════════════════════════════════════════════════
# 二、真实帧：解码 + 定标
# ══════════════════════════════════════════════════════════════════════════
def test_decoder_reads_the_captured_frame():
    got = hq_pb.decode_market_breadth(base64.b64decode(_SUMMARY_B64))
    assert got["shape"] == "summary"
    assert got["declared_count"] == 1
    assert got["fields"] == {1: 1637, 2: 3775, 3: 160, 4: 35, 5: 15}


def test_breadth_sum_equals_the_listed_security_count():
    """`up + down + flat == 5572` —— 这条自校验把"哪个字段是哪个"钉死。

    5572 是当日有价证券总数（27 帧恒等，M6 §1.2）；只有"涨/跌/平"三者相加才可能
    恒等于它，`涨停/跌停`（35/15）显然不是这一组，所以字段号与语义的对应没错位。
    """
    got = hq_pb.decode_market_breadth(base64.b64decode(_SUMMARY_B64))
    f = got["fields"]
    assert f[1] + f[2] + f[3] == TOTAL


def test_client_summary_table_contract(wire):
    """公开契约：**恰好 5 列**、1 行、值来自 `f1..f5`。"""
    wire["body"] = base64.b64decode(_SUMMARY_B64)
    t = _client().get_market_breadth()
    assert isinstance(t, Table)
    assert t.cols == ["up_count", "down_count", "flat_count",
                      "limit_up_count", "limit_down_count"]
    assert len(t.rows) == 1
    assert dict(t.rows[0]) == EXPECT
    assert t.cmd == "HQServ.PBSdstat"
    assert t.provenance.extra["view"] == "summary"


def test_sum_view_and_minute_view_are_not_confused(wire):
    """`view="minute"` 必须走形状 B（合成帧），列是 `minute_index`+`f2`。"""
    wire["body"] = _minute_frame(3)
    t = _client().get_market_breadth(view="minute")
    assert t.cols == ["minute_index", "f2"]
    assert len(t.rows) == 3
    assert [r["minute_index"] for r in t.rows] == [0, 1, 2]
    assert t.rows[0]["f2"] == pytest.approx(10000.0)


# ══════════════════════════════════════════════════════════════════════════
# 三、红线：unknown ≠ 0 / 未定字段只进 raw / as_of 必须解释
# ══════════════════════════════════════════════════════════════════════════
def test_missing_field_is_none_not_zero(wire):
    """服务端没发的字段 → `None`。**绝不能**变成 0（0 = "零家上涨"这个假事实）。"""
    wire["body"] = _summary_frame([(1, 5), (2, 6)])       # 只有 up/down
    t = _client().get_market_breadth()
    row = dict(t.rows[0])
    assert row["up_count"] == 5 and row["down_count"] == 6
    assert row["flat_count"] is None
    assert row["limit_up_count"] is None and row["limit_down_count"] is None


def test_minute_view_keeps_unknown_series_in_raw(wire):
    """每行 10 个 `uint32` 语义未定 → **不进标准列**，原样放 `row["raw"]["u32"]`。"""
    wire["body"] = _minute_frame(2)
    t = _client().get_market_breadth(view="minute")
    assert "u32" not in t.cols and "raw" not in t.cols
    assert t.rows[1]["raw"]["u32"] == [100 + k for k in range(10)]


def test_u32_is_not_decoded_as_float32():
    """同一个 40 字节：按 `uint32` 解是整数，按 `float32` 解会是 1e-3 级小数。

    这是最容易写错的一处（`hq_extra.parse_samples` 就把它当 float32 读过）。
    """
    got = hq_pb.decode_market_breadth(_minute_frame(1))
    u32 = got["rows"][0]["u32"]
    assert u32 == list(range(10))
    assert all(isinstance(v, int) for v in u32)


def test_provenance_declares_why_as_of_is_none(wire):
    """ARCH-002：`as_of is None` 必须在 notes 里给出原因；语义字段必须在。"""
    wire["body"] = base64.b64decode(_SUMMARY_B64)
    prov = _client().get_market_breadth().provenance
    for f in ("as_of", "status", "fetched_at", "elapsed", "cmd"):
        assert hasattr(prov, f), "缺语义字段 %s" % f
    assert prov.as_of is None
    assert any("as_of" in n for n in prov.notes), "as_of 是 None 却没写原因"
    assert prov.cmd == "HQServ.PBSdstat"
    assert prov.ctl in (0x11, None)


def test_note_distinguishes_it_from_market_stat(wire):
    """`notes` 必须说清与 `get_market_stat('updown')` / `get_breadth` 的区别。"""
    wire["body"] = base64.b64decode(_SUMMARY_B64)
    notes = _client().get_market_breadth().provenance.notes
    joined = "；".join(notes)
    assert "get_market_stat" in joined and "get_breadth" in joined


# ══════════════════════════════════════════════════════════════════════════
# 四、失败路径必须是**显式异常**，不是空表
# ══════════════════════════════════════════════════════════════════════════
def test_no_reply_raises_empty_result(wire):
    wire["body"] = None                                   # 一帧都没回
    with pytest.raises(errors.EmptyResult):
        _client().get_market_breadth()


def test_empty_bodies_raise_empty_result(wire):
    wire["body"] = b""
    with pytest.raises(errors.EmptyResult):
        _client().get_market_breadth()


def test_summary_shape_without_any_field_raises(wire):
    """判成 summary 但字段全空 → 抛错（而不是回一张全 `None` 的表）。"""
    wire["body"] = _vfield(2, 1) + _bfield(7, b"")
    with pytest.raises(errors.EmptyResult):
        _client().get_market_breadth()


def test_minute_shape_without_rows_raises(wire):
    wire["body"] = _vfield(2, 0)
    with pytest.raises(errors.EmptyResult):
        _client().get_market_breadth(view="minute")


def test_unknown_view_is_rejected_before_any_request(wire):
    with pytest.raises(ValueError) as ei:
        _client().get_market_breadth(view="all")
    assert "summary" in str(ei.value) and "minute" in str(ei.value)
    assert not wire["calls"], "非法 view 不许发请求"


def test_view_is_case_insensitive(wire):
    wire["body"] = base64.b64decode(_SUMMARY_B64)
    assert _client().get_market_breadth(view="SUMMARY").rows[0]["up_count"] == 1637


# ══════════════════════════════════════════════════════════════════════════
# 五、解码器对"垃圾输入"不许抛（更不许编数据）
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("junk", [b"", b"\x00", b"\x0b\x3b", b"\xff" * 8,
                                  b"\x08", b"\x12\xff\xff"])
def test_decoder_never_raises_on_junk(junk):
    got = hq_pb.decode_market_breadth(junk)
    assert got["shape"] in ("summary", "minute")
    assert not got.get("fields") or got["shape"] == "minute"
