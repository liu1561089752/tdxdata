# -*- coding: utf-8 -*-
"""整行全是 null 的占位表 → 判「无数据」，不要伪装成「有 1 行」。

真机来源：`CWServ.ph_tdxdatacenter_gzzc_ggxq_gzzs` 传入不存在的年段键
（`["603439","1","1"]`，正确是 `"3Y"`）时，服务端回 4 列 0 行的空序列 +
3 列 `[[null,null,null]]` 的占位表，ErrorCode 仍是 0。
"""
import pathlib

from tdxdata._engine import session as S

FIX = pathlib.Path(__file__).with_name("fixtures") / "rs_nullrow_gzzs.bin"


def test_整行null判无数据():
    v, nrow, ncol, cols, rows, primary = S.parse(FIX.read_bytes())
    assert v == "EMPTY_RS", v
    assert nrow == 0 and rows is None
    assert "N001" in cols and len(cols) == 4          # 列取并集（4 列）


def test_有真实值的行不受影响():
    pay = ('{"ErrorCode":0,"ResultSets":['
           '{"ColDes":[{"Name":"A"},{"Name":"B"}],"Content":[["1",null],["2","x"]]}]}')
    v, nrow, ncol, cols, rows, primary = S.parse(pay.encode("gbk"))
    assert v == "PASS" and nrow == 2
    assert rows[0] == ["1", None]                      # 部分 null 的行要保留
