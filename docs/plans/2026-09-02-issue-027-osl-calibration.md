# ISSUE-027 实施计划：OSL 校准模型与求解器

日期：2026-09-02
执行器：AgentTeams `uav-gpr-issue-027-osl-calibration` 成员 engineer（任务 t2，attempt 634692fc-284b-4b75-860e-4cfe39f9d4ae）
基线件：[docs/reports/ISSUE_027_BASELINE_CONFIRMATION.md](../reports/ISSUE_027_BASELINE_CONFIRMATION.md)（main @ `f68efff`，工作树干净，门禁 1055 passed / 4 deselected，参考源本地副本 8/8 哈希对拍一致）
目标 Issue：ISSUE-027（`docs/issues/M06_CALIBRATION_PROCESSING.md` L5–40）；约束文档：`AGENTS.md` §2.1/§3/§9/§10/§12、`docs/CALIBRATION.md` §1/§3/§5/§6/§9、`docs/DATA_FORMAT.md` §8、`docs/REFERENCE_MIGRATION.md` §5/§6/§9、ADR-0005、t1 基线确认单 §3.4/§3.5/§5。

## 1. 目标与用户价值

在 `calibration` 层交付一端口 Open/Short/Load 三项误差模型的**不可变模型与求解器**（纯计算、无 UI/硬件/存储依赖）：校准标准件（理想 Open=+1/Short=−1/Load=0 与可选频变复数 Cal Kit 模型）、校准 profile（显式绑定 `ChannelSpec`，S11/S22 各自独立，S21/S12 拒绝）、频率轴（严格递增有限）、OSL 复数求解与应用核心数学（`m = D + T·Γ/(1−Γ·S)` 与校正 `(m−D)/(T+S·(m−D))`）、奇异/退化检测（scale-free 归一化分母守卫）与数值质量指标（逐标准件 per-capture 重复性 rms/max）、有序多通道容器绑定（`OslCalibrationSet`，错配 fail-closed）。它是 ISSUE-028（无 UI 参考采集会话聚合）、ISSUE-029（`.rcal` 字段与质量报告）与 ISSUE-032（`OslCalibrationStage` 的 `frequency_raw → frequency_calibrated` 数学内核）的直接依据；本 Issue 不产生 domain 包装/history（032 职责）。

## 2. 范围（任务契约 inScope = 4 个精确路径，changedPaths 与此逐一相等）

1. `src/uav_gpr/calibration/osl.py`（新模块：OSL 模型 + 求解器 + 应用数学 + 容器）
2. `tests/contract/test_calibration_osl.py`（新文件：契约测试）
3. `docs/plans/2026-09-02-issue-027-osl-calibration.md`（本计划文档，含迁移清单与执行日志）
4. `docs/issues/M06_CALIBRATION_PROCESSING.md`（仅 ISSUE-027 状态行 L7：`Planned → In progress → Review`，勿动其他条目）

## 3. 明确排除项（M06 L22–24 + 提示词 + 任务契约）

不采硬件/标准件（无采集会话状态机——ISSUE-028）、不保存/加载 `.rcal/.rcbg`（ISSUE-029）、不做 UI/向导、不做空采背景（ISSUE-033）、不做 stage/history/provenance 包装与二次校准拒绝语义（ISSUE-032，模型层不提供对 `FrequencySweep`/`ProcessingHistory` 的应用入口，只交付纯 numpy 数学与模型）；不做频率插值/外推（严格轴相等 fail-closed）；不改 `core/**`（`ChannelSpec`/`SParameter`/`CalibrationProfileId`/`DomainError`/`ErrorCode` 只读消费，不新增错误码）；不改 `src/uav_gpr/calibration/__init__.py`（占位 docstring 原样；测试直接导入 `uav_gpr.calibration.osl`）；不改 `docs/reports/**`、`docs/CALIBRATION.md`、`docs/DATA_FORMAT.md`、`docs/REFERENCE_MIGRATION.md`、`docs/reference-baselines/**`、`docs/adr/**`、`tools/**` 与参考仓库（只读）；不 commit/push/merge、不创建/切换分支；不新增 inScope 之外文件。

## 4. 设计决策（D1–D10，2026-09-02 定案；若与 captain 意图冲突以任务契约为准并先报告）

- **D1 求解代数与奇异检测（转录参考闭式 + scale-free 守卫；实现中修正一次）**：误差模型 `m = D + T·Γ/(1 − Γ·S)` 与校正 `x = m − D; Γ' = x/(T + S·x)` 与参考源逐字一致（rebar `calibration/osl.py` docstring L3–12、`calibration_reference/osl_calibration.py` L275–300/174–178 审计确认）。**注意：该模型 D 与 S 耦合（m(1−SΓ) = D(1−SΓ)+TΓ 含 D·S 交叉项），对未知数并非联合线性**——首轮尝试的逐频点 3×3 线性化 `[1, Γ_i, m_i·Γ_i]` 实际求解了另一族方程（D/S 正确但 T 差 S·D 量级，实测 max|ΔT|≈1.8e-3，红灯暴露后弃用），最终按审计转录参考闭式解（denominator/D/S/Δ 四式，`T = D·S − Δ`）。奇异判定：参考绝对 `|denom| < 1e-15` 改为 **scale-free 归一化分母守卫**——`|denom| / (|lc·oc·(om−lm)| + |lc·sc·(lm−sm)| + |oc·sc·(sm−om)|) ≤ 1e-12` 任一点成立即 `DomainError(INVALID_ARGUMENT)`，context `kind="degenerate_standards"`/`first_index`/`normalized_denominator`（覆盖参考退化语义并更稳；任一点退化整体 fail-closed）。数值对拍证据：与冻结参考执行值（黄金向量 §8）容差内一致、DUT 前向恢复机器精度、闭式公式间互验（`T=DS−Δ` vs 经典 `T=A(1−S)`）三重核对。
- **D2 校正奇异守卫（相对阈值）**：`denom = T + S·x`；当 `|denom| ≤ 1e-12·(|T| + |S·x|)`（逐元素）→ `DomainError(INVALID_ARGUMENT)`，context `kind="correction_singular"`、`first_index`——0/0 与放大 >1e12 的场景一律拒绝，不产出伪值（验收「非有限/奇异结果明确不伪造」）。
- **D3 轴策略（适配：参考插值 → 严格相等）**：参考 `correct()` 接受请求频率网格并对误差项做带内插值（`complex_interpolate`，带外拒绝）；本项目 **profile 轴与 DUT 数据轴必须逐点相等**，不等长/不等值即 `AXIS_MISMATCH` fail-closed（DATA_FORMAT §8 硬性轴匹配口径；ISSUE-031/036 的轴复用语义由后续编排保证）。`interpolation.py` 不迁移。
- **D4 profile 绑定（适配：S 参数 → 全 ChannelSpec）**：参考 profile 仅绑 `s_parameter`；本项目 profile 绑定**完整 `ChannelSpec`**（channel_id + logical_polarization + s_parameter + display_name），构造时 `s_parameter ∈ {S11, S22}` 否则 `CHANNEL_CONTRACT_MISMATCH`（OSL 仅覆盖反射通道）。S11/S22 各自独立 profile、独立求解，互不共用误差项（CALIBRATION.md §3）。
- **D5 profile ID（适配 core）**：`profile_id: CalibrationProfileId`（core 冻结 UUID 标识）；builder 缺省 `CalibrationProfileId.new()`。参考的 `schema_version`/`created_utc`/自由 metadata 与保留键机制 **不迁移**（记录时间戳/软件版本/内容摘要属 `.rcal` schema，ISSUE-029）。
- **D6 质量指标（参考 9 键 → 本项目 6 值，记录排除）**：采纳参考「per-capture 重复性」口径——对每个标准件的每次 capture 用求解后 D/T/S 校正并与 actual 比较，`err=|Γ'_k − Γ_actual|` 对全部 capture×频率聚合为 `rms_abs_error`/`max_abs_error`（参考 `_QUALITY_KEYS` 后 6 键同口径）；**排除**参考前 3 键「均值拟合残差」（对均值求解 3 未知 3 方程 → 拟合残差数学上恒 ≈ 机器精度，不具质量信息，参考自身注释也仅称内部自洽指标）。质量对象冻结为 `OslCalibrationQuality`（open/short/load 各 rms/max 六浮点 + 校验非负有限），capture 计数驻留 profile。
- **D7 输入契约与错误码映射（core 冻结，不新增 code）**：标准件测量支持 `(frequency,)` 或 `(capture, frequency)` 复数数组；只接受复数 dtype（`DTYPE_MISMATCH`）；维度非法 `SHAPE_MISMATCH`；点数与轴不符 `AXIS_MISMATCH`；轴非有限/非严格递增 `NON_FINITE_AXIS`/`NON_INCREASING_AXIS`；测量/标准件值非有限、空 capture、退化求解、校正奇异 `INVALID_ARGUMENT`（context 分 kind）；通道错配 `CHANNEL_CONTRACT_MISMATCH`；容器重复通道 `DUPLICATE_CHANNEL`。参考 `ValueError`/`CalibrationNumericalError`/`CompatibilityError`/`OutOfBandError` 类型族不迁移。消息一律 ASCII（DomainError 契约）。
- **D8 不可变机制（不迁移 `_base.py`，按 UAV 口径自含）**：profile 构造时对全部数组防御性拷贝、`setflags(write=False)`；属性一律返回 `.view()`（base 只读 → 视图无法再开写，堵 `setflags(write=True)` 攻击）；frozen dataclass（`slots=True, eq=False`，自定义 `__eq__`（`np.array_equal`）+ `__repr__`）禁止属性改写。`build_osl_calibration` 与 `correct` 绝不修改输入数组。
- **D9 多通道容器（有序绑定，M06「多通道容器保持有序绑定」）**：`OslCalibrationSet` = 有序 `tuple[OslCalibrationProfile, ...]`（位置即通道顺序）：非空（`INVALID_ARGUMENT`）、通道唯一（`DUPLICATE_CHANNEL`）、全 profile 频率轴逐点相等（`AXIS_MISMATCH`）。`profile_for(channel)` 精确匹配（全字段相等），未知/错配 → `CHANNEL_CONTRACT_MISMATCH`；`apply(measured, channels)` 校验 `channels` 元组与容器通道逐位全等（错序/换通道/换 S 参数即拒）、`measured` 形状 `(n_channel, frequency)` 与复数 dtype、逐行校正，返回新数组（输入不变）。
- **D10 模块与测试落点**：`src/uav_gpr/calibration/osl.py` 单一模块（模型+求解+容器，实测 1008 行，含逐属性只读视图与验证 docstring）；`tests/contract/test_calibration_osl.py` 契约测试（实测 863 行 / 31 个测试函数；纯确定性：合成前向模型，无硬件/线程/sleep/串口；随机仅用固定 `default_rng(seed)` 的带噪用例）。模块 `__all__`：`OslStandard`、`OslCalibrationQuality`、`OslCalibrationProfile`、`OslCalibrationSet`、`build_osl_calibration` 与两个文档化阈值常量（`SOLVE_DEGENERACY_TOLERANCE`/`CORRECTION_SINGULAR_TOLERANCE`）。质量对象为扁平 6 值冻结模型（open/short/load × rms/max，无独立 `OslStandardQuality` 类——与计划草案差异在此落实，理由：与 rebar 9 键中采用的 6 键口径一一对应、便于 029 序列化）。`uav_gpr.calibration.__init__` 不改（占位原样）。

## 5. 文件改动（精确路径，无 glob）

| 路径 | 动作 | 内容 |
|---|---|---|
| `src/uav_gpr/calibration/osl.py` | 新增 | D1–D9 全部模型/求解/容器；模块 docstring 含误差模型、错误码映射表、下游契约（032/028/029）说明 |
| `tests/contract/test_calibration_osl.py` | 新增 | ~26 个契约测试（§6 矩阵），含参考黄金向量字面量（§8） |
| `docs/plans/2026-09-02-issue-027-osl-calibration.md` | 新增 | 本文档（决策 + 迁移清单 + 执行日志） |
| `docs/issues/M06_CALIBRATION_PROCESSING.md` | 修改 | 仅 L7 状态行 `Planned → In progress → Review` |

## 6. 测试矩阵（失败测试优先，先红灯后绿灯；纯确定性）

1. 理想 OSL 恢复已知 DUT：合成平滑误差项 D/T/S + 理想标准件前向测量 → 求解项与注入值、`correct(前向 DUT)` 与已知 Γ_DUT 全频点一致（atol/rtol 1e-9）。
2. 参考数值对拍（黄金向量）：固定场景（41 频点 0.5–2.5 GHz，§8）执行冻结参考实现得 D/T/S 与校正 DUT 采样点字面量 + 全向量和（checksum）字面量，容差内一致（采样 atol 1e-9、和 atol 1e-6）——验收 3。
3. 独立闭式交叉验证：理想标准件下 `D=m_l`、`S=(A+B')/(A−B')`（A=m_o−m_l、B'=m_s−m_l）、`T=A(1−S)` 与求解数组一致（1e-9）。
4. S11/S22 独立 profile：双通道各用独立误差模型建 profile，两套 D/T/S 互不相同、各自恢复本端口 DUT；容器 apply 逐行正确；跨端口 `profile_for`/apply 错配拒绝。
5. 容器有序绑定：`profile_for` 精确匹配；错序 channels、未知 channel、S 参数不同 → `CHANNEL_CONTRACT_MISMATCH`；重复通道 → `DUPLICATE_CHANNEL`；不同轴 → `AXIS_MISMATCH`；空容器 → `INVALID_ARGUMENT`。
6. 带噪/多 capture：固定 seed 噪声 2e-4 × 24 captures → 相干平均后 DUT 恢复误差有界（<1e-2 宽松界 + 与噪声 1e-6 场景的指标分级对比）；质量指标有限、非负、随噪声数量级变化；噪声-free 场景指标 ≈ 0（<1e-9）。
7. 奇异/退化：open 与 short 测量逐点相等（理想标准件）→ `INVALID_ARGUMENT`（kind=degenerate_standards）；仅单频点退化 → 同样拒绝；健康近退化（差 1e-6）→ 正常求解（fail-closed 有界）。
8. 非有限：NaN/Inf 测量、actual、轴 → 拒绝（轴走 `NON_FINITE_AXIS`，数据走 `INVALID_ARGUMENT`）。
9. dtype/shape：float 测量、复数轴、3-D 测量、空 capture、点数不符 → `DTYPE_MISMATCH`/`SHAPE_MISMATCH`/`AXIS_MISMATCH`/`INVALID_ARGUMENT`。
10. 校正奇异 DUT：`measured = D − T/S`（使 denom≈0）→ `INVALID_ARGUMENT`（kind=correction_singular）。
11. axis 错配：`correct` 长度不符、容器 apply 长度不符 → `AXIS_MISMATCH`。
12. raw 不可变：build/correct/apply 前后输入逐位不变；构造后改原数组不影响 profile；profile 属性视图 `setflags(write=True)` 抛 `ValueError`；属性赋值抛 `FrozenInstanceError`。
13. 频变 actuals：Cal Kit 模型（逐频点复数）下求解与 DUT 恢复一致（1e-9）；标量 actual 自动广播；长度不符 → `AXIS_MISMATCH`。
14. 回归：依赖定向（core enums/frequency/channels/identifiers/time_domain/manifest）+ 全量 `tools/quality/verify.py`（1055 + 新增数 passed / 4 deselected）+ ruff + mypy（46 → 47 文件）+ import（含 `uav_gpr.calibration.osl`）+ `git diff --check` + 工作树检查（仅 inScope 4 路径）。

## 7. 门禁命令（t2 完成时全绿；解释器以 `.venv/Scripts/python.exe` 为主，WSL `python3` 等价口径在日志注明）

```text
./.venv/Scripts/python.exe -m pytest tests/contract/test_calibration_osl.py -q          # 定向（先红灯后绿灯）
./.venv/Scripts/python.exe tools/quality/verify.py                                       # 全量非硬件 + ruff + mypy + import
./.venv/Scripts/python.exe -m ruff check src tests
./.venv/Scripts/python.exe -m mypy src
git diff --check && git status --porcelain=v1 -b
```

新基线预期：1055 + 新增非硬件测试数 passed / 4 deselected；mypy 46 → 47 文件（calibration/osl.py）。

## 8. 参考源审计与迁移清单（ISSUE-001 manifest 白名单 + 受控只读补充，2026-09-02 实测对拍）

原始 `E:\钢筋仪软件开发` 本环境不可达（t1 §3.5-1 实测 os error 3）；审计/执行基 = 本地只读副本 `D:\博士任务\rebar-inspector`。**白名单 8 文件 SHA-256 与 manifest 冻结值逐字节一致（t1 §4 实测）**；迁移只「提取契约并适配」，不复制整模块。

| # | 源文件（副本相对路径） | SHA-256（= manifest 冻结） | 采用 | 排除/适配 |
|---|---|---|---|---|
| 1 | `src/rebar_inspector/calibration/osl.py`（765 行，通读审计） | `afbc6a73dedf261c223ca2c798adccaba19894bf4627e99fcc94cc38e0e5d3d9` | 三项误差模型与校正代数（D1）；理想 + 频变 actuals（D3/§6-13）；`(freq,)/(capture,freq)` 复数输入与相干平均；per-capture 重复性指标口径（D6）；分母守卫语义（D1/D2 改相对阈值）；输入不变量与只读视图机制思路（D8） | `schema_version`/`created_utc`/free metadata/保留键 → ISSUE-029 `.rcal`（D5）；`check_compatible` config 告警 → ISSUE-029；`correct` 网格插值 → 严格轴相等（D3）；profile_id → core `CalibrationProfileId`（D5）；S 参数绑定 → 全 `ChannelSpec`（D4）；错误类型族/中文消息 → `DomainError`+ErrorCode（D7）；均值拟合残差 3 键 → 不采纳（D6）；求解闭式 → 逐式转录（含 `T=DS−Δ`），退化守卫 → scale-free 归一化分母（D1；3×3 线性化首轮尝试被红灯否决，见 §10 执行偏差 2） |
| 2 | `src/rebar_inspector/calibration/_base.py` | `8cd4fe95d3883283dbc4e8c10858bd972a4bc76c46f9a4b6ff79cc3c8590e8de` | 只读数组/防御性拷贝行为语义（D8 思路） | FrozenModel/deep_freeze/序列化工具不迁移（UAV frozen dataclass + 自含 helpers） |
| 3 | `src/rebar_inspector/calibration/errors.py` | `b6858162044d38220b5bb475304fd45aab6671701b017db07444378f1da77d47` | — | 全部排除：错误族 → UAV `DomainError` 映射（D7，core 错误码冻结不加新码） |
| 4 | `src/rebar_inspector/calibration/interpolation.py` | `9dc1b302be7fe9c0b67f293e79827a398fbb8f811312fcce8fa6625dcabf3da7` | — | 全部排除：`complex_interpolate`/`ensure_in_band` 不需要（D3 严格轴相等） |
| 5 | `src/rebar_inspector/calibration/__init__.py` | `0b5ed58170b7346484b65c500f6654322577c0fa2ee0b1c437413154e1a91258` | 惰性导出思路不采纳（UAV `__init__` 不改，直接模块导入） | 导出面/包结构不迁移 |
| 6 | `src/rebar_inspector/core/enums.py` | `08129eb7e2419ad413754d19f5d5552c051df64ad60747d4ff990ff027866b19` | `SParameter` 语义核对（S11/S22 反射 vs S21/S12 传输）：与 UAV core `SParameter` 值语义一致 | 不迁移：UAV core enums 只读消费 |
| 7 | `src/rebar_inspector/calibration/background.py` | `db96c7ba287e6e90cd273e7d71abc4c1f2172135c135ab7c9c2d67bf33435c86` | —（白名单核对；空采背景属 ISSUE-033，不迁移） | 全部排除 |
| 8 | `src/rebar_inspector/calibration/dual_reflection.py` | `6f3725e571185325047a39352db662770f787fb3962136fc3d6986890ebf70a8` | —（白名单核对；双反射配置/文件属 ISSUE-022/029 范畴，不迁移） | 全部排除 |

**受控只读补充（非 manifest 白名单；tests/ 被 manifest 明确排除——只作思路/执行参考，不迁移任何代码与数值）**：

| 源文件 | SHA-256（2026-09-02 实测） | 用途 |
|---|---|---|
| `calibration_reference/osl_calibration.py`（320 行，独立参考实现，numpy-only 可执行） | `0e278bf009b661ef066b845d7175fe047df538ef80721fa2ff3325f21dd3921d` | **黄金样本执行源**：2026-09-02 以 python3 3.12.3 执行其 `build_osl_calibration`/`correct`（合成前向场景），产出黄金向量（见下）；其自身 docstring 声明与生产 `osl.py` 数值对拍达机器精度 |
| `tests/test_calibration_osl.py`（661 行） | `2e566854c7aadb594c59e49c99eeee52dba21de5b850c11bbf71f96908661874` | 合成场景 procedure 参考（`_synthetic_terms`/`_forward`/`_small_frequency_grid` 思路，L38–76）——重写为独立测试代码，不复制 |

**黄金样本（独立合成 + 冻结参考执行对拍，验收 3 依据）**：

- 输入（合成，与任何实测数据无关）：`f = linspace(0.5e9, 2.5e9, 41)`；`norm=(f−f0)/(f1−f0)`；误差项 `D=0.025+0.008j·e^{1j·norm}`、`T=(0.91−0.04j)·e^{−0.08j·norm}`、`S=0.07+0.015j·norm`；理想标准件 Open=+1/Short=−1/Load=0；前向 `m = D + Γ·T/(1−Γ·S)` 单 capture 精确测量；DUT `Γ=0.5·e^{1j·π·norm}·(1−0.1·norm)`。
- 执行：`sys.path.insert(0, "…/calibration_reference"); import osl_calibration; ref.build_osl_calibration(f, m_open, m_short, m_load).correct(f, m_dut)`（numpy 2.5.2、Python 3.12.3；参考自检 max|corr−Γ_DUT|=1.69e-16，恢复误差 ≤3.9e-16——机器精度）。
- 产出（记录于 `tests/contract/test_calibration_osl.py` 字面量）：D/T/S 与校正 DUT 在 idx {0,10,20,30,40} 的复数值；全向量复数和 checksum（sumD/sumT/sumS/sumCorr）。UAV 实现对拍容差：采样点 atol=1e-9/rtol=1e-9，checksum atol=1e-6（两套代数路径舍入远小于此，见执行日志实测最大差）。
- 测试内另含闭式公式间互验（§6-3：`T=DS−Δ` 转录式 vs 经典 `T=A(1−S)` 推导式），与冻结参考执行（黄金向量）共同构成对拍证据链。

## 9. 性能/数据风险

- 求解为逐频点向量化闭式（四式 + 归一化守卫）与校正广播聚合：F≈数千点时毫秒级；无循环热点、无 I/O、无锁/线程——AGENTS.md §7 无风险面。校正沿最后一维广播（单道 `(f,)`/道集 `(n,f)` 同路径）。
- 输入绝不修改：全部数组路径防御性拷贝 + 只读视图；raw 语义在模型层无 `FrequencySweep` 写面（032 才挂 domain 对象）。
- 无 schema/协议变更（`.rcal` 属 029）；无新增 core 错误码（D7）；回退 = 删除 2 个新文件、还原 M06 L7 状态行。
- 数值风险点：归一化分母阈值 1e-12 与校正相对阈值 1e-12 为文档化常数；边界行为由 §6-7/§6-10 测试固化；真实 Cal Kit/硬件噪声水平需 ISSUE-028/029 真机阶段复核（记录于 CALIBRATION.md §6「阈值在算法迁移与真机测试阶段确定」——本 Issue 只保证明确拒绝而非伪值）。

## 10. 执行日志（实际数字，2026-09-02 实测）

环境：Windows `.venv/Scripts/python.exe` **3.13.14**（pytest 8.4.2、ruff 0.16.5、mypy 1.20.2、numpy 2.5.2；t2 门禁主口径，任务契约首选解释器）+ WSL `python3` 3.12.3（ruff 0.16.4，t1 基线口径交叉复核）；工作树 main @ f68efff（t1 基线，1055 passed / 4 deselected / mypy 46 files）。

| 步骤 | 命令 | 退出码 | 结果 |
|---|---|---|---|
| 红灯（实现前） | `./.venv/Scripts/python.exe -m pytest tests/contract/test_calibration_osl.py -q` | 2 | `ModuleNotFoundError: No module named 'uav_gpr.calibration.osl'`——collection error（1 error during collection，pytest 退出码 2），失败测试先行证据（31 个测试函数在模块缺失下整体无法收集） |
| 绿灯（实现后终态） | 同上 | 0 | **31 passed in 0.09s**（31 个测试函数全部通过；纯确定性：固定合成前向模型 + 固定 `default_rng` seed，无 sleep/线程/串口/硬件） |
| 定向+依赖回归 | `./.venv/Scripts/python.exe -m pytest tests/unit/test_core_frequency.py tests/unit/test_core_time_domain.py tests/unit/test_reference_manifest.py tests/unit/test_core_enums.py tests/unit/test_core_identifiers.py tests/contract/test_calibration_osl.py -q` | 0 | **145 passed in 2.98s**（114 依赖定向 + 31 新增，core 契约未被新模块扰动） |
| 全量门禁 | `./.venv/Scripts/python.exe tools/quality/verify.py` | 0 | pytest **1086 passed, 4 deselected in 278.52s**（= 1055 基线 + 31 新增，算术核对一致）→ ruff `All checks passed!` → mypy `Success: no issues found in 47 source files`（46 → 47，含 calibration/osl.py）→ `package import ok` → `[quality] all gates passed`（VERIFY_EXIT=0 日志捕获） |
| ruff 显式 | `./.venv/Scripts/python.exe -m ruff check src tests` | 0 | `All checks passed!`（修复轮详见偏差 3） |
| mypy 显式 | `./.venv/Scripts/python.exe -m mypy src` | 0 | `Success: no issues found in 47 source files`（46 → 47，含 calibration/osl.py） |
| import | `./.venv/Scripts/python.exe -c "import uav_gpr, uav_gpr.core, uav_gpr.calibration, uav_gpr.calibration.osl, uav_gpr.processing, uav_gpr.storage, uav_gpr.positioning"` | 0 | `package import ok` |
| Git | `git diff --check && git status --porcelain=v1 -b` | 0 | diff-check clean；工作树仅含 inScope 4 路径（M06 M + osl.py/tests/plan ??）+ t1 交付物基线单（??，非 t2 改动） |

执行偏差记录：

1. **首轮绿灯 3 轮修复后达成（红灯阶段即暴露两个真实缺陷）**：(a) 初版求解用逐频点 3×3 线性化 `[1, Γ_i, m_i·Γ_i]`——红灯揭示 D/T/S 中 T 系统性偏差（max|ΔT|≈1.8e-3 = |S·D|），原因是 OSL 模型对 (D,S) 非线性耦合、线性化求解的是另一族方程；改按审计转录参考闭式（denominator/D/S/Δ，`T=D·S−Δ`）后 T 恢复机器精度（D1 已更新记录）；(b) numpy 2.x `solve` 对 `(N,3)` 的 b 不再按 (M,) 列向量批量解释——转闭式后该问题自然消失（原 b reshape `(N,3,1)` 修复一度通过测试，后被闭式取代）；(c) `test_nonfinite_inputs_rejected` 初版把 NaN actual 传成长度 1 数组，先触发 AXIS_MISMATCH 而非非有限校验——改为整轴 NaN 数组（测试自身修正，非实现缺陷）。
2. **不可变机制两轮重构**：dataclass 公共字段直接暴露 readonly base——numpy 允许对**自有数据 base** `setflags(write=True)` 重新开写（仅视图禁止），immutability 攻击测试红灯否决；最终按参考源模式「write=False base + property 返回 `.view()`」+ 冻结属性（`FrozenInstanceError`），`setflags(write=True)` 攻击测试绿灯。期间 ruff RUF022/023（`__all__`/`__slots__` 排序）与测试文件 F841/E501 修复，非语义改动。
3. **门禁基线推进**：1055 → **1086 passed / 4 deselected**（+31，verify.py 实测 278.52s）；mypy 46 → **47 文件**；ruff/mypy/import 全绿。
4. M06 L7 状态行按流程 `Planned → In progress → Review`（两步编辑，diff 仅该行）。
5. 全量门禁在 Windows `.venv`（Python 3.13.14）完成；与 t1 WSL `python3` 3.12.3 口径交叉一致（t1 全量 1055/4；ruff 版本 0.16.5 vs 0.16.4 无差异结果）。

## 11. 给下游 Issue 的语义说明（ISSUE-028/029/032 消费方）

- `OslCalibrationProfile` 是**已求解**模型（含 D/T/S、measured means、actuals、capture 计数、质量指标），profile_id 为 core `CalibrationProfileId`——028 会话聚合后调 `build_osl_calibration`；029 序列化字段（频率轴、复数编码、每标准件指标、capture 计数）与 §8 口径一致；032 stage 校验 `profile.channel`/轴/域后逐通道调 `profile.correct`（profile 的 `channel` 全字段绑定提供最强错配防线）。
- 本模块不 import `core.frequency`/`core.time_domain`（无 `FrequencySweep` 依赖），保持 `calibration → core(channels/enums/errors/identifiers)` 单向、纯 numpy+stdlib 计算层；`correct` 是数学内核，可被 032 逐行（trace×channel×frequency 最后一维广播）复用。
- 错误码映射表（D7）是 029 兼容性报告与 032 fail-closed 判定的共享口径；奇异/退化一律拒绝而非降级——不伪造结果。
