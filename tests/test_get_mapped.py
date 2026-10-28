# -*- coding: utf-8 -*-
r"""`Client.get(..., mapped=True)`：**选开的列义语义层**（阶段 B）。

为什么要有这条测试
------------------
`FIELD_MAP` 扩了 16 个产品键（宏观 5 + 基金 11），但它必须**有人消费**才不是死配置；
同时 `get()` 的默认行为是「返回服务端原始列名」的公开契约 —— 两件事要同时成立，
只能用**选开参数**实现（`mapped=False` 默认，逐字节不变）。本文件钉住四点：

1. **默认契约不变**：`get(name)` 的 `cols` 仍是 `N00x` 原始列（老代码零改动）；
2. **选开生效**：`get(name, mapped=True)` 的 `cols` 是标准字段，原值逐行在 `raw` 里；
3. **语义 meta 不受影响**：`as_of/status/fetched_at/elapsed/source` 五键仍在，
   `data_date` 推断仍从**原始**首行 `N001` 做（apply 之后 `N001` 已改名，
   次序反了会静默丢 `data_date`——这正是本文件要守的次序契约）；
4. **未登记列义的产品不许静默降级**：`mapped=True` 却查不到映射 → `ValueError`。

全部离线：`Session.ask` 是**最底层**桩，返回真 JSON 字节；`session.parse` →
`_make_table` → `_stamp_table` → `_describe_product` → `mapping.apply` 全是真代码。
"""
from __future__ import annotations

import inspect
import json
import threading

import pytest

import tdxdata
from tdxdata.models import Dataset, Table

_CLS = getattr(tdxdata, "Client")

#: `etf_t0` 的**文档实测样本首行**（数据中心剩余接口.md §4，2026-10-24）：
#: 列名与值都按文档原文，别改 —— 它同时是 FIELD_MAP 映射的证据锚点。
_ETF_T0_COLS = ["N001", "N002", "N003", "N004", "N005", "N006", "N007"]
_ETF_T0_ROW = ["科创债ETF万家", "159110", "0", "10020101",
               "2.88", "-0.3085", "2430354000.00"]
_ETF_T0_STD = ["name", "code", "market", "category_code",
               "price", "change_pct", "amount_yuan"]


def _json_reply(cols, rows) -> bytes:
    """拼一份**真**的 `ResultSets` 回包（`session.parse()` 能直接吃）。"""
    return json.dumps({
        "ErrorCode": 0,
        "ResultSets": [{
            "ColName": list(cols),
            "Content": [list(r) for r in rows],
        }],
    }, ensure_ascii=False).encode("gbk", "replace")


class _FakeSession:
    """只实现 `Client` 真正会用到的那几个方法；`ask` 是**最底层**的桩。"""

    alive = True
    host = "10.0.0.9"
    port = 7615

    def __init__(self, cols, rows):
        self._cols, self._rows = cols, rows
        self.calls = []

    def ask(self, ctl, cmd, args, *, idle=4.0, tail=0.35):
        self.calls.append((cmd, ctl))
        return _json_reply(self._cols, self._rows)

    def reconnect(self):                                        # pragma: no cover
        pass

    def switch_host(self):                                      # pragma: no cover
        pass

    def close(self):
        pass


class _NullPacer:
    """零延迟的限流闸门替身（不碰 meta 路径）。"""

    gap = 0.0
    limited_events = 0

    def wait(self):
        pass

    def note_ok(self):
        pass

    def note_limited(self):
        pass

    def note_limited_soft(self):                                # pragma: no cover
        pass

    def stats(self):
        return {"gap": 0.0, "limited_events": 0}


def _client():
    """离线 `Client`：不建连、不联网（关探针），但 `_make_table` 等全跑真代码。"""
    from tdxdata._engine import datacenter as dc
    from tdxdata._engine import session as sess_mod

    c = _CLS.__new__(_CLS)
    c._dc_mod = dc
    c._session_mod = sess_mod
    c._pacer = _NullPacer()
    c._sess = _FakeSession(_ETF_T0_COLS, [_ETF_T0_ROW])
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
    c._clock_probe = False
    c._probe_allowed = False
    c._in_cal = False
    c._load_products()
    return c


def _table_of(got) -> Table:
    if isinstance(got, Dataset):
        return got.table
    assert isinstance(got, Table)
    return got


# ══════════════════════════════════════════════════════════════════════════
# 一、默认契约：一个字节都不变
# ══════════════════════════════════════════════════════════════════════════
def test_default_get_keeps_the_raw_column_contract():
    """`get(name)` 默认仍返回**服务端原始列**（`mapped=False` 是契约，不是建议）。"""
    c = _client()
    try:
        ds = c.get("etf_t0")
        t = _table_of(ds)
        assert list(t.cols) == _ETF_T0_COLS, list(t.cols)
        assert "raw" not in t.rows[0], "默认路径不该多出 raw sidecar"
        assert t.rows[0]["N001"] == "科创债ETF万家"
    finally:
        c.close()


# ══════════════════════════════════════════════════════════════════════════
# 二、选开：标准字段 + raw 一个不丢
# ══════════════════════════════════════════════════════════════════════════
def test_mapped_get_returns_standard_fields_and_keeps_raw():
    """`mapped=True` → `cols` 是标准字段；`raw` 里原列/原值一个不丢。"""
    c = _client()
    try:
        ds = c.get("etf_t0", mapped=True)
        t = _table_of(ds)
        assert list(t.cols) == _ETF_T0_STD, list(t.cols)
        row = t.rows[0]
        assert row["name"] == "科创债ETF万家"
        assert row["code"] == "159110"
        assert row["market"] == 0, "INT_FIELDS 里的 market 该是 int"
        assert row["category_code"] == "10020101", "分类码是码本身，别当数字算术"
        assert row["price"] == 2.88
        assert row["change_pct"] == -0.3085, "已是百分数，不许再 ×100"
        assert row["amount_yuan"] == 2430354000.0
        # raw sidecar：原始列名与原始值逐项都在
        assert row["raw"]["N001"] == "科创债ETF万家"
        assert row["raw"]["N007"] == "2430354000.00", "raw 收服务端原值（字符串）"
        assert len(row) == len(t.cols) + 1, (len(row), len(t.cols))
    finally:
        c.close()


# ══════════════════════════════════════════════════════════════════════════
# 三、次序契约：语义 meta 不受 apply 影响（data_date 从原始 N001 推）
# ══════════════════════════════════════════════════════════════════════════
def test_mapped_keeps_semantic_meta_and_data_date_inference_order():
    """5 键必须还在；`data_date` 是从**原始** `N001` 推的（推不出就 None，不填今天）。

    `etf_t0` 的首行 `N001` 是**名称**不是日期 —— 所以这里断言的是"推不出就给 None"，
    而不是某个具体日期。关键是：换列（apply）**之后**再推就会推不出来，
    所以次序必须是"先描述、后换列"；这条测试守的就是那个次序。
    """
    c = _client()
    try:
        ds = c.get("etf_t0", mapped=True)
        t = _table_of(ds)
        prov = t.provenance
        assert prov is not None, "mapped 之后 provenance 不许丢"
        for f in ("as_of", "status", "fetched_at", "elapsed", "cmd"):
            assert hasattr(prov, f), "mapped 之后 provenance 缺语义字段 %r" % f
        assert prov.cmd, "cmd 必须是结构化来源"
        assert prov.status in {"final", "provisional", "pending", "delayed"}
        assert ds.data_date is None, "首行 N001 是名称，推不出数据日"
        assert prov.data_date is None, "推不出就 None（unknown ≠ 今天）"
    finally:
        c.close()


# ══════════════════════════════════════════════════════════════════════════
# 四、未登记列义：抛错，不静默
# ══════════════════════════════════════════════════════════════════════════
def test_mapped_on_unregistered_product_raises():
    """`mapped=True` 但产品没登记列义 → `ValueError`（静默给原始列会让人误以为映射生效）。

    ⚠️ 2026-10-28：例子从 `lrmx` 换成 `zjlx` —— 抓包挖掘第二轮把 `lrmx` 登记进
    `FIELD_MAP` 了（`CWServ.ph_zt_lrzt_lrmx` 的 `p1=00102` 9 列，两条 A 级加和恒等式），
    于是它不再是「未登记」的例子。本用例的语义不变：只挑一个**当前没有映射**的产品。
    """
    c = _client()
    try:
        with pytest.raises(ValueError) as ei:
            c.get("zjlx", mapped=True)
        msg = str(ei.value)
        assert "zjlx" in msg and "FIELD_MAP" in msg, msg
    finally:
        c.close()


# ══════════════════════════════════════════════════════════════════════════
# 五、参数契约：keyword-only + 默认 False
# ══════════════════════════════════════════════════════════════════════════
def test_mapped_is_keyword_only_and_defaults_to_false():
    """`mapped` 必须是 **keyword-only**（产品参数占位符也叫 mapped 时不撞名）且默认 False。"""
    params = inspect.signature(_CLS.get).parameters
    assert "mapped" in params
    assert params["mapped"].kind is inspect.Parameter.KEYWORD_ONLY, \
        "mapped 必须是 keyword-only（防止与产品参数占位符撞名）"
    assert params["mapped"].default is False, "默认必须是 False（原始列契约）"


# ══════════════════════════════════════════════════════════════════════════
# 六、异步壳：`AsyncClient.get` 必须把 `mapped` 原样透传给同步层
# ══════════════════════════════════════════════════════════════════════════
def test_async_get_forwards_the_mapped_flag():
    """两套 API 能力一致：`await AsyncClient.get(name, mapped=True)` 要真的透传。

    异步壳的 `get` 是 `async def get(self, name, **kw)` → `_run(self._c.get, name, **kw)`，
    所以 keyword-only 的 `mapped` 应该原样落到同步 `get` 上。桩一个同步客户端记录收到的
    kwargs（零网络、零线程外副作用）。
    """
    import asyncio

    from tdxdata.aio import AsyncClient

    seen: dict = {}

    class _FakeSync:
        def get(self, name, **kw):
            seen["name"], seen["kw"] = name, kw
            return "sentinel"

    async def run():
        return await AsyncClient(client=_FakeSync()).get("etf_t0", mapped=True)

    assert asyncio.run(run()) == "sentinel"
    assert seen["name"] == "etf_t0"
    assert seen["kw"] == {"mapped": True}, "mapped 没被透传到同步层：%r" % (seen["kw"],)
