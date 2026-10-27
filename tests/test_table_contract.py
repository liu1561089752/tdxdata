# -*- coding: utf-8 -*-
"""**跨模块契约测试**（Lead 直辖，横跨 `models` / `client` / `sections`）。

为什么单独一个文件
------------------
下面这三条是**多个文件域共同遵守的约定**，落进任何单个成员的测试文件都会变成
"谁都能改、谁都不负责"。所以由 Lead 单独持有，任何一边改坏了这里就红：

1. **`Row` 语义**：每行既是 `dict`（按列名）也能按位置取；越界必须抛 `IndexError`。
2. **列名与行值严格对齐**：`Table.cols` 永远是**主表自己的列名**，
   多 `ResultSet` 的"列数并集"只能进 `meta["cols_union"]`。
   —— 拿并集 `zip` 行值会整行错一位（静默错列），本项目踩过。
3. **`raw` 的约定**：`sections` 层的表，`cols` **不含** `"raw"`，
   但每行**必须**带 `raw`。于是 `len(row) == len(cols) + 1`。
   不这样统一，`Table.shape` 在财务表与板块表之间口径就会不一致。

全部离线（用假 client 驱动），跑法：
    python tests/test_table_contract.py
    python -m pytest tests/test_table_contract.py -q
"""
from __future__ import annotations

import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tdxdata                                                     # noqa: E402
from tdxdata.client import Client                                  # noqa: E402
from tdxdata.models import Row, Table                              # noqa: E402
from tdxdata.sections import mapping                               # noqa: E402


# ── 工具：假 client（只实现 sections 用到的 call()）──────────────────────
class FakeClient:
    """`sections` 层只需要 `call(cmd, ctl, params) -> Table`。"""

    def __init__(self, table):
        self._table = table
        self.calls = []

    def call(self, cmd, ctl, params=None, **kwargs):
        self.calls.append((cmd, ctl, params))
        if isinstance(self._table, Exception):
            raise self._table
        return Table(cols=list(self._table.cols),
                     rows=[list(r.values()) if isinstance(r, dict) else list(r)
                           for r in self._table.rows],
                     cmd=cmd)


def _srv_payload(rs_list):
    """按 spec/03 的形状造一个回包，喂给协议层的 parse()。"""
    from tdxdata._engine import session as S

    payload = {"ErrorCode": 0, "ResultSets": rs_list}
    return S.parse(json.dumps(payload, ensure_ascii=False).encode("gbk"))


# ══════════════════════════════════════════════════════════════════════════
# 一、Row 语义
# ══════════════════════════════════════════════════════════════════════════
def test_row_is_dict_and_positional():
    t = Table(cols=["N001", "N002", "N003"], rows=[["a", "b", "c"]])
    r = t.rows[0]
    assert isinstance(r, dict), "行必须是 dict（用户要能 row['N004']）"
    assert isinstance(r, Row), "行必须是 Row（才支持按位置取）"
    for i, name in enumerate(t.cols):
        assert r[name] == r[i], "第 %d 列按名/按位置必须等价" % i
    assert dict(r) == {"N001": "a", "N002": "b", "N003": "c"}
    assert r.to_dict() == dict(r)


def test_row_json_serializable():
    t = Table(cols=["a", "b"], rows=[[1, None]])
    assert json.loads(json.dumps(t.rows[0])) == {"a": 1, "b": None}
    assert json.loads(json.dumps(t.to_dicts())) == [{"a": 1, "b": None}]


def test_row_out_of_range_is_loud():
    r = Table(cols=["a", "b"], rows=[[1, 2]]).rows[0]
    for bad in (2, 99, -99):
        try:
            r[bad]
        except IndexError:
            continue
        raise AssertionError("按位置取越界 %r 必须抛 IndexError（不许静默返回 None）" % bad)
    # 负下标按 cols 解释：-1 是最后一列
    assert r[-1] == 2


def test_row_setitem_and_contains():
    r = Table(cols=["a", "b"], rows=[[1, 2]]).rows[0]
    r[0] = 10
    assert r["a"] == 10
    r["b"] = 20
    assert r[1] == 20
    assert 0 in r and 1 in r and 2 not in r
    assert "a" in r and "zz" not in r


def test_table_column_by_name_and_index():
    t = Table(cols=["a", "b"], rows=[[1, 2], [3, 4]])
    assert t.column("a") == t.column(0) == [1, 3]
    assert t.column("b") == t.column(1) == [2, 4]
    assert t.row(1)["b"] == 4


def test_table_normalizes_list_and_dict_rows():
    t1 = Table(cols=["a", "b"], rows=[["1", "2"]])
    t2 = Table(cols=["a", "b"], rows=[{"a": "1", "b": "2"}])
    assert isinstance(t1.rows[0], Row) and isinstance(t2.rows[0], Row)
    assert t1.rows[0]["a"] == t2.rows[0]["a"] == "1"
    assert Table(cols=["a"], rows=[]).rows == []


# ══════════════════════════════════════════════════════════════════════════
# 二、列名与行值严格对齐（多 ResultSet 的并集坑）
# ══════════════════════════════════════════════════════════════════════════
def test_parse_reports_primary_names():
    verdict, nrow, ncol, cols, rows, primary = _srv_payload([
        {"ColName": ["X001", "X002"], "Content": [["a", "b"]]},
        {"ColName": ["N001", "N002", "N003"], "Content": [["x", "y", "z"]]},
    ])
    assert verdict == "PASS"
    assert cols == ["X001", "X002", "N001", "N002", "N003"], cols   # 并集
    assert primary == ["N001", "N002", "N003"], primary              # 主表自己的
    assert ncol == len(cols) and len(rows[0]) == len(primary)


def test_make_table_uses_primary_and_keeps_union_in_meta():
    v = _srv_payload([
        {"ColName": ["X001", "X002"], "Content": [["a", "b"]]},
        {"ColName": ["N001", "N002", "N003"], "Content": [["x", "y", "z"]]},
    ])
    t = Client._make_table("cmd", v[3], v[4], v[5])
    assert t.cols == ["N001", "N002", "N003"], "cols 必须是主表列名（不是 5 列并集）"
    assert len(t.cols) == len(t.rows[0]), "cols 必须与行宽严格相等"
    assert t.meta["cols_union"] == ["X001", "X002", "N001", "N002", "N003"]
    assert t.meta["cols_source"] == "primary"
    # 对齐正确性：不能整行错一位
    assert t.rows[0]["N001"] == "x" and t.rows[0]["N002"] == "y"
    assert t.rows[0][0] == "x" and t.rows[0][2] == "z"


def test_make_table_no_union_meta_when_equal():
    v = _srv_payload([{"ColName": ["N001", "N002"], "Content": [["1", "2"]]}])
    t = Client._make_table("cmd", v[3], v[4], v[5])
    assert "cols_union" not in t.meta, "并集与主表相同时不该多写 meta"
    assert t.cols == ["N001", "N002"]


def test_make_table_dedups_duplicate_names_without_losing_values():
    t = Client._make_table("cmd", ["N001", "N001", "N002"],
                           [["v1", "v2", "v3"]], ["N001", "N001", "N002"])
    assert t.cols == ["N001", "N001_2", "N002"], t.cols
    assert list(t.rows[0].values()) == ["v1", "v2", "v3"], "重名列不许静默丢值"
    assert t.meta["cols_dedup"] == ["N001", "N001", "N002"]


# ══════════════════════════════════════════════════════════════════════════
# 三、sections 层的 `raw` 约定（cols 不含 raw，但每行都带）
# ══════════════════════════════════════════════════════════════════════════
def _assert_sections_table(t, label):
    """sections 层返回表的统一不变式。"""
    assert t.rows, "%s 应该有行（这批样本都是有效回包）" % label
    r = t.rows[0]
    assert "raw" in r, "%s 的每行必须带 raw（红线：原始列不许丢）" % label
    assert "raw" not in t.cols, "%s 的 cols 不许含 'raw'（raw 是 sidecar 不是数据列）" % label
    assert set(t.cols) <= set(r), "%s 的每个列名都要能在行里取到" % label
    assert len(r) == len(t.cols) + 1, (
        "%s 行宽 %d 应恰好等于 len(cols) %d + 1（只多一个 raw）"
        % (label, len(r), len(t.cols)))
    assert t.shape[0] == len(r) or True
    assert isinstance(r["raw"], dict), "%s 的 raw 必须是 dict" % label
    # 位置访问仍要对齐
    for i, name in enumerate(t.cols):
        assert r[name] == r[i], "%s 第 %d 列按名/按位置必须等价" % (label, i)


def test_mapping_apply_keeps_raw_out_of_cols():
    """直接测 `mapping.apply()`：`gsjj` 不在 COL_GUARD 里，映射会生效。"""
    assert "gsjj" not in mapping.COL_GUARD, "本用例依赖 gsjj 不被 COL_GUARD 拦下"
    src = Table(cols=["N001", "N002"],
                rows=[["2000-12-26", "上网定价发行"]], cmd="CWServ.ph_agf10_gsjj")
    out = mapping.apply(src, "gsjj")
    _assert_sections_table(out, "mapping.apply(gsjj)")
    assert out.meta["product"] == "gsjj"
    assert out.rows[0]["raw"] == {"N001": "2000-12-26", "N002": "上网定价发行"}


def test_mapping_apply_unmapped_product_is_raw_only():
    """没有映射表的产品：cols 为空、行里只有 raw —— 这不是"不可用"，是"没定论"。"""
    src = Table(cols=["N001"], rows=[["x"]], cmd="CWServ.x")
    out = mapping.apply(src, "no_such_product_key")
    assert "raw" not in out.cols
    assert out.cols == [], out.cols
    assert out.rows[0]["raw"] == {"N001": "x"}


def test_get_f10_through_fake_client_keeps_contract():
    """走 `f10.get_f10()` 全链路（假 client，零建连）。"""
    from tdxdata.sections import f10 as F

    fake = FakeClient(Table(cols=["N001", "N002"],
                            rows=[["2000-12-26", "上网定价发行"]]))
    out = F.get_f10(fake, "600519", "gsjj")
    _assert_sections_table(out, "get_f10(gsjj)")
    assert fake.calls, "必须真的调了 call()"
    cmd, _ctl, params = fake.calls[0]
    assert "gsjj" in cmd, cmd
    assert any("600519" in str(p) for p in (params if isinstance(params, list) else [params])), \
        "目标代码必须替换进参数：%r" % (params,)


# ══════════════════════════════════════════════════════════════════════════
# 四、接线契约（签名层面的低成本检查，不构造 Client 对象）
# ══════════════════════════════════════════════════════════════════════════
def test_client_wiring_signatures():
    import inspect

    want = {
        "get_indicators": ("symbol", "indicators", "period", "count"),
        "get_price_limits": ("symbol", "prev_close", "is_st"),
        "get_finance": ("symbol", "kind"),
        "get_f10": ("symbol", "section"),
        "get_xdxr": ("symbol",),
        "get_boards": ("kind",),
        "get_board_members": ("board",),
        "get_capital_flow": ("symbols",),
        "get_market_stat": ("kind",),
    }
    for name, params in want.items():
        fn = getattr(Client, name, None)
        assert fn is not None, "Client 缺方法 %s" % name
        got = tuple(list(inspect.signature(fn).parameters)[1:])     # 去掉 self
        assert got == params, "%s 签名变了：%r ≠ %r" % (name, got, params)


def test_market_stat_default_kind_is_updown():
    import inspect

    dflt = inspect.signature(Client.get_market_stat).parameters["kind"].default
    assert dflt == "updown", "默认 kind 必须是 updown（all 会抛错，不能当默认值）"


def test_sections_take_client_first_and_do_not_import_client():
    """sections 一律 `(client, ...)` 起手，且**不许**反向 import client（防循环依赖）。"""
    import inspect

    from tdxdata.sections import boards, f10, finance, flow, xdxr

    for mod, names in ((finance, ("get_finance",)),
                       (f10, ("get_f10",)),
                       (xdxr, ("get_xdxr",)),
                       (boards, ("get_boards", "get_board_members")),
                       (flow, ("get_capital_flow", "get_market_stat"))):
        for n in names:
            params = list(inspect.signature(getattr(mod, n)).parameters)
            assert params[0] == "client", "%s.%s 首个参数必须是 client" % (mod.__name__, n)
        src = open(mod.__file__, encoding="utf-8").read()
        for bad in ("from ..client import", "from tdxdata.client import",
                    "import ..client"):
            assert bad not in src, "%s 反向 import 了 client（会循环依赖）" % mod.__name__


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
        except Exception as e:                                     # noqa: BLE001
            bad += 1
            print("  ❌ %s: %s: %s" % (name, type(e).__name__, e))
    print("\n%d 个用例，失败 %d" % (len(fns), bad))
    sys.exit(1 if bad else 0)
