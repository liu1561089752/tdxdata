# -*- coding: utf-8 -*-
"""默认日期与类型注解的回归闸门（2026-10-29）。零网络。

两条都是「沉默的坑」，必须有用例盯着：

1. **默认日期是写死的常量**（`2026-10-23`）。不传 `date=` 时它不会自己往前走，
   半年后你会拿到一份"看起来正常、其实是半年前"的表。修法是**告警 + 可显式设置**。
2. **`py.typed` + `Typing :: Typed` 说自己是 Typed 包**，那就不该有一半公开方法没注解。
   这条闸门会拦住"新加方法忘了写类型"。
"""
from __future__ import annotations

import ast
import datetime
import pathlib
import warnings

import pytest

import tdxdata
from tdxdata import errors
import tdxdata.client as _C


# ══════════════════════════════════════════════════════════════════════════
# 一、默认日期：过期要响，显式传就不响
# ══════════════════════════════════════════════════════════════════════════
def test_set_default_date_accepts_two_formats(monkeypatch):
    monkeypatch.setitem(_C._PLACEHOLDERS, "date", "2026-10-23")
    tdxdata.set_default_date("2026-10-24")
    assert _C._PLACEHOLDERS["date"] == "2026-10-24"
    tdxdata.set_default_date("20261025")
    assert _C._PLACEHOLDERS["date"] == "20261025"


@pytest.mark.parametrize("bad", ["2026-13-45", "昨天", "", "2026/1"])
def test_set_default_date_rejects_junk(bad):
    with pytest.raises(errors.ParamError):
        tdxdata.set_default_date(bad)


def test_stale_default_date_warns_once(monkeypatch):
    old = (datetime.date.today() - datetime.timedelta(days=400)).strftime("%Y-%m-%d")
    monkeypatch.setitem(_C._PLACEHOLDERS, "date", old)
    monkeypatch.setattr(_C, "_DATE_WARNED", False)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _C._fill(["001", "#date#"], {}, "lhb")
        _C._fill(["001", "#date#"], {}, "lhb")          # 第二次不该再吵
    msgs = [str(x.message) for x in w]
    assert len(msgs) == 1, "同一进程只告警一次：收到 %r" % (msgs,)
    assert "写死的默认日期" in msgs[0]
    assert "date=" in msgs[0] and "set_default_date" in msgs[0], "告警要给出两条出路"


def test_fresh_default_date_is_quiet(monkeypatch):
    today = datetime.date.today().strftime("%Y-%m-%d")
    monkeypatch.setitem(_C._PLACEHOLDERS, "date", today)
    monkeypatch.setattr(_C, "_DATE_WARNED", False)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _C._fill(["001", "#date#"], {}, "lhb")
    assert not w


def test_explicit_date_kwarg_never_warns(monkeypatch):
    monkeypatch.setitem(_C._PLACEHOLDERS, "date", "2000-01-01")
    monkeypatch.setattr(_C, "_DATE_WARNED", False)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        out = _C._fill(["001", "#date#"], {"date": "2026-10-23"}, "lhb")
    assert out == ["001", "2026-10-23"]
    assert not w, "用户已经显式给了日期，就不该再提默认值"


def test_fill_still_substitutes_code_and_date(monkeypatch):
    monkeypatch.setattr(_C, "_DATE_WARNED", True)       # 静音告警
    assert _C._fill(["@code@", "#date#"], {"code": "000001", "date": "2026-10-23"},
                    "x") == ["000001", "2026-10-23"]


# ══════════════════════════════════════════════════════════════════════════
# 二、类型注解：公开方法必须全annotate
# ══════════════════════════════════════════════════════════════════════════
def _client_module_ast():
    path = pathlib.Path(tdxdata.__file__).with_name("client.py")
    return ast.parse(path.read_text(encoding="utf-8"))


def test_every_public_client_method_is_annotated():
    missing = []
    for cls in [n for n in _client_module_ast().body if isinstance(n, ast.ClassDef)]:
        if cls.name != "Client":
            continue
        for fn in cls.body:
            if not isinstance(fn, ast.FunctionDef) or fn.name.startswith("_"):
                continue
            args = [a for a in fn.args.args if a.arg not in ("self", "cls")]
            bare = [a.arg for a in args if a.annotation is None]
            if bare or fn.returns is None:
                missing.append((fn.name, bare, fn.returns is None))
    assert not missing, (
        "这些公开方法还有未注解的参数/返回值（包声明了 `py.typed` + Typing :: Typed）：%r"
        % (missing,))


def test_typed_marker_is_shipped():
    marker = pathlib.Path(tdxdata.__file__).with_name("py.typed")
    assert marker.exists(), "删了 py.typed 就等于撤掉类型承诺，要么补文件要么改 classifier"
