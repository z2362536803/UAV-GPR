# ISSUE-033 开工基线确认单（自动化轮 · engineer）

## 1. 目标 Issue 与依据

- **Issue**：ISSUE-033「空采背景处理阶段与数据域保护」（`docs/issues/M06_CALIBRATION_PROCESSING.md` L227-262）
- **状态行核查**：M06 L229 ISSUE-033 状态 = `Planned`；映射 FR-011、012；直接依赖 ISSUE-029、030、032。
- **工作区根替换声明**：Issue 提示词中 `E:\wurenjiruanjian` 一律替换为当前工作区根 `/mnt/d/博士任务/无人机软件/UAV-GPR`（Windows：`D:\博士任务\无人机软件\UAV-GPR`）。
- **流程依据**：`AGENTS.md`、`docs/issues/README.md`（编号顺序为主执行顺序、依赖为开工门禁；本会话只执行 ISSUE-033）、`docs/ISSUE_REVIEW_STANDARD.md`（t1 基线 → t2 实现 → t3 独立复审）。
- **契约文档**：已读 `docs/CALIBRATION.md` §4（空采背景匹配规则）+ §5（处理顺序），`docs/PROCESSING.md` §1/§2（域链、引用/域兼容与 history 规则）。

## 2. 只读核查证据（全部实测）

| 项 | 结果 |
|---|---|
| HEAD / 分支 | `main @ 4cec913`（`docs(issues): mark ISSUE-032 Done after automated merge`）；`git status --porcelain` 核查前为空（工作树干净，除本单外无输出） |
| 远端同步 | `git rev-list --left-right --count origin/main...HEAD` = `0 3`：本地 main ahead of origin/main by 3 commits（032 合并链推送在网络重试中，以 captain 后台 push 为准，不在本单断言远端状态；origin/main 停在 `b4f6dec` = mark ISSUE-031 Done） |
| ISSUE-029 Done 证据 | M06 L80 状态行 = Done（2026-09-05 自动化轮 t3 PASS WITH CONDITIONS + F1(P2) 修复闭合后自动合并，见 `docs/reports/ISSUE_029_REVIEW_REPORT.md`）；合并提交 `fb758fe` 在 HEAD 历史（`git log --grep issue-029`）；tracked 模块 `src/uav_gpr/calibration/reference.py`（AirBackgroundReference/Session，含域声明与 profile id 约束）✓ |
| ISSUE-030 Done 证据 | M06 L118 状态行 = Done（t3 VERDICT=PASS 后自动合并，见 `docs/reports/ISSUE_030_REVIEW_REPORT.md`）；合并提交 `89fd9bb` 在 HEAD 历史；`bandpass.py`（ProcessingStage/StageResult 契约）tracked ✓ |
| ISSUE-032 Done 证据 | M06 L192 状态行 = Done（t3 VERDICT=PASS 后自动合并，见 `docs/reports/ISSUE_032_REVIEW_REPORT.md`）；合并提交 `4e5349e` + 标记提交 `4cec913`（= HEAD）；`osl_calibration.py` tracked ✓ |
| 依赖定向回归 | `.venv/Scripts/python.exe -m pytest tests/contract/test_calibration_reference.py tests/unit/test_core_time_domain.py -q` → **50 passed**；`tests/contract/test_processing_osl_calibration.py tests/contract/test_processing_bandpass.py -q` → **66 passed**（均 exit 0） |
| 可执行性 | `src/uav_gpr/processing/` 仅 `bandpass.py`（030）、`time_domain.py`（031）、`osl_calibration.py`（032），无任何背景减除 stage 先行实现；M06 中 033 起 `状态：Planned` 的最早条目即 ISSUE-033（L229；034 Dewow L266、035 Flat L303、036 编排 L340 其后）→ **ISSUE-033 为下一可执行 Issue**，无重复实现风险 |

关键本项目契约文件哈希（SHA-256 前 16 位，供 t3 交叉核对）：

```text
e2e5403766c45f88  src/uav_gpr/calibration/reference.py
f707839674ceb5e1  src/uav_gpr/processing/bandpass.py
30224c9a0091c02b  src/uav_gpr/processing/osl_calibration.py
5b7136979df9e6ad  src/uav_gpr/calibration/osl.py
cfa8271f5ebd545c  src/uav_gpr/core/time_domain.py
4e906f159b1c8599  src/uav_gpr/core/enums.py
5ca0dc5fdd2ccedc  docs/CALIBRATION.md
9d730ec7a0f7c223  docs/PROCESSING.md
```

参考源说明：M06 ISSUE-033 条目未引用 `E:\钢筋仪软件开发` 具体迁移文件（空采参考对象已由 029 完成并复审 PASS）；033 是纯 domain-conversion stage，数值对拍对象 = 本项目复数逐元素减法 `mean_data`（029 黄金值来自其采集聚合 mean）。无需新的参考源哈希登记。

## 3. 契约要点（对 t2 实现有约束）

### 3.1 CALIBRATION.md §4/§5 处理顺序与匹配规则

```text
frequency_raw -> optional OSL -> frequency_calibrated (保存 OSL 后、空采前)
  -> optional air background subtraction -> optional bandpass -> IFFT
```

空采参考在明确输入域采集：`raw` 或 `osl_calibrated`。应用时必须硬匹配：**channel/S 参数和顺序、完整频率轴、数据域；校准域须匹配 `calibration_profile_id`**；设备/天线配置硬性兼容字段。环境/离地高度/安装/日期等为重要元数据/警告项（软差异不硬拒）。不得覆盖 raw；不得把 raw 域背景应用到校准域。§8 错误边界明列：空采域/profile 不匹配、参考长度不匹配、非有限值均为拒绝对象。

### 3.2 PROCESSING.md §1/§2 强约束（逐条落到 033）

1. 频域派生链固定：`frequency_raw → frequency_calibrated → frequency_background_applied → frequency_filtered → time_base → time_processed`。033 的两个合法 hop：`RAW → BACKGROUND_APPLIED`、`CALIBRATED → BACKGROUND_APPLIED`（core `_ALLOWED_TRANSITIONS` 均已开）。
2. **输出 `frequency_background_applied` 必须带 `background_reference_id`**（core `_validate_references` 已强制）；后续记录显式携带的引用必须与产生其对应域输入的上一记录相同（`_validate_provenance_continuity` 已强制，省略重复引用合法）。
3. 稳定 `stage_name` 同一 history 内不得重复应用（`ProcessingHistory.__init__` 强制，bump version 不能绕过）→ 033"重复背景检测"由 core + stage 双重 fail-closed。
4. history 第一项输入域必须是 `frequency_raw`（`_START_DOMAINS`）；从 calibrated 快照开始无 anchor 不允许。
5. 每阶段输入不可变、输出新对象、追加可序列化参数/版本/历史（AGENTS.md §3 同文）。

### 3.3 core 层既有守卫（t2 直接复用，不新建平行类型）

- `DataDomain.FREQUENCY_BACKGROUND_APPLIED = "frequency_background_applied"`（enums L170）已在 `_ALLOWED_TRANSITIONS` / `_validate_references` / `_validate_provenance_continuity` 全链路消费。
- `BackgroundReferenceId(_UuidId)`（identifiers L140-143，label `background_reference_id`）——record 的引用字段类型现成。
- `ProcessingRecord` 构造校验三件套 + `to_dict/from_dict` JSON-safe 往返；`ProcessingHistory.append` 链校验 + 重复 stage 拒绝 + provenance continuity。
- `FrequencySweep`/`FrequencyScan`：data 写入即只读 complex128 view；形状 `channel×frequency` / `trace×channel×frequency` 严格校验；输出重建同容器类型、保留 channels 与 per-trace metadata。
- 030/032 模式沿用：`ProcessingStage` Protocol、`StageResult(source, history, domain)`、`_record_for`、`_input_domain_of`（history 空 ⇒ `FREQUENCY_RAW`）均在 `bandpass.py` 导出面——t2 从 `uav_gpr.processing.bandpass` import 公共符号（同层 processing 包内引用，符合 AGENTS.md §9），不复制第二套；032 的 `osl_profile_digest`（canonical JSON SHA-256）可作为 digest 计算姿势参照。

### 3.4 029 reference 对象消费面（`src/uav_gpr/calibration/reference.py`，只读，不改一字节）

| API | 语义 | 033 用法 |
|---|---|---|
| `ReferenceDomain`（L110-114） | `RAW="raw"` / `OSL_CALIBRATED="osl_calibrated"` | stage 输入域 ↔ reference.domain 严格匹配的唯一判据 |
| `AirBackgroundReference`（L533-541，frozen dataclass slots） | 字段：`channels: tuple[ChannelSpec,...]`、`frequency_hz: np.ndarray`、`mean_data: np.ndarray`、`trace_count: int`、`domain: ReferenceDomain`、`calibration_profile_id: CalibrationProfileId \| None` | t2 的减数对象（reference.mean_data 按 channel×frequency 广播到 scan） |
| `AirBackgroundSession(..., domain, calibration_profile_id)`（L544+） | 构造即 fail-closed：OSL_CALIBRATED 域必须显式给 profile id；RAW 域禁止携带 profile id | 033 **不调用**（排除项：不做参考采集）；此约束证明 reference 对象的域/profile 绑定已由 029 保证，t2 只消费 |
| `.build()`（L629-653） | COMPLETED 才产出；`mean_data=_readonly(np.mean(stack))`、`frequency_hz=_readonly(axis)` | 只读语义已在源头保证；t2 断言使用前后内容不变 |

**注意缺口（t2 设计决策点）**：`AirBackgroundReference` 本身**没有** `reference_id` 字段，也没有 digest 属性。record 的 `background_reference_id: BackgroundReferenceId` 供给方式需 t2 定稿：推荐 stage 构造入参接受显式 `BackgroundReferenceId`（调用方/存储层提供），parameters 另记 `mean_data` content digest（canonical 结构 hash，测试锁定格式）；calibrated 域比较以 `reference.calibration_profile_id ==` history 末记录 `calibration_profile_id`（ID 严格相等）为主判据 + digest 辅助审计。029 存储层 `.rcbg` 已含 reference ID/digest（`storage/reference_files` 侧），t2 可读其编码函数素材但不引入 storage 依赖（分层：processing 不依赖 storage）。

### 3.5 团队计划硬约束（t2 inScope）

inScope 精确 4 路径（changedPaths 逐一相等）：

1. `src/uav_gpr/processing/air_background.py` — `AirBackgroundSubtractionStage`（新文件）
2. `src/uav_gpr/processing/__init__.py` — 导出（编辑合规）
3. `tests/contract/test_processing_air_background.py` — 契约测试（新文件）
4. `docs/issues/M06_CALIBRATION_PROCESSING.md` — ISSUE-033 状态行 Planned → In progress → Review

（实施计划文档 `docs/plans/2026-09-05-issue-033-air-background.md` 为团队目标口径"含计划文档"的第 5 项候选——t2 开始时若纳入 changedPaths，须经 captain 在契约中锁定；本单如实记录两种口径，避免 t2 越界。）

## 4. 门禁基线（verify.py 复跑，实测）

- `tools/quality/verify.py`（interpreter = `.venv/Scripts/python.exe`）：
  - pytest (non-hardware)：**1249 passed / 4 deselected in 275.71s**（4 deselected = 硬件哨兵 + opt-in LibreVNA），与团队计划声明的 1249 一致 ✓
  - ruff：`All checks passed!` ✓
  - mypy：`Success: no issues found in 52 source files` ✓（计划口径 52）
  - package import ok；`[quality] all gates passed` exit 0
- 核查前后 `git status --porcelain` 均为空（本单为唯一新增未跟踪文件）；无重定向产物遗留。

## 5. 实施计划摘要（供 t2）

**范围**：`AirBackgroundSubtractionStage(reference: AirBackgroundReference, *, reference_id: BackgroundReferenceId)` 实现 `ProcessingStage`：
- `stage_name="air_background_subtraction"`（稳定 snake_case token）、`output_domain=FREQUENCY_BACKGROUND_APPLIED`、`input_domain={FREQUENCY_RAW, FREQUENCY_CALIBRATED}`。
- `apply(source, *, history, executed_utc/clock)`：
  - 末域（空 history ⇒ RAW）∈ {RAW, CALIBRATED}，且该域必须等于 `reference.domain` 对应物：`RAW↔ReferenceDomain.RAW`、`FREQUENCY_CALIBRATED↔ReferenceDomain.OSL_CALIBRATED`；错配 ⇒ `PROCESSING_DOMAIN_MISMATCH` 拒绝（raw reference 不能用于 calibrated 数据，反之亦然）。
  - calibrated 场景：`reference.calibration_profile_id` 必须与 history 末记录 `calibration_profile_id` 严格相等（ID 全等；缺失即拒）；digest 作为 parameters 审计项写入。
  - 通道校验：`source.channels` 与 `reference.channels` 精确序一致（channel_id + S 参数全等，错序/缺/多拒绝）；`reference.frequency_hz` 与 `source.frequencies_hz` `np.array_equal`（错轴/长度不符拒绝）；`mean_data.shape == (len(channels), len(axis))`；reference/source 数据非有限值拒绝。
  - 数值应用：输出 = 逐元素复数减 `source.data - reference.mean_data`；sweep `(channel,frequency)` 直减；scan `(trace,channel,frequency)` 沿 trace 轴广播同一 reference，但**不沿 trace 做任何统计**（与 Flat/连续背景明确区分——Flat 是沿 trace 滑动平均，033 绝不触碰 trace 轴语义）。输入不可变：core 模型自动 read-only copy，输出新对象，channels/per-trace metadata 保留。
  - history：`_record_for(stage_name=..., input_domain=末域, output_domain=FREQUENCY_BACKGROUND_APPLIED, background_reference_id=reference_id, calibration_profile_id=（calibrated 输入时继承传递）, parameters={reference: {channel_ids, s_parameters, axis_digest?, mean_content_sha256, trace_count, domain}, algorithm:"air_background_complex_subtract_v1", ...})`；`ProcessingHistory.append` 二次挡同 history 重复 `air_background_subtraction`。
- **排除**：不实现沿测线 Flat Reflection、连续背景、参考采集（不调 `AirBackgroundSession`/`ControllerReferenceAdapter`）、不做 UI、不写 .rcbg 文件、不修改 reference.py/osl*.py/storage 任何字节。

**测试矩阵**（失败测试优先）：
1. 域错配双向拒绝（raw ref → calibrated 数据；osl_calibrated ref → raw 数据）；
2. 多通道顺序：ref channels 与 source channels 交换序 ⇒ 拒绝；正确双通道各自减自己 row 的对拍（逐 channel 数值 != 交换后结果）；
3. profile 错配：calibrated 数据挂 profile A、ref 声明 profile B ⇒ 拒绝；A==B 通过；ref OSL 域但 profile_id=None（手工构造绕过 session 校验）⇒ 拒绝；
4. 非有限值（ref mean_data NaN / source）与 shape/axis 不匹配拒绝；
5. 重复应用：同 history 再 apply ⇒ stage_name 重复拒绝；bump stage_version 不绕过；
6. 数值/history 对拍：手算小向量减法黄金值；record to_dict/from_dict 往返 JSON-safe；`background_reference_id` 必填校验（core 已强制，stage 不遗漏）；后续 bandpass 链上 provenance continuity 不破坏；
7. 输入不变性：source/reference 数组 bytes 前后一致 + readonly；scan 广播不依赖 trace 数（trace=1 与 N 结果一致）；
8. 与 Flat 区分：stage 不做沿 trace 统计的行为断言（同一 trace 常数平移不减均值）。

**门禁**：目标测试 → 相关定向回归（reference/time_domain/osl/bandpass）→ verify.py 全量（基线 ≥1249 passed + 新增用例数）→ ruff + mypy(52) + import；diff 检查 changedPaths 与 inScope 逐一相等。

## 6. 结论

三个直接依赖均有合并提交与测试实测证据：ISSUE-029（`fb758fe`，reference.py 域约束 frozen 对象 + 复审 PASS-F1-closed 报告）、ISSUE-030（`89fd9bb`，ProcessingStage/StageResult/_record_for 契约就绪）、ISSUE-032（`4e5349e`，OslCalibrationStage 提供 calibrated 域生产端 + provenance 模式参照）。core 层 `_ALLOWED_TRANSITIONS` 两个合法 hop、`_validate_references` background_reference_id 必填、`_validate_provenance_continuity`、重复 stage 拒绝全部现成；029 消费面（`AirBackgroundReference` 六字段只读对象 + `ReferenceDomain`）齐备。已知设计缺口（reference 无自带 ID/digest）已定位为 t2 决策点并有推荐方案。**门禁基线 1249 passed / 4 deselected、ruff clean、mypy 52 files clean、package import ok 全部实测复现（exit 0），核查前后工作树干净。ISSUE-033 可以开工（Ready）**，t2 按第 3 节契约与 inScope 4 路径执行。
