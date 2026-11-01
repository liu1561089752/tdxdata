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


# ---------------------------------------------------------------------------
# 以下为 M1/T5a 追加：三档状态、双源实测形态、占位符补全。
# 上面的用例一条都没改；新增内容只追加在文件末尾。
# ---------------------------------------------------------------------------
import os


def _sidecar(name):
    """读 `tdxdata/_engine/catalog/<name>` 的原始 JSON（测试里直读文件对账用）。"""
    base = os.path.dirname(os.path.abspath(ctlmap.__file__))
    with open(os.path.join(base, "catalog", name), encoding="utf-8") as f:
        return json.load(f)


def test_fill_extra_overrides_defaults():
    # docstring 里的示例（调用方显式给业务值）
    out = ctlmap.fill(["00102", "1", "#name1#", "#name2#"],
                      extra={"#name1#": "中国平安", "#name2#": "深圳"})
    assert out == ["00102", "1", "中国平安", "深圳"]
    assert ctlmap.fill(["003", "@hycode@", ""],
                       extra={"@hycode@": "010301"}) == ["003", "010301", ""]
    # extra 压过 code / date 算出来的值
    assert ctlmap.fill(["@code@", "#date#"], code="600519", date="2026-10-30",
                       extra={"#date#": "20261030"}) == ["600519", "20261030"]
    # None 当空串；未提到的键仍走缺省表
    assert ctlmap.fill(["#flag#", "#curPage#"],
                       extra={"#flag#": None}) == ["", "1"]
    # 嵌套 dict/list 也要替换
    out = ctlmap.fill([{"Code": "#subTypeCode#", "More": ["@name@"]}],
                      extra={"#subTypeCode#": "880301", "@name@": "某板块"})
    assert out == [{"Code": "880301", "More": ["某板块"]}]


def test_fill_date_family_all_follow_date_argument():
    """日期族 7 个键与 `#date#` 完全同语义：走 `date`，不给就空串。"""
    assert set(ctlmap.DATE_PLACEHOLDER) == {
        "#date#", "@date@", "#Date#", "#dateTime#", "#endDate#", "#today#", "#stockDay#"}
    for tok in ctlmap.DATE_PLACEHOLDER:
        assert ctlmap.fill([tok], date="2026-10-30") == ["2026-10-30"], tok
        assert ctlmap.fill([tok]) == [""], tok
    # 真实命令：以前 `#dateTime#` / `#endDate#` / `#today#` / `#stockDay#` 全是裸发
    for cmd in ("CWServ.ph_zt_lhb_sy_lhfp", "CWServ.ph_hkdatacenter_wlnx_lzzdscj",
                "CWServ.ph_bigdata_xtld_zdsl", "HQServ.hq_nlp_jjqc",
                "CWServ.phzzfw_dxb"):
        out = json.dumps(ctlmap.fill(ctlmap.params_for(cmd), code="600519",
                                     date="2026-10-30"), ensure_ascii=False)
        for tok in ctlmap.DATE_PLACEHOLDER:
            assert tok not in out, (cmd, tok, out)


def test_setCode_and_setcode_are_distinct_keys_both_replaced():
    """`#setCode#`（大写 C）与 `#setcode#`（小写）是两个键，都要能换、语义都是市场代码。"""
    assert ctlmap.PLACEHOLDER["#setcode#"] == "1"
    assert ctlmap.PLACEHOLDER["#setCode#"] == "1"
    assert ctlmap.fill(["#setcode#", "#setCode#"], code="600519") == ["1", "1"]
    # 唯一用 `#setCode#` 的命令：HQServ.hq_nlp_xg 的 "Setcode" 字段
    rec = ctlmap.fill(ctlmap.params_for("HQServ.hq_nlp_xg"),
                      code="600519", date="2026-10-30")
    assert rec[0]["Setcode"] == "1"
    assert "#" not in json.dumps(rec, ensure_ascii=False)


def test_caller_provided_placeholders_default_to_empty_not_fabricated():
    """必须由调用方给的 13 个键默认空串 —— 不许编一个"看起来合理"的数值。"""
    required = ["@name@", "#name1#", "#name2#", "@hycode@", "@qsid@", "@jgid@",
                "@cstype@", "#entrycode#", "#subTypeCode#", "#SelfStock#",
                "#flag#", "#sortNum#", "#sortType#"]
    for tok in required:
        assert ctlmap.PLACEHOLDER[tok] == "", tok
        assert ctlmap.fill([tok]) == [""], tok
    # 反例钉死：`#sortNum#` 绝不能被悄悄填成 "1" 之类
    assert ctlmap.fill(ctlmap.params_for("CWServ.ph_zt_lhb_sy_yzycbd"),
                       code="600519", date="2026-10-30")[2:4] == ["", ""]


def test_tier_for_four_states():
    """四态：dead / verified / probe_ok / None，顺序按拍板（正向证据优先）。"""
    assert ctlmap.TIERS == ("dead", "verified", "probe_ok")
    assert ctlmap.tier_for("HQServ.ZST") == "verified"
    assert ctlmap.tier_for("CWSearch.search") == "dead"
    assert ctlmap.tier_for("CWServ.phzzfw_tsyb") == "probe_ok"
    assert ctlmap.tier_for("ACL.checkuser") is None          # 在表里但三档都没证据
    assert ctlmap.tier_for("CWServ.不存在_的_命令") is None
    assert ctlmap.tier_for("") is None
    assert ctlmap.tier_for(None) is None

    counts = {}
    for n in ctlmap.names():
        counts[ctlmap.tier_for(n)] = counts.get(ctlmap.tier_for(n), 0) + 1
    assert counts.get("verified") == 421, counts
    assert counts.get("dead") == 184, counts
    assert counts.get("probe_ok") == 44, counts
    assert counts.get(None) == 732 - 421 - 184 - 44, counts


def test_tier_positive_evidence_beats_dead():
    """47 条冲突（rows>0 却在 not_in_app）一律归 verified，不许被判死。"""
    verified = _sidecar("verified.json")
    dead = _sidecar("dead_cmds.json")
    assert not (set(verified) & set(dead)), "verified 与 dead 必须互斥（正向证据优先）"
    # 冲突清单里的已知成员：必须有数据那一档
    assert ctlmap.tier_for("CWServ.ph_agf10_hypm") == "verified"


def test_form_measured_for_has_no_fallback():
    """20 条双源实测有值；其余一律 None —— **绝不回退 "base"**。"""
    assert ctlmap.form_measured_for("HQServ.hq_nlp_misc") == "bare"
    assert ctlmap.form_measured_for("AppUpdateInfo.queryAppUpdateInfo") == "bare"
    assert ctlmap.form_measured_for("CWServ.tdx_vote") == "bare"
    assert ctlmap.form_measured_for("CWServ.ph_tdxdatacenter_zttz_zy") == "base"
    # 没双源实测 → None（哪怕 ctl_map 里有 form 字段、哪怕单源 body_form 给了值）
    assert ctlmap.form_measured_for("HQServ.ZST") is None
    assert ctlmap.form_measured_for("HQServ.hq_nlp_indexselect") is None
    assert ctlmap.form_measured_for("CWServ.不存在_的_命令") is None
    # form_for 的行为一字未改：未知仍按 base
    assert ctlmap.form_for("HQServ.ZST") == "base"
    assert ctlmap.form_for("CWServ.不存在_的_命令") == "base"


def test_stats_keeps_flag_semantics_and_adds_evidence_counts():
    s = ctlmap.stats()
    # 原有键一个不少、口径不变
    assert s["total"] == 732
    assert s["with_params"] == 517
    assert s["verified"] == 537          # ctl_map 的布尔标记（历史口径）
    assert set(s["forms"]) <= {"base", "call", "bare", "obj"}
    assert s["forms"] == {"base": 718, "call": 11, "bare": 3}
    # 新增两个证据计数，与布尔标记分开口径
    assert s["verified_rows"] == 421
    assert s["dead"] == 184
    assert s["form_measured"] == 20


def test_catalog_evidence_files_shape():
    """三份证据文件的条数与字段形状（交付判据：421 / 184 / 20）。"""
    verified = _sidecar("verified.json")
    dead = _sidecar("dead_cmds.json")
    measured = _sidecar("form_measured.json")

    assert len(verified) == 421
    assert len(dead) == 184
    assert len(measured) == 20

    for cmd, rec in verified.items():
        assert set(rec) == {"rows", "cols", "params", "checked_at"}, (cmd, sorted(rec))
        assert isinstance(rec["rows"], int) and rec["rows"] > 0, cmd
        assert isinstance(rec["cols"], int) and rec["cols"] >= 0, cmd
        # 2026-10-31 = 第 1 批（414 条）；2026-11-01 = 真机复验复活的 7 条产品命令
        assert rec["checked_at"] in {"2026-10-31", "2026-11-01"}, (cmd, rec["checked_at"])
        assert "args_hex" not in json.dumps(rec, ensure_ascii=False), cmd

    for cmd, rec in dead.items():
        assert set(rec) == {"reason", "checked_at"}, (cmd, sorted(rec))
        assert "未实现" in rec["reason"], cmd
        assert rec["checked_at"] == "2026-10-31"

    assert set(measured.values()) <= {"base", "call", "bare", "obj"}
    assert all(cmd in ctlmap.names() for cmd in measured)


def test_sidecar_files_degrade_when_missing(monkeypatch, tmp_path):
    """三份边车文件缺失/损坏时降级成 `{}`，**不能让 import 或查表炸掉**。"""
    monkeypatch.setattr(ctlmap, "_VERIFIED_PATH", str(tmp_path / "nope1.json"))
    monkeypatch.setattr(ctlmap, "_DEAD_PATH", str(tmp_path / "nope2.json"))
    bad = tmp_path / "broken.json"
    bad.write_text("{ 这不是 JSON", encoding="utf-8")
    monkeypatch.setattr(ctlmap, "_FORM_MEASURED_PATH", str(bad))
    monkeypatch.setattr(ctlmap, "_verified_cache", None)
    monkeypatch.setattr(ctlmap, "_dead_cache", None)
    monkeypatch.setattr(ctlmap, "_form_measured_cache", None)

    assert ctlmap._verified() == {}
    assert ctlmap._dead() == {}
    assert ctlmap._form_measured() == {}
    assert ctlmap.tier_for("HQServ.ZST") is None
    assert ctlmap.form_measured_for("HQServ.hq_nlp_misc") is None
    assert ctlmap.stats()["verified_rows"] == 0
    assert ctlmap.stats()["dead"] == 0


# ---------------------------------------------------------------------------
# 追加（T5b 补漏）：`params_best_for()` —— 抓包元数据模板的四档取值
# （原样 → verified.json 真参数 → 解 ctl_map 自己的 args_hex 样本 → None）。
# 依然只追加，上面的用例一条都没改。
# ---------------------------------------------------------------------------
def _meta_param_cmds():
    """表里 `params` 是**抓包元数据**（元素含 `args_hex`）的命令名。"""
    out = []
    for cmd in ctlmap.names():
        tpl = ctlmap.params_for(cmd)
        if isinstance(tpl, list) and any(
                isinstance(p, dict) and "args_hex" in p for p in tpl):
            out.append(cmd)
    return out


def test_params_best_for_uses_decoded_verified_params():
    """`HQServ.ZST`：ctl_map 里是元数据 → 换成 verified.json 解码后的真参数。

    verified.json 里是「每次抓包一个元素」的 2 元素列表，收敛后必须是**对象**
    （真实 body 是 `{"Code": "HSI", …}`，整表发出去会变成 JSON 数组）。
    """
    out = ctlmap.params_best_for("HQServ.ZST")
    assert isinstance(out, dict) and out, out
    assert "args_hex" not in json.dumps(out, ensure_ascii=False), out
    assert "Code" in out, out
    # 与已验证证据的第一条逐字段相同（没被谁改写）
    assert out == _sidecar("verified.json")["HQServ.ZST"]["params"][0]


def test_params_best_for_passes_through_normal_params_as_new_object():
    """普通命令走第 1 档：值与 `params_for` 相同，但**不是同一个对象**。"""
    plain = ctlmap.params_for("CWServ.ph_agf10_gsjj")
    best = ctlmap.params_best_for("CWServ.ph_agf10_gsjj")
    assert best == plain
    assert best is not plain, "必须返回新对象，不能把缓存里的 list 泄露出去"
    # 就地改返回值不许污染缓存
    best[1] = "999999"
    assert ctlmap.params_for("CWServ.ph_agf10_gsjj") == plain


def test_params_best_for_unknown_returns_none():
    assert ctlmap.params_best_for("CWServ.不存在_的_命令") is None
    assert ctlmap.params_best_for("") is None


def test_params_best_for_collapses_multi_capture_samples():
    """多次抓包拼成的列表必须收敛成**一个对象**（否则整表发成 JSON 数组）。"""
    verified = _sidecar("verified.json")
    for cmd in ("HQServ.ZST", "CWSearch.tdx_pv", "CWSearch.tzx_rcache",
                "CWServ.tzx_filedown"):
        raw = verified[cmd]["params"]
        assert isinstance(raw, list) and len(raw) > 1, (cmd, raw)   # 素材确实是多次抓包
        out = ctlmap.params_best_for(cmd)
        assert isinstance(out, dict), (cmd, type(out), out)
        assert out == raw[0], cmd
    # 25 条元数据命令里，任何一条都不许返回「≥2 个 dict 的列表」
    for cmd in _meta_param_cmds():
        out = ctlmap.params_best_for(cmd)
        assert not (isinstance(out, list) and len(out) > 1
                    and all(isinstance(x, dict) for x in out)), (cmd, out)


def test_single_element_dict_list_params_are_not_collapsed():
    """反例：单元素 dict 列表是**合法的位置参数**，第 1 档不许收敛。"""
    plain = ctlmap.params_for("DataAggregation.queryCardInfo")
    best = ctlmap.params_best_for("DataAggregation.queryCardInfo")
    assert plain == [{"stockCode": "920091", "cardId": "RISK_POINT_CARD"}], plain
    assert best == plain
    assert isinstance(best, list) and len(best) == 1, best


def test_params_best_for_decodes_own_args_hex_as_sample():
    """第 3 档：`ctl_map` 自己的 `args_hex` 解出来的**抓包样本参数**（只取第一条）。"""
    out = ctlmap.params_best_for("CWSearch.search")
    assert isinstance(out, (dict, list)) and out, out
    assert "args_hex" not in json.dumps(out, ensure_ascii=False), out
    # 恰好是第一条能解出来的抓包样本，不是把 N 次抓包拼成一个列表
    meta = ctlmap.params_for("CWSearch.search")
    first = json.loads(bytes.fromhex(meta[0]["args_hex"]).decode("utf-8"))
    assert out == first, (out, first)
    # protobuf 通道解不出 JSON → 必须 None（绝不把 hex 原样当参数）
    assert ctlmap.params_best_for("HQServ.PBFXT") is None
    assert ctlmap.params_best_for("FastHQ.Subscribe") is None


def test_no_capture_metadata_leaks_through_params_best_for():
    """反例守卫：25 条抓包元数据命令，四档分布与内容逐个钉死。"""
    meta = _meta_param_cmds()
    assert len(meta) == 25, "抓包元数据命令应恰好 25 条，实测 %d：%s" % (len(meta), meta)
    verified = _sidecar("verified.json")

    from_verified, from_args_hex, nothing = [], [], []
    leaked = []
    for cmd in meta:
        out = ctlmap.params_best_for(cmd)
        if out is None:
            nothing.append(cmd)
            continue
        if "args_hex" in json.dumps(out, ensure_ascii=False):
            leaked.append(cmd)
        if cmd in verified:
            # verified 的 params 是多次抓包列表 → 期望收敛成第一条
            assert out == verified[cmd]["params"][0], cmd
            from_verified.append(cmd)
        else:
            tpl = ctlmap.params_for(cmd)
            first = None
            for p in tpl:
                hx = p.get("args_hex")
                if not isinstance(hx, str):
                    continue
                try:
                    first = json.loads(bytes.fromhex(hx).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                break
            assert out == first, (cmd, out, first)
            from_args_hex.append(cmd)

    assert not leaked, "这些命令的 params_best_for 结果仍含 args_hex：%s" % leaked
    assert (len(from_verified), len(from_args_hex), len(nothing)) == (4, 8, 13), (
        from_verified, from_args_hex, nothing)


def test_params_best_for_never_leaks_args_hex_across_all_commands():
    """硬门禁：全表 732 条里，`params_best_for` 返回非 None 的结果不许含 `args_hex`。"""
    leaked = []
    checked = 0
    for cmd in ctlmap.names():
        out = ctlmap.params_best_for(cmd)
        if out is None:
            continue
        checked += 1
        if "args_hex" in json.dumps(out, ensure_ascii=False):
            leaked.append(cmd)
    # 517 条有模板 = 492 条第 1 档 + 12 条（4 走 verified、8 解 args_hex）；
    # 剩下 13 条是 protobuf 体 → None。所以非 None 恰好 504 条。
    assert checked == 504, "返回模板的条数应恰好 504，实测 %d" % checked
    assert not leaked, "这些命令的 params_best_for 结果含 args_hex：%s" % leaked
    # 顺带钉死：25 条元数据命令之外，取值与 params_for 完全一致（第 1 档行为不变）
    diff = [c for c in ctlmap.names()
            if c not in _meta_param_cmds()
            and ctlmap.params_best_for(c) != ctlmap.params_for(c)]
    assert not diff, "非元数据命令的取值被改动了：%s" % diff


def test_wrapped_capture_sample_passes_through_body_of_unchanged():
    """穿透守卫：第 3 档解出的「整段请求体」经 `fill()` → `session.body_of()` 必须**原样发出**。

    为什么必须有这条：`CWServ.ph_agf10_gzfx` / `ph_agf10_rdtc_bkztdb` 的抓包是带
    `CallName` 的**完整信封**，而它们在 `ctl_map` 里的 `form` 是 `base`。一旦有人
    （包括 `params_best_for` 自己）把信封拆开只留 `Params`，`body_of` 就会按 base
    发出**没有 CallName** 的包 —— 服务端**静默丢包**（不报错、只回空包），这是
    `ctlmap` 模块 docstring 里点名的第一类坑。
    本用例真的走 `session.body_of()`：产出必须与原始信封逐键相同、且没多出嵌套层。
    """
    from tdxdata._engine import session as S

    cases = {
        "CWServ.ph_agf10_gzfx": {"CallName", "Params"},
        "CWServ.ph_agf10_rdtc_bkztdb": {"CallName", "Params"},
        "CWServ.ph_tdxdatacenter_cb_tjcb": {"Params"},
    }
    for cmd, want_keys in cases.items():
        sample = ctlmap.params_best_for(cmd)
        assert isinstance(sample, dict), (cmd, sample)
        assert set(sample) == want_keys, (cmd, sorted(sample))

        params = ctlmap.fill(sample, code="600519", date="2026-10-30")
        got = json.loads(S.body_of(cmd, params).decode("utf-8"))

        assert isinstance(got, dict), (cmd, got)
        # 1) 顶层键集恰好是原信封的键集 —— 多一个 `Params`/`CallName` 就说明被包了一层
        assert set(got) == want_keys, (cmd, sorted(got))
        # 2) 原信封有 CallName 的，产出必须还带着同一个 CallName
        if "CallName" in want_keys:
            assert got.get("CallName") == sample["CallName"], (cmd, got)
        # 3) 任何顶层值不许再是信封 dict（二次包装的另一种表现）
        for k, v in got.items():
            assert not (isinstance(v, dict)
                        and ("CallName" in v or "Params" in v)), (cmd, k, v)
        # 4) 逐字节等价：直通就是直通，不带任何改写
        assert got == sample, (cmd, got, sample)

