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
        workaround="目前无解 —— 不要指望 get_bars(\"00700\") 能拿到腾讯",
    ),
    Capability(
        name="港股 实时报价",
        instrument="hk",
        interface="",
        available=False,
        reason="**服务端有、本库未实现**：PBCombHQ + setcode=31 实测回腾讯控股 385.200、"
               "阿里巴巴－Ｗ 97.000、汇丰控股 134.300 等 13/14 个码并带中文名，且支持批量；"
               "本库未开公开接口（未经批量/延时/名称语义验证，不造接口）",
        evidence="§12.1 / §12.8 第 4 条 / probe/Q1Q2",
        workaround="call() 走 JSON 通道发不出 PBCombHQ（二进制 protobuf），暂无绕法",
    ),
    Capability(
        name="港股 清单（可枚举代码表）",
        instrument="hk",
        interface="",
        available=False,
        reason="**不可枚举**：PBMultiHQ 所有域的 Code 长度恒为 6，5 位码出现 0 次；没有港股域",
        evidence="§12.1 / probe/Q1Q2",
        workaround="自己外部维护港股代码清单，再逐只用 setcode=31 报价（该通道本库未实现）",
    ),
    # ── 期货 / 期权 / 国债期货 / 外汇 ─────────────────────────────────────
    Capability(
        name="商品期货",
        instrument="futures",
        interface="",
        available=False,
        reason="**不在数据源证券表内**：PBFXT/PBZST/PBCombHQ/PBMultiHQ × 6 个 market 码"
               "（7/8/28/29/30/31）× 30 个合约代码 = 全部为空，且 ERR_-1000 出现 0 次；"
               "另外 IF00 这类合约码在 symbols.parse 层就 ValueError（一个字节都没发出）",
        evidence="§12.1 / probe/Q1Q2",
        workaround="无通道。⚠️ 别归因成「市场码被拒」—— field3 对已知码是宽松的"
                   "（600519 塞进 field3=99 照样回同一根 K 线，逐位相同）",
    ),
    Capability(
        name="期权",
        instrument="option",
        interface="",
        available=False,
        reason="同期货：4 条命令 × 6 个市场码 × 30 个合约代码全空、ERR_-1000 0 次，"
               "说明代码不在证券表里，不是市场码问题",
        evidence="§12.1 / probe/Q1Q2",
        workaround="无通道",
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
        name="外汇",
        instrument="forex",
        interface="",
        available=False,
        reason="同期货：4×6×30 组合全空，代码不在数据源证券表内（不是市场码被拒）",
        evidence="§12.1 / probe/Q1Q2",
        workaround="无通道",
    ),
    # ── 与品种无关 ────────────────────────────────────────────────────────
    Capability(
        name="非 6 位数字代码（如 00700 / 02800）",
        instrument="all",
        interface="",
        available=False,
        reason="**显式拒绝、不做左补零**：00700 会被补成 000700 静默返回「模塑科技」的 K 线"
               "（与 get_bars(\"000700\") 逐条相同、无任何报错）；02800→002800、00005→000005 同理",
        evidence="§12.1 红线缺陷 / §13.1 阶段 3 修复",
        workaround="要哪个标的就写 6 位现役代码；港股 5 位码本库目前无法表达",
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
        reason="**机制有、实测不可行**：concurrency=4（4 条 lane 各钉一台主机）头 5 秒就被重置 "
               "3 条连接，limited_events 0→3、current 2.2→12.0、175 s 内 0 次成功取数 —— "
               "配额按**来源 IP**，换服务器不换配额",
        evidence="probe/phase5/CONCLUSION.md（原始输出 probe_phase5.out.txt）",
        workaround="默认 concurrency=1；要更快只能减少调用次数，或跨进程/跨机分摊",
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
