# _superseded/ —— 有意变更后归档的旧样本

**这个目录是"历史证据"，不是"当前基准"。**它存在的唯一理由是：阶段 2/4 会**故意**
做破坏性改动，让某些样本**合法地**不再等于现冻结值。那时候不能靠"把比对逻辑放宽"
来糊（那等于关掉门禁），也不能"悄悄重录、把旧值丢掉"（那等于销毁证据）。
体面的做法是：**旧样本原样搬进来、登记清楚、新样本另录一份**。

## 铁律

1. **原样搬移，一个字节都不改。**`sha256` 必须仍是文件里 `meta.sha256` 记的那个值。
   `tests/test_golden_contract.py` 会逐份重算，改过就红。
2. **归档 ≠ 删除。**文件搬进来后立刻在下面的登记表加一行，写清「为什么」。
   **挪走却不登记**会被 `test_superseded_files_are_registered` 判红 ——
   因为那等于偷偷放水，而放水的门禁比没有门禁更危险。
3. **不参与 `check`。**`golden_runner.py check` 只读 `golden/` **顶层**的 `.json`，
   并会在输出里永远打一行 `已归档样本 N 份（有意变更，见 _superseded/README.md）`。
3b. **还必须改源码表 `EXPECTED_ARCHIVED`**（`tests/golden_runner.py` 与
   `tests/test_golden_contract.py` 各一份，互相校验）。原因：复核 V6c 实测过 ——
   把一份**在役**样本搬进来 + 补一行**虚构理由** + 从两张源码表删掉它，
   `check` 与 `pytest` **双双全绿**。因为「归档」天生就是"让样本合法退出比对"的
   开关，而上面那张登记表的 6 个字段**没有一个可机器判定**。
   现在归档集合由源码双向对账，且每条必须指向一个**真实在役**的替代样本 ——
   于是"搬走一份在役样本"必然留下源码 diff、可审，而不是一行自由文本就能抹掉基准。
4. **帧不搬（有一个例外，见下）。**搬移时 `frames/` 下对应的 `.bin` 留在原地、
   manifest 也照旧登记，所以归档样本仍可被独立复算；想复活就把它搬回顶层，一字节不改即可。
   ⚠️ **例外：同名重录会回收旧帧。**manifest 里一个样本名只挂一套帧，所以
   `record --only <同名>` 会删掉该名下**归档样本的旧帧**、换成新录的帧。
   后果：那份归档样本从此**只剩 payload 自证**（`payload` 的 sha256 仍等于
   `meta.sha256`，`test_superseded_files_are_registered` 照样绿），但**不再可回放**。
   需要保留可回放性就别用同名重录 —— 给新样本换个名字（如 `bars_880005` →
   `breadth_880005`，那 4 份 stage2 就是这么做的，它们的帧原样还在）。
5. **命名**：`_superseded/<原文件名>.<阶段标签>.json`，例如
   `bars_880005.stage2.json`、`boards_industry.stage4.json`。
   `<阶段标签>` 建议直接写 `stage2` / `stage4`，与改动落地的阶段对齐。
6. **同一份样本被二次推翻**时不要覆盖旧档：换标签再增一份
   （`boards_industry.stage4.json`），历史链条要留全，不覆盖 `stage1.5` 那份。

## 登记表

一行一份。`文件` 列写**归档后的完整文件名**（含 `.json`）。
`变更原因` 一句话说清"旧行为 → 新行为"，别写"优化了"这种空话。
`新样本` 写替它站岗的新样本文件名；确实没有替代品（通道被删除）就写 `-`。
`谁拍板` 写决策人（当前是 `lead`）。

| 文件 | 移入时间 | 变更阶段 | 变更原因 | 新样本 | 谁拍板 |
|---|---|---|---|---|---|
| `bars_880005.stage2.json` | 2026-10-27T17:36+08:00 | 2 | 880005 的 OHLC 是四个**家数**而非价格，阶段 2 起 `get_bars` 抛 `NotSupportedError` 并指路 `get_breadth()` | `breadth_880005.json` | Lead |
| `bars_880006.stage2.json` | 2026-10-27T17:36+08:00 | 2 | 880006 的 open/close 是两个**停板家数**而非价格，阶段 2 起 `get_bars` 抛 `NotSupportedError` 并指路 `get_breadth()` | `breadth_880006.json` | Lead |
| `bars_880866.stage2.json` | 2026-10-27T17:36+08:00 | 2 | 880866 的 close 是「近期新低」**指标点位**而非价格，阶段 2 起 `get_bars` 抛 `NotSupportedError` 并指路 `get_breadth()` | `breadth_880866.json` | Lead |
| `err_bars_880005.stage2.json` | 2026-10-27T17:36+08:00 | 2 | 本样本当年录成 `outcome=ok`（`880` 前缀被当成沪市、返回了 5 根"价格"）；阶段 2 起同一调用改为抛 `NotSupportedError`，新样本冻住拒绝现场 | `reject_bars_880005.json` | Lead |
| `boards_industry.stage1.5.json` | 2026-10-27T17:52+08:00 | 1.5 | Table 语义 meta 贯通：`source` 由人类可读字符串改为 5 键 dict（并补 `as_of`/`status`/`fetched_at`/`elapsed`） | `boards_industry_s4.json`（**改名重录**，理由见 stage4 那行） | Lead |
| `market_stat_updown.stage1.5.json` | 2026-10-27T17:52+08:00 | 1.5 | Table 语义 meta 贯通：`source` 由人类可读字符串改为 5 键 dict（并补 `as_of`/`status`/`fetched_at`/`elapsed`） | `market_stat_updown.json`（同名重录） | Lead |
| `market_stat_sentiment.stage1.5.json` | 2026-10-27T17:52+08:00 | 1.5 | Table 语义 meta 贯通：`source` 由人类可读字符串改为 5 键 dict（并补 `as_of`/`status`/`fetched_at`/`elapsed`） | `market_stat_sentiment.json`（同名重录） | Lead |
| `market_stat_industry.stage1.5.json` | 2026-10-27T17:52+08:00 | 1.5 | Table 语义 meta 贯通：`source` 由人类可读字符串改为 5 键 dict（并补 `as_of`/`status`/`fetched_at`/`elapsed`/`params`/`level1_code`） | `market_stat_industry.json`（同名重录） | Lead |
| `err_bars_IF00.stage3.json` | 2026-10-27T18:20+08:00 | 3 | 阶段 3 把「无法识别的标的」从 `ValueError` 改为 `OutOfScope`（期货/期权/国债期货/外汇不在证券表内，措辞显式否掉「服务端不支持市场码」的错归因） | `err_bars_IF00.json`（同名重录） | Lead |

| `boards_industry.stage4.json` | 2026-10-27T18:34+08:00 | 4 | 阶段 4 把 `change_pct` 从恒 `None` 改为 `NOW/CLOSE-1` 实算；industry 支路新增 `PBMultiHQ(market=10001)` 回填一轮（原 `app_bklist_ag` 无价格列），故多出一帧 | `boards_industry_s4.json`（**改名重录**：`record --only` 的 N4a 守卫拒绝同名重录 —— 同名会回收 stage4 自己声明的帧，让归档从此不可回放。改名后 stage1.5 与 stage4 两份归档的帧全部保住） | Lead |

（当前 **10 条**：阶段 2 的 `get_bars` 非价格拒绝 4 条 + 阶段 1.5 的 Table meta 贯通 4 条
+ 阶段 3 的标的归类 1 条 + 阶段 4 的 `change_pct` 实算 1 条。归档时逐份复核过：
文件字节不变、`payload` 仍等于 `meta.sha256`、mtime 仍是**原始录制时刻**。
`boards_industry` 有 `stage1.5` 与 `stage4` 两份，链条留全、后者不覆盖前者。）

## 待办（阶段 4 仍在进行中，**逐条等 Lead 派工**）

- ✅ `boards_industry` —— 阶段 4 已落地并**已归档 `stage4`**（见表）。
- ⏳ `ticks` —— **待定**：阶段 4 删掉了 `Tick.trade_count`，若样本 payload 里少这个键
  就会红，需要归档重录 `stage4`；但 Lead 尚未确认删改是否最终保留，**先不动**。
- ❎ `market_stat_updown` / `market_stat_sentiment` / `market_stat_industry` ——
  主写者实测排除：这三张表没有任何整列全空（`_drop_empty_cols` 无事可做），
  输出逐字节不变，**不需要动**。

每一条都由 Lead 逐条派工、确认新行为正确后再动；那时才
`record --only <名字>` 重录新样本，并把旧的搬进本目录 + 登记。
