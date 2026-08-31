# ISSUE-018 实施计划：`.rcscan` 文件回放后端（FileReplayBackend）

日期：2026-08-31
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-018-replay-backend`（执行器 engineer，任务 t2，attempt 8e57debb-d454-4244-b3fb-10db241787b9）
基线：`main` @ `9406b60`（工作树干净、origin/main 同步 0/0）；权威基线件：[docs/reports/ISSUE_018_BASELINE_CONFIRMATION.md](../reports/ISSUE_018_BASELINE_CONFIRMATION.md)（t1）
配套：本计划为 t2 执行契约与 t3 复审依据；执行日志随执行过程追加。

## 1. 目标与用户价值

让严格 reader 通过同一 `AcquisitionBackend` 接口回放 raw：`FileReplayBackend` 基于 ISSUE-011 `RcScanReader`（v2 air/ground）与 ISSUE-013 `RcScanV1Reader`（v1 adapter）按逻辑 trace 顺序输出原始 `FrequencySweep`，支持逐道/原始时间比例/显式加速三种节奏（等待可取消），可被 ISSUE-017 `AcquisitionController` 暂停/恢复/停止；原样保留 mission/trace ID、UTC/GNSS/缺失字段（不用当前时间或 0 坐标补齐），不自动应用文件已有 calibrated/time 结果、不重复处理。价值：为后续处理编排（ISSUE-036 安全回放）、地面回放应用（ISSUE-048）与 UI 回放（ISSUE-052）提供与真机/模拟器同一接口的确定性数据源（M03 门禁「模拟/回放、单调调度和状态机稳定」）。

## 2. 范围（M03 L126–131 + 提示词）

1. `FileReplayBackend`（`AcquisitionBackend` 子类，`src/uav_gpr/acquisition/replay.py` 单一新模块）：
   - open：v2 经 `RcScanReader`（严格打开校验 fail-closed）；v1 经 `RcScanV1Reader`（格式探测：仅当 v2 probe 报 `UNSUPPORTED_SCHEMA_VERSION` 时回落 v1）；打开期执行 v2 `validation_report()`，冲突身份与逐道 hash 问题（`HASH_MISMATCH`/`MISSING_HASH`）以及零 committed raw（无 raw）明确拒绝（fail-closed）。
   - configure：v2 以文件 mission config 为权威 applied（请求配置 digest 必须与文件 `config_sha256` 相等，否则 `BackendConfigRejectedError`；`AppliedConfig.diff` 如实记录 requested/applied 差异）；v1 校验请求配置的 channels/频率轴与文件一致（v1 无任务配置，请求配置即 applied）；v1 无逐道时间戳且节奏模式需要时间源时拒绝配置。
   - acquire：按逻辑顺序（v2 = reader 逻辑视图 `iter_logical` 的 `trace_index` 序列，乱序物理记录在逻辑视图正确排序；v1 = 物理行序）逐道输出原始 `FrequencySweep`；节奏等待使用基类 `_wait_cancellable`（honor `cancel()`/`close()`/`timeout_s`，不新建线程、不引入固定 sleep）；全部道输出完后再次 acquire 抛结构化 `ReplayEndedError`（消费方按 `trace_count` 通过 controller.stop() 结束任务；任务层自动停止属 ISSUE-043/048）。
   - 属性：`trace_count`（逻辑道数）、`source_format`（"rcscan_v2" | "rcscan_v1"）、`capabilities`。
2. 三种节奏模式（`ReplayMode` + `ReplayConfig`，构造注入）：
   - `PER_TRACE`（逐道）：无节奏等待，尽快输出；
   - `ORIGINAL_TIME`（原始时间比例）：gap = 文件中相邻道的时间差 × 1.0（v2 用 `sweep_started_monotonic_ns` 差值——单调时钟，UTC 跳变不影响；v1 用 `trace_timestamps_utc` 差值——v1 无单调记录，按 ISSUE-013 迁移同口径的文档化导入语义）；
   - `ACCELERATED`（显式加速）：gap × `acceleration`（必须为有限 float > 1.0）。
3. 原样保留：`TraceMetadata` 全字段（mission_id/trace_index/trace_uid/UTC 三时刻/单调三时刻/目标与实际间隔/schedule error/connection_generation/raw_trace_sha256/gnss_match/quality_status/quality_reasons）逐字节等于 reader 解码值；v1 无 mission/逐道 UID/GNSS/单调 → `FrequencySweep.metadata=None`（缺失保持缺失）。
4. 不重复应用：仅输出 `/frequency/raw`；文件可选组（`frequency/calibrated`、`time_base`、`time_processed`）存在时忽略（不读取、不应用、不处理）。

## 3. 明确排除项（M03 L133–135 + 提示词 + 任务契约）

- 不实现处理 revision、UI 播放条、文件迁移；
- 不改 `core/**`、`storage/**`（含 rcscan_reader/rcscan_v1）、`acquisition/backend.py`/`scheduler.py`/`controller.py`——只读消费既有契约；
- 不改两个参考项目；不 commit/push/merge、不创建/切换分支；不进入 ISSUE-019；
- 不在 `src/uav_gpr/acquisition/replay.py`、`tests/contract/test_acquisition_replay.py`、`docs/plans/2026-08-30-issue-018-replay-backend.md`、`docs/issues/M03_ACQUISITION.md` 之外新增任何文件（确需拆分先停止向 captain 报告）。

## 4. 关联需求/ADR/文档与参考源哈希

- 需求映射：FR-016、018（M03 L121）；AGENTS.md 第 3/4/5 节强制数据规则（raw 不可变、真实时间/GNSS、缺失不伪造）；无新的 ADR 需求（只读回放不改变强制数据规则/空地职责/持久化语义，t1 基线单 §3.5-7 同口径）。
- 文档：docs/ACQUISITION.md §1/2（同一接口、FileReplayBackend 语义）、§7/9（调度与暂停/停止语义）；docs/DATA_FORMAT.md §3.1（reader 契约、hash_verified/report.issues 为消费权威）、§6（空地差异：calibrated/时域不冒充 raw）；docs/PROCESSING.md §1/§7/§9（回放不重复 OSL/背景，重处理只能从 frequency_raw 开始）。
- 参考源哈希：本 Issue 不迁移参考项目代码；依赖模块为 main 内 tracked 契约（backend.py 725 行、controller.py 949 行、scheduler.py 447 行、rcscan_reader.py 1070 行、rcscan_v1.py 1431 行，行数实测），见 t1 基线单 §3.2。

## 5. 设计决策（ADR 级，含备选与理由）

| # | 决策 | 理由 | 备选（否决理由） |
|---|---|---|---|
| D1 | 节奏等待复用基类 `_wait_cancellable(seconds=gap, attempt, timeout_s)`，不注入独立 Waiter、不覆盖 cancel/close | 零新增等待路径：cancel()/close()/timeout_s 语义原生成立（`_cancel_event` 单一信号源）；与 ISSUE-015 `SimulationFaults.delay_s` 同一既有机制；测试沿用 015 既定模式（事件/join，禁固定 sleep） | 注入 Waiter（同 scheduler）：需覆盖 cancel/close 唤醒、引入跨模块依赖，收益仅是虚拟时间；回放节奏无需数万周期无漂移，下限断言+事件驱动已确定 |
| D2 | v1/v2 探测：先 `RcScanReader`，仅当 `ErrorCode.UNSUPPORTED_SCHEMA_VERSION` 回落 `RcScanV1Reader`；其它打开失败（含损坏/非 HDF5/未知 profile/未知版本）包装为 `ReplayUnsupportedFileError` | v2 probe 对 schema_version ∉ {2} 报 UNSUPPORTED_SCHEMA_VERSION（rcscan_v2.py L1395–1418 实测），是 v1 的唯一合法探测信号；其它失败即损坏，不降级 | 先 probe 再分派：probe 已抛错，等于重复探测；显式 format 参数：调用方负担 |
| D3 | 打开期 v2 校验策略：`conflicts` 或 `HASH_MISMATCH`/`MISSING_HASH` issue → `ReplayCorruptFileError`；`committed_record_count == 0` → `ReplayNoRawError`；缺道/重复**容忍**（逻辑视图确定性服务首份提交，reader 契约定义） | 「损坏/无 raw 明确拒绝」验收：冲突=身份歧义绝不静默选一份（AGENTS.md fail-closed）；hash 未验证=数据损坏不得冒充 raw；空文件无 raw 可回放 | 缺道也拒绝：ground 文件可合法补传后仍缺道（ISSUE-041/043 任务层负责完整性），且 reader 逻辑视图契约即服务存在项 |
| D4 | v2 configure 校验 `config.config_sha256 == 文件 config_sha256`，applied = 文件 config，diff = `ConfigDiff.compute(requested, file_config)` | 回放的数据契约就是文件记录的任务；摘要相等=契约字段一致（created_utc/note 描述性字段不入摘要，ACQUISITION.md §4）；diff 如实呈现请求/生效差异 | 全字段相等：过严（描述性字段差异无数据影响）；完全接受任意配置：数据契约说谎 |
| D5 | v2 逻辑顺序在 open 时固化：遍历 `iter_logical()` 收集 `(trace_index, sweep_started_monotonic_ns)` 小元数据；acquire 经 `reader.trace_by_index(index)` 单道读取 | 与逻辑视图（重复折叠、冲突排除）完全一致；内存只留每道小元数据（有界，符合 reader 懒加载契约）；`trace_by_index` 是 reader 公开单道 API | 持有全部 ReadTrace（含 raw 数组）：破坏有界内存；acquire 时重扫 iter_logical：每道全文件扫描 |
| D6 | EOF 语义：全部输出后 acquire 抛 `ReplayEndedError`（reason "replay_ended"，BackendError 子类）；后端暴露 `trace_count`，消费方按计数经 `controller.stop()` 结束 | controller 对任何 acquire 异常都会结构化 FAILED（controller.py `_tick` 实测），无法表达"自然结束"；任务层自动停止（planned_trace_count/结束状态）属 ISSUE-043/048 职责 | 阻塞等 cancel：stop() 只 scheduler.cancel() 不 wake backend，会挂死（实测 controller.py L600–642）；静默循环：无意义 |
| D7 | v1 逐道输出 `FrequencySweep(..., metadata=None)`；`Capabilities.device_id` 用 `uuid5(V1_MIGRATION_NAMESPACE, f"device:{source_sha256}")` 确定性派生；v1 `capabilities.gnss=False` | v1 无 mission/逐道 UID/GNSS/单调（RcScanV1Reader 实测），缺失保持缺失（D 规则）；DeviceId 是 Capabilities 必填项，派生与 ISSUE-013 迁移同一命名空间/规则（确定性、可复现） | 伪造合成 mission/UID/GNSS：违反 AGENTS.md 第 5 节；随机 DeviceId：不可复现 |
| D8 | v2 `capabilities.gnss = 逻辑视图任一记录 `gnss_match is not None`；`fault_injection=False`；`device_id/channels` 直接来自 reader | 能力描述如实反映文件内容（无 GNSS 的 v2 文件 gnss=False，供上层决策）；回放无故障注入面 | 恒 True/恒 False：与文件内容不符 |

## 6. 文件改动（inScope 精确路径，changedPaths 必须与此逐一相等）

| 路径 | 内容 |
|---|---|
| `src/uav_gpr/acquisition/replay.py` | 新模块：`ReplayMode`/`ReplayConfig`、`ReplayError` 家族（`ReplayUnsupportedFileError`/`ReplayCorruptFileError`/`ReplayNoRawError`/`ReplayEndedError`）、`FileReplayBackend` |
| `tests/contract/test_acquisition_replay.py` | 新契约测试（失败测试优先；v2 air/ground、v1 adapter、乱序、无 GNSS、三节奏/取消、损坏/无 raw、数值对拍、controller 配合） |
| `docs/plans/2026-08-30-issue-018-replay-backend.md` | 本计划文档（t2 先落盘） |
| `docs/issues/M03_ACQUISITION.md` | 仅 ISSUE-018 状态行：`Planned → In progress → Review`（勿动其它条目） |

## 7. 测试矩阵（提示词必测项 → 测试名）

| 必测项 | 测试 | 手段（禁固定 sleep） |
|---|---|---|
| v2 air/ground 数值对拍 | `test_v2_air_replay_matches_reader_logical`、`test_v2_ground_replay_matches_reader_logical` | 自建 v2 夹具（schema.create_rcscan_v2 + trace_metadata_to_cells，同 ISSUE-010 writer 契约）→ `RcScanReader.iter_logical` 基准 → 回放逐字段 `==`（data/axis/channels/metadata 全字段） |
| v1 adapter 回放 | `test_v1_replay_preserves_rows_metadata_none`、`test_v1_replay_without_timestamps_per_trace_ok` | 自建 v1 夹具（同 test_rcscan_v1 结构：attrs + channels/axes/frequency/raw + 可选 trace_metadata）→ `RcScanV1Reader.raw_row` 对拍；`metadata is None` |
| 乱序物理记录 | `test_out_of_order_physical_rows_replay_in_logical_order` | 物理行按乱序 trace_index 写入，回放序 = 逻辑序（与 reader.iter_logical 一致） |
| 无 GNSS | `test_no_gnss_rows_keep_missing` | with_gnss=False 行：`metadata.gnss_match is None`、quality DEGRADED/GNSS_MISSING 原样 |
| 三节奏 | `test_per_trace_mode_never_waits`（虚拟断言：全量输出耗时下限极低）、`test_original_time_paces_by_file_gaps`（gap=0.05/0.10s，elapsed ≥ gap−margin，仅下限）、`test_accelerated_mode_scales_gaps`（ratio=4，elapsed ≥ 4·gap−margin） | 真实小等待 + `time.monotonic()` 下限断言（无上限、无固定 sleep）；cancel 用 `acquire_started` 事件 + `join(timeout)`（015 既定模式） |
| 取消/超时 | `test_cancel_interrupts_paced_wait`、`test_close_interrupts_paced_wait`、`test_paced_wait_honors_timeout_s` | 事件驱动：`acquire_started.wait()` → cancel/close → `join()` → `BackendCancelledError`/`BackendClosedError`；`timeout_s < gap` → `BackendTimeoutError` |
| 损坏/无 raw | `test_hash_mismatch_rejected`、`test_missing_stored_hash_rejected`、`test_conflicting_identity_rejected`、`test_no_committed_raw_rejected`、`test_non_hdf5_rejected`、`test_unknown_schema_rejected` | 夹具 + `corrupt_cell`（raw 改写致 hash 不符 / 清空存储 hash 列 / 同 index 双 hash 冲突行 / committed=0 骨架 / 文本文件 / schema_version=3） |
| EOF | `test_acquire_past_end_raises_replay_ended`、`test_trace_count_matches_logical_view` | 全量输出后再次 acquire → `ReplayEndedError` |
| configure 契约 | `test_configure_rejects_config_digest_mismatch`、`test_v1_configure_rejects_channel_mismatch`、`test_v1_paced_mode_without_timestamps_rejected`、`test_applied_config_is_file_config_with_diff` | 错误配置 → `BackendConfigRejectedError`；applied/diff 断言 |
| 不重复应用 | `test_calibrated_group_ignored_raw_served` | 夹具写 `/frequency/calibrated`（含不同数值）→ 回放 data == raw ≠ calibrated |
| controller 配合 | `test_controller_pause_resume_replay`、`test_controller_stop_drains_replay`、`test_controller_emergency_stop_interrupts_paced_wait`、`test_controller_close_no_leaked_worker` | `AcquisitionController` + 有节奏回放文件（gap=0.2s）：pause 安全边界（在途道完成并发布、之后不再有）、resume 新锚点、stop drain、emergency 中断在途不发布、close join 无残留线程；`wait_finished(timeout)`/`sweeps.get(timeout)` 事件驱动 |
| 回归 | 依赖定向 155 passed（ISSUE-011/015/017）不被破坏；全量 verify.py | — |

## 8. 性能/数据风险

- 回放逐道经 `trace_by_index` 单道读取（有界内存，符合 reader 懒加载契约）；不整文件物化。
- 打开期分类扫描（reader 一次性，`_CLASSIFY_CHUNK=64` 有界）为唯一全文件元数据遍历。
- 节奏等待为真实时间等待（每道 gap×ratio），逐道模式零等待；取消路径为事件唤醒，无轮询。
- 数据风险：绝不修改/伪造文件字段；只读打开（reader 保证）；raw 经 `FrequencySweep` 不可变快照输出。

## 9. 完成定义与回退方式

- 完成定义：计划 D1–D8 全部落地；定向测试先红灯后绿灯；全量非硬件门禁（verify.py）+ ruff + mypy + import 全绿；`git diff --check` 干净；M03 ISSUE-018 状态行 = `Review`；工作树仅 4 个 inScope 路径（+ t1 基线单，属 t1 交付物）；不 commit/push/merge。
- 回退方式：本 Issue 为纯新增模块与测试，回退 = 删除 4 个 inScope 改动（恢复 M03 状态行为 `Planned`）即可；不触碰任何既有契约文件。

## 10. 执行日志（追加式）

- [2026-08-31] 计划落盘（本文件）；M03 ISSUE-018 状态行 → `In progress`。
- [2026-08-31] 红灯：`tests/contract/test_acquisition_replay.py`（36 个测试）先于实现落盘；实现缺失时定向运行报 `ModuleNotFoundError: No module named 'uav_gpr.acquisition.replay'`（collection 中断，1 error），实现前的真实失败证据。
- [2026-08-31] 绿灯：实现 `src/uav_gpr/acquisition/replay.py`（D1–D8 全部落地）；迭代中修复三处测试/实现细节：① 非首道 `TraceMetadata` 必须携带 `actual_interval_s`/`schedule_error_s`（core/metadata.py L175 契约，fixture 补齐）；② 空文件 fixture 的零长度骨架列不填充（避免 KeyError）；③ v1 通道匹配按有序 `channel_id` 契约比较（v1 adapter 的 display_name/antenna_note 为描述性字段，数据契约为通道身份）。
- [2026-08-31] 定向结果：`python3 -m pytest tests/contract/test_acquisition_replay.py -q` → **37 passed in 4.66s**（v2 air/ground 对拍、v1 adapter、乱序、无 GNSS、calibrated 忽略、三节奏、取消/超时、损坏/无 raw（含缺存储 hash）拒绝、EOF、configure 契约、controller 暂停/恢复/停止/紧急/关闭共 37 项；`python3 -m ruff check src tests` All checks passed!；`python3 -m mypy src` Success: no issues found in 39 source files）。
- [2026-08-31] 门禁（全部实测复跑，WSL Python 3.12.3）：`python3 tools/quality/verify.py` → **740 passed, 1 deselected in 187.97s**（基线 703 + 新增 37）、ruff `All checks passed!`、mypy `Success: no issues found in 39 source files`、`package import ok`、`[quality] all gates passed`、`VERIFY_EXIT=0`；`git diff --check` clean；`git status --porcelain=v1 -b` 仅含 4 个 inScope 路径（+ t1 基线单，属 t1 交付物）。M03 ISSUE-018 状态行 → `Review`。

## E 执行日志（复审后 P3 处理记录，2026-08-31）

按 `docs/reports/ISSUE_018_REVIEW_REPORT.md` 第 10 节处理 4 项 P3（项目负责人指示"处理 4 项 P3 先"；本段由 captain 实施并记录）：

1. **P3-01（失败 open 状态残留 OPEN）**：`replay.py` `_do_open()` 改为 try 包裹 `_do_open_impl()`，异常路径调用幂等 `self.close()` 回滚至 CLOSED（不遗留无 reader 的 OPEN 态）。新增 `test_failed_open_rolls_back_to_closed`（失败 open 后 `state is CLOSED` + 可重开有效文件）。变异验证：还原守卫后该测试 FAIL（1 failed），恢复后绿灯——真实可杀。
2. **P3-02（v1 频率轴拒绝缺定向测试）**：行为已存在（`_do_configure` v1 分支 `np.array_equal` 检查），补定向测试 `test_v1_configure_rejects_axis_mismatch`（点数不同/起止平移两种变体均 `BackendConfigRejectedError`）。
3. **P3-03（close 与在途读取窄竞态）**：`_do_acquire()` 可取消等待返回后加锁复查 `state is CLOSED` → 结构化 `BackendClosedError`（在 `trace_by_index`/`raw_row` 读取前），消除 close 窗口内的原生 h5py 异常。同时补齐 `BackendClosedError`/`BackendState` 导入。
4. **P3-04（节奏测试真实时间断言固有风险）**：接受，不修（既有裕度充分：实测耗时约为边界的 1/10–1/25；若 CI 出现 flake 再引入可注入 sleeper）。
5. **门禁（修复后）**：定向 **39 passed in 4.49s**（37+2）；全量 verify.py **742 passed/1 deselected**（pytest --collect-only 742/743）；ruff/mypy/import 全绿；`git diff --check` clean。改动仅 `replay.py`、`test_acquisition_replay.py` 与本文档（均在 ISSUE-018 inScope 内）；未 commit/push/merge。
