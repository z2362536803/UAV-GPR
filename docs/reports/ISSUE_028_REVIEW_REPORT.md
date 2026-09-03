# ISSUE-028 独立复审报告（OSL/空采无 UI 参考采集服务）

- 审查日期：2026-09-02
- 审查者：AgentTeams `uav-gpr-issue-028-osl-reference` 成员 reviewer（任务 t3，attempt 40724bd6-dda4-44ef-a7da-442f005e841d）
- 被审交付：t2 完成报告（attempt 59969921-cc3a-4b99-aa94-8d7c23478bb6，captain 接管 attempt 9 终态登记）+ 工作树 4 个 inScope 路径 + 计划文档（含执行日志与过程注记 §7/§7.1）+ M06 状态行
- 审查标准：[docs/ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) v1.0；基线件：[docs/reports/ISSUE_028_BASELINE_CONFIRMATION.md](ISSUE_028_BASELINE_CONFIRMATION.md)（t1，main @ 56c2f0f，门禁 1086 passed/4 deselected）
- 审查全程只读：未修改任何实现/测试/计划/文档/M06/Git 状态；本报告为唯一新增文件；探针全部在系统临时目录（`C:\Windows\Temp\issue028-review-probes`，Windows venv 解释器专用）运行并已 `rm -rf` 清理，项目内零残留（审查前后 `git status` 完全一致）。

## 1. 审查结论

**VERDICT: PASS**

ISSUE-028 的 3 条验收标准（M06 L63–67）与提示词全部强制项满足，无 P0/P1/P2 级问题。t2 交付真实、完整、合规，可进入人工验收（staged 流程：项目负责人授权合并后标记 Done）。发现 2 项 P3 级非阻塞建议（§3），不阻止合并。

独立复核要点：① 全量门禁独立复跑 verify.py exit 0 = pytest **1100 passed / 4 deselected**（= t1 基线 1086 + 新增 14，算术一致）+ ruff 全绿 + mypy 48 文件 + import ok；定向 14 passed 复跑 3 次稳定（3.15–4.16s）；② 探针实证 OSL 委托 I027 求解数值逐位一致（directivity/tracking/source_match 与直接调 `build_osl_calibration` `array_equal` 全 True，profile_id 各自新生成）；③ 双通道行绑定正确（S22 profile 的 open/short/load 均取自步骤 sweep 的 row 1，负对照不匹配）；④ 通道错序/子集/错 ID、axis 点数/数值错配全部 fail-closed 且计数不变；⑤ 后续步骤失败重试实证保留前序步骤数据；⑥ 无 `.rcal/.rcbg`/Qt/文件写入路径，无 `time.sleep`。

## 2. 自动识别的审查范围

| 项 | 识别结果 | 证据 |
|---|---|---|
| Issue | ISSUE-028「OSL/空采无 UI 参考采集服务」（M06 L42–77，状态行 `Review`） | M06 L44：`Review（2026-09-02 实现与测试完成，待独立复审…）`；README.md L95 依赖 015,027 |
| 基线/分支 | 无独立分支；工作树基于 `main` @ `56c2f0f`（HEAD == origin/main 0/0），未 commit/push/merge | `git log --oneline -2`；`git status --porcelain=v1 -b` |
| 工作树改动 | t1 交付物（`docs/reports/ISSUE_028_BASELINE_CONFIRMATION.md`，??）+ t2 的 4 个 inScope 路径：`src/uav_gpr/calibration/reference.py`（??，722 行）、`tests/contract/test_calibration_reference.py`（??，495 行/14 测试）、`docs/plans/2026-09-02-issue-028-osl-reference.md`（??，83 行）、`docs/issues/M06_CALIBRATION_PROCESSING.md`（M，diff 仅 L44 状态行 1 处） | `git status`；`git diff --stat` = 1 file +1/-1 |
| inScope/changedPaths 一致性 | t2 登记 changedPaths 4 条与 inScope 4 条逐一相等，与工作树实测一致（基线单为 t1 交付物，正确排除在 t2 inScope 外） | 任务契约 vs git status |
| 直接依赖 | ISSUE-015（`2f11cd9` 合并，SimulatedBackend 28 测试）与 ISSUE-027（`a2f65c6`+`4f2e1d3`+`56c2f0f`，osl.py 31 测试）均已在 main；reference.py 只读消费 `AcquisitionController`/`SimulatedBackend`/`build_osl_calibration`/core 枚举与错误码，接口签名实测匹配 | reflog；grep controller/backend/osl 公共 API；依赖定向（t1 §2 实测 59 passed） |
| 测试声明 | 定向 14 passed（复跑 3 次）；全量 verify.py exit 0 = 1100 passed/4 deselected；ruff `All checks passed!`；mypy `Success: no issues found in 48 source files`；import ok；diff-check clean | §6 独立复跑 |
| 过程异常 | t2 由 captain 接管（engineer 上下文耗尽停摆、替换模型首条命令挂死），7 类缺陷修复记录于计划 §7/§7.1，过程透明 | 计划文档 L61–83 |

## 3. 主要问题（按 P0→P3）

无 P0/P1/P2。P3（非阻塞）如下：

- **P3-1 [src/uav_gpr/calibration/reference.py L634–708] 空采会话无失败预算 → 持续设备错误时无界热重试**：`_SessionBase.record_step_failure` 对 `AirBackgroundSession` 是 no-op（L349–356）；`ControllerReferenceAdapter.run` 在 controller FAILED 时无差别 `factory()` 重建。探针实证：`SimulationFaults(timeout_at=(0,))` 持续失败 + 空采会话 → 6 秒观测窗口内工厂被调用 **37,659 次**（CPU 满转热循环；真实设备上等于无限重连风暴）。取消可干净恢复（state=cancelled、join=True、无线程泄漏），不破坏数据与状态机；且空采会话的「步骤失败按规则重试」验收原文绑定 OSL 六步语义（M06 L63-65「步骤」），空采无步骤，属规格未覆盖路径而非违约。**最小修复方向**：`AirBackgroundSession.record_step_failure` 也计数（复用同一 `max_retries` 或构造参数 `max_failures`），超预算 `_fail_locked()`；adapter 分支已统一走 `session.state is FAILED → break`，无需改动。
- **P3-2 [docs/plans/2026-09-02-issue-028-osl-reference.md L79 / README 状态行] 计划文档门禁数字未终态补录**：§7 修复 4 后写「全量门禁：verify.py 数字见下（终态登记时补录）」，但文档其后无 verify.py 实测数字（1100 passed/4 deselected 仅存在于 t2 完成报告，未落盘计划文档）；验收项「计划文档含门禁数字」以 t2 完成报告数字 + 本报告 §6 独立复跑共同满足，判 PASS，但计划文档自身缺口按标准 §10 应记录。**最小修复方向**：计划 §7 末补一行「verify.py exit 0 = 1100 passed / 4 deselected（1086 基线 + 14 新增）+ ruff + mypy(48) + import 全绿」。

（审查者注：曾评估「`_GATE_POLL_TIMEOUT_S = 0.05` 轮询」「重试测试 15s 有界截止轮询」是否违反「禁固定 sleep」——前者是带超时的条件等待（BoundedSweepBuffer.get），后者是 join(0.05)+monotonic 截止的事件驱动轮询，均非固定 sleep，且模块内零 `time.sleep`；判定合规。另曾评估 `calibration -> acquisition` 跨域导入——AGENTS.md §9 依赖方向允许 `application -> acquisition/calibration`，未禁止 calibration 消费 acquisition；ARCHITECTURE.md 分层图将两者列为平级领域层，且 controller adapter 是该会话的显式设计要求（「若接 controller，复用现有采集循环」），core 隔离未被触碰（core 不被反向依赖）；判定合规。）

## 4. 逐 Issue 验收矩阵（ISSUE-028，M06 L63–67 原文）

| # | 验收标准 | 状态 | 代码证据 | 测试证据 |
|---|---|---|---|---|
| 1 | 状态机不允许跳步/混配置；步骤失败可按规则重试/保留前序 | **PASS** | 无 skip 方法（模块零入口）；六步序 = 通道序×(open,short,load)（reference.py L396–404）；步骤仅在目标道数收齐时自动推进（L460–486）；`build()` 前置校验 COMPLETED（L490–497，kind=incomplete_steps）；accept_sweep 严格校验 channels 全字段/axis 逐点/dtype/shape/有限（L297–339）；构造冻结 config/axis/channels/target（L179–193、L365–404）；重试预算 `record_step_failure`（L428–440）保留已完成步骤与 captures | test L221–228（跳步 build 拒绝）、L285–307（错轴/非有限拒绝且计数不变）、L341–383（重试：工厂恰 2 次、failure≥1、终态 COMPLETED）、L386–414（预算耗尽 fail-closed）；探针 B（通道错序/子集/错 ID 拒绝，计数 0）、探针 C（axis 点数错配拒绝）、探针 F（**后续**步骤失败保留前序 open 行数据，均差=0.0） |
| 2 | 目标道数收齐后先关接受门，再安全停止 controller | **PASS** | 步骤收齐/目标达成即关 gate（L468–472、L597–599）；adapter 终局 `_shutdown_controller`：先 `close_gate`（若仍开）→ events 记录 gate_closed → `stop()` → `wait_finished(10s)` → `close()`（L710–722）；adapter 循环首查会话终态（L670–675） | test L269–277（事件序 `gate_closed` < `controller_stopped`，末事件 `controller_closed`，join=True，state=closed）、L315–333（目标达成后 gate 闭、晚到 sweep accepted=False）；探针 F 事件序同样 gate_closed 先于 controller_stopped |
| 3 | 取消/设备错误无线程泄漏，不伪造标准件 | **PASS** | `cancel()` 线程安全关 gate+终态（L239–254）；adapter finally 兜底 stop/close（L701–708）；全部等待为 Condition/Event/join；零文件 I/O、零 Qt、测量值全部来自 accept_sweep 实际接受数据（L451–458 仅存引用；`__init__` 只存 Cal Kit 理想值缺省，不生成测量）；无线程创建（线程归 controller/调用者） | test L404–450（取消后 join=True、accept 拒绝、controller join 全 True）、L386–414（设备错误超预算 join=True）；探针 E（空采持续错误：cancel 后 join=True 状态 cancelled，无线程泄漏——但暴露 P3-1 无界热重试）；探针 G（双通道行绑定正确=不伪造通道归属） |

提示词附加强制项（M06 L69–77 逐项）：无 UI（✓ 零 Qt）；冻结 sweep config/channel/axis/目标道数（✓ L179–193）；accept_sweep 严格聚合（✓ L282–339）；委托 I027 求解（✓ L490–526 仅调 `build_osl_calibration`，零求解数学复制——探针 C 实证数值与直调逐位一致）；重试/取消/错误/步骤保留（✓ 矩阵 #1/#3）；复用现有采集循环（✓ adapter 只消费 controller.sweeps，未新建采集循环）；收齐先关门再 stop（✓ 矩阵 #2）；不保存 .rcal/.rcbg（✓ 零文件路径引用）、不做 Qt wizard（✓）、不自动切换标准件（✓ 无任何硬件控制路径，标准件接入为会话外人工动作）、不伪造数据（✓ 矩阵 #3）；SimulatedBackend 覆盖跳步/混配置/in-flight/重试/取消/资源关闭（✓ test L221–228/L285–307/L315–333/L341–414/L422–450）；禁固定 sleep（✓ 模块零 sleep，测试仅 monotonic 截止+join 轮询+Event 等待）。

范围项逐项（M06 L52–56）：OSL 六步状态机/步骤冻结/目标道数/重试取消（✓）；空采会话与 raw/osl_calibrated 域声明（✓ L541–577，缺 profile_id 构造即拒 L560–573）；accept_sweep 严格检查委托 I027（✓）；会话不拥有窗口、controller adapter 只编排（✓）。

排除项合规（M06 L59–61）：不保存参考文件（零 I/O）、不做 Qt wizard（零 Qt）、不自动切换物理标准件（无硬件控制）；git status 无 core/storage/backend/controller/scheduler/osl.py 改动（只读消费面未触碰）；`calibration/__init__.py` 未改。

## 5. Git 与交付检查

```text
branch                 main；HEAD 56c2f0f == origin/main（0/0）；reflog 无 reset/rebase/amend/filter（0 条）
工作树（审查前后一致）  ## main...origin/main + M M06_CALIBRATION_PROCESSING.md + ?? 4 文件（基线单/reference.py/test/计划）
git diff --check       clean（exit 0）
M06 diff               仅 L44 一行：- 状态：Planned → + 状态：Review（…待独立复审…），与流程要求一致（终态 Review；中间 In progress 记录于计划 §7 执行日志）
inScope 精确路径       4 条与 changedPaths 逐一相等；无范围外文件、无 glob、无缓存/日志/密钥/实测数据混入
未授权操作             无 commit/push/merge/branch/stash（git stash list 空；branch 列表无新增）
依赖顺序               ISSUE-015/027 已合入且复审 PASS（既有 REVIEW_REPORT）；028 未越入 ISSUE-029（M06 L79 029 仍 Planned）
分支策略说明           与 ISSUE-024~027 既有先例一致：工作树交付、默认不 commit/push、staged 人工验收后授权合并
执行过程注记           t2 过程异常（执行器上下文耗尽停摆、替换模型命令挂死、captain 接管 + 7 类修复）在计划 §7/§7.1 透明披露；半成品→全绿路径可追溯，符合标准 §10（无隐藏失败：红灯/停摆/挂死均如实记录）
```

## 6. 测试与验证结果（独立复跑实录）

环境：工作区 `.venv/Scripts/python.exe` Windows Python **3.13.14**（pytest 8.4.2、ruff、mypy、numpy；t2 主口径）。

| 命令 | 退出码 | 实际结果 | 对照 t2 声称 |
|---|---|---|---|
| `.venv/Scripts/python.exe -m pytest tests/contract/test_calibration_reference.py -q` | 0 | **14 passed in 4.16s**；复跑 2 次 14 passed（3.15s/3.16s）——3 次稳定 | 14 passed（4 次稳定）✓ |
| `.venv/Scripts/python.exe tools/quality/verify.py`（后台 job，完整门） | 0 | `[quality] all gates passed`；pytest **1100 passed, 4 deselected in 258.23s**（=1086 基线+14） | 1100/4 ✓ |
| `.venv/Scripts/python.exe -m ruff check src tests` | 0 | `All checks passed!` | ✓ |
| `.venv/Scripts/python.exe -m mypy src` | 0 | `Success: no issues found in 48 source files`（47→48，含 calibration/reference.py） | ✓ |
| `.venv/Scripts/python.exe -c "import uav_gpr.calibration.reference"` | 0 | import OK，`__all__` 9 项与文档一致 | ✓ |
| `git diff --check && git status --porcelain=v1 -b` | 0 | clean；工作树与审查开始时逐项一致（探针清理后复核） | ✓ |
| 红灯证据 | — | 无法事后复现（实现已存在）；测试文件 L27–34 顶层 `from uav_gpr.calibration.reference import …` 在实现前必然 ModuleNotFoundError → collection error exit 2，与计划 §7 L68 记录相符——**未发现反证** | 合理可信 |

**独立探针（t2 未覆盖的关键反例，审查者新增，Windows 系统临时目录运行，全部已清理，项目内零残留）**：

- **探针 B（混配置·通道）**：双通道冻结下通道**顺序对调**（(VV,HH)）、**子集**（仅 HH_S11）、**错 ID**（HH_S21 顶替 VV_S22）的 sweep → 全部 `CHANNEL_CONTRACT_MISMATCH` fail-closed，accepted_total 保持 0（OSL 与空采两会话同测）✓；
- **探针 C（混配置·轴 + 委托一致性）**：点数不同的短轴 sweep → `AXIS_MISMATCH` 拒绝；captures_per_step=2 完整跑通后 `build()` 与直调 `build_osl_calibration`（同 6 个 sweep、同堆叠）directivity/reflection_tracking/source_match `array_equal` **全 True**、profile_id 各自新生成 → 委托无数学复制、无数值漂移 ✓；
- **探针 E（空采设备错误）**：持续 `timeout_at=(0,)` + 空采会话 → 6s 内工厂 37,659 次（→ P3-1 无失败预算热重试）；cancel 后干净恢复（cancelled、join=True、controller 全 join）→ 无线程泄漏 ✓（附 P3-1）；
- **探针 F（后续步骤失败保留前序）**：`timeout_at=(1,)`（失败发生在第 2 道而非 t2 测试的第 1 道）→ 重试 1 次、终态 COMPLETED、**open 行均值与失败前已接受的 sweep 逐位相等**（前序步骤数据保留实证）、无线程泄漏、事件序 gate_closed 先于 controller_stopped ✓；
- **探针 G（双通道行绑定）**：双反射 OSL build() 后 p0(HH_S11) open/short/load == 步骤 sweep[0/1/2] 的 **row 0**、p1(VV_S22) == sweep[3/4/5] 的 **row 1**（负对照 row 交叉均不匹配）→ 六步→profile 行归属无错绑 ✓；
- **静态审计**：模块零 `open()/Path/os./write/.rcal/.rcbg/Qt` 引用（grep 实测）；测试与实现零 `time.sleep`；`calibration/__init__.py` 未改；依赖方向合规（§3 审查者注）。

结论：执行器套件 14 + 探针 5 组全绿（除 P3-1 定性为非阻塞的空采无预算路径外），实现未被任何探针击穿。

## 7. 报告与事实差异

1. **P3-2**：t2 完成报告与任务登记含完整门禁数字（1100/4、48 文件），但**计划文档** §7 L79「verify.py 数字见下（终态登记时补录）」未补录——报告口径与文档口径存在落盘缺口；以完成报告 + 本审查独立复跑（§6）共同证实数字真实，判验收项 PASS，缺口记录为 P3-2。
2. t2 报告「定向 14 passed（4 次复跑稳定）」→ 本审查复跑 3 次均 14 passed（声明次数不可事后逐次复现，标「未发现反证」）。
3. 红灯证据（ModuleNotFoundError/exit 2）：无法事后复现，标「未发现反证」（与计划 §7 L68 记录相符）。
4. 行数核对：reference.py 722 行（t2 报告 ~713——差异源于报告四舍五入/修复后微调，实际以仓库 722 为准）、test 495 行/14 测试、计划 83 行、M06 单行 diff——全部实测一致或无实质出入。
5. 其余声明（M06 L44 单行 diff、changedPaths==inScope 4 条、未 commit/push/merge、mypy 47→48、依赖只读消费、零 I/O/Qt、无线程泄漏）**全部与仓库事实一致**。

## 8. 剩余风险

- **P3-1 空采无失败预算**：真机空采若遇持续设备错误，将出现无界重连风暴（CPU 满转 + 设备反复重试）；当前防线仅为调用方主动 cancel。建议在 ISSUE-029 开工前或 P3 批次修复（修复面小：AirBackgroundSession 失败计数 + 超预算 fail-closed；adapter 无需改动）。
- OSL 会话 `_actual` 缺省为理想值（1/-1/0）——真实 Cal Kit 值需调用方显式传入；ISSUE-029（.rcal 持久化）落地时须把 profile 的 actual 模型一并入档（I027 已支持 per-frequency Cal Kit，本模块透传即可，无接口缺口）。
- 双反射配置的物理语义：每个 sweep 携带全通道行、六步按「物理标准件连接状态」聚合（软件不感知标准件实际连接，符合「不自动切换/不伪造标准件」边界）；真机上错误连接的标准件只能靠后续质量指标（I027 残差/退化）发现——属设计预期行为，非缺陷。
- `ControllerReferenceAdapter` 停止链路依赖 `_GATE_POLL_TIMEOUT_S=0.05` 轮询感知终态（最长 ~50ms 延迟），对取消响应性足够；若未来要求即时响应，可让 `cancel()` 主动唤醒（当前无此接口，非缺陷）。
- 测试中 `advance_and_wake` 与 adapter 消费的共享 ManualWaiter 是 ISSUE-016/017 既有虚拟时间模式，重试测试对真实时间的依赖为有界截止（15s/10s）——CI 慢机上仍有裕度（本机 3–4s 完成）。

## 9. 合并建议

- **可合并**：建议按 staged 流程由项目负责人授权将 t2 的 4 个 inScope 路径（连同 t1 基线单）合入 main，随后将 M06 L44 状态行标记 `Done`（参照 ISSUE-024~027 先例）。
- 合并范围 = 工作树现有 5 个未提交/未跟踪条目；无拆分必要（单一新模块 + 单一新测试文件，可整体回退：删除 2 新文件、还原 M06 L44）。
- P3-1/P3-2 为可选清理项，建议随合并提交一并修正（P3-1 尤其建议在 ISSUE-029 开工前修复，避免空采热循环在后续集成被放大），或按既有 P3 批次先例延后关闭；不构成合并阻塞。
- 合并后不进入 ISSUE-029；等待项目负责人决定。

## 10. 最小修复清单

1. （P3-1）`src/uav_gpr/calibration/reference.py` L541–577：`AirBackgroundSession` 增加失败预算（如构造参数 `max_failures: int = 2`，`record_step_failure` 覆写计数并在超限时 `_fail_locked()`——注意 `record_step_failure` 当前在 `_SessionBase` 为 no-op 钩子，子类覆写即可，adapter 的 FAILED 分支已就绪无需改动）。配一条失败测试：持续 `timeout_at=(0,)` + 空采会话 → 有界次数工厂调用后 state=FAILED（禁固定 sleep，join 驱动断言）。
2. （P3-2）`docs/plans/2026-09-02-issue-028-osl-reference.md` §7 末尾补录终态门禁数字：verify.py exit 0 = 1100 passed / 4 deselected + ruff + mypy(48) + import 全绿（数字以本报告 §6 或 t2 复跑为准）。

（以上均不涉及公共语义变更；若选择暂缓，不影响本次 PASS 结论。）
