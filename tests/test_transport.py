# -*- coding: utf-8 -*-
"""D2 回归：PB 传输层（`datacenter._send`）的**离线**行为。

为什么用假 socket 而不是真机
--------------------------
D2 改的正是"每次调用都握手 + 空等 7 秒"这段时序。真机验证（耗时、数值逐位比对）
只能手动跑；但**连接复用/重连/失败重试/空回包丢弃**这些逻辑必须能进 CI，
否则下次谁一动就悄悄退化回 8.35 秒，而门禁看不出来。

所以这里塞一个 `FakeSock` 替掉 `socket.create_connection`，把"第几次 recv 返回什么"
写成脚本，完全不碰网络。

背景数字（2026-10-26 实测，600519 日线）：
    改前 8.35s/次  →  改后 首次 1.79s / 稳态 2.19s（被 pacer 的 2.2s 地板兜住）

跑法：
    python tests/test_transport.py
    python -m pytest tests/test_transport.py -q
"""
from __future__ import annotations

import os
import socket
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tdxdata._engine import datacenter as dc                       # noqa: E402
from tdxdata._engine import pacer as P                            # noqa: E402


# ── 假 socket：把 recv 的返回写成一个脚本队列 ─────────────────────────────
def _res(payload: bytes) -> bytes:
    """按协议拼一个「头 + 包体」，`unwrap` 能切出来。"""
    return b'{"Length": "%d"}' % len(payload) + payload


class FakeSock:
    def __init__(self, script, *, fail_on_sendall=None):
        self.script = list(script)
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
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item()          # 容忍脚本里写异常"类"而不是实例
        return item

    def close(self):
        self.closed = True


class Harness:
    """打桩 `create_connection` / `fresh_handshake` / `sleep` / `pacer.wait`。"""

    def __init__(self, socks):
        self.made = []
        self._socks = list(socks)
        self._real = {}

    def __enter__(self):
        self._real["cc"] = dc.socket.create_connection
        self._real["hs"] = dc.fresh_handshake
        self._real["wait"] = P.Pacer.wait
        self._real["sleep"] = time.sleep
        self.waits = []

        def _cc(addr, timeout=None):
            s = self._socks.pop(0) if self._socks else FakeSock([])
            self.made.append(s)
            return s

        dc.socket.create_connection = _cc
        dc.fresh_handshake = lambda *a, **k: b"FAKE-HANDSHAKE"
        P.Pacer.wait = lambda self: (self and 0) or 0.0     # 不睡
        time.sleep = lambda *a, **k: None                   # 不睡 1.3s
        dc._drop_pb_conn()
        return self

    def __exit__(self, *exc):
        dc.socket.create_connection = self._real["cc"]
        dc.fresh_handshake = self._real["hs"]
        P.Pacer.wait = self._real["wait"]
        time.sleep = self._real["sleep"]
        dc._drop_pb_conn()
        return False


TIMEOUT = socket.timeout()       # 用**实例**：类对象会被当成"正常字节"返回


# ══════════════════════════════════════════════════════════════════════════
# 一、闸门：每次 `_send` 恰好过一次（PB 路径原来一次都不过）
# ══════════════════════════════════════════════════════════════════════════
def test_send_goes_through_pacer_exactly_once():
    payload = b'{"N001": "1.5"}'
    sock = FakeSock([TIMEOUT, _res(payload), TIMEOUT])
    real_wait = P.Pacer.wait
    hits = []

    def spy(self):
        hits.append(1)
        return 0.0

    with Harness([sock]) as h:
        P.Pacer.wait = spy
        try:
            blobs = dc._send(b"SOMEFRAME")
        finally:
            P.Pacer.wait = real_wait
    assert blobs == [payload], blobs
    assert len(hits) == 1, "一次发送必须只过一次闸门，实际 %d" % len(hits)
    assert h.made and h.made[0].sent[0] == b"FAKE-HANDSHAKE"


def test_send_unwraps_and_returns_payload_list():
    payload = b'{"N001": "1.5", "N002": "2"}'
    with Harness([FakeSock([TIMEOUT, _res(payload), TIMEOUT])]):
        blobs = dc._send(b"F")
    assert blobs == [payload]


# ══════════════════════════════════════════════════════════════════════════
# 二、连接复用（阶段 B 的核心收益：握手只付一次）
# ══════════════════════════════════════════════════════════════════════════
def test_two_sends_reuse_one_tcp_connection():
    """**两次 PB 调用只建 1 条 TCP、只握手 1 次**（原来是每次都建）。"""
    p1, p2 = b'{"a": "1"}', b'{"b": "2"}'
    sock = FakeSock([TIMEOUT, _res(p1), TIMEOUT, _res(p2), TIMEOUT])
    with Harness([sock]) as h:
        assert dc._send(b"F1") == [p1]
        assert dc._send(b"F2") == [p2]
        assert not sock.closed, "复用期间连接不该被关"
        assert dc._CONN["sock"] is sock
    assert len(h.made) == 1, "应当复用同一条连接，实际建了 %d 条" % len(h.made)
    hs = [x for x in sock.sent if x == b"FAKE-HANDSHAKE"]
    assert len(hs) == 1, "握手应当只做 1 次，实际 %d 次" % len(hs)
    assert sock.sent == [b"FAKE-HANDSHAKE", b"F1", b"F2"], sock.sent


def test_reconnect_after_idle_timeout():
    """空闲超过 `_PB_IDLE_RECONNECT` → 必须重连（服务端会静默重置空闲连接）。"""
    p1, p2 = b'{"a": "1"}', b'{"b": "2"}'
    with Harness([FakeSock([TIMEOUT, _res(p1), TIMEOUT]),
                  FakeSock([TIMEOUT, _res(p2), TIMEOUT])]) as h:
        dc._send(b"F1")
        dc._CONN["t"] = time.time() - (dc._PB_IDLE_RECONNECT + 10)
        dc._send(b"F2")
    assert len(h.made) == 2, "空闲超时后应当重连，实际建了 %d 条" % len(h.made)
    assert h.made[0].closed, "旧连接要被关掉，别泄漏"


def test_drain_and_tail_timeouts_are_the_fast_ones():
    """`drain`/`tail` 必须是小值（0.25/0.15）——这两个是 8.35s 里的主要浪费。"""
    sock = FakeSock([TIMEOUT, _res(b'{"a": "1"}'), TIMEOUT])
    with Harness([sock]):
        dc._send(b"F", idle=2.0)
    # 顺序：drain(0.25) → idle(2.0) → tail(0.15)
    assert sock.timeouts[0] == 0.25, sock.timeouts
    assert sock.timeouts[1] == 2.0, sock.timeouts
    assert 0.1 <= sock.timeouts[2] <= 0.3, sock.timeouts


def test_send_defaults_regressed_to_old_slow_values():
    """守卫默认值：别有人把 `idle` 改回 5.0 / 去掉 `tail`。"""
    import inspect
    sig = inspect.signature(dc._send)
    assert sig.parameters["wait"].default <= 15.0
    assert sig.parameters["idle"].default <= 2.0
    assert sig.parameters["drain"].default <= 0.3
    assert sig.parameters["tail"].default <= 0.2


# ══════════════════════════════════════════════════════════════════════════
# 三、失败处理
# ══════════════════════════════════════════════════════════════════════════
def test_empty_response_is_an_error_not_an_empty_result():
    """空回包必须**上抛**并丢掉连接 —— 不许返回 `[]`。

    协议里"无数据"是"合法但 0 行的表"（`EmptyResult`），空字节只可能是连接被掐（实测 `10053`）。
    两者混在一起，调用方就分不清"今天没成交"和"被服务端踢了"——正是本项目的红线。
    """
    sock = FakeSock([TIMEOUT, TIMEOUT])            # 排空超时 + 主循环也超时
    with Harness([sock]):
        try:
            dc._send(b"F")
        except ConnectionError:
            pass
        else:
            raise AssertionError("空回包必须抛 ConnectionError")
        assert dc._CONN["sock"] is None, "空回包后必须丢弃连接"
    assert sock.closed


def test_empty_response_retries_with_a_fresh_connection():
    """第一次空回包 → 换一条连接重试 → 第二次有数据就正常返回。"""
    empty = FakeSock([TIMEOUT, TIMEOUT])
    good = FakeSock([TIMEOUT, _res(b'{"ok": "2"}'), TIMEOUT])
    with Harness([empty, good]) as h:
        blobs = dc._send(b"F")
    assert blobs == [b'{"ok": "2"}'], blobs
    assert len(h.made) == 2, "空回包后应当换连接重试，实际建了 %d 条" % len(h.made)


def test_connection_error_retries_once_with_new_connection():
    """`sendall` 撞 `10053` → 丢弃并**换一条连接重试 1 次**（真机上这是最常见的失败）。"""
    bad = FakeSock([], fail_on_sendall=ConnectionAbortedError(10053, "aborted"))
    good = FakeSock([TIMEOUT, _res(b'{"ok": "1"}'), TIMEOUT])
    with Harness([bad, good]) as h:
        blobs = dc._send(b"F")
    assert blobs == [b'{"ok": "1"}'], blobs
    assert len(h.made) == 2, "应当重建 1 次，实际建了 %d 条" % len(h.made)
    assert bad.closed


def test_connection_error_twice_raises():
    """两次都失败要**上抛**，不许假装成功返回 None/[]。"""
    b1 = FakeSock([], fail_on_sendall=ConnectionAbortedError(10053, "aborted"))
    b2 = FakeSock([], fail_on_sendall=ConnectionAbortedError(10053, "aborted"))
    with Harness([b1, b2]):
        try:
            dc._send(b"F")
        except OSError:
            pass
        else:
            raise AssertionError("两次连接失败必须抛 OSError")


def test_close_pb_is_idempotent():
    """`close_pb()` 可以反复调（`Client.close()` 和 `atexit` 都会碰到）。"""
    sock = FakeSock([TIMEOUT, _res(b'{"a": "1"}'), TIMEOUT])
    with Harness([sock]):
        dc._send(b"F")
        assert dc._CONN["sock"] is sock
        dc.close_pb()
        dc.close_pb()
        assert dc._CONN["sock"] is None and sock.closed


# ══════════════════════════════════════════════════════════════════════════
# 四、退避：最后一轮之后的 8 秒是纯浪费（D6，2026-10-27）
# ══════════════════════════════════════════════════════════════════════════
def test_raw_send_does_not_sleep_after_the_last_attempt():
    """`tries=4` 时只能睡 **3** 次；第 4 次失败后再睡 8 秒没有任何意义。

    实测依据：`113711` 当年 33~35 秒的等待里，有 8 秒就是这记多余的 sleep。
    """
    from tdxdata._engine import pb_api

    slept = []
    real_send, real_time = pb_api.dc._send, pb_api.time
    pb_api.dc._send = lambda *a, **k: []            # 永远"没有包体"
    pb_api.time = type("T", (), {"sleep": staticmethod(lambda s: slept.append(s))})()
    try:
        assert pb_api._raw_send(0x0E, "HQServ.PBFXT", b"x", tries=4) is None
    finally:
        pb_api.dc._send = real_send
        pb_api.time = real_time
    assert len(slept) == 3, "4 次尝试只该睡 3 次，实际 %d 次" % len(slept)
    assert slept == [8, 8, 8], slept


def test_raw_send_tries_once_is_zero_sleep():
    """`tries=1`（不重试）时一次都不该睡。"""
    from tdxdata._engine import pb_api

    slept = []
    real_send, real_time = pb_api.dc._send, pb_api.time
    pb_api.dc._send = lambda *a, **k: []
    pb_api.time = type("T", (), {"sleep": staticmethod(lambda s: slept.append(s))})()
    try:
        assert pb_api._raw_send(0x0E, "HQServ.PBFXT", b"x", tries=1) is None
    finally:
        pb_api.dc._send = real_send
        pb_api.time = real_time
    assert slept == [], slept


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
