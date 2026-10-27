# -*- coding: utf-8 -*-
"""列名映射表：`产品名 + 服务端原始列名 → 标准字段名`，以及 `apply()`。

三件东西
--------
1. `FIELD_MAP`：`{产品名: {服务端列名: 标准字段名}}`。**每条映射都带出处注释**。
2. `_TEMPLATE_COLS`：文档记录的「主表列清单」，现在**只做交叉校验**（不再是修复层）。
   协议层（`client.Client._make_table`）已把 `Table.cols` 改成**主表自己的列名**、
   与每行取值严格对齐（并集挪进 `meta["cols_union"]`），所以 `apply()` 直接信
   `table.cols` 即可。`_TEMPLATE_COLS` 用来在实际取数时顺手核对「运行时主表列」
   是否等于「文档清单」，不一致就记进 `meta["template_mismatch"]` —— 这是
   一次不要钱的实网一致性检查。
3. `apply(table, product) -> Table`：返回新 `Table`，`cols` = 该产品映射到的
   标准字段（按映射顺序，**不含 `"raw"`**）；每行 `{标准字段: 值, …, "raw": {原始列: 值}}`，
   所以 `len(row) == len(cols) + 1`。`raw` 是 sidecar（原始列备份），不是数据列，
   刻意不占 `Table.shape` 的列数 —— `t.column("raw")` 照样能取。
   另有 `COL_GUARD`：几张**报表模板会随行业变**的命令（银行股利润表实测
   25 期 × 57 列，与工商企业的 38 列根本不是同一张表），只有当运行时列清单
   与文档**完全一致**时才允许套映射，否则只给 `raw` 并在 meta 里说明原因。

证据来源（**只有这四处**，别处找不到就当不知道）
------------------------------------------------
* 实机回放样本（449 条命令的 行×列 / 列名 / 首行值）
* 列名映射核对（430 条 命令+RS+列名 → 字段名）
* 字段字典：见 `docs/VERIFICATION.md`
* 各业务域文档（F10 与个股资料、宏观与财报）

取值口径（见 `API设计.md` §2.7 通用约定）
----------------------------------------
* 服务端给 `""` / `"—"` / `"--"` / `"-"` / `"/"` 的位填 `None`（**不是 `0.0`**，
  0 和「没有」必须能区分）。
* 标准字段一律 `snake_case` + 单位后缀：`_pct` 百分数（已是百分数，别再 ×100）、
  `_yuan` 元、`_vol` 股、`_wan_yuan` 万元。
* 含义没定论的列**不给标准名**，只留在 `raw` 里 —— 宁可少映射，不许错映射。
"""
from __future__ import annotations

from ..errors import NotSupportedError
from ..models import Table

__all__ = ["FIELD_MAP", "PRODUCTS", "apply"]


# ── 空值标记 ──────────────────────────────────────────────────────────────
# 出处: F10与个股资料.md §2.8（null 极密集，""/null 混用）+ zg_scm 样本里的 "—"
# 出处: F10与个股资料.md §四 jyfx_zbyz 样本里的 "--"
_NULLS = frozenset(["", "-", "--", "—", "——", "/", "null", "None", "nan", "NaN"])

# 保持字符串的标准字段（代码/名称/日期/文本/URL/枚举词）——不能被 float() 吃掉
STR_FIELDS = frozenset([
    "report_period", "report_date", "date", "include_date",
    "name", "code", "internal_id",
    "product_line_name", "product_id", "product_name",
    "pdf_url", "title",
    "share_class", "issue_method", "pricing_method",
    "lead_underwriter", "co_underwriters", "issue_target", "issue_note",
    "risk_level", "rating_text", "trend_text",
    "benchmark_name", "industry_name",
    "category_text", "category_code", "board_code", "board_name",
    "reason_short", "reason_long", "reason_text",
    "concept_name", "entity_name", "tag_name",
    "highlight_name", "highlight_note", "item_name", "content_text",
    "change_reason", "main_business", "business_scope", "internal_key",
])

# 取整数更自然的标准字段（排名 / 户数 / 市场码 …）
INT_FIELDS = frozenset([
    "market", "holder_count", "rank", "hot_rank", "board_rank",
    "industry_rank", "industry_size", "market_rank", "market_size",
    "universe_size", "ipo_count", "weight_code", "item_seq",
])


# ══════════════════════════════════════════════════════════════════════════
# 一、列名映射表
# ══════════════════════════════════════════════════════════════════════════
FIELD_MAP: dict[str, dict[str, str]] = {

    # ── 资产负债表全表（CWServ.ph_agf10_cw_zcfzb，23 期 × 81 列）──────────
    # ctl=0x1A，参数 ["<6位代码>"]（无 p1 子码）；RS0=isjr、RS1=真表。
    # 只有 8 个会计恒等式钉住的列敢给名字；其余 72 个 N0xx 科目名接口不给。
    "fin": {
        "N001": "report_period",                 # 出处: 实测样本总表 #9（23×81）· F10与个股资料.md §三 cw_zcfzb「N001=报告期(中文期别)」
        "Tdate": "report_date",                  # 出处: F10与个股资料.md §2.2（Tdate = 该行报告期末日 "2026-06-30"，按列名取别按位置取）
        "N002": "monetary_fund_yuan",            # 出处: F10与个股资料.md §三 交叉验证（N002=58154644.44 与 cw_cwfxxq RS2 同值 → 货币资金；置信度中，待实网复核）
        "N015": "total_current_assets_yuan",     # 出处: F10与个股资料.md §三 恒等式 N038=N015+N037（600303/600519 各 23/23 全对）→ 流动资产合计
        "N037": "total_noncurrent_assets_yuan",  # 出处: F10与个股资料.md §三 恒等式 N038=N015+N037（23/23）→ 非流动资产合计
        "N038": "total_assets_yuan",             # 出处: F10与个股资料.md §三 恒等式 N038=N067+N077（23/23）→ 资产总计 3104851614.01
        "N067": "total_liabilities_yuan",        # 出处: F10与个股资料.md §三 恒等式 N038=N067+N077（23/23）→ 负债合计 2203108875.58
        "N077": "total_equity_yuan",             # 出处: F10与个股资料.md §三 恒等式 N038=N067+N077（23/23）→ 所有者权益合计 901742738.43
        "N078": "equity_parent_yuan",            # 出处: F10与个股资料.md §三 恒等式 N077=N078+N079（23/23）→ 归属母公司股东权益
        "N079": "minority_interest_yuan",        # 出处: F10与个股资料.md §三 恒等式 N077=N078+N079（23/23）→ 少数股东权益
    },

    # ── 利润表全表（CWServ.ph_agf10_cw_lyb，24 期 × 38 列）───────────────
    # ⚠️ 银行股实测 25 期 × 57 列（另一套模板），列清单只能当模板、不能当断言。
    "lyb": {
        "N001": "report_period",                 # 出处: 实测样本总表 #4（24×38）· F10与个股资料.md §三 cw_lyb（N001=报告期）
        "Tdate": "report_date",                  # 出处: F10与个股资料.md §2.2（38 列 = N001…N034, Tdate, N035…N037，Tdate 在中间）
        "N002": "total_revenue_yuan",            # 出处: F10与个股资料.md §三 cw_lyb 列义锚点（N002=505576951.99=营业总收入，三处交叉 ✅）
        "N003": "revenue_yuan",                  # 出处: F10与个股资料.md §三 cw_lyb（N003 与 N002 等值 → 营业收入）
        "N024": "net_profit_yuan",               # 出处: F10与个股资料.md §三 恒等式 N024=N025+N026（24/24）→ 净利润 -129298377.65
        "N025": "net_profit_parent_yuan",        # 出处: F10与个股资料.md §三 恒等式 N024=N025+N026（24/24）→ 归属母公司股东的净利润
        "N026": "minority_profit_yuan",          # 出处: F10与个股资料.md §三 恒等式 N024=N025+N026（24/24）→ 少数股东损益
    },

    # ── 现金流量表全表（CWServ.ph_agf10_cw_xjllb，23 期 × 64 列）─────────
    "xjllb": {
        "N001": "report_period",                 # 出处: 实测样本总表 #7（23×64）· F10与个股资料.md §三 cw_xjllb（N001=报告期）
        "Tdate": "report_date",                  # 出处: F10与个股资料.md §2.2（64 列 = N001…N063, Tdate，Tdate 在末尾）
        "N005": "operating_cash_inflow_yuan",    # 出处: F10与个股资料.md §三 恒等式 N005=N010+N011（23/23）→ 经营活动现金流入小计 364261091.43
        "N010": "operating_cash_outflow_yuan",   # 出处: F10与个股资料.md §三 恒等式 N005=N010+N011（23/23，N011 已确认是经营净额）→ 经营活动现金流出小计 405827161.47
        "N011": "operating_net_cash_yuan",       # 出处: F10与个股资料.md §三「N011=-41566070.04 = 经营活动产生的现金流量净额 ✅」（三命令同值）
        "N017": "investing_cash_inflow_yuan",    # 出处: F10与个股资料.md §三 恒等式 N017=N022+N023（投资活动同一结构，23/23）→ 投资活动现金流入小计
        "N022": "investing_cash_outflow_yuan",   # 出处: F10与个股资料.md §三 恒等式 N017=N022+N023（23/23）→ 投资活动现金流出小计（位置与 N010 同构）
        "N023": "investing_net_cash_yuan",       # 出处: F10与个股资料.md §三 恒等式 N017=N022+N023（23/23）→ 投资活动现金流量净额（位置与 N011 同构）
        "N037": "financing_cash_inflow_yuan",    # 出处: F10与个股资料.md §三 恒等式 N037=N035+N036（筹资活动同一结构，23/23）→ 筹资活动现金流入小计
        "N035": "financing_cash_outflow_yuan",   # 出处: F10与个股资料.md §三 恒等式 N037=N035+N036（23/23）→ 筹资活动现金流出小计（位置与 N010 同构）
        "N036": "financing_net_cash_yuan",       # 出处: F10与个股资料.md §三 恒等式 N037=N035+N036（23/23）→ 筹资活动现金流量净额（位置与 N011 同构）
    },

    # ── 主要财务指标矩阵（CWServ.ph_agf10_cw_zyzb，11 期 × 67 列）────────
    # 列名跳段（N015 之后直接 N101），按列名取、别用列号算术。
    "zyzb": {
        "N001": "report_period",                 # 出处: 实测样本总表 #10（11×67）· F10与个股资料.md §三 cw_zyzb「N001=报告期(中文期别)」
        "Tdate": "report_date",                  # 出处: F10与个股资料.md §2.2 / §三 cw_zyzb 列名清单（…N021, Tdate, N022）
        "N002": "eps_yuan",                      # 出处: F10与个股资料.md §三 cw_zyzb 列义锚点（N002=-0.1300=EPS，与 zyzbxq T056 同值 ✅）
        # 出处: 实网 2026-10-25 实测（600519）N005=200.9898 与 cw_zyzbxqy_sy T059 逐位相同，
        #       量级 = 归母权益/股本 ≈ 201 元/股 → 每股净资产（BPS，置信度中高）
        "N005": "bps_yuan",
        # 注：N016（扣非净利润，-7765250.97）在 RS1 小附表里，主表 RS0 没有这一列，故不映射。
    },

    # ── 主要指标 · 行业/机构对比（CWServ.ph_agf10_cw_zyzbxqy_sy，4 个 RS）──
    # 返回的最大表是 RS0（13 列）：N001,T056,T059,T061,T060,T062,T064,T063,
    # T065,T066,T067,JGID,Tdate。T0xx 是全局指标码，跨命令不可复用。
    "zyzbxq": {
        "N001": "report_period",                 # 出处: 实测样本总表 #11 · F10与个股资料.md §三 cw_zyzbxqy_sy「RS0 列名」表 + 列名映射总表（R2/RS1 N001=plugin date）
        "Tdate": "report_date",                  # 出处: F10与个股资料.md §三 cw_zyzbxqy_sy RS0 列名表（末列 Tdate = 报告期末日）
        "T056": "eps_yuan",                      # 出处: F10与个股资料.md §三 cw_zyzbxqy_sy 列义锚点（T056=-0.1300=EPS，与 cw_zyzb N002 同值 ✅）
        # 出处: 同上 + **实网 2026-10-25 实测（600519）**：JGID = "gssh0600519"，是
        #       「gssh + 6 位代码」内部键 —— **不是**文档 §2.7 说的 code_to_id 数字 ID，
        #       故字段名叫 internal_key 而不是 internal_id（宁可按实测命名）。
        "JGID": "internal_key",
        # 出处: F10与个股资料.md §三 cw_zyzbxqy_sy「T059=每股净资产类（与 cw_zyzb 同值 ✅）」
        #       + 实网 600519：T059=200.9898 与 cw_zyzb N005=200.9898 逐位相同；
        #       量级自洽（归母权益 2513 亿 / 约 12.5 亿股 ≈ 201 元/股）。
        #       注：该文档把列号写作「cw_zyzb N004」，实测对应 N005（N004=35.6112）。
        "T059": "bps_yuan",
    },

    # ── 财报公告列表（CWServ.ph_agf10_cw_cwbg，14 行 × 3 列）─────────────
    "cwbg": {
        "N001": "report_period",                 # 出处: 实测样本总表 #2（14×3）· F10与个股资料.md §三 cw_cwbg「列义」N001=报告期(中文期别)
        "N002": "pdf_url",                       # 出处: F10与个股资料.md §三 cw_cwbg「列义」N002=公告 PDF 直链（巨潮 dataclouds.cninfo.com.cn，http 明文）
        "N003": "title",                         # 出处: F10与个股资料.md §三 cw_cwbg「列义」N003=公告标题（含公司简称）
    },

    # ── 最新报告期探测（CWServ.ph_agf10_cw_tjcj，1 行 × 2 列）────────────
    "tjcj": {
        "zqjc": "name",                          # 出处: 实测样本总表 #6（1×2，列 zqjc/N001）· F10与个股资料.md §三 cw_tjcj「zqjc=简称 ✅」
        "N001": "report_period",                 # 出处: F10与个股资料.md §三 cw_tjcj「N001=服务端认定的最新报告期 ✅」（中文期别）
    },

    # ── 财务首页抬头（CWServ.ph_agf10_cw_sy_v2，1 行 × 2 列）─────────────
    "zysy": {
        "zqjc": "name",                          # 出处: 实测样本总表 #5（1×2，列 zqjc/hybs，首行 ["大鹏工业","4"]）· F10与个股资料.md §三 cw_sy_v2「zqjc=证券简称 ✅」
        # hybs = 「行业标识」（实测只见过 "4"，取值集合未穷举，F10与个股资料.md §三）→ 不映射，留 raw。
    },

    # ── 公司概况 / 发行上市（CWServ.ph_agf10_gsjj，1 行 × 19 列）─────────
    # 走 p1=00201 分支（1×19）；p1=00101 是另一张 7×43 的表，见 f10.py 模块 docstring。
    "gsjj": {
        "N001": "share_class",                   # 出处: 实测样本总表 #15（1×19，首行 "A股 2000-12-26 …"）· F10与个股资料.md §四 gsjj 列义表（N001=股票类别）
        "N002": "list_date",                     # 出处: F10与个股资料.md §四 gsjj 列义表（N002=上市日期 2000-12-26）
        "N003": "issue_method",                  # 出处: F10与个股资料.md §四 gsjj 列义表（N003=发行方式 上网定价发行）
        "N004": "pricing_method",                # 出处: F10与个股资料.md §四 gsjj 列义表（N004=定价方式 定价发行）
        "N005": "par_value_yuan",                # 出处: F10与个股资料.md §四 gsjj 列义表（N005=每股面值（元）1.00）
        "N006": "issue_price_yuan",              # 出处: F10与个股资料.md §四 gsjj 列义表（N006=发行价（元）8.800）
        "N007": "issue_vol",                     # 出处: F10与个股资料.md §四 gsjj 列义表（N007=发行数量（股）40000000）
        "N008": "issue_total_vol",               # 出处: F10与个股资料.md §四 gsjj 列义表（N008=发行总量（股））
        # N009 / N010 文档明确「未定论」（1.820 / 4.770）→ 不映射
        "N011": "proceeds_gross_yuan",           # 出处: F10与个股资料.md §四 gsjj（N011=募集资金总额，8.800×40000000=352000000 验算通过 ✅）
        "N012": "proceeds_net_yuan",             # 出处: F10与个股资料.md §四 gsjj 列义表（N012=募集净额 338160000 = 总额 − 发行费用）
        # N013 / N014 / N015 文档标「推断」（发行市盈率/中签率/发行费用）→ 不映射
        "N016": "lead_underwriter",              # 出处: F10与个股资料.md §四 gsjj 列义表（N016=主承销商 国泰海通证券）
        "N017": "co_underwriters",               # 出处: F10与个股资料.md §四 gsjj 列义表（N017=副主承销商/推荐人，逗号分隔）
        "N018": "issue_target",                  # 出处: F10与个股资料.md §四 gsjj 列义表（N018=发行对象，长文本）
        "N019": "issue_note",                    # 出处: F10与个股资料.md §四 gsjj 列义表（N019=其他说明/利润共享，长文本）
    },

    # ── 经营分析（CWServ.ph_agf10_jyfx，p1=00101 分支）───────────────────
    # 该分支回 2 个 RS：RS0 1×4（主营简述/经营范围/UI文案/内部键）、RS1 2×2（产品线）。
    # **实网实测（2026-10-25，600519）**：主表是 **RS0（1 行 × 4 列 N001…N004）**，
    # 不是 RS1 —— 单看「单元格数」RS0 与 RS1 都是 4，协议层按 (cells, rows) 选了行多
    # 的那张，实际拿到的是 RS0。所以按 RS0 的列义映射（RS1 的产品线取不到）。
    "jyfx": {
        "N001": "main_business",                 # 出处: 实网 600519 实测（RS0 N001=「茅台酒及系列酒的生产与销售」）· F10与个股资料.md §四 jyfx「N001=主营业务简述」
        "N002": "business_scope",                # 出处: 实网 600519 实测（RS0 N002=经营范围全文，上百字）· §四 jyfx「N002=经营范围全文」
        # N003 是写死的 UI 文案「查看详情」，N004 是内部键 gssh+代码 —— 都不是业务字段 → 留 raw
    },

    # ── 经营分析 · 指标/变动说明（CWServ.ph_agf10_jyfx_zbyz，13 行 × 7 列）──
    "zbyz": {
        "N001": "report_date",                   # 出处: 实测样本总表 #20（13×7）· F10与个股资料.md §四 jyfx_zbyz 样本首行首值 "2022-12-31"（日期）
        # 文档只说「子公司/参股公司 + 出资额 + 持股比例这类结构」，金额列/比例列对应哪列没定论
        # → 只映射「主体名」（样本第 2 值就是公司全名，无歧义），其余留 raw。
        "N002": "entity_name",                   # 出处: F10与个股资料.md §四 jyfx_zbyz 样本首行第 2 值 "北京曙光智行新能源汽车科技有限公司"
    },

    # ── 股东户数 / 人均持股（CWServ.ph_agf10_gbgd，5~7 期 × 3 列）────────
    "gbgd": {
        "N001": "report_date",                   # 出处: 实测样本总表 #14（7×3，首行 "2026-06-30 37143 2.49"）· F10与个股资料.md §七之二 gbgd 列义表
        "N002": "holder_count",                  # 出处: F10与个股资料.md §七之二 gbgd 列义表（N002=股东户数；与 zxts5 N003 逐值交叉 ✅）
        "N003": "per_capita_hold_amt_wan_yuan",  # 出处: F10与个股资料.md §七之二 gbgd 列义表（N003=人均持股金额（万元）；与 zxts5 N002 逐值交叉 ✅）
    },

    # ── 分红/融资统计（CWServ.ph_agf10_fhrz，1 行 × 12 列）───────────────
    # 6 组「次数 + 金额」，只有第 3 组确认是首发（IPO），其余 5 组口径未定论
    # （F10与个股资料.md §八 #9），所以只映射第 3 组。
    # ⚠️ 实网 600519 反证：第 1 组是 (30, 4011.45 亿) —— 茅台 IPO 只募了 22 亿，
    #    4011 亿更像「累计现金分红」，所以第 1 组**未必**是增发（文档原假设），
    #    更加不能映射。第 3 组 (1, 22.44 亿) 与 gsjj N011 逐位相同，确认是 IPO。
    "fhrz_agf10": {
        "N005": "ipo_count",                     # 出处: 实测样本总表 #13（1×12）· F10与个股资料.md §七之二 fhrz 列义表（第 3 组 = 首发次数）
        "N006": "ipo_proceeds_yuan",             # 出处: F10与个股资料.md §七之二 fhrz（N006=352000000 与 gsjj N011 跨命令逐位相同 ✅）
    },

    # ── 行业/板块成员（CWServ.ph_agf10_hypm，RS1 = 31 行 × 4 列）─────────
    # RS1 列名以 N003 开头（前两列名字被 RS0 的计数表占用）。
    "hypm": {
        "N003": "name",                          # 出处: 实测样本总表 #16（31×4，列 N003/N004/sc/lx）· F10与个股资料.md §四 hypm「RS1 N003=名称」
        "N004": "code",                          # 出处: F10与个股资料.md §四 hypm「RS1 N004=代码」
        "sc": "market",                          # 出处: F10与个股资料.md §四 hypm「sc=市场（0=深/1=沪/2=北）」
        "lx": "category_code",                   # 出处: F10与个股资料.md §四 hypm「lx=类别（0=A股，其余未穷举）」
    },

    # ── 热点题材 / 公司资料文本块（CWServ.ph_agf10_rdtc，24 行 × 3 列）───
    "rdtc": {
        "N001": "item_name",                     # 出处: 实测样本总表 #22（24×3，首行 "公司简介 …"）· F10与个股资料.md §四 rdtc「实际是资料条目名 + 正文」
        "N002": "content_text",                  # 出处: F10与个股资料.md §四 rdtc（N002=正文，首行是公司简介原文）
        # N003 实测全空串 → 不映射
    },

    # ── 风险等级（CWServ.ph_agf10_sl_v2，1 行 × 3 列）────────────────────
    "sl_v2": {
        "zqjc": "name",                          # 出处: 实测样本总表 #24（列 zqjc/zqdm/N001）· F10与个股资料.md §七 sl_v2「zqjc=简称」
        "zqdm": "code",                          # 出处: F10与个股资料.md §七 sl_v2「zqdm=代码」
        "N001": "risk_level",                    # 出处: F10与个股资料.md §七 sl_v2「N001=风险等级（中文）中低风险」
    },

    # ── 财务分析详情（CWServ.ph_agf10_cw_cwfxxq，5 个 RS）─────────────────
    # 该命令回 5 个 RS（1×10 / 19×29 / 4×46 / 45×7 / 4×5）。两份文档对「主表是哪张」
    # 有分歧（实测样本总表 #3 记 45×7 = RS3 变动说明；域文档记 RS1 = 19×29），
    # 所以**交给运行时决定**：协议层按单元格数挑主表并带出该表自己的列名。
    # 下面这套列义只对 **RS3「重大变动说明」** 成立，靠 `COL_GUARD` 逐位比对列清单
    # 来放闸 —— 运行时若拿到 29 列那张，映射自动不套（只给 raw，不猜）。
    "cwfxxq": {
        "N001": "report_date",        # 出处: F10与个股资料.md §三 cw_cwfxxq RS3 列义表（N001=报告期，格式 YYYY/MM/DD "2026/06/30"）
        "N002": "item_seq",           # 出处: 同上 RS3 列义表（N002=序号 1,2,…）
        "N003": "item_name",          # 出处: 同上 RS3 列义表（N003=报表项目中文名，如「应收票据」）
        "N004": "end_amount_yuan",    # 出处: 同上 RS3 列义表（N004=期末金额（元）72483587.75）
        "N005": "begin_amount_yuan",  # 出处: 同上 RS3 列义表（N005=期初金额（元）133476412.79）
        "N006": "change_pct",         # 出处: 同上 RS3 列义表（N006=变动幅度 %，(末-初)/初 逐行验算通过 ✅）
        "N007": "change_reason",      # 出处: 同上 RS3 列义表（N007=变动原因，公司原文中文；全 F10 唯一自带解释的列）
    },

    # ── 交易数据（CWServ.ph_agf10_jysj，1 行 × 3 列）─────────────────────
    # 实测 [null, null, "0"]，三个列义都没定论（实测样本总表 #21 · F10与个股资料.md §四 jysj「待确认」）→ 不映射。
    "jysj": {},

    # ── 基本面诊断（CWServ.ph_agf10_zg_jby，1 行 × 10 列）────────────────
    "zgjby": {
        "N001": "date",                          # 出处: 实测样本总表 #25（1×10）· F10与个股资料.md §五 zg_jby「N001=日期 YYYYMMDD」
        "N002": "score",                         # 出处: F10与个股资料.md §五 zg_jby「N002=评分 1.93」
        "N003": "rank",                          # 出处: F10与个股资料.md §五 zg_jby「N003=排名 294」
        "N004": "universe_size",                 # 出处: F10与个股资料.md §五 zg_jby「N004=样本总数 342」（✅ 自检 N003 ≤ N004）
        "N005": "rating_text",                   # 出处: F10与个股资料.md §五 zg_jby「N005=评级词 堪忧」（中文结论词，不是数值）
        # N006…N010：插件字段是 jzfx_ylnl/cznl/yynl/czznl/xjnl（列名映射总表 zg_jby 行），
        # 拼音缩写到中文维度的对应没有文档定论 → 不映射，留 raw。
    },

    # ── 近 17 日相对强弱（CWServ.ph_agf10_zg_jsm，17 行 × 7 列）──────────
    "zgjsm": {
        "N001": "date",                          # 出处: 实测样本总表 #26（17×7）· F10与个股资料.md §五 zg_jsm「N001=日期」
        "N002": "benchmark_name",                # 出处: F10与个股资料.md §五 zg_jsm「N002=基准名（沪深300）」
        "N003": "benchmark_chg_pct",             # 出处: F10与个股资料.md §五 zg_jsm「N003=基准区间涨跌 %」（已是百分数，别再 ×100）
        "N004": "industry_name",                 # 出处: F10与个股资料.md §五 zg_jsm「N004=所属行业名」
        "N005": "industry_chg_pct",              # 出处: F10与个股资料.md §五 zg_jsm「N005=行业区间涨跌 %」
        "N006": "name",                          # 出处: F10与个股资料.md §五 zg_jsm「N006=本股名」
        "N007": "chg_pct",                       # 出处: F10与个股资料.md §五 zg_jsm「N007=本股区间涨跌 %」
    },

    # ── 市场面诊断（CWServ.ph_agf10_zg_scm，p1=ztms 分支，1 行 × 8 列）───
    "zgscm": {
        "zqjc": "name",                          # 出处: 实测样本总表 #27 · F10与个股资料.md §五 zg_scm「列名（ztms 分支）: zqjc,N001…N007」
        "N002": "hot_rank",                      # 出处: 列名映射总表 zg_scm R1/RS0（ztms 分支：N002=插件字段 rqrd_pm=人气排名）
        # N001 = rqrd_cs、N003 = rqrd_ggs、N004…N007 属另一分支字段名 —— 含义均未定论 → 留 raw
    },

    # ── 题材标签 + 业绩预期（CWServ.ph_agf10_zg_yq，RS1 = 4 行 × 11 列）──
    # RS1 列名 = N002…N011 + T001（N001 这个名字被 RS0 的计数表占了）。
    "zgyq": {
        "N002": "concept_name",                  # 出处: 实测样本总表 #28（4×11）· F10与个股资料.md §五 zg_yq「N002=概念/题材名」
        "N003": "include_date",                  # 出处: F10与个股资料.md §五 zg_yq「N003=入选日期 YYYYMMDD」
        # N004 实测为空 → 不映射
        "N005": "reason_short",                  # 出处: F10与个股资料.md §五 zg_yq「N005=入选理由（短版）」
        "N006": "reason_long",                   # 出处: F10与个股资料.md §五 zg_yq「N006=入选理由（长版，含客户与业务细节）」
        "N007": "category_text",                 # 出处: F10与个股资料.md §五 zg_yq「N007=分类（其它研究/新能源车）」
        # N008…N011 = 4 个窗口的区间涨跌 %，哪个窗口对应哪列没定论 → 不映射
        "T001": "rank",                          # 出处: F10与个股资料.md §五 zg_yq「T001=排名 2979」
    },

    # ── 综合评分卡（CWServ.ph_agf10_zg_zs，1 行 × 13 列，首列是 zqjc）─────
    # 两处独立来源对 N001…N005 的读法一致（F10与个股资料.md §五 zg_zs 列义表 +
    # 列名映射总表 zg_zs R1/RS0 的 zhpf 分支插件字段），只是命名粗细不同。
    "zgzs": {
        "zqjc": "name",                          # 出处: 实测样本总表 #29（1×13，首列 zqjc）· F10与个股资料.md §五 zg_zs「zqjc=证券简称 ✅」
        "N001": "score",                         # 出处: F10与个股资料.md §五 zg_zs「N001=综合评分 2.3733」+ 列名映射总表 N001=gsgk_zhpf
        "N002": "industry_rank",                 # 出处: F10与个股资料.md §五 zg_zs「N002=排名 334」+ 列名映射总表 N002=gsgk_hypm（行业排名）→ 行业排名
        "N003": "industry_size",                 # 出处: F10与个股资料.md §五 zg_zs「N003=样本总数 342」+ 列名映射总表 N003=gsgk_hyzs（行业总数）→ 行业总数
        "N004": "market_rank",                   # 出处: F10与个股资料.md §五 zg_zs「N004=全市场排名 5472（推断）」+ 列名映射总表 N004=gsgk_scpm（市场排名）
        "N005": "market_size",                   # 出处: F10与个股资料.md §五 zg_zs「N005=全市场家数 5575（推断）」+ 列名映射总表 N005=gsgk_zggs（总个数）
        # N006 = gsgk_cgbfb（30 位小数字符串 0.0186…），口径未定论 → 不映射
        "N007": "trend_text",                    # 出处: F10与个股资料.md §五 zg_zs「N007=趋势结论（中文）✅ 弱势看空」
        "N008": "date",                          # 出处: F10与个股资料.md §五 zg_zs「N008=日期 YYYYMMDD」+ 列名映射总表 N008=gsgk_date
        # N009 恒为 null；N010…N012 文档标「推断」→ 不映射
    },

    # ── 个股标签 / 所属概念（CWServ.ph_agf10_zp，p1=00104，15 行 × 2 列）──
    "zp": {
        "N0101": "tag_name",                     # 出处: 实测样本总表 #31（15×2，列 N001/N0101）· F10与个股资料.md §七 zp「N0101=标签名」；实网 600519 首行 = "机构重仓"
        # ⚠️ 文档说该分支 N001 恒为 null，但**实网 600519 的 N001 有值**（首行 "基金扎堆"，
        #    与 N0101 的 "机构重仓" 并列，像是「类别 + 标签」但无从确定）→ 留 raw。
    },

    # ── 个股亮点标签（CWServ.ph_agf10_zp_ggld，RS0 = N 行 × 3 列）─────────
    "zpggld": {
        "N001": "highlight_name",                # 出处: 实测样本总表 #32（2×3）· F10与个股资料.md §七之二 zp_ggld「N001=亮点标签名」
        "N002": "highlight_note",                # 出处: F10与个股资料.md §七之二 zp_ggld「N002=命中说明（中文，常带数字）」
        "N003": "weight_code",                   # 出处: F10与个股资料.md §七之二 zp_ggld「N003=等级/权重码（5、3）」
        # RS1 的 zs（总条数）不在最大表里 → 拿不到（见 f10.py docstring）
    },

    # ── 概念题材命中（CWServ.ph_agf10_zxts3，RS1 = 9 行 × 5 列）──────────
    "zxts3": {
        "N001": "concept_name",                  # 出处: 实测样本总表 #36（9×5，列 N001…N005）· F10与个股资料.md §六 zxts3「N001=概念名」
        "N002": "reason_text",                   # 出处: F10与个股资料.md §六 zxts3「N002=命中理由（中文）」
        "N003": "category_code",                 # 出处: F10与个股资料.md §六 zxts3「N003=类别码（2）」
        "N004": "board_code",                    # 出处: F10与个股资料.md §六 zxts3「N004=板块代码 880xxx」（与 RS0 的板块码列表可对上 ✅）
        "N005": "board_rank",                    # 出处: F10与个股资料.md §六 zxts3「N005=该板块内排名」
    },

    # ── 主营构成（CWServ.ph_agf10_zxts4，RS0 = 6 行 × 4 列）──────────────
    "zxts4": {
        "N001": "product_name",                  # 出处: 实测样本总表 #37（6×4，列 N001/N002/N003/bs）· F10与个股资料.md §六 zxts4「N001=产品/业务名」
        "N002": "revenue_yuan",                  # 出处: F10与个股资料.md §六 zxts4「N002=收入（元）」
        "N003": "revenue_pct",                   # 出处: F10与个股资料.md §六 zxts4「N003=占比（%），可直接验算合计≈100」
        # bs = 标识（实测恒为 2），未定论 → 不映射
    },

    # ── 股东户数 / 人均持股（CWServ.ph_agf10_zxts5，RS0 = 3 行 × 4 列）───
    "zxts5": {
        "N001": "report_period",                 # 出处: 实测样本总表 #38（3×4，首行 "2026-06-30 00:00:00 …"）· F10与个股资料.md §六 zxts5「N001=报告期」
        "N002": "per_capita_hold_amt_wan_yuan",  # 出处: F10与个股资料.md §六 zxts5「N002=人均持股金额（万元）」+ 与 gbgd N003 交叉 ✅
        "N003": "holder_count",                  # 出处: F10与个股资料.md §六 zxts5「N003=股东户数」+ 与 gbgd N002 交叉 ✅
        "N004": "holder_count_chg_pct",          # 出处: F10与个股资料.md §六 zxts5「N004=户数环比变化 %」
        # RS1 的 N005（最新期别）不在最大表里 → 拿不到
    },

    # ── 综合提示（CWServ.ph_agf10_zxts，8 个 RS）────────────────────────
    # 返回的最大表是 RS2（10 期 × 9 列 = N001…N008 + N020），但文档给的列义锚点
    # 全是 RS1（1×26）的，RS2 的 9 列一个都没定论（实测样本总表 #34）→ 不映射，全部留 raw。
    "zxts": {},

    # ── 综合提示 2（CWServ.ph_agf10_zxts2，13 个 RS）────────────────────
    # 最大表是 RS11（同行比较 4 行 × 12 列），列名混用 N0xx 与 HYDM/zqdm/zqjc/zqsc
    # 且无逐列语义（实测样本总表 #35）→ 不映射，全部留 raw。
    "zxts2": {},

    # ── 资金/行业定位（CWServ.ph_agf10_zj，最大表 = RS3 1 行 × 4 列）─────
    # RS3 = N014…N017，实测四个值全 null（实测样本总表 #30）；有意义的 RS4
    # （行业名 + 行业码）不是最大表 → 不映射，全部留 raw。
    "agf10_zj": {},
}

# 只列「至少映射了 1 列」的产品，供门禁统计（覆盖率的分子分母都看这里）
PRODUCTS = tuple(sorted(p for p, m in FIELD_MAP.items() if m))


# ══════════════════════════════════════════════════════════════════════════
# 二、主表列清单（**交叉校验用**，不是修复层）
# ══════════════════════════════════════════════════════════════════════════
# 协议层已经保证 `Table.cols` == 主表自己的列名、与行值一一对齐
# （`client.Client._make_table`；并集在 `meta["cols_union"]`）。所以这里不再需要
# 拿文档清单去「对回去」。留着它的价值是：**实网取数时自动核一次**
# 「运行时主表列」是否和文档记录的一致 —— 不一致写 `meta["template_mismatch"]`。
# 每条都要有出处。长度与文档不符时不再抛错（列名已经可信），只报告。
_TEMPLATE_COLS: dict[str, list[str]] = {
    # 出处: F10与个股资料.md §三 cw_zcfzb（RS1 = 23×81，列 N001…N080, Tdate）
    "fin": ["N%03d" % i for i in range(1, 81)] + ["Tdate"],
    # 出处: F10与个股资料.md §2.2（38 列 = N001…N034, Tdate, N035, N036, N037）
    "lyb": ["N%03d" % i for i in range(1, 35)] + ["Tdate"] + ["N035", "N036", "N037"],
    # 出处: F10与个股资料.md §2.2（64 列 = N001…N063, Tdate）
    "xjllb": ["N%03d" % i for i in range(1, 64)] + ["Tdate"],
    # 出处: F10与个股资料.md §2.2 / §三 cw_zyzb（67 列，编号分 5 大段且跳段）
    "zyzb": (
        ["N%03d" % i for i in range(1, 16)]
        + ["N1%02d" % i for i in range(1, 16)]
        + ["N2%02d" % i for i in range(1, 9)]
        + ["N3%02d" % i for i in range(1, 9)]
        + ["N4%02d" % i for i in range(1, 15)]
        + ["N5%02d" % i for i in range(1, 4)]
        + ["N020", "N021", "Tdate", "N022"]
    ),
    # 出处: F10与个股资料.md §三 cw_zyzbxqy_sy「RS0 形状/列名」表
    "zyzbxq": ["N001", "T056", "T059", "T061", "T060", "T062", "T064", "T063",
               "T065", "T066", "T067", "JGID", "Tdate"],
    # 出处: 实网 2026-10-25 实测 600519（主表是 RS0 = 1 行 × 4 列 N001…N004）
    #       + F10与个股资料.md §四 jyfx「RS0 列义」
    "jyfx": ["N%03d" % i for i in range(1, 5)],
    # 出处: F10与个股资料.md §四 hypm（RS1 = 31 行 × 4 列，列名 N003、N004、sc、lx）
    "hypm": ["N003", "N004", "sc", "lx"],
    # 出处: F10与个股资料.md §五 zg_yq（RS1 = 4 行 × 11 列 = N002…N011, T001）
    "zgyq": ["N%03d" % i for i in range(2, 12)] + ["T001"],
    # 出处: F10与个股资料.md §六 zxts（RS2 = 10 行 × 9 列，列名 N001…N008, N020）
    "zxts": ["N%03d" % i for i in range(1, 9)] + ["N020"],
    # 出处: F10与个股资料.md §六 zxts2（RS11 = 4 行 × 12 列，「N0xx 与尾部四个拼音列名混用」）
    #       ⚠️ 拼音四列的**位置**是按文档描述排的（尾部），属推断，待实网核对。
    "zxts2": ["N%03d" % i for i in range(1, 9)] + ["HYDM", "zqdm", "zqjc", "zqsc"],
    # 出处: F10与个股资料.md §六 zxts3（RS1 = 9 行 × 5 列，列名 N001…N005）
    "zxts3": ["N%03d" % i for i in range(1, 6)],
    # 出处: F10与个股资料.md §六 zxts5（RS0 = 3 行 × 4 列，列名 N001…N004）
    "zxts5": ["N%03d" % i for i in range(1, 5)],
    # 出处: F10与个股资料.md §七之二 zp_ggld（RS0 = N 行 × 3 列，列名 N001…N003）
    "zpggld": ["N%03d" % i for i in range(1, 4)],
    # 出处: F10与个股资料.md §三 cw_cwfxxq「RS3 已完全解读 ✅」+ 实测样本总表 #3（45 行 × 7 列）
    #       这是 RS3「重大变动说明」的列清单；主表若不是它就靠 COL_GUARD 挡掉映射
    "cwfxxq": ["N%03d" % i for i in range(1, 8)],
    # 出处: F10与个股资料.md §七 zj（RS3 = 1 行 × 4 列，列名 N014…N017）+ 实测样本总表 #30
    "agf10_zj": ["N014", "N015", "N016", "N017"],
}


# ══════════════════════════════════════════════════════════════════════════
# 三、行业变体守卫：报表模板会随行业变的这几条命令
# ══════════════════════════════════════════════════════════════════════════
# 出处: F10与个股资料.md §三 cw_lyb「⚠️ 列布局随行业变：银行股（000001）实测是
#       25 期 × 57 列，与一般工商企业的 38 列根本不是同一张表…写程序必须按返回列数
#       分支，不能硬编码 38」。
# 同一套 N0xx 在另一套模板里**含义不同**，所以宁可只给 raw，也不能套错列名。
# 判据：运行时 `table.cols` 必须与文档清单**逐位相等**，才允许套 FIELD_MAP。
COL_GUARD: dict[str, list[str]] = {
    "fin": _TEMPLATE_COLS["fin"],
    "lyb": _TEMPLATE_COLS["lyb"],
    "xjllb": _TEMPLATE_COLS["xjllb"],
    "zyzb": _TEMPLATE_COLS["zyzb"],
    "zyzbxq": _TEMPLATE_COLS["zyzbxq"],
    # cwfxxq 回 5 个 RS，主表由协议层按单元格数挑；文档对「哪张是主表」有分歧
    # （实测样本总表 #3 记 45×7 = RS3 变动说明，域文档记 RS1 19×29）。
    # 只有运行时真的是 7 列（= 变动说明那张）才套映射，否则只给 raw。
    "cwfxxq": _TEMPLATE_COLS["cwfxxq"],
    # jyfx 的 p1=00301 分支实测是 31×9 的另一张表（按产品/地区构成），
    # 列义与 00101 的 N001…N004 完全不同 → 列清单不符就只给 raw。
    "jyfx": _TEMPLATE_COLS["jyfx"],
}


# ══════════════════════════════════════════════════════════════════════════
# 四、apply()
# ══════════════════════════════════════════════════════════════════════════
def _row_width(table: Table):
    """行宽（每行有多少个值）。没有行就返回 None。"""
    for r in table.rows:
        try:
            return len(r)
        except TypeError:                                      # pragma: no cover
            return None
    return None


def _resolve_cols(table: Table, product: str):
    """定出「与行内下标一一对应」的列名清单，返回 `(cols, 来源标记)`。

    来源标记：
      `server`        `table.cols` 与行宽一致 → 直接用（协议层的正常形态，最可信）
      `doc-template`  只有 cols 与行宽不一致（畸形表 / 旧回包）时才回落到文档清单，
                      **并且只在长度刚好对上时**才这么干；否则抛 NotSupportedError。

    `Table.__post_init__` 会把 list 行转成 `Row`（键 = cols），正常情况下
    `len(table.cols) == len(row)` 恒成立，走 `server` 分支。
    """
    cols = [str(c) for c in table.cols]
    width = _row_width(table)
    if width is None or len(cols) == width:
        return cols, "server"

    tpl = _TEMPLATE_COLS.get(product)
    if tpl is not None and len(tpl) == width:
        return list(tpl), "doc-template"

    raise NotSupportedError(
        "product=%s cmd=%s 的列名与行值对不上：cols %d 个、每行 %d 个值，"
        "文档里也没有该形状的列清单。不猜列名 —— 请检查 protocol 层是否回了异形表。"
        % (product, table.cmd or "?", len(cols), width))


def _template_check(cols, product: str):
    """把「运行时主表列」与「文档清单」对一次，返回 `(状态, 差异明细或 None)`。

    状态：`match` 一致 / `mismatch` 不一致 / `absent` 文档没记过这条。
    """
    tpl = _TEMPLATE_COLS.get(product)
    if not tpl:
        return "absent", None
    if list(tpl) == list(cols):
        return "match", None
    return "mismatch", {"docs_cols": list(tpl), "runtime_cols": list(cols)}


def _cast(field: str, value):
    """空值 → `None`；按字段类型给 str / int / float。"""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if s in _NULLS:
            return None
        if field in STR_FIELDS:
            return s
    else:
        s = value
        if field in STR_FIELDS:
            return str(value)

    try:
        f = float(s)
    except (TypeError, ValueError):
        return s                       # 非数值原样返回，不硬转 0
    if field in INT_FIELDS:
        try:
            return int(f)
        except (OverflowError, ValueError):                    # pragma: no cover
            return f
    return f


def apply(table: Table, product: str) -> Table:
    """把一张原始 `Table` 按 `product` 的映射表转成标准字段表。

    返回**新** `Table`（不改入参）：

    * `cols` = 该产品映射到的标准字段（按映射表顺序，**不含 `"raw"`**）
    * 每行 = `{标准字段: 值, …, "raw": {原始列: 原始值}}` → `len(row) == len(cols) + 1`
    * 该产品一列都没映射时 `cols == []`（数据列 0 个），原始列全在 `row["raw"]` 里

    `raw` 里放的是**服务端原值**（不清洗），原始列一个都不丢
    （协议层放进 `meta["cols_union"]` 的并集列名也原样跟着走）。

    几处如实告知都写在返回的 `meta` 里：
      * `template_check`：`match` / `mismatch` / `absent`（与文档列清单的比对结果）
      * `template_mismatch`：不一致时的两边清单
      * `mapping_skipped`：被 `COL_GUARD` 挡下（行业变体，如银行股利润表）时的原因

    列名与行值实在对不上（畸形表）→ `NotSupportedError`，**不猜**。
    """
    cols, align = _resolve_cols(table, product)
    present = set(cols)

    mapping = FIELD_MAP.get(product) or {}
    skipped = None
    guard = COL_GUARD.get(product)
    if mapping and guard is not None and list(guard) != list(cols):
        # 行业变体：同一套 N0xx 在另一张模板里含义不同 → 一列都不映射
        skipped = "cols-guard"
        mapping = {}

    # 标准字段的顺序 = **映射表顺序**（产品自己定的稳定顺序，不随服务端列序漂移）；
    # 这一张返回里没出现的列（例如银行股的利润表是 57 列）就不出现。
    std_cols: list[str] = []
    for col, s in mapping.items():
        if col in present and s not in std_cols:
            std_cols.append(s)

    rows: list[dict] = []
    for r in table.rows:
        if isinstance(r, dict):
            src_vals = [r.get(c) for c in cols]
            raw = dict(r)
        else:                                                  # pragma: no cover
            src_vals = list(r)
            raw = {}
            for i, c in enumerate(cols):
                raw[c] = src_vals[i] if i < len(src_vals) else None
            for j in range(len(cols), len(src_vals)):
                raw["_extra_%02d" % (j - len(cols) + 1)] = src_vals[j]

        row: dict = {}
        for i, c in enumerate(cols):
            s = mapping.get(c)
            if not s:
                continue
            v = src_vals[i] if i < len(src_vals) else None
            row[s] = _cast(s, v)
        row["raw"] = raw
        rows.append(row)

    meta = dict(table.meta or {})
    meta["product"] = product
    meta["cols_align"] = align
    status, detail = _template_check(cols, product)
    meta["template_check"] = status
    if detail:
        meta["template_mismatch"] = detail
    if skipped:
        meta["mapping_skipped"] = skipped
    unused = [c for c in map(str, table.cols) if c not in present]
    if unused:
        meta["unused_cols"] = unused

    return Table(cols=std_cols, rows=rows,
                 cmd=table.cmd, domain=table.domain, meta=meta)
