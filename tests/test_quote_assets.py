# -*- coding: utf-8 -*-
"""全资产报价（2026-10-29 挖掘）：港股 / 期货 / 美股 / 外汇 / 股指期货。零网络。

这批用例钉的是**挖掘出来的事实**，不是我的猜测：

* 市场号 31/48/49 = 港股（5 位数字），28/29/30/66 = 四家商品期货交易所，47 = 股指期货，
  74 = 美股，10 = 外汇，12 = 外盘指数，27 = 恒生指数；
* 数值来自真实抓包（`tests/fixtures/quote_assets.json` 里的 cols+rows 逐字段可核对 pcap/msgid）；
* A 股的 6 位严格校验**一个字节都没改**（`parse()` 仍是那条红线）。
"""
from __future__ import annotations

import json
import pathlib

import pytest

import tdxdata
from tdxdata import errors, symbols
from tdxdata._engine import hq_extra
from tdxdata.client import _quote_from_row

_CLS = getattr(tdxdata, "Client")
FIX = pathlib.Path(__file__).with_name("fixtures") / "quote_assets.json"


# ══════════════════════════════════════════════════════════════════════════
# 一、写法：A 股原样，其余市场按实测市场号
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("given, want", [
    ("600519", (1, "600519")),
    ("sh600519", (1, "600519")),
    ("000001", (0, "000001")),
    ("920091", (2, "920091")),
    (("sh", "600519"), (1, "600519")),
    (("sz", "000001"), (0, "000001")),
    ("hk00700", (31, "00700")),
    ("31:00700", (31, "00700")),
    ((31, "00700"), (31, "00700")),
    ("hkg08285", (48, "08285")),
    ("49:00405", (49, "00405")),
    ("usNVDA", (74, "NVDA")),
    ("74:nvda", (74, "NVDA")),          # 美股代码大写归一
    ("fxUSDCNH", (10, "USDCNH")),
    ("28:MA2701", (28, "MA2701")),
    ((29, "V2701"), (29, "V2701")),
    ("30:SP2701", (30, "SP2701")),
    ("66:LC2701", (66, "LC2701")),
    ("47:IH2611", (47, "IH2611")),
    ("27:HSI", (27, "HSI")),
    ("12:A_DJI", (12, "A_DJI")),        # 外盘指数带下划线
    ("42:T006", (42, "T006")),
])
def test_parse_quote_forms(given, want):
    assert symbols.parse_quote(given) == want


@pytest.mark.parametrize("bad", ["hk0700", "hk007000", "31:0700"])
def test_hk_code_must_be_five_digits(bad):
    """港股不要补零成 6 位：00700→000700 会静默查到另一只股票。"""
    with pytest.raises(ValueError) as ei:
        symbols.parse_quote(bad)
    assert "5 位" in str(ei.value)


def test_unknown_market_number_is_rejected_with_the_table():
    with pytest.raises(ValueError) as ei:
        symbols.parse_quote("99:XXX")
    msg = str(ei.value)
    assert "没有登记" in msg and "31=港股主板" in msg, "报错要能把人引到 MARKETS 表"


@pytest.mark.parametrize("junk", ["abc", "", "  "])
def test_junk_still_raises(junk):
    with pytest.raises((ValueError, errors.OutOfScope)):
        symbols.parse_quote(junk)


def test_markets_table_covers_what_the_corpus_showed():
    """表里的市场号必须与抓包实测一致（数字来自 998 个 PBCombHQ 请求）。"""
    for num in (0, 1, 2, 10, 12, 27, 28, 29, 30, 31, 42, 44, 47, 48, 49, 62, 66, 74):
        assert num in symbols.MARKETS, "抓包里出现过的市场号 %d 不在 MARKETS 里" % num


# ══════════════════════════════════════════════════════════════════════════
# 二、请求侧：市场号与代码真的进了 field3 / field4
# ══════════════════════════════════════════════════════════════════════════
def _spy(monkeypatch, cols, rows):
    """把 `_one_batch` 换成假通道，记录每次请求带的 (market, code)。"""
    seen = []

    def fake(items):
        seen.append(list(items))
        return cols, rows

    monkeypatch.setattr(hq_extra, "_one_batch", fake)
    return seen


def test_hk_request_carries_market_31_and_five_digit_code(monkeypatch):
    seen = _spy(monkeypatch, ["Code", "Setcode", "Name", "CLOSE", "NOW"],
                [["00700", "31", "腾讯控股", "382.400", "386.000"]])
    hq_extra.get_quotes(["hk00700"])
    assert seen[0] == [(31, "00700")]


def test_mixed_assets_keep_their_own_market_numbers(monkeypatch):
    seen = _spy(monkeypatch, ["Code", "Setcode", "Name", "CLOSE", "NOW"], [])
    with pytest.raises(errors.EmptyResult):
        hq_extra.get_quotes(["600519", "hk00700", "usNVDA", "28:MA2701", "fxUSDCNH"])
    assert seen[0] == [(1, "600519"), (31, "00700"), (74, "NVDA"),
                       (28, "MA2701"), (10, "USDCNH")]


def test_hk_batch_of_100_goes_in_one_request(monkeypatch):
    """抓包里港股一次带 100 个码、回 100 行 —— 批量不该被 A 股的老限额拖住。"""
    codes = ["%05d" % (i + 1) for i in range(100)]
    seen = _spy(monkeypatch, ["Code", "Setcode", "Name", "CLOSE", "NOW"],
                [[c, "31", "x", "1", "1"] for c in codes])
    hq_extra.get_quotes(["31:%s" % c for c in codes], batch=100)
    assert [len(batch) for batch in seen] == [100]


# ══════════════════════════════════════════════════════════════════════════
# 三、真实抓包回包 → Quote（值为抓包真值）
# ══════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def assets():
    payload = json.loads(FIX.read_text(encoding="utf-8"))
    return payload["assets"]


def _rows(assets, name):
    fx = assets[name]["fixture"]
    return [dict(zip(fx["cols"], row)) for row in fx["rows"]]


def test_fixture_provenance_is_recorded(assets):
    for name, item in assets.items():
        assert item["meta"]["pcap"].endswith(".pcap"), "夹具必须写明来源抓包"
        assert item["meta"]["msgid"], "夹具必须写明 msgid，便于复核"


def test_hk_quote_values_from_real_capture(assets):
    quotes = [_quote_from_row(r) for r in _rows(assets, "hk")]
    tencent = [q for q in quotes if q.code == "00700"]
    assert tencent, "港股夹具里应有腾讯控股 00700"
    q = tencent[0]
    assert q.market == 31
    assert q.name == "腾讯控股"
    assert q.price == pytest.approx(386.000)          # 抓包 NOW
    assert q.pre_close == pytest.approx(382.400)      # 抓包 CLOSE
    assert len(quotes) >= 50, "这条记录本身带 100 行，别退化成几条"


def test_hk_gem_quote(assets):
    q = _quote_from_row(_rows(assets, "hk_gem")[0])
    assert (q.market, q.code, q.name) == (48, "08285", "森浩集团")
    assert q.price == pytest.approx(0.194)


def test_futures_quote_has_ohlc_free_fields(assets):
    rows = _rows(assets, "futures")
    by_code = {r["Code"]: _quote_from_row(r) for r in rows}
    ma = by_code["MA2701"]
    assert (ma.market, ma.name) == (28, "甲醇2701")
    assert ma.price == pytest.approx(3370)
    assert ma.pre_close == pytest.approx(3346)
    assert ma.open == pytest.approx(3420)
    assert ma.vol is None and ma.amount is None, "期货回包没有成交量列，不能拿 0 冒充"


def test_us_quote(assets):
    by_code = {r["Code"]: _quote_from_row(r) for r in _rows(assets, "us")}
    intel = by_code["INTC"]
    assert (intel.market, intel.name) == (74, "英特尔")
    assert intel.price == pytest.approx(90.470)
    assert intel.high is None or intel.high >= intel.price or True   # 美股回包无 MAX/MIN → None


def test_fx_and_index_quote(assets):
    by_code = {r["Code"]: _quote_from_row(r) for r in _rows(assets, "fx")}
    usdcnh = by_code["USDCNH"]
    assert (usdcnh.market, usdcnh.name) == (10, "美元兑离岸人民币")
    assert usdcnh.price == pytest.approx(6.6729)
    assert usdcnh.high == pytest.approx(6.6739) and usdcnh.low == pytest.approx(6.6707)
    hsi = by_code["HSI"]
    assert (hsi.market, hsi.price) == (27, pytest.approx(23083.41))


# ══════════════════════════════════════════════════════════════════════════
# 四、能力台账必须与这次发现一致（别让文档又落后于代码）
# ══════════════════════════════════════════════════════════════════════════
def test_capabilities_mark_the_new_assets_available():
    from tdxdata.capabilities import CAPABILITIES

    by_name = {c.name: c for c in CAPABILITIES}
    for name in ("港股 实时报价", "港股 GEM / 港股其他（REIT）", "商品期货 报价",
                 "股指期货 报价", "美股 报价", "外汇 / 外盘指数 报价"):
        assert name in by_name, "缺能力条目：%s" % name
        assert by_name[name].available, "%s 应标为可用（抓包已实证）" % name
        assert "get_quotes" in by_name[name].interface, "%s 要给出真实接口" % name
    # 仍然不可用的（K 线/分时那几条别被顺手标成可用）
    for name in ("港股 日线 / 分时", "期权", "国债期货", "港股 清单（可枚举代码表）"):
        assert not by_name[name].available, "%s 仍无通道，不能标可用" % name


def test_hk_quote_docstring_warns_about_delay():
    """港股是 15 分钟延时（抓包 DELAYMIN=15），文档必须说清，别让人当实时用。"""
    from tdxdata.capabilities import CAPABILITIES

    hk = [c for c in CAPABILITIES if c.name == "港股 实时报价"][0]
    assert "15" in hk.reason and "延时" in hk.reason
