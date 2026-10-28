# -*- coding: utf-8 -*-
"""datacenter 模块离线单测（B4，2026-11）。

覆盖：decode_json / _cjk_score / unwrap / unwrap_frames / parse_resultset /
parse_flat / parse_tables / parse_main / _colnames / _pct / 持久连接
（set_preferred_host / _drop_pb_conn / close_pb，全离线喂假 socket）。
零网络：socket.create_connection 全程被护栏 mock。
"""
from __future__ import annotations

import json
import sys
import time
import zlib

import pytest

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "F:/TDX_stock_data/tdxdata")

from tdxdata._engine import datacenter as dc                    # noqa: E402
from tdxdata._engine import pacer as P                          # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("测试试图真实建连 %r —— 本文件必须保持零网络" % (a,))
    monkeypatch.setattr(dc.socket, "create_connection", _boom)
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    P.reset_pacer()
    dc._drop_pb_conn()
    yield
    dc._drop_pb_conn()
    dc.set_preferred_host(None)
    P.reset_pacer()


# ── _cjk_score ───────────────────────────────────────────────────────────
def test_cjk_score_chinese_positive():
    assert dc._cjk_score("财务报表元") > 0


def test_cjk_score_armenian_negative():
    assert dc._cjk_score("աԪ") < 0


def test_cjk_score_kana_mild():
    assert dc._cjk_score("かな") > dc._cjk_score("ա") - 100
    assert 0 <= dc._cjk_score("かな") <= 10


def test_cjk_score_control_chars_penalized():
    assert dc._cjk_score("a\x00b\x01") < dc._cjk_score("ab")


def test_cjk_score_empty():
    assert dc._cjk_score("") == 0


# ── decode_json ──────────────────────────────────────────────────────────
def test_decode_json_utf8():
    obj, txt = dc.decode_json('{"a": "元"}'.encode("utf-8"))
    assert obj == {"a": "元"}


def test_decode_json_gbk_prefers_cjk():
    raw = '{"col": "营业收入"}'.encode("gbk")
    obj, txt = dc.decode_json(raw)
    assert obj is not None
    assert "营业收入" in txt


def test_decode_json_invalid_returns_none():
    obj, txt = dc.decode_json(b"\xff\xfe not json")
    assert obj is None and txt is None


# ── unwrap ───────────────────────────────────────────────────────────────
def test_unwrap_plain_json_with_header():
    payload = b'{"ResultSets": []}'
    buf = json.dumps({"Length": str(len(payload))}).encode() + payload
    assert dc.unwrap(buf) == [payload]


def test_unwrap_zlib_blob():
    payload = json.dumps({"ResultSets": [{"ColName": ["x"], "Content": [[1]]}]}).encode("utf-8")
    z = zlib.compress(payload)
    buf = json.dumps({"Length": str(len(z))}).encode() + z
    out = dc.unwrap(buf)
    assert out and b"ResultSets" in out[0]


def test_unwrap_empty_returns_empty():
    assert dc.unwrap(b"") == []


def test_unwrap_zlib_magic_fallback():
    payload = json.dumps({"k": "v" * 50}).encode()
    z = zlib.compress(payload)
    out = dc.unwrap(b"\x00\x01junk" + z)
    assert out and b"k" in out[0]


# ── unwrap_frames ────────────────────────────────────────────────────────
def _mk_frame(body: bytes, zlib_flag=False) -> bytes:
    import struct
    head = b"\x3b" if zlib_flag else b"\x0b"
    head += struct.pack("<I", 0x7000) + bytes([0x0E])
    head += struct.pack("<H", len(body)) + struct.pack("<H", len(body))
    head += b"\x4b\x26" + b"\x00\x64"
    return head + body


def test_unwrap_frames_plaintext():
    body = b"\x0a\x01\x05protobuf-ish"
    out = dc.unwrap_frames(_mk_frame(body))
    assert out == [body]


def test_unwrap_frames_zlib():
    body = zlib.compress(b'{"x": 1}')
    out = dc.unwrap_frames(_mk_frame(body, zlib_flag=True))
    assert out == [b'{"x": 1}']


def test_unwrap_frames_multiple():
    b1, b2 = b"aaaa", b"bbbb"
    out = dc.unwrap_frames(_mk_frame(b1) + _mk_frame(b2))
    assert out == [b1, b2]


def test_unwrap_frames_garbage_ignored():
    assert dc.unwrap_frames(b"\x00" * 20) == []


# ── parse_resultset / parse_tables / parse_main / parse_flat ─────────────
def _rs(colname, coldes, content, key="t0"):
    return {"ResultSetKey": key, "ColName": colname, "ColDes": coldes,
            "Content": content}


def test_parse_resultset_basic():
    blob = json.dumps({"ResultSets": [
        _rs(["N018", "N007"], [{"Name": "zt"}, {"Name": "zb"}],
            [[5, 2], [3, 1]])
    ]}).encode("utf-8")
    rows = dc.parse_resultset([blob])
    assert rows == [{"zt": 5, "zb": 2}, {"zt": 3, "zb": 1}]


def test_parse_resultset_coldes_fallback_to_colname():
    blob = json.dumps({"ResultSets": [
        _rs(["a", "b"], [], [[1, 2]])
    ]}).encode("utf-8")
    assert dc.parse_resultset([blob]) == [{"a": 1, "b": 2}]


def test_parse_resultset_skips_ragged_rows():
    blob = json.dumps({"ResultSets": [
        _rs(["a", "b"], [], [[1, 2], [3]])
    ]}).encode("utf-8")
    assert dc.parse_resultset([blob]) == [{"a": 1, "b": 2}]


def test_parse_resultset_ignores_non_dict():
    assert dc.parse_resultset([b"[1,2,3]", b"junk"]) == []


def test_parse_tables_keeps_both():
    blob = json.dumps({"ResultSets": [
        _rs(["isjr"], [], [[0]], key="meta"),
        _rs(["a", "b"], [], [[1, 2], [3, 4]], key="data"),
    ]}).encode("utf-8")
    tabs = dc.parse_tables([blob])
    keys = [t[0] for t in tabs]
    assert keys == ["meta", "data"]
    assert tabs[1][2] == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]


def test_parse_main_picks_largest():
    blob = json.dumps({"ResultSets": [
        _rs(["isjr"], [], [[0]], key="meta"),
        _rs(["a", "b", "c"], [], [[1, 2, 3], [4, 5, 6]], key="data"),
    ]}).encode("utf-8")
    rows = dc.parse_main([blob])
    assert rows and rows[0] == {"a": 1, "b": 2, "c": 3}


def test_parse_main_empty():
    assert dc.parse_main([]) == []
    assert dc.parse_main([b"[1]"]) == []


def test_parse_flat_four_part():
    blob = json.dumps([{"Length": "1"}, ["col1", "col2"], [],
                       ["v1", "v2"]]).encode("utf-8")
    assert dc.parse_flat([blob]) == [{"col1": "v1", "col2": "v2"}]


def test_parse_flat_skips_short_or_mismatch():
    b1 = json.dumps(["a"]).encode()
    b2 = json.dumps([{}, ["c1", "c2"], [], ["only1"]]).encode()
    assert dc.parse_flat([b1, b2]) == []


def test_parse_flat_not_list():
    assert dc.parse_flat([b'{"a": 1}']) == []


# ── _colnames ────────────────────────────────────────────────────────────
def test_colnames_strings():
    assert dc._colnames(["a", "b"]) == ["a", "b"]


def test_colnames_dicts():
    assert dc._colnames([{"Name": "N018"}, {"name": "N007"},
                         {"ColName": "N005"}]) == ["N018", "N007", "N005"]


def test_colnames_none_and_other():
    assert dc._colnames(None) == []
    assert dc._colnames([1, None, "x"]) == ["1", "None", "x"]


# ── _pct / now_iso / trading days ────────────────────────────────────────
def test_pct_basic_and_guards():
    assert dc._pct(110, 100) == 10.0
    assert dc._pct(None, 100) is None
    assert dc._pct(110, 0) is None
    assert dc._pct("x", 100) is None


def test_prev_trading_day_skips_weekend():
    # 2026-11-02 是周一 → 上一个工作日 2026-10-30（周五）
    assert dc.prev_trading_day("2026-11-02") == "2026-10-30"


def test_now_iso_format():
    import re
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", dc.now_iso())


# ── 持久连接（全离线） ────────────────────────────────────────────────────
class PBHandle:
    """假 PB socket：握手期 recv 立即超时（预排空），之后可发可收。"""

    def __init__(self):
        self.sent = []
        self.closed = False
        self._drained = False
        self.next_chunks = []

    def settimeout(self, t):
        pass

    def sendall(self, d):
        self.sent.append(d)

    def recv(self, n):
        if not self._drained:
            self._drained = True
            raise TimeoutError()
        if self.next_chunks:
            return self.next_chunks.pop(0)
        raise TimeoutError()

    def close(self):
        self.closed = True


def test_set_preferred_host_roundtrip():
    dc.set_preferred_host("1.2.3.4")
    assert dc.preferred_host() == "1.2.3.4"
    dc.set_preferred_host(None)
    assert dc.preferred_host() is None


def test_set_preferred_host_drops_existing_conn(monkeypatch):
    handles = []

    def fake_conn(addr, timeout=None):
        h = PBHandle()
        handles.append((addr, h))
        return h

    monkeypatch.setattr(dc.socket, "create_connection", fake_conn)
    dc.set_preferred_host("5.6.7.8")
    s1 = dc._pb_conn()
    assert s1 is not None and handles[-1][0][0] == "5.6.7.8"
    # 换偏好 → 旧连接必须被关掉（DEF-048 语义）
    dc.set_preferred_host("9.9.9.9")
    assert s1.closed, "换 host 没有丢旧连接，'钉住'未生效"


def test_pb_conn_reuses_warm_connection(monkeypatch):
    made = []

    def fake_conn(addr, timeout=None):
        made.append(addr)
        return PBHandle()

    monkeypatch.setattr(dc.socket, "create_connection", fake_conn)
    s1 = dc._pb_conn()
    s2 = dc._pb_conn()
    assert s1 is s2
    assert len(made) == 1, "热连接被无谓重建"


def test_pb_conn_rebuilds_after_idle_expiry(monkeypatch):
    made = []

    def fake_conn(addr, timeout=None):
        made.append(addr)
        return PBHandle()

    monkeypatch.setattr(dc.socket, "create_connection", fake_conn)
    s1 = dc._pb_conn()
    dc._CONN["t"] -= dc._PB_IDLE_RECONNECT + 1     # 人为老化
    s2 = dc._pb_conn()
    assert s1 is not s2 and s1.closed
    assert len(made) == 2


def test_close_pb_is_idempotent(monkeypatch):
    monkeypatch.setattr(dc.socket, "create_connection",
                        lambda addr, timeout=None: PBHandle())
    dc._pb_conn()
    dc.close_pb()
    dc.close_pb()
    assert dc._CONN["sock"] is None


# ── _send / call（离线：mock _pb_conn）────────────────────────────────────
def test_send_returns_unwrapped_blobs(monkeypatch):
    h = PBHandle()
    payload = b'{"ResultSets": []}'
    frame = b'{"Length": "18", "AttachLog": ""}' + payload

    state = {"round": 0}

    def fake_conn(host=None, *, drain=0.25):
        # 第 1 轮：回完整帧；第 2 轮（尾段后续 recv 已耗尽，不会走到）
        if state["round"] == 0:
            state["round"] = 1
            h.next_chunks = [frame]
        return h

    monkeypatch.setattr(dc, "_pb_conn", fake_conn)
    out = dc._send(b"FRAME", wait=1.0, idle=0.05, tail=0.01)
    assert out == [payload]
    # 注意：第二次重试也会发出同一帧（首轮 recv 已耗尽 → 空回包 → 重试），
    # 这是 _send 的 2 轮语义；判"至少发出过、且结果正确"即可。
    assert h.sent.count(b"FRAME") >= 1


def test_send_empty_reply_raises_connectionerror(monkeypatch):
    """空回包必须上抛 ConnectionError，不许伪装成"没数据"。"""
    h = PBHandle()

    def fake_conn(host=None, *, drain=0.25):
        return h

    monkeypatch.setattr(dc, "_pb_conn", fake_conn)
    with pytest.raises(dc.errors.ConnectionFailed):
        dc._send(b"FRAME", wait=0.5, idle=0.02, tail=0.005)


def test_send_retries_once_then_normalized_error(monkeypatch):
    """两轮都空 → errors.ConnectionFailed（不许裸 ConnectionError 冒泡）。"""
    h = PBHandle()
    n = {"c": 0}

    def fake_conn(host=None, *, drain=0.25):
        n["c"] += 1
        return h

    monkeypatch.setattr(dc, "_pb_conn", fake_conn)
    with pytest.raises(dc.errors.ConnectionFailed) as ei:
        dc._send(b"FRAME", wait=0.5, idle=0.02, tail=0.005)
    assert n["c"] == 2, "空回包应触发 2 轮重试"


def test_send_falls_back_to_unwrap_frames(monkeypatch):
    """unwrap() 认不出明文 protobuf 帧时走 unwrap_frames 兜底（D6）。"""
    h = PBHandle()
    body = b"\x0a\x03abc"                       # 假 protobuf，非 JSON 非 zlib
    raw = _mk_frame(body)

    def fake_conn(host=None, *, drain=0.25):
        h.next_chunks = [raw]
        return h

    monkeypatch.setattr(dc, "_pb_conn", fake_conn)
    out = dc._send(b"FRAME", wait=1.0, idle=0.05, tail=0.01)
    assert out == [body]


def test_call_encodes_gbk_for_non_ascii(monkeypatch):
    seen = {}

    def fake_send(frame, host=None, wait=15.0, idle=2.0, **kw):
        seen["frame"] = frame
        return [b'{"ResultSets": []}']

    monkeypatch.setattr(dc, "_send", fake_send)
    dc.call("CWServ.x", 0x1E, ["营业", "收入"])
    body = seen["frame"]
    assert "营业".encode("gbk") in body or "营业".encode("utf-8") in body


def test_call_raw_builds_frame_with_body(monkeypatch):
    seen = {}

    def fake_send(frame, host=None, wait=8.0, idle=2.5, **kw):
        seen["frame"] = frame
        return []

    monkeypatch.setattr(dc, "_send", fake_send)
    dc.call_raw("DataAggregation.x", 0x0B, b"[{}]")
    assert b"[{}]" in seen["frame"]


# ── save（原子写） ───────────────────────────────────────────────────────
def test_save_atomic(tmp_path):
    fn = str(tmp_path / "out.json")
    dc.save(fn, {"a": 1})
    with open(fn, encoding="utf-8") as f:
        assert json.load(f) == {"a": 1}
    import os
    assert not os.path.exists(fn + ".tmp")


def test_retry_returns_default_after_failures(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    n = {"c": 0}

    def fn():
        n["c"] += 1
        return None

    assert dc.retry(fn, tries=3, sleep=0) is None
    assert n["c"] == 3


def test_retry_returns_first_truthy(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    vals = iter([None, "ok"])

    assert dc.retry(lambda: next(vals), tries=3, sleep=0) == "ok"


def test_retry_swallows_exceptions(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    def fn():
        raise RuntimeError("boom")

    assert dc.retry(fn, tries=2, sleep=0, default="d") == "d"


# ── fetch_* / _seal / main（离线：mock call）──────────────────────────────
def test_seal_rate():
    assert dc._seal({"N018": 8, "N007": 2}) == 80.0
    assert dc._seal({}) is None
    assert dc._seal({"N018": 0, "N007": 0}) is None


def test_fetch_zt_shapes(monkeypatch):
    day = "2026-11-02"

    def fake_call(cmd, ctl, params, **kw):
        assert params[0] in ("00101", "00102")
        assert params[3] == day
        blob = json.dumps({"ResultSets": [
            _rs(["N018", "N005", "N002"], [], [[42, 7, 1]])
        ]}).encode("utf-8")
        return [blob]

    monkeypatch.setattr(dc, "call", fake_call)
    zt = dc.fetch_zt(day)
    assert zt["zt"] == 42 and zt["dt"] == 7 and zt["src"] == "tdx"
    zl = dc.fetch_ztlist(day)
    assert zl["count"] == 1 and zl["list"][0]["N018"] == 42


def test_fetch_lr_iterates_subcodes(monkeypatch):
    calls = []
    monkeypatch.setattr(dc, "call",
                        lambda cmd, ctl, params, **kw:
                        calls.append(params) or [])
    out = dc.fetch_lr("2026-10-30")
    assert set(out) >= {"groups", "series", "stocks", "industry"}
    assert len(calls) == 4
    assert [c[0] for c in calls] == ["00101", "00103", "00201", "00301"]


def test_fetch_balance_and_lrzt(monkeypatch):
    blob = json.dumps({"ResultSets": [_rs(["N001"], [], [[1.0]])]}).encode()
    seen = {}

    def fake_call(cmd, ctl, params, **kw):
        seen["params"] = params
        return [blob]

    monkeypatch.setattr(dc, "call", fake_call)
    b = dc.fetch_balance("2026-10-30")
    assert b["count"] == 1
    assert seen["params"][0] == "00104"
    r = dc.fetch_lrzt("2026-10-30")
    assert r["rows"] and seen["params"][0] == "00102"


def test_fetch_hsgt_retries_then_succeeds(monkeypatch):
    seq = {"n": 0}
    blob = json.dumps({"ResultSets": [_rs(["N004"], [], [[12.5]])]}).encode()

    def fake_call(cmd, ctl, params, **kw):
        seq["n"] += 1
        return [blob] if seq["n"] >= 2 else []

    monkeypatch.setattr(dc, "call", fake_call)
    out = dc.fetch_hsgt(4, "2026-10-30")
    assert out["count"] == 1 and seq["n"] == 2


def test_fetch_hsgt_channel_is_p2_not_days(monkeypatch):
    """回归：p2 是通道码（0-5），不是天数。"""
    seen = {}

    def fake_call(cmd, ctl, params, **kw):
        seen["p"] = params
        return []

    monkeypatch.setattr(dc, "call", fake_call)
    dc.fetch_hsgt(channel=2, day="2026-10-30", tries=1)
    assert seen["p"] == ["lszjlx3", "2", ""]


def test_fetch_hot_lhb_rl_ztdd_zdtj_shapes(monkeypatch):
    blob = json.dumps({"ResultSets": [_rs(["a"], [], [[1]])]}).encode()
    seen = []
    monkeypatch.setattr(dc, "call",
                        lambda cmd, ctl, params, **kw: seen.append((cmd, ctl, params)) or [blob])
    day = "2026-11-02"
    assert dc.fetch_hot(day)["count"] == 1
    assert dc.fetch_lhb(day)["count"] == 1
    assert dc.fetch_rl(day)["count"] == 1
    assert dc.fetch_calendar(day)["count"] == 1
    assert dc.fetch_ztdd(day)["count"] == 1
    assert dc.fetch_zdtj(day)["count"] == 1
    cmds = [c[0] for c in seen]
    assert "CWServ.ph_tdxdatacenter_tdxrb_ag_rqb" in cmds
    assert "CWServ.ph_zt_lhb_sy" in cmds
    # 龙虎榜参数按年/月拆
    lhb = [p for c, _t, p in seen if "lhb" in c][0]
    assert lhb[2] == "2026" and lhb[3] == "11"


def test_main_reports_and_saves(monkeypatch, tmp_path, capsys):
    # main() 的 out 目录是 datacenter.py 同级的 out/；用 monkeypatch 改模块内
    # 引用的 os.path.dirname 不可靠（os 是全局模块），改为直接跑真实目录、
    # mock fetch_one 并断言 stdout 报告。
    monkeypatch.setattr(dc, "fetch_one", lambda k: {"count": 3})
    rc = dc.main(["zt"])
    out = capsys.readouterr().out
    assert rc == 0 and "完成" in out


def test_main_unknown_key_skipped(monkeypatch, tmp_path, capsys):
    rc = dc.main(["__nonexistent_key__"])
    out = capsys.readouterr().out
    assert rc == 1 and "完成 0" in out


def test_fetch_one_unknown_key():
    with pytest.raises(KeyError):
        dc.fetch_one("__nope__")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
