# ISSUE-016 实施计划：单调时钟采集间隔调度器

- 日期：2026-08-31
- Issue：ISSUE-016（`docs/issues/M03_ACQUISITION.md` L42–77，状态 `Planned`）
- 执行者：engineer（AgentTeams `uav-gpr-issue-016-scheduler`，任务 t2，attempt e41dd2b0-a6dc-4c24-bf7f-df83b4afb900）
- 权威基线：`docs/reports/ISSUE_016_BASELINE_CONFIRMATION.md`（t1 基线确认单）
- 配套：t3 独立复审按 `docs/ISSUE_REVIEW_STANDARD.md` 执行
- 性质：本计划文档是 t2 的权威执行契约；执行日志（红灯/绿灯/门禁数字）在本文件末尾追加

## 1. 目标与用户价值

实现纯逻辑 `MonotonicAcquisitionScheduler`：用**绝对单调 deadline** 调度 sweep，准确记录目标/实际间隔、schedule error、overrun 与暂停恢复锚点（M03 L48–53）。价值：ISSUE-017 控制器用它在单一 worker 线程上编排 backend 采集，保证长时间任务"无累计漂移、无暂停追债 burst"；调度观测值直接喂给 `TraceMetadata` 三字段（`target_interval_s`/`actual_interval_s`/`schedule_error_s`），不伪造墙钟（ACQUISITION.md §7、AGENTS.md §4/§8）。

## 2. 范围（inScope，精确文件路径，4 个）

1. `src/uav_gpr/acquisition/scheduler.py`（新模块：`MonotonicAcquisitionScheduler` + `Waiter` 协议 + `EventWaiter` + 观测/错误类型）
2. `tests/contract/test_acquisition_scheduler.py`（新测试文件，失败测试优先）
3. `docs/plans/2026-08-30-issue-016-scheduler.md`（本文件）
4. `docs/issues/M03_ACQUISITION.md`（仅 ISSUE-016 状态行：`Planned → In progress → Review`，勿动其他条目）

完成登记 changedPaths 必须与本 inScope 逐一相等；若确需拆分模块/新增文件，先停止并向 captain 报告，不得自行新增范围外文件。

## 3. 排除项（不得越界）

- 不创建业务线程（调度器本身零线程；`EventWaiter` 用 `threading.Event` 信号原语，不 spawn 线程）。
- 不调用 `acquisition/backend.py`、HDF5、网络、GNSS、Qt；不硬编码最小间隔（最小间隔属于 ISSUE-017/性能基准的实测预算，ACQUISITION.md §7 末条）。
- 不改 `src/uav_gpr/core/**`（只读消费：`timeutil.py` 的 `Clock`/`MonotonicNs`/`SystemClock`/`ManualClock`、`errors.py`、`enums.py` 的 `StableStrEnum`、`metadata.py` 的 `TraceMetadata` 校验口径）。
- 不改 `src/uav_gpr/acquisition/backend.py`、`storage/**`、`tools/**`、`docs/adr/**`、`docs/ACQUISITION.md`、`docs/DATA_MODEL.md`、`docs/PERFORMANCE.md`、`docs/TESTING.md`、`docs/reports/**`。
- 不 commit/push/merge、不创建/切换分支；不进入 ISSUE-017。

## 4. 关联需求/ADR/参考源

- 需求：FR-004（采集间隔）、FR-005（单调时钟调度与调度误差）——M03 L46。
- ADR：无新增 ADR 需求（不改变强制数据规则/空地职责/持久化语义；t1 基线单 §3.5-5 结论；`TraceMetadata` 三字段契约已存在，不扩展 schema）。
- 参考源：无代码搬运；复用 ISSUE-003（timeutil）、ISSUE-006（`target_interval_s` 校验口径）、ISSUE-015（backend 结构化错误与 busy 守卫模式、契约测试风格）冻结契约。观测语义与 `SimulatedBackend._produce_sweep` 的 `actual_interval = start − prev_start`、`schedule_error = actual − target` 完全一致（backend.py L633–639）。

## 5. 设计决策

### 5.1 模块结构（单一新模块 `scheduler.py`）

| 符号 | 种类 | 说明 |
|---|---|---|
| `SchedulerState` | `StableStrEnum` | `idle` / `running` / `paused` / `cancelled`（稳定小写值，与 ISSUE-015 `BackendState` 同风格） |
| `Waiter` | `@runtime_checkable Protocol` | 可注入阻塞等待：`wait(timeout_ns: int) -> bool`（超时到点返回 False，被 `wake()` 中断提前返回 True）+ `wake()`（幂等中断在途等待）——**整数纳秒**契约，虚拟时间下无浮点漂移 |
| `EventWaiter` | 生产默认实现 | `threading.Event` 支撑；不创建线程；`wake()` 可跨线程调用 |
| `SchedulerError` / `SchedulerStateError` | `DomainError` 子类 | 结构化错误，`reason="illegal_state"` + context（operation/state/busy），与 ISSUE-015 `BackendError` 模式一致 |
| `ScheduleObservation` | frozen dataclass | 每道调度观测（见 5.5），供 metadata 构建 |
| `MonotonicAcquisitionScheduler` | 纯逻辑类 | 调度核心（见 5.2–5.4） |

### 5.2 调度语义（绝对 deadline 链，无累计漂移）

- `interval_ns = round(target_interval_s * 1e9)`（构造期量化，要求 ≥ 1ns；观测中保留 float 秒口径与 `TraceMetadata` 一致）。
- **deadline 链**：`start()` 建锚 `anchor = now`，`deadline = anchor`（**首道立即到期**，后续每道一个间隔，避免无谓首道延迟）；每次 `sweep_finished()` 推进 `deadline += interval_ns`。因此第 k 道观测的 deadline 恒等于 `anchor + (k−1)·interval_ns`——纯函数于 k，与采集耗时、调用延迟、暂停完全无关，**结构上无累计漂移**。
- 等待：`wait_for_next()` 计算 `remaining = deadline − now`；`remaining ≤ 0` 即到期（立即返回 True）；否则 `waiter.wait(remaining)`。等待**不持锁**；被 `wake()` 打断（pause/cancel/伪唤醒）后回环重查状态与剩余时间。
- **overrun**：`overrun_s = max(0, (duration_ns − interval_ns)/1e9)`——"耗时超过间隔"即明确标记（验收口径）；超过后下一道 `wait_for_next()` 立即到期（remaining ≤ 0），符合 ACQUISITION.md §7「下一道立即或按策略开始」；deadline 链不因 overrun 平移（不追债）。
- 实际间隔链：`actual_interval_s = (start_k − start_{k−1})/1e9`，`schedule_error_s = actual − target`；**首道两者为 None**（DATA_MODEL/`TraceMetadata` 首道可空口径；与 SimulatedBackend 一致）。暂停后首道的 actual 如实包含暂停间隔（诚实数据，`TraceMetadata` 对非首道强制非空）。

### 5.3 暂停/恢复与取消

- `pause()`：`running → paused` 并 `wake()` 在途等待（等待立即返回 False）；`paused → paused` 幂等 no-op；`idle` 拒绝；`cancelled` no-op。
- `resume()`：`paused → running`，**新锚点** `anchor = now`、`deadline = anchor + interval_ns`——恢复后下一道恰在"恢复时刻 + 一个间隔"到期，**不追赶暂停期间次数（无 burst）**；`running → running` 幂等 no-op（绝不重锚已在跑的调度）；`idle/cancelled` 拒绝。
- `cancel()`：任意状态 → `cancelled`（终态，幂等）+ `wake()`；取消后 `wait_for_next()` 立即返回 False（取消即时生效）；在途 sweep 的 `sweep_finished()` 仍产出观测（诚实记录已完成数据）。
- 组合语义：`sweep_started()` 仅 `running` 允许（paused/cancelled/idle 结构化拒绝）；`sweep_finished()` 要求存在在途 sweep，`running/paused/cancelled` 均可（暂停/取消时在途 sweep 正常收尾记录）；`wait_for_next()` 在 `paused/cancelled` 返回 False、在 `idle` 拒绝、在途 sweep 时 busy 拒绝（**单 sweep 串行**结构化强制，同 ISSUE-015 busy 守卫风格）。

### 5.4 状态机

```text
IDLE --start()--> RUNNING --pause()--> PAUSED --resume()--> RUNNING（新锚点）
RUNNING/PAUSED --cancel()--> CANCELLED（终态，幂等）
sweep_started(): RUNNING only（在途时 busy 拒绝）
sweep_finished(): 需在途；RUNNING/PAUSED/CANCELLED 均可
wait_for_next(): RUNNING 下等待到期返回 True；PAUSED/CANCELLED 返回 False；IDLE 拒绝；在途 busy 拒绝
```

状态迁移与 deadline/锚点读写由 `threading.Lock` 串行化（锁只保护状态与整数时间元组，**绝不在持锁时调用 waiter.wait**）；`wake()` 在锁外调用。纯逻辑 + 锁即可被 ISSUE-017 的 worker 线程（调度侧）与命令线程（pause/resume/cancel 侧）安全共用；精确的"暂停安全边界"编排（如 pause 时在途 acquire 的处理）属 ISSUE-017 职责，本 Issue 只提供调度决策与观测。

### 5.5 观测结构与 metadata 兼容

```python
@dataclass(frozen=True, slots=True)
class ScheduleObservation:
    target_interval_s: float            # 构造期目标（>0 有限）
    actual_interval_s: float | None     # 首道 None；否则 start−prev_start（≥0 有限）
    schedule_error_s: float | None      # 首道 None；否则 actual−target（有限）
    overrun_s: float                    # ≥0；max(0, duration−interval)
    sweep_started_monotonic_ns: MonotonicNs
    sweep_finished_monotonic_ns: MonotonicNs
    deadline_monotonic_ns: MonotonicNs  # 本道对应的绝对 deadline（锚点链证据）
    @property sweep_duration_s: float   # (finish−start)/1e9
```

数值约束与 `TraceMetadata.__post_init__`（metadata.py L147–179）逐项对齐：target 正有限；actual 非负有限或 None；error 有限或 None；非首道两者必填。测试将用观测值直接构造 `TraceMetadata` 证明"可传入 metadata 构建"。

### 5.6 校验与错误

- 构造：`target_interval_s` 必须为 float（拒 bool/int）、正有限（TypeError/ValueError，口径同 metadata.py L147–156）；`interval_ns < 1` 抛 ValueError（低于调度量子）；`clock`/`waiter` 非 None 时须实现对应 Protocol（TypeError）。
- 生命周期违规一律 `SchedulerStateError`（`DomainError`，code=`INVALID_ARGUMENT`，context：`reason="illegal_state"`、`operation`、`state`、必要时 `allowed_states`/`busy`）。
- 防御：`sweep_finished` 时若 `finish < start`（单调时钟回拨，理论不可达）抛 `SchedulerStateError` 拒绝产出坏观测。

## 6. 测试矩阵（tests/contract/test_acquisition_scheduler.py，禁 sleep-based）

夹具：`ManualClock`（core 注入时钟）+ 两个测试 waiter——`AdvancingWaiter`（按请求 ns 精确推进共享 ManualClock、记录 `total_advanced_ns`，纯虚拟时间）与 `BlockingWaiter`（Event 阻塞 + `waiting_event` 同步，用于线程化中断测试；同步用 Event/join，不用固定 sleep）。

| # | 用例组 | 断言要点 |
|---|---|---|
| 1 | 构造校验 | bool/int/非有限/≤0 target → TypeError/ValueError；`interval_ns<1` → ValueError；clock/waiter 类型拒绝 |
| 2 | 生命周期 | start 两次/未 start 即 wait/sweep/finish、finish 无 start、wait 在途 busy、resume 无 pause、resume 在 cancelled、pause 在 idle 均结构化拒绝；pause/resume/cancel 幂等 |
| 3 | 首道 | 首道观测 actual/error 为 None、overrun=0、deadline==anchor；首道立即到期（wait 不推进 waiter） |
| 4 | 数万周期零漂移 | 50,000 道（target=1.0s、duration=0.1s）：每道 actual==1.0、error==0.0、overrun==0.0（精确相等）；waiter 总推进 == 49,999×9e8 ns；第 k 道 deadline == anchor+(k−1)×1e9 ns（整数精确）；末道后 clock == 期望值 |
| 5 | overrun | duration<target → 0.0；duration==target → 0.0（边界）；duration>target → (dur−target)/1e9 且下一道 wait 立即 True（remaining≤0）；连续 overrun 下 deadline 链仍逐道 +interval（不漂移、不追债） |
| 6 | 暂停/恢复无 burst（虚拟时间） | pause 后 wait→False；恢复锚点=resume 时刻，下一道恰在 resume+interval 到期（waiter 推进恰好 interval）；恢复后首道 actual 如实含暂停间隔（诚实），次道起恢复精确节拍 |
| 7 | 暂停/恢复中断在途等待（线程） | `BlockingWaiter` + worker：pause 打断 wait→worker 返回 False；resume 后再次 wait 不再立即到期（无 burst）；join 无残留线程 |
| 8 | 取消 | 未等待时 cancel → wait 立即 False 且 waiter 零调用；在途等待（线程）cancel → 立即 False（join）；在途 sweep 时 cancel → sweep_finished 仍产出观测，其后 wait False、sweep_started 拒绝 |
| 9 | UTC 跳变无关 | 100 道中多次 `advance_utc`（±1h/±24h，含等待间隙）→ 观测序列与无跳变基线逐项相等（调度只读 monotonic） |
| 10 | metadata 兼容 | 用观测值 + 固定其余字段构造 `TraceMetadata`（trace_index=0 用首道观测 None 语义；trace_index>0 用非首道观测）→ 构造通过，三字段逐值一致 |

回归与门禁：依赖定向（ISSUE-003/005/006/015 共 111 passed 不被破坏）+ 全量非硬件 `tools/quality/verify.py`（基线 590 passed/1 deselected）+ ruff + mypy + import + `git diff --check` + 工作树检查。

## 7. 完成定义与回退方式

- 完成定义：全部测试（矩阵 1–10）绿灯；全量门禁复跑全绿；M03 状态行置 `Review`；changedPaths == inScope 4 路径；不 commit/push/merge。
- 回退方式：本 Issue 全部为新文件 + 1 行状态行编辑，无既有语义变更；若发现问题直接删除/修改 inScope 内文件即可，无回退风险。

## 8. 执行日志（红灯/绿灯/门禁数字，t2 实测追加）

### 8.1 红灯证据（实现前，2026-08-31）

```text
$ ./.venv/Scripts/python.exe -m pytest tests/contract/test_acquisition_scheduler.py -q
ERROR tests/contract/test_acquisition_scheduler.py
E   ModuleNotFoundError: No module named 'uav_gpr.acquisition.scheduler'
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.11s
```

实现前定向测试以收集错误（模块不存在）红灯失败——符合"失败测试优先"。

### 8.2 实现与测试修正记录

首轮绿灯 19/23，4 个失败全部定位并修复（2 个测试侧、1 个实现侧、1 个测试侧）：
1. 测试侧：`make_scheduler` 未注入虚拟 waiter，误用生产 `EventWaiter` 走真实时间——默认改为 `AdvancingWaiter(clock)`。
2. 测试侧：`BlockingWaiter.wait` 未在返回后 clear 事件，唤醒后事件残留导致调度循环吸收"伪唤醒"忙转——按 `EventWaiter` 同型补 `clear()`。
3. 实现侧：`wait_for_next()` 在 IDLE 状态返回 False，与设计（IDLE 结构化拒绝，fail-closed）不符——补 `SchedulerStateError`（allowed_states=[running/paused/cancelled]）。
4. 测试侧：`test_overrun_flagged` 引用未定义常量 `interval_ns`——补定义；顺带修 ruff RUF100/F841 两处。

### 8.3 绿灯与门禁数字（2026-08-31 实测）

```text
$ ./.venv/Scripts/python.exe -m pytest tests/contract/test_acquisition_scheduler.py -q
23 passed in 0.21s                                    # 定向（矩阵 1–10 全覆盖）

$ ./.venv/Scripts/python.exe -m pytest tests/contract/test_acquisition_backend.py \
    tests/unit/test_core_config.py tests/unit/test_core_time.py \
    tests/unit/test_core_metadata.py -q
111 passed in 0.26s                                   # 依赖定向回归（ISSUE-003/005/006/015）未破坏

$ ./.venv/Scripts/python.exe -m ruff check src tests
All checks passed!                                    # ruff（新增 scheduler.py 与测试文件全绿）

$ ./.venv/Scripts/python.exe -m mypy src
Success: no issues found in 37 source files           # mypy strict（+1 = scheduler.py）

$ ./.venv/Scripts/python.exe tools/quality/verify.py
（全量非硬件门禁，结果见下方 8.4 追加）               # pytest 全量 + ruff + mypy + import

$ git diff --check && git status --porcelain=v1 -b
clean                                                # 见 8.4 工作树检查
```

### 8.4 全量门禁与工作树（2026-08-31 t2 收尾实测）

```text
$ ./.venv/Scripts/python.exe tools/quality/verify.py
All checks passed!                                # ruff
Success: no issues found in 37 source files       # mypy strict（含新 scheduler.py）
package import ok                                 # import 检查
[quality] ok: pytest (non-hardware) / ruff / mypy / package import
[quality] all gates passed
VERIFY_EXIT=0

$ ./.venv/Scripts/python.exe -m pytest -q
613 passed, 1 skipped in 246.25s (0:04:06)        # 全量非硬件（基线 590 + 新增 23；
                                                  #   1 个 hardware 哨兵双重 opt-in 跳过）

$ git diff --check && git status --porcelain=v1 -b
clean                                            # diff --check clean
## main...origin/main [ahead 8]
 M docs/issues/M03_ACQUISITION.md                 # 仅 ISSUE-016 状态行（Planned→In progress→Review）
?? docs/plans/2026-08-30-issue-016-scheduler.md   # inScope #3
?? src/uav_gpr/acquisition/scheduler.py           # inScope #1
?? tests/contract/test_acquisition_scheduler.py   # inScope #2
?? docs/reports/ISSUE_016_BASELINE_CONFIRMATION.md# t1 交付物（非 t2 范围，未触碰）
```

工作树仅含 inScope 4 路径改动 + t1 基线单（t2 未触碰）；无新缓存/日志/实测数据残留；不 commit/push/merge、未创建分支。

## E5 执行日志（审查后硬化，2026-08-31，人工验收阶段按复审报告 §10 处理）

按 `docs/reports/ISSUE_016_REVIEW_REPORT.md` 第 10 节可选 P3 硬化清单实施（项目负责人授权"处理两项 P3"）：

1. **P3-1（到期判定与重锚窄竞态）**：`wait_for_next()` 二次锁内改为按**当前** `self._deadline` 与时钟重算 remaining（`scheduler.py` 到期分支）；重锚后新锚仍在未来时 `continue` 继续等待，杜绝迟到调用方提前启动。回归测试 `test_late_wait_for_next_after_reanchor_waits_for_new_deadline`（确定性单线程：resume 重锚至 4s 后首次读取为过期陈旧值 → 必须等待剩余 1s，断言 `waited_ns == [1_000_000_000]`）。
2. **P3-2（跨道回拨防御不对称）**：`sweep_finished()` 在计算 `actual_interval` 前补 `start.ns < previous.ns → SchedulerStateError`（与道内回拨检查对称），不再静默产出负间隔观测。回归测试 `test_cross_trace_clock_rollback_rejected`（脚本时钟 1000→500 回拨注入，断言结构化拒绝且状态未破坏）。
3. **变异验证**：临时还原两处修复后两个新测试均 FAIL（`2 failed`），恢复后全绿——测试真实可杀。
4. **门禁**：定向 25 passed（0.18s，原 23 + 新 2）；ruff `All checks passed!`；mypy 无问题；全量 verify.py 复跑见门禁数字（613+2=615 passed/1 skipped）。
5. 范围纪律：仅改 inScope 内 `scheduler.py` 与 `test_acquisition_scheduler.py`（+本计划 E5）；未 commit/push/merge；其余文件未触碰。
