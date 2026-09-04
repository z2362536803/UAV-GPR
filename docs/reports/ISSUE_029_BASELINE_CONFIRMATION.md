# ISSUE-029 开工基线确认单（2026-09-05）

## 1. 目标 Issue 与依据

- **Issue**：ISSUE-029「`.rcal/.rcbg`、兼容性与质量报告」（`docs/issues/M06_CALIBRATION_PROCESSING.md` L79-115）
- **状态行核查**：M06 L81 ISSUE-029 状态 = `Planned`；映射 FR-011；直接依赖 ISSUE-011、027、028。
- **工作区根替换声明**：Issue 提示词中 `E:\wurenjiruanjian` 一律替换为当前工作区根 `D:\博士任务\无人机软件\UAV-GPR`。
- **流程依据**：`docs/issues/README.md`（依赖顺序执行、一次一个 Issue、通用执行协议 §3）、`docs/ISSUE_REVIEW_STANDARD.md` v1.0（t1 基线 → t2 实现 → t3 独立复审）。

## 2. 只读核查证据（全部实测）

| 项 | 结果 |
|---|---|
| HEAD / 分支 | `main @ 5147a15`（`docs(issues): mark ISSUE-028 Done after authorized merge`）；`git status --porcelain=v1 -b` = `## main...origin/main`（工作树干净、与 origin 同步 0/0） |
| reflog / 历史 | `git log --oneline -5`：5147a15 → 269d6e0（Merge feat/issue-028）→ 13acdfe（feat）→ 56c2f0f（mark 027 Done）→ a2f65c6（Merge feat/issue-027）；无 reset/rebase/amend/强推痕迹 |
| ISSUE-011 证据 | M02 L118 状态 = `Done（2026-08-30 独立复审 PASS WITH CONDITIONS 后授权合并，见 docs/reports/ISSUE_011_REVIEW_REPORT.md）`；`src/uav_gpr/storage/rcscan_reader.py`（1070 行）+ `tests/contract/test_rcscan_reader.py`（34 测试）tracked 实测存在 |
| ISSUE-027 证据 | M06 L7 状态 = `Done（2026-09-02 VERDICT=PASS 授权合并；3 项 P3 非阻塞挂账）`；`src/uav_gpr/calibration/osl.py`（1008 行）+ `tests/contract/test_calibration_osl.py`（31 测试）tracked 实测 |
| ISSUE-028 证据 | M06 L44 状态 = `Done（2026-09-02 团队复审 PASS + 第二意见 5 项发现关闭后授权合并）`；合并链 `269d6e0`（Merge）+ `13acdfe`（feat）在 main；`src/uav_gpr/calibration/reference.py`（744 行）+ `tests/contract/test_calibration_reference.py`（15 测试）tracked 实测 |
| 三份基线/复审报告 | `docs/reports/ISSUE_011_BASELINE_CONFIRMATION.md`、`ISSUE_027_BASELINE_CONFIRMATION.md`、`ISSUE_028_BASELINE_CONFIRMATION.md` 及对应 REVIEW_REPORT 均存在 |
| 依赖定向 | `.venv/Scripts/python.exe -m pytest tests/contract/test_calibration_osl.py tests/contract/test_calibration_reference.py tests/contract/test_rcscan_reader.py tests/unit/test_core_identifiers.py -q` → **113 passed in 12.53s**（31+15+34+33，exit 0） |
| 可执行性 | `src/uav_gpr/storage/` 无任何 rcal/rcbg 模块（仅 rcscan v1/v2/reader/writer/recovery/inventory）；`reference.py` 模块 docstring 明示 "`.rcal`/`.rcbg` persistence belongs to ISSUE-029"；`core/identifiers.py` 已有 `CalibrationProfileId`/`BackgroundReferenceId` 类型；`docs/plans|reports` 无 029 实现文件 → **ISSUE-029 为下一个可执行 Issue** |
| 参考源白名单 | `docs/reference-baselines/manifest.md` 已冻结钢筋仪候选源 SHA-256，含 t2 直接相关：`storage/reference_files.py`（`970ea6a9…`）、`calibration/osl.py`（`afbc6a73…`）、`calibration/background.py`（`db96c7ba…`）、`ui/reference_wizard.py`（`c45fedc7…`）；迁移时按此白名单记录实际使用源哈希 |
| 本地只读副本核查 | `E:\钢筋仪软件开发` 本机不可达（`Test-Path` = False），按 027/028 先例改用本地只读副本 `D:\博士任务\rebar-inspector` 实测哈希：`storage/reference_files.py` = `970ea6a94739a3d240859993d7d433b1e755cc4c063c2273d331e5845a1bd3ce`（31761 B）、`calibration/osl.py` = `afbc6a73dedf261c223ca2c798adccaba19894bf4627e99fcc94cc38e0e5d3d9`、`calibration/background.py` = `db96c7ba287e6e90cd273e7d71abc4c1f2172135c135ab7c9c2d67bf33435c86` —— **三者与 manifest.md 冻结值逐一相等**，t2 复数编码/JSON schema 迁移以 `reference_files.py` 为契约参考源 |

关键依赖文件内容哈希（SHA-256 前 16 位，供 t3 交叉核对）：

```text
5b7136979df9e6ad  src/uav_gpr/calibration/osl.py
e2e5403766c45f88  src/uav_gpr/calibration/reference.py
9e109a0e4bbd7267  src/uav_gpr/storage/rcscan_reader.py
79b73d17bf41e3ef  docs/DATA_FORMAT.md
5ca0dc5fdd2ccedc  docs/CALIBRATION.md
```

## 3. 契约要点（对实现有约束，t2/t3 引用）

来源：M06 ISSUE-029 条目 + `docs/DATA_FORMAT.md` §8（L260-272）+ `docs/CALIBRATION.md` §4/§6/§7/§9 + AGENTS.md §1/§3。

1. **文件 schema 必备字段**（DATA_FORMAT §8，延续钢筋仪 JSON 思路）：`format_name`、`schema_version`、profile/reference ID；创建 UTC、软件版本、设备/端口/通道；完整频率轴与扫频配置摘要；复数值的明确编码；采集道数、统计和质量报告；OSL 标准件/算法或空采数据域；内容摘要（digest）。
2. **加载时严格检查**（DATA_FORMAT §8 末段）：通道、S 参数、频率轴、数据域、算法版本逐项校验。**用户选择文件不等于兼容，不等于自动完成物理校准**（CALIBRATION §8：参考文件加载、启用和物理采集是不同动作）。
3. **字段级 compatibility result 三态**（CALIBRATION §6）：`compatible`（硬性字段全匹配）/ `compatible_with_warnings`（硬字段匹配但环境/时间等软字段差异）/ `incompatible`（拒绝启用并列出字段级差异）。硬错配 = channel/S 参数与顺序、完整频率轴、数据域、校准域时的 `calibration_profile_id`、设备/天线硬性字段（CALIBRATION §4）；环境、离地高度、天线安装、日期等为软警告项，哪些属硬拒绝应由实测后冻结——本 Issue 按现有文档口径实现并留扩展点。
4. **质量报告框架**（CALIBRATION §6）：至少含采集道数、稳定性/离群统计、标准件求解残差、非有限值、被拒道数；阈值在算法迁移与真机测试阶段确定（本 Issue 只做框架与字段，不定死阈值语义）。
5. **provenance 可审计性**（CALIBRATION §7 + 验收 3）：reference 文件离开原临时对象（会话内存态）后仍可审计——需自含 profile/reference ID、内容摘要、来源配置摘要。
6. **复数无损编码**：JSON 下复数以显式结构（如 real/imag 数组或经声明的 base64 二进制编码）无损往返，dtype/shape 显式声明，禁止精度损失或幅度-相位隐式重排。
7. **域约束**（CALIBRATION §4/§5 + AGENTS.md §3）：`raw` 与 `osl_calibrated` 域显式建模且互斥应用；不覆盖 raw；未知 `schema_version` fail-closed 拒绝（AGENTS.md §3：不兼容版本必须拒绝或显式迁移）。
8. **排除项**（M06 ISSUE-029）：不应用 OSL/背景（不做校准数学作用于任务数据）、不做 UI、不因用户选中文件自动启用。
9. **分层纪律**（AGENTS.md §9）：存储层不依赖 UI、不调用网络；新模块落 `uav_gpr.storage`（rcal/rcbg codec/reader/writer）+ 兼容性判定服务面（消费 core 模型）；复用 `CalibrationProfileId`/`BackgroundReferenceId`，不新建平行 ID 类型。
10. **测试纪律**（AGENTS.md §10 + M06 提示词）：失败测试优先；覆盖往返（数值/metadata/digest 稳定）、摘要篡改、未知版本、双通道顺序、频率微差、raw/calibrated domain、质量异常；禁固定 sleep；存储必须有版本契约测试。

## 4. 门禁基线（verify.py 复跑）

- 全量非硬件 pytest：**1101 passed / 4 deselected in 283.82s**（4 deselected = 1 硬件哨兵 + 3 opt-in LibreVNA 硬件测试），与团队计划声明的基线数字一致。
- verify.py（pytest → ruff → mypy → package import）**exit 0，`[quality] all gates passed`**；ruff `All checks passed!`；mypy `Success: no issues found in 48 source files`；import ok。
- 定向依赖：113 passed（见第 2 节）。
- 核查前后 `git status` 一致（本单为唯一新增未跟踪文件）；`git diff --check` clean。

## 5. 实施计划摘要（t2 依据）

**范围**：
1. `src/uav_gpr/storage/reference_files.py`（新）：versioned `.rcal`/`.rcbg` JSON schema（`format_name` + `schema_version` 判别）、复数无损编码（显式 real/imag + dtype/shape/字节序声明，往返 bit 级相等）、canonical-JSON digest（内容摘要，写入即校验、读取先验摘要）、writer（原子落盘：临时文件 + fsync + rename）与严格 reader（未知版本/损坏/篡改 fail-closed）。
2. profile/reference ID：复用 `CalibrationProfileId` / `BackgroundReferenceId`；完整 axis/channel/config 摘要/domain/provenance（创建 UTC、软件版本、设备/端口/通道、源配置哈希）/质量统计字段随文件自含。
3. 兼容性判定服务面（新，无 UI、不应用校准）：对 reference 文件与当前任务上下文产出字段级 `CompatibilityResult`——三态 `compatible / compatible_with_warnings / incompatible`，硬错配（channel/S 参数顺序、频率轴、domain、calibrated 域 profile ID、格式名/schema 版本）逐项 incompatible 原因；软差异（环境、离地高度、天线安装、日期等）warning 列表；结果不含任何“启用”副作用（选文件 ≠ 加载 ≠ 启用）。
4. 质量报告框架：采集道数、稳定性/离群统计、OSL 残差/退化指标、非有限值计数、被拒道数的结构化字段（阈值语义挂账真机阶段）。

**排除项**：不应用 OSL/背景到任务数据、不做 Qt/UI、不自动启用、不改 `osl.py`/`reference.py` 求解与会话语义、不新增 core 错误码（除非确有必要并说明）。

**验收标准对应**（M06 L100-104）：往返数值/metadata/digest 稳定 → 测试矩阵 T1/T2；未知 schema/损坏摘要拒绝 → T3/T4；硬错配拒绝+软警告单列 → T5-T8；离开原临时对象可审计 → T2 metadata 断言。

**测试矩阵**（先写失败测试，`tests/contract/test_reference_files.py` 等新文件）：
- T1 round-trip：OSL profile / air background 双类型，bit 级复数相等 + digest 稳定；
- T2 metadata/provenance 完整性与确定性序列化（同输入两次写字节一致）;
- T3 未知 `schema_version` / 未知 `format_name` → DomainError fail-closed；
- T4 digest 篡改（改 1 字节 payload / 改 digest 字段）→ 拒绝且报错指明字段；
- T5 双通道顺序交换（S11/S22 或 channel index 换序）→ incompatible；
- T6 频率轴微差（末位 1 Hz / 少一点 / dtype 变化）→ incompatible，软近邻不误放；
- T7 raw vs osl_calibrated domain 错配、calibrated 域 profile_id 错配 → incompatible；
- T8 软差异（日期/环境/安装不同）→ compatible_with_warnings 且逐条列明；
- T9 质量异常（NaN/inf 混入 mean、residual 超常、被拒道数 >0）→ 质量报告如实呈现且 writer 拒绝非有限值入库。

**inScope（t2 精确路径）**：`src/uav_gpr/storage/reference_files.py`、`src/uav_gpr/calibration/compatibility.py`（或与上述一致的等价新文件，登记时以实际为准）、`tests/contract/test_reference_files.py`、`docs/reports/ISSUE_029_IMPLEMENTATION_REPORT.md`。changedPaths 与之逐一相等。

## 6. 结论

ISSUE-011/027/028 依赖证据齐全且均在 `main@5147a15`，工作树干净，门禁全绿（1101/4），契约要点已冻结如上。**ISSUE-029 可以开工**（t2 实现按第 3 节约束执行；范围外不动）。
