# -*- coding: utf-8 -*-
"""`tdxdata.aio.AsyncClient` 的**离线**单元测试（不连服务器）。

判据：每个 `await` 方法把参数**原样**转给同步方法、返回值原样透传、
`async with` 退出时 `close()` 被调用一次。
全部用假 `Client`，**任何用例都不建连**。

跑法：
    python tests/test_aio.py
    pytest tests/test_aio.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tdxdata.aio import AsyncClient                               # noqa: E402


# ── 假 Client：任何方法都记录调用并原样回传 ───────────────────────────────
class FakeClient:
    def __init__(self):
        self.calls: list = []          # [(方法名, 位置参数, 关键字参数)]
        self.closed = 0

    def _rec(self, name, a, kw):
        self.calls.append((name, a, kw))
        return ("ret", name, a, kw)

    # 显式写出来（而不是全靠 __getattr__），这样测试也在检查拼写
    def call(self, cmd, ctl, params=None, **kw):
        return self._rec("call", (cmd, ctl, params), kw)

    def get(self, name, **kw):
        return self._rec("get", (name,), kw)

    def get_bars(self, symbol, *a, **kw):
        return self._rec("get_bars", (symbol,) + a, kw)

    def get_minutes(self, symbol, *a, **kw):
        return self._rec("get_minutes", (symbol,) + a, kw)

    def get_indicators(self, symbol, *a, **kw):
        return self._rec("get_indicators", (symbol,) + a, kw)

    def get_price_limits(self, symbol, *a, **kw):
        return self._rec("get_price_limits", (symbol,) + a, kw)

    def get_finance(self, symbol, *a, **kw):
        return self._rec("get_finance", (symbol,) + a, kw)

    def get_f10(self, symbol, *a, **kw):
        return self._rec("get_f10", (symbol,) + a, kw)

    def get_xdxr(self, symbol, *a, **kw):
        return self._rec("get_xdxr", (symbol,) + a, kw)

    def get_boards(self, *a, **kw):
        return self._rec("get_boards", a, kw)

    def get_board_members(self, board, *a, **kw):
        return self._rec("get_board_members", (board,) + a, kw)

    def get_capital_flow(self, symbols=None, *a, **kw):
        return self._rec("get_capital_flow", (symbols,) + a, kw)

    def get_market_stat(self, *a, **kw):
        return self._rec("get_market_stat", a, kw)

    def products(self, *a, **kw):
        return self._rec("products", a, kw)

    def domains(self, *a, **kw):
        return self._rec("domains", a, kw)

    def rate_stats(self, *a, **kw):
        return self._rec("rate_stats", a, kw)

    def close(self):
        self.closed += 1


def _run(coro):
    return asyncio.run(coro)


# ── 逐个方法：参数原样转 + 返回值透传 ─────────────────────────────────────
# (方法名, 调用参数, 期望同步方法收到的位置参数)
CASES = [
    ("call", ("CWServ.ph_datacenter_hg_holiday", 0x21, ["001", "1"]),
     ("CWServ.ph_datacenter_hg_holiday", 0x21, ["001", "1"])),
    ("get", ("lhb",), ("lhb",)),
    ("get_bars", ("600519", "day", 5), ("600519", "day", 5)),
    ("get_minutes", ("600519",), ("600519",)),
    ("get_indicators", ("600519", {"ma": {"n": [5, 20]}}, "day", 60),
     ("600519", {"ma": {"n": [5, 20]}}, "day", 60)),
    ("get_price_limits", ("600519", 1500.0, True), ("600519", 1500.0, True)),
    ("get_finance", ("600519", "zcfz"), ("600519", "zcfz")),
    ("get_f10", ("600519", "gsjj"), ("600519", "gsjj")),
    ("get_xdxr", ("600519",), ("600519",)),
    ("get_boards", ("industry",), ("industry",)),
    ("get_board_members", ("银行",), ("银行",)),
    ("get_capital_flow", (["600519", "000001"],), (["600519", "000001"],)),
    ("get_market_stat", ("updown",), ("updown",)),
    ("products", ("行情",), ("行情",)),
    ("domains", (), ()),
    ("rate_stats", (), ()),
]


def test_all_methods_forward_verbatim():
    fake = FakeClient()
    ac = AsyncClient(client=fake)
    for name, args, expect_pos in CASES:
        got = _run(getattr(ac, name)(*args))
        assert got == ("ret", name, expect_pos, {}), \
            "%s 返回值/参数没原样透传：%r" % (name, got)
        assert len(fake.calls) >= 1
        called_name, called_a, called_kw = fake.calls[-1]
        assert called_name == name, "调错了同步方法：%r" % (called_name,)
        assert called_a == expect_pos, \
            "%s 位置参数变形：%r != %r" % (name, called_a, expect_pos)
        assert called_kw == {}, "%s 凭空多了关键字参数：%r" % (name, called_kw)
    assert len(fake.calls) == len(CASES), \
        "调用次数不对：%d != %d" % (len(fake.calls), len(CASES))
    print("     %d 个协程方法全部原样转发" % len(CASES))


def test_kwargs_forward_verbatim():
    fake = FakeClient()
    ac = AsyncClient(client=fake)
    got = _run(ac.get_bars("600519", period="week", count=10,
                           adjust="QFQ"))
    assert got[1] == "get_bars"
    name, a, kw = fake.calls[-1]
    assert a == ("600519",), a
    assert kw == {"period": "week", "count": 10, "adjust": "QFQ"}, kw

    got = _run(ac.get("lhb", date="2026-10-23", allow_empty=True))
    assert got[1] == "get"
    name, a, kw = fake.calls[-1]
    assert a == ("lhb",) and kw == {"date": "2026-10-23", "allow_empty": True}
    print("     关键字参数原样转发：%r" % (kw,))


def test_default_params_not_invented():
    """没传的参数不许被包装层补上（补了就等于改了 Client 的默认行为）。"""
    fake = FakeClient()
    ac = AsyncClient(client=fake)
    _run(ac.get_capital_flow())
    name, a, kw = fake.calls[-1]
    assert a == (None,), "get_capital_flow() 应只传 symbols=None：%r" % (a,)
    assert kw == {}, kw

    _run(ac.get_boards())
    assert fake.calls[-1][1] == () and fake.calls[-1][2] == {}, fake.calls[-1]


def test_return_value_passthrough_with_objects():
    """同步方法返回任意对象都原样透传（不包装、不复制）。"""
    sentinel = object()

    class Fake2(FakeClient):
        def get_bars(self, symbol, *a, **kw):
            self.calls.append(("get_bars", (symbol,), kw))
            return sentinel

    fake = Fake2()
    ac = AsyncClient(client=fake)
    got = _run(ac.get_bars("600519"))
    assert got is sentinel, "返回值没有原样透传：%r" % (got,)


def test_exception_propagates():
    class Boom(FakeClient):
        def get_bars(self, symbol, *a, **kw):
            raise ValueError("count 必须 ≥ 1（收到 0）")

    ac = AsyncClient(client=Boom())
    try:
        _run(ac.get_bars("600519", count=0))
    except ValueError as e:
        assert "count" in str(e)
        print("     同步层异常原样透传：%s" % e)
    else:
        raise AssertionError("同步层异常应当原样冒出")


# ── 生命周期 ──────────────────────────────────────────────────────────────
def test_async_with_closes_once():
    fake = FakeClient()
    ac = AsyncClient(client=fake)

    async def main():
        async with ac as c:
            assert c is ac, "__aenter__ 应返回自己"
            await c.get_bars("600519")
        return ac

    _run(main())
    assert fake.closed == 1, "退出 async with 应恰好 close 一次，实际 %d" % fake.closed


def test_close_is_awaitable_and_idempotent():
    fake = FakeClient()
    ac = AsyncClient(client=fake)
    _run(ac.close())
    _run(ac.close())
    assert fake.closed == 2, "close() 每次都应转给同步层（幂等性由同步层负责）"


def test_client_property_is_readonly():
    fake = FakeClient()
    ac = AsyncClient(client=fake)
    assert ac.client is fake, "client 属性应返回注入的对象"
    try:
        ac.client = object()
    except AttributeError:
        pass
    else:
        raise AssertionError("client 应当是只读属性")


# ── 构造 ──────────────────────────────────────────────────────────────────
def test_injected_client_ignores_other_args():
    fake = FakeClient()
    ac = AsyncClient(client=fake)
    assert ac.client is fake
    for bad_kw in ({"host": "1.2.3.4"}, {}):
        try:
            AsyncClient("positional", client=fake, **bad_kw)
        except TypeError as e:
            assert "client" in str(e), e
        else:
            raise AssertionError("给了 client 又给 Client 参数应当报错")


def test_default_construction_passes_kwargs_to_client():
    """不给 client → 内部 new 一个真 Client（**只读产品表，不建连**）。"""
    ac = AsyncClient(host="127.0.0.1", timeout=1.0)
    from tdxdata.client import Client

    assert isinstance(ac.client, Client), type(ac.client)
    assert ac.client._host == "127.0.0.1"
    assert ac.client._timeout == 1.0
    _run(ac.close())                        # 没有连接，close 是空操作
    print("     默认构造走的 Client(**kw)：host=%s timeout=%s"
          % (ac.client._host, ac.client._timeout))


def test_no_network_modules_imported_eagerly():
    """`aio` 不许在导入期把重模块拉进来（Client 是懒 import 的）。"""
    import importlib

    src = importlib.import_module("tdxdata.aio")
    with open(src.__file__, encoding="utf-8") as f:
        code = f.read()
    for name in ("indicator", "limits", "sections", "hq_pb"):
        assert ("import %s" % name) not in code, "aio.py 不该 import %s" % name
    assert "asyncio.to_thread" in code


# ══════════════════════════════════════════════════════════════════════════
# D5 并发护栏（2026-10-26）
# ══════════════════════════════════════════════════════════════════════════
class SlowClient(FakeClient):
    """统计**同时在飞**的调用数（真并发会 >1）。"""

    def __init__(self, delay=0.02):
        super().__init__()
        self.delay = delay
        self.now = 0
        self.peak = 0

    def get_bars(self, symbol, *a, **kw):
        import time as _t
        self.now += 1
        self.peak = max(self.peak, self.now)
        try:
            _t.sleep(self.delay)
            return self._rec("get_bars", (symbol,) + a, kw)
        finally:
            self.now -= 1


def test_gather_default_is_serial():
    """**默认 max_concurrency=1**：`gather(10)` 峰值并发必须是 1。

    这是 D5 的核心判据 —— 没有这道闸门时，`gather` 会同时丢 10 个线程去建连，
    7615 会直接 `10053/10054`（实测 gap 压到 0.2s 时第 8 次就被踢）。
    """
    fake = SlowClient()
    ac = AsyncClient(client=fake)
    assert ac.max_concurrency == 1

    async def main():
        return await asyncio.gather(*[ac.get_bars("600519") for _ in range(10)])

    out = _run(main())
    assert len(out) == 10
    assert fake.peak == 1, "默认必须串行，实测峰值并发 %d" % fake.peak


def test_gather_respects_explicit_limit():
    """显式放宽也要守上限。"""
    fake = SlowClient(delay=0.03)
    ac = AsyncClient(client=fake, max_concurrency=3)

    async def main():
        return await asyncio.gather(*[ac.get_bars("600519") for _ in range(10)])

    _run(main())
    assert 1 <= fake.peak <= 3, "峰值并发 %d 超过上限 3" % fake.peak
    assert fake.peak > 1, "max_concurrency=3 却没有真并发（峰值 %d）" % fake.peak


def test_max_concurrency_validation():
    for bad in (0, -1):
        try:
            AsyncClient(client=FakeClient(), max_concurrency=bad)
        except ValueError as e:
            assert "max_concurrency" in str(e)
        else:
            raise AssertionError("max_concurrency=%r 必须抛 ValueError" % (bad,))


def test_all_wrappers_go_through_the_gate():
    """结构性护栏：不许有方法绕过 `_run` 直接 `asyncio.to_thread`。

    否则新加的方法会悄悄绕过并发闸门 —— 这类"漏一个"最难发现。
    """
    import importlib
    import re

    src = importlib.import_module("tdxdata.aio")
    with open(src.__file__, encoding="utf-8") as f:
        code = f.read()
    # `_run` 自己的实现里允许出现一次（注释里提到不算）
    assert code.count("await asyncio.to_thread(") == 1, \
        "只有 _run 里能真的调 asyncio.to_thread，实际 %d 处" % code.count("await asyncio.to_thread(")
    # 任何"碰底层 client"的方法，都必须通过 self._run
    bodies = re.findall(r"async def (\w+)\(.*?\n(.*?)(?=\n    async def |\n    def |\Z)",
                        code, re.S)
    bad = [n for n, b in bodies if "self._c." in b and "self._run" not in b]
    assert not bad, "这些方法直接用了 self._c 却绕过并发闸门：%s" % bad


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print("  ✅ %s" % name)
        except AssertionError as e:
            bad += 1
            print("  ❌ %s: %s" % (name, e))
        except Exception as e:                          # noqa: BLE001
            bad += 1
            print("  ❌ %s: %s: %s" % (name, type(e).__name__, e))
    print("\n%d 个用例，失败 %d" % (len(fns), bad))
    sys.exit(1 if bad else 0)
