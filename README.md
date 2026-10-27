# tdxdata

**A 股行情与数据中心，一行 import 就能用。纯标准库，零第三方依赖。**

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![Tests](https://img.shields.io/badge/tests-367%20passed-success)
![Products](https://img.shields.io/badge/%E6%95%B0%E6%8D%AE%E4%B8%AD%E5%BF%83%E4%BA%A7%E5%93%81-421-orange)
![License](https://img.shields.io/badge/license-MIT-green)

```python
import tdxdata

with tdxdata.Client() as c:
    bars = c.get_bars("600519", period="day", count=5, adjust=tdxdata.Adjust.QFQ)
    print(len(bars), bars[-1].date, bars[-1].close)
    # 5 20261026 1349.98
```

不需要安装任何客户端，不需要账号或 API Key，不需要 pandas / numpy / requests。

---

## 这个库能做什么

**一句话**：把 A 股的 K 线、分时、逐笔、报价，以及龙虎榜 / 财报 / 资金流等 421 个数据产品，
变成一个 `import` 就能调的 Python 对象。

### 三个"不需要"

| 你不需要… | 因为… |
|---|---|
| 安装通达信客户端 | 库直连公开行情服务器 |
| 注册账号 / 申请 Key | 无需鉴权 |
| 装第三方包 | 只用 `socket` / `struct` / `zlib` / `json` |

### 能取什么

| 想取什么 | 用这个 |
|---|---|
| 日 / 周 / 月 / 季 K 线，前复权后复权 | `get_bars()` |
| 1 / 5 / 15 / 30 / 60 分钟线 | `get_bars(period="1m")` |
| 当日分时（最多 240 点，含均价） | `get_minutes()` |
| **逐笔成交**（自动翻页） | `get_ticks()` |
| 按代码批量报价（含量额） | `get_quotes()` |
| 全市场代码枚举 | `get_market_page()` / `get_universe()` |
| 代码 → 名称 | `get_name()` |
| 板块清单（6 类）与成分股 | `get_boards()` / `get_board_members()` |
| 技术指标（EMA / RSI…） | `get_indicators()` |
| 涨跌停价 | `get_price_limits()` |
| 龙虎榜 / 财报 / 资金流 / 分红 / 大盘统计…（421 个产品） | `get("产品名")` |
| F10 / 财务报表 / 分红融资 | `get_f10()` / `get_finance()` / `get_xdxr()` |

### 取不到的（会明确抛异常，不返回假数据）

| 不能取 | 原因 |
|---|---|
| **五档盘口**（买一卖一…） | 该通道没有买卖盘字段 |
| **行业板块成分股** | 只有概念板块支持按板块取成分股 |
| **年线 / 3 分钟线** | 服务端返回 0 行 |
| **历史逐笔** | 只能取当日 |
| **期货 / 期权 / 国债期货 / 外汇** | **本数据源的证券表里没有这些品种** —— 库会抛 `OutOfScope`。⚠️ 注意归因：这**不是**「服务端不支持某个市场码」。已实证 `PBFXT` 的 `field3` 对已知代码是宽松的（`600519` 在 `field3=1/99/30` 三种下返回**逐位相同**的 K 线）。 |
| **港股** | 港股**实时报价服务端是有的**（`PBCombHQ` + `setcode=31`），但**本库尚未实现**；港股 **K 线/分时没有通道**，港股清单也**无法枚举**。本库目前只覆盖 A 股 / 北交所 / 指数 / 板块 / 债券。 |

---

## 安装

```bash
pip install git+https://github.com/liu1561089752/tdxdata.git
```

或本地开发：

```bash
git clone https://github.com/liu1561089752/tdxdata.git
cd tdxdata
pip install -e ".[dev]"
```

要求 Python ≥ 3.9，Windows / Linux / macOS 均可（纯 socket，无编译依赖）。

---

## 三十秒上手

```python
import tdxdata

with tdxdata.Client() as c:
    # ① 日线（前复权，服务端算好给）
    bars = c.get_bars("600519", period="day", count=120, adjust=tdxdata.Adjust.QFQ)

    # ② 当日分时（最多 240 点，盘中逐步给出）
    mins = c.get_minutes("000001")

    # ③ 逐笔成交（自动翻页，按时间升序）
    ticks = c.get_ticks("600519", count=300)

    # ④ 批量报价（含成交量/成交额）
    qs = c.get_quotes(["600519", "000001", "399001", "881007"])

    # ⑤ 数据中心产品（421 个之一）
    d = c.get("holiday")
    print(d.table.shape, d.table.cols[:3])
    print(d.rows[0])            # Row 是 dict 语义，可按列名取值
    # (58, 6) ['N001', 'N002', 'N003']
    # Row{'N001': '2026-05-01', 'N002': '星期五', 'N003': '中国', ...}
```

### 返回类型

| 调用 | 返回 | 常用属性 |
|---|---|---|
| `get_bars()` | `list[Bar]` | `date` `open` `high` `low` `close` `vol` `amount` `adjust` `period` |
| `get_minutes()` | `list[MinutePoint]` | `time`（`'09:30'`）`price` `avg_price` `vol` |
| `get_ticks()` | `list[Tick]` | `time` `price` `vol`（手）`raw` |
| `get_quotes()` / `get_market_page()` | `list[Quote]` | `code` `name` `price` `pre_close` `vol` `amount` |
| `get(产品名)` | `Dataset` | `.table`（`Table`）、`.rows`、`.name`、`.params` |
| `get_f10()` / `get_finance()` 等 | `Table` | `.shape` `.cols` `.rows` `.column('列名')` `.to_dicts()` |

**每个对象都带 `.raw`** —— 原始行原值，字段没覆盖到的部分可以自己从里面取。



### `Tick` 的字段表（`get_ticks` 返回的元素）

<!-- BEGIN: tick_fields（由 tdxdata/models.py:Tick.as_markdown_table() 生成，勿手改） -->
| 字段 | 含义 | 有值？ | 依据 / 口径 |
|---|---|---|---|
| `market` | 市场号 | 有 | 0=深 1=沪 2=北（与 `Quote`/`Tick.symbol` 同口径） |
| `code` | 股票代码 | 有 | 6 位字符串 |
| `time` | 成交时刻 | 有 | `HH:MM:SS`；服务端给当日秒数（如 40978 → 11:22:58） |
| `price` | 成交价 | 有 | 元；与 `raw[Now]` 逐笔相等（300/300 实测） |
| `vol` | 成交量 | 有 | **手**；与 `Bar.vol`/`Quote.vol`/`MinutePoint.vol` 同单位 |
| `trade_count` | 笔数 | **不提供（声明式不可用）** | 原因见 `models.UNPROVIDED_TICK_FIELDS`：`PBTickEx` 一行就是一笔，协议里没有「笔数」字段；要数笔数请 `len(ticks)` |
| `bs_flag` | 买卖方向 | **不提供（声明式不可用）** | 原因见 `models.UNPROVIDED_TICK_FIELDS`：服务端无可判定字段，`raw[InOutFlag]` 0/1 与涨跌只有 65.9%/76.8% 相关，映射会整批搞反 |
| `raw` | 原始行 | 有 | 服务端原列：`Second`/`Now`/`NowVol`/`VolInStockDiff`/`InOutFlag`/`HKTTFlag`/`HKInOutFlag`，本库不解释 |
<!-- END: tick_fields -->

`bs_flag` **恒为 `None` 是设计**（**声明式不可用**：原因写在机器可读的
`tdxdata.models.UNPROVIDED_TICK_FIELDS` 里，测试会对真实样本复核它确实恒 `None`）。
反过来给它填 `2` 才是编数据 —— `2` 会被下游读成「卖」。

`trade_count` 已在阶段 4 **删除**：`PBTickEx` 一行就是**一笔**成交，协议里根本没有
「笔数」字段。那不是「暂不可用」，是**这个概念在这里不存在** —— 留着只有当摆设
（旧代码还填过 `0`，会被 `sum()` 当真）。

买卖方向**目前不提供**，但原始列在 `tick.raw["InOutFlag"]` 里，2026-10-27 实网 1200 笔的实测分布是：`0` 像主动买（相对上一笔涨 65.9%）、`1` 像主动卖（跌 76.8%）、`5` 全在 15:05–15:30 且成交在收盘价（盘后固定价格交易）、`2` 零星（含 15:00:01 收盘集合竞价那一笔）。**相关不等于实证**，所以库不替你映射。


---

## `vol` 的单位：对外**恒为「手」**

`Bar` / `Quote` / `MinutePoint` / `Tick` **四者同单位**，可以直接比较、相加、相除。

```python
c.get_quotes(["600519"])[0].vol      # 手
c.get_bars("600519")[-1].vol         # 手
c.get_ticks("600519", 100)[0].vol    # 手
c.get_minutes("600519")[0].vol       # 手
```

换算**不四舍五入**（1,139,872 股 → `11398.72` 手），所以 `vol` 可能带小数。

| 品类 × 周期 | 库返回的 `vol` |
|---|---|
| 日线 / 分钟线 · 股票、ETF | 手（服务端给的是股，库已÷100） |
| 周线 / 月线 · 所有品类 | 手 |
| 日线 · 可转债 | 手（1 手 = 10 张） |
| 日线 · 逆回购 | 手（1 手 = 1000 元） |
| 日线 · 指数 / 板块指数 | 手 |
| **分钟线 · 指数 / 板块指数** | **`None`**（原因见下） |

> ⚠️ **指数/板块分钟线的 `vol` 是 `None`，不是 0，也不是手。**
> 服务端那一列填的是"成交额÷100"的派生值，不是真实成交量。库不拿派生值冒充数据，
> 所以给 `None`；原值仍在 `raw["Volume"]`。**该品类的日线 `vol` 是真值**，照常返回。

> ⚠️ **直接读 `raw` 时要小心**：`raw` 里保留的是服务端原值，单位随品类和周期变。

**`amount`（成交额，单位元）** 在所有周期口径一致，可直接用；`Quote.amount` 与 `Bar.amount` 同口径。
需要「手 → 股」自行乘 100。

---

## 复权

`adjust` 由**服务端计算**，不是本地乘系数：

| 值 | 含义 |
|---|---|
| `tdxdata.Adjust.NONE` | 不复权 |
| `tdxdata.Adjust.HFQ` | 后复权 |
| `tdxdata.Adjust.QFQ` | 前复权 |

```python
c.get_bars("600519", "day", 200, adjust=tdxdata.Adjust.NONE)[-1].close   # 1349.98
c.get_bars("600519", "day", 200, adjust=tdxdata.Adjust.QFQ)[-1].close    # 1349.98（最新一根相同）
c.get_bars("600519", "day", 200, adjust=tdxdata.Adjust.HFQ)[-1].close    # 9395.53
```

> 后复权与通达信客户端一致。**不同软件的后复权绝对值不可比** —— 基准不同，
> 例如同期东方财富给另一组数，差一个恒定倍数。

---

## 代码写法

```python
c.get_bars("600519")                 # 自动推断市场（推荐）
c.get_bars("sh600519")               # 显式前缀
c.get_bars(("sh", "600519"))         # 元组
c.get_bars("600519.SH")              # 后缀

c.get_bars("999999")                 # 上证指数（999 段自动判沪市）
c.get_bars(("sh", "000300"))         # 沪深300 —— 与深市代码段冲突，需显式指定
```

沪 A / 深 A / 创业板 / 科创 / 北交所 / B 股 / 可转债 / 逆回购 / 指数 / 板块指数（`880xxx`、`881xxx`）
都能直接传代码，市场归属自动判定。

---

## 能力一览

| 能力 | 说明 |
|---|---|
| 日 / 周 / 月 / 季 K 线 | 单次上限 416 根，超出自动翻页 |
| 1 / 5 / 15 / 30 / 60 分钟线 | 1 分钟线约覆盖 2 个交易日 |
| 当日分时 | 最多 240 点，含均价；盘中只给到当前时刻 |
| 复权 | 前复权 / 后复权 / 不复权，服务端计算 |
| 逐笔成交 | 自动翻页、去重、按时间升序 |
| 批量报价 | 沪深/创业板/科创/北交所/指数/板块指数，含成交量与成交额 |
| 技术指标 | EMA / RSI 等，与 K 线逐行对齐 |
| 涨跌停价 | 主板 ±10%、双创 ±20%、北交所 ±30%、转债 ±20%、基金与 B 股按 0.001 取整 |
| 板块清单 | 六类：行业一/二/三级 30 / 128 / 345，概念 269，风格 158，地区 32 |
| 概念板块成分股 | 含入选理由与纳入日期 |
| 数据中心 | 421 个产品、17 个域：龙虎榜 / 财报 / 资金流 / 分红 / 大盘统计… |
| 结构化接口 | F10、财务报表、分红融资（语义化英文列名） |
| 本地缓存 | `Client(cache=TTLCache(ttl=300))`，命中不占限流配额 |
| 同步 + 异步 | `Client` / `AsyncClient` |

> 涨跌停家数（`get_market_stat("updown")`）**含北交所**。不含北交所的数量与之不同，
> 与东方财富涨停池一致的是不含的口径 —— 用之前先确认你要哪个。

---

## 能力边界（`Client.capabilities()`，由 `tdxdata/capabilities.py` 生成）

上面那张表是「能干什么」；这张表是**机器可读的完整版**，连「服务端其实有、本库还没实现」这种中间态也写进去，逐行都能顺着「依据」列自己复核。

```python
with tdxdata.Client() as c:
    c.capabilities(instrument="hk")            # 只看港股
    c.capabilities(available_only=True)        # 只看拿得到的
```

<!-- BEGIN: capabilities（由 tdxdata/capabilities.py 的 as_markdown_table() 生成，勿手改） -->
| 品种 | 能力 | 接口 | 可用 | 为什么 | 依据 | 拿不到怎么办 |
|---|---|---|---|---|---|---|
| A 股（沪/深）（`a_share`） | A 股 日/周/月/季 K 线 | `get_bars` | ✅ | PBFXT：field3=市场码、field8=复权开关（服务端算复权）；单页 416 根，count>416 自动翻页；对非价格代码会拒绝并指路 get_breadth | §13.1 阶段 1 / golden: bars_day_300、bars_510300 | — |
| A 股（沪/深）（`a_share`） | A 股 5/15/30/60 分钟 K 线 | `get_bars` | ✅ | 同一条 PBFXT，period 映射到协议周期串（不能用 name.lower()：MIN_60 会变 min_60）；MIN_3/YEAR 服务端无数据，已显式拒绝而不是返回空表 | §13.1 阶段 1 / golden: bars_5m_300、bars_60m_300 | — |
| A 股（沪/深）（`a_share`） | A 股 当日分时（最多 240 点） | `get_minutes` | ✅ | PBZST 当日分时；盘中只回「到当前时刻为止」的点数（2026-10-27 改为最多 240 点，不再补齐到 240 个假点） | §13.1 阶段 1 / golden: minutes | 要分时成交明细请用 get_ticks |
| A 股（沪/深）（`a_share`） | A 股 逐笔成交（自动翻页） | `get_ticks` | ✅ | PBTickEx（ctl=0x11）自动翻页并按时间升序；`trade_count`/`bs_flag` 声明式不可用（协议里没有笔数字段，`InOutFlag` 与涨跌只有 65.9%/76.8% 相关） | §13.1 阶段 1 / golden: ticks、models.UNPROVIDED_TICK_FIELDS | 要笔数用 len(ticks)；方向字段没有可靠来源，别自己映射 |
| A 股（沪/深）（`a_share`） | 涨跌家数 / 停板家数 / 新低点位（880005/880006/880866/880641） | `get_breadth` | ✅ | 这 4 个代码的 OHLC 是「家数」或「指标点位」、**不是价格**，所以 get_bars 会拒绝并指路；get_breadth 给命名字段（up_count/down_count/limit_up_count/value…） | §12.3 / series_kinds.NON_PRICE_SERIES / golden: breadth_880005 等 4 份 | 只支持日线；分钟级家数序列未实证 |
| A 股（沪/深）（`a_share`） | 创业板（30xxxx）板块域枚举 | `get_universe` | ✅ | PBMultiHQ field4=14 实测有数据（旧表未登记）；⚠️ field4=2 是「深A全部」，不是「只有创业板」 | §12.2 / _core.MARKET_DOMAINS | — |
| A 股（沪/深）（`a_share`） | 科创板（68xxxx）板块域枚举 | `get_universe` | ✅ | PBMultiHQ field4=8 只含 68xxxx，是 field4=0 的**真子集**；field4=0 是沪A全部 | §12.2 / _core.MARKET_DOMAINS | — |
| A 股（沪/深）（`a_share`） | 全 A 股枚举（5577 只） | `iter_universe` | ✅ | field4=6 = set(0) ∪ set(2) ∪ set(12) = **5577 只**（沪A 2319 + 深A 2904 + 北A 354，重复 0）；page_size=80、max_pages>=70，实测 ≈158.6 s 翻到底 | §12.2 / probe/Q1Q2 | 别用 0+2 自己拼（会漏北交所 354 只）；只要沪深A 就用 0+2 |
| A 股（沪/深）（`a_share`） | 年线 / 3 分钟线 | — | ❌ | 服务端返回 0 行：`MIN_3` / `YEAR` 这两档 period 实测是空结果，所以 client 层**显式拒绝**（`_PERIOD_DEAD`）而不是返回空表 —— 空表会被读成「今天没数据」 | §13.1 / `client._PERIOD_DEAD` / README 能力表 | 年线用日线自己聚合；3 分钟线用 1 分钟线重采样（本库不代做） |
| A 股（沪/深）（`a_share`） | 历史逐笔（非当日） | — | ❌ | `PBTickEx` 只回**当日**逐笔：协议入参里没有「按日期取历史逐笔」这一位，不是本库没实现 | §13.1 / README 能力表（「历史逐笔：只能取当日」） | 要历史只能自己每天落盘累积（别把缺数据填 0） |
| A 股（沪/深）（`a_share`） | 五档盘口（买一~卖五） | — | ❌ | 协议层没有买卖盘字段：HQServ 回包里没有 bid/ask 列，且 PBMultiHQ 没有代码位（2026-10-25 解构确认）；不是本库没解析，是服务端不给 | §3.2 / __init__ 能力表 | 最优价用 get_quotes(...).NOW；行情列表用 get_market_page |
| 北交所（`bj`） | 北交所行情与 K 线 | `get_bars` | ✅ | 与沪深同一条 PBFXT/PBCombHQ；354 只**全部在 920 段**（430/830/870/820 一个都没有，430047 实测 EmptyResult） | §12.2 / probe/Q1Q2 | 别按 430/830 前缀判断北交所；批量报价用 get_quotes |
| 指数（`index`） | 指数行情与 K 线（999999 等） | `get_bars` | ✅ | PBFXT 同样支持指数；`get_bars("999999")` 的 raw["up"]/raw["down"] 就是当日涨跌家数且历史可回溯（免费彩蛋） | §12.3 E 组 / golden: quotes_indices | 要家数序列用 get_breadth("880005") |
| 板块（`board`） | 板块清单（concept/style/area/industry1-3） | `get_boards` | ✅ | PBMultiHQ field4=10001/10002/10004/10005/10006/10008，六类实测行数与手机端逐类一致（269/158/32/30/128/345）；change_pct=(NOW/CLOSE-1)*100 现算 | §12.5 / golden: boards_industry_s4 | — |
| 板块（`board`） | 板块成分股 | `get_board_members` | ✅ | 只有**概念**板块有成分通道（app_bklist_ag / zttz_xggp）；行业板块（881xxx）会抛 NotSupportedError 并指出替代接口，不返回残缺数据 | §13.1 阶段 2 / README 已知限制 | 行业板块要「点位」就用 get_bars("881xxx") |
| ETF / 基金（`etf`） | ETF / 基金行情与 K 线 | `get_universe` | ✅ | PBMultiHQ field4=9 实测有数据（旧表未登记这一域）；代码段 5xxxxx/15xxxx/51xxxx | §12.2 / _core.MARKET_DOMAINS | — |
| 债券 / 配债（`bond`） | 债券 / 配债行情 | `get_universe` | ✅ | field4=4（债券/回购）、5（债券，另一类）、13（配债）；⚠️ 3/4/5/7 沿用旧标注、未逐个逐页复验，如实标注而不假装查过 | §12.2 / _core.MARKET_DOMAINS | 下结论前先自己复验 4 与 5 的细分口径 |
| 港股（`hk`） | 港股 日线 / 分时 | — | ❌ | PBFXT/PBZST **没有港股通道**；且本库对非 6 位数字代码**显式拒绝**、不做左补零（00700 曾被补成 000700 静默返回模塑科技的 K 线，逐条相同且无报错） | §12.1 / §13.1 阶段 3 / probe/Q1Q2 | 目前无解 —— 不要指望 get_bars("00700") 能拿到腾讯 |
| 港股（`hk`） | 港股 实时报价 | — | ❌ | **服务端有、本库未实现**：PBCombHQ + setcode=31 实测回腾讯控股 385.200、阿里巴巴－Ｗ 97.000、汇丰控股 134.300 等 13/14 个码并带中文名，且支持批量；本库未开公开接口（未经批量/延时/名称语义验证，不造接口） | §12.1 / §12.8 第 4 条 / probe/Q1Q2 | call() 走 JSON 通道发不出 PBCombHQ（二进制 protobuf），暂无绕法 |
| 港股（`hk`） | 港股 清单（可枚举代码表） | — | ❌ | **不可枚举**：PBMultiHQ 所有域的 Code 长度恒为 6，5 位码出现 0 次；没有港股域 | §12.1 / probe/Q1Q2 | 自己外部维护港股代码清单，再逐只用 setcode=31 报价（该通道本库未实现） |
| 商品期货（`futures`） | 商品期货 | — | ❌ | **不在数据源证券表内**：PBFXT/PBZST/PBCombHQ/PBMultiHQ × 6 个 market 码（7/8/28/29/30/31）× 30 个合约代码 = 全部为空，且 ERR_-1000 出现 0 次；另外 IF00 这类合约码在 symbols.parse 层就 ValueError（一个字节都没发出） | §12.1 / probe/Q1Q2 | 无通道。⚠️ 别归因成「市场码被拒」—— field3 对已知码是宽松的（600519 塞进 field3=99 照样回同一根 K 线，逐位相同） |
| 期权（`option`） | 期权 | — | ❌ | 同期货：4 条命令 × 6 个市场码 × 30 个合约代码全空、ERR_-1000 0 次，说明代码不在证券表里，不是市场码问题 | §12.1 / probe/Q1Q2 | 无通道 |
| 国债期货（`cbt`） | 国债期货 | — | ❌ | 同期货：不在证券表内，4×6×30 组合全空；合约码形态在 symbols.parse 层就被拒（OutOfScope/ValueError），请求根本发不出去 | §12.1 / probe/Q1Q2 | 无通道 |
| 外汇（`forex`） | 外汇 | — | ❌ | 同期货：4×6×30 组合全空，代码不在数据源证券表内（不是市场码被拒） | §12.1 / probe/Q1Q2 | 无通道 |
| 与品种无关（全局）（`all`） | 非 6 位数字代码（如 00700 / 02800） | — | ❌ | **显式拒绝、不做左补零**：00700 会被补成 000700 静默返回「模塑科技」的 K 线（与 get_bars("000700") 逐条相同、无任何报错）；02800→002800、00005→000005 同理 | §12.1 红线缺陷 / §13.1 阶段 3 修复 | 要哪个标的就写 6 位现役代码；港股 5 位码本库目前无法表达 |
| 与品种无关（全局）（`all`） | 日线里含「盘后固定价格交易」（15:05–15:30）的量 | — | ❌ | 盘后成交**存在但不在日线里**：逐笔能看到 InOutFlag=5 的 15:05–15:29:50 成交（30–1588 手 = 日线量的 0.013%–0.119%，逐票都在日线之外）；而日线 vol == 分时 240 点合计（\|Δ\|≤0.48 手），Bar.raw 12 个键没有一个能区分盘后成交 | §12.3 E1/E4/E8 / probe/Q3Q6 | 只能 get_ticks 自聚合（≈100 s/只）；注意 status=final 只表示「日线口径定稿」 |
| 与品种无关（全局）（`all`） | 并发取数（靠多主机提速） | — | ❌ | **机制有、实测不可行**：concurrency=4（4 条 lane 各钉一台主机）头 5 秒就被重置 3 条连接，limited_events 0→3、current 2.2→12.0、175 s 内 0 次成功取数 —— 配额按**来源 IP**，换服务器不换配额 | probe/phase5/CONCLUSION.md（原始输出 probe_phase5.out.txt） | 默认 concurrency=1；要更快只能减少调用次数，或跨进程/跨机分摊 |
<!-- END: capabilities -->

`❌` **不等于「服务端不支持」**：港股**有**报价通道（`PBCombHQ` + `setcode=31`，实测腾讯 385.200）只是本库没实现；期货则是**代码不在数据源证券表里**。两种「拿不到」的处置完全不同 —— 详见「已知限制」第 10、11 条。

---

## 市场域语义表（`PBMultiHQ` 的 `field4`）

`get_market_page` / `get_universe` / `iter_universe` 的 `market` 参数就是 `PBMultiHQ` 的
`field4`。**数据源：`tdxdata/_core.py` 的 `MARKET_DOMAINS`**（本表由它生成，勿单独改）。

<!-- BEGIN: market_domains（由 tdxdata/capabilities.py 的 market_domains_markdown() 读 tdxdata/_core.py 的 MARKET_DOMAINS 生成，勿手改） -->
| `market` | 语义 |
|---|---|
| `0` | 沪A（沪市全部股票，含 60xxxx/68xxxx） |
| `1` | 沪B（900xxx） |
| `2` | 深A（深市全部股票，含 00xxxx/30xxxx） |
| `3` | 深B（200xxx/201xxx） |
| `4` | 债券/回购 |
| `5` | 债券（另一类） |
| `6` | 全部A股（沪A ∪ 深A ∪ 北交所） |
| `7` | B股 |
| `8` | 科创板（68 段；与 0 **不等**） |
| `9` | ETF/基金 |
| `10` | 全品种榜 |
| `11` | 指数 |
| `12` | 北交所（920xxx/430xxx/83xxxx/87xxxx） |
| `13` | 配债 |
| `14` | 创业板（30xxxx） |
<!-- END: market_domains -->

几条**会直接改变结论**的性质（2026-10-27 实网逐页取全比对得出）：

* **`0` 不是「科创板」**，是**沪市全部股票**（含 `60xxxx`/`68xxxx`）—— 老表把 `0` 标成科创板，会让「沪A 里有没有 68 段」这类问题答错。
* **`2` 不是「创业板」**，是**深市全部股票**（含 `00xxxx`/`30xxxx`）。
* **`6` = `set(0) | set(2) | set(12)`**（沪A ∪ 深A ∪ 北交所）—— **不是** `set(0) | set(1) | set(2)`：`1` 是沪B，而 `6` 里**没有** B 股。
  所以「全部 A 股」请直接 `market=6`，**别自己拼 0+2**（会漏北交所 354 只）。
* **`8` 与 `0` 不等**：`8` 只含 `68xxxx`，是 `0` 的真子集。
* `9`/`10`/`11`/`12`/`13`/`14` 这 6 个域**实测有数据**但老表没登记，现已补上（`9`=ETF/基金、`11`=指数、`12`=北交所…）。
* `3`/`4`/`5`/`7` **沿用旧标注、未逐个复验** —— 如实标注，不假装查过。

## API 一览

| 方法 | 返回 | 说明 |
|---|---|---|
| `products(domain=None)` | `list[ProductInfo]` | 421 个数据产品，可按域过滤 |
| `domains()` | `list[str]` | 17 个域 |
| `capabilities(*, instrument=None, available_only=False)` | `Table` | **能力边界表**：品种 × 接口 × 有无 × 依据 × 绕法；静态注册表，**一个请求都不发**；筛完为空抛 `EmptyResult` |
| `get_bars(symbol, period, count, adjust)` | `list[Bar]` | K 线；`count > 416` 自动翻页 |
| `get_breadth(code, count, adjust)` | `Table` | 非价格序列（涨跌家数/停板家数/新低点位），命名字段；未登记代码抛 `NotSupportedError`；只支持日线 |
| `get_minutes(symbol)` | `list[MinutePoint]` | 当日分时 |
| `get_ticks(symbol, count=200)` | `list[Tick]` | 逐笔成交，自动翻页，按时间升序 |
| `get_indicators(symbol, indicators, period, count)` | `Table` | 由 K 线派生，行一一对应 |
| `get_price_limits(symbol, prev_close, is_st)` | `tuple` | `(涨停价, 跌停价)` |
| `get_market_page(market, offset, page_size, cols)` | `list[Quote]` | 行情一页 |
| `get_universe(market, max_pages, page_size)` | `list[Quote]` | 翻页枚举 + 去重；`meta` 带 `truncated`/`pages`/`rows`/`estimated_cost_seconds`（**达上限就是 `truncated=True`**，不再把 1000 只当全量 —— 要全量请用 `iter_universe()`） |
| `iter_universe(market=6, page_size=80, max_pages=None)` | 生成器 | **逐页取全**，`max_pages=None` = 翻到底；可边取边停（全量约 5577 只 / ≈158 秒） |
| `get_quotes(symbols, batch, on_missing="raise")` | `list[Quote]` | 批量报价；缺码单发兜底。`on_missing`：`raise`（默认，抛 `EmptyResult` 点名）/ `skip`（不抛，缺失清单进 `meta["missing"]`）/ `report`（同 skip + 警告一次） |
| `get_name(symbol, market=None)` | `str` | 代码 → 名称 |
| `get_boards(kind)` | `Table` | `all` / `industry` / `concept` / `style` / `area` / `industry1~3`；`change_pct` 是**真值**（见下） |
| `get_board_members(board)` | `Table` | 概念板块成分股（含入选理由） |
| `get_block_data(block, spec, field2)` | `Table` | 块数据；`spec="类别,级别,起始,数量"` |
| `get_block_rows(block, kind, level, max_rows)` | `Table` | 块数据按页取全（单页上限 100 行） |
| `get_samples(symbols, batch)` | `Table` | 批量抽样；`pre_close` 可信，其余 `f0..fN` 列序未定 |
| `get_board_stat(f3, f5, f2)` | `Table` | 板块成分统计，第一行是板块本身 |
| `get_f10(symbol, section)` | `Table` | 公司简介 / 股东 / 分红… |
| `get_finance(symbol, kind)` | `Table` | 资产负债 / 利润 / 现金流 / 主要指标 |
| `get_xdxr(symbol)` | `Table` | 分红融资 |
| `get_capital_flow(symbols=None)` | `Table` | 资金流排行 / 单码 / 批量 |
| `get_market_stat(kind)` | `Table` | 涨跌停家数 / 行业涨跌 / 情绪 |
| `get(name, **kwargs)` | `Dataset` | 数据中心产品（表格在 `.table`） |
| `call(cmd, ctl, params)` | `Table` | 直调底层命令，参数给错抛 `ParamError` |
| `timeliness(kind, **kw)` | `dict` | **取数前问时效**：四态 + 服务器时刻 + 收盘截止（15:35） |
| `rate_stats()` | `dict` | 当前节流间隔、被限流次数、冷却主机数 |

```python
# 缓存默认关闭（保持"每次都真的问服务端"的可预测语义）
from tdxdata._engine.cache import TTLCache
with tdxdata.Client(cache=TTLCache(ttl=300)) as c:
    c.get("holiday")     # 第二次命中缓存，不占用限流配额
```

## `get_boards` 的 `change_pct` 怎么来的（阶段 4 修正）

**它不是造出来的，是算出来的**：`(now / close - 1) * 100`，单位是**百分数**（`1.18` = +1.18%）。`now` / `close` 两列也一并给出，可就地复核分子分母。

| `kind` | `change_pct` 来源 |
|---|---|
| `concept` / `style` / `area` / `industry1` / `industry2` / `industry3` | 同一条 `PBMultiHQ` 回包的 `NOW`(现价) / `CLOSE`(昨收)，**100% 非空** |
| `industry` | 该通道（`app_bklist_ag`）**没有价格**，所以**多打一轮** `industry2`（`PBMultiHQ field4=10001`）用**同码**回填 `NOW`/`CLOSE`；2026-10-27 实网 **128/128 同码全中** |
| `all` | 两条支路各按上表；`meta["change_pct_method"]` 会分开写清 |

三条必须知道的约定：

1. **口径写在 `meta["change_pct_method"]`** 里（`PBMultiHQ.NOW/CLOSE` 或 `industry2 回填`），别猜。
2. **取不到就是 `None`，不是 `0`**：`close <= 0`、`now <= 0`、或任一缺失 → `None`。「0%」是一个断言（今天没涨没跌），不能拿来当「我不知道」的占位符。
3. `industry` 支路**回填失败**的单条留 `None`，代码记进 `meta["change_pct_unavailable"]`（附 `meta["change_pct_note"]` 说明），不会替你猜一个数。

```python
t = c.get_boards("concept")
t.rows[0]["change_pct"]        # 1.18（百分数）
t.meta["change_pct_method"]    # 'PBMultiHQ.NOW/CLOSE'
t.meta.get("dropped_empty_cols")   # 整列全空时才会有这个键
```

## 指标序列（`get_breadth`）

`880xxx` / `881xxx` 里混着两类完全不同的东西，**返回形状一模一样**：

· **板块指数**（`880903` 水产品、`880952`、`881007` 油气开采…）—— OHLC **是价格**；
· **统计 / 广度序列**（`880005` 涨跌家数、`880006` 停板家数…）—— OHLC 是**家数或指标点位**。

同一条 `PBFXT`、同一个解码路径，库**没法从数据本身**分辨这根是价格还是家数。
所以这 4 个代码被登记为「非价格」，`get_bars()` 会**明确拒绝**并指路：

```python
c.get_bars("880005")
# tdxdata.errors.NotSupportedError:
#   880005 是「涨跌家数」指标，其 OHLC 是四个家数而非价格。
#   价格序列请用 get_bars；广度序列请用 get_breadth("880005")。

t = c.get_breadth("880005", count=5)
t.cols
# ['date', 'series', 'series_name', 'down_count', 'total', 'flat_count', 'up_count']
t.rows[-1]["up_count"]      # 上涨家数（不是「收盘价」）
```

**已登记（只登记已实证的这 4 个，多一个都要先有实证）：**

<!-- BEGIN: series_kinds（由 tdxdata/series_kinds.py 生成，勿手改） -->
| 代码 | 名称 | 字段映射（`Bar` 字段 → 标准名） | 说明 |
|---|---|---|---|
| `880005` | 涨跌家数 | `open`→`down_count`、`high`→`total`、`low`→`flat_count`、`close`→`up_count` | 四项之和不等于 total，差额为停牌/无成交（实测每日差 5–7 家）；⚠️ `raw` 里另有 `up`/`down` 两列，取值与 up_count/down_count 不同（实测同一根 435/2163 vs 606/4922），含义未实证，勿混用 |
| `880006` | 停板家数 | `close`→`limit_up_count`、`open`→`limit_down_count` | 其余字段无定义（high/low 不是「最高/最低停板家数」，只是占位） |
| `880641` | 历史新低 | `close`→`value` | 同 880866：指标点位，不是家数；open/high/low 无定义 |
| `880866` | 近期新低 | `close`→`value` | 带小数的**指标点位**，不是家数；open/high/low 无定义 |
<!-- END: series_kinds -->

`get_breadth()` 的返回是 `Table`：`cols = ["date", "series", "series_name"] + 该序列的标准字段`，
每行一根日线；行里另带 `raw`（服务端原始字段，一个不丢）与 `provisional`（该根是否盘中未定稿）；
`Table.meta` 里带 `field_map`（`Bar` 字段 → 标准名）、`note`、`unavailable`（该序列**没有定义**的字段），
以及 `as_of` / `status` / `fetched_at` / `source` / `elapsed`。

⚠️ **未登记的代码一律不猜**：`get_breadth()` 对它们直接抛 `NotSupportedError` 并列出上面 4 个代码。
因为按位置猜含义 = 把一个量当另一个量用，比报错糟糕得多。要原始数据走 `call()` 或 `_engine` 原始通道。

⚠️ **不是按 `880`/`881` 前缀拒绝**：`880903` / `880952` / `881007` 等是真价格的板块指数，
`get_bars()` 照常可用。判据只有一处：`tdxdata/series_kinds.py` 里登记且实证过的代码。

---
## 性能与限流

```
单次 K 线请求          2.2s       （稳态被节流闸门兜住）
20 次调用建连数         1 条
get_universe(20 页)    ~44s
数据中心单次命令         ~0.4s      （缓存命中接近 0）
```

瓶颈在**服务端限流**，不在库：实测每 0.2 秒连发 8 次就会被踢，间隔 2.2 秒可长期稳定。

因此节流闸门是**进程级单例** —— 同一进程里多个 `Client` 共享同一条时间线。
这不是可以随便调的参数，调快只会更容易被踢。库内含自适应退避与主机冷却，
被踢后会自动恢复。

---

## 并发（`Client(concurrency=)`，**默认关**）

```python
import tdxdata

with tdxdata.Client() as c:                     # 默认 concurrency=1：完全串行，不增加任何服务器压力
    t = c.get_market_stat("industry")           # 31 次调用 × 2.2 s ≈ 68 s

with tdxdata.Client(concurrency=4) as c:        # 显式开 4 条 lane（上限 = 内置服务器数 4）
    t = c.get_market_stat("industry")           # 只影响"彼此独立的多次调用"
    st = c.rate_stats()
    print(st["concurrency_effective"], st["lanes"])   # 4 ['121.37.180.65', ...]
```

| `concurrency` | 行为 |
|---|---|
| `1`（默认） | **完全串行**：不起线程池、不建额外连接、`pacer.wait()` 不传 host。与阶段 4 逐字节相同（27 份黄金样本差异 0） |
| `2..4` | 建 n 条 lane，第 i 条**钉死** `HOSTS[i]`；每条 lane 一条长连接 + 一个独立的 `pacer` 槽位 |
| `>4` / `<1` / 非整数 | `ValueError`（**不截断**：第 5 条 lane 只能和别的 lane 共用槽位，不会更快，只会多占一条连接） |

目前**只有** `get_market_stat('industry')` 的 30 个一级行业码走这条路（它们彼此独立，
结果按 codes 原序拼装）。翻页、先目录后明细这类有顺序依赖的链路**一律串行**。

### 实测：**按来源 IP 配额，并发不可行**（2026-10-27，本机，原始输出未修饰）

| 轮次 | 耗时 | `limited_events` 增量 | `hosts_tracked` |
|---|---|---|---|
| 串行 `concurrency=1` | **68.64 s**（439 行 × 8 列，31 次调用） | **+0** | 0 |
| 并发 `concurrency=4` | **未达成**：头 5 秒即被服务端重置，之后 175 s 内 0 次成功取数 | **+3**（`current` 2.2→12.0，3 台进冷却） | 4 |

结论原话：**按来源 IP 配额，并发不可行。** 4 台服务器同时建连时，服务端直接重置了其中
3 条握手（`10053/10054`）——换成 4 台不同的**服务器**并没有换来 4 份**配额**。
per-host 桶机制本身工作正常（`hosts_tracked` 0→4），但**桶独立 ≠ 配额独立**。

所以 `concurrency` 默认就是 `1`：**这不是"优化还没做"，是实测结论。**
要更快只有两条路：**减少调用次数**，或**跨进程/跨机分摊**（后者需自行验证配额口径）。
原始输出见阶段 5 实测记录 `reports/refactor/probe/phase5/probe_phase5.out.txt`（仓库外）。

---

## 常见问题

**Q：需要安装通达信客户端或开终端吗？**
不需要。库直连公开行情服务器，装完 `import` 即可。

**Q：要注册账号或 API Key 吗？**
不需要。

**Q：为什么老是被断开 / 连不上？**
服务端有频率限制。库已内置节流（约 2.2 秒一次）和自动重连，正常使用不会触发；
自己开多进程并发才是最常见原因，同一进程内多个 `Client` 是安全的。

**Q：为什么没有五档盘口？**
这条通道的服务端响应里没有买卖盘字段。库不会返回假数据，
调用相关方法会抛 `NotSupportedError` 而不是给你一堆空值。

**Q：为什么有些列名是 `N001` / `c3` / `f4`？**
服务端不返回中文表头，库也不替它编名字 —— 编了你就分不清"真的是这个含义"还是"库猜的"。
这类字段原值完整保留在 `raw` 里，`docs/VERIFICATION.md` 列了哪些字段为什么没定名。

**Q：能取历史逐笔吗？**
只能取当日。

**Q：Windows 能用吗？**
能，纯 socket，跨平台，无编译依赖。

**Q：可以商用吗？**
代码是 MIT 许可。但数据来自第三方公开服务器，**本项目不拥有数据**，请自行确认合规性。

---

## 已知限制

**共 26 条。** 每一条都按同一结构写：**现象**（你会看到什么）→ **为什么**（根因一句）→
**怎么办**（一个方法名或一行代码）→ **依据**（能自己复核的出处）。
写这么细是因为：这些限制里有 3 条曾让人**拿着错结论去下判断**，光写"不支持"救不了任何人。

跑一遍 `Client.capabilities()` 可以拿到机器可读版（含"服务端有但本库未实现"这种中间态）：

```python
with tdxdata.Client() as c:
    for r in c.capabilities(available_only=False):
        print(r["instrument"], r["capability"], r["available"], r["reason"][:40])
```

### 一、数据口径（读错会得出反结论）

**1. 日线「当根」是盘中实时累计值，数值里没有"未定稿"标记**
- 现象：同一交易日内 14:44 取两市成交额 **13,130.88 亿**，收盘后是 **14,609.58 亿**（+11.3%）。
- 为什么：服务端把"当天到目前为止"累计成一根 K 线，协议里**没有** `is_final` 字段。
- 怎么办：看 `meta["status"]`（`final`/`provisional`/`pending`/`delayed`）或 `Bar.provisional`；
  只有 `final` 能当定稿用（截止点 `CLOSE_CUTOFF = 15:35`，理由见第 8 条）。
- 依据：`§12.3` / `timeliness.py`

**2. `880005`/`880006`/`880866`/`880641` 的 OHLC 是家数或点位，不是价格**
- 现象：`get_bars("880005")` 抛 `NotSupportedError`；`get_breadth("880005")` 给的是
  `up_count`/`down_count`/`flat_count`/`total`。
- 为什么：这 4 个"代码"其实是统计序列，硬按价格解释会得到"股价 435 元"这种胡说。
- 怎么办：走 `get_breadth(code)`；字段映射见上面的 series kinds 表。
- 依据：`§4.3` / `series_kinds.NON_PRICE_SERIES`

**3. `Quote` / `Bar` 没有涨跌幅字段**
- 现象：`.change_pct` 不存在（只有板块表有）。
- 为什么：协议里没有这一列（历史上有过 `20ZAF` 这个"幽灵列"，阶段 4 已确认并删掉相关注释）。
- 怎么办：`(close / pre_close - 1) * 100` 自算，**先判 `pre_close > 0`** ——
  停牌/新股/异常会回 0，直接除会得到 `inf` 或假跌停。
- 依据：`§12.5` / 阶段 4 报告

**4. `000300`/`000852` 要加 `sh` 前缀；`000001` 不是上证指数**
- 现象：`get_bars("000001")` 拿到的是**平安银行**；上证指数是 `999999`。
- 为什么：同一串数字在沪深两市号段里重名（`000300` 在深市是"华润三九"）。
- 怎么办：指数写 `999999` / `sh000300` / `sh000852`；不确定就先 `get_name()` 验一下。
- 依据：`§6.2` 旧第 4 条 / 「代码写法」一节

**5. `880005` 的四项之和 ≠ `total`**
- 现象：`up_count + down_count + flat_count` 比 `total`（映射在 `high`）少 **5–7 家**，每日不同。
- 为什么：停牌与"有代码但当日无成交"的票不计入四项、但计入 `total`。**差额是口径，不是 bug。**
- 怎么办：要"全市场家数"用 `total`；要"涨/跌/平"用三项。别强行配平，更别把差额填成 0。
- 依据：series kinds 表 / `§12.3`

**6. `change_pct` 在查不到对应板块时留 `None`**
- 现象：`get_boards("industry2")` 里部分行 `change_pct is None`，清单在
  `meta["change_pct_unavailable"]`。
- 为什么：板块当期数据里找不到同名对应项（停牌/新设/重命名），此时**没有**可靠取值。
- 怎么办：按 `None` 处理，**别当 0**；用 `meta["change_pct_unavailable"]` 判断有多少行是这种情况。
- 依据：`§12.5` / 阶段 4

**7. 盘后固定价格交易（15:05–15:30）不计入日线**
- 现象：`status=final` 的日线里**没有**这半小时的成交。
- 为什么：日线 `vol` == 分时 240 点合计（7 只逐票 `|Δ| ≤ 0.48 手`），而逐笔通道能看到
  `InOutFlag=5` 的 15:05–15:29:50 成交（30–1588 手）**在日线之外**；`Bar.raw` 的 12 个键
  里没有任何一个能区分盘后成交。
- 怎么办：`final` 只表示"**日线这个口径**定稿了"，不代表"当天全市场成交都在里面"。
  要含盘后的口径只能 `get_ticks` 自聚合（≈100 s/只）。
- 依据：`§12.3` E1/E4/E8

**8. `HqTime` 是"最后成交时刻"，不是"当前时刻"**
- 现象：收盘后它冻结在 **15:30–15:33**（深主板 15:33:0x、创业板最晚 **15:33:57**），
  15:45 去取还是 15:33。
- 为什么：服务端写快照时刻 = 最后一笔成交时刻，与"现在几点"无关。
- 怎么办：**别用 `HqTime` 判数据新鲜度**。FINAL 判定走"服务器日期 + 本机时分 + 指数
  `HqTime >= 15:00`"三重交叉校验；本机时钟偏差大或跨日时自动降级 `provisional`（宁可保守）。
- 依据：`§12.3` E7 / `timeliness.py`

**9. `trade_count` / `bs_flag` 声明式不可用**
- 现象：`Tick.trade_count` 与 `Tick.bs_flag` **恒为 `None`**。
- 为什么：`PBTickEx` 一行就是一笔，协议里没有"笔数"字段；`raw[InOutFlag]` 的 0/1 与涨跌
  只有 65.9%/76.8% 相关，按它映射会**整批搞反**。
- 怎么办：要笔数用 `len(ticks)`；方向字段没有可靠来源，别自己映射。
  原因写在 `models.UNPROVIDED_TICK_FIELDS`（机器可读），不是"忘了实现"。
- 依据：`models.UNPROVIDED_TICK_FIELDS`

### 二、拿不到的东西（品种与接口边界）

**10. 期货 / 期权 / 国债期货 / 外汇：不在数据源证券表内**
- 现象：`PBFXT`/`PBZST`/`PBCombHQ`/`PBMultiHQ` × 6 个市场码（7/8/28/29/30/31）× 30 个合约代码
  = **全部为空**，且 `ERR_-1000` 出现 **0 次**；`get_bars("IF00")` 在 `symbols.parse` 层就报错
  （一个字节都没发出）。
- 为什么：**这些代码不在证券表里**。⚠️ 注意这**不是**"市场码被拒" —— `field3` 对已知代码是
  **宽松**的：把铁定存在的 `600519` 塞进 `field3=99`（不存在的市场码）服务端照样回同一份 K 线
  （`Close=1355.900024` 逐位相同）。所以别再归因到市场码上。
- 怎么办：无通道。**别再去试 7/8/28/29/30/31**，那只会白烧请求还把主机打进冷却。
- 依据：`§12.1` / `reports/refactor/probe/Q1Q2/`

**11. 港股：报价通道存在但本库未实现；K 线/分时无通道；清单不可枚举**
- 现象：`get_bars("00700")` 拿不到腾讯（5 位码会被**显式拒绝**，见第 12 条）。
- 为什么：**三件事必须分开说** ——
  ① `PBCombHQ` + `setcode=31` **实测能回港股实时报价**（腾讯控股 385.200、阿里巴巴－Ｗ 97.000、
  汇丰控股 134.300，14 个码里 13 个正确解析并带中文名，**且支持批量**）→ 服务端有，**本库未实现**；
  ② `PBFXT`/`PBZST` **没有港股通道** → K 线/分时拿不到；
  ③ `PBMultiHQ` 所有域的 `Code` 长度**恒为 6**、5 位码出现 0 次 → **清单不可枚举**。
  ⚠️ **不许把这句写成"服务端不支持港股"** —— 服务端明明回得出腾讯的价。
- 怎么办：目前没有公开接口能取。也别指望用 `call()` 发 `PBCombHQ`（那是二进制 protobuf，
  JSON 通道发不出去）。未经批量/延时/名称语义验证就开接口，本库不做（§8）。
- 依据：`§12.1` / `§12.8` 第 4 条 / `probe/Q1Q2/`

**12. 非 6 位数字代码显式拒绝（不做左补零）**
- 现象：`get_bars("00700")` 抛 `OutOfScope`，而不是返回别人的数据。
- 为什么：这是本项目踩过最狠的红线缺陷 —— 以前会补零成 `000700`，**静默返回"模塑科技"的
  K 线**，与 `get_bars("000700")` 逐条相同且无任何报错（`02800→002800`、`00005→000005` 同理）。
  任何 5 位数字串都会这样命中某个 A 股。
- 怎么办：写 6 位现役代码。宁可报错，也不要"猜一个像的"给你。
- 依据：`§12.1` 红线缺陷 / 阶段 3 修复

**13. 行业板块拿不到成分股**
- 现象：`get_board_members("881xxx")` 抛 `NotSupportedError` 并指出替代接口，**不返回残缺数据**。
- 为什么：只有**概念**板块有成分通道（`app_bklist_ag` / `zttz_xggp`）；行业板块只有点位。
- 怎么办：行业板块要"走势"就用 `get_bars("881xxx")`；要个股级信息请自己按行业分类映射。
- 依据：`§13.1` 阶段 2 / `sections/boards.py`

**14. `get_breadth` 只支持日线**
- 现象：`get_breadth("880005", period="5m")` 抛 `NotSupportedError`。
- 为什么：分钟级家数序列**从未实证**（服务端给不给、字段怎么映射都是未知），
  未实证就不开接口。
- 怎么办：要高频率只能用 `get_universe(market=6)` 拉全市场快照自己统计
  （一次 ≈158.6 s，很贵，见第 19 条）。
- 依据：`§13.1` 阶段 2

**15. `get_board_stat` 不能指定板块**
- 现象：它没有板块参数，返回哪个板块**由服务端决定**。
- 为什么：该命令的 `ctl` 里没有板块维度的入参（协议层就没这个位置）。
- 怎么办：要指定板块请用 `get_board_members`（成分）或 `get_bars("881xxx")`（点位）。
- 依据：`§13.1` 阶段 2 / 本库 `sections/boards.py`

**16. 五档盘口（买一~卖五）不存在**
- 现象：`Quote` 上没有 `bid*`/`ask*` 字段，也没有取盘口的公开方法。
- 为什么：**协议层没有买卖盘字段** —— `HQServ` 回包里没有 bid/ask 列，且 `PBMultiHQ` 没有代码位
  （2026-10-25 解构确认）。不是本库没解析。
- 怎么办：最优价用 `get_quotes(...).NOW`；一页行情用 `get_market_page`。
- 依据：`§3.2` / `__init__` 能力表

### 三、慢、限流与分页

**17. 被限流是常态，库只降低概率、不能消除**
- 现象：偶尔抛 `RateLimited`；`rate_stats()["limited_events"]` 会累加；被踢后进 30 s 冷却，
  一次限流事件平均多花 **≈28.15 s**。
- 为什么：服务端按**来源 IP** 计配额（实测见第 18 条），本库的 2.2 s 间隔只是把撞枪概率压低。
- 怎么办：别在循环里高频重试；先看 `rate_stats()` 的 `current`/`cooling_hosts`；
  批量优先用 `get_quotes` / `iter_universe`，别自己 for 循环逐只单发。
- 依据：`§12.6` / `probe/phase5/`

**18. 并发按「来源 IP」配额，实测不可行（`concurrency` 默认 1）**
- 现象：`Client(concurrency=4)` 跑 `get_market_stat("industry")`，**头 5 秒**就
  `limited_events` 0→3、`current` 2.2→12.0、3 台进冷却，之后 **175 s 内 0 次成功取数**；
  同一台机器串行只要 **68.64 s** 且零限流。
- 为什么：配额按**来源 IP**，不按"IP × 服务器"。机制本身是好的（`hosts_tracked` 0→4，
  4 条 lane 各钉一台主机），但 **桶独立 ≠ 配额独立** —— 换 4 台服务器没换来 4 份配额。
- 怎么办：保持默认 `concurrency=1`。要更快只有两条路：**减少调用次数**，或**跨进程/跨机分摊**。
  这是实测结论，不是"优化还没做"。
- 依据：`reports/refactor/probe/phase5/CONCLUSION.md`（原始输出 `probe_phase5.out.txt`）

**19. `get_market_stat("industry")` 慢：31 次调用 × 2.2 s ≈ 68 s**
- 现象：一次约 **68 s**（实测 68.20 s / 68.64 s 两轮），439 行；全 A 枚举（`market=6`，5577 只）
  约 **158.6 s**。
- 为什么：1 次目录 + 30 个一级行业各一次明细，串行过同一条 2.2 s 节流闸门；
  重试贡献 **0 s**（不是"重试拖慢"）。
- 怎么办：结果进 `Client(cache=TTLCache(ttl=...))`；只要几个行业就先 `get_boards("industry")`
  再挑着取；别在循环里反复调。
- 依据：`§12.6` / `probe/phase5/`

**20. 分页枚举保证不重复，不保证不遗漏**
- 现象：跨两次枚举拼起来时，可能出现"上一页有、下一页没有"的票。
- 为什么：服务端按涨幅排序，盘中榜单会漂移；`offset` 分页对着一个会动的序列翻。
- 怎么办：跨次拼接按代码**去重**；要"完整集合"请在收盘后取，或用 `iter_universe` 一次翻到底；
  `meta["truncated"]` 一定看（`True` 表示没取全，不是全量）。
- 依据：`§6.1` / `_core.paginate`

### 四、方法学边界（门禁覆盖不到的地方）

**21. 15 份黄金样本是阶段 1 之前录的：容器级 meta 不受它们约束**
- 现象：这 15 份没有容器 `meta`、也没有 `meta_sha256`。
- 为什么：按纪律**不重录**（重录会把回归一起盖掉）。它们只比对**数据**，不比对容器级
  `clock_note`/`provisional`/`as_of`。
- 怎么办：那部分由 `tests/test_series_containers.py` / `tests/test_timeliness.py` 守 ——
  是"换了守卫"，不是"没人管"。
- 依据：复核 02 `§6` / `§13.3`

**22. 多 blob 分帧路径没有被黄金样本覆盖**
- 现象：60+ 帧里 `n_blobs` **全为 1**，多帧分支在回放里从没走过。
- 为什么：录制窗口内没出现过跨帧响应；按纪律**不造数据**去凑覆盖。
- 怎么办：这是**已知缺口，不是"已验证"**。遇到疑似截断/串帧请附原始 frame 报 issue。
- 依据：`tests/fixtures/golden/README.md`「已知缺口」

**23. `elapsed` 的值不参与黄金比对**
- 现象：任何样本差异里都不会出现 `elapsed`。
- 为什么：它是性能量、不携带数据信号；放进比对会让门禁随机器快慢抖动。
- 怎么办：所以"`elapsed` 永久恒 0"这类退化**原理上抓不到**，只能靠计时类测试兜
  （`test_timeliness.py` 等）。别指望黄金样本发现它坏了。
- 依据：`§13.3` 第 2 条（`INFO_ONLY_META_CARRIERS`）

**24. `get_samples` 的浮点列序未锚定、个数会变**
- 现象：`f0`、`f1`… 的个数盘中递增，`f*` 的语义只锚定了 `f0`（开盘）与 `pre_close`。
- 为什么：服务端按"当前时刻"下发列，没有稳定 schema。
- 怎么办：**别写死个数，别把 `f*` 当 high/low/volume**；只信任 `f0` 与 `pre_close`。
- 依据：`§13.1` / README 旧第 5 条

**25. 块数据（`get_block_data`）的块名由服务端下发**
- 现象：块名不是通用常量，`get_block_data("MStock_XXX")` 的可用性随服务端配置变。
- 为什么：块名是服务端侧的台账键，本库只是转发。
- 怎么办：目前只验证了少数几个（如 `MStock_ZLJX_ADDE_R`）；用之前先 `get_block_rows()` 探一下。
- 依据：`§13.1` / `sections/` 模块文档

**26. 资金流净值口径与部分第三方软件不同**
- 现象：同一只票的"主力净流入"数值能和别的软件差出可观比例。
- 为什么：各家对"主力"的委托单大小分类阈值不同，本库不另做阈值，只如实给服务端口径。
- 怎么办：**不宜跨源比较**；要比就同一源比时间序列。
- 依据：`§13.1` / `docs/VERIFICATION.md`

---

## 数据可信度

每个字段名、单位、参数都经过实机验证，并且要求**两条独立通道互相对上**才算通过。
具体怎么验的（单位判定的恒等式、交叉验算模板、哪些字段没定名及原因）见：

📄 **[docs/VERIFICATION.md](docs/VERIFICATION.md)**

变更与破坏性改动见 **[CHANGELOG.md](CHANGELOG.md)**。

---

## 免责声明

本项目仅用于**学习与研究**，数据来自第三方公开服务器，不保证准确性、完整性与时效性。
**不构成任何投资建议**，请勿用于实盘交易决策或商业分发。
使用前请自行确认符合当地法律法规与数据源服务条款。

---

## 许可

[MIT](LICENSE)。数据来自第三方公开服务器，**本项目不拥有数据**，详见上方免责声明。
