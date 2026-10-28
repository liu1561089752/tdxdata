# -*- coding: utf-8 -*-
r"""ENG-007 版本单源的守卫测试（离线双路对账）。

口径（写清版）
--------------
`__version__` 的**单一事实源**是 `tdxdata/_version.py`。两路读数：

  · **import 路**：`import tdxdata; tdxdata.__version__`（运行时读数）；
  · **pyproject 路**：`pyproject.toml` 声明 `dynamic = ["version"]` 且
    `[tool.setuptools.dynamic] version = {attr = "tdxdata._version.__version__"}`
    —— 构建时 setuptools 会 `import tdxdata._version` 取值，所以**对账的
    判据是"pyproject 的 dynamic 配置指向的正是事实源文件"**（文本解析断言），
    而不是去跑一次 pip install（那是 test_packaging.py 的职责，两处互为补充）。

外加三道闸：__init__ 不许再写死字面量（双写回归防）、0.3.0 语义化版本
（ARCH-002 破坏性切换）、_version.py 必须只有一行赋值（不许长出第二事实源）。

红线：`tests/test_*.py` 不许出现 `Client(` / `Session(` 字面量。
"""
from __future__ import annotations

import io
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                        # noqa: BLE001
    pass

import tdxdata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _source_version() -> str:
    """事实源读数：tdxdata/_version.py 的 `__version__`。"""
    m = re.search(r'^__version__\s*=\s*"([^"]+)"',
                  _read("tdxdata/_version.py"), re.M)
    assert m, "tdxdata/_version.py 里找不到 __version__"
    return m.group(1)


def test_import_path_reads_the_source():
    """import 路：`tdxdata.__version__` == _version.py 的值。"""
    assert tdxdata.__version__ == _source_version(), (
        "import 读数 %r ≠ 事实源 %r —— __init__.py 的导入链被改坏了"
        % (tdxdata.__version__, _source_version()))


def test_pyproject_dynamic_points_at_the_source():
    """pyproject 路：dynamic version 的 attr 必须指向 tdxdata._version.__version__。"""
    py = _read("pyproject.toml")
    assert re.search(r'^dynamic\s*=\s*\[\s*"version"\s*\]', py, re.M), (
        "pyproject.toml 没有 dynamic = [\"version\"] —— 版本又写死回去了？")
    assert re.search(r'attr\s*=\s*"tdxdata\._version\.__version__"', py), (
        "pyproject.toml 的 dynamic attr 没指向 tdxdata._version.__version__")
    # 写死版本不许回来：project 段里不许出现字面量 version =
    assert not re.search(r'^version\s*=\s*"', py, re.M), (
        "pyproject.toml 又出现了字面量 version —— 与 _version.py 双写回归")


def test_init_does_not_hardcode_version():
    """__init__.py 不许再写死 `__version__ = "..."`（双写回归防）。"""
    src = _read("tdxdata/__init__.py")
    assert not re.search(r'^__version__\s*=\s*["\']', src, re.M), (
        "__init__.py 又写死 __version__ —— 双事实源回归（ENG-007 破坏）")
    assert "from ._version import __version__" in src


def test_version_is_030_for_breaking_change():
    """0.3.0：ARCH-002 破坏性切换（meta 移除 → Provenance）按语义化版本升 minor。"""
    v = _source_version()
    assert v == "0.3.0", (
        "版本 %r ≠ 0.3.0：ARCH-002 是破坏性切换（容器 meta 移除），"
        "语义化版本必须升 minor —— bump 后改这条断言即可" % v)


def test_version_file_is_a_single_line_of_truth():
    """_version.py 里 `__version__` 赋值恰好一次（不许长出第二事实源）。"""
    src = _read("tdxdata/_version.py")
    assert len(re.findall(r'^__version__\s*=', src, re.M)) == 1
