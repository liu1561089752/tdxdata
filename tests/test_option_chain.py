# -*- coding: utf-8 -*-
"""期权 T 型报价三步链（`PBQQUnderly2` / `PBQQMonth` / `PBQQTXCode2`）—— 全离线。

夹具是 **2026-10-29 真实抓包**的响应字节（`cap_1029_090838.pcap`，
服务端 124.222.181.56:7615，msgid 854/864/866），所以这些断言钉的是"线上真的这样"。

三条链的锚点（抓包里能逐字节核对的）：
* 标的：9 个 ETF，沪 5（market=1）+ 深 4（market 缺省 0）
* 月份：2611 / 2612 / 2703 / 2706，到期日 20261125 / 20261223 / 20270324 / 20270623
* 链：12 档行权价 2.65…3.40，每档挂 认购/认沽 两个代码 + 自解释名称
"""
from __future__ import annotations

import datetime
import inspect
import pathlib

import pytest

import tdxdata
from tdxdata._engine import hq_extra as H

FI = pathlib.Path(__file__).with_name("fixtures")


def _fixture(name: str) -> bytes:
    return (FI / name).read_bytes()


# ── 1. 请求体逐字节 == 抓包（最强的一条：编码写错就再也取不到）─────────────
def test_underly_request_is_an_8_byte_constant():
    """`PBQQUnderly2` 无参 → 8 字节常量，抓包 5 次一模一样。"""
    assert H._args_qq_underly().hex() == "0a04100128011800"


def test_month_request_matches_capture_byte_for_byte():
    """25 B：`0a0b…353130303530`（末尾就是 `510050` 的 ASCII）。"""
    got = H._args_qq_month("510050").hex()
    assert got == "0a0b0a013110011a0022002801120a08011206353130303530"
    assert got.endswith("353130303530")          # "510050"
    assert len(bytes.fromhex(got)) == 25


def test_chain_request_appends_month_and_width():
    """31 B = 月份请求 + `18 b314`（2611）+ `20 f403`（500）。"""
    got = H._args_qq_chain("510050", 2611).hex()
    assert got == ("0a0b0a013110011a0022002801120a08011206353130303530"
                   "18b31420f403")
    assert bytes.fromhex(got)[-6:] == bytes.fromhex("18b31420f403")


# ── 2. 标的列表 ───────────────────────────────────────────────────────────
def test_underlyings_are_nine_etfs_with_honest_markets():
    rows = H.parse_qq_underly(H.peel(_fixture("qq_underly_响应.bin")))
    assert len(rows) == 9
    assert rows[0] == {"market": 1, "code": "510050", "name": "上证50ETF"}
    assert rows[-1] == {"market": 0, "code": "159922", "name": "深中证500ETF"}
    assert [r["code"] for r in rows] == ["510050", "510300", "510500", "588000", "588080",
                                         "159901", "159915", "159919", "159922"]
    # market 不是猜的：沪 5 个带 f1=1，深 4 个**没有** f1 → 0
    assert [r["market"] for r in rows] == [1, 1, 1, 1, 1, 0, 0, 0, 0]
    assert all(r["name"].endswith("ETF") for r in rows)


# ── 3. 月份 ───────────────────────────────────────────────────────────────
def test_months_and_expiries_are_consistent():
    got = H.parse_qq_month(H.peel(_fixture("qq_month_响应.bin")))
    assert got["count"] == 4
    assert got["months"] == [2611, 2612, 2703, 2706]
    assert got["expires"] == [20261125, 20261223, 20270324, 20270623]
    # 自检：count 与自己数出来的一致，且到期日都是**真日期**、年月与月份对得上
    assert got["count"] == len(got["months"]) == len(got["expires"])
    for m, e in zip(got["months"], got["expires"]):
        d = datetime.datetime.strptime(str(e), "%Y%m%d").date()
        assert (d.year, d.month) == (2000 + m // 100, m % 100)
        assert d > datetime.date(2026, 10, 29), "期权到期日必须晚于抓包当天"


# ── 4. T 型链 ─────────────────────────────────────────────────────────────
def test_chain_rows_name_their_own_strike():
    cols, rows = H.parse_qq_chain(H.peel(_fixture("qq_chain_响应.bin")))
    assert cols == H.QQ_CHAIN_COLS == ("Strike", "CallCode", "CallName", "PutCode", "PutName")
    assert len(rows) == 12
    strike, call_code, call_name, put_code, put_name = rows[0]
    assert strike == 2.65
    assert (call_code, call_name) == ("10012637", "50ETF购11月2650")
    assert (put_code, put_name) == ("10012638", "50ETF沽11月2650")

    assert [r[0] for r in rows] == sorted(r[0] for r in rows), "行权价必须递增"
    for strike, cc, cn, pc, pn in rows:
        # 名称里的数字 = 行权价 × 1000 —— 这是服务端自己给的，可用来核 f32 有没有解错
        assert cn.split("月")[-1].isdigit()
        assert int(cn.split("月")[-1]) == round(strike * 1000)
        assert cn.replace("购", "沽") == pn, "同一档的购/沽名称只差一个字"
        assert "购" in cn and "沽" in pn
        assert cc != pc and cc.isdigit() and pc.isdigit()
        assert len(cc) == 8


# ── 5. 三个入口在内网打桩下的行为（不联网）───────────────────────────────
def test_entry_points_use_the_three_distinct_ctl(monkeypatch):
    """三步的 ctl 各不相同（15/12/14）—— 抄错一个就串包。"""
    assert (H.CTL["qq_underly"], H.CTL["qq_month"], H.CTL["qq_chain"]) == (0x0F, 0x0C, 0x0E)
    assert (H.CMD["qq_underly"], H.CMD["qq_month"], H.CMD["qq_chain"]) == (
        "HQServ:PBQQUnderly2", "HQServ:PBQQMonth", "HQServ:PBQQTXCode2")

    calls = []

    def fake_call(which, args, tries=3):
        calls.append((which, args.hex()))
        return {"qq_underly": H.peel(_fixture("qq_underly_响应.bin")),
                "qq_month": H.peel(_fixture("qq_month_响应.bin")),
                "qq_chain": H.peel(_fixture("qq_chain_响应.bin"))}[which]

    monkeypatch.setattr(H, "_call", fake_call)

    assert len(H.option_underlyings()) == 9
    assert H.option_months("510050")["months"] == [2611, 2612, 2703, 2706]
    cols, rows = H.option_chain("510050", 2611)
    assert len(rows) == 12 and cols[0] == "Strike"
    # month=None 时会**先要月份**再要链（两次请求），顺序不能反
    assert [c[0] for c in calls][-2:] == ["qq_month", "qq_chain"]


def test_option_chain_raises_when_channel_returns_nothing(monkeypatch):
    monkeypatch.setattr(H, "_call", lambda *a, **k: b"")
    with pytest.raises(tdxdata.EmptyResult):
        H.option_underlyings()
    with pytest.raises(tdxdata.EmptyResult):
        H.option_months("510050")
    with pytest.raises(tdxdata.EmptyResult):
        H.option_chain("510050", 2611)


# ── 6. 公开方法确实挂在 Client 上（不联网只查签名）──────────────────────
def test_public_methods_exist_with_documented_signatures():
    cls = getattr(tdxdata, "Client")
    for name in ("option_underlyings", "option_months", "get_option_chain"):
        assert hasattr(cls, name), "Client 缺 %s" % name
    sig = inspect.signature(cls.get_option_chain)
    assert list(sig.parameters) == ["self", "underly", "month"]
    assert sig.parameters["underly"].default == "510050"

# ── 7. 市场号 8 = 期权（`get_quotes(["8:10012637"])` 这条路必须通）─────────
def test_market_eight_is_option_and_reaches_the_batch_encoder():
    """期权行情走 `PBCombHQ` 的市场号 8 —— 这是链条的最后一环。

    没有这条，`get_option_chain()` 拿到的代码**没法换成行情**：
    `parse_quote("8:10012637")` 会因为"市场号 8 没登记"直接抛错。
    """
    from tdxdata import symbols as S

    assert S.MARKETS[8] == "期权（合约代码如 10012637）"
    assert S.parse_quote("8:10012637") == (8, "10012637")
    assert S.parse_quote((8, "10012638")) == (8, "10012638")

    # 混在别的资产里也要能一起编码（顺序与原样保留）
    pairs = S.parse_quote("8:10012637"), S.parse_quote("28:MA2701"), S.parse_quote("hk00700")
    assert pairs == ((8, "10012637"), (28, "MA2701"), (31, "00700"))

    # 市场号 8 不认识的值仍然要炸（别把 8 做成"什么都收"）
    with pytest.raises(ValueError):
        S.parse_quote("8:")
