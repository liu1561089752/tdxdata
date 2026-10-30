# -*- coding: utf-8 -*-
"""裸数组错误体（DataAggregation.* 的形态）：别再把"服务端说了什么"埋成协议错误。

2026-10-30 实测：`DataAggregation.queryKxdz` 回
`[[-1,"调用TGA:queryTrainingRankingList,Code：-103,msg:参数：queryType 为必须项",0,"",""],[],[],[]]`
（GBK 编码的 JSON）。库原来判 BADJSON，只告诉用户"不是 ResultSets 协议形态"。
"""
from tdxdata._engine import session as S

BARE = ('[[-1,"调用TGA:queryTrainingRankingList,Code：-103,msg:参数：queryType 为必须项",'
        '0,"",""],[],[],[]]')


def test_裸数组错误体被识别():
    v, nrow, ncol, cols, msg, primary = S.parse(BARE.encode("gbk"))
    assert v == "BARE_ERR:-1", v
    assert nrow == ncol == 0 and cols == [] and primary == []
    assert "queryType" in msg                       # 服务端原话要保留
    assert "为必须项" in msg                        # 且 GBK 要解对（不能是乱码）


def test_空数组仍是没数据_别升级成错误():
    assert S.parse(b"[]")[0] == "EMPTY_RS"


def test_非错误形态的数组还是BADJSON():
    assert S.parse(b"[[1,2]]")[0] == "BADJSON"      # 首元素不是负数 → 不认作错误体
    assert S.parse(b'[{"a":1}]')[0] == "BADJSON"


def test_正常ResultSets不受影响():
    ok = '{"ErrorCode":0,"ResultSets":[{"ColName":["N001"],"Content":[["x"]]}]}'
    v, nrow, ncol, cols, rows, primary = S.parse(ok.encode("gbk"))
    assert v == "PASS" and rows == [["x"]] and cols == ["N001"]
