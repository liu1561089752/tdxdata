# -*- coding: utf-8 -*-
"""B7 批次测试：call_pb None 语义（DEF-043）+ 容器序列化（DEF-024）。

零网络：`pb_api._raw_send` 全 monkeypatch（mock 假 socket 语义，不建真连接）。
门禁第 7 条：本文件不出现 `Client(` / `Session(` / `AsyncClient(` 字面量，
用 getattr 风格引用（如需测上层行为走 `hq_pb` 纯函数层）。
"""
import datetime
import json

import pytest

from tdxdata import errors
from tdxdata._provenance import ClockSource, Provenance
from tdxdata import models as M
from tdxdata._engine import pb_api as PA


# ══════════════════════════════════════════════════════════════════════════
# 一、DEF-043：call_pb「答了但解不出」不再静默 None
# ══════════════════════════════════════════════════════════════════════════

def test_call_pb_unparseable_reply_raises_not_none(monkeypatch):
    """raw 回包（答了但解不出）→ EmptyResult，绝不 return None（DEF-043）。"""
    monkeypatch.setattr(PA, "_raw_send",
                        lambda c, cmd, args, tries=4: b"\xde\xad\xbe\xef")
    with pytest.raises(errors.EmptyResult) as ei:
        PA.call_pb("HQServ.Unknown", ctl=0x0E)
    assert "HQServ.Unknown" in str(ei.value)          # cmd 上下文必须在
    assert "解不出" in str(ei.value)


def test_call_pb_no_candidates_raises_not_none(monkeypatch):
    """无候选 ctl 可用（且无传输异常）→ 同样 EmptyResult，不静默 None。"""
    monkeypatch.setattr(PA, "_raw_send",
                        lambda c, cmd, args, tries=4:
                        (_ for _ in ()).throw(AssertionError("不该被调到")))
    with pytest.raises(errors.EmptyResult):
        PA.call_pb("HQServ.Unknown", ctl=None)        # PB 里查无此 cmd → 无候选


def test_call_pb_transport_failure_still_typed(monkeypatch):
    """传输失败仍抛传输类异常（批次 A 语义不变），与「解不出」可区分。"""
    def boom(c, cmd, args, tries=4):
        raise errors.ConnectionFailed("down")
    monkeypatch.setattr(PA, "_raw_send", boom)
    with pytest.raises(errors.ConnectionFailed):
        PA.call_pb("HQServ.PBFXT")                    # 多候选 ctl 全失败


def test_call_pb_normal_paths_unchanged(monkeypatch):
    """正常路径行为不变：JSON → dict；表 → (cols, rows)。"""
    monkeypatch.setattr(PA, "_raw_send",
                        lambda c, cmd, args, tries=4: b'{"ok": 1}')
    assert PA.call_pb("HQServ.Unknown", ctl=0x0E) == {"ok": 1}
    monkeypatch.setattr(PA, "_raw_send",
                        lambda c, cmd, args, tries=4: PB_SAMPLE)
    cols, rows = PA.call_pb("HQServ.Unknown", ctl=0x0E)
    assert cols == ["Data", "Second", "Close"] and rows


def _pb_sample() -> bytes:
    """与 test_pb_api_offline_b4.py 同构的表形状样本（列名 field6 + 行 field7）。"""
    def vint(v):
        out = bytearray()
        while True:
            b = v & 0x7F
            v >>= 7
            if v:
                out.append(b | 0x80)
            else:
                out.append(b)
                return bytes(out)

    def ld(f, payload):
        return vint(f << 3 | 2) + vint(len(payload)) + payload

    names = b"".join(ld(1, c.encode()) for c in ("Data", "Second", "Close"))
    rows = b""
    for d, t, c in [("20260427", "0", "12.3"), ("20260428", "1", "12.5")]:
        cells = b"".join(ld(1, x.encode()) for x in (d, t, c))
        rows += ld(7, cells)
    return ld(6, names) + rows


PB_SAMPLE = _pb_sample()


# ══════════════════════════════════════════════════════════════════════════
# 二、DEF-024：容器序列化
# ══════════════════════════════════════════════════════════════════════════

def _bar(**kw):
    base = dict(market=1, code="600519", date="20261028", open=1.0,
                high=2.0, low=0.5, close=1.5, vol=100.0, amount=150.0)
    base.update(kw)
    return M.Bar(**base)


def _quote(**kw):
    base = dict(market=1, code="600519", price=1.5, vol=10.0, amount=15.0)
    base.update(kw)
    return M.Quote(**base)


def _tick(**kw):
    base = dict(market=1, code="600519", time="09:30:00", price=1.5, vol=1.0)
    base.update(kw)
    return M.Tick(**base)


# ── Series 家族（Bars/Quotes/Ticks/Minutes 共用 Series 实现）──────────────
@pytest.mark.parametrize("cls", [M.Bars, M.Quotes, M.Ticks, M.Minutes])
def test_series_json_roundtrip(cls):
    prov = Provenance(
        fetched_at=datetime.datetime(2026, 10, 28, 15, 0, 1),
        clock=ClockSource.SERVER, host="x", cmd="HQServ.PBFXT", ctl=14,
        status="final", as_of="2026-10-28 15:00:00",
        extra={"missing_cols": ["VOL"]})
    s = cls([_bar(), _bar(date="20261027")], provenance=prov)
    obj = json.loads(s.to_json())
    assert set(obj) == {"data", "provenance"}
    assert obj["provenance"]["status"] == "final"    # provenance 完整带出
    assert obj["provenance"]["fetched_at"].startswith("2026-10-28 15:00:01")
    assert obj["provenance"]["cmd"] == "HQServ.PBFXT"
    assert obj["provenance"]["extra"]["missing_cols"] == ["VOL"]
    assert len(obj["data"]) == 2
    assert obj["data"][0]["date"] == "20261028"
    assert obj["data"][0]["symbol"] == "sh600519"    # dataclass property 进 dict
    # 裸 `json.dumps(s)` 依旧 TypeError（list 子类契约未变，出口是 to_json）
    with pytest.raises(TypeError):
        json.dumps(s)


def test_series_roundtrip_keyset_matches_to_dict():
    s = M.Bars([_bar()], provenance=Provenance(
        fetched_at=datetime.datetime(2026, 10, 28, 15, 0, 1),
        clock=ClockSource.SERVER, cmd="HQServ.PBFXT",
        status="final", elapsed=0.5))
    d = s.to_dict()
    assert set(d) == {"data", "provenance"}
    j = json.loads(s.to_json())
    assert set(j["data"][0]) == set(d["data"][0])


def test_series_datetime_iso():
    when = datetime.datetime(2026, 10, 28, 15, 0, 1)
    s = M.Quotes([_quote(server_time="")], provenance=Provenance(
        fetched_at=when, clock=ClockSource.SERVER, cmd="HQServ.PBFA"))
    obj = json.loads(s.to_json())
    assert obj["provenance"]["fetched_at"].startswith("2026-10-28 15:00:01")


# ── Table ────────────────────────────────────────────────────────────────
def test_table_to_json_roundtrip_provenance():
    t = M.Table(cols=["N001", "N002"], rows=[{"N001": "20261028", "N002": "x"}],
                cmd="DC.ErrRate", domain="7615",
                provenance=Provenance(
                    fetched_at=datetime.datetime(2026, 10, 28, 9, 30),
                    clock=ClockSource.SERVER, cmd="DC.ErrRate",
                    as_of=None, status="unregistered"))
    obj = json.loads(t.to_json())
    assert set(obj) == {"cols", "rows", "cmd", "domain", "provenance"}
    assert obj["rows"] == t.to_dicts()
    assert obj["provenance"]["fetched_at"].startswith("2026-10-28 09:30:00")
    assert obj["provenance"]["as_of"] is None


def test_table_to_dicts_unchanged():
    """既有 `to_dicts()` 签名与行为不动（test_table_contract.py 的判据）。"""
    t = M.Table(cols=["a"], rows=[[1]])
    assert t.to_dicts() == [{"a": 1}]


def test_table_to_dict_equals_to_json_payload():
    t = M.Table(cols=["a"], rows=[[1]], provenance=Provenance(
        fetched_at=datetime.datetime(2026, 10, 28, 9, 30),
        clock=ClockSource.SERVER, cmd="T", extra={"k": 1}))
    assert t.to_dict() == json.loads(t.to_json())


# ── Dataset ──────────────────────────────────────────────────────────────
def test_dataset_json_roundtrip():
    ds = M.Dataset(name="成交概览", table=M.Table(
        cols=["N001"], rows=[[{"N001": "20261028"}][0]],
        provenance=Provenance(
            fetched_at=datetime.datetime(2026, 10, 28, 9, 30),
            clock=ClockSource.SERVER, cmd="T", status="final")),
        params={"start": "20261028"})
    obj = json.loads(ds.to_json())
    assert set(obj) == {"name", "params", "table"}
    assert obj["table"]["provenance"]["status"] == "final"
    assert ds.to_dicts() == [{"N001": "20261028"}]


# ── 裸 dataclass 行（Bar/Quote/Tick 直接进 to_dict/asdict 路径）────────────
def test_row_dataclasses_jsonable():
    for obj in (_bar(), _quote(), _tick()):
        d = json.loads(json.dumps(M._jsonable(obj)))
        assert d["code"] == "600519"
        assert d["symbol"] == "sh600519"


def test_jsonable_odds_and_ends():
    when = datetime.date(2026, 10, 28)
    payload = {"d": when, "b": b"ab", "s": {2, 1}, "t": (1, 2),
               "weird": object()}
    out = M._jsonable(payload)
    assert out["d"] == "2026-10-28"
    assert out["b"] == "ab"
    assert out["s"] == [1, 2]                        # set → sorted list（稳定）
    assert out["t"] == [1, 2]
    assert isinstance(out["weird"], str)             # 兜底不抛 TypeError
    assert json.dumps(out)                           # 整体可 dumps


# ── 完整链路：call_pb 结果形态可序列化（带 meta 的 Bars 是重点）────────────
def test_bars_full_chain_json_roundtrip():
    bars = M.Bars([_bar(), _bar(date="20261027", close=1.4)],
                  provenance=Provenance(
                      fetched_at=datetime.datetime(2026, 10, 28, 15, 0, 1),
                      clock=ClockSource.SERVER, host=None,
                      cmd="HQServ.PBFXT", ctl=14,
                      status="final", as_of="20261028"))
    text = bars.to_json()
    obj = json.loads(text)
    assert obj["provenance"]["ctl"] == 14
    assert [b_["date"] for b_ in obj["data"]] == ["20261028", "20261027"]
