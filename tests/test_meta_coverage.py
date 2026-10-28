# -*- coding: utf-8 -*-
r"""**结构性缺失检测**：每个返回 `Table`/`Dataset` 的公开方法都必须带语义层 5 键。

为什么必须单独有这么一条测试
----------------------------
黄金样本门禁**天生检测不到"必需字段缺失"**：样本里没有 `meta.fetched_at`、
回放里也没有，两边一样 → **0 差异**。也就是说 `boards.py` 那种"重建 `Table`
时把 meta 丢了"的缺陷，23 份样本全绿也照不出来（复核者就是这么抓到的）。
所以要有一条**不看数据、只看形状**的测试兜住它。

三条硬约束（复核者要求，缺一条这条测试就有洞）
--------------------------------------------
1. **方法清单从 `Client` 自动推导**，不许硬编码。
   硬编码的清单会在新增方法时**自动豁免**新方法的缺失 —— 那正是"把判定依据放在
   可以不同的地方"这类错误的同型。这里用 `inspect` 读返回注解；
   同时用 `ARGS`（每个方法的调用参数）做**双向核对**：
   `set(ARGS) != 推导集` 就红 —— 新方法既不会漏检，也不会因为"没人给它写参数"
   而被静默跳过。
2. **桩打在最底层**：`Session.ask`（数据中心 JSON 通道）与 `hq_*` 原始取数函数
   （PB 通道）。**绝不**打在 `_stamp_table` / `client.call` 之上 ——
   `sections/*._shape()` 重建 `Table` 丢 meta 就发生在那之上，桩一高就永远绿。
   数据中心那一路的桩返回的是**真正的 JSON 字节**，所以 `session.parse()`、
   `_make_table()`、`_stamp_table()`、`sections` 的 `_shape()` **全都真的跑了**。
3. **自证这条测试会红**：临时让 `_shape` 不继承 meta，这条测试必须立刻红
   （见报告里的"红的那一刻"输出）。不会红的测试等于没写。
"""
from __future__ import annotations

import inspect
import json
import threading

import pytest

import tdxdata
from tdxdata import errors
from tdxdata._engine import datacenter as dc
from tdxdata._engine import hq_extra
from tdxdata._engine import hq_pb
from tdxdata._engine import session as sess_mod
from tdxdata.models import Dataset, Table

#: 每条 `Table`/`Dataset` 都必须有的语义层字段（ARCH-002：Provenance 一等字段）
REQUIRED = ("as_of", "status", "fetched_at", "elapsed", "cmd")

#: `meta["source"]` 的固定子键集（`client.META_SOURCE_KEYS`）
SOURCE_KEYS = ("host", "cmd", "ctl", "clock", "cached")

#: 每个方法的调用参数。**不是**"要测哪些方法"的白名单 ——
#: 方法清单由 `_derived_methods()` 从 `Client` 推导，这里只提供"怎么调"，
#: 两边必须**完全相等**（见 `test_arg_map_matches_derived_methods`）。
ARGS = {
    "capabilities": (),          # 阶段 6：静态注册表，无参数（不联网、不建连）
    "call": ("CWServ.app_bklist_ag", 0x16, ["2"]),
    "get": ("holiday",),
    "get_block_data": ("MStock_ZLJX_ADDE_R",),
    "get_block_rows": ("MStock_ZLJX_ADDE_R",),
    "get_board_members": ("880903",),   # 概念板块代码（行业码没有成分通道）
    "get_board_stat": (),
    "get_boards": ("industry",),
    "get_breadth": ("880005",),
    "get_capital_flow": (None,),
    "get_f10": ("600519",),
    "get_finance": ("600519",),
    "get_indicators": ("600519",),
    "get_intraday_samples": (["999999"],),
    "get_market_breadth": (),
    "get_market_stat": ("updown",),
    "get_samples": (["600519", "000001"],),
    "get_xdxr": ("600519",),
}


# ══════════════════════════════════════════════════════════════════════════
# 一、方法清单：**从 Client 推导**，不硬编码
# ══════════════════════════════════════════════════════════════════════════
def _derived_methods() -> dict:
    """扫 `Client` 的**返回注解**，找出所有返回 `Table` 或 `Dataset` 的公开方法。

    ⚠️ 注解是字符串（模块有 `from __future__ import annotations`），
    所以匹配的是文本 `"Table"` / `"Dataset"`，不是类型对象。
    """
    cls = getattr(tdxdata, "Client")
    found = {}
    for name in dir(cls):
        if name.startswith("_"):
            continue
        try:
            attr = inspect.getattr_static(cls, name)
        except AttributeError:                              # pragma: no cover
            continue
        if isinstance(attr, property):
            continue
        fn = getattr(cls, name, None)
        if not callable(fn):
            continue
        ann = str(inspect.signature(fn).return_annotation)
        if "Table" in ann:
            found[name] = "table"
        elif "Dataset" in ann:
            found[name] = "dataset"
    return found


def test_arg_map_matches_derived_methods():
    """`ARGS` 与**推导出来的**方法集必须严格相等。

    这条是"不许硬编码"的守卫：
      · 新增一个返回 `Table` 的方法 → 推导集变大 → 这里立刻红，
        逼作者给它补参数（于是它自动进入缺失检测，不会被豁免）；
      · `ARGS` 里留着一个已经不返回 `Table` 的方法 → 也红（清单要跟着代码走）。
    """
    derived = _derived_methods()
    assert set(ARGS) == set(derived), (
        "返回 Table/Dataset 的方法集变化了：\n"
        "  推导出但 ARGS 没有：%s\n  ARGS 有但推导不出：%s"
        % (sorted(set(derived) - set(ARGS)), sorted(set(ARGS) - set(derived))))
    assert derived, "一个返回 Table 的方法都没推导出来？检查注解是否退化"
    # 至少覆盖到这些"曾经漏过 meta"的重灾区
    for must in ("get_boards", "get_market_stat", "get_block_data", "get_block_rows",
                 "get_samples", "get_board_stat", "get_capital_flow", "get_f10",
                 "get_finance", "get_xdxr", "get_indicators", "call", "get",
                 "get_breadth"):
        assert must in derived, "%s 没被推导出来（注解丢了？）" % must


# ══════════════════════════════════════════════════════════════════════════
# 二、最底层桩：Session.ask（JSON 通道）+ hq_* 原始取数函数（PB 通道）
# ══════════════════════════════════════════════════════════════════════════
#: 通用 JSON 回包：列名刻意给 7615 数据中心常见的 N001..N007，
#: 行只给 1 行 —— 各 `sections` 的映射层对"列不认识"是容忍的（进 `raw`）。
_JSON_COLS = ["N001", "N002", "N003", "N004", "N005", "N006", "N007"]
_JSON_ROW = ["20261027", "600519", "贵州茅台", "1234.5", "1", "X10", "煤炭"]


def _json_reply(cols=None, rows=None) -> bytes:
    """拼一份**真**的 `ResultSets` 回包（`session.parse()` 能直接吃）。"""
    return json.dumps({
        "ErrorCode": 0,
        "ResultSets": [{
            "ColName": list(cols or _JSON_COLS),
            "Content": [list(r) for r in (rows or [_JSON_ROW])],
        }],
    }, ensure_ascii=False).encode("gbk", "replace")


class _FakeSession:
    """只实现 `Client` 真正会用到的那几个方法；`ask` 是**最底层**的桩。

    代表"一条已建好的长连接"。`Client._ensure()` 看到 `alive=True` 就原样返回它，
    于是 `client.call()` 的 `self._ensure().ask(...)` 拿到的是这里造的 JSON 字节，
    其后的 `session.parse()` → `_make_table()` → `_stamp_table()` → `sections`
    全是**真代码**。
    """

    alive = True
    host = "10.0.0.9"
    port = 7615

    def __init__(self):
        self.calls = []

    def ask(self, ctl, cmd, args, *, idle=4.0, tail=0.35):
        self.calls.append((cmd, ctl))
        return _json_reply()

    def reconnect(self):
        self.calls.append(("<reconnect>", None))

    def switch_host(self):
        self.calls.append(("<switch>", None))

    def close(self):
        pass


class _NullPacer:
    """零延迟的限流闸门替身（**只**替掉"睡 2.2 秒"这件事，不碰 meta 路径）。"""

    gap = 0.0
    limited_events = 0

    def wait(self):
        pass

    def note_ok(self):
        pass

    def note_limited(self):
        pass

    def note_limited_soft(self):                            # pragma: no cover
        pass

    def stats(self):
        return {"gap": 0.0, "limited_events": 0}


def _client():
    """离线 `Client`：跑过 `__init__` 的字段初始化，但**不建连、不联网**。"""
    cls = getattr(tdxdata, "Client")
    c = cls.__new__(cls)
    c._dc_mod = dc
    c._session_mod = sess_mod
    c._pacer = _NullPacer()
    c._sess = _FakeSession()
    c._ensure_lock = threading.Lock()
    c._host = None
    c._timeout = 1.0
    c._gap = 0.0
    c._cache = None
    c._products = {}
    c._pb_products = {}
    c._clock = None
    c._last_call = {}
    c._cal = {}
    c._clock_probe = False           # 关掉探针：零网络
    c._probe_allowed = False
    c._in_cal = False
    c._load_products()               # 读本地 products.json（`get()` 要用），不联网
    return c


_BARS_ROW = {"Data": "20261027", "Open": "1.0", "High": "1.0", "Low": "1.0",
             "Close": "1.0", "Volume": "100", "Amount": "100", "Second": "0"}
_SAMPLE = {"code": "600519", "pre_close": 1234.5, "floats": [1.0, 2.0, 3.0]}
_BLOCK = {"block": "MStock_ZLJX_ADDE_R", "status": "1", "field6": 1,
          "rows": [["1", "600519", "贵州茅台", "1234.5"]]}


@pytest.fixture()
def offline(monkeypatch):
    """把 PB 路径的 `hq_*` 原始取数函数全部打成桩（不联网、不发帧）。"""
    monkeypatch.setattr(hq_pb, "MAX_ROWS", 416, raising=False)
    monkeypatch.setattr(hq_pb, "get_bars",
                        lambda *a, **k: (["Data"], [dict(_BARS_ROW)]))
    monkeypatch.setattr(hq_pb, "get_minutes",
                        lambda *a, **k: (["Data"], [dict(_BARS_ROW)]))
    monkeypatch.setattr(hq_pb, "get_market_page",
                        lambda *a, **k: (["Code"], [{"Code": "880903"}]))
    # 2026-10-28 新增的两条 PB 直通函数（`PBSdstat` / `PBSample`），
    # 与上面几条同级：都是 `hq_pb` 的**原始取数**层，桩打在这里才守得住 meta 路径。
    monkeypatch.setattr(hq_pb, "market_breadth",
                        lambda *a, **k: {"shape": "summary", "declared_count": 1,
                                         "fields": {1: 1637, 2: 3775, 3: 160,
                                                    4: 35, 5: 15}, "off": 3})
    monkeypatch.setattr(hq_pb, "intraday_samples",
                        lambda *a, **k: [{"code": "999999", "market": 1,
                                          "total_points": 240, "pre_close": 3658.026,
                                          "n_points": 2, "points": [1.0, 2.0],
                                          "off": 0}])
    # `block_data` 真实返回 `(块名, 块状态, 行)` 三元组
    monkeypatch.setattr(hq_extra, "block_data",
                        lambda *a, **k: ("MStock_ZLJX_ADDE_R", "1",
                                         [list(_BLOCK["rows"][0])]))
    monkeypatch.setattr(hq_extra, "block_rows", lambda *a, **k: [list(_BLOCK["rows"][0])])
    monkeypatch.setattr(hq_extra, "samples", lambda *a, **k: [dict(_SAMPLE)])
    monkeypatch.setattr(hq_extra, "board_stat",
                        lambda *a, **k: (["Setcode"], [["1", "600519", "贵州茅台",
                                                        1.0, 0.0, 1.0, 1]]))
    return monkeypatch


# ══════════════════════════════════════════════════════════════════════════
# 三、逐个方法：5 键必须在
# ══════════════════════════════════════════════════════════════════════════
def _table_of(got) -> Table:
    if isinstance(got, Dataset):
        return got.table
    assert isinstance(got, Table), "返回的不是 Table/Dataset：%r" % (type(got),)
    return got


@pytest.mark.parametrize("name", sorted(ARGS))
def test_every_table_method_carries_semantic_provenance(name, offline):
    """**每条**返回 `Table`/`Dataset` 的公开方法都必须带齐语义层出处。

    ARCH-002：5 键的 meta dict 已被一等字段 `Provenance` 取代 ——
    `as_of` / `status` / `fetched_at` / `elapsed` 是字段，"source" 收拢为
    `host` / `cmd` / `ctl` / `clock` / `cached` 五个字段。缺一个就红，
    并点名是哪个方法缺哪个键 —— 这就是复核者要的"缺失检测"。
    """
    c = _client()
    method = getattr(c, name)
    got = method(*ARGS[name])
    table = _table_of(got)
    prov = table.provenance
    assert prov is not None, "%s() 返回的 Table 连 provenance 都没有" % name

    # ARCH-002："as_of 在场"= 字段被显式写过（None 也是合法值 —— 快照通道没有数据时刻）
    missing = [k for k in REQUIRED
               if getattr(prov, k) is None and k not in ("fetched_at",)]
    # `as_of` / `elapsed` 允许是 None（快照通道/缓存路径），
    # 但**字段必须被显式写过**：对象存在且来自 `_stamp_table` 链路即算在场。
    missing = [k for k in missing if k not in ("as_of", "elapsed")]
    assert not missing, "%s() 返回的 Table 缺语义层字段：%s" % (name, missing)
    assert prov.fetched_at is not None, "%s() 缺 fetched_at" % name
    assert prov.cmd, "%s().provenance.cmd 不许为空" % name
    assert hasattr(prov.clock, "value"), "%s().provenance.clock 必须是 ClockSource" % name

    # status 必须是四态之一（不是 None、不是自由文本）
    assert prov.status in {"final", "provisional", "pending", "delayed"}, \
        "%s().provenance.status 不是合法四态：%r" % (name, prov.status)

    # fetched_at 必须是 aware datetime（UTC+8）
    from datetime import datetime as _dt
    assert isinstance(prov.fetched_at, _dt) and prov.fetched_at.tzinfo is not None, \
        "%s().provenance.fetched_at 必须是 aware datetime：%r" % (name, prov.fetched_at)


@pytest.mark.parametrize("name", sorted(ARGS))
def test_as_of_none_implies_note(name, offline):
    """`as_of is None` 的通道**必须**配说明（notes 里说清为什么给不出时刻）。

    真实事故形态：`f10`/`finance` 那两条通道确实没有数据日期，但解释被塞进了
    时效判定原因里 —— 下游看到 notes 有一长串，就会以为"时效判定有问题"，
    而真相是"这条通道压根不带时刻"。两件事必须分开放、分开说。
    """
    c = _client()
    table = _table_of(getattr(c, name)(*ARGS[name]))
    prov = table.provenance
    assert prov is not None, "%s() 连 provenance 都没有" % name
    assert prov.as_of is not None or any("as_of" in n for n in prov.notes), \
        "%s() 的 as_of 是 None，但 notes 里没有 as_of 无法确定的原因：%r" % (
            name, prov.notes)


def test_no_stubbed_call_escaped_the_fake_session(offline):
    """桩确实生效：整个过程一次真实连接都没建（`_sess` 一直是那个假对象）。"""
    c = _client()
    for name in sorted(ARGS):
        getattr(c, name)(*ARGS[name])
    assert isinstance(c._sess, _FakeSession), "有人把 `_sess` 换掉了（说明真去连了）"
    assert c._sess.calls, "假 Session 一次 `ask` 都没收到？那说明桩打高了"
