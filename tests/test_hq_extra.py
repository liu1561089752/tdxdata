# -*- coding: utf-8 -*-
"""交接包 5 条命令的**离线**用例（不连网）。

两层判据
--------
1. **请求逐字节对齐原始字节** —— 5 个 `_args_*` 构造函数必须与 `docs/VERIFICATION.md` 里
   抄下来的原始 hex 一模一样。这是本仓库的既有惯例（见 `tests/test_p8_adjust_page.py`），
   也是唯一能防住"字段号/wiretype 写错"的手段。
2. **解析器对真实响应夹具** —— `tests/fixtures/*.bin` 是 2026-10-27 实网抓下来、
   已经剥掉外壳的响应体。断言具体数值（贵州茅台 1335.00、行业块首行 汽车零部件、
   samplemore 61 个 float、hqinfo → 水产品），不是断言"返回非空"。

为什么钉 hex 而不钉"看起来对"
-----------------------------
本项目历史上因为**猜字段**栽了好几轮（field2 写成 bytes 导致深市查不到名字、
`DELAYMIN` 手抄成 `DELAYINN` 导致比对失败）。所以这里一律拿抓包原样比对。

跑法：
    python tests/test_hq_extra.py
    python -m pytest tests/test_hq_extra.py -q
"""
from __future__ import annotations

import base64
import inspect
import os
import struct
import sys
import zlib

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tdxdata import errors, symbols                                    # noqa: E402
from tdxdata._engine import hq_extra as X                             # noqa: E402
from tdxdata.client import _collect_ticks, _tick_from_row               # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _fix(name: str) -> bytes:
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


def _h(s: str) -> bytes:
    """抓包 hex → 字节（容忍折行/空格）。"""
    return bytes.fromhex("".join(s.split()))


# ══════════════════════════════════════════════════════════════════════════
# 一、请求：逐字节对齐抓包
# ══════════════════════════════════════════════════════════════════════════
# 01 文档 §2.3：192 B，6 个指数/混合代码
COMBHQ_HEX = """
0a0b0a013110001a0022002800 12065853464c4147 1204464c4147 12085350454354595045
1206487144617465 1206487154696d65 1206484153544950 120756415230353134 120552454c4147
120844454c41594d494e 1205434c4f5345 12034e4f57 12055a53534a4c 12044f50454e
12054d474a5a43 1203575442 12034d4158 12034d494e
1a06 010002010001
2206 393939393939 2206 333939303031 2206 383939303530
2206 303030363830 2206 333939303036 2206 303030363838
3000
"""


def test_args_combhq_is_byte_identical_to_capture():
    """6 个代码的组合行情请求必须 192 B 且与抓包逐字节一致。"""
    items = [(1, "999999"), (0, "399001"), (2, "899050"),
             (1, "000680"), (0, "399006"), (1, "000688")]
    want = _h(COMBHQ_HEX)
    got = X._args_combhq(items)
    assert len(want) == 192, len(want)
    assert got == want, "首个不等字节偏移=%d\n want=%s\n  got=%s" % (
        next((i for i in range(min(len(got), len(want))) if got[i] != want[i]), -1),
        want.hex(), got.hex())


def test_args_combhq_field3_carries_markets():
    """`field3` 是市场号 packed varint，与 field4 顺序一一对应。"""
    items = [(1, "999999"), (0, "399001"), (2, "899050")]
    args = X._args_combhq(items)
    # field3 的 tag 是 0x1a，长度 3，值 = 01 00 02
    i = args.index(b"\x1a\x03")
    assert args[i + 2:i + 5] == bytes([1, 0, 2]), args[i:i + 5]
    # field4 的 tag 是 0x22，紧随其后是代码
    assert b"\x22\x06" + b"999999" in args
    assert b"\x22\x06" + b"899050" in args


def test_args_combhq_cols_are_the_17_captured_names():
    """列名清单必须与抓包一致（手抄错一个字母字节就不等）。"""
    assert len(X.COMBHQ_COLS) == 17
    assert X.COMBHQ_COLS[8] == "DELAYMIN", X.COMBHQ_COLS   # 曾手抄成 DELAYINN
    args = X._args_combhq([(1, "600519")])
    for c in X.COMBHQ_COLS:
        assert b"\x12" + bytes([len(c)]) + c.encode() in args, c


def test_args_combhq_full_asks_vol_amount_lb_not_volume():
    """多要的三列：`VOL`/`AMOUNT`/`LB` 必须在请求里，**`VOLUME` 不许出现**。

    出处（2026-10-27 实网逐列试）：`VOLUME` 服务端**不认**（列数不变），
    所以 `COMBHQ_STD` 里原来那条 `"VOLUME": "volume"` 是**死映射**，永远不会命中。
    """
    assert X.COMBHQ_COLS_FULL == X.COMBHQ_COLS + ("VOL", "AMOUNT", "LB")
    args = X._args_combhq([(1, "600519")], X.COMBHQ_COLS_FULL)
    for c in ("VOL", "AMOUNT", "LB"):
        assert b"\x12" + bytes([len(c)]) + c.encode() in args, c
    assert b"VOLUME" not in args, "VOLUME 服务端不认，不该出现在请求里"
    # 抓包原样的 17 列一列都不能少（多要 ≠ 换掉）
    for c in X.COMBHQ_COLS:
        assert b"\x12" + bytes([len(c)]) + c.encode() in args, c
    # 默认参数仍只发那 17 列 —— 192 B 抓包保真靠的就是这个默认值
    assert X._args_combhq([(1, "600519")]) != args


def test_combhq_std_maps_vol_not_volume():
    """`COMBHQ_STD` 映 `VOL`（服务端认），不留 `VOLUME`（不认的死映射）。"""
    assert X.COMBHQ_STD["VOL"] == "volume"
    assert "VOLUME" not in X.COMBHQ_STD
    assert X.COMBHQ_STD["AMOUNT"] == "amount"
    assert "LB" not in X.COMBHQ_STD, "LB 语义未定，只许留在 raw"


def test_combhq_full_fixture_has_vol_amount_columns():
    """多要三列后的真实响应夹具：25 列，`VOL`/`LB`/`AMOUNT` 都在列名里。

    夹具 `combhq_full_vol_amount_响应.bin` 是 2026-10-27 实网抓的（盘前，
    所以 `VOL`/`AMOUNT` 是服务端真值 0，`LB` 已有值）。
    """
    cols, rows = X.parse_rowtable(_fix("combhq_full_vol_amount_响应.bin"))
    assert len(cols) == 25, cols
    for c in ("VOL", "AMOUNT", "LB"):
        assert c in cols, "%s 不在列里：%s" % (c, cols)
    d = {r[0]: dict(zip(cols, r)) for r in rows}
    assert d["600519"]["VOL"] == "0", d["600519"]        # 盘前真值 0（不是缺列）
    assert d["600519"]["AMOUNT"] == "0.00", d["600519"]
    assert float(d["600519"]["LB"]) > 0, d["600519"]     # LB 盘前就有值


def test_num_or_none_distinguishes_missing_column_from_zero():
    """「没这列」→ None；「这列是 0」→ 0.0。这两件事绝不能混。"""
    from tdxdata.client import _num_or_none

    assert _num_or_none({"VOL": "0"}, "VOL") == 0.0
    assert _num_or_none({"VOL": "0"}, "VOL") is not None
    assert _num_or_none({"VOL": "1234.5"}, "VOL") == 1234.5
    assert _num_or_none({}, "VOL") is None              # 列不存在
    assert _num_or_none({"VOL": ""}, "VOL") is None      # 空串当缺
    assert _num_or_none({"AMOUNT": "0.00"}, "AMOUNT") == 0.0


def test_quote_from_row_fills_vol_amount():
    """端到端（离线）：列 → `Quote` 的映射把 vol/amount 真的取出来，0 也照填。

    走的是 `client._quote_from_row()`（`get_quotes` 内部就调它），**不建连**：
    门禁第 7 条禁止在离线用例里 `Client()`，所以这里直接喂列值。
    """
    from tdxdata.client import _quote_from_row

    cols, rows = X.parse_rowtable(_fix("combhq_full_vol_amount_响应.bin"))
    qs = {q.code: q for q in (_quote_from_row(dict(zip(cols, r))) for r in rows)}
    moutai = qs["600519"]
    assert moutai.vol == 0.0 and moutai.vol is not None
    assert moutai.amount == 0.0 and moutai.amount is not None
    assert moutai.raw["VOL"] == "0"                  # 原值仍在 raw
    assert moutai.raw["LB"] == "0.23"                # LB 只留 raw，不映射字段
    assert moutai.name == "贵州茅台"
    assert moutai.price > 0


def test_quote_from_row_vol_amount_is_none_when_column_absent():
    """列**不存在**时 vol/amount 必须是 None —— 不许拿 0.0 冒充（假数据红线）。"""
    from tdxdata.client import _quote_from_row

    cols, rows = X.parse_rowtable(_fix("combhq_full_vol_amount_响应.bin"))
    keep = [i for i, c in enumerate(cols) if c not in ("VOL", "AMOUNT")]
    cols2 = [cols[i] for i in keep]
    row2 = [rows[0][i] for i in keep]
    assert "VOL" not in cols2 and "AMOUNT" not in cols2
    q = _quote_from_row(dict(zip(cols2, row2)))
    assert q.vol is None, q.vol
    assert q.amount is None, q.amount
    assert "VOL" not in q.raw


def test_stamp_pads_short_hqtime():
    """`_stamp` 必须给**不足 6 位**的 `HqTime` 补前导 0（2026-10-27 修的 bug）。

    服务端把 `HqTime` 当整数存，**前导 0 会被吃掉**：09:42:58 给 `"94258"`、
    00:05:09 给 `"509"`。旧代码只认 `len == 6`，于是原样拼成 `'2026-10-27 94258'`
    —— 每只票的 `server_time` 上午都是错的。
    """
    from tdxdata.client import _stamp

    assert _stamp("20261027", "94258") == "2026-10-27 09:42:58"     # 5 位
    assert _stamp("20261027", "153002") == "2026-10-27 15:30:02"    # 6 位（原来就对）
    assert _stamp("20261027", "0") == "2026-10-27 00:00:00"         # 1 位
    assert _stamp("20261027", "509") == "2026-10-27 00:05:09"       # 3 位
    assert _stamp("20261027", "1") == "2026-10-27 00:00:01"


def test_stamp_never_guesses_on_weird_input():
    """格式对不上就**原样返回**，不崩、不猜（"绝不猜"红线）。"""
    from tdxdata.client import _stamp

    assert _stamp("", "") == ""
    assert _stamp("20261027", "") == "2026-10-27"
    assert _stamp("", "94258") == "09:42:58"
    assert _stamp("20261027", "abc") == "2026-10-27 abc", "非数字原样，不许瞎格式化"
    assert _stamp("20261027", "1234567") == "2026-10-27 1234567", "超 6 位原样，不截断"
    assert _stamp("2026-10-27", "94258") == "2026-10-27 09:42:58", "日期已格式化则原样保留"


def test_quote_from_page_row_passes_vol_amount():
    """`PBMultiHQ` 一行 → `Quote`：**`AMOUNT`/`VOL` 要真的传进去**（2026-10-27 修的 bug）。

    症状回顾：这两列服务端一直有回、值也一直躺在 `raw` 里，但旧代码构造 `Quote` 时没传，
    于是 `Quote.amount` 对外恒 `0.0`、真值只能自己去翻 `raw`。这里断言"列在 → 字段有值"，
    而不是"反正 raw 里有"。
    """
    from tdxdata.client import _quote_from_page_row

    row = {"Code": "688252", "Setcode": "1", "Name": "天德钰", "CLOSE": "20.50",
           "NOW": "21.18", "AMOUNT": "43914168.00", "VOL": "21164"}
    q = _quote_from_page_row(row, market=6)
    assert q.code == "688252" and q.name == "天德钰"
    assert q.price == 21.18, q.price
    assert q.pre_close == 20.50, q.pre_close
    assert q.amount == 43914168.0, q.amount       # 真值必须进字段，不能只躺在 raw
    assert q.vol == 21164.0, q.vol
    assert q.vol is not None and q.amount is not None
    # 单位自洽：手 → 股 → 金额，应与 AMOUNT 同量级（差 <5% 是同刻不同字段的取舍）
    assert abs(q.vol * 100 * q.price - q.amount) / q.amount < 0.05, (q.vol, q.price, q.amount)


def test_quote_from_page_row_missing_columns_are_none():
    """行里没有 `VOL`/`AMOUNT` → **`None`**（断言 `is None`，不是 `== 0`）。"""
    from tdxdata.client import _quote_from_page_row

    q = _quote_from_page_row({"Code": "000001", "Setcode": "0", "Name": "平安银行",
                              "CLOSE": "11.20", "NOW": "11.28"}, market=6)
    assert q.vol is None, q.vol
    assert q.amount is None, q.amount
    assert "VOL" not in q.raw and "AMOUNT" not in q.raw


def test_quote_model_defaults_are_none_not_zero():
    """`Quote()` 不传量额时默认就是 `None` —— `0.0` 会被读成"成交量为零"，那是假值。"""
    from tdxdata.models import Quote

    q = Quote(market=6, code="600519", price=1349.98)
    assert q.vol is None, q.vol
    assert q.amount is None, q.amount
    assert q.bid == [] and q.ask == []      # 协议无五档，恒空


def test_args_hqinfo_matches_capture():
    """02 文档 §3 的两条抓包：`f2=3`（880903）与 `f2=1`（880005）。"""
    assert X._args_hqinfo("880903", 1, f2=3, f9=5) == _h(
        "0a022800 1003 1a06 383830393033 2001 2800 3000 3800 4000 4805 6000 6800")
    assert X._args_hqinfo("880005", 1, f2=1, f9=5) == _h(
        "0a022800 1001 1a06 383830303035 2001 2800 3000 3800 4000 4805 6000 6800")


def test_args_hqinfo_field2_is_varint_not_bytes():
    """`field2` 必须是 varint（`10 03`）。写成 bytes（`12 01 03`）深市就查不到名字。"""
    args = X._args_hqinfo("399001", 0)
    assert b"\x10\x03" in args, "field2 必须是 varint 形态"
    assert b"\x12\x01\x03" not in args, "field2 被写成 bytes 了 —— 这是踩过的坑"


def test_args_xmlblock_matches_capture():
    """03 文档 §3 的三条抓包（含块名少个 `_R` 的那条）。"""
    assert X._args_xmlblock("MStock_ZLJX_ADDE_R", "HY,1,0,3", 0) == _h(
        "0a022800 1000 1a08 48592c312c302c33 2000 2800"
        " 3212 4d53746f636b5f5a4c4a585f414444455f52")
    assert X._args_xmlblock("MStock_JCXH_ALL", "RN,0,2", 999) == _h(
        "0a022800 10e707 1a06 524e2c302c32 2000 2800"
        " 320f 4d53746f636b5f4a4358485f414c4c")
    assert X._args_xmlblock("MStock_ZLJX_ADDE", "HY,1,0,3", 0) == _h(
        "0a022800 1000 1a08 48592c312c302c33 2000 2800"
        " 3210 4d53746f636b5f5a4c4a585f41444445")


def test_args_xmlblock_field4_5_are_varint():
    """`field4`/`field5` 必须是 varint（`20 00` `28 00`）；写成空 bytes 只回一个头。"""
    args = X._args_xmlblock("MStock_JCXH_ALL", "RN,0,2", 999)
    assert b"\x20\x00" in args and b"\x28\x00" in args
    assert b"\x22\x00" not in args, "field4 被写成空 bytes 了 —— 服务端只回一个头"


def test_args_samplemore_matches_capture():
    """04 文档 §2：5 个代码 = 90 B。"""
    codes = ["880903", "880703", "880501", "880502", "880505"]
    want = _h("""
    0a100a02280010011a063838303930332001 0a100a02280010011a063838303730332001
    0a100a02280010011a063838303530312001 0a100a02280010011a063838303530322001
    0a100a02280010011a063838303530352001""")
    got = X._args_samplemore(codes)
    assert len(got) == 90, len(got)
    assert got == want, got.hex()


# `PBHYStat` 抓包里**全部** 5 种去重参数（2026-10-27 由 extract_params.py 对
# cap_gn.pcap 逐帧解码：53 次出现、去重后只有这 5 种；f4/f6/f7/f8 恒为 0/6/0/2）。
# ⚠️ 每一条都带 `28 00`（f5），**f5=0 也写出来** —— 这条曾被 05 文档 §2 误读成
# "f5=0 时字段不出现"，导致编码器漏字节；改正见 07 回执 §2.5。
HYSTAT_CAPTURED = [
    (-1, 14, "0a022800 1006 18ffffffffffffffffff01 2000 280e 3006 3800 4002"),  # 27 B
    (0, 0, "0a022800 1006 1800 2000 2800 3006 3800 4002"),                      # 18 B
    (3, 0, "0a022800 1006 1803 2000 2800 3006 3800 4002"),                      # 18 B
    (4, 0, "0a022800 1006 1804 2000 2800 3006 3800 4002"),                      # 18 B
    (5, 0, "0a022800 1006 1805 2000 2800 3006 3800 4002"),                      # 18 B
]


def test_args_hystat_matches_capture():
    """抓包 5 个变体逐个逐字节对齐；`f3=-1` 要编码成 uint64 二补码。"""
    for f3, f5, hexs in HYSTAT_CAPTURED:
        got = X._args_hystat(f3=f3, f5=f5)
        want = _h(hexs)
        assert got == want, "f3=%s f5=%s: %s != %s" % (f3, f5, got.hex(), want.hex())
    assert len(X._args_hystat(f3=-1, f5=14)) == 27
    assert len(X._args_hystat(f3=3, f5=0)) == 18


def test_args_hystat_f5_zero_still_emitted():
    """回归：`f5 == 0` 也必须写出 `28 00`，不许回退成 `if f5:`。

    证据：cap_gn.pcap 里 f3=0/3/4/5 的那 4 条参数都是 **18 B 且含 `28 00`**；
    若按旧实现省略 f5，编码出 16 B，与服务端抓到的请求字节不同。
    """
    got = X._args_hystat(f3=3, f5=0)
    assert got == _h("0a022800 1006 1803 2000 2800 3006 3800 4002")
    # f5 的 `28 00` 必须落在 field4(`20 00`) 与 field6(`30 06`) 之间
    assert got[8:10] == b"\x20\x00"
    assert got[10:12] == b"\x28\x00"
    assert got[12:14] == b"\x30\x06"
    # 5 个抓包变体：f5 都必须紧跟 field4(`20 00`) 写出，再跟 field6(`30 06`)
    for f3, f5, _hexs in HYSTAT_CAPTURED:
        got = X._args_hystat(f3=f3, f5=f5)
        assert got.endswith(b"\x30\x06\x38\x00\x40\x02"), got.hex()
        # f5 取值是 0 / 14，都小于 128，单字节 varint
        assert got[:-6].endswith(b"\x20\x00" + bytes([0x28, f5])), (
            "f3=%s f5=%s: f5 没紧跟 field4 写出，尾部是 %s" % (f3, f5, got[-8:].hex()))


def test_ctl_table_matches_handoff():
    """ctl 抄错就是"命令不通"，必须钉住。"""
    assert X.CTL == {"combhq": 0x11, "hqinfo": 0x11, "xmlblock": 0x13,
                     "samplemore": 0x15, "hystat": 0x11, "tickex": 0x11}
    assert X.CMD["xmlblock"] == "HQServ:PBXmlBlock"


# ══════════════════════════════════════════════════════════════════════════
# 二、响应外壳：peel()
# ══════════════════════════════════════════════════════════════════════════
def test_peel_plain_protobuf_is_untouched():
    raw = b"\x10\x01\x1a\x06abc"
    assert X.peel(raw) == raw


def test_peel_json_head():
    assert X.peel(b'{"Length": "4","AttachLog": ""}' + b"\x10\x01\x22\x00") == b"\x10\x01\x22\x00"


def test_peel_zlib_of_base64_of_protobuf():
    """PBCombHQ 6 个代码时就是这三层：JSON 头 → zlib → base64 → protobuf。"""
    proto = b"\x10\x06\x1a\x05hello"
    nested = zlib.compress(base64.b64encode(proto))
    raw = b'{"Length": "999","AttachLog": ""}' + nested
    assert X.peel(raw) == proto


def test_peel_base64_of_protobuf():
    proto = b"\x10\x03\x1a\x04\xe6\xb0\xb4\xe4\xba\xa7"     # field3="水产"
    assert X.peel(b'{"AttachLog": ""}' + base64.b64encode(proto)) == proto


# ══════════════════════════════════════════════════════════════════════════
# 三、解析器对真实响应夹具（2026-10-27 实网抓取）
# ══════════════════════════════════════════════════════════════════════════
def test_combhq_fixture_shape():
    body = _fix("combhq_6代码_响应.bin")
    cols, rows = X.parse_rowtable(body)
    assert len(cols) == 22, cols
    assert cols[:5] == ["Code", "Setcode", "Name", "CLOSE", "OPEN"], cols[:5]
    assert "NOW" in cols and "MAX" in cols and "MIN" in cols
    assert len(rows) == 6, len(rows)
    assert all(len(r) == 22 for r in rows), [len(r) for r in rows]


def test_combhq_fixture_values():
    """具体到数值 —— 只断言"非空"抓不到静默错列。"""
    cols, rows = X.parse_rowtable(_fix("combhq_6代码_响应.bin"))
    d = {r[0]: dict(zip(cols, r)) for r in rows}
    assert d["600519"]["Name"] == "贵州茅台"
    assert d["600519"]["CLOSE"] == "1335.00"
    assert d["000001"]["Name"] == "平安银行"
    assert d["300750"]["Name"] == "宁德时代"
    assert d["688981"]["Name"] == "中芯国际"
    # 880xxx 板块指数：CLOSE 与 get_boards 的 CLOSE 一致（551.40）
    assert d["880903"]["Name"] == "水产品"
    assert d["880903"]["CLOSE"] == "551.40"
    assert d["999999"]["Name"] == "上证指数"


def test_hystat_fixture():
    body = _fix("hystat_f3-1_响应.bin")
    cols, rows = X.parse_rowtable(body)
    assert cols == ["Setcode", "Code", "Name", "Now", "Other", "Close", "XSFlag"], cols
    assert len(rows) >= 10, len(rows)
    # 第一行是**板块本身**（硬件要求：别把第一行当成分股）
    assert rows[0][1] == "880703" and rows[0][2] == "人形机器人", rows[0]
    assert rows[0][5] == "2068.230", rows[0]
    assert all(len(r) == 7 for r in rows)


def test_hystat_market_cell_is_string_code():
    """`Setcode` 是市场号、`Code` 是代码 —— 顺序别弄反（表头是先 Setcode 再 Code）。"""
    cols, rows = X.parse_rowtable(_fix("hystat_f3-1_响应.bin"))
    assert cols[0] == "Setcode" and cols[1] == "Code"
    assert rows[0][0] in ("0", "1", "2"), rows[0][0]
    assert len(rows[0][1]) == 6, rows[0][1]


def test_xmlblock_fixture():
    got = X.parse_block(_fix("xmlblock_行业块_响应.bin"))
    assert got["block"] == "MStock_ZLJX_ADDE_R", got["block"]
    assert got["status"] == 128, got["status"]
    rows = got["rows"]
    assert rows and len(rows) == 3, (len(rows) if rows else None)
    assert rows[0][:3] == ["1", "881218", "汽车零部件"], rows[0][:3]


def test_xmlblock_field6_is_not_row_count():
    """`field6` **不是行数**（交接文档写错了）：夹具里它是 227，而真实只有 3 行。"""
    got = X.parse_block(_fix("xmlblock_行业块_响应.bin"))
    assert got["field6"] == 227, got["field6"]
    assert len(got["rows"]) == 3
    assert got["field6"] != len(got["rows"]), "field6 恰好等于行数的话，请重新核对文档"


def test_samplemore_fixture():
    items = {d["code"]: d for d in X.parse_samples(_fix("samplemore_2代码_响应.bin"))}
    assert set(items) == {"880903", "880703"}, list(items)
    a = items["880903"]
    assert a["count"] == 61, a["count"]
    assert round(a["pre_close"], 3) == 551.400, a["pre_close"]
    assert len(a["floats"]) == 61, len(a["floats"])
    assert [round(x, 2) for x in a["floats"][:3]] == [545.85, 546.25, 547.55]
    assert round(items["880703"]["pre_close"], 3) == 2068.230


def test_hqinfo_fixture_name():
    body = _fix("hqinfo_880903_响应.bin")
    assert X.parse_name(body, "880903") == "水产品"
    # 别把代码本身当成名称
    assert X.parse_name(body, "880903") != "880903"


def test_hqinfo_returns_none_when_no_chinese():
    """没有中文名（空响应）时必须给 None，让上层抛错，而不是返回代码冒充名称。"""
    assert X.parse_name(b"\x10\x01\x1a\x06abc123", "abc123") is None


# ══════════════════════════════════════════════════════════════════════════
# 四、代码规范化：市场号走唯一事实源
# ══════════════════════════════════════════════════════════════════════════
def test_pairs_accepts_three_spellings():
    assert X._pairs("600519") == [(1, "600519")]
    assert X._pairs(["600519", "000001"]) == [(1, "600519"), (0, "000001")]
    assert X._pairs([("sh", "600519")]) == [(1, "600519")]
    assert X._pairs(((2, "920091"),)) == [(2, "920091")]


def test_pairs_market_comes_from_symbols():
    """880xxx 板块 / 900901 沪B / 113711 沪转债 都由 `market_by_code` 定，
    这条命令不自己维护第二份市场表（D1 的教训）。"""
    for code in ("880903", "900901", "113711", "123283", "920091", "688981"):
        assert X._pairs(code)[0][0] == symbols.market_by_code(code), code


# ══════════════════════════════════════════════════════════════════════════
# 五、字段陷阱
# ══════════════════════════════════════════════════════════════════════════
def test_fields_raw_does_not_recurse():
    """平坦解析：字符串载荷不许被当子消息递归（`pb.decode` 会，所以这里自己解）。"""
    payload = b"\x10\x01\x1a\x06399519"        # field3 = "399519"
    got = X.fields_raw(payload)
    assert got[0] == (2, 0, 1), got
    fn, wt, val = got[1]
    assert (fn, wt) == (3, 2) and val == b"399519", got[1]


def test_fields_raw_skips_unknown_wiretype():
    """遇到不认识的 wiretype 要停下，不能死循环。"""
    assert X.fields_raw(b"\x0f" * 0) == []
    got = X.fields_raw(b"\x10\x01\x0b\xff")     # 第二个是 wiretype 3（start group）
    assert got == [(2, 0, 1)], got



# ══════════════════════════════════════════════════════════════════════════
# 七、逐笔 PBTickEx（2026-10-27 打通；夹具来自实网请求/响应）
# ══════════════════════════════════════════════════════════════════════════
#: 抓包里的原始参数体（`cap_tick_1027.pcap`，锚点 `0`，Base64 信封内解出来的 25 B）
TICKEX_HEX = "0a0228001206363030353139180120002a0130306438004001"


def test_tickex_args_byte_identical_to_capture():
    """参数体必须与抓包逐字节一致（少一个字段服务端就回空包）。"""
    body = X._args_tickex("600519", 1, "0", 100)
    assert body == _h(TICKEX_HEX), body.hex()
    assert _fix("tickex_600519_请求.bin") == body
    # 8 个字段一个都不能少
    fields = [f for f, _w, _v in X.fields_raw(body)]
    assert fields == [1, 2, 3, 4, 5, 6, 7, 8], fields


def test_tickex_anchor_only_changes_field5():
    """翻页只动 `field5`（锚点），其余 7 个字段原样 —— 抓包里 6 条请求就是这样。"""
    a = X._args_tickex("600519", 1, "0", 100)
    b = X._args_tickex("600519", 1, "1995", 100)
    assert a != b
    assert b"1995" in b and b"1995" not in a
    # 逐字段比：只有 field5 不同，其余 7 个必须一致
    fa = {f: v for f, _w, v in X.fields_raw(a)}
    fb = {f: v for f, _w, v in X.fields_raw(b)}
    assert sorted(fa) == sorted(fb) == [1, 2, 3, 4, 5, 6, 7, 8], sorted(fa)
    assert fa[5] == b"0" and fb[5] == b"1995", (fa[5], fb[5])
    for k in (1, 2, 3, 4, 6, 7, 8):
        assert fa[k] == fb[k], (k, fa[k], fb[k])


def test_parse_ticks_fixture_cols_rows_span():
    """真实响应夹具：7 列、100 行、区间 `1995,2095`、日期 20261027。"""
    got = X.parse_ticks(_fix("tickex_600519_响应.bin"))
    assert got["cols"] == ["Second", "Now", "NowVol", "VolInStockDiff",
                           "InOutFlag", "HKTTFlag", "HKInOutFlag"], got["cols"]
    assert len(got["rows"]) == 100, len(got["rows"])
    assert got["span"] == "1995,2095", got["span"]
    assert got["anchor"] == (1995, 2095), got["anchor"]
    assert got["date"] == "20261027", got["date"]
    first = got["rows"][0]
    assert first["Second"] == "40978", first
    assert first["Now"] == "1349.130005", first
    assert first["NowVol"] == "2", first
    assert first["VolInStockDiff"] == "2", first
    assert first["InOutFlag"] == "1", first
    assert first["HKInOutFlag"] == "0", first


def test_parse_ticks_all_cells_are_str_and_control_chars_survive():
    """行里**全是字符串**；`HKTTFlag` 会是 `\\x01` 或空串 —— 解析不能炸。"""
    got = X.parse_ticks(_fix("tickex_600519_响应.bin"))
    for row in got["rows"]:
        assert set(row) == set(X.TICKEX_COLS), row
        for k, v in row.items():
            assert isinstance(v, str), (k, type(v), v)
    flags = {row["HKTTFlag"] for row in got["rows"]}
    assert flags <= {"", "\x01"}, flags
    assert "\x01" in flags, "夹具里本来就有 \\x01，别把它洗掉"


def test_tick_from_row_second_is_seconds_of_day():
    """`Second` = **当日秒数** → `HH:MM:SS`（不是序号、不是 HHMMSS）。"""
    row = {"Second": "40978", "Now": "1349.130005", "NowVol": "2",
           "VolInStockDiff": "2", "InOutFlag": "1", "HKTTFlag": b"\x01",
           "HKInOutFlag": "0"}
    tk = _tick_from_row(row, 1, "600519")
    assert tk.time == "11:22:58", tk.time
    assert abs(tk.price - 1349.130005) < 1e-6
    assert tk.vol == 2.0
    assert tk.symbol == "sh600519"
    assert tk.raw["HKTTFlag"] == b"\x01"                  # 原样留 raw，不转
    # 09:30:00 = 34200；拿不到就是不猜
    assert _tick_from_row({"Second": "34200", "Now": "10", "NowVol": "1"},
                          1, "600519").time == "09:30:00"
    assert _tick_from_row({"Now": "10"}, 1, "600519").time is None


def _page(span, seconds, base_price=10.0):
    """造假的一页返回（结构和 `parse_ticks()` 一致）。"""
    start = int(span.split(",")[0])
    rows = [{"Second": str(s), "Now": "%.2f" % (base_price + i), "NowVol": "1",
             "VolInStockDiff": "1", "InOutFlag": "0", "HKTTFlag": "", "HKInOutFlag": "0"}
            for i, s in enumerate(seconds)]
    return {"cols": list(X.TICKEX_COLS), "rows": rows, "span": span,
            "anchor": (start, start + len(rows)), "date": "20261027"}


def test_tick_has_no_invented_defaults():
    """`trade_count` / `bs_flag` 服务端没有 → 必须 `None`，**不许填 0 / 2**。

    阶段 4 判定：两者都进 `models.UNPROVIDED_TICK_FIELDS`（声明式不可用）。
    `PBTickEx` 一行就是一笔，协议里没有「笔数」字段（填 0 会被 `sum()` 当真）；
    `bs_flag=2` 会被用户很自然地读成"卖"。所以默认值本身就得是 `None`
    （本库红线：拿不到就给 None），原因写在 `UNPROVIDED_TICK_FIELDS` 里，机器可读。
    真实方向信息在 `raw["InOutFlag"]`，只是"0 是买还是卖"未定。
    """
    tk = _tick_from_row({"Second": "40978", "Now": "1349.13", "NowVol": "2",
                         "VolInStockDiff": "2", "InOutFlag": "1",
                         "HKTTFlag": "", "HKInOutFlag": "0"}, 1, "600519")
    assert tk.trade_count is None, tk.trade_count
    assert tk.bs_flag is None, tk.bs_flag
    assert tk.raw["InOutFlag"] == "1", "方向原始值只能从 raw 拿"


def test_collect_ticks_next_anchor_is_span_start_not_second():
    """翻页锚点 = 上一页**区间起点**，不是行里的 `Second`（踩过的坑）。"""
    pages = {"0": _page("1995,2095", range(40000, 40005)),
             "1995": _page("1895,1995", range(39990, 39995))}
    used = []

    def fetch(anchor):
        used.append(anchor)
        return pages.get(anchor)

    out = _collect_ticks(fetch, 1, "600519", 8, max_pages=10)
    assert used == ["0", "1995"], used
    assert [t.time for t in out] == sorted(t.time for t in out)      # 升序
    assert len(out) == 8


def test_collect_ticks_dedupes_overlapping_pages_by_row_seq():
    """两页重叠时按**行序号**去重：不重复、也不丢。"""
    p1 = _page("1995,2100", range(40000, 40105))      # 序号 1995..2099
    p2 = _page("1990,2000", range(39995, 40005))      # 序号 1990..1999（与 p1 头部重叠 5 条）
    out = _collect_ticks(lambda a: p1 if a == "0" else p2, 1, "600519", 20, max_pages=3)
    keys = [(t.time, t.price) for t in out]
    assert len(keys) == len(set(keys)), "有重复行"
    # 序号 1995..1999 被 p1 认领，p2 那 5 条丢弃 → 总数 = 105 + 5 = 110 里取最近 20
    assert len(out) == 20


def test_collect_ticks_stops_when_page_is_empty():
    """某页 0 行 → 立刻停，不空转、不重复请求。"""
    calls = []

    def fetch(anchor):
        calls.append(anchor)
        return _page("1995,2005", range(40000, 40010)) if anchor == "0" else {
            "cols": list(X.TICKEX_COLS), "rows": [], "span": "0,0", "anchor": (0, 0)}

    out = _collect_ticks(fetch, 1, "600519", 100, max_pages=50)
    assert len(calls) == 2, calls
    assert len(out) == 10


def test_collect_ticks_respects_max_pages():
    """服务端一直给新页时，`max_pages` 必须兜住（防空转死循环）。"""
    calls = []

    def fetch(anchor):
        calls.append(anchor)
        start = int(anchor) if anchor != "0" else 2000
        return _page("%d,%d" % (start - 10, start), range(40000, 40010))

    out = _collect_ticks(fetch, 1, "600519", 10 ** 6, max_pages=4)
    assert len(calls) == 4, calls
    assert len(out) == 40


def test_collect_ticks_returns_newest_want_in_ascending_time():
    """要"最近 N 笔"：多取的部分从**旧端**裁掉，返回仍是升序。"""
    p1 = _page("1995,2005", range(40000, 40010))      # 新
    p2 = _page("1985,1995", range(39990, 40000))      # 旧
    out = _collect_ticks(lambda a: p1 if a == "0" else p2, 1, "600519", 12, max_pages=3)
    assert len(out) == 12
    assert [t.time for t in out] == sorted(t.time for t in out)
    assert out[-1].time == "11:06:49"                 # 40009 → 11:06:49（最新那条）


# ══════════════════════════════════════════════════════════════════════════
# 八、`parse_rowtable` / `_cells` 的两个已修缺陷（2026-10-28）
#     纯合成字节，不依赖抓包/网络 —— 这两条是**真实数据丢失 bug** 的最小复现。
# ══════════════════════════════════════════════════════════════════════════
def _varint(n: int) -> bytes:
    """无符号 varint（只为拼合成帧，不追求通用）。"""
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _ld(fn: int, payload: bytes) -> bytes:
    """length-delimited 字段（wiretype 2）。"""
    return bytes([(fn << 3) | 2]) + _varint(len(payload)) + payload


def _vk(fn: int, v: int) -> bytes:
    """varint 字段（wiretype 0）。"""
    return bytes([fn << 3]) + _varint(v)


def _cells_msg(items) -> bytes:
    """一个嵌套消息 = 若干个 `field1=<str>`（`_cells` 只看 wiretype，不看字段号）。"""
    return b"".join(_ld(1, s.encode("utf-8")) for s in items)


def _rowtable(col_blocks, rows, f2: int = 5) -> bytes:
    """拼一个 rowtable 帧：`field1` 空 → `field2` 计数 → 若干 `field3` → 若干 `field4`。"""
    out = _ld(1, b"") + _vk(2, f2)
    for cb in col_blocks:
        out += _ld(3, _cells_msg(cb))
    for r in rows:
        out += _ld(4, _cells_msg(r))
    return out


def _raises(exc_type, fn):
    """断言 `fn()` 抛 `exc_type` 并返回异常对象。

    本文件既有风格是不 import pytest（`__main__` 里自己跑用例），
    所以这里不写 `pytest.raises`，两种跑法都能用。
    """
    try:
        fn()
    except exc_type as e:                                          # noqa: BLE001
        return e
    raise AssertionError("应当抛 %s，却没有抛" % exc_type.__name__)


#: 真实故障帧的 8 个列名（`cap_1023_183457.pcap` msgid=150）
_RT_COLS8 = ["Code", "Setcode", "Name", "CLOSE", "NOW", "XSFLAG", "MISCFLAG", "EXT_ZF"]
_RT_ROWS = [
    ["NVDA", "74", "英伟达", "212.210", "213.330", "3", "0", "0.53"],
    ["AMD", "74", "超威半导体", "158.900", "160.120", "3", "0", "0.77"],
    ["MU", "74", "美光科技", "102.400", "103.550", "3", "0", "1.12"],
]


def test_rowtable_ignores_trailing_empty_field3():
    """★ 缺陷 1 的最小复现（`cap_1023_183457.pcap` msgid=150，曾丢 77 行）。

    真实故障帧的 `field3` 出现 **2 次**：第 1 个是 8 个真列名、**第 2 个是末尾空块**。
    旧实现 `if fn == 3: cols = _cells(val)` 取最后一个 → `cols=[]` → 整表丢。
    修法：取**第一个非空**列名块。这里断言列数与行数都不许丢。
    """
    body = _rowtable([_RT_COLS8, []], _RT_ROWS)
    cols, rows = X.parse_rowtable(body)
    assert cols == _RT_COLS8, cols
    assert rows == _RT_ROWS, rows
    assert all(len(r) == len(cols) for r in rows), [len(r) for r in rows]


def test_rowtable_raises_on_multiple_distinct_field3():
    """★ 缺陷 1 的第二半：多个**非空且内容不同**的列名块不许静默挑一个。

    （全量 1496 帧里这种情况出现 **0 次** —— 这是防御性护栏；
    但它一旦出现，静默挑错会把整表按错列切分，比报错危险得多。）
    """
    other = ["Code", "Setcode", "Name", "NOW", "CLOSE"]     # 列序与 _RT_COLS8 不同
    body = _rowtable([_RT_COLS8, other], _RT_ROWS)
    e = _raises(errors.NotSupportedError, lambda: X.parse_rowtable(body))
    msg = str(e)
    assert "多个非空列名块" in msg, msg
    assert "field3" in msg, msg


def test_rowtable_identical_field3_is_treated_as_duplicate():
    """两个非空但**内容相同**的列名块 = 重复，照第一个用，不许误报。"""
    body = _rowtable([_RT_COLS8, list(_RT_COLS8)], _RT_ROWS)
    cols, rows = X.parse_rowtable(body)
    assert cols == _RT_COLS8, cols
    assert rows == _RT_ROWS, rows


def test_rowtable_single_field3_regression():
    """回归：正常单 `field3` 帧仍解出正确 cols/rows（防改坏）。"""
    cols, rows = X.parse_rowtable(_rowtable([_RT_COLS8], _RT_ROWS))
    assert cols == _RT_COLS8, cols
    assert len(rows) == 3, len(rows)
    assert rows[2] == ["MU", "74", "美光科技", "102.400", "103.550", "3", "0", "1.12"], rows[2]


def test_rowtable_still_accepts_field1_rows():
    """`field1` 也当行收（兼容交接文档 §3 的说法）—— 本次改动不许把它弄丢。

    行序：`field1` 的行在前、`field4` 的行在后（既有行为，原样保留）。
    """
    body = (_ld(1, _cells_msg(["999999", "1", "上证指数"]))
            + _vk(2, 1)
            + _ld(3, _cells_msg(["Code", "Setcode", "Name"]))
            + _ld(4, _cells_msg(["600519", "1", "贵州茅台"])))
    cols, rows = X.parse_rowtable(body)
    assert cols == ["Code", "Setcode", "Name"], cols
    assert rows == [["999999", "1", "上证指数"], ["600519", "1", "贵州茅台"]], rows


def test_cells_short_fixed32_does_not_raise():
    """★ 缺陷 2：`fields_raw` 在流被截断时会给出不足 4 字节的 wiretype-5 载荷。

    旧实现直接 `struct.unpack("<f", val)` → 抛 `struct.error` 冒出解析函数，
    把整批 1496 帧的中断在那一条上。现在长度不是 4 就回 hex（不抛、也不丢格，
    否则后面所有列会左移错位）。
    """
    short = bytes([(1 << 3) | 5]) + b"\x00\x80\x3f"          # wiretype 5，只有 3 字节
    assert X.fields_raw(short) == [(1, 5, b"\x00\x80\x3f")], X.fields_raw(short)
    got = X._cells(short)                                     # 关键：不许抛
    assert got == ["00803f"], got


def test_cells_full_fixed32_still_decodes_as_float():
    """长度 == 4 仍按 float32 解（修复不能把正常路径改坏）。"""
    full = bytes([(1 << 3) | 5]) + struct.pack("<f", 1.5)
    assert X._cells(full) == ["1.500"], X._cells(full)



# ══════════════════════════════════════════════════════════════════════════
# 九、堵住「静默产出垃圾」：`parse_ticks` 不许回落 / `parse_samples` 两个命令分开
#     （2026-10-28；假阳性由 M6 用真实帧证出。合成字节，不依赖抓包）
# ══════════════════════════════════════════════════════════════════════════
def _fx(fn: int, value: float) -> bytes:
    """fixed32（wiretype 5）。"""
    return _varint((fn << 3) | 5) + struct.pack("<f", value)


def _f64(fn: int, value: int) -> bytes:
    """fixed64（wiretype 1）。"""
    return _varint((fn << 3) | 1) + struct.pack("<Q", value)


def _packed(xs) -> bytes:
    return b"".join(struct.pack("<f", float(x)) for x in xs)


def _tickex_frame(cols, rows, span=None, date=None) -> bytes:
    """造一个 `PBTickEx` 响应体（**未封壳**，`peel()` 对纯 protobuf 原样返回）。

    `cols=None` 表示**不带 `field7`**（列名块缺失）—— 旧实现正是在这里回落到
    `TICKEX_COLS`，然后照样 zip 出行来。
    """
    out = b""
    if span is not None:
        out += _ld(1, _ld(4, span.encode()))
    out += _vk(2, 1) + _ld(3, b"600519")
    if date is not None:
        out += _vk(4, int(date))
    if cols is not None:
        out += _ld(7, b"".join(_ld(1, c.encode()) for c in cols))
    for r in rows:
        out += _ld(8, b"".join(_ld(1, v.encode()) for v in r))
    return out


def _samples_more_frame(entries) -> bytes:
    """造 `PBSampleMore`：顶层只有重复的 `field1` 包装层。"""
    out = b""
    for code, cnt, pre, floats in entries:
        sub = _ld(3, code.encode()) + _vk(5, cnt)
        if pre is not None:
            sub += _fx(6, pre)
        if floats is not None:
            sub += _ld(8, _packed(floats))
        out += _ld(1, sub)
    return out


def _sample_single_frame(pre=None, declared=240, actual=None, floats=(),
                         f3=None, f12=None) -> bytes:
    """造 `PBSample`：**顶层 `field8` 打包数组 + 无 `field1`**（324 帧实测的形状）。"""
    packed = _packed(floats)
    out = _vk(2, 1) + _ld(3, f3 if f3 is not None else _f64(7, 99999)) + _vk(4, 1)
    out += _vk(5, declared)
    if pre is not None:
        out += _fx(6, pre)
    out += _vk(7, actual if actual is not None else len(floats))
    out += _ld(8, packed)
    if f12 is not None:
        out += _vk(12, f12)
    return out


def _deepwarn_frame(code="002990", cnt=14435) -> bytes:
    """造 `PBDeepWarn`：顶层只有 `field2`（里面恰好有 `field3` 代码 + `field5` 数字）+ 空 `field3`。

    ⚠️ 这正是旧 `parse_samples` 被**错配**成 `{code:'002990', count:14435,
    pre_close:None, floats:[]}` 的那个形状 —— 它根本不是样本帧。
    """
    sub = _ld(3, code.encode()) + _vk(4, 20) + _vk(5, cnt) + _fx(3, 169.7)
    return _ld(2, sub) + _ld(3, b"")


# ── A-1. 缺 `field7`：不许回落到 `TICKEX_COLS` ──────────────────────────────
def test_parse_ticks_without_field7_returns_empty_not_tickex_cols():
    """`field7` 缺失 → **空结果**，绝不拿 `TICKEX_COLS` 顶包。

    旧行为：`cols = list(TICKEX_COLS)`、`rows` 照样 zip → 7 个正经字段名 + 二进制垃圾值。
    那比返回空危险得多（字段名完全正常，下游无从察觉）。
    """
    body = _tickex_frame(None, [["40978", "mLO@", "2"]], span="1995,2096", date=20261027)
    got = X.parse_ticks(body)
    assert got["cols"] == [], got["cols"]
    assert got["rows"] == [], got["rows"]
    # 没有 7 个假列名，也没有任何一行
    assert set(got["cols"]) != set(X.TICKEX_COLS)
    assert len(got["rows"]) == 0
    # 真解出来的元信息照留（不是编的）
    assert got["span"] == "1995,2096" and got["date"] == "20261027"


def test_parse_ticks_with_empty_field7_returns_empty():
    """`field7` 存在但是空块（解不出一列）→ 同样视为解不出。"""
    got = X.parse_ticks(_tickex_frame([], [["1", "2"]], date=20261027))
    assert got["cols"] == [] and got["rows"] == []


# ── A-2. 列名判据：抓住 `PBZST` 那种「列与值整体互换」 ──────────────────────
def test_parse_ticks_rejects_value_as_column_names():
    """「列名」是一堆数值（`PBZST` 的列值互换）→ 不返回数据。

    只有「≥60% 的列名含字母」这条判据能抓住它：列数对得上、宽度也对得上。
    """
    bogus = ["899", "535.000000", "4999.000000", "192061937", "0", "0.000000"]
    row = ["1", "2", "3", "4", "5", "6"]
    got = X.parse_ticks(_tickex_frame(bogus, [row], date=20261027))
    assert got["cols"] == [] and got["rows"] == [], got
    # 反向：把同样的行配上**真列名**就该出来了（说明判据卡的是列名、不是行）
    ok = X.parse_ticks(_tickex_frame(list(X.TICKEX_COLS), [row], date=20261027))
    assert ok["cols"] == list(X.TICKEX_COLS) and len(ok["rows"]) == 1


def test_looks_like_col_names_threshold():
    """判据本身的边界：≥60% 含字母才算列名。"""
    assert X._looks_like_col_names(["Second", "Now"]) is True
    assert X._looks_like_col_names([]) is False
    assert X._looks_like_col_names(["899", "535.0", "0"]) is False
    # 5 个里 3 个含字母 = 60% → 通过
    assert X._looks_like_col_names(["a", "b", "c", "1", "2"]) is True
    # 5 个里 2 个 = 40% → 不通过
    assert X._looks_like_col_names(["a", "b", "1", "2", "3"]) is False


# ── A-3. 零回归：正常 `PBTickEx` 结果逐字段不变 ────────────────────────────
def test_parse_ticks_fixture_result_unchanged_after_no_fallback():
    """真实夹具（实网 100 行）解析结果**一个字节都没变** —— 这是零回归的硬证据。

    改的只是「解不出列名」那条分支；能解出列名的路径原样不动。
    """
    got = X.parse_ticks(_fix("tickex_600519_响应.bin"))
    assert got["cols"] == ["Second", "Now", "NowVol", "VolInStockDiff",
                           "InOutFlag", "HKTTFlag", "HKInOutFlag"]
    assert len(got["rows"]) == 100
    assert got["span"] == "1995,2095" and got["anchor"] == (1995, 2095)
    assert got["date"] == "20261027"
    assert got["rows"][0]["Now"] == "1349.130005"
    # 结构键不变（调用方按这几个 key 取值）
    assert sorted(got) == ["anchor", "cols", "date", "rows", "span"]


def test_parse_ticks_synthetic_tickex_still_parses():
    """合成帧（有 `field7`）也照常出 —— 证明不是「一律返回空」。"""
    cols = ["Second", "Now", "NowVol"]
    body = _tickex_frame(cols, [["40978", "1349.13", "2"]], span="7,8", date=20261027)
    got = X.parse_ticks(body)
    assert got["cols"] == cols
    assert got["rows"] == [{"Second": "40978", "Now": "1349.13", "NowVol": "2"}]
    assert got["anchor"] == (7, 8)


# ── B-1. `PBDeepWarn` 必须返回 `[]`，不许编造 code ─────────────────────────
def test_parse_samples_deepwarn_returns_empty():
    """`PBDeepWarn` → `[]`。

    旧实现遍历「任何顶层子消息」，于是把这个 `field2`（内含 `field3='002990'` +
    `field5=14435`）当成一条样本，产出 `{code:'002990', count:14435,
    pre_close:None, floats:[]}` —— 一个**看起来完全正常**的假记录。
    """
    body = _deepwarn_frame("002990", 14435)
    assert X.parse_samples(body) == []
    assert X.parse_samples_more(body) == []
    assert X.parse_sample_single(body) == []


def test_parse_samples_deepwarn_does_not_fabricate_code():
    """把旧假记录的具体特征钉死：不许出现 `code='002990'`。"""
    got = X.parse_samples(_deepwarn_frame("002990", 14435))
    assert [r.get("code") for r in got] == [], got


def test_parse_samples_rowtable_shape_returns_empty():
    """`PBHYStat`/`PBCombHQ` 那种 rowtable 形状（`field3`+`field4`）→ `[]`。"""
    body = _rowtable([["A", "B"]], [["1", "2"]])
    assert X.parse_samples(body) == []


# ── B-2. 拆分方案：两套形状各一条正例 ─────────────────────────────────────
def test_parse_samples_more_positive():
    """`PBSampleMore`：顶层 `field1` 包装层 → 每个标的 1 条。"""
    body = _samples_more_frame([
        ("999999", 60, 3658.026, [3623.567, 3610.925, 3606.588]),
        ("000001", 60, 11.15, [11.2, 11.19]),
    ])
    got = X.parse_samples_more(body)
    assert len(got) == 2, got
    assert got[0]["code"] == "999999" and got[0]["count"] == 60
    assert got[0]["pre_close"] == 3658.026
    assert got[0]["floats"] == [3623.567, 3610.925, 3606.588]
    assert got[1]["code"] == "000001" and got[1]["pre_close"] == 11.15
    # 分发器走同一条路
    assert X.parse_samples(body) == got


def test_parse_sample_single_positive():
    """`PBSample`：顶层 `field8`、无 `field1` → 1 条，`code` 如实为 `None`。

    这个响应里**没有 6 位代码**（`field3` 是子消息，装的是 5 位序数），所以不编造。
    """
    floats = [3622.798, 3619.24, 3610.925]
    body = _sample_single_frame(pre=3658.026, declared=240, actual=3, floats=floats)
    got = X.parse_sample_single(body)
    assert len(got) == 1, got
    rec = got[0]
    assert rec["code"] is None                     # 响应里没有代码 → 不编造
    assert rec["count"] == 3                       # 用 field7（实际个数）
    assert rec["declared_count"] == 240            # field5 是声明值，另存
    assert rec["pre_close"] == 3658.026
    assert rec["floats"] == floats
    assert X.parse_samples(body) == got


def test_sample_parsers_are_mutually_exclusive():
    """两套形状互不误吃：More 的帧不会被单只解析器吃，反之亦然。"""
    more = _samples_more_frame([("999999", 60, 3658.026, [1.0, 2.0])])
    single = _sample_single_frame(pre=11.15, actual=2, floats=[11.2, 11.19])
    assert X.parse_sample_single(more) == []
    assert X.parse_samples_more(single) == []
    # 但分发器对两者都认
    assert len(X.parse_samples(more)) == 1
    assert len(X.parse_samples(single)) == 1


def test_parse_sample_single_rejects_truncated_packed_array():
    """`field8` 长度不是 4 的倍数（截断）→ 认不出，返回 `[]`，不许硬解。"""
    body = _vk(5, 240) + _fx(6, 11.15) + _vk(7, 2) + _ld(8, b"\x00\x00\x80")
    assert X.parse_sample_single(body) == []
    assert X.parse_samples(body) == []


def test_parse_samples_does_not_peel():
    """`parse_samples` 系列**不吃壳**（只有 `parse_ticks` 内部 peel）—— 契约写清楚。"""
    body = _samples_more_frame([("999999", 60, 3658.026, [1.0])])
    shell = b'{"AttachLog": ""}' + base64.b64encode(body)   # 真实外壳写法（见 test_peel_*）
    assert X.parse_samples(shell) == []          # 传壳进去解不出东西
    assert X.parse_samples(X.peel(shell)) != []  # 调用方负责先 peel


def _required_fixtures(fn):
    """这个用例**必需**哪些 pytest fixture（没有默认值的形参就是 fixture）。

    独立 runner 无参调 `fn()`，所以带 fixture 的用例在这里跑不了。以前是「直接调 → TypeError
    → 算失败」，把「环境不提供」误报成「用例坏了」；现在按签名识别出来、记 SKIP 并说明原因。
    """
    out = []
    for p in inspect.signature(fn).parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if p.default is inspect.Parameter.empty:
            out.append(p.name)
    return out


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    skipped = []
    for name, fn in fns:
        need = _required_fixtures(fn)
        if need:
            skipped.append((name, need))
            print("  ⏭  %s: SKIP(fixture) —— 需要 pytest fixture（%s），独立 runner 不提供"
                  % (name, ", ".join(need)))
            continue
        try:
            fn()
            print("  ✅ %s" % name)
        except AssertionError as e:
            bad += 1
            print("  ❌ %s: %s" % (name, e))
        except Exception as e:                                     # noqa: BLE001
            bad += 1
            print("  ❌ %s: %s: %s" % (name, type(e).__name__, e))
    print("\n%d 个用例，失败 %d，跳过 %d" % (len(fns), bad, len(skipped)))
    if skipped:
        print("\n跳过明细（**原因都相同**：需要 pytest fixture，独立 runner 不提供；"
              "在 `python -m pytest` 里照常执行，不算覆盖缺口）：")
        for name, need in skipped:
            print("  ⏭  %s ← 需要 fixture: %s" % (name, ", ".join(need)))
    sys.exit(1 if bad else 0)
