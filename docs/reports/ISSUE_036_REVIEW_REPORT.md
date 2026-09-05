# ISSUE-036 独立复审报告（t3 / t5 round-2）

- 审查标准：`docs/ISSUE_REVIEW_STANDARD.md` v1.0（§13 固定十节格式）
- 审查者：AgentTeams `uav-gpr-issue-036-orchestration` 成员 reviewer（t3 attempt `6a7d003d`；round-2 = t5 attempt `50e40358`）
- 审查日期：2026-09-05
- 审查对象：t2 实现 + t4 repair-round-2（ISSUE-036「完整处理编排、revision 与安全回放」）
- 解释器：`.venv/Scripts/python.exe`（Windows venv，Python 3.13.14）
- 本版为 **round-2 复审**：round-1（t3）结论 PASS WITH CONDITIONS + F1(P2)+F2-F5(P3)；本版核验 t4 修复回合对 F1-F5 的闭合并重跑全部门禁与探针。

## 1. 审查结论

**PASS**

- t3 提出的 F1(P2) 已按最小修复方向完全闭合（`DerivedAttachmentWriter.inspect()` ⓪ 门卫：`file_role != GROUND` 或 `lifecycle_state ∉ {finalized, recovered}` ⇒ `DerivedAttachmentError(INVALID_ARGUMENT)`，且在任何暂存拷贝之前拒绝），F2-F5(P3) 同步闭合；round-1 的 25 项探针结论在本轮针对性复验中全部维持有效。
- M06 L359-363 三条验收标准维持 **PASS**（证据见 §4，行号按 t4 修复后文件更新）。
- 门禁独立复现：定向 **53 passed**；全量 pytest（非硬件）**1449 passed / 4 deselected**（= t1 基线 1396 + t2 49 + t4 4 ✓）；ruff clean；mypy 56 files clean；import ok；diff-check 干净；除一处与本 Issue 无关的既有 flaky 测试外全部 exit 0（详见 §6.3）。
- **无 P0/P1/P2 遗留**；新增 P3 一项（N1，M06 L340 状态行文案与 t4 changedPaths 登记口径出入——engineer 已如实说明 t4 inScope 不含该路径；实际 diff 显示该行确被更新为含 t4 闭合事实的准确文案。属流程性登记债，合并时顺手一行可解，不构成功能或数据问题）。
- t3 遗留的裁决项 D6(b)（011 reader 默认口径 vs IFFT 宽网格）维持 round-1 裁定：t2/t4 处置真实、合规、无数据破坏，属 schema 缺口登记的剩余风险（M06 收尾后 ADR），**不阻塞合并**（§8）。
- 审查者全程只读：未修改任何项目文件/Git；探针全部在系统临时目录运行并已清理；除本报告自身的 round-2 更新外，工作树与审查前完全一致。

## 2. 自动识别的审查范围

| 项 | 事实 |
|---|---|
| Issue | ISSUE-036（`docs/issues/M06_CALIBRATION_PROCESSING.md` L338-373）；状态行 L340 = `Review（…t3 复审 PASS WITH CONDITIONS，t4 repair-round-2 已闭合 F1(P2…) + F2-F5(P3)，定向 53 passed、全量 1449 passed / 4 deselected + ruff + mypy(56) + import 全绿…等待 t5 定向复验）` |
| 基线 | `main @ 487f9ad`（`origin/main` 同步 `0 0`）；reflog 最近 8 条无 reset/rebase/amend |
| t2+t4 交付路径 | `src/uav_gpr/application/processing_orchestrator.py`（2034 行，t2 1983 + t4 修复增量 51）、`tests/contract/test_processing_orchestrator.py`（1491 行 / 53 用例 = 49+4）、`docs/plans/2026-09-05-issue-036-orchestration.md`（含 §8b 修复日志与修复后门禁表）、`docs/issues/M06_CALIBRATION_PROCESSING.md`（仅 L340 一行） |
| 未提交状态 | 零 commit / 零 push / 零 merge；工作树 = 上述 4 路径 + t1/t3 报告共 6 项，无范围外修改 |
| 必读资料 | AGENTS.md、CLAUDE.md、docs/INDEX.md、docs/issues/README.md、M06 L338-373、PROCESSING/CALIBRATION/DATA_FORMAT、t1 基线单、t2/t4 计划文档（含 §8b）、ISSUE_REVIEW_STANDARD.md、round-1 报告（本文件上一版） |

## 3. 主要问题（round-1 F1-F5 处置 + 新增项）

| # | round-1 等级 | 复核状态 | 证据 |
|---|---|---|---|
| F1 | P2 | **CLOSED** | `processing_orchestrator.py` L1678-1696：`inspect()` ⓪ 门卫（`probe.file_role is not EndpointRole.GROUND` ⇒ 拒，context 带 file_role；`probe.lifecycle_state not in _SETTLED_LIFECYCLE_STATES`（L158：`frozenset({"finalized","recovered"})`）⇒ 拒，context 带 lifecycle_state/allowed），位于 `RcScanReaderLite` 打开与任何暂存拷贝之前。测试：`test_attachment_refuses_an_air_file`（L1276-1304：air 文件拒绝 + 整文件 SHA-256/raw 列摘要/raw 指纹三重不变 + 盘上无派生组）、`test_attachment_refuses_a_writing_partial`（L1306-1334：writing 拒绝 + 字节不变 + **无 `.derived.tmp` 残留**断言）、`test_recovered_files_are_accepted`（L1336-1347：recovered 放行）。reviewer round-2 探针独立复验：air 拒绝（`invalid_argument` + "ground" 文案 + 字节不变）、writing 拒绝（文案含 "finalized"、字节不变、零暂存残留）、recovered 发布成功、completed 文件仍发布成功——9/9 PASS |
| F2 | P3 | **CLOSED** | 计划文档 §3（L32）改为如实声明"直接使用 `h5py` 打开暂存副本写派生组……合法性依据 AGENTS.md §9 + 物理参数取自 `storage.rcscan_v2.dataset_contracts` 权威常量"；R7（L68）与 D5（L43）同步；§9 新增"收敛为 storage 公开 API"ADR 议题（L116）——文档与代码 L68 事实一致 |
| F3 | P3 | **CLOSED** | 三处恒真断言全部替换为真实对拍：越权载荷用例尾部整文件/摘要双不变（L1086-1087）、round-trip 用例 `written.raw_fingerprint == raw_column_fingerprint(path)` 与 `raw_column_digest(path) == raw_before`（L1130-1131，`raw_before` 于 L1097 捕获）；全仓 grep 无自比值断言残留 |
| F4 | P3 | **CLOSED** | `AttachmentReport.to_dict()`（L1458-1471）新增 `published` / `refused_reason` 两键；专测 `test_report_serialization_exposes_the_refusal_reason`（L1349-1361：拒发态 `published is False + refused_reason == "strict_validation"`、发布态 `refused_reason is None`） |
| F5 | P3 | **CLOSED** | `ProcessingRequest` docstring 明确"reuse 入口权威 history = `snapshot.history`，`request.history` 不参与（建议留空）"（L903-909）；reuse 分支注释（L1283）；行为无变更（round-1 已实测两种传法一致） |
| N1 | P3（新增） | **OPEN（合并时顺手可解，不阻塞）** | t4 登记"t4 的 inScope 不含 M06、未改状态行"，但仓库 diff 事实：M06 L340 实际已更新为含 t3 结论 + t4 闭合事实 + "等待 t5 定向复验"的文案。**文案内容本身准确且恰是 captain 需要的同步**，出入仅在 t4 changedPaths 声明（三路径）与实际触碰（四路径）不一致。属流程登记口径债：合并时把该行更新为 Done 最终口径（含 t5 PASS），并在合并说明中如实记录 t4+t5 的路径全集即可闭合。**不影响任何功能、数据或契约** |

## 4. 逐 Issue 验收矩阵（ISSUE-036，行号按 t4 修复后文件）

| # | 验收标准（M06 L359-363 原文） | 状态 | 精确代码证据 | 实际测试证据 | 问题/限制 |
|---|---|---|---|---|---|
| 1 | 「所有组合保持 raw，数据域/history 顺序正确」 | **PASS** | `PROCESSING_ORDER` 六 stage 冻结序（L165-172）；逐 stage `token.checkpoint`（L1305/1328/1345/1356/1378/1391）；core `_ALLOWED_TRANSITIONS`/`_START_DOMAINS` 兜底；输入不可变 | 14 参数化组合断言规范序限制/无重复/IFFT 恰一次/`final_domain==history 末域`；`test_history_first_record_always_consumes_frequency_raw`；raw 不变四组真实对拍（宽网格拒发、发布路径、取消、失败 attach）+ round-1 探针 25 项维持有效 | 无 |
| 2 | 「相同 profile 回放可安全复用 calibrated；错误/非空 raw history 拒绝」 | **PASS** | `_reuse_verified_snapshot`（L1160-1258）：032 `require_safe_reuse` + 033 `require_matching_calibration_provenance` 双权威委托 + digest/容器/通道/轴/数据/时戳逐项对拍；复用置 `calibration=None`（L1292）；fresh 空历史门（L1121-1135） | 四类错 profile 拒（field-level mismatches）；identical provenance 复用不二次校准；reuse==fresh bit-exact；快照 fail-closed；二次 OSL/背景拒绝（含绕过编排）；round-1 探针 P1i/P1j/P2/P9 维持 | 无 |
| 3 | 「time_base 总是 IFFT 基础，time_processed 仅在时域 stage 开启时存在」 | **PASS** | IFFT 无条件（L1361-1376）+ `archive_to_schema_grid` 幂等闸（L1616-1628）；`time_base` 必填 / `time_processed: …| None` | `test_time_base_is_always_the_ifft_output`；14 组合矩阵；display crop 不进 history 且不改 time_base；round-1 探针 P1g/P7 维持 | 无 |
| 4 | （范围项 L353）「结果写回/附加到 ground rcscan 的受控 storage 接口」 | **PASS（round-1 的 F1 门卫缺口已闭合）** | `DerivedAttachmentWriter`：⓪ role/lifecycle 门卫（L1678-1696）→ optional 白名单 → 契约参数取自 `dataset_contracts` → 行数=committed → preflight → 暂存副本 + 011 严格 reader 复核 + 原子 replace → 前后 raw 双指纹 | 成功路径（发布→严格 reader 通过→重放 bit-exact→再附着替换→raw 不变）；宽网格可观测拒发（字节不变）；越权载荷拒；**air/writing 拒 + recovered/completed 放行**（t4 新测 + round-2 探针 9/9）；round-1 其余 P1 探针维持 | D6(b) 网格互斥归剩余风险（§8），不阻塞 |

## 5. Git 与交付检查

- 分支/基线/同步：`main @ 487f9ad`，`origin/main...HEAD` = `0 0`；reflog 无历史重建迹象。
- t2+t4 均零 commit/push/merge（自动化轮由 captain 在 PASS 后执行合并推送）。
- 工作树恰为 6 项（t2+t4 四路径 + t1/t3 报告）；无范围外修改；无缓存/构建物/密钥/实测数据/参考文件混入；`git diff --check` 干净（M06 的 CRLF 提示为既有行尾属性，非本轮引入）。
- 单 Issue 单提交边界：M06 diff 仅 L340 一行（t2 写入 + t4 文案更新，见 N1 登记口径出入）。
- 公共契约零变更：`storage/core/processing/calibration` 零字节改动（t4 修复全部落在 orchestrator + 测试 + 文档三路径 + M06 一行文案内）；mission attrs 附加为 additive 且 011 严格 reader 实测接受。
- changedPaths 核对：t2 声明恰等 inScope 4 路径 ✓；t4 声明三路径但实际亦触碰 M06 L340 文案行（N1：内容正确、登记口径有出入）。

## 6. 测试与验证结果（round-2 独立复现，Python 3.13.14）

### 6.1 门禁

| 门禁 | 命令 | 结果 | 退出码 |
|---|---|---|---|
| 定向 | `./.venv/Scripts/python.exe -m pytest tests/contract/test_processing_orchestrator.py -q` | **53 passed** in 1.19s（无 skip/xfail） | 0 |
| 全量 pytest | `./.venv/Scripts/python.exe -m pytest -m "not hardware and not slow" -q` | **1449 passed / 4 deselected** in 267.90s（= 1396 基线 + 49 + 4 ✓） | 0 |
| ruff | `./.venv/Scripts/python.exe -m ruff check .` | All checks passed | 0 |
| mypy | `./.venv/Scripts/python.exe -m mypy src` | 56 source files clean | 0 |
| diff-check | `git diff --check` | 干净 | 0 |

### 6.2 round-2 反例/变异探针（系统临时目录，全部清理）

9/9 PASS，专注 F1 闭合与拒绝面：

| 探针 | 结果 |
|---|---|
| R1 air finalized 拒绝 | `DerivedAttachmentError[invalid_argument]`，文案含 "ground"，context 带 `file_role=air`；整文件 SHA-256 不变 ✓ |
| R2 writing partial 拒绝 | 拒绝发生在暂存之前；文案含 "finalized"；字节不变；`*.derived.tmp` 零残留 ✓ |
| R3 recovered 文件 | 门卫放行且发布成功；`to_dict()` 含 `published=True/refused_reason=None` ✓ |
| R4 completed 文件 | 仍发布成功（finalized 常规路径回归） ✓ |
| R5 拒发序列化 | 宽网格拒发态 `to_dict()["published"] is False` 且 `refused_reason=="strict_validation"` ✓ |

round-1 的 25 项探针（链序/双入口/revision 竞争/回放对拍/raw 不变/暂存冲突/谎报载荷等）针对的代码路径未被 t4 改动语义（t4 只增门卫与序列化字段，定向 53 用例全绿 + 上述针对性复验背书），结论维持有效。

### 6.3 既有 flaky 测试发现（与本 Issue 无关，如实登记）

首次全量复现出现 1 failed：`tests/contract/test_librevna_backend.py::test_close_interrupts_acquire`（`assert isinstance(errors[0], BackendClosedError)` 失败，实际收到 `DomainError(invalid_argument, "USB device disconnected during acquire")`）。**独立排查证据**：该测试为 ISSUE-023 交付的既有用例（最后提交 `0accd7b`，早于本 Issue，工作树对其零修改）；单测隔离复跑 8 次出现 2 次失败（~25% flake 率，close 与 acquire 线程对错误类型的赛跑）；该竞态与 orchestrator 无 import/状态关联（隔离运行不加载本 Issue 模块）。**第二次全量复现即全绿（1449 passed / exit 0）**，t4 报告的数字可复现。按 REVIEW_STANDARD §12"必要测试失败"评估：非必要、非确定性、非本 Issue 引入、复跑即绿——**不构成本批合并阻断**，登记为独立 P3 挂账（§10）。

## 7. 报告与事实差异

| t4 声明 | 核验结果 |
|---|---|
| 定向 53 passed / 全量 1449 passed+4 deselected / ruff / mypy(56) / import / diff-check | **全部复现**（§6.1；首次全量遇 flaky 一次、复跑全绿，§6.3） |
| F1-F5 全部闭合，先红后绿（3 failed → 53 passed） | **逐项证实**（§3：代码/测试/文档三面证据 + 探针 9/9） |
| "t4 的 inScope 不含 M06，因此未在本回合改状态行；若需要同步更新请在下回合把该路径纳入 inScope" | **与 diff 事实不符**：M06 L340 实际已含 t4 闭合文案（内容准确且有价值；出入是 changedPaths 登记口径而非功能问题）→ N1（P3） |
| "changedPaths 恰等 t4 inScope 三路径" | 因上一项，实际触碰四路径（N1 同源） |
| "D6(b) 维持剩余风险，本回合未改 schema/reader/stage" | **属实**：storage/core/processing 零修改 |

## 8. 剩余风险

1. **【首要，需 ADR，维持 round-1 裁定】派生时域网格 vs 011 reader 默认契约互斥（D6(b)）**：生产默认（oversampling=16、非二次幂频点）下附件必然被严格 reader 拒发（`strict_validation`，可观测、原文件零改动）。处置正确；解法（reader/creator 按实际轴长参数化 time_points，或 schema 可变长）留 M06 收尾后 ADR，影响 ISSUE-048。**合并本 Issue 不使现状变差**。
2. **频域派生历史非常规落点**（D6(a)，mission attrs）：011 兼容性实测通过；正规化同样待 ADR。
3. **HDF5 写面直连 h5py**（F2 后如实登记）：收敛为 storage 公开 API 属后续 ADR，防第二个直连点。
4. **ISSUE-023 flaky 测试**（§6.3）：`test_close_interrupts_acquire` 线程时序竞态（~25% 隔离失败率），非本 Issue 引入，建议独立挂账修复（P3）。
5. **线程宿主/实时增量/零时/连续背景**：维持 t2 §9 原文（M09 排期）。

## 9. 合并建议

**建议立即合并（M06 收尾）**：
- F1(P2) 完全闭合（最小修复方向落实 + 4 个新契约测试 + reviewer 探针复验 9/9）；F2-F5 同步闭合；三条验收 + 范围项全 PASS；门禁全绿可复现。
- N1（P3 登记债）不阻塞：合并提交中由 captain 把 M06 L340 更新为 Done 最终口径（含 t5 复审 PASS 事实），并在合并说明中如实记录 t4/t5 回合触碰的路径全集即可闭合。
- D6(b) 维持登记为 M06 收尾后第一条 ADR 议题（建议在合并说明中点名，避免 ISSUE-048 带缺口设计）。

## 10. 最小修复清单

| # | 等级 | 内容 | 时机 |
|---|---|---|---|
| N1 | P3 | M06 L340 状态行合并时更新为 Done 最终口径（含 t5 PASS）；t4 changedPaths 口径出入在合并说明补记 | 合并提交（captain） |
| — | P3（本 Issue 外） | ISSUE-023 `test_close_interrupts_acquire` 线程竞态去 flaky | 独立挂账 |
| — | ADR | D6(b) 时域网格 time_points 参数化 / 可变长 schema；频域 history 落盘位；HDF5 写面收敛 storage 公开 API | M06 收尾后 |

（round-2 审查结束，审查者停止，不改代码，等待 captain 合并决定。）
