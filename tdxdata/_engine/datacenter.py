# -*- coding: utf-8 -*-
"""通达信数据中心采集器（7615 协议）。

⚠️ 2026-10-22 本文件因一次误操作被截断，此为**重建版**。
重建依据：调用方接口约定（fetch_* / save /
last_trading_day / prev_trading_day）+ command_db.json 里的 ctl/参数实测值。

帧格式
------
    0b | msgid(4,LE) | ctl(1) | len(2,LE) | len(2,LE) | type(2,LE)
       | 00 64 | <命令名 ASCII> | 00 00 | <参数 JSON>

- `len` = **参数 JSON 的字节数**
- `type`: 0x264B=QUERY, 0x26B1=心跳, 0x26AC=CONNECT, 0x26AD=LOGIN
- `ctl` 是**服务端命令分发键**：错了不报错，只返回空包。取值 0x0E~0x2B。

响应解码
--------
1. 每个包体是 zlib 流或明文
2. 明文 JSON 可能是 **GBK**（中文参数/中文列名），盲目 UTF-8 会得到乱码
   （GBK 的 `元` = 0xD2AA 恰好是合法 UTF-8，会解成 `աԪ`）
3. 两种包头键序都要认：
       CWServ.*          -> {"Length": "909", "AttachLog": ""}
       DataAggregation.* -> {"AttachLog": "", "Length": "1293"}

两种响应体
----------
- 标准: {"ResultSets": [{"ColName":[...], "ColDes":[...], "Content":[[...]]}]}
- 4 段式: [meta, [列名...], [], [单行值...]]   ← DataAggregation / AppUpdateInfo
"""
from __future__ import annotations

import atexit
import json
import re
import socket
import struct
import sys
import threading
import time
import zlib
from datetime import datetime, timedelta, timezone

from .. import errors
from .._logging import logger
from . import zliblimit

CST = timezone(timedelta(hours=8))

# 四台 7615 服务器。限流是**按来源 IP**（不是「IP × 服务器」）算的 ——
# 轮换只能**躲开单台过热**，**不能放大配额**。2026-10-27 实测（阶段 5）：
# 4 条 lane 各钉一台主机并发取数，头 5 秒就有 3 条连接被重置
# （limited_events 0→3、3 台进冷却、175 s 内 0 次成功取数），
# 换成 4 台不同服务器并没有换来 4 份配额。
# 证据：`F:\TDX\reports\refactor\probe\phase5\CONCLUSION.md`（原始输出同目录）。
SERVERS = ["121.5.164.43", "124.223.177.105", "121.37.180.65", "60.204.149.166"]
_SRV_I = [0]

TYPE_QUERY = 0x264B

# 两种包头键序都要认（曾因只认 "Length" 在首位而漏掉全部 DataAggregation 响应）
HDR_RE = re.compile(rb'\{[^{}]*"Length":\s*"(\d+)"[^{}]*\}')


# ── 基础工具 ────────────────────────────────────────────────
def now_iso() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def _pct(cur, prev, digits=2):
    try:
        if cur is None or not prev:
            return None
        return round((float(cur) / float(prev) - 1) * 100, digits)
    except Exception:
        return None


_COMMON_HANZI: frozenset | None = None


def _common_hanzi() -> frozenset:
    """GB2312 一级+二级汉字表（6763 字），**懒构造**（只算一次，约 10 ms）。

    用途见 `_cjk_score()`：判断"这段文本像不像真的简体中文"。
    不在 import 期构造：`import tdxdata` 的耗时不该被一张字表拖走。
    """
    global _COMMON_HANZI
    if _COMMON_HANZI is None:
        chars = set()
        for hi in range(0xB0, 0xF8):
            for lo in range(0xA1, 0xFF):
                try:
                    ch = bytes((hi, lo)).decode("gb2312")
                except UnicodeDecodeError:
                    continue
                if len(ch) == 1:
                    chars.add(ch)
        _COMMON_HANZI = frozenset(chars)
    return _COMMON_HANZI


def _cjk_score(text: str) -> int:
    """给一段文本的「像中文」程度打分，用于在多种解码里择优。

    两个方向都会翻车，必须**同时**防（DEF-012）：

    * **GBK 字节碰巧是合法 UTF-8** —— `元`(0xD2AA) 解成亚美尼亚字母 `աԪ`。
      靠对亚美尼亚/西里尔/拉丁扩展区**扣分**识别。
    * **UTF-8 字节被当成 GBK** —— `上证指数` 解成 `涓婅瘉鎸囨暟`（每个汉字变两个
      罕用汉字，字符数还翻倍）。旧版「汉字 +3」会让**乱码分数更高**（6×3=18 > 4×3=12），
      于是长串必错、短串碰巧对 —— 正是评审里"随字符串长度翻转"的机制。
      靠**GB2312 常用字表**识别：真简体中文几乎全部落在 6763 字内，
      而乱码产出的罕用字（`涓`/`瘉`/`鎸`…）大多不在其中。

    所以汉字的权重是分级的：**常用汉字 +3 / 罕用汉字 −1**。绝不能用"凡是汉字都 +3"，
    那等于给乱码发奖励。
    """
    common = _common_hanzi()
    sc = 0
    for ch in text:
        o = ord(ch)
        if ch in common:
            sc += 3                                   # GB2312 常用汉字：真中文的强证据
        elif 0x4E00 <= o <= 0x9FFF:
            sc -= 1                                   # 罕用汉字：乱码的高发区
        elif 0x3040 <= o <= 0x30FF:
            sc += 1
        elif 0x0530 <= o <= 0x058F:
            sc -= 3
        elif 0x0400 <= o <= 0x04FF:
            sc -= 3
        elif 0x0100 <= o <= 0x024F:
            sc -= 2
        elif o < 0x20 and ch not in "\r\n\t":
            sc -= 1
    return sc


def prev_trading_day(day: str | None = None) -> str:
    """上一个工作日（不查节假日，仅跳周末）。"""
    if day is None:
        day = datetime.now(CST).strftime("%Y-%m-%d")
    d = datetime.strptime(day[:10], "%Y-%m-%d")
    while True:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            return d.strftime("%Y-%m-%d")


def last_trading_day() -> str:
    """最近一个交易日。周末回退到周五；15:00 前算上一交易日。"""
    now = datetime.now(CST)
    d = now
    if d.weekday() >= 5:
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d.strftime("%Y-%m-%d")
    if now.hour < 15:
        return prev_trading_day(d.strftime("%Y-%m-%d"))
    return d.strftime("%Y-%m-%d")


def save(fn: str, obj) -> None:
    """原子写 JSON。"""
    import os
    tmp = fn + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, fn)


# ── 握手 / 帧 ──────────────────────────────────────────────
def fresh_handshake(mid: int = 0x7000) -> bytes:
    """真实握手字节流（CONNECT 0x26AC + LOGIN 0x26AD + ACL）。

    必须用抓包得到的模板：服务端会校验 ACL/nonce 等字段，
    自己拼一个裸 CONNECT 帧会被直接断开（返回 0 字节）。

    模板来源（按优先级）：
      1. `tdx_handshake.py` —— 自包含模块，握手字节以 base64 内嵌（**推荐，零外部资产**）
      2. `tdx_hq.load_handshake()` —— 从 `assets/cw_template.bin` 截取（旧路径，可选）
      3. 兜底：裸 CONNECT 帧（**服务端会拒绝，仅供调试**）
    """
    try:
        from . import handshake as _t
        hs = _t.get()
        if hs:
            return hs
    except Exception as exc:                               # noqa: BLE001
        logger.debug("handshake template path 1 failed (%s), trying next",
                     type(exc).__name__, exc_info=True)
    try:
        from . import handshake as _h
        hs = _h.get()
        if hs:
            return hs
    except Exception as exc:                               # noqa: BLE001
        logger.debug("handshake template path 2 failed (%s), fallback to "
                     "built-in frame", type(exc).__name__, exc_info=True)
    return build_frame(mid, 0x01, 0x26AC, b"")


def unwrap(buf: bytes) -> list[bytes]:
    """从原始字节里切出每个包体（zlib 流或明文 JSON）。

    先按 HDR_RE 找 {"Length": "N"} 定位包体，找不到再按 0x78 的 zlib 魔数扫。
    """
    out: list[bytes] = []
    if not buf:
        return out
    pos = 0
    while True:
        m = HDR_RE.search(buf, pos)
        if not m:
            break
        try:
            ln = int(m.group(1))
        except Exception:
            pos = m.end()
            continue
        start = m.end()
        hit = False
        for off in (0, 1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64):
            s = start + off
            if s + ln > len(buf):
                break
            blob = buf[s:s + ln]
            z = _try_zlib(blob)
            if z is not None:
                out.append(z)
                pos = s + ln
                hit = True
                break
            if blob[:1] in (b"{", b"[") and _looks_json(blob):
                out.append(blob)
                pos = s + ln
                hit = True
                break
        if not hit:
            pos = m.end()
    if out:
        return out
    for m in re.finditer(rb"\x78[\x01\x5e\x9c\xda]", buf):
        z = _try_zlib(buf[m.start():])
        if z:
            out.append(z)
    return out


def unwrap_frames(buf: bytes) -> list[bytes]:
    """按 7615 **帧头**切包 —— 只能给 `unwrap()` 当兜底（D6，2026-10-27）。

    为什么需要它
    ------------
    服务端会按 payload 大小自己决定压不压缩：

    | 场景 | 首字节 | `len` | body | `unwrap()` |
    |---|---|---|---|---|
    | payload 大 | `0x3B` | 527 | **zlib** | ✅ 靠 `0x78 9C` 魔数扫到 |
    | payload 小 | `0x0B` | 385 | **明文 protobuf** | ❌ 既无 JSON 头、也无 zlib 魔数 → 返回 `[]` |

    后者以前被当成"传输失败"，于是 `_raw_send` 白等 `sleep(8)` × 4 轮 ≈ 35 秒，
    用户看到的是「K线无数据」。实际上包里**一直有数据**（实测 `113711` 明文帧
    解出 2 根真实成交，末根 139.751）。

    帧结构（响应方向，两种形态一致，逐字节 dump 确认）：

        0      0x0B 明文 / 0x3B zlib      ← 压缩标志位 0x30
        1-4    msgid (LE)
        5      ctl
        6-7    **wire 长度** (LE)          ← body 字节数，两种形态都是它
        8-9    解压后长度 (LE)             ← 明文时与 6-7 相等
        10-11  0x264B（"K&"）
        12-13  0x0064
        14-…   body

    ⚠️ **只能当兜底，不能当主路径**：JSON 命令（CWServ 那条）的 `len` 是**参数长度**
    而不是 body 长度，按这个规则切会切错。所以只在 `unwrap()` 返回空时才调用。
    即使误切也不会更糟：`call_pb` 的 `decode_any()` 会给出 `kind == "raw"` 然后跳过。

    ⚠️ **`len1`/`len2` 不可靠**（2026-10-27 交接包 §6.6 实测）：长 body 跨 TCP 段时
    帧头里的长度会比实际小，照它切会**把响应截断**（`PBCombHQ` 传 6 个代码时整张表被切掉）。
    所以切法是「**切到下一个合法帧头为止，最后一段取到流末尾**」，完全不看 `len`。
    代价：真被截断的响应也会被当成完整 body —— 由各命令的解析器（JSON/protobuf 校验）兜底拒绝。
    """
    out: list[bytes] = []
    i = 0
    n = len(buf)
    while i + 14 <= n:
        if not _is_frame_head(buf, i):
            i += 1
            continue
        # 往后找下一个帧头；找不到就认为这一段到流末尾
        nxt = n
        for j in range(i + 14, n - 13):
            if _is_frame_head(buf, j):
                nxt = j
                break
        body = buf[i + 14:nxt]
        if buf[i] == 0x3B:                 # 帧头声明 body 是 zlib
            try:
                body = zliblimit.inflate_limited(
                    body, context="帧头 0x3B 声明 zlib 的")
            except errors.ProtocolError:
                # 解压超硬上限 = 我方内存要被对端决定，**不降级、不原样交出**
                # （原样交出去等于把炸弹字节传给下游 decode_any）。直接上抛。
                raise
            except zlib.error as exc:
                # 解不开就原样交出去，让上层报错 —— 但留 debug 轨迹：
                # 「声明 zlib 实则不是」是解码错位的头号嫌疑。
                logger.debug("declared-zlib body failed to decompress (%s), "
                             "passing raw", type(exc).__name__, exc_info=True)
        out.append(body)
        i = nxt
    return out


def _is_frame_head(buf: bytes, i: int) -> bool:
    """`i` 处是不是一个 7615 帧头（三重校验，尽量别在随机字节上误命中）。

    ⚠️ 标记位有**两种**（2026-10-29 实测，别再只认一种）：

    | 协议 | `buf[i+10:i+12]` | 出现在 |
    |---|---|---|
    | protobuf（新版） | `4b 26`（即 `"K&"`） | `HQServ:PBFXT` 等 |
    | **经典二进制** | **`b4 26`** | `0x122E` 期货 K 线等 |

    只认前者时经典回包被整段丢掉 → 期货 K 线遇到 `count=10` 这种小回包会
    **静默返回空表**（实测 527 B 明文帧，body 明明就在里面：
    `0b 00700000 00 0102 0102 b426 0064 …`）。
    两个字节顺序颠倒不是笔误，是两条协议各自的实测事实。
    """
    if buf[i] not in (0x0B, 0x3B):           # 0x0B 明文 / 0x3B zlib
        return False
    return (buf[i + 10:i + 12] in (b"K&", b"\xb4\x26")
            and buf[i + 12:i + 14] == b"\x00d")


def _try_zlib(blob: bytes):
    """按 `0x78` 魔数猜"这段是 zlib"并试着解（**有硬上限**，SEC-2）。

    返回 `None` = "不是 zlib / 解不出"（探测语义，调用方继续找别的包体边界）；
    超硬上限则抛 `ProtocolError` —— 那是攻击/畸形，不该被当成"猜错"。
    """
    try:
        t = zliblimit.inflate_limited(blob, context="按 0x78 魔数扫到的")
        return t if len(t) > 1 else None
    except errors.ProtocolError:
        raise
    except zlib.error:
        return None


def _looks_json(blob: bytes) -> bool:
    for enc in ("utf-8", "gbk"):
        try:
            json.loads(blob.decode(enc))
            return True
        except Exception:
            continue
    return False


def decode_json(raw: bytes):
    """解码**一段**包体并解 JSON，返回 `(obj, text)`；择优依据是"哪个编码更像中文"。

    入参是 `call_raw()` 返回值的**一个元素**（即 `raw[i]`），
    **不要再 unwrap / 不要再解包** —— 传整个 `list[bytes]` 会直接解失败。

    为什么不能盲试 UTF-8
    --------------------
    GBK 的 `元` = `0xD2AA` 恰好是**合法**的 UTF-8 字节序列，盲解会得到 `աԪ` 这种
    乱码但"没有报错"的文本。所以这里对 `utf-8` / `gbk` / `gb18030` 逐个尝试，
    取 `_cjk_score(text)` **最高**的那个结果（CJK 字符占比）—— 名叫"按像中文得分择优"。

    返回
    ----
    `(obj, text)` : `tuple[Any, str | None]`
        `obj`  = `json.loads` 的结果（dict / list / 标量皆可能）；
        `text` = 被选中的那版解码文本，便于排查原始形态。
        三种编码**全都**解不出时返回 `(None, None)`（不抛异常，由调用方判空）。
    """
    best = (None, None, None)
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            t = raw.decode(enc)
            j = json.loads(t)
        except Exception:
            continue
        sc = _cjk_score(t)
        if best[2] is None or sc > best[2]:
            best = (j, t, sc)
    return best[0], best[1]


def build_frame(mid, ctl, typ, cmd: bytes, args: bytes | None = None) -> bytes:
    if args is None:
        return (b"\x0b" + struct.pack("<I", mid) + bytes([ctl])
                + struct.pack("<H", len(cmd)) + struct.pack("<H", len(cmd))
                + struct.pack("<H", typ) + b"\x00\x64" + cmd)
    return (b"\x0b" + struct.pack("<I", mid) + bytes([ctl])
            + struct.pack("<H", len(args)) + struct.pack("<H", len(args))
            + struct.pack("<H", typ) + b"\x00\x64"
            + cmd + b"\x00\x00" + args)


# ── PB 路径的持久连接（2026-10-26 D2 阶段 B）──────────────────────────────
# 每次调用都「握手 → sleep 1.3s → 预排空」是 8.35 秒的大头。握手后复用同一条
# 连接再发一次 PBFXT 实测只要 **0.176 秒**（与首发 0.175s 一致），所以长连接完全可用。
# 另一个收益：**降低建连频率**。实测每 2.2 秒新建一次连接时，约 1/6 会在
# `sendall` 阶段被服务端 `WinError 10053` 掐掉；复用后这类中止消失。
_CONN: dict = {"sock": None, "host": None, "t": 0.0}
_CONN_LOCK = threading.RLock()
_PB_IDLE_RECONNECT = 90.0      # 与 Session.IDLE_RECONNECT 对齐（服务端会静默重置空闲连接）

#: 「PB 通路优先连哪台」。`None` = 内置轮询。
#:
#: ⚠️ 为什么是**进程级**而不是实例级：PB 通路是**一条进程级持久连接**
#: （`_CONN` 本来就是模块级单例，两个 `Client` 共用同一根 socket）。
#: 既然连接本身没有"实例"可言，主机偏好也只能是进程级的 ——
#: 后一个带 `host=` 的 `Client` 会覆盖前一个（`set_preferred_host` 会丢旧连接，
#: 否则"钉住"就成了一句假话）。
_PREFERRED_HOST: str | None = None


def set_preferred_host(host) -> None:
    """登记「PB 通路优先连哪台」（`Client(host=...)` 调它）。

    实测背景（`DEF-048`）：`Client(host="10.0.0.5")` 原来只影响
    `Session`（数据中心 JSON 通路），PB 通路（K 线 / 分时 / 快照 / 逐笔 /
    涨跌家数 / 分页）**完全无视它**，照样连内置的 `SERVERS` ——
    用户以为钉住了某台机器（镜像、代理、抓包目标），实际流量跑去了别处，
    且没有任何提示。

    换目标时**必须丢掉已有连接**：`_pb_conn()` 命中空闲长连接会直接复用，
    不丢就会"继续用旧主机"，等于没生效。
    """
    global _PREFERRED_HOST
    with _CONN_LOCK:
        new = (host or None)
        if new != _PREFERRED_HOST:
            _PREFERRED_HOST = new
            _drop_pb_conn()


def preferred_host() -> str | None:
    """当前 PB 通路的主机偏好（排查/测试用）。"""
    return _PREFERRED_HOST


def _drop_pb_conn(state=None) -> None:
    """丢掉 PB 持久连接（幂等，可在锁内嵌套调用）。

    ARCH-001：`state=None` = 模块级 `_CONN`（旧行为）；传实例级 state dict
    则只丢那一条（实例化过渡缝，全局连接不动）。
    """
    if state is None:
        state = _CONN
    with _CONN_LOCK:
        s = state["sock"]
        state.update(sock=None, host=None, t=0.0)
    if s is not None:
        try:
            s.close()
        except Exception as exc:                   # noqa: BLE001
            logger.debug("stale socket close failed (%s)",
                         type(exc).__name__, exc_info=True)


atexit.register(_drop_pb_conn)


def _pb_conn(host=None, *, drain=0.25, state=None):
    """取一条**已握手**的 PB 连接；空闲超时/换机时重建。

    走持久连接时 `sleep(1.3)` + 预排空只付一次，之后每次调用只花 0.18 秒左右。

    `host=None` 时优先用 `set_preferred_host()` 登记的偏好（= `Client(host=…)`），
    没有偏好才按 `SERVERS` 轮询。

    ARCH-001：`state=None` = 模块级 `_CONN`（旧行为，进程级单例）；传实例级
    state dict（与 `_CONN` 同构：`{"sock","host","t"}`）则读写那条 ——
    这是「`_CONN` 从单例向实例化过渡」的第一步缝，全局默认行为不变。
    """
    if state is None:
        state = _CONN
    with _CONN_LOCK:
        s = state["sock"]
        if s is not None and (time.time() - state["t"]) < _PB_IDLE_RECONNECT:
            return s
        _drop_pb_conn(state)
        if host is None:
            host = _PREFERRED_HOST
        if host is None:
            host = SERVERS[_SRV_I[0] % len(SERVERS)]
            _SRV_I[0] += 1
        s = socket.create_connection((host, 7615), timeout=12)
        try:
            s.sendall(fresh_handshake())
            time.sleep(1.3)                # 服务端强制，不能省
            # 预排空握手残余：首包 14ms 就到，0.25s 足够；残余由 unwrap 的扫描容忍
            s.settimeout(drain)
            try:
                while s.recv(262144):
                    pass
            except Exception as exc:               # noqa: BLE001
                # 超时/对端关闭都是排空的**正常终止**，不是错误；
                # debug 只为诊断「握手残余异常多」这种怪象留轨迹。
                logger.debug("pre-drain terminated (%s)", type(exc).__name__,
                             exc_info=True)
        except Exception:
            try:
                s.close()
            except Exception as exc:               # noqa: BLE001
                logger.debug("socket close during connect cleanup failed (%s)",
                             type(exc).__name__, exc_info=True)
            raise
        state.update(sock=s, host=host, t=time.time())
        return s


def close_pb() -> None:
    """主动关闭 PB 持久连接（`Client.close()` / 进程退出时调）。"""
    _drop_pb_conn()


def _send(frame: bytes, host=None, wait=15.0, idle=2.0,
          *, drain=0.25, tail=0.15, conn=None):
    """发一帧、收齐回包，返回包体列表（已 `unwrap`）。

    ⚠️ 这是 **PB 路径的唯一咽喉**（`pb_api._raw_send` → 这里）。
    数据中心那条路（`Client.call`）走的是 `Session.ask` 自己的两段式读，不经过本函数。

    2026-10-26 重写（缺陷 D2）。原实现单次 **8.35 秒**，其中约 6.8 秒是纯等待：

    | 环节 | 原来 | 现在 | 说明 |
    |---|---|---|---|
    | `pacer.wait()` | **没有** | 有 | PB 路径原来完全绕过限流（难怪慢也没被踢） |
    | `sleep(1.3)` 等握手包 | 每次 1.30s | **只在重连时 1.30s** | 服务端强制，不等就发立刻 `10053`（实测复现） |
    | 预收包等超时 | 2.00s | **0.25s**（仅重连） | 握手首包实测 14ms 就到齐 |
    | 发请求→收齐响应 | 0.18s | 0.18s | 必须成本，省不掉 |
    | 收包循环靠 `idle` 超时退出 | 5.00s | **首包 2.0s / 之后 0.15s 静默** | 响应 0.18s 就收完，剩下的是空等 |

    实测（2026-10-26，600519 日线）：**8.35s → 首次 1.79s / 稳态 2.19s**（稳态被 pacer 的
    2.2s 地板兜住）。数值回归：改前改后各取 120 根日线，7 个字段**逐位全等**。

    ⚠️ `drain=0.25` 小于原来的 2.0，是"够快"和"别把握手残余字节当回包"之间的取舍 ——
    `unwrap()` 按 `{"Length":N}` 扫描定位，对残余有容忍度；但**数值必须比对过**才算数。

    失败处理：连接类异常/空回包 → 丢弃连接，**重试 1 次**（换一台服务器），仍失败则上抛。
    ⚠️ 空回包**上抛 `ConnectionError`**，不返回 `[]` —— 协议里"无数据"是"合法但 0 行的表"，
    空字节只可能是连接被掐（诊断实测就是 `10053`）。两者混在一起调用方分不清。
    """
    from .pacer import get_pacer

    # ARCH-001：`conn=` 显式指定 PB 通道实例（_CONN 实例化过渡缝）。
    # `None` = 旧行为：模块级 `_CONN` dict，行为逐位不变。
    global _CONN
    if conn is None:
        _conn_state = _CONN
    elif isinstance(conn, dict):
        _conn_state = conn          # 容忍 dict 形状的实例级通道（与 _CONN 同构）
    else:
        _conn_state = conn.state()  # PBChannel 实例 → 同构 dict 视图
    _conn_lock = getattr(conn, "lock", None) or _CONN_LOCK

    get_pacer().wait()                     # ★ 进程级匀速闸门，PB 路径原来没有

    last: Exception | None = None
    for attempt in (0, 1):
        try:
            with _conn_lock:
                # `state` 只在显式实例通道时传（既有的 `_pb_conn` 注入桩是
                # `(host, drain)` 签名，多传 kwarg 会炸 —— 默认路径保持原样）
                if _conn_state is _CONN:
                    s = _pb_conn(host if attempt == 0 else None, drain=drain)
                else:
                    s = _pb_conn(host if attempt == 0 else None, drain=drain,
                                 state=_conn_state)
                s.sendall(frame)
                s.settimeout(idle)         # 首包最多等 idle（默认 2.0）
                buf = b""
                t0 = time.time()
                while time.time() - t0 < wait:
                    try:
                        c = s.recv(262144)
                    except socket.timeout:
                        break
                    if not c:              # 服务端关了连接
                        break
                    buf += c
                    s.settimeout(tail)     # ★ 之后只要 tail 秒静默就收工
                    t0 = time.time()
                _conn_state["t"] = time.time()   # 记活跃时间，供空闲重连判断
            if not buf:
                # 空回包不是"没有数据"，是连接被掐了（诊断实测就是 10053）——
                # 按连接异常处理：丢弃这条连接，让下面的统一重试换一条新的。
                # 绝不返回 []，否则会跟"合法的空表"混在一起，调用方分不清。
                raise ConnectionError("PB 回包为空（连接疑似被服务端中止）")
            blobs = unwrap(buf)
            if not blobs:
                # ★ 兜底：服务端对较小的 payload 发明文 protobuf（首字节 0x0B），
                # unwrap() 认不出它 —— 以前就这样被当成传输失败，白等 8s × 4 轮。
                blobs = unwrap_frames(buf)
            return blobs
        except (ConnectionError, OSError) as exc:
            last = exc
            _drop_pb_conn(state=_conn_state)
    # 两轮都失败 → 上抛，不假装成功。
    #
    # ★ 必须**归一化到 errors 体系**（2026-10 修）：原来这里是 `raise last`，
    # 于是 `get_quotes` / `get_ticks` / `get_name` / `get_board_members` 在网络
    # 故障时抛的是裸 `ConnectionRefusedError` / `ConnectionError` ——
    # 用户的 `except tdxdata.TdxError` **接不住**，而离线/防火墙/代理恰恰是
    # 最常见的运行环境。传输层只允许抛 ConnectionFailed/Timeout（errors 的纪律）。
    if last is None:
        raise RuntimeError("PB 发送两轮既没返回也没异常，逻辑有洞")
    raise errors.ConnectionFailed(
        "数据中心通道发送失败（已换机重试 2 轮）：%s: %s"
        % (type(last).__name__, last)) from last


def call(cmd, ctl, params, mid=0x7000, wait=22.0, idle=4.0, host=None):
    """发一条 {"Params": [...]} 命令，返回包体列表。"""
    _enc = "gbk" if any(isinstance(x, str) and not x.isascii()
                        for x in (params or [])) else "utf-8"
    try:
        args = json.dumps({"Params": params}, ensure_ascii=False).encode(_enc)
    except Exception:
        args = json.dumps({"Params": params}, ensure_ascii=False).encode("utf-8")
    if isinstance(cmd, str):
        cmd = cmd.encode("ascii")
    return _send(build_frame(mid, ctl, TYPE_QUERY, cmd, args),
                 host=host, wait=wait, idle=idle)


def call_raw(cmd: str | bytes, ctl: int, body: bytes,
             host: str | None = None, wait: float = 8.0,
             idle: float = 2.5) -> list[bytes]:
    """发裸 JSON 参数（DataAggregation 用裸数组，如 `b"[{}]"`），返回**已切好的 blob 列表**。

    返回契约（**这是本库最容易误读的一处**）
    ----------------------------------------
    `-> list[bytes]`：每个元素是**一段包体正文**（明文 JSON，或 zlib 压缩过的 JSON）。
    切帧/`unwrap`/解压**都已经在这里做完了**。

    ★ **不要再 unwrap / 不要再切帧 / 不要再解包**。
      `raw` 直接喂给下面的解析函数即可；`raw[0]` 才是"一段"，
      `decode_json` 收的也是"一段"，不是整个 `raw`。

    正例（照抄这一段的形态就够了）:

    .. code-block:: python

        raw = call_raw("DataAggregation.queryCardInfo", 31, body)
        rows  = parse_resultset(raw)      # 拉平成 [{列名: 值}]
        tabs  = parse_tables(raw)         # 保留多表：[(key, 列名, [行])]
        obj, text = decode_json(raw[0])   # 单段解 JSON：(obj, text)

    错误用法会造成什么
    ------------------
    历史事故：调用方以为 `call_raw` 返回的是**原始帧字节**或**单段 bytes**，
    于是自己又写了一遍 unwrap/切帧/解包。结果是在已经切好的 blob 上做二次解包，
    必然解不出东西 —— 于是一批**本来能出数据的好命令**被误判成"取不到数据"
    （当时误判了 57 条）。这不是服务端没数据，是解析层自己把数据丢了。
    症状特征：命令在"实测能出数据"的清单里，但本地跑出来永远是空；换成
    `parse_resultset(raw)` 立刻就有行。

    参数
    ----
    cmd : `str | bytes`
        命令名（ASCII）；`str` 会按 ASCII 编码。
    ctl : `int`
        服务端命令分发键。⚠️ 见 `docs/ERROR-CODES.md`：**空请求体时填错 ctl
        服务器也不报 -1002**，所以不能靠"没报 -1002"反证 ctl 填对了。
    body : `bytes`
        裸请求体。形态必须与命令的实测形态一致（`base` / `call` / `bare`），
        否则服务端可能只回空包。
    host : `str | None`
        指定主机；`None` = 内置轮询。
    wait, idle : `float`
        收包超时/静默超时（秒）。

    失败
    ----
    传输层失败上抛 `ConnectionFailed` / `Timeout`（已换机重试 2 轮）；
    空回包**不返回 `[]`**，而是当连接被掐处理 —— "合法但 0 行"与"没收到"必须分得开。
    """
    if isinstance(cmd, str):
        cmd = cmd.encode("ascii")
    return _send(build_frame(0x7000, ctl, TYPE_QUERY, cmd, body),
                 host=host, wait=wait, idle=idle)


def _colnames(raw) -> list[str]:
    """把 ColName / ColDes 归一化成字符串列表。

    ColDes 实际是 **dict 列表**: [{"Name":"N018"},{"Name":"N100"},...]
    （早期误当成字符串列表，导致 dict(zip(...)) 的键全变成 dict 对象）
    """
    out: list[str] = []
    for x in raw or []:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            out.append(str(x.get("Name") or x.get("name") or x.get("ColName") or ""))
        else:
            out.append(str(x))
    return out


def parse_resultset(blobs) -> list[dict]:
    """把标准 ResultSets 响应拉平成 `[{列名: 值}]`（单表/多表全混在一起）。

    入参是 `call_raw()` 的**返回值本身**（`list[bytes]`）：
        `parse_resultset(call_raw(cmd, ctl, body))`
    **不要再 unwrap / 不要再逐段解包** —— 本函数内部已经对每个 blob 调 `decode_json`。
    传单段 `bytes` 进去会被当成"没有 blob"而返回 `[]`（不会报错，静默丢数据）。

    列名取法
    --------
    优先 `ColDes`（真实的业务列名，形如 `[{"Name":"N018"}, ...]`），
    但只有**与 `ColName` 等长**时才采信；`ColDes` 为空或长度对不上时
    **回退到 `ColName`**（如北向 `bxxj` 序列就是 ColDes 为空的情形）。
    两者都空 → 该表的行全部丢弃（没有列名就没法给字段命名）。

    行的取舍：`Content` 里长度与列数**不等**的行被跳过（防止错位配列）。

    需要区分"哪张表"或要跳过元数据小表时，用 `parse_tables` / `parse_main` 代替。
    """
    rows: list[dict] = []
    for blob in blobs or []:
        j, _t = decode_json(blob)
        if not isinstance(j, dict):
            continue
        for rs in j.get("ResultSets") or []:
            if not isinstance(rs, dict):
                continue
            names = _colnames(rs.get("ColName"))
            descs = _colnames(rs.get("ColDes"))
            cols = descs if (descs and len(descs) == len(names)) else names
            if not cols:
                cols = descs or names
            for line in rs.get("Content") or []:
                if isinstance(line, list) and len(line) == len(cols):
                    rows.append(dict(zip(cols, line)))
    return rows


def parse_tables(blobs) -> list[tuple]:
    """按 ResultSets **分组**返回 `[(key, 列名, [行])]`，每张表各占一项。

    入参是 `call_raw()` 的**返回值本身**（`list[bytes]`）：
        `parse_tables(call_raw(cmd, ctl, body))`
    **不要再 unwrap / 不要再逐段解包** —— 内部已对每个 blob 调 `decode_json`。

    与 `parse_resultset` 的唯一区别：那个把多张表**拉平混在一起**，
    这个**保留每张表的边界**（元素是三元组：`ResultSetKey`、列名列表、行列表）。
    元组里的 `key` 取 `rs["ResultSetKey"]`，缺省为空串 `""`；列名取法与
    `parse_resultset` 相同（`ColDes` 等长则优先，否则回退 `ColName`）。

    典型场景（`ph_agf10_cw_lyb` 利润表）一次返回多张表：
        table0: 1 行 1 列 `{isjr: 0}`   ← 元数据
        table1: 24 行 38 列             ← 真实数据
    只要最大那张表时用 `parse_main(blobs)`。

    ⚠️ 返回的**元素数** = 表数，不是行数；"`Content` 为空的表"也会占一项
    （`[行]` 为空列表），所以判"有没有数据"要看 `len(t[2])`，不能只看 `len(out)`。
    """
    out: list[tuple] = []
    for blob in blobs or []:
        j, _t = decode_json(blob)
        if not isinstance(j, dict):
            continue
        for rs in j.get("ResultSets") or []:
            if not isinstance(rs, dict):
                continue
            names = _colnames(rs.get("ColName"))
            descs = _colnames(rs.get("ColDes"))
            cols = descs if (descs and len(descs) == len(names)) else names
            if not cols:
                cols = descs or names
            rows = []
            for line in rs.get("Content") or []:
                if isinstance(line, list) and len(line) == len(cols):
                    rows.append(dict(zip(cols, line)))
            out.append((rs.get("ResultSetKey") or "", cols, rows))
    return out


def parse_main(blobs) -> list[dict]:
    """取响应里**最大**的那张表，自动跳过元数据小表。

    比 parse_resultset 更适合「一个接口一张主表 + 若干元数据表」的场景。
    """
    tabs = [t for t in parse_tables(blobs) if t[2]]
    if not tabs:
        return []
    tabs.sort(key=lambda t: (len(t[1]), len(t[2])), reverse=True)
    return tabs[0][2]


def parse_flat(blobs) -> list[dict]:
    """解析「4 段式」响应 [meta, [列名...], [], [单行值...]]。

    DataAggregation.* / AppUpdateInfo.* 用这种；parse_resultset 处理不了
    （会抛 "'list' object has no attribute 'get'"）。
    """
    out: list[dict] = []
    for blob in blobs or []:
        j, _t = decode_json(blob)
        if not isinstance(j, list) or len(j) < 3:
            continue
        cols = vals = None
        for part in j:
            if isinstance(part, list) and part and all(isinstance(x, str) for x in part):
                if cols is None:
                    cols = part
                elif vals is None:
                    vals = part
        if cols and vals and len(cols) == len(vals):
            out.append(dict(zip(cols, vals)))
    return out


def retry(fn, tries=3, sleep=6.0, default=None):
    for i in range(tries):
        try:
            r = fn()
            if r:
                return r
        except Exception as exc:
            # 「pb 重试轮次」事件：第几轮、为什么失败 —— 诊断限流/断连节奏用。
            logger.debug("retry attempt %d/%d failed (%s)", i + 1, tries,
                         type(exc).__name__, exc_info=True)
        time.sleep(sleep)
    return default


# ── 采集器 ─────────────────────────────────────────────────
def _seal(stat: dict):
    """封板率 = 涨停/(涨停+炸板)。口径见 ztqd 的 00101。"""
    try:
        a = float(stat.get("N018") or 0)
        b = float(stat.get("N007") or 0)
        if a + b > 0:
            return round(a / (a + b) * 100, 2)
    except Exception as exc:
        logger.debug("seal ratio unavailable (%s)", type(exc).__name__,
                     exc_info=True)
    return None


def fetch_zt(day: str | None = None) -> dict:
    """涨停/跌停统计（封板率口径）。

    ph_tdxdatacenter_ztqd ctl=0x1E
    Params ["00101","0","3","<YYYY-MM-DD>","2","0"]：00101=统计，00102=个股列表
    """
    day = day or last_trading_day()
    rows = parse_resultset(call("CWServ.ph_tdxdatacenter_ztqd", 0x1E,
                                ["00101", "0", "3", day, "2", "0"],
                                wait=18, idle=3.5))
    stat = rows[0] if rows else {}
    return {"asOf": day, "stats": stat,
            "zt": stat.get("N018"), "dt": stat.get("N005"),
            "opened": stat.get("N002"), "sealRate": _seal(stat),
            "src": "tdx", "fetchedAt": now_iso()}


def fetch_ztlist(day: str | None = None) -> dict:
    """涨停个股列表。"""
    day = day or last_trading_day()
    rows = parse_resultset(call("CWServ.ph_tdxdatacenter_ztqd", 0x1E,
                                ["00102", "0", "3", day, "2", "0"],
                                wait=18, idle=3.5))
    return {"asOf": day, "count": len(rows), "list": rows,
            "src": "tdx", "fetchedAt": now_iso()}


def fetch_lr(day: str | None = None) -> dict:
    """两融总览。ctl=0x16。发布时点：次日早 9 点才更新前一交易日。"""
    day = day or prev_trading_day(last_trading_day())
    out = {"asOf": day, "src": "tdx", "fetchedAt": now_iso()}
    for sub, key in (("00101", "groups"), ("00103", "series"),
                     ("00201", "stocks"), ("00301", "industry")):
        out[key] = parse_resultset(call("CWServ.ph_zt_lrzt_sy", 0x16,
                                        [sub, ""], wait=18, idle=3.5))
        time.sleep(3)
    return out


def fetch_balance(day: str | None = None) -> dict:
    """两融余额明细。ctl=0x18，子码 00104。

    RS1（9 列 × 1 行）: N001 沪融资余额 / N004 深融资余额 / N007 融券余额
    RS2（6 列 × 121 行）: total = r[1] + r[3]
    """
    day = day or prev_trading_day(last_trading_day())
    rows = parse_resultset(call("CWServ.ph_zt_lrzt_lrmx", 0x18,
                                ["00104", day, 0, "", 0, ""],
                                wait=20, idle=4.0))
    return {"asOf": day, "rows": rows, "count": len(rows),
            "src": "tdx", "fetchedAt": now_iso()}


def fetch_lrzt(day: str | None = None) -> dict:
    """两融多空对比。"""
    day = day or prev_trading_day(last_trading_day())
    rows = parse_resultset(call("CWServ.ph_zt_lrzt_sy", 0x16, ["00102", ""],
                                wait=18, idle=3.5))
    return {"asOf": day, "rows": rows, "src": "tdx", "fetchedAt": now_iso()}


def fetch_hsgt(channel=4, day: str | None = None, tries: int = 4) -> dict:
    """沪深港通。ctl=0x1E。

    p2 是**通道码不是天数**：
      0=沪股通 1=深股通 2=港股通(沪) 3=港股通(深) 4=北向合计 5=南向合计
    列: N002 成交额 / N003 **成交笔数** / N004 净流入（2024-08 起北向为 null）
    """
    day = day or last_trading_day()
    rows = []
    for _ in range(tries):
        rows = parse_resultset(call("CWServ.ph_tdxdatacenter_hsgt", 0x1E,
                                    ["lszjlx3", str(channel), ""],
                                    wait=16, idle=3.5))
        if rows:
            break
        time.sleep(6)
    return {"asOf": day, "channel": channel, "rows": rows,
            "count": len(rows), "src": "tdx", "fetchedAt": now_iso()}


def fetch_hot(day: str | None = None) -> dict:
    """人气榜。ctl=0x26，p1="001"。13 列 × 100 行。"""
    day = day or last_trading_day()
    rows = parse_resultset(call("CWServ.ph_tdxdatacenter_tdxrb_ag_rqb", 0x26,
                                ["001"], wait=18, idle=3.5))
    return {"asOf": day, "rows": rows, "count": len(rows),
            "src": "tdx", "fetchedAt": now_iso()}


def fetch_rl(day: str | None = None) -> dict:
    """日历数据。ctl=0x1C。

    ⚠️ 参数**是有效的**：窗口 = [季度首月−1 的 1 号, 季度末月−1 的月末]。
    早期误判为「参数无效」，是因为只测了同一季度内的三个日期。
    """
    day = day or last_trading_day()
    rows = parse_resultset(call("CWServ.ph_tdxdatacenter_rl", 0x1C, [day],
                                wait=16, idle=3.5))
    return {"asOf": day, "rows": rows, "count": len(rows),
            "src": "tdx", "fetchedAt": now_iso()}


def fetch_calendar(day: str | None = None) -> dict:
    """交易日历（同 rl，保留独立 key 供旧调用）。"""
    return fetch_rl(day)


def fetch_lhb(day: str | None = None) -> dict:
    """龙虎榜。ctl=0x15。Params ["00101","1","YYYY","MM",""]"""
    day = day or last_trading_day()
    rows = parse_resultset(call("CWServ.ph_zt_lhb_sy", 0x15,
                                ["00101", "1", day[:4], str(int(day[5:7])), ""],
                                wait=18, idle=3.5))
    return {"asOf": day, "rows": rows, "count": len(rows),
            "src": "tdx", "fetchedAt": now_iso()}


def fetch_ztdd(day: str | None = None) -> dict:
    """连板天梯。ctl=0x1E。Params ["<date>","0",3]"""
    day = day or last_trading_day()
    rows = parse_resultset(call("CWServ.ph_tdxdatacenter_ztzt", 0x1E,
                                [day, "0", 3], wait=18, idle=3.5))
    return {"asOf": day, "rows": rows, "count": len(rows),
            "src": "tdx", "fetchedAt": now_iso()}


def fetch_zdtj(day: str | None = None) -> dict:
    """市场概览 6 卡。ctl=0x1E。Params ["rzrq","1"]"""
    day = day or last_trading_day()
    rows = parse_resultset(call("CWServ.ph_tdxdatacenter_scfx", 0x1E,
                                ["rzrq", "1"], wait=16, idle=3.5))
    return {"asOf": day, "rows": rows, "count": len(rows),
            "src": "tdx", "fetchedAt": now_iso()}


# key → 输出文件名（写进 out/）
FILES = {
    "zt": "datacenter_zt.json",
    "ztlist": "datacenter_zt_list.json",
    "lr": "datacenter_lr.json",
    "balance": "datacenter_balance.json",
    "lrzt": "datacenter_lrzt.json",
    "hsgt": "datacenter_hsgt.json",
    "hot": "datacenter_hot.json",
    "rl": "datacenter_rl.json",
    "calendar": "datacenter_calendar.json",
    "lhb": "datacenter_lhb.json",
    "ztdd": "datacenter_ztdd.json",
    "zdtj": "datacenter_zdtj.json",
}

ORDER = ["zt", "ztlist", "zdtj", "ztdd", "lr", "balance", "lrzt",
         "hsgt", "hot", "lhb", "rl"]


def fetch_one(key: str):
    fn = globals().get("fetch_" + key)
    if not fn:
        raise KeyError("no fetcher for %r" % key)
    return fn()


def main(argv=None):
    import os
    argv = list(sys.argv[1:] if argv is None else argv)
    which = argv[0] if argv else "all"
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    os.makedirs(base, exist_ok=True)
    keys = ORDER if which in ("all", "") else [which]
    ok = 0
    for k in keys:
        fn = FILES.get(k)
        if not fn:
            continue
        try:
            obj = fetch_one(k)
            n = (obj.get("count") or len(obj.get("rows") or [])
                 or len(obj.get("list") or []))
            save(os.path.join(base, fn), obj)
            ok += 1
            print("  [✓] %-10s → %-26s %s" % (k, fn, ("%s 行" % n) if n else ""))
        except Exception as e:
            print("  [✗] %-10s %s" % (k, str(e)[:70]))
        time.sleep(3)
    print("\n完成 %d / %d" % (ok, len(keys)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
