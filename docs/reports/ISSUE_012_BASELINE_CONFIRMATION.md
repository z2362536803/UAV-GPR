# ISSUE-012 开工基线确认单（只读核查 + 实施计划）

日期：2026-08-30（ISSUE-012 开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-012-recovery`（执行器 engineer，任务 t1）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支、未修改实现与测试。
配套文件：本单为 t2（实现 partial 只读检查与非破坏恢复 API）与 t3（独立复审）的权威基线件；独立复审按 [ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) 执行。t2 完整实施契约见 [docs/plans/2026-08-30-issue-012-recovery.md](../plans/2026-08-30-issue-012-recovery.md)。

## 1. 锁定的目标 Issue 与依据

**ISSUE-012：partial 检查与非破坏恢复**（`docs/issues/M02_STORAGE.md` 第 5 个条目，状态 `Planned`）

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-012（M02_STORAGE.md L153–188） | docs/issues/README.md 依赖顺序主表 L79 |
| 直接依赖 | ISSUE-010（增量 writer/checkpoint/原子 finalize）、ISSUE-011（reader/严格校验/逻辑排序） | M02_STORAGE.md L156「直接依赖：ISSUE-010、011」 |
| 依赖状态 | 两者均 `Done`，均经独立审查 PASS WITH CONDITIONS 后由项目负责人授权合并进 `main` | M02_STORAGE.md L81/L118；第 3 节 Git 与报告证据 |
| 提示词工作区替换 | `E:\wurenjiruanjian` → 工作区根（见第 2 节） | docs/issues/README.md 第 3 节通用协议 |
| 一次一 Issue | 本轮只处理 ISSUE-012；不进入 ISSUE-013 | docs/issues/README.md 第 1/3 节 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

两个参考项目 `E:\钢筋仪软件开发`、`E:\UVA_GPR_system` 不在本机挂载范围内；ISSUE-012 为纯存储层恢复 API，无参考迁移需求，不触碰。

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      main（当前；工作树干净）
origin      https://github.com/z2362536803/UAV-GPR.git
HEAD        db95817  docs(issues): mark ISSUE-011 Done after authorized merge
相对远端    ahead 9（全部为 ISSUE-008/009/010/011 合并链与状态/文档提交，见下）
git status --porcelain=v1 -b   只有 "## main...origin/main [ahead 9]" 一行
```

依赖合并提交（`git log` / `git show --stat` 实测）：

| 提交 | 日期 | 内容 |
|---|---|---|
| `ee41360` | 2026-08-28 | `feat(core): add canonical raw trace hash and golden vectors (ISSUE-009)`（feature 提交，被 c10693f 合入） |
| `0046bd1` | 2026-08-30 | `feat(storage): incremental writer, checkpoint and atomic finalize (ISSUE-010)`（feature 提交，被 4ec7d0e 合入） |
| `c10693f` | 2026-08-30 | `Merge feat/issue-009: ...`——ISSUE-009 合入 main |
| `4ec7d0e` | 2026-08-30 | `Merge feat/issue-010: ...`——ISSUE-010 合入 main（`src/uav_gpr/storage/incremental_writer.py` 1043 行、`tests/integration/test_incremental_writer.py` 2014 行） |
| `aab502c` | 2026-08-30 | `docs(issues): mark ISSUE-009/010 Done after authorized merges` |
| `abfd312` | 2026-08-30 | `chore: ignore .agent-teams runtime directory` |
| `097a20e` | 2026-08-30 | `feat(storage): read-only rcscan reader, strict validation and logical ordering (ISSUE-011)`（feature 提交） |
| `57c4966` | 2026-08-30 | `Merge feat/issue-011: ...`——ISSUE-011 合入 main（`src/uav_gpr/storage/rcscan_reader.py` 1070 行、`tests/contract/test_rcscan_reader.py` 1325 行、`docs/DATA_FORMAT.md` +13、`docs/plans/2026-08-30-issue-011-reader.md`、`docs/reports/ISSUE_011_BASELINE_CONFIRMATION.md`、`docs/reports/ISSUE_011_REVIEW_REPORT.md`） |
| `db95817` | 2026-08-30 | `docs(issues): mark ISSUE-011 Done after authorized merge`（M02 状态行置 Done） |

合并历史为 `e852508`（008）→ `c10693f`（009）→ `4ec7d0e`（010）→ `57c4966`（011），后随状态/文档提交；无 reset/rebase/强推迹象（本次未做历史改写）。`git ls-files` 确认依赖模块与测试全部 tracked 于 main：

```text
src/uav_gpr/core/raw_hash.py
src/uav_gpr/storage/incremental_writer.py
src/uav_gpr/storage/rcscan_reader.py
src/uav_gpr/storage/rcscan_v2.py
tests/contract/test_rcscan_reader.py
tests/integration/test_incremental_writer.py
```

### 3.2 依赖 Issue 逐项核对（实际代码与测试证据）

| 依赖 | 交付物（main 内） | ISSUE-012 复用点 |
|---|---|---|
| ISSUE-010 增量 writer | `src/uav_gpr/storage/incremental_writer.py` | 夹具生成：`WritePhase` 故障注入（AFTER_RAW_WRITE / AFTER_TRACE_COLUMNS / AFTER_DATA_FLUSH / AFTER_CHECKPOINT_WRITE / AFTER_COMMIT_FLUSH / BEFORE_FINALIZE 等）制造崩溃 partial；`RcScanIncrementalWriter.create` 冻结 mission/config/axis/channels 契约；`close()` 原子 finalize 语义 |
| ISSUE-011 只读 reader/validator | `src/uav_gpr/storage/rcscan_reader.py` | `RcScanReader` 打开即校验（schema/checkpoint/列契约 fail-closed）、`validation_report()`（缺道/重复/冲突/逐行 issue 分类）、`rename_pending`/`probe` 判定、可见窗口 `[0, committed_record_count)`；恢复目标写完后必须通过严格 reader 读取验证 |

ISSUE-011 复审移交事项（`docs/reports/ISSUE_011_REVIEW_REPORT.md` 第 8/10 节）：

- **P3-1**：`rcscan_reader._validate_present_dataset` 与 `rcscan_v2._validate_dataset_against_contract` 镜像重复——移交 ISSUE-012/014 或负责人决定抽公共函数。**ISSUE-012 决策：不抽公共函数、不改两个既有模块**（保持 ISSUE-008/011 冻结契约零改动，镜像仅存在于 reader 内部，恢复模块直接复用 reader 打开校验，不新增第三份镜像）。
- **P3-3**：缺存储 hash 行呈现口径（`hash_verified=False` + 空串）需对 ISSUE-012/014 消费方明示——本 Issue 计划中显式声明：恢复按物理行字节复制，**不重新解码/不重新计算 hash**，缺 hash 行原样保留，恢复后由严格 reader 重新报告。
- **`awaiting_rename` 衔接**：`rename_pending=True`（lifecycle=finalized/recovered 但文件名仍 `*.partial.rcscan`）的文件按已完成任务读取；**ISSUE-012 的恢复目标仅限 `lifecycle_state=writing` 的 partial**，finalized/recovered 状态不进入恢复流程（plan 直接 blocked），与 ISSUE-011 移交要求一致。

### 3.3 审查报告与授权证据

- `docs/reports/ISSUE_010_REVIEW_REPORT_R2.md`：round-2 PASS WITH CONDITIONS → 授权合并（M02 L81）；
- `docs/reports/ISSUE_011_REVIEW_REPORT.md`：PASS WITH CONDITIONS（无 P0/P1/P2、3 项 P3 移交）→ 授权合并（M02 L118）；
- M02 状态行实测：ISSUE-010 `Done`（L81）、ISSUE-011 `Done`（L118）、ISSUE-012 `Planned`（L155）。

## 4. 门禁基线（核查时实测复跑）

环境：WSL Ubuntu 24.04 / Python 3.12.3；numpy 2.5.2、h5py 3.16.0、pytest、ruff、mypy；`uav_gpr` editable 可导入（`src/`）。

```text
$ python3 tools/quality/verify.py
474 passed, 1 deselected in 13.11s
[quality] ruff      ok   All checks passed!（32 source files）
[quality] mypy      ok   Success: no issues found in 32 source files
[quality] import    ok   package import ok
[quality] all gates passed

$ python3 -m pytest tests/contract/test_storage_schema.py \
    tests/contract/test_raw_trace_hash.py tests/integration/test_incremental_writer.py \
    tests/contract/test_rcscan_reader.py -q
232 passed in 10.14s
```

核查后 `git status` 与核查前一致（仅 `## main...origin/main [ahead 9]`），无缓存/日志/实测数据残留（`git check-ignore` 确认 `.agent-teams/`、`*.rcscan`、`*.partial.rcscan` 已忽略）。

## 5. ISSUE-012 实施计划摘要（t2 执行契约，完整版见 plans 文档）

1. **新模块 `src/uav_gpr/storage/partial_recovery.py`**（只读检查 + 显式非破坏恢复 API，全部不修改源文件）：
   - `inspect_partial(path)` → 结构化 `InspectReport`：probe/mission/checkpoint、全部必需 trace-major 列与 `/frequency/raw` 长度、`physical_record_count`、半写尾部行数、ISSUE-011 校验分类（缺道/重复/冲突/逐行 issue）、源文件 SHA256、可选 processed 组存在性；确定性可序列化（`to_dict()`）。
   - `plan_recovery(path, *, new_file_id=None, target_dir=None, clock=None)` → `RecoveryPlan`：**默认 dry-run，绝不写任何文件**；决策新 file_id（缺省按 role 生成 `AirFileId.new()`/`GroundFileId.new()`）与目标路径 `<new_file_id>.rcscan`；`recoverable` + `blocked_reasons`（非 partial 文件名、lifecycle 非 writing、目标已存在、可选 processed 组存在均 blocked）与数据级 warning（缺道/重复/冲突/缺 hash 行——恢复原样保留并重新报告，不静默丢弃）。
   - `execute_recovery(plan, *, clock=None, fault_hook=None, filesystem=None)` → `RecoveryResult`：显式执行（调用本身即确认）；重校验源文件 SHA256 与目标不存在（fail-closed）；用 ISSUE-008 `create_rcscan_v2` 建新骨架（新 file_id、同 mission/config/axis/channels），按**物理行字节复制** `[0, committed_record_count)` 的必需列与 raw 到新文件；写 checkpoint（committed/last_trace_index/updated_utc）；置 `lifecycle_state=recovered`、`completion_kind=recovered`、`ended_utc=恢复时刻`；新增 mission attrs `recovery_source_sha256` / `recovery_source_file_id` / `recovery_tool_version`（provenance，附加属性对旧 reader 透明）；flush/close 后用严格 reader 复验；任一步失败 → 关闭句柄、**best-effort 删除目标**、抛错（绝不留下看似 finalized 的结果）；`RecoveryPhase` + `RecoveryFaultHook` 故障注入 seam（与 ISSUE-010 同模式）。
2. **失败测试优先**：`tests/integration/test_partial_recovery.py`，必测矩阵见 plans 文档 §5.4（对应提示词验收：报告确定性、源字节不变、恢复往返、目标冲突、恢复过程失败、dry-run 默认、ISSUE-010 各故障点夹具、ground 无 transport、乱序、重复/冲突保留、大文件分块复制）。
3. **文档**：`docs/DATA_FORMAT.md` 第 4 节后新增 4.1「非破坏恢复（ISSUE-012 冻结）」小节（inspect/plan/execute 契约、recovered 文件属性、provenance attrs、失败清理语义）；`docs/issues/M02_STORAGE.md` ISSUE-012 状态行 `Planned → In progress`（完成后由人工置 Review/Done）；`docs/plans/2026-08-30-issue-012-recovery.md` 记录实施与门禁证据。
4. **门禁复跑**：定向新测试 + 全量非硬件 pytest + Ruff + mypy strict + `verify.py` + 工作树/diff 检查；不 commit、不 push。

### 排除项（out of scope，与 M02 L170–171 一致）

不原地 truncate/修复/改写/删除源 partial；不自动删除任何文件；不做 GUI；不实现 v1 迁移（ISSUE-013）、inventory（ISSUE-014）、网络 ACK/outbox；不改 `rcscan_v2.py`/`raw_hash.py`/`incremental_writer.py`/`rcscan_reader.py` 的既有公共语义（不抽公共函数）；不 commit、不 push、不创建/切换分支；不进入 ISSUE-013。

### 验收标准（M02_STORAGE.md L175–178 原文，t2 不得削弱）

1. 任意写入故障夹具都能生成稳定报告；
2. 恢复文件可被严格 reader 读取；
3. 原 partial 字节不变；
4. 恢复失败不留下伪 finalized 文件；
5. 未经确认只 dry-run。

## 6. 结论

ISSUE-012 开工基线已锁定：`main` @ `db95817`（工作树干净，ahead 9 = 008/009/010/011 合并链与状态/文档提交）；两项依赖（ISSUE-010/011）的代码、契约测试、独立审查报告与授权合并证据全部实测复现；门禁基线 474 passed/1 deselected、ruff/mypy/import 全绿、定向依赖测试 232 passed。本确认单即为 t2 实现与 t3 独立复审的权威基线件；t2 按 plans 文档契约执行（先失败测试→最小实现→门禁→报告），完成后停止，不进入 ISSUE-013。

> 后续记录：本单为开工时点的基线快照，不随 t2 改动；t2 实际完成记录见其完成报告与 `docs/plans/2026-08-30-issue-012-recovery.md`，t3 复审报告独立输出。
