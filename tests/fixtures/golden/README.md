# 黄金样本（golden fixtures）

**这份目录是语义层重构（阶段 1~5）的对照基准：改代码前后必须逐字段对得上。**

录制时点：**2026-10-27 收盘后**（`Quote.server_time = 2026-10-27 15:30:03`，
当日已是最终值，不会随盘中抖动）。`git_head = 04463f31e6081e4578feb97c5ccf625887f6f514`，
`tdxdata 0.2.0`，`python 3.14.2`。

```
golden/
├── README.md                 ← 本文件
├── <样本名>.json             ← 现行基准：27 份（阶段 0 的 19 份 + 阶段 2 新录的 8 份）
├── _superseded/              ← 有意变更后**归档**的旧样本（原样搬移，不参与 check）
│   ├── README.md             ← 归档登记表（谁、何时、为什么、新样本是谁）
│   └── <原名>.stage<N>.json
└── frames/
    ├── manifest.json         ← 59 个帧的清单 + 每个样本用了哪几个帧
    └── NNNN_<cmd>_0x<ctl>.bin ← 原始请求参数 + 原始回包字节
```

### 已知缺口：`unwrap_frames` 的多包切分路径**没有被覆盖**

全部 **59 个帧的 `n_blobs` 都是 1**（逐帧实测，含阶段 2 新录的 4 个 `breadth_*` 帧）。
也就是说一条响应被 TCP 拆成多个包、需要 `unwrap_frames()` 拼接的那条路径，
黄金样本**一次都没走到**。这是**已知缺口，没有造假数据去凑**：
硬造一个多 blob 帧等于让回放去验证一个真实通道里不存在的场景。

要补上它，只有两条路：① 等某次真实大响应恰好被拆包（`market_stat industry`
的 31 帧、`finance_zcfz` 都有机会），录到时顺手留档；② 给 `unwrap_frames`
写一条**构造输入**的单元测试（那里不需要真实通道，属 `tests/test_*.py` 的范畴）。

### 已知缺口：**容器 meta 不受黄金样本约束**（书面边界）

独立复核确认：在役样本的 `payload` 里**一份容器 meta 都没有**（都是阶段 1 之前录的），
而容器 meta 走的是**信息性通道**、不进 rc。所以：

1. **容器 meta 不受黄金约束。** 容器级 `clock_note`、`provisional`、`as_of` 这些键
   改坏了，`golden_runner check` 抓不到 —— 它只会在信息行里罗列，不判红。
2. **它们由另外两个测试管：** `tests/test_series_containers.py`（容器结构/继承/info 语义）
   与 `tests/test_timeliness.py`（时钟可信度、`status`/`as_of` 判定、跨机时不信任）。
3. **重录老样本会改变这一点，但按纪律不重录。** 这 15 份是阶段 0 的基准，
   为了"让门禁更好看"去重录它们等于重写基准；缺口留着、写清，比偷偷重录可靠。

> 这是**已知且接受的缺口，不是待修项**。写清比藏起来强 —— 藏起来的缺口会让
> 下一个人以为"黄金样本覆盖了一切"。

同样的记账也写在源码里：`golden_runner.META_KEYS_NOT_COVERED_BY_GOLDEN`
（白名单键里哪几个黄金样本覆盖不到、原因是什么），且有闭集守卫防它被悄悄改。

## 怎么用

```bash
python tests/golden_runner.py check        # 默认离线：用 frames/ 回放，逐字段 diff（0.3 s，零 socket）
python tests/golden_runner.py check --online   # 额外联网做结构级对照（慢，值变化不算失败）
python tests/golden_runner.py record       # 联网全量重录（约 150 s，串行 2.2 s/次）
python tests/golden_runner.py record --only bars   # 只重录名字前缀匹配的（其余样本连帧保留）
```

`check` 的退出码：`0` 一致 / `1` 有差异或硬错误 / `2` 无样本（**空输入绝不静默当 0**）。

## 容器 meta（阶段 1 起，**信息性**）

`get_bars` / `get_quotes` / `get_minutes` / `get_ticks` / `get_market_page` /
`get_universe` 的返回值会变成 `list` **子类**（`Bars` / `Quotes` / `Minutes` / `Ticks`），
元信息挂在实例的 `.meta` 上（`as_of` / `fetched_at` / `status` / `source` / `elapsed`）。
`json.dumps` 只看得到 list 内容、看不见 `.meta`，所以本工具单独取出来存成 payload 的
**同级键 `meta`**：

```jsonc
{"outcome": "ok", "kind": "list[Bar]", "value": [ … ],
 "meta": {"as_of": "2026-10-27", "status": "final", "elapsed": 0.42, "source": "PBFXT"}}
```

三条硬约定：

1. **`kind` 仍按元素类型算**（`Bars` → `list[Bar]`，不写成 `Bars`）。容器类名一改就动
   `kind`，会让阶段 0 冻结的 23 份老样本全部报差异 —— 那是噪声不是信号。
2. 回放时 **整体剔除 `payload["meta"]` 之后才做主 diff**，meta 走单独的信息性对照。
   `status` 依赖服务器时钟、`elapsed`/`fetched_at` 每次都变，拿它们判 FAIL 等于关掉门禁。
   `--online` 的结构比对同样剔除。
3. **本目录现有的 23 份样本都没有 `meta` 键**（阶段 1 之前录的）。缺它不是差异
   （`meta` 在 `ALLOWED_ADDED_KEYS` 里）。check 会打印
   `容器 meta（信息性，不计入失败）：样本无 meta 23 份 / …`，以及逐份的
   `xxx：样本无 meta（本阶段前录制）；回放产出 meta 键 [...]`。
   这些样本**按计划不重录** —— 它们是阶段 2/4 的行为基线，且 sha256 已自证完整。

## 允许新增的键（`ALLOWED_ADDED_KEYS`）

比对时**只放行"新增"**：已有的同名键仍然要比值，删键/改值/改结构一律照旧红。

| 批 | 键 | 说明 |
|---|---|---|
| 原批 | `meta` `as_of` `fetched_at` `status` `source` `elapsed` `provisional` `data_date` | 时效与来源元信息 |
| 2026-10-27 Lead 批准 | `as_of_note` `clock_note` `fetch_clock` `status_note` | **来源凭据类**（时钟来源与降级说明），不含数据信号 |

为什么批这 4 个：契约要求 `fetched_at` 必须是**服务器**时刻而不是本机时钟，那库就
**必须**能披露"我什么时候退回了本机时钟、为什么降级"，所以 `fetch_clock` 进
`Table.meta` 是契约需要。不放行则每份返回 Table/Dataset 的样本永久红，等于把门禁
训练成"红习惯了"。

两条硬约束：

1. **只认精确键名**，不许任何通配/前缀写法（不许 `*_note`）。由
   `test_source_credential_keys_are_unit_tested` 守。
2. **白名单是这套门禁唯一会被滥用的方向**：这 4 个键的语义 **goldens 原理上兜不住**
   —— 离线回放里 `fetch_clock` 恒为 `local`、`clock_note` 恒为
   `local-clock-fallback`，于是"服务器时钟探针永久失效"这种退化在回放里与正常态
   完全同形、永远不红。所以规定：**凡被放行的来源凭据键，键名必须出现在某个
   `tests/test_*.py` 的源码里**（预期是 `tests/test_timeliness.py`），
   由上面那条静态测试强制。

## 样本 JSON 格式

```jsonc
{
  "meta": {
    "name": "bars_day_300",          // 样本名（与文件名一致）
    "cmd": "HQServ:PBFXT",           // 产生该样本的**首个** wire 命令
    "ctl": 14,                       // 该帧的 ctl 字节；-1 = 该样本没有产生 wire frame
    "api": "Client.get_bars(600519, day, count=300)",  // 人读的调用描述
    "params": {"symbol": "600519", "period": "day", "count": 300, "adjust": "NONE"},
    "fetched_at": "2026-10-27T16:52:11+08:00",
    "server_time": "2026-10-27 15:30:03",   // 服务端时间（取自 Quote.server_time）
    "sha256": "…",                   // 对 payload 规范化 JSON 的 sha256（见下）
    "base_dir": "…\\tests\\fixtures\\golden",
    "python": "3.14.2",
    "tdxdata_version": "0.2.0",
    "git_head": "04463f3…",
    "wire_calls": [{"cmd": "HQServ:PBFXT", "ctl": 14, "n": 1}],  // 本次实际打了哪些命令
    "frames": [2],                   // 本样本用到的帧序号；抓不到时是 "unavailable: <原因>"
    "ctl_note": "…"
  },
  "payload": {
    "outcome": "ok",                 // ok | error
    "kind": "list[Bar]",             // list[Bar] / list[Quote] / list[MinutePoint] /
                                     // list[Tick] / Table / Dataset
    "value": [ … ]
  }
}
```

* `sha256` 算的是
  `sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode('utf-8'))`。
  **手改 payload 而不改 sha256，会被 `tests/test_golden_contract.py` 当场抓住。**
* dataclass（`Bar`/`MinutePoint`/`Quote`/`Tick`）用 `dataclasses.asdict()` 展开；
  `Table` 存 `{cols, rows, cmd, domain, meta}`（`Row` → 普通 dict）；
  `Dataset` 存 `{name, params, table:{…}}`。
* **浮点保留原值，不 round**（`672414367744.0` 就是它本身）。
* 所有文件 **UTF-8 无 BOM**。

### 「错误现场」样本

`payload` 变成：

```jsonc
{"outcome": "error",
 "exc_type": "tdxdata.errors.EmptyResult",   // 异常类型全名
 "exc_msg": "000300 day K线无数据",            // str(exc) 原文，一个字符没改
 "exc_bases": ["tdxdata.errors.TdxError", "builtins.Exception"]}
```

所以「以前抛 `EmptyResult`、改完变成静默返回空表」这类退化，在 diff 里会表现成
`payload.outcome 值变化 "error" → "ok"`。

## 帧文件格式（`frames/NNNN_<cmd>_0x<ctl>.bin`）

自定义二进制，读写只发生在 `tests/golden_runner.py`（`write_frame_file` / `read_frame_file`）：

| 偏移 | 长度 | 内容 |
|---|---|---|
| 0 | 6 B | 魔数 `b"TDXGF1"` |
| 6 | 4 B | `meta_len`（u32 小端） |
| 10 | `meta_len` | UTF-8 JSON：`{seq, sample, transport, cmd, ctl, args_len, raw_len, resp_len}` |
| … | 4 B | `args_len`（u32 小端） |
| … | `args_len` | **请求参数原字节** |
| … | 4 B | `raw_len`（u32 小端） |
| … | `raw_len` | **响应原始字节（`dc.unwrap` 之前）**；`ask` 侧恒为 0 |
| … | 4 B | `n_blobs`（u32 小端） |
| … | n ×(4 B + len) | 切包后的各包体 |

回放用的是 `dc.unwrap(raw) or dc.unwrap_frames(raw)` —— **解包层本身也在被校验**，
不只是它下游的 protobuf/JSON 解析。`raw_len = 0` 时才退化为直接返回 `blobs`。

帧里**不含任何时间戳/随机量**，所以同一批帧回放多少次结果都一样。

## 拦截点（只在本脚本内 monkeypatch，**绝不改库**）

| transport | 真实函数 | 覆盖接口 |
|---|---|---|
| `send` | `tdxdata._engine.datacenter._send(frame, ...)` | PB 路径：`PBFXT` / `PBZST` / `PBMultiHQ` / `PBTickEx` / `PBCombHQ` / `PBHYStat` … |
| `ask` | `tdxdata._engine.session.Session.ask(ctl, cmd, args)` | 数据中心 JSON 路径：`CWServ.*` / `JJSServ.*` |

录制时是**包裹**（先调原函数再记账，真实限流/重试/换机照原样走）；
回放时是**替换**（`dc._send` 查帧表、`Client._ensure` 返回假 Session、`time.sleep` 抹掉、
Client 的 pacer 换成零等待桩）—— 一条 socket 都不建。

查表键 = `(transport, cmd, ctl, sha256(args))`。**所以重构时一旦动了请求体（参数/列序/大小），
回放会直接报「帧缺失」而不是给出一个假的"0 差异"**；这是刻意的（阶段 1~5 需要知道线上字节变了）。

## `frames/manifest.json`

```jsonc
{
  "format": "TDXGF1",
  "generated_at": "…", "server_time": "…", "git_head": "…",
  "python": "3.14.2", "tdxdata_version": "0.2.0", "base_dir": "…",
  "recorded_seconds": 149.0,
  "count": 55,
  "entries": [{"seq": 1, "sample": "quotes_indices", "file": "0001_HQServ_PBCombHQ_0x11.bin",
               "transport": "send", "cmd": "HQServ:PBCombHQ", "ctl": 17,
               "args_len": 50, "args_sha256": "…", "resp_len": 2340, "resp_sha256": "…",
               "raw_len": 2455}],
  "samples": [{"name": "…", "file": "…json", "outcome": "ok", "frames": [1]}],
  "duplicate_keys": [15, 55],
  "conflicts": [],
  "note": "同一 (transport, cmd, ctl, args) 被请求多次时只保留首次用于回放；…"
}
```

* `duplicate_keys`：同一请求键出现了多次（回放只用首次）。本批有 2 处，都是**同内容重复**：
  - `seq 15` —— `market_stat_sentiment` 也先打了一次 `sys_fn_jyr_v3`（拿当日交易日），
    与 `market_stat_updown` 的 `seq 13` 是同一个请求；
  - `seq 55` —— 见下面「880005」那条。
* `conflicts`：重复键但两次回包**字节不同**（本批 0 个）。有冲突就说明服务端给过两个不同答案，
  回放只认首次，必须人工看一眼。

## 这批样本的已知情况（如实记，不粉饰）

1. **`err_bars_880005` 实测不报错。** 任务清单把 `880005` 归进「错误现场」，但与
   `bars_880005` 是**同一个请求**（`get_bars("880005", period="day", count=5)`），
   `880` 前缀在 `symbols._PREFIX3` 里映射到沪市，服务端正常返回 5 根日线。
   所以这份样本被如实记成 `outcome: "ok"`（因此才有 `seq 55` 这处重复键）。
   若阶段 2/4 想让 `880005` 变成错误，那是**行为变更**，这份样本正是它该对比的基线。
2. **`err_bars_IF00` 没有 wire frame**：`symbols.parse("IF00")` 在建帧之前就抛
   `ValueError`，`meta.frames` 记成 `"unavailable: 该调用在发出任何 wire 请求之前就失败了
   （builtins.ValueError），没有可回放的帧"`。回放时它照样能比（不需要帧就能复现同一个
   `ValueError`），所以本批 `跳过 0 份`。
3. **`err_bars_000300` / `err_bars_932000`** 都是 `EmptyResult`：服务端正常回包但 0 行。
   `000300` 是沪深300指数，必须写 `sh000300` 才有数据 —— 不带前缀会被
   `symbols.market_by_code` 判成深市（`_HEAD1["0"] = 0`），于是"K线无数据"。
4. **`market_stat_industry` 很慢**：实测 **88.0 s / 31 个帧**（1 次拿当日交易日
   + 1 次取一级行业目录 + 29 个一级行业各 1 次明细），439 行 × 8 列。
   `--online` 模式默认跳过它。
5. **`ticks` 是 2 个帧**（每页 100 笔，`count=200` 自动翻页），锚点写在请求参数里。
6. 全部 23 份都是 **单次串行** 录的（`pacer` 基准间隔 2.2 s），全程 149 s，未触发限流退避。

## 顺带发现（不在本目录职责内，未改，只上报）

`tdxdata/_engine/pb.py` 第 25 行在 **import 时**无条件执行 `sys.stdout.reconfigure(encoding="utf-8")`：

* import 副作用会改调用方的 stdout 编码；
* 任何把 `sys.stdout` 换成没有 `reconfigure` 的对象（`io.StringIO`、某些日志重定向、
  `contextlib.redirect_stdout`）的代码，**只要在 `tdxdata._engine.pb_api` 首次 import 之后
  碰一次 PB 接口就会 `AttributeError`**（本次写自测脚本时实际踩到）。
  建议改成 `if hasattr(sys.stdout, "reconfigure"): …` 包一层 —— 但那属于 `tdxdata/` 域，
  本任务的写权限只在 `tests/` 这三处。
