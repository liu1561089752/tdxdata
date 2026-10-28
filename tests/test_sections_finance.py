# -*- coding: utf-8 -*-
"""财报语义层（P3）的**离线**单元测试 —— 全程不联网，一条真实命令都不发。

fixture 全部来自 实机验证样本 与 实机验证样本（F10）
里的**真实样本**（列名 + 首行值），不是我编的数。

跑法：python tests/test_sections_finance.py   或   pytest tests/test_sections_finance.py -q
"""
from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tdxdata import errors                                              # noqa: E402
from tdxdata.models import Table                                        # noqa: E402
from tdxdata.sections import finance, mapping                           # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
# fixture 构造：按文档记录的**列顺序**摆值
# ══════════════════════════════════════════════════════════════════════════
def _fin_cols():
    """出处: F10与个股资料.md §三 cw_zcfzb（RS1 = 23 行 × 81 列 = N001…N080, Tdate）。"""
    return ["N%03d" % i for i in range(1, 81)] + ["Tdate"]


def _row_of(cols, **named):
    """按列名摆值，其余补 None。"""
    return [named.get(c) for c in cols]


from datetime import datetime as _dt
from tdxdata._provenance import ClockSource as _CS, Provenance

_T = _dt(2026, 10, 28, 9, 30)


def _prov_of(meta, cmd=""):
    """旧 meta dict 夹具 → Provenance（extra 收结构化旁证）。"""
    return (Provenance(fetched_at=_T, clock=_CS.SERVER, cmd=cmd,
                       extra=dict(meta)) if meta else None)


def fin_table(with_union_provenance=False):
    """一份真实味道的资产负债表（600303 / 2026 中报）。

    值来源: F10与个股资料.md §三 cw_zcfzb 样本首行 + 恒等式验算值
            （N038=N067+N077=3104851614.01 等，23/23 实测全对的那几个数）。

    `with_union_provenance=True` 模拟**真实 `client.call()` 的形态**：
    `cols` 是主表自己的 81 列（与行值严格对齐），各 RS 的「列名并集」放在
    `provenance.extra["cols_union"]`（协议层 `client._make_table` 就是这么给的）。
    """
    vals = {
        "N001": "2026中报",
        "N002": "58154644.44",        # 货币资金
        "N004": "375442742.96",
        "N005": "72483587.75",
        "N006": "302959155.21",
        "N007": "79490516.76",
        "N008": "6455157.85",
        "N011": "660511622.60",
        "N012": "41434910.10",
        "N013": "3738571.66",
        "N014": "14838210.97",
        "N015": "1283414820.87",      # 流动资产合计
        "N037": "1821436793.14",      # 非流动资产合计（= N038 - N015）
        "N038": "3104851614.01",      # 资产总计
        "N067": "2203108875.58",      # 负债合计
        "N077": "901742738.43",       # 所有者权益合计
        "N078": "878407168.86",       # 归母权益
        "N079": "23335569.57",        # 少数股东权益
        "Tdate": "2026-06-30",
    }
    cols = _fin_cols()
    row = _row_of(cols, **vals)
    if not with_union_provenance:
        return Table(cols=cols, rows=[row], cmd="CWServ.ph_agf10_cw_zcfzb")
    return Table(cols=cols, rows=[row], cmd="CWServ.ph_agf10_cw_zcfzb",
                 provenance=_prov_of({"cols_source": "primary",
                                      "cols_union": ["isjr"] + cols}))


def lyb_table():
    """出处: F10与个股资料.md §三 cw_lyb 样本首行（24 期 × 38 列）。"""
    cols = ["N%03d" % i for i in range(1, 35)] + ["Tdate", "N035", "N036", "N037"]
    vals = {
        "N001": "2026中报",
        "N002": "505576951.99",       # 营业总收入
        "N003": "505576951.99",       # 营业收入（与 N002 等值）
        "N024": "-129298377.65",      # 净利润
        "N025": "-128649818.60",      # 归母净利润
        "N026": "-648559.05",         # 少数股东损益（N024 = N025 + N026）
        "Tdate": "2026-06-30",
    }
    return Table(cols=cols, rows=[_row_of(cols, **vals)], cmd="CWServ.ph_agf10_cw_lyb")


def xjllb_table():
    """出处: F10与个股资料.md §三 cw_xjllb 样本首行（23 期 × 64 列，Tdate 在末尾）。"""
    cols = ["N%03d" % i for i in range(1, 64)] + ["Tdate"]
    vals = {
        "N001": "2026中报",
        "N002": "336938728.68",
        "N005": "364261091.43",       # 经营活动现金流入小计
        "N010": "405827161.47",       # 经营活动现金流出小计
        "N011": "-41566070.04",       # 经营活动现金流量净额 ✅ 文档确认
        "N017": "-1200000.00",
        "N022": "800000.00",
        "N023": "-2000000.00",
        "N037": "5000000.00",
        "N035": "3000000.00",
        "N036": "2000000.00",
        "Tdate": "2026-06-30",
    }
    return Table(cols=cols, rows=[_row_of(cols, **vals)], cmd="CWServ.ph_agf10_cw_xjllb")


def zyzb_table():
    """出处: F10与个股资料.md §三 cw_zyzb（11 期 × 67 列，编号跳段）。"""
    cols = mapping._TEMPLATE_COLS["zyzb"]
    vals = {"N001": "2026中报", "N002": "-0.1300", "N003": "-0.1300",
            "N004": "-0.1263", "N005": "7.3100", "Tdate": "2026-06-30"}
    return Table(cols=cols, rows=[_row_of(cols, **vals)], cmd="CWServ.ph_agf10_cw_zyzb")


def empty_value_fin_table():
    """服务端给 ""/"—" 的位（不是 0）。"""
    cols = _fin_cols()
    vals = {"N001": "2026三季报", "N038": "", "N067": "—",
            "N077": "--", "N078": "-", "Tdate": "/", "N015": "0"}
    return Table(cols=cols, rows=[_row_of(cols, **vals)], cmd="CWServ.ph_agf10_cw_zcfzb")


# ══════════════════════════════════════════════════════════════════════════
# 1. apply()：标准字段齐全 + raw 完整 + 空值填 None
# ══════════════════════════════════════════════════════════════════════════
def test_apply_fin_standard_fields():
    t = mapping.apply(fin_table(), "fin")
    assert t.cols == ["report_period", "report_date", "monetary_fund_yuan",
                      "total_current_assets_yuan",
                      "total_noncurrent_assets_yuan", "total_assets_yuan",
                      "total_liabilities_yuan", "total_equity_yuan",
                      "equity_parent_yuan", "minority_interest_yuan"], t.cols
    r = t.rows[0]
    assert r["report_period"] == "2026中报"
    assert r["report_date"] == "2026-06-30"
    assert r["total_assets_yuan"] == 3104851614.01
    assert r["total_liabilities_yuan"] == 2203108875.58
    assert r["total_equity_yuan"] == 901742738.43
    assert r["equity_parent_yuan"] == 878407168.86
    assert r["minority_interest_yuan"] == 23335569.57
    assert r["total_current_assets_yuan"] == 1283414820.87
    assert r["monetary_fund_yuan"] == 58154644.44
    # 会计恒等式必须成立（恒等式就是这几列被映射的**理由**）
    assert abs(r["total_assets_yuan"]
               - (r["total_liabilities_yuan"] + r["total_equity_yuan"])) < 0.01
    assert abs(r["total_equity_yuan"]
               - (r["equity_parent_yuan"] + r["minority_interest_yuan"])) < 0.01
    assert abs(r["total_assets_yuan"]
               - (r["total_current_assets_yuan"]
                  + r["total_noncurrent_assets_yuan"])) < 0.01


def test_apply_fin_raw_keeps_every_column():
    """红线 4：原始列一个都不许丢，且 raw 里是**服务端原值**（不清洗）。"""
    src = fin_table()
    t = mapping.apply(src, "fin")
    raw = t.rows[0]["raw"]
    assert len(raw) == 81, len(raw)
    for c in src.cols:
        assert c in raw, c
    assert raw["N038"] == "3104851614.01"      # 原始字符串，不是 float
    assert raw["N044"] is None                  # 该行这一位服务端就是 null
    # 没映射的列只存在于 raw 里（标准字段里不许出现 N0xx）
    assert "N044" not in t.cols
    assert not [c for c in t.cols if c.startswith("N0")], t.cols


def test_apply_null_handling():
    """`""`/`"—"`/`"--"`/`"-"`/`"/"` → None（**不是 0.0**）；"0" 仍是 0.0。"""
    t = mapping.apply(empty_value_fin_table(), "fin")
    r = t.rows[0]
    assert r["total_assets_yuan"] is None       # ""
    assert r["total_liabilities_yuan"] is None  # "—"
    assert r["total_equity_yuan"] is None       # "--"
    assert r["equity_parent_yuan"] is None      # "-"
    assert r["report_date"] is None             # "/"
    assert r["total_current_assets_yuan"] == 0.0   # "0" 与「没有」必须能区分
    assert r["raw"]["N038"] == ""               # raw 保留原值，不清洗


def test_apply_lyb_xjllb():
    lyb = mapping.apply(lyb_table(), "lyb").rows[0]
    assert lyb["total_revenue_yuan"] == 505576951.99
    assert lyb["revenue_yuan"] == 505576951.99
    assert lyb["net_profit_yuan"] == -129298377.65
    assert abs(lyb["net_profit_yuan"]
               - (lyb["net_profit_parent_yuan"] + lyb["minority_profit_yuan"])) < 0.01
    assert lyb["report_date"] == "2026-06-30"
    assert len(lyb["raw"]) == 38

    xj = mapping.apply(xjllb_table(), "xjllb").rows[0]
    assert xj["operating_net_cash_yuan"] == -41566070.04      # 文档明确确认的那一列
    assert abs(xj["operating_cash_inflow_yuan"]
               - (xj["operating_cash_outflow_yuan"] + xj["operating_net_cash_yuan"])) < 0.01
    assert abs(xj["investing_cash_inflow_yuan"]
               - (xj["investing_cash_outflow_yuan"] + xj["investing_net_cash_yuan"])) < 0.01
    assert abs(xj["financing_cash_inflow_yuan"]
               - (xj["financing_cash_outflow_yuan"] + xj["financing_net_cash_yuan"])) < 0.01
    assert xj["report_date"] == "2026-06-30"
    assert len(xj["raw"]) == 64


def test_apply_zyzb():
    t = mapping.apply(zyzb_table(), "zyzb")
    assert t.cols == ["report_period", "report_date", "eps_yuan", "bps_yuan"], t.cols
    r = t.rows[0]
    assert r["eps_yuan"] == -0.13
    assert r["report_date"] == "2026-06-30"
    assert len(r["raw"]) == 67


def test_apply_trusts_table_cols_and_keeps_union_meta():
    """协议层已保证 `Table.cols` 与行值对齐（并集在 meta）→ apply 直接信 cols。

    同时验证「顺手交叉校验」：运行时主表列 == 文档清单 → `template_check="match"`。
    """
    src = fin_table(with_union_provenance=True)
    assert len(src.cols) == len(src.rows[0]) == 81        # 新契约：列名与行值等长
    t = mapping.apply(src, "fin")
    assert t.provenance.extra["cols_align"] == "server", t.provenance
    assert t.provenance.extra["template_check"] == "match", t.provenance
    assert "template_mismatch" not in t.provenance.extra
    # 并集信息一点没丢（协议层放在 meta 里，apply 原样带出去）
    assert t.provenance.extra["cols_union"][0] == "isjr"
    assert "unused_cols" not in t.provenance.extra
    r = t.rows[0]
    assert r["report_period"] == "2026中报"
    assert r["report_date"] == "2026-06-30"
    assert r["total_assets_yuan"] == 3104851614.01
    assert len(r["raw"]) == 81


def test_apply_reports_template_mismatch():
    """文档清单与运行时列清单不一致 → 记进 meta，且**有守卫的产品一律不套映射**。"""
    cols = _fin_cols()
    swapped = list(cols)
    swapped[1], swapped[2] = swapped[2], swapped[1]      # 把 N002/N003 换个位置
    src = Table(cols=swapped, rows=[[None] * 81], cmd="CWServ.ph_agf10_cw_zcfzb")
    t = mapping.apply(src, "fin")
    assert t.provenance.extra["template_check"] == "mismatch", t.provenance
    mm = t.provenance.extra["template_mismatch"]
    assert mm["docs_cols"][:3] == ["N001", "N002", "N003"]
    assert mm["runtime_cols"][:3] == ["N001", "N003", "N002"]
    # fin 在 COL_GUARD 里 → 列清单不一致就不许套映射（宁可只给 raw，不许错映射）
    assert t.provenance.extra["mapping_skipped"] == "cols-guard", t.provenance
    assert t.cols == [], t.cols                     # 一列都没映射 → 0 个数据列
    assert len(t.rows[0]["raw"]) == 81


def test_apply_mismatch_without_guard_still_maps_by_name():
    """没进 COL_GUARD 的产品：列序变了也**按列名**映射，只把差异记进 meta。

    2026-10-27 换品：原用例拿 `hypm` 当「无守卫」样本，但抓包语料显示 hypm 有 5 套真实
    列形状，已给它加了 `COL_GUARD` —— 前提失效。改用同样「有模板、无守卫、≥2 个映射列」
    的 `zgyq`（zg_yq 舆情表，真实只有 1 套形状，不需要守卫），用例意图原样保留。
    """
    # 顺序与模板 [N002…N011, T001] 不同：把 N002/N003 对调
    cols = ["N003", "N002"] + ["N%03d" % i for i in range(4, 12)] + ["T001"]
    assert "zgyq" not in mapping.COL_GUARD
    src = Table(cols=cols,
                rows=[["20260105", "长安汽车概念", "", "因为…", "详细…", "其它研究",
                       None, None, None, None, 2979]],
                cmd="CWServ.ph_agf10_zg_yq")
    t = mapping.apply(src, "zgyq")
    assert t.provenance.extra["template_check"] == "mismatch", t.provenance
    assert "mapping_skipped" not in t.provenance.extra
    assert t.rows[0]["include_date"] == "20260105"      # N003（已换到第 1 位）
    assert t.rows[0]["concept_name"] == "长安汽车概念"   # N002（已换到第 2 位）
    assert t.rows[0]["rank"] == 2979                     # T001


def test_apply_does_not_mutate_input():
    src = fin_table()
    before_cols = list(src.cols)
    before_row = dict(src.rows[0])
    mapping.apply(src, "fin")
    assert src.cols == before_cols
    assert dict(src.rows[0]) == before_row


def test_apply_unknown_product_is_raw_only():
    """没映射的产品：标准字段为空，raw 里必须有全部原始列。"""
    src = Table(cols=["N001", "N002"], rows=[["a", "b"]], cmd="X")
    t = mapping.apply(src, "不存在的产品")
    assert t.cols == []
    assert t.rows[0]["raw"] == {"N001": "a", "N002": "b"}


def test_apply_malformed_table_raises():
    """防御分支：列名与行值长度对不上（畸形回包）→ NotSupportedError，**不许猜列名**。

    正常路径下 `Table.__post_init__` 会把行转成 `Row`（键 = cols），
    `len(cols) == len(row)` 恒成立；只有「行是残缺 dict」这种畸形构造才会触发。
    """
    src = Table(cols=["a"] + ["N%03d" % i for i in range(1, 8)],
                rows=[{"b": "x"}], cmd="CWServ.ph_agf10_cw_cwfxxq")
    assert len(src.cols) == 8 and len(src.rows[0]) == 1
    try:
        mapping.apply(src, "cwfxxq")
    except errors.NotSupportedError as e:
        assert "cwfxxq" in str(e)
    else:
        raise AssertionError("应当抛 NotSupportedError")


def test_col_guard_blocks_industry_variant():
    """银行股的利润表是另一套模板（25×57）→ 一列都不许映射，只能给 raw。"""
    bank_cols = ["N%03d" % i for i in range(1, 58)]      # 57 列（实测银行口径）
    src = Table(cols=bank_cols, rows=[[None] * 57], cmd="CWServ.ph_agf10_cw_lyb")
    t = mapping.apply(src, "lyb")
    assert t.cols == [], t.cols
    assert t.provenance.extra["mapping_skipped"] == "cols-guard", t.provenance
    assert t.provenance.extra["template_check"] == "mismatch"
    assert len(t.rows[0]["raw"]) == 57


def test_cwfxxq_maps_only_when_rs3_shape():
    """cwfxxq 主表由运行时决定：只有真的是 7 列（RS3 变动说明）才套映射。"""
    row = ["2026/06/30", "1", "应收票据", "72483587.75",
           "133476412.79", "-45.70", "期末应收银行承兑汇票较期初减少影响"]
    t7 = Table(cols=["N%03d" % i for i in range(1, 8)], rows=[row],
               cmd="CWServ.ph_agf10_cw_cwfxxq")
    out = mapping.apply(t7, "cwfxxq")
    assert out.provenance.extra["template_check"] == "match", out.provenance
    r = out.rows[0]
    assert r["report_date"] == "2026/06/30"
    assert r["item_seq"] == 1 and isinstance(r["item_seq"], int)
    assert r["item_name"] == "应收票据"
    assert r["end_amount_yuan"] == 72483587.75
    assert r["begin_amount_yuan"] == 133476412.79
    assert r["change_pct"] == -45.7
    assert r["change_reason"].startswith("期末应收银行承兑汇票")
    # 变动率恒等式（文档逐行验算过的那条）
    calc = (r["end_amount_yuan"] - r["begin_amount_yuan"]) / r["begin_amount_yuan"] * 100
    assert abs(calc - r["change_pct"]) < 0.01

    # 若是 29 列那张（RS1 指标矩阵）→ 列义完全不同，映射必须自动不套
    col29 = ["date"] + ["T%03d" % i for i in range(1, 29)]
    t29 = Table(cols=col29, rows=[["2026-06-30"] + [None] * 28],
                cmd="CWServ.ph_agf10_cw_cwfxxq")
    out29 = mapping.apply(t29, "cwfxxq")
    assert out29.cols == [], out29.cols
    assert out29.provenance.extra["mapping_skipped"] == "cols-guard", out29.provenance


# ══════════════════════════════════════════════════════════════════════════
# 2. FIELD_MAP 自身的门禁
# ══════════════════════════════════════════════════════════════════════════
def test_field_map_shape():
    assert isinstance(mapping.FIELD_MAP, dict)
    for product, m in mapping.FIELD_MAP.items():
        assert isinstance(m, dict), product
        for col, std in m.items():
            assert isinstance(col, str) and col, product
            assert isinstance(std, str) and std, (product, col)
            assert std == std.strip() and " " not in std, (product, col, std)
            # 标准字段不许是 N0xx / 拼音原样（那等于没映射）
            assert not std.startswith("N0"), (product, col, std)
    assert "raw" not in {s for m in mapping.FIELD_MAP.values() for s in m.values()}


def test_field_map_core_products_present():
    need = {"fin", "lyb", "xjllb", "zyzb", "cwbg", "tjcj", "zysy", "zyzbxq"}
    assert need <= set(mapping.FIELD_MAP), need - set(mapping.FIELD_MAP)


def test_kinds_complete():
    want = {"zcfz", "lyb", "xjllb", "zyzb", "cwbg", "cwfxxq", "tjcj", "zysy", "zyzbxq"}
    assert set(finance.KINDS) == want, set(finance.KINDS) ^ want
    # 契约：每项 = (产品名, 子码)
    for kind, val in finance.KINDS.items():
        assert isinstance(val, tuple) and len(val) == 2, (kind, val)
        assert isinstance(val[0], str)
        assert val[1] is None or isinstance(val[1], str)


def test_zcfz_is_balance_sheet_not_snapshot():
    """`zcfz` 必须是资产负债表本体 ph_agf10_cw_zcfzb，不是 1×9 的 zcfz 快照。"""
    product, _sub = finance.KINDS["zcfz"]
    cmd, _ctl, _tpl = finance._PARAMS[product]
    assert cmd == "CWServ.ph_agf10_cw_zcfzb", cmd
    assert cmd != "CWServ.ph_agf10_cw_zcfz"
    assert len(mapping._TEMPLATE_COLS["fin"]) == 81


# ══════════════════════════════════════════════════════════════════════════
# 3. get_finance()：参数构造 / 异常分支（假 client，零网络）
# ══════════════════════════════════════════════════════════════════════════
class FakeClient:
    """只实现 `call()` —— 正是本模块需要的全部接口。"""

    def __init__(self, table=None, exc=None):
        self.calls = []
        self._table = table
        self._exc = exc

    def call(self, cmd, ctl, params=None, **kw):
        self.calls.append((cmd, ctl, params))
        if self._exc is not None:
            raise self._exc
        return self._table


EXPECTED = {
    "zcfz":   ("CWServ.ph_agf10_cw_zcfzb",     0x1A, ["600303"]),
    "lyb":    ("CWServ.ph_agf10_cw_lyb",       0x18, ["00101", "600303"]),
    "xjllb":  ("CWServ.ph_agf10_cw_xjllb",     0x1A, ["00101", "600303"]),
    "zyzb":   ("CWServ.ph_agf10_cw_zyzb",      0x19, ["600303"]),
    "cwbg":   ("CWServ.ph_agf10_cw_cwbg",      0x19, ["00101", "600303", "0"]),
    "cwfxxq": ("CWServ.ph_agf10_cw_cwfxxq",    0x1B, ["600303"]),
    "tjcj":   ("CWServ.ph_agf10_cw_tjcj",      0x19, ["600303"]),
    "zysy":   ("CWServ.ph_agf10_cw_sy_v2",     0x1A, ["600303"]),
    "zyzbxq": ("CWServ.ph_agf10_cw_zyzbxqy_sy", 0x1F, ["600303"]),
}


def test_get_finance_params_for_every_kind():
    """目标代码位必须已被替换、子码位必须按 kind 填对。"""
    fakes = {}
    for kind, (cmd, ctl, params) in EXPECTED.items():
        f = FakeClient(table=Table(cols=["N001"], rows=[["2026中报"]], cmd=cmd))
        fakes[kind] = f
        got = finance.get_finance(f, "600303", kind=kind)
        assert f.calls == [(cmd, ctl, params)], (kind, f.calls)
        # 占位符必须全部替换掉，且模板里的样例代码不许残留
        flat = str(params)
        assert "@" not in flat, (kind, flat)
        for leftover in ("600519", "920091", "92009"):
            assert leftover not in flat, (kind, flat)
        assert "600303" in flat, (kind, flat)
        assert isinstance(got, Table)


def test_get_finance_accepts_prefixed_symbol():
    f = FakeClient(table=fin_table())
    finance.get_finance(f, "sh600303", kind="zcfz")
    assert f.calls[0][2] == ["600303"]
    f2 = FakeClient(table=fin_table())
    finance.get_finance(f2, (1, "600303"), kind="zcfz")
    assert f2.calls[0][2] == ["600303"]


def test_get_finance_default_kind_is_zcfz():
    f = FakeClient(table=fin_table())
    finance.get_finance(f, "600303")
    assert f.calls[0][0] == "CWServ.ph_agf10_cw_zcfzb"


def test_get_finance_bad_kind_lists_all_values():
    f = FakeClient(table=fin_table())
    try:
        finance.get_finance(f, "600303", kind="zcfzb")   # 常见写错
    except ValueError as e:
        msg = str(e)
        for k in finance.KINDS:
            assert k in msg, (k, msg)
        assert "zcfzb" in msg
    else:
        raise AssertionError("应当抛 ValueError")
    assert f.calls == [], "参数不认识时不许发请求"


def test_get_finance_propagates_empty_result():
    """当期无数据 → EmptyResult 原样上抛，**不许换成一个空 Table**。"""
    f = FakeClient(exc=errors.EmptyResult("CWServ.ph_agf10_cw_zcfzb 无数据（有列无行）"))
    try:
        finance.get_finance(f, "600303", kind="zcfz")
    except errors.EmptyResult:
        pass
    else:
        raise AssertionError("应当抛 EmptyResult")


def test_get_finance_propagates_not_supported():
    f = FakeClient(exc=errors.NotSupportedError("通道不通"))
    try:
        finance.get_finance(f, "600303", kind="lyb")
    except errors.NotSupportedError:
        pass
    else:
        raise AssertionError("应当抛 NotSupportedError")


def test_get_finance_bad_symbol():
    f = FakeClient(table=fin_table())
    try:
        finance.get_finance(f, "not-a-code", kind="zcfz")
    except ValueError:
        pass
    else:
        raise AssertionError("非法代码应当抛 ValueError")
    assert f.calls == []


def test_client_wiring_signature_matches():
    """`Client.get_finance/get_f10/get_xdxr` 的接线（Lead 写在 client.py 里）。

    ⚠️ 这里**只做签名/分派层面的检查**，不构造 `Client`（门禁 7 要求
    `tests/test_*.py` 里不出现 `Client(` / `Session(`）；
    真正的端到端接线由联网冒烟在真实环境验证。
    """
    import inspect

    from tdxdata import client as CL

    for name, args in (("get_finance", ("symbol", "kind")),
                       ("get_f10", ("symbol", "section")),
                       ("get_xdxr", ("symbol",))):
        fn = getattr(CL.Client, name, None)
        assert fn is not None, name
        got = list(inspect.signature(fn).parameters)[1:]     # 去掉 self
        assert tuple(got) == args, (name, got)

    # sections 三件套的第一个参数都是 client，且只依赖 client.call()
    from tdxdata.sections import finance as F, f10 as FE, xdxr as X
    for mod, fn_name in ((F, "get_finance"), (FE, "get_f10"), (X, "get_xdxr")):
        got = list(inspect.signature(getattr(mod, fn_name)).parameters)
        assert got[0] == "client", (fn_name, got)


# ══════════════════════════════════════════════════════════════════════════
# 5. 实网样本回归（2026-10-25 抓的，`Client(gap=4.4)` 单线程真跑）
# ══════════════════════════════════════════════════════════════════════════
# 下面的数字**不是编的**：全部来自 600519（贵州茅台）2026 中报的真回包，
# 列名 + 前 2 行值原样固化，任何映射改动都会在这里露馅。

REAL_FIN_600519 = {
    "cmd": 'CWServ.ph_agf10_cw_zcfzb',
    "n_rows": 23,          # 服务端实际返回的行数（这里只固化前 2 行）
    "cols": [
        'N001', 'N002', 'N003', 'N004', 'N005', 'N006', 'N007', 'N008', 'N009', 'N010', 'N011', 'N012',
        'N013', 'N014', 'N015', 'N016', 'N017', 'N018', 'N019', 'N020', 'N021', 'N022', 'N023', 'N024',
        'N025', 'N026', 'N027', 'N028', 'N029', 'N030', 'N031', 'N032', 'N033', 'N034', 'N035', 'N036',
        'N037', 'N038', 'N039', 'N040', 'N041', 'N042', 'N043', 'N044', 'N045', 'N046', 'N047', 'N048',
        'N049', 'N050', 'N051', 'N052', 'N053', 'N054', 'N055', 'N056', 'N057', 'N058', 'N059', 'N060',
        'N061', 'N062', 'N063', 'N064', 'N065', 'N066', 'N067', 'N068', 'N069', 'N070', 'N071', 'N072',
        'N073', 'N074', 'N075', 'N076', 'N077', 'N078', 'N079', 'N080', 'Tdate',
    ],
    "rows": [
        [
            '2026中报', '53518798979.08', None, '570895.04', None, '570895.04', '16190168.54', '47328677.41',
            None, None, '61317208371.30', '1798106445.56', None, '60066044.49', '260724668103.40', None,
            '1106707304.24', '2414885261.51', None, '3917510576.37', None, None, '147198786.54',
            '2716218.30', '22220890242.73', None, '2481191369.98', None, None, None, '8578743700.95',
            '137537251.51', None, '127852143.45', '6436890174.31', '115827807.77', '48326116465.91',
            '309050784569.31', None, None, None, None, '3509015535.31', None, '3509015535.31', None,
            '375947883.69', '6923321600.48', None, None, '6791660124.62', '56855365.72', None, None,
            '3177561597.07', '384394900.26', '46645073675.32', None, None, None, None, None, '71049838.47',
            '51479378.82', None, '309358719.63', '46954432394.95', '1250081601.00', '1577095.18',
            '49251429608.71', None, '5759574.41', None, None, None, '199683216816.20', '262096352174.36',
            '251253594419.50', '10842757754.86', '309050784569.31', '2026-06-30',
        ],
        [
            '2026一季报', '48786691397.55', None, '32344588.37', None, '32344588.37', '28053418.89',
            '60639127.61', None, None, '60692316104.51', '1800000000.00', None, '192742681.88',
            '271524528742.40', None, '1095970964.33', '2394027980.93', None, '4102064586.02', None, None,
            '147269141.83', '3146663.09', '22132684069.80', None, '2850892024.69', None, None, None,
            '8611214393.29', '133677051.16', None, '130368142.47', '5687250277.39', '144657424.59',
            '48394316163.18', '319918844905.58', None, None, None, None, '3671613767.68', None,
            '3671613767.68', None, '720262889.86', '7299360362.95', None, None, '6589812851.18',
            '53309216.00', None, None, '3027195224.08', '376773821.81', '38455594988.88', None, None, None,
            None, None, '79224591.60', '59026548.66', None, '327363481.01', '38782958469.89',
            '1252270215.00', '1577095.18', '50543176610.94', '1112188987.16', '9299.65', None, None, None,
            '219147661718.66', '281135886435.69', '270894035676.27', '10241850759.42', '319918844905.58',
            '2026-03-31',
        ],
    ],
    "cols_union": [
        'isjr', 'N001', 'N002', 'N003', 'N004', 'N005', 'N006', 'N007', 'N008', 'N009', 'N010', 'N011',
        'N012', 'N013', 'N014', 'N015', 'N016', 'N017', 'N018', 'N019', 'N020', 'N021', 'N022', 'N023',
        'N024', 'N025', 'N026', 'N027', 'N028', 'N029', 'N030', 'N031', 'N032', 'N033', 'N034', 'N035',
        'N036', 'N037', 'N038', 'N039', 'N040', 'N041', 'N042', 'N043', 'N044', 'N045', 'N046', 'N047',
        'N048', 'N049', 'N050', 'N051', 'N052', 'N053', 'N054', 'N055', 'N056', 'N057', 'N058', 'N059',
        'N060', 'N061', 'N062', 'N063', 'N064', 'N065', 'N066', 'N067', 'N068', 'N069', 'N070', 'N071',
        'N072', 'N073', 'N074', 'N075', 'N076', 'N077', 'N078', 'N079', 'N080', 'Tdate',
    ],
}
"""出处: 实网 600519 · cw_zcfzb 23 期 × 81 列（并集 82，多一个 isjr）"""
def real_fin_table():
    """原始 `Table`（按新契约：cols = 主表列，并集在 meta）。"""
    src = REAL_FIN_600519
    meta = {"cols_source": "primary"}
    if src.get("cols_union"):
        meta["cols_union"] = list(src["cols_union"])
    return Table(cols=list(src["cols"]), rows=[list(r) for r in src["rows"]],
                 cmd='CWServ.ph_agf10_cw_zcfzb', provenance=_prov_of(meta))

REAL_LYB_600519 = {
    "cmd": 'CWServ.ph_agf10_cw_lyb',
    "n_rows": 24,          # 服务端实际返回的行数（这里只固化前 2 行）
    "cols": [
        'N001', 'N002', 'N003', 'N004', 'N005', 'N006', 'N007', 'N008', 'N009', 'N010', 'N011', 'N012',
        'N013', 'N014', 'N015', 'N016', 'N017', 'N018', 'N019', 'N020', 'N021', 'N022', 'N023', 'N024',
        'N025', 'N026', 'N027', 'N028', 'N029', 'N030', 'N031', 'N032', 'N033', 'N034', 'Tdate',
        'N035', 'N036', 'N037',
    ],
    "rows": [
        [
            '2026中报', '92278072083.21', '90703260964.48', '30946044878.08', '9473762565.88',
            '14682159453.47', '3206308341.53', '3635355263.82', '114926219.60', '-243237716.96', None,
            None, None, '22082651.62', '1013870.58', '16912.87', '139731.04', '23554465.99',
            '61411291686.27', '35315234.56', '8187743.54', '61438419177.29', '15405088610.51',
            '46033330566.78', '44516880421.86', '1516450144.92', '46033330566.78', None, '35.570000',
            '35.570000', '27974688.63', '46061305255.41', '44528418839.89', '1532886415.52', '2026-06-30',
            '44464207646.01', None, '32473761.91',
        ],
        [
            '2026一季报', '54702912385.23', '53909252220.51', '17192223524.71', '5520729200.32',
            '8227407160.96', '1605764764.88', '1853763865.81', '59310724.46', '-115803334.98', None, None,
            None, '-3077007.20', '434629.49', '87268.16', '130409.49', '2970996.16', '37537008686.42',
            '6759734.60', '518777.00', '37543249644.02', '9389418154.13', '28153831489.89',
            '27242512886.45', '911318603.44', '28153831489.89', None, '21.760000', '21.760000',
            '14877422.36', '28168708912.25', '27248301029.72', '920407882.53', '2026-03-31',
            '27239985194.41', None, '25860797.96',
        ],
    ],
    "cols_union": [
        'isjr', 'N001', 'N002', 'N003', 'N004', 'N005', 'N006', 'N007', 'N008', 'N009', 'N010', 'N011',
        'N012', 'N013', 'N014', 'N015', 'N016', 'N017', 'N018', 'N019', 'N020', 'N021', 'N022', 'N023',
        'N024', 'N025', 'N026', 'N027', 'N028', 'N029', 'N030', 'N031', 'N032', 'N033', 'N034',
        'Tdate', 'N035', 'N036', 'N037',
    ],
}
"""出处: 实网 600519 · cw_lyb 24 期 × 38 列（并集 39）"""
def real_lyb_table():
    """原始 `Table`（按新契约：cols = 主表列，并集在 meta）。"""
    src = REAL_LYB_600519
    meta = {"cols_source": "primary"}
    if src.get("cols_union"):
        meta["cols_union"] = list(src["cols_union"])
    return Table(cols=list(src["cols"]), rows=[list(r) for r in src["rows"]],
                 cmd='CWServ.ph_agf10_cw_lyb', provenance=_prov_of(meta))

REAL_ZYBZ_600519 = {
    "cmd": 'CWServ.ph_agf10_cw_zyzb',
    "n_rows": 24,          # 服务端实际返回的行数（这里只固化前 2 行）
    "cols": [
        'N001', 'N002', 'N003', 'N004', 'N005', 'N006', 'N007', 'N008', 'N009', 'N010', 'N011', 'N012',
        'N013', 'N014', 'N015', 'N101', 'N102', 'N103', 'N104', 'N105', 'N106', 'N107', 'N108', 'N109',
        'N110', 'N111', 'N112', 'N113', 'N114', 'N115', 'N201', 'N202', 'N203', 'N204', 'N205', 'N206',
        'N207', 'N208', 'N301', 'N302', 'N303', 'N304', 'N305', 'N306', 'N307', 'N308', 'N401', 'N402',
        'N403', 'N404', 'N405', 'N406', 'N407', 'N408', 'N409', 'N410', 'N411', 'N412', 'N413', 'N414',
        'N501', 'N502', 'N503', 'N020', 'N021', 'Tdate', 'N022',
    ],
    "rows": [
        [
            '2026中报', '35.5700', '35.5700', '35.6112', '200.9898', '73.8176', '72.5579', '48.9511',
            '49.9170', '0.0013', '39.3986', '159.7361', '56.5489', '199.1347', '46.7053', '17.72',
            '16.7500', '17.7179', '17.6969', '16.7400', '17.72', '15.0218', '15.02', '89.56', '67.71',
            '50.75', '10.4448', '7.4014', '48.24', '66.5503', '15.19', '1.18', '84.3631', '15.6369',
            '76.3540', '99.34', '0.6588', '0.0014', '1166.0675', '1166.06', '0.00', '0.15', '57047.09',
            '0.36', '4.13', '0.30', '-1.6860', '-1.6860', '1.3001', '1.4699', '-2.1630', '-2.1338',
            '-1.9516', '-2.0402', '438.8400', '315.5653', '-6.8713', '5.2827', '8.8869', '5.7460',
            '92278072083.21', '44516880421.86', '44464207646.01', '309050784569.31', '262096352174.36',
            '2026-06-30', '未经审计',
        ],
        [
            '2026一季报', '21.7600', '21.7600', '21.7545', '216.3223', '43.6830', '43.0492', '29.8877', None,
            '0.0013', '40.3612', '175.0003', '21.4889', '215.3615', '41.2314', '10.06', '10.5700',
            '10.0565', '10.0556', None, '10.06', '9.0272', '9.03', '89.76', '69.63', '52.22', '10.2408',
            '6.3125', '49.80', '68.6198', '12.12', '1.14', '84.8729', '15.1271', '80.1239', '99.16',
            '0.8441', '0.0014', '995.4390', '995.41', '0.03', '0.09', '3084.61', '0.21', '2.45', '0.18',
            '1.7774', '1.7774', '6.3360', '6.5380', '1.3511', '1.3821', '1.4714', '1.4529', '205.4750',
            '683.5228', '-3.2244', '4.8523', '-12.2128', '2.4171', '54702912385.23', '27242512886.45',
            '27239985194.41', '319918844905.58', '281135886435.69', '2026-03-31', '未经审计',
        ],
    ],
    "cols_union": [
        'isjr', 'N001', 'N002', 'N003', 'N004', 'N005', 'N006', 'N007', 'N008', 'N009', 'N010', 'N011',
        'N012', 'N013', 'N014', 'N015', 'N101', 'N102', 'N103', 'N104', 'N105', 'N106', 'N107', 'N108',
        'N109', 'N110', 'N111', 'N112', 'N113', 'N114', 'N115', 'N201', 'N202', 'N203', 'N204', 'N205',
        'N206', 'N207', 'N208', 'N301', 'N302', 'N303', 'N304', 'N305', 'N306', 'N307', 'N308', 'N401',
        'N402', 'N403', 'N404', 'N405', 'N406', 'N407', 'N408', 'N409', 'N410', 'N411', 'N412', 'N413',
        'N414', 'N501', 'N502', 'N503', 'N020', 'N021', 'Tdate', 'N022',
    ],
}
"""出处: 实网 600519 · cw_zyzb 24 期 × 67 列"""
def real_zyzb_table():
    """原始 `Table`（按新契约：cols = 主表列，并集在 meta）。"""
    src = REAL_ZYBZ_600519
    meta = {"cols_source": "primary"}
    if src.get("cols_union"):
        meta["cols_union"] = list(src["cols_union"])
    return Table(cols=list(src["cols"]), rows=[list(r) for r in src["rows"]],
                 cmd='CWServ.ph_agf10_cw_zyzb', provenance=_prov_of(meta))

REAL_ZYBZXQ_600519 = {
    "cmd": 'CWServ.ph_agf10_cw_zyzbxqy_sy',
    "n_rows": 24,          # 服务端实际返回的行数（这里只固化前 2 行）
    "cols": [
        'N001', 'T056', 'T059', 'T061', 'T060', 'T062', 'T064', 'T063', 'T065', 'T066', 'T067', 'JGID',
        'Tdate',
    ],
    "rows": [
        [
            '2026中报', '35.5700', '200.9898', '0.0013', '159.7361', '56.5489', '17.72', '15.02', '89.56',
            '67.71', '50.75', 'gssh0600519', '2026-06-30',
        ],
        [
            '2026一季报', '21.7600', '216.3223', '0.0013', '175.0003', '21.4889', '10.06', '9.03', '89.76',
            '69.63', '52.22', 'gssh0600519', '2026-03-31',
        ],
    ],
}
"""出处: 实网 600519 · cw_zyzbxqy_sy 24 期 × 13 列"""
def real_zyzbxq_table():
    """原始 `Table`（按新契约：cols = 主表列，并集在 meta）。"""
    src = REAL_ZYBZXQ_600519
    meta = {"cols_source": "primary"}
    if src.get("cols_union"):
        meta["cols_union"] = list(src["cols_union"])
    return Table(cols=list(src["cols"]), rows=[list(r) for r in src["rows"]],
                 cmd='CWServ.ph_agf10_cw_zyzbxqy_sy', provenance=_prov_of(meta))

REAL_CWFXXQ_600519 = {
    "cmd": 'CWServ.ph_agf10_cw_cwfxxq',
    "n_rows": 105,          # 服务端实际返回的行数（这里只固化前 2 行）
    "cols": [
        'N001', 'N002', 'N003', 'N004', 'N005', 'N006', 'N007',
    ],
    "rows": [
        [
            '2026/06/30', '1', '货币资金', '53518798979.08', '51690610946.50', '3.54', None,
        ],
        [
            '2026/06/30', '2', '拆出资金', '141084158124.01', '99096188059.75', '42.37',
            '公司控股子公司贵州茅台集团财务有限公司存放同业增加',
        ],
    ],
}
"""出处: 实网 600519 · cw_cwfxxq 主表 = **105 行 × 7 列**（RS3 变动说明）"""
def real_cwfxxq_table():
    """原始 `Table`（按新契约：cols = 主表列，并集在 meta）。"""
    src = REAL_CWFXXQ_600519
    meta = {"cols_source": "primary"}
    if src.get("cols_union"):
        meta["cols_union"] = list(src["cols_union"])
    return Table(cols=list(src["cols"]), rows=[list(r) for r in src["rows"]],
                 cmd='CWServ.ph_agf10_cw_cwfxxq', provenance=_prov_of(meta))


# ── 实网断言 ─────────────────────────────────────────────────────────────
def test_real_fin_600519_shapes_and_mapping():
    """实网：23 期 × 81 列，cols 与行值等长；映射与文档清单 match。"""
    src = real_fin_table()
    assert src.shape == (2, 81), src.shape           # 固化只留 2 行
    assert all(len(r) == 81 for r in src.rows)
    assert REAL_FIN_600519["n_rows"] == 23
    t = mapping.apply(src, "fin")
    assert t.provenance.extra["template_check"] == "match", t.provenance
    assert t.provenance.extra.get("mapping_skipped") is None
    r0 = t.rows[0]
    assert r0["report_period"] == "2026中报"
    assert r0["report_date"] == "2026-06-30"
    assert r0["total_assets_yuan"] == 309050784569.31
    assert len(r0["raw"]) == 81


def test_real_fin_600519_identity_holds():
    """实网两期数据都满足 资产总计 = 负债合计 + 所有者权益合计（差额 0）。"""
    t = mapping.apply(real_fin_table(), "fin")
    for r in t.rows:
        a = r["total_assets_yuan"]
        l = r["total_liabilities_yuan"]
        e = r["total_equity_yuan"]
        assert abs(a - (l + e)) < 0.02, (r["report_period"], a, l, e)
        assert abs(a - (r["total_current_assets_yuan"]
                        + r["total_noncurrent_assets_yuan"])) < 0.02
        assert abs(e - (r["equity_parent_yuan"] + r["minority_interest_yuan"])) < 0.02


def test_real_lyb_600519_identity_holds():
    """实网：净利润 = 归母净利润 + 少数股东损益（两期都 0 差额）。"""
    t = mapping.apply(real_lyb_table(), "lyb")
    assert t.provenance.extra["template_check"] == "match", t.provenance
    for r in t.rows:
        n, p, m = (r["net_profit_yuan"], r["net_profit_parent_yuan"],
                   r["minority_profit_yuan"])
        assert abs(n - (p + m)) < 0.02, (r["report_period"], n, p, m)
    assert t.rows[0]["total_revenue_yuan"] == 92278072083.21


def test_real_cwfxxq_is_the_7col_variant_table():
    """实网定论：cwfxxq 主表 = 105×7（RS3 变动说明），映射全部命中。"""
    src = real_cwfxxq_table()
    assert src.shape == (2, 7)
    assert REAL_CWFXXQ_600519["n_rows"] == 105
    t = mapping.apply(src, "cwfxxq")
    assert t.provenance.extra["template_check"] == "match", t.provenance
    assert t.provenance.extra.get("mapping_skipped") is None
    r0, r1 = t.rows
    assert r0["item_name"] == "货币资金" and r0["item_seq"] == 1
    # 空值必须是 None 而不是 "" / 0.0（"货币资金"这一行公司没给变动原因）
    assert r0["change_reason"] is None, r0["change_reason"]
    assert r1["item_name"] == "拆出资金"
    assert r1["change_reason"].startswith("公司控股子公司贵州茅台集团财务有限公司")
    for r in (r0, r1):
        calc = ((r["end_amount_yuan"] - r["begin_amount_yuan"])
                / r["begin_amount_yuan"] * 100)
        assert abs(calc - r["change_pct"]) < 0.02, (calc, r["change_pct"])
    assert r0["report_date"] == "2026/06/30"      # 该表报告期是斜杠格式，原样返回


def test_real_cross_command_money_supply_matches():
    """跨命令对账：cwfxxq 首行「货币资金」期末额 == cw_zcfzb 的 N002（实网逐位相同）。

    这条同时钉死了 `fin.N002 = 货币资金` 这个原来只有「中等置信度」的映射。
    """
    fin = mapping.apply(real_fin_table(), "fin").rows[0]
    cw = mapping.apply(real_cwfxxq_table(), "cwfxxq").rows[0]
    assert cw["item_name"] == "货币资金"
    assert cw["end_amount_yuan"] == fin["monetary_fund_yuan"] == 53518798979.08


def test_real_zyzb_and_zyzbxq_agree_on_eps():
    """实网：cw_zyzb N002 与 cw_zyzbxqy_sy T056 是同一个 EPS（35.57）。"""
    z = mapping.apply(real_zyzb_table(), "zyzb").rows[0]
    x = mapping.apply(real_zyzbxq_table(), "zyzbxq").rows[0]
    assert z["eps_yuan"] == x["eps_yuan"] == 35.57
    assert z["bps_yuan"] == x["bps_yuan"] == 200.9898
    assert x["internal_key"] == "gssh0600519"      # 实测：内部键，不是数字 ID
    assert z["report_date"] == x["report_date"] == "2026-06-30"


# ══════════════════════════════════════════════════════════════════════════
# 6. 跨模块 Table 形状契约（与 C 的 boards/flow 同口径，Lead 裁决）
# ══════════════════════════════════════════════════════════════════════════
def assert_table_contract(t, who=""):
    """统一口径：`cols` **不含** `"raw"`；`raw` 是每行都带的 sidecar。

    这样 `Table.shape` 才是「数据矩阵形状」（10 个标准字段就是 10 列，
    不会因为备份原始列而变成 11 列），而 `t.column("raw")` 照样能取到。
    """
    assert "raw" not in t.cols, (who, t.cols)
    if not t.rows:
        return
    assert "raw" in t.rows[0], (who, list(t.rows[0]))
    assert set(t.cols) <= set(t.rows[0]), (who, set(t.cols) - set(t.rows[0]))
    assert len(t.rows[0]) == len(t.cols) + 1, (who, len(t.rows[0]), len(t.cols))
    for r in t.rows:
        assert isinstance(r, dict), who
        assert len(r) == len(t.cols) + 1, (who, len(r), len(t.cols))
        # 按名 / 按位置等价（Row 的两个口径）
        for i, c in enumerate(t.cols):
            assert r[i] == r[c], (who, i, c)


def _table_of_shape(product):
    """按该产品的「正确列清单」造一张只有 1 行的表（走守卫/模板那套列名）。"""
    cols = (mapping.COL_GUARD.get(product)
            or mapping._TEMPLATE_COLS.get(product)
            or ["N001", "N002"])
    return Table(cols=list(cols), rows=[[None] * len(cols)], cmd=" 假命令 ")


def test_contract_holds_for_every_mapped_product():
    """31 个产品全部过一遍形状契约（含 4 个 raw-only 的）。"""
    for product in mapping.FIELD_MAP:
        assert_table_contract(mapping.apply(_table_of_shape(product), product), product)


def test_contract_holds_through_get_finance():
    """走公开 API：9 个 kind 全过一遍契约。"""
    for kind in finance.KINDS:
        product = finance.KINDS[kind][0]
        t = finance.get_finance(FakeClient(table=_table_of_shape(product)),
                                "600303", kind=kind)
        assert_table_contract(t, kind)


def test_shape_excludes_raw():
    """`shape` 的列数 = 标准字段数（不含 raw）；行里仍带 raw。"""
    t = mapping.apply(fin_table(), "fin")
    assert t.shape == (1, 10), t.shape
    assert len(t.rows[0]) == 11
    assert t.cols[-1] == "minority_interest_yuan"
    assert len(t.column("raw")) == 1


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
