# -*- coding: utf-8 -*-
"""`tdxdata/sections/boards.py` 的**离线**单元测试（**完全不联网**）。

原则
----
1. 用 实机验证样本 + 域文档里的**真实样本**（真实列名 + 首行值）
   做 fixture，喂给归一函数，断言标准字段齐全、`raw` 完整、`None` 语义正确。
2. 用**假 client**（只有 `call()`，返回固定 `Table`）测参数构造与
   `ValueError` / `NotSupportedError` 分支 —— 根本不 import `tdxdata.client.Client`，
   所以脚本跑起来既不会建连也不会触发限流。

跑法：python tests/test_sections_boards.py   或   pytest tests/test_sections_boards.py -q
"""
from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tdxdata.errors import EmptyResult, NotSupportedError          # noqa: E402
from tdxdata.models import Table                                   # noqa: E402
from tdxdata.sections import boards                                # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
# 假 client：只有一个 call()，按命令名回固定表，并记录每次调用
# ══════════════════════════════════════════════════════════════════════════
class FakeClient:
    """只实现 `call(cmd, ctl, params)`，不建连、不限流。"""

    def __init__(self, tables=None, errors=None):
        self.tables = dict(tables or {})
        self.errors = dict(errors or {})
        self.calls: list[tuple[str, int, object]] = []

    def call(self, cmd, ctl, params=None, **_kw):
        self.calls.append((cmd, ctl, params))
        if cmd in self.errors:
            raise self.errors[cmd]
        cols, rows = self.tables[cmd]
        return Table(cols=list(cols), rows=[dict(r) if isinstance(r, dict) else list(r) for r in rows], cmd=cmd)

    # 便于断言
    def cmd_names(self):
        return [c[0] for c in self.calls]

    def board_page(self, offset=0, page_size=44, market=10004):
        """板块清单分页（PBMultiHQ 板块域）。2026-10-26 实网每类前两行的原样值。"""
        self.calls.append(("PBMultiHQ.board_page", 0x12,
                           {"offset": offset, "page_size": page_size,
                            "market": market}))
        if offset > 0:
            return list(_MULTIHQ_BOARD_COLS), []
        rows = _MULTIHQ_BY_MARKET.get(market, [])
        return list(_MULTIHQ_BOARD_COLS), [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════
# 真实样本 fixture（出处逐条标注）
# ══════════════════════════════════════════════════════════════════════════
def _bklist_ag_table():
    """`CWServ.app_bklist_ag`：9 列 × 128 行。

    **2026-10-25 实网抓取原样固化**（前 4 行，值一字未改）。实网核对结论：
      * `N002` 全 128 行皆 881xxx 数值（881002..881479）→ 板块指数代码
      * `N001 == N005 == N008`（128 行皆成立）→ 三级行业码
      * `N003 == N009`，128 行唯一 → 行业名
      * `N006` 去重 30 个（X10..X99）→ 一级行业码；`N007` 去重 30 个 → 一级行业名
      * `N004` 全 128 行恒为 1（不是涨跌幅，无量纲含义未确义）
    交叉验证：`N002`=881002/881007/881016 与 `hyfx_sy` 模式 2 的 `N003` 三处一致。
    """
    cols = ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "N009"]
    rows = [
        {"N001": "X1001", "N002": "881002", "N003": "煤炭开采", "N004": "1",
         "N005": "X1001", "N006": "X10", "N007": "煤炭", "N008": "X1001",
         "N009": "煤炭开采"},
        {"N001": "X1002", "N002": "881005", "N003": "焦炭加工", "N004": "1",
         "N005": "X1002", "N006": "X10", "N007": "煤炭", "N008": "X1002",
         "N009": "焦炭加工"},
        {"N001": "X1101", "N002": "881007", "N003": "油气开采", "N004": "1",
         "N005": "X1101", "N006": "X11", "N007": "石油", "N008": "X1101",
         "N009": "油气开采"},
        {"N001": "X1102", "N002": "881008", "N003": "油服工程", "N004": "1",
         "N005": "X1102", "N006": "X11", "N007": "石油", "N008": "X1102",
         "N009": "油服工程"},
    ]
    return cols, rows


# 兼容旧名（本文件内多处引用）
_hyfx_dir_table = _bklist_ag_table


def _tdxrb_block_table():
    """`CWServ.ph_tdxdatacenter_tdxrb_block`：14 列 × 20 行。

    **2026-10-25 实网抓取原样固化**（前 2 行）。实网首行：
        N001=2026-10-25 20:40  N002=机器人概念  N003=880904  N004=1
        N005=(空) N006=(空) N007=1 N008=1248003 N009=(空) N010=(空)
        N011=1 N012=1567864 N013=0 N014=None
    ⚠️ 注意实网里 `N008/N012` 的数值与 `N009` 的空值都发生了变化（热度是实时量），
    再次证明这两列只能留 raw、不能给标准名。
    """
    cols = ["N001", "N002", "N003", "N004", "N005", "N006", "N007",
            "N008", "N009", "N010", "N011", "N012", "N013", "N014"]
    rows = [
        {"N001": "2026-10-25 20:40", "N002": "机器人概念", "N003": "880904",
         "N004": "1", "N005": "", "N006": "", "N007": "1", "N008": "1248003",
         "N009": "", "N010": "", "N011": "1", "N012": "1567864",
         "N013": "0", "N014": None},
        {"N001": "2026-10-25 20:40", "N002": "数据要素", "N003": "880667",
         "N004": "1", "N005": "", "N006": "", "N007": "2", "N008": "1183134",
         "N009": "", "N010": "", "N011": "2", "N012": "1482115",
         "N013": "0", "N014": None},
    ]
    return cols, rows


def _zttz_xggp_table():
    """`CWServ.ph_tdxdatacenter_zttz_xggp`：9 列 × 477 行（`p5=500`）。

    **2026-10-25 实网抓取原样固化**（前 2 行，值一字未改）：
        ['2','880703','2998','603728','鸣志电器','1','5','入选理由全文','2024-02-27']
    第二行把 `N006`（市场）与 `N009`（日期）留成服务端可能给的脏写法，
    用来验证 `None` 语义与日期不硬转数值。
    """
    cols = ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "N009"]
    rows = [
        {"N001": "2", "N002": "880703", "N003": "2998", "N004": "603728",
         "N005": "鸣志电器", "N006": "1", "N007": "5",
         "N008": "公司在人形机器人应用领域专注于手掌模组及指模所用的控制电机模组的研发和制造。",
         "N009": "2024-02-27"},
        {"N001": "2", "N002": "880703", "N003": "2999", "N004": "002402",
         "N005": "和而泰", "N006": "", "N007": "5", "N008": "—", "N009": ""},
    ]
    return cols, rows


def _fake_all():
    return FakeClient({
        "CWServ.app_bklist_ag": _bklist_ag_table(),
        "CWServ.ph_tdxdatacenter_zttz_xggp": _zttz_xggp_table(),
    })


_MULTIHQ_BOARD_COLS = ["Code", "Setcode", "Name", "CLOSE", "NOW", "AMOUNT"]


# 六类板块域 → 2026-10-26 实网每类前 2 行（`cap4.pcap` / 邻近域扫描原样值）
# 行数与手机端逐个点数一致：30 / 128 / 345 / 269 / 158 / 32
_MULTIHQ_BY_MARKET = {
    10008: [                                   # 一级行业 30 个
        {"Code": "880305", "Setcode": 1, "Name": "石油",
         "CLOSE": "1201.33", "NOW": "1198.02", "AMOUNT": "0.00"},
        {"Code": "880301", "Setcode": 1, "Name": "房地产",
         "CLOSE": "880.11", "NOW": "876.40", "AMOUNT": "0.00"},
    ],
    10001: [                                   # 二级行业 128 个
        {"Code": "881107", "Setcode": 1, "Name": "油气开采",
         "CLOSE": "1502.66", "NOW": "1499.10", "AMOUNT": "0.00"},
        {"Code": "881108", "Setcode": 1, "Name": "油服工程",
         "CLOSE": "993.20", "NOW": "991.55", "AMOUNT": "0.00"},
    ],
    10002: [                                   # 三级行业 345 个
        {"Code": "881281", "Setcode": 1, "Name": "会展服务",
         "CLOSE": "712.44", "NOW": "710.90", "AMOUNT": "0.00"},
        {"Code": "881262", "Setcode": 1, "Name": "合成树脂",
         "CLOSE": "1043.02", "NOW": "1040.11", "AMOUNT": "0.00"},
    ],
    10004: [                                   # 概念 269 个
        {"Code": "880903", "Setcode": 1, "Name": "水产品",
         "CLOSE": "551.40", "NOW": "545.85", "AMOUNT": "0.00"},
        {"Code": "880904", "Setcode": 1, "Name": "机器人概念",
         "CLOSE": "2841.55", "NOW": "2850.12", "AMOUNT": "0.00"},
    ],
    10005: [                                   # 风格 158 个
        {"Code": "880601", "Setcode": 1, "Name": "昨日连板",
         "CLOSE": "1502.11", "NOW": "1498.30", "AMOUNT": "0.00"},
        {"Code": "880602", "Setcode": 1, "Name": "昨日涨停",
         "CLOSE": "1776.40", "NOW": "1770.02", "AMOUNT": "0.00"},
    ],
    10006: [                                   # 地区 32 个
        {"Code": "880321", "Setcode": 1, "Name": "宁夏板块",
         "CLOSE": "880.02", "NOW": "877.11", "AMOUNT": "0.00"},
        {"Code": "880322", "Setcode": 1, "Name": "北京板块",
         "CLOSE": "2210.44", "NOW": "2205.60", "AMOUNT": "0.00"},
    ],
}


def _multihq_board_table():
    """概念板块前两行（兼容旧调用）。"""
    return _MULTIHQ_BOARD_COLS, _MULTIHQ_BY_MARKET[10004]


# ══════════════════════════════════════════════════════════════════════════
# get_boards：正常路径
# ══════════════════════════════════════════════════════════════════════════
def test_boards_industry_from_real_sample():
    """industry：`app_bklist_ag` 实网样本（2026-10-25）→ 标准字段齐全、raw 完整。"""
    fc = _fake_all()
    t = boards.get_boards(fc, "industry")

    assert t.cols == ["board_code", "board_name", "board_type", "change_pct",
                      "now", "close",
                      "industry_code", "level1_code", "level1_name"], t.cols
    assert len(t.rows) == 4
    first = t.rows[0]
    assert first["board_code"] == "881002"          # 板块指数代码（N002）
    assert first["board_name"] == "煤炭开采"        # 行业名（N003）
    assert first["industry_code"] == "X1001"        # 三级行业码（N001）
    assert first["level1_code"] == "X10"            # 一级行业（N006）
    assert first["level1_name"] == "煤炭"           # 一级行业名（N007）
    assert first["board_type"] == "industry"
    # 契约：拿不到的列不要硬造 —— 这条通道没有涨跌幅 → None，不是 0.0
    assert first["change_pct"] is None, "change_pct 无通道时必须 None，不能填 0.0"

    # 红线：原始列一个都不许丢（9 列全在）
    assert set(first["raw"]) == {("N00%d" % i) for i in range(1, 10)}, first["raw"]
    assert first["raw"]["N004"] == "1"              # 恒 1 的列只留 raw，没给标准名
    assert first["raw"]["N009"] == "煤炭开采"       # 与 N003 重复，只留 raw

    # 阶段 4：`app_bklist_ag` 无价格列，故 industry 支路会**另打一轮**
    # `PBMultiHQ(market=10001)`（industry2）按同码回填 change_pct/now/close。
    assert fc.calls[0] == ("CWServ.app_bklist_ag", 0x16, ["2"]), fc.calls
    backfill = fc.calls[1:]
    assert backfill, "industry 支路必须有回填轮：%r" % (fc.calls,)
    assert all(c[0] == "PBMultiHQ.board_page" for c in backfill), fc.calls
    assert {c[2]["market"] for c in backfill} == {10001}, fc.calls
    # 本 fixture 的 industry2 里没有 881002/881005/881007/881008 →
    # 回填**必须留 None，不许猜**，并把四个码报进 change_pct_unavailable
    assert t.provenance.extra["change_pct_method"] == "industry2 回填", t.provenance
    assert t.provenance.extra["unavailable"] == ["change_pct"], t.provenance
    assert t.provenance.extra["change_pct_unavailable"] == ["881002", "881005", "881007", "881008"], t.provenance


def test_boards_concept_from_real_sample():
    """concept：`PBMultiHQ(market=10004)` 板块清单 → 代码/名称，其余留 raw。"""
    fc = _fake_all()
    t = boards.get_boards(fc, "concept")

    assert t.cols == ["board_code", "board_name", "board_type", "change_pct",
                      "now", "close"]
    assert len(t.rows) == 2
    # 阶段 4：concept 走 PBMultiHQ，`CLOSE`(昨收)/`NOW`(现价) 100% 非空 →
    # change_pct 由 `(NOW/CLOSE-1)*100` **实算**，不再是 None。
    # 这里逐行按 fake 里的真实值复算，避免硬编码常量。
    for r in t.rows:
        want = (float(r["now"]) / float(r["close"]) - 1.0) * 100.0
        assert abs(r["change_pct"] - want) < 1e-9, (r["board_code"], r["change_pct"], want)
    assert abs(t.rows[0]["change_pct"] - (-1.0065288356909563)) < 1e-9, t.rows[0]
    assert t.provenance.extra["change_pct_method"] == "PBMultiHQ.NOW/CLOSE", t.provenance
    assert t.provenance.extra["unavailable"] == [], "concept 的涨跌幅现在算得出来，不该再声明缺列"
    r0 = t.rows[0]
    assert r0["board_code"] == "880903"
    assert r0["board_name"] == "水产品"
    assert r0["board_type"] == "concept"

    # 原始列一列不少，数值留在 raw 里（涨跌幅口径未定论，不给标准名）
    assert set(r0["raw"]) >= {"Code", "Setcode", "Name", "CLOSE", "NOW"}
    assert r0["raw"]["CLOSE"] == "551.40"
    for bad in ("change", "zaf", "20zaf", "heat"):
        assert bad not in t.cols, "拿不准含义的列不许给标准名：%s" % bad

    # 翻页：第一页有数据、第二页空 → 停
    assert fc.calls[0][0] == "PBMultiHQ.board_page"
    assert fc.calls[0][2]["offset"] == 0
    assert [c[2]["offset"] for c in fc.calls] == [0, 44, 88]


def test_shape_accepts_real_list_rows():
    """**回归用**：服务端真实 `Table.rows` 是位置列表（列名在 `Table.cols` 里靠下标对齐），
    2026-10-25 实网踩过 —— 两种行形态都必须能吃，raw 也要按列名还原。

    保留双形态兼容是刻意的：`Row` 已统一形状，但兜底能兜住服务端短行与未来 parse 退化。
    """
    fc = FakeClient({
        # app_bklist_ag 真实形态：list 行（服务端就是位置数组）
        "CWServ.app_bklist_ag": (
            ["N001", "N002", "N003", "N006", "N007"],
            [["X1001", "881002", "煤炭开采", "X10", "煤炭"],
             ["X1101", "881007", "油气开采", "X11", "石油"]]),
        # tdxrb_block 真实形态：list 行（只给 3 列够判逻辑）
        "CWServ.ph_tdxdatacenter_tdxrb_block": (
            ["N001", "N002", "N003"],
            [["2026-10-23 23:15", "机器人概念", "880904"]]),
        "CWServ.ph_tdxdatacenter_zttz_xggp": (
            ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "N009"],
            [["2", "880703", "2998", "603728", "鸣志电器", "1", "5", "理由", "2024-02-27"]]),
    })

    t = boards.get_boards(fc, "industry")
    assert t.rows[0]["board_code"] == "881002"
    assert t.rows[0]["board_name"] == "煤炭开采"
    assert t.rows[0]["raw"] == {"N001": "X1001", "N002": "881002",
                               "N003": "煤炭开采", "N006": "X10", "N007": "煤炭"}

    t = boards.get_boards(fc, "concept")
    assert t.rows[0]["board_code"] == "880903"
    assert t.rows[0]["raw"]["Code"] == "880903"

    t = boards.get_board_members(fc, "880703")
    assert t.rows[0]["code"] == "603728"
    assert t.rows[0]["name"] == "鸣志电器"
    assert t.rows[0]["market"] == 1
    assert t.rows[0]["raw"]["N005"] == "鸣志电器"

    # 短行不许越界崩
    fc2 = FakeClient({
        "CWServ.ph_tdxdatacenter_zttz_xggp":
            (["N001", "N004", "N005"], [["2", "600000"]]),
    })
    t2 = boards.get_board_members(fc2, "880703")
    assert t2.rows[0]["code"] == "600000"
    assert t2.rows[0]["name"] is None


def test_boards_meta_declares_unavailable_column():
    """Lead 要求：哪一列拿不到，就必须在 `meta` 里显式声明，不能只写 docstring。

    阶段 4 起 `change_pct` **可以算**（PBMultiHQ 的 `NOW/CLOSE`），所以「必须声明缺列」
    的判据换成**按 kind 分别期望**：算得出来的不许声明缺，算不出来的必须声明缺。
    """
    fc = _fake_all()
    # concept：PBMultiHQ 自带 CLOSE/NOW → 算得出来 → 不许声明缺列
    t = boards.get_boards(fc, "concept")
    assert t.provenance.extra.get("unavailable") == [], t.provenance
    assert t.provenance.extra.get("change_pct_method") == "PBMultiHQ.NOW/CLOSE", t.provenance
    # industry：本 fixture 的 industry2 里没有这 4 个码 → 必须声明缺列，并点名是哪些码
    for kind in ("industry", "all"):
        fc = _fake_all()
        t = boards.get_boards(fc, kind)
        assert t.provenance.extra.get("unavailable") == ["change_pct"], (kind, t.provenance)
        assert t.provenance.extra.get("change_pct_unavailable"), (kind, t.provenance)
        why = "；".join(t.provenance.notes)
        assert isinstance(why, str) and len(why) > 10, (kind, why)
        assert "涨跌" in why, (kind, why)


def test_boards_all_is_documented_partial_union():
    """`all` = industry ∪ concept（两次调用），并在 meta 里标 partial。"""
    fc = _fake_all()
    t = boards.get_boards(fc, "all")

    names = fc.cmd_names()
    assert names[0] == "CWServ.app_bklist_ag", names
    assert set(names[1:]) == {"PBMultiHQ.board_page"}, names
    assert len(t.rows) == 6, len(t.rows)          # 4 行业 + 2 概念
    types = [r["board_type"] for r in t.rows]
    assert types[:4] == ["industry"] * 4
    assert types[4:] == ["concept"] * 2
    # 超集列：concept 行没有行业层级 → None（不是缺列）
    assert t.cols == ["board_code", "board_name", "board_type", "change_pct",
                      "now", "close",
                      "industry_code", "level1_code", "level1_name"], t.cols
    assert t.rows[-1]["industry_code"] is None
    assert t.rows[0]["level1_code"] == "X10"
    assert t.provenance.extra["partial"] is True
    assert t.provenance.extra["missing"] == ["index"]
    # 每行 raw 里加了 _source，能追溯来自哪条命令
    assert t.rows[0]["raw"]["_source"] == "app_bklist_ag"
    assert t.rows[-1]["raw"]["_source"] == "PBMultiHQ"


# ══════════════════════════════════════════════════════════════════════════
# get_boards：拒绝路径
# ══════════════════════════════════════════════════════════════════════════
def test_boards_area_and_index_raise_not_supported():
    """area / index 没有通道 → `NotSupportedError`，**绝不返回空表**。"""
    fc = _fake_all()
    for kind in ("index",):
        try:
            boards.get_boards(fc, kind)
        except NotSupportedError as e:
            msg = str(e)
            assert kind in msg, msg
            assert len(msg) > 30, "消息必须说清为什么没有通道：%r" % msg
        else:
            raise AssertionError("get_boards(%r) 应该抛 NotSupportedError" % kind)
    # 抛异常就不许真的去打服务器
    assert fc.calls == [], fc.calls


def test_boards_unknown_kind_raises_value_error_listing_legal_values():
    """不认识 kind → `ValueError`，消息里列出**全部**合法取值。"""
    fc = _fake_all()
    try:
        boards.get_boards(fc, "gn")
    except ValueError as e:
        msg = str(e)
        for k in boards.BOARD_KINDS:
            assert k in msg, "ValueError 消息缺合法取值 %r：%s" % (k, msg)
    else:
        raise AssertionError("未知 kind 应该抛 ValueError")
    assert fc.calls == []


def test_boards_kind_is_case_and_space_insensitive():
    fc = _fake_all()
    t = boards.get_boards(fc, "  Industry ")
    assert t.rows[0]["board_type"] == "industry"


def test_boards_empty_result_propagates_not_empty_table():
    """服务端"有列无行" → `EmptyResult` 往上抛，**不许转成空 Table**。"""
    fc = FakeClient(errors={"CWServ.app_bklist_ag": EmptyResult("0 行")})
    try:
        boards.get_boards(fc, "industry")
    except EmptyResult:
        pass
    else:
        raise AssertionError("EmptyResult 必须原样抛出，不能吞成空表")


# ══════════════════════════════════════════════════════════════════════════
# get_board_members
# ══════════════════════════════════════════════════════════════════════════
def test_board_members_by_code_uses_real_sample():
    """传板块代码 → 1 次调用；契约要求的 5 个最低字段齐全。"""
    fc = _fake_all()
    t = boards.get_board_members(fc, "880703")

    assert fc.calls == [(
        "CWServ.ph_tdxdatacenter_zttz_xggp", 0x23,
        ["00901", "880703", "2", 1, 500, 0, 1],   # p5=500：实网回 477 行（全部成员）
    )], fc.calls

    assert t.cols == list(boards._MEMBER_COLS)
    r0 = t.rows[0]
    assert r0["code"] == "603728"
    assert r0["name"] == "鸣志电器"
    assert r0["market"] == 1 and isinstance(r0["market"], int)
    assert r0["board_code"] == "880703"
    assert r0["theme_id"] == "2998"
    assert r0["list_date"] == "2024-02-27"
    # 本通道不提供价格/涨跌幅 → None（不是 0.0，也不是让列缺席）
    assert r0["price"] is None
    assert r0["change_pct"] is None
    # Lead 要求：拿不到的列必须在 meta 里显式声明
    assert t.provenance.extra.get("unavailable") == ["price", "change_pct"], t.provenance
    assert "不含价格与涨跌幅" in t.provenance.extra.get("why", ""), t.provenance
    # raw 完整（9 列全在）
    assert set(r0["raw"]) == set(_zttz_xggp_table()[0])


def test_board_members_none_semantics_from_dirty_values():
    """服务端给 `""` / `"—"` 的位填 None；`market` 无值也是 None（不是 0）。"""
    fc = _fake_all()
    t = boards.get_board_members(fc, "880703")
    r1 = t.rows[1]
    assert r1["code"] == "002402"
    assert r1["market"] is None, r1["market"]
    assert r1["reason"] is None, r1["reason"]
    assert r1["list_date"] is None, r1["list_date"]
    assert r1["raw"]["N006"] == ""          # 原始值原样保留
    assert r1["raw"]["N008"] == "—"


def test_board_members_by_name_resolves_two_calls():
    """板块名 → 先查板块清单拿代码，再取成分（**2 次调用**，docstring 已写明）。"""
    fc = _fake_all()
    t = boards.get_board_members(fc, "机器人概念")

    names = fc.cmd_names()
    assert names[-1] == "CWServ.ph_tdxdatacenter_zttz_xggp", names
    assert set(names[:-1]) == {"PBMultiHQ.board_page"}, names
    assert "880904" in fc.calls[-1][2], fc.calls[-1]
    assert t.rows[0]["board_code"] == "880703"    # 表里回显的是服务端列，不是请求参


def test_board_members_industry_board_raises_not_supported():
    """行业板块（881xxx / 行业名）没有成分通道 → `NotSupportedError`，消息说清原因。"""
    fc = _fake_all()
    try:
        boards.get_board_members(fc, "881002")
    except NotSupportedError as e:
        msg = str(e)
        assert "881002" in msg
        for kw in ("agf10_hypm", "hyfx_sy"):
            assert kw in msg, "消息里该点出替代/相关命令 %r：%s" % (kw, msg)
    else:
        raise AssertionError("行业板块应该抛 NotSupportedError，而不是空表")

    # 走名字路径命中行业目录里的名字，同样拒绝
    fc2 = _fake_all()
    try:
        boards.get_board_members(fc2, "煤炭开采")
    except NotSupportedError:
        pass
    else:
        raise AssertionError("行业名也应该抛 NotSupportedError")


def test_board_members_rejects_stock_code_and_unknown_name():
    """股票代码 / 不存在的名字 → `ValueError`，消息说明只接受什么。"""
    fc = _fake_all()
    try:
        boards.get_board_members(fc, "600519")
    except ValueError as e:
        assert "股票代码" in str(e) and "88" in str(e), str(e)
    else:
        raise AssertionError("股票代码应该抛 ValueError")

    fc2 = _fake_all()
    try:
        boards.get_board_members(fc2, "查无此板块")
    except ValueError as e:
        assert "查无此板块" in str(e)
        assert "880904" in str(e) or "机器人概念" in str(e), \
            "ValueError 应该给出可用候选/写法：%s" % e
    else:
        raise AssertionError("不存在的板块名应该抛 ValueError")

    for bad in (None, "", "   "):
        try:
            boards.get_board_members(_fake_all(), bad)
        except ValueError:
            pass
        else:
            raise AssertionError("board=%r 应该抛 ValueError" % (bad,))


def test_board_members_accepts_int_code():
    """数字型入参（`880703` 而非 `"880703"`）也要能用。"""
    fc = _fake_all()
    t = boards.get_board_members(fc, 880703)
    assert fc.calls[0][2][1] == "880703"
    assert t.rows[0]["code"] == "603728"



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
    唯一差额就是 raw 这一列（`cols` 7 / 行 8 那种）。
    """
    fc = _fake_all()
    for kind in ("industry", "concept", "all"):
        _assert_table_contract(boards.get_boards(fc, kind), "get_boards(%r)" % kind)
    _assert_table_contract(boards.get_board_members(fc, "880703"),
                           "get_board_members('880703')")
    _assert_table_contract(boards.get_board_members(fc, "机器人概念"),
                           "get_board_members('机器人概念')")


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


# ══════════════════════════════════════════════════════════════════════════
# 六类板块域（2026-10-26 抓包：field4 = 10xxx）
# ══════════════════════════════════════════════════════════════════════════
def test_boards_six_domains_map_to_right_market():
    """industry1/2/3 · concept · style · area 各自打到对的 `field4`。

    行数与手机端逐个点数一致：30 / 128 / 345 / 269 / 158 / 32（实网 2026-10-26）。
    """
    want = {
        "industry1": (10008, "880305", "石油"),
        "industry2": (10001, "881107", "油气开采"),
        "industry3": (10002, "881281", "会展服务"),
        "concept": (10004, "880903", "水产品"),
        "style": (10005, "880601", "昨日连板"),
        "area": (10006, "880321", "宁夏板块"),
    }
    for kind, (market, code, name) in want.items():
        fc = _fake_all()
        t = boards.get_boards(fc, kind)
        assert fc.calls[0][2]["market"] == market, (kind, fc.calls[0])
        assert t.rows[0]["board_code"] == code, kind
        assert t.rows[0]["board_name"] == name, kind
        assert t.rows[0]["board_type"] == kind, kind
        assert t.provenance.extra["params"]["market"] == market, kind
        # 阶段 4：涨跌幅由 `NOW/CLOSE-1` 实算（六类的 fake 行都带 CLOSE/NOW）——
        # 逐行按 fake 真值复算，不再断言 None。
        want_cp = (float(t.rows[0]["now"]) / float(t.rows[0]["close"]) - 1.0) * 100.0
        assert abs(t.rows[0]["change_pct"] - want_cp) < 1e-9, (kind, t.rows[0])