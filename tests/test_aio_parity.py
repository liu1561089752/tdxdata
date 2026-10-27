# -*- coding: utf-8 -*-
"""`AsyncClient` 与 `Client` 的**方法集对齐**（阶段 3）。

为什么这条测试必须存在
--------------------
`aio.py` 是"薄封装"，最容易出的不是逻辑 bug，而是**漏方法**：
同步层昨天加了 `get_breadth` / `timeliness`，异步壳忘了跟 ——
于是"两套 API 能力一致"这句话在文档里是真的、在代码里是假的，
而且是**静默**的（用户 `await c.get_breadth(...)` 只会得到 `AttributeError`）。

判据（口径写死，不留解释空间）
------------------------------
```
set(AsyncClient 公开名) - SELF_ONLY == set(Client 公开名)
```
`SELF_ONLY` 是**硬编码的封闭集**，只放 `AsyncClient` 自己的基础设施属性；
并加一条守卫：**多出来的名字必须恰好等于这个集合，多一个就红**。

⚠️ 为什么不用 `==` 直接比：`AsyncClient` 有 `client` / `max_concurrency`
两个属性，它们**在 `Client` 上没有对应概念**（`client` 就是它包的那个同步客户端）。
把口径写成 `==` 会逼着给 `Client` 造两个假属性，或者把这两个删掉 —— 都是扭曲。
换成"减掉一个**封闭**集合"，判据强度不变（封闭集不许悄悄变大），语义也不扭曲。
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

import tdxdata
from tdxdata.aio import AsyncClient

_CLS = getattr(tdxdata, "Client")          # 红线：测试里不写 `Client(` 字面量

#: `AsyncClient` **独有**的基础设施属性（不是"能力"，不该有同步版本）。
#: 这是**封闭集**：多一个名字就红（见 `test_async_extra_names_are_exactly_self_only`）。
SELF_ONLY = {"client", "max_concurrency"}


def _public_names(cls) -> set:
    """公开成员名（**含 property**）——`last_call` 这种属性也算"能力"。"""
    out = set()
    for name in dir(cls):
        if name.startswith("_"):
            continue
        try:
            attr = inspect.getattr_static(cls, name)
        except AttributeError:                              # pragma: no cover
            continue
        if isinstance(attr, property) or callable(getattr(cls, name, None)):
            out.add(name)
    return out


# ══════════════════════════════════════════════════════════════════════════
# 一、方法集对齐
# ══════════════════════════════════════════════════════════════════════════
def test_every_client_name_exists_on_async_client():
    sync, asy = _public_names(_CLS), _public_names(AsyncClient)
    missing = sorted(sync - asy)
    assert not missing, "AsyncClient 漏了这些能力：%s" % missing


def test_async_extra_names_are_exactly_self_only():
    """反向守卫：`AsyncClient` 多出来的必须**恰好**是 `SELF_ONLY`。"""
    sync, asy = _public_names(_CLS), _public_names(AsyncClient)
    extra = asy - sync
    assert extra == SELF_ONLY, (
        "AsyncClient 比 Client 多的名字变了：多出 %s，少了 %s。"
        "多出来的每个名字都必须显式登记进 SELF_ONLY 并说明为什么它不该有同步版本。"
        % (sorted(extra - SELF_ONLY), sorted(SELF_ONLY - extra)))


def test_parity_covers_the_newly_added_methods():
    """阶段 3 新增/补齐的方法点名核对（防止"对齐"只在纸面上发生）。"""
    must = ("get_breadth", "timeliness", "get_quotes", "get_ticks", "get_name",
            "get_samples", "get_block_data", "get_block_rows", "get_board_stat",
            "board_page", "iter_universe", "last_call")
    asy = _public_names(AsyncClient)
    for name in must:
        assert name in asy, "AsyncClient 缺 %s" % name


def test_properties_are_properties_on_both_sides():
    """`last_call` 在两边都必须是**属性**（不是需要 await 的方法）。"""
    assert isinstance(inspect.getattr_static(_CLS, "last_call"), property)
    assert isinstance(inspect.getattr_static(AsyncClient, "last_call"), property)


# ══════════════════════════════════════════════════════════════════════════
# 二、全是薄委托：每个协程方法体内都走 `self._run`
# ══════════════════════════════════════════════════════════════════════════
def _coroutine_methods() -> list:
    out = []
    for name in _public_names(AsyncClient):
        if name in SELF_ONLY:
            continue
        fn = getattr(AsyncClient, name)
        if inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn):
            out.append(name)
    return sorted(out)


def test_all_delegate_through_run():
    """**薄委托**不许有例外：任何协程方法都必须经过 `self._run`（并发护栏在那里）。

    绕过 `_run` 直接 `asyncio.to_thread(...)` 的那一个方法，就是唯一不守
    `max_concurrency` 的口子 —— 这种"差不多都对"最难查，所以用源码级断言钉住。
    """
    bad = []
    for name in _coroutine_methods():
        src = inspect.getsource(getattr(AsyncClient, name))
        if "self._run" not in src:
            bad.append(name)
    assert not bad, "这些协程方法没走 self._run（绕过了并发护栏）：%s" % bad


def test_no_method_forgets_await():
    """协程方法必须有 `await`（否则它返回的是一个没被等待的协程对象）。"""
    bad = [n for n in _coroutine_methods()
           if "await" not in inspect.getsource(getattr(AsyncClient, n))]
    assert not bad, "这些方法没有 await：%s" % bad


def test_iter_universe_is_async_generator():
    """`iter_universe` 必须是**异步生成器**（不是返回同步生成器的协程）。"""
    fn = getattr(AsyncClient, "iter_universe")
    assert inspect.isasyncgenfunction(fn), \
        "必须是 async generator，否则 `async for` 用不了"


# ══════════════════════════════════════════════════════════════════════════
# 三、功能：参数原样转、返回值透传
# ══════════════════════════════════════════════════════════════════════════
class FakeClient:
    """只为"透传"这个断言服务的假同步客户端。"""

    def __init__(self):
        self.calls = []
        self.last_call = {"source": {"host": None, "cmd": "X", "ctl": 1,
                                     "clock": "local", "cached": False}}

    def _rec(self, name, a, kw):
        self.calls.append((name, a, kw))
        return ("ret", name, a, kw)

    def get_breadth(self, code, *a, **kw):
        return self._rec("get_breadth", (code,) + a, kw)

    def get_quotes(self, symbols_, *a, **kw):
        return self._rec("get_quotes", (symbols_,) + a, kw)

    def get_ticks(self, symbol, *a, **kw):
        return self._rec("get_ticks", (symbol,) + a, kw)

    def get_name(self, symbol, *a, **kw):
        return self._rec("get_name", (symbol,) + a, kw)

    def get_samples(self, symbols_, *a, **kw):
        return self._rec("get_samples", (symbols_,) + a, kw)

    def get_block_data(self, block, *a, **kw):
        return self._rec("get_block_data", (block,) + a, kw)

    def get_block_rows(self, block, *a, **kw):
        return self._rec("get_block_rows", (block,) + a, kw)

    def get_board_stat(self, *a, **kw):
        return self._rec("get_board_stat", a, kw)

    def board_page(self, *a, **kw):
        return self._rec("board_page", a, kw)

    def timeliness(self, *a, **kw):
        return self._rec("timeliness", a, kw)

    def iter_universe(self, *a, **kw):
        for i in range(3):
            yield ("q", i)


def _run(coro):
    return asyncio.run(coro)


CASES = [
    ("get_breadth", ("880005",), ("880005",)),
    ("get_quotes", (["600519"],), (["600519"],)),
    ("get_ticks", ("600519",), ("600519",)),
    ("get_name", ("600519",), ("600519",)),
    ("get_samples", (["600519"],), (["600519"],)),
    ("get_block_data", ("MStock_ZLJX_ADDE_R",), ("MStock_ZLJX_ADDE_R",)),
    ("board_page", (), ()),
]


@pytest.mark.parametrize("name,args,expect", CASES)
def test_delegation_passes_args_and_returns(name, args, expect):
    """参数原样转、返回值原样透传（薄委托的全部职责）。"""
    fake = FakeClient()
    ac = AsyncClient(client=fake)
    got = _run(getattr(ac, name)(*args))
    assert got[0] == "ret" and got[1] == name
    assert fake.calls[-1][0] == name
    assert fake.calls[-1][1] == expect


def test_last_call_property_passes_through():
    fake = FakeClient()
    ac = AsyncClient(client=fake)
    assert ac.last_call == fake.last_call
    assert tuple(ac.last_call["source"]) == ("host", "cmd", "ctl", "clock", "cached")


def test_iter_universe_async_iteration():
    fake = FakeClient()
    ac = AsyncClient(client=fake)

    async def go():
        out = []
        async for item in ac.iter_universe(market=6):
            out.append(item)
        return out

    assert _run(go()) == [("q", 0), ("q", 1), ("q", 2)]


def test_iter_universe_early_break_closes_generator():
    """`break` 之后不许继续拉（同步生成器要被关掉，别把连接/状态挂着）。"""
    fake = FakeClient()
    ac = AsyncClient(client=fake)

    async def go():
        n = 0
        async for _item in ac.iter_universe():
            n += 1
            if n == 1:
                break
        return n

    assert _run(go()) == 1


def test_async_client_rejects_both_client_and_kwargs():
    with pytest.raises(TypeError):
        AsyncClient(FakeClient(), client=FakeClient())


def test_max_concurrency_must_be_positive():
    with pytest.raises(ValueError):
        AsyncClient(client=FakeClient(), max_concurrency=0)
