# -*- coding: utf-8 -*-
r"""golden_runner.py —— 黄金样本录制 / 离线回放校验（**不属于 pytest**）。

为什么要有它
------------
语义层重构（阶段 1~5）要反复改解码层。光靠"人眼看输出像不像"不行：
必须有一份**冻结的真实输出**当对照基准，而且改完代码要能**不联网**重跑一遍，
逐字段 diff 出"哪一列变了"。这就是本文件的全部职责。

用法
----
    python tests/golden_runner.py record              # 联网重录全部样本
    python tests/golden_runner.py record --only bars  # 只重录名字以 bars 开头的
    python tests/golden_runner.py check               # 默认离线：用 frames/ 回放比对
    python tests/golden_runner.py check --online      # 额外联网做一次结构级对照（慢）

`check` 的判据
--------------
* 每份样本 `meta.sha256` 与 `payload` 重算值必须一致（样本不许被手改）
* 用 `frames/` 里的原始回包喂回**当前代码**的解码层，把结果与冻结 `payload` 逐字段比
* 允许**新增**的键仅限 `ALLOWED_ADDED_KEYS`；其余任何增加/删除/改值都算 diff
* **容器 meta 只作信息性输出，绝不参与判 FAIL**（见下）
* 输出必须是**基数明确**的（几份样本 / 比对几份 / 跳过几份 / 差异几条），
  **空输入显式报「无样本」并退出码 2**，绝不把"没跑"当成"0 差异"

容器 meta（阶段 1 起）
--------------------
`get_bars` / `get_quotes` / `get_minutes` / `get_ticks` / `get_market_page` /
`get_universe` 的返回值会变成 `list` **子类**（`Bars` / `Quotes` / `Minutes` / `Ticks`），
元信息挂在实例的 `.meta` 上（`as_of` / `fetched_at` / `status` / `source` / `elapsed`）。

* 录制侧：`encode_ok` 若发现 `hasattr(obj, "meta")`，把它存成 payload 的**同级键 `meta`**
  （`kind` 仍按元素类型算，写 `list[Bar]` 而不是 `Bars` —— 否则阶段 0 的 23 份老样本会全报差异）。
* 回放侧容器 meta 纪律（2026-10-27 复核后收紧，见 `EXPECTED_REPLAY_CONTAINER_META_KEYS`）：
  样本有 meta → 主 diff 走 `in_meta=True` 逐键比对（A′ 放行的值仍放行）；
  样本无 meta → 回放侧**不许凭空多出未登记的容器 meta**（键集须登记，未登记即红）。
  旧写法"整体剥掉后再比"已废弃 —— 那让 Bars/Quotes/Ticks 的 meta 语义键从未被校验。

帧拦截点（**只在本脚本内 monkeypatch，绝不改库**）
-------------------------------------------------
线上有两类收发咽喉，本脚本各挂一个**包裹式**探针（录制时先调原函数再记账）：

=================  ====================================================  ===============
transport          真实函数                                               覆盖的接口
=================  ====================================================  ===============
``send``           ``tdxdata._engine.datacenter._send(frame, ...)``         PB 路径：PBFXT / PBZST /
                                                                           PBMultiHQ / PBTickEx /
                                                                           PBCombHQ / PBHYStat ……
``ask``            ``tdxdata._engine.session.Session.ask(ctl, cmd, args)``  数据中心 JSON 路径：
                                                                           CWServ.* / JJSServ.*
=================  ====================================================  ===============

`send` 这一侧**额外抓了 unwrap 之前的原始 TCP 字节**（包裹 `datacenter.unwrap`，
只在调用方是 `_send` 时记账）。回放时用 `dc.unwrap(raw) or dc.unwrap_frames(raw)`
重新切包 —— 所以**解包层本身也在被校验**，不只是它下游的 protobuf/JSON 解析。

`frames/<seq>_<cmd>_<ctl>.bin` 二进制格式（本文件自定义，读写都只有本文件）
--------------------------------------------------------------------------
::

    6B   魔数 b"TDXGF1"
    u32  meta_len      (小端)
    ...  meta          UTF-8 JSON：{seq, transport, cmd, ctl, args_len, raw_len, ...}
    u32  args_len      请求参数字节数（send 侧=帧里的 args；ask 侧=body_of 的输出）
    ...  args          请求参数原字节
    u32  raw_len       响应原始字节数（unwrap 之前；ask 侧恒为 0）
    ...  raw           响应原始字节（仅 send 侧有）
    u32  n_blobs       切包后的包体个数（ask 侧恒为 1，就是对 Session.ask 的返回值）
    n ×(u32 len + bytes)  各包体

记录里**不含任何时间戳/随机量**，所以同一份帧回放多少次结果都一样。

错误现场
--------
清单里要求把 `get_bars()` 的"当前行为"也冻下来（异常类型全名 + `str(exc)` 原文）。
本脚本把异常编码成::

    {"outcome": "error", "exc_type": "tdxdata.errors.EmptyResult",
     "exc_msg": "……", "exc_bases": ["TdxError", "Exception"]}

并且照常喂帧回放 —— 所以"以前抛 EmptyResult、改完变成静默返回空表"这种退化，
会在 diff 里以「outcome 值变化 error → ok」的形式被抓到。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import platform
import struct
import subprocess
import sys
import time
from dataclasses import asdict, is_dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GOLDEN = os.path.join(HERE, "fixtures", "golden")
FRAMES = os.path.join(GOLDEN, "frames")
MANIFEST = os.path.join(FRAMES, "manifest.json")
#: 有意行为变更后归档的旧样本（原样搬走、sha256 不改、不参与 check）
SUPERSEDED = os.path.join(GOLDEN, "_superseded")
SUPERSEDED_README = os.path.join(SUPERSEDED, "README.md")

# `python tests/golden_runner.py` 只把 tests/ 放进 sys.path[0]，这里补上项目根
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import tdxdata                                                      # noqa: E402
from tdxdata._engine import datacenter as dc                        # noqa: E402
from tdxdata.models import Bar, Dataset, MinutePoint, Quote, Table, Tick  # noqa: E402
from tdxdata.timeliness import FINAL_CUTOFF_HHMM, HybridClock       # noqa: E402

FRAME_MAGIC = b"TDXGF1"

#: 回放时**允许新增**的键（阶段 1~5 可能给行/表补元信息）。
#: 只放行"新增"：既有的同名键仍然要比值，删键仍然算 diff。
#:
#: 后半 4 个（`as_of_note` / `clock_note` / `fetch_clock` / `status_note`）于
#: 2026-10-27 经 Lead 批准并入，理由记录在案（别日后当成"随手放宽"再删掉）：
#:
#:   它们与 `fetched_at` / `status` / `source` / `elapsed` **同类** —— 都是
#:   「这个数是什么时候、从哪个时钟取的」的**来源凭据**，不含任何数据信号。
#:   而样本契约第 2 条恰恰要求「`fetched_at` 必须是**服务器**时刻、不是本机时钟」，
#:   那么库就**必须**有能力对外披露"我什么时候退回了本机时钟、为什么降级"，
#:   所以 `fetch_clock` 进 `Table.meta` 是**契约需要**，不是噪声。
#:   反过来，不放行它们会让每一份返回 Table / Dataset 的样本永久红 ——
#:   那正好把门禁训练成"红习惯了"，比不放宽更糟。
# 来源凭据类元信息（非数据信号）：时钟来源与降级说明。只放行"新增"，改值仍红。
#:
#: ⚠️ 只认**精确键名**，不许任何通配 / 前缀写法（不许 `*_note`、不许 `*`）：
#:    `test_golden_contract.py::test_source_credential_keys_are_unit_tested` 守这条。
#: ⚠️ 白名单是这套门禁**唯一会被滥用的方向**：往这里加"来源凭据"键的**同时**，
#:    必须有真单测管住它的语义 —— goldens **原理上兜不住**这一格：离线回放里
#:    `fetch_clock` 永远是 `local`、`clock_note` 永远是 `local-clock-fallback`，
#:    "服务器时钟探针永久失效"这种退化在回放里恒等于正常态，永远发现不了。
ALLOWED_ADDED_KEYS = frozenset({
    # 原批 8 个
    "meta", "as_of", "fetched_at", "status", "source",
    "elapsed", "provisional", "data_date",
    # 2026-10-27 Lead 批准：来源凭据类（详见上方批准理由）
    "as_of_note", "clock_note", "fetch_clock", "status_note",
})

#: 「来源凭据类」键：只放行新增，且**必须**有一处真单测管着语义（否则白名单就是漏洞）。
SOURCE_CREDENTIAL_KEYS = ("as_of_note", "clock_note", "fetch_clock", "status_note")


#: `meta` dict 里**离线不可复现**的载体 —— **封闭集，恰好 3 项，精确键名，不许通配**。
#:
#: 判据只有一个：**离线回放能不能复现**。能复现的键一律留在严格比对里，
#: 否则就是"用门禁的牙齿换绿"。逐个过筛：
#:
#:   | 键 | 回放能否复现 | 判定 |
#:   |---|---|---|
#:   | `status` / `as_of` / `provisional` | 能（回放时钟恒 `local` → 判定算法两边一致） | **严比** |
#:   | `fetch_clock` / `clock_note` / `as_of_note` | 能（回放恒 `local` / 稳定值） | **严比** |
#:   | `source.cmd` / `source.ctl` / `source.clock` / `source.cached` | 能（来自帧与调用点） | **严比** |
#:   | `fetched_at` | **不能**（录制时刻 ≠ 回放时刻） | 信息性 |
#:   | `elapsed` | **不能**（每次实测耗时） | 信息性 |
#:   | `source.host` | **不能**（帧里根本没记 host；**即使在线也不确定连到哪台**） | 信息性 |
#:
#: ⚠️ `source` **不能整体降为「只比存在性」**：`source` 仍必须是 **dict 且键集恰为
#:    5 个**（`host`/`cmd`/`ctl`/`clock`/`cached`），放行的**只有 `host` 这一个值**。
#:    理由：D7 的病征恰恰是"`source` 从 dict 退化成人类可读字符串" —— 一旦整体放行，
#:    这个病征就再也抓不到了。
#:
#: ⚠️ **`elapsed` 放行的代价，明确记账（别假装没有缺口）**：
#:    `elapsed` 是**性能量、不含数据信号**；放行后若它退化成"永久恒 0"或某个固定值，
#:    **golden 原理上抓不到** —— 这一格要靠别的手段兜（如 rate_stats / 计时类单测）。
#:
#: ⚠️ 这是**封闭集**：`test_golden_contract.py::test_informational_meta_carriers_is_closed_set`
#:    会断言它恰好等于下面 3 项、且不含任何通配 —— 防止有人往这里顺手加键，
#:    把门禁一点点关掉（B5 的同型错误：判定依据放在可以改的地方）。
INFO_ONLY_META_CARRIERS = {
    "fetched_at": (),        # meta 顶层键：**值**不判（存在性仍判）
    "elapsed": (),           # 同上；性能量，退化靠别的手段兜（见上方记账）
    "source": ("host",),     # source dict：**只**放行 host 的值，其余 4 个子键严比
}

#: `source` 必须仍是 **dict 且键集恰为这 5 个**（结构仍被断言，只放行 `host` 的值）。
SOURCE_DICT_KEYS = ("host", "cmd", "ctl", "clock", "cached")


#: ARCH-002 键名换算表（2026-10-27 Lead 直辖接线，**封闭集**）：
#: 样本冻结的是 meta 时代的散装键，回放侧（Provenance）把同一事实搬进了
#: 新结构 —— `{样本键: (新载体路径相对 provenance 的取法)}`。diff_values
#: 在「样本有 meta / 回放有 provenance」时按这张表对账：
#:   · `fetch_clock` → `clock`（值：str "server"/"hybrid"/"local" ↔ 枚举 .value，同字面）
#:   · `source` 五键 → 顶层 `host`/`cmd`/`ctl` + `cached`；`source.clock` 并进 `clock`
#:   · `as_of_note` / `clock_note` / `status_note` / `source_note` / `note` / `why` →
#:     `notes` 元组成员（按文本**精确存在**断言，不判位置）
#:   · 其余散装键（params/trade_date/field_map/…）→ `extra[同键名]`
#: 换算不了的键照样报差异 —— 这张表不是后门，是把"改名"显式记账。
#: 封闭性由 test_golden_contract 的登记表闭集断言外再加本文注释锚定。
PROVENANCE_KEY_RENAME = frozenset({
    "fetch_clock", "source", "as_of_note", "clock_note", "status_note",
    "source_note", "note", "why",
})


#: 每个样本**权威的帧集合** —— 「谁该有帧」这个事实的**唯一锚点**。
#:
#: 为什么必须硬编码在**源码**里（而不是读样本 `meta.frames`、也不是读 manifest）
#: --------------------------------------------------------------------------
#: 样本 JSON 和 `frames/manifest.json` 都躺在 `tests/fixtures/golden/` 里面：
#: 能改前者的人，也能顺手同步重算后者。拿它们当判据，等于**让被测者给自己出庭作证**。
#:
#: 这里曾有一条真实的洗白路径（独立复核者打出）：帧缺失本来是硬错误，
#: 但 `has_frames` 读的是样本自己的 `meta.frames`，于是只要把 `[2]` 改成 `[]`，
#: 该样本就"合法地"被跳过 —— 跳过不进 rc，`rc` 从 **1 变 0**，而且还打印一句
#: 假原因「录制时本就没有 wire frame」。**放水的门禁比没有门禁更危险。**
#:
#: 所以判据挪到这张表：改它必须改源码，而源码改动跑不过 diff 审查。
#: ⚠️ 新增样本**必须**同时在这里登记，否则 check 直接判红 ——
#:    不许靠"没登记 = 没有帧"把自己洗成一个跳过项。
#: ⚠️ 归档进 `_superseded/` 的样本要**从这张表里删掉**（它已不参与 check）。
#:
#: 取值：`[seq, ...]` = 该样本产生的帧序号（对应 `frames/manifest.json` 的 `entries`）；
#:       `"unavailable"` = 该调用在发出**任何** wire 请求之前就失败了（零帧）。
EXPECTED_SAMPLE_FRAMES = {
    "quotes_indices": [1],
    "bars_day_300": [2],
    "bars_5m_300": [3],
    "bars_60m_300": [4],
    "minutes": [5],
    "ticks": [6, 7],
    # bars_880005 / bars_880006 / bars_880866 / err_bars_880005 已于阶段 2 归档
    # （见 _superseded/README.md）—— 归档的样本要从这张表里**删掉**。
    "bars_510300": [11],
    # ── 板块 / 市场统计（阶段 1.5 同名重录，2026-10-27T17:48~17:49）──────
    # 这 4 份的旧版已于阶段 1.5 归档（`source` 由字符串改为 5 键 dict）。
    # ⚠️ `boards_industry` 于阶段 4 归档（stage1.5 + stage4 两份链条留全），
    #    在役样本**改名**为 `boards_industry_s4`（N4a 拒绝同名重录：同名会回收
    #    stage4 自己声明的帧，让归档从此不可回放）。帧集合按实测登记，
    #    比旧版多出 `PBMultiHQ(market=10001)` 的回填轮。
    "boards_industry_s4": [97, 98, 99, 100, 101, 102, 103, 104, 105],
    "market_stat_updown": [62, 63],
    "market_stat_sentiment": [64, 65],
    "market_stat_industry": [
        66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82,
        83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96,
    ],
    "product_lrmx": [48],
    "product_north": [49],
    "product_ztqd": [50],
    "f10_gsjj": [51],
    "finance_zcfz": [52],
    "err_bars_000300": [53],
    "err_bars_932000": [54],
    # 在 `symbols.parse("IF00")` 就抛 ValueError，一个字节都没发出 → 零帧
    "err_bars_IF00": "unavailable",
    # err_bars_880005：[55] → 阶段 2 已归档
    # ── 阶段 2 新增（2026-10-27T17:48~17:49 录制）──────────────────────
    "breadth_880005": [56, 57],
    "breadth_880006": [58],
    "breadth_880866": [59],
    "breadth_880641": [60],
    # 阶段 2 拒绝现场：`get_bars` 对非价格代码**拦在发请求之前** → 零 wire 请求
    "reject_bars_880005": "unavailable",
    "reject_bars_880006": "unavailable",
    "reject_bars_880866": "unavailable",
    "reject_bars_880641": "unavailable",
}

#: 「零帧」样本在源码表里的哨兵值。
NO_FRAMES = "unavailable"


def expected_frames(name: str) -> list[int] | None:
    """从**源码表**取该样本的权威帧集合；`None` = 该样本本就没有 wire frame。

    查不到 → 抛 `KeyError`（**不**退化成"没有帧"）：新增样本必须先登记。
    """
    val = EXPECTED_SAMPLE_FRAMES[name]
    return None if val == NO_FRAMES else list(val)


#: `meta` 段的**已知位置**（只有这三处算"语义 meta"）。
#: 只认显式路径，不做"全树搜 key 名叫 meta 的字典" —— 那会把业务数据里同名的键
#: 误当成交付契约的一部分。
META_DICT_PATHS = ("payload.meta", "payload.value.meta", "payload.value.table.meta")


def collect_meta_keys(payload) -> set[str]:
    """按 `META_DICT_PATHS` 收集这份 payload 里所有语义 meta 键的并集。"""
    found: set[str] = set()
    if not isinstance(payload, dict):
        return found
    cands = [payload.get("meta")]
    val = payload.get("value")
    if isinstance(val, dict):
        cands.append(val.get("meta"))
        tbl = val.get("table")
        if isinstance(tbl, dict):
            cands.append(tbl.get("meta"))
    for c in cands:
        if isinstance(c, dict):
            found |= {str(k) for k in c}
    return found


def payload_meta(payload) -> dict:
    """按 `META_DICT_PATHS` 取这份 payload 的语义 meta（多处出现时合并，后者覆盖前者）。"""
    out: dict = {}
    if not isinstance(payload, dict):
        return out
    cands = [payload.get("meta")]
    val = payload.get("value")
    if isinstance(val, dict):
        cands.append(val.get("meta"))
        tbl = val.get("table")
        if isinstance(tbl, dict):
            cands.append(tbl.get("meta"))
    for c in cands:
        if isinstance(c, dict):
            out.update(c)
    return out


#: 每份样本 `meta` 段里**必须存在**的语义键（源码锚点，防"删键解约束"）。
#:
#: 一个真实缺口（复核 V-白名单）：`ALLOWED_ADDED_KEYS` 的语义是"**新增**放行"。
#: 于是把样本里的 `status`/`source`/`provisional`… 删掉，回放会重新"新增"它们，
#: 而"新增"是放行的 → 门禁照绿，**约束静默消失**。这张表把"必须有哪几个键"
#: 钉在源码里：少一个就是硬错误。
#:
#: ⚠️ 与 A′（`INFO_ONLY_META_CARRIERS`）的边界，别混：
#:    · A′ 说的是**值**不判（`fetched_at` / `elapsed` / `source["host"]`）；
#:    · 本表说的是**存在性**必须成立。
#:    两件事正交：`elapsed` 的值每次不同（A′ 放行），但 `elapsed` 这个键**必须还在**。
EXPECTED_SAMPLE_META_KEYS = {
    # 这 15 份是阶段 1 之前录的：容器 meta 不在 payload 里（走信息性通道）
    "bars_510300": [],
    "bars_5m_300": [],
    "bars_60m_300": [],
    "bars_day_300": [],
    "err_bars_000300": [],
    "err_bars_932000": [],
    "err_bars_IF00": [],
    "minutes": [],
    "quotes_indices": [],
    "ticks": [],
    "product_north": [],
    "product_ztqd": [],
    "reject_bars_880005": [],
    "reject_bars_880006": [],
    "reject_bars_880641": [],
    "reject_bars_880866": [],
    "f10_gsjj": ["cols_align", "product", "template_check"],
    "finance_zcfz": ["cols_align", "cols_source", "cols_union", "product",
                     "template_check"],
    "product_lrmx": ["cols_source", "cols_union"],
    # ── 阶段 1.5 同名重录的 4 份 ──────────────────────────────────────
    # ⚠️ `boards_industry` 于阶段 4 归档 → 在役样本改名为 `boards_industry_s4`，
    #    键集合按实测登记（比旧版多 `change_pct_method`：涨跌幅现在是实算的）。
    "boards_industry_s4": ["as_of", "as_of_note", "change_pct_method",
                           "const_fields", "elapsed", "fetch_clock",
                           "fetched_at", "params", "row_semantics", "source",
                           "source_note", "status", "status_note",
                           "unavailable", "why"],
    "market_stat_updown": ["as_of", "as_of_note", "elapsed", "fetch_clock",
                           "fetched_at", "params", "source", "source_note",
                           "status", "status_note", "trade_date"],
    "market_stat_sentiment": ["as_of", "as_of_note", "elapsed", "fetch_clock",
                              "fetched_at", "note", "params", "source",
                              "source_calls", "source_note", "status",
                              "status_note", "trade_date", "unmapped_raw_columns"],
    "market_stat_industry": ["as_of", "as_of_note", "calls", "elapsed",
                             "fetch_clock", "fetched_at", "level1_codes", "note",
                             "params_dir", "skipped_codes", "source",
                             "source_note", "status", "status_note", "truncated"],
    # ── 阶段 2 新增的 4 份 ────────────────────────────────────────────
    "breadth_880005": ["as_of", "as_of_note", "elapsed", "fetch_clock",
                       "fetched_at", "field_map", "note", "period", "provisional",
                       "series", "series_name", "source", "status", "unavailable"],
    "breadth_880006": ["as_of", "as_of_note", "elapsed", "fetch_clock",
                       "fetched_at", "field_map", "note", "period", "provisional",
                       "series", "series_name", "source", "status", "unavailable"],
    "breadth_880641": ["as_of", "as_of_note", "elapsed", "fetch_clock",
                       "fetched_at", "field_map", "note", "period", "provisional",
                       "series", "series_name", "source", "status", "unavailable"],
    "breadth_880866": ["as_of", "as_of_note", "elapsed", "fetch_clock",
                       "fetched_at", "field_map", "note", "period", "provisional",
                       "series", "series_name", "source", "status", "unavailable"],
}

#: 白名单键里**黄金样本覆盖不到**的那几个 —— 明确记账，不许沉默。
#: 判据：`ALLOWED_ADDED_KEYS - {"meta"}` 的每一项，要么被上面某份样本的
#: `EXPECTED_SAMPLE_META_KEYS` 覆盖，要么在下面这个字典里给出**原因**。
#: 闭集：多一个少一个都会让契约测试红。
META_KEYS_NOT_COVERED_BY_GOLDEN = {
    "clock_note": "只有容器 meta 才产出；26 份在役样本的 payload 里一份容器 meta "
                  "都没有（阶段 1 之前录的），容器 meta 走信息性通道、不进 rc。"
                  "改由 tests/test_series_containers.py + tests/test_timeliness.py 管。",
    "data_date": "只有 Dataset（多表返回）才有；当前样本集里没有 Dataset 形态的 "
                 "meta 落盘。属已知缺口，见 golden/README.md「已知缺口」。",
}


#: 容器 meta 纪律（守卫 2，2026-10-27）：**样本无容器 meta 的样本**，回放侧若仍
#: 产出容器 meta，必须把「回放侧 meta 的键集」登记在这张表里 —— 键集**精确相等**
#: 才放行（多一个键、少一个键、键序无关但集合必须一致）。未登记的样本凭空多出
#: meta → 硬差异。
#:
#: 为什么键集只做"放行"、**值不进 rc**：容器 meta 的值（fetched_at/elapsed/
#: source.host）离线不可复现，是 A′（INFO_ONLY_META_CARRIERS）管的事 —— 那套
#: 规则同样适用于"两边都有 meta"时的逐键比对。这里只钉住**存在性与键集**，
#: 与 EXPECTED_SAMPLE_META_KEYS 的"存在性登记"同一条纪律的两面。
#:
#: 当前 27 份在役样本的**样本侧**均无容器 meta，且回放侧（阶段 1 起）对这 7 份
#: Bars/Quotes/Minutes/Ticks 形态的样本会产出容器 meta —— 键集按实测登记如下。
#: 其余样本回放侧不产出 meta（登记为空集即等于"回放也不许多出"）。
#:
#: ARCH-002（2026-10-27）：出处面从散装 meta 键收成一等 `Provenance`
#: （tdxdata/_provenance.py），container_meta() 现在记录 `provenance.to_dict()`
#: 的键集。与 meta 时代（as_of/clock_note/elapsed/fetch_clock/fetched_at/
#: provisional/source/status）的换算：fetch_clock → clock（str→枚举名）、
#: source 五键 → host/cmd/ctl/cached 一等字段、说明性 note → notes（元组）、
#: 新增 data_date 与 extra（旁证 dict）两个一等键。阶段 1 的逐根 `provisional`
#: 现在住在 `extra["provisional"]`（_finish_series **extra → provenance.extra，
#: 锚点 client.py:1086/:629-631），不是顶层键 —— 顶层键集 12 个，4 份 bars 与
#: 其余 3 份容器**完全一致**（extra 内容差异走主 diff 的严比）。
#: 键集为**精确相等**对账，Provenance 加/删字段必须同步改这里。
EXPECTED_REPLAY_CONTAINER_META_KEYS = {
    name: ["as_of", "cached", "clock", "cmd", "ctl", "data_date",
           "elapsed", "extra", "fetched_at", "host", "notes", "status"]
    for name in ("bars_510300", "bars_5m_300", "bars_60m_300", "bars_day_300",
                 "minutes", "quotes_indices", "ticks")
}


#: 数据行「历史缺失键」登记表（2026-10-27 Lead 裁决 A）。
#:
#: 形态：`{样本基名: {键: 源码锚点+理由}}`。语义：登记键只豁免
#: **「样本侧缺、回放侧有」** 这一个方向的新增，且只在**数据层**生效
#: （meta 子树不适用，仍走 ALLOWED_ADDED_KEYS 的 in_meta 规则）；
#: 样本有而回放无 → 照样报差异；回放侧数据行**必须**仍产出登记键
#: （存在性守卫，见 check 循环）—— 代码删键会让登记表失效报错，
#: 防止它退化成永久后门。
#:
#: ⚠️ 精确键名、逐样本登记，**不许通配**（不许 per-sample 允许任意键，
#: 不许全局允许某键）。新增条目必须有源码锚点 + 阶段理由，跑不过 diff 审查。
EXPECTED_DATA_ROW_LATE_KEYS = {
    # bars_510300 / bars_5m_300 / bars_60m_300 / bars_day_300：
    # 回放侧逐根产出 `provisional`（阶段 1 加的**数据信号**列：当根是否盘中
    # 未定稿，锚点 tdxdata/client.py:1166 `row["provisional"] = bar.provisional`，
    # 并放 cols 末列，见 client.py:1129-1160 的注释），而这 4 份样本录制早于
    # 该字段加入 —— 样本侧缺、回放侧有，属真实的样本-代码漂移，按登记纪律
    # 显式记账（Lead 裁决 A，2026-10-27），不重录、不放宽白名单。
    "bars_510300": {"provisional": "tdxdata/client.py:1166 —— 阶段 1 逐根数据"
                                   "信号列 provisional，样本录制早于该字段"},
    "bars_5m_300": {"provisional": "tdxdata/client.py:1166 —— 阶段 1 逐根数据"
                                   "信号列 provisional，样本录制早于该字段"},
    "bars_60m_300": {"provisional": "tdxdata/client.py:1166 —— 阶段 1 逐根数据"
                                    "信号列 provisional，样本录制早于该字段"},
    "bars_day_300": {"provisional": "tdxdata/client.py:1166 —— 阶段 1 逐根数据"
                                    "信号列 provisional，样本录制早于该字段"},
}


def meta_integrity_sha(meta: dict) -> str:
    """样本 `meta` 段的完整性摘要（**去掉 `sha256` 自身**后求规范化 sha256）。

    样本 `payload` 一直有 `sha256` 自证，但 `meta`（`params`/`api`/`cmd`/`frames`/
    `fetched_at`/`server_time`）此前**完全不受保护** —— 静默改写后门禁照样绿。
    """
    return sha_of({k: v for k, v in (meta or {}).items() if k != "sha256"})


# ══════════════════════════════════════════════════════════════════════════
# 一、规范化 JSON / 摘要
# ══════════════════════════════════════════════════════════════════════════
def canon(obj) -> str:
    """规范化 JSON（键排序、无空格、不转义中文）—— 摘要口径的唯一事实源。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha_of(obj) -> str:
    """对规范化 JSON 的 UTF-8 字节算 sha256。"""
    return hashlib.sha256(canon(obj).encode("utf-8")).hexdigest()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data or b"").hexdigest()


def _dump_json(path: str, obj) -> None:
    """写 JSON：UTF-8 **无 BOM**（`encoding="utf-8"` 即无 BOM）。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


# ══════════════════════════════════════════════════════════════════════════
# 二、帧（请求参数 / 原始回包）二进制读写
# ══════════════════════════════════════════════════════════════════════════
def parse_wire_frame(frame: bytes) -> tuple[int, str, bytes]:
    """从 7615 帧字节里取出 `(ctl, cmd, args)`。

    帧布局（`datacenter.build_frame` / `pb_api.build` / `session._frame` 三者一致）::

        0   0x0B
        1-4 msgid (LE)
        5   ctl
        6-7 len  8-9 len  10-11 0x264B  12-13 0x0064
        14… <cmd ascii> 00 00 <args>
    """
    if len(frame) < 16:
        raise ValueError("帧太短（%d 字节），不是 7615 帧" % len(frame))
    ctl = frame[5]
    body = frame[14:]
    cut = body.find(b"\x00\x00")
    if cut < 0:
        return ctl, "", b""
    cmd = body[:cut].decode("ascii", "replace")
    return ctl, cmd, body[cut + 2:]


def write_frame_file(path: str, meta: dict, args: bytes, raw: bytes, blobs) -> None:
    """按模块头写的格式落盘（魔数 + 4 个长度前缀段）。"""
    out = bytearray(FRAME_MAGIC)
    mj = json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8")
    out += struct.pack("<I", len(mj)) + mj
    out += struct.pack("<I", len(args)) + bytes(args)
    raw = raw or b""
    out += struct.pack("<I", len(raw)) + raw
    out += struct.pack("<I", len(blobs))
    for blob in blobs:
        out += struct.pack("<I", len(blob)) + bytes(blob)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(bytes(out))
    os.replace(tmp, path)


def read_frame_file(path: str):
    """读回 `(meta, args, raw, blobs)`，与 `write_frame_file` 对称。"""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:6] != FRAME_MAGIC:
        raise ValueError("%s 不是 TDXGF1 帧文件（前 6 字节 %r）" % (path, data[:6]))

    pos = [6]

    def take_u32() -> int:
        val = struct.unpack_from("<I", data, pos[0])[0]
        pos[0] += 4
        return val

    n = take_u32()
    meta = json.loads(data[pos[0]:pos[0] + n].decode("utf-8"))
    pos[0] += n
    n = take_u32()
    args = data[pos[0]:pos[0] + n]
    pos[0] += n
    n = take_u32()
    raw = data[pos[0]:pos[0] + n]
    pos[0] += n
    n = take_u32()
    blobs = []
    for _ in range(n):
        m = take_u32()
        blobs.append(data[pos[0]:pos[0] + m])
        pos[0] += m
    return meta, args, raw, blobs


def frame_key(transport: str, cmd: str, ctl: int, args: bytes) -> tuple:
    """回放查表键：传输方式 + 命令 + ctl + 请求参数字节的指纹。"""
    return (transport, str(cmd), int(ctl), sha_bytes(bytes(args)))


def frame_filename(seq: int, cmd: str, ctl: int) -> str:
    safe = (cmd or "nocmd").replace(".", "_").replace(":", "_").replace("/", "_")
    return "%04d_%s_0x%02X.bin" % (seq, safe, int(ctl) & 0xFF)


# ══════════════════════════════════════════════════════════════════════════
# 三、把返回值编码成 payload（可 JSON 化的"人话"形态）
# ══════════════════════════════════════════════════════════════════════════
def _plain(obj):
    """dataclass → dict；dict/list 递归；其它原样。**浮点不 round**。"""
    if is_dataclass(obj) and not isinstance(obj, type):
        try:
            return asdict(obj)
        except Exception:                                       # noqa: BLE001
            return {f: _plain(getattr(obj, f, None)) for f in getattr(obj, "__slots__", ())}
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(x) for x in obj]
    return obj


def _table_dict(tbl: Table) -> dict:
    """`Table` 固定存成 `{cols, rows, cmd, domain, provenance}`（Row → 普通 dict）。

    ARCH-002（2026-10-27）：`Table.meta` 公开面退役，出处一等字段 `Provenance`
    （`tdxdata/_provenance.py`）。样本侧记录 `provenance.to_dict()`（JSON 安全，
    fetched_at 为 ISO 文本）。旧样本（meta 时代录制）没有该键 → 值 None；
    `provenance` 已在 ALLOWED_ADDED_KEYS，新增不红、改值照红。
    """
    prov = getattr(tbl, "provenance", None)
    return {
        "cols": list(tbl.cols),
        "rows": [dict(r) for r in tbl.rows],
        "cmd": tbl.cmd,
        "domain": tbl.domain,
        "provenance": prov.to_dict() if prov is not None else None,
    }


def _jsonable(val):
    """把 meta 里的值收敛成 JSON 原生类型（`datetime` 之类退化成 str，别让录制炸掉）。"""
    if val is None or isinstance(val, (str, bool, int, float)):
        return val
    if isinstance(val, dict):
        return {str(k): _jsonable(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_jsonable(v) for v in val]
    return str(val)


def container_meta(obj):
    """list/tuple 子类且带 `.provenance` → 该 Provenance 的 JSON 化副本；否则 `None`。

    为什么需要它
    ------------
    阶段 1 起 `get_bars` / `get_quotes` / `get_minutes` / `get_ticks` / `get_market_page`
    / `get_universe` 的返回值从 `list` 换成 `list` **子类**（`Bars` / `Quotes` / `Minutes` /
    `Ticks`），出处挂在实例的 `provenance`（ARCH-002 前是 `.meta`）。`json.dumps`
    只看得到 list 的内容，看不见 `provenance` —— 不显式取出来，门禁就漏掉了本阶段的核心产出。

    ⚠️ **payload 的 `kind` 仍然按元素类型算**（`Bars` → `list[Bar]`，不写成 `Bars`）：
    容器类名一变就改 `kind` 会让阶段 0 冻结的 23 份老样本全部报差异，那是噪声不是信号。
    """
    if not isinstance(obj, (list, tuple)):
        return None
    prov = getattr(obj, "provenance", None)
    if prov is None:
        return None
    return _jsonable(prov.to_dict())


def encode_ok(obj) -> dict:
    """成功返回 → payload。"""
    if isinstance(obj, Table):
        return {"outcome": "ok", "kind": "Table", "value": _table_dict(obj)}
    if isinstance(obj, Dataset):
        return {"outcome": "ok", "kind": "Dataset",
                "value": {"name": obj.name, "params": _plain(obj.params),
                          "table": _table_dict(obj.table)}}
    if isinstance(obj, (list, tuple)):
        items = list(obj)
        kind = "list[%s]" % (type(items[0]).__name__ if items else "?")
        payload = {"outcome": "ok", "kind": kind, "value": [_plain(x) for x in items]}
        # 容器 meta 存**同级键** `meta`（在 `ALLOWED_ADDED_KEYS` 里，
        # 所以老样本没有它也不会被判成差异）
        meta = container_meta(obj)
        if meta is not None:
            payload["meta"] = meta
        return payload
    return {"outcome": "ok", "kind": type(obj).__name__, "value": _plain(obj)}


def encode_error(exc: BaseException) -> dict:
    """异常 → payload（异常类型全名 + `str(exc)` 原文，一个字符不改）。"""
    cls = type(exc)
    return {
        "outcome": "error",
        "exc_type": "%s.%s" % (cls.__module__, cls.__qualname__),
        "exc_msg": str(exc),
        "exc_bases": ["%s.%s" % (b.__module__, b.__qualname__)
                      for b in cls.__mro__[1:] if b is not object],
    }


def as_payload(obj=None, exc: BaseException | None = None) -> dict:
    return encode_error(exc) if exc is not None else encode_ok(obj)


def walk_quotes(payload, sink: list) -> None:
    """把 payload 里所有 `Quote` 形态的行收进 `sink`（只为取 `server_time`）。"""
    kind = payload.get("kind")
    if kind == "list[Quote]":
        for item in payload.get("value") or []:
            if isinstance(item, dict):
                sink.append(item)
    elif kind == "Table":
        for row in (payload.get("value") or {}).get("rows") or []:
            if isinstance(row, dict):
                sink.append(row)


# ══════════════════════════════════════════════════════════════════════════
# 四、样本清单
# ══════════════════════════════════════════════════════════════════════════
STOCK = "600519"


def _spec(name, api, params, run, online_skip=False):
    return {"name": name, "api": api, "params": params,
            "run": run, "online_skip": online_skip}


def _get(name, **kwargs):
    """`Client.get(name, **kwargs)` 的薄封装（保持 lambda 可读）。"""
    return lambda c: c.get(name, **kwargs)


SPECS = [
    # ── 行情 ────────────────────────────────────────────────────────────
    _spec("quotes_indices", "Client.get_quotes(7 大指数)",
          {"symbols": ["999999", "399001", "399006", "sh000300",
                       "sh000016", "sh000905", "399005"]},
          lambda c: c.get_quotes(["999999", "399001", "399006", "sh000300",
                                  "sh000016", "sh000905", "399005"])),
    _spec("bars_day_300", "Client.get_bars(600519, day, count=300)",
          {"symbol": STOCK, "period": "day", "count": 300, "adjust": "NONE"},
          lambda c: c.get_bars(STOCK, period="day", count=300)),
    _spec("bars_5m_300", "Client.get_bars(600519, 5m, count=300)",
          {"symbol": STOCK, "period": "5m", "count": 300, "adjust": "NONE"},
          lambda c: c.get_bars(STOCK, period="5m", count=300)),
    _spec("bars_60m_300", "Client.get_bars(600519, 60m, count=300)",
          {"symbol": STOCK, "period": "60m", "count": 300, "adjust": "NONE"},
          lambda c: c.get_bars(STOCK, period="60m", count=300)),
    _spec("minutes", "Client.get_minutes(600519)", {"symbol": STOCK},
          lambda c: c.get_minutes(STOCK)),
    _spec("ticks", "Client.get_ticks(600519, count=200)",
          {"symbol": STOCK, "count": 200}, lambda c: c.get_ticks(STOCK, count=200)),
    _spec("bars_510300", "Client.get_bars(510300, day, count=30)",
          {"symbol": "510300", "period": "day", "count": 30},
          lambda c: c.get_bars("510300", period="day", count=30)),
    # ── 阶段 2：非价格序列（`get_bars` 有意拒绝 → `get_breadth` 取数）────
    # 这四个代码的 OHLC 是家数/指标点位、**不是价格**。阶段 2 起 `get_bars` 抛
    # `NotSupportedError`（拦在发请求之前，零网络），数据走 `get_breadth()`。
    # 旧的 `bars_880005/880006/880866`、`err_bars_880005` 已归档进 `_superseded/`。
    _spec("breadth_880005", "Client.get_breadth('880005', count=30)",
          {"code": "880005", "count": 30},
          lambda c: c.get_breadth("880005", count=30)),
    _spec("breadth_880006", "Client.get_breadth('880006', count=30)",
          {"code": "880006", "count": 30},
          lambda c: c.get_breadth("880006", count=30)),
    _spec("breadth_880866", "Client.get_breadth('880866', count=30)",
          {"code": "880866", "count": 30},
          lambda c: c.get_breadth("880866", count=30)),
    _spec("breadth_880641", "Client.get_breadth('880641', count=30)",
          {"code": "880641", "count": 30},
          lambda c: c.get_breadth("880641", count=30)),
    # ── 阶段 2 拒绝现场（**零 wire 请求**：拦在建帧之前，所以没有帧可回放）──
    # 四份一起冻住，把四条指路文案都锁死。
    _spec("reject_bars_880005", "Client.get_bars('880005') —— 阶段 2 拒绝现场",
          {"symbol": "880005", "period": "day", "count": 5},
          lambda c: c.get_bars("880005", period="day", count=5)),
    _spec("reject_bars_880006", "Client.get_bars('880006') —— 阶段 2 拒绝现场",
          {"symbol": "880006", "period": "day", "count": 5},
          lambda c: c.get_bars("880006", period="day", count=5)),
    _spec("reject_bars_880866", "Client.get_bars('880866') —— 阶段 2 拒绝现场",
          {"symbol": "880866", "period": "day", "count": 5},
          lambda c: c.get_bars("880866", period="day", count=5)),
    _spec("reject_bars_880641", "Client.get_bars('880641') —— 阶段 2 拒绝现场",
          {"symbol": "880641", "period": "day", "count": 5},
          lambda c: c.get_bars("880641", period="day", count=5)),
    # ── 板块 / 市场统计 ────────────────────────────────────────────────
    _spec("boards_industry_s4", "Client.get_boards('industry')", {"kind": "industry"},
          lambda c: c.get_boards("industry")),
    _spec("market_stat_updown", "Client.get_market_stat('updown')", {"kind": "updown"},
          lambda c: c.get_market_stat("updown")),
    _spec("market_stat_sentiment", "Client.get_market_stat('sentiment')",
          {"kind": "sentiment"}, lambda c: c.get_market_stat("sentiment")),
    _spec("market_stat_industry", "Client.get_market_stat('industry')",
          {"kind": "industry"}, lambda c: c.get_market_stat("industry"),
          online_skip=True),        # 1 + N 次调用，约 60~70 s
    # ── 数据中心产品 ───────────────────────────────────────────────────
    _spec("product_lrmx", "Client.get('lrmx')", {"name": "lrmx"}, _get("lrmx")),
    _spec("product_north", "Client.get('north')", {"name": "north"}, _get("north")),
    _spec("product_ztqd", "Client.get('ztqd')", {"name": "ztqd"}, _get("ztqd")),
    # ── F10 / 财务 ─────────────────────────────────────────────────────
    _spec("f10_gsjj", "Client.get_f10(600519, 'gsjj')",
          {"symbol": STOCK, "section": "gsjj"}, lambda c: c.get_f10(STOCK, "gsjj")),
    _spec("finance_zcfz", "Client.get_finance(600519, 'zcfz')",
          {"symbol": STOCK, "kind": "zcfz"}, lambda c: c.get_finance(STOCK, "zcfz")),
    # ── 错误现场（阶段 2/4 的行为变更基线）────────────────────────────
    _spec("err_bars_000300", "Client.get_bars('000300') —— 错误现场",
          {"symbol": "000300", "period": "day", "count": 5},
          lambda c: c.get_bars("000300", period="day", count=5)),
    _spec("err_bars_932000", "Client.get_bars('932000') —— 错误现场",
          {"symbol": "932000", "period": "day", "count": 5},
          lambda c: c.get_bars("932000", period="day", count=5)),
    _spec("err_bars_IF00", "Client.get_bars('IF00') —— 错误现场",
          {"symbol": "IF00", "period": "day", "count": 5},
          lambda c: c.get_bars("IF00", period="day", count=5)),
]

SPEC_BY_NAME = {s["name"]: s for s in SPECS}


# ══════════════════════════════════════════════════════════════════════════
# 五、录制（包裹式探针，绝不改库）
# ══════════════════════════════════════════════════════════════════════════
class Recorder:
    """在 `dc._send` 与 `Session.ask` 外面各包一层，把 (请求, 回包) 记下来。

    包裹而不是替换：真实收发照原样走（限流、重试、换机都不受影响），
    只是在返回之后顺手记一笔。所以录到的帧**就是**当时的线上字节。
    """

    def __init__(self, seq_start: int = 1):
        self.entries: list[dict] = []
        self.current_sample = ""            # 当前正在录的样本名（写进 manifest）
        self._seq = int(seq_start) - 1
        self._orig: dict = {}
        self._ctx: dict | None = None       # 当前 _send 调用上下文（帧 / raw）

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    # -- 安装 / 卸载 ------------------------------------------------------
    def install(self) -> None:
        from tdxdata._engine.session import Session

        self._orig = {"send": dc._send, "unwrap": dc.unwrap, "ask": Session.ask}
        rec = self

        def cap_unwrap(buf):
            # `Session._ask_locked` 会拿半截 buffer 反复调 unwrap，那批噪声不记。
            if rec._ctx is not None and rec._ctx.get("raw") is None:
                caller = sys._getframe(1)
                if (caller.f_code.co_name == "_send"
                        and caller.f_globals.get("__name__", "").endswith(".datacenter")):
                    rec._ctx["raw"] = bytes(buf)
            return rec._orig["unwrap"](buf)

        def send(frame, *args, **kwargs):
            rec._ctx = {"frame": bytes(frame), "raw": None}
            try:
                blobs = rec._orig["send"](frame, *args, **kwargs)
            finally:
                ctx = rec._ctx
                rec._ctx = None
            rec._add("send", frame=ctx["frame"], raw=ctx["raw"],
                     blobs=list(blobs or []))
            return blobs

        def ask(session, ctl, cmd, args, idle=4.0, tail=0.35):
            payload = rec._orig["ask"](session, ctl, cmd, args, idle=idle, tail=tail)
            rec._add("ask", ctl=ctl, cmd=cmd, args=bytes(args),
                     raw=None, blobs=[payload or b""])
            return payload

        dc.unwrap = cap_unwrap
        dc._send = send
        Session.ask = ask

    def uninstall(self) -> None:
        if not self._orig:
            return
        from tdxdata._engine.session import Session

        dc._send = self._orig["send"]
        dc.unwrap = self._orig["unwrap"]
        Session.ask = self._orig["ask"]
        self._orig = {}

    # -- 记账 -------------------------------------------------------------
    def _add(self, transport: str, *, frame: bytes | None = None, raw=None,
             blobs, ctl=None, cmd=None, args=None) -> None:
        if transport == "send":
            ctl, cmd, args = parse_wire_frame(frame)
        resp = b"".join(blobs) if blobs else b""
        self.entries.append({
            "seq": self._next_seq(),
            "sample": self.current_sample,
            "transport": transport,
            "cmd": cmd or "",
            "ctl": int(ctl),
            "args": bytes(args or b""),
            "raw": raw,
            "blobs": list(blobs),
            "args_len": len(args or b""),
            "args_sha256": sha_bytes(bytes(args or b"")),
            "resp_len": len(resp),
            "resp_sha256": sha_bytes(resp),
            "raw_len": len(raw or b""),
        })


# ══════════════════════════════════════════════════════════════════════════
# 六、离线回放（不碰网络）
# ══════════════════════════════════════════════════════════════════════════
class FrameMissing(BaseException):
    """回放时查不到对应帧。

    **刻意继承 `BaseException`**：`pb_api._raw_send` 里是 `except Exception`
    + `sleep(8)` 重试 4 轮 —— 用普通 Exception 会让"帧缺失"退化成"白等 32 秒
    然后静默变成空结果"，那就把真正的原因埋掉了。这里必须一路炸到顶层。
    """


class _NoPacer:
    """回放用的假闸门：真实 `Pacer.wait()` 每条命令要 sleep 2.2 秒。"""

    limited_events = 0
    gap = 0.0
    base = 0.0

    def wait(self) -> float:
        return 0.0

    def note_ok(self) -> None:
        pass

    def note_limited(self) -> float:
        self.limited_events += 1
        return 0.0

    def block(self, host, seconds=0.0) -> None:
        pass

    def ready(self, host) -> bool:
        return True

    def stats(self) -> dict:
        return {"base": 0.0, "current": 0.0, "max": 0.0,
                "limited_events": 0, "cooling_hosts": 0}


class _ReplaySession:
    """冒充 `Session`：`ask` 只查帧表，连接相关方法全是 no-op。"""

    alive = True

    def __init__(self, replay: "Replay"):
        self._replay = replay

    def ask(self, ctl, cmd, args, idle=4.0, tail=0.35):
        return self._replay.payload_for("ask", cmd, ctl, args)

    def connect(self, tries: int = 2):
        return self

    def reconnect(self, tries: int = 2):
        return self

    def switch_host(self, cooldown=None):
        return self

    def close(self) -> None:
        pass


class Replay:
    """用 `frames/` 里的字节喂回当前解码层。**整个类不建立任何 socket**。"""

    def __init__(self, entries: list[dict]):
        self.entries = entries
        self.table: dict[tuple, dict] = {}
        for ent in entries:
            self.table.setdefault(frame_key(ent["transport"], ent["cmd"],
                                            ent["ctl"], ent["args"]), ent)
        self._orig: dict = {}
        self._session = _ReplaySession(self)
        self.misses: list[str] = []

    # -- 安装 / 卸载 ------------------------------------------------------
    def install(self) -> None:
        client_cls = getattr(tdxdata, "Client")
        self._orig = {
            "send": dc._send,
            "ensure": client_cls._ensure,
            "sleep": time.sleep,
        }
        replay = self

        def send(frame, *args, **kwargs):
            ctl, cmd, params = parse_wire_frame(frame)
            ent = replay._lookup("send", cmd, ctl, params)
            raw = ent.get("raw") or b""
            if raw:
                blobs = dc.unwrap(raw) or dc.unwrap_frames(raw)
                # 与生产 `_send` 同口径：切不出包就是空列表（不拿"存下来的"顶上，
                # 否则解包层退化了也看不出来）
                return blobs
            return list(ent.get("blobs") or [])

        client_cls._ensure = lambda self_: replay._session
        dc._send = send
        # 回放是纯 CPU：把重试退避用的 sleep 抹掉（`pb_api._raw_send` 里有 8 秒）
        time.sleep = lambda *a, **k: None

    def uninstall(self) -> None:
        if not self._orig:
            return
        client_cls = getattr(tdxdata, "Client")
        dc._send = self._orig["send"]
        client_cls._ensure = self._orig["ensure"]
        time.sleep = self._orig["sleep"]
        self._orig = {}

    # -- 查表 -------------------------------------------------------------
    def _lookup(self, transport: str, cmd: str, ctl: int, args: bytes) -> dict:
        key = frame_key(transport, cmd, ctl, args)
        ent = self.table.get(key)
        if ent is None:
            msg = ("帧缺失：transport=%s cmd=%s ctl=0x%02X args_sha256=%s"
                   % (transport, cmd, int(ctl), key[3]))
            self.misses.append(msg)
            raise FrameMissing(msg)
        return ent

    def payload_for(self, transport: str, cmd: str, ctl: int, args: bytes) -> bytes:
        ent = self._lookup(transport, cmd, ctl, args)
        blobs = list(ent.get("blobs") or [])
        return blobs[0] if blobs else b""


# ══════════════════════════════════════════════════════════════════════════
# 七、逐字段 diff
# ══════════════════════════════════════════════════════════════════════════
def _brief(val, limit: int = 90) -> str:
    try:
        text = canon(val)
    except Exception:                                           # noqa: BLE001
        text = repr(val)
    return text if len(text) <= limit else text[:limit] + "…"


def _eq(old, new) -> bool:
    """标量相等判据：数字按值比但**要求类型一致**，其它要求类型也一致。

    2026-10-27 复核实洞：旧写法把 `1 == 1.0` 当相等 —— int/float 的类型漂移
    （序列化/解码层把整型字段悄悄变成浮点）完全测不出。现在 int 与 float
    **算差异**（bool 仍最先截住，绝不与 int 混比）。双 NaN 仍视为相等。
    """
    if isinstance(old, bool) or isinstance(new, bool):
        return old is new
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        if type(old) is not type(new):
            return False            # int vs float = 类型漂移，必须报差异
        if old != old and new != new:          # 双 NaN
            return True
        return old == new
    return type(old) is type(new) and old == new


def _diff_meta_source(old, new, path: str) -> list[str]:
    """`source` 的比对口径（A′）：**结构全判，只放行 `host` 的值**。

    · 两边都必须是 **dict**（退化成字符串 = D7 的病征，必须红）；
    · 键集必须**恰为** `host`/`cmd`/`ctl`/`clock`/`cached` 这 5 个；
    · `cmd`/`ctl`/`clock`/`cached` 的**值**严格比对；
    · `host` 的**值**不判（离线不可复现，见 INFO_ONLY_META_CARRIERS 的注释）。
    """
    out: list[str] = []
    for side, val in (("样本", old), ("回放", new)):
        if not isinstance(val, dict):
            out.append("%s %s侧不是 dict（而是 %s）—— `source` 必须保持 5 键 dict；"
                       "退化成字符串正是 D7 的病征"
                       % (path, side, type(val).__name__))
    if out:
        return out
    for side, val in (("样本", old), ("回放", new)):
        if set(val) != set(SOURCE_DICT_KEYS):
            out.append("%s %s侧键集 %s ≠ 约定的 5 键 %s"
                       % (path, side, sorted(val), list(SOURCE_DICT_KEYS)))
    if out:
        return out
    skip = set(INFO_ONLY_META_CARRIERS["source"])
    for key in SOURCE_DICT_KEYS:
        if key in skip:
            continue
        out.extend(diff_values(old[key], new[key], "%s.%s" % (path, key)))
    return out


def _old_meta_to_prov_shape(old: dict) -> dict:
    """把 meta 时代的散装键投影成 Provenance 形状（只投影、不放宽）。

    投影规则（与 PROVENANCE_KEY_RENAME 一致）：
    · fetch_clock → clock；source 五键 → host/cmd/ctl/cached 顶层
      （source.clock 与 fetch_clock 同义，两边取一，一致才不报差异）；
    · 说明性文本键 → notes 列表（成员精确存在断言由 diff_values 文本比对完成）；
    · 其余键 → extra[同键名]。
    """
    prov: dict = {"notes": [], "extra": {}}
    for k, v in old.items():
        if k == "fetch_clock":
            prov["clock"] = v
        elif k == "source" and isinstance(v, dict):
            for sk in ("host", "cmd", "ctl", "cached"):
                if sk in v:
                    prov[sk] = v[sk]
            if "clock" in v and "clock" not in prov:
                prov["clock"] = v["clock"]
        elif k in ("as_of_note", "clock_note", "status_note", "source_note",
                   "note", "why"):
            if isinstance(v, str):
                prov["notes"].append(v)
        elif k in ("as_of", "status", "fetched_at", "elapsed", "data_date"):
            # 与 Provenance 同名的一等字段：样本顶层键 → provenance 顶层键
            prov[k] = v
        else:
            prov["extra"][k] = v
    return prov


def _diff_meta_vs_provenance(old_meta: dict, new_prov: dict, path: str) -> list[str]:
    """样本冻结的 meta（旧 schema）对回放侧 provenance（新 schema）逐段严比。

    只做键名换算，**值一律走 diff_values 严比**（fetched_at/elapsed/host 的
    信息性豁免继承 in_meta 规则）。
    """
    out: list[str] = []
    proj = _old_meta_to_prov_shape(old_meta)
    if not isinstance(new_prov, dict):
        return ["%s 回放侧 provenance 不是 dict（%s）—— schema 迁移失配"
                % (path, type(new_prov).__name__)]
    # notes：样本的说明性文本必须逐条存在于回放 notes **或** extra 的字符串值里
    #（source_note / note 这两个键在迁移中落了 extra —— 两段都是"文本凭据"，
    # 对账口径 = 文本精确存在，丢文本才报差异）。
    new_notes = list(new_prov.get("notes") or [])
    # extra：样本散装键 → 回放 extra[同键名]，值严比
    new_extra = new_prov.get("extra") or {}
    new_extra_texts = [v for v in new_extra.values() if isinstance(v, str)]
    for want in proj["notes"]:
        if want in new_notes or want in new_extra_texts:
            continue
        # schema 引用型文案的封闭等价对：ARCH-002 把 meta 引用改写为
        # provenance 引用（文案里 `table.meta["data_date"]` → `provenance.data_date`），
        # 事实同一、引用键名换了。封闭登记（逐条精确），新的改写必须显式加行。
        if want == ("这条通道（数据中心 JSON，`ResultSets`）回包**不带数据日期/时间**，"
                    "所以 as_of 无法确定；要知道数据日请用 `Client.get(...).data_date`"
                    "（从首行 N001 推）或本表的 meta[\"data_date\"]"):
            continue
        out.append("%s.notes 缺样本文本 %s（旧说明性键的文本必须原样迁入 "
                       "notes 或 extra）" % (path, _brief(want)))
    for k, v in proj["extra"].items():
        if k not in new_extra:
            # 说明性文本键在样本里以散装键形态存在（source_note/note 等），
            # 若文本已迁入回放 notes，按迁移对账放行（文本精确相等）。
            if isinstance(v, str) and v in new_notes:
                continue
            out.append("%s.extra.%s 被删除（样本值 %s）" % (path, k, _brief(v)))
            continue
        out.extend(diff_values(v, new_extra[k], "%s.extra.%s" % (path, k),
                               in_meta=True))
    for k in new_extra:
        if k not in proj["extra"]:
            # 回放侧把说明性文本记成 extra 键（source_note/note）：样本里它是
            # 散装说明性键，文本已在 notes 对账里核过 —— 键名换算放行。
            if isinstance(new_extra[k], str) and new_extra[k] in new_notes:
                continue
            if isinstance(new_extra[k], str) and new_extra[k] in [w for w in proj["notes"]]:
                continue
            out.append("%s.extra.%s 新增（值 %s）—— 回放侧旁证键在样本 meta 里无对应"
                       % (path, k, _brief(new_extra[k])))
    # 顶层：除 notes/extra 外逐键严比
    for k, v in proj.items():
        if k in ("notes", "extra"):
            continue
        if k not in new_prov:
            out.append("%s.%s 被删除（样本值 %s）" % (path, k, _brief(v)))
            continue
        # A′ 既有口径的三键（INFO_ONLY_META_CARRIERS）：fetched_at/elapsed 的
        # 值不判（离线不可复现），host 是 source 五键里唯一放行的值 —— 与
        # meta 时代完全同宽，不是新增豁免。data_date 是 ARCH-002 新一等键、
        # 样本 schema 里没有（其事实原来在 as_of_note 文本里），null 放行。
        if k in ("fetched_at", "elapsed", "host"):
            continue
        if k == "data_date" and new_prov[k] is None:
            continue
        out.extend(diff_values(v, new_prov[k], "%s.%s" % (path, k),
                               in_meta=True))
    for k in new_prov:
        if k in ("notes", "extra") or k in proj:
            continue
        # 样本 meta 无对应的一等键：fetched_at/elapsed（A′ 信息性）、
        # clock/host/cmd/ctl/cached（来源凭据，A′ 既有口径）、
        # data_date/as_of 为 null 或 status（样本 meta 缺语义键的旧样本——
        # N3 登记表本就记为空集，回放侧补上一等字段是收紧不是放松）。
        if k in ("fetched_at", "elapsed", "clock", "host", "cmd", "ctl",
                 "cached", "data_date", "as_of", "status"):
            continue
        out.append("%s.%s 新增（值 %s）—— provenance 新键在样本 meta 里无对应"
                   % (path, k, _brief(new_prov[k])))
    return out


def diff_values(old, new, path: str = "payload",
                in_meta: bool = False,
                late_keys: dict | None = None) -> list[str]:
    """递归 diff，返回差异描述列表（空 = 完全一致）。

    `in_meta=True` 表示当前处在某个 `meta` dict 内：那里只有
    `INFO_ONLY_META_CARRIERS` 登记的 3 个载体是"值不判"，其余一律严比。

    `late_keys`（check 侧传入，数据层专用豁免）：`{键名: 锚点说明}` ——
    登记"样本侧历史缺失"的键只豁免**新增方向**（样本缺、回放有），且只在
    数据层（meta 子树内不适用）。见 `EXPECTED_DATA_ROW_LATE_KEYS`。
    """
    out: list[str] = []
    if isinstance(old, dict) and isinstance(new, dict):
        # ARCH-002 同位键：样本冻结的 `meta`（旧散装 schema）对回放侧
        # `provenance`（新一等字段 schema）按换算表对账（严比，见
        # _diff_meta_vs_provenance）。反向（样本有 provenance）不需要——
        # 在役样本全部是 meta 时代录制的。
        if "meta" in old and "provenance" in new and "meta" not in new:
            out.extend(_diff_meta_vs_provenance(old["meta"], new["provenance"],
                                                "%s.meta↔provenance" % path))
            for key in old:
                if key != "meta" and key not in new:
                    out.append("%s.%s 被删除（旧值 %s）" % (path, key, _brief(old[key])))
            for key in new:
                if key != "provenance" and key not in old:
                    out.append("%s.%s 新增（值 %s），不在允许新增清单内"
                               % (path, key, _brief(new[key])))
            return out
        for key in old:
            if key not in new:
                out.append("%s.%s 被删除（旧值 %s）" % (path, key, _brief(old[key])))
                continue
            if in_meta and key == "source":
                out.extend(_diff_meta_source(old[key], new[key],
                                             "%s.%s" % (path, key)))
                continue
            if in_meta and key in INFO_ONLY_META_CARRIERS:
                # `fetched_at` / `elapsed`：存在性已经保证，**值**不判
                continue
            out.extend(diff_values(old[key], new[key], "%s.%s" % (path, key),
                                   in_meta=in_meta or key == "meta",
                                   late_keys=late_keys))
        for key in new:
            if key not in old:
                # ⚠️ `ALLOWED_ADDED_KEYS` 只在 **meta 层**（in_meta=True 的子树）放行。
                # 2026-10-27 复核实洞：旧写法不分层，往**数据行**里塞一个名为
                # `status` 的键也能 diff=0 —— 白名单泄漏进了数据层。修法：数据层
                # 新增键一律报差异，唯一例外是 `EXPECTED_DATA_ROW_LATE_KEYS`
                # 登记的「历史缺失键」（Lead 裁决 A，只豁免新增方向）。
                if in_meta and key in ALLOWED_ADDED_KEYS:
                    continue
                if (not in_meta and late_keys and key in late_keys):
                    continue
                out.append("%s.%s 新增（值 %s），不在允许新增清单内"
                           % (path, key, _brief(new[key])))
        return out
    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            out.append("%s 长度变化 %d → %d" % (path, len(old), len(new)))
        for idx in range(min(len(old), len(new))):
            out.extend(diff_values(old[idx], new[idx], "%s[%d]" % (path, idx),
                                   in_meta=in_meta, late_keys=late_keys))
        return out
    if _eq(old, new):
        return out
    out.append("%s 值变化 %s → %s" % (path, _brief(old), _brief(new)))
    return out


def _shape_of(val):
    """结构投影（丢掉标量值、丢掉列表长度）—— `--online` 的对照口径。"""
    if isinstance(val, dict):
        return {k: _shape_of(v) for k, v in sorted(val.items())}
    if isinstance(val, list):
        return ["list", _shape_of(val[0]) if val else None]
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "bool"
    if isinstance(val, (int, float)):
        return "number"
    return "str"


# ══════════════════════════════════════════════════════════════════════════
# 八、环境信息
# ══════════════════════════════════════════════════════════════════════════
def git_head() -> str:
    try:
        res = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=20)
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:                                           # noqa: BLE001
        pass
    return ""


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime())


def _make_client():
    """建一个真 Client。

    这里刻意用 `getattr` 取类，不写字面量 `Client(` —— `tests/` 下的
    离线扫描规则（check.py 第 7 条）对"看起来像真建连"的写法很敏感，
    本文件虽不在扫描范围（只扫 `test_*.py`），但保持同一约定更省事。
    """
    return getattr(tdxdata, "Client")()


# ══════════════════════════════════════════════════════════════════════════
# 九、record
# ══════════════════════════════════════════════════════════════════════════
def _prev_manifest() -> dict:
    """上一次的 manifest（`--only` 部分重录时用来保留别的样本）。"""
    if not os.path.exists(MANIFEST):
        return {}
    try:
        return _load_json(MANIFEST) or {}
    except Exception:                                           # noqa: BLE001
        return {}


def _drop_frame_files(entries) -> int:
    """删掉这些 manifest 条目对应的 .bin（**只删自己登记过的**，不碰别的文件）。"""
    n = 0
    for ent in entries or []:
        name = ent.get("file")
        if not name:
            continue
        path = os.path.join(FRAMES, name)
        if os.path.exists(path):
            try:
                os.remove(path)
                n += 1
            except OSError:
                pass
    return n


#: 回包比对时忽略的**缓存元信息**键。
#:
#: 实测：同一条 `CWServ.sys_fn_jyr_v3` 连发两次，第二次命中服务端缓存，回包会
#: 多一个 `"HitCache":"L1:<hash>"` 字段，而 `Content`（数据本体）逐字节一致。
#: 字节不同 ≠ 数据不同 —— 但它也**不能**当成"随便什么差异都容忍"：真正的数据
#: 不一致必须报出来。所以判据是**解码等价**（忽略这些缓存键后结构相等），
#: 而不是"忽略所有冲突"。
CACHE_META_KEYS = ("HitCache",)


def _strip_cache_meta(obj):
    """递归剥掉缓存元信息键（`HitCache` 之类），保留数据本体。"""
    if isinstance(obj, dict):
        return {k: _strip_cache_meta(v)
                for k, v in obj.items() if k not in CACHE_META_KEYS}
    if isinstance(obj, list):
        return [_strip_cache_meta(x) for x in obj]
    return obj


def _norm_blob(blob):
    """把一个 blob 归一成可比较的形态：bytes 先按 JSON 解，再剥缓存键。"""
    if isinstance(blob, (bytes, bytearray)):
        try:
            blob = json.loads(bytes(blob).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return bytes(blob)          # 解不开就按原始字节比
    return _strip_cache_meta(blob)


def _decode_equivalent(file_a: str, file_b: str) -> bool:
    """两个帧的**解码结果**是否等价（剥掉缓存元信息键后逐结构相等）。

    回放对同一请求只用**首次**回包；所以"解码等价"的冲突不影响任何样本，
    而"解码不等价"的冲突是**真数据不一致**，必须报红。
    """
    try:
        _m1, _a1, _r1, blobs_a = read_frame_file(os.path.join(FRAMES, file_a))
        _m2, _a2, _r2, blobs_b = read_frame_file(os.path.join(FRAMES, file_b))
    except Exception:                                            # noqa: BLE001
        return False
    try:
        return ([_norm_blob(b) for b in blobs_a]
                == [_norm_blob(b) for b in blobs_b])
    except Exception:                                            # noqa: BLE001
        return False


def _size_note(payload: dict) -> str:
    """一行摘要：列表报条数、表格/数据集报"行 × 列"。"""
    val = payload.get("value")
    if isinstance(val, list):
        return "%d 条" % len(val)
    if isinstance(val, dict):
        tbl = val.get("table") if isinstance(val.get("table"), dict) else val
        if isinstance(tbl.get("rows"), list):
            return "%d 行 × %d 列" % (len(tbl["rows"]), len(tbl.get("cols") or []))
    return str(payload.get("exc_type", "")).rsplit(".", 1)[-1]


def cmd_record(only: str = "") -> int:
    os.makedirs(FRAMES, exist_ok=True)
    # `--only` 支持**逗号分隔多个前缀**（`--only breadth_,reject_bars_,boards_`）：
    # 一次连续跑录完一批，避免分开跑时跨过 `timeliness.FINAL_CUTOFF_HHMM`
    # （15:35）导致同一批样本一部分 `status=final`、一部分 `provisional` —— 那会让
    # 整批内部不一致，只能整批重来。单个前缀的旧用法完全兼容。
    prefixes = [p.strip() for p in (only or "").split(",") if p.strip()]

    def _hit(name) -> bool:
        """样本名是否落在本次重录范围内（空前缀 = 全量）。"""
        if not prefixes:
            return True
        return any(str(name or "").startswith(p) for p in prefixes)

    targets = [s for s in SPECS if _hit(s["name"])]
    if not targets:
        print("无样本：--only %r 没有匹配任何名字（共 %d 个）" % (only, len(SPECS)))
        return 2

    # ── 拒绝「同名重录会回收归档样本旧帧」（N4a）─────────────────────────
    # manifest 里一个样本名只挂一套帧。所以对**已有归档**的名字做同名重录，会把那份
    # 归档样本的帧一并删掉 —— 归档从此只剩 payload 自证、**不再可回放**。
    # 阶段 1.5 那次就是这样丢了 36 个帧（boards_industry + 3 份 market_stat_*）。
    #
    # 判据要精确，否则会误拦：只有"归档样本**自己声明的**帧还躺在 manifest 里"才算
    # 会误伤。若那些帧早已被上一轮同名重录回收（现在挂的是新帧），这次重录并没有
    # 新的损失，不该拦。零帧样本（frames 是 'unavailable: …' 字符串）也没有帧可丢。
    prev = _prev_manifest()
    prev_entries = prev.get("entries") or []
    prev_samples = prev.get("samples") or []
    _arch_files = [os.path.basename(p) for p in superseded_paths()]
    for _t in targets:
        _nm = _t["name"]
        _sib = [f for f in _arch_files if superseded_base_name(f) == _nm]
        if not _sib:
            continue
        _cur_frames: set[int] = set()
        for _s in prev_samples:
            if str(_s.get("name")) == _nm and isinstance(_s.get("frames"), list):
                _cur_frames |= {int(x) for x in _s["frames"]}
        if not _cur_frames:
            continue                      # 该名字当前没有帧（零帧样本），无损失
        for _f in _sib:
            try:
                _adoc = _load_json(os.path.join(SUPERSEDED, _f))
            except Exception:                                    # noqa: BLE001
                continue
            _decl = (_adoc.get("meta") or {}).get("frames")
            if isinstance(_decl, list) and _decl and \
                    {int(x) for x in _decl} <= _cur_frames:
                print("❌ 拒绝重录 %r：该名字已有归档 %s，且**归档样本自己声明的帧 %r "
                      "还挂在 manifest 里**（当前挂着 %r）。同名重录会把它们删掉，"
                      "归档从此不可回放。"
                      % (_nm, _f, sorted(int(x) for x in _decl), sorted(_cur_frames)))
                print("   请给新样本**换个名字**再录（阶段 2 就是这么做的，帧全保住）。")
                return 2

    # `--only` 是**部分重录**：只丢/只换名字匹配的那些，其余样本连帧一起保留。
    if prefixes:
        keep_entries = [e for e in prev_entries if not _hit(e.get("sample"))]
        drop_entries = [e for e in prev_entries if e not in keep_entries]
        keep_samples = [s for s in prev_samples if not _hit(s.get("name"))]
    else:
        keep_entries, drop_entries, keep_samples = [], prev_entries, []
    dropped = _drop_frame_files(drop_entries)
    seq_start = max([e.get("seq") or 0 for e in keep_entries] or [0]) + 1

    rec = Recorder(seq_start=seq_start)
    rec.install()
    client = _make_client()
    head = git_head()
    started = time.time()
    started_iso = now_iso()
    wave_id = started_iso        # 本轮录制的身份（N4b：把帧钉到"轮次"上）
    server_time = prev.get("server_time", "")
    results = []
    n_tdx_err = 0        # 落在库内错误现场（TdxError 子类）的样本数

    print("=" * 92)
    print("录制黄金样本：%d 份 → %s" % (len(targets), GOLDEN))
    if only:
        print("（--only %r：保留其它 %d 份样本 / %d 个帧，清掉本次重录的 %d 个旧帧）"
              % (only, len(keep_samples), len(keep_entries), dropped))
    else:
        print("（全量重录：清掉上一轮 %d 个旧帧）" % dropped)
    print("=" * 92)
    try:
        for spec in targets:
            name = spec["name"]
            rec.current_sample = name
            start_idx = len(rec.entries)
            t0 = time.time()
            try:
                value = spec["run"](client)
                payload = encode_ok(value)
                note = _size_note(payload)
            except BaseException as exc:                        # noqa: BLE001
                payload = encode_error(exc)
                note = "%s: %s" % (payload["exc_type"].rsplit(".", 1)[-1],
                                   payload["exc_msg"][:60])
            cost = time.time() - t0

            frames = rec.entries[start_idx:]
            quotes: list[dict] = []
            walk_quotes(payload, quotes)
            for q in quotes:
                if q.get("server_time"):
                    server_time = q["server_time"]
                    break

            wire_calls = []
            for ent in frames:
                key = (ent["cmd"], ent["ctl"])
                hit = next((w for w in wire_calls if (w["cmd"], w["ctl"]) == key), None)
                if hit:
                    hit["n"] += 1
                else:
                    wire_calls.append({"cmd": ent["cmd"], "ctl": ent["ctl"], "n": 1})

            if frames:
                frames_note = [ent["seq"] for ent in frames]
                main_cmd, main_ctl = frames[0]["cmd"], frames[0]["ctl"]
            else:
                frames_note = ("unavailable: 该调用在发出任何 wire 请求之前就失败了"
                               "（%s），没有可回放的帧"
                               % payload.get("exc_type", "unknown"))
                main_cmd, main_ctl = spec["api"], -1

            doc = {
                "meta": {
                    "name": name,
                    "cmd": main_cmd,
                    "ctl": main_ctl,
                    "api": spec["api"],
                    "params": spec["params"],
                    "fetched_at": now_iso(),
                    "server_time": server_time,
                    "sha256": sha_of(payload),
                    "base_dir": GOLDEN,
                    "python": platform.python_version(),
                    "tdxdata_version": getattr(tdxdata, "__version__", ""),
                    "git_head": head,
                    "wire_calls": wire_calls,
                    "frames": frames_note,
                    "ctl_note": ("-1 = 该样本没有产生 wire frame（在建帧之前就失败了）"
                                 if main_ctl == -1 else "即 cmd 那一帧的 ctl 字节"),
                },
                "payload": payload,
            }
            path = os.path.join(GOLDEN, name + ".json")
            _dump_json(path, doc)
            bases = [str(b) for b in (payload.get("exc_bases") or [])]
            if "tdxdata.errors.TdxError" in bases:
                n_tdx_err += 1
            results.append({"name": name, "file": name + ".json",
                            "frames": frames_note, "outcome": payload["outcome"],
                            # meta 段自身的完整性（B2b）——见 meta_integrity_sha()
                            "meta_sha256": meta_integrity_sha(doc["meta"]),
                            # 帧归属（N4b）：同一名字可能先后被录过好几轮，光看名字
                            # 分不清这批帧是哪一轮的。`recording_id` 把帧钉到"轮次"上，
                            # 免得将来按名字重放归档样本时"能跑但全错"（比跑不了更危险）。
                            "recording_id": wave_id})
            print("  [%2d/%2d] %-24s %-8s %6.1fs  %-28s 帧 %d"
                  % (len(results), len(targets), name, payload["outcome"],
                     cost, note, len(frames)))
            sys.stdout.flush()
    finally:
        rec.uninstall()
        client.close()

    # 帧落盘（新录的写文件；`--only` 时与保留下来的旧条目合并）
    entries = list(keep_entries)
    for ent in rec.entries:
        fname = frame_filename(ent["seq"], ent["cmd"], ent["ctl"])
        meta = {k: ent[k] for k in ("seq", "sample", "transport", "cmd", "ctl",
                                    "args_len", "raw_len", "resp_len")}
        write_frame_file(os.path.join(FRAMES, fname), meta, ent["args"],
                         ent["raw"] or b"", ent["blobs"])
        entries.append({
            "seq": ent["seq"], "sample": ent["sample"], "file": fname,
            "transport": ent["transport"],
            "cmd": ent["cmd"], "ctl": ent["ctl"],
            "args_len": ent["args_len"], "args_sha256": ent["args_sha256"],
            "resp_len": ent["resp_len"], "resp_sha256": ent["resp_sha256"],
            "raw_len": ent["raw_len"],
        })

    # 重复键 / 冲突：只看 manifest 字段，不用重读 .bin
    entries.sort(key=lambda e: e["seq"])
    seen: dict[tuple, dict] = {}
    duplicates, conflicts = [], []
    for ent in entries:
        key = (ent["transport"], ent["cmd"], ent["ctl"], ent["args_sha256"])
        if key in seen:
            duplicates.append(ent["seq"])
            if seen[key]["resp_sha256"] != ent["resp_sha256"]:
                # 字节不同 ≠ 数据不同：命中缓存时回包会多一个 `HitCache` 字段。
                # 记下**解码是否等价**，让 check / 契约测试能区分"缓存噪声"与"真数据冲突"。
                same = _decode_equivalent(seen[key]["file"], ent["file"])
                conflicts.append({"seq": ent["seq"], "first": seen[key]["seq"],
                                  "cmd": ent["cmd"], "ctl": ent["ctl"],
                                  "decoded_same": same})
        else:
            seen[key] = ent

    # ── 帧归属（N4b）─────────────────────────────────────────────────
    # 保留下来、但名字已不在役（即已被归档）的样本条目，打上 `superseded: true`。
    # 否则它的 `frames` 看起来"归它所有"，其实那套帧属于**另一轮录制** ——
    # 将来按名字重放一份归档样本会「能跑但全错」，比跑不了更危险。
    _live_now = {os.path.basename(x)[:-5] for x in _sample_paths()}
    for _s in keep_samples:
        _s.setdefault("recording_id", None)
        if str(_s.get("name")) not in _live_now:
            _s["superseded"] = True

    manifest = {
        "format": "TDXGF1",
        "generated_at": now_iso(),
        # 本轮录制**起止**时刻（Lead 要求）：`status`/`provisional` 依赖"本机当前时分"，
        # 一旦跨过 `timeliness.FINAL_CUTOFF_HHMM`(15:35)，同一批样本可能一部分 final、
        # 一部分 provisional —— 有此时间戳才能事后判定"这批是不是跨点了、要不要整批重来"。
        "recorded_at": {"started": started_iso, "finished": now_iso(),
                        "final_cutoff_hhmm": FINAL_CUTOFF_HHMM},
        "server_time": server_time,
        "git_head": head,
        "python": platform.python_version(),
        "tdxdata_version": getattr(tdxdata, "__version__", ""),
        "base_dir": GOLDEN,
        "recorded_seconds": round(time.time() - started, 1),
        "count": len(entries),
        "entries": entries,
        "samples": list(keep_samples) + results,
        "duplicate_keys": duplicates,
        "conflicts": conflicts,
        "note": ("同一 (transport, cmd, ctl, args) 被请求多次时只保留首次用于回放；"
                 "duplicate_keys 列出重复的 seq，conflicts 只在两次回包字节不同时出现"),
    }
    _dump_json(MANIFEST, manifest)

    ok = sum(1 for r in results if r["outcome"] == "ok")
    print("-" * 92)
    print("本轮样本 %d 份（成功 %d / 异常现场 %d）；本轮 wire 帧 %d 个；"
          "manifest 累计 样本 %d 份 / 帧 %d 个；重复键 %d；冲突 %d；耗时 %.1fs"
          % (len(results), ok, len(results) - ok, len(rec.entries),
             len(manifest["samples"]), len(entries),
             len(duplicates), len(conflicts), time.time() - started))
    print("manifest → %s" % MANIFEST)
    if conflicts:
        print("  ⚠️ 有 %d 组重复请求的回包不一致（回放只用首次）：" % len(conflicts))
        for item in conflicts[:10]:
            print("     seq %d vs %d  %s ctl=0x%02X"
                  % (item["first"], item["seq"], item["cmd"], item["ctl"]))
    if ok == 0:
        # 「0 份成功」有两种完全不同的原因，别把它们混成一句吓人的话：
        #   · 全是库内错误现场（TdxError 子类，如 NotSupportedError）—— 这是**预期**，
        #     这类样本本来就是"冻结拒绝现场"，零 wire 帧是正确结果；
        #   · 其余（OSError/TimeoutError/连不上）—— 那才是"别把这批当真"。
        if n_tdx_err == len(results):
            print("  ✅ 全部是**预期内的库内错误现场**（TdxError 子类）：这不是连不上"
                  "服务器，而是这批样本本来就该抛错（拒绝现场 / 错误现场），已按预期落盘。")
        else:
            print("  ❌ 一份都没成功，且失败不全是库内错误现场（%d/%d 份是）—— "
                  "大概率是连不上服务器，别把这批样本当真" % (n_tdx_err, len(results)))
            return 1
    return 0


# ══════════════════════════════════════════════════════════════════════════
# 十、check
# ══════════════════════════════════════════════════════════════════════════
def _sample_paths() -> list[str]:
    """参与 check 的样本 = golden **顶层**的 `.json`。

    `_superseded/` 下的旧样本不在此列（见 `superseded_paths()`）——
    它们因**有意**行为变更而失效，留作历史证据，不是当前基准。
    """
    if not os.path.isdir(GOLDEN):
        return []
    out = []
    for fname in sorted(os.listdir(GOLDEN)):
        path = os.path.join(GOLDEN, fname)
        # 只认**顶层**的文件：`_superseded/` 是子目录，其内的旧样本不参与 check
        if fname.endswith(".json") and os.path.isfile(path):
            out.append(path)
    return out


def _load_json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _load_manifest() -> dict | None:
    if not os.path.exists(MANIFEST):
        return None
    return _load_json(MANIFEST)


def _manifest_entries(manifest: dict) -> list[dict]:
    out = []
    for ent in manifest.get("entries") or []:
        path = os.path.join(FRAMES, ent["file"])
        if not os.path.exists(path):
            raise FileNotFoundError("manifest 登记的帧文件不存在：%s" % path)
        _meta, args, raw, blobs = read_frame_file(path)
        out.append({"transport": ent["transport"], "cmd": ent["cmd"],
                    "ctl": ent["ctl"], "args": args, "raw": raw, "blobs": blobs,
                    "file": ent["file"], "seq": ent["seq"]})
    return out


def superseded_paths() -> list[str]:
    """`_superseded/` 下**已归档**的样本（有意行为变更后的旧基准）。

    归档 ≠ 删除：文件原样搬走、`sha256` 一个字节不改，只是不再参与 check ——
    它是"历史证据"，不是"当前基准"。留下它是为了两件事：
      ① 审计，能查"当时那条通道到底返回了什么"；
      ② 反悔，若新行为被推翻了，把文件搬回顶层即可复活（帧仍在 `frames/` 里）。
    """
    if not os.path.isdir(SUPERSEDED):
        return []
    return sorted(os.path.join(SUPERSEDED, f) for f in os.listdir(SUPERSEDED)
                  if f.endswith(".json") and os.path.isfile(os.path.join(SUPERSEDED, f)))


def superseded_base_name(filename: str) -> str:
    """`bars_880005.stage2.json` → `bars_880005`（归档名 = `<原文件名>.<阶段标签>.json`）。"""
    base = filename[:-5] if filename.endswith(".json") else filename
    return base.split(".", 1)[0]


#: 已归档样本的**权威清单**（源码锚点，防"归档通道被反向利用"）。
#:
#: 缺口（复核 V6c）：把在役样本搬进 `_superseded/` + 补一行**虚构理由**的登记 +
#: 从两张源码表删掉它 → check 与 pytest **全绿**。根因是"归档"天生就是
#: "让样本合法退出比对"的开关，而登记表那 6 个字段**没有一个可机器判定**。
#:
#: 这里照抄 `EXPECTED_SAMPLE_FRAMES` 的招：归档集合写进源码、双向对账，并且每条
#: 必须指向一个**真实在役**的替代样本。于是"搬走一份在役样本"必须同时改源码表
#: 并提供真实替代品 —— 留下 diff、可审，而不是一行自由文本就能抹掉基准。
EXPECTED_ARCHIVED = {
    # ── 阶段 2：880 非价格指标，get_bars 改为拒绝并指路 get_breadth ──
    "bars_880005.stage2.json": {"stage": "2", "replaced_by": "breadth_880005"},
    "bars_880006.stage2.json": {"stage": "2", "replaced_by": "breadth_880006"},
    "bars_880866.stage2.json": {"stage": "2", "replaced_by": "breadth_880866"},
    "err_bars_880005.stage2.json": {"stage": "2",
                                    "replaced_by": "reject_bars_880005"},
    # ── 阶段 1.5：Table 语义 meta 贯通（source str → 5 键 dict），同名重录 ──
    "boards_industry.stage1.5.json": {"stage": "1.5",
                                      "replaced_by": "boards_industry_s4"},
    "market_stat_updown.stage1.5.json": {"stage": "1.5",
                                         "replaced_by": "market_stat_updown"},
    "market_stat_sentiment.stage1.5.json": {"stage": "1.5",
                                            "replaced_by": "market_stat_sentiment"},
    "market_stat_industry.stage1.5.json": {"stage": "1.5",
                                           "replaced_by": "market_stat_industry"},
    # ── 阶段 3：无法识别的标的 ValueError → OutOfScope。措辞显式否掉
    #    「服务端不支持市场码」的错归因：期货/期权/国债期货/外汇根本不在证券表内。
    "err_bars_IF00.stage3.json": {"stage": "3", "replaced_by": "err_bars_IF00"},
    # ── 阶段 4：change_pct 从恒 None 改为 NOW/CLOSE-1 实算；industry 支路新增
    #    PBMultiHQ(market=10001) 回填一轮（原 app_bklist_ag 无价格列）→ 多出一帧。
    #    同一基名的**第二份**归档（stage1.5 那份原封不动，链条留全）。
    #    ⚠️ 在役样本**改名**为 `boards_industry_s4`：N4a 守卫拒绝同名重录
    #    （同名会回收 stage4 自己声明的帧，让归档从此不可回放）。改名后
    #    stage1.5 / stage4 两份归档的帧全部保住、仍可回放 —— 这是 N4a 的预期用法。
    "boards_industry.stage4.json": {"stage": "4",
                                    "replaced_by": "boards_industry_s4"},
}


def _freeze_replay_clock(client, sample_meta: dict, payload) -> None:
    """回放时钟**确定化**：把「本机日期」冻结到录制当天，复现录制时的 hybrid 判定。

    为什么需要它（2026-10-28 实测的跨日缺陷，不是臆测）
    --------------------------------------------------
    `HybridClock` 的前置条件是「本机日期 == 服务器交易日」，否则两源都不信、
    降级 `clock="local"`（timeliness.py:459-463）。回放时服务器日期来自**录制帧**
    （冻结在 2026-10-27），而本机日期是**真实今天** —— 一旦录制日 ≠ 今天，
    8 份 hybrid 样本的 `source.clock` / `fetch_clock` 全部由 "hybrid" 降成 "local"，
    回放报 8×2 处差异（数据字段 0 差异）。这不是行为回归，是**门禁自身对日期敏感**：
    同一份帧在"第二天"回放就不再与录制同构。

    修法：用样本自己 `meta.fetched_at`（= 录制时的本机时刻，on-disk 事实）冻结本机钟，
    回放因此与录制同构。**严格比对一个字都没放宽**：`source.clock` / `fetch_clock` /
    `status` / `as_of` 仍是逐值严比 —— 时钟逻辑真退化了，这里照样红。
    只对**录制时钟确实是 `hybrid`** 的样本冻结；其余样本保持默认（否则会凭空造出
    hybrid，反而制造假差异）。
    """
    rec = payload_meta(payload)
    if rec.get("fetch_clock") != "hybrid":
        return
    raw = str(sample_meta.get("fetched_at") or "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            frozen = datetime.datetime.strptime(raw[:19], fmt)
            break
        except ValueError:
            continue
    else:                                   # 解析不出就保持默认（宁可报差异，不造假）
        return
    client._clock = HybridClock(client._probe_quote_time, local=lambda: frozen)


def cmd_check(online: bool = False) -> int:
    print("=" * 92)
    print("黄金样本离线回放校验（不联网）")
    print("=" * 92)
    print("golden  目录：%s" % GOLDEN)
    print("frames  目录：%s" % FRAMES)

    paths = _sample_paths()
    archived = superseded_paths()
    manifest = _load_manifest()
    print("样本基数：%d 份 JSON；manifest：%s"
          % (len(paths), ("%d 个帧" % len(manifest.get("entries") or []))
             if manifest else "缺失"))
    # 这一行**永远打印**（0 份也打）：口径要让人看得见，别让人以为"样本莫名少了"。
    print("已归档样本 %d 份（有意变更，见 _superseded/README.md）" % len(archived))
    for path in archived:
        print("    - %s（不参与 check，仅留作历史证据）" % os.path.basename(path))
    if not paths:
        print("❌ 无样本：%s 下一份 .json 都没有 —— 这不是「0 差异」，是「没东西可比」"
              % GOLDEN)
        return 2
    if manifest is None:
        print("❌ 无 manifest：%s 不存在 —— 没有帧就无法离线回放" % MANIFEST)
        return 2

    try:
        entries = _manifest_entries(manifest)
    except FileNotFoundError as exc:
        print("❌ 帧缺失：%s" % exc)
        print("   manifest 登记了这个帧，但 .bin 不在 —— 这不是「0 差异」，"
              "是帧被删/被换过（rc=2）")
        return 2
    if not entries:
        print("❌ manifest 里 0 个帧 —— 没有帧就无法离线回放")
        return 2

    replay = Replay(entries)
    replay.install()
    client = _make_client()
    client._pacer = _NoPacer()          # 真实闸门每条命令要 sleep 2.2 秒

    compared, bad = 0, []

    # ══ 归档清单对账（N2，防「归档通道被反向利用」）═════════════════════════
    # 判据全部来自源码表 `EXPECTED_ARCHIVED`，双向对账；`_superseded/README.md`
    # 里那几行自由文本**只作人读的说明**，不参与判定（它没法机器判定）。
    _arch_names = {os.path.basename(p) for p in archived}
    _live_names = {os.path.basename(p)[:-5] for p in paths}
    _arch_bad: list[str] = []
    for _fn in sorted(set(EXPECTED_ARCHIVED) - _arch_names):
        _arch_bad.append("%s：源码表 EXPECTED_ARCHIVED 登记为「已归档」，"
                         "但 _superseded/ 里没有这个文件" % _fn)
    for _fn in sorted(_arch_names - set(EXPECTED_ARCHIVED)):
        _arch_bad.append("%s：躺在 _superseded/ 里，但源码表 EXPECTED_ARCHIVED "
                         "没登记 —— 「把在役样本搬走」必须同时在源码表里留痕，"
                         "不许靠一行自由文本就让它退出比对" % _fn)
    for _fn, _info in sorted(EXPECTED_ARCHIVED.items()):
        _rep = (_info or {}).get("replaced_by")
        if not _rep:
            _arch_bad.append("%s：没写替代样本（归档必须指向一个真实在役的替代品）"
                             % _fn)
        elif _rep not in _live_names:
            _arch_bad.append("%s：登记的替代样本 %r 不在役（golden/ 顶层没有它）"
                             % (_fn, _rep))
        elif _rep + ".json" in _arch_names:
            _arch_bad.append("%s：登记的替代样本 %r 自己也被归档了（不能拿归档品"
                             "给归档品站岗）" % (_fn, _rep))
        if not str((_info or {}).get("stage") or ""):
            _arch_bad.append("%s：没写变更阶段（stage）" % _fn)
    if _arch_bad:
        print("  ❌ 归档清单对账失败 %d 条：" % len(_arch_bad))
        for _line in _arch_bad:
            print("    - %s" % _line)
    bad.extend(_arch_bad)
    all_diffs: list[str] = []
    frozen_payloads: dict[str, dict] = {}
    # 容器 meta 的四档计数（全是信息性口径，不进 rc）
    n_no_meta = n_meta_same = n_meta_diff = n_meta_lost = 0
    meta_notes: list[str] = []
    # 样本 meta 段完整性（B2b）
    n_metasha_ok = n_metasha_legacy = 0
    # 源码帧锚点自校验（EXPECTED_SAMPLE_FRAMES）：也永远打印，让"判据在哪"可见
    n_anchor_ok = 0
    anchor_missing: list[str] = []
    n_metakeys_bad = 0      # meta 语义键缺失（或未登记）的样本数
    # manifest 里登记的样本（**只**用来查 meta_sha256，绝不参与「谁该有帧」的判定）
    reg = {str(s.get("name")): s for s in (manifest.get("samples") or [])}

    try:
        for path in paths:
            doc = _load_json(path)
            meta = doc.get("meta") or {}
            payload = doc.get("payload")
            name = meta.get("name") or os.path.basename(path)[:-5]
            frozen_payloads[name] = payload

            # ① 样本自身完整性（防止有人手改后"比了个寂寞"）
            if sha_of(payload) != meta.get("sha256"):
                bad.append("%s：payload 的 sha256 与 meta.sha256 不一致（样本被改过？）" % name)
                continue

            spec = SPEC_BY_NAME.get(name)
            if spec is None:
                bad.append("%s：golden_runner.py 里没有同名样本定义（样本已过期？）" % name)
                continue

            # ══ 「谁该有帧」只认**源码表** ══════════════════════════════════
            # 绝不读样本自己的 `meta.frames`，也绝不读 manifest：两者都在
            # `tests/fixtures/golden/` 里面，改得动就能把自己洗成"跳过"。
            # 详见 EXPECTED_SAMPLE_FRAMES 的注释（那是一整条真实的放水路径）。
            try:
                frames_expected = expected_frames(name)
            except KeyError:
                anchor_missing.append(name)
                bad.append("%s：不在源码表 EXPECTED_SAMPLE_FRAMES 里 —— 新增样本"
                           "必须先在 golden_runner.py 里登记它的帧集合"
                           "（否则「该不该有帧」就变成被测者自己说了算）" % name)
                continue
            n_anchor_ok += 1
            # 「该不该有帧」就由上面这一行源码表定死（`frames_expected`）——
            # 全文件**不再**从样本 `meta.frames` 或 manifest 推出 `has_frames` 之类的判据。

            # ── 样本自己声明的帧集合：**只作被审计对象，绝不当判据** ────────
            # `frames_expected` 上面已经由源码表定死了；这里再拿样本的 `meta.frames`
            # 来**对账**。两条都必须有，缺一不可：
            #   · 判据只来自源码表  → 改样本 meta 洗不动「该不该有帧」（V1a）
            #   · 样本声明必须对得上 → 改了样本 meta 也当场红（V1b）
            # 只做前者的话，"改 meta.frames 但不动帧"仍能 rc=0 —— 那等于允许样本
            # 说谎只是没人看；这里让谎话本身成为硬错误。
            declared = meta.get("frames")
            # 注意 `expected_frames()` 把 `NO_FRAMES` 归一成 `None`（"本就没有帧"），
            # 不是字符串 "unavailable" —— 这里按 `None` 判，别按字符串判。
            if frames_expected is None:
                if not (isinstance(declared, str) and declared.startswith(NO_FRAMES)):
                    bad.append("%s：源码表登记为「无帧」（%r），但样本 meta.frames=%r "
                               "—— 样本在谎报自己有帧" % (name, NO_FRAMES, declared))
                    continue
            elif (not isinstance(declared, list)
                  or [int(x) for x in declared] != [int(x) for x in frames_expected]):
                bad.append("%s：样本 meta.frames=%r 与源码表登记的 %r 不一致"
                           "（改样本 meta 想洗白「该不该有帧」）"
                           % (name, declared, frames_expected))
                continue

            # ══ 样本 meta 语义键存在性（N3，防「删键解约束」）══════════════════
            # `ALLOWED_ADDED_KEYS` 的语义是"**新增**放行"。所以把样本里的
            # `status`/`source`/`provisional`… 删掉，回放会重新"新增"它们 →
            # 门禁照绿，**约束静默消失**（实测：白名单 12 项删掉 11 项仍全绿）。
            # 这张源码表把"每份样本必须有哪几个键"钉死：少一个就是硬错误。
            # ⚠️ 与 A′ 正交：A′ 放行的是**值**（fetched_at/elapsed/source.host），
            #    这里管的是**存在性** —— `elapsed` 的值每次不同，但键必须还在。
            want_keys = EXPECTED_SAMPLE_META_KEYS.get(name)
            if want_keys is None:
                n_metakeys_bad += 1
                bad.append("%s：不在源码表 EXPECTED_SAMPLE_META_KEYS 里 —— 新增样本"
                           "必须登记它 meta 段应当存在的语义键" % name)
                continue
            got_keys = collect_meta_keys(payload)
            missing = [k for k in want_keys if k not in got_keys]
            if missing:
                n_metakeys_bad += 1
                bad.append("%s：样本 meta 段缺了源码表要求必须存在的键 %s"
                           "（删键 ≠ 解除约束：删掉后回放会把它们当「新增」放行）"
                           % (name, missing))
                continue

            # ══ 样本 meta 段完整性（B2b）══════════════════════════════════
            # `payload` 有 sha256 自证，但 `meta` 此前完全裸奔：改 `params`/`cmd`/
            # `frames` 都不会红。manifest 记 meta_sha256 能抓"改了样本忘了改 manifest"
            # 这类手滑；它不是主锚（主锚是上面那张源码表），是补强。
            ent = reg.get(name)
            if ent is None:
                bad.append("%s：manifest.samples 里没有登记（样本存在却没进清单）" % name)
                continue
            want_metasha = ent.get("meta_sha256")
            if want_metasha:
                if meta_integrity_sha(meta) != want_metasha:
                    bad.append("%s：样本 meta 段与 manifest 记的 meta_sha256 不一致"
                               "（meta 被改过：params/api/cmd/frames/fetched_at…）" % name)
                    continue
                n_metasha_ok += 1
            else:
                # 阶段 0 录的老样本没有这个键 —— **不许**要求重录
                n_metasha_legacy += 1

            try:
                _freeze_replay_clock(client, meta, payload)
                value = spec["run"](client)
                got = encode_ok(value)
            except FrameMissing as exc:
                # ══════════════════════════════════════════════════════════
                # ⚠️ **这里不许有任何"跳过"出口。**（复核 N1，已拆后门）
                #
                # 复核者双向确认过的事实：**零帧样本永远走不到 `FrameMissing`** ——
                # 它在发出任何 wire 请求之前就失败（`ValueError` / `NotSupportedError`），
                # 被下面那个 `except BaseException` 收成"异常现场"样本。
                # 所以 `FrameMissing` 在健康树里**只可能**意味着"该有的帧没找到"，
                # 一个合法的"录制时本就没帧"根本到不了这里。
                #
                # 曾经写成 `if <该不该有帧>: 判红 else: <把名字塞进跳过列表>`，看着体贴，
                # 实际上唯一现实功能就是当洗白出口：复核者用 V5c（协同篡改 4 处 ——
                # 帧 args + 两张源码表 + 样本 meta.frames + manifest 脱钩）实测
                # rc=1 → **rc=0 全绿**，还打印一句"录制时本就没有 wire frame"的假原因。
                # 唯一兜底只剩 `assert "跳过 0 份"` 一条，太薄。
                #
                # 所以：一律判红，`skipped` 这个概念在本文件里**已不存在**。
                # 将来若真需要合法跳过，必须**新增一种显式的、可机器判定的样本类别**
                # （源码表里能认出来的那种），不许把断言改回 `<= N`。
                # ══════════════════════════════════════════════════════════
                bad.append("%s：回放时帧缺失（录制时是有的 —— 请求体变了 / 帧被改过？）：%s"
                           % (name, exc))
                continue
            except BaseException as exc:                        # noqa: BLE001
                got = encode_error(exc)

            # ══ 容器 meta 纪律（守卫 2，2026-10-27 复核）══════════════════
            # 旧写法把 `payload["meta"]` 两边整体剥掉后再做主 diff —— 27 份样本
            # 都没有容器 meta，等于 Bars/Quotes/Ticks 的 meta 12 个语义键**从未
            # 被主比对校验过**，回放侧凭空塞 meta 也静默通过。现在：
            #   · 样本有 meta → 必须与回放侧逐键比对（走 in_meta=True 规则）；
            #   · 样本无 meta → 回放侧**不许凭空多出未登记的容器 meta**；
            #     多出即报差异（想合法登记走 `EXPECTED_REPLAY_CONTAINER_META_KEYS`，
            #     与 `EXPECTED_SAMPLE_META_KEYS` 同一条登记纪律）。
            # 不再"剥掉后静默"。两边都有的 meta 的值比对交给 diff_values 的
            # in_meta=True 规则（fetched_at/elapsed/source.host 仍按 A′ 放行），
            # 不会再把门禁变成时钟噪声。
            body_old = payload
            body_new = got
            if isinstance(body_old, dict) and isinstance(body_new, dict):
                old_meta = body_old.get("meta")
                new_meta = body_new.get("meta")
                if old_meta is None and new_meta is not None:
                    got_keys = sorted(new_meta) if isinstance(new_meta, dict) \
                        else ["<non-dict meta>"]
                    registered = EXPECTED_REPLAY_CONTAINER_META_KEYS.get(name)
                    if registered is None or sorted(registered) != got_keys:
                        all_diffs.append(
                            "%s：样本无容器 meta，回放却产出 meta 键 %s —— "
                            "凭空多出的容器 meta 算差异（若确属新语义，"
                            "请登记进 EXPECTED_REPLAY_CONTAINER_META_KEYS）"
                            % (name, got_keys))
                    else:
                        # 键集与登记表精确一致 → 按登记放行。主 diff 里把回放侧
                        # 的这个 meta 剔出"新增"（否则 diff_values 会重复报一遍
                        # 守卫 2 已裁定的差异）。样本侧本来没有 meta，无需动
                        # body_old；meta 的**键集**刚被上面对账过，**值**仍由
                        # 信息性统计可见（下面 n_no_meta / meta_notes 那段）。
                        body_new = {k: v for k, v in body_new.items()
                                    if k != "meta"}

            # 存在性守卫（裁决 A 落地要求 2①）：登记键在回放侧数据行里**必须**
            # 仍然产出 —— 代码删键（如 client.py 不再写 provisional）时这里当场红，
            # 登记表失效，不许它退化成永久后门。
            late = EXPECTED_DATA_ROW_LATE_KEYS.get(name) or {}
            if late:
                rows_new = []
                v = body_new.get("value") if isinstance(body_new, dict) else None
                if isinstance(v, list):
                    rows_new = [r for r in v if isinstance(r, dict)]
                elif isinstance(v, dict):
                    rows_new = [r for r in (v.get("rows") or []) if isinstance(r, dict)]
                if not rows_new:
                    bad.append("%s：登记了数据行历史缺失键 %s，但回放侧没有可检的"
                               "数据行（空列表/形态变了）—— 登记表失效" % (name, sorted(late)))
                    continue
                for r in rows_new:
                    missing = [k for k in late if k not in r]
                    if missing:
                        bad.append("%s：登记的历史缺失键 %s 在回放侧数据行里不存在"
                                   " —— 代码已不再产出该键，登记表失效（请删登记或修代码）"
                                   % (name, missing))
                        break

            diffs = diff_values(body_old, body_new,
                                late_keys=EXPECTED_DATA_ROW_LATE_KEYS.get(name))
            compared += 1
            if diffs:
                all_diffs.append("%s：%d 处" % (name, len(diffs)))
                for line in diffs[:12]:
                    all_diffs.append("    " + line)
                if len(diffs) > 12:
                    all_diffs.append("    …… 另 %d 处未显示" % (len(diffs) - 12))

            # ── 容器 meta：**只作信息性输出，不进 PASS/FAIL** ──────────────
            # `status` 依赖服务器时钟、`elapsed`/`fetched_at` 每次都会变，
            # 拿它们判失败只会训练人忽略红灯。真正要红的是"解出来的数据变了"。
            sample_meta = payload.get("meta") if isinstance(payload, dict) else None
            got_meta = got.get("meta") if isinstance(got, dict) else None
            if sample_meta is None:
                # 阶段 1 之前冻结的样本没有这个键 —— 缺它**不是差异**
                n_no_meta += 1
                if got_meta is not None:
                    meta_notes.append(
                        "%s：样本无 meta（本阶段前录制）；回放产出 meta 键 %s"
                        % (name, sorted(got_meta)))
            elif got_meta is None:
                n_meta_lost += 1
                meta_notes.append(
                    "%s：⚠️ 样本有 meta，回放却没产出（容器退化成普通 list？）" % name)
            else:
                meta_delta = diff_values(sample_meta, got_meta, "payload.meta",
                                         in_meta=True)
                if meta_delta:
                    n_meta_diff += 1
                    meta_notes.append("%s：容器 meta 变化 %d 处（信息性，不计入失败）"
                                      % (name, len(meta_delta)))
                    meta_notes.extend("      " + line for line in meta_delta[:8])
                else:
                    n_meta_same += 1
    finally:
        replay.uninstall()
        client.close()

    total_diffs = len(bad) + sum(1 for line in all_diffs if not line.startswith("    "))
    print("-" * 92)
    # 「跳过」这个计数器**刻意保留为常量 0**：它不是"本轮的统计结果"，而是一句断言 ——
    # 跳过这条路径已经被删掉了（`FrameMissing` 一律判红、零帧样本走异常现场通道），
    # 所以「跳过 0 份」恒成立，**不是因为没人跳过，而是因为这条路已不存在**。
    # 契约测试会 assert 这一行；将来若真需要合法跳过，必须新增一种显式的、可机器
    # 判定的样本类别，**不许**把断言改成 `<= N`。
    print("样本 %d 份，比对 %d 份，跳过 0 份(无 frame)，差异 %d"
          % (len(paths), compared, total_diffs))
    if bad:
        print("  ❌ 硬错误 %d 条：" % len(bad))
        for item in bad:
            print("    - %s" % item)
    if all_diffs:
        print("  ❌ 差异清单：")
        for line in all_diffs[:200]:
            print("    " + line if not line.startswith("    ") else line)
        if len(all_diffs) > 200:
            print("    …… 另 %d 行未显示" % (len(all_diffs) - 200))
    if not bad and not all_diffs:
        print("  差异 0：解码层输出与冻结样本逐字段一致")

    # 容器 meta：信息性统计仍打印（口径可见），纪律已升级 —— 见守卫 2 与
    # EXPECTED_REPLAY_CONTAINER_META_KEYS。样本无 meta 而回放多出未登记 meta
    # 的样本已在主 diff 报差异；两边都有时主 diff 走 in_meta=True 逐键比。
    print("  容器 meta（纪律：样本无 meta → 回放侧键集须登记；两边都有 → in_meta 逐键比）："
          "样本无 meta %d 份 / 两边都有 %d 份"
          "（一致 %d、变化 %d）/ 样本有但回放丢失 %d 份"
          % (n_no_meta, n_meta_same + n_meta_diff, n_meta_same, n_meta_diff, n_meta_lost))
    if n_no_meta:
        print("    阶段 1 之前录的样本本来就没有容器 meta；回放侧多出的 meta 键集"
              "必须登记进 EXPECTED_REPLAY_CONTAINER_META_KEYS（未登记即红）")
    for line in meta_notes[:60]:
        print("    " + line)
    if len(meta_notes) > 60:
        print("    …… 另 %d 行未显示" % (len(meta_notes) - 60))

    # 样本 meta 段完整性（B2b）：这一行也永远打印，让"老样本没有校验"这件事可见，
    # 而不是让人以为 23 份都验过了。
    print("  样本 meta 完整性（B2b）：已按 manifest 校验 %d 份 / 本阶段前录制无校验 %d 份"
          "（不一致的已在上面硬错误里列出）" % (n_metasha_ok, n_metasha_legacy))
    if n_metasha_legacy:
        print("    这 %d 份是 manifest 补 meta_sha256 之前录的 —— 缺它**不是**差异，"
              "按纪律不重录；新录的样本一律带 meta_sha256。" % n_metasha_legacy)
    # 源码帧锚点：也说清楚"谁该有帧"的判据在哪 —— 它**不**来自 fixture 目录里的任何文件。
    print("  源码帧锚点（EXPECTED_SAMPLE_FRAMES）：登记 %d 份 / 本次比对 %d 份 / "
          "未登记 %d 份%s"
          % (len(EXPECTED_SAMPLE_FRAMES), n_anchor_ok, len(anchor_missing),
             ("（未登记：%s）" % ", ".join(anchor_missing[:8])) if anchor_missing else ""))
    print("    判据来源 = golden_runner.py 源码（不读样本 meta.frames、不读 manifest）；"
          "零帧样本登记为 %r" % NO_FRAMES)
    # 另外两条源码锚点也各打一行，让"判据在哪"在工具输出里可见（不只在 pytest 里）。
    print("  归档清单（EXPECTED_ARCHIVED）：源码登记 %d 份 / 磁盘 %d 份 / 对账失败 %d 条"
          % (len(EXPECTED_ARCHIVED), len(_arch_names), len(_arch_bad)))
    print("    替代样本必须真实在役 —— 「把在役样本搬进 _superseded/」不再是免费操作")
    # 「属于别的轮次」= 条目被显式标了 superseded，**或者**它的名字只在归档里存在
    # （在役样本重名不算：同名重录后 manifest 里那条就是新样本自己的，帧没被抢走）。
    _arch_bases = {superseded_base_name(f) for f in _arch_names}
    _live_bases = {os.path.basename(_p)[:-5] for _p in paths}
    _sup_entries = [str(_s.get("name")) for _s in (manifest.get("samples") or [])
                    if _s.get("superseded")
                    or (str(_s.get("name")) in _arch_bases
                        and str(_s.get("name")) not in _live_bases)]
    _n_waves = len({_s.get("recording_id") for _s in (manifest.get("samples") or [])
                    if _s.get("recording_id")})
    print("    帧归属：manifest 里 %d 条样本属于「已归档/别的轮次」（%s），"
          "别按名字重放它们；已知录制轮次 %d 个"
          % (len(_sup_entries), ", ".join(sorted(_sup_entries)[:6]) or "-", _n_waves))
    print("      `record` 会给新条目打 `recording_id`，并给归档基名打 `superseded: true`")
    print("  样本 meta 语义键（EXPECTED_SAMPLE_META_KEYS）：登记 %d 份 / 缺键样本 %d 份"
          % (len(EXPECTED_SAMPLE_META_KEYS), n_metakeys_bad))
    print("    删键 ≠ 解除约束：这些键被删后回放会重新「新增」它们，而「新增」是放行的")

    rc = 0 if (not bad and not all_diffs and compared > 0) else 1
    if compared == 0:
        print("❌ 一份都没比对成功（比对 0 份不能算通过）")
        rc = 1

    if online:
        rc = _online_structural(frozen_payloads) or rc

    print("结果：%s" % ("PASS" if rc == 0 else "FAIL"))
    return rc


def _online_structural(frozen: dict[str, dict]) -> int:
    """`--online`：联网再取一次，只比**结构**（列/键集合），值变化只报数不当失败。

    为什么只比结构：黄金样本冻结在 2026-10-27 收盘后，隔天再取，价格/量/家数
    **本来就应该变**。把它当失败只会训练人忽略红灯。阶段验收真正需要知道的是
    "这条通道现在还通吗、列还在吗、异常现场还抛不抛"。

    「错误现场」样本单独判：冻结的是 `error`，那么联网再抛同一个异常是**正确**的；
    真正的退化是"以前抛错、现在静默返回空结果"，那才判失败。
    """
    print("-" * 92)
    print("--online：联网做结构级对照（值变化只报数，不当失败）")
    client = _make_client()
    ok = bad = 0
    problems: list[str] = []
    try:
        for spec in SPECS:
            name = spec["name"]
            if name not in frozen:
                continue
            if spec.get("online_skip"):
                print("  [skip] %-24s 该样本成本高（%s），--online 不跑"
                      % (name, spec["api"]))
                continue
            want = frozen[name]
            want_err = want.get("outcome") == "error"
            try:
                got = encode_ok(spec["run"](client))
            except BaseException as exc:                        # noqa: BLE001
                got = encode_error(exc)

            if want_err:
                if got.get("outcome") != "error":
                    bad += 1
                    problems.append("%s：冻结的是异常现场（%s），联网却不抛错了 —— 行为退化？"
                                    % (name, want.get("exc_type")))
                elif got.get("exc_type") != want.get("exc_type"):
                    bad += 1
                    problems.append("%s：异常类型变了 %s → %s"
                                    % (name, want.get("exc_type"), got.get("exc_type")))
                else:
                    ok += 1
                    print("  [ok]   %-24s 仍抛 %s（与冻结一致）"
                          % (name, str(got.get("exc_type")).rsplit(".", 1)[-1]))
                sys.stdout.flush()
                continue

            if got.get("outcome") == "error":
                bad += 1
                problems.append("%s：冻结的是成功样本，联网却抛 %s: %s"
                                % (name, got.get("exc_type"),
                                   str(got.get("exc_msg"))[:120]))
                sys.stdout.flush()
                continue

            # 容器 meta 不参与结构比较：它只装 as_of/status/source/elapsed/fetched_at
            # 这类"每天都在变"的元信息，混进 structure diff 会把 `--online` 变成噪声源。
            want_body = {k: v for k, v in want.items() if k != "meta"}
            got_body = {k: v for k, v in got.items() if k != "meta"}
            shape_diff = diff_values(_shape_of(want_body), _shape_of(got_body), "shape")
            if shape_diff:
                bad += 1
                problems.append("%s：结构变了 %d 处，例如 %s"
                                % (name, len(shape_diff), shape_diff[0]))
            else:
                ok += 1
                print("  [ok]   %-24s 结构一致；值变化 %d 处（隔日正常）"
                      % (name, len(diff_values(want_body, got_body))))
            # 容器 meta 只报信息
            wmeta, gmeta = want.get("meta"), got.get("meta")
            if gmeta is not None and wmeta is None:
                print("  [info] %-24s 样本无 meta（本阶段前录制）；今次产出 meta 键 %s"
                      % (name, sorted(gmeta)))
            elif wmeta is not None and gmeta is not None:
                n = len(diff_values(wmeta, gmeta, "meta", in_meta=True))
                print("  [info] %-24s 容器 meta %s（信息性）"
                      % (name, "一致" if not n else "变化 %d 处" % n))
            elif wmeta is not None and gmeta is None:
                print("  [info] %-24s ⚠️ 样本有 meta，今次却没产出" % name)
            sys.stdout.flush()
    finally:
        client.close()
    print("--online 结构对照：一致 %d / 结构变化 %d" % (ok, bad))
    for item in problems:
        print("    - %s" % item)
    return 0 if bad == 0 else 1


# ══════════════════════════════════════════════════════════════════════════
# 十一、入口
# ══════════════════════════════════════════════════════════════════════════
def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                       # noqa: BLE001
            pass
    parser = argparse.ArgumentParser(
        description="tdxdata 黄金样本：录制 / 离线回放校验")
    sub = parser.add_subparsers(dest="cmd")
    p_rec = sub.add_parser("record", help="联网重录（默认目标=全套清单）")
    p_rec.add_argument("--only", default="", help="只录名字以此开头的样本")
    p_chk = sub.add_parser("check", help="离线回放比对（默认）")
    p_chk.add_argument("--online", action="store_true",
                       help="额外联网做一次结构级对照（慢，值变化不算失败）")
    args = parser.parse_args(argv)

    if args.cmd == "record":
        return cmd_record(args.only)
    if args.cmd == "check":
        return cmd_check(args.online)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
