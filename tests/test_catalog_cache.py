# -*- coding: utf-8 -*-
"""ENG-014：`products.json` 每进程只读一次（10 个 Client 只读 1 次）。

判据不是"看起来很省"，而是**数 `open()` 的调用次数** —— 用 `builtins.open` 的探针数
目标路径被打开几次。附带两条：缓存必须是共享的（否则"只读一次"是假的），
以及**文件缺失时仍要抛带路径的 `TdxError`**（不能因为缓存把错误也缓存没了）。
"""
from __future__ import annotations

import builtins
import os

import pytest

import tdxdata
from tdxdata import client as client_mod
from tdxdata import errors


def _bare_client():
    """只跑到 `_load_products()` 的裸对象：不建连、不读时钟、零网络。"""
    cls = getattr(tdxdata, "Client")
    c = cls.__new__(cls)
    c._products = {}
    c._pb_products = {}
    c._load_products()
    return c


@pytest.fixture(autouse=True)
def _fresh_catalog(monkeypatch):
    """每条用例都从"空缓存"开始，避免用例间互相喂缓存。"""
    monkeypatch.setattr(client_mod, "_CATALOG", {})


def test_ten_clients_open_the_catalog_once(monkeypatch):
    opened = []
    real_open = builtins.open

    def spy(path, *a, **kw):
        opened.append(os.fspath(path))
        return real_open(path, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy)
    clients = [_bare_client() for _ in range(10)]
    hits = [p for p in opened if p.endswith("products.json")]
    assert len(hits) == 1, "products.json 被打开 %d 次（应为 1）：%s" % (len(hits), hits)
    assert all(len(c._products) == 424 for c in clients)


def test_cache_is_shared_not_copied():
    """共享同一份 dict —— 这也是"不许原地修改"这条纪律的由来（文档里已声明）。"""
    a = _bare_client()
    b = _bare_client()
    assert a._products is b._products
    assert a._pb_products is b._pb_products
    assert a._products is client_mod._catalog()["products"]


def test_catalog_content_is_intact():
    """缓存不许把内容读漏（424 个产品 + 36 个 PB 产品 + ctl 字段都在）。"""
    cat = client_mod._catalog()
    assert len(cat["products"]) == 424
    assert len(cat["pb_products"]) == 36
    macro = {k for k, v in cat["products"].items() if v.get("domain") == "宏观"}
    assert len(macro) == 29


def test_missing_catalog_still_raises_with_path(monkeypatch, tmp_path):
    """文件缺失 → `TdxError`（带路径），且**不会**因为缓存过一次就吞掉错误。"""
    ghost = str(tmp_path / "no_such_products.json")
    monkeypatch.setattr(client_mod, "_DATA", ghost)
    with pytest.raises(errors.TdxError) as ei:
        _bare_client()
    assert ghost in str(ei.value)


def test_products_view_still_works_after_cache(monkeypatch):
    """`products()` 走的是同一份缓存数据：过滤/计数与直接读 JSON 一致。"""
    c = _bare_client()
    all_products = c.products()
    assert len(all_products) == 424
    assert len(list({p.domain for p in all_products})) == 17
    macro = [p for p in all_products if p.domain == "宏观"]
    assert len(macro) == 29
    assert c.domains() == sorted({p.domain for p in all_products})
