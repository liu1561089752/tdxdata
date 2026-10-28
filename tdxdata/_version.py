# -*- coding: utf-8 -*-
r"""版本**单一事实源**（ENG-007，批次 E）。

为什么单独一个文件：`__version__` 曾经双写（pyproject.toml 一份、
`__init__.py` 一份）—— 升级时改一处漏一处，importlib.metadata 报的和
`import tdxdata; tdxdata.__version__` 不同步。现在：

    pyproject.toml:  dynamic = ["version"]
                     [tool.setuptools.dynamic]
                     version = {attr = "tdxdata._version.__version__"}
    __init__.py:     from ._version import __version__

两路读数必然一致（同一份源）。bump 到 **0.3.0**：ARCH-002 做了破坏性切换
（容器 meta 移除 → Provenance），按语义化版本必须升 minor。
"""
__version__ = "0.3.0"

#: 可比较的版本元组（ENG-008）：`if tdxdata.__version_info__ < (0, 4): ...`
#: 只放**数字段**（`0.3.0` → `(0, 3, 0)`）；将来的预发布标记另设键，
#: 不往这个元组里塞字符串 —— 那会让比较在混类型时抛 TypeError。
__version_info__ = tuple(int(p) for p in __version__.split(".") if p.isdigit())

__all__ = ["__version__", "__version_info__"]
