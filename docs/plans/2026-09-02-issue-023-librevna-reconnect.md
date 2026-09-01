# ISSUE-023 实施计划：LibreVNA 重连、暂停恢复与硬件基准

日期：2026-09-02
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-023-librevna-reconnect`（执行器 engineer，任务 t4（t2b），attempt d070ce6e-c422-452b-aaff-0c39ea4c2d3e；承接被取消的旧 t2 attempt b1f9ea86）
基线：`main` @ `8d795d5a40932158d68d6a47a878d26e280c1675`（工作树干净、origin/main 同步 0/0）；权威基线件：[docs/reports/ISSUE_023_BASELINE_CONFIRMATION.md](../reports/ISSUE_023_BASELINE_CONFIRMATION.md)（t1）
配套：本计划为 t4 执行契约与 t3 复审依据；执行日志随执行过程追加（第 8 节）。⚠️ 范围冲突 SC-1（第 4 节 D9）已于 2026-09-02 由 captain 裁决：**新增 `tests/unit/test_no_external_access.py` 入 inScope（8 路径版），硬件目录守卫期望集更新为 `{test_hardware_sentinel.py, test_librevna_hardware.py}`**——本计划 §2 同步为 8 个精确路径。

## 1. 目标与用户价值

在已合入的冻结面上实现 ISSUE-023「LibreVNA 重连、暂停恢复与硬件基准」（M04 L153–188，直接依赖 ISSUE-017/021/022 均 Done）：

1. **物理重连路径**：设备断开/重连状态、确定性退避、`connection_generation` 递增、重新 configure/回读确认（不重复 trace、不沿用未确认配置）——作为 ISSUE-017 controller `reconnect_hook` 的实现（controller.py L821–861 冻结协作面 + P3-03 注释 L838–844 要求记录 generation 语义）；
2. **pause/resume/stop 与 USB in-flight 安全协作**：backend 无线程（AGENTS.md §7），in-flight 取消沿用 base `cancel`/`close` 契约，契约测试证明无泄漏、无重复 trace_index；
3. **可复现 benchmark 工具**（`tools/benchmark/librevna_benchmark.py`）：输出 sweep 率、写前模型开销、错误率、CPU 与目标配置；smoke 模式供 CI；
4. **双重 opt-in 硬件测试**（`tests/hardware/test_librevna_hardware.py`）：真机矩阵（频段×点数×IFBW×S11/双通道）执行入口，默认跳过、不枚举 USB；
5. ⚠️ **本环境无指定真机**（t1 §3.5-2：Windows 宿主 159 个 present PnP 设备无 VID 0x1209/PID 0x4121 匹配）：真机矩阵（p50/p95/p99 + 硬件/固件/commit 报告）标 **BLOCKED（等待真机）** 不伪造；模拟/工具部分完整交付并复审；M04 状态行最终由 captain 标 Blocked 而非 Done。

价值：M04 门禁「单一真机路径、严格组装和硬件基准完成」收尾；落实 ACQUISITION.md §9（pause/resume/stop/故障语义）/§10（重连后 `connection_generation` 增加且配置重新确认、暂停/恢复不重复 trace_index）、PERFORMANCE.md §1/2/3/6（p50/p95/p99、基准矩阵、环境与 commit 记录）、TESTING.md 硬件双重 opt-in（L54–71）。

## 2. 范围（任务契约 inScope = 9 个精确路径，SC-1+SC-2 裁决后）

1. `src/uav_gpr/acquisition/librevna/reconnect.py`（**新模块**）：`LibreVnaReconnectPolicy`（确定性指数退避）+ `LibreVnaReconnectError`（结构化错误）+ `LibreVnaReconnector`（controller `reconnect_hook` 实现：退避重试 `backend.reconnect_session`，取消传播，耗尽即结构化失败）。
2. `src/uav_gpr/acquisition/librevna/backend.py`（**扩展**，最小实现）：`reconnect_session(config)`——原地重建 USB 会话（transport close→open、重读 DEVICE_INFO、SET_IDLE、重新 `_validate_config`/quantize/verify/SWEEP_SETTINGS、`_bump_generation()`、`_require_axis_verify=True`），**保留 trace_index/_prev_start_mono（不重复 trace）**；`cancel_requested` 只读属性（base `_cancel_event`）；`_finalize_sweep` 轴门禁条件扩展为 `trace_index == 0 or _require_axis_verify`（重连后第一道仍过首道轴门禁）；失败路径 fail-closed（清 applied/assembler/stream/pending + 尽力关 transport，**保留 trace 计数**——`_enter_fail_closed` 会重置 trace_index，重连失败路径不得使用）。
3. `tests/contract/test_librevna_backend.py`（**扩展**，失败测试优先）：reconnect_session 语义、退避策略、reconnector 重试/耗尽/取消、controller 集成（断开→hook→继续采集不重复 trace、pause/resume/stop/emergency 与 in-flight 协作、无泄漏线程）。既有 48 测试全部回归。
4. `tests/hardware/test_librevna_hardware.py`（**新文件**）：`@pytest.mark.hardware` + 双重 opt-in（`--hardware` + `UAV_GPR_HARDWARE_OPTIN=1`，conftest L59–75）；pyusb 惰性导入（模块级不得导入 `usb`——默认收集阶段即 import，缺失会破坏默认运行）；设备缺失 → `pytest.skip("BLOCKED: …")`（诚实 BLOCKED，不伪造）；设备存在 → 真机 mini 矩阵（2 cells × S11，≥5 sweep → p50/p95/p99 + 硬件/固件/commit/配置记录结构断言）；`UAV_GPR_DEVICE_ID` 自检。
5. `tests/unit/test_no_external_access.py`（**扩展**，SC-1 裁决新增）：`test_hardware_directory_is_the_only_authorized_place` 期望集更新为 `{test_hardware_sentinel.py, test_librevna_hardware.py}`（其余守卫不动）。
6. `tests/unit/test_quality_gates.py`（**扩展**，SC-2 裁决新增）：三处硬件哨兵断言 `'1 skipped'` → `'4 skipped'`（L124/L147/L191；L211 `'1 passed'`+`HARDWARE_SENTINEL_RAN` 保持不变）。
7. `tools/benchmark/librevna_benchmark.py`（**新文件**）：CLI 基准工具（默认 `--backend simulated` 确定性种子；`--hardware` 需双重 opt-in，设备缺失 → 输出 `status:"blocked"` BLOCKED 报告并 exit 3，绝不伪造数字；`--smoke` CI 小规模）；JSON 报告：sweep 时长 p50/p95/p99/mean/max、写前模型开销（RawHashSpec.compute 代表 ISSUE-009 写前步骤）、错误率（`--inject-timeouts` 确定性注入）、CPU 占比（process_time/wall）、目标配置、commit（`git rev-parse HEAD` 尽力）、python/platform/numpy。基准方法参考钢筋仪 `LibreVNA采集速度测试`（`/mnt/d/博士任务/rebar-inspector/LibreVNA采集速度测试`：point/IFBW 网格、USB 流式对比），**历史速度数字只作对照、不得写成新结果**（M04 L167/L186、ACQUISITION.md L46）。
8. `docs/plans/2026-09-02-issue-023-librevna-reconnect.md`（本计划文档）。
9. `docs/issues/M04_LIBREVNA.md`（**仅** ISSUE-023 状态行 L155）：`Planned → In progress`（本任务执行期）→ `Review`（实现完成时）；合并后由 captain 标 `Blocked（等待真机）` 而非 Done；勿动其他条目。

> 注：round-2 修复（t10/t8b）为 6 唯一路径子集：`controller.py`（P2-1 守卫，经 captain 授权动冻结面）、`backend.py`（wait_cancellable/_verify_device_identity）、`reconnect.py`（默认 wait 可取消）、`test_librevna_backend.py`（探针 A/身份拒绝）、`test_librevna_hardware.py`（P2-2 矩阵 cell 应用）、本计划文档（D10/D11/round-2 日志）。

## 3. 明确排除项（M04 L170–172 + 提示词 + 任务契约）

- 不含 HDF5/网络关键路径最终最小间隔；不做飞行验收；不实现 S21/S12、校准；不进入 ISSUE-024；
- 不改 `core/**`、`acquisition/backend.py`（基类）、`librevna/transport.py`、`librevna/stream.py`、`librevna/__init__.py`、`docs/ACQUISITION.md`、`docs/PERFORMANCE.md`、`docs/TESTING.md`、`docs/adr/**`、参考仓库（只读）；`acquisition/controller.py` 只读消费，**round-2 修复经 captain 授权最小改动**（`_handle_disconnect` hook 异常路径 closing/STOPPING 守卫 + P3-03 注释指向 D1）；
- 不 commit/push/merge、不创建/切换分支；不新增 inScope 之外的文件（SC-1 已裁决：`tests/unit/test_no_external_access.py` 在 8 路径 inScope 内，仅守卫期望集一行改动）；
- 默认测试不枚举 USB、不 import `serial/usb/socket/网络` 根（AST 守卫）；不新增固定 sleep（注入时钟/事件同步）；禁删测试/降断言/吞异常。

## 4. 设计决策（D1–D8）+ 范围冲突（D9/SC-1）

- **D1 重连 generation 语义（P3-03 记录）**：物理重连**不**走 base `open()`（close+open 会把 generation 重置为 1 **且 `_do_open`/`_do_configure` 会把 trace_index 重置为 0 → 重连后首道重复 trace_index/trace_uid，违反 ACQUISITION.md §10 与验收「不重复 trace」**）。改为 backend 新增 `reconnect_session(config)`：base 生命周期状态保持 `CONFIGURED` 不变（controller `_handle_disconnect` 校验 `state is CONFIGURED and generation changed` 因此通过），仅原地重建 transport 会话并重新 configure/回读；每次成功重连 `_bump_generation()` → generation **严格递增**（断开时 +1，重连成功再 +1，如 1→2→3）；trace_index/_prev_start_mono 保留 → 不重复 trace。P3-03 注记（controller L838–844）由此落实：真实 USB 重连的 generation 语义 = 每次成功重连 +1、每观察到断开 +1，记录于本计划与 t2 报告（若 captain 要求固化进 ADR，另行上报）。
- **D2 重连后轴门禁**：`_finalize_sweep` 的 `_verify_first_axis` 现仅 `trace_index == 0` 时执行；新增 `_require_axis_verify` 标志，`reconnect_session` 成功时置 True，`_finalize_sweep` 在 `trace_index == 0 or _require_axis_verify` 时执行轴门禁（拒绝则标志保持 True → 持续 fail-closed，不产出 trace），验证通过后清 False。重连后第一道仍受 requested/applied 轴门禁约束（「不沿用未确认配置」的最后一环）。
- **D3 退避策略**：`LibreVnaReconnectPolicy`（frozen dataclass）：`max_attempts`（默认 5）、`initial_delay_s`（默认 0.5）、`backoff_factor`（默认 2.0）、`max_delay_s`（默认 8.0）；`delay_after_failed_attempt(failed_attempt) = min(initial × factor^(failed_attempt−1), max)`；构造校验（正有限数、max_attempts ≥ 1）；确定性无抖动（可复现）。
- **D4 重连器（hook 实现）**：`LibreVnaReconnector(backend, config, policy=…, wait=…)`——`config` 接受 `MissionConfig` 或 `Callable[[], MissionConfig]`（app 可延迟绑定冻结配置）；`__call__()` 循环：`backend.cancel_requested` 时抛 `BackendCancelledError`；调 `backend.reconnect_session(frozen_config)`；`BackendCancelledError/BackendClosedError` 直接传播（controller close/emergency 不得吞）；其他异常 → 记录 last_exc → `wait(delay)`（**round-2 起默认 `backend.wait_cancellable`——事件驱动可取消，close/emergency_stop 在退避等待中即刻生效（P2-1）；测试注入 no-op 记录器**）→ 重试；耗尽 → `LibreVnaReconnectError("…", attempts=…, last_reason=…)`（`BackendError` 子类，`_reason="reconnect_failed"`）。失败原因由 controller `_handle_disconnect` 转 `ControllerFailure`（既有的 hook-failed 路径，test_acquisition_controller.py L879–943 已固定）。
- **D5 失败 fail-closed（保留 trace 计数）**：`reconnect_session` 任一步失败 → 尽力 `transport.close()` + 清 `_applied/_assembler/_frame_stream/_pending_sweeps/_sweep_*`（**保留 `_trace_index/_prev_start_mono`**，不同于 `_enter_fail_closed` 的重置语义——重连失败路径不得重置 trace 计数，否则后续重试成功会产生重复 trace）→ 抛原始错误给 reconnector 重试；generation 仅在重连成功时 bump。
- **D6 in-flight 安全协作**：backend 无线程；`_do_acquire` 每轮 `_raise_if_interrupted` + 传输层 `LibreVnaCancelledError` 处理已冻结（ISSUE-021），pause（安全边界停新 sweep，in-flight 完成并发布）/resume（新调度锚点）/stop（drain）/emergency_stop（`backend.cancel()` 打断 in-flight，失败道不发布）语义由 controller 不变提供；t2 用 LibreVnaUsbBackend + ScriptedAdapter 通过 AcquisitionController 集成验证：不重复 trace_index、worker join、adapter closed、无残留线程。
- **D7 基准工具确定性**：simulated 模式 `SimulatedBackend(seed)`（同 seed+config → 同数据）；`--inject-timeouts N` 用 `SimulationFaults(timeout_at=…)` 确定性注入 → 错误率可复现；时长/CPU 为单调时钟实测（结构确定）；报告字段固定；`--smoke`（sweeps=3、points=101、IFBW=100kHz）供 CI 数量级检查。
- **D8 硬件测试诚实口径**：设备缺失 → `pytest.skip("BLOCKED: no LibreVNA device (VID 0x1209/PID 0x4121) — hardware acceptance remains BLOCKED, not faked")`；双重 opt-in 由 conftest 收集期 skip 保证（默认 deselected）；pyusb 惰性导入防默认收集崩溃；`UAV_GPR_DEVICE_ID` 设置时与设备 serial 比对（fail-closed）。
- **D9/SC-1 范围冲突（已于 2026-09-02 裁决）**：`tests/unit/test_no_external_access.py::test_hardware_directory_is_the_only_authorized_place`（L77–81）断言 `tests/hardware/` 下 `.py` 集合 **恰等于** `{test_hardware_sentinel.py}`；新增 `tests/hardware/test_librevna_hardware.py` 会使其失败。**裁决结论（captain，t4 契约）**：`tests/unit/test_no_external_access.py` 加入 inScope，守卫期望集更新为 `{test_hardware_sentinel.py, test_librevna_hardware.py}`；changedPaths = 8 个精确路径。**SC-2（t6/t4b 契约）**：`tests/unit/test_quality_gates.py` 三处哨兵断言 `'1 skipped'→'4 skipped'`。**SC-3（t10/t8b 契约）**：repair round-2 inScope 修正为 6 唯一路径（controller.py 去重，补 reconnect.py 与 test_librevna_backend.py）。
- **D10 重连身份重验（round-2，P2-1）**：`reconnect_session` 重读 DEVICE_INFO 后，`_verify_device_identity(previous, fresh)` 比对 protocol/firmware/hardware_version/hardware_revision/num_ports，不一致 → `LibreVnaProtocolError` fail-closed（generation 不 bump、trace 计数保留）。协议 v14 `DEVICE_INFO` 无 USB serial 字段：`info.serial ↔ device_id` 绑定待真机期实施（与 `UAV_GPR_DEVICE_ID` 自检衔接），不阻塞本次修复。
- **D11 停止竞态守卫（round-2，P2-1）**：controller `_handle_disconnect` hook 异常路径补 `closing or state is STOPPING` 守卫——close()/stop()/emergency_stop() 在途时 hook 中止属停止竞态而非故障，直接 return 交循环顶部（CLOSED / STOPPED 保留 stop reason），不覆盖为 FAILED（同型 L795–801 的 cancelled/closed acquire 路径）；配合 D4 可取消 wait，探针 A 场景终态 STOPPED/EMERGENCY、error None。

## 5. 测试矩阵（失败测试优先，先红灯后绿灯；全部无硬件/无固定 sleep）

1. `reconnect_session` 成功：断开（generation 1→2）→ `reconnect_session(config)` → 返回 AppliedConfig、generation == 3、state CONFIGURED、trace_index 保留（1）→ 再 acquire 得 trace_index 1、metadata.connection_generation == 3、与 trace 0 的 uid 不同（不重复）。
2. 重连后轴门禁：重连成功后的首道 sweep 轴整体偏移 > `AXIS_TOLERANCE_HZ` → `BackendConfigRejectedError`、无 trace 分配、trace_index 不变。
3. `reconnect_session` 失败 fail-closed：重连脚本缺 DEVICE_INFO/ACK → 抛错、`_applied is None`、transport closed、generation 不 bump；再 acquire → 结构化 `BackendStateError`（不产出 trace）。
4. 退避：`LibreVnaReconnectPolicy.delay_after_failed_attempt` 序列 `[initial, initial×factor, …]` 封顶 max_delay_s；构造校验非法值。
5. reconnector 重试成功：adapter open 前 2 次失败（`LibreVnaDeviceNotFoundError`）→ wait 记录 `[d1, d2]` → 第 3 次成功（reconnect 脚本就绪）→ 返回 applied、generation +1、无 sleep（wait 注入 no-op）。
6. reconnector 耗尽：open 持续失败 → `LibreVnaReconnectError`（attempts == max_attempts、last_reason 可读）、backend fail-closed。
7. reconnector 取消：`backend.cancel()` 后调用 → `BackendCancelledError` 传播（controller close/emergency 路径不吞）。
8. controller 集成（断开→hook→继续）：`AcquisitionController` + `LibreVnaUsbBackend` + ScriptedAdapter，`reconnect_hook=LibreVnaReconnector(backend, config)`；第一道（generation 1）→ 注入断开 → hook 重连成功 → 继续出第二道（generation 3）；trace_index 严格 0→1、无重复、controller state RUNNING；stop/close 后 worker join、adapter closed。
9. controller pause/resume/stop：采集若干道后 pause（in-flight 完成并发布）→ resume → stop（drain）→ close；发布集合 trace_index 严格递增无重复、全部 generation==1、worker join、adapter closed、无残留线程。
10. emergency_stop 打断 in-flight：静默设备（acquire 阻塞）→ `backend.acquire_started` 就绪 → `controller.emergency_stop()` → STOPPED/EMERGENCY、无发布、close 后无泄漏。
11. 基准工具：`--smoke --backend simulated` exit 0，JSON 字段齐全（p50/p95/p99、model_overhead、error_rate、cpu_ratio、config、commit）；`--inject-timeouts` 错误率>0；`--hardware`（无授权/无设备）→ BLOCKED 报告 exit 3；同 seed 两次运行数据一致。
12. 硬件测试：默认收集 deselected 计数 +N（不执行）；`-m hardware` 无 opt-in → skip（conftest）；opt-in 但无设备 → skip(BLOCKED)。
13. 回归：既有 48 backend 测试 + 300 依赖定向全绿；AST 守卫（tests/hardware 豁免、contract 文件无 usb/serial/网络根导入）。

## 6. 门禁命令（t2 完成时全绿）

```text
./.venv/Scripts/python.exe -m pytest tests/contract/test_librevna_backend.py -q          # 定向（先红灯后绿灯）
./.venv/Scripts/python.exe tools/quality/verify.py                                       # 全量非硬件（opt-in 硬件默认跳过不计失败）
./.venv/Scripts/python.exe -m ruff check src tests tools
./.venv/Scripts/python.exe -m mypy src
git diff --check && git status --porcelain=v1 -b
```

## 7. 硬件 BLOCKED 口径

- 本环境无指定真机（t1 §3.5-2 实测）。真机矩阵（p50/p95/p99、硬件/固件/commit 报告）标 **BLOCKED（等待真机）**：benchmark 工具 `--hardware` 在无设备时输出 `status:"blocked"` 报告（exit 3）；硬件测试无设备时 `pytest.skip("BLOCKED: …")`；**任何路径不得伪造真机数字**。
- 模拟断开/重连、controller 协作、benchmark 工具、opt-in 硬件测试完整交付并复审；t2 报告如实说明；M04 状态行由 captain 在合并后标 `Blocked（等待真机）` 而非 Done。

## 8. 执行日志（随执行追加）

- [x] **计划落盘 + SC-1 裁决**：本计划已落盘；captain 裁决 `tests/unit/test_no_external_access.py` 入 inScope（8 路径版），守卫期望集已更新并通过（`3 passed`）。
- [x] **红灯（失败测试优先）**：新增 10 项定向测试（reconnect_session 成功/轴门禁/失败 fail-closed、退避策略、reconnector 重试/耗尽/取消、controller 集成断开→hook→继续、pause/resume/stop、emergency_stop）先行运行：`ModuleNotFoundError: No module named 'uav_gpr.acquisition.librevna.reconnect'`（1 error during collection）——红灯证据。
- [x] **绿灯（最小实现）**：`reconnect.py`（policy/error/reconnector）+ `backend.py`（`reconnect_session`/`cancel_requested`/`_require_axis_verify`）后定向测试 **58 passed**（既有 48 + 新增 10，0.42s）；修复测试侧 3 处编排问题（`BackendStateError` 导入缺失、`open_error` 需在断开后注入、首道立即到期无需 advance）。
- [x] **benchmark 工具**：`tools/benchmark/librevna_benchmark.py` smoke 通过（exit 0，JSON 字段齐全）；`--inject-timeouts 2 --sweeps 3` → `error_rate 0.4`（确定性）；`--backend hardware` 无授权 → exit 2；双重 opt-in 无设备 → `status:"blocked"` + exit 3（Python subprocess 驱动实测退出码，bash `$?` 在本环境不可靠）。
- [x] **硬件测试 + 守卫**：`tests/hardware/test_librevna_hardware.py`（3 项 `@pytest.mark.hardware`，pyusb 惰性导入，无设备 `pytest.skip("BLOCKED: …")`）落盘；`tests/unit/test_no_external_access.py` 守卫期望集更新；实测默认收集 915 collected / 911 selected / **4 deselected**（1 哨兵 + 3 新硬件测试）；`pytest tests/hardware` 无 opt-in → **4 skipped**（双重 opt-in 生效）。
- [x] **定向门禁**：`pytest tests/contract/test_librevna_backend.py` **58 passed**；ruff `All checks passed!`（src tests tools）；mypy **43 文件** clean。
- [x] **SC-2（已裁决并完成）**：captain 裁决 `tests/unit/test_quality_gates.py` 入 inScope（9 路径版）；3 处哨兵断言 `'1 skipped'` → `'4 skipped'`（L124/L147/L191，注释说明 ISSUE-023 新增 3 项硬件测试；L211 `'1 passed'`+`HARDWARE_SENTINEL_RAN` 实测仍成立未改）→ `test_quality_gates.py` **12 passed**。
- [x] **M04 状态行**：`In progress` → `Review`（2026-09-02 t6；注明无真机 → 最终由 captain 标 Blocked 而非 Done）。
- [x] **全量门禁（t6 收尾，2026-09-02 实测）**：`./.venv/Scripts/python.exe tools/quality/verify.py` → **911 passed, 4 deselected in 284.44s**（915 collected，4 deselected = 1 哨兵 + 3 LibreVNA 硬件测试）+ ruff `All checks passed!`（src tests tools）+ mypy `Success: no issues found in 43 source files` + `package import ok` + `[quality] all gates passed`（VERIFY_EXIT=0）；定向 `tests/contract/test_librevna_backend.py` **58 passed**；`git diff --check` clean；工作树仅含 9 个 inScope 路径（+t1 基线单未跟踪文件，不计入 t6）。
- [x] **round-2 修复（t10/t8b，2026-09-02）**：SC-3 裁决（6 唯一路径 inScope：controller.py 去重 + 补 reconnect.py/test_librevna_backend.py）。**红灯**：探针 A（emergency_stop×重连退避 → 修复前终态 FAILED）+ 身份变化拒绝（修复前不拒绝）+ wait_cancellable 缺失 → **3 failed**。**绿灯（最小修复）**：controller.py `_handle_disconnect` hook 异常路径 closing/STOPPING 守卫（D11）+ P3-03 注释指向 D1；reconnect.py 默认 wait → `backend.wait_cancellable`（移除 `import time`，D4 更新）；backend.py `wait_cancellable(seconds)`（`_cancel_event.wait` + `_raise_interrupted`，正数校验）+ `_verify_device_identity(previous, fresh)`（protocol/firmware/hw_version/hw_revision/num_ports 不一致 → `LibreVnaProtocolError` fail-closed，D10）+ reconnect_session 集成；hardware 测试 `_make_config` 增 start_hz/stop_hz 参数 + 矩阵 cell 应用 + applied config 一致性断言（P2-2）→ 定向 **61 passed**（58 + 探针 A + 身份拒绝 + wait_cancellable）。**t10 全量门禁实测（2026-09-02）**：`verify.py` → **914 passed, 4 deselected in 284.13s**（918 collected；基线 911 之上 +3 新增契约测试）+ ruff `All checks passed!` + mypy 43 文件 Success + `package import ok` + `[quality] all gates passed`（VERIFY_EXIT=0）；controller 回归 **88 passed**；`git diff --check` clean；工作树仅含声明路径（t10 changedPaths = 6 唯一路径），未 commit/push/merge。

## 9. 参考来源与黄金数据声明

- benchmark 方法仅参考钢筋仪 `LibreVNA采集速度测试`（README + programs 清单，t1 §3.5-5 实测）；**不迁移其脚本、不引用其历史数值作为本仓库结果**（ACQUISITION.md L46）。
- 协议夹具沿用 ISSUE-021/022 已冻结的黄金向量（DEVICE_INFO/SweepSettings payload、BLOCKED datapoint 布局、`ScriptedAdapter`/`TickClock`/`ManualClock` 范式，test_librevna_backend.py L94–330），无新增参考迁移。
