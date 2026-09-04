# ISSUE-029 实施计划：`.rcal/.rcbg`、兼容性与质量报告

日期：2026-09-05
执行器：AgentTeams `uav-gpr-issue-029-rcal-storage` 成员 engineer（任务 t2，attempt 56bb4753-5ce6-481a-887d-b951ddd34785）
基线件：[docs/reports/ISSUE_029_BASELINE_CONFIRMATION.md](../reports/ISSUE_029_BASELINE_CONFIRMATION.md)（main @ `5147a15`，工作树干净，门禁 1101 passed / 4 deselected，依赖定向 113 passed）
目标 Issue：ISSUE-029（`docs/issues/M06_CALIBRATION_PROCESSING.md` L79–115）；约束文档：`AGENTS.md` §3/§9/§10、`docs/DATA_FORMAT.md` §8、`docs/CALIBRATION.md` §4/§6/§7/§9、t1 基线单 §3 十条契约。

## 1. 目标与用户价值

在 `storage` 层交付版本化参考文件读写与字段级兼容性判定：`.rcal`（OSL 校准 Profile/集合）与 `.rcbg`（空采背景 Reference）的 JSON schema（格式名 + schema 版本 + 内容 digest + 复数无损编码）、严格 reader/writer、profile/reference ID、完整 axis/channel/config/domain/provenance/质量统计落档；加载只证明"可读且摘要可信"，启用前必须另行通过字段级 compatibility result（硬错配 incompatible、软差异 warning），**用户选中文件不等于启用**。它是后续校准应用与 UI 加载流程的直接依据。

## 2. 范围（任务契约 inScope = 4 个精确路径，changedPaths 与此逐一相等）

1. `src/uav_gpr/storage/calibration_files.py`（唯一实现模块：schema + digest + 复数编码 + writer/reader + CompatibilityResult + 质量报告框架，全部内部组织）
2. `tests/contract/test_calibration_files.py`（新文件：契约测试，红灯优先）
3. `docs/plans/2026-09-02-issue-029-rcal-storage.md`（本计划文档，含执行日志）
4. `docs/issues/M06_CALIBRATION_PROCESSING.md`（仅 ISSUE-029 状态行：`Planned → In progress → Review`）

## 3. 明确排除项（M06 L105–107 + 提示词 + 任务契约）

不把 OSL/背景应用到任何任务数据（不调用 `correct()/apply()` 做处理编排）、不做 Qt/UI、不因用户选中文件自动启用（本模块无"启用"副作用面）、不改 `osl.py`/`reference.py`/`core/**` 公共语义、不新增 core 错误码（复用现有 `ErrorCode`）、不改 `storage/__init__.py`（零导出约定同 rcscan 模块先例）、不 commit/push/创建分支、不新增 inScope 之外文件。

## 4. 设计决策（D1–D10，2026-09-05 定案）

- **D1 单一可判别信封**：两类文件共用顶层结构——`format_name` ∈ {`uav_gpr_rcal`, `uav_gpr_rcbg`}、`schema_version` = 整数 1（拒绝 bool/float/string 形态的版本值）、`payload` 对象、`content_sha256`（64 位小写 hex）。digest = SHA-256(canonical JSON(payload))（sort_keys、紧凑分隔符、UTF-8），写入时计算，读取时对 payload 重算比对——篡改任一数值节点必然失配。未知 `format_name`/`schema_version` fail-closed（`UNSUPPORTED_SCHEMA_VERSION`），不猜测语义。
- **D2 复数无损编码**：数组统一编码为 `{"dtype": "complex128"|"float64", "shape": [...], "re": [...], "im": [...]}`（实数数组省略 `im`），经 Python `float`（IEEE-754 double）往返保证 bit 级相等；`json.dumps(allow_nan=False)`、`loads(parse_constant=拒绝 NaN/Infinity)`；维度按域固定：OSL 各数组 `(frequency,)`，背景均值 `(channel, frequency)`，形状与声明 shape 严格一致否则 `SHAPE_MISMATCH`。参考源（rebar `storage/reference_files.py`，SHA-256 白名单见 t1 §2）的 real/imag 分离思路在此收紧为 dtype+shape 显式声明（AGENTS.md §3"所有数组轴、单位、通道和 S 参数绑定必须显式保存"）。
- **D3 类型化 domain 而非裸字符串**：`.rcbg` 载荷内 domain 存字符串，reader 重建为 I028 `ReferenceDomain.RAW/OSL_CALIBRATED` 枚举（非法值 `INVALID_ARGUMENT`）；`osl_calibrated` 必带 `calibration_profile_id`、`raw` 必不带（镜像 I028 会话构造校验，防止存储绕过内存态不变量）。
- **D4 `.rcal` 载荷 = 有序 profile 列表**：直接持久化 I027 `OslCalibrationSet`（profiles 顺序 = 通道顺序；position = channel order 是既有契约），每 profile 含 channel（ChannelSpec 全字段）、s_parameter、profile_id、三标准件 measured_mean/actual/capture_count、三个误差项、quality 六指标与 worst_max_abs_error。单 profile 也走同一格式（列表长度 1），避免第二套格式。
- **D5 provenance 自含**：两型文件都带 `provenance`：`created_utc`（canonical Z ISO，`from_utc_iso` 严格解析拒 naive）、`software_version`（非空 str）、`device_id`（UUID 或 null）、`config_sha256`（来源 MissionConfig 摘要或 null）、`axis`（完整频率轴 float64 列表，单位 Hz 显式字段 `frequency_unit: "Hz"`）、`channels` 顺序表。审计不依赖原临时对象（验收 3）。
- **D6 质量报告框架**：`.rcal` 侧透传 I027 `OslCalibrationQuality`（残差=rms/max abs error per standard）并附退化标志（求解成功即非退化，degenerate 在 I027 已 fail-closed，此处记录 `solve_degenerate: false` 常量字段留真机阶段扩展）；`.rcbg` 侧 `quality_report`：`trace_count`（采集道数）、`non_finite_rejected_traces`（被拒非有限道数，默认 0，供回放侧对账）、逐通道逐频点稳定性统计 `stability`（mean_abs_deviation 与 max_abs_deviation，由调用方从堆栈计算传入，reader 严格校验有限非负）。离群阈值语义挂账真机阶段（CALIBRATION §6）。
- **D7 字段级 compatibility result**：`check_calibration_compatibility(reference, context)` / `check_background_compatibility(...)` 返回 frozen dataclass：`verdict ∈ {compatible, compatible_with_warnings, incompatible}` + 逐项 `CompatibilityFieldCheck(field, severity ∈ {hard, soft}, status ∈ {match, mismatch, warning}, detail)`。**硬项**（任一 mismatch → incompatible 并全量列明）：channel/S 参数元组逐位等（换序= mismatch）、频率轴逐点 `array_equal`（长度与微差均 mismatch）、domain、`.rcbg` 校准域时的 `calibration_profile_id`、格式自身合法（digest 已在 reader 把关）。**软项**（→ warnings）：设备 ID 差异、`created_utc` 年龄超界、软件版本不同、环境标签（天线安装/离地高度/场地字符串）。纯判定函数：零文件 I/O、零副作用、绝不抛业务异常。
- **D8 "选择 ≠ 启用"建模**：reader 返回的对象只提供数据访问与 `compatibility_context()`（把文件内容整理成待比对上下文），不存在 enable/activate API；应用编排在后续 Issue。UI 集成不得在本模块出现（零 Qt 导入，import 守卫测试）。
- **D9 writer 原子性与不可覆盖**：目标存在 → `FileExistsError`（保留原件，AGENTS.md §11）；后缀不符（`.rcal`/`.rcbg`）→ `INVALID_ARGUMENT`；先写 `<target>.tmp-<uuid>` 再 `os.replace`；任何序列化/IO 失败清理半成品；`flush + os.fsync` 后 rename。读回后模型构造重跑全部校验（同 rebar 口径但改抛 core `DomainError` 家族）。
- **D10 测试纪律（红灯优先，T1–T9）**：往返（bit 级复数 + metadata + digest 稳定、两次写字节一致）、摘要篡改（改 payload 一字节 / 改 digest 字段 → 拒绝且指明字段）、未知版本/format（含 1.0/"1"/true 伪版本）、双通道顺序交换（S11/S22 换序 → hard mismatch 且不静默对齐）、频率微差（末点 +1 Hz 与少一点 → hard mismatch）、raw/calibrated domain 错配（含 profile_id 缺失/多余/不同）、质量异常（NaN mean/inf 稳定性指标/负道数 → writer/reader 双侧拒绝）、软警告路径（compatible_with_warnings 逐条列明、选文件不产生启用副作用）、损坏 JSON/缺字段/错类型 fail-closed。全部确定性合成数据（seeded RNG），无 sleep，tmp_path 夹具。

## 5. 文件改动（精确路径，无 glob）

| 路径 | 动作 | 内容 |
|---|---|---|
| `src/uav_gpr/storage/calibration_files.py` | 新增 | schema 常量、digest/编码助手、StoredOslProfile/StoredAirBackground、writer/reader、质量报告、CompatibilityResult |
| `tests/contract/test_calibration_files.py` | 新增 | T1–T10 契约测试（约 30+ 用例） |
| `docs/plans/2026-09-02-issue-029-rcal-storage.md` | 新增 | 本计划 + 执行日志 |
| `docs/issues/M06_CALIBRATION_PROCESSING.md` | 修改 | 仅 ISSUE-029 状态行 |

## 6. 验证命令

1. `./.venv/Scripts/python.exe -m pytest tests/contract/test_calibration_files.py -q`（红灯→绿灯）
2. 回归：`pytest tests/contract/test_calibration_osl.py tests/contract/test_calibration_reference.py tests/contract/test_rcscan_reader.py -q`
3. 全量门禁：`./.venv/Scripts/python.exe tools/quality/verify.py`（预期 ≥1101 passed/4 deselected 之上全绿）
4. `git diff --check`、`git status --porcelain=v1` 边界核对。

## 7. 执行日志

- 2026-09-05（attempt 56bb4753，t2 开工）：认领 t2 并 in_progress；captain 中途纠正落点为 inScope 4 精确路径（实现集中于 `storage/calibration_files.py` 单文件），确认此前未创建任何偏离文件（工作树仅 t1 报告未跟踪）。完成消费面调研：I027 `OslCalibrationProfile/OslCalibrationSet/OslCalibrationQuality/build_osl_calibration` 全属性面、I028 `AirBackgroundReference/ReferenceDomain`、core `MissionConfig.config_sha256/to_dict`、`ChannelSpec` 五字段、`to_utc_iso/from_utc_iso`、`ErrorCode` 可用码集（含 UNSUPPORTED_SCHEMA_VERSION/CALIBRATION_DOMAIN_MISMATCH 等，无需新增）；rebar `reference_files.py`（本地副本哈希核对通过）real/imag 编码与严格 JSON 口径借鉴点确认；轴非均匀不能成 MissionConfig（NON_UNIFORM_AXIS），近差以末点 +1 Hz 构造（仍均匀性破坏→用 array 直构 profile 轴即可，`_require_axis` 允许任意严格递增）。本计划落盘，随后红灯测试 → 实现。
- 2026-09-05（红灯）：`tests/contract/test_calibration_files.py` 先行落盘（collection error = 模块不存在，红）。
- 2026-09-05（实现+迭代）：`src/uav_gpr/storage/calibration_files.py` 落地 D1–D10；调试循环共 4 轮修复：①误写 `object.__setattr__=` 语句删除；②capture_count 私有槽漏绑定；③稳定性统计形状统一为 channel×frequency（测试辅助改逐通道 MAD/偏差）；④loaded reference 只读视图（write-protected base + view，同 I027 readonly 模式）。ruff/mypy 清零（删未用导入与 `_optional_str` 死代码、std_arrays 改 tuple 消 Any/int overload 报错）。M06 ISSUE-029 状态行 `Planned → Review`。
- 2026-09-05（验证）：定向 32 passed；相邻回归 86 passed；全量 verify.py 见下条执行日志续记。
- 2026-09-05（补强+复审前终验）：首轮全量门禁 1133 passed / 4 deselected exit 0 后，自查发现 `StoredOslProfile` 缺结构相等（往返仅逐数组断言），补 canonical-payload `__eq__` + `__repr__`（镜像 I027 模式）与测试断言 `loaded.profiles == payload.profiles`；ruff/mypy(49 files)/定向 32/相邻 91 全绿，最终 verify.py 复跑记录于 t2 完成报告。
