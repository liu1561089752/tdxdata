# -*- coding: utf-8 -*-
"""`Tick` 的字段审计（阶段 4）：**"看起来有、实际永远空"必须消失**。

两种死法、两种处理（Lead 拍板）
--------------------------------
| 字段 | 处理 | 理由 |
|---|---|---|
| `trade_count` | **删除** | `PBTickEx` 一行就是**一笔**成交，协议里**根本不存在**"笔数"字段。这不是"暂不可用"，是**概念不存在**；旧代码填 `0` 是凭空造数 |
| `bs_flag` | **保留 + 声明式不可用** | 概念真实、用户会找；但服务端没有可判定字段。删掉会让既有读 `.bs_flag` 的脚本 `AttributeError`（为一个拿不到的东西制造破坏），而 `None` 是诚实的"不知道" |

`bs_flag` 的"不可用"必须**机器可见**：写在 `models.UNPROVIDED_TICK_FIELDS` 里，
并由本文件对着**真实形状的样本**断言「它确实恒为 None」。这样一旦服务端哪天开始
给这个字段，测试会红 —— 逼我们更新声明。死字段 → 有检查的不可用。

实证依据（2026-10-27，600519，1200 笔真实逐笔）
----------------------------------------------
* `bs_flag`：`raw["InOutFlag"]` 的 0/1 与"相对上一笔涨跌"只有 65.9%/76.8% 一致，
  **相关 ≠ 实证**，映射错 = 整批方向反了；
* `HKTTFlag` 是 `InOutFlag` 的**单字节回声**（300/300 相等），`InOutFlag=5` 的 20 笔
  全在 15:05–15:30 且成交价恒为收盘价 = 盘后固定价格交易；
* `HKInOutFlag` 实测恒 `"0"`；`VolInStockDiff` 与 `NowVol` 只 77/300 相等（不是重复列）。
"""
from __future__ import annotations

import dataclasses
import inspect
import os

import pytest

import tdxdata
from tdxdata import models
from tdxdata.models import Tick

_CLS = getattr(tdxdata, "Client")
_HERE = os.path.dirname(os.path.abspath(__file__))
_README = os.path.join(os.path.dirname(_HERE), "README.md")


def _readme_block() -> str:
    with open(_README, encoding="utf-8") as fh:
        text = fh.read()
    begin = "<!-- BEGIN: tick_fields"
    end = "<!-- END: tick_fields -->"
    i = text.find(begin)
    j = text.find(end)
    assert i >= 0, "README 里找不到 tick_fields 标记块"
    assert j > i, "README 里的 tick_fields 标记块不成对"
    return text[text.index("\n", i) + 1:j].strip("\n")


# ══════════════════════════════════════════════════════════════════════════
# 一、字段表与代码同源
# ══════════════════════════════════════════════════════════════════════════
def test_readme_table_is_generated_from_code():
    """README 那一节必须是 `Tick.as_markdown_table()` 的逐字副本（禁手改）。"""
    assert _readme_block() == Tick.as_markdown_table(), (
        "README 的 Tick 字段表与 models.Tick.as_markdown_table() 漂移了 —— "
        "请用 as_markdown_table() 重新生成，别手改")


def test_every_dataclass_field_is_documented():
    fields = [f.name for f in dataclasses.fields(Tick)]
    documented = [row[0] for row in Tick.FIELDS_DOC]
    assert fields == documented, (
        "Tick 的字段变了但字段表没跟上：\n  代码里有但没写文档：%s\n  文档里有但代码里没有：%s"
        % ([f for f in fields if f not in documented],
           [d for d in documented if d not in fields]))


def test_no_field_is_silently_always_none():
    """**任何**字段都要么有值、要么在表里明写"不提供"。

    判定依据取代码里的声明（`FIELDS_DOC` 的"有值？"列 + `UNPROVIDED_TICK_FIELDS`），
    不靠人肉记忆。
    """
    for name, _mean, has, why in Tick.FIELDS_DOC:
        assert has.strip() in ("有", "**不提供（声明式不可用）**"), (name, has)
        if "不提供" in has:
            assert name in models.UNPROVIDED_TICK_FIELDS, (
                "%s 在字段表里标了『不提供』，却没进 UNPROVIDED_TICK_FIELDS "
                "（那就又变回只有文档知道了）" % name)
            assert len(why) > 8, "%s 标了不提供却没写依据" % name


def test_bs_flag_row_points_at_the_constant():
    text = Tick.as_markdown_table()
    line = [ln for ln in text.splitlines() if ln.startswith("| `bs_flag`")]
    assert line and "UNPROVIDED_TICK_FIELDS" in line[0], line


# ══════════════════════════════════════════════════════════════════════════
# 二、`trade_count`：**保留字段 + 声明式不可用**（阶段 4 最终判定）
# ══════════════════════════════════════════════════════════════════════════
# 为什么不是删掉：冻结的黄金样本 `ticks.json` 证明**既有行为本来就是 `null`**
# （不是 0），所以删字段**没有任何诚实性增益**，却要为它赔一次样本重录，并让既有
# 读 `t.trade_count` 的脚本 `AttributeError`。要求是"不许有永远为 None 却没人管
# 的字段"，而"声明式不可用 + 服务端一旦给值就报警"比"删掉"更强（删掉是静默的）。
def test_trade_count_stays_and_is_declared_unavailable():
    names = [f.name for f in dataclasses.fields(Tick)]
    assert "trade_count" in names, "阶段 4 最终判定：保留字段"
    assert names.index("trade_count") < names.index("bs_flag"), \
        "字段顺序保持原样（trade_count 在 bs_flag 之前）"
    assert Tick(market=1, code="600519").trade_count is None, "必须是 None，不是 0"
    assert "trade_count" in models.UNPROVIDED_TICK_FIELDS, \
        "保留就必须声明，否则又变成没人管的死字段"
    reason = models.UNPROVIDED_TICK_FIELDS["trade_count"]
    assert "一笔" in reason and "len(" in reason, \
        "依据要写具体：一行就是一笔，要数笔数用 len(ticks)"


def test_trade_count_docs_say_declared_unavailable_not_deleted():
    """文档口径必须与实现一致：**保留 + 声明不可用**，不许写成"已删除"。"""
    root = os.path.dirname(_HERE)
    targets = [os.path.join("tdxdata", "models.py"), "README.md"]
    for path in targets:
        with open(os.path.join(root, path), encoding="utf-8") as fh:
            text = fh.read()
        if path.endswith("README.md") and "| `trade_count` |" not in text:
            continue      # README 的字段表由代码生成，未收录也不算错
        assert "trade_count" in text, "%s 里应交代 trade_count 的处置" % path


# ══════════════════════════════════════════════════════════════════════════
# 三、`bs_flag`：声明式不可用 + 真实样本复核
# ══════════════════════════════════════════════════════════════════════════
def test_bs_flag_is_declared_unavailable():
    assert "bs_flag" in models.UNPROVIDED_TICK_FIELDS
    reason = models.UNPROVIDED_TICK_FIELDS["bs_flag"]
    assert "65.9" in reason and "76.8" in reason, "依据要写具体数字，不许含糊"


def _fake_tick_page():
    """`hq_extra.ticks()` 的返回形态（一行 = 一笔，全是字符串）。"""
    rows = [
        {"Second": "40978", "Now": "1356.000000", "NowVol": "4",
         "VolInStockDiff": "4", "InOutFlag": "0", "HKTTFlag": "", "HKInOutFlag": "0"},
        {"Second": "40981", "Now": "1355.900024", "NowVol": "1",
         "VolInStockDiff": "3", "InOutFlag": "1", "HKTTFlag": "\x01",
         "HKInOutFlag": "0"},
    ]
    return {"anchor": (1995, 2095), "rows": rows}


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
    return c


def test_bs_flag_is_none_on_real_shaped_sample(monkeypatch):
    """**这条就是"自动提醒机制"**：喂真实形态的行，`bs_flag` 必须 100% 为 `None`。

    服务端哪天开始给方向字段 → 这里会红 → 逼我们更新
    `models.UNPROVIDED_TICK_FIELDS` 与 README 的字段表。
    """
    from tdxdata._engine import hq_extra

    monkeypatch.setattr(hq_extra, "ticks",
                        lambda code, market, anchor="0", count=100: _fake_tick_page())
    out = _bare_client().get_ticks("600519", count=2)
    assert len(out) == 2
    n_none = sum(1 for t in out if t.bs_flag is None)
    assert n_none == len(out), "bs_flag 不再恒 None 了 —— 服务端开始给字段了？" \
                              "请更新 UNPROVIDED_TICK_FIELDS 并复核 Python 侧映射"


def test_get_ticks_leaves_real_fields_populated(monkeypatch):
    from tdxdata._engine import hq_extra

    monkeypatch.setattr(hq_extra, "ticks",
                        lambda code, market, anchor="0", count=100: _fake_tick_page())
    out = _bare_client().get_ticks("600519", count=2)
    for t in out:
        for name in ("market", "code", "time", "price", "vol"):
            assert getattr(t, name) is not None, (name, t)


def test_get_ticks_keeps_raw_columns_untouched(monkeypatch):
    """未定名的原始列**一个不许丢**（用户自己判，库不解释也不删）。"""
    from tdxdata._engine import hq_extra

    monkeypatch.setattr(hq_extra, "ticks",
                        lambda code, market, anchor="0", count=100: _fake_tick_page())
    out = _bare_client().get_ticks("600519", count=2)
    assert set(out[0].raw) == {"Second", "Now", "NowVol", "VolInStockDiff",
                              "InOutFlag", "HKTTFlag", "HKInOutFlag"}
    assert out[0].raw["InOutFlag"] == "0"
    assert out[1].raw["HKTTFlag"] == "\x01"      # 单字节回声，原样保留


def test_get_ticks_docstring_no_longer_claims_bs_flag_is_2():
    """旧 docstring 写「`bs_flag` 恒等于 2」—— 与实现（None）相反，必须已改掉。"""
    src = inspect.getsource(_CLS.get_ticks)
    assert "恒等于 2" not in src.replace("曾写「`bs_flag` 恒等于 2", "")
    assert "不提供" in src and "bs_flag" in src


def test_tick_time_is_converted_from_seconds():
    """`Second` 是**当日秒数**（40978 → 11:22:58），不是 HHMMSS。"""
    from tdxdata.client import _tick_from_row

    got = _tick_from_row({"Second": "40978", "Now": "1356.0", "NowVol": "4"},
                         market=1, code="600519")
    assert got.time == "11:22:58"


def test_tick_field_order_is_stable():
    """`slots=True` 的 dataclass 字段顺序对外可见（位置参数），不许随便动。"""
    names = [f.name for f in dataclasses.fields(Tick)]
    # 阶段 4 最终判定：保留 `trade_count`（声明式不可用），位置**恢复原样**
    # （trade_count 在 bs_flag 之前）—— 位置参数兼容性优先。
    assert names == ["market", "code", "time", "price", "vol",
                     "trade_count", "bs_flag", "raw"], names
