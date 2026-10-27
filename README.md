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
| 当日分时（240 点，含均价） | `get_minutes()` |
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

    # ② 当日分时 240 点
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
| 当日分时 | 240 点，含均价 |
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

## API 一览

| 方法 | 返回 | 说明 |
|---|---|---|
| `products(domain=None)` | `list[ProductInfo]` | 421 个数据产品，可按域过滤 |
| `domains()` | `list[str]` | 17 个域 |
| `get_bars(symbol, period, count, adjust)` | `list[Bar]` | K 线；`count > 416` 自动翻页 |
| `get_minutes(symbol)` | `list[MinutePoint]` | 当日分时 |
| `get_ticks(symbol, count=200)` | `list[Tick]` | 逐笔成交，自动翻页，按时间升序 |
| `get_indicators(symbol, indicators, period, count)` | `Table` | 由 K 线派生，行一一对应 |
| `get_price_limits(symbol, prev_close, is_st)` | `tuple` | `(涨停价, 跌停价)` |
| `get_market_page(market, offset, page_size, cols)` | `list[Quote]` | 行情一页 |
| `get_universe(market, max_pages, page_size)` | `list[Quote]` | 翻页枚举 + 去重 |
| `get_quotes(symbols, batch)` | `list[Quote]` | 批量报价；缺码单发兜底，仍缺则抛 `EmptyResult` 点名 |
| `get_name(symbol, market=None)` | `str` | 代码 → 名称 |
| `get_boards(kind)` | `Table` | `all` / `industry` / `concept` |
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
| `rate_stats()` | `dict` | 当前节流间隔、被限流次数、冷却主机数 |

```python
# 缓存默认关闭（保持"每次都真的问服务端"的可预测语义）
from tdxdata._engine.cache import TTLCache
with tdxdata.Client(cache=TTLCache(ttl=300)) as c:
    c.get("holiday")     # 第二次命中缓存，不占用限流配额
```

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

- **被限流是常态**：库只降低概率、被踢后自愈，不能消除。别指望高并发抓全市场。
- **分页枚举保证不重复，不保证不遗漏**（服务端按涨幅排序，盘中会漂移），
  跨次拼接时建议按代码去重。
- **行业板块拿不到成分股**：会抛 `NotSupportedError` 并指出替代接口，不返回残缺数据。
- **`get_board_stat` 不能指定板块**：返回哪个板块由服务端决定；要指定板块请用 `get_board_members`。
- **`get_samples` 的浮点列序未锚定、个数会变**（盘中递增），只有 `f0`（开盘）与 `pre_close` 可信；
  **别写死个数，别把 `f*` 当 high/low/volume**。
- **块数据（`get_block_data`）的块名由服务端下发**，不是通用常量，目前只验证了少数几个。
- **资金流净值口径**与部分第三方软件不同，**不宜跨源比较**。

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
