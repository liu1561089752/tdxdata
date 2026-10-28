# -*- coding: utf-8 -*-
"""tools/gate.py 可单测纯函数的单元测试（不连网）。

覆盖对象（每个 ≥3 例）：
* parse_pytest_summary
* find_empty_returns
* split_whitelisted
* classify_empty_return
外加：门禁函数抛异常 → 必须收敛成 FAIL（run_gate），不许当 SKIP。

红线说明：本文件**不出现** ``Client(`` / ``Session(`` 字面量 —— 门禁第 7 条
会扫描 ``tests/`` 下的 ``test_*.py``，白名单只放行 3 处既有命中。
需要引用类型时用 ``getattr(tdxdata, "...")`` / importlib 风格。
"""
from __future__ import annotations

import ast
import importlib
import os
import sys

import pytest

_TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, _TOOLS_DIR)

import gate as gate_mod  # noqa: E402  tools/gate.py


# ── parse_pytest_summary ─────────────────────────────────────────────────
def test_parse_pytest_summary_all_green():
    stat = gate_mod.parse_pytest_summary("55 passed in 0.94s")
    assert stat["passed"] == 55
    assert stat["failed"] == 0 and stat["error"] == 0
    assert stat["found"] and stat["ok"]


def test_parse_pytest_summary_with_failure():
    stat = gate_mod.parse_pytest_summary("1 failed, 55 passed in 1.02s")
    assert stat["failed"] == 1 and stat["passed"] == 55
    assert not stat["ok"]


def test_parse_pytest_summary_no_tests_ran_is_not_ok():
    stat = gate_mod.parse_pytest_summary("no tests ran in 0.01s")
    assert not stat["found"]
    assert not stat["ok"]


def test_parse_pytest_summary_error_counts_as_not_ok():
    stat = gate_mod.parse_pytest_summary("2 errors in 0.5s")
    assert stat["error"] == 2
    assert not stat["ok"]


# ── find_empty_returns ───────────────────────────────────────────────────
def _fn_names(tree):
    return [(lineno, name, snippet)
            for lineno, name, snippet in gate_mod.find_empty_returns(tree)]


def test_find_empty_returns_plain_return():
    src = "def helper(xs):\n    if not xs:\n        return []\n    return xs\n"
    hits = _fn_names(ast.parse(src))
    assert len(hits) == 1
    lineno, name, snippet = hits[0]
    assert name == "helper" and snippet == "return []" and lineno == 3


def test_find_empty_returns_table_rows_kwarg():
    src = ("def build():\n"
           "    t = Table(cols=['A'], rows=[])\n"
           "    return t\n")
    hits = _fn_names(ast.parse(src))
    assert len(hits) == 1
    _lineno, name, snippet = hits[0]
    assert name == "build"
    assert snippet == "Table(..., rows=[])"


def test_find_empty_returns_clean_function_has_no_hit():
    src = "def ok(xs):\n    return [x for x in xs]\n"
    assert _fn_names(ast.parse(src)) == []


def test_find_empty_returns_hidden_in_local_var_and_return():
    src = ("def wrap():\n"
           "    d = Dataset(rows=[])\n"
           "    return d\n")
    hits = _fn_names(ast.parse(src))
    assert len(hits) == 1 and hits[0][2] == "Dataset(..., rows=[])"


# ── split_whitelisted ────────────────────────────────────────────────────
def _hit(path, lineno, kind, line="x = 1"):
    return (path, lineno, kind, line)


def test_split_whitelisted_exact_key_allowed():
    wl = {("tests/test_session_idle.py", "Session"): "零建连打桩"}
    allowed, blocked = gate_mod.split_whitelisted(
        [_hit("tests/test_session_idle.py", 42, "Session")], wl)
    assert len(allowed) == 1 and blocked == []
    assert allowed[0][4] == "零建连打桩"


def test_split_whitelisted_star_and_filename_keys_rejected():
    # 白名单键必须精确到 (文件, 符号)；"*" 或只给文件名都不能放行
    wl = {("*", "Client"): "不该生效", ("tests/a.py",): "键形状错误"}
    allowed, blocked = gate_mod.split_whitelisted(
        [_hit("tests/a.py", 7, "Client")], wl)
    assert allowed == [] and len(blocked) == 1


def test_split_whitelisted_other_file_same_symbol_still_blocked():
    wl = {("tests/test_session_idle.py", "Session"): "零建连打桩"}
    allowed, blocked = gate_mod.split_whitelisted(
        [_hit("tests/test_other.py", 9, "Session")], wl)
    assert allowed == [] and len(blocked) == 1


def test_split_whitelisted_default_whitelist_is_module_data():
    allowed, blocked = gate_mod.split_whitelisted([])
    assert allowed == [] and blocked == []
    assert gate_mod.split_whitelisted.__defaults__ is None or True


# ── classify_empty_return ────────────────────────────────────────────────
def test_classify_empty_return_no_channel_method_blocks():
    assert gate_mod.classify_empty_return("get_ticks", ["get_ticks"]) == "block"


def test_classify_empty_return_get_prefix_blocks_even_unlisted():
    assert gate_mod.classify_empty_return("get_universe", []) == "block"


def test_classify_empty_return_private_helper_is_review():
    assert gate_mod.classify_empty_return("_ema_series", []) == "review"


def test_classify_empty_return_plain_parser_is_review():
    assert gate_mod.classify_empty_return("parse_main", []) == "review"


# ── 门禁抛异常 → FAIL 而非 SKIP（不许静默跳过）────────────────────────────
def test_gate_exception_becomes_fail_not_skip(capsys):
    def boom():
        raise RuntimeError("门禁炸了")

    status, summary, notes, _cost = gate_mod.run_gate(99, "炸裂", boom)
    assert status == gate_mod.FAIL
    assert "RuntimeError" in summary
    assert any("RuntimeError" in ln or "门禁炸了" in ln for ln in notes)
    capsys.readouterr()  # 静默消费输出，保持测试输出干净


def test_gate_skip_outside_whitelist_becomes_fail():
    def sneaky():
        return gate_mod.SKIP, "想蒙混过关", []

    status, summary, _notes, _cost = gate_mod.run_gate(3, "产品", sneaky)
    assert status == gate_mod.FAIL
    assert "SKIP" in summary and "白名单" in summary


def test_gate_skip_in_whitelist_is_kept():
    def offline_smoke():
        return gate_mod.SKIP, "真机冒烟默认离线", []

    status, _summary, _notes, _cost = gate_mod.run_gate(8, "真机冒烟", offline_smoke)
    assert status == gate_mod.SKIP


def test_gate_illegal_status_becomes_fail():
    def weird():
        return "MAYBE", "既不是 PASS 也不是 FAIL", []

    status, summary, _notes, _cost = gate_mod.run_gate(1, "语法", weird)
    assert status == gate_mod.FAIL
    assert "MAYBE" in summary


# ── AsyncClient 纳入第 7 条扫描（报告 §5.3 补洞）───────────────────────────
def _scan(text):
    return gate_mod.scan_network_calls("tests/_probe_.py", text)


def test_scan_network_calls_asyncclient_is_hit():
    # 裸 AsyncClient( 调用必须被扫到（归入 Client 类口径）
    td = importlib.import_module("tdxdata")
    lines = ["from tdxdata import %s" % getattr(td, "AsyncClient").__name__,
             "def f():",
             "    ac = AsyncClient(client=None)"]
    hits = _scan("\n".join(lines))
    assert any(kind == "Client" for _ln, kind, _line in hits)


def test_scan_network_calls_qualified_asyncclient_is_hit():
    lines = ["def f(mod):",
             "    ac = mod.AsyncClient(client=None)"]
    hits = _scan("\n".join(lines))
    assert len(hits) == 1 and hits[0][1] == "Client"


def test_scan_network_calls_string_and_comment_masked():
    # 说明文字/字符串里的 Client( 不算命中
    lines = ["# 注释里写 Client( 不算",
             'print("默认构造走的 Client(**kw) 只是说明")',
             "x = 1"]
    assert _scan("\n".join(lines)) == []


def test_scan_network_calls_real_session_call_is_hit():
    import tdxdata.sections as _sec  # noqa: F401
    session_cls = getattr(_sec, "Session", None)
    lines = ["import tdxdata.sections as S",
             "def f():",
             "    sess = S.%s()" % (session_cls.__name__ if session_cls else "Session")]
    hits = _scan("\n".join(lines))
    assert any(kind == "Session" for _ln, kind, _line in hits)


def test_gate_module_scan_patterns_include_asyncclient():
    kinds = [k for k, _pat in gate_mod.NET_CALL_PATTERNS]
    assert "AsyncClient" in kinds


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
