# -*- coding: utf-8 -*-
"""期货 K 线的离线测试（零网络）。

守三件事：
1. **请求体逐字节等于抓包**（48 B 定长体，抄错一个字节服务端就 reset）
2. **帧头逐字节等于抓包**（ctl=0x03 / 标记 b4 26 / `命令号+01`，msgid 除外）
3. **回包解析**：用真机缓存下来的 25353 B 原件（700 根），校验 OHLC 与末根数值
"""
from __future__ import annotations

import pathlib
import struct

import pytest

import tdxdata
from tdxdata._engine import hq_classic as classic

FIX = pathlib.Path(__file__).parent / "fixtures"

#: 抓包原件（cap_1029_090838.pcap，7615 → 服务端）
抓包_请求体_MA2612 = ("2e121c004d413236313200000000000000000000000000000000"
                    "0400010000000000bc020b0001020000000000000000")
抓包_整帧_IF2611 = '0b630100000330003000b42600642e12012e122f00494632363131000000000000000000000000000000000400010000000000bc020b0001020000000000000000'


def test_请求体逐字节等于抓包():
    body = classic.bars_args("MA2612", 28)
    assert len(body) == 48
    assert body.hex() == 抓包_请求体_MA2612


def test_整帧逐字节等于抓包_只差序号():
    got = classic.frame(classic.KLINE, classic.bars_args("IF2611", 47), msgid=0x0163)
    assert got.hex() == 抓包_整帧_IF2611


def test_帧头常量别抄错():
    """ctl=0x03、标记 `b4 26`（protobuf 帧是 `4b 26`）、命令号 0x122E。"""
    assert classic.CTL == 0x03
    assert classic.MARK == b"\xb4\x26"
    assert classic.KLINE == 0x122E
    f = classic.frame(classic.KLINE, b"\x00" * 48)
    assert f[5] == 0x03                      # ctl 在偏移 5
    assert f[10:12] == b"\xb4\x26"           # 标记位
    assert f[14:16] == struct.pack("<H", 0x122E)
    assert f[16] == 0x01                     # 命令号后面的 0x01


def test_市场号与合约码进对位置():
    body = classic.bars_args("CU2612", 30)
    assert struct.unpack_from("<H", body, 0)[0] == 0x122E
    assert struct.unpack_from("<H", body, 2)[0] == 30
    assert body[4:10] == b"CU2612"
    assert struct.unpack_from("<H", body, 34)[0] == 700


def test_长合约码直接拒():
    with pytest.raises(ValueError):
        classic.bars_args("MA2612999", 28)


def test_解析真机回包_700根():
    ans = (FIX / "fut_bars_ma2612_响应.bin").read_bytes()
    cols, rows = classic.parse_bars(ans)
    assert cols == classic.BARS_COLS
    assert len(rows) == 700, "缓存原件应解出 700 根"
    assert rows[-1][0] == 20261029
    assert [round(x, 2) for x in rows[-1][1:5]] == [3588.0, 3595.0, 3527.0, 3544.0]
    # 日期单调 + OHLC 自洽（全量）
    prev = 0
    for r in rows:
        d, o, h, l, c = r[0], r[1], r[2], r[3], r[4]
        assert prev <= d
        prev = d
        assert l <= min(o, c) + 0.01 and max(o, c) - 0.01 <= h


def test_空回包不炸():
    assert classic.parse_bars(b"") == ((), [])


def test_公开方法存在且参数对():
    import inspect

    f = getattr(tdxdata.Client, "get_futures_bars")
    sig = inspect.signature(f)
    assert list(sig.parameters)[:4] == ["self", "code", "market", "count"]
    assert sig.parameters["count"].default == 700
    assert hasattr(tdxdata.AsyncClient, "get_futures_bars")


def test_能力台账说可用():
    from tdxdata import capabilities as caps

    row = next(c for c in caps.CAPABILITIES if c.name == "商品期货")
    text = (row.reason or "") + (row.workaround or "")
    assert "0x122E" in text, "必须写明命令号，否则下一个人又要从头找"
    assert "get_futures_bars" in text
    assert "未定位" not in (row.workaround or ""), "workaround 不能再写未定位"
