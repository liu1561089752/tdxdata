# -*- coding: utf-8 -*-
"""阶段 6：能力边界注册表的**结构**测试 + 「注册表不许吹牛」硬断言。

这个文件要守住的东西
------------------
`capabilities.py` 是一张"我对世界的主张"表。主张错一次的代价，比少写一个功能大得多：
调用方会按它去设计（"README 说有港股 K 线，我就按有港股写"）。所以这里**不看数据、只看形状**：

1. **`available=True` 必须点得出来** —— 它声明的 `interface` 必须在 `Client` 上**真的存在**
   并且是公开方法。这是防"注册表吹牛"的唯一机械手段：只写 `available=True` 不给名字、
   或者给个不存在的名字，这里立刻红。
2. **`available=False` 必须说清是哪种"拿不到"** —— `reason` 要有长度、要有具体依据
   （数字或命令名），`evidence` 不能空。只写"暂不支持"这种空话一律红。
3. **港股三行必须并存** —— 报价（服务端有、本库未实现）/ K 线分时（无通道）/ 清单
   （不可枚举）是三种**完全不同**的结论。混成一句"不支持港股"就红。
4. **期货必须归因到证券表**，不许归因到"市场码被拒"（`field3` 对已知码是宽松的，
   这是反证；归错因会导致有人去试别的市场码，白烧几百次请求）。
5. **静态表不许联网** —— 整模块用钉死 socket 的守卫跑一遍。

⚠️ 本文件按项目纪律**不写类名调用**（一律 `getattr` 取类），也不许出现 `return []`。
"""
from __future__ import annotations

import inspect
import re
import socket

import pytest

import tdxdata
from tdxdata import capabilities as caps
from tdxdata.errors import EmptyResult

_CLS = getattr(tdxdata, "Client")

#: 派单要求**必须覆盖**的（品种 → 能力名里必须出现的关键词）。
#: 少一个就说明"真相关口"漏了某一块，不是排版问题。
COVERED = {
    "a_share": ("日/周/月/季", "分钟", "分时", "逐笔", "涨跌家数",
                "创业板", "科创板", "全 A 股"),
    "bj": ("北交所",),
    "index": ("指数",),
    "board": ("板块清单", "板块成分"),
    "etf": ("ETF",),
    "bond": ("债券",),
    "hk": ("日线", "实时报价", "清单"),
    "futures": ("期货",),
    "option": ("期权",),
    "cbt": ("国债期货",),
    "forex": ("外汇",),
    "all": ("并发", "盘后固定价格", "6 位数字"),
}

#: `reason` 里允许充当"具体依据"的标记（命令名 / 协议字段 / 报错码）。
#: 与"含数字"二选一即可 —— 但两条都不满足就说明写的是空话。
_MARKERS = ("PBFXT", "PBZST", "PBMultiHQ", "PBCombHQ", "PBTickEx", "HQServ",
            "ERR_", "field", "setcode", "symbols.parse", "InOutFlag", "Bar.raw")


def _client():
    """裸到不能更裸的客户端：`clock_probe=False` + 一次都不调取数方法。

    没有 session、没有 PB 连接、没有时钟探针 —— `capabilities()` 是静态表，
    本来就不该需要这些。造不出来的话说明它偷偷联网了。
    """
    return _CLS(clock_probe=False)


# ══════════════════════════════════════════════════════════════════════════
# 一、注册表自身：封闭集 / 覆盖度 / 不许吹牛
# ══════════════════════════════════════════════════════════════════════════
def test_registry_instruments_are_a_closed_set():
    """`instrument` 只能取 `INSTRUMENTS` 里的键，且每个键都得真有条目（没有死键）。"""
    declared = set(caps.INSTRUMENTS)
    used = {c.instrument for c in caps.CAPABILITIES}
    assert used <= declared, "注册表里出现了未登记的品种：%s" % sorted(used - declared)
    assert declared <= used, "INSTRUMENTS 里有品种一条都没登记：%s" % sorted(declared - used)


def test_required_instruments_and_capabilities_are_covered():
    """派单点名的品种 × 能力必须都在（漏一块就红，不是"差不多就行"）。"""
    by_ins: dict = {}
    for cap in caps.CAPABILITIES:
        by_ins.setdefault(cap.instrument, []).append(cap.name)
    missing = []
    for instrument, keywords in COVERED.items():
        names = by_ins.get(instrument, [])
        joined = " ｜ ".join(names)
        for kw in keywords:
            if kw not in joined:
                missing.append("%s / %s" % (instrument, kw))
    assert not missing, "这些品种 × 能力没有登记：\n  " + "\n  ".join(missing)


def test_available_true_must_name_a_real_public_method():
    """**防吹牛的核心断言**：`available=True` 的 `interface` 必须是 `Client` 上真有的公开方法。

    没有这一条，注册表可以随便写"支持港股 K 线"而没人发现 ——
    文档里最贵的错就是这种：读起来什么都能干，跑起来什么都没有。
    """
    public = {n for n in dir(_CLS) if not n.startswith("_")}
    bad = []
    for cap in caps.CAPABILITIES:
        if not cap.available:
            continue
        if not cap.interface:
            bad.append("%s：available=True 却没写 interface" % cap.name)
            continue
        if cap.interface not in public:
            bad.append("%s：interface=%r 不是 Client 的公开方法" % (cap.name, cap.interface))
            continue
        if not callable(getattr(_CLS, cap.interface, None)):
            bad.append("%s：interface=%r 不可调用" % (cap.name, cap.interface))
    assert not bad, "注册表吹牛了：\n  " + "\n  ".join(bad)


def test_unavailable_entries_say_why_with_concrete_evidence():
    """`available=False` 的每一项都要有：够长的 `reason` + 具体依据 + 非空 `evidence`。"""
    bad = []
    for cap in caps.CAPABILITIES:
        if cap.available:
            continue
        if len(cap.reason) <= 15:
            bad.append("%s：reason 太短（%r）" % (cap.name, cap.reason))
            continue
        has_digit = bool(re.search(r"\d", cap.reason))
        has_marker = any(m in cap.reason for m in _MARKERS)
        if not (has_digit or has_marker):
            bad.append("%s：reason 里既没有数字也没有命令名/报错码，属空话" % cap.name)
        if not cap.evidence.strip():
            bad.append("%s：evidence 是空的" % cap.name)
    assert not bad, "这些「拿不到」没说清依据：\n  " + "\n  ".join(bad)


def test_available_entries_also_carry_evidence():
    """有能力的项也要有出处 —— 否则「支持」同样是无法复核的主张。"""
    bad = [c.name for c in caps.CAPABILITIES
           if c.available and not c.evidence.strip()]
    assert not bad, "这些「有」没写出处：%s" % bad


def test_capability_is_frozen_and_slotted():
    """登记项是不可变的：注册表是常量表，改它等于改文档。"""
    cap = caps.CAPABILITIES[0]
    with pytest.raises(Exception):
        cap.name = "改一下"                       # frozen
    assert not hasattr(cap, "__dict__"), "slots=True 之后不该有 __dict__"
    assert isinstance(caps.CAPABILITIES, tuple), "注册表本体必须是 tuple（防就地 append）"


def test_select_returns_a_tuple_so_callers_cannot_mutate_the_registry():
    """`select()` 返回 tuple —— 调用方拿不走可变引用。"""
    got = caps.select()
    assert isinstance(got, tuple)
    assert len(got) == len(caps.CAPABILITIES)
    assert caps.select(instrument="hk") is not caps.CAPABILITIES


# ══════════════════════════════════════════════════════════════════════════
# 二、三处最容易写错的结论（港股三态 / 期货归因 / 空筛选）
# ══════════════════════════════════════════════════════════════════════════
def test_hk_three_conclusions_coexist_and_quote_is_not_written_as_unsupported():
    """港股：报价（**服务端有**、本库未实现）/ K 线分时（无通道）/ 清单（不可枚举）三行必须并存。

    并且**严禁**把报价那条写成"服务端不支持港股" —— 那是本项目明令禁止的错措辞
    （§12.8 第 4 条）。服务端明明回得出腾讯 385.200。
    """
    hk = caps.describe("hk")
    assert len(hk) >= 3, "港股应当有三条并列结论，实际 %d 条" % len(hk)

    quote = [c for c in hk if "报价" in c.name]
    bars = [c for c in hk if "日线" in c.name or "分时" in c.name]
    listing = [c for c in hk if "清单" in c.name]
    assert quote and bars and listing

    for cap in hk:
        assert cap.available is False, "港股目前一条都不该标 available=True"

    q = quote[0]
    assert "服务端有" in q.reason, "报价那条必须写明「服务端有、本库未实现」"
    assert "PBCombHQ" in q.reason and "setcode=31" in q.reason, \
        "报价那条必须指出具体通道（PBCombHQ + setcode=31），否则无法复核"
    assert "不支持" not in q.reason, \
        "**严禁**把「本库未实现」写成「服务端不支持」（§12.8 第 4 条）"


def test_futures_blames_the_security_table_not_the_market_code():
    """期货：必须归因到「不在数据源证券表内」，**不许**归因到「市场码被拒」。

    为什么这条这么重要：`field3` 对已知代码是**宽松**的（把 600519 塞进 field3=99
    照样回同一根 K 线）。归错因的后果是后来人继续去试 7/8/28/29/30/31 这些市场码，
    白烧几百次请求还什么都拿不到。
    """
    rows = caps.describe("futures")
    assert rows, "futures 一条都没登记"
    joined = " ".join(c.reason + " " + c.workaround for c in rows)
    assert "证券表" in joined, "期货必须归因到「不在数据源证券表内」"
    assert "不支持这个市场码" not in joined, "不许写成「不支持这个市场码」（那是错的归因）"
    assert "field3" in joined, "必须带上 field3 宽松这一反证，否则读者没法自己复核"


def test_forex_carries_both_channel_facts():
    """外汇归因必须两层并列：7615 主通道在证券表外 + EX 7727 服务端有、本库未接入。

    为什么单独立一条：这条曾被写成一句「无通道」，把「服务端没有」和「本库没接」
    混在一起（评审 SUMMARY §3.1 的文档归因缺陷）；两种「拿不到」的处置完全不同。
    """
    fx = caps.describe("forex")
    assert fx, "forex 一条都没登记"
    joined = " ".join(c.reason + " " + c.workaround + " " + c.evidence for c in fx)
    assert "7727" in joined, "必须写明 EX 7727 扩展通道（服务端有）"
    assert "证券表" in joined, "7615 侧必须保留「不在数据源证券表内」的归因"
    assert "USDCNY" in joined or "6.6746" in joined, "必须带实测报价锚点，否则无法复核"
    assert "2026-10-22" in joined, "必须带快照日期警示"
    for cap in fx:
        assert cap.available is False, "外汇尚无公开接口，不许标 available=True"


def test_forex_7727_quote_is_labelled_as_note_evidence_not_capture():
    """7727 那四个汇率报价只能标「笔记/在线探针」，**不许**标成「抓包实测」。

    为什么单独立一条：7709/7727 这类扩展端口在 31 份 pcap 里**一帧都没有**（端口直方图
    7709/7727/7708 各 0 帧、`USDCNY` 字符串 0 次命中）。把在线探针拿到的快照写成
    "抓包实测"，就是把「别人某次连上了」包装成「我们能从抓包复现」—— 后者根本做不到，
    复核的人按抓包去查会一无所获。证据性质必须写在文本里，不能靠读者猜。
    """
    fx = caps.describe("forex")
    joined = " ".join(c.reason + " " + c.workaround + " " + c.evidence for c in fx)
    assert "7727" in joined, "必须点名 7727 扩展通道"
    assert ("笔记" in joined) or ("在线探针" in joined), \
        "7727 侧必须标明证据性质是「笔记 / 在线探针」，否则会被读成抓包实测"
    assert "不是抓包实测" in joined, "必须显式否定「抓包实测」这个读法"
    assert "0 帧" in joined, "必须写明抓包侧的阴性证据：7709/7727/7708 各 0 帧"
    assert "抓包快照" not in joined, \
        "不许再把笔记证据写成「抓包快照」（该批 pcap 不含 7727 流量）"
    for token in ("USDCNY", "6.6746", "USDCNH", "CNHHKD", "CNYHKD"):
        assert token in joined, "四个报价锚点必须保留（可复核）：%s" % token
    # `fx` 字样最容易被误读成外汇 —— 必须点名这两个产品是「分析/ETF 盘点」
    assert "sczb" in joined and "jjhqpd" in joined, \
        "必须点名 ph_cfg_fx_sczb / ph_cfg_fx_jjhqpd，免得下次又被 `fx` 字样误导"
    assert "零汇率字段" in joined, "必须写清这两个产品零汇率字段"
    for cap in fx:
        assert cap.available is False, "外汇仍未接入，不许标 available=True"


def test_futures_has_two_layers_quote_gap_and_datacenter_gain():
    """期货必须拆成两层：**合约行情拿不到** + **专题数据拿得到**（数据中心侧实测）。

    为什么单独立一条：以前只写「不在证券表内 + 无通道」，读者会得出"期货什么都没有"。
    抓包证明 7615 数据中心侧有真数据（`qhlhb_sy` 合约清单/会员持仓增减、`tdxrb_qh`
    合约榜、`qh_hqpd_qhhy` 主力连续最新价）。同时必须点明 `qh_cdzl` **名不副实**
    （只回交易日列表）—— 否则又有人拿它当"持仓增量"用。
    """
    rows = caps.describe("futures")
    assert rows, "futures 一条都没登记"
    joined = " ".join(c.reason + " " + c.workaround + " " + c.evidence for c in rows)

    assert "拿不到" in joined and "拿得到" in joined, "必须同时写「拿不到」与「拿得到」两层"
    assert "证券表" in joined, "行情侧仍须保留「不在数据源证券表内」的归因"
    assert "数据中心" in joined, "拿得到那层必须落在「数据中心侧」这条通道上"

    for needle in ("qhlhb_sy", "qhlhb_jcgc", "tdxrb_qh", "qh_hqpd_qhhy"):
        assert needle in joined, "可用的期货专题命令必须点名（否则等于没说）：%s" % needle

    assert "qh_cdzl" in joined, "必须点明 qh_cdzl 名不副实，别让它继续冒充「持仓增量」"
    assert "交易日列表" in joined, "qh_cdzl 的真实形状要写出来（交易日列表）"

    for cap in rows:
        assert cap.available is False, \
            "本库仍未把期货专题数据封装成公开方法，不许标 available=True"


def test_empty_selection_raises_empty_result_not_an_empty_table():
    """空筛选 → `EmptyResult`。返回空表会被读成"这个品种确实没有能力"，那是撒谎。"""
    c = _client()
    try:
        with pytest.raises(EmptyResult):
            c.capabilities(instrument="no_such_instrument")
        with pytest.raises(EmptyResult):
            c.capabilities(instrument="hk", available_only=True)   # 港股三条全不可用
        with pytest.raises(EmptyResult):
            c.capabilities(instrument="  FUTURES  ", available_only=True)
    finally:
        c.close()


def test_describe_rejects_unknown_instrument_and_lists_the_legal_ones():
    """`describe()` 认不出的品种 → `ValueError`，并且把合法取值列出来（不然人只能猜）。"""
    with pytest.raises(ValueError) as ei:
        caps.describe("nasdaq")
    msg = str(ei.value)
    assert "a_share" in msg and "hk" in msg and "forex" in msg


# ══════════════════════════════════════════════════════════════════════════
# 三、`Client.capabilities()` 的返回契约
# ══════════════════════════════════════════════════════════════════════════
def test_capabilities_returns_the_contracted_table():
    """7 列、每行字段齐全、取值类型正确。"""
    c = _client()
    try:
        t = c.capabilities()
        assert list(t.cols) == list(caps.CAPABILITY_COLS)
        assert t.shape == (len(caps.CAPABILITIES), 7)
        for row in t.rows:
            for col in caps.CAPABILITY_COLS:
                assert col in row, "行里缺列 %s" % col
                assert row[col] is not None, "列 %s 不该是 None" % col
            assert isinstance(row["available"], bool), "available 必须是 bool，不能是字符串"
            assert isinstance(row["instrument"], str) and row["instrument"]
            if row["available"]:
                assert row["interface"], "可用项必须有 interface"
            else:
                assert row["reason"] and row["evidence"]
    finally:
        c.close()


def test_capabilities_carries_the_semantic_provenance_fields():
    """阶段 1 的语义字段一个不能少，且 `as_of is None` 必须配说明 note。"""
    c = _client()
    try:
        prov = c.capabilities().provenance
        for f in ("as_of", "status", "fetched_at", "elapsed", "cmd"):
            assert hasattr(prov, f), "capabilities() 的 provenance 缺 %s" % f
        assert prov.as_of is None, "静态注册表不该有数据时刻"
        joined = "；".join(prov.notes)
        assert joined, "as_of 是 None 就必须说明为什么"
        assert "静态" in joined and "没有这个概念" in joined
        assert prov.status in {"final", "provisional", "pending", "delayed"}
        assert prov.status == "provisional", "会随实证更新的声明不许自称定稿"
        assert len(prov.fetched_at.replace(microsecond=0, tzinfo=None)
                  .isoformat(sep=" ")) == 19
        assert prov.cmd == "tdxdata.capabilities"
        assert prov.cached is False
    finally:
        c.close()


def test_capabilities_does_not_record_a_call():
    """**静态表不是一次取数**：它不许覆盖 `last_call`（否则"最近一次取数"会被污染）。

    这是"它真的一个请求都没发"的数据面证据 —— 比"我没看到它连网"强。
    """
    c = _client()
    try:
        assert c.last_call == {}, "新客户端不该有 last_call"
        c.capabilities()
        assert c.last_call == {}, "capabilities() 覆盖了 last_call，说明它走了 _record_call"
    finally:
        c.close()


def test_filters_are_applied_and_results_stay_in_registry_order():
    """`instrument` / `available_only` 两个筛子各自生效，且顺序跟注册表一致。"""
    c = _client()
    try:
        hk = c.capabilities(instrument="HK")            # 大小写不敏感
        assert len(hk.rows) == len(caps.describe("hk"))
        assert {r["instrument"] for r in hk.rows} == {"hk"}

        ok = c.capabilities(available_only=True)
        assert ok.rows, "不可能一个可用的能力都没有"
        assert all(r["available"] for r in ok.rows)
        assert len(ok.rows) < len(caps.CAPABILITIES)

        both = c.capabilities(instrument="a_share", available_only=True)
        expected = [cap for cap in caps.CAPABILITIES
                    if cap.instrument == "a_share" and cap.available]
        assert [r["capability"] for r in both.rows] == [cap.name for cap in expected]
    finally:
        c.close()


def test_markdown_table_has_one_row_per_capability_and_seven_columns():
    """README 那张表由 `as_markdown_table()` 逐字生成 —— 行列数先钉住。"""
    text = caps.as_markdown_table()
    lines = text.split("\n")
    assert lines[0].startswith("| 品种 | 能力 | 接口 | 可用 | 为什么 | 依据 |")
    assert lines[1].startswith("|---|")
    body = lines[2:]
    assert len(body) == len(caps.CAPABILITIES)
    for line in body:
        # 只数**未被转义**的竖线：单元格里的 `|` 由渲染层写成 `\|`，否则整张表会错列
        assert len(re.findall(r"(?<!\\)\|", line)) == 8, \
            "每行必须 7 列（8 个未转义竖线）：%r" % line
        assert line.startswith("| ") and line.endswith(" |")


def test_market_domains_markdown_reads_the_single_source_of_truth():
    """市场域那张表的**事实源是 `_core.MARKET_DOMAINS`**，渲染器只负责排版。"""
    from tdxdata._core import MARKET_DOMAINS

    text = caps.market_domains_markdown()
    lines = text.split("\n")
    assert lines[0] == "| `market` | 语义 |"
    body = lines[2:]
    assert len(body) == len(MARKET_DOMAINS)
    for line, key in zip(body, sorted(MARKET_DOMAINS)):
        assert line == "| `%d` | %s |" % (key, MARKET_DOMAINS[key])


def test_this_module_never_touches_the_network(monkeypatch):
    """把 socket 钉死后再跑一遍 —— 证明上面那些绿不是靠连网换来的。"""

    def _boom(*a, **kw):
        raise AssertionError("能力表是静态注册表，不许建 socket")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket.socket, "connect", _boom)

    c = _client()
    try:
        t = c.capabilities()
        assert t.shape == (len(caps.CAPABILITIES), 7)
        assert c.capabilities(instrument="hk").shape[0] >= 3
    finally:
        c.close()


def test_capabilities_is_a_public_method_with_a_table_return_annotation():
    """返回注解必须是 `Table` —— `test_meta_coverage` 靠它自动推导 5 键断言。

    这条是给"后人嫌注解麻烦把它删掉"准备的：注解一丢，那条自动推导就再也管不到它。
    """
    fn = getattr(_CLS, "capabilities")
    ann = str(inspect.signature(fn).return_annotation)
    assert "Table" in ann, "capabilities() 的返回注解必须写 -> Table（别删）"
    assert not any(p.kind is inspect.Parameter.VAR_POSITIONAL
                   for p in inspect.signature(fn).parameters.values()), \
        "不要用 *args（会把 ARGS=() 那条守卫架空）"
