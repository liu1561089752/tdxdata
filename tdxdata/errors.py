# -*- coding: utf-8 -*-
"""异常层次。

设计原则：**让调用方能区分"没数据"和"取不到"**。
这是本项目最容易踩的坑 —— 把两者混成一个 None/空列表，上层会误判成"今天没数据"。
"""
from __future__ import annotations

__all__ = [
    "TdxError", "ConnectionFailed", "Timeout", "ProtocolError",
    "ParamError", "ServerError", "NotSupportedError", "RateLimited",
    "EmptyResult", "NoData",
]


class TdxError(Exception):
    """本库所有异常的基类。"""


# ── 传输层 ────────────────────────────────────────────────────────────────
class ConnectionFailed(TdxError):
    """连不上（全部服务器都失败）。"""


class Timeout(ConnectionFailed):
    """读写超时。"""


class RateLimited(ConnectionFailed):
    """被限流/被踢（10053/10054）。调用方应退避后换服务器重试。"""


class ProtocolError(TdxError):
    """帧头非法、zlib 解压失败、响应截断等 —— 协议层异常，通常需要抓包排查。"""


# ── 语义层 ────────────────────────────────────────────────────────────────
class ParamError(TdxError):
    """服务端返回 -1002：参数不对。"""


class ServerError(TdxError):
    """服务端返回 -1005 / -1009：命令执行失败。"""


class NotSupportedError(TdxError):
    """这条命令/能力**根本不可用**（-1000 协议不存在，或本库未实现）。

    典型场景：按代码取五档
    （协议无 bid/ask 字段，且 `PBMultiHQ` 参数里无代码位 —— 2026-10-25 解构确认）。
    **不要**用 `EmptyResult` 代替它 —— 否则调用方会以为是"当期无数据"。
    """


class EmptyResult(TdxError):
    """命令可用，但**这一次**没数据（`EMPTY_RS 0×N` / `EMPTY 0×0`）。

    区分三态见 `docs/VERIFICATION.md`：
      · `EMPTY_RS 0×N`（有列无行）= 参数对、当期无数据 → 本异常
      · `EMPTY 0×0`（无列无行）= 通道没通 → `NotSupportedError`
      · `ERR_-1002` = 参数错 → `ParamError`
    """


class NoData(EmptyResult):
    """`EmptyResult` 的别名，语义更直白。"""
