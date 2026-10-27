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

CST = timezone(timedelta(hours=8))

# 四台 7615 服务器。限流是**按客户端 IP × 服务器**算的，轮着打才不会被单台掐死。
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


def _cjk_score(text: str) -> int:
    """给一段文本的「像中文」程度打分，用于在多种解码里择优。

    GBK 的「元」(0xD2AA) 是合法 UTF-8，会解成亚美尼亚字母 `աԪ`，
    所以不能只按「能不能解」判断。
    """
    sc = 0
    for ch in text:
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF:
            sc += 3
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
    except Exception:                                      # noqa: BLE001
        pass
    try:
        from . import handshake as _h
        hs = _h.get()
        if hs:
            return hs
    except Exception:                                      # noqa: BLE001
        pass
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
                body = zlib.decompress(body)
            except Exception:              # noqa: BLE001
                pass                       # 解不开就原样交出去，让上层报错
        out.append(body)
        i = nxt
    return out


def _is_frame_head(buf: bytes, i: int) -> bool:
    """`i` 处是不是一个 7615 帧头（三重校验，尽量别在随机字节上误命中）。"""
    return (buf[i] in (0x0B, 0x3B)          # 0x0B 明文 / 0x3B zlib
            and buf[i + 10:i + 12] == b"K&"     # 0x264B
            and buf[i + 12:i + 14] == b"\x00d")


def _try_zlib(blob: bytes):
    try:
        d = zlib.decompressobj()
        t = d.decompress(blob)
        return t if len(t) > 1 else None
    except Exception:
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
    """按「像中文」得分择优解码，返回 (obj, text)。"""
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


def _drop_pb_conn() -> None:
    """丢掉 PB 持久连接（幂等，可在锁内嵌套调用）。"""
    with _CONN_LOCK:
        s = _CONN["sock"]
        _CONN.update(sock=None, host=None, t=0.0)
    if s is not None:
        try:
            s.close()
        except Exception:                  # noqa: BLE001
            pass


atexit.register(_drop_pb_conn)


def _pb_conn(host=None, *, drain=0.25):
    """取一条**已握手**的 PB 连接；空闲超时/换机时重建。

    走持久连接时 `sleep(1.3)` + 预排空只付一次，之后每次调用只花 0.18 秒左右。
    """
    with _CONN_LOCK:
        s = _CONN["sock"]
        if s is not None and (time.time() - _CONN["t"]) < _PB_IDLE_RECONNECT:
            return s
        _drop_pb_conn()
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
            except Exception:              # noqa: BLE001
                pass
        except Exception:
            try:
                s.close()
            except Exception:              # noqa: BLE001
                pass
            raise
        _CONN.update(sock=s, host=host, t=time.time())
        return s


def close_pb() -> None:
    """主动关闭 PB 持久连接（`Client.close()` / 进程退出时调）。"""
    _drop_pb_conn()


def _send(frame: bytes, host=None, wait=15.0, idle=2.0,
          *, drain=0.25, tail=0.15):
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

    get_pacer().wait()                     # ★ 进程级匀速闸门，PB 路径原来没有

    last: Exception | None = None
    for attempt in (0, 1):
        try:
            with _CONN_LOCK:
                s = _pb_conn(host if attempt == 0 else None, drain=drain)
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
                _CONN["t"] = time.time()   # 记活跃时间，供空闲重连判断
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
            _drop_pb_conn()
    # 两轮都失败 → 上抛，不假装成功
    raise last if last is not None else RuntimeError(
        "PB 发送两轮既没返回也没异常，逻辑有洞")


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


def call_raw(cmd, ctl, body: bytes, host=None, wait=8.0, idle=2.5):
    """发裸 JSON 参数（DataAggregation 用裸数组，如 b"[{}]"）。"""
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
    """标准 ResultSets 响应拉平成 [{列名: 值}]。

    ColDes 可能为空（如北向 bxxj 序列），此时回退到 ColName。
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
    """按 ResultSets 分组返回 [(key, 列名, [行])]，保留每张表。

    有些接口（如 ph_agf10_cw_lyb 利润表）一次返回多张表：
        table0: 1 行 1 列 {isjr: 0}      ← 元数据
        table1: 24 行 38 列              ← 真实数据
    `parse_resultset` 会把它们拉平混在一起，需要区分时用这个。
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
    for _ in range(tries):
        try:
            r = fn()
            if r:
                return r
        except Exception:
            pass
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
    except Exception:
        pass
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
