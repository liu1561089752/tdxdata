# -*- coding: utf-8 -*-
"""F10 / 分红语义层（P3）的**离线**单元测试 —— 全程不联网。

fixture 全部照抄 实机验证样本 与 实机验证样本（F10）
里的真实样本（列名 + 首行值）。

跑法：python tests/test_sections_f10.py   或   pytest tests/test_sections_f10.py -q
"""
from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tdxdata import errors                                              # noqa: E402
from datetime import datetime as _dt
from tdxdata._provenance import ClockSource as _CS, Provenance

_T = _dt(2026, 10, 28, 9, 30)


def _prov_of(meta, cmd=""):
    """旧 meta dict 夹具 → Provenance（extra 收结构化旁证）。"""
    return (Provenance(fetched_at=_T, clock=_CS.SERVER, cmd=cmd,
                       extra=dict(meta)) if meta else None)
from tdxdata.models import Table                                        # noqa: E402
from tdxdata.sections import f10, mapping, xdxr                         # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
# fixture：全部来自真实样本
# ══════════════════════════════════════════════════════════════════════════
def _primary_table(cmd, cols, rows, union=None):
    """按**新契约**造表：`cols` 只放主表自己的列名（与行值严格对齐），
    「各 RS 列名并集」放 `provenance.extra["cols_union"]`
    （协议层 `client._make_table` 就是这么给的）。
    """
    prov = None
    if union:
        prov = Provenance(fetched_at=_T, clock=_CS.SERVER, cmd=cmd,
                          extra={"cols_source": "primary",
                                 "cols_union": list(union)})
    return Table(cols=list(cols), rows=rows, cmd=cmd, provenance=prov)


def gsjj_table():
    """出处: F10与个股资料.md §四 gsjj 样本（600303 曙光股份，1 行 × 19 列，p1=00201）。"""
    cols = ["N%03d" % i for i in range(1, 20)]
    row = ["A股", "2000-12-26", "上网定价发行", "定价发行", "1.00", "8.800",
           "40000000.0000", "40000000.0000", "1.820", "4.770", "352000000.0000",
           "338160000.0000", "33.850", "0.2689", "13840000.0000",
           "国泰海通证券股份有限公司",
           "东北证券股份有限公司,国泰海通证券股份有限公司",
           "在上交所开立股票帐户的境内自然人及法人（法律、法规禁止者除外）",
           "新老股东共享1999年1月1日后实现利润"]
    return Table(cols=cols, rows=[row], cmd="CWServ.ph_agf10_gsjj")


def gbgd_table():
    """出处: F10与个股资料.md §七之二 gbgd 样本（600303 → 7 行 × 3 列，取前 2 行）。"""
    return Table(cols=["N001", "N002", "N003"],
                 rows=[["2026-06-30", "37143", "2.49"],
                       ["2026-03-31", "36880", "3.32"]],
                 cmd="CWServ.ph_agf10_gbgd")


def hypm_table():
    """出处: 实测样本总表 #16 + §四 hypm（RS0 7×2 + RS1 31×4）。

    主表是 RS1（4 列：N003/N004/sc/lx）；RS0 计数表的列名在 `meta["cols_union"]` 里。
    """
    return _primary_table(
        "CWServ.ph_agf10_hypm",
        ["N003", "N004", "sc", "lx"],
        [["思进智能", "003025", "0", "0"],
         ["高澜股份", "300499", "0", "0"]],
        union=["N001", "N002", "N003", "N004", "sc", "lx"])


def jyfx_table():
    """**实网样本**（2026-10-25，600519）：p1=00101 的主表是 RS0 = 1 行 × 4 列。

    ⚠️ 早前按「单元格数」推断主表是 RS1（2×2 产品线），实网证明错了 ——
    4 个单元格时协议层按 (cells, rows, cols) 选了行多的 RS0。
    """
    return _primary_table(
        "CWServ.ph_agf10_jyfx",
        ["N001", "N002", "N003", "N004"],
        [["茅台酒及系列酒的生产与销售",
          "茅台酒系列产品的生产与销售；饮料、食品、包装材料的生产、销售；防伪技术开发…",
          "查看详情", "gssh0600519"]],
        union=["N001", "N002", "N003", "N004", "cpid"])


def zg_yq_table():
    """出处: §五 zg_yq RS1 首行（4 行 × 11 列 = N002…N011 + T001）。

    主表是 RS1；RS0 的计数列（N001）只出现在 `meta["cols_union"]` 里。
    """
    row = ["长安汽车概念", "20231127", None,
           "公司服务的客户包括比亚迪、长安集团、吉利汽车……",
           "公司服务的客户包括比亚迪、长安集团……玉柴股份、吉孚传动等。",
           "其它研究", "-3.24", "-9.27", "-17.62", "-18.94", "2979"]
    return _primary_table(
        "CWServ.ph_agf10_zg_yq",
        ["N%03d" % i for i in range(2, 12)] + ["T001"],
        [row],
        union=["N001"] + ["N%03d" % i for i in range(2, 12)] + ["T001"])


def zg_zs_table():
    """出处: §五 zg_zs 样本（1 行 × 13 列，首列 zqjc）。"""
    cols = ["zqjc"] + ["N%03d" % i for i in range(1, 13)]
    row = ["大鹏工业", "2.3733", "334", "342", "5472", "5575",
           "0.018654709000000000000000000000", "弱势看空", "20261023", None,
           "1.9333", "3.0000", "5.0000"]
    return Table(cols=cols, rows=[row], cmd="CWServ.ph_agf10_zg_zs")


def zxts_table():
    """出处: §六 zxts（8 个 RS；主表 RS2 = 10 行 × 9 列 = N001…N008 + N020）。

    按新契约：`cols` = RS2 自己的 9 列（与行值对齐），8 个 RS 的并集（32 个）
    在 `meta["cols_union"]` 里。行值是实测采样首行。
    """
    union = (["gpdm", "gpjc", "jgid"] + ["N%03d" % i for i in range(1, 27)]
             + ["zqjc", "zqdm", "zqsc"])
    row = ["2026-06-30", "-7765250.97", "72663366.0000", "-10.72", "-0.1300",
           "7.3100", "139994642.00", "130880500.60", None]
    return _primary_table(
        "CWServ.ph_agf10_zxts",
        ["N%03d" % i for i in range(1, 9)] + ["N020"],
        [row], union=union)


def zxts2_table():
    """出处: §六 zxts2（13 RS；主表 RS11 = 4 行 × 12 列，尾部 4 个拼音列名）。"""
    union = (["N%03d" % i for i in range(1, 13)] + ["HYDM", "zqdm", "zqjc", "zqsc"]
             + ["N013", "N014"])
    row = ["行", "业", "均", "值", "1", "2", "3", "4", "881309", "600303",
           "曙光股份", "1"]
    return _primary_table(
        "CWServ.ph_agf10_zxts2",
        ["N%03d" % i for i in range(1, 9)] + ["HYDM", "zqdm", "zqjc", "zqsc"],
        [row], union=union)


def zxts3_table():
    """出处: §六 zxts3 RS1 首行（9 行 × 5 列）。"""
    cols = ["N001", "N002", "N003", "N004", "N005"]
    row = ["无人驾驶",
           "公司无人驾驶技术主要应用于煤矿井下辅助运输场景，已应用于WC50Y支架搬运车等产品。",
           "2", "880944", "477"]
    return Table(cols=cols, rows=[row], cmd="CWServ.ph_agf10_zxts3")


def zxts4_table():
    """出处: §六 zxts4 RS0 样本（6 行 × 4 列，列名 N001/N002/N003/bs）。"""
    cols = ["N001", "N002", "N003", "bs"]
    rows = [["其他收入之和", "1314157.81", "1.808556", "2"],
            ["工业清洗剂", "6051325.27", "8.327890", "2"]]
    return Table(cols=cols, rows=rows, cmd="CWServ.ph_agf10_zxts4")


def zxts5_table():
    """出处: §六 zxts5（主表 RS0 = 3 行 × 4 列；RS1 的 N005 只在并集里）。"""
    return _primary_table(
        "CWServ.ph_agf10_zxts5",
        ["N001", "N002", "N003", "N004"],
        [["2026-06-30 00:00:00", "38.250", "4557", "-21.4852"],
         ["2026-03-31 00:00:00", "60.700", "5804", "-23.4705"]],
        union=["N001", "N002", "N003", "N004", "N005"])


def zp_table():
    """出处: §七 zp（p1=00104，15 行 × 2 列，列名 N001/N0101，N001 全 null）。"""
    return Table(cols=["N001", "N0101"],
                 rows=[[None, "机构重仓"], [None, "价值低估"]],
                 cmd="CWServ.ph_agf10_zp")


def zp_ggld_table():
    """出处: §七之二 zp_ggld（主表 RS0 = 2 行 × 3 列；RS1 的 zs 只在并集里）。"""
    return _primary_table(
        "CWServ.ph_agf10_zp_ggld",
        ["N001", "N002", "N003"],
        [["专精特新", "公司已入选工信部专精特新小巨人企业名单。", "5"],
         ["筹码集中", "股东人数较上期-21.49%", "3"]],
        union=["N001", "N002", "N003", "zs"])


def sl_v2_table():
    """出处: §七 sl_v2 样本（1 行 × 3 列，列 zqjc/zqdm/N001）。"""
    return Table(cols=["zqjc", "zqdm", "N001"],
                 rows=[["大鹏工业", "920091", "中低风险"]],
                 cmd="CWServ.ph_agf10_sl_v2")


def fhrz_table(sample="600303"):
    """出处: §七之二 fhrz（1 行 × 12 列，两只票的实测样本都在文档里）。"""
    if sample == "600303":
        row = ["16", "480187293.0000", "6", "1755588712.0000", "1",
               "352000000.0000", "4", "933767126.0000", "1",
               "469821586.0000", "0", None]
    else:
        row = ["1", "19668864.0000", "1", "135000000.0000", "1",
               "135000000.0000", "0", None, "0", None, "0", None]
    return Table(cols=["N%03d" % i for i in range(1, 13)], rows=[row],
                 cmd="CWServ.ph_agf10_fhrz")


def zg_jsm_table():
    """出处: §五 zg_jsm（17 行 × 7 列，取前 2 行）。"""
    cols = ["N%03d" % i for i in range(1, 8)]
    rows = [["20261023", "沪深300", "-9.119426", "工业机械", "-16.927534",
             "大鹏工业", "19.8497854"],
            ["20261022", "沪深300", "-10.647558", "工业机械", "-19.420014",
             "大鹏工业", "-7.7968526"]]
    return Table(cols=cols, rows=rows, cmd="CWServ.ph_agf10_zg_jsm")


def zg_jby_table():
    """出处: §五 zg_jby 样本（1 行 × 10 列）。"""
    return Table(cols=["N%03d" % i for i in range(1, 11)],
                 rows=[["20261023", "1.93", "294", "342", "堪忧",
                        "0.00", "1.00", "3.00", "4.67", "1.00"]],
                 cmd="CWServ.ph_agf10_zg_jby")


def agf10_zj_table():
    """出处: 实测样本总表 #30（主表 RS3 = 1 行 × 4 列 N014…N017，四个值全 null）。"""
    return _primary_table(
        "CWServ.ph_agf10_zj",
        ["N014", "N015", "N016", "N017"],
        [[None, None, None, None]],
        union=["N%03d" % i for i in range(1, 19)])


def zbyz_table():
    """出处: §四 jyfx_zbyz 样本首行（13 行 × 7 列）。"""
    cols = ["N%03d" % i for i in range(1, 8)]
    return Table(cols=cols,
                 rows=[["2022-12-31", "北京曙光智行新能源汽车科技有限公司",
                        "9900000", "33.00", "--", None, None]],
                 cmd="CWServ.ph_agf10_jyfx_zbyz")


def rdtc_table():
    """出处: §四 rdtc 样本首行（24 行 × 3 列）。"""
    return Table(cols=["N001", "N002", "N003"],
                 rows=[["公司简介",
                        "辽宁曙光汽车集团股份有限公司(简称\"曙光汽车集团\")是以汽车整车、车桥及零部件为主营…",
                        ""]],
                 cmd="CWServ.ph_agf10_rdtc")


def zg_scm_table():
    """出处: §五 zg_scm（p1=ztms，1 行 × 8 列，首列 zqjc）。"""
    cols = ["zqjc"] + ["N%03d" % i for i in range(1, 8)]
    return Table(cols=cols,
                 rows=[["曙光股份", "1038639", "1235", "5577", "1654",
                        "5577", "419", "一般"]],
                 cmd="CWServ.ph_agf10_zg_scm")


def jysj_table():
    """出处: §四 jysj（1 行 × 3 列，实测全空）。"""
    return Table(cols=["N001", "N002", "N003"], rows=[[None, None, "0"]],
                 cmd="CWServ.ph_agf10_jysj")


# ══════════════════════════════════════════════════════════════════════════
# 1. apply()：逐产品断言
# ══════════════════════════════════════════════════════════════════════════
def test_apply_gsjj():
    t = mapping.apply(gsjj_table(), "gsjj")
    r = t.rows[0]
    assert r["share_class"] == "A股"
    assert r["list_date"] == "2000-12-26"
    assert r["issue_price_yuan"] == 8.8
    assert r["issue_vol"] == 40000000.0
    assert r["proceeds_gross_yuan"] == 352000000.0
    assert r["proceeds_net_yuan"] == 338160000.0
    assert r["lead_underwriter"] == "国泰海通证券股份有限公司"
    assert r["co_underwriters"].startswith("东北证券")
    # 唯一能自证的那条恒等式
    assert abs(r["proceeds_gross_yuan"]
               - r["issue_price_yuan"] * r["issue_vol"]) < 1.0
    # 文档标「未定论」的三列不许出现在标准字段里
    assert "N009" not in t.cols and "N013" not in t.cols and "N015" not in t.cols
    assert len(r["raw"]) == 19


def test_apply_gbgd():
    t = mapping.apply(gbgd_table(), "gbgd")
    assert t.cols == ["report_date", "holder_count",
                      "per_capita_hold_amt_wan_yuan"], t.cols
    r = t.rows[0]
    assert r["report_date"] == "2026-06-30"
    assert r["holder_count"] == 37143 and isinstance(r["holder_count"], int)
    assert r["per_capita_hold_amt_wan_yuan"] == 2.49
    assert len(t.rows) == 2
    assert len(r["raw"]) == 3


def test_apply_hypm_primary_cols():
    """主表是 RS1（N003/N004/sc/lx）→ 直接按 cols 取就是对的。"""
    src = hypm_table()
    assert len(src.cols) == len(src.rows[0]) == 4
    assert src.provenance.extra["cols_union"][:2] == ["N001", "N002"]
    t = mapping.apply(src, "hypm")
    assert t.provenance.extra["cols_align"] == "server", t.provenance
    assert t.provenance.extra["template_check"] == "match", t.provenance
    r = t.rows[0]
    assert r["name"] == "思进智能"
    assert r["code"] == "003025"
    assert r["market"] == 0 and isinstance(r["market"], int)
    assert r["category_code"] == "0"
    assert len(r["raw"]) == 4
    assert set(r["raw"]) == {"N003", "N004", "sc", "lx"}


def test_apply_jyfx_primary_cols():
    """主表 RS0：N001=主营简述、N002=经营范围；N003(UI 文案)/N004(内部键) 不映射。"""
    src = jyfx_table()
    assert len(src.cols) == len(src.rows[0]) == 4
    t = mapping.apply(src, "jyfx")
    assert t.provenance.extra["template_check"] == "match", t.provenance
    assert t.cols == ["main_business", "business_scope"], t.cols
    r = t.rows[0]
    assert r["main_business"].startswith("茅台酒及系列酒")
    assert "饮料、食品" in r["business_scope"]
    # 内部键不许被当成业务字段（曾经的错映射：N004 -> product_line_name）
    assert "gssh0600519" not in [v for k, v in r.items() if k != "raw"]
    assert r["raw"]["N004"] == "gssh0600519"


def test_jyfx_other_branch_guarded():
    """p1=00301 分支是 31×9 的另一张表（按产品构成）→ 守卫挡掉，只给 raw。"""
    cols = ["N%03d" % i for i in range(1, 10)]
    src = Table(cols=cols, rows=[[None] * 9], cmd="CWServ.ph_agf10_jyfx")
    t = mapping.apply(src, "jyfx")
    assert t.cols == [], t.cols
    assert t.provenance.extra["mapping_skipped"] == "cols-guard"
    assert t.provenance.extra["template_check"] == "mismatch"


def test_apply_zg_yq_zg_zs():
    yq = mapping.apply(zg_yq_table(), "zgyq").rows[0]
    assert yq["concept_name"] == "长安汽车概念"
    assert yq["include_date"] == "20231127"        # 日期保持字符串
    assert yq["category_text"] == "其它研究"
    assert yq["rank"] == 2979
    assert yq["reason_short"].startswith("公司服务的客户包括比亚迪")
    assert len(yq["raw"]) == 11
    # N008…N011（窗口涨跌）没映射
    for k in ("N008", "N009", "N010", "N011"):
        assert k in yq["raw"] and k not in yq

    zs = mapping.apply(zg_zs_table(), "zgzs").rows[0]
    assert zs["name"] == "大鹏工业"
    assert zs["score"] == 2.3733
    assert zs["industry_rank"] == 334
    assert zs["industry_size"] == 342
    assert zs["market_rank"] == 5472
    assert zs["market_size"] == 5575
    assert zs["trend_text"] == "弱势看空"
    assert zs["date"] == "20261023"
    assert zs["industry_rank"] <= zs["industry_size"]      # 文档给的自检点
    # N006（分位口径未定论）与 N010…N012（文档标「推断」）只留在 raw
    for k in ("N006", "N010", "N011", "N012"):
        assert k in zs["raw"] and k not in zs, k


def test_apply_zxts_is_raw_only():
    """zxts 主表是 RS2，其 9 列语义文档没给 → 不映射，只给 raw（键名即主表列名）。"""
    src = zxts_table()
    assert len(src.cols) == len(src.rows[0]) == 9
    t = mapping.apply(src, "zxts")
    assert t.cols == [], t.cols
    assert t.provenance.extra["template_check"] == "match", t.provenance
    raw = t.rows[0]["raw"]
    assert list(raw) == ["N001", "N002", "N003", "N004", "N005",
                         "N006", "N007", "N008", "N020"], list(raw)
    assert raw["N001"] == "2026-06-30"
    assert raw["N002"] == "-7765250.97"
    assert raw["N020"] is None


def test_apply_zxts2_zj_raw_only():
    t2 = mapping.apply(zxts2_table(), "zxts2")
    assert t2.cols == []
    assert t2.provenance.extra["template_check"] == "match", t2.provenance
    assert len(t2.rows[0]["raw"]) == 12

    zj = mapping.apply(agf10_zj_table(), "agf10_zj")
    assert zj.cols == []
    assert list(zj.rows[0]["raw"]) == ["N014", "N015", "N016", "N017"]
    assert all(v is None for v in zj.rows[0]["raw"].values())


def test_apply_zxts3_zxts4_zxts5():
    c = mapping.apply(zxts3_table(), "zxts3").rows[0]
    assert c["concept_name"] == "无人驾驶"
    assert c["board_code"] == "880944"
    assert c["board_rank"] == 477
    assert c["category_code"] == "2"

    z4 = mapping.apply(zxts4_table(), "zxts4")
    assert z4.rows[0]["product_name"] == "其他收入之和"
    assert z4.rows[0]["revenue_yuan"] == 1314157.81
    assert z4.rows[0]["revenue_pct"] == 1.808556
    assert "bs" in z4.rows[0]["raw"]

    z5 = mapping.apply(zxts5_table(), "zxts5").rows[0]
    assert z5["report_period"] == "2026-06-30 00:00:00"
    assert z5["per_capita_hold_amt_wan_yuan"] == 38.25
    assert z5["holder_count"] == 4557
    assert z5["holder_count_chg_pct"] == -21.4852
    assert len(z5["raw"]) == 4          # 并集里的第 5 个 N005 不属于最大表


def test_apply_zp_zp_ggld_sl_v2():
    zp = mapping.apply(zp_table(), "zp")
    assert zp.cols == ["tag_name"], zp.cols
    assert [r["tag_name"] for r in zp.rows] == ["机构重仓", "价值低估"]

    gl = mapping.apply(zp_ggld_table(), "zpggld")
    assert gl.cols == ["highlight_name", "highlight_note", "weight_code"]
    assert gl.rows[0]["highlight_name"] == "专精特新"
    assert gl.rows[0]["weight_code"] == 5 and isinstance(gl.rows[0]["weight_code"], int)
    assert gl.rows[1]["highlight_note"] == "股东人数较上期-21.49%"

    s = mapping.apply(sl_v2_table(), "sl_v2").rows[0]
    assert s["name"] == "大鹏工业" and s["code"] == "920091"
    assert s["risk_level"] == "中低风险"


def test_apply_rdtc_zbyz_zg_scm_zg_jsm_zg_jby_jysj():
    rd = mapping.apply(rdtc_table(), "rdtc").rows[0]
    assert rd["item_name"] == "公司简介"
    assert rd["content_text"].startswith("辽宁曙光汽车集团")

    zb = mapping.apply(zbyz_table(), "zbyz").rows[0]
    assert zb["report_date"] == "2022-12-31"
    assert zb["entity_name"] == "北京曙光智行新能源汽车科技有限公司"
    assert zb["raw"]["N005"] == "--"          # 未映射的列原样留在 raw

    scm = mapping.apply(zg_scm_table(), "zgscm").rows[0]
    assert scm["name"] == "曙光股份"
    assert scm["hot_rank"] == 1235
    assert scm["raw"]["N001"] == "1038639"

    jsm = mapping.apply(zg_jsm_table(), "zgjsm")
    assert jsm.cols == ["date", "benchmark_name", "benchmark_chg_pct",
                        "industry_name", "industry_chg_pct", "name", "chg_pct"], jsm.cols
    assert jsm.rows[0]["benchmark_chg_pct"] == -9.119426
    assert len(jsm.rows) == 2

    jby = mapping.apply(zg_jby_table(), "zgjby").rows[0]
    assert jby["date"] == "20261023"
    assert jby["score"] == 1.93
    assert jby["rank"] == 294 and jby["universe_size"] == 342
    assert jby["rating_text"] == "堪忧"
    assert jby["rank"] <= jby["universe_size"]
    for k in ("N006", "N007", "N008", "N009", "N010"):
        assert k in jby["raw"] and k not in jby

    jy = mapping.apply(jysj_table(), "jysj")
    assert jy.cols == [], jy.cols
    assert jy.rows[0]["raw"] == {"N001": None, "N002": None, "N003": "0"}


def test_apply_fhrz_agf10_two_stocks():
    a = mapping.apply(fhrz_table("600303"), "fhrz_agf10").rows[0]
    assert a["ipo_count"] == 1
    assert a["ipo_proceeds_yuan"] == 352000000.0
    assert "ipo_proceeds_yuan" in a

    b = mapping.apply(fhrz_table("920091"), "fhrz_agf10").rows[0]
    assert b["ipo_count"] == 1
    assert b["ipo_proceeds_yuan"] == 135000000.0
    # 结构规律：没有次数的组金额一定是 null（N011=0 → N012=null）
    assert b["raw"]["N011"] == "0" and b["raw"]["N012"] is None


# ══════════════════════════════════════════════════════════════════════════
# 2. SECTIONS / FIELD_MAP 门禁
# ══════════════════════════════════════════════════════════════════════════
def test_sections_complete():
    want = {"gsjj", "jyfx", "gbgd", "zbyz", "rdtc", "fhrz", "hypm",
            "zxts", "zxts2", "zxts3", "zxts4", "zxts5",
            "zg_jby", "zg_jsm", "zg_scm", "zg_yq", "zg_zs",
            "zp", "zp_ggld", "sl_v2", "jysj", "agf10_zj"}
    assert set(f10.SECTIONS) == want, set(f10.SECTIONS) ^ want
    for sec, val in f10.SECTIONS.items():
        assert isinstance(val, tuple) and len(val) == 2, (sec, val)
        assert val[0] in f10._PARAMS, (sec, val)


def test_every_section_product_has_params_and_fresh_template():
    """每个 section 的模板都必须带 `@code@`（否则等于没换代码）。"""
    for sec, (product, _sub) in f10.SECTIONS.items():
        cmd, ctl, tpl = f10._PARAMS[product]
        assert cmd.startswith("CWServ."), (sec, cmd)
        assert isinstance(ctl, int) and 0 < ctl < 0x40, (sec, ctl)
        assert isinstance(tpl, list) and "@code@" in tpl, (sec, tpl)
        if _sub is not None:
            assert "@sub@" in tpl, (sec, tpl)


def test_multi_rs_products_are_documented_in_template_cols():
    """凡 FIELD_MAP 里存在的产品，只要它在多 RS 命令里会错位，就必须有列清单。"""
    for product in mapping._TEMPLATE_COLS:
        assert product in mapping.FIELD_MAP, product


def test_every_reachable_product_has_field_map_key():
    """`FIELD_MAP` 的键必须是 **products.json 里的产品名**，不能是看着更好看的变体。

    （真 bug 案例：曾经把 `zgjby` 写成 `zg_jby`，`apply()` 查不到映射就静默只给
    `raw` —— 测试必须能把这种「键错但不报错」抓出来。）
    """
    for sec, (product, _sub) in f10.SECTIONS.items():
        assert product in mapping.FIELD_MAP, (sec, product)
    from tdxdata.sections import finance as F
    for kind, (product, _sub) in F.KINDS.items():
        assert product in mapping.FIELD_MAP, (kind, product)


def test_get_f10_maps_through_real_product_name():
    """走 `get_f10()` 的完整链路：SECTIONS → 产品名 → FIELD_MAP → 标准字段。

    这六条的产品名不带下划线（`zgjby` / `zgjsm` / `zgscm` / `zgyq` / `zgzs` /
    `zpggld`），最容易把 section 名与产品名写成一样。
    """
    cases = [
        ("zg_jby", zg_jby_table(), "score", 1.93),
        ("zg_jsm", zg_jsm_table(), "benchmark_chg_pct", -9.119426),
        ("zg_scm", zg_scm_table(), "hot_rank", 1235),
        ("zg_yq", zg_yq_table(), "concept_name", "长安汽车概念"),
        ("zg_zs", zg_zs_table(), "industry_rank", 334),
        ("zp_ggld", zp_ggld_table(), "highlight_name", "专精特新"),
    ]
    for sec, table, field, want in cases:
        f = FakeClient(table=table)
        out = f10.get_f10(f, "600303", section=sec)
        assert field in out.cols, (sec, out.cols)
        assert out.rows[0][field] == want, (sec, field, out.rows[0][field])
        assert out.rows[0]["raw"], sec


# ══════════════════════════════════════════════════════════════════════════
# 3. get_f10 / get_xdxr：参数构造与异常（假 client，零网络）
# ══════════════════════════════════════════════════════════════════════════
class FakeClient:
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
    "gsjj":     ("CWServ.ph_agf10_gsjj", 0x16, ["00201", "600303", "0"]),
    "jyfx":     ("CWServ.ph_agf10_jyfx", 0x16, ["00101", "600303", ""]),
    "gbgd":     ("CWServ.ph_agf10_gbgd", 0x16, ["00201", "600303", "", "", 0]),
    "zbyz":     ("CWServ.ph_agf10_jyfx_zbyz", 0x1B, ["00102", "600303"]),
    "rdtc":     ("CWServ.ph_agf10_rdtc", 0x16, ["00103", "600303", "0"]),
    "fhrz":     ("CWServ.ph_agf10_fhrz", 0x16, ["00101", "600303"]),
    "hypm":     ("CWServ.ph_agf10_hypm", 0x16, ["00301", "600303", ""]),
    "zxts":     ("CWServ.ph_agf10_zxts", 0x16, ["600303"]),
    "zxts2":    ("CWServ.ph_agf10_zxts2", 0x17, ["600303"]),
    "zxts3":    ("CWServ.ph_agf10_zxts3", 0x17, ["00501", "600303", "", ""]),
    "zxts4":    ("CWServ.ph_agf10_zxts4", 0x17, ["600303"]),
    "zxts5":    ("CWServ.ph_agf10_zxts5", 0x17, ["00101", "600303", 0]),
    "zg_jby":   ("CWServ.ph_agf10_zg_jby", 0x18, ["zhpf", "600303", "2"]),
    "zg_jsm":   ("CWServ.ph_agf10_zg_jsm", 0x18, ["jdbxzxt", "600303", "1", "", ""]),
    "zg_scm":   ("CWServ.ph_agf10_zg_scm", 0x18, ["ztms", "600303", ""]),
    "zg_yq":    ("CWServ.ph_agf10_zg_yq", 0x17, ["rdzt", "600303"]),
    "zg_zs":    ("CWServ.ph_agf10_zg_zs", 0x17, ["zhpf", "600303"]),
    "zp":       ("CWServ.ph_agf10_zp", 0x14, ["00104", "600303"]),
    "zp_ggld":  ("CWServ.ph_agf10_zp_ggld", 0x19, ["600303"]),
    "sl_v2":    ("CWServ.ph_agf10_sl_v2", 0x17, ["00201", "600303"]),
    "jysj":     ("CWServ.ph_agf10_jysj", 0x16, ["00101", "600303", "0", "0"]),
    "agf10_zj": ("CWServ.ph_agf10_zj", 0x14, ["600303"]),
}


def test_get_f10_params_for_every_section():
    for sec, (cmd, ctl, params) in EXPECTED.items():
        f = FakeClient(table=Table(cols=["N001"], rows=[["2026中报"]], cmd=cmd))
        got = f10.get_f10(f, "600303", section=sec)
        assert f.calls == [(cmd, ctl, params)], (sec, f.calls)
        flat = str(params)
        assert "@" not in flat, (sec, flat)
        # 模板里的样例代码（920091 / 600519）不许残留，目标代码必须在位
        assert "920091" not in flat and "600519" not in flat, (sec, flat)
        assert "600303" in flat, (sec, flat)
        assert isinstance(got, Table)


def test_get_f10_gsjj_uses_00201_branch():
    """文档 §2.1/§四 的 19 列列义是按 p1=00201 建立的（产品表模板却是 00101）。"""
    f = FakeClient(table=gsjj_table())
    t = f10.get_f10(f, "600303", section="gsjj")
    assert f.calls[0][2][0] == "00201"
    assert t.rows[0]["list_date"] == "2000-12-26"


def test_get_f10_default_section_is_gsjj():
    f = FakeClient(table=gsjj_table())
    f10.get_f10(f, "600303")
    assert f.calls[0][0] == "CWServ.ph_agf10_gsjj"


def test_get_f10_prefixed_symbol():
    f = FakeClient(table=sl_v2_table())
    f10.get_f10(f, "bj920091", section="sl_v2")
    assert f.calls[0][2] == ["00201", "920091"]


def test_get_f10_bad_section_lists_all_values():
    f = FakeClient(table=gsjj_table())
    try:
        f10.get_f10(f, "600303", section="gongsi")     # 常见写错
    except ValueError as e:
        msg = str(e)
        for sec in f10.SECTIONS:
            assert sec in msg, (sec, msg)
        assert "gongsi" in msg
    else:
        raise AssertionError("应当抛 ValueError")
    assert f.calls == [], "参数不认识时不许发请求"


def test_get_f10_propagates_empty_result():
    f = FakeClient(exc=errors.EmptyResult("CWServ.ph_agf10_gsjj 无数据（有列无行）"))
    try:
        f10.get_f10(f, "600303", section="gsjj")
    except errors.EmptyResult:
        pass
    else:
        raise AssertionError("应当抛 EmptyResult")


def test_get_f10_propagates_not_supported():
    f = FakeClient(exc=errors.NotSupportedError("通道不通"))
    try:
        f10.get_f10(f, "600303", section="zxts")
    except errors.NotSupportedError:
        pass
    else:
        raise AssertionError("应当抛 NotSupportedError")


# ── 分红融资（xdxr）──────────────────────────────────────────────────────
def test_get_xdxr_params_and_mapping():
    f = FakeClient(table=fhrz_table("600303"))
    t = xdxr.get_xdxr(f, "600303")
    assert f.calls == [("CWServ.ph_agf10_fhrz", 0x16, ["00101", "600303"])], f.calls
    assert t.cols == ["ipo_count", "ipo_proceeds_yuan"], t.cols
    assert t.rows[0]["ipo_count"] == 1
    assert t.rows[0]["ipo_proceeds_yuan"] == 352000000.0
    assert len(t.rows[0]["raw"]) == 12


def test_get_xdxr_same_as_f10_fhrz():
    """xdxr 与 get_f10(..., "fhrz") 同源同表 —— 保证两处不漂移。"""
    a = FakeClient(table=fhrz_table("600303"))
    b = FakeClient(table=fhrz_table("600303"))
    ta = xdxr.get_xdxr(a, "600303")
    tb = f10.get_f10(b, "600303", section="fhrz")
    assert a.calls == b.calls
    assert ta.cols == tb.cols
    assert ta.rows == tb.rows


def test_xdxr_is_honest_about_no_real_xdxr_command():
    """docstring 必须写明「7615 没有独立的除权除息命令」—— 防止有人把这句话删掉。"""
    doc = xdxr.__doc__ or ""
    assert "没有独立的" in doc and "除权除息" in doc, doc[:120]
    assert "分红融资" in doc


def test_apply_unknown_product_keeps_raw():
    src = Table(cols=["N001", "N002"], rows=[["a", "b"]], cmd="X")
    t = mapping.apply(src, "不存在")
    assert t.cols == []
    assert t.rows[0]["raw"] == {"N001": "a", "N002": "b"}


# ══════════════════════════════════════════════════════════════════════════
# 4. 实网样本回归（2026-10-25 抓的，`Client(gap=4.4)` 单线程真跑）
# ══════════════════════════════════════════════════════════════════════════
# 列名 + 前 2 行值全部原样固化；改映射必在这里露馅。

REAL_GSJJ_600519 = {
    "cmd": 'CWServ.ph_agf10_gsjj',
    "n_rows": 1,          # 服务端实际返回的行数（这里只固化前 1 行）
    "cols": [
        'N001', 'N002', 'N003', 'N004', 'N005', 'N006', 'N007', 'N008', 'N009', 'N010', 'N011', 'N012',
        'N013', 'N014', 'N015', 'N016', 'N017', 'N018', 'N019',
    ],
    "rows": [
        [
            'A股', '2001-08-27', '上网定价发行', '定价发行', '1.00', '31.390', '71500000.0000', '71500000.0000',
            '2.400', '9.760', '2244385000.0000', '2202179475.0000', '23.930', '1.1306', '42205525.0000',
            '南方证券股份有限公司', '北京证券有限责任公司,南方证券股份有限公司', '在上交所开立股票帐户的境内自然人及法人(法律、法规禁止者除外)',
            '新老股东共享2001年1月1日以后利润',
        ],
    ],
}
"""出处: 实网 600519 · gsjj p1=00201 → 1 行 × 19 列"""
def real_gsjj_table():
    """原始 `Table`（按新契约：cols = 主表列，并集在 meta）。"""
    src = REAL_GSJJ_600519
    meta = {"cols_source": "primary"}
    if src.get("cols_union"):
        meta["cols_union"] = list(src["cols_union"])
    return Table(cols=list(src["cols"]), rows=[list(r) for r in src["rows"]],
                 cmd='CWServ.ph_agf10_gsjj', provenance=_prov_of(meta))

REAL_FHRZ_600519 = {
    "cmd": 'CWServ.ph_agf10_fhrz',
    "n_rows": 1,          # 服务端实际返回的行数（这里只固化前 1 行）
    "cols": [
        'N001', 'N002', 'N003', 'N004', 'N005', 'N006', 'N007', 'N008', 'N009', 'N010', 'N011', 'N012',
    ],
    "rows": [
        [
            '30', '401145437253.0000', '1', '2244385000.0000', '1', '2244385000.0000', '0', None, '0',
            None, '0', None,
        ],
    ],
}
"""出处: 实网 600519 · fhrz → 1 行 × 12 列"""
def real_fhrz_table():
    """原始 `Table`（按新契约：cols = 主表列，并集在 meta）。"""
    src = REAL_FHRZ_600519
    meta = {"cols_source": "primary"}
    if src.get("cols_union"):
        meta["cols_union"] = list(src["cols_union"])
    return Table(cols=list(src["cols"]), rows=[list(r) for r in src["rows"]],
                 cmd='CWServ.ph_agf10_fhrz', provenance=_prov_of(meta))

REAL_JYFX_600519 = {
    "cmd": 'CWServ.ph_agf10_jyfx',
    "n_rows": 1,          # 服务端实际返回的行数（这里只固化前 1 行）
    "cols": [
        'N001', 'N002', 'N003', 'N004',
    ],
    "rows": [
        [
            '茅台酒及系列酒的生产与销售',
            '茅台酒系列产品的生产与销售；饮料、食品、包装材料的生产、销售；防伪技术开发、信息产业相关产品的研制、开发；酒店经营管理、住宿、餐饮、娱乐、洗浴及停车场管理服务(具体内容以工商核定登记为准)。',
            '查看详情', 'gssh0600519',
        ],
    ],
}
"""出处: 实网 600519 · jyfx p1=00101 → 主表是 RS0 1 行 × 4 列"""
def real_jyfx_table():
    """原始 `Table`（按新契约：cols = 主表列，并集在 meta）。"""
    src = REAL_JYFX_600519
    meta = {"cols_source": "primary"}
    if src.get("cols_union"):
        meta["cols_union"] = list(src["cols_union"])
    return Table(cols=list(src["cols"]), rows=[list(r) for r in src["rows"]],
                 cmd='CWServ.ph_agf10_jyfx', provenance=_prov_of(meta))

REAL_HYPM_600519 = {
    "cmd": 'CWServ.ph_agf10_hypm',
    "n_rows": 25,          # 服务端实际返回的行数（这里只固化前 2 行）
    "cols": [
        'N003', 'N004', 'sc', 'lx',
    ],
    "rows": [
        [
            '泸州老窖', '000568', '0', '0',
        ],
        [
            '酒鬼酒', '000799', '0', '0',
        ],
    ],
}
"""出处: 实网 600519 · hypm p1=00301 → 25 行 × 4 列（N003/N004/sc/lx）"""
def real_hypm_table():
    """原始 `Table`（按新契约：cols = 主表列，并集在 meta）。"""
    src = REAL_HYPM_600519
    meta = {"cols_source": "primary"}
    if src.get("cols_union"):
        meta["cols_union"] = list(src["cols_union"])
    return Table(cols=list(src["cols"]), rows=[list(r) for r in src["rows"]],
                 cmd='CWServ.ph_agf10_hypm', provenance=_prov_of(meta))

REAL_ZXTS5_600519 = {
    "cmd": 'CWServ.ph_agf10_zxts5',
    "n_rows": 8,          # 服务端实际返回的行数（这里只固化前 2 行）
    "cols": [
        'N001', 'N002', 'N003', 'N004',
    ],
    "rows": [
        [
            '2026-06-30 00:00:00', '1185.490', '296404', '21.8972',
        ],
        [
            '2026-03-31 00:00:00', '1450.000', '243159', '-4.9759',
        ],
    ],
}
"""出处: 实网 600519 · zxts5 → 8 行 × 4 列（并集 5）"""
def real_zxts5_table():
    """原始 `Table`（按新契约：cols = 主表列，并集在 meta）。"""
    src = REAL_ZXTS5_600519
    meta = {"cols_source": "primary"}
    if src.get("cols_union"):
        meta["cols_union"] = list(src["cols_union"])
    return Table(cols=list(src["cols"]), rows=[list(r) for r in src["rows"]],
                 cmd='CWServ.ph_agf10_zxts5', provenance=_prov_of(meta))

REAL_ZP_600519 = {
    "cmd": 'CWServ.ph_agf10_zp',
    "n_rows": 15,          # 服务端实际返回的行数（这里只固化前 2 行）
    "cols": [
        'N001', 'N0101',
    ],
    "rows": [
        [
            '基金扎堆', '机构重仓',
        ],
        [
            '机构重仓', '机构重仓',
        ],
    ],
}
"""出处: 实网 600519 · zp p1=00104 → 15 行 × 2 列"""
def real_zp_table():
    """原始 `Table`（按新契约：cols = 主表列，并集在 meta）。"""
    src = REAL_ZP_600519
    meta = {"cols_source": "primary"}
    if src.get("cols_union"):
        meta["cols_union"] = list(src["cols_union"])
    return Table(cols=list(src["cols"]), rows=[list(r) for r in src["rows"]],
                 cmd='CWServ.ph_agf10_zp', provenance=_prov_of(meta))


def t_cols(applied):
    return list(applied.cols)


# ── 实网断言 ─────────────────────────────────────────────────────────────
def test_real_gsjj_600519_mapping_and_identity():
    """实网 gsjj（p1=00201）= 1 行 × 19 列；发行价 × 发行量 == 募集总额。"""
    src = real_gsjj_table()
    assert src.shape == (1, 19)
    t = mapping.apply(src, "gsjj")
    r = t.rows[0]
    assert r["share_class"] == "A股"
    assert r["list_date"] == "2001-08-27"
    assert r["issue_price_yuan"] == 31.39
    assert r["issue_vol"] == 71500000.0
    assert abs(r["issue_price_yuan"] * r["issue_vol"] - r["proceeds_gross_yuan"]) < 1.0
    assert r["proceeds_gross_yuan"] == 2244385000.0


def test_real_fhrz_ipo_group_matches_gsjj():
    """实网：fhrz 第 3 组（IPO）的募资额与 gsjj 的募集总额逐位相同。"""
    f = mapping.apply(real_fhrz_table(), "fhrz_agf10").rows[0]
    g = mapping.apply(real_gsjj_table(), "gsjj").rows[0]
    assert f["ipo_proceeds_yuan"] == g["proceeds_gross_yuan"] == 2244385000.0
    assert f["ipo_count"] == 1
    # 其余 5 组（含 N001=30 / N002=4011 亿 —— 更像「累计分红次数/金额」而不是增发）
    # 文档没定论 → 一律不映射，只留 raw
    assert f["raw"]["N001"] == "30"
    assert "rights_issue_count" not in t_cols(f)


def test_real_jyfx_is_rs0_not_product_lines():
    """实网定论：jyfx(00101) 主表是 RS0（主营简述 + 经营范围），不是 RS1 产品线。"""
    src = real_jyfx_table()
    assert src.shape == (1, 4)
    t = mapping.apply(src, "jyfx")
    assert t.provenance.extra["template_check"] == "match", t.provenance
    r = t.rows[0]
    assert t.cols == ["main_business", "business_scope"]
    assert r["main_business"].startswith("茅台酒")
    assert "经营范围" not in str(r["main_business"])
    assert len(r["business_scope"]) > 50
    assert r["raw"]["N003"] == "查看详情"      # UI 文案，不映射
    assert r["raw"]["N004"].startswith("gssh")  # 内部键，不映射


def test_real_hypm_600519():
    """实网 hypm(00301) = 25 行 × 4 列；第 1 行是「同行业另一只票」。"""
    src = real_hypm_table()
    assert src.shape == (2, 4) and REAL_HYPM_600519["n_rows"] == 25
    t = mapping.apply(src, "hypm")
    assert t.provenance.extra["template_check"] == "match", t.provenance
    r = t.rows[0]
    assert r["name"] == "泸州老窖"
    assert r["code"] == "000568"
    assert r["market"] == 0
    assert r["category_code"] == "0"


def test_real_zxts5_600519():
    """实网 zxts5 = 8 行 × 4 列（文档说 3 行，行数可变）；筹码集中度四列全中。"""
    src = real_zxts5_table()
    assert src.shape == (2, 4) and REAL_ZXTS5_600519["n_rows"] == 8
    t = mapping.apply(src, "zxts5")
    assert t.provenance.extra["template_check"] == "match", t.provenance
    r = t.rows[0]
    assert r["per_capita_hold_amt_wan_yuan"] == 1185.49
    assert r["holder_count"] == 296404
    assert r["holder_count_chg_pct"] == 21.8972


def test_real_zp_600519_n001_is_not_null():
    """实测反例：文档说 zp 的 N001 恒 null，实网 600519 有值 → 只映射 N0101。"""
    src = real_zp_table()
    assert src.shape == (2, 2) and REAL_ZP_600519["n_rows"] == 15
    assert src.rows[0]["N001"] == "基金扎堆"        # 文档错，实测有值
    t = mapping.apply(src, "zp")
    assert t.cols == ["tag_name"], t.cols
    assert t.rows[0]["tag_name"] == "机构重仓"
    assert t.rows[0]["raw"]["N001"] == "基金扎堆"


# ══════════════════════════════════════════════════════════════════════════
# 5. 跨模块 Table 形状契约（与 C 的 boards/flow 同口径，Lead 裁决）
# ══════════════════════════════════════════════════════════════════════════
def assert_table_contract(t, who=""):
    """统一口径：`cols` **不含** `"raw"`；`raw` 是每行都带的 sidecar。"""
    assert "raw" not in t.cols, (who, t.cols)
    if not t.rows:
        return
    assert "raw" in t.rows[0], (who, list(t.rows[0]))
    assert set(t.cols) <= set(t.rows[0]), (who, set(t.cols) - set(t.rows[0]))
    assert len(t.rows[0]) == len(t.cols) + 1, (who, len(t.rows[0]), len(t.cols))
    for r in t.rows:
        assert isinstance(r, dict), who
        assert len(r) == len(t.cols) + 1, (who, len(r), len(t.cols))
        for i, c in enumerate(t.cols):
            assert r[i] == r[c], (who, i, c)


def _table_of_shape(product):
    cols = (mapping.COL_GUARD.get(product)
            or mapping._TEMPLATE_COLS.get(product)
            or ["N001", "N002"])
    return Table(cols=list(cols), rows=[[None] * len(cols)], cmd=" 假命令 ")


def test_contract_holds_for_every_section():
    """走公开 API：22 个 section 全过一遍形状契约。"""
    for section in f10.SECTIONS:
        product = f10.SECTIONS[section][0]
        t = f10.get_f10(FakeClient(table=_table_of_shape(product)),
                        "600303", section=section)
        assert_table_contract(t, section)


def test_contract_holds_for_every_mapped_product():
    for product in mapping.FIELD_MAP:
        assert_table_contract(mapping.apply(_table_of_shape(product), product), product)


def test_contract_holds_for_xdxr():
    t = xdxr.get_xdxr(FakeClient(table=fhrz_table()), "600303")
    assert_table_contract(t, "xdxr")
    assert t.shape == (1, 2), t.shape        # ipo_count / ipo_proceeds_yuan


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
