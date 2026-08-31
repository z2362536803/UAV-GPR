# ISSUE-017 实施计划：采集控制器与暂停/停止状态机

日期：2026-08-31（t2 开工首产物，先于一切实现落盘）
基线：`docs/reports/ISSUE_017_BASELINE_CONFIRMATION.md`（t1 权威基线件，main @ cfbc92e，工作树干净）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-017-controller`（执行器 engineer，任务 t2，attempt ddb00f37-f27d-42af-8d3f-20880cba820c）

## 1. 目标与用户价值

实现无 Qt 的 `AcquisitionController`：唯一拥有 backend worker，集中编排 configure/scheduler/acquire，提供 start/pause/resume/stop/emergency-stop/close 与集中状态机（PREPARING/RUNNING/PAUSED/STOPPING/FAILED 等），完整 sweep 有界发布、背压策略/指标和 connection_generation 重连 hook。为 ISSUE-018（回放后端）与 ISSUE-044（空中端任务运行器）提供状态/线程/发布的单一权威边界；与 ISSUE-015/016 组合成 M03 门禁「暂停/恢复/停止/故障和长时合成采集」的核心控制器。

## 2. 范围（inScope，精确文件路径，4 个）

1. `src/uav_gpr/acquisition/controller.py`（新模块：`AcquisitionController` + `ControllerState`/`BackpressurePolicy`/`StopReason` 枚举 + `ControllerError` 族 + `BoundedSweepBuffer` + `ControllerMetrics`）
2. `tests/contract/test_acquisition_controller.py`（新测试文件，失败测试优先）
3. `docs/plans/2026-08-30-issue-017-controller.md`（本计划文档）
4. `docs/issues/M03_ACQUISITION.md`（仅 ISSUE-017 状态行：`Planned → In progress → Review`，勿动其他条目）

完成登记 changedPaths 必须与本清单逐一相等（沿用 ISSUE-014/015/016 教训：精确文件路径，非 glob）。t1 基线单 `docs/reports/ISSUE_017_BASELINE_CONFIRMATION.md` 不计入本清单（t1 交付物，沿用 ISSUE-016 先例）。

## 3. 排除项（不得越界）

不写 HDF5、不发送网络、不做 Qt controller、不实现 LibreVNA USB 重连；不改 `core/` 既有公共语义；不改 `backend.py`/`scheduler.py` 已冻结契约（只消费其公共 API）；不改两个参考项目；不 commit、不 push、不创建/切换分支；不进入 ISSUE-018；不新增任何依赖（仅 stdlib + numpy 传递 + 既有 `uav_gpr.core`/`acquisition.backend`/`acquisition.scheduler`）。

## 4. 关联需求/ADR/参考源

- FR-002/003/005/018（M03 L83）；ACQUISITION.md §8/9/10（L107–138）；ARCHITECTURE.md §5/6（L147–173）。
- 无新增 ADR：本 Issue 不改变强制数据规则/空地职责/持久化语义（不落盘、不联网）；状态机与背压策略均落在既有架构边界内（t1 基线单 §3.5.6）。
- 无参考源搬运（纯逻辑模块，沿用 ISSUE-001 manifest 既有冻结契约；参考项目路径不在本机挂载范围）。
- 复用契约：`AcquisitionBackend`（open/configure/acquire/cancel/close、`acquire_started` 事件、`connection_generation`、`BackendError` 族，backend.py）；`MonotonicAcquisitionScheduler`（start/pause/resume/cancel/wait_for_next/sweep_started/sweep_finished、`Waiter` Protocol、`ScheduleObservation`，scheduler.py）；`Clock` Protocol/`ManualClock`（timeutil.py）；`MissionConfig`（config.py）。

## 5. 设计决策

### 5.1 模块结构（单一新模块 `controller.py`）

```
controller.py
├── ControllerState(StableStrEnum): IDLE, PREPARING, READY, RUNNING, PAUSED,
│                                   STOPPING, STOPPED, FAILED, CLOSED
├── BackpressurePolicy(StableStrEnum): BLOCK, DROP_NEWEST
├── StopReason(StableStrEnum): USER_STOP, EMERGENCY
├── ControllerError(DomainError)            # reason="controller_error"
│   ├── ControllerStateError                # reason="illegal_state"（非法命令）
│   └── ControllerFailure                   # reason="controller_failure"（终态失败，包装 cause）
├── BoundedSweepBuffer                      # 有界消费者接口（Condition 事件驱动，无轮询）
│   ├── put(sweep) -> bool                  # BLOCK：满则等待；abort 后返回 False（丢弃计数）
│   ├── try_put(sweep) -> bool              # 非阻塞；满/abort 返回 False（丢弃计数）
│   ├── get(timeout_s=None) -> FrequencySweep | None
│   ├── abort()                             # 唤醒所有阻塞生产者（close 专用）
│   └── capacity/size/published/dropped
├── ControllerMetrics(frozen dataclass)     # 状态 + published/dropped/queue_size/capacity/
│                                           # connection_generation/stop_reason 快照
└── AcquisitionController
    ├── configure(config) -> AppliedConfig  # 同步：IDLE→PREPARING→READY（失败→FAILED）
    ├── start()                             # READY→RUNNING（spawn worker；RUNNING 时幂等 no-op）
    ├── pause()/resume()                    # 跨线程安全调用 scheduler.pause/resume（scheduler 锁不持锁等待）
    ├── stop()/emergency_stop()             # 不发起新 sweep；drain；紧急时中断在途 acquire
    ├── close()                             # 任何非 CLOSED 状态→CLOSED；join worker；幂等
    ├── join(timeout_s=None) -> bool        # worker 线程退出；无 worker 恒 True
    ├── wait_finished(timeout_s=None) -> bool  # 终态（STOPPED/FAILED/CLOSED）事件
    ├── sweeps: BoundedSweepBuffer          # 有界发布通道（消费者接口）
    ├── state/capabilities/applied_config/connection_generation/error/stop_reason
    └── metrics() -> ControllerMetrics
```

### 5.2 状态机与命令表（验收 1：「状态转换表全覆盖，非法/重复命令结果确定」）

状态：`IDLE → PREPARING → READY → RUNNING ⇄ PAUSED → STOPPING → STOPPED`，任意非终态可 `→ FAILED`（错误）或 `→ CLOSED`（close）。对齐 ARCHITECTURE.md §5 建议模型（COMPLETED/FINALIZING 属 ISSUE-043+ 任务层，不在本 Issue；STOPPED 承载用户停止与紧急停止，经 `stop_reason` 区分）。

命令 × 状态结果表（`OK`=执行转换；`no-op`=幂等返回；`ERR`=抛 `ControllerStateError`（结构化，context 含 state/allowed_states）；`N/A`=不存在/不可达）：

| 命令 | IDLE | PREPARING | READY | RUNNING | PAUSED | STOPPING | STOPPED | FAILED | CLOSED |
|---|---|---|---|---|---|---|---|---|---|
| configure | OK→READY | ERR | ERR | ERR | ERR | ERR | ERR | ERR | ERR |
| start | ERR | ERR | OK→RUNNING | no-op | ERR | ERR | ERR | ERR | ERR |
| pause | ERR | ERR | ERR | OK→PAUSED | no-op | no-op | no-op | no-op | ERR |
| resume | ERR | ERR | ERR | no-op | OK→RUNNING | ERR | ERR | ERR | ERR |
| stop | ERR | ERR | OK→STOPPED | OK→STOPPING | OK→STOPPING | no-op | no-op | no-op | ERR |
| emergency_stop | ERR | ERR | OK→STOPPED | OK→STOPPING | OK→STOPPING | OK(EMERGENCY) | no-op | no-op | ERR |
| close | OK→CLOSED | OK→CLOSED | OK→CLOSED | OK→CLOSED | OK→CLOSED | OK→CLOSED | OK→CLOSED | OK→CLOSED | no-op |

说明：
- `configure` 同步执行（backend.open→configure）；PREPARING 在 configure 期间对**其他线程**可观测（阻塞式 open 测试双验证）；失败→FAILED 并抛 `ControllerFailure`；非 `MissionConfig` 抛 TypeError；backend 非 CLOSED 抛结构化错误。
- `start` 幂等：RUNNING 时重复 start 为 no-op。
- `pause/resume` 直接调用 `scheduler.pause/resume`（scheduler 锁从不持锁等待，跨线程安全；scheduler 尚为 IDLE 的竞态窗口由 `SchedulerStateError` 捕获并忽略——控制器自身状态是权威暂停标记，worker 在 scheduler.start 后按控制器状态推进）。
- `stop`：READY（无 worker）直接→STOPPED(USER_STOP)；RUNNING/PAUSED→STOPPING + `scheduler.cancel()`（不再发起新 sweep）。
- `emergency_stop`：在 stop 基础上再 `backend.cancel()`（中断在途 acquire；在途 sweep 未完成→不发布，fail-closed）并置 stop_reason=EMERGENCY。
- `close`：置 closing 标志 → `scheduler.cancel()` → `backend.cancel()` → `sweeps.abort()` → 唤醒 → join worker → CLOSED。worker 退出路径统一由 worker 自身置 CLOSED；无 worker 状态（IDLE/READY/STOPPED/FAILED）由 close() 直接置 CLOSED。
- 终态（STOPPED/FAILED/CLOSED）后除 close 外的命令：stop/emergency/pause/resume 为 no-op（ACQUISITION §9「重复远程命令返回已有结果」），start/configure 抛结构化错误。

### 5.3 线程与所有权边界（ARCHITECTURE.md §6；验收 2「close 无遗留 worker」）

- **worker 线程（daemon=False，名 `uav-gpr-acquisition-controller-worker`）由 start() 创建，终态前必须由 close() join**；worker 是 scheduler 的 wait/sweep 方法与 backend 的 open/configure/acquire/close 的唯一调用者。
- 命令线程（调用方）只允许跨线程调用：`backend.cancel()`（backend 设计支持的唤醒）、`scheduler.pause/resume/cancel()`（scheduler 锁不持锁等待）、缓冲 abort、事件置位。
- worker 全部阻塞点可中断：`scheduler.wait_for_next`（pause/cancel 唤醒）、`backend.acquire`（cancel 唤醒）、`BoundedSweepBuffer.put`（消费者取走或 abort 唤醒，Condition 事件驱动，**零轮询零固定 sleep**）、`_command_event.wait`（PAUSED 等待，任何命令唤醒）。
- close 顺序（ARCHITECTURE §6 关闭顺序）：不再接受新 sweep（closing 标志 + scheduler.cancel）→ drain（worker 发布已完成 sweep；BLOCK 策略下 stop 会等待消费者腾位）→ 关闭设备（worker 调 backend.close）→ 退出线程（join）。

### 5.4 调度协同（安全边界语义；验收 2「pause 不接受新 sweep，stop drain 已完成 sweep」）

worker 循环（每次迭代一步，状态在循环顶读取）：

```
scheduler.start()
loop:
  若 closing → _finish_closed（置 CLOSED、backend.close、终态事件）
  若 STOPPING → _finish_stopped（置 STOPPED、stop_reason、backend.close、终态事件）
  若 PAUSED → _command_event.wait()
  若 RUNNING → _tick():
      wait_for_next()  # False（paused/cancelled）→ 返回重查状态
      sweep_started()  # SchedulerStateError（pause/stop 竞态抢占）→ 安全边界：本道不开采，返回重查
      sweep = backend.acquire()
         # BackendDisconnectedError → _handle_disconnect（重连 hook 或 FAILED）
         # BackendCancelledError → closing? 结束 : EMERGENCY? 结束 : FAILED（意外取消 fail-closed）
         # BackendClosedError → closing? 结束 : FAILED
         # 其他异常 → FAILED
      sweep_finished()  # 在 RUNNING/PAUSED/CANCELLED 均可记账（scheduler 既有契约）
      publish(sweep)    # 背压策略；abort（close）时丢弃计数
```

关键语义：pause/stop 经由 scheduler 状态在**安全边界**生效——在途 sweep 可完成并被发布（`sweep_finished` 在 PAUSED/CANCELLED 下合法），新 sweep 不发起（`wait_for_next` 对非 RUNNING 立即 False、`sweep_started` 非 RUNNING 拒绝）。这正好复用 ISSUE-016「暂停恢复无 burst、cancel 即时」的既有验证。

### 5.5 有界发布与背压（验收 3「有界队列不会无限增长，消费慢有明确策略/指标」）

- `BoundedSweepBuffer`：`deque + Condition`，容量构造校验（int ≥ 1，否则 TypeError/ValueError）；`size` 恒 ≤ capacity（结构性）。
- `BackpressurePolicy.BLOCK`（默认）：worker 在 put 上等待腾位——消费慢即自然节流采集（背压）；stop 时继续等待直至发布完成（drain 保证）；close/abort 才放弃（丢弃计数）。
- `BackpressurePolicy.DROP_NEWEST`：满则丢弃最新道并计数 `dropped_sweeps`，worker 不阻塞；stop 时按同一策略（要保证 drain 必须用 BLOCK，文档明示）。
- 指标：`published_sweeps`（成功入队）、`dropped_sweeps`（丢弃，含 abort）、`queue_size`、`capacity`、`connection_generation`、`stop_reason`、`state`（`metrics()` 快照）。

### 5.6 重连 hook 与 connection generation（验收 A10 语义 + ACQUISITION.md §10）

- 构造参数 `reconnect_hook: Callable[[], None] | None`；worker 捕获 `BackendDisconnectedError` 时在 **worker 线程**上调用。
- hook 契约：把 backend 重建到 CONFIGURED（close+open+configure，由 hook 完成，控制器不实现具体 USB 重连）；hook 返回后控制器校验 `backend.state is CONFIGURED` 且 `connection_generation != 断开时代数`（新连接纪元），否则 FAILED。
- 无 hook 或 hook 抛异常 → FAILED（结构化，cause 含断开/hook 错误）。
- 重连成功后继续调度（deadline 链继续，迟到按 overrun/实际间隔如实记录——不伪造时间）；trace_index 重置属 SimulatedBackend 既有语义（mission 连续性归 ISSUE-043/044）。
- `resume()` 的设备再检查：backend 非 CONFIGURED → FAILED（设备丢失 fail-closed）。

### 5.7 错误分类与资源释放顺序（验收「错误转结构化 FAILED 并按顺序释放资源」）

- 错误分类（context 判别键 `cause_type`/`cause_message` + `reason`）：timeout/half_sweep/config_rejected/state 等 `BackendError` → `ControllerFailure`（FAILED）；disconnect → 重连 hook 路径；cancelled/closed → 依 closing/emergency 上下文归类（用户停止 vs 意外）；非 DomainError 异常 → 包装 `ControllerFailure`。
- FAILED 释放顺序：`scheduler.cancel()`（停止调度）→ 置 FAILED + error → `backend.close()`（释放设备，幂等）→ 终态事件 → worker 退出。已完成并已入队的 sweep 仍留在缓冲供消费者取走（不承诺未完成 sweep：在途失败道不发布，fail-closed）。

## 6. 测试矩阵（tests/contract/test_acquisition_controller.py，禁固定 sleep）

全部使用事件/屏障/join/虚拟时钟（ManualClock + 事件式 ManualWaiter，沿用 016 BlockingWaiter 模式；测试自身零 `time.sleep`）：

1. 构造校验：capacity 非法（0/负/非 int/bool）、backend 非 AcquisitionBackend、clock/waiter/reconnect_hook 类型校验。
2. configure：IDLE→READY（返回 AppliedConfig、capabilities/applied_config 暴露）；重复/非法状态拒绝；非 MissionConfig TypeError；backend 非 CLOSED 拒绝；reject_config 故障 → ControllerFailure + FAILED；PREPARING 可观测（阻塞式 open 测试双：configure 线程 + open 事件，断言 state==PREPARING 后放行）。
3. start：READY→RUNNING（首道立即产出：deadline=anchor，无等待即到期）；RUNNING 重复 start no-op；IDLE/PAUSED/STOPPED/FAILED/CLOSED/PREPARING 拒绝。
4. 正常采集：start → 收道（waiting_event + advance + wake 循环，逐道 trace_index 递增、sweep 完整）→ pause（waiting_event 同步）→ 暂停期 advance+wake 数次无新道 → resume → 恢复后无 burst（恰等一个间隔才出下一道）→ stop → drain → STOPPED(USER_STOP)、join 无残留。
5. pause 安全边界：delay 故障（acquire_started 事件同步）在途 sweep 中 pause → 在途道完成并发布 → 之后无新道。
6. stop drain：delay 故障在途 sweep 中 stop → STOPPING 可观测 → 在途道完成并发布（drain）→ STOPPED；PAUSED 中 stop → STOPPED；READY 中 stop → STOPPED。
7. emergency_stop：block_until_cancelled 故障在途 acquire 中 emergency → acquire 中断（BackendCancelledError）→ 该道不发布（缓冲空，fail-closed）→ STOPPED(EMERGENCY)；STOPPING 中 emergency → EMERGENCY 原因升级。
8. 背压 BLOCK：capacity=1、不消费 → 第二道 worker 阻塞 put（stop 后 wait_finished 不完成）→ 消费者取走 → drain 完成 → published==2、dropped==0、STOPPED。
9. 背压 DROP_NEWEST：capacity=1、不消费连续两周期 → 第二道丢弃 → stop 完成 → published==1、dropped==1、queue_size==1。
10. 错误→FAILED：timeout_at / half_sweep_at 故障 → FAILED、error 为 ControllerFailure（cause_type=BackendTimeoutError/BackendHalfSweepError、reason="controller_failure"）、backend 已关闭、无在途道发布、join 无残留；disconnect 无 hook → FAILED。
11. 重连 hook：disconnect_at 故障 + hook（close+open+configure 同 config）→ 代数变化（2→1）校验通过 → 采集继续（后续道发布）；hook 抛异常 → FAILED；hook 未重建到 CONFIGURED → FAILED。
12. close：从 IDLE/READY/RUNNING/PAUSED/STOPPING/STOPPED/FAILED 各状态 → CLOSED；幂等（二次 close no-op）；RUNNING 中 close（block_until_cancelled 在途）→ worker join 完成、`worker.is_alive() is False`、无残留线程；close 后命令拒绝。
13. 幂等/非法表：pause×2、resume×2、stop×2、emergency×2、start×2；全部 (state, command) 非法组合结构化拒绝（allowed_states 在 context）。
14. 回归：`test_acquisition_backend.py` + `test_acquisition_scheduler.py` 53 项不破坏；全量 verify.py + ruff + mypy + import + diff/status 检查。

## 7. 完成定义与回退方式

- 完成定义：本计划 §6 矩阵全绿（失败测试先行：先落测试文件跑红灯→最小实现→绿灯）；定向 `pytest tests/contract/test_acquisition_controller.py` 全过；全量 `tools/quality/verify.py`（615 + 新增）全绿；ruff/mypy/import 全过；M03 状态行更新为 Review；changedPaths 与 inScope 逐一相等；工作树仅含 inScope 4 路径（+ t1 基线单已存在）；不 commit/push/merge。
- 回退方式：全部改动为新增文件 + 一行状态行，回退即删除 3 个新增文件并还原状态行；无迁移/数据风险。

## 8. 执行日志（红灯/绿灯/门禁数字，t2 实测追加）

### 8.1 红灯证据（实现前，2026-08-31）

先落测试文件（`tests/contract/test_acquisition_controller.py`），随后把 `src/uav_gpr/acquisition/controller.py` 临时移出再跑定向测试（红色证据，真实命令输出）：

```text
$ mv src/uav_gpr/acquisition/controller.py /tmp/controller.py.bak && python3 -m pytest tests/contract/test_acquisition_controller.py -q
    from uav_gpr.acquisition.controller import (
E   ModuleNotFoundError: No module named 'uav_gpr.acquisition.controller'
=========================== short test summary info ============================
ERROR tests/contract/test_acquisition_controller.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.30s
$ mv /tmp/controller.py.bak src/uav_gpr/acquisition/controller.py   # 恢复
```

（恢复后首次定向运行出现 3 个测试失败，属测试脚本自身两处取道方式错误 + 一处 bool 容量预期错误，见 8.2；实现文件本身一次通过除上述测试逻辑外的全部用例。）

### 8.2 实现与测试修正记录

1. **测试脚本取道方式修正（2 处）**：`test_backpressure_block_*` 与 `test_backpressure_drop_newest_*` 原先用 `sweeps.get()` 取首道——`get()` 是消费者（出队），会把容量 1 的缓冲清空，导致第二道不再满缓冲（BLOCK 节流场景不成立 / DROP 不再丢弃）。修正为：用 `waiter.waiting_event.wait()` 同步 worker 已进入调度等待（即首道已发布未消费），再断言 `size==1`，随后才 advance+wake 构造满缓冲场景。BLOCK 用例顺带修正了 drain 断言的入队顺序（消费者先取到 trace_index 0，drain 的在途道随后可取到 trace_index 1）。
2. **bool 容量预期修正（1 处）**：`capacity=True` 抛 `TypeError`（与 `_require_timeout`/backend 对 bool 的语义一致），测试原预期 ValueError，改为 TypeError；0/-1 仍为 ValueError。
3. **重连 hook 失败 cause_type 断言修正（2 处）**：hook 未重建 backend 的失败 cause_type 固定为 `"ReconnectContract"`（实现侧契约），测试断言从 `"ControllerFailure"` 改为 `"ReconnectContract"`。
4. **mypy 窄化修正（1 处）**：`close()` 的 `if self._state is not CLOSED` 被 mypy 判定为 non-overlapping（属性窄化跨锁持续），改用局部 `state` 变量做早期返回检查，运行时语义不变（worker 可能并发置 CLOSED，运行时检查仍然必要）。
5. **实现设计确认**（计划 §5 与实现一致）：重连成功后**新建 scheduler 并重锚**（ISSUE-016 scheduler 无 abort API，且重连本应新锚，符合 ACQUISITION.md §9「从新调度锚点继续」）；STOPPED 终态统一由 worker/命令路径关闭 backend（READY-stop/emergency 直接关）；`_fail` 同时唤醒 command_event（覆盖 PAUSED 等待中的 worker）。

### 8.3 绿灯与门禁数字（2026-08-31 实测）

```text
$ python3 -m pytest tests/contract/test_acquisition_controller.py -q
24 passed in 3.63s                    # 首次全绿；复跑 2 次均 24 passed（无 flaky）
$ python3 -m pytest tests/contract/test_acquisition_backend.py \
    tests/contract/test_acquisition_scheduler.py -q
53 passed in 0.56s                    # 依赖定向回归（ISSUE-015/016 未破坏）
$ python3 -m ruff check src tests
All checks passed!
$ python3 -m mypy src
Success: no issues found in 38 source files   # 36 + scheduler + controller
$ python3 tools/quality/verify.py
639 passed, 1 deselected in 124.76s (0:02:04)   # 615 基线 + 24 新测试
All checks passed!                               # ruff（src+tests 全量）
Success: no issues found in 38 source files      # mypy（36 基线 + scheduler + controller）
package import ok                                # import 检查
[quality] all gates passed
VERIFY_EXIT=0

$ git status --porcelain=v1 -b
## main...origin/main
 M docs/issues/M03_ACQUISITION.md        # 仅 ISSUE-017 状态行：Planned → Review（1 行）
?? docs/plans/2026-08-30-issue-017-controller.md
?? src/uav_gpr/acquisition/controller.py
?? tests/contract/test_acquisition_controller.py
?? docs/reports/ISSUE_017_BASELINE_CONFIRMATION.md   # t1 交付物（非 t2 inScope）
$ git diff --check    # clean（exit 0）
```

## E 执行日志（Round 1 复审修复记录，2026-08-31）

按 `docs/reports/ISSUE_017_REVIEW_REPORT.md` Round 1（VERDICT=FAIL）4 项 findings 完成最小修复（t4 repair-round-2；本段由 captain 在 t5 VERDICT=PASS 后补记，纯文档）：

- **P1-01（阻塞）close×configure 并发终态漂移**：`configure()` except 分支加终态守卫——CLOSED 时仅 `backend.close()` 并重抛，不覆盖 CLOSED、不写伪错误（`controller.py` L460-479）；测试补完整断言（state is CLOSED 且 error is None）。红灯（回退守卫后复现漂移）→ 绿灯。
- **P3-01 亚纳秒间隔裸 ValueError 卡 PREPARING**：scheduler 构造移入 try，失败转 `ControllerFailure`→FAILED（cause_type=ValueError）；新测试 `target_interval_s=1e-12` 断言结构化失败。
- **P3-02 状态表测试缺口**：新增参数化 `test_command_table_all_cells`（9 状态×7 命令 = 63 格，每格独立建态+teardown，ok/noop/err+终态+stop_reason），与计划 §5.2 命令表逐格对应。
- **P3-03 generation 代数语义**：重连校验处注释 `connection_generation` 每 open 会话语义（`controller.py` L838-844），真机代数语义留 ISSUE-019/023 ADR。
- **门禁（t4 复跑）**：定向 88 passed（复跑稳定）；依赖 53 passed；全量 703 passed/1 deselected（127.95s）；ruff/mypy(38)/import 全绿；git diff --check clean；未 commit/push/merge。
- **t5 Round-2 复审**：VERDICT=PASS，验收矩阵 11/11，变异探针 90/90（含 1000 次 close×configure 压力 0 残余漂移）；无 P0/P1/P2，无必须修复项。
