# -*- coding: utf-8 -*-
r"""经典二进制协议（**不是 protobuf**）：期货 K 线，命令 `0x122E`。

为什么单独一个模块
------------------
本库其它行情命令都是 `HQServ:<名字>` + `Base64(protobuf)`。期货 K 线不一样：
它走**经典二进制协议** —— 没有命令名字符串，帧里放的是 `命令号 + 定长请求体`。
所以 `hq_pb` 那套（`PBFXT`）对期货永远 0 行，这不是"没有通道"，是"通道在另一条协议上"。

证据（2026-10-29）
------------------
1. **抓包**：`raw/pcap/cap_1029_090838.pcap` 里有真实帧（7615 端口，向上）：

       0b 63 01 00 00 03 | 30 00 30 00 | b4 26 00 64 | 2e 12 01 | <48 B 请求体>
       └ 固定头 ┘ └ 序号 ┘ └ ctl=3 ┘ └两处长度┘ └标记┘ └cmd+01┘ └── body ──┘

   与 protobuf 帧的四处差别：序号递增（不是固定 0x7000）、ctl=0x03、
   标记是 `b4 26`（protobuf 是 `4b 26`）、**没有命令名字符串**（换成 `命令号(2B LE)+01`）。

2. **缓存**：App 的 `CacheData.db`（`t_defcacheNewEx`）把同一请求的
   `ReqData/AnsData` 存了下来（`ReqNo=4654` 就是 `0x122E`），据此定死了请求模板与
   回包记录布局。

3. **实测**：按上面的帧复原后直连服务端，`MA2612/CU2612/IF2611` 各回 700/700/626 根，
   OHLC 全部通过 `low ≤ min(open,close) ≤ max(open,close) ≤ high` 校验。

请求体（48 B，`MA2612` @市场 28）
--------------------------------
```
2e 12 | 1c 00 | 4d4132363132 | 00×20 | 04 | 00 | 01 | 00×5 | bc 02 | 0b 00 01 02 | 00×4
cmd    市场     "MA2612"       补零    周期          700 根
```
`bc 02` = 700（小端），`04` = 日线标记（分钟周期未验证）。

回包记录（36 B/根，实测 700 根 25353 B）
---------------------------------------
```
[0:4]   f32  持仓量  ┐
[4:8]   f32  成交量  ├ 名称为**推断**：量级与真实合约吻合，但没有独立标签证据
[8:12]  f32  均价    ┘
[12:16] u32  日期 YYYYMMDD   ← 以它为锚点拟合出来的（700/704 合法且单调）
[16:20] u32  保留（日线恒 0；分钟线可能放 HHMM）
[20:24] f32  Open
[24:28] f32  High
[28:32] f32  Low
[32:36] f32  Close
```

⚠️ 与 `hq_pb` 的红线一致：**推断出来的列名必须在 docstring 里标明是推断**。
"""
from __future__ import annotations

import struct

#: 经典帧的 ctl 字节（抓包实测，与 protobuf 的 0x0E/0x11/0x12 完全不同）
CTL = 0x03

#: 命令号（2 字节小端写进帧头；也等于 App 缓存里的 `ReqNo`）
KLINE = 0x122E      # K 线（4654）
INFO = 0x122A       # 基础信息（4650）
LEVEL5 = 0x122B     # 五档（4651）
SNAPSHOT = 0x122F   # 分时/快照（4655）

#: 抓包里 `b4 26` 这个标记位（protobuf 帧是 `4b 26`，**别抄错**）
MARK = b"\xb4\x26"

#: 默认要多少根：与服务端单页上限一致（实测 700）
COUNT_DEFAULT = 700

#: 日线周期标记（分钟周期未验证，先只放日线）
PERIOD_DAY = 4

#: 回包记录宽度
REC_SIZE = 36

#: 列名。前 4 列（Date/Open/High/Low/Close）是**实测校验过**的；
#: 后 3 列（OpenInterest/Volume/AvgPrice）是按量级关系推断的，见 §回包记录。
BARS_COLS = ("Date", "Open", "High", "Low", "Close",
             "OpenInterest", "Volume", "AvgPrice")

#: 哪些市场号是期货（28 郑商所 / 29 大商所 / 30 上期所·能源 / 66 广期所 / 47 中金所）
FUTURES_MARKETS = frozenset({28, 29, 30, 47, 66})


#: 品种字母 → 交易所市场号（**只覆盖常见主力品种**，新品种请显式传 market=）
PRODUCT_MARKET = {
    # 郑商所 28
    "MA": 28, "TA": 28, "FG": 28, "CF": 28, "SR": 28, "RM": 28, "OI": 28,
    "ZC": 28, "SF": 28, "SM": 28, "UR": 28, "SA": 28, "PF": 28, "PK": 28,
    "AP": 28, "CJ": 28, "PX": 28, "SH": 28, "PR": 28, "M": 28,
    # 大商所 29
    "V": 29, "L": 29, "J": 29, "JM": 29, "I": 29, "PP": 29, "EG": 29,
    "EB": 29, "PG": 29, "LH": 29, "JD": 29, "C": 29, "CS": 29, "A": 29,
    "B": 29, "Y": 29, "P": 29, "RR": 29,
    # 上期所 / 能源中心 30
    "CU": 30, "AL": 30, "ZN": 30, "PB": 30, "NI": 30, "SN": 30, "AU": 30,
    "AG": 30, "RB": 30, "WR": 30, "HC": 30, "SS": 30, "RU": 30, "BU": 30,
    "FU": 30, "SP": 30, "SC": 30, "LU": 30, "NR": 30, "BC": 30, "AO": 30,
    "BR": 30,
    # 中金所 47
    "IF": 47, "IH": 47, "IC": 47, "IM": 47, "T": 47, "TF": 47, "TS": 47, "TL": 47,
    # 广期所 66
    "LC": 66, "SI": 66, "PS": 66,
}


def market_of(code: str) -> int:
    """按品种字母猜交易所；猜不出**不瞎猜**，抛 ValueError 让调用方显式给 market。

    解析规则：取开头的字母段（`MA2612` → `MA`，`IFL9` → `IF`）。
    ⚠️ 同一字母在不同交易所可能存在（如 `M` 在大商所），所以这张表只覆盖常见品种；
    遇到 `ValueError` 就用 `get_futures_bars("28:MA2612")` 这种显式写法。
    """
    letters = ""
    for ch in str(code):
        if ch.isalpha():
            letters += ch
        else:
            break
    letters = letters.upper()
    if letters in PRODUCT_MARKET:
        return PRODUCT_MARKET[letters]
    for n in (2, 1):                       # 退一步：用前 2/1 个字母再试
        if letters[:n] in PRODUCT_MARKET:
            return PRODUCT_MARKET[letters[:n]]
    raise ValueError(
        "推断不出 %r 的交易所（品种字母 %r 不在表里）。请显式写市场号："
        "28 郑商所 / 29 大商所 / 30 上期所·能源 / 47 中金所 / 66 广期所 "
        "—— 例如 get_futures_bars(\"28:MA2612\")" % (code, letters))


def bars_args(code: str, market: int, count: int = COUNT_DEFAULT,
              period: int = PERIOD_DAY) -> bytes:
    """构造 4654 请求体（48 B）。与抓包/缓存里的字节**逐字节一致**。

    偏移来自实测：`04 00 01` 在 26、`bc 02`（=700）在 34、尾块 `0b 00 01 02` 在 36。
    """
    code_b = str(code).encode("ascii")
    if len(code_b) > 6:
        raise ValueError("合约码最长 6 字节：%r" % (code,))
    body = bytearray(48)
    struct.pack_into("<H", body, 0, KLINE)
    struct.pack_into("<H", body, 2, int(market))
    body[4:4 + len(code_b)] = code_b
    body[26:29] = bytes([period & 0xFF, 0x00, 0x01])
    struct.pack_into("<H", body, 34, max(1, int(count)))
    body[36:40] = b"\x0b\x00\x01\x02"
    return bytes(body)


def frame(cmd_no: int, body: bytes, msgid: int = 0x7000) -> bytes:
    """经典帧：`0b + 序号(4) + 03 + 长度×2 + b4 26 + 00 64 + 命令号(2) + 01 + body`。

    `msgid` 在抓包里是递增序号；本项目实测**固定值也能通**，所以默认 0x7000
    （与 protobuf 帧一致），需要时调用方可传自己的序号。
    """
    return (b"\x0b" + struct.pack("<I", int(msgid) & 0xFFFFFFFF) + bytes([CTL])
            + struct.pack("<H", len(body)) + struct.pack("<H", len(body))
            + MARK + b"\x00\x64"
            + struct.pack("<H", cmd_no) + b"\x01" + body)


def is_date(v: int) -> bool:
    m, d = v // 100 % 100, v % 100
    return 19900101 <= v <= 21001231 and 1 <= m <= 12 and 1 <= d <= 31


def parse_bars(payload: bytes):
    """回包 → `(列名, 行)`。行是 dict 风格元组（与库内其它解析器一致）。

    定位方式：先在载荷里找第一个**合法日期**，再以 36 B 为步长往后切；
    日期必须单调不减，否则丢弃该行（尾部常有 3~4 条非行情尾包）。
    """
    if not payload:
        return (), []
    buf = bytes(payload)
    start = -1
    for i in range(0, max(0, len(buf) - 4)):
        if is_date(struct.unpack_from("<I", buf, i)[0]):
            # 要求下一根也对得上，避免撞到随机字节
            nxt = i + REC_SIZE
            if nxt + 4 <= len(buf) and is_date(struct.unpack_from("<I", buf, nxt)[0]):
                start = i
                break
    if start < 0:
        return (), []

    rows = []
    last_date = 0
    off = start
    while off + REC_SIZE <= len(buf):
        rec = buf[off:off + REC_SIZE]
        date = struct.unpack_from("<I", rec, 0)[0]
        if not is_date(date):
            break
        oi, vol, avg = struct.unpack_from("<3f", rec, -12)    # 0..12 相对记录起点
        open_, high, low, close = struct.unpack_from("<4f", rec, 8)
        if not (low <= min(open_, close) + 1e-3 and max(open_, close) - 1e-3 <= high):
            break
        if date < last_date:
            break
        last_date = date
        rows.append((date,
                     round(open_, 3), round(high, 3), round(low, 3), round(close, 3),
                     round(oi, 1), round(vol, 1), round(avg, 3)))
        off += REC_SIZE
    return BARS_COLS, rows


def get_bars(code: str, market: int, count: int = COUNT_DEFAULT):
    """直连取期货 K 线，返回 `(列名, 行)`；失败返回 `((), [])`。"""
    from . import datacenter as dc          # 局部导入：避免引擎层循环依赖

    body = bars_args(code, market, count)
    blobs = dc._send(frame(KLINE, body), wait=8.0, idle=1.5)
    if not blobs:
        return (), []
    return parse_bars(blobs[0])
