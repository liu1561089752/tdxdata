# -*- coding: utf-8 -*-
r"""PROD-003 宏观 typed 层：**用 pcap 里抽出的真实回包字节**做语料回放，钉住 44 个模式的列义。

为什么这样测
------------
`tests/fixtures/macro/*.bin` 是 `F:\TDX\raw\pcap\s3_other_20func.pcap` 里**原始回包字节**
（`datacenter.unwrap()` 之后的那一段，就是生产 `Session.ask()` 返回的东西），
每条都带 `manifest.json` 的 `sha256` 与 `msgid` 溯源。所以本文件测的是
「真包 → `session.parse()` → `Table` → 宏观投影」的**整条链**，不是手搓字典。

四类判据
--------
1. **形状**：每个登记模式都有 fixture，回放后 `macro_guard` 不出现、
   `table.cols` 等于登记的标准字段序列、行数与语料一致。
2. **算术**（最有信息量的那类）：把 typed 字段拿去重算语料里已证实的算式 ——
   `czsz` 环比增减 = 本期−上一行（17/17）、`gdp` 同比增量（19/19）、
   三次产业之和 = 总量、`yhhb` 004 分项之和 = 合计、`hg_cal` 的 244/122 分布。
   **列错位会让这些断言立刻红**（这正是「按位置映射」踩雷时的形态）。
3. **守卫 fail-closed**：把别的模式的回包喂进来 → 一列都不映射、原因进 meta。
4. **不猜**：语料没覆盖的产品/模式抛 `NotSupportedError`；参数位不接受就抛 `ParamError`。
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import threading

import pytest

import tdxdata
from tdxdata import errors
from tdxdata._engine import datacenter as dc
from tdxdata._engine import session as sess_mod
from tdxdata.models import Table
from tdxdata.sections import macro as M

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "macro")
MANIFEST = json.load(open(os.path.join(FIX, "manifest.json"), encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════════
# 一、离线回放桩（打在最底层：`Session.ask`）
# ══════════════════════════════════════════════════════════════════════════
def _payload(fname: str) -> bytes:
    with open(os.path.join(FIX, fname), "rb") as fh:
        return fh.read()


def _replies() -> dict:
    """`{(cmd, (参数…)): 真实回包字节}` —— 键与请求体逐字对齐，错一个字就查不到。"""
    out = {}
    for fname, ent in MANIFEST["entries"].items():
        key = (ent["cmd"], tuple(str(p) for p in ent["req_params"]))
        out[key] = _payload(fname)
    return out


class _FakeSession:
    """代表"一条已建好的长连接"；`ask` 返回语料里的真实回包字节。"""

    alive = True
    host = "10.0.0.9"
    port = 7615

    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def ask(self, ctl, cmd, args, *, idle=4.0, tail=0.35):
        params = None
        try:
            obj = json.loads(args.decode("utf-8"))
            params = obj.get("Params") if isinstance(obj, dict) else obj
        except Exception:                                       # noqa: BLE001
            params = None
        key = (cmd, tuple(str(p) for p in (params or ())))
        self.calls.append((cmd, ctl, key))
        if key not in self.replies:
            raise AssertionError(
                "没有这条回包的桩：cmd=%r params=%r（args=%r）—— "
                "说明宏观层发出去的请求参数与语料里抓到的不是同一套" % (cmd, params, args))
        return self.replies[key]

    def reconnect(self):
        pass

    def switch_host(self):
        pass

    def close(self):
        pass


class _NullPacer:
    gap = 0.0
    limited_events = 0

    def wait(self):
        pass

    def note_ok(self):
        pass

    def note_limited(self):
        pass

    def note_limited_soft(self):                                # pragma: no cover
        pass

    def stats(self):
        return {"gap": 0.0, "limited_events": 0}


def _client(replies=None):
    """离线 `Client`：字段初始化跑全、**不建连、不联网**（`_sess` 是回放桩）。"""
    cls = getattr(tdxdata, "Client")
    c = cls.__new__(cls)
    c._dc_mod = dc
    c._session_mod = sess_mod
    c._pacer = _NullPacer()
    c._sess = _FakeSession(replies if replies is not None else _replies())
    c._ensure_lock = threading.Lock()
    c._host = None
    c._timeout = 1.0
    c._gap = 0.0
    c._cache = None
    c._products = {}
    c._pb_products = {}
    c._clock = None
    c._last_call = {}
    c._cal = {}
    c._clock_probe = False            # 关探针：零网络
    c._probe_allowed = False
    c._in_cal = False
    c._load_products()
    c.macro = M.Macro(c)
    return c


@pytest.fixture(scope="module")
def offline():
    return _client()


_ENT = MANIFEST["entries"]


def _rows(product: str, mode: str) -> list:
    """某模式的 typed 行（从 fixture 回放，返回 `[{字段: 值, "raw": {...}}]`）。"""
    c = _client()
    t = c.macro.fetch(product, mode, day="2026-10-20" if mode.startswith("events") else None)
    assert isinstance(t, Table)
    return t.rows


def _col(product: str, mode: str, field: str) -> list:
    return [r[field] for r in _rows(product, mode)]


# ══════════════════════════════════════════════════════════════════════════
# 二、fixture 自身：真实、未漂移、覆盖登记表
# ══════════════════════════════════════════════════════════════════════════
def test_fixtures_are_byte_exact_and_traceable():
    """每个 fixture 的 sha256 必须与 manifest 一致，且能追溯到 pcap + msgid。"""
    assert MANIFEST["source_pcap"] == "s3_other_20func.pcap"
    assert len(_ENT) == 47, "语料回放桩应有 47 个（18 个产品 / 44 个登记模式 + 未暴露模式的旁证）"
    for fname, ent in _ENT.items():
        blob = _payload(fname)
        assert len(blob) == ent["resp_len"], "%s 长度与 manifest 不符" % fname
        assert hashlib.sha256(blob).hexdigest() == ent["sha256"], "%s 字节被改过？" % fname
        assert ent["msgid"] > 0 and ent["ctl"] > 0
        # 真包必须能被库自己的解析链吃下（PASS），否则回放测的是假东西
        verdict = sess_mod.parse(blob)
        assert verdict[0] == "PASS", "%s 不是库能解析的 ResultSets 回包" % fname
        assert verdict[1] == ent["n_rows"]


def test_every_registered_mode_has_a_fixture():
    """登记表的**每个模式**都必须有真实回包可回放（没有就不许登记）。"""
    missing = []
    for product, spec in M.AVAILABLE.items():
        for key in spec.modes:
            fname = "%s__%s.bin" % (product, key)
            if fname not in _ENT:
                missing.append(fname)
    assert not missing, "这些模式没有语料回放桩：%s" % missing


# ══════════════════════════════════════════════════════════════════════════
# 三、登记表的结构不变量
# ══════════════════════════════════════════════════════════════════════════
def _macro_products_in_catalog() -> set:
    import tdxdata.client as cm
    payload = json.load(open(cm._DATA, encoding="utf-8"))
    return {k for k, v in payload["products"].items() if v.get("domain") == "宏观"}


def test_registry_partitions_the_whole_macro_domain():
    """29 个宏观产品必须被**切开且不重叠**：18 个 typed + 11 个 unavailable。"""
    catalog = _macro_products_in_catalog()
    assert len(catalog) == 29, "products.json 里宏观产品数变了（%d）" % len(catalog)
    typed = set(M.AVAILABLE)
    other = set(M.UNAVAILABLE)
    assert not (typed & other), "同一产品既 typed 又 unavailable：%s" % (typed & other)
    assert typed | other == catalog, "漏登记/多登记：%s" % ((catalog ^ (typed | other)),)
    # 主题方法必须覆盖全部 typed 产品（否则某些产品没有 typed 入口）
    assert set(_theme_products()) == typed


def _theme_products() -> set:
    return {p for p, _m in M._THEMES.values()}


def test_every_layout_column_is_either_named_or_explained():
    """`layout` 里**每一列**要么给标准名，要么登记「为什么没名字」。不许有第三态。"""
    for product, spec in M.AVAILABLE.items():
        for mode in spec.modes.values():
            layout = list(mode.layout)
            assert len(layout) == len(set(layout)), "%s/%s layout 有重复列名" % (product, mode.key)
            covered = set(mode.fields) | set(mode.unmapped)
            assert set(layout) == covered, (
                "%s/%s 的 layout 与 fields∪unmapped 不一致：多 %s / 少 %s"
                % (product, mode.key, sorted(set(layout) - covered),
                   sorted(covered - set(layout))))
            names = [v for v in mode.fields.values() if v]
            assert len(names) == len(set(names)), "%s/%s 有重复标准字段名" % (product, mode.key)
            assert mode.evidence, "%s/%s 没有证据/出处" % (product, mode.key)


def test_params_arity_matches_the_catalog():
    """模式参数位数必须与 `products.json` 里该产品的默认参数位数一致（少一位就是发错帧）。"""
    import tdxdata.client as cm
    payload = json.load(open(cm._DATA, encoding="utf-8"))["products"]
    for product, spec in M.AVAILABLE.items():
        n = len(payload[product]["params"])
        for mode in spec.modes.values():
            assert len(mode.params) == n, (
                "%s/%s 参数 %d 位，而 products.json 是 %d 位"
                % (product, mode.key, len(mode.params), n))


def test_slots_are_in_range():
    for product, spec in M.AVAILABLE.items():
        for mode in spec.modes.values():
            for slot, what in ((mode.count_slot, "count_slot"), (mode.day_slot, "day_slot")):
                if slot is None:
                    continue
                assert 0 <= slot < len(mode.params), "%s/%s %s 越界" % (product, mode.key, what)


# ══════════════════════════════════════════════════════════════════════════
# 四、语料回放：每个模式的形状与取值
# ══════════════════════════════════════════════════════════════════════════
def test_all_registered_modes_replay_without_guard_trip(offline):
    """44 个模式逐个回放：不许触发守卫，`cols` 必须逐位等于登记顺序，行数对得上语料。"""
    bad = []
    for product, spec in sorted(M.AVAILABLE.items()):
        for key, mode in sorted(spec.modes.items()):
            kw = {"day": "2026-10-20"} if mode.day_slot is not None else {}
            t = offline.macro.fetch(product, key, **kw)
            prov = t.provenance
            extra = prov.extra if prov else {}
            if extra.get("macro_guard"):
                bad.append((product, key, extra.get("macro_guard"),
                            extra.get("macro_expected_cols"), extra.get("macro_actual_cols")))
                continue
            expect = [f for f in (mode.fields.get(c) for c in mode.layout) if f]
            assert t.cols == expect, "%s/%s 标准列顺序不对" % (product, key)
            ent = _ENT["%s__%s.bin" % (product, key)]
            assert len(t.rows) == ent["n_rows"], \
                "%s/%s 行数 %d ≠ 语料 %d" % (product, key, len(t.rows), ent["n_rows"])
            assert extra.get("macro_mode") == key
            assert extra.get("macro_theme") == spec.theme
            assert extra.get("macro_evidence")
    assert not bad, "这些模式触发了守卫（列清单与语料不符）：%s" % bad


def test_raw_sidecar_keeps_every_original_column(offline):
    """`raw` 是「原始列一个不丢」的保险：每行 `raw` 必须覆盖整个 layout。"""
    for product, spec in sorted(M.AVAILABLE.items()):
        for key, mode in sorted(spec.modes.items()):
            kw = {"day": "2026-10-20"} if mode.day_slot is not None else {}
            t = offline.macro.fetch(product, key, **kw)
            for r in t.rows:
                assert set(r["raw"]) == set(mode.layout), \
                    "%s/%s 的 raw 列不全" % (product, key)
                assert len(r) == len(t.cols) + 1        # 标准字段 + raw


def test_code_like_fields_stay_strings():
    """带前导零的码必须保持字符串（`"01"` 不能变 1.0），否则分组身份就没了。"""
    czsz = _rows("czsz", "monthly")
    assert czsz[0]["national_group_code"] == "01"
    assert czsz[0]["central_group_code"] == "02"
    assert czsz[0]["local_group_code"] == "03"
    yhhb = _rows("yhhb", "by_group")
    assert yhhb[0]["group_code"] == "01"
    retail = _rows("dyjjyxf", "code_163001")
    assert retail[0]["indicator_code"] == "163001"
    tz = _rows("hg_tz", "total")
    assert tz[0]["total_item_code"] == "1"
    assert tz[0]["item111_code"] == "111"


def test_int_fields_are_ints():
    cal = _rows("world_cal", "trading_days")
    assert isinstance(cal[0]["is_trading_day"], int)
    assert isinstance(cal[0]["status_flag"], int)
    ev = _rows("world_cal", "events")
    assert isinstance(ev[0]["importance"], int)


# ══════════════════════════════════════════════════════════════════════════
# 五、算术判据：用 typed 字段重算 M2 里已证实的算式（列错位就会红）
# ══════════════════════════════════════════════════════════════════════════
def _close(a, b, tol=1e-6):
    assert a is not None and b is not None, "断言里出现 None：%r vs %r" % (a, b)
    scale = max(abs(a), abs(b), 1.0)
    assert abs(a - b) <= tol * scale, "%r ≠ %r（差 %r）" % (a, b, a - b)


def test_czsz_mom_change_equals_delta_of_previous_row():
    """M2 §2.1：N003 = N002(t) − N002(t−1) 17/17（文档曾写「同比增减额」→ 已否证）。"""
    rows = _rows("czsz", "monthly")
    hits = 0
    for i in range(len(rows) - 1):
        got = rows[i]["national_mom_chg_yuan"]
        if got is None:                       # 语料里确有 null 行（服务端缺当期），跳过不是放宽
            continue
        want = rows[i]["national_value_yuan"] - rows[i + 1]["national_value_yuan"]
        _close(got, want)
        # 中央/地方两列同构
        _close(rows[i]["central_mom_chg_yuan"],
               rows[i]["central_value_yuan"] - rows[i + 1]["central_value_yuan"])
        _close(rows[i]["local_mom_chg_yuan"],
               rows[i]["local_value_yuan"] - rows[i + 1]["local_value_yuan"])
        hits += 1
    assert hits == 17, "可验行数变了：%d（M2 记的是 17/17）" % hits


def test_czsz_national_equals_central_plus_local():
    """M2 §2.1：N002 = N006 + N010（20/20）。"""
    for r in _rows("czsz", "monthly"):
        _close(r["national_value_yuan"], r["central_value_yuan"] + r["local_value_yuan"])


def test_gdp_yoy_change_equals_delta():
    """M2 §2.2：N004 = N003(t) − N003(t−1) 19/19。"""
    rows = _rows("hg_gdp", "total")
    for i in range(len(rows) - 1):
        _close(rows[i]["yoy_chg_yuan"], rows[i]["gdp_yuan"] - rows[i + 1]["gdp_yuan"])


def test_gdp_industries_sum_to_total_and_shares_to_100():
    """M2 §2.2：N002+N006+N010 = 001 的 N003；三组占比之和 = 100.0000。

    容差不是「放宽」：003 的三次产业是**保留 6 位小数的万亿**值，反算回来与总量的
    整数元口径差 1e7（相对 7e-8）—— 这是服务端给数的精度，不是映射错。
    """
    ind = _rows("hg_gdp", "industries3")[0]
    total = _rows("hg_gdp", "total")[0]["gdp_yuan"]
    _close(ind["primary_value_yuan"] + ind["secondary_value_yuan"] + ind["tertiary_value_yuan"],
           total, tol=1e-7)
    _close(ind["primary_share_pct"] + ind["secondary_share_pct"] + ind["tertiary_share_pct"],
           100.0, tol=1e-3)
    # 单位不统一：增加值是元、增减额是亿元（差 1e8）—— 这条把「别同尺度缩放」钉住
    for key in ("primary", "secondary", "tertiary"):
        chg_yuan = ind[key + "_yoy_chg_yi_yuan"] * 1e8
        assert chg_yuan < ind[key + "_value_yuan"], "增减额乘 1e8 后不该大于增加值"


def test_gdp_by_industry_sums_to_gdp():
    """M2 §2.2/003：Σ N003（亿元）= GDP（精确）。"""
    rows = _rows("hg_gdp", "by_industry")
    total_yi = sum(r["industry_value_yi_yuan"] for r in rows)
    gdp_yuan = _rows("hg_gdp", "total")[0]["gdp_yuan"]
    _close(total_yi * 1e8, gdp_yuan, tol=1e-6)
    assert len(rows) == 11


def test_calendar_is_not_all_trading_days():
    """M2 §2.3：`hg_cal` 366 行**不是**全交易日 —— 分布 {1:244, 0:122}。"""
    dist = collections.Counter(r["is_trading_day"] for r in _rows("world_cal", "trading_days"))
    assert dict(dist) == {1: 244, 0: 122}, "交易日分布变了：%r" % dict(dist)
    # N004 状态位 366/366 恒 1
    assert {r["status_flag"] for r in _rows("world_cal", "trading_days")} == {1}


def test_jqzs_mom_diff_is_point_difference():
    """M2 §2.5：N005 = 值(t) − 值(t−1) 19/19；且 N004 的分母是 t−12 月。"""
    rows = _rows("jqzs", "index")
    hits = 0
    for i in range(len(rows) - 1):
        _close(rows[i]["mom_diff_point"], rows[i]["index_value"] - rows[i + 1]["index_value"],
               tol=1e-6)
        hits += 1
    assert hits == 19


def test_yhhb_breakdown_items_sum_to_total():
    """M2 §2.7/005：4 个分项相加 = 合计（精确）。"""
    r = _rows("yhhb", "breakdown")[0]
    _close(r["item1_value_yuan"] + r["item2_value_yuan"]
           + r["item3_value_yuan"] + r["item4_value_yuan"],
           r["total_value_yuan"], tol=1e-9)


def test_tz_breakdown_is_complete_decomposition():
    """M2 §2.8/001：111+112+113 = 合计（精确）。"""
    r = _rows("hg_tz", "total")[0]
    _close(r["item111_value_yuan"] + r["item112_value_yuan"] + r["item113_value_yuan"],
           r["total_value_yuan"], tol=1e-9)


def test_mlc_breakdown_shares_sum_to_100():
    """M2 §2.14/004：N007..N010 = 各分项/合计×100（合计≈100%）。"""
    r = _rows("mghg_mlc", "breakdown")[0]
    _close(r["item1_value"] + r["item2_value"] + r["item3_value"] + r["item4_value"],
           r["total_value"], tol=1e-9)
    _close(r["item1_share_pct"] + r["item2_share_pct"]
           + r["item3_share_pct"] + r["item4_share_pct"], 100.0, tol=1e-3)


def test_yhhb_by_group_swaps_yoy_and_mom_in_col_des():
    """M2 §2.7/004：物理第 5 列是**同比**（N006 命名）、第 6 列是**环比**（N005 命名）。

    这条测试专门防「按位置映射」：位置 5/6 若互换，同比列会拿到量级相近的环比值，
    类型检查与肉眼都看不出来 —— 只有「哪个更接近 12 期前的变化」这种口径断言能抓。
    """
    rows = _rows("yhhb", "by_group")
    assert "yoy_pct" in rows[0] and "mom_pct" in rows[0]
    # 环比% 应满足 N004/(值−N004)×100（20/20）
    for r in rows:
        want = r["mom_chg_yuan"] / (r["value_yuan"] - r["mom_chg_yuan"]) * 100.0
        _close(r["mom_pct"], want, tol=1e-6)
    # 同比% 与环比% 不该是同一列（同一个数被映射两次的典型病征）
    assert any(abs(r["yoy_pct"] - r["mom_pct"]) > 1e-9 for r in rows)


def test_retail_two_modes_have_different_column_semantics():
    """`dyjjyxf` 002/004：同名 N002 一个是科目名、一个是指标码；N004 一个是增减额、一个是同比%。

    这是「同一产品同名列换语义」的活教材（M2 §4.2 R5）——按位置映射必错。
    """
    total = _rows("dyjjyxf", "total")[0]
    code = _rows("dyjjyxf", "code_163001")[0]
    assert isinstance(total["subject_name"], str) and total["subject_name"] == "合计"
    assert code["indicator_code"] == "163001"
    # 004 模式下 N004 是同比%，002 模式下 N004 是环比增减额（量级差 10 个数量级）
    assert abs(code["yoy_pct"]) < 100
    assert abs(total["mom_chg_yuan"]) > 1e10


def test_holiday_remark_is_not_a_placeholder():
    """M2 §2.4：N006 有 20 行非空（不是占位列）。"""
    rows = _rows("holiday", "all")
    assert len(rows) == 189
    non_null = [r for r in rows if r["remark"]]
    assert len(non_null) == 20, "remark 非空行数变了：%d" % len(non_null)


def test_world_cal_events_rows_are_events_not_indicators():
    """M2 §2.3/模式B：同一列既装指标名也装整句事件（如实命名 `item_name`）。"""
    rows = _rows("world_cal", "events")
    assert len(rows) == 65
    assert all(r["date"] for r in rows)
    assert any(len(str(r["item_name"] or "")) > 20 for r in rows), \
        "语料里应有整句事件（如「2026金融街论坛年会举办。」）"


# ══════════════════════════════════════════════════════════════════════════
# 六、守卫与「不猜」：负向判据
# ══════════════════════════════════════════════════════════════════════════
def test_guard_is_fail_closed_on_shape_mismatch():
    """把 annual 的回包喂给 monthly 模式 → 一列都不映射，原因进 meta（不静默错映射）。"""
    replies = _replies()
    # 用 annual 的字节冒充 monthly 的响应
    replies[("CWServ.ph_datacenter_hg_czsz",
             tuple(str(p) for p in ["001", "", "20"]))] = _payload("czsz__annual.bin")
    c = _client(replies)
    t = c.macro.fetch("czsz", "monthly")
    extra = t.provenance.extra
    assert extra.get("macro_guard") == "cols-mismatch"
    assert extra["macro_expected_cols"] == list(M.AVAILABLE["czsz"].modes["monthly"].layout)
    assert extra["macro_actual_cols"] == ["N001", "N002", "N003", "N004", "N005"]
    # 原样返回：cols 还是服务端列名，没有半套映射
    assert t.cols == ["N001", "N002", "N003", "N004", "N005"]


@pytest.mark.parametrize("product", sorted(M.UNAVAILABLE))
def test_unavailable_products_refuse_to_guess(product):
    c = _client()
    with pytest.raises(errors.NotSupportedError) as ei:
        c.macro.fetch(product)
    assert "没有 typed 通道" in str(ei.value)


def test_unknown_mode_lists_what_is_registered():
    c = _client()
    with pytest.raises(errors.NotSupportedError) as ei:
        c.macro.fetch("czsz", "nope")
    msg = str(ei.value)
    assert "已登记" in msg and "monthly" in msg


def test_unknown_product_raises_keyerror():
    c = _client()
    with pytest.raises(KeyError):
        c.macro.fetch("no_such_macro_product")


def test_count_on_fixed_shape_mode_is_rejected():
    """固定形状的模式不接受 `count=` —— 静默忽略参数是本项目明令禁止的缺陷形态（DEF-002）。"""
    c = _client()
    with pytest.raises(errors.ParamError) as ei:
        c.macro.fetch("world_cal", "trading_days", count=10)
    assert "固定形状" in str(ei.value)


def test_count_is_actually_sent_to_the_wire():
    """`count=` 必须**真的**进请求参数（否则就是静默丢弃，DEF-002 的同型）。"""
    replies = _replies()
    # 语料里只有 count=20 的包；同一条命令换个条数，用同一份回包当桩（形状不变）
    replies[("CWServ.ph_datacenter_hg_czsz", ("001", "", "5"))] = _payload("czsz__monthly.bin")
    c = _client(replies)
    t = c.macro.fetch("czsz", "monthly", count=5)
    assert len(t.rows) == 20                      # 桩回的是 20 行，与 count 无关（服务端说了算）
    cmd, ctl, key = c._sess.calls[-1]
    assert key[1][2] == "5", "count 没进请求参数：%r" % (key,)


def test_day_is_required_for_event_calendar():
    c = _client()
    with pytest.raises(errors.ParamError) as ei:
        c.macro.fetch("world_cal", "events")
    assert "day=" in str(ei.value)


def test_day_must_be_iso_date():
    c = _client()
    with pytest.raises(errors.ParamError):
        c.macro.fetch("world_cal", "events", day="2026/10/20")


def test_day_on_mode_without_date_slot_is_rejected():
    c = _client()
    with pytest.raises(errors.ParamError):
        c.macro.fetch("hg_gdp", "total", day="2026-10-20")


# ══════════════════════════════════════════════════════════════════════════
# 七、主题方法 = fetch 的薄封装（名字与参数都要对上）
# ══════════════════════════════════════════════════════════════════════════
def test_theme_methods_map_to_registered_products():
    for name, (product, default_mode) in M._THEMES.items():
        assert product in M.AVAILABLE, "%s 指向未登记产品 %r" % (name, product)
        assert M.AVAILABLE[product].default_mode == default_mode, \
            "%s 的默认模式与登记不一致" % name
        assert default_mode in M.AVAILABLE[product].modes
        assert callable(getattr(M.Macro, name, None)), "Macro 上缺方法 %r" % name


def test_theme_methods_replay(offline):
    """每个主题方法用默认模式跑一遍（等价于 fetch(product, default_mode)）。"""
    for name, (product, default_mode) in sorted(M._THEMES.items()):
        mode = M.AVAILABLE[product].modes[default_mode]
        kw = {"day": "2026-10-20"} if mode.day_slot is not None else {}
        t = getattr(offline.macro, name)(**kw)
        assert t.provenance.extra["macro_mode"] == default_mode


def test_modes_registry_is_json_safe():
    """登记表要能 `json.dumps`（文档/工具直接吃它）。"""
    blob = M.products_covered()
    json.dumps(blob, ensure_ascii=False)          # 不抛就算过
    assert blob["available"]["hg_gdp"]["default_mode"] == "total"
    assert len(blob["available"]) == 18
