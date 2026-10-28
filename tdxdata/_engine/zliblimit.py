# -*- coding: utf-8 -*-
"""zlib 解压的**唯一**硬上限实现（DEF-B4-02 / SEC-2）。

为什么单独一个模块
------------------
评审发现"解压上限"这件事有**四条**各写各的通路，全部直接 `zlib.decompress(...)`
或 `decompressobj().decompress(...)`（后者不限长 = 不限量）：

| 通路 | 位置 | 谁能触发 |
|---|---|---|
| PB 回包解码 | `pb_api.decode_any()` | 每条 `call_pb` 回包 |
| 帧拆包 | `datacenter.unwrap_frames()` | 帧头声明 `0x3B`（zlib）时 |
| 包体切分 | `datacenter._try_zlib()` | 按 `0x78` 魔数扫包体时 |
| 探测链 | `hq_extra` 的 `Length`/`AttachLog` 剥离 | 每条带 `Length` 头的回包 |

四条通路上只要有一条还留 `zlib.decompress(blob)`，**炸弹就从那条路进来**
（一个 800 KB 的包能解出 80 MiB，实测压缩比 >100:1）。所以上限必须只有一份实现，
新增通路时也只许调这里 —— 否则下一个人加通路又会漏一条。

语义（三档，必须分开）
----------------------
* **超限** → `errors.ProtocolError`。硬错误：**绝不降级**、绝不截断后当正常回包
  （截断的 JSON 会在下游变成"列错位/行缺失"这类最贵的静默错）。
* **不是 zlib / 流被截断** → `zlib.error`。这是"编码判断失败"，交调用方继续探测
  （原有降级语义一个字节都不改）。
* `limit=None` → 现读模块级 `MAX_DECOMPRESS`（**每次调用现读**，不写成默认参数值，
  这样测试才能 monkeypatch 到小值）。
"""
from __future__ import annotations

import zlib

from .. import errors

__all__ = ["MAX_DECOMPRESS", "inflate_limited"]

#: 单次解压的硬上限。依据不是拍脑袋：真实帧解压后是**几十 KB 量级**
#: （实测最大 `PBZSTDDE` JSON ≈ 40 KB、`PBFXT` 12×120 protobuf ≈ 12 KB，
#: 整目录无一超 200 KB），64 MiB 相对实测最大值有 **3 个数量级**余量。
#: 红线：调用方的内存占用**不许**由对端回包决定。
MAX_DECOMPRESS = 64 * 1024 * 1024


def inflate_limited(blob: bytes, limit: int | None = None, context: str = "") -> bytes:
    """带上限的 zlib 解压（见模块文档的三档语义）。

    `context` 只进**报错文本**（哪个命令 / 哪条通路的回包出的问题），不参与判断。
    实现要点：`decompress(data, max_length)` 只多要 1 字节 —— 够判「有没有超」，
    又**不会真把炸弹解开**（全程内存 ≈ 上限 + 1，而不是上限的 100 倍）。
    """
    cap = MAX_DECOMPRESS if limit is None else int(limit)
    ctx = ("%s " % context) if context else ""
    d = zlib.decompressobj()
    out = d.decompress(blob, cap + 1)
    if len(out) > cap:
        raise errors.ProtocolError(
            "%szlib 回包解压超过硬上限：已解出 %d 字节即停止 > 上限 %d 字节"
            "（压缩后只有 %d 字节，压缩比 ≥ %.0f:1）—— 已中止解压，"
            "**绝不返回被截断的假数据**。上限是 _engine/zliblimit.MAX_DECOMPRESS"
            "（真实帧解压后仅几十 KB，64 MiB 已有 3 个数量级余量）；"
            "确有大包需求请显式抬高该常量并核对协议"
            % (ctx, cap + 1, cap, len(blob), float(cap + 1) / max(1, len(blob))))
    if not d.eof:
        # 输入喂完了却没到流结束 = 流被截断（含「首字节 0x78 实则不是 zlib」）。
        raise zlib.error("incomplete or truncated zlib stream"
                         "（输入 %d 字节，已解出 %d 字节）" % (len(blob), len(out)))
    return out
