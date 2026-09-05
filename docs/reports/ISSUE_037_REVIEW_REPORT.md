# ISSUE-037 独立复审报告（t5 · round 2，覆盖 t4 修复验证）

日期：2026-09-05 · 审查者：reviewer（xkiro/z-ai/glm-5.3，只读）· 依据：`docs/ISSUE_REVIEW_STANDARD.md` v1.0 §13 固定十节
复审脉络：t3 首轮复审 = PASS WITH CONDITIONS（P2-1 合并门槛 + P3×3，见附录 A 摘要）；t4 修复回合闭合全部四项；本轮（t5）验证修复与整体可合并性。

## 1. 审查结论

**PASS**

- t4 修复全部真实闭合：**P2-1**（trace 路径 `DomainError` 逃逸 → 整函数体包裹 `_decode_trace_inner`，poisoned 契约恢复，探针 8 例独立复现）；**P3-1**（envelope bool-as-int 显式拒绝）；**P3-2**（`golden_messages()` 纯化 + 副作用显式拆出 + docstring 登记）；**P3-3**（ADR 补通道合同前提句）。
- 黄金帧字节跨轮**逐字节不变**（四帧尺寸/载荷与 t3 轮实测完全一致），合法帧语义零改动，既有 57 项测试无削弱（关键断言逐项抽查）。
- 门禁独立复现全绿：定向 **68 passed**（= t2 57 + 修复新增 11）、全量 **1517 passed / 4 deselected**（= t2 基线 1506 + 新增 11）、ruff、mypy(57 files)、import、`git diff --check`。
- M07 ISSUE-037 三条验收**逐项 PASS**（§4）；**无新增 P0/P1/P2/P3 合并门槛项**。
- 唯一登记项：t4 任务登记 `changedPaths` 取 inScope 声明 2 路径，实际修改 4 文件（差异已由 t4 如实报告；全部落在 ISSUE-037 交付面内，判定为登记口径问题非范围违规，详见 §7）。

## 2. 自动识别的审查范围

| 项 | 识别结果（t4 完成报告 + Git + 仓库实测交叉验证） |
|---|---|
| Issue | ISSUE-037（`docs/issues/M07_TRANSPORT.md` L5-40，M07 L7 状态 `Review`） |
| 基线 | `main @ dc5d697`；`origin/main...HEAD = 0 0`（实测）；全程未 commit/push（待 PASS 合并） |
| 审查对象 | t4（repair-round-2）修复 + t2 原交付复核（修复触点回归） |
| 交付物 | 工作树文件集合与 t3 轮完全一致（6 项，无新增游离文件）：`protocol_v1.py`（2224 行 / 84,809 字节，t4 +44 行）、`test_protocol_v1.py`（1085 行，+88 行）、`ADR-0006`、`docs/plans/2026-09-05-issue-037-protocol-v1.md`（新增 §8 t4 日志）、`M07_TRANSPORT.md`（L7）、t1 基线单 + 本报告 |
| 工作树 | `git status --porcelain` 与 t3 轮一致；`git diff --check` 干净；reflog 无 reset/rebase/amend；无 stash |

必读资料：t3 轮已全部阅读（AGENTS/README/TRANSPORT_PROTOCOL/adr README/M07 L5-40/t1 基线单/t2 计划/ADR-0006/审查标准）；本轮补读 t4 计划 §8 执行日志与全部修复触点代码。

## 3. 主要问题（P0→P3）

**无未闭合问题。** t3 轮四项发现全部闭合（逐项验证）：

| 原发现 | 闭合验证（文件:行号 + 独立探针） |
|---|---|
| P2-1 trace 路径 DomainError 逃逸、绕过 poisoned 契约 | `protocol_v1.py` L1537-1547：`_decode_trace` 整函数体包裹 `_decode_trace_inner`（比建议的最小两处包裹更完整，覆盖 `_trace_context`/`_recover_axis`/构造全部逃逸面）+ 内层 L1569-1572/L1591-1592 双保险；`feed()` 既有 FrameError-poison 逻辑自动生效。探针 8 例 malformation（channel_ids=str/frequency_points=str/dtype=int/shape=str/raw_trace_sha256 非 hex/config_sha256=null/metadata=str/frequency_start_hz=str）全部 `FrameError` + `poisoned=True` + poisoned 持续至 reset + reset 恢复；合法 trace 逐字节 feed 回归通过。仓库测试 `test_malformed_trace_header_fields_fail_closed_with_poison`（L1019-1049，8 参数化）+ 点名复现（L1052-1060）+ 回归守卫（L1063-1067） |
| P3-1 envelope bool-as-int 宽松 | `protocol_v1.py` L1416-1425：`isinstance(actual, bool)` 显式拒绝；测试 L1070-1085。探针：major=true 与 minor=false 均被拒且 poisoned；合法帧往返不受影响 |
| P3-2 GOLDEN_FRAMES import 注册副作用 | `protocol_v1.py` L2097-2157：`golden_messages()` 拆为纯构建器（注释 L2106-2110 显式声明纯度）；注册移至 `_register_golden_contracts()`（L2161-2163）于 L2173 显式调用；模块 docstring L42-48 "Global state" 段登记副作用与安全论证。探针：连续调用 `golden_messages()` 注册表零变化（纯度验证）；import 种子恰好两个 fixture id |
| P3-3 ADR 复算前提缺失 | `docs/adr/0006-protocol-v1-framing.md` L57：Trace 红线段补「**前提**：复算要求接收端已注册通道合同（ChannelSpec 集）——经 mission config 绑定或独立注册满足；未注册时 fail-closed 并明确指路」 |

## 4. 逐 Issue 验收矩阵（M07 ISSUE-037，L26-30 三条验收，修复后行号）

| 验收标准 | 状态 | 代码证据（文件:行号） | 测试/独立验证证据 | 问题或限制 |
|---|---|---|---|---|
| 1. ADR 记录候选、选择与后果；codec 跨进程确定 | **PASS** | `ADR-0006` L10-15（四候选+拒绝理由）、L17-65（帧布局/消息族/trace 红线含 L57 前提句/版本协商）、L67-74（后果）；`protocol_v1.py` L2174（GOLDEN_FRAMES） | 仓库测试 `test_cross_process_encoder_determinism`（`-S` 子进程三方逐字节相等）；探针：独立子进程脚本重编码四方 hex + fresh 进程 GOLDEN_FRAMES **逐字节相等**；黄金帧尺寸跨 t3/t5 轮不变（272/273/1345+128/331） | 黄金帧 import 时计算而非字面量 hex（同解释器钉死；非本条验收要求，§8） |
| 2. parser 在读 payload 前验证上限，损坏不无界分配 | **PASS** | `protocol_v1.py` L1992-2006（`feed`：先验两长度上限再累积 body）、L1903-1914（`_decode_bytes` 同检）；常量 L72-73 | 探针：`payload_length=0xFFFFFFFF` 在仅 18 字节前缀时立即拒绝（pending=18）；`header>MAX` 拒绝；500 次恶意 feed tracemalloc 峰值 4174 字节；仓库测试 L373-393 | 无 |
| 3. unknown minor 能按能力策略处理，major 不兼容拒绝 | **PASS** | `protocol_v1.py` L1274-1296（`CapabilityPolicy.accepts_minor`）、L1377-1382（major≠1 ⇒ `UNSUPPORTED_PROTOCOL_VERSION` + observed/supported context）、L1388-1394（minor 超窗拒绝） | 探针：major 0/2/255 二进制前缀路径拒绝（UNSUPPORTED_PROTOCOL_VERSION + context.major）；header-lie-only 路径被 envelope 冗余校验拒绝；minor=255 默认接受；narrow policy 拒绝 minor=1；仓库测试 L426-452 | 无 |

范围/排除项复核：八类不可变消息、framing 契约、trace 红线、无 socket/heartbeat/outbox（探针 + tests L972-980）全部维持 t3 轮 PASS 判定；修复未触碰任何合法帧语义。

## 5. Git 与交付检查

- 分支/基线/远端同步与 t3 轮一致（`main @ dc5d697`，`0 0`，无 stash，reflog 干净）；t4 未 commit/push（符合协议）。
- t4 实际修改 = `src/uav_gpr/transport/protocol_v1.py`、`tests/contract/test_protocol_v1.py`、`docs/adr/0006-protocol-v1-framing.md`、`docs/plans/2026-09-05-issue-037-protocol-v1.md`（§8 执行日志）——全部为 ISSUE-037 t2 交付面（inScope 5 路径 ∪ 计划文档）子集，**无范围外蔓延**。
- 登记差异见 §7；交付物内容纯代码/文档，无缓存/日志/密钥/实测数据/参考仓库文件。
- 修复有依据（t3 审查报告 §3/§10 最小修复清单），ADR 与实现同步修订（P3-3）。

## 6. 测试与验证结果（审查者独立复现）

解释器：`.venv/Scripts/python.exe` = **Python 3.13.14**（Windows venv）。

| 命令 | 结果 | 退出码 |
|---|---|---|
| `pytest tests/contract/test_protocol_v1.py -q` | **68 passed** in 0.27s（= t2 57 + t4 新增 11 ✓；53 个测试函数含 8 参数化展开） | 0 |
| `tools/quality/verify.py` | pytest **1517 passed / 4 deselected in 281.87s**（= t2 基线 1506 + 新增 11 ✓，无 flaky 命中）+ ruff "All checks passed!" + mypy "Success: no issues found in 57 source files" + package import ok + `[quality] all gates passed` | 0（后台作业完成状态 exit code 0 + `verify.py` L57-71 run_gates 语义双重证明） |
| `ruff check .` / `mypy src` / `git diff --check` | All checks passed / Success 57 files / 干净 | 0 / 0 / 0 |

**round-2 独立探针 48/48 全 PASS**（仓库外 `D:\tmp` 执行、运行后删除）：

- **P2-1 闭合验证 8 例**：全部 malformation → `FrameError` + `poisoned=True` + poisoned 持续（后续 feed 仍拒）+ reset 恢复；合法 trace 逐字节 feed 回归；
- **P3-1**：major=true/minor=false 拒绝 + poisoned；合法帧不受影响；
- **P3-2 纯度**：`golden_messages()` 连续调用注册表零变化；import 种子恰为两个 fixture id；
- **黄金帧不变性**：四帧尺寸域与 t3 轮实测逐字节一致；trace payload == `golden_raw_data` canonical 字节；进程内重编码 == GOLDEN_FRAMES；
- **核心对抗面回归**：200 次随机切分重组、恶意长度（pending=18、500 次峰值 4174 字节）、未知 type×3、major×3 双路径（二进制前缀 + header-lie）、minor 策略双测、非 canonical 七变体、pickle/NPZ magic 拒绝、trace 篡改/伪造轴戳/短 payload/未注册通道 fail-closed、独立子进程跨进程确定性三方逐字节相等。

既有测试无削弱抽查：bounded-buffer 断言（L393）、`-S` 子进程 + timeout（L583-599）、46 处 `pytest.raises` 均在。

## 7. 报告与事实差异

| 声称 | 核验结果 |
|---|---|
| t4：68 定向 / 1517 全量 / ruff / mypy(57) / import 全绿 | ✓ 全部独立复现（数字逐字一致） |
| t4：P2-1 修复 + 三项 P3 顺带 + 先红（7 failed）后绿 | ✓ 代码/测试/ADR/日志四处交叉一致；「先红」过程声明无法事后逐字复现（交付未 commit），**未发现反证** |
| t4：黄金字节零改动 | ✓ 探针跨轮不变性验证 |
| t4：`changedPaths` 登记 2 路径（inScope 声明），实际修改 4 文件（tests + 计划文档未计入） | **登记差异**（t4 已如实报告）：登记口径未覆盖实际修改全集。判定：非范围违规——4 文件全部在 ISSUE-037 交付面内且均为 t3 修复方向（requiredFix 明确要求新增负面测试；计划日志为团队流程惯例）；影响仅限 captain 合并登记时应以实际 4 文件为 merged-changes 清单。**P3 级、不设门槛** |
| t4：`/tmp/verify_037_t4f.log` 零遗留 | 仓库内实测零遗留 ✓；仓库外日志无法事后核验——未发现反证 |

## 8. 剩余风险（均不阻止合并）

1. 黄金帧为 import 时计算：同解释器跨进程确定性已证，解释器/numpy 大版本漂移不被现有测试钉死（建议未来在测试中固化字面量 hex 或其 SHA-256；非本 Issue 验收要求）。
2. 模块级注册表无锁：多线程并发注册/解码理论竞态；本 Issue 明确无线程（tests L972 断言），ISSUE-038 读写 worker 需自行串行化（docstring Global state 段已登记副作用语义）。
3. exact-key-set 严格性演进约束（同 t3 轮）：未来 minor 新增字段需新 MessageKind 或新 ADR——fail-closed 方向正确。
4. `AckState`/`AckResult` 命名相邻易混淆（wire 无影响，nit）。

## 9. 合并建议

- **建议合并**：三条验收全 PASS、P2/P3 全部闭合、黄金字节不变、门禁全绿、范围/Git 边界干净。
- 合并动作建议：(a) `ADR-0006` 状态 Proposed → Accepted（其 L3 自述「项目负责人/t3 复审确认前不得视为最终决定」——本轮 PASS 即其设定的确认点）；(b) M07 L7 状态行 Review → Done（引用本报告）；(c) 合并登记的 changed-paths 清单采用**实际修改文件全集**（protocol_v1.py、test_protocol_v1.py、ADR-0006、计划文档、M07 状态行 + t1 基线单/本报告两份 docs/reports 交付），勿沿用 t4 登记的 2 路径口径。
- 本轮无新增修复项；§8 风险为后续 Issue 输入。

## 10. 最小修复清单

**无（合并门槛清零）。** 可选后续（不设门槛、可留待后续 Issue）：
1. 黄金帧字面量 hex（或其 SHA-256）钉入测试，防解释器大版本漂移（§8-1）；
2. ISSUE-038 并发边界串行化注册表访问（§8-2）。

---

### 附录 A：t3 首轮复审发现摘要（已被 t4 闭合，存档）

- P2-1：trace 路径字段级校验错误以 `DomainError` 逃逸 `FrameParser.feed`，未按 ADR-0006 转 `FrameError`+poisoned（三实例复现）→ t4 整函数体包裹闭合（§3）。
- P3-1：envelope 冗余校验 `!=` 比较 bool-as-int 宽松（True==1 通过）→ t4 显式 isinstance 拒绝（§3）。
- P3-2：`GOLDEN_FRAMES` import 副作用预置全局注册表 → t4 纯化 + 显式拆出 + docstring 登记（§3）。
- P3-3：ADR-0006 L57「不持有 config 复算」未写明通道合同注册前提 → t4 补前提句（§3）。

——审查结束。审查者未修改任何 t2/t4 交付物与 Git 状态；探针脚本已从临时目录删除。ISSUE-037 可合并。
