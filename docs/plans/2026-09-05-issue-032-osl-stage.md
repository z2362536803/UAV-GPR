# ISSUE-032 实施计划：OSL 处理阶段与 calibrated provenance

日期：2026-09-05
执行器：AgentTeams `uav-gpr-issue-032-osl-stage` 成员 engineer（任务 t2）
基线件：[docs/reports/ISSUE_032_BASELINE_CONFIRMATION.md](../reports/ISSUE_032_BASELINE_CONFIRMATION.md)（main @ `b4f6dec`，工作树干净，门禁基线 1215 passed / 4 deselected + ruff + mypy(51) + import 全绿；依赖 027/030 Done 实测证据齐全）
目标 Issue：ISSUE-032（`docs/issues/M06_CALIBRATION_PROCESSING.md` L190–225）；约束文档：`AGENTS.md` §3/§9/§10、`docs/CALIBRATION.md` §5/§6/§7、`docs/PROCESSING.md` §1/§2、t1 基线确认单 §3。

## 1. 目标与用户价值

在 `processing` 层交付**独立的 OslCalibrationStage**：把 `frequency_raw` 严格转换为 `frequency_calibrated`（OSL 后、空采前语义钉死）新对象，多通道分别应用其绑定 profile，逐通道校验 S 参数/频率轴/profile ID+内容摘要，追加完整可序列化 history/provenance（含有序逐通道引用与组合 digest），拒绝二次校准，并提供 **safe reuse 判定接口**（仅接受严格相同 profile provenance）。它是 ISSUE-033（空采背景必须匹配 calibrated 域与 profile）的直接前置。

## 2. 范围（任务契约 inScope = 4 个精确路径，changedPaths 与此逐一相等）

1. `src/uav_gpr/processing/osl_calibration.py`（唯一实现模块：`OslProfileProvenance` + `osl_profile_digest` + `osl_set_digest` + `OslCalibrationStage` + `SafeReuseResult` + `check_safe_reuse` + `osl_provenance_of`——stage 契约复用 030 的 `ProcessingStage`/`StageResult`/`_record_for`/`_input_domain_of`）
2. `tests/contract/test_processing_osl_calibration.py`（唯一测试文件：契约 + 数值对拍 + 拒绝矩阵 + safe reuse 矩阵）
3. `docs/plans/2026-09-05-issue-032-osl-stage.md`（本计划文档，含 D 节决策记录与执行日志）
4. `docs/issues/M06_CALIBRATION_PROCESSING.md`（仅 ISSUE-032 状态行 L192：`Planned → In progress → Review`，勿动其他条目）

注：t1 基线单 §3.5 曾建议含 `processing/__init__.py` 的布局，与任务契约不符，已被 captain 纠正作废——实际执行以本节 4 路径为准，不创建/修改任何额外文件。

## 3. 明确排除项（M06 L205 + 提示词 + 任务契约）

不采集 OSL（不调用 `build_osl_calibration`，模块内无求解入口）、不保存文件（不触碰 `.rcal/.rcbg` writer/reader，storage 零 import）、不应用空采或 IFFT（不含背景减除/ifft 代码；链式测试证明 osl→ifft 是两个独立 stage 两条记录）、不做 UI；不改 `core/**`（`ProcessingRecord`/`ProcessingHistory`/`DataDomain`/`ErrorCode` 只读消费，不新增错误码——复用 `PROCESSING_DOMAIN_MISMATCH`/`CHANNEL_CONTRACT_MISMATCH`/`AXIS_MISMATCH`/`SHAPE_MISMATCH`/`INVALID_ARGUMENT`）、不改 `calibration/osl.py`（profile 消费面只读）、不改 `processing/bandpass.py` 与 `processing/time_domain.py` 公共语义（import 复用）、不改 `processing/__init__.py`（沿用 030/031 先例，测试直接 import `uav_gpr.processing.osl_calibration`）；不修改 `docs/reports/**`、`docs/CALIBRATION.md`、`docs/PROCESSING.md`、`docs/adr/**`、`tools/**` 与参考仓库（只读）；raw 绝不修改；不 commit/push/merge；不进入 ISSUE-033。

## 4. 设计决策（D1–D9）

- **D1 stage 契约复用**：`OslCalibrationStage` 结构化实现 030 冻结的 `ProcessingStage` Protocol（`stage_name="osl_calibration"`、`stage_version="1.0"`、`input_domain=frozenset{frequency_raw}`、`output_domain=frequency_calibrated`），import 尾部 `assert isinstance(...)` 静态防漂移。输入域**仅** raw：CALIBRATION §5 把 `frequency_calibrated` 钉为"OSL 后、空采前"唯一产物，任何非 raw 前驱（含已 calibrated、filtered 后回流）都意味着二次校准或乱序，全部 fail-closed。history 追加一律走 `ProcessingHistory.append`（链式校验 + 同 history stage_name 唯一由 core fail-closed 强制，bump version 不能绕过）。返回值复用 030 的 `StageResult`（source 为频域容器，类型兼容）。
- **D2 逐通道有序引用 + 组合 digest 的 provenance 表达（captain 裁决项）**：core `ProcessingRecord` 只有单个 `calibration_profile_id` 字段且公共语义不得改动。双通道场景的表达方式：record parameters 携带按 scan 通道序排列的 `"profiles": [{channel_id, s_parameter, profile_id, content_sha256}, ...]`（每通道一个 `OslProfileProvenance`），并携带 `"set_content_sha256"`（对有序 `[{channel_id, profile_id, content_sha256}]` 序列做 canonical JSON SHA-256 的组合摘要，覆盖"通道↔profile 绑定顺序"本身）；record 的 `calibration_profile_id` 字段取**有序集合的首 profile id**（该选择显式记录在 parameters 键 `"profile_id_field_semantics": "first_profile_of_ordered_set"`，避免误读为"整条记录只绑一个 profile"）。safe reuse 判定的权威依据是逐通道 `content_sha256` + 绑定序的 `set_content_sha256`，而非单字段。
- **D3 profile 内容摘要（digest）**：每个 profile 的 `content_sha256` = SHA-256(canonical JSON of 本地转写的 stored payload)。payload 字段结构与 `storage/calibration_files.StoredOslProfile.to_payload()` 逐项对齐（profile_id/channel/s_parameter/frequency_hz/standards(open,short,load: measured_mean+actual+capture_count)/error_terms(directivity+reflection_tracking+source_match)/quality），canonical dump 规则与 storage envelope 完全一致（`json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=True, allow_nan=False)` UTF-8）。数组编码 `{dtype,shape,re[,im]}` 本地复刻。**分层裁决（D3a）**：processing 不 import storage（AGENTS §9 方向纪律），故 payload 构造在本模块本地转写，并用一份 golden digest 字面量测试钉死格式（漂移即红）。浮点经 `float()` 入 JSON，确定性由 Python repr 往返保证。
- **D4 逐通道校验矩阵（apply 前，全部结构化 fail-closed）**：① source.channels 元组与 `set.channels` 精确全等（错序/缺道/多道/异 ChannelSpec → `CHANNEL_CONTRACT_MISMATCH`，context 给首个差异位点）；② 每 profile `frequency_hz` 与 source 轴 `np.array_equal`（错轴/长度不符 → `AXIS_MISMATCH`）；③ S 参数反射性（S11/S22）由 027 构造保证，本层再断言 `profile.s_parameter == channel.s_parameter`（防御性冗余）；④ data 末维 = 轴长（`SHAPE_MISMATCH`）；⑤ 非有限值透传 `set.apply`/`profile.correct` 的既有拒绝。
- **D5 数值应用**：sweep `(channel,frequency)` 直接 `set.apply(source.data, source.channels)`（行序严格断言）；scan `(trace,channel,frequency)` 沿 trace 轴逐道调用同一 `apply` 后 stack（数学与 sweep 完全同一实现，无第二套），输出经 core 模型构造防御拷贝为 never-writable 快照；输出容器保持输入类型（FrequencySweep→FrequencySweep、FrequencyScan→FrequencyScan），channels/per-trace metadata/frequency 轴原样保留。
- **D6 重复 OSL 检测（双保险）**：第一道门 = stage 自身输入域检查（history 末域 ≠ raw 即拒，已校准数据不可能通过）；第二道门 = core `ProcessingHistory` 同 stage_name 唯一性（探针测试证明 bump `stage_version` 也绕不过）。错误消息 context 均带 stage/domain 字段级证据。
- **D7 safe reuse 判定接口**：`check_safe_reuse(history, calibration: OslCalibrationSet) -> SafeReuseResult`（纯函数，不抛业务异常，返回 `compatible: bool` + `mismatches: tuple[str, ...]` 字段级差异列表，口径仿 CALIBRATION §6"列出字段级差异"）+ 便捷异常版 `require_safe_reuse(...) -> None`（不兼容时 `DomainError(PROCESSING_DOMAIN_MISMATCH)`，context 带差异清单）。判定链：① history 中必须存在输出 `frequency_calibrated` 的记录且其为**末条**（否则 incompatible：未校准/校准非当前域）；② 该记录 parameters 的 profiles 数组与请求 set 逐位置比较 `{channel_id, s_parameter, profile_id, content_sha256}` 全等 + `set_content_sha256` 相等 + `profile_id_field_semantics` 一致——**ID 相同但内容摘要不同（重解/篡改）也拒绝**（严格相同 provenance）；记录缺 digest 字段视为不严格 → 拒绝（无 anchor 宽容）。
- **D8 时间与版本**：`executed_utc` 显式优先（naive → `ensure_utc` 拒 `NAIVE_DATETIME`），缺省注入 clock（默认 SystemClock，一次读取，无 sleep）；`software_version` 经 `_record_for` 取 `uav_gpr.__version__`；record `calibration_profile_id` = D2 首 profile id。
- **D9 黄金/对拍口径**：数值对拍对照物 = osl.py 直接应用（`set.apply` 逐道 / `profile.correct` 手工 forward-model 合成 DUT，027 已对拍参考源）：① 双通道合成场景下 stage 输出 == 逐道 `set.apply` 期望 stack（bit 级 `array_equal`）；② 理想 OSL 恢复已知 Γ（atol 1e-9，复用 027 `_forward` 公式本地转写）；③ golden digest 字面量（固定 profile 场景的 content_sha256/set_content_sha256 十六进制串钉死，防 canonical 规则漂移）。参考源说明：032 无新迁移文件（OSL 数学已在 027 迁移对拍），登记于 t1 §2。

## 5. 文件改动（精确路径，无 glob）

| 路径 | 动作 | 内容 |
|---|---|---|
| `src/uav_gpr/processing/osl_calibration.py` | 新增 | D1–D9：provenance/digest + stage + safe reuse |
| `tests/contract/test_processing_osl_calibration.py` | 新增 | §6 测试矩阵，纯确定性、无 sleep、无硬件 |
| `docs/plans/2026-09-05-issue-032-osl-stage.md` | 新增 | 本文档 |
| `docs/issues/M06_CALIBRATION_PROCESSING.md` | 修改 | 仅 L192 状态行 `Planned → In progress → Review` |

## 6. 测试矩阵（失败测试优先）

1. 协议合规：isinstance ProcessingStage；名称/版本/输入输出域精确；`StageResult.domain=frequency_calibrated`。
2. 双通道正确应用：S11+S22 各用其绑定 profile，输出 == `set.apply` 逐道 stack（bit 级）；scan == 各 sweep 批处理一致性；shape/dtype/channels/metadata 保持。
3. 理想恢复对拍：forward-model 合成 DUT → stage 输出 ≈ 真值 Γ（atol 1e-9）。
4. 拒绝矩阵：错序通道 / 缺通道 / 多余通道 / 异 polarization ChannelSpec（CHANNEL_CONTRACT_MISMATCH）；错轴（平移/换长轴，AXIS_MISMATCH）；data 末维≠轴长（SHAPE_MISMATCH，core 构造已拦，stage 层双检）；非有限值输入（027 守卫透传）；非 FrequencySweep/Scan 输入（TypeError）；history 非 ProcessingHistory（TypeError）。
5. 二次校准：真实路径（stage 输出再进 → 输入域门拒 PROCESSING_DOMAIN_MISMATCH）；已有 calibrated 末域 history 再 apply → 拒；core 唯一性隔离探针（临时放宽链限制证明同 stage_name 重复仍拒 INVALID_ARGUMENT，bump version 不绕过，030/031 先例）。
6. history/provenance：单条追加、旧 history 不动；record 字段（input=raw/output=calibrated/profile_id=首 pid/software/stamp）；parameters.profiles 有序逐通道 {channel_id,s_parameter,profile_id,content_sha256} + set_content_sha256 + profile_id_field_semantics；to_dict/from_dict 往返全等；golden digest 字面量钉死。
7. safe reuse：相同 provenance → compatible=True 零差异；不同 ID → False+差异；同 ID 不同内容（重解）→ False；通道错序 → False；缺 calibrated 记录/非末条 → False；旧格式缺 digest → False（严格）；require_safe_reuse raise 路径带上下文。
8. raw 不变：输入数组 writeable=False + 应用前后 bytes 全等；输出 writeable=False 且写入抛 ValueError；输入对象 id 不变、输出为新对象。
9. 排除守卫：模块源码无 `build_osl_calibration(` 调用、无 ifft/background/UI/depth/storage import 符号（AST/文本级）。

## 7. 执行日志

- （计划）落盘本文档 → M06 L192 Planned→In progress → 红灯测试 → 实现 → 绿灯 → 门禁 → M06 In progress→Review → 登记。
- 红灯：定向 pytest collection error（`ModuleNotFoundError: uav_gpr.processing.osl_calibration`）确认失败在先；首跑 19 failed/9 passed 后逐项修复至全绿。
- 实现修正记录：scan 路径实测 `OslCalibrationSet.apply` 仅接受 2-D `(channel,frequency)`，遂按 D5 原案沿 trace 轴逐道调用同一权威（无第二套数学）；provenance record 直接经 core `ProcessingRecord` 构造以携带 `calibration_profile_id`（`_record_for` 不支持引用字段，模块 docstring 已澄清）；mypy strict 下 `list[JsonValue]` 不变性显式中间标注；重复 stage 探针改用临时放宽 `_ALLOWED_TRANSITIONS`（finally 恢复）隔离唯一性规则。
- 实现后数字（实测）：
  - 定向 `tests/contract/test_processing_osl_calibration.py`：**34 passed**。
  - 相关回归（test_processing_osl_calibration + test_processing_bandpass + test_processing_time_domain + test_calibration_osl + unit/test_core_time_domain）：**178 passed**。
  - 全量 `tools/quality/verify.py`：**1249 passed / 4 deselected**（基线 1215 + 新增 34），ruff `All checks passed!`，mypy `Success: no issues found in 52 source files`（51 + 新模块），package import ok，`[quality] all gates passed` exit 0。
  - diff-check：`git diff --check` clean；工作树改动恰为 inScope 4 路径（M06 modified + 3 untracked 新文件；t1 交付的基线报告为 t1 changedPath，不计入本任务）。

## 8. 验收映射

- 「raw→osl_calibrated stage、profile/channel/axis 兼容检查」→ 测试矩阵 1/2/4。
- 「多通道分别应用对应 profile」→ 矩阵 2（bit 级对拍 `set.apply`）。
- 「history/provenance/profile digest；重复 OSL 检测」→ 矩阵 5/6（有序逐通道 digest + 组合摘要 + 双保险拒绝）。
- 「safe reuse 判定接口（严格相同 provenance）」→ 矩阵 7。
- 「raw 永不修改；calibrated = OSL 后空采前」→ 矩阵 8 + D1 输入域钉死 raw。
- 排除项（不采/不存/不背景/不 IFFT/不 UI）→ 矩阵 9 + §3。
