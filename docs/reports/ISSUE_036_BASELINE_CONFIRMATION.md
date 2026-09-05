# ISSUE-036 开工基线确认单（自动化轮 · engineer）

## 1. 目标 Issue 与依据

- **Issue**：ISSUE-036「完整处理编排、revision 与安全回放」（`docs/issues/M06_CALIBRATION_PROCESSING.md` L338-373）
- **状态行核查**：M06 L340 ISSUE-036 状态 = `Planned`；映射 FR-011、012、016；直接依赖声明 = ISSUE-011、018、029～035。
- **工作区根替换声明**：Issue 提示词中 `E:\wurenjiruanjian` 一律替换为当前工作区根 `/mnt/d/博士任务/无人机软件/UAV-GPR`（Windows：`D:\博士任务\无人机软件\UAV-GPR`）。
- **流程依据**：`AGENTS.md`、`docs/issues/README.md` §1（依赖是实际开工门禁、编号为主执行顺序）、§3 通用执行协议（本会话只执行 ISSUE-036，不 commit/push）、`docs/ISSUE_REVIEW_STANDARD.md`（t1 基线 → t2 实现 → t3 独立复审）。
- **契约文档**：已读 `docs/PROCESSING.md` §1/§2/§7/§9（阶段原则、推荐流水线与域链、实时显示与重处理/revision、验证），`docs/CALIBRATION.md` §1/§5/§6/§7（四概念边界、固定基础顺序、兼容性三态、provenance），`docs/DATA_FORMAT.md` §2/§3/§3.1/§8（`.rcscan` v2 逻辑结构、增量写入、reader 冻结契约、`.rcal/.rcbg`）。

## 2. 只读核查证据（全部实测）

| 项 | 结果 |
|---|---|
| HEAD / 分支 | `main @ 487f9ad`（`docs(issues): mark ISSUE-035 Done after automated merge`）；核查前 `git status --porcelain` 为空（工作树干净，除本单外无输出）；`git diff --check` 干净 |
| 远端同步 | `git rev-list --left-right --count origin/main...HEAD` = `0 0` —— **本地 main 与 origin/main 完全同步**，029-035 合并链推送均已闭合（与 035 基线单当时的 ahead 3 不同，本轮无遗留） |
| ISSUE-011 Done 证据 | M02 L118 状态 = `Done（2026-08-30 独立复审 PASS WITH CONDITIONS 后经项目负责人授权合并，见 docs/reports/ISSUE_011_REVIEW_REPORT.md）`；合并提交 `57c4966`（`Merge feat/issue-011: ... read-only rcscan reader, strict validation and logical ordering`）在 HEAD 历史；tracked `src/uav_gpr/storage/rcscan_reader.py` + `tests/contract/test_rcscan_reader.py`（34 个 test 定义）✓；Done 标记提交 `db95817` |
| ISSUE-018 Done 证据 | M03 L118 状态 = `Done（2026-08-31 独立复审 PASS WITH CONDITIONS 后经授权合并，见 ISSUE_018_REVIEW_REPORT.md）`；合并提交 `29af4f5` + 标记 `c0cd067` 在 HEAD 历史；tracked `src/uav_gpr/acquisition/replay.py`（`FileReplayBackend`/`ReplayConfig`/`ReplayMode`）+ `tests/contract/test_acquisition_replay.py`（39 个 test 定义）✓ |
| ISSUE-029 Done 证据 | M06 L81 状态 = `Done（2026-09-05 自动化轮：t3 复审 PASS WITH CONDITIONS + F1(P2) 修复闭合（t4，captain 接管）并经定向复验 F1 CLOSED 后自动合并；P3 F2-F5 挂账）`；合并提交 `fb758fe` + 标记 `b4d4b0f`；tracked `src/uav_gpr/storage/calibration_files.py` + `tests/contract/test_calibration_files.py`（32 个 test 定义）✓ |
| ISSUE-030 Done 证据 | M06 L118 = `Done（t3 复审 VERDICT=PASS（无 P0/P1/P2）后自动合并；4 项 P3 挂账）`；合并提交 `89fd9bb` + 标记 `2c0abdc`；tracked `src/uav_gpr/processing/bandpass.py`（`ProcessingStage` Protocol / `StageResult` / `_input_domain_of` / `_record_for` / `BandpassStage`）+ `test_processing_bandpass.py`（22）✓ |
| ISSUE-031 Done 证据 | M06 L155 = `Done（VERDICT=PASS，2 项 P3 挂账）`；合并提交 `9afdfb2` + 标记 `2c0abdc`；tracked `src/uav_gpr/processing/time_domain.py`（`FrequencyToTimeStage` / `TimeDomainStageResult` / `DisplayCropConfig` / `DisplayTimeWindowView`）+ `test_processing_time_domain.py`（35）✓ |
| ISSUE-032 Done 证据 | M06 L192 = `Done（VERDICT=PASS，4 项 P3 挂账）`；合并提交 `4e5349e` + 标记 `4cec913`；tracked `src/uav_gpr/processing/osl_calibration.py`（`OslCalibrationStage` / `OslProfileProvenance` / `SafeReuseResult` / `check_safe_reuse` / `require_safe_reuse`）+ `test_processing_osl_calibration.py`（34）✓ |
| ISSUE-033 Done 证据 | M06 L229 = `Done（VERDICT=PASS，4 项 P3 挂账）`；合并提交 `21fb64f` + 标记 `b4f6dec`；tracked `src/uav_gpr/processing/background_subtraction.py`（`AirBackgroundSubtractionStage` + `SafeReuseResult`/`check_safe_reuse`/`require_matching_calibration_provenance`）+ `test_processing_background_subtraction.py`（38）✓ |
| ISSUE-034 Done 证据 | M06 L266 = `Done（VERDICT=PASS，2 项 P3 挂账）`；合并提交 `9f6caff` + 标记 `8accb76`；tracked `src/uav_gpr/processing/dewow.py`（`DewowStage`）+ `test_processing_dewow.py`（40）✓ |
| ISSUE-035 Done 证据 | M06 L303 = `Done（VERDICT=PASS，1 项 P3 挂账）`；合并提交 `932b08d` + 标记 `487f9ad`；tracked `src/uav_gpr/processing/flat_reflection.py`（`FlatReflectionFilterStage`，stage_name token `flat_reflection_filter`）+ `test_processing_flat_reflection.py`（38）✓ |
| 可执行性（application 层无编排实现） | `src/uav_gpr/application/` 仅有三个 `__init__.py`（`application`、`application/air`、`application/ground` 命名空间占位，**零实现文件**）；全仓 grep `orchestrat|pipeline|run_processing|process_mission|ProcessingProfile` 在 `src/uav_gpr/` 下仅命中既有模块的 docstring 叙述（acquisition/controller、calibration/reference、processing/*），无任何编排类/函数；产品代码中 `revision` 仅有两类命中：core/processing 的**拒绝消息与文档字符串**（"re-processing requires a new history/revision"，见 `core/time_domain.py` L582、`dewow.py` L403、`flat_reflection.py` L403）与 LibreVNA 设备身份字段 `hardware_revision`（无关），**没有 processing revision 计数器 / 过期丢弃机制**（仅 `docs/PROCESSING.md` L12/L65、`docs/DATA_MODEL.md` L172、`docs/PERFORMANCE.md` L55/L72、`docs/UI.md` L73/L101 等文档口径）——**ISSUE-036 为唯一待建能力，无重复实现风险** |
| 下一可执行 Issue | M06 自 L340 起首个 `状态：Planned` 即 ISSUE-036（其后 M07 ISSUE-037 亦 Planned，但 036 属 M06 收尾且依赖已全部 Done）→ **ISSUE-036 为下一可执行 Issue**，完成即 M06 里程碑（027–036）闭合 |

### 关键本项目契约文件哈希（SHA-256 前 16 位，供 t3 交叉核对）

```text
f707839674ceb5e1  src/uav_gpr/processing/bandpass.py            (ProcessingStage/StageResult/_record_for)
b7da55717148645b  src/uav_gpr/processing/time_domain.py         (FrequencyToTimeStage/TimeDomainStageResult/DisplayCrop)
30224c9a0091c02b  src/uav_gpr/processing/osl_calibration.py     (OslCalibrationStage + check_safe_reuse)
a96d59f63289a8c8  src/uav_gpr/processing/background_subtraction.py
7efaa728ab3f96ad  src/uav_gpr/processing/dewow.py
01b9d9b7f2c5d321  src/uav_gpr/processing/flat_reflection.py
34f61e720305d301  src/uav_gpr/processing/__init__.py            (仅 docstring，无导出)
de34545e4ce1a36a  src/uav_gpr/storage/rcscan_v2.py              (冻结 schema 契约面)
9e109a0e4bbd7267  src/uav_gpr/storage/rcscan_reader.py          (ISSUE-011 只读 reader)
9d730ec7a0f7c223  docs/PROCESSING.md
5ca0dc5fdd2ccedc  docs/CALIBRATION.md
79b73d17bf41e3ef  docs/DATA_FORMAT.md
84ead0f4e7222767  docs/issues/M06_CALIBRATION_PROCESSING.md
```

（参考项目说明：ISSUE-036 是 application 层纯编排，**不迁移任何参考项目代码**，因此本节无参考源哈希对拍要求；`E:\钢筋仪软件开发` 本机不可达，沿用 027-035 先例仅使用本地只读副本，本单未读取。）

## 3. 契约要点（对 t2 实现有约束）

### 3.1 PROCESSING.md：唯一流水线与域链（强约束）

- §2 推荐流水线（逐字为序）：`frequency_raw -> OSL(optional) -> frequency_calibrated -> air background(optional) -> frequency_background_applied -> bandpass(optional) -> frequency_filtered -> IFFT -> time_base -> dewow(optional) -> time_processed -> flat reflection filter(optional) -> time_processed`；`time zero` 与 `continuous background` 标注 future（**本 Issue 排除**）。
- 域转换规则由 core `_ALLOWED_TRANSITIONS`（`core/time_domain.py` L94-124）落地并 fail-closed：raw 永不出现在输出集；`frequency_filtered` 只由带通产生；不允许 time→frequency、不允许 `time_processed → time_base`、不允许跳过 `time_base`。
- history **第一项输入域必须是 `frequency_raw`**（`_START_DOMAINS`，L124）；从派生频域快照开始需未来独立 provenance anchor，**当前不允许** → 这直接决定"safe replay reuse"入口的语义只能是"同一 raw + 相同 profile/provenance ⇒ 复用已算好的 calibrated 快照，不二次 OSL"，而**不是**以 calibrated 作为新 history 起点。
- §1：每阶段输入不可变、输出新对象、完整记录参数/版本/输入输出域/历史；同一 history 内稳定 `stage_name` 只得应用一次（改 `stage_version` 不能绕过）；重新处理必须开始新的 history/revision；引用与域兼容（输出 `frequency_calibrated` 必带校准引用、`frequency_background_applied` 必带空采引用、时域阶段不得携带频域引用）；`frequency_raw` 只允许作为处理输入；**处理失败不得破坏已保存的 `frequency_raw`**。
- §7：参数变更产生新的 processing revision，过期 worker 结果按 revision 丢弃；实时预览与任务后重处理复用同一算法（不复制两套数学）；UI 显示增益/色图/动态范围不属于处理历史。
- §2 尾：无时域后处理时 `time_processed` 可以不存在，消费方使用 `time_base`；**不得把处理后数组写回 `time_base`** → 对应验收"time_base 总是 IFFT 基础，time_processed 仅在时域 stage 开启时存在"。

### 3.2 CALIBRATION.md：概念边界与固定顺序

- §1 四者目的/输入域/参数不同，**不得合并成一个"校正"按钮或处理函数**：OSL 校准（VNA 一端口系统误差）、空采背景（复数频域参考减除）、Flat Reflection（沿测线方向时域局部背景）、零时校正。
- §5 固定基础顺序与 Issue 范围一致，并明确三条禁止：不得覆盖 raw；**不得对已使用相同 profile 校准的数据再次校准**（⇒"二次 OSL 拒绝"测试点，落点在 `OslCalibrationStage` 已有守卫 + 编排层不得重复调用）；不得把 raw 域背景应用到校准域（⇒ `AirBackgroundReference.domain` 必须 `osl_calibrated` 当输入已校准，`check_safe_reuse` 已实现该判定）。
- §6 加载参考文件 ≠ 可用：`compatible` / `compatible_with_warnings` / `incompatible` 三态（ISSUE-029 `storage/calibration_files.py` 已交付），编排层必须以兼容性结论决定是否启用。
- §7 应用到任务后 `.rcscan` 必须保存引用 ID、内容摘要、原文件信息与**实际执行阶段记录**，使离开原参考文件后仍可审计（`OslProfileProvenance` / record parameters `set_content_sha256` 等已在 032/033 落盘）。

### 3.3 DATA_FORMAT.md：rcscan 结构与受控 storage 接口面

- §2 v2 逻辑结构中派生数据槽位齐备且**全部声明为 optional**：`/axes/time_base_s`、`/axes/time_processed_s`、`/frequency/calibrated [trace,channel,frequency]`、`/time_base/data [trace,channel,time]` + `/time_base/history_json`、`/time_processed/data` + `/time_processed/history_json`（`rcscan_v2.dataset_contracts` L337-375，`optional=True`）。缺失合法、存在则与其它数据集同样严格校验（§3.1 reader 契约）。
- **schema 缺口（如实登记，t2 必须先决策）**：v2 冻结契约**未声明** `/frequency/history_json`（`rcscan_v2.py` 中 `history` 仅出现于 `time_base`/`time_processed` 两处；DATA_FORMAT §2 结构树的 `history_json` 也只挂在 `time_base/` 与 `time_processed/` 下），而 v1 侧存在 `/frequency/history_json`（`rcscan_v1.py` L844/L896）。⇒ 若 t2 要把"calibrated snapshot + 其处理历史"持久化到 ground `.rcscan`，可选路径只有两条：(a) 只用已声明的 optional 组承载（频域派生 history 序列化进 `/time_base/history_json` 同一条记录的 provenance，或作为 mission attrs / 随 time_base 历史一起可重建）；(b) 扩展 v2 schema。**AGENTS.md §11/§10 与 README §3.7 明确：新增 schema 前须说明必要性、架构决定缺失时先写 Proposed ADR 并等待批准，而不是扩大 Issue** → t2 默认走 (a)（不改 schema、不改 `rcscan_v2.py` 任何字节），并在计划文档显式记录该缺口与本 Issue 的取舍。
- **受控 storage 接口面现状（t2 落点）**：`storage/rcscan_v2.py` 提供一次性 skeleton creator `create_rcscan_v2(...)`（含全部 dataset contract + `probe_rcscan_v2`）；`storage/incremental_writer.py` 的 `RcScanIncrementalWriter` 只有 raw 路径写 API（`create` / `append_trace(TraceAppendRequest)` / `flush` / `close` / `abort` + `classify_trace` 冲突判定），**没有任何写派生组的公开方法**；`storage/rcscan_reader.py` 只读（`"r"` 模式，读取前后文件字节不变，暴露 `iter_physical`/`iter_logical`/`ReadTrace.frequency_raw`/`ValidationReport`）。ISSUE-012 恢复流程更进一步把 processed 组存在视为不可恢复（`partial_recovery.py` L88-89 列入拒绝项）。⇒ ISSUE-036 要求的"通过受控 storage 接口附加派生数据/history"必须在**新建的 application 层受控接口**内实现（例如持有已 finalize 文件或专用 derived-writer 门面，遵守 §3 增量写入次序：先写数据 flush、后更新 checkpoint 再 flush；HDF5 无跨数据集事务），且**绝不修改 `frequency/raw` 与 trace-major 必需列的任何字节**（对应验收"所有组合保持 raw"+"raw byte 不变"）。t2 若要改 `storage/` 任一文件必须先经计划文档说明必要性（inScope 4 路径限制意味着大概率不可改，见 §3.5）。
- §5/§3.1：逐道 `raw_trace_sha256` 是空地一致性权威；GNSS 不入 raw hash。回放复用判定必须以 raw 身份 + config/profile digest 为准。

### 3.4 可直接复用的既有资产（t2 不新建平行类型）

- Stage 协议（030 冻结）：`ProcessingStage`（`stage_name` / `stage_version` / `input_domain` / `output_domain` / `apply(source, *, history, executed_utc=None, clock=None)`）、`StageResult{source, history, domain}`、`_input_domain_of(history)`、`_record_for(...)`；时域返回 `TimeDomainStageResult`（031）。
- 五个具体 stage 全在主干：`OslCalibrationStage`(032)、`AirBackgroundSubtractionStage`(033)、`BandpassStage`(030)、`FrequencyToTimeStage`(031)、`DewowStage`(034)、`FlatReflectionFilterStage`(035)。
- safe-reuse 判定现成：`osl_calibration.check_safe_reuse/require_safe_reuse`（严格逐项 `channel_id/s_parameter/profile_id/content_sha256` + `set_content_sha256` + profile-id-field semantics，legacy 无 provenance 一律拒绝）；`background_subtraction.check_safe_reuse/require_matching_calibration_provenance`（要求 history 末域 = `frequency_calibrated`、reference.domain = `osl_calibrated`、绑定 profile id + 活体 digest 复核）。**编排层的"错 profile 拒绝"应委托这两处权威，不重写判定。**
- core 模型：`ProcessingHistory.append/to_dict/from_dict`、`ProcessingRecord`（JSON-safe）、`DataDomain`、`TimeDomainScan(kind/history 校验)`、`FrequencySweep/FrequencyScan`（`FrequencyScan.append(sweep)` 可用于增量累积）、`ErrorCode`（含 `PROCESSING_DOMAIN_MISMATCH`、`ID_CONFLICT`）。
- 时间/时钟注入：`core/timeutil.Clock`、`ensure_utc`（各 stage apply 均支持 `executed_utc` 优先、否则 clock，便于确定性测试）。

### 3.5 团队计划硬约束（t2 inScope 表述以 t2 任务契约原文为准）

本单不约定文件集；t2 的 **inScope 精确 4 路径**（含计划文档，预期形如 `docs/plans/2026-09-05-issue-036-processing-orchestration.md`）以 t2 任务契约原文为准，`changedPaths` 与之逐一相等。据此推论并提醒 t2：
- 4 路径预算紧张（编排模块 + 契约测试 + 计划文档 + M06 状态行），因此 **`src/uav_gpr/storage/*`、`core/*`、`processing/*` 既有文件、`processing/__init__.py` 均不在可改集合内**（035 先例同样不含 `__init__.py`）→ 编排实现应为**新增 application 层模块**，派生数据落盘经由该模块内部的受控 HDF5 写路径（只写 §3.3 已声明的 optional 组），并以"raw 字节不变"断言钉死边界。
- 若 t2 认为必须扩展 schema 或改 core/storage，属于架构决定：按 AGENTS.md 停止并报告，先写 Proposed ADR，不在本 Issue 内形成事实。

## 4. 门禁基线（verify.py 复跑，实测）

- 命令：`.venv/Scripts/python.exe tools/quality/verify.py`（gate 顺序：pytest `-m "not hardware and not slow"` → `ruff check .` → `mypy src` → package import）。输出重定向至仓库外 `/tmp/verify_036_t1.log`，仓库内零遗留产物。
- pytest (non-hardware)：**1396 passed / 4 deselected in 271.83s** ✓（4 deselected = 硬件哨兵 + opt-in LibreVNA），与团队计划口径 1396 逐字一致
- ruff：`All checks passed!` ✓
- mypy：`Success: no issues found in 55 source files` ✓（035 基线为 54，flat_reflection.py 入主干后 54→55，与计划口径一致）
- package import ok；`[quality] all gates passed`，exit 0
- 单独复现（并行旁证）：`ruff check src tests tools` → All checks passed；`mypy src` → 55 files clean；import 门 → `package import ok`
- 核查前后 `git status --porcelain` 均为空（本单为唯一新增未跟踪文件）；`git diff --check` 干净

## 5. 实施计划摘要（供 t2）

**范围（唯一 application 层处理编排）**：新增 `src/uav_gpr/application/ground/<编排模块>`（t2 契约锁定名）实现单一处理编排入口，串接既有 stage，顺序严格按 §3.1：可选 OSL → **保存 calibrated snapshot** → 可选 air background → 可选 bandpass → IFFT/`time_base` → 可选 Dewow → 可选 Flat/`time_processed`。要点：
1. **配置模型**：一个不可变 ProcessingProfile/请求对象描述各 stage 开关与其 stage 构造参数（OSL `OslCalibrationSet`、air `AirBackgroundReference`、bandpass edges、IFFT 参数与可选 `DisplayCropConfig`、dewow、flat window_traces）；profile 必须有稳定 JSON-safe 序列化 + 内容摘要（用于 reuse 判定与 revision 身份）。
2. **两条严格入口**：
   - `fresh raw`：输入必须是 raw 域（history **必须为空**）；非空 history 传入 ⇒ `PROCESSING_DOMAIN_MISMATCH`/`INVALID_ARGUMENT` fail-closed（对应验收"错误/非空 raw history 拒绝"）。
   - `safe replay reuse`：给定已存在的 calibrated 快照 + 其 provenance，只有当 `check_safe_reuse`（OSL 侧逐项 profile id + content digest + set digest）与（若启用背景）`background_subtraction.check_safe_reuse` 全部 compatible 才复用，**绝不二次执行 OSL/背景**；任何字段差异 ⇒ 拒绝并给出 field-level mismatch 清单，退回 fresh 或报错。注意 §3.1：reuse 不等于以 calibrated 作为新 history 起点——复用产出的 history 必须仍从 raw 起步或以等价可审计方式重建（t2 决策并写入计划文档）。
3. **revision / cancel**：每次参数变更产生单调递增 processing revision；worker 结果携带其 revision，过期结果按 revision 丢弃（可观测计数/回调），取消路径幂等、无 sleep/poll；**过期或被取消只影响显示/派生结果，raw 存储不受影响**（断言：全程 raw 数据集字节与 trace-major 必需列 SHA256 前后相等）。
4. **受控 storage 附加**：编排结果（`frequency/calibrated`、`axes/time_*_s`、`time_base/data`+`history_json`、`time_processed/data`+`history_json`）通过 application 层新增的受控写门面落盘：只写 §3.3 已声明 optional 组、遵循"先写数据 flush → 后更新 checkpoint flush"次序、目标文件已 finalize 时按显式策略处理、绝不原地改 raw；缺 `time_processed` 时对应组保持不存在（不得写空壳伪装）。
5. **域/history 正确性兜底**：所有组合都靠 core `_ALLOWED_TRANSITIONS` + `_validate_history_kind` + 各 stage 既有守卫，编排层不放宽、不吞异常、不重排 stage。
6. **排除**：不实现 UI、不做零时（time zero）与连续背景 stage、不改 core/processing/storage 既有文件、不新增第三方依赖、不引入 schema 版本变更（若必要 ⇒ 停并报告写 ADR）、不 commit/push。

**测试矩阵**（失败测试优先，`tests/contract/`（或 integration）新增单文件）：
1. **关键组合全覆盖**：OSL on/off × background on/off × bandpass on/off × dewow on/off × flat on/off 的代表组合（至少全关、仅 IFFT、全开、OSL+IFFT、OSL+bg+IFFT、OSL+bg+bp+dewow、全开+flat、flat-only-from-time_base 等）⇒ 断言最终域、history 序列（stage_name 有序、无重复）、`time_base` 恒存在、`time_processed` 仅在时域 stage 开启时存在。
2. **二次 OSL / 背景拒绝**：对已用相同 profile 校准的 history 再调 OSL ⇒ 拒绝；raw 域背景应用到校准域 ⇒ 拒绝；重复 background ⇒ 拒绝（core 唯一性 + stage 守卫双向）。
3. **错 profile / 非空 raw history**：fresh 入口喂非空 history ⇒ 拒绝；reuse 入口喂 profile_id 相同但内容变化、id 不同、通道绑定交换、缺 provenance（legacy）⇒ 全部 incompatible 且 mismatch 文案含字段级证据。
4. **保存-加载-回放对拍**：编排产出写入受控接口 → `RcScanReader` 重新打开校验通过 → 重放同一 raw + 同一 profile ⇒ 派生数组与 history **逐字节/bit-exact 相等**（幂等确定性；注入固定 `executed_utc` 消除时钟噪声）。
5. **raw byte 不变**：每个组合前后对 `/frequency/raw` 与全部 trace-major 必需列做 SHA256 对拍；reader 打开-关闭本身也不改字节。
6. **revision 竞争**：并发/交错提交两个 revision（用注入 clock + 显式线程/队列夹具，禁 sleep 猜时序）⇒ 过期结果被丢弃且不计入派生存储，最终态等于最新 revision；cancel 中途退出资源干净、可重入新 revision。
7. **取消与错误路径**：stage 抛 `DomainError` 时编排不留下半写派生组（或派生写入原子替换），raw 完好，错误可序列化上报。
8. **依赖回归**：029-035 定向测试 + core time_domain/storage reader 相关回归全绿。

**门禁**：定向测试（先红后绿）→ 相关回归 → `verify.py` 全量（基线 1396 passed + 新增用例数；mypy 55→56+ 源文件为预期变化）→ ruff + mypy + import + `git diff --check`；`changedPaths` 与 t2 契约 inScope 4 路径逐一相等；登记时 acceptanceResults criterion 与契约逐字一致、payload 精简。

## 6. 结论

八项依赖（ISSUE-011、018、029、030、031、032、033、034、035）全部有里程碑文件 `Done` 状态行 + HEAD 历史中的合并提交 + tracked 交付模块 + tracked 契约测试（34/39/32/22/35/34/38/40/38 个 test 定义）三重实证；`origin/main...HEAD` 计数 `0 0`，035 遗留的推送已在 036 开工前闭合。M06 L340 ISSUE-036 为首个 `Planned`，`src/uav_gpr/application/` 仅有命名空间占位、产品代码中无编排/revision 实现，**ISSUE-036 为下一可执行 Issue**。PROCESSING/CALIBRATION/DATA_FORMAT 三份契约的阶段顺序、域链、四概念边界、`.rcscan` v2 派生槽位与 reader/writer 接口面已逐条读取并落到本报告 §3；其中两项必须在 t2 前置决策并如实记录：(i) v2 冻结 schema 未声明 `/frequency/history_json`，派生频域历史的持久化形态需在既有 optional 组内解决或另写 ADR；(ii) 现有公开 writer 无派生组写路径，"受控 storage 接口"须在 application 层新建且不得触碰 raw 字节。门禁基线 **1396 passed / 4 deselected（271.83s）、ruff clean、mypy 55 source files clean、package import ok、`all gates passed` exit 0** 全部实测复现，核查前后工作树干净、无遗留产物。**ISSUE-036 可以开工（Ready）**，t2 按第 3 节契约与 t2 任务锁定的 inScope 4 路径执行。
