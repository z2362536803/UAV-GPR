# ISSUE-013 开工基线确认单（只读核查 + 实施计划）

日期：2026-08-30（ISSUE-013 开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-013-v1-migration`（执行器 engineer，任务 t1）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支、未修改实现与测试。
配套文件：本单为 t2（实现 v1 只读 adapter 与显式 v1→v2 迁移）与 t3（独立复审）的权威基线件；独立复审按 [ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) 执行。t2 完整实施契约见 [docs/plans/2026-08-31-issue-013-v1-migration.md](../plans/2026-08-31-issue-013-v1-migration.md)。

## 1. 锁定的目标 Issue 与依据

**ISSUE-013：`.rcscan` v1 兼容读取与显式迁移**（`docs/issues/M02_STORAGE.md` 第 6 个条目，状态 `Planned`）

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-013（M02_STORAGE.md L190–225） | docs/issues/README.md 依赖顺序主表 L80 |
| 直接依赖 | ISSUE-001（参考基线 manifest）、ISSUE-011（reader/严格校验/逻辑排序）、ISSUE-012（partial 检查与非破坏恢复） | M02_STORAGE.md L193「直接依赖：ISSUE-001、011、012」 |
| 依赖状态 | 三者均已合入 `main`（见第 3 节 Git 与报告证据；ISSUE-001 状态行在 M01 文档仍标 `Planned`，属文档滞后，见 3.5） | M02 L118/L155；ISSUE_001_005_REVIEW_SUMMARY.md；git log |
| 提示词工作区替换 | `E:\wurenjiruanjian` → 工作区根（见第 2 节） | docs/issues/README.md 第 3 节通用协议 |
| 一次一 Issue | 本轮只处理 ISSUE-013；不进入 ISSUE-014 | docs/issues/README.md 第 1/3 节 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

两个参考项目 `E:\钢筋仪软件开发`、`E:\UVA_GPR_system` 的原始路径**不在本机挂载范围内**（WSL 仅挂载 C/D 盘）；本 Issue 的参考源可用性结论见 3.2（内容哈希可移植事实已在本机副本上全部复现）。

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      main（当前；工作树干净）
HEAD        0903749  docs(data-format): freeze ISSUE-012 non-destructive recovery contract (4.1)
相对远端    ahead 1（docs/data-format freeze 提交未推送）
git status --porcelain=v1 -b   只有 "## main...origin/main [ahead 1]" 一行
```

依赖合并提交（`git log` / `git show --stat` 实测）：

| 提交 | 日期 | 内容 |
|---|---|---|
| `2c5b379` | 2026-08-21 | `chore: freeze reference project baselines`——ISSUE-001 原始交付：`docs/reference-baselines/{README.md,spec.json,manifest.json,manifest.md}` + `tools/migration/reference_manifest.py` + `tests/unit/test_reference_manifest.py` |
| `097a20e` | 2026-08-30 | `feat(storage): read-only rcscan reader, strict validation and logical ordering (ISSUE-011)`（feature 提交） |
| `57c4966` | 2026-08-30 | `Merge feat/issue-011: ...`——ISSUE-011 合入 main（`src/uav_gpr/storage/rcscan_reader.py` 1070 行、`tests/contract/test_rcscan_reader.py` 1325 行） |
| `db95817` | 2026-08-30 | `docs(issues): mark ISSUE-011 Done after authorized merge` |
| `0bde602` | 2026-08-30 | `feat(storage): partial inspect and non-destructive recovery (ISSUE-012)`（feature 提交） |
| `24d3505` | 2026-08-30 | `Merge feat/issue-012: ...`——ISSUE-012 合入 main（`src/uav_gpr/storage/partial_recovery.py` 775 行、`tests/integration/test_partial_recovery.py`、基线/计划/复审报告） |
| `59c4dda` | 2026-08-30 | `docs(issues): mark ISSUE-012 Done after authorized merge` |
| `0903749` | 2026-08-30 | `docs(data-format): freeze ISSUE-012 non-destructive recovery contract (4.1)`（当前 HEAD） |

合并历史：`2c5b379`（ISSUE-001 部分）→ 008/009/010/011/012 链（`e852508`→`c10693f`→`4ec7d0e`→`57c4966`→`24d3505`）；无 reset/rebase/强推迹象（本次未做历史改写）。`git ls-files` 确认 ISSUE-001/011/012 交付物全部 tracked 于 main：`docs/reference-baselines/manifest.json`、`tools/migration/reference_manifest.py`、`tests/unit/test_reference_manifest.py`、`src/uav_gpr/storage/rcscan_reader.py`、`src/uav_gpr/storage/partial_recovery.py`、`tests/contract/test_rcscan_reader.py`、`tests/integration/test_partial_recovery.py`。

### 3.2 ISSUE-001 manifest 冻结哈希与参考源本地可用性（本 Issue 核心核查）

**结论：钢筋仪 rcscan v1 的全部相关源文件已在本地可只读访问的副本上完成 SHA-256 对拍，16/16 与 ISSUE-001 manifest 冻结哈希完全一致。**

- ISSUE-001 manifest（`docs/reference-baselines/manifest.{json,md}`，schema 1.0，工具 `uav-gpr-reference-manifest` v1.0.0）冻结的钢筋仪仓库事实：路径 `E:\钢筋仪软件开发`、branch `feat/issue-16-pause-resume`、HEAD `938875234a99b47d78cfec940671005b63e9d15c`、worktree dirty。
- 原始 E 盘路径本环境不可达（`/mnt/e` 不存在）。本地存在只读副本 **`D:\博士任务\rebar-inspector`**（WSL 视角 `/mnt/d/博士任务/rebar-inspector`）：
  - 来源：GitHub 克隆 `z2362536803/rebar-inspector`（下载脚本 `D:\博士任务\download-rebar-inspector.ps1` 记录 provenance：git clone，fallback ZIP）；
  - 该副本自身 git 状态：branch `main`、HEAD `7c522d2aebe6a835acb969e8012565715f64a238`、worktree dirty（与 manifest 冻结的 branch/HEAD 不同——**内容哈希才是可移植事实**，见 reference-baselines/README.md）；
  - 对拍方式：对 16 个 v1 相关候选文件计算 SHA-256，与 manifest.json 逐项比对。

对拍结果（`sha256sum` 实测，全部 `committed` 状态）：

| 角色 | 文件 | manifest 冻结 SHA256 | 本地实测 |
|---|---|---|---|
| storage | `src/rebar_inspector/storage/rcscan.py` | `290c5dad…bc4c` | ✅ 一致（v1 schema/reader 权威源） |
| storage | `src/rebar_inspector/storage/document.py` | `a173d3ad…57da` | ✅ 一致（RcScanDocument 聚合） |
| storage | `src/rebar_inspector/storage/errors.py` | `abc52ea0…86ec` | ✅ 一致（RcScanError 层级） |
| storage | `src/rebar_inspector/storage/__init__.py` | `dbd4d071…42cc` | ✅ 一致 |
| core | `src/rebar_inspector/core/schema.py` | `84a8d91a…4ae4` | ✅ 一致（SCHEMA_VERSION=1） |
| core | `src/rebar_inspector/core/scan.py` | `3f608405…3c03` | ✅ 一致（BScan） |
| core | `src/rebar_inspector/core/frequency.py` | `8164a641…5bad` | ✅ 一致（FrequencySweep） |
| core | `src/rebar_inspector/core/time_domain.py` | `882a2911…4597` | ✅ 一致（TimeDomainScan） |
| core | `src/rebar_inspector/core/history.py` | `077c8b29…69ba` | ✅ 一致（ProcessingHistory） |
| core | `src/rebar_inspector/core/trace.py` | `a9f7ed31…edf2` | ✅ 一致（TraceMetadata） |
| core | `src/rebar_inspector/core/channels.py` | `cf0fb505…9340` | ✅ 一致（ChannelSpec） |
| core | `src/rebar_inspector/core/enums.py` | `08129eb7…6b19` | ✅ 一致（SParameter/LogicalChannel/TriggerMode/PositionSource/TimeDomainKind） |
| core | `src/rebar_inspector/core/_scan_common.py` | `b11b2139…5a1ca` | ✅ 一致 |
| core | `src/rebar_inspector/core/_arrays.py` | `db69a18b…90d` | ✅ 一致 |
| core | `src/rebar_inspector/core/_frozen.py` | `4893ef26…1bf` | ✅ 一致 |
| core | `src/rebar_inspector/core/_serializable.py` | `6f4fd12d…5f03` | ✅ 一致 |

**v1 schema 事实（从冻结源 `rcscan.py` 逐行提取，供 t2 契约使用）**：`format_name="rcscan"`、`schema_version=1`（严格整数，拒绝 bool/float/str）；必需节点 `/channels`（JSON：`[{"logical","s_parameter"},...]`）、`/axes/frequencies_hz`（float64）、`/frequency/raw`（complex128，trace×channel×freq）、`/frequency/history_json`；可选节点 `/frequency/calibrated`、`/position_m`、`/trace_metadata/{timestamps_utc,extras_json}`、`/time_base/{data,history_json}` + `/axes/time_base_s`、`/time_processed/{data,history_json}` + `/axes/time_processed_s`（time_processed 存在时 time_base 必须存在）；根属性 `created_utc`（ISO8601）、`generator`、`trigger`、`position_source`。JSON 严格解析（拒绝 NaN/Infinity）；非法枚举/JSON/节点类型抛 `RcScanFormatError`；版本高于 1 抛 `RcScanVersionError`。参考仓库只读使用，绝不修改/复制（匿名夹具除外，见 plans §5）。

**无真实 v1 现场文件可用**：manifest 明确排除根目录实测数据（`*.rcscan/*.rcal/*.rcbg/*.npz/*.csv`）；本地副本无任何 `*.rcscan` 样本。因此匿名黄金夹具为**合成构建、结构忠实于冻结 v1 schema**（fixture builder 按 `rcscan.py` 冻结布局镜像 `save_rcscan` 写出，内容全部合成、无现场隐私），并在 plans §5 记录结构对拍方式。

### 3.3 ISSUE-011/012 依赖证据（实际代码与测试）

| 依赖 | 交付物（main 内） | ISSUE-013 复用点 |
|---|---|---|
| ISSUE-001 参考基线 | `docs/reference-baselines/manifest.json`（16 个 v1 文件冻结哈希，见 3.2） | v1 schema/reader 来源与哈希权威；迁移必须按 manifest 冻结哈希记录参考源 |
| ISSUE-011 只读 reader/validator | `src/uav_gpr/storage/rcscan_reader.py` | `RcScanReader`/`RcScanValidator`（v2 严格打开校验/可见窗口/双视图/报告）作为**迁移产物的发布前验证器**（迁移写出的 v2 必须能被严格 reader 读取）；`ValidationReport` 语义参照 |
| ISSUE-012 非破坏恢复 | `src/uav_gpr/storage/partial_recovery.py` | 迁移写文件的模式参照：`create_rcscan_v2` 建骨架 → 直写数据 → 暂存名 `*.partial.rcscan` → 严格 reader 复验 → 原子改名 → 失败清理；provenance 附加属性（对旧 reader 透明）先例；恢复工具版本常量先例 |

ISSUE-011 复审移交事项（`docs/reports/ISSUE_011_REVIEW_REPORT.md`）：P3-1（reader 内部镜像校验）在 ISSUE-012 已决策不抽公共函数——**ISSUE-013 沿用该决策**：不修改 `rcscan_v2.py`/`rcscan_reader.py`/`incremental_writer.py` 的既有公共语义；v1 适配与迁移写入为独立新模块。

### 3.4 审查报告与授权证据

- `docs/reports/ISSUE_001_005_REVIEW_SUMMARY.md`：ISSUE-001～005 批次 `PASS`，已合入 `main` 并推送（ISSUE-001 原始提交 `2c5b379`、复审修复 `6b74dcf`）；
- `docs/reports/ISSUE_011_REVIEW_REPORT.md`：PASS WITH CONDITIONS（无 P0/P1/P2，3 项 P3 移交）→ 授权合并（M02 L118）；
- `docs/reports/ISSUE_012_REVIEW_REPORT.md`：PASS WITH CONDITIONS → 授权合并（M02 L155）；
- M02 状态行实测：ISSUE-011 `Done`（L118）、ISSUE-012 `Done`（L155）、ISSUE-013 `Planned`（L192）。

### 3.5 发现的事实（非阻塞）

- **M01 文档状态滞后**：`docs/issues/M01_GOVERNANCE_CORE.md` 中 ISSUE-001 状态行仍为 `Planned`，但 ISSUE-001 已由审查总结证明 PASS 并合入 main（`2c5b379`/`6b74dcf`）。属 M01 里程碑文档未随批次更新（ISSUE-006/007 批次同样未回填），不影响 ISSUE-013 依赖判定（以合并提交与审查总结为权威），建议负责人择机回填。
- 本环境无真实 v1 数据文件；黄金夹具为合成结构夹具（见 3.2），"真实结构"由冻结源 `rcscan.py`（SHA-256 钉死）与参照其 storage 测试构建方式保证。

## 4. 门禁基线（核查时实测复跑）

环境：WSL Ubuntu 24.04 / Python 3.12.3；numpy 2.5.2、h5py 3.16.0、pytest、ruff、mypy；`uav_gpr` editable 可导入（`src/`）。

```text
$ python3 tools/quality/verify.py
511 passed, 1 deselected in 18.68s
[quality] ruff      ok   All checks passed!（33 source files）
[quality] mypy      ok   Success: no issues found in 33 source files
[quality] import    ok   package import ok
[quality] all gates passed

$ python3 -m pytest tests/unit/test_reference_manifest.py \
    tests/contract/test_storage_schema.py tests/contract/test_raw_trace_hash.py \
    tests/integration/test_incremental_writer.py tests/contract/test_rcscan_reader.py \
    tests/integration/test_partial_recovery.py -q
282 passed in 15.29s
```

核查后 `git status` 与核查前一致（仅 `## main...origin/main [ahead 1]`），无缓存/日志/实测数据残留（`git check-ignore` 确认 `.agent-teams/`、`*.rcscan`、`*.partial.rcscan` 已忽略）。

## 5. ISSUE-013 实施计划摘要（t2 执行契约，完整版见 plans 文档）

1. **新模块 `src/uav_gpr/storage/rcscan_v1.py`**：v1 只读 adapter。
   - `RcScanV1Reader`：打开即校验（fail-closed）——`format_name="rcscan"` 且 `schema_version` 为**真正整数 1**（bool/float/str 拒绝；`1` 之外版本抛 `DomainError(UNSUPPORTED_SCHEMA_VERSION)`）；必需节点缺失/类型错误/严格 JSON 失败 → `DomainError(INVALID_ARGUMENT)`（结构化、带字段）；`time_processed` 无 `time_base` → 拒绝（镜像 v1 自身规则）；字段级 `V1InspectionReport`（可序列化：每个节点的存在性/类型/长度/解码状态 + 源文件 SHA256），损坏/不支持 v1 的"字段级报告"由报告 + fail-closed 错误共同承载。
   - 映射到新领域模型（`V1RcScanData` 聚合容器）：channels → `ChannelSpec`（`channel_id = f"{logical.value.lower()}_{s_parameter.value.lower()}"`，如 `hh_s11`/`vv_s22`，确定性推导；`display_name` 非空；重复推导 ID → `DUPLICATE_CHANNEL` fail-closed）；`frequencies_hz`/`frequency_raw` → `FrequencyScan(channels, axis, data, metadata=())`；`frequency_calibrated` → 原样数组（形状须与 raw 一致）；`time_base`/`time_processed` → `TimeDomainScan`（kind 对应；history 为合成 `ProcessingRecord`：`v1_import_time_base`（raw→time_base）/`v1_import_time_processed`（time_base→time_processed），参数内嵌 v1 逐条 stage/params/timestamp 与 v1 history_json 原文，`executed_utc` 取 v1 `created_utc` 属性——**绝不取当前时间**）；trace 时间戳 → `tuple[datetime,...] | None`（v1 ISO 字符串严格解析，tz-aware）；`position_m`/`position_source`/`trigger`/`generator`/`created_utc` → 原样暴露（缺失/损坏 → 报告 + 相应 fail-closed 语义）。
   - 只读保证：`"r"` 打开，读取前后源文件字节不变（测试以 SHA-256 钉死）。
2. **新模块 `src/uav_gpr/storage/rcscan_migration.py`**：显式 v1→v2 迁移 API。
   - `migrate_v1_to_v2(source, target_dir, *, if_bw_hz, power_dbm, target_interval_s, gnss_max_age_s, software_version, mission_id=None, file_id=None, device_id=None, role=GROUND, created_utc=None, note=None, clock=None, fault_hook=None, filesystem=None)`：**确定性默认**——`mission_id`/`file_id`(GroundFileId)/`device_id`/逐道 `trace_uid` 均以 uuid5（命名空间 `uav-gpr-v1-migration`，名称含源文件 SHA-256）推导，可显式覆盖；重复迁移同输入+同选项 → 输出逐字节一致；目标已存在 → 拒绝（绝不覆盖）。
   - 写路径（ISSUE-012 模式）：`create_rcscan_v2` 建骨架（writing）→ 逐道经权威 codec `trace_metadata_to_cells` 写行 + raw 切片（分块、有界内存）→ 写可选组（`/frequency/calibrated`、`/axes/time_base_s`、`/time_base/data`+`history_json`、`/axes/time_processed_s`、`/time_processed/data`+`history_json`；history_json 内容 = 适配后 `ProcessingHistory.to_dict()` 规范 JSON，v1 原文内嵌于 import 记录参数）→ 写 checkpoint → 置 `ended_utc`（= 末道时间戳，源自文件数据）/`completion_kind=completed`/`lifecycle_state=finalized` → mission attrs 附加迁移 provenance（`migration_source_sha256`、`migration_tool_version`、`migration_v1_created_utc`、`migration_source_format="rcscan_v1"`，对旧 reader 透明）→ 暂存名 `*.partial.rcscan` 经严格 `RcScanReader` 复验 → 原子改名 `<file_id>.rcscan`；任一步失败 → 关闭句柄、best-effort 删除暂存、抛错（绝不留下看似 finalized 的结果）。
   - 迁移逐道语义（诚实映射，禁止伪造）：GNSS 全部为空（`gnss_match=None`、`valid=0`，`quality_status=degraded` + `quality_reasons=(gnss_missing,)`——与 TraceMetadata fail-closed 规则一致，无 0/0 坐标）；逐道 UTC = v1 `timestamps_utc`（started=midpoint=finished=该时间戳，v1 单时间戳语义）；单调纳秒 = **由 UTC 相对 v1 `created_utc` 确定性推导**（v1 无单调时钟记录，v2 冻结 schema 又不允许缺失——推导值在文档中显式标注为导入推导值而非硬件单调读数，见 plans §6 风险与决策记录，供复审重点审查）；`target_interval_s` 取显式参数；`actual_interval_s`/`schedule_error_s` = 相邻时间戳差分/差分−目标（首道 None，合法）；`connection_generation=0`（v1 无此概念，文档标注）；逐道 `raw_trace_sha256` 按 ISSUE-009 framing 对新 mission/uid/index 现算（迁移后 `hash_verified=True`）。
   - 迁移 blocked（结构化拒绝，fail-closed）：v1 缺逐道时间戳（v2 行契约无法诚实表达无 UTC 记录）；频率轴非均匀/负起点（`MissionConfig` 无法无伪造表示，`NON_UNIFORM_AXIS`）；v1 结构损坏/未知版本（读侧已拒绝）。
3. **匿名黄金夹具**：`tests/fixtures/rcscan_v1_builder.py`（按冻结 v1 schema 镜像 `save_rcscan` 布局的合成 builder）+ `tests/contract/rcscan_v1_golden.json`（黄金 manifest：文件结构、映射期望、S11 单通道与 HH/VV 双通道等变体）+ 变体（含 calibrated、含 time_base+time_processed、无 trace_metadata、无 position、损坏/未知版本/坏 JSON/形状不符等负面夹具）。全部合成、无现场隐私。
4. **失败测试优先**：`tests/contract/test_rcscan_v1_reader.py` + `tests/integration/test_rcscan_v1_migration.py`，必测矩阵见 plans §7（对应提示词验收：真实结构夹具可读、数值/axis/channel/history 往返对拍、损坏/未知 v1 字段级报告、源字节不变、重复迁移确定性、v2 可被严格 reader 读取、迁移后无伪造 UTC/GNSS）。
5. **文档**：`docs/DATA_FORMAT.md` 第 9 节扩充「v1 兼容读取与显式迁移（ISSUE-013 冻结）」子节（v1 映射契约、迁移写路径、provenance attrs、blocked 语义、单调推导值标注）；`docs/issues/M02_STORAGE.md` ISSUE-013 状态行 `Planned → In progress`（完成后由人工置 Review/Done）；本计划文档。
6. **门禁复跑**：定向新测试 + 全量非硬件 pytest + Ruff + mypy strict + `verify.py` + 工作树/diff 检查；不 commit、不 push。

### 排除项（out of scope，与 M02 L208–209 一致）

不导入旧 UAV-GPR CSV/NPZ；不原地升级/改写 v1 源文件；不伪造 UTC/GNSS/位置（缺字段保持 None/空，推导值显式标注）；不改 `rcscan_v2.py`/`raw_hash.py`/`incremental_writer.py`/`rcscan_reader.py` 的既有公共语义（不抽公共函数、不扩展 writer）；不改两个参考项目；不做 GUI；不 commit、不 push、不创建/切换分支；不进入 ISSUE-014。

### 验收标准（M02_STORAGE.md L207–211 原文，t2 不得削弱）

1. 真实结构的匿名 v1 fixture 可读取；往返迁移保持数值/axis/channel/history；
2. 缺字段不生成当前时间或 0 坐标；
3. 源 v1 文件不变，v2 明确记录 migration provenance。

## 6. 结论

ISSUE-013 开工基线已锁定：`main` @ `0903749`（工作树干净，ahead 1）；三项依赖（ISSUE-001/011/012）的代码、契约测试、独立审查报告与授权合并证据全部实测复现；**ISSUE-001 manifest 冻结的 16 个钢筋仪 rcscan v1 源文件哈希全部在本机只读副本上对拍一致**（原始 E 盘不可达，以内容哈希为可移植事实）；门禁基线 511 passed/1 deselected、ruff/mypy/import 全绿、定向依赖测试 282 passed。本确认单即为 t2 实现与 t3 独立复审的权威基线件；t2 按 plans 文档契约执行（先失败测试→最小实现→门禁→报告），完成后停止，不进入 ISSUE-014。

> 后续记录：本单为开工时点的基线快照，不随 t2 改动；t2 实际完成记录见其完成报告与 `docs/plans/2026-08-31-issue-013-v1-migration.md`，t3 复审报告独立输出。
