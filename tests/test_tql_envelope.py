# -*- coding: utf-8 -*-
"""TQL 信封：`[[0,"",行数,"",""],[列名…],[],[行…],…]`

2026-10-30 实测两份真机回包（存 fixtures）：
  * `JNLPSE.expandList`              12 列 × 100 行，第 3 元起是值列表；
  * `DataAggregation.queryBigDataEffect`  2 列(R1/R2) × 1 行，每格是一段 JSON **文本**。
以前这两种回包一律判 `BADJSON`（"不是 ResultSets 协议形态"），命令看着像解不开。
"""
import json
import pathlib

import pytest

from tdxdata._engine import session as S

FIX = pathlib.Path(__file__).with_name("fixtures")


def _parse(name):
    return S.parse((FIX / name).read_bytes())


def test_展开列表_12列100行():
    v, nrow, ncol, cols, rows, primary = _parse("tql_expandlist.bin")
    assert v == "PASS", v
    assert (nrow, ncol) == (100, 12), (nrow, ncol)
    assert cols[:3] == ["market", "sec_code", "stock_name"]
    assert cols[-1] == "changeFlag"
    assert rows[0][:3] == ["1", "588000", "科创50ETF华夏"]   # 科创50ETF华夏
    assert all(len(r) == 12 for r in rows)                    # 每行宽度一致


def test_大数据效应_两列R1R2_每格是JSON文本():
    v, nrow, ncol, cols, rows, primary = _parse("tql_bigeffect.bin")
    assert v == "PASS", v
    assert (nrow, ncol) == (1, 2), (nrow, ncol)
    assert cols == ["R1", "R2"]
    events = json.loads(rows[0][0])                           # R1 是事件表
    assert isinstance(events, list) and len(events) > 30
    assert {"N001", "N002", "N014"} <= set(events[0])         # 事件名/类别/指标
    assert json.loads(rows[0][1]) == []                       # R2 本期为空


def test_信封里没有数据行时算没数据():
    v, nrow, ncol, cols, rows, primary = S.parse(
        b'[[0,"",0,"",""],["a","b"],[],[]]')
    assert v == "EMPTY_RS"
    assert cols == ["a", "b"] and rows is None


def test_太短或首元非零不认作信封():
    assert S.parse(b'[[0,""]]')[0] == "BADJSON"
    assert S.parse(b'[[5,"",1,"",""],["a"],[],[]]')[0] == "BADJSON"


def test_错误体优先于信封():
    """首元是负数 → 仍是错误体（BARE_ERR），不能被信封规则抢走。"""
    v = S.parse('[[-1,"参数：queryType 为必须项",0,"",""],[],[],[]]'.encode("gbk"))[0]
    assert v == "BARE_ERR:-1"


@pytest.mark.parametrize("name", ["tql_expandlist.bin", "tql_bigeffect.bin"])
def test_信封回包能构建成表(name):
    """端到端：parse 的结果要能喂给客户端的表构造（列数/行数自洽）。"""
    v, nrow, ncol, cols, rows, primary = _parse(name)
    assert v == "PASS"
    assert len(cols) == ncol
    assert len(rows) == nrow
    assert primary == cols
