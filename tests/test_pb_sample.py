# -*- coding: utf-8 -*-
r"""`PBSample` → `Client.get_intraday_samples()`：**离线验收**（零网络、零抓包依赖）。

这条测试的核心不是"能解出 139 个 float"，而是**语义锚定**——
"结构解出来了"和"这些数字是分时点位"是两件事，后者需要外部证据：

| 断言 | 证据 |
|---|---|
| `n_points(f7) == len(points)` | 25/25 帧恒等 → **逐点**，不是等距抽样 |
| `pre_close(f6) == 3658.026` | 同抓包 `PBCombHQ(999999).CLOSE == 3658.03` |
| `points[-1] == 3646.048` | 同刻 `PBCombHQ(999999).NOW` 读数 `3645.42`(#165)↔`3646.30`(#398) 之间 |
| `len(points) == 139 == 121 + 18` | 13:18 已走过的分钟数（上午 121 + 下午 18），与同刻 `PBSdstat(minute)` 的 139 行相同 |

⚠️ 本轮**没能**用 `PBZST` 做值锚：抓包里 25 帧 `PBZST` 全是 `880005`（板块涨跌家数），
没有一条是 `999999`，标的都不同，数值不可比。`PBZST` 只用来校时刻轴（140 行 = 139 + 1 分钟）。
这条限制写在方法的 docstring 里，也写在下面的 `test_why_pbzst_cannot_anchor_values`。
"""
from __future__ import annotations

import base64
import struct

import pytest

import tdxdata
from tdxdata import errors
from tdxdata._engine import hq_pb
from tdxdata.models import Table

_CLS = getattr(tdxdata, "Client")

#: 真实应答体（582 B，已剥壳的 protobuf）。
#:
#: 来源：`PCAPdroid_23_10月_13_18_52(2).pcap`，`msgid=168`，ctl `0x11`，
#: `HQServ.PBSample` 应答：`field2=1` / `field3="999999"` / `field4=1`（沪）/
#: `field5=240`（全天点数）/ `field6=3658.0256`（昨收）/ `field7=139`（本帧点数）/
#: `field8=139 × float32`（点位）。
_SAMPLE_B64 = (
    "EAEaBjk5OTk5OSABKPABNWmgZEU4iwFCrATDbGJF1TNiRc6uYUUy8GFFN+VhRWR4YUVoaWFFz3VhRVEwYUUDN2FFbz9hRfM9"
    "YUVAz2FFPY5hRT08YUXDXGFFW0ZhRYo1YUUPwmBFMNxgRSB1YUXYjmFF75RhRSyBYUUEDmFFEuhgRYS9YEUskmBFG2BgRYN2"
    "YEXBhGBFLaNgRTJwYEX9W2BF4XhgRRI7YEXcMGBFdSdgRdUuYEWAXmBF+XtgRaNGYEXlF2BFNuhfRRy/X0UkrV9F16tfRbW2"
    "X0WWm19Fw2xfRRabX0XcZWBFirtgRW3SYEWuHGFFtIBgRWJyYEVwe2BF6hVgRR/6X0VeXmBFJ99gRc5DYUUUf2FFM6JhRUe+"
    "YUW4UGFFZ3ZhRbEvYUWmfmFFwJZhRU1cYUXhP2FFGWthRaNAYUVeh2FF7U5hRcwEYUVS+WBFzwVhRZhzYUUOOmJFTUdiRcWK"
    "YkWvbmJFsxpiRdcTYkUH/WFF5HFiRd5JYkUXB2JF8+1hRYY+YkVoAmJFWehhRQv0YUVoCmJFf9lhRVHtYUU0vWFFznhhRRau"
    "YUXfwWFFQKthRUS5YUUbymFF0JBhRQHAYUWA6GFFO6lhRZqVYUVWyWFFA69hRWLYYUXME2JF4/dhRd79YUXxFWJFVV1iRfjh"
    "YUUpp2JFHvZiRX8YY0X5AmNFEdliRSAiY0XkbGNFbZBjRa66Y0VjuGNFBXxjRZArY0W1qGNFlttjRcvyY0XvzWNFutdjRVcd"
    "ZEXG4GNF"
)

N_POINTS = 139          # field7
TOTAL_POINTS = 240      # field5（全天上限）
PRE_CLOSE = 3658.026    # field6，round 到 3 位（原值 3658.025634765625）
FIRST, LAST = 3622.798, 3646.048
LO, HI = 3574.798, 3649.834


# ══════════════════════════════════════════════════════════════════════════
# 合帧工具（不用被测代码拼字节）
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
    return _tag(field, 0) + _varint(value)


def _bfield(field: int, payload: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(payload)) + payload


def _f32field(field: int, value: float) -> bytes:
    return _tag(field, 5) + struct.pack("<f", value)


def _one(code="999999", market=1, total=240, pre=3658.025634765625,
         points=(1.0, 2.0, 3.0)) -> bytes:
    body = (_vfield(2, 1) + _bfield(3, code.encode()) + _vfield(4, market)
            + _vfield(5, total) + _f32field(6, pre) + _vfield(7, len(points)))
    body += _bfield(8, b"".join(struct.pack("<f", x) for x in points))
    return body


# ══════════════════════════════════════════════════════════════════════════
# 离线 Client
# ══════════════════════════════════════════════════════════════════════════
def _client():
    c = _CLS.__new__(_CLS)
    c._probe_allowed = False
    c._clock_probe = False
    c._clock = None
    c._last_call = {}
    c._in_cal = False
    return c


@pytest.fixture()
def wire(monkeypatch):
    """替换唯一出口 `pb_api._raw_send`；`peel`/解码/meta 全是真代码。"""
    state = {"body": b"", "calls": []}

    def fake(ctl, cmd, args, tries=4):
        state["calls"].append((cmd, ctl, bytes(args)))
        return state["body"]

    monkeypatch.setattr(hq_pb.tdx_pb_api, "_raw_send", fake)
    return state


# ══════════════════════════════════════════════════════════════════════════
# 一、请求字节：与抓包逐字节一致
# ══════════════════════════════════════════════════════════════════════════
def test_request_bytes_match_the_capture():
    """`field1{5:0}` + `f2=1` + `f3=999999` + `f4=1` + `f7=1` == 抓包字节。"""
    assert hq_pb._args_pbsample("999999", 1, None).hex() == \
        "0a02280010011a0639393939393920013801"


def test_request_carries_market_and_count():
    """市场进 `field4`、`count` 进 `field7`（抓包值是 1）。"""
    args = hq_pb._args_pbsample("600519", 0, 5)
    assert args.startswith(bytes.fromhex("0a02280010011a0636303035313920"))
    assert args.endswith(bytes.fromhex("3805"))


def test_request_omits_market_when_unknown():
    """`market=None` 时不写 `field4`（**不猜市场**）。"""
    assert _tag(4, 0) not in hq_pb._args_pbsample("999999", None, None)


def test_request_is_sent_with_ctl_0x11(wire):
    wire["body"] = base64.b64decode(_SAMPLE_B64)
    _client().get_intraday_samples(["999999"])
    assert wire["calls"][0][:2] == ("HQServ.PBSample", 0x11)


# ══════════════════════════════════════════════════════════════════════════
# 二、真实帧解码 + 语义锚定
# ══════════════════════════════════════════════════════════════════════════
def test_decoder_reads_the_captured_frame():
    d = hq_pb.decode_intraday_samples(base64.b64decode(_SAMPLE_B64))[0]
    assert d["code"] == "999999"
    assert d["market"] == 1
    assert d["total_points"] == TOTAL_POINTS          # field5 = 全天点数上限
    assert d["pre_close"] == PRE_CLOSE                # field6 = 昨收
    assert d["n_points"] == N_POINTS                  # field7 = 本帧点数
    assert len(d["points"]) == N_POINTS


def test_n_points_equals_len_points_so_it_is_point_by_point():
    """**逐点**（不是等距抽样）的判据：服务端声明 N 个点，就恰好给 N 个。"""
    d = hq_pb.decode_intraday_samples(base64.b64decode(_SAMPLE_B64))[0]
    assert d["n_points"] == len(d["points"]) == N_POINTS


# ══════════════════════════════════════════════════════════════════════════
# 构造回放：n_points 恒等式的属性测试（批次 E，报告 §6.5）
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("n", [1, 2, 7, 25, 121, 240])
def test_n_points_identity_survives_constructed_roundtrip(n):
    """**构造回放**属性测试：用 `_vfield/_f32field` 既有构造函数造 N 点帧
    → `decode_intraday_samples` 解码 → `n_points(field7) ≡ len(points)`。

    与 `test_n_points_equals_len_points_so_it_is_point_by_point`（单点真实
    抓包锚定）互补：那条证明"服务端真帧如此"，这条证明"解码层对**任意**
    N 都保持恒等"（1 点 / 满帧 240 / 越过 240 上限形态都验）。恒等破坏 =
    解码错位，这条抓的就是它。
    """
    pts = [float(i + 1) for i in range(n)]
    wire = _one(points=tuple(pts))
    d = hq_pb.decode_intraday_samples(wire)[0]
    assert d["n_points"] == n
    assert len(d["points"]) == n
    assert d["points"] == pts          # 逐点原样（float32 精度内）
    # 闸门侧同口径：invariants.check_minute_points 对解码输出也是绿的
    from tdxdata import invariants as inv

    assert inv.check_minute_points([d]) == []


def test_points_span_the_session_that_has_elapsed():
    """139 = 121（09:30–11:30）+ 18（13:00–13:18）—— 与同刻 `PBSdstat(minute)` 行数相同。"""
    assert N_POINTS == 121 + 18


def test_points_values_are_anchored_to_pbcombhq():
    """值锚：昨收 == `PBCombHQ.CLOSE`；末点落在同刻 `PBCombHQ.NOW` 的两条读数之间。"""
    d = hq_pb.decode_intraday_samples(base64.b64decode(_SAMPLE_B64))[0]
    pts = d["points"]
    assert pts[0] == FIRST and pts[-1] == LAST
    assert min(pts) == LO and max(pts) == HI
    # 昨收 3658.0256 ↔ PBCombHQ(999999).CLOSE = 3658.03（两位小数报价）
    assert round(d["pre_close"], 2) == 3658.03
    # 末点在 #165 的 NOW=3645.42 与 #398 的 NOW=3646.30 之间（同一抓包，相距几百个 msgid）
    assert 3645.42 <= pts[-1] <= 3646.30
    # 序列最高点与稍后一条 NOW=3649.84 吻合（说明序列确实覆盖到了那一刻）
    assert abs(max(pts) - 3649.84) < 0.01


def test_why_pbzst_cannot_anchor_values():
    """把"为什么没用 PBZST 做值锚"钉成可执行的断言（防止后人以为漏做了）。

    抓包里 `PBZST` 的样本**全是 `880005`**（板块涨跌家数），没有 `999999`；
    标的都不同 → 数值不可比。这条测试记录的是**证据缺口**，不是实现细节。
    """
    pbzst_codes = {"880005"}          # 25/25 帧（M6 §5）
    assert "999999" not in pbzst_codes, "抓包里若有 999999 的 PBZST，就该改用它做值锚"


def test_client_table_contract(wire):
    wire["body"] = base64.b64decode(_SAMPLE_B64)
    t = _client().get_intraday_samples(["999999"])
    assert isinstance(t, Table)
    assert t.cols == ["code", "market", "total_points", "pre_close",
                      "n_points", "points"]
    assert len(t.rows) == 1
    r = t.rows[0]
    assert r["code"] == "999999" and r["market"] == 1
    assert r["total_points"] == TOTAL_POINTS
    assert r["pre_close"] == PRE_CLOSE
    assert r["n_points"] == N_POINTS
    assert isinstance(r["points"], list) and len(r["points"]) == N_POINTS
    assert t.cmd == "HQServ.PBSample"


def test_points_stay_a_list_not_dynamic_columns(wire):
    """`points` **保留为 list**：绝不摊平成 `f0..f138` 那种动态列名。"""
    wire["body"] = base64.b64decode(_SAMPLE_B64)
    t = _client().get_intraday_samples(["999999"])
    assert t.cols[-1] == "points"
    assert not [c for c in t.cols if c.startswith("f")]


# ══════════════════════════════════════════════════════════════════════════
# 三、批量 / count / 多形状
# ══════════════════════════════════════════════════════════════════════════
def test_multiple_symbols_make_multiple_requests(monkeypatch, wire):
    """一只一次请求（`PBSample` 的参数里只有一个代码位，没有批量位）。

    这里把 `hq_pb.intraday_samples` 换成探针：要验的是**客户端**的批量编排
    （逐个发、市场从 `_check_symbol` 带下去、行序与入参一致），
    `peel`/解码那条链路已由真实帧的用例覆盖，不必在这里再跑一遍。
    """
    seen = []

    def spy(code, market=None, count=None, *, tries=4):
        seen.append((code, market))
        return [{"code": code, "market": market, "total_points": 240,
                 "pre_close": 1.0, "n_points": 2, "points": [1.0, 2.0], "off": 0}]

    monkeypatch.setattr(hq_pb, "intraday_samples", spy)
    t = _client().get_intraday_samples(["999999", "600519"])
    assert seen == [("999999", 1), ("600519", 1)]     # 市场由 _check_symbol 定
    assert [r["code"] for r in t.rows] == ["999999", "600519"]
    assert t.provenance.extra["missing"] == []


def test_count_truncates_locally_and_says_so(wire):
    """`count=5` → 本地保留**最近** 5 点；`n_points` 仍是服务端声明值 139。"""
    wire["body"] = base64.b64decode(_SAMPLE_B64)
    r = _client().get_intraday_samples(["999999"], count=5).rows[0]
    assert len(r["points"]) == 5
    assert r["points"][-1] == LAST       # 保留的是尾部（最近的）
    assert r["n_points"] == N_POINTS     # 服务端声明值不因本地截尾而改


def test_sample_more_shape_is_also_understood(monkeypatch, wire):
    """`PBSampleMore` 的**重复 `field1`** 形状也能解（两者单条布局同构）。"""
    body = (_bfield(1, _one(code="999999", points=(1.0, 2.0)))
            + _bfield(1, _one(code="399001", market=0, total=60, points=(3.0,))))
    got = hq_pb.decode_intraday_samples(body)
    assert [g["code"] for g in got] == ["999999", "399001"]
    assert [g["total_points"] for g in got] == [240, 60]


def test_partial_success_is_reported_in_meta(wire, monkeypatch):
    """一只回、一只不回 → 有一行结果 + `meta["missing"]` 点名，不静默丢。"""
    wire["body"] = base64.b64decode(_SAMPLE_B64)
    real = hq_pb.intraday_samples

    def half(code, market=None, count=None, *, tries=4):
        return real(code, market, count, tries=tries) if code == "999999" else None

    monkeypatch.setattr(hq_pb, "intraday_samples", half)
    t = _client().get_intraday_samples(["999999", "600519"])
    assert len(t.rows) == 1
    assert t.provenance.extra["missing"] == ["600519"]
    assert any("600519" in n for n in t.provenance.notes)


# ══════════════════════════════════════════════════════════════════════════
# 四、红线：非法标的先拒（零请求）/ 空结果抛异常 / meta 完整
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("bad", ["00700", "60051", "1234567"])
def test_out_of_scope_symbol_rejected_before_any_request(bad, wire):
    """位数不对（5 位会补零命中别的标的 / 7 位会截断）必须在**发请求之前**就拒。"""
    with pytest.raises(errors.OutOfScope):
        _client().get_intraday_samples([bad])
    assert not wire["calls"], "非法标的居然发了请求"


def test_empty_argument_raises_value_error(wire):
    with pytest.raises(ValueError):
        _client().get_intraday_samples([])


def test_no_reply_raises_empty_result(wire):
    wire["body"] = None
    with pytest.raises(errors.EmptyResult) as ei:
        _client().get_intraday_samples(["999999"])
    assert "999999" in str(ei.value)


def test_provenance_declares_why_as_of_is_none(wire):
    """ARCH-002：出处是一等字段 Provenance；语义访问走字段与 notes。"""
    wire["body"] = base64.b64decode(_SAMPLE_B64)
    prov = _client().get_intraday_samples(["999999"]).provenance
    for f in ("as_of", "status", "fetched_at", "elapsed", "cmd"):
        assert hasattr(prov, f), "缺语义字段 %s" % f
    assert prov.as_of is None and any("as_of" in n for n in prov.notes)
    assert prov.cmd == "HQServ.PBSample"
    assert prov.ctl in (0x11, None)
    # 实证范围必须写在 notes 里（个股未复核）
    assert any("999999" in n for n in prov.notes)


# ══════════════════════════════════════════════════════════════════════════
# 五、解码器韧性
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("junk", [b"", b"\x00", b"\x0b", b"\xff" * 4, b"\x12\xff"])
def test_decoder_never_raises_on_junk(junk):
    assert hq_pb.decode_intraday_samples(junk) == []          # noqa: PLC1901


def test_decoder_without_points_field_returns_none_not_empty_list():
    """没有 `field8` → `points is None`（不知道），不是 `[]`（"零个点"这个假事实）。"""
    d = hq_pb.decode_intraday_samples(_one(points=()))[0]
    # 空 payload 的 field8 仍在 → 解出空数组；完全没有 field8 才是 None
    assert d["points"] == []
    body = (_vfield(2, 1) + _bfield(3, b"999999") + _vfield(4, 1) + _vfield(5, 240)
            + _vfield(7, 0))
    assert hq_pb.decode_intraday_samples(body)[0]["points"] is None
