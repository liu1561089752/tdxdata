# -*- coding: utf-8 -*-
"""枚举：市场、K 线周期、复权方式。

取值与协议字段一一对应（**不是自定义的**，改这里等于改协议）：
    · Market       → `PBFXT`/`PBZST` 的 field3，且与 7615 的 `setcode` 完全一致
    · KlinePeriod  → `PBFXT` 的 field4（与 PC 版 `KlineCategory` 同一套）
    · Adjust       → `PBFXT` 的 field8（**服务端算好给**，不是本地算）
"""
from __future__ import annotations

from enum import IntEnum

__all__ = ["Market", "KlinePeriod", "Adjust"]


class Market(IntEnum):
    """市场码。"""
    SZ = 0          # 深圳
    SH = 1          # 上海
    BJ = 2          # 北京


class KlinePeriod(IntEnum):
    """K 线周期（`PBFXT` field4）。

    取值与 PC 版 `KlineCategory` 是同一套。**服务端实测可用性**：
      · 可用：MIN_1/5/15/30/60、DAY、WEEK、MONTH、SEASON
      · 无数据：YEAR(9)、YEAR_ALT(11)、MIN_3(8) —— 实测返回 0 行，
        库会抛 `NotSupportedError`，不返回空列表
    """
    MIN_5 = 0
    MIN_15 = 1
    MIN_30 = 2
    MIN_60 = 3
    DAY = 4
    WEEK = 5
    MONTH = 6
    MIN_1 = 7
    MIN_3 = 8
    YEAR = 9
    SEASON = 10
    YEAR_ALT = 11        # PC 版另有此值；7615 实测无数据


class Adjust(IntEnum):
    """复权方式 → `PBFXT` 的 `field8`（2026-10-25 实测确认，见 `reports/P8调研_复权与分页.md`）。

    ```
    Adjust.NONE → field8=1   原始价（基准）
    Adjust.QFQ  → field8=3   前复权：以最新价为锚 → 最新一根 == 原始价，历史价被调整
    Adjust.HFQ  → field8=2   后复权：以上市首日为锚 → 最新价被上调（600519 ×6.97）
    ```

    ⚠️ **枚举值本身不是协议码**（`Adjust.QFQ == 1`，而协议里 `1` 表示"不复权"）。
    映射表在 `_engine/hq_pb.py` 的 `ADJUST_*` 常量，由 `Client.get_bars` 转换，
    **不要**把 `int(Adjust.xxx)` 直接塞进请求。

    ⚠️ 服务端 `field8=4` 是另一个口径（600519 ×8.53），语义待定，**本枚举不表示它**。

    前复权的调整方向**不恒定**：600519 历史段被下调（×0.9948），000001 被上调（×1.0221）
    —— 服务端按自身口径处理（与配股/增发/送转有关），**不是"原始价减累计分红"**。
    """
    NONE = 0
    QFQ = 1
    HFQ = 2
