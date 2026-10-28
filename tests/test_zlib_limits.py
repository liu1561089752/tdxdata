# -*- coding: utf-8 -*-
"""SEC-2：**四条** zlib 通路都必须在同一个硬上限内解压。

为什么这组测试不能只测 `pb_api`：评审时只在 `decode_any` 里堵了炸弹，另外三条
（帧拆包 / 魔数扫包体 / 探测链剥层）还各自留着无上限的 `zlib.decompress` ——
炸弹从那三条任意一条进来，前面的修复就白做了。所以这里**逐条通路**各点一次火：

| 通路 | 入口 |
|---|---|
| 统一实现 | `zliblimit.inflate_limited` |
| PB 回包 | `pb_api.decode_any`（既有的 `test_pb_api_limits.py` 已覆盖） |
| 帧拆包 | `datacenter.unwrap_frames`（帧头 `0x3B` 声明 zlib） |
| 魔数扫包 | `datacenter._try_zlib` |
| 探测链 | `hq_extra.peel` |

判据两侧都要有：**超限必须抛** `ProtocolError`（硬错误，不许降级成"猜错"），
**正常包必须照常解开**（不许为了安全把功能弄坏）。
"""
from __future__ import annotations

import struct
import zlib

import pytest

from tdxdata import errors
from tdxdata._engine import datacenter as dc
from tdxdata._engine import hq_extra
from tdxdata._engine import pb_api
from tdxdata._engine import zliblimit


@pytest.fixture()
def tiny_cap(monkeypatch):
    """把所有通路的统一上限压到 1 KiB（`limit=None` 时现读模块常量，所以立刻生效）。"""
    monkeypatch.setattr(zliblimit, "MAX_DECOMPRESS", 1024)
    return 1024


def _bomb(n=4096):
    """n 字节的 zlib 流（上限 1024 时必定超限）。"""
    return zlib.compress(b"\x00" * n, 9)


def _frame(zbody):
    """造一个真帧头：`0x3B + msgid(4) + ctl(1) + len(2) + len2(2) + b"K&" + b"\\x00d" + body`。"""
    return (b"\x3b" + struct.pack("<I", 1) + b"\x11"
            + struct.pack("<H", len(zbody)) + struct.pack("<H", len(zbody))
            + b"K&" + b"\x00d" + zbody)


# ══════════════════════════════════════════════════════════════════════════
# 一、统一实现本身
# ══════════════════════════════════════════════════════════════════════════
def test_constant_is_64mib_and_shared():
    assert zliblimit.MAX_DECOMPRESS == 64 * 1024 * 1024
    # pb_api 保留的别名必须与统一常量同值（漂移会让两条通路的上限悄悄分叉）
    assert pb_api._MAX_DECOMPRESS == zliblimit.MAX_DECOMPRESS


def test_inflate_limited_raises_protocol_error_over_cap(tiny_cap):
    with pytest.raises(errors.ProtocolError) as ei:
        zliblimit.inflate_limited(_bomb(), context="测试通路")
    msg = str(ei.value)
    assert "1024" in msg and "超过硬上限" in msg
    assert "测试通路" in msg, "context 必须进报错文本（运维要知道是哪条通路）"


def test_inflate_limited_decompresses_normal_payload(tiny_cap):
    small = zlib.compress(b"hello" * 10)
    assert zliblimit.inflate_limited(small) == b"hello" * 10


def test_inflate_limited_truncated_stream_raises_zlib_error():
    """截断流 ≠ 超限：必须是 `zlib.error`（调用方继续探测），不是硬错误。"""
    with pytest.raises(zlib.error):
        zliblimit.inflate_limited(zlib.compress(b"x" * 100)[:-3])


def test_inflate_limited_explicit_limit_wins(tiny_cap):
    """显式 `limit=` 覆盖模块常量（给将来真有大包的调用方留的门）。"""
    payload = zlib.compress(b"y" * 4000, 9)
    assert len(zliblimit.inflate_limited(payload, limit=8192)) == 4000


# ══════════════════════════════════════════════════════════════════════════
# 二、帧拆包通路（datacenter.unwrap_frames）
# ══════════════════════════════════════════════════════════════════════════
def test_unwrap_frames_rejects_bomb(tiny_cap):
    with pytest.raises(errors.ProtocolError):
        dc.unwrap_frames(_frame(_bomb()))


def test_unwrap_frames_still_unwraps_normal_zlib_frame(tiny_cap):
    body = zlib.compress(b'{"ColName":["a"],"Content":[[1]]}')
    out = dc.unwrap_frames(_frame(body))
    assert out and out[0] == b'{"ColName":["a"],"Content":[[1]]}'


def test_unwrap_frames_passes_through_non_zlib_body(tiny_cap):
    """帧头声明 zlib、实际不是 → 仍原样交出（原有降级语义不能被上限改掉）。"""
    body = b"\x78not-really-zlib-at-all"
    out = dc.unwrap_frames(_frame(body))
    assert out and out[0] == body


# ══════════════════════════════════════════════════════════════════════════
# 三、魔数扫包体通路（datacenter._try_zlib）
# ══════════════════════════════════════════════════════════════════════════
def test_try_zlib_rejects_bomb(tiny_cap):
    with pytest.raises(errors.ProtocolError):
        dc._try_zlib(_bomb())


def test_try_zlib_returns_none_for_fake_magic(tiny_cap):
    assert dc._try_zlib(b"\x78\x9cnope") is None


def test_try_zlib_returns_payload_for_real_stream(tiny_cap):
    assert dc._try_zlib(zlib.compress(b"ok-bytes")) == b"ok-bytes"


# ══════════════════════════════════════════════════════════════════════════
# 四、探测链通路（hq_extra.peel）
# ══════════════════════════════════════════════════════════════════════════
def test_peel_rejects_bomb(tiny_cap):
    with pytest.raises(errors.ProtocolError):
        hq_extra.peel(_bomb())


def test_peel_still_strips_normal_zlib(tiny_cap):
    assert hq_extra.peel(zlib.compress(b"\x08\x01")) == b"\x08\x01"


def test_peel_keeps_fake_magic_silent(tiny_cap):
    """`0x78` 开头但解不开 = 不是 zlib（探测语义），必须继续往下走而不是抛。"""
    out = hq_extra.peel(b"\x78garbage-not-zlib")
    assert out == b"\x78garbage-not-zlib"


# ══════════════════════════════════════════════════════════════════════════
# 五、静态铁律：不许再出现无上限的 zlib 解压
# ══════════════════════════════════════════════════════════════════════════
def test_no_unbounded_zlib_decompress_left_in_package():
    """全包静态扫描：只允许 `zliblimit` 内部出现 `zlib.decompress` / `decompressobj()`。

    这条是**给未来的人**写的 —— 新增一条通路时若直接 `zlib.decompress(blob)`，
    这里就会红，而不是等到有人拿炸弹来试。
    """
    import pathlib
    import re

    root = pathlib.Path(pb_api.__file__).resolve().parents[1]
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "zliblimit.py":
            continue
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"zlib\.decompress\w*\(|zlib\.decompressobj\(", text):
            line = text[:m.start()].count("\n") + 1
            raw = text.splitlines()[line - 1]
            if raw.lstrip().startswith("#"):
                continue                       # 注释里提旧写法不算
            offenders.append("%s:%d %s" % (path.relative_to(root), line, raw.strip()))
    assert not offenders, "这些地方还在无上限解压，请改用 zliblimit.inflate_limited：\n" + "\n".join(offenders)
