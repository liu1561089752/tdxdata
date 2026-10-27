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
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from . import errors, symbols
from .enums import Adjust, KlinePeriod
from .models import Bar, Dataset, MinutePoint, ProductInfo, Quote, Table, Tick

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
    """

    def __init__(self, host: str | None = None, timeout: float = 15.0,
                 gap: float = 2.2, cache=None):
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
        self._load_products()

    # ── 产品表 ────────────────────────────────────────────────────────────
    def _load_products(self) -> None:
        if not os.path.exists(_DATA):
            raise errors.TdxError("产品数据缺失：%s" % _DATA)
        payload = json.load(open(_DATA, encoding="utf-8"))
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
    def _pace(self) -> None:
        """走**进程级**匀速闸门（不是本实例私有时钟）。

        这样同一进程里开多个 Client 也不会把请求频率叠加起来 ——
        服务端限流是按来源算的，不是按 Client 对象算的。
        """
        self._pacer.wait()

    def _ensure(self):
        """懒建连（长连接复用；断了换服务器重连）。

        **必须加锁**：多线程同时首次调用时，不加锁会各自建一条连接，
        实测 3 线程建出 4 条（多余连接泄漏且各自被服务端计入频率）。
        """
        if self._sess is not None and self._sess.alive:
            return self._sess
        with self._ensure_lock:
            if self._sess is None or not self._sess.alive:
                self._sess = self._session_mod.Session(
                    self._host, self._timeout).connect()
            return self._sess

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
        """
        args = self._session_mod.body_of(cmd, params)

        # ① 缓存命中：闸门都不用进（缓存不是为了绕过限流，是为了根本不发请求）
        ck = None
        if self._cache is not None:
            from ._engine.cache import cache_key

            ck = cache_key(cmd, params, None)
            hit = self._cache.get(ck)
            if hit is not None:
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
                        self._sess.reconnect()
                    except Exception:                      # noqa: BLE001
                        self._sess = None
                else:
                    self._pacer.note_limited()             # 间隔翻倍
                    try:
                        self._sess.switch_host()
                    except Exception:                      # noqa: BLE001
                        self._sess = None
                continue

            verdict, nrow, ncol, cols, rows, primary = self._session_mod.parse(raw)
            if verdict == "PASS":
                self._pacer.note_ok()
                table = self._make_table(cmd, cols, rows, primary)
                if ck is not None:
                    self._cache.set(ck, table)
                return table

            if verdict == "EMPTY_RS":                      # 有列无行 = 当期无数据
                self._pacer.note_ok()                      # 通道是通的，不算限流
                if allow_empty:
                    table = Table(cols=list(primary or cols), rows=[], cmd=cmd,
                                  meta={"empty": "EMPTY_RS"})
                    if ck is not None:
                        self._cache.set(ck, table)
                    return table
                raise errors.EmptyResult("%s 无数据（有列无行）" % cmd)

            if verdict == "EMPTY":
                # 空包可能是「限流静默丢包」，也可能是「命令确实不存在」。
                # 第一次不急着定性：退避重试；连续多轮都空才判 NotSupported。
                empty_hits += 1
                last_err = errors.NotSupportedError("%s 无响应" % cmd)
                if attempt < retry:
                    self._pacer.note_limited()             # 当作疑似限流处理
                    try:
                        self._sess.switch_host()
                    except Exception:                      # noqa: BLE001
                        self._sess = None
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
        """
        spec = self._products.get(name)
        if spec is None:
            raise KeyError("没有这个产品：%s（用 products() 查看全部）" % name)
        params = _fill(spec["params"], kwargs, name)
        table = self.call(spec["cmd"], spec["ctl"], params, allow_empty=allow_empty)
        table.domain = spec.get("domain", "")
        return Dataset(name=name, table=table, params=params)

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
        """
        from ._engine import hq_pb

        per = self._period_str(period)
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

        out = []
        for r in rows:
            out.append(Bar(
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
            ))
        return out

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
        """当日分时（240 点）。7615 固定取当日，不接受日期参数。"""
        from ._engine import hq_pb

        market, code = symbols.parse(symbol)
        cols, rows = hq_pb.get_minutes(code, market=market)
        if not rows:
            raise errors.EmptyResult("%s 当日无分时数据" % symbol)

        out = []
        for r in rows:
            m = int(_f(r.get("Minute"), 0))
            out.append(MinutePoint(
                code=code, market=market,
                time="%02d:%02d" % (m // 60, m % 60),
                price=_f(r.get("Now")), avg_price=_f(r.get("Average")),
                vol=_f(r.get("NowVol")), raw=r,
            ))
        return out

    def get_quotes(self, symbols_, batch: int = 20, **kwargs) -> list[Quote]:
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
        """
        from ._engine import hq_extra as _x

        cols, rows = _x.get_quotes(symbols_, batch=batch)
        out: list[Quote] = []
        for r in rows:
            out.append(_quote_from_row(dict(zip(cols, r))))
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

        blk, status, rows = _x.block_data(block, spec, field2)
        n = max((len(r) for r in rows), default=0)
        cols = ["market", "code", "name"] + ["c%d" % i for i in range(3, n)]
        return Table(cols=cols, rows=rows, cmd="HQServ.PBXmlBlock",
                     meta={"block": blk, "status": status, "spec": spec,
                           "field2": field2, "note": "c3 起的列含义未定：服务端未给列名，这里只按位置命名"})

    def get_block_rows(self, block: str, kind: str = "HY", level: int = 1,
                       max_rows: int = 200, field2: int = 0) -> Table:
        """块数据**按页取全**（`PBXmlBlock` 分页）→ `Table`。

        `spec` 的第 3/4 位就是「起始 / 数量」，所以翻页是服务端原生支持的，
        本方法只是把每页 100 行串起来。`max_rows` 是硬上限（防块名给错时打太多请求）。
        """
        from ._engine import hq_extra as _x

        rows = _x.block_rows(block, kind=kind, level=level,
                             max_rows=max_rows, field2=field2)
        if not rows:
            raise errors.EmptyResult(
                "块 %s 一页都没取到（kind=%s level=%d field2=%s）" % (block, kind, level, field2))
        n = max((len(r) for r in rows), default=0)
        cols = ["market", "code", "name"] + ["c%d" % i for i in range(3, n)]
        return Table(cols=cols, rows=rows, cmd="HQServ.PBXmlBlock",
                     meta={"block": block, "kind": kind, "level": level, "field2": field2,
                           "note": "c3 起的列含义未定：服务端未给列名，这里只按位置命名"})

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

        items = _x.samples(symbols_, batch=batch)
        n = max((len(d["floats"]) for d in items), default=0)
        cols = ["code", "pre_close"] + ["f%d" % i for i in range(n)]
        rows = []
        for d in items:
            row = {"code": d["code"], "pre_close": d["pre_close"] or 0.0}
            for i, v in enumerate(d["floats"]):
                row["f%d" % i] = v
            rows.append(row)
        return Table(cols=cols, rows=rows, cmd="HQServ.PBSampleMore",
                     meta={"note": "f0.. 的列序未锚定，别当 high/low 用：服务端未给列名"})

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

        cols, rows = _x.board_stat(f2=f2, f3=f3, f5=f5)
        if not rows:
            raise errors.EmptyResult(
                "PBHYStat 没返回数据（f3=%s f5=%s）" % (f3, f5))
        return Table(cols=cols, rows=rows, cmd="HQServ.PBHYStat",
                     meta={"f3": f3, "f5": f5,
                           "note": "f5=视图(0=纯板块列表/14=板块+成分交替)；f3=类别"
                                   "(f5=14 时只影响第 9 行起的尾部)；Other 量纲未定，"
                                   "f3 取值含义见 docs/VERIFICATION.md"})

    def get_ticks(self, symbol, count: int = 200, max_pages: int = 50) -> list[Tick]:
        """逐笔成交（`HQServ:PBTickEx`，2026-10-27 打通）。返回 `list[Tick]`，**按时间升序**（旧→新）。

        **翻页是自动的**：首页锚点 `"0"`（最新一页），之后每页锚点 = 上一页响应里的
        **「区间起点」**（`field1.field4` 的 `"1995,2095"` 取 `1995`）。
        ⚠️ 锚点**不是**行里的 `Second`（拿 `Second` 当锚点会回一整页 0 —— 实测踩过）。

        停止条件（任一满足）：拿到 `count` 条 / 某页 0 行 / 区间起点为 0 / 翻满 `max_pages` 页。
        按**协议行的序号**（`区间起点 + 行内下标`）去重，所以多页衔接**不会重复也不会丢**。

        **单位**：`price` 元；`vol` **手**（与 `Quote.vol`/`Bar.vol` 同单位）。
        **未定名字段**（`VolInStockDiff`/`InOutFlag`/`HKTTFlag`/`HKInOutFlag`）只在 `tick.raw` 里，
        本库**不猜买卖方向**（`bs_flag` 恒为 2 = 未知）。

        参数：
            symbol: `"600519"` / `"sh600519"` / `(1, "600519")` 等，交给 `symbols.parse`
            count:  要多少笔（默认 200；每页 100，自动翻页）
            max_pages: 最多翻多少页（防死循环，默认 50）
        """
        from ._engine import hq_extra

        market, code = symbols.parse(symbol)
        want = int(count)
        if want <= 0:
            raise ValueError("count 必须 > 0（收到 %r）" % (count,))
        out = _collect_ticks(lambda a: hq_extra.ticks(code, market, anchor=a, count=100),
                             market, code, want, max_pages)
        if not out:
            raise errors.EmptyResult("%s 当日无逐笔数据" % symbol)
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
        """
        from ._engine import hq_pb

        cols_, rows = hq_pb.get_market_page(market=market, offset=offset,
                                            page_size=page_size, cols=cols)
        if not rows:
            raise errors.EmptyResult(
                "PBMultiHQ market=%s offset=%s 返回 0 行（起始页大小 %s，已翻倍重试到 %d 仍空）。"
                "常见原因：列数太少导致「列数×page_size」低于服务端下限，或该市场码无数据。"
                "可加大 cols 或 page_size 再试。"
                % (market, offset, page_size, hq_pb.MAX_PAGE_ROWS))
        out = []
        for r in rows:
            out.append(_quote_from_page_row(r, market))
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
        """
        pages = int(max_pages)
        if pages < 1:
            raise ValueError("max_pages 必须 ≥ 1（收到 %r）" % (max_pages,))
        out: list[Quote] = []
        seen: set[tuple[int, str]] = set()
        off = 0
        for _ in range(pages):
            try:
                page = self.get_market_page(market=market, offset=off, page_size=page_size)
            except errors.EmptyResult:
                if not out:
                    raise       # 第一页就空 = 参数/列数配错，绝不静默当"到底了"
                break           # 翻到后面才空 = 正常到底
            fresh = [q for q in page if (q.market, q.code) not in seen]
            if not fresh:                  # 无进展：到底了，或服务端没按 offset 挪窗口
                break
            for q in fresh:
                seen.add((q.market, q.code))
                out.append(q)
            off += len(page)               # offset 是行偏移 → 按本次返回行数推进
            if len(page) < int(page_size):  # 不足一页 → 已是最后一页
                break
        return out

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
        return Table(cols=cols, rows=rows, cmd="local:indicator",
                     meta={"period": period, "count": len(bars)})

    def get_price_limits(self, symbol, prev_close: float | None = None,
                         is_st: bool = False) -> tuple[float, float]:
        """涨跌停价 `(limit_up, limit_down)`，按板块规则 + 交易所取整。

        `prev_close` 不给就取最近一根日线收盘（= 上一交易日收盘）。
        ST 判定需要证券名称，本库不做名称推断 —— **要 5% 就显式传 `is_st=True`**，
        否则一律按非 ST 板块幅度算（这是契约，不是猜测）。
        """
        from . import limits as _limits

        _mkt, code = symbols.parse(symbol)
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

        return _finance.get_finance(self, symbol, kind=kind)

    def get_f10(self, symbol, section: str = "gsjj") -> Table:
        """F10 个股资料（公司简介 / 经营分析 / 股东 / 分红…）。"""
        from .sections import f10 as _f10

        return _f10.get_f10(self, symbol, section=section)

    def get_xdxr(self, symbol) -> Table:
        """分红融资（`ph_agf10_fhrz`）。手机版 7615 无独立除权除息命令。"""
        from .sections import xdxr as _xdxr

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
        """
        from ._engine import hq_pb

        return hq_pb.get_market_page(
            market=market, offset=offset, page_size=page_size,
            cols=["Code", "Setcode", "Name", "CLOSE", "NOW", "AMOUNT"])

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

    # ── 生命周期 ──────────────────────────────────────────────────────────
    def rate_stats(self) -> dict:
        """当前限流状态（排查用）：基准/当前间隔、累计被限流次数、冷却中的服务器数。"""
        return self._pacer.stats()

    def close(self) -> None:
        """关闭长连接（数据中心那条 + PB 路径的持久连接）。"""
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
