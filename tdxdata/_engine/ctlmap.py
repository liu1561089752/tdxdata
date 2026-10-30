# -*- coding: utf-8 -*-
"""`ctlmap` —— 命令名 → ctl / 请求体形态 / 参数模板 的**查表层**。

它解决什么问题
--------------
帧头里的 `ctl` 是服务端的**命令分发键**：错了不报错，只回空包。所以调用
`datacenter.call(cmd, ctl, params)` 时必须先知道 ctl。以前这些 ctl 散落在各个
手写封装里（`def ztqd(): return _call("ph_tdxdata_ztqd", 0x1E, …)`）。

本模块把**实测**出来的 ctl 集中成一张表（`catalog/ctl_map.json`），
调用方只需给命令名，就能拿到 ctl、请求体形态和参数模板。

数据从哪来
----------
不是猜的，是 2026-10~11 逐条打服务器验证出来的（33 个候选值 × 数百条命令），
方法见 `notes/33-APK全面分析-还有什么功能没被发现.md` 第 19~24 节。
`verified: true` 表示该命令实测能取到数据；没有这个标记的只是 ctl 确认对了。

三种请求体形态（也是实测出来的）
--------------------------------
* `base`  `{"Params": [...]}`        —— 大多数 `CWServ.*`
* `call`  `{"CallName": "...", "Params": [...]}` —— 少部分 `CWServ.*`（省了会**静默丢包**）
* `bare`  裸 JSON                    —— `HQServ.*` / `CWSearch.*` / `DataAggregation.*`
* `obj`   `{"Params": {"p1": …}}`    —— 少数接口要对象而不是数组

注意：`HQServ.PB*` 一族不在本表覆盖范围内——它们要的是 protobuf 二进制体，
走 `pb_api` 那条通路。

用法
----
    from tdxdata._engine import ctlmap

    ctlmap.ctl_for("CWServ.ph_datacenter_mghg_xf")     # -> 30
    ctlmap.form_for("SNS.query_Recommend")             # -> 'call'
    ctlmap.build_body("SNS.query_Recommend", [])       # -> b'{"CallName": …}'
    ctlmap.fill(ctlmap.params_for("CWServ.ph_agf10_gsjj"), code="600900")

本模块**不发请求、不读网络**，纯查表；导入开销只有一次 JSON 读取。
"""
import json
import os

__all__ = [
    "names", "lookup", "ctl_for", "form_for", "params_for",
    "build_body", "fill", "stats", "PLACEHOLDER",
]

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog", "ctl_map.json")

#: 参数模板里的占位符 → 常用取值。日期留空表示「由调用方给」。
PLACEHOLDER = {
    "@code@": "600900", "#code#": "600900",
    "@sc@": "1", "#sc#": "1", "#type#": "1", "@type@": "1",
    "#setcode#": "1", "@setcode@": "1", "#market#": "1", "@market@": "1",
    "#curPage#": "1", "@curPage@": "1", "#page#": "1", "#pageSize#": "20",
    "#count#": "20", "@count@": "20",
}

_cache = None


def _load():
    """读一次表，之后都在内存里。缺文件时返回空表（库要能在无数据时照常导入）。"""
    global _cache
    if _cache is None:
        try:
            with open(_PATH, encoding="utf-8") as f:
                doc = json.load(f)
            _cache = doc.get("commands") or {}
        except FileNotFoundError:
            _cache = {}
    return _cache


def names(prefix=""):
    """所有已知命令名（可按命名空间前缀过滤，如 `names("SNS.")`）。"""
    return sorted(k for k in _load() if k.startswith(prefix))


def lookup(cmd):
    """返回 `{"ctl":…, "form":…, "params":…, "verified":…}`；未知命令返回 `None`。"""
    rec = _load().get(cmd)
    return dict(rec) if rec else None


def ctl_for(cmd):
    """命令 → ctl（整数）。未知返回 `None`——**不要**猜一个默认值，
    错的 ctl 不会报错，只会回空包，是最难查的一类 bug。"""
    rec = _load().get(cmd)
    return rec["ctl"] if rec else None


def form_for(cmd):
    """命令 → 请求体形态（`base` / `call` / `bare` / `obj`），未知按 `base`。"""
    rec = _load().get(cmd)
    return rec.get("form", "base") if rec else "base"


def params_for(cmd):
    """命令 → 参数模板（可能含占位符）；没有模板返回 `None`。"""
    rec = _load().get(cmd)
    if not rec:
        return None
    return rec.get("params")


def _json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def build_body(cmd, params=None):
    """按实测形态把参数编成 `00 00` 之后的那段字节。

    `params` 为 `None` 时用表里的模板（未做占位符替换，需要替换请先 `fill`）。
    """
    if params is None:
        params = params_for(cmd)
        if params is None:
            params = []
    form = form_for(cmd)
    if form == "call":
        short = cmd.split(".", 1)[1] if "." in cmd else cmd
        return _json_bytes({"CallName": short, "Params": params})
    if form == "bare":
        return _json_bytes(params)
    if form == "obj":
        if isinstance(params, list):
            params = {"p%d" % (i + 1): v for i, v in enumerate(params)}
        return _json_bytes({"Params": params})
    return _json_bytes({"Params": params})


def fill(params, code=None, date=None):
    """把参数模板里的占位符换成可用值（返回新对象，不改原模板）。

    `code` 给股票代码（默认 600900）；`date` 给 `#date#`/`@date@` 的值，
    不给就留空串——日期格式各接口不一致，交给调用方更安全。
    """
    sub = dict(PLACEHOLDER)
    if code is not None:
        sub["@code@"] = sub["#code#"] = str(code)
    date_val = "" if date is None else str(date)

    def one(x):
        if isinstance(x, str):
            out = x
            for k, v in sub.items():
                out = out.replace(k, v)
            for k in ("#date#", "@date@", "#Date#"):
                out = out.replace(k, date_val)
            return out
        if isinstance(x, list):
            return [one(i) for i in x]
        if isinstance(x, dict):
            return {k: one(v) for k, v in x.items()}
        return x

    return one(params)


def stats():
    """表的基本统计：总数 / 有参数 / 已验证 / 各形态条数。"""
    d = _load()
    forms = {}
    for rec in d.values():
        f = rec.get("form", "base")
        forms[f] = forms.get(f, 0) + 1
    return {
        "total": len(d),
        "with_params": sum(1 for r in d.values() if "params" in r),
        "verified": sum(1 for r in d.values() if r.get("verified")),
        "forms": forms,
    }
