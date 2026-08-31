# ISSUE-016 开工基线确认单（只读核查 + 实施计划）

日期：2026-08-31（ISSUE-016 开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-016-scheduler`（执行器 engineer，任务 t1，attempt a55cdf68-72da-45a0-8aac-cd6c70920db0）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支、未修改实现与测试/计划文件。
配套文件：本单为 t2（实现单调时钟采集间隔调度器）与 t3（独立复审）的权威基线件；独立复审按 [ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) 执行。t2 完整实施契约见 t2 计划文档（`docs/plans/2026-08-30-issue-016-scheduler.md`，由 t2 先落盘）。

## 1. 锁定的目标 Issue 与依据

**ISSUE-016：单调时钟采集间隔调度器**（`docs/issues/M03_ACQUISITION.md` 第 2 个条目，状态 `Planned`）

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-016（M03_ACQUISITION.md L42–77） | docs/issues/README.md 依赖顺序主表 L83 |
| 直接依赖 | ISSUE-006（MissionConfig、时窗推导与配置摘要）、ISSUE-015（AcquisitionBackend 契约与确定性模拟器） | M03 L44「直接依赖：ISSUE-006、015」；README.md L83 |
| 依赖状态 | 两者均已合入 `main`，tracked 代码/测试/合并提交为权威证据（见第 3 节）：ISSUE-006 经 PR #1（`0ddbd81`）合入；ISSUE-015 经 `2f11cd9` 合入并在 `579f92b` 由项目负责人授权标记 Done | git log/ls-files；docs/reports/ISSUE_015_REVIEW_REPORT.md（R2 复审 PASS WITH CONDITIONS） |
| 功能映射 | FR-004（采集间隔）、FR-005（单调时钟调度与调度误差） | M03 L46 |
| 提示词工作区替换 | `E:\wurenjiruanjian` → 工作区根（见第 2 节） | docs/issues/README.md 第 3 节通用协议 |
| 一次一 Issue | 本轮只处理 ISSUE-016；不进入 ISSUE-017 | docs/issues/README.md 第 1/3 节；团队目标 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

参考项目 `E:\钢筋仪软件开发`、`E:\UVA_GPR_system` 的原始路径不在本机挂载范围内（WSL 仅挂载 C/D 盘）；本 Issue 为纯逻辑调度器（无硬件、无 I/O、无参考源搬运），不新增参考源依赖（沿用 ISSUE-001 manifest 与 ISSUE-003～006/015 已冻结契约）。

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      main（当前；工作树干净）
HEAD        579f92b7a92ee06aae2cb16bdc8a2abfa053761d  docs(issues): mark ISSUE-013/014/015 Done after authorized merges
分支关系    main 相对 origin/main ahead 8（ISSUE-013/014/015 合并提交与状态文档提交 579f92b 均未推送，属既有授权合并历史）
git status --porcelain=v1 -b
    ## main...origin/main [ahead 8]
    （无其他条目——工作树完全干净，无未跟踪/未提交文件）
git diff --check    # clean（exit 0）
```

依赖合并提交（`git log --oneline main` 实测，全部在 main 历史中）：

| 提交 | 内容 |
|---|---|
| `22b0b0f` | `feat(core): add mission configuration contracts`（ISSUE-006 feature 提交） |
| `bcef87c` | `fix(issue-006): harden mission configuration contracts`（ISSUE-006 复审修复） |
| `0ddbd81` | `Merge pull request #1 from z2362536803/feat/m01-issues-006-007`（ISSUE-006/007 合入 main） |
| `0b69e6d` | `feat(acquisition): backend contract and deterministic simulated backend`（ISSUE-015 feature 提交，含 P1-01 修复） |
| `2f11cd9` | `Merge feat/issue-015: ISSUE-015 acquisition backend contract and deterministic simulator` |
| `579f92b` | `docs(issues): mark ISSUE-013/014/015 Done after authorized merges` |

合并历史：M01 批次（001–005 经 `ISSUE_001_005_REVIEW_SUMMARY.md` PASS 合入，006/007 经 PR #1 `0ddbd81` 合入）→ 008（`e852508`）→ 009（`c10693f`）→ 010（`4ec7d0e`）→ 011（`57c4966`）→ 012（`24d3505`）→ 013（`9d79c83`）→ 014（`4c2525b`）→ 015（`2f11cd9`）→ 状态标记 `579f92b`；无 reset/rebase/强推迹象（reflog 未显示历史重建）。`git ls-files` 确认 ISSUE-006/015 交付模块与测试全部 tracked 于 main。

### 3.2 依赖交付物（main 内实测，行数为 `wc -l` 实测，测试数为 `pytest --collect-only` 实测）

| 依赖 | 交付物（main，tracked） | 行数/测试数 | ISSUE-016 复用点 |
|---|---|---|---|
| ISSUE-006 | `src/uav_gpr/core/config.py` | 925 行 | `MissionConfig.target_interval_s`（正有限 float，L133/270/338/433）——调度目标间隔的唯一种子输入；`config_sha256` 摘要、schema/protocol 版本 fail-closed |
| ISSUE-006 | `tests/unit/test_core_config.py` | 681 行 / 45 测试 | 配置契约回归（含 target_interval_s 校验） |
| ISSUE-006 | `src/uav_gpr/core/enums.py` / `errors.py` | 184 / 158 行 | `StableStrEnum`、`ErrorCode`+`DomainError`（结构化错误，调度器错误契约必须复用） |
| ISSUE-005 | `src/uav_gpr/core/metadata.py` | 428 行 | `TraceMetadata`（L106–108：`target_interval_s`/`actual_interval_s`/`schedule_error_s`；L157–177：首道两值可空、后续道必填、actual 非负有限、error 有限）——调度观测值的落点契约 |
| ISSUE-015 | `src/uav_gpr/acquisition/backend.py` | 725 行 | `AcquisitionBackend` 生命周期/`SimulatedBackend`/`SimulationFaults`——**调度器不调用 backend**（排除项），仅作为 ISSUE-017 组合时的既有输入来源 |
| ISSUE-015 | `tests/contract/test_acquisition_backend.py` | 625 行 / 28 测试 | backend 契约回归（ISSUE-016 不得破坏） |
| ISSUE-003 | `src/uav_gpr/core/timeutil.py` | 132 行 | `Clock` Protocol（`utc_now` + `monotonic_ns`）、`MonotonicNs`、`SystemClock`/`ManualClock`（UTC 与 monotonic 独立推进）——调度器的可注入时钟基础；`ManualClock.advance_monotonic/advance_utc` 支持"UTC 跳变不影响调度"的虚拟时间测试 |
| ISSUE-003 | `tests/unit/test_core_time.py` | 110 行 / 9 测试 | 时间工具回归 |
| 相关 | `tests/conftest.py` | 111 行 | `virtual_clock` fixture（UTC+单调 ns 可推进）、`--seed`（默认 0）确定性重置——ISSUE-016 虚拟时间测试的既定基础设施 |

### 3.3 复审报告与状态行证据

- ISSUE-015 复审：`docs/reports/ISSUE_015_REVIEW_REPORT.md` 记录两轮审查——round-1 **FAIL**（P1-01：configure 未拒绝在途 acquire 并发重配）+ 3 项 P3；round-2（同文件 R2 节）独立复现修复后 **PASS WITH CONDITIONS**（28 passed 定向、590 passed 全量、ruff/mypy/import 全绿；无必须修复项），合并建议"可以合并（人工验收通过后授权）"。合并提交 `2f11cd9` 的 diff 实测包含 P1-01 修复（`backend.py:252-258` 的 `busy=True` 守卫），与复审 R2 结论一致。
- M03 状态行实测（`sed -n` 逐行）：ISSUE-015 `Done`（L7，注明"2026-08-31 独立复审 PASS WITH CONDITIONS 后经项目负责人授权合并"）、ISSUE-016 `Planned`（L44）、ISSUE-017 `Planned`（L81）、ISSUE-018 `Planned`（L118）。
- M01 状态行仍写 `Planned` 属已知文档滞后（ISSUE-008/011/015 基线单均记录过），以 tracked 代码/测试/合并提交为权威，不影响依赖判定。
- **ISSUE-016 为下一个可执行 Issue 的判定**：直接依赖 ISSUE-006/015 均已完成并合入 `main`（合并提交 + tracked 代码/测试 + 复审报告多源一致）；M03 状态行 ISSUE-016 仍为 `Planned`；仓库内无任何 scheduler/Waiter 模块或测试（`grep -ril "scheduler\|waiter" src/uav_gpr` 仅命中 `acquisition/__init__.py` 的文档字符串，无实现）。

### 3.4 对 ISSUE-016 有约束的契约要点（读自 ACQUISITION.md §7、PERFORMANCE.md、DATA_MODEL.md、TESTING.md）

**ACQUISITION.md §7（采集间隔调度，L94–105）**：

1. 目标间隔由地面端配置，空中端执行；**使用单调时钟和绝对 deadline**，避免每轮 `sleep(interval)` 累积漂移。
2. 调度间隔按 sweep 开始时刻或明确的固定基准定义（必须文档化选择）。
3. 一次 sweep 已超过目标间隔时，下一道立即或按策略开始，并记录 overrun。
4. **不并发驱动同一设备获取多个 sweep**（单 sweep 串行）。
5. 暂停期间不累计"补采债务"，恢复时建立新的调度锚点。
6. 每道保存目标间隔、实际间隔和 schedule error（落点：`TraceMetadata` 三字段）。
7. 允许的最小间隔必须来自"采集+空中写盘+哈希+安全余量"的实测，不只使用 USB 平均吞吐——**ISSUE-016 不得硬编码最小间隔**。

**PERFORMANCE.md（§1/2/4/6）**：

1. 平均速度不是唯一指标；关键链路记录 p50/p95/p99、最大值与错误数。
2. 网络发送和地面处理不得阻塞下一道采集；最小允许间隔必须大于关键路径 p99 加安全余量。
3. 8 小时合成任务结束时没有未解释的数据缺失、死线程或持续线性内存增长。
4. 基准输入和环境信息固定并记录 commit、Python/依赖、CPU、磁盘和配置（t2 计划需注明虚拟时间测试的规模定义）。

**DATA_MODEL.md（§2/§4/§5）**：

1. 每道保存 UTC 采集时间、**单调时钟时间**、目标间隔、实际间隔和调度误差（AGENTS.md §4 同步规则）。
2. 首道没有前一道，`actual_interval_s`/`schedule_error_s` 可以为空（metadata.py L104 同口径）。
3. 单调时钟纳秒与 UTC 分别建模、不得混用（timeutil.py 头注释同口径）。
4. 调度观测值传给 metadata 构建，不伪造墙钟（ISSUE-016 范围原文）。

**TESTING.md（§2.1/§3/§4/§6）**：

1. Acquisition 必测契约含"**单调间隔和 overrun**"；任务契约已将调度器测试落位 `tests/contract/test_acquisition_scheduler.py`（t2 定向验证命令同口径），t2 不得偏离。
2. 环境：`TZ=UTC`、`QT_QPA_PLATFORM=offscreen`、`--seed`（默认 0）、`virtual_clock` fixture（UTC + 单调 ns 可推进）。
3. **使用事件/barrier/虚拟时钟等待条件，不使用固定 `sleep` 猜并发时序**；任何 flaky test 视为缺陷，不能简单重跑或扩大 sleep。
4. 硬件双重 opt-in（`--hardware` + `UAV_GPR_HARDWARE_OPTIN=1`）——本 Issue 为纯软件调度器，不应引入 hardware 标记测试。

### 3.5 发现的事实（非阻塞，供 t2 注意）

1. **工作树完全干净**：`git status --porcelain=v1 -b` 仅 `## main...origin/main [ahead 8]` 一行；无 ISSUE-013/014 时代遗留的未提交产物（均已合并）。t2 交付物将是唯一的新改动，冲突面为零。
2. `main` 相对 `origin/main` ahead 8（ISSUE-013/014/015 合并 + 状态文档提交未推送）——既有授权合并历史，非本次引入，不影响 016 开工；t2 仍遵守"不 commit、不 push"。
3. **调度器落点为空**：`src/uav_gpr/acquisition/` 目前仅 `__init__.py`（文档字符串占位，tracked）与 `backend.py`；无 scheduler、无 Waiter 类型。ISSUE-016 需要新增"可注入 Waiter"（等待/休眠抽象，可取消）——`core/timeutil.py` 只有 `Clock` 协议，Waiter 属于本 Issue 范围内的新设计（t2 计划文档须给出精确文件路径与设计）。
4. `ManualClock` 支持 UTC 与 monotonic 独立推进（`advance_utc`/`advance_monotonic`），天然支撑"系统 UTC 跳变不影响调度"的验收测试；`conftest.virtual_clock` 也可用（注意其 `monotonic_ns()` 返回裸 int，与 `Clock` 协议的 `MonotonicNs` 不同——t2 测试若注入 `ManualClock` 需用 `core.timeutil` 类型口径）。
5. M01 文档状态滞后（3.3）不影响 016 依赖判定；M03 状态行与 README 依赖表（L83）一致，无计划冲突，无需新增 ADR（本 Issue 不改变强制数据规则/空地职责/持久化语义；`TraceMetadata` 三字段契约已存在，不扩展 schema）。
6. ISSUE-015 复审遗留的 P3 契约（reopen 须 drain、mission_id 轮换、钩子回滚）与 ISSUE-016 无交集（调度器不调用 backend）；ISSUE-017 将承接组合职责。

## 4. 门禁基线（核查时实测复跑，2026-08-31）

环境：WSL Ubuntu / Python 3.12.3；numpy 2.5.2、pytest 8.4.2、h5py 3.16.0；`uav_gpr` editable 可导入（`src/`）。

```text
$ python3 tools/quality/verify.py
590 passed, 1 deselected in 119.42s (0:01:59)   # 全量非硬件 pytest
All checks passed!                               # ruff
Success: no issues found in 36 source files      # mypy
package import ok                                # import 检查
[quality] all gates passed
VERIFY_EXIT=0

$ python3 -m pytest tests/contract/test_acquisition_backend.py \
    tests/unit/test_core_config.py tests/unit/test_core_time.py \
    tests/unit/test_core_metadata.py -q
111 passed in 0.51s                              # 依赖定向回归（ISSUE-003/005/006/015：28+45+9+29）
```

核查前后 `git status --porcelain=v1 -b` **逐字节一致**（仅 `## main...origin/main [ahead 8]` 一行）；`git diff --check` clean；无新缓存/日志/实测数据残留（`.pytest_cache/`/`.mypy_cache/`/`.ruff_cache/` 等已忽略，`git check-ignore` 确认；`data/`、`runs/`、`outbox/`、`*.rcscan` 均被忽略且无新增）。

## 5. ISSUE-016 实施计划摘要（t2 执行契约，完整版见 t2 计划文档）

### 范围（M03 L49–53 原文口径 + 提示词）

1. 可注入 clock/waiter 的无硬件 scheduler（纯逻辑，不创建业务线程）。
2. 绝对 deadline、无累计漂移、单 sweep 串行。
3. overrun、取消、暂停和恢复重新锚定；**不补偿暂停期间"欠债"**（不追赶暂停期间次数）。
4. 调度观测值传给 metadata 构建，不伪造墙钟（输出目标间隔、实际间隔、schedule error、overrun 观测；首道 actual/schedule error 为空语义对齐 `TraceMetadata`）。
5. 复用 `core.timeutil` 的 `Clock` Protocol/`MonotonicNs`/`ManualClock`；新增可取消 `Waiter` 抽象（本 Issue 范围内）。
6. 错误使用 core 结构化错误（`DomainError` + `ErrorCode`）。

### 排除项（M03 L55–56 + 提示词，t2 不得越界）

不启动线程、不调用 backend（`acquisition/backend.py`）、不调用 HDF5/网络、不硬编码最小间隔；不改 `core/` 既有公共语义；不改两个参考项目；不做 GUI；不 commit、不 push、不创建/切换分支；不进入 ISSUE-017。

### 验收标准（M03 L58–62 原文，t2 不得削弱）

1. 虚拟时间下长期 deadline 无漂移；耗时超过间隔有明确 overrun。
2. 取消即时生效，暂停恢复没有 burst。
3. 系统 UTC 跳变不影响调度。

### 测试矩阵要点（提示词必测项 + 依赖回归）

- 虚拟时间数万周期（≥数万）无累计漂移（绝对 deadline 性质，禁 sleep-based 测试）；
- 采集耗时小于/大于目标间隔（overrun 明确、下一道立即或按策略）；
- 首道（无前一道，actual/schedule error 为空语义）；
- 暂停/恢复（新锚点、无 burst、不追债）；
- 取消（等待可中断、即时生效）；
- 墙钟跳变（UTC 前跳/回跳不影响 monotonic deadline 与观测）；
- Waiter 可注入（虚拟 waiter 步进，无真实 sleep）；
- 回归：依赖定向 111 passed（ISSUE-003/005/006/015）不被破坏；
- 门禁复跑：定向新测试 + 全量非硬件 pytest（`tools/quality/verify.py`）+ ruff + mypy + import + 工作树/diff 检查。

### inScope 精确路径（以任务契约 t2 inScope 为准；t2 完成登记 changedPaths 必须与此逐一相等；沿用 ISSUE-014/015 教训：精确文件路径，非 glob）

1. `src/uav_gpr/acquisition/scheduler.py`（新模块：`MonotonicAcquisitionScheduler` + `Waiter` 协议 + 观测/错误类型）
2. `tests/contract/test_acquisition_scheduler.py`（新测试文件，失败测试优先）
3. `docs/plans/2026-08-30-issue-016-scheduler.md`（计划文档，t2 先落盘）
4. `docs/issues/M03_ACQUISITION.md`（仅 ISSUE-016 状态行：`Planned → In progress → Review`，勿动其他条目）

t2 验证命令按任务契约执行：`./.venv/Scripts/python.exe -m pytest tests/contract/test_acquisition_scheduler.py -q`（定向）、`./.venv/Scripts/python.exe tools/quality/verify.py`（全量）、`-m ruff check src tests`、`-m mypy src`、`git diff --check && git status --porcelain=v1 -b`；本机 WSL 侧若 `.venv/Scripts/python.exe` 不可用，以等价 `python3`（venv）执行并在执行日志注明解释器路径。

若确需拆分模块/新增文件，t2 必须先停止并向 captain 报告，不得自行新增范围外文件。

## 6. 结论

ISSUE-016 开工基线已锁定：`main`/HEAD @ `579f92b`（工作树完全干净，ahead 8 为既有授权合并历史）；两项依赖（ISSUE-006/015）的 tracked 代码、契约测试、合并提交与复审报告证据全部实测复现（ISSUE-006 经 PR #1 合入；ISSUE-015 两轮复审 PASS WITH CONDITIONS 后授权合并并标记 Done）；**ISSUE-016 是下一个可执行 Issue**（M03 状态行 `Planned`、无 scheduler/Waiter 实现存在、依赖全绿）；契约要点（绝对单调 deadline、无累计漂移、单 sweep 串行、overrun 记录、暂停恢复新锚点不追债、取消即时、UTC 跳变免疫、观测落点 `TraceMetadata` 三字段、禁 sleep-based 测试、不硬编码最小间隔、不调用 backend/线程/HDF5/网络）已固化于第 3.4 节；门禁基线全绿（全量 590 passed / 1 deselected、ruff/mypy/import 全过、依赖定向 111 passed），核查前后 git 状态一致、无残留。本确认单即为 t2 实现与 t3 独立复审的权威基线件；t2 按第 5 节契约执行（先落盘计划文档 → 失败测试优先 → 最小实现 → M03 状态行 → 门禁 → 报告），完成后停止，不进入 ISSUE-017。

> 后续记录：本单为开工时点的基线快照，不随 t2 改动；t2 实际完成记录见其完成报告与 `docs/plans/2026-08-30-issue-016-scheduler.md`，t3 复审报告独立输出。
