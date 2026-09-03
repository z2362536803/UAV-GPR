# ISSUE-028 实施计划：OSL/空采无 UI 参考采集服务

日期：2026-09-02
执行器：AgentTeams `uav-gpr-issue-028-osl-reference` 成员 engineer-2（任务 t2，attempt 78789eca-bded-42be-bde7-610887410a4d）
基线件：[docs/reports/ISSUE_028_BASELINE_CONFIRMATION.md](../reports/ISSUE_028_BASELINE_CONFIRMATION.md)（main @ `56c2f0f`，工作树干净，门禁 1086 passed / 4 deselected，依赖定向 59 passed）
目标 Issue：ISSUE-028（`docs/issues/M06_CALIBRATION_PROCESSING.md` L42–77）；约束文档：`AGENTS.md` §3/§6/§7/§9/§10/§12、`docs/CALIBRATION.md` §3/§4/§8/§9、`docs/ACQUISITION.md` §1/§2、ISSUE-015/017 采集契约、ISSUE-027 `osl.py` 消费面、t1 基线确认单 §3/§5。

## 1. 目标与用户价值

在 `calibration` 层交付**无 UI** 的参考采集会话：OSL 六步（Open/Short/Load × S11/S22 反射通道）显式状态机与空采（AirBackground）会话。会话冻结 sweep 配置（`MissionConfig` + 频率轴 + 通道 + 目标道数），通过 `accept_sweep` 严格校验（axis/channel/数据契约）后聚合；步骤收齐后委托 ISSUE-027 `build_osl_calibration` 构建 `OslCalibrationSet`（不复制求解数学）；空采会话按声明的 `raw`/`osl_calibrated` 域聚合出 `AirBackgroundReference`。可选 `ControllerReferenceAdapter` 只编排：复用 ISSUE-015/017 采集循环（SimulatedBackend/真机同一接口），目标道数收齐后**先关闭接受门再安全 stop controller**。它是 ISSUE-029（`.rcal/.rcbg` 持久化）与空采应用（后续 Issue）的直接依据。

## 2. 范围（任务契约 inScope = 4 个精确路径，changedPaths 与此逐一相等）

1. `src/uav_gpr/calibration/reference.py`（新模块：ReferenceCaptureSession 基类 + OslReferenceSession + AirBackgroundSession + ControllerReferenceAdapter）
2. `tests/contract/test_calibration_reference.py`（新文件：SimulatedBackend 驱动契约测试）
3. `docs/plans/2026-09-02-issue-028-osl-reference.md`（本计划文档，含执行日志）
4. `docs/issues/M06_CALIBRATION_PROCESSING.md`（仅 ISSUE-028 状态行：`Planned → In progress → Review`）

## 3. 明确排除项（M06 L54–56 + 提示词 + 任务契约）

不保存 `.rcal/.rcbg`（ISSUE-029）、不做 Qt wizard/任何 UI、不自动切换物理标准件（标准件接入始终是显式人工动作，会话只按步骤期待数据）、不复制硬件采集循环（复用 backend/controller 生命周期）、不改 `osl.py`/`core/**` 公共语义、不新增 core 错误码（复用现有 `ErrorCode`）、不改 `calibration/__init__.py`（占位原样）、不 commit/push/创建分支、不新增 inScope 之外文件。

## 4. 设计决策（D1–D10，2026-09-02 定案）

- **D1 两级状态机（会话级 + 步骤级，显式禁跳步/混配置）**：会话状态 `ReferenceSessionState`（`IDLE → RUNNING → COMPLETED/CANCELLED/FAILED`）；OSL 步骤为有序物理六步 `(channel, standard)`，按会话通道序 × (open, short, load) 展开，`ReferenceStepState`（`PENDING → RUNNING → COMPLETED/FAILED`）。**不提供 skip 方法**：步骤只在目标道数收齐时自动完成并推进，`build()` 前置校验全部步骤 COMPLETED（`INVALID_ARGUMENT`，kind=`incomplete_steps`）；`accept_sweep` 在 IDLE/CANCELLED/FAILED/门关闭时拒绝（accepted=False，reason 记录），契约违规（axis/channel/非有限）抛 `DomainError` fail-closed。步骤配置冻结于构造时（config/axis/channels/captures_per_step 全部只读），任何步骤间差异不可能发生（混配置在 accept 层被 axis/channel 严格相等拒绝）。
- **D2 accept_sweep 严格校验（冻结配置比对）**：期望轴 = `config.frequency_axis_hz`（逐点 `np.array_equal`）；期望通道 = 冻结通道元组逐位全等（ChannelSpec 全字段）；数据复数 dtype、形状 channel×frequency、全有限。违规 `AXIS_MISMATCH`/`CHANNEL_CONTRACT_MISMATCH`/`DTYPE_MISMATCH`/`SHAPE_MISMATCH`/`INVALID_ARGUMENT`（非有限）。通过后仅聚合并不对 sweep 数据做任何修改（sweep 本身不可变，会话保存聚合副本且数组 `setflags(write=False)`）。
- **D3 委托 I027（不复制数学）**：OSL 会话收齐某通道三标准件后，`build()` 对每通道调 `build_osl_calibration(channel=..., frequency_hz=轴, open/short/load_measured=(captures, frequency) 堆叠, *_actual=会话构造冻结的 Cal Kit 值或缺省理想值)`，返回 `OslCalibrationSet`。质量指标、奇异检测、profile_id 生成全部复用 I027。
- **D4 空采域声明（CALIBRATION §4）**：`AirBackgroundSession(config, target_traces, domain)`，`domain ∈ {raw, osl_calibrated}`；`osl_calibrated` 必须显式提供 `calibration_profile_id`（缺省即构造错误）。收齐产出 `AirBackgroundReference`（channels/axis/domain/profile_id/trace_count/逐通道复数均值，只读），**不做应用侧匹配**（应用属后续 Issue），仅携带足够匹配字段。
- **D5 关门顺序（先关接受门再 stop）**：`ControllerReferenceAdapter` 消费 `controller.sweeps`（Condition 驱动 `get(timeout)`，无固定 sleep），会话目标收齐自动关接受门（`accepting_gate=False`），adapter 记录事件序 `gate_closed → controller_stopped → controller_closed` 并暴露事件列表供测试断言；stop 后 drain 余量（in-flight 数据走关门拒绝语义，不计数不崩溃），`wait_finished` + `close` + 线程 join 断言无泄漏。
- **D6 重试/保留前序**：adapter 在 controller FAILED 时调 `session.record_step_failure(context)`：若当前步骤失败次数 ≤ `max_step_retries`，已完成步骤与已接受 captures 保留，adapter 用工厂新建 controller（同一冻结 config 重新 configure/start）继续当前步骤；超出预算则会话 FAILED（门关闭），adapter 走安全关闭。重试计数与失败上下文驻留会话（可观测）。
- **D7 取消/资源关闭**：`session.cancel()` 线程安全（内部 `threading.Lock`）：门关闭、状态 CANCELLED、唤醒 adapter（adapter 循环每轮检查会话终态）；adapter 随后 stop/join/close controller，无线程泄漏；取消后再 accept 一律拒绝。
- **D8 in-flight 语义**：门关闭/步骤完成后到达的 sweep 返回 `accepted=False`（reason=`gate_closed`/`session_terminal`），不抛异常不改变计数——in-flight 数据不能破坏状态机；硬契约违规仍抛错。
- **D9 无持久化/无伪造**：模块零文件 I/O、零 Qt 导入；不生成任何标准件测量值（所有测量数据必须来自 `accept_sweep` 实际接受的 sweep）；`__all__` 仅会话/适配器/枚举/结果与参考值对象。
- **D10 测试纪律**：SimulatedBackend + AcquisitionController + ManualClock/ManualWaiter 虚拟时间（ISSUE-016/017 既有模式，`advance_and_wake` 同步），全部等待为 Condition/Event 驱动或 join(timeout)，**无 time.sleep**；错误注入用 `SimulationFaults`（timeout/half_sweep/disconnect）。

## 5. 文件改动（精确路径，无 glob）

| 路径 | 动作 | 内容 |
|---|---|---|
| `src/uav_gpr/calibration/reference.py` | 新增 | 会话/适配器/枚举/结果与参考值对象；模块 docstring 含契约与错误码映射 |
| `tests/contract/test_calibration_reference.py` | 新增 | 契约测试矩阵（§6） |
| `docs/plans/2026-09-02-issue-028-osl-reference.md` | 新增 | 本文档（决策 + 执行日志） |
| `docs/issues/M06_CALIBRATION_PROCESSING.md` | 修改 | 仅 ISSUE-028 状态行 `Planned → In progress → Review` |

## 6. 测试矩阵（失败测试优先，先红灯后绿灯；虚拟时间确定性）

1. 构造校验：非反射通道 OSL 会话 `CHANNEL_CONTRACT_MISMATCH`；captures_per_step/target_traces 非法；空采 `osl_calibrated` 缺 profile_id 拒绝；枚举非法。
2. 门与状态机：未 start 时 accept 拒绝；start 后 accept 通过并计数；重复 start 拒绝；跳步（`build()` 未收齐）`INVALID_ARGUMENT`。
3. OSL 六步 happy path（SimulatedBackend + controller + adapter，双反射通道，captures_per_step=1 → 6 道）：产出 `OslCalibrationSet`（2 profile、通道绑定正确、capture 计数正确、质量有限非负）；事件序 `gate_closed` 严格先于 `controller_stopped`；controller join=True、state CLOSED；全程无 sleep。
4. 混配置拒绝：错误频率轴 / 非有限数据 → 对应 DomainError，accepted 计数不变。
5. in-flight：步骤目标收齐后与全会话完成后到达的额外 sweep → accepted=False（reason 记录），状态与计数不变；取消后同样拒绝。
6. 重试/保留前序：第一步中途 controller FAILED（SimulationFaults.timeout_at）→ record_step_failure → 新 controller 续采同一步 → 最终 set 构建成功、工厂调用恰 2 次、step_failure_count ≥ 1。
7. 设备错误超预算：重试预算耗尽 → 会话 FAILED、门关闭、adapter 安全关闭、join=True。
8. 取消：采集中 `session.cancel()` → 状态 CANCELLED、门关闭、adapter stop/join/close、随后 accept 拒绝、无线程泄漏。
9. 空采 happy path：raw 域 3 道 → `AirBackgroundReference` 均值逐通道逐频点等于输入均值；字段（channels/axis/domain/trace_count）正确；数组只读。
10. 空采校准域：`osl_calibrated` + profile_id → reference 携带 domain 与 profile_id。
11. 门禁：依赖定向 + 全量 `tools/quality/verify.py`（1086 + 新增 passed / 4 deselected）+ ruff + mypy + import + `git diff --check` + 工作树仅 inScope 4 路径。

## 7. 执行日志

- 2026-09-02（engineer-2，t2 attempt 3）：计划落盘（本节即框架）。按「红灯测试 → 最小实现 → 绿灯 → M06 状态行 → 全量门禁 → 登记」追加日志。
  - [x] §4 设计决策定案
  - [ ] 红灯：tests/contract/test_calibration_reference.py
  - [ ] 绿灯：src/uav_gpr/calibration/reference.py
  - [ ] M06 状态行 In progress → Review
  - [ ] 门禁复跑与登记

- 2026-09-02（engineer-2）：红灯（实现前）——`ModuleNotFoundError: No module named 'uav_gpr.calibration.reference'`，collection error（exit 2）。
- 2026-09-02（engineer-2）：实现初稿落盘后停摆（上下文耗尽，见 §7.1）；半成品 6 failed/8 passed。
- 2026-09-02（captain 接管 attempt 7，完整修复与验证）：
  - **修复 1（只读防御）**：`_readonly` 改为"只读基座 + 视图"（原实现返回自有数组，`setflags(write=True)` 可重开写；视图对只读基座重开写会抛 ValueError，测试 `test_air_background_reference_mean_and_readonly` 由红转绿）。
  - **修复 2（会话未启动）**：`ControllerReferenceAdapter.run()` 首行补 `session.start()`（原实现 gate 永闭、accept 全部拒绝；适配器驱动测试全部受影响）。
  - **修复 3（重试后门未开 + controller 未重建）**：重试分支 `_shutdown_controller` 置空 `self._controller` 后未重建（`assert controller is not None` 崩溃）且关闭的接受门未重开（sweep 全部 `accepted=False reason=adapter_controller_restarted`）；补 `self._factory()` 重建 + 新增 `_SessionBase.open_gate()` 重开接受门。
  - **修复 4（双通道 sweep 误拒）**：`OslReferenceSession._absorb_locked` 原按 `sweep.channels[0]` 与当前步骤通道比对并拒绝——双反射配置下每帧 sweep 携带全通道集（S11+S22 同行），该检查必然误拒；移除该检查（步骤线性推进由 gate 保证，行归属在 `build()` 按 channel_index 决定）。
  - **修复 5（测试 API 笔误）**：`controller.join(timeout=…)` → `join(timeout_s=…)`（4 处）；in-flight 测试语义对齐自动推进模型。
  - **修复 6（重试测试编排竞态）**：重试测试原依赖 `advance_and_wake` 手动唤醒与 adapter 重试时序竞态（首唤醒 10s 超时）；改为有界截止时间的事件驱动等待（轮询 session 终态，非固定 sleep）——适配器以自身虚拟时间节奏驱动健康 replacement 完成。
  - **修复 7（静态检查）**：未用导入（Mapping/Sequence）、循环变量、8 处盲 `pytest.raises(Exception)` → `DomainError`、mypy JsonValue 类型与 `_SessionBase.record_step_failure` 默认钩子。
  - 定向结果：**14 passed**（3.16–4.19s，4 次复跑稳定）；ruff `All checks passed!`；mypy `Success: no issues found in 48 source files`。
  - 全量门禁：`verify.py` exit 0 = **pytest 1100 passed / 4 deselected in 255.01s**（= 1086 基线 + 14 新增）+ ruff `All checks passed!` + mypy `Success: no issues found in 48 source files` + `package import ok` + `[quality] all gates passed`。

- 2026-09-02（captain，第二意见复审后修复批次，P2×1 + P3×4 全部关闭）：
  - **修复 8（P2-1 空采失败预算）**：`AirBackgroundSession` 增 `max_retries`（默认 3）构造参数与 `record_step_failure` 覆写（计数超预算 `_fail_locked()`）；新增回归 `test_air_background_failure_budget_fails_closed`（持续 timeout fault → 工厂恰 4 次调用后 state=FAILED、adapter 干净退出、join 全 True——原无界热重试风暴实测 3 秒 19,164 次工厂调用，现 bounded）。
  - **修复 9（P3-2 每步预算）**：`OslReferenceSession._after_accept_locked` 步骤推进时重置 `_step_failures = 0`（预算语义与命名/文档「per step」一致；原为会话累计，第 2 步首败即耗尽）。
  - **修复 10（P3-3 重试测试名副其实）**：`test_retry_after_device_error_preserves_prior_steps` 改 `timeout_at=(1,)`（第 1 道 open 采集成功后失败）+ 接受数据捕获（accept_sweep 包装）+ 断言 `profile.open_measured_mean` 与故障前接受的行逐位相等（前序保留 bit-exact）；c2 换 seed=0 使组合测量非退化；`step_failure_count` 终态断言移除（每步重置语义下终值为 0，重试事实由 calls==2 证明）。
  - **修复 11（P3-4 残留盲异常）**：`test_osl_session_rejects_non_reflection_channel` 的 `pytest.raises(Exception)` → `pytest.raises(DomainError)` 且断言 `code is ErrorCode.CHANNEL_CONTRACT_MISMATCH`。
  - **修复 12（P3-5 本行）**：终态门禁数字补录（上行）。
  - 修复后定向：**15 passed**（2.12–3.14s，3 次复跑稳定 = 14 + 1 新增回归）；ruff/mypy(48 files) 全绿；全量门禁见下。
  - 全量门禁（修复批次后）：`verify.py` exit 0 = **pytest 1101 passed / 4 deselected**（= 1086 基线 + 15）+ ruff + mypy(48) + import 全绿（数字与定向复跑一致口径，详见 t2 登记与复审报告）。

### 7.1 过程注记（透明）

- 执行端（xkiro glm-5.3-flash）在 t1 上下文耗尽、t2 半成品停摆（22:07–22:10 落盘后静默 80 分钟）；更换 deepseek-v4-flash 后其首条 pytest 命令挂死 15+ 分钟（进程 CPU≈0，已杀）；captain 按 ISSUE-014 先例接管完成 t2（t1 亦为 captain 完成）。半成品文件经 7 类修复后 14/14 全绿。
