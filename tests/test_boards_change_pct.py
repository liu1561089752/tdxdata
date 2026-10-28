# -*- coding: utf-8 -*-
"""`get_boards` 的 `change_pct`（阶段 4）：**可修，不是该删**。

背景（调研 Q5 的实证结论）
--------------------------
`sections/boards.py` 曾经在收尾处**无条件**写 `row["change_pct"] = None`，
把上游回包里**已经有值**的 `CLOSE`(昨收) / `NOW`(现价) 两列白扔了。
于是全库唯一的"板块涨跌幅"永远是 `None` —— 那不是"服务端没给"，
那是"拿到了又扔掉"。本文件守的就是这条：

1. `PBMultiHQ` 系列（concept/style/area/industry1-3）：`(now/close-1)*100` 现算；
2. `industry`（`app_bklist_ag` 9 列**确实没有价格**）：用同码去 `industry2` 回填，
   2026-10-27 实网 128/128 同码全中；
3. **红线**：`close <= 0` / `now <= 0` / 任一缺失 → `None`。
   "0%" 是断言（今天没涨没跌），不能拿来当"我不知道"。
4. 回填不到的单条留 `None` + 记账（`change_pct_unavailable`），不许猜。
5. 口径写进 `meta["change_pct_method"]`，用户不看 docstring 也能知道数怎么来的。
"""
from __future__ import annotations

import pytest

from tdxdata.models import Table
from tdxdata.sections import boards

# ── 假 client：call() 按命令名回表；board_page() 按 market 回板块域 ───────
_MULTIHQ_COLS = ["Code", "Setcode", "Name", "CLOSE", "NOW", "AMOUNT"]


class FakeClient:
    def __init__(self, tables=None, pages=None):
        self.tables = dict(tables or {})
        self.pages = dict(pages or {})       # market -> 第一页行（后续页空）
        self.calls: list = []

    def call(self, cmd, ctl, params=None, **_kw):
        self.calls.append((cmd, ctl, params))
        cols, rows = self.tables[cmd]
        return Table(cols=list(cols), rows=[dict(r) for r in rows], cmd=cmd)

    def board_page(self, offset=0, page_size=44, market=10004):
        self.calls.append(("PBMultiHQ.board_page", 0x12,
                           {"offset": offset, "page_size": page_size,
                            "market": market}))
        if offset > 0:
            return list(_MULTIHQ_COLS), []
        return list(_MULTIHQ_COLS), [dict(r) for r in self.pages.get(market, [])]


def _bklist(rows):
    """`app_bklist_ag`：9 列，**没有价格**（这就是 industry 支路的难点）。"""
    cols = ["N001", "N002", "N003", "N004", "N005", "N006", "N007", "N008", "N009"]
    out = []
    for code, name, lv1_code, lv1_name in rows:
        out.append({"N001": "X1001", "N002": code, "N003": name, "N004": "1",
                    "N005": "X1001", "N006": lv1_code, "N007": lv1_name,
                    "N008": "X1001", "N009": name})
    return cols, out


def _mhq(rows):
    """PBMultiHQ 板块域：(代码, 名称, 昨收, 现价)。"""
    return [{"Code": c, "Setcode": 1, "Name": n, "CLOSE": cl, "NOW": nw,
             "AMOUNT": "0.00"} for c, n, cl, nw in rows]


# ══════════════════════════════════════════════════════════════════════════
# 一、`_pct`：公式与红线
# ══════════════════════════════════════════════════════════════════════════
def test_pct_formula_is_percentage_not_ratio():
    assert boards._pct(110.0, 100.0) == pytest.approx(10.0)
    assert boards._pct(101.18, 100.0) == pytest.approx(1.18)
    assert boards._pct(99.0, 100.0) == pytest.approx(-1.0)
    assert boards._pct(100.0, 100.0) == 0.0


@pytest.mark.parametrize("now,close", [
    (None, 100.0), (100.0, None), (None, None),
    (110.0, 0), (110.0, 0.0), (110.0, -1.0),     # 分母非正：不许算出 inf/巨大数
    (0, 100.0), (-5.0, 100.0),                   # 分子非正：不是真成交价
])
def test_pct_unknown_is_none_never_zero(now, close):
    """**红线**：拿不准就是 `None`，绝不返回 0（0 会被当成"今天没涨没跌"）。"""
    got = boards._pct(now, close)
    assert got is None, (now, close, got)


def test_pct_accepts_string_inputs():
    assert boards._pct("110.0", "100.0") == pytest.approx(10.0)
    assert boards._pct("—", "100.0") is None      # 脏值当缺失


# ══════════════════════════════════════════════════════════════════════════
# 二、PBMultiHQ 支路：现算
# ══════════════════════════════════════════════════════════════════════════
def test_concept_change_pct_is_computed_from_now_close():
    fc = FakeClient(pages={10004: _mhq([
        ("880903", "水产品", "551.40", "545.85"),
        ("880904", "机器人概念", "2841.55", "2850.12"),
    ])})
    t = boards.get_boards(fc, "concept")

    r0, r1 = t.rows[0], t.rows[1]
    assert r0["now"] == 545.85 and r0["close"] == 551.40
    assert r0["change_pct"] == pytest.approx((545.85 / 551.40 - 1) * 100)   # 约 -1.01
    assert r1["change_pct"] == pytest.approx((2850.12 / 2841.55 - 1) * 100)  # 约 +0.30
    assert "change_pct" in t.cols and "now" in t.cols and "close" in t.cols
    assert t.provenance.extra["change_pct_method"] == "PBMultiHQ.NOW/CLOSE"
    assert t.provenance.extra["unavailable"] == [], "算得出来就不许再声称这一列不可用"


def test_six_domains_all_carry_real_change_pct():
    """六类板块域（industry1/2/3 · concept · style · area）全都现算得出来。"""
    pages = {m: _mhq([("%06d" % (880000 + m), "域%d" % m, "1000.00", "1010.00")])
             for m in (10008, 10001, 10002, 10004, 10005, 10006)}
    fc = FakeClient(pages=pages)
    for kind in ("industry1", "industry2", "industry3", "concept", "style", "area"):
        t = boards.get_boards(fc, kind)
        assert t.rows[0]["change_pct"] == pytest.approx(1.0), kind
        assert t.provenance.extra["change_pct_method"] == "PBMultiHQ.NOW/CLOSE", kind


def test_missing_price_row_stays_none_but_column_kept():
    """单条缺价 → 该行 `None`（不是 0），列仍在（col 是契约字段）。"""
    fc = FakeClient(pages={10004: _mhq([
        ("880903", "水产品", "551.40", "545.85"),
        ("880904", "机器人概念", "—", "2850.12"),      # 脏值 → 昨收 None
    ])})
    t = boards.get_boards(fc, "concept")
    assert t.rows[1]["change_pct"] is None
    assert t.rows[0]["change_pct"] is not None
    assert "change_pct" in t.cols
    assert t.provenance.extra["unavailable"] == ["change_pct"]
    assert t.provenance.extra["change_pct_unavailable"] == ["880904"]


# ══════════════════════════════════════════════════════════════════════════
# 三、industry 支路：industry2 同码回填
# ══════════════════════════════════════════════════════════════════════════
def _industry_client(*, ind2_rows, ind2_market=10001):
    """industry 清单（无价格）+ 可配的 industry2 板块域。"""
    return FakeClient(
        tables={"CWServ.app_bklist_ag": _bklist([
            ("881002", "煤炭开采", "X10", "煤炭"),
            ("881005", "焦炭加工", "X10", "煤炭"),
        ])},
        pages={ind2_market: ind2_rows},
    )


def test_industry_backfills_from_industry2():
    fc = _industry_client(ind2_rows=_mhq([
        ("881002", "煤炭开采", "1200.00", "1212.00"),
        ("881005", "焦炭加工", "800.00", "796.00"),
    ]))
    t = boards.get_boards(fc, "industry")

    assert t.provenance.extra["change_pct_method"] == "industry2 回填"
    assert t.rows[0]["board_code"] == "881002"
    assert t.rows[0]["now"] == 1212.0 and t.rows[0]["close"] == 1200.0
    assert t.rows[0]["change_pct"] == pytest.approx(1.0)
    assert t.rows[1]["change_pct"] == pytest.approx(-0.5)
    assert "change_pct_unavailable" not in t.provenance.extra
    assert t.provenance.extra["unavailable"] == []
    # 那一轮 industry2 确实打出去了（market=10001）
    assert ("PBMultiHQ.board_page", 0x12,
            {"offset": 0, "page_size": 44, "market": 10001}) in fc.calls


def test_industry_backfill_miss_is_recorded_not_guessed():
    """回填失败的单条：留 `None` + 记进 `change_pct_unavailable`，**绝不猜**。"""
    fc = _industry_client(ind2_rows=_mhq([
        ("881002", "煤炭开采", "1200.00", "1212.00"),
        # 881005 在 industry2 里没有 → 回填不到
    ]))
    t = boards.get_boards(fc, "industry")

    assert t.rows[0]["change_pct"] == pytest.approx(1.0)
    assert t.rows[1]["change_pct"] is None, "回填不到必须 None（不是 0）"
    assert t.provenance.extra["change_pct_unavailable"] == ["881005"]
    note = t.provenance.extra["change_pct_note"]
    assert "industry2" in note and "881005" in note
    assert t.rows[1]["now"] is None and t.rows[1]["close"] is None


def test_industry_when_industry2_is_empty_says_so():
    """industry2 这次 0 行：industry 清单本身照常返回，但必须**明说**没算出来。"""
    fc = _industry_client(ind2_rows=[])
    t = boards.get_boards(fc, "industry")
    assert len(t.rows) == 2, "行业清单不能因为价格没取到就整张失败"
    assert all(r["change_pct"] is None for r in t.rows)
    assert t.provenance.extra["change_pct_method"] == "industry2 回填"
    assert "industry2" in t.provenance.extra["change_pct_note"]
    assert t.provenance.extra["unavailable"] == ["change_pct"]


def test_industry_name_lookup_does_not_pay_for_prices():
    """内部按名字查代码时**不该**多打一轮 industry2（省 10 秒）。"""
    fc = _industry_client(ind2_rows=[])
    boards._fetch_industry_list_light(fc)
    assert not any(c[0] == "PBMultiHQ.board_page" for c in fc.calls)


# ══════════════════════════════════════════════════════════════════════════
# 四、`all`：两条支路口径不同，必须在 meta 里分开写
# ══════════════════════════════════════════════════════════════════════════
def test_all_kind_documents_both_branches():
    fc = FakeClient(
        tables={"CWServ.app_bklist_ag": _bklist([("881002", "煤炭开采", "X10", "煤炭")])},
        pages={10001: _mhq([("881002", "煤炭开采", "1200.00", "1212.00")]),
               10004: _mhq([("880903", "水产品", "551.40", "545.85")])},
    )
    t = boards.get_boards(fc, "all")
    method = t.provenance.extra["change_pct_method"]
    assert "industry2 回填" in method and "PBMultiHQ.NOW/CLOSE" in method
    assert t.rows[0]["change_pct"] == pytest.approx(1.0)         # industry 支路
    assert t.rows[-1]["change_pct"] == pytest.approx((545.85 / 551.40 - 1) * 100)


# ══════════════════════════════════════════════════════════════════════════
# 五、幽灵列：`20ZAF` 不许再出现在任何代码路径/注释里
# ══════════════════════════════════════════════════════════════════════════
def test_ghost_column_is_gone_from_module():
    """`20ZAF` 是**幽灵列**：全库没有任何代码路径请求过它（实网 32 个候选列名
    一次发进 `field2` 全部被服务端丢弃）。残留在注释里会误导下一个人去映射它。
    """
    import inspect

    src = inspect.getsource(boards)
    assert "20ZAF" not in src and "20zaf" not in src
    # 连"涨跌幅拿不到"这类过期结论也不许留
    assert "恒 None" not in src and "恒为 None" not in src
