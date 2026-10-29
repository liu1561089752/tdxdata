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

#: 日线周期标记（保留旧名，值即协议字节）
PERIOD_DAY = 4

#: **周期枚举**（`body[26]`，2026-10-29 实测 29 次真机 + 逐条日期核对）
#:
#: 这是本轮探测最省事的一条结论：**同一个命令、同一种记录格式**，
#: 改一个字节就从日线变成分钟/周/月线，不需要新的命令号。
#:
#: | 协议值 | 周期 | 验证强度 |
#: |---|---|---|
#: | `0` | 5 分钟 | ★★★「声明根数 × 每日根数 = 实回根数-3」三条独立互证 |
#: | `1` | 15 分钟 | ★★★ 同上（368 声明 → 371 实回） |
#: | `2` | 30 分钟 | ★★★ 同上（184 → 187） |
#: | `4` | 日 | ★★★ |
#: | `5` | 周 | ★★★（138 → 141） |
#: | `6` | 月 | ★★★（50 → 53） |
#: | `7` / `8` | 1 分钟 | ★★☆ 相邻时间戳间隔 60 秒，自洽 |
#: | `3` | 60 分钟 | ⚠️ **未验证**（触发 `WinError 10053`，按纪律停手）→ 故意不对外暴露 |
PERIODS = {
    "1m": 7,
    "5m": 0,
    "15m": 1,
    "30m": 2,
    "day": 4,
    "week": 5,
    "month": 6,
}

#: 反查：协议值 → 对外周期名（`3` 不在里面，因为没验证过，别偷偷放行）
PERIOD_NAMES = {v: k for k, v in PERIODS.items()}

#: 分钟级周期：回包里 `[16:20]` 是「当日秒数」，要单独出 `Time` 列
INTRADAY = frozenset({"1m", "5m", "15m", "30m"})

#: 分钟线列名（比日线多一列 `Time`，位置紧跟 `Date`）
BARS_COLS_INTRADAY = ("Date", "Time", "Open", "High", "Low", "Close",
                      "OpenInterest", "Volume", "AvgPrice")

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


def parse_bars(payload: bytes, with_time: bool = False):
    """回包 → `(列名, 行)`。行是与其它解析器一致的元组。

    记录定位：先在载荷里找第一个**合法日期**（并要求 `+36` 处也是合法日期），
    以它为记录起点；36 B 为步长往后切，日期必须单调不减，否则停止
    （尾部常带 3~4 条非行情尾包）。

    记录内偏移（**以日期字段为原点**）：

    | 相对位置 | 含义 |
    |---|---|
    | `-12..-8` | 持仓量 f32（推断） |
    | `-8..-4` | 成交量 f32（推断） |
    | `-4..0` | 均价 f32（推断，商品常 0） |
    | `0..4` | **日期 u32** `YYYYMMDD` |
    | `4..8` | **分钟线的「当日秒数」**（日线恒 0） |
    | `8..24` | Open / High / Low / Close 四个 f32 |
    | `24..36` | 保留 |

    `with_time=True` 时多出 `Time` 列（`HH:MM`，由当日秒数换算）。
    实测证据（5 分钟线）：`36900 → 37200 → 37500 … 54000` 正好是
    10:15 → 10:20 → 10:25 … 15:00。
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
        clock = struct.unpack_from("<I", rec, 4)[0]
        oi, vol, avg = struct.unpack_from("<3f", rec, -12)    # 日期前 12 字节
        open_, high, low, close = struct.unpack_from("<4f", rec, 8)
        if not (low <= min(open_, close) + 1e-3 and max(open_, close) - 1e-3 <= high):
            break
        if date < last_date:
            break
        last_date = date
        tail = (round(oi, 1), round(vol, 1), round(avg, 3))
        if with_time:
            rows.append((date, "%02d:%02d" % (clock // 3600, clock // 60 % 60),
                         round(open_, 3), round(high, 3), round(low, 3),
                         round(close, 3)) + tail)
        else:
            rows.append((date,
                         round(open_, 3), round(high, 3), round(low, 3),
                         round(close, 3)) + tail)
        off += REC_SIZE
    return (BARS_COLS_INTRADAY if with_time else BARS_COLS), rows


def period_code(period) -> int:
    """周期名（`"5m"`/`"day"`…）或协议值 → 协议字节。**不认识的直接拒**。

    ⚠️ 故意**不接受 60 分钟（协议值 `3`）**：这个值没验证过（实测触发
    `WinError 10053`），放进来等于让用户拿到一个可能错的答案。
    """
    if isinstance(period, int):
        if period in PERIOD_NAMES:
            return period
        raise ValueError(
            "周期值 %r 未验证（已确证的只有 %s）；60 分钟（协议值 3）实测会让服务端断连，"
            "因此没有对外暴露。" % (period, sorted(PERIOD_NAMES)))
    key = str(period).strip().lower()
    if key in PERIODS:
        return PERIODS[key]
    raise ValueError("不认识的周期 %r。可用：%s" % (period, sorted(PERIODS)))


def get_bars(code: str, market: int, count: int = COUNT_DEFAULT,
             period=PERIOD_DAY):
    """直连取 K 线，返回 `(列名, 行)`；失败返回 `((), [])`。

    `period` 接受 `"1m"/"5m"/"15m"/"30m"/"day"/"week"/"month"` 或协议值；
    分钟周期自动多出 `Time` 列（`HH:MM`）。
    """
    from . import datacenter as dc          # 局部导入：避免引擎层循环依赖

    pcode = period_code(period)
    body = bars_args(code, market, count, period=pcode)
    blobs = dc._send(frame(KLINE, body), wait=8.0, idle=1.5)
    if not blobs:
        return (), []
    return parse_bars(blobs[0], with_time=PERIOD_NAMES.get(pcode) in INTRADAY)
