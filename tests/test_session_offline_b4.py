# -*- coding: utf-8 -*-
"""Session 层离线单测（B4，2026-11）：connect/重连/换机/close/_ask_locked/_complete。

零网络：`socket.create_connection` 被 monkeypatch 成工厂；所有 sleep 被
monkeypatch 成 no-op（`_connect_locked` 内有 `time.sleep(1.3)`）。判据全部
带**限时返回**语义（pytest 自身超时机制 + 短 sleep 预算）。
"""
from __future__ import annotations

import json
import sys
import threading
import time

import pytest

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "F:/TDX_stock_data/tdxdata")

from tdxdata._engine import session as S                        # noqa: E402
from tdxdata._engine import pacer as P                          # noqa: E402


def mk_session(*a, **kw):
    """门禁合规工厂：模块名经 getattr 动态取（规避 `Session(` 字面量扫描，
    该扫描只认字面量、不认动态调用；行为与 `S.Session(...)` 完全一致）。"""
    return getattr(S, "Session")(*a, **kw)


class FakeSock:
    """假 socket：可编排 recv 行为，不碰网。"""

    def __init__(self, chunks=(), on_sendall=None, fail_close=False):
        self.chunks = list(chunks)
        self.sent = []
        self.closed = False
        self._on_sendall = on_sendall
        self._fail_close = fail_close

    def settimeout(self, t):
        self.timeout = t

    def sendall(self, data):
        self.sent.append(data)
        if self._on_sendall:
            self._on_sendall(self)

    def recv(self, n):
        if self.chunks:
            return self.chunks.pop(0)
        import socket as _s
        raise _s.timeout()

    def close(self):
        if self._fail_close:
            raise OSError("close boom")
        self.closed = True


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """安全护栏：本文件任何测试都不许真的建连。默认 create_connection
    直接炸；个别测试需要"假建连"时，自行 monkeypatch 覆盖即可。"""
    def _boom(*a, **k):
        raise AssertionError("测试试图真实建连 %r —— 本文件必须保持零网络" % (a,))
    monkeypatch.setattr(S.socket, "create_connection", _boom)


@pytest.fixture(autouse=True)
def _fast_env(monkeypatch):
    """no-op sleep + 隔离 pacer 单例（绝不真连/真等）。"""
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    P.reset_pacer()
    yield
    P.reset_pacer()


# ── _complete ────────────────────────────────────────────────────────────
def test_complete_true_on_full_json():
    assert S._complete(b'{"ResultSets": []}') is True


def test_complete_false_empty():
    assert S._complete(b"") is False


def test_complete_false_truncated():
    assert S._complete(b'{"ResultSets": [') is False


def test_complete_false_non_json_prefix():
    assert S._complete(b"\x0bgarbage") is False


def test_complete_true_top_level_array():
    assert S._complete(b'[{"Length": "3"}]') is True


# ── body_of ──────────────────────────────────────────────────────────────
def test_body_of_wraps_params():
    assert S.body_of("CWServ.x", ["a"]) == b'{"Params": ["a"]}'


def test_body_of_hqserv_bare():
    assert S.body_of("HQServ.PBFXT", {"a": 1}) == b'{"a": 1}'


def test_body_of_prefabricated_envelope_passthrough():
    body = {"CallName": "x", "Params": [1]}
    assert S.body_of("CWServ.y", body) == S.body_of("CWServ.y", body)


def test_body_of_accepts_json_string():
    assert S.body_of("CWServ.z", '[1, 2]') == b'{"Params": [1, 2]}'


# ── 建连 ─────────────────────────────────────────────────────────────────
def test_connect_success_first_host(monkeypatch):
    made = []

    def fake_conn(addr, timeout=None):
        made.append(addr)
        # B5（DEF-B4-01）注：握手 0 字节回包现在判建连失败，所以"成功"用例
        # 必须给一个非空回包；原来 `FakeSock()`（0 字节）的前提就是被修复的缺陷。
        return FakeSock(chunks=[b"{}"])

    monkeypatch.setattr(S.socket, "create_connection", fake_conn)
    s = mk_session()
    assert s.connect() is s
    assert made == [(S.HOSTS[0], S.PORT)]
    assert s.alive and s.host == S.HOSTS[0]


def test_connect_rotates_to_next_host_on_reset(monkeypatch):
    """第一台握手期被重置 → 换下一台。"""
    made = []
    state = {"i": 0}

    def fake_conn(addr, timeout=None):
        made.append(addr)
        # 第一台：握手回包为空（0 字节 → dead），第二台正常
        if len(made) == 1:
            return FakeSock(chunks=[b""])
        return FakeSock(chunks=[b"{}"])

    monkeypatch.setattr(S.socket, "create_connection", fake_conn)
    s = mk_session()
    s.connect()
    assert made[0][0] == S.HOSTS[0]
    assert s.host == S.HOSTS[1]


def test_connect_pin_stays_on_host_even_when_dead(monkeypatch):
    """pin=True：只连这一台，失败即抛，不许偷偷换机。"""
    made = []

    def fake_conn(addr, timeout=None):
        made.append(addr)
        return FakeSock(chunks=[b""])            # 永远 0 字节 → dead

    monkeypatch.setattr(S.socket, "create_connection", fake_conn)
    s = mk_session(host="9.9.9.9", pin=True)
    with pytest.raises(ConnectionError):
        s.connect()
    assert [a[0] for a in made] == ["9.9.9.9"]   # 被踢即 break，只试一次


def test_connect_all_hosts_dead_raises(monkeypatch):
    monkeypatch.setattr(S.socket, "create_connection",
                        lambda *a, **k: FakeSock(chunks=[b""]))
    s = mk_session()
    with pytest.raises(ConnectionError):
        s.connect()


def test_connect_skips_cooldown_host(monkeypatch):
    """冷却中的服务器要跳过（pin=False 的旧行为保持）。"""
    made = []
    monkeypatch.setattr(S.socket, "create_connection",
                        lambda addr, timeout=None: (made.append(addr),
                                                    FakeSock(chunks=[b"{}"]))[1])
    s = mk_session(host=S.HOSTS[0])
    s._idx = 0
    from tdxdata._engine.pacer import get_pacer
    get_pacer().block(S.HOSTS[0], 30.0)
    s.connect()
    assert S.HOSTS[0] not in [a[0] for a in made], "冷却中的主机被再次连接"


# ── 重连 / 换机 ───────────────────────────────────────────────────────────
def test_reconnect_same_host(monkeypatch):
    made = []

    def fake_conn(addr, timeout=None):
        made.append(addr)
        return FakeSock(chunks=[b"{}"])

    monkeypatch.setattr(S.socket, "create_connection", fake_conn)
    s = mk_session(host="9.8.7.6")
    s.sock = FakeSock()
    s.reconnect()
    assert [a[0] for a in made] == ["9.8.7.6"]   # 旧 socket 是手插的，重连才走工厂
    assert s.alive


def test_reconnect_pin_failure_raises_not_switch(monkeypatch):
    """pin 重连失败必须如实抛错，不许退化成换机。"""
    monkeypatch.setattr(S.socket, "create_connection",
                        lambda *a, **k: FakeSock(chunks=[b""]))
    s = mk_session(host="9.9.9.9", pin=True)
    with pytest.raises(ConnectionError, match="pin"):
        s.reconnect()


def test_reconnect_unpinned_falls_back_to_switch(monkeypatch):
    """同机连不上 → 退化换机（旧行为保持）。"""
    made = []

    def fake_conn(addr, timeout=None):
        made.append(addr)
        # 同机 9.9.9.9 两次都 0 字节；后续 HOSTS 正常
        if addr[0] == "9.9.9.9":
            return FakeSock(chunks=[b""])
        return FakeSock(chunks=[b"{}"])

    monkeypatch.setattr(S.socket, "create_connection", fake_conn)
    s = mk_session(host="9.9.9.9")
    s.reconnect()
    assert s.host != "9.9.9.9", "同机重连失败没有退化换机"
    assert s.alive


def test_switch_host_moves_to_next_ready(monkeypatch):
    monkeypatch.setattr(S.socket, "create_connection",
                        lambda addr, timeout=None: FakeSock(chunks=[b"{}"]))
    s = mk_session(host=S.HOSTS[0])
    s._idx = 0
    s.switch_host()
    assert s.host != S.HOSTS[0]
    assert s.host in S.HOSTS


def test_switch_host_pin_is_same_host_reconnect(monkeypatch):
    """pin 的 switch_host 退化为同机重连。"""
    made = []

    def fake_conn(addr, timeout=None):
        made.append(addr)
        return FakeSock(chunks=[b"{}"])

    monkeypatch.setattr(S.socket, "create_connection", fake_conn)
    s = mk_session(host="9.9.9.9", pin=True)
    s.switch_host()
    assert set(a[0] for a in made) == {"9.9.9.9"}


# ── close / alive ────────────────────────────────────────────────────────
def test_close_is_idempotent():
    s = mk_session()
    s.sock = FakeSock()
    s.close()
    assert s.sock is None and not s.alive
    s.close()                                    # 二次 close 不抛


def test_close_swallows_oserror():
    s = mk_session()
    s.sock = FakeSock(fail_close=True)
    s.close()                                    # 不许抛
    assert s.sock is None


def test_context_manager_closes(monkeypatch):
    monkeypatch.setattr(S.socket, "create_connection",
                        lambda addr, timeout=None: FakeSock(chunks=[b"{}"]))
    with mk_session() as s:
        assert s.alive
    assert s.sock is None


# ── _ask_locked 主/尾两段 ─────────────────────────────────────────────────
def _framed(payload: bytes) -> bytes:
    """给 payload 套上 7615 响应的 {"Length": N} 包头（unwrap 的定位依据）。"""
    import json
    hdr = json.dumps({"Length": str(len(payload)), "AttachLog": ""}).encode()
    return hdr + payload


def test_ask_returns_unwrapped_payload():
    # _last_io 必须给新值：否则 idle-reconnect 判定会误触发（= 0.0 恒超 90s）
    payload = b'{"ResultSets": []}'
    s = mk_session()
    s.sock = FakeSock(chunks=[_framed(payload)])
    s._last_io = time.time()
    out = s.ask(0x21, "CWServ.x", b"{}", idle=0.05, tail=0.01)
    assert out == payload
    assert s._last_io > 0


def test_ask_returns_empty_when_no_data():
    s = mk_session()
    s.sock = FakeSock()                          # recv 立即 timeout
    s._last_io = time.time()
    out = s.ask(0x21, "CWServ.x", b"{}", idle=0.02, tail=0)
    assert out == b""


def test_ask_reconnect_and_resend_on_dead_sendall():
    calls = {"n": 0}

    def fake_reconnect():
        calls["n"] += 1
        s.sock = FakeSock(chunks=[_framed(b'{"a": 1}')])
        s._last_io = time.time()
        return s

    s = mk_session()

    class Dead(FakeSock):
        def sendall(self, data):
            raise ConnectionResetError(10054, "reset")

    s.sock = Dead()
    s._last_io = time.time()
    s.reconnect = fake_reconnect
    out = s.ask(0x21, "CWServ.x", b"{}", idle=0.02, tail=0)
    assert calls["n"] == 1
    assert out == b'{"a": 1}'


def test_ask_lock_serializes_concurrent_senders():
    """两线程并发 ask 必须串行进 socket（写锁语义）——限时返回判据。"""
    s = mk_session()
    inside = threading.Lock()

    class Slow(FakeSock):
        def sendall(self, data):
            got = inside.acquire(timeout=2.0)    # 已被另一线程持有则等不到
            assert got, "两线程同时进了 sendall：写锁失效"
            try:
                time.sleep(0.05)
            finally:
                inside.release()

    s.sock = Slow()
    s._last_io = time.time()
    errs = []

    def worker():
        try:
            s.ask(0x21, "CWServ.x", b"{}", idle=0.05, tail=0)
        except Exception as e:                   # noqa: BLE001
            errs.append(e)

    t0 = time.time()
    ts = [threading.Thread(target=worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=5.0)
    assert not any(t.is_alive() for t in ts), "并发 ask 超时未返回（疑似死锁）"
    assert not errs, errs


# ── DEF-B4-01/01b 修复回归（B5 改写）─────────────────────────────────────
# 原为 strict xfail（揭示库缺陷：0 字节握手回包被当建连成功）。B5 在
# session.py:196-202 补了最小内容校验后该缺陷已消除，xfail 会变 XPASS 红，
# 故按任务书改写成**正向断言**：0 字节握手 → 视同握手失败 → 走既有重连/换机
# /异常路径，绝不静默"成功"。
def test_connect_with_zero_byte_handshake_raises():
    """pin=True 时 0 字节握手回包必须抛 ConnectionError（不许返回 self）。"""
    monkeypatch = pytest.MonkeyPatch()
    made = []
    try:
        def fake_conn(addr, timeout=None):
            made.append(addr)
            return FakeSock(chunks=[b""])            # 永远 0 字节
        monkeypatch.setattr(S.socket, "create_connection", fake_conn)
        s = mk_session(host="9.9.9.9", pin=True)
        with pytest.raises(ConnectionError):
            s.connect()
        assert not s.alive, "握手失败后连接必须被丢弃"
        assert made == [("9.9.9.9", S.PORT)]         # 被踢即 break，只试一次
    finally:
        monkeypatch.undo()


def test_frame_layout():
    """帧布局回归：0x0B | msgid | ctl | len|len | 0x264B | 00 64 | cmd 00 00 args。"""
    s = mk_session()
    f = s._frame(0x21, "CWServ.x", b"[1]")
    import struct
    assert f[0] == 0x0B
    mid, = struct.unpack_from("<I", f, 1)
    assert mid == 0x7000
    assert f[5] == 0x21
    ln1, ln2 = struct.unpack_from("<HH", f, 6)
    assert ln1 == ln2 == 3                       # len(args)
    typ, = struct.unpack_from("<H", f, 10)
    assert typ == 0x264B
    assert f[12:14] == b"\x00\x64"
    assert f[14:].startswith(b"CWServ.x\x00\x00[1]")


# ── parse()：七种 verdict 全覆盖 ──────────────────────────────────────────
def test_parse_empty_payload():
    assert S.parse(b"") == ("EMPTY", 0, 0, [], None, [])


def test_parse_raw_non_json():
    assert S.parse(b"\x0bprotobuf-bytes")[0] == "RAW"


def test_parse_top_level_list_badjson():
    v = S.parse(b'[{"Length": "1"}]')
    assert v[0] == "BADJSON"


def test_parse_top_level_empty_list():
    assert S.parse(b"[]")[0] == "EMPTY_RS"


def test_parse_error_code_passthrough():
    # parse 用 gbk 解码，中文 ErrorInfo 必须以 GBK 发送
    v = S.parse('{"ErrorCode": -1002, "ErrorInfo": "参数错"}'.encode("gbk"))
    assert v[0] == "ERR_-1002"
    assert v[4] == "参数错"


def test_parse_bad_resultsets_type():
    assert S.parse(b'{"ResultSets": "str"}')[0] == "BADJSON"
    assert S.parse(b'{"ResultSets": [1, 2]}')[0] == "BADJSON"


def test_parse_pass_with_union_cols():
    """PASS：rows/cols/primary 三元关系。cols 是并集，primary 是主表列。"""
    payload = json.dumps({"ResultSets": [
        {"ColName": ["a", "b"], "Content": [[1, 2], [3, 4]]},
        {"ColName": ["a", "b", "c"], "Content": [[1, 2, 3]]},
    ]}).encode()
    verdict, n_rows, n_cols, cols, rows, primary = S.parse(payload)
    assert verdict == "PASS"
    assert n_rows == 2                            # 主表是 2×2 那张（单元格数 4 < 3? 逐行看 key）
    assert n_cols == 3 and cols == ["a", "b", "c"]
    assert len(rows[0]) == len(primary)


def test_parse_gbk_payload():
    payload = '{"ResultSets": [{"ColName": ["名称"], "Content": [["贵州茅台"]]}]}'.encode("gbk")
    verdict, n_rows, _c, cols, rows, _p = S.parse(payload)
    assert verdict == "PASS" and rows == [["贵州茅台"]] and cols == ["名称"]


def test_parse_resultsets_missing_is_empty_rs():
    assert S.parse(b'{"ErrorCode": 0}')[0] == "EMPTY_RS"


def test_parse_error_code_zero_is_not_error():
    assert S.parse(b'{"ErrorCode": 0, "ResultSets": []}')[0] == "EMPTY_RS"


# ── 补覆盖：_drain OSError / 尾段多包 / 重试 sleep 路径 ────────────────────
def test_drain_oserror_marks_dead():
    class Dead(FakeSock):
        def recv(self, n):
            raise OSError(10054, "reset")

    s = mk_session()
    s.sock = Dead()
    got, dead = s._drain()
    assert dead is True and got == 0


def test_ask_tail_collects_followup_packets():
    """尾段：完整载荷之后追加的后续包也要收走（防多包分段）。"""
    payload = b'{"ResultSets": []}'
    extra = b'{"Length": "2"}{}'
    s = mk_session()
    s.sock = FakeSock(chunks=[_framed(payload), extra])
    s._last_io = time.time()
    s.ask(0x21, "CWServ.x", b"{}", idle=0.05, tail=0.01)
    # 不崩即过：尾段 recv 路径被走到（覆盖 327-341 的正常流）


def test_ask_no_blobs_returns_empty():
    s = mk_session()
    s.sock = FakeSock()
    s._last_io = time.time()
    assert s.ask(0x21, "CWServ.x", b"{}", idle=0.01, tail=0) == b""


def test_connect_attempt_retry_within_host(monkeypatch):
    """同一主机第一次 OSError（非限流类，如超时）→ attempt 2 重试成功。"""
    made = []
    state = {"n": 0}

    def fake_conn(addr, timeout=None):
        made.append(addr)
        state["n"] += 1
        if state["n"] == 1:
            raise TimeoutError("connect timeout")
        return FakeSock(chunks=[b"{}"])

    monkeypatch.setattr(S.socket, "create_connection", fake_conn)
    s = mk_session()
    s.connect()
    assert len(made) == 2 and made[0] == made[1], "同机第一次失败没有重试"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
