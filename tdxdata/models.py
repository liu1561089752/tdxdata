# -*- coding: utf-8 -*-
"""返回模型：全部是 dataclass，字段名与服务中心返回的列名对齐。

设计取向（与"返回 dict"的区别）：
  · **可静态检查**：IDE 能补全，拼错字段名当场报错
  · **可序列化**：`dataclasses.asdict()` 一行转 dict / JSON
  · **可扩展**：原始行保留在 `raw` 里，标准字段不够用时不会丢信息
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Bar", "MinutePoint", "Table", "Row", "Dataset", "ProductInfo",
    "Quote", "Tick", "SecurityInfo",
]


@dataclass(slots=True)
class Bar:
    """一根 K 线。

    **单位（实测；`vol` 已于 2026-10-27 归一化）**
      · `open/high/low/close` —— 元
      · `vol`    —— **手**（唯一单位，见下）
      · `amount` —— 元（各品类、各周期实测一致）
      · `time`   —— 分钟线为 `HH:MM`；日线及以上为空串
      · `date`   —— `YYYYMMDD`；分钟线同一天有多根，靠 `time` 区分
      · `adjust` —— 本根用的是哪种复权口径（`NONE`/`QFQ`/`HFQ`/`FIELD8_4`）

    ⚠️ **`vol` 恒为「手」**（2026-10-27 口径变更，此前是"日线股票=股、周线=手"混着来）。
    服务端原始单位其实是**随品种和周期变**的：

      · 股票 / ETF：日线与分钟线是**股**，周/月线是**手**（= 日线/100）
      · 可转债 / 回购 / 指数 / 板块：日线本来就是**手**，周/月线是"手/100"

    `client.get_bars()` 在出口按 `_vol_to_hand()` 的实测表统一换算成手，
    **原始值原样留在 `raw["Volume"]` 里**（那里仍是服务端的原单位，直接读要小心）。
    换算不四舍五入，所以可能出现小数（如 `10588.72` 手）。
    ⚠️ **指数/板块的分钟线 `vol` 恒为 `None`**：服务端那一列是 `amount/100` 的**派生值**，
    不是成交量（实测 999999 / 880903 / 899050 的分钟线 `amount/vol` **恰好 100.000**，
    而 `amount` 与当日日线合计的比值是 1.0000 ⇒ amount 真、vol 是造的；
    999999 的 60m 若按手讲是 2.86 万亿股/小时，不可能）。本库红线：**拿不到就给 `None`**。
    该品类**日线**的 `vol` 是**真值**，照常提供。
    """
    market: int
    code: str
    date: str                 # YYYYMMDD
    open: float
    high: float
    low: float
    close: float
    vol: float | None = 0.0   # 手（归一化后；原值见 raw["Volume"]；指数/板块分钟线为 None）
    amount: float = 0.0       # 元
    period: str = "day"
    time: str = ""            # HH:MM，仅分钟线
    raw: dict[str, Any] = field(default_factory=dict)
    adjust: str = "NONE"      # 复权口径，见 enums.Adjust / hq_pb.ADJUST_*

    @property
    def symbol(self) -> str:
        return symbol_of(self.market, self.code)

    @property
    def vol_unit(self) -> str:
        """`vol` 的单位，**恒为 `"手"`**（2026-10-27 归一化后不再随品种/周期变）。"""
        return "手"


@dataclass(slots=True)
class MinutePoint:
    """分时点（240 点/天）。

    **单位（实测）**：`price`/`avg_price` 元；`vol` —— **手**
    （与 `Bar.vol` 现在**同单位**了：2026-10-27 起 `Bar.vol` 也归一化成手，
    所以 240 点 `vol` 合计 = 当日 `Bar.vol`，不再差 100 倍）。
    """
    code: str
    time: str                 # HH:MM
    price: float
    avg_price: float
    vol: float = 0.0
    market: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def symbol(self) -> str:
        return symbol_of(self.market, self.code)


@dataclass(slots=True)
class Quote:
    """一行报价。两个来源共用：`get_quotes`（`PBCombHQ`）与 `get_market_page`/`get_universe`（`PBMultiHQ`）。

    字段单位（**都是实网对拍过的，不是猜的**）：

    ============= ============================================================
    `price`       **元**（`NOW` 列）
    `pre_close`   **元**（`CLOSE` 列；本通道 `CLOSE` 是**昨收**，不是当日收盘）
    `open/high/low` **元**（`OPEN`/`MAX`/`MIN` 列）
    `vol`         **手**（1 手 = 100 股；2026-10-27 起与 `Bar.vol` **同单位**，可直接比）
    `amount`      **元**（与 `Bar.amount` 同口径）
    `bid`/`ask`   **恒为空列表**：协议层没有五档（`PBCombHQ` 25 列里也没有 bid/ask）
    ============= ============================================================

    `vol` 单位是「手」的铁证（2026-10-27 09:25:22 集合竞价撮合后，同一交易日 600519）：
    `get_quotes` 给 `vol=210`，当日 `get_bars` 给 `vol=21000`，比值正好 **0.01**；
    而 `amount` 两边都是 `28,221,480`（比值 1.0）。自洽校验：
    `28,221,480 / 21,000 = 1343.88` = 当日收盘价 —— 若把 210 当"股"，这笔账根本算不平。

    两个来源的差别（**两条通道 `vol` 都是「手」、`amount` 都是「元」**）：
      · `get_quotes`（`PBCombHQ`）—— **按代码**取，`field4` 就是代码列表，
        并显式多要 `VOL`/`AMOUNT`/`LB` 三列，所以 `vol`/`amount` 有真值；
      · `get_market_page` / `get_universe`（`PBMultiHQ`）—— 按市场码**翻页列表**，
        默认 `cols` 就带 `AMOUNT`（所以 `amount` 有值）；`VOL` 默认没要，
        要 `vol` 就把它加进 `cols`。**这两列现在都会进 `Quote`**
        （2026-10-27 修掉的 bug：以前值躺在 `raw` 里、对外却报 `0.0`）。
      · 两个来源都是：**没给该列 → `None`**（不拿 0 冒充"没有这一列"）。

    `PBMultiHQ` 的 `VOL` 也是「手」的算术验证（2026-10-27，688252 天德钰）：
    `21164 手 × 100 × 21.18 ≈ 44,825,352`，与同一行 `AMOUNT = 43,914,168` 同口径
    （差 2% 是同刻不同字段的取舍）；若 `VOL` 是「股」，`21164 × 21.18 = 448,254`，
    与成交额差 100 倍，对不上。

    ⚠️ 历史更正（2026-10-27）：
      - 本节旧文案写"`Client.get_quotes(symbols)` 仍然抛 `NotSupportedError`" —— **已过时**，
        `PBCombHQ` 打通后 `get_quotes` 可用；
      - 旧文案写"`vol`/`amount` 默认 0" 且暗示"拿不到" —— 现在 `get_quotes` **能拿到**，
        只是缺列时给 `None`；
      - `vol`/`amount` 的**默认值已从 `0.0` 改为 `None`**：`0.0` 会被读成"成交量为零"，
        而真含义是"该来源没给这个字段"。顺带修掉 `get_market_page` 的同类 bug
        （真值在 `raw` 里、对外报 0），两条通道现在都如实传值。
    """
    market: int
    code: str
    price: float
    pre_close: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    #: 成交量，单位 **手**（×100 = 股）。**`None` = 该来源没给这一列**（不是"没有成交"）
    vol: float | None = None
    #: 成交额，单位 **元**。**`None` = 该来源没给这一列**（不是"没有成交"）
    amount: float | None = None
    bid: list[tuple[float, float]] = field(default_factory=list)
    ask: list[tuple[float, float]] = field(default_factory=list)
    server_time: str = ""
    name: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def symbol(self) -> str:
        return symbol_of(self.market, self.code)


@dataclass(slots=True)
class Tick:
    """一笔**逐笔成交**（`HQServ:PBTickEx`，2026-10-27 打通）。

    **单位**（实网复核）：`price` 元；`vol` **手**（与 `Bar.vol` / `Quote.vol` /
    `MinutePoint.vol` **同单位**）；`time` 为 `HH:MM:SS`（服务端给的是**当日秒数**，
    例如 `40978` → `11:22:58`）。

    ⚠️ `trade_count` / `bs_flag` **服务端没有对应字段，恒为 `None`**。
    早前给它们填 0 / 2 是**编数据**（`bs_flag=2` 会被读成"卖"），已改掉：
    **拿不到就是 `None`，不填 0 冒充**（本库红线）。
    真实的方向信息其实在 `raw["InOutFlag"]`（`0`/`1`）里，只是"0 是买还是卖"**未定**
    —— 所以本库不映射成 `bs_flag`，让用户自己看 `raw`。
    `VolInStockDiff` / `InOutFlag` / `HKTTFlag` / `HKInOutFlag` **语义未定**，
    原样放在 `raw` 里（`HKTTFlag` 实测会是 `\x01` 这种控制字符）。
    """
    market: int
    code: str
    time: str | None = None   # HH:MM:SS（拿不到就是 None）
    price: float | None = None
    vol: float | None = None  # 手
    trade_count: int | None = None   # 服务端无此字段 → None（不填 0 冒充）
    bs_flag: int | None = None       # 服务端无此字段 → None（不猜方向）
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def symbol(self) -> str:
        return symbol_of(self.market, self.code)


@dataclass(slots=True)
class SecurityInfo:
    """证券基础信息（代码表元素）。"""
    market: int
    code: str
    name: str = ""
    volunit: float = 0.0
    decimal_point: int = 2
    pre_close: float = 0.0

    @property
    def symbol(self) -> str:
        return symbol_of(self.market, self.code)


class Row(dict):
    """一行数据：**既是 dict，也能按位置取**。

    为什么不是普通 dict
    -------------------
    7615 回包给的是**按位置排列的值列表**，而使用者想的是**按列名取**。
    两边都保留才不会有人踩坑：

    ```python
    row["N004"]     # 按列名（推荐）
    row[3]          # 按位置（等价于 row[cols[3]]）—— 协议层口径
    row.get("name", "—")
    dict(row)       # 干净转普通 dict，可 json.dumps
    ```

    它是 `dict` 的子类，所以 `isinstance(row, dict)` 为真、可 `json.dumps`、
    `**row` 展开都照常可用。位置下标只额外接 `int`（越界抛 `IndexError`）。
    """
    __slots__ = ("_cols",)

    def __init__(self, cols=(), values=None, **kwargs):
        super().__init__()
        self._cols = list(cols)
        if values is not None:
            for i, name in enumerate(self._cols):
                dict.__setitem__(self, name, values[i] if i < len(values) else None)
        if kwargs:
            dict.update(self, kwargs)

    # -- 位置访问（协议层口径）--------------------------------------------
    def __getitem__(self, key):
        if isinstance(key, int) and not isinstance(key, bool):
            try:
                name = self._cols[key]
            except IndexError:
                raise IndexError("行只有 %d 列，取不到第 %d 列" % (len(self._cols), key)) from None
            return dict.__getitem__(self, name)
        return dict.__getitem__(self, key)

    def __setitem__(self, key, value):
        if isinstance(key, int) and not isinstance(key, bool):
            try:
                key = self._cols[key]
            except IndexError:
                raise IndexError("行只有 %d 列，写不到第 %d 列" % (len(self._cols), key)) from None
        dict.__setitem__(self, key, value)

    def __delitem__(self, key):
        if isinstance(key, int) and not isinstance(key, bool):
            key = self._cols[key]
        dict.__delitem__(self, key)

    def __contains__(self, key):
        if isinstance(key, int) and not isinstance(key, bool):
            return 0 <= key < len(self._cols)
        return dict.__contains__(self, key)

    def get(self, key, default=None):
        if isinstance(key, int) and not isinstance(key, bool):
            if 0 <= key < len(self._cols):
                return dict.get(self, self._cols[key], default)
            return default
        return dict.get(self, key, default)

    @property
    def cols(self) -> list[str]:
        """这一行绑定的列名（与 `Table.cols` 同源）。"""
        return list(self._cols)

    def to_dict(self) -> dict:
        """转普通 dict（去掉位置下标能力，纯数据）。"""
        return dict(self)

    def __repr__(self) -> str:
        return "Row%r" % (dict(self),)


def _make_row(cols, raw):
    """把「值列表」或「dict」统一成 `Row`（位置与列名都可用）。

    已经是 `Row` 的话，**位置口径必须以所在表的 `cols` 为准**：
    列清单变了就重绑一份新的（不复用原对象 —— 同一行可能被两张表共享，
    原地改 `_cols` 会**改坏另一张表**的位置下标）。
    """
    if isinstance(raw, Row):
        if list(raw._cols) == list(cols):
            return raw
        row = Row(cols)
        dict.update(row, raw)
        return row
    if isinstance(raw, dict):
        row = Row(cols)
        dict.update(row, raw)
        return row
    return Row(cols, list(raw))


@dataclass(slots=True)
class Table:
    """一张结果表：列名 + 行（每行是一个 `Row`，即 dict，也支持位置取）。

    这是 7615 数据中心产品的**统一返回类型**。`cols` 来自协议（多为 `N001…`），
    含义见 `docs/VERIFICATION.md` 的「未定字段」清单。

    重要：**`cols` 与每一行的取值严格对齐**。服务端回多个 ResultSet 时，
    `spec/06 §2.5.7` 的"列数"口径是并集，而并集通常比主表宽 ——
    并集放在 `meta["cols_union"]`，`cols` 只放**主表自己的列名**，
    否则 `zip(cols, row)` 会把整行错一位（静默错列）。
    """
    cols: list[str]
    rows: list[dict[str, Any]]
    cmd: str = ""
    domain: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.cols = list(self.cols)
        self.rows = [_make_row(self.cols, r) for r in (self.rows or [])]

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def __bool__(self) -> bool:
        return bool(self.rows)

    @property
    def shape(self) -> tuple[int, int]:
        """(行数, 列数)。"""
        return len(self.rows), len(self.cols)

    def to_dicts(self) -> list[dict[str, Any]]:
        """转普通 dict 列表（可直接 `json.dumps`）。"""
        return [dict(r) for r in self.rows]

    def column(self, name: str) -> list[Any]:
        """取一整列。`name` 也可以是列下标（`int`）。"""
        if isinstance(name, int) and not isinstance(name, bool):
            name = self.cols[name]
        return [r.get(name) for r in self.rows]

    def row(self, index: int) -> dict[str, Any]:
        """取第 `index` 行（返回 `Row`：按列名/按位置都能读）。"""
        return self.rows[index]

    def set_cols(self, cols) -> "Table":
        """改列清单，并**重绑所有行的位置口径**（返回 `self` 便于链式）。

        ⚠️ **改列清单请一律用这个方法，别写 `t.cols = [...]`。**
        直接赋值只会改表头的 `cols`，行的 `row[0]` 还按旧列清单解释 ——
        表现为「按名取值对、按位置取值错」。本仓踩过一次（`boards.py` 收尾时
        把 3 列补成 4 列，结果 `row[0]` 读到的还是旧的第 0 列）。
        """
        self.cols = list(cols)
        self.rows = [_make_row(self.cols, r) for r in self.rows]
        return self


@dataclass(slots=True)
class Dataset:
    """一个数据产品（= 某条命令 + 一组参数）。`Table` 的带元信息版本。"""
    name: str
    table: Table
    params: Any = None

    def __len__(self) -> int:
        return len(self.table.rows)

    def __iter__(self):
        return iter(self.table.rows)

    @property
    def cols(self) -> list[str]:
        return self.table.cols

    @property
    def rows(self) -> list[dict[str, Any]]:
        return self.table.rows


@dataclass(slots=True)
class ProductInfo:
    """产品元信息（`products()` 返回的元素）。"""
    name: str
    cmd: str
    ctl: int
    domain: str = ""
    params: Any = None

    @property
    def ctl_hex(self) -> str:
        return "0x%02X" % self.ctl


def symbol_of(market: int, code: str) -> str:
    """(market, code) → `sh600519`。"""
    p = {1: "sh", 0: "sz", 2: "bj"}.get(int(market), "")
    return p + str(code)
