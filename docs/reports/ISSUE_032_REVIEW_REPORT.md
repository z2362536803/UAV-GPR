# ISSUE-032 独立审查报告（t3 · reviewer）

日期：2026-09-05
审查者：AgentTeams `uav-gpr-issue-032-osl-stage` 成员 reviewer（只读角色）
审查对象：t2 交付（engineer）——ISSUE-032「OSL 处理阶段与 calibrated provenance」
审查标准：`docs/ISSUE_REVIEW_STANDARD.md` v1.0（§13 固定格式）
依据材料：`docs/issues/M06_CALIBRATION_PROCESSING.md` L190–225、`AGENTS.md`、`docs/CALIBRATION.md` §5–§7、`docs/PROCESSING.md` §1–§2、`docs/issues/README.md`、t1 基线单（`docs/reports/ISSUE_032_BASELINE_CONFIRMATION.md`）、t2 计划（`docs/plans/2026-09-05-issue-032-osl-stage.md`）

## 1. 审查结论

**VERDICT = PASS**（无 P0/P1/P2；3 项 P3 挂账，均不阻止合并）

ISSUE-032 三条验收标准（M06 L211–215）全部以代码与测试证据逐项满足；t2 完成报告的测试数字、文件清单与 Git 状态**全部独立复现一致，未发现报告与事实差异**；排除项（不采 OSL/不存文件/不应用空采或 IFFT/不做 UI）经源码 AST 独立核查成立；raw 不可变性经字节级探针证实；safe reuse 严格性（同 ID 异内容/错序/legacy 全拒）经反例探针证实；本地转写 digest 与 storage 编码器 digest 在**非钉死的新 profile** 上逐位相等（D3a 镜像忠实性独立验证）。建议按自动化轮流程合并。

## 2. 自动识别的审查范围

| 项 | 识别结果（仓库证据） |
|---|---|
| Issue | ISSUE-032（M06 L190–225；状态行 L192 = `Review`，t2 置位） |
| 目标/验收 | L196–198 目标、L200–205 范围/排除、L211–215 三条验收 |
| 依赖 | ISSUE-027（Done，`a2f65c6`，osl.py 1008 行在 HEAD 历史）、ISSUE-030（Done，`89fd9bb`，bandpass.py/ProcessingStage 契约在 HEAD 历史）——均为 Git 实证，非仅引用声明 |
| 基线 | `main @ b4f6dec`（t1 基线单口径一致；reflog 显示本审查期间无 reset/rebase/amend） |
| 交付文件（t2 changedPaths） | `src/uav_gpr/processing/osl_calibration.py`（新，778 行）、`tests/contract/test_processing_osl_calibration.py`（新，746 行）、`docs/plans/2026-09-05-issue-032-osl-stage.md`（新）、`docs/issues/M06_CALIBRATION_PROCESSING.md`（M，仅 L192 一行）——与 t2 计划 §2 inScope 4 路径逐一相等；另存在 t1 交付的 `docs/reports/ISSUE_032_BASELINE_CONFIRMATION.md`（t1 changedPath，不计入 t2） |
| 未提交/未跟踪 | 上述 4 文件 + t1 报告；无 staged 内容（`git diff --cached` 空）；无缓存/日志/构建物/实测数据混入 |
| 提交状态 | t2 未 commit/push（符合「不 commit/push」纪律，等待 PASS 后 captain 合并） |

范围唯一可确定（Issue 编号、状态行、计划文档、文件清单相互印证），无需 BLOCKED。

## 3. 主要问题（按等级排序）

无 P0/P1/P2。P3 挂账如下（均不阻止本次合并，供后续 Issue/批次处理）：

- **P3-1（测试卫生）** `tests/contract/test_processing_osl_calibration.py` L341–361：`test_duplicate_stage_name_guard_is_core_enforced` 在进入 `try/finally` **之前**完成对 `core_time_domain._ALLOWED_TRANSITIONS` 的临时放宽并构造 `chained` 记录（L346–356）；若该构造在未来 core 变更下抛异常，补丁将泄漏到同进程其它测试。当前构造在放宽表下必然通过记录级校验（`_validate_transition`/`_validate_references` 在 `ProcessingRecord.__post_init__` 强制），实际风险很低。修复方向：把补丁应用与 `chained` 构造一并移入 `try` 块（或 try 包住从 patch 到断言的全程）。
- **P3-2（外观重复）** 同测试 L362–365 存在重复的断言对（`code`/`context` 各断言两次）。无行为影响，顺手清理即可。
- **P3-3（既有 core 契约限制，非 032 缺陷）** 数据域由 supplied history 而非数组内容决定：把已校准数据重新包进空 history 的容器会被再次校准。`docs/PROCESSING.md` §1 L14 明确将该 provenance anchor 留给未来工作，属全项目既有设计决策；ISSUE-036 的 safe replay 入口必须承担该严格性（fresh raw 要求空 history / 复用要求严格相同 provenance）。登记为 036 的输入约束，不在本轮修复。
- **P3-4（性能特征登记）** scan 路径沿 trace 轴 Python 循环逐道调用 `OslCalibrationSet.apply`（`osl_calibration.py` L536–542；027 的 apply 只接受 2-D，故这是唯一正确路径）。正确性无虞；与 bandpass 的单次广播相比有 O(trace) Python 开销。无本 Issue 性能验收要求，登记给 PROCESSING.md §8 的后续性能 pass。

## 4. 逐 Issue 验收矩阵（ISSUE-032，M06 L211–215）

| 验收标准 | 状态 | 代码证据（文件:行号） | 测试/探针证据 | 问题或限制 |
|---|---|---|---|---|
| L213-a raw 永不修改 | PASS | `osl_calibration.py` L479–587：输入只读视图（core `FrequencySweep`/`FrequencyScan` 构造即 write-protect），输出为重建 core 模型的防御拷贝（L533–582）；无任何原地写 | 工程测试 `test_raw_input_never_modified`（L474–483，bytes 前后全等 + 输出写保护 + 写入抛 ValueError）；探针 P4（字节级 + 对象 id/新对象/只读标志） | 无 |
| L213-b calibrated 是 OSL 后、空采前 | PASS | `osl_calibration.py` L100 `_INPUT_DOMAINS={FREQUENCY_RAW}`、L509–523 输入域门（错误消息明示 "frequency_calibrated means after-OSL, pre-subtraction"）；core `_ALLOWED_TRANSITIONS`（`core/time_domain.py` L94–119）把 `FREQUENCY_CALIBRATED` 的后继钉为 background/filtered/time_base；本 stage 不含任何背景/IFFT 代码 | 探针 P10（osl→bandpass→ifft 三条独立记录、raw 字节不变）；`test_calibrated_history_then_other_predecessor_rejected`（L298）；`test_module_source_contains_no_excluded_symbols`（L708–746）+ 审查者独立 AST 复查（无 storage/solver/fft/UI 符号） | 无 |
| L214-a 错 profile/axis/channel fail-closed | PASS | `_validate_binding`（`osl_calibration.py` L409–475）：通道数/序/spec 精确全等（CHANNEL_CONTRACT_MISMATCH，含首个差异位点 context）；逐 profile 轴 `np.array_equal`（AXIS_MISMATCH）；S 参数绑定断言；data 末维=轴长（SHAPE_MISMATCH） | 工程测试 L216–274（错序/缺道/异 spec/错轴/非反射）；探针 P5a–P5d（错轴长度、错通道 id、交换 profile 绑定、非有限值，错误码全部命中） | 无 |
| L214-b 已有 OSL history fail-closed | PASS | 第一道门：stage 输入域门 L509–523（任何非 raw 末域，含 filtered 后回流，拒绝）；第二道门：core `ProcessingHistory` 同 stage_name 唯一（`core/time_domain.py` L576–588） | `test_reapply_on_own_output_rejected`（L289）、`test_duplicate_stage_name_guard_is_core_enforced`（L309–365，bump version 9.9 不绕过）；探针 P6a（换 calibration 的第二个 stage 实例仍拒）、P6b（外来 legacy calibrated 记录历史仍拒） | P3-1（测试补丁卫生） |
| L215 safe reuse 只接受严格相同 profile provenance | PASS | `check_safe_reuse`/`require_safe_reuse`（`osl_calibration.py` L606–731）：逐位置比较 `{channel_id, s_parameter, profile_id, content_sha256}` + `set_content_sha256` + `profile_id_field_semantics`；legacy/缺 digest 一律拒绝（L645–651）；require 版 fail-closed 带 mismatch 清单（L714–731） | 工程测试 L598–691（相同通过/异 ID/同 ID 异内容/错序/非末条 calibrated/legacy 全拒）；探针 P7a–P7f（重建同内容对象集合通过；同 ID 重解拒绝；绑定交换拒绝且 set digest 对序敏感；legacy 拒绝；链路越过 calibrated 后拒绝） | 无 |

范围项补充核对（M06 L200–205）：raw→osl_calibrated 严格域转换 stage（PASS，L386–391）；多通道分别应用对应 profile（PASS——探针 P1 证明 ch 行与各自 `profile.correct` bit 级相等，S11+S22 各用其 profile）；history/provenance/profile digest（PASS——L544–565 有序逐通道 entries + 组合摘要 + D2 首 pid 语义键；测试 L373–404 + 探针 P9/P9b/P9c JSON 往返全等）；重复 OSL 检测（PASS，见 L214-b）；safe reuse 判定接口（PASS，见 L215）。排除项全部成立（AST 双重核查）。

## 5. Git 与交付检查

- 当前分支 `main`，HEAD `b4f6dec`（与 t1 基线一致）；审查前后 HEAD 未移动、无新提交、无 stash（`git stash list` 空）。
- `git status --porcelain` 前后一致（5 项：M06 M + 4 untracked，其中 t1 报告 1 项）；`git diff --cached` 空；`git diff --check` clean。
- M06 差异精确为一行状态行（L192 `Planned → Review`），未触碰其他条目（`git diff` 实测）。
- reflog 最近 8 条为 031/030 轮的 commit/merge/checkout，无 reset/rebase/amend 迹象；无强推迹象。
- 远端：`origin/main...HEAD = 0 0`（031 推送重试已完成——与 t1 报告「ahead 3、重试中，以 captain push 为准」的如实口径一致，非矛盾）。
- 无范围外文件修改；未提交实测数据/缓存/日志/密钥/构建物；`processing/__init__.py`、`core/**`、`calibration/osl.py`、`storage/**`、`bandpass.py`、`time_domain.py` 全部零改动（`git status` 佐证 + 模块 import 集不变）。
- 提交边界：t2 遵守「不 commit/push」；合并由 captain 在 PASS 后执行（自动化轮流程）。

## 6. 测试与验证结果

解释器：`.venv/Scripts/python.exe`（Windows，Python 3.13.14，与 t1/t2 相同）。审查者全程只读复跑：

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python -m pytest tests/contract/test_processing_osl_calibration.py -q` | 0 | **34 passed**（0.13s） |
| `python -m pytest tests/contract/test_processing_osl_calibration.py tests/contract/test_processing_bandpass.py tests/contract/test_processing_time_domain.py tests/contract/test_calibration_osl.py tests/unit/test_core_time_domain.py -q` | 0 | **178 passed**（0.28s） |
| `python tools/quality/verify.py` | 0 | pytest **1249 passed / 4 deselected** in 272.01s；ruff `All checks passed!`；mypy `Success: no issues found in 52 source files`；package import ok；`[quality] all gates passed` |
| `python -m ruff check .` | 0 | `All checks passed!` |
| `python -m mypy src` | 0 | `Success: no issues found in 52 source files` |
| `python -c "import uav_gpr.processing.osl_calibration"` | 0 | import ok |
| 审查探针（系统临时目录 `D:/dsh/windows/test-0.1.2-rc.1/temp/issue032_review_probe.py`，运行后已删除） | 0 | **22/22 探针全 PASS** |

探针明细（反例/变异补查，覆盖执行者测试之外的需求面）：

- **P1** 双通道各自 profile bit 级应用（ch 行 == 各自 `profile.correct`）；**P2** scan == 逐道 sweep 应用；**P3** 独立逆公式 `Γ=(m−D)/(T+(m−D)S)` 数值对拍（atol 1e-9）；
- **P4** raw 字节前后全等 + 输出只读新对象；**P5a–d** 错轴长度/错通道 id/交换 profile 绑定/非有限值（错误码逐一命中）；**P6a/b** 二次校准（跨 calibration 实例、外来 legacy calibrated 历史）拒绝；
- **P7a–f** safe reuse 矩阵：同内容重建对象集合兼容（digest 判内容不判对象身份）；同 ID 重解拒绝（require 版 raise 带 mismatch 清单）；绑定交换拒绝且 `osl_set_digest` 对序敏感实证；legacy 缺 digest 拒绝；链路越过 calibrated 后拒绝；
- **P8** 本地转写 digest == storage 编码器 digest（`StoredOslProfile.from_profile().to_payload()` canonical SHA-256），在**非测试钉死的新 profile**（shift=0.17、VV/S22）上逐位相等——D3a 镜像忠实性独立成立；
- **P9/P9b/P9c** record 字段与 parameters 精确性（首 pid、语义键、有序 profiles、逐通道与组合 digest）+ history JSON 往返全等 + `osl_provenance_of` 往返后可用；
- **P10/P10b** osl→bandpass→ifft 三条独立记录且 raw 不变；safe reuse 在数据越过 calibrated 后拒绝。

全部门禁与探针复跑后 `git status` 与复跑前逐字节一致（工作区无测试残留进入跟踪范围）。

## 7. 报告与事实差异

逐项核对 t2 完成报告（任务登记 + 计划文档 §7）与仓库事实：**未发现差异**。

- 「定向 34 passed」→ 复现 34 passed ✓；「相关回归 178 passed」→ 复现 ✓；「verify.py 1249 passed/4 deselected（基线 1215+34）」→ 复现 ✓（1215+34=1249）；「ruff 全绿」→ ✓；「mypy 52 files」→ ✓（t1 基线 51 + 新模块 1）；「import ok」→ ✓。
- 「osl_calibration.py 778 行」→ 实测 778 行 ✓；「M06 置 Review」→ diff 实证 ✓；「changedPaths=inScope 4」→ status 实证 ✓；「未 commit/push」→ ✓。
- 黄金 digest 字面量测试（L426–460）声称经 storage 编解码独立重算并钉死 hex——审查者在**另一个新 profile** 上独立复算一致（P8），该声明可信 ✓。
- t1 基线单「本地 ahead origin 3（推送重试中）」→ 现为 0/0（后台推送已完成）；t1 当时已显式声明以 captain push 为准，非报告失实。
- 过程性声明（红灯先行、首跑 19 failed/9 passed 修复过程）无法事后独立验证，标为「未发现反证」。

## 8. 剩余风险

- **P3-3（provenance anchor 缺失，既有 core 契约）**：域身份由 history 决定，重包已校准数据到空 history 容器会被再次校准。`PROCESSING.md` §1 已把 anchor 列为未来工作；ISSUE-036 replay 入口是闭合点（fresh raw 必须空 history，reuse 必须严格相同 provenance）。032 在其边界内已把可判定面做满（输入域门 + 重复 stage 门 + safe reuse 判定）。
- **P3-4** scan 路径逐道 Python 循环的性能特征（正确性优先，ISSUE 无性能验收）。
- 033 依赖提示：`AirBackgroundSubtractionStage` 校准域校验可直接消费本模块公开面（`osl_provenance_of` / `check_safe_reuse` / `OslProfileProvenance`），无需触碰私有力；`calibration_profile_id` 单字段在多通道场景只代表首 profile（D2 已用语义键 + 逐通道 digest 补偿），033/036 不得把该字段当作多通道完整绑定使用。
- digest 为 canonical JSON 转写（非存储层单一权威调用）——双实现漂移风险已由 golden 字面量 + 本审查 P8 独立对拍压制，但若 029 未来改动 `to_payload` 字段结构，两处需同步（建议 033/036 消费时以本模块 digest 为准绳做回归）。

## 9. 合并建议

**建议合并（MERGE）**：VERDICT=PASS，无阻止合并问题；P3 挂账 4 项（P3-1/P3-2 测试卫生、P3-3 转 036 输入约束、P3-4 转性能登记）均不影响本次交付的正确性、契约符合性与可维护性。合并后由 captain 按自动化轮流程：M06 状态 Review → Done（附本报告链接）、提交 4 个 inScope 文件 + 本报告、推送、删团队建 ISSUE-033。

## 10. 最小修复清单（全部 P3，不阻止合并；修复执行者遵守 §14）

1. **P3-1**：`tests/contract/test_processing_osl_calibration.py` L341–361——将 `_ALLOWED_TRANSITIONS` 补丁的应用与 `chained` 构造移入 `try` 保护域，确保任何异常路径都恢复原始表（先加一个能复现泄漏的失败测试再修）。
2. **P3-2**：同文件 L362–365 删除重复断言对。
3. **P3-3**：不在本轮修（core 契约级）；转记为 ISSUE-036 的输入约束：replay 入口必须区分 fresh raw（空 history）与 strict-identical reuse（`check_safe_reuse` 全等）。
4. **P3-4**：不在本轮修；登记到处理性能批次（PROCESSING.md §8）与 ISSUE-036 编排性能验收时一并评估。

审查结束：reviewer 停止，未修改任何 t2 交付文件与 Git 状态（本报告为 t3 交付物），等待 captain 决策。
