# -*- coding: utf-8 -*-
r"""黄金样本契约（**离线**）：样本不许被手改 + 离线回放差异必须为 0。

这个文件守两件事
----------------
1. **样本自身完整**：每份 JSON 的 `meta.sha256` 必须等于它 `payload` 重算值。
   没有这一条，谁手改一下 payload 就能让"回放一致"变成自我安慰。
2. **解码层没退化**：把 `fixtures/golden/frames/` 里的原始回包喂回**当前代码**，
   逐字段与冻结 payload 比，差异必须为 0。这是语义层重构（阶段 1~5）的对照基准。

红线（`_dev/check.py` 第 7 条会离线扫 `tests/test_*.py`）
--------------------------------------------------------
不许出现 `Client(` / `Session(` 这种"看起来要真建连"的字面量；本文件也**不联网**
—— 回放路径走 `golden_runner.py` 的帧表，一个 socket 都不建。要真联网请用
`python tests/golden_runner.py record`（人工跑，不进 pytest）。

跑法
----
    python -m pytest tests/test_golden_contract.py -q
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GOLDEN = os.path.join(HERE, "fixtures", "golden")
FRAMES = os.path.join(GOLDEN, "frames")
RUNNER = os.path.join(HERE, "golden_runner.py")
MANIFEST = os.path.join(FRAMES, "manifest.json")
#: 有意行为变更后归档的旧样本（原样搬走、sha256 不改、不参与 check）
SUPERSEDED = os.path.join(GOLDEN, "_superseded")
SUPERSEDED_README = os.path.join(SUPERSEDED, "README.md")

REQUIRED_META = (
    "name", "cmd", "ctl", "params", "fetched_at", "server_time", "sha256",
    "base_dir", "python", "tdxdata_version", "git_head",
)

# 与 `golden_runner.ALLOWED_ADDED_KEYS` 同一个口径，这里独立写一遍：
# 样本契约不该依赖 runner 的实现细节，两边各写一份才能互相校验。
ALLOWED_ADDED_KEYS = frozenset({
    # 原批 8 个
    "meta", "as_of", "fetched_at", "status", "source",
    "elapsed", "provisional", "data_date",
    # 2026-10-27 Lead 批准：来源凭据类元信息（时钟来源与降级说明），
    # 与 fetched_at/status/source/elapsed 同类、不含数据信号。只放行"新增"，改值仍红。
    "as_of_note", "clock_note", "fetch_clock", "status_note",
})

#: 「来源凭据类」键：被 golden 放行的同时，**必须**有一处真单测管着语义。
SOURCE_CREDENTIAL_KEYS = ("as_of_note", "clock_note", "fetch_clock", "status_note")

#: `meta` dict 里「离线不可复现」的载体 —— **封闭集，恰好 3 项，精确键名，不许通配**。
#: 与 `golden_runner.INFO_ONLY_META_CARRIERS` 各写一份、互相校验。
#: 判据只有一条：**能离线复现的键不许出现在这里**（那等于用门禁的牙齿换绿）。
INFO_ONLY_META_CARRIERS = {
    "fetched_at": (),        # 录制时刻 ≠ 回放时刻
    "elapsed": (),           # 每次实测耗时；性能量，退化靠别的手段兜
    "source": ("host",),     # 帧里没记 host，且在线时也不确定连到哪台
}

#: `source` 必须仍是 dict 且**键集恰为这 5 个**（只放行 `host` 的值）。
SOURCE_DICT_KEYS = ("host", "cmd", "ctl", "clock", "cached")

#: `meta` 段的**已知位置**（只有这三处算"语义 meta"）。
META_DICT_PATHS = ("payload.meta", "payload.value.meta", "payload.value.table.meta")

#: 已归档样本的**权威清单**（与 `golden_runner.EXPECTED_ARCHIVED` 互为校验）。
#:
#: 缺口（复核 V6c）：把在役样本搬进 `_superseded/` + 补一行**虚构理由**的登记 +
#: 从两张源码表删掉它 → 全绿。根因是"归档"天生就是"让样本合法退出比对"的开关，
#: 而登记表 6 个字段**没有一个可机器判定**。这张表把它变成可机器判定：
#: 归档集合双向对账 + 每条必须指向一个**真实在役**的替代样本。
EXPECTED_ARCHIVED = {
    "bars_880005.stage2.json": {"stage": "2", "replaced_by": "breadth_880005"},
    "bars_880006.stage2.json": {"stage": "2", "replaced_by": "breadth_880006"},
    "bars_880866.stage2.json": {"stage": "2", "replaced_by": "breadth_880866"},
    "err_bars_880005.stage2.json": {"stage": "2",
                                    "replaced_by": "reject_bars_880005"},
    "boards_industry.stage1.5.json": {"stage": "1.5",
                                      "replaced_by": "boards_industry_s4"},
    "market_stat_updown.stage1.5.json": {"stage": "1.5",
                                         "replaced_by": "market_stat_updown"},
    "market_stat_sentiment.stage1.5.json": {"stage": "1.5",
                                            "replaced_by": "market_stat_sentiment"},
    "market_stat_industry.stage1.5.json": {"stage": "1.5",
                                           "replaced_by": "market_stat_industry"},
    # 阶段 3：无法识别的标的 ValueError → OutOfScope（显式否掉「服务端不支持市场码」
    # 的错归因：期货/期权/国债期货/外汇根本不在证券表内）
    "err_bars_IF00.stage3.json": {"stage": "3", "replaced_by": "err_bars_IF00"},
    # 阶段 4：change_pct 实算 + industry 支路多一轮 PBMultiHQ(market=10001)
    # （同一基名的**第二份**归档，stage1.5 原封不动）
    # 在役替代样本**改名**为 `boards_industry_s4` —— N4a 拒绝同名重录
    # （同名会回收 stage4 自己声明的帧，归档从此不可回放）。
    "boards_industry.stage4.json": {"stage": "4",
                                    "replaced_by": "boards_industry_s4"},
}

#: 每份样本 `meta` 段里**必须存在**的语义键（与 runner 同名表互为校验）。
#: 缺口（复核 V-白名单）：`ALLOWED_ADDED_KEYS` 放行的是"**新增**"，所以把样本里的
#: `status`/`source`/`provisional`… 删掉，回放会重新"新增"它们 → 门禁照绿、
#: **约束静默消失**。这张表把"必须有哪几个键"钉在源码里。
EXPECTED_SAMPLE_META_KEYS = {
    "bars_510300": [],
    "bars_5m_300": [],
    "bars_60m_300": [],
    "bars_day_300": [],
    "err_bars_000300": [],
    "err_bars_932000": [],
    "err_bars_IF00": [],
    "minutes": [],
    "quotes_indices": [],
    "ticks": [],
    "product_north": [],
    "product_ztqd": [],
    "reject_bars_880005": [],
    "reject_bars_880006": [],
    "reject_bars_880641": [],
    "reject_bars_880866": [],
    "f10_gsjj": ["cols_align", "product", "template_check"],
    "finance_zcfz": ["cols_align", "cols_source", "cols_union", "product",
                     "template_check"],
    "product_lrmx": ["cols_source", "cols_union"],
    # ⚠️ `boards_industry` 于**阶段 4 归档** → 在役样本改名为 `boards_industry_s4`
    #    （N4a 拒绝同名重录，否则 stage4 归档的帧会被回收、从此不可回放）。
    #    键集合按实测登记，比旧版多 `change_pct_method`（涨跌幅现在是实算的）。
    "boards_industry_s4": ["as_of", "as_of_note", "change_pct_method",
                           "const_fields", "elapsed", "fetch_clock",
                           "fetched_at", "params", "row_semantics", "source",
                           "source_note", "status", "status_note",
                           "unavailable", "why"],
    "market_stat_updown": ["as_of", "as_of_note", "elapsed", "fetch_clock",
                           "fetched_at", "params", "source", "source_note",
                           "status", "status_note", "trade_date"],
    "market_stat_sentiment": ["as_of", "as_of_note", "elapsed", "fetch_clock",
                              "fetched_at", "note", "params", "source",
                              "source_calls", "source_note", "status",
                              "status_note", "trade_date", "unmapped_raw_columns"],
    "market_stat_industry": ["as_of", "as_of_note", "calls", "elapsed",
                             "fetch_clock", "fetched_at", "level1_codes", "note",
                             "params_dir", "skipped_codes", "source",
                             "source_note", "status", "status_note", "truncated"],
    "breadth_880005": ["as_of", "as_of_note", "elapsed", "fetch_clock",
                       "fetched_at", "field_map", "note", "period", "provisional",
                       "series", "series_name", "source", "status", "unavailable"],
    "breadth_880006": ["as_of", "as_of_note", "elapsed", "fetch_clock",
                       "fetched_at", "field_map", "note", "period", "provisional",
                       "series", "series_name", "source", "status", "unavailable"],
    "breadth_880641": ["as_of", "as_of_note", "elapsed", "fetch_clock",
                       "fetched_at", "field_map", "note", "period", "provisional",
                       "series", "series_name", "source", "status", "unavailable"],
    "breadth_880866": ["as_of", "as_of_note", "elapsed", "fetch_clock",
                       "fetched_at", "field_map", "note", "period", "provisional",
                       "series", "series_name", "source", "status", "unavailable"],
}

#: 白名单键里**黄金样本覆盖不到**的那几个 —— 明确记账（闭集，多一个少一个都红）。
META_KEYS_NOT_COVERED_BY_GOLDEN = {
    "clock_note": "只有容器 meta 才产出；在役样本 payload 里一份容器 meta 都没有，"
                  "容器 meta 走信息性通道、不进 rc。改由 test_series_containers.py "
                  "+ test_timeliness.py 管。",
    "data_date": "只有 Dataset（多表返回）才有；当前样本集里没有 Dataset 形态的 "
                 "meta 落盘。属已知缺口，见 golden/README.md「已知缺口」。",
}

#: 每个样本的权威帧集合 —— **唯一不受 fixture 目录内篡改影响的锚点**。
#:
#: `payload` 有 sha256 自证、帧有 args 指纹，但「谁该有帧」这个事实此前只存在于
#: 样本自己的 `meta.frames` 里 —— 等于让被测者给自己出庭作证。独立复核者实测：
#: 把 `meta.frames` 从 `[2]` 改成 `[]`，`has_frames` 就变假，该样本被"跳过"，
#: 跳过不进 rc，于是 `rc` 从 1 变 0 还打印假原因「录制时本就没有 wire frame」。
#:
#: 这张表与 `golden_runner.EXPECTED_SAMPLE_FRAMES` **各写一份、互相校验**
#: （同 `ALLOWED_ADDED_KEYS` 的既有做法）：改它必须改源码，改源码跑不过 diff 审查。
#: 取值：`[seq, ...]` = 该样本产生的帧序号；`"unavailable"` = 零 wire 请求。
EXPECTED_SAMPLE_FRAMES = {
    "quotes_indices": [1],
    "bars_day_300": [2],
    "bars_5m_300": [3],
    "bars_60m_300": [4],
    "minutes": [5],
    "ticks": [6, 7],
    # bars_880005 / bars_880006 / bars_880866 / err_bars_880005 已于阶段 2 归档
    "bars_510300": [11],
    # 这 3 份的旧版已于阶段 1.5 归档（source 由字符串改为 5 键 dict），
    # 下面是同名重录后的**新帧号**（2026-10-27T17:48~17:49）
    # ⚠️ `boards_industry` 于**阶段 4 归档**（stage1.5 + stage4 两份链条留全），
    #    在役样本改名 `boards_industry_s4`，帧集合按实测登记（多出回填轮）。
    "boards_industry_s4": [97, 98, 99, 100, 101, 102, 103, 104, 105],
    "market_stat_updown": [62, 63],
    "market_stat_sentiment": [64, 65],
    "market_stat_industry": [
        66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82,
        83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96,
    ],
    "product_lrmx": [48],
    "product_north": [49],
    "product_ztqd": [50],
    "f10_gsjj": [51],
    "finance_zcfz": [52],
    "err_bars_000300": [53],
    "err_bars_932000": [54],
    "err_bars_IF00": "unavailable",
    # err_bars_880005：[55] → 阶段 2 已归档
    # ── 阶段 2 新增（2026-10-27T17:48~17:49 录制）──────────────────────
    "breadth_880005": [56, 57],
    "breadth_880006": [58],
    "breadth_880866": [59],
    "breadth_880641": [60],
    # 阶段 2 拒绝现场：零 wire 请求（拦在建帧之前）
    "reject_bars_880005": "unavailable",
    "reject_bars_880006": "unavailable",
    "reject_bars_880866": "unavailable",
    "reject_bars_880641": "unavailable",
}

#: 零帧样本在表里的哨兵值。
NO_FRAMES = "unavailable"

#: 阶段 0 冻结时录的那 23 份：manifest 支持 `meta_sha256` 之前录的，
#: 按纪律**不重录**，所以允许它们没有这个键（打印信息行，不判红）。
#: 一旦有**新**样本落进这里，就说明有人想用"老样本"身份跳过 meta 完整性校验。
LEGACY_NO_METASHA = frozenset({
    "quotes_indices", "bars_day_300", "bars_5m_300", "bars_60m_300", "minutes",
    "ticks", "bars_880005", "bars_880006", "bars_880866", "bars_510300",
    "boards_industry", "market_stat_updown", "market_stat_sentiment",
    "market_stat_industry", "product_lrmx", "product_north", "product_ztqd",
    "f10_gsjj", "finance_zcfz", "err_bars_000300", "err_bars_932000",
    "err_bars_IF00", "err_bars_880005",
})


def canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha_of(obj) -> str:
    return hashlib.sha256(canon(obj).encode("utf-8")).hexdigest()


def _load(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def sample_paths() -> list[str]:
    """参与 check 的样本 = `golden/` **顶层**的 `.json`（`_superseded/` 不算）。"""
    if not os.path.isdir(GOLDEN):
        return []
    out = []
    for name in sorted(os.listdir(GOLDEN)):
        path = os.path.join(GOLDEN, name)
        if name.endswith(".json") and os.path.isfile(path):
            out.append(path)
    return out


def archived_paths() -> list[str]:
    """`_superseded/` 下已归档的旧样本。"""
    if not os.path.isdir(SUPERSEDED):
        return []
    return sorted(os.path.join(SUPERSEDED, n) for n in os.listdir(SUPERSEDED)
                  if n.endswith(".json") and os.path.isfile(os.path.join(SUPERSEDED, n)))


def archived_base_name(filename: str) -> str:
    """`bars_880005.stage2.json` → `bars_880005`（归档名 = `<原文件名>.<阶段标签>.json`）。"""
    base = filename[:-5] if filename.endswith(".json") else filename
    return base.split(".", 1)[0]


def registered_rows() -> dict[str, dict]:
    """把 `_superseded/README.md` 的登记表读成 `{归档文件名: {列名: 值}}`。

    只认 `|` 开头的表格行；表头取第一行，`|---|` 分隔行跳过。
    单元格里允许带反引号（`` `bars_880005.stage2.json` ``），这里会剥掉。
    """
    if not os.path.exists(SUPERSEDED_README):
        return {}
    rows: dict[str, dict] = {}
    header: list[str] | None = None
    with open(SUPERSEDED_README, encoding="utf-8") as fh:
        for line in fh:
            text = line.strip()
            if not text.startswith("|"):
                continue
            cells = [c.strip() for c in text.strip("|").split("|")]
            if header is None:
                header = cells
                continue
            if all(set(c) <= set("-: ") for c in cells if c):        # |---|---|
                continue
            first = cells[0].strip("`").strip()
            if not first or set(first) <= set("-: "):
                continue
            rows[first] = dict(zip(header, cells))
    return rows


def _col(row: dict, prefix: str) -> str:
    """按列名前缀取值（容忍表头微调，比如后面加了备注）。"""
    for key, val in row.items():
        if key and key.startswith(prefix):
            return (val or "").strip().strip("`").strip()
    return ""


SAMPLES = sample_paths()
IDS = [os.path.basename(p)[:-5] for p in SAMPLES]


def test_golden_dir_is_not_empty():
    """空目录必须显式红，绝不能让"0 份样本"被读成"0 差异"。"""
    assert os.path.isdir(GOLDEN), "黄金样本目录不存在：%s" % GOLDEN
    assert SAMPLES, "%s 下一份样本 JSON 都没有" % GOLDEN
    assert os.path.exists(MANIFEST), "帧清单不存在：%s" % MANIFEST


@pytest.mark.parametrize("path", SAMPLES, ids=IDS)
def test_golden_sample_is_intact(path):
    """每份样本要有完整 meta，且 sha256 与 payload 重算值一致（防手改）。"""
    doc = _load(path)
    assert set(doc) == {"meta", "payload"}, "%s 顶层只该有 meta / payload" % path
    meta = doc["meta"]

    missing = [k for k in REQUIRED_META if k not in meta]
    assert not missing, "%s 的 meta 缺字段：%s" % (path, missing)

    payload = doc["payload"]
    assert isinstance(payload, dict) and "outcome" in payload, \
        "%s 的 payload 没有 outcome（成功/异常两种形态都要标明）" % path
    assert payload["outcome"] in ("ok", "error"), \
        "%s 的 outcome 非法：%r" % (path, payload["outcome"])
    if payload["outcome"] == "error":
        assert payload.get("exc_type") and "exc_msg" in payload, \
            "%s 记的是异常现场，必须带 exc_type 与 exc_msg 原文" % path

    assert sha_of(payload) == meta["sha256"], (
        "%s 的 payload 与 meta.sha256 不一致 —— 样本被改过？"
        "（要重录请跑 python tests/golden_runner.py record）" % path)

    frames = meta.get("frames")
    assert isinstance(frames, list) or (
        isinstance(frames, str) and frames.startswith("unavailable: ")), \
        "%s 的 meta.frames 必须是帧序号列表，或 'unavailable: <原因>' 字符串" % path


def test_frames_manifest_matches_bin_files():
    """manifest 的每一条都要有对应 .bin，且 args/resp 的 sha256 能重算对上。"""
    sys.path.insert(0, HERE)
    import golden_runner as gr

    manifest = _load(MANIFEST)
    entries = manifest.get("entries") or []
    assert entries, "manifest 里 0 个帧 —— 没有帧就没法离线回放"
    assert manifest.get("count") == len(entries), \
        "manifest.count=%r 与实际 entries 数 %d 不一致" % (manifest.get("count"), len(entries))

    for ent in entries:
        for key in ("seq", "transport", "cmd", "ctl", "args_sha256",
                    "resp_sha256", "resp_len", "file"):
            assert key in ent, "manifest 条目缺 %r：%r" % (key, ent)
        assert ent["transport"] in ("send", "ask"), \
            "未知 transport %r（只认 send / ask）" % (ent["transport"],)

        path = os.path.join(FRAMES, ent["file"])
        assert os.path.exists(path), "帧文件缺失：%s" % path
        _meta, args, _raw, blobs = gr.read_frame_file(path)
        assert gr.sha_bytes(args) == ent["args_sha256"], \
            "%s 的请求参数 sha256 与 manifest 不一致" % ent["file"]
        resp = b"".join(blobs) if blobs else b""
        assert gr.sha_bytes(resp) == ent["resp_sha256"], \
            "%s 的回包 sha256 与 manifest 不一致" % ent["file"]
        assert len(resp) == ent["resp_len"], \
            "%s 的回包长度与 manifest 不一致" % ent["file"]

    # 样例与帧的双向覆盖：manifest 里登记的样本名 == 目录里的样本文件名
    # （被有意变更归档掉的样本允许只在 manifest 里，但必须能在 _superseded/ 找到同名归档）
    names = {os.path.basename(p)[:-5] for p in SAMPLES}
    listed = {str(s.get("name")) for s in (manifest.get("samples") or [])}
    archived_bases = {archived_base_name(os.path.basename(p)) for p in archived_paths()}
    assert not (names - listed), (
        "目录里有样本但 manifest 没登记（漏录？）：%s" % sorted(names - listed))
    orphans = listed - names - archived_bases
    assert not orphans, (
        "manifest 登记的样本既不在目录、也没归档到 _superseded/：%s"
        "（要么补录，要么归档并登记）" % sorted(orphans))
    # 重复请求的回包冲突：**只允许「解码等价」的**。
    #
    # 实测：同一条 `CWServ.sys_fn_jyr_v3` 连发两次，第二次命中服务端缓存，回包多一个
    # `"HitCache":"L1:<hash>"`，而 `Content`（数据本体）逐字节一致 —— 字节不同 ≠ 数据
    # 不同。回放对同一请求**只用首次**回包，所以解码等价的冲突不影响任何样本。
    #
    # 但**解码不等价**的冲突是真数据不一致（同一请求两次拿到不同数据），必须红。
    # 判据不写成"允许 N 个冲突"那种糊涂账，而是逐个验解码结果。
    conflicts = manifest.get("conflicts") or []
    if conflicts:
        by_seq = {int(e["seq"]): e for e in (manifest.get("entries") or [])}
        sys.path.insert(0, HERE)
        try:
            import golden_runner as runner_mod
        finally:
            sys.path.pop(0)
        for item in conflicts:
            a = by_seq.get(int(item["first"]))
            b = by_seq.get(int(item["seq"]))
            assert a and b, "冲突记录里的 seq 在 entries 里找不到：%r" % (item,)
            same = item.get("decoded_same")
            if same is None:                     # 老 manifest 没这个字段 → 自己算
                same = runner_mod._decode_equivalent(a["file"], b["file"])
            assert same is True, (
                "冲突帧 %s seq %s vs %s **解码结果不等价** —— 同一请求两次拿到了不同"
                "数据，这是真冲突，不是缓存噪声（缓存噪声只会多一个 %s 字段）"
                % (item.get("cmd"), item.get("first"), item.get("seq"),
                   ", ".join(getattr(runner_mod, "CACHE_META_KEYS", ("HitCache",)))))


def test_superseded_files_are_registered():
    """`_superseded/` 里每一份都必须：① 在 README 登记表里有对应行；
    ② 写得清"为什么"（变更阶段 + 变更原因非空）；③ 内容原样未改（sha256 仍对得上）。

    这条守的是「有意变更」通道不被滥用的底线：
    **挪走却不解释 = 偷偷放水**，而放水的门禁比没有门禁更危险。
    """
    archived = archived_paths()
    if not archived:
        # 现在就是 0 份（阶段 0 的 23 份全部仍在服役）。0 份时这条测试仍有价值：
        # 一旦有人往里放东西却忘了登记，它立刻变红。
        assert os.path.exists(SUPERSEDED_README), (
            "归档目录的契约文件缺失：%s（没有它就没法判定'挪走是否有解释'）"
            % SUPERSEDED_README)
        assert registered_rows() == {}, \
            "README 登记表里有行，但 _superseded/ 下没有任何归档样本：%s" \
            % sorted(registered_rows())
        return

    rows = registered_rows()
    for path in archived:
        name = os.path.basename(path)

        assert name in rows, (
            "%s 被归档了却没在 _superseded/README.md 登记表里登记 —— "
            "挪走样本必须写清阶段与原因（否则等于偷偷放水）" % name)
        row = rows[name]
        assert _col(row, "变更阶段"), "%s 的登记行「变更阶段」为空" % name
        assert _col(row, "变更原因"), "%s 的登记行「变更原因」为空（别写空话，写清旧→新）" % name
        assert _col(row, "移入时间"), "%s 的登记行「移入时间」为空" % name

        doc = _load(path)
        assert "payload" in doc and "meta" in doc, "%s 不是合法样本（缺 payload/meta）" % name
        assert sha_of(doc["payload"]) == doc["meta"]["sha256"], (
            "%s 归档后被改过字节（payload 与 meta.sha256 不一致）—— "
            "归档铁律是「原样搬移，一个字节都不改」" % name)

    # 反向：登记表里不该有"没归档"的幽灵行
    extra = sorted(set(rows) - {os.path.basename(p) for p in archived})
    assert not extra, "README 登记表里有行但 _superseded/ 下没有对应文件：%s" % extra


def test_source_credential_keys_are_unit_tested():
    """被 golden 放行的「来源凭据类」键，**必须**有一处真单测管着它的语义。

    为什么这条非加不可
    ------------------
    白名单是这套门禁**唯一会被滥用的方向**：把比对放宽到"不再报差异"永远比修数据
    省事。而来源凭据类键（`fetch_clock` / `clock_note` / `status_note` / `as_of_note`）
    恰恰是 goldens **原理上兜不住**的一格 —— 离线回放里 `fetch_clock` 恒为 `local`、
    `clock_note` 恒为 `local-clock-fallback`，于是"服务器时钟探针永久失效"这种退化
    在回放里与正常态**完全同形**，永远不会红。

    所以这里立一条静态规矩（成本 ≈ 0，不联网、不 import 被测模块）：
    **凡进了 `ALLOWED_ADDED_KEYS` 的来源凭据键，键名必须在某个 `tests/test_*.py`
    的源码里出现过** —— 出现在断言里，就意味着有一处真单测在管它。
    预期的家是 `tests/test_timeliness.py`（探针成功 → `fetch_clock == "server"`；
    探针失败 → `fetch_clock == "local"` 且 `clock_note` 非空），但这里**不写死文件名**：
    要守的是"有单测管着"，不是"必须放哪个文件"（放死文件名只会制造假红）。

    顺带守两条硬约束：
      · 只认**精确键名**，白名单里不许出现任何通配 / 前缀写法（`*`、`*_note` 之类）；
      · 4 个来源凭据键必须都在白名单里（防止有人只加键不加单测，或删了单测却不撤键）。
    """
    # ① 精确键名，不许通配/前缀
    for key in ALLOWED_ADDED_KEYS:
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key), (
            "ALLOWED_ADDED_KEYS 里出现了非精确键名 %r —— 白名单只认完整键名，"
            "不许通配/前缀匹配（否则等于把整个比对放宽掉）" % (key,))
        assert not any(ch in key for ch in "*?[]"), (
            "ALLOWED_ADDED_KEYS 里 %r 含通配字符，不允许" % (key,))

    # ② 来源凭据键必须在白名单里（两边清单一致）
    assert set(SOURCE_CREDENTIAL_KEYS) <= ALLOWED_ADDED_KEYS, (
        "来源凭据键没进白名单：%s"
        % sorted(set(SOURCE_CREDENTIAL_KEYS) - ALLOWED_ADDED_KEYS))

    # ③ 每个来源凭据键都要有单测提到它
    test_files = sorted(
        os.path.join(HERE, n) for n in os.listdir(HERE)
        if n.startswith("test_") and n.endswith(".py")
        and os.path.isfile(os.path.join(HERE, n)))
    assert test_files, "tests/ 下没有 test_*.py —— 路径推算错了"

    sources = {}
    for path in test_files:
        with open(path, encoding="utf-8") as fh:
            sources[os.path.basename(path)] = fh.read()

    missing = {}
    for key in SOURCE_CREDENTIAL_KEYS:
        owners = [name for name, text in sources.items()
                  if key in text and name != os.path.basename(__file__)]
        if not owners:
            missing[key] = None
    assert not missing, (
        "这些来源凭据键被 golden 放行了，却没有任何 tests/test_*.py 提到它们：%s\n"
        "白名单 + 无单测 = 漏洞：离线回放里 fetch_clock 恒为 local、clock_note 恒为 "
        "local-clock-fallback，原理上发现不了「服务器时钟探针永久失效」。\n"
        "请让 tests/test_timeliness.py 覆盖：探针成功 → fetch_clock == \"server\"；"
        "探针失败 → fetch_clock == \"local\" 且 clock_note 非空；"
        "并让 %s 这四个键名在源码里出现。"
        % (sorted(missing), " / ".join(SOURCE_CREDENTIAL_KEYS)))


def test_expected_sample_frames_anchor_is_sound():
    """「谁该有帧」的**源码锚点**必须与磁盘、与 manifest 三方一致，且双向对齐。

    这是 B5 洗白路径的根治点。判据**只许**来自源码表 `EXPECTED_SAMPLE_FRAMES`；
    这里做四件事：
      ① 表 ↔ runner 里的同名表逐项一致（两份独立源码互为校验）；
      ② 表里的 key 集合 == 磁盘上的样本集合（双向，不含 `_superseded/`）；
         少一个 → 有新样本没登记（防"偷偷加个无帧样本绕过"）；
         多一个 → 表里有幽灵行；
      ③ 每个样本**磁盘上真实的帧集合**（从它自己的 `meta.frames` + manifest 交叉读，
         这里只是"核对"，不是"判定依据"）== 表里登记的；
      ④ 表里 `"unavailable"` 的样本必须**真的**没有任何帧文件挂靠它。
    """
    # ① 两份源码表一致（import runner 只为取这张表，不做别的）
    sys.path.insert(0, HERE)
    try:
        import golden_runner as runner_mod
    finally:
        sys.path.pop(0)
    assert dict(runner_mod.EXPECTED_SAMPLE_FRAMES) == dict(EXPECTED_SAMPLE_FRAMES), (
        "runner 与契约测试里的 EXPECTED_SAMPLE_FRAMES 不一致：\n"
        "只在 runner=%s\n只在契约=%s"
        % (sorted(set(runner_mod.EXPECTED_SAMPLE_FRAMES) - set(EXPECTED_SAMPLE_FRAMES)),
           sorted(set(EXPECTED_SAMPLE_FRAMES) - set(runner_mod.EXPECTED_SAMPLE_FRAMES))))

    # ② 双向：表 key ↔ 磁盘样本
    disk = {os.path.basename(p)[:-5] for p in SAMPLES}
    table = set(EXPECTED_SAMPLE_FRAMES)
    assert not (disk - table), (
        "这些样本在磁盘上却没有在源码表里登记帧集合：%s\n"
        "新增样本必须同时登记进 EXPECTED_SAMPLE_FRAMES —— 否则「该不该有帧」"
        "就变成样本自己说了算（B5 洗白路径）" % sorted(disk - table))
    assert not (table - disk), (
        "源码表里登记了磁盘上不存在的样本：%s\n"
        "（归档进 _superseded/ 的样本要同步从表里删掉）" % sorted(table - disk))

    # 读 manifest 的 entries 与 samples（这里仅用于核对，不作判定依据）
    manifest = _load(MANIFEST)
    frames_by_sample: dict[str, set[int]] = {}
    for ent in manifest.get("entries") or []:
        owner = str(ent.get("sample") or "")
        if owner:
            frames_by_sample.setdefault(owner, set()).add(int(ent["seq"]))
    declared = {str(s.get("name")): s for s in (manifest.get("samples") or [])}

    # ③ 每个样本的帧集合必须与表一致（三处交叉：表 / 样本 meta.frames / manifest）
    for name in sorted(table):
        want = EXPECTED_SAMPLE_FRAMES[name]
        if want == NO_FRAMES:
            continue
        want_set = set(want)

        doc = _load(os.path.join(GOLDEN, name + ".json"))
        got_meta = (doc.get("meta") or {}).get("frames")
        assert isinstance(got_meta, list), (
            "%s：样本 meta.frames 不是列表（%r），但源码表说它有帧 %s"
            % (name, got_meta, sorted(want_set)))
        assert set(got_meta) == want_set, (
            "%s：样本 meta.frames = %s，与源码表的 %s 不一致 —— "
            "注意源码表才是判据，meta.frames 只是被核对对象"
            % (name, sorted(got_meta), sorted(want_set)))

        from_manifest = frames_by_sample.get(name, set())
        assert from_manifest == want_set, (
            "%s：manifest 里挂在该样本名下的帧 = %s，与源码表的 %s 不一致"
            % (name, sorted(from_manifest), sorted(want_set)))

        for seq in want_set:
            hit = [e for e in (manifest.get("entries") or [])
                   if int(e["seq"]) == seq]
            assert hit, "%s：源码表登记了帧 seq=%d，但 manifest 里没有这个帧" % (name, seq)
            fpath = os.path.join(FRAMES, hit[0]["file"])
            assert os.path.exists(fpath), \
                "%s：源码表登记了帧 seq=%d，但帧文件不存在：%s" % (name, seq, fpath)

    # ④ "unavailable" 的样本必须真的零帧
    for name in sorted(table):
        if EXPECTED_SAMPLE_FRAMES[name] != NO_FRAMES:
            continue
        assert not frames_by_sample.get(name), (
            "%s：源码表登记为零帧，但 manifest 里挂了它的帧 %s"
            % (name, sorted(frames_by_sample.get(name, set()))))
        doc = _load(os.path.join(GOLDEN, name + ".json"))
        raw = (doc.get("meta") or {}).get("frames")
        assert not isinstance(raw, list), (
            "%s：源码表登记为零帧，但样本 meta.frames 是个列表 %r" % (name, raw))
        assert str(raw or "").startswith("unavailable"), (
            "%s：零帧样本的 meta.frames 应显式记 unavailable，实得 %r" % (name, raw))
        # 零帧样本**必须**能回放成功（它是在建帧之前就失败的，回放不碰 wire）
        _ = declared.get(name)  # 存在性由 ② 保证

    # ⑤ 新录的样本必须带 meta_sha256；老样本允许没有（按纪律不重录）
    for name in sorted(disk):
        s = declared.get(name) or {}
        assert "meta_sha256" in s or name in LEGACY_NO_METASHA, (
            "%s：manifest 里既没有 meta_sha256、也不在阶段 0 的老样本名单里 —— "
            "新录样本必须带 meta 完整性摘要（B2b），否则 meta 段裸奔" % name)


def test_informational_meta_carriers_is_closed_set():
    """「离线不可复现」清单是**源码里的封闭集**：恰好 3 项、精确键名、不许通配。

    这是 B5 / B2b 的同型防线：判定依据一旦放在"可以随手加"的地方，门禁就会被
    一点点关掉。所以这里把清单本身钉死 ——
      · 项集合必须**恰好**是 `{fetched_at, elapsed, source}`，多一项/少一项都红；
      · 不许出现任何通配/前缀写法（`*_at`、`elapsed*` 之类）；
      · `source` 只许放行 `host` 这一个子键；
      · 能离线复现的键**绝不许**混进来（这条按名字逐个点名，改一个就红）。
    """
    assert set(INFO_ONLY_META_CARRIERS) == {"fetched_at", "elapsed", "source"}, (
        "「信息性载体」清单必须恰为 3 项 fetched_at/elapsed/source，实得 %s\n"
        "多一项就等于多关一格门禁 —— 加键前先证明它**离线不可复现**"
        % sorted(INFO_ONLY_META_CARRIERS))

    for key, subkeys in INFO_ONLY_META_CARRIERS.items():
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key), (
            "清单项 %r 不是精确键名（不许通配/前缀）" % (key,))
        assert not any(ch in key for ch in "*?[]"), \
            "清单项 %r 含通配字符，不允许" % (key,)
        assert isinstance(subkeys, tuple), \
            "清单项 %r 的放行子键必须是 tuple，实得 %r" % (key, type(subkeys))
        for sub in subkeys:
            assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", sub), \
                "子键 %r 不是精确键名（不许通配/前缀）" % (sub,)

    # `source` 只放行 host 的值；cmd/ctl/clock/cached 必须留在严格比对里
    assert INFO_ONLY_META_CARRIERS["source"] == ("host",), (
        "`source` 只许放行 host 的值（实得 %r）—— "
        "把整个 source 降为「只比存在性」，就再也抓不到 D7（source 退化成字符串）"
        % (INFO_ONLY_META_CARRIERS["source"],))
    # fetched_at / elapsed 是标量，没有"子键"可放行
    assert INFO_ONLY_META_CARRIERS["fetched_at"] == ()
    assert INFO_ONLY_META_CARRIERS["elapsed"] == ()

    # 反向：这些键**能离线复现**，绝不许被挪进信息性清单
    for forbidden in ("status", "as_of", "provisional", "fetch_clock",
                      "clock_note", "as_of_note"):
        assert forbidden not in INFO_ONLY_META_CARRIERS, (
            "%s 在离线回放里**能**复现（回放时钟恒 local、判定算法两边一致），"
            "不许降为信息性" % forbidden)

    # 与 runner 的清单逐项一致（两份源码互为校验）
    sys.path.insert(0, HERE)
    try:
        import golden_runner as runner_mod
    finally:
        sys.path.pop(0)
    assert dict(runner_mod.INFO_ONLY_META_CARRIERS) == dict(INFO_ONLY_META_CARRIERS), (
        "runner 与契约测试里的 INFO_ONLY_META_CARRIERS 不一致：runner=%r 契约=%r"
        % (runner_mod.INFO_ONLY_META_CARRIERS, INFO_ONLY_META_CARRIERS))
    assert tuple(runner_mod.SOURCE_DICT_KEYS) == tuple(SOURCE_DICT_KEYS), (
        "source 的 5 键约定两边不一致：runner=%r 契约=%r"
        % (runner_mod.SOURCE_DICT_KEYS, SOURCE_DICT_KEYS))


def test_meta_source_structure_is_still_asserted():
    """A′ 的口径验证：`source` **结构全判**，只有 `host` 的值不判。

    这条守的是"收窄版 vs 放水版"的分界：
      · host 变了          → 0 差异（离线不可复现，放行）
      · cmd/ctl/clock/cached 变了 → **必须红**
      · source 退化成字符串 → **必须红**（D7 的病征）
      · source 少一个键     → **必须红**（键集必须恰为 5 个）
      · fetched_at/elapsed 变了 → 0 差异（离线不可复现）
      · 但 status/as_of/fetch_clock 变了 → **必须红**（能复现，严比）
    """
    sys.path.insert(0, HERE)
    try:
        import golden_runner as runner_mod
    finally:
        sys.path.pop(0)

    base = {
        "meta": {
            "fetched_at": "2026-10-27 17:38:19",
            "elapsed": 3.0978,
            "status": "provisional",
            "as_of": "2026-10-27",
            "fetch_clock": "server",
            "provisional": True,
            "source": {"host": "124.223.177.105", "cmd": "HQServ.PBFXT",
                       "ctl": 14, "clock": "local", "cached": False},
            "series": "880005",
        }
    }

    def diff(mutate):
        other = json.loads(json.dumps(base))
        mutate(other)
        return runner_mod.diff_values(base, other)

    assert diff(lambda d: None) == [], "完全相同的 meta 不该有差异"

    # 放行项：host / fetched_at / elapsed
    assert diff(lambda d: d["meta"]["source"].__setitem__("host", "10.0.0.9")) == [], \
        "source.host 离线不可复现，值变了不该判红"
    assert diff(lambda d: d["meta"].__setitem__("fetched_at", "2026-10-28 09:00:00")) == [], \
        "fetched_at 是录制时刻，变了不该判红"
    assert diff(lambda d: d["meta"].__setitem__("elapsed", 0.0011)) == [], \
        "elapsed 是实测耗时，变了不该判红"

    # 严比项：source 的其余 4 个子键
    for sub in ("cmd", "ctl", "clock", "cached"):
        assert diff(lambda d, s=sub: d["meta"]["source"].__setitem__(s, "__X__")), \
            "source.%s 能离线复现，值变了**必须**判红" % sub

    # 严比项：能复现的 meta 键
    for key in ("status", "as_of", "fetch_clock", "provisional", "series"):
        assert diff(lambda d, k=key: d["meta"].__setitem__(k, "__X__")), \
            "%s 能离线复现，值变了**必须**判红" % key

    # D7 病征：source 退化成字符串
    assert diff(lambda d: d["meta"].__setitem__("source", "人类可读的来源说明")), \
        "source 从 dict 退化成字符串是 D7 的病征，**必须**判红"
    # 键集不完整
    assert diff(lambda d: d["meta"]["source"].pop("ctl")), \
        "source 少一个键**必须**判红（键集必须恰为 5 个）"
    # 多一个键
    assert diff(lambda d: d["meta"]["source"].__setitem__("extra", 1)), \
        "source 多一个键**必须**判红（键集必须恰为 5 个）"


def _runner_source() -> str:
    with open(os.path.join(HERE, "golden_runner.py"), encoding="utf-8") as fh:
        return fh.read()


def test_no_skip_backdoor_in_runner():
    """**源码级**守卫：`FrameMissing` 那条路上不许有任何"跳过"出口。

    复核 N1 实测（V5c）：协同篡改 4 处（帧 args + 两张源码表 + 样本 `meta.frames`
    + manifest 脱钩）就能让 `check` 从 rc=1 变 **rc=0 全绿**，还打印一句假原因
    「录制时本就没有 wire frame」。根因是 `except FrameMissing` 里那个
    `if <该不该有帧>: 判红 else: skipped.append(...)` —— 而**零帧样本永远走不到
    `FrameMissing`**（它在发出任何 wire 请求之前就失败），所以那个 else 分支在
    健康树里是**死代码**，唯一现实功能就是当洗白出口。

    这条测试从**源码文本**上禁止它回来（比行为测试更硬：行为测试只能证明"当前
    没被洗白"，源码断言能证明"洗白通道不存在"）。
    """
    src = _runner_source()
    assert "skipped.append" not in src, (
        "golden_runner.py 里又出现了 `skipped.append` —— 那是 FrameMissing 的洗白出口，"
        "永远不许回来（零帧样本根本走不到 FrameMissing，这个分支只服务于洗白）")
    assert "len(skipped)" not in src, \
        "「跳过数」不许再当统计量用；它必须是一个恒为 0 的断言"
    assert "if has_frames" not in src, (
        "FrameMissing 分支里又出现了以「该不该有帧」为条件的判断 —— "
        "FrameMissing 在健康树里只可能意味着「该有的帧没找到」，一律判红")
    # 「跳过 0 份」必须是**字面量 0**，不是 %d 占位符
    assert "跳过 0 份" in src, (
        "输出里 `跳过 0 份` 必须写成字面量 0（它是断言，不是统计）；"
        "将来若真需要合法跳过，必须新增一种显式的、可机器判定的样本类别，"
        "不许把断言改成 `<= N`")


def test_archived_set_is_anchored():
    """归档集合必须由**源码表**钉死，且每条都指向真实在役的替代样本。

    复核 V6c 实测：把在役样本搬进 `_superseded/` + 补一行**虚构理由**的登记 +
    从两张源码表删掉它 → `check` 与 `pytest` **双双全绿**。原因是「归档」天生
    就是"让样本合法退出比对"的开关，而登记表 6 个字段没有一个可机器判定。

    所以照抄帧锚点的招：归档集合进源码、双向对账、替代样本必须真实在役。
    """
    on_disk = {os.path.basename(p) for p in archived_paths()}
    anchored = set(EXPECTED_ARCHIVED)

    assert not (on_disk - anchored), (
        "这些文件躺在 _superseded/ 里，但源码表 EXPECTED_ARCHIVED 没登记：%s\n"
        "「把在役样本搬走」必须同时在源码里留痕 —— 不许靠一行自由文本就让它退出比对"
        % sorted(on_disk - anchored))
    assert not (anchored - on_disk), (
        "源码表 EXPECTED_ARCHIVED 登记了但 _superseded/ 里没有：%s"
        % sorted(anchored - on_disk))

    live = {os.path.basename(p)[:-5] for p in SAMPLES}
    for fn, info in sorted(EXPECTED_ARCHIVED.items()):
        assert info.get("stage"), "%s：源码表没写变更阶段" % fn
        rep = info.get("replaced_by")
        assert rep, "%s：没写替代样本（归档必须指向真实在役的替代品）" % fn
        assert rep in live, (
            "%s：登记的替代样本 %r 不在役（golden/ 顶层没有它）—— "
            "归档不能凭空声称「有新的在管」，替代品必须真实存在" % (fn, rep))
        assert rep + ".json" not in on_disk, \
            "%s：替代样本 %r 自己也被归档了（不能拿归档品给归档品站岗）" % (fn, rep)

    # 登记表那几行自由文本仍需与源码表**对齐**（阶段列），否则人读的和机器读的会漂移
    rows = registered_rows()
    assert set(rows) == anchored, (
        "_superseded/README.md 登记表 %d 行 vs 源码表 %d 项，集合不一致：\n"
        "  表里有源码没登记：%s\n  源码登记表里没写：%s"
        % (len(rows), len(anchored), sorted(set(rows) - anchored),
           sorted(anchored - set(rows))))
    for fn, info in sorted(EXPECTED_ARCHIVED.items()):
        got_stage = _col(rows[fn], "变更阶段")
        assert got_stage == str(info["stage"]), (
            "%s：登记表写「变更阶段=%s」，源码表写 %r —— 两边必须一致"
            % (fn, got_stage, info["stage"]))
        got_new = _col(rows[fn], "新样本")
        assert got_new.startswith(str(info["replaced_by"])), (
            "%s：登记表「新样本」列写 %r，但源码表说替代品是 %r"
            % (fn, got_new, info["replaced_by"]))

    # 归档品自身完整性：payload 仍等于它自己的 meta.sha256（搬移不是重写）
    for fn in sorted(anchored & on_disk):
        doc = _load(os.path.join(SUPERSEDED, fn))
        assert sha_of(doc["payload"]) == doc["meta"]["sha256"], (
            "%s：归档后 payload 与 meta.sha256 不符 —— 搬移过程中被改过" % fn)


def test_sample_meta_keys_are_anchored():
    """每份样本 `meta` 段必须含源码表点名的语义键（防「删键解约束」）。

    复核 V-白名单实测：`ALLOWED_ADDED_KEYS` 12 项删掉 11 项，门禁**全绿** ——
    因为回放会重新"新增"那些键，而"新增"是放行的。于是「把样本里的键删掉」
    就等于**静默解除约束**。这张源码表把"必须有哪几个键"钉死。

    ⚠️ 与 A′ 正交，别混：A′ 放行的是**值**（fetched_at / elapsed / source.host），
    这里管的是**存在性** —— `elapsed` 的值每次不同，但这个键必须还在。
    """
    # ① 样本集合双向对齐
    names = {os.path.basename(p)[:-5] for p in SAMPLES}
    anchored = set(EXPECTED_SAMPLE_META_KEYS)
    assert names == anchored, (
        "源码表 EXPECTED_SAMPLE_META_KEYS 与磁盘样本集合不一致：\n"
        "  磁盘有源码没登记：%s\n  源码登记了磁盘没有：%s"
        % (sorted(names - anchored), sorted(anchored - names)))

    def collect(payload) -> set:
        found: set = set()
        if not isinstance(payload, dict):
            return found
        cands = [payload.get("meta")]
        val = payload.get("value")
        if isinstance(val, dict):
            cands.append(val.get("meta"))
            tbl = val.get("table")
            if isinstance(tbl, dict):
                cands.append(tbl.get("meta"))
        for c in cands:
            if isinstance(c, dict):
                found |= {str(k) for k in c}
        return found

    # ② 逐份：必须有 ⊇ 源码表
    for path in SAMPLES:
        name = os.path.basename(path)[:-5]
        doc = _load(path)
        got = collect(doc["payload"])
        need = set(EXPECTED_SAMPLE_META_KEYS[name])
        assert need <= got, (
            "%s：样本 meta 段缺了源码表要求必须存在的键 %s\n"
            "  删键 ≠ 解除约束 —— 删掉后回放会把它们当「新增」放行，"
            "约束就静默消失了" % (name, sorted(need - got)))

    # ③ 白名单每一项：要么被某份样本覆盖，要么在「覆盖不到」清单里给出原因。
    #    这样"把某键从所有样本的 required 里删掉"也会红（闭集守卫）。
    union = set()
    for keys in EXPECTED_SAMPLE_META_KEYS.values():
        union |= set(keys)
    covered_or_declared = union | set(META_KEYS_NOT_COVERED_BY_GOLDEN)
    allow = set(ALLOWED_ADDED_KEYS) - {"meta"}
    missing = allow - covered_or_declared
    assert not missing, (
        "白名单键 %s 既没有被任何样本的 EXPECTED_SAMPLE_META_KEYS 覆盖，"
        "也不在 META_KEYS_NOT_COVERED_BY_GOLDEN 里 —— 它等于不受任何约束"
        % sorted(missing))

    # ④ 记账清单本身是闭集：不许有"已覆盖却还声称覆盖不到"的僵尸项
    for key, why in META_KEYS_NOT_COVERED_BY_GOLDEN.items():
        assert key in allow, (
            "%s 不在 ALLOWED_ADDED_KEYS 里，记账清单却是闭集 —— 请同步" % key)
        assert key not in union, (
            "%s 已被样本覆盖，不能再列进「覆盖不到」清单（僵尸记账）" % key)
        assert len(str(why or "").strip()) >= 20, (
            "%s 的「覆盖不到」原因太短，写清为什么不覆盖" % key)

    # ⑤ runner 与契约测试两份表逐项一致
    sys.path.insert(0, HERE)
    try:
        import golden_runner as runner_mod
    finally:
        sys.path.pop(0)
    assert {k: sorted(v) for k, v in runner_mod.EXPECTED_SAMPLE_META_KEYS.items()} \
        == {k: sorted(v) for k, v in EXPECTED_SAMPLE_META_KEYS.items()}, (
        "runner 与契约测试的 EXPECTED_SAMPLE_META_KEYS 不一致")
    assert dict(runner_mod.EXPECTED_ARCHIVED) == dict(EXPECTED_ARCHIVED), (
        "runner 与契约测试的 EXPECTED_ARCHIVED 不一致")
    assert set(runner_mod.META_KEYS_NOT_COVERED_BY_GOLDEN) \
        == set(META_KEYS_NOT_COVERED_BY_GOLDEN), (
        "runner 与契约测试的 META_KEYS_NOT_COVERED_BY_GOLDEN **键集**不一致："
        "runner=%s 契约=%s（说明文字可以各自表述，键集必须一致）"
        % (sorted(runner_mod.META_KEYS_NOT_COVERED_BY_GOLDEN),
           sorted(META_KEYS_NOT_COVERED_BY_GOLDEN)))


def test_offline_replay_diff_is_zero():
    """离线回放（不联网）结果与冻结样本逐字段一致，且报告里带明确基数。"""
    assert os.path.exists(RUNNER), "找不到 %s" % RUNNER
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.pop("PYTHONPATH", None)
    proc = subprocess.run([sys.executable, RUNNER, "check"], cwd=ROOT, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=300)
    out = (proc.stdout or b"").decode("utf-8", "replace")
    assert proc.returncode == 0, "golden_runner check 退出码 %s：\n%s" % (proc.returncode, out)

    # 基数必须明确：报告的样本份数要等于实际样本文件数
    assert "无样本" not in out, "runner 报了「无样本」，说明 fixtures 没落好：\n%s" % out
    base_line = [ln for ln in out.splitlines() if "样本基数：" in ln]
    assert base_line, "runner 没打印样本基数行：\n%s" % out
    base = int(base_line[-1].split("样本基数：")[1].split("份")[0].strip())
    assert base == len(SAMPLES), \
        "runner 报的基数 %d 与目录里的 %d 份样本不一致" % (base, len(SAMPLES))

    tail = [ln.strip() for ln in out.splitlines()
            if "份，比对" in ln and "差异" in ln]
    assert tail, "runner 没打印「N 份样本，比对 M 份，跳过 K 份，差异 D」行：\n%s" % out
    assert "差异 0" in tail[-1], "回放存在差异：%s\n%s" % (tail[-1], out)

    # 「跳过 K 份」必须**精确**是 0：跳过**不进 rc**，等于一条后门。
    # 复核者全仓 grep 确认过：此前没有任何测试管这个数。
    # ⚠️ 将来若真出现合法的"无帧可跳过"样本（像 err_bars_IF00 那样在建帧前就失败），
    #    **不许**把这条改成模糊断言（`跳过`/`跳过 <= N` 之类），必须改成
    #    「跳过份数 == manifest 里登记的零帧样本数」，口径可见 —— 而不是不查。
    assert "跳过 0 份" in out, (
        "runner 报了跳过，跳过不进 rc 等于后门；若属新增的零帧样本，"
        "请在源码表 EXPECTED_SAMPLE_FRAMES 里登记为 \"unavailable\" 并把本断言"
        "改成「跳过份数 == 登记的零帧样本数」：\n%s" % tail[-1])

    # 「已归档样本 N 份」这行**0 份也必须打** —— 让人看得见"样本为什么少了",
    # 否则阶段 2/4 归档几份之后，谁也不知道 23 变 19 是正常的还是丢文件了。
    arc_line = [ln.strip() for ln in out.splitlines() if "已归档样本" in ln]
    assert arc_line, "runner 没打印「已归档样本 N 份（有意变更，见 _superseded/README.md）」行：\n%s" % out
    arc = int(arc_line[-1].split("已归档样本")[1].split("份")[0].strip())
    assert arc == len(archived_paths()), \
        "runner 报的已归档 %d 份与 _superseded/ 下实际的 %d 份不一致" % (arc, len(archived_paths()))


def test_allowed_added_keys_matches_runner():
    """本文件的"允许新增键"清单必须与 runner 的实现一致（两边各写一份，互为校验）。"""
    sys.path.insert(0, HERE)
    import golden_runner as gr

    assert gr.ALLOWED_ADDED_KEYS == ALLOWED_ADDED_KEYS, (
        "test_golden_contract 与 golden_runner 的允许新增键清单不一致："
        "runner=%s，本文件=%s"
        % (sorted(gr.ALLOWED_ADDED_KEYS), sorted(ALLOWED_ADDED_KEYS)))


def test_container_meta_is_recorded_but_never_judged():
    """容器 meta（阶段 1 的 `Bars`/`Quotes`/`Minutes`/`Ticks`.meta）必须被录进 payload，
    但**只能作信息性输出**、绝不能让 check 判 FAIL。

    守的是两件容易一起退化的东西：
      ① `json.dumps` 看不见普通 list 的 `.meta` —— 不显式取出来就等于门禁漏掉本阶段产出；
      ② `status`/`elapsed`/`fetched_at` 本来每次都变 —— 一旦进主 diff，门禁就会被关掉。
    """
    sys.path.insert(0, HERE)
    import golden_runner as gr

    class CaseBars(list):        # 冒充阶段 1 的 `Bars`：list 子类 + .meta
        pass

    obj = CaseBars([{"code": "600519", "close": 1335.0}])
    obj.meta = {"as_of": "2026-10-27", "status": "final", "elapsed": 0.42}

    payload = gr.encode_ok(obj)
    assert payload["kind"] == "list[dict]", "容器类名不该改变 kind（老样本按元素类型记）"
    assert payload["value"] == [{"code": "600519", "close": 1335.0}]
    assert payload.get("meta") == obj.meta, "带 .meta 的 list 子类必须把 meta 记成同级键"

    assert "meta" not in gr.encode_ok([{"code": "600519"}]), \
        "普通 list / tuple 不该凭空多出 meta 键"

    # meta 键单独出现在"新增"侧时按 ALLOWED_ADDED_KEYS 放行（老样本没有它）
    old = {"outcome": "ok", "kind": "list[Bar]", "value": []}
    new = dict(old, meta={"status": "final"})
    assert gr.diff_values(old, new) == [], "样本无 meta 时，回放新增 meta 不该算差异"
    assert "meta" in gr.ALLOWED_ADDED_KEYS
