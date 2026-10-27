# -*- coding: utf-8 -*-
"""PB* 接口统一入口：protobuf / JSON 混合响应都能解析。

为什么需要单独一个模块
----------------------
`HQServ.PB*` 这一族与其它接口有三处不同：

1. **参数**可能是 protobuf（Base64）、JSON，或无参数
2. **响应**可能是 protobuf，也可能是 JSON
3. 帧格式**仍然一样**，`00 00` 分隔符保留

实测（2026-10-23）：

    PBFXT     ctl=0x0E  protobuf  12 列 × 120 行日K线
    PBZST     ctl=0x0E  protobuf   6 列 × 240 行分时
    PBSdstat  ctl=0x11  protobuf  打包 float32 数组
    PBZSTDDE  ctl=0x11  **JSON**   40KB DDE 数据
    PBPathFile ctl=0x13 几乎空

用法
----
    from tdx_pb_api import call_pb, PB

    cols, rows = call_pb("HQServ.PBFXT")      # 表结构直接给列名+行
    obj = call_pb("HQServ.PBZSTDDE")          # JSON 类给 dict
"""
import base64
import json
import os
import struct
import sys
import time
import zlib

BASE = os.path.dirname(os.path.abspath(__file__))

from . import datacenter as dc                              # noqa: E402
from . import pb as tdx_pb                               # noqa: E402

PARSE = os.path.join(BASE, "catalog")

# ctl 候选与类型：**从 pb_specs.json 读**，代码里不硬编码。
# 这样改文档/规格即改行为，只有一份事实来源。
def _load_specs():
    p = os.path.join(PARSE, "pb_specs.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:      # 必须 with：裸 open 会漏文件句柄
        raw = json.load(f)                   # （实测每次 import 漏 1 条 ResourceWarning）
    out = {}
    for k, v in raw.items():
        # 只认命令名（含点号），跳过「说明」「约定」这类元信息块
        if "." not in k or not isinstance(v, dict) or "kind" not in v:
            continue
        out[k] = {"ctl": v.get("ctl") or v.get("ctl_alt") or [],
                  "kind": v["kind"],
                  "cols": v.get("列名") or [],
                  "size": v.get("规模", "")}
    return out


PB = _load_specs()


def load_args():
    """从 page_cmd_params / raw_args 取参数模板。"""
    out = {}
    for fn in ("page_cmd_params.json", "raw_args.json"):
        p = os.path.join(PARSE, fn)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                out.update(json.load(f))
    return out


_ARGS = load_args()


def args_for(cmd):
    """把参数模板转成原始字节。"""
    p = _ARGS.get(cmd)
    if p is None:
        return b""
    if isinstance(p, str):
        if p.startswith("Base64:"):
            return base64.b64decode(p[7:])
        return p.encode("utf-8")
    return json.dumps(p, ensure_ascii=False).encode("utf-8")


def build(ctl, cmd, args):
    """PB 帧：与普通帧一致，`00 00` 分隔符必须保留。"""
    return (b"\x0b" + struct.pack("<I", 0x7000) + bytes([ctl])
            + struct.pack("<H", len(args)) + struct.pack("<H", len(args))
            + struct.pack("<H", 0x264B) + b"\x00\x64"
            + cmd.encode("ascii") + b"\x00\x00" + args)


def _raw_send(ctl, cmd, args, tries=4):
    for k in range(tries):
        try:
            # idle=2.0：首包 2 秒内必到（实测 0.18s）；原来是 5.0，纯空等。
            # 限流闸门在 dc._send 里统一过，这里不再重复等。
            blobs = dc._send(build(ctl, cmd, args), wait=15, idle=2.0)
            if blobs:
                return blobs[0]
        except Exception:                                  # noqa: BLE001
            pass
        if k < tries - 1:
            # ★ 最后一轮不再等：原来 4 轮的最后 8 秒是纯浪费（33s 里有 8s 是它）
            time.sleep(8)
    return None


def decode_any(blob):
    """自动识别 protobuf / JSON / zlib+JSON。

    返回 (kind, payload)：
        ("json", dict|list)
        ("pb",   [(field, wire, value)])
        ("raw",  bytes)
    """
    if not blob:
        return "raw", b""
    # zlib
    if blob[:1] == b"\x78":
        try:
            blob = zlib.decompress(blob)
        except Exception:                                  # noqa: BLE001
            pass
    s = blob.lstrip()
    if s[:1] in (b"{", b"["):
        try:
            return "json", json.loads(s.decode("utf-8", "replace"))
        except Exception:                                  # noqa: BLE001
            pass
    tree = tdx_pb.decode(blob)
    if tree:
        return "pb", tree
    return "raw", blob


def call_pb(cmd, ctl=None, args=None, tries=4):
    """调用 PB 接口。

    返回：
        表类(kind=table)  → (列名列表, [字典行])
        其它             → decode_any 的 payload；失败返回 None
    """
    meta = PB.get(cmd) or {}
    cands = [ctl] if ctl is not None else (meta.get("ctl") or [None])
    if args is None:
        args = args_for(cmd)

    for c in cands:
        if c is None:
            continue
        blob = _raw_send(c, cmd, args, tries=tries)
        if blob is None:
            continue
        kind, payload = decode_any(blob)
        if kind == "raw":
            continue
        if meta.get("kind") == "table" or kind == "pb":
            cols, rows = tdx_pb.parse_table(payload) if kind == "pb" \
                else (None, [])
            if cols:
                return cols, rows
        if kind == "json":
            return payload
        if kind == "pb":
            return payload
    return None


def parse_pb_any(blob):
    """把一段 PB 响应直接转成 (列名, 行) 或 dict。"""
    kind, payload = decode_any(blob)
    if kind == "json":
        return payload
    if kind == "pb":
        cols, rows = tdx_pb.parse_table(payload)
        if cols:
            return cols, rows
        return tdx_pb.to_dict(payload)
    return None


if __name__ == "__main__":
    import collections
    print("PB 接口自检\n")
    stat = collections.Counter()
    for cmd in sorted(PB):
        r = call_pb(cmd)
        if r is None:
            print("  ✗ %-26s 无响应" % cmd.split(".")[-1])
            stat["fail"] += 1
        elif isinstance(r, tuple):
            cols, rows = r
            print("  ✓ %-26s 表 %d 列 × %d 行" % (cmd.split(".")[-1],
                                                len(cols), len(rows)))
            if rows:
                print("       %s" % str(rows[0])[:130])
            stat["table"] += 1
        else:
            n = len(r) if hasattr(r, "__len__") else 0
            print("  ✓ %-26s %s 长度 %d" % (cmd.split(".")[-1],
                                           type(r).__name__, n))
            stat["obj"] += 1
        time.sleep(3)
    print("\n汇总: %s" % dict(stat))
