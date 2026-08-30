# ISSUE-010 独立审查报告（round 2）

- 审查日期：2026-08-28
- 审查性质：只读复审（未修改实现、测试、计划、Git 状态；仅新增本报告文件）
- 审查标准：`docs/ISSUE_REVIEW_STANDARD.md` v1.0（输出格式按第 13 节 10 段）
- 审查对象：ISSUE-010 round-2 最小修复（t2 交付）——闭合 round-1 报告的 P2-2/P2-3 并修正 P2-1/P2-4 注释与报告口径
- 审查基线：`ee41360 feat(core): add canonical raw trace hash and golden vectors (ISSUE-009)`（round-1 审查基线，未变）
- 修复基线件：`docs/reports/ISSUE_010_BASELINE_CONFIRMATION.md`（t1，开工时点快照）
- round-1 依据：`docs/reports/ISSUE_010_REVIEW_REPORT.md`（`PASS WITH CONDITIONS`，P2-1～P2-4 定义见其第 3 节）

---

## 1. 审查结论

| 项 | 结论 |
|---|---|
| **整批结论** | **`PASS WITH CONDITIONS`** |
| **ISSUE-010 round-2 修复结论** | **`PASS WITH CONDITIONS`** |
| 是否存在 P0 | 否 |
| 是否存在 P1 | 否 |
| 是否存在 P2（未闭合） | 否——P2-1/P2-2/P2-3/P2-4 四项全部闭合且经审查者独立变异探针复验 |
| 关键验收是否失败 | 否（M02_STORAGE.md L100–104 三条验收标准全部 `PASS`） |
| 必要测试是否失败 | 否（定向 59 passed；契约 134 passed；全量 435 passed/1 deselected；ruff/mypy/import 全绿，全部在任务契约指定的 Windows 解释器上复跑） |
| 可否拆分合并 | 本批只有 ISSUE-010 一个 Issue，无需拆分；不阻塞下游 ISSUE-011/012 开工 |

判定依据：round-1 的 4 项 P2 全部闭合——P2-2 守卫测试真实存在且变异可杀（审查者独立复现 1 failed）；P2-3 预置哈希冲突统一走 `_record_conflict`，新 3 测试 1:1 钉住（变异复现 2 failed/1 passed）；P2-1 注入 handle 的 flush 计数/顺序与真实 flush 失败测试钉住两次真实 flush（变异复现 3 failed，而相位序列测试不受影响——精确区分"锁 flush"与"锁相位"）；P2-4 崩溃模型注释已改为事实描述。注释与报告口径无"删 flush → N 例失败证明 flush"类不实断言；无新增 xfail/skip/TODO/FIXME、无断言弱化、无范围越界。

判定为 `PASS WITH CONDITIONS` 而非 `PASS` 的原因：仅剩明确、低风险且不阻止本次合并的收尾条件——P3-6（`.agent-teams/` 是否加入 `.gitignore`）待项目负责人决定；P3-4（axis 校验重复）与 `awaiting_rename` 状态识别分别移交 ISSUE-011/012；flush 在本环境行为不可观测的限制须由 M12 真实掉电演练最终确认。以上均为既有记录事项，不是本轮修复缺陷。

---

## 2. 自动识别的审查范围

### 2.1 从 t2 完成输出与仓库交叉识别

| 项 | 事实（以仓库证据为准） |
|---|---|
| Issue | ISSUE-010 增量 writer、checkpoint 与原子 finalize（round-2 最小修复轮） |
| 开发分支 | `feat/issue-010`（无上游跟踪） |
| 目标分支 | `main`（`e852508`，= remotes/origin/main） |
| 审查基线 / HEAD | `ee41360`（= ISSUE-009 提交；feat/issue-009 与 feat/issue-010 同指向） |
| 本批提交 | **0 个**；改动全部为工作区未提交内容 |
| 声称改动文件 | 4 个（见 2.2 表），Git 事实与之相符 |
| 声称状态 | `docs/issues/M02_STORAGE.md` L81：`Review（round 2 最小修复完成，等待独立复审；仅人工验收后置 Done）` |

### 2.2 实际改动文件（`git status --porcelain=v1 -b` 实测）

| 文件 | 状态 | 行数 / 规模 |
|---|---|---|
| `src/uav_gpr/storage/incremental_writer.py` | 未跟踪（本 Issue 生产模块） | 1043 行（与 t1 基线记录一致——**t2 零改动**，实测核实） |
| `tests/integration/test_incremental_writer.py` | 未跟踪（本 Issue 集成测试） | 2014 行 / 51 个测试函数 → **59 个用例**（`--collect-only` 实测 59；t1 基线为 1897 行/56 用例，+117 行/+3 用例与 t2 声明一致） |
| `docs/issues/M02_STORAGE.md` | 已修改（`git diff` 实测恰 1 行：状态行 `Planned → Review（…）`） | 1 行 |
| `docs/plans/2026-08-28-issue-010-writer-close.md` | 未跟踪（修复日志，t2 新增） | 101 行 |

另有 2 个既有未跟踪件（非 t2 改动）：`docs/reports/ISSUE_010_BASELINE_CONFIRMATION.md`（t1 交付）、`docs/reports/ISSUE_010_REVIEW_REPORT.md`（round-1 报告）；`.agent-teams/` 为团队运行时目录（见 P3-6）。

范围外核对（Git 实测）：`core/**`、`src/uav_gpr/storage/rcscan_v2.py`、`tests/contract/**`、`tests/unit/**`、`docs/DATA_FORMAT.md`、`docs/adr/**`、`tools/**`、`.gitignore` 全部**零改动**（无 M/A/D 状态、无新增未跟踪件）。

### 2.3 t2 声称行号核对

P2-3 新测试 L865–973 ✓；P2-4 注释 L1438–1457（实测块为 L1439–1457，±1 行）✓；P2-2 守卫测试 L1747–1788（t1 基线记录旧位置 L1630–1671，因上方 +117 行插入而平移）✓；实现侧 P2-3 预设块 L702–715 ✓、第二处守卫 L946–951 ✓、`_force_close_handle` L1023–1033 ✓。

---

## 3. 主要问题（按 P0 → P3 排序）

**P0 / P1：无。**

**P2：无（round-1 的 4 项 P2 逐项闭合，证据见第 4 节）。**

**P3 级剩余项（全部为既有记录事项，非本轮修复引入，不阻止合并）：**

- **P3-4**　`_require_axis`（`incremental_writer.py` L324–344）与 `rcscan_v2._require_frequency_axis` 的维度/有限性/严格递增校验重复。建议 ISSUE-011 合并到 schema 侧单点维护。`[src/uav_gpr/storage/incremental_writer.py:324]`
- **P3-6**　`.agent-teams/` 未跟踪且未加入 `.gitignore`（`git check-ignore` 实测 NOT IGNORED）。合并前确认不纳入提交；是否忽略由项目负责人决定。`[工作区根]`
- **P3-7（记录）**　`awaiting_rename` 状态下文件已带 `lifecycle_state=finalized` + `completion_kind` 但仍名 `.partial.rcscan`；ISSUE-012 恢复工具必须显式识别该状态。`[src/uav_gpr/storage/incremental_writer.py:128, 967–997]`

---

## 4. 逐 Issue 验收矩阵（ISSUE-010）

### 4.1 round-2 修复条件（round-1 报告第 10 节第 1–3 行）逐项独立复验

| 条件 | 状态 | 代码证据（精确行号） | 审查者独立测试证据 |
|---|---|---|---|
| **P2-2**：rename 前第二处"目标已存在"守卫有真实测试、断言形态正确 | **`PASS`** | 守卫：`incremental_writer.py` L946–951（`close()` 内，仅 `AWAITING_RENAME` 重试路径可达；第一处 L973–978 在 `_finalize_file` 内）。测试：`tests/integration/test_incremental_writer.py` L1747–1788 `test_rename_retry_refuses_when_the_target_appeared_in_the_meantime`——`_FlakyRenameFacade(fail_times=1)` → 首次 `close()` 抛 OSError → 状态 `AWAITING_RENAME` → 外部写哨兵字节 → 重试 `close()` 断言 `pytest.raises(DomainError)` + `code is ErrorCode.INVALID_ARGUMENT` + `context["path"]` + 哨兵字节未变 + `filesystem.attempts == 1`（rename 未再尝试）+ partial 保留且 `lifecycle_state=finalized` | **变异探针 A（复现 t2，独立执行）**：在系统临时目录 `D:\dsh\windows\temp\uav-gpr-review-r2-probes\mutA` 复制源码树并仅删除 L946–951 第二处守卫 → 定向 `-k "rename_retry_refuses or close_refuses_to_overwrite"` **1 failed**（守卫测试 `DID NOT RAISE`——守卫缺失时重试直接 `os.replace` 覆盖哨兵）+ 1 passed（第一处守卫测试不受影响，证明 1:1 钉住**第二处**而非第一处）。真实代码上同命令 2 passed |
| **P2-3**：预置哈希矛盾统一走 `_record_conflict`、留 `TraceConflict` 证据、context 键集统一、fail-closed 不变 | **`PASS`** | 预设块：`incremental_writer.py` L702–715（`preset_hash is not None and preset_hash != digest` → `_record_conflict`；新 index 以 `(-1, preset_hash, uid)` 为 stored）；`metadata.with_integrity(digest)` L718 在其后。统一证据/键集：`_record_conflict` L872–905（`trace_index`/`record_position`/`stored_hash`/`incoming_hash`/`stored_trace_uid`/`incoming_trace_uid`/`duplicate_trace_uid`/`conflicting_trace_index`）。测试：L865–914（新鲜 index：断言 `ID_CONFLICT`、context 各键、`conflicts` 长度 1、`record_position == -1`、committed/physical 不推进、去哈希重试 `NEW` 成功、writer 保持可用）、L917–948（已提交 index：证据指向已提交行、`abort()` 后 reader 只看到原道）、L951–973（预置匹配 digest：`NEW` → 重复提交 `DUPLICATE` 幂等、无冲突） | **变异探针 B（复现 t2，独立执行）**：临时树 `mutB` 仅删除 L702–715 预设块（保留 `with_integrity`）→ `-k "pre_attached"` **2 failed**（两个负向用例分别 `KeyError: 'trace_index'` / `'record_position'`——context 键集缺失且 `conflicts` 为空）+ 1 passed（匹配哈希用例不依赖该块）。真实代码上同命令 3 passed。与既有同 index 不同 hash 冲突路径对拍：二者共用同一 `_record_conflict`，错误码、键集、不写入、不推进 checkpoint 行为一致 |
| **P2-1**：两次 flush 有测试可杀死；注释与报告口径为事实描述 | **`PASS`** | 测试：L1302–1358 `test_each_commit_performs_two_real_hdf5_flushes_around_the_checkpoint`（`hdf5_opener` 注入 `_FlushSpy`，断言 1 创建 + 2×2 提交 + 1 finalize = 6 次真实 `flush()` 且逐事件对拍：`flush#2` 在 `AFTER_DATA_FLUSH` 播报**前**、`flush#3` 在 `AFTER_COMMIT_FLUSH` 前——flush 与相位播报的先后也被钉住）；L1361–1436 两个真实 flush 失败用例（`_ArmFlushFailure` 使 `h5.flush()` 抛 `OSError(28)`：数据 flush 失败 → checkpoint 不推进、中断行不可见；提交 flush 失败 → 落盘行完整可读）。口径：L1214–1225 注释（"本环境删除 flush 无法通过字节比较观测，唯一可观测点是 handle 本身"——诚实）；相位序列测试 L1962–1990 docstring（"删除 flush 会连带删除相位播报并破坏序列"——只声称锁序列，不声称证明 flush 持久化） | **变异探针 C（审查者补充，独立执行）**：临时树 `mutC` 仅删除 `_flush` 内 `self._h5.flush()`（保留 `on_phase` 播报）→ `-k "two_real_hdf5_flushes or data_flush_failure or commit_flush_failure"` **3 failed**（计数断言失败 + 2×`DID NOT RAISE`）；同变异体上相位序列测试 **1 passed**。精确证明：spy/失败测试锁的是 flush 本身，相位序列测试锁的是提交顺序——与报告口径完全一致 |
| **P2-4**：子进程崩溃用例注释改为事实描述，无"掉电现场"不实论断 | **`PASS`** | 注释：`tests/integration/test_incremental_writer.py` L1439–1457——明确表述：writer 在异常传出 `append_trace`/`_finalize_file` **之前**已应急 `flush()+close()`（`_force_close_handle`，实现 L1023–1033 在 `except BaseException` 路径上先于异常传播执行）；子进程 `os._exit()` 因此不改变落盘状态，与进程内故障模型一致；本组用例价值 = 跨进程不变式验证；flush 本身由 handle spy 测试钉住；"本环境无法产生未 flush 掉电"如实说明 | 6 个子进程用例全部通过（含在 59 passed 中）；注释与实现事实逐句对拍无矛盾。测试行为零改动、未删测试 |

### 4.2 三条验收标准（M02_STORAGE.md L100–104，round-1 已独立复验 PASS，本轮回归未削弱）

| 验收标准 | 状态 | 证据 |
|---|---|---|
| ① 每个故障点后 reader 最多看到最后完整 checkpoint，不看到半道 | **`PASS`** | 提交顺序实现 L747–787（raw → 全列 → flush#1 → checkpoint 三值 → flush#2 → 内存态推进）；回归：全量 435 passed 含全部 10 相位崩溃矩阵与半道不可见用例（L1137–1613）；round-1 独立探针结论未被动摇（t2 未改实现） |
| ② 不兼容 sweep、重复冲突、磁盘/flush 失败不推进 checkpoint | **`PASS`** | 冻结校验 L799–831；冲突/重复 L702–745；真实 flush 失败 L1361–1436（ENOSPC 用例）；回归 59 passed |
| ③ finalized 文件不可继续 append，原 partial 不被无意覆盖 | **`PASS`** | 两处守卫 L946–951 / L973–978 + `os.replace` 原子改名（L953）；`test_close_refuses_to_overwrite_an_existing_target` L1876–1896 + 新守卫测试 L1747–1788；变异探针 A 证明第二处守卫被 1:1 钉住 |

### 附加核对（ISSUE-010 范围/硬约束）

| 核对项 | 结论 | 证据 |
|---|---|---|
| 复用 ISSUE-008 schema/codec、ISSUE-009 哈希 | PASS | 实现仅调用 `schema.create_rcscan_v2`/`dataset_contracts`/`trace_metadata_to_cells`（L497/523/756）与 `compute_raw_trace_sha256`（L693）；契约 134 passed |
| 未越界实现 ISSUE-011/012、UI/网络/outbox | PASS | 模块 docstring L67–68 声明 out of scope；无 reader/恢复/网络代码 |
| core 隔离 | PASS | `tests/unit/test_core_isolation.py` + `test_no_external_access.py` 4 passed |
| 无 xfail/skip/TODO/FIXME/sleep，无断言弱化 | PASS | 测试全文 grep 实测：`sleep` 仅 2 处且均在注释（L27、L1457）；无 xfail/skip/TODO/FIXME；`pytest.raises` 均带错误码断言；新增 3 测试全部强等值断言 |
| t2 未改实现文件 | PASS | 实现 1043 行与 t1 基线记录一致；Git 事实该文件始终未跟踪、无中间快照可 diff，以行数 + 关键行号（L702–715/L946–951/L1023–1033 与 t1 基线单描述逐一相符）核实 |

---

## 5. Git 与交付检查

- **当前分支**：`feat/issue-010` @ HEAD `ee41360`（= ISSUE-009 提交，= round-1 审查基线）；`main` = `e852508`（= remotes/origin/main，无领先/落后之外的远端提交）。
- **本批提交**：**0 个**。无 commit/push/merge/stash。`git reflog` 实测仅 ISSUE-009 的 commit 记录与 HEAD 移动，无 reset/rebase/amend 迹象。
- **未提交修改**：仅 `docs/issues/M02_STORAGE.md` 1 行（`git diff` 实测，内容 = 状态行）。
- **未跟踪文件**：`src/uav_gpr/storage/incremental_writer.py`、`tests/integration/test_incremental_writer.py`、`docs/plans/2026-08-28-issue-010-writer-close.md`、`docs/reports/ISSUE_010_BASELINE_CONFIRMATION.md`、`docs/reports/ISSUE_010_REVIEW_REPORT.md`、`.agent-teams/`。**无任何内容进入暂存区**（`git status` 无 staged 条目），`.agent-teams/**` 未被纳入任何提交（`git ls-files` 实测其不在跟踪清单）。
- **状态行一致性**：M02 L81 `Review（round 2 最小修复完成，等待独立复审；仅人工验收后置 Done）` 与 `docs/issues/README.md` 第 2 节 `Review：实现和测试完成，等待人工审查` 定义一致；置 `Done` 由项目负责人执行，审查者不操作。
- **空白/卫生**：`git diff --check` 退出 0；对两个未跟踪文件 `git diff --no-index --check /dev/null <file>` 亦干净（CRLF 提示为仓库 autocrlf 信息，非错误）。审查全程 `-p no:cacheprovider`，未在项目内留缓存/日志/实测数据；审查后 `git status` 与审查前完全一致。
- **审查者自身产出**：仅新增本报告文件。全部变异探针树与脚本位于系统临时目录 `D:\dsh\windows\temp\uav-gpr-review-r2-probes\`（审查结束前已删除），项目内零残留。

---

## 6. 测试与验证结果

### 6.1 环境（按任务契约使用 Windows 解释器，未启用 WSL）

| 项 | 实测值 |
|---|---|
| 解释器 | `D:\博士任务\无人机软件\UAV-GPR\.venv\Scripts\python.exe`，**Python 3.13.14** |
| 关键包 | numpy 2.5.2 / h5py 3.16.0 / pytest 8.4.2（venv 内实测导入）；ruff / mypy 由 verify.py 调用 |
| 说明 | t1/t2 因自身 shell 为 WSL 而使用 `/usr/bin/python3`（3.12.3）并如实记录；审查者在 pwsh 中实测 Windows venv 可用，全部数字与 t2 的 WSL 数字**完全一致**——跨环境一致，进一步佐证结论不依赖环境 |

### 6.2 复跑的命令与结果（真实命令 / 退出码 / 通过数）

| # | 命令（工作目录 `D:\博士任务\无人机软件\UAV-GPR`） | 退出码 | 结果 |
|---|---|---:|---|
| 1 | `.\.venv\Scripts\python.exe -m pytest tests\integration\test_incremental_writer.py -q -p no:cacheprovider` | 0 | **59 passed in 2.75s**（`--collect-only` 实测 59；t2 声明 59 ✓） |
| 2 | `.\.venv\Scripts\python.exe -m pytest tests\contract\test_storage_schema.py tests\contract\test_raw_trace_hash.py -q -p no:cacheprovider` | 0 | **134 passed**（t2 声明 134 ✓） |
| 3 | `.\.venv\Scripts\python.exe tools\quality\verify.py` | 0 | pytest 非硬件 **435 passed, 1 deselected**；ruff ok；mypy `Success: no issues found in 31 source files`；package import ok；`[quality] all gates passed`（t2 声明 435/1/全绿 ✓；deselect 为既有 hardware 双重 opt-in 哨兵，与本 Issue 无关） |
| 4 | `.\.venv\Scripts\python.exe -m pytest tests\unit\test_core_isolation.py tests\unit\test_no_external_access.py -q -p no:cacheprovider` | 0 | **4 passed** |
| 5 | `git diff --check`；`git diff --no-index --check /dev/null <两个未跟踪文件>` | 0 | 无空白错误 |
| 6 | 变异探针 A（P2-2）：临时树删除 `close()` 第二处守卫 | 1（预期） | `test_rename_retry_refuses_when_the_target_appeared_in_the_meantime` **1 failed（DID NOT RAISE）**；`test_close_refuses_to_overwrite_an_existing_target` 1 passed |
| 7 | 变异探针 B（P2-3）：临时树删除预设块 L702–715 | 1（预期） | `test_pre_attached_contradictory_hash_*` **2 failed（KeyError：context 缺 `trace_index`/`record_position`）**；匹配哈希用例 1 passed |
| 8 | 变异探针 C（P2-1 补强）：临时树删除 `_flush` 内 `h5.flush()`（保留播报） | 1（预期） | 3 个 flush 测试 **3 failed**；相位序列测试 1 passed（锁 flush ≠ 锁相位，与报告口径一致） |

无失败、无 error、无 xfail、无 skip；无 `sleep` 猜时序（仅注释声明）。所有探针在系统临时目录运行、对项目文件零写入、事后已删除。

### 6.3 测试质量核对（t2 新增 3 用例 + 注释重写）

- 新增 3 用例覆盖：新鲜 index 冲突（证据 + fail-closed + writer 可恢复使用）、已提交 index 冲突（证据指向已提交行 + 原道不被污染）、匹配哈希幂等（NEW → DUPLICATE、零冲突）；断言全部强等值（`is ErrorCode.ID_CONFLICT`、context 逐键、`conflicts` 逐字段），无宽松断言。
- P2-4 注释重写后与 `_force_close_handle`（L1023–1033）实现事实逐句一致；6 个子进程用例行为零改动。
- 测试独立性：59 用例可单文件运行（定向 2.75s），无顺序依赖。

---

## 7. 报告与事实差异

| # | t2/任务材料声明 | 审查核实 | 性质 |
|---|---|---|---|
| 1 | "59 passed / 134 passed / 435 passed, 1 deselected / ruff / mypy 31 files / import 全绿；core 隔离 4 passed" | 全部在 Windows venv 上逐条复现，数字一致 | 一致 |
| 2 | "变异：删第二处守卫 → 守卫测试 DID NOT RAISE 失败；删预设块 → 负向 2 用例 KeyError、匹配哈希通过" | 审查者独立重做变异（临时树，未复用 t2 脚本），结果逐项一致 | 一致 |
| 3 | "实现文件零改动" | 行数（1043）与 t1 基线一致；关键行号（L702–715/L946–951/L1023–1033）与 t1 描述相符；Git 事实无第三方改动迹象 | 一致（未发现反证） |
| 4 | "测试 1897→2014 行、56→59 用例" | 实测 2014 行、`--collect-only` 59 | 一致 |
| 5 | 环境：t2 称 Windows `.venv` 在其 WSL shell 中不可用，改用 `python3`（3.12.3） | 属实（WSL bash 视角）；但 Windows venv 经 pwsh 可用（3.13.14），审查者按契约使用之，全部数字与 WSL 完全一致 | 环境口径差异，不影响结论 |
| 6 | 任务契约必读清单列出 `docs/reports/ISSUE_010_BASELINE_CONFIRMATION_R2.md` | 仓库无此文件；实际基线件为 `ISSUE_010_BASELINE_CONFIRMATION.md`（已读） | 任务提示词命名笔误（ISSUE-009 曾有 R2 基线单，疑似沿用命名） |
| 7 | t2 称 P2-4 注释位于 L1438–1457 | 实测块为 L1439–1457 | ±1 行，无实质差异 |
| 8 | 无任何"删 flush → N 例失败证明 flush 持久化"类断言 | 测试注释（L1214–1225、L1968–1972）、计划 0.2/0.5、t2 输出均表述为"锁提交顺序与相位序列 + 注入 handle 观测 flush 次数/顺序" | 一致（审查者变异探针 C 佐证该口径精确） |

---

## 8. 剩余风险

1. **flush 在本环境行为不可观测**（h5py/HDF5 写入即对其他进程可见）。P2-1 的注入 handle spy + 真实 flush 失败用例 + 变异探针 C 是当前最强可重复证据，但"掉电后 checkpoint 不变式"的硬件级验证仍需 M12 副本数据掉电/partial 恢复演练。
2. **`awaiting_rename` 状态识别**移交 ISSUE-012：该状态下文件已 `lifecycle_state=finalized` 且带 `completion_kind`，恢复工具不得当普通未完成任务处理。
3. **P3-4** axis 校验重复：建议 ISSUE-011 收口为 schema 单点。
4. **P3-6** `.agent-teams/` 未忽略：合并前确认不纳入提交；是否加 `.gitignore` 由项目负责人决定。
5. **全部改动未提交**（本批 0 commit）：合并/提交动作须由项目负责人明确授权执行，执行器不得自行 commit/push。

---

## 9. 合并建议

- **整批结论 `PASS WITH CONDITIONS`；ISSUE-010 round-2 修复真实、完整地闭合了 round-1 的 P2-1～P2-4，无 P0/P1、无关键验收失败、无必要测试失败，可进入项目负责人人工验收。**
- 验收通过后建议：由负责人将 `docs/issues/M02_STORAGE.md` ISSUE-010 状态置 `Done`，并按项目惯例决定提交/合并（审查者不执行）。
- 不阻塞下游开工：ISSUE-011（reader）依赖的 `committed_record_count` 语义、ISSUE-012（partial 恢复）依赖的 partial 保留与 4 种 `completion_kind` 落盘均未被动摇；ISSUE-012 需显式识别 `awaiting_rename` 状态（见第 8 节）。
- 不建议拆分合并；本批仅一个 Issue。

---

## 10. 最小修复清单

**本轮阻断项：无。** round-1 清单第 1–3 行（P2-2/P2-3/P2-1/P2-4）全部闭合并经独立变异复验；第 4–8 行（P3）中 P3-1/2/3/5 已在工作树闭合（t1 基线实测），剩余事项如下（均为移交/负责人决策，不阻断本次合并）：

| 序号 | 等级 | 事项 | 位置 | 处理 |
|---|---|---|---|---|
| 1 | P3 | `.agent-teams/` 未跟踪未忽略 | 工作区根 | 合并前确认不纳入提交；是否加 `.gitignore` 由项目负责人决定 |
| 2 | P3 | `_require_axis` 与 `rcscan_v2` 校验重复 | `incremental_writer.py` L324–344 | 移交 ISSUE-011 收口 |
| 3 | 记录 | `awaiting_rename` 状态识别 | `incremental_writer.py` L128、L967–997 | 移交 ISSUE-012 恢复工具 |
| 4 | 记录 | 真实掉电演练 | M12（ISSUE-059/060） | 副本数据掉电/partial 恢复演练补足 flush 行为验证 |

清理项（非修复）：本审查产生的探针树与脚本位于系统临时目录 `D:\dsh\windows\temp\uav-gpr-review-r2-probes\`，项目内零残留，已随审查结束删除。

---

*审查结束。审查者未修改任何实现、测试、计划或 Git 状态，仅新增本报告文件，等待项目负责人决定修复、拆分或合并。*
