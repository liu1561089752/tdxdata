# -*- coding: utf-8 -*-
r"""`dead` 档（本版本 App 未实现）**发请求之前**就被拦住 —— 守卫测试（全离线）。

为什么这条判据值一条独立测试
--------------------------
`dead` 名单（`_engine/catalog/dead_cmds.json`，191 条）是**负证据**：本地实测
那些命令回 `104`（App 未实现），且**没有任何一次 `rows>0` 正证据**。这类命令
真要发出去，服务端只会回空包 → 被重试逻辑误判成"疑似限流" → 白等 3 轮退避，
还把**进程级**间隔从 2.2 s 往上顶。所以正确行为是：**一个字节都不发**。

核心判据 = **假 `Session.ask` 的调用计数必须是 0**（"报错"不够 —— 报错之后
才发出去同样是把配额烧掉了）。

两条路径都要守：`call_cmd()`（新入口）与 `call()`（老入口，用户可能绕过前者）。

红线：本文件不出现 `Client(` / `Session(` / `AsyncClient(` 字面量
（`tools/gate.py` 会扫），裸对象用 `getattr(tdxdata, "Client").__new__(cls)` 造。
"""
from __future__ import annotations

import sys
import threading

import pytest

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                        # noqa: BLE001
    pass

import tdxdata
from tdxdata._engine import ctlmap
from tdxdata._engine import session as session_mod

_CLS = getattr(tdxdata, "Client")           # 红线：不写 `Client(` 字面量
errors = tdxdata.errors

#: 最小可解析回包（真有请求漏出去时，`call()` 也能正常走完，测的才是"计数"）。
_PASS_REPLY = b'{"ResultSets":[{"ColName":["N001"],"Content":[["20261027"]]}]}'


class _FakeSession:
    """假长连接：`ask()` 只做一件事 —— **记下自己被调用了几次**。"""

    host = "fake-host:7615"
    alive = True

    def __init__(self, reply=_PASS_REPLY):
        self.calls = []
        self._reply = reply

    def ask(self, ctl, cmd, args, idle=4.0):
        self.calls.append((ctl, cmd, args))
        return self._reply

    def reconnect(self):
        pass

    def switch_host(self):
        pass

    def close(self):
        pass


class _FakePacer:
    """零延迟闸门替身；**顺便记账** —— 用来证明连限速闸门都没进。"""

    limited_events = 0
    gap = 2.2

    def __init__(self):
        self.waits = 0

    def wait(self):
        self.waits += 1

    def note_ok(self):
        pass

    def note_limited(self):
        return 0.0

    def stats(self):
        return {}


def _client(reply=_PASS_REPLY):
    c = _CLS.__new__(_CLS)
    c._probe_allowed = False
    c._clock_probe = False
    c._clock = None
    c._last_call = {}
    c._in_cal = False
    c._cal = {}
    c._cache = None
    c._products = {}
    c._pb_products = {}
    c._pacer = _FakePacer()
    c._sess = _FakeSession(reply)
    c._ensure_lock = threading.Lock()
    c._host = None
    c._timeout = 1.0
    c._gap = 0.0
    c._session_mod = session_mod
    return c


#: 三个**不同命名空间**的 dead 命令（名单里各一条，且 ctl 都在目录里）。
_DEAD_CMDS = ("CWSearch.search", "CWServ.app_keyboard_ag_v2", "JJSServ.ph_zt_bxzj_ss")


# ══════════════════════════════════════════════════════════════════════════
# 一、核心判据：dead 命令 → 抛错 + **0 次请求**
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("cmd", _DEAD_CMDS)
def test_dead_command_is_blocked_before_any_request(cmd):
    """`call_cmd()` 的 dead 闸：`ask()` 计数 == 0（连限速闸门都没进）。"""
    assert ctlmap.tier_for(cmd) == "dead", "夹具假设变了：%s 不是 dead 档" % cmd
    c = _client()
    with pytest.raises(errors.NotSupportedError) as ei:
        c.call_cmd(cmd)
    assert c._sess.calls == [], "%s 是 dead 档，却发出了 %d 次请求" % (
        cmd, len(c._sess.calls))
    assert c._pacer.waits == 0, "dead 闸必须在限速闸门**之前**（waits=%d）" % c._pacer.waits
    assert cmd in str(ei.value), "错误消息要点名是哪条命令"


@pytest.mark.parametrize("cmd", _DEAD_CMDS)
def test_call_path_is_blocked_too(cmd):
    """`call()` 这条老入口**也要**拦（用户可能绕过 `call_cmd` 直接给 ctl）。"""
    assert ctlmap.tier_for(cmd) == "dead", "夹具假设变了：%s 不是 dead 档" % cmd
    ctl = ctlmap.ctl_for(cmd)
    assert ctl is not None, "夹具假设变了：%s 在目录里没有 ctl" % cmd
    c = _client()
    with pytest.raises(errors.NotSupportedError):
        c.call(cmd, ctl, [])
    assert c._sess.calls == [], "%s 经 call() 发出了 %d 次请求" % (
        cmd, len(c._sess.calls))
    assert c._pacer.waits == 0


def test_dead_message_says_104_and_names_the_escape_hatch():
    """消息要同时说清三件事：**104** / **快照日期** / **逃生舱 `allow_dead=True`**。

    第三件事是产品的诚实性要求：名单是负证据快照，App 升级后可能已经实现了，
    用户必须能自己解开这个判断（否则库就成了"替你永久判死"的中间人）。
    """
    c = _client()
    with pytest.raises(errors.NotSupportedError) as ei:
        c.call_cmd("CWSearch.search")
    msg = str(ei.value)
    assert "104" in msg
    assert "2026-10-31" in msg
    assert "allow_dead=True" in msg
    assert "没" in msg and "发" in msg          # 明说"请求没有发出"


# ══════════════════════════════════════════════════════════════════════════
# 二、逃生舱：`allow_dead=True` 时**真的**发出去
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("cmd", _DEAD_CMDS)
def test_allow_dead_really_sends_via_call_cmd(cmd):
    """`allow_dead=True` → `ask()` 计数 == 1（证明这个口子是通的，不是安慰剂）。"""
    c = _client()
    table = c.call_cmd(cmd, allow_dead=True)
    assert len(c._sess.calls) == 1, "allow_dead=True 应当真的发一次，实际 %d 次" % len(
        c._sess.calls)
    assert c._sess.calls[0][1] == cmd
    assert isinstance(table, tdxdata.Table)
    assert c._pacer.waits == 1                  # 这次确实进了限速闸门


def test_allow_dead_really_sends_via_call():
    cmd = "CWSearch.search"
    c = _client()
    table = c.call(cmd, ctlmap.ctl_for(cmd), [], allow_dead=True)
    assert len(c._sess.calls) == 1
    assert isinstance(table, tdxdata.Table)


# ══════════════════════════════════════════════════════════════════════════
# 三、优先级与边界：`verified` 不被拦、非 dead 不受影响
# ══════════════════════════════════════════════════════════════════════════
def test_verified_tier_is_never_blocked():
    """`verified > dead`：有 `rows>0` 正证据的命令一律放行（哪怕名单里有同名项）。

    这类冲突项共 47 条（其中 46 条是 products.json 的产品命令）—— 它们由
    `ctlmap.tier_for` 一律判 `verified`，这里的闸只看**那一档的结论**，不重判。
    """
    cmd = "CWServ.ph_agf10_gsjj"
    assert ctlmap.tier_for(cmd) == "verified"
    c = _client()
    c.call_cmd(cmd)
    assert len(c._sess.calls) == 1


#: 2026-11-01 **真机复验复活**的 7 条产品命令（原在 dead 名单里，实测都有数据，
#: 已移出名单 191 → 184 并并入 verified）。行数是 Lead 真机跑的：
#: `zcjj 4310 行 / xg_xgjx 10 行 / queryRandom 2 行 / jjs_jgxq 1 行 /
#:  north_jg 8 行 / bzxj_sy_bkbd 1 行 / bzxj_sy_jgbd 1 行`。
_REVIVED_PRODUCTS = (
    ("zcjj", "CWServ.ph_jjzt_zcjj"),
    ("xg_xgjx", "CWServ.ph_zt_xg_xgjx"),
    ("queryRandom", "DataAggregation.queryRandom"),
    ("jjs_jgxq", "JJSServ.ph_datacenter_jjs_hsgt_v3_jgxq"),
    ("north_jg", "JJSServ.ph_datacenter_jjs_hsgtzt_v3_bsjj_jg"),
    ("bxzj_sy_bkbd", "JJSServ.ph_zt_bxzj_sy_bkbd"),
    ("bxzj_sy_jgbd", "JJSServ.ph_zt_bxzj_sy_jgbd"),
)


@pytest.mark.parametrize("name,cmd", _REVIVED_PRODUCTS)
def test_revived_product_commands_are_no_longer_gated(name, cmd):
    """这 7 条**不再是 dead**：`get()` 也不许被 dead 闸拦（真机已实测有数据）。

    2026-11-01 真机复验把 dead 名单从 191 条压到 184 条 —— **dead 档此后不含任何
    产品命令**。这条守的是这个新不变量：一旦有人（或某次重生成）把这 7 条重新加回
    `dead_cmds.json`，`tier_for` 立刻变回 `dead`、本用例立刻红。

    离线判据不是"取到数据"（假 Session 只能回假包），而是**请求真的发出去了**：
    `c._sess.calls` 非空 = 没有被本地 dead 闸拦在发请求之前。
    """
    tier = ctlmap.tier_for(cmd)
    assert tier != "dead", "%s 又被加回 dead 名单了（真机 2026-11-01 实测有数据）" % cmd
    assert tier == "verified", (
        "真机实测有数据应落 verified 档（已并入 verified.json），实际 %r" % (tier,))
    c = _client()
    c._load_products()                          # 只读本地 products.json
    ds = c.get(name)                            # 不许因 dead 闸抛错
    assert c._sess.calls, "%s 的请求没发出去（被本地闸门拦在发送前）" % cmd
    assert c._sess.calls[0][0] == ctlmap.ctl_for(cmd)
    assert c._sess.calls[0][1] == cmd
    assert isinstance(ds, tdxdata.Dataset)


def test_get_allow_dead_transparent(monkeypatch):
    """`allow_dead` **真的透传**到 `call()`（用 monkeypatch 造假 dead 来测管道）。

    为什么要造假 dead：2026-11-01 之后真实名单里**已经没有产品命令**，无法再用
    真实数据覆盖"`get()` 的 `allow_dead` 关键字有没有透到 `call()`"这条链。
    所以这里只把 `ctlmap.tier_for` 对**这一条**命令报 `dead`，其余照原函数 ——
    测的是**管道**，不是数据。
    """
    target = "CWServ.ph_jjzt_zcjj"              # 产品名 `zcjj`
    real_tier_for = ctlmap.tier_for

    def fake_tier_for(cmd):
        return "dead" if cmd == target else real_tier_for(cmd)

    monkeypatch.setattr(ctlmap, "tier_for", fake_tier_for)

    # ① 默认：被 dead 闸拦在发请求之前（按 dead 闸那条消息判断，不靠异常类）
    c = _client()
    c._load_products()
    with pytest.raises(errors.NotSupportedError) as ei:
        c.get("zcjj")
    assert "没有发出" in str(ei.value), "应当是 dead 闸那条消息：%r" % str(ei.value)[:80]
    assert c._sess.calls == [], "dead 档不许发请求"
    assert c._pacer.waits == 0

    # ② allow_dead=True：关键字透传到 call()，真的发一次
    c2 = _client()
    c2._load_products()
    ds = c2.get("zcjj", allow_dead=True)
    assert len(c2._sess.calls) == 1, "allow_dead=True 应当真的发一次，实际 %d 次" % len(
        c2._sess.calls)
    assert c2._sess.calls[0][0] == ctlmap.ctl_for(target)
    assert c2._sess.calls[0][1] == target
    assert isinstance(ds, tdxdata.Dataset)


def test_allow_dead_is_keyword_only_and_defaults_false():
    """`get()` 的 `allow_dead` 是 **keyword-only 且默认 False**（默认行为不许变）。"""
    import inspect

    params = inspect.signature(_CLS.get).parameters
    assert "allow_dead" in params
    assert params["allow_dead"].default is False
    assert params["allow_dead"].kind is inspect.Parameter.KEYWORD_ONLY


def test_unknown_command_is_not_blocked_by_dead_gate():
    """不在目录的命令 `tier_for` 返回 `None` —— 不该被当成 dead（那条路另有报错）。"""
    cmd = "CWServ.ph_definitely_not_a_command"
    assert ctlmap.tier_for(cmd) is None
    c = _client()
    with pytest.raises(errors.NotSupportedError) as ei:
        c.call_cmd(cmd)
    assert "命令目录" in str(ei.value)          # 是"不在目录"，不是"dead 档"
    assert "104" not in str(ei.value)
    assert c._sess.calls == []
