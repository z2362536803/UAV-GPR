# ISSUE-028 开工基线确认单（2026-09-02）

## 1. 目标 Issue 与依据

- **Issue**：ISSUE-028「OSL/空采无 UI 参考采集服务」（`docs/issues/M06_CALIBRATION_PROCESSING.md` L42-77）
- **工作区根替换声明**：Issue 提示词中 `E:\wurenjiruanjian` 一律替换为当前工作区根 `D:\博士任务\无人机软件\UAV-GPR`。
- **直接依赖**：ISSUE-015（SimulatedBackend/AcquisitionBackend）、ISSUE-027（OSL 模型与求解器）——本单下文核查均 Done 合入 main。
- **流程依据**：`docs/issues/README.md`（依赖顺序执行、一次一个 Issue）、`docs/ISSUE_REVIEW_STANDARD.md` v1.0（t1 基线 → t2 实现 → t3 独立复审）。

## 2. 只读核查证据（全部实测）

| 项 | 结果 |
|---|---|
| HEAD / 分支 | `main @ 56c2f0f`（`docs(issues): mark ISSUE-027 Done after authorized merge`）；`git status --porcelain=v1 -b` = `## main...origin/main`（工作树干净、与 origin 同步 0/0） |
| reflog / 历史 | `git log --oneline -3`：56c2f0f → a2f65c6（Merge feat/issue-027）→ 4f2e1d3（feat）；无 reset/rebase/amend/强推痕迹 |
| ISSUE-015 证据 | 合并提交 `2f11cd9`（Merge feat/issue-015）已在 main 历史；`src/uav_gpr/acquisition/backend.py` + `tests/contract/test_acquisition_backend.py`（28 测试）tracked 实测 |
| ISSUE-027 证据 | `a2f65c6`（Merge）+ `4f2e1d3`（feat）+ `56c2f0f`（M06 状态行 Done）均在 main；`src/uav_gpr/calibration/osl.py`（1008 行）与 `tests/contract/test_calibration_osl.py`（31 测试）tracked 实测 |
| 依赖定向 | `.venv/Scripts/python.exe -m pytest tests/contract/test_acquisition_backend.py tests/contract/test_calibration_osl.py -q` → **59 passed in 0.24s**（28+31，exit 0） |
| 可执行性 | M06 L44 ISSUE-028 状态 = `Planned`；`src/uav_gpr/calibration/` 仅 `__init__.py` + `osl.py`（无 reference 模块）；`docs/plans|reports` 无任何 028 文件 → **ISSUE-028 为下一个可执行 Issue** |

## 3. 契约要点（对实现有约束，t2/t3 引用）

来源：M06 ISSUE-028 条目（目标/范围/排除/验收）+ `docs/CALIBRATION.md` §3/§4 + `docs/ACQUISITION.md`（backend/controller 生命周期）+ ISSUE-027 `osl.py` 消费面。

1. **OSL 六步状态机**（CALIBRATION §3）：Open/Short/Load × S11/S22 六个物理采集步骤，显式状态机、**不允许跳步/混配置**；向导状态机必须独立于窗口按钮、可由合成数据测试。
2. **步骤冻结配置**：sweep config/channel/axis/目标道数在会话/步骤级冻结；`accept_sweep` 严格检查 axis/channel/config 后才聚合。
3. **委托 I027 求解**：步骤收齐后构建 OSL 误差模型（复用 `uav_gpr.calibration.osl` 的 profile/求解/质量指标），不复制求解数学。
4. **空采背景会话**（CALIBRATION §4）：`raw` 或 `osl_calibrated` 域显式声明；应用匹配 channel/S 参数与顺序、完整频率轴、数据域、校准域时匹配 `calibration_profile_id`。
5. **关闭顺序**：目标道数收齐后**先关闭接受门**再安全 stop controller（复用 ISSUE-015/017 采集循环，会话不拥有窗口；可选 controller adapter 只编排）。
6. **失败语义**：步骤失败按规则重试/保留前序；取消/设备错误**无线程泄漏**、**不伪造标准件**。
7. **排除**：不保存 `.rcal/.rcbg`、不做 Qt wizard、不自动切换物理标准件、不复制硬件采集循环。
8. **测试纪律**：SimulatedBackend 覆盖跳步、混配置、in-flight、重试、取消、资源关闭；禁固定 sleep（事件/屏障/虚拟时间）；默认不枚举硬件。

## 4. 门禁基线（verify.py 复跑）

- 全量非硬件 pytest：**1086 passed / 4 deselected**（`--collect-only` 1086/1090，4 deselected = 1 硬件哨兵 + 3 opt-in LibreVNA 硬件测试；基线 = ISSUE-027 合并后状态，与 ISSUE-027 t2/t3 复现一致）；verify.py exit 0（`[quality] all gates passed`）。
- ruff / mypy（47 files）/ import：全绿。
- 定向依赖：59 passed（见第 2 节）。
- 核查前后 `git status` 一致（本单为唯一新增未跟踪文件）；`git diff --check` clean。

## 5. 实施计划摘要（t2 契约要点）

- **inScope 精确路径 4 条**（t2 changedPaths 必须逐一相等）：
  1. `src/uav_gpr/calibration/reference.py`（ReferenceCaptureSession + OSL 六步/AirBackground 状态机 + accept_sweep 聚合 + 委托 I027）
  2. `tests/contract/test_calibration_reference.py`
  3. `docs/plans/2026-09-02-issue-028-osl-reference.md`
  4. `docs/issues/M06_CALIBRATION_PROCESSING.md`（仅 ISSUE-028 状态行 Planned → In progress → Review）
- **范围**：无 UI ReferenceCaptureSession（OSL 六步 + AirBackground）；步骤冻结配置；accept_sweep 严格聚合；重试/取消/错误/步骤保留；收齐先关接受门再 stop。
- **排除项**：不保存参考文件、不做 Qt wizard、不自动切换标准件、不复制硬件采集循环、不改 osl.py/core 公共语义。
- **验收**（M06 L63-67 原文）：状态机不允许跳步/混配置；步骤失败可按规则重试/保留前序；目标道数收齐后先关接受门再安全停止 controller；取消/设备错误无线程泄漏、不伪造标准件。
- **测试矩阵**：SimulatedBackend 驱动——跳步拒绝、混配置拒绝、in-flight 语义、重试/保留前序、目标道数收齐关门顺序、取消、设备错误资源关闭、axis/channel/config 错配拒绝、raw/calibrated 域声明、AirBackground 会话。

## 6. 结论

无阻塞项，**可开工**。t2 按「计划文档先落盘 → 红灯测试 → 最小实现 → 绿灯 → M06 状态行 → 全量门禁 → 登记 completed」执行。
