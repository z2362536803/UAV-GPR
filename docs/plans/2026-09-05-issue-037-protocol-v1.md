# ISSUE-037 实施计划：协议 ADR、消息模型与二进制 framing（plan-first）

日期：2026-09-05 · 执行：engineer（自动化轮）· 基线：`main @ dc5d697`（见 `docs/reports/ISSUE_037_BASELINE_CONFIRMATION.md`）

## 1. 范围与 inScope（5 路径，changedPaths 与之逐一相等）

1. `docs/plans/2026-09-05-issue-037-protocol-v1.md`（本文件）
2. `docs/adr/0006-protocol-v1-framing.md`（protocol v1 binary framing ADR：候选/选择/后果 + 测试证据引用）
3. `src/uav_gpr/transport/protocol_v1.py`（不可变消息模型 + envelope + 增量 codec/parser + 黄金帧常量）
4. `tests/contract/test_protocol_v1.py`（契约测试矩阵 + 跨进程确定性子进程钉死）
5. `docs/issues/M07_TRANSPORT.md`（仅 ISSUE-037 状态行 Planned → Review/Done 按流程）

不新增任何 inScope 外文件；不改 `core/*`、`storage/*`、`application/*` 既有文件；不 commit/push/merge。

## 2. 排除项（Issue 明确）

- 不打开 socket、不做 TCP 连接/握手驱动、不实现 heartbeat/outbox/业务状态机（属 ISSUE-038～041）。
- 不迁移旧 UAV-GPR `RemoteSender`/`Receiver`/NPZ 线上格式（M07 L3 否定参考，ADR-0005）。
- 不分块传输：payload ≤ MAX_PAYLOAD_BYTES，单帧承载完整 sweep；分块需求出现时另写 ADR。
- 不新增第三方依赖（numpy/stdlib only）。

## 3. 设计决策（ADR-0006 冻结要点）

### 3.1 Frame 布局（全部大端网络序）

```text
offset  size  field
0       4     magic = 55 41 56 50 ("UAVP")
4       1     major (u8)                —— 当前 = 1
5       1     minor (u8)                —— 当前 = 0
6       2     type (u16be)              —— MessageType 枚举码，未知即拒
8       2     flags (u16be)             —— 必须为 0（RESERVED_FLAGS_MUST_BE_ZERO，能力协商位留给未来 minor）
10      4     header_length (u32be)     —— UTF-8 canonical JSON header 字节数
14      4     payload_length (u32be)    —— binary payload 字节数
18      H     header: UTF-8 canonical JSON（键排序、紧凑分隔符、ASCII、非有限 float 拒绝）
18+H    P     payload: binary（trace_record 为规范 raw C-order complex128 LE 数组；其余消息 P==0）
```

- 固定前缀 18 字节；parser **在读 payload（甚至 header）之前**验证 `header_length ≤ MAX_HEADER_BYTES(1 MiB)` 与 `payload_length ≤ MAX_PAYLOAD_BYTES(64 MiB)` 及总帧长上限，超限 fail-closed，不无界分配（缓冲只在已验证长度内增长）。
- magic/major 不符 ⇒ `FrameError(INCOMPATIBLE_MAJOR_VERSION / PROTOCOL_FRAME_INVALID)`；unknown minor ⇒ parser 层容忍交付（能力策略在应用层），unknown **type** ⇒ 解码拒绝（消息族不允许静默丢弃语义载荷）。
- 域分离：framing 域 magic `"UAVP"` ≠ raw-hash 域 `"UAVGPR-RAW-SHA256"` ≠ config digest 域；header canonical JSON 规则与 `MissionConfig.to_canonical_json` 同口径（`sort_keys=True, separators=(",",":"), ensure_ascii=True`）。

### 3.2 Header 通用字段（envelope 冗余校验）

`spec_version`(u64=1)、`major`、`minor`、`type_name`、`flags`：codec 双向核对 header 声明与 binary 前缀一致（防篡改/错拼帧）；`mission_id` 出现在 mission/trace/ack/inventory/status/error/command（hello 可选——入网时尚无任务）。

### 3.3 八类消息（type code 稳定，禁止重排复用）

| code | name | 方向语义 | 关键字段（payload 列于后） |
|---|---|---|---|
| 0x0001 | hello | air→ground 握手请求 | device_id, software_version, protocol_major/minor, min_accepted_major, capabilities(seq), session_id, connection_generation |
| 0x0002 | status | air→ground 状态快照 | device_id, mission_id?, connection_generation, acquisition_state, storage_writable, pending_trace_count, error? |
| 0x0003 | command | ground→air 命令信封 | command_id, operation, mission_id?, issued_utc, payload_json? |
| 0x0004 | mission | ground→air 任务冻结下发 | mission_id, config(MissionConfig.to_dict 全量), config_sha256(复核) |
| 0x0005 | trace | air→ground 逐道原始数据 | identity(MissionId/TraceUid/trace_index/device_id), trace_metadata(TraceMetadata.to_dict 权威, 含 raw_trace_sha256 与 GNSS match), config_sha256, channel_ids, frequency_count, dtype="complex128", byte_order="little", shape=[C,F], **均匀轴三戳 frequency_start_hz/frequency_stop_hz/frequency_points**（接收端据此精确重建冻结 linspace 轴并逐字节复算 ISSUE-009 hash，fail-closed 对拍；持有 config 的端点另经 register_mission_config/decode_trace_with_config 交叉核对轴戳与通道合同）; **payload = 规范 raw 数组 bytes**（不含频率轴数组/派生 display/time——§2 红线） |
| 0x0006 | ack | ground→air 持久化确认 | mission_id, trace_uid, trace_index, raw_trace_sha256, result(persisted\|duplicate\|rejected\|conflict), received_utc |
| 0x0007 | inventory | 双向对账摘要 | mission_id, device_id, first_index, last_index, count, xor_of_hashes, missing_ranges, conflicts, complete |
| 0x0008 | error | 双向结构化错误 | code(DomainError.code), message(ASCII safe), context(JSON-safe), origin_type?, origin_device_id, occurred_utc, mission_id? |

- 全部消息为 `@dataclass(frozen=True, slots=True)`，`__post_init__` fail-closed（复用 core 类型校验：canonical UUID、64-hex hash、UTC、非负 int、有限 float）。
- `to_payload()`：`None`（七类元数据消息）或 `bytes`（trace：raw 数组 C-order `<c16` 字节，validate-only 不拷贝放大）。
- `encode_message(msg) -> bytes` / `decode_frame(header_bytes, payload_bytes) -> msg`；`ProtocolEnvelope{major,minor,type,flags,header,payload}` 为解码产物（header 保留原文 bytes 供 canonical 复核）。
- unknown minor：**解析容忍 + 能力策略对象**（`CapabilityPolicy(accept_minor_range)`），major≠1 一律拒绝并给出 `IncompatibleInfo`（由 error 消息承载）。
- 禁 pickle/NPZ：负面测试证明 pickle 流无法通过 magic 门；payload 只接受 `bytes|bytearray|memoryview` 且 trace payload 长度必须 == channels×freqs×16。

### 3.4 增量 parser

`FrameParser(policy=...)`：`feed(data: bytes) -> list[DecodedFrame]`（任意 chunk 合法）、`pending_bytes`、`reset()`；内部用 `bytearray` 有界累积（≤ prefix+MAX_HEADER 才读长度域；payload 仅在声明长度验证后按需收取）。损坏帧 ⇒ `FrameError(code, partial=False/True)` 且 parser 进入 poisoned 态（要求 reset），防止在错位字节流上猜帧边界。

### 3.5 黄金帧（golden bytes）

`GOLDEN_FRAMES: tuple[GoldenFrame{name, hex}, ...]` 固化于模块常量：hello minimal、error minimal、trace minimal（2ch×4freq 手工可复算数组）、oversize-header 反例说明不入库。**跨进程确定性**：测试 spawn `[sys.executable, "-S", "-c", script]` 独立解释器重新 encode 同样消息，断言 stdout hex 与本进程及 golden hex 三方逐字节相等。

## 4. 测试矩阵（失败测试优先，先红后绿）

1. 任意 chunk：黄金帧流按 1 字节/随机小块/半帧喂入 ⇒ 产出恒等、顺序保持；
2. 粘包：N 帧一次 feed ⇒ N 个 DecodedFrame 依序；
3. 截断：前缀截断 ⇒ 零产出零异常；EOF 后 `pending_bytes>0`；
4. 恶意长度：header/payload 超上限、prefix+巨大 length ⇒ 在读 payload 前 `FRAME_TOO_LARGE`，缓冲峰值受控（断言 pending ≤ 18+1MiB）；坏 magic ⇒ `MAGIC_MISMATCH`；flags≠0 ⇒ `FLAGS_NONZERO`；
5. 未知 type/version：major=2/0 ⇒ `INCOMPATIBLE_MAJOR_VERSION`；unknown minor ⇒ policy 决定 accept/reject；unknown type ⇒ `UNKNOWN_MESSAGE_TYPE`；
6. 非 canonical header：键乱序/多余空白/UTF-8 BOM/NaN Infinity/bool 当 int/重复键 ⇒ `HEADER_NOT_CANONICAL`（encode 侧恒产 canonical）；
7. 黄金帧 + 跨进程：subprocess `-S -c` 重编码 ⇒ 三方字节相等；
8. 字段契约：8 类消息 round-trip 恒等（含 numpy array equal、tuple 序）；trace payload hash 与 `compute_raw_trace_sha256` 对拍（ISSUE-009 引用锚点）；config digest 复核失配 ⇒ `CONFIG_DIGEST_MISMATCH`；pickle payload 拒绝；display/time 派生数据无字段可携带（结构即约束，登记负面说明）。

## 5. 实施顺序与门禁

计划文档（本文件）→ ADR-0006 → 红灯测试（先跑必失败）→ `protocol_v1.py` 实现 → 定向绿灯 → M07 状态行 → `verify.py` 全量（基线 1449 passed 之上 + 新增用例；mypy 56→57 files 为预期变化）→ ruff/mypy/import/diff-check 全绿 → 登记（acceptanceResults criterion 逐字、changedPaths=5 路径、commandsRun 精简）。

## 6. 风险与挂账预案

- mypy strict + `slots=True` dataclass 的 `Self` 返回需 `cast`；以局部 cast 解决，不放宽全局。
- `-S` 模式导入 uav_gpr 依赖 sys.path 注入（脚本内 `sys.path.insert(0, str(Path(__file__)...))`）；如 site-packages 缺失导致 numpy 不可用则退化为普通 `python -c`（仍是独立进程，确定性口径不变）。
- flaky 挂账 `test_close_interrupts_acquire`（036 §6.3）若首跑命中，复跑即绿，不算本 Issue 回归。

## 7. 执行日志（t2，2026-09-05）

| 步骤 | 结果 |
|---|---|
| 计划文档落盘 | 本文件（先于一切代码） |
| ADR-0006 落盘 | `docs/adr/0006-protocol-v1-framing.md`：候选（NPZ/pickle、纯 JSON、第三方 codec、固定前缀+canonical JSON+binary）→ 选择 → 后果；状态 Proposed（复审确认前不视为最终决定） |
| 红灯测试 | `tests/contract/test_protocol_v1.py` 先行落盘；首轮收集即失败（模块不存在），实现期间经历 21 failed / 35 passed 等梯度红灯后转绿 |
| 实现 | `src/uav_gpr/transport/protocol_v1.py`：magic UAVP / major-minor / u16 type / flags=0 / u32 header & payload length（1 MiB / 64 MiB 上限，读 body 前验证）、canonical UTF-8 JSON header（sort_keys + compact separators + ASCII + NaN/Inf/重复键/乱序拒绝）、八类不可变消息（frozen dataclass + 精确冻结 key-set）、增量 FrameParser（结构损坏即 poisoned + 显式 reset）、黄金帧 GOLDEN_FRAMES（hello/error/trace/ack） |
| 设计偏差登记 | trace header 额外携带**均匀轴三戳** frequency_start_hz/frequency_stop_hz/frequency_points（ADR Trace 红线段同步修订）：接收端无需 config 即可精确复算 ISSUE-009 raw_trace_sha256 完成 fail-closed 对拍；轴数组本身仍不发送；持有 config 的端点经 register_mission_config / decode_trace_with_config 将轴戳与冻结 config 交叉核对（自洽伪造轴戳亦拒）。§3.3 表中 frame_timestamps_ns 未采用（时间事实权威在 TraceMetadata.to_dict，避免平行字段） |
| 定向绿灯 | `pytest tests/contract/test_protocol_v1.py -q`：**57 passed**（byte-by-byte、chunk∈{1..4096} 粘包、截断 pending、恶意长度 bounded-buffer 断言、major/minor/type 策略、非 canonical 多变体、黄金帧四枚、subprocess `-S` 跨进程三方相等、pickle/NPZ 负面、display/time 无槽、payload 篡改与轴戳伪造双路径、字段契约 fail-closed、线程/socket 边界检查） |
| flaky 挂账命中 | 首次全量 verify.py：1 failed(test_close_interrupts_acquire)/1505 passed——正是 t1 登记的 036 §6.3 既有 flaky（隔离复跑 1 passed、backend 目录 61 passed）；按预案复跑 |
| 全量门禁（复跑） | **1506 passed / 4 deselected**（= 基线 1449 + 新增 57 ✓）+ ruff All checks passed + mypy Success no issues in **57** source files（56→57 预期变化兑现）+ package import ok + all gates passed exit 0；日志重定向仓库外 `/tmp/verify_037_t2_final.log`，零遗留 |
| M07 状态行 | L7 Planned → Review（t3 复审对象）；未 commit/push/merge |
| git 边界 | changedPaths 恰为 inScope 5 路径（t1 基线单属 t1 交付不计入）；`git diff --check` 干净 |

## 8. repair-round-2 执行日志（t4，2026-09-05）

依据 docs/reports/ISSUE_037_REVIEW_REPORT.md §3/§10：

| 项 | 处置 | 证据 |
|---|---|---|
| P2-1（必改）trace 头字段 DomainError 逃逸 parser、绕过 poisoned 契约 | _decode_trace 中 _trace_context 与 _recover_axis 调用纳入与非 trace 分支一致的 except DomainError -> FrameError(code, message, context) 转换；合法帧语义与黄金字节零改动 | 新增参数化负面测试 test_malformed_trace_header_fields_fail_closed_with_poison（8 例：channel_ids=str、frequency_points=str、dtype=int、shape=str、raw_trace_sha256 非 hex、config_sha256=null、metadata=str、frequency_start_hz=str），先红（7 failed）后绿；断言 FrameError + parser.poisoned is True + poisoned 持续至显式 reset；回归守卫 test_valid_trace_still_decodes_after_repair_touchpoints |
| P3-1（顺带）envelope bool-as-int 宽松 | _finish_decode envelope 冗余校验显式 isinstance(actual, bool) -> FrameError 拒绝 | 新增 test_envelope_bool_as_int_stamp_rejected（major:true 被拒且 poisoned） |
| P3-2（可选）import 时注册副作用 | golden_messages() 拆为纯构建器；注册移至 _register_golden_contracts() 并在模块 docstring Global state 段显式登记 | 代码结构 + docstring；黄金字节不变（67 全绿含四枚 golden/跨进程对拍） |
| P3-3（可选）ADR 复算前提缺失 | ADR-0006 Trace 红线段补「前提：接收端已注册通道合同（经 mission config 或 register_trace_channels 独立注册）」 | docs/adr/0006-protocol-v1-framing.md Trace 语义红线节 |

门禁（t4 权威复跑，日志仓库外 /tmp/verify_037_t4f.log）：定向 **68 passed**（= t2 57 + 修复新增 11 ✓，先红后绿）；全量 verify.py **1517 passed / 4 deselected**（= t2 基线 1506 + 新增 11 ✓，无 flaky 命中）+ ruff All checks passed + mypy Success no issues in 57 source files + package import ok + all gates passed exit 0；git diff --check 干净。t4 实际修改文件 = src/uav_gpr/transport/protocol_v1.py、tests/contract/test_protocol_v1.py（评审建议的负面测试载体）、docs/adr/0006-protocol-v1-framing.md、本计划文档；无其他范围外变更；未 commit/push/merge。
