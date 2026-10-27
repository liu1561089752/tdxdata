# -*- coding: utf-8 -*-
r"""pytest 公共配置：**离线**可跑，`import tdxdata` 必须拿到本目录的包。

为什么需要
----------
同一台机器上可能装着别的 `tdxdata`（旧 copy、另一份工作区）。若测试 import 到
那份包，会出现"改了代码测试却没变化"的假绿。这里把项目根插到 `sys.path[0]`，
并额外打印实际 import 到的路径；离线用例也会硬断言这一点。

跑法
----
    python -m pytest tests -q -m "not online"      # 离线全量
    python tests/test_core.py                      # 单文件（脚本自带 __main__）
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_DIR = os.path.join(ROOT, "tdxdata")

# conftest 先于测试模块被导入，这里插最前即可保证拿到本目录的包
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
# 保险：把项目根顶到第一位（别的地方可能也插过 sys.path）
elif sys.path[0] != ROOT:
    sys.path.remove(ROOT)
    sys.path.insert(0, ROOT)


def pytest_configure(config):
    """兜底注册 `online` marker（`pyproject.toml` 已声明，此处不改它）。"""
    config.addinivalue_line("markers", "online: 需要真实网络（默认不跑）")


def pytest_report_header(config):
    """表头里报出实际 import 到哪个包 —— 一眼看出有没有测错副本。"""
    try:
        import tdxdata
    except Exception as exc:                                  # noqa: BLE001
        return "tdxdata: 导入失败 %s: %s" % (type(exc).__name__, exc)
    got = os.path.dirname(os.path.abspath(tdxdata.__file__))
    flag = "OK" if os.path.normcase(got) == os.path.normcase(PKG_DIR) else "!! 不是本目录的包"
    return "tdxdata %s ← %s [%s]" % (getattr(tdxdata, "__version__", "?"), got, flag)
