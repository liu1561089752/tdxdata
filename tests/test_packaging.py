# -*- coding: utf-8 -*-
r"""打包完整性：`pip install` 之后包是否完整可用。

这是「clone 下来就能用」这条承诺的自动化保证 —— 验的是**装出来那一份**，
不是源码树里那一份：

1. 把仓库复制到临时目录，从那里 `pip install --target`
2. 用**独立子进程**、只挂载安装目录去 import
3. 断言：版本号与 `pyproject.toml` 一致、421 个产品齐全、子包可导入、
   **包内数据文件**（`data/*.json`、`_engine/catalog/*.json`）确实被带上了

约束（硬红线）

* 装到 `%TEMP%` 的临时目录，**不碰 site-packages**
* 全程 `--no-deps --no-build-isolation`（离线、不拉第三方包）
* 源码是**复制**出去的，所以项目目录不会被写出 `build/` / `*.egg-info`
* 用完删临时目录

跑法：`python -m pytest tests/test_packaging.py -q`
跳过：`TDXDATA_SKIP_PACKAGING=1`，或环境里没有可用 pip
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 打包只需要这些（tests/docs/examples 不进包）
COPY_ITEMS = ("tdxdata", "pyproject.toml", "README.md", "LICENSE")

_CACHE = {}


def _pip_available() -> bool:
    try:
        proc = subprocess.run([sys.executable, "-m", "pip", "--version"],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=120)
    except Exception:                                        # noqa: BLE001
        return False
    return proc.returncode == 0


SKIP_REASON = ""
if os.environ.get("TDXDATA_SKIP_PACKAGING") == "1":
    SKIP_REASON = "TDXDATA_SKIP_PACKAGING=1（主动跳过）"
elif not _pip_available():
    SKIP_REASON = "环境里没有可用的 pip（python -m pip --version 失败）"


def _declared_version() -> str:
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        m = re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.M)
    assert m, "pyproject.toml 里找不到 version"
    return m.group(1)


def _install_once():
    """装一次并缓存结果：返回 (安装目录, 子进程 stdout)。"""
    if "res" in _CACHE:
        return _CACHE["res"]

    work = tempfile.mkdtemp(prefix="tdxdata_pkgchk_")
    src = os.path.join(work, "src")
    site = os.path.join(work, "site")
    os.makedirs(src)
    os.makedirs(site)
    for item in COPY_ITEMS:
        s = os.path.join(ROOT, item)
        d = os.path.join(src, item)
        if os.path.isdir(s):
            shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(s, d)

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install",
             "--no-deps", "--no-build-isolation", "--no-index",
             "--target", site, src],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600,
        )
        assert proc.returncode == 0, "pip install 失败：\n%s" % proc.stdout.decode(
            "utf-8", "replace")[-3000:]

        # 独立子进程：只挂载安装目录，确保验的是装出来那份
        probe = (
            "import json, sys\n"
            "sys.path.insert(0, %r)\n"
            "import tdxdata\n"
            "c = tdxdata.Client()\n"
            "info = {'version': tdxdata.__version__,\n"
            "        'products': len(c.products()),\n"
            "        'domains': len(c.domains())}\n"
            "print('PROBE ' + json.dumps(info))\n"
        ) % site
        run = subprocess.run([sys.executable, "-c", probe],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             timeout=300, cwd=site)
        out = run.stdout.decode("utf-8", "replace")
        assert run.returncode == 0, "子进程 import 失败：\n%s" % out[-3000:]
        _CACHE["res"] = (site, out)
        return _CACHE["res"]
    finally:
        # 安装目录要留给断言用，源码副本先删
        shutil.rmtree(src, ignore_errors=True)


def _probe_info() -> dict:
    import json
    _site, out = _install_once()
    for line in out.splitlines():
        if line.startswith("PROBE "):
            return json.loads(line[6:])
    raise AssertionError("子进程没输出 PROBE 行：\n%s" % out[-2000:])


def test_installs_and_imports():
    """装出来的包能 import，版本号与 pyproject 一致。"""
    if SKIP_REASON:
        print("跳过：%s" % SKIP_REASON)
        return
    info = _probe_info()
    assert info["version"] == _declared_version(), (
        "版本号不一致：装出来 %r，pyproject 声明 %r"
        % (info["version"], _declared_version()))


def test_products_complete_after_install():
    """421 个数据中心产品随包走（验的是包内数据文件没漏打）。"""
    if SKIP_REASON:
        print("跳过：%s" % SKIP_REASON)
        return
    info = _probe_info()
    assert info["products"] == 421, "产品数 %r ≠ 421（包内数据文件可能没打进去）" % (
        info["products"],)


def test_package_data_present():
    """`data/*.json` 与 `_engine/catalog/*.json` 必须在安装目录里。"""
    if SKIP_REASON:
        print("跳过：%s" % SKIP_REASON)
        return
    site, _out = _install_once()
    pkg = os.path.join(site, "tdxdata")
    assert os.path.isfile(os.path.join(pkg, "data", "products.json")), \
        "缺少 data/products.json"
    catalog = os.path.join(pkg, "_engine", "catalog")
    assert os.path.isdir(catalog), "缺少 _engine/catalog/"
    jsons = [n for n in os.listdir(catalog) if n.endswith(".json")]
    assert len(jsons) >= 3, "catalog 里的 json 少于 3 个：%s" % jsons


def test_subpackages_importable():
    """子包（_engine / sections）必须能被单独导入。"""
    if SKIP_REASON:
        print("跳过：%s" % SKIP_REASON)
        return
    site, _out = _install_once()
    code = (
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "import tdxdata.sections, tdxdata._engine.pb, tdxdata._engine.cache\n"
        "print('OK')\n"
    ) % site
    run = subprocess.run([sys.executable, "-c", code],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         timeout=300, cwd=site)
    assert run.returncode == 0, run.stdout.decode("utf-8", "replace")[-2000:]


def test_no_build_artifacts_left_in_project():
    """打包不许在项目目录里留下 `build/` / `dist/` / `*.egg-info`。"""
    if SKIP_REASON:
        print("跳过：%s" % SKIP_REASON)
        return
    _install_once()
    leftovers = [n for n in ("build", "dist") if os.path.isdir(os.path.join(ROOT, n))]
    leftovers += [n for n in os.listdir(ROOT) if n.endswith("egg-info")]
    assert not leftovers, "打包留下了临时产物：%s" % leftovers


if __name__ == "__main__":
    if SKIP_REASON:
        print("跳过打包测试：%s" % SKIP_REASON)
        sys.exit(0)
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print("  [PASS] %s" % name)
        except AssertionError as exc:
            bad += 1
            print("  [FAIL] %s: %s" % (name, exc))
        except Exception as exc:                             # noqa: BLE001
            bad += 1
            print("  [ERR ] %s: %s: %s" % (name, type(exc).__name__, exc))
    print("\n%d 个用例，失败 %d" % (len(fns), bad))
    sys.exit(1 if bad else 0)
