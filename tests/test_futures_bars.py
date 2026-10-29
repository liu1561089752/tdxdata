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


# ══════════════════════════════════════════════════════════════════════════
# 周期（2026-10-29 探测 D 结论）：一个字节换分钟/周/月
# ══════════════════════════════════════════════════════════════════════════
def test_周期表与实测一致():
    """协议值必须和真机试验对得上，且**60 分钟不许偷偷放行**。"""
    assert classic.PERIODS == {"1m": 7, "5m": 0, "15m": 1, "30m": 2,
                               "day": 4, "week": 5, "month": 6}
    for name, code in classic.PERIODS.items():
        assert classic.period_code(name) == code
        assert classic.period_code(code) == code
    for bad in ("60m", "3", 3, "7m", "", None):
        try:
            classic.period_code(bad)
        except (ValueError, TypeError):
            pass
        else:                                    # pragma: no cover
            raise AssertionError("%r 应该被拒绝（未验证的周期不许放出去）" % (bad,))


def test_周期字段进的是第26字节():
    day = classic.bars_args("MA2612", 28)
    for name, code in classic.PERIODS.items():
        body = classic.bars_args("MA2612", 28, period=code)
        assert body[26] == code, name
        # 除了第 26 字节，其它位置必须一字不动
        assert body[:26] == day[:26] and body[27:] == day[27:], name


def test_分钟线多出Time列且时间按序():
    """5 分钟 fixture（真机回包 1593 B）：10:15 起、每根 +5 分钟、收在 15:00。"""
    buf = (FIX / "fut_bars_if2611_5m.bin").read_bytes()
    cols, rows = classic.parse_bars(buf, with_time=True)
    assert cols == classic.BARS_COLS_INTRADAY
    assert "Time" in cols and cols.index("Time") == 1
    assert len(rows) >= 30
    times = [r[1] for r in rows]
    assert times[0] == "10:15", times[:3]
    assert times[-1] == "15:00", times[-3:]
    minutes = [int(x[:2]) * 60 + int(x[3:]) for x in times]
    gaps = [b - a for a, b in zip(minutes, minutes[1:])]
    # 相邻间隔只允许两种：正常 5 分钟，以及**午休跨段**（11:25 → 13:00 = 95 分钟）
    assert set(gaps) <= {5, 95}, "出现了非预期间隔：%s" % sorted(set(gaps))
    assert gaps.count(5) >= 30, "5 分钟间隔至少要占绝大多数，实测 %d/%d" % (
        gaps.count(5), len(gaps))


def test_日线不加Time列():
    ans = (FIX / "fut_bars_ma2612_响应.bin").read_bytes()
    cols, rows = classic.parse_bars(ans)
    assert cols == classic.BARS_COLS and "Time" not in cols
    assert len(rows) == 700


# ══════════════════════════════════════════════════════════════════════════
# 帧头：经典回包的标记是 b4 26（不是 protobuf 的 4b 26）
# ══════════════════════════════════════════════════════════════════════════
def test_帧头两种标记都认():
    """只认 `4b 26` 时，小回包（count=10，527 B 明文帧）会被静默丢成空表。"""
    from tdxdata._engine.datacenter import _is_frame_head

    pb = bytes.fromhex("0b0070000000010201024b260064") + b"\x00" * 8
    cls = bytes.fromhex("0b007000000001020102b4260064") + b"\x00" * 8
    assert _is_frame_head(pb, 0), "protobuf 帧（4b 26）必须认"
    assert _is_frame_head(cls, 0), "经典帧（b4 26）也必须认 —— 否则小回包全被丢"
    bad = bytearray(cls)
    bad[10:12] = b"\x4b\x26"      # 标记换回 protobuf 但下面 0064 也对不上
    bad[12:14] = b"\x00\x00"
    assert not _is_frame_head(bytes(bad), 0)
