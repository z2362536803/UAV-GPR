# ISSUE-018 开工基线确认单（只读核查 + 实施计划）

日期：2026-08-31（ISSUE-018 开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-018-replay-backend`（执行器 engineer，任务 t1，attempt be2f5aa1-6abd-4abf-995f-9a3c2d801a52）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支、未修改实现与测试/计划文件。
配套文件：本单为 t2（实现 `.rcscan` 文件回放后端）与 t3（独立复审）的权威基线件；独立复审按 [ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) 执行。t2 完整实施契约见 t2 计划文档（`docs/plans/2026-08-30-issue-018-replay-backend.md`，由 t2 先落盘）。

## 1. 锁定的目标 Issue 与依据

**ISSUE-018：`.rcscan` 文件回放后端**（`docs/issues/M03_ACQUISITION.md` 第 4 个条目，状态 `Planned`，L116–151）

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-018（M03_ACQUISITION.md L116–151） | docs/issues/README.md 依赖顺序主表 L85 |
| 直接依赖 | ISSUE-011（reader、严格校验与逻辑道排序）、ISSUE-015（AcquisitionBackend 契约与确定性模拟器）、ISSUE-017（采集控制器与暂停/停止状态机） | M03 L119「直接依赖：ISSUE-011、015、017」；README.md L85 |
| 依赖状态 | 三者均已合入 `main`，tracked 代码/测试/合并提交为权威证据（见第 3 节）：ISSUE-011 经 `57c4966` 合入、`db95817` 标记 Done（复审 PASS WITH CONDITIONS，无 P0/P1/P2）；ISSUE-015 经 `2f11cd9` 合入、`579f92b` 标记 Done（R2 复审 PASS WITH CONDITIONS）；ISSUE-017 经 `b8712c5` 合入、`9406b60` 标记 Done（Round-2 复审 VERDICT=PASS，验收矩阵 11/11） | git log/ls-files/reflog；docs/reports/ISSUE_011_REVIEW_REPORT.md、ISSUE_015_REVIEW_REPORT.md、ISSUE_017_REVIEW_REPORT.md |
| 功能映射 | FR-016、018 | M03 L121 |
| 提示词工作区替换 | `E:\wurenjiruanjian` → 工作区根（见第 2 节） | docs/issues/README.md 第 3 节通用协议 |
| 一次一 Issue | 本轮只处理 ISSUE-018；不进入 ISSUE-019 | docs/issues/README.md 第 1/3 节；团队目标 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

参考项目 `E:\钢筋仪软件开发`、`E:\UVA_GPR_system` 的原始路径不在本机挂载范围内（WSL 仅挂载 C/D 盘）；本 Issue 为纯逻辑只读回放后端（无硬件、无写盘、无网络），复用 ISSUE-011/013 既有 reader 与 ISSUE-015/016/017 已冻结契约，不新增参考源依赖。

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      main（当前；工作树完全干净）
HEAD        9406b60  docs(issues): mark ISSUE-017 Done after authorized merge
分支关系    main...origin/main = 0/0（完全同步；`git log main..origin/main` 与 `origin/main..main` 均为 0）
git status --porcelain=v1 -b
    ## main...origin/main
    （无其他条目——无未跟踪/未提交文件，非忽略未跟踪计数 = 0）
git diff --check    # clean（exit 0）
```

依赖合并提交（`git log --oneline main` 实测，全部在 main 历史中）：

| 提交 | 内容 |
|---|---|
| `097a20e` | `feat(storage): read-only rcscan reader, strict validation and logical ordering (ISSUE-011)` |
| `57c4966` | `Merge feat/issue-011: ISSUE-011 read-only rcscan reader, strict validation and logical ordering` |
| `db95817` | `docs(issues): mark ISSUE-011 Done after authorized merge` |
| `0b69e6d` | `feat(acquisition): backend contract and deterministic simulated backend (ISSUE-015)`（含 P1-01 修复） |
| `2f11cd9` | `Merge feat/issue-015: ISSUE-015 acquisition backend contract and deterministic simulator` |
| `579f92b` | `docs(issues): mark ISSUE-013/014/015 Done after authorized merges` |
| `1ceca4e` | `feat(acquisition): acquisition controller with pause/stop state machine (ISSUE-017)` |
| `b8712c5` | `Merge feat/issue-017: ISSUE-017 acquisition controller` |
| `9406b60` | `docs(issues): mark ISSUE-017 Done after authorized merge` |

合并历史：…→ 011（`57c4966`）→ 状态标记 `db95817` → 012（`24d3505`）→ 013（`9d79c83`）→ 014（`4c2525b`）→ 015（`2f11cd9`）→ 状态标记 `579f92b` → 016（`f28bf28`）→ 状态标记 `cfbc92e` → 017（`b8712c5`）→ 状态标记 `9406b60`；reflog 实测仅 commit/merge/checkout 记录（`9406b60` commit → `b8712c5` merge → `cfbc92e` checkout …），**无 reset/rebase/amend/强推迹象**。`git ls-files` 确认 ISSUE-011/013/015/016/017 交付模块与测试全部 tracked 于 main。

### 3.2 依赖交付物（main 内实测，行数为 `wc -l` 实测，测试数为 `pytest --collect-only` 实测）

| 依赖 | 交付物（main，tracked） | 行数/测试数 | ISSUE-018 复用点 |
|---|---|---|---|
| ISSUE-011 | `src/uav_gpr/storage/rcscan_reader.py` | 1070 行 / 39 测试 | `RcScanReader` 严格打开校验 fail-closed（L322–364）、双视图 `iter_physical`/`iter_logical`（L942–974，逻辑视图按显式 `trace_index` 排序、重复折叠、冲突排除）、`trace_by_index`（L976–1019）、`ReadTrace`（L157–168：record_position/trace_index/trace_uid/metadata/frequency_raw/raw_trace_sha256/hash_verified）、`ValidationReport`（L180–255：missing/duplicates/conflicts/issues）、只读保证（`"r"` 打开不修复不迁移）——回放数据源与对拍基准 |
| ISSUE-013（v1 适配，ISSUE-018 的 v1 回放路径） | `src/uav_gpr/storage/rcscan_v1.py` | 1431 行 / 14 测试 | `RcScanV1Reader`（L498–873）：`data`（`V1RcScanData` L414–437：channels/frequencies_hz/`frequency`(FrequencyScan)/`frequency_calibrated`/`time_base`/`time_processed`/`trace_timestamps_utc`(可 None)/`trace_extras`/`position_m`/`source_sha256`）、逐行 `raw_row(index)`（L815–816）、`close` 幂等；v1 无 mission/GNSS/单调时钟，缺失一律保持 None/empty——**经 adapter 的 v1 raw 回放无需改本模块** |
| ISSUE-015 | `src/uav_gpr/acquisition/backend.py` | 725 行 / 28 测试 | `AcquisitionBackend` 严格生命周期（L159–359）、`_wait_cancellable`（L342–367，backend 内可取消/可超时阻塞等待的基础：honor cancel/close/timeout_s）、`BackendCancelledError/BackendClosedError/BackendTimeoutError`（L147–156）、`acquire_started` 可观测事件（L183/288/294）——`FileReplayBackend` 必须继承的契约与可取消等待原语 |
| ISSUE-016 | `src/uav_gpr/acquisition/scheduler.py` | 447 行 / 25 测试 | `MonotonicAcquisitionScheduler`/`Waiter` Protocol/`EventWaiter`（L50–87）、暂停/取消可唤醒等待（pause 安全边界、resume 重新锚定）——controller 对回放节奏的外部门控组件 |
| ISSUE-017 | `src/uav_gpr/acquisition/controller.py` | 949 行 / 88 测试 | `AcquisitionController`（L294–949）：worker 唯一调用 `backend.acquire()`（`_tick` L787）、pause 只走 `scheduler.pause()`（不 wake backend，在途 sweep 完成并发布=安全边界）、stop 走 `scheduler.cancel()`（drain 已完成 sweep）、emergency_stop/close 额外 `backend.cancel()`（中断在途 I/O，L686/718）、`BackendCancelledError` 分类（L791–798：closing/EMERGENCY 归静默，否则结构化 FAILED）、`BackpressurePolicy.BLOCK/DROP_NEWEST` + `BoundedSweepBuffer`（L184–278）——回放后端与 controller 配合的权威行为表 |
| 相关 | `src/uav_gpr/core/frequency.py` | 487 行 | `FrequencySweep`（L208–274：`metadata: TraceMetadata | None`，可无 metadata——v1 回放缺字段保持缺失的模型基础；数据不可变 frombuffer 快照） |
| 相关 | `src/uav_gpr/core/timeutil.py` / `tests/conftest.py` | — | `Clock` Protocol + `ManualClock`（`monotonic_ns()` 返回 `MonotonicNs`，符合 Protocol）；**`conftest.virtual_clock` 返回裸 int，不符合 `Clock` Protocol，t2 测试统一注入 `ManualClock`（016/017 基线单同口径）** |
| 相关 | `tests/contract/test_rcscan_reader.py` | 1325 行 / 39 测试 | t2 可复用的 v2 文件构造夹具：`create_writer`（L279–304）、`bulk_build_rcscan`（L331–441，整列快速构造 + 乱序 rows + checkpoint/lifecycle 覆盖）、`make_metadata`/`make_raw`/`make_gnss_match`（L166–257）、`corrupt_cell`（L444）、`file_sha256`（L460）；既有乱序物理记录测试 `test_out_of_order_physical_rows_are_sorted_in_logical_view`（L555）与无 GNSS 测试 `test_missing_gnss_rows_decode_without_fabrication`（L612）为回放测试的构造参照 |

### 3.3 复审报告与状态行证据

- ISSUE-011 复审：`docs/reports/ISSUE_011_REVIEW_REPORT.md` **PASS WITH CONDITIONS**（无 P0/P1/P2；3 项 P3 收尾条件不阻止合并；验收矩阵三标准全 PASS；审查者独立变异探针 3/3 被定向测试杀死）。合并提交 `57c4966`。
- ISSUE-015 复审：`docs/reports/ISSUE_015_REVIEW_REPORT.md` round-1 **FAIL**（P1-01：configure 未拒绝在途 acquire 并发重配）+ 3 项 P3；R2 独立复现修复后 **PASS WITH CONDITIONS**（28 passed 定向、全量门禁全绿）。合并提交 `2f11cd9`。
- ISSUE-017 复审：`docs/reports/ISSUE_017_REVIEW_REPORT.md` Round-1 **FAIL**（P1-01 close×configure 并发终态漂移 + 3 项 P3）→ Round-2 **VERDICT: PASS**（验收矩阵 11/11 PASS；定向 88 passed、依赖 53 passed、全量 703 passed/1 deselected、变异探针 90/90）。合并提交 `b8712c5`，状态标记 `9406b60`。
- M03 状态行实测（`docs/issues/M03_ACQUISITION.md`）：ISSUE-015 `Done`（L7）、ISSUE-016 `Done`（L44）、ISSUE-017 `Done`（L81）、ISSUE-018 `Planned`（L118）。
- **ISSUE-018 为下一个可执行 Issue 的判定**：直接依赖 ISSUE-011/015/017 均已完成并合入 `main`（合并提交 + tracked 代码/测试 + 复审报告多源一致）；M03 状态行 ISSUE-018 仍为 `Planned`；仓库内无任何回放实现（`grep -rn "FileReplayBackend" src tests` 零命中；`grep -rn "replay" src/uav_gpr/acquisition` 仅 `__init__.py` 模块 docstring 与 backend.py 文档字符串提及，无实现）；`docs/plans/` 无 issue-018 计划文档、`docs/reports/` 无 issue-018 报告；`src/uav_gpr/acquisition/` 目前仅 `__init__.py`、`backend.py`、`controller.py`、`scheduler.py`、`librevna/__init__.py`（占位）。

### 3.4 对 ISSUE-018 有约束的契约要点（读自 M03 L116–151、ACQUISITION.md §1/2、DATA_FORMAT.md §3.1/§6、PROCESSING.md §1/§9、ARCHITECTURE.md 状态/并发边界与 ISSUE_REVIEW_STANDARD.md）

**ISSUE-018 范围（M03 L126–131）+ 提示词**：

1. 回放 air/ground v2 和经 adapter 的 v1 raw（`FileReplayBackend`，基于严格 `RcScanReader`，逻辑 trace 顺序输出原始 `FrequencySweep`）。
2. 顺序/原始时间比例/加速/逐道模式，使用可取消等待；可由 `AcquisitionController` 暂停/恢复/停止。
3. 原样输出 trace identity/UTC/GNSS；文件缺失即保持缺失；**不用当前时间或 0 坐标补齐**。
4. 不重复应用文件已有校准/处理（`frequency_calibrated`/`time_base`/`time_processed` 不自动应用、不重复处理）。

**排除项（M03 L133–135）**：不实现处理 revision、UI 播放条或文件迁移。

**验收标准（M03 L137–141 原文，t2 不得削弱）**：

1. 回放 raw 与 reader 数值/axis/channel/metadata 对拍。
2. pause/resume/stop 与 controller 配合，无伪当前时间/位置。
3. 损坏/无 raw 文件明确拒绝。

**ACQUISITION.md**：§1 L9「真实后端、模拟后端和文件回放实现同一接口」；§2 L32「`FileReplayBackend` 原样保留文件元数据，不为缺失字段伪造当前时间或位置」；§4 配置冻结与回读（applied/diff 契约对回放同样成立：文件 config 是权威 applied，配置不符必须 fail-closed，不得静默改用文件外配置）。

**DATA_FORMAT.md §3.1（reader 契约，ISSUE-011 冻结）**：打开即校验 fail-closed；可见窗口只含 committed 且完整行；逻辑视图按 `trace_index` 排序（乱序补传在逻辑视图正确排序，物理行≠`trace_index`）；冲突身份在逻辑视图**排除**、`trace_by_index` 抛 `ID_CONFLICT`；`raw_trace_sha256` 缺存储 hash 的行呈现 `raw_trace_sha256=""`、`hash_verified=False` 并进报告——**消费方（本 Issue 回放）以 `hash_verified`/`report.issues` 为权威，不能只看字段值**。§6 空地文件差异：两端必须相同的是任务 ID、道索引/UID、频率轴、通道、原始数组、逐道 raw hash 与接收到的 GNSS 记录；calibrated/时域/历史是地面端可增项，回放**不得**把它们冒充 raw。

**PROCESSING.md**：§1 L11「任何阶段输出 `frequency_raw`（含 raw→raw 恒等）都拒绝」——回放不是处理阶段，输出文件原样 raw 是"搬运"而非"输出 frequency_raw 的处理结果"；§9 L82「保存/加载后对拍处理结果；**回放不重复 OSL 或背景**」；§7「任务后重处理当前只能从 `frequency_raw` 开始」——回放输出的 raw 是后续重处理的安全输入。

**controller 配合语义（controller.py 实测）**：worker `_tick` 是 `backend.acquire()` 唯一调用者（L787）；`pause` 只 `scheduler.pause()`（L550）→ 在途 sweep 完成并发布（安全边界），新 sweep 不发起；`stop` 只 `scheduler.cancel()`（L640）→ 在途完成、drain 已发布 sweep；`emergency_stop`/`close` 额外 `backend.cancel()`（L686/718）→ 在途 acquire 被 `BackendCancelledError` 中断且不发布（fail-closed）；`BackendCancelledError` 在非 closing/非 EMERGENCY 场景会触发结构化 FAILED（L791–798）——**回放后端若在 `_do_acquire` 内自行等待节奏，必须用 `_wait_cancellable`（honor cancel/close/timeout_s）而非裸 sleep**；回放暂停语义的等待归属（在途 gap 等待随安全边界完成）由 t2 计划明确。

**TESTING.md / 团队教训（禁固定 sleep）**：环境 `TZ=UTC`、`QT_QPA_PLATFORM=offscreen`、`--seed`；测试用事件/barrier/join/虚拟时钟等待条件，**不使用固定 `sleep` 猜时序**；任何 flaky test 视为缺陷。沿用 ISSUE-014/015/016/017 教训：**t2 inScope 一律用精确文件路径（非 glob），完成登记 changedPaths 必须与 inScope 逐一相等**；硬件双重 opt-in（本 Issue 纯软件，不引入 hardware 标记测试）。

### 3.5 发现的事实（非阻塞，供 t2 注意）

1. **工作树完全干净且与远端同步**：`git status --porcelain=v1 -b` 仅 `## main...origin/main` 一行（0/0）；非忽略未跟踪计数 = 0；t2 交付物将是唯一的新改动，冲突面为零。
2. **回放落点为空**：无 `FileReplayBackend`、无 replay 测试、无 issue-018 计划文档；`acquisition/__init__.py` 模块 docstring 已预留 "replay backends" 措辞，与 ISSUE-018 落点一致。t2 需要新增回放模块与契约测试（精确路径见第 5 节）。
3. **t2 测试注入口径**：`core.timeutil.ManualClock` 符合 `Clock` Protocol；`conftest.virtual_clock` 返回裸 int，**不可直接注入** backend/scheduler/controller（Protocol 检查会拒绝），t2 测试统一用 `ManualClock`（016/017 基线单同口径）。
4. **v2 测试夹具直接可复用**：`test_rcscan_reader.py` 的 `bulk_build_rcscan`（乱序 rows、checkpoint/lifecycle 覆盖、air/ground role）与 `corrupt_cell` 可构造回放测试所需的 v2 air/ground、乱序物理记录、无 GNSS、损坏文件；`RcScanReader` 对拍基准 = 同文件 `iter_logical` 的 `ReadTrace`（数值/axis/channel/metadata 全字段）。
5. **v1 回放路径无需改 rcscan_v1.py**：`RcScanV1Reader.data` 已暴露 `frequency`（`FrequencyScan`，trace×channel×freq raw）、`raw_row(index)`、`trace_timestamps_utc`（可 None）、`trace_extras`；v1 无 mission/逐道 UID/GNSS/单调时钟 → 对应字段保持缺失（`FrequencySweep.metadata=None` 或按 adapter 既有映射）；回放不得为 v1 伪造当前时间/0 坐标。
6. **回放节奏 × controller 调度是开放设计点（须在 t2 计划中明确）**：controller 以 `target_interval_s` 固定间隔调度（scheduler），而"原始时间比例/加速/逐道"需要逐道可变的等待；自然方案是回放后端在 `_do_acquire` 内用基类 `_wait_cancellable(seconds=gap*ratio, timeout_s=...)` 承担节奏等待（honor cancel/close/timeout_s），controller 侧配置小间隔避免双重门控；pause 安全边界（在途 gap 等待完成并发布）、stop drain、emergency/close 用 `backend.cancel()` 中断的语义必须逐一测试。t2 不得为节奏引入新的线程或固定 sleep。
7. M03 状态行与 README 依赖表（L85）一致，无计划冲突；本 Issue 为只读回放后端，不改变强制数据规则/空地职责/持久化语义（不落盘、不联网、不迁移），**无需新增 ADR**。M01 文档状态滞后属已知项（多期基线单记录过），不影响依赖判定。

## 4. 门禁基线（核查时实测复跑，2026-08-31）

环境：WSL Ubuntu / Python 3.12.3；numpy 2.5.2、pytest 8.4.2、h5py 3.16.0、ruff 0.16.4、mypy 1.20.2；`uav_gpr` editable 可导入（`src/`）。

```text
$ python3 -m pytest tests/contract/test_rcscan_reader.py \
    tests/contract/test_acquisition_backend.py \
    tests/contract/test_acquisition_controller.py -q
155 passed in 9.62s                            # 依赖定向回归（ISSUE-011：39 + ISSUE-015：28 + ISSUE-017：88）

$ python3 tools/quality/verify.py
703 passed, 1 deselected in 173.28s (0:02:53)   # 全量非硬件 pytest
All checks passed!                               # ruff
Success: no issues found in 38 source files      # mypy
package import ok                                # import 检查
[quality] all gates passed
VERIFY_EXIT=0                                    # run_gates 仅在全部通过时打印 all gates passed
```

核查前后 `git status --porcelain=v1 -b` **逐字节一致**（仅 `## main...origin/main` 一行）；`git diff --check` clean；无新缓存/日志/实测数据残留（`.pytest_cache/`/`.mypy_cache/`/`.ruff_cache/` 等已忽略，`git check-ignore` 确认；`.agent-teams/` 已忽略，非交付物）。

## 5. ISSUE-018 实施计划摘要（t2 执行契约，完整版见 t2 计划文档）

### 范围（M03 L126–131 原文口径 + 提示词）

1. `FileReplayBackend`（`AcquisitionBackend` 子类）：open 时经严格 `RcScanReader`（v2 air/ground）或 `RcScanV1Reader`（v1 adapter）打开文件并 fail-closed 校验；configure 时以文件 mission config 为权威 applied（requested/applied diff 如实记录，配置不符 fail-closed）；acquire 按逻辑 trace 顺序逐道输出原始 `FrequencySweep`。
2. 三种节奏模式（逐道/原始时间比例/显式加速），等待可取消（基类 `_wait_cancellable`，honor cancel/close/timeout_s），可被 `AcquisitionController` 暂停/恢复/停止（安全边界语义见 3.4/3.5-6）。
3. 原样保留 mission/trace ID、UTC/GNSS/缺失字段；**不用当前时间或 0 坐标补齐**；不自动应用已保存 calibrated/time 结果、不重复处理。
4. 损坏/无 raw 文件明确拒绝（打开期 schema 校验 + 逐道 `hash_verified`/报告分类的消费策略，t2 计划定稿）。

### 排除项（M03 L133–135 + 提示词，t2 不得越界）

不实现处理 revision、UI 播放条、文件迁移；不改 `core/` 既有公共语义、不改 `backend.py`/`scheduler.py`/`controller.py`/`rcscan_reader.py`/`rcscan_v1.py` 已冻结契约；不改两个参考项目；不 commit、不 push、不创建/切换分支；不进入 ISSUE-019。

### 验收标准（M03 L137–141 原文，t2 不得削弱）

1. 回放 raw 与 reader 数值/axis/channel/metadata 对拍。
2. pause/resume/stop 与 controller 配合，无伪当前时间/位置。
3. 损坏/无 raw 文件明确拒绝。

### 测试矩阵要点（提示词必测项 + 依赖回归）

- v2 air/ground 回放对拍（`iter_logical` 数值/axis/channel/metadata 逐字段）；v1 adapter 回放（缺字段保持缺失、不伪造）；
- 乱序物理记录（逻辑顺序输出）；无 GNSS 行（`gnss_match=None`、`GNSS_MISSING` 原样保留）；
- 三种节奏模式 + 取消（`backend.cancel()` 中断等待 → `BackendCancelledError`，无固定 sleep，虚拟时钟/事件驱动）；
- 与 `AcquisitionController` 配合：pause 安全边界、resume 不追债、stop drain、emergency_stop 中断在途、close 无残留线程；
- 损坏文件（schema 违例/截断/缺 raw）与无 raw 文件明确拒绝；
- 回归：依赖定向 155 passed（ISSUE-011/015/017）不被破坏；
- 门禁复跑：定向新测试 + 全量非硬件 pytest（`tools/quality/verify.py`）+ ruff + mypy + import + 工作树/diff 检查；**测试禁固定 sleep**。

### inScope 精确路径（以任务契约 t2 inScope 为准，实测自 team.json t2 契约；t2 完成登记 changedPaths 必须与此逐一相等；沿用 ISSUE-014/015/016/017 教训：精确文件路径，非 glob）

1. `src/uav_gpr/acquisition/replay.py`（新模块：`FileReplayBackend` + 节奏模式/错误类型）
2. `tests/contract/test_acquisition_replay.py`（新测试文件，失败测试优先）
3. `docs/plans/2026-08-30-issue-018-replay-backend.md`（计划文档，t2 先落盘）
4. `docs/issues/M03_ACQUISITION.md`（仅 ISSUE-018 状态行：`Planned → In progress → Review`，勿动其他条目）

（t1 基线单 `docs/reports/ISSUE_018_BASELINE_CONFIRMATION.md` 为 t1 交付物，不计入 t2 inScope；沿用 ISSUE-016/017 先例。t2 outOfScope 实测：`docs/reports/**`、`docs/ACQUISITION.md`、`docs/DATA_FORMAT.md`、`docs/PROCESSING.md`、`docs/TESTING.md`、`docs/adr/**`、`tools/**`、`src/uav_gpr/core/**`（只读消费）、`src/uav_gpr/storage/**`（只读消费，含 rcscan_reader/rcscan_v1）、`src/uav_gpr/acquisition/backend.py`/`scheduler.py`/`controller.py`（只读消费）。）

t2 验证命令按任务契约执行：`./.venv/Scripts/python.exe -m pytest tests/contract/test_acquisition_replay.py -q`（定向，先红灯后绿灯）、`./.venv/Scripts/python.exe tools/quality/verify.py`（全量）、`-m ruff check src tests`、`-m mypy src`、`git diff --check && git status --porcelain=v1 -b`；本机 WSL 侧若 `.venv/Scripts/python.exe` 不可用，以等价 `python3`（venv 或 editable src）执行并在执行日志注明解释器路径。

若确需拆分模块/新增文件，t2 必须先停止并向 captain 报告，不得自行新增范围外文件。

## 6. 结论

ISSUE-018 开工基线已锁定：`main`/HEAD @ `9406b60`（工作树完全干净、与 origin/main 同步 0/0）；三项直接依赖（ISSUE-011/015/017）的 tracked 代码、契约测试、合并提交与复审报告证据全部实测复现（011 经 `57c4966` 合入、PASS WITH CONDITIONS 后标记 Done；015 经 `2f11cd9` 合入、R2 PASS WITH CONDITIONS 后标记 Done；017 经 `b8712c5` 合入、Round-2 VERDICT=PASS 后标记 Done）；**ISSUE-018 是下一个可执行 Issue**（M03 状态行 `Planned`、无回放实现/测试/计划存在、依赖全绿）；契约要点（严格 reader 逻辑序输出、原样保留 identity/UTC/GNSS/缺失字段、禁伪当前时间/0 坐标、不重复应用校准/处理、损坏/无 raw fail-closed、可取消等待 + controller 暂停/恢复/停止配合、禁 sleep-based 测试、精确 inScope 路径）已固化于第 3.4/5 节；门禁基线全绿（全量 703 passed / 1 deselected、ruff/mypy(38 文件)/import 全过、依赖定向 155 passed），核查前后 git 状态一致、无残留。本确认单即为 t2 实现与 t3 独立复审的权威基线件；t2 按第 5 节契约执行（先落盘计划文档 → 失败测试优先 → 最小实现 → M03 状态行 → 门禁 → 报告），完成后停止，不进入 ISSUE-019。

> 后续记录：本单为开工时点的基线快照，不随 t2 改动；t2 实际完成记录见其完成报告与 `docs/plans/2026-08-30-issue-018-replay-backend.md`，t3 复审报告独立输出。
