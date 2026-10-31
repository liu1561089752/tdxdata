# -*- coding: utf-8 -*-
"""跨表一致性：产品表 `products.json` 与命令目录 `ctl_map.json` 的 `ctl` 必须逐条一致。

为什么要专门有一个测试
------------------
`tools/gate.py` 的“ctl 一致性”步骤比对的是 `tools/catalog/05-接口目录.md`，
**不比对 `ctl_map.json`** —— 也就是说“产品表里写的 ctl”和“查表层记住的 ctl”
这两份数据一直没有交叉校验。`DataAggregation.queryCardInfo` 就是从这个盲区里漏出去的：
产品表写 30、命令目录写 31，两边各自都“自洽”，没有任何断言会把它们摆在一起看。
本文件补上这个盲区：

1. **值必须相等**：每条产品的 `ctl` == `ctl_map[cmd]["ctl"]`，失败时点名命令 + 两个值；
2. **命令必须存在**：每条产品的 `cmd` 都能在命令目录里找到，落单的就是真缺陷（不补数据、不放宽）；
3. **基数下界**：防止文件被读空导致“空满足”（0 条产品当然 0 条冲突）。

证据（`DataAggregation.queryCardInfo` 取 31 是真值）：`F:\\TDX\\raw\\catalog\\b2_0.jsonl`
第 210 行 `{"name": "DataAggregation.queryCardInfo", "ctl": 31, "kind": "POS_BIN", "len": 2334}`
是正响应；`F:\\TDX\\raw\\catalog\\ctl_unified.json` 该命令为 `{"ctl": 31, "src": "capture"}`。

纯离线：只读两个 JSON 文件，不建连、不发请求。
"""
from __future__ import annotations

import io
import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PRODUCTS_JSON = os.path.join(_ROOT, "tdxdata", "data", "products.json")
_CTL_MAP_JSON = os.path.join(_ROOT, "tdxdata", "_engine", "catalog", "ctl_map.json")

#: 基线（防止文件被读空/被截断后“空满足”）。只当下界用，涨了不会红。
_BASE_PRODUCTS_WITH_CMD = 419
_BASE_COMMANDS = 732


def _load(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _products():
    return _load(_PRODUCTS_JSON)["products"]


def _commands():
    return _load(_CTL_MAP_JSON)["commands"]


def test_registry_files_are_not_empty():
    """两个 JSON 都得读得出东西，且条数不低于基线 —— 否则后面的比对全是空满足。"""
    products = _products()
    commands = _commands()

    products_with_cmd = [(name, spec) for name, spec in products.items() if spec.get("cmd")]
    cmds = {spec["cmd"] for _name, spec in products_with_cmd}

    assert len(products) >= 421, "products.json 只有 %d 个产品（基线 421）" % len(products)
    assert len(products_with_cmd) >= _BASE_PRODUCTS_WITH_CMD, (
        "带 cmd 的产品只有 %d 条（基线 %d）" % (len(products_with_cmd), _BASE_PRODUCTS_WITH_CMD))
    assert len(cmds) >= _BASE_PRODUCTS_WITH_CMD, (
        "产品表只覆盖 %d 条去重命令（基线 %d）" % (len(cmds), _BASE_PRODUCTS_WITH_CMD))
    assert len(commands) >= _BASE_COMMANDS, (
        "ctl_map.json 的 commands 只有 %d 条（基线 %d）" % (len(commands), _BASE_COMMANDS))


def test_every_product_cmd_exists_in_ctl_map():
    """产品表里每条 `cmd` 都必须在命令目录里查得到；落单的全列出来。"""
    commands = _commands()
    orphans = ["%s → %s" % (name, spec["cmd"])
               for name, spec in _products().items()
               if spec.get("cmd") and spec["cmd"] not in commands]

    assert not orphans, (
        "有 %d 条产品的 cmd 在 ctl_map.json 里查不到（产品名 → 命令名）：\n  %s"
        % (len(orphans), "\n  ".join(orphans)))


def test_product_ctl_equals_ctl_map_ctl():
    """每条产品的 `ctl` 必须逐条等于命令目录里同一条命令的 `ctl`。

    不一致时报出**命令名 + 产品表的值 + 命令目录的值** —— 只说"assert 不等"没法定位。
    """
    commands = _commands()
    bad = []
    compared = 0
    for name, spec in _products().items():
        cmd = spec.get("cmd")
        if not cmd or cmd not in commands:
            continue          # 落单命令由 test_every_product_cmd_exists_in_ctl_map 负责报红
        compared += 1
        want = commands[cmd].get("ctl")
        got = spec.get("ctl")
        if got != want:
            bad.append("%s（产品 %s）: products.json ctl=%r ≠ ctl_map.json ctl=%r"
                       % (cmd, name, got, want))

    assert compared >= _BASE_PRODUCTS_WITH_CMD, (
        "只比对了 %d 条产品（基线 %d）—— 比对面塌了，不是全对" % (compared, _BASE_PRODUCTS_WITH_CMD))
    assert not bad, (
        "%d 条产品的 ctl 与 ctl_map.json 冲突：\n  %s" % (len(bad), "\n  ".join(bad)))


def test_ctl_values_are_ints():
    """`ctl` 一律是 int —— 一张表写成 `"31"`、另一张写 `31`，相等判断会静默失效。"""
    def _non_int(table):
        return ["%s: %r" % (key, rec.get("ctl"))
                for key, rec in table.items() if not isinstance(rec.get("ctl"), int)]

    bad = _non_int(_products()) + _non_int(_commands())
    assert not bad, "%d 条 ctl 不是 int：\n  %s" % (len(bad), "\n  ".join(bad[:20]))
