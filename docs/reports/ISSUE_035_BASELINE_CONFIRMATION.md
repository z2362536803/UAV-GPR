# ISSUE-035 开工基线确认单（自动化轮 · engineer）

## 1. 目标 Issue 与依据

- **Issue**：ISSUE-035「Flat Reflection 时域阶段」（`docs/issues/M06_CALIBRATION_PROCESSING.md` L301-336）
- **状态行核查**：M06 L303 ISSUE-035 状态 = `Planned`；映射 FR-012；直接依赖 ISSUE-030、031。
- **工作区根替换声明**：Issue 提示词中 `E:\wurenjiruanjian` 一律替换为当前工作区根 `/mnt/d/博士任务/无人机软件/UAV-GPR`（Windows：`D:\博士任务\无人机软件\UAV-GPR`）。
- **流程依据**：`AGENTS.md`、`docs/issues/README.md`（编号顺序为主执行顺序、依赖为开工门禁；本会话只执行 ISSUE-035）、`docs/ISSUE_REVIEW_STANDARD.md`（t1 基线 → t2 实现 → t3 独立复审）。
- **契约文档**：已读 `docs/PROCESSING.md` §6（Flat Reflection）+ §2（推荐流水线与数据域转换规则），`docs/CALIBRATION.md` L9-10 概念边界（空采背景 = 频域复数参考减除；Flat Reflection = 沿测线方向的时域局部背景处理），迁移规则见 `docs/REFERENCE_MIGRATION.md` L29（带通/IFFT/Dewow/Flat 保持独立 stage 和黄金样本）。

## 2. 只读核查证据（全部实测）

| 项 | 结果 |
|---|---|
| HEAD / 分支 | `main @ 8accb76`（`docs(issues): mark ISSUE-034 Done after automated merge`）；`git status --porcelain` 核查前为空（工作树干净，除本单外无输出） |
| 远端同步 | `git rev-list --left-right --count origin/main...HEAD` = `0 3`：本地 main 领先 origin/main 3 提交（034 feat `bb49e32` + merge `9f6caff` + Done 标记 `8accb76`）——**034 推送在后台重试中，尚未闭合**，按团队计划如实记录当下实测值，不阻塞开工 |
| ISSUE-030 Done 证据 | M06 L118 状态行 = Done（t3 复审 VERDICT=PASS 后自动合并，见 `docs/reports/ISSUE_030_REVIEW_REPORT.md`）；合并提交 `89fd9bb` 在 HEAD 历史；tracked 交付物齐全：`src/uav_gpr/processing/bandpass.py`（`ProcessingStage` Protocol / `StageResult` / `_input_domain_of` / `_record_for` 契约面）+ `tests/contract/test_processing_bandpass.py` ✓（`git ls-tree HEAD` 实证） |
| ISSUE-031 Done 证据 | M06 L155 状态行 = Done（t3 复审 VERDICT=PASS 后自动合并，见 `docs/reports/ISSUE_031_REVIEW_REPORT.md`）；合并提交 `9afdfb2` + 标记提交 `b4f6dec` 在 HEAD 历史；tracked `src/uav_gpr/processing/time_domain.py`（`FrequencyToTimeStage` / `TimeDomainStageResult` / 显示时窗层）+ `tests/contract/test_processing_time_domain.py` ✓ |
| 依赖定向回归 | 三个依赖模块 import ok（package import 门通过）；其全部测试包含于全量 verify（1342 passed，exit 0，见 §4）；034 已在链上（M06 L266 Done，`dewow.py` tracked），flat 的直接前置正是 dewow 输出域 |
| 可执行性 | `src/uav_gpr/processing/` 现有 `bandpass.py`(030)、`time_domain.py`(031)、`osl_calibration.py`(032)、`background_subtraction.py`(033)、`dewow.py`(034)，**无任何 flat 先行实现**（仓库内 `find -iname "*flat*"` 仅命中 `.agent-teams/` 团队目录本身）；M06 中自 035 起首个 `状态：Planned` 条目即 ISSUE-035（L303；036 编排 L340 其后）→ **ISSUE-035 为下一可执行 Issue**，无重复实现风险 |
| 顺序守卫就绪 | `dewow.py` 已内置 `_FLAT_STAGE_NAME = "flat_reflection_filter"`（L102）并拒绝"history 已含 flat 再 dewow"的错误顺序（guard 3，L410-421）——t2 的 stage_name token 必须与之逐字一致，双向顺序保护才有落点 |
| 参考源可达性 | `E:\钢筋仪软件开发` 本机不可达（盘符不存在），沿用 027-034 先例使用本地只读副本 `D:\博士任务\rebar-inspector`（WSL 路径 `/mnt/d/博士任务/rebar-inspector`，存在，只读使用、不修改） |

### 参考源哈希实测（与 manifest.md 冻结值逐一相等）

| 用途 | 参考文件（本地只读副本相对路径） | 实测 SHA-256 | manifest 冻结值 |
|---|---|---|---|
| Flat 算法主体 | `src/rebar_inspector/processing/flat_reflection.py`（7228 B / 178 行） | `89e3c01b3ce4135fd96495b27a67ff69760224bdc80c9144fd9aeeaf4ca87df0` | 相同 ✓（manifest L77） |
| 时域 stage 共性 | `src/rebar_inspector/processing/_time_stage_common.py`（9224 B / 228 行） | `e0c201b55acbaece0edb1546bbb8a00492874bb79fb9caf789d5ba416d333c81` | 相同 ✓（与 034 基线单登记值一致） |
| 参考侧 flat 原型（上游未跟踪脚本，不迁移不引用） | `数据处理函数/flat_refelction_filter.py` | — | manifest worktree dirty L23 登记 |

关键本项目契约文件哈希（SHA-256 前 16 位，供 t3 交叉核对）：

```text
7efaa728ab3f96ad  src/uav_gpr/processing/dewow.py
f707839674ceb5e1  src/uav_gpr/processing/bandpass.py
b7da55717148645b  src/uav_gpr/processing/time_domain.py
a96d59f63289a8c8  src/uav_gpr/processing/background_subtraction.py
cfa8271f5ebd545c  src/uav_gpr/core/time_domain.py
9d730ec7a0f7c223  docs/PROCESSING.md
5ca0dc5fdd2ccedc  docs/CALIBRATION.md
793d80c0d9fa9221  docs/issues/M06_CALIBRATION_PROCESSING.md
```

## 3. 契约要点（对 t2 实现有约束）

### 3.1 PROCESSING.md §6 + CALIBRATION.md 概念边界强约束

- §6 Flat Reflection：**沿 trace 轴减局部滑动平均**，减少近似水平背景；**可能削弱连续层状反射或与测线方向一致的目标** → 默认可选、UI 必须说明影响（本 Issue 只做"文档说明影响"，UI 默认启用属排除项）；实时增量预览语义明确（本 Issue 不做实时增量近似，属排除项）。
- CALIBRATION.md L9-10 概念边界：**空采背景**减去空场/系统环境的**复数频域参考**（`air_background_subtraction`，频域链，033 已交付）；**Flat Reflection** 是**沿测线方向的时域局部背景处理**（trace 轴统计）。两者不得混名、不得混 history、不得互相冒充等价——t2 模块 docstring 与 record parameters 必须显式声明该区分（参考源 docstring 已有同款声明可迁移语义）。
- §2 数据域转换规则：`time_base → time_processed`、`time_processed → time_processed` 均合法（core `_ALLOWED_TRANSITIONS` 现成）；不允许跳过 `time_base`；同一 history 内稳定 `stage_name` 只得应用一次（core `ProcessingHistory` 强制，bump version 不能绕过）→ "重复 flat 拒绝"由 core + stage 双重 fail-closed。
- AGENTS.md §3：每阶段输入不可变、输出新对象、追加可序列化参数/版本/历史；complex/trace/channel/time 轴与 metadata 完整保留。

### 3.2 core / 既有 stage 层可直接复用的资产（t2 不新建平行类型）

- `DataDomain.TIME_BASE`/`TIME_PROCESSED`、`TimeDomainKind`、`TimeDomainScan`（构造防御拷贝+只读化、`trace × channel × time` 严格校验、`_validate_history_kind` 入场兜底）——034 同款。
- `_ALLOWED_TRANSITIONS` 两条 hop 已开：`TIME_BASE → TIME_PROCESSED`（flat 直挂 IFFT 输出合法）与 `TIME_PROCESSED → TIME_PROCESSED`（推荐链 dewow → flat 合法）。
- 034 公共符号沿用：从 `uav_gpr.processing.bandpass` import `ProcessingStage` / `_input_domain_of` / `_record_for`，返回 `uav_gpr.processing.time_domain.TimeDomainStageResult`；错误统一走 `DomainError + ErrorCode`（INVALID_ARGUMENT / DTYPE_MISMATCH / SHAPE_MISMATCH / NON_FINITE_AXIS / PROCESSING_DOMAIN_MISMATCH 均为 core/errors.py 现成枚举）。
- `centered_moving_mean` 累积和 O(N) 核已在 `dewow.py`（沿最后轴、reflect pad）；flat 需要**沿第 0 维 trace 轴、edge pad**——t2 决策：将核泛化为 axis+padding 参数或在本模块内独立实现同构核（不移植参考的 `[time, trace]` 布局与 float64 假设；core/processing 既有 stage 公共语义不改，见 t2 nonGoals）。

### 3.3 参考实现（flat_reflection.py，冻结哈希 `89e3c01b…`）迁移契约

1. **算法**：`filtered(t,c,τ) = original − local_mean_along_trace_axis(window=W, edge padding)`；trace 轴恒为第 0 维；complex128 下 real/imag 各自独立同均值，减除天然保复。
2. **窗口策略（与 dewow 不同，钉死）**：`window_traces` 为**奇数 int ≥3**，构造期即校验（拒绝 bool / 偶数 / <3——参考侧明确拒绝 window=1"输出全零"属安全性加固）；`≤ n_traces` 在 apply 时按实际道数检查；**无秒→样本换算**（窗口单位就是道数，默认常量 `DEFAULT_FLAT_REFLECTION_WINDOW_TRACES = 101`，本项目是否沿用 101 默认值由 t2 决策并在计划文档记录）。
3. **边界策略**：固定 `"edge"`（首尾道值平铺填充），不可配置；与 dewow 的 `"reflect"` 形成对照，二者均在 record parameters 显式落盘。
4. **入场守卫**：非 `TimeDomainScan` 拒绝（`TimeDomainData` 无测线方向必须拒）；`_reject_duplicate_stage("flat_reflection_filter")`；复数有限性检查（NaN/inf 拒绝）；history 末域 ∈ {TIME_BASE, TIME_PROCESSED} 且与 `source.kind` 一致（core 兜底 + 显式守卫）。**错序拒绝已由 034 侧闭环**：dewow 遇含 flat 的 history 拒绝，flat 侧无需重复实现该方向、但顺序 token 必须逐字为 `flat_reflection_filter`。
5. **输出重建**：同类型 `TimeDomainScan`，`kind=TIME_PROCESSED`（无论输入 TIME_BASE 还是 TIME_PROCESSED），channels/time_axis_s/metadata/schema_version 全量沿用；新数组经 core 构造防御拷贝置只读；输入零修改。
6. **record parameters**（JSON-safe）：`{window_traces, padding:"edge", operation:"subtract_local_trace_mean", axis:"trace_first"}` + stage/stage_version/input_domain/output_domain/executed_utc，经 `_record_for` 写 `ProcessingRecord`。

### 3.4 团队计划硬约束（t2 inScope 表述以 t2 任务契约为准）

本单不约定文件集；t2 的 inScope 精确 4 路径（含计划文档 `docs/plans/2026-09-05-issue-035-flat-reflection.md`）以 **t2 任务契约原文为准**，changedPaths 与之逐一相等：
`src/uav_gpr/processing/flat_reflection.py`、`tests/contract/test_processing_flat_reflection.py`、`docs/plans/2026-09-05-issue-035-flat-reflection.md`、`docs/issues/M06_CALIBRATION_PROCESSING.md`。
（注意：034 先例形态含 `processing/__init__.py` 导出，本 Issue t2 契约 inScope **不含** `__init__.py`——t2 不得改该文件，模块经完整路径 import。）

## 4. 门禁基线（verify.py 复跑，实测）

- `tools/quality/verify.py`（interpreter = `.venv/Scripts/python.exe`，本次命令：`.venv/Scripts/python.exe tools/quality/verify.py`）：
  - pytest (non-hardware)：**1342 passed / 4 deselected in 270.82s**（4 deselected = 硬件哨兵 + opt-in LibreVNA），与团队计划声明的 1342 一致 ✓
  - ruff：`All checks passed!` ✓
  - mypy：`Success: no issues found in 54 source files` ✓（计划口径 54；034 新增 dewow.py 后 53→54）
  - package import ok；`[quality] all gates passed` exit 0
- 核查前后 `git status --porcelain` 均为空（本单为唯一新增未跟踪文件）；`git diff --check` 干净；verify 输出重定向至工作区外临时文件（`/tmp/verify_035_t1.log`），仓库内无遗留产物。

## 5. 实施计划摘要（供 t2）

**范围**：`FlatReflectionFilterStage`（t2 契约锁定文件 `src/uav_gpr/processing/flat_reflection.py`）实现 `ProcessingStage` 协议：
- `stage_name="flat_reflection_filter"`（与 034 dewow 侧顺序守卫 token 逐字一致）、`input_domain={TIME_BASE, TIME_PROCESSED}`、`output_domain=TIME_PROCESSED`。
- `apply(source: TimeDomainScan, *, history=None, executed_utc/clock)`（签名按 030/031/034 冻结协议对齐）：
  1. 入场守卫链（仿 034 guard 1-6）：合法时域前驱域 ⇒ 否则 `PROCESSING_DOMAIN_MISMATCH`；重复 flat 拒绝（stage 级第一闸 + core history 唯一性兜底）；kind/predecessor 一致性；`ndim==3` 且 shape[0]=trace 道数复核；复数有限性入场闸。
  2. 窗口：构造期奇数≥3 校验；apply 期 `window_traces > n_traces` ⇒ 拒绝（短测线保护，消息含操作指引）。
  3. 数值：沿**第 0 维 trace 轴** edge-pad 中心滑动均值（cumsum O(N)、complex128、无 Python 逐窗循环），输出 = data − local_mean；shape `(trace, channel, time)` 不变、channel/time/metadata 完整保留；输入零修改（readonly view + 新数组）。
  4. 输出 `TimeDomainScan(kind=TIME_PROCESSED)` + `TimeDomainStageResult`，history 经 `_record_for` 追加 `{window_traces, padding:"edge", operation:"subtract_local_trace_mean", axis:"trace_first"}` 记录。
  5. 模块 docstring 显式声明：与 `air_background_subtraction`（频域空采参考减除）语义区分、绝不等同；可能削弱连续层状反射/平行测线目标的警示。
- **排除**：不做实时增量近似、不做 UI 默认启用；不 import 参考项目；不改 core / 既有 stage / `__init__.py` / storage 任何字节。

**测试矩阵**（失败测试优先，`tests/contract/test_processing_flat_reflection.py`）：
1. **水平背景**：沿 trace 轴恒定（各道同值）的背景场 ⇒ 输出 ≈ 0（减自身均值）——flat 的核心去除对象。
2. **局部目标**：仅个别 trace 有响应的脉冲道 ⇒ 输出 ≈ 原值（大窗口均值稀释，凸显局部目标）；倾斜/双曲响应保留定性断言。
3. **复数等价**：`flat(a+bj) == flat(a)+flat(b)j` 逐位断言。
4. **多通道/多 time**：channel/time 轴互不串扰；逐 trace 切片与整缓冲结果一致；trace 轴方向性（沿第 0 维而非 time 轴运算的反例断言：交换轴语义结果必不同）。
5. **短测线**：n_traces < window_traces 拒绝且消息含指引；n_traces == window 允许（全缓冲一窗）。
6. **窗口边界**：偶数/<3/bool/非 int 构造拒绝；edge 填充手算小例黄金字面量（首尾道值平铺语义钉死）；window=3 最小窗手算核对。
7. **顺序与 history**：重复 flat 拒绝（bump version 不绕过）；dewow→flat 合法链（用真 `DewowStage` 串联）；time_base 直挂 flat 合法；record to_dict/from_dict JSON-safe 往返；与 `air_background_subtraction` 不混名（history 同时含两者合法、语义域不同）。
8. **输入不变性**：source.data bytes 前后一致 + read-only；输出为新对象。
9. **黄金对拍**（034 三方姿势）：① 冻结参考源 SHA-256（`89e3c01b…87df0` + `e0c201b5…333c81`）登记进测试注释；② 参考核（edge-pad cumsum）独立重算小数组内嵌字面量；③ 同公式独立转写整缓冲 bit-exact 比对；生成脚本置于仓库外，零污染。
10. **性能 smoke**：如 512 trace × 2 ch × 1024 time 随机 complex128，单次 apply 墙钟宽松阈值，证明 O(N) 无逐窗循环。

**门禁**：定向测试（先红灯后绿灯）→ 相关回归（dewow/bandpass/time_domain/core）→ verify.py 全量（基线 1342 passed + 新增用例数；mypy 54→55 源文件为预期变化）→ ruff + mypy + import + diff-check；changedPaths 与 t2 契约 inScope 4 路径逐一相等；登记时 acceptanceResults criterion 与 t2 契约逐字一致、payload 精简。

## 6. 结论

两个直接依赖均有合并提交与 tracked 交付物实证：ISSUE-030（`89fd9bb`，`ProcessingStage`/`_record_for`/`_input_domain_of` 契约就绪）、ISSUE-031（`9afdfb2`，`TimeDomainScan` 不可变模型 + `_validate_history_kind` + `TimeDomainStageResult` + `time_base` 生产端）。034 已把反向顺序守卫（dewow 拒 flat-first history）与 `_FLAT_STAGE_NAME` token 钉入主干；core `_ALLOWED_TRANSITIONS` 两条 hop、重复 stage 拒绝、provenance continuity 全部现成。参考源 `flat_reflection.py`（`89e3c01b…87df0`）与 `_time_stage_common.py`（`e0c201b5…`）在本地只读副本实测哈希与冻结 manifest 逐一相等、算法/守卫/edge 策略全文可读；黄金对拍样本可用（034 同类 40 测试先例）。仓库无 flat 先行实现，ISSUE-035 为下一可执行 Issue。034 合并链推送尚在后台重试（ahead 3，如实记录，不构成开工阻塞）。**门禁基线 1342 passed / 4 deselected、ruff clean、mypy 54 files clean、package import ok 全部实测复现（exit 0），核查前后工作树干净、无遗留产物。ISSUE-035 可以开工（Ready）**，t2 按第 3 节契约与 t2 任务锁定的 inScope 4 路径执行。
