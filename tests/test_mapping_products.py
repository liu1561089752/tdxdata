# -*- coding: utf-8 -*-
r"""宏观域 / 基金域 `mapping.FIELD_MAP` 的**真实数据锚定**测试（离线，零网络）。

三轮演进（本文件是第三轮的形态）
--------------------------------
1. **阶段 B（2026-10-20/24 文档样本）**：只按域文档的「采样首行」锚定。
2. **审计修正轮（03-AUDIT-REPORT.md）**：撤掉凭空升级的 `agf10_jj N011`。
3. **抓包挖掘轮（2026-10-27）**：**改用 31 份原始 pcap 解出的真实列清单做输入锚点**。
   为什么必须换：上一轮的用例是「自己造模板表 → 断言映射」，一旦模板与真实漂移就
   **测不出来**（`zsjj` 的 PB 错位整整一轮没被发现，就是这么漏的）。

证据来源（只读）
----------------
* `F:\TDX\reports\eval3\corpus\full.jsonl` —— 6976 条真实请求↔回包（31 份 pcap，2026-10-20~27）
* `F:\TDX\reports\eval3\corpus\real_shapes.json` —— 同源的列形状汇总
* `F:\TDX\reports\eval3\findings\M1-fund.md`（基金/ETF）、`M2-macro.md`（宏观）
* 文档样本（**只在语料没有回包时**兜底）：`数据中心剩余接口.md` §4、`F10与个股资料.md`

三条不变式（本文件的地基）
--------------------------
1. **形状真相**：`REAL_SHAPES[p]` 是 pcap 里 p 的真实列清单集合（硬编码，注明来源）。
2. **守卫完备**：真实有多套形状且已映射的产品**必须**在 `COL_GUARD` 里，
   且守卫清单必须**逐位等于**其中一套真实形状。
3. **错形状被挡**：除登记形状外的真实形状一律 `mapping_skipped == "cols-guard"`、`cols == []`。

跑法：`python -m pytest tests/test_mapping_products.py -q`
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.stdout.reconfigure(encoding="utf-8")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from tdxdata.models import Table                                        # noqa: E402
from tdxdata.sections import mapping                                    # noqa: E402

_PRODUCTS_JSON = os.path.join(_ROOT, "tdxdata", "data", "products.json")

#: 阶段 B 新加的 16 个产品键（闭集）。
NEW_PRODUCTS = (
    "hg_gdp", "czsz", "holiday", "world_cal", "gscb",
    "etf_t0", "etf_kjzs", "etf_sx_jjgs", "jjph", "jjpj", "jjhq",
    "zsjj", "zsgz", "zcjj", "agf10_jj", "jj_cgtj",
)

#: 产品名 → 磁盘 cmd（键名必须钉在真实 cmd 上，防「看着更顺眼但查不到」）。
EXPECTED_CMD = {
    "hg_gdp": "CWServ.ph_datacenter_hg_gdp",
    "czsz": "CWServ.ph_datacenter_hg_czsz",
    "holiday": "CWServ.ph_datacenter_hg_holiday",
    "world_cal": "CWServ.ph_datacenter_hg_cal",
    "gscb": "CWServ.ph_tdxdatacenter_gscb_sy",
    "etf_t0": "CWServ.ph_jjzt_etf_t0",
    "etf_kjzs": "CWServ.ph_jjzt_etfjj_kjzsetf",
    "etf_sx_jjgs": "CWServ.ph_jjzt_etf_sx_jjgs",
    "jjph": "CWServ.ph_jjzt_jjph",
    "jjpj": "CWServ.ph_jjzt_jjpj",
    "jjhq": "CWServ.ph_jjzt_jjsc_jjhq",
    "zsjj": "CWServ.ph_jjzt_etf_zsjj",
    "zsgz": "CWServ.ph_jjzt_zsgz",
    "zcjj": "CWServ.ph_jjzt_zcjj",
    "agf10_jj": "CWServ.ph_agf10_jj",
    "jj_cgtj": "CWServ.ph_agf10_jj_cgtj",
}


# ══════════════════════════════════════════════════════════════════════════
# 一、形状真相源：pcap 里出现过的**全部**列清单（硬编码）
# ══════════════════════════════════════════════════════════════════════════
# 出处: `F:\TDX\reports\eval3\corpus\full.jsonl`（31 份 pcap，2026-10-20~27；
#       只取 verdict=PASS 且 primary 非空的记录）—— 与 `real_shapes.json` 同源。
#       注：`real_shapes.json` 对个别产品只保留了部分形状（如 sl_v2 只留 1 条），
#       所以这里以 full.jsonl 的全集为准。
REAL_SHAPES = {
    "gbgd": [
        ["N001"],
        ["N001", "N002", "N0021", "N003", "N0031", "N004", "N0041", "N005",
         "N0051", "N006", "N0061", "N007", "N0071", "N008", "N0081", "N009",
         "N0091", "N010", "N0101", "N011", "N0111", "N012", "N0121", "N013",
         "N0131", "N014", "N0141", "N015", "N0151", "N016", "N0161"],
        ["N001", "N002", "N003"],
        ["N001", "N002", "N003", "N004", "N005"],
        ["N001", "N002", "N003", "N004", "N005", "N006"],
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008"],
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "N009",
         "N010"],
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "N009",
         "N010", "N011"],
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "N009",
         "N010", "N011", "N012", "N013", "N014", "N015", "N016"],
        ["N002", "N003", "N004", "N005", "N006", "N007", "N022", "N016", "N017",
         "N008", "N025", "N026", "TDXGDID", "JGID"],
        ["zqjc"],
    ],
    "fhrz_agf10": [
        ["N001", "N002", "N003", "N004", "N005"],
        ["N001", "N002", "N003", "N004", "N005", "N006"],
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "N009",
         "N010", "N011"],
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "N009",
         "N010", "N011", "N012"],
        ["N004", "N005", "N006", "N007", "N014"],
        ["N005", "N006", "N007", "N008", "zqsc"],
        ["N006", "N007", "N008", "N009", "N014"],
        ["N006", "N007", "N008", "N009", "zqsc"],
    ],
    "zbyz": [
        ["N001", "N002", "N003", "N004", "N005", "N006"],
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007"],
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "N009",
         "N010"],
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "N009",
         "N010", "N011", "N012", "N013"],
    ],
    "gsjj": [
        ["N001", "N002", "N003", "N004", "N005"],
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "N009",
         "N010", "N011", "N012", "N013", "N014", "N015", "N016", "N017", "N018",
         "N019"],
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "N009",
         "N010", "N011", "N012", "N013", "N014", "N015", "N016", "N017", "N018",
         "N019", "N020", "N021", "N022", "N023", "N024", "N025", "N026", "N027",
         "N028", "N029", "N030", "N031", "N035", "N039", "N041", "N042", "N043",
         "N044", "N045", "N046", "N047"],
    ],
    "hypm": [
        ["N001", "N002", "N003"],
        ["N001", "N002", "N003", "N004", "N005"],
        ["N003", "N004", "sc", "lx"],
        ["N006", "N007", "N008", "zqdm", "zqsc"],
        ["zqjc", "zqdm", "zqsc"],
    ],
    "rdtc": [
        ["N001", "N002", "N003"],
        ["N001", "N002", "N003", "N004"],
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "zqjc", "zqsc",
         "zqdm1", "zqjc1", "zqsc1", "zqdm2", "zqjc2", "zqsc2", "zqdm3", "zqjc3",
         "zqsc3", "id"],
    ],
    "sl_v2": [
        ["N001", "N002", "N003", "N004", "N005"],
        ["zqjc", "zqdm", "N001"],
        ["zqjc", "zqdm", "N002", "N003", "N004", "N005", "N006"],
    ],
    "zgscm": [
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008"],
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "N009",
         "N010", "N011"],
        ["zqjc", "N001", "N002", "N003", "N004", "N005", "N006", "N007"],
    ],
    "zxts5": [
        ["N001", "N002", "N003", "N004"],
        ["N001", "N002", "N003", "N004", "N005"],
        ["N004", "N005", "N006", "N007"],
    ],
    "cwbg": [
        ["N001", "N002", "N003"],
        ["N001", "N002", "N003", "N004", "N005", "t006"],
        ["N005"],
    ],
    "zgjsm": [
        ["N001", "N002"],
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007"],
    ],
    "zgzs": [
        ["N001"],
        ["zqjc", "N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008",
         "N009", "N010", "N011", "N012"],
    ],
    "zxts3": [
        ["N001", "N002", "N003", "N004", "N005"],
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "HYDM",
         "zqdm", "zqjc", "zqsc"],
        ["zqjc", "zqdm", "zqsc"],
    ],
    "zxts4": [
        ["N001", "N002", "N003", "bs"],
        ["N001", "N002", "N005"],
    ],
    "etf_kjzs": [
        ["N001", "N002", "SC", "N005"],
        ["N001", "N002", "SC", "N006", "N007", "N009", "N010", "N011", "N012",
         "N013", "N014", "N015", "N016", "N017", "N018"],
    ],
    "zsgz": [
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "N009",
         "N010", "N011", "zqsc", "N012", "N013", "N014", "N015", "N016", "N017",
         "N018", "N019", "N020"],
        ["N001", "N002", "N003", "sc", "N004", "N006", "N007", "N008", "N009",
         "N010", "N011", "N012", "N013", "N014"],
    ],
    "agf10_jj": [
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007"],
        ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "N009",
         "N010", "N011"],
    ],
    "zpggld": [
        ["N001", "N002", "N003"],
        ["zs"],
    ],
    # 单形状（不需要守卫）—— 列在这里是为了「样本形状必须是真实形状」这条断言
    "zsjj": [["N%03d" % i for i in range(1, 21)]],
    "jjph": [["rank"] + ["N%03d" % i for i in range(1, 17)] + ["zqsc"]],
    "jjpj": [["rank"] + ["N%03d" % i for i in range(1, 8)] + ["zqsc"]],
    "zcjj": [["N001", "N002", "SC"] + ["N%03d" % i for i in range(3, 18)]],
    "czsz": [["N%03d" % i for i in range(1, 6)],
             ["N%03d" % i for i in range(1, 14)],
             ["N%03d" % i for i in range(1, 17)]],
    "hg_gdp": [["N%03d" % i for i in range(1, 6)],
               ["N%03d" % i for i in range(1, 7)],
               ["N%03d" % i for i in range(1, 14)]],
    "world_cal": [["N%03d" % i for i in range(1, 5)],
                  ["N%03d" % i for i in range(1, 10)]],
    "holiday": [["N%03d" % i for i in range(1, 7)]],
    "gscb": [["N%03d" % i for i in range(1, 5)],
             ["N%03d" % i for i in range(1, 6)],
             ["N%03d" % i for i in range(1, 8)]],
    "etf_t0": [["N%03d" % i for i in range(1, 8)]],
    "jj_cgtj": [["N%03d" % i for i in range(1, 15)]],

    # ── 抓包挖掘第二轮（2026-10-28，改派 M2 执行）──
    # 出处: `full.jsonl` 现算（同上口径，verdict=PASS 且 primary 非空）；
    #       注释里的 n= 是该形状在语料里的记录数，顺序按 n 降序。
    "hq_nlp_zjlx": [                       # 列名由服务端下发，不是 N0xx
        ["code", "name", "setcode", "now", "zf", "zf_range", "hsl_range",
         "net_buy", "rate_buy", "jb_buy", "net_zl", "rate_zl", "jb_zl",
         "hy_zl", "amo_min2", "hsl_d", "zs"],                       # n=22
        ["code", "name", "setcode", "now", "zf", "zf_range", "hsl_range",
         "net_buy", "rate_buy", "jb_buy", "net_zl", "rate_zl", "jb_zl",
         "hy_zl", "amo_min2", "hsl_d", "zs", "stockList"],          # n=14（板块）
    ],
    "lrmx": [
        ["N%03d" % i for i in range(1, 8)],                         # n=29  p1=00103 日度序列
        ["N%03d" % i for i in range(1, 10)],                        # n=23  p1=00102 ← 映射依据
        ["Sdate"],                                                 # n=6   p1=00101 最新数据日
        ["N%03d" % i for i in range(1, 7)],                         # n=2   p1=00104 另一口径日度
        ["zqjc", "zqdm"] + ["N%03d" % i for i in range(1, 16)] + ["zqsc"],   # n=2  p1=00203 个股
    ],
    "north": [
        ["N%03d" % i for i in range(1, 5)],                         # n=14  lszjlx2
        ["N%03d" % i for i in range(1, 10)],                        # n=7   lszjlx3 ← 默认+映射依据
    ],
    "zdsl": [["zqjc", "zqdm", "N001", "N002", "N003", "N004", "N005", "TYPE"]],   # n=32
    "zdzq": [["zqjc", "zqdm", "N001", "N002", "N003", "N004", "N005", "TYPE"]],   # n=1
    "lrrb": [                                                       # 本轮**只登记守卫**，无映射
        ["N%03d" % i for i in range(1, 9)],                          # n=16  p1=00301 ← 默认
        ["N%03d" % i for i in range(1, 23)],                         # n=15  p1=00101 句子表
        ["N%03d" % i for i in range(1, 6)],                          # n=4   p1=00102/00103
    ],
}

#: 语料里**没有回包**的产品（31 份 pcap 零帧）：样本形状只能来自文档采样。
NO_CORPUS = {
    "etf_sx_jjgs": "31 份 pcap 全字节扫描 0 帧（M1 §2.8；需补抓 params=[] 的那次请求）",
    "jjhq": "31 份 pcap 全字节扫描 0 帧（M1 §2.8；需补抓 ph_jjzt_jjsc_jjhq）",
}


def _products():
    with open(_PRODUCTS_JSON, encoding="utf-8") as fh:
        return json.load(fh)["products"]


def _cmd(product):
    """产品名 → 真实 cmd（守卫用例也会用到非本域的 18 个产品）。"""
    if product in EXPECTED_CMD:
        return EXPECTED_CMD[product]
    return _products()[product]["cmd"]


def _apply(product, cols, rows):
    """按**真实列清单** + 真实样本行造表，走真正的 `mapping.apply()`。"""
    return mapping.apply(Table(cols=list(cols), rows=[list(r) for r in rows],
                               cmd=_cmd(product)), product)


def _assert_shape(out, src_cols, expect):
    """统一口径：标准字段集合/顺序 + `len(row) == len(标准字段) + 1` + raw 完整。"""
    assert out.cols == list(expect), (out.cols, list(expect))
    assert len(out.rows) >= 1
    for r in out.rows:
        assert "raw" in r, "每行必须带 raw（原始列一个都不丢）"
        assert set(r["raw"]) == set(src_cols), (set(src_cols) - set(r["raw"]))
        assert len(r) == len(out.cols) + 1, (len(r), len(out.cols))
    return out.rows[0]


def _assert_unmapped(out, *col_names):
    """这些列**必须**没被映射：仍在 `raw` 里，但一条映射都没有（铁律的可执行形式）。"""
    mapped_cols = set(mapping.FIELD_MAP[out.provenance.extra["product"]])
    for col in col_names:
        assert col in out.rows[0]["raw"], ("原始列丢了", col)
        assert col not in mapped_cols, ("这列不该被映射", col)


# ══════════════════════════════════════════════════════════════════════════
# 二、结构门禁
# ══════════════════════════════════════════════════════════════════════════
def test_every_field_map_key_is_a_real_product():
    products = _products()
    missing = [p for p in mapping.FIELD_MAP if p not in products]
    assert not missing, "FIELD_MAP 里有 products.json 不存在的产品名：%r" % (missing,)


def test_new_products_list_is_exactly_what_was_added():
    assert len(NEW_PRODUCTS) == 16
    assert len(set(NEW_PRODUCTS)) == 16
    for product in NEW_PRODUCTS:
        assert product in mapping.FIELD_MAP, product
        assert product in _products(), product
        assert product in mapping.PRODUCTS, product        # 16 个都有映射（zcjj 本轮补上）
    assert set(mapping.PRODUCTS) == {p for p in mapping.FIELD_MAP if mapping.FIELD_MAP[p]}


def test_new_product_cmd_matches_documented_command():
    products = _products()
    for product in NEW_PRODUCTS:
        spec = products[product]
        assert spec["cmd"] == EXPECTED_CMD[product], (product, spec["cmd"])
        assert isinstance(spec["ctl"], int) and 0 < spec["ctl"] < 0x40, (product, spec["ctl"])


def test_standard_fields_are_unique_within_each_registered_shape():
    """同一**形状**内标准字段不得重名。

    为什么按形状而不是按产品：`etf_kjzs` 两套形状里 `N005`（4 列精简）与 `N007`（15 列明细）
    是**同一个底层字段的两个 id**（280/280 逐只相等，M1 §2.1）——两列**互斥出现**，
    所以在整个产品上「share_vol 出现两次」是对的；同一形状内仍然必须唯一。
    """
    for product in NEW_PRODUCTS:
        fmap = mapping.FIELD_MAP[product]
        shapes = []
        guard = mapping.COL_GUARD.get(product)
        if guard is not None:
            shapes.append(list(guard))
            shapes.extend(list(s) for s in mapping.COL_GUARD_MORE.get(product, ()))
        else:
            shapes.append(list(fmap))
        for shape in shapes:
            present = set(shape)
            stds = [s for col, s in fmap.items() if col in present]
            dup = {s for s in stds if stds.count(s) > 1}
            assert not dup, (product, shape, dup)
            for std in stds:
                assert std == std.strip() and " " not in std, (product, std)
                assert not std.startswith("N0"), (product, std)


def test_template_cols_are_subset_of_field_map_and_cover_mapped_cols():
    """模板若存在，必须能装下该产品**所有**被映射的列（辅助形状也算）。"""
    for product, tpl in mapping._TEMPLATE_COLS.items():
        assert product in mapping.FIELD_MAP, product
        covered = set(tpl)
        for shape in mapping.COL_GUARD_MORE.get(product, ()):
            covered |= set(shape)
        missing = set(mapping.FIELD_MAP[product]) - covered
        assert not missing, (product, missing)


def test_sample_shapes_are_real_pcap_shapes():
    """本文件用到的每个产品都必须登记过**真实**形状（否则就是「自造模板」的一类）。"""
    for product in NEW_PRODUCTS:
        if product in NO_CORPUS:
            assert product not in REAL_SHAPES, product
            continue
        assert product in REAL_SHAPES, ("缺少真实形状登记", product)


# ══════════════════════════════════════════════════════════════════════════
# 三、守卫完备性（这一节让 `map_audit.py` 的风险数从 18 降到 0）
# ══════════════════════════════════════════════════════════════════════════
def test_every_multi_shape_mapped_product_is_guarded():
    """真实有多套列形状 + 已映射 → **必须**有 `COL_GUARD`（否则换参数就静默错映射）。"""
    unguarded = []
    for product, shapes in REAL_SHAPES.items():
        if len(shapes) <= 1:
            continue
        if not mapping.FIELD_MAP.get(product):
            continue
        if product not in mapping.COL_GUARD:
            unguarded.append(
                "%s 有 %d 套真实形状（%s）但没有 COL_GUARD"
                % (product, len(shapes), [len(s) for s in shapes]))
    assert not unguarded, "\n  " + "\n  ".join(unguarded)


@pytest.mark.parametrize("product", sorted(p for p in REAL_SHAPES
                                           if len(REAL_SHAPES[p]) > 1
                                           and mapping.FIELD_MAP.get(p)))
def test_guard_list_is_one_of_the_real_shapes(product):
    """守卫清单必须是**真实形状之一**且逐位相等（不是手写模板、不是错的顺序）。"""
    guard = mapping.COL_GUARD.get(product)
    assert guard is not None, product
    assert list(guard) in REAL_SHAPES[product], (product, list(guard), REAL_SHAPES[product])
    # 映射键必须都落在**允许的形状并集**里（否则守卫过严会把映射全丢掉）
    allowed = set(guard)
    for shape in mapping.COL_GUARD_MORE.get(product, ()):
        allowed |= set(shape)
    assert set(mapping.FIELD_MAP[product]) <= allowed, product


@pytest.mark.parametrize("product", sorted(p for p in REAL_SHAPES
                                           if len(REAL_SHAPES[p]) > 1
                                           and mapping.FIELD_MAP.get(p)))
def test_other_real_shapes_are_blocked(product):
    """除登记形状外的**真实**形状一律被挡：`cols == []` + `mapping_skipped == cols-guard`。"""
    allowed = [list(mapping.COL_GUARD[product])]
    allowed += [list(s) for s in mapping.COL_GUARD_MORE.get(product, ())]
    blocked = [s for s in REAL_SHAPES[product] if list(s) not in allowed]
    if not blocked:
        # `etf_kjzs` 是唯一「两套真实形状都登记为合法」的产品 → 没有「其它真实形状」可挡，
        # 它的假形状拦截由 test_guard_more_allows_the_second_registered_shape 负责。
        assert product in mapping.COL_GUARD_MORE, ("该产品只有一套真实形状，不需要守卫", product)
        return
    for shape in blocked:
        out = _apply(product, shape, [[None] * len(shape)])
        assert out.cols == [], (product, len(shape), out.cols)
        assert out.provenance.extra["mapping_skipped"] == "cols-guard", (product, len(shape), out.meta)
        assert len(out.rows[0]["raw"]) == len(shape), (product, len(shape))


def test_guard_more_allows_the_second_registered_shape():
    """`etf_kjzs` 两套形状**都要**能映射（4 列：`N005`；15 列：`N007`）。"""
    assert "etf_kjzs" in mapping.COL_GUARD_MORE
    shapes = mapping.COL_GUARD_MORE["etf_kjzs"]
    assert len(shapes) == 1 and list(shapes[0]) in REAL_SHAPES["etf_kjzs"]
    # 登记形状之外再塞一个假形状 → 必须挡
    bogus = ["N001", "N002", "SC", "N005", "N006"]
    out = _apply("etf_kjzs", bogus, [["x", "y", "0", "1", "2"]])
    assert out.cols == [] and out.provenance.extra["mapping_skipped"] == "cols-guard", out.meta


def test_agf10_jj_mapping_after_m3_and_guard():
    """`agf10_jj` 的守卫在（11 列），且 M3 轮把 `N009` 改成了基金规模（列义已变）。"""
    assert mapping.FIELD_MAP["agf10_jj"] == {
        "N001": "nav_date", "N002": "code", "N003": "market", "N004": "name",
        "N005": "benchmark_name", "N008": "holding_weight_pct", "N009": "scale_yuan",
    }
    assert list(mapping.COL_GUARD["agf10_jj"]) == ["N%03d" % i for i in range(1, 12)]


def test_expected_noise_templates_are_documented():
    """`cwfxxq` / `jyfx` 的 `template_check` **预期**报 mismatch（映射目标 ≠ primary 那张 RS）。

    Lead 裁决（2026-10-27）：这两条**不做**模板校准 —— `_TEMPLATE_COLS` 的语义是
    「映射目标那张 RS 的列清单」，而语料里的 `primary` 是「最大那张 RS」，本来就不是同一张表。
    这里把「故意保留」钉成可执行断言，防止以后有人"顺手修好"。
    """
    # primary（= 协议层按单元格数挑出来的最大 RS）在语料里的真实列数
    primary_lens = {"cwfxxq": [29], "jyfx": [3, 2, 5]}
    assert len(mapping._TEMPLATE_COLS["cwfxxq"]) == 7       # RS3 变动说明
    assert len(mapping._TEMPLATE_COLS["jyfx"]) == 4         # RS0（00101 分支）
    assert len(mapping._TEMPLATE_COLS["cwfxxq"]) not in primary_lens["cwfxxq"]
    assert len(mapping._TEMPLATE_COLS["jyfx"]) not in primary_lens["jyfx"]
    # 两个守卫都在（守卫清单 = 映射目标那张 RS 的列清单，与模板一致）
    assert list(mapping.COL_GUARD["cwfxxq"]) == list(mapping._TEMPLATE_COLS["cwfxxq"])
    assert list(mapping.COL_GUARD["jyfx"]) == list(mapping._TEMPLATE_COLS["jyfx"])


# ══════════════════════════════════════════════════════════════════════════
# 四、P0 修正的正/反向断言（这些列以前是错的，必须有反向护栏）
# ══════════════════════════════════════════════════════════════════════════
def test_zsjj_pb_and_percentiles_are_not_shifted():
    """`zsjj`：`N008=PB`、`N009=PB 百分位`、`N007=PE 百分位(%)`（旧映射整体错位一位）。

    出处: M1-fund.md §2.2（msgid 762 @2026-10-22 真实回包）+ 与 `gzzc_zsgz` 逐值对齐表。
    反向断言：PB 必须在 0.5~11 量级（市净率），百分位必须在 0~100 —— 互换就会越界。
    """
    cols = REAL_SHAPES["zsjj"][0]
    out = _apply("zsjj", cols, [[
        "000690", "科创成长", "2026-10-22", "1", "低估", "51.1224", "0.1429",
        "9.3390", "54.8571", "0.2000", "8.1800", "-1.09", "-26.74", "-21.05",
        "-15.12", "7.54", "-1.05", "83.99", "109.44", "29.90"]])
    r = _assert_shape(out, cols, ["code", "name", "date", "market",
                                  "valuation_text", "pe", "pe_percentile_pct",
                                  "pb", "pb_percentile_pct",
                                  "dividend_yield_pct", "roe_pct"])
    assert r["code"] == "000690"
    assert r["market"] == 1                    # 000xxx → 1（94/94）
    assert r["pe"] == 51.1224
    assert r["pe_percentile_pct"] == 0.1429    # 0~100 口径（0.14%），不是 0~1 分位
    assert r["pb"] == 9.339
    assert r["pb_percentile_pct"] == 54.8571
    assert r["dividend_yield_pct"] == 0.2
    assert r["roe_pct"] == 8.18
    # 反向护栏：两列的值域互换了就会越界
    assert 0.5 <= r["pb"] <= 11.0, r["pb"]
    assert 0.0 <= r["pb_percentile_pct"] <= 100.0, r["pb_percentile_pct"]
    assert 0.0 <= r["pe_percentile_pct"] <= 100.0, r["pe_percentile_pct"]
    # 旧错映射的两个特征（N009 当 pb / N007 当 0~1 分位）都必须已不成立
    assert r["pb"] != 54.8571 and r["pe_percentile_pct"] != 0.1652
    # N012…N020 的周期涨跌幅仍未破解 → 留 raw
    _assert_unmapped(out, *["N%03d" % i for i in range(12, 21)])


def test_zsjj_cross_table_identity_pb_vs_pe_times_roe():
    """`zsjj` 的 PB ≈ PE × ROE（`PB/PE = ROE`），M1 §2.2 中位相对差 5.1%（n=94）。

    用「中证银行」那一行（它在 `zsjj` 与 `gzzc_zsgz` 都出现过，见 M1 §2.2 逐值对齐表）。
    """
    cols = REAL_SHAPES["zsjj"][0]
    row = ["399986", "中证银行", "2026-10-22", "0", "高估", "7.6939", "99.8348",
           "0.6438", "96.2841", "2.04", "8.75"] + [None] * 9
    r = _assert_shape(_apply("zsjj", cols, [row]), cols,
                      ["code", "name", "date", "market", "valuation_text", "pe",
                       "pe_percentile_pct", "pb", "pb_percentile_pct",
                       "dividend_yield_pct", "roe_pct"])
    assert r["market"] == 0                                    # 399xxx → 0
    assert r["pb"] < 1.0                                       # 银行股 PB ≈ 0.64
    implied = r["pe"] * r["roe_pct"] / 100.0
    assert abs(implied - r["pb"]) / r["pb"] < 0.15, (implied, r["pb"])


def test_czsz_mom_identity_and_not_yoy():
    """`czsz`：第 2 个值是**环比**增减额（`N002(t) − N003(t) == N002(t−1)`）。

    出处: M2-macro.md §2.1（msgid 967）「N003 = N002(t)−N002(t−1) 17/17；当同比用 0/10」。
    row0/row1 都是真实回包 → 闭环在同一份数据里可验。
    """
    cols = ["N%03d" % i for i in range(1, 14)]
    out = _apply("czsz", cols, [
        ["202608", "1293700000000.00", "-971200000000.00", "4.68", "01",
         "626100000000.00", "-535600000000.00", "9.27", "02",
         "667600000000.00", "-435600000000.00", "0.71", "03"],
        ["202607", "2264900000000.00", "206700000000.00", "11.72", "01",
         "1161700000000.00", "322700000000.00", "16.77", "02",
         "1103200000000.00", "-116000000000.00", "6.86", "03"],
    ])
    r, prev = out.rows[0], out.rows[1]
    # 字段名必须是 mom（**不是** yoy）——旧名会把下游引到 −42.88% 的假同比
    assert "national_mom_chg_yuan" in out.cols and "national_yoy_chg_yuan" not in out.cols
    assert "central_mom_chg_yuan" in out.cols and "local_mom_chg_yuan" in out.cols
    # 三数闭环：本期 − 环比增减 == 上一行值（真实数据 17/17）
    assert (r["national_value_yuan"] - r["national_mom_chg_yuan"]
            == prev["national_value_yuan"])
    assert (r["central_value_yuan"] - r["central_mom_chg_yuan"]
            == prev["central_value_yuan"])
    assert (r["local_value_yuan"] - r["local_mom_chg_yuan"]
            == prev["local_value_yuan"])
    # 分组恒等式：中央 + 地方 = 全国（值 & 环比增减额两组）
    assert r["central_value_yuan"] + r["local_value_yuan"] == r["national_value_yuan"]
    assert (r["central_mom_chg_yuan"] + r["local_mom_chg_yuan"]
            == r["national_mom_chg_yuan"])
    # 反向护栏：把 mom 当 yoy 会算出 −42.88%，而真同比是 +4.68%
    fake_yoy = (r["national_value_yuan"]
                / (r["national_value_yuan"] - r["national_mom_chg_yuan"]) - 1) * 100
    assert fake_yoy < -40 and abs(fake_yoy - r["national_yoy_pct"]) > 40


def test_hg_gdp_real_yoy_pct_is_not_nominal():
    """`hg_gdp N005` = **不变价（实际）增速**，字段名 `real_yoy_pct`；名义增速是 3.99%。

    出处: M2-macro.md §2.2「名义 3.99% vs N005 5.00；2022 符号还相反」。
    """
    cols = mapping.COL_GUARD["hg_gdp"]
    out = _apply("hg_gdp", cols, [
        ["2025-12-31", "元", "140187920000000.00", "5381300000000.00", "5.00"],
        ["2024-12-31", "元", "134806620000000.00", "5379450000000.00", "5.00"],
        ["2023-12-31", "元", "129427170000000.00", "6024230000000.00", "5.40"],
    ])
    r, prev = out.rows[0], out.rows[1]
    assert "real_yoy_pct" in out.cols and "yoy_pct" not in out.cols
    assert r["real_yoy_pct"] == 5.00
    # 现价增量恒等式（19/19）
    assert r["gdp_yuan"] - r["yoy_chg_yuan"] == prev["gdp_yuan"]
    # 反向护栏：名义增速 ≠ 实际增速（若字段名写 yoy_pct 就会被当成名义）
    nominal = (r["gdp_yuan"] / prev["gdp_yuan"] - 1) * 100
    assert nominal == pytest.approx(3.9919, abs=1e-3)
    assert abs(nominal - r["real_yoy_pct"]) > 0.5


def test_world_cal_is_trading_day_is_not_a_constant():
    """`world_cal N002` 有真实分布 {1: 244, 0: 122}（104 周末 + 18 法定节假日）。

    出处: M2-macro.md §2.3「N002 取值分布 = {'1': 244, '0': 122}；N004 才是恒 1」
    → 断言**不允许**假设常量：同一列必须能出 1 也能出 0。
    """
    cols = REAL_SHAPES["world_cal"][0]
    out = _apply("world_cal", cols, [
        ["2027-04-20", "1", "星期二", "1"],
        ["2026-05-01", "0", "星期五", "1"],     # 劳动节：工作日但休市
        ["2026-05-02", "0", "星期六", "1"],     # 周末
    ])
    assert out.cols == ["date", "is_trading_day", "weekday_text"]
    days = [row["is_trading_day"] for row in out.rows]
    assert days == [1, 0, 0], days                          # 不是常量！
    assert all(isinstance(d, int) for d in days)
    assert out.rows[0]["weekday_text"] == "星期二"
    assert out.rows[1]["weekday_text"] == "星期五" and out.rows[1]["is_trading_day"] == 0
    assert out.rows[2]["weekday_text"] == "星期六" and out.rows[2]["is_trading_day"] == 0
    _assert_unmapped(out, "N004")                           # 恒 1 的状态位，含义待定 → raw


def test_holiday_remark_is_mapped():
    """`holiday N006` 不是占位列：189 行里 20 行有备注（M2-macro.md §2.4）。"""
    cols = REAL_SHAPES["holiday"][0]
    out = _apply("holiday", cols, [
        ["2026-12-31", "星期四", "中国香港", "新年前夕", "港交所休市半日",
         "港交所休市交易主要包括股票、期货和指数期权"],
        ["2026-11-01", "星期日", "美国", "冬令时开始", None,
         "美国金融市场的交易恢复到原时区的时间"],
    ])
    r = _assert_shape(out, cols, ["date", "weekday_text", "country_name",
                                  "holiday_name", "closure_note", "remark"])
    assert r["remark"].startswith("港交所休市交易主要包括")
    assert out.rows[1]["closure_note"] is None              # N005 可以空
    assert out.rows[1]["remark"].startswith("美国金融市场")  # N006 仍然有值
    assert "N006" in mapping.FIELD_MAP["holiday"]


# ══════════════════════════════════════════════════════════════════════════
# 五、P1 新增映射的真实样本
# ══════════════════════════════════════════════════════════════════════════
def test_etf_kjzs_4col_share_vol_not_scale():
    """`etf_kjzs` 4 列形态：`N005` = **最新份额（份）**，不是规模（M1 §2.1）。

    判据：①280/280 都是 100 的整数倍（金额不会有这个性质）；②前端「规模」是 `现价×份额` 现算。
    """
    cols = REAL_SHAPES["etf_kjzs"][0]
    assert cols == ["N001", "N002", "SC", "N005"]
    out = _apply("etf_kjzs", cols, [["科创创业50ETF鹏华", "159012", "0", "164881100.00"]])
    r = _assert_shape(out, cols, ["name", "code", "market", "share_vol"])
    assert r["share_vol"] == 164881100.0
    assert r["share_vol"] % 100 == 0                       # 份数量化到 100 份
    assert "scale_yuan" not in out.cols                    # 严禁把份额当金额
    assert r["raw"]["N005"] == "164881100.00"


def test_etf_kjzs_15col_shape_shares_nav_and_periods():
    """`etf_kjzs` 15 列形态：`N007` 与 4 列的 `N005` **同值**（都是份额），`N006`=净值。

    出处: M1-fund.md §2.1 + §2.9 前端绑定 `jyz…ssl → N009…N018`。
    """
    cols = mapping.COL_GUARD_MORE["etf_kjzs"][0]
    assert cols in REAL_SHAPES["etf_kjzs"]
    out = _apply("etf_kjzs", cols, [[
        "科创创业50ETF鹏华", "159012", "0", "0.6563", "164881100.00",
        "-5.69", "-23.19", "-30.64", None, "-32.37", None, None, None, None, "-32.37"]])
    r = _assert_shape(out, cols, ["name", "code", "market", "share_vol", "nav",
                                  "return_1w_pct", "return_1m_pct", "return_3m_pct",
                                  "return_6m_pct", "return_ytd_pct", "return_1y_pct",
                                  "return_2y_pct", "return_3y_pct", "return_5y_pct",
                                  "return_since_listing_pct"])
    assert r["share_vol"] == 164881100.0                   # 与 4 列 N005 同值
    assert r["nav"] == 0.6563
    assert r["return_1w_pct"] == -5.69
    assert r["return_3m_pct"] == -30.64
    assert r["return_6m_pct"] is None                       # 上市不足半年
    assert r["return_since_listing_pct"] == -32.37
    # 数据自洽（M1 §2.9）：上市不足一年 ⇒「今年来 == 上市来」
    assert r["return_ytd_pct"] == r["return_since_listing_pct"]
    # 份额 × 净值 = 规模，量级合理
    assert 1e7 < r["share_vol"] * r["nav"] < 1e9


def test_jjph_multi_periods_and_nav():
    """`jjph`：`N004`=单位净值、`N006…N015`=10 个多周期（前端绑定），`N005` 未破解。"""
    cols = REAL_SHAPES["jjph"][0]
    out = _apply("jjph", cols, [[
        "1", "508058", "厦门安居", "2026-06-30 00:00:00",
        "2.8130", "17.22", "17.22", "17.22", "17.22", "18.65", "17.22", "18.65",
        "21.44", "23.93", None, "26.44", "2006585850.59", "33"]])
    r = _assert_shape(out, cols, ["rank", "code", "name", "report_period", "nav",
                                  "return_1w_pct", "return_1m_pct", "return_3m_pct",
                                  "return_6m_pct", "return_ytd_pct", "return_1y_pct",
                                  "return_2y_pct", "return_3y_pct", "return_5y_pct",
                                  "return_since_inception_pct", "scale_yuan"])
    assert r["rank"] == 1
    assert r["nav"] == 2.813                                # 单位净值（REIT）
    assert r["return_since_inception_pct"] == 26.44          # 成立来
    assert r["return_5y_pct"] is None                        # 成立不足 5 年
    assert r["scale_yuan"] == 2006585850.59                  # 带分 ⇒ 真金额
    _assert_unmapped(out, "N005", "zqsc")                    # 未破解 / 码空间未标定


def test_zcjj_real_18col_mapping_and_price_identity():
    """`zcjj` 18 列真实回包（msgid 570/571）：`N004/N006 = 29.79 元/股` 是锚点。

    出处: M1-fund.md §2.4（20 只的价量互证全部落在真实价位）。
    """
    cols = REAL_SHAPES["zcjj"][0]
    out = _apply("zcjj", cols, [[
        "601899", "紫金矿业", "1", "2026-09-30", "2343519660.42", "-33688492008.60",
        "78667998", "-1354586245", "16", "减仓", "48/48", "连续48季度",
        "-3.57", "-11.37", "-12.40", "-19.84", "-2.83", "-17.32"]])
    r = _assert_shape(out, cols, ["code", "name", "market", "report_period",
                                  "market_value_yuan", "hold_vol", "value_chg_yuan",
                                  "change_type"])
    assert r["code"] == "601899"
    assert r["name"] == "紫金矿业"
    assert r["market"] == 1
    assert r["report_period"] == "2026-09-30"
    assert r["market_value_yuan"] == 2343519660.42
    assert r["hold_vol"] == 78667998
    assert r["value_chg_yuan"] == -1354586245
    assert r["change_type"] == "减仓"
    # 价量恒等式（文档的硬证据）：持股市值 / 持股数 = 29.79 元/股
    assert r["market_value_yuan"] / r["hold_vol"] == pytest.approx(29.79, abs=0.05)
    # 符号互证：市值变动为负 ↔ 标签是「减仓」
    assert (r["value_chg_yuan"] < 0) == (r["change_type"] == "减仓")
    # 未破解 / 展示串 / 周期列 → 全部留 raw
    _assert_unmapped(out, "N005", "N008", "N010", "N011",
                     *["N%03d" % i for i in range(12, 18)])


def test_zsgz_21col_valuation_and_pb():
    """`zsgz` 21 列形态：`N003=market`、`N007=PB`、`N008=PB 百分位%`（M1 §2.3）。"""
    cols = mapping.COL_GUARD["zsgz"]
    assert cols in REAL_SHAPES["zsgz"]
    out = _apply("zsgz", cols, [[
        "399986", "中证银行", "0", "高估", "7.5096", "98.3485", "0.6284", "92.5681",
        "2.0900", "8.7500", "45", "0",
        "-1.60", "1.58", "5.38", "2.88", "-2.03", "10.97", "37.81", "14.81", "-0.32"]])
    r = _assert_shape(out, cols, ["code", "name", "market", "valuation_text", "pe",
                                  "pe_percentile_pct", "pb", "pb_percentile_pct",
                                  "dividend_yield_pct", "roe_pct",
                                  "tracking_fund_count"])
    assert r["market"] == 0
    assert r["pb"] == 0.6284 and r["pb"] < 1.0              # 银行股 PB 量级
    assert r["pb_percentile_pct"] == 92.5681
    assert r["tracking_fund_count"] == 45
    assert 0.0 <= r["pe_percentile_pct"] <= 100.0
    _assert_unmapped(out, "zqsc", *["N%03d" % i for i in range(12, 21)])


def test_zsgz_14col_etf_list_is_blocked():
    """`zsgz` 的 14 列形态是「跟踪 ETF 列表」，语义完全不同 → 必须一列都不映射。"""
    shape = [s for s in REAL_SHAPES["zsgz"]
             if list(s) != list(mapping.COL_GUARD["zsgz"])][0]
    assert len(shape) == 14
    out = _apply("zsgz", shape, [[
        "5", "588020", "科创50E", "1", "2026-10-22 00:00:00", "-4.91", "-28.62",
        "-23.56", "-19.42", "-3.20", "4.55", "78.81", "1771963974.00", "22.79"]])
    assert out.cols == [], out.cols
    assert out.provenance.extra["mapping_skipped"] == "cols-guard"
    assert out.rows[0]["raw"]["N001"] == "5"               # 真实语义：跟踪基金数
    assert out.rows[0]["raw"]["N002"] == "588020"           # 基金代码


# ══════════════════════════════════════════════════════════════════════════
# 六、其余产品的真实样本
# ══════════════════════════════════════════════════════════════════════════
def test_etf_t0_sample():
    """T+0 ETF（7 列；名称在前、代码在后）。出处: 数据中心剩余接口.md §4 + 语料。"""
    cols = REAL_SHAPES["etf_t0"][0]
    out = _apply("etf_t0", cols, [
        ["科创债ETF万家", "159110", "0", "10020101", "2.88", "-0.3085", "2430354000.00"]])
    r = _assert_shape(out, cols, ["name", "code", "market", "category_code",
                                  "price", "change_pct", "amount_yuan"])
    assert (r["name"], r["code"], r["market"]) == ("科创债ETF万家", "159110", 0)
    assert r["category_code"] == "10020101"
    assert r["price"] == 2.88
    assert r["change_pct"] == -0.3085
    assert r["amount_yuan"] == 2430354000.0


def test_etf_sx_jjgs_sample_without_corpus():
    """ETF 筛选·基金公司（3 列）：**语料 0 帧**，形状只能来自文档采样（M1 §2.8）。"""
    assert "etf_sx_jjgs" in NO_CORPUS
    cols = mapping._TEMPLATE_COLS["etf_sx_jjgs"]
    out = _apply("etf_sx_jjgs", cols, [["9900059554", "安联基金", "A"]])
    r = _assert_shape(out, cols, ["fund_company_id", "name"])
    assert r["fund_company_id"] == "9900059554"
    assert r["name"] == "安联基金"
    _assert_unmapped(out, "N003")


def test_jjpj_sample():
    """基金评级（9 列，真实回包 msgid 555）。"""
    cols = REAL_SHAPES["jjpj"][0]
    out = _apply("jjpj", cols, [[
        "1", "004320", "前海沪港深乐享", "4", "五星", "五星", "五星", "五星", "33"]])
    r = _assert_shape(out, cols, ["rank", "code", "name", "rating_agency_count",
                                  "rating_star_1", "rating_star_2",
                                  "rating_star_3", "rating_star_4"])
    assert r["rating_agency_count"] == 4
    assert [r["rating_star_%d" % i] for i in range(1, 5)] == ["五星"] * 4
    _assert_unmapped(out, "zqsc")


def test_jjhq_sample_without_corpus():
    """基金市场行情（6 列）：**语料 0 帧**（M1 §2.8），`N005` 仍是 `?` → 留 raw。"""
    assert "jjhq" in NO_CORPUS
    cols = mapping._TEMPLATE_COLS["jjhq"]
    out = _apply("jjhq", cols, [
        ["158008", "储能电池ETF华夏", "0", "980027", "102", "237413100"]])
    r = _assert_shape(out, cols, ["code", "name", "market", "category_code",
                                  "amount_yuan"])
    assert (r["code"], r["market"], r["amount_yuan"]) == ("158008", 0, 237413100.0)
    _assert_unmapped(out, "N005")


def test_agf10_jj_n009_is_fund_scale_not_holding_value():
    """`agf10_jj N009` 是**该基金的规模**，不是持该股的市值（2026-10-27 M3 抓包轮改错）。

    语料铁证（同一份 pcap、同一票 600303）：`N009 × N008/100 = 00702.N005`（同批汇总的合计持仓市值）。
    59059572.00 × 0.0002 = 11811.91（万元）⇒ `N009` 只能是基金级总量。
    反向断言：`scale_yuan` 必须远大于同批 `jj_cgtj` 的持仓市值（55358 元）——旧映射把它当持仓市值时，
    数值上看起来"也合理"（5906 万符合基金持仓量级），这正是当初没抓住它的原因。
    """
    cols = mapping.COL_GUARD["agf10_jj"]
    out = _apply("agf10_jj", cols, [[
        "20260714", "563600", "1", "A500增强", "中证A500", "-740.37", "1477.68",
        "0.02", "59059572.00", "-3207900.00", "6492300.00"]])
    r = _assert_shape(out, cols, ["nav_date", "code", "market", "name",
                                  "benchmark_name", "holding_weight_pct",
                                  "scale_yuan"])
    assert r["nav_date"] == "20260714"                     # 基金净值日（≠ 报告期 2026-09-30）
    assert r["holding_weight_pct"] == 0.02                 # 占基金净值比 %（0.02%）
    assert r["scale_yuan"] == 59059572.0                   # 基金规模
    # 恒等式：规模 × 占净值比 = 同批汇总的合计持仓市值（万元口径）
    assert r["scale_yuan"] * r["holding_weight_pct"] / 100 == pytest.approx(11811.91, abs=0.01)
    assert "market_value_yuan" not in out.cols             # 旧的错字段名必须消失
    # 推断/基金级字段全部留 raw
    _assert_unmapped(out, "N006", "N007", "N010", "N011")
    assert r["raw"]["N011"] == "6492300.00"
    assert "N011" in mapping._TEMPLATE_COLS["agf10_jj"]      # 模板是列清单，≠ 映射


def test_jj_cgtj_n003_market_hypothesis_is_withdrawn():
    """`jj_cgtj N003` 不再映射（旧文档说「市场=1」，但同票随时间 1→4 ⇒ 证伪）。

    依据（2026-10-27 M3）：抓包里 600303 的 N003=1，成员实网复取同一只票是 4 ——
    同一只票的市场码不可能随时间变。候选语义 = 持股基金家数（与 `gbgd` 00204.N002 逐票同值：
    600303=4、600519=1145），但抓包无法复核 → 按「宁可少映射」留 raw。
    """
    assert "N003" not in mapping.FIELD_MAP["jj_cgtj"]
    assert "market" not in mapping.FIELD_MAP["jj_cgtj"].values()
    cols = REAL_SHAPES["jj_cgtj"][0]
    out = _apply("jj_cgtj", cols, [[
        "2026-09-30", "2026年三季度", "1", "17800", "17800", "0.0026", "55358.00",
        "55358.00", None, None, "新进", "曙光股份", "600303", "1"]])
    r = _assert_shape(out, cols, ["report_date", "report_period", "hold_vol",
                                  "float_share_pct", "market_value_yuan",
                                  "change_type", "name", "code"])
    assert r["raw"]["N003"] == "1"                          # 原值仍在 raw
    assert r["raw"]["N014"] == "1"                          # 与 N003 同值，两者都不映射
    _assert_unmapped(out, "N003", "N005", "N008", "N009", "N010", "N014")
    # 文档的硬校验仍在（与 N003 无关）：17800 × 3.11 ≈ 55358
    assert r["hold_vol"] * 3.11 == pytest.approx(r["market_value_yuan"], abs=1e-6)


def test_rows_shorter_than_cols_are_missing_values_not_misaligned():
    """机制事实：列名比行值宽时，`Table` 把值放在**前 N 列**、尾列补 `None` —— 不会错位。

    为什么钉这条：M3 报告里 `hypm.002xx`（12 列/5 值）等案例被描述成「套 FIELD_MAP 必错位」。
    实测（本用例）证明性质是**缺值**而不是**错位**：`Table.__post_init__` 用
    `Row(cols, values)` 逐列填，`i >= len(values)` 的列就是 `None`；`_row_width` 返回的是
    Row 的键数（= 列数），所以 `_resolve_cols` 照旧走 `server` 分支，值不会串到别的列名上。
    ⇒ 这类回包的处置是「按 guard 挡住 / 缺值即 None」，注释里不能写成「错位」。
    """
    cols12 = ["N%03d" % i for i in range(1, 13)]
    t = Table(cols=cols12, rows=[["A", "B", "C", "D", "E"]], cmd=mapping.__name__)
    row = dict(t.rows[0])
    assert [row[c] for c in cols12[:5]] == ["A", "B", "C", "D", "E"]   # 值在前 5 列
    assert [row[c] for c in cols12[5:]] == [None] * 7                  # 尾 7 列是 None
    assert mapping._row_width(t) == 12                                 # 宽度按列数算
    assert mapping._resolve_cols(t, "hypm")[1] == "server"
    # 有守卫的产品：12 列 ≠ 登记的 4 列 → 整体挡住（缺值也不套映射）
    assert mapping.apply(t, "hypm").provenance.extra["mapping_skipped"] == "cols-guard"
    # 无守卫产品：值只落在**前面对应的列名**上，不会错位
    t2 = Table(cols=["zqjc", "zqdm", "N001", "N002", "N003"],
               rows=[["曙光股份", "600303", "2026-06-30"]])
    r2 = dict(mapping.apply(t2, "tjcj").rows[0])
    assert r2["name"] == "曙光股份" and r2["report_period"] == "2026-06-30"
    assert r2["raw"]["N002"] is None and r2["raw"]["N003"] is None



# ══════════════════════════════════════════════════════════════════════════
# 七、抓包挖掘第二轮（2026-10-28，改派 M2 执行）：5 个产品 / 42 列
#     证据：`eval3/corpus/full.jsonl`（31 份 pcap，2026-10-20~27）
#     每组都配**反向断言**：恒等式要么成立、要么这条测试红。
# ══════════════════════════════════════════════════════════════════════════
#: 本轮新增的 5 个**有映射**的产品键（闭集）。
NEW_PRODUCTS_ROUND2 = ("hq_nlp_zjlx", "lrmx", "north", "zdsl", "zdzq")

#: 本轮新增的 6 条 `COL_GUARD`（`lrrb` 只登记守卫、没有映射，见 E 段说明）。
ROUND2_GUARDED = NEW_PRODUCTS_ROUND2 + ("lrrb",)

EXPECTED_CMD_ROUND2 = {
    "hq_nlp_zjlx": "HQServ.hq_nlp_zjlx",
    "lrmx": "CWServ.ph_zt_lrzt_lrmx",
    "north": "CWServ.ph_tdxdatacenter_hsgt",
    "zdsl": "CWServ.ph_bigdata_xtld_zdsl",
    "zdzq": "CWServ.ph_bigdata_xtld_zdzq",
    "lrrb": "CWServ.ph_zt_lrzt_lrrb",
}

# ── 真实样本行（逐字节抄自 full.jsonl，不造数）────────────────────────────
#: `north` 9 列（`lszjlx3`）：4 个交易日
NORTH_ROWS = [
    ["2026-10-22", "232622000000.00", "113327000000.00", "119295000000.00",
     "12730059.00", "6102935.00", "6627124.00", "3658.03", "2026-10-22"],
    ["2026-10-21", "222082000000.00", "102488000000.00", "119594000000.00",
     "11707265.00", "5578396.00", "6128869.00", "3713.03", "2026-10-21"],
    ["2026-10-16", "225272000000.00", "114865000000.00", "110407000000.00",
     "13232192.00", "6499420.00", "6732772.00", "3716.28", "2026-10-16"],
    ["2026-10-15", "259797000000.00", "132799000000.00", "126998000000.00",
     "13613379.00", "6856595.00", "6756784.00", "3707.48", "2026-10-15"],
]

#: `lrmx` 9 列（`p1=00102`）：两个交易日
LRMX_ROWS = [
    ["2488857015317.0000", "-14334701057.0000", "-0.57265694",
     "2459705595593.0000", "-14489992176.0000", "-0.58564457",
     "29151419724.0000", "155291119.0000", "0.53555811"],
    ["2503191716374.0000", "-31899805742.0000", "-1.25832955",
     "2474195587769.0000", "-31359688042.0000", "-1.25160631",
     "28996128605.0000", "-540117700.0000", "-1.82866060"],
]

#: `zdsl` 8 列：5 只票（含涨停收在最高价的 301313）
ZDSL_ROWS = [
    ["凡拓数创", "301313", "20261020", "43.230", "46.800", "41.890", "46.800", "0"],
    ["凡拓数创", "301313", "20261019", "31.560", "39.000", "30.850", "39.000", "0"],
    ["润建股份", "002929", "20261020", "46.400", "51.040", "45.430", "51.040", "0"],
    ["吉大正元", "003029", "20261020", "26.610", "26.610", "24.890", "26.610", "0"],
    ["海洋王", "002724", "20261020", "8.780", "9.260", "8.680", "9.260", "0"],
    ["东安动力", "600178", "20261020", "12.410", "12.410", "12.410", "12.410", "0"],
]

#: `zdzq` 8 列
ZDZQ_ROWS = [
    ["财富趋势", "688318", "20261020", "74.000", "75.100", "71.700", "72.790", "0"],
    ["财富趋势", "688318", "20261019", "73.660", "75.980", "73.200", "74.370", "0"],
]

#: `hq_nlp_zjlx` 17 列：688825（正）与 002354（负）
ZJLX_ROWS = [
    ["688825", "长鑫科技", "1", "42.8500", "7.9345", "7.9345", "10.6741",
     "2299742208.0000", "11.6160", "1.1919", "1654721024.0000", "8.3580",
     "0.8576", "0.0575", "270181760.0000", "0.2498", "-0.0233"],
    ["002354", "天娱数科", "0", "8.0300", "10.0000", "10.0000", "17.9299",
     "-1335644928.0000", "-57.8200", "-10.2233", "1258629504.0000", "54.4860",
     "9.6338", "0.0953", "2180948.0000", "0.0245", "0.0000"],
]


def test_round2_products_are_real_and_mapped():
    """5 个新键必须都落在真实 products.json 上，且都在 `PRODUCTS` 里。"""
    assert len(NEW_PRODUCTS_ROUND2) == 5 and len(set(NEW_PRODUCTS_ROUND2)) == 5
    products = _products()
    for product in NEW_PRODUCTS_ROUND2:
        assert product in mapping.FIELD_MAP and mapping.FIELD_MAP[product], product
        assert product in products, product
        assert product in mapping.PRODUCTS, product
        assert products[product]["cmd"] == EXPECTED_CMD_ROUND2[product], product


def test_round2_field_map_keys_are_all_real_columns():
    """映射键必须是该产品**真实形状里出现过**的列名（防手抄错列名 → 死映射）。"""
    for product in NEW_PRODUCTS_ROUND2:
        real = set()
        for shape in REAL_SHAPES[product]:
            real |= set(shape)
        bogus = set(mapping.FIELD_MAP[product]) - real
        assert not bogus, (product, bogus)


@pytest.mark.parametrize("product", ROUND2_GUARDED)
def test_round2_guard_list_is_a_real_shape(product):
    """守卫清单必须**逐位等于**某套真实形状，且装得下全部映射键。"""
    guard = mapping.COL_GUARD.get(product)
    assert guard is not None, product
    assert list(guard) in REAL_SHAPES[product], (product, list(guard), REAL_SHAPES[product])
    allowed = set(guard)
    for shape in mapping.COL_GUARD_MORE.get(product, ()):
        assert list(shape) in REAL_SHAPES[product], (product, "COL_GUARD_MORE 不是真实形状")
        allowed |= set(shape)
    if mapping.FIELD_MAP.get(product):
        assert set(mapping.FIELD_MAP[product]) <= allowed, product


@pytest.mark.parametrize("product", NEW_PRODUCTS_ROUND2)
def test_round2_wrong_shape_is_blocked(product):
    """每个守卫至少一条「错形状被挡下」：`cols == []` + `mapping_skipped == "cols-guard"`。

    优先用**另一套真实形状**；只有单形状产品才退化成「真形状砍掉末列」的合成错形状。

    ⚠️ 这里只参数化**有映射**的 5 个：`lrrb` 本轮只登记守卫、没有 `FIELD_MAP`，
    而 `apply()` 只在有映射时才读 `COL_GUARD` → 它的守卫当前不生效，
    「被挡」这条不成立（由 `test_lrrb_guard_is_preregistered_only` 单独钉住）。
    """
    allowed = [list(mapping.COL_GUARD[product])]
    allowed += [list(s) for s in mapping.COL_GUARD_MORE.get(product, ())]
    others = [s for s in REAL_SHAPES[product] if list(s) not in allowed]
    if others:
        shape = list(others[0])
    else:
        shape = list(mapping.COL_GUARD[product])[:-1]       # 单形状产品：砍掉末列
    out = _apply(product, shape, [["1"] * len(shape)])
    assert out.cols == [], (product, len(shape), out.cols)
    assert out.provenance.extra["mapping_skipped"] == "cols-guard", (product, len(shape), out.meta)
    assert len(out.rows[0]["raw"]) == len(shape), (product, len(shape))


def test_north_total_equals_sh_plus_sz():
    """`north` 两条 A 级加和恒等式（语料 210/210 × 2，本机独立复核）。

    `N002 = N003 + N004`、`N005 = N006 + N007` —— 只能证明「合计 = 沪深分项」，
    **证不出**是成交额还是持股市值，所以字段名一律中性（`_yuan` / `_alt`）。
    """
    cols = REAL_SHAPES["north"][1]
    out = _apply("north", cols, NORTH_ROWS)
    r0 = _assert_shape(
        out, cols,
        ["date", "hsgt_total_yuan", "hsgt_sh_yuan", "hsgt_sz_yuan",
         "hsgt_total_alt", "hsgt_sh_alt", "hsgt_sz_alt"])
    for r in out.rows:
        assert r["hsgt_total_yuan"] == pytest.approx(
            r["hsgt_sh_yuan"] + r["hsgt_sz_yuan"], rel=1e-12), r
        assert r["hsgt_total_alt"] == pytest.approx(
            r["hsgt_sh_alt"] + r["hsgt_sz_alt"], rel=1e-12), r
    # 具体到数值（只断言「非空」抓不到列错位）
    assert r0["date"] == "2026-10-22" and isinstance(r0["date"], str)
    assert r0["hsgt_total_yuan"] == pytest.approx(232622000000.0)
    assert r0["hsgt_sh_yuan"] == pytest.approx(113327000000.0)
    assert r0["hsgt_total_alt"] == pytest.approx(12730059.0)
    # N008（点位）/N009（与 N001 逐位相同的冗余日期）**必须**留 raw
    _assert_unmapped(out, "N008", "N009")
    # 两个口径差 5 个数量级 —— 这正是「不能用同一个后缀命名」的证据
    assert r0["hsgt_total_yuan"] / r0["hsgt_total_alt"] > 1e4


def test_lrmx_balance_identity():
    """`lrmx`（`p1=00102`）两式：`N001 = N004 + N007`、`N002 = N005 + N008`（23/23）。"""
    cols = REAL_SHAPES["lrmx"][1]                # 9 列那套
    out = _apply("lrmx", cols, LRMX_ROWS)
    r0 = _assert_shape(
        out, cols,
        ["margin_balance_yuan", "margin_balance_change_yuan", "margin_balance_change_pct",
         "financing_balance_yuan", "financing_balance_change_yuan", "financing_balance_change_pct",
         "securities_lending_balance_yuan", "securities_lending_balance_change_yuan",
         "securities_lending_balance_change_pct"])
    for r in out.rows:
        assert r["margin_balance_yuan"] == pytest.approx(
            r["financing_balance_yuan"] + r["securities_lending_balance_yuan"], rel=1e-12), r
        assert r["margin_balance_change_yuan"] == pytest.approx(
            r["financing_balance_change_yuan"]
            + r["securities_lending_balance_change_yuan"], rel=1e-9), r
    assert r0["margin_balance_yuan"] == pytest.approx(2488857015317.0)
    assert r0["securities_lending_balance_yuan"] == pytest.approx(29151419724.0)


def test_lrmx_default_params_do_not_hit_the_mapped_shape():
    """⚠️ 如实告知：`products.json` 的默认 `p1="00104"` 是**另一张 6 列表**。

    默认路径**拿不到**任何标准字段（`mapping_skipped == "cols-guard"`）——
    要用这张 9 列汇总表必须显式传 `p1="00102"`。把这条钉成断言，防止有人
    「以为默认就生效」。
    """
    default_p1 = _products()["lrmx"]["params"][0]
    assert default_p1 == "00104", default_p1
    shape_6 = ["N%03d" % i for i in range(1, 7)]
    assert shape_6 in REAL_SHAPES["lrmx"], REAL_SHAPES["lrmx"]
    assert list(mapping.COL_GUARD["lrmx"]) != shape_6
    out = _apply("lrmx", shape_6, [["1"] * 6])
    assert out.cols == [] and out.provenance.extra["mapping_skipped"] == "cols-guard", out.meta


def test_north_default_params_do_hit_the_mapped_shape():
    """与 `lrmx` 相反：`north` 的默认 `lszjlx3` 就是 9 列 → **默认生效**。"""
    assert _products()["north"]["params"][0] == "lszjlx3", _products()["north"]["params"]
    assert list(mapping.COL_GUARD["north"]) == REAL_SHAPES["north"][1]


@pytest.mark.parametrize("product,rows", [("zdsl", ZDSL_ROWS), ("zdzq", ZDZQ_ROWS)])
def test_zdsl_zdzq_ohlc_ordering(product, rows):
    """OHLC 结构不变量：`high >= max(open, close)` 且 `low <= min(open, close)`。

    语料全量复核：`zdsl` 1021/1021、`zdzq` 90/90 无违反。
    """
    cols = REAL_SHAPES[product][0]
    out = _apply(product, cols, rows)
    r0 = _assert_shape(out, cols,
                       ["name", "code", "date", "open", "high", "low", "close"])
    for r in out.rows:
        assert r["high"] >= max(r["open"], r["close"]) - 1e-9, r
        assert r["low"] <= min(r["open"], r["close"]) + 1e-9, r
    assert isinstance(r0["date"], str) and len(r0["date"]) == 8   # YYYYMMDD 不许被 float 吃掉
    _assert_unmapped(out, "TYPE")
    if product == "zdsl":
        # 凡拓数创 2026-10-20：涨停收在最高价 → 收盘 == 最高
        assert r0["close"] == 46.8 and r0["high"] == 46.8 and r0["low"] == 41.89


def test_hq_nlp_zjlx_amo_min2_is_not_amount():
    """★ 反向断言：`amo_min2` **不是成交额**（也不在标准字段里）。

    证据：由 `net_buy / rate_buy × 100` 反推的成交额 = 2,310,005,064 元
    （与 `lhzy` 同日同票的成交额 2,310,005,000 逐位吻合），而 `amo_min2` = 2,180,948
    → 相差 ≈1059 倍；换 688825 是 73 倍、603986 是 89 倍、板块 880961 是 103 倍
    —— **倍数不固定**，所以它既不是成交额、也不是成交额的固定缩放。语义未定 → 留 raw。
    """
    cols = REAL_SHAPES["hq_nlp_zjlx"][0]
    out = _apply("hq_nlp_zjlx", cols, ZJLX_ROWS)
    r0 = _assert_shape(
        out, cols,
        ["code", "name", "market", "price", "change_pct", "amplitude_pct",
         "turnover_rate_pct", "turnover_rate_day_pct", "net_buy_yuan", "net_buy_pct",
         "main_net_inflow_yuan", "main_net_inflow_pct"])
    # ① 标准字段里**不许**出现 amo_min2 或任何「成交额」语义的名字
    assert "amo_min2" not in out.cols
    assert not [c for c in out.cols if "amount" in c or "amo" in c], out.cols
    _assert_unmapped(out, "jb_buy", "jb_zl", "hy_zl", "amo_min2", "zs")
    # ② 真实值锚点（price / change_pct 取到的是对的列）
    row_by_code = {r["code"]: r for r in out.rows}
    assert row_by_code["688825"]["price"] == pytest.approx(42.85)
    assert row_by_code["688825"]["change_pct"] == pytest.approx(7.9345)
    assert row_by_code["002354"]["price"] == pytest.approx(8.03)
    assert row_by_code["002354"]["change_pct"] == pytest.approx(10.0)      # 涨停
    assert row_by_code["002354"]["market"] == 0                            # 深市
    assert row_by_code["688825"]["market"] == 1                            # 沪市
    # ③ 「amo_min2 不是成交额」的算式本身
    r = row_by_code["002354"]
    implied_turnover = abs(r["net_buy_yuan"] / r["net_buy_pct"] * 100)
    amo = float(r["raw"]["amo_min2"])
    assert implied_turnover == pytest.approx(2310005064.0, rel=1e-6)       # ≈ lhzy 的 2.31e9
    assert implied_turnover / amo > 100                                     # ≈1059 倍，不是 1


def test_hq_nlp_zjlx_sector_shape_is_allowed_but_stocklist_stays_raw():
    """18 列（`Market=1` 板块）**放行**，但 `stockList` 只进 raw、不成为标准字段。"""
    shape17 = list(mapping.COL_GUARD["hq_nlp_zjlx"])
    shape18 = shape17 + ["stockList"]
    assert shape18 in REAL_SHAPES["hq_nlp_zjlx"]
    assert list(mapping.COL_GUARD_MORE["hq_nlp_zjlx"][0]) == shape18
    row = ["880948", "人工智能", "1", "1671.1200", "3.0525", "3.0525", "2.8147",
           "14707441664.0000", "3.2623", "0.0008", "7269457920.0000", "1.6125",
           "0.0004", "0.0072", "4361566720.0000", "0.9342", "0.0509",
           [{"code": "300296", "name": "利亚德"}]]
    out = _apply("hq_nlp_zjlx", shape18, [row])
    assert out.provenance.extra.get("mapping_skipped") is None, out.meta
    _assert_shape(out, shape18,
                  ["code", "name", "market", "price", "change_pct", "amplitude_pct",
                   "turnover_rate_pct", "turnover_rate_day_pct", "net_buy_yuan",
                   "net_buy_pct", "main_net_inflow_yuan", "main_net_inflow_pct"])
    assert "stockList" not in out.cols
    assert isinstance(out.rows[0]["raw"]["stockList"], list)        # 嵌套 JSON 原样保留
    assert out.rows[0]["price"] == pytest.approx(1671.12)
    assert out.rows[0]["market"] == 1


def test_round2_standard_fields_are_unique_within_each_registered_shape():
    """同一形状内标准字段不得重名（与上面 NEW_PRODUCTS 的同名用例同口径）。"""
    for product in NEW_PRODUCTS_ROUND2:
        fmap = mapping.FIELD_MAP[product]
        shapes = [list(mapping.COL_GUARD[product])]
        shapes.extend(list(s) for s in mapping.COL_GUARD_MORE.get(product, ()))
        for shape in shapes:
            present = set(shape)
            stds = [s for col, s in fmap.items() if col in present]
            dup = {s for s in stds if stds.count(s) > 1}
            assert not dup, (product, dup)
            for std in stds:
                assert std == std.strip() and " " not in std, (product, std)
                assert not std.startswith("N0"), (product, std)


def test_lrrb_guard_is_preregistered_only():
    """`lrrb` 本轮**只登记守卫、没有映射** → 守卫当前不生效（如实记录，不假装）。

    `apply()` 只在 `FIELD_MAP[product]` 非空时才读 `COL_GUARD`；`lrrb` 没有映射，
    所以即使给一张 8 列表也不会被 `cols-guard` 挡（也一列都映射不出来）。
    等哪一轮把 §3.1 建议的 00101/00301 两套映射落下来，这条守卫才会真正开始挡。
    """
    assert "lrrb" in mapping.COL_GUARD
    assert not mapping.FIELD_MAP.get("lrrb")
    assert "lrrb" not in mapping.PRODUCTS
    shape8 = ["N%03d" % i for i in range(1, 9)]
    out = _apply("lrrb", shape8, [["1"] * 8])
    assert out.cols == []
    assert out.provenance.extra.get("mapping_skipped") is None      # 不是被挡，是压根没映射
    assert len(out.rows[0]["raw"]) == 8


def test_gscb_00301_calendar_sample():
    """财报·交易日历（gscb_sy 默认子码 00301，4 列）。"""
    cols = REAL_SHAPES["gscb"][0]
    out = _apply("gscb", cols, [["2026-10-21", "1", "星期三", "1"]])
    r = _assert_shape(out, cols, ["date", "weekday_text"])
    assert r["date"] == "2026-10-21" and r["weekday_text"] == "星期三"
    _assert_unmapped(out, "N002", "N004")
