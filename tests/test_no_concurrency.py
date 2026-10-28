# -*- coding: utf-8 -*-
"""并发特性**已删除**的契约测试（离线，不打任何 socket）。

背景：为什么删
--------------
本库曾有 `Client(concurrency=2..4)`：建 N 条 lane、每条钉一台服务器、
用 per-host 槽位分摊配额。2026-10-27 实测结论是**不可行**：

| 轮次 | 耗时 | `limited_events` 增量 | 结果 |
|---|---|---|---|
| 串行 `concurrency=1` | 68.64 s（439 行 × 8 列，31 次调用） | +0 | 全部成功 |
| 并发 `concurrency=4` | 未达成：头 5 秒即被重置，之后 175 s 内 0 次成功 | +3（间隔 2.2→12.0，3 台进冷却） | 全失败 |

即**配额按来源 IP 算，不按「IP × 服务器」算** —— 4 台服务器换不来 4 份配额。
而且它带一个**永久死锁**：`lane.lock` 是不可重入的 `threading.Lock`，
`_run_in_lane` 持锁调用 `fn`，`_ensure` 又去取同一把锁 →
`get_market_stat("industry")` 一调就挂住，无超时、无提示，只能 Ctrl-C。

所以整个特性被移除。这个文件钉住四件事：

1. **参数真的没了**：`Client.__init__` 不接受 `concurrency`（传了就 `TypeError`，
   明确报错，**不静默忽略**）。
2. **实现里不再有线程池/lane**：`client.py` 不许再 import `ThreadPoolExecutor`、
   不许再定义 `_Lane` —— 防止有人"顺手"把它加回来。
3. **死锁回归**：`get_market_stat("industry")` 必须**在有限时间内返回**。
   ⚠️ 历史教训：旧用例把 `Client._ensure` **整个替换掉**，于是真 `_ensure`
   根本不在被测路径上，死锁在结构上不可能被发现。这里只替换
   `session.Session`（最底层），`_ensure` / `_map_independent` / `flow` 全走真代码。
4. **串行语义不变**：`_map_independent` 按输入原序返回、**遇错即停**、按原类型上抛；
   `rate_stats()` 的三个兼容键恒为 `1 / 1 / []`。

⚠️ 本文件按项目纪律**不写类名字面量**（一律 `getattr` 取类）。
"""
from __future__ import annotations

import inspect
import json
import os
import threading

import pytest

import tdxdata
import tdxdata.client as client_mod
from tdxdata._engine import session as session_mod
from tdxdata.sections import flow as flow_mod

#: 红线：测试里不写类名字面量。
_CLS = getattr(tdxdata, "Client")

#: `flow` 只认 `^X\d{2}$` 形状的一级行业码。
_CODES = ["X%02d" % n for n in range(10, 40)]
#: 刻意让这两个码返回"有列无行" → 必须被跳过并记进 `skipped_codes`。
_EMPTY_CODES = {"X15", "X27"}


# ══════════════════════════════════════════════════════════════════════════
# 一、桩：只替换最底层（`session.Session`），其余全走真代码
# ══════════════════════════════════════════════════════════════════════════
def _payload(names, rows) -> bytes:
    """造一份和真实回包同形状的载荷（`session.parse()` 能直接吃）。"""
    return json.dumps(
        {"ResultSets": [{"ColName": list(names),
                         "Content": [list(r) for r in rows]}]},
        ensure_ascii=False).encode("gbk")


class _FakeSession:
    """冒充 `Session`：`ask` 按参数合成回包，绝不碰网络。

    只实现 `Client` 真正会用到的那几个成员（`connect` / `alive` / `ask` /
    `close` / `reconnect` / `switch_host` / `host`）。
    """

    def __init__(self, host=None, timeout=15.0, **_kw):
        self.host = host or "fake-host"
        self.timeout = timeout
        self.calls: list = []
        self.closed = False

    @property
    def alive(self) -> bool:
        return not self.closed

    def connect(self):
        return self

    def reconnect(self):
        return self

    def switch_host(self, cooldown=None):        # noqa: ARG002
        return self

    def close(self) -> None:
        self.closed = True

    def ask(self, ctl, cmd, args, idle=4.0, tail=0.35):   # noqa: ARG002
        body = json.loads(args.decode())
        mode, code = body["Params"][0], body["Params"][1]
        self.calls.append((mode, code))
        if str(mode) == "1":                       # 一级行业目录
            return _payload(["N001"], [[c] for c in _CODES])
        if code in _EMPTY_CODES:                   # 有列无行 = 该码当期无数据
            return _payload(["N001", "N002", "N003", "N004",
                             "N005", "N006", "N007", "N008"], [])
        i = _CODES.index(code)
        return _payload(
            ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008"],
            [["IND-" + code, code + "01", "881%03d" % i, "Ⅱ",
              "20261019", "%.4f" % (10.0 + i), str(100 + i), str(5 + i)]])


class _FakePacer:
    """假闸门：不睡、不排队，只记账。"""

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

    def block(self, host, seconds=0.0):           # noqa: ARG002
        pass

    def ready(self, host):                        # noqa: ARG002
        return True

    def stats(self):
        return {"base": self.base, "current": self.gap, "max": self.max,
                "limited_events": self.limited_events, "cooling_hosts": 0,
                "hosts_tracked": 0}


# ══════════════════════════════════════════════════════════════════════════
# 二、参数与实现：并发**真的**没了
# ══════════════════════════════════════════════════════════════════════════
def test_client_has_no_concurrency_parameter():
    """`Client.__init__` 不接受 `concurrency`：传了必须是**明确的 TypeError**。

    「静默忽略一个参数」是本库最不能接受的失败形态（`get(**kwargs)` 丢参数
    就是这个病）—— 所以这里钉的是"报错"，不是"兼容"。
    """
    params = inspect.signature(_CLS.__init__).parameters
    assert "concurrency" not in params, "并发参数又回来了：%r" % list(params)

    c = _CLS(clock_probe=False)
    try:
        with pytest.raises(TypeError):
            _CLS(clock_probe=False, concurrency=2)
    finally:
        c.close()


def test_client_module_has_no_thread_pool_machinery():
    """`client.py` 的**代码**里不许再出现线程池/lane 相关符号。

    这是"删除"这件事在源码层的守卫：只看行为测不出"又 import 了
    `ThreadPoolExecutor` 但暂时没人调用"这种半恢复状态。

    ⚠️ 必须走 AST 而不是字符串搜索 —— 模块 docstring 里**要**讲
    `lane.lock` 的死锁成因（那是历史证据），字符串搜索会把它误判成残留。
    """
    import ast

    tree = ast.parse(open(client_mod.__file__, encoding="utf-8").read())

    imported: set[str] = set()
    classes: set[str] = set()
    names: set[str] = set()
    kwargs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add("%s.%s" % (node.module or "", "*"))
            imported.update("%s.%s" % (node.module or "", a.name) for a in node.names)
        elif isinstance(node, ast.ClassDef):
            classes.add(node.name)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.keyword) and node.arg:
            kwargs.add(node.arg)

    assert not any("concurrent.futures" in m for m in imported), \
        "client.py 又 import 了 concurrent.futures：%r" % sorted(imported)
    assert "ThreadPoolExecutor" not in names, "又用了 ThreadPoolExecutor"
    assert "_Lane" not in classes, "又定义了 _Lane"
    for banned in ("_lanes", "_concurrency", "_concurrency_eff", "_tls", "_pool"):
        assert banned not in names, "client.py 还有并发残留：%r" % banned
    assert "concurrency" not in kwargs, "又出现了 concurrency= 关键字参数"


# ══════════════════════════════════════════════════════════════════════════
# 三、死锁回归（本文件最重要的一条）
# ══════════════════════════════════════════════════════════════════════════
def test_industry_stat_returns_without_deadlock(monkeypatch):
    """`get_market_stat("industry")` 必须**在有限时间内返回**。

    历史 bug：`concurrency>1` 时 `lane.lock`（不可重入）被 `_run_in_lane`
    持住，`_ensure` 又去取同一把锁 → 永久挂起。旧用例因为把 `_ensure`
    整个替换掉了，结构上测不到。

    本用例的做法：**只**把 `session.Session` 换成假的，`_ensure`、
    `_map_independent`、`flow.get_market_stat` 全走真代码，
    然后在外层线程里限时等待 —— 挂住就红。
    """
    monkeypatch.setattr(session_mod, "Session", _FakeSession)

    box: dict = {}

    def _run():
        c = _CLS(clock_probe=False)
        c._pacer = _FakePacer()
        try:
            box["table"] = flow_mod.get_market_stat(c, "industry")
        except BaseException as exc:            # noqa: BLE001
            box["error"] = exc
        finally:
            try:
                c.close()
            except BaseException:               # noqa: BLE001
                pass

    th = threading.Thread(target=_run, daemon=True, name="industry-no-deadlock")
    th.start()
    th.join(timeout=20.0)

    assert not th.is_alive(), (
        "get_market_stat('industry') 20 秒没返回 —— 疑似死锁复发"
        "（检查 _ensure / _map_independent 里是否有锁重入）")
    assert "error" not in box, "industry 取数抛了异常：%r" % (box.get("error"),)

    table = box["table"]
    got = [r["industry_code"] for r in table.rows]
    want = [(c + "01") for c in _CODES if c not in _EMPTY_CODES]
    assert got == want, "结果没有按 codes 原序拼装"
    assert sorted(table.provenance.extra["skipped_codes"]) == sorted(_EMPTY_CODES)
    assert table.provenance.extra["calls"] == 1 + len(_CODES)


# ══════════════════════════════════════════════════════════════════════════
# 四、串行语义：保序 / 遇错即停 / 原类型上抛
# ══════════════════════════════════════════════════════════════════════════
class _Boom(RuntimeError):
    pass


def test_map_independent_preserves_order():
    """按 `items` 原序返回（哪怕 `fn` 内部顺序被扰动）。"""
    c = _CLS(clock_probe=False)
    try:
        seen: list = []

        def fn(x):
            seen.append(x)
            return x * 2

        out = c._map_independent(fn, [3, 1, 2])
    finally:
        c.close()
    assert out == [6, 2, 4]
    assert seen == [3, 1, 2]


def test_map_independent_short_circuits_and_reraises():
    """遇错即停 + 按**原类型**上抛（不许吞、不许继续烧配额）。"""
    c = _CLS(clock_probe=False)
    calls: list = []

    def fn(x):
        calls.append(x)
        if x == 2:
            raise _Boom("boom at %r" % (x,))
        return x

    try:
        with pytest.raises(_Boom):
            c._map_independent(fn, [1, 2, 3])
        assert calls == [1, 2], (
            "第 3 项不该被调用（遇错即停），实际调了 %r" % (calls,))
    finally:
        c.close()


# ══════════════════════════════════════════════════════════════════════════
# 五、兼容面：`rate_stats()` 的旧键还在，但恒为固定值
# ══════════════════════════════════════════════════════════════════════════
def test_rate_stats_keeps_compat_keys_with_fixed_values():
    """三个并发键保留（不破坏已有监控解析），但值恒为 `1 / 1 / []`。"""
    c = _CLS(clock_probe=False)
    try:
        st = c.rate_stats()
    finally:
        c.close()
    assert st["concurrency"] == 1
    assert st["concurrency_effective"] == 1
    assert st["lanes"] == []
    # `pacer.stats()` 的老键一个都没动
    for k in ("base", "current", "max", "limited_events", "cooling_hosts"):
        assert k in st, "pacer 统计键丢了：%r" % (k,)


def test_close_leaves_no_session_and_is_idempotent():
    """`close()` 只关本实例那条连接，反复调不炸。"""
    c = _CLS(clock_probe=False)
    c._sess = _FakeSession()
    c.close()
    assert c._sess is None
    c.close()                       # 幂等
    assert c._sess is None


def test_session_pin_is_still_a_session_capability():
    """`Session(pin=True)` 仍然存在（把连接钉死在一台主机上）。

    它不是并发专用的：手工排查、抓包复现时"钉住某台服务器"同样有用，
    所以随并发特性一起删掉的是 `Client` 的 lane，不是 `Session` 的 `pin`。
    """
    sess = getattr(session_mod, "Session")(session_mod.HOSTS[1], pin=True)
    try:
        assert sess.pin is True
        assert sess.host == session_mod.HOSTS[1]
        assert sess.alive is False, "只构造不该连网"
    finally:
        sess.close()

    plain = getattr(session_mod, "Session")(session_mod.HOSTS[1])
    try:
        assert plain.pin is False, "默认必须可轮换"
    finally:
        plain.close()


def test_this_module_never_touches_the_network(monkeypatch):
    """本文件所有用例都必须离线：真去连 socket 就直接失败。"""
    import socket as _socket

    def _boom(*a, **k):                                # noqa: ARG001
        raise AssertionError("测试里出现了真实 socket 连接")

    monkeypatch.setattr(_socket, "create_connection", _boom)
    monkeypatch.setattr(_socket.socket, "connect", _boom)
    assert os.path.basename(__file__) == "test_no_concurrency.py"
