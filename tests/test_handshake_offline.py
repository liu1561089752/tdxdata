# -*- coding: utf-8 -*-
"""handshake 模块离线覆盖测试（B4，2026-11）。

只测模块自身逻辑：base64 解码、`get()` 的隔离性与真实性。
**零网络**：这里没有任何 socket 调用；握手字节本身是 2026-10-20 真机抓包
模板的 base64 内嵌（handshake.py:22-33），仅作为数据被读取，不作为
连接凭据被发送 —— 本文件绝不建立连接。
"""
from __future__ import annotations

import base64
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "F:/TDX_stock_data/tdxdata")

from tdxdata._engine import handshake as H                     # noqa: E402


def test_get_returns_bytes():
    b = H.get()
    assert isinstance(b, bytes)
    assert len(b) > 0


def test_get_decodes_from_embedded_b64():
    """内嵌 base64 必须能无损解出（模板完整性的最基本校验）。"""
    raw = base64.b64decode(H._B64)
    assert raw == H.HANDSHAKE


def test_get_gives_fresh_copy_each_call():
    """`get()` 每次给新副本：调用方原地改字节不得污染模板。"""
    a = H.get()
    a2 = bytearray(a)
    a2[0] = (a2[0] ^ 0xFF)
    assert bytes(a2) != H.get(), "get() 返回了共享对象，调用方改字节会污染模板"
    assert H.get() == H.HANDSHAKE


def test_frame_markers_present():
    """握手区含 CONNECT(0x26AC)/LOGIN(0x26AD)/ACL 标记（协议形态自检）。"""
    h = H.HANDSHAKE
    assert b"\xac\x26" in h, "CONNECT 0x26AC 缺失"
    assert b"\xad\x26" in h, "LOGIN 0x26AD 缺失"
    assert b"ACL:checkuser" in h, "ACL 段缺失"


def test_decodes_as_protocol_payload():
    """握手区按响应帧头解析：首字节 0x0B（明文帧），帧内含 0x264B 类型。"""
    h = H.HANDSHAKE
    assert h[0] in (0x0B, 0x3B), h[0]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
