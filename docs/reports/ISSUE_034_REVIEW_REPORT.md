# ISSUE-034 独立复审报告（t3）

- 审查者：AgentTeams `uav-gpr-issue-034-dewow` 成员 reviewer（只读角色）
- 审查日期：2026-09-05
- 审查标准：`docs/ISSUE_REVIEW_STANDARD.md` v1.0（§13 固定十节格式）
- 被审对象：t2 交付 ISSUE-034「Dewow 时域阶段」（M06 L264-299）；完成报告 = t2 任务输出 + `docs/plans/2026-09-05-issue-034-dewow.md` §9 执行日志
- 解释器：`.venv/Scripts/python.exe` = Python 3.13.14（Windows venv，全部门禁统一使用）

## 1. 审查结论

**PASS**（无 P0/P1/P2 问题；两条 P3 测试精度观察，不阻止合并）。

ISSUE-034 交付真实、完整、合规：`DewowStage` 沿最后 time 轴减 reflect 填充中心滑动平均（累积和 O(N)、complex128），窗口秒→样本舍入链、双重重复门、固定 dewow→flat 顺序、输入不可变、time_processed/history 输出全部满足 M06 验收标准。**复审最硬证据：迁移实现与冻结参考项目真实代码（本地只读副本，哈希实测等于 manifest 冻结值）在 kernel 级与完整 stage 级逐位 bit-exact**（含默认窗口 4e-9 s ↔ 参考 4.0 ns 等价）。全量门禁独立复现全绿。建议 captain 按自动化轮流程合并（PASS → 自动合并推送 → 进入 ISSUE-035）。

## 2. 自动识别的审查范围

| 项 | 实测值 |
|---|---|
| Issue | ISSUE-034（M06 L264-299；直接依赖 030/031 均 Done，t1 已实证） |
| 分支 / 基线 | `main @ 124fe56`（t2 无 commit/push，按 M06 提示词"报告并停止"设计；合并归 captain） |
| 交付物（inScope 4 路径） | `src/uav_gpr/processing/dewow.py`（新增 500 行）、`tests/contract/test_processing_dewow.py`（新增 1046 行 / 51 测试）、`docs/plans/2026-09-05-issue-034-dewow.md`（新增）、`docs/issues/M06_CALIBRATION_PROCESSING.md`（仅 L266 状态行 Planned→Review，diff 实测仅 1 行） |
| 工作树其余变化 | 仅 t1 交付物 `docs/reports/ISSUE_034_BASELINE_CONFIRMATION.md`（t1 任务产出，非 t2 范围外修改） |
| 参考源 | 本地只读副本 `D:\博士任务\rebar-inspector`：`dewow.py` SHA-256 `eb6690e7…30e2c`、`_time_stage_common.py` SHA-256 `e0c201b5…333c81`——**复审实测哈希与 manifest.json 冻结值、t1 登记、测试字面量三方相等** |

## 3. 主要问题（按 P0→P3）

无 P0 / P1 / P2。两条 P3（低风险测试精度观察，均不影响行为正确性）：

- **P3-1（测试夹具区分度）**：`tests/contract/test_processing_dewow.py` L622-625 的 jittered 轴夹具仅 3 点 / 2 个 diff，此时 median(diff) == mean(diff)，无法区分 dt 推导用 median 还是 mean——变异探针 M5（`np.median`→`np.mean`）在 51 测试下**存活**。实现本身正确（源码 L173 实测为 `np.median`，与参考 `_compute_dt` 一致且 A/B bit-exact）；仅夹具区分度弱于 D3 意图。修复方向：把 jittered 夹具扩到 ≥4 点 / ≥3 个非对称容差内 diff（如 [1, 1+4e-7, 1-4e-7]，median≠mean 且通过 1e-6 均匀性容差），断言仍钉 median。
- **P3-2（双重门变异遮蔽，设计使然）**：变异探针 M7（关闭 stage 层重复 dewow 门 dewow.py L399-408）在 51 测试下**存活**——因为 core `ProcessingHistory` 唯一性门（`core/time_domain.py` L576-586）在 append 时必然以同一错误码/消息再次拒绝，行为仍 fail-closed。这是 t1 契约 §3.3-4 明确的"core + stage 双重 fail-closed"设计（031 先例同构），非缺陷；如需独立钉死 stage 门，可加一条"stage 门先于数值工作触发"的路径断言（可选，非必须）。

## 4. 逐 Issue 验收矩阵（M06 L285-289 三条 + 范围/排除项）

| 验收标准 | 状态 | 代码证据（文件:行号） | 测试/探针证据 | 问题或限制 |
|---|---|---|---|---|
| complex 等价于 real/imag 独立处理，shape/axis 不变 | **PASS** | `dewow.py` L273 `np.cumsum(..., dtype=np.complex128)`（实虚部在复数累加内独立）；L245 dtype 守卫；L280 除法保持 complex128；L483-490 输出重建 shape/channels/axis 全量透传 | 测试 L467-476 `test_complex_linearity_real_imag_independent` 逐位断言；L944 dtype complex128；A/B 探针 §6：complex kernel == 独立 real/imag kernel bit-exact；§7 shape/axis/dtype 不变 | 无 |
| 短数组/超大窗口/边界行为固定 | **PASS** | `dewow.py` L199-217 舍入链（round→max(1)→奇化→==1 拒→>n_time 拒，消息含操作指引）；L154-159 轴 ≥2 点；L257-269 kernel 窗口奇数 ≥3 且 ≤ 轴长；L97 `DEWOW_PADDING="reflect"` 契约常量（L272 唯一使用处） | 测试 L628-648（round-half-even 2.5→3、3.5→5、偶化奇、恰等 n_time 通过）；L794-824（n_time=1 拒、0.4ns→1 样本拒、超大窗口拒、非均匀轴拒）；L450-464 脉冲 reflect 边界手算钉死；A/B 探针 §5 密集网格奇偶/范围不变式 + §8 拒绝矩阵；变异 M1/M3/M4 全 killed | P3-1 夹具区分度（见 §3） |
| 输入 time_base 不变，输出 history 正确 | **PASS** | `dewow.py` L363-367 非 scan TypeError；L381-396 域门（{TIME_BASE, TIME_PROCESSED}）；L423-433 kind/前驱一致性；L447-454 非有限拒；L460-465 只读视图相减产新数组；L467-481 `_record_for` 完整参数；L481-495 append + TIME_PROCESSED 重建 | 测试 L881-895 输入 bytes 前后全等 + 双向只读；L830-849 record 字段/参数/参考哈希全检；L852-859 to_dict/from_dict JSON 往返；L861-868 时钟注入/naive 拒；A/B 探针 §7（bytes/只读/新对象/单条记录）+ §10（参数含冻结参考哈希）；变异 M6/M8/M9 killed | 无 |
| 范围：窗口秒→样本明确舍入/奇数策略 | **PASS** | `dewow.py` L188-217 `window_samples_for` + L123-138 `window_s` 入场校验（bool/非实数/非有限/≤0 拒） | 测试 L425-432 非法 window_s 矩阵；L628-648 舍入链逐条；A/B 探针 §3/§4（与参考 `_compute_window_samples` 全 stage bit-exact）+ §5 密集网格 | 无 |
| 范围：reflect 边界、O(N) 算法 | **PASS** | `dewow.py` L270-280 累积和 O(N)（pad→cumsum→差分→除），无逐窗 Python 循环 | 测试 L983-997 性能 smoke；A/B 探针 §9：512×2×1024 apply 0.032 s，8 倍数据比 8.3（线性）；§1 kernel 与参考 bit-exact | 无 |
| 范围：time_base/time_processed 输入规则、重复 stage 保护 | **PASS** | `dewow.py` L118-120 输入域集；L398-408 stage 重复门；L410-421 flat 顺序门；core `time_domain.py` L576-586 唯一性第二道（bump version 不绕过） | 测试 L669-676 空 history 拒；L697-713 频域末域拒；L727-746 双门（含 version="99.0" 隔离探针）；L749-761 flat-before-dewow 拒；L764-776 合法 TIME_PROCESSED 前驱通过；变异 M7 存活属双重门设计（P3-2） | P3-2（见 §3） |
| 范围：黄金样本和性能 smoke | **PASS** | 测试 L251-386 内嵌黄金字面量（场景 A/B/C）+ L584-601 canonical digest SHA-256 钉死 | **A/B 探针 §2：黄金字面量由冻结参考真实代码重算逐位复现**；§1/§3 kernel 与全 stage bit-exact（8 缓冲 × 5 窗口 + 8 完整 stage 案例 + 默认窗口） | 无 |
| 排除项：不实现 Flat / UI | **PASS** | `dewow.py` 无 flat/edge 符号（L97 注释明确 edge 属 035）；无 Qt/storage/acquisition import | 测试 L1003-1032 AST import 守卫；L1035-1046 公共面无 flat/display/crop/depth/velocity 符号；复审源码全文人工核读 | 无 |

## 5. Git 与交付检查

- HEAD `main @ 124fe56`，`git rev-list --left-right --count origin/main...HEAD` = `0 0`（与 t1 基线一致；033 推送链闭合未被破坏）。
- reflog 仅含既有 033/032 合并链，**无 reset / rebase / amend / 强推痕迹**；无 stash。
- t2 未 commit/push——符合 M06 提示词"运行门禁，报告并停止，不 commit/push"；合并推送归 captain（自动化轮设计，031/033 同构）。
- changedPaths 与 t2 inScope 4 路径逐一相等（实测 `git status --porcelain`：M06 状态行 + 3 新增；`docs/reports/ISSUE_034_BASELINE_CONFIRMATION.md` 为 t1 交付物）。无范围外修改、无缓存/日志/构建物/密钥/实测数据入库（`.pytest_cache`/`__pycache__` 均为已忽略项）。
- t1 登记的契约文件哈希复测全部相等（bandpass `f707839674ceb5e1`、time_domain `b7da55717148645b`、osl `30224c9a0091c02b`、background `a96d59f63289a8c8`、PROCESSING.md `9d730ec7a0f7c223`）——**依赖交付物未被触碰**。
- 无公共 schema/协议/架构契约变更（新增模块复用 030/031 冻结契约面，不新增错误码）。

## 6. 测试与验证结果（全部本机独立复现）

| 命令（解释器 = `.venv/Scripts/python.exe`，Python 3.13.14） | 退出码 | 结果 |
|---|---|---|
| `-m pytest tests/contract/test_processing_dewow.py -q` | 0 | **51 passed** in 0.15s |
| `tools/quality/verify.py` | 0 | **1342 passed / 4 deselected in 272.11s** + ruff `All checks passed!` + mypy `Success: no issues found in 54 source files` + `package import ok` + `[quality] all gates passed` |
| `-m ruff check src tests` | 0 | `All checks passed!` |
| `-m mypy src` | 0 | `Success: no issues found in 54 source files`（53→54 为新模块预期变化） |
| `-c "import uav_gpr; import uav_gpr.processing.dewow"` | 0 | import ok（`dewow 1.0 4e-09`） |
| A/B 对拍探针（系统临时目录，参考真实代码 vs 迁移代码） | 0 | **全部 PASS**：① kernel bit-exact（A/B/C + 5 随机缓冲 ×5 窗口）；② 黄金字面量由冻结参考代码重算逐位复现；③ 完整 stage A/B bit-exact（8 案例 + 默认窗口 4.0ns↔4e-9s 等价）；④ 舍入链密集网格不变式（奇/≥3/≤n）；⑤ 朴素 reflect 索引循环与 kernel bit-exact；⑥ complex==独立 real/imag；⑦ 输入 bytes/只读/history 不变 + 输出新对象只读 + 单条记录；⑧ 拒绝矩阵 10 项全拒（重复/频域/flat 逆序/NaN/Inf/窗口 1/超大/单点轴/非均匀轴/非 scan TypeError）；⑨ 性能 0.032s + 线性比 8.3；⑩ record 携带冻结参考哈希与完整可复现参数 |
| 变异探针（exec 注入变异源码 → 跑 51 测试，期望 ≥1 失败） | — | **9 变异：7 killed / 2 survived**。M1 reflect→edge：5 failed；M2 cumsum 差分换序：8 failed；M3 奇化跳过：14 failed；M4 min 钳位 1→2：2 failed；M6 非有限门关闭：1 failed；M8 flat 顺序门关闭：1 failed；M9 kind 透传：16 failed。存活：M5 median→mean（P3-1）、M7 stage 重复门关闭（P3-2，core 门仍拒绝） |

探针全部置于系统临时目录（`D:\dsh\windows\test-0.1.2-rc.1\temp\dewow_t3_probe\` 与 WSL `/tmp`），仓库零污染；复审前后 `git status --porcelain` 一致（本报告为唯一新增文件）。无硬件依赖项。

## 7. 报告与事实差异

逐项核对 t2/计划声明：全部与仓库事实一致，**无差异**。

- 声称 51 测试 → 实测 51 passed ✓；声称 verify 1342 passed/4 deselected（271.85s）→ 实测 1342/4（272.11s，机器噪声级差异）✓；声称 ruff/mypy(54)/import 全绿 → 实测复现 ✓。
- 声称 changedPaths == inScope 4 路径 → 实测逐一相等 ✓；声称 M06 L266 → Review → diff 实测仅该行 ✓。
- 计划 §9 执行日志声称的黄金 digest `e5e24861…31b8` → 测试 L584-600 运行通过 ✓；声称参考哈希入 record parameters → 探针 §10 实测 ✓。
- 计划 D9③ 声称"朴素转写 bit-exact、convolve rtol=1e-12" → 测试 L553-581 运行通过 + 探针 §5 复验 ✓。
- 声称"输入不变" → 探针 §7 bytes 级复验 ✓。
- 计划 §9 口径修正记录（默认窗口 W=5、reflect t=0 双计、位级缓冲、ruff Mapping）均与代码/测试一致 ✓。
- 时间线声明（t1 attempt ada20505、t2 attempt dde4227e、captain attempt 2 接管）无法从 Git 独立验证（未 commit，符合流程设计）——**未发现反证**。

## 8. 剩余风险

- P3-1 / P3-2（§3）：纯测试精度观察，行为已由 A/B bit-exact 与源码人工核读证明正确；可留待 035 或后续测试任务顺手关闭，不阻止合并。
- 平台口径：黄金字面量与 A/B 均在 Windows CPython 3.13.14 / numpy 本 venv 复现；跨平台浮点顺序差异已由计划 D9③ 的容差策略（dyadic bit-exact + 一般缓冲 rtol=1e-12）诚实覆盖，无新风险。
- 本 Issue 交付为工作树未提交状态，合并动作（feat/issue-034 分支 + merge + push）由 captain 在 PASS 后执行——合并前需保证工作树仍为本报告所见状态。

## 9. 合并建议

**建议合并（PASS）**：M06 三条验收标准全部 PASS，范围/排除项全部 PASS，门禁独立复现全绿，迁移数学与冻结参考真实代码 bit-exact，变异测试 7/9 killed（2 存活均为 P3 级非行为缺陷）。captain 可按自动化轮流程执行合并推送，随后将 M06 L266 状态行 Review→Done 并进入 ISSUE-035（Flat Reflection，沿 trace 轴——035 需注意 034 已内嵌 `_centered_moving_mean` 的 edge/trace 轴变体评估，计划 D5 已预留该决策点）。

## 10. 最小修复清单

无阻止合并项。可选（P3，非本轮必做）：

1. （P3-1）扩展 `test_derive_sample_interval_rules` 的 jittered 夹具至 ≥3 个非对称容差内 diff，使 median 语义可被变异级钉死。
2. （P3-2，可选）如需独立钉死 stage 层重复门：加断言该门先于数值工作触发（或错误上下文区分），否则维持双重门设计现状即可。

——审查结束。审查者全程只读（除本报告文件外未修改任何仓库文件/Git），探针与日志已清理（系统临时目录），等待 captain 决定合并。
