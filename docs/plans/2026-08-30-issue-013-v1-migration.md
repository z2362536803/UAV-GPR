# ISSUE-013 实施计划：`.rcscan` v1 兼容读取与显式迁移

日期：2026-08-30
状态：已随基线确认单（`docs/reports/ISSUE_013_BASELINE_CONFIRMATION.md`）锁定，作为 t2 执行的权威契约
Issue：ISSUE-013（`docs/issues/M02_STORAGE.md` L190–225，状态 Planned）
执行者：engineer（t2）；复审：reviewer（t3，按 `docs/ISSUE_REVIEW_STANDARD.md`）

## 1. 目标

让地面端**安全只读**打开钢筋仪 `.rcscan` v1 文件（映射 raw/calibrated/time/channels/axes/history 到 UAV-GPR 领域模型），并可选择**显式**生成携带完整 migration provenance 的新 v2 文件。不原地升级、不伪造 UTC/GNSS。

## 2. 用户价值

- 旧钢筋仪数据可在地面端只读回看，无数据迁移风险（源文件字节不变）。
- 显式迁移提供一条可审计的 v1→v2 路径：新 mission/file ID、逐道新 UID、ISSUE-009 规范 raw hash、逐道行经 v2 权威 codec 写入，产出可被 ISSUE-011 严格 reader 验证的 v2 文件。

## 3. 范围（in scope，按 t2 任务契约收窄）

1. `src/uav_gpr/storage/rcscan_v1.py`：v1 只读 adapter（`RcScanV1Reader` + `V1RcScanData` + `V1InspectionReport`）**与显式 v1→v2 迁移 API（`migrate_v1_to_v2`）同置于此单一新模块**（t2 任务契约 in-scope 仅列此模块，故不新建 `rcscan_migration.py`；本计划 v1.0 曾声明独立模块，见 §13 执行日志 D-P1）。
2. `tests/contract/test_rcscan_v1.py`：失败测试优先 + **匿名黄金夹具 builder 内嵌于此测试文件**（t2 契约 in-scope 不含 `tests/fixtures/`；builder 采用 `test_rcscan_reader.py` 同款"测试内 builder"模式，见 §13 执行日志 D-P2）。
3. `tests/contract/rcscan_v1_golden.json`：匿名黄金 manifest（结构/数值/expected digest/生成参数/参考源哈希记录）。
4. `docs/issues/M02_STORAGE.md`：ISSUE-013 状态行 `Planned → In progress`（开工时）→ `Review`（实现+测试完成后）。
5. `docs/plans/2026-08-30-issue-013-v1-migration.md`：本计划 + §13 执行日志（红灯→绿灯、v1 结构提取记录、决策、门禁数字、环境事实）。

> **文档范围调整（v1.1）**：t2 任务契约将 `docs/DATA_FORMAT.md` 列为 out of scope（零改动）。v1 映射契约与迁移语义的全部文档化改由本计划（§6 决策记录、§13 执行日志）与 golden manifest 承载；`DATA_FORMAT.md` §9 扩充延后（作为遗留限制报告，建议后续经授权流程补入，见 §10 R5）。

## 4. 明确排除项（out of scope）

- 不导入旧 UAV-GPR CSV/NPZ（AGENTS.md 2.2 禁止）；不做批量转换 UI/CLI 入口之外的任何自动迁移；
- 不原地升级/改写 v1 文件（源字节不变，SHA-256 钉死）；
- 不伪造 UTC/GNSS/位置：缺字段保持 None/空；单调纳秒为显式标注的 UTC 推导值（见 §6 决策 D3）；
- 不改 `rcscan_v2.py`/`raw_hash.py`/`incremental_writer.py`/`rcscan_reader.py` 的既有公共语义（沿用 ISSUE-012 决策：不抽公共函数、不扩展 writer；迁移写文件为独立路径）；
- 不修改两个参考项目（含本地副本 `D:\博士任务\rebar-inspector`），只读使用；
- 不做 GUI、不 commit、不 push、不创建/切换分支；不进入 ISSUE-014。

## 5. 参考源哈希与迁移清单

按 `docs/REFERENCE_MIGRATION.md` 第 5 节模板（迁移清单，只记录真正阅读和使用的文件）：

```text
target issue/task:          ISSUE-013（.rcscan v1 只读适配与显式 v1→v2 迁移）
reference repository:       钢筋仪软件开发（E:\钢筋仪软件开发；本机不可达）
reference branch + HEAD:    manifest 冻结 feat/issue-16-pause-resume @ 938875234a99b47d78cfec940671005b63e9d15c（worktree dirty）
本地只读副本:               D:\博士任务\rebar-inspector（GitHub 克隆 z2362536803/rebar-inspector，
                            main @ 7c522d2aebe6a835acb969e8012565715f64a238；下载脚本 download-rebar-inspector.ps1）
reference worktree status:  dirty（副本自身；以逐文件 SHA-256 为事实）
source file(s) + SHA256:    16 个文件全部与 ISSUE-001 manifest 冻结哈希对拍一致（基线确认单 §3.2 表）；
                            核心：storage/rcscan.py=290c5dad…bc4c、storage/document.py=a173d3ad…57da、
                            core/schema.py=84a8d91a…4ae4、core/scan.py=3f608405…3c03、
                            core/frequency.py=8164a641…5bad、core/time_domain.py=882a2911…4597、
                            core/history.py=077c8b29…69ba、core/trace.py=a9f7ed31…edf2、
                            core/channels.py=cf0fb505…9340、core/enums.py=08129eb7…6b19
trusted behavior/contract:  v1 物理 schema（节点/属性/JSON 编码/严格整数版本/版本拒绝/JSON 严格解析）、
                            读取校验语义（缺必需节点、类型错误、time_processed 无 time_base 拒绝）、
                            BScan/TimeDomainScan/TraceMetadata/ProcessingHistory 聚合约束
excluded behavior:          钢筋仪 UI/采集/校准/处理实现；RcScanDocument 之外的模型不迁移；
                            不导入 rebar_inspector 包（产品与测试都不依赖参考包）
new target module(s):       src/uav_gpr/storage/rcscan_v1.py、src/uav_gpr/storage/rcscan_migration.py
UAV-specific adaptations:   通道映射为新 ChannelSpec（channel_id/display_name 推导）；trace 元数据映射为
                            逐道 UTC + 推导单调值（标注）；GNSS 全空 + gnss_missing；新 mission/file/trace ID；
                            ISSUE-009 规范 raw hash；迁移 provenance attrs
tests/golden fixtures:      tests/fixtures/rcscan_v1_builder.py（合成，镜像冻结 v1 schema 布局）；
                            tests/contract/rcscan_v1_golden.json（黄金 manifest）
new tests added:            test_rcscan_v1_reader.py（contract）、test_rcscan_v1_migration.py（integration）
numeric or comparison:      迁移后 v2 由严格 RcScanReader 复验；raw/calibrated/time 数组、axis、channel、
                            history（stage/params/timestamp）与 v1 源逐值对拍
license/provenance review:  参考项目为私有授权参考（ADR-0005）；只提取格式契约，不复制代码；
                            夹具全部合成（无现场隐私）
```

## 6. 设计决策记录（t2 必须遵守；复审重点审查项）

### D1：v1 只读 adapter 的模块与错误语义

- `RcScanV1Reader(path)`：`"r"` 打开；`format_name` 必须为 `"rcscan"`；`schema_version` 必须为**真正整数 1**（bool/float/str 拒绝——镜像 v1 `_coerce_schema_version`）；`1` 以外整数 → `DomainError(ErrorCode.UNSUPPORTED_SCHEMA_VERSION)`（v2 文件打开即被 v1 adapter 拒绝，双向隔离）。
- 必需节点缺失/类型错误（dataset vs group）/严格 JSON 失败/`time_processed` 无 `time_base` → `DomainError(ErrorCode.INVALID_ARGUMENT)`，`details` 带字段路径（结构化、可序列化）。
- `V1InspectionReport`（`to_dict()`）：逐字段（根 attrs、channels、axes、frequency raw/calibrated、position、trace_metadata、time_base、time_processed）的存在性/类型/长度/解码状态 + 源文件 SHA-256；与 fail-closed 错误共同满足「不支持/损坏 v1 的字段级报告」验收。
- 只读保证：读取前后源文件字节不变（测试 SHA-256 钉死）。

### D2：v1 → 新领域模型映射（`V1RcScanData` 聚合）

- channels：`ChannelSpec(channel_id=f"{logical.value.lower()}_{s_parameter.value.lower()}", logical_polarization=LogicalPolarization.from_value(logical.value), s_parameter=SParameter.from_value(s_parameter.value), display_name=f"{logical.value} {s_parameter.value}", antenna_note=None)`；推导 ID 重复 → `DUPLICATE_CHANNEL` fail-closed；v1 只存在 HH/VV × S11/S21/S12/S22 组合。
- frequency：`FrequencyScan(channels, frequencies_hz, data=raw, metadata=())`（raw 为 trace×channel×freq，不可变快照由模型保证）。
- `frequency_calibrated`：数组原样暴露（形状必须与 raw 一致，否则 fail-closed）。
- `time_base`/`time_processed` → `TimeDomainScan`：kind 对应；`history` = 合成 `ProcessingHistory`：
  - time_base：`[ProcessingRecord(stage_name="v1_import_time_base", stage_version=<adapter 版本 token>, parameters={"v1_history": <v1 history_json 逐条 stage/params/timestamp 的规范 JSON>}, input_domain=FREQUENCY_RAW, output_domain=TIME_BASE, executed_utc=<v1 created_utc>, software_version=<adapter 版本 token>)]`；
  - time_processed：time_base 记录 + `ProcessingRecord(stage_name="v1_import_time_processed", ..., input_domain=TIME_BASE, output_domain=TIME_PROCESSED, ...)`（链合法、stage 名唯一）；
  - **`executed_utc` 恒取 v1 文件 `created_utc` 属性**（属性缺失/损坏 → fail-closed；绝不取当前时间）。
- trace 时间戳：`/trace_metadata/timestamps_utc`（ISO 字符串，须 tz-aware，镜像 v1 语义）→ `tuple[datetime, ...] | None`（缺组即 None）；`extras_json` → `tuple[Mapping[str, JsonValue], ...] | None`；两者长度必须等于 trace 数且互等（镜像 v1 校验）。
- `position_m`/`position_source`/`trigger`/`generator`/`created_utc` 原样暴露（`created_utc` 解析失败 → 报告 + fail-closed）。

### D3：迁移写路径与逐道语义（核心决策，复审重点）

- `migrate_v1_to_v2(source, target_dir, *, if_bw_hz, power_dbm, target_interval_s, gnss_max_age_s, software_version, mission_id=None, file_id=None, device_id=None, role=EndpointRole.GROUND, created_utc=None, note=None, clock=None, fault_hook=None, filesystem=None)`。
- **确定性**：`mission_id`/`file_id`（GroundFileId）/`device_id`/逐道 `trace_uid` 默认按 uuid5 推导——命名空间常量 `UUID_NS = uuid.UUID("2e6c5f60-8f3b-4a1e-9c6d-0b7a3d5e9f01")`（ISSUE-013 冻结），名称分别为 `f"mission:{source_sha256}"`、`f"file:{source_sha256}"`、`f"device:{source_sha256}"`、`f"trace:{source_sha256}:{trace_index}"`；均可显式覆盖。同输入 + 同选项 → 输出逐字节一致（重复迁移确定性）；目标已存在 → 拒绝（绝不覆盖）。
- **写路径**（ISSUE-012 模式，不用增量 writer——其不写可选组且本 Issue 不得扩展其公共语义）：
  1. `RcScanV1Reader` 打开源（源只读）；
  2. `MissionConfig.from_frequency_axis(frequency_axis_hz=源轴, if_bw_hz, power_dbm, channels=映射通道, acquisition_mode=CONTINUOUS, planned_trace_count=None, target_interval_s, gnss_max_age_s, gnss_no_fix_policy=RECORD_WITHOUT_POSITION, created_utc=源 created_utc（默认）, software_version, ...)`——轴非均匀/负起点 → `DomainError`（blocked，无法无伪造表示）；
  3. `create_rcscan_v2` 建 `<file_id>.partial.rcscan` 骨架（writing；mission/device/file id、config、channels、axis、config_sha256）；
  4. 逐道：`trace_metadata_to_cells(metadata)` 写全部行列 + raw `<c16` 切片（分块有界内存；`chunk_rows` 参数化）→ flush → 更新 checkpoint（committed=n、last_trace_index=n-1、updated_utc=clock）→ flush；
  5. 写可选组（存在才写）：`/frequency/calibrated`、`/axes/time_base_s` + `/time_base/data` + `/time_base/history_json`、`/axes/time_processed_s` + `/time_processed/data` + `/time_processed/history_json`；**history_json 内容 = 适配后 `ProcessingHistory.to_dict()` 的规范 JSON**（v1 原文内嵌于 import 记录参数，单一事实源）；
  6. mission attrs：`started_utc`=首道时间戳、`ended_utc`=末道时间戳（源自文件数据，不取 now）、`completion_kind=completed`、`lifecycle_state=finalized`；迁移 provenance 附加属性（对旧 reader 透明，ISSUE-012 先例）：`migration_source_sha256`、`migration_tool_version`、`migration_v1_created_utc`、`migration_source_format="rcscan_v1"`；
  7. 关闭句柄 → 严格 `RcScanReader` 复验（lifecycle/committed/可选组一致）→ 原子改名 `<file_id>.rcscan`；
  8. 任一步失败 → 关闭句柄、best-effort 删除暂存（残留恒为 partial 命名）、抛结构化错误；重试安全。
- **逐道 TraceMetadata**（v2 行契约）：mission/device 新 ID；`trace_index=i`；`trace_uid` 推导；sweep UTC = v1 `timestamps_utc[i]`（started=midpoint=finished=该值，v1 单时间戳语义——文档标注）；**单调纳秒 = `(timestamps_utc[i] - created_utc)` 的整数纳秒（确定性 UTC 推导值，恒 ≥0、有序）——v1 无单调时钟记录而 v2 冻结行契约不允许缺失，故以推导值填充；该推导值以迁移 provenance（`migration_source_format="rcscan_v1"` 属性 + 本计划/模块 docstring）标记为 derived 导入值，绝不冒充真实单调时钟读数**（见 §6 风险 R2；DATA_FORMAT 契约文档按 R5 延后，标注落在本计划与模块 docstring）；`target_interval_s`=显式参数（>0 校验由模型保证）；`actual_interval_s`=首道 None，其后 `t[i]-t[i-1]` 秒；`schedule_error_s`=首道 None，其后 `actual-target`；`connection_generation=0`（v1 无此概念，文档标注）；`raw_trace_sha256`=按 ISSUE-009 framing 现算（迁移后 `hash_verified=True`）；`gnss_match=None`、`quality_status=DEGRADED`、`quality_reasons=(GNSS_MISSING,)`（满足 TraceMetadata fail-closed：无 match 必须带 gnss_missing；无 0/0 坐标）。
- **迁移 blocked（结构化拒绝）**：v1 缺逐道时间戳（无法诚实构造 v2 行）；频率轴非均匀/负起点；读侧已拒绝的损坏/未知版本。blocked 原因进入结构化错误 details。
- **v1 频域 history**：v2 冻结 schema 无 `/frequency/history_json` 数据集——v1 频域 history 原文作为规范 JSON 字符串写入 mission attr `migration_v1_frequency_history`（附加属性，透明；信息零丢失）。

### D4：夹具与黄金 manifest

- `tests/fixtures/rcscan_v1_builder.py`：合成 builder，按冻结 v1 schema 镜像 `save_rcscan` 布局（attrs、channels JSON、axes、frequency raw/calibrated、history_json、position_m、trace_metadata、time_base/time_processed 组）；变体参数化（单通道 S11 / HH+VV 双通道、含/不含 calibrated、含/不含 time 组、含/不含 trace_metadata/position）。
- `tests/contract/rcscan_v1_golden.json`：每个黄金夹具的结构期望（节点/属性/形状/映射结果），独立于代码对拍。
- 全部合成：频率轴、复数数据、时间戳、position 均为确定性合成值，无任何现场隐私。

## 7. 文件改动

| 文件 | 动作 | 说明 |
|---|---|---|
| `src/uav_gpr/storage/rcscan_v1.py` | 新增 | v1 只读 adapter（D1/D2） |
| `src/uav_gpr/storage/rcscan_v1.py` | 新增 | v1 只读 adapter + 显式迁移 API（D1/D2/D3，单一新模块） |
| `tests/contract/test_rcscan_v1.py` | 新增 | 读侧契约测试（矩阵 A）+ 迁移契约测试（矩阵 B）+ 匿名夹具 builder（D4） |
| `tests/contract/rcscan_v1_golden.json` | 新增 | 黄金 manifest（D4） |
| `docs/issues/M02_STORAGE.md` | 修改 | ISSUE-013 状态行 `Planned → In progress`（开工时）→ `Review`（实现+测试完成后） |
| `docs/plans/2026-08-30-issue-013-v1-migration.md` | 修改 | 本计划 v1.1 范围收窄 + §13 执行日志 |

## 8. 测试矩阵（失败测试优先；对应提示词验收逐项）

### A. 读侧（`tests/contract/test_rcscan_v1.py`）

| # | 场景 | 断言 |
|---|---|---|
| A1 | 黄金夹具（双通道 + calibrated + time_base + time_processed + trace_metadata + position）读取 | channels/axis/raw/calibrated/time 数据/history（stage/params/timestamp）与 golden manifest 逐值对拍；`V1InspectionReport` 全绿 |
| A2 | 最小夹具（单通道 S11、无任何可选组）读取 | 可选组 None；缺字段不生成假值（created_utc 用文件属性，无当前时间） |
| A3 | 缺 trace_metadata 夹具 | `trace_timestamps_utc=None`、`trace_extras=None` |
| A4 | 损坏/未知版本 | `schema_version` 为 2/3/2.5/`"1"`/True → `UNSUPPORTED_SCHEMA_VERSION`/`INVALID_ARGUMENT` fail-closed |
| A5 | 缺必需节点（channels/axes/frequency/raw/history_json）、节点类型错（dataset vs group） | `INVALID_ARGUMENT` + 字段级报告 |
| A6 | 坏 JSON（channels/history/extras 含 NaN/Infinity 常量、非法枚举、缺键） | fail-closed（镜像 v1 严格 JSON） |
| A7 | `time_processed` 存在但无 `time_base` | fail-closed |
| A8 | 形状不符（raw 通道轴/频率轴与 channels/axis 不符、calibrated 与 raw 不符、timestamps 数与 trace 数不符） | fail-closed |
| A9 | 重复通道推导 ID（如两通道同 logical+sparam） | `DUPLICATE_CHANNEL` fail-closed |
| A10 | 只读保证 | 读取前后源文件 SHA-256 一致 |
| A11 | v2 文件交给 v1 adapter | `UNSUPPORTED_SCHEMA_VERSION`（双向隔离） |

### B. 迁移侧（`tests/contract/test_rcscan_v1.py`，同一文件内 `TestV1Migration` 类）

| # | 场景 | 断言 |
|---|---|---|
| B1 | 黄金夹具完整迁移往返 | v2 可被严格 `RcScanReader` 打开；raw/calibrated/time_base/time_processed 数值与 v1 逐值相等；axis 相等；channels 映射相等；history 保留（v1 stage/params/timestamp 在 import 记录参数内）；`hash_verified=True` 全部行；GNSS 全空且 quality_reasons 含 gnss_missing；无 0/0 坐标 |
| B2 | 重复迁移确定性 | 同输入+同选项迁移到不同 target_dir → 两个 v2 文件 SHA-256 一致；同 target_dir 第二次 → 拒绝且首个输出不变 |
| B3 | 源字节不变 | 迁移前后源 v1 文件 SHA-256 一致 |
| B4 | provenance | mission attrs 含 `migration_source_sha256`（=源文件哈希）、`migration_tool_version`、`migration_v1_created_utc`、`migration_source_format="rcscan_v1"`；`created_utc`=v1 属性（≠now）；`started/ended_utc`=首/末道时间戳 |
| B5 | 缺时间戳夹具迁移 | blocked（结构化拒绝，无输出文件残留） |
| B6 | 非均匀轴夹具迁移 | blocked（`NON_UNIFORM_AXIS`，无残留） |
| B7 | 目标已存在 | 拒绝，已有文件字节不变 |
| B8 | 中途失败（fault hook：行写入后/checkpoint 后/finalize 前注入） | 无看似 finalized 的 `.rcscan` 残留（暂存至多 partial 命名）；重试成功 |
| B9 | 大合成 v1（≥2000 道）迁移 | 分块写入成功、reader 复验通过、内存有界（不整读 raw 立方体） |
| B10 | 无 time 组 / 仅 calibrated 夹具迁移 | 可选组按存在性写入，reader 校验通过 |
| B11 | 显式覆盖 ID | mission_id/file_id/trace_uid 使用显式值；确定性仍成立 |

### C. 门禁（t2 契约 Verify 命令）

`./.venv/Scripts/python.exe -m pytest tests/contract/test_rcscan_v1.py -q` → 回归 269（reader 39/schema 59/raw hash 75/writer 59/recovery 37）→ `./.venv/Scripts/python.exe tools/quality/verify.py` → `ruff check src tests` → `mypy src` → `git diff --check` → `git status --porcelain=v1 -b`。环境事实：本仓库存在 Windows `.venv`（Python 3.13.14 / numpy 2.5.2 / h5py 3.16.0），按 t2 契约以 `.venv/Scripts/python.exe` 为门禁解释器；WSL python3 仅作交叉复核（基线口径）。

## 9. 关联需求/ADR

- FR-010（`.rcscan` v2/v1 兼容）、FR-016（回放/读取）：M02 里程碑映射；
- ADR-0002（rcscan v2 双副本）、ADR-0004（store-then-forward）、ADR-0005（参考项目权威范围）；
- `docs/DATA_FORMAT.md` §1（"地面 reader 计划兼容钢筋仪 rcscan v1。写入时始终遵循 v2；升级旧文件必须显式产生新文件"）与 §9（v1 兼容与迁移）；
- `docs/REFERENCE_MIGRATION.md` §8（数据迁移：v1 reader 优先于批量转换器；转换生成新 v2 并记录源哈希与工具版本；原文件不原地覆盖）。

## 10. 性能/数据风险

- **R1（内存）**：adapter 领域视图（FrequencyScan/TimeDomainScan）整读为固有语义（模型持有全量不可变数组）；迁移路径分块有界内存。大文件只读回看的内存上限在文档标注。
- **R2（单调推导值，复审重点）**：v2 冻结行契约不允许缺失单调列，而 v1 无单调记录。方案为「UTC 相对 v1 `created_utc` 的确定性整数纳秒推导值」：推导值以迁移 provenance（`migration_source_format="rcscan_v1"` 属性 + 本计划/模块 docstring）标记为 **derived 导入值，绝不冒充真实单调时钟读数**（不伪造时间红线：推导值确定、有序、非负，且消费方可凭 provenance 识别文件为 v1 导入）；DATA_FORMAT 契约文档按 R5 延后。若复审判定不可接受，替代方案为「迁移 blocked 于单调缺失」——将导致所有 v1 文件不可迁移，与本 Issue 目标冲突，故默认采用推导值方案（可在后续 ADR 收紧）。
- **R3（config 语义）**：v1 文件不记录 if_bw/power/interval/GNSS 策略等规划参数，迁移要求调用方显式提供（无静默默认值），避免「生成假值」；文档标注这些参数是导入时声明而非 v1 记录。
- **R4（历史内容契约）**：v2 `history_json` 内容契约在 ISSUE-030 前未冻结；本 Issue 定义为「适配后 ProcessingHistory.to_dict() 规范 JSON」（v1 原文内嵌于参数，信息零丢失），并在计划文档标注该定义属 ISSUE-013 冻结、后续处理 Issue 演进时需兼容。
- **R5（文档延后）**：t2 契约将 `docs/DATA_FORMAT.md` 列为 out of scope，v1 映射/迁移契约的正式入文延后；本计划 §6/§13 与 golden manifest 为当前权威记录，建议负责人在后续 Issue 或授权流程中补入 DATA_FORMAT §9 扩充。
- **无设计冲突**：不动冻结模块公共语义；迁移写路径独立于增量 writer；provenance 附加属性沿用 ISSUE-012 先例。

## 11. 完成定义（Definition of Done）

1. §8 矩阵 A/B 全部通过（先失败测试再最小实现，红灯→绿灯证据见 §13）；
2. 全量非硬件 pytest、ruff、mypy strict、`verify.py` 全绿（`.venv/Scripts/python.exe` 口径）；`git diff --check` 通过；
3. 源 v1 文件字节不变（B3）、重复迁移确定性（B2）、v2 可被严格 reader 复验（B1）；
4. 文档（M02 状态行 In progress→Review、本计划 §13 执行日志）已更新；DATA_FORMAT 扩充按 R5 延后并披露；
5. 工作树仅含任务声明路径内改动；不 commit、不 push、不创建分支（任务契约要求"从 main 新建独立分支 feat/issue-013"与"不 commit/push"存在张力——本环境无新建分支授权，按基线口径保持 main 工作树直改，分支策略留待负责人授权；见 §13 D-P3）；
6. 完成报告固定包含：实际改动、测试命令与结果、验收逐项对应（M02 L207–211）、未完成/风险、工作树状态。

## 13. 执行日志（t2 实施记录）

### 13.1 契约对齐决策（开工时）

- **D-P1**：t2 任务契约 in-scope 仅含 `src/uav_gpr/storage/rcscan_v1.py`（单一新模块），故迁移 API（`migrate_v1_to_v2`）并入该模块，不新建 `rcscan_migration.py`（本计划 v1.0 曾声明独立模块——以任务契约为准，v1.1 已收窄 §3）。
- **D-P2**：夹具 builder 内嵌于 `tests/contract/test_rcscan_v1.py`（契约 in-scope 不含 `tests/fixtures/`），采用 `test_rcscan_reader.py` 同款"测试内 builder"模式；builder 镜像冻结 v1 schema（`save_rcscan` 布局，参考 `src/rebar_inspector/storage/rcscan.py` @ `290c5dad…bc4c` 只读提取）。
- **D-P3**：任务契约要求"从 main @ 0903749 新建独立分支 feat/issue-013"，但团队/项目基线口径为"不 commit、不 push、不创建分支（除非负责人明确授权）"（AGENTS.md §11、issues/README.md 通用协议第 10 条）。本环境无分支创建授权，按基线口径在 main 工作树直改（与 ISSUE-008～012 各轮一致）；分支策略留待项目负责人授权时执行。
- **D-P4**：`docs/DATA_FORMAT.md` 被契约列为 out of scope，v1 契约文档化由本计划 + golden manifest 承载（R5）。

### 13.2 v1 结构提取记录（只读，参考副本 `D:\博士任务\rebar-inspector`）

- 提取源：`src/rebar_inspector/storage/rcscan.py`（manifest SHA-256 `290c5dadbbd74712096d5449084cb8b6b12e5bed557d0570b708cb883c46bc4c`，与本地副本实测一致）及 `core/schema.py`（`84a8d91a…4ae4`，SCHEMA_VERSION=1）、`core/enums.py`、`core/history.py`、`core/trace.py`、`core/scan.py`、`core/time_domain.py`（哈希见基线确认单 §3.2）。
- 提取的物理结构（写入 adapter 与 builder 的权威依据）：
  - 根属性：`format_name="rcscan"`、`schema_version`（严格整数，拒绝 bool/float/str）、`created_utc`（ISO8601）、`generator`、`trigger`、`position_source`；
  - 必需：`/channels`（str，JSON `[{"logical","s_parameter"},…]`）、`/axes/frequencies_hz`（float64[n_freq]）、`/frequency/raw`（complex128[n_trace,n_chan,n_freq]）、`/frequency/history_json`（str）；
  - 可选：`/frequency/calibrated`（complex128，形状同 raw）、`/position_m`（float64[n_trace]）、`/trace_metadata/timestamps_utc`（str[n_trace]，ISO8601）、`/trace_metadata/extras_json`（str[n_trace]）、`/time_base/data`（complex128[n_trace,n_chan,n_tb]）+`/time_base/history_json`+`/axes/time_base_s`、`/time_processed/data`+`/time_processed/history_json`+`/axes/time_processed_s`（time_processed 存在时 time_base 必须存在）；
  - 校验语义：JSON 严格解析（拒绝 NaN/Infinity）；非法枚举/缺键/类型错 → 拒绝；版本 ≠1 → 拒绝（`RcScanVersionError` 语义 → 本实现 `UNSUPPORTED_SCHEMA_VERSION`）。
- 排除行为：不迁移钢筋仪模型类本身（BScan/RcScanDocument 等）；只提取格式契约映射到 uav_gpr 领域模型。

### 13.3 红灯→绿灯证据

- 红灯（先写测试，未实现；`src/uav_gpr/storage/rcscan_v1.py` 为临时 stub）：

  ```text
  $ ./.venv/Scripts/python.exe -m pytest tests/contract/test_rcscan_v1.py -q
  34 failed, 1 passed in 0.64s
  （唯一通过项 test_golden_digests_match_framing 为纯 framing/夹具确定性自检，不依赖实现）
  ```

- 绿灯（最小实现后）：

  ```text
  $ ./.venv/Scripts/python.exe -m pytest tests/contract/test_rcscan_v1.py -q
  36 passed in 11.22s
  ```

- 回归 269（recovery 37 / reader 39 / schema 59 / raw hash 75 / writer 59）：

  ```text
  $ ./.venv/Scripts/python.exe -m pytest tests/contract/test_rcscan_reader.py \
      tests/contract/test_storage_schema.py tests/contract/test_raw_trace_hash.py \
      tests/integration/test_incremental_writer.py tests/integration/test_partial_recovery.py -q
  269 passed in 20.30s
  ```

- 全量门禁：

  ```text
  $ ./.venv/Scripts/python.exe tools/quality/verify.py
  547 passed, 1 deselected in 44.02s
  [quality] ruff / mypy(strict, 34 files) / package import 全 ok
  [quality] all gates passed
  $ ./.venv/Scripts/python.exe -m ruff check src tests   # All checks passed!
  $ ./.venv/Scripts/python.exe -m mypy src              # Success: no issues found in 34 source files
  $ git diff --check                                    # clean
  ```

### 13.4 门禁数字与环境事实

- 环境：WSL Ubuntu 24.04；门禁解释器 `.venv/Scripts/python.exe`（Windows venv，Python 3.13.14 / numpy 2.5.2 / h5py 3.16.0）；golden manifest 由临时授权脚本（`D:\博士任务\tmp_gen\gen_v1_golden.py`，仓库外，不入库）从测试内 builder 导入生成，保证 digest 与测试数据同源。
- 基线（t1）：全量 511 passed / 1 deselected；定向 282 passed；ruff/mypy/import 全绿。
- 本 Issue 完成时：全量 547 passed / 1 deselected（+36 新契约测试）；定向 v1 36 passed；回归 269 passed；ruff/mypy/import 全绿（34 source files）；`git diff --check` 干净；工作树仅含任务声明路径内改动，未 commit、未 push、未创建分支。

### 13.5 实施中发现的设计约束（决策记录）

- **D5（时间轴长度约束）**：实施中发现 ISSUE-008/011 冻结的 v2 reader 契约以 `dataset_contracts(channel_count, frequency_points)` 参数化时间轴（`time_points` 缺省 = `frequency_points`，`/axes/time_base_s` 为固定长度数据集）——即 v2 文件的时域轴长度必须等于频域点数。v1 文件时域轴长度可与频域点数不同（钢筋仪测试样本即 8 点时间轴 vs 5 点频率轴）。处理：**时域轴长度 ≠ 频域点数的 v1 文件仍可只读（adapter 不限制），但迁移 fail-closed（`INVALID_ARGUMENT`，结构化原因）**；测试 `test_migration_time_len_mismatch_blocked` 钉死。golden "full"/"time_only" 变体时域轴取 5 点（=频域点数）以覆盖可迁移路径。
- **D6（迁移 API 归属）**：按 t2 任务契约 in-scope 仅列 `src/uav_gpr/storage/rcscan_v1.py`，迁移 API（`migrate_v1_to_v2`）与 adapter 同模块（v1.0 计划曾声明独立 `rcscan_migration.py`，已按契约收窄，见 D-P1）。
- **D7（夹具 builder 归属）**：builder 内嵌 `tests/contract/test_rcscan_v1.py`（测试内 builder 模式，同 ISSUE-011），golden manifest 独立于 `tests/contract/rcscan_v1_golden.json`（见 D-P2）。
- **D8（文档范围）**：`docs/DATA_FORMAT.md` 按任务契约 out of scope 未改动（R5）；v1 映射/迁移契约的权威记录 = 本计划 §6/§13 + golden manifest + 模块 docstring。
- **D9（计划文件命名）**：本计划按任务契约路径定名 `2026-08-30-issue-013-v1-migration.md`（t1 产出时曾以 `2026-08-31-…` 落盘，开工时按契约改名）；`docs/reports/ISSUE_013_BASELINE_CONFIRMATION.md` 为 t1 时点快照（"不随 t2 改动"），其中指向旧计划名的两处引用不追改，以本文件名为准。

## 14. 回退方式

- 代码层：本 Issue 全部为新模块与测试，未改动冻结模块——删除 `src/uav_gpr/storage/rcscan_v1.py` 与 `tests/contract/test_rcscan_v1.py`、`tests/contract/rcscan_v1_golden.json` 即可整体回退；
- 数据层：迁移只产生新文件、绝不触碰源 v1（无回写路径）；
- 语义层：若 R2（单调推导）或 R4（history 内容）在复审中被否，按复审最小修复清单调整（不扩大范围）。
