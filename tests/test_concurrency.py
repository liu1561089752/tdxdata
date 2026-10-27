# -*- coding: utf-8 -*-
"""阶段 5「真并发」的**离线**单元测试（不连服务器、不打任何 socket）。

这个文件要钉住的四件事
--------------------
1. **默认路径没变**：`wait()` 不传 `host` 时的槽位序列与改造前完全一致；
   `concurrency=1` 时**一个线程池都不许建**。
2. **per-host 桶真的独立**：`wait(host="A")` 与 `wait(host="B")` 互不排队；
   `note_limited(host="A")` 推后 A 的桶**和**全局桶，但**不碰** B 的桶。
3. **上限 = 服务器台数**：`1..4` 正常（4 条 lane、主机各不相同）；
   `>4` / `<1` / 非整数 → `ValueError`（**不截断** —— 静默降级是不许的）。
4. **并发结果按输入原序**：30 个一级行业码并发取明细，**完成顺序被刻意打乱**，
   拼装结果仍与串行**逐项相同**（这是"并发不许乱序"的唯一真凭据）。

为什么全程打桩
-------------
真实串行跑一次 `get_market_stat('industry')` 是 31 次调用 × 2.2 s ≈ 68 s，
一次限流事件还要 +28.15 s。**测试绝不许为了证明并发去加压**：
这里把最底层取数（`Session.ask` 那一层）、匀速闸门、时钟全部换成假的，
断言的是**逻辑**，不是网速。

⚠️ 本文件按项目纪律**不写类名调用**（一律 `getattr` 取类），
也**不许**出现任何真实网络：
`test_this_module_never_touches_the_network` 会把 socket 也钉死。
"""
from __future__ import annotations

import json
import socket
import time

import pytest

import tdxdata
import tdxdata.client as client_mod
from tdxdata._engine import pacer as pacer_mod
from tdxdata._engine import session as session_mod
from tdxdata.sections import flow as flow_mod

#: 红线：测试里不写类名字面量（与 `test_aio_parity.py` 同一套路）。
_CLS = getattr(tdxdata, "Client")
_SESSION_CLS = getattr(session_mod, "Session")
_HOSTS = list(session_mod.HOSTS)


# ══════════════════════════════════════════════════════════════════════════
# 一、假时钟：把 `pacer` 里的 time 换掉，槽位序列才可断言
# ══════════════════════════════════════════════════════════════════════════
class _FakeTime:
    """只实现 `pacer` 用到的两个函数：`monotonic()` / `sleep()`。

    `sleep` 直接把"虚拟时钟"往前推 —— 于是"等了多久"变成可精确断言的数，
    而不是去比真实墙钟（那既慢又会抖）。
    """

    def __init__(self, start: float = 1000.0):
        self.now = float(start)
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, sec) -> None:
        sec = float(sec)
        self.slept.append(sec)
        if sec > 0:
            self.now += sec


def _new_pacer(monkeypatch, base_gap: float = 2.2):
    """造一个闸门，并把**它的** `time` 换成假时钟（不影响别的模块）。"""
    clk = _FakeTime()
    monkeypatch.setattr(pacer_mod, "time", clk)
    return clk, pacer_mod.Pacer(base_gap=base_gap)


# ══════════════════════════════════════════════════════════════════════════
# 二、Pacer：默认路径不变 / per-host 桶 / 限流推后
# ══════════════════════════════════════════════════════════════════════════
def test_wait_without_host_keeps_the_old_slot_sequence(monkeypatch):
    """`wait()`（host=None）的槽位序列必须是 2.2 / 4.4 / 6.6 / 8.8 —— 与改造前一致。

    这是"默认关闭、行为不变"的技术保证：`host=None` 时**只用**全局槽位，
    连 per-host 那个 dict 都不该被碰。
    """
    clk, p = _new_pacer(monkeypatch)
    t0 = clk.now

    slots, delays = [], []
    for _ in range(4):
        delays.append(round(p.wait(), 6))                # ← 与旧版完全相同的调用形态
        slots.append(round(p._next_at - t0, 6))

    assert slots == [2.2, 4.4, 6.6, 8.8], "全局槽位序列变了：%s" % (slots,)
    assert delays == [0.0, 2.2, 2.2, 2.2], "每次实际等待变了：%s" % (delays,)
    assert p._next_at_by_host == {}, "默认路径不许建 per-host 桶"
    assert p.stats()["hosts_tracked"] == 0

    # 退避/回落的老语义一个都没动
    assert p.note_limited() == 4.4
    assert p.gap == 4.4
    assert p.limited_events == 1
    assert set(p.stats()) >= {"base", "current", "max",
                              "limited_events", "cooling_hosts"}


def test_host_buckets_do_not_queue_each_other(monkeypatch):
    """A、B 两条桶各自从 2.2 起 —— 共用槽位的话 B 会白等 2.2 秒。

    这就是"多主机能否分摊"的全部机制：**钉在不同主机的 lane 互不排队**。
    """
    clk, p = _new_pacer(monkeypatch)
    t0 = clk.now

    assert p.wait(host="A") == 0.0
    assert p.wait(host="B") == 0.0, "B 被 A 排上了队（per-host 桶没生效）"
    assert p._next_at == 0.0, "per-host 的 wait 不该碰全局槽位"
    assert round(p._next_at_by_host["A"] - t0, 6) == 2.2
    assert round(p._next_at_by_host["B"] - t0, 6) == 2.2
    assert p.stats()["hosts_tracked"] == 2

    # A 的第二条要按 A 自己的 2.2 排；B 的槽位早在过去 → 立刻可发（不受 A 连累）
    assert round(p.wait(host="A"), 6) == 2.2
    assert p.wait(host="B") == 0.0


def test_note_limited_host_pushes_host_and_global_not_siblings(monkeypatch):
    """被踢一次：A 的桶被推后、**全局桶也被推后**、B 的桶不受影响。

    为什么全局也要推：被踢说明"整体该慢下来"；只推 A 会让 B 继续按旧节奏
    撞同一堵墙 —— 那正是"换个头继续撞"的成因。
    """
    clk, p = _new_pacer(monkeypatch)
    t0 = clk.now
    p.wait(host="A")
    p.wait(host="B")
    assert p._next_at == 0.0

    p.note_limited(host="A")          # cur 2.2 → 4.4

    assert p.limited_events == 1, "limited_events 的语义必须不变（全局计数 +1）"
    assert p.gap == 4.4
    assert round(p._next_at_by_host["A"] - t0, 6) == 4.4, "A 的桶没被推后"
    assert round(p._next_at - t0, 6) == 4.4, "全局桶没被推后（保守口径丢了）"
    assert round(p._next_at_by_host["B"] - t0, 6) == 2.2, "B 的桶被连累了"

    # 行为面（另一只闸门，避免上一只的睡眠把时钟推乱）：
    clk2, p2 = _new_pacer(monkeypatch)
    p2.wait(host="A")
    p2.note_limited(host="A")
    assert round(p2.wait(), 6) == 4.4, "全局槽位没被 note_limited(host=) 推后"

    # 对照：host=None 的 note_limited 仍是老行为（2.2 → 4.4，只推全局）
    clk3, p3 = _new_pacer(monkeypatch)
    p3.wait()
    assert p3.note_limited() == 4.4
    assert p3._next_at_by_host == {}, "默认路径的 note_limited 不许建 per-host 桶"


# ══════════════════════════════════════════════════════════════════════════
# 三、并发度参数：默认不起线程池 / 上限 = 服务器台数
# ══════════════════════════════════════════════════════════════════════════
def test_concurrency_one_never_builds_a_thread_pool(monkeypatch):
    """`concurrency=1`（默认）：线程池构造器**一次都不许被调用**。

    这条是"默认路径不增加任何服务器压力"的机器判据：
    不是"起了 1 个 worker"，而是**根本没有线程池这回事**。
    """
    made: list = []

    def _spy(*a, **kw):
        made.append((a, kw))
        raise AssertionError("concurrency=1 不许建线程池（默认路径必须是纯串行）")

    monkeypatch.setattr(client_mod, "ThreadPoolExecutor", _spy)

    c = _CLS(clock_probe=False)
    try:
        assert c._pool is None
        assert c._lanes == [], "concurrency=1 不该造任何 lane"
        assert c._concurrency == 1 and c._concurrency_eff == 1

        # 本库唯一的并发入口，在默认配置下也必须退化成纯串行（按原序、无队列）
        assert c._map_independent(lambda x: x * 2, [1, 2, 3]) == [2, 4, 6]
        assert made == [], "默认路径居然建了线程池：%r" % (made,)

        st = c.rate_stats()
        assert st["concurrency"] == 1
        assert st["concurrency_effective"] == 1
        assert st["lanes"] == []
        assert st["hosts_tracked"] == 0
        # 旧键一个都不能丢（README / test_pacer 依赖）
        for k in ("base", "current", "max", "limited_events", "cooling_hosts"):
            assert k in st, "rate_stats 丢了旧键 %s" % k
    finally:
        c.close()


def test_concurrency_upper_bound_is_the_host_count():
    """上限 = **服务器台数**：`4` 正常（4 条 lane、主机各不相同）；`>4` 一律 `ValueError`。

    为什么不是"截断到 4"：第 5 条 lane 只能和别的 lane 共用同一个 per-host 桶
    → 它**不会更快**，只会多占一条连接。截断等于"用户以为拿到了 99 的语义、
    实际拿到 4"——静默降级，与「宁可拒绝」的纪律直接冲突。
    """
    hosts = len(_HOSTS)
    assert hosts >= 2, "本用例假定至少两台服务器"

    # ① 恰好等于服务器台数 → 正常，且每条 lane 的 host **各不相同**
    top = _CLS(clock_probe=False, concurrency=hosts)
    try:
        assert top._concurrency == hosts
        assert top._concurrency_eff == hosts
        assert len(top._lanes) == hosts
        assert {lane.host for lane in top._lanes} == set(_HOSTS), \
            "lane 的主机没有互不相同（per-host 桶就白做了）"
        st = top.rate_stats()
        assert st["concurrency"] == hosts
        assert st["concurrency_effective"] == hosts
        assert st["lanes"] == _HOSTS, "第 i 条 lane 必须钉 HOSTS[i]"
        assert top._pool is None, "只是构造 Client 不该预先起线程池"
    finally:
        top.close()

    # ② 超一台就拒绝（不截断、不警告、不静默）
    with pytest.raises(ValueError):
        _CLS(clock_probe=False, concurrency=hosts + 1)
    # 99 落进同一条规则
    with pytest.raises(ValueError):
        _CLS(clock_probe=False, concurrency=99)
    # ③ 下界与"非整数"
    for bad in (0, -1, 1.5):
        with pytest.raises(ValueError):
            _CLS(clock_probe=False, concurrency=bad)


def test_lanes_are_pinned_through_session_pin():
    """`Session` 的 `pin=True`：钉死主机后 `switch_host()` 不许偷偷换机。

    "第 i 条 lane 钉 HOSTS[i]"这句话只有在 Session 层面被强制时才成立 ——
    否则一次失败就悄悄跑到别的机器上，per-host 槽位和实际流量就对不上了。
    """
    sess = _SESSION_CLS(_HOSTS[1], pin=True)
    assert sess.pin is True
    assert sess.host == _HOSTS[1]
    assert sess.alive is False                    # 不连网：只是构造

    plain = _SESSION_CLS(_HOSTS[1])
    assert plain.pin is False, "默认必须是旧行为（可轮换）"
    plain.close()


# ══════════════════════════════════════════════════════════════════════════
# 四、并发跑 true 的"独立多次调用"：结果必须按原序
# ══════════════════════════════════════════════════════════════════════════
#: 30 个一级行业码（形状必须是 `^X\d{2}$`，否则会被 flow 过滤掉）
_CODES = ["X%02d" % n for n in range(10, 40)]
#: 刻意让这两个码返回"有列无行"→ 必须被跳过并记进 `skipped_codes`
_EMPTY_CODES = {"X15", "X27"}


def _payload(names, rows) -> bytes:
    """造一份和真实回包同形状的载荷（`parse()` 读 `ResultSets`）。"""
    return json.dumps(
        {"ResultSets": [{"ColName": list(names),
                         "Content": [list(r) for r in rows]}]},
        ensure_ascii=False).encode("gbk")


class _FakeSess:
    """冒充 `Session`：`ask` 只按参数合成回包，**完成顺序被刻意打乱**。

    打乱手法：第 i 个码睡 `(N-i)` 毫秒 —— 越靠前的越慢，
    于是"完成顺序"和"提交顺序"必然不同。如果拼装是"谁先回来谁在前"，
    这个用例会立刻红。
    """

    alive = True

    def __init__(self, sink: list, delay_ms: float = 1.0):
        self.host = "fake-host"
        self.sink = sink
        self.delay_ms = delay_ms
        self.calls: list = []

    def ask(self, ctl, cmd, args, idle=4.0, tail=0.35):
        body = json.loads(args.decode())
        mode, code = body["Params"][0], body["Params"][1]
        self.calls.append((mode, code))
        if mode == 1:                                  # 模式 1：一级行业目录
            rows = [[c] for c in _CODES]
            return _payload(["N001"], rows)
        # 模式 2：一级行业下的明细（8 列，见 flow._HYFX_LV2_MAP）
        time.sleep(max(0.0, (len(_CODES) - _CODES.index(code)) * self.delay_ms / 1000.0))
        self.sink.append(code)                          # 记"完成顺序"
        if code in _EMPTY_CODES:
            return _payload(["N001", "N002", "N003", "N004",
                             "N005", "N006", "N007", "N008"], [])
        i = _CODES.index(code)
        return _payload(
            ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008"],
            [["IND-" + code, code + "01", "881%03d" % i, "Ⅱ",
              "20261019", "%.4f" % (10.0 + i), str(100 + i), str(5 + i)]])

    def close(self) -> None:
        pass


class _FakePacer:
    """假闸门：不起线程、不睡，只**记账**（并发到底有没有按主机分包，看这里）。"""

    def __init__(self):
        self.waits: list = []
        self.limited_events = 0
        self.gap = 2.2
        self.base = 2.2
        self.max = 12.0

    def wait(self, host=None):
        self.waits.append(host)
        return 0.0

    def note_ok(self):
        pass

    def note_limited(self, host=None):
        self.limited_events += 1
        return self.gap

    def block(self, host, seconds=0.0):
        pass

    def ready(self, host):
        return True

    def stats(self):
        return {"base": self.base, "current": self.gap, "max": self.max,
                "limited_events": self.limited_events, "cooling_hosts": 0,
                "hosts_tracked": len({h for h in self.waits if h})}


def _run_industry(concurrency: int):
    """用真 Client + 打桩的会话/闸门跑一次 `get_market_stat('industry')`。"""
    done: list = []
    pacer, sess = _FakePacer(), _FakeSess(done)
    c = _CLS(clock_probe=False, concurrency=concurrency)
    c._pacer = pacer
    c._ensure = lambda: sess              # 最底层取数（Session.ask 那一层）被打桩
    try:
        table = flow_mod.get_market_stat(c, "industry")
    finally:
        c.close()
    return table, pacer, sess, done


def _fingerprint(table):
    """逐行指纹：并发与串行**必须逐项相同**（含 `raw` 的键集）。"""
    return [(r["industry_code"], r["industry_name"], r["board_code"],
             r["level"], r["date"], r["change_pct"], r["up_count"],
             r["down_count"], tuple(sorted(r["raw"])))
            for r in table.rows]


def test_concurrent_industry_keeps_input_order():
    """30 个一级码并发取明细 → 结果**按 codes 原序**，与串行逐项相同。

    这一条同时守住 Lead 要求的 4 条：保序、语义零变化、
    单码失败照样 skip 并记账、且并发路径**真的**用了 per-host 槽位。
    """
    serial_tbl, serial_pacer, serial_sess, serial_done = _run_industry(1)
    conc_tbl, conc_pacer, conc_sess, conc_done = _run_industry(4)

    # ① 完成顺序确实被打乱了（否则这条用例是空转的）
    assert serial_done == list(_CODES), "串行的完成顺序应当等于提交顺序"
    assert conc_done != serial_done, "完成顺序没被打乱，测不出保序"
    assert sorted(conc_done) == sorted(_CODES)

    # ② 结果逐项相同（顺序、字段、raw 键集都相同）
    assert conc_tbl.cols == serial_tbl.cols
    assert _fingerprint(conc_tbl) == _fingerprint(serial_tbl), "并发把顺序/内容改了"

    # ③ 结果顺序 = codes 原序（显式再钉一遍，别只依赖"两次一样"）
    got = [r["industry_code"] for r in conc_tbl.rows]
    want = [(c + "01") for c in _CODES if c not in _EMPTY_CODES]
    assert got == want, "并发结果没有按 codes 原序拼装"

    # ④ 跳过的码照样如实记账（单码失败不拖垮整批）
    assert conc_tbl.meta["skipped_codes"] == serial_tbl.meta["skipped_codes"]
    assert sorted(conc_tbl.meta["skipped_codes"]) == sorted(_EMPTY_CODES)

    # ⑤ meta 的语义键一个不差（黄金样本靠这几项判"语义漂移"）
    for k in ("params_dir", "level1_codes", "calls", "truncated", "skipped_codes"):
        assert conc_tbl.meta[k] == serial_tbl.meta[k], "meta[%r] 变了" % (k,)
    assert conc_tbl.meta["level1_codes"] == _CODES
    assert conc_tbl.meta["calls"] == 1 + len(_CODES)

    # ⑥ 默认（串行）路径：闸门全是 `wait()`（不传 host）
    assert serial_pacer.waits == [None] * (1 + len(_CODES)), \
        "concurrency=1 时不许出现 per-host 槽位"
    assert serial_pacer.stats()["hosts_tracked"] == 0

    # ⑦ 并发路径：只有模式 1 的目录调用在全局槽位，30 个明细全在 per-host 桶上
    assert conc_pacer.waits.count(None) == 1, "目录调用该走全局槽位（它必须先取）"
    used = {h for h in conc_pacer.waits if h is not None}
    assert used == set(_HOSTS[:4]), "per-host 槽位没按 HOSTS 分包：%r" % (used,)
    assert conc_pacer.stats()["hosts_tracked"] == 4

    # ⑧ 并发轮**不许**产生任何限流事件（测试层面也不许）
    assert conc_pacer.limited_events == 0
    assert serial_pacer.limited_events == 0


def test_map_independent_reraises_first_error_in_input_order():
    """`_map_independent`：失败按**原类型**抛出（不许吞、不许换成别的类型）。"""

    class _Boom(RuntimeError):
        pass

    c = _CLS(clock_probe=False, concurrency=4)
    try:
        def fn(x):
            if x == 3:
                raise _Boom("第 3 个炸了")
            return x * 10

        with pytest.raises(_Boom):
            c._map_independent(fn, [1, 2, 3, 4])
    finally:
        c.close()


# ══════════════════════════════════════════════════════════════════════════
# 五、零压测守卫：本模块一个 socket 都不许建
# ══════════════════════════════════════════════════════════════════════════
def test_this_module_never_touches_the_network(monkeypatch):
    """把 socket 钉死后再跑一遍并发路径 —— 证明上面的绿不是靠连网换来的。"""

    def _boom(*a, **kw):
        raise AssertionError("本测试模块必须离线：不许建 socket")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket.socket, "connect", _boom)

    tbl, pacer, _sess, _done = _run_industry(4)
    assert len(tbl.rows) == len(_CODES) - len(_EMPTY_CODES)
    assert pacer.limited_events == 0
