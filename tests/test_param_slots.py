# -*- coding: utf-8 -*-
"""空参数槽校验（2026-10-29 修复）—— 把 `-1002` 挡在发请求之前。

背景（全部是抓包实证，不是推测）
--------------------------------
31 份语料里「参数数组含空槽」的请求一共只出现 **2 次**，两次都是 `-1002 请求参数错误`：

```
CWServ.ph_tdxdatacenter_slb   ["zxt", [],  1, 0, 0, 0]
CWServ.ph_tdxdatacenter_aitc  ["zxgt", [], 1, 0, 0, 0, 0]
```

改成 `""` 就通过（同一份抓包里改名后的请求是 PASS）。而**合法的空参数**是
"整个参数数组为空"（`{"Params": []}`），语料里 PASS / EMPTY_RS 都有 —— 不能一起拦。

两条边界
--------
1. **够窄**：只在"产品台账里这一位是标量、用户塞了空容器"时拦；台账里没有的命令不猜。
2. **够早**：必须在 `body_of()` / 建连 / 限流闸门**之前**抛，否则白等一轮 2.2 s。
"""
from __future__ import annotations

import pytest

import tdxdata
from tdxdata import errors
from tdxdata.client import _check_empty_slots

_CLS = getattr(tdxdata, "Client")

SLB = ["zxt", [], 1, 0, 0, 0]
SLB_OK = ["zxt", "", 1, 0, 0, 0]
AITC = ["zxgt", [], 1, 0, 0, 0, 0]


# ══════════════════════════════════════════════════════════════════════════
# 一、该拦的拦住，且消息带证据
# ══════════════════════════════════════════════════════════════════════════
def test_empty_list_slot_is_rejected_with_actionable_message():
    with pytest.raises(errors.ParamError) as ei:
        _check_empty_slots("CWServ.ph_tdxdatacenter_slb", SLB)
    msg = str(ei.value)
    assert "第 2 个参数" in msg, "要指出是哪一位，否则用户只能一位一位试"
    assert "[]" in msg and "-1002" in msg
    assert '空字符串 ""' in msg, "必须直接给出改法"
    assert "2 次" in msg, "证据要写进消息里（抓包里只出现过 2 次，都是 -1002）"


def test_aitc_same_shape_also_rejected():
    with pytest.raises(errors.ParamError) as ei:
        _check_empty_slots("CWServ.ph_tdxdatacenter_aitc", AITC)
    assert "第 2 个参数" in str(ei.value)


def test_empty_dict_slot_is_rejected_too():
    with pytest.raises(errors.ParamError):
        _check_empty_slots("CWServ.ph_tdxdatacenter_slb", ["zxt", {}, 1, 0, 0, 0])


def test_json_string_params_are_checked():
    """`params` 允许传 JSON 字符串（`call()` 文档这么写），字符串形态也要拦。"""
    with pytest.raises(errors.ParamError):
        _check_empty_slots("CWServ.ph_tdxdatacenter_slb", '["zxt", [], 1, 0, 0, 0]')


# ══════════════════════════════════════════════════════════════════════════
# 二、不该误杀的放过
# ══════════════════════════════════════════════════════════════════════════
def test_empty_string_slot_passes():
    assert _check_empty_slots("CWServ.ph_tdxdatacenter_slb", SLB_OK) is None


def test_whole_params_array_may_be_empty():
    """`{"Params": []}` 是**合法**的空参数（语料里 PASS/EMPTY_RS 都有），不能拦。"""
    assert _check_empty_slots("CWServ.ph_tdxdatacenter_slb", {"Params": []}) is None
    assert _check_empty_slots("CWServ.ph_tdxdatacenter_slb", []) is None


def test_unknown_command_is_not_guessed():
    """台账里没这条命令 → 没有模板可比 → 不拦（宁可放过，不可误杀）。"""
    assert _check_empty_slots("HQServ.PBNOSUCHCMD", ["x", []]) is None


def test_slot_beyond_template_is_ignored():
    """参数的位比模板多时，多出来的位不判（模板说不清就不猜）。"""
    assert _check_empty_slots("CWServ.ph_tdxdatacenter_slb", SLB_OK + [[], []]) is None


def test_non_json_string_is_left_to_server():
    assert _check_empty_slots("CWServ.ph_tdxdatacenter_slb", "not-json") is None


def test_none_params_pass():
    assert _check_empty_slots("CWServ.ph_tdxdatacenter_slb", None) is None


# ══════════════════════════════════════════════════════════════════════════
# 三、时序：必须在建连 / 闸门之前
# ══════════════════════════════════════════════════════════════════════════
def test_call_rejects_before_touching_session():
    """裸 Client（连 `_session_mod` 都没装）：若校验没排在前面，会先炸 AttributeError。"""
    c = _CLS.__new__(_CLS)
    with pytest.raises(errors.ParamError):
        c.call("CWServ.ph_tdxdatacenter_slb", 1, SLB)


def test_call_lets_good_params_through_to_transport():
    """合法参数要能走到传输层（这里让它必失败），证明拦截点没有过度前置。"""
    c = _CLS.__new__(_CLS)
    with pytest.raises(AttributeError):
        c.call("CWServ.ph_tdxdatacenter_slb", 1, SLB_OK)
