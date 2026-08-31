# ISSUE-017 开工基线确认单（只读核查 + 实施计划）

日期：2026-08-31（ISSUE-017 开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-017-controller`（执行器 engineer，任务 t1，attempt 41813b01-452a-4850-9d41-8126d4835cab）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支、未修改实现与测试/计划文件。
配套文件：本单为 t2（实现采集控制器与暂停/停止状态机）与 t3（独立复审）的权威基线件；独立复审按 [ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) 执行。t2 完整实施契约见 t2 计划文档（`docs/plans/2026-08-30-issue-017-controller.md`，由 t2 先落盘）。

## 1. 锁定的目标 Issue 与依据

**ISSUE-017：采集控制器与暂停/停止状态机**（`docs/issues/M03_ACQUISITION.md` 第 3 个条目，状态 `Planned`）

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-017（M03_ACQUISITION.md L79–114） | docs/issues/README.md 依赖顺序主表 L84 |
| 直接依赖 | ISSUE-015（AcquisitionBackend 契约与确定性模拟器）、ISSUE-016（单调时钟采集间隔调度器） | M03 L82「直接依赖：ISSUE-015、016」；README.md L84 |
| 依赖状态 | 两者均已合入 `main`，tracked 代码/测试/合并提交为权威证据（见第 3 节）：ISSUE-015 经 `2f11cd9` 合入、`579f92b` 标记 Done（R2 复审 PASS WITH CONDITIONS）；ISSUE-016 经 `f28bf28` 合入、`cfbc92e` 标记 Done（复审 VERDICT=PASS） | git log/ls-files/reflog；docs/reports/ISSUE_015_REVIEW_REPORT.md、ISSUE_016_REVIEW_REPORT.md |
| 功能映射 | FR-002、003、005、018 | M03 L83 |
| 提示词工作区替换 | `E:\wurenjiruanjian` → 工作区根（见第 2 节） | docs/issues/README.md 第 3 节通用协议 |
| 一次一 Issue | 本轮只处理 ISSUE-017；不进入 ISSUE-018 | docs/issues/README.md 第 1/3 节；团队目标 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

参考项目 `E:\钢筋仪软件开发`、`E:\UVA_GPR_system` 的原始路径不在本机挂载范围内（WSL 仅挂载 C/D 盘）；本 Issue 为纯逻辑控制器（无硬件、无 I/O、无参考源搬运），不新增参考源依赖（沿用 ISSUE-001 manifest 与 ISSUE-003～006/015/016 已冻结契约）。

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      main（当前；工作树完全干净）
HEAD        cfbc92efc57637fbf2e9f48e93782ecc7e5b9dce  docs(issues): mark ISSUE-016 Done after authorized merge
分支关系    main...origin/main = 0/0（完全同步；ISSUE-016 基线时的 ahead 8 已随推送归零）
git status --porcelain=v1 -b
    ## main...origin/main
    （无其他条目——无未跟踪/未提交文件，非忽略未跟踪计数 = 0）
git diff --check    # clean（exit 0）
```

依赖合并提交（`git log --oneline main` 实测，全部在 main 历史中）：

| 提交 | 内容 |
|---|---|
| `0b69e6d` | `feat(acquisition): backend contract and deterministic simulated backend`（ISSUE-015 feature 提交，含 P1-01 修复） |
| `2f11cd9` | `Merge feat/issue-015: ISSUE-015 acquisition backend contract and deterministic simulator` |
| `579f92b` | `docs(issues): mark ISSUE-013/014/015 Done after authorized merges` |
| `999a210` | `feat(acquisition): monotonic acquisition scheduler with virtual-time contract`（ISSUE-016 feature 提交，含 P3 硬化） |
| `f28bf28` | `Merge feat/issue-016: ISSUE-016 monotonic acquisition scheduler` |
| `cfbc92e` | `docs(issues): mark ISSUE-016 Done after authorized merge` |

合并历史：…→ 013（`9d79c83`）→ 014（`4c2525b`）→ 015（`2f11cd9`）→ 状态标记 `579f92b` → 016（`f28bf28`）→ 状态标记 `cfbc92e`；reflog 实测仅 commit/merge/checkout 记录，**无 reset/rebase/amend/强推迹象**。`git ls-files` 确认 ISSUE-015/016 交付模块与测试全部 tracked 于 main。

### 3.2 依赖交付物（main 内实测，行数为 `wc -l` 实测，测试数为 `pytest --collect-only` 实测）

| 依赖 | 交付物（main，tracked） | 行数/测试数 | ISSUE-017 复用点 |
|---|---|---|---|
| ISSUE-015 | `src/uav_gpr/acquisition/backend.py` | 725 行 | `AcquisitionBackend` 严格生命周期 CLOSED→OPEN→CONFIGURED（L159–359）、`cancel/close` 幂等与唤醒（L296–312）、`acquire_started` 可观测事件（L183/288/294）、`connection_generation`（open=1、disconnect +1，L193–196/574–575）、`acquiring` busy 守卫（L279–285）；`SimulatedBackend` + `SimulationFaults`（timeout/half_sweep/disconnect/delay/reject_config/block_until_cancelled，L388–447）——控制器 worker 编排与故障注入的既有输入来源 |
| ISSUE-015 | `tests/contract/test_acquisition_backend.py` | 625 行 / 28 测试 | backend 契约回归（ISSUE-017 不得破坏），含并发 busy 守卫（L506–534）与 cancel/close 无残留（L536–552） |
| ISSUE-016 | `src/uav_gpr/acquisition/scheduler.py` | 447 行 | `MonotonicAcquisitionScheduler`（IDLE/RUNNING/PAUSED/CANCELLED，L146–447）：`start/pause/resume/cancel/wait_for_next/sweep_started/sweep_finished`；`Waiter` Protocol + `EventWaiter`（L50–87，可注入、可唤醒）；`ScheduleObservation`（L119–143）——控制器每道编排（wait→acquire→观测）的调度组件 |
| ISSUE-016 | `tests/contract/test_acquisition_scheduler.py` | 732 行 / 25 测试 | 调度器契约回归（ISSUE-017 不得破坏） |
| ISSUE-006 | `src/uav_gpr/core/config.py` | 925 行 | `MissionConfig.target_interval_s`（正有限 float，L270/338/433）、`frequency_axis_hz`、`channels`、`config_sha256`——控制器 configure 阶段的配置来源 |
| ISSUE-004/005 | `src/uav_gpr/core/frequency.py` / `metadata.py` | — | `FrequencySweep`（发布单元）、`TraceMetadata`（含 `connection_generation` 字段）——控制器发布完整 sweep 的数据形态 |
| ISSUE-003 | `src/uav_gpr/core/timeutil.py` | 132 行 | `Clock` Protocol（`utc_now`+`monotonic_ns`→`MonotonicNs`）、`ManualClock`（`advance_monotonic/advance_utc`）——控制器/测试的虚拟时间注入基础；**注意 `tests/conftest.py` 的 `virtual_clock` fixture 返回裸 int，不符合 `Clock` Protocol（须 `MonotonicNs`），t2 测试应注入 `core.timeutil.ManualClock`（016 基线单 §3.5 同口径）** |
| ISSUE-003 | `src/uav_gpr/core/errors.py` / `enums.py` | 158 / 184 行 | `DomainError`+`ErrorCode`+`StableStrEnum`——控制器状态/错误必须复用 core 结构化错误与稳定枚举模式 |
| 相关 | `tests/conftest.py` | 111 行 | `TZ=UTC`/`QT_QPA_PLATFORM=offscreen` 环境策略、`--seed` 确定性、`virtual_clock` fixture |

### 3.3 复审报告与状态行证据

- ISSUE-015 复审：`docs/reports/ISSUE_015_REVIEW_REPORT.md` 两轮——round-1 **FAIL**（P1-01：configure 未拒绝在途 acquire 并发重配）+ 3 项 P3；R2 独立复现修复后 **PASS WITH CONDITIONS**（28 passed 定向、全量门禁全绿、无必须修复项），合并建议"可以合并（人工验收通过后授权）"。合并提交 `2f11cd9` 的 diff 实测含 P1-01 修复（`backend.py:252-258` busy 守卫），与 R2 结论一致。
- ISSUE-016 复审：`docs/reports/ISSUE_016_REVIEW_REPORT.md` **VERDICT: PASS**（验收矩阵 16/16 PASS；2 项 P3 硬化建议不阻止合并，且 M03 L44 注明已在合并前处理——scheduler.py L343–353 P3-1 re-anchor 竞态硬化与对应测试 `test_late_wait_for_next_after_reanchor_waits_for_new_deadline` 实测存在）。
- M03 状态行实测：ISSUE-015 `Done`（L7）、ISSUE-016 `Done`（L44）、ISSUE-017 `Planned`（L81）、ISSUE-018 `Planned`（L118）。
- **ISSUE-017 为下一个可执行 Issue 的判定**：直接依赖 ISSUE-015/016 均已完成并合入 `main`（合并提交 + tracked 代码/测试 + 复审报告多源一致）；M03 状态行 ISSUE-017 仍为 `Planned`；仓库内无任何 controller 实现或测试（`grep -rn "controller" src/uav_gpr` 仅命中 scheduler.py L73 文档字符串与 `acquisition/__init__.py` 模块 docstring，无实现）；`docs/plans/` 无 issue-017 计划文档。

### 3.4 对 ISSUE-017 有约束的契约要点（读自 ACQUISITION.md §8/9/10、ARCHITECTURE.md §5/6、ISSUE_REVIEW_STANDARD.md）

**ACQUISITION.md §8（空中端采集流水线，L107–119）**：

1. 流水线：schedule deadline → acquire 完整 sweep → 构建不可变模型 → GNSS 中点匹配 → append+flush 空中 rcscan → 规范 raw hash/outbox 提交 → 发布有界状态/显示通知。
2. 磁盘写入失败、文件契约冲突或无法确认完整 sweep 时 fail-closed；网络失败只积压+告警，不自动停止采集。

**ACQUISITION.md §9（暂停、停止与故障，L121–129）**：

1. `pause`：停止发起新 sweep，等待当前 sweep 处理到**安全边界**并 flush；任务和文件保持打开。
2. `resume`：重新检查设备/磁盘，增加必要连接代数，**从新调度锚点继续**。
3. `stop`：不再发起新 sweep，**drain 已完整 sweep**，finalize 为 `stopped_by_user`。
4. `failure stop`：尽量 flush 并 finalize/保留 partial，终态含结构化错误。
5. `emergency stop`：优先停止硬件 I/O，但仍尽可能保存已完成 sweep；不得承诺未完成 sweep。
6. **所有操作必须幂等，重复远程命令返回已有结果**（ISSUE-017 验收"重复命令结果确定"的直接依据）。

**ACQUISITION.md §10（采集验收，L131–138）**：

- 暂停/恢复不重复 `trace_index`、不制造巨大调度误差补偿；设备重连后 `connection_generation` 增加且**配置重新确认**。

**ARCHITECTURE.md §5（状态模型，L147–159）**：

- 任务主状态建议：`IDLE -> PREPARING -> READY -> RUNNING <-> PAUSED -> FINALIZING -> COMPLETED`，`\-> STOPPING -> STOPPED`，`\-> FAILING -> FAILED`——ISSUE-017 状态机的架构落点（M03 目标写 PREPARING/RUNNING/PAUSED/STOPPING/FAILED 等转换，与之一致）。
- 状态转换由**应用层集中定义**，不让按钮、网络回调和硬件线程各自维护布尔变量；远程命令有 `command_id`，结果分 `received/accepted/executing/succeeded/failed`。
- 断线是链路状态，不自动等同于任务失败。

**ARCHITECTURE.md §6（并发边界，L161–173）**：

- LibreVNA worker 独立工作单元（设备 I/O 与 sweep 组装）；线程间只传不可变对象或清晰所有权的缓冲。
- **关闭顺序：不再接受新 sweep → drain → flush → 关闭设备 → 退出线程**——ISSUE-017"资源关闭顺序"验收的直接依据。
- 关闭/取消不得遗留线程（ISSUE-015 验收 A5 同口径）。

**ISSUE-017 范围/排除/验收（M03 L85–93 + 提示词，t2 不得偏离）**：

- 范围：worker 所有权、start/pause/resume/stop/emergency-stop/close；完整 sweep 发布、有界 consumer 接口和背压策略；当前 sweep 安全边界、幂等命令、错误分类和资源关闭顺序；设备重连 hook 与 connection generation（不实现具体 USB 重连）。
- 排除：不落盘、不发送网络、不做 Qt controller、不实现 LibreVNA。
- 验收：① 状态转换表全覆盖，非法/重复命令结果确定；② pause 不接受新 sweep，stop drain 已完成 sweep，close 无遗留 worker；③ 有界队列不会无限增长，消费慢有明确策略/指标。

**TESTING.md / 团队教训（禁固定 sleep）**：

- 环境：`TZ=UTC`、`QT_QPA_PLATFORM=offscreen`、`--seed`；测试用 SimulatedBackend/事件/barrier/join/虚拟时钟等待条件，**不使用固定 `sleep` 猜时序**；任何 flaky test 视为缺陷。
- 沿用 ISSUE-014/015/016 教训：**t2 inScope 一律用精确文件路径（非 glob），完成登记 changedPaths 必须与 inScope 逐一相等**；硬件双重 opt-in（本 Issue 纯软件，不应引入 hardware 标记测试）。

### 3.5 发现的事实（非阻塞，供 t2 注意）

1. **工作树完全干净且与远端同步**：`git status --porcelain=v1 -b` 仅 `## main...origin/main` 一行（0/0）；非忽略未跟踪计数 = 0；t2 交付物将是唯一的新改动，冲突面为零。
2. **控制器落点为空**：`src/uav_gpr/acquisition/` 目前仅 `__init__.py`、`backend.py`、`scheduler.py`、`librevna/__init__.py`（占位）；无 controller 模块、无 `test_acquisition_controller.py`、无 issue-017 计划文档。ISSUE-017 需要新增控制器模块与契约测试（t2 计划文档须给出精确文件路径与设计）。
3. **t2 测试注入口径**：`core.timeutil.ManualClock` 符合 `Clock` Protocol（`monotonic_ns()` 返回 `MonotonicNs`）；`conftest.virtual_clock` 返回裸 int，**不可直接注入** `AcquisitionBackend`/scheduler（Protocol 检查会拒绝），t2 测试统一用 `ManualClock`（016 基线单 §3.5 同口径）。
4. **调度器/后端组合边界已就绪**：`EventWaiter`（scheduler.py L69–87）是控制器 worker 等待中断的生产路径（注释明示"controller's pause/cancel path"）；`backend.acquire_started` 事件可作 worker 已进入阻塞 acquire 的同步屏障；`SimulationFaults.block_until_cancelled` 可作可取消阻塞故障注入。
5. **`connection_generation` 契约**：backend 在 open=1、disconnect+1；`TraceMetadata.connection_generation` 记录每道；controller 的 reconnect hook 应读 backend 代数并允许配置重新确认（ACQUISITION.md §10），但不实现具体 USB 重连。
6. M03 状态行与 README 依赖表（L84）一致，无计划冲突；本 Issue 不改变强制数据规则/空地职责/持久化语义（不落盘、不联网），**无需新增 ADR**。M01 文档状态滞后属已知项（多期基线单记录过），不影响依赖判定。TESTING.md L4 仍写"tests/contract 尚无测试"属文档滞后（contract 已有 7 个测试文件），不阻塞。

## 4. 门禁基线（核查时实测复跑，2026-08-31）

环境：WSL Ubuntu / Python 3.12.3；numpy 2.5.2、pytest 8.4.2、h5py 3.16.0；`uav_gpr` editable 可导入（`src/`）。

```text
$ python3 -m pytest tests/contract/test_acquisition_backend.py \
    tests/contract/test_acquisition_scheduler.py -q
53 passed in 0.54s                              # 依赖定向回归（ISSUE-015：28 + ISSUE-016：25）

$ python3 tools/quality/verify.py
615 passed, 1 deselected in 129.11s (0:02:09)   # 全量非硬件 pytest
All checks passed!                               # ruff
Success: no issues found in 37 source files      # mypy
package import ok                                # import 检查
[quality] all gates passed
VERIFY_EXIT=0
```

核查前后 `git status --porcelain=v1 -b` **逐字节一致**（仅 `## main...origin/main` 一行）；`git diff --check` clean；无新缓存/日志/实测数据残留（`.pytest_cache/`/`.mypy_cache/`/`.ruff_cache/` 等已忽略，`git check-ignore` 确认；`.agent-teams/` 已忽略，非交付物）。

## 5. ISSUE-017 实施计划摘要（t2 执行契约，完整版见 t2 计划文档）

### 范围（M03 L87–91 原文口径 + 提示词）

1. 无 Qt 的 `AcquisitionController` 与集中状态机（PREPARING/RUNNING/PAUSED/STOPPING/FAILED 等，对齐 ARCHITECTURE.md §5 建议状态模型）。
2. 唯一拥有 backend worker，编排 configure/scheduler/acquire；提供 start/pause/resume/stop/emergency-stop/close。
3. 完整 sweep 有界发布（有界 consumer 接口 + 背压策略/指标）；当前 sweep 安全边界（pause 在安全边界停止新 sweep）。
4. 重复命令幂等、结果确定；错误转结构化 FAILED 并按顺序释放资源（不再接受新 sweep → drain → flush → 关闭设备 → 退出线程）。
5. 设备重连 hook 与 connection generation（读 backend 代数，支持配置重新确认；不实现具体 USB 重连）。

### 排除项（M03 L93–95 + 提示词，t2 不得越界）

不写 HDF5、不发送网络、不做 Qt controller、不实现 LibreVNA；不改 `core/` 既有公共语义、不改 `backend.py`/`scheduler.py` 已冻结契约；不改两个参考项目；不 commit、不 push、不创建/切换分支；不进入 ISSUE-018。

### 验收标准（M03 L97–99 原文，t2 不得削弱）

1. 状态转换表全覆盖，非法/重复命令结果确定。
2. pause 不接受新 sweep，stop drain 已完成 sweep，close 无遗留 worker。
3. 有界队列不会无限增长，消费慢有明确策略/指标。

### 测试矩阵要点（提示词必测项 + 依赖回归）

- SimulatedBackend/事件/barrier 覆盖全部状态边（PREPARING→RUNNING→PAUSED→RUNNING→STOPPING→终态、FAILED 路径）；
- 慢 consumer（有界队列满 → 明确背压策略/指标，不无限增长）；
- 错误（timeout/half_sweep/disconnect/config_rejected/block_until_cancelled → 结构化 FAILED，资源按序释放）；
- 取消/close 无残留线程（join 断言）、幂等重复命令；
- 暂停在安全边界停止新 sweep、stop drain、resume 重新锚定不追债（与 scheduler 契约协同）；
- connection generation hook（断开 → 代数增加 → 重连钩子 → 配置重新确认）；
- 回归：依赖定向 53 passed（ISSUE-015/016）不被破坏；
- 门禁复跑：定向新测试 + 全量非硬件 pytest（`tools/quality/verify.py`）+ ruff + mypy + import + 工作树/diff 检查；**测试禁固定 sleep**。

### inScope 精确路径（以任务契约 t2 inScope 为准；t2 完成登记 changedPaths 必须与此逐一相等；沿用 ISSUE-014/015/016 教训：精确文件路径，非 glob）

1. `src/uav_gpr/acquisition/controller.py`（新模块：`AcquisitionController` + 状态机 + 有界发布/背压类型 + 错误类型）
2. `tests/contract/test_acquisition_controller.py`（新测试文件，失败测试优先）
3. `docs/plans/2026-08-30-issue-017-controller.md`（计划文档，t2 先落盘）
4. `docs/issues/M03_ACQUISITION.md`（仅 ISSUE-017 状态行：`Planned → In progress → Review`，勿动其他条目）

（t1 基线单 `docs/reports/ISSUE_017_BASELINE_CONFIRMATION.md` 为 t1 交付物，不计入 t2 inScope；沿用 ISSUE-016 先例。）

t2 验证命令按任务契约执行：`./.venv/Scripts/python.exe -m pytest tests/contract/test_acquisition_controller.py -q`（定向）、`./.venv/Scripts/python.exe tools/quality/verify.py`（全量）、`-m ruff check src tests`、`-m mypy src`、`git diff --check && git status --porcelain=v1 -b`；本机 WSL 侧若 `.venv/Scripts/python.exe` 不可用，以等价 `python3`（venv）执行并在执行日志注明解释器路径。

若确需拆分模块/新增文件，t2 必须先停止并向 captain 报告，不得自行新增范围外文件。

## 6. 结论

ISSUE-017 开工基线已锁定：`main`/HEAD @ `cfbc92e`（工作树完全干净、与 origin/main 同步 0/0）；两项依赖（ISSUE-015/016）的 tracked 代码、契约测试、合并提交与复审报告证据全部实测复现（ISSUE-015 经 `2f11cd9` 合入、R2 PASS WITH CONDITIONS 后标记 Done；ISSUE-016 经 `f28bf28` 合入、VERDICT=PASS 后标记 Done）；**ISSUE-017 是下一个可执行 Issue**（M03 状态行 `Planned`、无 controller 实现/测试/计划存在、依赖全绿）；契约要点（集中状态机、worker 唯一所有权、pause 安全边界、stop drain、close 无残留与关闭顺序、幂等命令、有界发布+背压、connection generation 重连钩子、结构化 FAILED、禁 sleep-based 测试、精确 inScope 路径）已固化于第 3.4/5 节；门禁基线全绿（全量 615 passed / 1 deselected、ruff/mypy(37 文件)/import 全过、依赖定向 53 passed），核查前后 git 状态一致、无残留。本确认单即为 t2 实现与 t3 独立复审的权威基线件；t2 按第 5 节契约执行（先落盘计划文档 → 失败测试优先 → 最小实现 → M03 状态行 → 门禁 → 报告），完成后停止，不进入 ISSUE-018。

> 后续记录：本单为开工时点的基线快照，不随 t2 改动；t2 实际完成记录见其完成报告与 `docs/plans/2026-08-30-issue-017-controller.md`，t3 复审报告独立输出。
