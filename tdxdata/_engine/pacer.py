# -*- coding: utf-8 -*-
"""限流防护（库内私有）：进程级匀速闸门 + 自适应退避 + 服务器冷却。

为什么必须是**进程级**而不是"每个 Client 各自计时"
--------------------------------------------------
7615 的限流是**服务端按来源**判定的，跟你有几个 Client 对象无关。
如果每个 Client 各算各的 `gap`，那开 3 个 Client 就等于把频率放大 3 倍 —— 必被踢。
所以闸门做成模块级单例，同进程所有 Client 共享同一个"下一次可发送时刻"。

三层防护
--------
1. **匀速闸门**（`wait()`）：全局槽位预约，保证任意两条命令间隔 ≥ `cur`
2. **自适应退避**（`note_limited()` / `note_ok()`）：
   被踢就间隔翻倍（2.2 → 4.4 → 8.8 → 12 上限）；
   连续成功 8 次后缓慢回落（×0.75），不会一直卡在 12 秒
3. **服务器冷却**（`block()` / `ready()`）：
   刚踢过我们的服务器，30 秒内不再连，避免"换个头继续撞同一堵墙"

per-host 槽位（`host=` 参数；阶段 5 新增，**默认不用**）
--------------------------------------------------------
`wait(host="x")` / `note_limited(host="x")` 让这台主机拥有**自己的一份槽位**，
于是钉在不同主机上的两条 lane 互不排队 —— 这是"多主机分摊"能提速的唯一机制。

⚠️ **为什么默认不用它**（`host=None` 时行为与本文件改造前逐字节一致）：
    库内部对"配额按什么算"至今**没有定论**，两种说法同时存在于源码里：
      · `_engine/datacenter.py` 的注释说「按客户端 IP × 服务器」→ 多主机可以放大；
      · 本文件 / `client.py` / `aio.py` 的历史结论说「按来源（IP）」→ 放大必被踢。
    在实测说清之前，**默认路径必须只用一个槽位**（等价于"按来源"这个保守假设），
    否则等于替所有用户私自放大请求频率。所以：
      · `Client(concurrency=1)`（默认）→ 从不传 `host=`，只有全局槽位，与旧版一致；
      · `Client(concurrency>1)` 才由调用方显式传 `host=`，并在 `Client` 的文档里
        写明"自担可能触发按来源 IP 的限流"。
    实测口径与原始输出见 `reports/refactor/probe/phase5/`（并发是否真带来收益以实测为准）。

⚠️ `note_limited(host="x")` 除了推后 x 的槽位，**还会推后全局槽位**（保守）：
    被踢说明"整体都该慢下来"，不能因为"踢的是 x"就让别的 lane 继续抢。
"""
from __future__ import annotations

import threading
import time

__all__ = ["Pacer", "get_pacer", "reset_pacer"]

BASE_GAP = 2.2          # 实测安全下限（低于 2 秒明显更容易被踢）
MAX_GAP = 12.0          # 退避上限
DECAY_AFTER = 8         # 连续成功多少次开始回落
COOLDOWN = 30.0         # 服务器被踢后的冷却秒数


class Pacer:
    """全局匀速闸门。线程安全。

    槽位有两种粒度（阶段 5）：
      · **全局槽位** `_next_at` —— `host is None` 时用，等价于旧版的唯一槽位；
      · **per-host 槽位** `_next_at_by_host[host]` —— 只在显式给 `host` 时用。
    两种槽位共用同一个 `cur`（退避间隔是进程级的，不按主机分叉）。
    """

    def __init__(self, base_gap: float = BASE_GAP, max_gap: float = MAX_GAP):
        self._lock = threading.Lock()
        self.base = float(base_gap)
        self.max = float(max_gap)
        self.cur = float(base_gap)
        self._next_at = 0.0             # 下一次允许发送的单调时刻（全局槽位）
        #: per-host 槽位。**只**在调用方显式给出 `host` 时才建条目 ——
        #: 所以默认路径（`host=None`）连这个 dict 都不会被碰，`hosts_tracked` 恒为 0。
        self._next_at_by_host: dict[str, float] = {}
        self._ok_streak = 0
        self.limited_events = 0         # 累计被限流次数（供上层判断）
        self._cooldown: dict[str, float] = {}

    # ── 匀速闸门 ──────────────────────────────────────────────────────────
    def wait(self, host: str | None = None) -> float:
        """预约一个发送槽位并 sleep 到它。返回实际等待秒数。

        `host=None`（默认）→ 只用全局槽位，**行为与改造前逐字节一致**。
        `host="x"`        → 只用 x 的槽位（首次出现即建条目，起始 0.0 = 立刻可发）。
        """
        with self._lock:
            now = time.monotonic()
            if host is None:
                slot = max(now, self._next_at)
                self._next_at = slot + self.cur
            else:
                nxt = self._next_at_by_host.get(host, 0.0)
                slot = max(now, nxt)
                self._next_at_by_host[host] = slot + self.cur
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        return max(0.0, delay)

    # ── 反馈 ──────────────────────────────────────────────────────────────
    def note_ok(self) -> None:
        """一条命令成功：连续成功够多就把间隔慢慢收回基准。"""
        with self._lock:
            self._ok_streak += 1
            if self._ok_streak >= DECAY_AFTER and self.cur > self.base:
                self.cur = max(self.base, self.cur * 0.75)
                self._ok_streak = 0

    def note_limited(self, host: str | None = None) -> float:
        """被限流/被踢：间隔翻倍（不超过上限）。返回新间隔。

        `host=None`（默认）→ 只推后全局槽位，**行为与改造前一致**。
        `host="x"`        → **既**推后 x 的槽位，**也**推后全局槽位。
        为什么被踢了还要推全局：被踢说明整体该慢下来；只推 x 会让另一条 lane
        继续按旧节奏撞同一堵墙（"换个头继续撞"就是这么发生的）。
        """
        with self._lock:
            self.limited_events += 1
            self._ok_streak = 0
            self.cur = min(self.max, max(self.cur * 2.0, 1.0))
            # 把下一个槽位往后推，避免立刻再撞
            now = time.monotonic()
            self._next_at = max(self._next_at, now + self.cur)
            if host is not None:
                self._next_at_by_host[host] = max(
                    self._next_at_by_host.get(host, 0.0), now + self.cur)
            return self.cur

    # ── 服务器冷却 ────────────────────────────────────────────────────────
    def block(self, host: str, seconds: float = COOLDOWN) -> None:
        with self._lock:
            self._cooldown[host] = time.monotonic() + seconds

    def ready(self, host: str) -> bool:
        with self._lock:
            until = self._cooldown.get(host)
            return until is None or time.monotonic() >= until

    # ── 观测 ──────────────────────────────────────────────────────────────
    @property
    def gap(self) -> float:
        with self._lock:
            return self.cur

    def stats(self) -> dict:
        """限流状态快照。

        ⚠️ `base` / `current` / `max` / `limited_events` / `cooling_hosts`
        这五个键的**键名与含义自阶段 0 起就没变**（`tests/test_pacer.py`、
        `README` 的 `rate_stats()` 表格、`golden_runner._NoPacer` 都依赖它们）。
        阶段 5 只**新增** `hosts_tracked`，一个旧键都没动。
        """
        with self._lock:
            return {
                "base": self.base,
                "current": round(self.cur, 2),
                "max": self.max,
                "limited_events": self.limited_events,
                "cooling_hosts": sum(1 for h, t in self._cooldown.items()
                                     if t > time.monotonic()),
                #: 已建 per-host 槽位的主机数。默认路径（`host=None`）恒为 0 ——
                #: 这个数一旦 >0，说明有人显式开了多主机摊销（`concurrency>1`）。
                "hosts_tracked": len(self._next_at_by_host),
            }


_PACER: Pacer | None = None
_PACER_LOCK = threading.Lock()


def get_pacer(base_gap: float = BASE_GAP) -> Pacer:
    """取进程级单例闸门（首次调用时用 `base_gap` 初始化）。"""
    global _PACER
    with _PACER_LOCK:
        if _PACER is None:
            _PACER = Pacer(base_gap=base_gap)
        return _PACER


def reset_pacer() -> None:
    """重置单例（测试用）。"""
    global _PACER
    with _PACER_LOCK:
        _PACER = None
