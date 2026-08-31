# ISSUE-015 开工基线确认单（只读核查 + 实施计划）

日期：2026-08-30（ISSUE-015 开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-015-simulated-backend`（执行器 engineer，任务 t1）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支、未修改实现与测试/计划文件。
配套文件：本单为 t2（实现 AcquisitionBackend 契约与确定性模拟器）与 t3（独立复审）的权威基线件；独立复审按 [ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) 执行。t2 完整实施契约见 t2 计划文档（`docs/plans/2026-08-30-issue-015-acquisition-backend.md`，由 t2 先落盘）。

## 1. 锁定的目标 Issue 与依据

**ISSUE-015：AcquisitionBackend 契约与确定性模拟器**（`docs/issues/M03_ACQUISITION.md` 第 1 个条目，状态 `Planned`）

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-015（M03_ACQUISITION.md L5–40） | docs/issues/README.md 依赖顺序主表 L82 |
| 直接依赖 | ISSUE-004（不可变通道与频域数据模型）、ISSUE-005（GNSS、道元数据与质量状态模型）、ISSUE-006（MissionConfig、时窗推导与配置摘要） | M03 L8「直接依赖：ISSUE-004～006」；README.md L82 |
| 依赖状态 | 三者均已合入 `main`，tracked 代码/测试/合并提交为权威证据（见第 3 节）；M01 状态行仍写 `Planned` 属已知文档滞后 | git log/ls-files；ISSUE_001_005_REVIEW_SUMMARY.md；ISSUE_008_BASELINE_CONFIRMATION.md L50 |
| 功能映射 | FR-003（多通道 sweep 数据）、FR-018（模拟/回放驱动应用测试） | M03 L10；PRODUCT_REQUIREMENTS.md |
| 提示词工作区替换 | `E:\wurenjiruanjian` → 工作区根（见第 2 节） | docs/issues/README.md 第 3 节通用协议 |
| 一次一 Issue | 本轮只处理 ISSUE-015；不进入 ISSUE-016 | docs/issues/README.md 第 1/3 节 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

参考项目 `E:\钢筋仪软件开发`、`E:\UVA_GPR_system` 的原始路径不在本机挂载范围内（WSL 仅挂载 C/D 盘）；本 Issue 为纯逻辑后端契约 + 确定性模拟器，不新增参考源依赖（沿用 ISSUE-001 manifest 与 ISSUE-003～006 已冻结契约）。

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      feat/issue-013（当前；工作树非干净，含 ISSUE-013/014 在制未提交产物，见 3.5）
HEAD        09037496435eea0e6842ccb71b920b743fafd217  docs(data-format): freeze ISSUE-012 non-destructive recovery contract (4.1)
分支关系    feat/issue-013 与 main 均指向 0903749（merge-base == HEAD == main）；main 相对 origin/main ahead 1（该提交未推送）
git status --porcelain=v1 -b
    ## feat/issue-013
     M docs/issues/M02_STORAGE.md
    ?? docs/plans/2026-08-30-issue-013-v1-migration.md
    ?? docs/plans/2026-08-30-issue-014-inventory.md
    ?? docs/reports/ISSUE_013_BASELINE_CONFIRMATION.md
    ?? docs/reports/ISSUE_013_REVIEW_REPORT.md
    ?? docs/reports/ISSUE_014_BASELINE_CONFIRMATION.md
    ?? docs/reports/ISSUE_014_REVIEW_REPORT.md
    ?? src/uav_gpr/storage/inventory/
    ?? src/uav_gpr/storage/rcscan_v1.py
    ?? tests/contract/rcscan_v1_golden.json
    ?? tests/contract/test_rcscan_v1.py
    ?? tests/integration/test_inventory.py
```

依赖合并提交（`git log --oneline main` 实测，全部在 main 历史中）：

| 提交 | 内容 |
|---|---|
| `45c5657` | `feat(core): add immutable frequency models`（ISSUE-004 feature 提交） |
| `952883e` | `feat(core): add GNSS and trace metadata models`（ISSUE-005 feature 提交） |
| `b11e741` | `fix(issue-005): enforce trace metadata integrity and GNSS consistency`（ISSUE-005 复审修复） |
| `22b0b0f` | `feat(core): add mission configuration contracts`（ISSUE-006 feature 提交） |
| `bcef87c` | `fix(issue-006): harden mission configuration contracts`（ISSUE-006 复审修复） |
| `0ddbd81` | `Merge pull request #1 from z2362536803/feat/m01-issues-006-007`（ISSUE-006/007 合入 main） |

合并历史：M01 批次（001–005 经 `ISSUE_001_005_REVIEW_SUMMARY.md` PASS 合入，006/007 经 PR #1 `0ddbd81` 合入）→ 008（`e852508`）→ 009（`c10693f`）→ 010（`4ec7d0e`）→ 011（`57c4966`）→ 012（`24d3505`）→ 当前 HEAD `0903749`；无 reset/rebase/强推迹象。`git ls-files` 确认 ISSUE-004/005/006 交付模块与测试全部 tracked 于 main。

### 3.2 依赖交付物（main 内实测，行数为 `wc -l` 实测）

| 依赖 | 交付物（main，tracked） | 行数 | ISSUE-015 复用点 |
|---|---|---|---|
| ISSUE-004 | `src/uav_gpr/core/channels.py` | 49 | `ChannelSpec`（channel_id/极化/S 参数/display_name），后端能力声明与 sweep 通道契约 |
| ISSUE-004 | `src/uav_gpr/core/frequency.py` | 487 | `FrequencySweep`（`channel × frequency` 复数只读、严格递增频率轴、`with_metadata` fail-closed 演进）——模拟器输出对象；`FrequencyScan` 连续模型 |
| ISSUE-004 | `tests/unit/test_core_frequency.py` | 556（34 个测试函数） | sweep 契约回归 |
| ISSUE-005 | `src/uav_gpr/core/gnss.py` | 375 | `GnssFix`/`GnssMatch`（GNSS/无 GNSS 场景，`gnss_missing` 显式原因）——模拟器可注入 GNSS/无 GNSS |
| ISSUE-005 | `src/uav_gpr/core/metadata.py` | 428 | `TraceMetadata`（mission/trace ID、sweep start/mid/finish 的 UTC+monotonic、目标/实际间隔、`connection_generation`、raw hash、GNSS match、质量状态）——模拟器每道 metadata 构建 |
| ISSUE-005 | `tests/unit/test_core_gnss.py` + `test_core_metadata.py` | 352 + 527（18 + 29 个测试函数） | GNSS/道元数据契约回归 |
| ISSUE-006 | `src/uav_gpr/core/config.py` | 925 | `MissionConfig`（冻结配置、`frequency_axis_hz` 推导、`config_sha256`、schema/protocol 版本 fail-closed）；`ConfigDiff.compute`（requested/applied 字段级差异）——backend configure 契约核心 |
| ISSUE-006 | `src/uav_gpr/core/enums.py` | 184 | `AcquisitionMode`/`SParameter`/`LogicalPolarization`/`GnssNoFixPolicy` 等稳定枚举 |
| ISSUE-006 | `src/uav_gpr/core/errors.py` | 158 | `ErrorCode` + `DomainError`（结构化错误，业务分支只按 code）——backend 错误契约必须复用 |
| ISSUE-006 | `tests/unit/test_core_config.py` | 681（45 个测试函数） | 配置契约回归 |
| ISSUE-003 | `src/uav_gpr/core/timeutil.py` | 132 | `Clock` Protocol + `SystemClock`/`ManualClock`（可注入 UTC+monotonic 时钟）——模拟器确定性虚拟时钟基础；`MonotonicNs` |
| 相关 | `tests/unit/test_core_time.py` / `test_core_errors.py` / `test_core_enums.py` / `test_core_identifiers.py` / `test_core_isolation.py` | 110/121/114/94/42 | 时间/错误/枚举/ID/隔离回归 |

### 3.3 复审报告与状态行证据

- `docs/reports/ISSUE_001_005_REVIEW_SUMMARY.md`：**PASS**（ISSUE-001～005 实现与复审修复已合入 `main` 并推送）；最终实现提交 `b11e741f…`；全量门禁 `162 passed, 1 deselected`、ruff/mypy/import 全绿。
- ISSUE-006/007 经 PR #1（`0ddbd81`）合入 main，后续基线单（ISSUE_008/009/011）均以 tracked 代码与提交为权威确认其完成（ISSUE_008_BASELINE_CONFIRMATION.md L50–51）。
- M01 状态行实测（`sed -n` 逐行）：ISSUE-004 `Planned`（L118）、ISSUE-005 `Planned`（L155）、ISSUE-006 `Planned`（L192）——**全部仍写 `Planned` 属 M01 里程碑文档滞后（已知事实）**，以第 3.1/3.2 节的 tracked 代码/测试/合并提交与复审总结为权威。
- M02 状态行实测：ISSUE-008～012 `Done`（L7/44/81/118/155），ISSUE-013 `Review`（L192）、ISSUE-014 `Review`（L229）。
- M03 状态行实测：ISSUE-015 `Planned`（L7）、ISSUE-016 `Planned`（L44）、ISSUE-017 `Planned`（L81）、ISSUE-018 `Planned`（L118）。

**ISSUE-015 为下一个可执行 Issue 的判定**：直接依赖 ISSUE-004/005/006 均已完成并合入 `main`（合并提交 + tracked 代码/测试 + 复审总结多源一致）；ISSUE-013/014 为 `Review`（实现/测试完成、复审报告 PASS WITH CONDITIONS，等待人工验收），且按 M03 L8 与 README L82 **不是 ISSUE-015 的依赖**——ISSUE-015 开工不依赖 013/014 合入。

### 3.4 对 ISSUE-015 有约束的契约要点（读自 ACQUISITION.md、DATA_MODEL.md、TESTING.md）

**ACQUISITION.md（§1/2/4/5/9/10）**：

1. 真实/模拟/回放实现**同一 backend 接口**；生命周期 `open -> configure(frozen config) -> acquire sweeps -> pause/resume -> stop -> close`。
2. 接口必须提供：设备身份/固件协议能力/支持通道；配置校验与实际生效配置回读；完整 `FrequencySweep` 输出；可取消的阻塞等待；可分类错误与设备重连代数；幂等、安全的 stop/close。
3. `requested_config`（地面冻结配置）与 `applied_config`（硬件回读/确认配置）必须**分别记录**，`config_diff` 说明设备量化或拒绝原因（复用 `ConfigDiff`，字段唯一、按契约排序、每项为实际变化）。
4. sweep 完整性：超时或缺点的 sweep **不能用零填充冒充完整道**；`trace_index` 只在完整 sweep 被任务接受时分配。
5. 暂停/停止/故障语义（§9）：`pause` 停止发起新 sweep、等待当前 sweep 到安全边界；`stop` 不再发起新 sweep、drain 已完整 sweep；所有操作**幂等**，重复命令返回已有结果。
6. 验收（§10）：合成数据证明数组形状/通道顺序/时间戳正确；缺点/重复点/错序/CRC 错/超时被拒绝；暂停/恢复不重复 `trace_index`；`connection_generation` 语义清晰。

**DATA_MODEL.md（§1/2/5/6）**：

1. 领域对象默认不可变；NumPy 数组由对象拥有并设为只读；ID 为规范 UUID；枚举持久化为稳定小写字符串。
2. `FrequencySweep`：`data` 复数只读 `channel × frequency`；`frequencies_hz` 一维严格递增只读；`channels` 长度/顺序稳定；`metadata` 完整 `TraceMetadata`；`history` 原始采集对象必须为空。
3. `TraceMetadata`：sweep start/mid/finish 的 UTC+monotonic 各自保存；首道 `actual_interval_s`/`schedule_error_s` 可为空；`connection_generation` 非负；raw hash 可后附（`with_integrity` 演进）。
4. 配置版本 fail-closed：`SUPPORTED_CONFIG_SCHEMA_VERSIONS`/`SUPPORTED_PROTOCOL_VERSIONS` 常量；未知版本以 `unsupported_schema_version`/`unsupported_protocol_version` 拒绝。
5. 错误必须携带结构化代码和上下文（`ErrorCode` + `DomainError`），不得只依赖中文异常字符串判断流程。

**TESTING.md（§1/2.1/3/4）**：

1. `tests/unit` 为纯领域/算法/状态机默认 CI 目录；Acquisition 必测：配置回读、暂停/恢复、取消、重连。
2. 故障注入必须**确定性**：USB timeout、半个 sweep、GNSS 断串口等；使用事件/barrier/虚拟时钟等待条件，**不使用固定 `sleep` 猜并发时序**。
3. 全局 `--seed`（默认 0）重置 `random`/`numpy.random`；`virtual_clock` fixture（UTC+monotonic 可推进）；`TZ=UTC`、`QT_QPA_PLATFORM=offscreen`。
4. 模拟器与回放元数据真实性：不伪造当前时间/位置（对应 ACQUISITION.md §2 SimulatedBackend 的 GNSS/无 GNSS 场景）。
5. 硬件双重 opt-in（`--hardware` + `UAV_GPR_HARDWARE_OPTIN=1`）——本 Issue 为纯软件模拟器，不应引入 hardware 标记测试。

### 3.5 发现的事实（非阻塞，供 t2 注意）

1. **工作树非干净且含 ISSUE-013/014 在制产物**：`docs/issues/M02_STORAGE.md` 已被 ISSUE-013/014 修改（`git diff` 实测为两处状态行 `Planned → Review`）；另有 11 项未提交文件/目录（013 计划/基线/复审报告、`rcscan_v1.py` + golden + 契约测试、014 计划/基线/复审报告、`storage/inventory/`、`tests/integration/test_inventory.py`）。这些是**既有用户/前一 Issue 工作**：ISSUE-015 交付物必须与其文件隔离（不触碰、不覆盖、不删除）；t2 更新 M03 状态行（015 `Planned → In progress`）时只做最小编辑。
2. `main` 相对 `origin/main` ahead 1（`0903749` 未推送）——历史遗留，非本次引入，不影响 015 开工。
3. 门禁扫描天然包含 ISSUE-013/014 未提交文件（ruff/mypy 扫描 35 个源文件 = 33 tracked + `rcscan_v1.py` + `inventory/__init__.py`；pytest 全量 562 含 `test_rcscan_v1.py` 与 `test_inventory.py`），本单门禁数字为含 013/014 在制产物的实际现状，属预期。
4. `src/uav_gpr/acquisition/` 包已存在（`__init__.py` 为文档字符串占位，tracked；`librevna/__init__.py` 为边界占位），是 ISSUE-015 的既定落点；无任何既有 acquisition 实现或测试。
5. M01 文档状态滞后（3.3）不影响 015 依赖判定；M03 状态行与 README 依赖表（L82）一致，无计划冲突，无需新增 ADR（本 Issue 不改变强制数据规则/空地职责/持久化语义）。

## 4. 门禁基线（核查时实测复跑，2026-08-30）

环境：WSL Ubuntu 24.04 / Python 3.12.3；numpy 2.5.2、h5py 3.16.0、pytest 8.4.2；`uav_gpr` editable 可导入（`src/`）。

```text
$ python3 tools/quality/verify.py
562 passed, 1 deselected in 118.57s (0:01:58)   # 全量非硬件 pytest（含 013/014 在制测试）
All checks passed!                               # ruff
Success: no issues found in 35 source files      # mypy
package import ok                                # import 检查
[quality] all gates passed
VERIFY_EXIT=0

$ python3 -m pytest tests/unit/test_core_frequency.py tests/unit/test_core_config.py \
    tests/unit/test_core_gnss.py tests/unit/test_core_metadata.py \
    tests/unit/test_core_time.py tests/unit/test_core_errors.py \
    tests/unit/test_core_enums.py tests/unit/test_core_identifiers.py \
    tests/unit/test_core_isolation.py -q
179 passed in 0.56s                              # 依赖定向回归（ISSUE-003/004/005/006）
```

核查前后 `git status --porcelain=v1 -b` **逐字节一致**（同一 13 条目快照：1 个 M02 状态行修改 + 12 项 ISSUE-013/014 未提交产物，均核查前已存在）；`git diff --check` clean；无新缓存/日志/实测数据残留（`.pytest_cache/`/`.mypy_cache/`/`.ruff_cache/` 等已忽略，`git check-ignore` 确认；`data/`、`runs/`、`outbox/`、`*.rcscan` 均被忽略且无新增）。

## 5. ISSUE-015 实施计划摘要（t2 执行契约，完整版见 t2 计划文档）

### 范围（M03 L13–17 原文口径 + 提示词）

1. 定义 `AcquisitionBackend` 生命周期/能力/错误契约：`open/configure/acquire/cancel/close`、`capabilities`、`requested/applied config`（`config_diff` 复用 `ConfigDiff`）。
2. 实现确定性 `SimulatedBackend`：按 seed/config/可注入 `Clock`（复用 `core.timeutil` 的 `Clock` Protocol/`ManualClock` 模式）生成多通道 `FrequencySweep`，真实 shape/axis/UTC+monotonic metadata（复用 `TraceMetadata`/`GnssFix`/`GnssMatch`）。
3. 故障注入：timeout、半道、配置拒绝、设备断开、延迟；可取消阻塞等待。
4. 资源所有权与幂等 close/cancel；错误使用 core 结构化错误（`DomainError` + `ErrorCode`）。
5. 单/双通道共用接口；非法生命周期结构化拒绝（fail-closed）。

### 排除项（M03 L19–20 + 提示词，t2 不得越界）

不实现调度循环/controller（ISSUE-016/017）、Qt、HDF5、GNSS reader 或 LibreVNA USB；不改 `core/` 既有公共语义；不改两个参考项目；不做 GUI；不 commit、不 push、不创建/切换分支；不进入 ISSUE-016。

### 验收标准（M03 L24–28 原文，t2 不得削弱）

1. 相同 seed/config/虚拟 clock 产生相同 raw；错误按计划在确定道触发。
2. 单/双通道共用接口，非法生命周期被结构化拒绝。
3. cancel/close 不遗留线程或等待。

### 测试矩阵要点（提示词必测项 + 依赖回归）

- 生命周期（open→configure→acquire→cancel/close 全序、非法/重复生命周期结构化拒绝、幂等 close/cancel）；
- 确定性（同 seed/config/clock 的 raw 逐值相等；不同 seed 不同 raw）；
- 单/双通道共用接口（channel×frequency shape、通道顺序）；
- requested/applied config 与 `config_diff`（配置拒绝场景）；
- 故障点注入（timeout、半道、配置拒绝、断开、延迟——在确定道触发，不依赖固定 sleep）；
- cancel/close 资源清理（无遗留线程/等待，用事件/barrier/虚拟时钟断言）；
- 回归：ISSUE-003/004/005/006 定向（`test_core_frequency.py`/`test_core_config.py`/`test_core_gnss.py`/`test_core_metadata.py`/`test_core_time.py` 等，当前 179 passed）；
- 门禁复跑：定向新测试 + 全量非硬件 pytest（`tools/quality/verify.py`）+ ruff + mypy + import + 工作树/diff 检查。

## 6. 结论

ISSUE-015 开工基线已锁定：`main`/HEAD @ `0903749`（工作树非干净——含 ISSUE-013/014 在制未提交产物，交付须隔离，非阻塞）；三项依赖（ISSUE-004/005/006）的 tracked 代码、契约测试、合并提交与复审总结证据全部实测复现（M01 状态行滞后为已知事实，不影响判定）；ISSUE-013/014 为 `Review` 且非 015 依赖，**ISSUE-015 是下一个可执行 Issue**；契约要点（backend 统一生命周期、requested/applied + config_diff、sweep 完整性 fail-closed、确定性虚拟时钟、结构化错误、幂等 stop/close、测试禁 sleep）已固化于第 3.4 节；门禁基线全绿（全量 562 passed / 1 deselected、ruff/mypy/import 全过、依赖定向 179 passed），核查前后 git 状态一致、无残留。本确认单即为 t2 实现与 t3 独立复审的权威基线件；t2 按本节契约执行（先落盘计划文档 → 失败测试优先 → 最小实现 → M03 状态行 → 门禁 → 报告），完成后停止，不进入 ISSUE-016。

> 后续记录：本单为开工时点的基线快照，不随 t2 改动；t2 实际完成记录见其完成报告与 `docs/plans/2026-08-30-issue-015-acquisition-backend.md`，t3 复审报告独立输出。
