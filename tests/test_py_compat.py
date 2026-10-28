# -*- coding: utf-8 -*-
"""Python 版本下限的**编译级**门禁（`DEF-003`）。

为什么需要这个文件
------------------
`pyproject.toml` 里写了 `requires-python = ">=3.10"`，但那只是个**声明** ——
它对源码里能不能出现 3.11+ 语法没有任何约束力。历史情况是：
声明 `>=3.9`，而 `client.py` 里已经用上了 `X | None` 这类 3.10 才支持的
运行期类型语法（`from __future__ import annotations` 只推迟**求值**，
`isinstance(x, A | B)`、`dict[str, int]()` 这种运行期用法照样会炸）。
于是 3.9 用户 `pip install` 成功、一 import 就 `TypeError`。

这个文件把"声明"变成"判据"：
1. 用 `ast.parse(..., feature_version=(3, 10))` 逐个解析库内**所有**
   `.py`（含 `_engine/`、`sections/`），出现更高版本才有的**语法**就红；
2. 断言 `pyproject.toml` 的 `requires-python` 与本文件的判定版本一致
   （防止有人改了声明却忘了这里，或反过来）；
3. 断言 `py.typed` 存在（`Typing :: Typed` classifier 必须有这个文件兜底，
   否则类型信息不会随包分发）。

⚠️ `feature_version` 只管**语法**，管不到"库函数在旧版本里不存在"
（例如 `asyncio.timeout`）。那类问题由 B 批的 CI 矩阵（3.10/3.11/3.12/3.14）
真跑一遍来兜。
"""
from __future__ import annotations

import ast
import os
import re

import pytest

#: 本项目承诺的下限。改这里必须同时改 `pyproject.toml` 的 `requires-python`。
FLOOR = (3, 10)

_PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tdxdata")
_ROOT = os.path.dirname(_PKG)


def _all_sources():
    """库内所有 `.py` 文件（含子包）。测试目录不算 —— 测试不随包分发。"""
    out = []
    for dirpath, _dirnames, filenames in os.walk(_PKG):
        for name in sorted(filenames):
            if name.endswith(".py"):
                out.append(os.path.join(dirpath, name))
    assert out, "一个 .py 都没找到？路径错了：%s" % _PKG
    return sorted(out)


def _rel(path: str) -> str:
    return os.path.relpath(path, _ROOT).replace(os.sep, "/")


@pytest.mark.parametrize("path", _all_sources(), ids=_rel)
def test_source_parses_with_the_declared_floor(path):
    """每个库内文件都必须能被 `FLOOR` 版本的解析器吃下。"""
    with open(path, "rb") as fh:
        src = fh.read()
    try:
        ast.parse(src, filename=path, feature_version=FLOOR)
    except SyntaxError as exc:
        raise AssertionError(
            "%s 用了 Python %d.%d 以上才有的语法（第 %s 行：%s）—— "
            "要么改写，要么把 pyproject 的 requires-python 和本文件的 FLOOR 一起抬上去"
            % (_rel(path), FLOOR[0], FLOOR[1], exc.lineno, exc.msg)) from None


def test_declared_python_floor_matches_this_gate():
    """`pyproject.toml` 的 `requires-python` 必须与本文件的 `FLOOR` 一致。"""
    pyproject = os.path.join(_ROOT, "pyproject.toml")
    text = open(pyproject, encoding="utf-8").read()
    m = re.search(r'^requires-python\s*=\s*"([^"]+)"', text, re.M)
    assert m, "pyproject.toml 里找不到 requires-python"
    decl = m.group(1)
    want = ">=%d.%d" % FLOOR
    assert decl == want, (
        "声明是 %r、本门禁按 %r 判定 —— 两处必须一致（否则门禁守的是一个"
        "已经不存在的事实）" % (decl, want))


def test_py_typed_ships_with_the_package():
    """`Typing :: Typed` classifier 必须有 `py.typed` 兜底。"""
    assert os.path.isfile(os.path.join(_PKG, "py.typed")), (
        "tdxdata/py.typed 不存在：`Typing :: Typed` classifier 会变成空头承诺，"
        "mypy/pyright 会直接忽略本包的类型信息")


def test_importing_the_package_starts_no_threads_and_opens_no_sockets():
    """`import tdxdata` 必须是**纯**的：不起线程、不连网。

    两个历史依据：
    * 曾有 `concurrency`，一个 `import` 就可能带出 `ThreadPoolExecutor` 与
      `threading.local()`；删掉后这条断言防止它被顺手加回来；
    * 本库的头号承诺是"导入不产生任何副作用"（不读配置、不探活、不预建连接），
      所以导入期间任何 socket 创建都是缺陷。

    ⚠️ 必须跑在**子进程**里：本进程早就 import 过 `tdxdata` 了，
    在同一进程里断言"导入时"发生了什么没有意义。
    """
    import subprocess
    import sys

    code = (
        "import socket, sys, threading\n"
        "def _boom(*a, **k):\n"
        "    raise AssertionError('import tdxdata 期间创建了 socket')\n"
        "socket.socket = _boom\n"
        "socket.create_connection = _boom\n"
        "import tdxdata\n"
        "assert tdxdata.__version__, 'missing __version__'\n"
        "alive = [t.name for t in threading.enumerate() if t is not threading.main_thread()]\n"
        "assert not alive, 'import 起了线程: %r' % alive\n"
        "print('OK')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=_ROOT,
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, "子进程失败：\n%s\n%s" % (r.stdout, r.stderr)
    assert "OK" in r.stdout, r.stdout


def test_no_three_point_eleven_only_syntax():
    """3.11+ 独有的语法/内建在本库里一个都不许出现（`DEF-003` 的正面清单）。"""
    #: 只在 3.11+ 才有的**语法节点**（`except*` / `try` 的 `ExceptStar`）。
    banned_nodes = ("TryStar",)
    hits = []
    for path in _all_sources():
        tree = ast.parse(open(path, "rb").read(), filename=path)
        for node in ast.walk(tree):
            if type(node).__name__ in banned_nodes:
                hits.append("%s:%s %s" % (_rel(path), getattr(node, "lineno", "?"),
                                          type(node).__name__))
    assert not hits, "用了 3.11+ 才有的语法：%s" % hits


def test_tokenize_has_no_bom_or_mixed_newlines():
    """源文件不许带 BOM，也不许混用行尾（Windows 上最容易混进来）。"""
    bad = []
    for path in _all_sources():
        raw = open(path, "rb").read()
        if raw.startswith(b"\xef\xbb\xbf"):
            bad.append("%s: 有 UTF-8 BOM" % _rel(path))
        crlf = raw.count(b"\r\n")
        lf = raw.count(b"\n") - crlf
        if crlf and lf:
            bad.append("%s: CRLF %d 行 / LF %d 行 混用" % (_rel(path), crlf, lf))
    assert not bad, "；".join(bad)


def test_every_source_is_valid_utf8_and_has_an_encoding_line():
    """中文注释必须能被独立解码（打包/CI 换 locale 时最容易炸的地方）。"""
    bad = []
    for path in _all_sources():
        try:
            text = open(path, "rb").read().decode("utf-8")
        except UnicodeDecodeError as exc:
            bad.append("%s: 不是合法 UTF-8（%s）" % (_rel(path), exc))
            continue
        # 有非 ASCII 内容的文件必须显式声明编码，别指望 PEP 3120 的默认值
        if any(ord(ch) > 127 for ch in text) and "coding" not in text.split("\n")[0]:
            bad.append("%s: 含中文但首行没有 coding 声明" % _rel(path))
    assert not bad, "；".join(bad)
