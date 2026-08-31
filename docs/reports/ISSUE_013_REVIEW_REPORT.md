# ISSUE-013 独立复审报告（round 1）

日期：2026-08-30
审查者：DeepSeek Harness AgentTeams `uav-gpr-issue-013-v1-migration`（reviewer，任务 t3，全程只读）
审查对象：ISSUE-013「.rcscan v1 兼容读取与显式迁移」任务 t2 交付（`src/uav_gpr/storage/rcscan_v1.py`、`tests/contract/test_rcscan_v1.py`、`tests/contract/rcscan_v1_golden.json`、`docs/issues/M02_STORAGE.md` 状态行、`docs/plans/2026-08-30-issue-013-v1-migration.md`）
审查依据：`docs/ISSUE_REVIEW_STANDARD.md` v1.0、`AGENTS.md`、`CONTRIBUTING.md`、`docs/INDEX.md`、`docs/issues/README.md`（第 2/3 节）、`docs/issues/M02_STORAGE.md`（ISSUE-013 条目 L190–225）、`docs/DATA_FORMAT.md`（§1/2/3.1/4.1/9）、`docs/REFERENCE_MIGRATION.md`、`docs/adr/0005-reference-authority.md`、`docs/reference-baselines/manifest.md`、`docs/TESTING.md`、`docs/reports/ISSUE_013_BASELINE_CONFIRMATION.md`（t1）、`docs/plans/2026-08-30-issue-013-v1-migration.md`（t2 权威执行契约 + §13 执行日志）；衔接件：`docs/reports/ISSUE_011_REVIEW_REPORT.md`（严格 reader 口径）、`docs/reports/ISSUE_012_REVIEW_REPORT.md`（provenance attrs 模式）。

## 1. 审查结论

**PASS WITH CONDITIONS**

M02 ISSUE-013 全部 4 条验收口径（含范围/排除项）逐条独立复验 **PASS**；门禁全量复现（定向 36、回归 269、全量 547 passed + 1 deselected、ruff/mypy/import 全绿、`git diff --check` 干净）；3 项独立变异探针（伪造缺失时间戳、删除 provenance attrs、迁移期间改写源 v1 文件）全部被定向测试真实杀死；参考源合规（冻结哈希 18/18 对拍一致、无代码搬运）；无 P0/P1/P2 问题。剩余 6 项 P3 为文档性/低风险观察（见第 3 节与第 10 节），不阻止合并；剩余风险（单调推导值语义、DATA_FORMAT §9 入文延后等）留项目负责人合并时决定（第 8 节）。

## 2. 自动识别的审查范围

从 t2 契约/计划与仓库事实自动识别并逐项对拍：

| 项 | 计划/契约声称 | Git/仓库实测 | 一致 |
|---|---|---|---|
| 分支 | 任务契约要求 `feat/issue-013` 自 `main @ 0903749`；计划 D-P3 曾声明"不创建分支、main 直改" | 审查开始时工作树在 `main`（`## main...origin/main [ahead 1]`）；审查期间 reflog 新增唯一条目 `0903749 HEAD@{0}: checkout: moving from main to feat/issue-013`；现当前分支 = `feat/issue-013`，0 提交，基线 = `main` = `0903749` | ✔（终态符合契约；过程见第 3 节 P3-5） |
| 提交 | 0 commit/push/merge | `git log main..feat/issue-013` = 0 提交；reflog 无 reset/rebase/amend/force | ✔ |
| 改动文件 | 计划 §7：`rcscan_v1.py`（adapter+迁移 API 同模块）、`test_rcscan_v1.py`（builder 内嵌）、`rcscan_v1_golden.json`、M02 状态行、计划 §13 | `git status --porcelain`：` M docs/issues/M02_STORAGE.md`（仅状态行 Planned→Review，diff 1 行）；未跟踪 5 件：`docs/plans/2026-08-30-issue-013-v1-migration.md`（264 行）、`docs/reports/ISSUE_013_BASELINE_CONFIRMATION.md`（t1）、`src/uav_gpr/storage/rcscan_v1.py`（1431 行）、`tests/contract/rcscan_v1_golden.json`（499 行）、`tests/contract/test_rcscan_v1.py`（1164 行） | ✔ |
| 范围外零改动 | 不改 rcscan_v2/incremental_writer/rcscan_reader/partial_recovery/core/**/DATA_FORMAT/adr/tools | `git status` 仅上述 6 项；`git diff --check` 干净；被依赖模块内容哈希无变化 | ✔ |
| 测试数字 | 36 / 269（37+39+59+75+59）/ 547+1 deselected / ruff / mypy(34) / diff-check | 全部复现（见第 6 节） | ✔ |
| 状态行 | Planned → Review | M02 L192 `Review（2026-08-30 实现与测试完成，等待独立复审；仅项目负责人人工验收后置 Done）`，与 `docs/issues/README.md` §2 `Review` 定义一致 | ✔ |

审查范围 = 上述 6 个文件/条目。变异探针全部在系统临时目录复制树（`D:\dsh\windows\temp\iss013-review-probe`）执行，审查终检已删除、项目内零残留（第 5 节）。

## 3. 主要问题（按 P0 → P3 排序）

无 P0、P1、P2 问题。

- **P3-1（文档漂移）计划 D3 冻结 UUID 命名空间值与实现不符**：计划 §6 D3 写明 `UUID_NS = uuid.UUID("2e6c5f60-8f3b-4a1e-9c6d-0b7a3d5e9f01")`（ISSUE-013 冻结），实现与 golden manifest 均为 `9c5c4f3e-2a1b-4c6d-8e7f-0a1b2c3d4e5f`（`src/uav_gpr/storage/rcscan_v1.py:125`、`tests/contract/rcscan_v1_golden.json:5`）。代码与 manifest 一致、确定性测试按实际值钉死，无功能影响；仅计划行文未随实施更新。最小修复：计划 D3 改为实际冻结值或补记变更。
- **P3-2（文档）golden manifest 引用文件名有误**：`tests/contract/rcscan_v1_golden.json:11` 写 `"baseline_manifest": "docs/reference-baselines/manifest.json"`，仓库实际文件为 `docs/reference-baselines/manifest.md`。最小修复：更正文件名。
- **P3-3（测试弱断言）`test_migration_target_exists_refused` 存在空转断言**：`tests/contract/test_rcscan_v1.py:1032` `assert _sha256(target) == _sha256(target)` 是自比较，未真正钉死"已有文件字节不变"（B7 意图）。拒绝语义本身已被 `pytest.raises(DomainError)`（L1033）验证，覆盖缺口为"字节不变"而非"拒绝"，影响低。最小修复：第二次迁移前记录哈希、失败后复比对。
- **P3-4（测试覆盖缺口）乱序（递减）逐道时间戳的迁移 blocked 路径无测试**：实现 `_require_non_decreasing`（`src/uav_gpr/storage/rcscan_v1.py:1002–1009`）在迁移前拒绝递减时间戳（fail-closed、理由充分：无法推导非负有序单调值），但测试矩阵 A/B 无对应用例。最小修复：补一条递减 timestamps 夹具断言 blocked 且无残留。
- **P3-5（文档/过程）计划 §11.5/§13.4/D-P3"未创建分支、main 直改"表述已与 Git 事实不符**：reflog 显示审查期间已 `checkout` 至 `feat/issue-013`（0 提交、无改写历史）；D-P3 中"与 ISSUE-008～012 各轮一致"亦与 ISSUE-011/012 复审报告及 reflog 事实矛盾（011/012 均建有 feat 分支）。无任何历史污染（main 上从未产生本 Issue 提交），但计划文本需随事实更新。最小修复：计划 §13 补记分支创建时点与授权，修正 D-P3 依据。
- **P3-6（报告深度说明）`V1InspectionReport` 只覆盖结构层，未含 JSON 内容解码状态**：`_build_report`（`src/uav_gpr/storage/rcscan_v1.py:910–968`）逐字段报告存在性/节点类型/dtype/shape/版本状态，但对 channels/history/extras 等 JSON 数据集不做内容解码判定；计划 D1 措辞"解码状态"略宽于实现。损坏 JSON 内容由 `RcScanV1Reader` fail-closed 抛出且 `DomainError.context` 携带字段路径（如 `{"field": "channels"}`），结合满足 M02"不支持/损坏 v1 的字段级报告"验收。最小修复（可选）：`inspect_v1` 报告对 JSON 数据集增加解码状态列。

## 4. 逐 Issue 验收矩阵（M02_STORAGE.md L213–215 + 范围 L202–205 + 排除项 L209）

| # | 验收标准 | 状态 | 代码证据 | 测试/实测证据 |
|---|---|---|---|---|
| 1 | 真实结构的匿名 v1 fixture 可读取 | **PASS** | builder 镜像冻结 `save_rcscan` 布局（`test_rcscan_v1.py:188–271`，与参考 `rcscan.py@290c5dad…` L202–274 逐节点对拍：根 attrs/channels JSON/axes/frequency raw+calibrated+history_json/position_m/trace_metadata/time_base/time_processed）；`RcScanV1Reader._read_document`（`rcscan_v1.py:551–742`） | 定向 36 passed；A1 full roundtrip、A2 minimal、calibrated_only/time_only 变体逐值对拍（`test_rcscan_v1.py:396–500`）；golden digest 自证（L379–394） |
| 2 | adapter 映射 raw/calibrated/time/channels/axes/history | **PASS** | `rcscan_v1.py:719–742`（FrequencyScan/TimeDomainScan/ChannelSpec 映射）；time history 合成 import 记录（L463–495，`executed_utc` 恒取 v1 `created_utc`） | A1 断言 channels/axis/raw/calibrated/time 数据/stage/params/timestamp 与 manifest 逐值相等（L401–464） |
| 3 | 缺 mission/GNSS/UTC 保持 None，不伪造当前时间或 0 坐标 | **PASS** | 模块内无 `datetime.now`（grep 0 命中）；`created_utc` 缺省 None（L568–578）；GNSS 行 `gnss_match=None` + `quality_status=DEGRADED` + `GNSS_MISSING`（L1182–1184）；无逐道时间戳 → 迁移 blocked（L1051–1057）；`SystemClock` 仅用于 checkpoint `updated_utc` 写入时戳（L1351，计划 D3 明示非数据伪造） | A2 minimal 断言可选组/timestamps/extras/position 全 None（L466–481）；B1 断言 GNSS 全空且含 gnss_missing（L879–881）；变异探针 A（把缺失时间戳换成伪造时间）被 `test_migration_no_timestamps_blocked` 杀死（L985 失败） |
| 4 | 往返迁移保持数值/axis/channel/history | **PASS** | 逐道经 ISSUE-008 权威 codec `trace_metadata_to_cells` 写行（L1316–1318）、raw 行切片复制（L1319）、可选组直写（L1381–1431）、history_json=适配后 `ProcessingHistory.to_dict()` 规范 JSON（L1220–1228） | B1：严格 reader 复验 + raw/calibrated/time_base/time_processed 逐值相等 + axis 相等 + history stage/params/timestamp 保留（L851–937） |
| 5 | 缺字段不生成当前时间或 0 坐标 | **PASS** | 同 #3 | 同 #3；A2/B1 无 0/0 坐标断言 |
| 6 | 源 v1 文件不变 | **PASS** | 源仅 `"r"` 打开（L513）；迁移重开为只读句柄（L1313、L1390） | A10 读前读后 SHA-256 一致（L755–764）；B3 迁移前后一致（L970–977）；变异探针 C（迁移期间写源）被 `test_migration_source_bytes_unchanged` 杀死（L977 AssertionError） |
| 7 | v2 明确记录 migration provenance | **PASS** | mission attrs：`migration_source_sha256`/`migration_tool_version`/`migration_source_format`/`migration_v1_created_utc`/`migration_v1_frequency_history`（L1339–1345）；`started/ended_utc`=首/末道时间戳、`completion_kind=completed`、`lifecycle_state=finalized`（L1336–1346）；新 mission/file/device/trace ID 按 uuid5(源 sha256) 推导（L1114–1146） | B4（L922–937）；B2 确定性（同输入双目录输出 SHA-256 一致 + 同目录拒绝，L939–968）；变异探针 B（删除 provenance attrs）被 `test_migration_roundtrip_full` 杀死（KeyError） |
| 8 | 不支持/损坏 v1 的字段级报告 | **PASS** | `inspect_v1`/`_build_report` 逐字段存在性/类型/dtype/shape/版本状态，内容问题不抛（L910–990）；`RcScanV1Reader` fail-closed：版本 2/3→`UNSUPPORTED_SCHEMA_VERSION`、bool/float/str 版本→`INVALID_ARGUMENT`（L529–549）、坏 JSON（严格 parse，L135–148）、节点类型错、形状不符、time_processed 无 time_base、position 三规则（L647–655，镜像参考 `_scan_common.py:13–47`） | A4–A9、TestV1Inspection 4 例（L502–827）全部实测通过 |
| 9 | 显式迁移生成新 mission/file ID 和源文件 hash（范围 L204） | **PASS** | 同 #7；目标已存在/陈旧暂存拒绝（L1190–1201、L1366–1371）；暂存 `*.partial.rcscan` + 严格 reader 复验后原子改名（L1356–1372）；失败清理无伪 finalized 残留（L1248–1256） | B2/B7/B8/B11（含 AIR role、显式 ID、无 /transport 断言 L1147–1156） |
| 10 | 排除项：不导入旧 CSV/NPZ、不原地升级、不伪造 UTC/GNSS | **PASS** | src/tests 无 `rebar_inspector` 导入（grep 0 命中）；无 CSV/NPZ 引用；源恒只读 | 全部门禁 + 探针 A/C 佐证 |

**结论：全部验收 PASS，无 PARTIAL/BLOCKED。**

## 5. Git 与交付检查

- **分支与基线**：当前分支 `feat/issue-013`，HEAD = `0903749` = `main`（共同祖先即基线）；`git log main..feat/issue-013` = 0 提交。
- **reflog**：本 Issue 仅新增 `HEAD@{0}: checkout: moving from main to feat/issue-013`；无 reset/rebase/amend/force-push 迹象；无绕过 main 保护或历史重建。
- **工作树**：`M docs/issues/M02_STORAGE.md`（仅状态行 1 行变更，diff 见第 2 节）+ 5 个未跟踪新文件；未提交、未 push、未 merge。
- **提交卫生**：无缓存/日志/密钥/实测数据入库；`.agent-teams/` 已在 gitignore（`git check-ignore .agent-teams` OK；`git ls-files` 无该路径）；`.pytest_cache/`、`__pycache__/` 均被忽略。
- **范围外零改动**：`rcscan_v2.py`、`incremental_writer.py`、`rcscan_reader.py`、`partial_recovery.py`、`core/**`、`docs/DATA_FORMAT.md`、`docs/adr/**`、`tools/**` 零改动（git status 仅预期 6 项）。
- **参考项目合规**：`D:\博士任务\rebar-inspector` 18 个冻结 core/storage 源文件 SHA-256 与 `docs/reference-baselines/manifest.md` 全部一致（含核心 `storage/rcscan.py=290c5dad…bc4c`、`core/schema.py=84a8d91a…4ae4`）；本地副本 HEAD=`7c522d2`（=基线声明），预存 dirty 文件与本次无关；实现为契约提取与适配，未复制参考代码大块（结构提取仅体现为节点名/校验语义，无整函数搬运）。`E:\UVA_GPR_system` 本机不存在（manifest 亦仅记录路径），本 Issue 契约不涉及。
- **终检**：审查结束时 `git status --porcelain` 仍为上述 6 项，无审查残留（探针目录已删除）。

## 6. 测试与验证结果

解释器：`.venv/Scripts/python.exe`（Windows venv，Python 3.13.14，numpy 2.5.2，h5py 3.16.0；WSL Ubuntu 24.04 环境）。

| 命令 | 实测结果 | 退出码 | 与 t2 声明对拍 |
|---|---|---|---|
| `pytest tests/contract/test_rcscan_v1.py -q` | **36 passed in 10.80s** | 0 | 36 passed in 11.22s ✔ |
| `pytest tests/contract/test_rcscan_reader.py tests/contract/test_storage_schema.py tests/contract/test_raw_trace_hash.py tests/integration/test_incremental_writer.py tests/integration/test_partial_recovery.py -q` | **269 passed in 21.20s**；`--collect-only` 拆分：reader 39 / schema 59 / raw hash 75 / writer 59 / recovery 37 | 0 | 269 passed（37/39/59/75/59）✔ |
| `tools/quality/verify.py`（pytest 非硬件 + ruff + mypy strict + import） | **547 passed, 1 deselected in 44.40s**；ruff/mypy/import 全 ok；all gates passed | 0 | 547 passed, 1 deselected in 44.02s ✔ |
| `python -m ruff check src tests` | All checks passed! | 0 | ✔ |
| `python -m mypy src` | Success: no issues found in 34 source files | 0 | ✔ |
| `git diff --check` | 干净 | 0 | ✔ |

- 新文件无 `xfail`/`skip`/`TODO`/`FIXME`/断言弱化（grep 0 命中；`conftest.py` 的硬件双 opt-in 跳过为既有基础设施）。
- **独立变异探针**（系统临时目录 `D:\dsh\windows\temp\iss013-review-probe` 复制树，`-S` 隔离可编辑安装、确认导入来自副本；执行后已删除，零残留）：
  - 探针 A（绕过缺字段 None 映射：无逐道时间戳时按 `created_utc` 伪造等间隔时间戳继续迁移）→ `test_migration_no_timestamps_blocked` 失败（`test_rcscan_v1.py:985 Failed`），证明"缺字段不伪造"测试真实可杀；
  - 探针 B（跳过 provenance：删除 5 个 migration 属性写入）→ `test_migration_roundtrip_full` 失败（KeyError），证明 provenance 测试真实可杀；
  - 探针 C（放宽源字节校验：迁移期间以 `r+` 打开源 v1 并写入属性）→ `test_migration_source_bytes_unchanged` 失败（`test_rcscan_v1.py:977 AssertionError`），证明源字节不变测试真实可杀。

## 7. 报告与事实差异

1. **计划 D-P3/§11.5/§13.4"不创建分支、main 直改"与终态 Git 事实不符**：`feat/issue-013` 已存在（reflog 唯一新增 checkout 条目、0 提交、无历史改写）。差异无功能影响（main 从未承载本 Issue 提交），计划文本需随事实更新（P3-5）。
2. **计划 D3 冻结 UUID 值（`2e6c5f60-…`）≠ 代码/manifest 实际值（`9c5c4f3e-…`）**（P3-1）；两者内部自洽、确定性测试按实际值钉死，无行为风险。
3. **计划 D1 措辞"字段级报告含解码状态"宽于实现**：`V1InspectionReport` 为结构层报告，JSON 内容解码由 reader fail-closed + 字段路径错误上下文承担（P3-6）。
4. **t3 分派时 t2 完成输出为空**（"(no output recorded)"），实际完成记录由计划 §13 执行日志承载；本报告全部数字均已从仓库独立复现，未依赖该输出。审查结束时 t2 已登记 completed。
5. 其余数字（36/269/547/ruff/mypy/diff-check、哈希、行数、状态行）全部一致，未发现其他差异。

## 8. 剩余风险

1. **单调纳秒为 UTC 推导值（计划 R2）**：v1 无单调时钟记录而 v2 冻结行契约不允许缺失，迁移以 `(timestamps[i]-created_utc)` 确定性整数纳秒填充并已文档标注为"导入推导值，非硬件单调读数"（`rcscan_v1.py:43–47`、计划 §6 R2）。ISSUE-014 及后续对账按逐道 raw hash 为主键，不受影响；若后续 ADR 收紧可改为迁移 blocked。
2. **规划参数显式提供（R3）**：if_bw/power/target_interval/GNSS 策略由调用方显式传入、无静默默认（`rcscan_v1.py:1011–1028`），v1 未记录这些参数——迁移者须知其为导入时声明。
3. **history 内容契约（R4）**：v2 `history_json` 采用"适配后 ProcessingHistory.to_dict() 规范 JSON、v1 原文内嵌参数"的定义，属 ISSUE-013 冻结；后续处理 Issue 演进时需兼容。
4. **DATA_FORMAT §9 v1 映射条文未入文（R5）**：t2 契约将 `docs/DATA_FORMAT.md` 列为 out of scope，v1 映射/迁移契约目前以计划 §6/§13 + golden manifest + 模块 docstring 为权威记录。建议负责人合并时或后续授权流程将 §9 扩充入文（与 ISSUE-011/012 同类 P3 先例）。
5. **v1 源可用性口径**：原始参考 `E:\钢筋仪软件开发` 本机不可达，事实依据为本地只读副本 `D:\博士任务\rebar-inspector`（main@7c522d2）内容哈希与 ISSUE-001 manifest 冻结值逐一对拍一致；无真实现场 v1 文件（manifest 排除），黄金夹具为按冻结 schema 的合成结构——真实现场文件首次打开建议走 `inspect_v1` 预检。
6. **ISSUE-014 消费方衔接**：迁移产物为标准 v2 文件（严格 reader 可读、逐道 raw hash 按 ISSUE-009 framing 重算、GNSS 全空带 gnss_missing），可直接进入 ISSUE-014 inventory 路径；唯需注意推导单调值与 migration provenance 附件的语义已在文档标注。

## 9. 合并建议

可以进入项目负责人人工验收。建议负责人：① 审阅本报告第 3/8 节后决定 P3 项与 DATA_FORMAT §9 入文时机；② 合并时将 `feat/issue-013` 与既有 011/012 分支同等处置（授权 merge 或 cherry-pick 未跟踪文件入树），并把 M02 状态行由 Review 置 Done（沿用 ISSUE-011/012 的"授权合并后置 Done"流程）；③ 采纳第 10 节最小修复清单（不阻止合并，可合并后择机执行）。

## 10. 最小修复清单

| # | 项 | 位置 | 性质 |
|---|---|---|---|
| 1 | 计划 D3 冻结 UUID 值改为实际值 `9c5c4f3e-2a1b-4c6d-8e7f-0a1b2c3d4e5f`（或补记变更记录） | `docs/plans/2026-08-30-issue-013-v1-migration.md` L95 | P3 文档 |
| 2 | golden manifest `baseline_manifest` 文件名 `manifest.json` → `manifest.md` | `tests/contract/rcscan_v1_golden.json:11` | P3 文档 |
| 3 | `test_migration_target_exists_refused` 用"迁移前哈希→拒绝→复比对"替换自比较断言 | `tests/contract/test_rcscan_v1.py:1032` | P3 测试 |
| 4 | 补递减时间戳夹具用例：迁移 blocked 且无残留 | `tests/contract/test_rcscan_v1.py` | P3 测试 |
| 5 | 计划 §13 补记 `feat/issue-013` 分支创建时点与授权；修正 D-P3"与 008–012 一致"的依据 | `docs/plans/2026-08-30-issue-013-v1-migration.md` L186/195/250 | P3 文档 |
| 6 | （可选）`inspect_v1` 报告对 JSON 数据集增加内容解码状态列，落实 D1"解码状态"措辞 | `src/uav_gpr/storage/rcscan_v1.py:910–968` | P3 增强 |
| 7 | DATA_FORMAT §9 v1 映射/迁移契约入文（含推导单调值标注、provenance attrs、blocked 语义） | `docs/DATA_FORMAT.md` §9 | 负责人合并时决定 |

审查结束。审查者未修改任何实现/测试/计划/M02/Git 状态；唯一产出为本报告文件。
