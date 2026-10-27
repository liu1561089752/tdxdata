# -*- coding: utf-8 -*-
"""D6 回归：**明文 protobuf 帧**必须能被切出来（离线，不连网）。

缺陷是什么
---------
`datacenter.unwrap()` 只有两条判别路径：
  1. 找 `{"Length": "N"}` JSON 头（那是 CWServ 数据中心的形态）
  2. 扫 zlib 魔数 `0x78 9C`

而 7615 对 **PB 命令**的响应根本不含 JSON 头，body 压不压缩由服务端按 payload 大小决定：

| payload | 首字节 | body | `unwrap()` |
|---|---|---|---|
| 大 | `0x3B` | zlib | ✅ 魔数扫到 |
| 小 | `0x0B` | **明文 protobuf** | ❌ 两条都不占 → 返回 `[]` |

于是"明文帧"被当成传输失败：`_raw_send` 白等 `sleep(8)` × 4 轮 ≈ 35 秒，
用户看到的是「K线无数据」。实测 `113711` 就是被这条藏住的——它一直有 2 根真实成交。

为什么用夹具而不是钉代码
---------------------
之前钉过 `113711`「必须抛 EmptyResult」，结果这只债券一有数据钉桩就红了，
还得人判断是不是库坏了。这里钉的是**帧形态**（一份真实抓下来的 399 字节响应），
与任何债券的存续状态无关，永远不会老化。

跑法：
    python tests/test_frame_plaintext.py
    python -m pytest tests/test_frame_plaintext.py -q
"""
from __future__ import annotations

import os
import socket
import struct
import sys
import zlib

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tdxdata._engine import datacenter as dc                       # noqa: E402
from tdxdata._engine import pb as tdx_pb                           # noqa: E402
from tdxdata._engine import pb_api                                 # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "明文帧_113711_399字节.bin")


def _load() -> bytes:
    with open(FIXTURE, "rb") as f:
        return f.read()


def test_fixture_is_the_real_plaintext_frame():
    """夹具本身要符合文档里那张帧结构表 —— 防止谁手滑改坏夹具。"""
    buf = _load()
    assert len(buf) == 399, len(buf)
    assert buf[0] == 0x0B, "明文帧首字节必须是 0x0B（0x3B 才是 zlib）"
    assert buf[10:12] == b"K&", "偏移 10-11 必须是 0x264B"
    assert buf[12:14] == b"\x00d", "偏移 12-13 必须是 0x0064"
    l1, l2 = struct.unpack("<HH", buf[6:10])
    assert l1 == l2 == 385, (l1, l2)          # 明文：wire 长度 == 解压后长度
    assert 14 + l1 == len(buf), "帧长必须是 14 + wire 长度"
    assert buf[14:16] == b"\x10\x01", "body 开头是 protobuf（field2=1）"


def test_unwrap_alone_cannot_see_it():
    """**钉住旧缺陷**：`unwrap()` 对这个帧就是返回空 —— 这正是当年 35 秒的来源。"""
    assert dc.unwrap(_load()) == [], "如果 unwrap 能认了，说明主路径被增强了，请复核本文件前提"


def test_unwrap_frames_cuts_it_out():
    """兜底切帧必须切出**恰好 1 个**包体。"""
    blobs = dc.unwrap_frames(_load())
    assert len(blobs) == 1, "应切出 1 个包体，实际 %d" % len(blobs)
    assert len(blobs[0]) == 385, len(blobs[0])
    assert blobs[0][:2] == b"\x10\x01", blobs[0][:4]


def test_cut_out_body_decodes_to_two_real_bars():
    """切出来的明文 body 必须能解成 2 根真实成交 —— 这是 D6 的验收数字。"""
    blobs = dc.unwrap_frames(_load())
    kind, payload = pb_api.decode_any(blobs[0])
    assert kind == "pb", kind
    cols, rows = tdx_pb.parse_table(payload)
    assert cols and "Close" in cols, cols
    assert len(rows) == 2, "应有 2 根，实际 %d" % len(rows)
    got = [(r["Data"], round(float(r["Close"]), 3)) for r in rows]
    assert got == [("20261023", 137.928), ("20261026", 139.751)], got
    assert round(float(rows[1]["Volume"]), 0) == 291441.0, rows[1]["Volume"]


def test_send_returns_the_blob_instead_of_retrying():
    """端到端（假 socket）：这条帧经 `_send` 必须直接给出 1 个包体，而不是空 → 重试。"""
    buf = _load()

    class Sock:
        def __init__(self):
            self.sent = []
            self.script = [socket.timeout(), buf, socket.timeout()]

        def sendall(self, d):
            self.sent.append(d)

        def settimeout(self, t):
            pass

        def recv(self, n):
            if not self.script:
                raise socket.timeout()
            it = self.script.pop(0)
            if isinstance(it, BaseException):
                raise it
            return it

        def close(self):
            pass

    sock = Sock()
    real_cc, real_hs, real_sleep = (dc.socket.create_connection, dc.fresh_handshake,
                                    __import__("time").sleep)
    dc.socket.create_connection = lambda *a, **k: sock
    dc.fresh_handshake = lambda *a, **k: b"HS"
    __import__("time").sleep = lambda *a, **k: None
    dc._drop_pb_conn()
    try:
        blobs = dc._send(b"FRAME")
    finally:
        dc.socket.create_connection = real_cc
        dc.fresh_handshake = real_hs
        __import__("time").sleep = real_sleep
        dc._drop_pb_conn()
    assert len(blobs) == 1, "明文帧必须切出 1 个包体（实测 %d）" % len(blobs)
    assert len(blobs[0]) == 385


# ── 兜底解析器自身的边界 ──────────────────────────────────────────────────
def _frame(body: bytes, *, flag: int = 0x0B, msgid: int = 0x7000,
           ctl: int = 0x00) -> bytes:
    return (bytes([flag]) + struct.pack("<I", msgid) + bytes([ctl])
            + struct.pack("<H", len(body)) + struct.pack("<H", len(body))
            + b"K&" + b"\x00d" + body)


def test_synthetic_plain_frame():
    body = b"\x10\x01\x1a\x03abc"
    assert dc.unwrap_frames(_frame(body)) == [body]


def test_zlib_body_is_decompressed_by_the_splitter():
    """首字节 0x3B + zlib body：切出来应当是**解压后**的内容。"""
    raw = b'{"N001": "1.5"}' * 20
    body = zlib.compress(raw)
    buf = _frame(body, flag=0x3B)
    got = dc.unwrap_frames(buf)
    assert len(got) == 1 and got[0] == raw, got


def test_multiple_frames_in_one_buffer():
    a, b = b"\x10\x01" + b"a" * 10, b"\x10\x02" + b"b" * 12
    assert dc.unwrap_frames(_frame(a) + _frame(b)) == [a, b]


def test_declared_length_smaller_than_body_must_not_truncate():
    """**`len1/len2` 不可靠**：声明长度比实际小时不许按它截断（交接包 §6.6）。

    实测场景：`PBCombHQ` 一次传 6 个代码，帧头声明的长度比实际 body **小**，
    老实现照它切 → 整张表被切掉、一行都解不出来。所以切法改成「切到下一个帧头为止」。
    """
    body = b"\x10\x01" + b"x" * 200
    frame = _frame(body)
    # 篡改声明长度：说只有 10 字节（真实 202）
    tampered = frame[:6] + struct.pack("<H", 10) + struct.pack("<H", 10) + frame[10:]
    got = dc.unwrap_frames(tampered)
    assert len(got) == 1 and len(got[0]) == len(body), \
        "不许按声明长度截断：期望 %d 字节，实际 %s" % (len(body), [len(b) for b in got])
    assert got[0] == body


def test_truncated_tail_is_taken_as_is():
    """尾部不全时**取到流末尾**（因为长度字段不可信，没有别的判据）。

    老实现选择"丢弃"，等于在长度可信的前提下才成立 —— 而长度恰恰不可信。
    真被截断的响应由解析器（JSON/protobuf 校验）拒掉，不靠切帧层猜。
    """
    full = _frame(b"\x10\x01" + b"x" * 30)
    got = dc.unwrap_frames(full[:-5])
    assert len(got) == 1, got
    assert got[0] == (b"\x10\x01" + b"x" * 30)[:-5], len(got[0])


def test_garbage_does_not_match():
    assert dc.unwrap_frames(b"\x00" * 100) == []
    assert dc.unwrap_frames(b"") == []
    # 首字节对、标记不对 → 不许误命中
    fake = bytearray(_frame(b"\x10\x01hello"))
    fake[10] = 0x00
    assert dc.unwrap_frames(bytes(fake)) == []


def test_splitter_never_used_for_json_commands():
    """结构性护栏：兜底只能在 `unwrap()` 失败之后调用（JSON 命令的 len 不是 body 长度）。"""
    import inspect

    src = inspect.getsource(dc._send)
    assert "unwrap_frames(buf)" in src
    i_unwrap = src.index("unwrap(buf)")
    i_frames = src.index("unwrap_frames(buf)")
    assert i_unwrap < i_frames, "必须先试 unwrap()，兜底只能在其后"
    # 且必须有一次 "if not blobs" 式的判空
    assert "if not blobs" in src[i_unwrap - 200:i_frames + 20], src[i_unwrap - 200:i_frames + 20]


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
