# -*- coding: utf-8 -*-
"""P8 修正版离线用例：**复权参数** + **PBMultiHQ 分页**。

为什么这些用例必须以「字节」为单位断言
--------------------------------------
复权与翻页都只是请求里的**几个数字**，本地不产生任何可观察的差异 ——
写错一个字段，出数依旧"正常"，只是价格口径错/翻页跳行。
所以这里钉的不是"函数能跑"，而是**发出去的字节对不对**：

1. `_args_pbfxt` 的 `field8` 只能随 `adjust` 变，其他字段一个字节都不许动；
2. 自造的 `PBMultiHQ` 参数必须与内置模板 **74 字节完全一致**
   （早期按文档推演时多写过 `field10/field11`，就是被这条抓出来的）；
3. 负数 varint 不能死循环（服务端 `field6` 默认值就是 `-1`）。

全部离线：`hq_pb.get_bars` / `hq_pb.get_market_page` 被替换成假实现，
`Client` 对象用 `__new__` 造（不跑 `__init__`），**零建连**。

跑法：
    python tests/test_p8_adjust_page.py
    python -m pytest tests/test_p8_adjust_page.py -q
"""
from __future__ import annotations

import base64
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tdxdata                                                      # noqa: E402
from tdxdata import errors                                          # noqa: E402
from tdxdata._engine import hq_pb, pb, pb_api                       # noqa: E402
from tdxdata.client import Client, _adjust_code                     # noqa: E402
from tdxdata.enums import Adjust                                    # noqa: E402


# ── 工具 ────────────────────────────────────────────────────────────────────
def _bare_client():
    """不跑 `__init__` 的 Client（不建连、不读产品表，只测参数透传）。"""
    return Client.__new__(Client)


def _fields(args):
    """解成 [(field, wire, value)]，便于按字段号断言。"""
    return [(f, w, v) for f, w, v in pb.decode(args)]


def _by_field(args):
    out = {}
    for f, w, v in pb.decode(args):
        out.setdefault(f, []).append(v)
    return out


# ══════════════════════════════════════════════════════════════════════════
# 一、T1 复权：field8 是唯一变量
# ══════════════════════════════════════════════════════════════════════════
def test_pbfxt_field8_follows_adjust():
    """field8 必须等于传入的 adjust；1/2/3 三种口径各对一次。"""
    for code_adj, want in ((1, 1), (2, 2), (3, 3)):
        a = _by_field(hq_pb._args_pbfxt("600519", 4, 30, 0, code_adj))
        assert a[8] == [want], "adjust=%d 应写成 field8=%d，实际 %r" % (code_adj, want, a[8])


def test_pbfxt_adjust_is_only_difference():
    """换复权口径时**只有** field8 变，别的字段一个都不许动。"""
    base = _fields(hq_pb._args_pbfxt("600519", 4, 120, 40, 1))
    for adj in (2, 3):
        other = _fields(hq_pb._args_pbfxt("600519", 4, 120, 40, adj))
        assert len(base) == len(other)
        diff = [(f1, v1, v2) for (f1, _w1, v1), (_f2, _w2, v2) in zip(base, other) if v1 != v2]
        assert len(diff) == 1 and diff[0][0] == 8, \
            "adjust=%d 改了不止 field8：%r" % (adj, diff)
        assert diff[0][2] == adj


def test_pbfxt_structure_matches_builtin_template():
    """结构必须与内置模板一致（模板是 688318 / 120 根，只比"字段序列"）。"""
    tpl = _fields(pb_api.args_for("HQServ.PBFXT"))
    mine = _fields(hq_pb._args_pbfxt("688318", 4, 120, 0, 1))
    assert [f for f, _w, _v in tpl] == [f for f, _w, _v in mine], \
        "字段序列不一致：模板 %r vs 我 %r" % ([f for f, _w, _v in tpl],
                                             [f for f, _w, _v in mine])


def test_adjust_code_mapping():
    """`Adjust` → 协议码。NONE=1 / QFQ=3 / HFQ=2 —— **枚举值不是协议码**。"""
    assert _adjust_code(Adjust.NONE) == (1, "NONE")
    assert _adjust_code(Adjust.QFQ) == (3, "QFQ")
    assert _adjust_code(Adjust.HFQ) == (2, "HFQ")
    assert _adjust_code("qfq") == (3, "QFQ")
    assert _adjust_code(" HFQ ") == (2, "HFQ")
    assert _adjust_code(None) == (1, "NONE")
    # 关键：Adjust.QFQ == 1，如果直接按 int 映射会变成"不复权"，必须拦
    assert int(Adjust.QFQ) == 1 and _adjust_code(Adjust.QFQ)[0] == 3


def test_adjust_rejects_raw_int():
    """裸 int 一律拒绝（否则 QFQ 会被静默当地成不复权）。"""
    for bad in (0, 1, 2, 3):
        try:
            _adjust_code(bad)
        except ValueError as e:
            assert "不要传裸 int" in str(e) or "adjust 只接受" in str(e)
        else:
            raise AssertionError("裸 int %r 必须被拒绝" % (bad,))


def test_adjust_rejects_unknown_name():
    try:
        _adjust_code("QFQ2")
    except ValueError as e:
        assert "不认识" in str(e) and "QFQ" in str(e)
    else:
        raise AssertionError("未知口径必须抛 ValueError")


def test_get_bars_passes_adjust_through_and_stamps_bar():
    """`Client.get_bars` 必须把 adjust 传下去，并把口径盖到每根 Bar 上。"""
    calls = []

    def fake_bars(code, period="day", count=120, offset=0, adjust=1, market=None):
        calls.append({"code": code, "period": period, "count": count,
                      "offset": offset, "adjust": adjust, "market": market})
        return ["Data", "Open", "High", "Low", "Close", "Volume", "Amount", "Second"], [
            {"Data": "20261022", "Open": "1", "High": "2", "Low": "0.5",
             "Close": "1335.00", "Volume": "100", "Amount": "133500", "Second": "0"},
            {"Data": "20261023", "Open": "1", "High": "2", "Low": "0.5",
             "Close": "9311.23", "Volume": "100", "Amount": "931123", "Second": "0"},
        ]

    real = hq_pb.get_bars
    hq_pb.get_bars = fake_bars
    try:
        c = _bare_client()
        for adj, want_code, want_name in ((Adjust.NONE, 1, "NONE"),
                                          (Adjust.QFQ, 3, "QFQ"),
                                          (Adjust.HFQ, 2, "HFQ")):
            del calls[:]
            bars = c.get_bars("600519", count=2, adjust=adj)
            assert calls and calls[0]["adjust"] == want_code, \
                "%s 应传 adjust=%d，实际 %r" % (adj, want_code, calls)
            assert bars[-1].adjust == want_name, bars[-1].adjust
            # 旧的"标记未生效"逻辑必须彻底消失
            assert "_adjust_unapplied" not in bars[-1].raw
            assert bars[-1].raw["Close"] == "9311.23", "raw 必须是原始回包，不许被改写"
    finally:
        hq_pb.get_bars = real


def test_get_bars_default_is_none_adjust():
    """不传 adjust 时必须是 field8=1（不复权），行为与改动前一致。"""
    seen = {}

    def fake_bars(code, period="day", count=120, offset=0, adjust=1, market=None):
        seen["adjust"] = adjust
        return ["Data", "Close"], [{"Data": "20261023", "Close": "1335.00"}]

    real = hq_pb.get_bars
    hq_pb.get_bars = fake_bars
    try:
        bars = _bare_client().get_bars("600519", count=1)
        assert seen["adjust"] == 1, seen
        assert bars[0].close == 1335.00 and bars[0].adjust == "NONE"
    finally:
        hq_pb.get_bars = real


# ══════════════════════════════════════════════════════════════════════════
# 二、protobuf 小工具：负数 varint 不许死循环
# ══════════════════════════════════════════════════════════════════════════
def test_varint_negative_is_int64_twos_complement():
    """`-1` → 10 字节 `ff…01`（服务端 field6 默认值就是 -1）。"""
    assert hq_pb._varint(-1).hex() == "ff" * 9 + "01"
    assert len(hq_pb._varint(-1)) == 10
    assert hq_pb._varint(0) == b"\x00"
    assert hq_pb._varint(20) == b"\x14"
    assert hq_pb._varint(300) == b"\xac\x02"


# ══════════════════════════════════════════════════════════════════════════
# 三、T2 PBMultiHQ 参数：必须与内置模板字节一致
# ══════════════════════════════════════════════════════════════════════════
def test_multihq_args_byte_identical_to_template():
    """自造参数 == 内置 base64 模板（74 字节），**逐字节**。

    模板 = 列 [NOW,CLOSE,NAME,VAR0404,FLAG,BELONGHY]、市场 6、offset −1、PageSize 20。
    """
    tpl = pb_api.args_for("HQServ.PBMultiHQ")
    mine = hq_pb._args_multihq(
        ["NOW", "CLOSE", "NAME", "VAR0404", "FLAG", "BELONGHY"], 6, -1, 20)
    assert len(tpl) == 74, "模板长度变了（%d），先确认真实模板" % len(tpl)
    assert base64.b64encode(mine).decode() == base64.b64encode(tpl).decode()
    assert mine == tpl


def test_multihq_capture_cols_request_bytes_frozen():
    """**抓包原样的 3 列**形态：请求 hex 逐字节冻结（40 B）。

    这 40 B 是 2026-10-27 实网真实发出的字节（`market=6, offset=0, ps=50`，
    当时默认列还是抓包的 3 列）。留它的意义：**抓包形态不许漂** ——
    默认列后来改成了 5 列，但抓包那 3 列必须仍能原样复现。
    """
    assert hq_pb.MULTIHQ_COLS_CAPTURE == ("NOW", "CLOSE", "NAME"), "抓包原样就是这 3 列"
    got = hq_pb._args_multihq(list(hq_pb.MULTIHQ_COLS_CAPTURE), 6, 0, 50)
    assert len(got) == 40, "3 列请求体实测 40 B，现在 %d B" % len(got)
    assert got.hex() == (
        "0a02280012034e4f571205434c4f534512044e414d45"
        "1a002006280e300038324001480160016800"
    ), got.hex()


def test_multihq_default_cols_request_bytes_frozen():
    """**默认 5 列**形态：命名诚实 + 请求 hex 逐字节冻结（53 B）。

    名字叫 `DEFAULT` 就必须等于"默认实际拿到的"；抓包那 3 列另存
    `MULTIHQ_COLS_CAPTURE`，两者是**包含关系**（默认 = 抓包 3 列 + AMOUNT + VOL），
    改任一边立刻红。53 B 是 2026-10-27 实网发出且被服务端照单接受的字节
    （同一页仍 1 次请求、1.75 s、满页 50 行）。
    """
    assert hq_pb.MULTIHQ_DEFAULT_COLS == hq_pb.MULTIHQ_COLS_CAPTURE + ("AMOUNT", "VOL"), \
        hq_pb.MULTIHQ_DEFAULT_COLS
    got = hq_pb._args_multihq(list(hq_pb.MULTIHQ_DEFAULT_COLS), 6, 0, 50)
    assert len(got) == 53, "5 列请求体实测 53 B，现在 %d B" % len(got)
    assert got.hex() == (
        "0a02280012034e4f571205434c4f534512044e414d451206414d4f554e541203564f4c"
        "1a002006280e300038324001480160016800"
    ), got.hex()
    for name in hq_pb.MULTIHQ_COLS_CAPTURE + ("AMOUNT", "VOL"):
        assert b"\x12" + bytes([len(name)]) + name.encode() in got, name


def test_get_market_page_default_cols_yield_vol_amount():
    """`cols=None`（走默认 5 列）→ `Quote.vol`/`Quote.amount` **有真值**，不是 None。

    用 2026-10-27 实网原样值（688252 天德钰）：`VOL=82823` 手、`AMOUNT=176519296.00` 元。
    """
    row = {"Code": "688252", "Setcode": "1", "Name": "天德钰", "CLOSE": "20.50",
           "NOW": "21.95", "AMOUNT": "176519296.00", "VOL": "82823"}

    def fake(market=6, offset=0, page_size=50, cols=None):
        use = list(hq_pb.MULTIHQ_DEFAULT_COLS if cols is None else cols)   # 与真实实现同语义
        return use, [dict(row)]

    real = hq_pb.get_market_page
    hq_pb.get_market_page = fake
    try:
        q = _bare_client().get_market_page(market=6, page_size=50)[0]
        assert q.vol == 82823.0, q.vol
        assert q.amount == 176519296.0, q.amount
        assert q.vol is not None and q.amount is not None
        assert abs(q.vol * 100 * q.price - q.amount) / q.amount < 0.05   # 手→股→金额 自洽
    finally:
        hq_pb.get_market_page = real


def test_get_market_page_explicit_narrow_cols_gives_none():
    """显式窄列（如 `cols=["NOW"]`）→ 两个字段 `is None`（不是 0）。"""
    def fake(market=6, offset=0, page_size=50, cols=None):
        use = list(hq_pb.MULTIHQ_DEFAULT_COLS if cols is None else cols)
        return use, [{"Code": "000001", "Setcode": "0", "Name": "平安银行", "NOW": "11.28"}]

    real = hq_pb.get_market_page
    hq_pb.get_market_page = fake
    try:
        q = _bare_client().get_market_page(market=6, page_size=50, cols=["NOW"])[0]
        assert q.vol is None, q.vol
        assert q.amount is None, q.amount
    finally:
        hq_pb.get_market_page = real


def test_multihq_fields_have_expected_meaning():
    """按字段号核一遍解构结论（field4=市场码、field6=偏移、field7=PageSize）。"""
    a = _by_field(hq_pb._args_multihq(["NOW"], 2, 40, 50))
    assert a[2] == ["NOW"], "field2 是列名，不是代码"
    assert a[4] == [2], "field4 = 市场码"
    assert a[6] == [40], "field6 = 偏移"
    assert a[7] == [50], "field7 = PageSize"
    assert a[5] == [14]
    assert 10 not in a and 11 not in a, "field10/11 不该出现（这是曾经写错的地方）"
    assert a[12] == [1] and a[13] == [0]


def test_vol_normalized_to_hand_for_stocks():
    """股票/ETF：日线与分钟线原始是**股** → 出口 ÷100 成**手**（2026-10-27 口径变更）。

    实测依据（2026-10-27 日线 `amount/vol` 恒等式）：600519 比 0.998、000001 0.995、
    300750 1.002、688981 1.002、920091 1.004、510300 1.000、159915 1.001 → 全是"股"。
    """
    from tdxdata.client import _vol_to_hand

    for code in ("600519", "000001", "300750", "688981", "920091", "510300", "159915"):
        assert _vol_to_hand(1_058_872.0, code, "day") == 10588.72, code
        assert _vol_to_hand(45_000.0, code, "5m") == 450.0, code
    # 允许小数，**不许四舍五入**
    assert _vol_to_hand(4_159_916.0, "600519", "day") == 41599.16


def test_vol_hand_kinds_are_never_divided_by_100():
    """⚠️ 最危险的回归点：**可转债 / 回购 / 指数 / 板块的日线本来就是"手"**，绝不能 ÷100。

    实测依据：113054 `amount/vol`=9.99（= 收盘价×10，即 1 手=10 张）、
    204001/131810 `amount/vol` **恰好 1000.000**（1 手 = 1000 元面值）、
    999999/880903/881007 由东财逐值交叉核对（上证 552,842,880 vs 东财 552,842,912 手）定为手。
    """
    from tdxdata.client import _vol_to_hand

    cases = {                      # code -> 原始 vol（已经是手）
        "113054": 10_382.0,        # 沪可转债
        "110077": 12_872.0,
        "123283": 5_000.0,         # 深可转债
        "204001": 349_787_936.0,   # 沪逆回购
        "131810": 29_399_118.0,    # 深逆回购
        "999999": 156_620_736.0,   # 上证指数
        "399001": 222_114_912.0,   # 深证成指
        "899050": 2_806_958.0,     # 北证50
        "880903": 3_352_003.0,     # 板块（水产品）
        "881007": 1_115_909.0,     # 板块（油气开采）
    }
    for code, raw in cases.items():
        got = _vol_to_hand(raw, code, "day")
        assert got == raw, "日线 %s 被误改了：%r -> %r" % (code, raw, got)
    # 可转债的分钟线实测也是手 → 不动
    assert _vol_to_hand(1_152.0, "113054", "5m") == 1152.0


def test_index_minute_vol_is_none_not_a_derived_fake():
    """⚠️ 指数/板块的**分钟线** `vol` 是 `amount/100` 派生值 → 必须给 `None`，不许冒充成交量。

    实测（2026-10-27）：999999、880903、899050 的分钟线 `amount/vol` **恰好 100.000**（整数不差），
    而它们的 `amount` 与当日日线合计比值是 1.0000 / 1.0002 ⇒ amount 真、vol 是造的。
    量级也印证：999999 的 60m `vol` 折算成手是 2.86 万亿股/小时，当股讲 28.6 亿股也和大盘不符。
    本库红线：**拿不到就给 None，不许拿派生值冒充**。
    """
    from tdxdata.client import _vol_to_hand

    for code in ("999999", "399001", "899050", "880903", "881007"):
        for per in ("1m", "5m", "15m", "30m", "60m"):
            assert _vol_to_hand(2_865_168_384.0, code, per) is None, (code, per)
    # 但日线是**真值**，不许跟着一起变成 None
    assert _vol_to_hand(162_406_144.0, "999999", "day") == 162_406_144.0
    assert _vol_to_hand(3_456_649.0, "880903", "day") == 3_456_649.0
    # 周线仍是 ×100（真值）
    assert _vol_to_hand(5_956_564.0, "999999", "week") == 595_656_400.0


def test_index_minute_bar_end_to_end_vol_none_raw_kept():
    """端到端（离线）：`get_bars("999999","60m")` 的 `vol is None`，原值仍在 `raw`。"""
    row = {"Data": "20261027", "Open": "3690", "High": "3702", "Low": "3688",
           "Close": "3700.9", "Second": "1030",
           "Volume": "2865168384", "Amount": "286516871168"}

    def fake(code, period="60m", count=120, offset=0, adjust=0, market=1):
        return ["Data", "Open", "High", "Low", "Close", "Volume", "Amount", "Second"], [dict(row)]

    real = hq_pb.get_bars
    hq_pb.get_bars = fake
    try:
        b = _bare_client().get_bars("999999", "60m", 1)[0]
        assert b.vol is None, b.vol                      # 派生值不冒充
        assert b.raw["Volume"] == "2865168384"           # 原值仍在 raw
        assert b.amount == 286516871168.0                # amount 真值不动
        assert b.period == "60m"
    finally:
        hq_pb.get_bars = real


def test_vol_week_month_for_hand_kinds_is_multiplied_back():
    """可转债/指数/板块的**周/月线**：服务端给的是"日线手数/100" → 出口 ×100 换回手。

    实测（2026-10-27，周线 vol vs 当日日线求和）：113054 640 对 64007/100、
    999999 5,895,076 对 589,415,040/100、880903 88,407 对 8,840,700/100，
    而 `amount` 比值全是 **1.000** —— 服务端在周/月线上把 vol **统一除以 100**。
    """
    from tdxdata.client import _vol_to_hand

    assert _vol_to_hand(640.0, "113054", "week") == 64000.0
    assert _vol_to_hand(5_895_076.0, "999999", "week") == 589_507_600.0
    assert _vol_to_hand(88_407.0, "880903", "month") == 8_840_700.0
    # 股票周线服务端本来就是手 → 不变
    assert _vol_to_hand(52_551.0, "600519", "week") == 52_551.0
    assert _vol_to_hand(498_214.0, "600519", "month") == 498_214.0


def test_get_bars_exposes_hand_and_keeps_raw():
    """端到端（离线）：`get_bars` 出口 vol 已是手，`raw["Volume"]` 仍是服务端原值。"""
    row = {"Data": "20261027", "Open": "1340", "High": "1360", "Low": "1330",
           "Close": "1350.09", "Volume": "1058872", "Amount": "1426369152"}

    def fake(code, period="day", count=120, offset=0, adjust=0, market=1):
        return ["Data", "Open", "High", "Low", "Close", "Volume", "Amount"], [dict(row)]

    real = hq_pb.get_bars
    hq_pb.get_bars = fake
    try:
        b = _bare_client().get_bars("600519", "day", 1)[0]
        assert b.vol == 10588.72, b.vol                        # 已 ÷100
        assert b.vol_unit == "手"                               # 明确属性
        assert b.raw["Volume"] == "1058872", b.raw["Volume"]   # 原值仍在 raw
        assert b.amount == 1426369152.0                         # amount 不动
        assert b.close == 1350.09
    finally:
        hq_pb.get_bars = real


def test_multihq_cols_order_preserved():
    """列名顺序必须原样保留（服务端按声明顺序回列）。"""
    got = _by_field(hq_pb._args_multihq(["NOW", "CLOSE", "NAME"], 6, 0, 20))[2]
    assert got == ["NOW", "CLOSE", "NAME"], got


def test_multihq_offset_changes_only_field6():
    a = _fields(hq_pb._args_multihq(["NOW", "CLOSE", "NAME"], 6, 0, 50))
    b = _fields(hq_pb._args_multihq(["NOW", "CLOSE", "NAME"], 6, 49, 50))
    diff = [(f1, v1, v2) for (f1, _w1, v1), (_f2, _w2, v2) in zip(a, b) if v1 != v2]
    assert len(diff) == 1 and diff[0][0] == 6, diff


# ══════════════════════════════════════════════════════════════════════════
# 四、T2 分页封装
# ══════════════════════════════════════════════════════════════════════════
def _fake_page(offset, page_size, market=6):
    """假服务端：返回**正好 page_size 行**（= 实测 21~80 的行为），代码从 offset 起连续编号。"""
    n = page_size
    cols = ["Code", "Setcode", "Name", "NOW", "CLOSE"]
    rows = [{"Code": "%06d" % (offset + i), "Setcode": str(market),
             "Name": "股%03d" % (offset + i), "NOW": "10.%02d" % (i % 100),
             "CLOSE": "9.99"} for i in range(n)]
    return cols, rows


def test_get_market_page_builds_quotes():
    """`get_market_page` → `list[Quote]`，字段映射与行数（= page_size）正确。"""
    seen = {}

    def fake(market=6, offset=0, page_size=50, cols=None):
        seen.update(market=market, offset=offset, page_size=page_size, cols=cols)
        return _fake_page(offset, page_size, market)

    real = hq_pb.get_market_page
    hq_pb.get_market_page = fake
    try:
        page = _bare_client().get_market_page(market=6, page_size=50)
        assert len(page) == 50, "实测 page_size=50 返回 50 行，实际 %d" % len(page)
        q = page[0]
        assert (q.market, q.code, q.name) == (6, "000000", "股000")
        assert q.price == 10.0 and q.pre_close == 9.99
        assert q.bid == [] and q.ask == [], "无五档 → 必须是空列表（不是假数据）"
        assert seen["market"] == 6 and seen["page_size"] == 50 and seen["offset"] == 0
        assert seen["cols"] is None, "默认列交给 hq_pb 决定"
    finally:
        hq_pb.get_market_page = real


def test_get_market_page_page_size_clamped_to_80():
    """`page_size > 80` → 本地截到 80（服务端实测也只给 80 行）。

    打桩的是 `pb_api.call_pb`（而不是 `hq_pb.get_market_page`），
    这样校验/截断/拼参数的**真实代码路径**都跑到，并且能从字节里读回真正发出去的 page_size。
    """
    from tdxdata._engine import pacer as P

    seen = {}
    real_call, real_wait = pb_api.call_pb, P.Pacer.wait

    def fake_call(cmd, ctl=None, args=None, tries=4):
        seen["ps"] = _by_field(args)[7][0]
        return _fake_page(0, seen["ps"])

    pb_api.call_pb = fake_call
    P.Pacer.wait = lambda self: 0.0
    try:
        for big in (81, 100, 1000):
            got = _bare_client().get_market_page(page_size=big)
            assert seen["ps"] == 80, "page_size=%r 应截到 80，实际发出 %r" % (big, seen["ps"])
            assert len(got) == 80
        # 只有 < 2 是硬下限（纯 sanity，不是服务端约束）
        for bad in (0, 1, -5):
            try:
                _bare_client().get_market_page(page_size=bad)
            except ValueError as e:
                assert "page_size" in str(e), str(e)
            else:
                raise AssertionError("page_size=%r 必须抛 ValueError" % (bad,))
    finally:
        pb_api.call_pb = real_call
        P.Pacer.wait = real_wait


def test_get_market_page_grows_page_size_on_empty():
    """**复核修正的核心**：0 行不是"没数据"，而是「列数 × page_size」太小 → 翻倍重试。

    模拟服务端：只有「列数 × page_size > 60」才出数（这就是复核方实测出来的那条规律）。
    1 列 + 起始 ps=21 → 21×1=21 不够 → 应当依次发出 21 / 42 / 80 直到出数。
    """
    from tdxdata._engine import pacer as P

    sent = []
    real_call, real_wait = pb_api.call_pb, P.Pacer.wait

    def fake_call(cmd, ctl=None, args=None, tries=4):
        f = _by_field(args)
        ps, ncols = f[7][0], len(f[2])
        sent.append((ncols, ps))
        if ncols * ps <= 60:                     # 服务端的隐含下限
            return (None, [])
        return _fake_page(0, ps)

    pb_api.call_pb = fake_call
    P.Pacer.wait = lambda self: 0.0
    try:
        got = _bare_client().get_market_page(market=6, page_size=21, cols=["NOW"])
        assert sent == [(1, 21), (1, 42), (1, 80)], "应当翻倍重试：%r" % sent
        assert len(got) == 80, "最终拿到 80 行，实际 %d" % len(got)

        # 6 列 + ps=20 本来就能出数（复核方实测）→ 不许误伤、不许重试
        del sent[:]
        got = _bare_client().get_market_page(market=6, page_size=20,
                                            cols=["NOW", "CLOSE", "NAME", "VAR0404",
                                                  "FLAG", "BELONGHY"])
        assert sent == [(6, 20)], "能出数就不许重试：%r" % sent
        assert len(got) == 20
    finally:
        pb_api.call_pb = real_call
        P.Pacer.wait = real_wait


def test_get_market_page_gives_up_after_max_attempts():
    """一直 0 行：翻倍到 80 封顶、尝试次数有上限，最终如实返回空（不无限打）。"""
    from tdxdata._engine import pacer as P

    sent = []
    real_call, real_wait = pb_api.call_pb, P.Pacer.wait

    def fake_call(cmd, ctl=None, args=None, tries=4):
        sent.append(_by_field(args)[7][0])
        return (None, [])

    pb_api.call_pb = fake_call
    P.Pacer.wait = lambda self: 0.0
    try:
        cols, rows = hq_pb.get_market_page(market=6, offset=0, page_size=3, cols=["NOW"])
        assert rows == [] and cols is None
        # 3→6→12→24→48→(80 已封顶就停)，最多 _PAGE_GROW_ATTEMPTS 次
        assert len(sent) <= hq_pb._PAGE_GROW_ATTEMPTS, sent
        assert sent == sorted(sent) and sent[-1] <= hq_pb.MAX_PAGE_ROWS, sent
        # 每次尝试都过闸门
    finally:
        pb_api.call_pb = real_call
        P.Pacer.wait = real_wait


def test_every_retry_attempt_is_a_separate_request():
    """翻倍重试时，每一次尝试都必须是**独立的一次请求**（不是一次打 N 发同一份参数）。

    闸门是否被过，在 `tests/test_transport.py` 里验证（现在统一在 `dc._send` 过）；
    这里只钉"尝试次数 = 请求次数"和"每次的参数不同"。
    """
    sent = []
    real_call = pb_api.call_pb

    def fake_call(cmd, ctl=None, args=None, tries=4):
        sent.append(_by_field(args)[7][0])
        return (None, []) if len(sent) < 3 else _fake_page(0, _by_field(args)[7][0])

    pb_api.call_pb = fake_call
    try:
        _bare_client().get_market_page(market=6, page_size=21, cols=["NOW"])
        assert len(sent) == 3, sent
        assert sent == [21, 42, 80], "每次尝试都要重新拼参数（翻倍）：%r" % sent
    finally:
        pb_api.call_pb = real_call


def test_get_market_page_empty_raises_not_returns_empty_list():
    """0 行必须抛 `EmptyResult` —— 红线：不许返回空列表冒充"没有数据"。"""
    real = hq_pb.get_market_page
    hq_pb.get_market_page = lambda **kw: (None, [])
    try:
        try:
            _bare_client().get_market_page()
        except errors.EmptyResult as e:
            assert "PBMultiHQ" in str(e)
        else:
            raise AssertionError("0 行必须抛 EmptyResult")
    finally:
        hq_pb.get_market_page = real


def test_get_universe_pages_without_overlap_and_respects_cap():
    """翻页：offset 单调递增、代码无重复、页间严格连续、`max_pages` 是硬上限。"""
    offsets = []

    def fake(market=6, offset=0, page_size=50, cols=None):
        offsets.append(offset)
        return _fake_page(offset, page_size, market)

    real = hq_pb.get_market_page
    hq_pb.get_market_page = fake
    try:
        uni = _bare_client().get_universe(market=6, max_pages=3, page_size=50)
        codes = [q.code for q in uni]
        assert len(codes) == len(set(codes)), "翻页结果不许有重复代码"
        assert len(uni) == 50 * 3, "3 页 × 50 行，实际 %d" % len(uni)
        assert offsets == [0, 50, 100], "offset 按行推进（步长=返回行数）：%r" % offsets
        assert offsets == sorted(offsets) and len(set(offsets)) == 3
        # 页间连续性：第 50 条紧接第 49 条（无跳行、无重叠）
        assert uni[49].code == "000049" and uni[50].code == "000050"
        assert codes == ["%06d" % i for i in range(150)], "必须是 0..149 连续无洞"
    finally:
        hq_pb.get_market_page = real


def test_get_universe_stops_on_empty_result():
    """服务端没这一页时 `get_market_page` 抛 `EmptyResult` → universe 必须**优雅停下**。"""
    calls = []

    def fake(market=6, offset=0, page_size=50, cols=None):
        calls.append(offset)
        if offset >= 100:
            raise errors.EmptyResult("到尾了")
        return _fake_page(offset, page_size, market)

    real = hq_pb.get_market_page
    hq_pb.get_market_page = fake
    try:
        uni = _bare_client().get_universe(market=6, max_pages=10, page_size=50)
        assert len(uni) == 100, "两页共 100 条，实际 %d" % len(uni)
        assert calls == [0, 50, 100], "第 3 次拿到空页后必须停：%r" % calls
    finally:
        hq_pb.get_market_page = real


def test_get_universe_grow_and_offset_advance_interact():
    """**第二轮复核点出的交互路径**：`page_size` 翻倍 + `off += len(page)` 叠加不许错位。

    `page_size=20` 会让服务端回 0 行 → 内部翻倍到 40 → 实际返回 40 行。
    那么下一页的 offset 必须按**真实返回的 40** 推进（而不是调用方给的 20），
    否则会重复采集 / 跳行。

    ⚠️ 列数由 3 变 5（2026-10-27，默认列加 `AMOUNT`/`VOL`，见 task-20）：
    这里钉的 `ncol` 跟着变成 5 —— 断言仍是**精确元组**，没有放宽。
    """
    from tdxdata._engine import pacer as P

    sent = []
    real_call, real_wait = pb_api.call_pb, P.Pacer.wait

    def fake_call(cmd, ctl=None, args=None, tries=4):
        f = _by_field(args)
        ps, off, ncol = f[7][0], f[6][0], len(f[2])
        sent.append((off, ncol, ps))
        if ps <= 21:                       # ps=20 稳定回空（实测那种「列组合门槛」）
            return (None, [])
        return _fake_page(off, ps)

    pb_api.call_pb = fake_call
    P.Pacer.wait = lambda self: 0.0
    try:
        uni = _bare_client().get_universe(market=6, max_pages=2, page_size=20)
        # 每页都：先 20（空）再 40（出数），第 2 页 offset 必须是 40 不是 20
        # 元组 = (offset, 列数, page_size)；列数是默认 5（2026-10-27 由 3 改成 5）
        assert sent == [(0, 5, 20), (0, 5, 40), (40, 5, 20), (40, 5, 40)], sent
        codes = [q.code for q in uni]
        assert len(codes) == 80 and len(set(codes)) == 80, "两页共 80 条且零重复"
        assert codes[0] == "000000" and codes[40] == "000040" and codes[-1] == "000079", \
            "接缝必须落在 40（真实返回行数）而不是 20"
    finally:
        pb_api.call_pb = real_call
        P.Pacer.wait = real_wait


def test_get_universe_first_page_empty_must_raise():
    """**复核修正的核心**：第一页就 0 行 = 参数/列数配错，必须**报错**而不是静默返回 []。

    旧实现把 `EmptyResult` 一律当"到底了"，会导致「少拿数据还不报错」——
    这正是本库红线（不许把"取不到"当"没有"）。
    """
    calls = []

    def fake(market=6, offset=0, page_size=50, cols=None):
        calls.append(offset)
        raise errors.EmptyResult("一上来就空")

    real = hq_pb.get_market_page
    hq_pb.get_market_page = fake
    try:
        try:
            _bare_client().get_universe(market=6, max_pages=5, page_size=50)
        except errors.EmptyResult:
            assert calls == [0], "第一页空就该抛错，不该继续打：%r" % calls
        else:
            raise AssertionError("第一页就空必须抛 EmptyResult（不许静默返回 []）")
    finally:
        hq_pb.get_market_page = real


def test_get_universe_stops_on_short_page():
    """末页不足 page_size 行 → 取完就停，不再多请求一页。"""
    calls = []

    def fake(market=6, offset=0, page_size=50, cols=None):
        calls.append(offset)
        cols = ["Code", "Setcode", "Name", "NOW", "CLOSE"]
        n = 50 if offset == 0 else 7            # 第 2 页只有 7 行
        return cols, [{"Code": "%06d" % (offset + i), "Setcode": "6", "Name": "",
                       "NOW": "1.0", "CLOSE": "1.0"} for i in range(n)]

    real = hq_pb.get_market_page
    hq_pb.get_market_page = fake
    try:
        uni = _bare_client().get_universe(market=6, max_pages=10, page_size=50)
        assert len(uni) == 57 and calls == [0, 50], "短页后必须停：%r" % calls
    finally:
        hq_pb.get_market_page = real


def test_get_universe_stops_at_cap():
    """服务端"永远有下一页"时，必须被 `max_pages` 拦住，不许无限请求。"""
    n = {"c": 0}

    def fake(market=6, offset=0, page_size=50, cols=None):
        n["c"] += 1
        return _fake_page(offset, page_size, market)

    real = hq_pb.get_market_page
    hq_pb.get_market_page = fake
    try:
        _bare_client().get_universe(market=6, max_pages=5, page_size=50)
        assert n["c"] == 5, "max_pages=5 时必须恰好请求 5 次，实际 %d" % n["c"]
    finally:
        hq_pb.get_market_page = real


def test_get_universe_stops_when_page_repeats():
    """服务端不按 offset 挪窗口（同一页一直回）→ 必须靠"无新代码"停下。"""
    n = {"c": 0}

    def fake(market=6, offset=0, page_size=50, cols=None):
        n["c"] += 1
        return _fake_page(0, page_size, market)          # 永远第一页

    real = hq_pb.get_market_page
    hq_pb.get_market_page = fake
    try:
        uni = _bare_client().get_universe(market=6, max_pages=20, page_size=50)
        assert len(uni) == 50, "重复页应当只算一次，实际 %d" % len(uni)
        assert n["c"] == 2, "第 2 页无新代码就该停，实际请求 %d 次" % n["c"]
    finally:
        hq_pb.get_market_page = real


def test_get_universe_rejects_bad_max_pages():
    for bad in (0, -1):
        try:
            _bare_client().get_universe(max_pages=bad)
        except ValueError as e:
            assert "max_pages" in str(e)
        else:
            raise AssertionError("max_pages=%r 必须抛 ValueError" % (bad,))


def test_get_market_page_validates_params():
    """参数自检：market 必须在映射表里、offset ≥ 0、cols 非空。"""
    c = _bare_client()
    # 阶段 3 授权改动：原来这一例用 `market: 9`（当时 9 **未登记**）。
    # 9 现已按 2026-10-27 实网证据登记为「ETF/基金」域，它变成**合法**参数，
    # 所以"未登记 → ValueError"的实例换成真正未登记的 999 —— 判据强度不变。
    for kw, needle in (({"market": 999}, "market"),
                       ({"offset": -1}, "offset"),
                       ({"cols": []}, "cols")):
        try:
            c.get_market_page(**kw)
        except ValueError as e:
            assert needle in str(e), (kw, str(e))
        else:
            raise AssertionError("%r 必须抛 ValueError" % (kw,))


def test_no_double_pacing_in_get_market_page():
    """**闸门只能在一个地方过**：`dc._send`。

    2026-10-26 D2 把 `pacer.wait()` 下沉到 `dc._send`（PB 路径唯一咽喉），
    如果 `get_market_page` 里还留着 wait，每次调用就会等 2×2.2 = 4.4 秒。
    这条用例做源码级护栏（传输层的行为验证在 `tests/test_transport.py`）。
    """
    import inspect

    src = inspect.getsource(hq_pb.get_market_page)
    # 只看函数体（docstring 里会正常提到 pacer，不能误判）
    first = src.find('"""')
    body = src[src.find('"""', first + 3) + 3:] if first >= 0 else src
    assert "pacer" not in body and "wait()" not in body, \
        "get_market_page 里不许再自己 pacer.wait()（会双重等待 4.4s）"


# ══════════════════════════════════════════════════════════════════════════
# 五、红线：该抛错的仍然抛
# ══════════════════════════════════════════════════════════════════════════
def test_get_ticks_now_works_and_pages_by_span_start():
    """`get_ticks` **不再抛错**（2026-10-27 由 `PBTickEx` 打通）—— 钉住翻页与配送。

    离线做法：把引擎那层换成假的两页响应，验证
    ① 首页锚点 `"0"`、② 下一页锚点 = 上一页**区间起点**（不是行里的 `Second`）、
    ③ 按行序号去重、④ 返回时间升序、⑤ `Tick` 的字段映射与单位。
    """
    from tdxdata._engine import hq_extra as _x

    def _page(span, secs):
        start = int(span.split(",")[0])
        rows = [{"Second": str(s), "Now": "1349.13", "NowVol": "2",
                 "VolInStockDiff": "2", "InOutFlag": "1", "HKTTFlag": "\x01",
                 "HKInOutFlag": "0"} for s in secs]
        return {"cols": list(_x.TICKEX_COLS), "rows": rows, "span": span,
                "anchor": (start, start + len(rows)), "date": "20261027"}

    pages = {"0": _page("1995,2000", range(41000, 41005)),
             "1995": _page("1990,1995", range(40990, 40995))}
    used = []

    def fake(code, market, anchor="0", count=100):
        used.append((code, market, anchor))
        return pages.get(anchor)

    c = _bare_client()
    orig = _x.ticks
    try:
        _x.ticks = fake
        ts = c.get_ticks("600519", count=10)
    finally:
        _x.ticks = orig

    assert [a for _c, _m, a in used] == ["0", "1995"], used   # 锚点 = 区间起点
    assert used[0][:2] == ("600519", 1), used[0]              # code / market 传对了
    assert len(ts) == 10, len(ts)
    assert [x.time for x in ts] == sorted(x.time for x in ts), "必须时间升序"
    assert ts[0].time == "11:23:10", ts[0].time               # 40990 → 11:23:10（最旧）
    assert ts[-1].time == "11:23:24", ts[-1].time             # 41004 → 11:23:24（最新）
    assert ts[0].price == 1349.13 and ts[0].vol == 2.0        # price 元 / vol 手
    assert ts[0].symbol == "sh600519"
    assert ts[0].raw["InOutFlag"] == "1", "未定名字段留 raw，不许编成属性"
    # 阶段 4：`trade_count` / `bs_flag` 均为「声明式不可用」（models.UNPROVIDED_TICK_FIELDS）。
    assert all(x.trade_count is None and x.bs_flag is None for x in ts), \
        "服务端没有 trade_count / bs_flag → 必须 None（不许填 0 或 2 冒充）"


def test_get_quotes_now_works_and_maps_row_to_quote():
    """`get_quotes` **不再抛错**（2026-10-27 改走 `PBCombHQ`）——钉住行→Quote 的映射。

    离线做法：把引擎那层换成假响应，只验配送逻辑（列名对齐、昨收口径、raw 不丢、无五档）。
    """
    from tdxdata._engine import hq_extra as _x
    cols = ["Code", "Setcode", "Name", "CLOSE", "OPEN", "MAX", "MIN", "NOW"]
    rows = [["600519", "1", "贵州茅台", "1335.00", "1325.40",
             "1355.00", "1320.00", "1349.98"]]
    c = _bare_client()
    orig = _x.get_quotes
    try:
        _x.get_quotes = lambda syms, batch=20: (cols, rows)
        q = c.get_quotes(["600519"])[0]
    finally:
        _x.get_quotes = orig
    assert (q.code, q.market, q.name) == ("600519", 1, "贵州茅台")
    assert q.price == 1349.98
    assert q.pre_close == 1335.00, "PBCombHQ 的 CLOSE 是昨收，别当当日收盘"
    assert (q.open, q.high, q.low) == (1325.40, 1355.0, 1320.0)
    assert q.raw["Setcode"] == "1", "原始列一个不丢"
    assert q.bid == [] and q.ask == [], "协议无五档，恒空"


def test_not_impl_table_is_honest():
    """`_NOT_IMPL` 只留真正做不到的。

    `get_universe`（2026-10-25）、`get_quotes`（2026-10-27）、
    **`get_ticks`（2026-10-27，`PBTickEx`）** 都已实现，一个都不该在里面。
    """
    from tdxdata.client import _NOT_IMPL
    assert set(_NOT_IMPL) == set(), sorted(_NOT_IMPL)


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
