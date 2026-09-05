# ISSUE-035 独立复审报告（t3）

- 审查者：AgentTeams `uav-gpr-issue-035-flat-reflection` 成员 reviewer（只读角色）
- 审查日期：2026-09-05
- 审查标准：`docs/ISSUE_REVIEW_STANDARD.md` v1.0（§13 固定十节格式）
- 被审对象：t2 交付 ISSUE-035「Flat Reflection 时域阶段」（`docs/issues/M06_CALIBRATION_PROCESSING.md` L301-336）；完成报告 = t2 任务输出 + `docs/plans/2026-09-05-issue-035-flat-reflection.md` §9 执行日志
- 解释器：`.venv/Scripts/python.exe` = Python 3.13.14（Windows venv，全部门禁统一使用；探针经 stdin 注入、零仓库文件）

## 1. 审查结论

**PASS**（无 P0/P1/P2 问题；一条 P3 变异遮蔽观察，与 034 复审 P3-2 同族、双重 fail-closed 设计使然，不阻止合并）。

ISSUE-035 交付真实、完整、合规：`FlatReflectionFilterStage` 沿 `TimeDomainScan` 第 0 维（trace 轴，测线方向）减去 edge 填充中心滑动均值（cumsum O(N)、complex128 实虚独立），窗口为奇数道数 ≥3 构造期钉死、短测线 apply 期拒绝；六守卫链 fail-closed，重复 flat 双门（stage + core 唯一性）拒绝，dewow→flat 推荐顺序以真 `DewowStage` 端到端串联、错序（flat 先）由 034 dewow 侧 guard 3 拒绝；与 033 空采背景减除在名称/token/域链/数学对象四重区分且长链共存合法；"可能削弱连续层状反射"风险随模块与类 docstring 落盘。**复审最硬证据：迁移实现与冻结参考项目真实代码（本地只读副本，SHA-256 实测 = manifest.json/md/代码常量/测试字面量五方相等）在 kernel 级 bit-exact（W=7 随机复缓冲）**；黄金三方对拍（场景 A/B/C 字面量 + 朴素 O(N·W) clip 索引转写 + canonical JSON SHA `060f8342…`）全部独立复现。全量门禁独立复现全绿（1396 passed/4 deselected、ruff、mypy 55 files、import、exit 0）。建议 captain 按自动化轮流程合并（PASS → feat 分支提交 → 合并 → M06 Done 标记 → 推送 → 进入 ISSUE-036）。

## 2. 自动识别的审查范围

| 项 | 实测值 |
|---|---|
| Issue | ISSUE-035（M06 L301-336；直接依赖 030/031 均 Done，t1 已实证交付物与合并提交） |
| 分支 / 基线 | `main @ 8accb76`（t2 无 commit/push，按 M06 提示词"报告并停止"设计；合并归 captain） |
| 交付物（inScope 4 路径，逐一相等） | `src/uav_gpr/processing/flat_reflection.py`（新增 493 行）、`tests/contract/test_processing_flat_reflection.py`（新增 996 行 / 54 测试）、`docs/plans/2026-09-05-issue-035-flat-reflection.md`（新增）、`docs/issues/M06_CALIBRATION_PROCESSING.md`（仅 L303 状态行 Planned→Review，diff 实测仅 1 行） |
| 工作树其余变化 | 仅 t1 交付物 `docs/reports/ISSUE_035_BASELINE_CONFIRMATION.md`（t1 任务产出，非 t2 范围外修改；034 复审同款口径） |
| 远端同步 | `git rev-list --left-right --count origin/main...HEAD` = `0 0`——t1 登记的"034 推送后台重试（ahead 3）"已闭合 |
| 参考源 | 本地只读副本 `D:\博士任务\rebar-inspector`：`processing/flat_reflection.py` SHA-256 `89e3c01b…87df0`（实测 = manifest.md L77 = manifest.json = 代码常量 L123 = 测试 docstring）、`_time_stage_common.py` `e0c201b5…33c81`（同五方相等） |

## 3. 主要问题（按 P0→P3）

无 P0 / P1 / P2。一条 P3：

- **P3-1（stage 级重复门变异遮蔽，设计使然，034 复审 P3-2 同族）**：若删去 `flat_reflection.py` L395-408 的 stage 级重复 flat 门（guard 2），54 测试仍全绿——因为 core `ProcessingHistory.append` 的 stage 唯一性门（`src/uav_gpr/core/time_domain.py` L578-589）会以**同错误码、同消息子串**（"…may be applied only once per history; re-processing requires a new history/revision"，core 侧 L581）在 append 时再次拒绝，行为仍 fail-closed（代价仅是先做无用数值再被拒）。t1 契约 §3.3-4 明确要求"core + stage 双重 fail-closed"，非缺陷。修复方向（可选）：在 `test_duplicate_flat_refused_twice` 中把第一闸断言收紧到 stage 消息前缀 `"flat_reflection_filter may be applied"`，或补一条"重复 + 短测线双违规输入优先报重复门（guard 2 在 guard 6 之前）"的路径断言，使 stage 门可被独立钉死。

## 4. 逐 Issue 验收矩阵（M06 L322-326 三条 + 范围/排除项）

| 验收标准 | 状态 | 代码证据（文件:行号） | 测试/探针证据 | 问题或限制 |
|---|---|---|---|---|
| L324 沿 trace 而非 frequency/time 轴运算 | **PASS** | `flat_reflection.py` L105 `FLAT_AXIS: Final = 0`（契约常量非选项）；L455-460 唯一调用 `axis=FLAT_AXIS`；L254-272 kernel `moveaxis(norm_axis→0)` 后仅在首维 pad/cumsum/差分 | 测试 L558-568 `test_axis_direction_is_trace_not_time`（trace 变化被消、time 轴滤波会成 no-op 的反例断言）；探针 P1a：输出与 time 下标无关（最大跨 k 偏差 2.8e-16，纯 FP 舍入）；P1b：模块输出 == 朴素 axis-0 模型 bit-exact；P1c：axis-0 结果 ≠ time 轴结果（反例）；P3c/P3d：W=5/7/101 与参考核 bit-exact | 无 |
| L325 不与 AirBackground 混名/混 history | **PASS** | L91 `FLAT_STAGE_NAME="flat_reflection_filter"` vs 033 `air_background_subtraction`；L19-28 模块 docstring 概念边界（CALIBRATION.md L9-10 原文口径：外部频域复数参考 vs 数据内部沿测线局部统计，名称/history token/域转换/数学对象四重不同）；record 域链恒 time→time_processed（L470-471），不携带任何背景引用 | 测试 L803-834 `test_not_air_background_distinct_everywhere`：token 不等 + 长链（osl→air_bg→bandpass→ifft→dewow→flat）共存合法、两 token 各恰一次、air 先于 flat；探针 P7a-c：token/域链/record 参数键集（无 background_reference_id 等空采键） | 无 |
| L326 对拍、输入不变、潜在目标削弱明确记录 | **PASS** | 对拍：L120-129 `_REFERENCE_SOURCE_SHA256` 入 record、L7-12 docstring 冻结哈希；输入不变：L451-461 只读视图相减产新数组、L476-483 core 构造防御拷贝置只读；削弱记录：L30-35 `KNOWN LIMITATION / RISK STATEMENT`（"may attenuate laterally continuous layered reflections and targets aligned with the survey line… must stay optional"）+ 类 docstring L289-291 | 对拍：黄金场景 A/B/C 字面量 exact（测试 L871-889）+ canonical JSON SHA `060f8342…` 逐位钉死（L892-910）+ 朴素 O(N·W) clip 转写 dyadic 缓冲 bit-exact（L913-930）；复审探针 P3a-d 独立重算（场景 A bit-exact、随机缓冲 W=5/7/101 allclose 1e-12、参考核 verbatim 转写 bit-exact）+ 参考源文件哈希五方相等实测；输入不变：测试 L851-865 bytes 前后全等 + 双向只读 + 写入抛 ValueError + history 旧对象不动；探针 P6a-d 同款复现；削弱：测试 L493-503 以数学演示钉死（线性坡残差 < 原幅值 90%）；风险文档存在于 docstring（探针 P8 复核） | 无 |
| 范围 L313：trace 窗口/edge 策略/O(N) complex/多通道/time | **PASS** | L100 默认窗口 101 道（= 参考冻结值）；L110 `FLAT_PADDING="edge"` 契约常量；L136-169 构造期校验（bool/非 int/`<3`（含 window=1 全零安全加固理由）/偶数全拒）；L265 `np.cumsum(dtype=complex128)` 实虚独立；L476-483 channels/time_axis_s/metadata 全量透传 | 测试 L427-453（默认值 + 拒绝矩阵 12 参数化 + window=1 理由钉死）；L676-687 edge vs reflect 反例；L541-555 双通道逐 slice bit-exact；L509-517 复数实虚等价；探针 P2/P9/P8 | 无 |
| 范围 L314：time_processed/history 与推荐 Dewow→Flat 顺序 | **PASS** | L322-323 `output_domain=TIME_PROCESSED`；L318-319 输入域 {TIME_BASE, TIME_PROCESSED}；L463-474 `_record_for` + append；错序另半边由 034 `dewow.py` L102 `_FLAT_STAGE_NAME` + L410-421 guard 3 闭环（token 与 L91 逐字一致，实测比对） | 测试 L758-767 真 `DewowStage` 端到端（names=[ifft,dewow,flat]、域链 (TIME_BASE→TIME_PROCESSED, TIME_PROCESSED→TIME_PROCESSED)）；L771-778 time_base 直挂合法；探针 P5c 同款 + P5b flat→dewow 被 dewow guard 3 以 PROCESSING_DOMAIN_MISMATCH 拒绝（消息含 flat_reflection_filter） | 无 |
| 范围 L315：短测线/窗口边界/重复/错误顺序保护 | **PASS** | L441-449 guard 6 `window_traces > n_traces` 拒（消息含道数与减小窗口指引）；L623-635 恰等 n 合法；L395-408 guard 2 重复拒 | 测试 L614-620（消息与 context n_traces 断言）；L623-635 clip-多重集逐行模型；L638-653 类型守卫；L717-755 空 history/频域前驱/重复双门；探针 P4/P5a/P5b | 无 |
| 范围 L316：黄金样本与风险文档 | **PASS** | 见 L326 行证据 | 见 L326 行证据 | 无 |
| 排除项 L320：不做实时增量近似 / UI 默认启用 | **PASS** | 模块无任何增量状态缓存（`self.x = …` 仅存在于 `__init__`）；无 UI/编排符号 | 测试 L951-987 AST 排除守卫（禁 rebar_inspector/storage/acquisition/Qt import + 增量状态检测 + 守卫本身活性断言）；L990-996 无 UI 自动启用符号 | 无 |

## 5. Git 与交付检查

- **基线与分支**：`main @ 8accb76`（`docs(issues): mark ISSUE-034 Done after automated merge`），t2 交付为工作树未提交状态（M06 L303 一行修改 + 3 个未跟踪新文件），与 M06 提示词"运行门禁，报告并停止，不 commit/push"及团队"PASS 后自动合并推送"流程一致；合并动作归 captain（建议见 §9）。
- **changedPaths 与 inScope 逐一相等**：`git status --porcelain` 实测 = ` M docs/issues/M06_CALIBRATION_PROCESSING.md` + `?? docs/plans/2026-09-05-issue-035-flat-reflection.md`、`?? docs/reports/ISSUE_035_BASELINE_CONFIRMATION.md`、`?? src/uav_gpr/processing/flat_reflection.py`、`?? tests/contract/test_processing_flat_reflection.py`——与 t2 契约 inScope 4 路径精确相等，无范围外修改；`processing/__init__.py` 零字节改动（inScope 不含，实测未列入 status）。
- **M06 diff 精确性**：`git diff docs/issues/M06_CALIBRATION_PROCESSING.md` 仅 L303 一行（Planned→Review + t2 摘要），无其他条目改动。
- **reflog / stash**：最近 12 条 reflog 全部为 033/034 正常分支-合并-标记流（checkout/commit/merge），无 reset/rebase/amend/历史重建痕迹；`git stash list` 空。
- **diff-check**：`git diff --check` 干净（exit 0），无空白错误。
- **无污染交付**：无缓存/日志/构建物/密钥/实测数据/参考仓库文件进入交付；t2 日志披露的"中途误生成仓内 `C:/Users/Public/...` 目录"实测已复原（status 无残留）；审查自身探针零仓库文件、verify 日志置于仓库外系统临时目录并在收尾清理。
- **推送状态**：origin/main 与 HEAD `0 0` 同步——t1 登记的 034 推送后台重试已闭合，无强推或绕过保护迹象（报告与 Git 无冲突，状态演进已核实）。

## 6. 测试与验证结果（全部本机独立复现）

解释器统一 `.venv/Scripts/python.exe`（Python 3.13.14，Windows venv）。

| 命令 | 结果 | 退出码 |
|---|---|---|
| `python -m pytest tests/contract/test_processing_flat_reflection.py -q` | **54 passed** in 0.14s | 0 |
| `python -m pytest tests/contract/test_processing_dewow.py tests/contract/test_processing_bandpass.py tests/contract/test_processing_time_domain.py tests/unit/test_core_time_domain.py tests/contract/test_processing_background_subtraction.py -q` | **206 passed** in 0.33s | 0 |
| `python tools/quality/verify.py`（日志重定向仓库外系统临时目录） | pytest **1396 passed / 4 deselected** in 272.21s；ruff `All checks passed!`；mypy `Success: no issues found in 55 source files`；package import ok；`[quality] all gates passed` | 0 |
| `git diff --check` | 干净 | 0 |
| `sha256sum` 参考源两文件 | `89e3c01b…87df0` / `e0c201b5…33c81` = manifest.md L77 + manifest.json + 代码常量 + 测试字面量五方相等 | 0 |

**独立探针矩阵（stdin 注入、零仓库文件、临时产物收尾清理，全部 PASS）**：

| # | 探针 | 结果 |
|---|---|---|
| P1a-c | trace 轴语义反例：输出与 time 下标无关（max dev 2.8e-16）；模块 == 朴素 axis-0 模型 bit-exact；≠ time 轴滤波结果 | PASS |
| P2 | edge 平铺手算：(2x0+x1)/3、(x0+x1+x2)/3、(x1+2x2)/3 | PASS |
| P3a-d | 黄金独立重算：场景 A bit-exact vs 朴素循环；随机缓冲 (128,3,37) W=5/7/101 allclose 1e-12；模块 == 参考核 verbatim 转写 bit-exact（W=7） | PASS |
| P4 | 短测线：W=101 > n=50 → INVALID_ARGUMENT，context 含 n_traces=50 | PASS |
| P5a-c | 重复 flat 拒（stage 门）；flat→dewow 错序被 dewow guard 3 拒（PROCESSING_DOMAIN_MISMATCH）；dewow→flat 合法链 names/域链正确 | PASS |
| P6a-d | 输入不变：bytes 全等、双向只读、输出新对象 complex128 shape 保持、旧 history 不动（len 1→2 新实例） | PASS |
| P7a-c | AirBackground 区分：token、域链 time→time_processed、record 参数无任何空采背景键 | PASS |
| P8 | 水平背景（各道同值 4.0-2.5j）输出逐元素恰 0（flat 核心目的） | PASS |
| P9 | W==n_traces clip-多重集逐行模型 bit-exact | PASS |
| 变异遮蔽评估 | 删 stage 重复门 → core 唯一性门同码同消息子串再拒（fail-closed 保持，记 P3-1）；删 edge→reflect、axis 0→last、去黄金、去不变性断言均有对应测试钉死 | 见 §3 |

测试口径：54（定向）= 计划 §7 矩阵全部维度（协议/水平背景/局部目标/复数/多通道/短测线/窗口边界/顺序 history/不变性/黄金/性能/排除守卫）；1396 = 基线 1342 + 新增 54（数量账目相符）。

## 7. 报告与事实差异

逐项核对 t2 完成报告与仓库事实，**未发现不实陈述**：

- 定向 54 / 依赖回归 206 / verify 1396 passed / 4 deselected / ruff / mypy 55 files / import ok / exit 0——全部独立复现相等 ✓。
- "changedPaths = inScope 4 路径逐一相等" ✓（git status 实测）；"未 commit/push" ✓；"M06 L303 → Review" ✓（diff 仅一行）。
- "canonical SHA `060f8342…` 逐位 pin / 朴素转写 bit-exact" ✓（测试运行 + 独立探针双重复核）。
- "与 034 `_FLAT_STAGE_NAME` token 逐字一致" ✓（dewow.py L102 实测比对）。
- t2 §9 披露的六项口径修正（ASCII 消息、window==n 期望、skew 探针语义、history 链 fixture 合规、顺序守卫分工、mypy np.pad 重载）均可在最终代码/测试中核实为已落地的正确形态；其中"红灯在先"（首轮 ModuleNotFoundError 收集错误）属过程声明，**无法事后独立验证，未发现反证**（与计划 §9 记录一致）。
- t1 基线单登记"034 推送后台重试（ahead 3）"——当下实测已闭合（0 0），非差异，属状态演进。
- t2 披露"误生成仓内目录已删除复原"——实测无残留 ✓。

## 8. 剩余风险

- **默认窗口 101 道与短测线**：少于 101 道的测线用默认构造会在 apply 期被拒（fail-closed，消息含指引）——设计使然；ISSUE-036 编排须按测线长度选窗或显式降窗，属编排职责非本 stage 缺陷。
- **目标削弱固有风险**：本阶段按设计会削弱连续层状反射/平行测线目标（docstring 已声明；PROCESSING.md §6 要求 UI 必须说明影响）——UI 说明义务由后续 ISSUE-049/052 承接。
- **stage 重复门变异遮蔽**（P3-1）：行为 fail-closed 不受影响，仅测试对 stage 门缺少独立钉死路径。
- **黄金样本覆盖面**：黄金字面量为小缓冲钉死 + 随机缓冲对拍（复审探针扩展至 128 道随机复数）；超大真实数据对拍属 M12 现场验收范围。

## 9. 合并建议

**建议合并（PASS）**。按 034 既定模式执行自动化合并：

1. 从当前工作树创建 `feat/issue-035` 分支，提交 6 个文件：4 个 inScope 路径 + `docs/reports/ISSUE_035_BASELINE_CONFIRMATION.md`（t1 交付物）+ 本复审报告（034 先例 bb49e32 同构：实现+测试+计划+基线单+复审报告同一 feat 提交，M06 不入 feat 提交）；
2. 合并回 `main`（merge 提交，`ort` 策略）；
3. 独立 docs 提交把 M06 L303 `Review` → `Done`（034 先例 8accb76 同构，避免混提交）；
4. 推送 origin/main（无强推）；
5. 删除本团队并建立 ISSUE-036 团队（编排、revision 与安全回放——035 是其直接前置，flat 的 stage_name/域链/record 参数已被本复审钉死可安全依赖）。

## 10. 最小修复清单

无阻止合并项。可选（不阻塞合并，可并入 ISSUE-036 或后续微修）：

- P3-1：把 `test_duplicate_flat_refused_twice` 第一闸断言收紧到 stage 消息前缀 `"flat_reflection_filter may be applied"`，或补"重复 + 短测线双违规优先报 guard 2（先于 guard 6）"路径断言，独立钉死 stage 级重复门。
