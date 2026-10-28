# -*- coding: utf-8 -*-
r"""ARCH-001 步骤 2 交付件：`ReplayTransport` —— 读 tests/fixtures/golden/ 的 Transport 实现。

为什么独立成文件（而不是塞进 golden_runner.py）
----------------------------------------------
`tests/golden_runner.py` 本步**禁碰**（Lead 直辖），但 golden_runner 自己已经
有一套内联的 `Replay` / `_ReplaySession`（走 `Client._ensure` + `dc._send`
monkeypatch 接缝）。本文件提供的是**目标形态**：把回放做成一个 `Transport`，
通过 `datacenter._send(..., conn=PBChannel(state=...))` 或直接替换
`pb_api._raw_send` 注入 —— 不碰 client 内部接缝，为后续 golden_runner
接线改造提供可独立测试的实现。

**职责边界（与 golden_runner.Replay 的关系）**
  * golden_runner.Replay —— 现役：整库端到端回放 + 逐字段 diff（Lead 直辖）；
  * ReplayTransport —— 本步交付：**纯传输层**回放（查帧表 → 返回 blobs），
    供 `_raw_send` / `dc._send(conn=…)` 注入与传输契约单测；不做样本级比对。

帧文件格式（与 golden_runner.write_frame_file / read_frame_file 同口径）：
    魔数 `TDXGF1` + [meta json][args][raw][blobs×n]（各段 u32 长度前缀）
manifest（frames/manifest.json）entries：`{transport, cmd, ctl, args_sha256, file, …}`
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
from typing import Callable

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "fixtures", "golden")
FRAMES = os.path.join(GOLDEN, "frames")
MANIFEST = os.path.join(FRAMES, "manifest.json")

FRAME_MAGIC = b"TDXGF1"

#: 帧表键口径：与 golden_runner.frame_key 完全一致（transport, cmd, ctl, args_sha256）。
#: 不 import golden_runner（它模块级 import tdxdata 并做环境断言，测试侧轻量使用
#: 不该背上这层）——两边口径由 test_transport_arch001 的对账用例钉死。


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def frame_key(transport: str, cmd: str, ctl: int, args: bytes) -> tuple:
    """回放查表键：传输方式 + 命令 + ctl + 请求参数字节的指纹（同 golden_runner）。"""
    return (transport, str(cmd), int(ctl), sha_bytes(bytes(args)))


def parse_wire_frame(frame: bytes) -> tuple[int, str, bytes]:
    """从 7615 帧字节取 `(ctl, cmd, args)`（同 golden_runner / transport.frame_ctl_cmd_args）。"""
    if len(frame) < 16:
        raise ValueError("帧太短（%d 字节），不是 7615 帧" % len(frame))
    ctl = frame[5]
    body = frame[14:]
    cut = body.find(b"\x00\x00")
    if cut < 0:
        return ctl, "", b""
    return ctl, body[:cut].decode("ascii", "replace"), body[cut + 2:]


def read_frame_file(path: str):
    """读回 `(meta, args, raw, blobs)`，与 golden_runner.read_frame_file 同口径。"""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:6] != FRAME_MAGIC:
        raise ValueError("%s 不是 TDXGF1 帧文件（前 6 字节 %r）" % (path, data[:6]))
    pos = [6]

    def take_u32() -> int:
        val = struct.unpack_from("<I", data, pos[0])[0]
        pos[0] += 4
        return val

    n = take_u32()
    meta = json.loads(data[pos[0]:pos[0] + n].decode("utf-8"))
    pos[0] += n
    n = take_u32()
    args = data[pos[0]:pos[0] + n]
    pos[0] += n
    n = take_u32()
    raw = data[pos[0]:pos[0] + n]
    pos[0] += n
    blobs = []
    for _ in range(take_u32()):
        m = take_u32()
        blobs.append(data[pos[0]:pos[0] + m])
        pos[0] += m
    return meta, args, raw, blobs


class FrameMissing(BaseException):
    """回放时查不到对应帧（刻意 BaseException，同 golden_runner.FrameMissing：不许被重试吞掉）。"""


class ReplayTransport:
    """`Transport` 的离线回放实现：查帧表，返回录下的包列表，零 socket。

    用法（两种注入缝，测试与后续接线二选一）：
        t = ReplayTransport()                       # 读真实 golden 帧表
        t.call("HQServ.PBFXT", 0x0E, b"...", idle=2.0)   # -> list[bytes]

        # 注入 PB 通路：替换 pb_api._raw_send，或在 dc._send(conn=…) 缝上
        # 用 PBChannel(state=…)+FakeSock 桥接（见 test_transport_arch001）。

    `loader` 可注入自定义帧表（单测里造最小帧集，不必落盘）。
    """

    def __init__(self, *, manifest_path: str | None = None,
                 loader: Callable[[], list[dict]] | None = None):
        if loader is not None:
            entries = loader()
        else:
            entries = self._load(manifest_path or MANIFEST)
        self.entries = entries
        self.table: dict[tuple, dict] = {}
        for ent in entries:
            self.table.setdefault(frame_key(ent["transport"], ent["cmd"],
                                            ent["ctl"], ent["args"]), ent)
        self.misses: list[str] = []

    @staticmethod
    def _load(manifest_path: str) -> list[dict]:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        out = []
        for ent in manifest.get("entries") or []:
            path = os.path.join(os.path.dirname(manifest_path), ent["file"])
            _meta, args, raw, blobs = read_frame_file(path)
            out.append({"transport": ent["transport"], "cmd": ent["cmd"],
                        "ctl": ent["ctl"], "args": args, "raw": raw,
                        "blobs": blobs, "file": ent["file"], "seq": ent["seq"]})
        return out

    # -- Transport 协议 ---------------------------------------------------
    def call(self, cmd: str, ctl: int, body: bytes, *,
             idle: float = 4.0) -> list[bytes]:
        """查表返回包列表。**传输形态 = "send"**（整帧进、包列表出），
        这是 `dc._send`/`pb_api` 注入的口径；"ask" 口径（args 直接进、单包出）
        由 `call_ask` 提供，两者都查同一张表。"""
        return self.call_send(cmd, ctl, body)

    def call_send(self, cmd: str, ctl: int, body: bytes) -> list[bytes]:
        ent = self._lookup("send", cmd, ctl, body)
        raw = ent.get("raw") or b""
        if raw:
            from tdxdata._engine import datacenter as dc
            blobs = dc.unwrap(raw) or dc.unwrap_frames(raw)
            # 与生产 `_send` 同口径：切不出包就是空列表（不拿存档顶上，
            # 否则解包层退化了也看不出来）
            return list(blobs)
        return list(ent.get("blobs") or [])

    def call_ask(self, cmd: str, ctl: int, body: bytes) -> bytes:
        """JSON 通路口径：返回**拍平后的单包载荷**（golden 录制时 ask 侧只存一包）。"""
        ent = self._lookup("ask", cmd, ctl, body)
        blobs = list(ent.get("blobs") or [])
        return blobs[0] if blobs else b""

    # -- 查表 -------------------------------------------------------------
    def _lookup(self, transport: str, cmd: str, ctl: int, args: bytes) -> dict:
        key = frame_key(transport, cmd, ctl, args)
        ent = self.table.get(key)
        if ent is None:
            msg = ("帧缺失：transport=%s cmd=%s ctl=0x%02X args_sha256=%s"
                   % (transport, cmd, int(ctl), key[3]))
            self.misses.append(msg)
            raise FrameMissing(msg)
        return ent


class PBChannel:
    """`_CONN` 实例化的最小形态：一个**同构 state dict + 自己的锁**。

    `dc._send(..., conn=ch)` 即可让 PB 通路走这条通道；不传 `conn` 时
    全局单侧行为逐位不变（`state()` 就是模块级 `_CONN`）。
    """

    def __init__(self):
        self._state = {"sock": None, "host": None, "t": 0.0}
        import threading
        self._lock = threading.RLock()

    def state(self) -> dict:
        return self._state

    @property
    def lock(self):
        return self._lock

    @property
    def alive(self) -> bool:
        return self._state["sock"] is not None

    def close(self) -> None:
        from tdxdata._engine import datacenter as dc
        dc._drop_pb_conn(state=self._state)
