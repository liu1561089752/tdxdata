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

from .timeliness import parse_yyyymmdd

__all__ = [
    "Bar", "MinutePoint", "Table", "Row", "Dataset", "ProductInfo",
    "Quote", "Tick", "SecurityInfo",
    "Series", "Bars", "Quotes", "Ticks", "Minutes",
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
    #: 本根是不是**盘中未定稿**的当根值（当根日线 / 当根分钟）。
    #: 判定规则见 `tdxdata/timeliness.py`（`judge_bar`），口径写在 `enums.Status`。
    #: ⚠️ 必须留在**最后**：`slots=True` 下位置参数会变，插在中间会把既有调用错位。
    provisional: bool = False

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


#: **声明式不可用**字段（机器可读，不靠 docstring 记性）。
#:
#: 为什么要有这个常量：光在文档里写"不提供"没人能自动检查；写成常量以后
#: `tests/test_tick_fields.py` 可以对着**真实形状的样本**断言「它确实恒为 None」，
#: 而且一旦服务端哪天开始给这个字段，那条断言会红 —— 逼我们更新声明。
#: 这就是把"死字段"变成"有检查的不可用"。
UNPROVIDED_TICK_FIELDS: dict[str, str] = {
    "bs_flag": ("服务端无可判定买卖方向的字段：实测 raw[InOutFlag] 的 0/1 与"
                "相对上一笔涨跌只有 65.9%/76.8% 一致（相关 ≠ 实证），"
                "映射错会把整批方向搞反，故不映射；原始列仍在 tick.raw 里"),
    "trade_count": ("`PBTickEx` 的**一行就是一笔**成交，协议里没有「笔数」字段。"
                    "这个字段恒为 None —— 早前填 0 是凭空造数（会被 sum() 当真）；"
                    "要数笔数请自己 `len(ticks)`"),
}


@dataclass(slots=True)
class Tick:
    """一笔**逐笔成交**（`HQServ:PBTickEx`，2026-10-27 打通）。

    **单位**（实网复核）：`price` 元；`vol` **手**（与 `Bar.vol` / `Quote.vol` /
    `MinutePoint.vol` **同单位**）；`time` 为 `HH:MM:SS`（服务端给的是**当日秒数**，
    例如 `40978` → `11:22:58`）。

    **`bs_flag` 是「声明式不可用」，不是"忘了填"**
    （机器可读的清单见模块级 `UNPROVIDED_TICK_FIELDS`）：
    服务端**没有**可判定买卖方向的字段，所以它恒为 `None`。
    早前给它填 2 是**编数据**（`bs_flag=2` 会被下游读成"卖"），已改掉：
    **拿不到就是 `None`，不填 0 冒充**（本库红线）。

    为什么保留而不是删掉：这个**概念**是真的，用户会来找它；服务端只是不给。
    删掉会让既有读 `.bs_flag` 的脚本 `AttributeError` —— 为了一个本来就拿不到的
    东西制造破坏。`None` 才是诚实的"不知道"。

    ⚠️ **`trade_count` 同为「声明式不可用」（阶段 4 判定，最终保留字段）**：
    `PBTickEx` 一行就是**一笔**成交，协议里**根本没有"笔数"字段**。
    它恒为 `None`——早前填 0 会被 `sum()` 当真、是凭空造数。
    **为什么保留而不删**：冻结的黄金样本证明既有行为本来就是 `null`（不是 0），
    所以删掉**没有任何诚实性增益**，却要为它赔一次样本重录 + 让既有读
    `t.trade_count` 的脚本报 `AttributeError`。要数笔数直接 `len(ticks)`。
    （判定依据见 `UNPROVIDED_TICK_FIELDS`；「永远为 None 的字段」由
    `tests/test_tick_fields.py` 的声明式断言管，服务端哪天开始给值该断言会红。）

    方向相关的原始列在 `raw` 里，自己看、自己判：
      * `InOutFlag`：实测 4 个取值。`0` 相对上一笔涨 65.9%、`1` 跌 76.8%
        （**只是相关**，13%~34% 对不上，所以本库不映射）；
        `5` 的 20 笔**全在 15:05–15:30 且成交价 = 收盘价** = 盘后固定价格交易；
        `2` 零星（含 15:00:01 收盘集合竞价那笔）。
      * `HKTTFlag`：`InOutFlag` 的**单字节回声**（300/300 相等，会是 `\x01` 这种控制字符）。
      * `HKInOutFlag` 实测恒 `"0"`；`VolInStockDiff` 与 `NowVol` 只 77/300 相等 → 不是重复列。
    这些**语义未定**，原样放在 `raw` 里，本库不解释也不删。
    """
    market: int
    code: str
    time: str | None = None   # HH:MM:SS（拿不到就是 None）
    price: float | None = None
    vol: float | None = None  # 手
    trade_count: int | None = None    # **声明式不可用**：见 UNPROVIDED_TICK_FIELDS
    bs_flag: int | None = None       # **声明式不可用**：见 UNPROVIDED_TICK_FIELDS
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def symbol(self) -> str:
        return symbol_of(self.market, self.code)

    #: 逐字段说明（README 的字段表**从这里生成**，别手改 README 那一节）。
    #: 形状：(字段, 含义, 是否有值, 依据/口径)
    #: ⚠️ **不能写类型注解**：`@dataclass(slots=True)` 会把带注解的类属性当成字段
    #: 收进 `__slots__`，`Tick.FIELDS_DOC` 就变成一个 member_descriptor 了（实测踩过）。
    FIELDS_DOC = (
        ("market", "市场号", "有", "0=深 1=沪 2=北（与 `Quote`/`Tick.symbol` 同口径）"),
        ("code", "股票代码", "有", "6 位字符串"),
        ("time", "成交时刻", "有", "`HH:MM:SS`；服务端给当日秒数（如 40978 → 11:22:58）"),
        ("price", "成交价", "有", "元；与 `raw[Now]` 逐笔相等（300/300 实测）"),
        ("vol", "成交量", "有", "**手**；与 `Bar.vol`/`Quote.vol`/`MinutePoint.vol` 同单位"),
        ("trade_count", "笔数", "**不提供（声明式不可用）**",
         "原因见 `models.UNPROVIDED_TICK_FIELDS`：`PBTickEx` 一行就是一笔，"
         "协议里没有「笔数」字段；要数笔数请 `len(ticks)`"),
        ("bs_flag", "买卖方向", "**不提供（声明式不可用）**",
         "原因见 `models.UNPROVIDED_TICK_FIELDS`：服务端无可判定字段，"
         "`raw[InOutFlag]` 0/1 与涨跌只有 65.9%/76.8% 相关，映射会整批搞反"),
        ("raw", "原始行", "有",
         "服务端原列：`Second`/`Now`/`NowVol`/`VolInStockDiff`/`InOutFlag`/"
         "`HKTTFlag`/`HKInOutFlag`，本库不解释"),
    )

    @classmethod
    def as_markdown_table(cls) -> str:
        """`Tick` 字段表（markdown）——README 那一节是它的**逐字副本**。

        为什么由代码生成：字段"有没有值"是**事实**，会随实证变化（`bs_flag` 就从
        "恒 2" 变过 "恒 None"）。手写表格必然漂移，而漂移的文档比没有文档更坏。
        """
        out = ["| 字段 | 含义 | 有值？ | 依据 / 口径 |", "|---|---|---|---|"]
        for name, mean, has, why in cls.FIELDS_DOC:
            out.append("| `%s` | %s | %s | %s |" % (name, mean, has, why))
        return "\n".join(out)


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


class Series(list):
    """带 `meta` 的 **list 子类**：`for` / `len` / 切片 / 索引 / `== [...]` 全兼容。

    为什么是 list 子类，而不是"新的信封类型"
    ----------------------------------------
    语义层要给每次取数挂上「这是什么时刻的、是不是最终值」（`as_of` / `status` /
    `fetched_at` / `source` / `elapsed`）。做成 `{"data": [...], "meta": {...}}`
    这种信封会**破坏**所有既有用法：`len(x)`、`x[0]`、`for bar in x`、
    `x[-1].close`、`json.dumps(x)` 全得改。而本次改造的底线是"只许扩展"。

    于是把 meta 挂在**实例属性**上：`isinstance(bars, list)` 仍为真，
    老代码一行不用改，新代码 `bars.status` 就能问到。代价是 `json.dumps(bars)`
    看不见 meta（那是 JSON 序列化器的语义，不是本类的缺陷）。

    ⚠️ **不要**给它加 `@dataclass(slots=True)`：list 子类要能挂实例属性，
    `slots` 会把这个能力拿掉。

    ⚠️ `meta` 里出现 `status=FINAL` **只表示"这个口径定稿了"**，
    不表示"今天全市场成交都在里面"（日线口径不含盘后固定价格交易，
    详见 `tdxdata/timeliness.py` 模块头）。
    """

    def __init__(self, iterable=(), meta=None):
        super().__init__(iterable)
        self.meta = dict(meta or {})

    # -- 切片/复制要**保住类型与 meta** ------------------------------------
    def __getitem__(self, key):
        """`bars[10:]` 仍返回 `Bars`（而不是退化成 `list`），并**带着同一份 meta**。

        为什么必须管：切片是最常用的取子集方式（`bars[-60:]` 算 60 日均线之类）。
        若切片退化成 `list`，用户拿到的子集就**悄悄丢了 `status`/`as_of`** ——
        而"这批数是不是定稿的"恰恰是切完片之后才要判的事。
        语义层的信息不能因为一次切片而蒸发。
        """
        got = super().__getitem__(key)
        if isinstance(key, slice):
            return type(self)(got, meta=dict(self.meta))
        return got

    def copy(self):
        """`copy()` 也保住类型与 meta（`list.copy()` 会退回普通 `list`）。"""
        return type(self)(self, meta=dict(self.meta))

    # -- 便捷只读属性（读不到就是 None，**不编默认值**）--------------------
    @property
    def as_of(self):
        """这批数据"是什么时候的"（末根的日期/时间，或报价的 `server_time`）。"""
        return self.meta.get("as_of")

    @property
    def fetched_at(self):
        """这批数据"是什么时候取的"。"""
        return self.meta.get("fetched_at")

    @property
    def status(self):
        """时效四态（`enums.Status` 的取值字符串，没判出就是 `None`）。"""
        return self.meta.get("status")

    @property
    def source(self):
        """来源：**固定 5 键**的 dict —— `{host, cmd, ctl, clock, cached}`。

        · `host` —— 哪台服务器（取不到就是 `None`，**键一定在**，不编主机名）；
        · `cmd` / `ctl` —— 走的哪条命令及其 ctl（如 `cmd="HQServ.PBFXT"`, `ctl=14`）；
        · `clock` —— `"server"` / `"local"`，同 `fetch_clock`；
        · `cached` —— 是否命中了 `Client(cache=...)` 的缓存（那 `elapsed` 会≈0）。

        ⚠️ 这个**键集是契约**（`client.META_SOURCE_KEYS`），不许再长 ——
        它跟黄金样本"允许新增键"白名单是同一套工程约束，多一个键要动三处契约文件。
        ⚠️ `Client.last_call["source"]` 刻意是精简三元组 `(host, cmd, ctl)`：
        那是一行日志，这里是可序列化的结构。
        """
        return self.meta.get("source")

    @property
    def elapsed(self):
        """这次调用实际花了多少秒（缓存命中≈0）。"""
        return self.meta.get("elapsed")

    @property
    def fetch_clock(self):
        """这批数据的**时刻来源**：`"server"`（服务端时刻）/ `"local"`（本机兜底）。

        ⚠️ 这是**一等字段**，不是自由文本：`"local"` 就意味着 `fetched_at` 不可全信，
        此时时效判定会自动降级成更保守的一档（见 `tdxdata/timeliness.py`）。
        """
        return self.meta.get("fetch_clock")

    @property
    def clock_note(self):
        """时钟降级说明（没问题时是 `None`）。"""
        return self.meta.get("clock_note")

    def __repr__(self) -> str:
        return "%s(%d 条, meta=%r)" % (type(self).__name__, len(self), self.meta)


class Bars(Series):
    """K 线序列（`get_bars` 的返回）。元素是 `Bar`。"""


class Quotes(Series):
    """报价序列（`get_quotes` / `get_market_page` / `get_universe` / `board_page`）。"""


class Ticks(Series):
    """逐笔成交序列（`get_ticks`）。元素是 `Tick`。"""


class Minutes(Series):
    """当日分时序列（`get_minutes`）。元素是 `MinutePoint`。"""


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

    # -- 语义层便捷属性（只读，读 `meta`；**结构不变**）--------------------
    @property
    def as_of(self):
        """这张表的数据时刻（`meta["as_of"]`；判不出来就是 `None`，不编）。"""
        return self.meta.get("as_of")

    @property
    def status(self):
        """时效四态（`meta["status"]`；没登记过时效口径的表不会是 `final`）。"""
        return self.meta.get("status")

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

    def inherit_meta(self, *sources, **extra) -> "Table":
        """**继承上游 meta**，再叠本次的键（`extra` 优先）。就地改，返回 `self`。

        为什么要有它（而不是在每个调用点手工 `dict(src.meta)`）
        ------------------------------------------------------
        `sections/*` 里的 `_shape()` 会**重建**一张 `Table`（换标准列名、补 `raw`）。
        重建时若从空 meta 开始，上游 `client.call()` 刚盖上去的
        `as_of` / `status` / `fetched_at` / `elapsed` / `source` 就**全丢了** ——
        这正是复核者判的那条阻断（`sections/boards.py` 的收尾重建点）。
        逐点手工 copy 是**必然**会漏的（本项目已有 5 处直造点漏过），
        所以做成一个**单点**工具：所有"由 `Table` 派生出 `Table`"的地方都调它。

        用法::

            return Table(cols=…, rows=…).inherit_meta(src, unavailable=["change_pct"])

        `sources` 里可以有 `Table`、任何带 `.meta` 的对象（如 `Series`/`Bars`）、
        也可以直接给 `dict`；靠前的先铺、靠后的覆盖，最后 `extra` 覆盖一切。
        """
        base: dict[str, Any] = {}
        for src in sources:
            if isinstance(src, dict):
                base.update(src)
                continue
            meta = getattr(src, "meta", None)      # Table / Series / Bars …
            if isinstance(meta, dict):
                base.update(meta)
        base.update(extra)          # ⚠️ 不过滤 None：「知道它是 None」也是有信息量的
        self.meta = base
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

    @property
    def data_date(self):
        """这张表的**数据日**（`datetime.date`）；判不出来就是 `None`。

        取值顺序：
          1. `table.meta["data_date"]`（谁知道的谁写，最可靠）；
          2. 从首行 `N001` 推 —— 7615 数据中心产品的第一列惯例就是日期
             （`"20261027"` / `"2026-10-27"` 两种形态都认）。

        ⚠️ **推不出来就给 `None`**：绝不许拿 `0`、空串或"今天"顶替。
        本项目红线是 **unknown ≠ 0** —— "不知道是哪天的"和"今天的"是两件事，
        混成一个会让下游把过期数据当今日数据用（那正是本项目出过事故的形态）。
        """
        meta_date = parse_yyyymmdd(self.table.meta.get("data_date"))
        if meta_date is not None:
            return meta_date
        rows = self.table.rows
        if not rows:
            return None
        first = rows[0]
        try:
            got = first.get("N001")
        except AttributeError:                    # 不是 dict 形态（理论上不会发生）
            return None
        return parse_yyyymmdd(got)


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
