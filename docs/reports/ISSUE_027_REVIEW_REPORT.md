# ISSUE-027 独立复审报告（OSL 校准模型与求解器）

- 审查日期：2026-09-02
- 审查者：AgentTeams `uav-gpr-issue-027-osl-calibration` 成员 reviewer（任务 t3，attempt 02ec1985-7686-4b29-93c8-2ae45af52154）
- 被审交付：t2 完成报告（attempt 634692fc-284b-4b75-860e-4cfe39f9d4ae）+ 工作树 4 个 inScope 路径 + 计划文档 + M06 状态行
- 审查标准：[docs/ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) v1.0；基线件：docs/reports/ISSUE_027_BASELINE_CONFIRMATION.md（t1，attempt 0a605184-6238-4f4b-b420-3325cf8bbed7）
- 审查全程只读：未修改任何实现/测试/计划/文档/M06/Git 状态；本报告为唯一新增文件；变异探针在系统临时目录 `/tmp/osl_review/` 中运行并已清理，项目内零残留。

## 1. 审查结论

**VERDICT: PASS**

ISSUE-027 的 3 条验收标准（M06 L26–30）全部满足，未发现 P0/P1/P2 级问题。t2 交付真实、完整、合规，可进入人工验收（staged 流程：由项目负责人授权合并后标记 Done）。发现 3 项 P3 级非阻塞建议（见 §3），不阻止合并。

独立复核要点：① 冻结参考闭式解与 UAV 实现逐式一致（rebar `calibration_reference/osl_calibration.py` L275–300 vs `src/uav_gpr/calibration/osl.py` L306–348），且 10/10 源文件 SHA-256 与计划文档迁移清单逐一相等；② 黄金向量由审查者独立重跑冻结参考复现，与测试字面量逐位一致；③ 全量门禁独立复跑 1086 passed / 4 deselected / ruff / mypy 47 文件 / import 全绿（exit 0）；④ 补查 15+ 变异探针，除 1 项极端量级输入路径暴露的错误类型瑕疵（P3-1，fail-closed 未破）外，实现未被击穿。

## 2. 自动识别的审查范围

从 t2 完成报告、Git 工作树与文档交叉识别（证据见 §5）：

| 项 | 识别结果 | 证据 |
|---|---|---|
| Issue | ISSUE-027「OSL 校准模型与求解器」（M06 L5–40，状态行 `Review`） | docs/issues/M06_CALIBRATION_PROCESSING.md L7；docs/issues/README.md L94（依赖 001,004,007） |
| 基线/分支 | 无独立分支；工作树直接基于 `main` @ `f68efff`（`f68efff3efbbb0b71609873541f4a07160d442a0`），HEAD == origin/main（0/0），未 commit/push/merge | `git rev-parse HEAD origin/main` 相等；`git reflog | grep -icE "reset|rebase|amend|filter"` = 0 |
| 审查范围 | 工作树全部改动 = t1 交付物（`docs/reports/ISSUE_027_BASELINE_CONFIRMATION.md`，??）+ t2 的 4 个 inScope 路径：`src/uav_gpr/calibration/osl.py`（??，1008 行）、`tests/contract/test_calibration_osl.py`（??，863 行 / 31 个测试函数）、`docs/plans/2026-09-02-issue-027-osl-calibration.md`（??，137 行）、`docs/issues/M06_CALIBRATION_PROCESSING.md`（M，diff 仅 L7 状态行） | `git status --porcelain=v1 -b`；`git diff` 仅 1 行 |
| inScope/changedPaths 一致性 | t2 登记 changedPaths 4 条与 inScope 4 条逐一相等，与工作树实测一致（基线单为 t1 交付物，正确排除在 t2 inScope 外） | 任务契约 inScope vs git status |
| 直接依赖 | ISSUE-001/004/007 已合入 main（t1 §3.2 实测提交链）；HEAD 上 `ChannelSpec`/`SParameter`/`StableStrEnum`/`DomainError`/`ErrorCode`/`CalibrationProfileId` 实际存在且被 osl.py 只读消费；ErrorCode 未新增（osl.py 使用的 8 个错误码全部 pre-exist 于 core/errors.py L25–33） | 本次复读 core/channels.py、core/errors.py、core/enums.py、core/identifiers.py |
| 测试声明 | 定向 31 passed；定向+依赖 145 passed；全量 1086 passed/4 deselected；ruff 全绿；mypy 47 文件；import ok；diff-check clean | 见 §6 独立复跑 |

## 3. 主要问题（按 P0→P3）

无 P0/P1/P2。P3（非阻塞）如下：

- **P3-1 [src/uav_gpr/calibration/osl.py L434–437] 非有限质量指标的错误路径抛 TypeError 而非 DomainError**：`OslCalibrationQuality.__post_init__` 对非有限值构造 `DomainError(..., {"field": name, "value": value})`，当 value 为 nan/inf 时 core `errors._require_json_safe`（errors.py L52–53）拒绝非有限 JSON 值 → 抛 `TypeError: context value at $.value is not finite: nan`，预期中的 `DomainError(INVALID_ARGUMENT)`（"quality metric must be finite and non-negative"）成为死代码。触发条件：极大量级测量（实测 |m|≈1e200 即复现，1e300 亦复现；复数乘法溢出产生 nan 项 → 质量指标非有限）。影响：无数据破坏、无伪造结果（仍然 fail-closed，异常照抛），但验收口径「非有限结果明确」在该极端路径下以异常类型混淆的形式呈现。**最小修复方向**：context 不含原始数值（如只含 `field`），或 `_capture_repetition_errors`/`_solve_terms` 显式校验求解项有限性并抛出干净的 `DomainError(INVALID_ARGUMENT)`；建议配一条失败测试（1e200 量级输入 → DomainError）。
- **P3-2 [src/uav_gpr/calibration/osl.py L465–589] 私有 base 数组可经 `setflags(write=True)` 重开写**：numpy 对「自有数据的 write=False base」允许 `setflags(write=True)`（仅视图禁止）——实测 `profile._directivity.setflags(write=True)` 成功。公共 API 不受影响：属性一律返回 `.view()`（视图无法重开写，探针/测试证实抛 ValueError），属性赋值抛 `FrozenInstanceError`，输入绝不修改；且与冻结参考源的保护层级一致（计划文档 D8 已如实记录该机制与两轮重构）。仅私有属性越界访问（等价于 `del profile._channel` 亦可）存在逃逸口。**可选硬化方向**：base 改为不可变缓冲（如 `np.frombuffer(bytes, ...)` 只读缓冲）的视图存储，从机制上封死；若不硬化，建议在模块 docstring 补一句私有属性访问不属公共契约的说明。
- **P3-3 [tests/contract/test_calibration_osl.py L477–484] 重复断言**：`test_calibration_set_channel_mismatch_fail_closed` 中「错误 S 参数顺序 apply 拒绝」场景连续断言两次（L478–480 与 L482–484 完全相同），建议删除其一。

## 4. 逐 Issue 验收矩阵（ISSUE-027，M06 L26–30 原文）

| # | 验收标准 | 状态 | 代码证据 | 测试证据 |
|---|---|---|---|---|
| 1 | 理想 OSL 可恢复已知 DUT；带噪/奇异/轴不匹配结果明确 | **PASS** | osl.py L282–349 转录参考闭式解（denominator/D/S/Δ 四式，`T=D·S−Δ`，与冻结参考 L275–300 逐式一致，审查者独立推导理想标准件下 D=m_l、S=(A+B′)/(A−B′)、T=A(1−S) 恒等成立）；L352–379 校正 `x=m−D; Γ=x/(T+S·x)` + 相对阈值奇异守卫；L311–332 scale-free 归一化分母退化守卫（任一点退化整体 fail-closed，context 带 kind/first_index）；L386–401 per-capture 重复性质量指标 | test_calibration_osl.py L168–179（理想恢复 D/T/S 与 DUT 至 1e-9）、L204–214（闭式互验 1e-9）、L279–336（带噪 24 captures 相干平均 + 指标随噪声分级）、L549–621（open==short 退化/单频点退化/全零/校正奇异 → INVALID_ARGUMENT 带 kind）、L624–670（NaN/Inf 输入/actual/轴 → INVALID_ARGUMENT/NON_FINITE_AXIS）、L673–706（降序/重复/复数轴 → NON_INCREASING_AXIS/DTYPE_MISMATCH）；审查探针：全向量与冻结参考 0 差对拍、溢出量级 fail-closed（P3-1 注） |
| 2 | 输入 raw 不变，通道/profile 不可误用 | **PASS** | 全部数组路径防御性拷贝（L106–109、L144–146、L171/L185、L198、L222、L235、L263）；profile 构造后数组以 write=False base + `.view()` 属性暴露（L611–649），属性赋值 FrozenInstanceError（L591–592）；build/correct/apply 无任何输入写面；容器 L858–1008：非空/通道唯一（DUPLICATE_CHANNEL）/全 profile 轴逐点相等（AXIS_MISMATCH）/profile_for 全字段精确匹配/apply 通道顺序逐位全等（CHANNEL_CONTRACT_MISMATCH）；S21/S12 通道构造即拒（L540–548、L777–785）；core ErrorCode 未新增（全部 pre-exist） | test_calibration_osl.py L387–415（S11/S22 独立 profile 互不串扰、各自恢复本端口）、L418–527（有序绑定、profile_for、apply 逐通道恢复、错序/换通道/未知通道/重复通道/轴不符/空容器全部 fail-closed）、L530–541（S21 拒绝）、L774–823（build/correct/apply 前后输入逐位不变、后改输入不影响 profile、视图 setflags(write=True) 抛 ValueError、属性赋值 FrozenInstanceError）；审查探针：private base 逃逸口为 P3-2（公共 API 未被击穿）、apply 输出重开写不影响 profile 内部状态 |
| 3 | 与冻结参考黄金样本在明确容差内一致 | **PASS** | 黄金向量字面量 L63–104（采样点 atol=1e-9/rtol=1e-9、复数和 atol=1e-6，口径记录于计划文档 §8）；源哈希 10/10 与计划迁移清单逐一相等（§5 本报告） | test_calibration_osl.py L182–201（D/T/S/校正 DUT 采样点 + 全向量和与黄金字面量容差内一致）；审查者独立以 WSL python3 3.12.3 重跑冻结参考（SHA-256 0e278bf0…）复现全部黄金字面量**逐位一致**，且 UAV 实现与参考全向量最大差 = 0.0（§6 探针 P1） |

提示词附加必测项（M06 L34–39 提示词逐项）：理想 DUT 恢复（✓ L168–179）、带噪（✓ L279–336）、奇异（✓ L549–608）、非有限（✓ L624–670）、axis/channel/profile 错配（✓ L248–260/L460–527/L673–766）、参考数值对拍（✓ L182–201 + 审查独立重跑）。

范围项逐项（M06 L13–20）：不可变 profile/标准件/通道/频率轴模型（✓ D4/D5/D8，osl.py L465–746）、OSL 复数求解/应用数学/退化奇异检测/质量指标（✓ D1/D2/D6，L282–401）、S11/S22 各自独立 profile + 有序多通道容器（✓ D4/D9，L858–1008）、钢筋仪源哈希与黄金向量对拍（✓ §8 迁移清单 + L182–201）。

排除项合规（M06 L22–24）：不采标准件/无硬件（osl.py 零 I/O/线程/串口）、不写 `.rcal`（无任何文件写入路径）、无 UI（零 Qt import）、无空采（无 background 语义；docstring 仅声明 ISSUE-033 为下游）；无 ISSUE-028 状态机、无 ISSUE-032 stage/history（`grep "class.*Stage|ProcessingHistory|accept_sweep"` 零命中）；不改 core（git status 无 core 文件）；`src/uav_gpr/calibration/__init__.py` 未改（git 未跟踪修改）。

## 5. Git 与交付检查

```text
branch                 main；HEAD f68efff3efbbb0b71609873541f4a07160d442a0 == origin/main（0/0）
reflog                 grep reset|rebase|amend|filter = 0 条 —— 无破坏性历史操作
工作树（审查前后一致）  ## main...origin/main + M M06_CALIBRATION_PROCESSING.md + ?? 4 文件（基线单/osl.py/测试/计划文档）
                        —— 审查复跑前后 5 个文件 SHA-256 逐字节一致；.pytest_cache/.mypy_cache/.ruff_cache/__pycache__ 均 git-ignored（.gitignore L2/6/7/8）
git diff --check       clean（exit 0）
M06 diff               仅 L7 一行：- 状态：Planned → + 状态：Review（…等待独立复审…），与 t2 声称一致
inScope 精确路径       4 条与 changedPaths 逐一相等；无范围外文件、无 glob、无缓存/日志/密钥/实测数据混入
参考源哈希对拍         10/10 逐字节一致：manifest 白名单 8 文件（calibration/__init__.py=0b5ed581…、_base.py=8cd4fe95…、background.py=db96c7ba…、dual_reflection.py=6f3725e5…、errors.py=b6858162…、interpolation.py=9dc1b302…、osl.py=afbc6a73…、core/enums.py=08129eb7…）+ 受控补充 2 文件（calibration_reference/osl_calibration.py=0e278bf0…、tests/test_calibration_osl.py=2e566854…）——审查者实测与计划文档 §8 清单逐一相等
依赖顺序               ISSUE-001/004/007 已合入 main 且复审 PASS（ISSUE_001_005_REVIEW_SUMMARY）；027 未越入 ISSUE-028（M06 L44 仍 Planned）
分支策略说明           README §1.2 建议独立分支，但团队目标与 ISSUE-024/025/026 既有先例均为「工作树交付、默认不 commit/push、staged 人工验收后授权合并」；t2 遵守了该既定流程，无未授权提交
```

## 6. 测试与验证结果（独立复跑实录）

环境 A：WSL Ubuntu，`/usr/bin/python3` Python 3.12.3（pytest 8.4.2、ruff 0.16.4、mypy 1.20.2、numpy 2.5.2）。
环境 B：工作区 `.venv/Scripts/python.exe` Windows Python 3.13.14（pytest 8.4.2、ruff 0.16.5、mypy 1.20.2、numpy 2.5.2；t2 主口径）。

| 命令 | 环境 | 退出码 | 实际结果 | 对照 t2 声称 |
|---|---|---|---|---|
| `python -m pytest tests/contract/test_calibration_osl.py -q` | B | 0 | **31 passed in 0.06s** | 31 passed / 0.09s ✓ |
| 同上 | A | 0 | **31 passed in 1.62s**（交叉口径） | — |
| `python -m pytest tests/unit/test_core_frequency.py tests/unit/test_core_time_domain.py tests/unit/test_reference_manifest.py tests/unit/test_core_enums.py tests/unit/test_core_identifiers.py tests/contract/test_calibration_osl.py -q` | B | 0 | **145 passed in 3.03s**（114 依赖 + 31 新增） | 145 passed / 2.98s ✓ |
| `python tools/quality/verify.py` | B | 0 | pytest **1086 passed, 4 deselected in 275.37s**（=1055 基线+31，算术一致）；ruff `All checks passed!`；mypy `Success: no issues found in 47 source files`；`package import ok`；`[quality] all gates passed` | 1086/4 ✓、47 文件 ✓、全绿 ✓（t2 声称 278.52s，同量级负载噪声） |
| `python -m ruff check src tests`（verify.py 内含） | B | 0 | `All checks passed!` | ✓ |
| `python -m mypy src`（verify.py 内含） | B | 0 | `Success: no issues found in 47 source files`（46→47，含 calibration/osl.py） | ✓ |
| `python -c "import uav_gpr, uav_gpr.core, uav_gpr.calibration, uav_gpr.calibration.osl, ..."` | B | 0 | `package import ok`（verify.py 门内） | ✓ |
| `git diff --check && git status --porcelain=v1 -b` | A/B | 0 | diff-check clean；工作树与审查开始时一致 | ✓ |
| 红灯证据 | — | — | 无法事后复现（实现已存在）；测试文件 L35–41 顶层 `from uav_gpr.calibration.osl import …` 在实现前必然 ModuleNotFoundError → collection error exit 2，与 t2 声称相符——**未发现反证** | 合理可信 |

**独立变异探针（t2 未覆盖的关键反例，审查者新增，`/tmp/osl_review/` 临时目录运行，已 `rm -rf` 清理，项目内零残留）**：

- P1 全向量对拍：UAV 实现 vs 冻结参考 D/T/S/校正 DUT 全 41 点最大差 **0.0**（强于采样点+checksum 口径）；
- P2 `(trace, frequency)` 广播校正（ISSUE-032 复用路径）形状/数值正确，输出只读；
- P3 全部公开数组属性视图 `setflags(write=True)` 抛 ValueError（不可重开写）；
- P4 私有 `_directivity` base 可重开写（→ P3-2，公共 API 不受影响）；
- P5 全零测量 → `degenerate_standards` ✓；P6 单点频率轴可解（与 core 频率轴无最小点数规则一致，非缺陷）；P7 int 轴自动转 float64 ✓；
- P8 溢出量级（1e300）与 P8b（1e200）→ 抛异常 fail-closed，但异常类型为 TypeError（→ P3-1）；P8c 质量指标直接构造 inf → TypeError（同根因）；P8d 负值（有限）→ 干净 DomainError ✓；
- P9 容器 apply：real dtype → DTYPE_MISMATCH、行数不符 → SHAPE_MISMATCH、输出只读、双通道恢复 1e-9、apply 输出重开写不影响 profile 内部状态（caller-owned copy）✓；
- P10 `profile_for` 全字段相等（display_name 不同即拒）✓；P11 2-D correct 输入不变 ✓；P12 质量指标随噪声数量级单调 ✓；P13 quality/profile FrozenInstanceError ✓。

结论：执行器套件 31 + 探针 15+ 全绿（除 P3-1/P3-2 两项已定性为非阻塞的边界瑕疵），实现未被任何探针击穿。

## 7. 报告与事实差异

1. **P3-1**：t2 报告「非有限 → INVALID_ARGUMENT 结构化拒绝」对直接 NaN/Inf **输入**成立（测试实证）；但**内部溢出**产生的非有限结果经质量指标路径抛 TypeError 而非 DomainError——t2 报告未提及该极端路径（输入需 ≥~1e155 量级，物理 S 参数场景不可达）。→ 与 §3 P3-1 相同。
2. 门禁耗时：t2 声称 278.52s，本审查者实测 275.37s——同一数量级、机器负载噪声，不计差异。
3. 红灯证据（ModuleNotFoundError/exit 2）：无法事后复现，标为「未发现反证」。
4. 其余声明（行数 1008/863/137/229、31 个测试函数、mypy 46→47、ruff 全绿、31/145/1086 数字、M06 单行 diff、未 commit/push/建分支、inScope 逐一相等、10 个源 SHA-256、黄金字面量）**全部与仓库事实一致**；黄金向量经审查者独立重跑冻结参考复现，逐位一致。

## 8. 剩余风险

- 归一化分母阈值 1e-12 与校正相对阈值 1e-12 为文档化常数，边界行为已由测试固化；真实 Cal Kit/硬件噪声水平需 ISSUE-028/029 真机阶段复核（CALIBRATION.md §6「阈值在算法迁移与真机测试阶段确定」——本 Issue 只保证明确拒绝而非伪值）。
- 相干平均为简单复数均值；参考同口径。若未来需按噪声方差加权，属 ISSUE-028 聚合会话职责。
- 私有属性越界访问逃逸口（P3-2）与极端量级输入错误类型（P3-1）均不改变 fail-closed 事实、不影响 raw 与公共契约；可随 P3 批次或合并提交一并处理。
- 本 Issue 未接 core `FrequencySweep`/`ProcessingHistory` 应用入口（ISSUE-032 职责）；下游 028/029/032 消费面已在计划文档 §11 说明，接口语义（profile_id 类型、channel 全字段绑定、轴严格相等）与 core 冻结契约一致。

## 9. 合并建议

- **可合并**：建议按 staged 流程由项目负责人授权将 t2 的 4 个 inScope 路径（连同 t1 基线单）合入 main，随后将 M06 L7 状态行标记 `Done`（参照 ISSUE-024/025/026 先例「复审 PASS 后负责人授权合并再标 Done」）。
- 合并范围 = 工作树现有 5 个未提交/未跟踪条目；无拆分必要（单一新模块 + 单一新测试文件，可整体回退：删除 2 个新文件、还原 M06 L7）。
- P3-1/P3-2/P3-3 为可选清理项，可随合并提交一并修正（建议）或按 ISSUE-024/025 先例在后续 P3 批次关闭；不构成合并阻塞。
- 合并后不进入 ISSUE-028；等待项目负责人决定。

## 10. 最小修复清单

1. （P3-1）`src/uav_gpr/calibration/osl.py` L434–437：质量指标 DomainError 的 context 移除原始 `value`（仅保留 `field`，或改为 `repr(value)`），保证非有限值也抛 `DomainError(INVALID_ARGUMENT)`；更彻底的做法是在 `_capture_repetition_errors` 入口先校验求解项有限性并抛干净错误。配一条失败测试：1e200 量级测量 → `DomainError(INVALID_ARGUMENT)`（非 TypeError）。
2. （P3-2，可选）`src/uav_gpr/calibration/osl.py` L465–589：若需机制级封死，将 profile 数组以不可变缓冲视图存储；否则在模块 docstring 补充「私有属性访问不属公共契约」一句说明。
3. （P3-3）`tests/contract/test_calibration_osl.py` L482–484：删除与 L478–480 重复的断言块。

（以上均不涉及语义变更；若选择暂缓，不影响本次 PASS 结论。）
