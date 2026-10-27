# -*- coding: utf-8 -*-
"""`tdxdata.Client` —— 库的唯一入口。

用法
----
    import tdxdata

    with tdxdata.Client() as c:
        bars = c.get_bars("600519", period="day", count=5)      # list[Bar]
        mins = c.get_minutes("600519")                          # list[MinutePoint]
        t    = c.get("lhb", date="2026-10-23")                  # Table（421 个产品之一）
        cols, rows = c.call("CWServ.ph_datacenter_hg_holiday", 0x21, ["001", "1"])

线程模型：**同步**。7615 是「握手 + 慢速串行」协议，真瓶颈在服务端限流，
异步/并发收益极小，反而容易触发断连（实测 10053/10054）。需要异步的调用方
在外层用 `asyncio.to_thread` 即可，不必让库承担两套实现。

并发（阶段 5，**默认关闭**）
--------------------------
`Client(concurrency=1)` 是默认值：**完全不起线程池、不建额外连接**，走的是
和改造前逐字节相同的代码路径。只有显式 `concurrency>1`（keyword-only）时，
才会建 `min(concurrency, 4)` 条 lane，第 i 条 lane 钉死 `HOSTS[i]` 并用
`pacer.wait(host=...)` 的**独立槽位** —— 详见 `Client` 类文档的"并发与限流"。
"""
from __future__ import annotations

import json
import os
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Any

from . import _core, errors, series_kinds, symbols, timeliness
from .enums import Adjust, KlinePeriod, Status
from .models import (
    Bar, Bars, Dataset, MinutePoint, Minutes, ProductInfo, Quote, Quotes,
    Table, Tick, Ticks,
)
from .timeliness import FINAL_CUTOFF_HHMM, NowInfo, probe_or_none

__all__ = ["Client"]

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "products.json")

# KlinePeriod 枚举 → 协议周期字符串（**不能用 name.lower()**：MIN_60 会变成 min_60）
_PERIOD_STR = {
    "MIN_1": "1m", "MIN_5": "5m", "MIN_15": "15m", "MIN_30": "30m",
    "MIN_60": "60m", "DAY": "day", "WEEK": "week", "MONTH": "month",
    "SEASON": "season",
}
# 服务端实测无数据：明确报错，不返回空列表
_PERIOD_DEAD = {"MIN_3": "3m", "YEAR": "year", "YEAR_ALT": "year"}

# 记录哪些方法尚未实现（诚实告知，而不是静默返回空）
# 2026-10-25：`get_universe` 已实现（PBMultiHQ 参数解构后可按市场翻页），从本表移除。
# 2026-10-27：`get_quotes` 已实现（改走 `PBCombHQ`，它的 field4 就是代码列表），从本表移除。
_NOT_IMPL = {
}

# `Adjust` → PBFXT `field8`（**枚举值不是协议码**：Adjust.QFQ==1，而协议 1 表示不复权）
_ADJUST_CODE = {"NONE": 1, "QFQ": 3, "HFQ": 2}
_ADJUST_NAME = {1: "NONE", 2: "HFQ", 3: "QFQ", 4: "FIELD8_4"}

#: 容器/表 `meta` 里**允许出现**的键（阶段 1 语义层）。
#:
#: ⚠️ 这份清单是**契约**，不是风格偏好：
#: `tests/golden_runner.ALLOWED_ADDED_KEYS` 与
#: `tests/test_golden_contract.ALLOWED_ADDED_KEYS` 各写了一份同样的键，
#: 黄金样本回放逐字段比对时只放行这些"新增键"。多写一个键 → 23 份样本全报差异。
#: 要扩键必须**三处一起改**。
#:
#: 分两组：
#:   · **数据信号**：`as_of` / `fetched_at` / `status` / `provisional` / `data_date`
#:     —— 这些是"能不能拿这批数下结论"的依据，**只许放在结构化字段里**；
#:   · **来源凭据**：`source` / `elapsed` / `fetch_clock` / `clock_note` /
#:     `status_note` / `as_of_note` —— 记录"这个数是哪来的、时钟可不可信"。
#:     ⚠️ `fetch_clock` 是**一等字段**（`"server"` / `"local"`），不是自由文本；
#:     `*_note` 只承载"为什么"，**绝不承载唯一信息**（删掉它，结论仍要从
#:     `as_of`/`status`/`fetch_clock` 拿得到）。
META_KEYS = frozenset({
    "meta", "as_of", "fetched_at", "status", "source", "elapsed",
    "provisional", "data_date",
    # 2026-10-27 Lead 批准：来源凭据类
    "fetch_clock", "clock_note", "status_note", "as_of_note",
})

#: `meta["source"]` 的**子键集**（Lead 2026-10-27 拍板：形状固定，不许再长）。
#:
#: 为什么是 dict 而不是 `(host, cmd, ctl)` 三元组：黄金样本白名单只放行**精确键名**，
#: 而"时钟可不可信"（`clock`）与"是不是缓存"（`cached`）都是来源凭据、必须可见 ——
#: 两条都塞进 `source` 的**值**里是最小改动（值不参与比对）。
#: ⚠️ `host` 取不到就给 `None`（**键必须在**，不许省）：省键会让下游 `source["host"]`
#: 抛 `KeyError`，而"这台服务器是谁"本身就是排查要问的第一个问题。
#: `Client.last_call["source"]` 仍是精简的三元组 `(host, cmd, ctl)`，两者刻意不同。
META_SOURCE_KEYS = ("host", "cmd", "ctl", "clock", "cached")

#: 「这条通道为什么给不出 `as_of`」的标准说法（数据中心 JSON 通道专用）。
#:
#: ⚠️ 它必须写在 **`as_of_note`** 里，**不许**塞进 `status_note`：
#: `status_note` 回答"为什么判成这个时效档"，`as_of_note` 回答"为什么没有数据时刻"。
#: 合成一句会让人分不清"是数据不定稿"还是"是通道压根没给时刻"——两件事的处置完全不同
#: （前者等收盘，后者只能自己按数据日推断）。
_NO_AS_OF_DC = ("这条通道（数据中心 JSON，`ResultSets`）回包**不带数据日期/时间**，"
                "所以 as_of 无法确定；要知道数据日请用 `Client.get(...).data_date`"
                "（从首行 N001 推）或本表的 meta[\"data_date\"]")

#: `Client.capabilities()` 的**列名 → `Capability` 字段名**（阶段 6）。
#:
#: 唯一不一致的是 `capability` ← `name`：对外叫 `capability` 是为了自解释
#: （"这一行说的是哪种能力"），而内部字段叫 `name` 是为了跟 `ProductInfo` 等
#: 既有 dataclass 的口径一致。**不改列名**：列名是对外契约（测试逐列核对）。
_CAP_FIELD = {
    "instrument": "instrument",
    "capability": "name",
    "interface": "interface",
    "available": "available",
    "reason": "reason",
    "evidence": "evidence",
    "workaround": "workaround",
}


class _Lane:
    """一条并发 lane：**钉死**一台服务器 + 自己的 `Session` + 自己的锁。

    为什么 lane 必须有自己的 `Session`：`Session` 里是一根 socket + 一把 `RLock`，
    两条 lane 共用一根 socket 就等于还是串行（`ask()` 会互相阻塞），
    "钉主机"就成了摆设 —— 那样并发收益必然是 0%。所以一条 lane 一条长连接。

    为什么还要一把 `lane.lock`：lane 是**逻辑槽位**（下标 `i % lanes`），
    `ThreadPoolExecutor` 不保证同一个下标永远落在同一个物理线程上；
    这把锁保证"同一时刻只有一个线程在用这条 lane / 这根 socket"。
    """

    __slots__ = ("host", "sess", "lock")

    def __init__(self, host: str):
        self.host = host
        self.sess = None                 # 懒建：不预热、不额外建连
        self.lock = threading.Lock()

    def __repr__(self) -> str:                                # pragma: no cover
        return "<lane %s sess=%s>" % (self.host, "up" if self.sess is not None else "-")


class Client:
    """通达信 7615 数据客户端（同步，可作上下文管理器）。

    参数
    ----
    host : str | None
        指定单台服务器；不给则用内置 4 台轮询。
    timeout : float
        单次收发超时（秒）。
    gap : float
        两次命令之间的最小间隔（秒）。**默认 2.2**，低于 2 会被限流。
    cache : TTLCache | None
        **默认 None = 不缓存**（保持"每次都真的去服务端"这一可预测语义）。
        需要减少重复请求时显式传一个缓存对象，例如
        `Client(cache=tdxdata._engine.cache.TTLCache(ttl=300))` ——
        命中缓存**不占用限流闸门**（未命中才 `wait()`）。
    concurrency : int
        **keyword-only，默认 1 = 完全串行**（与改造前逐字节相同的路径：
        不起线程池、不建额外连接、`pacer.wait()` 不传 `host`）。
        `1 < concurrency <= 4`（= 内置服务器数）时建 `concurrency` 条 lane，
        第 i 条 lane **钉死** `HOSTS[i]`，每条 lane 有自己的长连接和自己的
        `pacer` 槽位；只有**明确列出的"彼此独立的多次调用"**（目前是
        `get_market_stat('industry')` 的 30 个一级行业码）会真的走并发。
        `> 4` → **`ValueError`**（不是截断）：第 5 条 lane 只能和别的 lane
        共用同一个 per-host 桶，**不会更快**，只会多占一条连接。
        实际生效值见 `rate_stats()["concurrency_effective"]`。

    语义层（阶段 1）与它的**取数成本**
    ---------------------------------
    每个返回值都带"这是什么时刻的、是不是最终值"（`meta` 见 `META_KEYS`；
    容器还有 `Bars/Quotes/Ticks/Minutes` 上的只读属性）。为此需要两样外部事实：
    ① **服务器当前时刻**（判断"今天这根走完没有"）；② **上一交易日**（判断 T+1 产品）。
    两者都**懒取 + 缓存**，且都可以注入假时钟：

    | 事实 | 取法 | 成本 |
    |---|---|---|
    | 服务器时刻 | 一条 `PBCombHQ` 只问 `999999` 的 `HqDate`+`HqTime` | 首次 1 次请求，之后锚点+单调增量，**超 30 分钟**才重取 |
    | 上一交易日 | `get_bars("999999", count=2)` 的两根日期 | 每个服务器日 **1 次**（取不到就降级，不重试刷屏） |

    ⚠️ 上面两次请求是本库**唯一**会"自己多发"的取数。它们受
    `clock_probe`（keyword-only，**默认 `True`**）控制：传 `clock_probe=False`
    就**一次探针都不发**（连"上一交易日"也不去取），此时
    `meta["fetch_clock"]="local"`、`meta["clock_note"]="local-clock-disabled-by-user"`
    （刻意与"探针失败"的 `"local-clock-fallback"` 区分，两者原因不同），
    时效判定照旧按保守档降级。**不许让用户在不知情时多花配额**。

    ⚠️ 取不到就**降级并标注**（`meta["fetch_clock"]="local"` +
    `meta["clock_note"]`），且时效判定自动更保守 ——
    **绝不拿本机时钟冒充服务器时刻而不标注**。
    ⚠️ 用 `Client.__new__` 造的裸对象（测试里那种）**不会**为补 meta 而联网。

    并发与限流（阶段 5，**默认关**）
    ------------------------------
    **默认 `concurrency=1` = 不增加任何服务器压力。** 这不是"优化没做"，
    而是刻意的保守默认：`_engine/pacer.py` 的模块头写了，库内部对"配额按
    来源 IP 算还是按 IP × 服务器算"**至今没有定论**。在没定论之前替用户
    多开连接、多占槽位，等于替所有用户私自放大请求频率。

    `concurrency>1` 是**用户显式自担风险**的开关，语义边界写在下面三条：

    | `concurrency` | 连接 | `pacer` 槽位 | 收益 |
    |---|---|---|---|
    | `1`（默认） | 就是原来那一条 | 只有**全局**槽位（`wait()` 不传 host） | 与改造前逐字节相同 |
    | `2..4` | 每条 lane 一条，钉 `HOSTS[i]` | 每条 lane **独立**槽位 | 多主机能否分摊，**取决于服务端配额口径** |
    | `>4` / `<1` / 非整数 | — | — | `ValueError`（第 5 条 lane 只会共用桶，不会更快 → 拒绝，不静默截断） |

    ⚠️ **可能触发按来源 IP 的限流**：如果服务端按来源计配额（这是更保守、
    也更可能的那种口径），多开 lane **不会更快**，只会更容易被踢 ——
    一次限流事件的实测代价是 **+28.15 s**。所以默认必须是 `1`。
    ⚠️ 并发**只**用在"彼此独立的多次调用"上（例：30 个一级行业码逐个取明细）。
    **有依赖顺序的链路一律串行**（翻页必须一页接一页，先目录后明细必须等目录）。
    ⚠️ 结果**顺序稳定**：并发完成先后绝不影响输出顺序，拼装按输入原序。
    ⚠️ `close()` 会 `shutdown(wait=True)` 收掉线程池并关掉每条 lane 的连接，
    不留悬挂线程。
    """

    def __init__(self, host: str | None = None, timeout: float = 15.0,
                 gap: float = 2.2, cache=None, *, clock_probe: bool = True,
                 strict_symbols: bool = False, concurrency: int = 1):
        from ._engine import session as _session

        from ._engine import pacer as _pacer

        from ._engine import datacenter as _dc

        self._dc_mod = _dc
        self._session_mod = _session
        self._pacer = _pacer.get_pacer(base_gap=gap)   # 进程级单例
        self._sess = None                 # 懒建连
        self._ensure_lock = threading.Lock()   # 防多线程重复建连
        self._host = host
        self._timeout = timeout
        self._gap = gap
        self._cache = cache               # None = 不缓存（默认行为不变）
        self._products: dict[str, dict] = {}
        self._pb_products: dict[str, dict] = {}
        # ── 并发（阶段 5，**默认关**）─────────────────────────────────────
        #: `concurrency` 的**请求值**（用户传什么就记什么，截断另记）。
        self._concurrency, self._concurrency_eff = self._check_concurrency(
            concurrency, len(_session.HOSTS))
        #: 线程池。**`concurrency==1` 时永远是 `None`** —— 默认路径一个线程都不起。
        self._pool = None
        #: lane 列表；`concurrency==1` 时是**空列表**（连对象都不造）。
        self._lanes: list[_Lane] = []
        #: 线程局部：当前线程跑在哪条 lane 上（不在 lane 上 → 没有这个属性）。
        self._tls = threading.local()
        if self._concurrency_eff > 1:
            self._lanes = [_Lane(_session.HOSTS[i % len(_session.HOSTS)])
                           for i in range(self._concurrency_eff)]
        # ── 语义层状态（阶段 1 新增；全部有默认值，不影响既有签名）────────
        #: 时钟（`timeliness.Clock`）。`None` = 懒建 `ServerClock`；单测可注入 `FakeClock`。
        self._clock = None
        #: 最近一次取数的来源/耗时/时刻（排查用，见 `Client.last_call`）。
        self._last_call: dict = {}
        #: 交易日历缓存（只缓存"上一交易日"，同一天只取一次）。
        self._cal: dict = {}
        #: 用户是否允许为取时刻/交易日发"便宜的探针"（`__init__` 的 `clock_probe`）。
        self._clock_probe = bool(clock_probe)
        #: 实际是否允许探针。⚠️ 只有跑过 `__init__` 的 Client 才有；
        #: 测试里用 `Client.__new__` 造的裸对象（不建连）读不到 → 直接当 False，
        #: 绝不能因为补 meta 而真去联网。
        self._probe_allowed = self._clock_probe
        self._in_cal = False              # 防"取上一交易日"递归重入
        #: 两义代码（`000001`/`000300`/`000852`/`000016`）是否**升级成异常**。
        #:
        #: 默认 `False` = 只 `warnings.warn` 提醒、照旧解析（"两义"不等于"错"：
        #: `000001` 确实是平安银行）。批处理/入库这类要确定性的场景传 `True`，
        #: 拿不到确定答案就 `errors.AmbiguousSymbol` 炸掉，而不是生成一批错口径的数据。
        self._strict_symbols = bool(strict_symbols)
        self._load_products()

    @staticmethod
    def _check_concurrency(value, hosts: int) -> tuple[int, int]:
        """校验 `concurrency`，返回 `(请求值, 生效值)`。

        规则只有一条（阶段 5，Lead 拍板）：

        ```
        1 <= concurrency <= len(HOSTS)  → 正常（lane = concurrency，第 i 条钉 HOSTS[i]）
        concurrency >  len(HOSTS)       → ValueError
        concurrency <  1 / 非整数        → ValueError
        ```

        **为什么上限是 `len(HOSTS)` 而不是别的数**：per-host 桶的全部意义就是
        "每条 lane 独占**一台不同**的主机"。第 5 条 lane 必然和某条 lane 共用
        同一个桶 → **它不会更快**，只会多占一条连接、多一份被踢的概率。
        所以宁可**拒绝**，也不静默截断（"截断 = 用户以为拿到了 99 的语义，
        实际拿到 4" —— 这与本库「宁可拒绝，不许静默降级」的纪律直接冲突）。

        想要更快只有两条路：**减少调用次数**，或**等服务端放宽配额**；
        跨进程/跨机分摊也可以，但那不归一个进程内的 `concurrency` 管。

        `hosts` 由调用方传（= `session.HOSTS` 的长度，现在是 4），
        这样这个函数不依赖模块级常量，单测可以直接喂不同的 `hosts`。
        """
        try:
            n = int(value)
            integral = (float(value) == n)
        except (TypeError, ValueError):
            raise ValueError(
                "concurrency 必须是整数（收到 %r）" % (value,)) from None
        if not integral:
            raise ValueError(
                "concurrency 必须是整数（收到 %r）—— 没有「半条 lane」这回事。"
                % (value,))
        if n < 1:
            raise ValueError(
                "concurrency 必须 ≥ 1（收到 %r）。默认 1 = 完全串行、不起线程池、"
                "不建额外连接。" % (value,))
        hosts = int(hosts)
        if n > hosts:
            raise ValueError(
                "concurrency=%d 超过上限 %d（= 内置服务器数）。"
                "每条 lane 必须绑定**一台不同**的主机，per-host 桶才有意义；"
                "第 %d 条 lane 只能和别的 lane 共用同一个桶 → 它**不会更快**，"
                "只会多占一条连接、多一份被踢的概率。想要更快只有两条路："
                "减少调用次数，或等服务端放宽配额（跨进程/跨机分摊也可以，"
                "但那个不归一个进程内的 concurrency 管）。"
                % (n, hosts, hosts + 1))
        return n, n

    # ── 产品表 ────────────────────────────────────────────────────────────
    def _check_symbol(self, symbol):
        """**所有收 `symbol` 的公开方法都必须过这里**，再往下交给 `symbols.parse`。

        为什么不放在 `symbols.parse` 里：`parse()` 是**纯函数**（给定输入给输出，
        无 warning、无"这一次要严格"的全局状态），这是它能在别处被安全复用的前提。
        "要不要提醒、要不要炸"是**调用方策略**，属于 Client。

        做两件事：
        1. 走 `symbols.parse`（位数不对 → `OutOfScope`；期货形态 → `OutOfScope`；
           垃圾输入 → `ValueError`）；
        2. **只在市场是猜出来的**时候，对两义代码提醒（`000300` 常被当成沪深300，
           实际那只在沪市）。显式写了 `sh000300` 就不再提醒 —— 否则指路文案自相矛盾。
        """
        market, code = symbols.parse(symbol)
        if code in symbols.AMBIGUOUS and not symbols.is_explicit(symbol):
            msg = ("%s 是**两义代码**：%s（本次按 %r 解析 market=%d）"
                   % (code, symbols.AMBIGUOUS[code], symbol, market))
            if getattr(self, "_strict_symbols", False):
                raise errors.AmbiguousSymbol(
                    msg + " —— 你开了 strict_symbols=True，两义代码一律拒绝；"
                          "确认口径后请显式写前缀（如 \"sh%s\"）。" % code)
            warnings.warn(msg, stacklevel=3)
        return market, code

    def _load_products(self) -> None:
        if not os.path.exists(_DATA):
            raise errors.TdxError("产品数据缺失：%s" % _DATA)
        with open(_DATA, encoding="utf-8") as f:   # 必须 with：裸 open 会漏句柄
            payload = json.load(f)
        self._products = payload.get("products", {})
        self._pb_products = payload.get("pb_products", {})

    def products(self, domain: str | None = None) -> list[ProductInfo]:
        """列出全部数据产品（可按域过滤）。"""
        out = []
        for name, spec in self._products.items():
            if domain and spec.get("domain") != domain:
                continue
            out.append(ProductInfo(name=name, cmd=spec["cmd"], ctl=spec["ctl"],
                                   domain=spec.get("domain", ""),
                                   params=spec.get("params")))
        return out

    def domains(self) -> list[str]:
        """列出有产品的域。"""
        return sorted({v.get("domain", "") for v in self._products.values()})

    # ── 底层调用 ──────────────────────────────────────────────────────────
    def _current_lane(self):
        """当前线程跑在哪条 lane 上？不在任何 lane 上 → `None`。

        ⚠️ 这是**默认路径与并发路径的唯一分岔点**：`None` 时下面每一处都走
        和改造前一字不差的老代码（全局槽位 + 实例那一条 `Session`）。
        ⚠️ 用 `Client.__new__` 造的裸对象没有 `_tls`，这里必须能返回 `None`。
        """
        tls = getattr(self, "_tls", None)
        return getattr(tls, "lane", None) if tls is not None else None

    def _pace(self) -> None:
        """走**进程级**匀速闸门（不是本实例私有时钟）。

        这样同一进程里开多个 Client 也不会把请求频率叠加起来 ——
        服务端限流是按来源算的，不是按 Client 对象算的。

        阶段 5：在 lane 上时改用**这条 lane 的主机**的独立槽位。
        ⚠️ 不在 lane 上时调用的是 `wait()`（**不传 `host=`**）——
        参数列表和调用形态与改造前完全一致，`golden_runner._NoPacer` 那种
        只实现 `wait(self)` 的假闸门照样能用。
        """
        lane = self._current_lane()
        if lane is None:
            self._pacer.wait()
        else:
            self._pacer.wait(host=lane.host)

    def _note_limited(self) -> float:
        """记一次限流（间隔翻倍 + 推槽位）。

        ⚠️ 不在 lane 上时调用的是 `note_limited()`（**不传 `host=`**），
        与改造前完全一致；在 lane 上才连带推该主机的槽位
        （`Pacer.note_limited(host=…)` 内部**同时**推全局槽位，保守）。
        """
        lane = self._current_lane()
        if lane is None:
            return self._pacer.note_limited()
        return self._pacer.note_limited(host=lane.host)

    def _ensure(self):
        """懒建连（长连接复用；断了换服务器重连）。

        **必须加锁**：多线程同时首次调用时，不加锁会各自建一条连接，
        实测 3 线程建出 4 条（多余连接泄漏且各自被服务端计入频率）。

        阶段 5：在 lane 上时返回**这条 lane 自己的** `Session`（钉死主机），
        不复用实例那条 —— 否则多 lane 会抢同一根 socket，等于还是串行。
        """
        lane = self._current_lane()
        if lane is not None:
            with lane.lock:
                if lane.sess is not None and lane.sess.alive:
                    return lane.sess
                lane.sess = self._session_mod.Session(
                    lane.host, self._timeout, pin=True).connect()
                return lane.sess
        if self._sess is not None and self._sess.alive:
            return self._sess
        with self._ensure_lock:
            if self._sess is None or not self._sess.alive:
                self._sess = self._session_mod.Session(
                    self._host, self._timeout).connect()
            return self._sess

    def _live_session(self):
        """当前上下文实际在用的那条 `Session`（lane 上 → lane 的；否则实例的）。"""
        lane = self._current_lane()
        if lane is not None:
            return lane.sess
        return self._sess

    def _drop_session(self) -> None:
        """丢弃当前上下文那条 `Session`（断连且重连也失败时用）。

        行为与改造前一致：只把引用置 `None`，**不在这里 close**
        （重连/换机内部已经 `_kill()` 过 socket）。
        """
        lane = self._current_lane()
        if lane is not None:
            lane.sess = None
        else:
            self._sess = None

    # ── 并发（阶段 5，**默认关**）─────────────────────────────────────────
    def _executor(self, workers: int):
        """懒建 lane 线程池（`concurrency>1` 才可能被调用到）。"""
        if self._pool is None:
            self._pool = ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="tdxdata-lane")
        return self._pool

    def _run_in_lane(self, idx: int, fn, item):
        """在线程池里跑一个任务：把它**标记**成"在 lane idx 上"。"""
        lane = self._lanes[idx]
        tls = getattr(self, "_tls", None)
        with lane.lock:                     # 同一条 lane 同一时刻只有一个线程
            prev = getattr(tls, "lane", None) if tls is not None else None
            if tls is not None:
                tls.lane = lane
            try:
                return fn(item)
            finally:
                if tls is not None:
                    tls.lane = prev

    def _map_independent(self, fn, items, *, what: str = ""):
        """把**一批彼此独立**的取数跑完，结果**按 `items` 原序**返回。

        这是本库唯一"会真的并发"的入口，用它的前提是三条**同时**满足：

        1. 这些调用**彼此独立**（互不依赖对方的返回值）；
        2. 拼装结果时**顺序有意义**，所以要按输入原序拿回结果；
        3. 单个失败不该拖垮整批（失败怎么处置由 `fn` 自己决定 ——
           `fn` 抛什么，这里就按**原类型**往外抛第一个）。

        `concurrency == 1`（默认）→ **纯串行** `[fn(x) for x in items]`：
        **不建线程池、不进队列、不碰 `ThreadPoolExecutor`** —— 这是
        "默认路径逐字节不变"的技术保证，不是口头承诺。

        `concurrency > 1` → 提交到 lane 池，第 `i` 个任务跑在 lane `i % lanes`
        上（该 lane 的主机 = `HOSTS[i % len(HOSTS)]`），完成后**按 `i` 原序**拼装。

        ⚠️ **不要**用它并发化有顺序依赖的链路：翻页（下一页的 offset 依赖上一页）、
        先取目录后逐项取明细（明细的 key 来自目录）都属于"必须串行"。
        目前唯一被允许的用法是 `get_market_stat('industry')` 的 30 个一级行业码。
        """
        items = list(items)
        workers = getattr(self, "_concurrency_eff", 1)
        if workers <= 1 or len(items) <= 1:
            return [fn(x) for x in items]
        # 先把时钟锚点取好：否则 4 条 lane 会同时首次触发时钟探针（多一次请求）
        try:
            self._now()
        except BaseException:                                # noqa: BLE001
            pass                                             # 补 meta 绝不拖垮取数
        pool = self._executor(min(workers, len(items)))
        futures = [pool.submit(self._run_in_lane, i % workers, fn, x)
                   for i, x in enumerate(items)]
        out: list = []
        first_err: BaseException | None = None
        for fut in futures:
            try:
                out.append(fut.result())
            except BaseException as exc:                     # noqa: BLE001
                out.append(None)
                if first_err is None:
                    first_err = exc
        if first_err is not None:
            raise first_err       # 按**原类型**抛出（EmptyResult 之类由 fn 自己先接住）
        return out

    # ── 语义层：时刻 / 时效 / 来源（阶段 1）───────────────────────────────
    #
    # 这一组方法只做两件事：**给每次取数标上"什么时候的、是不是最终值"**，
    # 以及**记住这次请求打到哪儿、花了多久**。它们**绝不改变任何取值语义**
    # （不改返回的数据、不改重试/退避、不加 socket 行为），且**自身永不抛错**：
    # 取不到时刻就降级成本机时钟并标注，绝不让"元信息不可得"拖垮一次取数。
    def _clock_obj(self):
        """取（或懒建）本实例的时钟。

        · 外部注入（`self._clock = FakeClock(...)`）→ 直接用；
        · 否则建 `HybridClock`（**双源交叉校验**：服务器日期 + 本机时分 + 指数收盘标记），
          探针 = 一条便宜的行情查询；
        · 未初始化（`Client.__new__` 造出来的测试裸对象）→ 建**无探针**的
          时钟，直接走本机时钟并标注 —— 这种对象绝不能因为补 meta 去联网。
        """
        clk = getattr(self, "_clock", None)
        if clk is not None:
            return clk
        if not getattr(self, "_probe_allowed", False):
            clk = timeliness.HybridClock(
                None, note="不允许探针（用户关了 clock_probe，或 Client 没跑 __init__）")
        else:
            clk = timeliness.HybridClock(self._probe_quote_time)
        self._clock = clk
        return clk

    def _local_clock_note(self) -> str:
        """本机时钟兜底时写进 `meta["clock_note"]` 的**稳定**取值。

        两个值刻意区分开（都是有限枚举，不随过程变，否则黄金门禁会抖）：
          · `"local-clock-disabled-by-user"` —— 用户传了 `clock_probe=False`；
          · `"local-clock-fallback"` —— 想探针但没拿到服务器时刻。
        两者原因完全不同，混成一个会让"用户主动关掉"看起来像"探针坏了"。
        """
        return ("local-clock-disabled-by-user"
                if not getattr(self, "_clock_probe", True)
                else "local-clock-fallback")

    def _snapshot_status(self):
        """「**快照类**通道」的时效判定（报价/块数据/抽样/板块统计都用它）。

        这些通道回的都是"服务端此刻的截面"，没有数据日期、也没有分页时刻，
        所以只能按 CLOSE_CUTOFF 判 `FINAL` / `PROVISIONAL`，`as_of` 一律 `None`
        + `as_of_note` 说明为什么（阶段 1.5 的契约：`as_of is None` 必须配 note）。
        """
        return timeliness.judge_intraday(self._now().at,
                                         cutoff=timeliness.CLOSE_CUTOFF)

    def _now(self) -> NowInfo:
        """服务器当前时刻（带锚点缓存）。**永不抛错。**

        返回值里的 `clock` 标明这次是服务端时刻（`"server"`）还是本机兜底
        （`"local"`）—— 后者一定带 `note`，下游能看出"这个时刻不可全信"。
        """
        try:
            info = self._clock_obj().now()
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException as exc:                        # noqa: BLE001
            return NowInfo(datetime.now(), "local",
                           "local-clock-fallback: clock.now() 抛 %s" % type(exc).__name__)
        if not isinstance(info, NowInfo):                    # 注入的时钟不合协议
            return NowInfo(datetime.now(), "local",
                           "local-clock-fallback: 注入的时钟没按 Clock 协议返回 NowInfo")
        return info

    def _probe_quote_time(self):
        """一条**便宜的**行情查询取 `(服务器交易日, 最后成交时刻)`（指数 999999）。

        为什么是 `PBCombHQ`：它是唯一**回包里带服务端日期/时间**的通道
        （`Quote.server_time` 就是从这两列来的），一次 1 个代码的请求最省。
        拿不到就返回 `None`，由 `HybridClock` 退回本机时钟并标注。

        ⚠️ 这里**不复用** `self.get_quotes()`：那个方法自己也要补 meta，
        走它会绕回本方法造成递归。所以直接用 `_engine` 层。

        ⚠️ 返回的是**两个事实**，不是一个时刻：`HqDate` 是可信的"今天哪一天"，
        而 `HqTime` 是"最后成交时刻"（收盘后冻结，不能当"现在几点"）。
        怎么用见 `timeliness.HybridClock` 的类文档。
        """
        from ._engine import hq_extra as _x

        cols, rows = _x.get_quotes(["999999"], batch=1)
        if not rows:
            return None
        raw = dict(zip(cols, rows[0]))
        d = timeliness.parse_yyyymmdd(raw.get("HqDate"))
        t = timeliness.parse_hhmmss(raw.get("HqTime"))
        if d is None or t is None:
            return None
        return (d, t)

    def _prev_trade_date(self):
        """**上一交易日**（`datetime.date`）；取不到 `None`。

        为什么懒取而不是查表：本库**不维护节假日表**（那是第二份事实源，
        法定调休一变就错，错得还静默）。直接问服务端要两根日线最实在 ——
        `get_bars("999999", count=2)` 的两根日期里：
          · 末根是今天 → 上一交易日 = 前一根；
          · 末根不是今天（周末/休市）→ 末根本身就是上一交易日。
        同一天（按服务器日期）只取一次，之后走缓存。取不到就 `None`，
        判定端会降级成 `PROVISIONAL`/`DELAYED`（**绝不当成 FINAL**）。

        ⚠️ `clock_probe=False`（或裸 Client）时**一次都不去取** —— 这是硬约束：
        用户关掉探针就等于"一个多余请求都不许发"，所以这里也必须一起关。
        """
        cal = getattr(self, "_cal", None)
        if cal is None:
            cal = self._cal = {}
        info = self._now()
        key = info.at.date().isoformat()
        if cal.get("key") == key:
            return cal.get("prev")
        self._cal = cal = {"key": key, "prev": None}
        if getattr(self, "_in_cal", False):          # 递归重入：直接给 None，别绕圈
            return None
        if not getattr(self, "_probe_allowed", False):
            return None                              # 关掉探针 / 裸对象：零网络
        self._in_cal = True
        try:
            bars = probe_or_none(lambda: self.get_bars("999999", period="day", count=2))
            today = info.at.date()
            older = [d for d in (timeliness.parse_yyyymmdd(getattr(b, "date", ""))
                                 for b in (bars or [])) if d is not None and d < today]
            cal["prev"] = max(older) if older else None
        finally:
            self._in_cal = False
        return cal["prev"]

    def _session_host(self):
        """长连接那台服务器的主机名（取不到 `None`，**不编**）。

        阶段 5：在 lane 上时如实报**那条 lane** 的主机 ——
        `meta["source"]["host"]` 要能回答"这个数是从哪台服务器拿的"，
        报实例那条（可能压根没建）就是假陈述。
        """
        lane = self._current_lane()
        if lane is not None:
            return getattr(lane.sess, "host", None) if lane.sess is not None else None
        sess = getattr(self, "_sess", None)
        return getattr(sess, "host", None) if sess is not None else None

    def _pb_host(self):
        """PB 持久连接当前落在哪台主机（取不到 `None`，**不编**）。

        `PB*` 通路是模块级持久连接（不走 `Session`），主机名记在
        `datacenter._CONN["host"]` 里 —— 这里只**读**，不改任何连接行为。
        `_engine/session.py` 的 `HOSTS` 是 **4 台**，不是 11 台。
        """
        dc = getattr(self, "_dc_mod", None)
        if dc is None:
            return None
        try:
            return dc._CONN.get("host")
        except Exception:                                    # noqa: BLE001
            return None

    def _record_call(self, cmd: str, ctl, *, host, t0: float | None,
                     cached: bool = False) -> dict:
        """记下这次调用的来源/耗时/时刻（同时刷新 `self._last_call`）。

        ⚠️ `clock_note` **只取有限几个稳定取值**：
          · `"local-clock-fallback"` —— 想探针但没拿到服务器时刻；
          · `"local-clock-disabled-by-user"` —— 用户传了 `clock_probe=False`。
        因为黄金样本一旦重新录制，两边都有这个键时**改值仍会判红** ——
        若把"探针冷却中/超时/无结果"这类**过程性**细节编进 `clock_note`，
        同一份数据在不同样本里就可能出现不同阶段，门禁会抖。
        细节另存 `clock_detail`（只进 `Client.last_call`，不进 meta）。
        """
        info = self._now()
        rec = {
            "source": (host, cmd, ctl),
            "elapsed": (round(max(0.0, time.monotonic() - t0), 4)
                        if t0 is not None else None),
            "fetched_at": info.at.isoformat(sep=" ", timespec="seconds"),
            "fetch_clock": info.clock,
            "cached": bool(cached),
        }
        if info.clock != "server" and info.clock not in timeliness.TRUSTED_CLOCKS:
            rec["clock_note"] = self._local_clock_note()   # 稳定取值，不随过程变
        if info.note:
            rec["clock_detail"] = info.note                 # 细节：仅排查用
        if info.market_closed is False:
            # 「时间说收盘了、行情说没到」→ 这条必须让人看见（它是判定被压低的**原因**），
            # 所以单独记一笔，由 `_stamp_table` / `_finish_series` 并进 `status_note`。
            rec["status_conflict"] = info.note or "时钟与行情自相矛盾，按保守处理"
        self._last_call = rec
        return rec

    @property
    def last_call(self) -> dict:
        """最近一次取数的 `{source, elapsed, fetched_at, fetch_clock, cached}`。

        这是**排查用**的完整记录；容器/表的 `meta` 里只放 `META_KEYS` 那 8 个键
        （见 `_meta_from`）。
        """
        return dict(getattr(self, "_last_call", {}) or {})

    @staticmethod
    def _meta_from(rec: dict) -> dict:
        """把 `_record_call` 的结果摊成 meta 的公共部分（`as_of` 由调用方补）。

        ⚠️ **只放 `META_KEYS` 里的键**：黄金样本回放只放行这些"新增键"，
        多一个键就会被判成"不在允许新增清单内"。
        `fetch_clock` 是一等字段（`"server"` / `"local"`），不是自由文本 ——
        本库红线是"绝不许用本机时钟冒充服务器时刻而不标注"，标注就落在它上面。
        """
        info = rec.get("source")
        host, cmd, ctl = (info if isinstance(info, tuple) and len(info) == 3
                          else (None, None, None))
        meta = {
            "fetched_at": rec.get("fetched_at"),
            # 子键集**固定为 META_SOURCE_KEYS**（5 个，一个不多一个不少）；
            # `host` 取不到就是 None，但**键必须在**（省键会让下游 KeyError）。
            "source": {
                "host": host,                       # 取不到就是 None（不编主机名）
                "cmd": cmd,
                "ctl": ctl,
                "clock": rec.get("fetch_clock"),      # "server" | "local"
                "cached": rec.get("cached"),
            },
            "elapsed": rec.get("elapsed"),
            "fetch_clock": rec.get("fetch_clock"),
        }
        if rec.get("clock_note"):
            meta["clock_note"] = rec["clock_note"]
        return meta

    def _stamp_table(self, table: Table, cmd: str, ctl, *, host, t0,
                     cached: bool = False, status=None, as_of=None,
                     status_note: str | None = None,
                     as_of_note: str | None = None) -> Table:
        """给 `Table.meta` **补**语义层键（`Table.meta` 原有的键一个都不删）。"""
        rec = self._record_call(cmd, ctl, host=host, t0=t0, cached=cached)
        meta = table.meta if isinstance(table.meta, dict) else {}
        meta.update(self._meta_from(rec))
        meta["as_of"] = as_of
        meta["status"] = status.value if isinstance(status, Status) else status
        if rec.get("status_conflict"):
            status_note = ("%s；%s" % (status_note, rec["status_conflict"])
                           if status_note else rec["status_conflict"])
        if status_note:
            meta["status_note"] = status_note
        if as_of_note:
            meta["as_of_note"] = as_of_note
        table.meta = meta
        return table

    def _finish_series(self, container, rec: dict, *, as_of, status,
                       as_of_note: str | None = None):
        """给 `Series` 容器补 meta。

        容器 meta 与 `Table.meta` 的**契约宽严不同**：
          · `Table.meta` 里只能出现 `META_KEYS`（那 8 个）—— 黄金样本会把
            `Table.meta` 逐字段比，多一个键就报"不在允许新增清单内"；
          · 容器（`Bars`/`Quotes`/`Ticks`/`Minutes`）的 meta 在黄金样本里走的是
            **信息性**口径（`golden_runner` 主比对前会把顶层 `meta` 整段剔除），
            所以这里可以多带**来源凭据**：`fetch_clock` / `clock_note` / `as_of_note`。

        ⚠️ `fetch_clock` 是**一等字段**（不是自由文本）：`"server"` = 服务端时刻，
        `"local"` = 本机兜底。`clock_note` 只在降级时出现，说明为什么降级。
        数据信号（`status` / `provisional` / `as_of` / `data_date`）一律进
        **结构化字段**，绝不只写在 `*_note` 里。
        """
        meta = dict(self._meta_from(rec))
        meta["as_of"] = as_of
        meta["status"] = status.value if isinstance(status, Status) else status
        meta["fetch_clock"] = rec.get("fetch_clock")
        if rec.get("clock_note"):
            meta["clock_note"] = rec["clock_note"]
        if rec.get("status_conflict"):
            meta["status_note"] = rec["status_conflict"]
        if as_of_note:
            meta["as_of_note"] = as_of_note
        container.meta = meta
        return container
    @staticmethod
    def _set_meta(container, **kw):
        """只往 meta 里写**白名单**键（`META_KEYS`），其余一律拒绝。

        这是本次改造的**防呆**：黄金样本契约只放行 8 个键，
        "顺手多写一个 `status_note`" 就会让 23 份样本全部报差异。
        需要说明性文字请写进 `source` 的值里，或走 `Client.last_call`。
        """
        meta = getattr(container, "meta", None)
        if not isinstance(meta, dict):
            meta = {}
        for key, val in kw.items():
            if key not in META_KEYS:
                raise KeyError(
                    "%r 不在容器 meta 的允许键里（%s）：扩键必须同步 "
                    "tests/golden_runner.ALLOWED_ADDED_KEYS 与 "
                    "tests/test_golden_contract.ALLOWED_ADDED_KEYS"
                    % (key, ", ".join(sorted(META_KEYS))))
            meta[key] = val.value if isinstance(val, Status) else val
        container.meta = meta
        return container

    @staticmethod
    def _make_table(cmd: str, cols, rows, primary) -> Table:
        """把协议层回包组成 `Table`，并**保证列名与取值严格对齐**。

        服务端回多个 `ResultSet` 时，`spec/06 §2.5.7` 的"列数"口径是**并集**，
        而并集通常比主表宽（`ph_agf10_cw_zcfzb` 是 82 vs 81）。
        拿并集去 `zip` 行值会把整行错一位 —— 静默错列，比抛错更坏。
        所以：`Table.cols` 用**主表自己的列名**（`primary`），
        并集原样留在 `meta["cols_union"]`，信息一点不丢。

        另一个坑：主表列名**可能重名**（同一 ResultSet 里两个 `N001`）。
        直接 `dict(zip(...))` 会把它们合成一列、**静默丢值**。
        这里给重名的第 2、3… 次出现加 `_2`/`_3` 后缀，原样清单记进
        `meta["cols_dedup"]` —— 宁可变个名字，也不丢数据。
        """
        names = list(primary or cols or [])
        meta = {}
        if list(cols or []) and list(cols) != names:
            meta["cols_union"] = list(cols)
            meta["cols_source"] = "primary"      # 并集比主表宽时，列名以主表为准

        if len(set(names)) != len(names):
            seen: dict[str, int] = {}
            fixed = []
            for n in names:
                seen[n] = seen.get(n, 0) + 1
                fixed.append(n if seen[n] == 1 else "%s_%d" % (n, seen[n]))
            meta["cols_dedup"] = names
            names = fixed

        return Table(cols=names, rows=rows, cmd=cmd, meta=meta)

    def call(self, cmd: str, ctl: int, params: Any = None,
             *, retry: int = 3, idle: float = 4.0,
             allow_empty: bool = False) -> Table:
        """直接调一条文档里的命令（`cmd` + `ctl` + 参数按命令约定填写）。

        参数 `params` 给 list / dict / str 均可；包装形态由库按命令前缀自动决定
        （`HQServ` 裸发，其余包 `{"Params":…}`）。

        返回 `Table`；失败抛 `ParamError` / `ServerError` / `NotSupportedError`
        / `EmptyResult` / `RateLimited` / `ConnectionFailed`。

        限流策略见 `_engine/pacer.py`：全局匀速 → 被踢则间隔翻倍 → 换服务器冷却。

        `allow_empty=True` 时，"有列无行"（当期无数据）返回空 `Table` 而不抛
        `EmptyResult` —— 批量遍历 421 个产品时用得上。

        `cache`（构造时注入）命中时**直接返回、不占限流闸门**；未命中才走 `wait()`。

        语义层（阶段 1）
        ----------------
        返回的 `Table.meta` 会**补**上 `as_of` / `status` / `fetched_at` / `source` /
        `elapsed`（原有键一个不删）。⚠️ 这条通道（数据中心）**回包不带数据日期**，
        所以 `as_of` 是 `None`：要知道数据日请用 `Client.get(...).data_date`
        （从首行 `N001` 推）或 `get()` 补的 `table.meta["data_date"]`。
        没有登记过时效口径的命令一律标 `PROVISIONAL`（`judge_unregistered`），
        **绝不默认 FINAL**。
        """
        t0 = time.monotonic()
        args = self._session_mod.body_of(cmd, params)

        # ① 缓存命中：闸门都不用进（缓存不是为了绕过限流，是为了根本不发请求）
        ck = None
        if self._cache is not None:
            from ._engine.cache import cache_key

            ck = cache_key(cmd, params, None)
            hit = self._cache.get(ck)
            if hit is not None:
                self._stamp_table(hit, cmd, ctl, host=self._session_host(),
                                  t0=t0, cached=True,
                                  status=timeliness.judge_unregistered(
                                      server_now=self._now()),
                                  as_of=hit.meta.get("as_of"),
                                  status_note="命中缓存：数据本体是上次取的那份，"
                                              "`elapsed` 接近 0、`source.cached` 为 true",
                                  as_of_note=_NO_AS_OF_DC)
                return hit

        limited_before = self._pacer.limited_events
        last_err: Exception | None = None
        empty_hits = 0

        for attempt in range(max(1, retry + 1)):
            self._pace()                       # 全局匀速闸门（含 sleep）
            try:
                raw = self._ensure().ask(ctl, cmd, args, idle=idle)
            except (OSError, ConnectionError) as e:        # 断连
                last_err = e
                # 第 1 次：同机重连（多为空闲超时/半死连接，换机反而丢好节点）
                # 第 2 次起：才认定为限流，退避 + 换机
                if attempt == 0:
                    try:
                        self._live_session().reconnect()
                    except Exception:                      # noqa: BLE001
                        self._drop_session()
                else:
                    self._note_limited()                   # 间隔翻倍
                    try:
                        self._live_session().switch_host()
                    except Exception:                      # noqa: BLE001
                        self._drop_session()
                continue

            verdict, nrow, ncol, cols, rows, primary = self._session_mod.parse(raw)
            if verdict == "PASS":
                self._pacer.note_ok()
                table = self._make_table(cmd, cols, rows, primary)
                if ck is not None:
                    self._cache.set(ck, table)
                self._stamp_table(table, cmd, ctl, host=self._session_host(), t0=t0,
                                  status=timeliness.judge_unregistered(
                                      server_now=self._now()),
                                  as_of=None,
                                  status_note=timeliness.UNREGISTERED_NOTE,
                                  as_of_note=_NO_AS_OF_DC)
                return table

            if verdict == "EMPTY_RS":                      # 有列无行 = 当期无数据
                self._pacer.note_ok()                      # 通道是通的，不算限流
                if allow_empty:
                    table = Table(cols=list(primary or cols), rows=[], cmd=cmd,
                                  meta={"empty": "EMPTY_RS"})
                    if ck is not None:
                        self._cache.set(ck, table)
                    self._stamp_table(table, cmd, ctl, host=self._session_host(),
                                      t0=t0,
                                      status=timeliness.judge_unregistered(
                                          server_now=self._now()),
                                      as_of=None,
                                      status_note="有列无行：当期无数据（不是失败）；"
                                                  + timeliness.UNREGISTERED_NOTE,
                                      as_of_note=_NO_AS_OF_DC)
                    return table
                raise errors.EmptyResult("%s 无数据（有列无行）" % cmd)

            if verdict == "EMPTY":
                # 空包可能是「限流静默丢包」，也可能是「命令确实不存在」。
                # 第一次不急着定性：退避重试；连续多轮都空才判 NotSupported。
                empty_hits += 1
                last_err = errors.NotSupportedError("%s 无响应" % cmd)
                if attempt < retry:
                    self._note_limited()                   # 当作疑似限流处理
                    try:
                        self._live_session().switch_host()
                    except Exception:                      # noqa: BLE001
                        self._drop_session()
                    continue
                if empty_hits >= 2 and self._pacer.limited_events > limited_before:
                    raise errors.RateLimited(
                        "%s 连续空包，疑似被限流（当前间隔 %.1fs）"
                        % (cmd, self._pacer.gap))
                raise errors.NotSupportedError("%s 无响应（通道不通）" % cmd)

            if verdict == "RAW":
                raise errors.ProtocolError(
                    "%s 回包不是 JSON（可能是 protobuf 通道，改用 pb 接口）" % cmd)

            if verdict.startswith("ERR_"):
                code = verdict[4:]
                if code == "-1002":
                    raise errors.ParamError("%s 参数错误(-1002)" % cmd)
                if code == "-1000":
                    raise errors.NotSupportedError("%s 命令不存在(-1000)" % cmd)
                raise errors.ServerError("%s 执行失败(%s)" % (cmd, code))

            raise errors.TdxError("未知结果 %r" % verdict)

        # 重试用尽：区分「被限流」和「纯粹连不上」
        if self._pacer.limited_events > limited_before:
            raise errors.RateLimited(
                "重试 %d 次仍被限流（当前间隔 %.1fs，已累计限流 %d 次）"
                % (retry, self._pacer.gap, self._pacer.limited_events))
        raise errors.ConnectionFailed("重试 %d 次仍失败：%s" % (retry, last_err))

    def get(self, name: str, allow_empty: bool = False, **kwargs) -> Dataset:
        """按产品名取数（421 个产品之一）。

        ```python
        c.get("lhb", date="2026-10-23")
        c.get("holiday")
        ```
        参数按产品声明里的占位符替换（`@code@` / `#date@` 等）。

        语义层（阶段 1）
        ----------------
        返回的 `Dataset.table.meta` 除 `call()` 那几项外，还会补上**按产品登记的时效口径**：
        `status`（`enums.Status`）、`data_date`（从首行 `N001` 推，推不出就是 `None`）。
        已登记：`lrmx`/`north` 是 T+1（`delayed`）、`ztqd` 盘中是 `pending`。
        没登记的产品标 `provisional` —— **不默认 FINAL**（unknown ≠ 0），
        且 `status_note` 会直说"台账里没有时效声明、无法判定"，不会让人误以为
        库"判定过"它是盘中值。
        """
        spec = self._products.get(name)
        if spec is None:
            raise KeyError("没有这个产品：%s（用 products() 查看全部）" % name)
        params = _fill(spec["params"], kwargs, name)
        table = self.call(spec["cmd"], spec["ctl"], params, allow_empty=allow_empty)
        table.domain = spec.get("domain", "")
        # 整列为 None 的列**保留**、只记账：数据中心产品（尤其 ztqd 涨停清单的
        # 00102 口径）整列空往往是"当日样本还不完整"，那是**数据信号**；
        # 在这里删列就等于把"数据不全"藏起来。判死列请拿多日样本，别只看今天。
        from .sections.boards import _flag_all_null_cols
        _flag_all_null_cols(table, product=name)
        ds = Dataset(name=name, table=table, params=params)
        self._describe_product(ds)
        return ds

    def _describe_product(self, ds: Dataset) -> Dataset:
        """给产品的 `Table.meta` 补上时效/数据日（**只加不删**，自身不抛错）。"""
        info = self._now()
        data_date = ds.data_date                    # 先按 N001 推，再落进 meta
        prev = self._prev_trade_date()
        try:
            status = timeliness.judge_product(
                ds.name, data_date, server_now=info, prev_trade_date=prev)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException as exc:                        # noqa: BLE001
            status = timeliness.judge_unregistered(server_now=info)
            info = info._replace(note="%s; 时效判定自身抛 %s，降级为 provisional"
                                        % (info.note or "judge_product 失败",
                                           type(exc).__name__))
        notes = []
        if ds.name.strip().lower() not in ("lrmx", "north", "ztqd"):
            notes.append(timeliness.UNREGISTERED_NOTE)
        # 已登记产品的**证据型**说明（能复核"为什么这么判"，不是只给个枚举值）
        evidence = timeliness.PRODUCT_EVIDENCE.get(ds.name.strip().lower())
        if evidence and status is Status.DELAYED:
            notes.append(evidence)
        if prev is None:
            notes.append("无法确定上一交易日（日线探针没取到），时效判定已降级")
        if info.note:
            notes.append(info.note)
        extra = {}
        if notes:
            extra["status_note"] = "；".join(notes)
        missing = {}
        if "fetch_clock" not in ds.table.meta:
            # 这张表没走过 `call()`（单测里直接造的表、或 `allow_empty` 之外的路径）：
            # 把时钟来源补上，否则下游分不出 `fetched_at` 可不可信。
            missing["fetch_clock"] = info.clock
            if info.clock != "server":
                missing["clock_note"] = "local-clock-fallback"
        self._set_meta(ds.table,
                       status=status,
                       data_date=data_date.isoformat() if data_date else None,
                       as_of=data_date.isoformat() if data_date else None,
                       **extra, **missing)
        return ds

    # ── 行情 ──────────────────────────────────────────────────────────────
    def get_bars(self, symbol, period="day", count: int = 120,
                 adjust: Adjust | str = Adjust.NONE) -> list[Bar]:
        """K 线。`period` 接受 `"day"/"week"/"60m"/"month"…` 或 `KlinePeriod`。

        参数
        ----
        count : int
            要多少根。**超过单页上限 416 会自动翻页**（服务端 `offset` 翻页，对外不暴露该参数）。
        adjust : Adjust | str
            **真复权，服务端算好给**（`PBFXT` 的 `field8`，2026-10-25 实测）：

            | 传什么 | field8 | 效果（600519 日线实测） |
            |---|---|---|
            | `Adjust.NONE` / `"NONE"`（默认） | 1 | 原始价，最新一根 1335.00 |
            | `Adjust.QFQ` / `"QFQ"` | 3 | 前复权：最新一根仍 1335.00，历史价被调整 |
            | `Adjust.HFQ` / `"HFQ"` | 2 | 后复权：最新一根 9311.23（×6.97） |

            每根 `Bar.adjust` 会标明本根用的口径。
            ⚠️ 只接受 `Adjust` 枚举或这三个字符串名 —— **裸 `int` 会被拒绝**，
            因为 `Adjust.QFQ == 1` 而协议 `1` 表示"不复权"，猜错就是把复权价当原始价用。

        单位
        ----
        价格元、`amount` 元、分钟线的 `time` 为 `HH:MM`。

        ⚠️ **`vol` 的单位随周期/品种变**（2026-10-26 手机通达信实测）：

        | 周期 / 品种 | `vol` 单位 |
        |---|---|
        | 日线 · 股票/ETF | 股 / 份 |
        | 日线 · 可转债 | 手 |
        | 周线 / 月线 | **手** |
        | 指数 | **手** |
        | 1/5/15/30/60 分钟 | 股 |

        ⚠️ **以上是服务端的"原始"差异，不是本方法返回的单位。**
        2026-10-27 起 `Bar.vol` **对外恒为「手」**：`stock` 的日线/分钟线 ÷100，
        周/月线本来就已是手；`可转债 / 回购 / 指数 / 板块` 日线本来就是手，
        而它们的**周/月线**在服务端是"日线值 ÷ 100"，所以 ×100 换回手。
        原始值完整保留在 `Bar.raw["Volume"]`（**那里仍是服务端原单位**）。
        ⚠️ 指数/板块**分钟线**的原始 `vol` 是 `amount/100` 派生值（不是真实成交量），
        这里不做换算，详见 `_vol_to_hand` 的实测表。

        语义层（阶段 1）
        ----------------
        返回 `Bars`（`list` 子类，`isinstance(x, list)` 仍为真），`meta` 里带
        `as_of`（末根的日期/时间）、`status`（`final`/`provisional`）、
        `provisional`（末根是否盘中未定稿）、`fetched_at`、`source`、`elapsed`。
        每根 `Bar.provisional` 都**逐根**判（历史根恒 `False`）。
        判定口径见 `tdxdata/timeliness.py`；`status=final` 只表示**日线口径定稿**
        （不含盘后固定价格交易，见该模块头）。

        ⚠️ **非价格序列会被拒绝（阶段 2 的破坏性变更，故意如此）**
        --------------------------------------------------------
        `880005`（涨跌家数）/ `880006`（停板家数）/ `880866`（近期新低）/
        `880641`（历史新低）这 4 个代码走的是同一条 `PBFXT`，返回形状也是同样的
        `open/high/low/close` —— 但**它们是家数或指标点位，不是价格**。
        以前本方法照价格返回，于是"880005 收盘 1317.00"这种读数会被当成点数用
        （实际是上涨家数 1317 家），足以毁掉任何基于价格的结论。

        现在命中这 4 个代码会抛 `errors.NotSupportedError` 并给出指路文案
        （价格 → 本方法；广度 → `get_breadth()`）。**零网络调用**：拦在发请求之前。

        ⚠️ **不是按 `880`/`881` 前缀拒绝**：`880903`/`880952`/`881007` 等是**真价格**
        的板块指数，照常可用。判据只有一处：`series_kinds.NON_PRICE_SERIES`
        里登记过且实证过的代码（未登记的一律不猜）。
        """
        market, code = self._check_symbol(symbol)
        spec = series_kinds.get_spec(code)
        if spec is not None:
            # 必须在发请求**之前**拦（零网络 —— 见 tests/test_series_kinds.py 的计数断言）
            raise errors.NotSupportedError(series_kinds.render_redirect(spec))
        return self._fetch_bars(symbol, period=period, count=count, adjust=adjust)

    def _fetch_bars(self, symbol, *, period="day", count: int = 120,
                    adjust: Adjust | str = Adjust.NONE) -> Bars:
        """`get_bars` 的**取数本体**（不含"非价格拒绝"那道闸）。

        单独拆出来的唯一原因是 `get_breadth()` 要复用同一条取数路径
        （同一套翻页、复权、`vol` 归一化、逐根 `provisional`），但它取的**就是**
        被 `get_bars` 拒绝的那批代码。拆函数比加一个 `allow_non_price=True`
        私有开关更干净：公开签名上一个可疑参数都不多。
        """
        from ._engine import hq_pb

        t0 = time.monotonic()
        per = self._period_str(period)
        # 这里用**纯函数** `symbols.parse`，不再 `_check_symbol`：
        # 两个调用方（`get_bars` / `get_breadth`）都已经在入口处做过策略性提醒/拒绝，
        # 本体再提醒一次就会让**一次调用收到两条同义警告**（实测：`get_bars("000300")`
        # 曾 warn 两遍）。校验责任在入口，本体只负责解析。
        market, code = symbols.parse(symbol)
        adj_code, adj_name = _adjust_code(adjust)

        want = int(count)
        if want < 1:
            raise ValueError("count 必须 ≥ 1（收到 %r）" % (count,))
        page = hq_pb.MAX_ROWS

        if want <= page:
            cols, rows = hq_pb.get_bars(code, period=per, count=want,
                                        adjust=adj_code, market=market)
        else:
            # 多页：offset 往回翻更早的数据，每页按时间升序 → 前插保持整体升序
            rows, off = [], 0
            while len(rows) < want:
                _c, part = hq_pb.get_bars(code, period=per, count=page,
                                          offset=off, adjust=adj_code,
                                          market=market)
                if not part:
                    break
                rows = part + rows
                off += len(part)
                if len(part) < page:
                    break
            rows = rows[-want:]

        if not rows:
            raise errors.EmptyResult("%s %s K线无数据" % (symbol, per))

        info = self._now()
        out = Bars()
        for r in rows:
            bar = Bar(
                market=market, code=code,
                date=str(r.get("Data", "")),
                open=_f(r.get("Open")), high=_f(r.get("High")),
                low=_f(r.get("Low")), close=_f(r.get("Close")),
                # 出口统一成"手"（2026-10-27 口径变更，实测表见 _vol_to_hand）
                vol=_vol_to_hand(_f(r.get("Volume")), code, per),
                amount=_f(r.get("Amount")),          # 元，各品类各周期一致（实测）
                period=per,
                time=_hhmm(r.get("Second")) if per in _INTRADAY else "",
                raw=dict(r),
                adjust=adj_name,
            )
            # 逐根判（不是只判末根）：历史根恒 FINAL，当根按 CLOSE_CUTOFF/周期结束时刻
            bar.provisional = (timeliness.judge_bar(bar, server_now=info)
                               is Status.PROVISIONAL)
            out.append(bar)

        last = out[-1]
        status = timeliness.judge_bar(last, server_now=info)
        rec = self._record_call("HQServ.PBFXT", 0x0E, host=self._pb_host(), t0=t0)
        out.meta = dict(self._meta_from(rec))
        self._finish_series(out, rec, as_of=_bar_as_of(last), status=status,
                            as_of_note="as_of 取末根 bar 的日期/时间（服务端数据自带），"
                                       "不是取数时刻；取数时刻见 fetched_at")
        out.meta["provisional"] = status is Status.PROVISIONAL
        return out

    def get_breadth(self, code, count: int = 120,
                    adjust: Adjust | str = Adjust.NONE) -> Table:
        """**广度 / 统计序列**（涨跌家数、停板家数、新低点位…），返回命名字段的 `Table`。

        ```python
        t = c.get_breadth("880005", count=5)
        t.cols
        # ['date', 'series', 'series_name', 'down_count', 'total', 'flat_count',
        #  'up_count', 'provisional']
        t.rows[-1]["up_count"]      # 上涨家数（不是「收盘价」）
        ```

        为什么要有它：`880005` 这类代码的 `open/high/low/close` **不是价格**
        （是四个家数），继续用 `.close` 读会得到"收盘 1317.00"这种荒谬结论。
        本方法把列名改成**语义正确的名字**，并且**不再暴露 `.close` 语义**。

        只接受登记表里的代码
        --------------------
        登记表 = `tdxdata/series_kinds.NON_PRICE_SERIES`（**只登记已实证的 4 个**：
        `880005` 涨跌家数 / `880006` 停板家数 / `880866` 近期新低 / `880641` 历史新低）。
        未登记的代码 → `errors.NotSupportedError`，消息里列出这 4 个并指路原始通道。
        **绝不用"位置猜含义"**：OHLC 在不同代码上可能是价格、家数或点位，
        猜错就是把一个数当另一个量用，比报错糟糕得多。

        返回结构（**形状已冻结，改动要同步 fixture-keeper 的样本**）
        ----------------------------------------------------------
        `Table`，`cols = ["date", "series", "series_name"] + [该 spec 的字段（固定顺序）]
        + ["provisional"]` —— `provisional` 放**最后一列**（不是插在数值列前）：
        它是**数据信号**（当根未定稿），放进 `cols` 才能 `df[df.provisional]` 直接用；
        放最后是为了"以后再加数值列也不会把它挤到中间"。

        行里另带一个**非列**辅助键 `raw`（服务端原始字段一个不丢，单位仍是服务端原单位）。

        `Table.meta` 含 `series` / `series_name` / `field_map` / `note` / `unavailable`
        （该序列**没有定义**的 `Bar` 字段）/ `period`，以及阶段 1 的
        `as_of` / `status` / `fetched_at` / `source` / `elapsed` / `fetch_clock`。
        行数不另给键：`len(t.rows)` 直接可得，多一个 `count` 只是多一处将来漂移的地方。

        ⚠️ **只支持日线，且没有 `period` 参数**（这是有意的显式拒绝）：
        这 4 个序列的"一天四个家数"就是日频语义，**分钟级的家数序列未实证**
        （`PBFXT` 的分钟周期对 880005 返回什么没有任何实测证据），所以不开放 ——
        而不是"接受一个 `period` 参数然后偷偷忽略它"。
        ⚠️ 无数据 → `raise errors.EmptyResult`（**绝不返回空表冒充"没有数据"**）。
        """
        # 先过标的校验：位数不对/期货形态要在"未登记"之前说清楚 ——
        # `get_breadth("00700")` 的真实原因是"5 位会补零命中别的标的"，
        # 而不是"这个广度序列没登记"（后者会让人以为只是缺个注册项）。
        _mkt, code = self._check_symbol(code)
        spec = series_kinds.get_spec(code)
        if spec is None:
            raise errors.NotSupportedError(_breadth_unknown_msg(code))

        bars = self._fetch_bars(code, period="day", count=count, adjust=adjust)
        if not bars:
            raise errors.EmptyResult("%s 广度序列无数据" % spec.code)

        cols = (["date", "series", "series_name"]
                + list(spec.fields.values())        # 固定顺序 = 登记表插入顺序
                + ["provisional"])                  # 末列：数据信号，不是元信息
        rows = []
        for bar in bars:
            row = {"date": bar.date, "series": spec.code, "series_name": spec.name}
            for bar_field, std in spec.fields.items():
                row[std] = getattr(bar, bar_field)
            row["provisional"] = bar.provisional    # 逐根：阶段 1 的判定口径
            row["raw"] = dict(bar.raw)
            rows.append(row)

        meta = dict(bars.meta)          # 阶段 1 的 as_of/status/fetched_at/source/elapsed…
        meta.update({
            "series": spec.code,
            "series_name": spec.name,
            "field_map": dict(spec.fields),        # {"close": "up_count", ...} 原样
            "note": spec.note,
            "unavailable": series_kinds.unavailable_fields(spec),
            "period": "day",            # 数据来自哪个周期的凭据（与 get_indicators 同词表）
        })
        return Table(cols=cols, rows=rows, cmd="HQServ.PBFXT", meta=meta)

    @staticmethod
    def _period_str(period) -> str:
        """周期归一化：枚举/字符串 → 协议字符串。"""
        if isinstance(period, KlinePeriod):
            name = period.name
            if name in _PERIOD_DEAD:
                raise errors.NotSupportedError(
                    "周期 %s 在手机版 7615 无数据（实测返回 0 行）" % name)
            return _PERIOD_STR[name]
        s = str(period).strip().lower()
        if s in _PERIOD_DEAD.values():
            raise errors.NotSupportedError(
                "周期 %r 在手机版 7615 无数据（实测返回 0 行）" % s)
        if s not in _PERIOD_STR.values():
            raise errors.NotSupportedError(
                "周期 %r 不支持；可用：%s"
                % (s, " / ".join(sorted(set(_PERIOD_STR.values())))))
        return s

    def get_minutes(self, symbol) -> list[MinutePoint]:
        """当日分时（240 点）。7615 固定取当日，不接受日期参数。

        语义层（阶段 1）：返回 `Minutes`（`list` 子类），`meta` 带
        `as_of`（服务器日期 + 末点 `HH:MM`）、`status`、`fetched_at`、`source`、`elapsed`。
        ⚠️ 分时通道**回包不带日期**（只有 `HH:MM`），`as_of` 的日期部分来自服务器时刻
        —— `meta["source"]["clock"]` 会写 `"server"` 还是 `"local"`，`"local"` 表示
        服务器时刻没取到（那时 `source["note"]` 里有原因）。
        """
        from ._engine import hq_pb

        t0 = time.monotonic()
        market, code = self._check_symbol(symbol)
        cols, rows = hq_pb.get_minutes(code, market=market)
        if not rows:
            raise errors.EmptyResult("%s 当日无分时数据" % symbol)

        info = self._now()
        out = Minutes()
        for r in rows:
            m = int(_f(r.get("Minute"), 0))
            out.append(MinutePoint(
                code=code, market=market,
                time="%02d:%02d" % (m // 60, m % 60),
                price=_f(r.get("Now")), avg_price=_f(r.get("Average")),
                vol=_f(r.get("NowVol")), raw=r,
            ))

        status = timeliness.judge_intraday(info.at, cutoff=timeliness.MIN_BAR_CUTOFF)
        rec = self._record_call("HQServ.PBZST", 0x0E, host=self._pb_host(), t0=t0)
        self._finish_series(out, rec,
                            as_of="%s %s" % (info.at.date().isoformat(), out[-1].time),
                            status=status,
                            as_of_note="分时通道只有 HH:MM，日期部分取自服务器时刻"
                                       "（分时固定取当日）")
        return out

    def get_quotes(self, symbols_, batch: int = 20, on_missing: str = "raise",
                   **kwargs) -> list[Quote]:
        """**按代码批量报价**（`PBCombHQ`），返回 `list[Quote]`（按你给的顺序）。

        这是 2026-10-27 才打通的能力 —— 之前这里抛 `NotSupportedError`：
        `PBMultiHQ` 的参数里**没有代码位**（`field2` 是列名），确实取不到指定代码的报价；
        补上这一点的是另一条命令 `PBCombHQ`，它的 `field4` 就是**代码列表**。

        覆盖：沪市 / 深市 / 创业板 / 科创板 / **北交所** / 指数 / `880xxx` 板块。
        ```python
        qs = c.get_quotes(["600519", "000001", "880903", "999999"])
        qs[0].name, qs[0].price, qs[0].pre_close     # ('贵州茅台', 1349.98, 1335.00)
        ```

        要点（都是实测）：
        * **`pre_close` 取 `CLOSE` 列** —— 本通道的 `CLOSE` 是**昨收**，不是当日收盘
          （`880903 CLOSE=551.40` 与 `get_boards` 的 CLOSE 一字不差）。
        * **没有五档**：返回列里没有 bid/ask，所以 `bid`/`ask` 恒为空
          （这是协议限制，不是没实现）。
        * **`vol` / `amount` 现在能拿到了**（2026-10-27 起）：`field2` 是我们传的列名，
          本方法多要了 `VOL` / `AMOUNT` / `LB` 三列（见 `hq_extra.COMBHQ_COLS_FULL`），
          服务端认并回列。⚠️ 列名是 **`VOL`**（不是 `VOLUME`，后者服务端不认）。
          盘前 `0` 是服务端真值；**若响应里没有该列则是 `None`**（不再拿 0 冒充）。
        * **量纲（2026-10-27 09:25 实网对拍同一交易日）**：
          `vol` 单位是 **「手」**（`210` 手 = 当日 K 线 `21000` 股，比值 0.01），
          `amount` 单位是 **「元」**（与 `Bar.amount` 比值 1.0）。
          ✅ **2026-10-27 起 `Bar.vol` 也归一化成「手」**，所以 `Quote.vol` 与
          `Bar.vol` 现在**同单位、可直接比较**（原值见 `raw`）。
          对拍明细见 `docs/VERIFICATION.md`。
        * 无效/不支持的代码服务端**静默不返回**（如老新三板 `430047`）；
          本方法会自动**对缺的码单独补一次**，仍无就抛 `EmptyResult` 并点名，
          绝不返回"少了几个"的表冒充完整结果。
        * `batch`：每次请求放几个代码（抓包只验证到 6 个，上限未知，默认 20 保守）。
        * 每只的原始列一个不丢（多要三列后是 25 列左右），全在 `Quote.raw` 里（按服务端列名）。

        语义层（阶段 1）
        ----------------
        返回 `Quotes`（`list` 子类），`meta` 带 `as_of`（**首只的 `server_time`**，
        这是全库唯一**回包自带服务端时刻**的通道）、`status`、`fetched_at`、
        `source`、`elapsed`。盘中（< 15:35）一律 `provisional`。

        `on_missing`（阶段 3 新增）
        --------------------------
        服务端对无效代码**静默不返回**，所以"少了几只"是常态而非异常。三种策略：

        | 取值 | 行为 |
        |---|---|
        | `"raise"`（**默认，行为与旧版一字不差**） | 缺码 → `EmptyResult` 点名 |
        | `"skip"` | 丢缺失、**不抛**；`meta["missing"]` 精确列出，`meta["requested"]`/`meta["returned"]` 给基数 |
        | `"report"` | 同 `skip`，但额外 `warnings.warn` 一次（"我允许缺，但我要看见"） |

        ```python
        qs = c.get_quotes(["600519", "881999"], on_missing="skip")
        len(qs)                    # 1
        qs.meta["missing"]         # [{'symbol': 'sh881999', 'market': 1, 'code': '881999',
                                   #   'reason': '服务端未返回'}]   ← 键集固定，就这四个
        ```
        ⚠️ `"skip"` **不等于"静默"**：缺失清单一定在 `meta` 里，绝不是悄悄少几行。
        非法取值 → `ParamError`（在**发请求之前**炸，不白等一轮网络）。
        """
        from ._core import check_on_missing, resolve_missing
        from ._engine import hq_extra as _x

        mode = check_on_missing(on_missing)      # 非法值先炸，别白跑一轮网络
        for one in symbols.iter_symbols(symbols_):
            self._check_symbol(one)              # 位数/期货/两义：进网络之前就拦
        pairs = _x._pairs(symbols_)

        t0 = time.monotonic()
        if mode == "raise":
            # 默认路径的调用形态与旧版**一字不差**（老的单测/桩只认 batch，
            # 多塞一个 kwarg 会把它们全打红 —— 而它们的桩是对的，不该改）
            cols, rows = _x.get_quotes(symbols_, batch=batch)
        else:
            cols, rows = _x.get_quotes(symbols_, batch=batch, on_missing=mode)
        out = Quotes()
        got: set[str] = set()
        for r in rows:
            out.append(_quote_from_row(dict(zip(cols, r))))
            got.add(r[0])

        info = self._now()
        stamp = getattr(out[0], "server_time", "") if out else ""
        data_at = _parse_stamp(stamp)
        status = timeliness.judge_quote(info.at, data_at)
        rec = self._record_call("HQServ:PBCombHQ", 0x11, host=self._pb_host(), t0=t0)
        self._finish_series(out, rec, as_of=stamp or None, status=status,
                            as_of_note=None if stamp else
                            "回包里没有 HqDate/HqTime，as_of 无法确定")
        if mode != "raise":
            # 只在非默认模式下动 meta：`raise` 路径的 meta 必须与旧版逐字节相同
            # （黄金样本钉着它），不允许因为"顺手多写两个键"就抖一下。
            missing = resolve_missing(pairs, got, on_missing=mode,
                                      warn_msg=None if out else
                                      "get_quotes 全部 %d 个代码服务端都未返回" % len(pairs))
            out.meta["requested"] = len(pairs)
            out.meta["returned"] = len(out)
            out.meta["missing"] = missing
        return out

    def get_name(self, symbol, market: int | None = None, **kwargs) -> str:
        """**代码 → 名称**（`PBHQInfo`）。`symbol` 可给 `'880903'` 或 `('sh','600519')`。

        ```python
        c.get_name("880903")          # '水产品'
        c.get_name("600519")          # '贵州茅台'
        c.get_name("899050")          # '北证50'
        ```

        库原来没法把 `880903` 还原成名字（只能反查板块清单）；这条命令补掉了。
        `market` 不给就按 `symbols.market_by_code()` 推断；`000680` 这类两义代码可显式指定
        （市场 1 = 科创综指、市场 0 = 山推股份）。

        ⚠️ 名称**原样返回**，不做 strip/规范化（`万 科Ａ` 中间有空格、`Ａ` 是全角）。
        要批量取名称请用 `get_quotes`（一次几十个），本方法一次只能一只。
        """
        from ._engine import hq_extra as _x

        if market is None:
            self._check_symbol(symbol)
        else:
            symbols.parse(symbol)      # 位数/期货照旧校验；显式 market 不算两义
        return _x.name_of(symbol, market)

    def get_block_data(self, block: str, spec: str = "RN,0,2",
                       field2: int = 999) -> Table:
        """**块数据**（`PBXmlBlock`）：给块名 + 取数规格，服务端回一批 JSON 行。

        `spec` = **`"类别,级别,起始,数量"`**（后两位是本机实测定下来的）：
        `"HY,1,0,10"` = 行业块、1 级、从第 0 行起、要 10 行。

        实网验证过的块名（**块名是服务端页面配置下发的，不是通用常量**）：

        | 块名 | 典型 spec | 内容 |
        |---|---|---|
        | `MStock_ZLJX_ADDE_R` | `HY,1,0,N` | 行业块（`1`/`881218`/`汽车零部件`/价格…） |
        | `MStock_JCXH_ALL` | `RN,0,0,N` | 个股 + 选股策略标签 |

        ⚠️ **单页上限 100 行**（要 150/227 都只给 100）；要取全用 `get_block_rows()`。
        ⚠️ 只有前 3 列确认含义：`market` / `code` / `name`；第 4 列起是价格量额，
        **含义未定**（疑似 现价/昨收/涨跌额/成交额），所以列名只给 `c3`…，原值一个不丢。
        """
        from ._engine import hq_extra as _x

        t0 = time.monotonic()
        blk, blk_status, rows = _x.block_data(block, spec, field2)
        n = max((len(r) for r in rows), default=0)
        cols = ["market", "code", "name"] + ["c%d" % i for i in range(3, n)]
        # ⚠️ 服务端回包里的 `status` 是**块状态**（协议字段），语义层的 `status`
        # 是**时效四态** —— 两个同名不同义的东西绝不能共用一个键，所以改叫 `block_status`。
        table = Table(cols=cols, rows=rows, cmd="HQServ.PBXmlBlock",
                      meta={"block": blk, "block_status": blk_status, "spec": spec,
                            "field2": field2,
                            "note": "c3 起的列含义未定：服务端未给列名，这里只按位置命名"})
        return self._stamp_table(
            table, "HQServ.PBXmlBlock", 0x0E, host=self._pb_host(), t0=t0,
            status=self._snapshot_status(), as_of=None,
            as_of_note="PBXmlBlock 回包没有时间列，as_of 无法确定（快照口径，按取数时刻看 fetched_at）")

    def get_block_rows(self, block: str, kind: str = "HY", level: int = 1,
                       max_rows: int = 200, field2: int = 0) -> Table:
        """块数据**按页取全**（`PBXmlBlock` 分页）→ `Table`。

        `spec` 的第 3/4 位就是「起始 / 数量」，所以翻页是服务端原生支持的，
        本方法只是把每页 100 行串起来。`max_rows` 是硬上限（防块名给错时打太多请求）。
        """
        from ._engine import hq_extra as _x

        t0 = time.monotonic()
        rows = _x.block_rows(block, kind=kind, level=level,
                             max_rows=max_rows, field2=field2)
        if not rows:
            raise errors.EmptyResult(
                "块 %s 一页都没取到（kind=%s level=%d field2=%s）" % (block, kind, level, field2))
        n = max((len(r) for r in rows), default=0)
        cols = ["market", "code", "name"] + ["c%d" % i for i in range(3, n)]
        table = Table(cols=cols, rows=rows, cmd="HQServ.PBXmlBlock",
                      meta={"block": block, "kind": kind, "level": level, "field2": field2,
                            "note": "c3 起的列含义未定：服务端未给列名，这里只按位置命名"})
        return self._stamp_table(
            table, "HQServ.PBXmlBlock", 0x0E, host=self._pb_host(), t0=t0,
            status=self._snapshot_status(), as_of=None,
            as_of_note="PBXmlBlock 回包没有时间列，as_of 无法确定"
                       "（多页拼接，各页取数时刻不同；按取数时刻看 fetched_at）")

    def get_samples(self, symbols_, batch: int = 15) -> Table:
        """**批量抽样行情**（`PBSampleMore`）：一次几十个代码的紧凑快照。

        只在"要极省流量 / 大批量"时用它 —— 同样传一批代码，
        `get_quotes`（`PBCombHQ`）回的是**列名 + 字符串值**，好解析得多。

        能锚定的只有 `pre_close`（= `field6`，与 `get_boards` 的 CLOSE 一字不差）；
        剩下的 float32 **列序至今没锚定、个数还会变**（`count` 字段声明 60，
但实测返回 9 / 11 / 13 个，随盘中推进增长 —— 2026-10-27），所以列名就是 `f0`..`fN`，
        **不是** high/low/volume —— 别自己脑补（04 文档 §3.1 给了锚定方法）。
        """
        from ._engine import hq_extra as _x

        for one in symbols.iter_symbols(symbols_):
            self._check_symbol(one)          # 位数/期货/两义：进网络之前就拦
        t0 = time.monotonic()
        items = _x.samples(symbols_, batch=batch)
        n = max((len(d["floats"]) for d in items), default=0)
        cols = ["code", "pre_close"] + ["f%d" % i for i in range(n)]
        rows = []
        for d in items:
            # ⚠️ 拿不到就是 `None`，**不许** `or 0.0`：0.0 是"昨收 0 元"这个**假事实**，
            # 会让下游算涨跌幅时正好除零/得出 ±100%。红线：unknown ≠ 0。
            row = {"code": d["code"], "pre_close": d["pre_close"]}
            for i, v in enumerate(d["floats"]):
                row["f%d" % i] = v
            rows.append(row)
        table = Table(cols=cols, rows=rows, cmd="HQServ.PBSampleMore",
                      meta={"note": "f0.. 的列序未锚定，别当 high/low 用：服务端未给列名"})
        return self._stamp_table(
            table, "HQServ.PBSampleMore", 0x0E, host=self._pb_host(), t0=t0,
            status=self._snapshot_status(), as_of=None,
            as_of_note="PBSampleMore 回包没有时间列，as_of 无法确定（快照口径）")

    def get_board_stat(self, f3: int = -1, f5: int = 14, f2: int = 6) -> Table:
        """**板块成分统计**（`PBHYStat`）：七列 `Setcode/Code/Name/Now/Other/Close/XSFlag`。

        `f5` 选**视图**、`f3` 选**板块类别**（两种视图下都生效）—— 2026-10-27 实网扫描定出
        （`f3` 从 -1 扫到 8 的对照表见 `docs/VERIFICATION.md`）：

        - `f5=0` → 12 行**纯板块列表**，`f3` 决定整张表的类别；
        - `f5=14`（默认）→ 板块行 + 一只成分行**交替**；`f3` **仍生效，但只影响尾部**：
          前 8 行（4 组）是各 `f3` 共有的头部榜单，**第 9 行起按类别分叉**
          （实测 `f3=-1` → 连续亏损 / 微小盘股；`f3=3` → 新材料 / 低空经济；
          同一 `f3` 连发两次结果一致，差异确实来自 `f3`）；
        - `f3` 类别：`-1/0/6/8`=行业三级、`1`=行业(按涨幅)、`3`=概念、
          `4`=特色/资金榜单(昨日连板等)、`5`=地区、`7`=一级行业、`2`=空包。

        ⚠️ `Other` 列**不定名**：量纲随 `f5` 变（`f5=0` 是 ±0.1 量级；`f5=14` 板块行是大整数、
        成分行恒 0.000）。按 `Other` 原样取，别当市值/成交额。

        请求里**没有板块代码位**，所以本方法只能按"类别"取列表、按默认视图取"板块+首只成分"；
        要按板块代码取成分请用 `get_board_members(board)`（走 `zttz_xggp`）。
        """
        from ._engine import hq_extra as _x

        t0 = time.monotonic()
        cols, rows = _x.board_stat(f2=f2, f3=f3, f5=f5)
        if not rows:
            raise errors.EmptyResult(
                "PBHYStat 没返回数据（f3=%s f5=%s）" % (f3, f5))
        table = Table(cols=cols, rows=rows, cmd="HQServ.PBHYStat",
                      meta={"f3": f3, "f5": f5,
                            "note": "f5=视图(0=纯板块列表/14=板块+成分交替)；f3=类别"
                                    "(f5=14 时只影响第 9 行起的尾部)；Other 量纲未定，"
                                    "f3 取值含义见 docs/VERIFICATION.md"})
        return self._stamp_table(
            table, "HQServ.PBHYStat", 0x0E, host=self._pb_host(), t0=t0,
            status=self._snapshot_status(), as_of=None,
            as_of_note="PBHYStat 回包没有时间列，as_of 无法确定（快照口径）")

    def get_ticks(self, symbol, count: int = 200, max_pages: int = 50) -> list[Tick]:
        """逐笔成交（`HQServ:PBTickEx`，2026-10-27 打通）。返回 `list[Tick]`，**按时间升序**（旧→新）。

        **翻页是自动的**：首页锚点 `"0"`（最新一页），之后每页锚点 = 上一页响应里的
        **「区间起点」**（`field1.field4` 的 `"1995,2095"` 取 `1995`）。
        ⚠️ 锚点**不是**行里的 `Second`（拿 `Second` 当锚点会回一整页 0 —— 实测踩过）。

        停止条件（任一满足）：拿到 `count` 条 / 某页 0 行 / 区间起点为 0 / 翻满 `max_pages` 页。
        按**协议行的序号**（`区间起点 + 行内下标`）去重，所以多页衔接**不会重复也不会丢**。

        **单位**：`price` 元；`vol` **手**（与 `Quote.vol`/`Bar.vol` 同单位）。
        **买卖方向：本库不提供**（`Tick.bs_flag` 恒为 `None`，**声明式不可用**）
        ------------------------------------------------------------------
        机器可读的清单在 `models.UNPROVIDED_TICK_FIELDS`（测试会对真实样本复核它
        确实恒 None；服务端哪天开始给这个字段，那条测试会红）。
        阶段 4 实证（2026-10-27，600519，1200 笔真实逐笔）：
          * `trade_count` **已删除**：`PBTickEx` 一行就是**一笔**成交，协议里根本
            没有"笔数"字段。这不是"暂不可用"，是概念不存在；旧代码填 0 是编数据。
          * `bs_flag`：服务端**没有**可直接判定买卖方向的字段，恒 `None`。
            ⚠️ 旧 docstring 曾写「`bs_flag` 恒等于 2 = 未知」，那是**错的**
              （2 会被下游读成"卖"），现在是 `None`。
          * `raw["InOutFlag"]` 有 4 个取值，实测分布与含义（**相关 ≠ 实证，故不映射**）：
            - `0`：608/1200 笔，相对上一笔涨 65.9% / 跌 13.2% → **像主动买**；
            - `1`：358/1200 笔，相对上一笔跌 76.8% / 涨 10.3% → **像主动卖**；
              0/1 方向是"高度相关但仍有 13%~34% 不一致"，**不足以当判据**，
              所以库不把它映射成 `bs_flag`（映射错 = 把整批数据的方向搞反）；
            - `5`：20/20 笔**全在 15:05–15:30、且全部成交在收盘价**（量 1–11 手）
              → 这是**盘后固定价格交易**（这一条是**实证**，按时间与价格双重合判定）；
            - `2`：全天零星 16 笔，其中 15:00:01 那笔 1382 手、成交价=收盘价
              → 至少收盘那笔是**收盘集合竞价**；其余同 flag 的盘中笔含义未定。
          * `HKTTFlag` 实测是 `InOutFlag` 的**单字节回声**（300/300 逐笔相等，
            如 `InOutFlag=1` → `HKTTFlag="\x01"`）；`HKInOutFlag` 恒 `"0"`（无信息量）。
          * **未定名字段**（`VolInStockDiff`/`InOutFlag`/`HKTTFlag`/`HKInOutFlag`）
            一律只放在 `tick.raw` 里，原样可查、本库不解释。

        参数：
            symbol: `"600519"` / `"sh600519"` / `(1, "600519")` 等，交给 `symbols.parse`
            count:  要多少笔（默认 200；每页 100，自动翻页）
            max_pages: 最多翻多少页（防死循环，默认 50）

        语义层（阶段 1）：返回 `Ticks`（`list` 子类），`meta` 带 `as_of`
        （服务器日期 + 末笔 `HH:MM:SS`）、`status`、`fetched_at`、`source`、`elapsed`。
        逐笔用 `CLOSE_CUTOFF` 判（盘后固定价格成交到 15:29 还在进来，别提前宣布定稿）。
        """
        from ._engine import hq_extra

        t0 = time.monotonic()
        market, code = self._check_symbol(symbol)
        want = int(count)
        if want <= 0:
            raise ValueError("count 必须 > 0（收到 %r）" % (count,))
        got = _collect_ticks(lambda a: hq_extra.ticks(code, market, anchor=a, count=100),
                             market, code, want, max_pages)
        if not got:
            raise errors.EmptyResult("%s 当日无逐笔数据" % symbol)

        info = self._now()
        out = Ticks(got)
        status = timeliness.judge_intraday(info.at, cutoff=timeliness.CLOSE_CUTOFF)
        rec = self._record_call("HQServ:PBTickEx", 0x11, host=self._pb_host(), t0=t0)
        last_time = getattr(out[-1], "time", None) or ""
        self._finish_series(
            out, rec,
            as_of=(("%s %s" % (info.at.date().isoformat(), last_time)).strip()
                   if last_time else None),
            status=status,
            as_of_note="逐笔通道只有 HH:MM:SS（当日秒数），日期部分取自服务器时刻")
        return out

    def get_market_page(self, market: int = 6, offset: int = 0,
                        page_size: int = 50, cols: list[str] | None = None) -> list[Quote]:
        """市场行情**一页**（`PBMultiHQ`），返回 `list[Quote]`。

        2026-10-25 解构结论：`PBMultiHQ` = 「按涨幅排序、可按市场码过滤、可翻页的
        市场行情列表」。**不是按代码取**，也**没有五档**。

        参数
        ----
        market : int
            `field4` 市场/品种码（**不是 setcode**）：
            `0` 科创 / `1` 沪B / `2` 创业 / `3` 深B / `4`,`5` 债券 / **`6` 全部A股** / `7` B股。
        offset : int
            `field6` 偏移，**单位=行**，窗口 `[offset, offset+page_size)`。
            实测 `offset=0` 与 `offset=50` 的代码零重叠 → 翻页有效。
        page_size : int
            `field7`，返回**至多** `page_size` 行；`>80` 封顶 80。
            ⚠️ **部分「(列集合, page_size)」组合服务端会稳定回 0 行**，规律未定论：
            跟列数有关、**也跟具体列名有关**，**不是「列数 × ps」的单调函数**
            （同为 4 列、同为 ps=18，含 `BELONGHY` 有数据、含 `VAR0404` 回空；
            且重复请求结果一致 → 确定性行为，不是偶发空包）。
            所以这里**不设硬下限**：0 行会把 `page_size` 翻倍重试（封顶 80、最多 5 次），
            **实际返回行数可能多于你给的 `page_size`** —— 以 `len(返回值)` 为准。
        cols : list[str] | None
            `field2` 要返回哪些**列名**；`Code`/`Setcode` 由服务端自动补。
            **默认 5 列**：`NOW/CLOSE/NAME/AMOUNT/VOL`（2026-10-27 从 3 列改过来，
            所以默认调用 `Quote.amount`/`Quote.vol` 就有真值）。
            显式传 `cols=` 会**完全覆盖**默认值 —— 传 `hq_pb.MULTIHQ_COLS_CAPTURE`
            可回到 2026-10-26 抓包原样的 3 列形态。

        ```python
        page = c.get_market_page(market=6, page_size=50)
        print(len(page), page[0].code, page[0].name, page[0].price)
        print(page[0].vol, page[0].amount)   # 默认就**有真值**（手 / 元）
        ```

        ⚠️ `VOL`/`AMOUNT` 两列**会进 `Quote`**（2026-10-27 修掉的 bug）：
        以前这两列即使回了、值也已经在 `raw` 里，旧代码也没往 `Quote` 传 ——
        于是 `Quote.amount` 对外恒 `0.0`，真值只能自己去翻 `raw`（"数据在手里却报假值"）。
        现在：**列在就如实填（含真值 0），列不在就是 `None`**（不是 `0.0`）。
        `VOL` 单位与 `get_quotes` 一致，都是**手**（实测 2026-10-27：688252
        `vol=82823` 手 ×100×21.95 ≈ 1.82 亿，与同行 `AMOUNT=176,519,296` 同口径，偏差 3%）。

        每个请求都过进程级闸门 `pacer`（`PB*` 走 `datacenter._send`，不经过 `Session`，
        不主动开口就等于绕过限流）。翻倍重试时**每次尝试都单独过闸门**。

        语义层（阶段 1）：返回 `Quotes`（`list` 子类），`meta` 带 `status` /
        `fetched_at` / `source` / `elapsed` / `fetch_clock`。⚠️ `PBMultiHQ` 的列里
        **没有时间字段**，所以 `as_of` 是 `None`（不拿 `fetched_at` 冒充数据时刻），
        原因写在 `as_of_note`；`status` 本身已经是保守值，删掉 `as_of_note`
        也不影响"能不能拿这批数下结论"。
        """
        from ._engine import hq_pb

        t0 = time.monotonic()
        cols_, rows = hq_pb.get_market_page(market=market, offset=offset,
                                            page_size=page_size, cols=cols)
        if not rows:
            raise errors.EmptyResult(
                "PBMultiHQ market=%s offset=%s 返回 0 行（起始页大小 %s，已翻倍重试到 %d 仍空）。"
                "常见原因：列数太少导致「列数×page_size」低于服务端下限，或该市场码无数据。"
                "可加大 cols 或 page_size 再试。"
                % (market, offset, page_size, hq_pb.MAX_PAGE_ROWS))
        out = Quotes()
        for r in rows:
            out.append(_quote_from_page_row(r, market))

        info = self._now()
        status = timeliness.judge_intraday(info.at, cutoff=timeliness.CLOSE_CUTOFF)
        rec = self._record_call("HQServ.PBMultiHQ", 0x12, host=self._pb_host(), t0=t0)
        self._finish_series(out, rec, as_of=None, status=status,
                            as_of_note="PBMultiHQ 回包无时间列（NOW/CLOSE/NAME/AMOUNT/VOL…），"
                                       "as_of 无法确定；要看取数时刻请用 fetched_at")
        return out

    def get_universe(self, market: int = 6, max_pages: int = 20,
                     page_size: int = 50) -> list[Quote]:
        """按市场**翻页枚举**行情列表（`PBMultiHQ` 多次调用），返回去重后的 `list[Quote]`。

        ```python
        uni = c.get_universe(market=6, max_pages=5)      # 最多 5 页
        print(len(uni), uni[0].code, uni[-1].code)
        ```

        退出条件（任一满足即停）：
          1. **后续页**取到 0 行（`get_market_page` 抛 `EmptyResult` → 当"到底了"）。
             ⚠️ **第一页就 0 行不算到底，直接抛错** —— 那说明参数/列数配错了，
             静默 break 会变成"少拿数据还不报错"（这正是本库的红线）。
          2. **本页没有带来任何新代码**（服务端没按 offset 挪窗口 —— 同时也是无进展保护）；
          3. 本页不足 `page_size` 行（已到末页）；
          4. 达到 `max_pages` 上限（**硬保护，绝不无限请求**）。

        ⚠️ 本方法**不接 `cols`**，每页都走 `get_market_page` 的默认列
        （2026-10-27 起是 5 列 `NOW/CLOSE/NAME/AMOUNT/VOL`），所以返回的 `Quote`
        **默认就带 `vol`/`amount`**（手 / 元）。想改变列集合请直接用 `get_market_page(cols=...)`。

        ⚠️ 只能保证**不重复**，不能保证**不遗漏**：
        `PBMultiHQ` 是按**涨幅排序**的列表，**盘中排序会漂移**，所以翻页接缝不可能严格对齐 ——
        某只票在一页之间从第 50 名跳到第 40 名，就会被跳过。
        这也意味着 `get_universe()` **不是完整证券代码表**，只是"当前可枚举到的行情列表"。
        ⚠️ 每页 1 次请求、每页各过一次闸门：**实测 3 页 6.60 秒**（2026-10-26，含 TCP 复用），
        即约 2.2 秒/页 → `max_pages=20` 约 **44 秒**。
        （D2 修复前是 8.36 秒/页，20 页要 167 秒 —— 那会儿写 44 秒是错的。）
        若某次因 0 行触发翻倍重试，会额外消耗 1~4 次。

        语义层（阶段 1）：返回 `Quotes`（`list` 子类），`meta` 带 `status` /
        `fetched_at` / `source` / `elapsed` / `pages`。
        ⚠️ 多页之间本来就有时间差，`as_of` 记 `None` 并附说明（见 `get_market_page`）。
        """
        t0 = time.monotonic()
        pairs, stats = _core.paginate(
            lambda off: self.get_market_page(market=market, offset=off,
                                             page_size=page_size),
            page_size=int(page_size), max_pages=int(max_pages),
            key=lambda q: (q.market, q.code))
        out: Quotes = Quotes(pairs)

        info = self._now()
        status = timeliness.judge_intraday(info.at, cutoff=timeliness.CLOSE_CUTOFF)
        rec = self._record_call("HQServ.PBMultiHQ", 0x12, host=self._pb_host(), t0=t0)
        self._finish_series(
            out, rec, as_of=None, status=status,
            as_of_note="按 offset 翻页拼接：各页取数时刻不同，as_of 无法用一个时刻表示")
        out.meta["pages"] = stats["pages"]
        # ── 封顶必须**显式**（阶段 3）────────────────────────────────────
        # 历史缺陷：默认 max_pages=20 × page_size=50 = 最多 1000 只，而 market=6
        # 全量约 5577 只 —— 于是 1000 只被当成"全量"交给了调用方。方向是"少给还
        # 不报错"，正是本库红线。现在封顶写进 meta，并给成本与出路。
        out.meta["truncated"] = bool(stats["truncated"])
        out.meta["rows"] = stats["rows"]
        out.meta["estimated_cost_seconds"] = _core.estimate_cost(
            stats["pages"], gap=getattr(getattr(self, "_pacer", None), "gap", 2.2))
        if stats["truncated"]:
            out.meta["note"] = (
                "⚠️ 已达 max_pages=%d 上限（本次 %d 页 / %d 条），**后面还有没取的**："
                "服务端**没有**封顶，封顶是本方法的参数。`market=6` 全量约 5577 只，"
                "`page_size=80` 需 `max_pages>=70`、实测约 158.6 秒。"
                "要全量请用 `iter_universe()`（可边取边停），或显式调大 max_pages。"
                % (int(max_pages), stats["pages"], stats["rows"]))
        else:
            out.meta["note"] = (
                "已翻到末页（本次 %d 页 / %d 条），没有再被截断。"
                "⚠️ 仍需注意：`PBMultiHQ` 是按涨幅排序的列表，盘中排序会漂移，"
                "翻页接缝可能漏票 —— 本方法**不保证不遗漏**。" % (stats["pages"], stats["rows"]))
        return out

    def iter_universe(self, market: int = 6, page_size: int = 80,
                      max_pages: int | None = None):
        """**逐页取全的生成器**：一枚一枚 `Quote` 交出去，边取边给。

        ```python
        for q in c.iter_universe(market=6, page_size=80):     # max_pages=None = 翻到底
            print(q.code, q.name)
        ```
        与 `get_universe` 的关系
        ------------------------
        同一个翻页内核（`_core.iter_pages`），差别只在"什么时候交给人"：

        | | `get_universe` | `iter_universe` |
        |---|---|---|
        | 返回 | 一次性 `Quotes` | 生成器（逐个 yield） |
        | 默认封顶 | `max_pages=20`（≈1000 只，**会 `truncated`**） | `None`（翻到底） |
        | 适用 | 要一小批、要 `meta` 快照 | 要全量、或"取到够就停" |

        **为什么生成器是对的默认**：`market=6` 全量 ≈ 5577 只 / 70 页 / ≈158 秒。
        先攒成一个 5577 元素的列表再返回，等于强迫调用方为"随时可以不要"的部分付满等待。

        ⚠️ 取到的每一条都在**取它的那一刻**才拿到 —— 所以这里**不提供**
        `Quotes.meta` 快照（生成器无法在"结束时"再回头改已 yield 出去的东西）。
        要 `status`/`as_of`/`truncated` 这类整体口径请用 `get_universe`，或每页自行
        `get_market_page`。两个通道的 `as_of` 都是 `None`（多页时刻不同）。
        """
        yield from _core.iter_pages(
            lambda off: self.get_market_page(market=market, offset=off,
                                             page_size=page_size),
            page_size=int(page_size), max_pages=max_pages,
            key=lambda q: (q.market, q.code))

    # ── 指标 / 涨跌停（本地计算，契约见 API设计.md §2.7）─────────────────
    def get_indicators(self, symbol, indicators=None, period: str = "day",
                       count: int = 120) -> Table:
        """由 `get_bars` 派生的技术指标，**行与 K 线一一对应**。

        ```python
        c.get_indicators("600519", count=60)                  # 默认全套
        c.get_indicators("600519", {"ma": {"n": [5, 20]}})    # 只算两条均线
        ```

        返回 `Table`：`cols = ["date","close"] + 指标列`，无值的格子是 `None`
        （不是 0 —— 0 和"周期不足"必须能区分）。口径见 `tdxdata/indicator.py`。
        """
        from . import indicator as _ind

        bars = self.get_bars(symbol, period=period, count=count)
        cols = ["date", "close"]
        series: list[list] = []
        for col, vals in _indicator_columns(_ind, bars, indicators):
            cols.append(col)
            series.append(vals)

        rows = []
        for i, b in enumerate(bars):
            row = {"date": b.date, "close": b.close}
            for j, col in enumerate(cols[2:]):
                row[col] = series[j][i]
            rows.append(row)
        # 派生表：语义层信息**整份继承自那批 K 线**（它就是由这批 K 线算出来的，
        # 独立性零 —— 末根是不定稿的，指标也一定不是定稿的）。`period`/`count` 是
        # 派生表自己的口径凭据，与 `get_breadth` 同一套词表。
        table = Table(cols=cols, rows=rows, cmd="local:indicator",
                      meta={"period": period, "count": len(bars)})
        table.inherit_meta(bars, **{"period": period, "count": len(bars)})
        return table

    def get_price_limits(self, symbol, prev_close: float | None = None,
                         is_st: bool = False) -> tuple[float, float]:
        """涨跌停价 `(limit_up, limit_down)`，按板块规则 + 交易所取整。

        `prev_close` 不给就取最近一根日线收盘（= 上一交易日收盘）。
        ST 判定需要证券名称，本库不做名称推断 —— **要 5% 就显式传 `is_st=True`**，
        否则一律按非 ST 板块幅度算（这是契约，不是猜测）。
        """
        from . import limits as _limits

        _mkt, code = self._check_symbol(symbol)
        if prev_close is None:
            bars = self.get_bars(symbol, period="day", count=10)
            prev_close = bars[-1].close
        return _limits.price_limits(prev_close, code, is_st=is_st)

    # ── 财务 / F10 / 分红（B 组，契约见 API设计.md §2.7）──────────────────
    def get_finance(self, symbol, kind: str = "zcfz") -> Table:
        """财务报表（资产负债表 / 利润表 / 现金流量表 / 主要指标…）。

        标准字段名见 `tdxdata/sections/mapping.py`，原始列在每行 `raw` 里。
        `kind` 不认识会抛 `ValueError` 并列出全部合法取值。
        """
        from .sections import finance as _finance

        self._check_symbol(symbol)          # 位数/期货/两义：进网络之前就拦
        return _finance.get_finance(self, symbol, kind=kind)

    def get_f10(self, symbol, section: str = "gsjj") -> Table:
        """F10 个股资料（公司简介 / 经营分析 / 股东 / 分红…）。"""
        from .sections import f10 as _f10

        self._check_symbol(symbol)          # 位数/期货/两义：进网络之前就拦
        return _f10.get_f10(self, symbol, section=section)

    def get_xdxr(self, symbol) -> Table:
        """分红融资（`ph_agf10_fhrz`）。手机版 7615 无独立除权除息命令。"""
        from .sections import xdxr as _xdxr

        self._check_symbol(symbol)          # 位数/期货/两义：进网络之前就拦
        return _xdxr.get_xdxr(self, symbol)

    # ── 板块 / 资金 / 市场（C 组）────────────────────────────────────────
    def get_boards(self, kind: str = "all") -> Table:
        """板块列表。`kind`：all / industry / concept / area / index。"""
        from .sections import boards as _boards

        return _boards.get_boards(self, kind=kind)

    def board_page(self, offset: int = 0, page_size: int = 44,
                   market: int = 10004) -> tuple[list, list]:
        """板块清单分页行情（`PBMultiHQ` 的板块域）。

        `market`：10008 一级行业 / 10001 二级 / 10002 三级 / 10004 概念（默认）
        / 10005 风格 / 10006 地区。返回 `(列名, 行)`，行里带
        `Code`/`Setcode`/`Name`/`CLOSE`/`NOW`/`AMOUNT`。
        手机端「板块」页就是按 `offset` 步进 44 这么翻的（2026-10-26 抓包实测）。

        语义层（阶段 1）：返回值仍是 **`(cols, rows)` 二元组**（签名不变），
        但 `rows` 换成 `Quotes`（`list` 子类），`meta` 里带 `status` / `fetched_at` /
        `source` / `elapsed`；`as_of` 记 `None`（这条通道无时间列）。
        """
        from ._engine import hq_pb

        t0 = time.monotonic()
        cols, rows = hq_pb.get_market_page(
            market=market, offset=offset, page_size=page_size,
            cols=["Code", "Setcode", "Name", "CLOSE", "NOW", "AMOUNT"])
        info = self._now()
        status = timeliness.judge_intraday(info.at, cutoff=timeliness.CLOSE_CUTOFF)
        rec = self._record_call("HQServ.PBMultiHQ", 0x12, host=self._pb_host(), t0=t0)
        box = Quotes(rows or [])
        self._finish_series(box, rec, as_of=None, status=status,
                            as_of_note="板块行情（PBMultiHQ）回包无时间列，as_of 无法确定")
        return cols, box

    def get_board_members(self, board) -> Table:
        """板块成分股。`board` 可给板块名或板块代码。"""
        from .sections import boards as _boards

        return _boards.get_board_members(self, board)

    def get_capital_flow(self, symbols=None) -> Table:
        """资金流向。`symbols=None` 取全市场排行，也可给单代码或代码列表。"""
        from .sections import flow as _flow

        return _flow.get_capital_flow(self, symbols)

    def get_market_stat(self, kind: str = "updown") -> Table:
        """市场概览。`kind`：updown（涨跌停家数）/ industry（行业涨跌）/ sentiment（情绪）。

        `kind="all"` **合法但会抛 `NotSupportedError`**：这三类的列结构互斥
        （按日 ×2 计数 / 按行业 ×2 计数 / 单行多指标），拼成一张表只能是造列名。
        """
        from .sections import flow as _flow

        return _flow.get_market_stat(self, kind=kind)

    # ── 能力边界（阶段 6）────────────────────────────────────────────────
    def capabilities(self, *, instrument: str | None = None,
                     available_only: bool = False) -> Table:
        """**能力边界表**：哪个品种的哪种能力拿得到、拿不到、为什么、怎么绕。

        它回答的是"这个库到底能干什么"，而不是"现在数据是多少"。所以：

        * **一个请求都不发**（静态注册表，见 `tdxdata/capabilities.py`）——
          也**不会**覆盖 `Client.last_call`，因为它不是一次取数；
        * `meta["as_of"]` 恒为 `None` 且**必带** `as_of_note` 说明"这是代码里的注册表，
          不来自行情，所以没有数据日期这个概念"（不是"取不到数据日期"）；
        * `meta["status"]` 是 `provisional`：它是一份**会被实证推翻**的声明，
          不享受 `final` 那种"定稿"待遇。

        参数
        ----
        instrument : str | None
            品种代码过滤，取值见 `tdxdata.capabilities.INSTRUMENTS`
            （`a_share` / `bj` / `index` / `board` / `etf` / `bond` / `hk` /
            `futures` / `option` / `cbt` / `forex` / `all`）。大小写/空白都会规整。
        available_only : bool
            只要 `available=True` 的项。

        返回
        ----
        `Table`，列固定为
        `["instrument", "capability", "interface", "available", "reason", "evidence", "workaround"]`
        （`tdxdata.capabilities.CAPABILITY_COLS`）。

        抛错
        ----
        筛完为空 → `EmptyResult`（**不是**空表）：空表会被读成"这个品种确实没有能力"，
        而真相是"你筛的那个品种名不存在"。

        ```python
        with tdxdata.Client() as c:
            for r in c.capabilities(instrument="hk"):
                print(r["capability"], r["available"], r["reason"][:40])
        ```
        """
        from . import capabilities as _capmod

        t0 = time.monotonic()
        picked = _capmod.select(instrument=instrument,
                                available_only=available_only)
        if not picked:
            raise errors.EmptyResult(
                "capabilities(instrument=%r, available_only=%r) 筛完是空的。"
                "品种代码只接受 %s；若你要看全部就别传 instrument。"
                % (instrument, available_only,
                   " / ".join(_capmod.INSTRUMENTS)))
        rows = [{col: getattr(cap, _CAP_FIELD[col]) for col in _capmod.CAPABILITY_COLS}
                for cap in picked]
        table = Table(cols=list(_capmod.CAPABILITY_COLS), rows=rows,
                      cmd="tdxdata.capabilities", domain="能力边界")
        # 手工拼 meta 而**不走 `_stamp_table`**：后者会调 `_record_call` → `_now()`，
        # 对一张静态表那就是白发一个请求（`_now()` 首次会做时钟探针）。
        # `fetch_clock="local"` 是**如实标注**（这份时间戳确实是本机时钟），不是冒充服务器时刻。
        meta = self._meta_from({
            "source": (None, "tdxdata.capabilities", None),
            "elapsed": round(max(0.0, time.monotonic() - t0), 4),
            "fetched_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
            "fetch_clock": "local",
            "cached": False,
        })
        meta["as_of"] = None
        meta["status"] = Status.PROVISIONAL.value
        meta["status_note"] = (
            "能力声明是**代码里的注册表**，随实证更新（例如「服务端有、本库未实现」"
            "可能变成「已实现」），所以按非定稿处理；想复核请顺着 evidence 列去看原始记录")
        meta["as_of_note"] = (
            "这是库内**静态注册表**，不来自任何行情通道，所以没有数据日期/时刻可言 —— "
            "as_of 恒为 None 是「没有这个概念」，不是「取不到」")
        table.meta = meta
        return table

    # ── 生命周期 ──────────────────────────────────────────────────────────
    def rate_stats(self) -> dict:
        """当前限流状态（排查用）：基准/当前间隔、累计被限流次数、冷却中的服务器数。

        阶段 5 起另加三个**并发**相关的键（`pacer.stats()` 的旧键一个没动）：

        | 键 | 含义 |
        |---|---|
        | `concurrency` | 用户**请求**的并发度（原样） |
        | `concurrency_effective` | **实际建了几条 lane**（= `len(lanes)`）。当前规则下它恒等于 `concurrency`（超上限是 `ValueError`，**不截断**），留着是为了让这份快照自解释：读的人不必先去翻构造规则，就看得出"到底真的并了几条" |
        | `lanes` | 每条 lane 钉的主机名列表（默认 `concurrency=1` 时是 `[]`） |

        `pacer.stats()` 里的 `hosts_tracked` 是"已经有独立槽位的主机数"：
        默认路径恒为 `0`，`concurrency>1` 后才可能 >0 —— 一眼看出有没有人
        真的在按主机分摊。
        """
        out = dict(self._pacer.stats())
        out["concurrency"] = getattr(self, "_concurrency", 1)
        out["concurrency_effective"] = getattr(self, "_concurrency_eff", 1)
        out["lanes"] = [lane.host for lane in getattr(self, "_lanes", None) or []]
        return out

    # ── 语义层：先问再取 ──────────────────────────────────────────────────
    def timeliness(self, kind: str, **kw) -> dict:
        """**先问再取**：这条通道现在是什么时效状态？（阶段 1 新增）

        为什么要有它：`get_bars()` 返回什么，取决于"现在几点"和"今天是不是交易日"。
        与其让调用方自己拼这些判断（拼错的代价已经实测过：盘中 13,130.88 亿 vs
        收盘 14,609.58 亿，+11.3% 直接反转"60 日最低"的结论），不如**先问一句**：

        ```python
        with tdxdata.Client() as c:
            t = c.timeliness("bar_day")
            print(t["status"], t["server_time"], t["close_cutoff"])
            bars = c.get_bars("999999", count=60)
            if bars.status != "final":
                ...    # 盘中：别拿这批数下结论
        ```

        参数
        ----
        kind : str
            至少支持：`"bar_day"` / `"bar_min"` / `"minutes"` / `"ticks"` /
            `"ztqd"` / `"lrmx"` / `"north"` / `"market_stat_industry"` / `"boards"`。
            不认识的 kind **不抛错**，返回 `provisional` + `note` 说明（问一句不该炸）。
        data_date : str | date | None
            （可选，走 `**kw`）该数据集的数据日；不给就按"当日"判。

        返回
        ----
        dict：`kind` / `data_date` / `status`（`enums.Status`）/ `server_time` /
        `close_cutoff`（`"15:35"`）/ `note`，另附 `fetch_clock`（`server`|`local`）
        与 `prev_trade_date`（拿不到就是 `None`）。

        ⚠️ `Status.FINAL` 只表示**这个口径定稿了**，不表示"今天全市场成交都在里面"：
        日线口径不含盘后固定价格交易（详见 `tdxdata/timeliness.py` 模块头）。
        ⚠️ 名字**刻意不以 `get_` 开头**：它不是取数入口，没有"空结果"这种形态。
        """
        key = str(kind or "").strip().lower()
        info = self._now()
        raw_date = kw.get("data_date")
        data_date = timeliness.parse_yyyymmdd(raw_date)
        notes: list[str] = []

        if info.clock != "server":
            notes.append("时刻来自本机兜底（%s）" % (info.note or "服务器时刻不可得"))
        if raw_date is not None and data_date is None:
            notes.append("data_date=%r 解析不出日期，已按 None 处理" % (raw_date,))

        prev = None
        if key in ("ztqd", "lrmx", "north", "market_stat_industry", "boards"):
            prev = self._prev_trade_date()
            if prev is None:
                notes.append("无法确定上一交易日，时效判定已降级（不会给出 final）")

        if key == "bar_day":
            status = timeliness.judge_intraday(info.at, cutoff=timeliness.CLOSE_CUTOFF)
            notes.append("口径同 judge_bar 的「当日日线」分支：%s 前算 provisional"
                         % FINAL_CUTOFF_HHMM)
        elif key == "bar_min":
            status = timeliness.judge_intraday(info.at, cutoff=timeliness.MIN_BAR_CUTOFF)
            notes.append("分钟当根的最终口径是「该根周期结束时刻」，见 timeliness.bar_period_end")
        elif key in ("minutes",):
            status = timeliness.judge_intraday(info.at, cutoff=timeliness.MIN_BAR_CUTOFF)
        elif key in ("ticks", "quotes", "boards"):
            status = timeliness.judge_intraday(info.at, cutoff=timeliness.CLOSE_CUTOFF)
        elif key in ("ztqd", "lrmx", "north"):
            status = timeliness.judge_product(key, data_date, server_now=info,
                                              prev_trade_date=prev)
            evidence = timeliness.PRODUCT_EVIDENCE.get(key)
            if evidence and status is Status.DELAYED:
                notes.append(evidence)
        elif key in ("market_stat_industry", "industry"):
            status = timeliness.judge_market_stat("industry", data_date,
                                                  server_now=info,
                                                  prev_trade_date=prev)
            key = "market_stat_industry"
        elif key in ("updown", "sentiment"):
            status = timeliness.judge_market_stat(key, data_date, server_now=info,
                                                  prev_trade_date=prev)
        else:
            status = timeliness.judge_unregistered(server_now=info)
            notes.append("未登记的 kind=%r：%s" % (kind, timeliness.UNREGISTERED_NOTE))

        return {
            "kind": key,
            "data_date": data_date.isoformat() if data_date else None,
            "status": status,
            "server_time": info.at.isoformat(sep=" ", timespec="seconds"),
            "close_cutoff": FINAL_CUTOFF_HHMM,
            "fetch_clock": info.clock,
            "prev_trade_date": prev.isoformat() if prev else None,
            "note": "；".join(notes) if notes else "",
        }

    def close(self) -> None:
        """关闭长连接（数据中心那条 + PB 路径的持久连接）。

        阶段 5：`concurrency>1` 时先把 lane 线程池 `shutdown(wait=True)` 收干净
        （**不留悬挂线程**、不吞掉正在跑的任务），再关每条 lane 的长连接。
        `concurrency=1`（默认）时 `_pool` / `_lanes` 都是空的，
        这里只是两次空循环 —— 默认路径的行为一点没变。
        """
        pool, self._pool = getattr(self, "_pool", None), None
        if pool is not None:
            pool.shutdown(wait=True)               # 等正在跑的任务跑完再退出
        for lane in getattr(self, "_lanes", None) or []:
            sess, lane.sess = lane.sess, None
            if sess is not None:
                try:
                    sess.close()
                except Exception:                              # noqa: BLE001
                    pass
        if self._sess is not None:
            self._sess.close()
            self._sess = None
        # PB 路径用的是模块级持久连接（进程内共享），这里一并释放
        try:
            self._dc_mod.close_pb()
        except Exception:                                      # noqa: BLE001
            pass

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False

    def __repr__(self) -> str:
        return "<tdxdata.Client host=%s 产品=%d>" % (self._host or "auto",
                                                    len(self._products))


# ── 工具 ──────────────────────────────────────────────────────────────────
# 日内周期（有 time）；日线及以上 Second=0，不该显示成 00:00
_INTRADAY = {"1m", "5m", "15m", "30m", "60m"}

# `get_indicators` 的默认指标规格：(指标名, 默认参数)
_IND_DEFAULTS = (
    ("ma", {"n": (5, 10, 20)}),
    ("ema", {"n": (12, 26)}),
    ("macd", {}),
    ("kdj", {}),
    ("rsi", {"n": (14,)}),
    ("boll", {}),
)
_IND_DEFAULT_PARAMS = {k: v for k, v in _IND_DEFAULTS}
_IND_NAMES = tuple(k for k, _ in _IND_DEFAULTS)


def _indicator_columns(ind, bars, spec):
    """把 `indicators` 规格展开成 `[(列名, 与 bars 等长的序列)]`。

    规格形态（API设计.md §2.7）：
      · `None`            → 全套默认
      · `["ma", "rsi"]`   → 按名字，用默认参数
      · `{"ma": {"n": [5, 20]}, "macd": {"fast": 8}}` → 自定义参数
    """
    if spec is None:
        items = [(k, dict(v)) for k, v in _IND_DEFAULTS]
    elif isinstance(spec, dict):
        items = [(str(k), dict(v or {})) for k, v in spec.items()]
    else:
        items = [(str(k), {}) for k in spec]

    out = []
    for raw_name, params in items:
        name = raw_name.strip().lower()
        if name not in _IND_DEFAULT_PARAMS:
            raise ValueError("不认识的指标 %r；可用：%s"
                             % (raw_name, " / ".join(_IND_NAMES)))
        dflt = _IND_DEFAULT_PARAMS[name]

        if name in ("ma", "ema"):
            ns = params.get("n", dflt["n"])
            if isinstance(ns, int):
                ns = (ns,)
            for n in ns:
                out.append(("%s%d" % (name, int(n)),
                            getattr(ind, name)(bars, int(n))))
        elif name == "rsi":
            ns = params.get("n", dflt["n"])
            if isinstance(ns, int):
                ns = (ns,)
            for n in ns:
                out.append(("rsi%d" % int(n), ind.rsi(bars, int(n))))
        elif name == "macd":
            dif, dea, hist = ind.macd(bars,
                                      fast=int(params.get("fast", 12)),
                                      slow=int(params.get("slow", 26)),
                                      signal=int(params.get("signal", 9)))
            out += [("dif", dif), ("dea", dea), ("macd", hist)]
        elif name == "kdj":
            k, d, j = ind.kdj(bars, n=int(params.get("n", 9)),
                              k=int(params.get("k", 3)), d=int(params.get("d", 3)))
            out += [("k", k), ("d", d), ("j", j)]
        elif name == "boll":
            up, mid, low = ind.boll(bars, n=int(params.get("n", 20)),
                                    k=int(params.get("k", 2)))
            out += [("boll_upper", up), ("boll_mid", mid), ("boll_lower", low)]
    return out


def _hhmm(seconds) -> str:
    """`Second` 字段（自 00:00 起的秒数）→ `HH:MM`；无效/零值返回空串。"""
    try:
        s = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    if not 0 < s < 86400:          # 0 视为「无时间」（日线就是 0）
        return ""
    return "%02d:%02d" % (s // 3600, s % 3600 // 60)


def _bar_as_of(bar) -> str | None:
    """一根 `Bar` 的「数据时刻」字符串（容器 `meta["as_of"]` 用它）。

    日线及以上 → `"YYYY-MM-DD"`；分钟线 → `"YYYY-MM-DD HH:MM"`（服务端本来就给
    周期**结束**时刻）。`date` 解析不出来 → `None`（**不拿今天顶替**）。
    """
    d = timeliness.parse_yyyymmdd(getattr(bar, "date", ""))
    if d is None:
        return None
    t = str(getattr(bar, "time", "") or "").strip()
    return "%s %s" % (d.isoformat(), t) if t else d.isoformat()


def _parse_stamp(stamp) -> datetime | None:
    """`Quote.server_time`（`"2026-10-27 15:30:02"`）→ `datetime`；解析不了 `None`。"""
    text = str(stamp or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _breadth_unknown_msg(code) -> str:
    """`get_breadth()` 收到**未登记**代码时的报错文案。

    要求（Lead 2026-10-27）：必须**列出已登记的 4 个代码**并指路原始通道。
    为什么不猜：同一批 `880xxx` 里既有价格序列也有家数/点位序列，返回形状完全一样，
    按位置猜含义 = 把一个量当另一个量用 —— 报错比这好。
    """
    known = "、".join("%s（%s）" % (s.code, s.name)
                      for s in (series_kinds.NON_PRICE_SERIES[c]
                                for c in sorted(series_kinds.NON_PRICE_SERIES)))
    return (
        "get_breadth 只接受**已实证**的广度/指标序列代码：%s。\n"
        "%r 没有登记 —— 本库**不猜**未登记代码的字段含义（同一条 PBFXT 返回的"
        "open/high/low/close 在不同代码上可能是价格、家数或指标点位，猜错就是把"
        "一个量当另一个量用）。\n"
        "要原始数据请走**未加工**的通道：`c.call(cmd, ctl, params)`（数据中心命令），"
        "或 `tdxdata._engine.hq_pb.get_bars(...)`（PB 行情命令）；"
        "若它确实是价格序列，直接用 `get_bars()`。" % (known, code))


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _quote_from_page_row(raw: dict, market: int = 6) -> Quote:
    """`PBMultiHQ`（`get_market_page` 一页）的一行 → `Quote`。**纯函数**，不联网。

    ⚠️ `VOL`/`AMOUNT` **会被传进 `Quote`**（2026-10-27 修复的 bug）：
    这两列只要在响应里就有真值，但旧代码构造 `Quote` 时**没传**这两个字段 ——
    于是真值躺在 `raw` 里、对外却报 `0.0`。属于"数据在手里却报假值"，比"没要这一列"更隐蔽。

    行里**没有**该列 → `None`（不是 `0.0`）。⚠️ 默认 `cols` 只有 `NOW/CLOSE/NAME` 三列，
    所以默认调用两个字段都是 `None`；要量额得显式把 `"AMOUNT"`/`"VOL"` 加进
    `get_market_page(cols=...)`（不改默认，免得动既有请求）。
    """
    raw = dict(raw)
    return Quote(
        market=int(_f(raw.get("Setcode"), market)),
        code=str(raw.get("Code", "")).strip(),
        price=_f(raw.get("NOW")),
        pre_close=_f(raw.get("CLOSE")),
        # 列在就照实转（含真值 0）；列不在 → None
        vol=_num_or_none(raw, "VOL"),
        amount=_num_or_none(raw, "AMOUNT"),
        name=str(raw.get("Name", "")).strip(),
        raw=raw,
    )


def _collect_ticks(fetch, market: int, code: str, want: int, max_pages: int = 50):
    """**翻页取逐笔的纯逻辑**（`fetch(anchor) -> parse_ticks() 的字典或 None`）。

    抽成纯函数是为了**离线可测**：翻页/去重/排序这几条最容易出错，不该只能靠实网验。

    * 首页锚点 `"0"`；之后 `anchor = 上一页 anchor[0]`（**区间起点**，不是行里的 `Second`）
    * 去重键 = **行序号**（`区间起点 + 行内下标`）—— 多页衔接既不重复也不丢
    * 停止：拿到 `want` 条 / 某页 0 行 / 区间起点为 0 或 `起点==终点` / 翻满 `max_pages`
    * 返回**时间升序**（序号升序）的最近 `want` 条
    """
    seen: set[int] = set()
    picked: list[tuple[int, Tick]] = []
    anchor = "0"
    for _page in range(max(1, int(max_pages))):
        got = fetch(anchor)
        if not got or not got.get("rows"):
            break
        start = (got.get("anchor") or (None, None))[0]
        for i, row in enumerate(got["rows"]):
            seq = (start + i) if isinstance(start, int) else i
            if seq in seen:
                continue
            seen.add(seq)
            picked.append((seq, _tick_from_row(row, market, code)))
        if len(picked) >= want:
            break
        nxt = got.get("anchor")
        if not nxt or nxt[0] == 0 or nxt[0] == nxt[1]:
            break
        anchor = str(nxt[0])
    picked.sort(key=lambda pair: pair[0])
    return [pair[1] for pair in picked[-want:]]


def _tick_from_row(raw: dict, market: int, code: str) -> Tick:
    """把 `PBTickEx` 的一行（**全是字符串**）转成 `Tick`。

    `Second` 是**当日秒数**（`40978` → `11:22:58`），不是 `HHMMSS`、也不是序号。
    `Now`/`NowVol` 是字符串小数/整数，容错转 `float`，转不动就记 `None`
    （**不填 0.0**：0.0 会被当成真实成交价/量）。
    """
    sec = _num_or_none(raw, "Second")
    if sec is None:
        hhmmss = None
    else:
        s = int(sec)
        hhmmss = "%02d:%02d:%02d" % (s // 3600, (s // 60) % 60, s % 60)
    return Tick(
        market=market,
        code=code,
        time=hhmmss,
        price=_num_or_none(raw, "Now"),
        vol=_num_or_none(raw, "NowVol"),
        raw=dict(raw),
    )


def _quote_from_row(raw: dict) -> Quote:
    """`PBCombHQ` 的一行列值（`{列名: 原值}`）→ `Quote`（**纯函数**，不联网，便于离线用例）。

    `vol`/`amount` 用 `_num_or_none()`：列在就照实转（盘前的 `0` 是服务端真值），
    列不在 → `None`（**绝不**用 0 冒充"没有这一列"）。
    """
    return Quote(
        market=_i(raw.get("Setcode")),
        code=raw.get("Code", ""),
        price=_f(raw.get("NOW")),
        pre_close=_f(raw.get("CLOSE")),
        open=_f(raw.get("OPEN")),
        high=_f(raw.get("MAX")),
        low=_f(raw.get("MIN")),
        vol=_num_or_none(raw, "VOL"),
        amount=_num_or_none(raw, "AMOUNT"),
        name=raw.get("Name", ""),
        server_time=_stamp(raw.get("HqDate", ""), raw.get("HqTime", "")),
        raw=raw,
    )


def _num_or_none(raw: dict, key: str) -> float | None:
    """列**存在**就转 float（服务端真值 0 也照填）；**列不存在**返回 None。

    为什么要和 `_f()` 分开：`PBCombHQ` 的列是我们按名字要的，少要一列时
    `raw.get("VOL")` 是 None，`_f()` 会把它变成 0.0 —— 于是"没这列"和"这列真是 0（盘前）"
    混成同一个 0，下游分不开就是假数据。所以这里必须先判 `key in raw`。
    """
    if key not in raw:
        return None
    try:
        return float(raw[key])
    except (TypeError, ValueError):
        return None


def _vol_kind(code: str) -> str:
    """按**代码段白名单**判品种类别，只用于 `vol` 单位换算（不做任何启发式猜）。

    为什么需要它：服务端 `vol` 的**原始单位随品种和周期变**，实测表见
    `_vol_to_hand()` 的注释。判定**只按前缀**，因为"`amount/vol` 像不像收盘价"
    这类启发式会被行情抖动误判（尤其指数/板块的点位与成交额没有价格关系）。

    实测样本（2026-10-27，日线，`amount/vol` 恒等式）：
      · `stock`：600519 比 0.998 / 000001 0.995 / 300750 1.002 / 688981 1.002 /
        920091 1.004 / 510300 1.000 / 159915 1.001 → 全部 ≈1 ⇒ 原始单位是**股/份**
      · `bond`（可转债 110/111/113/118/123/127…）：113054 比 **9.99**、110077 比 9.95
        ⇒ `amount/vol = 收盘价 × 10` ⇒ 原始单位是**手**（1 手 = 10 张 = 1000 元面值）
      · `repo`（逆回购 204/131）：204001、131810 比 888.9，但 `amount/vol` **恰好 1000.000**
        ⇒ 原始单位是**手**（1 手 = 1000 元面值）；回购的 `close` 是**利率**，不是价格
      · `index`（999/399/899 指数 + 880/881 板块）：999999 比 0.454、399001 0.133、
        899050 2.024、880903 0.904 —— 指数点位与成交额没有价格关系，恒等式**不适用**；
        这里靠 2026-10-26 与东财的逐值交叉核对定案（上证 552,842,880 vs 东财 552,842,912 **手**）
        ⇒ 原始单位是**手**
      · 其它一律按 `stock`（ETF 实测与股票同口径，不需要单独分支）
    """
    c3 = code[:3]
    if c3 in ("880", "881"):                    # 通达信板块指数（与指数同口径）
        return "index"
    if c3 in ("999", "399", "899"):             # 上证/深证/北证指数
        return "index"
    if c3 in ("110", "111", "113", "118", "123", "127", "128"):
        return "bond"                           # 沪/深可转债
    if c3 in ("204", "131"):
        return "repo"                           # 沪/深逆回购
    return "stock"


def _vol_to_hand(vol, code: str, per: str):
    """把 `Bar.vol` 归一化成**手**（对外唯一单位，2026-10-27 起）。

    实测表（2026-10-27；"周线=日线/100" 用**周线 vol 与日线求和对比**验证：
    vol 比值恒为 **0.010**、`amount` 比值恒为 **1.000**，五个品类全部如此）：

    ================  ==========  ============  ==================
    品类              日线        周线/月线     分钟线（60m/5m/…）
    ================  ==========  ============  ==================
    `stock`（含 ETF） **股**      手(=日线/100) **股**
    `bond` 可转债     **手**      日线/100      **手**
    `repo` 逆回购     **手**      未实测        ？→ 按日线同类处理
    `index` 指数/板块 **手**      日线/100      **`amount/100` 派生值 → 不可用，给 None**
    ================  ==========  ============  ==================

    于是换算系数（保证出口恒为手）：

    | 品类 | 日线 | 分钟线 | 周线 / 月线 |
    |---|---|---|---|
    | `stock` | **÷100** | **÷100** | 不变（已经是手） |
    | `bond`/`repo` | 不变（已经是手） | 不变（已经是手） | **×100**（服务端给的是"日线/100"） |
    | `index` | 不变（已经是手，日线是真值） | **`None`**（派生值，不提供） | **×100** |

    ⚠️ `index` 的**分钟线**原始值是 `amount/100` —— **派生值，不是成交量**，
    所以给 **`None`**（原始值仍留在 `raw["Volume"]`）。依据是本库红线：
    **拿不到就给 `None`，不许拿派生值冒充** —— 999999 的 60m `vol` 折算成手是
    **2.86 万亿股/小时**，当股讲 28.6 亿股也和大盘量级不符，怎么解释都不成立。
    实测证据：999999 / 880903 / 899050 的分钟线 `amount/vol` **恰好 100.000**
    （整数、一点不差，不可能是真实均价），而它们的 `amount` 与当日日线合计的
    比值是 1.0000 / 1.0002 ⇒ **amount 是真的，`vol` 是拿 amount 造出来的**。

    ⚠️ 换算**不允许四舍五入**（茅台 1,058,872 股 → 10,588.72 手），
    原始值完整保留在 `Bar.raw["Volume"]`。
    """
    if vol is None:
        return None
    try:
        x = float(vol)
    except (TypeError, ValueError):
        return vol
    kind = _vol_kind(code)
    if kind == "index" and per in _INTRADAY:
        return None                              # 派生值，不提供（原值见 raw）
    hand_native = kind != "stock"                # 日线本来就是"手"的品类
    higher = per in ("week", "month")            # 服务端在这两档给的是"日线值/100"
    if hand_native:
        return x * 100.0 if higher else x
    return x if higher else x / 100.0


def _i(v, default: int = 0) -> int:
    """宽松转 int（服务端偶尔给空串/None，别让一行坏数据炸掉整批）。"""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _stamp(date: str, time_: str) -> str:
    """`HqDate`/`HqTime`（`20261026` / `153002`）→ `2026-10-26 15:30:02`。

    原样值另存在 `Quote.raw` 里，这里只是给人看时好读一点；
    格式对不上就原样拼回去，绝不猜。

    ⚠️ `HqTime` **经常不足 6 位**：服务端把它当整数存，**前导 0 会被吃掉** ——
    上午 09:42:58 给的是 `"94258"`（5 位）、00:05:09 会给 `"509"`（3 位）。
    2026-10-27 修：**纯数字且长度 ≤ 6 → 左补 0 到 6 位**再格式化
    （`"94258"` → `"094258"` → `09:42:58`）；
    长度 > 6 或含非数字 → **原样返回**（守住"绝不猜"）。
    """
    d, t = str(date or ""), str(time_ or "")
    if len(d) == 8 and d.isdigit():
        d = "%s-%s-%s" % (d[:4], d[4:6], d[6:])
    if t.isascii() and t.isdigit() and 0 < len(t) <= 6:
        p = t.zfill(6)
        t = "%s:%s:%s" % (p[:2], p[2:4], p[4:])
    return ("%s %s" % (d, t)).strip()


def _adjust_code(adjust) -> tuple[int, str]:
    """`Adjust` / 字符串 → `(PBFXT field8, 口径名)`。

    **裸 `int` 一律拒绝**：`Adjust.QFQ == 1`，而协议里 `1` 表示"不复权" ——
    真把 `int(Adjust.QFQ)` 塞进请求，用户会拿到原始价却以为拿到了前复权价。
    这种错会静默算错收益率，所以宁可变麻烦也要拦。
    """
    if isinstance(adjust, Adjust):                 # 必须先判枚举（IntEnum 也是 int）
        name = adjust.name
    elif isinstance(adjust, str):
        name = adjust.strip().upper() or "NONE"
    elif adjust is None:
        name = "NONE"
    else:
        raise ValueError(
            "adjust 只接受 Adjust 枚举或字符串 %s；收到 %r。"
            "⚠️ 不要传裸 int —— 枚举值与协议码不同（Adjust.QFQ==1 而协议 1=不复权）"
            % ("/".join(_ADJUST_CODE), adjust))
    if name not in _ADJUST_CODE:
        raise ValueError("adjust 不认识：%r（可用 %s）"
                         % (adjust, " / ".join(_ADJUST_CODE)))
    return _ADJUST_CODE[name], name


_PLACEHOLDERS = {
    "code": "600519",          # 股票代码占位符的默认值（茅台，各命令都用得起）
    "date": "2026-10-23",      # 最近一个已收盘交易日
}


def _fill(params: Any, kwargs: dict, name: str) -> Any:
    """把产品参数里的占位符替换成 kwargs 的值。

    支持的占位符（本仓历史约定）：
        `@code@` / `#code#` / `@code#`   → kwargs["code"]
        `#date#`                          → kwargs["date"]
    其余 kwargs 直接按键名覆盖（若参数是 dict）。
    """
    kw = dict(_PLACEHOLDERS)
    kw.update(kwargs)

    def sub(x):
        if isinstance(x, str):
            s = x
            for key in ("code", "date"):
                s = s.replace("@%s@" % key, str(kw.get(key, "")))
                s = s.replace("#%s#" % key, str(kw.get(key, "")))
                s = s.replace("@%s#" % key, str(kw.get(key, "")))
            return s
        if isinstance(x, list):
            return [sub(i) for i in x]
        if isinstance(x, dict):
            return {k: sub(v) for k, v in x.items()}
        return x

    return sub(params)
