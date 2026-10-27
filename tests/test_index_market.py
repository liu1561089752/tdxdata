# -*- coding: utf-8 -*-
r"""回归：市场码必须从 `parse_symbol` 一路透传到 `PBFXT` / `PBZST`。

背景（2026-10-26 手机通达信 + 东方财富双向核对发现）：
  `999999` 上证指数取不到，`get_bars(("sh","000001"))` 返回深市平安银行 ——
  根因是 `hq_pb.get_bars(code, ...)` 没有 market 参数，内部按前缀重推，
  把 `Client.get_bars` 里 `symbols.parse()` 算好的市场码丢掉了。

全部离线：不发任何网络请求。
"""
import sys

import pytest

import tdxdata
from tdxdata import limits, symbols
from tdxdata._engine import hq_pb
from tdxdata.client import Client


def _bare_client():
    """不建连接的 Client（只测参数编排）。"""
    c = Client.__new__(Client)
    return c


# ── 1. 代码 → 市场码推断 ────────────────────────────────────────────────
def test_index_market_inference():
    assert symbols.market_by_code("999999") == 1, "999999 上证指数必须是沪市"
    assert symbols.market_by_code("399001") == 0, "399001 深证成指必须是深市"
    assert symbols.market_by_code("399006") == 0
    assert symbols.market_by_code("600519") == 1
    assert symbols.market_by_code("000001") == 0
    assert symbols.market_by_code("920207") == 2
    assert symbols.market_by_code("900901") == 1


def test_board_index_market_is_shanghai():
    """板块指数 880xxx 是沪市（2026-10-26 实测 + 抓包 Setcode）。

    证据 1：`get_bars("sh880903")` → 561.57（水产品板块指数）
    证据 2：抓包里 `PBZSTDDE` 的 `{"Code":"880001","Setcode":1}`
    修前 `_PREFIX3["880"] = 2`（北交所），导致 `get_bars("880903")` 抛 EmptyResult。
    """
    assert symbols.market_by_code("880903") == 1
    assert symbols.market_by_code("880703") == 1
    assert symbols.market_by_code("880001") == 1
    # 北交所 6 位票仍走 8 兜底，不能被误改
    assert symbols.market_by_code("830799") == 2
    assert symbols.market_by_code("871981") == 2


def test_parse_symbol_keeps_explicit_market():
    assert symbols.parse("999999") == (1, "999999")
    assert symbols.parse("sh999999") == (1, "999999")
    assert symbols.parse(("sh", "999999")) == (1, "999999")
    assert symbols.parse(("sh", "000001")) == (1, "000001")
    assert symbols.parse("sz000001") == (0, "000001")


# ── 2. 参数构造真的把 field3 写成市场码 ─────────────────────────────────
def test_args_pbfxt_market_field():
    """field3 = varint 市场码 → 编码里应出现 `18 01`（沪）或 `18 00`（深）。"""
    hu = hq_pb._args_pbfxt("999999", 101, 10, 0, 1, market=1)
    shen = hq_pb._args_pbfxt("999999", 101, 10, 0, 1, market=0)
    assert b"\x18\x01" in hu
    assert b"\x18\x00" in shen
    assert hu != shen
    # 不给 market 时按代码推断（999999 → 沪）
    inferred = hq_pb._args_pbfxt("999999", 101, 10, 0, 1)
    assert inferred == hu


def test_args_pbzst_market_field():
    hu = hq_pb._args_pbzst("999999", market=1)
    assert b"\x18\x01" in hu
    assert hq_pb._args_pbzst("999999") == hu


# ── 3. Client 把市场码透传下去 ──────────────────────────────────────────
def test_client_get_bars_passes_market(monkeypatch):
    seen = []

    def fake_bars(code, period="day", count=120, offset=0, adjust=1, market=None):
        seen.append((code, market))
        return ["Data", "Close"], [{"Data": "20261026", "Close": "3697.52"}]

    monkeypatch.setattr(hq_pb, "get_bars", fake_bars)
    c = _bare_client()
    c.get_bars("sh000001", count=1)
    c.get_bars("999999", count=1)
    c.get_bars("sz000001", count=1)
    assert seen[0] == ("000001", 1), seen      # 显式 sh 必须生效
    assert seen[1] == ("999999", 1), seen      # 前缀表推断
    assert seen[2] == ("000001", 0), seen


def test_client_get_bars_passes_market_when_paging(monkeypatch):
    seen = []

    def fake_bars(code, period="day", count=120, offset=0, adjust=1, market=None):
        seen.append((offset, market))
        base = 500 if offset == 0 else 0
        rows = [{"Data": "2026%04d" % (base + i), "Close": "1.00"}
                for i in range(count)]
        return ["Data", "Close"], rows

    monkeypatch.setattr(hq_pb, "get_bars", fake_bars)
    c = _bare_client()
    c.get_bars("sh000001", count=hq_pb.MAX_ROWS + 10)
    assert len(seen) >= 2, seen
    assert all(m == 1 for _o, m in seen), seen


def test_client_get_minutes_passes_market(monkeypatch):
    seen = []

    def fake_minutes(code, market=None):
        seen.append((code, market))
        return ["Minute", "Now"], [{"Minute": "570", "Now": "3697.52"}]

    monkeypatch.setattr(hq_pb, "get_minutes", fake_minutes)
    _bare_client().get_minutes(("sh", "000001"))
    assert seen == [("000001", 1)], seen


# ── 4. 涨跌停：转债 ±20% + 3 位小数取整（2026-10-26 实测值）────────────
@pytest.mark.parametrize("code,prev,up,down", [
    ("600519", 1335.00, 1468.500, 1201.500),
    ("000001", 11.20, 12.320, 10.080),
    ("300750", 237.60, 285.120, 190.080),
    ("688981", 104.10, 124.920, 83.280),
    ("920207", 28.34, 36.840, 19.840),
    ("900901", 0.569, 0.626, 0.512),
    ("113054", 120.384, 144.461, 96.307),
    ("510300", 4.178, 4.596, 3.760),
])
def test_price_limits_verified_values(code, prev, up, down):
    assert limits.price_limits(prev, code) == (up, down)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
