# ISSUE-009 round-2 基线确认单

日期：2026-08-28（round 2 修复轮开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-009-r2`（执行器 engineer）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支（分支迁移已在 round-1 审查后完成，见第 2.1 节）。
配套文件：round-1 基线确认单 [ISSUE_009_BASELINE_CONFIRMATION.md](ISSUE_009_BASELINE_CONFIRMATION.md)（保留）；round-1 独立审查报告 [ISSUE_009_REVIEW_REPORT.md](ISSUE_009_REVIEW_REPORT.md)（FAIL 依据）；实施计划 [docs/plans/2026-08-28-issue-009-raw-hash.md](../plans/2026-08-28-issue-009-raw-hash.md)（含 round-2 修复日志，本文档第 4 节指出其与代码事实不符）。

## 1. 锁定的目标 Issue 与依据

**ISSUE-009：规范逐道 raw 哈希与黄金向量（round-2 最小修复轮）**

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-009（M02_STORAGE.md 第二个条目） | docs/issues/README.md 依赖顺序主表 |
| round-1 结论 | **独立审查 FAIL**，不得合并、不得进入 ISSUE-010 | docs/reports/ISSUE_009_REVIEW_REPORT.md 第 1/9 节 |
| 本轮性质 | 只按审查报告第 10 节做最小修复，再交 round-2 独立复审 | docs/ISSUE_REVIEW_STANDARD.md 第 14 节 |
| 直接依赖 | ISSUE-004～006（另 ISSUE-008 已合入，提供 64 ASCII 存储列契约） | M02_STORAGE.md「直接依赖」字段；第 3 节证据 |
| 一次一 Issue | 本轮只处理 ISSUE-009；不进入 ISSUE-010 | docs/issues/README.md 第 1/3 节 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

两个参考项目 `E:\钢筋仪软件开发`、`E:\UVA_GPR_system` 不在本机挂载范围内；ISSUE-009 无参考迁移需求，不触碰。

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      feat/issue-009（当前；round-1 审查后已从 main 迁移至此，reflog 留证）
HEAD        e8525080fc9b5aa00ff24c252ba972809d09b604  Merge feat/issue-008
main         e852508（本地）＝ origin/main（remote-tracking）＝ HEAD
工作树       feat/issue-009 上有未提交实现与文档（3 个 tracked 修改 + 5 个 untracked 项目文件）
reflog       HEAD@{2026-08-28}: checkout: moving from main to feat/issue-009（e852508 上，未丢失改动）
历史         git log --all --grep 009 无任何 ISSUE-009 提交；无 commit/push/merge
```

未提交文件清单（`git status --porcelain=v1 -b`，11 行）：

| 状态 | 文件 | 归属 |
|---|---|---|
| M | `docs/DATA_FORMAT.md` | ISSUE-009 文档（+41 行，第 5.1 节精确 framing） |
| M | `docs/issues/M02_STORAGE.md` | ISSUE-009 状态行注释（见 4.5 P3-01） |
| M | `src/uav_gpr/core/__init__.py` | ISSUE-009 导出（+12 行） |
| ?? | `src/uav_gpr/core/raw_hash.py`（420 行） | ISSUE-009 生产模块 |
| ?? | `tests/contract/test_raw_trace_hash.py`（735 行） | ISSUE-009 契约测试（44 个测试函数） |
| ?? | `tests/contract/raw_trace_hash_golden.json`（4 个黄金向量） | ISSUE-009 黄金 manifest |
| ?? | `docs/plans/2026-08-28-issue-009-raw-hash.md` | 实施计划（含 round-2 日志） |
| ?? | `docs/reports/ISSUE_009_BASELINE_CONFIRMATION.md` | round-1 基线确认单 |
| ?? | `docs/reports/ISSUE_009_REVIEW_REPORT.md` | round-1 审查报告 |
| ?? | `.agent-teams/` | 团队运行时目录，非项目内容，不入库 |

注：`raw_hash.py` 420 行 / 测试文件 735 行，而 round-1 审查记录为 444 行 / 780 行；round-2 修复轮开工前文件已被改动过（内容见第 4 节，缺陷仍全部存在）。测试数量 44 对 round-1 记录 48 也相差 4，全量 345 对 349 同样相差 4，差值一致。

### 3.2 依赖 Issue 逐项核对（实际代码与测试证据）

基线 HEAD `e852508` 未变，依赖文件全部 tracked 且工作树无修改（`git status` 对该 13 个文件无输出）：

| 依赖 | main 中实际交付物 | 测试证据 |
|---|---|---|
| ISSUE-004 不可变通道与频域数据模型 | `src/uav_gpr/core/channels.py`（ChannelSpec 五元组、稳定 channel_id）、`frequency.py`（bytes 后备不可变数组，`channel × frequency` / `trace × channel × frequency`） | `tests/unit/test_core_frequency.py` 等 |
| ISSUE-005 GNSS、道元数据与质量状态模型 | `core/gnss.py`、`core/metadata.py`、`core/enums.py`（GnssFix/GnssMatch、TraceMetadata 含 `raw_trace_sha256` 64 位小写 hex 字段契约） | `test_core_gnss.py`、`test_core_metadata.py` |
| ISSUE-006 MissionConfig、时窗推导与配置摘要 | `core/config.py`（冻结配置、canonical JSON、`config_sha256`、版本 fail-closed；`ErrorCode.OUT_OF_RANGE` 已有先例，raw_hash 修复可复用） | `test_core_config.py` |
| ISSUE-008（已合入，非依赖但提供存储契约） | `src/uav_gpr/storage/rcscan_v2.py`：`/trace_metadata/raw_trace_sha256` 64 ASCII 列（`:304`）、`trace_metadata_to_cells()` 写入（`:679-681`） | `tests/contract/test_storage_schema.py`、`rcscan_v2_golden.json` |

接口挂钩点（修复只读引用，不改 core 契约）：

- `MissionId.to_json()` / `TraceUid.to_json()` → 规范小写 UUID 字符串；`MissionId`/`TraceUid` 构造对非规范字符串抛 **`ValueError`**（`identifiers.py:28`），即 P2-01 中「非 DomainError」缺陷的直接来源；
- `ErrorCode` 已含 `INVALID_UUID`、`NON_INCREASING_AXIS`、`NON_FINITE_AXIS`、`OUT_OF_RANGE`、`INVALID_ARGUMENT` 等稳定错误码（`errors.py:26,31-32,44`），修复无需新增错误码；
- `TraceMetadata.raw_trace_sha256`（64 位小写 hex）→ 输出落点契约；ISSUE-008 64 ASCII 列兼容。

## 4. round-1 最小修复清单（审查报告第 10 节）与当前代码实际状态逐项映射

审查报告第 10 节共 6 条。逐项对照**当前实际代码**（非计划文档声称），并给出独立探针实测证据（探针为只读执行，未写任何项目文件）：

| # | 修复项 | 当前代码实际状态 | 实测证据 |
|---|---|---|---|
| 1 | **P1-01** 频率轴 finite/strictly-increasing 校验 | **未修复** | `raw_hash.py:128` 仍在 `<f8` 规范化（`:133`）之前对**原 dtype** 执行 `np.diff(raw) > 0`；探针：`np.uint64([2,1])` 被**接受**并产出 digest `d2daec90…`（应抛 `NON_INCREASING_AXIS`）；`np.int64([2**53, 2**53+1])` 转换后坍缩为相等 float64 也被**接受**（digest `e039944d…`）。有限性检查仅在 `kind=="f"` 分支（`:123`），与「规范值上检查」的修复方向不符 |
| 2 | **P1-02** `RawHashSpec.frequencies_hz` 不可变自有快照 | **未修复** | `raw_hash.py:295-300` 仍用 `np.ascontiguousarray(..., dtype="<f8")`（dtype 匹配时零拷贝、返回同一数组，非 bytes-backed 自有副本）；探针：`flags.writeable == True`、`np.shares_memory(spec.frequencies_hz, src) == True`、修改源数组后 `spec.compute()` digest **改变**且 `__hash__` **改变**、直接 `spec.frequencies_hz[0] = …` 成功、`setflags(write=True)` 可重新开启写 |
| 3 | **P2-01** uint64 上下界显式校验 + UUID 结构化错误 | **未修复** | `raw_hash.py:164-177 _require_trace_index` 无上界检查，全文件无 `_require_uint64`、无 `OUT_OF_RANGE` 使用；探针：`trace_index=2**64` → 抛 **`struct.error`**（非 `DomainError`）；`"NOT-A-UUID"` → **`ValueError`**（`identifiers.py:28`，非 `DomainError(INVALID_UUID)`） |
| 4 | **P2-02** `RawHashSpec.from_dict` 严格键集/版本 | **未修复** | `raw_hash.py:360-420` 仍以 `.get()` 读取、无精确键集校验、无 `spec_version`/`hash_version` 解析；`to_dict()` 也不含版本字段；shape 元素仍 `int(...)` 强制转换（`:382-383`）；探针：payload 增加 `"unexpected": "silently ignored"` 被**接受**且往返相等 |
| 5 | **P1-03** 切 `feat/issue-009` 独立分支 | **已修复** | 当前分支 `feat/issue-009` @ `e852508`，reflog `checkout: moving from main to feat/issue-009` 留证；改动随分支迁移未丢失；未 commit/push |
| 5 | **P3-01** M02 状态按真实阶段更新 | **部分** | `docs/issues/M02_STORAGE.md:44` 已加注释「（round 1 独立审查 FAIL，等待最小修复；修复通过后置 Review，仅人工验收后置 Done）」，但状态词仍为 **`Planned`**，未按计划日志所称置 `In progress` |
| 6 | 复跑全部门禁与反例 | **门禁数字可复现，反例全部仍失败** | 本轮基线复跑：定向 44 passed、全量 345 passed/1 deselected、Ruff/mypy/import/verify.py 全绿（第 5 节）；但审查报告 6.2 节四类反例（unsigned 下降、2**64、spec 别名、from_dict 未知键）实测**全部仍失败**，见上 |

### 4.1 关键差异事实：计划文档 round-2 日志与代码事实不符

`docs/plans/2026-08-28-issue-009-raw-hash.md` 第 0 节「round 2 修复日志」声称四项代码缺陷已修复，并给出证据行号；与仓库事实对照：

| 计划日志声称 | 代码实际事实 |
|---|---|
| P1-01 已修，证据 `raw_hash.py:106-145` | 该区间即 `_validate_frequency_axis`/`_validate_data` 现状，`np.diff` 仍在原 dtype 上执行，unsigned 下降轴实测被接受 |
| P1-02 已修，证据 `raw_hash.py:336-352` | 该区间是 `__eq__`/`__hash__`，无任何 bytes-backed 快照逻辑；实测 writeable=True、源别名、digest/`__hash__` 随源变化 |
| P2-01 已修，证据 `raw_hash.py:88-109, 213-263` | 全文件无 `_require_uint64`、无 `OUT_OF_RANGE` 使用；实测 2**64 → `struct.error`、坏 UUID → `ValueError` |
| P2-02 已修，证据 `raw_hash.py:79-94, 420-497` | 文件仅 420 行，`:420-497` 不存在；`from_dict` 仍 `.get()` 无严格键集、无版本解析；实测未知键被接受 |
| 声称新增测试 `TestFailClosed`（unsigned 下降等）、`TestRawHashSpecImmutability`、`TestHashMetadata` | 实际测试文件 735 行、44 个测试函数，仅有 `TestFailClosed`（其 `test_descending_frequency_axis_rejected` 只覆盖 `[2.0e9, 1.0e9]` float 下降，**无 unsigned 下降/溢出/坍缩用例**）；`TestRawHashSpecImmutability`、`TestHashMetadata` **不存在**；UUID 用例断言放宽为 `pytest.raises((DomainError, ValueError))`（`test_raw_trace_hash.py:389,400`） |
| 声称 M02 置 `In progress` | M02 状态词仍为 `Planned`（仅加注释） |

结论：**计划文档 round-2 日志与代码事实不符——四项代码缺陷（P1-01/P1-02/P2-01/P2-02）实际均未修复**。t2 修复任务必须以当前代码事实为基线，不能以计划日志为完成依据；复审（t3）同样以代码与实测为准。

## 5. 门禁基线与环境差异（核查时实测复跑）

```text
$ python3 tools/quality/verify.py
[quality] pytest (non-hardware) ok    345 passed, 1 deselected（hardware 双重 opt-in sentinel）
[quality] ruff                   ok
[quality] mypy                   ok（strict, 30 files）
[quality] package import         ok
[quality] all gates passed
$ python3 -m pytest tests/contract/test_raw_trace_hash.py -q
44 passed（round-1 审查记录 48；差额 4 与全量差额一致，测试文件在 round-1 后被改动，详见第 4.1 节）
```

环境说明（与 round-1 基线单一致，记录差异非项目缺陷）：WSL Ubuntu 24.04 / Python 3.12.3；系统无 pip/ensurepip/sudo，Windows `py`/`.venv` 形态不可用；测试栈（numpy 2.5.2、pytest 8.4.2、ruff、mypy）装于用户站点 `~/.local`，`uav_gpr` 以 `pip install -e . --no-deps` 提供，均在工作区外或已被 .gitignore 覆盖；ISSUE-009 仅需 stdlib+numpy，无新增依赖。

## 6. 范围 / 排除项 / 验收标准（round-2 修复轮）

**范围（in scope，最小修复，对应审查报告第 10 节）**：

1. P1-01：构造规范 `<f8` 视图后在规范值上校验 finite/strictly-increasing；新增 unsigned 下降、signed 极值溢出、转换后相邻值坍缩的失败测试（稳定 `DomainError(NON_INCREASING_AXIS/NON_FINITE_AXIS)`）；输入数组不变。
2. P1-02：`RawHashSpec.frequencies_hz` 改为 bytes-backed 自有只读快照（与 raw data 同策略）；新增源别名修改、直接写入、`setflags(write=True)`、`__hash__/compute` 稳定性测试；不改变即时 compute 的合法 v1 digest。
3. P2-01：显式校验 framing 整数 uint64 上下界（`0 <= v < 2**64`，`OUT_OF_RANGE`）；公共 API 非规范 UUID 转结构化 `DomainError(INVALID_UUID/INVALID_ARGUMENT)`；精确错误码测试。
4. P2-02：`RawHashSpec.from_dict` 冻结顶层/channel 精确键集、拒绝未知/缺失/错误类型；shape 严格非 bool 整数与范围/乘积校验；明确并测试 spec/hash version 解析策略（v1-only，未知版本拒绝）。
5. P1-03/P3-01：保持 `feat/issue-009` 分支交付边界；M02 状态按真实阶段更新（修复中 `In progress`，复审通过后 `Review`，仅人工验收后 `Done`）。不 commit/push。
6. 复跑：定向契约测试、全部非硬件 pytest、Ruff、mypy strict、package import、`verify.py`、tracked+untracked 文本检查及审查报告 6.2 节全部反例。

**排除项（out of scope）**：`src/` 中除 `raw_hash.py`（及必要测试/manifest）外的任何改动；`docs/issues/M02_STORAGE.md` 仅限 ISSUE-009 状态行；`docs/plans/`、`docs/DATA_FORMAT.md` 仅在有明确依据时最小修订（本基线单不含实现代码，不属实现范围）；`.agent-teams/`；Git 分支切换/提交/推送；ISSUE-010 及后续 Issue；两个参考项目；HDF5 writer、整文件 hash、transport、v1 迁移、处理算法、UI。

**验收标准（M02_STORAGE.md 原文，修复不得削弱）**：

1. 等价内存布局/本机字节序得到相同 digest；任一身份/axis/channel/raw 改变会变化；
2. 简单拼接歧义被长度 framing 消除；
3. 非规范 shape/dtype/ID fail-closed。

外加审查报告第 10 节六条修复项的逐条关闭证据（见第 4 节映射表）。

## 7. 冲突与风险

- 无设计冲突：修复保留既有 v1 framing 与 4 个 expected digest（审查报告第 9 节明确无该证据前不得改动黄金向量）；`OUT_OF_RANGE`/`INVALID_UUID` 等错误码已有定义，复用即可。
- 修复只落在 `src/uav_gpr/core/raw_hash.py`、`tests/contract/test_raw_trace_hash.py` 与必要的 manifest/文档；不触碰 ISSUE-004～006/008 任何代码（core isolation 守卫继续有效）。
- 风险：计划文档 round-2 日志与事实不符，修复轮与复审都必须以代码实测为准；测试文件曾被改动（44 vs 48），修复时不得删除既有测试、降低断言或吞异常。
- 未完成事项：本确认单范围内无；修复任务（t2）由调度器按契约派发，完成后交 round-2 独立复审（t3），审查通过后停止交人工验收，不进入 ISSUE-010。

## 8. 结论

ISSUE-009 round-2 修复轮开工基线已锁定：分支 `feat/issue-009` @ `e852508`（main/origin/main 同为 `e852508`），未提交实现与文档清单确定，依赖证据完整，round-1 六条修复项当前状态逐项可复现（P1-01/P1-02/P2-01/P2-02 未修复、P1-03 已修复、P3-01 部分），计划文档 round-2 日志与代码事实不符的差异已明示。本确认单即为 t2 修复与 t3 复审的权威基线件。

> 后续记录：本单为开工时点的基线快照，不随修复改动；round-2 修复的实际完成记录见
> [实施计划 round-2 修复日志（据实改写）](../plans/2026-08-28-issue-009-raw-hash.md) 第 0 节与 t2 完成报告。
