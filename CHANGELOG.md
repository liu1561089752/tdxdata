# 变更记录

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [未发布]
### 性能与防错（2026-10-29 库体检后的六项修复）

> 依据：31 份抓包的实测复核 + `notes/17` 的问题总账。**没有一项是"感觉更快"**，每项都带判据与用例。

- **`get_quotes` 批量默认 20 → 100，上限 447（实测）**：945 个 `PBCombHQ` 请求中最大一次带
  **447 个码**、回包同样 447 行且完整；≥20 码的 188 个请求里 180 个回包行数 ≥ 请求数。
  旧默认 20 的依据「抓包只验证到 6 个」已被推翻。全市场 5400 只：270 次往返 → **13 次**。
  超过 447 抛 `ParamError`。测试 `tests/test_batch_ceiling.py`（15 例）。
- **缺码补发改成成批**：`1+N` → `1+ceil(N/batch)`；歧义码仍单发（`DEF-014` 不复活）。
- **空参数槽 fail-fast**：`["zxt", [], …]` 会被服务端判 `-1002`，现在发请求前抛 `ParamError`
  并给出改法（写 `""`）。判据：语料里「参数数组含空槽」只出现 2 次、两次都是 `-1002`；
  整数组为空的 `{"Params": []}` 不受影响。测试 `tests/test_param_slots.py`（12 例）。
- **`cache="auto"`：按命令的实测新鲜度逐条定 TTL**：新增 `tdxdata/caching.py` +
  `data/cache-ttl.json`（211 命令；131 可缓存 / 80 不缓存）。`ttl = clamp(滞后中位数/2, 1h, 30d)`，
  未登记命令 `default_ttl=0`。`TTLCache` / `auto_cache` / `ttl_for_cmd` / `default_ttl` /
  `CACHE_POLICY` 提到包根。**默认仍不缓存**。测试 `tests/test_caching_policy.py`（14 例）。
- **`mapping.apply` 热路径**：取值改走 `dict.get`（`Row.get` 每次多判一次 `isinstance(key,int)`），
  映射对提到行外。实测单表 140 → **114 µs**（−19%）、5000×30 大表 44 → **29 ms**（−34%），
  函数调用 278 万 → 136 万。
- **默认 gap 单一来源**：`Client(gap=…)` 默认改为 `pacer.BASE_GAP`（原来两处各写 2.2）。
- **默认日期不再闷头查旧数据**：`#date#` 默认值超 30 天未校准会 `RuntimeWarning`，
  新增 `tdxdata.set_default_date("YYYY-MM-DD")`。
- **公开方法类型注解 53% → 100%**（包声明 `py.typed` + `Typing :: Typed`）。
  回归闸门 `tests/test_default_date_and_annotations.py`。
- README 更正：`get_quotes` 的 batch 默认/上限；限流结论补「2026-10-29 复核未观察到限流」的边界（**常量未改**）。



### 修复

- **错位表（ColName 数 ≠ Content 行长）按名字读取会静默整行错位（挖掘轮 M8）**：
  语料 2529 条表里 **424 条（16.8%）** 列名数与行长不等，涉及 **70 个命令**，
  且是两种稳定形态 —— **尾部多**（`ph_bigdata_xtld_zdsl` 12 名 / 8 值：`EndDate`/
  `AdjustingFactor`/`fq` 等复权附加列行里没有值）与 **头部多**（`hq_nlp_zjlx` 21/17、
  `ph_agf10_cw_xjllb` 65/64：`ans/date/time/total_num`、`isjr` 是一次查询的元信息，
  不逐行重复）。`zip()` 只按前缀对齐，所以**头部多**的那种会把每个列名整体左移，
  用户读 `row["code"]` 拿到的是别的东西却毫无提示。
  - `Table.ragged` / `Table.is_ragged`（新，只读）：如实报出 `(列名数, 真实行长)`，
    正常表为 `None`，不参与 `repr`/比较、不进 `to_dict()`（序列化键集合不变）；
  - `provenance.notes` 自动多一条说明 + `extra["ragged_cols"]`（`client._ragged_note()`）；
  - `mapping._resolve_cols()` 新增 `server-prefix` / `server-suffix` 两个来源标记：
    只在**前缀窗口或后缀窗口唯一命中登记形状**时才对齐，歧义/无命中一律不猜；
  - `mapping.apply()` 的对齐窗口行改按**位置**取值（曾经按新列名 `r.get()` 绕回错位值）；
  - 新增第二条守卫（**只对登记过 `COL_GUARD` 的产品生效**）：登记形状逐位相等、
    但**行长更短** → `mapping_skipped="cols-guard-ragged"`，一列都不映射
    （反例 `ph_zt_lrzt_lrmx` 9 名 / 6 值：行首是日期，`N001` 却登记为
    `margin_balance_yuan`，旧守卫会把它塞进「融资余额」）。没登记形状的产品
    沿用 M3 结论（缺值在尾部、前缀映射成立，`tjcj` 有实证），不一刀切。
  - 验收：24 个「已登记但错位」产品中 **13 个**从「整表退化成 raw」变成正确映射
    （`zdsl`/`zdzq`/`hq_nlp_zjlx`/`xjllb`/`fin`/`lyb`/`zgyq`/`zyzbxq`/`zyzb`/`zxts5`/
    `hypm`/`zpggld`/`gscb`），其余 11 个仍 fail-closed。夹具 4 份真实回包字节 +
    `tests/test_ragged_table.py`（20 例）。证据：`reports/eval3/findings/M8-错位表与NLP家族.md`、
    `notes/12-错位表与NLP家族.md`。

---

## [0.3.0] — 2026-10-28

> ⚠️ **本次含 4 项破坏性变更**：`meta` 字典 → `Provenance`、`vol` 单位统一、非价格序列拒绝、
> `concurrency` 参数移除。**升级前先读文末「迁移指南」** —— 旧代码该改哪一行都在那张表里
> （表由 `tdxdata.MIGRATIONS` 渲染，与代码同源）。

### 新增

- **宏观域 typed 层 `c.macro.*`（PROD-003）**：`products.json` 里 29 个宏观产品切成
  **18 个 typed（44 个模式）+ 11 个明确 unavailable**。每个模式一条登记：请求参数模板、
  期望列清单（ColDes 顺序）、列名映射、单位、**证据**（哪条算式、命中率多少）。列义**不靠文档**，
  靠语料回包里逐行算得出来的关系（例：`czsz` 的「增减额」= 本期−上一行 17/17，文档写的「同比」
  被 0/10 否证）。`c.macro.gdp()` / `fiscal()` / `calendar()` / `holidays()` / `prosperity()` /
  `prices()` / `bank()` / `investment()` / `employment()` / `income()` / `industry()` /
  `output()` / `retail()` / `us_gdp()` / `us_rate()` / `us_money()` / `us_prices()` /
  `us_balance()`，统一 `mode=`；另有 `fetch()` / `modes()` / `products()` 三条自省。
  **守卫是 fail-closed**：运行时列清单与登记 layout 不是逐位相等就一列都不映射
  （`provenance.extra["macro_guard"]` 说明原因）。语料覆盖不到的 11 个产品抛
  `NotSupportedError` 并说清「没有回包可证列义」—— **不猜**。证据链见
  `notes/11-宏观域列语义与typed映射.md`、`tests/fixtures/macro/`（47 份真实回包字节）。
- **`c.master` 证券主数据（PROD-001）**：`snapshot()`（`get_universe` 聚合 + 进程缓存 + 边界声明）、
  `resolve(code)`（两层防猜：5 位先拒、未命中不退前缀推断）、`search(name_like=)`；
  309 条服务端真值 100% 命中（北交 2 条按口径如实豁免）。
- **`c.calendar` 交易日历（PROD-002）**：`is_trading_day` / `next` / `prev` / `range`。
  事实源两路、**不维护节假日表**（单点=999999 日线起步窗、区间=880005 的 `date` 列）；
  `None ≠ False`（取不到就说取不到）。`Client._prev_trade_date` 收编为单行委托。
- **`tdxdata.invariants.validate(container)`（批次 E）**：§6.5 六条恒等式纯函数检查器
  （ohlc / amount≈vol×100×close / breadth_total / minute_points / minute_volume_sum / pre_close 跨通道）。
  **闸门不自动开**（性能取舍，有源码断言钉住）。
- **`tdxdata.deprecated()` 弃用机制 + `tdxdata.__version_info__`（ENG-008）**：
  统一消息格式（旧名 / since / 替代品 / 计划移除版本）、每进程同一处只提醒一次、
  `stacklevel` 指到调用方；`MIGRATIONS` 登记已生效的破坏性变更并可直接渲染迁移表。
- **`products.json` 进程级只读缓存（ENG-014）**：10 个 `Client` 只读 1 次盘（77 KB / ~1.5 ms/实例）；
  两个规格字典改为**共享只读**（文档已声明不许原地改）。
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

### 变更（破坏性）

| 版本 | 变了什么 | 旧代码怎么改 | 为什么 |
|---|---|---|---|
| `0.3.0` | `Table`/容器的 `meta` 字典**公开面移除**，改为 `Provenance` 一等字段（`as_of` / `status` / `fetched_at` / `elapsed` / `cmd` / `clock` / `source` / `extra`） | `t.meta["as_of"]` → `t.as_of`；`t.meta["status"]` → `t.status`；`t.meta["source"]["host"]` → `t.provenance.source["host"]`；未登记的键一律在 `t.provenance.extra`（**不保证**存在，读时要 `.get`） | ARCH-002：`meta` 是自由字典，键名漂移无法被类型检查/黄金样本钉住；改成 dataclass 字段后「哪些键必须存在」由代码表达，缺键在构造期就报错 |
| `0.2.0` | `vol` 对外统一为**手**（此前日线/分钟线的股票与 ETF 是「股」） | 旧版拿到的「股」数值 ÷100 即手；需要服务端原值时读 `raw["Volume"]`（**原单位，别直接参与计算**） | 同库四类容器（Bar/Quote/MinutePoint/Tick）单位必须一致，否则「合计成交量」这类跨容器运算会静默差 100 倍 |
| `0.3.0` | `get_bars()` **拒绝**非价格序列（`880005` 涨跌家数、`880006` 停板家数等），抛 `NotSupportedError`（旧版照发，回来的 OHLC 是四个家数） | `c.get_bars("880005")` → `c.get_breadth("880005")`（列名 `up_count`/`down_count`/…）；判断某代码是否属于这类序列用 `tdxdata.is_non_price(code)` | 把家数当价格算，任何技术指标都会给出**看起来正常但完全错**的值 —— 这是本项目最贵的一类静默错误（见 `series_kinds.py` 模块文档） |
| `0.3.0` | `Client(concurrency=2..4)` **参数已移除**（现在传它会 `TypeError`） | 串行取数；要多标的请自己 `for` 遍历或 `AsyncClient`（同一根连接仍受进程级节流约束） | 实测 4 lane：头 5 秒即被服务端重置，175 秒 0 次成功、3 台服务器进冷却；串行 68.64 s 全部成功。功能是**删掉**的，不是修好的 |

> 上表由 `tdxdata.migration_table()` 从 `tdxdata.MIGRATIONS` 渲染 —— 改迁移说明请改代码，
> 不要只改这张表（否则下一个版本又会出现「文档说 A、代码做 B」）。

### 内部架构（不影响调用方，但解释为什么有些行为变了）

- **ARCH-001 `Transport` Protocol**（`_engine/transport.py`）：`SessionTransport` / `PBTransport`，
  传输层只允许抛 4 类传输异常 → 断网时**全部公开方法**抛的都是 `TdxError` 子类
  （不再有裸 `ConnectionRefusedError` / `binascii.Error`）。
- **ARCH-003 `Client._fetch()` 统一取数管道**：cache → pace → transport+retry/switch → codec →
  建容器 → 盖 Provenance → 日志，全在一个地方；样板收敛后 `timeliness()` 等零覆盖方法清零。

### 修复

- **`decode_any` 解压无上限（DEF-B4-02 / SEC-2，zip bomb）**：`zlib.decompress(blob)` 的输出大小
  完全由回包决定 —— 800 KB 的压缩包可以解出 80 MiB（实测压缩比 103:1）。修法分两层：
  ① **`_engine/zliblimit.py` 单一实现** `inflate_limited()`（`decompressobj().decompress(data, cap+1)`，
  只多要 1 字节判超限，全程内存 ≈ 上限+1；上限 `MAX_DECOMPRESS = 64 MiB`，真实帧解压后仅几十 KB，
  3 个数量级余量）；② **四条通路全部改走它**：`pb_api.decode_any`、`datacenter.unwrap_frames`、
  `datacenter._try_zlib`、`hq_extra.peel`。超限抛 `ProtocolError` 并**显式重抛**（旧写法
  `except Exception` 全吞，正是炸弹变静默的根因）；「首字节 0x78 实则不是 zlib」仍走原有降级探测。
  判据：`tests/test_zlib_limits.py`（逐通路点火 + 一条**静态扫描**：全包除 `zliblimit.py` 外
  再出现 `zlib.decompress` / `decompressobj()` 就红）。
- **`decode_json` 的编码启发式随字符串长度翻转（DEF-012）**：旧打分"凡是汉字 +3"，
  而 UTF-8 汉字按 GBK 解会**变成更多罕用汉字**（`上证指数` → `涓婅瘉鎸囨暟`，6×3 > 4×3），
  于是"短串碰巧对、长串一定错"（一个 JSON 里汉字越多越容易整段变乱码）。现在汉字分级权重：
  **GB2312 常用字 +3 / 罕用字 −1**（6763 字表懒构造）—— 真简体中文几乎全落在表内，乱码大多不在。
  GBK→UTF-8 方向（`元` → 亚美尼亚 `աԪ`）的扣分照旧。判据：`tests/test_decode_encoding.py`（14 例，
  含两个方向 × 9 个真实列名/取值）。
- **`get_quotes` 按裸 code 去重（DEF-014）**：回包 `COMBHQ_COLS` 里**没有 Setcode 列**，
  所以 `got[row[0]] = row` 会让"沪 000001"与"深 000001"互相覆盖 —— 用户要两只完全不同的标的，
  回来的是**同一行出现两次**且不报错。修法：跨市场同码不进批量（批量里无法归属），
  逐个 `(market, code)` 单发并按**对**归档；其余码仍一次批量（行为与旧版一字不差）。
  判据：`tests/test_quotes_dedup_and_varint.py`（两次单发、两行取值必须不同、普通请求仍只发一次包）。
- **`pb.write_varint` 对负数死循环（DEF-047）**：`val >>= 7` 是算术右移，`-1 >> 7 == -1` →
  `while True` **不返回**（一个负字段就能挂死编码线程）。现在按 protobuf 语义把负值编成
  **64 位补码 10 字节**（`-1` → `ff…ff 01`），超 64 位范围抛 `ValueError`；`read_varint` 往返一致。
- **`Table`/`Row`/`Dataset` 的 `repr` 全量 dump（DEF-022）**：5000 行表的 `repr()` 是**单行 42 万字符**。
  现在只给**摘要**（行数/列数/前 3 行/`…(+N more rows)`），`Row` 最多 6 个键值 + 400 字符、
  `Table`/`Dataset` 硬上限 2000 字符；自引用（`row["x"]=row`）不再 `RecursionError`。
  `to_dicts()` / `shape` / `cols` / `to_json()` 语义一个字节未动。
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

### 工程化（批次 E）

- **门禁入仓（ENG-002）**：`tools/gate.py`（原 `_dev/check.py` 在 git 之外，干净 clone 跑不了）；
  现 **10 条 → 11 条**：新增「公开面 docstring 覆盖率」门禁，`SKIP_WHITELIST = {8}`（真机冒烟）。
- **CI（ENG-001）**：`.github/workflows/ci.yml`，Python 3.10–3.14 × {ubuntu, windows}，
  `pytest -m "not online"` + `ruff` + `pip wheel`。
- **测试离线铁律（门禁第 7 条）**：`tests/` 不许出现 `Client(` / `Session(` / `AsyncClient(`
  字面量；白名单逐条给「零建连」证据，并在输出里打印每条放行理由。
- **`except: pass` 20 → 4（ENG-005）**：AST 复扫亲验；15 处补 `logger.debug(exc_info=True)`，
  剩下的收窄保留并在注释里钉死理由。
- **日志骨架（ENG-006）**：`_logging.py`，`NullHandler` 默认零输出；`caplog` 逐类捕获 12 例。
- **版本单源（ENG-007）**：`_version.py`；`pyproject.toml` 走 `dynamic` + `attr`，双路读数必一致。
- **打包（ENG-010）**：`packages` 硬编码 3 名 → `find` + `include`（新增子包不会再静默漏进 wheel）。
- **文档（ENG-011/012/013）**：README 受生成块守护的行数占比 9% → **≥40%**（返回值/单位/复权等表
  改为代码生成 + 逐块比对）；公开面 docstring 覆盖率进 CI；`--doctest-modules` 全绿。

### 安全

- **新增 [`SECURITY.md`](SECURITY.md)（ENG-009）**：把"这个库的安全边界"写成可核对的文件而不是
  散落的注释 —— 明文 7615 / 硬编码 4 台服务器 / 无 TLS 无签名无校验的**信任模型**、敌手模型、
  「保证什么 / 不保证什么」两张表、可执行的自查命令，以及 5 条风险登记：
  SEC-1 握手模板内嵌真机指纹与票据（**只登记不修**，含处置建议）、SEC-2 解压上限（已修，四条通路）、
  SEC-3 节流退避误判、SEC-4 `host=` 的双重语义、SEC-5 `hosts=[...]` 尚不存在（roadmap）。
  红线写在最前面：**本库只保证"字段与协议一致"，不保证"数据是真值"**。

### 当前基线（0.3.0 收尾）

| 判据 | 结果 |
|---|---|
| `python -m pytest -q` | **1509 passed**（0 failed） |
| `python tools\gate.py` | 通过 **10** / 失败 0 / 跳过 1（共 11 条） |
| `python tests\golden_runner.py check` | **PASS**（27 份样本、差异 0） |
| `python -m pytest --doctest-modules tdxdata -q` | 2 passed / 2 skipped（示例块） |

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
