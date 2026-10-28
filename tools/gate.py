# -*- coding: utf-8 -*-
r"""tdxdata 质量门禁（入仓版）。

> **本文件是 `F:\TDX_stock_data\_dev\check.py` 的入仓版本。** 外部那份保留为
> 过渡期双跑对照，行为应与本文件逐条一致（10 条门禁：通过 9 / 失败 0 / 跳过 1）。
> 过渡期结束后，外部 `_dev/check.py` 以本文件为准。

用法
----
    python tools/gate.py                    # 默认离线：第 8 条（真机冒烟）SKIP
    python tools/gate.py --online           # 连跑 `python smoke.py`（需 2~4 分钟）
    python tools/gate.py --only 1,2,9       # 只跑指定编号（Lead 抽查用）
    python tools/gate.py --list             # 只列 10 条门禁，不跑

输出约定
--------
* 每条一行：`[序号/10] 名称  PASS|FAIL|SKIP  (耗时)  判据：一句说明`
* **每条判据都必须带基数**（"421/421 个产品" 这类），不许只写"全部通过"
* 有 FAIL 则退出码 1；SKIP 不影响退出码
* **任何一条门禁抛异常都必须计为 FAIL**（不许当成 SKIP、也不许静默吞掉）；
  SKIP 只允许出现在下面的 `SKIP_WHITELIST` 显式白名单里（当前只有第 8 条
  online 冒烟，因 CI 无真机而默认跳过）。
"""
from __future__ import annotations

import argparse
import ast
import collections
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tokenize
import unicodedata

# ── 路径常量（全部从本文件位置反推仓库根，和 cwd 无关）─────────────────────
# 本脚本固定位于 <仓库根>/tools/gate.py，root 即上一级目录。
_HERE = os.path.dirname(os.path.abspath(__file__))


def _resolve_root():
    """以本文件位置定位仓库根：`tools/gate.py` 的上一级就是根。

    兼容：环境变量 `TDXDATA_ROOT` 可显式覆盖（CI 或非常规布局时用）。
    """
    env = os.environ.get('TDXDATA_ROOT')
    if env and os.path.isdir(os.path.join(env, 'tdxdata')):
        return env
    return os.path.dirname(_HERE)


ROOT = _resolve_root()
PKG_DIR = os.path.join(ROOT, "tdxdata")
PRODUCTS_JSON = os.path.join(PKG_DIR, "data", "products.json")
CATALOG_PKG_DIR = os.path.join(PKG_DIR, "_engine", "catalog")
CATALOG_MD = os.environ.get("TDXDATA_CATALOG") or os.path.join(
    ROOT, "接口文档", "spec", "05-接口目录.md")
if not os.path.exists(CATALOG_MD):
    # 重建后的仓库里没有接口目录（已移出到归档）。本脚本同目录的 catalog/
    # 留了一份证据源，所以第 4/5 条仍能跑。
    _cat_alt = os.path.join(_HERE, 'catalog', '05-接口目录.md')
    if os.path.exists(_cat_alt):
        CATALOG_MD = _cat_alt
TESTS_DIR = os.path.join(ROOT, "tests")
SMOKE_PY = os.environ.get("TDXDATA_SMOKE") or os.path.join(ROOT, "smoke.py")
if not os.path.exists(SMOKE_PY):
    _smoke_alt = os.path.join(_HERE, "smoke.py")
    if os.path.exists(_smoke_alt):
        SMOKE_PY = _smoke_alt

EXPECT_PRODUCTS = 421
# `_NOT_IMPL` 读不出来时的兜底清单（契约：这三个方法必须抛 NotSupportedError）
#: 仍然"无通道、必抛 NotSupportedError"的方法。
#: 2026-10-27：`get_universe`(10-25) / `get_quotes`(10-27) / `get_ticks`(10-27 `PBTickEx`) 全部实装，
#: 这份契约清单**已清空**。以后真有做不到的方法再往里加；别为了"让门禁有东西查"把已实装的塞回来。
NO_CHANNEL_METHODS = ()

# 遍历 `.py` 时跳过的目录（名字精确匹配）
EXCLUDE_DIRS = frozenset({
    "__pycache__", "build", "dist", ".git", ".hg", ".svn", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".venv", "venv", "env", "node_modules", ".idea",
})
# 名字以这些后缀结尾的目录也跳过（`tdxdata.egg-info` 之类）
EXCLUDE_DIR_SUFFIX = ("egg-info",)

TIMEOUT_PYTEST = 1200          # 整批的安全网（不是逐文件判据，别当死线）
TIMEOUT_TEST_FILE = 60         # 逐文件跑时的单文件上限（pytest 不可用的情况）
TIMEOUT_SMOKE = 600
TIMEOUT_PIP = 300

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

#: 显式 SKIP 白名单：只有登记在这里的门禁编号允许返回 SKIP。
#: 其他任何门禁返回 SKIP 一律改判 FAIL（不许"静默跳过"）。
SKIP_WHITELIST = {8}           # 8 = 真机冒烟（CI 无真机，默认离线跳过）

#: 命令行允许的 SKIP 理由（--online 关掉冒烟时的合法跳过原因）。
SKIP_REASONS = {
    8: "真机冒烟需实机环境（CI / 默认离线跑都不具备），显式白名单放行",
}

_OPT: dict = {"online": False}


# ─────────────────────────────────────────────────────────────────────────
# 一、可单测的纯函数（`tests/test_gate.py` 只测这一段，不连网）
# ─────────────────────────────────────────────────────────────────────────
def rel(path: str, root: str = ROOT) -> str:
    """绝对路径 → 相对项目根（打印用，路径分隔符统一成 `/`）。"""
    try:
        r = os.path.relpath(path, root)
    except ValueError:                                    # 跨盘符
        r = path
    return r.replace(os.sep, "/")


def iter_py_files(root: str = ROOT):
    """递归列出所有 `.py`，排除 `__pycache__` / `build` / `*egg-info` 等。"""
    out = []
    for cur, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs
                         if d not in EXCLUDE_DIRS
                         and not d.endswith(EXCLUDE_DIR_SUFFIX))
        for name in sorted(files):
            if name.endswith(".py"):
                out.append(os.path.join(cur, name))
    return out


def has_bom(head: bytes) -> bool:
    """前 3 字节是不是 UTF-8 BOM（`EF BB BF`）。"""
    return head[:3] == b"\xef\xbb\xbf"


def read_source(path: str) -> str:
    """按 `utf-8-sig` 读源码（有 BOM 也读得出来；BOM 由第 6 条单独判）。"""
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8-sig")


def find_duplicate_keys(text: str):
    """用 `object_pairs_hook` 找 JSON 里重复的键（`json.load` 会静默吞掉重复键）。

    返回 `[(键, 出现次数)]`，正常数据应为空。
    """
    dups = []

    def hook(pairs):
        counter = collections.Counter(k for k, _ in pairs)
        for key, n in counter.items():
            if n > 1:
                dups.append((key, n))
        return dict(pairs)

    json.loads(text, object_pairs_hook=hook)
    return dups


# 命令名 = 命名空间 + 分隔符 + 方法名。任务书要求三种分隔符都认：
#   `CWServ.ph_xxx`（点）、`HQServ.PBFXT`（点）、`JNLPSE:getHotQues`（冒号）
CMD_NAMESPACES = (
    "CWServ", "HQServ", "DataAggregation", "JNLPSE", "JJSServ",
    "CWSearch", "AppUpdateInfo", "BaseInfo", "TdxHq",
)
_CMD_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_.])([A-Za-z][A-Za-z0-9_]*)[.:]([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)")


def extract_cmd_names(text: str, namespaces=CMD_NAMESPACES):
    """从文档正文里正则抽出命令名集合（`CWServ.xxx` / `HQServ.xxx` / `JNLPSE:xxx`）。"""
    out = set()
    for m in _CMD_TOKEN_RE.finditer(text):
        if m.group(1) in namespaces:
            out.add(m.group(1) + text[m.start(1) + len(m.group(1))] + m.group(2))
    return out


def cmd_token_present(text: str, cmd: str) -> bool:
    """文档里是否出现该命令名（按标识符边界精确匹配，避免 `a_b` 命中 `a_bc`）。"""
    if not cmd:
        return False
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(cmd) + r"(?![A-Za-z0-9_])",
                     text) is not None


# 目录明细表的行形状：`| \`CWServ.ph_xxx\` | 0x1F | \`[...]\` | ✅ 35×7 |`
# ctl 多候选写成 `0x1F/0x20`，这里只认纯单值（多候选算"核不到"，如实计 SKIP）
_CTL_ROW_RE = re.compile(
    r"^\|\s*`([A-Za-z][A-Za-z0-9_]*(?:[.:][A-Za-z0-9_]+)+)`\s*\|"
    r"\s*(0[xX][0-9A-Fa-f]+|\d+)\s*\|", re.M)


def parse_cmd_ctl_table(text: str):
    """从接口目录抽出 `{命令名: {ctl, ...}}`（ctl 统一成 int）。"""
    out: dict = {}
    for m in _CTL_ROW_RE.finditer(text):
        out.setdefault(m.group(1), set()).add(int(m.group(2), 0))
    return out


def compare_ctl(products: dict, ctl_map: dict):
    """比对产品 ctl 与目录 ctl。

    返回 `(可核对数, 命中数, 不符清单, 核不到清单)`；
    不符清单元素 `(产品名, 命令, 产品ctl, 目录ctl集合)`，
    核不到 = 目录里没这条命令的纯数值 ctl。
    """
    checkable = ok = 0
    bad, unchecked = [], []
    for name, spec in products.items():
        cmd, ctl = spec.get("cmd", ""), spec.get("ctl")
        want = ctl_map.get(cmd)
        if not want:
            unchecked.append((name, cmd))
            continue
        checkable += 1
        if ctl in want:
            ok += 1
        else:
            bad.append((name, cmd, ctl, sorted(want)))
    return checkable, ok, bad, unchecked


def _walk_functions(tree, filename: str = ""):
    """遍历模块级函数与类方法，产出 `(函数名, ast 节点)`。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node


def _raises_not_supported(func_node) -> bool:
    """函数体里有没有 `raise ...NotSupportedError(...)`。"""
    for node in ast.walk(func_node):
        if isinstance(node, ast.Raise) and node.exc is not None:
            target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            name = getattr(target, "id", None) or getattr(target, "attr", None)
            if name == "NotSupportedError":
                return True
    return False


def extract_not_impl_names(tree):
    """从 `client.py` 的 `_NOT_IMPL = {...}` 字面量里取方法名。"""
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_NOT_IMPL" for t in node.targets):
            if isinstance(node.value, ast.Dict):
                for key in node.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        names.append(key.value)
    return names


def has_not_impl_literal(tree) -> bool:
    """`client.py` 里有没有 `_NOT_IMPL = {...}` 这个字面量（空字典也算"有"）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_NOT_IMPL" for t in node.targets):
            return isinstance(node.value, ast.Dict)
    return False


def pure_stub_raise(func_node):
    """函数体**只由一条 `raise XxxError(...)` 构成**时返回异常名，否则 None（docstring 不算）。

    用途：反向审计「谁偷偷加了空实现桩」。只白名单式地查"清单里的方法"会漏 ——
    清单清空后就成了空转的门禁（这条规则曾经就这样空转过）。
    """
    body = [b for b in func_node.body
            if not (isinstance(b, ast.Expr)
                    and isinstance(getattr(b, "value", None), ast.Constant)
                    and isinstance(b.value.value, str))]
    if len(body) != 1 or not isinstance(body[0], ast.Raise):
        return None
    exc = body[0].exc
    target = exc.func if isinstance(exc, ast.Call) else exc
    return getattr(target, "id", None) or getattr(target, "attr", None)


def find_empty_returns(tree, filename: str = ""):
    """文本级检出"用空值冒充不可用"：`return []` 与 `Table/Dataset(..., rows=[])`。

    返回 `[(行号, 函数名, 代码)]`。这是**检出**，判据见 `tools/README.md`。

    注意 `rows=[]` 是**全函数体**扫（不只盯 `return`）：写成
    `t = Table(cols=..., rows=[]); return t` 也一样躲不过去。
    """
    hits = []
    for func_name, node in _walk_functions(tree):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.List) and not sub.value.elts:
                hits.append((sub.lineno, func_name, "return []"))
            elif isinstance(sub, ast.Call):
                callee = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
                if callee not in ("Table", "Dataset"):
                    continue
                for kw in sub.keywords:
                    if kw.arg == "rows" and isinstance(kw.value, ast.List) and not kw.value.elts:
                        hits.append((sub.lineno, func_name, "%s(..., rows=[])" % callee))
    return sorted(set(hits))


# tests/ 里一旦出现这些构造，就可能真连服务器。
# 报告 §5.3 点名：`AsyncClient(` 也是"真建连"入口，必须与 `Client(` 同等纳入扫描。
NET_CALL_PATTERNS = (
    ("Client", re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z_][A-Za-z0-9_]*\.)?Client\s*\(")),
    ("Session", re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z_][A-Za-z0-9_]*\.)?Session\s*\(")),
    ("AsyncClient", re.compile(r"(?<![A-Za-z0-9_])AsyncClient\s*\(")),
)
# 白名单：`(相对路径, 命中符号) → 理由`。**是数据，不是逻辑后门**：
# 键必须精确到「文件 + 命中符号」，禁止 `"*"`、禁止只写文件名放行整个文件。
# 每条放行都会在输出里逐条打印 `⚠️ 白名单放行 文件:行号 ← 理由`，并单独计数。
# 新增白名单必须给出"零建连"的同类证据（做法见 tools/README.md §二.2）。
NET_CALL_WHITELIST: dict = {
    ("tests/test_session_idle.py", "Session"):
        "配 _Fake socket + reconnect 打桩，实测零建连（Lead 已核）",
    ("tests/test_aio.py", "Client"):
        "AsyncClient(client=fake) 全部包假客户端；AsyncClient(host=…) 仅构造 Client（A6 已证 Client() 零建连），21 处 2026-10-27 Lead 逐类核验",
    ("tests/test_aio_parity.py", "Client"):
        "AsyncClient(client=FakeClient()) 包假客户端，零建连（Lead 已核）",
    ("tests/test_get_mapped.py", "Client"):
        "AsyncClient(client=_FakeSync()) 包假客户端，零建连（Lead 已核）",
}

_STRING_TOKEN_TYPES = None


def _string_token_types():
    """字符串字面量类 token（`FSTRING_MIDDLE` 只有 3.12+ 才有，动态取）。"""
    global _STRING_TOKEN_TYPES
    if _STRING_TOKEN_TYPES is None:
        types = {tokenize.STRING, tokenize.COMMENT}
        for name in ("FSTRING_MIDDLE", "TSTRING_MIDDLE"):
            if hasattr(tokenize, name):
                types.add(getattr(tokenize, name))
        _STRING_TOKEN_TYPES = frozenset(types)
    return _STRING_TOKEN_TYPES


def mask_strings_and_comments(text: str) -> str:
    """把字符串字面量与注释替换成空格（行号/列号不变），只留"真代码"。

    目的：`print("... 默认构造走的 Client(**kw) ...")` 这种**说明文字**不该算命中，
    而 `S.Session()` 这种**真调用**必须算命中。tokenize 失败（语法错）时原样返回。
    """
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except Exception:                                        # noqa: BLE001
        return text
    mask_types = _string_token_types()
    lines = text.splitlines(keepends=True)
    grid = [list(line) for line in lines]
    for tok in toks:
        if tok.type not in mask_types:
            continue
        (srow, scol), (erow, ecol) = tok.start, tok.end
        for row in range(srow, erow + 1):
            if not (1 <= row <= len(grid)):
                continue
            line = grid[row - 1]
            start = scol if row == srow else 0
            end = ecol if row == erow else len(line)
            for idx in range(start, min(end, len(line))):
                if line[idx] not in "\r\n":
                    line[idx] = " "
    return "".join("".join(line) for line in grid)


def scan_network_calls(path: str, text: str, patterns=NET_CALL_PATTERNS):
    """扫一个测试文件里的 `Client(` / `Session(` / `AsyncClient(` 真调用。

    返回 `[(行号, 类别, 行文本)]`。
    匹配在"去掉字符串与注释"的版本上做（避免说明文字误报），行文本用原文打印。

    注意：`AsyncClient(` 命中时**同时**归入 `Client` 类别 —— 因为
    `x.AsyncClient(` 这类带前缀的写法会被 `Client` 的正则（允许 `.Name` 前缀）
    吃到，而裸 `AsyncClient(` 只被 `AsyncClient` 正则吃到。把
    `AsyncClient` 命中映射到 `Client` 键，白名单/计数口径就只有三类符号：
    `Client` / `Session`（`AsyncClient` 是 `Client` 的一种）。
    """
    masked = mask_strings_and_comments(text).splitlines()
    originals = text.splitlines()
    hits = []
    for idx, mline in enumerate(masked):
        for kind, pat in patterns:
            if pat.search(mline):
                shown = originals[idx] if idx < len(originals) else mline
                hits.append((idx + 1, "Client" if kind == "AsyncClient" else kind,
                             shown.strip()))
    return hits


def split_whitelisted(hits, whitelist=None):
    """按 `(文件, 命中符号)` 把命中拆成 `(放行, 未放行)`。

    元素统一是 `(path, lineno, kind, line, reason|None)`。
    键必须精确匹配 —— 不认 `"*"`、不认只给文件名的键（放行整个文件等于关掉这条门禁）。
    """
    wl = NET_CALL_WHITELIST if whitelist is None else whitelist
    allowed, blocked = [], []
    for path, lineno, kind, line in hits:
        reason = wl.get((path, kind))
        item = (path, lineno, kind, line, reason)
        (allowed if reason else blocked).append(item)
    return allowed, blocked


_PYTEST_COUNT_RE = re.compile(
    r"(\d+)\s+(passed|failed|error|errors|skipped|deselected|xfailed|xpassed|warnings?)\b")
_PYTEST_WORDS = ("passed", "failed", "error", "skipped", "deselected", "xfailed", "xpassed")


def parse_pytest_summary(output: str):
    """解析 `pytest -q` 的末行统计（如 `1 failed, 55 passed in 0.94s`）。

    返回 `{"passed":55, "failed":1, "error":0, ..., "ok":bool, "found":bool}`。
    `ok` 的判据：找到统计行 且 failed==0 且 error==0 且 passed>0（"no tests ran" 不算绿）。
    """
    counts = {w: 0 for w in _PYTEST_WORDS}
    counts.update(ok=False, found=False)
    for raw in reversed([ln for ln in output.splitlines() if ln.strip()]):
        line = raw.strip(" \t=*")
        hits = _PYTEST_COUNT_RE.findall(line)
        if not hits:
            continue
        for num, word in hits:
            key = "error" if word.startswith("error") else word
            if key in counts:
                counts[key] += int(num)
                counts["found"] = True
        break
    counts["ok"] = bool(counts["found"] and counts["failed"] == 0
                        and counts["error"] == 0 and counts["passed"] > 0)
    return counts


_SMOKE_FAIL_RE = re.compile(r"失败\s*(\d+)\s*项")


def parse_smoke_fail_count(output: str):
    """从 smoke.py 输出里抠 `===== 失败 N 项 =====` 的 N；抠不到返回 None。"""
    m = _SMOKE_FAIL_RE.search(output or "")
    return int(m.group(1)) if m else None


def classify_empty_return(func_name: str, no_channel_names) -> str:
    """给 `return []` 命中定性：`block` 必须 FAIL，`review` 交人工判据。

    `block`：声明过的无通道方法，或任何 `get_*`（对外取数入口）—— 这两类返回空值
    就是"用空冒充不可用"，是红线 3 明令禁止的。
    `review`：私有 helper / 解析器 / 序列计算，空输入返回空序列是正确语义，
    逐条列出来交人工判据（见 tools/README.md §二.3）。
    """
    if func_name in set(no_channel_names):
        return "block"
    if func_name.startswith("get_"):
        return "block"
    return "review"


# ─────────────────────────────────────────────────────────────────────────
# 二、外部命令封装
# ─────────────────────────────────────────────────────────────────────────
def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def run_cmd(argv, cwd: str = ROOT, timeout: int = 120, env_extra=None, stdin_text=None):
    """跑子进程，返回 `(returncode, 合并输出)`。**不抛异常**（超时/缺解释器都算结果）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONPATH", None)
    env["PYTHONUTF8"] = "1"
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(
            argv, cwd=cwd, env=env, timeout=timeout,
            input=stdin_text.encode("utf-8") if stdin_text else None,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
    except FileNotFoundError as exc:
        return 127, "命令不存在：%s（%s）" % (argv[0], exc)
    except subprocess.TimeoutExpired:
        return -1, "子进程超时（%ds）：%s" % (timeout, " ".join(argv))
    return proc.returncode, _decode(proc.stdout or b"")


def py_cmd(*args):
    """当前解释器 + 参数（保证和 gate.py 同一个 python）。"""
    return [sys.executable, *args]


# ─────────────────────────────────────────────────────────────────────────
# 三、10 条门禁
# ─────────────────────────────────────────────────────────────────────────
def gate_01_syntax():
    files = iter_py_files()
    bad = []
    for path in files:
        try:
            ast.parse(read_source(path), filename=path)
        except SyntaxError as exc:
            bad.append("%s:%s %s" % (rel(path), exc.lineno, exc.msg))
        except Exception as exc:                             # noqa: BLE001
            bad.append("%s 读取失败 %s: %s" % (rel(path), type(exc).__name__, exc))
    notes = ["跳过目录：%s / *%s" % ("/".join(sorted(EXCLUDE_DIRS)), "/*".join(EXCLUDE_DIR_SUFFIX))]
    if bad:
        notes += ["解析失败："] + ["  " + b for b in bad[:20]]
        return FAIL, "ast.parse 失败 %d/%d 个 .py 文件" % (len(bad), len(files)), notes
    return PASS, "ast.parse 全过：%d/%d 个 .py 文件" % (len(files), len(files)), notes


def gate_02_import_export():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    import tdxdata                                          # noqa: PLC0415

    got_dir = os.path.dirname(os.path.abspath(tdxdata.__file__))
    notes = ["包路径：%s" % rel(tdxdata.__file__)]
    if os.path.normcase(got_dir) != os.path.normcase(PKG_DIR):
        notes.append("期望包目录：%s" % rel(PKG_DIR))
        return FAIL, "import 到的不是本目录的包（%s）" % rel(tdxdata.__file__), notes

    exports = list(getattr(tdxdata, "__all__", []) or [])
    if not exports:
        return FAIL, "tdxdata.__all__ 缺失或为空（基数 0 项）", notes
    missing = [n for n in exports if not hasattr(tdxdata, n)]
    version = getattr(tdxdata, "__version__", None)
    if version is None:
        notes.append("`__version__` 不存在")
    if missing:
        notes.append("`__all__` 里取不到：%s" % ", ".join(missing[:20]))
    ok = not missing and version is not None
    summary = ("import tdxdata 成功；__all__ %d/%d 项可 getattr；__version__=%r"
               % (len(exports) - len(missing), len(exports), version))
    return (PASS if ok else FAIL), summary, notes


def _load_products_json():
    with open(PRODUCTS_JSON, "rb") as fh:
        text = fh.read().decode("utf-8-sig")
    return json.loads(text), text


def gate_03_products():
    payload, raw_text = _load_products_json()
    products = payload.get("products", {})
    pb_products = payload.get("pb_products", {})

    notes = ["products.json 原始键：products=%d、pb_products=%d（合计 %d）"
             % (len(products), len(pb_products), len(products) + len(pb_products))]
    dups = find_duplicate_keys(raw_text)
    if dups:
        notes.append("JSON 重复键（json.load 会静默吞）：%s" % dups[:20])

    sys.path.insert(0, ROOT)
    from tdxdata import Client                              # noqa: PLC0415
    client = Client()
    try:
        listed = client.products()
    finally:
        client.close()
    names = [p.name for p in listed]
    dup_names = [n for n, c in collections.Counter(names).items() if c > 1]
    if dup_names:
        notes.append("products() 里重名：%s" % dup_names[:20])

    ok = len(listed) == EXPECT_PRODUCTS and not dups and not dup_names
    summary = ("Client().products() 返回 %d/%d 个产品；重名 %d 个；JSON 重复键 %d 处"
               % (len(listed), EXPECT_PRODUCTS, len(dup_names), len(dups)))
    return (PASS if ok else FAIL), summary, notes


def gate_04_cmd_ledger():
    if not os.path.exists(CATALOG_MD):
        return SKIP, ("接口目录不在本仓库（%s），该规则跳过。"
                      "需要跑就把接口目录放回原位，或用 TDXDATA_CATALOG 指过去。"
                      % rel(CATALOG_MD)), []
    with open(CATALOG_MD, "rb") as fh:
        text = fh.read().decode("utf-8-sig")

    payload, _ = _load_products_json()
    products = payload.get("products", {})
    pb_products = payload.get("pb_products", {})
    all_cmds = [(n, s.get("cmd", "")) for n, s in products.items()]
    distinct = sorted({c for _, c in all_cmds if c})
    text_cmds = extract_cmd_names(text)

    miss = [c for c in distinct if not cmd_token_present(text, c)]
    hit = len(distinct) - len(miss)

    # pb_products 只作参考（任务书第 4 条只要求 products）
    pb_distinct = sorted({s.get("cmd", "") for s in pb_products.values() if s.get("cmd")})
    pb_miss = [c for c in pb_distinct if not cmd_token_present(text, c)]

    notes = [
        "证据源：%s（任务书写的 catalog/iface_catalog.json 本仓不存在，属口径差异）" % rel(CATALOG_MD),
        "目录正则认出命令名 %d 条（认 CWServ. / HQServ. / JNLPSE: 三种分隔符）" % len(text_cmds),
        "参考（不计入判据）：pb_products %d 个去重 cmd，命中 %d/%d"
        % (len(pb_distinct), len(pb_distinct) - len(pb_miss), len(pb_distinct)),
    ]
    if miss:
        notes.append("未命中清单（前 20 条，共 %d 条）：" % len(miss))
        notes += ["  " + c for c in miss[:20]]
    summary = ("产品 %d 个 / 去重 cmd %d 个：命中 %d/%d，未命中 %d"
               % (len(all_cmds), len(distinct), hit, len(distinct), len(miss)))
    return (PASS if not miss else FAIL), summary, notes


def gate_05_ctl_consistency():
    if not os.path.exists(CATALOG_MD):
        return SKIP, ("接口目录不在本仓库（%s），该规则跳过。"
                      "需要跑就把接口目录放回原位，或用 TDXDATA_CATALOG 指过去。"
                      % rel(CATALOG_MD)), []
    with open(CATALOG_MD, "rb") as fh:
        text = fh.read().decode("utf-8-sig")
    ctl_map = parse_cmd_ctl_table(text)

    payload, _ = _load_products_json()
    products = payload.get("products", {})
    checkable, ok, bad, unchecked = compare_ctl(products, ctl_map)

    notes = ["目录里带纯数值 ctl 的命令 %d 条（ctl 多候选/空的算'核不到'）" % len(ctl_map)]
    if bad:
        notes.append("ctl 不符（前 20 条，共 %d 条）：" % len(bad))
        notes += ["  %s: %s 产品 ctl=%s 目录 ctl=%s" % (n, c, p, d) for n, c, p, d in bad[:20]]
    if unchecked:
        notes.append("核不到（前 20 条，共 %d 条）：" % len(unchecked))
        notes += ["  %s: %s" % (n, c) for n, c in unchecked[:20]]

    total = len(products)
    summary = "可核对 %d/%d，一致 %d，不符 %d，核不到 %d" % (
        checkable, total, ok, len(bad), len(unchecked))
    if bad:
        return FAIL, summary, notes
    if checkable < total:
        notes.append("口径：核不到的按任务书判 SKIP（不假绿）")
        return SKIP, summary, notes
    return PASS, summary, notes


def gate_06_encoding():
    files = iter_py_files()
    bom, undecodable = [], []
    for path in files:
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            undecodable.append("%s 读取失败 %s" % (rel(path), exc))
            continue
        if has_bom(raw[:3]):
            bom.append(rel(path))
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            undecodable.append("%s 位置 %s: %s" % (rel(path), exc.start, exc.reason))
    notes = ["UTF-8 BOM 判据：前 3 字节 == EF BB BF；解码判据：strict utf-8 不抛错"]
    if bom:
        notes.append("带 BOM（前 20 个，共 %d 个）：%s" % (len(bom), bom[:20]))
    if undecodable:
        notes.append("解码失败（前 20 个，共 %d 个）：" % len(undecodable))
        notes += ["  " + u for u in undecodable[:20]]
    ok = not bom and not undecodable
    summary = "%d/%d 个 .py 文件 UTF-8 无 BOM（BOM %d 个、解码失败 %d 个）" % (
        len(files) - len(set(bom) | set(undecodable)), len(files), len(bom), len(undecodable))
    return (PASS if ok else FAIL), summary, notes


def _test_files():
    out = []
    for cur, dirs, files in os.walk(TESTS_DIR):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
        for name in sorted(files):
            if name.startswith("test_") and name.endswith(".py"):
                out.append(os.path.join(cur, name))
    return out


def _pytest_available():
    code, out = run_cmd(py_cmd("-m", "pytest", "--version"), timeout=60)
    return code == 0, out.strip().splitlines()[0] if out.strip() else ""


def _scan_tests_network(files):
    hits = []
    for path in files:
        text = read_source(path)
        for lineno, kind, line in scan_network_calls(path, text):
            hits.append((rel(path), lineno, kind, line))
    return hits


def gate_07_unit_tests():
    files = _test_files()                                   # 动态取，不写死清单
    notes = ["纳入的测试文件（动态 glob tests/test_*.py，共 %d 个）：%s"
             % (len(files), ", ".join(rel(p) for p in files) or "（无）")]
    if not files:
        notes.append("目录：%s" % rel(TESTS_DIR))
        return FAIL, "tests/ 下 test_*.py 0 个（基数 0，不能算绿）", notes

    available, pytest_ver = _pytest_available()
    if available:
        code, out = run_cmd(py_cmd("-m", "pytest", "tests", "-q", "-m", "not online",
                                   "-p", "no:cacheprovider", "--tb=short"),
                            timeout=TIMEOUT_PYTEST)
        stat = parse_pytest_summary(out)
        runner = "pytest %s" % pytest_ver.replace("pytest ", "")
        ok_tests = (code == 0 and stat["ok"])
        summary = ("%s：%d 个测试文件、passed %d / failed %d / error %d / skipped %d / deselected %d"
                   % (runner, len(files), stat["passed"], stat["failed"],
                      stat["error"], stat["skipped"], stat["deselected"]))
        if not stat["found"]:
            notes.append("没解析到 pytest 统计行（末尾输出）：")
            notes += ["  " + ln for ln in out.strip().splitlines()[-8:]]
        if stat["failed"] or stat["error"]:
            keep = [ln for ln in out.splitlines()
                    if ln.startswith("FAILED") or ln.startswith("ERROR") or " - " in ln]
            notes.append("失败/错误行（至多 15 行）：")
            notes += ["  " + ln for ln in keep[:15]] or ["  " + ln for ln in out.strip().splitlines()[-10:]]
    else:
        runner = "逐个 python tests/test_x.py（pytest 不可用：%s）" % pytest_ver
        passed = failed = 0
        for path in files:
            code, out = run_cmd(py_cmd(path), timeout=TIMEOUT_TEST_FILE)
            if code == 0:
                passed += 1
            else:
                failed += 1
                notes.append("FAIL %s（退出码 %s）" % (rel(path), code))
                notes += ["  " + ln for ln in out.strip().splitlines()[-6:]]
        ok_tests = failed == 0
        summary = "%s：%d 个测试文件、通过 %d / 失败 %d" % (runner, len(files), passed, failed)

    # 子检查：tests/ 不许真连服务器
    # 统计口径：命中 N 处 = 白名单放行 X + 未放行 Y，**只对 Y 判 FAIL**（不许写成"命中 0 处"）
    # 扫描符号：`Client(` / `Session(` / `AsyncClient(`（报告 §5.3 补入，AsyncClient 归 Client 类）
    hits = _scan_tests_network(files)
    allowed, blocked = split_whitelisted(hits)
    notes.append("离线扫描：动态取 %d 个 test_*.py，`Client(`/`Session(`/`AsyncClient(` 命中 %d 处 "
                 "= 白名单放行 %d + 未放行 %d（白名单 %d 条）"
                 % (len(files), len(hits), len(allowed), len(blocked), len(NET_CALL_WHITELIST)))
    for path_rel, lineno, kind, line, reason in allowed:
        notes.append("  ⚠️ 白名单放行 %s:%d [%s] ← %s" % (path_rel, lineno, kind, reason))
        notes.append("     该行：%s" % line[:110])
    if blocked:
        notes.append("  ❌ 未放行（只对这一档判 FAIL，逐条列出）：")
        for path_rel, lineno, kind, line, _reason in blocked:
            notes.append("  %s:%d [%s] %s" % (path_rel, lineno, kind, line[:110]))

    ok = ok_tests and not blocked
    summary += "；离线扫描命中 %d 处 = 白名单放行 %d + 未放行 %d" % (
        len(hits), len(allowed), len(blocked))
    return (PASS if ok else FAIL), summary, notes


def gate_08_smoke():
    if not _OPT.get("online"):
        return SKIP, ("默认离线跳过：判据 = `python smoke.py` 输出含「失败 0 项」且退出码 0"
                      "（基数 1 个冒烟脚本，需真机 2~4 分钟；加 --online 才跑）"
                      "【显式 SKIP 白名单：%s】" % SKIP_REASONS[8]), []
    if not os.path.exists(SMOKE_PY):
        return SKIP, ("找不到 smoke.py（%s），该规则跳过。"
                      "把 smoke.py 放到仓库根或本脚本同目录即可。" % rel(SMOKE_PY)), []
    started = time.time()
    code, out = run_cmd(py_cmd(SMOKE_PY), timeout=TIMEOUT_SMOKE)
    fails = parse_smoke_fail_count(out)
    ok = code == 0 and fails == 0
    notes = ["退出码 %s，耗时 %.1fs" % (code, time.time() - started)]
    if fails is None:
        notes.append("没抠到「失败 N 项」行，末尾输出：")
        notes += ["  " + ln for ln in out.strip().splitlines()[-10:]]
    else:
        bad_lines = [ln for ln in out.splitlines() if "❌" in ln or "⚠️" in ln]
        if bad_lines:
            notes += ["失败项（至多 20 行）："] + ["  " + ln.strip() for ln in bad_lines[:20]]
    summary = "smoke.py 退出码 %s，「失败 %s 项」（基数 1 个冒烟脚本）" % (
        code, "?" if fails is None else fails)
    return (PASS if ok else FAIL), summary, notes


def gate_09_no_channel_raises():
    client_py = os.path.join(PKG_DIR, "client.py")
    notes = []
    if not os.path.exists(client_py):
        return FAIL, "client.py 不存在：%s（基数 0）" % rel(client_py), []
    tree = ast.parse(read_source(client_py), filename=client_py)

    # ── 反向审计（2026-10-27 改）─────────────────────────────────────
    # 旧写法 `names = extract_not_impl_names(tree) or list(NO_CHANNEL_METHODS)`：
    # 一旦 `_NOT_IMPL` 与 `NO_CHANNEL_METHODS` 都为空，`names = []` → 循环一次都不跑
    # → 这条门禁**永远绿**。三个方法全实装后它就真的空转了，所以改成：
    #   A. 库自己的声明 `_NOT_IMPL` 必须是**空**的（"没有做不到的方法"要写进代码里）；
    #   B. 逃生舱 `NO_CHANNEL_METHODS` 登记的方法必须真的抛 `NotSupportedError`；
    #      非空时**必须打印出来**，不许静默；
    #   C. 扫 `Client` 的**所有公开方法**，不许有"函数体只有一条 raise NotSupportedError"的桩
    #      —— 谁偷偷加桩，门禁立刻红。
    hatch = list(NO_CHANNEL_METHODS)
    declared = extract_not_impl_names(tree)
    if not has_not_impl_literal(tree):
        notes.append("⚠️ client.py 里没有 `_NOT_IMPL = {...}` 字面量（按空处理）")
    names = sorted(set(declared) | set(hatch))

    # Client 方法表
    methods = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Client":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods[item.name] = item
    missing, not_raising = [], []
    for name in names:
        fn = methods.get(name)
        if fn is None:
            missing.append(name)
            continue
        if not _raises_not_supported(fn):
            not_raising.append("%s（第 %d 行起，体内没有 raise NotSupportedError）"
                               % (name, fn.lineno))

    # 整个 tdxdata/ 里"空值冒充不可用"的文本级检出
    scanned = 0
    empty_hits = []
    for path in iter_py_files(PKG_DIR):
        pkg_tree = ast.parse(read_source(path), filename=path)
        for name, _node in _walk_functions(pkg_tree):
            scanned += 1
        for lineno, func_name, snippet in find_empty_returns(pkg_tree, path):
            empty_hits.append((rel(path), lineno, func_name, snippet,
                               classify_empty_return(func_name, names)))

    blocked = [h for h in empty_hits if h[4] == "block"]
    review = [h for h in empty_hits if h[4] == "review"]

    # A. 库声明必须为空
    if declared:
        notes.append("❌ `_NOT_IMPL` 非空：%s —— 库自己声明了做不到的方法。"
                     "若确实做不到，请从 `_NOT_IMPL` 里删掉、改登记到 gate.py 的 "
                     "`NO_CHANNEL_METHODS` 并写明原因" % ", ".join(declared))
    else:
        notes.append("✅ `_NOT_IMPL` 为空（库声明：没有做不到的方法）")

    # B. 逃生舱：登记了就必须真的抛
    if hatch:
        notes.append("⚠️ 逃生舱 `NO_CHANNEL_METHODS` 非空，登记 %d 个：%s"
                     % (len(hatch), ", ".join(hatch)))
    else:
        notes.append("逃生舱 `NO_CHANNEL_METHODS` 为空（当前无登记项）")

    # C. 反向审计：公开方法不许是纯抛错桩
    stubs = []
    for name, fn in sorted(methods.items()):
        if name.startswith("_"):
            continue
        err = pure_stub_raise(fn)
        if err == "NotSupportedError" and name not in hatch:
            stubs.append("%s（第 %d 行起，函数体只有一条 raise NotSupportedError）"
                         % (name, fn.lineno))
    public_n = sum(1 for n in methods if not n.startswith("_"))
    notes.append("Client 类上共 %d 个方法（公开 %d 个）" % (len(methods), public_n))
    if missing:
        notes.append("Client 上找不到这些方法：%s" % ", ".join(missing))
    if not_raising:
        notes.append("逃生舱里这些没抛 NotSupportedError：")
        notes += ["  " + x for x in not_raising]
    if stubs:
        notes.append("❌ 发现纯抛错桩（没登记逃生舱）：")
        notes += ["  " + x for x in stubs]
    notes.append("空值检出：扫 %d 个函数，命中 `return []` / `Table(rows=[])` %d 处"
                 "（阻断 %d、交人工判据 %d）" % (scanned, len(empty_hits), len(blocked), len(review)))
    for path_rel, lineno, func_name, snippet, kind in blocked[:20]:
        notes.append("  阻断 %s:%d def %s → %s" % (path_rel, lineno, func_name, snippet))
    for path_rel, lineno, func_name, snippet, kind in review[:20]:
        notes.append("  人工判据 %s:%d def %s → %s（口径见 tools/README.md）"
                     % (path_rel, lineno, func_name, snippet))

    if declared or missing or not_raising or stubs or blocked:
        return FAIL, ("`_NOT_IMPL` %d 项；逃生舱 %d 项（缺 %d、没抛 %d）；"
                      "纯抛错桩 %d 个；空值阻断 %d 处"
                      % (len(declared), len(hatch), len(missing), len(not_raising),
                         len(stubs), len(blocked))), notes
    summary = ("反向审计：%d 个公开方法无纯抛错桩；`_NOT_IMPL` 为空；逃生舱 %d 项；"
               "扫 %d 个函数，空值 %d 处全部为人工判据档"
               % (public_n, len(hatch), scanned, len(empty_hits)))
    return PASS, summary, notes


_PROBE_SCRIPT = r'''
import importlib, json, os, sys
target = os.path.abspath(sys.argv[1])
sys.path.insert(0, target)
res = {"target": target}
import tdxdata
res["file"] = os.path.abspath(tdxdata.__file__)
res["under_target"] = res["file"].startswith(target + os.sep)
res["version"] = getattr(tdxdata, "__version__", None)
client = tdxdata.Client()
try:
    res["products"] = len(client.products())
except Exception as exc:
    res["products"] = "%s: %s" % (type(exc).__name__, exc)
finally:
    try:
        client.close()
    except Exception:
        pass
sub = {}
for mod in ("tdxdata.sections", "tdxdata._engine"):
    try:
        importlib.import_module(mod)
        sub[mod] = True
    except Exception as exc:
        sub[mod] = "%s: %s" % (type(exc).__name__, exc)
res["subpkgs"] = sub
pkg = os.path.join(target, "tdxdata")
res["products_json"] = os.path.exists(os.path.join(pkg, "data", "products.json"))
cat = os.path.join(pkg, "_engine", "catalog")
res["catalog"] = sorted(f for f in os.listdir(cat) if f.endswith(".json")) if os.path.isdir(cat) else []
print("PROBE_JSON " + json.dumps(res, ensure_ascii=False))
'''


def _stage_sdist_copy(dst_root: str) -> None:
    """把 sdist 需要的文件抄到临时目录（避免 build/、egg-info 污染项目根）。"""
    os.makedirs(dst_root, exist_ok=True)
    shutil.copytree(PKG_DIR, os.path.join(dst_root, "tdxdata"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    for name in ("pyproject.toml", "README.md"):
        src = os.path.join(ROOT, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dst_root, name))


def gate_10_packaging():
    tmp = tempfile.mkdtemp(prefix="tdxdata_pkgchk_")
    notes = ["临时目录：%s（用完即删）" % tmp]
    try:
        src = os.path.join(tmp, "src")
        site = os.path.join(tmp, "site")
        _stage_sdist_copy(src)
        code, out = run_cmd(
            py_cmd("-m", "pip", "install", "--target", site, "--no-deps",
                   "--no-build-isolation", "--quiet", src),
            cwd=tmp, timeout=TIMEOUT_PIP)
        if code != 0:
            notes.append("pip install 退出码 %s，末尾输出：" % code)
            notes += ["  " + ln for ln in out.strip().splitlines()[-12:]]
            return FAIL, "pip install --target 失败（退出码 %s）" % code, notes

        probe = run_cmd(py_cmd("-c", _PROBE_SCRIPT, site), cwd=tmp, timeout=180,
                        env_extra={"PYTHONPATH": ""})
        pcode, pout = probe
        payload = None
        for line in pout.splitlines():
            if line.startswith("PROBE_JSON "):
                payload = json.loads(line[len("PROBE_JSON "):])
        if payload is None:
            notes.append("子进程没有输出 PROBE_JSON（退出码 %s），末尾输出：" % pcode)
            notes += ["  " + ln for ln in pout.strip().splitlines()[-12:]]
            return FAIL, "打包后子进程验证没拿到结果", notes

        problems = []
        if not payload.get("under_target"):
            problems.append("`tdxdata.__file__` 不在临时站点目录：%s" % payload.get("file"))
        if payload.get("products") != EXPECT_PRODUCTS:
            problems.append("products() = %r，期望 %d" % (payload.get("products"), EXPECT_PRODUCTS))
        for mod, got in (payload.get("subpkgs") or {}).items():
            if got is not True:
                problems.append("子包 %s 不能 import：%s" % (mod, got))
        if not payload.get("products_json"):
            problems.append("包内缺 data/products.json")
        cat = payload.get("catalog") or []
        if not cat:
            problems.append("包内缺 _engine/catalog/*.json")

        notes.append("子进程：__file__=%s" % payload.get("file"))
        notes.append("子进程：products()=%r、__version__=%r"
                     % (payload.get("products"), payload.get("version")))
        notes.append("子进程：子包 %r；包内 data/products.json=%s；catalog=%s"
                     % (payload.get("subpkgs"), payload.get("products_json"), cat))
        if problems:
            notes += ["问题：" + p for p in problems]
        summary = ("pip install --target 后 import 成功：products %r/%d、子包 2 个、catalog %d 个 json"
                   % (payload.get("products"), EXPECT_PRODUCTS, len(cat)))
        return (PASS if not problems else FAIL), summary, notes
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


GATES = (
    (1, "语法", gate_01_syntax),
    (2, "导入与导出", gate_02_import_export),
    (3, "产品", gate_03_products),
    (4, "cmd 台账", gate_04_cmd_ledger),
    (5, "ctl 一致性", gate_05_ctl_consistency),
    (6, "编码", gate_06_encoding),
    (7, "单元测试（离线）", gate_07_unit_tests),
    (8, "真机冒烟", gate_08_smoke),
    (9, "空实现必须抛错", gate_09_no_channel_raises),
    (10, "打包完整性", gate_10_packaging),
)

CRITERIA = {
    1: "递归所有 .py（排除 __pycache__/build/*egg-info）ast.parse 全过",
    2: "import tdxdata 拿到本目录的包；__all__ 每项可 getattr；__version__ 存在",
    3: "products() 长度 == 421；产品名/JSON 键无重复",
    4: "所有产品 cmd 能在 05-接口目录.md 里命中（认 CWServ./HQServ./JNLPSE: 三种分隔符）",
    5: "产品 ctl 与目录 ctl 一致；核不到的如实报「可核对/总数」并判 SKIP",
    6: "所有 .py 是 UTF-8 且无 BOM（前 3 字节非 EF BB BF），strict 解码不报错",
    7: "tests/ 全绿且离线：pytest -m 'not online' 或逐文件跑；test_*.py 不得出现 Client(/Session(/AsyncClient(",
    8: "python smoke.py 退出码 0 且输出含「失败 0 项」（默认 SKIP，--online 才跑）",
    9: "无通道方法源码体必须 raise NotSupportedError；空值冒充不可用要文本级检出",
    10: "pip install --target %TEMP% 后子进程 import：__file__ 在临时目录、products 421、子包可导入、数据文件在包内",
}


# ─────────────────────────────────────────────────────────────────────────
# 四、输出与入口
# ─────────────────────────────────────────────────────────────────────────
def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


_COLOR = {"PASS": "\033[32m", "FAIL": "\033[31m", "SKIP": "\033[33m"}
_RESET = "\033[0m"


def _tag(status: str, color: bool) -> str:
    if not color:
        return status
    return "%s%s%s" % (_COLOR.get(status, ""), status, _RESET)


def run_gate(no, title, fn):
    """跑一条门禁，统一收敛成 `(status, summary, notes)`。

    门禁自身抛异常 → FAIL（不许吞、不许当 SKIP）；返回非法状态 → FAIL；
    返回 SKIP 但编号不在 `SKIP_WHITELIST` → FAIL（"门禁不许被静默跳过"）。
    """
    started = time.time()
    try:
        status, summary, notes = fn()
    except Exception as exc:                                 # noqa: BLE001 门禁自己崩 → FAIL
        import traceback
        status, summary = FAIL, "门禁自身抛异常：%s: %s" % (type(exc).__name__, exc)
        notes = [ln.rstrip() for ln in traceback.format_exc().splitlines()[-8:]]
    if status not in (PASS, FAIL, SKIP):
        status, summary = FAIL, "门禁返回了非法状态 %r" % (status,)
    elif status == SKIP and no not in SKIP_WHITELIST:
        status, summary = FAIL, ("门禁返回 SKIP 但不在显式 SKIP 白名单（编号 %d 不在 %s）—— 不许静默跳过"
                                 % (no, sorted(SKIP_WHITELIST)))
    cost = time.time() - started
    return status, summary, notes, cost


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="tdxdata 质量门禁（默认离线；--online 才跑真机冒烟）")
    parser.add_argument("--online", action="store_true",
                        help="跑第 8 条真机冒烟（python smoke.py，约 2~4 分钟）")
    parser.add_argument("--only", default="",
                        help="只跑指定编号，如 --only 1,2,9（默认全跑）")
    parser.add_argument("--list", action="store_true", help="只列 10 条门禁与判据")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:                                    # noqa: BLE001
            pass
    color = bool(sys.stdout.isatty()) and os.environ.get("NO_COLOR") is None

    if args.list:
        print("tdxdata 质量门禁（%d 条）" % len(GATES))
        for no, title, _fn in GATES:
            print("  %2d. %s  %s" % (no, _pad(title, 18), CRITERIA[no]))
        return 0

    _OPT["online"] = args.online
    wanted = set()
    if args.only.strip():
        for piece in re.split(r"[,\s]+", args.only.strip()):
            if not piece:
                continue
            if not piece.isdigit() or not (1 <= int(piece) <= len(GATES)):
                print("--only 只接受 1..%d 的编号，收到 %r" % (len(GATES), piece))
                return 2
            wanted.add(int(piece))
    selected = [g for g in GATES if not wanted or g[0] in wanted]

    print("=" * 100)
    print("tdxdata 质量门禁　项目根 %s" % ROOT)
    print("模式：%s　运行 %d/%d 条　python %s"
          % ("联网（含真机冒烟）" if args.online else "离线（第 8 条跳过）",
             len(selected), len(GATES), sys.version.split()[0]))
    print("=" * 100)

    counts = {PASS: 0, FAIL: 0, SKIP: 0}
    failed_titles = []
    for no, title, fn in selected:
        status, summary, notes, cost = run_gate(no, title, fn)
        counts[status] += 1
        if status == FAIL:
            failed_titles.append("%d.%s" % (no, title))
        head = "[%2d/%d] %s" % (no, len(GATES), _pad(title, 18))
        print("%s %s (%5.1fs) 判据：%s" % (head, _tag(status, color), cost, summary))
        print("         依据：%s" % CRITERIA[no])
        for line in notes or []:
            print("         " + line)
        sys.stdout.flush()

    print("-" * 100)
    print("汇总：通过 %d / 失败 %d / 跳过 %d（共 %d 条）"
          % (counts[PASS], counts[FAIL], counts[SKIP], len(selected)))
    if counts[FAIL]:
        print("失败门禁：%s" % "，".join(failed_titles))
        print("结论：门禁未全绿，退出码 1")
        return 1
    print("结论：全绿（SKIP 仅限显式白名单 %s，不计失败）" % sorted(SKIP_WHITELIST))
    return 0


if __name__ == "__main__":
    sys.exit(main())
