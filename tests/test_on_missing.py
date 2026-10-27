# -*- coding: utf-8 -*-
"""批量取数的缺失策略 `on_missing`（阶段 3）—— **三态都必须可断言**。

为什么这个参数值得一套专门用例
------------------------------
服务端对无效代码**静默不返回**，所以"少了几只"是常态。旧行为只有一种：
抛 `EmptyResult` 并点名。这在"我就想要能拿到的那几只"时没法用 ——
于是有人会去 `try/except` 包一层再 `continue`，然后**再也看不见缺了谁**。

三态的语义边界（本文件逐条钉住）
--------------------------------
| 值 | 抛不抛 | 缺失去哪了 | 什么时候用 |
|---|---|---|---|
| `raise`（默认） | 抛 `EmptyResult` | 消息里点名 | 少一只就说明参数错了，不能继续 |
| `skip` | 不抛 | `meta["missing"]` 结构化 | 批量轮询，缺的码本来就可能退市/停牌 |
| `report` | 不抛 | 同上 + `warnings.warn` 一次 | 同上，但想让缺失**在日志里显形** |

三条不许退让的性质
------------------
1. **默认路径一个字节都不许变**：`raise` 的异常文案与旧版逐字相同（黄金门禁钉着它），
   连**调用下游的签名**都不许多一个 kwarg（老的打桩只认 `batch`）。
2. **`skip` ≠ 静默**：缺失清单一定在 `meta` 里，可数、可断言、可入库。
3. **非法取值在发请求之前就炸**（`ParamError`）—— 不能白等一轮 2.2 秒。
"""
from __future__ import annotations

import warnings

import pytest

import tdxdata
from tdxdata import _core, errors
from tdxdata.models import Quotes

_CLS = getattr(tdxdata, "Client")


def _bare_client():
    """不建连的裸 Client（`_pb_host` / `_now` 都走本地路径，零网络）。"""
    from tdxdata._engine import datacenter as dc

    c = _CLS.__new__(_CLS)
    c._dc_mod = dc
    c._probe_allowed = False
    c._clock_probe = False
    c._clock = None
    c._last_call = {}
    c._in_cal = False
    c._strict_symbols = False
    return c


#: 服务端"有数据"的码（其余一律静默不返回，模拟真服务端的脾气）
SERVED = {"600519", "000001", "600036", "999999"}


def _install_engine(monkeypatch, *, served=None, served_only_on_retry=None):
    """把 `hq_extra._one_batch` 换成"只认某些码"的假通道。

    这是**最底层**的桩（`PBCombHQ` 一次请求的收发就在这一层），
    所以 `hq_extra.get_quotes` 的成批/补发/缺失判定全是真代码。
    """
    from tdxdata._engine import hq_extra

    served = SERVED if served is None else set(served)
    served_retry = set(served_only_on_retry or ())
    seen = {"calls": 0}
    asked: set = set()

    cols = ["Code", "Setcode", "NAME", "CLOSE", "Now", "HqDate", "HqTime"]

    def fake(items):
        """第一次问某个码 = **成批阶段**；再问同一个码 = **补发阶段**。

        不能用"列表长度"区分：原始请求只有 1 个码时也是成批阶段。
        """
        seen["calls"] += 1
        rows = []
        for mkt, code in items:
            first_time = code not in asked
            asked.add(code)
            allow = served if first_time else (served | served_retry)
            if code in allow:
                rows.append([code, str(mkt), "名" + code, "10.0", "11.0",
                             "20261027", "15:30:03"])
        return cols, rows

    monkeypatch.setattr(hq_extra, "_one_batch", fake)
    return seen


# ══════════════════════════════════════════════════════════════════════════
# 一、默认 raise：**行为与旧版一字不差**
# ══════════════════════════════════════════════════════════════════════════
def test_default_raise_keeps_the_exact_old_message(monkeypatch):
    _install_engine(monkeypatch)
    c = _bare_client()
    with pytest.raises(errors.EmptyResult) as ei:
        c.get_quotes(["600519", "881999"])
    msg = str(ei.value)
    assert msg.startswith("PBCombHQ 取不到这些代码（服务端静默不返回，单发也没有）：881999。"), \
        "默认文案必须与旧版一致（黄金样本钉着它）"
    assert "8 开头分两类" in msg, "原有那段 8 开头的解释不许丢"


def test_default_mode_is_raise():
    import inspect

    sig = inspect.signature(_CLS.get_quotes)
    assert sig.parameters["on_missing"].default == "raise"


def test_default_path_does_not_pass_extra_kwargs_downstream(monkeypatch):
    """默认路径**不许**多传 `on_missing` 给引擎层（老的单测/桩只认 `batch`）。

    这不是洁癖：`on_missing="raise"` 时引擎层的行为完全没变，
    多传一个 kwarg 只会把所有"只写了 batch 的桩"打成 TypeError。
    """
    from tdxdata._engine import hq_extra

    got = {}

    def fake(symbols_, batch=20, **kw):
        got.update(kw)
        return ["Code"], [["600519", "1", "贵州茅台", "10", "11", "20261027", "15:30:03"]]

    monkeypatch.setattr(hq_extra, "get_quotes", fake)
    c = _bare_client()
    out = c.get_quotes(["600519"])
    assert len(out) == 1
    assert "on_missing" not in got, "默认路径不许把新参数漏下去"


# ══════════════════════════════════════════════════════════════════════════
# 二、skip：不抛，但**缺失必须结构化地留下**
# ══════════════════════════════════════════════════════════════════════════
def test_skip_returns_partial_and_lists_missing(monkeypatch):
    _install_engine(monkeypatch)
    c = _bare_client()
    out = c.get_quotes(["600519", "000001", "600036", "999999", "881999"],
                       on_missing="skip")
    assert isinstance(out, Quotes)
    assert [q.code for q in out] == ["600519", "000001", "600036", "999999"], \
        "按请求顺序返回能拿到的那些"
    assert out.meta["requested"] == 5
    assert out.meta["returned"] == 4
    miss = out.meta["missing"]
    assert isinstance(miss, list) and len(miss) == 1
    item = miss[0]
    assert set(item) == {"symbol", "market", "code", "reason"}, \
        "缺失条目的键集必须固定（下游要按名取值）"
    assert item["code"] == "881999"
    assert item["symbol"] == "sh881999"
    assert item["market"] == 1
    assert item["reason"] == "服务端未返回"


def test_skip_is_not_silent(monkeypatch):
    """`skip` 只是"不抛"，**绝不是"不说"**：`missing` 必须在 meta 里。"""
    _install_engine(monkeypatch)
    out = _bare_client().get_quotes(["600519", "881999"], on_missing="skip")
    assert "missing" in out.meta and out.meta["missing"]


def test_skip_all_missing_returns_empty_not_error(monkeypatch):
    """全缺时返回**空** `Quotes`（不抛），且 `missing` 列出全部。"""
    _install_engine(monkeypatch, served=set())
    out = _bare_client().get_quotes(["600519", "881999"], on_missing="skip")
    assert len(out) == 0
    assert out.meta["returned"] == 0
    assert out.meta["requested"] == 2
    assert {d["code"] for d in out.meta["missing"]} == {"600519", "881999"}


def test_skip_no_missing_gives_empty_list(monkeypatch):
    _install_engine(monkeypatch)
    out = _bare_client().get_quotes(["600519", "000001"], on_missing="skip")
    assert out.meta["missing"] == []
    assert out.meta["returned"] == out.meta["requested"] == 2


# ══════════════════════════════════════════════════════════════════════════
# 三、report：同 skip，但**警告恰好一次**
# ══════════════════════════════════════════════════════════════════════════
def test_report_is_skip_plus_exactly_one_warning(monkeypatch):
    _install_engine(monkeypatch)
    c = _bare_client()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = c.get_quotes(["600519", "881999"], on_missing="report")
    assert len(out) == 1
    assert out.meta["missing"][0]["code"] == "881999"
    ours = [w for w in caught if "881999" in str(w.message)]
    assert len(ours) == 1, "只许 warn 一次（引擎层也 warn 就变成两条同义噪声）"


def test_skip_does_not_warn(monkeypatch):
    _install_engine(monkeypatch)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _bare_client().get_quotes(["600519", "881999"], on_missing="skip")
    ours = [w for w in caught if "881999" in str(w.message)]
    assert not ours, "skip 不警告"


# ══════════════════════════════════════════════════════════════════════════
# 四、非法取值 → ParamError，且**在发请求之前**
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("bad", ["ignore", "", "warn", "partial", None, 123])
def test_invalid_mode_raises_param_error_before_network(bad, monkeypatch):
    seen = _install_engine(monkeypatch)
    c = _bare_client()
    with pytest.raises(errors.ParamError) as ei:
        c.get_quotes(["600519"], on_missing=bad)
    assert "on_missing" in str(ei.value)
    assert tuple(seen) and seen["calls"] == 0, "非法取值必须在发请求之前就炸"


def test_mode_is_case_and_space_insensitive():
    assert _core.check_on_missing(" SKIP ") == "skip"
    assert _core.check_on_missing("Report") == "report"
    assert _core.check_on_missing("raise") == "raise"


# ══════════════════════════════════════════════════════════════════════════
# 五、下沉层（`hq_extra.get_quotes`）：只决定抛不抛，"缺了谁"由上层算
# ══════════════════════════════════════════════════════════════════════════
def test_engine_layer_skip_returns_partial(monkeypatch):
    from tdxdata._engine import hq_extra

    _install_engine(monkeypatch)
    cols, rows = hq_extra.get_quotes(["600519", "881999"], on_missing="skip")
    assert [r[0] for r in rows] == ["600519"]


def test_engine_layer_raise_still_raises(monkeypatch):
    from tdxdata._engine import hq_extra

    _install_engine(monkeypatch)
    with pytest.raises(errors.EmptyResult):
        hq_extra.get_quotes(["600519", "881999"])


def test_retry_missing_is_orthogonal_to_on_missing(monkeypatch):
    """`retry_missing` 管"要不要为缺的码补一次"，`on_missing` 管"补完还缺怎么办"。"""
    from tdxdata._engine import hq_extra

    # 成批时不给、单发时给 → 只有补发才能拿到
    _install_engine(monkeypatch, served=set(), served_only_on_retry={"600519"})
    cols, rows = hq_extra.get_quotes(["600519"], retry_missing=True, on_missing="skip")
    assert [r[0] for r in rows] == ["600519"], "补发成功就必须拿到"

    _install_engine(monkeypatch, served=set(), served_only_on_retry={"600519"})
    cols, rows = hq_extra.get_quotes(["600519"], retry_missing=False, on_missing="skip")
    assert rows == [], "不补发就拿不到（证明两个开关互不替代）"


# ══════════════════════════════════════════════════════════════════════════
# 六、`resolve_missing` 单元性质
# ══════════════════════════════════════════════════════════════════════════
def test_resolve_missing_keeps_request_order_and_shape():
    got = {"600519"}
    miss = _core.resolve_missing([(1, "600519"), (0, "000001"), (2, "920091")], got,
                                 on_missing="skip")
    assert [d["code"] for d in miss] == ["000001", "920091"], "按请求顺序"
    assert miss[0]["symbol"] == "sz000001"
    assert miss[1]["market"] == 2


def test_resolve_missing_raise_uses_given_message():
    with pytest.raises(errors.EmptyResult) as ei:
        _core.resolve_missing([(1, "881999")], set(), raise_msg="自定义文案 881999")
    assert "自定义文案" in str(ei.value)


def test_quotes_missing_entries_are_json_serializable(monkeypatch):
    """缺失清单要能直接入库/进日志（不得塞非 JSON 类型）。"""
    import json

    _install_engine(monkeypatch)
    out = _bare_client().get_quotes(["600519", "881999"], on_missing="skip")
    text = json.dumps(out.meta["missing"], ensure_ascii=False)
    assert "881999" in text

