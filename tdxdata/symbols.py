# -*- coding: utf-8 -*-
"""标的代码归一化。

对外统一 `"sh600519"` / `"600519"` / `(Market.SH, "600519")` 三种写法都接受，
内部一律转成 `(market, 6 位代码)`。
"""
from __future__ import annotations

__all__ = ["parse", "symbol_of", "market_of", "market_by_code", "clean"]

_PREFIX = {"sh": 1, "sz": 0, "bj": 2}
_SUFFIX = {1: "sh", 0: "sz", 2: "bj"}

# ── 代码段 → 市场（2026-10-26 修正，依据服务端真值）─────────────────────────
# **来源**：`HQServ.PBMultiHQ` 返回的 `Setcode` 列（309 条服务端真值，
# 夹具在 `tests/fixtures/市场码真值样本.json`，用例 `test_symbols_market.py` 全量比对）。
#
# 修正前的老规则是「5/6 开头=沪、83/87/88/92/43/82=北、其余=深」，
# 实测在 309 条样本上 **错 92 条（29.8%）**，错的全是：
#   900 沪B(41) / 113(21) / 118(10) / 204 沪逆回购(9) / 110(4) / 111(3) / 241~243(1 each) / 524(1)
# 后果是真机上 `get_bars("900901")` 抛 `EmptyResult`（报"K线无数据"），用户会以为这票不存在，
# 其实只要把 market 从 0 改成 1 就能取到（实测 10 行、0.569 元）。
#
# 规则：**3 位前缀优先**（一组前缀在真值里都只对应唯一 setcode），1 位兜底。
# 新增品种时**只加表项，不要改逻辑**。
_PREFIX3 = {
    # 沪市 (1)
    "110": 1, "111": 1, "113": 1, "118": 1,   # 沪转债
    "204": 1,                                   # 沪逆回购（1 开头但不在深）
    "241": 1, "242": 1, "243": 1,               # 沪债券
    "900": 1,                                   # 沪 B
    "999": 1,                                   # 沪市指数（999999 上证指数，2026-10-26 手机端确认）
    "880": 1,                                   # 板块指数（880903 水产品 / 880001）；
                                                # 2026-10-26 实测：get_bars("sh880903")=561.57，
                                                # 且抓包里 PBZSTDDE 的 {"Code":"880001","Setcode":1}
    "881": 1,                                   # 行业板块指数（881007 油气开采 / 881218）
                                                # 2026-10-27 实测：PBCombHQ `Setcode=1` → 有数据
                                                # （['881007','1','油气开采']）；`Setcode=2`（北交所）
                                                # 一律 EmptyResult，3/4/5 也全空。
                                                # ⚠️ 880xxx 与 881xxx 是**同一族通达信板块指数**，
                                                # **不是北交所证券** —— 漏了这一项时它们会掉进
                                                # `_HEAD1["8"]` 的北交所兜底，于是 get_quotes/
                                                # get_bars 都取不到（旧 bug，2026-10-27 修）。
    # 深市 (0)
    "123": 0, "127": 0, "131": 0,               # 深转债 / 深回购
    "200": 0, "201": 0,                          # 深 B
    "524": 0,                                    # ⚠️ 5 开头但服务端是深（实测）
    "399": 0,                                    # 深市指数（399001 深证成指）
    # 北交所 (2)
    "920": 2, "430": 2, "830": 2, "870": 2, "820": 2,
}
# 1 位兜底（表里没命中时用）。注意 `1` 兜底必须是 0 —— 深转债 123/127/131 都在 1 段。
# `8` 兜底是北交所证券（43xxxx/83xxxx/87xxxx/92xxxx 里的北交所票；`88xxxx` 里的
# **板块指数 880/881 已在上表单独覆盖**，别把"8 开头"一律当北交所）。
_HEAD1 = {"6": 1, "5": 1, "0": 0, "3": 0, "1": 0, "2": 0, "4": 2, "8": 2, "9": 0}


def market_by_code(code: str) -> int:
    """由 6 位代码推断市场码（3 位前缀优先，1 位兜底）。数据来源见 `_PREFIX3`。"""
    m = _PREFIX3.get(code[:3])
    if m is not None:
        return m
    return _HEAD1.get(code[0], 0)


def clean(code) -> str:
    """取出 6 位数字代码（容忍 `sh`/`sz`/`bj` 前缀、`.SH` 后缀、空格）。"""
    s = str(code).strip().lower()
    for suf in (".sh", ".sz", ".bj", ".ss"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    if s[:2] in _PREFIX:
        s = s[2:]
    return s[:6]


def parse(symbol) -> tuple[int, str]:
    """任意写法 → `(market, code)`。

    不带前缀时按**代码段**推断市场（3 位前缀表 → 1 位兜底，见 `_PREFIX3`）。
    推断规则来自服务端 `Setcode` 真值，**不要在别处重复实现**（历史教训：
    老规则把沪 B `900xxx`、沪转债 `11xxxx`、沪逆回购 `204xxx` 全判成深市，
    309 条样本里错 92 条）。

    >>> parse("sh600519")
    (1, '600519')
    >>> parse("600519")
    (1, '600519')
    >>> parse("000001")
    (0, '000001')
    >>> parse("920091")
    (2, '920091')
    >>> parse("900901")      # 沪 B：老规则会错判成深市
    (1, '900901')
    >>> parse("113711")      # 沪转债
    (1, '113711')
    >>> parse("123283")      # 深转债
    (0, '123283')
    >>> parse("524001")      # 5 开头但服务端是深
    (0, '524001')
    """
    if isinstance(symbol, (tuple, list)) and len(symbol) >= 2:
        m, c = symbol[0], symbol[1]
        market = int(m) if not isinstance(m, str) else _PREFIX.get(m.lower(), 0)
        return market, clean(c)

    s = str(symbol).strip().lower()
    if s[:2] in _PREFIX:
        return _PREFIX[s[:2]], clean(s)
    code = clean(s)
    if not code or not code.isdigit():
        raise ValueError("无法识别的标的：%r（需要 6 位数字代码，可带 sh/sz/bj 前缀）"
                         % symbol)
    return market_by_code(code), code


def market_of(symbol) -> int:
    return parse(symbol)[0]


def symbol_of(market: int, code: str) -> str:
    return _SUFFIX.get(int(market), "") + clean(code)
