# -*- coding: utf-8 -*-
r"""ARCH-001：Transport 接口 + ReplayTransport 的离线契约测试。

覆盖（报告 §6.3/§6.4 步骤 1+2）：
  ① Transport 协议形状：`call(cmd, ctl, body, *, idle) -> list[bytes]`、
     四类异常契约、空回包上抛不许返回 []。
  ② SessionTransport：包 `Session.ask_blobs`，异常归一化（裸 OSError → 四类）。
  ③ `Session.ask_blobs` 新接缝：包边界保留（多包不再拍平）；`ask` 旧行为不变。
  ④ `dc._send(conn=…)` 实例化缝：实例通道与全局 `_CONN` 互不串扰。
  ⑤ ReplayTransport：读真实 golden 帧表回放、帧键口径与 golden_runner 对账、
     FrameMissing 不被重试吞掉。
零网络（socket.create_connection 全程被封死）。
"""
from __future__ import annotations

import os
import socket
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest                                                    # noqa: E402

import tdxdata                                                   # noqa: E402
from tdxdata import errors                                       # noqa: E402
from tdxdata._engine import datacenter as dc                     # noqa: E402
from tdxdata._engine import pacer as P                           # noqa: E402
from tdxdata._engine import session as S                         # noqa: E402
from tdxdata._engine import transport as T                       # noqa: E402
import transport_replay as R                                     # noqa: E402

# 项目纪律：测试里不写 Client( / Session( / AsyncClient( 字面量（门禁第 7 条）
_S = getattr(S, "Session")
_T = T.SessionTransport
_R = R.ReplayTransport
_CH = R.PBChannel


# ── 复用 test_transport 的假 socket 基建（本文件独立实现，避免互 import）──
def _res(payload: bytes) -> bytes:
    return b'{"Length": "%d"}' % len(payload) + payload


class FakeSock:
    def __init__(self, script=None, *, fail_on_sendall=None):
        self.script = list(script or [])
        self.sent = []
        self.timeouts = []
        self.closed = False
        self.fail_on_sendall = fail_on_sendall

    def sendall(self, data):
        if self.fail_on_sendall is not None:
            raise self.fail_on_sendall
        self.sent.append(data)

    def settimeout(self, t):
        self.timeouts.append(t)

    def recv(self, n):
        if not self.script:
            raise socket.timeout()
        item = self.script.pop(0)
        if item is TIMEOUT:               # 标记：这一拍超时（不进数据）
            raise socket.timeout()
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


TIMEOUT = socket.timeout()


class Harness:
    """封死真网 + 打桩建连/握手/sleep/pacer（口径同 test_transport.Harness）。"""

    def __init__(self, socks):
        self.made = []
        self._socks = list(socks)
        self._real = {}

    def __enter__(self):
        self._real["cc"] = dc.socket.create_connection
        self._real["hs"] = dc.fresh_handshake
        self._real["wait"] = P.Pacer.wait
        self._real["sleep"] = time.sleep

        def _cc(addr, timeout=None):
            s = self._socks.pop(0) if self._socks else FakeSock([])
            self.made.append(s)
            return s

        dc.socket.create_connection = _cc
        dc.fresh_handshake = lambda *a, **k: b"FAKE-HANDSHAKE"
        P.Pacer.wait = lambda self_: 0.0
        time.sleep = lambda *a, **k: None
        dc._drop_pb_conn()
        return self

    def __exit__(self, *exc):
        dc.socket.create_connection = self._real["cc"]
        dc.fresh_handshake = self._real["hs"]
        P.Pacer.wait = self._real["wait"]
        time.sleep = self._real["sleep"]
        dc._drop_pb_conn()
        return False


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", _boom, raising=True)


def _boom(*a, **k):
    raise AssertionError("测试试图真实建连")


def _bare_session():
    s = _S.__new__(_S)
    s.host = "127.0.0.1"
    s.timeout = 5.0
    s.sock = None
    s._idx = 0
    s._last_io = 0.0
    s._lock = __import__("threading").RLock()
    s.idle_reconnect = 0.0            # 关掉空闲重连（_last_io=0 会误触发）
    s.pin = False
    return s


# ══════════════════════════════════════════════════════════════════════════
# 一、Transport 协议形状
# ══════════════════════════════════════════════════════════════════════════
def test_transport_protocol_is_runtime_checkable_and_satisfied():
    """`Transport` 是 runtime Protocol；两个适配器都 isinstance 通过。"""
    assert isinstance(_T(_bare_session()), T.Transport)
    assert isinstance(T.PBTransport(), T.Transport)


def test_transport_returns_blob_list_and_never_joins():
    """契约 ①：返回 list[bytes]，多包不许 join 拍平。"""
    sess = _bare_session()
    # 首拍即数据（真机时序：响应 0.18s 就到，首拍超时只会发生在「没数据」场景）
    sess.sock = FakeSock([_res(b'{"a": 1}') + _res(b'{"b": 2}'), TIMEOUT])
    sess._last_io = time.time()
    t = _T(sess)
    out = t.call("CWServ.x", 0x21, b"{}", idle=0.05)
    assert isinstance(out, list) and all(isinstance(b, bytes) for b in out)
    assert out == [b'{"a": 1}', b'{"b": 2}'], out     # 两包都留、边界还在


def test_transport_empty_reply_raises_connection_failed_not_empty_list():
    """契约 ④：空回包必须上抛 ConnectionFailed，绝不返回 []。"""
    sess = _bare_session()
    sess.sock = FakeSock([])          # recv 立即 timeout，无任何包
    sess._last_io = time.time()
    t = _T(sess)
    with pytest.raises(errors.ConnectionFailed):
        t.call("CWServ.x", 0x21, b"{}", idle=0.02)


@pytest.mark.parametrize("exc, want", [
    (socket.timeout("t"), errors.Timeout),
    (ConnectionResetError(10054, "r"), errors.RateLimited),
    (ConnectionAbortedError(10053, "a"), errors.RateLimited),
    (ConnectionRefusedError(111, "x"), errors.ConnectionFailed),
])
def test_transport_normalizes_socket_errors_into_the_four_types(exc, want):
    """契约 ②：裸 socket 域异常归一化成四类（用户的 TdxError 必须接得住）。"""
    sess = _bare_session()

    class Dead(FakeSock):
        def sendall(self, data):
            raise exc

    sess.sock = Dead()
    sess._last_io = time.time()
    sess.reconnect = lambda *a, **k: (_ for _ in ()).throw(exc)
    t = _T(sess)
    with pytest.raises(want):
        t.call("CWServ.x", 0x21, b"{}", idle=0.02)


def test_transport_does_not_swallow_non_transport_exceptions():
    """非传输类异常（ValueError 等）原样冒泡，不许被归一化吞掉重分类。"""
    sess = _bare_session()
    sess.ask_blobs = lambda *a, **k: (_ for _ in ()).throw(ValueError("bug"))
    t = _T(sess)
    with pytest.raises(ValueError):
        t.call("CWServ.x", 0x21, b"{}", idle=0.02)


# ══════════════════════════════════════════════════════════════════════════
# 二、Session.ask_blobs 新接缝（ask 旧行为不变）
# ══════════════════════════════════════════════════════════════════════════
def _framed(payload: bytes) -> bytes:
    hdr = '{"Length": "%d", "AttachLog": ""}' % len(payload)
    return hdr.encode("gbk") + payload


def test_ask_old_contract_unchanged_joined_bytes():
    """回归锚：`ask` 仍返回拍平的 bytes（既有判据一个不动）。"""
    payload = b'{"ResultSets": []}'
    s = _bare_session()
    s.sock = FakeSock([_framed(payload)])
    s._last_io = time.time()
    assert s.ask(0x21, "CWServ.x", b"{}", idle=0.05, tail=0) == payload


def test_ask_blobs_preserves_packet_boundaries():
    """新接缝：两个独立帧 → ask_blobs 返回两个元素（边界保留）。"""
    s = _bare_session()
    s.sock = FakeSock([_framed(b'{"a": 1}'), _framed(b'{"b": 2}')])
    s._last_io = time.time()
    out = s.ask_blobs(0x21, "CWServ.x", b"{}", idle=0.05, tail=0.01)
    assert len(out) == 2, out
    assert out[0] == b'{"a": 1}' and out[1] == b'{"b": 2}'


def test_ask_blobs_empty_returns_empty_list_not_bytes():
    s = _bare_session()
    s.sock = FakeSock()
    s._last_io = time.time()
    assert s.ask_blobs(0x21, "CWServ.x", b"{}", idle=0.02, tail=0) == []


# ══════════════════════════════════════════════════════════════════════════
# 三、dc._send(conn=…) 实例化缝
# ══════════════════════════════════════════════════════════════════════════
def test_send_with_explicit_channel_isolates_state_from_global_conn():
    """实例通道：连接记在通道里，全局 `_CONN` 一个 sock 都不落。

    假 socket 用「一条长脚本喂两次调用」（同 test_transport 的口径）：
    脚本耗尽的 recv 会立刻抛 timeout（真 socket 只是等），所以耗尽即换。
    """
    p1, p2 = b'{"a": "1"}', b'{"b": "2"}'
    ch = _CH()
    with Harness([FakeSock([TIMEOUT, _res(p1), TIMEOUT,
                            _res(p2), TIMEOUT])]) as h:
        t = T.PBTransport(conn=ch)
        assert t.call("HQServ.PBFXT", 0x0E, b"x", idle=0.05) == [p1]
        assert ch.alive, "连接应记在实例通道上"
        assert dc._CONN["sock"] is None, "全局 _CONN 不该被实例通道写脏"
        assert t.call("HQServ.PBFXT", 0x0E, b"y", idle=0.05) == [p2]
        assert len(h.made) == 1, "同一条通道应复用连接"
        sock = h.made[0]
    # Harness 退出只清全局；实例通道要自己关（实例生命周期 = 实例自己的责任）
    ch.close()
    assert not ch.alive and sock.closed, "通道 close() 必须关掉自己的连接"


def test_send_without_conn_keeps_global_singleton_behavior():
    """回归锚：不传 conn 时旧行为逐位不变（连接仍在全局 _CONN）。"""
    with Harness([FakeSock([TIMEOUT, _res(b'{"a": "1"}'), TIMEOUT])]):
        dc._send(b"F", idle=0.05)
        assert dc._CONN["sock"] is not None


def test_two_channels_do_not_share_connections():
    """两条实例通道各建各的连接（实例化的意义所在）。"""
    ch1, ch2 = _CH(), _CH()
    with Harness([FakeSock([TIMEOUT, _res(b'{"a": "1"}'), TIMEOUT]),
                  FakeSock([TIMEOUT, _res(b'{"b": "2"}'), TIMEOUT])]) as h:
        T.PBTransport(conn=ch1).call("HQServ.PBFXT", 0x0E, b"x", idle=0.05)
        T.PBTransport(conn=ch2).call("HQServ.PBFXT", 0x0E, b"x", idle=0.05)
        assert len(h.made) == 2
        assert h.made[0] is not h.made[1]
    ch1.close()
    ch2.close()


# ══════════════════════════════════════════════════════════════════════════
# 四、ReplayTransport（golden 帧表）
# ══════════════════════════════════════════════════════════════════════════
def test_replay_transport_reads_real_golden_manifest():
    """读真实 manifest：68 帧、键齐全、查表命中（路径口径与 runner 一致）。"""
    t = _R()
    assert len(t.table) >= 60, len(t.table)
    # 任取一条 send 帧验证能查回来（send 口径：整帧 args 进）
    ent = next(e for e in t.entries if e["transport"] == "send" and e["raw"])
    out = t.call_send(ent["cmd"], ent["ctl"], ent["args"])
    assert isinstance(out, list) and out, ent["file"]


def test_replay_transport_frame_key_matches_golden_runner():
    """口径对账：本文件 frame_key 必须与 golden_runner.frame_key 逐字节一致。"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import golden_runner as gr
    finally:
        sys.path.pop(0)
    args = b'{"Params": ["001", "1"]}'
    assert R.frame_key("ask", "CWServ.x", 0x21, args) \
        == gr.frame_key("ask", "CWServ.x", 0x21, args)
    # parse_wire_frame 也必须同口径
    from tdxdata._engine.transport import frame_ctl_cmd_args
    frame = b"\x0b" + (0x7000).to_bytes(4, "little") + bytes([0x0E]) \
        + (3).to_bytes(2, "little") + (3).to_bytes(2, "little") \
        + (0x264B).to_bytes(2, "little") + b"\x00\x64" \
        + b"HQServ.PBFXT\x00\x00arg"
    assert gr.parse_wire_frame(frame) == frame_ctl_cmd_args(frame)


def test_replay_transport_with_minimal_injected_table():
    """loader 注入最小帧表（不落盘）：call 按 send 口径返回录下的 blobs。"""
    def loader():
        return [{"transport": "send", "cmd": "HQServ.PBFXT", "ctl": 0x0E,
                 "args": b"ARGS", "raw": b"", "blobs": [b"\x0a\x03abc"],
                 "file": "fake.bin", "seq": 1}]

    t = _R(loader=loader)
    assert t.call("HQServ.PBFXT", 0x0E, b"ARGS") == [b"\x0a\x03abc"]


def test_replay_frame_missing_is_baseexception_and_not_swallowed():
    """帧缺失必须炸穿（BaseException）：不许被 pb_api 的 except Exception 重试吞掉。"""
    t = _R(loader=lambda: [])
    with pytest.raises(R.FrameMissing):
        t.call("HQServ.Nope", 0x0E, b"x")
    assert len(t.misses) == 1


def test_replay_transport_is_a_transport():
    assert isinstance(_R(loader=lambda: []), T.Transport)


# ══════════════════════════════════════════════════════════════════════════
# 五、回放注缝桥：ReplayTransport ↔ pb_api._raw_send（不改断言语义）
# ══════════════════════════════════════════════════════════════════════════
def test_replay_transport_feeds_pb_api_raw_send():
    """ReplayTransport 通过 `_raw_send` 注入缝供 pb_api 消费（接线预演）。"""
    from tdxdata._engine import pb_api

    def loader():
        return [{"transport": "send", "cmd": "HQServ.PBFXT", "ctl": 0x0E,
                 "args": b"Q", "raw": b"", "blobs": [b"BODY"],
                 "file": "fake.bin", "seq": 1}]

    t = _R(loader=loader)
    real = pb_api._raw_send
    pb_api._raw_send = lambda ctl, cmd, args, tries=2, backoff=3.0: \
        t.call_send(cmd, ctl, args)[0]
    try:
        assert pb_api._raw_send(0x0E, "HQServ.PBFXT", b"Q") == b"BODY"
    finally:
        pb_api._raw_send = real


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print("  ✅ %s" % name)
        except AssertionError as e:
            bad += 1
            print("  ❌ %s: %s" % (name, e))
        except Exception as e:                                     # noqa: BLE001
            bad += 1
            print("  ❌ %s: %s: %s" % (name, type(e).__name__, e))
    print("\n%d 个用例，失败 %d" % (len(fns), bad))
    sys.exit(1 if bad else 0)
