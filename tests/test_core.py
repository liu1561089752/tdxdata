# -*- coding: utf-8 -*-
"""核心逻辑的**离线**单元测试（不连服务器）。

覆盖：周期映射、时间字段、标的解析、域分类、产品表、模型、占位符替换。
跑法：python tests/test_core.py   或   pytest tests/test_core.py -q
"""
from __future__ import annotations

import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tdxdata                                                     # noqa: E402
from tdxdata import client as CL, domains, symbols                 # noqa: E402
from tdxdata.enums import KlinePeriod                              # noqa: E402


# ── 周期映射（曾经的真 bug：MIN_60 → "min_60" 会崩）───────────────────────
def test_period_enum_mapping():
    want = {
        KlinePeriod.MIN_1: "1m", KlinePeriod.MIN_5: "5m",
        KlinePeriod.MIN_15: "15m", KlinePeriod.MIN_30: "30m",
        KlinePeriod.MIN_60: "60m", KlinePeriod.DAY: "day",
        KlinePeriod.WEEK: "week", KlinePeriod.MONTH: "month",
        KlinePeriod.SEASON: "season",
    }
    for p, s in want.items():
        got = CL.Client._period_str(p)
        assert got == s, "%s → %r，期望 %r" % (p.name, got, s)


def test_period_string_passthrough():
    for s in ("day", "week", "60m", "1m", "month"):
        assert CL.Client._period_str(s) == s


def test_period_dead_raises():
    for bad in (KlinePeriod.YEAR, KlinePeriod.YEAR_ALT, "year"):
        try:
            CL.Client._period_str(bad)
        except tdxdata.NotSupportedError:
            continue
        raise AssertionError("%r 应抛 NotSupportedError" % (bad,))


def test_period_unknown_raises():
    for bad in ("3m", "tick", "hour"):
        try:
            CL.Client._period_str(bad)
        except tdxdata.NotSupportedError:
            continue
        raise AssertionError("%r 应抛 NotSupportedError" % (bad,))


# ── 时间字段 ─────────────────────────────────────────────────────────────
def test_hhmm():
    assert CL._hhmm(54000) == "15:00"
    assert CL._hhmm(37800) == "10:30"
    assert CL._hhmm("41400") == "11:30"
    assert CL._hhmm(0) == ""          # 日线 Second=0，不该显示 00:00
    assert CL._hhmm(None) == ""
    assert CL._hhmm("abc") == ""
    assert CL._hhmm(-1) == ""
    assert CL._hhmm(86400) == ""


def test_intraday_set():
    assert "60m" in CL._INTRADAY and "day" not in CL._INTRADAY


# ── 标的解析 ─────────────────────────────────────────────────────────────
def test_parse_symbol_variants():
    cases = [("600519", (1, "600519")), ("sh600519", (1, "600519")),
             ("SH600519", (1, "600519")), ("600519.SH", (1, "600519")),
             ("000001", (0, "000001")), ("sz000001", (0, "000001")),
             ("920091", (2, "920091")), (" 600519 ", (1, "600519")),
             ((1, "600519"), (1, "600519")), (("sh", "600519"), (1, "600519"))]
    for inp, want in cases:
        got = symbols.parse(inp)
        assert got == want, "%r → %s，期望 %s" % (inp, got, want)


def test_parse_symbol_rejects_garbage():
    for bad in ("", "abc", None, "!!!", ()):
        try:
            symbols.parse(bad)
        except (ValueError, TypeError):
            continue
        raise AssertionError("%r 应抛错" % (bad,))


# ── 域分类 ───────────────────────────────────────────────────────────────
def test_domain_rules():
    cases = [
        ("CWServ.ph_agf10_cw_zcfzb", "财报"),
        ("CWServ.ph_zt_lhb_sy", "龙虎榜与两融"),
        ("CWServ.ph_tdxdatacenter_hsgt", "沪深港通"),
        ("CWServ.ph_zt_xg_ipo", "新股与再融资"),
        ("CWServ.ph_tdxdatacenter_dzjy_v2", "大宗交易"),
        ("HQServ.PBFXT", "行情"),
        ("CWServ.ph_datacenter_mghg_gy", "宏观"),
        ("CWServ.ph_tdxdatacenter_tfp_v2_kzz", "其他"),
        ("CWServ.ph_zt_tlsq_wxjg", "新股与再融资"),
    ]
    for cmd, want in cases:
        got = domains.domain_of(cmd)
        assert got == want, "%s → %s，期望 %s" % (cmd, got, want)


def test_every_product_has_known_domain():
    pj = json.load(io.open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tdxdata", "data", "products.json"), encoding="utf-8"))
    bad = [n for n, s in pj["products"].items()
           if s.get("domain") not in domains.DOMAINS]
    assert not bad, "域不在 DOMAINS 里: %s" % bad[:5]


def test_registered_domains_agree_with_domain_of():
    """products.json 的 `domain` 与 `domains.domain_of(cmd)` 必须逐条一致。

    事实源是 products.json 的 `_meta.域分类`：「由 tdxdata/domains.py 的
    domain_of() 生成」。两处漂移过一次（tfp_v2_kzz 被 kzz 兜底卷进新股域、
    xjjj 被 ph_zt 吞进涨停域，评审 SUMMARY §3.2），所以这里把它变成硬门禁：
    421 条逐条比，任何一条不一致都点名报红。
    """
    pj = json.load(io.open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tdxdata", "data", "products.json"), encoding="utf-8"))
    bad = []
    for name, spec in pj["products"].items():
        want = domains.domain_of(spec.get("cmd", ""))
        got = spec.get("domain")
        if got != want:
            bad.append("%s: 注册=%r, domain_of=%r" % (name, got, want))
    assert not bad, "products.json 域分类与 domain_of() 不一致：\n  " + "\n  ".join(bad[:20])


# ── 产品表 / 占位符 ──────────────────────────────────────────────────────
def test_product_count():
    pj = json.load(io.open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tdxdata", "data", "products.json"), encoding="utf-8"))
    assert len(pj["products"]) == 421, len(pj["products"])
    assert len(pj["pb_products"]) == 36, len(pj["pb_products"])


def test_fill_placeholders():
    got = CL._fill(["00101", "@code@", "#date#"], {"code": "600519"}, "x")
    assert got == ["00101", "600519", "2026-10-23"], got
    # 默认值可被覆盖
    got = CL._fill({"d": "#date#"}, {"date": "2020-01-01"}, "x")
    assert got == {"d": "2020-01-01"}, got


def test_all_products_fill_without_leftover():
    pj = json.load(io.open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tdxdata", "data", "products.json"), encoding="utf-8"))
    for name, spec in pj["products"].items():
        out = json.dumps(CL._fill(spec["params"], {}, name), ensure_ascii=False)
        for ph in ("@code@", "#code#", "@code#", "#date#", "@date@"):
            assert ph not in out, "%s 残留 %s" % (name, ph)


# ── 模型 ─────────────────────────────────────────────────────────────────
def test_bar_units_documented_and_time_field():
    b = tdxdata.Bar(market=1, code="600519", date="20261023", open=1.0,
                    high=2.0, low=0.5, close=1.5, vol=100, amount=150)
    assert b.time == "", "日线 time 应为空"
    assert b.symbol == "sh600519"
    b2 = tdxdata.Bar(market=1, code="600519", date="20261023", open=1.0,
                     high=2.0, low=0.5, close=1.5, time="15:00", period="60m")
    assert b2.time == "15:00"


def test_table_api():
    t = tdxdata.Table(cols=["N001", "N002"], rows=[{"N001": 1, "N002": 2}])
    assert len(t) == 1 and t.shape == (1, 2)
    assert t.column("N001") == [1]
    assert list(t) == [{"N001": 1, "N002": 2}]
    assert bool(t) is True
    assert bool(tdxdata.Table(cols=["N001"], rows=[])) is False


def test_exception_hierarchy():
    assert issubclass(tdxdata.NoData, tdxdata.EmptyResult)
    assert not issubclass(tdxdata.NotSupportedError, tdxdata.EmptyResult)
    for name in ("ConnectionFailed", "Timeout", "RateLimited", "ProtocolError",
                 "ParamError", "ServerError", "NotSupportedError", "EmptyResult"):
        assert issubclass(getattr(tdxdata, name), tdxdata.TdxError)


def test_public_exports_complete():
    missing = [x for x in tdxdata.__all__ if not hasattr(tdxdata, x)]
    assert not missing, missing


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print("  ✅ %s" % name)
        except AssertionError as e:
            bad += 1
            print("  ❌ %s: %s" % (name, e))
        except Exception as e:                                     # noqa: BLE001
            bad += 1
            print("  ❌ %s: %s: %s" % (name, type(e).__name__, e))
    print("\n%d 个用例，失败 %d" % (len(fns), bad))
    sys.exit(1 if bad else 0)
