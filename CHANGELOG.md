# 变更记录

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [未发布] — 2026-10-28

### 新增

- **`indicator.py` 5 个确定性数学指标**：`hv`（滚动年化历史波动率）、`momentum`（n 期动量）、
  `sharpe`（滚动年化夏普）、`max_drawdown`（滚动最大回撤）、`beta`（对基准的滚动 OLS 斜率）。
  契约与既有 6 指标一致：输出长度 ≡ 输入长度、不足周期填 `None`、纯标准库无网络。
  `get_indicators` 可点名使用（默认输出列逐列不变）；`beta` 需两条序列，直接调
  `tdxdata.indicator.beta(个股bars, 基准bars, n)`。
- **`Client.get_market_breadth(view="summary"|"minute")`** — **实时涨跌家数**（`HQServ.PBSdstat`，
  ctl `0x11`）。`summary` 恰好 5 列：`up_count`/`down_count`/`flat_count`/`limit_up_count`/
  `limit_down_count`（抓包回放实测 `1637/3775/160/35/15`，自校验 `up+down+flat = 5572` = 当日
  有价证券总数）；`minute` 为逐分钟序列，语义未定的 10 个 `uint32` 只进 `row["raw"]["u32"]`。
  ⚠️ 与 `get_market_stat("updown")` **不同**：后者是历史近 30 个交易日，本条是**实时快照**
  （回包无日期字段 → `meta.as_of=None` 且必带 `as_of_note`）。
- **`Client.get_intraday_samples(symbols_, count=None)`** — **多代码当日分时点位**（`HQServ.PBSample`，
  ctl `0x11`）。列 `code`/`market`/`total_points`/`pre_close`/`n_points`/`points`（`points` 是 list，
  不摊成 `f0..fN`）。语义已锚定：`n_points(field7) ≡ len(points)` **逐点非抽样**（25/25 帧），
  `points[0]` 为 09:30 首点，`pre_close` ↔ `PBCombHQ.CLOSE`（**昨收**）。实证范围仅指数 `999999`，
  个股未复核 → docstring 与 `meta["note"]` 标实验性；`count` 只是本地截尾。
- **`FIELD_MAP` 扩到 52 键 / 296 条列映射**：宏观域 5 + 基金与 ETF 域 11 + 本轮新增 5 个
  （`hq_nlp_zjlx` 12 列、`lrmx` 9、`north` 7、`zdsl` 7、`zdzq` 7），全部只映射
  **有实测证据**的列（每条带文档/日期/样本首行出处 + 恒等式）；语义未定的列一律留在 `raw`
  （宁可少映射，不许错映射）。`COL_GUARD` 由 4 条扩到 **35 条**（+1 条 `COL_GUARD_MORE`），
  守卫清单一律取**真实列形状**，不手写模板。测试见 `tests/test_mapping_products.py`（103 例）。
- **`Client.get(..., mapped=True)`**：选开的列义语义层 —— 已登记列义的产品返回标准字段 +
  逐行 `raw`（原值一个不丢）；未登记的产品抛 `ValueError`。默认 `False`，`get()` 的原始列
  契约一个字节都不变。

### 修复

- **`hq_extra.parse_rowtable` 丢整表（数据丢失）**：`field3` 列名块可能出现两次（首非空 + 末尾空
  占位），旧实现取**最后一个** → 列名被顶成 `[]`，`cap_1023_183457.pcap` msgid=150 的 **77 行
  整表消失**。现改为**取第一个非空列名块**；多个非空且内容不同则抛 `NotSupportedError`（不静默挑）。
  全量 1496 帧里"多个 field3"仅此 1 帧、"多个非空且不同"0 次 → 零回归。
- **`hq_extra._cells` 崩溃**：`wt==5` 的字节长度 ≠ 4 时 `struct.unpack` 抛 `struct.error`；
  现按长度判断，非 4 字节回 hex（**刻意不跳过**——跳过会让后面所有列左移一位）。
- **`hq_extra.parse_ticks` 静默产出垃圾**：缺 `field7` 时旧实现**回落到硬编码 `TICKEX_COLS`**，
  对 `PBHQInfo`/`PBSample` 返回"字段名正常、值是二进制垃圾"，对 `PBZST` 出现列值互换。
  现改为**认不出列名就返回空**（列名判据：≥60% 含字母），`PBTickEx` 24/24 真实帧逐字节不变。
- **`hq_extra.parse_samples` 跨命令错配**：拆成 `parse_samples_more`（只认顶层 `field1`）与
  `parse_sample_single`（顶层 `field8`、无 `field1`），`parse_samples` 保留为分发器（零改动调用方）。
  `PBSampleMore` 257/257 帧结果不变；`PBDeepWarn` 36/36 → `[]`（改前会产出假记录
  `{code:'002990', count:14435}`）。
- **能力注册表 forex 归因过时**（评审 §3.1）：`capabilities.py` 由「同期货：无通道」改为
  「**7615 主通道拿不到（证券表外）+ EX 7727 扩展通道服务端有、本库未接入**」
  （USDCNY 6.6746 等实测锚点，2026-10-22 快照，4 台 EX 服务器当时 2 台可用 —— 快照不是承诺），
  README 能力表标记块同步重生成。
- **README 已知限制**：新增 1 条（`qh_cdzl`/`qh_bkzs`/`qh_qhhy` 三个 `qh_` 周边产品是
  板块指数/研判类、**不是合约行情** + PC EX 7727 通道未接入现状），修正 item 10 的外汇半句；
  总条数 26 → 27，交叉引用同步。
- **域分类错位两处**（评审 §3.2）：`tfp_v2_kzz`/`tfp_v2_kzzxq`（停复牌-可转债）不再被
  `kzz` 兜底规则卷进「新股与再融资」（tfp 全家族统一「其他」）；限售解禁 `xjjj`
  （`ph_zt_tlsq_wxjg`）从「涨停与题材」归「新股与再融资」。新增「注册域 == domain_of(cmd)」
  421 条全量一致性门禁。

### 门禁 / 测试

- **跨日缺陷修复**（2026-10-28 实测）：`test_timeliness` 两个用例与黄金回放的时钟来源，
  在录制日之后回放会由 `hybrid` 降级 `local`（本机日期 ≠ 录制日）。修法是**冻结"本机日期"
  到样本录制当天**（用例注入场景日 / 回放时钟按样本 `fetched_at` 冻结）—— 严格比对一个字
  未放宽（关掉冻结立即复红，已做 mutation 验证）。
- 新增测试：`tests/test_get_mapped.py`（5 例）、`tests/test_mapping_products.py`（26 例）、
  `tests/test_indicator.py` +10 例、`tests/test_capabilities.py` +1 例、`tests/test_core.py` +1 例。
- **抓包挖掘轮的测试增量**：`tests/test_hq_extra.py` +21 例（`field3` 双块 / 短 fixed32 /
  `parse_ticks` 不回落 / `PBZST` 列值互换判据 / `parse_samples` 两命令互斥 等，全部合成字节、
  零抓包依赖）、`tests/test_mapping_products.py` 74 → 103 例、新增
  `tests/test_pb_breadth.py`（24 例）+ `tests/test_pb_sample.py`（27 例）。
  全量 **928 passed / 0 failed**；`golden_runner.py check` PASS（27 份样本、差异 0）；
  `_dev/check.py` 通过 9 / 失败 0 / 跳过 1。

---

## [0.2.0] — 2026-10-27

> ⚠️ **本次含破坏性变更**：`vol` 单位统一为「手」。升级后数值可能与旧版差 100 倍。

### 新增

- **`get_ticks(symbol, count=200)`** — 逐笔成交。自动翻页、按时间升序、去重。
  返回 `Tick(price, vol, time, raw)`；`vol` 单位为手。
- `get_quotes()` 增加**成交量 / 成交额**两列（`vol`、`amount`）。
- `get_block_rows()` — 块数据按页取全（单页上限 100 行）。
- `get_board_stat()` — 板块成分统计（七列）。
- `get_samples()` — 批量抽样行情，`pre_close` 可信。

### 变更（破坏性）

- **`vol` 对外统一为「手」**：`Bar` / `Quote` / `MinutePoint` / `Tick` 四者同单位。
  - 日线、分钟线的股票与 ETF：服务端给「股」，库÷100
  - 周线、月线的手计价品种（转债/回购/指数/板块）：服务端已÷100，库×100 还原
  - **`Bar.vol` 类型为 `float | None`**
- **指数 / 板块的分钟线 `vol` 改为 `None`**：服务端该列是 `amount/100` 的派生值，
  不是成交量。原值仍在 `raw["Volume"]`。该品类日线 `vol` 不受影响。
- **`Tick.trade_count` / `Tick.bs_flag` 改为 `None`**：服务端没有这两个字段，
  旧版恒填 `0` / `2` 是编造值（`bs_flag=2` 会被误读成"卖"）。
  真实方向信息在 `raw["InOutFlag"]`（`0`/`1`），**语义尚未确定，故不映射**。

### 修复

- **`881xxx` 板块代码被误判为北交所**：导致 `get_quotes` / `get_bars` 取不到
  （报错还误导成"北交所请用 8xxxxx"）。现与前缀 `880xxx` 同属沪市板块指数。
- `Bar.vol` 缺失时默认 `0.0`，现为 `None`（未知 ≠ 0）。
- `server_time` 时分秒缺前导零（`94258` → `09:42:58`）。
- `get_market_page()` 默认列缺少成交额与成交量。
- 5 处冒烟断言写死了当时的行情数值，换日必红；改为结构性断言。

### 文档

- README 重写为使用者视角：怎么装、怎么用、能取什么、有哪些限制。
- 新增 [`docs/VERIFICATION.md`](docs/VERIFICATION.md)：单位判定、未定字段清单、交叉验算模板。
- 新增本文件。

---

## [0.1.0] — 2026-10-25

首个可用版本。

### 包含

- **K 线**：日 / 周 / 月 / 季 / 1·5·15·30·60 分钟，支持前复权 / 后复权 / 不复权
- **当日分时**：240 点，含均价
- **跨市场代码**：沪 / 深 / 创业板 / 科创 / 北交所 / B 股 / 可转债 / 逆回购 / 指数 / 板块指数
- **技术指标**：EMA、RSI 等（由 K 线派生，与 K 线逐行对齐）
- **涨跌停价**：按板块规则 + 交易所取整（主板 ±10%、双创 ±20%、北交所 ±30%、转债 ±20%）
- **数据中心 421 个产品**：龙虎榜、财报、资金流、分红融资、大盘统计等 17 个域
- **结构化接口**：F10、财务报表、分红融资、板块清单与成分股
- **限流自保护**：进程级匀速闸门 + 自适应退避 + 主机冷却 + 空闲重连
- **同步与异步**：`Client` / `AsyncClient`
- **可选本地缓存**：`TTLCache`，默认关闭

### 不支持（明确抛 `NotSupportedError`）

- 五档盘口（协议无买卖盘字段）
- 行业板块成分股（无"按板块代码取成分"通道）
- 年线 / 3 分钟线（服务端返回 0 行）
