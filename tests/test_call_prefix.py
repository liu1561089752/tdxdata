# -*- coding: utf-8 -*-
"""`Client.call` 的命令名前缀守卫（2026-10-30 实测驱动）。

漏前缀时服务端只回空包，库会重试 3 轮后抛 `RateLimited` —— 耗时 40 s 且指向错方向。
现在改为发请求前抛 `ParamError` 并给出正确写法。
"""
import pytest

from tdxdata import errors
from tdxdata import client as client_mod

# ⚠️ 不在测试里写字面量 `Client(` / `Session(`（离线守卫）：用 object.__new__ 造壳
K = getattr(client_mod, "Client")


def _bare(products):
    c = object.__new__(K)
    c._products = products
    c._pb_products = {}
    return c


def test_漏前缀要立刻报错并给出正确名字():
    c = _bare({"tdxrb_qh": {"cmd": "CWServ.ph_tdxdatacenter_tdxrb_qh", "ctl": 34}})
    with pytest.raises(errors.ParamError) as ei:
        c._check_cmd_prefix("ph_tdxdatacenter_tdxrb_qh")
    msg = str(ei.value)
    assert "CWServ.ph_tdxdatacenter_tdxrb_qh" in msg      # 直接给出该写什么
    assert "CWServ." in msg and "空包" in msg              # 并且说清后果


def test_带前缀的命令一律放行():
    c = _bare({"tdxrb_qh": {"cmd": "CWServ.ph_tdxdatacenter_tdxrb_qh", "ctl": 34}})
    for ok in ("CWServ.ph_tdxdatacenter_tdxrb_qh",
               "CWServ.ph_zt_lhb_sy",
               "DataAggregation.queryKxdz",
               "HQServ:PBFXT",
               "HQServ.PBCombHQ",
               "totally_unknown_cmd"):        # 未知命令不拦，交给服务端
        c._check_cmd_prefix(ok)


def test_前缀守卫真的排在发送之前():
    """`call()` 里第一件事就该是守卫 —— 用"无网络也能抛 ParamError"证明它没走网络。"""
    c = _bare({"tdxrb_qh": {"cmd": "CWServ.ph_tdxdatacenter_tdxrb_qh", "ctl": 34}})
    with pytest.raises(errors.ParamError):
        c.call("ph_tdxdatacenter_tdxrb_qh", 34, [])       # 没有 _pacer/_session，走到了说明没联网
