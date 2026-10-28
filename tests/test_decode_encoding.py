# -*- coding: utf-8 -*-
"""DEF-012：`decode_json` 的编解码选择不再随字符串长度翻转。

缺陷形态
--------
旧打分是「凡是汉字 +3」。两个方向都会翻车：

* **UTF-8 字节按 GBK 解**（长串必错）：`上证指数`(4 字) → `涓婅瘉鎸囨暟`(6 字)，
  6×3 = 18 > 4×3 = 12 —— **乱码分数更高**，于是"短串碰巧对、长串一定错"。
  一个 JSON 里汉字越多越容易被判成 GBK，这就是评审说的"随字符串长度翻转"。
* **GBK 字节按 UTF-8 解**（偶发）：`元`(0xD2AA) 是合法 UTF-8 → 亚美尼亚字母 `աԪ`。

现在汉字权重分级：**GB2312 常用字 +3 / 罕用字 −1**。真简体中文几乎全部落在
6763 个 GB2312 汉字内，而乱码产出的罕用字大多不在其中 —— 这是"像不像真中文"的
可判定代理，不依赖任何外部词表。
"""
from __future__ import annotations

import json

from tdxdata._engine import datacenter as dc

# 评审里点名的那条 + 几条容易翻车的真实列名/取值
CASES = [
    "行业板块涨跌家数统计",
    "上证指数",
    "贵州茅台",
    "成交额（元）",
    "所属行业板块名称",
    "融资融券余额合计",
    "涨跌幅",
    "量比",
    "元",
]


def _payload(obj, enc):
    return json.dumps(obj, ensure_ascii=False).encode(enc)


# ══════════════════════════════════════════════════════════════════════════
# 一、两个方向都必须解对
# ══════════════════════════════════════════════════════════════════════════
def test_utf8_payload_keeps_chinese_intact():
    """UTF-8 编码的中文 JSON：一个字都不许变（旧实现在长串上会整体转成乱码）。"""
    for text in CASES:
        obj, got_text = dc.decode_json(_payload({"ColName": [text]}, "utf-8"))
        assert obj is not None, "%r 的 UTF-8 JSON 没解出来" % text
        assert obj["ColName"][0] == text, "UTF-8 解出了乱码：%r" % (obj["ColName"][0],)
        assert got_text is not None and text in got_text


def test_gbk_payload_keeps_chinese_intact():
    """GBK 编码的中文 JSON（服务端主用）：同样不许变。"""
    for text in CASES:
        obj, _ = dc.decode_json(_payload({"ColName": [text]}, "gbk"))
        assert obj is not None, "%r 的 GBK JSON 没解出来" % text
        assert obj["ColName"][0] == text, "GBK 解出了乱码：%r" % (obj["ColName"][0],)


def test_gbk_byte_that_is_also_valid_utf8_is_not_mistaken():
    """`元`(0xD2AA) 是合法 UTF-8（亚美尼亚字母）—— 这是旧的 `_cjk_score` 文档里点名的坑。"""
    raw = _payload({"unit": "元"}, "gbk")
    obj, _ = dc.decode_json(raw)
    assert obj["unit"] == "元"


# ══════════════════════════════════════════════════════════════════════════
# 二、打分本身：常用字必须打赢罕用字（否则上面的结论就是碰巧）
# ══════════════════════════════════════════════════════════════════════════
def test_common_hanzi_table_is_gb2312():
    table = dc._common_hanzi()
    assert len(table) == 6763, "GB2312 汉字数应为 6763，实测 %d" % len(table)
    assert "上" in table and "指" in table and "数" in table
    assert dc._common_hanzi() is table, "字表必须只构造一次（懒缓存）"


def test_score_prefers_real_chinese_over_gbk_mojibake():
    """`上证指数` 的分数必须**高于**它被 GBK 误读出来的 `涓婅瘉鎸囨暟`。"""
    right = "上证指数"
    wrong = right.encode("utf-8").decode("gbk")
    assert wrong != right
    assert dc._cjk_score(right) > dc._cjk_score(wrong), (
        "打分仍然偏爱乱码：%d vs %d（%r vs %r）"
        % (dc._cjk_score(right), dc._cjk_score(wrong), right, wrong))


def test_score_penalises_armenian_mojibake():
    """GBK→UTF-8 方向的乱码（亚美尼亚/西里尔/拉丁扩展）仍要被扣分。"""
    assert dc._cjk_score("元") > dc._cjk_score("元".encode("gbk").decode("utf-8"))


def test_decode_is_deterministic():
    raw = _payload({"a": "上证指数", "b": ["涨跌幅"]}, "utf-8")
    first = dc.decode_json(raw)
    second = dc.decode_json(raw)
    assert first == second
