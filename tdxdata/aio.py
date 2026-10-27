# -*- coding: utf-8 -*-
"""`tdxdata.aio.AsyncClient` —— 同步 `Client` 的 asyncio 薄封装。

只做一件事
----------
每个方法都是 `await self._run(self._c.方法, ...)`：参数原样转、返回值原样透传。
**不重写协议层、不加自己的重试/缓存** —— 限流、重连、结果判定全在同步层，
两套实现只会让行为出现分叉（本仓 7615 是"握手 + 串行慢速"协议，异步收益很小）。

用法
----
    import asyncio
    from tdxdata.aio import AsyncClient

    async def main():
        async with AsyncClient() as c:                 # 退出时关连接
            bars = await c.get_bars("600519", count=5)
            t = await c.get("lhb", date="2026-10-23")
            print(bars[-1].close, t.shape)

    asyncio.run(main())

注意
----
* 并发度受同步层约束：7615 的匀速闸门是**进程级**的，开 8 个任务并不会快 8 倍，
  只是把等待挪到线程池里。**多开连接没用**（2026-10-26 实测：建连过频直接吃 `10053`，
  已把 `ConnectionPool` 删掉）—— 要更快只能减少请求数，或跨进程/跨机分摊。
* 这层**不导入** `indicator` / `limits` / `sections` 等重模块 ——
  它们是 `Client` 方法体里懒 import 的，这里提前导入会连带整包启动变慢。
"""
from __future__ import annotations

import asyncio

__all__ = ["AsyncClient"]


class AsyncClient:
    """把 `Client` 的常用方法包成协程（`await`）。

    参数
    ----
    client : Client | None
        注入一个已建好的同步客户端（**测试用假对象即可，不必连网**）。
        给了它就不能再给其它参数。
    **kw :
        原样转给 `Client(**kw)`（`host` / `timeout` / `gap` …）。`client` 为
        `None` 时才生效。

    位置参数也会原样转给 `Client`（兼容 `API设计.md` §2.7 的 `__init__(*a, **kw)` 写法）。

    并发与限流（2026-10-26 加护栏）
    ------------------------------
    `asyncio.gather(*[ac.get_bars(x) for x in 20只])` 在**没有闸门**时会把 20 个任务一起
    丢进线程池，等于"20 路并发取数" —— 而 7615 是按来源限速的，实测 `gap` 压到 0.2 秒时
    第 8 次就被踢（`10053/10054`）。

    所以默认 `max_concurrency=1`：`gather` 只是把等待挪进线程池，**不会**并发出多条网络流。
    想更快只有两个办法：**减少请求数**，或**跨进程/跨机分摊**。

    `max_concurrency>1` 只在两种情况下有意义：
      * **数据中心类**命令（`get`/`call`）：内部走 `Session` + `RLock` + pacer，排队是安全的；
      * **纯本地计算**（`get_indicators`/`get_price_limits`）：根本不碰网络。

    PB 路径（K 线 / 分时 / 市场分页）即使开大，也只是排在 `datacenter` 的连接锁和 pacer 后面。
    """

    def __init__(self, *a, client=None, max_concurrency: int = 1, **kw):
        if client is None:
            from .client import Client          # 懒 import：避免包初始化期绕圈
            client = Client(*a, **kw)
        elif a or kw:
            raise TypeError(
                "给了 client 就不要再传 Client 的参数（多余的：%r / %r）" % (a, kw))
        self._c = client
        n = int(max_concurrency)
        if n < 1:
            raise ValueError("max_concurrency 必须 ≥ 1（收到 %r）" % (max_concurrency,))
        self._max_concurrency = n
        self._sem = asyncio.Semaphore(n)        # 3.10+ 无需绑定 loop

    # ── 只读入口 ──────────────────────────────────────────────────────────
    @property
    def client(self):
        """底层同步 `Client`（想要同步语义时直接用）。"""
        return self._c

    @property
    def max_concurrency(self) -> int:
        """当前并发上限（默认 1）。"""
        return self._max_concurrency

    # ── 统一入口：**所有方法都必须走这里**，别再直接 asyncio.to_thread ──────
    async def _run(self, fn, *a, **kw):
        async with self._sem:                   # ★ 并发护栏
            return await asyncio.to_thread(fn, *a, **kw)

    # ── 底层 ──────────────────────────────────────────────────────────────
    async def call(self, cmd, ctl, params=None, **kw):
        """`await` 版 `Client.call`，参数/返回值完全不改。"""
        return await self._run(self._c.call, cmd, ctl, params, **kw)

    async def get(self, name, **kw):
        """`await` 版 `Client.get`（按产品名取数）。"""
        return await self._run(self._c.get, name, **kw)

    # ── 行情 ──────────────────────────────────────────────────────────────
    async def get_bars(self, symbol, *a, **kw):
        """`await` 版 `Client.get_bars`（K 线，返回 `list[Bar]`）。"""
        return await self._run(self._c.get_bars, symbol, *a, **kw)

    async def get_minutes(self, symbol, *a, **kw):
        """`await` 版 `Client.get_minutes`（当日分时）。"""
        return await self._run(self._c.get_minutes, symbol, *a, **kw)

    # ── 本地计算（派生指标 / 涨跌停）──────────────────────────────────────
    async def get_indicators(self, symbol, *a, **kw):
        """`await` 版 `Client.get_indicators`（MACD/MA/KDJ/RSI/BOLL）。"""
        return await self._run(self._c.get_indicators, symbol, *a, **kw)

    async def get_price_limits(self, symbol, *a, **kw):
        """`await` 版 `Client.get_price_limits`（涨跌停价）。"""
        return await self._run(self._c.get_price_limits, symbol, *a, **kw)

    # ── 财务 / F10 / 分红 ─────────────────────────────────────────────────
    async def get_finance(self, symbol, *a, **kw):
        """`await` 版 `Client.get_finance`（财务报表）。"""
        return await self._run(self._c.get_finance, symbol, *a, **kw)

    async def get_f10(self, symbol, *a, **kw):
        """`await` 版 `Client.get_f10`（F10 个股资料）。"""
        return await self._run(self._c.get_f10, symbol, *a, **kw)

    async def get_xdxr(self, symbol, *a, **kw):
        """`await` 版 `Client.get_xdxr`（分红融资）。"""
        return await self._run(self._c.get_xdxr, symbol, *a, **kw)

    # ── 板块 / 资金 / 市场 ───────────────────────────────────────────────
    async def get_boards(self, *a, **kw):
        """`await` 版 `Client.get_boards`（板块列表）。"""
        return await self._run(self._c.get_boards, *a, **kw)

    async def get_board_members(self, board, *a, **kw):
        """`await` 版 `Client.get_board_members`（板块成分股）。"""
        return await self._run(self._c.get_board_members, board, *a, **kw)

    async def get_capital_flow(self, symbols=None, *a, **kw):
        """`await` 版 `Client.get_capital_flow`（资金流向）。"""
        return await self._run(self._c.get_capital_flow, symbols, *a, **kw)

    async def get_market_stat(self, *a, **kw):
        """`await` 版 `Client.get_market_stat`（市场概览）。"""
        return await self._run(self._c.get_market_stat, *a, **kw)

    async def get_market_page(self, *a, **kw):
        """`await` 版 `Client.get_market_page`（市场行情一页，2026-10-25 新增）。"""
        return await self._run(self._c.get_market_page, *a, **kw)

    async def get_universe(self, *a, **kw):
        """`await` 版 `Client.get_universe`（按市场翻页枚举，有 max_pages 上限）。

        ⚠️ 每页各过一次闸门（`gap=2.2`）；**实测 3 页 6.60 秒**（2026-10-26），
        即约 2.2 秒/页 → `max_pages=20` 约 44 秒。在事件循环里**仍然阻塞在 to_thread 的线程上**，
        不会自动并行加速。
        """
        return await self._run(self._c.get_universe, *a, **kw)

    # ── 元信息（本地，不发网络请求）──────────────────────────────────────
    async def products(self, *a, **kw):
        """`await` 版 `Client.products`（421 个数据产品清单）。"""
        return await self._run(self._c.products, *a, **kw)

    async def domains(self, *a, **kw):
        """`await` 版 `Client.domains`（有产品的域）。"""
        return await self._run(self._c.domains, *a, **kw)

    async def rate_stats(self, *a, **kw):
        """`await` 版 `Client.rate_stats`（限流状态快照）。"""
        return await self._run(self._c.rate_stats, *a, **kw)

    # ── 生命周期 ──────────────────────────────────────────────────────────
    async def close(self):
        """关连接（同步层的 `close()` 放进线程池执行）。"""
        return await self._run(self._c.close)

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, *exc) -> bool:
        await self.close()
        return False

    def __repr__(self) -> str:
        return "<aio.AsyncClient of %r>" % (self._c,)
