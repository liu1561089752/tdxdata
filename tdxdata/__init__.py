# -*- coding: utf-8 -*-
"""tdxdata —— 通达信手机版（7615）数据客户端库。

首个面向「手机版 7615」的完整 Python 库：一条 TCP 通道同时取到
**行情**（K 线 / 分时）与**数据中心**（龙虎榜 / 两融北向 / 财报 / 宏观 / 市场统计…）。

特点
----
* **纯标准库**：只用 `socket` / `struct` / `zlib` / `json`，零第三方依赖
* **同步 API**：`with Client() as c: c.get_bars(...)`，无 asyncio 学习成本
* **421 个数据产品**：按名字取数（`c.get("lhb", date=...)`），不必记命令名和 ctl
* **类型化返回**：`Bar` / `MinutePoint` / `Table` 等 dataclass，可 IDE 补全
* **错误分级**：`ParamError` / `ServerError` / `NotSupportedError` / `EmptyResult`
  —— 明确区分「参数错」「通道不通」「当期无数据」

快速开始
--------
    import tdxdata

    with tdxdata.Client() as c:
        # ① 行情
        bars = c.get_bars("600519", period="day", count=5)
        for b in bars:
            print(b.date, b.open, b.close)

        # ② 数据中心产品（421 个）
        t = c.get("lhb", date="2026-10-23")
        print(t.shape, t.cols[:5])

        # ③ 任意命令（文档里的 cmd + ctl + 参数照抄）
        cols, rows = c.call("CWServ.ph_datacenter_hg_holiday", 0x21,
                            ["001", "1"]).shape

能力边界（**重要，别当成 pytdxdata 用**）
----------------------------------------
| 能力 | 本库 | 说明 |
|---|---|---|
| K 线（1/5/15/30/60 分、日、周） | ✅ | `PBFXT`，单页 416 条 |
| 当日分时 240 点 | ✅ | `PBZST` |
| 龙虎榜 / 两融 / 北向 / 财报 / 宏观 / 市场统计 / F10 / 新股 / 大宗 | ✅ | 421 个产品 |
| 五档盘口（按代码） | ❌ | **协议层无 bid/ask 字段**，且 `PBMultiHQ` 无代码位（2026-10-25 解构）；行情列表用 `get_market_page` |
| 逐笔成交 | ✅ | `HQServ:PBTickEx`（ctl=0x11，2026-10-27 打通，自动翻页） |
| **复权 K 线** | ✅ | **`PBFXT` 的 `field8` 即复权开关**（2026-10-25 实测）：`Adjust.QFQ`=前复权、`Adjust.HFQ`=后复权，服务端算 |
| 证券代码表 / 板块成分 | ⚠️ 部分 | `get_universe` 只枚举「行情列表」；无完整代码表命令 |
"""
from .client import Client
from .enums import Adjust, KlinePeriod, Market
from .errors import (
    ConnectionFailed,
    EmptyResult,
    NoData,
    NotSupportedError,
    ParamError,
    ProtocolError,
    RateLimited,
    ServerError,
    TdxError,
    Timeout,
)
from .models import (
    Bar,
    Dataset,
    MinutePoint,
    ProductInfo,
    Quote,
    Row,
    SecurityInfo,
    Table,
    Tick,
)
from .symbols import parse as parse_symbol

def __getattr__(name):
    """`AsyncClient` 懒导入：`import tdxdata` 不必拖进 asyncio。

    同时也让 `tdxdata.indicator` / `tdxdata.limits` 这类子模块可以用
    `tdxdata.indicator` 直接访问（它们本来就是子模块，这里只是方便）。
    """
    if name == "AsyncClient":
        from .aio import AsyncClient as _AC

        return _AC
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


__version__ = "0.2.0"

__all__ = [
    # 入口
    "Client", "AsyncClient",
    # 枚举
    "Market", "KlinePeriod", "Adjust",
    # 模型
    "Bar", "MinutePoint", "Quote", "Tick", "SecurityInfo",
    "Table", "Row", "Dataset", "ProductInfo",
    # 异常
    "TdxError", "ConnectionFailed", "Timeout", "RateLimited", "ProtocolError",
    "ParamError", "ServerError", "NotSupportedError", "EmptyResult", "NoData",
    # 工具
    "parse_symbol",
    "__version__",
]
