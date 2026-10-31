# -*- coding: utf-8 -*-
"""跨表一致性：`products.json` / `ctl_map.json` / `tools/catalog/05-接口目录.md` 的 `ctl` 必须互相对得上。

为什么要专门有一个测试
------------------
`tools/gate.py` 的“ctl 一致性”步骤比对的是 `tools/catalog/05-接口目录.md`，
**不比对 `ctl_map.json`** —— 也就是说“产品表里写的 ctl”和“查表层记住的 ctl”
这两份数据一直没有交叉校验。`DataAggregation.queryCardInfo` 就是从这个盲区里漏出去的：
产品表写 30、命令目录写 31，两边各自都“自洽”，没有任何断言会把它们摆在一起看。
本文件补上这个盲区：

1. **值必须相等**：每条产品的 `ctl` == `ctl_map[cmd]["ctl"]`，失败时点名命令 + 两个值；
2. **命令必须存在**：每条产品的 `cmd` 都能在命令目录里找到，落单的就是真缺陷（不补数据、不放宽）；
3. **基数下界**：防止文件被读空导致“空满足”（0 条产品当然 0 条冲突）；
4. **md 的 ctl 列也要对上目录**：`tools/catalog/05-接口目录.md` 与 `products.json`
   一起错时 gate 仍然是绿的（两处同源错误），所以 md 的 ctl 列单独再跟
   `ctl_map.json` 比一遍（2026-10-31 两例：`DataAggregation.queryCardInfo` 30→31、
   `JNLPSE.hotListCard` 0x13→0x14）。

证据（`DataAggregation.queryCardInfo` 取 31 是真值）：`F:\\TDX\\raw\\catalog\\b2_0.jsonl`
第 210 行 `{"name": "DataAggregation.queryCardInfo", "ctl": 31, "kind": "POS_BIN", "len": 2334}`
是正响应；`F:\\TDX\\raw\\catalog\\ctl_unified.json` 该命令为 `{"ctl": 31, "src": "capture"}`。

纯离线：只读文件，不建连、不发请求。
"""
from __future__ import annotations

import io
import json
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PRODUCTS_JSON = os.path.join(_ROOT, "tdxdata", "data", "products.json")
_CTL_MAP_JSON = os.path.join(_ROOT, "tdxdata", "_engine", "catalog", "ctl_map.json")
#: 接口目录的留档副本（gate 第 4/5 条的证据源；`接口文档/spec/` 那份已不在本仓库）
_INTERFACE_MD = os.path.join(_ROOT, "tools", "catalog", "05-接口目录.md")
#: PB* 接口的实测规格（14 条，键是命令名/说明；本条判据只用来证明"冒号形态连这里也查不到"）
_PB_SPECS_JSON = os.path.join(_ROOT, "tdxdata", "_engine", "catalog", "pb_specs.json")

#: md 里的一行 ctl 登记：`| \`命令名\` | 0x1F | ... |`（命令名带反引号、ctl 是 0x 十六进制）
_MD_CTL_ROW = re.compile(r"^\s*\|\s*`([^`]+)`\s*\|\s*(0x[0-9A-Fa-f]+)\s*\|")

#: 基线（防止文件被读空/被截断后“空满足”）。只当下界用，涨了不会红。
_BASE_PRODUCTS_WITH_CMD = 419
_BASE_COMMANDS = 732
_BASE_MD_ROWS = 470


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


def _md_ctl_rows():
    """解析 `tools/catalog/05-接口目录.md`：返回 `[(行号, 命令名, "0xNN")]`。

    只认形如 ``| `命令名` | 0x1F | ...`` 的行（命令名带反引号、ctl 是 0x 十六进制）——
    md 里还有别的表，不按这个形状的行不算 ctl 登记，解析时直接跳过。
    """
    rows = []
    with io.open(_INTERFACE_MD, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            m = _MD_CTL_ROW.match(line)
            if m:
                rows.append((lineno, m.group(1), m.group(2)))
    return rows


def test_interface_catalog_md_ctl_matches_ctlmap():
    """`tools/catalog/05-接口目录.md` 的 ctl 列必须与 `ctl_map.json` 一致。

    关闭的盲区：gate 第 5 条只比 products.json ↔ md，**两者一起错时它仍是绿的**
    （2026-10-31 两例：DataAggregation.queryCardInfo 30→31、JNLPSE.hotListCard 0x13→0x14）。
    md 是留档副本、没有生成器（`tools/gen_catalog_doc.py` 不在仓库里），所以只能靠这条
    用例把它跟 `ctl_map.json`（实测表）钉在一起。
    """
    rows = _md_ctl_rows()
    assert len(rows) >= _BASE_MD_ROWS, (
        "`tools/catalog/05-接口目录.md` 只解析出 %d 行 ctl 登记（下界 %d）——"
        "正则失效、文件被换掉、或表格式变了都会长这样，别把空集当‘全对’"
        % (len(rows), _BASE_MD_ROWS))

    commands = _commands()
    bad = []
    for lineno, cmd, ctl_hex in rows:
        rec = commands.get(cmd)
        if rec is None:
            continue          # 只在 md 里有、目录里没有的命令不在本条判据内
        md_ctl = int(ctl_hex, 16)
        map_ctl = rec.get("ctl")
        if md_ctl != map_ctl:
            bad.append("第 %d 行 %s: md=%s(%d) ≠ ctl_map=%s(%r)"
                       % (lineno, cmd, ctl_hex, md_ctl,
                          hex(map_ctl) if isinstance(map_ctl, int) else "?", map_ctl))

    assert not bad, (
        "`05-接口目录.md` 有 %d 行的 ctl 与 ctl_map.json 不一致：\n  %s"
        % (len(bad), "\n  ".join(bad)))

    # 独立钉死两条实测结论（防止有人把 md 与 ctl_map 一起改回旧值 → 上面的等值断言仍绿）
    pinned = {cmd: ctl_hex.lower() for _lineno, cmd, ctl_hex in rows}
    assert pinned.get("DataAggregation.queryCardInfo") == "0x1f", (
        "md 里 DataAggregation.queryCardInfo 的 ctl 是 %r，实测结论是 0x1F（31）"
        % (pinned.get("DataAggregation.queryCardInfo"),))
    assert pinned.get("JNLPSE.hotListCard") == "0x14", (
        "md 里 JNLPSE.hotListCard 的 ctl 是 %r，实测结论是 0x14（20）"
        % (pinned.get("JNLPSE.hotListCard"),))


#: `pb_products` 里 4 条**已知例外**：`cmd` 用的是 `JNLPSE:` 冒号形态（来自协议层 `command_db.json`），
#: 而 `ctl_map.json` / `pb_specs.json` 的目录口径是点号形态 `JNLPSE.xxx`。
#: 为什么是"显式枚举"而不是直接改数据：`pb_products` 的 36 条都不在 `products()` 的 421 条里
#: （`get()` 够不着、没有任何公开 API 消费它），而"服务端到底认哪个名字"离线无法证伪 ——
#: 改它是猜。所以钉成已知例外：未来新增悬挂、或这 4 条被补齐，本用例立刻红。
#: {产品名: 冒号形态命令}
_KNOWN_DANGLING = {
    "hot_ques_v2": "JNLPSE:getHotQues",
    "hot_strategy": "JNLPSE:hotStrategy",
    "result_get_v2": "JNLPSE:resultGet",
    "wenda": "JNLPSE:wendaQuery",
}


def _pb_products():
    return _load(_PRODUCTS_JSON)["pb_products"]


def _pb_specs():
    return _load(_PB_SPECS_JSON)


def test_pb_products_ctl_and_dangling_cmds_are_enumerated():
    """`pb_products`（36 条，不在 `products()` 里、`get()` 够不着）的 `cmd`/`ctl` 必须与目录一致，
    除了 4 条**显式枚举**的已知例外（冒号形态，目录里只有点号形态）。

    另一个要说的：`JNLPSE.hotListCard` 的旧 ctl **19** 曾经在**三处**同时存在 ——
    `tools/catalog/05-接口目录.md`、仓库外 `raw/catalog/iface_catalog.json`、
    以及 `products.json` 的 `pb_products`。同一个错值从一份上游素材扩散成三份，
    而 `gate` 第 5 条只比其中两处 —— 这正是"只比半张表"的盲区，本条用例把第三处也钉住。
    """
    pb = _pb_products()
    commands = _commands()
    specs = _pb_specs()

    assert len(pb) >= 36, "pb_products 只有 %d 条（基线 36）—— 文件被截断或读错" % len(pb)

    # 1) 悬挂命令必须**恰好**等于已知例外清单：多一条红（新增悬挂）、少一条也红（该删例外了）
    dangling = sorted(name for name, rec in pb.items()
                      if rec.get("cmd") and rec["cmd"] not in commands)
    new_dangling = sorted(set(dangling) - set(_KNOWN_DANGLING))
    gone = sorted(set(_KNOWN_DANGLING) - set(dangling))
    assert not new_dangling, (
        "pb_products 出现 %d 条未登记的悬挂命令（cmd 在 ctl_map.json 里查不到）：%s\n"
        "确认是真缺陷就该修数据；若确认是命名形态问题（冒号 vs 点号），"
        "再把它加进 _KNOWN_DANGLING 并在注释里写理由" % (len(new_dangling), new_dangling))
    assert not gone, (
        "这 %d 条已不再悬挂，请把它们从 _KNOWN_DANGLING 里删掉（例外不许变成永久豁免）：%s"
        % (len(gone), gone))

    # 2) 4 条例外逐条核对：名字没被悄悄换掉、确实仍查不到（目录 + pb_specs），
    #    但换成点号形态就能查到 —— 证明这是"命名形态"问题，不是"命令不存在"
    problems = []
    for name, want_cmd in sorted(_KNOWN_DANGLING.items()):
        rec = pb.get(name)
        if rec is None:
            problems.append("%s: pb_products 里已没有这个产品（例外该删掉）" % name)
            continue
        got_cmd = rec.get("cmd")
        if got_cmd != want_cmd:
            problems.append("%s: cmd=%r，例外常量记的是 %r" % (name, got_cmd, want_cmd))
            continue
        if got_cmd in commands:
            problems.append("%s: %s 现在能在 ctl_map.json 里查到了 → 例外该删掉" % (name, got_cmd))
        if got_cmd in specs:
            problems.append("%s: %s 现在能在 pb_specs.json 里查到了 → 例外该删掉" % (name, got_cmd))
        dotted = got_cmd.replace(":", ".", 1)
        if dotted not in commands:
            problems.append("%s: 点号形态 %s 在 ctl_map.json 里也查不到 —— 这不是命名形态问题，"
                            "是真缺陷（命令不存在）" % (name, dotted))
    assert not problems, ("pb_products 的已知例外与现状不符：\n  " + "\n  ".join(problems))

    # 3) 其余（32 条）的 cmd 必须能在 ctl_map 里查到，且 ctl 相等
    bad = []
    checked = 0
    for name, rec in sorted(pb.items()):
        cmd = rec.get("cmd")
        if not cmd or name in _KNOWN_DANGLING:
            continue
        rec_map = commands.get(cmd)
        if rec_map is None:
            bad.append("%s: cmd=%s 在 ctl_map.json 里查不到" % (name, cmd))
            continue
        checked += 1
        if rec.get("ctl") != rec_map.get("ctl"):
            bad.append("%s: cmd=%s products.json(pb_products) ctl=%r ≠ ctl_map.json ctl=%r"
                       % (name, cmd, rec.get("ctl"), rec_map.get("ctl")))
    assert checked >= 32, (
        "只比对了 %d 条 pb_products（基线 32 = 36 − 4 条例外）—— 比对面塌了，不是全对" % checked)
    assert not bad, (
        "%d 条 pb_products 与 ctl_map.json 不一致：\n  %s" % (len(bad), "\n  ".join(bad)))


