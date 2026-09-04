# ISSUE-029 独立复审报告（t3）

- **审查对象**：t2 交付的 ISSUE-029「`.rcal/.rcbg`、兼容性与质量报告」（`docs/issues/M06_CALIBRATION_PROCESSING.md` L79-115）
- **执行者**：AgentTeams `uav-gpr-issue-029-rcal-storage` 成员 engineer（t2，attempt 56bb4753-5ce6-481a-887d-b951ddd34785）
- **审查者**：同团队成员 reviewer（t3，attempt 8a516ebe-ac2b-4ae1-8b0f-f82c5c63b705，独立只读复审）
- **审查标准**：`docs/ISSUE_REVIEW_STANDARD.md` v1.0（§13 固定十节格式）
- **审查日期**：2026-09-05
- **审查方式**：全程只读（不修改 t2 交付物、不 commit/push/merge、探针在系统临时目录运行并清理，项目零残留；审查前后工作树一致）

## 1. 审查结论

**PASS WITH CONDITIONS（可进入合并，附 1 项明确低风险条件与 4 项 P3 挂账）**

- 逐 Issue 验收矩阵（M06 L100-104 三条验收标准 + 排除项 + 交付纪律）**全部 PASS**（见第 4 节）。
- 无 P0、无 P1 问题。存在 **1 项 P2**（F1：损坏文件路径上 reader 泄漏裸 `ValueError` 而非 `DomainError`）与 **4 项 P3**（F2-F5）。
- 按 `ISSUE_REVIEW_STANDARD.md` §12：P2 不属于阻止合并级别（fail-closed 仍然生效，仅异常类型违反模块自身错误契约）；修复方向明确、低风险、不阻止本次合并，故判 PASS WITH CONDITIONS。
- t2 声称的门禁数字全部独立复现（定向 32 / 全量 1133 passed + 4 deselected / ruff / mypy(49) / import / diff-check 全绿，见第 6 节）。

## 2. 自动识别的审查范围

| 项 | 事实（实测） |
|---|---|
| Issue | ISSUE-029，M06 L79-115，状态行当前为 `Review`（L81） |
| 基线 | `main @ 5147a15`（`git rev-parse HEAD` = 5147a1565eaa896e415288e117f1fd5e7512fb1ee）；t2 按契约**未提交**，交付物全部位于工作树（共同祖先 = HEAD 本身） |
| 改动文件（t2 inScope 4 精确路径，逐一实测存在） | `src/uav_gpr/storage/calibration_files.py`（1475 行，新）、`tests/contract/test_calibration_files.py`（718 行/32 用例，新）、`docs/plans/2026-09-02-issue-029-rcal-storage.md`（58 行，新）、`docs/issues/M06_CALIBRATION_PROCESSING.md`（仅 L81 一行 `Planned → Review`，实测 `git diff` 单 hunk） |
| 范围外文件 | `docs/reports/ISSUE_029_BASELINE_CONFIRMATION.md`（t1 前置任务产物，非 t2 changedPaths，符合预期） |
| 声称测试数字 | 定向 32 passed；verify.py 1133 passed/4 deselected exit 0；ruff 全绿；mypy 49 files；diff-check clean——**全部复现**（第 6 节） |
| 范围判定依据 | t2 任务契约 inScope 与 `git status --porcelain=v1` 输出**逐一相等**（changedPaths 契约满足）；M06 提示词、t1 基线单 §3 十条契约、t2 计划 §2 相互印证 |
| 直接依赖（011/027/028） | 实测 tracked：`rcscan_reader.py`、`osl.py`、`reference.py` 均在 main@5147a15；t2 只读消费其模型（`OslCalibrationProfile`/`OslCalibrationQuality`/`AirBackgroundReference`/`ReferenceDomain`），未修改其公共语义（`git status` 无这些路径） |

## 3. 主要问题（按 P2 → P3 排序，无 P0/P1）

### F1（P2）reader 在超范围数值字面量上泄漏裸 `ValueError`，违反模块错误契约

- **所属 Issue**：ISSUE-029（`.rcal/.rcbg` 严格 reader）
- **位置**：`src/uav_gpr/storage/calibration_files.py` L1108（`computed = _digest_of(payload)`，经 L126-130 `_canonical` 的 `json.dumps(..., allow_nan=False)`）与 L1063-1116 `_read_envelope`（无该异常的捕获）
- **触发条件**：`.rcal/.rcbg` 文件 payload 中出现解析为 ±inf 的 JSON 数值字面量（如 `1e999`/`-1e999`）。`json.loads` 只对 `NaN`/`Infinity`/`-Infinity` 字面量调用 `parse_constant`（L1079，已正确拒绝），**数值字面量 `1e999` 直接解析为 `float('inf')`**，随后摘要重算在 `json.dumps(allow_nan=False)` 处抛出未被捕获的 `ValueError: Out of range float values are not JSON compliant`。
- **实际影响**：文件仍被拒绝（fail-closed 效果成立），但异常类型为裸 `ValueError` 而非模块文档承诺的 `DomainError`（模块 docstring L49-51「raises core `DomainError` with existing `ErrorCode` values only」）。捕获 `DomainError` 做结构化错误呈现的调用方（后续 UI/加载流程）在此路径会以未处理异常崩溃。契约测试未覆盖该形态。
- **探针证据**：R7（探针实测：`1e999` → `ValueError` 泄漏；对照 R6/R6b：`NaN`/`Infinity` 字面量 → `INVALID_ARGUMENT` 正常拒绝）。
- **违反要求**：模块自身错误契约（docstring）、t1 基线单 §3 第 10 条「失败测试优先」未覆盖此反例、§8.1 异常传播纪律。
- **最小修复方向**：在 `_read_envelope`（或 `_digest_of` 调用处）将 `ValueError` 捕获并转 `_invalid("content_sha256", "payload contains out-of-range numeric values", ...)`；补一条 `1e999`/`-1e999` 字面量契约测试（先红后绿）。

### F2（P3）`.rcal` 顶层 `frequency_hz` 节点未与各 profile 轴交叉校验

- **所属 Issue**：ISSUE-029（加载时严格检查频率轴）
- **位置**：`src/uav_gpr/storage/calibration_files.py` L748-750（`from_document` 仅解码并检查 1-D，未与 `payload.profiles[*].frequency_hz` 比较；消费方 `OslCalibrationFilePayload.frequency_hz` L703-705 取 `profiles[0]` 轴）
- **触发条件**：人为构造 digest 合法但顶层 `payload.frequency_hz` 与 per-profile 轴不一致的文件（探针 R12 已实测：reader **接受**，顶层节点被忽略，实际消费值为 profile 轴）。
- **实际影响**：无数值污染（profile 轴胜出、兼容性判定也用 profile 轴），但 `DATA_FORMAT.md` §8「加载时严格检查……频率轴」对这一冗余节点留有未校验面；直接读 JSON 做审计的下游会看到自相矛盾的元数据。
- **最小修复方向**：`from_document` 中将解码后的顶层轴与每个 profile 轴 `np.array_equal` 比对，不一致拒绝（`AXIS_MISMATCH`/`INVALID_ARGUMENT`），补契约测试。

### F3（P3）`config_sha256` 兼容性检查单侧为 None 时静默跳过

- **所属 Issue**：ISSUE-029（字段级 compatibility result）
- **位置**：`src/uav_gpr/storage/calibration_files.py` L1323-1335（`if context.config_sha256 is not None and have_config is not None:` 才追加该硬项检查）
- **触发条件**：任务上下文声明了 `config_sha256`，而文件 `config_sha256=None`（探针 R14g 实测：verdict=`compatible`，checks 中**不出现** `config_sha256` 项，既非硬错配也非软警告）。
- **实际影响**：t2 完成报告将 `config_sha256` 列为硬项，但单侧 None 的语义（跳过）未在计划/代码注释中说明；`CALIBRATION.md` §4 将「设备/天线配置的硬性兼容字段」列为匹配项。低风险（双侧非 None 的错配已正确硬拒，探针 R14e：hard=[`config_sha256`]），但与报告表述存在未文档化偏差。
- **最小修复方向**：明确语义——上下文要求 config digest 而文件缺失时给出硬错配或至少软警告，并在计划文档记录；补测试。

### F4（P3）报告数字可复现性偏差（文档级）

- **所属 Issue**：ISSUE-029 流程文档（t1 基线单 / t2 完成报告与计划 §7）
- **位置**：`docs/reports/ISSUE_029_BASELINE_CONFIRMATION.md` L16-20（每文件测试计数 prose：rcscan_reader「34 测试」、identifiers「33」，实际收集 39/28；31+15+39+28=113，命令与总数精确复现）；t2 输出/计划 §7「相邻回归 86→91 passed」——commandsRun 未记录该命令，且任何显然组合（三依赖文件 85 / +identifiers 118 / +storage_schema 144）均不等于 91，**无法独立验证**。
- **实际影响**：仅文档准确性；绑定门禁（定向 32 / 全量 1133）与命令全部复现。
- **最小修复方向**：更正 t1 prose 计数；t2 后续报告为非绑定数字附完整命令。

### F5（P3）writer 拒覆盖为 check-then-act，并发写同目标存在覆盖窗口

- **所属 Issue**：ISSUE-029（原子 writer）
- **位置**：`src/uav_gpr/storage/calibration_files.py` L1036-1037（先 `target.exists()` 检查）与 L1054（`os.replace` 无条件原子替换目标）
- **触发条件**：两个进程同时通过 `exists()` 检查后先后 `os.replace` 同一目标——后写者静默覆盖先写者（TOCTOU）。`AGENTS.md` §3「writer 进程内只有一个所有者」纪律与交互式保存场景使实际概率极低。
- **实际影响**：单写者场景无影响（测试 L453-458 顺序覆盖拒绝 + 原件保留已验证，探针 R17a：tmp 残留=0）。
- **最小修复方向**：在文档/计划记录单写者纪律为本格式前置条件；或改为独占创建目标（Windows `CreateFile`/`os.link` 语义）后写入。无需阻塞合并。

## 4. 逐 Issue 验收矩阵（M06 L100-104）

| # | 验收标准（M06 原文） | 状态 | 代码证据（文件:行号） | 测试/探针证据 | 问题或限制 |
|---|---|---|---|---|---|
| 1 | L102 往返数值/metadata/digest 稳定，未知 schema/损坏摘要拒绝 | **PASS** | 信封判别与版本常量 `calibration_files.py:102-108`；canonical digest 写算 `:133-134`、读验 `:1104-1115`；复数编码 `:184-235`（dtype/shape/re/im 显式、NaN/Inf 双侧拒）；原子写 `:1031-1060`；严格读 `:1063-1116` | 测试 `test_calibration_files.py:183-277`（bit 级往返+metadata+两次写字节一致）、`:295-343`（payload 微扰 +1e-12/摘要字段/缺节点/损坏 JSON/非 UTF-8 全拒）、`:351-388`（版本 2/1.0/"1"/true 与换序 format 全拒）；探针 R1（denormal/-0.0/1e308/pi bit 级往返+digest 稳定）、R1b（确定性字节）、R2/R3、R4（2/0/-1/1.0/"1"/True）、R5/R5b、R6/R6b、R18a/b | F1：`1e999` 字面量以裸 ValueError 拒绝（仍拒绝，异常类型违约，P2 挂条件）；`_read_envelope` 先验摘要再解释结构（探针 R8b 证实次序） |
| 2 | L103 axis/channel/domain/profile 硬错配拒绝；软警告单独列明 | **PASS** | `CompatibilityResult` `:1257-1276`（hard_mismatches/warnings 分列）；`_compare_common` `:1279-1390`（channels 全等硬项 `:1293-1306`、频率轴逐点 `array_equal` 硬项 `:1307-1322`、config 硬项 `:1323-1335`、device/software/age/environment 软项 `:1336-1390`）；`check_osl` `:1402-1422`；`check_bg`（domain 硬项 `:1433-1445`、calibrated 域 profile 绑定硬项 `:1446-1461`） | 测试 `:572-580`（全匹配 compatible）、`:583-592`（换序 incompatible）、`:595-608`（+1 Hz/少一点 incompatible）、`:611-627`（软差异四项逐条列明 compatible_with_warnings）、`:630-643`（硬软混合两列并存）、`:665-672`（domain 错配）、`:675-695`（profile 绑定：相同/不同/上下文缺 id 三态）；探针 R14b/c-fix/d/e/f（含 1-ULP 最小可表示频差→incompatible）、R15、R13a/b/c（raw+id、calibrated 缺 id、未知 domain 结构拒收） | F3：config 单侧 None 跳过检查（P3 挂账；双侧非 None 错配已硬拒 R14e） |
| 3 | L104 reference 文件不依赖原临时对象即可审计 | **PASS** | `to_document`（.rcal）`:711-726`：profile_id/channel 表/逐标准件 capture_count/误差项/quality/provenance（created_utc/software_version/device_id/config_sha256/algorithm）；（.rcbg）`:875-912`：domain/calibration_profile_id/axis_unit/channels/frequency/mean_data/provenance/quality；reader 重建只依赖文件内容 `:732-792`/`:914-1017` | 测试 `:214-231`（provenance/ID/通道绑定存活 + 结构相等）、`:253-264`（calibrated 域往返含 profile_id）；探针 R20（丢弃内存对象后 gc，仅凭文件重建全部字段：通道/S 参数/captures 3/4/5/quality.worst/domain/traces/stability shape） | 无 |
| 排除项 | L96-98 不应用校准/背景、不做 UI、不因选中文件自动启用 | **PASS**（合规） | 模块无 apply/enable/activate API（`__all__` `:78-100`）；`CompatibilityContext` 构造纯描述 `:1188-1216`；兼容性函数零 I/O 零副作用 `:1402-1475` | 测试 `:698-710`（dir() 无启用面 + 检查前后数组不变）、`:713-718`（结果 frozen）；AST 分层守卫 `:396-415`（依赖面 ⊆ {core, calibration}，无 Qt/h5py/socket/urllib）；探针 R21（API 面无 enable/apply/activate）、R19（加载数组只读） | 无 |
| 交付纪律 | M06 状态行、计划文档、工作树边界、不 commit/push | **PASS** | 见第 5 节 | `git diff` 单行；`git status` = inScope 4 路径 + t1 报告；reflog 无新提交/无 reset/rebase/amend | 无 |

## 5. Git 与交付检查

- **分支/HEAD**：`main @ 5147a15`，`## main...origin/main`（与远程同步 0/0；t2 按契约未 commit/push/merge/建分支——实测 HEAD 与 reflog 均无 t2 产生的新提交）。
- **reflog**（近 10 条）：全部为基线前的 ISSUE-027/028 历史（merge/checkout/commit），**无 reset、rebase、amend、强推迹象**。
- **工作树**：`M docs/issues/M06_CALIBRATION_PROCESSING.md` + 4 个未跟踪文件 = t2 inScope 4 精确路径 + t1 基线单（前任务产物）。**changedPaths 与 inScope 逐一相等，无范围外修改、无遗漏提交**。
- **M06 改动**：实测仅 L81 一行 `Planned → Review`，未触碰其他条目（含 ISSUE-030-036 状态）。
- **`git diff --check`**：clean（exit 0），无空白错误。
- **提交内容卫生**：改动仅源码/测试/文档，无缓存、日志、构建物、密钥、本地配置、实测数据、参考仓库文件。
- **公共契约变更**：新增 `.rcal/.rcbg` schema_version=1 为 DATA_FORMAT §8 既定契约的落地，非对既有公共 schema（rcscan v2）的变更，无需新 ADR；未知版本 fail-closed 满足 AGENTS.md §3。
- **审查前后一致性**：审查期间产生的探针/临时文件全部清理（harness 系统临时目录删除 + 意外落入项目根的一个探针残留文件已删除），审查结束时 `git status` 与 t2 交付状态**完全一致**（同 5 条目）。

## 6. 测试与验证结果

解释器：`.venv/Scripts/python.exe`（Windows venv，**Python 3.13.14**）。全部命令在仓库根实测，退出码逐一记录：

| # | 命令 | 退出码 | 结果 |
|---|---|---|---|
| 1 | `./.venv/Scripts/python.exe -m pytest tests/contract/test_calibration_files.py -q` | 0 | **32 passed in 0.20s**（t2 声称复现） |
| 2 | `./.venv/Scripts/python.exe -m pytest tests/contract/test_calibration_osl.py tests/contract/test_calibration_reference.py tests/contract/test_rcscan_reader.py tests/unit/test_core_identifiers.py -q`（t1 依赖定向命令复现） | 0 | **113 passed in 12.15s**（31+15+39+28） |
| 3 | `./.venv/Scripts/python.exe tools/quality/verify.py`（独占运行） | 0 | **1133 passed, 4 deselected in 269.92s**；ruff ok、mypy ok、import ok、`all gates passed`（t2 声称复现；= 基线 1101 + 新增 32） |
| 4 | `./.venv/Scripts/python.exe -m ruff check .` | 0 | `All checks passed!` |
| 5 | `./.venv/Scripts/python.exe -m mypy src` | 0 | `Success: no issues found in 49 source files`（48→49，含新模块） |
| 6 | `./.venv/Scripts/python.exe -c "import uav_gpr.storage.calibration_files"` | 0 | import ok（SCHEMA_VERSION=1） |
| 7 | `git diff --check && git status --porcelain=v1 -b` | 0 | clean；边界见第 5 节 |
| 8 | `pytest tests/contract/test_librevna_backend.py::test_close_interrupts_acquire`（隔离复跑 ×3） | 0×3 | 3× `1 passed in 0.07s` |

**复审过程透明声明**：我在首次复跑 verify.py 时误将两个全量门禁并行执行（后台 job 未结束即启动前台运行），出现 1 个与 ISSUE-029 无关的线程时序测试失败（`test_close_interrupts_acquire`，librevna 后端，期望 `BackendClosedError` 实得 `DomainError`）。该失败可完全归因于我方并发资源竞争：t2 改动仅新增文件、不可能影响采集线程行为；隔离复跑 3/3 通过；随后**独占复跑全量门禁全绿**（表 #3）。此为审查者操作失误的记录，不构成对 t2 的扣分项。

**变异/反例探针（系统临时目录运行并清理，项目零残留）**：22 组探针（R1-R22 + 修正组），全部落盘证据如下摘要——

- R1/R1b：denormal(5e-324)/-0.0/1e308/pi 极值 bit 级往返相等（`tobytes()` 对拍）+ digest 稳定 + 两次写字节一致 → 通过
- R2：payload +1e-12 微扰 → `INVALID_ARGUMENT`，stored/computed digest 双双呈现 → 通过
- R3：摘要字段篡改 → 拒绝 → 通过
- R4：schema_version ∈ {2, 0, -1, 1.0, "1", True} → 全部 `UNSUPPORTED_SCHEMA_VERSION` → 通过
- R5/R5b：换序/未知 format_name → `UNSUPPORTED_SCHEMA_VERSION` → 通过
- R6/R6b：`NaN`/`Infinity` 字面量 → `INVALID_ARGUMENT` → 通过
- **R7：`1e999` 超范围数值字面量 → 裸 `ValueError` 泄漏（F1，唯一实质性反例发现）**
- R8a/R8b：修复摘要后的结构缺失仍拒收；摘要先于结构校验（错误次序证实「先验摘要再解释」）→ 通过
- R9：JSON `true` 作为 capture_count → 拒收 → 通过
- R10：channel 表与 profile 序不一致 → 拒收 → 通过
- R11：profile 轴分歧（第二 profile 首点 +4e9）→ 拒收 → 通过
- **R12：顶层 `payload.frequency_hz` 与 profile 轴分歧 + 合法 digest → 被接受（F2）**
- R13a/b/c：raw+profile_id / calibrated 缺 profile_id / 未知 domain → 全拒收 → 通过
- R14a-f：全匹配 compatible；通道换序硬拒；**1-ULP 最小可表示频差 → incompatible（修正组证实精确逐点相等契约在位，t2 测试的 +1 Hz 与我的初版 1e-9（低于 ULP 被吸收）不构成矛盾）**；短轴硬拒；config 双侧错配硬拒；软差异四项（device/software/age/environment）逐条 warning 且 compatible_with_warnings → 通过
- **R14g：config 单侧 None → 检查静默跳过（F3）**；R14h：`max_age_days=None` 时不产生年龄检查（与 D7 设计一致）；R14i 修正组：未来日期文件负年龄不判过期（行为观察项，见第 8 节）
- R15：背景三态矩阵（same/other-profile/domain-swap/ctx-no-profile）全部符合硬绑定语义 → 通过
- R16a/b：NaN mean writer 拒收且零文件残留；inf stability 构造拒收 → 通过（R16c 为探针设计缺陷：直接构造 I028 模型未走存储层，非 ISSUE-029 缺陷——存储层 `AirBackgroundFilePayload.__post_init__:808-819` 已拦截负 trace_count，t2 测试 `:492-503` 覆盖）
- R17a/b/c：拒覆盖+原件保留+tmp 零残留；错后缀拒收；rcbg reader 读 .rcal 拒收 → 通过
- R18a/b：JSON 尾随垃圾 / 非 UTF-8 → 拒收 → 通过
- R19：加载数组写保护（赋值抛错）（R19b `object.__setattr__` 旁路为 I027/I028 同款惯例模式，不构成发现）
- R20：丢弃全部内存对象并 gc 后仅凭文件审计：通道/S 参数绑定、created_utc、device/config、captures 3/4/5、quality.worst、domain、profile 绑定、trace 数、stability 形状全部在场 → 通过
- R21：模块与载荷对象无 apply/enable/activate API 面 → 通过
- R22：重复 JSON 键（last-wins 同值）接受——语义等价对象重算摘要一致，不构成完整性漏洞（行为观察项）
- 探针清理：临时目录 `issue029-review-*` 全部删除（`residue now = False`），harness temp 探针脚本目录已删除

## 7. 报告与事实差异

| # | 差异 | 事实核对 | 定性 |
|---|---|---|---|
| 1 | t1 基线单 §2 每文件测试计数 prose（rcscan_reader「34 测试」、identifiers 隐含「33」） | 实际收集 39/28（31+15+39+28=113）；**t1 记录的命令与总数 113 passed 精确复现** | 文档级偏差（F4a），不影响结论 |
| 2 | t2 输出与计划 §7「相邻回归 86→91 passed」 | commandsRun 未记录该命令；任何显然组合（85/118/144）均不等于 91，**无法独立验证** | 文档级偏差（F4b）；绑定门禁全部复现 |
| 3 | t2「红灯先行（collection error → 32 passed）」 | 过程性声明，无中间产物可事后核验；计划 §7 有日志、无反证 | 按 §10 记为「未发现反证」 |
| 4 | t2「32 用例」「~1480 行」「mypy 49 files」「ruff 全绿」「1133/4 exit 0」「M06 仅 L81 一行」「changedPaths=inScope」「未 commit/push」 | 全部实测一致（1475 行、32 collected/passed、49 files、exit 0、单 hunk、5 条目 status、HEAD/reflog 无新提交） | 声称属实 |
| 5 | t2「唯一实现模块 + compatibility 无单独文件」 | 与 t1 基线单 §5 预告的 `reference_files.py`/`compatibility.py` 落点名不同，但 t1 同时声明「或与上述一致的等价新文件，登记时以实际为准」；t2 任务契约 inScope 即 4 精确路径（captain 中途纠正，计划 §7 有记录） | 已授权的范围落点，无违约 |

## 8. 剩余风险

1. **F1 未修复前**，后续加载流程若以 `DomainError` 为统一错误面，遇到含 `1e999` 类字面量的损坏/恶意文件会出现未处理 `ValueError`（文件本身仍被拒绝，无数据风险）。
2. 质量指标阈值语义（stability/outlier 拒收阈值、degenerate 判定）按 CALIBRATION §6 挂账真机阶段——本 Issue 只交付框架与字段，符合范围；`solve_degenerate` 恒 `false` 常量字段（D6）意味着未来退化求解路径需要 schema 演进（版本化信封已预留升级位）。
3. F2/F3 的防御纵深缺口：digest 合法的刻意构造文件可呈现自相矛盾的顶层轴元数据 / 跳过 config 检查——均在攻击者需重算摘要的前提下，风险低。
4. 未来日期 `created_utc` 的负年龄不触发 stale 警告（软字段语义选择，无文档要求冲突，记录为观察项）。
5. F5 并发写覆盖窗口（单写者纪律缓解）。
6. `1e-9` 级频差在 1e9 Hz 量级低于 float64 ULP 不可表示——「频率微差」的实际最小可分辨差为 1 ULP（已实测该值会被硬拒），下游对「微差」的预期应以可表示差值为准。

## 9. 合并建议

- **建议合并（PASS WITH CONDITIONS）**：三项验收标准与排除项全部满足，无 P0/P1；实现质量整体扎实（判别式信封、写算读验 digest、bit 级复数编码、原子写、只读视图、字段级三态兼容、分层纪律与 AST 守卫均有测试与探针双重证据）。
- **合并条件（非阻塞，建议合并前后由执行者按 §14 最小修复）**：F1（P2）——`ValueError` → `DomainError(INVALID_ARGUMENT)` 包装 + `1e999`/`-1e999` 契约测试。
- **挂账（P3，随后续 Issue 或专项清理）**：F2（顶层轴交叉校验）、F3（config 单侧 None 语义）、F4（报告数字更正）、F5（并发写纪律记录）。
- 依赖顺序：ISSUE-030-033 均消费本交付（033 显式依赖 029 的参考文件与兼容判定），F1 修复建议在 ISSUE-032/033 消费加载错误面之前完成。

## 10. 最小修复清单（按优先级）

1. **F1（P2，合并条件）**：`calibration_files.py` `_read_envelope` 捕获摘要重算路径的 `ValueError` → `DomainError(INVALID_ARGUMENT, field="content_sha256", ...)`；新增契约测试：写入合法文件后以文本替换注入 `1e999`/`-1e999` 字面量，断言 `DomainError` 且 `code == INVALID_ARGUMENT`（先红后绿）。
2. **F2（P3）**：`OslCalibrationFilePayload.from_document` 将顶层 `frequency_hz` 与逐 profile 轴 `np.array_equal` 校验，分歧拒绝；补 digest 合法 + 顶层轴分歧的反例测试。
3. **F3（P3）**：`_compare_common` 明确 `config_sha256` 单侧 None 语义（建议：上下文有而文件无 → 软警告或硬错配，任选其一但写入计划文档）；补测试。
4. **F4（P3）**：更正 t1 基线单每文件计数；t2 后续非绑定数字附完整命令。
5. **F5（P3）**：计划文档记录「同一目标参考文件单写者」前置条件；可选改进写入排他性。

---

审查结束。reviewer 停止工作，等待负责人（captain）按上述结论决定合并、修复或拆分；不进入 ISSUE-030。
