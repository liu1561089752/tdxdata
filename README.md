# tdxdata

**A 股行情与数据中心，一行 import 就能用。纯标准库，零第三方依赖。**

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![Tests](https://img.shields.io/badge/tests-1762%20passed-success)
![Products](https://img.shields.io/badge/%E6%95%B0%E6%8D%AE%E4%B8%AD%E5%BF%83%E4%BA%A7%E5%93%81-424-orange)
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

**一句话**：把 A 股的 K 线、分时、逐笔、报价，以及龙虎榜 / 财报 / 资金流等 424 个数据产品，
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
| 龙虎榜 / 财报 / 资金流 / 分红 / 大盘统计…（424 个产品） | `get("产品名")` |
| F10 / 财务报表 / 分红融资 | `get_f10()` / `get_finance()` / `get_xdxr()` |

### 取不到的（会明确抛异常，不返回假数据）

| 不能取 | 原因 |
|---|---|
| **五档盘口**（买一卖一…） | 该通道没有买卖盘字段 |
| **行业板块成分股** | 只有概念板块支持按板块取成分股 |
| **年线 / 3 分钟线** | 服务端返回 0 行 |
| **历史逐笔** | 只能取当日 |
| **期货 / 期权 / 国债期货 / 外汇的合约 K 线/分时/五档** | **这些合约代码不在本数据源的证券表里** —— 库会抛 `OutOfScope`。⚠️ 注意归因：这**不是**「服务端不支持某个市场码」。已实证 `PBFXT` 的 `field3` 对已知代码是宽松的（`600519` 在 `field3=1/99/30` 三种下返回**逐位相同**的 K 线）。⚠️ 但也别读成「期货什么都没有」：**合约清单 / 会员持仓榜单 / 主力连续最新价快照**在数据中心侧实测可用（见「已知限制」第 17 条）。 |
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

    # ⑤ 数据中心产品（424 个之一）
    d = c.get("holiday")
    print(d.table.shape, d.table.cols[:3])
    print(d.rows[0])            # Row 是 dict 语义，可按列名取值
    # (58, 6) ['N001', 'N002', 'N003']
    # Row{'N001': '2026-05-01', 'N002': '星期五', 'N003': '中国', ...}
```

### 返回类型

<!-- BEGIN: api_types（由 tdxdata/_docgen.py:api_types_table() 生成，勿手改） -->
| 调用 | 返回 | 字段 / 公开成员 |
|---|---|---|
| `get_bars()` | `list[Bar]` | `market` `code` `date` `open` `high` `low` `close` `vol` `amount` `period` `time` `raw` `adjust` `provisional` `symbol` `vol_unit` |
| `get_minutes()` | `list[MinutePoint]` | `code` `time` `price` `avg_price` `vol` `market` `raw` `symbol` |
| `get_ticks()` | `list[Tick]` | `market` `code` `time` `price` `vol` `trade_count` `bs_flag` `raw` `as_markdown_table` `symbol` |
| `get_quotes()` / `get_market_page()` | `list[Quote]` | `market` `code` `price` `pre_close` `open` `high` `low` `vol` `amount` `bid` `ask` `server_time` `name` `raw` `symbol` |
| `get()` | `Dataset` | `name` `table` `params` `cols` `data_date` `rows` `to_dict()` `to_dicts()` `to_json()` |
| `get_f10()` / `get_finance()` / `get_xdxr()` | `Table` | `cols` `rows` `cmd` `domain` `provenance` `ragged` `as_of` `column()` `inherit_provenance()` `is_ragged` `row()` `set_cols()` `shape` `status` `to_dict()` `to_dicts()` `to_json()` |
<!-- END: api_types -->

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

<!-- BEGIN: enum_adjust（由 tdxdata/_docgen.py:adjust_enum_table() 生成，勿手改） -->
| 值 | 含义 | 协议 `field8` |
|---|---|---|
| `tdxdata.Adjust.NONE` | 不复权 | `1` |
| `tdxdata.Adjust.QFQ` | 前复权 | `3` |
| `tdxdata.Adjust.HFQ` | 后复权 | `2` |
<!-- END: enum_adjust -->

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
| 数据中心 | 424 个产品、17 个域：龙虎榜 / 财报 / 资金流 / 分红 / 大盘统计… |
| 结构化接口 | F10、财务报表、分红融资（语义化英文列名） |
| 本地缓存 | `Client(cache="auto")`（按命令的**实测**新鲜度给 TTL）或 `Client(cache=TTLCache(ttl=300))`；命中不占限流配额 |
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
| A 股（沪/深）（`a_share`） | 实时涨跌家数（涨/跌/平 + 涨停/跌停，含逐分钟序列） | `get_market_breadth` | ✅ | HQServ.PBSdstat（ctl=0x11）：summary 回 5 个家数（up_count/down_count/flat_count/limit_up_count/limit_down_count），抓包回放实测 1637/3775/160/35/15，且 up+down+flat ≡ 5572（= 当日有价证券总数，27 帧恒等）；view='minute' 回逐分钟序列。⚠️ 与 get_market_stat('updown') **不是一回事**：后者走 CWServ.ph_datacenter_zdt_zdfx，是**历史近 30 个交易日**，本条是**实时快照** | eval3/M6-pb-undecoded.md §PBSdstat / tests/test_pb_breadth.py / pcap: PBSdstat 27 帧 f1+f2+f3≡5572 | 回包不含日期/时间字段 → provenance.as_of 恒 None 并带说明 note；要历史逐日请用 get_market_stat('updown')。view='minute' 的标的列与 10 个 uint32 语义未定，只进 row['raw'] |
| A 股（沪/深）（`a_share`） | 创业板（30xxxx）板块域枚举 | `get_universe` | ✅ | PBMultiHQ field4=14 实测有数据（旧表未登记）；⚠️ field4=2 是「深A全部」，不是「只有创业板」 | §12.2 / _core.MARKET_DOMAINS | — |
| A 股（沪/深）（`a_share`） | 科创板（68xxxx）板块域枚举 | `get_universe` | ✅ | PBMultiHQ field4=8 只含 68xxxx，是 field4=0 的**真子集**；field4=0 是沪A全部 | §12.2 / _core.MARKET_DOMAINS | — |
| A 股（沪/深）（`a_share`） | 全 A 股枚举（5577 只） | `iter_universe` | ✅ | field4=6 = set(0) ∪ set(2) ∪ set(12) = **5577 只**（沪A 2319 + 深A 2904 + 北A 354，重复 0）；page_size=80、max_pages>=70，实测 ≈158.6 s 翻到底 | §12.2 / probe/Q1Q2 | 别用 0+2 自己拼（会漏北交所 354 只）；只要沪深A 就用 0+2 |
| A 股（沪/深）（`a_share`） | 年线 / 3 分钟线 | — | ❌ | 服务端返回 0 行：`MIN_3` / `YEAR` 这两档 period 实测是空结果，所以 client 层**显式拒绝**（`_PERIOD_DEAD`）而不是返回空表 —— 空表会被读成「今天没数据」 | §13.1 / `client._PERIOD_DEAD` / README 能力表 | 年线用日线自己聚合；3 分钟线用 1 分钟线重采样（本库不代做） |
| A 股（沪/深）（`a_share`） | 历史逐笔（非当日） | — | ❌ | `PBTickEx` 只回**当日**逐笔：协议入参里没有「按日期取历史逐笔」这一位，不是本库没实现 | §13.1 / README 能力表（「历史逐笔：只能取当日」） | 要历史只能自己每天落盘累积（别把缺数据填 0） |
| A 股（沪/深）（`a_share`） | 五档盘口（买一~卖五） | — | ❌ | 协议层没有买卖盘字段：HQServ 回包里没有 bid/ask 列，且 PBMultiHQ 没有代码位（2026-10-25 解构确认）；不是本库没解析，是服务端不给 | §3.2 / __init__ 能力表 | 最优价用 get_quotes(...).NOW；行情列表用 get_market_page |
| 北交所（`bj`） | 北交所行情与 K 线 | `get_bars` | ✅ | 与沪深同一条 PBFXT/PBCombHQ；354 只**全部在 920 段**（430/830/870/820 一个都没有，430047 实测 EmptyResult） | §12.2 / probe/Q1Q2 | 别按 430/830 前缀判断北交所；批量报价用 get_quotes |
| 指数（`index`） | 指数行情与 K 线（999999 等） | `get_bars` | ✅ | PBFXT 同样支持指数；`get_bars("999999")` 的 raw["up"]/raw["down"] 就是当日涨跌家数且历史可回溯（免费彩蛋） | §12.3 E 组 / golden: quotes_indices | 要家数序列用 get_breadth("880005") |
| 指数（`index`） | 当日分时点位（多代码一次取，轻量抽样通道） | `get_intraday_samples` | ✅ | HQServ.PBSample（ctl=0x11）：回「代码/市场/全天点数/昨收/本帧点数/float32[]」。语义已锚定 n_points(field7) ≡ len(points)（**逐点非抽样**，25/25 帧），points[0] 是 09:30 首点，pre_close ↔ PBCombHQ.CLOSE（**昨收**）；实测 999999 一帧回 139 点 = 09:30–11:30 的 121 + 13:00–13:18 的 18 | eval3/M6-pb-undecoded.md §PBSample / tests/test_pb_sample.py（27 例）/ 离线回放：tools/m6_replay.py | ⚠️ 实证标的只有指数 999999，**个股未复核**（个股 field5 是否仍 240、是否仍每分钟一点没有证据）→ docstring 与 meta 标实验性；只要单只股票当日分时请用get_minutes；count 只是本地截尾（field7 发 1 仍回 139 点，不是服务端上限） |
| 板块（`board`） | 板块清单（concept/style/area/industry1-3） | `get_boards` | ✅ | PBMultiHQ field4=10001/10002/10004/10005/10006/10008，六类实测行数与手机端逐类一致（269/158/32/30/128/345）；change_pct=(NOW/CLOSE-1)*100 现算 | §12.5 / golden: boards_industry_s4 | — |
| 板块（`board`） | 板块成分股 | `get_board_members` | ✅ | 只有**概念**板块有成分通道（app_bklist_ag / zttz_xggp）；行业板块（881xxx）会抛 NotSupportedError 并指出替代接口，不返回残缺数据 | §13.1 阶段 2 / README 已知限制 | 行业板块要「点位」就用 get_bars("881xxx") |
| ETF / 基金（`etf`） | ETF / 基金行情与 K 线 | `get_universe` | ✅ | PBMultiHQ field4=9 实测有数据（旧表未登记这一域）；代码段 5xxxxx/15xxxx/51xxxx | §12.2 / _core.MARKET_DOMAINS | — |
| 债券 / 配债（`bond`） | 债券 / 配债行情 | `get_universe` | ✅ | field4=4（债券/回购）、5（债券，另一类）、13（配债）；⚠️ 3/4/5/7 沿用旧标注、未逐个逐页复验，如实标注而不假装查过 | §12.2 / _core.MARKET_DOMAINS | 下结论前先自己复验 4 与 5 的细分口径 |
| 港股（`hk`） | 港股 日线 / 分时 | — | ❌ | PBFXT/PBZST **没有港股通道**；且本库对非 6 位数字代码**显式拒绝**、不做左补零（00700 曾被补成 000700 静默返回模塑科技的 K 线，逐条相同且无报错） | §12.1 / §13.1 阶段 3 / probe/Q1Q2 | K 线/分时无解（不要指望 get_bars("00700") 能拿到腾讯）；但**报价**已可用：get_quotes(["hk00700"]) 回现价/昨收/涨跌幅（15 分钟延时） |
| 港股（`hk`） | 港股 实时报价 | `get_quotes` | ✅ | **可用（2026-10-29 落地）**：PBCombHQ + 市场号 31（主板）/48（GEM）/49（REIT 等），代码是 **5 位数字**。抓包实证：998 个 PBCombHQ 请求里 1188 行 setcode=31、290 只不同港股（腾讯控股 00700 386.000、比亚迪股份 01211 71.600、工商银行 01398 7.240、中芯国际 00981 47.060…），一次请求最多带 100 个码、发多少回多少。⚠️ 港股是**延时行情**：回包 DELAYMIN=15（15 分钟），别当实时用。 | reports/eval3/corpus/pb.jsonl（998 请求 / 1188 行 setcode=31 / 7 份 pcap）+ 原始请求字节解出 field3=31、field4=5 位码（notes/18 §1） | 用法：get_quotes(["hk00700"]) / ("31:00700") / ("hkg08285")。清单仍不可枚举（PBMultiHQ 所有域 Code 长度恒 6，5 位码 0 次）—— 代码要自己维护；K 线/分时仍无通道 |
| 港股（`hk`） | 港股 清单（可枚举代码表） | — | ❌ | **不可枚举**：PBMultiHQ 所有域的 Code 长度恒为 6，5 位码出现 0 次；没有港股域 | §12.1 / probe/Q1Q2 | 自己外部维护港股代码清单，再用 get_quotes(["hk00700", …]) 报价（该接口 2026-10-29 起可用；但**清单**本身仍要外部维护） |
| 港股（`hk`） | 港股 GEM / 港股其他（REIT） | `get_quotes` | ✅ | 市场号 48（GEM，08xxx）/ 49（REIT 等，00405 越秀房产信托基金）实测都有回包：48 回 9 列（含 AMOUNT）、49 回 10 列（含 ACTIVECAPITAL/ZSZ） | pb.jsonl：setcode=48 共 7 行 / 49 共 1 行 | GEM 里很多是仙股，NOW 可能为 0（停牌）—— 别把 0 当跌停 |
| 商品期货（`futures`） | 商品期货 报价 | `get_quotes` | ✅ | **可用**：PBCombHQ 市场号 28=郑商所、29=大商所、30=上期所/能源中心、66=广期所，合约码是「品种字母+年月」（MA2701 甲醇2701、V2701 PVC2701、SP2701 纸浆2701、LC2701 碳酸锂2701），连月合约是 FUL9/SCL9 这种写法。回 19 列：OPEN/CLOSE/NOW/MGJZC(昨结?)/SPECTYPE/WTB(委比)/RELAG/EXT_ZF(涨跌幅)。⚠️ 没有成交量/持仓量列（VOL/OI 不在回包里），要量得走专题（tdxrb_qh / qhlhb_sy） | pb.jsonl：setcode=28 57 行 / 29 82 行 / 30 77 行 / 66 12 行；请求侧 field3 与代码逐条解出（notes/18 §2） | 用法：get_quotes(["28:MA2701", "29:V2701", "30:SP2701", "66:LC2701"])。合约 K 线/分时/五档无通道（7727 仅笔记证据） |
| 商品期货（`futures`） | 股指期货 报价 | `get_quotes` | ✅ | **可用**：市场号 47，IH2611 上证2611 实测回 8 列（CLOSE/NOW/XSFLAG/EXT_ZF…） | pb.jsonl：setcode=47 共 2 行 | 只有报价，没有合约乘数/保证金/基差字段 |
| 美股（`us`） | 美股 报价 | `get_quotes` | ✅ | **可用**：市场号 74，代码是美股 ticker（NVDA 英伟达 / INTC 英特尔 / AMD / MU…），回 19 列（OPEN/CLOSE/NOW/MGJZC/ZSSJL/WTB/EXT_ZF），一次可带 100 只。另有数据中心的美股榜单产品（usa_gzb / usa_rqb / usa_bsb / tdxrb_hypm_usa，10–16 列，含中概与行业排名） | pb.jsonl：setcode=74 共 573 行；美股榜单：tdxrb_usa_gzb 10 列×100 行（NVDA 28974）/ usa_bsb 13 列×100 行 / tdxrb_hypm_usa 16 列 | 美股 K 线/分时无通道 |
| 外汇（`forex`） | 外汇 / 外盘指数 报价 | `get_quotes` | ✅ | **可用（品种有限）**：市场号 10=外汇（USDCNH 美元兑离岸人民币，回 21 列含 OPEN/MAX/MIN/NOW/HqTime）、27=恒生指数（HSI）、12=外盘指数（A_DJI 道琼斯 / A_IXIC 纳斯达克 / A_SPX 标普500 / CNY0 富时A50期指）。抓包里只有 USDCNH 一个汇率品种，**不要推断其它货币对也存在** | pb.jsonl：setcode=10 共 10 行（USDCNH）、12 共 43 行、27 共 10 行（HSI 22934.68） | 其他货币对（EURUSD/CNHHKD…）无实测证据；7727 侧仍是笔记证据，未接入 |
| 商品期货（`futures`） | 商品期货 | — | ❌ | **2026-10-29 复核：报价能取，K 线/分时不能**。(a) 报价 ✅：真实 App 流量里 PBCombHQ 用市场号 28/29/30/66（郑商所/大商所/上期所·能源/广期所）+ **带年份月份的合约码**（MA2701、V2701、SP2701、LC2701、FUL9）实测回 19 列（OPEN/CLOSE/NOW/MGJZC/SPECTYPE/WTB/RELAG/EXT_ZF），一次带 100 个合约、发多少回多少。旧结论「PBCombHQ 期货全空」是**探针代码写错**（IF00 这类缺年份/缺交易所前缀的码），不是通道没有，也不是市场码被拒。(b) K 线 ✅（2026-10-29 深夜实测打通）：走的是**经典二进制协议**，不是 protobuf —— 命令号 `0x122E`（App 缓存里的 ReqNo=4654），帧 ctl=`0x03`、标记 `b4 26`、48 字节定长请求体（市场号 u16 + 合约码 + 700 根）。实测 MA2612/CU2612 各 700 根、IF2611 626 根，OHLC 逐根通过 low≤min(o,c)≤max(o,c)≤high 校验；**周期也已打通**（同一个命令、同一 36 B 记录，只改请求体第 26 字节）：1m/5m/15m/30m/day/week/month 全部真机验过，分钟线自动多 Time 列；60 分钟（协议值 3）实测触发断连，**故意没开放**。（此前写「无通道」「未定位」都是错的：抓包里其实一直有这些帧，只是**帧里没有命令名字符串**，按命令名搜永远搜不到。）(c) 专题数据 ✅（7615 数据中心侧抓包实测）：tdxrb_qh 期货合约榜（12 列×100 行，含 MA2701/V2701/FU2701 与市场码 28/29/30）、qhlhb_sy 001 全市场合约清单（5 列×318 行）与 002 会员成交量/多空持仓/增减（10 列×21 行，p3=0..4 五种榜单）、qhlhb_jcgc 002 机构持仓（8 列×336 行）、qh_hqpd_qhhy 品种主力连续合约快照（5 列×10 行，now=最新价 721.3~9378.0，**是合约级价格但没有 OHLC**）。⚠️ qh_cdzl 名不副实：只回交易日列表（20×4 + 1×1），002/003 在 31 份抓包里连请求都没有，不能叫「持仓增量」 | §12.1 / probe/Q1Q2（行情侧）；reports/eval3/findings/M4-fx-futures.md §2.3-2.5（专题侧，31 份 pcap 真实回包） | 合约 K 线用 `get_futures_bars("MA2612")`（经典协议 0x122E）；分时/五档仍是 `0x122F`/`0x122B`，**未封装**（帧已确证，缺客户端调用）。要专题数据走数据中心通道：qhlhb_sy（001 合约清单 / 002 持仓与增减）/ qhlhb_jcgc（002 机构持仓）/ tdxrb_qh（合约榜）/ qh_hqpd_qhhy（主力连续最新价）—— 本库均未封装成公开方法。⚠️ 别归因成「市场码被拒」—— field3 对已知码是宽松的（600519 塞进 field3=99 照样回同一根 K 线，逐位相同） |
| 期权（`option`） | 期权 T 型报价链 | `get_option_chain` | ✅ | 三步链 2026-10-29 抓包实测打通：PBQQUnderly2(ctl=15) 回 9 个标的 → PBQQMonth(ctl=12) 回月份 2611/2612/2703/2706 + 到期日 → PBQQTXCode2(ctl=14) 回每档「行权价 + 认购代码/名称 + 认沽代码/名称」；回包是 {"Length"}+zlib+Base64 四层信封，peel() 已能剥 | pcap cap_1029_090838.pcap msgid 854/864/866；夹具 qq_*_响应.bin；tests/test_option_chain.py 9 例（请求体逐字节对照抓包） | 用法：get_option_chain("510050")（默认最近月份）、option_underlyings()、option_months("510050") |
| 期权（`option`） | 期权 行情（市场号 8） | `get_quotes` | ✅ | T 型链里每档的 f2/f5 恒为 8 —— 市场号 8 就是期权市场，与 PBCombHQ 的 setcode 体系一致，可 get_quotes(["8:10012637"]) 取行情 | cap_1029_090838.pcap（12 档 T 型链，市场号 8 全命中） | 期权没有一次性全量合约清单，只能按「标的 + 月份」现取 |
| 期权（`option`） | 期权（旧结论，已作废） | — | ❌ | 当时写「4 条命令 × 6 个市场码 × 30 个合约码全空 ⇒ 期权无通道」，**错在探针**：期权不走那 4 条命令，而是 PBQQ* 三步链 | §12.1 / probe/Q1Q2（结论作废），更正见 notes/19 | 别再用「命令 × 市场码 × 合约码」笛卡尔积猜通道；先看抓包里 App 发了什么 |
| 国债期货（`cbt`） | 国债期货 | — | ❌ | 同期货：不在证券表内，4×6×30 组合全空；合约码形态在 symbols.parse 层就被拒（OutOfScope/ValueError），请求根本发不出去 | §12.1 / probe/Q1Q2 | 无通道 |
| 外汇（`forex`） | 外汇（7727 专用通道 / 其他货币对） | — | ❌ | **7615 无外汇报价；`fx` 是「分析」不是「外汇」**：`ph_cfg_fx_sczb`（产品 `sczb`）实测 15 项全是 A 股市场/流动性指标（央行净投放 -650 亿、LPR 1Y/5Y 3.00/3.50、M2 359.63 万亿、两融 24533.74 亿、道指/纳指/标普点位…），`ph_cfg_fx_jjhqpd`（产品 `jjhqpd`）是 10 只境内 ETF 行情盘点（510300/511880…），**两者零汇率字段**；行情类 4 命令 × 6 市场码 × 30 代码全空、代码不在数据源证券表内（不是市场码被拒，同期货）。7727 侧 USDCNY 6.6746 / USDCNH 6.67267 / CNHHKD 1.17471 / CNYHKD 1.17377 只是《汇率抓包新增接口.md》§十二:286-307（2026-10-22，4 台 EX 服务器当时 2 台可用 —— 快照不是承诺）的**笔记/在线探针证据**，**不是抓包实测** —— 全部 31 份 pcap 里 7709/7727/7708 各 0 帧、`USDCNY` 字符串 0 次命中。本库未接入 7727、未实测 | 7615 侧：§12.1 / probe/Q1Q2；7727 侧仅笔记：notes/archive/接口文档/《汇率抓包新增接口.md》§十二:286-307（2026-10-22 在线探针）；抓包侧：31 份 pcap 端口直方图 7709/7727/7708 = 0 帧（reports/eval3/findings/M4-fx-futures.md §3-4） | 本库暂无公开接口。⚠️ 别把 `ph_cfg_fx_*`（`sczb`/`jjhqpd`）当外汇：那是 A 股指标面板与境内 ETF 盘点。是否接入 EX 7727 属通道攻关决策（期货/期权行情出路同此通道），不默认施工 |
| 与品种无关（全局）（`all`） | 非 6 位数字代码（K 线等非报价接口） | — | ❌ | **显式拒绝、不做左补零**：00700 会被补成 000700 静默返回「模塑科技」的 K 线（与 get_bars("000700") 逐条相同、无任何报错）；02800→002800、00005→000005 同理 | §12.1 红线缺陷 / §13.1 阶段 3 修复 | **报价**已能表达：get_quotes(["hk00700"]) 走 symbols.parse_quote()，港股按 5 位校验、美股/期货按字母数字放行；但 get_bars/get_minutes 等 K 线接口仍只认 6 位 A 股代码 —— 那里继续显式拒绝，不做左补零 |
| 与品种无关（全局）（`all`） | 日线里含「盘后固定价格交易」（15:05–15:30）的量 | — | ❌ | 盘后成交**存在但不在日线里**：逐笔能看到 InOutFlag=5 的 15:05–15:29:50 成交（30–1588 手 = 日线量的 0.013%–0.119%，逐票都在日线之外）；而日线 vol == 分时 240 点合计（\|Δ\|≤0.48 手），Bar.raw 12 个键没有一个能区分盘后成交 | §12.3 E1/E4/E8 / probe/Q3Q6 | 只能 get_ticks 自聚合（≈100 s/只）；注意 status=final 只表示「日线口径定稿」 |
| 与品种无关（全局）（`all`） | 并发取数（靠多主机提速） | — | ❌ | **实测不可行，特性已删除**：concurrency=4（4 条 lane 各钉一台主机）头 5 秒就被重置 3 条连接，limited_events 0→3、current 2.2→12.0、175 s 内 0 次成功取数 —— 配额按**来源 IP**，换服务器不换配额。2026-10 起 `Client` 不再接受 `concurrency` 参数（传了直接 `TypeError`） | probe/phase5/CONCLUSION.md（原始输出 probe_phase5.out.txt） | 要更快只能减少调用次数，或跨进程/跨机分摊（后者需自行验证配额口径） |
<!-- END: capabilities -->

`❌` **不等于「服务端不支持」**：港股**有**报价通道（`PBCombHQ` + `setcode=31`，实测腾讯 385.200）只是本库没实现；期货的**合约行情**则是**代码不在数据源证券表里**（但期货的合约清单/会员持仓榜单/主力连续快照在数据中心侧可用，见第 17 条）。两种「拿不到」的处置完全不同 —— 详见「已知限制」第 10、11 条。

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
| `products(domain=None)` | `list[ProductInfo]` | 424 个数据产品，可按域过滤 |
| `domains()` | `list[str]` | 17 个域 |
| `capabilities(*, instrument=None, available_only=False)` | `Table` | **能力边界表**：品种 × 接口 × 有无 × 依据 × 绕法；静态注册表，**一个请求都不发**；筛完为空抛 `EmptyResult` |
| `get_bars(symbol, period, count, adjust)` | `list[Bar]` | K 线；`count > 416` 自动翻页 |
| `get_breadth(code, count, adjust)` | `Table` | 非价格序列（涨跌家数/停板家数/新低点位），命名字段；未登记代码抛 `NotSupportedError`；只支持日线 |
| `get_market_breadth(view="summary")` | `Table` | **实时**涨跌家数（`summary` 五列：涨/跌/平/涨停/跌停；`minute` 逐分钟）；与 `get_market_stat("updown")` 的**历史 30 日**不是一回事，`meta["as_of"]` 恒 `None` 并带说明 |
| `get_intraday_samples(symbols, count=None)` | `Table` | 多代码**当日分时点位**（`points` 为 list，逐点非抽样）；⚠️ 仅指数 `999999` 实证，个股未复核（实验性）；`count` 只做本地截尾 |
| `get_minutes(symbol)` | `list[MinutePoint]` | 当日分时 |
| `get_ticks(symbol, count=200)` | `list[Tick]` | 逐笔成交，自动翻页，按时间升序 |
| `get_indicators(symbol, indicators, period, count)` | `Table` | 由 K 线派生，行一一对应 |
| `get_price_limits(symbol, prev_close, is_st)` | `tuple` | `(涨停价, 跌停价)` |
| `get_market_page(market, offset, page_size, cols)` | `list[Quote]` | 行情一页 |
| `get_universe(market, max_pages, page_size)` | `list[Quote]` | 翻页枚举 + 去重；`meta` 带 `truncated`/`pages`/`rows`/`estimated_cost_seconds`（**达上限就是 `truncated=True`**，不再把 1000 只当全量 —— 要全量请用 `iter_universe()`） |
| `iter_universe(market=6, page_size=80, max_pages=None)` | 生成器 | **逐页取全**，`max_pages=None` = 翻到底；可边取边停（全量约 5577 只 / ≈158 秒） |
| `get_quotes(symbols, batch=100, on_missing="raise")` | `list[Quote]` | 批量报价；`batch` 默认 100、上限 447（实测一次能带走 447 个码）；缺码**成批**补发。`on_missing`：`raise`（默认，抛 `EmptyResult` 点名）/ `skip`（不抛，缺失清单进 `meta["missing"]`）/ `report`（同 skip + 警告一次） |
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
| `get(name, allow_empty=False, *, mapped=False, **params)` | `Dataset` | 数据中心产品（表格在 `.table`）；`mapped=True` 时对**已登记列义**的产品返回标准字段 + 逐行 `raw`，未登记的产品抛 `ValueError`（默认 False，原始列契约不变） |
| `call(cmd, ctl, params)` | `Table` | 直调底层命令，参数给错抛 `ParamError` |
| `timeliness(kind, **kw)` | `dict` | **取数前问时效**：四态 + 服务器时刻 + 收盘截止（15:35） |
| `rate_stats()` | `dict` | 当前节流间隔、被限流次数、冷却主机数 |

```python
# 缓存默认关闭（保持"每次都真的问服务端"的可预测语义）
from tdxdata import TTLCache
with tdxdata.Client(cache=TTLCache(ttl=300)) as c:
    c.get("holiday")     # 第二次命中缓存，不占用限流配额

# 2026-10-29 起也可以交给库：按**每条命令的实测新鲜度**逐条定 TTL
with tdxdata.Client(cache="auto") as c:
    c.get("hg_gdp")      # 宏观：滞后 293 天 → 缓存 30 天
    c.get("zdt_zdfx")    # 当日涨跌停：滞后 0 天 → **不缓存**（ttl=0）
tdxdata.ttl_for_cmd("hg_gdp")      # 2592000（秒），可自查某个产品的缓存时长
```

## 直接调命令目录里的任意命令（`Client.call_cmd`）

产品层 `get()` 只有**424 条**封装好的产品，而命令目录里有**732 条** —— 剩下那 300 多条以前只能手抄 `ctl` 加手拼参数。`call_cmd()` 把这两件事交给目录（`tdxdata.ctlmap`）查表：

```python
import tdxdata

with tdxdata.Client() as c:
    t = c.call_cmd("DataAggregation.queryCyl")            # ctl / 请求体形态 / 参数全自动
    t = c.call_cmd("CWServ.ph_tdxdatacenter_tjcb_gdqk",   # 占位符用 code= / date= 填
                   code="600519", date="2026-10-30")
```

* `code=` / `date=` / `extra=` 只填**目录模板里的占位符** —— 只对模板里**真有**占位符的命令生效（如示例二那条；`CWServ.ph_agf10_gsjj` 的模板是 `["00201","600303","0"]`，对它传 `code=` 是空操作）；`params=` 一给就完全用它（模板不参与）。⚠️ 少数命令（`catalog` 里 25 条）的参数是**抓包样本值**：里面的标的代码/日期是抓包当时的现场值，不是通用默认值 —— 要换标的就显式传 `params=`。
* `{{searchText}}` 是 App 端 JS 模板语法，本库不填 —— 自己给 `extra` 或 `params`。
* 命令不在目录：抛 `NotSupportedError`（消息里列同前缀的相近命令，不做编辑距离）。
* `dead` 档（本地实测回 104）：**发请求之前**就抛 `NotSupportedError`，一个字节都不发；名单是 2026-10-31 的负证据快照，确实要试一次就传 `allow_dead=True`。
* 通道通但 0 行：抛 `EmptyResult`（`probe_ok` 档的消息会写明"接口可达、不是命令坏"）；只要空表就传 `allow_empty=True`。

命令清单：`tdxdata.ctlmap.names()`；某条命令的三档状态：`tdxdata.ctlmap.tier_for(cmd)`（`verified` / `probe_ok` / `dead` / `None`）。

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

瓶颈在**服务端限流**，不在库：**库早期**实测每 0.2 秒连发 8 次就会被踢，间隔 2.2 秒可长期稳定。

> ⚠️ **这条结论的边界（2026-10-29 复核）**：用 31 份手机端抓包回放时，数据中心表出现过**单秒 22 个不同查询**、2316 个回包**零限流错误**，与上面的结论存在张力。
> 差异可能来自「同参数重复」与「不同参数并发」的区别，也可能来自服务端策略变化，**尚未做对照实验**。所以实验完成前 `BASE_GAP` 保持 2.2 秒不动：宁可慢，不拿用户 IP 去赌。

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

## 接口维护总册

维护与扩展命令前**先读** [接口维护总册.md](接口维护总册.md)——它汇总了 7615 协议基础（帧格式 / ctl 通道族语义 / 服务器列表）、457 条已注册接口总表、PB 命令实测详解（列名/参数位/注意）、raw_args 实抓参数体、待登记清单（13 条 catalog-ready + 3 条 JJSServ 新命令 + 74 条未登记），以及 60 条页面声明 params 模板。要给 `products.json` 新增命令或调试现有命令的 ctl / 参数，直接按总册对应章节操作即可。

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

**共 27 条。** 每一条都按同一结构写：**现象**（你会看到什么）→ **为什么**（根因一句）→
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

**10. 期货 / 期权 / 国债期货 / 外汇的合约行情：不在数据源证券表内（专题数据另说，见第 17 条）**
- 现象：`PBFXT`/`PBZST`/`PBCombHQ`/`PBMultiHQ` × 6 个市场码（7/8/28/29/30/31）× 30 个合约代码
  = **全部为空**，且 `ERR_-1000` 出现 **0 次**；`get_bars("IF00")` 在 `symbols.parse` 层就报错
  （一个字节都没发出）。
- 为什么：**这些代码不在证券表里**。⚠️ 注意这**不是**"市场码被拒" —— `field3` 对已知代码是
  **宽松**的：把铁定存在的 `600519` 塞进 `field3=99`（不存在的市场码）服务端照样回同一份 K 线
  （`Close=1355.900024` 逐位相同）。所以别再归因到市场码上。
- 怎么办：**行情类（K 线/分时/五档）无通道**，别再去试 7/8/28/29/30/31，那只会白烧请求还把主机打进冷却。
  但**「期货完全没有」是错的**：合约清单 / 会员持仓榜单 / 主力连续最新价快照在 7615 数据中心侧实测可用
  （`qhlhb_sy` / `qhlhb_jcgc` / `tdxrb_qh` / `qh_hqpd_qhhy`），详见第 17 条。
- 补充（外汇现货另有线索，但只是笔记证据）：EX **扩展市场通道（端口 7727，另一套服务器）**的报价只**见于笔记/在线探针**
  （《汇率抓包新增接口.md》§十二:286-307，2026-10-22，快照不是承诺）；**31 份 pcap 里 7709/7727/7708 各 0 帧、
  `USDCNY` 字符串 0 次命中**，所以 7727 侧**不能当抓包实测**。本库未接入。
  另注意 `ph_cfg_fx_sczb`（产品 `sczb`）/ `ph_cfg_fx_jjhqpd`（产品 `jjhqpd`）**不是外汇**：
  `fx` 是「分析」，前者是 15 项 A 股市场指标、后者是 10 只境内 ETF 盘点，零汇率字段。
- 依据：`§12.1` / `reports/refactor/probe/Q1Q2/` / `reports/eval3/findings/M4-fx-futures.md` §3-4

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
  （一次 ≈158.6 s，很贵，见第 20 条）。
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

**17. 期货：合约 K 线/分时/五档无通道，但合约清单 / 会员持仓榜单 / 主力连续快照在数据中心侧可用**
- 现象：`c.products()` 里能看到 3 个 `qh_` 前缀产品（`qh_cdzl` / `qh_bkzs` / `qh_qhhy`），很容易被
  当成"期货行情终于有了"；把 31 份抓包逐条解码后真相是 —— **这三个都不是 K 线行情**，而**真正的
  期货专题数据在另一批 `ph_tdxdatacenter_*` 命令里**（下面点名，都是真实回包）。
- 为什么：逐个实测形状（`reports/eval3/findings/M4-fx-futures.md` §2.3–2.5）——
  `qh_bkzs`（`CWServ.ph_pcwebcall_qh_hqpd_bkzs`）是**板块指数排行**：14 行×5 列，`code` 是
  `T004`…`T030` 这类占位码（无名称映射）、`now` 113.9~363.1 是**指数基点不是合约价**；
  `qh_qhhy`（`CWServ.ph_pcwebcall_qh_hqpd_qhhy`）**不是行业排行**，是**期货品种主力连续合约快照**：
  10 行×5 列，`code`=`FUL9`/`SCL9`/`LL9` 这类「品种码+L9」，`now` 721.3~9378.0 是**真实合约价格**
  （market 28/29/30 经 `tdxrb_qh`、`qhlhb_sy` 交叉核对落在郑商所/大商所/上期所），
  **是合约级价格，但只有「代码/市场/排序值/名次/最新价」5 个字段，没有 OHLC/成交量/持仓量**；
  `qh_cdzl`（`CWServ.ph_tdxdatacenter_qh_cdzl`）**名不副实**：只回**交易日列表**
  （20 行×4 列 + 1 行×1 列，内容是 日期/常量"1"/中文星期/常量"1"），`002`/`003` 在 31 份抓包里
  **连请求都没有** —— **不能叫"持仓增量"**。
- 怎么办：要期货数据走这几条（**本库均未封装成公开方法**）：**合约清单** `qhlhb_sy` 001
  （5 列×318 行）；**会员成交量/持买/持卖/买单增减/卖单增减** `qhlhb_sy` 002（10 列×21 行，
  `p3`=0..4 五种榜单）；**机构持仓** `qhlhb_jcgc` 002（8 列×336 行）；**期货合约榜** `tdxrb_qh`
  （12 列×100 行，含 `MA2701`/`FU2701` 与市场码 28/29/30）；**主力连续最新价** `qh_hqpd_qhhy`。
  **合约 K 线/分时/五档仍然无通道**，出路是 PC 侧 EX 扩展通道（端口 7727，
  `notes/archive/接口文档/行情接口.md:563`）—— **本库未接入、未实测**；该通道上外汇现货的报价只
  **见于笔记/在线探针**（USDCNY 6.6746 / USDCNH 6.67267 / CNHHKD 1.17471 / CNYHKD 1.17377，
  2026-10-22 快照，**快照不是承诺**）。7727 未接入属**通道攻关决策**，**不默认施工**。
- 依据：`reports/eval3/findings/M4-fx-futures.md` §2.3–2.5、§3–4（31 份 pcap 端口直方图
  7709/7727/7708 = **0 帧**、`USDCNY` 字符串 **0 次**）/ `notes/archive/接口文档/行情接口.md:563`

### 三、慢、限流与分页

**18. 被限流是常态，库只降低概率、不能消除**
- 现象：偶尔抛 `RateLimited`；`rate_stats()["limited_events"]` 会累加；被踢后进 30 s 冷却，
  一次限流事件平均多花 **≈28.15 s**。
- 为什么：服务端按**来源 IP** 计配额（见第 19 条），本库的 2.2 s 间隔只是把撞枪概率压低。
- 怎么办：别在循环里高频重试；先看 `rate_stats()` 的 `current`/`cooling_hosts`；
  批量优先用 `get_quotes` / `iter_universe`，别自己 for 循环逐只单发。
- 依据：`§12.6` / `probe/phase5/`

**19. 并发按「来源 IP」配额，实测不可行（`concurrency` 默认 1）**
- 现象：`Client(concurrency=4)` 跑 `get_market_stat("industry")`，**头 5 秒**就
  `limited_events` 0→3、`current` 2.2→12.0、3 台进冷却，之后 **175 s 内 0 次成功取数**；
  同一台机器串行只要 **68.64 s** 且零限流。
- 为什么：配额按**来源 IP**，不按"IP × 服务器"。机制本身是好的（`hosts_tracked` 0→4，
  4 条 lane 各钉一台主机），但 **桶独立 ≠ 配额独立** —— 换 4 台服务器没换来 4 份配额。
- 怎么办：保持默认 `concurrency=1`。要更快只有两条路：**减少调用次数**，或**跨进程/跨机分摊**。
  这是实测结论，不是"优化还没做"。
- 依据：`reports/refactor/probe/phase5/CONCLUSION.md`（原始输出 `probe_phase5.out.txt`）

**20. `get_market_stat("industry")` 慢：31 次调用 × 2.2 s ≈ 68 s**
- 现象：一次约 **68 s**（实测 68.20 s / 68.64 s 两轮），439 行；全 A 枚举（`market=6`，5577 只）
  约 **158.6 s**。
- 为什么：1 次目录 + 30 个一级行业各一次明细，串行过同一条 2.2 s 节流闸门；
  重试贡献 **0 s**（不是"重试拖慢"）。
- 怎么办：结果进 `Client(cache=TTLCache(ttl=...))`；只要几个行业就先 `get_boards("industry")`
  再挑着取；别在循环里反复调。
- 依据：`§12.6` / `probe/phase5/`

**21. 分页枚举保证不重复，不保证不遗漏**
- 现象：跨两次枚举拼起来时，可能出现"上一页有、下一页没有"的票。
- 为什么：服务端按涨幅排序，盘中榜单会漂移；`offset` 分页对着一个会动的序列翻。
- 怎么办：跨次拼接按代码**去重**；要"完整集合"请在收盘后取，或用 `iter_universe` 一次翻到底；
  `meta["truncated"]` 一定看（`True` 表示没取全，不是全量）。
- 依据：`§6.1` / `_core.paginate`

### 四、方法学边界（门禁覆盖不到的地方）

**22. 15 份黄金样本是阶段 1 之前录的：容器级 meta 不受它们约束**
- 现象：这 15 份没有容器 `meta`、也没有 `meta_sha256`。
- 为什么：按纪律**不重录**（重录会把回归一起盖掉）。它们只比对**数据**，不比对容器级
  `clock_note`/`provisional`/`as_of`。
- 怎么办：那部分由 `tests/test_series_containers.py` / `tests/test_timeliness.py` 守 ——
  是"换了守卫"，不是"没人管"。
- 依据：复核 02 `§6` / `§13.3`

**23. 多 blob 分帧路径没有被黄金样本覆盖**
- 现象：60+ 帧里 `n_blobs` **全为 1**，多帧分支在回放里从没走过。
- 为什么：录制窗口内没出现过跨帧响应；按纪律**不造数据**去凑覆盖。
- 怎么办：这是**已知缺口，不是"已验证"**。遇到疑似截断/串帧请附原始 frame 报 issue。
- 依据：`tests/fixtures/golden/README.md`「已知缺口」

**24. `elapsed` 的值不参与黄金比对**
- 现象：任何样本差异里都不会出现 `elapsed`。
- 为什么：它是性能量、不携带数据信号；放进比对会让门禁随机器快慢抖动。
- 怎么办：所以"`elapsed` 永久恒 0"这类退化**原理上抓不到**，只能靠计时类测试兜
  （`test_timeliness.py` 等）。别指望黄金样本发现它坏了。
- 依据：`§13.3` 第 2 条（`INFO_ONLY_META_CARRIERS`）

**25. `get_samples` 的浮点列序未锚定、个数会变**
- 现象：`f0`、`f1`… 的个数盘中递增，`f*` 的语义只锚定了 `f0`（开盘）与 `pre_close`。
- 为什么：服务端按"当前时刻"下发列，没有稳定 schema。
- 怎么办：**别写死个数，别把 `f*` 当 high/low/volume**；只信任 `f0` 与 `pre_close`。
- 依据：`§13.1` / README 旧第 5 条

**26. 块数据（`get_block_data`）的块名由服务端下发**
- 现象：块名不是通用常量，`get_block_data("MStock_XXX")` 的可用性随服务端配置变。
- 为什么：块名是服务端侧的台账键，本库只是转发。
- 怎么办：目前只验证了少数几个（如 `MStock_ZLJX_ADDE_R`）；用之前先 `get_block_rows()` 探一下。
- 依据：`§13.1` / `sections/` 模块文档

**27. 资金流净值口径与部分第三方软件不同**
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

## 附录：由代码生成的表（勿手改）

> 本附录的**每一张表**都由 `tdxdata/_docgen.py` 里的纯函数生成，事实源是
> `enums` / `models` / `errors` / `limits` / `_core` / `domains` / `symbols` / `timeliness` /
> `capabilities` / `indicator` / `invariants` / `_provenance` / `client` 的模块级常量，
> 以及 `data/products.json` —— **没有一处是手抄的**。
> `tests/test_readme_drift.py` 逐字比对内容，`tests/test_docgen.py` 守「两次调用同一结果 /
> 行尾无 `\r` / 每行列数一致」并实算受守护行数占比。所以**手改这里会立刻红**：
> 要改表请先改代码里的注册表，再重跑生成。

### A1 枚举速查（`tdxdata/enums.py`）

市场码就是 `PBFXT` / `PBZST` 的 `field3`（与 7615 的 `setcode` 一致）：

<!-- BEGIN: enum_market（由 tdxdata/_docgen.py:market_enum_table() 生成，勿手改） -->
| 成员 | 值 | 市场 | 对应协议位 |
|---|---|---|---|
| `SZ` | 0 | 深圳 | `PBFXT` / `PBZST` 的 `field3` |
| `SH` | 1 | 上海 | `PBFXT` / `PBZST` 的 `field3` |
| `BJ` | 2 | 北京 | `PBFXT` / `PBZST` 的 `field3` |
<!-- END: enum_market -->

`KlinePeriod` 的「请求名」取 `client._PERIOD_STR`（有映射就是发得出去），
「无数据」取 `client._PERIOD_DEAD`（实测 0 行，库**显式拒绝**而不是返回空表）：

<!-- BEGIN: enum_period（由 tdxdata/_docgen.py:period_enum_table() 生成，勿手改） -->
| 成员 | 值 | 请求名 | 本库可用性 |
|---|---|---|---|
| `MIN_5` | 0 | `5m` | ✅ `5m` |
| `MIN_15` | 1 | `15m` | ✅ `15m` |
| `MIN_30` | 2 | `30m` | ✅ `30m` |
| `MIN_60` | 3 | `60m` | ✅ `60m` |
| `DAY` | 4 | `day` | ✅ `day` |
| `WEEK` | 5 | `week` | ✅ `week` |
| `MONTH` | 6 | `month` | ✅ `month` |
| `MIN_1` | 7 | `1m` | ✅ `1m` |
| `MIN_3` | 8 | — | ❌ 无数据（`_PERIOD_DEAD`，抛 `NotSupportedError`） |
| `YEAR` | 9 | — | ❌ 无数据（`_PERIOD_DEAD`，抛 `NotSupportedError`） |
| `SEASON` | 10 | `season` | ✅ `season` |
| `YEAR_ALT` | 11 | — | ❌ 无数据（`_PERIOD_DEAD`，抛 `NotSupportedError`） |
<!-- END: enum_period -->

时效四态（`Status`，字符串枚举，可直接进 JSON / 日志）：

<!-- BEGIN: enum_status（由 tdxdata/_docgen.py:status_enum_table() 生成，勿手改） -->
| 成员 | 值 | 含义 |
|---|---|---|
| `FINAL` | `'final'` | 最终值：这个口径已经定稿 |
| `PROVISIONAL` | `'provisional'` | 盘中/当根未走完，会变 |
| `PENDING` | `'pending'` | 今天的还没生成（如盘中查涨停清单） |
| `DELAYED` | `'delayed'` | 数据本身滞后（两融 T+1、行业统计 T-1） |
<!-- END: enum_status -->

### A2 涨跌停价与最小变动单位（`tdxdata/limits.py`）

纯本地计算，`Decimal` + `ROUND_HALF_UP` 取整到分（**不用内置 `round()`**）：

<!-- BEGIN: price_limit_boards（由 tdxdata/_docgen.py:price_limit_table() 生成，勿手改） -->
| 板块 | 代码前缀 | 涨跌幅 | ST 涨跌幅 | 最小变动单位 |
|---|---|---|---|---|
| 主板（沪 60 / 深 00） | 60/00 | ±10% | ±5% | 0.01 元（2 位小数） |
| 创业板（30） | 30 | ±20% | 同左（ST 只在主板生效） | 0.01 元（2 位小数） |
| 科创板（68） | 68 | ±20% | 同左（ST 只在主板生效） | 0.01 元（2 位小数） |
| 北交所（43/83/87/88/92） | 43/83/87/88/92 | ±30% | 同左（ST 只在主板生效） | 0.01 元（2 位小数） |
| 基金 / ETF | 15/16/50/51/52/56/58 | ±10% | 同左（ST 只在主板生效） | 0.001 元（3 位小数） |
| 可转债 / 可交换债 | 110/111/113/118/120/123/127/128/132 | ±20% | 同左（ST 只在主板生效） | 0.001 元（3 位小数） |
| B 股（沪 900 / 深 200） | 900/200 | ±10% | 同左（ST 只在主板生效） | 0.001 元（3 位小数） |
<!-- END: price_limit_boards -->

### A3 `Client` 的构造参数

参数表直接来自 `inspect.signature(Client.__init__)`，改签名这里就跟着变：

<!-- BEGIN: client_params（由 tdxdata/_docgen.py:client_params_table() 生成，勿手改） -->
| 参数 | 类型 | 默认值 | 传法 |
|---|---|---|---|
| `host` | `str \| None` | `None` | 位置或关键字 |
| `timeout` | `float` | `15.0` | 位置或关键字 |
| `gap` | `float` | `2.2` | 位置或关键字 |
| `cache` | — | `None` | 位置或关键字 |
| `clock_probe` | `bool` | `True` | 关键字专用 |
| `strict_symbols` | `bool` | `False` | 关键字专用 |
<!-- END: client_params -->

### A4 异常层次（`tdxdata/errors.py`）

设计原则：**让调用方能区分「没数据」和「取不到」** —— 混成一个 `None` 或空列表，
上层就会误判成「今天没数据」。基类一路 `TdxError`，`__all__` 的顺序即下表顺序：

<!-- BEGIN: error_types（由 tdxdata/_docgen.py:error_types_table() 生成，勿手改） -->
| 异常 | 直系基类 | 什么时候抛 |
|---|---|---|
| `TdxError` | `Exception` | 本库所有异常的基类。 |
| `ConnectionFailed` | `TdxError` | 连不上（全部服务器都失败）。 |
| `Timeout` | `ConnectionFailed` | 读写超时。 |
| `ProtocolError` | `TdxError` | 帧头非法、zlib 解压失败、响应截断等 —— 协议层异常，通常需要抓包排查。 |
| `ParamError` | `TdxError` | 服务端返回 -1002：参数不对。 |
| `ServerError` | `TdxError` | 服务端返回 -1005 / -1009：命令执行失败。 |
| `NotSupportedError` | `TdxError` | 这条命令/能力**根本不可用**（-1000 协议不存在，或本库未实现）。 |
| `RateLimited` | `ConnectionFailed` | 被限流/被踢（10053/10054）。调用方应退避后换服务器重试。 |
| `EmptyResult` | `TdxError` | 命令可用，但**这一次**没数据（`EMPTY_RS 0×N` / `EMPTY 0×0`）。 |
| `NoData` | `EmptyResult` | `EmptyResult` 的别名，语义更直白。 |
| `OutOfScope` | `TdxError` | **这个品种不在本数据源的覆盖范围**（不是"接口做不了这件事"）。 |
| `AmbiguousSymbol` | `TdxError` | **两义代码**，且调用方要求严格（`Client(strict_symbols=True)`）。 |
<!-- END: error_types -->

### A5 批量取数的 `on_missing` 三态

<!-- BEGIN: on_missing_modes（由 tdxdata/_docgen.py:on_missing_table() 生成，勿手改） -->
| 取值 | 缺码时怎么处理 |
|---|---|
| `raise` | 有缺失就抛 `EmptyResult`，点名缺了哪些代码（默认） |
| `skip` | 不抛，缺失清单进 `provenance.extra["missing"]` |
| `report` | 同 `skip`，另外 `warnings.warn` 一次 |
<!-- END: on_missing_modes -->

### A6 数据模型字段（`tdxdata/models.py`）

8 个模型类的全部字段（字段名 / 类型 / 默认值取 `dataclasses.fields`，
语义注释以源码为准）。

⚠️ `Quote.bid` / `Quote.ask` 这两个字段**恒为空列表** —— 协议层没有五档（`PBCombHQ` 25 列里也没有 bid/ask），
字段留着是为了兼容；别把「有这个列表」读成「取得到盘口」。

<!-- BEGIN: model_fields（由 tdxdata/_docgen.py:model_fields_table() 生成，勿手改） -->
| 模型 | 字段 | 类型 | 默认值 |
|---|---|---|---|
| `Bar` | `market` | int | **必填** |
| `Bar` | `code` | str | **必填** |
| `Bar` | `date` | str | **必填** |
| `Bar` | `open` | float | **必填** |
| `Bar` | `high` | float | **必填** |
| `Bar` | `low` | float | **必填** |
| `Bar` | `close` | float | **必填** |
| `Bar` | `vol` | float \| None | `None` |
| `Bar` | `amount` | float \| None | `None` |
| `Bar` | `period` | str | `'day'` |
| `Bar` | `time` | str | `''` |
| `Bar` | `raw` | dict[str, Any] | 工厂 `dict()` |
| `Bar` | `adjust` | str | `'NONE'` |
| `Bar` | `provisional` | bool | `False` |
| `MinutePoint` | `code` | str | **必填** |
| `MinutePoint` | `time` | str | **必填** |
| `MinutePoint` | `price` | float | **必填** |
| `MinutePoint` | `avg_price` | float | **必填** |
| `MinutePoint` | `vol` | float | `0.0` |
| `MinutePoint` | `market` | int | `0` |
| `MinutePoint` | `raw` | dict[str, Any] | 工厂 `dict()` |
| `Quote` | `market` | int | **必填** |
| `Quote` | `code` | str | **必填** |
| `Quote` | `price` | float | **必填** |
| `Quote` | `pre_close` | float | `0.0` |
| `Quote` | `open` | float | `0.0` |
| `Quote` | `high` | float | `0.0` |
| `Quote` | `low` | float | `0.0` |
| `Quote` | `vol` | float \| None | `None` |
| `Quote` | `amount` | float \| None | `None` |
| `Quote` | `bid` | list[tuple[float, float]] | 工厂 `list()` |
| `Quote` | `ask` | list[tuple[float, float]] | 工厂 `list()` |
| `Quote` | `server_time` | str | `''` |
| `Quote` | `name` | str | `''` |
| `Quote` | `raw` | dict[str, Any] | 工厂 `dict()` |
| `Tick` | `market` | int | **必填** |
| `Tick` | `code` | str | **必填** |
| `Tick` | `time` | str \| None | `None` |
| `Tick` | `price` | float \| None | `None` |
| `Tick` | `vol` | float \| None | `None` |
| `Tick` | `trade_count` | int \| None | `None` |
| `Tick` | `bs_flag` | int \| None | `None` |
| `Tick` | `raw` | dict[str, Any] | 工厂 `dict()` |
| `SecurityInfo` | `market` | int | **必填** |
| `SecurityInfo` | `code` | str | **必填** |
| `SecurityInfo` | `name` | str | `''` |
| `SecurityInfo` | `volunit` | float | `0.0` |
| `SecurityInfo` | `decimal_point` | int | `2` |
| `SecurityInfo` | `pre_close` | float | `0.0` |
| `Table` | `cols` | list[str] | **必填** |
| `Table` | `rows` | list[dict[str, Any]] | **必填** |
| `Table` | `cmd` | str | `''` |
| `Table` | `domain` | str | `''` |
| `Table` | `provenance` | Provenance \| None | `None` |
| `Table` | `ragged` | tuple[int, int] \| None | `None` |
| `Dataset` | `name` | str | **必填** |
| `Dataset` | `table` | Table | **必填** |
| `Dataset` | `params` | Any | `None` |
| `ProductInfo` | `name` | str | **必填** |
| `ProductInfo` | `cmd` | str | **必填** |
| `ProductInfo` | `ctl` | int | **必填** |
| `ProductInfo` | `domain` | str | `''` |
| `ProductInfo` | `params` | Any | `None` |
<!-- END: model_fields -->

### A7 技术指标（`tdxdata/indicator.py`）

「默认参数」列取 `client._IND_DEFAULT_PARAMS`（`get_indicators` 真正会算的那几组），
没有登记的函数要自己传 `n`：

<!-- BEGIN: indicators（由 tdxdata/_docgen.py:indicator_table() 生成，勿手改） -->
| 指标 | 签名 | `get_indicators` 默认参数 | 口径 |
|---|---|---|---|
| `beta` | `indicator.beta(bars, market, n=20)` | —（`get_indicators` 未登记） | 相对基准的滚动 OLS 斜率 `BETA(close, 基准close, n)`（无量纲）。 |
| `boll` | `indicator.boll(bars, n=20, k=2)` | 无（固定参数） | 布林带 `BOLL(close, n, k)`，返回 `(upper, mid, lower)` 三个**等长**序列。 |
| `ema` | `indicator.ema(bars, n)` | n=12/26 | 指数移动平均 `EMA(close, n)`。 |
| `hv` | `indicator.hv(bars, n=20)` | n=20 | 滚动**年化**历史波动率 `HV(close, n)`（**百分数**，`18.5` 表示 18.5%）。 |
| `kdj` | `indicator.kdj(bars, n=9, k=3, d=3)` | 无（固定参数） | KDJ（通达信口径），返回 `(k_line, d_line, j_line)` 三个**等长**序列。 |
| `ma` | `indicator.ma(bars, n)` | n=5/10/20 | 简单移动平均 `MA(close, n)`。 |
| `macd` | `indicator.macd(bars, fast=12, slow=26, signal=9)` | 无（固定参数） | MACD（通达信口径），返回 `(dif, dea, hist)` 三个**等长**序列。 |
| `max_drawdown` | `indicator.max_drawdown(bars, n=20)` | n=20 | 滚动窗口最大回撤 `MDD(close, n)`（**百分数**，恒 `<= 0`）。 |
| `momentum` | `indicator.momentum(bars, n=20)` | n=20 | 动量 `MOM(close, n)`（**百分数**）：`(close[i] / close[i-n] - 1) * 100`。 |
| `rsi` | `indicator.rsi(bars, n=14)` | n=14 | 相对强弱指标 RSI（**Wilder 递推**口径）。 |
| `sharpe` | `indicator.sharpe(bars, n=20, rf=0.0)` | n=20 | 滚动**年化**夏普比率 `SHARPE(close, n, rf)`（无量纲）。 |
<!-- END: indicators -->

### A8 恒等式闸门（`tdxdata/invariants.py`）

`invariants.validate(container)` 显式调用才跑（闸门不自动开，性能取舍）：

<!-- BEGIN: invariants（由 tdxdata/_docgen.py:invariant_table() 生成，勿手改） -->
| 检查器 | 断言什么 |
|---|---|
| `check_amount_vol_close()` | `amount ≈ vol×100×close`（相对误差 ≤5%，与 test_hq_extra.py:235 同宽）。 |
| `check_breadth_total()` | `up + down + flat ≡ total`（**当日**证券总数；5572 不是跨日真理）。 |
| `check_minute_points()` | `n_points(field7) ≡ len(points)`（test_pb_sample.py 实证 25/25 帧恒等）。 |
| `check_minute_volume_sum()` | 240 点 `vol` 合计 ≈ 当日 `Bar.vol`；`amount` 合计 ≈ 当日 `Bar.amount`。 |
| `check_ohlc()` | `low ≤ open,close ≤ high`（`high/low` 也互为包络：low ≤ high）。 |
| `check_pre_close()` | `pre_close` ↔ 同码同刻的另一通道昨收一致（相对误差 ≤0.1%）。 |
<!-- END: invariants -->

### A9 代码段 → 市场（`tdxdata/symbols.py`）

规则：**3 位前缀优先、1 位兜底**；数据来源是 `PBMultiHQ` 的 `Setcode` 真值样本
（`tests/fixtures/市场码真值样本.json`）。新起品种请**只加表项，不要改逻辑**：

<!-- BEGIN: symbol_prefixes（由 tdxdata/_docgen.py:symbol_prefix_table() 生成，勿手改） -->
| 代码段 | 市场 | 市场码 | 规则 |
|---|---|---|---|
| `110xxx` | sh | 1 | 3 位前缀（**优先**） |
| `111xxx` | sh | 1 | 3 位前缀（**优先**） |
| `113xxx` | sh | 1 | 3 位前缀（**优先**） |
| `118xxx` | sh | 1 | 3 位前缀（**优先**） |
| `123xxx` | sz | 0 | 3 位前缀（**优先**） |
| `127xxx` | sz | 0 | 3 位前缀（**优先**） |
| `131xxx` | sz | 0 | 3 位前缀（**优先**） |
| `200xxx` | sz | 0 | 3 位前缀（**优先**） |
| `201xxx` | sz | 0 | 3 位前缀（**优先**） |
| `204xxx` | sh | 1 | 3 位前缀（**优先**） |
| `241xxx` | sh | 1 | 3 位前缀（**优先**） |
| `242xxx` | sh | 1 | 3 位前缀（**优先**） |
| `243xxx` | sh | 1 | 3 位前缀（**优先**） |
| `399xxx` | sz | 0 | 3 位前缀（**优先**） |
| `430xxx` | bj | 2 | 3 位前缀（**优先**） |
| `524xxx` | sz | 0 | 3 位前缀（**优先**） |
| `820xxx` | bj | 2 | 3 位前缀（**优先**） |
| `830xxx` | bj | 2 | 3 位前缀（**优先**） |
| `870xxx` | bj | 2 | 3 位前缀（**优先**） |
| `880xxx` | sh | 1 | 3 位前缀（**优先**） |
| `881xxx` | sh | 1 | 3 位前缀（**优先**） |
| `900xxx` | sh | 1 | 3 位前缀（**优先**） |
| `920xxx` | bj | 2 | 3 位前缀（**优先**） |
| `999xxx` | sh | 1 | 3 位前缀（**优先**） |
| `0xxxxx` | sz | 0 | 1 位兜底（3 位没命中时） |
| `1xxxxx` | sz | 0 | 1 位兜底（3 位没命中时） |
| `2xxxxx` | sz | 0 | 1 位兜底（3 位没命中时） |
| `3xxxxx` | sz | 0 | 1 位兜底（3 位没命中时） |
| `4xxxxx` | bj | 2 | 1 位兜底（3 位没命中时） |
| `5xxxxx` | sh | 1 | 1 位兜底（3 位没命中时） |
| `6xxxxx` | sh | 1 | 1 位兜底（3 位没命中时） |
| `8xxxxx` | bj | 2 | 1 位兜底（3 位没命中时） |
| `9xxxxx` | sz | 0 | 1 位兜底（3 位没命中时） |
<!-- END: symbol_prefixes -->

### A10 两义代码（`tdxdata/symbols.py` 的 `AMBIGUOUS`）

同一个写法在不同市场里指向不同标的，库会 `warnings.warn` 提醒（`strict_symbols=True` 才抛）：

<!-- BEGIN: ambiguous_symbols（由 tdxdata/_docgen.py:ambiguous_symbol_table() 生成，勿手改） -->
| 写法 | 它到底是谁 / 该怎么写 |
|---|---|
| `000001` | 平安银行（深市 000001）。⚠️ **上证指数是 999999，不是 000001** —— 写成 000001 拿到的是平安银行，这是本项目最常见的口径错。真要上证指数请写 999999 或 "sh999999"。 |
| `000016` | 深市没有这个代码（深市指数是 399xxx）。⚠️ **上证50 是沪市指数**，请写 "sh000016" 或 ("sh", "000016")。 |
| `000300` | 深市没有这个代码（深市指数是 399xxx）。⚠️ **沪深300 是沪市指数**，请写 "sh000300" 或 ("sh", "000300")。 |
| `000852` | 深市没有这个代码（深市指数是 399xxx）。⚠️ **中证1000 是沪市指数**，请写 "sh000852" 或 ("sh", "000852")。 |
<!-- END: ambiguous_symbols -->

### A11 时效常量（`tdxdata/timeliness.py`）

<!-- BEGIN: time_gates（由 tdxdata/_docgen.py:timeliness_table() 生成，勿手改） -->
| 常量 / 键 | 值 | 用途 |
|---|---|---|
| `CLOSE_CUTOFF` | 15:35:00 | 日线当日定稿的判定时刻 |
| `FINAL_CUTOFF_HHMM` | 15:35 | 给人看的同一件事（`timeliness()` 的 `close_cutoff`） |
| `MIN_BAR_CUTOFF` | 15:00:00 | 分钟周期收盘边界 |
| `_PERIOD_MINUTES["1m"]` | 1 分钟 | 该周期的长度（边界检查用） |
| `_PERIOD_MINUTES["5m"]` | 5 分钟 | 该周期的长度（边界检查用） |
| `_PERIOD_MINUTES["15m"]` | 15 分钟 | 该周期的长度（边界检查用） |
| `_PERIOD_MINUTES["30m"]` | 30 分钟 | 该周期的长度（边界检查用） |
| `_PERIOD_MINUTES["60m"]` | 60 分钟 | 该周期的长度（边界检查用） |
| `_DAYLIKE` | `day` / `week` / `month` / `season` | 按 `CLOSE_CUTOFF` 判当根的周期 |
| `TRUSTED_CLOCKS` | `server` / `hybrid` | 可判定 FINAL 的时钟来源（其余一律降级） |
<!-- END: time_gates -->

### A12 来源信息字段（`tdxdata/_provenance.py`）

每个容器/表的 `provenance` 就是这一组字段 —— 「这份数据是什么时候、从哪台机器、
哪个时钟来源拿的」全部可复核：

<!-- BEGIN: provenance_fields（由 tdxdata/_docgen.py:provenance_table() 生成，勿手改） -->
| 载体 | 名称 | 类型 / 取值 |
|---|---|---|
| `Provenance` | `fetched_at` | datetime |
| `Provenance` | `clock` | ClockSource |
| `Provenance` | `host` | str \| None |
| `Provenance` | `cmd` | str |
| `Provenance` | `ctl` | int \| None |
| `Provenance` | `cached` | bool |
| `Provenance` | `elapsed` | float |
| `Provenance` | `status` | str \| None |
| `Provenance` | `as_of` | str \| None |
| `Provenance` | `data_date` | str \| None |
| `Provenance` | `notes` | tuple[str, ...] |
| `Provenance` | `extra` | dict[str, Any] |
| `ClockSource` | `SERVER` | `'server'` |
| `ClockSource` | `HYBRID` | `'hybrid'` |
| `ClockSource` | `LOCAL` | `'local'` |
<!-- END: provenance_fields -->

### A13 产品层 API（交易日历 / 证券主数据）

`Client.calendar()` / `Client.master()` 拿到的两个门面的公开方法：

<!-- BEGIN: product_layer_api（由 tdxdata/_docgen.py:product_layer_table() 生成，勿手改） -->
| 类 | 方法 | 出处 |
|---|---|---|
| `TradeCalendar` | `is_trading_day(self, d)` | `tdxdata/calendar.py` |
| `TradeCalendar` | `next(self, d)` | `tdxdata/calendar.py` |
| `TradeCalendar` | `prev(self, d)` | `tdxdata/calendar.py` |
| `TradeCalendar` | `raise_if_unavailable(self)` | `tdxdata/calendar.py` |
| `TradeCalendar` | `range(self, a, b)` | `tdxdata/calendar.py` |
| `SecMaster` | `resolve(self, code, *, missing='none')` | `tdxdata/master.py` |
| `SecMaster` | `search(self, name_like)` | `tdxdata/master.py` |
| `SecMaster` | `snapshot(self, *, refresh=False)` | `tdxdata/master.py` |
<!-- END: product_layer_api -->

### A14 品种 × 能力统计（`tdxdata/capabilities.py`）

按 `CAPABILITIES` 逐条计数（`available` 为 `True` 即「可用」，明细见上面的能力边界表）：

<!-- BEGIN: instrument_stats（由 tdxdata/_docgen.py:instrument_stats_table() 生成，勿手改） -->
| 品种码 | 品种 | 能力条目 | 可用 | 不可用 |
|---|---|---|---|---|
| `a_share` | A 股（沪/深） | 12 | 9 | 3 |
| `bj` | 北交所 | 1 | 1 | 0 |
| `index` | 指数 | 2 | 2 | 0 |
| `board` | 板块 | 2 | 2 | 0 |
| `etf` | ETF / 基金 | 1 | 1 | 0 |
| `bond` | 债券 / 配债 | 1 | 1 | 0 |
| `hk` | 港股 | 4 | 2 | 2 |
| `futures` | 商品期货 | 3 | 2 | 1 |
| `option` | 期权 | 3 | 2 | 1 |
| `cbt` | 国债期货 | 1 | 0 | 1 |
| `forex` | 外汇 | 2 | 1 | 1 |
| `us` | 美股 | 1 | 1 | 0 |
| `all` | 与品种无关（全局） | 3 | 0 | 3 |
<!-- END: instrument_stats -->

### A15 数据中心的数据域统计（`tdxdata/domains.py` + `data/products.json`）

域分类只有一处规则表（`domains._RULES`，先匹配先生效），产品数从 `products.json` 现数：

<!-- BEGIN: data_domains（由 tdxdata/_docgen.py:domain_stats_table() 生成，勿手改） -->
| 域 | 产品数 | 占比 | 域分类规则 |
|---|---|---|---|
| `行情` | 6 | 1.4% | 命中规则 3 条 |
| `财报` | 22 | 5.2% | 命中规则 10 条 |
| `F10与个股资料` | 43 | 10.1% | 命中规则 6 条 |
| `龙虎榜与两融` | 61 | 14.4% | 命中规则 5 条 |
| `沪深港通` | 23 | 5.4% | 命中规则 2 条 |
| `涨停与题材` | 30 | 7.1% | 命中规则 1 条 |
| `板块与行业` | 6 | 1.4% | 命中规则 5 条 |
| `大宗交易` | 9 | 2.1% | 命中规则 2 条 |
| `新股与再融资` | 8 | 1.9% | 命中规则 4 条 |
| `资金与筹码` | 13 | 3.1% | 命中规则 3 条 |
| `市场统计` | 6 | 1.4% | 命中规则 5 条 |
| `宏观` | 29 | 6.8% | 命中规则 3 条 |
| `基金与ETF` | 20 | 4.7% | 命中规则 3 条 |
| `资讯与AI` | 15 | 3.5% | 命中规则 6 条 |
| `股东与持股` | 11 | 2.6% | 命中规则 5 条 |
| `估值` | 20 | 4.7% | 命中规则 1 条 |
| `其他` | 102 | 24.1% | 命中规则 1 条 |
| **合计** | 424 | 100.0% | — |
<!-- END: data_domains -->

### A16 数据中心产品索引（424 个）

`c.products()` 返回的就是下面这些（按产品名排序）；「命令」列是 `c.call()` 直调时
要写的 `cmd`，「域」列是 `domains.domain_of(cmd)` 的判定结果：

<!-- BEGIN: product_index（由 tdxdata/_docgen.py:product_index_table() 生成，勿手改） -->
| 产品名 | 域 | ctl | 命令 |
|---|---|---|---|
| `ag_bsb` | 龙虎榜与两融 | 38 | `CWServ.ph_tdxdatacenter_tdxrb_ag_bsb` |
| `ag_gzb` | 龙虎榜与两融 | 38 | `CWServ.ph_tdxdatacenter_tdxrb_ag_gzb` |
| `ag_rqb` | 龙虎榜与两融 | 38 | `CWServ.ph_tdxdatacenter_tdxrb_ag_rqb` |
| `ag_rsb` | 龙虎榜与两融 | 38 | `CWServ.ph_tdxdatacenter_tdxrb_ag_rsb` |
| `ag_zrb` | 龙虎榜与两融 | 38 | `CWServ.ph_tdxdatacenter_tdxrb_ag_zrb` |
| `agf10_jj` | F10与个股资料 | 20 | `CWServ.ph_agf10_jj` |
| `agf10_zj` | F10与个股资料 | 20 | `CWServ.ph_agf10_zj` |
| `aitc` | 其他 | 30 | `CWServ.ph_tdxdatacenter_aitc` |
| `app_misc` | 行情 | 24 | `HQServ.hq_nlp_app_misc` |
| `bgcz_gsbg` | F10与个股资料 | 35 | `CWServ.ph_tdxdatacenter_bgcz_gsbg` |
| `bgcz_hghb` | F10与个股资料 | 35 | `CWServ.ph_tdxdatacenter_bgcz_hghb` |
| `bgcz_zccz` | F10与个股资料 | 35 | `CWServ.ph_tdxdatacenter_bgcz_zccz` |
| `bjszt` | 涨停与题材 | 20 | `CWServ.ph_zt_bjszt` |
| `bkld_cfg` | 板块与行业 | 33 | `CWServ.ph_pcwebcall_hy_bkldscfg` |
| `bkld_cnx` | 板块与行业 | 33 | `CWServ.ph_pcwebcall_hy_bkldscnx` |
| `bklist` | 板块与行业 | 22 | `CWServ.app_bklist_ag` |
| `bklt` | 板块与行业 | 30 | `CWServ.ph_tdxdatacenter_bklt` |
| `bxzj_ss` | 涨停与题材 | 23 | `JJSServ.ph_zt_bxzj_ss` |
| `bxzj_sy_bkbd` | 板块与行业 | 28 | `JJSServ.ph_zt_bxzj_sy_bkbd` |
| `bxzj_sy_ggbd` | 涨停与题材 | 28 | `JJSServ.ph_zt_bxzj_sy_ggbd` |
| `bxzj_sy_jgbd` | 涨停与题材 | 28 | `JJSServ.ph_zt_bxzj_sy_jgbd` |
| `bxzj_xq` | 涨停与题材 | 23 | `JJSServ.ph_zt_bxzj_xq` |
| `cb_yjgg` | 财报 | 33 | `CWServ.ph_tdxdatacenter_cb_yjgg` |
| `cb_yjkb` | 财报 | 33 | `CWServ.ph_tdxdatacenter_cb_yjkb` |
| `cb_yjpd` | 财报 | 33 | `CWServ.ph_tdxdatacenter_cb_yjpd` |
| `cb_yjpd_xqy` | 财报 | 37 | `CWServ.ph_tdxdatacenter_cb_yjpd_xqy` |
| `cb_yjyg` | 财报 | 33 | `CWServ.ph_tdxdatacenter_cb_yjyg` |
| `cgfx_bgq` | 股东与持股 | 34 | `CWServ.ph_tdxdatacenter_cgfx_bgq` |
| `cgfx_jgzc_gn_lscgbd` | 股东与持股 | 45 | `CWServ.ph_tdxdatacenter_cgfx_jgzc_gn_lscgbd` |
| `cgfx_jgzc_hy_ggzcmx` | 股东与持股 | 45 | `CWServ.ph_tdxdatacenter_cgfx_jgzc_hy_ggzcmx` |
| `cgfx_jgzc_hy_xxq` | 股东与持股 | 42 | `CWServ.ph_tdxdatacenter_cgfx_jgzc_hy_xxq` |
| `cgfx_jgzc_hyzc` | 股东与持股 | 40 | `CWServ.ph_tdxdatacenter_cgfx_jgzc_hyzc` |
| `cjzx` | 资讯与AI | 24 | `CWServ.ph_content_cjzx` |
| `cmfb` | 其他 | 35 | `CWServ.ph_tdxdatacenter_cmfb_ggss` |
| `code_to_id` | F10与个股资料 | 28 | `CWServ.ph_agf10_code_to_id` |
| `cwbg` | 财报 | 25 | `CWServ.ph_agf10_cw_cwbg` |
| `cwfxxq` | 财报 | 27 | `CWServ.ph_agf10_cw_cwfxxq` |
| `cyl_clyzxsy` | 资金与筹码 | 37 | `CWServ.ph_tdxdatacenter_cyl_clyzxsy` |
| `cylv2_ag_cyljs` | 资金与筹码 | 40 | `CWServ.ph_tdxdatacenter_cylv2_ag_cyljs` |
| `cylv2_ag_cysj` | 资金与筹码 | 39 | `CWServ.ph_tdxdatacenter_cylv2_ag_cysj` |
| `cylv2_ag_qjt` | 资金与筹码 | 38 | `CWServ.ph_tdxdatacenter_cylv2_ag_qjt` |
| `cylv2_qb_sy` | 资金与筹码 | 37 | `CWServ.ph_tdxdatacenter_cylv2_qb_sy` |
| `cyq64` | 行情 | 25 | `HQServ.hq_nlp_app_cyq64` |
| `czsz` | 宏观 | 30 | `CWServ.ph_datacenter_hg_czsz` |
| `dbl_qsxt` | 其他 | 30 | `CWServ.ph_pcwebcall_dbl_qsxt` |
| `dghg_gdp` | 宏观 | 31 | `CWServ.ph_datacenter_dghg_gdp` |
| `dghg_jy` | 宏观 | 30 | `CWServ.ph_datacenter_dghg_jy` |
| `dghg_wm` | 宏观 | 30 | `CWServ.ph_datacenter_dghg_wm` |
| `dsjxy` | 其他 | 31 | `CWServ.ph_tdxdatacenter_dsjxy` |
| `dstx_gg` | 资讯与AI | 22 | `CWServ.phf10_zx_dstx` |
| `dstx_lhb` | 资讯与AI | 26 | `CWServ.ph_agf10_dstx_lhb` |
| `dstx_list` | 资讯与AI | 19 | `CWServ.ph_zx_dstx` |
| `dxb` | 其他 | 19 | `CWServ.phzzfw_dxb` |
| `dyjjyxf` | 宏观 | 33 | `CWServ.ph_datacenter_hg_dyjjyxf` |
| `dzjy` | 大宗交易 | 33 | `CWServ.ph_tdxdatacenter_dzjy_v2` |
| `dzjy_ss` | 大宗交易 | 33 | `CWServ.ph_tdxdatacenter_dzjy_ss` |
| `dzjy_sy` | 大宗交易 | 33 | `CWServ.ph_tdxdatacenter_dzjy_sy` |
| `dzjy_yxsb` | 大宗交易 | 35 | `CWServ.ph_tdxdatacenter_dzjy_yxsb` |
| `dzjy_zlfx` | 大宗交易 | 35 | `CWServ.ph_tdxdatacenter_dzjy_zlfx` |
| `dzjygp` | 大宗交易 | 35 | `CWServ.ph_tdxdatacenter_dzjy_gpxq` |
| `dzzx` | 大宗交易 | 35 | `CWServ.ph_tdxdatacenter_dzjy_dzzx` |
| `etf_fejk` | 基金与ETF | 25 | `CWServ.ph_jjzt_etf_fejk` |
| `etf_hq` | 基金与ETF | 23 | `CWServ.ph_jjzt_etf_hq` |
| `etf_kjzs` | 基金与ETF | 30 | `CWServ.ph_jjzt_etfjj_kjzsetf` |
| `etf_ldhy` | 基金与ETF | 25 | `CWServ.ph_jjzt_etf_ldhy` |
| `etf_rb` | 龙虎榜与两融 | 35 | `CWServ.ph_tdxdatacenter_tdxrb_etf` |
| `etf_sx_jjgs` | 基金与ETF | 28 | `CWServ.ph_jjzt_etf_sx_jjgs` |
| `etf_t0` | 基金与ETF | 23 | `CWServ.ph_jjzt_etf_t0` |
| `etf_tt` | 基金与ETF | 23 | `CWServ.ph_jjzt_etf_tt` |
| `etf_xf` | 基金与ETF | 30 | `CWServ.ph_jjzt_etfjj_xfetfjj` |
| `etf_yxhy` | 基金与ETF | 25 | `CWServ.ph_jjzt_etf_yxhy` |
| `etfjj_hyztetf` | 基金与ETF | 30 | `CWServ.ph_jjzt_etfjj_hyztetf` |
| `fhrz` | F10与个股资料 | 30 | `CWServ.ph_tdxdatacenter_fhrz` |
| `fhrz_agf10` | F10与个股资料 | 22 | `CWServ.ph_agf10_fhrz` |
| `filedown` | 其他 | 21 | `CWServ.tzx_filedown` |
| `fin` | 财报 | 26 | `CWServ.ph_agf10_cw_zcfzb` |
| `fn_jyr_v3` | 其他 | 22 | `CWServ.sys_fn_jyr_v3` |
| `fn_sczhjk` | 其他 | 22 | `CWServ.sys_fn_sczhjk` |
| `gbgd` | F10与个股资料 | 22 | `CWServ.ph_agf10_gbgd` |
| `gbgdsy` | 股东与持股 | 22 | `CWServ.ph_zt_gbgd_sy` |
| `gbgdzjc` | 股东与持股 | 23 | `CWServ.ph_zt_gbgd_zjc` |
| `gdfx` | 股东与持股 | 24 | `CWServ.ph_zt_gbgd_gdfx` |
| `gdhs` | 股东与持股 | 24 | `CWServ.ph_zt_gbgd_gdhs` |
| `ggbkxq` | 沪深港通 | 40 | `CWServ.ph_tdxdatacenter_hsgt_v3_ggbkxq` |
| `gggljj` | 基金与ETF | 23 | `CWServ.ph_jjzt_gggljj` |
| `ggrbxq` | 其他 | 32 | `CWServ.ph_tdxdatacenter_ggrbxq` |
| `ggrzrqmx` | 龙虎榜与两融 | 28 | `CWServ.ph_zt_lrzt_ggrzrqmx` |
| `ggxc_ggxcmx` | F10与个股资料 | 37 | `CWServ.ph_tdxdatacenter_ggxc_ggxcmx` |
| `ggxc_ggxctj` | F10与个股资料 | 37 | `CWServ.ph_tdxdatacenter_ggxc_ggxctj` |
| `ggxc_sy` | F10与个股资料 | 33 | `CWServ.ph_tdxdatacenter_ggxc_sy` |
| `ggxq` | 龙虎榜与两融 | 25 | `CWServ.ph_zt_lhbkp_ggxq` |
| `ggzj` | 资金与筹码 | 36 | `CWServ.ph_tdxdatacenter_zpfxb_ggzj` |
| `ggzx` | 其他 | 30 | `CWServ.ph_tdxdatacenter_ggzx` |
| `glbk` | 其他 | 21 | `CWServ.app_gmg_glbk` |
| `gmjjyjy` | 宏观 | 35 | `CWServ.ph_datacenter_mghg_gmjjyjy` |
| `gnzt_sjqd` | 其他 | 25 | `CWServ.ph_cfg_gnzt_sjqd` |
| `gnztetf` | 基金与ETF | 30 | `CWServ.ph_jjzt_etfjj_gnztetf` |
| `gqzy` | 其他 | 30 | `CWServ.ph_tdxdatacenter_gqzy` |
| `gscb` | 财报 | 33 | `CWServ.ph_tdxdatacenter_gscb_sy` |
| `gscb_qzfx` | 财报 | 35 | `CWServ.ph_tdxdatacenter_gscb_qzfx` |
| `gscb_stms` | 财报 | 35 | `CWServ.ph_tdxdatacenter_gscb_stms` |
| `gscb_yjdq` | 财报 | 35 | `CWServ.ph_tdxdatacenter_gscb_yjdq` |
| `gscb_zzms` | 财报 | 35 | `CWServ.ph_tdxdatacenter_gscb_zzms` |
| `gsjj` | F10与个股资料 | 22 | `CWServ.ph_agf10_gsjj` |
| `gszl_gglz` | F10与个股资料 | 35 | `CWServ.ph_tdxdatacenter_gszl_gglz` |
| `gszl_gsbg` | F10与个股资料 | 35 | `CWServ.ph_tdxdatacenter_gszl_gsbg` |
| `gszl_gstz` | F10与个股资料 | 35 | `CWServ.ph_tdxdatacenter_gszl_gstz` |
| `gszl_gszl` | F10与个股资料 | 35 | `CWServ.ph_tdxdatacenter_gszl_gszl` |
| `gszl_ryjg` | F10与个股资料 | 35 | `CWServ.ph_tdxdatacenter_gszl_ryjg` |
| `gszl_ryxl` | F10与个股资料 | 35 | `CWServ.ph_tdxdatacenter_gszl_ryxl` |
| `gszl_swjy` | F10与个股资料 | 35 | `CWServ.ph_tdxdatacenter_gszl_swjy` |
| `gszl_zxjy` | F10与个股资料 | 35 | `CWServ.ph_tdxdatacenter_gszl_zxjy` |
| `gyjj` | 宏观 | 30 | `CWServ.ph_datacenter_hg_gyjj` |
| `gzdb` | 估值 | 40 | `CWServ.ph_tdxdatacenter_gzzc_ggxq_gzdb` |
| `gzh` | 其他 | 28 | `CWServ.ph_tdxdatacenter_gz` |
| `gzzc_gg` | 估值 | 33 | `CWServ.ph_tdxdatacenter_gzzc_gg` |
| `gzzc_ggxq_ylpm` | 估值 | 40 | `CWServ.ph_tdxdatacenter_gzzc_ggxq_ylpm` |
| `gzzc_hyfx_hyfxsy_yjhy` | 估值 | 47 | `CWServ.ph_tdxdatacenter_gzzc_hyfx_hyfxsy_yjhy` |
| `gzzc_hyfx_hyfxsy_zhysj` | 估值 | 48 | `CWServ.ph_tdxdatacenter_gzzc_hyfx_hyfxsy_zhysj` |
| `gzzc_hyxq_czx` | 估值 | 39 | `CWServ.ph_tdxdatacenter_gzzc_hyxq_czx` |
| `gzzc_hyxq_hytj` | 估值 | 40 | `CWServ.ph_tdxdatacenter_gzzc_hyxq_hytj` |
| `gzzc_hyxq_scbx` | 估值 | 40 | `CWServ.ph_tdxdatacenter_gzzc_hyxq_scbx` |
| `gzzc_sy_ggbx` | 估值 | 38 | `CWServ.ph_tdxdatacenter_gzzc_sy_ggbx` |
| `gzzc_sy_hybx` | 估值 | 38 | `CWServ.ph_tdxdatacenter_gzzc_sy_hybx` |
| `gzzc_sy_ztms` | 估值 | 38 | `CWServ.ph_tdxdatacenter_gzzc_sy_ztms` |
| `gzzc_zsgz` | 估值 | 35 | `CWServ.ph_tdxdatacenter_gzzc_zsgz` |
| `gzzc_zsxq_gzzs` | 估值 | 40 | `CWServ.ph_tdxdatacenter_gzzc_zsxq_gzzs` |
| `gzzc_zsxq_hq_jdzf` | 估值 | 43 | `CWServ.ph_tdxdatacenter_gzzc_zsxq_hq_jdzf` |
| `gzzc_zsxq_lshc` | 估值 | 40 | `CWServ.ph_tdxdatacenter_gzzc_zsxq_lshc` |
| `gzzc_zsxq_zxgg` | 估值 | 40 | `CWServ.ph_tdxdatacenter_gzzc_zsxq_zxgg` |
| `hg_gdp` | 宏观 | 29 | `CWServ.ph_datacenter_hg_gdp` |
| `hg_jmsh` | 宏观 | 30 | `CWServ.ph_datacenter_hg_jmsh` |
| `hg_jybz` | 宏观 | 30 | `CWServ.ph_datacenter_hg_jybz` |
| `hg_nysc` | 宏观 | 30 | `CWServ.ph_datacenter_hg_nysc` |
| `hg_tz` | 宏观 | 28 | `CWServ.ph_datacenter_hg_tz` |
| `hg_wjzs` | 宏观 | 30 | `CWServ.ph_datacenter_hg_wjzs` |
| `hgtzy` | 龙虎榜与两融 | 27 | `CWServ.ph_zt_lhb_sy_hgtzy` |
| `hjcb` | 行情 | 19 | `CWServ.ph_hq_hjcb` |
| `hjhq` | 其他 | 30 | `CWServ.ph_tdxdatacenter_hjhq` |
| `hk_bsb` | 龙虎榜与两融 | 38 | `CWServ.ph_tdxdatacenter_tdxrb_hk_bsb` |
| `hk_gzb` | 龙虎榜与两融 | 38 | `CWServ.ph_tdxdatacenter_tdxrb_hk_gzb` |
| `hk_rqb` | 龙虎榜与两融 | 38 | `CWServ.ph_tdxdatacenter_tdxrb_hk_rqb` |
| `hk_rsb` | 龙虎榜与两融 | 38 | `CWServ.ph_tdxdatacenter_tdxrb_hk_rsb` |
| `hk_zrb` | 龙虎榜与两融 | 38 | `CWServ.ph_tdxdatacenter_tdxrb_hk_zrb` |
| `holiday` | 宏观 | 33 | `CWServ.ph_datacenter_hg_holiday` |
| `hq_nlp_zjlx` | 行情 | 20 | `HQServ.hq_nlp_zjlx` |
| `hscnjj` | 其他 | 28 | `CWServ.app_keyboard_hscnjj` |
| `hsgt_agxq` | 沪深港通 | 38 | `CWServ.ph_tdxdatacenter_hsgt_v3_agxq` |
| `hsgt_cgtj` | 沪深港通 | 38 | `CWServ.ph_tdxdatacenter_hsgt_v3_cgtj` |
| `hsgt_ss` | 沪深港通 | 36 | `CWServ.ph_tdxdatacenter_hsgt_v3_ss` |
| `hsgtzt` | 沪深港通 | 21 | `CWServ.ph_zt_hsgtzt` |
| `hsgtzt_ggtgg` | 沪深港通 | 27 | `CWServ.ph_zt_hsgtzt_ggtgg` |
| `hsgtzt_v3_bdbd` | 沪深港通 | 29 | `CWServ.ph_zt_hsgtzt_v3_bdbd` |
| `hsgtzt_v3_bsjj_gg` | 沪深港通 | 32 | `CWServ.ph_zt_hsgtzt_v3_bsjj_gg` |
| `hsgtzt_v3_bsjj_zj` | 沪深港通 | 32 | `CWServ.ph_zt_hsgtzt_v3_bsjj_zj` |
| `hsgtzt_v3_hyzb` | 沪深港通 | 29 | `CWServ.ph_zt_hsgtzt_v3_hyzb` |
| `hsgtzt_v3_nxjj_zj` | 沪深港通 | 32 | `CWServ.ph_zt_hsgtzt_v3_nxjj_zj` |
| `hundred_high` | 行情 | 32 | `HQServ.hq_nlp_app_hundred_high` |
| `hyfx` | 市场统计 | 33 | `CWServ.ph_tdxdatacenter_hyfx_sy` |
| `hygg` | 龙虎榜与两融 | 26 | `CWServ.ph_zt_lhb_sy_hygg` |
| `hygz` | 估值 | 40 | `CWServ.ph_tdxdatacenter_gzzc_hyxq_hygz` |
| `hypm` | F10与个股资料 | 22 | `CWServ.ph_agf10_hypm` |
| `hyyyb` | 大宗交易 | 36 | `CWServ.ph_tdxdatacenter_dzjy_hyyyb` |
| `hzkx` | 其他 | 30 | `CWServ.ph_tdxdatacenter_hzkx` |
| `ipozcz` | 新股与再融资 | 24 | `CWServ.ph_zt_xg_ipozcz` |
| `jgcg` | 股东与持股 | 24 | `CWServ.ph_zt_gbgd_jgcg` |
| `jgcg2` | 股东与持股 | 40 | `CWServ.ph_tdxdatacenter_cgfx_gdzz_jgcg` |
| `jgdy` | 其他 | 35 | `CWServ.ph_tdxdatacenter_jgdy_jgdy` |
| `jgfx` | 大宗交易 | 35 | `CWServ.ph_tdxdatacenter_dzjy_jgfx` |
| `jglrtj` | 龙虎榜与两融 | 28 | `CWServ.ph_zt_lrzt_lrtj_zrt` |
| `jgnc` | 其他 | 30 | `CWServ.ph_tdxdatacenter_jgnc` |
| `jgpj_sy` | 其他 | 33 | `CWServ.ph_tdxdatacenter_jgpj_sy` |
| `jj_cgtj` | 基金与ETF | 25 | `CWServ.ph_agf10_jj_cgtj` |
| `jjhq` | 基金与ETF | 26 | `CWServ.ph_jjzt_jjsc_jjhq` |
| `jjhqpd` | 其他 | 25 | `CWServ.ph_cfg_fx_jjhqpd` |
| `jjph` | 基金与ETF | 21 | `CWServ.ph_jjzt_jjph` |
| `jjpj` | 基金与ETF | 21 | `CWServ.ph_jjzt_jjpj` |
| `jjs_agxq` | 沪深港通 | 40 | `JJSServ.ph_datacenter_jjs_hsgt_v3_agxq` |
| `jjs_hsgtzt_v3_bsjj_bk` | 沪深港通 | 45 | `JJSServ.ph_datacenter_jjs_hsgtzt_v3_bsjj_bk` |
| `jjs_jgxq` | 沪深港通 | 40 | `JJSServ.ph_datacenter_jjs_hsgt_v3_jgxq` |
| `jjs_ss` | 沪深港通 | 38 | `JJSServ.ph_datacenter_jjs_hsgt_v3_ss` |
| `jjsph_cyqbgp_cgbh` | 其他 | 41 | `JJSServ.ph_datacenter_jjsph_cyqbgp_cgbh` |
| `jqzs` | 宏观 | 30 | `CWServ.ph_datacenter_hg_jqzs` |
| `jyfx` | F10与个股资料 | 22 | `CWServ.ph_agf10_jyfx` |
| `jysj` | F10与个股资料 | 22 | `CWServ.ph_agf10_jysj` |
| `kb_ag` | 其他 | 24 | `CWServ.app_keyboard_ag` |
| `kxjy` | 资讯与AI | 24 | `CWServ.ph_ai_sjfx_kxjy` |
| `lhb` | 龙虎榜与两融 | 21 | `CWServ.ph_zt_lhb_sy` |
| `lhb_ggxq` | 龙虎榜与两融 | 26 | `CWServ.ph_zt_lhb_sy_ggxq` |
| `lhb_jg` | 龙虎榜与两融 | 24 | `CWServ.ph_zt_lhb_sy_jg` |
| `lhb_jyr` | 龙虎榜与两融 | 22 | `CWServ.ph_zt_lhb_jyr` |
| `lhbjd` | 龙虎榜与两融 | 27 | `CWServ.ph_zt_lhb_sy_lhbjd` |
| `lhbkp_sy` | 龙虎榜与两融 | 23 | `CWServ.ph_zt_lhbkp_sy` |
| `lhbsj` | 龙虎榜与两融 | 23 | `DataAggregation.lhbsj` |
| `lhbss` | 龙虎榜与两融 | 21 | `CWServ.ph_zt_lhb_ss` |
| `lhbyc` | 龙虎榜与两融 | 27 | `CWServ.ph_zt_lhb_sy_lhbyc` |
| `lhfp` | 龙虎榜与两融 | 26 | `CWServ.ph_zt_lhb_sy_lhfp` |
| `lhfp_v2` | 龙虎榜与两融 | 27 | `DataAggregation.queryLhfp` |
| `lhjy` | 资讯与AI | 24 | `CWServ.ph_ai_sjfx_lhjy` |
| `lhqc` | 龙虎榜与两融 | 26 | `CWServ.ph_zt_lhb_sy_lhqc` |
| `lhtc` | 龙虎榜与两融 | 26 | `CWServ.ph_zt_lhb_sy_lhtc` |
| `lhzj` | 龙虎榜与两融 | 26 | `CWServ.ph_zt_lhb_sy_lhzj` |
| `lhzy` | 龙虎榜与两融 | 24 | `CWServ.ph_zt_lhb_sy_zy` |
| `lnjy` | 资讯与AI | 24 | `CWServ.ph_ai_sjfx_lnjy` |
| `lrbd` | 龙虎榜与两融 | 24 | `CWServ.ph_zt_lrzt_lrbd` |
| `lrmx` | 龙虎榜与两融 | 24 | `CWServ.ph_zt_lrzt_lrmx` |
| `lrrb` | 龙虎榜与两融 | 24 | `CWServ.ph_zt_lrzt_lrrb` |
| `lrzt_sy` | 龙虎榜与两融 | 22 | `CWServ.ph_zt_lrzt_sy` |
| `lyb` | 财报 | 24 | `CWServ.ph_agf10_cw_lyb` |
| `lzzdscj` | 沪深港通 | 37 | `CWServ.ph_hkdatacenter_wlnx_lzzdscj` |
| `macro_gy` | 宏观 | 30 | `CWServ.ph_datacenter_mghg_gy` |
| `macro_jq` | 宏观 | 32 | `CWServ.ph_datacenter_mghg_hgjq` |
| `macro_my` | 宏观 | 30 | `CWServ.ph_datacenter_mghg_my` |
| `macro_sz` | 宏观 | 30 | `CWServ.ph_datacenter_mghg_sz` |
| `macro_wj` | 宏观 | 30 | `CWServ.ph_datacenter_mghg_wj` |
| `macro_xf` | 宏观 | 30 | `CWServ.ph_datacenter_mghg_xf` |
| `macro_yh` | 宏观 | 30 | `CWServ.ph_datacenter_mghg_yh` |
| `margin` | 龙虎榜与两融 | 28 | `CWServ.ph_zt_lrzt_lrtj_zrt` |
| `mghg_cz` | 宏观 | 30 | `CWServ.ph_datacenter_mghg_cz` |
| `mghg_hbzc` | 宏观 | 32 | `CWServ.ph_datacenter_mghg_hbzc` |
| `mghg_mlc` | 宏观 | 31 | `CWServ.ph_datacenter_mghg_mlc` |
| `mkzj` | 沪深港通 | 29 | `CWServ.ph_hkdatacenter_mkzj` |
| `mtcsm_htss` | 其他 | 36 | `CWServ.ph_tdxdatacenter_mtcsm_htss` |
| `mtcsm_sy` | 其他 | 34 | `CWServ.ph_tdxdatacenter_mtcsm_sy` |
| `north` | 沪深港通 | 30 | `CWServ.ph_tdxdatacenter_hsgt` |
| `north_jg` | 沪深港通 | 45 | `JJSServ.ph_datacenter_jjs_hsgtzt_v3_bsjj_jg` |
| `nxjj` | 沪深港通 | 32 | `CWServ.ph_zt_hsgtzt_v3_nxjj_gg` |
| `phbj` | 其他 | 35 | `CWServ.ph_tdxdatacenter_hqpd_phbj` |
| `qh_bkzs` | 板块与行业 | 34 | `CWServ.ph_pcwebcall_qh_hqpd_bkzs` |
| `qh_cdzl` | 其他 | 33 | `CWServ.ph_tdxdatacenter_qh_cdzl` |
| `qh_qhhy` | 其他 | 34 | `CWServ.ph_pcwebcall_qh_hqpd_qhhy` |
| `qhlhb` | 其他 | 34 | `CWServ.ph_tdxdatacenter_qhlhb_sy` |
| `qhlhb_jcgc` | 其他 | 36 | `CWServ.ph_tdxdatacenter_qhlhb_jcgc` |
| `qhlhb_syss` | 其他 | 36 | `CWServ.ph_tdxdatacenter_qhlhb_syss` |
| `qsfx` | 估值 | 38 | `CWServ.ph_tdxdatacenter_gzzc_sy_qsfx` |
| `qsjg` | 其他 | 20 | `CWServ.phzzfw_qsjg` |
| `queryCardInfo` | 其他 | 31 | `DataAggregation.queryCardInfo` |
| `queryCardInfoStrategy` | 其他 | 39 | `DataAggregation.queryCardInfoStrategy` |
| `queryCyl` | 资金与筹码 | 25 | `DataAggregation.queryCyl` |
| `queryGggz` | 其他 | 26 | `DataAggregation.queryGggz` |
| `queryKxdz` | 其他 | 27 | `DataAggregation.queryKxdz` |
| `queryMarketSentiment` | 市场统计 | 38 | `DataAggregation.queryMarketSentiment` |
| `queryMessageCardInfo` | 其他 | 38 | `DataAggregation.queryMessageCardInfo` |
| `queryRandom` | 其他 | 29 | `DataAggregation.queryRandom` |
| `queryTechInfo` | 其他 | 31 | `DataAggregation.queryTechInfo` |
| `queryZST` | 其他 | 26 | `DataAggregation.queryZST` |
| `queryZbsq` | 其他 | 27 | `DataAggregation.queryZbsq` |
| `rddh` | 其他 | 23 | `CWServ.ph_cfg_rd_rddh` |
| `rdjd` | 其他 | 22 | `DataAggregation.rdjd` |
| `rdtc` | F10与个股资料 | 22 | `CWServ.ph_agf10_rdtc` |
| `rgfx` | 其他 | 30 | `CWServ.ph_tdxdatacenter_rgfx` |
| `rl` | 其他 | 28 | `CWServ.ph_tdxdatacenter_rl` |
| `rzrj` | 资讯与AI | 24 | `CWServ.ph_ai_sjfx_rzrj` |
| `rzrq_che` | 涨停与题材 | 26 | `CWServ.ph_zt_kp_rzrq_che` |
| `rzrq_mre` | 涨停与题材 | 26 | `CWServ.ph_zt_kp_rzrq_mre` |
| `sc_xgrl` | 其他 | 21 | `CWServ.phsj_sc_xgrl` |
| `scfx` | 市场统计 | 30 | `CWServ.ph_tdxdatacenter_scfx` |
| `scrd` | F10与个股资料 | 22 | `CWServ.ph_agf10_scrd` |
| `sctj` | 市场统计 | 30 | `CWServ.ph_tdxdatacenter_sctj` |
| `sczb` | 其他 | 23 | `CWServ.ph_cfg_fx_sczb` |
| `sgj` | 资讯与AI | 23 | `CWServ.ph_ai_sjfx_sgj` |
| `sgtzy` | 龙虎榜与两融 | 27 | `CWServ.ph_zt_lhb_sy_sgtzy` |
| `sl_v2` | F10与个股资料 | 23 | `CWServ.ph_agf10_sl_v2` |
| `slb` | 其他 | 29 | `CWServ.ph_tdxdatacenter_slb` |
| `slb_ggsy` | 其他 | 31 | `CWServ.ph_datacenter_slb_ggsy` |
| `slph` | 龙虎榜与两融 | 26 | `CWServ.ph_zt_lhb_sy_slph` |
| `sq_dkb` | 其他 | 32 | `CWServ.ph_tdxdatacenter_sq_dkb` |
| `sy_cmfb` | 涨停与题材 | 25 | `CWServ.ph_zt_kp_sy_cmfb` |
| `sy_gppk` | 涨停与题材 | 25 | `CWServ.ph_zt_kp_sy_gppk` |
| `sy_jgdy` | 涨停与题材 | 25 | `CWServ.ph_zt_kp_sy_jgdy` |
| `sy_qsjg` | 涨停与题材 | 25 | `CWServ.ph_zt_kp_sy_qsjg` |
| `sy_scfx` | 市场统计 | 25 | `CWServ.ph_zt_kp_sy_scfx` |
| `sy_tjcb` | 涨停与题材 | 25 | `CWServ.ph_zt_kp_sy_tjcb` |
| `sy_tp` | 其他 | 23 | `DataAggregation.sy_tp` |
| `sy_tztp` | 涨停与题材 | 25 | `CWServ.ph_zt_kp_sy_tztp` |
| `sy_ybzx` | 涨停与题材 | 25 | `CWServ.ph_zt_kp_sy_ybzx` |
| `sy_yjyg` | 涨停与题材 | 25 | `CWServ.ph_zt_kp_sy_yjyg` |
| `syfx_ggsy` | 其他 | 35 | `CWServ.ph_tdxdatacenter_syfx_ggsy` |
| `syfx_sytj` | 其他 | 35 | `CWServ.ph_tdxdatacenter_syfx_sytj` |
| `syph` | 龙虎榜与两融 | 26 | `CWServ.ph_zt_lhb_sy_syph` |
| `syzjzcbxq` | 其他 | 40 | `CWServ.ph_tdxdatacenter_syfx_syzjzcbxq` |
| `tdxrb_block` | 龙虎榜与两融 | 37 | `CWServ.ph_tdxdatacenter_tdxrb_block` |
| `tdxrb_gscb` | 财报 | 36 | `CWServ.ph_tdxdatacenter_tdxrb_gscb` |
| `tdxrb_gzb_stock` | 龙虎榜与两融 | 41 | `CWServ.ph_tdxdatacenter_tdxrb_gzb_stock` |
| `tdxrb_hypm_ag` | 龙虎榜与两融 | 39 | `CWServ.ph_tdxdatacenter_tdxrb_hypm_ag` |
| `tdxrb_hypm_hk` | 龙虎榜与两融 | 39 | `CWServ.ph_tdxdatacenter_tdxrb_hypm_hk` |
| `tdxrb_hypm_usa` | 龙虎榜与两融 | 40 | `CWServ.ph_tdxdatacenter_tdxrb_hypm_usa` |
| `tdxrb_kzz` | 龙虎榜与两融 | 35 | `CWServ.ph_tdxdatacenter_tdxrb_kzz` |
| `tdxrb_qh` | 龙虎榜与两融 | 34 | `CWServ.ph_tdxdatacenter_tdxrb_qh` |
| `tdxrb_rdfx` | 龙虎榜与两融 | 36 | `CWServ.ph_tdxdatacenter_tdxrb_rdfx` |
| `tdxrb_rqb_stock` | 龙虎榜与两融 | 41 | `CWServ.ph_tdxdatacenter_tdxrb_rqb_stock` |
| `tdxrb_rsb_stock` | 龙虎榜与两融 | 41 | `CWServ.ph_tdxdatacenter_tdxrb_rsb_stock` |
| `tdxrb_tjcb_sy` | 龙虎榜与两融 | 39 | `CWServ.ph_tdxdatacenter_tdxrb_tjcb_sy` |
| `tdxrb_zxg` | 龙虎榜与两融 | 35 | `CWServ.ph_tdxdatacenter_tdxrb_zxg` |
| `tfp_v2` | 其他 | 32 | `CWServ.ph_tdxdatacenter_tfp_v2` |
| `tfp_v2_agxq` | 其他 | 37 | `CWServ.ph_tdxdatacenter_tfp_v2_agxq` |
| `tfp_v2_bg` | 其他 | 35 | `CWServ.ph_tdxdatacenter_tfp_v2_bg` |
| `tfp_v2_bgxq` | 其他 | 37 | `CWServ.ph_tdxdatacenter_tfp_v2_bgxq` |
| `tfp_v2_gg` | 其他 | 35 | `CWServ.ph_tdxdatacenter_tfp_v2_gg` |
| `tfp_v2_ggxq` | 其他 | 37 | `CWServ.ph_tdxdatacenter_tfp_v2_ggxq` |
| `tfp_v2_gz` | 其他 | 35 | `CWServ.ph_tdxdatacenter_tfp_v2_gz` |
| `tfp_v2_gzxq` | 其他 | 37 | `CWServ.ph_tdxdatacenter_tfp_v2_gzxq` |
| `tfp_v2_kzz` | 其他 | 36 | `CWServ.ph_tdxdatacenter_tfp_v2_kzz` |
| `tfp_v2_kzzxq` | 其他 | 38 | `CWServ.ph_tdxdatacenter_tfp_v2_kzzxq` |
| `tfp_v2_tfptj` | 其他 | 38 | `CWServ.ph_tdxdatacenter_tfp_v2_tfptj` |
| `tjcb_cwfx` | 其他 | 35 | `CWServ.ph_tdxdatacenter_tjcb_cwfx` |
| `tjcb_gdqk` | 其他 | 35 | `CWServ.ph_tdxdatacenter_tjcb_gdqk` |
| `tjcb_gsds` | 其他 | 35 | `CWServ.ph_tdxdatacenter_tjcb_gsds` |
| `tjcb_gzdb` | 其他 | 35 | `CWServ.ph_tdxdatacenter_tjcb_gzdb` |
| `tjcb_scbx` | 其他 | 35 | `CWServ.ph_tdxdatacenter_tjcb_scbx` |
| `tjcb_yjgl` | 其他 | 35 | `CWServ.ph_tdxdatacenter_tjcb_yjgl` |
| `tjcj` | 财报 | 25 | `CWServ.ph_agf10_cw_tjcj` |
| `tjx` | 其他 | 36 | `CWServ.ph_tdxdatacenter_ybzxrm_tjx` |
| `tlsq` | 涨停与题材 | 22 | `CWServ.ph_zt_tlsq_sy` |
| `tlsq_dzyd` | 涨停与题材 | 24 | `CWServ.ph_zt_tlsq_dzyd` |
| `tlsq_fmxw` | 涨停与题材 | 24 | `CWServ.ph_zt_tlsq_fmxw` |
| `tlsq_gdjc` | 涨停与题材 | 24 | `CWServ.ph_zt_tlsq_gdjc` |
| `tlsq_knst` | 涨停与题材 | 24 | `CWServ.ph_zt_tlsq_knst` |
| `tlsq_ladc` | 涨停与题材 | 24 | `CWServ.ph_zt_tlsq_ladc` |
| `tlsq_lryd` | 涨停与题材 | 24 | `CWServ.ph_zt_tlsq_lryd` |
| `tlsq_lryd_20` | 涨停与题材 | 27 | `CWServ.ph_zt_tlsq_lryd_20` |
| `tlsq_tplq` | 涨停与题材 | 24 | `CWServ.ph_zt_tlsq_tplq` |
| `tlsq_xsjj` | 涨停与题材 | 24 | `CWServ.ph_zt_tlsq_xsjj` |
| `tlsq_yjbl` | 涨停与题材 | 24 | `CWServ.ph_zt_tlsq_yjbl` |
| `tqmf` | 其他 | 28 | `CWServ.ph_cfg_sc_rdzz_tqmf` |
| `tsyb` | 其他 | 20 | `CWServ.phzzfw_tsyb` |
| `tztp_gdtp` | 其他 | 32 | `CWServ.ph_datacenter_tztp_gdtp` |
| `tztp_ggtp` | 其他 | 32 | `CWServ.ph_datacenter_tztp_ggtp` |
| `tztp_gstp` | 其他 | 32 | `CWServ.ph_datacenter_tztp_gstp` |
| `tztp_ssk` | 其他 | 31 | `CWServ.ph_datacenter_tztp_ssk` |
| `tztp_zntp_cyltp` | 资金与筹码 | 38 | `CWServ.ph_datacenter_tztp_zntp_cyltp` |
| `tztp_zntp_djgz` | 其他 | 37 | `CWServ.ph_datacenter_tztp_zntp_djgz` |
| `tztp_zntp_kgtp` | 其他 | 37 | `CWServ.ph_datacenter_tztp_zntp_kgtp` |
| `tztp_zntp_khtp` | 其他 | 37 | `CWServ.ph_datacenter_tztp_zntp_khtp` |
| `usa_bsb` | 龙虎榜与两融 | 39 | `CWServ.ph_tdxdatacenter_tdxrb_usa_bsb` |
| `usa_gzb` | 龙虎榜与两融 | 39 | `CWServ.ph_tdxdatacenter_tdxrb_usa_gzb` |
| `usa_rqb` | 龙虎榜与两融 | 39 | `CWServ.ph_tdxdatacenter_tdxrb_usa_rqb` |
| `usa_rsb` | 龙虎榜与两融 | 39 | `CWServ.ph_tdxdatacenter_tdxrb_usa_rsb` |
| `usa_zrb` | 龙虎榜与两融 | 39 | `CWServ.ph_tdxdatacenter_tdxrb_usa_zrb` |
| `wlnx_hyzg` | 沪深港通 | 34 | `CWServ.ph_hkdatacenter_wlnx_hyzg` |
| `wlnx_nxjhfb` | 沪深港通 | 36 | `CWServ.ph_hkdatacenter_wlnx_nxjhfb` |
| `world_cal` | 宏观 | 29 | `CWServ.ph_datacenter_hg_cal` |
| `wxd` | 龙虎榜与两融 | 25 | `CWServ.ph_zt_lhb_sy_wxd` |
| `xfetfjj_v2` | 基金与ETF | 33 | `CWServ.ph_jjzt_etfjj_xfetfjj_v2` |
| `xg_xgjx` | 新股与再融资 | 22 | `CWServ.ph_zt_xg_xgjx` |
| `xg_xgkzz` | 新股与再融资 | 23 | `CWServ.ph_zt_xg_xgkzz` |
| `xg_xgsg` | 新股与再融资 | 22 | `CWServ.ph_zt_xg_xgsg` |
| `xg_xgsh` | 新股与再融资 | 22 | `CWServ.ph_zt_xg_xgsh` |
| `xgb` | 其他 | 29 | `CWServ.ph_tdxdatacenter_xgb` |
| `xgb_bfx` | 涨停与题材 | 25 | `CWServ.ph_zt_kp_xgb_bfx` |
| `xgb_kxt` | 涨停与题材 | 25 | `CWServ.ph_zt_kp_xgb_kxt` |
| `xgb_txx` | 涨停与题材 | 25 | `CWServ.ph_zt_kp_xgb_txx` |
| `xgipo` | 新股与再融资 | 21 | `CWServ.ph_zt_xg_ipo` |
| `xgmz` | 新股与再融资 | 22 | `CWServ.ph_zt_xg_xgmz` |
| `xgrl` | 其他 | 19 | `CWServ.ph_gg_xgrl` |
| `xjjj` | 新股与再融资 | 24 | `CWServ.ph_zt_tlsq_wxjg` |
| `xjllb` | 财报 | 26 | `CWServ.ph_agf10_cw_xjllb` |
| `xjllrfz` | 财报 | 38 | `CWServ.ph_tdxdatacenter_gscb_xjllrfz` |
| `xjphg` | 宏观 | 31 | `CWServ.ph_datacenter_xjphg_wm` |
| `xjphg_hgjq` | 宏观 | 33 | `CWServ.ph_datacenter_xjphg_hgjq` |
| `xsjj` | 其他 | 30 | `CWServ.ph_tdxdatacenter_xsjj` |
| `xwbj` | 龙虎榜与两融 | 26 | `CWServ.ph_zt_lhb_sy_xwbj` |
| `xwss` | 沪深港通 | 29 | `CWServ.ph_hkdatacenter_xwss` |
| `ybzx_xq_gg` | 其他 | 36 | `CWServ.ph_tdxdatacenter_ybzx_xq_gg` |
| `yffx` | 其他 | 30 | `CWServ.ph_tdxdatacenter_yffx` |
| `yhhb` | 宏观 | 30 | `CWServ.ph_datacenter_hg_yhhb` |
| `yjzx` | 其他 | 35 | `CWServ.ph_tdxdatacenter_ybzxrm_jx` |
| `ymdb` | 估值 | 40 | `CWServ.ph_tdxdatacenter_gzzc_ggxq_ymdb` |
| `yyb` | 龙虎榜与两融 | 25 | `CWServ.ph_zt_lhb_sy_yyb` |
| `yybxq` | 龙虎榜与两融 | 27 | `CWServ.ph_zt_lhb_sy_yybxq` |
| `yzycbd` | 龙虎榜与两融 | 28 | `CWServ.ph_zt_lhb_sy_yzycbd` |
| `zbhc` | 行情 | 24 | `HQServ.hq_nlp_app_zbhc` |
| `zbyz` | F10与个股资料 | 27 | `CWServ.ph_agf10_jyfx_zbyz` |
| `zcfz_all` | 财报 | 25 | `CWServ.ph_agf10_cw_zcfz` |
| `zcjj` | 基金与ETF | 21 | `CWServ.ph_jjzt_zcjj` |
| `zdjy` | 资讯与AI | 24 | `CWServ.ph_ai_sjfx_zdjy` |
| `zdsl` | 其他 | 29 | `CWServ.ph_bigdata_xtld_zdsl` |
| `zdt` | 市场统计 | 31 | `CWServ.ph_datacenter_zdt_zdfx` |
| `zdzq` | 其他 | 29 | `CWServ.ph_bigdata_xtld_zdzq` |
| `zgjby` | F10与个股资料 | 24 | `CWServ.ph_agf10_zg_jby` |
| `zgjsm` | F10与个股资料 | 24 | `CWServ.ph_agf10_zg_jsm` |
| `zgscm` | F10与个股资料 | 24 | `CWServ.ph_agf10_zg_scm` |
| `zgyq` | F10与个股资料 | 23 | `CWServ.ph_agf10_zg_yq` |
| `zgzs` | F10与个股资料 | 23 | `CWServ.ph_agf10_zg_zs` |
| `zhpj` | 资讯与AI | 24 | `CWServ.ph_ai_sjfx_zhpj` |
| `zjlx` | 资金与筹码 | 22 | `CWServ.ph_zt_kp_zjlx` |
| `zjlx_cxlclr` | 资金与筹码 | 26 | `CWServ.ph_zt_zjlx_cxlclr` |
| `zjlx_sy_zjjd` | 资金与筹码 | 27 | `CWServ.ph_zt_zjlx_sy_zjjd` |
| `zp` | F10与个股资料 | 20 | `CWServ.ph_agf10_zp` |
| `zp_v2` | F10与个股资料 | 20 | `CWServ.ph_agf10_zp` |
| `zpfxb_ggzsph` | 资金与筹码 | 38 | `CWServ.ph_tdxdatacenter_zpfxb_ggzsph` |
| `zpfxb_qsgfx` | 资金与筹码 | 37 | `CWServ.ph_tdxdatacenter_zpfxb_qsgfx` |
| `zpggld` | F10与个股资料 | 25 | `CWServ.ph_agf10_zp_ggld` |
| `zs_zsgz` | 资讯与AI | 24 | `CWServ.ph_zx_zsdz_zsgz` |
| `zsgz` | 基金与ETF | 21 | `CWServ.ph_jjzt_zsgz` |
| `zsjj` | 基金与ETF | 25 | `CWServ.ph_jjzt_etf_zsjj` |
| `zsxq_xgjj` | 估值 | 40 | `CWServ.ph_tdxdatacenter_gzzc_zsxq_xgjj` |
| `ztmx` | 其他 | 31 | `CWServ.ph_pcwebcall_tclt_ztmx` |
| `ztqd` | 其他 | 30 | `CWServ.ph_tdxdatacenter_ztqd` |
| `zttz_jhqz` | 其他 | 27 | `DataAggregation.zttz_jhqz` |
| `zttz_xg` | 其他 | 35 | `CWServ.ph_tdxdatacenter_zttz_xggp` |
| `zttz_xqy` | 其他 | 34 | `CWServ.ph_tdxdatacenter_zttz_xqy` |
| `zttz_xqy_v2_qsid` | 其他 | 42 | `CWServ.ph_tdxdatacenter_zttz_xqy_v2_qsid` |
| `zttz_xzcfg` | 其他 | 36 | `CWServ.ph_tdxdatacenter_zttz_xzcfg` |
| `zttz_xzgn2` | 其他 | 28 | `DataAggregation.zttz_xzgn2` |
| `zttz_zhid` | 资讯与AI | 28 | `CWServ.ph_AIcard_zttz_zhid` |
| `zttz_ztld` | 其他 | 35 | `CWServ.ph_tdxdatacenter_zttz_ztld` |
| `zttz_zy` | 其他 | 33 | `CWServ.ph_tdxdatacenter_zttz_zy` |
| `ztzb` | 涨停与题材 | 25 | `CWServ.ph_zt_kp_sy_ztzb` |
| `ztzt` | 其他 | 30 | `CWServ.ph_tdxdatacenter_ztzt` |
| `zx_dstx` | 资讯与AI | 25 | `CWServ.ph_agf10_zx_dstx` |
| `zx_gggz` | 资讯与AI | 24 | `CWServ.ph_zx_zsgz_gggz` |
| `zx_tsyb` | F10与个股资料 | 25 | `CWServ.ph_agf10_zx_tsyb` |
| `zxgzb` | 其他 | 18 | `CWServ.mzx_zxgzb` |
| `zxts` | F10与个股资料 | 22 | `CWServ.ph_agf10_zxts` |
| `zxts2` | F10与个股资料 | 23 | `CWServ.ph_agf10_zxts2` |
| `zxts3` | F10与个股资料 | 23 | `CWServ.ph_agf10_zxts3` |
| `zxts4` | F10与个股资料 | 23 | `CWServ.ph_agf10_zxts4` |
| `zxts5` | F10与个股资料 | 23 | `CWServ.ph_agf10_zxts5` |
| `zxts_yjpl` | F10与个股资料 | 27 | `CWServ.ph_agf10_zxts_yjpl` |
| `zxzx_zt_new` | 其他 | 24 | `CWServ.mzx_zxzx_zt_new` |
| `zysy` | 财报 | 26 | `CWServ.ph_agf10_cw_sy_v2` |
| `zyzb` | 财报 | 25 | `CWServ.ph_agf10_cw_zyzb` |
| `zyzbxq` | 财报 | 31 | `CWServ.ph_agf10_cw_zyzbxqy_sy` |
<!-- END: product_index -->

---

## 免责声明

本项目仅用于**学习与研究**，数据来自第三方公开服务器，不保证准确性、完整性与时效性。
**不构成任何投资建议**，请勿用于实盘交易决策或商业分发。
使用前请自行确认符合当地法律法规与数据源服务条款。

---

## 许可

[MIT](LICENSE)。数据来自第三方公开服务器，**本项目不拥有数据**，详见上方免责声明。

