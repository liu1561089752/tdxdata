# -*- coding: utf-8 -*-
"""宏观域 typed 层（PROD-003）：`c.macro.<主题>(mode=…)` → 标准字段 `Table`。

为什么需要这一层
----------------
宏观 29 个产品（`products.json` 里 `domain="宏观"`）原先只有 `c.get("hg_gdp")` 一条路，
回来的是 `N001/N002…` —— 叫不出名字的列等于没有列。这一层把**每张表、每个模式**
的列义钉在一张登记表里，调用方拿到的是 `gdp_yuan` / `real_yoy_pct` 这样的字段。

三个必须记住的横切结论（出处：`F:\\TDX\\reports\\eval3\\findings\\M2-macro.md`）
----------------------------------------------------------------------------
1. **「增减额」= 本期 − 上一行**。月度表里这叫环比（`*_mom_chg_*`），年度表里才等于
   同比。文档与旧映射在 `czsz`/`yhhb` 共 4 处把它标成「同比」，按同比用会算出 −42.88%
   而真实同比是 +4.68%（`czsz` N003 vs N004，逐位可验）。
2. **百分比列有三种互不等价的口径**：同比 %、环比 %（分母是**上期** `V−Δ`）、环比差。
   字段名里 `_yoy_pct` / `_mom_pct` / `_mom_diff_point` / `_ratio` 各指一种，别混。
3. **`N00x` 编号不是位置**。`jqzs` 002 的 ColDes 是 `N001,N003,N004,N005`（没有 N002）；
   `yhhb` 004 是 `…,N004,N006,N005`（同比/环比**反着命名**）。所以本模块一律按
   **ColDes 名**取列，且先做「运行时列清单 == 登记 layout」的逐位守卫。

模式（mode）为什么是一等公民
----------------------------
同一个产品换个 `params[0]` 就是**另一张表**：`czsz` 001 是 13 列月度横表、005 是 5 列年度、
007 是 16 列分项；`yhhb` 002 的 `N003` 是环比增减额、004 的 `N003` 是存量值。
所以 `mode` 不是可选项，是**形状标识**。每个 mode 都登记了自己的 `layout`（ColDes 顺序），
运行时对不上就**一列都不映射**（fail-closed），把原因写进 `provenance.extra["macro_guard"]`。

覆盖边界（诚实清单，不猜）
--------------------------
* **有语料实证、已 typed**：18 个产品 / 44 个模式（`AVAILABLE`）。
  每个模式的证据是「语料回包里逐行算得出来的算式」（见每个 `ModeSpec.evidence`），
  并由 `tests/fixtures/macro/*.bin`（**pcap 里抽出的真实回包字节**）回放钉住。
* **语料里没有任何回包 → 不暴露**：11 个产品（`UNAVAILABLE`，如 `dghg_gdp`/`macro_xf`/`xjphg`）。
  调 `fetch` 会抛 `NotSupportedError` 并说明「没有回包可证列义」，**绝不猜列名**。
* **有回包但列义未破解 → 当模式不暴露、或该列不进标准名**：例如 `jqzs` 004/005、
  `hg_tz` 003 的指标身份、`mghg_yh` 的 4 个存量值、`hg_gyjj` 001 的 N004。
  没定名的列仍逐行留在 `row["raw"]` 里，并在 `provenance.extra["macro_unmapped"]` 说明原因。

用法
----
```python
c = tdxdata.Client()
c.macro.gdp()                       # hg_gdp/001：5 列 × 20 期，字段 gdp_yuan / real_yoy_pct …
c.macro.gdp(mode="industries3")     # 三次产业：primary/secondary/tertiary_*
c.macro.fiscal(mode="monthly")      # 财政收支：national/central/local_*
c.macro.calendar(mode="events", day="2026-10-20")
c.macro.retail(mode="code_163001")  # ⚠️ 同名列在这里是「指标码」，在 total 里是「科目名」
c.macro.modes()                     # 登记表：每个产品每个模式的可信度与证据
```
每个返回的 `Table`：`cols` = 标准字段（不含 `raw`），每行 `{"字段": 值, …, "raw": {...}}`，
`raw` 里是服务端原值（一个都不丢）。单位同时写进 `provenance.extra["macro_units"]`
（`元`/`亿元`/`点`/`%` —— 注意 `gdp` 002 模式里增加值是元、增减额是亿元，差 1e8）。
"""
from __future__ import annotations

import json  # noqa: F401  （保留：本模块的登记表按 JSON 兼容结构对外，见 products_covered）
from dataclasses import dataclass, field
from datetime import date as _date

from .. import errors
from ..models import Table

__all__ = ["Macro", "AVAILABLE", "UNAVAILABLE", "UNVERIFIED", "products_covered"]


# ══════════════════════════════════════════════════════════════════════════
# 一、登记表：产品 → 模式 → 列义
# ══════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class ModeSpec:
    """一个「产品 + p1 子码」的形状登记。

    字段
    ----
    key       : 模式键（同一产品内唯一，typed 方法的 `mode=` 就是它）
    params    : 请求参数模板（与语料里抓到的逐字一致；`count_slot`/`day_slot` 处会被替换）
    layout    : 期望的运行时列名（**ColDes 顺序**）。逐位相等才允许映射 —— 这就是守卫。
    fields    : 原始列名 → 标准字段名。**不在表里的列不进标准名**（只留在 raw）。
    title     : 中文标题（给人看）
    evidence  : 出处 + 判据（哪条算式、命中率多少）
    units     : 字段 → 单位说明（写进 provenance）
    unmapped  : 原始列名 → 为什么不给标准名（未破解/占位/推断不硬）
    count_slot: 哪个参数位是「取多少期」；`None` = 本模式不开放条数
    day_slot  : 哪个参数位是日期（仅 `world_cal` 的事件日历用）；`None` = 不需要
    """
    key: str
    params: tuple
    layout: tuple
    fields: dict
    title: str
    evidence: str
    units: dict = field(default_factory=dict)
    unmapped: dict = field(default_factory=dict)
    count_slot: int | None = None
    day_slot: int | None = None


@dataclass(frozen=True)
class ProductSpec:
    """一个宏观产品：产品键（`products.json` 的键）+ 主题 + 若干模式。"""
    product: str
    theme: str
    title: str
    default_mode: str
    modes: dict
    note: str = ""


def _cols(n: int, start: int = 1) -> tuple:
    """`N001..Nnn` 的连续列清单（ColDes 连续时用；不连续的必须手写）。"""
    return tuple("N%03d" % i for i in range(start, start + n))


#: 已 typed 的产品（18 个）。**每一条都有 pcap 回包 + 算式证据**。
AVAILABLE: dict[str, ProductSpec] = {

    # ── GDP（hg_gdp，ctl=29）──────────────────────────────────────────────
    "hg_gdp": ProductSpec(
        product="hg_gdp", theme="gdp", title="国内生产总值", default_mode="total",
        note="001 总量（现价，元）/ 002 三次产业（现价，元+亿元）/ 003 分行业（不变价）",
        modes={
            "total": ModeSpec(
                key="total", params=("001", "", "", "0", 20), count_slot=4,
                layout=_cols(5),
                fields={"N001": "report_period", "N002": "unit_text",
                        "N003": "gdp_yuan", "N004": "yoy_chg_yuan",
                        "N005": "real_yoy_pct"},
                title="GDP 总量（年度，现价）",
                evidence="M2 §2.2/001：N004=N003(t)−N003(t−1) 19/19；"
                         "N005 是**不变价/实际**增速（当名义增速用 0/19 成立，"
                         "2022 年连符号都反：名义 +5.13% vs N005 3.10）",
                units={"gdp_yuan": "元", "yoy_chg_yuan": "元（现价同比增量）",
                       "real_yoy_pct": "%（不变价/实际增速）"},
            ),
            "industries3": ModeSpec(
                key="industries3", params=("002", "0", "0", "0", "1"),
                layout=_cols(13),
                fields={
                    "N001": "report_period",
                    "N002": "primary_value_yuan", "N003": "primary_yoy_pct",
                    "N004": "primary_yoy_chg_yi_yuan", "N005": "primary_share_pct",
                    "N006": "secondary_value_yuan", "N007": "secondary_yoy_pct",
                    "N008": "secondary_yoy_chg_yi_yuan", "N009": "secondary_share_pct",
                    "N010": "tertiary_value_yuan", "N011": "tertiary_yoy_pct",
                    "N012": "tertiary_yoy_chg_yi_yuan", "N013": "tertiary_share_pct",
                },
                title="GDP 三次产业（现价）",
                evidence="M2 §2.2/002：N002+N006+N010 = 001 的 N003（140.18791 万亿，精确）；"
                         "(N004+N008+N012)×1e8 = 001 的 N004（5.3813 万亿，精确）；"
                         "组内 同比% = 增量/(值−增量)×100 三组逐位；占比% = 值/合计 且三组之和=100.0000",
                units={"*_value_yuan": "元", "*_yoy_chg_yi_yuan": "亿元（⚠️与值差 1e8，别同尺度缩放）",
                       "*_yoy_pct": "%（现价）", "*_share_pct": "%（占 GDP 比重）"},
            ),
            "by_industry": ModeSpec(
                key="by_industry", params=("003", "", "", "0", "1"),
                layout=_cols(6),
                fields={"N001": "report_period", "N002": "industry_name",
                        "N003": "industry_value_yi_yuan", "N004": "industry_real_yoy_pct",
                        "N005": "industry_share_pct", "N006": "industry_code"},
                title="GDP 分行业增加值（11 个行业）",
                evidence="M2 §2.2/003：ΣN003 = 140.18793 万亿 = GDP（精确）；"
                         "N005=N003/Σ 11/11；N004 是**不变价**（Σ占比×N004=5.0326% 落在 001 的 N005=5.00 上）",
                units={"industry_value_yi_yuan": "亿元", "industry_real_yoy_pct": "%（不变价）",
                       "industry_share_pct": "%"},
            ),
        },
    ),

    # ── 财政收支（czsz，ctl=30）────────────────────────────────────────────
    "czsz": ProductSpec(
        product="czsz", theme="fiscal", title="财政收支", default_mode="monthly",
        note="001 月度横表（全国/中央/地方 × 3 组）/ 005 年度 / 007 年度分项（5 个税种）",
        modes={
            "monthly": ModeSpec(
                key="monthly", params=("001", "", 20), count_slot=2,
                layout=_cols(13),
                fields={
                    "N001": "report_period",
                    "N002": "national_value_yuan", "N003": "national_mom_chg_yuan",
                    "N004": "national_yoy_pct", "N005": "national_group_code",
                    "N006": "central_value_yuan", "N007": "central_mom_chg_yuan",
                    "N008": "central_yoy_pct", "N009": "central_group_code",
                    "N010": "local_value_yuan", "N011": "local_mom_chg_yuan",
                    "N012": "local_yoy_pct", "N013": "local_group_code",
                },
                title="财政收支（月度横表，3 组：全国/中央/地方）",
                evidence="M2 §2.1/001：N002=N006+N010 20/20；N003=N002(t)−N002(t−1) **17/17**"
                         "（文档曾写「同比增减额」，按同比用 0/10 成立 → 已否证）；"
                         "N004=(N002(t)/N002(t−12)−1)×100 10/10。码尾置，第一组才是全国",
                units={"*_value_yuan": "元", "*_mom_chg_yuan": "元（**环比**增减额）",
                       "*_yoy_pct": "%（同比）"},
            ),
            "annual": ModeSpec(
                key="annual", params=("005", "", 20), count_slot=2,
                layout=_cols(5),
                fields={"N001": "report_period", "N002": "annual_value_yuan",
                        "N003": "annual_yoy_chg_yuan", "N004": "annual_yoy_pct",
                        "N005": "gdp_share_pct"},
                title="财政收支（年度）",
                evidence="M2 §2.1/005：年度行 → N003=N002(t)−N002(t−1) 19/19（这里**就是**同比增减额）；"
                         "N004 16/19 精确（2015-2017 是同口径调整年）；"
                         "N005 占 GDP 比重的分母是**当时公布**的 GDP，不可用 001 的核实数重算（20/20 证明 ≤ 核实数）",
                units={"annual_value_yuan": "元", "annual_yoy_chg_yuan": "元",
                       "annual_yoy_pct": "%", "gdp_share_pct": "%（分母为当时公布值，勿重算）"},
            ),
            "items": ModeSpec(
                key="items", params=("007", "", "1"),
                layout=_cols(16),
                fields={
                    "N001": "report_period",
                    "N002": "tax_total_value_yuan", "N003": "tax_total_yoy_pct",
                    "N004": "tax_total_group_code",
                    "N005": "vat_value_yuan", "N006": "vat_yoy_pct", "N007": "vat_group_code",
                    "N008": "consumption_tax_value_yuan", "N009": "consumption_tax_yoy_pct",
                    "N010": "consumption_tax_group_code",
                    "N011": "tariff_value_yuan", "N012": "tariff_yoy_pct",
                    "N013": "tariff_group_code",
                    "N014": "corporate_income_tax_value_yuan",
                    "N015": "corporate_income_tax_yoy_pct",
                    "N016": "corporate_income_tax_group_code",
                },
                title="财政收支（年度分项：5 个税种）",
                evidence="M2 §2.1/007：结构 = 年份 + 5 组×(值,同比%) + 尾置码；"
                         "分项和 = 合计的 73.42% → **不是**加法恒等式（合计是全税种，分项只到 4 个税种）；"
                         "01=税收合计/02=增值税/03=消费税/04=关税/05=企业所得税 来自接口文档（M2 §2.1 逐条核对）",
                units={"*_value_yuan": "元", "*_yoy_pct": "%"},
            ),
        },
    ),

    # ── 日历 / 财经事件（world_cal = hg_cal，ctl=29）──────────────────────
    "world_cal": ProductSpec(
        product="world_cal", theme="calendar", title="日历（交易日 + 财经事件）",
        default_mode="trading_days",
        note="001 交易日历（366 行）/ 002 财经事件日历（需要 day）",
        modes={
            "trading_days": ModeSpec(
                key="trading_days", params=("001", "", "", ""),
                layout=_cols(4),
                fields={"N001": "date", "N002": "is_trading_day",
                        "N003": "weekday_text", "N004": "status_flag"},
                title="交易日历（未来 366 天）",
                evidence="M2 §2.3/模式A：N002 分布 {1:244, 0:122} —— **不是**「全为交易日」；"
                         "122 行 = 104 个周末 + 18 个工作日休市（与 holiday 的中国沪深休市日逐日互锁）；"
                         "N004 恒 1（366/366）",
                units={"is_trading_day": "1=开市 0=休市（周末调休上班日不算交易日）"},
            ),
            "events": ModeSpec(
                key="events", params=("002", "0", "1", "2026-10-20"), day_slot=3,
                layout=_cols(9),
                fields={"N001": "date", "N002": "data_status", "N003": "time_text",
                        "N004": "importance", "N005": "region_name", "N006": "item_name",
                        "N007": "actual", "N008": "forecast", "N009": "previous"},
                title="财经事件日历（按日）",
                evidence="M2 §2.3/模式B：N002 ∈ {预期, null}；N004 重要度 1–4；"
                         "N006 是指标名，但实测也有整句事件（如「2026金融街论坛年会举办。」）",
                units={"importance": "1–4", "actual": "今值（可能是文字，非数值原样保留）"},
            ),
            "events_month": ModeSpec(
                key="events_month", params=("002", "0", "2", "2026-10-20"), day_slot=3,
                layout=_cols(9),
                fields={"N001": "date", "N002": "data_status", "N003": "time_text",
                        "N004": "importance", "N005": "region_name", "N006": "item_name",
                        "N007": "actual", "N008": "forecast", "N009": "previous"},
                title="财经事件日历（按月视图，同一 9 列）",
                evidence="M2 §2.3/模式B 同一形状；pos2=2 时同一天回 4 行（月视图）——"
                         "两套视图列义相同，只是范围不同",
            ),
        },
    ),

    # ── 节假日（holiday = hg_holiday，ctl=33）──────────────────────────────
    "holiday": ProductSpec(
        product="holiday", theme="holidays", title="全球交易所节假日", default_mode="all",
        modes={
            "all": ModeSpec(
                key="all", params=("001", "0"),
                layout=_cols(6),
                fields={"N001": "date", "N002": "weekday_text", "N003": "country_name",
                        "N004": "holiday_name", "N005": "closure_note", "N006": "remark"},
                title="全球交易所节假日（全部市场，189 行）",
                evidence="M2 §2.4：N006 **不是**占位列 —— 189 行里 20 行非空"
                         "（交易品种范围/日期可能更改/具体收盘时点）；N005 可为 null（如「冬令时开始」）",
            ),
        },
    ),

    # ── 景气指数（jqzs，ctl=30）───────────────────────────────────────────
    "jqzs": ProductSpec(
        product="jqzs", theme="prosperity", title="景气指数", default_mode="index",
        note="002 单指标序列（制造业 PMI，pos2=01）/ 003 分量占比；004/005 只有 1 行、未破解（不暴露）",
        modes={
            "index": ModeSpec(
                key="index", params=("002", "01", "1", 20), count_slot=3,
                layout=("N001", "N003", "N004", "N005"),      # ⚠️ 没有 N002
                fields={"N001": "report_period", "N003": "index_value",
                        "N004": "yoy_pct", "N005": "mom_diff_point"},
                title="景气指数（制造业 PMI 序列）",
                evidence="M2 §2.5/002：ColDes = N001,N003,N004,N005（服务端**略去 N002**，按序号猜位置会把"
                         "值/同比/环比整体错位）；N004=(值(t)/值(t−12)−1)×100 8/8；N005=值(t)−值(t−1) 19/19",
                units={"index_value": "点（荣枯线 50）", "mom_diff_point": "点（绝对差，不是%）"},
            ),
            "components": ModeSpec(
                key="components", params=("003", "", "1", 20), count_slot=3,
                layout=_cols(5),
                fields={"N001": "report_period", "N002": "up_share_pct",
                        "N003": "down_share_pct", "N004": "net_diff_ratio"},
                unmapped={"N005": "「同比%」是谁的同比未破解（与 N002/N003/N004 任何组合都对不上，M2 §3）"},
                title="景气指数（分量占比）",
                evidence="M2 §2.5/003：N002+N003 ≤ 100（余量=持平）；N004=(N002−N003)/100 20/20 —— "
                         "⚠️ N004 是**小数**（0.37），N002/N003 是**百分数**（68/31），同表两种刻度",
                units={"up_share_pct": "%", "down_share_pct": "%", "net_diff_ratio": "小数（非 %）"},
            ),
        },
    ),

    # ── 银行报表（yhhb，ctl=30）───────────────────────────────────────────
    "yhhb": ProductSpec(
        product="yhhb", theme="bank", title="央行/银行报表", default_mode="main",
        note="002 单序列 / 004 按分组码（ColDes 乱序！）/ 005 合计+4 分项。"
             "科目身份回包只给数字码 → 不给科目名列",
        modes={
            "main": ModeSpec(
                key="main", params=("002", "0", "1", 20), count_slot=3,
                layout=_cols(5),
                fields={"N001": "report_period", "N002": "value_yuan",
                        "N003": "mom_chg_yuan", "N004": "yoy_pct", "N005": "mom_pct"},
                title="银行报表（主序列）",
                evidence="M2 §2.7/002：N003=N002(t)−N002(t−1) **19/19**（文档写「同比增减额」→ 错）；"
                         "N005=N003/(N002−N003)×100 20/20（分母是**上期**）",
                units={"value_yuan": "元", "mom_chg_yuan": "元（环比增减额）",
                       "yoy_pct": "%", "mom_pct": "%（环比，分母=上期）"},
            ),
            "by_group": ModeSpec(
                key="by_group", params=("004", "01", "1", 20), count_slot=3,
                # ⚠️ 物理顺序 = ColDes = N001,N002,N003,N004,**N006**,**N005**
                layout=("N001", "N002", "N003", "N004", "N006", "N005"),
                fields={"N001": "report_period", "N002": "group_code",
                        "N003": "value_yuan", "N004": "mom_chg_yuan",
                        "N006": "yoy_pct", "N005": "mom_pct"},
                title="银行报表（按分组码，**同比/环比在 ColDes 里反着命名**）",
                evidence="M2 §2.7/004：物理第 5 列叫 N006 且是**同比%**（(值(t)/值(t−12)−1)×100 8/8）、"
                         "第 6 列叫 N005 且是**环比%**（N004/(值−N004)×100 20/20）；"
                         "N002 在本模式是**字符串分组码**（回显请求），在 002 模式却是浮点值",
            ),
            "breakdown": ModeSpec(
                key="breakdown", params=("005", "", "1", "1"),
                # 结构 = 期间 + 合计(值,同比%,码) + 4 组×(值,同比%,码)：**码尾置**。
                # （数据核对：N002=11.328487万亿=4 项之和，N004="00"，N005..N007=(值,%,01)…）
                layout=_cols(16),
                fields={
                    "N001": "report_period", "N002": "total_value_yuan",
                    "N003": "total_yoy_pct", "N004": "total_group_code",
                    "N005": "item1_value_yuan", "N006": "item1_yoy_pct",
                    "N007": "item1_code",
                    "N008": "item2_value_yuan", "N009": "item2_yoy_pct",
                    "N010": "item2_code",
                    "N011": "item3_value_yuan", "N012": "item3_yoy_pct",
                    "N013": "item3_code",
                    "N014": "item4_value_yuan", "N015": "item4_yoy_pct",
                    "N016": "item4_code",
                },
                title="银行报表（合计 + 4 分项，码**尾置**）",
                evidence="M2 §2.7/005：4 个分项相加 = 合计**精确相等**（4.6555+3.2322+1.4003+2.0404 = 11.3285 万亿）"
                         "→ 完整加法分解；码序 00/01/02/03。"
                         "⚠️ 原始 M2 正文写「码在值前面」，逐列核对回包后修正为**码尾置**"
                         "（N004=\"00\" 是合计的码，N005 起才是值）—— 数据优先于叙述",
                units={"*_value_yuan": "元", "*_yoy_pct": "%"},
            ),
        },
    ),

    # ── 固定资产投资（hg_tz，ctl=28）──────────────────────────────────────
    "hg_tz": ProductSpec(
        product="hg_tz", theme="investment", title="固定资产投资", default_mode="total",
        note="001 三分分解（1/111/112/113）/ 003 累计序列 / 004 月度流量",
        modes={
            "total": ModeSpec(
                key="total", params=("001", "", "1", "1"),
                layout=_cols(13),
                fields={
                    "N001": "report_period",
                    "N002": "total_value_yuan", "N003": "total_yoy_pct",
                    "N004": "total_item_code",
                    "N005": "item111_value_yuan", "N006": "item111_yoy_pct",
                    "N007": "item111_code",
                    "N008": "item112_value_yuan", "N009": "item112_yoy_pct",
                    "N010": "item112_code",
                    "N011": "item113_value_yuan", "N012": "item113_yoy_pct",
                    "N013": "item113_code",
                },
                title="固定资产投资（合计 + 3 分项，层级码 1/111/112/113）",
                evidence="M2 §2.8/001：111+112+113 = 合计**精确相等**（0.6527+12.9743+19.7845 = 33.4115 万亿）"
                         "→ 完整三分分解；分组身份（三次产业）按量级+层级码**推断**，接口不回科目名",
                units={"*_value_yuan": "元（年初至今累计）", "*_yoy_pct": "%（累计同比）"},
            ),
            "cumulative": ModeSpec(
                key="cumulative", params=("003", "", "1", 20), count_slot=3,
                layout=_cols(3),
                fields={"N001": "report_period", "N002": "cumulative_value_yi_yuan",
                        "N003": "cumulative_yoy_ratio"},
                title="固定资产投资（本年累计，亿元）",
                evidence="M2 §2.8/003：同一年内单调不减、跨年归零；N003=(N002(t)/N002(t−12)−1)×100 9/9，"
                         "但**以小数形式给**（−0.1345643 = −0.13%，不是 −13.4%）",
                units={"cumulative_value_yi_yuan": "亿元",
                       "cumulative_yoy_ratio": "⚠️ 小数形式（×100 才是 %）"},
                unmapped={},
            ),
            "monthly": ModeSpec(
                key="monthly", params=("004", "", "1", 20), count_slot=3,
                layout=_cols(4),
                fields={"N001": "report_period", "N002": "monthly_value_yuan",
                        "N003": "yoy_pct", "N004": "mom_pct"},
                title="固定资产投资（月度流量）",
                evidence="M2 §2.8/004：N003=(N002(t)/N002(t−12)−1)×100 8/8；"
                         "N004=**环比%**（19/19，文档写「同比%另一口径」→ 错）",
                units={"monthly_value_yuan": "元", "yoy_pct": "%", "mom_pct": "%"},
            ),
        },
    ),

    # ── 就业保障（hg_jybz，ctl=30）────────────────────────────────────────
    "hg_jybz": ProductSpec(
        product="hg_jybz", theme="employment", title="就业与保障", default_mode="two_series",
        note="001 双序列（码 371/372，身份未破解）/ 003 年度 / 004 三组累计（身份未破解）",
        modes={
            "two_series": ModeSpec(
                key="two_series", params=("001", "", 5), count_slot=2,
                layout=_cols(7),
                fields={"N001": "report_period",
                        "N002": "series371_value_yuan", "N003": "series371_yoy_pct",
                        "N004": "series371_code",
                        "N005": "series372_value_yuan", "N006": "series372_yoy_pct",
                        "N007": "series372_code"},
                title="就业保障（双序列，指标码 371/372）",
                evidence="M2 §2.9/001：结构 = 期间 + 2 组×(值,同比%,码)，码尾置；"
                         "序列身份（371/372 是什么）**未破解** → 字段名按码命名，不编科目名",
            ),
            "annual": ModeSpec(
                key="annual", params=("003", "", 20), count_slot=2,
                layout=_cols(5),
                fields={"N001": "report_period", "N002": "annual_value",
                        "N003": "annual_yoy_pct", "N005": "annual_yoy_chg"},
                unmapped={"N004": "一个 4% 量级的百分率，2022 起停更（2010–2021 有 12 年真值 3.62~4.24）。"
                                 "形态吻合「城镇登记失业率」，但接口不给列名 → 高置信推断，不当事实映射"},
                title="就业保障（年度）",
                evidence="M2 §2.9/003：N003=(N002(t)/N002(t−1)−1)×100 15/15；"
                         "N005=N002(t)−N002(t−1) 15/15（年度行 → 同比增减额）",
            ),
            "three_groups": ModeSpec(
                key="three_groups", params=("004", "", 20), count_slot=2,
                layout=_cols(10),
                fields={"N001": "report_period",
                        "N002": "g01_value", "N003": "g01_yoy_pct", "N004": "g01_code",
                        "N005": "g02_value", "N006": "g02_yoy_pct", "N007": "g02_code",
                        "N008": "g03_value", "N009": "g03_yoy_pct", "N010": "g03_code"},
                title="就业保障（三组本年累计，码 01/02/03）",
                evidence="M2 §2.9/004：三组值为本年累计（同年内单调不减 PASS）；"
                         "三组身份**未破解**（量级像「新增就业/再就业/困难人员就业」，但无内部证据）",
            ),
        },
    ),

    # ── 居民收入（hg_jmsh，ctl=30）────────────────────────────────────────
    "hg_jmsh": ProductSpec(
        product="hg_jmsh", theme="income", title="居民收入与消费支出", default_mode="income",
        note="001 人均可支配收入 / 003 人均消费支出；两者码空间不同（01/02/03 vs 167001-167003）",
        modes={
            "income": ModeSpec(
                key="income", params=("001", "", 20), count_slot=2,
                layout=_cols(10),
                fields={"N001": "report_period",
                        "N002": "national_income_yuan", "N003": "national_income_yoy_pct",
                        "N004": "national_income_group_code",
                        "N005": "urban_income_yuan", "N006": "urban_income_yoy_pct",
                        "N007": "urban_income_group_code",
                        "N008": "rural_income_yuan", "N009": "rural_income_yoy_pct",
                        "N010": "rural_income_group_code"},
                title="居民人均可支配收入（全国/城镇/农村）",
                evidence="M2 §2.10/001：码 02 > 码 01 > 码 03 **20/20** 恒成立（城镇>全国>农村）；"
                         "外部对照 2024 前三季度 30941.12/41182.96/16740.22 元逐位相等",
                units={"*_income_yuan": "元（累计）", "*_income_yoy_pct": "%"},
            ),
            "spending": ModeSpec(
                key="spending", params=("003", "", 20), count_slot=2,
                layout=_cols(10),
                fields={"N001": "report_period",
                        "N002": "national_spending_yuan", "N003": "national_spending_yoy_pct",
                        "N004": "national_spending_group_code",
                        "N005": "urban_spending_yuan", "N006": "urban_spending_yoy_pct",
                        "N007": "urban_spending_group_code",
                        "N008": "rural_spending_yuan", "N009": "rural_spending_yoy_pct",
                        "N010": "rural_spending_group_code"},
                title="居民人均消费支出（全国/城镇/农村）",
                evidence="M2 §2.10/003：同结构，码换 167001/167002/167003；"
                         "外部对照 2024 前三季度 20631.28/25529.58/13839.49 元逐位相等。"
                         "⚠️ 码空间是**产品内局部**的：167001 在 hg_wjzs 里是另一条序列，不能跨产品推",
                units={"*_spending_yuan": "元（累计）", "*_spending_yoy_pct": "%"},
            ),
        },
    ),

    # ── 工业经济（gyjj = hg_gyjj，ctl=30）─────────────────────────────────
    "gyjj": ProductSpec(
        product="gyjj", theme="industry", title="工业经济", default_mode="growth",
        note="001 增速三列 / 003 营收累计；N004 两模式都未破解",
        modes={
            "growth": ModeSpec(
                key="growth", params=("001", "", "1", 20), count_slot=3,
                layout=_cols(4),
                fields={"N001": "report_period", "N002": "current_month_yoy_pct",
                        "N003": "cumulative_yoy_pct"},
                unmapped={"N004": "环比% **只是推断**（0.11~0.76 量级，无独立基期可验，M2 §2.11）"},
                title="工业经济（当月/累计同比）",
                evidence="M2 §2.11/001：N002 在 1-2 月缺值（合并发布，结构性证据）；"
                         "N003 从不缺值且 Δ 更平滑（15 vs 0）；202603 手工可验 (6.3×2+5.7)/3 = 6.10",
            ),
            "revenue": ModeSpec(
                key="revenue", params=("003", "N002", "1", 20), count_slot=3,
                layout=_cols(4),
                fields={"N001": "report_period", "N002": "revenue_cumulative_yuan",
                        "N003": "revenue_cumulative_yoy_pct"},
                unmapped={"N004": "4.33 万亿那个量**未破解**：与「规上工业利润总额」（7.4 万亿级）差 40%+，"
                                 "不许冒充（M2 §2.11、§3）"},
                title="工业经济（规上工业企业营业收入，本年累计）",
                evidence="M2 §2.11/003：同一年内单调不减；2025 全年 139.2 万亿；"
                         "N003=(N002(t)/N002(t−12)−1)×100 9/9",
                units={"revenue_cumulative_yuan": "元（累计）", "revenue_cumulative_yoy_pct": "%"},
            ),
        },
    ),

    # ── 产量/能源（hg_nysc，ctl=30）───────────────────────────────────────
    "hg_nysc": ProductSpec(
        product="hg_nysc", theme="output", title="产量序列（产品名疑为「农业」，实为产量类）",
        default_mode="series_1",
        note="001 模式 × pos2=1/2 两条序列；列语义确定，但**序列身份与单位未证明**",
        modes={
            "series_1": ModeSpec(
                key="series_1", params=("001", "1", "1", 20), count_slot=3,
                layout=_cols(5),
                fields={"N001": "report_period", "N002": "output_value",
                        "N003": "mom_chg", "N004": "yoy_pct", "N005": "mom_pct"},
                title="产量序列 1（pos2=1）",
                evidence="M2 §2.12：N003=N002(t)−N002(t−1) **17/17**（两个 pos2 都验）；"
                         "N002 是**月度**产量（跨年不归零）；N004 同比%、N005 环比%",
                units={"output_value": "⚠️ 单位未证明（1.7e7/月量级，形态像原油「吨」）",
                       "mom_chg": "与 output_value 同单位"},
            ),
            "series_2": ModeSpec(
                key="series_2", params=("001", "2", "1", 20), count_slot=3,
                layout=_cols(5),
                fields={"N001": "report_period", "N002": "output_value",
                        "N003": "mom_chg", "N004": "yoy_pct", "N005": "mom_pct"},
                title="产量序列 2（pos2=2）",
                evidence="M2 §2.12：同结构（环比增减 17/17）；两条序列身份**未破解**"
                         "（量级 1.8e7 vs 3.9e8），不许编科目名",
                units={"output_value": "⚠️ 单位未证明"},
            ),
        },
    ),

    # ── 社零（dyjjyxf，ctl=33）───────────────────────────────────────────
    "dyjjyxf": ProductSpec(
        product="dyjjyxf", theme="retail", title="社会消费品零售总额（产品名「对外经济」是误标）",
        default_mode="total",
        note="⚠️ 同名列在两个模式里语义不同：total 的 N002=科目名、N004=环比增减额；"
             "code_163001 的 N002=指标码、N004=同比% —— 这正是「不能按位置映射」的活教材",
        modes={
            "total": ModeSpec(
                key="total", params=("002", "184000", "", "1", 20), count_slot=4,
                layout=_cols(6),
                fields={"N001": "report_period", "N002": "subject_name",
                        "N003": "value_yuan", "N004": "mom_chg_yuan",
                        "N005": "yoy_pct", "N006": "mom_pct"},
                title="社零（合计口径）",
                evidence="M2 §2.13/002：N004=N003(t)−N003(t−1) **17/17**；"
                         "同表内 2025/2026 同框自证：同比 1.101% ≈ N005 1.10、环比 6.5503% = N006；"
                         "外部对照逐月吻合「社零总额」→ 产品名「对外经济」是误标（内需，不是外贸）",
                units={"value_yuan": "元（月度流量，非累计）", "mom_chg_yuan": "元（环比）",
                       "yoy_pct": "%", "mom_pct": "%"},
            ),
            "code_163001": ModeSpec(
                key="code_163001", params=("004", "163001", "", "1", 20), count_slot=4,
                layout=_cols(6),
                fields={"N001": "report_period", "N002": "indicator_code",
                        "N003": "value_yuan", "N004": "yoy_pct",
                        "N005": "mom_chg_yuan", "N006": "mom_pct"},
                title="社零（指标码 163001，与「合计」不是同一序列）",
                evidence="M2 §2.13/004：N005=N003(t)−N003(t−1) 19/19；N006=N005/(N003−N005)×100 19/19；"
                         "N004 同比（202609 报 27.50 vs 裸比 27.77，口径略有差）；"
                         "指标 163001 身份**未破解**（202609 合计 4.24 万亿 vs 163001 5.16 万亿）",
                units={"value_yuan": "元", "yoy_pct": "%", "mom_chg_yuan": "元（环比）"},
            ),
        },
    ),

    # ── 物价指数（hg_wjzs，ctl=30）───────────────────────────────────────
    "hg_wjzs": ProductSpec(
        product="hg_wjzs", theme="prices", title="物价指数", default_mode="by_code",
        note="002 单指标（pos2=指标码）/ 003 4 组（分组身份未破解）/ 005 季度（身份未破解）",
        modes={
            "by_code": ModeSpec(
                key="by_code", params=("002", "167001", "1", 20), count_slot=3,
                layout=_cols(5),
                fields={"N001": "report_period", "N002": "yoy_index",
                        "N003": "cumulative_index", "N004": "mom_index",
                        "N005": "indicator_code"},
                title="物价指数（当月同比/累计/环比三条指数）",
                evidence="M2 §2.6/002：N002 当月同比指数（上年同月=100）；"
                         "N003 累计同比指数 ≈ 同年内 N002 均值 8/8（100.2→100.8→101.0 逐月精确）；"
                         "N004 环比指数（上月=100，202602=101.0 符合春节季节性）；N005 恒为回显码",
                units={"*_index": "指数（100=基期，不是 %）"},
            ),
            "four_groups": ModeSpec(
                key="four_groups", params=("003", "", "1", 20), count_slot=3,
                layout=_cols(9),
                fields={"N001": "report_period",
                        "N002": "g1_index", "N003": "g1_code",
                        "N004": "g2_index", "N005": "g2_code",
                        "N006": "g3_index", "N007": "g3_code",
                        "N008": "g4_index", "N009": "g4_code"},
                title="物价指数（4 组同比指数，码 01–04 身份未破解）",
                evidence="M2 §2.6/003：结构 = 期间 + 4 组×(指数, 码)，码尾置；四组量级 94.9/105.7/111.1/103.7。"
                         "**分组身份不可判定**（接口不回分组名，167001 首值 100.9 与四组都不等）→ 字段按码命名",
            ),
            "quarterly": ModeSpec(
                key="quarterly", params=("005", "", "1", 20), count_slot=3,
                layout=_cols(2),
                fields={"N001": "report_period", "N002": "index_value"},
                title="物价指数（季度，指标身份未破解）",
                evidence="M2 §2.6/005：季度末（03/06/09/12），量级 92~108，2022→2023 单边下行；"
                         "指标身份无内部线索 → 只给 `index_value`，不编口径名",
            ),
        },
    ),

    # ── 美国：国民经济（gmjjyjy = mghg_gmjjyjy，ctl=35）───────────────────
    "gmjjyjy": ProductSpec(
        product="gmjjyjy", theme="us_gdp", title="美国国民经济（NIPA 月度/季度）",
        default_mode="quarterly",
        note="001 季度 / 003、005 月度人数序列（两条线，身份未证实）",
        modes={
            "quarterly": ModeSpec(
                key="quarterly", params=("001", "", 20, "1"), count_slot=2,
                layout=_cols(9),
                fields={"N001": "report_period", "N002": "gdp_nominal",
                        "N003": "quarter_chg_milli", "N004": "yoy_pct",
                        "N005": "qoq_pct"},
                unmapped={"N006": "恒 null（占位）",
                          "N007": "三个 124~133 的指数之一：**未破解**",
                          "N008": "同上（N008 与 N009 只差 0.005~0.012，像原始值 vs 修订值）",
                          "N009": "同上"},
                title="美国 GDP（季度，季调折年）",
                evidence="M2 §2.14：N004=(N002(t)/N002(t−12月)−1)×100 16/16；"
                         "N005=(N002(t)/N002(t−3月)−1)×100 19/19（季环比原值，非折年）；"
                         "⚠️ N003 = 真实增减额的 **1/1000**（32563030000000−31865721000000 = 697309000000，而 N003=697309000）",
                units={"gdp_nominal": "美元（季调折年量级）",
                       "quarter_chg_milli": "⚠️ 值为真实增减额 ÷ 1000（单位不一致，M2 §2.14 实测）",
                       "yoy_pct": "%", "qoq_pct": "%"},
            ),
            "persons_a": ModeSpec(
                key="persons_a", params=("003", "0", 20, "1"), count_slot=2,
                layout=_cols(5),
                fields={"N001": "report_period", "N002": "persons_value",
                        "N003": "mom_chg", "N004": "yoy_pct", "N005": "mom_pct"},
                title="美国人数序列 A（pos2=0）",
                evidence="M2 §2.14/003：N003=N002(t)−N002(t−1) 18/18；N005=N003/(N002−N003)×100 19/19；"
                         "量级 1.70 亿（同比 −0.58%）→ 口径身份**未证实**（像劳动力总人口）",
                units={"persons_value": "人（口径未证实）"},
            ),
            "persons_b": ModeSpec(
                key="persons_b", params=("005", "", 20, "1"), count_slot=2,
                layout=_cols(5),
                fields={"N001": "report_period", "N002": "persons_value",
                        "N003": "mom_chg", "N004": "yoy_pct", "N005": "mom_pct"},
                title="美国人数序列 B（与 A 是两条不同序列）",
                evidence="M2 §2.14/005：同结构（N003 19/19、N005 20/20）；量级 1.59 亿（同比 +0.31%）"
                         "→ 身份**未证实**（像非农就业人数）。两模式同名字段，比较前先确认取的是哪条",
                units={"persons_value": "人（口径未证实）"},
            ),
        },
    ),

    # ── 美国：利率决议（mghg_hbzc，ctl=32）────────────────────────────────
    "mghg_hbzc": ProductSpec(
        product="mghg_hbzc", theme="us_rate", title="美联储利率决议（最新一次快照）",
        default_mode="policy_rate",
        modes={
            "policy_rate": ModeSpec(
                key="policy_rate", params=("001", "", "0", "0"),
                layout=_cols(3),
                fields={"N001": "meeting_date", "N002": "target_rate_range_pct",
                        "N003": "action_text"},
                title="美联储目标利率区间 + 决议动作",
                evidence="M2 §2.14：区间带宽 4.00−3.75 = 0.25% 恰为 25bp，与「加息25」步长自洽；"
                         "⚠️ 只回 1 行 → 这是**最新一次决议快照**，不是历史序列",
                units={"target_rate_range_pct": "「下限-上限」% 字符串（别直接 float）"},
            ),
        },
    ),

    # ── 美国：货币与信贷（mghg_mlc，ctl=31）───────────────────────────────
    "mghg_mlc": ProductSpec(
        product="mghg_mlc", theme="us_money", title="美国货币与信贷", default_mode="stock",
        note="001/003 存量序列（两条线）/ 004 合计+4 分项+占比；005 语料有回包但 M2 未分析 → 不暴露",
        modes={
            "stock": ModeSpec(
                key="stock", params=("001", "", 20, "1"), count_slot=2,
                layout=_cols(5),
                fields={"N001": "report_period", "N002": "value",
                        "N003": "mom_chg", "N004": "yoy_pct", "N005": "mom_pct"},
                title="美国货币/信贷存量（序列 001）",
                evidence="M2 §2.14/001：N003=N002(t)−N002(t−1) 17/17；N005=N003/(N002−N003)×100 20/20；"
                         "N004 同比 6.73%",
                units={"value": "⚠️ 单位未定（量级 4.0e7）", "mom_chg": "同 value 单位"},
            ),
            "stock_2": ModeSpec(
                key="stock_2", params=("003", "2", 20, "1"), count_slot=2,
                layout=_cols(5),
                fields={"N001": "report_period", "N002": "value",
                        "N003": "mom_chg", "N004": "yoy_pct", "N005": "mom_pct"},
                title="美国货币/信贷存量（序列 003，pos2=2）",
                evidence="M2 §2.14/003：同结构（N003 18/18、N005 19/19）；量级 23.28 万亿、同比 +5.68% "
                         "→ 与「美国 M2」吻合（**推断**，接口不给科目名）",
                units={"value": "美元（量级 2.3e13，推断 M2）"},
            ),
            "breakdown": ModeSpec(
                key="breakdown", params=("004", "", "1", "1"),
                layout=_cols(10),
                fields={"N001": "report_period", "N002": "total_value",
                        "N003": "item1_value", "N004": "item2_value",
                        "N005": "item3_value", "N006": "item4_value",
                        "N007": "item1_share_pct", "N008": "item2_share_pct",
                        "N009": "item3_share_pct", "N010": "item4_share_pct"},
                title="美国货币/信贷（合计 + 4 分项 + 各自占比）",
                evidence="M2 §2.14/004：N003+N004+N005+N006 = N002 **精确相等**；"
                         "N007..N010 = 各分项/N002×100 4/4（4.332/67.782/12.747/15.138，合计≈100%）。"
                         "⚠️ N003 在 004 模式是**分项**、在 005 模式是**环比增减额** → 同名列两种角色",
            ),
        },
    ),

    # ── 美国：物价（macro_wj = mghg_wj，ctl=30）───────────────────────────
    "macro_wj": ProductSpec(
        product="macro_wj", theme="us_prices", title="美国物价指数（月度水平，359 期）",
        default_mode="m001",
        note="4 个模式都是**指数水平**（1996-11 起），不是同比/环比；各列口径身份未破解",
        modes={
            "m001": ModeSpec(
                key="m001", params=("001", "", "-1", "1"), count_slot=2,
                layout=_cols(5),
                fields={"N001": "report_period", "N002": "index_1", "N003": "index_2",
                        "N004": "index_3", "N005": "index_4"},
                title="美国物价指数 001（4 列指数水平）",
                evidence="M2 §2.14：单调上行、不是同比/环比；4 列互不构成加减关系 → "
                         "**口径身份未破解**，只给 index_1..4；count=-1 表示全部 359 期",
            ),
            "m003": ModeSpec(
                key="m003", params=("003", "", "-1", "1"), count_slot=2,
                layout=_cols(6),
                fields={"N001": "report_period", "N002": "index_1", "N003": "index_2",
                        "N004": "index_3", "N005": "index_4", "N006": "index_5"},
                title="美国物价指数 003（5 列指数水平）",
                evidence="M2 §2.14：同上；1996-11 全 null（该模式更短的可用区间）",
            ),
            "m005": ModeSpec(
                key="m005", params=("005", "", "-1", "1"), count_slot=2,
                layout=_cols(3),
                fields={"N001": "report_period", "N002": "index_1", "N003": "index_2"},
                title="美国物价指数 005（2 列指数水平）",
                evidence="M2 §2.14：同上；口径身份未破解",
            ),
            "m007": ModeSpec(
                key="m007", params=("007", "", "-1", "1"), count_slot=2,
                layout=_cols(3),
                fields={"N001": "report_period", "N002": "index_1", "N003": "index_2"},
                title="美国物价指数 007（2 列指数水平）",
                evidence="M2 §2.14：同上；口径身份未破解",
            ),
        },
    ),

    # ── 美国：存量快照（macro_yh = mghg_yh，ctl=30）───────────────────────
    "macro_yh": ProductSpec(
        product="macro_yh", theme="us_balance", title="美国资产负债类快照（1 行 × 4 存量值）",
        default_mode="snapshot",
        modes={
            "snapshot": ModeSpec(
                key="snapshot", params=("001", "", "1", "1"),
                layout=_cols(5),
                fields={"N001": "report_period", "N002": "value_1", "N003": "value_2",
                        "N004": "value_3", "N005": "value_4"},
                title="美国存量快照（4 个存量值，身份未破解）",
                evidence="M2 §2.14/mghg_yh：只有 1 行、无跨期、无恒等式（前两个之和不等于任何后两个组合）"
                         "→ 只能给 value_1..4，**不许编科目名**",
            ),
        },
    ),
}


#: 语料里**没有任何回包** → 不暴露 typed 方法（调 `fetch` 抛 `NotSupportedError`）。
UNAVAILABLE: dict[str, str] = {
    "dghg_gdp": "语料 6976 条请求↔回包里 0 条该 cmd 的回包（pcap 也只抓到请求）",
    "dghg_jy": "同上：无回包 → 列义无证据",
    "dghg_wm": "同上：无回包 → 列义无证据",
    "macro_jq": "同上：无回包 → 列义无证据",
    "macro_xf": "同上：无回包 → 列义无证据",
    "macro_gy": "同上：无回包 → 列义无证据",
    "macro_my": "同上：无回包 → 列义无证据",
    "macro_sz": "同上：无回包 → 列义无证据",
    "mghg_cz": "同上：无回包（`mghg_*` 共 11 个，语料只覆盖 5 个）",
    "xjphg": "同上：无回包 → 列义无证据",
    "xjphg_hgjq": "同上：无回包 → 列义无证据",
}

#: 有回包、但**形状/语义未破解到可以给字段名**的模式 → 不暴露，只登记理由。
UNVERIFIED: dict[str, dict] = {
    "jqzs": {
        "004": "4 列只有 1 行（98.9/97.5/98.4），无跨期 → 分不清本期/上月/上年同期",
        "005": "同上（89.5/90.3/88.4）",
        "001": "products.json 的默认 p1=001 这一套语料没抓到（抓到的是 002/003）",
    },
    "hg_tz": {"002": "products.json 默认 p1=002 语料没抓到"},
    "yhhb": {"001": "products.json 默认 p1=001 语料没抓到（抓到 002/004/005）"},
    "hg_wjzs": {"001": "products.json 默认 p1=001 语料没抓到"},
    "dyjjyxf": {"001": "products.json 默认 p1=001 语料没抓到"},
    "mghg_mlc": {"005": "语料里有回包（1 行 × 5 列），但 M2 未分析这一模式 → 语义无结论"},
    "macro_wj": {"002": "语料里只有 001/003/005/007 四个模式"},
    "czsz": {"002": "语料只覆盖 001/005/007", "003": "同上", "004": "同上", "006": "同上"},
    "hg_jybz": {"002": "语料只覆盖 001/003/004"},
    "gyjj": {"002": "语料只覆盖 001/003"},
    "hg_nysc": {"002": "语料只覆盖 001 的 pos2=1/2"},
    "hg_jmsh": {"002": "语料只覆盖 001/003"},
    "gmjjyjy": {"002": "语料只覆盖 001/003/005", "004": "同上"},
    "mghg_hbzc": {},
}

#: 主题方法 → (产品键, 默认模式)。名字就是 `c.macro.<name>(mode=…)`。
_THEMES: dict[str, tuple[str, str]] = {
    "gdp": ("hg_gdp", "total"),
    "fiscal": ("czsz", "monthly"),
    "calendar": ("world_cal", "trading_days"),
    "holidays": ("holiday", "all"),
    "prosperity": ("jqzs", "index"),
    "prices": ("hg_wjzs", "by_code"),
    "bank": ("yhhb", "main"),
    "investment": ("hg_tz", "total"),
    "employment": ("hg_jybz", "two_series"),
    "income": ("hg_jmsh", "income"),
    "industry": ("gyjj", "growth"),
    "output": ("hg_nysc", "series_1"),
    "retail": ("dyjjyxf", "total"),
    "us_gdp": ("gmjjyjy", "quarterly"),
    "us_rate": ("mghg_hbzc", "policy_rate"),
    "us_money": ("mghg_mlc", "stock"),
    "us_prices": ("macro_wj", "m001"),
    "us_balance": ("macro_yh", "snapshot"),
}


def products_covered() -> dict:
    """登记总表（产品 → 主题/模式/是否有 typed 通道）。**给文档与测试用**。

    返回 `{"available": {产品: {...}}, "unavailable": {...}, "unverified": {...}}`。
    """
    return {
        "available": {
            p: {"theme": s.theme, "title": s.title, "default_mode": s.default_mode,
                "modes": sorted(s.modes)}
            for p, s in sorted(AVAILABLE.items())
        },
        "unavailable": dict(sorted(UNAVAILABLE.items())),
        "unverified": {p: dict(sorted(v.items())) for p, v in sorted(UNVERIFIED.items()) if v},
    }


# ══════════════════════════════════════════════════════════════════════════
# 二、投影：原始 Table → 标准字段 Table（带逐位守卫）
# ══════════════════════════════════════════════════════════════════════════
def _cast(field_name: str, value):
    """沿用 `sections/mapping.py::_cast` 的口径（空值→None、按 REGISTRY 决定 str/int/float）。"""
    from .mapping import _cast as mapping_cast
    return mapping_cast(field_name, value)


def _project(table: Table, spec: ProductSpec, mode: ModeSpec) -> Table:
    """按 `mode` 的登记把一张原始表投影成标准字段表。

    **守卫（fail-closed）**：运行时列清单必须与 `mode.layout` 逐位相等。
    不相等就一列都不映射，原表原样返回，把两边清单写进
    `provenance.extra["macro_guard"]` —— 这与 `sections/mapping.py::COL_GUARD` 同一纪律
    （「同名列换语义」是本域最容易踩的雷：`yhhb` 002 的 N003 与 004 的 N003 是两种东西）。
    """
    actual = tuple(str(c) for c in table.cols)
    prov = table.provenance
    extra = dict(prov.extra) if prov is not None else {}
    extra["product"] = spec.product
    extra["macro_theme"] = spec.theme
    extra["macro_mode"] = mode.key
    extra["macro_mode_title"] = mode.title
    extra["macro_evidence"] = mode.evidence
    if mode.units:
        extra["macro_units"] = dict(mode.units)
    if mode.unmapped:
        extra["macro_unmapped"] = dict(mode.unmapped)

    if actual != tuple(mode.layout):
        extra["macro_guard"] = "cols-mismatch"
        extra["macro_expected_cols"] = list(mode.layout)
        extra["macro_actual_cols"] = list(actual)
        prov2 = prov.with_extra(**extra) if prov is not None else None
        return Table(cols=list(actual), rows=[dict(r) if isinstance(r, dict) else r
                                             for r in table.rows],
                     cmd=table.cmd, domain=table.domain, provenance=prov2)

    std_cols = [f for f in (mode.fields.get(c) for c in mode.layout) if f]
    rows: list[dict] = []
    for r in table.rows:
        if isinstance(r, dict):
            src = [r.get(c) for c in mode.layout]
        else:                                              # pragma: no cover - 协议层都给 dict
            src = list(r)
        row: dict = {}
        for i, c in enumerate(mode.layout):
            name = mode.fields.get(c)
            if not name:
                continue
            row[name] = _cast(name, src[i] if i < len(src) else None)
        row["raw"] = {c: (src[i] if i < len(src) else None)
                      for i, c in enumerate(mode.layout)}
        rows.append(row)

    prov2 = prov.with_extra(**extra) if prov is not None else prov
    return Table(cols=std_cols, rows=rows, cmd=table.cmd, domain=table.domain,
                 provenance=prov2)


# ══════════════════════════════════════════════════════════════════════════
# 三、对外 API
# ══════════════════════════════════════════════════════════════════════════
class Macro:
    """宏观域 typed 入口（`Client.macro`）。每次调用都**真取数**（不在这里做缓存）。"""

    def __init__(self, client):
        self._c = client

    # -- 内省 -------------------------------------------------------------
    def products(self) -> dict:
        """登记总表：`{"available": …, "unavailable": …, "unverified": …}`。"""
        return products_covered()

    def modes(self, product: str | None = None) -> dict:
        """列出模式登记：给 `product` 就只列它的；否则列全部。

        每项含 `title` / `layout`（期望列名）/ `params` / `count_slot` / `evidence` /
        `unmapped`（哪些列故意没给标准名、为什么）。
        """
        if product is not None:
            spec = self._spec(product)
            return {m.key: self._mode_brief(m) for m in spec.modes.values()}
        out: dict = {}
        for p, spec in sorted(AVAILABLE.items()):
            out[p] = {m.key: self._mode_brief(m) for m in spec.modes.values()}
        return out

    @staticmethod
    def _mode_brief(m: ModeSpec) -> dict:
        return {"title": m.title, "layout": list(m.layout),
                "params": [None if i == m.count_slot or i == m.day_slot else v
                           for i, v in enumerate(m.params)],
                "count_slot": m.count_slot, "day_slot": m.day_slot,
                "fields": dict(m.fields), "units": dict(m.units),
                "unmapped": dict(m.unmapped), "evidence": m.evidence}

    # -- 通用取数 ---------------------------------------------------------
    def fetch(self, product: str, mode: str | None = None, *,
              count: int | None = None, day: str | None = None,
              allow_empty: bool = False) -> Table:
        """按 `产品 + 模式` 取一张宏观表（其余 typed 方法都是它的薄封装）。

        * `count`：取多少期（只有登记了 `count_slot` 的模式才接受；其余传了会抛
          `ParamError` —— 静默忽略一个参数是本项目明令禁止的缺陷形态）；
        * `day`：仅 `world_cal` 的事件日历需要（`YYYY-MM-DD`）；
        * 产品/模式没登记 → `NotSupportedError`（并给出可用的模式清单），**不猜**。
        """
        spec = self._spec(product)
        key = mode or spec.default_mode
        mspec = spec.modes.get(key)
        if mspec is None:
            raise errors.NotSupportedError(
                "宏观产品 %r 没有登记模式 %r（已登记：%s）。"
                "未登记的模式一律不猜列义 —— 详见 `c.macro.modes(%r)` 与 "
                "reports/eval3/findings/M2-macro.md"
                % (product, key, ", ".join(sorted(spec.modes)), product))
        params = self._build_params(product, mspec, count=count, day=day)
        ds = self._c._get_with_params(spec.product, params, allow_empty=allow_empty)
        return _project(ds.table, spec, mspec)

    def _spec(self, product: str) -> ProductSpec:
        spec = AVAILABLE.get(product)
        if spec is not None:
            return spec
        if product in UNAVAILABLE:
            raise errors.NotSupportedError(
                "宏观产品 %r 没有 typed 通道：%s。"
                "用 `c.get(%r)` 只能拿到 N001… 原始列（列义无证据，本库不猜）"
                % (product, UNAVAILABLE[product], product))
        raise KeyError("没有这个宏观产品：%r（可用：%s）"
                       % (product, ", ".join(sorted(AVAILABLE))))

    def _build_params(self, product: str, mode: ModeSpec, *,
                      count: int | None, day: str | None) -> list:
        params = list(mode.params)
        if day is not None or mode.day_slot is not None:
            if mode.day_slot is None:
                raise errors.ParamError(
                    "%s/%s 不接受 day= 参数（该模式没有日期位）" % (product, mode.key))
            if day is None:
                raise errors.ParamError(
                    "%s/%s 需要 day=\"YYYY-MM-DD\"（财经事件日历按日取；"
                    "库不替你猜日期 —— 猜出来的日期会静默拿错数据）"
                    % (product, mode.key))
            _check_day(day)
            params[mode.day_slot] = day
        if count is not None:
            if mode.count_slot is None:
                raise errors.ParamError(
                    "%s/%s 不接受 count= 参数：该模式是固定形状（登记 layout 共 %d 列，"
                    "条数由服务端按模式决定，语料里没有可变证据）"
                    % (product, mode.key, len(mode.layout)))
            params[mode.count_slot] = int(count)
        return params

    # -- 主题方法（薄封装；每个都等价于 fetch(product, mode)）-------------
    def gdp(self, mode: str = "total", **kw) -> Table:
        """GDP：`total`（现价总量）/ `industries3`（三次产业）/ `by_industry`（11 行业）。"""
        return self.fetch("hg_gdp", mode, **kw)

    def fiscal(self, mode: str = "monthly", **kw) -> Table:
        """财政收支：`monthly`（3 组横表）/ `annual`（年度）/ `items`（5 个税种）。"""
        return self.fetch("czsz", mode, **kw)

    def calendar(self, mode: str = "trading_days", **kw) -> Table:
        """日历：`trading_days`（未来 366 天）/ `events` / `events_month`（都要 `day=`）。"""
        return self.fetch("world_cal", mode, **kw)

    def holidays(self, mode: str = "all", **kw) -> Table:
        """全球交易所节假日（189 行，6 列；`remark` 20 行非空，不是占位）。"""
        return self.fetch("holiday", mode, **kw)

    def prosperity(self, mode: str = "index", **kw) -> Table:
        """景气指数：`index`（PMI 序列，注意 ColDes **没有 N002**）/ `components`（分量占比）。"""
        return self.fetch("jqzs", mode, **kw)

    def prices(self, mode: str = "by_code", **kw) -> Table:
        """物价指数（国内）：`by_code` / `four_groups` / `quarterly`。"""
        return self.fetch("hg_wjzs", mode, **kw)

    def bank(self, mode: str = "main", **kw) -> Table:
        """银行报表：`main` / `by_group`（ColDes 乱序）/ `breakdown`（合计+4 分项）。"""
        return self.fetch("yhhb", mode, **kw)

    def investment(self, mode: str = "total", **kw) -> Table:
        """固定资产投资：`total` / `cumulative` / `monthly`。"""
        return self.fetch("hg_tz", mode, **kw)

    def employment(self, mode: str = "two_series", **kw) -> Table:
        """就业保障：`two_series` / `annual` / `three_groups`。"""
        return self.fetch("hg_jybz", mode, **kw)

    def income(self, mode: str = "income", **kw) -> Table:
        """居民收入：`income`（可支配收入）/ `spending`（消费支出）。"""
        return self.fetch("hg_jmsh", mode, **kw)

    def industry(self, mode: str = "growth", **kw) -> Table:
        """工业经济：`growth`（当月/累计同比）/ `revenue`（营收累计）。"""
        return self.fetch("gyjj", mode, **kw)

    def output(self, mode: str = "series_1", **kw) -> Table:
        """产量序列：`series_1` / `series_2`（序列身份与单位未证明）。"""
        return self.fetch("hg_nysc", mode, **kw)

    def retail(self, mode: str = "total", **kw) -> Table:
        """社零：`total`（合计）/ `code_163001`（指标码）。⚠️ 同名 N002 语义不同。"""
        return self.fetch("dyjjyxf", mode, **kw)

    def us_gdp(self, mode: str = "quarterly", **kw) -> Table:
        """美国 GDP：`quarterly` / `persons_a` / `persons_b`（后两条是人数序列）。"""
        return self.fetch("gmjjyjy", mode, **kw)

    def us_rate(self, mode: str = "policy_rate", **kw) -> Table:
        """美联储利率决议（**最新一次快照**，不是历史序列）。"""
        return self.fetch("mghg_hbzc", mode, **kw)

    def us_money(self, mode: str = "stock", **kw) -> Table:
        """美国货币/信贷：`stock` / `stock_2` / `breakdown`。"""
        return self.fetch("mghg_mlc", mode, **kw)

    def us_prices(self, mode: str = "m001", **kw) -> Table:
        """美国物价指数水平：`m001` / `m003` / `m005` / `m007`（口径未破解）。"""
        return self.fetch("macro_wj", mode, **kw)

    def us_balance(self, mode: str = "snapshot", **kw) -> Table:
        """美国存量快照（4 个存量值，身份未破解）。"""
        return self.fetch("macro_yh", mode, **kw)


def _check_day(day: str) -> None:
    """日期必须是 `YYYY-MM-DD`（与语料里的请求参数逐字一致；写错会被服务端当空值）。"""
    try:
        _date.fromisoformat(str(day))
    except ValueError:
        raise errors.ParamError("day 必须是 YYYY-MM-DD，收到 %r" % (day,))
