# ADR-0006：protocol v1 消息模型与二进制 framing

- 状态：Accepted（t3/t5 独立复审确认：黄金帧跨轮逐字节不变、round-2 探针 48/48、门禁 1517 passed 全绿；2026-09-05 自动化轮合并时生效）
- 日期：2026-09-05

## 背景

`docs/TRANSPORT_PROTOCOL.md` §11 给出候选 framing（固定大端前缀 + UTF-8 JSON header + binary payload）但声明「最终编码必须在实现前用 ADR 和契约样本冻结」「在 ADR 接受前不得让临时 framing 成为事实标准」。M07 决定重新实现协议：不迁移旧 UAV-GPR 的 `RemoteSender`/`Receiver` 与 NPZ payload（ADR-0005、AGENTS.md §2.2）。HM30 是普通 IP 承载，TCP 字节流存在任意粘包/拆包/截断；地面端必须防内存分配攻击（§9 严格上限、解析前先验证），且 trace 数据只能承载一次规范原始频域数组——频率轴与通道定义在任务配置中冻结，逐道一致性以 ISSUE-009 `raw_trace_sha256` 为准（AGENTS.md §4），禁止重复发送 display/time 派生数据（§2 非目标）。

## 备选方案

1. **NPZ / pickle 直发**（旧 UAV-GPR 做法）：拒绝。pickle 是不可信反序列化执行面，NPZ 绑定 numpy 存档格式、无版本协商、无法在不完整解压前验长度；违反 TRANSPORT_PROTOCOL §2 与 adr/README「不依赖 Python pickle」。
2. **纯 JSON（含 base64 raw）**：拒绝。complex128 数组 base64 膨胀 ~33% 且每次编解码有 CPU 放大；无法表达"读 payload 前验上限"以外的强类型边界；破坏「payload 只承载规范 raw 字节」的零转换语义。
3. **protobuf/CBOR 等第三方 codec**：拒绝。新增主依赖违背 pyproject 最小依赖策略（stdlib+numpy+Qt/H5 分层），且 schema 演进仍要自定义版本协商，收益不足。
4. **固定大端前缀 + canonical UTF-8 JSON header + binary payload**（采纳）：前缀承担 O(1) 快速分帧与上限防御；header 用 JSON 复用既有 core 模型 `to_dict()`（TraceMetadata/MissionConfig/GnssMatch 权威形态，杜绝平行类型）；payload 保留裸字节给 trace 热路径。

## 决策

冻结 protocol v1 帧格式与消息族如下（本 ADR 为唯一权威口径，改动需新 ADR 标明替代关系）：

### 帧布局（全部大端网络序，固定前缀 18 字节）

```text
[0:4)   magic   = 55 41 56 50 ("UAVP")
[4]     major   u8（当前 1；≠1 拒绝连接语义）
[5]     minor   u8（能力协商位；接收侧按 CapabilityPolicy 处理）
[6:8)   type    u16be（MessageType 稳定码，未知即拒）
[8:10)  flags   u16be（v1 必须为 0；非 0 ⇒ FLAGS_NONZERO fail-closed）
[10:14) header_length  u32be ≤ MAX_HEADER_BYTES = 1 MiB
[14:18) payload_length u32be ≤ MAX_PAYLOAD_BYTES = 64 MiB
[18:18+H)   header  = UTF-8 canonical JSON（sort_keys、separators=(",",":")、ensure_ascii、拒绝非有限 float）
[18+H:…)    payload = binary（trace_record 为规范 raw C-order complex128 小端数组字节；其余 7 类必须为空）
```

- 上限在**读取 payload 之前**验证；增量 parser 只在已验证长度内累积缓冲，损坏即 poisoned（要求显式 `reset()`），不在错位流上猜帧。
- 帧头与 header 双向冗余校验：header 内 `spec_version/major/minor/type_name/flags` 必须与 binary 前缀一致，防拼接错帧。
- 域分离：framing magic `"UAVP"` ≠ raw-hash 域 `"UAVGPR-RAW-SHA256"` ≠ config digest 域；任何哈希不复用另一域的字节前缀。

### 消息族（type code 一经发布永不重排/复用）

| code | name | 角色 |
|---|---|---|
| 0x0001 | hello | 握手请求（设备身份、软件版本、协议范围、能力、连接代数） |
| 0x0002 | status | 空中端状态快照（采集态、存储可写、待同步计数） |
| 0x0003 | command | 命令信封（command_id 幂等键 + operation + 可选 payload_json） |
| 0x0004 | mission | 任务冻结下发（mission_id + MissionConfig 全量 + config_sha256 复核） |
| 0x0005 | trace | 逐道原始数据（TraceMetadata 权威 header + 规范 raw payload；引用 ISSUE-009 hash） |
| 0x0006 | ack | 地面持久化确认（result ∈ persisted/duplicate/rejected/conflict） |
| 0x0007 | inventory | 对账摘要（区间计数 + xor-of-hashes + missing_ranges + conflicts） |
| 0x0008 | error | 结构化错误（DomainError.code/message/context 透传 + 可选 major 不兼容信息） |

八类均为不可变 dataclass；字段校验委托 `uav_gpr.core`（canonical UUID、64-lowercase-hex hash、UTC ISO、fail-closed 数值），不新建平行类型。heartbeat/outbox/命令状态机/manifest 分页等业务语义属 ISSUE-038～041，在本消息族之上建模但不驱动。

### Trace 语义红线

- `trace_record.payload` 只承载一次规范原始频域数组（shape = `[channel, frequency]`、dtype `complex128`、byte order little、C-order），长度必须等于 `len(channel_ids) × frequency_count × 16`。
- 频率轴与通道合同由 `mission.config_sha256`（ISSUE-006 canonical digest）冻结引用；header 携带 `channel_ids/frequency_count/dtype/byte_order/shape` 供独立解码核对，并额外携带**均匀轴三戳** `frequency_start_hz/frequency_stop_hz/frequency_points`（非轴数组本身）。接收端以 `linspace(start, stop, points)` 精确重建发送端冻结的 linspace 轴，即可在不持有 config 时逐字节复算 ISSUE-009 `raw_trace_sha256` 完成 fail-closed 对拍；持有 config 的端点再经 `decode_trace_with_config`/`register_mission_config` 将轴戳与 config 交叉核对（伪造自洽轴戳亦被拒）。该设计不违背"不重复发送派生数据"红线：轴是 raw hash 的必要输入而非可重建派生物，且三戳开销远小于轴数组。**前提**：复算要求接收端已注册通道合同（ChannelSpec 集）——经 mission config 绑定（`register_mission_config`）或独立注册（`register_trace_channels`）满足；未注册时 fail-closed 并明确指路（review P3-3）。
- 不携带 `time_base/time_processed/display` 任何派生数组（模型层无该字段 = 结构性禁止）；`raw_trace_sha256` 在 header 中引用 ISSUE-009 值，地面端复算对拍后才可 ACK。
- 首版不分块（单帧 ≤ 64 MiB 覆盖 HM30 场景最大 sweep：例如 2×4096 complex128 ≈ 131 KiB）；若未来需要分块另写 ADR。

### 版本协商

- major ≠ 1 ⇒ `INCOMPATIBLE_MAJOR_VERSION`（拒绝连接语义，error 消息承载 `IncompatibleInfo`）。
- unknown minor ⇒ 默认容忍交付（`CapabilityPolicy(minor_low=0, minor_high=...)` 可收紧）；unknown type ⇒ 一律 `UNKNOWN_MESSAGE_TYPE` 拒绝——未知类型意味着我们不知道其载荷边界，静默跳过会污染后续帧。
- spec_version（header 字段）与 major/minor 三层解耦：帧格式演进走 spec_version，语义演进走 minor，不兼容演进走 major。

## 后果

- `src/uav_gpr/transport/protocol_v1.py` 成为唯一线上编解码入口；038～041 只做链路与业务编排，不再触碰帧格式。
- parser 与消息模型可完全离线测试：任意 chunk/粘包/截断/恶意长度/未知 type-version/非 canonical header/黄金帧/跨进程确定性全部进 `tests/contract/test_protocol_v1.py`，无需 socket。
- header JSON 复用 core `to_dict()` 带来少量序列化开销，换取单一权威模型与审计可读性；trace 热路径的 payload 是零转换裸字节，开销占比可忽略。
- flags 恒零约束使未来能力位（如分块、压缩）必须 bump minor 并更新本 ADR 后继，不存在"悄悄启用"路径。
- 黄金帧 hex 常量入库（模块级 `GOLDEN_FRAMES`），任何编解码漂移都会在契约测试与子进程对拍中暴露。
- 内存安全边界明确：单连接最坏持有 ~65 MiB 缓冲上限（prefix + header + payload 各自受检），超限即 poisoned + reset，无无界分配面。
