# -*- coding: utf-8 -*-
r"""把「只能真机才能定」的三类遗留项变成**一条命令可结算**（只报告，不改数据）。

离线默认（安全，永远不联网）
----------------------------
    python tools/verify_pending.py
        → 只打印三类清单的**条数**、每条待验问题的**一句话**、以及「真跑请加 `--run`」的提示。
          退出码 0。仓库里 / CI / gate 里跑它都不会建连。

联网核对（需要真机 / 真实网络）
--------------------------------
    python tools/verify_pending.py --run                  # 三类全跑
    python tools/verify_pending.py --run --only A         # 只跑 A 类
    python tools/verify_pending.py --run --json out.json  # 指定结果落盘位置

三类遗留项
----------
**A. `call` 形态命令**（`tdxdata/_engine/catalog/ctl_map.json` 里 `form == "call"`）

    目录说请求体要带 `CallName`，但 `session.body_of` 只对 `catalog/form_measured.json`
    里实测过的命令用实测形态，其余按前缀兜底发 `{"Params": …}`。若服务端**只认**
    `CallName`，这些命令会**静默丢包**（不报错、只回空包）—— 只有真发两次才知道哪个成立。
    判据：同一条命令发两遍（带 `CallName` / 不带），比行数。

**B. `not_in_app` ∧ `rows>0` 冲突**

    仓库外 `F:\TDX\raw\catalog\not_in_app.json`（**未随包分发**）说「本版本 App 未实现」，
    包内 `tdxdata/_engine/catalog/verified.json` 说「实测取到过行」。离线按「正向证据优先」
    全归 verified（不拦）。这里再发一次：还能出行 → 保持 verified；回 -104 / 空 → 该归 dead。
    清单**现算**（两个文件求交集），脚本里不硬编码命令名 —— 素材变了清单跟着变。

**C. `dead` 档的产品命令**

    `products.json` 的 `cmd` ∩ `catalog/dead_cmds.json`：`get()` 一律抛 `NotSupportedError`
    （`allow_dead=True` 可强发）。名单是 2026-10-31 的负证据快照，这里复核它是否已过期。

怎么发（全部走库的公开 API，不自己解帧）
----------------------------------------
* A 类：`tdxdata._engine.datacenter.call_raw(cmd, ctl, body, host=…, wait=…, idle=…)`，
  返回**已切好的 blob 列表**（不要再 unwrap / 再解包）；行数用 `parse_resultset` /
  `decode_json` 这两个库函数，**按 `call_raw` 的契约直接吃 `raw[0]`**。
* B / C 类：`tdxdata.Client().call_cmd(cmd, allow_dead=True, allow_empty=True)`，
  行数取返回 `Table.rows` 的长度；所有异常都 `try/except` 住并打印异常类型，
  **一条炸掉不影响后面的跑**。

输出 / 落盘
-----------
* **只报告**：本工具**不会自动改任何数据文件**（连 `--json` 也只写仓库外）。
  跑完会打印「下一步该改哪个文件的哪一行」，改不改由人决定。
* `--json PATH`：结果落一份 JSON。默认 `%TEMP%\verify_pending_<yyyymmdd-HHMMSS>.json`；
  **写进仓库目录会被直接拒绝**（防止把探测结果当数据提交）。

退出码
------
* `0` = 跑完（结果好坏看报告，不看退出码）；
* `1` = 参数 / 环境错误（比如 `--json` 指到仓库里、`--conflicts` 指到非 JSON）；
* `2` = 有探测抛了**非预期**异常（报告照出，但要求人看一眼）。

依赖：纯标准库。脚本顶层**不建连**，所有网络动作都在 `--run` 分支里。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import tempfile
import time
import traceback

# ── 路径常量（从本文件位置反推仓库根，和 cwd 无关）─────────────────────────
# 本脚本固定位于 <仓库根>/tools/verify_pending.py。
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_CATALOG = os.path.join(_ROOT, "tdxdata", "_engine", "catalog")
_PRODUCTS_JSON = os.path.join(_ROOT, "tdxdata", "data", "products.json")
_CTL_MAP_JSON = os.path.join(_CATALOG, "ctl_map.json")
_VERIFIED_JSON = os.path.join(_CATALOG, "verified.json")
_DEAD_JSON = os.path.join(_CATALOG, "dead_cmds.json")
_FORM_MEASURED_JSON = os.path.join(_CATALOG, "form_measured.json")

#: B 类的负证据素材：**仓库外**、未随包分发。缺了就优雅跳过（可用 --conflicts 指定）。
_DEFAULT_CONFLICTS = os.path.join(r"F:\TDX\raw\catalog", "not_in_app.json")

LABEL_A = "A"
LABEL_B = "B"
LABEL_C = "C"
CLASS_TITLE = {
    LABEL_A: 'A. `call` 形态命令（目录说带 CallName，实际可能按 base 兜底发）',
    LABEL_B: "B. `not_in_app` ∧ `rows>0` 冲突（负证据 vs 正证据）",
    LABEL_C: "C. `dead` 档的产品命令（负证据快照是否已过期）",
}

#: A 类两种形态之间/命令之间的人工限速（`call_raw` 自己管 idle，这里只防打太密）
_SLEEP_BETWEEN_FORMS = 1.5
_SLEEP_BETWEEN_CMDS = 1.5


# ── 小工具 ────────────────────────────────────────────────────────────────
def _say(text=""):
    print(text)


def _load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _commands():
    return _load_json(_CTL_MAP_JSON)["commands"]


def _product_cmds():
    """`products.json` 的 `products` 段里所有 `cmd` → {cmd: [产品名, …]}（不去重丢掉归属）。"""
    products = _load_json(_PRODUCTS_JSON)["products"]
    out = {}
    for name, spec in products.items():
        cmd = spec.get("cmd")
        if cmd:
            out.setdefault(cmd, []).append(name)
    return out


def _norm_missing_list(data):
    """`not_in_app.json` 可能是裸 list，也可能是 {cmd: …} 的 dict —— 两种都吃。"""
    if isinstance(data, dict):
        return sorted(data.keys())
    return sorted(data)


def _stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def _inside_repo(path):
    try:
        target = os.path.abspath(path)
        root = os.path.abspath(_ROOT)
        return os.path.commonpath([target, root]) == root
    except ValueError:            # 不同盘符 → 一定不在仓库里
        return False


def _ensure_pkg_importable():
    """让 `import tdxdata` 找到**本仓库**的包（`python tools\\verify_pending.py` 时 sys.path[0]
    是 `tools/`，不是仓库根）。已安装的同名包不算数 —— 核对工具必须对着眼前这份仓库跑。
    """
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)


# ── 清单：全部离线求得（dry-run 只跑到这里）────────────────────────────────
def collect_a():
    """A 类：`ctl_map.json` 里 `form == "call"` 的命令。"""
    commands = _commands()
    try:
        measured = _load_json(_FORM_MEASURED_JSON)
    except (OSError, ValueError):
        measured = {}
    items = []
    for cmd in sorted(commands):
        rec = commands[cmd]
        if rec.get("form") != "call":
            continue
        items.append({
            "cmd": cmd,
            "ctl": rec.get("ctl"),
            "measured_form": measured.get(cmd),
            "question": "带 `CallName` 与不带，哪个能出行？（目录说 call，代码可能按 base 兜底）",
        })
    return items


def collect_b(conflicts_path):
    """B 类：`not_in_app.json` ∩ `verified.json`（现算，不硬编码命令名）。

    返回 `(items, note)`；素材缺失时 `items is None`、`note` 说明原因。
    """
    if not conflicts_path or not os.path.isfile(conflicts_path):
        return None, ("素材缺失：%s（该文件**未随包分发**；可用 `--conflicts PATH` 指定）"
                      % (conflicts_path or "<未指定>"))
    try:
        not_in_app = _norm_missing_list(_load_json(conflicts_path))
    except (OSError, ValueError) as exc:
        return None, "素材读不了：%s（%s: %s）" % (conflicts_path, type(exc).__name__, exc)
    try:
        verified = _load_json(_VERIFIED_JSON)
    except (OSError, ValueError) as exc:
        return None, "包内 verified.json 读不了（%s: %s）" % (type(exc).__name__, exc)

    overlap = sorted(set(not_in_app) & set(verified))
    items = []
    for cmd in overlap:
        rec = verified.get(cmd) or {}
        items.append({
            "cmd": cmd,
            "verified_rows": rec.get("rows"),
            "verified_cols": rec.get("cols"),
            "checked_at": rec.get("checked_at"),
            "question": "两源冲突：not_in_app 说未实现、verified 说取到过 %r 行 —— 现在再发一次还有行吗？"
                        % (rec.get("rows"),),
        })
    return items, None


def collect_c():
    """C 类：`products.json` 的 cmd ∩ `dead_cmds.json`。"""
    try:
        dead = _load_json(_DEAD_JSON)
    except (OSError, ValueError) as exc:
        _say("! catalog/dead_cmds.json 读不了（%s: %s），C 类跳过" % (type(exc).__name__, exc))
        return []
    product_cmds = _product_cmds()
    items = []
    for cmd in sorted(product_cmds):
        if cmd not in dead:
            continue
        items.append({
            "cmd": cmd,
            "products": sorted(product_cmds[cmd]),
            "dead_reason": (dead.get(cmd) or {}).get("reason"),
            "question": "`get()` 现在会抛 NotSupportedError；真发一次还有数据吗？",
        })
    return items


def build_plan(args):
    b_items, b_note = collect_b(args.conflicts)
    plan = {LABEL_A: collect_a(), LABEL_B: b_items, LABEL_C: collect_c()}
    notes = {LABEL_B: b_note}
    wanted = _parse_only(args.only)
    if wanted is not None:
        for label in (LABEL_A, LABEL_B, LABEL_C):
            if label not in wanted:
                plan[label] = [] if plan[label] is not None else None
                if label == LABEL_B and plan[LABEL_B] is None:
                    notes[LABEL_B] = "（--only 未选 B）"
    return plan, notes


def _parse_only(value):
    if not value:
        return None
    wanted = set()
    for part in str(value).replace(",", " ").split():
        part = part.strip().upper()
        if part:
            wanted.add(part)
    bad = wanted - {LABEL_A, LABEL_B, LABEL_C}
    if bad:
        raise SystemExit("--only 只认 A/B/C（收到 %s）" % sorted(bad))
    return wanted


def _cap(items, limit):
    if limit and len(items) > limit:
        return items[:limit], True
    return items, False


# ── dry-run 输出 ──────────────────────────────────────────────────────────
def print_dry_run(plan, notes):
    _say("=" * 78)
    _say("tools/verify_pending.py  ·  离线清单（dry-run，**不联网**）")
    _say("=" * 78)

    a_items = plan[LABEL_A] or []
    b_items = plan[LABEL_B]
    c_items = plan[LABEL_C] or []

    measured_hit = [it["cmd"] for it in a_items if it.get("measured_form")]
    _say("")
    _say("%s —— %d 条" % (CLASS_TITLE[LABEL_A], len(a_items)))
    _say("    说明：`catalog/form_measured.json` 里已有实测形态的 %d 条（不受影响）"
         % len(measured_hit))
    for it in a_items:
        _say("    - %-42s ctl=%-4s %s" % (it["cmd"], it["ctl"], it["question"]))
    _say("    结算判据：两种形态各发 1 次比行数 —— 带 CallName 有行、不带为空 ⇒ 目录的 call 是对的")

    _say("")
    if b_items is None:
        _say("%s —— 跳过（%d 条算不出来）" % (CLASS_TITLE[LABEL_B], 0))
        _say("    原因：%s" % notes.get(LABEL_B))
    else:
        _say("%s —— %d 条" % (CLASS_TITLE[LABEL_B], len(b_items)))
        for it in b_items:
            _say("    - %-42s verified rows=%-4s %s"
                 % (it["cmd"], it["verified_rows"], it["question"]))
        _say("    结算判据：还能出行 ⇒ 保持 verified（负证据作废）；回 -104 / 空 ⇒ 该归 dead")

    _say("")
    _say("%s —— %d 条" % (CLASS_TITLE[LABEL_C], len(c_items)))
    for it in c_items:
        _say("    - %-42s 产品=%s" % (it["cmd"], ",".join(it["products"])))
        _say("      待验：%s" % it["question"])
    _say("    结算判据：有数据 ⇒ 从 dead_cmds.json 删掉该条；仍空/-104 ⇒ 维持 dead")

    _say("")
    _say("-" * 78)
    _say("合计：A %d 条 / B %s / C %d 条"
         % (len(a_items), "素材缺失" if b_items is None else ("%d 条" % len(b_items)), len(c_items)))
    _say("真跑请加 `--run`（会**真连服务端**，逐条发一次；本工具只报告、不改任何数据文件）。")
    _say("  python tools/verify_pending.py --run")
    _say("  python tools/verify_pending.py --run --only A --json %s"
         % os.path.join(tempfile.gettempdir(), "verify_pending_%s.json" % _stamp()))
    _say("如果只想看某一类：`--only A` / `--only B,C`；只想试几条：`--limit 3`。")


# ── 行数统计（严格按 call_raw 的契约：它给的 list[bytes] 已经是切好的 blob）──
def _count_json_rows(obj):
    if obj is None:
        return 0
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        for key in ("data", "Data", "result", "Result", "list", "List", "items", "rows"):
            value = obj.get(key)
            if isinstance(value, list):
                return len(value)
        return 1 if obj else 0
    return 1


def _count_blobs(raw, decode_json, parse_resultset):
    """blob 列表 → (行数, 用了哪个解析器)。解析器全失败返回 (None, 原因)。"""
    if not raw:
        return 0, "空 blob 列表"
    rows = None
    try:
        parsed = parse_resultset(raw)
    except Exception:                       # noqa: BLE001 —— 解析器抛什么都只是"这条路不通"
        parsed = None
    if isinstance(parsed, list):
        rows = parsed
        if rows:
            return len(rows), "parse_resultset"
    try:
        obj, _text = decode_json(raw[0])
    except Exception:                       # noqa: BLE001
        obj = None
    if obj is not None:
        return _count_json_rows(obj), "decode_json"
    if isinstance(rows, list):
        return len(rows), "parse_resultset(0 行)"
    return None, "两种解析器都没吃下（%d 个 blob）" % len(raw)


# ── --run：A 类（自定义 body，走 call_raw）────────────────────────────────
def _params_for(ctlmap, cmd, args, notes):
    """`ctlmap.params_best_for(cmd)`，`None` → `[]`；模板里的占位符按 `--code/--date` 填。"""
    params = ctlmap.params_best_for(cmd)
    if params is None:
        return []
    try:
        filled = ctlmap.fill(params, code=args.code, date=args.date)
    except Exception as exc:                # noqa: BLE001
        notes.append("fill() 失败（%s: %s），按原模板发" % (type(exc).__name__, exc))
        return params
    return filled


def _body_call(cmd, params):
    name = cmd.split(".", 1)[1] if "." in cmd else cmd
    return json.dumps({"CallName": name, "Params": params}, ensure_ascii=False).encode("utf-8")


def _body_base(params):
    return json.dumps({"Params": params}, ensure_ascii=False).encode("utf-8")


def _send_a(call_raw, decode_json, parse_resultset, item, body_kind, body, args):
    """发一次 A 类请求，返回一条结果 dict。"""
    result = {
        "cmd": item["cmd"],
        "class": "%s.%s" % (LABEL_A, body_kind),
        "ctl": item.get("ctl"),
        "body_kind": body_kind,
        "rows": None,
        "parser": None,
        "error": None,
    }
    try:
        raw = call_raw(item["cmd"], item["ctl"], body, host=args.host, wait=args.wait, idle=args.idle)
    except Exception as exc:                # noqa: BLE001 —— 一条炸掉不影响后面
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        return result
    rows, parser = _count_blobs(raw, decode_json, parse_resultset)
    result["rows"] = rows
    result["parser"] = parser
    if rows is None:
        result["error"] = "解析失败"
    return result


def _verdict_a(call_res, base_res):
    """两种形态的对照 → (结论码, 一句话判定, 下一步该改哪儿)。"""
    c_rows = None if call_res is None else call_res.get("rows")
    b_rows = None if base_res is None else base_res.get("rows")

    if c_rows is None and b_rows is None:
        return ("INCONCLUSIVE", "两种形态都没拿到可解析的回包",
                "先看 ctl 对不对、参数模板是否缺占位符；仍无解再查 dead_cmds.json")
    if (c_rows or 0) > 0 and not (b_rows or 0) > 0:
        return ("NEED_CALLNAME", "带 CallName 有行、不带为空 → 目录的 form=call 是对的",
                '把该命令补进 tdxdata/_engine/catalog/form_measured.json，值 "call"')
    if not (c_rows or 0) > 0 and (b_rows or 0) > 0:
        return ("CATALOG_WRONG", "带 CallName 为空、不带反而有行 → 目录记的 form=call 是错的",
                '把 ctl_map.json 该命令的 form 改 "base"，并在 form_measured.json 记 "base"')
    if (c_rows or 0) > 0 and (b_rows or 0) > 0:
        return ("BOTH_OK", "两种形态都能出行 → 现状（前缀兜底 base）不会丢包，但 CallName 也合法",
                "可不改；要更贴目录就把该命令补进 form_measured.json，值 \"call\"")
    return ("BOTH_EMPTY", "两种形态都是 0 行 → 不是形态问题",
            "查参数模板 / ctl（form_measured.json 里没有这条的实测）；再看是否实机未实现")


# ── --run：B / C 类（走 Client().call_cmd）───────────────────────────────
def _send_via_client(client, cmd, args):
    """发一条目录命令，返回 (行数, 列数, 错误字符串)。异常一律转成字符串。"""
    try:
        table = client.call_cmd(
            cmd,
            code=args.code,
            date=args.date,
            allow_dead=True,        # 目的就是绕过"负证据门禁"去问服务端
            allow_empty=True,       # 空包是**有用信息**（=这条可能真死了），不该抛
            idle=args.idle,
        )
    except Exception as exc:        # noqa: BLE001
        return None, None, "%s: %s" % (type(exc).__name__, exc)
    rows = getattr(table, "rows", None)
    cols = getattr(table, "cols", None)
    n_rows = len(rows) if isinstance(rows, list) else None
    n_cols = len(cols) if isinstance(cols, list) else None
    return n_rows, n_cols, None


def _looks_like_dead_error(err):
    """异常文本像不像"服务端说这条没实现"（-104 这类）。

    只是**启发式**：判定结果会把异常原文一起打出来，人还能复核。
    """
    text = str(err)
    return ("-104" in text) or ("104" in text) or ("未实现" in text)


def _verdict_b(rows, err):
    if err:
        if _looks_like_dead_error(err):
            return ("DEAD", "报错里带 -104/未实现 → 负证据成立",
                    "把该命令移进 tdxdata/_engine/catalog/dead_cmds.json（并从 verified.json 摘掉）")
        return ("ERROR", "非预期异常：%s" % err, "人工看一眼；不改数据")
    if (rows or 0) > 0:
        return ("KEEP_VERIFIED", "%d 行 → 现在还能出行，正证据仍成立" % rows,
                "不改（保持 verified；not_in_app.json 那条负证据按作废处理）")
    return ("DEAD", "0 行 → 现在取不到数据",
            "把该命令移进 tdxdata/_engine/catalog/dead_cmds.json（并从 verified.json 摘掉）")


def _verdict_c(rows, err):
    if err:
        if _looks_like_dead_error(err):
            return ("KEEP_DEAD", "报错里带 -104/未实现 → 负证据仍成立",
                    "不改（维持 dead_cmds.json）")
        return ("ERROR", "非预期异常：%s" % err, "人工看一眼；维持 dead 不变")
    if (rows or 0) > 0:
        return ("REVIVE", "%d 行 → dead 快照已过期（现在有数据）" % rows,
                "把该命令从 tdxdata/_engine/catalog/dead_cmds.json 删掉（dead_cmds 是负证据快照）")
    return ("KEEP_DEAD", "0 行 → 仍取不到数据，负证据成立",
            "不改（维持 dead_cmds.json）")


# ── --run 主流程 ──────────────────────────────────────────────────────────
def run_probes(plan, notes, args):
    """真连跑一遍。返回 (results, summary, unexpected_errors)。"""
    # 所有库内导入都放在 --run 分支里：dry-run 一行都不 import 网络相关模块
    _ensure_pkg_importable()
    from tdxdata._engine.datacenter import call_raw, decode_json, parse_resultset  # noqa: PLC0415
    from tdxdata._engine import ctlmap                                            # noqa: PLC0415

    results = {LABEL_A: [], LABEL_B: [], LABEL_C: []}
    summary = {LABEL_A: {}, LABEL_B: {}, LABEL_C: {}}
    unexpected = 0

    a_items, a_capped = _cap(plan[LABEL_A] or [], args.limit)
    b_items, b_capped = _cap(plan[LABEL_B] or [], args.limit)
    c_items, c_capped = _cap(plan[LABEL_C] or [], args.limit)
    bound = 2 * len(a_items) + len(b_items) + len(c_items)
    _say("")
    _say("总请求数上界：%d（A %d×2 + B %d + C %d）%s"
         % (bound, len(a_items), len(b_items), len(c_items),
            "  ← 已按 --limit %d 截断" % args.limit if (a_capped or b_capped or c_capped) else ""))
    _say("A 类两种形态之间 sleep %.1fs，命令之间 sleep %.1fs；B/C 的限速由 Client(gap=%.1fs) 管。"
         % (_SLEEP_BETWEEN_FORMS, _SLEEP_BETWEEN_CMDS, args.gap))
    _say("-" * 78)
    _say("%-42s | %-10s | %5s | %s" % ("命令", "类别", "行数", "判定"))
    _say("-" * 78)

    # ── A 类 ──
    for index, item in enumerate(a_items):
        per_cmd_notes = []
        params = _params_for(ctlmap, item["cmd"], args, per_cmd_notes)
        call_res = _send_a(call_raw, decode_json, parse_resultset, item, "call",
                           _body_call(item["cmd"], params), args)
        time.sleep(_SLEEP_BETWEEN_FORMS)
        base_res = _send_a(call_raw, decode_json, parse_resultset, item, "base",
                           _body_base(params), args)

        code, text, next_step = _verdict_a(call_res, base_res)
        for res, tag in ((call_res, "call"), (base_res, "base")):
            results[LABEL_A].append({
                "cmd": item["cmd"], "class": res["class"], "ctl": item.get("ctl"),
                "body_kind": tag, "rows": res.get("rows"), "parser": res.get("parser"),
                "error": res.get("error"), "params": params,
            })
            _say("%-42s | %-10s | %5s | %s"
                 % (item["cmd"], "%s.%s" % (LABEL_A, tag),
                    res.get("rows") if res.get("rows") is not None else "-",
                    res.get("error") or "ok"))
        _say("  ↳ 结论[%s] %s" % (code, text))
        _say("  ↳ 下一步：%s" % next_step)
        if any(r.get("error") for r in (call_res, base_res)) and code == "INCONCLUSIVE":
            unexpected += 1
        summary[LABEL_A][item["cmd"]] = {
            "code": code, "text": text, "next_step": next_step,
            "call_rows": call_res.get("rows"), "base_rows": base_res.get("rows"),
            "notes": per_cmd_notes,
        }
        if index + 1 < len(a_items):
            time.sleep(_SLEEP_BETWEEN_CMDS)

    # ── B / C 类：共用一条连接 ──
    if b_items or c_items:
        from tdxdata import Client                                     # noqa: PLC0415
        with Client(host=args.host, gap=args.gap) as client:
            for item in b_items:
                rows, cols, err = _send_via_client(client, item["cmd"], args)
                code, text, next_step = _verdict_b(rows, err)
                results[LABEL_B].append({
                    "cmd": item["cmd"], "class": LABEL_B, "rows": rows, "cols": cols,
                    "error": err, "verified_rows": item.get("verified_rows"),
                })
                _say("%-42s | %-10s | %5s | %s"
                     % (item["cmd"], LABEL_B, rows if rows is not None else "-", err or text))
                _say("  ↳ 结论[%s] %s" % (code, text))
                _say("  ↳ 下一步：%s" % next_step)
                if code == "ERROR":
                    unexpected += 1
                summary[LABEL_B][item["cmd"]] = {
                    "code": code, "text": text, "next_step": next_step,
                    "rows": rows, "cols": cols, "error": err,
                }

            for item in c_items:
                rows, cols, err = _send_via_client(client, item["cmd"], args)
                code, text, next_step = _verdict_c(rows, err)
                results[LABEL_C].append({
                    "cmd": item["cmd"], "class": LABEL_C, "rows": rows, "cols": cols,
                    "error": err, "products": item.get("products"),
                })
                _say("%-42s | %-10s | %5s | %s"
                     % (item["cmd"], LABEL_C, rows if rows is not None else "-", err or text))
                _say("  ↳ 结论[%s] %s" % (code, text))
                _say("  ↳ 下一步：%s" % next_step)
                if code == "ERROR":
                    unexpected += 1
                summary[LABEL_C][item["cmd"]] = {
                    "code": code, "text": text, "next_step": next_step,
                    "rows": rows, "cols": cols, "error": err,
                    "products": item.get("products"),
                }

    return results, summary, unexpected


# ── 汇总 / 落盘 ───────────────────────────────────────────────────────────
def print_summary(summary, plan, notes):
    _say("")
    _say("=" * 78)
    _say("汇总（**本工具没有改任何数据文件**；下面是下一步该改哪儿）")
    _say("=" * 78)
    for label in (LABEL_A, LABEL_B, LABEL_C):
        per = summary.get(label) or {}
        if not per:
            reason = "（未跑）"
            if label == LABEL_B and notes.get(LABEL_B):
                reason = "（跳过：%s）" % notes[LABEL_B]
            _say("%s: 0 条已结算 %s" % (label, reason))
            continue
        counts = {}
        for rec in per.values():
            counts[rec["code"]] = counts.get(rec["code"], 0) + 1
        _say("%s: %d 条已结算 —— %s" % (label, len(per), counts))
        steps = {}
        for cmd, rec in per.items():
            if rec["code"] in ("BOTH_OK", "KEEP_VERIFIED", "KEEP_DEAD"):
                continue          # 这三类不用改数据
            steps.setdefault(rec["next_step"], []).append(cmd)
        for step, cmds in sorted(steps.items(), key=lambda kv: -len(kv[1])):
            _say("    · %s" % step)
            _say("      %d 条：%s" % (len(cmds), ", ".join(cmds[:8]) + (" …" if len(cmds) > 8 else "")))
    _say("")
    _say("提醒：以上全部是**建议**。本工具不写 tdxdata/ 下任何文件；要改数据请人工改并重跑")
    _say("      python -m pytest -q -m \"not online\" 与 python tools\\gate.py。")


def write_json(path, plan, results, summary, notes, bound, args):
    if not path:
        path = os.path.join(tempfile.gettempdir(), "verify_pending_%s.json" % _stamp())
    if _inside_repo(path):
        raise SystemExit("拒绝写入仓库目录（%s）—— 探测结果不是数据，请落到 %%TEMP%% 或仓库外" % path)
    payload = {
        "tool": "tools/verify_pending.py",
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "argv": list(sys.argv),
        "host": args.host,
        "probe_params": {"code": args.code, "date": args.date},
        "counts": {
            LABEL_A: len(plan[LABEL_A] or []),
            LABEL_B: None if plan[LABEL_B] is None else len(plan[LABEL_B] or []),
            LABEL_C: len(plan[LABEL_C] or []),
        },
        "notes": notes,
        "requests_bound": bound,
        "results": results,
        "summary": summary,
    }
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    _say("")
    _say("结果已落盘（仓库外）：%s" % path)
    return path


# ── CLI ───────────────────────────────────────────────────────────────────
def build_parser():
    parser = argparse.ArgumentParser(
        prog="verify_pending.py",
        description="把「只能真机才能定」的三类遗留项变成一条命令可结算（默认 dry-run 不联网）。",
        epilog="默认只打印清单；加 --run 才真连。本工具只报告，不改任何数据文件。",
    )
    parser.add_argument("--run", action="store_true",
                        help="真连服务端逐条发一次（默认不联网，只打印清单）")
    parser.add_argument("--only", default="",
                        help="只跑某几类：A / B / C，可逗号分隔（默认三类都跑）")
    parser.add_argument("--limit", type=int, default=0,
                        help="每类最多跑几条（0 = 不限；试水用）")
    parser.add_argument("--conflicts", default=_DEFAULT_CONFLICTS,
                        help="B 类的负证据素材（默认仓库外 %s）" % _DEFAULT_CONFLICTS)
    parser.add_argument("--json", default="",
                        help="结果 JSON 落盘路径（默认 %%TEMP%%\\verify_pending_<时间戳>.json；不许写进仓库）")
    parser.add_argument("--code", default="600519",
                        help="填参数模板里的 @code@（默认 600519）")
    parser.add_argument("--date", default="",
                        help="填参数模板里的日期族（默认空串：库不猜日期格式）")
    parser.add_argument("--host", default=None,
                        help="服务端地址（默认用库的缺省，可用 TDXDATA_HOST 之类的既有约定）")
    parser.add_argument("--wait", type=float, default=8.0,
                        help="call_raw 的 wait（秒，默认 8.0）")
    parser.add_argument("--idle", type=float, default=2.5,
                        help="call_raw / call_cmd 的 idle（秒，默认 2.5）")
    parser.add_argument("--gap", type=float, default=2.2,
                        help="Client 的请求间隔（秒，默认 2.2 = pacer.BASE_GAP）")
    return parser


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)

    try:
        plan, notes = build_plan(args)
    except SystemExit:
        raise
    except Exception as exc:                # noqa: BLE001
        _say("读清单失败（%s: %s）" % (type(exc).__name__, exc))
        traceback.print_exc()
        return 1

    if not args.run:
        print_dry_run(plan, notes)
        if args.json:
            _say("")
            _say("注：dry-run 不落盘（`--json` 只对 `--run` 有意义）。")
        return 0

    _say("=" * 78)
    _say("tools/verify_pending.py  ·  --run 真连核对（**只报告，不改数据**）")
    _say("=" * 78)
    if plan[LABEL_B] is None:
        _say("! B 类跳过：%s" % notes.get(LABEL_B))

    # 先校验落盘位置：指到仓库里就直接退出，**一个请求都不发**
    if args.json and _inside_repo(args.json):
        _say("拒绝写入仓库目录（%s）—— 探测结果不是数据，请落到 %%TEMP%% 或仓库外" % args.json)
        return 1

    try:
        results, summary, unexpected = run_probes(plan, notes, args)
    except KeyboardInterrupt:
        _say("")
        _say("已中断（Ctrl-C）。已经打出来的结果仍然是有效的。")
        return 130

    a_items, _ = _cap(plan[LABEL_A] or [], args.limit)
    b_items, _ = _cap(plan[LABEL_B] or [], args.limit)
    c_items, _ = _cap(plan[LABEL_C] or [], args.limit)
    bound = 2 * len(a_items) + len(b_items) + len(c_items)

    print_summary(summary, plan, notes)
    write_json(args.json, plan, results, summary, notes, bound, args)

    if unexpected:
        _say("")
        _say("! 有 %d 条探测抛了非预期异常，见上（退出码 2）。" % unexpected)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
