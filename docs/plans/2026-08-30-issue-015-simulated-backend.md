# ISSUE-015 实施计划：AcquisitionBackend 契约与确定性模拟器

- 日期：2026-08-30
- Issue：ISSUE-015（`docs/issues/M03_ACQUISITION.md` L5–40，状态 `Planned`）
- 执行者：engineer（AgentTeams `uav-gpr-issue-015-simulated-backend`，任务 t2，attempt 1730d0e0）
- 权威基线：`docs/reports/ISSUE_015_BASELINE_CONFIRMATION.md`（t1 基线确认单）
- 配套：t3 独立复审按 `docs/ISSUE_REVIEW_STANDARD.md` 执行
- 性质：本计划文档是 t2 的权威执行契约；执行日志（红灯/绿灯/门禁数字）在本文件末尾追加

## 1. 目标与用户价值

定义统一 `AcquisitionBackend` 生命周期/能力/错误契约，并实现确定性 `SimulatedBackend`：按 seed/config/可注入 Clock 生成多通道 `FrequencySweep`（真实 shape/axis/UTC+monotonic metadata），支持 timeout、半道、配置拒绝、设备断开、延迟注入与可取消等待。价值：M03 后续 Issue（016 调度器、017 控制器、018 回放、019+ LibreVNA 真机后端）都在同一接口上工作；真机能力先由模拟器驱动应用测试（AGENTS.md §10、ACQUISITION.md §1「真实后端、模拟后端和文件回放实现同一接口」）。

## 2. 范围（inScope，精确文件路径，4 个）

1. `src/uav_gpr/acquisition/backend.py`（新模块：契约 + 状态机 + 模拟器）
2. `tests/contract/test_acquisition_backend.py`（新测试文件，失败测试优先）
3. `docs/plans/2026-08-30-issue-015-simulated-backend.md`（本文件）
4. `docs/issues/M03_ACQUISITION.md`（仅 ISSUE-015 状态行：`Planned → In progress → Review`，勿动其他条目）

完成登记 changedPaths 必须与本 inScope 逐一相等；若确需拆分模块/新增文件，先停止并向 captain 报告，不得自行新增范围外文件。

## 3. 排除项（不得越界）

- 不实现调度循环/scheduler（ISSUE-016）、采集控制器（ISSUE-017）、文件回放（ISSUE-018）。
- 不实现 Qt、HDF5、GNSS 串口 reader、LibreVNA USB/协议。
- 不改 `src/uav_gpr/core/**`（只读消费：`errors.py`/`timeutil.py`/`frequency.py`/`config.py`/`metadata.py`/`gnss.py`/`identifiers.py`/`raw_hash.py`/`enums.py`/`channels.py`）。
- 不改 `src/uav_gpr/storage/**`、`tools/**`、`docs/adr/**`、`docs/DATA_MODEL.md`、`docs/ACQUISITION.md`、`docs/TESTING.md`、`docs/reports/**`。
- 不触碰 ISSUE-013/014 在制产物（`rcscan_v1.py`、`test_rcscan_v1.py`、`inventory/`、`test_inventory.py`、相关计划/报告、M02 状态行）。
- 不 commit/push/merge、不创建/切换分支；不进入 ISSUE-016。

## 4. 关联需求/ADR/参考源

- 需求：FR-003（多通道 sweep 数据）、FR-018（模拟/回放驱动应用测试）——M03 L10。
- ADR：无新增 ADR 需求（不改变强制数据规则/空地职责/持久化语义；t1 基线单 §3.5-5 结论）。
- 参考源：无代码搬运（纯新契约模块）；复用核心冻结契约（ISSUE-003～006）。

## 5. 设计决策

### 5.1 模块结构（单一新模块 `backend.py`）

| 符号 | 种类 | 说明 |
|---|---|---|
| `BackendState` | `StableStrEnum` | `closed` / `open` / `configured` |
| `Capabilities` | frozen dataclass | `device_id`、`channels`（支持的通道元组，有序）、`fault_injection: bool`、`gnss: bool`；属性 `supports_dual_channel`（len≥2 推导） |
| `AppliedConfig` | frozen dataclass | `config: MissionConfig`（设备实际生效配置）+ `diff: ConfigDiff`（requested vs applied，复用 ISSUE-006 契约） |
| `AcquisitionBackend` | ABC | 严格生命周期状态机（模板方法）：`open/configure/acquire/cancel/close`、属性 `state`/`connection_generation`/`acquiring`、可观测 `acquire_started` Event；抽象钩子 `_do_open/_do_configure/_do_acquire/_do_close` |
| `BackendError` 族 | `DomainError` 子类 | `BackendStateError`/`BackendTimeoutError`/`BackendHalfSweepError`/`BackendDisconnectedError`/`BackendConfigRejectedError`/`BackendCancelledError`/`BackendClosedError` |
| `SimulationFaults` | frozen dataclass | 确定性故障计划（见 5.4） |
| `SimulatedBackend` | `AcquisitionBackend` | 确定性多通道模拟器（见 5.3） |

### 5.2 严格生命周期（基类统一强制，非法转换结构化拒绝）

```text
CLOSED --open()--> OPEN --configure()--> CONFIGURED --acquire()*--> CONFIGURED
                                                            \--cancel()--> CONFIGURED（中断在途等待；无在途等待则为 no-op）
OPEN/CONFIGURED --close()--> CLOSED；CLOSED --close()--> CLOSED（幂等 no-op）
CLOSED --cancel()--> CLOSED（幂等 no-op）
```

- `open()` 仅允许从 `CLOSED`（含 close 后重开）；`open` 时 `connection_generation = 1`。**重开（reopen）必须在在途 acquire 全部终止后**：`close()` 会唤醒在途等待（其抛 `BackendClosedError`），调用方须先 join 再 `open()`。
- `configure()` 仅允许从 `OPEN`（首配）或 `CONFIGURED`（重配）；重配重置道计数/尝试计数/rng（新任务语义）。**configure 与 acquire 互斥**：在途 acquire 期间 `configure()` 结构化拒绝（`BackendStateError`，context `busy=True`），绝不静默重配。**重配视为新任务，调用方必须更换 `mission_id`**（`trace_uid` 由 `mission:index` 派生，同 mission 重配会重复 UID——ISSUE-017/043 契约）。
- `acquire()` 仅允许从 `CONFIGURED`；并发 acquire（已有在途）结构化拒绝（单设备串行，ACQUISITION.md §7）。
- `close()` 从任意状态允许且幂等；`cancel()` 从任意状态允许且幂等（`CLOSED` 下 no-op）。
- 非法转换一律 `BackendStateError`（`DomainError`，code=`INVALID_ARGUMENT`，context 带 `reason="illegal_state"`、`operation`、`state`、`allowed_states`）。
- **钩子（`_do_open`/`_do_configure`/`_do_acquire`/`_do_close`）失败回滚语义留给 ISSUE-019（LibreVNA 传输层）处理**（复审 P3-03；本 Issue 不实现）。

### 5.3 确定性 SimulatedBackend

- 构造参数：`mission_id: MissionId`、`device_id: DeviceId`、`channels: Sequence[ChannelSpec]`（设备支持通道）、`seed: int = 0`、`clock: Clock | None = None`（缺省 `SystemClock`，测试注入 `ManualClock`）、`faults: SimulationFaults = SimulationFaults()`、`gnss_enabled: bool = False`。
- **rng 派生**：`configure()` 时 `rng = np.random.default_rng(derive(seed, config.config_sha256))`——相同 seed+config 必产生相同 raw；不同 seed 或不同 config 产生不同 raw；与 configure 历史无关。
- **数据生成**：每道 `data = envelope(f) * (rng.standard_normal((n_ch, n_f)) + 1j * rng.standard_normal((n_ch, n_f)))`，complex128，shape `channel × frequency`；频率轴取 `config.frequency_axis_hz()`（严格递增）；通道取配置通道（须为设备支持通道的有序子集）。
- **metadata**（真实 UTC+monotonic）：`TraceMetadata` 由注入 Clock 读取 start/mid/finish（UTC 与 monotonic 各自有序）；`trace_index` 从 0 起仅成功道递增；`trace_uid` 由 `uuid5(NAMESPACE_URL, mission:index)` 确定性生成；首道 `actual_interval_s`/`schedule_error_s` 为 `None`，后续道按 start-to-start monotonic 差值计算 `actual_interval_s`、`schedule_error_s = actual - target`（ACQUISITION.md §7 口径）；`target_interval_s` 取 applied config；`connection_generation` 取当前代数；`raw_trace_sha256` 用 ISSUE-009 `RawHashSpec.compute()` 计算；GNSS 关闭 → `gnss_match=None` + `quality_reasons=(gnss_missing,)` + `DEGRADED`（metadata.py 校验要求），GNSS 开启 → 确定性 `GnssFix`/`GnssMatch`（`usable_for_map=True`、midpoint 与 sweep 中点一致）+ `NOMINAL`。
- **确定性故障计划**：故障索引按 **acquire 尝试序号**（0 起，含失败尝试）匹配，`trace_index` 只随成功道递增——「错误按计划在确定道触发」。

### 5.4 SimulationFaults（确定性故障注入）

| 字段 | 语义 |
|---|---|
| `timeout_at: tuple[int, ...]` | 指定尝试序号立即抛 `BackendTimeoutError`（设备超时，无道产出） |
| `half_sweep_at: tuple[int, ...]` | 指定尝试序号抛 `BackendHalfSweepError`（半道 fail-closed，禁止零填充；不消耗 trace_index） |
| `disconnect_at: tuple[int, ...]` | 指定尝试序号抛 `BackendDisconnectedError` 且 `connection_generation += 1`（模拟重连代数） |
| `delay_s: Mapping[int, float]` | 尝试序号 → 模拟设备延迟（秒）；等待可取消（`_cancel_event.wait(delay)`），被取消抛 `BackendCancelledError` |
| `reject_config: bool` | `configure()` 抛 `BackendConfigRejectedError`（配置拒绝） |
| `applied_if_bw_hz: float | None` | 非 None 时 applied config 的 `if_bw_hz` 量化为此值 → `AppliedConfig.diff` 非空（requested/applied diff 场景） |
| `block_until_cancelled: bool` | 每次 acquire 先阻塞等待直到 cancel/close（或 `acquire(timeout_s)` 超时），用于可取消等待/资源清理测试 |

- `acquire(timeout_s: float | None)`：阻塞条件（delay/block_until_cancelled）下最多等待 `timeout_s`；超时抛 `BackendTimeoutError`；被 cancel 抛 `BackendCancelledError`；被 close 抛 `BackendClosedError`。无阻塞条件时立即返回。
- 校验顺序：尝试序号故障（timeout→half→disconnect）→ 阻塞/延迟 → 生成道。

### 5.5 错误契约（core 结构化错误）

- core `ErrorCode` 不可扩展（core 只读消费），backend 错误统一 `DomainError` 且 `code=ErrorCode.INVALID_ARGUMENT`，context 携带**稳定 `reason` 判别键**：`illegal_state` / `device_timeout` / `half_sweep` / `device_disconnected` / `config_rejected` / `cancelled` / `closed`；并提供类型化子类（`BackendStateError` 等）供 `isinstance` 分支（ISSUE-017 控制器需分类错误）。业务分支仍以 `error.code` + `error.context["reason"]` 为机器判据。

### 5.6 并发与资源所有权

- 基类持有 `threading.Lock`（保护状态/代数/在途标记）与 `threading.Event _cancel_event`（cancel/close 置位唤醒阻塞等待）；`acquire()` 持锁仅做状态检查与在途标记，**阻塞等待期间不持锁**（否则 close 会死锁）。
- `acquire_started` Event：进入 `_do_acquire` 前置位、退出清除——测试/控制器可事件等待「已阻塞」，不用固定 sleep 猜时序。
- `close()` 置位 cancel 事件并置 `CLOSED`；被阻塞的 `acquire` 被唤醒后检查状态抛 `BackendClosedError`；close/cancel 均不创建线程（模拟器无自有线程），「不遗留线程或等待」由测试用事件/join 验证。

## 6. 文件改动（= inScope，完成登记 changedPaths 逐一相等）

| 文件 | 动作 | 内容 |
|---|---|---|
| `src/uav_gpr/acquisition/backend.py` | 新建 | 5.1–5.6 全部符号 |
| `tests/contract/test_acquisition_backend.py` | 新建 | 20+ 用例（见 §7），先红灯后绿灯 |
| `docs/plans/2026-08-30-issue-015-simulated-backend.md` | 新建 | 本文件（含执行日志） |
| `docs/issues/M03_ACQUISITION.md` | 最小编辑 | 仅 ISSUE-015 状态行（L7）：`Planned → In progress`（开工）→ `Review`（完成） |

## 7. 测试矩阵（tests/contract/test_acquisition_backend.py，失败测试优先）

1. 生命周期：完整 happy path（open→configure→acquire→close）；非法转换结构化拒绝（open 二次/configure 未 open/acquire 未 configure/acquire after close）；重开。
2. 确定性：同 seed/config/clock → raw 逐值相等（含 metadata/哈希）；异 seed → 不同 raw；同 seed 异 config → 不同 raw。
3. 单/双通道：共用同一 `SimulatedBackend`/`AcquisitionBackend` 接口无分叉；shape `channel×frequency`、通道顺序、频率轴与 config 一致。
4. requested/applied diff：`applied_if_bw_hz` 量化 → `AppliedConfig.diff` 非空且字段唯一/排序/实际变化；缺省 → `diff.is_identical()`。
5. 故障点：`timeout_at`/`half_sweep_at`/`disconnect_at` 在确定尝试触发、不消耗 trace_index、断开后 generation+1 且后续道携带新代数；`reject_config` 与不支持通道 → `BackendConfigRejectedError` 且状态可恢复。
6. 可取消等待与资源清理：`delay_s`/`block_until_cancelled` + 线程 + `acquire_started` 事件 + `join(timeout)` 验证 cancel 唤醒（`BackendCancelledError`）、close 唤醒（`BackendClosedError`）、`acquire(timeout_s)` 超时（`BackendTimeoutError`）、无在途 cancel 为 no-op、并发 acquire 结构化拒绝；**全程事件/屏障/join，无固定 sleep 猜时序**。
7. metadata：UTC start≤mid≤finish、monotonic 有序、首道间隔 None、后续道 actual/schedule error 口径、raw hash 与 ISSUE-009 `RawHashSpec.compute()` 对拍、GNSS 关闭显式 `gnss_missing`/开启 `usable_for_map`。
8. 门禁复跑：定向新测试 + 全量非硬件 verify.py + ruff + mypy + import + `git diff --check` + 工作树快照。

## 8. 性能/数据风险

- 模拟器为纯内存单线程逻辑；每道只做 O(n_ch×n_f) 随机生成与一次 raw hash（n_f 默认 ≤ 千级），无性能风险。
- 延迟故障使用真实 `Event.wait(timeout)`（模拟设备延迟），测试中延迟值小（≤0.2s）或无限阻塞由 cancel 终止；无 busy-wait。
- 不产生任何数据文件；不触碰实测数据。

## 9. 完成定义与回退方式

- 完成定义：① 失败测试优先（红灯证据）→ 实现 → 绿灯；② 定向 15+ 用例全过；③ `verify.py` 全量非硬件 + ruff + mypy + import 全绿；④ `git diff --check` 干净；⑤ M03 ISSUE-015 状态行置 `Review`；⑥ 本计划文件含执行日志与门禁数字；⑦ 不 commit/push/merge、不创建分支；⑧ changedPaths == inScope 4 个精确路径。
- 回退方式：全部改动为新增文件 + 1 行状态行编辑；如遇阻断性问题，删除新增文件与状态行编辑即可完全回退（不覆盖任何既有文件）。

---

## 执行日志（t2 实际执行，追加记录）

### E1 开工与红灯（失败测试优先）

- M03 L7 `Planned → In progress` 已登记。
- 先写 `tests/contract/test_acquisition_backend.py`（27 个用例），在实现前运行定向 pytest：
  - 红灯证据（实现前）：`.venv/Scripts/python.exe -m pytest tests/contract/test_acquisition_backend.py -q`
    → `ModuleNotFoundError: No module named 'uav_gpr.acquisition.backend'`（1 error during collection，实现前定向测试失败）。

### E2 最小实现

- 实现 `src/uav_gpr/acquisition/backend.py`：`BackendState`/`Capabilities`/`AppliedConfig`、`AcquisitionBackend`（ABC 严格生命周期：open/configure/acquire/cancel/close、connection_generation、acquiring、acquire_started 事件）、`BackendError` 家族（DomainError + 稳定 reason）、`SimulationFaults`、`SimulatedBackend`（seed+config 派生 rng、注入 Clock、raw hash、GNSS/无 GNSS、故障注入与可取消等待）。
- 实现期修正两处核心 API 用法（非范围外改动）：`MissionConfig.frequency_axis_hz` 与 `ConfigDiff.is_identical` 是属性（非方法），同步修正实现与测试。

### E3 绿灯与门禁

- 绿灯证据：`.venv/Scripts/python.exe -m pytest tests/contract/test_acquisition_backend.py -q` → **27 passed in 0.19s**（全部用例，≥15 达标）。
- ruff：`.venv/Scripts/python.exe -m ruff check src tests` → 初查 3 项（UP012/F401/RUF100）经 `ruff --fix` 修复后 **All checks passed!**（exit 0）。
- mypy：`.venv/Scripts/python.exe -m mypy src` → **Success: no issues found in 36 source files**（exit 0）。
- 全量门禁：`.venv/Scripts/python.exe tools/quality/verify.py`（Python 3.13.14 venv，Windows 解释器经 WSL 调用）→ **589 passed, 1 deselected in 253.79s**（基线 562 + 本 Issue 27 = 589 ✓）；ruff `All checks passed!`；mypy `Success: no issues found in 36 source files`（基线 35 + backend.py = 36 ✓）；`package import ok`；`[quality] all gates passed`，exit 0。
- `git diff --check`：clean（exit 0）。
- 工作树快照（`git status --porcelain=v1 -b`）：仅新增/修改本 inScope 4 个精确路径 + t1 基线单；ISSUE-013/014 在制产物（M02 两处状态行 + 11 项未提交文件）全程未触碰；无新缓存/日志/实测数据残留。
- M03 L7 `In progress → Review`（2026-08-30，等待独立复审）。

### E4 复审修复（repair round 2，2026-08-30，t4）

- 修复范围（仅 P1-01，其余 P3 不修，见下）：
  - **P1-01 关闭**：`configure()` 持锁段增加在途 acquire 守卫——`if self._acquiring: raise BackendStateError(..., busy=True)`（与 `acquire()` 在途守卫同型）。红灯证据（修复前）：新增回归测试 `test_configure_rejected_while_acquire_in_flight` → `Failed: DID NOT RAISE BackendStateError`（1 failed in 0.12s）；绿灯证据（修复后）：**28 passed in 0.26s**（原 27 + 新 1）。测试同时验证：在途重配被结构化拒绝（`reason=illegal_state`、`busy=True`、`operation=configure`）、原任务状态不变（在途 acquire 不受影响、cancel 后正常收尾、随后 configure/acquire 恢复）。
  - **测试自终止保证**：该回归测试全程有界等待——`acquire_started.wait(2.0)` 确认在途 → 断言 configure 拒绝 → `finally: cancel() + thread.join(2.0)` 唤醒并回收线程；红灯（修复前）与绿灯（修复后）均在 <0.3s 内退出进程（`timeout 60/120` 硬超时复验：单测 1 passed in 0.10s、全文件 28 passed in 0.23s，无挂死）。早期一次红灯复跑曾因失败路径未达 cancel 而滞留进程（harness 120s 超时终止），已通过 try/finally 结构根除；修复后不再有未唤醒线程路径。
- P3 处理（不阻止合并，默认不修，仅文档化）：
  - P3-01（close→open 唤醒误分类）：backend.py `open()`/`close()` docstring 文档化『重开必须在在途 acquire 全部终止后』（close 唤醒后 join 再 open）；未引入 close 纪元（epoch）判别。
  - P3-02（同 mission 重配 trace_uid 重复）：backend.py `configure()` docstring 与本计划 §5.2 显式声明『重配视为新任务，调用方必须更换 mission_id（ISSUE-017/043 契约）』。
  - P3-03（open 钩子失败不回滚）：本计划 §5.2 标注留给 ISSUE-019（LibreVNA 传输层）处理，本 Issue 不实现。
- 门禁复跑（修复后）：定向 28 passed（0.13s）；verify.py 全量 **590 passed, 1 deselected**（253s 级；基线 562 + 本 Issue 27 + 回归用例 1 = 590 ✓）→ ruff/mypy/import 全绿；`git diff --check` clean；工作树快照仅含 inScope 4 路径 + t1 基线单 + 既有 ISSUE-013/014 在制产物。
- M03 ISSUE-015 状态行保持 `Review`（修复轮不改变复审状态；状态行已追加 round-2 修复完成标注）。
