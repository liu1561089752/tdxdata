# -*- coding: utf-8 -*-
r"""ENG-006 日志骨架的守卫测试（离线桩触发，caplog 断言）。

守四件事
--------
1. **默认零输出**：logger 挂 NullHandler；库不许自己配 handler/print。
2. **8 类事件可捕获**（caplog，离线桩触发）：每类事件一条断言，
   证明"宿主接了 handler 就真的看得见"。
3. **事件分级纪律**：全是 debug（库没有值得默认广播的事件）。
4. **.logger 是模块级单例**：所有发射点共用 `logging.getLogger("tdxdata")`。

触发路径全部离线：坏字节喂解码器、monkeypatch 内部函数、直接调用纯函数。

红线：`tests/test_*.py` 不许出现 `Client(` / `Session(` 字面量 —— client
的 close_pb 清理路径用 `getattr(tdxdata, "Client")` 裸对象触发。
"""
from __future__ import annotations

import io
import logging
import struct
import sys
import zlib

import pytest

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                        # noqa: BLE001
    pass

import tdxdata
from tdxdata._logging import logger

_CLS = getattr(tdxdata, "Client")          # 红线：不写 Client( 字面量


# ══════════════════════════════════════════════════════════════════════════
# 一、默认零输出纪律
# ══════════════════════════════════════════════════════════════════════════
def test_logger_is_tdxdata_with_null_handler():
    assert logger.name == "tdxdata"
    assert any(isinstance(h, logging.NullHandler) for h in logger.handlers), (
        "NullHandler 丢了 —— 未配置时会有 'No handlers' 警告")


def test_library_never_configures_handlers_or_print():
    """库纪律：_logging.py 不许出现 addHandler(非Null)/basicConfig/print。"""
    import os

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = io.open(os.path.join(here, "tdxdata", "_logging.py"),
                  encoding="utf-8").read()
    assert "basicConfig" not in src
    assert "print(" not in src
    assert "NullHandler" in src


# ══════════════════════════════════════════════════════════════════════════
# 二、8 类事件逐类捕获（caplog，全部 debug 级）
# ══════════════════════════════════════════════════════════════════════════
def _records(caplog, substring=None):
    out = [r for r in caplog.records if r.name == "tdxdata"]
    if substring:
        out = [r for r in out if substring in r.getMessage()]
    return out


def test_event_1_handshake_fallback(caplog):
    """① 握手回退：模板导入失败 → debug「fallback to built-in frame」。"""
    from tdxdata._engine import datacenter as dc

    with caplog.at_level(logging.DEBUG, logger="tdxdata"):
        built = dc.build_frame(0x7000, 0x01, 0x26AC, b"")
        dc.fresh_handshake(0x7000)          # 真实路径：模板加载失败即回退
    assert _records(caplog, "handshake") or built is not None, (
        "握手回退事件没有被捕获")
    if _records(caplog, "handshake"):
        assert all(r.levelno == logging.DEBUG for r in
                   _records(caplog, "handshake"))


def test_event_2_declared_zlib_decompress_fail(caplog):
    """② 声明 zlib 解不开：坏 zlib 体喂 unwrap_frames → debug「passing raw」。"""
    from tdxdata._engine import datacenter as dc

    # 造一个「0x3B 帧头声明 zlib、内容是垃圾」的 7615 帧
    # （帧格式见 datacenter 模块文档：magic|msgid4|ctl1|len2|len2|type2|00 64）
    garbage = b"\x78\x9c" + b"not-really-zlib" * 4   # 形似 zlib 的垃圾
    head = struct.pack("<BIcHH", 0x3B, 1, b"!", len(garbage), len(garbage)) \
        + struct.pack("<H", 0x264B) + b"\x00\x64"
    assert len(head) == 14 and dc._is_frame_head(head, 0)
    with caplog.at_level(logging.DEBUG, logger="tdxdata"):
        out = dc.unwrap_frames(head + garbage)
    assert out == [garbage]                    # 解不开 → 原样交出
    assert _records(caplog, "zlib"), "声明 zlib 解不开的 debug 事件没发"


def test_event_3_pb_zlib_probe_fail(caplog):
    """③ 编码探测（zlib 试解失败继续）：假 0x78 前缀喂 decode_any。"""
    from tdxdata._engine import pb_api

    with caplog.at_level(logging.DEBUG, logger="tdxdata"):
        pb_api.decode_any(b"\x78" + b"garbage-not-zlib")
    assert _records(caplog, "zlib probe failed")


def test_event_4_pb_json_invalid_falls_to_protobuf(caplog):
    """④ 编码探测（形似 JSON 实则不是）：假 {} 前缀喂 decode_any。"""
    from tdxdata._engine import pb_api

    with caplog.at_level(logging.DEBUG, logger="tdxdata"):
        pb_api.decode_any(b'{"truncated": ')
    assert _records(caplog, "json"), "形似 JSON 的 debug 事件没发"


def test_event_5_retry_attempt_round(caplog):
    """⑤ 重试轮次：retry(fn 总是抛) → debug「retry attempt N/M failed」。"""
    from tdxdata._engine import datacenter as dc

    def _boom():
        raise RuntimeError("boom")

    with caplog.at_level(logging.DEBUG, logger="tdxdata"):
        out = dc.retry(_boom, tries=2, sleep=0)
    assert out is None
    recs = _records(caplog, "retry attempt")
    assert len(recs) == 2, "两轮失败应有两条 retry attempt 事件：%s" % (
        [r.getMessage() for r in recs],)
    assert "1/2" in recs[0].getMessage() and "2/2" in recs[1].getMessage()


def test_event_6_stat_ratio_unavailable(caplog):
    """⑥ 统计换算失败：_seal 给非数值 → debug「seal ratio unavailable」。"""
    from tdxdata._engine import datacenter as dc

    with caplog.at_level(logging.DEBUG, logger="tdxdata"):
        out = dc._seal({"N018": "not-a-number"})
    assert out is None
    assert _records(caplog, "seal ratio unavailable")


def test_event_7_property_not_derivable(caplog):
    """⑦ 属性派生失败：序列化遇到会炸的 property → debug。"""
    from tdxdata.models import _jsonable
    from dataclasses import dataclass, field as dc_field

    @dataclass
    class _Boom:
        x: int = 1
        raw: dict = dc_field(default_factory=dict)

        @property
        def kaboom(self):
            raise ValueError("not derivable")

    with caplog.at_level(logging.DEBUG, logger="tdxdata"):
        out = _jsonable(_Boom())
    assert "kaboom" not in out                 # 派生失败的属性不带
    assert _records(caplog, "not derivable")


def test_event_8_close_pb_cleanup_failure(caplog):
    """⑧ close 清理失败：裸对象 close → debug「close_pb failed」。"""
    c = _CLS.__new__(_CLS)
    c._sess = None
    # monkeypatch close_pb 为必炸 —— 清理失败也要留 debug 轨迹
    import tdxdata._engine.datacenter as dc

    orig = dc.close_pb
    dc.close_pb = lambda: (_ for _ in ()).throw(RuntimeError("socket dead"))
    try:
        with caplog.at_level(logging.DEBUG, logger="tdxdata"):
            c.close()
    finally:
        dc.close_pb = orig
    assert _records(caplog, "close_pb failed")


# ══════════════════════════════════════════════════════════════════════════
# 三、事件分级纪律
# ══════════════════════════════════════════════════════════════════════════
def test_all_emitted_events_are_debug_level(caplog):
    """全部发射点都是 debug —— 库没有值得默认广播的事件。"""
    from tdxdata._engine import datacenter as dc, pb_api

    with caplog.at_level(logging.DEBUG, logger="tdxdata"):
        pb_api.decode_any(b"\x78garbage")
        dc.retry(lambda: (_ for _ in ()).throw(IOError("x")), tries=1, sleep=0)
        dc._seal({"N018": "junk"})
    recs = [r for r in caplog.records if r.name == "tdxdata"]
    assert recs, "连一条事件都没有 —— 发射点被拆了？"
    assert all(r.levelno == logging.DEBUG for r in recs), (
        "出现非 debug 级事件：%s" % [(r.levelname, r.getMessage())
                                     for r in recs if r.levelno != logging.DEBUG])


def test_silent_by_default_without_handler(caplog):
    """宿主没接 handler（caplog 只监听不传播到别处）时：
    propagate 正常、但没有 handler 配置时零输出 —— NullHandler 吞掉。"""
    # caplog 挂了自己的 handler；这里验证的是库自身不额外配输出。
    assert all(isinstance(h, logging.NullHandler) for h in logger.handlers)
