# -*- coding: utf-8 -*-
"""`tdxdata/sections/flow.py` 的**离线**单元测试（**完全不联网**）。

原则
----
1. 用域文档里的**真实样本**（真实列名 + 首行值）做 fixture，测归一与 `None` 语义；
2. 用**假 client**（只有 `call()`）测参数构造与 `ValueError` / `NotSupportedError`；
3. 不 import `tdxdata.client.Client` —— 脚本跑起来不会建连、不会触发限流。

跑法：python tests/test_sections_flow.py   或   pytest tests/test_sections_flow.py -q
"""
from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tdxdata.errors import EmptyResult, NotSupportedError, ParamError   # noqa: E402
from tdxdata.models import Table                                     # noqa: E402
from tdxdata.sections import flow                                    # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
# 假 client
# ══════════════════════════════════════════════════════════════════════════
class FakeClient:
    """只实现 `call(cmd, ctl, params)`；`by_params` 用于同命令多参数分支。

    行形态**原样保留**（dict 或 list）——服务端真实回的是位置列表，
    fixture 也要能喂 list 才能测出回归。
    """

    def __init__(self, tables=None, errors=None, by_params=None):
        self.tables = dict(tables or {})
        self.errors = dict(errors or {})
        self.by_params = dict(by_params or {})     # (cmd, tuple(params)) → (cols, rows)
        self.calls: list[tuple[str, int, object]] = []

    @staticmethod
    def _rows(rows):
        return [dict(r) if isinstance(r, dict) else list(r) for r in rows]

    def call(self, cmd, ctl, params=None, **_kw):
        self.calls.append((cmd, ctl, params))
        key = (cmd, tuple(params) if isinstance(params, (list, tuple)) else params)
        if key in self.by_params:
            cols, rows = self.by_params[key]
            return Table(cols=list(cols), rows=self._rows(rows), cmd=cmd)
        if cmd in self.errors:
            raise self.errors[cmd]
        if cmd in self.tables:
            cols, rows = self.tables[cmd]
            return Table(cols=list(cols), rows=self._rows(rows), cmd=cmd)
        raise AssertionError("假 client 没准备这条命令：%r params=%r" % (cmd, params))

    def cmd_names(self):
        return [c[0] for c in self.calls]


# ══════════════════════════════════════════════════════════════════════════
# 真实样本 fixture
# ══════════════════════════════════════════════════════════════════════════
def _cxlclr_table():
    """`CWServ.ph_zt_zjlx_cxlclr` 榜单模式：9 列。

    出处：实机验证样本/新股与资金流向.md `ph_zt_zjlx_cxlclr`
      · `00202` 样本 A：['003006','0','百亚股份','181721280','3392245964','24','20261020','-5.0819672','46.58']
      · `00201` 样本 B：['601857','1','中国石油','152152128.00','4328653792.00','17','2026-10-20','1.1811024','21.00']
      · `00201` 样本 B 第 2 行（脏值 `0E-7`）：['601328','1','交通银行','156353216.00','3478804544.00','16','2026-10-20','0E-7','10.32']
    """
    cols = ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "N009"]
    rows = [
        {"N001": "601857", "N002": "1", "N003": "中国石油", "N004": "152152128.00",
         "N005": "4328653792.00", "N006": "17", "N007": "2026-10-20",
         "N008": "1.1811024", "N009": "21.00"},
        {"N001": "601328", "N002": "1", "N003": "交通银行", "N004": "156353216.00",
         "N005": "3478804544.00", "N006": "16", "N007": "2026-10-20",
         "N008": "0E-7", "N009": "10.32"},
        {"N001": "003006", "N002": "0", "N003": "百亚股份", "N004": "",
         "N005": "—", "N006": "24", "N007": "20261020",
         "N008": "-5.0819672", "N009": "46.58"},
    ]
    return cols, rows


def _today_table():
    """`CWServ.sys_fn_jyr_v3`：1 行 × 1 列（当前交易日）。出处：数据中心剩余接口.md。"""
    return ["N001"], [{"N001": "2026-10-23"}]


def _zdt_table():
    """`CWServ.ph_datacenter_zdt_zdfx`：3 列 × 30 行。

    出处：实机验证样本/数据中心剩余接口.md（样本首行 `20261023 / 84 / 7`：
    涨停 84 家、跌停 7 家）。第二行放 `""`/`"—"` 验证 None 语义。
    """
    cols = ["N001", "N002", "N003"]
    rows = [
        {"N001": "20261023", "N002": "84", "N003": "7"},
        {"N001": "20261022", "N002": "", "N003": "—"},
    ]
    return cols, rows


def _bkldscnx_table():
    """`CWServ.ph_pcwebcall_hy_bkldscnx` 视图 002：12 列 × 1 行。

    出处：实机验证样本/市场统计与行业分析.md §十三（样本原样，`N005` 实测就是 null）
    """
    cols = ["N001", "N002", "N003", "N004", "N005", "N006",
            "N007", "N008", "N009", "N010", "N011", "N012"]
    rows = [{
        "N001": "47.81521429",
        "N002": "市场氛围一般",
        "N003": "因为计算市场热度值的源数据存在时滞风险，所以市场热度值存在滞后风险，使用者风险自担。",
        "N004": "79.3042", "N005": None, "N006": "63.7016", "N007": "35.0072",
        "N008": "23.7667", "N009": "10.5452", "N010": "36.6212",
        "N011": "85.7604", "N012": "2026-10-23",
    }]
    return cols, rows


def _hyfx_dir_table():
    """`hyfx_sy` 模式 1 目录：2 列 × 30 行（只取前 3 行，混一个非 X 码验证过滤）。

    出处：市场统计与行业分析.md §三 `[1,"H01"]` → ['X10','煤炭'] ['X11','石油'] ['X12','化工']
    """
    cols = ["N001", "N002"]
    rows = [
        {"N001": "X10", "N002": "煤炭"},
        {"N001": "X11", "N002": "石油"},
        {"N001": "H01", "N002": "其它维度（模式 2 不吃，必须被过滤掉）"},
    ]
    return cols, rows


def _hyfx_detail_table():
    """`hyfx_sy` 模式 2：8 列 × 4 行（煤炭一级下全部成员）。

    出处：市场统计与行业分析.md §三 模式 2 样本原样：
        ['煤炭开采','X1001','881002','Ⅱ','20261019','1.0131','19','6']
        ['焦炭加工','X1002','881005','Ⅱ','20261019','-0.4769','2','4']
    文档已做自洽校验：上级 19 涨 / 6 跌 = 下级 10+9 / 5+1 ✅
    """
    cols = ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008"]
    rows = [
        {"N001": "煤炭开采", "N002": "X1001", "N003": "881002", "N004": "Ⅱ",
         "N005": "20261019", "N006": "1.0131", "N007": "19", "N008": "6"},
        {"N001": "焦炭加工", "N002": "X1002", "N003": "881005", "N004": "Ⅱ",
         "N005": "20261019", "N006": "-0.4769", "N007": "2", "N008": "4"},
    ]
    return cols, rows


# ══════════════════════════════════════════════════════════════════════════
# get_capital_flow（symbols=None）
# ══════════════════════════════════════════════════════════════════════════
def test_capital_flow_none_from_real_sample():
    """全市场排行：真实样本 → 标准字段齐全、单位口径正确、raw 完整。"""
    fc = FakeClient({"CWServ.ph_zt_zjlx_cxlclr": _cxlclr_table()})
    t = flow.get_capital_flow(fc)

    # 契约要求的最低字段全在，且顺序以它们打头
    assert t.cols[:7] == list(flow.CAPITAL_FLOW_HEAD), t.cols
    assert len(t.rows) == 3

    r0 = t.rows[0]
    assert r0["code"] == "601857"
    assert r0["name"] == "中国石油"
    assert r0["market"] == 1 and isinstance(r0["market"], int)
    assert r0["net_inflow"] == 152152128.0          # 元（1.52 亿，量级自洽）
    assert r0["net_inflow_continued"] == 4328653792.0
    assert r0["net_inflow_days"] == 17 and isinstance(r0["net_inflow_days"], int)
    assert r0["change_pct"] == 1.1811024            # 百分数（1.18%），不是 0.0118
    assert r0["change_pct_period"] == 21.0
    assert r0["date"] == "2026-10-20"
    # 本通道不提供价格 / 净额占比 → None，不是 0.0
    assert r0["price"] is None
    assert r0["net_inflow_pct"] is None
    # 红线：9 个原始列一个都不丢
    assert set(r0["raw"]) == set(_cxlclr_table()[0])

    # 参数构造：净流入榜·主力净额、日期留空（服务端给最新）、位置3=6
    assert fc.calls == [(
        "CWServ.ph_zt_zjlx_cxlclr", 0x1A, ["00202", "", 6, 1, 1],
    )], fc.calls
    assert t.provenance.extra["p1"] == "00202"


def test_capital_flow_none_semantics():
    """`""` / `"—"` → None；`"0E-7"` 是合法 0（不能当脏数据丢成 None）。"""
    fc = FakeClient({"CWServ.ph_zt_zjlx_cxlclr": _cxlclr_table()})
    t = flow.get_capital_flow(fc)

    r1 = t.rows[1]                                   # 交通银行：N008 = "0E-7"
    assert r1["change_pct"] == 0.0, r1["change_pct"]
    assert r1["market"] == 1

    r2 = t.rows[2]                                   # 百亚股份：N004="" N005="—"
    assert r2["net_inflow"] is None, r2["net_inflow"]
    assert r2["net_inflow_continued"] is None, r2["net_inflow_continued"]
    assert r2["change_pct"] == -5.0819672
    assert r2["raw"]["N004"] == "" and r2["raw"]["N005"] == "—"
    assert r2["raw"]["N007"] == "20261020"           # 原始日期原样保留


def test_capital_flow_prefers_numeric_market_and_text_codes():
    """代码必须保持**字符串**（前导 0 不能丢）。"""
    fc = FakeClient({"CWServ.ph_zt_zjlx_cxlclr": _cxlclr_table()})
    t = flow.get_capital_flow(fc)
    assert t.rows[2]["code"] == "003006", t.rows[2]["code"]
    assert isinstance(t.rows[2]["code"], str)


def test_capital_flow_rejects_symbols():
    """symbols 非 None → `NotSupportedError`，消息列出三条硬约束与替代物。"""
    fc = FakeClient({"CWServ.ph_zt_zjlx_cxlclr": _cxlclr_table()})
    for bad in ("600519", ["600519"], ["600519", "000001"], 600519):
        try:
            flow.get_capital_flow(fc, bad)
        except NotSupportedError as e:
            msg = str(e)
            assert "PBONEZJLX" in msg, "消息要点出唯一的按代码通道：%s" % msg
            assert "symbols=None" in msg
        else:
            raise AssertionError("symbols=%r 应该抛 NotSupportedError" % (bad,))
    assert fc.calls == [], "拒绝路径不许真的打服务器：%r" % (fc.calls,)


def test_capital_flow_empty_result_propagates():
    """当期无数据 → `EmptyResult` 原样抛，**不许变空 Table**。"""
    fc = FakeClient(errors={"CWServ.ph_zt_zjlx_cxlclr": EmptyResult("0 行")})
    try:
        flow.get_capital_flow(fc)
    except EmptyResult:
        pass
    else:
        raise AssertionError("EmptyResult 必须原样抛")


# ══════════════════════════════════════════════════════════════════════════
# get_market_stat
# ══════════════════════════════════════════════════════════════════════════
def test_market_stat_updown_from_real_sample():
    """updown：先问当前交易日，再查涨跌停统计（**2 次调用**）。"""
    fc = FakeClient({
        "CWServ.sys_fn_jyr_v3": _today_table(),
        "CWServ.ph_datacenter_zdt_zdfx": _zdt_table(),
    })
    t = flow.get_market_stat(fc, "updown")

    assert t.cols == ["stat_date", "limit_up_count", "limit_down_count", "date"], t.cols
    r0 = t.rows[0]
    assert r0["stat_date"] == "20261023"             # 服务端原样
    assert r0["date"] == "2026-10-23"                # 归一版
    assert r0["limit_up_count"] == 84
    assert r0["limit_down_count"] == 7
    assert set(r0["raw"]) == {"N001", "N002", "N003"}

    r1 = t.rows[1]
    assert r1["limit_up_count"] is None and r1["limit_down_count"] is None

    # 参数：日期必须是服务端当前交易日、带横线；ctl 0x1F
    assert fc.calls[0] == ("CWServ.sys_fn_jyr_v3", 0x16, [])
    assert fc.calls[1] == (
        "CWServ.ph_datacenter_zdt_zdfx", 0x1F, ["zdtj", "2026-10-23", 0, ""],
    ), fc.calls[1]


def test_market_stat_updown_normalizes_compact_date():
    """服务端若回 `20261023` 这种无横线日期，也要归一后再构造参数。"""
    fc = FakeClient({
        "CWServ.sys_fn_jyr_v3": (["N001"], [{"N001": "20261023"}]),
        "CWServ.ph_datacenter_zdt_zdfx": _zdt_table(),
    })
    flow.get_market_stat(fc, "updown")
    assert fc.calls[1][2][1] == "2026-10-23"


def test_market_stat_updown_refuses_to_guess_today():
    """取不到交易日 → `NotSupportedError`（**不拿本机时钟冒充**）。"""
    fc = FakeClient({
        "CWServ.sys_fn_jyr_v3": (["N001"], []),      # 0 行
    })
    try:
        flow.get_market_stat(fc, "updown")
    except NotSupportedError as e:
        assert "交易日" in str(e)
    else:
        raise AssertionError("取不到交易日应该抛 NotSupportedError")
    # 只打了探测命令，没有继续拿脏日期去查统计
    assert fc.cmd_names() == ["CWServ.sys_fn_jyr_v3"]


def test_market_stat_sentiment_from_real_sample():
    """sentiment：热度值/中文判语/日期；免责声明与 8 个分项只留 raw。"""
    fc = FakeClient({
        "CWServ.sys_fn_jyr_v3": _today_table(),          # 视图 002 必须先拿交易日
        "CWServ.ph_pcwebcall_hy_bkldscnx": _bkldscnx_table(),
    })
    t = flow.get_market_stat(fc, "sentiment")

    assert t.cols == ["heat_value", "heat_label", "date"], t.cols
    assert len(t.rows) == 1
    r0 = t.rows[0]
    assert abs(r0["heat_value"] - 47.81521429) < 1e-9
    assert r0["heat_label"] == "市场氛围一般"
    assert r0["date"] == "2026-10-23"
    # 红线：名字未破解的分项不许给标准名
    for bad in ("n004", "n005", "factor1", "sub_value", "risk_note", "disclaimer"):
        assert bad not in t.cols, "拿不准含义的列不许给标准名：%s" % bad
    # 免责声明仍保留在 raw 里（原始列一个不丢）
    assert r0["raw"]["N003"].startswith("因为计算市场热度值")
    assert r0["raw"]["N005"] is None

    # 参数：视图 002 + **真实交易日**（实网：传空串 → -1005），ctl 0x21；先探交易日
    assert fc.cmd_names() == ["CWServ.sys_fn_jyr_v3",
                              "CWServ.ph_pcwebcall_hy_bkldscnx"], fc.calls
    assert fc.calls[1][2] == ["002", "2026-10-23"], fc.calls[1]


def test_market_stat_industry_loops_level1_codes():
    """industry：模式 1 取目录 → 逐一级行业码跑模式 2（H01 这种非 X 码必须被过滤）。"""
    fc = FakeClient(by_params={
        ("CWServ.ph_tdxdatacenter_hyfx_sy", (1, "H01")): _hyfx_dir_table(),
        ("CWServ.ph_tdxdatacenter_hyfx_sy", (2, "X10")): _hyfx_detail_table(),
        ("CWServ.ph_tdxdatacenter_hyfx_sy", (2, "X11")): _hyfx_detail_table(),
    })
    t = flow.get_market_stat(fc, "industry")

    assert [c[2] for c in fc.calls] == [[1, "H01"], [2, "X10"], [2, "X11"]], fc.calls
    assert t.provenance.extra["level1_codes"] == ["X10", "X11"]
    assert t.provenance.extra["calls"] == 3

    assert t.cols == ["industry_name", "industry_code", "board_code", "level",
                      "date", "change_pct", "up_count", "down_count"], t.cols
    assert len(t.rows) == 4                          # 2 个一级行业 × 各 2 行明细
    r0 = t.rows[0]
    assert r0["industry_name"] == "煤炭开采"
    assert r0["industry_code"] == "X1001"
    assert r0["board_code"] == "881002"
    assert r0["level"] == "Ⅱ"
    assert r0["change_pct"] == 1.0131                # 百分数
    assert r0["up_count"] == 19 and r0["down_count"] == 6
    assert set(r0["raw"]) == set(_hyfx_detail_table()[0])


def test_market_stat_industry_skips_empty_but_propagates_other_errors():
    """某个一级行业当期无数据 → 跳过并记进 meta；其它异常必须原样往上抛。"""
    class LoopFake(FakeClient):
        """X11 抛 EmptyResult（当期无数据），X99 抛 ParamError（真错误）。"""

        def call(self, cmd, ctl, params=None, **_kw):
            self.calls.append((cmd, ctl, params))
            if isinstance(params, list) and len(params) == 2 and params[0] == 2:
                if params[1] == "X11":
                    raise EmptyResult("该一级行业当期无数据")
                if params[1] == "X99":
                    raise ParamError("参数错")
            cols, rows = self.by_params.get((cmd, tuple(params)))
            return Table(cols=list(cols), rows=[dict(r) if isinstance(r, dict) else list(r) for r in rows], cmd=cmd)

    base = {
        ("CWServ.ph_tdxdatacenter_hyfx_sy", (1, "H01")): _hyfx_dir_table(),
        ("CWServ.ph_tdxdatacenter_hyfx_sy", (2, "X10")): _hyfx_detail_table(),
    }
    fc = LoopFake(by_params=base)
    t = flow.get_market_stat(fc, "industry")
    assert t.provenance.extra["skipped_codes"] == ["X11"], t.meta
    assert len(t.rows) == 2, "只应留下 X10 的 2 行明细"

    # ParamError 不是"没数据"，必须往上抛（不许当空表吞掉）
    fc2 = LoopFake(by_params={
        ("CWServ.ph_tdxdatacenter_hyfx_sy", (1, "H01")):
            (["N001", "N002"], [{"N001": "X99", "N002": "坏行业"}]),
    })
    try:
        flow.get_market_stat(fc2, "industry")
    except ParamError:
        pass
    else:
        raise AssertionError("ParamError 必须原样抛，不能被当成'当期无数据'")


def test_market_stat_default_kind_is_updown():
    """默认 kind 必须是 `updown`（Lead 拍板）：无参调用要能真出数，不能必抛。"""
    fc = FakeClient({
        "CWServ.sys_fn_jyr_v3": _today_table(),
        "CWServ.ph_datacenter_zdt_zdfx": _zdt_table(),
    })
    t = flow.get_market_stat(fc)                    # 不给 kind
    assert t.rows[0]["limit_up_count"] == 84
    assert fc.cmd_names() == ["CWServ.sys_fn_jyr_v3",
                              "CWServ.ph_datacenter_zdt_zdfx"]


def test_market_stat_industry_without_dir_raises_not_supported():
    """目录里没有任何一级行业码 → `NotSupportedError`，不许返回空表。"""
    fc = FakeClient({
        "CWServ.ph_tdxdatacenter_hyfx_sy": (["N001", "N002"], [{"N001": "H01", "N002": "x"}]),
    })
    try:
        flow.get_market_stat(fc, "industry")
    except NotSupportedError as e:
        assert "一级行业码" in str(e)
    else:
        raise AssertionError("没有一级行业码应该抛 NotSupportedError")


def test_market_stat_industry_all_empty_raises_empty_result():
    """逐行业都"有列无行" → `EmptyResult`，**不许返回空 Table**（红线）。"""
    class AllEmptyFake(FakeClient):
        def call(self, cmd, ctl, params=None, **_kw):
            self.calls.append((cmd, ctl, params))
            if isinstance(params, list) and params[0] == 2:
                raise EmptyResult("当期无数据")
            cols, rows = self.by_params[(cmd, tuple(params))]
            return Table(cols=list(cols), rows=[dict(r) if isinstance(r, dict) else list(r) for r in rows], cmd=cmd)

    fc = AllEmptyFake(by_params={
        ("CWServ.ph_tdxdatacenter_hyfx_sy", (1, "H01")): _hyfx_dir_table(),
    })
    try:
        flow.get_market_stat(fc, "industry")
    except EmptyResult as e:
        assert "当期无数据" in str(e)
    else:
        raise AssertionError("全部行业无数据必须抛 EmptyResult，不能返回空表")


def test_market_stat_all_raises_not_supported():
    """`all` 没有单条通道 → `NotSupportedError`，消息列出三个可用 kind。"""
    fc = FakeClient()
    try:
        flow.get_market_stat(fc, "all")
    except NotSupportedError as e:
        msg = str(e)
        for k in ("updown", "industry", "sentiment"):
            assert k in msg, "消息该列出可用 kind %r：%s" % (k, msg)
    else:
        raise AssertionError("kind='all' 应该抛 NotSupportedError")
    assert fc.calls == []


def test_market_stat_unknown_kind_raises_value_error():
    """不认识 kind → `ValueError`，消息里列出**全部**合法取值。"""
    fc = FakeClient()
    try:
        flow.get_market_stat(fc, "zt")
    except ValueError as e:
        msg = str(e)
        for k in flow.MARKET_STAT_KINDS:
            assert k in msg, "ValueError 消息缺合法取值 %r：%s" % (k, msg)
    else:
        raise AssertionError("未知 kind 应该抛 ValueError")
    assert fc.calls == []


def test_market_stat_kind_case_insensitive():
    fc = FakeClient({
        "CWServ.sys_fn_jyr_v3": _today_table(),
        "CWServ.ph_pcwebcall_hy_bkldscnx": _bkldscnx_table(),
    })
    t = flow.get_market_stat(fc, "  SENTIMENT ")
    assert t.rows[0]["heat_label"] == "市场氛围一般"


def test_shape_accepts_real_list_rows():
    """**回归用**：服务端真实 `Table.rows` 是位置列表（`[[v1, v2, ...], ...]`），
    列名在 `Table.cols` 里靠下标对齐 —— 2026-10-25 实网踩过，必须两种形态都吃。

    保留双形态兼容是刻意的：`Row` 已统一形状，但兜底能兜住服务端短行与未来 parse 退化。
    """
    cols = ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "N009"]
    rows = [
        ["601857", "1", "中国石油", "152152128.00", "4328653792.00", "17",
         "2026-10-20", "1.1811024", "21.00"],
        ["601328", "1", "交通银行", "156353216.00", "3478804544.00", "16",
         "2026-10-20", "0E-7", "10.32"],
    ]
    fc = FakeClient({"CWServ.ph_zt_zjlx_cxlclr": (cols, rows)})
    t = flow.get_capital_flow(fc)

    r0 = t.rows[0]
    assert r0["code"] == "601857"
    assert r0["name"] == "中国石油"
    assert r0["market"] == 1
    assert r0["net_inflow"] == 152152128.0
    assert r0["change_pct"] == 1.1811024
    assert r0["price"] is None and r0["net_inflow_pct"] is None
    # raw 也要按列名还原完整（不许因为行是 list 就丢列）
    assert r0["raw"] == {
        "N001": "601857", "N002": "1", "N003": "中国石油",
        "N004": "152152128.00", "N005": "4328653792.00", "N006": "17",
        "N007": "2026-10-20", "N008": "1.1811024", "N009": "21.00",
    }
    assert t.rows[1]["change_pct"] == 0.0        # "0E-7" 仍是 0.0

    # 短行不能越界崩（服务端偶有列数不齐的包）
    fc2 = FakeClient({"CWServ.ph_zt_zjlx_cxlclr": (cols, [["600000", "1"]])})
    t2 = flow.get_capital_flow(fc2)
    assert t2.rows[0]["code"] == "600000"
    assert t2.rows[0]["name"] is None


# ══════════════════════════════════════════════════════════════════════════
# 2026-10-25 实网抓取原样固化（列名 + 前 N 行，值一字未改）
# 这样"实网跑通"不是唯一证据：离线也能复跑真实形状（含 list 行）。
# ══════════════════════════════════════════════════════════════════════════
_LIVE_CXLCLR_COLS = ["N001", "N002", "N003", "N004", "N005",
                     "N006", "N007", "N008", "N009"]
_LIVE_CXLCLR_ROWS = [
    ["003006", "0", "百亚股份", "134297776.00", "3823888036.00", "27",
     "2026-10-23", "6.4211944", "50.00"],
    ["001326", "0", "联域股份", "3598016.00", "300092714.00", "17",
     "2026-10-23", "-2.9126214", "-8.68"],
]
_LIVE_ZDT_COLS = ["N001", "N002", "N003"]
_LIVE_ZDT_ROWS = [["20261023", "84", "7"], ["20261022", "33", "55"]]
_LIVE_BKLDSCNX_COLS = ["N001", "N002", "N003", "N004", "N005", "N006",
                       "N007", "N008", "N009", "N010", "N011", "N012"]
_LIVE_BKLDSCNX_ROWS = [[
    "44.92967500", "市场氛围一般",
    "因为计算市场热度值的源数据存在时滞风险，所以市场热度值存在滞后风险，使用者风险自担。",
    "79.3042", "24.6949", "63.7016", "35.0072", "23.8027",
    "10.5452", "36.6212", "85.7604", "2026-10-23",
]]
# 模式 1 目录实网 30 行全部是 ^X\d{2}$（X10..X99），这里取首行 + 一个非 X 脏码
_LIVE_HYFX_DIR = (["N001", "N002"], [["X10", "煤炭"], ["H01", "其它维度"]])
# 模式 2（X10）实网 4 行 × 8 列，只抓到首行
_LIVE_HYFX_DETAIL = (
    ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008"],
    [["煤炭开采", "X1001", "881002", "Ⅱ", "20261023", "0.1274", "14", "9"]],
)


def test_live_20261025_capital_flow():
    """实网样本：`cxlclr` 榜单 → 单位/口径与列含义必须与抓到的样本一致。"""
    fc = FakeClient({"CWServ.ph_zt_zjlx_cxlclr":
                     (_LIVE_CXLCLR_COLS, _LIVE_CXLCLR_ROWS)})
    t = flow.get_capital_flow(fc)

    assert len(t.rows) == 2
    r0 = t.rows[0]
    assert r0["code"] == "003006"
    assert r0["name"] == "百亚股份"
    assert r0["market"] == 0
    assert r0["net_inflow"] == 134297776.0        # 元（1.34 亿）
    assert r0["net_inflow_continued"] == 3823888036.0
    assert r0["net_inflow_days"] == 27
    assert r0["change_pct"] == 6.4211944          # 百分数：6.42%
    assert r0["change_pct_period"] == 50.0
    assert r0["date"] == "2026-10-23"             # 实网带横线
    assert r0["price"] is None and r0["net_inflow_pct"] is None
    assert r0["raw"] == dict(zip(_LIVE_CXLCLR_COLS, _LIVE_CXLCLR_ROWS[0]))
    assert t.rows[1]["change_pct"] == -2.9126214


def test_live_20261025_market_stat():
    """实网样本：updown / sentiment / industry 三条的取值与列宽。"""
    fc = FakeClient({
        "CWServ.sys_fn_jyr_v3": (["N001"], [["2026-10-23"]]),
        "CWServ.ph_datacenter_zdt_zdfx": (_LIVE_ZDT_COLS, _LIVE_ZDT_ROWS),
        "CWServ.ph_pcwebcall_hy_bkldscnx":
            (_LIVE_BKLDSCNX_COLS, _LIVE_BKLDSCNX_ROWS),
        "CWServ.ph_tdxdatacenter_hyfx_sy": (None, None),   # 由 by_params 覆盖
    }, by_params={
        ("CWServ.ph_tdxdatacenter_hyfx_sy", (1, "H01")): _LIVE_HYFX_DIR,
        ("CWServ.ph_tdxdatacenter_hyfx_sy", (2, "X10")): _LIVE_HYFX_DETAIL,
    })

    up = flow.get_market_stat(fc, "updown")
    assert len(up.rows) == 2
    assert up.rows[0]["limit_up_count"] == 84
    assert up.rows[0]["limit_down_count"] == 7
    assert up.rows[0]["date"] == "2026-10-23"
    assert up.rows[1]["limit_up_count"] == 33

    se = flow.get_market_stat(fc, "sentiment")
    assert abs(se.rows[0]["heat_value"] - 44.929675) < 1e-9
    assert se.rows[0]["heat_label"] == "市场氛围一般"
    assert se.rows[0]["date"] == "2026-10-23"
    assert len(se.rows[0]["raw"]) == 12           # 12 列原样保留

    ind = flow.get_market_stat(fc, "industry")
    assert ind.provenance.extra["level1_codes"] == ["X10"]    # 非 X 码被过滤
    r0 = ind.rows[0]
    assert r0["industry_name"] == "煤炭开采"
    assert r0["industry_code"] == "X1001"
    assert r0["board_code"] == "881002"
    assert r0["level"] == "Ⅱ"
    assert r0["date"] == "2026-10-23"             # 20261023 已归一
    assert r0["change_pct"] == 0.1274             # 百分数
    assert r0["up_count"] == 14 and r0["down_count"] == 9
    # 列数 == 首行值个数 + 1（多出来的那个是 raw）—— 主表列宽已由库保证对齐
    assert len(ind.rows[0]) == len(ind.cols) + 1
    assert len(r0["raw"]) == 8



# ══════════════════════════════════════════════════════════════════════════
# 跨域表契约（`API设计.md §2.7`；Lead 的 `tests/test_table_contract.py` 是总闸）
# 四条不变式：raw 不是数据列 / 每行都带 raw / 列名都能在行里取到 /
#             行宽恰好 = len(cols) + 1（差额只能是 raw 这一个）。
# 落进本文件域，是为了"谁改坏 cols 装配，这里立刻红"。
# ══════════════════════════════════════════════════════════════════════════
def _assert_table_contract(t, label):
    assert t.rows, "%s：返回了空表（红线：绝不用空表冒充『今天没数据』）" % label
    assert "raw" not in t.cols, "%s：raw 是 sidecar，不许进 cols：%s" % (label, t.cols)
    assert "raw" in t.rows[0], "%s：每行必须带 raw（红线：原始列一个不丢）" % label
    missing = set(t.cols) - set(t.rows[0])
    assert not missing, "%s：这些列名在行里取不到：%s" % (label, sorted(missing))
    assert len(t.rows[0]) == len(t.cols) + 1, (
        "%s：首行宽 %d 应恰好等于 len(cols) %d + 1(raw)"
        % (label, len(t.rows[0]), len(t.cols)))
    for j, row in enumerate(t.rows):
        assert isinstance(row.get("raw"), dict) and row["raw"], (
            "%s：第 %d 行 raw 丢了或为空（红线 4）" % (label, j))
        assert len(row) == len(t.cols) + 1, (
            "%s：第 %d 行宽 %d != len(cols) %d + 1"
            % (label, j, len(row), len(t.cols)))
        # 按名取值 == 按位置取值（逐行做：all 是两次调用拼接，行可能来自不同来源）。
        # 这条曾漏掉，导致「只是 cols 赋值、没重绑行位置口径」的 bug 测不出来。
        for i, c in enumerate(t.cols):
            assert row[c] == row[i], (
                "%s：第 %d 行 第 %d 列 %r 按名=%r 但按位置=%r（列口径错位）"
                % (label, j, i, c, row[c], row[i]))


def test_table_contract_outlets():
    """本文件域的 4 个出口：`cols` 不含 raw，但每行都带 raw，行宽 = cols + 1。

    这是"锁约定"不是"修 bug"：Lead 端到端复核已确认 6 个出口在列值对齐上全对，
    唯一差额就是 raw 这一列（`get_capital_flow` cols 11 / 行 12 那种）。
    """
    fc = FakeClient({
        "CWServ.sys_fn_jyr_v3": (["N001"], [["2026-10-23"]]),
        "CWServ.ph_datacenter_zdt_zdfx": (_LIVE_ZDT_COLS, _LIVE_ZDT_ROWS),
        "CWServ.ph_pcwebcall_hy_bkldscnx":
            (_LIVE_BKLDSCNX_COLS, _LIVE_BKLDSCNX_ROWS),
        "CWServ.ph_zt_zjlx_cxlclr": (_LIVE_CXLCLR_COLS, _LIVE_CXLCLR_ROWS),
    }, by_params={
        ("CWServ.ph_tdxdatacenter_hyfx_sy", (1, "H01")): _LIVE_HYFX_DIR,
        ("CWServ.ph_tdxdatacenter_hyfx_sy", (2, "X10")): _LIVE_HYFX_DETAIL,
    })

    _assert_table_contract(flow.get_capital_flow(fc), "get_capital_flow()")
    _assert_table_contract(flow.get_market_stat(fc, "updown"),
                           "get_market_stat('updown')")
    _assert_table_contract(flow.get_market_stat(fc, "industry"),
                           "get_market_stat('industry')")
    _assert_table_contract(flow.get_market_stat(fc, "sentiment"),
                           "get_market_stat('sentiment')")


# ══════════════════════════════════════════════════════════════════════════
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
