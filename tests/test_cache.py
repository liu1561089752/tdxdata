# -*- coding: utf-8 -*-
"""`TTLCache` / `cache_key` 的**离线**单元测试。

全部用注入的假时钟"快进时间" —— **不许 `time.sleep` 真等**。

跑法：
    python tests/test_cache.py
    pytest tests/test_cache.py -q
"""
from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tdxdata._engine.cache import TTLCache, cache_key                 # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")


class FakeClock:
    """可快进的单调时钟。"""

    def __init__(self, t: float = 1000.0):
        self.t = float(t)

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> float:
        self.t += float(dt)
        return self.t


# ── TTL ───────────────────────────────────────────────────────────────────
def test_ttl_expiry_by_fake_clock():
    clk = FakeClock()
    c = TTLCache(ttl=10.0, maxsize=8, clock=clk)
    c.set("k", "v")
    assert c.get("k") == "v", "刚写入就取不到"
    clk.advance(9.0)
    assert c.get("k") == "v", "还没到期就丢了"
    clk.advance(1.5)                          # 累计 10.5s > ttl
    assert c.get("k") is None, "过期了还返回旧值"
    assert len(c) == 0, "过期条目应当被删掉"
    st = c.stats()
    assert st["expired"] == 1, "过期计数不对：%s" % st
    print("     ttl=10s：9s 命中 → 10.5s 过期（hits=%d misses=%d expired=%d）"
          % (st["hits"], st["misses"], st["expired"]))


def test_per_entry_ttl_and_non_positive_ttl():
    clk = FakeClock()
    c = TTLCache(ttl=100.0, maxsize=8, clock=clk)
    c.set("long", 1)
    c.set("short", 2, ttl=1.0)
    clk.advance(2.0)
    assert c.get("long") == 1
    assert c.get("short") is None
    # 有效 ttl ≤ 0 → 不缓存
    c.set("never", 3, ttl=0)
    assert c.get("never") is None, "ttl<=0 不该缓存"
    assert len(c) == 1, c.stats()


# ── LRU ───────────────────────────────────────────────────────────────────
def test_lru_evicts_least_recently_used():
    clk = FakeClock()
    c = TTLCache(ttl=1000.0, maxsize=3, clock=clk)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)
    assert c.get("a") == 1                    # 用一下 a → a 变成最新
    c.set("d", 4)                             # 超容量 → 淘汰最久未用的 b
    assert c.get("b") is None, "b 应被淘汰"
    assert c.get("a") == 1 and c.get("c") == 3 and c.get("d") == 4
    st = c.stats()
    assert st["evictions"] == 1, "淘汰计数不对：%s" % st
    assert st["size"] == 3
    print("     maxsize=3：插入第 4 条 → 淘汰最久未用的 b（evictions=%d）"
          % st["evictions"])


def test_set_existing_key_does_not_evict():
    clk = FakeClock()
    c = TTLCache(ttl=1000.0, maxsize=2, clock=clk)
    c.set("a", 1)
    c.set("b", 2)
    c.set("a", 11)                            # 覆盖已有键，不该触发淘汰
    assert c.stats()["evictions"] == 0, c.stats()
    assert c.get("a") == 11 and c.get("b") == 2


# ── stats ─────────────────────────────────────────────────────────────────
def test_stats_counts_add_up():
    clk = FakeClock()
    c = TTLCache(ttl=5.0, maxsize=4, clock=clk)
    st = c.stats()
    for k in ("hits", "misses", "evictions", "size", "ttl"):
        assert k in st, "stats 缺字段 %s" % k

    c.set("x", 1)
    assert c.get("x") == 1                    # hit
    assert c.get("x") == 1                    # hit
    assert c.get("nope") is None              # miss
    clk.advance(6.0)
    assert c.get("x") is None                 # 过期 → miss
    st = c.stats()
    assert st["hits"] == 2, st
    assert st["misses"] == 2, st
    assert st["expired"] == 1, st
    assert st["size"] == 0, st
    assert st["ttl"] == 5.0, st
    print("     stats: hits=%(hits)d misses=%(misses)d expired=%(expired)d "
          "evictions=%(evictions)d size=%(size)d" % st)


def test_clear_and_purge():
    clk = FakeClock()
    c = TTLCache(ttl=5.0, maxsize=8, clock=clk)
    c.set("a", 1)
    c.set("b", 2)
    clk.advance(6.0)
    c.set("c", 3)
    assert c.purge() == 2, "purge 应清掉 2 条过期条目"
    assert len(c) == 1
    c.clear()
    assert len(c) == 0
    assert c.stats()["size"] == 0


def test_maxsize_validation():
    for bad in (0, -5):
        try:
            TTLCache(maxsize=bad)
        except ValueError:
            continue
        raise AssertionError("maxsize=%r 应抛 ValueError" % (bad,))


# ── 并发 ──────────────────────────────────────────────────────────────────
def test_concurrent_set_get_no_crash():
    clk = FakeClock()
    c = TTLCache(ttl=1000.0, maxsize=64, clock=clk)
    errors: list = []

    def worker(base: int):
        try:
            for i in range(200):
                k = "k%d" % ((base + i) % 128)
                c.get(k)                  # 先读（会有 miss）再写，两个计数都会动
                c.set(k, i)
                if i % 17 == 0:
                    clk.advance(0.001)        # 顺便让时钟动起来
        except BaseException as e:            # noqa: BLE001
            errors.append("%s: %s" % (type(e).__name__, e))

    threads = [threading.Thread(target=worker, args=(n * 7,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not any(t.is_alive() for t in threads), "有线程卡死（多半是死锁）"
    assert not errors, "并发期间抛异常：%s" % errors
    st = c.stats()
    assert st["size"] <= 64, "超出 maxsize：%s" % st
    assert st["hits"] + st["misses"] == 6 * 200, "读写计数对不上：%s" % st
    assert st["misses"] > 0, "这轮本该有未命中：%s" % st
    print("     6 线程 × 200 轮 set/get → size=%d hits=%d misses=%d"
          % (st["size"], st["hits"], st["misses"]))


# ── cache_key ─────────────────────────────────────────────────────────────
def test_cache_key_ignores_dict_order():
    a = cache_key("CWServ.x", {"a": 1, "b": 2, "c": [1, 2]}, "2026-10-23")
    b = cache_key("CWServ.x", {"c": [1, 2], "b": 2, "a": 1}, "2026-10-23")
    assert a == b, "%r != %r" % (a, b)
    assert hash(a) == hash(b), "不可哈希/哈希不一致"
    assert isinstance(a, tuple)
    # 嵌套 dict 的顺序也不影响
    assert cache_key("c", {"z": {"p": 1, "q": 2}}) == cache_key(
        "c", {"z": {"q": 2, "p": 1}})
    print("     cache_key 与 dict 顺序无关：%r" % (a,))


def test_cache_key_distinguishes_real_differences():
    base = cache_key("CWServ.x", {"a": 1})
    assert base != cache_key("CWServ.y", {"a": 1}), "命令变了键却没变"
    assert base != cache_key("CWServ.x", {"a": 2}), "参数变了键却没变"
    assert base != cache_key("CWServ.x", {"a": 1}, "2026-10-24"), "日期没进键"
    assert base != cache_key("CWServ.x", {"a": 1, "b": 1}), "少参数键相同"


def test_cache_key_hashable_and_usable_in_dict():
    d = {}
    d[cache_key("c", {"b": 2, "a": 1})] = "v"
    assert d[cache_key("c", {"a": 1, "b": 2})] == "v"


def test_cache_key_str_params_equivalent():
    """JSON 字符串形态与对象形态的同一次请求 → 同一个键。"""
    s = cache_key("c", '{"b": 2, "a": 1}')
    o = cache_key("c", {"a": 1, "b": 2})
    assert s == o, "%r != %r" % (s, o)
    # 解析不了的字符串按原文入键，不抛错
    raw = cache_key("c", "not json")
    assert isinstance(raw, tuple) and raw[1] == "not json"
    # 非 JSON 可序列化对象不崩（default=str 兜底）
    assert cache_key("c", {"d": object}).__class__ is tuple


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
