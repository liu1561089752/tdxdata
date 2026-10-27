# -*- coding: utf-8 -*-
"""D1 回归：`symbols.parse` 的市场码推断，必须与服务端 `Setcode` 真值一致。

为什么单独一个文件
------------------
市场码是**隐式**参数 —— 判错了不会报错，只会变成"K线无数据"，
用户会以为这票不存在。历史上老规则在 309 条真值样本上**错 92 条（29.8%）**：
沪 B `900xxx`、沪转债 `11xxxx`、沪逆回购 `204xxx` 全被判成深市。

所以这里不做"抽样测几个"，而是**全量比对服务端真值夹具**
（`tests/fixtures/市场码真值样本.json`，由 `HQServ.PBMultiHQ` 的 `Setcode` 列导出）。

跑法：
    python tests/test_symbols_market.py
    python -m pytest tests/test_symbols_market.py -q
"""
from __future__ import annotations

import collections
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import pytest                                                       # noqa: E402
from tdxdata import errors, symbols                                 # noqa: E402

FIX = os.path.join(_HERE, "fixtures", "市场码真值样本.json")
with open(FIX, encoding="utf-8") as f:
    _FX = json.load(f)
TRUTH = _FX["map"]


# ══════════════════════════════════════════════════════════════════════════
# 一、核心判据：309/309 全对
# ══════════════════════════════════════════════════════════════════════════
def test_market_of_matches_server_setcode():
    """`symbols.market_of` 与服务端 `Setcode` **逐条**一致（一个都不许错）。"""
    bad = {c: (want, symbols.market_of(c)) for c, want in TRUTH.items()
           if symbols.market_of(c) != want}
    assert not bad, "与服务器 Setcode 不一致 %d 条：%s" % (len(bad), list(bad.items())[:8])


def test_fixture_shape():
    """夹具自检：别让一个坏掉的夹具把用例骗绿。"""
    assert _FX["count"] == len(TRUTH) == 309, (_FX.get("count"), len(TRUTH))
    assert all(len(c) == 6 and c.isdigit() for c in TRUTH), "存在非 6 位数字代码"
    assert set(TRUTH.values()) <= {0, 1, 2}, sorted(set(TRUTH.values()))
    # 三个市场都得有样本，否则"全对"没意义
    per = collections.Counter(TRUTH.values())
    assert set(per) == {0, 1, 2} and min(per.values()) >= 2, dict(per)


def test_old_rule_would_fail_92():
    """回归证据：**老规则**在同一份夹具上正好错 92 条。

    这条不是在测实现，是在钉住"改动确有必要"——如果哪天夹具换了，
    这个数字变了，说明真值样本本身动了，得回头看。
    """
    def old_rule(code):
        if code[0] in "56":
            return 1
        if code[:2] in ("83", "87", "88", "92", "43", "82"):
            return 2
        return 0

    bad = [c for c, want in TRUTH.items() if old_rule(c) != want]
    assert len(bad) == 92, "老规则应当错 92 条，实际 %d" % len(bad)
    # 错的必须全是这几个品种（沪B / 沪转债 / 沪逆回购 / 沪债券 / 5开头深市）
    fams = collections.Counter(c[:3] for c in bad)
    assert set(fams) == {"900", "110", "111", "113", "118", "204", "241", "242", "243", "524"}, fams


# ══════════════════════════════════════════════════════════════════════════
# 二、结构不变量：3 位前缀必须无歧义、且"反常识"的前缀必须显式入表
# ══════════════════════════════════════════════════════════════════════════
def test_prefix3_unambiguous_in_truth():
    """真值里同一个 3 位前缀只能对应一个 setcode —— 这是"前缀优先"成立的前提。"""
    by3 = collections.defaultdict(set)
    for c, s in TRUTH.items():
        by3[c[:3]].add(s)
    bad = {p: s for p, s in by3.items() if len(s) > 1}
    assert not bad, "前缀有歧义，不能靠 3 位前缀推断：%s" % bad


def test_prefixes_that_beat_head1_must_be_explicit():
    """**结构护栏**：凡是「3 位前缀的真值与 1 位兜底不一致」的，必须在 `_PREFIX3` 里有显式表项。

    这样以后夹具里新增一个前缀（比如新的债券段）时，如果只在 `_HEAD1` 里蒙对了、
    却没进表，这条会红 —— 逼着把结论写进表里并留下注释。
    """
    by3 = {}
    for c, s in TRUTH.items():
        by3.setdefault(c[:3], s)
    missing = {p: s for p, s in by3.items()
               if s != symbols._HEAD1.get(p[0], 0) and p not in symbols._PREFIX3}
    assert not missing, "这些前缀与 1 位兜底不一致，却没进 _PREFIX3：%s" % missing
    # 反向：进了表的，真值必须支持（防止表里写错或写多余）
    contradict = {p: (v, by3[p]) for p, v in symbols._PREFIX3.items()
                  if p in by3 and by3[p] != v}
    assert not contradict, "_PREFIX3 与真值冲突：%s" % contradict


def test_market_by_code_is_the_single_source():
    """`parse` 与 `market_by_code` 必须是同一套规则（不许两处各写一份）。"""
    for c in TRUTH:
        assert symbols.parse(c) == (symbols.market_by_code(c), c)


def test_hq_pb_market_matches_too():
    """**关键回归**：`hq_pb._market_of` 也必须同源。

    它原来自带一份老规则，导致 `symbols.parse("900901")` 已算出 1、
    而 `_args_pbfxt` 又把报文里的市场码改回 0 → 服务端回空 →
    用户看到「K线无数据」（2026-10-26 真机实测）。
    """
    from tdxdata._engine import hq_pb

    bad = {c: (want, hq_pb._market_of(c)) for c, want in TRUTH.items()
           if hq_pb._market_of(c) != want}
    assert not bad, "hq_pb._market_of 与真值不一致 %d 条：%s" % (len(bad), list(bad.items())[:8])


# ══════════════════════════════════════════════════════════════════════════
# 三、这条缺陷的"现场"：显式列几个曾经判错的品种
# ══════════════════════════════════════════════════════════════════════════
def test_known_hard_cases():
    """曾经判错的品种，逐个钉住**市场码**（与"有没有 K 线"无关）。

    ⚠️ 别在这条里断言"某只取不到数据"：市场码正确 ≠ 该代码有行情。
    实测沪转债 `110077/113054/111012/118013` 都能取到 K 线，只有 `113711`
    （疑似已摘牌）取不到 —— 市场码对它同样是 1，也是**对的**。
    """
    cases = {
        "900901": 1,   # 沪 B —— 老规则判 0，真机「K线无数据」
        "900940": 1,
        "113711": 1,   # 沪转债（疑似已摘牌，但市场码仍是 1）
        "110077": 1,
        "118012": 1,
        "204001": 1,   # 沪逆回购
        "241001": 1,   # 沪债券
        "524001": 0,   # ⚠️ 5 开头但服务端是深
        "123283": 0,   # 深转债
        "200011": 0,   # 深 B
        "600519": 1,   # 沪 A
        "688318": 1,   # 科创
        "000001": 0,   # 深 A
        "300750": 0,   # 创业板
        "920091": 2,   # 北交所
        "430047": 2,
    }
    bad = {c: (want, symbols.parse(c)[0]) for c, want in cases.items()
           if symbols.parse(c)[0] != want}
    assert not bad, bad


def test_881xxx_is_board_index_not_bse():
    """`881xxx` 是**行业板块指数**（沪市 1），不是北交所 —— 2026-10-27 修的漏项。

    证据（Lead 实测 + 我复核，`PBCombHQ`）：
      · `881007` `Setcode=1` → 1 行 `['881007','1','油气开采']`；`Setcode=2` → `EmptyResult`
      · `Setcode=0/3/4/5` 也全 `EmptyResult` → 唯一正确市场码是 1
      · `881218` 同族
    旧 bug：前缀表只有 `"880": 1`、没有 `"881"`，881xxx 掉进 `_HEAD1["8"]` 的北交所兜底，
    于是 `get_quotes`/`get_bars` 都取不到（会误报"代码不存在"）。
    """
    assert symbols.parse("881007") == (1, "881007")
    assert symbols.parse("881218") == (1, "881218")
    assert symbols.parse("880903") == (1, "880903"), "880xxx 本来就是对，别被改坏"
    # 显式前缀仍然优先（用户覆盖不许被"顺手修正"）
    assert symbols.parse("bj881007") == (2, "881007")


def test_bse_must_not_be_broken_by_881_fix():
    """⚠️ 这是**全库共用**的映射：加了 `"881": 1` 之后，北交所**一只都不许变**。

    北交所证券段：43xxxx / 83xxxx / 87xxxx / 92xxxx。它们与前缀表里的
    `"430"/"830"/"870"/"820"/"920"` 及 `_HEAD1["8"]` 兜底一致 —— 改 881 不许动到它们。
    """
    for code in ("830799", "920091", "430047", "871981"):
        assert symbols.parse(code)[0] == 2, (code, symbols.parse(code))
    # 88xxxx 里只有 880/881 是板块指数；其余 88 段仍按 1 位兜底走北交所
    assert symbols.parse("889999")[0] == 2, symbols.parse("889999")


def test_market_segments_unchanged_after_881_fix():
    """改 881 不许影响其余各段（5/6→1、0/3→0、9→0、4/8→2）。"""
    for code, want in {
        "600519": 1, "688318": 1, "510300": 1, "999999": 1,
        "000001": 0, "300750": 0, "159915": 0, "399001": 0,
        "200011": 0, "123283": 0, "524001": 0,
        "430047": 2, "830799": 2, "920091": 2,
    }.items():
        assert symbols.market_by_code(code) == want, (code, symbols.market_by_code(code))


def test_explicit_prefix_still_wins():
    """显式前缀/元组是**用户覆盖**，必须优先于代码段推断（别"顺手修正"掉）。"""
    assert symbols.parse("sz900901") == (0, "900901")
    assert symbols.parse("sh123283") == (1, "123283")
    assert symbols.parse(("bj", "600519")) == (2, "600519")
    assert symbols.parse((1, "900901")) == (1, "900901")


def test_docstring_examples_hold():
    """docstring 里的示例必须真成立（改了规则就会红）。"""
    assert symbols.parse("sh600519") == (1, "600519")
    assert symbols.parse("600519") == (1, "600519")
    assert symbols.parse("000001") == (0, "000001")
    assert symbols.parse("920091") == (2, "920091")
    assert symbols.parse("900901") == (1, "900901")
    assert symbols.parse("113711") == (1, "113711")
    assert symbols.parse("123283") == (0, "123283")
    assert symbols.parse("524001") == (0, "524001")


def test_bad_input_still_raises():
    """非数字形态仍然报错（D1 没有放宽输入校验）。"""
    for bad in ("", "abcdef", "  ", ".sh"):
        try:
            symbols.parse(bad)
        except ValueError as e:
            assert "无法识别" in str(e) or "6 位" in str(e)
        else:
            raise AssertionError("%r 必须抛 ValueError" % (bad,))


def test_short_code_is_rejected():
    """**B1 红线修复（阶段 3）**：位数不对必须**显式拒绝**，绝不补零/截断。

    改前的既有行为是"容忍"：`parse("60051") → (1, "60051")`、
    `parse("6005199x") → (1, "600519")`。旧用例的 docstring 自己写着
    「加长度校验属于行为变更…已记进 `reports/P8缺陷修复记录.md` 待办」——
    也就是它**明知是缺陷而暂时钉住现状**，本条即该待办的落地。

    为什么必须改：`00700`（港股腾讯）被判成深市后下游 `PBFXT` **左补 0**，
    `get_bars("00700")` **静默返回「模塑科技 000700」的 K 线**
    （`close=13.58`，与 `get_bars("000700")` 逐条相同，`get_minutes` 同病）。
    补零等于把**另一个标的的数据冒充成你要的标的**，比报错危险得多，
    所以现在抛 `errors.OutOfScope`（消息里指路"要 A 股 000700 请写全 6 位"）。

    ⚠️ `clean()` 仍是**内部宽容版**（`s[:6]` 截断，供已校验的内部值用），
    对它的断言不变 —— 校验责任在 `parse()`。
    """
    with pytest.raises(errors.OutOfScope):
        symbols.parse("60051")                     # 纯数字 5 位：补零会命中 060051
    # 这一条是 `ValueError` 而**不是** `OutOfScope`，分层是有意的：
    # `OutOfScope` 的意思是"这是**另一种品种**（港股/期货），本数据源没有"；
    # `"6005199x"` 里混了字母，是**写错了**，不是别的品种 —— 报 OutOfScope 会误导人去换数据源。
    with pytest.raises(ValueError):
        symbols.parse("6005199x")                  # 超长且含字母：截断会命中 600519
    assert symbols.parse("sh600519") == (1, "600519"), "写全 6 位照旧可用"
    assert symbols.clean(" 600519 ") == "600519"
    assert symbols.clean("600519.SH") == "600519"


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print("  ✅ %s" % name)
        except AssertionError as e:
            bad += 1
            print("  ❌ %s: %s" % (name, e))
        except Exception as e:                                       # noqa: BLE001
            bad += 1
            print("  ❌ %s: %s: %s" % (name, type(e).__name__, e))
    print("\n%d 个用例，失败 %d" % (len(fns), bad))
    sys.exit(1 if bad else 0)
