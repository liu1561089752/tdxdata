# -*- coding: utf-8 -*-
"""`ctlmap` 的离线测试：只查表、不联网。"""
import json

from tdxdata._engine import ctlmap


def test_stats_shape():
    s = ctlmap.stats()
    assert s["total"] > 500, s
    assert s["with_params"] > 400, s
    assert s["verified"] > 400, s
    assert set(s["forms"]) <= {"base", "call", "bare", "obj"}


def test_known_ctls():
    # 实测值：错一个字节都会回空包，所以这几个必须钉死
    assert ctlmap.ctl_for("CWServ.ph_agf10_gsjj") == 0x16
    assert ctlmap.ctl_for("CWServ.ph_zt_kp_xgb_kxt") == 0x19
    assert ctlmap.ctl_for("CWServ.ph_datacenter_mghg_xf") == 0x1E
    assert ctlmap.ctl_for("HQServ.hq_nlp_misc") is not None


def test_unknown_returns_none():
    assert ctlmap.ctl_for("CWServ.不存在_的_命令") is None
    assert ctlmap.lookup("CWServ.不存在_的_命令") is None
    assert ctlmap.params_for("CWServ.不存在_的_命令") is None
    # 未知命令按 base 形态编码，不抛异常
    assert ctlmap.form_for("CWServ.不存在_的_命令") == "base"


def test_body_forms():
    # base：Params 包裹
    b = ctlmap.build_body("CWServ.ph_datacenter_mghg_xf", ["001", "", "-1", "1"])
    assert json.loads(b) == {"Params": ["001", "", "-1", "1"]}
    # call：必须带 CallName，否则服务端静默丢包
    b = ctlmap.build_body("SNS.query_Recommend", [])
    assert json.loads(b) == {"CallName": "query_Recommend", "Params": []}
    # bare：裸 JSON
    rec = ctlmap.lookup("HQServ.hq_nlp_misc")
    if rec and rec.get("form") == "bare":
        b = ctlmap.build_body("HQServ.hq_nlp_misc", [{"ReqId": "200800"}])
        assert json.loads(b) == [{"ReqId": "200800"}]


def test_build_body_uses_template_when_params_none():
    rec = ctlmap.lookup("CWServ.ph_agf10_gsjj")
    assert rec and "params" in rec
    b = ctlmap.build_body("CWServ.ph_agf10_gsjj")
    assert json.loads(b)["Params"] == rec["params"]


def test_fill_replaces_placeholders_without_mutating():
    tpl = ["00101", "@code@", "#date#", "#curPage#"]
    out = ctlmap.fill(tpl, code="600519", date="2026-10-30")
    assert out == ["00101", "600519", "2026-10-30", "1"]
    assert tpl == ["00101", "@code@", "#date#", "#curPage#"], "不得改动原模板"
    # 不传 date 时留空
    assert ctlmap.fill(["@code@", "#date#"], code="000001") == ["000001", ""]


def test_names_prefix():
    sns = ctlmap.names("SNS.")
    assert sns and all(n.startswith("SNS.") for n in sns)
    assert ctlmap.names("不存在的命名空间.") == []


def test_every_verified_has_ctl():
    for n in ctlmap.names():
        rec = ctlmap.lookup(n)
        assert isinstance(rec["ctl"], int)
        assert 0 <= rec["ctl"] <= 0xFF
