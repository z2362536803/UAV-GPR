# ISSUE-020 实施计划：LibreVNA 包流与严格 sweep 组装器

日期：2026-09-02
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-020-librevna-stream`（执行器 engineer，任务 t2，attempt fcac6437-8b4b-4e02-aaeb-106a184a25e6）
基线：`main` @ `2c3941d`（工作树干净、origin/main 同步 0/0）；权威基线件：[docs/reports/ISSUE_020_BASELINE_CONFIRMATION.md](../reports/ISSUE_020_BASELINE_CONFIRMATION.md)（t1）
配套：本计划为 t2 执行契约与 t3 复审依据；迁移/夹具 provenance（第 4 节）按 REFERENCE_MIGRATION.md §5 模板；执行日志随执行过程追加（第 10 节）。

## 1. 目标与用户价值

把任意边界 USB 字节流解析为协议包并解析 VNADatapoint，再把 VNADatapoint 严格组装成完整、有序、校验一致的中间 sweep：增量 `LibreVnaPacketStream`（粘包/拆包、噪声/损坏同步、有界缓存）+ `StrictSweepAssembler`（按 sweep/point/channel 严格检测范围、重复、缺失、乱序、跨 sweep、reference 分母和非有限值，只在完整一致时输出 assembled sweep）。价值：为 ISSUE-021（S11 生产采集后端）提供「严格组装」核心（M04 门禁「单一真机路径、严格组装和硬件基准完成」的第二步），并满足 AGENTS.md「只有完整、校验通过且通道齐全的 sweep 才能进入存储」与「超时或缺点的 sweep 不能用零填充后冒充完整道」；不配置设备、不计算最终 S11/S22 backend metadata（ISSUE-021/022）。

## 2. 范围（M04 L50–56 + 提示词）

1. `src/uav_gpr/acquisition/librevna/stream.py`（**单一新模块**，承载三层内容）：
   - **VNADatapoint 解析**：`VNADatapoint` frozen dataclass（`point_number`/`frequency_hz`/`cdbm`/`receivers`，receivers 为 `(desc, complex)` 元组，payload 顺序）；`parse_vna_datapoint(payload)`——严格结构校验（头 `<QhH` = frequency(8,u64)+cdbm(2,i16)+point_number(2,u16)，头长 12；每接收机组 real(4,f32)+imag(4,f32)+desc(1) = 9 字节；payload 长度必须恰为 12+9k，截断/多余字节 → `LibreVnaDatapointError`）。
   - **增量包流**：`LibreVnaPacketStream`——组合复用 ISSUE-019 `transport.PacketStream`（帧/长度/CRC/噪声同步/有界缓存全部继承，不重写帧层）：`feed(data) -> list[VNADatapoint]`（任意 chunk 边界）；非 datapoint 包计数 `ignored_packets`；结构损坏的 datapoint payload 计数 `malformed_datapoints` 并继续（保持同步，不产出假数据）；`reset()`（清缓冲保留统计）/`reset_stats()`/`stats`。
   - **严格组装器**：`StrictSweepAssembler(expected_points, *, receiver_plan, timeout_ms, clock)`——状态机与丢弃统计语义逐条对齐参考 `ContinuousSweepAssembler`（见第 4 节 trusted behavior）；`feed_datapoint(dp) -> AssembledSweep | None`（完整一致才返回，绝不零填/部分输出）；`check_timeout()`（超时 → 统计 + `LibreVnaSweepTimeoutError`）；`reset()`/`reset_stats()`/`stats`。
2. 接收机 plan 校验：`ReceiverSlot(stage, mask)`、默认 `S11_RECEIVER_PLAN = ((0, REFERENCE), (0, PORT1))`（S11 输入集，ISSUE-022 扩展 stage1）；`datapoint_matches_plan` 逐槽位校验：必需槽位恰出现一次（重复拒绝、不静默采用首/末）、reference 幅度 > 0（分母）、必需值全部有限（NaN/Inf 拒绝）；非必需接收机忽略（stage/端口无关值不干扰，与参考 `parse_s11_point` 一致）。
3. 结构化错误：`LibreVnaStreamError(LibreVnaTransportError)` 家族——`LibreVnaDatapointError`(malformed_datapoint)、`LibreVnaSweepError`(sweep_integrity)、`LibreVnaSweepTimeoutError`(sweep_timeout)；沿用 transport.py 既有模式（`DomainError`+`ErrorCode.INVALID_ARGUMENT`+类级 `_reason`）。
4. 测试（tests/contract/test_librevna_stream.py，单一新测试文件）：参考黄金字节对拍（VNA_DATAPOINT 固定向量）、生成式 chunk 切分（同一包序列/同一 assembled sweep）、严格组装状态机全路径、恶意长度/有界缓存、超时、丢弃统计。

## 3. 明确排除项（M04 L58–60 + 提示词 + 任务契约）

- 不配置设备（不发送 SWEEP_SETTINGS/DEVICE_INFO/SET_IDLE 等控制包，P3-1 观察项不触发）；不计算最终 S11/S22 backend metadata（ISSUE-021/022 职责）；不分配 `trace_index`（backend 职责）；
- 不重写帧层（`transport.PacketStream` 只读消费）；不改 `transport.py` 的 VNA_DATAPOINT 跳 CRC 既有协议行为（不"修复"）；
- 不改 `core/**`（ErrorCode 枚举只读）、不改 `acquisition/backend.py`、不改 `__init__.py`；
- 不改两个参考项目（本地副本只读）；不 commit/push/merge、不创建/切换分支；不进入 ISSUE-021；
- 不在 `src/uav_gpr/acquisition/librevna/stream.py`、`tests/contract/test_librevna_stream.py`、`docs/plans/2026-09-02-issue-020-librevna-stream.md`、`docs/issues/M04_LIBREVNA.md` 之外新增任何文件（确需拆分先停止向 captain 报告）。

## 4. 关联需求/ADR/文档与参考源哈希（迁移清单，REFERENCE_MIGRATION.md §5 模板）

```text
target issue/task:        ISSUE-020 LibreVNA 包流与严格 sweep 组装器（M04，FR-003、ACQUISITION.md §5）
reference repository:     钢筋仪软件开发（E:\钢筋仪软件开发；本机不可达，WSL 仅挂载 C/D）
                          + 本地只读副本 D:\博士任务\rebar-inspector（GitHub 克隆
                          z2362536803/rebar-inspector，来源记录见 t1 基线单 §3.3/ISSUE-019 计划 §4）
reference branch + HEAD:  manifest 冻结：feat/issue-16-pause-resume @
                          938875234a99b47d78cfec940671005b63e9d15c（ISSUE-001 冻结时点）
                          本地副本：main @ 7c522d2aebe6a835acb969e8012565715f64a238
reference worktree status:manifest 记录 worktree_dirty=True；本地副本 librevna 候选源
                          SHA-256 与 manifest 逐一相等（t1/ISSUE-019 实测 11/11，仅 CRLF 行尾差）
source file(s) + SHA256（本次实际阅读并采用）:
  librevna_usb.py        a73adc1c41f186ee51ab938c4c245809bf36dea0bc2769ad1115fffa99b22d87
                         （ContinuousSweepAssembler 状态机/统计/超时语义，L150–409/640–666）
  librevna_protocol.py   6a41c4b11cfa8272c1687893a53219ea0465987a378700a0b74ab3973353d8ce
                         （VNADatapoint payload 布局 L50–80、parse_vna_datapoint L367–383、
                         desc 位掩码 L65–80、datapoint_to_s11/parse_s11_point 校验语义 L386–437）
  aggregation.py         c8b64176f461f75a72809f0d072c09a31c752a3ede49a5d81543bfbf026126d1
                         （参考：未采用——sweep 数据结构由 UAV-GPR 自有模型承接）
trusted behavior/contract（采用）:
  - VNADatapoint 布局：头 <QhH（frequency u64/cdbm i16/point_number u16，头长 12）+
    每组 real(4,f32)+imag(4,f32)+desc(1)=9 字节（BLOCKED 布局：全部 real 块 + 全部 imag 块 + 全部 desc 块）；
  - desc 位掩码：bits7–5=stage、bit4=reference、bit3..0=Port4..Port1；
    reference 判定 desc&0x10 优先（0x11=ref+port1 视为 reference，port 槽位排除 ref 位）；
  - parse_s11_point 校验语义：必需槽位缺失/重复 → 无效（不静默采用首/末）；
    reference 幅度 0 → 无效；S 值实/虚部非有限 → 无效；无关 receiver 忽略；
  - ContinuousSweepAssembler 状态机：只有 point 0 能开始新 sweep；严格连续
    0..n_points-1；重复（<expected）/向前跳号（>expected）/越界/无效点 → 当前道作废，
    进入"等下一个 point 0"失同步态；失同步段非零点忽略且整段只计一次 drop；
    新 point 0 打断残缺道只对旧道 +1；绝不拼道、残缺绝不返回；
  - 完成校验：频率严格递增（防御性；参考对非有限频率也抛错，UAV 侧 u64 整数天然有限）；
  - 统计语义（整道/事件计数）：dropped_sweeps / incomplete_sweeps（⊂ dropped，收到过
    point 0 未完成）/ duplicate_points / out_of_range_points / invalid_points；
  - 超时：sweep 超时 → 抛结构化 Sweep 错误（参考 `LibreVnaSweepError`，测试
    test_missing_point_then_timeout/test_no_silent_partial_sweep 语义对齐）；
  - 黄金字节向量与 `_vna_payload` 构造范式（来源见下）。
excluded behavior（排除）:
  - datapoint_to_s11/parse_s11_point/parse_s11_s22_point 的 S11/S22 比值计算
    （ISSUE-021/022；本 Issue 只做输入校验，不产出 S11/S22 数值）；
  - encode_sweep_settings/SweepSettings/stages_bitmap/DeviceInfo（ISSUE-021 配置）；
  - 参考后端 acquire/pending 队列/暂停恢复/重连（ISSUE-021/023）；
  - `take_completed` 双步取道设计（UAV 侧改为 feed 内联返回完整 sweep，见 D3）；
  - aggregation/acquired/backend/simulated/file_replay/sweep_config 模块整体；
  - UAV-GPR 全部旧采集代码（含 legacy/continuous 双路径）。
new target module(s):     src/uav_gpr/acquisition/librevna/stream.py（唯一新模块）
UAV-specific adaptations:
  - 结构化错误沿用 transport.py 先例：LibreVnaStreamError(LibreVnaTransportError) +
    类级 reason + 类型化子类（core ErrorCode 只读不扩展）；
  - 帧层完全复用 ISSUE-019 transport.PacketStream（组合而非重写），有界缓存继承
    （单帧 ≤4096、噪声逐字节即时丢弃；恶意长度不分配无限内存）；
  - 损坏 datapoint payload：参考后端直接抛 LibreVnaProtocolError fail-closed；
    UAV 侧解析器抛 LibreVnaDatapointError、流层计数后继续（保持同步、统计可观测），
    ISSUE-021 可按需升级为 fail-closed（设计决策 D2）；
  - 超时：注入 clock（默认 time.monotonic）+ 显式 check_timeout()（统计 + 结构化
    错误），不依赖固定 sleep（AGENTS.md §10）；
  - 完成校验频率单调性：frequency 为 u64 整数，仅校验严格递增（非有限不可能）；
  - feed 内联返回完整 sweep（无 pending 队列/take 双步），见 D3。
tests/golden fixtures migrated:
  - VNA_DATAPOINT_PAYLOAD_HEX =
    "0065cd1d0000000018fc00000000803f000000bf000000000000803e1001"
    （freq=500MHz、cdbm=-1000、point=0、reals=[1.0(ref),-0.5(port1)]、
    imags=[0.0,0.25]、descs=[0x10,0x01]）与
    VNA_DATAPOINT_PACKET_HEX =
    "5a26001b0065cd1d0000000018fc00000000803f000000bf000000000000803e1001fecc9f61"；
  - `_vna_payload` 构造范式（头 + 全部 real + 全部 imag + 全部 desc）；
  - 来源（只读，不在 manifest 白名单——tests/** 排除，provenance 记录于此）：
    D:\博士任务\rebar-inspector\tests\test_librevna_protocol.py（455 行，
    SHA-256 f3019795c6906ae62479532b755ac73dd375d1452a5e4c5eaca31451a7cef5c7，
    固定向量 L50–56、_vna_payload L69–84）与
    D:\博士任务\rebar-inspector\tests\test_librevna_usb_backend.py（947 行，
    SHA-256 2d4db31333ef58d586b0f024531ae6f593ea8c38be351708792306272a43bc38，
    ContinuousSweepAssemblerTests L235–441、超时/无效点后端用例 L697–777）。
new tests added:          tests/contract/test_librevna_stream.py（新契约测试，失败测试优先）
numeric or performance comparison: 不适用——无真机、无性能声明（ISSUE-023 负责硬件基准；
                          参考历史数字不得写成新结果）
license/provenance review:参考项目为内部 proprietary；本迁移为契约提取与适配（行为级），
                          新实现为独立代码（非逐行复制），docstring 声明来源与既有协议
                          行为（含 VNA_DATAPOINT 跳 CRC 不"修复"）；未复制大模块。
```

## 5. 设计决策（ADR 级，含备选与理由）

| # | 决策 | 理由 | 备选（否决理由） |
|---|---|---|---|
| D1 | 单一新模块 `stream.py` 承载解析+流+组装器三层 | inScope 唯一模块约束（t2 契约）；三层共享 desc 位掩码/错误家族/常量，同模块内聚；transport.py 保持 sweep-free | 拆 protocol.py/assembler.py 多文件（超出 inScope；参考两文件分层在 UAV 侧合并为 stream 层） |
| D2 | 损坏 datapoint payload：`parse_vna_datapoint` 抛 `LibreVnaDatapointError`；`LibreVnaPacketStream.feed` 捕获并计数 `malformed_datapoints` 后继续（不中断流） | VNA_DATAPOINT 跳 CRC 是既有协议行为，损坏 payload 可能只是噪声对齐的假帧；计数+继续保持同步且统计可观测，满足「坏数据不产出假完整 sweep」；参考后端抛 ProtocolError 的 fail-closed 决策留给 ISSUE-021 消费层 | 流层直接抛错（单帧损坏中断整流，噪声场景下过于脆弱）；静默忽略（统计不可观测，违反验收 3） |
| D3 | 组装器 feed 内联返回完整 `AssembledSweep`（无 pending 队列、无 take 双步）；完成后自动复位等待下一 point 0 | 纯组装器无外部流控需求；UAV 侧 ISSUE-021 的 pending 路由是 backend 职责（参考 `_pending_packets` 属后端）；内联返回天然不丢点（下一道 point 0 直接开始新道） | 参考式 feed/take_completed 双步（引入 pending 状态机，超出本 Issue；后端接线时再评估） |
| D4 | 超时：注入 `clock`（默认 `time.monotonic`）+ `timeout_ms`（None 禁用）+ 显式 `check_timeout()`：过期 → 统计（timeouts⊂incomplete⊂dropped）并抛 `LibreVnaSweepTimeoutError` | 「超时产生统计/结构化错误」验收：统计与结构化错误同时满足；显式轮询由 ISSUE-021 采集循环调用（对齐参考 acquire 的 deadline 检查）；注入时钟使测试确定、无固定 sleep | feed 内隐式超时检查（时序隐式化，测试不可控）；仅统计不抛错（结构化错误缺失，验收弱化） |
| D5 | 接收机 plan：`ReceiverSlot(stage, mask)` + 默认 `S11_RECEIVER_PLAN`；槽位匹配语义对齐参考（ref 位优先、port 槽排除 ref 位）；必需槽位恰一个、reference 幅度>0、值有限；无关接收机忽略 | 「通道/receiver 字段」与「reference 分母、非有限值」验收的直接落地；S11 输入集是 ISSUE-021 的最小生产 plan；ISSUE-022 以 (0,REF)+(0,P1)+(1,REF)+(1,P2) 扩展同一机制 | 组装器硬编码 stage0/port1 判定（ISSUE-022 无法复用，违反多通道前瞻）；只查 reference 不查端口槽（验收「通道/receiver 字段」不完整） |
| D6 | 状态机/统计逐条对齐参考 `ContinuousSweepAssembler`（point 0 同步、严格连续、重复/跳号/越界/无效作废、失同步整段计一次、绝不拼道、incomplete⊂dropped） | 参考行为已经真机验证与 15+ 用例固化；「绝不拼道」「宁丢不拼」与 ACQUISITION.md §5 完全一致；逐条对齐便于复审对照 | 宽松策略（缓冲乱序点后补齐）（无 sweep_id 下无法区分跨道，违反绝不拼道） |
| D7 | 完成校验：频率严格递增（u64 int 比较），违反抛 `LibreVnaSweepError` | 参考完成时防御性校验（test_non_monotonic_frequency_raises）；「只在完整一致时输出」 | 不做单调性校验（设备错序频点会产出逻辑不一致 sweep） |
| D8 | 测试：参考黄金向量对拍 + 生成式 chunk 切分（seed 固定随机切分，1 字节粒度/跨包/跨点/跨 sweep）+ 参考状态机用例全移植 + 恶意长度/有界缓存 + 超时注入时钟 | 「任意 byte chunking 得到同一包序列」验收只能由生成式切分证明；参考用例是状态机语义的权威回归 | 仅手写固定切分（无法覆盖任意边界）；仅移植参考用例（生成式验收缺失） |

## 6. 文件改动（inScope 精确路径，changedPaths 必须与此逐一相等）

| 路径 | 内容 |
|---|---|
| `src/uav_gpr/acquisition/librevna/stream.py` | 新模块：desc 位掩码常量、`VNADatapoint`、`parse_vna_datapoint`、`LibreVnaStreamError` 家族、`PacketStreamStats`、`LibreVnaPacketStream`、`ReceiverSlot`/`S11_RECEIVER_PLAN`、`datapoint_matches_plan`、`SweepAssemblerStats`、`AssembledSweep`、`StrictSweepAssembler` |
| `tests/contract/test_librevna_stream.py` | 新契约测试（失败测试优先；黄金对拍/生成式切分/状态机全路径/有界缓存/超时/统计） |
| `docs/plans/2026-09-02-issue-020-librevna-stream.md` | 本计划文档（t2 先落盘；执行日志第 10 节随执行追加） |
| `docs/issues/M04_LIBREVNA.md` | 仅 ISSUE-020 状态行：`Planned → In progress → Review`（勿动其它条目） |

## 7. 测试矩阵（提示词必测项 → 测试名，与实现逐一对应）

| 必测项 | 测试 | 手段 |
|---|---|---|
| 黄金字节对拍 | `test_parse_golden_payload_vector`、`test_stream_golden_packet_vector`、`test_stream_corrupted_datapoint_crc_still_parses` | 参考固定向量（第 4 节）直接断言字段值 |
| 解析结构校验 | `test_parse_rejects_short_payload`、`test_parse_rejects_truncated_group`、`test_parse_rejects_trailing_bytes`、`test_parse_empty_receivers_allowed` | `LibreVnaDatapointError` 断言；12 字节头无接收机按参考可解析（组装校验再拒） |
| 任意 chunk 边界（生成式） | `test_generative_chunking_same_datapoint_sequence`、`test_generative_chunking_one_byte_granularity`、`test_generative_chunking_same_assembled_sweep`、`test_generative_chunking_across_sweep_boundary` | `random.Random(seed)` 固定 seed 随机切分（含 1 字节粒度、噪声/坏包混入），逐 chunk feed 断言序列/统计一致 |
| 帧/长度/CRC/噪声同步 | `test_noise_prefix_ignored`、`test_non_datapoint_packet_counted_ignored`、`test_bad_crc_non_datapoint_dropped`、`test_malformed_datapoint_payload_counted`、`test_invalid_length_realigns`、`test_split_across_reads`、`test_reset_clears_buffer_keeps_stats` | 复用参考 framing 用例，经 LibreVnaPacketStream 端到端断言 |
| 有界缓存/恶意长度 | `test_malicious_length_field_bounded_buffer`、`test_garbage_flood_buffer_bounded`、`test_max_length_packet_accepted_at_frame_cap` | 恶意长度字段/1MB 垃圾洪泛后断言内部 buffer ≤ MAX_PACKET_LENGTH+8 且后续合法包仍解析；4096 上限帧接受、4097 拒绝 |
| 组装状态机（参考移植） | `test_constructor_rejects_invalid_expected_points`、`test_constructor_rejects_invalid_receiver_plan`、`test_constructor_rejects_invalid_timeout`、`test_complete_sweep_from_point_zero`、`test_only_syncs_from_point_zero`、`test_new_point_zero_interrupts_drops_current`、`test_never_stitches_two_sweeps`、`test_duplicate_point_drops_current`、`test_stitching_partial_a_then_b_without_zero_rejected`、`test_initial_unsynced_nonzero_counts_one_drop`、`test_forward_jump_drops_current`、`test_backward_point_drops_current`、`test_two_complete_consecutive_sweeps`、`test_out_of_range_point_drops_current`、`test_non_monotonic_frequency_raises`、`test_no_partial_output_ever`、`test_assembled_sweep_ordered_and_started_at` | 参考 ContinuousSweepAssemblerTests 语义逐条对齐（L235–441） |
| receiver plan/分母/非有限 | `test_plan_valid_s11_datapoint`、`test_missing_reference_slot_invalid`、`test_missing_port1_slot_invalid`、`test_duplicate_reference_receiver_invalid`、`test_zero_reference_denominator_invalid`、`test_nan_receiver_value_invalid`、`test_inf_receiver_value_invalid`、`test_extra_receivers_ignored`、`test_empty_receivers_invalid_for_s11_plan`、`test_dual_stage_payload_valid_under_s11_plan`、`test_invalid_datapoint_drops_current_sweep` | `datapoint_matches_plan` 单元 + 组装器集成断言 |
| 超时 | `test_timeout_drops_and_raises_structured_error`、`test_timeout_disabled_when_none`、`test_timeout_before_deadline_noop`、`test_timeout_no_active_sweep_noop` | 注入 clock 序列控制（无固定 sleep） |
| 统计可观测 | `test_drop_stats_subsets_after_timeout`（timeouts⊂incomplete⊂dropped）、`test_stats_reset`、`test_reset_keeps_stats` | 冻结 dataclass 断言 |
| 集成（流+组装器） | `test_full_pipeline_generative_chunking`、`test_corrupted_datapoint_payload_no_fake_sweep`、`test_reference_zero_mid_sweep_drops_and_resyncs`、`test_bad_crc_mid_sweep_still_assembles_reference_behavior`、`test_stream_and_assembler_stats_observable` | 原始字节流端到端：坏数据不产出假完整 sweep |
| 回归 | ISSUE-019 定向 50 passed、依赖 41 passed 不被破坏；全量 verify.py 792 passed/1 deselected 基线；ruff/mypy/import/`git diff --check` | — |

## 8. 性能/数据风险

- 无性能声明：本 Issue 无真机基准（ISSUE-023 负责）；不把参考项目历史速度写成新结果。
- 有界性：帧缓冲继承 transport.PacketStream（单帧 ≤4096、噪声逐字节即时丢弃——恶意长度字段 0xFFFF 逐字节重对齐，缓冲不可能超过 ~4096+4 字节）；datapoint 解析输入 ≤4088 字节（12+9k，k≤452），无无限分配路径；组装器半道缓冲 ≤ expected_points×9 字节 + 常数（构造时冻结 expected_points，防恶意超大点数：构造参数 int ≥2 即可，无内存放大风险）。
- 数据风险：不落盘、不联网、不修改 raw；只读消费 transport.py；不修改两个参考项目；默认测试不枚举 USB（AST 守卫：测试不 import usb/serial/网络根）。
- 线程风险：本模块无线程（注入 clock 纯函数）；不引入固定 sleep。
- 行为风险：VNA_DATAPOINT 跳 CRC 为参考既有协议行为，组装器以结构校验/点序校验兜底（不"修复"）；损坏 payload 计数继续（D2）与参考 fail-closed 的差异已在第 4 节记录，供 ISSUE-021 决策。

## 9. 完成定义与回退

- 完成定义（全部满足才可登记 completed）：验收标准 3 条（M04 L62–66）逐条 PASS；定向测试红灯→绿灯记录于执行日志；全量 verify.py + ruff + mypy + import + `git diff --check` 全绿；`git status` 仅 4 个 inScope 路径改动（changedPaths 与 inScope 逐一相等）；M04 状态行更新为 Review；不 commit/push/merge、不创建分支。
- 回退方式：实现为新增文件（stream.py + 测试 + 两份文档），只修改 M04 状态行一行；异常时删除未登记文件并还原 M04 状态行即可回到 `main @ 2c3941d` 干净基线；无破坏性操作。

## 10. 执行日志（随执行追加）

```text
[2026-09-02] t2 开工：claim t2（attempt fcac6437-8b4b-4e02-aaeb-106a184a25e6）→ in_progress。
[2026-09-02] 参考审计（只读）：ContinuousSweepAssembler（librevna_usb.py L150–409）、
              后端超时/无效点用例（test_librevna_usb_backend.py L697–777）、
              黄金向量与 _vna_payload（test_librevna_protocol.py L50–84）；
              源哈希记录于第 4 节。
[2026-09-02] 计划文档落盘（本文件第 1–9 节）。
[2026-09-02] 失败测试优先（红灯，实现前）：
              $ python3 -m pytest tests/contract/test_librevna_stream.py -q
              -> ERROR: ModuleNotFoundError: No module named
                 'uav_gpr.acquisition.librevna.stream'（collection 1 error）——红灯成立。
[2026-09-02] 最小实现：src/uav_gpr/acquisition/librevna/stream.py 落盘
              （VNADatapoint 解析 / LibreVnaPacketStream / StrictSweepAssembler /
               ReceiverSlot plan / 错误家族 / 统计）。
[2026-09-02] 定向测试（绿灯）：
              $ python3 -m pytest tests/contract/test_librevna_stream.py -q
              -> 61 passed in 0.50s——绿灯成立。
              （过程：第 1 轮 26 failed/35 passed——实现漏初始化统计计数
              （__init__ 未调 reset_stats），修复后 2 failed/59 passed；
              剩余 2 个失败为测试数据笔误——0x12 实际是 stage0（非 stage1）、
              reset 后完整包重新解析属正确行为——修正测试数据后全绿；
              随后静态检查修复（F401/B905/RUF007/F841/E501）后仍 61 全绿。）
[2026-09-02] 依赖回归：
              $ python3 -m pytest tests/contract/test_librevna_transport.py \
                  tests/unit/test_reference_manifest.py tests/contract/test_acquisition_backend.py -q
              -> 91 passed in 1.58s（ISSUE-019：50 + ISSUE-001：13 + ISSUE-015：28）。
[2026-09-02] 静态检查：`python3 -m ruff check src tests` -> All checks passed!
              （修复 F401×2、B905/RUF007 zip→pairwise、F841×2、E501 后干净）；
              `python3 -m mypy src` -> Success: no issues found in 41 source files。
[2026-09-02] 门禁（全量，tools/quality/verify.py）：
              $ python3 tools/quality/verify.py
              853 passed, 1 deselected in 132.24s (0:02:12)   # 792 基线 + 61 新测试
              All checks passed!                               # ruff
              Success: no issues found in 41 source files      # mypy
              package import ok                                # import 检查
              [quality] all gates passed
              VERIFY_EXIT=0
[2026-09-02] 工作树/交付检查：`git diff --check` clean；`git status --porcelain=v1 -b`
              仅 4 个 inScope 路径（1 modified + 3 untracked，见第 6 节）+ t1 基线单
              （t1 交付物，不计入 t2 inScope）；无缓存/日志/实测数据残留。
[2026-09-02] M04 状态行：Planned → In progress → Review（最终态，2026-09-02）。
```

> 后续记录：本计划的执行日志只记录事实与数字；t3 复审报告独立输出。
