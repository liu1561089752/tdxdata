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
from . import zliblimit                                  # noqa: E402
from .. import errors                                    # noqa: E402
from .._logging import logger                            # noqa: E402

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


def _raw_send(ctl, cmd, args, tries=2, backoff=3.0):
    """发一帧 PB 命令，返回首个 blob（**失败抛 typed 异常，不再返回 `None`**）。

    失败语义（2026-10 修，`DEF-005`/`DEF-006`）
    --------------------------------------------
    * 原来这里是 `except Exception: pass` + `sleep(8)` × 4 轮：
      ① 把**传输失败折叠成 `None`**，上层再翻成 `EmptyResult("K线无数据")`
         —— 用户以为今天没行情，真相是没网；这是诊断成本最高的一类错；
      ② 一次断网调用要白等 **24 秒**（4 轮里 3 次 `sleep(8)`）才等到那句错话。
    * 现在：只捕**归一化后**的传输类异常（`errors.ConnectionFailed` /
      `errors.ProtocolError`，裸 `OSError` 已在 `dc._send` 里被转掉），
      重试 2 轮 × 3 秒（`dc._send` 自己已经换机重试 2 轮，这里叠 4 轮纯属浪费），
      仍失败则**抛最后一个**，绝不静默返回 `None`。
    """
    last: Exception | None = None
    for k in range(tries):
        try:
            # idle=2.0：首包 2 秒内必到（实测 0.18s）；原来是 5.0，纯空等。
            # 限流闸门在 dc._send 里统一过，这里不再重复等。
            blobs = dc._send(build(ctl, cmd, args), wait=15, idle=2.0)
            if not blobs:
                raise errors.ProtocolError("%s 回包解不出一帧（全为残余字节）" % cmd)
            return blobs[0]
        except (errors.ConnectionFailed, errors.ProtocolError) as exc:
            last = exc
        if k < tries - 1:
            time.sleep(backoff)
    assert last is not None
    raise last


# ══════════════════════════════════════════════════════════════════════════
# 解压硬上限（`DEF-B4-02`，2026-10-28）——**实现已收编到 `zliblimit`**
#
# 缺陷形态：原来这里是 `blob = zlib.decompress(blob)` —— **输出大小完全由回包
# 决定**。一个 800 KB 的 zlib 炸弹就能解出 80 MiB（实测压缩比 > 100:1）。
#
# 为什么搬到 `_engine/zliblimit.py`：同样的缺陷在这条通路上修好后，
# `datacenter.unwrap_frames` / `datacenter._try_zlib` / `hq_extra` 的探测链
# **还各自留着无上限的 `zlib.decompress`**（SEC-2）。四条通路任一没堵住，
# 炸弹就从那条进来。现在上限只有一份实现，本模块保留这两个名字只为**兼容**
# （测试与文档一直按 `pb_api._MAX_DECOMPRESS` 引用），实际逻辑在 zliblimit。
# ══════════════════════════════════════════════════════════════════════════
_MAX_DECOMPRESS = zliblimit.MAX_DECOMPRESS


def _inflate_limited(blob, limit=None, context=""):
    """带上限的 zlib 解压（薄封装，语义见 `zliblimit.inflate_limited`）。

    保留本函数与 `_MAX_DECOMPRESS` 这个名字：既有测试是 monkeypatch
    `pb_api._MAX_DECOMPRESS` 到 1 MiB 来验证"超限即抛"的，这里**每次调用现读**
    本模块的全局值，所以那条测试仍然有效（不是摆设）。
    """
    return zliblimit.inflate_limited(
        blob, _MAX_DECOMPRESS if limit is None else limit, context)


def decode_any(blob, context=""):
    """自动识别 protobuf / JSON / zlib+JSON。

    返回 (kind, payload)：
        ("json", dict|list)
        ("pb",   [(field, wire, value)])
        ("raw",  bytes)

    `context`：只用于**报错文本**（哪个命令、哪个 ctl 的回包出的问题），
    不参与解码判断 —— 运维看到一句带 cmd 的错比看到裸字节数有用得多。
    """
    if not blob:
        return "raw", b""
    # zlib
    if blob[:1] == b"\x78":
        try:
            blob = _inflate_limited(blob, context=context)
        except errors.ProtocolError:
            # 超限是**硬错误**：显式重抛，绝不落进下面的降级探测
            # （旧写法是 `except Exception` 全吞 —— 炸弹会因此变回静默）。
            raise
        except zlib.error as exc:
            # 编码探测链：「声明 zlib 实则不是」→ 继续走明文/protobuf 探测，
            # 不是错误；debug 留编码选择的轨迹（诊断畸形帧用）。
            logger.debug("zlib probe failed (%s), continuing decode chain",
                         type(exc).__name__, exc_info=True)
    s = blob.lstrip()
    if s[:1] in (b"{", b"["):
        try:
            return "json", json.loads(s.decode("utf-8", "replace"))
        except Exception as exc:                           # noqa: BLE001
            # 编码探测链：形似 JSON 实则不是（utf-8 replace 后仍非法）→
            # 落到 protobuf 解码。debug 留轨迹。
            logger.debug("payload looks-like-json but invalid (%s), "
                         "trying protobuf", type(exc).__name__, exc_info=True)
    tree = tdx_pb.decode(blob)
    if tree:
        return "pb", tree
    return "raw", blob


def call_pb(cmd, ctl=None, args=None, tries=4):
    """调用 PB 接口。

    返回：
        表类(kind=table)  → (列名列表, [字典行])
        其它             → decode_any 的 payload

    ⚠️ DEF-043（2026-11 收敛）：**「答了但解不出」不再静默返回 `None`**。
    旧行为：所有候选 ctl 的回包都解不出（kind="raw"）或没有可用候选时
    `return None` —— 与「服务端无响应」不可区分，上层只能把它当"没数据"。
    新行为：抛 `errors.EmptyResult`（消息里带 cmd，且明说"回包解不出"，
    与"当期无数据"在文本上可区分；异常类型沿用 `EmptyResult` 是为了
    **上层零修改** —— `client.py` / `hq_pb` 现有捕 `EmptyResult` 的批量遍历
    路径行为不变，只是错误消息更诚实）。
    「服务端无响应」仍走传输路径抛 `ConnectionFailed` / `ProtocolError`
    （批次 A 已保证），两态由此彻底可区分。

    DEF-041（只登记不修）：有 ctl → Table / 无 ctl → dict 的双重载返回
    类型是历史形状，签名重构归架构批。
    DEF-022（已修，2026-11）：`Table`/`Row`/`Dataset` 的 `repr` 现在带硬上限，
    见 `models.py`；这里不再提"另批处理"。
    """
    meta = PB.get(cmd) or {}
    cands = [ctl] if ctl is not None else (meta.get("ctl") or [None])
    if args is None:
        args = args_for(cmd)

    last_err: Exception | None = None
    for c in cands:
        if c is None:
            continue
        try:
            blob = _raw_send(c, cmd, args, tries=tries)
        except (errors.ConnectionFailed, errors.ProtocolError) as exc:
            # 传输层失败：换下一个候选 ctl 再试。全部候选都失败才上抛 ——
            # 绝不能把"连不上"折叠成 `return None`（那会被上层当成"没数据"）。
            last_err = exc
            continue
        # context 只进报错文本：解压超限时要能指出是「哪个 cmd、哪个 ctl」的回包
        kind, payload = decode_any(blob, "%s ctl=0x%02X 的" % (cmd, c))
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
    if last_err is not None:
        raise last_err
    raise errors.EmptyResult(
        "%s 回包解不出（所有候选 ctl 的响应都不是可识别的表/JSON/PB 结构；"
        "不是\"当期无数据\"，请抓包核协议）" % cmd)


def parse_pb_any(blob):
    """把一段 PB 响应直接转成 (列名, 行) 或 dict。"""
    kind, payload = decode_any(blob, "parse_pb_any 的")
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
        try:
            r = call_pb(cmd)
        except Exception as exc:                           # noqa: BLE001
            print("  ✗ %-26s %s" % (cmd.split(".")[-1], exc))
            stat["fail"] += 1
            time.sleep(3)
            continue
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
