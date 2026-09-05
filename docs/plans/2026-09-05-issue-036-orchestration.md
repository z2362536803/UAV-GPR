# ISSUE-036 实施计划：完整处理编排、revision 与安全回放

日期：2026-09-05
执行器：AgentTeams `uav-gpr-issue-036-orchestration` 成员 engineer（任务 t2）
基线件：[docs/reports/ISSUE_036_BASELINE_CONFIRMATION.md](../reports/ISSUE_036_BASELINE_CONFIRMATION.md)（main @ `487f9ad`，与 origin/main 同步 ahead 0；工作树干净；九项依赖 011/018/029-035 Done 三重实证齐全；门禁基线 **1396 passed / 4 deselected** + ruff + mypy(55) + import 全绿实测复现）
目标 Issue：ISSUE-036（`docs/issues/M06_CALIBRATION_PROCESSING.md` L338–373）；约束文档：`AGENTS.md` §3/§6/§9/§10/§11、`docs/PROCESSING.md` §1/§2/§7/§9、`docs/CALIBRATION.md` §1/§5/§6/§7、`docs/DATA_FORMAT.md` §2/§3/§3.1/§5、t1 基线确认单 §3。

## 1. 目标与用户价值

在 `application` 层交付**唯一处理编排**（`processing_orchestrator.py`）：把已交付的六个独立 stage（032 OSL、033 air background、030 bandpass、031 IFFT、034 Dewow、035 Flat）按 `docs/PROCESSING.md` §2 冻结顺序串成一条不可旁路的链——可选 OSL → **保存 calibrated snapshot** → 可选 air background → 可选 bandpass → IFFT/`time_base` → 可选 Dewow → 可选 Flat/`time_processed`——并在此之上提供三条本里程碑收口能力：

1. **两条严格入口**：fresh raw（history 必须为空）与 safe replay reuse（严格相同 profile/provenance 才复用 calibrated 快照，绝不二次校准）；
2. **processing revision / cancellation**：参数变更产生新 revision，过期 worker 结果按 revision 丢弃，取消路径幂等——**丢弃只影响显示/派生结果，raw 存储字节不受影响**；
3. **受控 storage 附加接口**：把派生数据与 history 落回 ground `.rcscan` 的既有 optional 组，全程不触碰 `/frequency/raw` 与 trace-major 必需列。

它是 M06 的收尾条目：完成后"黄金样本、provenance、raw 不变与安全回放"门禁（README §4）具备闭合条件。

## 2. 范围（任务契约 inScope = 4 个精确路径，changedPaths 与此逐一相等）

1. `src/uav_gpr/application/processing_orchestrator.py`（唯一实现模块：配置模型 + 双入口 + revision/cancel + 受控派生写门面；所有 stage/core/storage 能力 import 复用，零复制）
2. `tests/contract/test_processing_orchestrator.py`（唯一测试文件：关键组合矩阵、二次 OSL/背景拒绝、错 profile、非空 raw history、revision 竞争、保存-加载-回放对拍、raw 字节不变、取消/错误路径）
3. `docs/plans/2026-09-05-issue-036-orchestration.md`（本计划文档，含 D/M/X 决策记录与执行日志）
4. `docs/issues/M06_CALIBRATION_PROCESSING.md`（仅 ISSUE-036 状态行 L340：`Planned → In progress → Review`，勿动其他条目）

注：沿用 030–035 先例——测试直接 import `uav_gpr.application.processing_orchestrator`，**不改** `application/__init__.py`、`application/ground/__init__.py`（不在 inScope），模块经完整路径导入。

## 3. 明确排除项（M06 L325 + 提示词 + 任务契约 nonGoals）

- 不实现 UI（`docs/UI.md` 属 M09）、不实现**零时（time zero）**与**连续背景（continuous background）**stage（PROCESSING.md §2 标注 future，编排链上不存在其槽位）。
- 不改 `src/uav_gpr/core/**`（`ProcessingHistory`/`ProcessingRecord`/`TimeDomainScan`/`DataDomain`/`ErrorCode` 只读消费；不新增错误码，复用 `INVALID_ARGUMENT`/`PROCESSING_DOMAIN_MISMATCH`/`SHAPE_MISMATCH`/`AXIS_MISMATCH`/`DTYPE_MISMATCH`）。
- 不改 `src/uav_gpr/processing/**`、`src/uav_gpr/calibration/**`、`src/uav_gpr/storage/**`、各 `__init__.py`：**零字节**。特别是 `.rcscan` v2 物理 schema（`storage/rcscan_v2.dataset_contracts`）**不做任何扩展**——schema 变更需 Proposed ADR（AGENTS.md §11、README §3.7），超出本 inScope（见 D6）。
- 不新增第三方依赖（h5py/numpy 均为 storage 层既有依赖）。**如实声明实现形态（t4/F2 修正）**：本模块直接使用 `h5py` 打开暂存副本写派生组（`import h5py`、`h5py.File(..., "r+")`、`h5py.string_dtype`），并未把 HDF5 写面收敛进 storage 公开接口——因为 ISSUE-010 writer 的公开面只有 raw 路径，扩展它属于冻结契约变更且不在本 Issue inScope。合法性依据：AGENTS.md §9 允许 `application -> storage` 方向，h5py 不是新增依赖；所有物理参数一律取自 `storage.rcscan_v2.dataset_contracts` 权威常量（无硬编码 dtype/maxshape/chunks），严格复核复用 `storage.rcscan_reader`。把该写面提升为 storage 公开 API 属后续 ADR 议题（记入 §9）。详见 D5。
- 不重写 032/033 的 safe-reuse 判定与二次校准守卫（委托权威，见 D3/D4）；不新建平行数据模型。
- 不改 `docs/reports/**`、`docs/PROCESSING.md`、`docs/CALIBRATION.md`、`docs/DATA_FORMAT.md`、`docs/TESTING.md`、`docs/adr/**`、`tools/**`。
- 不 commit / push / merge；完成后停止，不进入 ISSUE-037。

## 4. 设计决策（D1–D9）

- **D1 单一编排入口与链序**：`run_processing(request: ProcessingRequest) -> ProcessedMission` 是产品代码中唯一的完整链实现；链序固定为 `osl_calibration`（可选）→ calibrated snapshot 物化 → `air_background_subtraction`（可选）→ `frequency_bandpass`（可选）→ `frequency_to_time_ifft`（**必选**，产出 `time_base`）→ `dewow`（可选）→ `flat_reflection_filter`（可选）。每个开关只决定"调用/跳过对应 stage"，绝不重排、绝不合并数学实现（CALIBRATION.md §1 四概念不得混为一函数；REFERENCE_MIGRATION 要求各 stage 保持独立）。IFFT 必选的理由：Issue 验收"time_base 总是 IFFT 基础"，且没有 time 域就没有 B-scan 产物。追加一律走 `ProcessingHistory.append`，core `_ALLOWED_TRANSITIONS` + `_START_DOMAINS` + 同名单次性 + provenance continuity 全部作为兜底闸门（编排层不放宽一档）。
- **D2 配置模型 = 可哈希的 ProcessingProfile**：`ProcessingProfile(osl: OslCalibrationSet|None, background: AirBackgroundSelection|None, bandpass_edges_hz: Sequence[float]|None, ifft_oversampling:int, dewow_window_s: float|None, flat_window_traces: int|None)` 加 `profile_digest()`（SHA-256 over canonical JSON-safe description）。描述符**只含内容摘要**：OSL 用 `osl_set_digest` + `osl_provenance_of`-形态逐项 `{channel_id,s_parameter,profile_id,content_sha256}`；background 用 `background_reference_digest` + `reference_id` + `calibration_profile_id`；bandpass/dewow/flat/ifft 用数值参数。⇒ 同一引用被重新求解（同 ID 不同内容）必然换 digest，"错 profile"不可能靠 ID 蒙过。digest 同时充当 revision 身份的一部分（D7）。
- **D3 fresh raw 入口（history 必须为空）**：`ProcessingRequest.entry is ENTRY_FRESH_RAW` 要求 `history == ProcessingHistory()`（空）且输入容器为 `FrequencySweep/FrequencyScan`；非空 history ⇒ `DomainError(INVALID_ARGUMENT)`（context 含 `records`、首末域）；非频域容器 ⇒ `TypeError` fail-closed。空 history 下 `_input_domain_of` 给 `FREQUENCY_RAW`，与 core "第一项输入域必须是 frequency_raw" 天然一致。
- **D4 safe replay reuse 入口（严格 profile/provenance，绝不二次校准）**：`ENTRY_SAFE_REPLAY_REUSE` 接收一个由上一次编排产出的 `CalibratedSnapshot{source, history, calibration_digest, provenance}`，编排层复用前**必须**通过两道既有权威：① `processing.osl_calibration.check_safe_reuse(history, calibration)`（history 末域 = `frequency_calibrated`、有序 provenance 与请求的 live `OslCalibrationSet` 逐字段相等、set digest 相等、profile-id-field semantics 相等；legacy 无 provenance 一律拒）——只有当本次 profile 仍启用 OSL 时该项才有比对对象，未启用 OSL 时改用 ②；② `processing.background_subtraction.check_safe_reuse(...)`（若启用背景）。任何 `mismatches` ⇒ `DomainError(PROCESSING_DOMAIN_MISMATCH)` 并把 field-level 清单原样放进 context，**不退化为静默重算**。复用后从快照的 history 继续挂 bandpass/IFFT/Dewow/Flat。**关键语义澄清（t1 §3.1）**：reuse 不是"以 calibrated 为新 history 起点"（core `_START_DOMAINS` 明令禁止，需未来 provenance anchor）——它只是"跳过 OSL 计算"，其 history 仍是当初从 raw 起步的那条完整链；因此快照必须携带原始 history（`from_dict/to_dict` 可序列化往返）+ 数组本体，缺一即不可复用。二次 OSL/二次背景即使绕过编排层，也会被 stage 自身的 raw-only 门 + core 唯一性双重拒绝（编排测试专列此点，证明编排层没有开后门）。
- **D5 受控 storage 附加接口（不越权改 raw）**：`attach_derived_result(path, result)` 与 `DerivedAttachmentWriter(path).write(payload)` 是唯一落盘通道。硬约束由 writer 自身强制而非信任调用方：⓪ **门卫（t4/F1 补全）**——`inspect()` 先取 `probe_rcscan_v2`，`file_role` 非 `GROUND` 或 `lifecycle_state ∉ {finalized, recovered}` ⇒ `DerivedAttachmentError(INVALID_ARGUMENT)`（context 带 `file_role` / `lifecycle_state`）；依据 AGENTS.md §6 空地职责与 DATA_FORMAT §6（校准/处理/派生数据是**地面端**能力），而 `writing` partial 由活的 incremental writer 持有句柄，替换文件会让后续 append 落入旧 inode、终态 rename 语义漂移（POSIX 无 OS 锁兜底），故必须在暂存之前拒绝而非依赖平台行为；① 白名单——只允许 ISSUE-008 契约中 `optional=True` 的派生路径（`/axes/time_base_s`、`/axes/time_processed_s`、`/frequency/calibrated`、`/time_base/data` + `/time_base/history_json`、`/time_processed/data` + `/time_processed/history_json`），任何必需数据集（含 `/frequency/raw` 与 trace-major 列）出现在载荷即拒；② 物理参数全部取自 `storage.rcscan_v2.dataset_contracts(channel_count, frequency_points, time_points)`，绝不硬编码 dtype/maxshape/chunks；③ 先做 preflight（秩/轴长/行数/history 可 canonical 化），再 `shutil.copy2` 到 `*.derived.tmp` 暂存副本上追加（HDF5 写面为本模块内直接使用 `h5py` 的受控实现，见 §3 如实声明），随后用 **011 严格 reader 复核暂存文件**，只有通过才 `replace()` 原子发布——失败即删暂存、原文件字节不变（返回 `published=False, refused_reason="strict_validation"`，不抛裸异常、不留半成品）；④ 写入前后各做一次 `raw_column_fingerprint`（`/frequency/raw` + 全部必需行列 + 形状），`assert_raw_bytes_unchanged` 公开可测且被 attach 流程内部使用；⑤ 行数必须等于 `committed_record_count`（派生数据绝不可能暗示未提交的道）。`AttachmentReport.to_dict()` 同时序列化 `published` 与 `refused_reason`（t4/F4：可观测拒绝必须对日志/诊断面可见）。
- **D6 v2 schema 缺口的实测结论与处置（t1 §3.3(i) 续）**：实现期实测确认两件事。(a) v2 契约确实**没有** `/frequency/history_json`（`rcscan_v2.py` 仅声明 time_base/time_processed 两处），因此 calibrated 阶段的频域历史以 **mission 属性 `frequency_history_json`** 承载（非数据集、不改 schema），全链 records 仍完整落在 `/time_base/history_json`；同时写 `derived_profile_digest` / `derived_writer_version` 供审计。(b) 更硬的约束来自 011 reader：它用 `dataset_contracts(channels, freq_points)` 的**默认口径**（`time_points = frequency_points`）校验"存在即核"的可选组，而 IFFT 归档的是完整物理时窗的插值网格（实测：33 频点 ⇒ 1024 时间样本）。⇒ **真实派生数组长度与 reader 默认契约不可同时满足**；把网格缩到与频点数相等在 core 模型里也不可行（频率轴必须严格递增，无法构造等长均匀轴）。本 Issue 据此**拒绝在编排层裁剪 stage 输出**（那会静默改写 provenance 所声称的产物，并绕过 PROCESSING.md §8"数值优化不得改变 dtype/轴/边界结果而无版本升级"的纪律），改为：`archive_to_schema_grid` 只做**幂等闸**（网格非 2 的幂即 `SHAPE_MISMATCH` fail-closed，符合则原样返回同一对象），并把"宽网格 ⇒ 011 拒绝 ⇒ 附件不发布"作为**受控、可观测、原始数据零改动**的结果如实登记（测试 `test_attachment_refuses_a_grid_the_frozen_reader_cannot_validate` 钉死该行为）。派生写面的成功路径由 `test_published_attachment_round_trips_and_is_replaceable` 以契约一致网格覆盖（保存 → 严格 reader 加载 → 重放 → 再附着 → 逐字节对拍）。**这是 schema 缺口而非实现缺陷**：真正打通需要扩展 v2 可选组以携带真实 `time_points`（或让 reader 接受按载荷推导的 `time_points`），属 ADR + schema 变更，记入 §9 剩余风险首项。
- **D7 processing revision**：`ProcessingRevision(value: int)` 单调正整数（≥1）；`ProcessingController(initial_revision=N)` 提供 `begin(revision)` / `cancel(revision)` / `publish(result)` / `accepts(revision)` / `snapshot()`，`ProcessingToken.checkpoint(stage_name)` 是 worker 侧唯一检查点。规则：`begin` 要求严格新 revision（或当前 revision 上无人在跑的重试），checkpoint 发现 `revision != current` 或已取消 ⇒ 抛 `StaleProcessingResult`（可预期控制流异常，携带 `revision/current_revision/cancelled/stage_name` 与 `to_dict()`），过期结果在**任何派生落盘之前**被丢弃；`publish` 只接受最新可见者——旧 revision 迟到返回既有可见结果并计 `stale_publications`，绝不覆盖更新；同 revision 同一对象重复 publish 幂等。`snapshot()` 返回有界 `VisibleState{current_revision, visible_revision, cancelled_revisions, accepted, dropped, stale_publications}`（AGENTS.md §7 可观测 + 无界增长禁止）。取消后立即可开新 revision。
- **D8 结果对象**：`ProcessedMission{profile_digest, entry, revision, source_input, input_container_before_ifft, history, time_base, time_processed, final_domain, calibrated_snapshot, executed_utc, applied_stages, reused_calibrated, display_view}`——`time_base` 恒存在（IFFT 必选），`time_processed` 仅在 Dewow/Flat 至少其一开启时存在，`final_domain` 与 history 末域一致（属性 `domain_of_history_last` 供对拍断言）。`derived_payload()` 产出 D5 的规范化写入视图（含 `/time_base/history_json` 全链 records 与频域历史字符串），`history_json()` / `to_dict()` 提供审计面。
- **D9 确定性与线程边界**：所有时间戳经注入 `Clock`/`executed_utc`（各 stage apply 原生支持）；同一 raw + 同一 profile 两次编排 ⇒ history records 与全部派生数组逐字节相等；reuse 入口上报**请求 profile** 的 digest，故与等价的 fresh 运行共享身份（专测断言）。编排本身不起线程、不 sleep、不轮询——revision 竞争的测试用显式交错调用模拟并发 worker，符合"UI 测试不用 sleep 猜时序"的同源纪律。生产路径的线程所有权留给 M09 地面应用服务（ISSUE-048），本模块只提供可被安全调用的纯编排 + 有界状态对象（`ProcessingController` 内部锁保护、bookkeeping 受 `history_limit` 约束，无无界增长）。

## 5. 文件改动（精确路径，无 glob）

| 路径 | 动作 | 内容 |
|---|---|---|
| `src/uav_gpr/application/processing_orchestrator.py` | 新增 | D1–D9：`ProcessingProfile`/`AirBackgroundSelection`/`ProcessingRequest`/`CalibratedSnapshot`/`ProcessingController`/`ProcessingToken`/`ProcessingRevision`/`VisibleState`/`StaleProcessingResult`/`ProcessedMission`/`run_processing`/`DerivedAttachmentWriter`/`DerivedWritePayload`/`AttachmentReport`/`attach_derived_result`/`assert_raw_bytes_unchanged`/`raw_column_fingerprint`/`archive_to_schema_grid`（全部经完整路径 import 复用既有 stage/core/storage，零复制） |
| `tests/contract/test_processing_orchestrator.py` | 新增 | §7 测试矩阵（组合全覆盖 + 拒绝矩阵 + revision 竞争 + 往返对拍 + raw 不变） |
| `docs/plans/2026-09-05-issue-036-orchestration.md` | 新增 | 本文档 |
| `docs/issues/M06_CALIBRATION_PROCESSING.md` | 修改 | 仅 L340 状态行 `Planned → In progress → Review` |

## 6. 复用清单（M 节：提取契约，零复制、零修改被复用模块）

| # | 被复用能力（只读消费） | 用途 |
|---|---|---|
| R1 | `processing.bandpass.StageResult`（频域阶段返回值类型）| 复用既有结果契约、不新建平行类型；`ProcessingStage` 协议守卫与 `_record_for`/`_input_domain_of` 由各 stage 的 `apply` 内部生效，编排层不重复实现（避免两套判定漂移） |
| R2 | `processing.osl_calibration.OslCalibrationStage`/`check_safe_reuse`/`require_safe_reuse`/`osl_set_digest`/`osl_profile_digest`/`OslProfileProvenance` | 链首 OSL + safe reuse 权威 + profile digest |
| R3 | `processing.background_subtraction.AirBackgroundSubtractionStage`/`check_safe_reuse`/`background_reference_digest` | 背景减除 + calibrated 域绑定校验 |
| R4 | `processing.time_domain.FrequencyToTimeStage`/`TimeDomainStageResult`/`DisplayCropConfig`/`DisplayTimeWindowView` | IFFT/`time_base` 生产端 + 显示裁剪（不进 history） |
| R5 | `processing.dewow.DewowStage`、`processing.flat_reflection.FlatReflectionFilterStage` | 两个可选时域后处理 |
| R6 | `core.time_domain.ProcessingHistory`/`ProcessingRecord`/`TimeDomainScan`、`core.frequency.FrequencySweep`/`FrequencyScan`、`core.enums.DataDomain`/`TimeDomainKind`、`core.errors.DomainError`/`ErrorCode`、`core.identifiers.*`、`core.timeutil.Clock`/`ensure_utc` | 模型、错误、时间 |
| R7 | `storage.rcscan_reader.RcScanReader`（011 只读面）、`storage.rcscan_v2.dataset_contracts`/`probe_rcscan_v2`/`dumps_utf8_json`/`loads_utf8_json`、`storage.incremental_writer` 生命周期口径 | 派生附加与严格复核（不 import 参考项目；HDF5 写面为本模块内直接使用 h5py 的受控实现，物理参数取自上述权威契约常量——见 §3 与 D5） |
| 不采用 | 参考项目 `E:\钢筋仪软件开发` 巨型处理窗口/blackbox 函数；旧 UAV-GPR 处理实现 | 编排是本项目契约的新建能力，无迁移来源 |

## 7. 测试矩阵（失败测试优先；覆盖 captain 指派全部维度）

实际交付 49 个用例（无 skip / xfail / 断言删减），逐条对应上表：

1. **关键组合覆盖**（`test_chain_order_and_domains` ×14 参数化 + `test_time_base_is_always_the_ifft_output` + `test_history_first_record_always_consumes_frequency_raw`）：全关、仅 OSL、bg-on-raw、OSL+bg(calibrated)、仅 bandpass、OSL+bandpass、四级链、全开、dewow-only、flat-only、dewow→flat、bg+bandpass+dewow、OSL+flat、OSL+bg+IFFT ⇒ 断言 stage 序列 = 规范序在开启集合上的限制、无重复、IFFT 恰一次、`time_base.kind=time_base` 且其 history 恰为 IFFT 前缀、`time_processed` 仅时域开启时存在、`final_domain == history.records[-1].output_domain`、snapshot 摘要与 `osl_set_digest` 相等。
2. **入口严格性**：`test_fresh_entry_rejects_non_empty_history`（非空 ⇒ `INVALID_ARGUMENT`，消息含 empty）、`test_fresh_entry_requires_frequency_container`（时域容器拒）、`test_orchestrator_does_not_mutate_input_source`（输入 bytes 不变 + read-only）、`test_sweep_input_supported_and_container_preserved`。
3. **二次 OSL / 背景拒绝**：`test_second_background_application_is_refused_by_the_chain`、`test_double_osl_through_stages_is_refused_even_bumping_version`（绕过编排直调 stage 亦被 raw-only 门 + core 唯一性拒）。
4. **错 profile / reuse 严格性**：`test_reuse_refuses_wrong_profile[resolved_content|different_id|swapped_binding|legacy_provenance]` ⇒ 全部 `PROCESSING_DOMAIN_MISMATCH` 且 `context["mismatches"]` 非空；`test_reuse_with_identical_provenance_skips_second_calibration`（记录数不增 ⇒ 未二次校准）；`test_reuse_matches_fresh_run_bit_exact`；`test_snapshot_construction_is_fail_closed`、`test_reuse_entry_refuses_missing_or_mismatched_snapshot`（缺快照 / 数据不符 / 通道或轴不一致均拒）；`test_calibrated_snapshot_round_trips_losslessly`（base64 complex128 + records 往返后 reuse 结果与内存版一致）。
5. **revision / cancel 竞争**：`test_revision_token_advances_and_publishes`、`test_stale_worker_result_is_dropped_before_publication`（rev1 抛 `StaleProcessingResult`，`dropped>=1`、visible=2、`accepts(1)` False）、`test_republish_older_revision_never_overwrites`（旧结果迟到返回既有可见者）、`test_cancelled_revision_raises_and_next_revision_completes`、`test_cancellation_leaves_raw_storage_untouched`（取消 ⇒ raw 摘要不变且无任何派生组）。
6. **受控附加接口与 raw 不变**：`test_attachment_refuses_a_grid_the_frozen_reader_cannot_validate`（宽网格 ⇒ `published=False`/`refused_reason="strict_validation"`、原文件 SHA-256 与 raw 列摘要双不变、reader 报告仍 clean）、`test_archivable_grid_guard_is_fail_closed`（非 2 的幂网格 fail-closed；合规网格原样返回，证明编排不裁剪 stage 输出）、`test_payload_matches_the_contract_of_its_own_grid`（载荷路径 ⊆ optional 集且形状/dtype 与 `dataset_contracts(..., time_points=n_actual)` 逐项相等）、`test_published_attachment_round_trips_and_is_replaceable`（成功路径：写 ⇒ 011 严格 reader 通过且逐道 hash_verified ⇒ 重放 payload 与盘上数组/history bit-exact ⇒ 再附着替换并保留 raw 不变）、`test_reader_accepts_attached_file`、`test_attachment_rejects_row_count_mismatch_without_touching_raw`、`test_assert_raw_bytes_unchanged_detects_tampering`（篡改 raw 立即被指纹闸捕获）、`test_failed_attach_leaves_file_byte_identical`、`test_writer_refuses_payloads_outside_the_controlled_allow_list`（伪造 `/frequency/raw`、 invented path、行数不足、非 canonical history 四类越权载荷全部拒且零落盘）。
7. **profile 身份与 provenance**：`test_profile_digest_tracks_content_not_identity`（同引用同摘要；开关变/内容重解 ⇒ 摘要变）、`test_every_stage_record_keeps_full_provenance`（六段全开 records 完整、JSON-safe 可 `from_dict` 重建等值）。
8. **依赖回归**：029–035 定向 + core time_domain + storage reader/writer 相关回归包含于全量 verify（1445 passed，exit 0）。

## 8. 执行日志

- 2026-09-05 t2 开工：认领 t2（attempt `106aa520`）。依 captain 口径确认 (i) 不扩 schema、calibrated 阶段频域历史以 mission 属性承载且全链 records 落 `/time_base/history_json`；(ii) 派生写在 application 层新门面内完成，raw/trace-major 零字节。
- **红灯先行**：先写 `tests/contract/test_processing_orchestrator.py`（49 用例），收集即失败于 `ModuleNotFoundError: No module named 'uav_gpr.application.processing_orchestrator'`（未实现前不可通过，非降级断言）。
- 实现 `processing_orchestrator.py`（D1–D9）后逐步转绿。实现期实测发现并如实登记三处契约事实（详见 D5/D6/D7）：
  1. v2 无 `/frequency/history_json` ⇒ 频域历史改落 mission 属性 `frequency_history_json`（不改 schema）；
  2. **011 reader 用默认 `time_points = frequency_points` 校验"存在即核"的可选组**，而 IFFT 归档网格为插值后的 2 的幂长度（33 频点 ⇒ 1024 样本）⇒ 真实宽网格附件必然被严格 reader 拒绝。裁决：**不在编排层裁剪 stage 输出**（会静默改写 provenance 声称的产物、违反 PROCESSING §8），改为受控 writer "暂存副本 + 严格 reader 复核 + 只有通过才原子发布"，拒绝时原文件字节不变并返回可观测 `refused_reason="strict_validation"`；成功路径另以契约一致网格全覆盖（保存→加载→重放→再附着→逐字节对拍）。此缺口列为 §9 首要 ADR 议题。
  3. reuse 入口的 profile 语义：复用即"不再跑 OSL"，故内部把该次执行的 calibration 置 None 后继续挂 bandpass/IFFT/Dewow/Flat（严格 provenance 校验仍按请求 profile 的 live set 由 032/033 权威执行，错 profile 一律拒）。
- 定向测试最终 **49 passed**（无 skip、无 xfail、无断言删减）；M06 L340 状态行 `Planned → Review`（仅此一行，`git diff --stat` = 1 insertion / 1 deletion）。
- 门禁数字见 §10。

### 8b. t4 repair-round-2（闭合 t3 复审 F1-F5）

依 [docs/reports/ISSUE_036_REVIEW_REPORT.md](../reports/ISSUE_036_REVIEW_REPORT.md) 结论 PASS WITH CONDITIONS，本轮按"先红后绿"闭合全部 5 项：

| # | 等级 | 修复 | 证据 |
|---|---|---|---|
| F1 | P2 | `inspect()` ⓪ 门卫：`file_role != GROUND` 或 `lifecycle_state ∉ {finalized, recovered}` ⇒ `DerivedAttachmentError(INVALID_ARGUMENT)`（context 带 `file_role` / `lifecycle_state`），在任何暂存拷贝之前拒绝 | 新增 `test_attachment_refuses_an_air_file`、`test_attachment_refuses_a_writing_partial`（两者均断言整文件 SHA-256 + raw 列摘要 + raw 指纹三者前后不变、盘上无派生组、无 `.derived.tmp` 残留）、`test_recovered_files_are_accepted`（放行 recovered 态）；builder 泛化为 `build_rcscan(role=..., finalize=...)` |
| F2 | P3 | 计划文档 §3/R7/D5 措辞改为如实声明"application 层直接使用 h5py 写受控暂存副本、物理参数取自 `storage.rcscan_v2.dataset_contracts` 权威常量"，并把"收敛为 storage 公开 API"记入 §9 后续 ADR | 本文档 diff |
| F3 | P3 | 三处恒真断言改为真实 before/after 对拍：`test_writer_refuses_payloads…` 捕获 `whole_before/raw_before/fingerprint_before` 并逐项比较；round-trip 用例改为 `written.raw_fingerprint == raw_column_fingerprint(path)` 与 `raw_column_digest(path) == raw_before` | 定向全绿（新增值被真实约束） |
| F4 | P3 | `AttachmentReport.to_dict()` 增加 `published` / `refused_reason` 两键 | 新增 `test_report_serialization_exposes_the_refusal_reason`（拒发态序列化可见 + 发布态 `refused_reason is None`） |
| F5 | P3 | `ProcessingRequest` docstring 明确入口相关的 `history` 语义（fresh=必须空且被校验；reuse=权威取 `snapshot.history`，`request.history` 不参与，建议留空），reuse 分支加同源注释 | 模块 docstring + 代码注释（无行为变更，reviewer 实测两种传法行为一致） |

定向：红灯 3 failed（F1×2 + F4）→ 绿灯 **53 passed**（t2 的 49 + 本轮 4 个新用例）。

## 9. 剩余风险 / 未完成项（如实登记）

- **【首要】派生网格与 011 reader 默认契约互斥（D6(b)，需 ADR）**：真实 IFFT 归档网格（插值后，如 33 频点 ⇒ 1024 样本）长于 v2 可选组默认声明的 `time_points = frequency_points`，而 ISSUE-011 reader 用该默认口径"存在即核"。后果：**当前任何带时域派生数据的 ground `.rcscan` 无法在被严格 reader 接受的同时保留真实网格**；受控 writer 因此拒绝发布（原文件字节不变、可观测 `refused_reason="strict_validation"`），本轮不伪造通过、也不裁剪 stage 输出。解法需 ADR + schema 变更二选一：(i) 让 reader/creator 以文件内实际 `/axes/time_base_s` 长度参数化 `dataset_contracts(..., time_points=n_actual)`；(ii) 在 schema 中把派生时间轴改为可变长并显式持久化其长度。该项挂 M06 收尾后的 ADR 议题，同时影响 ISSUE-048（地面 ingest/处理/存储应用服务）。
- **频域派生历史无独立落盘位**（D6(a)）：`/frequency/history_json` 未在 v2 契约声明，本轮全链 records 落 `/time_base/history_json`、calibrated 阶段前缀落 mission 属性 `frequency_history_json`。若后续要求"无 time 域产物时也归档频域快照历史"，同样需要先写 ADR 扩展 schema。
- **实时增量与显示裁剪**：本编排只做一次性/可重入的批式处理 + 显示裁剪旁路（不写 history）；实时逐道增量预览语义留给 M09（ISSUE-048/050）。
- **线程宿主**：`ProcessingController` 是有界可观测状态机但自身不拥有线程；真实 worker 调度在 M09 接入，届时须遵守 AGENTS.md §7（不在 UI 主线程跑长处理）。
- **零时/连续背景**：链上无槽位（Issue 排除项），PROCESSING.md §2 标注 future。
- **HDF5 写面尚未收敛为 storage 公开 API**（t4/F2 如实登记）：本模块内直接使用 `h5py` 完成受控暂存写入，物理参数取自 `storage.rcscan_v2.dataset_contracts`；后续若 M09/M11 需要同一能力，应先以 ADR 把它提升为 storage 层公开接口，避免第二个直连点出现。
- ~~F1 受控附加接口缺 file_role/lifecycle_state 门卫~~ **已于 t4 闭合**（inspect() ⓪ 门 + 4 个契约测试）。

## 10. 门禁结果（实测）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 定向（先红后绿） | `./.venv/Scripts/python.exe -m pytest tests/contract/test_processing_orchestrator.py -q` | t2：红灯 `ModuleNotFoundError`（收集期失败）⇒ 绿灯 49 passed；**t4：新增用例先红（F1 air / F1 writing / F4 序列化 = 3 failed, 50 passed）⇒ 绿灯 53 passed in 1.09s**，全程无 skip / xfail / 断言删减 |
| 全量 | `./.venv/Scripts/python.exe tools/quality/verify.py` | pytest `-m "not hardware and not slow"`：**t2 1445 passed / 4 deselected ⇒ t4 1449 passed / 4 deselected**（基线 1396 + 编排 49 + 修复回合 4 = 1449 ✓，4 deselected 为硬件哨兵与 opt-in LibreVNA）；`[quality] all gates passed` exit 0 |
| ruff | `./.venv/Scripts/python.exe -m ruff check .` | `All checks passed!` |
| mypy | `./.venv/Scripts/python.exe -m mypy src` | `Success: no issues found in 56 source files`（t1 基线 55 ⇒ 新增编排模块后 56，符合 t1 §5 预期变化） |
| package import | verify 内置 `IMPORT_CHECK` | `package import ok` |
| diff / 状态 | `git diff --check && git status --porcelain=v1 -b` | diff-check 干净；工作树仅 §2 四路径（M06 为唯一 tracked 修改，其余三文件新增）；临时日志写于仓库外 `/tmp/verify_036_t2.log` 并已删除，仓库零遗留产物 |

未 commit / 未 push / 未 merge（AGENTS.md §11、README §3.10）。完成后停止，不进入 ISSUE-037。
