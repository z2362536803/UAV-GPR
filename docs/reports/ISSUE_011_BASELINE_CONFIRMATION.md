# ISSUE-011 开工基线确认单（只读核查 + 实施计划）

日期：2026-08-30（ISSUE-011 开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-011-reader`（执行器 engineer，任务 t1）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支、未修改实现与测试。
配套文件：本单为 t2（实现 RcScanReader/Validator）与 t3（独立复审）的权威基线件；独立复审按 [ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) 执行。

## 1. 锁定的目标 Issue 与依据

**ISSUE-011：reader、严格校验与逻辑道排序**（`docs/issues/M02_STORAGE.md` 第 4 个条目，状态 `Planned`）

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-011（M02_STORAGE.md L116–151） | docs/issues/README.md 依赖顺序主表 L78 |
| 直接依赖 | ISSUE-008（schema/codec）、ISSUE-009（raw hash）、ISSUE-010（writer） | M02_STORAGE.md L119「直接依赖：ISSUE-008～010」 |
| 依赖状态 | 三者均 `Done`，均经独立审查 PASS WITH CONDITIONS 后由项目负责人授权合并进 `main` | M02_STORAGE.md L7/L44/L81；第 3 节 Git 与报告证据 |
| 提示词工作区替换 | `E:\wurenjiruanjian` → 工作区根（见第 2 节） | docs/issues/README.md 第 3 节通用协议 |
| 一次一 Issue | 本轮只处理 ISSUE-011；不进入 ISSUE-012 | docs/issues/README.md 第 1/3 节 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

两个参考项目 `E:\钢筋仪软件开发`、`E:\UVA_GPR_system` 不在本机挂载范围内；ISSUE-011 为纯只读 reader，无参考迁移需求，不触碰。

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      main（当前；工作树干净）
origin      https://github.com/z2362536803/UAV-GPR.git
HEAD        abfd312  chore: ignore .agent-teams runtime directory
相对远端    ahead 6（全部为 ISSUE-008/009/010 合并链，见下）
git status --porcelain=v1 -b   只有 "## main...origin/main [ahead 6]" 一行
```

依赖合并提交（`git log` / `git show --stat` 实测）：

| 提交 | 日期 | 内容 |
|---|---|---|
| `e852508` | 2026-08-28 | `Merge feat/issue-008: ISSUE-008 .rcscan v2 schema and codec`——新增 `src/uav_gpr/storage/rcscan_v2.py`（1556 行）、`tests/contract/test_storage_schema.py`（1341 行）、`tests/contract/rcscan_v2_golden.json`（581 行）、`docs/DATA_FORMAT.md`（+30 行） |
| `ee41360` | 2026-08-28 | `feat(core): add canonical raw trace hash and golden vectors (ISSUE-009)`（feature 提交，被 c10693f 合入） |
| `c10693f` | 2026-08-30 | `Merge feat/issue-009: ISSUE-009 canonical raw trace hash and golden vectors`——新增 `src/uav_gpr/core/raw_hash.py`（686 行）、`tests/contract/test_raw_trace_hash.py`（1123 行）、`tests/contract/raw_trace_hash_golden.json`（309 行）、`docs/DATA_FORMAT.md`（+42 行） |
| `0046bd1` | 2026-08-30 | `feat(storage): incremental writer, checkpoint and atomic finalize (ISSUE-010)`（feature 提交，被 4ec7d0e 合入） |
| `4ec7d0e` | 2026-08-30 | `Merge feat/issue-010: ISSUE-010 incremental writer, checkpoint and atomic finalize`——新增 `src/uav_gpr/storage/incremental_writer.py`（1043 行）、`tests/integration/test_incremental_writer.py`（2014 行）、ISSUE-010 报告/基线/计划 |
| `aab502c` | 2026-08-30 | `docs(issues): mark ISSUE-009/010 Done after authorized merges`（M02 状态行置 Done） |
| `abfd312` | 2026-08-30 | `chore: ignore .agent-teams runtime directory`（闭合 ISSUE-010 审查遗留 P3-6；`git check-ignore .agent-teams/` 实测已忽略） |

合并历史为 `0ddbd81`（PR #1，M01 ISSUE-006/007，2026-08-23）→ `e852508`（008）→ `c10693f`（009）→ `4ec7d0e`（010），后随 `aab502c` 状态标记与 `abfd312` gitignore；无 reset/rebase/强推迹象（本次未做历史改写）。`git ls-files` 确认三个依赖模块与两个黄金 manifest、契约/集成测试全部 tracked 于 main。

### 3.2 依赖 Issue 逐项核对（实际代码与测试证据）

| 依赖 | 交付物（main 内） | ISSUE-011 复用点 |
|---|---|---|
| ISSUE-008 `.rcscan` v2 schema/codec | `src/uav_gpr/storage/rcscan_v2.py` | `probe_rcscan_v2`（身份/版本/profile/role/lifecycle 探测）、`dataset_contracts`（全部 dtype/shape/maxshape/chunks/compression 契约）、`_validate_dataset_against_contract`、`trace_metadata_from_cells`（逐道行解码权威 codec）、`loads_utf8_json`、checkpoint 三列（`committed_record_count`/`last_trace_index`/`updated_utc`） |
| ISSUE-009 规范 raw hash | `src/uav_gpr/core/raw_hash.py` | `compute_raw_trace_sha256(mission_id, trace_index, trace_uid, channels, frequencies_hz, data)`（逐道重算，校验存储 hash 一致性） |
| ISSUE-010 增量 writer | `src/uav_gpr/storage/incremental_writer.py` | 夹具生成：`WritePhase` 故障注入（AFTER_RAW_WRITE / AFTER_TRACE_COLUMNS 等制造半写尾部）、`append_trace` 乱序/重复/冲突语义、`close()` 原子 finalize；`RcScanIncrementalWriter` 已证明物理行 ≠ trace_index |

依赖契约测试全绿（第 4 节门禁基线），依赖模块零工作树改动。

### 3.3 审查报告与授权证据

- `docs/reports/ISSUE_008_REVIEW_REPORT.md`：PASS WITH CONDITIONS → 授权合并（M02 L7）；
- `docs/reports/ISSUE_009_REVIEW_REPORT_R3.md`：round-3 PASS WITH CONDITIONS（3 条 P3 全关）→ 授权合并（M02 L44）；
- `docs/reports/ISSUE_010_REVIEW_REPORT_R2.md`：round-2 PASS WITH CONDITIONS（4 项 P2 全闭合）→ 授权合并（M02 L81）；
- ISSUE-010 遗留 P3-6（`.agent-teams/` 忽略）已由 `abfd312` 关闭；P3-4（axis 校验重复）与 `awaiting_rename` 状态识别按报告移交 ISSUE-011/012——**ISSUE-011 须承接 `awaiting_rename` 相关文件状态的可读性核对**（生命周期值 `writing/finalized/recovered` 由 ISSUE-008 冻结；`awaiting_rename` 是 writer 实例状态而非文件属性，reader 以文件内 `lifecycle_state` 为准）。

## 4. 门禁基线（核查时实测复跑）

环境：WSL Ubuntu 24.04 / Python 3.12.3；numpy 2.5.2、h5py 3.16.0、pytest 8.4.2、ruff、mypy；`uav_gpr` editable 可导入（`src/`）。仓库同时存在 Windows `.venv`（审查报告环境），本单按既有基线口径使用 WSL Python 复跑。

```text
$ python3 tools/quality/verify.py
[quality] pytest (non-hardware) ok   435 passed, 1 deselected in 8.23s
[quality] ruff                   ok   Success: no issues found in 31 source files
[quality] mypy                   ok   Success（strict, 31 files）
[quality] package import         ok
[quality] all gates passed

$ python3 -m pytest tests/contract/test_storage_schema.py \
    tests/contract/test_raw_trace_hash.py tests/integration/test_incremental_writer.py -q
193 passed in 4.63s
```

核查后 `git status` 与核查前一致（仅 `## main...origin/main [ahead 6]`），无缓存/日志/实测数据残留（`git check-ignore` 确认 `.agent-teams/`、`*.rcscan`、`*.partial.rcscan` 已忽略）。

## 5. ISSUE-011 实施计划（t2 执行契约）

### 5.1 范围（in scope）

1. **新模块 `src/uav_gpr/storage/rcscan_reader.py`**：只读 `RcScanReader` + `RcScanValidator`（或等价命名，t2 以失败测试先钉住公共语义）：
   - **严格打开校验（fail-closed）**：在 `probe_rcscan_v2` 基础上完整校验——未知 schema 版本/未知 profile/错误 format_name → `DomainError`（`UNSUPPORTED_SCHEMA_VERSION`/`INVALID_ARGUMENT`）；对每个已存在数据集按 `dataset_contracts` 校验 dtype/maxshape/chunks/compression/固定轴长度；role 规则（air 必须有 `/transport` 且按冻结结构校验；ground 可无）；`lifecycle_state ∈ {writing, finalized, recovered}`。
   - **checkpoint 完整性**：`committed_record_count` 必须为单元素 int64 且 ∈ `[0, 全部必需 trace-major 列与 /frequency/raw 的最小长度]`；损坏（非 int、负值、越过列长度、缺失）→ fail-closed 拒绝；`last_trace_index`/`updated_utc` 格式合法。
   - **可见窗口**：只暴露物理行 `< committed_record_count` 且所有必需列在该行完整的记录（半写尾部天然不可见）。
   - **双视图 + 分块**：物理提交顺序迭代/分块读取；按显式 `trace_index/trace_uid` 排序的逻辑视图/分块读取；`/frequency/raw` 与行列按块切片（h5py 分块、`chunk_rows` 参数），不强制全文件驻内存。
   - **逐道解码**：行解码只走 `trace_metadata_from_cells`（单一权威 codec），raw 经 `compute_raw_trace_sha256` 重算。
   - **缺道/重复/冲突报告**：结构化 `ValidationReport`（可序列化）——缺道清单（区间空洞；`planned_trace_count` 存在时按其核对）、重复（同 index/同 uid 同 hash）、冲突（同 index 不同 hash、同 uid 不同 index）、hash 不一致、ID 非法（uid/index 非规范）。schema/checkpoint 级问题 fail-closed 抛出；数据级问题进入报告（"明确拒绝/报告"双语义由测试钉死：schema 级拒绝，数据级报告）。
2. **失败测试优先**：`tests/contract/test_rcscan_reader.py`（或按 t2 判定放 integration），先写能失败的测试再写最小实现。必测清单（对应提示词验收）：
   - 半写尾部不可见（writer 故障注入 AFTER_RAW_WRITE / AFTER_TRACE_COLUMNS 制造列短缺，reader 只见 checkpoint 内完整记录）；
   - 乱序物理行（trace_index 5,1,3 追加）→ 物理视图保序、逻辑视图按 index 排序；
   - 缺道清单（含 planned_trace_count 口径）；
   - 重复同 hash → 报告而非报错；冲突不同 hash → 明确拒绝或报告（钉死语义）；
   - 损坏 checkpoint（越界/负数/非 int/缺失）→ fail-closed；
   - 未知版本（如 3、2.5）→ fail-closed；
   - 缺 GNSS 行（valid=0）解码合法且原因保留；
   - 可选 processed 组（time_base/time_processed/calibrated）缺失合法、存在则按契约校验；
   - 存储 hash ≠ 重算 hash → 报告；
   - 非法 trace_uid/trace_index → 报告；
   - 大合成文件（≥2000 道）分块读取，块尺寸有界、逐块独立；
   - `writing` 生命周期 partial 可读（为 ISSUE-012 留扩展点）；finalized 重命名文件端到端可读；ground 无 transport 可读。
3. **文档**：`docs/DATA_FORMAT.md` 增加 reader 契约小节（沿用 ISSUE-008/009 冻结契约入文的模式：可见窗口、fail-closed 边界、双视图语义、报告分类），`docs/issues/M02_STORAGE.md` 状态行 `Planned → In progress`（完成后由人工置 Review/Done），`docs/plans/2026-08-30-issue-011-reader.md` 记录实施与门禁证据。
4. **门禁复跑**：定向新测试 + 全量非硬件 pytest + Ruff + mypy strict + `verify.py` + 工作树/diff 检查。

### 5.2 排除项（out of scope）

不修复/改写文件、不自动迁移（ISSUE-012/013）、不运行处理（ISSUE-030+）、不实现网络 ACK/outbox/UI、不触碰两个参考项目、不改 `rcscan_v2.py`/`raw_hash.py`/`incremental_writer.py` 的既有公共语义；不 commit、不 push、不创建/切换分支；不进入 ISSUE-012。

### 5.3 验收标准（M02_STORAGE.md L137–141 原文，t2 不得削弱）

1. 尾部半写记录不可见；
2. 乱序补传可正确排序；
3. 未知版本、损坏 checkpoint、重复索引不同 hash 被明确拒绝/报告；
4. 大合成文件可分块读取。

### 5.4 风险

- **风险 1**：冲突语义（拒绝 vs 报告）存在设计空间——t2 以失败测试先钉死：schema/checkpoint 级必须 fail-closed（DomainError），数据级（hash 不一致、重复、冲突）进入 ValidationReport；逻辑视图对冲突 index 的策略（省略并报告）也须测试固定，避免 ISSUE-014 依赖落空。
- **风险 2**：`trace_metadata_from_cells` 行解码对损坏单元格抛 `ValueError/DomainError`——reader 须定义逐行解码失败归类（报告为 ID/单元格问题 vs 整体拒绝），测试钉死，避免静默跳过。
- **风险 3**：本环境 HDF5 行为与 Windows .venv 的差异（既有基线已接受 WSL 口径）；大文件分块测试用合成数据，不引入实测数据。
- **无设计冲突**：ISSUE-011 只读，不触碰 writer/checkpoint 语义；`awaiting_rename` 为 writer 实例状态，不影响文件级生命周期校验。

## 6. 结论

ISSUE-011 开工基线已锁定：`main` @ `abfd312`（工作树干净，ahead 6 = 008/009/010 合并链）；三项依赖（ISSUE-008/009/010）的代码、契约测试、独立审查报告与授权合并证据全部实测复现；门禁基线 435 passed/1 deselected、ruff/mypy/import 全绿、定向依赖测试 193 passed。本确认单即为 t2 实现与 t3 独立复审的权威基线件；t2 按第 5 节契约执行（先失败测试→最小实现→门禁→报告），完成后停止，不进入 ISSUE-012。

> 后续记录：本单为开工时点的基线快照，不随 t2 改动；t2 实际完成记录见其完成报告与 `docs/plans/2026-08-30-issue-011-reader.md`，t3 复审报告独立输出。
