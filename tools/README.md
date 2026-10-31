# tools/ —— 质量门禁与生成工具

本目录有三类东西：

* `gate.py`：**11 条质量门禁**的唯一入仓实现（`python tools/gate.py`）；
* `catalog/`：第 4/5 条门禁的证据源（接口目录的留档副本）；
* `verify_pending.py`：把「只能真机才能定」的几类遗留项列成清单的核对工具
  （**默认 dry-run 不联网**，加 `--run` 才真连；只报告、不改任何数据文件）。

门禁的判据一律**带基数**（"421/421 个产品"这种），不许写"全部通过"——
一个不带基数的绿，等于一句无法被复核的话。

```
python tools\gate.py                 # 离线全跑（第 8 条真机冒烟 SKIP）
python tools\gate.py --online        # 连跑 smoke.py（需真机 2~4 分钟）
python tools\gate.py --only 7,11     # 只跑指定编号
python tools\gate.py --list          # 只列 11 条判据
```

## 一、门禁清单

| 编号 | 名称 | 判据（一句话） | 默认 |
|---|---|---|---|
| 1 | 语法 | 递归所有 `.py`（排除 `__pycache__`/`build`/`*egg-info`）`ast.parse` 全过 | 跑 |
| 2 | 导入与导出 | `import tdxdata` 拿到本目录的包；`__all__` 每项可 `getattr`；`__version__` 存在 | 跑 |
| 3 | 产品 | `products()` 长度 == 421；产品名 / JSON 键无重复 | 跑 |
| 4 | cmd 台账 | 所有产品 `cmd` 能在接口目录里命中（认 `CWServ.` / `HQServ.` / `JNLPSE:`） | 跑 |
| 5 | ctl 一致性 | 产品 `ctl` 与目录 `ctl` 一致；核不到的如实报「可核对/总数」 | 跑 |
| 6 | 编码 | 所有 `.py` 是 UTF-8 且无 BOM，strict 解码不报错 | 跑 |
| 7 | 单元测试（离线） | `pytest -m "not online"` 全绿；`test_*.py` 不得出现 `Client(` / `Session(` / `AsyncClient(` 真调用 | 跑 |
| 8 | 真机冒烟 | `python smoke.py` 退出码 0 且输出含「失败 0 项」 | **SKIP**（显式白名单，需真机） |
| 9 | 空实现必须抛错 | 无通道方法源码体必须 `raise NotSupportedError`；`return []` / `Table(rows=[])` 文本级检出 | 跑 |
| 10 | 打包完整性 | `pip install --target %TEMP%` 后子进程 import：`__file__` 在临时目录、products 421、子包可导入 | 跑 |
| 11 | 公开面 docstring | `tdxdata.__all__` 的模块/类/函数 + `Client` 公开成员，带 docstring 的比例 **≥ 基线 100%（78/78）** | 跑 |

汇总行基线：`通过 10 / 失败 0 / 跳过 1`（共 11 条；第 8 条 SKIP 不计失败）。

## 二、判据细则（口径写在这里，代码与本文必须一致）

### 二.1 SKIP 只有一处

`SKIP_WHITELIST = {8}`。**任何**门禁返回 SKIP 而编号不在白名单里，`run_gate()`
一律改判 FAIL —— 目的是堵死"这条今天不想管，就当跳过"这条路。
门禁自身抛异常也算 FAIL，不许静默吞。

### 二.2 第 7 条的离线扫描与白名单

扫的是**去掉字符串与注释之后**的代码（`mask_strings_and_comments`），
所以 `print("... Client() ...")` 这种说明文字不会误报，`S.Session()` 这种真调用必须报。

白名单键是 **`(相对路径, 命中符号)`**，必须精确到"文件 + 符号"：

* 禁止 `"*"`、禁止只写文件名（那等于关掉整个文件的扫描）；
* 每条放行都在输出里逐条打印 `⚠️ 白名单放行 文件:行号 ← 理由` 并单独计数；
* 新增一条白名单，必须给出"零建连"的**同类证据**（打桩方式 / 已核人 / 日期）。

### 二.3 第 9 条的空值判据

`return []` 与 `Table(..., rows=[])` 是**检出**，不是一律 FAIL：

* `block`（必须 FAIL）：`get_*` 这类对外取数入口，以及登记过的无通道方法 ——
  用空值冒充不可用是红线；
* `review`（交人工判据）：私有 helper / 解析器 / 序列计算，空输入返回空序列是正确语义。

### 二.4 第 11 条：公开面 docstring 覆盖率（ENG-011）

**判据**：`tdxdata` 公开面带 docstring 的比例 **= 100%**（ENG-011 执行当天实测 78/78；
之后的实测数见 gate 11 那一行本身）。
一个都不能少 —— 少一个就 FAIL，并逐个点名是谁。

⚠️ 判据是**比例**，不是"至少 78 个"：公开面会随功能增长（`__all__` 变大、
`Client` 加方法），分子分母一起长，唯有 100% 这个口径不会随着时间失效。

**怎么数**（`gate.py:docstring_subjects()`，改这里必须同时改本文）：

1. `tdxdata/__init__.py` 的 `__all__` **每一项**都算一个主体：
   * 模块（`invariants`）/ 类（`Bar` / `Client` / `TdxError` …）/ 函数
     （`parse_symbol` / `as_markdown_table` …）→ **必须**有 docstring；
   * **纯值**（`__version__` 这类 `str`、`NON_PRICE_SERIES` / `CAPABILITIES` /
     `INSTRUMENTS` 这类 `dict`）没有 docstring 这个概念 → **不可数**，
     但输出里逐个点名（ENG-011 当天 4 个）。不静默丢的原因很直白：静默丢就等于允许
     "把一个类换成同名常量"来绕过门禁。
2. `Client` 的**公开成员**（名字不以 `_` 开头，ENG-011 当天 34 个，含 `property`
   —— property 取 `fget` 的 docstring）→ **必须**有 docstring。
   `AsyncClient` 只数它自己的**类** docstring；它和 `Client` 的方法面一致性
   由 `tests/test_aio_parity.py` 守，不在这里重复数（否则一个镜像方法漏写
   docstring 会算两次账，反而看不清是哪一侧欠的）。

**为什么阈值是 100%（而不是 90% 或"不低于今天"）**

* 这是**先量基线再定阈值**的结果：执行 ENG-011 当天实测就是 78/78 —— 不是拍的；
* 公开面是**契约面**：用户从 `import tdxdata` 看到的一切都在这 78 个里。
  一个没有 docstring 的公开方法，等于一句"你自己猜参数是什么意思"；
* 阈值放到 95% 之类，第一个新增的公开方法就可以合法地不带 docstring ——
  那不是"留余量"，那是把欠债记成默认允许的欠账。**宁紧不松**。
* 反过来说：这条红了**不是**"把阈值调低"的信号，而是"补 docstring"的信号。

## 三、README 生成块（ENG-012）

README 里由代码派生的表分两处：老的 4 个块（`models.Tick.as_markdown_table()`
/ `series_kinds.as_markdown_table()` / `capabilities.*`）与新的 20 个块
（`tdxdata/_docgen.py` 的纯函数，共 24 个块）。守护关系：

| 守什么 | 谁守 |
|---|---|
| 块内容 == 生成函数输出（逐字） | `tests/test_readme_drift.py`（`BLOCKS` 就是登记表） |
| 生成器稳定 / 行尾无 `\r` / 表格列数一致 | `tests/test_docgen.py` |
| 块内行数 / README 总行数 ≥ 40% | `tests/test_docgen.py::test_readme_block_coverage_at_least_40_percent` |
| README 是 CRLF 且无 BOM | `tests/test_readme_drift.py`（两条专门的行尾/BOM 守护） |

**改表的正确姿势**：改代码里的事实源（`enums` / `models` / `limits` / `_core` /
`domains` / `symbols` / `timeliness` / `capabilities` / `data/products.json` …）
→ 用 `tdxdata/_docgen.py` 里的函数重新渲染对应块 → 跑 `pytest -q` 与
`python tools\gate.py`。

**不要手改 README 的表**：`test_readme_drift.py` 会逐字比对。
**不要用 PowerShell `-replace` / `@()` 管道批量改 README**：本项目有过一次
3 文件字符级污染事故；改写 README 只许用 Python 脚本，`open(..., newline="")`
读写、逐块替换，写完必须复核 `CRLF == LF` 且前 3 字节不是 `EF BB BF`。

## 四、联网核对工具 `verify_pending.py`

离线能定的都定完之后，剩下几类只有真机能答（`call` 形态到底要不要 `CallName`、
`not_in_app ∧ rows>0` 的 47 条冲突、`dead` 档是否已过期）。本工具把它们变成一条命令：

```
python tools\verify_pending.py           # dry-run：只打印清单与条数，**不联网**，退出码 0
python tools\verify_pending.py --run     # 真连：逐条发一次，打印 命令|类别|行数|判定
python tools\verify_pending.py --run --only A --limit 3 --json out.json
```

判据口径：A 类同一条命令发两遍（带 `CallName` / 不带）比行数；B/C 类看还能不能出行。
**只报告不改数据**：跑完只打印「下一步该改哪个文件的哪一行」，`--json` 默认落 `%TEMP%`、
写进仓库会被拒绝。B 类的素材（`raw/catalog/not_in_app.json`）不随包分发，缺了会跳过并提示
`--conflicts PATH`。
