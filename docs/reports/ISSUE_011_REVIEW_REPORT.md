# ISSUE-011 独立审查报告（round 1）

- 审查日期：2026-08-30
- 审查性质：只读复审（未修改实现、测试、计划、M02 状态或 Git 状态；仅新增本报告文件）
- 审查标准：`docs/ISSUE_REVIEW_STANDARD.md` v1.0（输出格式按第 13 节 10 段）
- 审查对象：ISSUE-011「reader、严格校验与逻辑道排序」首次交付（任务 t2，执行者 engineer）
- 审查基线：`abfd312`（= `main` = `origin/main` 之后 ahead 6 的本地头；`feat/issue-011` 自该点创建，0 提交）
- 基线件：[docs/reports/ISSUE_011_BASELINE_CONFIRMATION.md](ISSUE_011_BASELINE_CONFIRMATION.md)（t1）；执行日志：[docs/plans/2026-08-30-issue-011-reader.md](../plans/2026-08-30-issue-011-reader.md)（t2）
- 审查者：独立 reviewer（非本轮执行者）

---

## 1. 审查结论

| 项 | 结论 |
|---|---|
| **整批结论** | **`PASS WITH CONDITIONS`** |
| **ISSUE-011 单 Issue 结论** | **`PASS WITH CONDITIONS`** |
| 是否存在 P0 | 否 |
| 是否存在 P1 | 否 |
| 是否存在 P2 | 否 |
| 关键验收是否失败 | 否（M02_STORAGE.md L137–141 三条验收标准全部 `PASS`，见第 4 节） |
| 必要测试是否失败 | 否（定向 39 passed；契约 134 passed；writer 59 passed；全量 474 passed/1 deselected；ruff/mypy strict 32 files/import 全绿；全部在 Windows venv 3.13.14 复现） |
| 测试是否非空泛 | 否——审查者 3 项独立变异（放宽 checkpoint 边界 / 破坏逻辑排序键 / 绕过 hash 重算）均被对应定向测试 1:1 杀死；另有 WSL 未变异对照 39/39（探针机制自证） |
| 可否拆分合并 | 本批只有 ISSUE-011 一个 Issue，无需拆分；不阻塞下游 ISSUE-012/013/014 开工 |

判定为 `PASS WITH CONDITIONS` 而非 `PASS` 的唯一原因：剩余 3 项明确、低风险且不阻止本次合并的 P3 收尾条件（见第 3/10 节）：本地镜像校验函数的维护性重复（执行者已如实声明）、`docs/DATA_FORMAT.md` reader 契约小节留待项目负责人决定、缺存储 hash 行在视图中的呈现口径建议随该小节入文。三者均不落入验收标准、不影响运行行为。

---

## 2. 自动识别的审查范围

### 2.1 从 t2 完成输出与仓库交叉识别

| 项 | 事实（以仓库证据为准） |
|---|---|
| Issue | ISSUE-011 reader、严格校验与逻辑道排序（`docs/issues/M02_STORAGE.md` L116–151） |
| 开发分支 | `feat/issue-011`（`git branch -vv` 实测无上游跟踪） |
| 目标分支 / 审查基线 | `main` @ `abfd312f6ab312180cabca8aaf6da671a463ccb5`（`chore: ignore .agent-teams runtime directory`） |
| 本批提交 | **0 个**；HEAD = 基线 = `abfd312`，全部改动为工作区未提交内容 |
| 声称改动文件 | 4 个，Git 事实与之相符（见 2.2） |
| 声称状态 | `docs/issues/M02_STORAGE.md` L118：`Review（实现+测试完成，等待独立复审；仅人工验收后置 Done…）` |

### 2.2 实际改动文件（`git status --porcelain=v1 -b` 实测）

| 文件 | 状态 | 规模（实测） |
|---|---|---|
| `src/uav_gpr/storage/rcscan_reader.py` | 未跟踪（本 Issue 生产模块） | 1070 行 |
| `tests/contract/test_rcscan_reader.py` | 未跟踪（本 Issue 契约测试） | 1325 行 / 39 个测试项（pytest 实测 39 passed） |
| `docs/issues/M02_STORAGE.md` | 已修改（`git diff` 实测恰 1 行：状态行 `Planned → Review（…）`） | 1 行 |
| `docs/plans/2026-08-30-issue-011-reader.md` | 未跟踪（t2 修复日志） | 113 行 |
| `docs/reports/ISSUE_011_BASELINE_CONFIRMATION.md` | 未跟踪（t1 交付，非 t2 改动） | 139 行 |

范围外核对（Git 实测）：`src/uav_gpr/core/**`、`src/uav_gpr/storage/rcscan_v2.py`、`src/uav_gpr/storage/incremental_writer.py`、`docs/DATA_FORMAT.md`、`docs/adr/**`、`tools/**`、`tests/unit|contract|integration` 既有文件 **零改动**（无 M/A/D 状态、无新增未跟踪件）；无 staged 条目；`git stash list` 为空。

---

## 3. 主要问题（按 P0 → P3 排序）

**P0 / P1 / P2：无。**

**P3 级剩余项（全部为执行者已如实声明或文档性收尾项，不阻止合并）：**

- **P3-1**　`rcscan_reader._validate_present_dataset`（`src/uav_gpr/storage/rcscan_reader.py:515-575`）是 `rcscan_v2._validate_dataset_against_contract`（`rcscan_v2.py:1302-1360`）的本地镜像。审查者逐行对拍确认两实现逻辑、错误码、context 键完全等价（仅错误文案不同），**当前无功能风险**；但若 ISSUE-008 契约校验演进，两处须同步。建议项目负责人在 ISSUE-012/014 中决定是否抽为公共函数（执行者已在计划文档第 7 节如实声明该维护性重复）。
- **P3-2**　`docs/DATA_FORMAT.md` 尚无 ISSUE-011 reader 契约小节（可见窗口、fail-closed 边界、双视图语义、报告分类、`rename_pending`）。执行者按 t1 契约将其列为范围外并移交负责人决定；本轮钉死的数据级语义（冲突排除 + 报告 + `ID_CONFLICT`）在入文前仅存在于计划文档与 docstring/测试，建议人工验收时一并补入。
- **P3-3**　缺存储 hash 的行仍进入双视图（`raw_trace_sha256=""`、`hash_verified=False`，见 `rcscan_reader.py:848-863`）。该口径已被 `test_missing_stored_hash_is_reported` 与实现 docstring 钉住，与计划决策 1（数据级问题进报告）一致；但消费方（ISSUE-014 inventory、ISSUE-012 恢复报告）需要明确「以 `hash_verified`/report.issues 为权威而非仅看字段值」。建议随 P3-2 的 DATA_FORMAT 小节明示。

---

## 4. 逐 Issue 验收矩阵（ISSUE-011）

### 4.1 M02_STORAGE.md L137–141 三条验收标准（原文）

| 验收标准 | 状态 | 代码证据（精确行号） | 审查者独立测试证据 |
|---|---|---|---|
| ① 尾部半写记录不可见 | **`PASS`** | 可见窗口：`_load_contract` 取所有必需列与 `/frequency/raw` 的最小长度（L509-513）；`_load_checkpoint` 拒绝 `committed > physical`（L587-595）；分类/视图只遍历 `< committed`（L697-698、L947-951、L960-974） | 定向 `test_half_written_tail_is_invisible_after_writer_fault`（3 故障相位，L733-775）与 `test_physical_rows_beyond_checkpoint_are_exposed_as_count_only`（L778-809）实测通过；**变异探针 A**：删除 L587-595 边界拒绝 → `test_corrupted_checkpoint_value_fail_closed[99]` **1 failed（DID NOT RAISE）**，证明该守卫被 1:1 钉住 |
| ② 乱序补传可正确排序 | **`PASS`** | 逻辑视图按 `sorted(self._by_index)` 遍历（L961）、重复塌缩到首个提交位（L964）、物理视图按提交顺序（L947-951） | 定向 `test_out_of_order_physical_rows_are_sorted_in_logical_view`（L555-583：物理 [5,1,3,0,4,2] / 逻辑 [0..5] / 位置 [3,1,5,2,4,0]）实测通过；**变异探针 B**：删除 `sorted()` → 该测试 **1 failed**，其余 4 项相关测试不受影响 |
| ③ 未知版本、损坏 checkpoint、重复索引不同 hash 被明确拒绝/报告 | **`PASS`** | 未知版本/未知 profile 由 ISSUE-008 probe fail-closed（`rcscan_v2.py:1434-1439`，reader L334 调用）；损坏 checkpoint：负值/越界/缺失/时间戳非法（L577-622）；同 index 不同 hash → 逻辑视图排除 + `ConflictTrace` 证据 + `trace_by_index` 抛 `ID_CONFLICT`（L724-738、L961-969、L990-1003）；同 uid 不同 index 亦为冲突（L750-773） | 定向：`test_unknown_schema_version_fail_closed`[3/1/2.5]、`test_unknown_profile_fail_closed`、`test_corrupted_checkpoint_value_fail_closed`[99/-1]、`test_missing_checkpoint_dataset_fail_closed`、`test_corrupted_checkpoint_timestamp_fail_closed`、`test_conflicting_hash_is_classified_and_fails_closed_in_logical_view`、`test_trace_uid_reuse_across_indices_is_conflict_and_excluded` 实测全部通过；**变异探针 C**：`verified = recomputed == stored` → `True` → `test_hash_mismatch_is_reported_and_row_flagged` **1 failed**，证明 hash 校验非空泛 |
| ④ 大合成文件可分块读取 | **`PASS`** | 分类扫描按 `_CLASSIFY_CHUNK=64` 行切片（L697-702）；`iter_physical/iter_logical(chunk_rows)` 每块 ≤ chunk_rows（L942-974）；`_read_records` 按连续区间成 run 切片（L898-938），无整文件读取 | 定向 `test_large_file_chunked_iteration_is_correct_and_bounded`（L1239-1282，10 000 道、块 ≤64、总数一致、逐块断言）与 `test_chunk_boundaries_are_continuous`（L1285-1295）实测通过；**审查者独立 2 万道探针**（临时目录独立构建器 + 真实 RcScanReader）：chunked 模式 20 000/20 000 道、313 块/视图、ru_maxrss ≈ 108 MB；对照 whole 模式（chunk_rows=20000）ru_maxrss ≈ 265 MB——Δ≈160 MB 恰为整载代价，证明分块迭代未整载文件 |

### 4.2 ISSUE-011 提示词补充要求（M02_STORAGE.md L143-151）逐项核对

| 要求 | 状态 | 代码/测试证据（精确行号） |
|---|---|---|
| 严格验证 schema/profile/role/lifecycle/dtype/长度/checkpoint，只暴露 committed 以内且必需列完整的记录 | **`PASS`** | probe（L334）+ mission/contract/checkpoint 三阶段加载（L368-628）；`_validate_present_dataset` 对每个已存在数据集核 dtype/maxshape/chunks/compression/固定轴长（L515-575）；必需列缺失拒绝（L499-504）；`/transport` 仅 ground 可缺（L495-498）；lifecycle ∈ {writing, finalized, recovered} 由 probe 校验；行解码失败的行不进入视图（L826-846） |
| 物理提交顺序 + 按显式 trace_index/trace_uid 排序的逻辑迭代/分块 | **`PASS`** | `iter_physical`（L942-951）/`iter_logical`（L953-974）/`trace_by_index`（L976-1019）；测试 L487-583、L1239-1295 |
| 报告缺道、重复和冲突 | **`PASS`** | 缺道= [0, max 已解码 index] 空洞（L776-781）；重复= 同 index+uid+hash 多副本（L739-747）；冲突= 同 index 异 hash / 同 uid 异 index（L724-738、L750-773）；`ValidationReport` 可序列化（L180-255）；测试 L586-609、L972-1074 |
| 可选 processed 组缺失合法、存在按契约校验 | **`PASS`** | 可选组缺失不报（L493-494 `contract.optional: continue`）；存在则与其他数据集同样校验（L490-492）；测试 L901-964 |
| 未知版本 fail-closed | **`PASS`** | 测试 L817-834（版本 3/1/2.5、未知 profile → `UNSUPPORTED_SCHEMA_VERSION`） |
| 缺 GNSS 哨兵语义、不伪造位置 | **`PASS`** | 行解码只走 ISSUE-008 权威 codec（L828-832）；`test_missing_gnss_rows_decode_without_fabrication`（L612-642：gnss_match=None + DEGRADED 原样保留） |
| 行解码单一权威 codec + hash 重算 `int()` 转换（ISSUE-009 R3 风险 5） | **`PASS`** | `trace_metadata_from_cells`（L828）；`trace_index=int(metadata.trace_index)`（L869）；`_cell_int` 已返回 plain int（`rcscan_v2.py:600-603`） |
| 不修复/改写文件、不迁移、不处理 | **`PASS`** | 模块 docstring L49-50；`h5py.File(..., "r")`（L336）；`test_reader_is_strictly_read_only`（L1217-1231：读取前后文件 SHA256 不变）实测通过 |
| `awaiting_rename` 呈现（ISSUE-010 R2 P3-7 衔接） | **`PASS`** | `rename_pending = lifecycle ∈ {finalized, recovered} 且名尾 .partial.rcscan`（L624-628）；`test_awaiting_rename_partial_is_presented_as_finalized`（L1172-1214：lifecycle=finalized、completion_kind=completed、rename_pending=True、committed 3 道全读）实测通过；与 ISSUE-012 无冲突——完整识别/处置仍留 ISSUE-012，`rename_pending` 为其扩展点 |

---

## 5. Git 与交付检查

- **当前分支**：`feat/issue-011` @ HEAD `abfd312`（= `main` = 基线，无领先落后之外的远端提交）；`git reflog` 实测仅 `checkout: moving from main to feat/issue-011`，无 reset/rebase/amend/merge 迹象。
- **本批提交**：**0 个**。无 commit/push/merge/stash（`git stash list` 空；无远程分支跟踪）。
- **未提交修改**：仅 `docs/issues/M02_STORAGE.md` 1 行（`git diff` 实测，内容 = 状态行 `Planned → Review（…）`，见本报告 2.2）。
- **未跟踪文件**：4 个范围内文件 + `docs/reports/ISSUE_011_BASELINE_CONFIRMATION.md`（t1 交付）。**无任何内容进入暂存区**；`.agent-teams/**` 不在跟踪清单（`git ls-files .agent-teams` = 0）且已被 `.gitignore:58` 忽略（`git check-ignore -v` 实测命中）。
- **状态行一致性**：M02 L118 `Review（实现+测试完成，等待独立复审；仅人工验收后置 Done…）` 与 `docs/issues/README.md` 第 2 节 `Review：实现和测试完成，等待人工审查` 定义一致；`Planned → In progress → Review` 推进记录于计划文档第 6 节与 t2 输出，合理；置 `Done` 由项目负责人执行，审查者不操作。
- **空白/卫生**：`git diff --check` 退出 0。审查全程定向测试使用 `-p no:cacheprovider`；verify.py 仅更新已忽略缓存目录（`__pycache__/`、`.pytest_cache/`、`.ruff_cache/`、`.mypy_cache/`，均被 `.gitignore:2,6,7,8` 覆盖）。审查后 `git status --porcelain` 与审查前完全一致（除本报告文件外）。
- **审查者自身产出**：仅新增本报告文件。全部变异探针树与 2 万道探针脚本位于系统临时目录 `D:\dsh\windows\temp\uav-gpr-review-011-probes\`，审查结束前已整体删除，项目内零残留。

---

## 6. 测试与验证结果

### 6.1 环境

| 项 | 实测值 |
|---|---|
| 门禁复跑解释器 | `D:\博士任务\无人机软件\UAV-GPR\.venv\Scripts\python.exe`，**Python 3.13.14**（与 t2 声明一致；numpy 2.5.2 / h5py 3.16.0 / pytest 8.4.2 venv 内实测） |
| 探针/大文件解释器 | WSL `python3` **3.12.3**（numpy 2.5.2 / h5py 3.16.0；探针树位于 Windows 系统临时目录、经 PYTHONPATH 覆盖导入，未触碰项目文件） |
| 说明 | t2 门禁数字与 ISSUE-010 R2 口径一致（Windows venv）；审查者在两环境均复现，结论不依赖环境 |

### 6.2 复跑的命令与结果（真实命令 / 退出码 / 通过数）

| # | 命令（工作目录 `D:\博士任务\无人机软件\UAV-GPR`） | 退出码 | 结果 |
|---|---|---:|---|
| 1 | `.venv/Scripts/python.exe -m pytest tests/contract/test_rcscan_reader.py -q -p no:cacheprovider` | 0 | **39 passed in 5.82s**（t2 声明 39 ✓） |
| 2 | `.venv/Scripts/python.exe -m pytest tests/contract/test_storage_schema.py tests/contract/test_raw_trace_hash.py -q -p no:cacheprovider` | 0 | **134 passed in 0.39s**（t2 声明 134 ✓） |
| 3 | `.venv/Scripts/python.exe -m pytest tests/integration/test_incremental_writer.py -q -p no:cacheprovider` | 0 | **59 passed in 2.30s**（t2 声明 59 ✓） |
| 4 | `.venv/Scripts/python.exe tools/quality/verify.py` | 0 | pytest 非硬件 ok；ruff ok；mypy ok；package import ok；**`[quality] all gates passed`** |
| 5 | `.venv/Scripts/python.exe -m pytest -m "not hardware and not slow" -q -p no:cacheprovider` | 0 | **474 passed, 1 deselected in 12.30s**（= 435 基线 + 39 新增；deselect 为既有 hardware 双重 opt-in 哨兵，与本 Issue 无关） |
| 6 | `.venv/Scripts/python.exe -m ruff check src tests` | 0 | `All checks passed!` |
| 7 | `.venv/Scripts/python.exe -m mypy src` | 0 | `Success: no issues found in 32 source files`（strict，新增 rcscan_reader.py；t2 声明 32 ✓） |
| 8 | `git diff --check` | 0 | 无空白错误 |
| 9 | **变异探针 A**（临时树删除 `_load_checkpoint` 的 committed>physical 拒绝） | 1（预期） | `test_corrupted_checkpoint_value_fail_closed[99]` **1 failed（DID NOT RAISE）**；同组 [-1]、3 个半写尾部、raw 尾行计数测试 5 passed（证明锁的是越界而非负值/半写逻辑） |
| 10 | **变异探针 B**（临时树 `sorted(self._by_index)` → `self._by_index`） | 1（预期） | `test_out_of_order_physical_rows_are_sorted_in_logical_view` **1 failed**；缺道/重复/uid 冲突/万道分块 4 passed |
| 11 | **变异探针 C**（临时树 `verified = recomputed == stored` → `True`） | 1（预期） | `test_hash_mismatch_is_reported_and_row_flagged` **1 failed**；缺 hash/严格只读 2 passed |
| 12 | 探针机制 M0 对照（未变异副本，WSL python3 + PYTHONPATH 指向临时树） | 0 | **39 passed**（探针机制本身不污染结果；venv 侧同命令 39 passed 为仓库代码二次对照） |
| 13 | **独立 2 万道有界性探针**（临时目录独立构建器 + 真实 RcScanReader） | 0 | chunked(chunk_rows=64)：物理/逻辑各 313 块、20 000/20 000 道、报告 0 missing/0 duplicate/0 conflict/0 issue、**ru_maxrss 108 468 KiB**；whole(chunk_rows=20000) 对照：**ru_maxrss 271 276 KiB**（Δ≈160 MB = 整载代价）——分块迭代未整载文件 |

无失败、无 error、无 xfail、无 skip；新增测试无 `sleep`（仅 docstring 声明"no sleep"）。

### 6.3 测试质量核对

- 39 个测试项覆盖：正常往返、乱序、缺道、重复同 hash、冲突异 hash、uid 复用、hash 不一致、缺存储 hash、行解码失败、非法 index 单元格、checkpoint 不一致、半写尾部（3 故障相位）、raw 尾行计数、未知版本/profile、损坏/缺失 checkpoint、时间戳损坏、列缩短、dtype 错、可选组缺失/存在/坏 dtype、空骨架、aborted partial、ground 无 transport、awaiting_rename、严格只读、万道分块、块边界连续、validator 表面。
- 断言形态：`pytest.raises` 15 处，其中 12 处 DomainError 均带 `as caught` + `code is ErrorCode.X` 精确断言；其余 3 处为 writer 故障注入（`InjectedStorageFault`）与 flaky rename（`OSError`），属夹具注入而非弱化。
- 弱化扫描：`xfail`/`skip`/`TODO`/`FIXME`/`sleep` 在两个新文件中命中 0（`except BaseException` 仅 1 处，为打开失败时关闭句柄的守卫，与 ISSUE-010 既有模式一致）；未删改任何既有测试（Git 实测）。
- 探针均独立编写、在系统临时目录执行、对项目文件零写入、事后整体删除。

---

## 7. 报告与事实差异

| # | t2/任务材料声明 | 审查核实 | 性质 |
|---|---|---|---|
| 1 | "39 passed / 134 passed / 59 passed / 474 passed, 1 deselected / ruff / mypy 32 files / import / diff --check 全绿" | 在 Windows venv 3.13.14 逐条复现，数字一致（第 6.2 节） | 一致 |
| 2 | "红灯→绿灯：1st 12 passed/27 failed → 2nd 33/6 → 3rd 38/1 → 4th 39 passed" | 修复后代码已就位，红灯无法在不改代码的前提下复现；按审查标准第 10 节记为「未发现反证」；审查者以 3 项独立变异 + M0 对照等价且更强地验证了测试非空泛 | 不可独立复现（无损害） |
| 3 | "`_validate_present_dataset` 为 rcscan_v2 私有校验的本地镜像（P3 级）" | 逐行对拍：逻辑/错误码/context 键完全等价，仅文案不同；评级 P3-1 确认 | 一致 |
| 4 | "changedPaths 与 inScope 注解条目无法精确匹配（计划声明缺陷）" | 属实：t2 任务 inScope 测试文件条目带「（或按分层合理命名）」注解；该缺陷属任务计划元数据，不影响仓库内容，建议后续任务声明精确路径 | 计划元数据缺陷（非仓库差异） |
| 5 | 计划文档 §6 工作树事实（1 M + 4 ??） | 实测一致（+ t1 基线确认单 1 个未跟踪件，计划 §2 已注明） | 一致 |
| 6 | "测试无 xfail/skip/TODO/FIXME/sleep" | grep 实测命中 0 | 一致 |
| 7 | M02 状态 `Planned → In progress → Review` | 行内容与 README §2 定义一致；中间态在计划文档与 t2 输出有记录 | 一致 |
| 8 | 报告与仓库冲突 | **无** | — |

---

## 8. 剩余风险

1. **冲突/重复分类语义为本轮钉死的新契约**（逻辑视图排除冲突 + 报告证据 + `trace_by_index` 抛 `ID_CONFLICT`；重复塌缩到首个提交位）。ISSUE-014（空地 inventory）与 ISSUE-012（恢复报告）必须按此口径消费；该语义已写入模块 docstring（L28-41）、计划文档决策 1-4 与测试，但尚未进入 `docs/DATA_FORMAT.md`（见 P3-2）。
2. **`awaiting_rename` 呈现与 ISSUE-012 的衔接**：`rename_pending=True` 时文件按已完成任务读取（lifecycle=finalized/recovered、completion_kind 原样）。与 ISSUE-010 R2 P3-7 的移交要求一致，完整恢复处置仍属 ISSUE-012，无冲突；ISSUE-012 实现时应沿用本 reader 的 `rename_pending`/`probe` 判定而非另起识别。
3. **大文件 IO 特征**：10 000 道（套件内）与 20 000 道（审查者探针）分块迭代均通过且内存有界（ΔRSS 证据见 6.2）；真实万道级 HDF5 的吞吐/缓存行为由 M12（ISSUE-057/060）基准确认（沿用 t2 风险）。
4. **全部交付未提交**（本批 0 commit、0 push）：合并/提交动作须由项目负责人明确授权执行，执行者与审查者均不得自行 commit/push。
5. **flush 持久化在本环境不可观测**（ISSUE-010 遗留，与本 Issue 的「可见窗口」直接相关）：reader 的 committed 语义由 checkpoint 值决定，checkpoint 的物理持久化保证由 ISSUE-010 的注入 handle 测试与 M12 掉电演练承担，不在本 Issue 范围。
6. **镜像校验维护性重复**（P3-1）：当前逐行等价、无功能差异；若 ISSUE-008 契约演进未同步镜像，reader 会与权威校验漂移——建议在 ISSUE-012/014 抽公共函数。

---

## 9. 合并建议

- **整批结论 `PASS WITH CONDITIONS`；ISSUE-011 交付真实、完整地满足 M02 L137–141 全部验收与提示词补充要求，无 P0/P1/P2、无关键验收失败、无必要测试失败，可进入项目负责人人工验收。**
- 验收通过后建议：由负责人将 `docs/issues/M02_STORAGE.md` ISSUE-011 状态置 `Done`，并按项目惯例决定提交/合并（审查者不执行）；合并时一并决定 P3-2（DATA_FORMAT reader 契约小节是否入文）。
- 不阻塞下游开工：ISSUE-012 依赖的 committed 语义/`rename_pending`、ISSUE-013 依赖的严格 reader、ISSUE-014 依赖的缺道/重复/冲突分类与逐道 hash 校验均已落盘且被测试钉住。
- 不建议拆分合并；本批仅一个 Issue。

---

## 10. 最小修复清单

**本轮阻断项：无。** 剩余事项如下（均为低风险收尾/移交/负责人决策，不阻断本次合并）：

| 序号 | 等级 | 事项 | 位置 | 处理 |
|---|---|---|---|---|
| 1 | P3 | `_validate_present_dataset` 与 `rcscan_v2._validate_dataset_against_contract` 镜像重复 | `rcscan_reader.py:515-575` | 移交 ISSUE-012/014 或负责人决定抽公共函数（当前逐行等价，无功能风险） |
| 2 | P3 | `docs/DATA_FORMAT.md` reader 契约小节缺失 | `docs/DATA_FORMAT.md` 第 3 节之后 | 人工验收时由负责人决定：补入可见窗口/fail-closed 边界/双视图/报告分类/`rename_pending` 契约条文（并入本轮钉死语义） |
| 3 | P3 | 缺存储 hash 行在视图中的呈现口径（`hash_verified=False` + 空串）需对 ISSUE-014/012 消费方明示 | `rcscan_reader.py:848-863`；随上条入文 | 文档化即可，不改实现 |
| 4 | 记录 | 计划声明缺陷：t2 任务 inScope 测试文件条目带注解导致 changedPaths 无法精确匹配 | `.agent-teams` 任务 t2 元数据 | 后续任务计划声明精确路径（队长已知会，无需修复仓库） |

清理项（非修复）：本审查产生的探针树、变异脚本与 2 万道探针文件位于系统临时目录 `D:\dsh\windows\temp\uav-gpr-review-011-probes\`，项目内零残留，已随审查结束整体删除。

---

*审查结束。审查者未修改任何实现、测试、计划、M02 状态或 Git 状态，仅新增本报告文件，等待项目负责人决定修复、拆分或合并。*
