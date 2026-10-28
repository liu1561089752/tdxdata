# -*- coding: utf-8 -*-
"""弃用与迁移机制（ENG-008）。

为什么需要这个文件
------------------
0.2.0 → 0.3.0 之间发生了**破坏性变更**（`meta` 字典公开面移除 → `Provenance` 一等字段），
但库里既没有 `DeprecationWarning` 机制、也没有一张「旧写法 → 新写法」的迁移表。
结果是：升级的人只能靠读 CHANGELOG 全文猜自己该改哪一行，或者干脆被 `AttributeError` 打回来。

本模块提供两件东西，**刻意分成两类、不要混用**：

1. **机制**（给未来的弃用用）：`deprecated()` 装饰器 / `warn()` 函数。
   统一消息格式：`<旧名> 已弃用（since <版本>）→ 改用 <新名>；计划在 <版本> 移除`。
   * 用 `DeprecationWarning`（不是 `FutureWarning`：这是**开发者**该处理的，不是终端用户的运行时故障）；
   * 同一处**每进程只提醒一次**（`_seen` 兜底）—— 即使调用方把
     `warnings.simplefilter("always")` 打开，也不会在循环里刷屏；
   * `stacklevel` 指到**调用方**，`pytest.warns` / `-W error` 能正常抓到。
2. **登记**（给已经生效的破坏性变更用）：`MIGRATIONS`。
   已经发生的事**不应该**再 warn（warn 只在"旧写法还能跑"时有意义）；这些条目是
   CHANGELOG / README 生成块的**单一事实源**，`as_markdown_table()` 直接渲染。

红线
----
* **不许**为了让测试安静就把 `DeprecationWarning` 全局过滤掉；
* **不许**在 `MIGRATIONS` 里写"将改为…"这种未来时 —— 已生效的才登记，
  未生效的走 `DEPRECATED_API`（含 `remove_in`，届时由 `deprecated()` 提示）。
"""
from __future__ import annotations

import functools
import warnings

__all__ = [
    "DEPRECATED_API", "MIGRATIONS", "as_markdown_table", "deprecated",
    "pending_markdown", "reset_seen", "warn",
]

#: 已经**生效**的破坏性变更（不再 warn，只作迁移说明 + 文档单一事实源）。
#:
#: 每条字段：
#:   `version`   这次变更**进入**的版本
#:   `change`    变了什么（旧行为 → 新行为）
#:   `migration` 旧代码怎么改（给出可直接替换的写法）
#:   `why`       为什么这么变（给未来的自己留判据，避免有人"顺手改回去"）
MIGRATIONS: dict[str, dict] = {
    "meta-to-provenance": {
        "version": "0.3.0",
        "change": "`Table`/容器的 `meta` 字典**公开面移除**，改为 `Provenance` 一等字段"
                  "（`as_of` / `status` / `fetched_at` / `elapsed` / `cmd` / `clock` / `source` / `extra`）",
        "migration": "`t.meta[\"as_of\"]` → `t.as_of`；`t.meta[\"status\"]` → `t.status`；"
                     "`t.meta[\"source\"][\"host\"]` → `t.provenance.source[\"host\"]`；"
                     "未登记的键一律在 `t.provenance.extra`（**不保证**存在，读时要 `.get`）",
        "why": "ARCH-002：`meta` 是自由字典，键名漂移无法被类型检查/黄金样本钉住；"
               "改成 dataclass 字段后「哪些键必须存在」由代码表达，缺键在构造期就报错",
    },
    "vol-unit-hand": {
        "version": "0.2.0",
        "change": "`vol` 对外统一为**手**（此前日线/分钟线的股票与 ETF 是「股」）",
        "migration": "旧版拿到的「股」数值 ÷100 即手；需要服务端原值时读 `raw[\"Volume\"]`"
                     "（**原单位，别直接参与计算**）",
        "why": "同库四类容器（Bar/Quote/MinutePoint/Tick）单位必须一致，否则"
               "「合计成交量」这类跨容器运算会静默差 100 倍",
    },
    "non-price-series-rejected": {
        "version": "0.3.0",
        "change": "`get_bars()` **拒绝**非价格序列（`880005` 涨跌家数、`880006` 停板家数等），"
                  "抛 `NotSupportedError`（旧版照发，回来的 OHLC 是四个家数）",
        "migration": "`c.get_bars(\"880005\")` → `c.get_breadth(\"880005\")`（列名 `up_count`/`down_count`/…）；"
                     "判断某代码是否属于这类序列用 `tdxdata.is_non_price(code)`",
        "why": "把家数当价格算，任何技术指标都会给出**看起来正常但完全错**的值 —— "
               "这是本项目最贵的一类静默错误（见 `series_kinds.py` 模块文档）",
    },
    "concurrency-removed": {
        "version": "0.3.0",
        "change": "`Client(concurrency=2..4)` **参数已移除**（现在传它会 `TypeError`）",
        "migration": "串行取数；要多标的请自己 `for` 遍历或 `AsyncClient`（同一根连接仍受进程级节流约束）",
        "why": "实测 4 lane：头 5 秒即被服务端重置，175 秒 0 次成功、3 台服务器进冷却；"
               "串行 68.64 s 全部成功。功能是**删掉**的，不是修好的",
    },
}

#: 已声明弃用、但仍然可用的接口（`键 = 旧名`）。条目由 `deprecated()` 的使用者登记，
#: 交付判据：每条都有 `new` / `since` / `remove_in` / `detail`，否则 `test_deprecation` 会红。
#: **当前为空**：0.3.0 没有"可用但已弃用"的接口（4 条破坏性变更都已直接生效，见 MIGRATIONS）。
DEPRECATED_API: dict[str, dict] = {}

#: 已提醒过的（`"旧名@since"`）—— 让同一处每进程只响一次。
_seen: set[str] = set()


def reset_seen() -> None:
    """清空"已提醒"记录（**只给测试用**：生产代码里永远不该需要它）。"""
    _seen.clear()


def warn(old: str, *, new: str | None = None, since: str,
         remove_in: str | None = None, detail: str = "") -> None:
    """以 `DeprecationWarning` 提醒一次某个旧接口。

    `stacklevel=3`：`warn` → 调用它的包装函数 → **调用方**（这样 `-W error` 抓到的位置是用户代码）。
    """
    key = "%s@%s" % (old, since)
    if key in _seen:
        return
    _seen.add(key)
    msg = "%s 已弃用（since %s）" % (old, since)
    if new:
        msg += " → 改用 %s" % new
    if remove_in:
        msg += "；计划在 %s 移除" % remove_in
    if detail:
        msg += "。%s" % detail
    warnings.warn(msg, DeprecationWarning, stacklevel=3)


def deprecated(new: str | None = None, *, since: str,
               remove_in: str | None = None, detail: str = ""):
    """把函数/方法标记为弃用：调用时发一次 `DeprecationWarning`，然后**照常执行**。

    用法::

        @deprecated("c.master.resolve()", since="0.4.0", remove_in="0.6.0")
        def resolve_old(self, code): ...

    * 保留 `__name__` / `__doc__`（`functools.wraps`）—— 文档生成与 `inspect` 不受影响；
    * 关键：**只是提醒，不改变行为**。弃用期内行为必须与旧版逐位一致，
      否则"弃用"就变成了"偷偷改语义"。
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            warn(fn.__qualname__, new=new, since=since, remove_in=remove_in, detail=detail)
            return fn(*args, **kwargs)
        wrapper.__deprecated__ = {"new": new, "since": since,
                                  "remove_in": remove_in, "detail": detail}
        return wrapper
    return deco


def as_markdown_table() -> str:
    """渲染 `MIGRATIONS` 为 markdown 迁移表（CHANGELOG / README 生成块的单一事实源）。"""
    lines = ["| 版本 | 变了什么 | 旧代码怎么改 | 为什么 |", "|---|---|---|---|"]
    for _key, ent in MIGRATIONS.items():
        lines.append("| `%s` | %s | %s | %s |"
                     % (ent["version"], ent["change"], ent["migration"], ent["why"]))
    return "\n".join(lines)


def pending_markdown() -> str:
    """渲染"已声明弃用、仍可用"的接口表（当前为空 → 返回表头 + 一句"无"）。"""
    lines = ["| 旧接口 | 替代 | 自 | 预计移除 |", "|---|---|---|---|"]
    if not DEPRECATED_API:
        lines.append("| —— | 当前没有「可用但已弃用」的接口（4 条破坏性变更已直接生效，见迁移表） | —— | —— |")
        return "\n".join(lines)
    for old, ent in DEPRECATED_API.items():
        lines.append("| `%s` | `%s` | %s | %s |"
                     % (old, ent.get("new") or "—", ent["since"], ent.get("remove_in") or "—"))
    return "\n".join(lines)
