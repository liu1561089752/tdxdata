# -*- coding: utf-8 -*-
"""T6 发现机制：**请求体形态的单一真相** —— `session.body_of` ↔ `ctlmap` 交叉护栏。

这份测试为什么存在
==================
`session.body_of()` 原本靠**命令前缀硬编码**决定请求体形态（`HQServ.*` /
`CWSearch.*` / `DataAggregation.*` 裸发，其余包 `{"Params":…}`）；而
`catalog/form_measured.json` 里另有一份**双源实测**形态（`ctlmap.form_measured_for()`）。
两套规则并存、不一致时**没有任何机制会发现** —— 命令照发、服务端也不报错，
只回空包（`ctl_map.json` 的 `ctl` 错、体形态错都是这种"静默丢包"）。

本文件把这个盲区钉死：逐条比对 `body_of` 的实际输出与「实测优先、前缀兜底」的
期望结构，**732 条一条都不跳过**。以后谁改了 `body_of` 的前缀规则、或往
`form_measured.json` 里塞了没证据的条目，这里立刻红。

证据来源与日期
--------------
**2026-10-31**，抓包 `pcap_req_args.json`（`args_hex` 解码）+ `raw_args.json`
∩ `api_catalog_usable.json` 的 `body_form` 字段。判定规则（Lead 拍板）：

* **双源一致**（抓包形态 == `body_form`）才算实测 → 恰好 **19 条**，进
  `form_measured.json`，`body_of` 按它编；
* **单源冲突**（抓包有形态、但 `body_form` 与之矛盾）→ **不覆盖**（只有一份证据，
  改行为等于赌），仍走前缀规则 → 恰好 **12 条**，本文件冻结其"未被覆盖"状态；
* 抓包拍到**两种形态**的命令（服务端两形态都收）不作判据；
* 只拍到 protobuf 体的不作判据 —— 见下。

`HQServ.PB*` 一族不在覆盖范围
-----------------------------
`HQServ.PB*`（以及 `HQServ:PB*`、`FastHQ.Subscribe`）要的是 **protobuf 二进制体**，
走 `pb_api` 那条通路；`ctlmap` / `body_of` 本就不覆盖它们（见 `ctlmap` 模块
docstring 第 26 行"注意：`HQServ.PB*` 一族不在本表覆盖范围内"）。
因此它们**不出现在任何白名单里**（两个冻结名单都只是普通 JSON 体命令）。

纯离线
------
本文件只 `import` 两个模块、只做内存比较：**不建连**（无 socket / 无网络）、
只读 catalog JSON。这也是 gate 的硬要求。
"""
from __future__ import annotations

import json

import pytest

from tdxdata._engine import ctlmap
from tdxdata._engine import session as S

# ── 冻结名单 1：19 条双源实测 ──────────────────────────────────────────────
#: `bare` —— 裸 JSON（不包 `Params`）。3 条。
MEASURED_BARE = frozenset({
    "AppUpdateInfo.queryAppUpdateInfo",
    "CWServ.tdx_vote",
    "HQServ.hq_nlp_misc",
})

#: `base` —— `{"Params": obj}`。16 条，全是 `CWServ.*`。
MEASURED_BASE = frozenset({
    "CWServ.ph_datacenter_hg_jybz",
    "CWServ.ph_datacenter_mghg_hbzc",
    "CWServ.ph_tdxdatacenter_bgcz_gsbg",
    "CWServ.ph_tdxdatacenter_gqzy",
    "CWServ.ph_tdxdatacenter_gszl_ryxl",
    "CWServ.ph_tdxdatacenter_qhlhb_sy",
    "CWServ.ph_tdxdatacenter_tdxrb_rsb_stock",
    "CWServ.ph_tdxdatacenter_tfp_v2_agxq",
    "CWServ.ph_tdxdatacenter_zpfxb_qsgfx",
    "CWServ.ph_tdxdatacenter_zttz_xqy_v2_qsid",
    "CWServ.ph_tdxdatacenter_zttz_zy",
    "CWServ.ph_zt_lhb_sy_lhqc",
    "CWServ.ph_zt_lrzt_lrbd",
    "CWServ.ph_zt_tlsq_lryd",
    "CWServ.ph_zt_tlsq_sy",
    "CWServ.ph_zt_tlsq_tplq",
})

#: 命令 → 期望形态。**19 条之外一律不许有值**（第 1 条用例会断言这一点）。
MEASURED = {c: "bare" for c in MEASURED_BARE}
MEASURED.update({c: "base" for c in MEASURED_BASE})

# ── 冻结名单 2：12 条单源冲突（抓包与 body_form 不一致 → 不覆盖）──────────
#: 判定规则：抓包（`pcap_req_args.json` 的 `args_hex` 解码 + `raw_args.json`）
#: **只有一种可判 JSON 形态**，且与 `api_catalog_usable.json[cmd]["body_form"]`
#: **矛盾**。这类命令拿不到第二份独立证据，**改行为等于赌**，所以
#: `form_measured.json` 里刻意不收录它们，`body_of` 继续按前缀规则走。
#:
#: 为什么是 12 条而不是当初派单写的 11 条：初版清单漏了
#: `CWSearch.tdx_pv`（capture=`bare` vs body_form=`base`），Lead 已确认更正。
#: 注意它和 `CWSearch.tzx_rcache` **都不在** 19 条实测集里 —— 所以
#: `tests/test_da_body.py::test_CWSearch_也是裸body`（用 tzx_rcache 断言裸发）
#: 仍然由**前缀规则**满足，必须是绿的。
SINGLE_SOURCE_CONFLICT = frozenset({
    "CWSearch.tdx_pv",                          # capture=bare  body_form=base
    "CWSearch.tzx_rcache",                      # capture=bare  body_form=base
    "CWServ.ph_agf10_gbgd",                     # capture=call  body_form=base
    "CWServ.ph_agf10_zxts_yjpl",                # capture=call  body_form=base
    "CWServ.ph_tdxdatacenter_cgfx_jgzc_hy_xxq",  # capture=base  body_form=call
    "CWServ.ph_tdxdatacenter_hyfx_sy",          # capture=call  body_form=base
    "CWServ.ph_tdxdatacenter_jgpj_sy",          # capture=base  body_form=call
    "CWServ.ph_tdxdatacenter_tfp_v2_tfptj",     # capture=base  body_form=call
    "CWServ.ph_zt_tlsq_yjbl",                   # capture=base  body_form=call
    "CWServ.ph_zt_xg_ipozcz",                   # capture=call  body_form=base
    "DataAggregation.queryMarketSentiment",     # capture=bare  body_form=base
    "HQServ.hq_nlp_indexselect",                # capture=bare  body_form=base
})

#: 覆盖率下限：`ctl_map.json` 共 732 条。低于它说明文件被读空/读残，
#: 否则"遍历零条 → 全部通过"会假绿（空满足是这类测试最危险的失效模式）。
MIN_COMMANDS = 732

#: 两种 sample：`list` 与 `dict` 各跑一遍 —— 防止实现只对 list 正确
#: （历史上 `obj` 形态就是"list 要按位置映射成 p1/p2，dict 原样"的分叉）。
SAMPLES = [["S1"], {"k": "v"}]


def _prefix_form(cmd: str) -> str:
    """**无实测时**的前缀规则（`session.body_of` 第 3 步的等价复述）。

    这里刻意独立写一遍、而不是调 `body_of` 内部的函数：如果直接复用实现，
    前缀规则被改坏时本测试会跟着一起"改对"，护栏就失去意义。
    """
    if cmd.startswith("HQServ"):
        return "bare"
    if cmd.startswith("CWSearch."):
        return "bare"
    if cmd.startswith("DataAggregation."):
        return "bare"
    return "base"


def _expected(cmd: str, sample):
    """按「实测优先、前缀兜底」算出**期望的已解析结构**。"""
    form = MEASURED.get(cmd) or _prefix_form(cmd)
    if form == "bare":
        return sample
    if form == "call":
        short = cmd.split(".", 1)[1] if "." in cmd else cmd
        return {"CallName": short, "Params": sample}
    if form == "obj":
        if isinstance(sample, list):
            sample = {"p%d" % (i + 1): v for i, v in enumerate(sample)}
        return {"Params": sample}
    return {"Params": sample}          # base


# ── 1. 冻结实测集 ─────────────────────────────────────────────────────────
def test_measured_form_set_is_frozen_at_19():
    """为什么这么断言：实测集是**证据**，不是配置。

    允许它悄悄长大/缩小就等于允许别人把"没双源支撑的猜测"塞进 `body_of` 的
    决策路径 —— 那正是 T6 要消灭的东西。所以命中的命令集合必须**恰好等于**
    下面这 19 条字面量（证据来源：2026-10-31 抓包 `pcap_req_args.json` /
    `raw_args.json` ∩ `api_catalog_usable.json` 的 `body_form`，两源一致才收）；
    多一条、少一条、或某条形态从 bare 变 base，都必须红。
    """
    hits = {c for c in ctlmap.names() if ctlmap.form_measured_for(c) is not None}
    assert hits == set(MEASURED), (
        "双源实测集与冻结名单不一致：多出 %s，缺少 %s"
        % (sorted(hits - set(MEASURED)), sorted(set(MEASURED) - hits))
    )
    # 逐条形态也要钉死（只对集合相等不够 —— 形态被换掉同样危险）
    for cmd, form in MEASURED.items():
        assert ctlmap.form_measured_for(cmd) == form, (
            "%s 的实测形态应为 %r，实际 %r"
            % (cmd, form, ctlmap.form_measured_for(cmd))
        )
    assert len(hits) == 19


# ── 2. 逐条形态断言（732 条 × 两种 sample）───────────────────────────────
@pytest.mark.parametrize("sample", SAMPLES, ids=["list", "dict"])
def test_body_of_matches_measured_then_prefix(sample):
    """为什么这么断言：这是 T6 的**主护栏**。

    对 `ctl_map.json` 全部命令逐条验证「实测形态（若在 19 条里）否则前缀规则」
    与 `body_of` 的实际输出一致。**一条都不许跳过**（无 `params` 模板的命令
    也照样用固定 sample 跑）—— 跳过就是给"以后新加的命令悄悄违反规则"留后门。
    list / dict 两种 sample 各跑一遍，是因为 `obj` 形态对两者语义不同，
    只测一种会漏掉半个实现。
    """
    names = ctlmap.names()
    assert len(names) >= MIN_COMMANDS, (
        "命令表只读到 %d 条（< %d），疑似文件读空 —— 空遍历会让本用例假绿"
        % (len(names), MIN_COMMANDS)
    )

    checked = 0
    for cmd in names:
        want = _expected(cmd, sample)
        actual = S.body_of(cmd, sample)
        try:
            got = json.loads(actual)
        except ValueError:                      # pragma: no cover - 明确失败原因
            pytest.fail("%s 的 body_of 输出不是合法 JSON：%r" % (cmd, actual))
        assert got == want, (
            "%s（sample=%s）体形态不符：期望 %r，实际 %r"
            % (cmd, type(sample).__name__, want, got)
        )
        checked += 1

    assert checked == len(names), "遍历数与命令表条数不一致（有命令被跳过）"


# ── 3. 点名这 2 条行为差异 ────────────────────────────────────────────────
def test_two_commands_flipped_to_bare():
    """为什么这么断言：这 2 条是本次改造**唯一的**行为变化，必须点名钉死。

    `AppUpdateInfo.queryAppUpdateInfo` 与 `CWServ.tdx_vote` 在前缀规则下会被
    包成 `{"Params":…}`，而双源实测是**裸 JSON**；其余 730 条逐字节不变。
    如果哪天有人把前缀规则或实测表改回去，这两条会最先露馅 —— 所以除了
    结构相等，还显式断言输出里**不含 `Params` 键**。
    """
    for cmd, sample in (
        ("AppUpdateInfo.queryAppUpdateInfo", {"appName": "x"}),
        ("CWServ.tdx_vote", {"action": "vote_get"}),
    ):
        raw = S.body_of(cmd, sample)
        assert json.loads(raw) == sample, "%s 应裸发参数本身，实际 %r" % (cmd, raw)
        assert b"Params" not in raw, "%s 不该被包 `Params` 信封：%r" % (cmd, raw)
        # 前缀规则下这 2 条本来会得到 `{"Params":…}` —— 与实测矛盾，故须纠正
        assert _prefix_form(cmd) == "base", "%s 不属于前缀裸发族，才会被纠正" % cmd


# ── 4. 冻结"不作覆盖"的单源冲突 ───────────────────────────────────────────
def test_single_source_conflicts_are_not_overridden():
    """为什么这么断言：**只有一份证据时不许改行为**。

    这 12 条抓包形态与 `api_catalog_usable.json` 的 `body_form` 自相矛盾，
    拿不到第二份独立证据。既然无法判定谁对，覆盖它们就是拿线上可用性去赌
    （错了不报错、只回空包）。所以 `form_measured_for` 必须对它们返回 `None`，
    让它们继续走前缀规则。若有人"顺手"把它们收进实测表，本用例立刻红。
    """
    unknown = [c for c in SINGLE_SOURCE_CONFLICT if c not in set(ctlmap.names())]
    assert not unknown, "白名单里有命令不在 732 条表内：%s" % unknown
    assert len(SINGLE_SOURCE_CONFLICT) == 12

    for cmd in sorted(SINGLE_SOURCE_CONFLICT):
        assert ctlmap.form_measured_for(cmd) is None, (
            "%s 是单源冲突，不该被覆盖成实测形态（当前 %r）"
            % (cmd, ctlmap.form_measured_for(cmd))
        )
        # 与 19 条实测集必须互斥：一条命令不可能既"双源一致"又"单源冲突"
        assert cmd not in MEASURED
        # 未覆盖 = 实际输出仍由前缀规则决定
        assert json.loads(S.body_of(cmd, ["S1"])) == _expected(cmd, ["S1"])


# ── 5. 目录规模下限 ───────────────────────────────────────────────────────
def test_catalog_is_not_empty():
    """为什么这么断言：防"空满足"式假绿。

    上面几条用例都是"遍历命令表逐条断言"—— 如果 `ctl_map.json` 因为打包/
    路径问题读空，遍历零条会**全部通过**，护栏形同虚设。这里设 732 的下限，
    任何让表变少的回归都会被它拦住（表变大则是新命令，用例 2 会自动覆盖）。
    """
    assert len(ctlmap.names()) >= MIN_COMMANDS
