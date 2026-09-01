# ISSUE-021 实施计划：S11 生产采集后端

日期：2026-09-02
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-021-librevna-backend`（执行器 engineer，任务 t2，attempt 3689c04d-e7f5-44ea-95f5-f4f4a18ceafe）
基线：`main` @ `def2c28d759c92c443ad81354227e39bb5a7ca11`（工作树干净、origin/main 同步 0/0）；权威基线件：[docs/reports/ISSUE_021_BASELINE_CONFIRMATION.md](../reports/ISSUE_021_BASELINE_CONFIRMATION.md)（t1）
配套：本计划为 t2 执行契约与 t3 复审依据；迁移/夹具 provenance（第 4 节）按 REFERENCE_MIGRATION.md §5 模板；执行日志随执行过程追加（第 10 节）。

## 1. 目标与用户价值

用唯一生产路径 `LibreVnaUsbBackend` 实现 `AcquisitionBackend` 的 S11 真机采集：复用 ISSUE-019 `LibreVnaUsbTransport`（USB 会话/控制包）与 ISSUE-020 `PacketStream`/`StrictSweepAssembler`（严格组装），完成 capability/open/configure（`SweepSettings` 发送/回读、applied config/axis 回读）、Port1÷Reference 复数 S11、真实 sweep UTC+monotonic 边界、cancel/close、connection_generation；第一道前严格比较 requested/applied（axis/config 超差 fail-closed 拒绝）；部分/坏 sweep 不输出 `FrequencySweep`（不分配正式 trace）；无硬件协议 simulator 覆盖（默认不枚举 USB）。价值：M04 门禁「单一真机路径、严格组装和硬件基准完成」的第三步——生产采集后端接线（ISSUE-022 双通道与 ISSUE-023 重连/基准的直接基座），并落实 ACQUISITION.md §2/§4/§5 与 ISSUE-020 复审剩余风险 2（NACK fail-closed 路由）与 P3-3（超时错误捕获策略）。

## 2. 范围（M04 L85–90 + 提示词）

1. `src/uav_gpr/acquisition/librevna/backend.py`（**单一新模块**）：
   - **协议编解码**：`LibreVnaDeviceInfo`（frozen dataclass）+ `decode_device_info(payload)`（protocol v14 `_DEVICE_INFO_FORMAT="<HBBBBcQQIIHhhIIBQBH"`，payload 补 2 字节后解包——参考既有行为）；`SweepSettings`（frozen dataclass）+ `encode_sweep_settings(...)`（`_SWEEP_SETTINGS_FORMAT="<QQHIhBHhH"`，config=(1<<2)|(1<<3)、stages_bitmap 仅 `S11_STAGES_BITMAP=0x1240`——真机已验证集合中 S11 项，双反射 0x1241 归 ISSUE-022）；
   - **结构化错误**：`LibreVnaNackError(LibreVnaStreamError)`（reason `nack`）、`LibreVnaProtocolError(LibreVnaStreamError)`（reason `protocol_error`）——沿用既有模式（DomainError + ErrorCode.INVALID_ARGUMENT + 类级 `_reason`，core ErrorCode 只读）；
   - **`LibreVnaUsbBackend(AcquisitionBackend)`**：`_do_open`（transport.open → REQUEST_DEVICE_INFO 回读 → SET_IDLE → 会话状态复位 → `Capabilities(device_id, (hh_s11,), fault_injection=False, gnss=False)`；失败尽力关闭 transport 后重抛）；`_do_configure`（设备能力校验（频率/IFBW/点数/功率范围 + 通道恰为 S11）→ int 量化 applied config → 第一道前 requested/applied 容差校验 → SET_IDLE → 重建帧流/组装器 → 发送一次 SWEEP_SETTINGS 等 ACK；命令失败 fail-closed：清空本地采集状态后重抛，基类保持 OPEN 可重配）；`_do_acquire`（pending 无——inline 组装器直接消费；USB read（短超时 tick）→ 帧流 → 包路由（VNA_DATAPOINT→`parse_vna_datapoint`→`StrictSweepAssembler.feed_datapoint`；NACK→fail-closed；ACK→unexpected 计数；其他→ignored 计数）→ 完成 sweep → S11=Port1÷Reference → 第一道前轴门禁 → `FrequencySweep`+`TraceMetadata`（真实 UTC+monotonic、connection_generation、trace_uid/index、raw hash、GNSS_MISSING））；`_do_close`（尽力写 SET_IDLE（不等待 ACK）+ transport.close()，幂等无泄漏）；`session_stats` 可观测（traces/dropped/incomplete/timeouts/duplicate/out_of_range/invalid/unexpected_acks/ignored_packets）。
2. 配置契约：applied = int/0.01 dBm 量化的 requested（`replace(config, ...)`）；`AppliedConfig(config, diff)` + `ConfigDiff.compute`；容差常量（频率/IFBW 1.0 Hz、功率 0.01 dBm、点数精确）；**第一道前轴门禁**：首个完成 sweep 的实际频率轴 vs applied `frequency_axis_hz`，任一频点超 `AXIS_TOLERANCE_HZ=1.0` → `BackendConfigRejectedError`（不分配 trace）。
3. 测试（tests/contract/test_librevna_backend.py，单一新测试文件）：脚本化 fake adapter（`UsbAdapter` Protocol 注入 `LibreVnaUsbTransport`），覆盖生命周期全路径、黄金编解码对拍、requested/applied、轴门禁、坏/半 sweep 不输出、NACK/超时/断开/取消/关闭、UTC+monotonic 元数据、统计。

## 3. 明确排除项（M04 L88–90 + 提示词 + 任务契约）

- 不实现 S22（`S11_S22_STAGES_BITMAP` 不发送、双通道配置拒绝）、校准、IFFT、HDF5、UI；不增加 TCP/SCPI/LibreVNA-GUI 第二路径；不自动启动 LibreVNA-GUI；
- 不改 `core/**`、`acquisition/backend.py`、`librevna/transport.py`、`librevna/stream.py`、`librevna/__init__.py`（全部只读消费）；不改两个参考仓库（本地副本只读）；
- 不分配 `trace_index`/不输出 `FrequencySweep` 给不完整/坏 sweep；不零填；不枚举 USB（AST 守卫）；
- **ISSUE-020 复审 P3-2（`ReceiverSlot` 构造校验）需要改 `stream.py`（本任务 out of scope）→ 显式记录为延后项**（见 §5 D11）；P3-3 以本后端显式捕获 `LibreVnaSweepTimeoutError` 并映射 `BackendTimeoutError` 的决策落实（无需改 stream.py）；P3-1/P3-5 为 ISSUE-020 文档措辞（out of scope，不随本任务改）；
- 不 commit/push/merge、不创建/切换分支；不进入 ISSUE-022；
- 不在 `src/uav_gpr/acquisition/librevna/backend.py`、`tests/contract/test_librevna_backend.py`、`docs/plans/2026-09-02-issue-021-librevna-backend.md`、`docs/issues/M04_LIBREVNA.md` 之外新增任何文件（确需拆分先停止向 captain 报告）。

## 4. 关联需求/ADR/文档与参考源哈希（迁移清单，REFERENCE_MIGRATION.md §5 模板）

```text
target issue/task:        ISSUE-021 S11 生产采集后端（M04，FR-003/004、ACQUISITION.md §1–§5）
reference repository:     钢筋仪软件开发（E:\钢筋仪软件开发；本机不可达，WSL 仅挂载 C/D）
                          + 本地只读副本 D:\博士任务\rebar-inspector（GitHub 克隆
                          z2362536803/rebar-inspector，来源记录见 t1 基线单 §2/ISSUE-019 计划 §4）
reference branch + HEAD:  manifest 冻结：feat/issue-16-pause-resume @
                          938875234a99b47d78cfec940671005b63e9d15c（ISSUE-001 冻结时点）
                          本地副本：main @ 7c522d2aebe6a835acb969e8012565715f64a238
reference worktree status:manifest 记录 worktree_dirty=True；本地副本 librevna 候选源
                          SHA-256 与 manifest 逐一相等（t1 实测 4/4，仅 CRLF 行尾差）
source file(s) + SHA256（t1 实测，本次实际阅读并采用）:
  librevna_protocol.py   6a41c4b11cfa8272c1687893a53219ea0465987a378700a0b74ab3973353d8ce
                         （DeviceInfo 解码 L243–270、SweepSettings 编码 L329–362、
                         stages_bitmap L93–97、datapoint_to_s11/parse_s11_point L386–437）
  librevna_usb.py        a73adc1c41f186ee51ab938c4c245809bf36dea0bc2769ad1115fffa99b22d87
                         （后端生命周期 L500–560、_do_acquire L640–666、
                         _wait_for_device_info/_send_command/_wait_for_ack L760–814、
                         _handle_packet L818–869、_take_sweep L873–929、_validate_config
                         L933–984、超时推导 L986–1003、settings 常量 L134–143）
  backend.py             f05da35cdee84604d43945da8c30854a289fb7de36a90a3c46c110cf8ab3340f
                         （参考后端能力/生命周期参考；UAV 侧以自有 AcquisitionBackend
                         基类契约为准——只取错误/能力语义参考）
  sweep_config.py        9877b7619747c07aeb7657ba3667322c2687396040bb00193afd5d8508c44801
                         （SweepSettings 构造/校验参考；UAV 侧由 MissionConfig 承接）
trusted behavior/contract（采用）:
  - DeviceInfo 解码逐字段与参考一致（payload 补 b"\x00\x00" 再解包）；
  - SweepSettings 编码逐字节与参考一致（start/stop/points/ifbw/cdbm/config=0x0C/
    stages_bitmap=0x1240/cdbm/dwell；cdbm 出现两次为参考格式，不"修复"）；
  - S11 = stage0 Port1 receiver ÷ stage0 reference receiver（复数除法；
    reference 幅度 0 / 缺槽位 / 非有限 → 无效点，UAV 侧由
    `datapoint_matches_plan` + `StrictSweepAssembler` 承接，backend 不再重复判定）；
  - 命令阶段：REQUEST_DEVICE_INFO→DEVICE_INFO、SET_IDLE/SWEEP_SETTINGS→ACK；
    NACK 立即失败；等待期间非目标 packet 不得静默丢弃（UAV 适配：inline
    组装器直接消费 datapoint，见 D5）；ACK 等待超时/DeviceInfo 超时 → 结构化错误；
  - acquire 循环：先消费命令期残余，再短超时读 USB（50ms tick），feed 帧流，
    逐包路由；sweep 总体 deadline（覆盖静默设备）+ 组装器超时（active sweep）；
  - 每道边界：首个有效 point 被主机接收时刻为道起点（参考 L18 语义）；
    trace_index 仅完整 sweep 递增；部分/坏 sweep 只统计不输出；
  - 超时推导：expected_s = n_points × n_stages(1) / ifbw；
    sweep_timeout = max(2.0, expected_s × 5.0)（可注入覆盖）；
  - close 尽力 SET_IDLE 后释放资源；幂等；异常路径也释放；
  - configure 失败 fail-closed（清空本地采集状态，保持打开可重配）。
excluded behavior（排除）:
  - S22/双反射（S11_S22_STAGES_BITMAP=0x1241、parse_s11_s22_point）→ ISSUE-022；
  - 暂停/恢复（_do_pause/_do_resume、SET_IDLE 边界清理、_enter_fail_closed 复用）→
    ISSUE-023（ISSUE-021 的 AcquisitionBackend 基类无 pause hook）；
  - 断线重连/退避/配置重确认 → ISSUE-023（本任务仅映射断开为
    BackendDisconnectedError 并递增 connection_generation）；
  - take_completed 双步/pending 队列 → UAV inline 组装器（D5）；
  - 真机数值/吞吐基准 → ISSUE-023；
  - UAV-GPR 全部旧采集代码（含 legacy/continuous 双路径）。
new target module(s):     src/uav_gpr/acquisition/librevna/backend.py（唯一新模块）
UAV-specific adaptations:
  - 错误边界统一：生命周期/配置/超时/断开/取消 → BackendError 家族
    （BackendConfigRejectedError/BackendTimeoutError/BackendDisconnectedError/
    BackendCancelledError/BackendClosedError，与 AcquisitionBackend 契约和
    controller 的 except 分支一致）；协议/设备语义错误（NACK/坏 payload）→
    LibreVnaNackError/LibreVnaProtocolError（DomainError 结构化，controller
    通用 fail-closed）；
  - applied config = int 量化 requested（频率/IFBW 整数 Hz、功率 0.01 dBm），
    ConfigDiff 记录实际变化；第一道前轴门禁落实 ACQUISITION.md §4「实际轴与
    契约超差在第一道前拒绝」；
  - 时间：注入 core Clock（utc_now/monotonic_ns）+ 注入 float mono clock
    （组装器超时与整体 deadline 共用，确定性测试无 sleep）；道起点 = 最后一个
    plan-valid point 0 被主机接收时刻（utc + monotonic 同时记录）；
  - 功率：发送 config.power_dbm（量化），不再固定 -10 dBm（MissionConfig 契约
    字段，参考固定功率的设置不适用于 UAV 任务契约）；
  - close 的 SET_IDLE 为 fire-and-forget（写而不等 ACK）——close 必须快速幂等，
    设备 idle 状态确认不是 close 的职责（open/configure 命令阶段已确认）；
  - stages_bitmap 校验仅允许 0x1240（S11 真机验证值；0x1241 归 ISSUE-022）。
tests/golden fixtures migrated:
  - DeviceInfo 黄金 payload（结构 <HBBBBcQQIIHhhIIBQBH，参考 test 构造范式）与
    解码字段断言（protocol=14/firmware=1.2.3/min-max 频率等）；
  - SweepSettings 黄金字节（本任务用参考 encode 语义独立计算并硬编码 hex，
    provenance：librevna_protocol.py encode_sweep_settings L329–362；
    参考测试 test_librevna_protocol.py 未含 SweepSettings 固定向量，本任务补充）；
  - VNADatapoint BLOCKED 布局构造范式（头 + reals(ref,port1) + imags +
    descs[0x10,0x01]）与 S11=port1/ref 数值断言（参考 test_librevna_usb_backend.py
    `_s11_point_payload` L57–66，SHA-256 2d4db31333ef58d586b0f024531ae6f593ea8c38be351708792306272a43bc38）；
  - 脚本化 fake transport 范式（参考 test_librevna_usb_backend.py `_ScriptedTransport`）：
    UAV 侧实现为 UsbAdapter Protocol 脚本（chunks: bytes|异常），经
    LibreVnaUsbTransport 注入（UAV 的 adapter 注入边界是 ISSUE-019 冻结契约）。
new tests added:          tests/contract/test_librevna_backend.py（新契约测试，失败测试优先）
numeric or performance comparison: 不适用——无真机、无性能声明（ISSUE-023 负责硬件基准；
                          参考历史数字不得写成新结果）
license/provenance review:参考项目为内部 proprietary；本迁移为契约提取与适配（行为级），
                          新实现为独立代码（非逐行复制），docstring 声明来源与既有协议
                          行为；未复制大模块。
```

## 5. 设计决策（ADR 级，含备选与理由）

| # | 决策 | 理由 | 备选（否决理由） |
|---|---|---|---|
| D1 | 协议编解码（DeviceInfo/SweepSettings/错误家族）全部落在唯一新模块 `backend.py` | t2 契约 inScope 仅允许新增 backend.py；transport.py/stream.py 只读消费；ISSUE-019 排除项明确把这两块留给 ISSUE-021 | 扩展 transport.py（改冻结契约，out of scope）；新建第三模块（超出 inScope，需先报告） |
| D2 | 后端经 `transport.PacketStream`（帧层）直接路由包：VNA_DATAPOINT→`parse_vna_datapoint`+`StrictSweepAssembler`；NACK 可见并 fail-closed | ISSUE-020 复审剩余风险 2 明确「NACK 路由是 ISSUE-021 backend 职责，接线时须补 NACK 中断测试」；`LibreVnaPacketStream` 把控制包吞进 ignored 计数，后端不可见 | 使用 `LibreVnaPacketStream.feed`（NACK 不可见，无法 fail-closed，违反剩余风险 2） |
| D3 | 错误映射表（见 §4 UAV-specific adaptations）：命令超时/DeviceInfo 超时/sweep 超时→`BackendTimeoutError`；能力/通道/容差/轴门禁→`BackendConfigRejectedError`；断开→`BackendDisconnectedError`（+generation 递增）；cancel/close→基类 `_raise_interrupted`；NACK/坏 payload→`LibreVnaNackError`/`LibreVnaProtocolError` | controller 对 BackendDisconnected/Cancelled/Closed 有专门分支；其余 DomainError 通用 fail-closed；与 AcquisitionBackend 契约一致 | 全部抛 LibreVNA 家族错误（controller 特殊分支失效）；全部抛 BackendError（丢失协议语义区分） |
| D4 | applied config = int/0.01 dBm 量化 requested；configure 时 requested/applied 容差校验 + 设备能力范围校验；**第一道前轴门禁**（首个完成 sweep 实际轴 vs applied linspace，1.0 Hz） | ACQUISITION.md §4「频率轴以设备实际输出/确认值为准；实际轴与任务契约超差在第一道前拒绝」；applied 回读对 protocol v14 = 发送值量化 + 实际轴观测 | 仅量化不校验（「超差拒绝」验收落空）；每道都验轴（ISSUE-023 真机再评估，首道门禁已满足验收） |
| D5 | 无 pending 队列：命令等待期（open/configure）到达的 datapoint 直接喂 inline 组装器（configure 后组装器已存在）；configure 前到达的 datapoint 计 ignored | UAV `StrictSweepAssembler` 是 inline 语义（ISSUE-020 D3：无 pending/take 双步），SWEEP_SETTINGS ACK 同批首批点直接入组装器不丢 | 参考式 pending deque（UAV 组装器无 take_completed，pending 无消费面；引入额外状态） |
| D6 | 时间契约：道起点 = 最后一个 plan-valid point 0 被主机接收的 utc_now/monotonic_ns；完成时刻 = 组装完成时；midpoint = 起点+跨度/2；trace 0 无 actual/schedule error，后续道按单调起点差计算 | 对齐参考「首个有效 point 被主机接收的 UTC 时间」与 TraceMetadata/SimulatedBackend 既有模式；start≤midpoint≤finish 两域均成立 | 用完成时刻当起点（伪造扫描时长）；用 assembler.started_at（float 域，缺 UTC） |
| D7 | 超时双保险：组装器 timeout_ms（n_points/ifbw 推导，注入 mono clock）+ 后端整体 sweep deadline（覆盖静默设备）+ 调用方 timeout_s 上限；全部经注入 clock 确定性测试 | 组装器 check_timeout 保持 timeouts⊆incomplete⊆dropped 统计不变量；整体 deadline 对齐参考（无 point 0 时也会超时）；timeout_s 语义与基类/SimulatedBackend 一致 | 仅组装器超时（静默设备永不超时）；仅整体 deadline（组装器 timeouts 统计恒 0） |
| D8 | close 尽力 fire-and-forget 写 SET_IDLE 后 transport.close()；写失败静默；幂等无泄漏；_do_open 失败尽力 close 后重抛 | 参考 close 语义（尽力 SET_IDLE + 释放；异常路径也释放）；不等待 ACK 使 close 快速幂等（close 不是配置确认点） | 等 ACK（测试/真实断开场景下 close 阻塞 2s，违背「close/cancel 无泄漏」的快速性） |
| D9 | 能力固定单通道 `(hh_s11,)`；stages_bitmap 仅 0x1240；S22/双通道/0x1241 一律拒绝 | ISSUE-021 是 S11 唯一路径；「禁止第二条路径」与 ISSUE-022 边界清晰 | 预支持 0x1241（越界进 ISSUE-022；未验证组合不得下发——参考 _validate_stages_bitmap 原则） |
| D10 | 功率发送 config.power_dbm（量化 0.01 dBm，设备 min/max 范围校验） | MissionConfig.power_dbm 是任务契约字段；「requested/applied 严格比较」含功率；参考固定 -10 dBm 是参考项目内部设置 | 固定 -10 dBm（与任务契约不符，diff 恒含 power 变化） |
| D11 | ISSUE-020 复审 P3-2（ReceiverSlot stage∈0..7/mask≠0 校验）**显式延后**：需要改 stream.py（out of scope）；P3-3 以「backend 显式捕获 LibreVnaSweepTimeoutError 并映射 BackendTimeoutError」落实（不改 stream.py，catch 兼容在消费面完成）；P3-1/P3-5 为 ISSUE-020 文档措辞，不随本任务改 | 任务契约 changedPaths 必须恰好等于 4 个 inScope 路径；stream.py 只读消费 | 改 stream.py（违反 inScope/changedPaths 门禁，需先报告 captain） |

## 6. 文件改动（inScope 精确路径，changedPaths 必须与此逐一相等）

| 路径 | 内容 |
|---|---|
| `src/uav_gpr/acquisition/librevna/backend.py` | 新模块：`LibreVnaDeviceInfo`/`decode_device_info`、`SweepSettings`/`encode_sweep_settings`/`S11_STAGES_BITMAP`、`LibreVnaNackError`/`LibreVnaProtocolError`、`LibreVnaUsbSettings`、`LibreVnaUsbBackend`（四 hook + 包路由 + 轴门禁 + 元数据 + session_stats） |
| `tests/contract/test_librevna_backend.py` | 新契约测试（失败测试优先；脚本化 fake adapter；黄金对拍/生命周期/配置/acquire/错误/元数据/统计/回归） |
| `docs/plans/2026-09-02-issue-021-librevna-backend.md` | 本计划文档（t2 先落盘；执行日志第 10 节随执行追加） |
| `docs/issues/M04_LIBREVNA.md` | 仅 ISSUE-021 状态行：`Planned → In progress → Review`（勿动其它条目） |

## 7. 测试矩阵（提示词必测项 → 测试名，与实现逐一对应）

| 必测项 | 测试 | 手段 |
|---|---|---|
| 黄金编解码对拍 | `test_golden_device_info_decode`、`test_golden_sweep_settings_encode`、`test_sweep_settings_validation` | 参考语义独立计算的固定字节向量（第 4 节 provenance） |
| 生命周期 open | `test_open_requests_device_info_and_set_idle`、`test_open_failure_closes_transport`、`test_open_device_info_timeout`、`test_reopen_after_close` | 脚本化 fake adapter（chunks: bytes/异常） |
| 生命周期 configure | `test_configure_sends_set_idle_and_sweep_settings`、`test_configure_applied_quantization_and_diff`、`test_configure_rejects_unsupported_channels`、`test_configure_rejects_out_of_device_range`、`test_configure_nack_fail_closed`、`test_configure_ack_timeout_fail_closed`、`test_reconfigure_resets_trace_index` | 命令脚本 + 状态断言（基类 state） |
| acquire 正常 | `test_acquire_complete_sweep_values_and_metadata`、`test_acquire_two_sweeps_trace_index_interval`、`test_acquire_across_read_boundary`、`test_acquire_datapoints_arriving_with_ack` | 多 chunk 脚本；数值/形状/元数据断言 |
| 第一道前轴门禁 | `test_first_sweep_axis_mismatch_rejected`、`test_axis_mismatch_no_trace_allocated` | 设备返回偏离轴 → BackendConfigRejectedError，trace_index 恒 0 |
| 不完整/坏 sweep | `test_incomplete_sweep_timeout_no_trace`、`test_duplicate_point_no_fake_trace`、`test_out_of_range_point_no_fake_trace`、`test_zero_reference_no_fake_trace`、`test_malformed_datapoint_fail_closed` | 统计断言（dropped/incomplete/invalid）+ 无输出断言 |
| NACK/协议错误 | `test_nack_during_configure`、`test_nack_during_acquire_fails_closed`、`test_unexpected_ack_and_ignored_packets_stats` | LibreVnaNackError/LibreVnaProtocolError + stats |
| 超时 | `test_sweep_timeout_no_data_no_trace`、`test_sweep_timeout_partial_sweep_stats`、`test_acquire_caller_timeout_cap` | 注入 mono clock（无 sleep） |
| 取消/关闭 | `test_cancel_interrupts_acquire`、`test_close_interrupts_acquire`、`test_close_idempotent_no_leak`、`test_close_writes_set_idle_best_effort` | 事件/状态驱动（无 sleep） |
| 断开/代数 | `test_disconnect_during_acquire_maps_and_bumps_generation` | 脚本抛 LibreVnaDisconnectedError → BackendDisconnectedError + generation+1 |
| 元数据/身份 | `test_metadata_utc_monotonic_ordered`、`test_metadata_trace_identity_and_hash`、`test_metadata_gnss_missing_degraded`、`test_connection_generation_in_metadata` | TraceMetadata 字段断言 |
| 回归 | ISSUE-019 50 / ISSUE-020 61 / ISSUE-015 28 / ISSUE-017 88；全量 verify.py 853 passed/1 deselected 基线；ruff/mypy/import/`git diff --check` | — |

## 8. 性能/数据风险

- 无性能声明：无真机基准（ISSUE-023 负责）；不把参考历史速度写成新结果。
- 有界性：帧缓冲继承 transport.PacketStream（≤4096+头）；无 pending 队列（D5）；组装器半道缓冲 ≤ expected_points（构造冻结）；命令等待只处理单次 read 的包，无长度派生分配路径。
- 数据风险：不落盘、不联网、不修改 raw；`FrequencySweep`/`TraceMetadata` 全走 core 冻结契约（不可变、UTC+monotonic 有序、raw hash）；失败路径不分配 trace。
- 线程风险：本模块不创建线程（由 controller 工作线程调用，AGENTS.md §7/ACQUISITION.md §1）；取消经短读超时 tick + 事件检查；不引入固定 sleep。
- 行为风险：VNA_DATAPOINT 跳 CRC 为参考既有协议行为（结构/点序/轴门禁兜底，不"修复"）；stages_bitmap 仅 0x1240；首道轴容差 1.0 Hz 为保守门禁（真机复核归 ISSUE-023，超差 fail-closed 是安全方向）。

## 9. 完成定义与回退

- 完成定义（全部满足才可登记 completed）：验收标准（M04 L92–96 + 任务契约）逐条 PASS；定向测试红灯→绿灯记录于执行日志；全量 verify.py + ruff + mypy + import + `git diff --check` 全绿；`git status` 仅 4 个 inScope 路径改动（changedPaths 与 inScope 逐一相等）+ t1 基线单（t1 交付物，不计入 t2 inScope）；M04 状态行更新为 Review；不 commit/push/merge、不创建分支。
- 回退方式：实现为新增文件（backend.py + 测试 + 两份文档），只修改 M04 状态行一行；异常时删除未登记文件并还原 M04 状态行即可回到 `main @ def2c28` 干净基线；无破坏性操作。

## 10. 执行日志（随执行追加）

```text
[2026-09-02] t2 开工：claim t2（attempt 3689c04d-e7f5-44ea-95f5-f4f4a18ceafe）→ in_progress。
[2026-09-02] 参考审计（只读）：librevna_protocol.py（DeviceInfo/SweepSettings 编解码、
              stages_bitmap、S11 语义）、librevna_usb.py（后端生命周期/acquire 循环/
              命令等待/包路由/_take_sweep/_validate_config/超时推导/close）、
              sweep_config.py、参考测试 _ScriptedTransport/_s11_point_payload 范式；
              源哈希 t1 实测 4/4 相等（第 4 节）。
[2026-09-02] 计划文档落盘（本文件第 1–9 节）。
[2026-09-02] 失败测试优先（红灯，实现前）：
              $ ./.venv/Scripts/python.exe -m pytest tests/contract/test_librevna_backend.py -q
              -> ERROR: ModuleNotFoundError: No module named
                 'uav_gpr.acquisition.librevna.backend'（collection 1 error）——红灯成立。
[2026-09-02] 最小实现：src/uav_gpr/acquisition/librevna/backend.py 落盘
              （DeviceInfo/SweepSettings 编解码、LibreVnaNackError/LibreVnaProtocolError、
               LibreVnaUsbSettings、LibreVnaUsbBackend 四 hook + 包路由 + 轴门禁 +
               元数据 + session_stats）。
[2026-09-02] 定向测试（绿灯）：
              $ ./.venv/Scripts/python.exe -m pytest tests/contract/test_librevna_backend.py -q
              -> 39 passed in 0.15s——绿灯成立。
              （过程：首轮 32 passed/7 failed——①测试侧 ScriptedAdapter 把
              UsbAdapter 契约的 is_open 属性实现成方法（transport.open 读到
              方法对象恒真值，跳过 open），改为 @property 后通过；②适配器 open
              未复位 closed 导致 reopen 失败；③失败测试数据笔误（100.4e6 是
              整数值，量化用例改 100_000_000.4）；④reconfigure 用例第二道轴未
              随 stop=190e6 更新；⑤cancel/close 用例改用真实 time.monotonic +
              长 sweep_timeout（虚拟时钟会让 acquire 先超时退出）；⑥实现侧
              StrictSweepAssembler.check_timeout 按"时钟单位"比较（ISSUE-020
              测试用毫秒时钟），后端注入秒时钟须传秒值 timeout_ms；⑦acquire
              循环补"上一批 read 完成的多道先返回"的 pending 弹出。随后
              ruff/mypy 修复（RUF046/RUF005/E501/RUF100/F841、JsonValue
              上下文类型）后仍 39 全绿。）
[2026-09-02] 依赖回归：
              $ ./.venv/Scripts/python.exe -m pytest tests/contract/test_librevna_transport.py \
                  tests/contract/test_librevna_stream.py \
                  tests/contract/test_acquisition_backend.py \
                  tests/contract/test_acquisition_controller.py \
                  tests/contract/test_librevna_backend.py -q
              -> 266 passed in 4.36s（ISSUE-019：50 + ISSUE-020：61 + ISSUE-015：28
                 + ISSUE-017：88 + ISSUE-021：39）。
[2026-09-02] 静态检查：
              $ ./.venv/Scripts/python.exe -m ruff check src tests
              -> All checks passed!
              $ ./.venv/Scripts/python.exe -m mypy src
              -> Success: no issues found in 42 source files。
[2026-09-02] 门禁（全量，tools/quality/verify.py）：
              $ ./.venv/Scripts/python.exe tools/quality/verify.py
              -> exit 0（[quality] all gates passed；pytest/ruff/mypy/import 全过）
              $ python3 -m pytest -m "not hardware and not slow" -q   # 显式复跑计数
              -> 892 passed, 1 deselected in 135.08s (0:02:15)
                 （853 基线 + 39 新后端测试）；ruff All checks passed!；
                 mypy Success: no issues found in 42 source files；
                 package import ok。
[2026-09-02] 工作树/交付检查：git diff --check clean；git status --porcelain=v1 -b
              仅 5 条目：4 个 t2 inScope 路径（M04 1 行 + 3 个新文件）+ t1 基线单
              （t1 交付物，不计入 t2 inScope）；无缓存/日志/实测数据残留
              （.pytest_cache/.mypy_cache/.ruff_cache git check-ignore 命中）。
[2026-09-02] M04 状态行：Planned → In progress → Review（最终态，2026-09-02）。
```

> 后续记录：本计划的执行日志只记录事实与数字；t3 复审报告独立输出。
