# ISSUE-010 独立审查报告

- 审查日期：2026-08-28
- 审查性质：只读复审（未修改实现、测试、Git 状态；仅新增本报告文件）
- 审查标准：`docs/ISSUE_REVIEW_STANDARD.md` v1.0（输出格式按第 13 节 10 段）
- 审查基线：`ee41360 feat(core): add canonical raw trace hash and golden vectors (ISSUE-009)`
- 审查对象：`ISSUE-010 增量 writer、checkpoint 与原子 finalize`

---

## 1. 审查结论

| 项 | 结论 |
|---|---|
| **整批结论** | **`PASS WITH CONDITIONS`** |
| **ISSUE-010 单 Issue 结论** | **`PASS WITH CONDITIONS`** |
| 是否存在 P0 | 否 |
| 是否存在 P1 | 否 |
| 关键验收是否失败 | 否（三条验收标准独立复验全部 `PASS`） |
| 必要测试是否失败 | 否（全量门禁与定向测试复跑全绿） |

判定依据：三条验收标准均由**审查者独立探针**（跨进程硬崩溃矩阵、真实 ENOSPC flush 失败、撒谎文件系统门面）直接验证通过；实现严格复用 ISSUE-008 schema/codec 与 ISSUE-009 哈希，未越界实现 ISSUE-011/012，未破坏 core 隔离与 raw 不可变。未发现 P0/P1。

判定为 `PASS WITH CONDITIONS` 而非 `PASS` 的原因：存在 4 项 P2——均不阻断合并、不破坏已验证的验收标准，但属于"明确要求处理"的测试强度与证据完整性缺陷，且其中 2 项使完成报告中的变异验证结论无法按字面成立。合并前建议至少闭合 P2-2（第二处覆盖守卫零覆盖）与 P2-3（冲突证据缺失），二者均为小范围改动。

可否拆分合并：本批只有 ISSUE-010 一个 Issue，无需拆分。下游 ISSUE-011（reader）与 ISSUE-012（partial 恢复）依赖本 Issue 的 `committed_record_count` 语义与 partial 保留语义，本报告的 P2 项均不改变这两个对外契约，故**不阻塞**下游开工。

---

## 2. 自动识别的审查范围

### 2.1 从完成报告与仓库交叉识别

| 项 | 事实（以仓库证据为准） |
|---|---|
| Issue | ISSUE-010 增量 writer、checkpoint 与原子 finalize；直接依赖 ISSUE-008、009 |
| 开发分支 | `feat/issue-010`（本地分支，无上游跟踪） |
| 目标分支 | `main`（`e852508`） |
| 审查基线 | `ee41360`（= ISSUE-009 提交，符合声明） |
| 本批提交 | **0 个**；`feat/issue-010` 与 `feat/issue-009` 同指向 `ee41360`，改动全部为工作区未提交内容 |
| 基线后完整差异 | 仅 3 项（见第 5 节） |
| 声称状态 | `docs/issues/M02_STORAGE.md` L81 `Planned → In progress`；Issue 文档本身仍无"完成报告" |

### 2.2 实际改动文件

| 文件 | 状态 | 行数 |
|---|---|---|
| `src/uav_gpr/storage/incremental_writer.py` | 新增（未跟踪） | 1005 |
| `tests/integration/test_incremental_writer.py` | 新增（未跟踪） | 1616，44 个测试函数 → 52 个用例（含参数化） |
| `docs/issues/M02_STORAGE.md` | 已修改（1 行） | `- 状态：Planned` → `- 状态：In progress` |

完成报告第 2 节把测试路径写作 `tests/integration/test_integration/test_incremental_writer.py`，实际路径为 `tests/integration/test_incremental_writer.py`（无 `test_integration` 中间层）。属于笔误，不影响范围判断。

### 2.3 声称行号核对（全部准确）

`WritePhase` L132、`PhaseFaultHook` L167、`FileSystemFacade` L204、`LocalFileSystemFacade` L216、`WriterState` L120、`create()` L404、`classify_trace()` L606、`append_trace()` L632、`_append_trace()` L655、`_flush()` L751、`_write_row()` L818、`_record_conflict()` L837、`close()` L874、`_finalize_file()` L929、`abort()` L961。

---

## 3. 主要问题

### P2-1　两次 flush 的移除无法被任何测试杀死；完成报告的变异数字只锁定了"相位播报"而非"flush"

- **所属 Issue**：ISSUE-010
- **文件与行号**：`src/uav_gpr/storage/incremental_writer.py` L724（`self._flush(WritePhase.AFTER_DATA_FLUSH)`）、L736（`self._flush(WritePhase.AFTER_COMMIT_FLUSH)`）、L751–759（`_flush` 实现）；`tests/integration/test_incremental_writer.py` L1564–1592（相位序列测试）、L1116–1173（子进程崩溃模型）
- **触发条件**：在复制的源码树上做变异（审查者独立执行，未改项目文件），分别
  - (a) 仅删除 `self._h5.flush()`、保留 `on_phase(...)`；
  - (b) 删除整行（flush + 相位播报）。
- **实际影响（实测）**：

  | 变异 | 失败用例数 |
  |---|---:|
  | (a) 仅删 flush#1 的 `h5.flush()` | **0**（52 passed） |
  | (b) 删 flush#1 整行 | 5 |
  | (a) 仅删 flush#2 的 `h5.flush()` | **0**（52 passed） |
  | (b) 删 flush#2 整行 | 3 |
  | 仅删 finalize flush 的 `h5.flush()` | **0** |

  即：测试套件只能发现"相位播报消失"，无法发现"flush 消失"。进一步，审查者用绕过 writer 应急 flush 的探针在**已删除 flush#1 的变异体**上重跑真实掉电矩阵，6 个相位的落盘状态与未变异时**逐字节一致**（HDF5/h5py 3.16 在本环境写入即持久化，flush 在本环境不可观测）。因此"删 flush → N 例失败"不构成对"flush 生效"的回归保护。
- **违反要求**：`docs/DATA_FORMAT.md` 第 3 节"writer 必须先写数据、flush，再更新 `committed_record_count` 并再次 flush"；`docs/ISSUE_REVIEW_STANDARD.md` 第 9 节"标准门禁全绿不等于验收自动通过"。
- **最小修复方向**：不要求改实现（实现已正确执行两次 flush）。建议：(1) 把完成报告/测试注释中的变异结论改为"锁定提交顺序与相位序列"，不再声称证明了 flush；(2) 若需真正锁死 flush，用可替换的 `FileSystemFacade` 或注入的 handle 包装器记录 `flush()` 调用次数与顺序（与相位序列一并断言），或至少在相位序列测试旁补一条"每次提交恰好两次 flush"的可观测断言。

### P2-2　`close()` 中 rename 前的第二处"目标已存在"守卫零测试覆盖

- **所属 Issue**：ISSUE-010
- **文件与行号**：`src/uav_gpr/storage/incremental_writer.py` L906–913（`close()` 内 rename 前守卫）；对照第一处 L935–940（`_finalize_file()` 内）
- **触发条件**：变异删除 L906–913 的守卫块后运行 `tests/integration/test_incremental_writer.py`。
- **实际影响（实测）**：删除第二处守卫 → **0 失败（52 passed）**；删除第一处守卫 → 1 失败。也就是说"两处守卫"只有第一处被测试覆盖。该守卫唯一可达路径是"rename 失败 → 状态 `awaiting_rename` → 重试 `close()`"，而这正是它存在的理由；`test_rename_failure_preserves_partial_and_can_be_retried`（L1360–1390）已经到达 `awaiting_rename` 状态，但没有覆盖"重试期间目标文件出现"这一分支。
- **违反要求**：`AGENTS.md` 第 10 节"每项能力必须覆盖正常、错误、取消/恢复路径"；验收标准③（原 partial/终态不被无意覆盖）。
- **最小修复方向**：新增一条测试：注入始终失败的 rename 门面 → `close()` 抛错 → 外部创建 `final_path` → 换回正常工作门面（或直接让门面成功）再 `close()`，断言抛 `DomainError(INVALID_ARGUMENT)` 且既有终态文件字节未被改写。

### P2-3　预置哈希不一致的冲突绕过 `_record_conflict`，`writer.conflicts` 不留证据

- **所属 Issue**：ISSUE-010
- **文件与行号**：`src/uav_gpr/storage/incremental_writer.py` L680（`metadata = metadata.with_integrity(digest)`）、L694–708（writer 自身的 `_record_conflict` 调用）、L45–53（模块 docstring 的冲突承诺）
- **触发条件**：提交的 `TraceMetadata` 自身已携带 `raw_trace_sha256`，且该值与 writer 依据 frozen axis/channels 计算出的 digest 不一致（空地重传/补传场景的典型形态）。
- **实际影响（实测）**：

  ```
  == A. conflict detected by TraceMetadata.with_integrity (pre-attached hash) ==
    code: id_conflict
    context: {'stored_hash': 'aaaa...', 'incoming_hash': 'c764f222...'}
    writer.conflicts: ()          <-- 证据为空
    committed: 1 ; physical: 1    <-- 未写入、未覆盖（fail-closed 正确）
  ```

  冲突确实 fail-closed，原记录未被覆盖，结构化 `DomainError(ID_CONFLICT)` 已抛出；但 `writer.conflicts`（模块 docstring L49–53 承诺的"immutable `TraceConflict` evidence entry"）为空，错误上下文也缺少 `trace_index` / `trace_uid` / `record_position`，调用方无法据此建立审计链。
- **违反要求**：`AGENTS.md` 第 4 节"已存在相同 `mission_id + trace_index` 但哈希不同的数据属于冲突：必须 fail-closed、报警并**保留证据**"；与本模块 docstring L45–53 自述不一致。
- **最小修复方向**：在 `_append_trace` L680 处捕获 `with_integrity` 抛出的 `ID_CONFLICT`，或先判断 `metadata.raw_trace_sha256 not in (None, digest)`，统一改走 `self._record_conflict(...)` 再抛错，使两条冲突路径都产生 `TraceConflict` 证据并携带一致的 context 键集。

### P2-4　6 个"子进程 `os._exit()` 硬崩溃"用例的建模前提不成立，测试注释论断与事实不符

- **所属 Issue**：ISSUE-010
- **文件与行号**：`tests/integration/test_incremental_writer.py` L1102–1111（注释）、L1116–1173（`crash_child_main` / `run_crash_child`）；`src/uav_gpr/storage/incremental_writer.py` L647–653、L951–957
- **触发条件**：任一 append/finalize 相位故障。
- **实际影响（实测）**：`append_trace` L651–653 与 `_finalize_file` L951–957 在把异常交给调用方**之前**已经执行 `self._h5.flush()` + `self._h5.close()`（writer 自身的应急路径）。因此子进程在 `os._exit()` 之前，HDF5 文件**已经被 flush 并关闭**，`os._exit()` 不改变任何落盘状态。注释 L1103–1106 声称"unwinding the stack closes (and therefore flushes) the HDF5 handle, which hides any unflushed state"，并据此认为子进程能留下"掉电现场"，这一论断不成立。
  审查者用绕过应急 flush 的探针（直接调用私有 `_append_trace`，再 `os._exit`）做对照，6 个相位的落盘 `committed/rows/lifecycle` 与子进程用例**完全一致**，证明这 6 个用例相对进程内故障用例没有增加区分力。
- **违反要求**：`docs/TESTING.md` 第 4 节故障注入必须"可确定性注入"；`ISSUE_REVIEW_STANDARD.md` 第 10 节完成报告真实性核对。
- **最小修复方向**：不删测试（断言本身仍然有效）。建议把 L1102–1111 的注释改为事实描述："writer 在异常传出前会应急 flush+close，子进程模型与进程内故障模型落盘状态一致；本组用例的价值是跨进程验证不变式，而非模拟未 flush 的掉电。"若要真正建模未 flush 的掉电，需要把故障注入点前移到 HDF5 写调用本身（可注入 handle/门面），而不是相位钩子。

### P3-1　`close()` 注释声称 "TOCTOU-safe"，实际是 check-then-use

- **文件与行号**：`src/uav_gpr/storage/incremental_writer.py` L906–907（注释）、L908–913 + L915（`exists()` 后 `replace()`）、L222–223（`os.replace`）
- **触发条件**：`filesystem.exists()` 与 `replace()` 之间目标文件被创建；或 `exists()` 实现不诚实。
- **实际影响（实测）**：注入一个 `exists()` 恒返回 `False` 的门面后，`close()` 成功执行并把已存在的 `0f0e8a3b-….rcscan` 覆盖为新 HDF5 文件（原有字节被完全替换）。生产门面是诚实的，正常情况下两处守卫有效（见验收矩阵③），但注释的技术断言不成立；Windows 上 `os.replace` 是**无条件覆盖**语义。
- **违反要求**：`ISSUE_REVIEW_STANDARD.md` 第 8.5 节"危险文件操作"与注释/实现一致性。
- **最小修复方向**：二选一——(a) 把注释改为"best-effort 前置检查，单所有者前提下有效"；(b) 真正做原子不覆盖：先 `os.link(src, dst)`（目标存在则失败）再 `os.unlink(src)`，并以 `FileSystemFacade` 暴露该原语。

### P3-2　`trace_index_at_record` 的反向查找是 O(n) 线性扫描

- **文件与行号**：`src/uav_gpr/storage/incremental_writer.py` L831–835（`_index_of_position`）、L595–604（`trace_index_at_record`）
- **触发条件**：对已提交物理行做逐行反查。
- **实际影响（实测）**：2000 道全量反查耗时 0.044 s（≈4×10⁶ 次字典遍历）；按此线性外推，M10/M12 要求的十万道规模将退化到数十秒量级，且 writer 进程内该调用常用于对账与显示联动。
- **违反要求**：`AGENTS.md` 第 7 节性能规则；`ISSUE_REVIEW_STANDARD.md` 第 8.5 节"性能退化"。
- **最小修复方向**：在 `_by_trace_index` 之外维护 `dict[int, int]`（position → trace_index）反向索引，写入时同步更新。

### P3-3　`create()` 不检查终态文件是否已存在，失败发现得太晚

- **文件与行号**：`src/uav_gpr/storage/incremental_writer.py` L474–492
- **触发条件**：目标目录下已存在 `<mission_id>.rcscan`。
- **实际影响（实测）**：writer 正常创建并写入，`close()` 时才以 `INVALID_ARGUMENT` 拒绝，整任务数据滞留在 partial。行为是 fail-closed 的（旧终态文件字节未被改动，见验收矩阵③），符合"不覆盖"优先级，但运维上发现太晚。
- **最小修复方向**：`create()` 在建文件前用 `filesystem.exists(final_path)` 做一次早期 fail-closed，或在文档/返回值中明确"终态冲突只在 finalize 时暴露"。

### P3-4　`_require_axis` 与 `rcscan_v2._require_frequency_axis` 重复校验逻辑

- **文件与行号**：`src/uav_gpr/storage/incremental_writer.py` L316–336；`src/uav_gpr/storage/rcscan_v2.py` L987–997
- **实际影响**：同一维度/有限性/严格递增校验有两份实现，仅异常类型不同（`DomainError(ErrorCode.*)` vs `ValueError`）。后续 schema 收紧时存在只改一处而漂移的风险。
- **最小修复方向**：把校验收在 `rcscan_v2` 一处并让 writer 捕获/转换，或让 writer 显式调用 schema 的版本。低优先，可在 ISSUE-011 一并处理。

### P3-5　测试通过改写私有属性完成 rename 重试

- **文件与行号**：`tests/integration/test_incremental_writer.py` L1387（`writer._filesystem = working`）
- **实际影响**：白盒耦合到私有字段；重构 `FileSystemFacade` 时测试会静默失效而不报错。
- **最小修复方向**：给 writer 增加"可重新注入门面"的公开方式（如 `replace_filesystem()`），或在 `create()` 支持 `filesystem` 由测试持有的可变对象。

### P3-6　交付卫生：`.agent-teams/` 未跟踪且未加入 `.gitignore`

- **文件与行号**：工作区未跟踪项 `.agent-teams/retired-members.json`、`.agent-teams/uav-gpr-issue-009-r2/**`、`.agent-teams/uav-gpr-issue-009-raw-hash/**`
- **实际影响**：历史会话（ISSUE-009）遗留的协作目录，非本 Issue 产生，但 `git status` 中显示为未跟踪，存在被误 `git add .` 带入提交的风险。
- **最小修复方向**：合并前确认不纳入提交；建议由项目负责人决定是否加入 `.gitignore`。（审查者未做任何 Git 写操作。）

---

## 4. 逐 Issue 验收矩阵（ISSUE-010）

验收标准来源：`docs/issues/M02_STORAGE.md` L100–104。

### ① 每个故障点后 reader 最多看到最后完整 checkpoint，不看到半道 — **`PASS`**

- **代码证据**：
  - 提交顺序 L710–749：raw 写入（L713–717）→ 全列写入（L719–721）→ `flush#1`（L724）→ 写 `committed_record_count` / `last_trace_index` / `updated_utc`（L727–732）→ `flush#2`（L736）→ 内存态推进（L738–741）。checkpoint 写入严格位于数据 flush 之后。
  - `_flush()` L751–759：先 `self._h5.flush()`，后 `on_phase(phase)`——**相位播报与真实 flush 不可分离**（不存在"只播报不 flush"的缝）。
  - 任何异常 L651–653 / L951–957 都会关闭句柄并把 writer 置为 `aborted` / `awaiting_rename`，绝不留下伪 finalized。
- **审查者独立测试证据**（一次性探针，系统临时目录）：对全部 10 个相位点（6 个 append + 4 个 finalize）用**子进程 `os._exit()` 硬崩溃**后，以 h5py 直接读取 HDF5 并按 `DATA_FORMAT.md` 第 3 节规则（行号 < `committed_record_count` 且所有必需列齐备）判定：

  | 相位 | committed | raw 行 | idx 行 | gnss 行 | 短列 | 可解码行 | 判定 |
  |---|---:|---:|---:|---:|---:|---:|---|
  | before_raw_write | 3 | 3 | 3 | 3 | 0 | 3 | OK |
  | after_raw_write | 3 | 4 | 3 | 3 | 0 | 3 | OK（半道不可见） |
  | after_trace_columns | 3 | 4 | 4 | 4 | 0 | 3 | OK |
  | after_data_flush | 3 | 4 | 4 | 4 | 0 | 3 | OK（数据已持久但未提交） |
  | after_checkpoint_write | 4 | 4 | 4 | 4 | 0 | 4 | OK（数据先持久） |
  | after_commit_flush | 4 | 4 | 4 | 4 | 0 | 4 | OK |
  | before_finalize | 3 | 3 | 3 | 3 | 0 | 3 | OK（lifecycle=writing） |
  | after_finalize_mark | 3 | 3 | 3 | 3 | 0 | 3 | OK（lifecycle=finalized，可重试） |
  | after_finalize_flush | 3 | 3 | 3 | 3 | 0 | 3 | OK |
  | before_rename | 3 | 3 | 3 | 3 | 0 | 3 | OK（partial 保留，可重试） |

  关键证据：`after_raw_write` 处 raw=4 而 idx/gnss=3、committed=3——**半道物理行存在但对 checkpoint 遵守者不可见**；`after_data_flush` 处数据完整持久而 checkpoint 未动；`after_checkpoint_write` 处 checkpoint 前进时 4 行全部完整可解码。所有 committed 行均通过 `schema.trace_metadata_from_cells` 解码成功。
- **问题或限制**：见 P2-1（测试套件本身无法证明两次 flush 生效，本条结论依赖审查者探针而非套件）与 P2-4（子进程崩溃模型的区分力为零）。

### ② 不兼容 sweep、重复冲突、磁盘/flush 失败不推进 checkpoint — **`PASS`**

- **代码证据**：
  - 冻结校验发生在任何写入之前：`_require_frozen_contract()` L761–793 由 L667 调用，早于 L710 的物理行分配。axis 不符 → `AXIS_MISMATCH`（L765–772）；channels 不符 → `CHANNEL_CONTRACT_MISMATCH`（L773–781）；config 摘要不符 → `CONFIG_DIGEST_MISMATCH`（L782–793）。
  - 重复（同 index 同 hash）→ L683–693 返回 `AppendDecision.DUPLICATE` 的幂等空操作，不写盘、不动 checkpoint。
  - 冲突（同 index 不同 hash / 同 uid 不同 index）→ L694–708 → `_record_conflict()` L837–870 抛出 `DomainError(ErrorCode.ID_CONFLICT)` 并保留 `TraceConflict` 证据；`append_trace` L649–650 对 `DomainError` 只重抛不中止，writer 保持可用。
  - 原始形状/dtype 不符 → L795–816 → `SHAPE_MISMATCH` / `DTYPE_MISMATCH`；外来 `mission_id` → L657–665 → `INVALID_ARGUMENT`。
  - 磁盘/rename 失败 → L914–915 的 `replace` 抛错后状态保持 `awaiting_rename`，可重试；`test_rename_failure_preserves_partial_and_can_be_retried` 覆盖。
- **审查者独立测试证据**：
  - **真实 ENOSPC flush 失败**（套件未覆盖，审查者用注入的 handle 包装器使 `h5.flush()` 抛 `OSError(28)`）：
    - `flush#1` 失败 → 抛出 `OSError`，writer `aborted`，**落盘 committed=2 而 raw/idx/gnss=3 → checkpoint 未推进**；
    - `flush#2` 失败 → 落盘 committed=3 且 4→3 行全部完整（数据已由 flush#1 持久）→ 不出现"checkpoint 指向不完整数据"。
  - 变异复验：关掉两处冲突守卫 → 2 例失败（与完成报告一致）；关掉 axis 冻结 → 2 例失败（一致）。
  - 冲突后重复追加仍返回 `DUPLICATE`，`physical_record_count` 保持 1（writer 可用性未被破坏）。
- **问题或限制**：P2-3（预置哈希冲突路径不产生 `conflicts` 证据）；套件内没有让 `h5.flush()` 真正失败的用例（"磁盘/flush 失败"目前只由相位钩子在 flush **之后**建模 + 审查者探针覆盖）。

### ③ finalized 文件不可继续 append，原 partial 不被无意覆盖 — **`PASS`**

- **代码证据**：
  - `append_trace` L646 `_require_open`；`flush()` L629 `_require_open`；`abort()` L967–972 已 finalized 时拒绝。finalized 后 append/abort/flush 全部抛 `DomainError(INVALID_ARGUMENT)`。
  - 两处"目标已存在"守卫：`_finalize_file()` L935–940（写终态标记前）、`close()` L908–913（rename 前）。
  - 原子改名：`LocalFileSystemFacade.replace()` L222–223 使用 `os.replace`（同卷原子）。
  - rename 失败不产生伪 finalized：L917 的 `FINALIZED` 只在 `replace` 成功之后设置。
- **审查者独立测试证据**：
  - 已存在终态文件时 `create()` + 写入 + `close()` → 抛 `DomainError(INVALID_ARGUMENT)`，旧终态文件字节仍为 `b'OLD FINAL'` 未被改写，partial 保留且 `lifecycle_state=writing`。
  - 相位 `before_rename` 崩溃后：partial 存在、`lifecycle_state=finalized`、`completion_kind=completed`、终态文件不存在——可恢复而非伪 finalized。
  - 4 种 `completion_kind`（completed / user_stopped / failed / crash_recovered）均已落盘并可通过 `probe_rcscan_v2` 读取。
  - `close()` 幂等：第二次、第三次调用返回同一 `FinalizeResult`，终态文件大小不变。
- **问题或限制**：P2-2（rename 前第二处守卫零覆盖）、P3-1（`exists()`+`os.replace` 非真正 TOCTOU 安全，撒谎门面实测可覆盖）。

### 附加核对（不属于三条验收标准，但为 ISSUE-010 范围/硬约束要求）

| 核对项 | 结论 | 证据 |
|---|---|---|
| 复用 ISSUE-008 schema/codec | PASS | 仅调用 `schema.create_rcscan_v2`（L477）、`schema.dataset_contracts`（L500）、`schema.trace_metadata_to_cells`（L719）；未自建 dtype/chunk/列编码 |
| 复用 ISSUE-009 哈希 | PASS | `compute_raw_trace_sha256`（L670）；测试用独立重算 `expected_hash()` 对拍，未取 writer 返回值自证 |
| 未引入 UI/网络依赖 | PASS | 模块导入仅 `os/pathlib/dataclasses/datetime/enum/typing` + `h5py` + `numpy` + `uav_gpr.core.*` + `uav_gpr.storage.rcscan_v2`（L68–89） |
| core 隔离未破坏 | PASS | `tests/unit/test_core_isolation.py` + `tests/unit/test_no_external_access.py` 4 passed（见第 6 节） |
| raw 不可变 | PASS | `_require_raw` L795–816 只校验并用 `np.ascontiguousarray(..., dtype="<c16")`，不原地改写输入；测试 `test_raw_data_is_stored_unmodified` + 审查者探针逐道 `np.array_equal` 比对通过 |
| 多通道 shape | PASS | 全程 `channel × frequency`（单道）与 `trace × channel × frequency`（连续）；`_require_raw` L803–809 强制 `(channel_count, frequency_points)`；`/frequency/raw` 落盘 shape `(3, 2, 16)` 经探针确认 |
| 未提前实现 ISSUE-011（reader/恢复） | PASS | 源码内无文件扫描、无 committed 视图读取、无缺道/排序迭代实现；"读者可见"判定只存在于**测试内** `read_committed_view()`（L381–418） |
| 未提前实现 ISSUE-012（partial 恢复） | PASS | `abort()` L961–975 只关闭句柄并保留 partial；无截取、无 recovered 产物、无报告生成；源码 L31 / L64 明确声明 out of scope |
| 错误码未不必要新增 | PASS | 使用的 9 个码（`AXIS_MISMATCH`/`CHANNEL_CONTRACT_MISMATCH`/`CONFIG_DIGEST_MISMATCH`/`ID_CONFLICT`/`INVALID_ARGUMENT`/`SHAPE_MISMATCH`/`DTYPE_MISMATCH`/`NON_FINITE_AXIS`/`NON_INCREASING_AXIS`）全部为 `core/errors.py` 既有成员；`core/` 与 `rcscan_v2.py` 本次零改动（git status 已证） |
| 架构/store-then-forward | PASS | 本模块只实现"本地可靠提交"这一半（L1–6 与 ADR-0004 一致）；无 ACK、无 outbox、无网络 |

---

## 5. Git 与交付检查

- **当前分支**：`feat/issue-010`（无上游跟踪）；`main` = `e852508`；共同祖先 = `e852508`。
- **本批提交**：**0**。HEAD == 基线 `ee41360`，无新增提交，因此不存在"一个提交混入多个 Issue"或"Issue 被拆碎"的问题。
- **reflog / 历史完整性**：仅执行只读 git 命令（`status` / `branch` / `log` / `diff` / `ls-files`），**未执行任何写操作**（无 commit / add / checkout / branch / reset / gc / merge / push / stash），亦未修改 `.venv` 与 `.agent-teams`。工作区此前"引用与对象被外部删除"的异常本次未复现。
- **未提交修改**：`docs/issues/M02_STORAGE.md`（1 行状态变更，属允许范围）。
- **未跟踪文件**：
  - 本 Issue 产物：`src/uav_gpr/storage/incremental_writer.py`、`tests/integration/test_incremental_writer.py`
  - 历史遗留（非本 Issue）：`.agent-teams/**`（见 P3-6）
- **范围外修改**：无。未发现对 `core/`、`rcscan_v2.py`、`tests/contract/`、既有测试的改动。
- **缓存/日志/实测数据**：改动集中不含 `__pycache__`、`.pytest_cache`、日志文件、实测 `.rcscan`、地图缓存、密钥或本地配置。门禁运行中出现的 `[safe-delete][SAFE_DELETE_FAIL_CLOSED]` 提示指向系统临时目录 `pytest-of-Administrator`，属沙箱环境行为，未在项目内留下文件。
- **契约与文档影响**：本 Issue 未改变公共 schema/协议/架构契约，无需新增 ADR；`docs/DATA_FORMAT.md` 未被改动（第 3 节的提交顺序已被本实现落实，无需变更）。
- **Issue 状态**：`M02_STORAGE.md` 现为 `In progress`。按 `docs/issues/README.md` 第 2 节定义，通过人工验收后应置为 `Done`（或审查期间置为 `Review`），该状态推进需由项目负责人执行，审查者未改动。
- **审查者自身产出**：**仅新增 `docs/reports/ISSUE_010_REVIEW_REPORT.md` 一个文件**。所有探针脚本与运行目录位于系统临时目录 `C:\Users\Administrator\AppData\Local\Temp\uav-gpr-review-010\`，项目内无残留。

---

## 6. 测试与验证结果

### 6.1 环境

| 项 | 实测值 |
|---|---|
| 解释器 | `.venv/Scripts/python.exe`，**Python 3.13.14**（完成报告写 3.13.12，见第 7 节） |
| 平台 | Windows 11 Enterprise LTSC 2024 |
| 关键包 | numpy 2.5.2 / pytest 8.4.2 / ruff 0.16.5 / mypy 1.20.2 / h5py 3.16.0；`uav-gpr` editable 安装 |
| WSL | 未使用（按约束禁用） |

### 6.2 复跑的命令与结果

| # | 命令（工作目录 `d:\博士任务\无人机软件\UAV-GPR`） | 退出码 | 结果 |
|---|---|---:|---|
| 1 | `.venv/Scripts/python.exe tools/quality/verify.py` | **0** | pytest 非硬件 **428 passed, 1 deselected**（429 收集）；ruff ok；mypy **Success: no issues found in 31 source files**；package import ok；最终 `[quality] all gates passed` |
| 2 | `.venv/Scripts/python.exe -m pytest tests/integration/test_incremental_writer.py -q -p no:cacheprovider` | **0** | **52 passed**（3.24 s；`--collect-only` 同为 52） |
| 3 | `.venv/Scripts/python.exe -m pytest tests/unit/test_core_isolation.py tests/unit/test_no_external_access.py -q` | **0** | **4 passed** |
| 4 | 探针 A：10 相位子进程硬崩溃矩阵（`probe_crash.py`） | 0 | 10/10 `committed <= 完整行数` 且 committed 行全部可解码 |
| 5 | 探针 B：绕过 writer 应急 flush 的真实掉电矩阵（`probe_raw_crash.py`） | 0 | 6/6 OK，与探针 A 状态逐项一致 |
| 6 | 探针 C：删除 flush#1 变异体上重跑探针 B | 0 | 6/6 OK，落盘状态与未变异一致（P2-1 证据） |
| 7 | 探针 D：真实 `h5.flush()` ENOSPC 失败（`FlushFails` 包装器） | 0 | flush#1 失败 → committed 保持 2；flush#2 失败 → committed 3 且行完整 |
| 8 | 探针 E：功能性边界（冲突证据 / 撒谎门面 / awaiting_rename / 终态已存在 / 乱序映射 / 地面角色 / 反查开销） | 0 | 见第 3 节与第 4 节各处引用 |
| 9 | 变异复验（在复制到临时目录的源码树上执行，未改项目文件） | — | 见 P2-1 / P2-2 表格：`drop_axis_freeze` 2 失败、冲突双守卫 2 失败、终态守卫（finalize 处）1 失败、flush 单删 0 失败、rename 处终态守卫 0 失败 |

- 被 deselect 的 1 例是既有的 `tests/hardware/test_hardware_sentinel.py` 硬件哨兵（`-m hardware` 未授权即跳过），与本 Issue 无关。
- 无失败、无 error、无 xfail、无 skip（测试文件内无 `xfail`/`skip`/`TODO`/`FIXME`）。

### 6.3 测试质量核对（52 例）

| 核对项 | 结论 |
|---|---|
| 正常 / 错误 / 取消-恢复路径 | 覆盖：正常 append（L622/642）、创建与冻结（L512/529/549）、四类终态（L1424）、显式 flush（L696）、abort 后不可写（L1517）、上下文管理器退出即 abort（L1536）、rename 失败重试（L1360）、finalize 各相位失败重试（L1303/1327） |
| 用 `sleep` 猜时序 | **无**。全文仅两处注释声明"no sleeps"；时钟一律用 `ManualClock`，子进程用 `subprocess.run(..., timeout=180)` 同步等待 |
| 删除/削弱断言 | 未发现。断言均为强等值比较（`==`、逐道哈希对拍、`np.array_equal`），未使用 `assertTrue`/宽松范围 |
| 吞异常 | 未发现。唯一的 `except`（L1142）是子进程崩溃入口，捕获后立即打印标记并 `os._exit`；`pytest.raises` 均带错误码断言 |
| 放置目录符合 `docs/TESTING.md` | 符合。第 1 节定义 `tests/integration` 为"多层组合与崩溃/重连流程"，本文件组合 storage + core codec + ISSUE-009 哈希 + 可注入文件系统，并在真实 HDF5 磁盘文件上做崩溃流程；`integration` 标记已在 `pyproject.toml` L56 注册，`--strict-markers` 下收集正常 |
| 独立性 | 52 例可单文件、可单条运行，无顺序依赖（审查者多次单跑与全量跑结果一致） |

---

## 7. 报告与事实差异

| # | 完成报告声明 | 审查核实 | 性质 |
|---|---|---|---|
| 1 | "变异验证（删 flush#1 → 5 例失败；删 flush#2 → 3 例）" | **整行删除**（flush + 相位播报）时复现为 5 / 3；**仅删除 `h5.flush()`** 时为 **0 / 0**。即该数字锁定的是相位顺序，不是 flush | 结论不可按字面成立 |
| 2 | "关冲突守卫 → 2 例" | 复现：同时去掉两处 `_record_conflict` 调用 → 2 例失败；只去掉同 index 不同 hash 那一处 → 1 例 | 一致（口径澄清） |
| 3 | "关 axis 冻结 → 2 例" | 复现为 2 例失败 | 一致 |
| 4 | "删不覆盖守卫 → 1 例" | 复现：删 `_finalize_file` 内守卫（L935）→ 1 例；删 `close()` 内 rename 前守卫（L908）→ **0 例**。即"两处守卫"只有一处被覆盖 | 部分成立（见 P2-2） |
| 5 | "6 个用子进程 `os._exit()` 硬崩溃建模掉电" | 建模不成立：`append_trace` L651–653 / `_finalize_file` L951–957 在异常传出前已 flush+close，`os._exit()` 不改变落盘状态；绕过应急 flush 的对照探针落盘状态与之一致 | 声明的覆盖强度不成立（见 P2-4） |
| 6 | "全量 428 passed / 1 deselected；ruff、mypy(31 files)、import 全绿" | 全部复现一致（见第 6.2 节） | 一致 |
| 7 | "Python 3.13.12" | 实测 **3.13.14** | 环境口径差异（不影响结论） |
| 8 | 测试路径写作 `tests/integration/test_integration/test_incremental_writer.py` | 实际为 `tests/integration/test_incremental_writer.py` | 笔误 |
| 9 | "写 raw+metadata+GNSS+hash" | 实际写 `/frequency/raw` + `trace_metadata_to_cells()` 产出的**全部** trace-major 列（含 GNSS 列、`/acquisition/*`、role 相关的 `/transport/*`）；哈希是其中一列而非独立写入 | 表述简化，语义等价 |
| 10 | "`.partial.rcscan` 未被无意覆盖" 措辞 | 实测被保护的是**终态** `<mission_id>.rcscan`；partial 在 rename 成功后按设计消失 | 措辞澄清 |

---

## 8. 剩余风险

1. **两次 flush 缺乏回归保护**（P2-1）。实现正确，但本环境 HDF5 写入即持久化，使 flush 无法通过行为测试观测。若将来更换 HDF5 配置/平台（如开启写缓存、改用网络文件系统或 Linux），缺少 flush 的行为后果会立即显现而测试无法报警。
2. **重命名失败的运维路径仍薄弱**（P2-2）。`awaiting_rename` 状态下目标文件在重试前出现的分支无测试；此时若守卫被绕过，将发生终态文件覆盖（`os.replace` 在 Windows 上无条件覆盖）。
3. **冲突证据链不完整**（P2-3）。预置哈希不一致的冲突会绕过 `conflicts` 记录，ISSUE-041/048 的地面 ingest 与审计工具若以 `writer.conflicts` 为唯一证据来源，将漏记这一类冲突。
4. **`awaiting_rename` 状态下 `abort()` 是静默空操作**（实测确认）。文件此时已带 `lifecycle_state=finalized` + `completion_kind`，仍叫 `.partial.rcscan`。这是合理的"已终态待改名"状态，但 ISSUE-012 的恢复工具必须显式识别它，且不能把它当成普通未完成任务。
5. **`create()` 不预检终态文件**（P3-3）。整任务写完后才在 finalize 被拒，数据滞留 partial 等待人工介入。
6. **性能外推**（P3-2）。`trace_index_at_record` 的 O(n) 反查在十万道规模会退化；M10/M12 的对账与联动若按行反查将成为瓶颈。
7. **本环境无法真正模拟未 flush 的掉电**（P2-4 附带结论）。因此"掉电后 checkpoint 不变式"目前主要依赖**提交顺序的逻辑正确性**与审查者探针，而非可重复的硬件级验证；M12 的"副本数据掉电/partial 恢复演练"仍需真实验证。
8. **`.agent-teams/` 未跟踪**（P3-6），存在误提交风险。

---

## 9. 合并建议

- **整批结论 `PASS WITH CONDITIONS`；ISSUE-010 可进入人工验收。**
- 建议**有条件合并**：合并前优先处理 P2-2（补一条 rename 重试期目标出现的测试，约 20 行）与 P2-3（让预置哈希冲突也走 `_record_conflict`，约 10 行）——两项均为低风险小改动，且直接对应"验收标准③"与 `AGENTS.md` 第 4 节的证据要求。
- P2-1、P2-4 属于测试强度与文档真实性问题，**不阻断合并**，但建议在合并同一批次内修正测试注释与完成报告口径（否则后续 Issue 会基于错误前提设计故障注入）。
- P3 各项建议纳入后续 Issue 或技术债清单，不在本轮修复。
- 不建议拆分合并；ISSUE-011（reader）与 ISSUE-012（partial 恢复）可以开工，二者依赖的 `committed_record_count` 语义、partial 保留语义与 4 种 `completion_kind` 落盘均已通过独立验证。ISSUE-012 需额外注意第 8 节第 4 条的 `awaiting_rename` 状态识别。
- 合并后由项目负责人将 `docs/issues/M02_STORAGE.md` 中 ISSUE-010 状态推进为 `Done`；审查者不执行该操作。

---

## 10. 最小修复清单

只列阻止合并或明确要求处理的问题；**审查者不执行任何修复**。

| 序号 | 等级 | 问题 | 位置 | 最小修复 |
|---|---|---|---|---|
| 1 | P2 | rename 前"目标已存在"守卫零测试覆盖 | `incremental_writer.py` L906–913；`tests/integration/test_incremental_writer.py` | 新增用例：注入失败 rename 门面 → `close()` 抛错 → 外部创建 `final_path` → 换回正常门面再 `close()`，断言抛 `DomainError(INVALID_ARGUMENT)` 且既有终态字节未变 |
| 2 | P2 | 预置哈希冲突不留 `TraceConflict` 证据 | `incremental_writer.py` L680（对照 L694–708、L837–870） | 使 `with_integrity` 抛出的 `ID_CONFLICT` 也经 `self._record_conflict(...)` 记录证据，并统一 context 键集（`trace_index`/`record_position`/`stored_hash`/`incoming_hash`/双方 `trace_uid`） |
| 3 | P2 | 测试注释与完成报告对 flush 变异/掉电建模的论断不成立 | `tests/integration/test_incremental_writer.py` L1102–1111、L1564–1592 | 修正注释为事实描述（writer 在异常传出前已应急 flush+close；本组用例的价值是跨进程验证不变式）；如需锁死 flush，改为对可注入门面/handle 断言 flush 调用次数与顺序 |
| 4 | P3 | `close()` 注释声称 TOCTOU-safe | `incremental_writer.py` L906–907 | 改注释为"best-effort 前置检查"，或改用 `os.link` + `os.unlink` 实现真正不覆盖的原子改名 |
| 5 | P3 | `trace_index_at_record` O(n) 反查 | `incremental_writer.py` L831–835、L595–604 | 增加 `position -> trace_index` 反向字典，写入时同步维护 |
| 6 | P3 | `create()` 不预检终态文件是否存在 | `incremental_writer.py` L474–476 | 建文件前用 `filesystem.exists(final_path)` 早期 fail-closed，或明确记录"终态冲突仅在 finalize 暴露" |
| 7 | P3 | 测试改写私有属性完成重试 | `tests/integration/test_incremental_writer.py` L1387 | 提供公开的重注入门面入口，避免白盒耦合 |
| 8 | P3 | `.agent-teams/` 未跟踪未忽略 | 工作区根 | 合并前确认不纳入提交；是否加入 `.gitignore` 由项目负责人决定 |

清理项（非修复）：本轮审查产生的探针脚本位于系统临时目录 `C:\Users\Administrator\AppData\Local\Temp\uav-gpr-review-010\`，项目内无残留，可随时删除。

---

*审查结束。审查者未修改任何实现、测试或 Git 状态，仅新增本报告文件，等待项目负责人决定修复、拆分或合并。*
