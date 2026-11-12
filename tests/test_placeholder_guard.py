# -*- coding: utf-8 -*-
"""占位符守卫：`ctlmap.fill()` 填完全表后**不许残留**帧内占位符。

背景（T3）：`catalog/ctl_map.json` 732 条命令的参数模板里共 29 种占位符形态，
历史上 `PLACEHOLDER` 只认 16 种，于是 `fill(code=…, date=…)` 之后仍有 22 条命令
把 `#dateTime#` / `@name@` 之类**原样发到服务器** —— 服务端不报错，只回空包，
是最难查的一类 bug。本文件把这条钉死：宁可测试红，也不许静默发占位符出去。

只查表，**不联网**、不建连。
"""
import copy
import json
import re

from tdxdata._engine import ctlmap

#: 帧内占位符形态：`#xxx#` / `@xxx@`。
_TOKEN = re.compile(r"#[A-Za-z0-9_]+#|@[A-Za-z0-9_]+@")

#: `{{xxx}}` 形态（App 端 JS 模板语法），见 `test_app_js_template_whitelist_is_exact`。
_BRACE = re.compile(r"\{\{[^{}]*\}\}")

#: `{{searchText}}` 的逐条点名白名单 —— **恰好这 2 条，多一条都不许放行**。
#:
#: 理由：`CWServ.app_keyboard_ag` / `CWServ.app_keyboard_hscnjj` 这两个搜索联想
#: 接口，把 App 前端的一段 JS 模板表达式（`{{searchText}}`）当成**参数原文**
#: 发给服务端，由 App 侧的渲染层先替换成用户输入的内容。它**不是帧内占位符**，
#: 库里没有用户输入可填（填空串反而会改变协议语义），所以 `fill()` 原样保留。
#: 白名单必须与集合**完全相等**：多出一条说明出现了新的、未处理的模板语法，
#: 那宁可让这条测试红，也不要默默放过一个新形态。
_APP_JS_TEMPLATE = {"CWServ.app_keyboard_ag", "CWServ.app_keyboard_hscnjj"}


def _filled(cmd):
    """拿命令的参数模板和 `fill()` 后的结果；没有模板返回 `(None, None)`。"""
    tpl = ctlmap.params_for(cmd)
    if tpl is None:
        return None, None
    return tpl, ctlmap.fill(tpl, code="600519", date="2026-10-30")


def _as_text(obj):
    """把填完的参数拍成文本再扫 token（嵌套 dict/list 一并覆盖）。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def test_no_placeholder_residue_across_all_commands():
    """全表逐条 `fill` 后不许出现任何 `#xxx#` / `@xxx@` 残留。"""
    residue = {}
    names = ctlmap.names()
    checked = 0
    for cmd in names:
        tpl, out = _filled(cmd)
        if tpl is None:
            continue
        checked += 1
        toks = sorted(set(_TOKEN.findall(_as_text(out))))
        if toks:
            residue[cmd] = toks

    # 覆盖度：遍历的是**全表**（无参数模板的跳过后，仍等于 stats 的 with_params），
    # 不是抽查 —— 否则"清零"就没有意义了。
    assert len(names) == ctlmap.stats()["total"], len(names)
    assert checked == ctlmap.stats()["with_params"], checked
    assert checked > 500, "只检查了 %d 条，覆盖面不足" % checked
    assert not residue, "fill() 后仍残留 %d 条命令的占位符：%s" % (
        len(residue), json.dumps(residue, ensure_ascii=False, sort_keys=True))


def test_app_js_template_whitelist_is_exact():
    """残留 `{{...}}` 的命令集合**恰好**等于 App 端 JS 模板白名单（2 条）。

    详见 `_APP_JS_TEMPLATE` 的理由：这是 App 前端模板语法，不是帧内占位符。
    """
    hits = set()
    for cmd in ctlmap.names():
        tpl, out = _filled(cmd)
        if tpl is None:
            continue
        if _BRACE.search(_as_text(out)):
            hits.add(cmd)
    assert hits == _APP_JS_TEMPLATE, (
        "残留 {{...}} 的命令集合与白名单不符：多出 %s，缺少 %s" % (
            sorted(hits - _APP_JS_TEMPLATE), sorted(_APP_JS_TEMPLATE - hits)))


def test_fill_does_not_mutate_template():
    """`fill()` 返回新对象，**不得改动**调用方传进来的模板（深比较）。"""
    changed = []
    checked = 0
    for cmd in ctlmap.names():
        tpl = ctlmap.params_for(cmd)
        if tpl is None:
            continue
        checked += 1
        before = copy.deepcopy(tpl)
        ctlmap.fill(tpl, code="600519", date="2026-10-30")
        if tpl != before:
            changed.append(cmd)
    assert checked == ctlmap.stats()["with_params"], checked
    assert not changed, "fill() 改动了原模板：%s" % changed


def test_real_send_path_has_no_placeholder_residue():
    """真实发送路径 = `ctlmap.params_best_for()` + `fill()`；护栏必须盯这条路。

    上面 3 条用例守的是 `ctlmap.params_for()`（**目录模板**，517 条非 None），
    但 `call_cmd()` 实际**发出去**的参数来自 `ctlmap.params_best_for()`
    （**发送路径**，504 条非 None / 228 条 None）—— 两者对那 25 条抓包元数据命令
    取值不同（`params_for` 给的是含 `args_hex` 的元数据，`params_best_for` 换成
    解码后的真参数）。只守前者会漏掉这 25 条在真实发送路径上的表现，所以：
    `params_for` 管「目录模板」、`params_best_for` 管「实际发出去的参数」，
    **两条路径都要守**（前 3 条守前者，本条守后者）。

    判据与前 3 条同口径：`#xxx#`/`@xxx@` 残留必须为 0；`{{...}}` 残留集合仍须
    恰好是那 2 条 App 端 JS 模板命令（多一条都不放行）。
    """
    residue = {}
    braces = set()
    checked = 0
    skipped = 0
    names = ctlmap.names()
    for cmd in names:
        tpl = ctlmap.params_best_for(cmd)
        if tpl is None:
            skipped += 1
            continue
        checked += 1
        out = ctlmap.fill(tpl, code="600519", date="2026-10-30")
        text = _as_text(out)
        toks = sorted(set(_TOKEN.findall(text)))
        if toks:
            residue[cmd] = toks
        if _BRACE.search(text):
            braces.add(cmd)

    # 覆盖度下界：504 条有模板、228 条 None（防"读空 → 空满足"）
    assert len(names) == ctlmap.stats()["total"], len(names)
    assert checked >= 500, "真实发送路径只检查了 %d 条，覆盖面不足" % checked
    assert checked == 511 and skipped == 228, (checked, skipped)
    assert not residue, "真实发送路径 fill() 后仍残留 %d 条命令的占位符：%s" % (
        len(residue), json.dumps(residue, ensure_ascii=False, sort_keys=True))
    assert braces == _APP_JS_TEMPLATE, (
        "真实发送路径残留 {{...}} 的命令集合与白名单不符：多出 %s，缺少 %s" % (
            sorted(braces - _APP_JS_TEMPLATE), sorted(_APP_JS_TEMPLATE - braces)))
