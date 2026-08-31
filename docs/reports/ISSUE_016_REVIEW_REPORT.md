# ISSUE-016 独立复审报告：单调时钟采集间隔调度器

- 日期：2026-08-31
- 审查者：reviewer（AgentTeams `uav-gpr-issue-016-scheduler`，任务 t3，attempt 4b40996c-5ff6-4555-a022-bf23a7f034ac）
- 审查依据：docs/ISSUE_REVIEW_STANDARD.md v1.0（§13 固定格式）
- 被审交付：t2（engineer，attempt e41dd2b0）完成报告、docs/plans/2026-08-30-issue-016-scheduler.md、src/uav_gpr/acquisition/scheduler.py、tests/contract/test_acquisition_scheduler.py、docs/issues/M03_ACQUISITION.md（ISSUE-016 状态行）
- 权威基线：docs/reports/ISSUE_016_BASELINE_CONFIRMATION.md（t1 基线确认单）
- 性质：只读复审。审查期间未修改实现/测试/计划/M03 任何内容；未 commit/push/merge/clean/checkout；变异探针在系统临时目录的源码副本中运行并已清理，项目内零残留。审查新增的唯一文件是本报告本身。

## 1. 审查结论

**VERDICT: PASS**

ISSUE-016 全部验收标准满足（验收矩阵 16/16 PASS），复现证据与 t2 完成报告一致，无 P0/P1/P2 问题；发现 2 项 P3 低风险硬化建议（不阻止合并，见第 3、10 节）。依赖（ISSUE-006/015）代码/测试真实合入 main 且回归未破坏；工作树仅含 t2 的 inScope 4 路径 + t1 基线单 + 本报告。可进入项目负责人人工验收；验收授权后按 ISSUE-015 先例合并。

## 2. 自动识别的审查范围

从 t2 完成报告、Git、计划文档与代码交叉识别：

| 项 | 识别结果 | 证据 |
|---|---|---|
| Issue | ISSUE-016 单调时钟采集间隔调度器（M03 L42–77；依赖 ISSUE-006/015） | docs/issues/M03_ACQUISITION.md L42–77；docs/issues/README.md L83 |
| 基线 | `main` @ `579f92b7a92ee06aae2cb16bdc8a2abfa053761d`（审查前后 HEAD 未变） | `git log -1 --format=%H`；reflog 无 reset/rebase/amend/强推 |
| 分支 | 当前分支 main（ahead origin/main 8，属既有授权合并历史，非本批引入） | `git status --porcelain=v1 -b` |
| 改动文件 | inScope 4 路径：`src/uav_gpr/acquisition/scheduler.py`（新 431 行）、`tests/contract/test_acquisition_scheduler.py`（新 633 行/23 测试）、`docs/plans/2026-08-30-issue-016-scheduler.md`（新 195 行）、`docs/issues/M03_ACQUISITION.md`（仅 ISSUE-016 状态行 Planned→Review） | `git status`；`git diff docs/issues/M03_ACQUISITION.md`（仅 1 行状态变更） |
| 非本批文件 | `docs/reports/ISSUE_016_BASELINE_CONFIRMATION.md`（t1 交付物，t2 未触碰） | git status；t1 完成报告 |
| 声称测试 | 定向 23 passed；依赖定向 111 passed；全量 613 passed/1 skipped；ruff/mypy/import 全绿 | t2 完成报告 + 计划文档 §8.3/8.4；本轮逐项复现（见第 6 节） |
| 声称交付 | 不 commit/push/merge、不建分支；changedPaths == inScope 4 路径 | HEAD 未变；工作树实测一致 |
| 排除项声明 | 零业务线程、不调用 backend/HDF5/网络/GNSS/Qt、不硬编码最小间隔、不改 core/ | 代码实测（见第 4 节第 9–11 行） |

## 3. 主要问题（P0 → P3）

无 P0 / P1 / P2。

**P3-1（低风险并发边角）：`wait_for_next()` 的“到期判定”与 pause+resume 重锚存在窄竞态**
- 文件与行号：src/uav_gpr/acquisition/scheduler.py:340-344（`remaining_ns <= 0` 分支在重取锁后只复查 state==RUNNING，未按**当前** deadline 复查剩余时间）。
- 触发条件：调用方已迟到（remaining ≤ 0 已算出）→ 释放锁后、重取锁前，另一线程完成 `pause()`+`resume()`（deadline 重锚为 now+interval）→ 该调用立即拿到 True 并在新锚点后提前最多一个间隔启动一道。
- 实际影响：仅一道提前启动、窗口极窄、deadline 链整体仍锚定（无累计漂移、无追债 burst、无数据完整性影响）；观测仍诚实记录实际时刻。
- 违反要求：无（不违反验收标准；与设计表述“恢复后下一道恰在恢复时刻+一个间隔到期”存在微观偏差）。
- 最小修复方向：在重取锁分支内按当前 deadline 重新计算 remaining，>0 则继续等待。

**P3-2（低风险防御不对称）：跨道单调时钟回拨未防御，可产出负 actual_interval_s 观测**
- 文件与行号：src/uav_gpr/acquisition/scheduler.py:398-404（道内回拨已抛 `SchedulerStateError`）vs 410-412（`actual_interval = start − prev_start` 未检查非负）。
- 触发条件：注入的 Clock 违背单调契约（两道之间回拨）。变异探针 F 实测：回拨 600 ns 后观测 `actual_interval_s = -5.5e-07` 静默产出。
- 实际影响：真实单调时钟不回拨，理论路径；且该观测送入 `TraceMetadata` 构造时会被 metadata.py:159-163（actual 非负校验）fail-closed 拒绝，不会静默污染数据。两处回拨路径均无测试覆盖。
- 违反要求：无（Clock 协议本身约定单调；属防御纵深不一致）。
- 最小修复方向：`sweep_finished` 中补 `start.ns < previous.ns → SchedulerStateError` 对称检查（或显式记录为信任边界），并补一条回拨测试。

按 ISSUE_REVIEW_STANDARD.md §12，P3 非阻止合并问题，不改变 PASS 结论。

## 4. 逐 Issue 验收矩阵（ISSUE-016，16 项）

| # | 验收标准 | 状态 | 代码证据 | 测试/实测证据 |
|---|---|---|---|---|
| 1 | 虚拟时间下长期 deadline 无累计漂移 | PASS | deadline 链 `deadline += interval_ns`（scheduler.py:419）；第 k 道恒为 anchor+(k−1)·interval（scheduler.py:166-167 文档） | test_no_drift_over_fifty_thousand_cycles（50,000 道整数精确断言，tests L275-307）；探针 C（1/3 s 非二进间隔 10,000 道 deadline 链整数精确、error ≤ 3.4e-10） |
| 2 | 采集耗时超过间隔有明确 overrun | PASS | `overrun = max(0, (duration−interval)/1e9)`（scheduler.py:414-416）；超时后 next 立即到期（scheduler.py:340-344） | test_overrun_flagged_when_duration_exceeds_interval（L316-341）、test_overrun_zero_when_duration_at_or_below_interval（L344-355）、test_deadline_chain_anchored_under_repeated_overrun（L358-376）；探针 D（间隔+1ns → overrun=1e-9、下一道零等待） |
| 3 | 取消即时生效 | PASS | `cancel()` 任意状态→CANCELLED + wake（scheduler.py:293-302）；wait_for_next 对 CANCELLED 立即 False（scheduler.py:328-329） | test_cancel_interrupts_in_flight_wait_immediately（线程 join，L484-502）、test_cancel_before_wait_returns_false_without_waiting（L544-554）、test_cancel_with_in_flight_sweep_still_records_observation（L557-570）；探针 E（IDLE 取消终态、start 被拒） |
| 4 | 暂停恢复无 burst、新锚点不追债 | PASS | `resume()` 重锚 deadline=now+interval（scheduler.py:275-279）；paused 等待立即 False（scheduler.py:328-329） | test_pause_resume_reanchors_without_burst（恢复后恰等 1 个间隔、L384-424）、test_resume_after_pause_does_not_burst_a_blocked_wait（L505-536）；探针 A（残留 wake 吸收、恢复后恰等满 1 间隔）、探针 B（在途 sweep 中 pause→finish→resume，间隔如实含暂停段 6.2s 后节拍恢复） |
| 5 | 系统 UTC 跳变不影响调度 | PASS | 调度器全模块零 UTC 读取（grep：无 `utc_now`/`datetime`/`time` 导入） | test_utc_jumps_do_not_affect_scheduling（±5h/−24h 跳变序列与无跳变逐项相等，L578-601） |
| 6 | 可注入 monotonic Clock/Waiter 的纯逻辑 scheduler | PASS | 构造注入 + Protocol 类型检查（scheduler.py:175-208）；Waiter 整数 ns 契约（scheduler.py:50-66） | test_clock_and_waiter_must_implement_protocols（L154-162）；全部虚拟时间测试经 AdvancingWaiter 注入 |
| 7 | 输出目标/实际间隔、schedule error、overrun | PASS | ScheduleObservation 五字段 + sweep_duration_s（scheduler.py:119-143） | 矩阵 #1/#2 断言逐字段；metadata 兼容测试 L609-633 |
| 8 | 首道 actual/schedule error 为空语义 | PASS | `previous is None → (None, None)`（scheduler.py:405-408） | test_first_sweep_due_immediately_with_null_interval_fields（L248-267）；与 TraceMetadata 首道可空契约一致（metadata.py:171-173） |
| 9 | 不创建业务线程 | PASS | 模块仅 threading.Lock/threading.Event（信号原语，零 spawn）；grep 无 `Thread(` | 静态 grep 实测；线程化测试全部 join 无残留（L449-536） |
| 10 | 不调用 backend/HDF5/网络/GNSS/Qt | PASS | import 仅 core.enums/core.errors/core.timeutil（scheduler.py:34-36） | grep 实测；ISSUE-015 backend 测试回归未破坏（111 passed） |
| 11 | 不硬编码最小间隔 | PASS | 无任何硬件间隔常量；0.5 ns 仅为 round→1ns 的表示量子下限（scheduler.py:191-195，计划 §5.6 明文），不属 ACQUISITION.md §7 实测预算口径 | test_target_below_scheduling_quantum_rejected（L149-151）；政策最小间隔留待 ISSUE-017/性能基准 |
| 12 | 单 sweep 串行 | PASS | `_in_flight` busy 守卫（scheduler.py:330-336、366-372） | test_single_sweep_serial_enforced（busy context 断言，L194-205） |
| 13 | 结构化错误、生命周期拒绝 | PASS | SchedulerError/SchedulerStateError 复用 DomainError+ErrorCode.INVALID_ARGUMENT+reason（scheduler.py:90-116，与 BackendError 同型） | test_start_twice_rejected、test_operations_before_start_rejected、test_sweep_finished_without_started_sweep_rejected、test_pause_resume_cancel_lifecycle、test_sweep_started_rejected_while_paused_or_cancelled（L170-240） |
| 14 | 禁 sleep-based 测试（虚拟时间/事件/join） | PASS | 测试文件零 time.sleep（grep 仅文档字符串）；BlockingWaiter 用 Event 同步 + 10s 安全上界 | 全量 613 passed 零 flaky；跨解释器（Python 3.12.3）复跑 23 passed |
| 15 | 门禁全绿且依赖未破坏 | PASS | 依赖 ISSUE-006/015 代码/测试 tracked 于 main（t1 已核，本轮复核 git ls-files/reflog） | 本轮复现：定向 23 passed、依赖定向 111 passed、全量 613 passed/1 skipped（exit 0）、ruff/mypy(37)/import/verify.py exit 0（见第 6 节） |
| 16 | 完成登记 changedPaths == inScope 4 路径；不 commit/push/merge | PASS | 工作树实测恰为 4 路径 + t1 基线单；HEAD 恒为 579f92b | git status/diff/reflog 实测（见第 5 节） |

状态取值均按 ISSUE_REVIEW_STANDARD.md §7 定义；无 FAIL/PARTIAL/BLOCKED。

## 5. Git 与交付检查

| 检查项 | 结果 | 证据 |
|---|---|---|
| 当前分支/目标分支/基线 | main / main @ 579f92b（HEAD 审查前后未变） | `git log -1 --format=%H`；`git status --porcelain=v1 -b` |
| 本批提交 | 无新提交（t2 承诺不 commit，事实一致） | reflog 顶部仍为 579f92b 的 commit 记录，无 reset/rebase/amend/强推迹象 |
| 改动文件归属 | M03 仅 ISSUE-016 状态行 1 行变更；3 个新文件为 inScope #1/#2/#3；t1 基线单为 t1 交付物 | `git diff docs/issues/M03_ACQUISITION.md`（±1 行）；git status |
| 范围外修改 | 无（core/、backend.py、storage/、tools/、docs/adr/、参考项目均未动） | git status 全量条目 |
| 缓存/日志/密钥/实测数据 | 无（.pytest_cache/.mypy_cache/.ruff_cache 被 .gitignore 忽略且无新未忽略产物） | `git check-ignore -v`；git status |
| diff 检查 | clean | `git diff --check` exit 0 |
| 公共契约变更 | 无（不新增 schema/ADR；观测类型为新增公共 API，仅增量） | scheduler.py 仅新增符号；TraceMetadata 契约未动 |
| 依赖顺序/可拆分合并 | ISSUE-016 依赖 006/015 均已在 main 且回归全绿；本批可整体随人工验收授权合并 | 依赖定向 111 passed；t1 基线单 §3 |

审查前后工作树逐字节一致（唯一新增 = 本报告文件；变异探针在 `/tmp` 与 `D:\tmp` 的源码副本运行并已删除）。

## 6. 测试与验证结果（本轮独立复现）

环境：WSL Ubuntu，工作区 D:\博士任务\无人机软件\UAV-GPR（/mnt/d/博士任务/无人机软件/UAV-GPR）；解释器 A = `.venv/Scripts/python.exe`（Python 3.13.14，t2 所用）；解释器 B = WSL python3（Python 3.12.3，交叉验证）。pytest 8.4.2、numpy 2.5.2、h5py 3.16.0。

| # | 命令（实际执行） | 实际结果 | 退出码 | t2 声称 |
|---|---|---|---|---|
| 1 | `./.venv/Scripts/python.exe -m pytest tests/contract/test_acquisition_scheduler.py -q` | **23 passed in 0.17s** | 0 | 23 passed（0.21s/0.17s，计时噪声一致） |
| 2 | 同上 4 文件依赖定向（backend/config/time/metadata） | **111 passed in 0.22s** | 0 | 111 passed（0.26s，一致） |
| 3 | `./.venv/Scripts/python.exe -m pytest -q`（全量，含硬件哨兵收集） | **613 passed, 1 skipped in 236.78s**；skipped = tests\hardware\test_hardware_sentinel.py（双重 opt-in，符合 TESTING.md） | 0 | 613 passed, 1 skipped in 246.25s（一致） |
| 4 | `./.venv/Scripts/python.exe tools/quality/verify.py` | pytest(非硬件)+ruff+mypy+import 四门全部通过，VERIFY_EXIT=0 | 0 | 全绿（一致；该后台运行的逐行日志被会话临时目录回收，仅留存退出码与上述独立复跑数字） |
| 5 | `./.venv/Scripts/python.exe -m ruff check src tests` | All checks passed! | 0 | 一致 |
| 6 | `./.venv/Scripts/python.exe -m mypy src` | Success: no issues found in 37 source files | 0 | 37 files（一致） |
| 7 | `git diff --check && git status --porcelain=v1 -b` | clean；仅 inScope 4 路径 + t1 基线单 | 0 | 一致 |
| 8 | `python3 -m pytest tests/contract/test_acquisition_scheduler.py -q`（交叉解释器 3.12.3） | 23 passed in 2.07s | 0 | t2 未声称（审查补查） |

补查反例/变异探针（ISSUE_REVIEW_STANDARD.md §9 末条要求，≥1 项）：在 `/tmp/uavgpr_probe`（WSL 临时目录源码副本）执行 15 项探针全部通过——A 残留 wake 吸收与恢复无 burst；B 在途 sweep 中 pause/finish/resume 重锚与诚实间隔；C 1/3s 非二进间隔 10,000 道 deadline 链整数精确、error ≤3.4e-10；D overrun 1ns 边界；E IDLE 取消终态；F 跨道时钟回拨行为确认（→P3-2）；另补 G 在途 sweep 存在时 resume 的 deadline 语义（节拍恢复正确）。探针结束后副本与日志已删除，项目内零残留。

## 7. 报告与事实差异

- t2 声称的数字、文件行数（scheduler.py 431 行 / 测试 633 行 / 计划 195 行）、changedPaths==inScope、M03 仅改 1 行、不 commit/push：**全部与仓库事实一致，无差异**。
- 定向/全量测试计时（0.21s vs 0.17s；246.25s vs 236.78s）：同机计时噪声，不构成差异。
- t2 过程声明（红灯 ModuleNotFoundError、首轮 19/23、4 项修复清单，计划 §8.1/8.2）：与最终代码状态相容，**未发现反证，但属事后不可复现的过程声明，按标准 §10 记为“无法独立验证”**。
- t2 完成报告“依赖定向（ISSUE-003/005/006/015）111 passed 未破坏”：复现一致。
- 未发现隐藏失败、跳过伪造、占位实现或范围偏离。

## 8. 剩余风险

1. P3-1 窄竞态：迟到的调用方恰逢 pause+resume 时可能提前一道启动（无漂移/无 burst/无数据影响）。
2. P3-2 跨道时钟回拨防御不对称（真实单调时钟不可达；metadata 构造会 fail-closed 兜底）。
3. 本 Issue 无硬件/真实时钟依赖，无硬件风险；生产默认 `EventWaiter`+`SystemClock` 路径由既有线程化测试（Event/join）与全量门禁覆盖，未在真机 8 小时耐久中验证（属 ISSUE-059/060 范围）。
4. ISSUE-017 组合（pause 安全边界、worker 所有权、backend acquire 编排）不在本 Issue 范围，交接边界已在计划 §5.4 明示。

## 9. 合并建议

**可以合并（经项目负责人人工验收并授权后）**：本批为 3 个新文件 + M03 状态行 1 行，不触碰既有语义；依赖回归与全量门禁全绿；无 P0/P1/P2。建议沿用 ISSUE-015 流程：人工验收 → 授权合并 → 将 M03 状态行置 Done（注明本报告结论）。P3-1/P3-2 可在合并前顺手修复（改动极小），也可作为后续硬化项记录；二者均不阻止合并。

## 10. 最小修复清单

1. **（可选）P3-1**：scheduler.py:340-344 —— 重取锁后按当前 `self._deadline` 重新计算 remaining，>0 则 `continue` 继续等待，消除重锚竞态下的提前启动；补一条“迟到 + pause/resume 交错”的确定性回归测试。
2. **（可选）P3-2**：scheduler.py:410 —— 补 `start.ns < previous.ns → SchedulerStateError`（与 398-404 道内回拨检查对称）；补一条回拨注入测试。
3. 无其他必须修复项。

审查结束：按 ISSUE_REVIEW_STANDARD.md §13/§14，本报告为终稿；不修改代码，等待项目负责人决定修复、拆分或合并。
