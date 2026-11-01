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

三档状态与形态证据（边车文件，缺了也照常 import）
--------------------------------------------------
* `catalog/verified.json` —— 421 条实测 `rows>0` 的「已验证有数据」证据 → `tier_for()`
* `catalog/dead_cmds.json` —— 184 条「本版本 App 未实现（回 104）」 → `tier_for()`
  （2026-11-01 真机复验后由 191 降到 184：7 条产品命令实测有数据，已移入 `verified.json`）
* `catalog/form_measured.json` —— 20 条**有硬证据**的实测请求体形态 → `form_measured_for()`
  （门槛是"两种等价硬证据之一"，见 `form_measured_for()` docstring：
  ① 抓包 ∩ `api_catalog_usable.body_form` 双源一致 = 19 条；
  ② 抓包原文重放命中 `rows>0` = 1 条，即 `CWServ.tzx_filedown`，重放拿到 47 行 × 8 列）

**包内只有 7 个 json**：`ctl_map` / `verified` / `dead_cmds` / `form_measured` /
`page_cmd_params` / `pb_specs` / `raw_args`。**原始素材不在包里** ——
`not_in_app.json` / `verified_working.json` / `api_catalog_usable.json` /
`pcap_req_args.json` 都是**仓库外** `F:/TDX/raw/catalog/` 的一次性实机快照
（2026-10-31，未随包分发）。**排障请查包内的生成物**，别去找那些素材，否则会扑空。

`ctl_map.json` 自己的 `verified: true`（537 条）与 `probe_ok`（55 条）是**历史布尔
标记**，和上面三份证据文件口径不同，别混（详见 `stats()` docstring）。

用法
----
    from tdxdata._engine import ctlmap

    ctlmap.ctl_for("CWServ.ph_datacenter_mghg_xf")     # -> 30
    ctlmap.form_for("SNS.query_Recommend")             # -> 'call'（兜底用）
    ctlmap.form_measured_for("HQServ.hq_nlp_misc")     # -> 'bare'（双源实测）
    ctlmap.tier_for("HQServ.ZST")                      # -> 'verified'
    ctlmap.params_best_for("HQServ.ZST")               # -> 真参数（元数据已用 verified.json 换掉）
    ctlmap.build_body("SNS.query_Recommend", [])       # -> b'{"CallName": …}'
    ctlmap.fill(ctlmap.params_for("CWServ.ph_agf10_gsjj"), code="600900")

⚠️ 要发请求请用 `params_best_for(cmd)` 取模板：`params_for()` 对 25 条命令返回的是
**抓包元数据**（`args_hex` / `pcap`），直接发出去必然空包。

本模块**不发请求、不读网络**，纯查表；导入开销只有一次 JSON 读取。
"""
import copy
import json
import os

__all__ = [
    "names", "lookup", "ctl_for", "form_for", "params_for", "params_best_for",
    "build_body", "fill", "stats", "PLACEHOLDER",
    "tier_for", "form_measured_for", "TIERS", "DATE_PLACEHOLDER",
]

_CATALOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog")
_PATH = os.path.join(_CATALOG_DIR, "ctl_map.json")
#: 三档状态的**证据文件**（与 `ctl_map.json` 的布尔标记分开，别混口径）。
_VERIFIED_PATH = os.path.join(_CATALOG_DIR, "verified.json")
_DEAD_PATH = os.path.join(_CATALOG_DIR, "dead_cmds.json")
#: 进这张表要满足**两种等价硬证据之一**（合计 **20 条**）：
#: ① **双源一致**（19 条）：源 A = 线上抓包 `pcap_req_args.json` 的 `args_hex` 解码
#:    + 包内的 `raw_args.json`，源 B = `api_catalog_usable.json[cmd]["body_form"]`，
#:    两者相等且唯一才收；
#: ② **抓包原文重放命中**（1 条：`CWServ.tzx_filedown` = `bare`）：
#:    抓包体 `{"filename":…,"format":"1"}` 被**原样**发出后拿到 47 行 × 8 列
#:    （`verified_working.json` 记 `{"rows":47,"cols":8,"via":"args_hex"}`）。
#:    为什么这条不算"单源"：`body_form` 对它是**同义反复** ——
#:    `merge_fills.py` 的 `body_form = w.get("form")`，而 `w["form"]` 来自
#:    `ctl_map` 的 `form` 字段（`fill_params.py` 记录时取 `e.get("form")`），
#:    等于拿目录的猜测去"印证"目录，不是观测值。重放命中行数才是观测。
#: 素材都在**仓库外** `F:/TDX/raw/catalog/`（2026-10-31 快照，未随包分发）。
#: 不是 732 条的缺省填充，也不是那 63 条单源 `body_form`（它与抓包有 12 条冲突）。
_FORM_MEASURED_PATH = os.path.join(_CATALOG_DIR, "form_measured.json")

#: `fill()` 认的**日期族**占位符：语义完全一致，全走 `date` 参数；
#: 调用方不给就填空串（库不猜日期格式，见 `fill()` docstring）。
DATE_PLACEHOLDER = ("#date#", "@date@", "#Date#", "#dateTime#", "#endDate#",
                    "#today#", "#stockDay#")

#: `tier_for()` 可能返回的三档（外加 `None` = 没证据）。`dead` 优先于 `verified`。
TIERS = ("dead", "verified", "probe_ok")

#: 参数模板里的占位符 → 缺省取值。日期族不在这里（走 `date` 参数）。
#:
#: 分两类，**别混**：
#: 1. 有安全缺省（沿用历史值，不传也大概率能通）；
#: 2. ``""`` —— 必须由调用方提供。填空串只是「参数位不缺、帧能发出去」，
#:    **不是**能取到数据的业务值；要数据请走 `fill(..., extra={...})`。
#:    这里刻意不编任何「看起来合理」的数字（宁可空，也不假装完整）。
PLACEHOLDER = {
    "@code@": "600900", "#code#": "600900",
    "@sc@": "1", "#sc#": "1", "#type#": "1", "@type@": "1",
    "#setcode#": "1", "@setcode@": "1", "#market#": "1", "@market@": "1",
    "#curPage#": "1", "@curPage@": "1", "#page#": "1", "#pageSize#": "20",
    "#count#": "20", "@count@": "20",
    # `#setCode#`（大写 C）与 `#setcode#`（小写）是**两个不同的键**，不是笔误：
    # 前者只有 1 条命令用（`HQServ.hq_nlp_xg` 的 `"Setcode"` 字段），语义相同（市场代码）。
    "#setCode#": "1",
    # ↓ 以下 13 个必须由调用方给，缺省空串；语义见 `fill()` docstring。
    "@name@": "", "#name1#": "", "#name2#": "",
    "@hycode@": "", "@qsid@": "", "@jgid@": "", "@cstype@": "",
    "#entrycode#": "", "#subTypeCode#": "", "#SelfStock#": "",
    "#flag#": "", "#sortNum#": "", "#sortType#": "",
}

_cache = None
_verified_cache = None
_dead_cache = None
_form_measured_cache = None


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


def _load_optional(path):
    """懒加载边车 JSON：**缺文件/坏文件都返回空表**，绝不让 import 失败。

    这三份证据文件是可选资产（`verified.json` / `dead_cmds.json` /
    `form_measured.json`），老版本安装包里可能没有 —— 那时 `tier_for()` 一律
    返回 `None`、`form_measured_for()` 一律返回 `None`，库照常工作。
    """
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _verified():
    global _verified_cache
    if _verified_cache is None:
        _verified_cache = _load_optional(_VERIFIED_PATH)
    return _verified_cache


def _dead():
    global _dead_cache
    if _dead_cache is None:
        _dead_cache = _load_optional(_DEAD_PATH)
    return _dead_cache


def _form_measured():
    global _form_measured_cache
    if _form_measured_cache is None:
        _form_measured_cache = _load_optional(_FORM_MEASURED_PATH)
    return _form_measured_cache


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
    """命令 → 请求体形态（`base` / `call` / `bare` / `obj`），未知按 `base`。

    ⚠️ **这不是实测证据，只是兜底编码。** `ctl_map.json` 里 732 条都有 `form`
    字段，但其中 **669 条的 `form` 是生成时的缺省填充**（几乎全填成 `base`），
    只有 20 条有硬证据支撑（见 `form_measured_for()`）。

    反证（别按前缀猜、也别按缺省值猜）：
    * `DataAggregation.*` 共 23 条在表里是 `base`，但实测必须**裸发**；
    * 同族的 `DataAggregation.queryZbsq` / `queryMarketSentiment` 实测又恰恰是 `base`；
    * 单源的 `api_catalog_usable.json["body_form"]`（63 条）也**不是可信证据**：
      它与线上抓包原文有 12 条自相矛盾（如 `CWSearch.tzx_rcache` 记 `base`、
      抓包却是裸 JSON；`CWServ.ph_tdxdatacenter_jgpj_sy` 记 `call`、
      抓包却是 `{"Params": […]}`）。该文件在**仓库外** `F:/TDX/raw/catalog/`
      （2026-10-31 快照），包里没有，别去翻。

    所以要**证据**请用 `form_measured_for(cmd)`（没实测就返回 `None`，
    不回退 `"base"`）；本函数只适合「没有实测时的兜底」，行为对未知命令仍是 `base`。
    """
    rec = _load().get(cmd)
    return rec.get("form", "base") if rec else "base"


def form_measured_for(cmd):
    """命令 → **有硬证据**的请求体形态（`base` / `call` / `bare` / `obj`）。

    **收录门槛 = 两种等价硬证据之一**（当前共 20 条，落在包内
    `catalog/form_measured.json`）：

    ① **双源一致**（19 条）—— 两个独立证据源结论相等且唯一：
       源 A = 线上抓包正文（`pcap_req_args.json` 的 `args_hex` 解码 + 包内的
       `raw_args.json`）按首键判定（`{"CallName"` → `call`，`{"Params"` → `base`，
       其余可解析 JSON → `bare`）；源 B = `api_catalog_usable.json[cmd]["body_form"]`。
    ② **抓包原文重放命中**（1 条：`CWServ.tzx_filedown` = `bare`）——
       把抓包体 `{"filename":…,"format":"1"}` **原样**发出去，拿到
       **47 行 × 8 列**（`verified_working.json` 记 `via="args_hex"`，
       即 `bytes.fromhex(entry["args_hex"])` 直发；`score()` 只在 `rows>0`
       时才记录命中）。这条的 `body_form` 记 `base` 属于**同义反复**，不算观测：
       `merge_fills.py` 里 `body_form = w.get("form")`，而 `w["form"]` 来自
       `ctl_map` 的 `form` 字段 → 拿目录的猜测去"印证"目录。
       所以它是"两份不同性质证据（重放命中 vs 目录缺省）"，证据等级**高于**①。

    同一条命令抓到多种形态（说明服务端都收）、解码不出 JSON（protobuf 体）的都不作判据。
    素材都在**仓库外** `F:/TDX/raw/catalog/`（2026-10-31 快照，未随包分发）。

    没命中就返回 `None` —— **不许回退成 `"base"`**：缺省填充的 `base` 用在
    `DataAggregation.*` 这类必须裸发的接口上会静默丢包（不报错、只回空包）。

    调用方（如 `session.body_of`）的正确用法是「有实测用实测，`None` 才走
    自己的前缀规则」：这样其余 712 条的行为一字不变，20 条按硬证据校正。
    """
    return _form_measured().get(cmd)


def params_for(cmd):
    """命令 → **目录里的**参数模板（可能含占位符）；没有模板返回 `None`。

    ⚠️ 少数命令（含 `args_hex` 的 25 条）这里返回的是**抓包元数据**，
    发请求请用 `params_best_for()`。本函数行为（含返回原对象）保持不变。

    两个模板函数的差别：本函数管「**目录模板**」（原样、可能带占位符或元数据），
    `params_best_for()` 管「**实际发出去的参数**」（会换掉元数据、收敛多次抓包）。
    护栏分工：本函数由 `tests/test_placeholder_guard.py` 的前 3 条用例守
    （全表 `#xxx#`/`@xxx@` 残留 = 0），`params_best_for` 由同文件
    `test_real_send_path_has_no_placeholder_residue` 守 —— **两条路径都要守**，
    只守前者会漏掉那 25 条元数据命令在真实发送路径上的表现。
    """
    rec = _load().get(cmd)
    if not rec:
        return None
    return rec.get("params")


def _is_capture_meta(params):
    """参数模板像不像**抓包元数据**：list 且**任一**元素是 dict 且含 `args_hex` 键。

    判据只认 `args_hex`（业务参数里不会出现这个键）。**刻意不**把 `pcap` / `ctl`
    也纳入判据：业务参数里真有可能有叫 `ctl` 的字段，不赌。
    """
    if not isinstance(params, list):
        return False
    return any(isinstance(p, dict) and "args_hex" in p for p in params)


def _decode_capture_args(params):
    """从抓包元数据里解出**一次**调用的真实参数：取**第一条**能解出来的 `args_hex`。

    只取第一条 —— 同一命令的多条抓包是**N 次不同调用**的样本，拼成一个列表
    不是"模板"。解码链：`bytes.fromhex` → `utf-8` → `json.loads`，任一环失败就
    试下一条；全失败（protobuf 通道：`HQServ.PB*` / `FastHQ.Subscribe` 等）返回
    `None`。**绝不**把 hex 字符串 / base64 原样当参数返回。
    """
    if not isinstance(params, list):
        return None
    for p in params:
        if not isinstance(p, dict):
            continue
        hx = p.get("args_hex")
        if not isinstance(hx, str):
            continue
        try:
            text = bytes.fromhex(hx).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        try:
            return json.loads(text)
        except ValueError:
            continue
    return None


def _collapse_capture_samples(value):
    """把「多次抓包样本列表」收敛成**一次请求的 body**：`list` 且 `len > 1` 且元素
    **全部**是 dict → 返回 `value[0]`；其余原样返回。

    为什么判据是 `len > 1` 而不是"元素是 dict"：**单元素 dict 列表可以是合法的
    位置参数**。反例 `DataAggregation.queryCardInfo` 的入参就是
    `[{"stockCode": "920091", "cardId": "RISK_POINT_CARD"}]` —— 一个元素、dict、
    **完全正确**，收敛成 `{"stockCode": …}` 反而会破协议。而同一命令的多次抓包
    一定是 ≥2 个元素，所以用长度区分这两件事。

    这条收敛只对**抓包元数据**命令生效（`params_best_for` 的第 2/3 档内部），
    正常命令（第 1 档）一个字节都不碰。
    """
    if isinstance(value, list) and len(value) > 1 and all(
            isinstance(x, dict) for x in value):
        return value[0]
    return value


def params_best_for(cmd):
    """命令 → **最可信**的参数模板；连可信模板都没有返回 `None`。

    为什么需要它：`ctl_map.json` 里有 **25 条**命令的 `params` 不是业务参数，
    而是抓包记录
    `[{"ctl": 12, "args_hex": "7b22436f6465…", "pcap": "PCAPdroid_….pcap"}, …]`。
    照 `params_for()` 直接发出去，等于把 `args_hex` / `pcap` 这些**元数据当业务
    参数**送给服务端 —— 必然 -1002 或空包，`call_cmd()` 一上手就废。

    优先顺序（找到就返回，四档）：

    1. `ctl_map.json` 的 `params`，**若不像抓包元数据** → 用它（707 条走这条，
       取值与 `params_for()` 相同）；
    2. 否则 `catalog/verified.json` 里有该命令 → 用里面**已解码**的真参数（4 条）；
    3. 否则解码 `ctl_map` **自己的 `args_hex`**：取**第一条**能 `hex→utf-8→json.loads`
       成功的（8 条；只取一条，多份抓包是 N 次调用的样本，拼起来不是模板）；
    4. 都没有 / 解不出（protobuf 通道）→ `None`。

    **第 2/3 档取到的值还要再收敛一次**（见 `_collapse_capture_samples()`）：
    `verified.json` 的 `params` 是「**每次抓包一个元素**」的列表，直接整表发出去会
    变成 JSON 数组 `[{…},{…}]`，与任何一次真实请求都不等。所以：

    * `list` 且 `len > 1` 且元素**全是 dict** → 只返回**第一个**（那是一次请求的 body）。
      `HQServ.ZST`（2 元素）/ `CWSearch.tdx_pv`、`CWSearch.tzx_rcache`、
      `CWServ.tzx_filedown`（各 6 元素）都靠这条收敛成**对象**。
    * `len == 1` → **原样返回**。单元素 dict 列表是**合法的位置参数**，反例：
      `DataAggregation.queryCardInfo` → `[{"stockCode": "920091", "cardId": "RISK_POINT_CARD"}]`
      是对的，收敛成 `{…}` 会破协议（它走第 1 档，本来不经过这里，但判据必须写清）。

    ⚠️ **第 3 档返回的是「抓包样本参数」**：那是**那一次调用**的真实参数，
    可以当模板用，但**必须按业务改** —— 里面的股票代码 / 日期 / 页码都是抓包
    当时的现场值，**不是通用默认值**。别把它当"这条命令的固定入参"。

    「抓包元数据」判据：`params` 是 list 且任一元素是 dict 且含 `"args_hex"` 键
    （只认这一个键，见 `_is_capture_meta()`）。

    返回值永远是**新对象**（深拷贝）：调用方会拿它去 `fill()`，绝不能把
    `_load()` / `_verified()` 缓存里的字典泄露出去被就地改掉。

    降级（**更安全的口径**）：
    * `verified.json` 读不到（老安装包无边车文件）**不会**退回 `params_for()`
      —— 那等于把元数据当参数发出去；直接走第 3 档，解不出就 `None`。
    * 返回 `None` 的调用方语义是「**没有可信模板**」：此时应报错 / 跳过，
      而不是自己编参数或退回 `params_for()`。

    当前分布（25 条元数据命令）：第 2 档 4 条、第 3 档 8 条、第 4 档 13 条
    （后者全是 protobuf 体：`HQServ.PB*` / `FastHQ.Subscribe`）。

    ⚠️ 第 3 档解出来的**可能是整段请求体**而不是纯参数：例如
    `CWServ.ph_agf10_gzfx` 解出 `{"CallName": …, "Params": […]}`、
    `CWServ.ph_tdxdatacenter_cb_tjcb` 解出 `{"Params": […]}`。这是**好事** ——
    `session.body_of()` 对带 `CallName`/`Params` 键的 dict 走「**完整信封直通**」，
    会原样发出、不会再包一层。**不要**把信封拆开只留 `Params`：对 `call` 形态的
    命令，去掉 `CallName` 会让服务端**静默丢包**（不报错、只回空包）。
    所以本函数**不替调用方拆信封**，这也是它按原样返回抓包样本的原因。

    两个模板函数的差别：`params_for()` 管「**目录模板**」（原样），本函数管
    「**实际发出去的参数**」。护栏分工：本函数由
    `tests/test_placeholder_guard.py::test_real_send_path_has_no_placeholder_residue`
    守（真实发送路径 = 本函数 + `fill()`，残留必须 0），
    `tests/test_ctlmap.py` 另守四档分布与「多次抓包收敛」。
    """
    rec = _load().get(cmd)
    if rec is None:
        return None
    tpl = rec.get("params")
    if not _is_capture_meta(tpl):
        return copy.deepcopy(tpl)
    ev = _verified().get(cmd)
    if isinstance(ev, dict) and "params" in ev:
        return copy.deepcopy(_collapse_capture_samples(ev["params"]))
    decoded = _decode_capture_args(tpl)
    if decoded is not None:
        return copy.deepcopy(_collapse_capture_samples(decoded))
    return None


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


def fill(params, code=None, date=None, extra=None):
    """把参数模板里的占位符换成可用值（返回**新对象**，不改原模板）。

    取值优先级：`extra` > `code`/`date` > `PLACEHOLDER` 缺省表。

    `code`
        股票代码（`str`/`int` 都行，统一转 `str`），默认 `600900`。
        替换 `@code@` / `#code#`。

    `date`
        日期原样字符串，替换整个**日期族**（`DATE_PLACEHOLDER`，7 个键）：
        `#date#` `@date@` `#Date#` `#dateTime#` `#endDate#` `#today#` `#stockDay#`。
        **不给（None）就填空串** —— 库不猜日期格式：同一个「日期」，
        有的接口要 `20261030`，有的要 `2026-10-30 09:30:00`，猜错不报错、
        只回空包，所以格式由调用方决定。

    `extra`
        调用方覆盖表，`{占位符原文: 值}`。键要写成**占位符原文**（带 `#`/`@`），
        值统一转 `str`、`None` 当空串；这里给的值压过缺省表（含 `code`/`date`
        算出来的值）。没提到的键仍走缺省表。

            >>> tpl = ["00102", "1", "#name1#", "#name2#"]
            >>> fill(tpl, extra={"#name1#": "中国平安", "#name2#": "深圳"})
            ['00102', '1', '中国平安', '深圳']
            >>> fill(["003", "@hycode@", ""], extra={"@hycode@": "010301"})
            ['003', '010301', '']

    缺省表里 **``""`` 的那 13 个键必须由调用方提供**（走 `extra`）。填 `""`
    只保证「参数位不缺、帧能发出去」，**不保证有数据**；且空串不会被
    `client._check_empty_slots()` 拦住（它只拦 `[]`/`{}` 容器），错了也不会报错。
    逐条语义（格式照这里写，别自己编数值）：

    * `@name@` —— 名称/关键字，字符串原文（营业部名、游资名等），如 `"中国平安"`；
      用在 `CWServ.ph_tdxdatacenter_dzjy_yybxq` / `ph_zt_lhb_sy_yybxq` / `ph_zt_lhb_sy_yzxq`。
    * `#name1#` `#name2#` —— 同上，`CWServ.ph_zt_lhb_sy_xtyyb` 第 3、4 个参数是
      两个**并列**的名称检索项（同一实体的两段名称），要成对给。
    * `@hycode@` —— 行业代码（数字串，如 `"010301"`）；
      `CWServ.ph_tdxdatacenter_tdxrb_hypm_{ag,hk,usa}` 第 2 参数。
    * `@qsid@` —— 子查询 id（字符串）；`CWServ.ph_tdxdatacenter_zttz_xqy_v2_qsid` 第 4 参数。
    * `@jgid@` —— 机构 id（字符串）；`JJSServ.ph_datacenter_jjs_hsgt_v3_jgxq` 第 2 参数。
    * `@cstype@` —— 公司/财务类型代码（字符串）；`CWServ.ph_datacenter_mghg_cz` 第 2 参数。
    * `#entrycode#` —— 入口/栏目代码（字符串）；`CWServ.ph_tdxdatacenter_sq_dkb` 第 1 参数。
    * `#subTypeCode#` —— 板块（子类型）代码（字符串）；`HQServ.hq_nlp_xg` 的 `Code` 字段。
    * `#SelfStock#` —— 自选股编码串（整段由 App 侧拼好，格式照接口约定）；
      `CWServ.ph_tdxdatacenter_tdxrb_zxg` 第 2 参数。
    * `#flag#` —— 开关/标志位（字符串，按该接口定义给）；`CWServ.ph_tdxdatacenter_bgcz_gsbg` 第 3 参数。
    * `#sortNum#` —— 排序字段编号（字符串，是**字段序号**不是行数）；`CWServ.ph_zt_lhb_sy_yzycbd` 第 3 参数。
    * `#sortType#` —— 排序方向（字符串，按该接口定义给）；`CWServ.ph_zt_lhb_sy_yzycbd` 第 4 参数。

    不处理 `{{searchText}}`：那是 App 端 JS 的模板语法（2 条 `CWServ.app_keyboard_*`），
    不是帧内占位符，原样保留（守卫测试按逐条点名白名单放行）。
    """
    sub = dict(PLACEHOLDER)
    if code is not None:
        sub["@code@"] = sub["#code#"] = str(code)
    date_val = "" if date is None else str(date)
    for k in DATE_PLACEHOLDER:
        sub[k] = date_val
    if extra:
        for k, v in extra.items():
            sub[k] = "" if v is None else str(v)

    # 长的键先换：避免短 token 抢先吃掉长 token 的一段（如 `#code#` / `#setcode#`）。
    pairs = sorted(sub.items(), key=lambda kv: len(kv[0]), reverse=True)

    def one(x):
        if isinstance(x, str):
            out = x
            for k, v in pairs:
                if k in out:
                    out = out.replace(k, v)
            return out
        if isinstance(x, list):
            return [one(i) for i in x]
        if isinstance(x, dict):
            return {k: one(v) for k, v in x.items()}
        return x

    return one(params)


def stats():
    """表的基本统计：总数 / 有参数 / 已验证 / 各形态条数 + 三档证据计数。

    三个「verified」相关数**口径不同，别混**：

    * `"verified"` —— `ctl_map.json` 里的**布尔标记** `verified: true`（537 条）。
      历史口径：当时确认 ctl 打对了并且实测取到过数据。
    * `"verified_rows"` —— `verified.json` 的条数（**421** 条）：带 `rows`/`cols`
      实际数值的「已验证有数据」证据，比布尔标记严（要能指到行数）。
    * `"dead"` —— `dead_cmds.json` 的条数（**184** 条）：实机确认本版本 App
      未实现（回 104 通讯错误），且没有 `rows>0` 的正向证据。
      （2026-11-01 真机复验：7 条原 dead 的产品命令实测有数据 → 移入
      `verified.json`、同时从本表删除，所以是 184 而不是旧的 191。）

    条数对不上时不要「凑」：差一条就该去查生成脚本，而不是改断言。
    """
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
        "verified_rows": len(_verified()),
        "dead": len(_dead()),
        "form_measured": len(_form_measured()),
    }


def tier_for(cmd):
    """命令 → 三档状态：`"verified"` / `"probe_ok"` / `"dead"`，都没证据返回 `None`。

    判定顺序是**硬约定**（正向证据优先）：

    1. `"dead"`     —— 在 `dead_cmds.json` 里（184 条：本版本 App 未实现 + 回 104）；
    2. `"verified"` —— 在 `verified.json` 里（421 条：实测 `rows>0`，有具体行数证据）；
    3. `"probe_ok"` —— `ctl_map.json` 的 `probe_ok` 为真，且上面两档都没占（44 条）；
    4. `None`       —— 其余（含不在 732 条表里的命令）。**不造第四档。**

    为什么 `dead` 排在 `verified` 前、但两者在数据层已经互斥：两份素材原有 47 条
    冲突（`rows>0` 却在 `not_in_app` 名单里），按**正向证据优先**全部归 `verified`，
    并从 `dead_cmds.json` 里剔掉 —— 否则会拦掉 46 个真能出数据的产品命令。
    所以这里 `dead` 命中即 `dead`，不会把能出数据的命令判死。

    ⚠️ 负证据会**过期**：2026-11-01 真机复验发现再 7 条原 dead 的产品命令
    （`CWServ.ph_jjzt_zcjj` 4310 行、`CWServ.ph_zt_xg_xgjx`、`DataAggregation.queryRandom`、
    `JJSServ.ph_datacenter_jjs_hsgt_v3_jgxq`、`JJSServ.ph_datacenter_jjs_hsgtzt_v3_bsjj_jg`、
    `JJSServ.ph_zt_bxzj_sy_bkbd`、`JJSServ.ph_zt_bxzj_sy_jgbd`）实测都能出数据，
    已移入 `verified.json` 并从 `dead_cmds.json` 删除（191 → 184）。
    判死前**先真机跑一次**，别只信历史名单。

    📍 名单从哪来（**排障只看包内生成物**）：死名单的素材是**仓库外**的
    `F:/TDX/raw/catalog/not_in_app.json`（2026-10-31 实机快照，**未随包分发**，
    包里 `catalog/` 下没有这个文件）；随包的是**生成物**
    `tdxdata/_engine/catalog/dead_cmds.json`（184 条 = 该快照 238 条减去本地实测
    `rows>0` 的命令）。同理 `verified.json` 的素材 `verified_working.json` 也在仓库外。
    所以：**`dead_cmds.json` / `verified.json` 才是包内可查的真相源**，
    别按 `not_in_app.json` 去包内找。
    """
    if not isinstance(cmd, str) or not cmd:
        return None
    if cmd in _dead():
        return "dead"
    if cmd in _verified():
        return "verified"
    rec = _load().get(cmd)
    if rec is not None and rec.get("probe_ok"):
        return "probe_ok"
    return None
