# ISSUE-010 收尾基线确认单

日期：2026-08-28（收尾修复轮开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-010-close`（执行器 engineer）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支、未修改实现与测试。
配套文件：独立审查报告 [ISSUE_010_REVIEW_REPORT.md](ISSUE_010_REVIEW_REPORT.md)（`PASS WITH CONDITIONS`，最小修复清单见其第 10 节）；本单为 t2 修复与 t3 复审的权威基线件。

## 1. 锁定的目标 Issue 与依据

**ISSUE-010：增量 writer、checkpoint 与原子 finalize（收尾最小修复轮）**

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-010（M02_STORAGE.md 第三个条目） | docs/issues/README.md 依赖顺序主表 |
| 审查结论 | **PASS WITH CONDITIONS**：4 项 P2（P2-1/P2-2/P2-3/P2-4），无 P0/P1；三条验收标准独立复验全 PASS | docs/reports/ISSUE_010_REVIEW_REPORT.md 第 1/9 节 |
| 本轮性质 | 只按审查报告第 10 节最小修复清单闭合 P2-2/P2-3 并修正 P2-1/P2-4 测试注释与报告口径，再交 round-2 独立复审（t3） | docs/ISSUE_REVIEW_STANDARD.md 第 14 节 |
| 直接依赖 | ISSUE-008（schema/codec，已合入 main）、ISSUE-009（raw hash，HEAD 提交） | M02_STORAGE.md「直接依赖」字段；第 3 节证据 |
| 一次一 Issue | 本轮只处理 ISSUE-010；不进入 ISSUE-011 | docs/issues/README.md 第 1/3 节 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

两个参考项目 `E:\钢筋仪软件开发`、`E:\UVA_GPR_system` 不在本机挂载范围内；ISSUE-010 无参考迁移需求，不触碰。

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      feat/issue-010（当前，无上游跟踪）
HEAD        ee41360  feat(core): add canonical raw trace hash and golden vectors (ISSUE-009)
main        e852508  Merge feat/issue-008（与审查报告声明一致）
本批提交    0 个；HEAD == 审查基线 ee41360，改动全部为工作区未提交内容
```

`git status --porcelain=v1 -b`（5 行）：

| 状态 | 文件 | 归属 |
|---|---|---|
| M | `docs/issues/M02_STORAGE.md` | 1 行状态变更：ISSUE-010 `- 状态：Planned` → `+ 状态：In progress`（属允许范围） |
| ?? | `.agent-teams/` | 团队运行时目录（含历史会话遗留），非项目内容；未加入 `.gitignore`（见 P3-6） |
| ?? | `docs/reports/ISSUE_010_REVIEW_REPORT.md` | 审查报告（审查者产出） |
| ?? | `src/uav_gpr/storage/incremental_writer.py` | 本 Issue 生产模块，现 1043 行 |
| ?? | `tests/integration/test_incremental_writer.py` | 本 Issue 集成测试，现 1897 行 / 48 个测试函数 → 56 个用例 |

**重要差异声明**：审查报告记录实现 1005 行、测试 1616 行/52 用例；当前实测 1043 行、1897 行/56 用例（+4 用例）。即审查快照之后工作树已被改动过（新增 flush 计数、真实 flush 失败、rename 重试守卫等测试与 `hdf5_opener` 生产缝、`_by_position` 反向索引等实现），第 4 节逐项映射以**当前代码事实**为准，报告中的旧行号仅供参考。

### 3.2 依赖 Issue 逐项核对（实际代码与测试证据）

基线 HEAD `ee41360` 未变，依赖文件全部 tracked 且工作树无修改：

| 依赖 | 交付物 | 本 Issue 复用点 |
|---|---|---|
| ISSUE-008 `.rcscan` v2 schema/codec | `src/uav_gpr/storage/rcscan_v2.py`（main `e852508` 合入） | writer 仅调用 `schema.create_rcscan_v2`（L497）、`dataset_contracts`（L523）、`trace_metadata_to_cells`（L756）；未自建 dtype/chunk/列编码 |
| ISSUE-009 规范 raw hash | `src/uav_gpr/core/raw_hash.py`（HEAD `ee41360`） | `compute_raw_trace_sha256`（L693）；测试用独立重算 `expected_hash()` 对拍 |

门禁基线：`tests/contract/test_storage_schema.py`、`tests/contract/test_raw_trace_hash.py` 等全部含在 432 passed 中（第 5 节），依赖契约未回归。

## 4. 审查报告最小修复清单（第 10 节）与当前代码实际状态逐项映射

审查报告第 10 节共 8 条。逐项对照**当前实际代码**（非报告快照、非计划声称），并给出实测证据：

| # | 修复项 | 当前代码实际状态 | 实测证据 |
|---|---|---|---|
| 1 | **P2-2** rename 前第二处"目标已存在"守卫零测试覆盖 | **已闭合** | 新增 `test_rename_retry_refuses_when_the_target_appeared_in_the_meantime`（测试 L1630–1671）：`_FlakyRenameFacade(fail_times=1)` → `close()` 抛 OSError → 状态 `AWAITING_RENAME` → 外部写入 `final_path` 哨兵字节 → 再次 `close()` 抛 `DomainError(INVALID_ARGUMENT)`（`context["path"]` 正确）→ 哨兵字节未变、`filesystem.attempts == 1`（rename 未被再次尝试）、partial 保留且 `lifecycle_state=finalized`、committed=3。该路径唯一可达第二处守卫（实现 L946–951；`_finalize_file` 守卫 L973–978 在 `AWAITING_RENAME` 时不再进入，测试注释 L1637–1641 已说明） |
| 2 | **P2-3** 预置哈希不一致的冲突绕过 `_record_conflict`、无证据 | **实现已修，测试缺失** | 实现：`_append_trace` L702–715 在 `metadata.with_integrity(digest)`（L718）之前先判 `preset_hash is not None and preset_hash != digest` → 统一走 `_record_conflict(...)`；`_record_conflict`（L872–905）统一 context 键集：`trace_index`/`record_position`/`stored_hash`/`incoming_hash`/`stored_trace_uid`/`incoming_trace_uid`/`duplicate_trace_uid`/`conflicting_trace_index`。审查者探针（只读，系统临时目录）：预置错误哈希提交 → `ErrorCode.ID_CONFLICT`、`writer.conflicts` 长度 1（`TraceConflict(trace_index=1, record_position=-1, stored_hash='aaa…', incoming_hash=<重算 digest>)`）、committed=1/physical=1（fail-closed 正确）。**但测试套件中没有任何用例在 metadata 上预置 `raw_trace_sha256`（grep 全文件仅 L266/L994 两处赋值且均为 `None`）——L702–715 这条新代码路径零测试覆盖**，t2 必须补失败测试 |
| 3 | **P2-1** 两次 flush 的移除无法被任何测试杀死；注释/报告变异结论锁定的是"相位播报" | **测试侧已闭合，报告口径待 t2 报告落实** | 新增 `test_each_commit_performs_two_real_hdf5_flushes_around_the_checkpoint`（测试 L1191–1247）：经 `create(hdf5_opener=…)` 注入 handle 包装器（`_FlushSpy`），断言 2 次提交 + finalize 共 6 次真实 `flush()` 调用、每次提交恰好在 checkpoint 前后各一次（`flush#2` 在 `AFTER_DATA_FLUSH` 之前、`flush#3` 在 `AFTER_COMMIT_FLUSH` 之前，事件序列逐项对拍）；`test_data_flush_failure_never_advances_the_checkpoint`（L1250–1284）与 `test_commit_flush_failure_never_exposes_an_incomplete_committed_row`（L1287–1325）用 `_ArmFlushFailure` 让 `h5.flush()` 真实抛 `OSError`，覆盖"磁盘/flush 失败不推进 checkpoint"（审查者探针 D 的套件化）。相位序列测试注释（L1851–1856）已改为事实口径："…`AFTER_DATA_FLUSH` is announced by the writer's flush step itself, so dropping the flush before the checkpoint removes the phase and breaks this sequence"；实现 `_flush` 注释（L789–797）同样为诚实表述。仓库内不存在 ISSUE-010 完成报告文件（旧会话完成报告未入库），"报告口径"由 t2 完成报告（任务输出）落实，须明确"锁定提交顺序与相位序列 + 经注入 handle 观测的 flush 调用次数/顺序"，不再声称相位测试证明 flush 持久化 |
| 4 | **P2-4** 6 个子进程 `os._exit()` 硬崩溃用例的建模前提不成立、注释论断与事实不符 | **未修复（注释仍错误）** | 测试 L1328–1340 注释仍声称 "unwinding the stack closes (and therefore flushes) the HDF5 handle, which hides any unflushed state" 与 "no unwinding, no ``atexit``, no GC, no **flush**. What remains on disk is **exactly what a power loss would leave**"。事实（审查报告 P2-4 实测 + 本基线复核实现 L670–676/L1023–1033）：`append_trace` 的 `except BaseException` → `_force_close_handle` 在异常传出**之前**已 `flush()+close()`，子进程 `os._exit()` 不改变任何落盘状态，与进程内故障模型一致。t2 必须把该注释改为事实描述（见第 7 节范围 2） |
| 5 | **P3-1** `close()` 注释声称 TOCTOU-safe | **已闭合** | 实现 L941–945 注释已改为 "best-effort check-then-use guard, not a TOCTOU-proof one: it is sound because the writer is the single owner…" |
| 6 | **P3-2** `trace_index_at_record` O(n) 反查 | **已闭合** | 实现新增 `_by_position: dict[int, int]`（L405，写入时 L779 同步，查询 L869–870 `_index_of_position` O(1)） |
| 7 | **P3-3** `create()` 不预检终态文件 | **已闭合（文档化选项）** | `create()` docstring L445–446 明确 "a pre-existing final ``<mission_id>.rcscan`` is *not* rejected here; it is detected fail-closed at finalize time" |
| 8 | **P3-5** 测试改写私有属性完成 rename 重试 | **已闭合** | `test_rename_failure_preserves_partial_and_can_be_retried`（L1596–1627）改用 `_FlakyRenameFacade(fail_times=1)`（失败 N 次后成功），不再改写 `writer._filesystem` |
| 9 | **P3-6** `.agent-teams/` 未跟踪未忽略 | **保持开放（负责人决策项）** | `git status` 仍显示 `?? .agent-teams/`；`.gitignore` 无 `.agent-teams/` 条目，`git check-ignore` 确认未忽略。按审查报告，是否加入 `.gitignore` 由项目负责人决定；本修复轮不纳入提交 |

## 5. 门禁基线与环境差异（核查时实测复跑）

```text
$ python3 -m pytest tests/integration/test_incremental_writer.py -q -p no:cacheprovider
56 passed in 3.77s        （审查时 52；+4 = flush 计数、2×真实 flush 失败、rename 重试守卫）
$ python3 tools/quality/verify.py
[quality] pytest (non-hardware) ok    432 passed, 1 deselected（审查时 428；deselect 为 hardware 双重 opt-in 哨兵）
[quality] ruff                   ok
[quality] mypy                   ok（strict, 31 files）
[quality] package import         ok
[quality] all gates passed
```

环境说明（与 ISSUE-009 round-2 基线单一致）：WSL Ubuntu 24.04 / Python 3.12.3；Windows `.venv` 形态不可用；测试栈（numpy 2.5.2、h5py 3.16.0、pytest 8.4.2、ruff、mypy）装于用户站点，`uav_gpr` editable 可导入。审查报告环境为 Windows Python 3.13.14/.venv——环境口径差异，不影响结论。测试文件无 `sleep`/`xfail`/`skip`/`TODO`/`FIXME`（grep 实测），`pytestmark = pytest.mark.integration`（L89），`--strict-markers` 下收集正常。核查后 `git status` 与核查前一致，无缓存/日志/实测数据残留。

## 6. 范围 / 排除项 / 验收标准（t2 修复轮）

**范围（in scope，最小修复，对应审查报告第 10 节 1–4 项，P3 不修）**：

1. **P2-3 补测试**：先写能失败的测试——metadata 预置与重算 digest 矛盾的 `raw_trace_sha256` 提交（新鲜 index 与已提交 index 两种形态），断言抛 `DomainError(ID_CONFLICT)`、`writer.conflicts` 留下 `TraceConflict` 证据（`trace_index`/`record_position`/`stored_hash`/`incoming_hash`/双方 `trace_uid` 键集一致）、committed/physical 不推进、writer 保持可用；再确认实现 L702–715 已满足（当前实现应直接通过，测试补上即闭合）。
2. **P2-4 修正注释**：把测试 L1328–1340 的崩溃模型注释改为事实描述——writer 在异常传出前已应急 `flush()+close()`（`_force_close_handle`），子进程 `os._exit()` 与进程内故障模型的落盘状态一致；本组用例的价值是跨进程验证不变式（checkpoint 不越界、半道不可见），而非模拟未 flush 的掉电；不做代码行为改动、不删测试。
3. **P2-1/P2-4 报告口径**：t2 完成报告明确——变异结论表述为"锁定提交顺序与相位序列 + 注入 handle 观测的 flush 次数/顺序"；子进程用例表述为"跨进程不变式验证"；不再声称相位测试证明 flush 持久化或模拟掉电。
4. **复跑**：定向 56+ 用例、全量非硬件 pytest、Ruff、mypy strict、package import、`verify.py`；核查工作树与 diff。

**排除项（out of scope）**：P3 各项（1/2/5/7/9 已闭合或文档化，6 由负责人决定，8 不修）；`src/` 除 `incremental_writer.py` 必要改动外的任何改动；`docs/issues/M02_STORAGE.md` 状态行（保持 `In progress`，复审通过后由负责人置 `Review`/`Done`）；`.agent-teams/`；Git 分支切换/提交/推送；ISSUE-011/012 及后续；网络 ACK/outbox/UI/恢复工具；两个参考项目。

**验收标准（M02_STORAGE.md L100–104 原文，修复不得削弱）**：

1. 每个故障点后 reader 最多看到最后完整 checkpoint，不看到半道；
2. 不兼容 sweep、重复冲突、磁盘/flush 失败不推进 checkpoint；
3. finalized 文件不可继续 append，原 partial 不被无意覆盖。

外加审查报告第 10 节 1–4 项的逐条关闭证据（第 4 节映射表为基准）。

## 7. 冲突与风险

- **无设计冲突**：P2-3 实现已按审查报告最小修复方向落地（统一走 `_record_conflict` + 统一 context 键集），P2-4 只改注释不改行为，不触碰对外契约（`committed_record_count` 语义、partial 保留语义、4 种 `completion_kind` 均不变），ISSUE-011/012 可继续依赖。
- **风险 1**：P2-3 代码路径（L702–715）当前**零测试覆盖**——这是 t2 的首要修复项；修复轮与复审都必须以代码实测为准，不得以"实现已改"代替"测试已补"。
- **风险 2**：审查快照（1005/1616 行）与当前工作树（1043/1897 行）不一致，历史会话完成报告的变异数字（删 flush → 5/3 例失败）基于旧代码；t2 报告不得沿用旧数字，须引用当前 56 用例与实测。
- **风险 3**：本环境 HDF5 写入即持久化，flush 仍无法通过行为测试观测——P2-1 的注入 handle 计数测试是当前最强可重复证据，t2 报告须如实表述。
- **未完成事项**：本确认单范围内无；t2 由调度器按契约派发，完成后交 round-2 独立复审（t3），复审通过后停止交人工验收，不进入 ISSUE-011。

## 8. 结论

ISSUE-010 收尾修复轮开工基线已锁定：分支 `feat/issue-010` @ `ee41360`（main `e852508`），本批 0 提交、改动全部未提交；审查报告最小修复清单当前状态逐项可复现——P2-2 已闭合（测试已补且通过）、P2-3 实现已修但**测试缺失**、P2-1 测试侧已闭合（flush 计数 + 真实失败 + 注释修正）报告口径待 t2 报告落实、P2-4 注释**未修正**；P3-1/2/3/5 已在工作树中闭合（属既有改动，非本轮范围），P3-6 保持开放待负责人决定。门禁基线：定向 56 passed、全量 432 passed/1 deselected、ruff/mypy/import 全绿。本确认单即为 t2 修复与 t3 复审的权威基线件。

> 后续记录：本单为开工时点的基线快照，不随修复改动；round-2 修复的实际完成记录见 t2 完成报告（agent_teams 任务输出）与 t3 复审报告。
