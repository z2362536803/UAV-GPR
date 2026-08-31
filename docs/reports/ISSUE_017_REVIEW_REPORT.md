# ISSUE-017 独立复审报告（Round 2 / 最终）

日期：2026-08-31（Round 2）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-017-controller`（审查器 reviewer，任务 t5，attempt 4939404f-01e1-40b6-ad43-f37370420756）
依据：[ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) v1.0（含 §14 修复后复审口径）；基线件：[ISSUE_017_BASELINE_CONFIRMATION.md](ISSUE_017_BASELINE_CONFIRMATION.md)（t1）；实施计划：[../plans/2026-08-30-issue-017-controller.md](../plans/2026-08-30-issue-017-controller.md)（t2）
Round 1 记录：t3（attempt 844c76fc）VERDICT=FAIL（needs_revision），4 项发现（P1-01/P3-01/P3-02/P3-03）交付 t4 修复；本报告为 Round 2 复审结论，保留 Round 1 要点以保持审计链。
性质：独立只读复审；未修改任何实现/测试/计划/M03/Git 状态；变异探针在系统临时目录复制树中运行并已清理；审查前后工作树一致。

## 1. 审查结论

**VERDICT: PASS**

Round 1 的 4 项发现全部关闭并经独立复现验证：

- P1-01（close×configure 并发终态漂移）：configure() except 分支新增终态守卫（先查 `_closing`/CLOSED，CLOSED 时仅释放 backend 并重新抛出，绝不覆盖 CLOSED/error）；测试补断言 configure 线程 join 后 `state is CLOSED` 且 `error is None`。探针复现：`final state=closed error=None` ✓；补充 1000 次 close-vs-configure-failure 压力探针 0 次残余漂移。
- P3-01（亚纳秒间隔裸 ValueError 卡 PREPARING）：scheduler 构造移入 try（可空局部变量防护），失败走 ControllerFailure→FAILED；新增测试 `test_configure_below_scheduler_quantum_fails_structurally`。探针复现：`exc=ControllerFailure state=failed` ✓。
- P3-02（63 格全表缺测试）：新增参数化 `test_command_table_all_cells`（9 状态 × 7 命令 = 63 单元格，每格独立建态+teardown，断言 err/noop/ok+终态+stop_reason），与计划 §5.2 表格逐格对应。收集/运行实测 63 格全过。
- P3-03（generation 代数语义）：`_handle_disconnect` 重连校验处已加注释说明每 open 会话语义并留待 ISSUE-019/023 ADR 记录 ✓。

验收矩阵（§4）11/11 全部 PASS；无 P0/P1/P2 问题；全量门禁独立复现（定向 88 passed、依赖 53 passed、全量 703 passed/1 deselected、ruff/mypy 38 文件/import 全绿、diff-check clean）；变异探针 90/90 PASS。按 §12 可判定 PASS，交项目负责人人工验收后授权合并。

## 2. 自动识别的审查范围

自 t2/t4 完成报告、Git 事实与 M03 文档交叉识别：

| 项 | 值 | 证据 |
|---|---|---|
| Issue | ISSUE-017：采集控制器与暂停/停止状态机 | docs/issues/M03_ACQUISITION.md L79–114（状态行 Planned→Review） |
| 直接依赖 | ISSUE-015/016，均 Done 且合入 main | 合并提交 2f11cd9 / f28bf28，tracked 代码/测试实测存在 |
| 分支/基线 | main @ `cfbc92e`（ISSUE-016 Done 标记提交）；**无任何新提交** | git log/reflog 实测（Round 1/2 均未 commit/push/merge） |
| 工作树改动 | M03（1 行）+ 4 未跟踪文件（controller.py 949 行、测试 1344 行/88 用例、计划、t1 基线单）+ 本审查报告 | git status 实测；t4 changedPaths 仅 controller.py + 测试文件，仍在 t2 inScope 内 |
| 审查对象 | t2 实现 + t4 修复（Round 2 增量） | t4 完成报告 + 代码 diff 实测 |
| 排除项 | 不落盘/网络/Qt/LibreVNA；不改 core/backend/scheduler 契约；不 commit/push/merge | controller.py 仅 import stdlib + uav_gpr.core/backend/scheduler；backend.py/scheduler.py 零改动 |

## 3. 主要问题（P0→P3）

**Round 2：无未解决问题。** Round 1 的 4 项发现逐项关闭核对如下：

| Round 1 发现 | 等级 | 关闭证据（Round 2 实测） | 状态 |
|---|---|---|---|
| P1-01 close×configure 终态漂移（controller.py L459-467 缺终态守卫） | P1 | 守卫已加（现 L460-479：except 分支先读 `_closing`/state，CLOSED 时仅 backend.close+重抛，不覆盖 CLOSED/error，注释说明与 `_fail` 终态守卫一致）；测试 L427-433 补断言；探针 `state=closed error=None`；1000 次压力 0 漂移 | **已关闭** |
| P3-01 亚纳秒间隔裸 ValueError 卡 PREPARING（L448-452） | P3 | scheduler 构造移入 try（L449-457，可空局部变量 `scheduler: MonotonicAcquisitionScheduler | None = None`，except 中 `if scheduler is not None: scheduler.cancel()`）；新测试 L347-366 断言 ControllerFailure/cause_type=ValueError/FAILED/backend 已关；探针 `ControllerFailure state=failed` | **已关闭** |
| P3-02 测试未枚举全表 63 格 | P3 | 新增参数化 `test_command_table_all_cells`（L1089-1344）：_TABLE 与计划 §5.2 逐格对应（RUNNING/PAUSED 的 stop/emergency 断言瞬态 STOPPING 之后的确定性终态 STOPPED+reason，比计划表格更严格）；PREPARING/STOPPING 每格独立建态+teardown；63 格全过 | **已关闭** |
| P3-03 generation 代数语义记录 | P3 | controller.py L838-844 注释已说明每 open 会话语义与 ISSUE-019/023 ADR 留待项 | **已关闭** |

无新增问题：Round 2 增量 diff（configure 守卫重构 + 注释 + 测试 293 行）经逐行审查未引入回归；90 项探针全部通过。

## 4. 逐 Issue 验收矩阵

| # | 验收标准（M03 L100–104 + 提示词 + t1 §5 契约） | 状态 | 代码证据 | 测试/探针证据（Round 2 实测） |
|---|---|---|---|---|
| 1 | 状态转换表全覆盖，非法/重复命令结果确定 | **PASS** | 状态机 L76-94；命令实现 L420-726；P1-01 守卫 L460-479 | 63 格参数化测试全过（88 用例中 63 格）；探针 63/63 与计划表一致；close×configure 并发终态确定（守卫+压力 0/1000） |
| 2 | pause 不接受新 sweep，stop drain 已完成 sweep，close 无遗留 worker | **PASS** | pause 安全边界 L518-554 + worker L772-812；stop drain L600-642；close L690-726 join | 在途道完成/无新道（L560-590）；stop drain（L592-618）；BLOCK 探针 published=3/dropped=0；close 5 场景 0 残留线程（探针+测试 L951-1057） |
| 3 | 有界队列不会无限增长，消费慢有明确策略/指标 | **PASS** | BoundedSweepBuffer put/try_put 结构性容量上限 L223-249；BackpressurePolicy L97-106；ControllerMetrics L281-291 | BLOCK 节流+drain（L711-751 + 探针）；DROP_NEWEST 计数（L753-799 + 探针 published=1/dropped=4/size=1）；capacity 校验 L166-170 |
| 4 | worker 唯一所有权，编排 configure/scheduler/acquire | **PASS** | worker 为 backend/scheduler sweep 方法唯一调用者（L730-812、L933-942）；命令线程仅用中断路径（L33-39 文档承诺与实现一致） | 并发测试全程无串扰；acquire_started/事件同步 |
| 5 | 错误分类 → 结构化 FAILED 并按序释放资源 | **PASS** | 分类 L788-812；FAILED 释放顺序 L884-906（cancel→置态→backend.close→唤醒）；P3-01 修复后 configure 错误全部结构化（L449-479） | timeout/half_sweep/disconnect/cancel/closed 结构化 FAILED + backend CLOSED + 无发布（L801-830）；亚纳秒间隔 → ControllerFailure/FAILED（L347-366 + 探针） |
| 6 | 设备重连 hook 与 connection generation | **PASS** | _handle_disconnect L820-889：hook 后校验 CONFIGURED+代数变化否则 FAILED；成功重锚 scheduler；L838-844 代数语义注释（P3-03） | hook 成功续采（L832-877）、hook 抛异常→FAILED、未重建→ReconnectContract（L879-949）；resume 设备再检查 fail-closed（L1059-1078） |
| 7 | 排除项：不落盘/不联网/无 Qt/不实现 LibreVNA/不动 core·backend·scheduler 契约 | **PASS** | controller.py 导入面仅 stdlib + uav_gpr.core/backend/scheduler；git diff 零改动 backend.py/scheduler.py/core | 全量门禁 + import 检查 |
| 8 | 测试禁固定 sleep（SimulatedBackend/事件/屏障） | **PASS** | 测试 0 处 `time.sleep`（grep 实测仅文档字符串）；ManualWaiter/acquire_started/join/虚拟时钟驱动 | 88 用例复跑稳定；63 格表每格独立建态无时序猜测 |
| 9 | 依赖回归（ISSUE-015/016 53 测试）不被破坏 | **PASS** | 无依赖模块改动 | 53 passed in 0.53s 实测 |
| 10 | changedPaths 与 inScope 逐一相等（精确路径） | **PASS** | t4 仅改 controller.py + 测试文件（均在 t2 inScope 4 路径内）；工作树无范围外新文件 | git status 实测 |
| 11 | 红灯先行（失败测试优先） | **PASS** | t4 报告：P1-01/P3-01 修复均为红灯→绿灯（测试先行）；63 格表新增即收集失败→实现后全过 | t4 完成报告与代码现状一致；未发现反证 |

## 5. Git 与交付检查

- 当前分支 main，HEAD `cfbc92e` 与开工基线一致；**未 commit/push/merge、未创建/切换分支**（Round 1/2 均一致）。
- reflog 仅 commit/merge/checkout 记录，**无 reset/rebase/amend/强推迹象**。
- 工作树 = ` M docs/issues/M03_ACQUISITION.md`（恰 1 行 Planned→Review）+ `?? docs/plans/2026-08-30-issue-017-controller.md` + `?? src/uav_gpr/acquisition/controller.py` + `?? tests/contract/test_acquisition_controller.py` + `?? docs/reports/ISSUE_017_BASELINE_CONFIRMATION.md`（t1）+ `?? docs/reports/ISSUE_017_REVIEW_REPORT.md`（本报告）；审查前后一致。
- `git diff --check` clean（exit 0）；无缓存/日志/密钥/本地配置/实测数据/参考项目文件进入工作树（pytest/.mypy/.ruff 缓存经 git check-ignore 忽略）。
- 一个提交混入多 Issue / 碎片化拆分 / 范围外修改：均未发现。

## 6. 测试与验证结果（Round 2 独立复现）

环境：WSL Ubuntu / Python 3.12.3；pytest 8.4.2、numpy 2.5.2、ruff 0.16.4、mypy 1.20.2。

| 命令 | 实测结果 | 与 t4 声称 |
|---|---|---|
| `python3 -m pytest tests/contract/test_acquisition_controller.py -q` | **88 passed** in 3.74s（含 63 格全表 + 新增量子间隔测试） | 一致 |
| `python3 -m pytest tests/contract/test_acquisition_backend.py tests/contract/test_acquisition_scheduler.py -q` | 53 passed in 0.53s | 一致 |
| `python3 tools/quality/verify.py` | **703 passed, 1 deselected** in 129.66s；All checks passed；VERIFY_EXIT=0 | 一致（703/1 deselected/127.95s） |
| `python3 -m ruff check src tests` | All checks passed | 一致 |
| `python3 -m mypy src` | Success: no issues found in 38 source files | 一致 |
| `git diff --check` | clean（exit 0） | 一致 |

**变异探针**（系统临时目录 `/tmp/iss017_probe` 复制树运行，`PYTHONPATH` 指向复制树 `src`，运行后整树清理，项目内零残留；共 **90 项检查，90 PASS / 0 FAIL**）：

- 状态转换表 63 单元格（9 状态 × 7 命令，err/noop/ok + 终态 + stop_reason）——全部与计划 §5.2 一致、结果确定；
- BLOCK 慢消费者（capacity=2）：队列恒 ≤2、stop 等待 drain、消费后 published=3/dropped=0；
- DROP_NEWEST（capacity=1）：published=1/dropped=4/size=1、stop 无消费者完成；
- 暂停/恢复 3 轮：trace_index 严格 [0,1,2,3]、UID 唯一、无 burst；
- emergency：READY/PAUSED/STOPPING 升级、在途道不发布（fail-closed）；
- 线程残留：running/blocked-acquire/blocked-put/paused/failed 五场景 close 后 0 残留 worker；
- **P1-01 复验**：close-during-configure 终态 `closed`、`error=None`、configure 侧结构化 ControllerFailure；
- **P3-01 复验**：亚纳秒间隔 → `ControllerFailure`、state `failed`、cause_type=ValueError；
- **残余窗口压力**：close 与真实 configure 故障并发 1000 次，0 次出现"close 已返回但终态仍 FAILED"。

## 7. 报告与事实差异

- t4 修复声明与代码逐一核对属实（4 项发现各有对应代码/测试变更，见 §3 表）。
- t4 门禁数字全部独立复现（88/53/703+1deselected/38 files/全绿），无数字差异。
- t2 计划的 5 条早期修正记录（Round 1 已核对属实）不受本轮修复影响。
- "红灯→绿灯"为过程声明，无法完全回溯，未发现反证（测试先行结构与代码现状一致）。
- 无隐藏失败、跳过、占位或范围偏离。

## 8. 剩余风险

- connection_generation 为每 open 会话代数（重连后数值回 1，控制器以 `!=` 做变化检测 + 配置重确认）；ISSUE-019/023 真机 USB 重连时需以文档/ADR 固化代数语义（已留注释）。
- BLOCK 策略下 stop 的完成依赖消费者腾位（设计如此、计划 §5.5 已明示）；紧急场景应使用 emergency_stop/close。
- STOPPING 为瞬时态，仅能经故障注入构造（真实硬件路径 ISSUE-019/021/023 需重验安全边界）。
- 本 Issue 纯逻辑无硬件依赖；真机/现场验收归 M12（ISSUE-060）。
- Round 1 发现的残余检查-后写窗口（close 恰落在守卫读取与 FAILED 写入之间）经 1000 次压力 0 命中，且 `_closing` 为粘性标志、close() 幂等可恢复——判定为理论残余，不阻止合并（如需绝对原子性，可将守卫+写入合并为单次锁临界区，属可选硬化，未计入修复清单）。

## 9. 合并建议

**可以合并**（项目负责人人工验收通过后授权）：无 P0/P1/P2 问题，验收矩阵 11/11 PASS，门禁与探针全绿，未 commit/push/merge（由负责人决定后续合并操作）。合并后按依赖顺序进入 ISSUE-018 前需项目负责人确认；本团队不自动推进下一 Issue。

## 10. 最小修复清单

**无必须修复项。** 可选硬化（不阻止合并）：

1. （可选）configure() except 分支的终态守卫与 FAILED 写入合并为单次锁临界区，彻底消除理论上残余的检查-后写窗口。
2. （记录）ISSUE-019/023 开工时把 connection_generation 每会话语义写入 ADR（controller.py L838-844 注释已留待项）。

审查结束：不修改代码，等待项目负责人决定合并或进一步处置。
