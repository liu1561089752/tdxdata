# -*- coding: utf-8 -*-
"""限流防护层的**离线**单元测试（不连服务器，可随时复跑）。

跑法：
    python tests/test_pacer.py          # 不依赖 pytest
    pytest tests/test_pacer.py -q       # 装了 pytest 也行
"""
from __future__ import annotations

import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tdxdata._engine.pacer import Pacer, get_pacer, reset_pacer     # noqa: E402


def test_wait_serializes_slots():
    """两条连续 wait 的实际间隔 ≥ gap。"""
    p = Pacer(base_gap=0.2)
    t0 = time.monotonic()
    p.wait()
    p.wait()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.18, "间隔不足：%.3fs" % elapsed


def test_limited_doubles_gap():
    """被限流一次 → 间隔翻倍。"""
    p = Pacer(base_gap=2.2)
    assert p.gap == 2.2
    p.note_limited()
    assert abs(p.gap - 4.4) < 1e-6, p.gap
    p.note_limited()
    assert abs(p.gap - 8.8) < 1e-6, p.gap


def test_gap_capped_at_max():
    """间隔不会超过上限。"""
    p = Pacer(base_gap=2.2, max_gap=12.0)
    for _ in range(10):
        p.note_limited()
    assert p.gap == 12.0, p.gap


def test_gap_decays_after_success():
    """连续成功够多 → 间隔回落，但不会低于基准。"""
    p = Pacer(base_gap=2.2)
    for _ in range(3):
        p.note_limited()
    high = p.gap
    for _ in range(8 * 6):              # 6 轮衰减
        p.note_ok()
    assert p.gap < high, "没有回落：%s → %s" % (high, p.gap)
    assert p.gap >= 2.2, "低于基准了：%s" % p.gap


def test_host_cooldown():
    """被踢的服务器在一段时间内被标记为不可用。"""
    p = Pacer()
    assert p.ready("1.2.3.4") is True
    p.block("1.2.3.4", seconds=5)
    assert p.ready("1.2.3.4") is False
    assert p.ready("5.6.7.8") is True


def test_singleton_is_process_wide():
    """同一进程内多次取到同一个闸门 —— 多 Client 不会把频率叠加。"""
    reset_pacer()
    a = get_pacer()
    b = get_pacer()
    assert a is b, "闸门不是单例，多 Client 会放大请求频率"
    reset_pacer()
    c = get_pacer()
    assert c is not a, "reset 无效"


def test_stats_shape():
    p = Pacer()
    p.note_limited()
    s = p.stats()
    for k in ("base", "current", "max", "limited_events", "cooling_hosts"):
        assert k in s, "stats 缺字段 %s" % k
    assert s["limited_events"] == 1


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
