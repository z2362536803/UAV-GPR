# ISSUE-033 独立复审报告（t3 · 自动化轮）

- **审查者**：AgentTeams `uav-gpr-issue-033-bg-subtraction` 成员 reviewer（只读审查器）
- **审查日期**：2026-09-05
- **审查对象**：t2 交付的 ISSUE-033「空采背景处理阶段与数据域保护」实现
- **审查标准**：`docs/ISSUE_REVIEW_STANDARD.md` v1.0（§13 固定十节格式）
- **审查基线**：`main @ 4cec913`（与 t1 基线单一致）；工作树改动 = t2 inScope 4 路径（未提交，按流程等待复审后合并）

## 1. 审查结论

**PASS** —— 无 P0/P1/P2 问题；无阻止合并的验收缺陷。四项 P3 观察（不阻止本次合并）列于第 3 节，均带修复方向。

t2 交付的 `AirBackgroundSubtractionStage` 及其 42 项契约测试、计划文档、M06 状态行更新全部真实、完整、合规；声称的全部测试数字（定向 42 / 相关回归 204 / 全量 1291 passed + 4 deselected / ruff / mypy(53) / import / diff-check）由本审查者独立复现，逐项一致；35 项独立反例/变异探针（含工程师测试未覆盖的构造期/apply 期两级校验路径、跨层 digest 编码对齐、`check_safe_reuse` 函数级语义）全部按契约预期行为通过。计划 §7 偏差 2（OSL 域参考构造期强制 `current_calibration`，fail-closed）经独立判断为正确且必要的收紧（见 §7 分析）。

## 2. 自动识别的审查范围

- **Issue**：ISSUE-033（`docs/issues/M06_CALIBRATION_PROCESSING.md` L227-262）；状态行 L229 = `Review`（t2 更新，diff 实测仅此 1 行）
- **依赖**：ISSUE-029（`fb758fe`）、030（`89fd9bb`）、032（`4e5349e`）均 Done（M06 状态行 + 合并提交在 HEAD 历史）
- **t2 声称改动**（与 git 实测逐一相等）：
  | 路径 | 状态 |
  |---|---|
  | `src/uav_gpr/processing/background_subtraction.py` | 新增（800 行，untracked） |
  | `tests/contract/test_processing_background_subtraction.py` | 新增（885 行，untracked） |
  | `docs/plans/2026-09-05-issue-033-bg-subtraction.md` | 新增（80 行，untracked） |
  | `docs/issues/M06_CALIBRATION_PROCESSING.md` | modified（仅 L229 状态行，+1/-1） |
- **不计入 t2 的并存改动**：`docs/reports/ISSUE_033_BASELINE_CONFIRMATION.md`（t1 交付，t1 changedPath）——与 t2 计划 §7 diff-check 口径一致
- **必读资料**（全部完整阅读）：AGENTS.md、CLAUDE.md、`docs/issues/README.md`、M06 ISSUE-033 条目、`docs/CALIBRATION.md`（§4/§5/§8/§9）、`docs/PROCESSING.md`（§1/§2）、`docs/ISSUE_REVIEW_STANDARD.md`、t1 基线单、t2 计划（D1-D9 + §7 执行日志）；实现与依赖模块（`background_subtraction.py`、`reference.py` L532-653、`osl_calibration.py`、`bandpass.py`、`core/time_domain.py`、`core/identifiers.py`、`core/frequency.py`、`storage/calibration_files.py` L126-274/L819-935）
- **Git 检查**：HEAD `main@4cec913`；`origin/main...HEAD` = `0 0`（t1 时为 0 3 ahead，captain 的 032 推送重试已完成，远端同步）；reflog 全序列为 checkout/commit/merge，无 reset/rebase/amend；`git diff --check` clean

## 3. 主要问题（P0 → P3 排序）

无 P0 / P1 / P2 问题。P3 观察四项（均不阻止合并，不构成验收失败）：

### P3-1 digest 跨层口径差异：`background_reference_digest` ≠ `.rcbg` 存储侧 `content_sha256`

- **位置**：`src/uav_gpr/processing/background_subtraction.py` L174-208（`_reference_payload`）对照 `src/uav_gpr/storage/calibration_files.py` L898-935（`AirBackgroundFilePayload.to_document`）
- **触发条件**：审计者拿 record 里的 `mean_content_sha256` 去比对 `.rcbg` 文件的 `content_sha256`
- **实际影响**：两个 digest 覆盖域不同（033 侧多 `format`/`trace_count` 顶层、少 provenance/quality 域），数值必然不等；但数组与通道编码节点逐字节一致（探针 I4 验证：`_encode_array`/`_channel_to_dict`/`_canonical` 与 storage codec 同构），docstring 已用「content domain」限定口径，golden 测试钉死格式。跨层互验需要知道口径不同。
- **违反要求**：无硬性违反（M06 未要求 digest 与存储层互验；t1 §3.4 已预告 digest 为 stage 参数审计项）
- **最小修复方向**：后续 Issue（036 编排或存储衔接）在文档/ADR 登记 processing 层 digest 与存储层 digest 的各自适用域；或统一字段集后互验。

### P3-2 `check_safe_reuse` 对缺 `channel_id` 的 provenance entry 宽容

- **位置**：`background_subtraction.py` L345-346（`channel_id = channel_node if isinstance(channel_node, str) else None`，None 时跳过通道绑定核对，仅凭 digest 判定）
- **触发条件**：history 末记录 `parameters["profiles"]` 的 entry 含 `profile_id`+`content_sha256` 但缺 `channel_id`（032 生产端 `OslProfileProvenance.from_profile` 总是写全四字段，正常路径不会出现；仅手工/未来写入器）
- **实际影响**：极小——032 的 `from_json` 对缺字段 entry 返回 None（严格拒），033 的解析较宽容，语义略弱于 032 同名检查的严格度
- **违反要求**：无验收违反（digest+ID 双匹配仍然强制）
- **最小修复方向**：`channel_id` 缺失时按「strict provenance unavailable」返回 incompatible，与 032 对齐。

### P3-3 digest 路径的 `_encode_array` 静默 dtype 升位

- **位置**：`background_subtraction.py` L137-139（`arr = np.asarray(arr, dtype=expected)`）
- **触发条件**：绕过 stage 构造（其 L425-431 已强制 complex128）直接调用公开函数 `background_reference_digest` 且传入 float64 `mean_data`
- **实际影响**：公开函数独立使用时 dtype 语义依赖调用方自律（stage 路径不可绕）；与 032 同名先例代码（`osl_calibration.py` L119-124）行为完全一致，属沿袭而非新增弱化
- **最小修复方向**：`_encode_array` 遇 dtype 不匹配直接拒绝（与 032 一并调整，单独改 033 会破坏两模块间的一致性）。

### P3-4 raw 域 reference 携带 `current_calibration` 时静默接受

- **位置**：`background_subtraction.py` L485-506（仅 OSL 域检查 live set 必需性，raw 域+live set 不拒也不警告）
- **触发条件**：构造 raw 域 stage 时误传 `current_calibration`
- **实际影响**：无行为影响（raw 路径完全不消费 live set，探针 J3 验证应用干净），仅 API 卫生问题
- **最小修复方向**：raw 域收到 live set 时抛 `INVALID_ARGUMENT`（防调用方误配置）。

### 计划 §7 偏差 2 的独立判断（captain 要求项）

「OSL 域参考构造期强制 live `current_calibration`（kind=`missing_current_calibration` fail-closed）」**正确且必要**：

1. D4 第三道门要求「末记录 `content_sha256` == 当前绑定 profile 的重算 digest」——重算必须有 profile 对象，其唯一权威来源就是产生该数据的 `OslCalibrationSet`。无 live set 时实现只有两条路：静默退化为 ID-only 匹配（违反 M06「校准域要求 profile ID/digest 相同」的双匹配验收），或 fail-closed。构造期拒绝是唯一把验收做实的路径。
2. 收紧而非放松：captain 口径的 D4 隐含「当前绑定 profile」存在；偏差 2 只是把它显化为构造期必填参数，并给结构化错误 kind。
3. 组合自洽：stage 级强制（合并门禁必须严格）+ `check_safe_reuse` 函数级可选（None → incompatible，业务错配永不抛，CALIBRATION §6 纪律）与 032 先例一致。
4. 影响评估：校准域背景减除的调用方必须持有产生该数据的校准集（回放场景需从 `.rcal` 重建）——这正是「严格 provenance」的本意，与 PROCESSING §1「从已验证 calibrated 快照开始需要独立 anchor（当前不允许）」的精神同向。不构成可用性缺陷，记为设计选择。

## 4. 逐 Issue 验收矩阵（M06 L248-252）

| 验收标准 | 状态 | 代码证据 | 测试/探针证据 | 问题或限制 |
|---|---|---|---|---|
| raw reference 不能用于 calibrated 数据，反之亦然 | **PASS** | `background_subtraction.py` L112-115（`_REFERENCE_DOMAIN_OF` 双射）、L603-618（前驱域门 `{RAW, CALIBRATED}`）、L619-632（reference 域 ↔ 数据域错配拒 `PROCESSING_DOMAIN_MISMATCH`）、L492-506（OSL 参考+无 live set 构造拒） | 工程师测试 `test_raw_reference_on_calibrated_data_rejected`（L257）、`test_calibrated_reference_on_raw_data_rejected`（L269）、`test_osl_reference_without_live_calibration_construction_rejected`（L278）、`test_background_applied_predecessor_rejected`（L289）；探针 A1-A4 | 无 |
| 多通道顺序和 profile 错配拒绝 | **PASS** | 通道：L727-750（`source.channels != tuple(reference.channels)` 精确序全等 → `CHANNEL_CONTRACT_MISMATCH`，context 含左右 channel_id 列表与首差位点）；轴：L751-760（`np.array_equal` → `AXIS_MISMATCH`）；shape：L761-773（`(channels, axis)` → `SHAPE_MISMATCH`）；profile：`check_safe_reuse` L247-373（ID 严格相等 L302 + recorded digest == live 重算 digest L366-372 + legacy 缺 digest 拒 L316-323/L338-344）+ L643-646（calibrated 输入强制调用） | 工程师测试 `test_swapped_reference_channel_order_rejected`（L314）、`test_missing_and_extra_channel_rejected`（L325）、`test_dual_channel_rows_subtract_independently`（L335，行绑定负向钉死）、`test_calibrated_different_profile_id_rejected`（L364）、`test_calibrated_reference_without_profile_id_rejected`（L375）、`test_calibrated_legacy_record_without_digest_rejected`（L389）、`test_same_id_different_profile_content_rejected`（L416）；探针 B1/C1/C2/A4 | 无 |
| 数值/history 对拍且与 Flat 明确区分 | **PASS** | 数值：L648-685（单一广播减法 `source.data - mean_data`，scan 沿 trace 广播同一 2-D 参考，无任何 trace 轴统计）；history：L687-698（record 经 core `ProcessingRecord` 直构，`background_reference_id` 必带（core `_validate_references` 兜底，`time_domain.py` L214-226）、calibrated 场景显式继承 `calibration_profile_id` + `calibration_profile_content_sha256` L658-667）、输出先重建后 append（L669-685 半成品防护）；参数结构 L544-570（algorithm/reference 节点含 axis/mean digest、channel 有序表、trace_count） | 工程师测试 `test_golden_small_vector_subtraction`（L591，手算 bit 级）、`test_scan_broadcast_matches_per_trace_sweeps`（L613，广播==逐 trace stack）、`test_history_record_fields_and_round_trip`（L645，字段全检+JSON 往返）、`test_calibrated_record_inherits_calibration_profile_id`（L674）、`test_digest_is_canonical_and_content_sensitive`（L689，golden digest 字面量+独立 canonical 重算）、`test_single_trace_and_multi_trace_share_one_reference_semantics`（L797，DC 不被沿 trace 平均掉）、`test_downstream_bandpass_chain_accepts_background_output`（L830）、`test_module_source_contains_no_excluded_symbols`（L840，AST 级排除守卫：无 storage/acquisition/Qt/FFT/trace 统计符号）；探针 G1/G2/H1-H3 | 无 |
| （范围项）输入不可变 | **PASS** | L672-685（输出经 core 模型重建为 never-writable 快照；输入全程只读消费） | 工程师测试 `test_inputs_never_modified_outputs_write_protected`（L779）、`test_non_writeable_input_arrays_accepted_unchanged`（L521）；探针 F1-F4（bytes 前后全等/写入抛 ValueError/新对象） | 无 |
| （范围项）重复应用拒绝 | **PASS** | 双保险：L604-618 前驱域门（背景后再减 → `PROCESSING_DOMAIN_MISMATCH`）+ core `ProcessingHistory` 同 stage_name 唯一（`time_domain.py` L576-588，bump version 不绕） | 工程师测试 `test_duplicate_stage_name_rejected_on_real_chain`（L535）、`test_core_uniqueness_probe_bumped_version_still_rejected`（L547，patch transition 表的隔离探针）、`test_record_always_carries_background_reference_id`（L579）；探针 E1/E2 独立复现 | 无 |
| （范围项）不实现 Flat/连续背景/参考采集/UI/.rcbg | **PASS** | 模块 import 面仅 core/calibration/bandpass/osl_calibration（L56-82）；无 storage/acquisition/UI 符号 | 工程师测试 `test_module_source_contains_no_excluded_symbols`（L840-885，AST 守卫含 `moving_average`/`sliding`/`AirBackgroundSession`/`controllerreferenceadapter`/`accept_sweep`/fft 词汇）；本审查复核 import 面一致 | 无 |

依赖 Issue 实际接口兼容性（非仅引用声明）：029 `AirBackgroundReference` 六字段只读对象（`reference.py` L532-541）与 `ReferenceDomain`（L110-114 消费面核对）；030 `ProcessingStage`/`StageResult`/`_input_domain_of`（`bandpass.py` L97-158，`runtime_checkable` Protocol isinstance 探针）；032 `osl_profile_digest`/`OslCalibrationSet`/`OslCalibrationStage`（provenance entry 四字段格式 L243-293 与 033 解析对齐）。依赖文件 SHA-256 前 16 位 8/8 与 t1 基线单一致（`reference.py` e2e5403766c45f88、`bandpass.py` f707839674ceb5e1、`osl_calibration.py` 30224c9a0091c02b、`osl.py` 5b7136979df9e6ad、`time_domain.py` cfa8271f5ebd545c、`enums.py` 4e906f159b1c8599、`CALIBRATION.md` 5ca0dc5fdd2ccedc、`PROCESSING.md` 9d730ec7a0f7c223）——t2「只读依赖一字节不动」属实。

## 5. Git 与交付检查

| 检查项 | 结果 |
|---|---|
| 分支/HEAD | `main @ 4cec913`，与 t1 基线一致 ✓ |
| 远端 | `origin/main...HEAD` = `0 0`（032 推送重试已完成；t1 报告中的未决项已闭环）✓ |
| 本批提交 | t2 按流程未 commit/push（等待复审 PASS 后 captain 自动合并）——工作树实测证实 ✓ |
| changedPaths = inScope | 4/4 逐一相等（`git status --porcelain` 实测：M06 modified + 3 untracked；无范围外文件、无遗漏）✓ |
| 缓存/构建物入库 | 无（`__pycache__` .pyc 被 .gitignore 覆盖，`git status` 不显示）✓ |
| `git diff --check` | clean ✓ |
| reflog | 全序列 checkout/commit/merge（031/032 历史正常推进），无 reset/rebase/amend/历史重建 ✓ |
| 契约变更与文档 | 输出域 `frequency_background_applied`、引用字段 `background_reference_id` 均为 core 既有冻结面（029-032 已建立），无公共契约变更；M06 状态行按流程更新 ✓ |
| 混入多 Issue | 无——改动全部归属 ISSUE-033 ✓ |

## 6. 测试与验证结果（独立复现，interpreter = `.venv/Scripts/python.exe`，Python 3.13.14）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python -m pytest tests/contract/test_processing_background_subtraction.py -q` | 0 | **42 passed** in 0.11s（与 t2 声称一致） |
| `python -m pytest`（背景减除+osl+bandpass+处理时域+core 时域+reference 六文件）`-q` | 0 | **204 passed** in 2.42s（与 t2 声称的相关回归 204 一致） |
| `python tools/quality/verify.py` | 0 | **1291 passed / 4 deselected** in 270.11s + ruff `All checks passed!` + mypy `Success: no issues found in 53 source files` + `package import ok` + `[quality] all gates passed`（基线 1249 + 新增 42；mypy 52→53 = 新模块；与 t2 声称逐项一致） |
| `python -m ruff check src tests` | 0 | `All checks passed!` |
| `python -m mypy src` | 0 | `Success: no issues found in 53 source files` |

**独立反例/变异探针**（35 项，系统临时目录运行后清理，`exit 0`，不触碰仓库文件）：

- 域保护：A1 raw 参考打 calibrated 数据拒 / A2 OSL 参考无 live set 构造拒 / A3 OSL 参考打 raw 数据拒 / A4 同 ID 不同内容（重解 profile）拒 / A5 匹配 live set 正常通过（calibrated 正向控制）
- 通道/轴：B1 交换序拒 `CHANNEL_CONTRACT_MISMATCH`；D1 mean NaN 构造拒 / D2 轴 inf 拒 / D3 float64 mean 拒 `DTYPE_MISMATCH` / **D4 1-D mean 构造通过但 apply 拒 `SHAPE_MISMATCH`（两级校验路径，工程师测试未直接覆盖的变异）** / **D5 3-D mean 同上** / D6 平移轴拒 `AXIS_MISMATCH`
- profile：C1 ref PID_B vs 记录 PID_A 拒 / C2 OSL 域 ref 无 profile_id 拒
- 重复应用：E1 背景输出再进拒（域门）/ E2 patch transition 表 + bump stage_version="9.9" 构造 chained record 仍拒（core 唯一性，`INVALID_ARGUMENT`）
- 不可变：F1 source/reference/axis bytes 前后全等 / F2 输出 write-protected / F3 新对象 / F4 输出写入抛 `ValueError`
- 广播/Flat：G1 scan 广播 bit 级等于逐 trace sweep stack / G2 常数信号 DC 减参考后保留（沿 trace 无统计的数学指纹）
- history：H1 record 字段精确（stage_name/域/引用/时间/参数结构）/ H2 `to_dict/from_dict` 往返全等且 `background_reference_id` 回填 / H3 下游 bandpass 链 2 records（provenance continuity）
- digest：I1 确定性 / I2 64 hex / I3 trace_count 敏感 / **I4 数组编码节点与 storage codec `_encode_array` 同构（D3「storage-mirrored」声称实测成立）** / I5 独立调用 digest 对非有限 mean 拒
- 函数级语义：J1 `check_safe_reuse` 无 live set → incompatible（不抛）/ J2 匹配 live set → compatible / J3 raw 参考 + live set 静默接受且行为干净（P3-4 观察）/ J4 metadata 透传（不可变 tuple 共享，安全）
- 数值宽容度：K1 整数值 int64 轴与 float64 轴数值相等被接受（同轴语义，`np.array_equal` 行为）

审查前后 `git status --porcelain` 完全一致（5 项：M06 + t1 报告 + t2 三新文件）；探针临时目录已删除；无遗留产物。

## 7. 报告与事实差异

t2 完成报告与仓库事实逐项核对，**无重大差异**：

| 声称 | 核对结果 |
|---|---|
| 定向 42 passed | ✓ 独立复现 42 passed（exit 0） |
| 相关回归 204 passed | ✓ 独立复现 204 passed（exit 0） |
| verify.py 1291 passed / 4 deselected + ruff + mypy(53) + import 全绿 exit 0 | ✓ 逐项复现一致（270.11s vs 声称 271.45s，正常波动） |
| changedPaths = inScope 4 逐一相等 | ✓ git 实测证实 |
| 只读依赖哈希与 t1 一致 | ✓ 8/8 复算一致 |
| M06 L229 仅状态行 Review | ✓ diff 实测 +1/-1 |
| 未 commit/push | ✓ 工作树未提交 |
| 红灯先行（ModuleNotFoundError） | **无法独立事后验证**（过程声明）——未见反证：模块为全新 untracked 文件，测试与其结构严格对应，不构成怀疑理由 |
| 计划 §7 偏差 2（live `current_calibration` 构造强制） | ✓ 已实证（A2/A4 探针 + 代码 L492-506）；正确性独立判断见 §3 末尾 |

t1 基线单的「origin 停在 b4f6dec、本地 ahead 3」已过时（captain 推送完成，现为 0 0）——按流程由 captain 后台 push 兜底，非差异缺陷。

## 8. 剩余风险

1. **raw 域「设备/天线配置硬性兼容字段」未实现**（CALIBRATION §4 列出但注明「哪些属于硬拒绝应由实测后冻结」）：`AirBackgroundReference`（029 冻结面）本身不含设备/天线字段，M06 ISSUE-033 范围亦未列设备校验。属未来 Issue/实测冻结事项，非本轮缺陷；编排 Issue（036）引入任务上下文时应重访。
2. **P3-1 跨层 digest 口径差异**（见 §3）：审计跨 `.rcbg` 文件与 record 时需明确两套 digest 的适用域。
3. **digest 的 dtype 自律**（P3-3）：公开函数 `background_reference_digest` 独立使用时依赖调用方保证 complex128/float64 dtype（stage 路径已强制）。
4. **校准域背景的可用性边界**：OSL 域参考必须有产生数据的 `OslCalibrationSet`（构造期强制）——回放/离线场景需从 `.rcal` 重建校准集才能应用校准域背景；这是严格 provenance 的设计代价，已在计划 §7 偏差 2 显式记录，非缺陷。

## 9. 合并建议

**建议合并（自动流程照常执行）**：

- M06 L248-252 三条验收标准全部 PASS，且各带文件:行号代码证据与工程师测试+独立探针双重测试证据；
- 全量门禁独立复现全绿（1291/4 + ruff + mypy(53) + import，exit 0），数字与 t2 报告逐项一致；
- 无 P0/P1/P2 问题；四项 P3 观察均为后续改进项，不阻止本次合并；
- changedPaths = inScope 4 路径逐一相等，无范围外改动；依赖文件零字节改动（哈希 8/8 复核）；
- 排除项（Flat/连续背景/参考采集/UI/.rcbg/storage 依赖）经 AST 守卫测试 + 本审查 import 复核双重确认未触碰。

合并时按流程：M06 L229 `Review` → `Done`（引用本报告），commit + push 至 main。

## 10. 最小修复清单

无阻止合并问题，无必须本轮处理的修复项。供后续 Issue 排期参考（P3，任选时机）：

1. **P3-2**：`check_safe_reuse` 的 provenance entry 缺 `channel_id` 时改按 strict-provenance-unavailable 拒绝（与 032 `OslProfileProvenance.from_json` 的严格度对齐）。
2. **P3-3**：`_encode_array` 对 dtype 不匹配从静默升位改为拒绝（与 032 同名函数一并调整，保持两模块一致）。
3. **P3-4**：raw 域 stage 构造收到 `current_calibration` 时抛 `INVALID_ARGUMENT`（防调用方误配置）。
4. **P3-1**：在 036 编排/存储衔接时用 ADR 或文档登记 processing 层 digest 与 `.rcbg` 存储 digest 的口径差异（或统一字段集）。

——审查结束。审查者全程只读（除本报告这一 t3 契约交付物外未修改任何文件、未执行 Git 写操作），等待 captain 决定合并。
