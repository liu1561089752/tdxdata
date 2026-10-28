# -*- coding: utf-8 -*-
r"""README **生成块**的代码侧（ENG-012）：每张表一个纯函数，输入是代码里的事实源。

为什么要有这个模块
------------------
README 之前有大量**手抄**的表（返回类型、复权、域统计…）。手抄的问题不是"会写错"，
而是**错了没人知道怎么错的** —— 市场域表把 `0` 标成「科创板」的错误就是这么进来的，
它带着一整轮调研期的结论一起错（详见 `tdxdata/_core.py:MARKET_DOMAINS` 的注释）。

所以口径只有一条：**能由代码派生的表，一律由代码派生**。
- 事实源 = `enums` / `models` / `errors` / `limits` / `_core` / `domains` / `symbols` /
  `timeliness` / `capabilities` / `indicator` / `invariants` / `_provenance` / `client`
  的模块级常量与 `dataclasses.fields()` / `inspect.signature()`，以及
  `data/products.json`（421 个产品的唯一来源）。
- 每个函数返回 **markdown 字符串**，行尾 `\n`（README 本身是 CRLF，
  `tests/test_readme_drift.py` 比对前会把行尾统一，见那边的注释）。
- 函数**不许**连网、不许读 README、不许有副作用；同样的代码调用两次必须一模一样
  （`tests/test_docgen.py::test_every_generator_is_stable` 钉着这条）。

怎么用
------
1. 改表 → **改事实源**（改代码常量），不要改 README；
2. 重新生成 README 的块（见 `tools/README.md` 里 ENG-012 那一节）；
3. `tests/test_readme_drift.py` 逐字比对、`tests/test_docgen.py` 守稳定性与覆盖率。

⚠️ 这里的每一行都进了「受守护的行数」统计：`tests/test_docgen.py` 会实算
「标记块内行数 / README 总行数」，低于 0.40 直接红。
"""
from __future__ import annotations

import dataclasses
import inspect
import json
import os

from . import _core, calendar, capabilities as caps, client, domains
from . import enums, errors, indicator, invariants, limits, master, models
from . import symbols, timeliness
from ._provenance import ClockSource, Provenance

__all__ = [
    "api_types_table",
    "error_types_table",
    "market_enum_table",
    "period_enum_table",
    "adjust_enum_table",
    "status_enum_table",
    "price_limit_table",
    "on_missing_table",
    "model_fields_table",
    "client_params_table",
    "indicator_table",
    "invariant_table",
    "symbol_prefix_table",
    "ambiguous_symbol_table",
    "timeliness_table",
    "provenance_table",
    "product_layer_table",
    "instrument_stats_table",
    "domain_stats_table",
    "product_index_table",
    # 辅助（给测试与生成脚本用）
    "PRODUCTS_JSON",
    "load_products",
]

#: `data/products.json` 的绝对路径（包的唯一数据源，421 个产品）。
PRODUCTS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "data", "products.json")


# ══════════════════════════════════════════════════════════════════════════
# 一、markdown 小工具（所有生成器共用，保证列数一致、不吞竖线）
# ══════════════════════════════════════════════════════════════════════════
def _cell(value) -> str:
    """单元格文本：压平换行、把 `|` 转义掉（否则会把表格列数打乱）。"""
    text = "" if value is None else str(value)
    for bad in ("\r\n", "\r", "\n"):
        text = text.replace(bad, " ")
    return text.replace("|", "\\|").strip()


def _table(headers, rows) -> str:
    """`|` 表格：`headers` 一项、`rows` 每项与之一一对应（列数由 headers 决定）。"""
    heads = list(headers)
    lines = ["| " + " | ".join(_cell(h) for h in heads) + " |",
             "|" + "|".join("---" for _ in heads) + "|"]
    for row in rows:
        cells = list(row)
        # 列数必须与表头一致 —— 少了补空，多了截断（宁可显示空也不许错列）
        cells = (cells + [""] * len(heads))[: len(heads)]
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    return "\n".join(lines)


def _codes(names) -> str:
    """`("a", "b")` → ``` `a` `b` ```（README 里字段/取名一律带反引号）。"""
    return " ".join("`%s`" % n for n in names)


def _first_line(obj) -> str:
    """取 docstring 的**第一句**（多行时只留第一行，去掉多余的空白）。

    ⚠️ 这里唯一一处"从 docstring 取文字"的地方 —— 只在**没有结构化事实源**、
    且 docstring 那一行本身就是判据时使用（异常/指标/恒等式的"这是什么"）。
    机制性的内容（名称、取值、默认值、字段、命令名）**一律**取结构，不取文字。
    """
    doc = (inspect.getdoc(obj) or "").strip()
    return doc.split("\n")[0].strip() if doc else ""


def _annotation(obj) -> str:
    """函数返回值的**类型注解**原文（`from __future__ import annotations` 下是字符串）。"""
    ann = inspect.signature(obj).return_annotation
    if ann is inspect.Signature.empty:
        return ""
    if isinstance(ann, str):
        return ann
    return getattr(ann, "__name__", str(ann))


def load_products() -> dict:
    """读 `data/products.json` → `{"products": {...}, "pb_products": {...}}`。

    JSON 是**唯一事实源**：产品名 / cmd / ctl / params 全从这里来，
    别在 docgen 里再抄一份（抄一份就会漂）。
    """
    with open(PRODUCTS_JSON, "rb") as fh:
        payload = json.loads(fh.read().decode("utf-8-sig"))
    return {"products": payload.get("products", {}),
            "pb_products": payload.get("pb_products", {})}


# ══════════════════════════════════════════════════════════════════════════
# 二、README 里**原来就有**的表（从手抄搬到生成）
# ══════════════════════════════════════════════════════════════════════════
#: 返回类型表：调用 → 元素类型。元素的**字段**由 `dataclasses.fields()` 给出，
#: 返回类型由 `Client` 方法的类型注解给出（两边都是代码，没有一处手抄）。
_API_TYPE_ROWS = (
    (("get_bars",), "Bar"),
    (("get_minutes",), "MinutePoint"),
    (("get_ticks",), "Tick"),
    (("get_quotes", "get_market_page"), "Quote"),
    (("get",), "Dataset"),
    (("get_f10", "get_finance", "get_xdxr"), "Table"),
)


def _members(cls) -> list:
    """类的**公开成员**：先 dataclass 字段，再其余公开属性/方法（方法带 `()`）。

    顺序稳定（字段按定义序、其余按名字排序），这样两次调用输出一定相同。
    全大写的类常量（`FIELDS_DOC` 这类）不算"成员"—— 它们是实现细节。
    """
    names = []
    field_names = set()
    if dataclasses.is_dataclass(cls):
        for f in dataclasses.fields(cls):
            names.append(f.name)
            field_names.add(f.name)
    rest = []
    for name in dir(cls):
        if name.startswith("_") or name in field_names:
            continue
        if name.isupper():                       # 类常量：不是给调用方用的成员
            continue
        member = inspect.getattr_static(cls, name)
        if isinstance(member, property):
            rest.append(name)
        elif callable(member):
            rest.append(name + "()")
        else:                                   # 类属性
            rest.append(name)
    return names + sorted(rest)


def _sig_text(func) -> str:
    """函数签名的人读版：**只留参数名与默认值**，不带类型注解（注解另有专栏）。"""
    parts = []
    star_done = False
    for param in inspect.signature(func).parameters.values():
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            parts.append("*" + param.name)
            star_done = True
            continue
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            parts.append("**" + param.name)
            continue
        if param.kind is inspect.Parameter.KEYWORD_ONLY and not star_done:
            parts.append("*")
            star_done = True
        if param.default is inspect.Parameter.empty:
            parts.append(param.name)
        else:
            parts.append("%s=%r" % (param.name, param.default))
    return "(" + ", ".join(parts) + ")"


def api_types_table() -> str:
    """`### 返回类型` 那张表：调用 / 返回 / 字段（全部来自代码）。"""
    rows = []
    for methods, type_name in _API_TYPE_ROWS:
        cls = getattr(models, type_name)
        call = " / ".join("`%s()`" % m for m in methods)
        rows.append((call, "`%s`" % _annotation(getattr(client.Client, methods[0])),
                     _codes(_members(cls))))
    return _table(("调用", "返回", "字段 / 公开成员"), rows)


#: 复权枚举的**中文口径**（`enums.Adjust` 只存名字与值，中文名是给人看的展示层，
#: 集中留在这一处；协议 `field8` 一律取 `client._ADJUST_CODE`，不在这里抄）。
_ADJUST_LABELS = {
    "NONE": "不复权",
    "QFQ": "前复权",
    "HFQ": "后复权",
}


def adjust_enum_table() -> str:
    """`## 复权` 那张表：枚举值 / 含义 / 协议 `field8`（枚举与 field8 都取代码）。"""
    rows = []
    for member in enums.Adjust:
        rows.append(("`tdxdata.Adjust.%s`" % member.name,
                     _ADJUST_LABELS.get(member.name, ""),
                     "`%s`" % client._ADJUST_CODE.get(member.name, "")))
    return _table(("值", "含义", "协议 `field8`"), rows)


# ══════════════════════════════════════════════════════════════════════════
# 三、枚举速查（`tdxdata/enums.py`）
# ══════════════════════════════════════════════════════════════════════════
#: `Market` 的中文名（展示层；值是 `enums.Market` 给的）。
_MARKET_LABELS = {"SZ": "深圳", "SH": "上海", "BJ": "北京"}


def market_enum_table() -> str:
    """市场码枚举：成员 / 值 / 中文名 / 协议角色。"""
    rows = []
    for member in enums.Market:
        rows.append(("`%s`" % member.name, member.value,
                     _MARKET_LABELS.get(member.name, ""),
                     "`PBFXT` / `PBZST` 的 `field3`"))
    return _table(("成员", "值", "市场", "对应协议位"), rows)


def period_enum_table() -> str:
    """K 线周期枚举：成员 / 值 / 请求名 / 本库可用性。

    可用性取 `client._PERIOD_STR`（映射表里有名字 = 能发出去）与
    `client._PERIOD_DEAD`（实测 0 行、库会显式拒绝）两张表，**不抄**。
    """
    rows = []
    for member in enums.KlinePeriod:
        req = client._PERIOD_STR.get(member.name, "")
        if member.name in client._PERIOD_DEAD:
            usable = "❌ 无数据（`_PERIOD_DEAD`，抛 `NotSupportedError`）"
        elif req:
            usable = "✅ `%s`" % req
        else:
            usable = "—（未登记）"
        rows.append(("`%s`" % member.name, member.value,
                     "`%s`" % req if req else "—", usable))
    return _table(("成员", "值", "请求名", "本库可用性"), rows)


# `Status` 的四个值没有逐成员 docstring，所以中文解释集中在下面这张表
# （读的是 `enums.Status` 的模块级成员名，**不是**手抄枚举值）。
_STATUS_MEANING = {
    "FINAL": "最终值：这个口径已经定稿",
    "PROVISIONAL": "盘中/当根未走完，会变",
    "PENDING": "今天的还没生成（如盘中查涨停清单）",
    "DELAYED": "数据本身滞后（两融 T+1、行业统计 T-1）",
}


def status_enum_table() -> str:
    """时效四态：成员 / 值 / 语义（值取枚举，语义取下表）。"""
    rows = []
    for member in enums.Status:
        rows.append(("`%s`" % member.name, "`%r`" % member.value,
                     _STATUS_MEANING.get(member.name, "")))
    return _table(("成员", "值", "含义"), rows)


# ══════════════════════════════════════════════════════════════════════════
# 四、涨跌停口径（`tdxdata/limits.py`）
# ══════════════════════════════════════════════════════════════════════════
#: 板块键 → 该板块的代码前缀常量名（`limits` 里的模块级常量，读的时候现取）。
_BOARD_PREFIX_ATTRS = (
    ("main", "_MAIN_PREFIXES"),
    ("chinext", "_CHINEXT_PREFIXES"),
    ("star", "_STAR_PREFIXES"),
    ("bse", "_BSE_PREFIXES"),
    ("fund", "_FUND_PREFIXES"),
    ("bond", "_BOND_PREFIXES"),
    ("bshare", "_BSHARE_PREFIXES"),
)

#: 板块键的中文名（展示层；比例 / 取整单位 / 前缀全部取 `limits` 的常量）。
_BOARD_LABELS = {
    "main": "主板（沪 60 / 深 00）",
    "chinext": "创业板（30）",
    "star": "科创板（68）",
    "bse": "北交所（43/83/87/88/92）",
    "fund": "基金 / ETF",
    "bond": "可转债 / 可交换债",
    "bshare": "B 股（沪 900 / 深 200）",
}


def _pct(value) -> str:
    """`0.1` → `±10%`（比例来自 `limits._RATIO`，这里只做展示换算）。"""
    return "±%g%%" % round(float(value) * 100, 4)


def price_limit_table() -> str:
    """涨跌停口径：板块 / 前缀 / 涨跌幅 / ST / 取整单位。"""
    rows = []
    for board, attr in _BOARD_PREFIX_ATTRS:
        prefixes = getattr(limits, attr)
        ratio = limits._RATIO[board]
        st = _pct(limits._ST_RATIO) if board == "main" else "同左（ST 只在主板生效）"
        tick = limits._TICK[board]
        rows.append((_BOARD_LABELS.get(board, board), "/".join(prefixes),
                     _pct(ratio), st, "%s 元（%d 位小数）"
                     % (tick, max(0, -tick.as_tuple().exponent))))
    return _table(("板块", "代码前缀", "涨跌幅", "ST 涨跌幅", "最小变动单位"), rows)


# ══════════════════════════════════════════════════════════════════════════
# 五、`on_missing` 三态（`tdxdata/_core.py`）
# ══════════════════════════════════════════════════════════════════════════
#: 三个取值的**行为**（取值集合来自 `_core.ON_MISSING_MODES`，这里只给说法）。
_ON_MISSING_LABELS = {
    "raise": "有缺失就抛 `EmptyResult`，点名缺了哪些代码（默认）",
    "skip": "不抛，缺失清单进 `provenance.extra[\"missing\"]`",
    "report": "同 `skip`，另外 `warnings.warn` 一次",
}


def on_missing_table() -> str:
    """`get_quotes` 等批量接口的 `on_missing` 三态。"""
    rows = [("`%s`" % mode, _ON_MISSING_LABELS.get(mode, ""))
            for mode in _core.ON_MISSING_MODES]
    return _table(("取值", "缺码时怎么处理"), rows)


# ══════════════════════════════════════════════════════════════════════════
# 六、数据模型字段（`tdxdata/models.py`）
# ══════════════════════════════════════════════════════════════════════════
#: 参与渲染的模型类（顺序 = 文档里的顺序，全部是 `tdxdata.models` 的属性）。
_MODEL_CLASSES = ("Bar", "MinutePoint", "Quote", "Tick", "SecurityInfo",
                  "Table", "Dataset", "ProductInfo")


def _type_text(field) -> str:
    """字段类型原文（`from __future__ import annotations` 下是字符串）。"""
    ann = field.type
    if isinstance(ann, str):
        return ann
    return getattr(ann, "__name__", str(ann))


def _default_text(field) -> str:
    """字段默认值：必填 / 字面默认 / 工厂。"""
    if field.default is not dataclasses.MISSING:
        return "`%r`" % (field.default,)
    if field.default_factory is not dataclasses.MISSING:      # type: ignore[misc]
        factory = field.default_factory                        # type: ignore[misc]
        name = getattr(factory, "__name__", None) or type(factory).__name__
        return "工厂 `%s()`" % name
    return "**必填**"


def model_fields_table() -> str:
    """8 个模型类的全字段表（字段名 / 类型 / 默认值全部取 `dataclasses.fields`）。"""
    rows = []
    for cls_name in _MODEL_CLASSES:
        cls = getattr(models, cls_name)
        for field in dataclasses.fields(cls):
            rows.append(("`%s`" % cls_name, "`%s`" % field.name,
                         _type_text(field), _default_text(field)))
    return _table(("模型", "字段", "类型", "默认值"), rows)


# ══════════════════════════════════════════════════════════════════════════
# 七、`Client` 构造参数（`inspect.signature(Client.__init__)`）
# ══════════════════════════════════════════════════════════════════════════
def client_params_table() -> str:
    """`Client(...)` 的全部构造参数：参数 / 类型 / 默认值 / 位置或关键字。"""
    sig = inspect.signature(client.Client.__init__)
    rows = []
    for name, param in sig.parameters.items():
        if name == "self":
            continue
        kind = ("关键字专用" if param.kind is inspect.Parameter.KEYWORD_ONLY
                else "位置或关键字")
        ann = param.annotation
        if ann is inspect.Parameter.empty:
            type_text = ""
        else:
            type_text = ann if isinstance(ann, str) else getattr(ann, "__name__", "")
        default = ("`%r`" % (param.default,)
                   if param.default is not inspect.Parameter.empty else "**必填**")
        rows.append(("`%s`" % name, "`%s`" % type_text if type_text else "—",
                     default, kind))
    return _table(("参数", "类型", "默认值", "传法"), rows)


# ══════════════════════════════════════════════════════════════════════════
# 八、技术指标（`tdxdata/indicator.py` + `client._IND_DEFAULT_PARAMS`）
# ══════════════════════════════════════════════════════════════════════════
def _indicator_names() -> list:
    """`indicator` 模块自己对外承诺的指标函数（按 `__all__` 之外的函数名排序）。"""
    out = []
    for name in sorted(dir(indicator)):
        if name.startswith("_"):
            continue
        obj = getattr(indicator, name)
        if inspect.isfunction(obj) and obj.__module__ == indicator.__name__:
            out.append(name)
    return out


def _default_params(name: str) -> str:
    """`get_indicators` 的默认参数（取 `client._IND_DEFAULT_PARAMS`，不抄）。"""
    params = client._IND_DEFAULT_PARAMS.get(name)
    if params is None:
        return "—（`get_indicators` 未登记）"
    if not params:
        return "无（固定参数）"
    return "、".join("%s=%s" % (k, "/".join(str(x) for x in v))
                     for k, v in params.items())


def indicator_table() -> str:
    """技术指标清单：导出名 / 签名 / `get_indicators` 默认参数 / 口径。"""
    rows = []
    for name in _indicator_names():
        func = getattr(indicator, name)
        rows.append(("`%s`" % name, "`indicator.%s%s`" % (name, _sig_text(func)),
                     _default_params(name), _first_line(func)))
    return _table(("指标", "签名", "`get_indicators` 默认参数", "口径"), rows)


# ══════════════════════════════════════════════════════════════════════════
# 九、恒等式闸门（`tdxdata/invariants.py`）
# ══════════════════════════════════════════════════════════════════════════
#: 恒等式检查器 → 它查什么（第一句取自 docstring，判据本身就是那句话）。
def _invariant_checkers() -> list:
    out = []
    for name in sorted(dir(invariants)):
        if not name.startswith("check_"):
            continue
        obj = getattr(invariants, name)
        if inspect.isfunction(obj) and obj.__module__ == invariants.__name__:
            out.append((name, obj))
    return out


def invariant_table() -> str:
    """恒等式清单：检查器 / 断言什么。"""
    rows = [("`%s()`" % name, _first_line(func))
            for name, func in _invariant_checkers()]
    return _table(("检查器", "断言什么"), rows)


# ══════════════════════════════════════════════════════════════════════════
# 十、代码段 → 市场（`tdxdata/symbols.py`）
# ══════════════════════════════════════════════════════════════════════════
def _market_name(code: int) -> str:
    """市场码 → `sh`/`sz`/`bj`（反查 `symbols._SUFFIX`，不抄）。"""
    return symbols._SUFFIX.get(int(code), str(code))


def symbol_prefix_table() -> str:
    """代码段 → 市场：3 位前缀（优先）与 1 位兜底两张表合成一张。"""
    rows = []
    for prefix in sorted(symbols._PREFIX3):
        market = symbols._PREFIX3[prefix]
        rows.append(("`%sxxx`" % prefix, _market_name(market), int(market),
                     "3 位前缀（**优先**）"))
    for head in sorted(symbols._HEAD1):
        market = symbols._HEAD1[head]
        rows.append(("`%sxxxxx`" % head, _market_name(market), int(market),
                     "1 位兜底（3 位没命中时）"))
    return _table(("代码段", "市场", "市场码", "规则"), rows)


def ambiguous_symbol_table() -> str:
    """两义代码表（`symbols.AMBIGUOUS` 原样，一行一条）。"""
    rows = [("`%s`" % code, text) for code, text in sorted(symbols.AMBIGUOUS.items())]
    return _table(("写法", "它到底是谁 / 该怎么写"), rows)


# ══════════════════════════════════════════════════════════════════════════
# 十一、时效常量（`tdxdata/timeliness.py`）
# ══════════════════════════════════════════════════════════════════════════
def _hhmm(value) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M:%S")
    return str(value)


def timeliness_table() -> str:
    """三个时刻常量 + 周期分钟数 + 日线及以上周期 + 可信时钟来源。"""
    rows = [
        ("`CLOSE_CUTOFF`", _hhmm(timeliness.CLOSE_CUTOFF), "日线当日定稿的判定时刻"),
        ("`FINAL_CUTOFF_HHMM`", timeliness.FINAL_CUTOFF_HHMM,
         "给人看的同一件事（`timeliness()` 的 `close_cutoff`）"),
        ("`MIN_BAR_CUTOFF`", _hhmm(timeliness.MIN_BAR_CUTOFF),
         "分钟周期收盘边界"),
    ]
    for name in sorted(timeliness._PERIOD_MINUTES,
                       key=timeliness._PERIOD_MINUTES.get):
        rows.append(("`_PERIOD_MINUTES[\"%s\"]`" % name,
                     "%d 分钟" % timeliness._PERIOD_MINUTES[name],
                     "该周期的长度（边界检查用）"))
    rows.append(("`_DAYLIKE`", " / ".join("`%s`" % p for p in timeliness._DAYLIKE),
                 "按 `CLOSE_CUTOFF` 判当根的周期"))
    rows.append(("`TRUSTED_CLOCKS`",
                 " / ".join("`%s`" % c for c in timeliness.TRUSTED_CLOCKS),
                 "可判定 FINAL 的时钟来源（其余一律降级）"))
    return _table(("常量 / 键", "值", "用途"), rows)


# ══════════════════════════════════════════════════════════════════════════
# 十二、来源信息（`tdxdata/_provenance.py`）
# ══════════════════════════════════════════════════════════════════════════
def provenance_table() -> str:
    """`Provenance` 的字段与 `ClockSource` 的取值（都取 `dataclasses` / `Enum`）。"""
    rows = []
    for field in dataclasses.fields(Provenance):
        rows.append(("`Provenance`", "`%s`" % field.name, _type_text(field)))
    for member in ClockSource:
        rows.append(("`ClockSource`", "`%s`" % member.name, "`%r`" % member.value))
    return _table(("载体", "名称", "类型 / 取值"), rows)


# ══════════════════════════════════════════════════════════════════════════
# 十三、产品层 API（交易日历 / 证券主数据）
# ══════════════════════════════════════════════════════════════════════════
#: 产品层的两个门面类（模块属性；两个都在，缺一个会在 import 阶段直接炸 —— 有意的）。
_PRODUCT_LAYER = (("TradeCalendar", "tdxdata/calendar.py", calendar),
                  ("SecMaster", "tdxdata/master.py", master))


def product_layer_table() -> str:
    """`TradeCalendar` / `SecMaster` 的公开方法一览（取 `inspect.signature`）。"""
    rows = []
    for cls_name, where, module in _PRODUCT_LAYER:
        cls = getattr(module, cls_name)
        for name in sorted(dir(cls)):
            if name.startswith("_"):
                continue
            member = inspect.getattr_static(cls, name)
            if not callable(member):
                continue
            sig = _sig_text(member)
            rows.append(("`%s`" % cls_name, "`%s%s`" % (name, sig),
                         _codes([where])))
    return _table(("类", "方法", "出处"), rows)


# ══════════════════════════════════════════════════════════════════════════
# 十四、品种 × 能力统计（`tdxdata/capabilities.py`）与产品域统计（products.json）
# ══════════════════════════════════════════════════════════════════════════
def instrument_stats_table() -> str:
    """按品种统计能力条目：总条数 / 可用 / 不可用（全部数 `CAPABILITIES`，不抄）。"""
    rows = []
    for code in caps.INSTRUMENTS:
        items = [c for c in caps.CAPABILITIES if c.instrument == code]
        yes = sum(1 for c in items if c.available)
        rows.append(("`%s`" % code, caps.INSTRUMENTS[code], len(items), yes,
                     len(items) - yes))
    return _table(("品种码", "品种", "能力条目", "可用", "不可用"), rows)


def domain_stats_table() -> str:
    """17 个数据域的产品数（域分类取 `domains.domain_of(cmd)`，产品取 products.json）。"""
    products = load_products()["products"]
    order = list(domains.DOMAINS)
    counts = {}
    for spec in products.values():
        dom = domains.domain_of(spec.get("cmd", ""))
        counts[dom] = counts.get(dom, 0) + 1
    for dom in sorted(counts):                 # 兜底：域分类表里没有的（正常 0 个）
        if dom not in order:
            order.append(dom)
    total = len(products)
    rows = []
    for dom in order:
        n = counts.get(dom, 0)
        rows.append(("`%s`" % dom, n,
                     "%.1f%%" % (100.0 * n / total if total else 0.0),
                     "命中规则 %d 条" % sum(1 for _tok, d in domains._RULES if d == dom)))
    rows.append(("**合计**", total, "100.0%", "—"))
    return _table(("域", "产品数", "占比", "域分类规则"), rows)


def product_index_table() -> str:
    """421 个数据中心产品的全量索引：产品名 / 域 / ctl / 命令（全取 products.json）。"""
    products = load_products()["products"]
    rows = []
    for name in sorted(products):
        spec = products[name]
        rows.append(("`%s`" % name,
                     domains.domain_of(spec.get("cmd", "")),
                     spec.get("ctl", ""),
                     "`%s`" % spec.get("cmd", "")))
    return _table(("产品名", "域", "ctl", "命令"), rows)


def error_types_table() -> str:
    """异常层次：异常类 / 直系基类 / 一句话（`errors.__all__` 的顺序与 docstring）。"""
    rows = []
    for name in errors.__all__:
        cls = getattr(errors, name)
        bases = [b.__name__ for b in cls.__bases__]
        rows.append(("`%s`" % name, "`%s`" % (bases[0] if bases else "—"),
                     _first_line(cls)))
    return _table(("异常", "直系基类", "什么时候抛"), rows)
