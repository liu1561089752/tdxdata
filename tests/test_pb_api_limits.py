# -*- coding: utf-8 -*-
"""`decode_any` 的**解压硬上限**回归测试（`DEF-B4-02`）—— 零网络、零第三方依赖。

被守的红线
----------
1. **超限必须报错**：zlib 炸弹不许把内存打爆，更不许"解到一半截断后当正常回包"
   —— 截断的回包会在下游变成缺行/错列的**假数据**，比抛错贵得多；
2. **降级语义不许被改死**：首字节 `0x78` 但**真不是** zlib 的帧，仍要能落到
   明文 JSON / protobuf 探测（历史兼容路径，改死等于批量回归）；
3. **合法回包不许误伤**：正常大小的压缩回包照常解出 JSON。

跑法：`python -m pytest tests/test_pb_api_limits.py -q`
     或 `python tests/test_pb_api_limits.py`
"""
from __future__ import annotations

import json
import os
import sys
import zlib

import pytest

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tdxdata import errors                                    # noqa: E402
from tdxdata._engine import pb_api as PA                      # noqa: E402

MIB = 1024 * 1024


def _bomb(total: int, level: int = 9) -> bytes:
    """高压缩比炸弹：`total` 字节 NUL 压成几十 KB（压缩比远超 100:1）。"""
    return zlib.compress(b"\x00" * total, level)


# ── 1. 上限常量本身 ──────────────────────────────────────────────────────
def test_max_decompress_constant_is_64_mib():
    """上限必须写成模块级常量（调用方/测试都能读，别藏在函数默认值里）。"""
    assert PA._MAX_DECOMPRESS == 64 * MIB, PA._MAX_DECOMPRESS


# ── 2. 真炸弹（真实上限，不 monkeypatch）────────────────────────────────
def test_real_bomb_over_default_limit_raises_protocol_error():
    """80 MiB 的解压输出（压缩后 ~800 KB）必须抛 `ProtocolError`，不许返回 payload。

    这是**实测数字**跑的：压缩 0.02s、解压判定 0.001s（只解出上限+1 字节就停）。
    """
    bomb = _bomb(80 * MIB, level=1)
    assert len(bomb) < 2 * MIB, "压缩比前提失效：%d 字节" % len(bomb)

    with pytest.raises(errors.ProtocolError) as ei:
        PA.decode_any(bomb, "HQServ.PBZSTDDE ctl=0x11 的")

    msg = str(ei.value)
    assert str(PA._MAX_DECOMPRESS) in msg, "异常消息必须带上限数字：%s" % msg
    assert str(PA._MAX_DECOMPRESS + 1) in msg, "异常消息必须带已解出的实际字节数：%s" % msg
    assert str(len(bomb)) in msg, "异常消息必须带压缩后大小：%s" % msg
    assert "HQServ.PBZSTDDE" in msg, "异常消息必须带调用上下文（哪个命令）：%s" % msg


def test_patched_small_limit_also_hard_errors(monkeypatch):
    """把上限 monkeypatch 到 1 MiB，8 MiB 载荷同样必须硬报错（判据与规模无关）。"""
    monkeypatch.setattr(PA, "_MAX_DECOMPRESS", 1 * MIB)
    bomb = _bomb(8 * MIB)

    with pytest.raises(errors.ProtocolError) as ei:
        PA.decode_any(bomb)

    msg = str(ei.value)
    assert str(1 * MIB) in msg
    assert str(1 * MIB + 1) in msg


def test_limit_error_is_not_downgraded_to_probe_chain():
    """超限是**硬错误**：绝不许被 `except zlib.error` 吞掉后继续明文/PB 探测。

    旧写法 `except Exception` 会把炸弹悄悄放过去 —— 这里正面钉住
    "全字节为 NUL 的炸弹不会被解释成 pb/raw 而正常返回"。
    """
    bomb = _bomb(80 * MIB)
    with pytest.raises(errors.ProtocolError):
        PA.decode_any(bomb)


def test_call_pb_propagates_bomb_as_protocol_error(monkeypatch):
    """经过 `call_pb`（真实调用路径）时也必须是 `ProtocolError`，不是 `EmptyResult`。"""
    bomb = _bomb(80 * MIB)

    def fake_send(ctl, cmd, args, tries=2, backoff=3.0):
        return bomb

    monkeypatch.setattr(PA, "_raw_send", fake_send)
    with pytest.raises(errors.ProtocolError):
        PA.call_pb("HQServ.PBZSTDDE")


# ── 3. 反向对照：正常/临界以下的压缩回包照常解 ──────────────────────────
def test_small_zlib_json_still_decodes():
    """正常小 JSON 经 zlib 压缩 → 仍是 `("json", {...})`。"""
    payload = {"a": 1, "b": [1, 2, 3], "c": "中文"}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    kind, obj = PA.decode_any(zlib.compress(raw))
    assert kind == "json", kind
    assert obj == payload


def test_payload_just_under_patched_limit_still_decodes(monkeypatch):
    """上限内的大一点的载荷必须照常解出（防止把上限设得过紧而误伤）。"""
    monkeypatch.setattr(PA, "_MAX_DECOMPRESS", 2 * MIB)
    big = json.dumps({"rows": list(range(20000))}).encode("utf-8")
    assert len(big) < 2 * MIB
    kind, obj = PA.decode_any(zlib.compress(big))
    assert kind == "json"
    assert len(obj["rows"]) == 20000


# ── 4. 降级语义：像 zlib 但不是 zlib ────────────────────────────────────
def test_fake_zlib_prefix_falls_through_to_probe():
    """首字节 `0x78` 但不是 zlib → 不许抛，仍落到 pb/raw 探测。"""
    kind, payload = PA.decode_any(b"\x78" + b"garbage-not-zlib")
    assert kind in ("pb", "raw"), kind
    if kind == "raw":
        assert payload == b"\x78" + b"garbage-not-zlib"


def test_truncated_zlib_stream_falls_through_to_probe():
    """被截断的 zlib 流（校验尾缺失）属于"解不开"，同样走降级而不是硬错误。"""
    good = zlib.compress(json.dumps({"a": 1}).encode())
    kind, _ = PA.decode_any(good[:-4])
    assert kind in ("json", "pb", "raw")


def test_empty_blob_is_raw_empty():
    assert PA.decode_any(b"") == ("raw", b"")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
