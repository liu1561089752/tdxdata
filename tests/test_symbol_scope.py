# -*- coding: utf-8 -*-
"""标的范围与写法（阶段 3）：**位数红线 / 期货归属 / 两义代码**。

这个文件守的三条，每条都对应一个**已实证**的错误现场
--------------------------------------------------
1. **位数红线（B1）**：`parse("00700")` 曾被放行 → 判深市 → `PBFXT` **左补 0**
   → `get_bars("00700")` **静默返回「模塑科技 000700」的 K 线**
   （`close=13.58`，与 `get_bars("000700")` 逐条相同；`get_minutes` 同病）。
   任何 5 位数字串都会命中某只 6 位 A 股（`02800→002800`、`00005→000005`）。
   现在的契约：**位数不对 → `OutOfScope`，绝不补零也绝不截断**。

2. **期货归属（B2）**：`IF00` 以前抛 `ValueError`（"无法识别的标的"），
   听起来像"你写错了"；实际是"**这个品种本数据源根本没有**"。
   归因必须写对 —— 措辞纪律：**不许**说"服务端不支持这个市场码"
   （已实证 `PBFXT` 的 `field3` 对已知代码宽松：`600519` 在 `field3=1/99/30`
   三种下返回**逐位相同**的 K 线），只能说"**不在证券表里**"。

3. **两义代码（B3）**：`000001` 是平安银行，而上证指数是 `999999`；
   `000300`/`000852`/`000016` 都是**沪市指数**，写不带前缀会被判成深市。
   `symbols.parse()` 保持**纯函数**（不加 warning），提醒/严格模式在
   `Client._check_symbol()`，且**只在市场是猜出来的**时候触发。

为什么提醒不能放在 `parse()` 里
-------------------------------
`parse()` 被 `_pairs` / `_sentinel` / 各 sections 反复调用；在里面 `warnings.warn`
会让"一次 `get_quotes` 报 50 条同类警告"，而且"要不要严格"这种**策略**一旦进库函数，
就没有任何调用方能关掉它。纯函数 + 调用方策略是唯一干净的切法。
"""
from __future__ import annotations

import warnings

import pytest

import tdxdata
from tdxdata import errors, symbols

# 红线（`_dev/check.py` 第 7 条）：测试里不许出现 `Client(` / `Session(` 字面量。
_CLS = getattr(tdxdata, "Client")


def _bare_client(**kw):
    """不建连的裸 Client（`__new__`，只补真正会用到的字段）。"""
    c = _CLS.__new__(_CLS)
    from tdxdata._engine import datacenter as dc

    c._dc_mod = dc
    c._probe_allowed = False
    c._clock_probe = False
    c._clock = None
    c._last_call = {}
    c._in_cal = False
    c._strict_symbols = bool(kw.get("strict_symbols", False))
    return c


# ══════════════════════════════════════════════════════════════════════════
# 一、B1：位数红线
# ══════════════════════════════════════════════════════════════════════════
SHORT_CODES = ["00700", "02800", "00005", "09988", "60051"]


@pytest.mark.parametrize("code", SHORT_CODES)
def test_short_numeric_is_out_of_scope(code):
    """5 位纯数字**必须**拒绝 —— 补零会静默命中另一只 A 股。"""
    with pytest.raises(errors.OutOfScope):
        symbols.parse(code)
    # 带前缀也一样逃不掉（`sh00700` 不是"写全了"，还是 5 位）
    with pytest.raises(errors.OutOfScope):
        symbols.parse("sh" + code)
    # 元组写法同样校验（`("sh", "00700")` 也是用户输入）
    with pytest.raises(errors.OutOfScope):
        symbols.parse(("sh", code))


def test_out_of_scope_message_says_all_three_things():
    """消息必须同时说清三件事：**会命中谁 / 港股现状 / 该怎么写**。

    只说"位数不对"是不够的 —— 用户会以为库坏了，然后自己去加 `0`。
    """
    with pytest.raises(errors.OutOfScope) as ei:
        symbols.parse("00700")
    msg = str(ei.value)
    assert "000700" in msg, "必须点出会命中哪只标的（左补零的实际后果）"
    assert "模塑" in msg, "要给出标的**名字**，代码本身不足以让人意识到串了"
    assert "PBCombHQ" in msg and "setcode=31" in msg, \
        "要说明港股实时报价其实服务端有、只是本库没实现（否则用户会去换数据源）"
    assert "6 位" in msg and "00700" in msg


def test_overlong_numeric_is_out_of_scope_not_truncated():
    """超长纯数字也拒绝：截断与补零是**同一个坑的两面**（都会命中别的标的）。"""
    with pytest.raises(errors.OutOfScope) as ei:
        symbols.parse("6005199")
    assert "截断" in str(ei.value)


def test_full_six_digit_still_works():
    """修完红线不能把正常写法也误伤 —— 6 位照旧全部通过。"""
    assert symbols.parse("600519") == (1, "600519")
    assert symbols.parse("sh600519") == (1, "600519")
    assert symbols.parse("000001") == (0, "000001")
    assert symbols.parse(("sh", "000300")) == (1, "000300")
    assert symbols.parse("920091") == (2, "920091")
    assert symbols.parse("900901") == (1, "900901")      # 沪B
    assert symbols.parse("113711") == (1, "113711")      # 沪转债


# ══════════════════════════════════════════════════════════════════════════
# 二、B2：期货/期权/国债期货/外汇
# ══════════════════════════════════════════════════════════════════════════
FUTURES = ["IF2612", "IC00", "IH00", "IM00", "IF00",
           "T00", "TF00", "TS00", "TL00", "IO00", "MO00", "IO2609-C-3900"]


@pytest.mark.parametrize("code", FUTURES)
def test_futures_like_is_out_of_scope(code):
    with pytest.raises(errors.OutOfScope) as ei:
        symbols.parse(code)
    msg = str(ei.value)
    assert "证券表" in msg, "归因必须是「不在证券表里」"
    assert "不是**「服务端不支持这个市场码」" in msg or "不是「服务端不支持这个市场码」" in msg, \
        "必须显式否掉「服务端不支持市场码」这个错归因"


def test_futures_message_cites_the_field3_evidence():
    """措辞纪律的证据要写在消息里，否则下一个人会凭直觉改回旧的归因。"""
    with pytest.raises(errors.OutOfScope) as ei:
        symbols.parse("IF2612")
    msg = str(ei.value)
    assert "field3" in msg and "逐位相同" in msg


def test_garbage_still_value_error():
    """**垃圾输入保持 `ValueError`** —— 别把 `"abc"` 也归成"品种不在范围内"。

    `OutOfScope` 的语义是"这是另一种品种"；`"abc"` 是**写错了**。
    两者混起来，用户会按"换数据源"去处理一个拼写错误。
    """
    for junk in ("abc", "", "sh", "sz-", "600519.xx"):
        with pytest.raises(ValueError) as ei:
            symbols.parse(junk)
        assert not isinstance(ei.value, errors.OutOfScope), \
            "%r 不该被判成 OutOfScope（它不是另一种品种）" % (junk,)


def test_is_futures_like_shape():
    assert symbols.is_futures_like("IF2612")
    assert symbols.is_futures_like("T00")
    assert not symbols.is_futures_like("600519")
    assert not symbols.is_futures_like("sh600519")


# ══════════════════════════════════════════════════════════════════════════
# 三、B3：两义代码注册表 + Client 层策略
# ══════════════════════════════════════════════════════════════════════════
def test_ambiguous_registry_shape():
    assert set(symbols.AMBIGUOUS) == {"000001", "000300", "000852", "000016"}
    for code, text in symbols.AMBIGUOUS.items():
        assert isinstance(text, str) and len(text) > 20, code
    assert "999999" in symbols.AMBIGUOUS["000001"], "上证指数的正确代码必须写出来"
    for code in ("000300", "000852", "000016"):
        assert "sh" + code in symbols.AMBIGUOUS[code], \
            "%s 的指路文案必须给出带前缀的写法" % code
        assert "沪市指数" in symbols.AMBIGUOUS[code]


def test_parse_stays_pure_no_warning():
    """`symbols.parse()` **不许**自己发警告（提醒是调用方的策略）。"""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert symbols.parse("000300") == (0, "000300")
    assert not caught, "parse() 必须保持纯函数，警告应只在 Client 层发"


def test_client_warns_by_default():
    """默认：**照旧解析 + `warnings.warn`**（不静默，也不打断）。"""
    c = _bare_client(strict_symbols=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        got = c._check_symbol("000300")
    assert got == (0, "000300"), "默认策略下解析结果不变（兼容）"
    assert len(caught) == 1, "必须提醒，且只提醒一次"
    text = str(caught[0].message)
    assert "sh000300" in text and "两义" in text


def test_client_strict_raises_ambiguous():
    c = _bare_client(strict_symbols=True)
    with pytest.raises(errors.AmbiguousSymbol) as ei:
        c._check_symbol("000001")
    assert "999999" in str(ei.value)
    assert "strict_symbols" in str(ei.value)


def test_explicit_market_silences_the_warning():
    """写了 `sh000300` 就说明口径已明确 —— 再提醒一遍等于把正确写法判成可疑。"""
    c = _bare_client(strict_symbols=True)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert c._check_symbol("sh000300") == (1, "000300")
        assert c._check_symbol(("sh", "000300")) == (1, "000300")
    assert not caught
    # 显式 market 的整数元组同理
    assert c._check_symbol((1, "000300")) == (1, "000300")


def test_zero_symbol_silenced_by_explicit_market_via_tuple():
    """`(0, "000001")` 也是显式口径（用户明确要深市 000001 = 平安银行）。"""
    c = _bare_client(strict_symbols=True)
    assert c._check_symbol((0, "000001")) == (0, "000001")


# ══════════════════════════════════════════════════════════════════════════
# 四、**每个收 symbol 的公开方法都要过 `_check_symbol`**
# ══════════════════════════════════════════════════════════════════════════
#: (方法名, 调用参数模板)。参数里的 `{}` 会被替换成被测代码。
#: ⚠️ 这份清单**不是**"要测哪些方法"的白名单 —— `test_symbol_methods_are_covered`
#: 会与"从 `Client` 签名推导出的 symbol 方法集"精确比对，新增一个收 symbol 的
#: 公开方法却没进这张表，测试立刻红。
SYMBOL_METHODS = {
    "get_bars": ("{}",),
    "get_breadth": ("{}",),
    "get_minutes": ("{}",),
    "get_ticks": ("{}",),
    "get_price_limits": ("{}",),
    "get_name": ("{}",),
    "get_quotes": ("{}",),
    "get_samples": ("{}",),
    "get_intraday_samples": ("{}",),
    "get_indicators": ("{}",),
    "get_f10": ("{}",),
    "get_finance": ("{}",),
    "get_xdxr": ("{}",),
}


def _derived_symbol_methods() -> set:
    """从 `Client` 的**签名**推导"第一个参数是 symbol 的公开方法"。

    判定依据只看注解/参数名，不读实现 —— 依据一旦放进实现里，
    就可以通过"改实现"让测试静默失效。
    """
    import inspect

    out = set()
    for name in dir(_CLS):
        if name.startswith("_"):
            continue
        fn = getattr(_CLS, name, None)
        if not callable(fn):
            continue
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):                     # pragma: no cover
            continue
        params = [p for p in sig.parameters if p != "self"]   # ← 别把 self 当成标的参数
        if not params:
            continue
        first = params[0]
        ann = str(sig.parameters[first].annotation).lower()
        if first in ("symbol", "symbols_", "symbols", "code") or "symbol" in ann:
            out.add(name)
    return out


#: **例外白名单**：参数名看着像标的、但其实**不解析标的**的方法。
#: 每条都必须写清理由 —— 这是"封闭集"，不是"可以悄悄变大的地方"
#: （同 B5 教训：判定依据不许放在自己能随手改的地方）。
SYMBOL_EXEMPT = {
    # `symbols` 只接受 None（全市场排行）；给任何代码都直接 NotSupportedError，
    # 压根不走 `symbols.parse`，所以没有"位数/期货/两义"可言。
    "get_capital_flow": "只接受 symbols=None；任何代码都在 flow 层 NotSupportedError，不解析标的",
}


def test_symbol_methods_are_covered():
    """推导集 == 显式清单 ∪ 例外白名单：新增收 symbol 的方法不会被"自动豁免"。"""
    derived = _derived_symbol_methods()
    assert derived == set(SYMBOL_METHODS) | set(SYMBOL_EXEMPT), (
        "收 symbol 的公开方法集变了：\n  推导出但没测也没豁免：%s\n  测了但推导不出：%s"
        % (sorted(derived - set(SYMBOL_METHODS) - set(SYMBOL_EXEMPT)),
           sorted((set(SYMBOL_METHODS) | set(SYMBOL_EXEMPT)) - derived)))
    # 例外必须**有理由**（空字符串/占位一律不算）
    for name, why in SYMBOL_EXEMPT.items():
        assert isinstance(why, str) and len(why) >= 10, "例外 %s 没写清理由" % name
    for must in ("get_bars", "get_minutes", "get_ticks", "get_quotes", "get_name"):
        assert must in derived


@pytest.mark.parametrize("name", sorted(SYMBOL_METHODS))
def test_symbol_method_rejects_out_of_scope_before_network(name, monkeypatch):
    """**红线方法级验证**：非法代码必须在**发请求之前**就拒绝（零网络）。

    这一条是 B1 的实质：只修 `symbols.parse` 不够 —— 若某个方法自己先干了别的
    （拼参数、探针、翻页），用户还是会先等一轮网络才收到错误。
    """
    from tdxdata._engine import hq_extra, hq_pb

    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("不许发网络请求")

    for mod, fname in ((hq_pb, "get_bars"), (hq_pb, "get_minutes"),
                       (hq_pb, "get_market_page"), (hq_extra, "get_quotes"),
                       (hq_extra, "name_of"), (hq_extra, "block_data")):
        monkeypatch.setattr(mod, fname, boom)

    c = _bare_client()
    args = tuple("00700" if x == "{}" else x for x in SYMBOL_METHODS[name])
    with pytest.raises(errors.OutOfScope):
        getattr(c, name)(*args)
    assert calls["n"] == 0, "%s 必须在发请求前就拒绝" % name


def test_one_call_emits_exactly_one_ambiguity_warning(monkeypatch):
    """**一次调用只许一条警告** —— 内部路径不许重复提醒（实测踩过：`get_bars` warn 两遍）。

    `get_bars` 与它的取数本体 `_fetch_bars` 历史上**都**做了 `_check_symbol`，
    于是同一句提醒发两次；而 `get_breadth` 走 `_fetch_bars`，同一个坑。
    校验责任在**入口**，本体只解析（纯函数），这条用例就是那道护栏。
    """
    from tdxdata._engine import hq_pb
    from tdxdata.models import Bars

    def fake_bars(code, market=1, *a, **k):
        return ["Data"], []

    monkeypatch.setattr(hq_pb, "get_bars", fake_bars)
    c = _bare_client()
    for call in (lambda: c.get_bars("000300", count=2),
                 lambda: c.get_breadth("000300")):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                call()
            except Exception:                       # 数据空/未登记都无妨，只看警告
                pass
        mine = [w for w in caught if "两义代码" in str(w.message)]
        assert len(mine) == 1, "一次调用只许一条两义提醒（实际 %d 条）" % len(mine)


def test_get_bars_00700_does_not_return_mouxing_keji(monkeypatch):
    """**最直白的红线断言**：绝不许因为补零而拿到 `000700` 的数据。"""
    from tdxdata._engine import hq_pb

    served = []

    def fake_bars(code, market=1, *a, **k):
        served.append((market, code))
        return ["Data"], [{"Data": "20261027", "Open": "1", "High": "1",
                           "Low": "1", "Close": "13.58", "Volume": "1"}]

    monkeypatch.setattr(hq_pb, "get_bars", fake_bars)
    c = _bare_client()
    with pytest.raises(errors.OutOfScope):
        c.get_bars("00700")
    assert served == [], "一次都不许发：发出去就等于让服务端去猜 000700"
