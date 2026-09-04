# ISSUE-033 实施计划：空采背景处理阶段与数据域保护

日期：2026-09-05
执行器：AgentTeams `uav-gpr-issue-033-bg-subtraction` 成员 engineer（任务 t2）
基线件：[docs/reports/ISSUE_033_BASELINE_CONFIRMATION.md](../reports/ISSUE_033_BASELINE_CONFIRMATION.md)（main @ `4cec913`，工作树干净，门禁基线 1249 passed / 4 deselected + ruff + mypy(52) + import 全绿；依赖 029/030/032 Done 实测证据齐全）
目标 Issue：ISSUE-033（`docs/issues/M06_CALIBRATION_PROCESSING.md` L227–262）；约束文档：`AGENTS.md` §3/§9/§10、`docs/CALIBRATION.md` §4/§5/§8、`docs/PROCESSING.md` §1/§2、t1 基线确认单 §3、captain t2 指派（inScope 口径锁定）。

## 1. 目标与用户价值

在 `processing` 层交付**独立的 AirBackgroundSubtractionStage**：在复数频域按通道/频率减去 029 冻结的 `AirBackgroundReference.mean_data`，把 `frequency_raw` 或 `frequency_calibrated` 严格转换为 `frequency_background_applied` 新对象。核心是**数据域保护**：raw 参考绝不能用于校准数据（反之亦然）；calibrated 域必须匹配产生该域的 calibration profile ID + 内容 digest；输入不可变、history 完整、重复应用拒绝、单道 sweep 与 scan 广播同语义。它是 ISSUE-036（完整编排/安全回放）的背景引用 provenance 前置，并与 Flat Reflection（ISSUE-035，沿 trace 统计）在数学上明确区分。

## 2. 范围（任务契约 inScope = 4 个精确路径，changedPaths 与此逐一相等；captain 锁定口径，无 __init__.py）

1. `src/uav_gpr/processing/background_subtraction.py`（唯一实现模块：`AirBackgroundSubtractionStage` + `background_reference_digest` + `require_matching_calibration_provenance` + `SafeReuseResult`/`check_safe_reuse`——stage 契约复用 030 的 `ProcessingStage`/`StageResult`/`_input_domain_of`，record 直接经 core `ProcessingRecord` 构造以携带 `background_reference_id`，沿用 032 先例）
2. `tests/contract/test_processing_background_subtraction.py`（唯一测试文件：契约 + 数值/history 对拍 + 域/profile/channel/axis 拒绝矩阵 + 重复应用双保险 + 输入不变性）
3. `docs/plans/2026-09-05-issue-033-bg-subtraction.md`（本计划文档，含 D 节决策记录与执行日志）
4. `docs/issues/M06_CALIBRATION_PROCESSING.md`（仅 ISSUE-033 状态行 L229：`Planned → In progress → Review`，勿动其他条目）

注：t1 基线单 §3.5 曾列 `processing/__init__.py` 为导出路径候选，captain t2 指派已作废该口径——实际执行以本节 4 路径为准，不创建/修改任何额外文件（测试直接 import `uav_gpr.processing.background_subtraction`，沿用 030/031/032 先例）。

## 3. 明确排除项（M06 L244 + 提示词 + 任务契约）

不实现沿测线 Flat Reflection（trace 轴滑动平均属 ISSUE-035）、不实现连续背景（time_processed 链属后续）、不做参考采集（不调用 `AirBackgroundSession`/`ControllerReferenceAdapter`/任何 acquisition 符号）、不做 UI、不保存/读取 `.rcbg` 文件（storage 零 import，AGENTS §9）；不改 `core/**`（`ProcessingRecord`/`ProcessingHistory`/`DataDomain`/`ErrorCode`/`BackgroundReferenceId` 只读消费，不新增错误码——复用 `PROCESSING_DOMAIN_MISMATCH`/`CHANNEL_CONTRACT_MISMATCH`/`AXIS_MISMATCH`/`SHAPE_MISMATCH`/`INVALID_ARGUMENT`/`DTYPE_MISMATCH`）、不改 `calibration/reference.py` 与 `calibration/osl.py`（消费面只读一字节不动）、不改 `processing/bandpass.py`/`osl_calibration.py`/`time_domain.py`（import 复用）、不改 `processing/__init__.py`；不修改 `docs/reports/**`、`docs/CALIBRATION.md`、`docs/PROCESSING.md`、`docs/adr/**`、`tools/**` 与参考仓库（只读）；raw/reference 数组绝不修改；不 commit/push/merge；完成后停止，不进入 ISSUE-034。

## 4. 设计决策（D1–D9）

- **D1 stage 契约与域映射**：`AirBackgroundSubtractionStage` 结构化实现 030 冻结的 `ProcessingStage` Protocol：`stage_name="air_background_subtraction"`（与 `test_processing_time_domain.py::_filtered_history` 既有 fixture 名称一致）、`stage_version="1.0"`、`input_domain=frozenset{frequency_raw, frequency_calibrated}`、`output_domain=frequency_background_applied`。reference 声明域 ↔ 数据域双射映射：`RAW↔ReferenceDomain.RAW`、`FREQUENCY_CALIBRATED↔ReferenceDomain.OSL_CALIBRATED`；非枚举 domain 值 fail-closed（仿 029 session L559 isinstance 检查）。历史为空 ⇒ 输入域 RAW（`_input_domain_of`），要求 raw 域参考。合法前驱仅这两个（CALIBRATION §5 "OSL 后、空采前"固定序；BACKGROUND_APPLIED/FILTERED/时域前驱 = 乱序或二次减除，拒）。追加走 `ProcessingHistory.append`（链校验 + 同 history stage_name 唯一，bump version 不绕过）。返回 030 的 `StageResult`（domain 恒 BACKGROUND_APPLIED）。
- **D2 引用 ID 供给（captain 裁决的执行）**：`AirBackgroundReference`（029 冻结）无自带 reference ID —— stage 构造签名 `AirBackgroundSubtractionStage(reference: AirBackgroundReference, reference_id: BackgroundReferenceId)`：`reference_id` 显式入参（调用方/存储层身份，core `_validate_references` 强制写入 record 的 `background_reference_id`），strict identity 校验：类型必须是 `BackgroundReferenceId`（UUID 型标签不匹配即 TypeError，防拿 CalibrationProfileId 冒充）。
- **D3 mean_data 内容摘要（digest）**：公开函数 `background_reference_digest(reference) -> str` = SHA-256(canonical JSON of storage-mirrored payload)，payload 字段与 `storage/calibration_files.AirBackgroundFilePayload.to_document()` 的核心内容域逐项对齐（`domain`/`calibration_profile_id`/`axis_unit:"Hz"`/`channels`(逐 ChannelSpec 五字段)/`frequency_hz`/`mean_data`），canonical dump 规则与 storage envelope 完全一致（`json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=True, allow_nan=False)` UTF-8），数组编码 `{dtype,shape,re[,im]}` 本地转写。**分层裁决（D3a，同 032 D3a）**：processing 不 import storage，payload 构造本地转写；golden digest 字面量测试钉死格式（漂移即红）。摘要覆盖通道序与轴本身：换通道序/改一个 float 位都翻转摘要。
- **D4 calibrated 域 profile ID+digest 匹配（验收核心）**：输入域 = `FREQUENCY_CALIBRATED` 时，三道门：① `reference.calibration_profile_id` 必须是 `CalibrationProfileId`（029 session 保证构建路径，但 dataclass 可直接构造，stage 再防御）；② 与产生该 calibrated 域的末记录 `calibration_profile_id` UUID **严格相等**；③ 该记录 parameters 中对应 profile 的 `content_sha256` == 当前绑定 profile 的重算 digest（同 032 `check_safe_reuse` 口径：**同 ID 不同内容也拒**）。为此提供可复用判定 `require_matching_calibration_provenance(history, reference) -> None`（抛 `PROCESSING_DOMAIN_MISMATCH` 带字段级差异 context）与纯判定 `check_safe_reuse(history, reference) -> SafeReuseResult`（compatible + mismatches 清单，CALIBRATION §6 纪律，业务错配永不抛）。raw 域反向门：数据来自 calibrated 而参考声明 RAW ⇒ 拒；参考 OSL_CALIBRATED 而数据为 raw ⇒ 拒（双向域错配）。
- **D5 逐 channel/axis/shape 校验矩阵（apply 前，全部结构化 fail-closed）**：① source.channels 元组与 `reference.channels` 精确全等（`==` 逐位 ChannelSpec frozen dataclass 比较；错序/缺道/多道/异 polarization → `CHANNEL_CONTRACT_MISMATCH`，context 给左右 channel_id 列表与首个差异位点）；② `reference.frequency_hz` 与 `source.frequencies_hz` `np.array_equal`（错轴/长度不符 → `AXIS_MISMATCH`）；③ `reference.mean_data` shape == `(len(channels), len(axis))` 且 ndim==2（→ `SHAPE_MISMATCH`）；④ dtype 严格 complex128（float/int 输入拒 `DTYPE_MISMATCH`——复数减法是复域契约，静默升位不允许）；⑤ mean_data/轴非有限值 → `INVALID_ARGUMENT`（仿 029 accept_sweep 与 storage `_encode_array` 守卫，报首个坏位 flat index）；⑥ source.data 末维 == 轴长（`SHAPE_MISMATCH`，core 构造已拦，stage 双检，仿 030）。
- **D6 数值应用（单实现，无第二套）**：输出 = `source.data - reference.mean_data`：sweep `(channel,frequency)` 直减；scan `(trace,channel,frequency)` 同一 2-D 广播（numpy 沿 trace 轴自动广播，等价于逐 trace 减同一向量——与"逐 sweep 批处理"bit 级一致，测试钉死）。**绝不沿 trace 轴做任何统计或差分**（这就是与 Flat/连续背景的数学分界，行为断言：所有 trace 减的是同一行）。输出经重建 core 模型防御拷贝为 never-writable 快照；容器保持输入类型（Sweep→Sweep、Scan→Scan），channels/per-trace metadata/frequency 轴原样保留（`metadata=source.metadata` 透传，idempotent no-op 路径已由 core 保证）。输入 source/reference 全程只读（bytes 前后全等断言）。
- **D7 重复背景检测（双保险，同 032 D6 模式）**：第一道门 = stage 输入域集合不含 BACKGROUND_APPLIED（history 末域已是 background_applied ⇒ 二次减除，`PROCESSING_DOMAIN_MISMATCH` 拒绝，消息点明"reducing twice would double-subtract the environment"）；第二道门 = core `ProcessingHistory` 同 stage_name 唯一性（探针测试证明 bump `stage_version` 仍拒 INVALID_ARGUMENT，030/032 先例）。record 层面 `background_reference_id` 必填由 core `_validate_references` 兜底（stage 不可能漏）。
- **D8 provenance/参数/时间**：record 经 core `ProcessingRecord` 直接构造（`_record_for` 不支持引用字段，032 先例澄清）：`parameters = {algorithm:"air_background_complex_subtract_v1", reference: {reference_id, domain, channels:[{channel_id,s_parameter}], axis_content_sha256, mean_content_sha256, trace_count}, calibration_profile_id: (继承传递或 None), [calibration_profile_content_sha256: calibrated 场景]}`。**calibrated 输入时 record 显式重携带 `calibration_profile_id`（= 末记录同值）**：PROCESSING §2 允许省略重复引用，但 033 输出仍处校准血统之上，显式继承使每条记录自足可审计（选择记此段；provenance continuity 因严格同值而通过）。`executed_utc` 显式优先（naive → `ensure_utc` 拒 NAIVE_DATETIME），缺省注入 clock（默认 SystemClock 一次读取，无 sleep）；`software_version` 取 `uav_gpr.__version__`。
- **D9 黄金/对拍口径**：数值对照物 = 本项目内手算复数减法：① 小维度合成场景（已知 mean_data 常数矩阵）逐元素 `expected = data - mean` bit 级 `array_equal`；② scan == 各 trace 单独 sweep 处理后 stack 一致性（广播语义钉死）；③ golden digest 字面量（固定 reference 场景的 `mean_content_sha256`/`axis_content_sha256` 十六进制串钉死 canonical 规则）；④ history 往返 `to_dict/from_dict` 全等 + `background_reference_id` 字段回填。参考源说明：033 无新迁移文件（空采聚合对象已在 029 迁移冻结并复审 PASS），登记于 t1 §2。

## 5. 文件改动（精确路径，无 glob）

| 路径 | 动作 | 内容 |
|---|---|---|
| `src/uav_gpr/processing/background_subtraction.py` | 新增 | D1–D9：stage + digest + provenance 判定接口 |
| `tests/contract/test_processing_background_subtraction.py` | 新增 | §6 测试矩阵，纯确定性、无 sleep、无硬件、无文件 IO |
| `docs/plans/2026-09-05-issue-033-bg-subtraction.md` | 新增 | 本文档 |
| `docs/issues/M06_CALIBRATION_PROCESSING.md` | 修改 | 仅 L229 状态行 `Planned → In progress → Review` |

## 6. 测试矩阵（失败测试优先；覆盖 captain 指派 8 项）

1. 协议合规：isinstance ProcessingStage；名称/版本/输入域 {raw,calibrated}/输出域 background_applied 精确；`StageResult.domain=BACKGROUND_APPLIED`；非法构造入参（非 reference/非 BackgroundReferenceId/None）TypeError。
2. **域错配（双向）**：raw 数据 + OSL_CALIBRATED 参考 → 拒；calibrated 数据 + RAW 参考 → 拒；空 history + OSL 参考 → 拒；background_applied/filtered/时域末域前驱 → 拒（乱序/二次）。
3. **多通道顺序**：ref channels 交换序 → CHANNEL_CONTRACT_MISMATCH（context 左右列表）；双通道各自减自己行的正确性（结果 != 交叉相减的期望，负向钉死）；缺道/多道/异 ChannelSpec(polarization) 拒。
4. **profile 错配**：calibrated 数据挂 PID_A、ref 声明 PID_B → 拒（字段级上下文）；PID 相同但 profile 内容 digest 不同（重解/篡改）→ 拒；ref OSL 域但 profile_id=None（手工构造绕过 029 会话校验）→ 拒；末记录缺 content_sha256（legacy）→ 严格拒；A==B 且 digest 匹配 → 通过；`check_safe_reuse`/`require_matching_calibration_provenance` 单元化验证。
5. **非有限/shape/轴/dtype**：mean_data NaN/Inf → INVALID_ARGUMENT（含 flat index）；轴含 NaN → 拒；mean_data shape 转置/行数≠通道数/列数≠轴长 → SHAPE_MISMATCH；float64 mean_data → DTYPE_MISMATCH；frequencies 平移/换长轴 → AXIS_MISMATCH；data 末维≠轴长 → SHAPE_MISMATCH。
6. **重复应用双保险**：stage 输出再进 → 输入域门拒；真实 osl→bg 两步链后再 bg → core 唯一性拒 INVALID_ARGUMENT；隔离探针 bump stage_version 仍拒；record `background_reference_id` 必填 core 兜底探针。
7. **数值/history 对拍**：手算黄金值 bit 级；scan 广播 == 逐 trace sweep 一致性；record 字段全检（input/output/software/stamp/parameters 结构有序）；to_dict/from_dict 往返全等；golden digest 字面量；parameters.profiles 继承字段（calibrated 场景 calibration_profile_id 与 parameters 一致）。
8. **输入不变性 + 与 Flat 区分 + 排除守卫**：source/reference 数组 bytes 前后全等、readonly（写入抛 ValueError）、输出为新对象 write-protected；trace=1 与 trace=N 每 trace 结果 == 同一 sweep 处理结果（无任何 trace 轴统计的数学指纹）；AST 级守卫：模块无 `uav_gpr.storage`/`uav_gpr.acquisition`/Qt import、源码无 `AirBackgroundSession(`/`moving_average`/fft 符号。

## 7. 执行日志

- （计划）落盘本文档 → M06 L229 Planned→In progress → 红灯测试 → 实现 → 绿灯 → 定向回归 → verify.py 全量（≥1249+新增）→ ruff/mypy(53)/import → M06 In progress→Review → diff-check changedPaths=inScope → 登记。
- 红灯：定向 pytest collection error（`ModuleNotFoundError: uav_gpr.processing.background_subtraction`）确认失败在先；首跑 12 failed/29 passed，逐项修复后全绿。
- 实现修正记录（相对 D 节的落地偏差，均为收紧而非放松）：
  1. **reference 结构校验分两级**：轴一维/非空/有限、mean_data dtype==complex128、mean_data 非有限值在**构造期**拒绝（与通道数无关的错误越早越好）；依赖通道数的 `mean_data.shape == (channels, axis)` 检查移到 apply 期 `_validate_contract`（reference.channels 与 source.channels 可能不同——错序场景先报 CHANNEL_CONTRACT_MISMATCH，shape 检查以 source 为准）。
  2. **OSL 域参考必须携带 live calibration**：stage 构造签名增 kwarg `current_calibration: OslCalibrationSet | None`；reference.domain==OSL_CALIBRATED 且未提供 ⇒ 构造即拒（`INVALID_ARGUMENT`, kind=`missing_current_calibration`）——否则 ID+digest 严格契约可能静默退化为 ID-only 匹配。calibrated apply 时 digest 重算的权威 = 该 live set 中 id 等于 reference profile_id 的 profile。
  3. **输出模型先于 history 突变重建**（apply 内顺序调整），任何 append 失败不留半成品 provenance。
  4. 重复应用双保险探针按 core 实际执行序重写：record 构造即校验 transition，故须在 patch `_ALLOWED_TRANSITIONS` 期间构造 chained record；`background_reference_id` 沿用同一 producer id（否则先触发 provenance continuity 而非唯一性）。
  5. Flat 区分测试改用同一 rng 流的 data[0] 同时喂 sweep 与 scan（原 per-trace rng 消耗序不同导致 bit 级不等，属测试夹具缺陷非实现缺陷）。
- 实现后数字（实测）：
  - 定向 `tests/contract/test_processing_background_subtraction.py`：**42 passed**。
  - 相关回归（background + osl + bandpass + processing time_domain + unit core_time_domain + calibration reference）：**204 passed**。
  - 全量 `tools/quality/verify.py`：**1291 passed / 4 deselected in 271.45s**（基线 1249 + 新增 42），ruff `All checks passed!`，mypy `Success: no issues found in 53 source files`（52 + 新模块），package import ok，`[quality] all gates passed` exit 0。
  - diff-check：见下节。
- diff-check（完成时）：工作树改动恰为 inScope 4 路径（M06 modified + background_subtraction.py / test_processing_background_subtraction.py / 本计划文档 3 个 untracked 新文件；t1 交付的基线报告为 t1 changedPath，不计入本任务）；`git diff --check` clean。

## 8. 验收映射（M06 L247-250 + 任务契约）

- 「raw reference 不能用于 calibrated 数据，反之亦然」→ 矩阵 2（双向域错配 + 域↔ReferenceDomain 映射 D1/D4）。
- 「多通道顺序和 profile 错配拒绝」→ 矩阵 3/4（精确序全等 + ID/digest 双匹配 strict provenance）。
- 「数值/history 对拍且与 Flat 明确区分」→ 矩阵 7/8（bit 级黄金值 + 广播一致性 + 往返全等 + 无 trace 统计指纹）。
- 「输入不可变、history 完整、重复应用拒绝、单道与 scan 广播」→ 矩阵 6/8 + D6/D7/D8。
- 排除项（Flat/连续背景/参考采集/UI/.rcbg）→ 矩阵 8 AST 守卫 + §3。
