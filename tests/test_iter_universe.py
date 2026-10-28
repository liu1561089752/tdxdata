# -*- coding: utf-8 -*-
"""翻页取全（阶段 3）：`_core.paginate` / `iter_pages` / `get_universe` / `iter_universe`。

这个文件存在的理由：**"少给了数据却不报错"是本库的红线**。
旧 `get_universe` 默认 `max_pages=20 × page_size=50` = 最多 **1000** 只，
而 `market=6` 全量约 **5577** 只 —— 1000 只被**静默当成全量**交给了调用方。
方向错在"少给 + 不告知"，比"报错"危险得多（下游会拿它当"全市场"做统计）。

现在三条硬要求：
1. 封顶必须**写在 `meta["truncated"]` 里**，不许只留在参数里；
2. 要全量有 `iter_universe()`（**生成器**，可边取边停，不必先等 158 秒攒完）；
3. 退出条件（空页 / 无进展 / 不足一页 / 达上限 / 首页就空）**逐条**可断言，
   且顺序正确：**"最后一页正好是满页"不许被误报成"因上限而截断"**。
"""
from __future__ import annotations

import pytest

import tdxdata
from tdxdata import _core, errors

_CLS = getattr(tdxdata, "Client")


class FakeQuote:
    """只为翻页测试用的极简条目（`key` 取 `(market, code)`）。"""

    __slots__ = ("market", "code")

    def __init__(self, code, market=0):
        self.market = market
        self.code = code

    def __repr__(self):
        return "<%s.%s>" % (self.market, self.code)


def _pages(*sizes, prefix="C", market=0):
    """造若干页（每页 `size` 条，代码递增且全局唯一）。"""
    out = []
    n = 0
    for size in sizes:
        page = []
        for _ in range(size):
            n += 1
            page.append(FakeQuote("%s%05d" % (prefix, n), market))
        out.append(page)
    return out


def _fetcher(pages, *, counter=None):
    """把"页列表"包成 `fetch_page(offset)`：offset 是**行偏移**，按累计行数换算。"""
    starts = []
    acc = 0
    for p in pages:
        starts.append(acc)
        acc += len(p)

    def fetch(offset):
        if counter is not None:
            counter.append(offset)
        if offset in starts:
            return list(pages[starts.index(offset)])
        raise errors.EmptyResult("到底了")            # 超出末页 = 服务端 0 行

    return fetch


def _key(q):
    return (q.market, q.code)


# ══════════════════════════════════════════════════════════════════════════
# 一、`paginate`：五个退出条件逐条
# ══════════════════════════════════════════════════════════════════════════
def test_natural_end_on_short_page():
    """不足一页 ⇒ 末页（不许多打一次请求去确认）。"""
    counter: list = []
    items, st = _core.paginate(_fetcher(_pages(80, 20), counter=counter),
                               page_size=80, key=_key)
    assert len(items) == 100
    assert st == {"pages": 2, "rows": 100, "truncated": False}
    assert counter == [0, 80], "只发 2 次，别为确认末页再多打一轮"


def test_cap_marks_truncated():
    counter: list = []
    items, st = _core.paginate(_fetcher(_pages(80, 80, 80, 80), counter=counter),
                               page_size=80, max_pages=2, key=_key)
    assert len(items) == 160
    assert st == {"pages": 2, "rows": 160, "truncated": True}
    assert counter == [0, 80], "达上限就停，不许再发"


def test_full_last_page_is_not_misreported_as_truncated():
    """**顺序判据**：最后一页正好是满页时，必须靠"下一条空页"收尾，而不是"因上限截断"。

    这是最容易写反的一处：先判 `pages >= max_pages` 再判"不足一页"，
    就会把"恰好取完"报成"截断了"（于是调用方被劝去多打 70 页）。
    """
    items, st = _core.paginate(_fetcher(_pages(80, 80)), page_size=80,
                               max_pages=10, key=_key)
    assert len(items) == 160
    assert st["truncated"] is False, "取完了就不是截断"
    assert st["pages"] == 2


def test_first_page_empty_raises_not_silently_empty():
    """**首页就空 = 参数/列数配错**，必须上抛，绝不能静默当"到底了"。"""
    def fetch(offset):
        raise errors.EmptyResult("服务端 0 行")

    with pytest.raises(errors.EmptyResult):
        _core.paginate(fetch, page_size=80, max_pages=5, key=_key)


def test_later_empty_page_is_normal_end():
    """翻到后面才空 = 正常到底（不抛）。"""
    def fetch(offset):
        if offset == 0:
            return _pages(80)[0]
        raise errors.EmptyResult("到底了")

    items, st = _core.paginate(fetch, page_size=80, key=_key)
    assert len(items) == 80 and st["truncated"] is False


def test_no_progress_page_stops():
    """服务端没按 offset 挪窗口（本页全是见过的）→ 停，且**不**算截断。"""
    def fetch(offset):
        return _pages(80)[0]                     # 每次都给同一页

    items, st = _core.paginate(fetch, page_size=80, max_pages=10, key=_key)
    assert len(items) == 80, "重复的条目不许进结果"
    assert st["truncated"] is False, "无进展是「到底」，不是「截断」"
    assert st["pages"] == 2, "第二页发现无新条目后立刻停"


def test_dedup_by_key():
    page1 = _pages(3, prefix="A")
    page2 = [FakeQuote("A00002"), FakeQuote("B00001")]     # 与第一页有重叠
    items, _st = _core.paginate(_fetcher([page1[0], page2]), page_size=3, key=_key)
    codes = [q.code for q in items]
    assert codes == ["A00001", "A00002", "A00003", "B00001"]


@pytest.mark.parametrize("bad", [0, -1])
def test_bad_max_pages_raises(bad):
    with pytest.raises(ValueError):
        _core.paginate(_fetcher(_pages(1)), page_size=10, max_pages=bad, key=_key)


@pytest.mark.parametrize("bad", [0, -5])
def test_bad_page_size_raises(bad):
    with pytest.raises(ValueError):
        _core.paginate(_fetcher(_pages(1)), page_size=bad, key=_key)


def test_max_pages_none_means_go_to_the_end():
    items, st = _core.paginate(_fetcher(_pages(80, 80, 80)), page_size=80,
                               max_pages=None, key=_key)
    assert len(items) == 240 and st["truncated"] is False


# ══════════════════════════════════════════════════════════════════════════
# 二、`iter_pages`：真·逐页（懒）
# ══════════════════════════════════════════════════════════════════════════
def test_iter_pages_is_lazy():
    """取第一条时**只发了第一页**的请求（证明不是"先攒完再吐"）。"""
    counter: list = []
    gen = _core.iter_pages(_fetcher(_pages(80, 80, 80), counter=counter),
                           page_size=80, key=_key)
    assert counter == [], "还没 next() 就不许发请求"
    first = next(gen)
    assert first is not None
    assert counter == [0], "只翻了第一页"
    gen.close()


def test_iter_pages_stats_written_on_exhaustion():
    stats: dict = {}
    list(_core.iter_pages(_fetcher(_pages(80, 20)), page_size=80,
                          key=_key, stats=stats))
    assert stats == {"pages": 2, "rows": 100, "truncated": False}


def test_iter_pages_stats_written_even_if_closed_early():
    """提前 `break`/`close()` 也要把已经发生的页数写进去（否则没法观测）。"""
    stats: dict = {}
    gen = _core.iter_pages(_fetcher(_pages(80, 80, 80)), page_size=80,
                           max_pages=None, key=_key, stats=stats)
    next(gen)
    gen.close()
    assert stats["pages"] == 1, "已经发出去的那一页必须记账"


def test_estimate_cost():
    assert _core.estimate_cost(3) == 6.6
    assert _core.estimate_cost(20) == 44.0
    assert _core.estimate_cost(70) == 154.0
    assert _core.estimate_cost(3, gap=1.0) == 3.0
    assert _core.estimate_cost(0) == 0.0


# ══════════════════════════════════════════════════════════════════════════
# 三、Client 层：封顶必须显式 + `iter_universe` 是生成器
# ══════════════════════════════════════════════════════════════════════════
def _bare_client():
    from tdxdata._engine import datacenter as dc

    c = _CLS.__new__(_CLS)
    c._dc_mod = dc
    c._probe_allowed = False
    c._clock_probe = False
    c._clock = None
    c._last_call = {}
    c._in_cal = False
    c._strict_symbols = False

    class _Pacer:
        gap = 2.2
        limited_events = 0

    c._pacer = _Pacer()
    return c


def _stub_page(c, *, total=240, page_size=80):
    """把 `get_market_page` 换成本地假实现（翻页逻辑才是被测对象）。"""
    served = {"n": 0}

    def fake(market=6, offset=0, page_size=page_size, cols=None):
        served["n"] += 1
        if offset >= total:
            raise errors.EmptyResult("到底了")
        size = min(int(page_size), total - offset)
        return [FakeQuote("%06d" % (offset + i + 1), market)
                for i in range(size)]

    c.get_market_page = fake
    return served


def test_iter_universe_is_a_generator_not_a_list():
    """`iter_universe` 必须是**生成器**：`list(...)` 之外还能 `next()` / `break`。"""
    import inspect

    c = _bare_client()
    _stub_page(c, total=240, page_size=80)
    gen = c.iter_universe(market=6, page_size=80, max_pages=3)
    assert inspect.isgenerator(gen), "必须是生成器（否则没法边取边停）"
    items = list(gen)
    assert len(items) == 240, "3 页 × 80 = 240"
    assert items[0].code != items[-1].code


def test_iter_universe_early_break_stops_paging():
    """取够就停：`break` 之后不该继续翻页（这正是生成器的意义）。"""
    c = _bare_client()
    served = _stub_page(c, total=800, page_size=80)
    got = 0
    for _q in c.iter_universe(market=6, page_size=80):
        got += 1
        if got == 10:
            break
    assert got == 10
    assert served["n"] == 1, "只翻了第一页（10 条都在第一页里）"


def test_iter_universe_dedups_across_pages():
    c = _bare_client()

    def fake(market=6, offset=0, page_size=80, cols=None):
        if offset == 0:
            return [FakeQuote("000001", 6), FakeQuote("000002", 6)]
        if offset == 2:
            return [FakeQuote("000002", 6), FakeQuote("000003", 6)]
        raise errors.EmptyResult("到底了")

    c.get_market_page = fake
    codes = [q.code for q in c.iter_universe(page_size=2)]
    assert codes == ["000001", "000002", "000003"]


def test_iter_universe_default_is_unbounded():
    """默认 `max_pages=None` = 翻到底（与 `get_universe` 的 20 页默认**不同**）。"""
    import inspect

    sig = inspect.signature(_CLS.iter_universe)
    assert sig.parameters["max_pages"].default is None
    assert sig.parameters["page_size"].default == 80
    assert sig.parameters["market"].default == 6


def test_get_universe_reports_truncation_in_meta():
    """**核心**：达上限 → `meta["truncated"] is True` + 成本 + 出路说明。"""
    c = _bare_client()
    _stub_page(c, total=800, page_size=50)
    out = c.get_universe(market=6, max_pages=2, page_size=50)
    assert len(out) == 100
    assert out.provenance.extra["truncated"] is True
    assert out.provenance.extra["pages"] == 2
    assert out.provenance.extra["rows"] == 100
    assert out.provenance.extra["estimated_cost_seconds"] == 4.4
    note = "；".join(out.provenance.notes)
    assert "5577" in note and "iter_universe" in note, "必须给出全量与出路"
    assert "max_pages" in note


def test_get_universe_not_truncated_when_finished():
    c = _bare_client()
    _stub_page(c, total=30, page_size=50)
    out = c.get_universe(market=6, max_pages=20, page_size=50)
    assert len(out) == 30
    assert out.provenance.extra["truncated"] is False
    assert out.provenance.extra["pages"] == 1
    assert "末页" in "；".join(out.provenance.notes)


def test_get_universe_signature_is_unchanged():
    """兼容性：签名默认值不许动（老调用方还按 20 页 / 50 条在跑）。"""
    import inspect

    sig = inspect.signature(_CLS.get_universe)
    assert sig.parameters["market"].default == 6
    assert sig.parameters["max_pages"].default == 20
    assert sig.parameters["page_size"].default == 50


def test_get_universe_keeps_semantic_meta():
    """阶段 1/1.5 的语义层必须还在（ARCH-002：一等字段 + extra 旁证）。"""
    c = _bare_client()
    _stub_page(c, total=30, page_size=50)
    out = c.get_universe()
    prov = out.provenance
    assert prov is not None
    for f in ("as_of", "status", "fetched_at", "elapsed", "cmd"):
        assert hasattr(prov, f), "缺 %s" % f
    for k in ("pages", "truncated", "rows", "estimated_cost_seconds"):
        assert k in prov.extra, "缺 %s" % k
    assert prov.notes, "翻页口径必须写在 notes 里"


# ══════════════════════════════════════════════════════════════════════════
# 四、市场域表（`_core.MARKET_DOMAINS`）—— 语义与证据一起钉住
# ══════════════════════════════════════════════════════════════════════════
def test_market_domains_covers_the_registered_set():
    for k in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14):
        assert k in _core.MARKET_DOMAINS, "market=%d 没登记" % k
    assert _core.MARKET_DOMAINS[0].startswith("沪A"), "0 不是「科创板」"
    assert _core.MARKET_DOMAINS[2].startswith("深A"), "2 不是「创业板」"
    assert "不等" in _core.MARKET_DOMAINS[8], "8 与 0 不是同一个域"
    assert "北交所" in _core.MARKET_DOMAINS[12]


def test_market_domains_unverified_ones_are_labelled():
    """没复验过的必须**如实标注**（`hq_pb` 里那几条要能看出来是沿用的）。"""
    from tdxdata._engine import hq_pb

    for k in (3, 4, 5, 7):
        text = hq_pb.MARKET_CODES[k]
        assert isinstance(text, str) and text
    assert set(hq_pb.MARKET_CODES) >= set(_core.MARKET_DOMAINS), \
        "hq_pb.MARKET_CODES 必须覆盖 _core 的全部已登记域"


def test_domain_proof_is_stated():
    """能力断言必须写在代码里（`6 = 0 ∪ 2 ∪ 12`，**不是** 0∪1∪2）。"""
    proof = _core.DOMAIN_PROOF
    assert "set(6)" in proof and "set(0)" in proof and "set(12)" in proof
    assert 6 in _core.COMPOSITE_MARKETS


def test_paginate_does_not_reach_into_client():
    """分层：`_core` 只依赖 errors/symbols，不许反向 import `client`（会成环）。"""
    import inspect

    src = inspect.getsource(_core)
    assert "import client" not in src and "from .client" not in src
