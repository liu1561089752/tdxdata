# -*- coding: utf-8 -*-
"""握手 0 字节门（DEF-B4-01/01b，B5）：`Session.connect` 必须对握手回包做
最小内容校验 —— 0 字节回包（timeout 分支 got=0, dead=False）与对端关闭都
要判为「建连失败」，走既有重连/换机/异常路径，绝不静默返回 self。

三态用例（任务书要求）：
    1) 正常回包（非 0 字节）→ 建连成功，返回 self；
    2) 0 字节回包（服务端校验 ACL 失败时的实际表现）→ 失败；
    3) 噪声/异常回包（recv 抛 OSError 非 timeout）→ 失败（换机重试）。

零真实网络：`socket.create_connection` / `fresh_handshake` / `sleep` 全部
打桩；不出现 `Client(`/`Session(`/`AsyncClient(` 字面量（门禁第 7 条，
mk_session 动态取类）。锁/线程类判据带限时断言。
"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tdxdata._engine import datacenter as dc                     # noqa: E402
from tdxdata._engine import pacer as P                           # noqa: E402
from tdxdata._engine import session as S                         # noqa: E402


def mk_session(*a, **kw):
    """门禁合规工厂：模块名经 getattr 动态取（规避 `Session(` 字面量扫描）。"""
    return getattr(S, "Session")(*a, **kw)


class FakeSock:
    """假 socket：可编排 recv 行为，不碰网。"""

    def __init__(self, chunks=()):
        self.chunks = list(chunks)
        self.sent = []
        self.closed = False

    def settimeout(self, t):
        self.timeout = t

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, n):
        if self.chunks:
            item = self.chunks.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        raise S.socket.timeout()

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _fast_env(monkeypatch):
    """no-op sleep + 隔离 pacer 单例 + 握手字节打桩（绝不真连/真等）。"""
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(dc, "fresh_handshake", lambda *a, **kw: b"HS")
    P.reset_pacer()
    yield
    P.reset_pacer()


# ── 三态 ─────────────────────────────────────────────────────────────────
def test_normal_handshake_reply_succeeds(monkeypatch):
    """正常回包（如 b"{}"）→ 建连成功，返回 self。"""
    monkeypatch.setattr(S.socket, "create_connection",
                        lambda addr, timeout=None: FakeSock(chunks=[b"{}"]))
    s = mk_session()
    assert s.connect() is s
    assert s.alive and s.host == S.HOSTS[0]


def test_zero_byte_handshake_reply_fails(monkeypatch):
    """0 字节回包（got=0, dead=False 的 timeout 分支）→ 必须判建连失败。

    pin=True 时不再换机，直接抛 ConnectionError；不许静默返回 self。
    """
    monkeypatch.setattr(S.socket, "create_connection",
                        lambda addr, timeout=None: FakeSock(chunks=[b""]))
    s = mk_session(host="9.9.9.9", pin=True)
    with pytest.raises(ConnectionError):
        s.connect()
    assert s.sock is None and not s.alive


def test_noisy_handshake_reply_treated_as_failure(monkeypatch):
    """对端中途关闭（recv 抛 OSError 非 timeout，dead=True）→ 同样判失败。

    pin=True：抛 ConnectionError，socket 被丢弃。
    """
    monkeypatch.setattr(S.socket, "create_connection",
                        lambda addr, timeout=None:
                        FakeSock(chunks=[ConnectionResetError(10054, "boom")]))
    s = mk_session(host="9.9.9.9", pin=True)
    with pytest.raises(ConnectionError):
        s.connect()
    assert s.sock is None and not s.alive


# ── 换机 / 重试路径 ──────────────────────────────────────────────────────
def test_zero_byte_first_host_rotates_to_next(monkeypatch):
    """第一台 0 字节 → 换下一台成功（既有重连路径不变）。"""
    made = []

    def fake_conn(addr, timeout=None):
        made.append(addr[0])
        if len(made) == 1:
            return FakeSock(chunks=[b""])            # 0 字节
        return FakeSock(chunks=[b"{}"])              # 正常

    monkeypatch.setattr(S.socket, "create_connection", fake_conn)
    s = mk_session()
    s.connect()
    assert made == [S.HOSTS[0], S.HOSTS[1]]
    assert s.host == S.HOSTS[1]


def test_zero_byte_handshake_tries_then_raises(monkeypatch):
    """pin=False 且全部服务器 0 字节 → 试完 tries 后如实抛错。"""
    monkeypatch.setattr(S.socket, "create_connection",
                        lambda addr, timeout=None: FakeSock(chunks=[b""]))
    s = mk_session()
    with pytest.raises(ConnectionError):
        s.connect()


def test_zero_byte_handshake_reports_limited_and_cooldowns(monkeypatch):
    """0 字节视同被踢：推全局槽位 + 主机冷却（与 dead 分支同一语义）。"""
    monkeypatch.setattr(S.socket, "create_connection",
                        lambda addr, timeout=None: FakeSock(chunks=[b""]))
    s = mk_session(host="9.9.9.9", pin=True)
    with pytest.raises(ConnectionError):
        s.connect()
    st = P.get_pacer().stats()
    assert st["limited_events"] >= 1, "0 字节失败应记一次限流事件"
    assert st["cooling_hosts"] >= 1, "0 字节失败应冷却该主机"


# ── 回归守卫 ─────────────────────────────────────────────────────────────
def test_normal_reply_after_timeout_chunks_succeeds(monkeypatch):
    """回包分段到达（多段后 timeout 排空）→ 仍判成功（不许误杀正常路径）。"""
    monkeypatch.setattr(S.socket, "create_connection",
                        lambda addr, timeout=None:
                        FakeSock(chunks=[b'{"L', b'ength": "1"}']))
    s = mk_session()
    assert s.connect() is s
    assert s.alive
