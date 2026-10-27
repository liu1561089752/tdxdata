# -*- coding: utf-8 -*-
"""空闲重连的离线单测（不连服务器）。"""
from __future__ import annotations

import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tdxdata._engine import session as S                            # noqa: E402


class _Fake:
    """假的 socket：记录重连次数，不真的连网。"""

    def __init__(self):
        self.closed = False
        self.sent = []

    def settimeout(self, _t):
        pass

    def recv(self, _n):
        raise TimeoutError

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


def test_idle_threshold_constant():
    """空闲阈值必须是 90s（实测 60s 可用、180s 已死）。"""
    assert S.IDLE_RECONNECT == 90.0, S.IDLE_RECONNECT


def test_ask_reconnects_when_idle_exceeded():
    """空闲超过阈值时，ask() 必须先重连再发。"""
    sess = S.Session()
    calls = {"reconnect": 0}

    def fake_reconnect():
        calls["reconnect"] += 1
        sess.sock = _Fake()
        sess._last_io = time.time()
        return sess

    sess.reconnect = fake_reconnect
    sess.sock = _Fake()
    sess._last_io = time.time() - (S.IDLE_RECONNECT + 10)      # 假装闲置了很久

    sess.ask(0x21, "CWServ.x", b"{}", idle=0.01, tail=0)
    assert calls["reconnect"] == 1, "空闲超阈值没有重连（%d 次）" % calls["reconnect"]


def test_ask_does_not_reconnect_when_fresh():
    """刚用过就不要重连（否则每次都握手，白花 1.3 秒）。"""
    sess = S.Session()
    calls = {"reconnect": 0}

    def fake_reconnect():
        calls["reconnect"] += 1
        sess.sock = _Fake()
        return sess

    sess.reconnect = fake_reconnect
    sess.sock = _Fake()
    sess._last_io = time.time()                                # 刚用过

    sess.ask(0x21, "CWServ.x", b"{}", idle=0.01, tail=0)
    assert calls["reconnect"] == 0, "刚用过却重连了"


def test_sendall_failure_triggers_same_host_reconnect():
    """半死连接：sendall 抛 OSError 时应同机重连并重发。"""
    sess = S.Session()
    calls = {"reconnect": 0}

    class _Dead(_Fake):
        def sendall(self, data):
            raise ConnectionResetError(10054, "reset")

    def fake_reconnect():
        calls["reconnect"] += 1
        sess.sock = _Fake()                                    # 换成好连接
        return sess

    sess.reconnect = fake_reconnect
    sess.sock = _Dead()
    sess._last_io = time.time()

    sess.ask(0x21, "CWServ.x", b"{}", idle=0.01, tail=0)
    assert calls["reconnect"] == 1, "半死连接没触发重连"


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
        except Exception as e:                                  # noqa: BLE001
            bad += 1
            print("  ❌ %s: %s: %s" % (name, type(e).__name__, e))
    print("\n%d 个用例，失败 %d" % (len(fns), bad))
    sys.exit(1 if bad else 0)
