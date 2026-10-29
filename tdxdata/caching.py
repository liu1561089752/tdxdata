# -*- coding: utf-8 -*-
"""缓存策略（公开面）：`TTLCache` + 按命令的实测 TTL + `cache="auto"`。

为什么要"按命令"给 TTL，而不是一个统一值
----------------------------------------
同一批 211 个命令里，**数据变化的节奏差三个数量级**（实测，见 `data/cache-ttl.json`）：

| 命令 | 滞后中位数 | 合适的缓存 |
|---|---|---|
| `hq_nlp_peg` | 30 天 | 30 天 |
| `agf10_gsjj` | 296 天 | 30 天（封顶） |
| `zdt_zdfx`（当日涨跌停） | 0 天 | **不缓存** |
| 逐笔 / 分时 / 快照（PB 通道） | 0 | **不缓存** |

统一给 300 秒：宏观表等于没缓存（重复拉一年不变的东西）；
统一给 1 天：当日涨跌停会拿到昨天的表当今天。所以 TTL 只能查表。

用法
----
```python
import tdxdata

c = tdxdata.Client(cache="auto")     # ← 按命令查表定 TTL，逐条 set 时生效
c.get("hg_gdp")        # 宏观：命中后 30 天内不再发请求
c.get("zdt_zdfx")      # 当日数据：ttl=0 → 根本不进缓存
```

自定义（行为与之前完全一致，默认 TTL 由你定）：
```python
c = tdxdata.Client(cache=tdxdata.TTLCache(ttl=300))
```

边界
----
* **命中缓存不占限流闸门**（本来就一个字节都不发）；
* 缓存的是 `Table` **对象本身**（只读语义：`Table` 与 `Row` 都是"取出来就别再改"的东西）；
* 键 = `(cmd, 归一化 params, trade_date)`，与 dict 顺序无关（`cache_key`）；
* 没有实测 TTL 的命令一律 `default_ttl = 0`：**没证据就不缓存**。
"""
from __future__ import annotations

import json
import os

from ._engine.cache import TTLCache, cache_key

__all__ = ["TTLCache", "cache_key", "auto_cache", "ttl_for_cmd", "default_ttl", "POLICY"]

_TTL: dict[str, int] | None = None
_KEY2CMD: dict[str, str] | None = None      # 产品键 → 命令名（让 ttl_for_cmd 两种写法都认）
_DEFAULT = 0

#: 策略来源（写进诊断信息，便于回答"这个 TTL 哪来的"）
POLICY = ("tdxdata/data/cache-ttl.json：按 211 个命令的实测滞后中位数取 ttl = "
          "clamp(lag/2, 1h, 30d)；滞后 0 天 = 不缓存；表里没有的命令默认不缓存")


def _load() -> dict[str, int]:
    global _TTL, _DEFAULT
    if _TTL is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data", "cache-ttl.json")
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
            _TTL = {str(k): int(v) for k, v in (payload.get("ttl") or {}).items()}
            _DEFAULT = int(payload.get("default_ttl") or 0)
        except (OSError, ValueError):        # 数据文件缺失/损坏：退化成"不缓存"
            _TTL, _DEFAULT = {}, 0
    return _TTL


def ttl_for_cmd(name: str) -> int:
    """这条命令（或产品键）该缓存多少秒；`0` = 不缓存。

    `name` 两种写法都认：数据中心的**命令名**（`CWServ.ph_datacenter_hg_gdp`）
    或产品**键**（`hg_gdp`）—— 用户手上通常是后者。
    """
    ttl = _load()
    key = str(name or "")
    if key in ttl:
        return ttl[key]
    global _KEY2CMD
    if _KEY2CMD is None:
        _KEY2CMD = {}
        try:
            from .client import _catalog
            for pkey, spec in _catalog()["products"].items():
                cmd = spec.get("cmd")
                if cmd:
                    _KEY2CMD[pkey] = cmd        # 产品键 → 命令名
        except Exception:                    # noqa: BLE001  台账读不出来不该影响取数
            _KEY2CMD = {}
    cmd = _KEY2CMD.get(key, key)
    return ttl.get(cmd, _DEFAULT)


def auto_cache(maxsize: int = 2048, clock=None) -> "TTLCache":
    """建一个"TTL 逐条查表"的缓存对象。

    它自己的默认 TTL 是 0（不缓存），真正的时长由 `Client` 在写入时按命令查表给 ——
    这样同一个缓存对象能同时服务"缓存 30 天"的宏观表和"绝不缓存"的当日行情。
    """
    kw = {"ttl": 0, "maxsize": maxsize}
    if clock is not None:
        kw["clock"] = clock
    return TTLCache(**kw)


def default_ttl() -> int:
    """表里没登记的命令用多少秒：**0（不缓存）**。"""
    _load()
    return _DEFAULT
