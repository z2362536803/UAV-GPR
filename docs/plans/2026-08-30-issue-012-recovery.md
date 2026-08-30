# ISSUE-012 实施计划与修复日志：partial 检查与非破坏恢复

日期：2026-08-30
执行者：DeepSeek Harness AgentTeams `uav-gpr-issue-012-recovery`（engineer，任务 t2）
基线件：[docs/reports/ISSUE_012_BASELINE_CONFIRMATION.md](../reports/ISSUE_012_BASELINE_CONFIRMATION.md)（t1 只读核查 + 实施契约）
目标 Issue：`docs/issues/M02_STORAGE.md` ISSUE-012（Planned → In progress → Review）
验收原文：M02_STORAGE.md L175–178 五条 + 提示词验收
环境：WSL Ubuntu 24.04 / Python 3.12.3（numpy 2.5.2 / h5py 3.16.0 / pytest / ruff / mypy），与 ISSUE-012 基线口径一致；命令在仓库根 `D:\博士任务\无人机软件\UAV-GPR`（WSL 视角 `/mnt/d/...`）执行。

## 1. 决策记录（本轮钉死的契约语义）

1. **恢复目标仅限崩溃中的 writing partial**：`inspect/plan/execute` 只接受文件名为 `*.partial.rcscan` 且 `lifecycle_state=writing` 的源文件；`finalized/recovered`（含 ISSUE-011 `rename_pending=True` 的已完成文件）→ plan `recoverable=False` + `blocked_reasons`（无需恢复，走 reader 读取）；未知版本/损坏 checkpoint 等 schema 级问题 → inspect 打开即 `DomainError` fail-closed（复用 ISSUE-011 reader 的严格校验）。
2. **物理行字节复制**：恢复复制物理行 `[0, committed_record_count)` 的**全部必需 trace-major 列与 `/frequency/raw` 原始单元格**（按列分块切片，不整文件驻留），**不重新解码、不重新计算 hash、不做缺道补全、不丢弃重复/冲突/缺 hash 行**——证据原样保留，恢复后由严格 reader 重新报告（与 ISSUE-011 复审 P3-3 移交口径一致）。
3. **recovered 文件属性**：新 `file_id`（按 role 生成 `AirFileId.new()`/`GroundFileId.new()`，可显式传入）、目标名 `<new_file_id>.rcscan`（与 `<mission_id>.rcscan` 天然不冲突）、`lifecycle_state=recovered`、`completion_kind=recovered`（M02 L166 原文）、`writer_version=恢复工具版本`、`ended_utc/checkpoint.updated_utc=恢复时刻`（可注入 clock）、`started_utc/created_utc` 与 mission/config/axis/channels/config_sha256 从源原样继承。新增 mission attrs provenance：`recovery_source_sha256`（源文件 SHA256）、`recovery_source_file_id`、`recovery_tool_version`——**附加属性**，ISSUE-008 probe/ISSUE-011 reader 对未知 attrs 透明容忍，不改变既有冻结属性语义。
4. **dry-run 默认**：`plan_recovery` 是唯一通向执行的入口，本身**绝不写文件**（断言目标不存在、源 SHA 不变）；`execute_recovery(plan)` 的调用即显式确认，无隐式执行路径。
5. **执行前重校验**：execute 时重算源 SHA256（与 plan 不符 → fail-closed 拒绝，防止 plan 后源文件被换）、重查目标不存在（已存在 → `DomainError(INVALID_ARGUMENT)`，绝不覆盖）。
6. **失败清理**：execute 全程有 `RecoveryPhase` 故障注入 seam（与 ISSUE-010 `PhaseFaultHook` 同模式）；任一步失败 → 关闭已开句柄、**best-effort 删除目标文件**、抛错；删除也失败 → 抛显式错误并带残留路径（fail-closed，且残留文件从未写入 finalized 标记，不构成"伪 finalized"）。恢复中断后重试是安全的（源未动、目标已被清理）。
7. **可选 processed 组**：源 partial 中存在可选组（`time_base/time_processed/frequency/calibrated`）→ plan `recoverable=False`（ISSUE-012 复制范围是 ISSUE-010 writer 的必需列集合，静默丢弃处理数据不可接受；ISSUE-010 writer 从不创建可选组，属防御性 fail-closed）。
8. **不抽公共函数、不改既有模块**：`rcscan_v2.py`/`incremental_writer.py`/`rcscan_reader.py`/`core/**` 零改动（ISSUE-011 复审 P3-1 镜像问题继续移交，本 Issue 只复用 reader 打开校验，不新增第三份镜像）。
9. **报告确定性**：`InspectReport`/`RecoveryPlan`/`RecoveryResult` 均为 frozen dataclass + `to_dict()`（稳定键序、JSON-safe）；时间一律走可注入 `Clock`（测试用 `ManualClock`）；同输入两次 inspect 输出逐字节一致。

## 2. 范围与排除项

**范围内**：
- `src/uav_gpr/storage/partial_recovery.py`（新增，API：`inspect_partial` / `plan_recovery` / `execute_recovery` + `InspectReport`/`RecoveryPlan`/`RecoveryResult`/`RecoveryPhase`/`RecoveryFaultHook`/`LocalRecoveryFileSystem`）；
- `tests/integration/test_partial_recovery.py`（新增，失败测试先行）；
- `docs/issues/M02_STORAGE.md`（ISSUE-012 状态行 `Planned → In progress`，完成后 `→ Review`）；
- 本计划文档 + 基线确认单（t1 交付）。
- `docs/DATA_FORMAT.md`：**任务契约列为 out-of-scope，本 Issue 不改动**；已实现的冻结契约条文草案写入本计划第 9 节附录 A，由负责人人工验收/合并时决定是否入文（与 ISSUE-011 复审 P3-2 同类决策）。

**范围外**：`core/**`、`rcscan_v2.py`、`incremental_writer.py`、`rcscan_reader.py`、`docs/adr/**`、`tools/**`、既有 `tests/**` 文件（零改动）、`tests/hardware/**`、两个参考项目；不原地 truncate/修复/删除源 partial、不自动删除任何文件、不做 GUI；不 commit/push/merge（本地分支 `feat/issue-012` 自 `main @ db95817` 创建，0 提交）；不实现 ISSUE-013 迁移/ISSUE-014 inventory/处理算法。

## 3. 设计（t2 最小实现骨架）

```text
inspect_partial(path) -> InspectReport
  RcScanReader(path) 打开（严格校验 fail-closed）-> validation_report()
  + 读取 mission attrs / checkpoint / 各必需列与 raw 长度 / physical_record_count
  + 半写尾部行数 = physical - committed；可选组存在性清单
  + 源文件 SHA256（流式分块，不整文件驻留）
  + to_dict() 确定性序列化（审计报告载体）

plan_recovery(path, *, new_file_id=None, target_dir=None, clock=None) -> RecoveryPlan
  只读检查（不写任何文件）：
  - inspect 结果摘要；文件名/生命周期/可选组/目标存在性 -> blocked_reasons
  - new_file_id 缺省按 role 生成；target = (target_dir or path.parent) / f"{new_file_id}.rcscan"
  - 数据级问题（缺道/重复/冲突/缺 hash/未解码行）-> warnings（不阻断，恢复原样保留）

execute_recovery(plan, *, clock=None, fault_hook=None, filesystem=None) -> RecoveryResult
  1. 重校验源存在 + 源 SHA256 == plan.source_sha256 + 生命周期 writing（fail-closed）
  2. 目标不存在检查（fail-closed，绝不覆盖）
  3. schema.create_rcscan_v2(目标, 新 file_id, 同 mission/config/axis/channels,
     created_utc=源 created_utc, writer_version=RECOVERY_COMPONENT_VERSION)
  4. 打开目标 "r+"；按列分块复制 [0, committed) 的必需列与 /frequency/raw
  5. checkpoint：committed_record_count / last_trace_index（继承源）/ updated_utc（恢复时刻）
  6. mission attrs：started_utc（继承源）、ended_utc（恢复时刻）、completion_kind="recovered"、
     recovery_source_sha256 / recovery_source_file_id / recovery_tool_version
  7. 根 attrs：lifecycle_state="recovered"
  8. flush + close；用 RcScanReader 复验（schema 级必须通过，committed 一致）
  9. 任一步异常 -> 关闭句柄 + best-effort 删除目标 + 抛错（RecoveryPhase 注入点见下）

RecoveryPhase：BEFORE_TARGET_CREATE / AFTER_TARGET_CREATE / AFTER_ROW_COPY /
  AFTER_CHECKPOINT_WRITE / BEFORE_FINAL_MARK / AFTER_FINAL_MARK / BEFORE_RENAME
```

## 4. 测试矩阵（`tests/integration/test_partial_recovery.py`，先失败后实现）

夹具复用 ISSUE-010/011 测试同构常量（2 通道、16 频点、`ManualClock`、`_MISSION_ID` 等）与 `PhaseFaultHook` 各故障点制造崩溃 partial；大文件用 ISSUE-011 同款 bulk 构建器思路（本文件内独立实现，不 import 测试模块）。

| # | 用例 | 对应验收/提示词 |
|---|---|---|
| 1 | **ISSUE-010 全 10 相位崩溃矩阵**（BEFORE_RAW_WRITE/AFTER_RAW_WRITE/AFTER_TRACE_COLUMNS/AFTER_DATA_FLUSH/AFTER_CHECKPOINT_WRITE/AFTER_COMMIT_FLUSH + close 期 BEFORE_FINALIZE/AFTER_FINALIZE_MARK/AFTER_FINALIZE_FLUSH/BEFORE_RENAME）+ **真实 ENOSPC**（flush 级 OSError(28)：data-flush 丢提交留尾 / commit-flush 文件内已提交）+ 空 partial → `inspect_partial` 生成稳定报告（两次调用 `to_dict()` 一致；committed/列长度/尾部行数/lifecycle 正确） | 任意写入故障夹具都能生成稳定报告 |
| 2 | dry-run 默认：`plan_recovery` 后目标不存在、源 SHA256 不变；`inspect/plan` 全程源字节不变（前后 SHA256 对拍） | 未经确认只 dry-run；源字节不变 |
| 3 | 恢复往返：`execute_recovery(plan)` → 目标存在且为 `*.rcscan`；严格 `RcScanReader` 打开通过；committed 一致；物理视图与源 committed 行逐道对拍（raw 数值、hash_verified、trace_index/uid、GNSS）；逻辑视图排序正确；`lifecycle_state=recovered`、`completion_kind=recovered`、新 file_id ≠ 源 file_id、provenance attrs 正确；`rename_pending=False` | 恢复文件可被严格 reader 读取 |
| 4 | 半写尾部：AFTER_RAW_WRITE 崩溃（raw 列长于 committed）→ 恢复只复制 committed 行，目标不含尾部行 | 复制最后完整提交点 |
| 5 | 目标已存在 → `execute_recovery` fail-closed（`INVALID_ARGUMENT`），源与既有目标均不变；plan 后源文件被改 → execute 拒绝 | 目标存在保护 |
| 6 | 恢复过程失败：每个 `RecoveryPhase` 注入点 → 抛错、目标被删除（`not exists`）、源 SHA256 不变；随后无注入重试成功 | 恢复失败不留下伪 finalized 文件 |
| 7 | 删除目标也失败（filesystem seam 注入）→ 显式错误含残留路径；残留文件恒为 partial 命名、无 finalized 标记 | 同上（fail-closed） |
| 8 | 源非 partial 文件名 / lifecycle=finalized / lifecycle=recovered / awaiting_rename（撒谎门面 rename 失败）→ plan `recoverable=False` 且不写文件；execute 拒绝 | 只恢复崩溃 partial；awaiting_rename 显式分类 |
| 9 | 未知 schema 版本 / 损坏 checkpoint / 列短于 checkpoint → inspect 抛 `DomainError` | fail-closed |
| 10 | ground 无 `/transport` 的 partial → 恢复成功，目标无 transport 组且 reader 可读 | role 差异 |
| 11 | 乱序物理行 + 重复同 hash + 冲突不同 hash + 缺道 + 缺 GNSS/缺存储 hash 行 → 恢复原样保留；目标 reader 重新报告相同分类（重复/冲突/缺道/issue 计数一致） | 证据保留、确定性 |
| 12 | 大合成文件（≥2000 行）→ 分块复制正确、目标 reader 分块读取一致（内存有界） | 大文件 |
| 13 | 可选 processed 组存在（按冻结契约手工添加合法组）→ plan `recoverable=False` | 防御性 fail-closed |
| 14 | `plan_recovery` 显式传 `new_file_id`/`target_dir` → 目标路径与 file_id 按传入值；`clock` 注入 → recovered 时间确定；role 不匹配的 file_id → 拒绝 | API 契约 |

## 5. 门禁复跑清单（t2 完成时必须全绿）

```text
.venv/Scripts/python.exe -m pytest tests/integration/test_partial_recovery.py -q
.venv/Scripts/python.exe -m pytest tests/contract/test_rcscan_reader.py tests/contract/test_storage_schema.py \
    tests/contract/test_raw_trace_hash.py tests/integration/test_incremental_writer.py -q   # 既有 232 无回归
.venv/Scripts/python.exe tools/quality/verify.py        # 全量非硬件 pytest + ruff + mypy strict + import
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m mypy src
git diff --check                       # 无空白错误
git status --porcelain=v1 -b           # 仅范围内文件（见第 8.3 节事实记录）
```

## 6. 验收逐项对应（M02 L175–178 五条）

1. **任意写入故障夹具都能生成稳定报告** → 测试 #1（每个 WritePhase 故障点 + 报告确定性）；
2. **恢复文件可被严格 reader 读取** → 测试 #3/#4/#11/#12（RcScanReader 打开 + 视图对拍 + 分类一致）；
3. **原 partial 字节不变** → 测试 #2/#5/#6（inspect/plan/execute/失败全路径 SHA256 对拍）；
4. **恢复失败不留下伪 finalized 文件** → 测试 #6/#7（目标删除 + 残留文件无 finalized 标记）；
5. **未经确认只 dry-run** → 测试 #2（plan 不写文件；execute 为唯一显式执行入口）。

## 7. 未完成 / 风险

- **未完成**：ISSUE-013 v1 迁移、ISSUE-014 inventory（明确范围外）；不抽 `_validate_present_dataset` 公共函数（ISSUE-011 P3-1 继续移交）。
- **风险 1**：恢复文件新增 3 个 mission attrs（provenance）为附加属性扩展——ISSUE-008 probe/ISSUE-011 reader 容忍未知 attrs（实测语义），但需在 DATA_FORMAT 4.1 节与复审报告中明示，避免未来 schema 冻结校验误伤。
- **风险 2**：vlen/ASCII 列在 h5py 读-写往返中的字节保真（同 dtype 复制，契约测试 #3 逐道对拍钉死）。
- **风险 3**：恢复对象是"崩溃 partial"，真实掉电场景的 HDF5 文件本身可能无法被 h5py 打开（文件级损坏）——inspect 对不可打开文件 fail-closed（DomainError），与 ISSUE-010 的 flush 持久化保证（M12 掉电演练）衔接，不在本 Issue 制造伪恢复。

## 8. 执行记录（t2 实测证据，2026-08-30）

### 8.1 红灯 → 绿灯

```text
$ python3 -m pytest tests/integration/test_partial_recovery.py -q -p no:cacheprovider
ERROR tests/integration/test_partial_recovery.py
E   ModuleNotFoundError: No module named 'uav_gpr.storage.partial_recovery'   # 红：模块不存在，全量收集失败

实现 src/uav_gpr/storage/partial_recovery.py 后首轮：
1st run: 27 passed, 2 failed
  # 修复 1：BEFORE_FINALIZE 故障发生在 close()（非 append），文件 committed=已追加行数（2 而非 3）；
  # 修复 2：AFTER_COMMIT_FLUSH 故障发生在提交 flush 之后，故障行已在文件中提交——乱序用例改用
  #         AFTER_TRACE_COLUMNS 制造"最后完整提交点"语义（该语义差异已写入测试注释钉死）。
2nd run: 29 passed

按任务契约验收第 1 条扩展到 ISSUE-010 全 10 相位崩溃矩阵 + 真实 ENOSPC + 撒谎门面：
3rd run: 37 passed    # +10 相位参数化（含 4 个 close 期故障 → awaiting_rename 分类）+ ENOSPC flush 夹具 + 撒谎门面
```

### 8.2 门禁数字（最终复跑，Windows .venv Python 3.13.14 = 任务契约 verify 命令）

```text
$ .venv/Scripts/python.exe -m pytest tests/integration/test_partial_recovery.py -q
37 passed in 5.01s

$ .venv/Scripts/python.exe -m pytest tests/contract/test_rcscan_reader.py tests/contract/test_storage_schema.py \
    tests/contract/test_raw_trace_hash.py tests/integration/test_incremental_writer.py -q
232 passed in 11.00s                          # 既有依赖契约/集成（39+59+75+59）零回归

$ .venv/Scripts/python.exe tools/quality/verify.py
511 passed, 1 deselected in 25.89s            # 474 基线 + 37 新恢复测试
[quality] ruff      ok   All checks passed!
[quality] mypy      ok   Success: no issues found in 33 source files（新增 partial_recovery.py）
[quality] package import ok
[quality] all gates passed

$ .venv/Scripts/python.exe -m ruff check src tests   # All checks passed!
$ .venv/Scripts/python.exe -m mypy src               # Success: no issues found in 33 source files
$ git diff --check                                   # 干净
$ git status --porcelain=v1 -b                       # 见 8.3
```

新测试/新模块无 xfail/skip/TODO/FIXME/sleep（grep 实测命中 0）。

### 8.3 工作树事实

```text
## feat/issue-012            # 本地分支，自 main @ db95817 创建，0 提交
?? docs/plans/2026-08-30-issue-012-recovery.md
?? docs/reports/ISSUE_012_BASELINE_CONFIRMATION.md
?? src/uav_gpr/storage/partial_recovery.py
?? tests/integration/test_partial_recovery.py
 M docs/issues/M02_STORAGE.md               # 仅状态行 1 行（Planned → Review）
```

未 commit、未 push、未 merge；`rcscan_v2.py/incremental_writer.py/rcscan_reader.py/core/**/adr/**/tools/**/docs/DATA_FORMAT.md` 零改动；测试无残留缓存（`__pycache__`/`.pytest_cache` 已忽略）。

### 8.4 测试矩阵 → 用例映射（14 项全落地，37 用例）

| # | 测试函数 | 结果 |
|---|---|---|
| 1 | `test_inspect_report_is_deterministic_across_writer_faults`（**ISSUE-010 全 10 相位**参数化，含 4 个 close 期故障→finalized 分类）+ `test_inspect_is_stable_for_real_enospc_flush_fixtures`（真实 ENOSPC：data-flush/commit-flush）+ `test_inspect_on_empty_partial_reports_zero_committed` | 37/37 通过 |
| 2 | `test_plan_is_dry_run_and_never_writes` + `test_plan_blocks_non_writing_and_non_partial_sources_without_writing` | 通过 |
| 3 | `test_recovery_roundtrip_matches_source_committed_rows` + `test_ground_partial_without_transport_recovers` + `test_empty_partial_recovers_to_empty_recovered_file` | 通过 |
| 4 | `test_half_written_tail_is_not_copied` | 通过 |
| 5 | `test_execute_fails_closed_on_target_collision_and_source_change` + `test_plan_blocks_when_target_already_exists` | 通过 |
| 6 | `test_mid_recovery_failure_cleans_target_and_allows_retry`（7 个 RecoveryPhase 参数化） | 通过 |
| 7 | `test_cleanup_remove_failure_leaves_only_partial_named_remnant` + `test_cleanup_failure_after_final_mark_still_leaves_partial_named_remnant` | 通过 |
| 8 | `test_plan_blocks_non_writing_and_non_partial_sources_without_writing`（finalized/rename-pending/misnamed） | 通过 |
| 9 | `test_inspect_fails_closed_on_unknown_version_and_bad_checkpoint` | 通过 |
| 10 | `test_ground_partial_without_transport_recovers` | 通过 |
| 11 | `test_duplicates_conflicts_and_missing_hash_are_preserved_verbatim` + `test_out_of_order_physical_rows_survive_recovery_in_commit_order` | 通过 |
| 12 | `test_large_file_recovery_is_correct_and_bounded`（2000 行、32 块、块 ≤64） | 通过 |
| 13 | `test_plan_honours_explicit_target_dir_and_optional_groups_block` | 通过 |
| 14 | `test_recovered_target_naming_and_explicit_file_id` + `test_plan_rejects_wrong_role_file_id` | 通过 |
| 附加 | `test_awaiting_rename_partial_is_classified_as_completed_not_unfinished`（撒谎门面 `_FlakyRenameFacade`：reader.rename_pending=True、inspect lifecycle=finalized、plan blocked、execute 拒绝、源字节不变） | 通过 |

## 9. 附录 A：建议入文 `docs/DATA_FORMAT.md` 4.1 的冻结契约草案（合并时由负责人决定）

t2 任务契约将 `docs/DATA_FORMAT.md` 列为 out-of-scope，故本 Issue 未改动该文件；以下为已实现并被 37 项测试钉死的契约条文草案，建议负责人人工验收/合并时决定是否入文（与 ISSUE-011 复审 P3-2 同类决策）：

```text
### 4.1 非破坏恢复（ISSUE-012 冻结）

权威实现：src/uav_gpr/storage/partial_recovery.py；契约测试：tests/integration/test_partial_recovery.py。
恢复对象只限崩溃中的 writing 生命周期 *.partial.rcscan；finalized/recovered（含 ISSUE-011
rename_pending=True 的已完成文件）不进入恢复流程。

- 只读检查：inspect_partial(path) 返回结构化 InspectReport（确定性、可序列化）：schema/mission/
  checkpoint 事实、全部必需 trace-major 列与 /frequency/raw 长度、physical_record_count（最短耐序列，
  同 ISSUE-011 reader 口径）、半写尾部行数（最长列 − checkpoint）、ISSUE-011 校验分类与源文件 SHA256。
  schema 级问题（未知版本、损坏 checkpoint、列短于 checkpoint、非 HDF5）fail-closed 抛 DomainError。
- dry-run 计划：plan_recovery(path) 默认 dry-run、绝不写文件；决策新 file_id（按 role 生成
  AirFileId.new()/GroundFileId.new()，可显式传入）与目标 <new_file_id>.rcscan；非 partial 文件名、
  生命周期非 writing、可选 processed 组存在、目标已存在 → recoverable=False + blocked_reasons；
  数据级问题（缺道/重复/冲突/缺存储 hash 行）→ warnings（恢复原样保留并重新报告，不静默丢弃）。
- 显式执行：execute_recovery(plan) 的调用即确认，是唯一写路径。执行前重校验源 SHA256（与计划不符
  拒绝）与目标不存在（已存在拒绝，绝不覆盖）；按物理行字节复制 [0, committed_record_count) 的全部
  必需列与 raw（分块、有界内存），不重新解码、不重新计算 hash。
- recovered 文件属性：新 file_id、lifecycle_state=recovered、completion_kind=recovered、
  writer_version=恢复工具版本（当前 issue012.1）、ended_utc/checkpoint updated_utc=恢复时刻（可注入
  clock）、started_utc/created_utc 与 mission/config/axis/channels/config_sha256 从源原样继承；
  mission attrs 附加恢复 provenance（对 ISSUE-008 probe/ISSUE-011 reader 透明，不改既有冻结属性语义）：
  recovery_source_sha256（源 partial 文件 SHA256）、recovery_source_file_id（源 partial 的 file_id）、
  recovery_tool_version（恢复工具组件版本）。
- 发布前验证：recovered 文件先以 *.partial.rcscan 暂存名写入，经严格 RcScanReader 验证
  （lifecycle/committed 一致）后才原子改名为最终 .rcscan。
- 失败清理：任一步失败 → 关闭句柄并 best-effort 删除暂存文件，绝不留下看似 finalized 的结果；
  删除也失败 → 显式错误携带残留路径，且残留文件恒为 partial 命名（永不伪装成最终 .rcscan）。
  恢复中断后重试安全（源未动、暂存已清理）。
- 源文件保证：inspect/plan/execute 全程源文件只以 "r" 打开，字节不变（测试以 SHA256 对拍钉死）。
- 排除项：不原地 truncate/修复/删除源 partial、不自动删除任何文件、不做 GUI；可选 processed 组
  不在复制范围（存在即拒绝恢复，fail-closed）。
```
