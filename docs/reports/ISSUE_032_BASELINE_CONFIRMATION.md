# ISSUE-032 开工基线确认单（自动化轮 · engineer）

## 1. 目标 Issue 与依据

- **Issue**：ISSUE-032「OSL 处理阶段与 calibrated provenance」（`docs/issues/M06_CALIBRATION_PROCESSING.md` L190-225）
- **状态行核查**：M06 L192 ISSUE-032 状态 = `Planned`；映射 FR-011、012；直接依赖 ISSUE-027、030。
- **工作区根替换声明**：Issue 提示词中 `E:\wurenjiruanjian` 一律替换为当前工作区根 `/mnt/d/博士任务/无人机软件/UAV-GPR`（Windows：`D:\博士任务\无人机软件\UAV-GPR`）。
- **流程依据**：`AGENTS.md`、`docs/issues/README.md`（通用执行协议 §3、一次一个 Issue）、`docs/ISSUE_REVIEW_STANDARD.md`（t1 基线 → t2 实现 → t3 独立复审）。
- **契约文档**：已读 `docs/CALIBRATION.md` 第 5 节（处理顺序）+ 第 6/7 节，`docs/PROCESSING.md` §1/§2（域链与 history 规则）。

## 2. 只读核查证据（全部实测）

| 项 | 结果 |
|---|---|
| HEAD / 分支 | `main @ b4f6dec`（`docs(issues): mark ISSUE-031 Done after automated merge`）；工作树干净（`git status --porcelain` 空，除本单外无输出） |
| 远端同步 | `git rev-list --left-right --count origin/main...HEAD` = `0 3`：本地 main ahead of origin/main by 3 commits（031 合并链推送在网络重试中，以 captain 后台 push 为准，不在本单断言远端状态；origin/main 停在 `2c0abdc` = mark ISSUE-030 Done） |
| ISSUE-027 Done 证据 | M06 L7 状态行 = Done（2026-09-02 独立复审 VERDICT=PASS 后经授权合并，见 `docs/reports/ISSUE_027_REVIEW_REPORT.md`）；合并提交 `a2f65c6` 在 HEAD 历史（`git show a2f65c6 --stat`：`src/uav_gpr/calibration/osl.py` 1008 行 + `tests/contract/test_calibration_osl.py` 863 行均 tracked）✓ |
| ISSUE-030 Done 证据 | M06 L118 状态行 = Done（2026-09-05 自动化轮 t3 复审 VERDICT=PASS 后自动合并，见 `docs/reports/ISSUE_030_REVIEW_REPORT.md`）；合并提交 `89fd9bb` 在 HEAD 历史（`bandpass.py` 460 行 + 契约测试 674 行 tracked）✓ |
| 依赖定向回归 | `.venv/Scripts/python.exe -m pytest tests/contract/test_processing_bandpass.py tests/contract/test_calibration_osl.py tests/unit/test_core_time_domain.py -q` → **98 passed**；另跑 `test_calibration_osl.py + test_processing_bandpass.py` → **63 passed**（exit 0） |
| 可执行性 | `src/uav_gpr/processing/` 仅 `bandpass.py`（030）与 `time_domain.py`（031），无 OSL stage 先行实现；`DataDomain.FREQUENCY_CALIBRATED` 已在 `core/enums.py` 定义且被 `ProcessingRecord` fail-closed 引用规则消费 → **ISSUE-032 为下一个可执行 Issue**，无重复实现风险 |

关键本项目契约文件哈希（SHA-256 前 16 位，供 t3 交叉核对）：

```text
5b7136979df9e6ad  src/uav_gpr/calibration/osl.py
f707839674ceb5e1  src/uav_gpr/processing/bandpass.py
cfa8271f5ebd545c  src/uav_gpr/core/time_domain.py
386c8bdf21fdf2c3  src/uav_gpr/core/frequency.py
4e906f159b1c8599  src/uav_gpr/core/enums.py
f015489a2eb18c79  src/uav_gpr/storage/calibration_files.py
5ca0dc5fdd2ccedc  docs/CALIBRATION.md
9d730ec7a0f7c223  docs/PROCESSING.md
```

参考源说明：M06 ISSUE-032 条目未引用 `E:\钢筋仪软件开发` 具体迁移文件（OSL 数学已由 027 完成迁移并黄金对拍）；032 是纯 domain-conversion stage，数值对拍对象 = 本项目 `OslCalibrationProfile.correct()` / `OslCalibrationSet.apply()` 输出（027 已对拍过参考），无需新的参考源哈希登记。

## 3. 契约要点（对 t2 实现有约束）

### 3.1 CALIBRATION.md §5 处理顺序

```text
frequency_raw -> optional OSL -> frequency_calibrated (保存 OSL 后、空采前)
  -> optional air background -> optional bandpass -> IFFT
```

不得覆盖 raw；不得对已使用相同 profile 校准的数据再次校准；不得把 raw 域背景应用到校准域。§7：应用后必须保存引用 ID、内容摘要、原文件信息与执行阶段记录（provenance 审计要求）。

### 3.2 PROCESSING.md §1/§2 强约束（逐条落到 032）

1. `frequency_raw` 只允许作为输入；任何阶段输出 `frequency_raw` 拒绝。
2. 稳定 `stage_name` 同一 history 内不得重复应用（`ProcessingHistory.__init__` 已强制，bump version 不能绕过——032 的"重复 OSL 检测"由 core + stage 双重 fail-closed）。
3. **输出 `frequency_calibrated` 必须带 `calibration_profile_id`**（`_validate_references` 已强制）；后续记录显式携带的引用必须与产生其对应域输入的上一记录相同（`_validate_provenance_continuity` 已强制）→ safe reuse 判定接口的核心依据。
4. history 第一项输入域必须是 `frequency_raw`（`_START_DOMAINS` 已强制）。
5. 每阶段输入不可变、输出新对象、追加可序列化参数/版本/历史（AGENTS.md §3 同文）。

### 3.3 core 层既有守卫（t2 直接复用，不新建平行类型）

- `ProcessingRecord`：构造时 `_validate_transition`（合法 hop：`FREQUENCY_RAW → FREQUENCY_CALIBRATED` 在 `_ALLOWED_TRANSITIONS` 中）+ `_validate_references`（calibrated 输出必须带 profile id；时域记录不得携带频域引用）；`to_dict/from_dict` JSON-safe 往返。
- `ProcessingHistory.append`：链校验 + 重复 stage_name 拒绝 + provenance continuity 校验，返回新对象。
- `FrequencySweep`/`FrequencyScan`：data 写入即 `_immutable_array`（read-only view），形状 `channel×frequency` / `trace×channel×frequency` 严格校验，输出重建同容器类型、保留 channels 与 per-trace metadata。
- 030 模式沿用：`ProcessingStage` Protocol、`StageResult`、`_record_for`、`_input_domain_of`（history 空 ⇒ `FREQUENCY_RAW`）均在 `bandpass.py` 内——t2 从 `uav_gpr.processing.bandpass` import 这些公共符号（同层 processing 包内引用，符合 AGENTS.md §9 依赖方向），不复制第二套。

### 3.4 osl.py（ISSUE-027）profile 消费面（只读，不改一字节）

| API | 语义 | 032 用法 |
|---|---|---|
| `OslCalibrationProfile.profile_id -> CalibrationProfileId` | UUID 身份 | 写入 record 的 `calibration_profile_id` + parameters |
| `.channel -> ChannelSpec` / `.s_parameter`（委托 channel） | 绑定通道与 S11/S22 | 逐通道校验：scan.channels 与 set.profiles 序一致、S 参数匹配 |
| `.frequency_hz -> np.ndarray`（readonly） | profile 轴 | 与 source.frequencies_hz `array_equal` 校验（错轴拒绝） |
| `.correct(measured) -> np.ndarray` | `(frequency,)` 复数 → 校正副本（只读 complex128，输入不改） | 单 sweep 路径逐道应用 |
| `OslCalibrationSet(profiles)` | 位置=通道序；拒空/非 profile/重复 channel/共享轴不一致 | t2 构造入参（或 `Mapping[channel_id, profile]` 形态，最终接口在 t2 定稿） |
| `.apply(measured, channels) -> np.ndarray` | `(channel,frequency)` 行序严格等于 set.channels（精确全等比较，错序拒绝）；shape/dtype/non-finite 全 fail-closed；逐 profile `correct` 后 stack | sweep 主路径数值应用 |
| `.profile_for(channel)` | 全等绑定查询，无绑定则 CHANNEL_CONTRACT_MISMATCH | 多通道分别应用对应 profile |
| `build_osl_calibration(...)` | 求解器（027） | **032 不调用**（排除项：不采 OSL、不求解） |

digest 供给面：`OslCalibrationProfile` 本身无 digest 属性；`storage/calibration_files.py` 提供 `StoredOslProfile.from_profile(profile).to_payload()`（含 profile_id/channel/轴/九向量/道数的完整 JSON）与 `_digest_of`（canonical JSON SHA-256，sorted keys）。t2 计算 profile content digest 的正确姿势：`hashlib.sha256(canonical_json(StoredOslProfile.from_profile(p).to_payload()))`（自实现 canonical dump 或复用 storage 私有函数需按分层决定——processing 不应依赖 storage，倾向在 stage 内以同一 canonical 规则（`json.dumps(payload, sort_keys=True, separators=(",",":"))` + UTF-8）本地实现并测试锁定格式名/字段序无关性）。record parameters 记 `profiles: [{channel_id, s_parameter, profile_id, content_sha256}]` 按 scan 通道序排列。

### 3.5 团队计划硬约束（t2 inScope）

inScope 精确 4 路径（changedPaths 逐一相等）：

1. `src/uav_gpr/processing/osl_calibration.py` — `OslCalibrationStage`（新文件）
2. `src/uav_gpr/processing/__init__.py` — 导出（当前文件存在但近空，编辑合规）
3. `tests/contract/test_processing_osl_calibration.py` — 契约测试（新文件）
4. `docs/issues/M06_CALIBRATION_PROCESSING.md` — ISSUE-032 状态行 Planned → In progress → Review

（若 t2 需要第 5 个文件如 docs/plans 日志，须先经 captain 调整契约；本单如实记录计划口径，t2 开始时锁定。）

## 4. 门禁基线（verify.py 复跑，实测）

- `tools/quality/verify.py`（interpreter = `.venv/Scripts/python.exe`，Python 3.13.14）：
  - pytest (non-hardware)：**1215 passed / 4 deselected in 270.81s**（4 deselected = 硬件哨兵 + opt-in LibreVNA），与团队计划声明的 1215 一致 ✓
  - ruff：`All checks passed!` ✓
  - mypy：`Success: no issues found in 51 source files` ✓（计划口径 51）
  - package import ok；`[quality] all gates passed` exit 0
- 核查前后 `git status --porcelain` 均为空（本单为唯一新增未跟踪文件）；无重定向产物遗留。

## 5. 实施计划摘要（供 t2）

**范围**：`OslCalibrationStage(calibration: OslCalibrationSet)` 实现 `ProcessingStage`：
- `stage_name="osl_calibration"`（snake_case 稳定 token）、`output_domain=FREQUENCY_CALIBRATED`、`input_domain={FREQUENCY_RAW}`（032 语义固定 raw→calibrated；从派生快照开始无 anchor 不允许）。
- `apply(source, *, history, executed_utc/clock)`：history 末域 ≠ `FREQUENCY_RAW`（含已有 OSL history 后接其它阶段的场景）⇒ `PROCESSING_DOMAIN_MISMATCH` 拒绝；`ProcessingHistory.append` 再挡同 history 重复 `osl_calibration`（二次校准拒绝的双保险）。
- 逐通道校验：source.channels 与 set 的通道序精确一致（错序/缺通道/多余通道拒绝）；每 profile 的 `frequency_hz` 与 source 轴 `np.array_equal`（错轴/长度不符拒绝）；S 参数反射性由 027 构造保证 + 显式断言。
- 数值应用：sweep `(channel,frequency)` 走 `set.apply(data, channels)`；scan `(trace,channel,frequency)` 向量化逐 trace 应用（或等价 transpose 批量），输出新对象（core 模型自动 read-only copy），channels/metadata 保留。
- provenance：record.parameters 含 per-channel `{channel_id, s_parameter, profile_id, content_sha256(digest)}` + `algorithm="osl_one_port_v1"`；record 携 `calibration_profile_id`（多通道 set 时取集合级聚合 digest 对应的代表 ID 策略须在 t2 定稿并测试——core 只允许一个 profile id 字段，双通道场景的表述方式（如 set digest 放 parameters，id 字段放首 profile 或显式约束单 profile 记录）为 t2 设计决策点，验收要求是"错 profile/axis/channel 或已有 OSL history fail-closed"+"safe reuse 只接受严格相同 profile provenance"）。
- safe reuse 判定：公开函数/方法（如 `check_safe_reuse(history, calibration) -> bool/raise`）：仅当 history 中产出 `FREQUENCY_CALIBRATED` 的记录 `calibration_profile_id` 与请求 profile id 一致，且 parameters 记录的 per-channel digest 与当前 set 各 profile digest 全等（严格相同 provenance）才判可复用；任何差异（同 ID 不同内容、不同 ID、缺 digest）拒绝并给字段级差异上下文。
- **排除**：不采 OSL、不调 `build_osl_calibration`、不保存文件（不写 .rcal/.rcbg）、不应用空采/IFFT、不做 UI、不修改 osl.py/storage 任何字节。

**测试矩阵**（失败测试优先）：双通道 profile 正确应用（S11+S22 各用其 profile，逐道对拍 `set.apply`/`profile.correct` 参考值）；错序通道拒绝；错轴/错长度拒绝；错 S 参数/未绑定通道拒绝；空 history（raw 域）通过、非 raw 末域 history 拒绝；二次 OSL（同 history 再 apply）拒绝；bump stage_version 不绕过；safe reuse 相同 provenance 通过 / 异 profile（ID 或 digest 不同）拒绝；history 追加完整性（to_dict/from_dict 往返、parameters JSON-safe）；raw 不变（输入数组 readonly + 内容 bytes 前后一致）；shape/dtype/metadata 保持；黄金数值对拍（人工小向量 OSL 公式 E=(m-D)/(-T·m+S...) 等价于 `profile.correct`，源哈希登记）。

**门禁**：目标测试 → 相关回归（processing/calibration 定向）→ verify.py 全量（基线 ≥1215 passed + 新增用例数）→ ruff + mypy(51) + import；diff 检查 changedPaths 与 inScope 逐一相等。

## 6. 结论

直接依赖 ISSUE-027（`a2f65c6`，osl.py 1008 行 tracked + 复审 PASS 报告）与 ISSUE-030（`89fd9bb`，bandpass.py + ProcessingStage/StageResult 契约就绪）均有合并提交与测试实测证据；ISSUE-031 亦 Done（HEAD 即其标记提交），域链 `frequency_calibrated → background/filtered/ifft` 下游全部可用。core 层 `_validate_references`/`_validate_provenance_continuity`/重复 stage 拒绝为 032 的 fail-closed 语义提供现成守卫；osl.py 消费面（`profile_for`/`apply`/`correct`/`from_profile.to_payload` digest 素材）齐备且只读。**门禁基线 1215 passed / ruff clean / mypy 51 files clean 全部实测复现，工作树干净。ISSUE-032 可以开工（Ready）**，t2 按第 3 节契约与 inScope 4 路径执行。
