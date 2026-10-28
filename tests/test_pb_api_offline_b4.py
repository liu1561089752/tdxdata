# -*- coding: utf-8 -*-
"""pb_api 模块离线单测（B4，2026-11）。

覆盖：_load_specs / load_args / args_for / build / _raw_send（重试与失败
路径）/ decode_any / call_pb / parse_pb_any。零网络：dc._send 全 mock。
"""
from __future__ import annotations

import base64
import json
import sys
import time
import zlib

import pytest

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "F:/TDX_stock_data/tdxdata")

from tdxdata._engine import pb_api as PA                        # noqa: E402
from tdxdata._engine import datacenter as dc                    # noqa: E402
from tdxdata import errors                                      # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("测试试图真实建连 %r —— 本文件必须保持零网络" % (a,))
    monkeypatch.setattr(dc.socket, "create_connection", _boom)
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    yield


# ── _load_specs / PB ─────────────────────────────────────────────────────
def test_pb_specs_loaded_from_catalog():
    """pb_specs.json 必须能加载出至少一个 PB 命令（含点号 + kind）。"""
    assert isinstance(PA.PB, dict) and PA.PB, "PB 规格表为空"
    for k, v in list(PA.PB.items())[:5]:
        assert "." in k
        assert "kind" in v and "ctl" in v


def test_args_for_b64_and_text(monkeypatch):
    monkeypatch.setattr(PA, "_ARGS", {
        "A.Cmd1": "Base64:CAE=",
        "A.Cmd2": "plain-text",
        "A.Cmd3": {"p": 1},
    })
    assert PA.args_for("A.Cmd1") == base64.b64decode("CAE=")
    assert PA.args_for("A.Cmd2") == b"plain-text"
    assert PA.args_for("A.Cmd3") == json.dumps({"p": 1}).encode()
    assert PA.args_for("A.Unknown") == b""


# ── build ────────────────────────────────────────────────────────────────
def test_build_frame_layout():
    import struct
    f = PA.build(0x0E, "HQServ.PBFXT", b"\x01\x02")
    assert f[0] == 0x0B
    mid, = struct.unpack_from("<I", f, 1)
    assert mid == 0x7000
    assert f[5] == 0x0E
    ln1, ln2 = struct.unpack_from("<HH", f, 6)
    assert ln1 == ln2 == 2
    typ, = struct.unpack_from("<H", f, 10)
    assert typ == 0x264B
    assert f[14:].startswith(b"HQServ.PBFXT\x00\x00\x01\x02")


# ── _raw_send ────────────────────────────────────────────────────────────
def test_raw_send_returns_first_blob(monkeypatch):
    monkeypatch.setattr(dc, "_send",
                        lambda frame, **kw: [b"blob0", b"blob1"])
    assert PA._raw_send(0x0E, "HQServ.X", b"") == b"blob0"


def test_raw_send_retries_then_raises_last(monkeypatch):
    """ConnectionFailed 重试 tries 轮，仍失败抛最后一个（不折叠成 None）。"""
    n = {"c": 0}

    def fake_send(frame, **kw):
        n["c"] += 1
        raise errors.ConnectionFailed("down")

    monkeypatch.setattr(dc, "_send", fake_send)
    with pytest.raises(errors.ConnectionFailed):
        PA._raw_send(0x0E, "HQServ.X", b"", tries=3, backoff=0)
    assert n["c"] == 3


def test_raw_send_empty_blobs_is_protocol_error(monkeypatch):
    monkeypatch.setattr(dc, "_send", lambda frame, **kw: [])
    with pytest.raises(errors.ProtocolError):
        PA._raw_send(0x0E, "HQServ.X", b"", tries=1)


def test_raw_send_does_not_retry_unexpected_exceptions(monkeypatch):
    """非传输类异常（如 ValueError）不许被吞掉重试——原样冒泡。"""
    n = {"c": 0}

    def fake_send(frame, **kw):
        n["c"] += 1
        raise ValueError("bad frame")

    monkeypatch.setattr(dc, "_send", fake_send)
    with pytest.raises(ValueError):
        PA._raw_send(0x0E, "HQServ.X", b"", tries=3)
    assert n["c"] == 1


# ── decode_any ───────────────────────────────────────────────────────────
def _pb_sample() -> bytes:
    def vint(v):
        out = bytearray()
        while True:
            b = v & 0x7F
            v >>= 7
            if v:
                out.append(b | 0x80)
            else:
                out.append(b)
                return bytes(out)

    def ld(f, payload):
        return vint(f << 3 | 2) + vint(len(payload)) + payload

    names = b"".join(ld(1, c.encode()) for c in ("Data", "Second", "Close"))
    rows = b""
    for d, t, c in [("20260427", "0", "12.3"), ("20260428", "1", "12.5")]:
        cells = b"".join(ld(1, x.encode()) for x in (d, t, c))
        rows += ld(7, cells)
    return ld(6, names) + rows


PB_SAMPLE = _pb_sample()


def test_decode_any_empty():
    assert PA.decode_any(b"") == ("raw", b"")


def test_decode_any_json():
    kind, obj = PA.decode_any(b'{"a": 1}')
    assert kind == "json" and obj == {"a": 1}


def test_decode_any_zlib_json():
    kind, obj = PA.decode_any(zlib.compress(b'[1, 2, 3]'))
    assert kind == "json" and obj == [1, 2, 3]


def test_decode_any_zlib_bad_stays_raw():
    kind, _ = PA.decode_any(b"\x78" + b"\x00" * 8)
    assert kind in ("raw", "pb")            # 坏 zlib 流不许抛异常


def test_decode_any_pb():
    kind, tree = PA.decode_any(PB_SAMPLE)
    assert kind == "pb"
    fields = {f for f, _w, _v in tree}
    assert 6 in fields and 7 in fields


def test_decode_any_pb_zlib_wrapped():
    kind, tree = PA.decode_any(zlib.compress(PB_SAMPLE))
    assert kind == "pb"


def test_decode_any_unknown_is_raw():
    kind, blob = PA.decode_any(b"\xde\xad\xbe\xef")
    assert kind == "raw" and blob == b"\xde\xad\xbe\xef"


def test_decode_any_broken_json_then_pb():
    """首字节像 JSON 但解析失败 → 落到 PB/raw，不许抛。"""
    kind, _ = PA.decode_any(b'{"not closed')
    assert kind in ("pb", "raw")


# ── call_pb ──────────────────────────────────────────────────────────────
def test_call_pb_json_result(monkeypatch):
    monkeypatch.setattr(PA, "_raw_send",
                        lambda c, cmd, args, tries=4: b'{"ok": 1}')
    assert PA.call_pb("HQServ.Unknown", ctl=0x0E) == {"ok": 1}


def test_call_pb_table_result(monkeypatch):
    monkeypatch.setattr(PA, "_raw_send",
                        lambda c, cmd, args, tries=4: PB_SAMPLE)
    cols, rows = PA.call_pb("HQServ.Unknown", ctl=0x0E)
    assert cols == ["Data", "Second", "Close"]
    assert rows[0]["Data"] == "20260427"
    assert rows[1]["Close"] == "12.5"


def test_call_pb_all_candidates_fail_raises(monkeypatch):
    """传输失败遍历所有候选 ctl 后上抛，绝不 return None。"""
    monkeypatch.setattr(PA, "_raw_send",
                        lambda c, cmd, args, tries=4:
                        (_ for _ in ()).throw(errors.ConnectionFailed("down")))
    with pytest.raises(errors.ConnectionFailed):
        PA.call_pb("HQServ.PBFXT")           # 多个候选 ctl


def test_call_pb_raw_blob_skipped_then_error(monkeypatch):
    """raw 回包跳过；全候选解不出 → EmptyResult（B7/DEF-043 改型：不再静默 None）。"""
    monkeypatch.setattr(PA, "_raw_send", lambda c, cmd, args, tries=4: b"\xde\xad")
    with pytest.raises(errors.EmptyResult):
        PA.call_pb("HQServ.Unknown", ctl=0x0E)


def test_call_pb_uses_args_template(monkeypatch):
    """args=None 时必须从 _ARGS 模板取参数。"""
    seen = {}
    def fake(c, cmd, args, tries=4):
        seen.setdefault("a", args)
        return b'{"ok": 1}'                       # 回个可解析的，别落 EmptyResult
    monkeypatch.setattr(PA, "_raw_send", fake)
    monkeypatch.setattr(PA, "_ARGS", {"HQServ.Known": "tpl-bytes"})
    PA.call_pb("HQServ.Known", ctl=0x0E)
    assert seen["a"] == b"tpl-bytes"


# ── parse_pb_any ─────────────────────────────────────────────────────────
def test_parse_pb_any_json():
    assert PA.parse_pb_any(b'{"x": 9}') == {"x": 9}


def test_parse_pb_any_table():
    cols, rows = PA.parse_pb_any(PB_SAMPLE)
    assert cols == ["Data", "Second", "Close"] and len(rows) == 2


def test_parse_pb_any_raw_is_none():
    assert PA.parse_pb_any(b"\xde\xad\xbe\xef") is None


# ── 揭示性测试 ───────────────────────────────────────────────────────────
def test_decode_any_zip_bomb_materializes_fully():
    """揭示（登记用，当前行为如此）：decode_any 直接 zlib.decompress，没有
    输出上限 —— 一个 60KB 的炸弹会解出 64MB。这本身是**当前**行为：
    本测试把它固定下来，将来若加防护（期望 max_output 限制）应改写此测试
    为"超限 → raw/ProtocolError"。参照 DEF-B4-02。"""
    bomb = zlib.compress(b"\x00" * (16 * 1024 * 1024), 9)   # 16MB → ~15KB
    kind, _ = PA.decode_any(bomb)
    assert kind == "raw"                                   # NUL 流不是 JSON/PB
    assert len(zlib.compress(b"\x00" * (16 * 1024 * 1024), 9)) < 100_000, \
        "压缩比前提失效，请重写此揭示测试"


def test_decode_any_large_but_legit_still_works():
    """基线：合法的大一点的压缩响应必须照常解出（防止将来加防护时误伤）。"""
    big = json.dumps({"rows": list(range(10000))}).encode()
    kind, obj = PA.decode_any(zlib.compress(big))
    assert kind == "json" and len(obj["rows"]) == 10000


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
