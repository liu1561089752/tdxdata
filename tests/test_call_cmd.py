# -*- coding: utf-8 -*-
r"""`Client.call_cmd()` 的契约守卫（**全离线**）。

守五件事
--------
1. **三种请求体形态真的到位**：`base` → `{"Params": …}` / `bare` → 裸 JSON /
   `call` → 目录声明 `{"CallName": …}`。比对的是**真正送进 `ask()` 的字节**，
   不是"返回值看着对"。
2. **`ctl` 由目录给**：调用方不必知道 ctl（`ctl_for('HQServ.ZST') == 12`），
   实际发出的 `(cmd, ctl)` 与目录逐条一致。
3. **占位符**：`code=` / `date=` 落进模板；`params=` 一给，模板**完全不参与**。
4. **三档状态不是三个新异常类**：`probe_ok` 档 0 行仍是 `EmptyResult`
   （`type()` 相同），只是消息补上"接口可达、不是命令坏"；`verified` 档消息不变。
5. **不在目录的命令**抛 `NotSupportedError`，并给同前缀的相近命令提示。

红线：`tests/test_*.py` 不许出现 `Client(` / `Session(` / `AsyncClient(` 字面量
（`tools/gate.py` 会扫）。所以裸对象一律 `getattr(tdxdata, "Client").__new__(cls)`
造，`Session` 那一层用假对象顶替 —— **零真实网络**。
"""
from __future__ import annotations

import json
import sys
import threading

import pytest

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                        # noqa: BLE001
    pass

import tdxdata
from tdxdata._engine import ctlmap
from tdxdata._engine import session as session_mod

_CLS = getattr(tdxdata, "Client")           # 红线：不写 `Client(` 字面量
errors = tdxdata.errors

#: 一份**能过 `session.parse()`** 的最小 ResultSets 回包（1 列 1 行）。
_PASS_REPLY = b'{"ResultSets":[{"ColName":["N001"],"Content":[["20261027"]]}]}'
#: 有列无行 = 当期无数据（`parse()` 判 `EMPTY_RS`）→ `call()` 抛 `EmptyResult`。
_EMPTY_REPLY = b'{"ResultSets":[{"ColName":["N001"],"Content":[]}]}'


class _FakeSession:
    """假长连接：只实现 `ask(ctl, cmd, args)`，把**真正发出去的参数**记下来。

    这是**最底层**的桩（`client.call()` 里 `self._ensure().ask(...)` 那一层），
    往上 `body_of` / `parse` / `_make_table` / `_stamp_table` 全是真代码。
    """

    host = "fake-host:7615"
    alive = True

    def __init__(self, reply=_PASS_REPLY):
        self.calls = []                     # [(ctl, cmd, args_bytes), ...]
        self._reply = reply

    def ask(self, ctl, cmd, args, idle=4.0):
        self.calls.append((ctl, cmd, args))
        return self._reply

    def reconnect(self):
        pass

    def switch_host(self):
        pass

    def close(self):
        pass


class _FakePacer:
    """零延迟闸门替身（只替掉"睡 2.2 秒"，不碰限流判定）。"""

    limited_events = 0
    gap = 2.2

    def wait(self):
        pass

    def note_ok(self):
        pass

    def note_limited(self):
        return 0.0

    def stats(self):
        return {}


def _client(reply=_PASS_REPLY):
    """离线客户端：跑过字段初始化、**不建连**（`_sess` 是假对象）。"""
    c = _CLS.__new__(_CLS)
    c._probe_allowed = False
    c._clock_probe = False
    c._clock = None
    c._last_call = {}
    c._in_cal = False
    c._cal = {}
    c._cache = None
    c._products = {}
    c._pb_products = {}
    c._pacer = _FakePacer()
    c._sess = _FakeSession(reply)
    c._ensure_lock = threading.Lock()
    c._host = None
    c._timeout = 1.0
    c._gap = 0.0
    c._session_mod = session_mod
    return c


def _only_call(c):
    """取出唯一一次 `ask()` 的 `(ctl, cmd, args)`；多/少都是夹具坏了。"""
    assert len(c._sess.calls) == 1, "应当只发一次请求，实际 %d 次" % len(c._sess.calls)
    return c._sess.calls[0]


# ══════════════════════════════════════════════════════════════════════════
# 一、三种请求体形态：断言"真正送出去的那几个字节"
# ══════════════════════════════════════════════════════════════════════════
def test_base_form_sends_params_envelope():
    """`base` 形态（大多数 `CWServ.*`）：`{"Params": [...]}`，参数取目录模板。"""
    c = _client()
    c.call_cmd("CWServ.ph_agf10_gsjj")
    ctl, cmd, args = _only_call(c)
    assert (ctl, cmd) == (22, "CWServ.ph_agf10_gsjj")
    assert args == b'{"Params": ["00201", "600303", "0"]}'


def test_bare_form_sends_raw_json_without_envelope():
    """`bare` 形态（`HQServ.*`）：**裸 JSON**，一个 `Params` 包装都不许有。

    顺带守住"目录里 25 条命令的 `params` 是**抓包元数据**"这件事：`call_cmd` 必须
    拿到**一次真实请求的 body**（`ctlmap.params_best_for` 负责），而不是把
    `args_hex` / `pcap` 那些元数据塞进帧里。
    ⚠️ 不比字节、比**语义**：抓包原文是紧凑格式且按键的原顺序
    （`{"Code":"HSI","Setcode":27,…}`），而参数现在来自 `verified.json`
    （标准 `json.dumps` 空格 + 字母序键）。JSON 对象无序，故按解析后的值比对；
    形状（是否带 `Params` 包装）仍逐字节比。
    """
    c = _client()
    c.call_cmd("HQServ.ZST")
    ctl, cmd, args = _only_call(c)
    assert (ctl, cmd) == (12, "HQServ.ZST")
    assert json.loads(args.decode()) == {
        "Code": "HSI", "Setcode": 27, "HasAttachInfo": 1,
        "Period": "", "Date": "20261023", "ExHQFlag": 1,
    }
    assert args.startswith(b'{"Code"'), "裸发：报文应以对象开头，实际 %r" % args[:40]
    assert b'"Params"' not in args, "`bare` 形态不许套 Params 包装"


def test_call_form_matches_ctlmap_build_body():
    """`call` 形态：目录（单一真相）声明的是 `{"CallName": …, "Params": …}`。

    ⚠️ 帧里的包装由 **session 层**决定，不是本层：对**不在 19 条双源实测**里的
    命令，`body_of` 走的是**前缀规则兜底**（SPEC §1.3 冻结的既有行为），所以
    `SNS.query_Recommend` 今天实际发出去的是 `{"Params": []}`。这里把两边都钉住：
    · 目录口径 = `call`（`form_for` / `build_body`）；
    · 今天真发出去的字节 = 前缀规则兜底的结果。
    哪天 session 层改成"按 `form_for` 包装"，这条会红 —— 那是**该红**的。
    """
    cmd = "SNS.query_Recommend"
    c = _client()
    c.call_cmd(cmd)
    ctl, sent_cmd, args = _only_call(c)
    assert (ctl, sent_cmd) == (20, cmd)
    # ① 目录口径：`call` 形态（`build_body` 与 `form_for` 是同一张表）
    assert ctlmap.form_for(cmd) == "call"
    assert ctlmap.build_body(cmd, []) == b'{"CallName": "query_Recommend", "Params": []}'
    assert ctlmap.form_measured_for(cmd) is None      # 不在 19 条双源实测里
    # ② 实际帧：前缀规则兜底（未实测命令的行为一字未改）
    assert args == b'{"Params": []}'


def test_capture_metadata_uses_the_captured_body_not_the_whole_list():
    """25 条"抓包元数据"命令：拿到的是**一次真实请求的 body**，不是"所有抓包的数组"。

    背景（2026-10-31 修）：`ctlmap.params_best_for` 最初对这 4 条
    （`HQServ.ZST` / `CWSearch.tdx_pv` / `CWSearch.tzx_rcache` /
    `CWServ.tzx_filedown`）返回 `verified.json` 里**"每次抓包一个元素"的列表**
    （2~6 个 dict）。整表交给 `body_of` 会序列化成 JSON **数组** `[{…},{…}]` ——
    与任何一次真实请求都不等。M1 已按 "len>1 且全为 dict → 只取第一个" 修好。
    这条从现在起守的是**修好后的形状**：返回值是单个 body（`dict`），`body_of`
    以 `{` 开头。
    """
    for cmd in ("HQServ.ZST", "CWSearch.tdx_pv", "CWSearch.tzx_rcache",
                "CWServ.tzx_filedown"):
        tpl = ctlmap.params_best_for(cmd)
        assert isinstance(tpl, dict), "%s 的 params_best_for 应是单个 body，实际 %r" % (
            cmd, type(tpl))
        body = session_mod.body_of(cmd, tpl)
        assert body.startswith(b"{"), "%s 的报文应以对象开头，实际 %r" % (cmd, body[:24])
    c = _client()
    c.call_cmd("HQServ.ZST")
    args = _only_call(c)[2]
    assert args.startswith(b'{"Code"'), "实际发出去的是 %r" % args[:40]


def test_querycardinfo_keeps_its_single_element_positional_list():
    """反向守卫：修"只取第一个"时**不许**把合法的单元素位置参数也砍掉。

    `DataAggregation.queryCardInfo` 的 `[{"stockCode": …}]` 是**位置参数数组**
    （len==1），不是"抓包 body 列表"—— 砍成 dict 会改变参数位置语义。
    """
    q = ctlmap.params_best_for("DataAggregation.queryCardInfo")
    assert isinstance(q, list) and len(q) == 1, "queryCardInfo 的位置参数被砍了：%r" % (q,)
    assert isinstance(q[0], dict)


# ══════════════════════════════════════════════════════════════════════════
# 二、ctl 由目录给（不必调用方知道）
# ══════════════════════════════════════════════════════════════════════════
def test_ctl_comes_from_catalog_not_from_caller():
    """`ctl_for` 给出的 ctl 就是实际发出去的那个 —— 这几条的代表值写死。"""
    assert ctlmap.ctl_for("HQServ.ZST") == 12
    expected = {
        "HQServ.ZST": 12,
        "CWServ.ph_agf10_gsjj": 22,
        "SNS.query_Recommend": 20,
    }
    for cmd, want_ctl in expected.items():
        c = _client()
        c.call_cmd(cmd)
        ctl, sent_cmd, _args = _only_call(c)
        assert ctl == want_ctl, "%s 的 ctl 应为 %d，实际 %r" % (cmd, want_ctl, ctl)
        assert sent_cmd == cmd


# ══════════════════════════════════════════════════════════════════════════
# 三、占位符：code= / date= 填模板；params= 覆盖模板
# ══════════════════════════════════════════════════════════════════════════
_PH_CMD = "CWServ.ph_tdxdatacenter_tjcb_cwfx"        # 模板 ["00102", "@code@", "#date#"]


def test_code_and_date_fill_template_placeholders():
    c = _client()
    c.call_cmd(_PH_CMD, code="600519", date="2026-10-30")
    _ctl, _cmd, args = _only_call(c)
    assert b"@code@" not in args and b"#date#" not in args, "占位符没被替换：%r" % args
    assert args == b'{"Params": ["00102", "600519", "2026-10-30"]}'


def test_explicit_params_wins_and_template_is_untouched():
    """`params=` 一给：模板（含它的占位符）**完全不参与**，code=/date= 也不参与。"""
    c = _client()
    c.call_cmd(_PH_CMD, code="600519", date="2026-10-30",
               params=["00102", "000001", "20261030"])
    _ctl, _cmd, args = _only_call(c)
    assert args == b'{"Params": ["00102", "000001", "20261030"]}'
    assert b"600519" not in args, "显式 params 被 code= 改写了"
    assert b"2026-10-30" not in args, "显式 params 被 date= 改写了"


def test_extra_fills_the_rest_of_the_placeholders():
    """`extra=` 填 `code=`/`date=` 管不到的占位符（键写占位符原文）。"""
    tpl = ctlmap.params_for(_PH_CMD)
    filled = ctlmap.fill(tpl, code="1", date="2", extra={"#date#": "2026-10-30 09:30:00"})
    assert filled == ["00102", "1", "2026-10-30 09:30:00"]


def test_template_none_means_empty_params():
    """目录里没有参数模板的命令 → 参数就是空（不是把 `None` 塞进帧里）。

    `ACL.checkuser`：目录里有 ctl（15）/ 形态 `base`，但 `params` 是 `None`
    —— 这类命令（732 条里有 215 条）本来就不收参数。
    """
    cmd = "ACL.checkuser"
    assert ctlmap.params_for(cmd) is None, "夹具假设变了：这条命令有参数模板了"
    assert ctlmap.ctl_for(cmd) == 15
    c = _client()
    c.call_cmd(cmd)
    ctl, sent_cmd, args = _only_call(c)
    assert ctl == 15 and sent_cmd == cmd
    assert json.loads(args.decode()) == {"Params": []}


# ══════════════════════════════════════════════════════════════════════════
# 四、不在目录的命令：本地报错 + 相近命令提示
# ══════════════════════════════════════════════════════════════════════════
def test_unknown_command_raises_with_near_hints_and_sends_nothing():
    c = _client()
    with pytest.raises(errors.NotSupportedError) as ei:
        c.call_cmd("CWServ.ph_definitely_not_a_command")
    msg = str(ei.value)
    assert "不在库的命令目录里" in msg
    assert "tdxdata.ctlmap.names()" in msg                 # 自查入口必须写在消息里
    assert "同前缀" in msg                                  # 相近命令提示
    near = ctlmap.names("CWServ.")[:5]
    assert any(n in msg for n in near), "提示里没有一条真的目录命令：%r" % msg
    assert c._sess.calls == [], "命令不在目录却发了请求"


def test_unknown_command_hint_is_capped_at_five():
    c = _client()
    with pytest.raises(errors.NotSupportedError) as ei:
        c.call_cmd("CWServ.nope")
    msg = str(ei.value)
    hinted = [n for n in ctlmap.names("CWServ.") if n in msg]
    assert 0 < len(hinted) <= 5, "相近命令提示应 ≤ 5 条，实际 %d 条" % len(hinted)


# ══════════════════════════════════════════════════════════════════════════
# 五、三档状态：`probe_ok` 补话、`verified` 原样、异常类不变
# ══════════════════════════════════════════════════════════════════════════
_PROBE_OK_CMD = "CWServ.ph_agf10_rdtc"       # tier_for == "probe_ok"
_VERIFIED_CMD = "CWServ.ph_agf10_gsjj"       # tier_for == "verified"


def test_probe_ok_zero_rows_keeps_class_but_explains_reachable():
    """`probe_ok` 档 0 行：**同一个 `EmptyResult` 类**，消息补上"接口可达"。"""
    assert ctlmap.tier_for(_PROBE_OK_CMD) == "probe_ok", "夹具假设变了：tier 不是 probe_ok"
    c = _client(reply=_EMPTY_REPLY)
    with pytest.raises(errors.EmptyResult) as ei:
        c.call_cmd(_PROBE_OK_CMD)
    assert type(ei.value) is errors.EmptyResult, "不许新造异常类"
    msg = str(ei.value)
    assert "接口可达" in msg
    assert "不是命令坏" in msg
    assert len(c._sess.calls) == 1, "probe_ok 档应当真的发了一次请求"


def test_verified_zero_rows_message_is_unchanged():
    """`verified` 档 0 行：**不补话**（只说明"有列无行"，别把判断加戏）。"""
    assert ctlmap.tier_for(_VERIFIED_CMD) == "verified", "夹具假设变了：tier 不是 verified"
    c = _client(reply=_EMPTY_REPLY)
    with pytest.raises(errors.EmptyResult) as ei:
        c.call_cmd(_VERIFIED_CMD)
    msg = str(ei.value)
    assert "有列无行" in msg
    assert "接口可达" not in msg


def test_probe_ok_allow_empty_returns_empty_table():
    """`allow_empty=True` 时 0 行不抛 —— 走 `call()` 的既有语义（本方法不另写一条）。"""
    c = _client(reply=_EMPTY_REPLY)
    table = c.call_cmd(_PROBE_OK_CMD, allow_empty=True)
    assert isinstance(table, tdxdata.Table)
    assert table.rows == []
    assert table.cmd == _PROBE_OK_CMD
