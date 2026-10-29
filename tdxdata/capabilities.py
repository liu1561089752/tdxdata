# -*- coding: utf-8 -*-
"""**能力边界注册表**（阶段 6）：哪个品种的哪种能力拿得到、拿不到、为什么、怎么绕。

为什么要有这个模块
----------------
"这个库能不能取港股？" "期货呢？" —— 这类问题以前散在 README、`__init__` 的说明表、
方法 docstring 和几次调研报告里，**同一个问题有四个答案**，而且会各自漂移。
更糟的是漂移方向通常是**乐观的**：README 写"支持港股"，代码里其实没有通道。

所以把"能力"收进**一张带证据的表**：

| 字段 | 作用 |
|---|---|
| `available` | 机械可查：`True` 的项**必须**指出一个真的存在于 `Client` 上的公开方法 |
| `reason` | **为什么**。`True` 要说清是哪条命令；`False` 要说清是"服务端没有"还是"本库未实现" |
| `evidence` | 让人能自己复核（`§12.x` / `reports/refactor/probe/...`） |
| `workaround` | 拿不到时**能怎么办**（一个方法名或一行代码），别只留"不支持" |

⚠️ **`available=False` 不等于"服务端不支持"**。本库最难查的几次误判都出在这里：
港股**有**报价通道（`PBCombHQ` + `setcode=31`，实测腾讯 385.200）只是本库没实现；
期货则是**代码不在数据源证券表里**（不是"市场码被拒"）。两种"拿不到"的处置完全不同，
所以 `reason` 必须写明是哪一种。

⚠️ 这张表是**静态事实**（代码里的常量），不来自任何行情通道 ——
所以 `Client.capabilities()` 返回的 `Table` 里 `as_of` 恒为 `None` 并且**必须**配
`as_of_note` 说明"没有数据日期这个概念"，而不是"取不到数据日期"。
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Capability", "CAPABILITIES", "INSTRUMENTS",
    "as_markdown_table", "by_instrument", "describe", "select",
    "market_domains_markdown", "CAPABILITY_COLS",
]

#: `Client.capabilities()` 返回表的列顺序（**契约**，测试逐列核对）。
CAPABILITY_COLS = ("instrument", "capability", "interface", "available",
                   "reason", "evidence", "workaround")

#: 品种代码 → 中文名。**封闭集**：`CAPABILITIES` 里出现不在这个表里的代码就算错。
INSTRUMENTS: dict[str, str] = {
    "a_share": "A 股（沪/深）",
    "bj": "北交所",
    "index": "指数",
    "board": "板块",
    "etf": "ETF / 基金",
    "bond": "债券 / 配债",
    "hk": "港股",
    "futures": "商品期货",
    "option": "期权",
    "cbt": "国债期货",
    "forex": "外汇",
    "us": "美股",
    "all": "与品种无关（全局）",
}


@dataclass(frozen=True, slots=True)
class Capability:
    """一项能力（或一项**确认拿不到**的能力）的单条登记。"""

    name: str            # 人类可读名
    instrument: str      # INSTRUMENTS 的键
    interface: str       # 提供它的公开方法名；`available=False` 时可为 ""
    available: bool
    reason: str          # 为什么有 / 为什么不 —— 必须具体
    evidence: str        # 依据出处
    workaround: str      # 拿不到时的绕法；没有就 ""


#: 注册表本体。**顺序即 README 表格顺序**（按品种聚在一起，便于人读）。
CAPABILITIES: tuple[Capability, ...] = (
    # ── A 股（沪/深）──────────────────────────────────────────────────────
    Capability(
        name="A 股 日/周/月/季 K 线",
        instrument="a_share",
        interface="get_bars",
        available=True,
        reason="PBFXT：field3=市场码、field8=复权开关（服务端算复权）；单页 416 根，"
               "count>416 自动翻页；对非价格代码会拒绝并指路 get_breadth",
        evidence="§13.1 阶段 1 / golden: bars_day_300、bars_510300",
        workaround="",
    ),
    Capability(
        name="A 股 5/15/30/60 分钟 K 线",
        instrument="a_share",
        interface="get_bars",
        available=True,
        reason="同一条 PBFXT，period 映射到协议周期串（不能用 name.lower()：MIN_60 会变 min_60）；"
               "MIN_3/YEAR 服务端无数据，已显式拒绝而不是返回空表",
        evidence="§13.1 阶段 1 / golden: bars_5m_300、bars_60m_300",
        workaround="",
    ),
    Capability(
        name="A 股 当日分时（最多 240 点）",
        instrument="a_share",
        interface="get_minutes",
        available=True,
        reason="PBZST 当日分时；盘中只回「到当前时刻为止」的点数（2026-10-27 改为最多 240 点，"
               "不再补齐到 240 个假点）",
        evidence="§13.1 阶段 1 / golden: minutes",
        workaround="要分时成交明细请用 get_ticks",
    ),
    Capability(
        name="A 股 逐笔成交（自动翻页）",
        instrument="a_share",
        interface="get_ticks",
        available=True,
        reason="PBTickEx（ctl=0x11）自动翻页并按时间升序；`trade_count`/`bs_flag` 声明式不可用"
               "（协议里没有笔数字段，`InOutFlag` 与涨跌只有 65.9%/76.8% 相关）",
        evidence="§13.1 阶段 1 / golden: ticks、models.UNPROVIDED_TICK_FIELDS",
        workaround="要笔数用 len(ticks)；方向字段没有可靠来源，别自己映射",
    ),
    Capability(
        name="涨跌家数 / 停板家数 / 新低点位（880005/880006/880866/880641）",
        instrument="a_share",
        interface="get_breadth",
        available=True,
        reason="这 4 个代码的 OHLC 是「家数」或「指标点位」、**不是价格**，所以 get_bars 会拒绝"
               "并指路；get_breadth 给命名字段（up_count/down_count/limit_up_count/value…）",
        evidence="§12.3 / series_kinds.NON_PRICE_SERIES / golden: breadth_880005 等 4 份",
        workaround="只支持日线；分钟级家数序列未实证",
    ),
    Capability(
        name="实时涨跌家数（涨/跌/平 + 涨停/跌停，含逐分钟序列）",
        instrument="a_share",
        interface="get_market_breadth",
        available=True,
        reason="HQServ.PBSdstat（ctl=0x11）：summary 回 5 个家数（up_count/down_count/"
               "flat_count/limit_up_count/limit_down_count），抓包回放实测 1637/3775/160/35/15，"
               "且 up+down+flat ≡ 5572（= 当日有价证券总数，27 帧恒等）；view='minute' 回逐分钟序列。"
               "⚠️ 与 get_market_stat('updown') **不是一回事**：后者走 "
               "CWServ.ph_datacenter_zdt_zdfx，是**历史近 30 个交易日**，本条是**实时快照**",
        evidence="eval3/M6-pb-undecoded.md §PBSdstat / tests/test_pb_breadth.py / "
                 "pcap: PBSdstat 27 帧 f1+f2+f3≡5572",
        workaround="回包不含日期/时间字段 → provenance.as_of 恒 None 并带说明 note；"
                   "要历史逐日请用 get_market_stat('updown')。view='minute' 的标的列与 "
                   "10 个 uint32 语义未定，只进 row['raw']",
    ),
    Capability(
        name="创业板（30xxxx）板块域枚举",
        instrument="a_share",
        interface="get_universe",
        available=True,
        reason="PBMultiHQ field4=14 实测有数据（旧表未登记）；⚠️ field4=2 是「深A全部」，"
               "不是「只有创业板」",
        evidence="§12.2 / _core.MARKET_DOMAINS",
        workaround="",
    ),
    Capability(
        name="科创板（68xxxx）板块域枚举",
        instrument="a_share",
        interface="get_universe",
        available=True,
        reason="PBMultiHQ field4=8 只含 68xxxx，是 field4=0 的**真子集**；field4=0 是沪A全部",
        evidence="§12.2 / _core.MARKET_DOMAINS",
        workaround="",
    ),
    Capability(
        name="全 A 股枚举（5577 只）",
        instrument="a_share",
        interface="iter_universe",
        available=True,
        reason="field4=6 = set(0) ∪ set(2) ∪ set(12) = **5577 只**（沪A 2319 + 深A 2904 + 北A 354，"
               "重复 0）；page_size=80、max_pages>=70，实测 ≈158.6 s 翻到底",
        evidence="§12.2 / probe/Q1Q2",
        workaround="别用 0+2 自己拼（会漏北交所 354 只）；只要沪深A 就用 0+2",
    ),
    Capability(
        name="年线 / 3 分钟线",
        instrument="a_share",
        interface="",
        available=False,
        reason="服务端返回 0 行：`MIN_3` / `YEAR` 这两档 period 实测是空结果，所以 client 层"
               "**显式拒绝**（`_PERIOD_DEAD`）而不是返回空表 —— 空表会被读成「今天没数据」",
        evidence="§13.1 / `client._PERIOD_DEAD` / README 能力表",
        workaround="年线用日线自己聚合；3 分钟线用 1 分钟线重采样（本库不代做）",
    ),
    Capability(
        name="历史逐笔（非当日）",
        instrument="a_share",
        interface="",
        available=False,
        reason="`PBTickEx` 只回**当日**逐笔：协议入参里没有「按日期取历史逐笔」这一位，"
               "不是本库没实现",
        evidence="§13.1 / README 能力表（「历史逐笔：只能取当日」）",
        workaround="要历史只能自己每天落盘累积（别把缺数据填 0）",
    ),
    Capability(
        name="五档盘口（买一~卖五）",
        instrument="a_share",
        interface="",
        available=False,
        reason="协议层没有买卖盘字段：HQServ 回包里没有 bid/ask 列，且 PBMultiHQ 没有代码位"
               "（2026-10-25 解构确认）；不是本库没解析，是服务端不给",
        evidence="§3.2 / __init__ 能力表",
        workaround="最优价用 get_quotes(...).NOW；行情列表用 get_market_page",
    ),
    # ── 北交所 ────────────────────────────────────────────────────────────
    Capability(
        name="北交所行情与 K 线",
        instrument="bj",
        interface="get_bars",
        available=True,
        reason="与沪深同一条 PBFXT/PBCombHQ；354 只**全部在 920 段**（430/830/870/820 一个都没有，"
               "430047 实测 EmptyResult）",
        evidence="§12.2 / probe/Q1Q2",
        workaround="别按 430/830 前缀判断北交所；批量报价用 get_quotes",
    ),
    # ── 指数 ──────────────────────────────────────────────────────────────
    Capability(
        name="指数行情与 K 线（999999 等）",
        instrument="index",
        interface="get_bars",
        available=True,
        reason="PBFXT 同样支持指数；`get_bars(\"999999\")` 的 raw[\"up\"]/raw[\"down\"] 就是当日"
               "涨跌家数且历史可回溯（免费彩蛋）",
        evidence="§12.3 E 组 / golden: quotes_indices",
        workaround="要家数序列用 get_breadth(\"880005\")",
    ),
    Capability(
        name="当日分时点位（多代码一次取，轻量抽样通道）",
        instrument="index",
        interface="get_intraday_samples",
        available=True,
        reason="HQServ.PBSample（ctl=0x11）：回「代码/市场/全天点数/昨收/本帧点数/float32[]」。"
               "语义已锚定 n_points(field7) ≡ len(points)（**逐点非抽样**，25/25 帧），"
               "points[0] 是 09:30 首点，pre_close ↔ PBCombHQ.CLOSE（**昨收**）；"
               "实测 999999 一帧回 139 点 = 09:30–11:30 的 121 + 13:00–13:18 的 18",
        evidence="eval3/M6-pb-undecoded.md §PBSample / tests/test_pb_sample.py（27 例）/ "
                 "离线回放：tools/m6_replay.py",
        workaround="⚠️ 实证标的只有指数 999999，**个股未复核**（个股 field5 是否仍 240、是否仍"
                   "每分钟一点没有证据）→ docstring 与 meta 标实验性；只要单只股票当日分时请用"
                   "get_minutes；count 只是本地截尾（field7 发 1 仍回 139 点，不是服务端上限）",
    ),
    # ── 板块 ──────────────────────────────────────────────────────────────
    Capability(
        name="板块清单（concept/style/area/industry1-3）",
        instrument="board",
        interface="get_boards",
        available=True,
        reason="PBMultiHQ field4=10001/10002/10004/10005/10006/10008，六类实测行数与手机端逐类一致"
               "（269/158/32/30/128/345）；change_pct=(NOW/CLOSE-1)*100 现算",
        evidence="§12.5 / golden: boards_industry_s4",
        workaround="",
    ),
    Capability(
        name="板块成分股",
        instrument="board",
        interface="get_board_members",
        available=True,
        reason="只有**概念**板块有成分通道（app_bklist_ag / zttz_xggp）；行业板块（881xxx）会抛 "
               "NotSupportedError 并指出替代接口，不返回残缺数据",
        evidence="§13.1 阶段 2 / README 已知限制",
        workaround="行业板块要「点位」就用 get_bars(\"881xxx\")",
    ),
    # ── ETF / 债券 ────────────────────────────────────────────────────────
    Capability(
        name="ETF / 基金行情与 K 线",
        instrument="etf",
        interface="get_universe",
        available=True,
        reason="PBMultiHQ field4=9 实测有数据（旧表未登记这一域）；代码段 5xxxxx/15xxxx/51xxxx",
        evidence="§12.2 / _core.MARKET_DOMAINS",
        workaround="",
    ),
    Capability(
        name="债券 / 配债行情",
        instrument="bond",
        interface="get_universe",
        available=True,
        reason="field4=4（债券/回购）、5（债券，另一类）、13（配债）；⚠️ 3/4/5/7 沿用旧标注、"
               "未逐个逐页复验，如实标注而不假装查过",
        evidence="§12.2 / _core.MARKET_DOMAINS",
        workaround="下结论前先自己复验 4 与 5 的细分口径",
    ),
    # ── 港股（三条并存，最容易写错）────────────────────────────────────────
    Capability(
        name="港股 日线 / 分时",
        instrument="hk",
        interface="",
        available=False,
        reason="PBFXT/PBZST **没有港股通道**；且本库对非 6 位数字代码**显式拒绝**、不做左补零"
               "（00700 曾被补成 000700 静默返回模塑科技的 K 线，逐条相同且无报错）",
        evidence="§12.1 / §13.1 阶段 3 / probe/Q1Q2",
        workaround="K 线/分时无解（不要指望 get_bars(\"00700\") 能拿到腾讯）；但**报价**已可用：get_quotes([\"hk00700\"]) 回现价/昨收/涨跌幅（15 分钟延时）",
    ),
    Capability(
        name="港股 实时报价",
        instrument="hk",
        interface="get_quotes",
        available=True,
        reason="**可用（2026-10-29 落地）**：PBCombHQ + 市场号 31（主板）/48（GEM）/49（REIT 等），"
               "代码是 **5 位数字**。抓包实证：998 个 PBCombHQ 请求里 1188 行 setcode=31、"
               "290 只不同港股（腾讯控股 00700 386.000、比亚迪股份 01211 71.600、"
               "工商银行 01398 7.240、中芯国际 00981 47.060…），一次请求最多带 100 个码、"
               "发多少回多少。"
               "⚠️ 港股是**延时行情**：回包 DELAYMIN=15（15 分钟），别当实时用。",
        evidence="reports/eval3/corpus/pb.jsonl（998 请求 / 1188 行 setcode=31 / 7 份 pcap）"
                 "+ 原始请求字节解出 field3=31、field4=5 位码（notes/18 §1）",
        workaround="用法：get_quotes([\"hk00700\"]) / (\"31:00700\") / (\"hkg08285\")。清单仍不可枚举（PBMultiHQ 所有域 Code 长度恒 6，5 位码 0 次）—— 代码要自己维护；"
                   "K 线/分时仍无通道",
    ),
    Capability(
        name="港股 清单（可枚举代码表）",
        instrument="hk",
        interface="",
        available=False,
        reason="**不可枚举**：PBMultiHQ 所有域的 Code 长度恒为 6，5 位码出现 0 次；没有港股域",
        evidence="§12.1 / probe/Q1Q2",
        workaround="自己外部维护港股代码清单，再用 get_quotes([\"hk00700\", …]) 报价（该接口 2026-10-29 起可用；但**清单**本身仍要外部维护）",
    ),
    # ── 期货 / 期权 / 国债期货 / 外汇 ─────────────────────────────────────
    Capability(
        name="港股 GEM / 港股其他（REIT）",
        instrument="hk",
        interface="get_quotes",
        available=True,
        reason="市场号 48（GEM，08xxx）/ 49（REIT 等，00405 越秀房产信托基金）实测都有回包："
               "48 回 9 列（含 AMOUNT）、49 回 10 列（含 ACTIVECAPITAL/ZSZ）",
        evidence="pb.jsonl：setcode=48 共 7 行 / 49 共 1 行", 
        workaround="GEM 里很多是仙股，NOW 可能为 0（停牌）—— 别把 0 当跌停",
    ),
    Capability(
        name="商品期货 报价",
        instrument="futures",
        interface="get_quotes",
        available=True,
        reason="**可用**：PBCombHQ 市场号 28=郑商所、29=大商所、30=上期所/能源中心、66=广期所，"
               "合约码是「品种字母+年月」（MA2701 甲醇2701、V2701 PVC2701、SP2701 纸浆2701、"
               "LC2701 碳酸锂2701），连月合约是 FUL9/SCL9 这种写法。回 19 列："
               "OPEN/CLOSE/NOW/MGJZC(昨结?)/SPECTYPE/WTB(委比)/RELAG/EXT_ZF(涨跌幅)。"
               "⚠️ 没有成交量/持仓量列（VOL/OI 不在回包里），要量得走专题（tdxrb_qh / qhlhb_sy）",
        evidence="pb.jsonl：setcode=28 57 行 / 29 82 行 / 30 77 行 / 66 12 行；"
                 "请求侧 field3 与代码逐条解出（notes/18 §2）",
        workaround="用法：get_quotes([\"28:MA2701\", \"29:V2701\", \"30:SP2701\", \"66:LC2701\"])。合约 K 线/分时/五档无通道（7727 仅笔记证据）",
    ),
    Capability(
        name="股指期货 报价",
        instrument="futures",
        interface="get_quotes",
        available=True,
        reason="**可用**：市场号 47，IH2611 上证2611 实测回 8 列（CLOSE/NOW/XSFLAG/EXT_ZF…）",
        evidence="pb.jsonl：setcode=47 共 2 行", 
        workaround="只有报价，没有合约乘数/保证金/基差字段",
    ),
    Capability(
        name="美股 报价",
        instrument="us",
        interface="get_quotes",
        available=True,
        reason="**可用**：市场号 74，代码是美股 ticker（NVDA 英伟达 / INTC 英特尔 / AMD / MU…），"
               "回 19 列（OPEN/CLOSE/NOW/MGJZC/ZSSJL/WTB/EXT_ZF），一次可带 100 只。"
               "另有数据中心的美股榜单产品（usa_gzb / usa_rqb / usa_bsb / tdxrb_hypm_usa，"
               "10–16 列，含中概与行业排名）",
        evidence="pb.jsonl：setcode=74 共 573 行；美股榜单：tdxrb_usa_gzb 10 列×100 行（NVDA 28974）"
                 "/ usa_bsb 13 列×100 行 / tdxrb_hypm_usa 16 列",
        workaround="美股 K 线/分时无通道",
    ),
    Capability(
        name="外汇 / 外盘指数 报价",
        instrument="forex",
        interface="get_quotes",
        available=True,
        reason="**可用（品种有限）**：市场号 10=外汇（USDCNH 美元兑离岸人民币，回 21 列含 "
               "OPEN/MAX/MIN/NOW/HqTime）、27=恒生指数（HSI）、12=外盘指数（A_DJI 道琼斯 / "
               "A_IXIC 纳斯达克 / A_SPX 标普500 / CNY0 富时A50期指）。"
               "抓包里只有 USDCNH 一个汇率品种，**不要推断其它货币对也存在**",
        evidence="pb.jsonl：setcode=10 共 10 行（USDCNH）、12 共 43 行、27 共 10 行（HSI 22934.68）",
        workaround="其他货币对（EURUSD/CNHHKD…）无实测证据；7727 侧仍是笔记证据，未接入",
    ),
    Capability(
        name="商品期货",
        instrument="futures",
        interface="",
        available=False,
        reason="**2026-10-29 复核：报价能取，K 线/分时不能**。"
               "(a) 报价 ✅：真实 App 流量里 PBCombHQ 用市场号 28/29/30/66（郑商所/大商所/"
               "上期所·能源/广期所）+ **带年份月份的合约码**（MA2701、V2701、SP2701、LC2701、FUL9）"
               "实测回 19 列（OPEN/CLOSE/NOW/MGJZC/SPECTYPE/WTB/RELAG/EXT_ZF），"
               "一次带 100 个合约、发多少回多少。旧结论「PBCombHQ 期货全空」是**探针代码写错**"
               "（IF00 这类缺年份/缺交易所前缀的码），不是通道没有，也不是市场码被拒。"
               "(b) K 线/分时 ❌：PBFXT/PBZST 无期货通道。"
               "(c) 专题数据 ✅（7615 数据中心侧抓包实测）：tdxrb_qh "
               "期货合约榜（12 列×100 行，含 MA2701/V2701/FU2701 与市场码 28/29/30）、qhlhb_sy 001 "
               "全市场合约清单（5 列×318 行）与 002 会员成交量/多空持仓/增减（10 列×21 行，"
               "p3=0..4 五种榜单）、qhlhb_jcgc 002 机构持仓（8 列×336 行）、qh_hqpd_qhhy 品种主力"
               "连续合约快照（5 列×10 行，now=最新价 721.3~9378.0，**是合约级价格但没有 OHLC**）。"
               "⚠️ qh_cdzl 名不副实：只回交易日列表（20×4 + 1×1），002/003 在 31 份抓包里连请求都没有，"
               "不能叫「持仓增量」",
        evidence="§12.1 / probe/Q1Q2（行情侧）；reports/eval3/findings/M4-fx-futures.md §2.3-2.5（专题侧，"
                 "31 份 pcap 真实回包）",
        workaround="合约 K 线/分时/五档无通道（7727 仅笔记证据）。要专题数据走数据中心通道："
                   "qhlhb_sy（001 合约清单 / 002 持仓与增减）/ qhlhb_jcgc（002 机构持仓）/ "
                   "tdxrb_qh（合约榜）/ qh_hqpd_qhhy（主力连续最新价）—— 本库均未封装成公开方法。"
                   "⚠️ 别归因成「市场码被拒」—— field3 对已知码是宽松的"
                   "（600519 塞进 field3=99 照样回同一根 K 线，逐位相同）",
    ),
    Capability(
        name="期权 T 型报价链",
        instrument="option",
        interface="get_option_chain",
        available=True,
        reason="三步链 2026-10-29 抓包实测打通：PBQQUnderly2(ctl=15) 回 9 个标的 → "
               "PBQQMonth(ctl=12) 回月份 2611/2612/2703/2706 + 到期日 → "
               "PBQQTXCode2(ctl=14) 回每档「行权价 + 认购代码/名称 + 认沽代码/名称」；"
               "回包是 {\"Length\"}+zlib+Base64 四层信封，peel() 已能剥",
        workaround='用法：get_option_chain("510050")（默认最近月份）、option_underlyings()、'
                   'option_months("510050")',
        evidence="pcap cap_1029_090838.pcap msgid 854/864/866；夹具 qq_*_响应.bin；"
                 "tests/test_option_chain.py 9 例（请求体逐字节对照抓包）",
    ),
    Capability(
        name="期权 行情（市场号 8）",
        instrument="option",
        interface="get_quotes",
        available=True,
        reason="T 型链里每档的 f2/f5 恒为 8 —— 市场号 8 就是期权市场，"
               "与 PBCombHQ 的 setcode 体系一致，可 get_quotes([\"8:10012637\"]) 取行情",
        workaround="期权没有一次性全量合约清单，只能按「标的 + 月份」现取",
        evidence="cap_1029_090838.pcap（12 档 T 型链，市场号 8 全命中）",
    ),
    Capability(
        name="期权（旧结论，已作废）",
        instrument="option",
        interface="",
        available=False,
        reason="当时写「4 条命令 × 6 个市场码 × 30 个合约码全空 ⇒ 期权无通道」，"
               "**错在探针**：期权不走那 4 条命令，而是 PBQQ* 三步链",
        workaround="别再用「命令 × 市场码 × 合约码」笛卡尔积猜通道；先看抓包里 App 发了什么",
        evidence="§12.1 / probe/Q1Q2（结论作废），更正见 notes/19",
    ),
    Capability(
        name="国债期货",
        instrument="cbt",
        interface="",
        available=False,
        reason="同期货：不在证券表内，4×6×30 组合全空；合约码形态在 symbols.parse 层就被拒"
               "（OutOfScope/ValueError），请求根本发不出去",
        evidence="§12.1 / probe/Q1Q2",
        workaround="无通道",
    ),
    Capability(
        name="外汇（7727 专用通道 / 其他货币对）",
        instrument="forex",
        interface="",
        available=False,
        reason="**7615 无外汇报价；`fx` 是「分析」不是「外汇」**：`ph_cfg_fx_sczb`（产品 `sczb`）"
               "实测 15 项全是 A 股市场/流动性指标（央行净投放 -650 亿、LPR 1Y/5Y 3.00/3.50、"
               "M2 359.63 万亿、两融 24533.74 亿、道指/纳指/标普点位…），`ph_cfg_fx_jjhqpd`"
               "（产品 `jjhqpd`）是 10 只境内 ETF 行情盘点（510300/511880…），**两者零汇率字段**；"
               "行情类 4 命令 × 6 市场码 × 30 代码全空、代码不在数据源证券表内（不是市场码被拒，"
               "同期货）。7727 侧 USDCNY 6.6746 / USDCNH 6.67267 / CNHHKD 1.17471 / CNYHKD 1.17377 "
               "只是《汇率抓包新增接口.md》§十二:286-307（2026-10-22，4 台 EX 服务器当时 2 台可用 —— "
               "快照不是承诺）的**笔记/在线探针证据**，"
               "**不是抓包实测** —— 全部 31 份 pcap 里 7709/7727/7708 各 0 帧、`USDCNY` 字符串 0 次命中。"
               "本库未接入 7727、未实测",
        evidence="7615 侧：§12.1 / probe/Q1Q2；7727 侧仅笔记："
                 "notes/archive/接口文档/《汇率抓包新增接口.md》§十二:286-307（2026-10-22 在线探针）；"
                 "抓包侧：31 份 pcap 端口直方图 7709/7727/7708 = 0 帧（reports/eval3/findings/"
                 "M4-fx-futures.md §3-4）",
        workaround="本库暂无公开接口。⚠️ 别把 `ph_cfg_fx_*`（`sczb`/`jjhqpd`）当外汇：那是 A 股指标"
                   "面板与境内 ETF 盘点。是否接入 EX 7727 属通道攻关决策（期货/期权行情出路同此通道），"
                   "不默认施工",
    ),
    # ── 与品种无关 ────────────────────────────────────────────────────────
    Capability(
        name="非 6 位数字代码（K 线等非报价接口）",
        instrument="all",
        interface="",
        available=False,
        reason="**显式拒绝、不做左补零**：00700 会被补成 000700 静默返回「模塑科技」的 K 线"
               "（与 get_bars(\"000700\") 逐条相同、无任何报错）；02800→002800、00005→000005 同理",
        evidence="§12.1 红线缺陷 / §13.1 阶段 3 修复",
        workaround="**报价**已能表达：get_quotes([\"hk00700\"]) 走 symbols.parse_quote()，港股按 5 位校验、美股/期货按字母数字放行；但 get_bars/get_minutes 等 K 线接口仍只认 6 位 A 股代码 —— 那里继续显式拒绝，不做左补零",
    ),
    Capability(
        name="日线里含「盘后固定价格交易」（15:05–15:30）的量",
        instrument="all",
        interface="",
        available=False,
        reason="盘后成交**存在但不在日线里**：逐笔能看到 InOutFlag=5 的 15:05–15:29:50 成交"
               "（30–1588 手 = 日线量的 0.013%–0.119%，逐票都在日线之外）；而日线 vol == "
               "分时 240 点合计（|Δ|≤0.48 手），Bar.raw 12 个键没有一个能区分盘后成交",
        evidence="§12.3 E1/E4/E8 / probe/Q3Q6",
        workaround="只能 get_ticks 自聚合（≈100 s/只）；注意 status=final 只表示「日线口径定稿」",
    ),
    Capability(
        name="并发取数（靠多主机提速）",
        instrument="all",
        interface="",
        available=False,
        reason="**实测不可行，特性已删除**：concurrency=4（4 条 lane 各钉一台主机）头 5 秒就被重置 "
               "3 条连接，limited_events 0→3、current 2.2→12.0、175 s 内 0 次成功取数 —— "
               "配额按**来源 IP**，换服务器不换配额。2026-10 起 `Client` 不再接受 "
               "`concurrency` 参数（传了直接 `TypeError`）",
        evidence="probe/phase5/CONCLUSION.md（原始输出 probe_phase5.out.txt）",
        workaround="要更快只能减少调用次数，或跨进程/跨机分摊（后者需自行验证配额口径）",
    ),
)


# ══════════════════════════════════════════════════════════════════════════
# 查询
# ══════════════════════════════════════════════════════════════════════════
def select(*, instrument: str | None = None,
           available_only: bool = False) -> tuple[Capability, ...]:
    """按条件筛注册表。**空结果是合法的**（由调用方决定怎么报）。

    为什么单独抽出这么一层：`Client.capabilities()` 要把空结果转成 `EmptyResult`，
    而模块级的 `describe()` 要把空结果转成 `ValueError` —— 两条路都不该各自复制筛选逻辑。
    返回 `tuple` 而不是 `list`，是为了**调用方改不动注册表本体**。
    """
    out = CAPABILITIES
    if instrument is not None:
        key = str(instrument).strip().lower()
        out = tuple(c for c in out if c.instrument == key)
    if available_only:
        out = tuple(c for c in out if c.available)
    return out


def by_instrument() -> dict[str, list[Capability]]:
    """按品种分组（键是 `INSTRUMENTS` 里的代码，值是登记项列表，保持注册表顺序）。"""
    out: dict[str, list[Capability]] = {}
    for cap in CAPABILITIES:
        out.setdefault(cap.instrument, []).append(cap)
    return out


def describe(instrument: str) -> list[Capability]:
    """取某个品种的全部登记项。品种不认识 → `ValueError`（列出合法取值）。

    为什么不返回空列表：空列表会让调用方以为"这个品种确实没有能力"，
    而真相多半是"品种名打错了"。红线：**拿不到要说清是哪种拿不到**。
    """
    key = str(instrument or "").strip().lower()
    got = select(instrument=key)
    if not got:
        raise ValueError(
            "不认识的品种 %r。合法取值：%s"
            % (instrument, " / ".join("%s（%s）" % (k, v)
                                      for k, v in INSTRUMENTS.items())))
    return list(got)


# ══════════════════════════════════════════════════════════════════════════
# 渲染（README 的标记块逐字用这里，别手改 README）
# ══════════════════════════════════════════════════════════════════════════
def _cell(text: str) -> str:
    """把一段文本塞进 markdown 单元格。

    ⚠️ **必须转义 `|`**：单元格里的裸竖线会把一行切成两列，整张表从那一行起错位。
    `reason` 里出现竖线一点都不稀奇（"`set(0)|set(2)`"这种集合写法首当其冲），
    所以转义放在渲染层，而不是要求每个写注册表的人自己记得别用。
    """
    return (str(text).replace("|", "\\|")
            .replace("\r\n", " ").replace("\n", " ").replace("\r", " "))


def as_markdown_table() -> str:
    """能力边界表（markdown）—— README「能力边界（机器可读版）」那一节是它的**逐字副本**。

    为什么由代码生成：`available` 会随实证变化（"服务端有但本库未实现"就可能变成"已实现"），
    手写表格必然漂移，而漂移的能力表会让调用方**按错的边界设计**。
    """
    head = ("| 品种 | 能力 | 接口 | 可用 | 为什么 | 依据 | 拿不到怎么办 |\n"
            "|---|---|---|---|---|---|---|")
    rows = []
    for cap in CAPABILITIES:
        label = "%s（`%s`）" % (INSTRUMENTS.get(cap.instrument, cap.instrument),
                               cap.instrument)
        rows.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            _cell(label),
            _cell(cap.name),
            _cell("`%s`" % cap.interface) if cap.interface else "—",
            "✅" if cap.available else "❌",
            _cell(cap.reason),
            _cell(cap.evidence),
            _cell(cap.workaround) if cap.workaround else "—",
        ))
    return "\n".join([head] + rows)


def market_domains_markdown() -> str:
    """`PBMultiHQ` 的 `field4` 语义表（markdown）—— README 那一节是它的**逐字副本**。

    渲染器为什么放在这里而不是 `_core.py`：本阶段的文件域里 `_core.py` 是冻结的（唯一写者
    分工），而"把注册表渲染成 README 表格"正是本模块已经在做的事。**事实源仍是
    `_core.MARKET_DOMAINS`**，这里只做渲染，不复制任何一个语义字符串。
    """
    from ._core import MARKET_DOMAINS

    head = "| `market` | 语义 |\n|---|---|"
    rows = ["| `%d` | %s |" % (k, _cell(MARKET_DOMAINS[k]))
            for k in sorted(MARKET_DOMAINS)]
    return "\n".join([head] + rows)
