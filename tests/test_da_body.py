# -*- coding: utf-8 -*-
"""`DataAggregation.*` 的请求体是裸数组/对象（不套 `{"Params":…}`）。

依据：js 原件 `fwCallTql("DataAggregation.queryBigDataEffect", [{year:…,timetype:"3"}], …)`，
以及真机对照（裸数组 → 56 条事件；套包装 → 服务端 "For input string"）。
"""
import json

from tdxdata._engine import session as S


def test_DataAggregation_裸数组():
    body = S.body_of("DataAggregation.queryBigDataEffect", [{"year": 2025, "timetype": "3"}])
    assert json.loads(body) == [{"year": 2025, "timetype": "3"}]


def test_DataAggregation_裸对象():
    body = S.body_of("DataAggregation.queryKxdz", {"queryType": "1"})
    assert json.loads(body) == {"queryType": "1"}


def test_CWServ_仍然带Params包装():
    body = S.body_of("CWServ.ph_tdxdatacenter_tjcb_sy", ["00101", "1"])
    assert json.loads(body) == {"Params": ["00101", "1"]}


def test_HQServ_仍然裸发():
    body = S.body_of("HQServ.PBPzxh", {"Code": ["600303"]})
    assert json.loads(body) == {"Code": ["600303"]}
