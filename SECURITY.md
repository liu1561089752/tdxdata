# 安全说明（tdxdata）

> 适用对象：把 tdxdata 放进生产/内网/批量采集链路的人。
> 用法问题请先看 [`README.md`](README.md)；本文只谈**信任边界与已知风险**。
>
> ⚠️ 本文记录的是**当前代码**的属性，不是愿望清单。每条风险的「现状」都指到具体
> 文件/符号，末尾 §6 给了对账命令 —— **改完代码请重跑一遍**，别让这份文档比代码乐观。
> 凡这里写"未实现"的能力，一律不要假设它存在。
>
> 行号说明：`client.py` 正在被并行开发，**行号会漂移**，所以那一类引用一律写成
> `文件::符号`（如 `client.py::_peer_reset`）；`_engine/*`、`pacer.py`、`handshake.py`
> 的引用带行号，核对时请以符号为准、行号为辅。

---

## 0. 一句话结论

本库只承诺「**字段与协议一致**」，**不承诺「数据是真值」**。

它是一条本机主动出站、走 7615 **明文 TCP**、对**硬编码 IP** 说话、回包**没有签名/没有完整性校验/没有 TLS**
的行情与数据中心客户端。因此：**同一条链路上的中间人可以改写你拿到的行情、财务与统计数据，而本库无法察觉。**

如果你的下游要做风控、对账、合规披露或"数据可信"类结论，真值必须由**库外**可信源交叉验证（见 §4.3）。

---

## 1. 信任模型

### 1.1 通信面：出站、明文、单端口

| 事实 | 证据 |
|---|---|
| 只**出站**，不监听任何端口（无 server socket / 无入站回调） | `socket.create_connection` 全库 3 处，全是出站：`tdxdata/_engine/session.py:191`（首连）、`session.py:397`（重连）、`tdxdata/_engine/datacenter.py:418`（PB 持久连接） |
| 唯一端口 **7615**，**明文 TCP**（不是 7709/7727，也没有任何加密层） | `tdxdata/_engine/session.py:29` `PORT = 7615`；`datacenter.py:418` 直接 `(host, 7615)` |
| 全库**没有** TLS / 证书 / 签名 / 摘要校验 | `git grep -n -e "import ssl" -e hmac -e hashlib -- tdxdata/` → **0 命中** |
| 无第三方依赖（纯标准库），不引入新的信任面 | `pyproject.toml` `dependencies = []` |

协议帧是自定义二进制 + JSON/zlib/protobuf 混装（`datacenter.py` 模块 docstring 有逐字节说明），
**没有任何字段用来认证服务端**。

### 1.2 对端身份：硬编码 IP + 固定握手字节

- 服务器地址**写死在源码里**，两处各一份、内容同一批：

  | 位置 | 内容 |
  |---|---|
  | `tdxdata/_engine/datacenter.py:55` `SERVERS` | `121.5.164.43` / `124.223.177.105` / `121.37.180.65` / `60.204.149.166` |
  | `tdxdata/_engine/session.py:30` `HOSTS` | 同上 4 台（顺序不同） |

- 建连握手是一段**固定字节模板**（`tdxdata/_engine/handshake.py:23-35` 的 `_B64`，解码后 614 字节）：
  同一份字节、对每台服务器、每次建连都一样。详见风险 **SEC-1**。
- 没有任何证书固定（pinning）、密钥协商、会话密钥 —— 也就**没有可泄露的密钥**，同样**没有可验证的身份**。

### 1.3 敌手模型：同链路中间人

假设敌手能读改这条 TCP 链路（同 Wi-Fi、同交换机、上游 ISP、被劫持的 DNS/路由、你误配的代理、
一台被人接管的"内网镜像"），则它能：

- **读**：你请求了哪些证券代码/产品（明文 JSON 参数）、拿到了什么财务与行情数据；
- **改写**：回包任意字节 —— K 线 OHLCV、分时、逐笔、快照、财务三表、涨跌停与两融统计、板块成分……
  本库**没有任何手段发现**这一点（无签名、无校验和、无交叉比对、无 TLS）；
- **重放/裁剪**：把昨天的回包当今天给你（时效层只能看"服务器时刻探针"，而探针本身也在同一条链路上，
  同样可被伪造）；
- **制造资源压力**：例如塞一段高压缩比回包（**SEC-2**）。

库**能**看见的只有**内部一致性**：列名/列数/行宽是否自洽（`sections/mapping.py`）、响应形态是否像
`ResultSets`（`_engine/session.py::parse` 的 7 种 verdict）、类型/单位是否归一、以及**用户显式调用**
的恒等式闸门 `tdxdata.invariants.validate(...)`。精心构造的假数据可以同时满足以上全部。
（恒等式默认**不**在取数管道里自动跑 —— 见 `invariants.py` 模块头的取舍说明。）

### 1.4 本库保证什么、不保证什么

| 保证 | 不保证 |
|---|---|
| **字段与协议一致**：列名按 `sections/mapping.py::FIELD_MAP` 还原，行宽与 `cols` 对齐，`raw` 原样保留，未映射的 section 只给 `raw`（`sections/f10.py`、`sections/finance.py` 的返回契约） | **数值是真值**：库不知道也不判断服务端是否在骗你 |
| **失败语义可区分**：连不上 ≠ 没数据（`errors.ConnectionFailed` / `ProtocolError` vs `EmptyResult`；见 `_engine/pb_api.py` DEF-043 注释） | **对端是官方服务器**：库只连 `SERVERS`/`HOSTS` 里的 IP，不验证它背后是谁 |
| **时效被标注而非冒充**：拿不到服务器时刻就降级为 `LOCAL` 并写 note（`client.py` 模块 docstring） | **时效判断不可伪造**：探针与数据走同一条无认证链路 |
| **不做隐式多发、不隐式落盘**（缓存是可选注入、纯内存；日志默认零输出） | **不保证服务端可用性/合法性**：那 4 台服务器不由本项目运营 |

---

## 2. 已知风险清单

> 格式：**现状 → 影响 → 缓解**。「状态」列写的是代码现状，不是计划；与 §6 自查结果冲突时**以代码为准**。

| ID | 风险 | 状态 |
|---|---|---|
| SEC-1 | 握手模板内嵌真机指纹 / 内网 IP / ACL 票据 | 未修（已登记 ENG-009，只到文档化） |
| SEC-2 | 解压无上限 = zip bomb（DEF-B4-02） | **已修**：四条通路统一到 `_engine/zliblimit.py`（64 MiB 硬上限，超限抛 `ProtocolError` 不降级） |
| SEC-3 | 进程级节流 / 限流退避可能误判（间隔顶到 12s） | 设计如此，有已知误判面 |
| SEC-4 | `Client(host=...)` 语义：进程级偏好 + JSON 通路仍会轮换 | 已修 DEF-048，但语义易被误读 |
| SEC-5 | 候选主机列表 `hosts=[...]` **尚不存在** | 未实现（roadmap） |

---

### SEC-1 握手模板内嵌真机指纹 / 内网 IP / ACL 票据

**位置**：`tdxdata/_engine/handshake.py:23-35`（`_B64` → `HANDSHAKE`，614 字节，来源为 2026-10-20 真机抓包）。

**现状**：这段字节是"能直接连上服务端"的完整握手（CONNECT + LOGIN + ACL 三帧）。解码后明文可读到：

- 设备/客户端指纹：`GPhone_Standard`、`Redmi_22122RK93C_34_14`（型号 + 构建串）、版本串 `7.27;2026-08-06 15:15 2.02.76`；
- 原抓包机的**内网地址** `192.168.0.104` 与**公网出口** `117.147.45.243`；
- ACL / 票据类字段：`ACL:checkuser`、`SPEC=2834,STRUCT=ACL:checkuser`、`KCHnq0Ryon475MRE`、
  `@ec0:woiVse…`（base64 段）、`f4f652dbff48aa10`、`9f064decd66aeb1b1902eb46_2011819129871074975`（疑似 OID/会话票据）。

它在 **`.py` 源码里**，所以随 sdist/wheel 一起分发 —— 任何人 `pip download tdxdata` 即可提取。

**影响**：

1. **信息泄露**：抓包者的内网 IP、出口 IP、设备标识进入公开仓库（即使将来删掉，也删不掉 git 历史）。
2. **身份混同**：若服务端按模板里的票据/指纹认人，则**所有使用者共享同一个"设备身份"** ——
   任何一人的滥用都可能牵连全部使用者，封禁会打到别人头上。
3. **可直接复用**：这段字节等于一份"合法握手样本"，任何第三方拿去就能冒充。
4. 这条模板同时说明：**本库没有"你的身份"这个概念**，也没有轮换/失效机制。

**处置现状（未修）**：没有任何外置、生成或轮换机制，也没有开关。代码侧只把它登记为待办 ——
`tdxdata/_engine/session.py:198-205` 的注释明确写着"只做「非空」这一最小校验，不动模板内容
（指纹/票据外置是 ENG-009 的范围）"。本文件（ENG-009 的文档部分）**只登记、不改变行为**。

**缓解（今天就能做）**：

- 不要把它当凭据，也不要因为包里带着它，就认为本库对外有"可追责身份"；
- 内网/合规场景请在**网络层**做来源标识（固定出口 NAT + 出站白名单 + 边界日志），别依赖模板指纹；
- 报告问题、提 issue、贴日志时**不要贴这段 base64**（它本身就是材料）；
- 若你要 fork 或二次分发，建议先用自己的抓包模板替换 `_B64`，并清理旧指纹。

---

### SEC-2 解压上限（zip bomb，DEF-B4-02）—— **四条通路已统一到一处实现**

**现状（2026-10-28 复核；四条通路全部有界，实现只有一份）**：

| 位置 | 调用 | 现状 |
|---|---|---|
| `tdxdata/_engine/zliblimit.py` | `inflate_limited(blob, limit=None, context="")` | ✅ **唯一实现**：`decompressobj().decompress(data, cap+1)` 只多解 1 字节判超限（全程内存 ≈ 上限+1，不真解炸弹）；超限抛 `errors.ProtocolError`；"不是 zlib / 流截断"抛 `zlib.error` 交调用方继续探测 |
| `tdxdata/_engine/pb_api.py` `decode_any()` | 经 `_inflate_limited()` → `zliblimit` | ✅ 有界，且**显式禁止**落进"形似 zlib 实则不是"的降级分支（否则炸弹又变回静默） |
| `tdxdata/_engine/datacenter.py` `unwrap_frames()` | 帧头 `0x3B` 声明 zlib | ✅ 有界；超限**上抛**、不把原始字节交给下游 |
| `tdxdata/_engine/datacenter.py` `_try_zlib()` | `0x78` 魔数扫包体 | ✅ 有界；"解不出"仍返回 `None`（探测语义不变），超限上抛 |
| `tdxdata/_engine/hq_extra.py` `peel()` | `Length`/`AttachLog` 探测链剥 zlib 层 | ✅ 有界；"形似 zlib 实则不是"仍静默继续探测 |

`MAX_DECOMPRESS = 64 MiB` 的依据：真实帧解压后是**几十 KB 量级**（最大 `PBZSTDDE` JSON ≈ 40 KB、
`PBFXT` 12×120 protobuf ≈ 12 KB，整目录无一超 200 KB）→ **3 个数量级**余量。
红线：**调用方的内存占用不许由对端回包决定**。

注意两件事，别把"有界"读成"无害"：

1. 64 MiB 是"**有界**"不是"**无害**"：单次调用仍可让进程吃掉 64 MiB 量级内存；多命令并发的批任务会叠加。
2. 这四条通路**都可达**（`_send()` 每条 PB 回包都走 `unwrap()`/`_try_zlib`，解不出时兜底 `unwrap_frames()`；
   `hq_extra.peel` 是独立入口）—— 所以"只修主入口"是**不够**的，这也是本条目从"部分已修"改成"已统一"的原因。

**判据**：`tests/test_zlib_limits.py`（17 条：统一实现 / 帧拆包 / 魔数扫包 / 探测链各点一次火 +
一条**静态扫描**断言"全包除 `zliblimit.py` 外不许再出现 `zlib.decompress` / `decompressobj()"），
`tests/test_pb_api_limits.py`（10 条，PB 主入口）。

历史参照：`tests/test_pb_api_offline_b4.py::test_decode_any_zip_bomb_materializes_fully`（L239-248）是当年
"固定现状"的揭示性测试，用的是 16 MB（**低于** 64 MiB 上限），所以它**现在仍然通过** —— 它不能证明上限生效，
真正的判据是上面两个文件。

**影响**：链路上的中间人（或一台被写错/被接管的镜像）只要回一段高压缩比数据，就能让进程内存暴涨
（压缩比可轻松超过 100:1）→ OOM / 长时间卡死 / 被 OOM killer 连带杀掉同容器其它工作。
攻击者**不需要**攻破服务器，也不需要你的任何凭据。上限生效后放大倍数封顶在 64 MiB/次。

**缓解**：

- 只连可信 host（§4.1），不要在不可信网络上跑采集；
- 给采集进程加**资源硬上限**（容器 memory limit / cgroup / `resource.RLIMIT_AS`），让炸弹最多杀掉采集进程；
- 自己写解析链时，解压**只许**调 `zliblimit.inflate_limited()`；不要用 `zlib.decompress(blob)`，
  也不要用 `except Exception` 把超限一起吞掉（那就等于没加）；
- 新增取数通路时**不要**自己解压 —— 走 `zliblimit`，`tests/test_zlib_limits.py` 的静态扫描会拦住你。

---

### SEC-3 进程级节流与限流退避（触发后上限 12s，判据可能被误判）

**现状**：限流防护是**进程级单例** `Pacer`（`tdxdata/_engine/pacer.py`），同进程所有 `Client` 共享同一份状态：

| 参数 | 值 | 位置 |
|---|---|---|
| 基准间隔 | `BASE_GAP = 2.2` 秒 | `pacer.py:46` |
| 退避上限 | `MAX_GAP = 12.0` 秒（每次被判定限流**翻倍**） | `pacer.py:47,90-99` |
| 回落条件 | 连续成功 `DECAY_AFTER = 8` 次后 ×0.75，不会一直卡在 12s | `pacer.py:48,82-88` |
| 服务器冷却 | 被判"踢过"的 host 冷却 `COOLDOWN = 30` 秒后不再连 | `pacer.py:49,102-109` |

"被判限流"的**判据是连接被重置/中止**：`client.py::_peer_reset()`（WinError 10053/10054、
`ECONNRESET`/`ECONNABORTED`/`EPIPE`、`errors.RateLimited`）；建连期同样按 reset/abort 判（`session.py:212-220`）。

**影响（两个方向都成立）**：

1. **误判成限流**：中间盒 / 防火墙 / NAT 空闲超时把 TCP 重置，信号与服务端踢人**完全一样**
   → 进程级间隔被顶到 12s，同进程**所有** Client 一起变慢；按注释的实测口径，回落需要约 **48 次成功调用（≈9.6 分钟）**。
   表现是"库突然很慢"，**不抛异常、不打印、不提示**。
2. **漏判**：真限流如果表现为超时/空包而不是 reset，就不退避，继续撞墙。
3. 这不威胁机密性，但它会与"数据看起来旧/少/缺"混在一起，**误导结论**（把限流当"今天没数据"）。

**缓解**：

- 用 `Client.rate_stats()` 看 `current` / `limited_events` / `cooling_hosts`（`client.py::Client.rate_stats`）判断是不是被压频；
- **一个进程跑一个采集任务**：`Pacer` 跨 Client 共享，但**不跨进程**；多开进程 = 多份配额互相挤；
- 批量任务里不要把 `Client` 建在循环内部；真吃到限流就降频/隔日再取，**别加线程**（并发特性已按实测删除）。

---

### SEC-4 `Client(host=...)`：不是"钉死"，PB 侧还是进程级偏好

**现状（两条通路语义不同，别当成一个）**：

| 通路 | 受影响接口 | `host=` 的真实语义 |
|---|---|---|
| 数据中心 JSON | `get` / `get_f10` / `get_finance` / `get_boards` / `call` … | **起始主机**。`client.py::Client._ensure()` 用 `Session(self._host, timeout)` 建连，而 `Session` 默认 `pin=False`：建连失败或被踢会按 `HOSTS` **继续轮换**（`session.py:180-188`）。`Client` **不暴露** `pin`。 |
| PB（protobuf） | `get_bars` / `get_minutes` / `get_quotes` / `get_ticks` / `get_breadth` / 分页 … | **进程级偏好**。`Client.__init__` 里调 `datacenter.set_preferred_host()`，状态是模块级全局 `_PREFERRED_HOST`（`datacenter.py:344-369`）。同进程**后建**的 `Client(host=…)` 覆盖前一个；`host=None` **不清除**已有偏好，要显式 `datacenter.set_preferred_host(None)`。 |

历史：修 DEF-048 之前 PB 通路**完全无视** `host=`，静默连内置 `SERVERS`（代码里"想钉住镜像/代理/抓包目标的人
会拿到别处的数据"那段注释即指此）。

**影响**：你以为钉住了内网镜像/代理/抓包目标，实际 **JSON 通路可能已经轮换到公网 IP**；
同一进程里两个 `Client` 的 PB 主机偏好会互相串味（实验/审计记录里"这台机器取的数据"未必是那台）。
这本身不是漏洞，但会**直接毁掉**"我确实连的是内网镜像"这个安全前提。

**缓解**：

- 每次都核验实际对端：`c.last_call["source"][0]`（`client.py::Client.last_call`，含 `(host, cmd, ctl)`）、`result.provenance.host`（`_provenance.py`）；
- 需要**强**钉住时在**网络层**做：出站 ACL 只放行镜像 IP / 透明代理 / 本地 hosts 映射，别只靠 `host=`；
- 单进程单 `Client`，别指望"两个实例各自钉各自的主机"。

---

### SEC-5 候选主机列表 `hosts=[...]` **尚不存在**（roadmap）

**现状**：`Client.__init__` 只接受 `host: str | None`（见 `client.py::Client.__init__` 签名，§6 第 4 条可直接打印）；服务器集合固定为
`datacenter.SERVERS`（4 个硬编码 IP）与 `session.HOSTS`（同一批）。全仓 `git grep "hosts="` **0 命中** ——
**没有** `hosts=[...]` 参数、没有健康度/顺序/冷却语义，也没有对应的配置项。

**影响**：今天无法"声明一份可信服务器清单（内网镜像 / 代理池）"；内网部署只能靠 NAT/代理 + `host=`，
而后者带着 SEC-4 的轮换隐患。反过来，任何**声称**支持 `hosts=` 的文档/示例都是错的。

**计划（roadmap，未实现）**：候选列表 `hosts=[...]`（顺序、健康度、冷却、与 `host=` 的优先级）。
在它落地前，请**不要**在文档、示例或你的代码里假设它存在。

---

## 3. 漏洞报告渠道

- **一般问题 / 加固建议**：开 GitHub issue —— <https://github.com/liu1561089752/tdxdata/issues>。
  请附：`tdxdata.__version__`、Python 版本、平台、最小复现（能离线的请附离线桩，本仓库测试全部离线）。
- **敏感问题**（内网地址、票据字节、真实抓包、可被滥用的载荷、具体中间人手法）：
  **不要开公开 issue**。请走仓库 owner（GitHub `@liu1561089752`，按其 GitHub 主页所列联系方式私信）。
  若确实联系不上，开 issue 时**只写影响面与模块位置**，不要把可利用细节贴出去。
- **实际情况如实说明**：本文写作时仓库**没有**专用安全邮箱、**没有** SECURITY 响应流程与 SLA、**没有** CVE 编号流程。
  请按"单人维护的开源项目"预期它，别假设有 24 小时响应。
- 已登记项（SEC-1 / SEC-2 / SEC-3 / SEC-4）**不要求**你先读完再报；但请注明是"新增证据"还是"复述"。
  漏洞报告请遵守基本善意披露：**先私下、再公开**，公开前留出修复时间。

---

## 4. 离线 / 内网部署建议

### 4.1 钉住单一 host（在网络层，不在应用层）

出站白名单只放行你镜像的 `host:7615`，其余拒绝；然后用
`c.last_call["source"][0]` / `result.provenance.host` **逐次核验**实际对端（SEC-4 的 `host=` 不足以保证）。
一个进程只跑一个采集任务。

### 4.2 只读镜像 + 可审计

镜像只做"回放/转发"，不参与写入与清洗；镜像自身要可审计（版本、采集时间、回放的是哪一批数据）。
镜像被接管时的最坏情况就是 SEC-1..SEC-2 —— 所以镜像账号不要复用生产权限。

### 4.3 不要在校验链路上信任本库输出

这是本文最重要的一条：**本库只保证"字段与协议一致"，不保证"数据是真值"。**

- 对账 / 风控 / 合规披露 / 影响决策的回测结论，**必须**由库外可信源（交易所、权威数据商、你自建的可信基线）交叉验证；
- `result.provenance.status` / `as_of` 只说明"这个口径定稿了 / 这是哪个时刻"，**不是真值证明**；
- `tdxdata.invariants.validate(...)` 只能发现"数据自己跟自己打架"，防不住精心构造的假数据（§1.3）。

### 4.4 明文链路的一切都是可见的

7615 无 TLS：请求里的证券代码与回包里的财务数据，对同链路上的任何设备都可见。
不要在**同一条链路**上携带别的敏感凭据；抓包/排障请用隔离网段的一次性环境，抓包文件按敏感数据保管。

### 4.5 资源隔离

给采集进程设内存/CPU 上限与超时：既防 SEC-2 的炸弹，也防 SEC-3 的 12s 退避把批任务拖成"卡死"。

### 4.6 供应链

固定版本 + 校验哈希，只从官方渠道安装；不要用来源不明的"改过的 tdxdata" ——
它会在你的机器上发起**带内嵌握手模板**的出站连接（SEC-1），等于把可信网络边界借给别人。

### 4.7 观测

- 限流：`Client.rate_stats()`；
- 对端与耗时：`Client.last_call`、容器上的 `provenance`；
- 日志：库 logger **默认零输出**（`NullHandler`，见 `_logging.py`），要看得显式
  `logging.getLogger("tdxdata").setLevel(logging.DEBUG)`；现有发射点只带异常类型名/属性名与计数，
  未见回包正文（升级后请按 `grep logger.debug` 复核，别把这条当永久承诺）。

---

## 5. 明确"没有"的能力（不要假设）

- ❌ TLS / 任何加密；❌ 回包签名 / 校验和 / 来源证明；❌ 服务端证书或公钥固定。
- ❌ 候选主机列表 `hosts=[...]`（SEC-5）；❌ 握手模板外置 / 轮换（SEC-1）。
- ⚠️ 解压上限**已覆盖全部四条通路**（`_engine/zliblimit.py` 单一实现，64 MiB），但"有界 ≠ 无害"：
  单次仍可吃 64 MiB 量级内存（SEC-2）。
- ❌ 业务真值校验（§1.4）；❌ 认证/鉴权（本库不做用户身份，也没有凭据管理）。
- ❌ 入站服务 / 代理服务；❌ 隐式落盘缓存（缓存是可选注入的**内存** `TTLCache`，默认关闭）。

---

## 6. 自查（改代码后请重跑这几条，让文档与代码一起更新）

```powershell
# 1) 是否仍然"无 TLS / 无完整性校验"（期望：0 命中）
git grep -n "import ssl\|hmac\|hashlib" -- tdxdata/

# 2) SEC-2：解压上限是否只在 zliblimit 一处实现（DEF-B4-02）
git grep -n "zlib.decompress\|decompressobj" -- tdxdata/     # 期望：只命中 _engine/zliblimit.py
python -m pytest tests/test_zlib_limits.py tests/test_pb_api_limits.py -q
#    前者含一条静态扫描：全包除 zliblimit.py 外出现 zlib.decompress 就直接红
#    （tests/test_pb_api_offline_b4.py 那条 16MB 揭示测试**低于** 64MiB 上限，通过不证明什么）

# 3) SEC-1：握手模板是否仍是内嵌真机字节
git grep -n "_B64" -- tdxdata/_engine/handshake.py
python -m tdxdata._engine.handshake                     # 打印字节数（现状 614）与前 16B

# 4) SEC-4 / SEC-5：host= 的实际签名（有无 hosts=）
python -c "import inspect, tdxdata; print(inspect.signature(tdxdata.Client.__init__))"

# 5) SEC-3：节流参数
git grep -n "BASE_GAP\|MAX_GAP\|DECAY_AFTER\|COOLDOWN" -- tdxdata/_engine/pacer.py
```

> 本文件只做登记与建议，**不改变任何运行时行为**。相关实现待办见各条"现状"里指出的代码注释。
