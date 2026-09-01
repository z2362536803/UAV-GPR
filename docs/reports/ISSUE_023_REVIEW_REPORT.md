# ISSUE-023 独立复审报告

日期：2026-09-02
审查者：reviewer（AgentTeams `uav-gpr-issue-023-librevna-reconnect`，任务 t7，attempt 645e3c34-d259-412f-8b94-5c2166652cc4）
审查对象：t6（t4b，9 路径 inScope 契约版）的 ISSUE-023 交付（承接被取消的 t5 契约）
依据：docs/ISSUE_REVIEW_STANDARD.md v1.0；AGENTS.md；docs/INDEX.md；docs/issues/README.md；docs/issues/M04_LIBREVNA.md（ISSUE-023，L153–188）；docs/ACQUISITION.md；docs/PERFORMANCE.md；docs/TESTING.md；t1 基线单（docs/reports/ISSUE_023_BASELINE_CONFIRMATION.md）；t6 计划文档（docs/plans/2026-09-02-issue-023-librevna-reconnect.md）
审查性质：全程只读。未修改任何实现/测试/计划/文档/M04/Git 状态；未 commit/push/merge/clean；变异探针在系统临时目录运行并已清理，项目内零残留；审查前后 `git status --porcelain=v1` 逐字节一致。

## 1. 审查结论

**VERDICT：FAIL（needs_revision）—— 2 项 P2 缺陷需最小修复后复审；无 P0/P1；硬件验收保持 BLOCKED**

- 模拟断开/重连（不重复 trace、不沿用未确认配置、generation 严格递增）、退避、controller pause/resume/stop 与 in-flight 协作、benchmark 工具、双重 opt-in 硬件测试主体真实交付并通过独立复跑（911 passed/4 deselected、定向 58 passed、ruff/mypy/import/diff-check 全绿、benchmark 各模式退出码实测）——交付物整体真实、完整、合规，无伪造、无隐藏失败。
- 但存在 2 项已复现/已定位到行的 P2 缺陷（见第 3 节）：P2-1 重连退避期间 emergency_stop 终态 FAILED（状态机契约偏离，变异探针 A 实测复现）；P2-2 真机矩阵测试 cell 的 start/stop 频率未真正应用于配置（真机补跑时将产生与实测不符的矩阵记录）。二者按质量门禁（未解决的 high 缺陷不得判 pass）与 ISSUE_REVIEW_STANDARD §12（存在错误实现时不得判 PASS）判定为阻止合并，需按第 10 节最小修复清单修复后复审；修复量很小（C1/C2 各约 3–5 行 + 配套失败测试），预计一个修复轮次即可关闭。
- ⚠️ 硬件验收矩阵项按硬性约束标 **BLOCKED（等待真机）**：本环境无指定真机（t1 §3.5-2 已实测），任何路径未伪造真机数字（实测 `--backend hardware` 无设备 → `status:"blocked"` + exit 3、硬件测试 `pytest.skip("BLOCKED: …")`）。复审通过并合并后 M04 状态行由 captain 标 **Blocked（等待真机）** 而非 Done。
- 3 项 P3（见第 3 节）不阻止合并，随修复或后续 Issue 顺带处理。

## 2. 自动识别的审查范围

| 项 | 识别结果 | 证据 |
|---|---|---|
| Issue | ISSUE-023：LibreVNA 重连、暂停恢复与硬件基准 | M04_LIBREVNA.md L153–188（状态行现为 Review） |
| 依赖 | ISSUE-017/021/022 全部 Done 合入 main | git log：`1ceca4e`、`82d1c3b`+`7af5403`+`9d55533`、`18ec076`+`9972a9c`+`8d795d5`（HEAD）；M03 L81、M04 L81/L118 状态行 |
| 基线 | `main` @ `8d795d5a40932158d68d6a47a878d26e280c1675` | HEAD=origin/main 0/0；reflog 顶层仅 commit/merge/checkout，无 reset/rebase/amend/强推迹象 |
| 工作形态 | 本轮交付为**未提交工作树改动**（未 commit/push/merge，未创建分支）——沿用 ISSUE-019～022 流水线：审查通过后由 captain 统一提交合并 | git status 实测 |
| inScope | **9 个精确路径**（原团队目标 7 路径经 captain 两次裁决扩展：SC-1 加入 `tests/unit/test_no_external_access.py`、SC-2 加入 `tests/unit/test_quality_gates.py`，均记录于计划文档 §4 D9 / §8） | 计划文档 §2/§8；任务契约 |
| changedPaths | 与 inScope **逐一相等**（9=9）：5 modified + 4 untracked；工作树另含 t1 基线单（`docs/reports/ISSUE_023_BASELINE_CONFIRMATION.md`，t1 交付物，不计入 t6） | git status 实测比对 |

9 个 inScope 路径（逐一核对，无 glob、无范围外改动）：

1. `src/uav_gpr/acquisition/librevna/reconnect.py`（新模块，184 行）
2. `src/uav_gpr/acquisition/librevna/backend.py`（扩展 +144/−5）
3. `tests/contract/test_librevna_backend.py`（扩展 +348/−6，新增 10 测试）
4. `tests/hardware/test_librevna_hardware.py`（新文件，226 行，3 项 `@pytest.mark.hardware`）
5. `tools/benchmark/librevna_benchmark.py`（新文件，402 行）
6. `tests/unit/test_no_external_access.py`（SC-1：硬件目录守卫期望集 +1 文件）
7. `tests/unit/test_quality_gates.py`（SC-2：3 处 `'1 skipped'`→`'4 skipped'`）
8. `docs/plans/2026-09-02-issue-023-librevna-reconnect.md`（计划文档）
9. `docs/issues/M04_LIBREVNA.md`（仅 ISSUE-023 状态行 L155：Planned→Review）

排除项核实（计划 §3）：未改 `core/**`、`acquisition/backend.py` 基类、`acquisition/controller.py`、`librevna/transport.py`、`librevna/stream.py`、`librevna/__init__.py`、`docs/ACQUISITION.md`、`docs/PERFORMANCE.md`、`docs/TESTING.md`、`docs/adr/**`、参考仓库——git status 与 diff 实测无此类改动。未进入 ISSUE-024。

## 3. 主要问题（按 P0→P3）

无 P0 / P1。

### P2-1 重连退避期间 emergency_stop 终态为 FAILED 而非 STOPPED/EMERGENCY

- 位置：`src/uav_gpr/acquisition/controller.py` L827–837（`_handle_disconnect` hook 失败路径，冻结面，t6 未改）与 `src/uav_gpr/acquisition/librevna/reconnect.py` L154–184（hook 正确抛 `BackendCancelledError`，非缺陷）。
- 触发条件：worker 在重连 hook 的退避 `wait`（默认 `time.sleep`，最长一个退避间隔 ≤8s）期间，操作者调用 `controller.emergency_stop()` → `backend.cancel()` 置位 → hook 在下一循环顶抛 `BackendCancelledError` → `_handle_disconnect` 无 closing/STOPPING 守卫 → `_fail(ControllerFailure("reconnect hook failed"))` → 终态 **FAILED**（`stop_reason` 仍为 EMERGENCY，错误信息误导）。acquire 循环中的同类路径（L795–796、L800–801）有守卫，唯独 hook 路径没有。
- 实际影响：无数据丢失（`_fail` 关闭 backend、已完成 sweep 保留、无重复 trace、无泄漏）；停止被延迟至多一个退避间隔；终态标签违反 ISSUE-017 状态机契约（emergency_stop 应 STOPPED/EMERGENCY）且错误原因误导（“reconnect hook failed”实际是用户停止）。
- 复现证据：变异探针 A（系统临时目录运行、已清理）——事件门控 wait 注入，断开→hook 进入退避→`emergency_stop()`→终态实测 `state=failed, stop_reason=emergency, error=ControllerFailure: reconnect hook failed, backend_gen=2`。
- 违反要求：ACQUISITION.md §9（emergency stop 语义）；ISSUE-017 冻结状态机；t1 基线测试矩阵“断开期间 pause/stop 语义”为必测反例（t6 未覆盖此组合）。
- 最小修复方向：见第 10 节 C1（需 captain 授权动 controller.py 冻结面，或先记录为已知限制并排期）。

### P2-2 硬件测试矩阵 cell 的 start/stop 频率未真正应用于配置

- 位置：`tests/hardware/test_librevna_hardware.py` L180–183（cell 解包 `start_hz, stop_hz, points, ifbw_hz` 后仅 `_make_config(points=points, ifbw_hz=ifbw_hz)`）与 L90–110（`_make_config` 硬编码 100–200 MHz，无 start/stop 参数）。
- 触发条件：真机在场时执行 `test_hardware_benchmark_matrix_report_structure`——cell 2 报告 `start_hz=100e6/stop_hz=500e6`，实际测量配置仍为 100–200 MHz。
- 实际影响：将来真机补跑矩阵时产生**与实测配置不符的矩阵记录**（违背“矩阵报告包含配置”的诚实口径，PERFORMANCE.md §3/§6、M04 L177）；本环境未执行（无真机，测试 skip），故当前不产生假数字。
- 违反要求：M04 L177 真机矩阵报告含配置；PERFORMANCE.md §6 基准输入固定并记录。
- 最小修复方向：见第 10 节 C2（为 `_make_config` 增加 `start_hz/stop_hz` 参数并按 cell 传入）。

### P3-1 重连未复核设备身份（serial）绑定

- 位置：`src/uav_gpr/acquisition/librevna/backend.py` L793–795（`reconnect_session` 重读 `_device_info` 但未更新/比对 `_device_id`）。
- 影响：重连窗口内若接入不同 LibreVNA，配置会针对新设备重新校验（安全），但后续 trace 元数据仍携带原 `_device_id`（`_finalize_sweep` L1067 用 `self._device_id`）。与 `_do_open` 既有的“不比对 serial”模式一致（ISSUE-021 遗留），非 t6 新引入。
- 建议：重连成功后将 `info.serial` 与 `_device_id` 比对（fail-closed）或更新；可在真机可用时随 ISSUE-060 一并硬化。

### P3-2 P3-03 注释要求记录进 docs/ADR，计划 D1 仅记录于计划文档

- 位置：`controller.py` L838–844（冻结注释：“definitive generation semantics … must be recorded in docs/ADR”）；计划文档 §4 D1 记录了语义但未落 ADR。
- 影响：与 t1 基线 §3.4-1 的裁决（记录于计划文档即可，仅改变 controller 语义才需 ADR）一致，不违规；但代码注释字面仍指向 docs/ADR。
- 建议：待 captain 决定——(a) 固化一份简短 ADR 关闭 P3-03，或 (b) 授权微调该注释指向计划文档。

### P3-3 计划文档 §2 标题仍写“8 个精确路径”

- 位置：计划文档 §2 标题（SC-2 裁决后应为 9；§8 执行日志已记录 SC-2）。纯文档滞后，不影响契约（changedPaths=inScope=9 实测成立）。
- 建议：随 C1/C2 修复提交顺带更正。

## 4. 逐 Issue 验收矩阵

| # | 验收标准 | 状态 | 代码证据 | 测试证据（本次独立复跑） |
|---|---|---|---|---|
| 1 | 模拟断开/重连不重复 trace | **PASS** | `backend.py` L756–844 `reconnect_session`（保留 `_trace_index/_prev_start_mono`，仅 `_do_open/_do_configure/_enter_fail_closed` 重置计数）；`_finalize_sweep` L1082 继续递增 | `test_reconnect_session_preserves_trace_and_bumps_generation`：trace_index 0→1、uid 不同、traces==2；`test_controller_reconnect_hook_librevna_continues_without_duplicate_trace`：controller 级 0→1 无重复；定向 58 passed 实测 |
| 2 | 模拟断开/重连不沿用未确认配置 | **PASS** | `reconnect_session` 重读 DEVICE_INFO→`_validate_config`(L802)→`_quantize_config`→`_verify_contract_tolerance`→重发 SWEEP_SETTINGS；失败路径 fail-closed 清 `_applied`（L845–857）；重连后首道轴门禁 `_require_axis_verify`（L994–1001） | `test_reconnect_session_reapplies_axis_gate_on_first_sweep`（偏移 500 Hz 拒绝、无 trace）；`test_reconnect_session_failure_fails_closed_without_resetting_trace`；**变异探针 B**：重连遇窄频设备（max 150 MHz）→ `BackendConfigRejectedError`、generation 不 bump（=2）、transport 释放、重试成功后 trace_index 1/gen 3——配置重确认链完整 |
| 3 | connection_generation 重连后增加 | **PASS** | 断开 `_bump_generation`（L699–700）→ 重连成功再 bump（L842）：1→2→3 严格递增；失败不 bump；controller `_handle_disconnect` L845–848 校验 generation 改变 | 同上两测试：gen==2 断开后、==3 重连后；`test_reconnector_exhaustion_raises_structured_error` gen==2 不 bump；`test_reconnector_retries_with_backoff_then_succeeds` gen==3 |
| 4 | 退避策略（确定性、构造校验） | **PASS** | `reconnect.py` L57–103：`delay_after_failed_attempt = min(initial×factor^(n−1), max)`；`__post_init__` 校验非法值；无抖动 | `test_reconnect_policy_delays_are_exponential_and_capped`（0.5/1.0/2.0/4.0 封顶、4 类非法构造拒绝）；reconnector 测试实测 waits==[0.1, 0.2]（注入 no-op wait，无固定 sleep） |
| 5 | reconnector 重试/耗尽/取消语义 | **PASS** | `reconnect.py` L154–184：循环顶查 `cancel_requested`；`BackendCancelledError/BackendClosedError` 直接传播；耗尽 → `LibreVnaReconnectError(_reason="reconnect_failed")` | `test_reconnector_retries_with_backoff_then_succeeds`、`test_reconnector_exhaustion_raises_structured_error`（attempts==3）、`test_reconnector_propagates_cancellation`——3 项全绿 |
| 6 | controller pause/resume/stop 与 USB in-flight 安全协作 | **PARTIAL** | backend 无线程、`_raise_if_interrupted`/传输取消沿 ISSUE-021 冻结面；hook 正确传播取消（reconnect.py L172–173） | `test_controller_pause_resume_stop_librevna_backend_no_leak`（pause 无新 sweep、resume 续采、trace_index 无重复、worker join、adapter closed）；`test_controller_emergency_stop_interrupts_in_flight_librevna`（STOPPED/EMERGENCY、无发布）——均 PASS。**缺口**：重连退避期间 emergency_stop → 终态 FAILED（P2-1，探针 A 实测）；原因在冻结面 controller.py L827–837，t6 无权改。主路径安全（无泄漏、无重复、数据保留），终态标签不符 |
| 7 | benchmark 工具可复现（smoke/错误率/字段） | **PASS** | `tools/benchmark/librevna_benchmark.py`：simulated 确定性（同 seed+config）；`SimulationFaults(timeout_at=range(N))` 确定性注入；报告含 p50/p95/p99/mean/max、model_overhead、error_rate、cpu_ratio、config、environment(commit/python/platform/numpy)；`--smoke` CI 预设 | 实测：smoke exit 0×2（两次静态字段逐字节一致、environment 一致）；`--inject-timeouts 2 --sweeps 3` → error_rate **0.4**（failed=2/completed=3，确定性）；字段齐全（percentile 五键、8 个 results 键） |
| 8 | hardware 路径双重 opt-in + 无设备 BLOCKED 不伪造 | **PASS** | 工具：`--hardware`+`UAV_GPR_HARDWARE_OPTIN=1` 双闸（L259–267），无设备/缺 pyusb → `_blocked_report(status:"blocked")` exit 3（L302–317）；测试：`@pytest.mark.hardware`×3 + conftest 收集期双闸 + `pytest.skip("BLOCKED: …")` + `UAV_GPR_DEVICE_ID` 自检 | 实测（Python subprocess 驱动，规避本环境 bash `$?` 不可靠）：无授权 → **exit 2**；授权+无设备 → **exit 3**、`status:blocked`；`pytest tests/hardware`（无 opt-in）→ **4 skipped**；默认收集 **915 collected / 4 deselected**（1 哨兵+3 硬件）；AST 守卫更新后 3 passed |
| 9 | 真机矩阵报告含硬件/固件/配置/commit + p50/p95/p99 | **BLOCKED（等待真机）** | 工具 `run_hardware`/`_blocked_report` 与硬件测试 L154–226 已实现报告结构（hardware{firmware,protocol}/config/commit/p50/p95/p99） | 本环境无指定真机（t1 §3.5-2：Windows 宿主 159 PnP 设备无 VID 0x1209/PID 0x4121）；实测所有真机路径诚实 BLOCKED（skip/exit 3），未伪造任何真机数字。⚠️ 结构件存在 P2-2（cell 频率未应用），真机补跑前必须修复 |
| 10 | 没有指定真机时 Issue 保持 Blocked，不伪造完成 | **PASS** | M04 L155 状态行为 Review 且注明“硬件验收标 Blocked（等待真机）而非 Done”；计划 §7；工具 `--hardware` 输出 `device_present:false` 而非数字 | 实测退出码/`status:blocked`/skip 消息与文档一致；t6 报告未声称 Issue Done。合并后由 captain 标 Blocked（等待真机） |
| 11 | 依赖回归（ISSUE-017/021/022 冻结面不破坏） | **PASS** | 未改 controller/transport/stream/基类；backend 扩展仅新增方法与 1 标志 | 全量 **911 passed, 4 deselected in 282.83s**（915 collected）；定向 `test_librevna_backend.py` **58 passed**；controller **88 passed**、quality gates **12 passed**、no-external-access **3 passed** 实测；t1 基线 901+1 → 现 911+4 口径一致（+10 契约测试） |
| 12 | 排除项不越界 | **PASS** | 无 HDF5/网络最小间隔、无飞行验收、无 S21/S12/校准、未进入 ISSUE-024；diff 仅 9 路径 | git status/diff 实测；`test_quality_gates.py` L211 哨兵断言未动（“1 passed”+HARDWARE_SENTINEL_RAN） |

## 5. Git 与交付检查

- 当前分支 `main`，HEAD=origin/main=8d795d5，共同祖先明确；本批改动为**未提交工作树状态**（0 commits），未 commit/push/merge、未创建/切换分支——与 t6 报告及流水线设计（captain 复审后统一提交合并）一致；CONTRIBUTING 的“独立分支开发”在本自动化流水线下由 captain 合并步骤落地（ISSUE-019～022 同口径），如实记录，不视为违规。
- reflog 顶层仅 commit/merge/checkout（`8d795d5 commit ← 9972a9c merge ← …`），无 reset/rebase/amend/强推迹象。
- 工作树仅含：9 个 inScope 路径 + `docs/reports/ISSUE_023_BASELINE_CONFIRMATION.md`（t1 交付物）。无缓存、日志、构建物、密钥、实测数据、参考仓库文件进入工作树（`.pytest_cache/.mypy_cache/.ruff_cache/__pycache__` 均 git check-ignore 已忽略，审查前后 git status 逐字节一致）。
- `git diff --check` clean。
- 一个提交是否混入多个 Issue：本批无提交；改动内容全部对应 ISSUE-023（守卫/哨兵断言为 SC-1/SC-2 裁决的必要配套）。
- 公共契约变更：无 schema/协议/数据模型变更；新增能力（`reconnect_session`/`cancel_requested`/`LibreVnaReconnector`）为增量 API，未改变既有公共语义（`_finalize_sweep` 轴门禁条件为“trace_index==0 **或** require_axis_verify”，对既有会话行为不变）。

## 6. 测试与验证结果

环境：WSL Ubuntu / 仓库 `.venv`（`./.venv/Scripts/python.exe`，Python 3.12，与 t6 计划 §6 同口径）；t1 基线口径 `python3`（3.12.3）同步可用。

| 命令（实际执行） | 结果 | 退出码 |
|---|---|---|
| `./.venv/Scripts/python.exe -m pytest tests/contract/test_librevna_backend.py -q` | **58 passed in 0.35s**（= t6 声称 58） | 0 |
| `./.venv/Scripts/python.exe tools/quality/verify.py`（pytest -m "not hardware and not slow" + ruff + mypy + import） | **911 passed, 4 deselected in 282.83s**（t6 声称 284.44s，仅计时噪声，数字一致）；`All checks passed!`；`Success: no issues found in 43 source files`（= t6 声称 43 文件）；`package import ok`；`[quality] all gates passed` | 0 |
| `./.venv/Scripts/python.exe -m ruff check src tests tools` | All checks passed! | 0 |
| `./.venv/Scripts/python.exe -m mypy src` | Success: 43 source files | 0 |
| `git diff --check && git status --porcelain=v1 -b` | clean；仅 9 路径 + t1 基线单 | 0 |
| `pytest tests/unit/test_quality_gates.py tests/unit/test_no_external_access.py tests/contract/test_acquisition_controller.py -q` | **103 passed**（12+3+88；t6 声称 12 门禁自检、88 controller 回归成立） | 0 |
| `pytest tests/hardware -q`（无 opt-in） | **4 skipped**（双重 opt-in 收集期跳过） | 0 |
| `pytest --collect-only -q` | **915 tests collected**（t6 声称 915/911/4 口径一致） | 0 |
| benchmark：`--smoke`×2 | exit 0×2；两次静态字段与环境逐字节一致 | 0 |
| benchmark：`--inject-timeouts 2 --sweeps 3` | `error_rate = 0.4`（failed=2/completed=3，确定性复现 t6 数字） | 0 |
| benchmark：`--backend hardware`（无授权） | “double opt-in required” | **2**（t6 声称 2） |
| benchmark：`--backend hardware` + `UAV_GPR_HARDWARE_OPTIN=1`（无设备/pyusb） | `status:"blocked"` + `blocked_reason`（USB runtime dependency missing） | **3**（t6 声称 3） |

变异探针（系统临时目录 `/mnt/d/tmp` 运行、`python3 -B` 无字节码、已清理，项目零残留）：

- 探针 A（controller 级 emergency_stop × 重连退避，事件门控 wait 无固定 sleep）：终态 `failed / stop_reason=emergency / error=ControllerFailure("reconnect hook failed")` → **发现 P2-1**。
- 探针 B（重连遇窄频设备→重试成功）：`BackendConfigRejectedError`（“frequency range outside the device range”）、generation 不 bump（2）、transport 释放、重试后 generation 3、trace_index 1、uid 不重复 → 配置重确认与计数保留链**通过**（补 t6 未直接覆盖的“重连中途失败→重试成功”组合）。

补查结论：t6 未覆盖但需求明确要求的关键反例中，配置重确认/计数保留/退避/generation/benchmark 可复现/opt-in 默认跳过/守卫断言均已通过（上表 + 探针 B）；唯一缺口为“断开期间 stop 语义”中的 emergency×backoff 组合（探针 A，P2-1）。

## 7. 报告与事实差异

逐项核对 t6 完成报告与计划 §8 执行日志的声称：

| 声称 | 核对结果 |
|---|---|
| 定向 58 passed | ✅ 独立复现 58 passed |
| 全量 911 passed / 4 deselected（284.44s） | ✅ 独立复现 911 passed / 4 deselected（282.83s，计时噪声） |
| 915 collected / 4 deselected | ✅ 独立复现 915 collected、4 deselected |
| ruff All checks passed（src tests tools） | ✅ 独立复现 |
| mypy 43 文件 | ✅ 独立复现 |
| git diff --check clean、工作树仅 9 路径（+t1 基线单） | ✅ 独立复现 |
| benchmark exit 2（无授权）/ exit 3（无设备 blocked）/ error_rate 0.4 / smoke exit 0 | ✅ 全部独立复现（Python subprocess 驱动） |
| 红灯证据（ModuleNotFoundError） | 计划 §8 记录；代码已绿，红灯状态无法在不回退的情况下重放——标“未发现反证”，按过程声明采信 |
| M04 状态行 Review、未声称 Done | ✅ 实测一致 |
| 硬件验收 BLOCKED 不伪造 | ✅ 实测：无设备全部路径输出 blocked/skip，无任何真机数字 |

未发现隐藏失败、跳过、占位、范围偏离或把模拟结果写成真机结果的情况。

## 8. 剩余风险

1. **真机矩阵未跑**（BLOCKED）：p50/p95/p99、硬件/固件/commit 真实数字缺失；重连/暂停恢复行为仅经协议夹具验证，真机 USB 时序（拔插、枚举延迟、固件重连行为）待真机补跑。合并后 M04 必须保持 Blocked 而非 Done。
2. **P2-1 窗口**：重连退避期间 emergency_stop 终态 FAILED（误导性错误信息）；有界（≤一个退避间隔）、无数据丢失，但状态机语义不符。修复需动冻结面 controller.py。
3. **P2-2**：真机补跑前若不修复，矩阵报告将记录未实测的频率配置。
4. 重连未复核设备 serial 绑定（P3-1）；P3-03 注释指向 docs/ADR 尚未落 ADR（P3-2，基线裁决允许）。
5. 默认 `wait=time.sleep` 的取消响应粒度为一个退避间隔（≤8s）——已在 P2-1 修复方向内一并处理。

## 9. 合并建议

- **暂不合并（needs_revision）**：P2-1/P2-2 修复并复审通过前，9 个 inScope 路径不得进入自动合并——按质量门禁，未解决 high 缺陷不得判 pass。
- 修复范围仅 C1/C2（见第 10 节，各约 3–5 行 + 配套失败测试），不涉及模拟核心语义；C3–C5 为 P3，可随修复批或后续 Issue 顺带。
- 复审通过后由 captain 执行合并动作：提交（或按 ISSUE-019～022 口径建分支后合入）并推送；M04 状态行按口径改为 **Blocked（等待真机）** 而非 Done。
- 不建议拆分 ISSUE-023（模拟与工具互为验收整体；硬件 BLOCKED 为环境缺失，不是缺陷拆分理由）。

## 10. 最小修复清单

| 编号 | 问题 | 最小修复 |
|---|---|---|
| C1（P2-1） | emergency_stop × 重连退避 → FAILED | 方案 A（推荐，需 captain 授权动冻结面）：`controller.py::_handle_disconnect` 的 hook 异常路径加守卫——`closing or state is STOPPING` 时直接 return（与 L795–796、L800–801 同型），并在 `reconnect.py` 的 `wait` 默认改为可取消等待（如 `backend._cancel_event.wait(delay)` 或注入分段 sleep）以消除 ≤8s 响应延迟；配套失败测试：探针 A 场景断言终态 STOPPED/EMERGENCY。方案 B（不动冻结面）：在计划/ADR 记录为已知限制，随下次 controller 表面变更修复。 |
| C2（P2-2） | 硬件矩阵 cell 频率未应用 | `tests/hardware/test_librevna_hardware.py`：`_make_config` 增加 `start_hz/stop_hz` 参数（默认 100e6/200e6），`test_hardware_benchmark_matrix_report_structure` 按 cell 解包值传入；配套断言 cell 报告的 start/stop 与 `applied.config` 一致。 |
| C3（P3-1） | 重连未复核设备身份 | `reconnect_session` 成功后比对 `info.serial` 与既有 `_device_id`（不一致 → fail-closed）或同步更新；可随真机可用期实施。 |
| C4（P3-2） | P3-03 注释与 ADR 落点 | captain 决定：固化 1 份 ADR（记录 generation 语义：断开 +1、重连成功 +1、失败不 bump、base 状态保持 CONFIGURED）或授权更正 controller 注释指向计划文档。 |
| C5（P3-3） | 计划文档 §2 标题 8→9 路径 | 随任一修复提交顺带更正。 |

审查结束。按 ISSUE_REVIEW_STANDARD.md §13 立即停止：不修改代码，等待项目负责人/captain 决定修复、拆分或合并。

---

# ISSUE-023 Round-2 复审（t11，2026-09-02）

审查者：reviewer（任务 t11，attempt 8ecf83b2-cb24-418e-b07d-4f687871a351）；审查对象：t10（t8b，repair round-2，6 路径 inScope 修复版）；依据：ISSUE_REVIEW_STANDARD.md §14 + 本报告第 10 节 C1–C5 + captain 对 C1 方案 A 的授权。全程只读；探针在系统临时目录运行并已清理；审查前后工作树仅新增本报告。

## R2-1. 审查结论

**VERDICT：PASS（round-2）**——2 项 P2 全部关闭（独立复验）、3 项 P3 全部处理、全部门禁独立复现、硬件验收保持 BLOCKED 不伪造、无 P0/P1/P2 遗留。可进入 captain 自动化合并；合并后 M04 状态行按口径标 **Blocked（等待真机）** 而非 Done。

## R2-2. 审查范围（自动识别）

- t10 changedPaths = **6 唯一路径**，与 inScope 逐一相等（SC-3 裁决，计划 §2 注）：（P2-1 守卫 + P3-03 注释，captain 授权动冻结面）、（wait_cancellable/_verify_device_identity/reconnect_session 集成）、（默认 wait 可取消）、（+3 测试）、（P2-2 cell 应用 + 一致性断言）、（§2 标题/D10/D11/round-2 日志）。
- 工作树其余条目为 t1/t6/t7 遗留（t1 基线单、t6 的 M04 与两个守卫文件、本复审报告），不计入 t10；实测 diff：controller +26、backend +194、contract 测试 +446（t6 的 +348 之上 +98）、hardware 测试新增 start/stop 参数化与断言、计划文档更新。
- 无范围外改动；未 commit/push/merge； clean。

## R2-3. 逐项核验（C1–C5 关闭证据）

| 项 | 状态 | 代码/测试证据（独立复验） |
|---|---|---|
| C1-a 停止竞态守卫 | **关闭** |  hook 异常路径新增  守卫后 return（同型 L795–801 的 cancelled/closed acquire 路径；不覆盖 FAILED， 对已 FAILED 为 no-op）；既有 hook 失败测试（test_acquisition_controller.py L879–943）仍走  语义，88 passed 无回归 |
| C1-b 可取消退避 wait | **关闭** |  L144 默认 （移除 ）；  =  +  + 正数校验 |
| C1-c 探针 A 失败测试 | **关闭** | 自动化测试 （断言 STOPPED/EMERGENCY/error None/adapter closed）；**独立探针 A′**（默认 wait、事件同步无固定 sleep）实测：state=stopped、stop_reason=emergency、error=None、停止延迟 0.0001s（<< 1.0s 初始退避）——取消即刻生效 |
| C2 矩阵 cell 应用 | **关闭** |  （L90–93）；cell 解包值传入（L188–194）；applied.config 四字段一致性断言（L209–215）与报告 cell 字段一致（L229–232）——真机补跑时矩阵记录与实测配置绑定 |
| C3 身份重验（P3-1） | **关闭（超出最小清单）** | （protocol/firmware/hardware_version/hardware_revision/num_ports 不一致 →  fail-closed）+  集成；测试 （gen 不 bump=2、traces=1 保留、adapter closed）；**独立探针 B′** 复验通过；serial↔device_id 绑定待真机期（D10 已如实记录，协议 v14 DEVICE_INFO 无 serial） |
| C4 P3-03 注释（P3-2） | **关闭** |  P3-03 注释改写：物理重连 generation 语义已记录于计划 D1（不再指向 docs/ADR） |
| C5 计划标题（P3-3） | **关闭** | 计划文档 §2 标题更新为“9 个精确路径”（L20），round-2 6 路径子集以注记说明 |

## R2-4. 验收矩阵（round-2 增量）

| 验收项 | 状态 | 证据 |
|---|---|---|
| P2-1 关闭（终态 STOPPED/EMERGENCY + 可取消 wait + 回归不破坏） | **PASS** | 探针 A′ + 自动化测试 + controller 88 passed + 定向 61 passed |
| P2-2 关闭（cell 频率真实应用 + applied 一致性断言） | **PASS** | 代码 L188–215（真机执行仍 BLOCKED，结构已固定） |
| P3-1/P3-2/P3-3 处理 | **PASS** | R2-3 表 C3/C4/C5 |
| 硬件验收 BLOCKED 口径保持 | **PASS** | 918 collected/4 deselected、 无 opt-in 4 skipped、benchmark +env 无设备 exit 3  实测；无任何真机数字 |
| 全量门禁 | **PASS** | 见 R2-5 |

## R2-5. 测试与验证结果（独立复现）

环境：仓库 （，Python 3.12；与 t10 同口径）。

| 命令 | 结果 | 退出码 |
|---|---|---|
|  | **61 passed in 0.37s**（= t10 声称 61 = 58 + 3 新增） | 0 |
|  | **88 passed in 3.54s**（= t10 声称，冻结面回归无破坏） | 0 |
| （pytest -m "not hardware and not slow" + ruff + mypy + import） | **914 passed, 4 deselected in 286.32s**（= t10 声称 914/4，284.13s 为计时噪声）；；；； | 0 |
|  | All checks passed! | 0 |
|  | Success: 43 source files | 0 |
|  | clean | 0 |
|  | **918 tests collected**（= t10 声称；914+4 口径） | 0 |
| （无 opt-in） | 4 skipped（双重 opt-in 收集期跳过） | 0 |
| benchmark： | exit 0（回归不变） | 0 |
| benchmark： + env（无设备） | （回归不变） | 3 |
| 探针 A′ / B′（临时目录，，已清理） | STOPPED/EMERGENCY/error None/0.0001s；身份拒绝/无 bump/计数保留 | 0 |

红灯→绿灯过程声明（计划 round-2 日志：探针 A + 身份拒绝 + wait_cancellable 缺失 = 3 failed → 61 passed）：代码已绿，红灯状态无法不回退重放，标“未发现反证”。

## R2-6. 报告与事实差异

t10 声称的 61/88/914+4/918/ruff/mypy 43/import/diff-check/changedPaths 6 路径——全部独立复现，无差异。硬件 BLOCKED 口径一致，无伪造。M04 状态行保持 Review 未声称 Done。

## R2-7. 剩余风险

1. 真机矩阵（p50/p95/p99、硬件/固件/commit）仍 BLOCKED（无指定真机）——重连/暂停恢复仅经协议夹具验证，真机 USB 时序待真机补跑；合并后 M04 标 Blocked 而非 Done。
2. 设备身份 serial↔device_id 绑定待真机期实施（D10 已记录，协议 v14 DEVICE_INFO 无 serial 字段）。
3. P3-03 generation 语义记录于计划文档（D1），若后续需权威化可再落 ADR（非阻塞）。

## R2-8. 合并建议

**可合并**：round-2 关闭了全部 P2/P3，验收矩阵无 FAIL、无 P0/P1/P2，全部门禁独立复现。建议 captain 按自动化流水线执行：提交/合入 9 路径总改动（含 t6 与 t10 修复）并推送；合并后 M04 ISSUE-023 状态行标 **Blocked（等待真机）** 而非 Done；t3/t5 僵尸 pending 任务保持不动（删除团队时随行清理）。

审查结束。按 §13/§14 停止：不修改代码，等待 captain 合并。
