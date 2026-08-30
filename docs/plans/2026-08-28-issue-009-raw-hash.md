# ISSUE-009 实施计划：规范逐道 raw 哈希与黄金向量

日期：2026-08-28
状态：round 1 独立审查 FAIL → round 2 修复完成 → round 2 复审 PASS WITH CONDITIONS →
round 3 最小修复（P3-01/P3-02/P3-03）完成，等待复审与人工验收
执行会话：uav-gpr-issue-009-raw-hash / engineer（round 2 修复于 uav-gpr-issue-009-r2 会话完成；
round 3 最小修复于独立会话完成）

> 正文事实说明：第 1–10 节原为 round-1 开工前快照，曾与仓库事实冲突（分支、环境、门禁基线、M02 状态）。
> round 3 已按仓库实测据实改写：当前分支 `feat/issue-009`、Windows `.venv` / Python 3.13、
> M02 中 ISSUE-009 状态 `Review`。
> 门禁数字口径：372 passed / 1 deselected 是 **round-3 进入实现前的基线**；round-3 新增 4 项测试后，
> 当前实测为 376 passed / 1 deselected，定向契约测试 71（基线）→ 75（round-3 后）。
> 冲突时以第 0 节与仓库实测为准。

## 0. round 2 修复日志（2026-08-28，据实改写）

### 0.1 纠错说明（原日志与代码事实不符）

本节曾被写成「round 2 修复完成」并声称 P1-01/P1-02/P2-01/P2-02 已修复、给出不存在的行号与测试类。
实测（见 [ISSUE_009_BASELINE_CONFIRMATION_R2.md](../reports/ISSUE_009_BASELINE_CONFIRMATION_R2.md) 第 4.1 节）证明
原日志与代码事实不符：`raw_hash.py` 中四个缺陷全部仍存在（无符号下降轴被接受、spec 频率轴可写且源别名、
`trace_index=2**64` 抛 `struct.error`、`from_dict` 静默接受未知字段），声称的
`_require_uint64`、`TestRawHashSpecImmutability`、`TestHashMetadata` 等均不存在。为保留真实记录，本节已
据实改写为下述 round-2 实际完成的修复；黄金向量 framing 与 4 个 expected digest 全程未变。

### 0.2 真实修复记录（先失败测试 → 最小修复 → 测试通过）

| 问题 | 失败测试（修复前红灯） | 最小修复 | 验证 |
|---|---|---|---|
| P1-01 下降无符号轴绕过校验 | `TestFailClosed::test_unsigned_descending_axis_rejected`（`np.uint64([2,1])`）、`test_unsigned_extreme_descending_axis_rejected`（`[2**64-2, 2**64-1]`）、`test_signed_overflow_descending_axis_rejected`（`np.int64([2**63-1, -(2**63)])`）、`test_conversion_collapse_axis_rejected`（`[2**53, 2**53+1]`）——修复前 4/4 失败（被接受并产出 digest） | `_validate_frequency_axis` 先构造规范 `<f8` 视图，再在规范值上检查 finite 与 strictly-increasing；输入数组不变 | 4/4 转绿，均稳定 `NON_INCREASING_AXIS`；uint64 输入数组实测未被修改 |
| P1-02 spec 频率轴非不可变 | `TestRawHashSpecImmutability`（4 个测试：自有只读快照/shares_memory、源修改后 digest/`__hash__` 稳定性、直接写入、`setflags(write=True)`）——修复前 4/4 失败（writeable=True、源别名、改源后 digest/`__hash__` 均变） | `frequencies_hz` 与 `data` 改用 `_immutable_array`（bytes-backed `frombuffer` 自有只读快照，`writeable=False`，`setflags(write=True)` 被 NumPy 拒绝） | 4/4 转绿；合法 v1 digest 不变 |
| P2-01 uint64 边界/结构化错误 | `test_trace_index_int64_bound_rejected`（`2**63`）、`test_trace_index_uint64_overflow_rejected`（`2**64`）、`test_mission_id_wrong_type_rejected`、`test_trace_uid_wrong_type_rejected`、强化 `test_non_canonical_mission_id/trace_uid_rejected`——修复前 2**64 抛 `struct.error`、坏 UUID 抛 `ValueError` | `_require_trace_index` 增加上界 `2**63-1`（与 ISSUE-008 `<i8` 列对齐，决策见 0.3），超界 → `OUT_OF_RANGE`；`_require_mission_id`/`_require_trace_uid` 把非规范 UUID 转 `DomainError(INVALID_UUID)`、非字符串类型转 `INVALID_ARGUMENT` | 全部转绿，精确错误码断言 |
| P2-02 from_dict 严格性 | `TestHashMetadata`（14 个测试：版本字段携带、未知/缺失顶层键、未知/缺失 channel 键、`data_shape` 非 bool 整数与乘积、未知 spec/hash 版本、缺失/错误类型版本、非数值频率/复数对、bool trace_index）——修复前未知键被接受、无版本字段 | `to_dict` 携带 `spec_version`/`hash_version`（均 1）；`from_dict` 冻结顶层与 channel 精确键集（`_SPEC_JSON_KEYS`/`_SPEC_CHANNEL_KEYS`），拒绝未知/缺失/错误类型，`data_shape` 严格非 bool 正整数与乘积校验，v1-only 版本解析（未知版本 → `UNSUPPORTED_SCHEMA_VERSION`） | 14/14 转绿，全部稳定 `DomainError` 错误码 |
| P1-03 main 直接开发 | —（分支边界核查） | 改动位于 `feat/issue-009` @ `e852508`（round-1 后已迁移），`main`/`origin/main` 干净无本 Issue 改动，无 commit/push/merge/rebase/reset | `git status`/`branch -avv`/reflog 核查通过 |
| P3-01 M02 状态/文档真实 | — | `docs/issues/M02_STORAGE.md` 状态置 `Review`（修复完成等待复审）；本计划日志据实改写（即本节） | 状态行与事实一致 |

### 0.3 决策记录

- **`trace_index` 上界 = `2**63 - 1`**：与 ISSUE-008 `.rcscan` v2 `/trace_metadata/trace_index` 的 `<i8`
  （有符号 64 位）存储列对齐，保证每个被接受的 `trace_index` 可空地一致存储；framing 内仍按 `uint64be`
  编码。负数保持既有契约 `invalid_argument`，超上界 `out_of_range`。已写入 DATA_FORMAT 5.1。
- **`RawHashSpec` JSON v1-only**：`to_dict()` 顶层携带 `spec_version=1`（JSON schema 版本）与
  `hash_version=1`（framing 版本）；`from_dict()` 只接受 v1，未知版本 → `UNSUPPORTED_SCHEMA_VERSION`，
  精确键集 → 未知/缺失键 `INVALID_ARGUMENT`。

### 0.4 黄金向量不变声明

修复未改变 framing 语义：合法输入产生的规范 `<f8`/`<c16` 字节流与 round-1 完全一致，
`tests/contract/raw_trace_hash_golden.json` 4 个 expected SHA256 未做任何修改，契约测试 4/4 对拍通过，
独立 reference builder 复算 4/4 一致（见完成报告反例探针）。


## 1. 目标 Issue 锁定

依据 [docs/issues/README.md](../issues/README.md) 的依赖顺序与状态定义：

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | **ISSUE-009：规范逐道 raw 哈希与黄金向量** | [M02_STORAGE.md](../issues/M02_STORAGE.md) 第二个条目（008 已 Done 时本 Issue 为下一个 Ready Issue） |
| 直接依赖 | ISSUE-004～006 | M02_STORAGE.md「直接依赖」字段 |
| 依赖状态 | 全部已合入 `main` 并通过门禁（不随本 Issue 改动） | [基线确认单](../reports/ISSUE_009_BASELINE_CONFIRMATION.md) 第 3 节 |
| 本 Issue 状态（当前事实） | M02_STORAGE.md 中为 **`Review`**（round 2 修复完成并经 round 2 复审 PASS WITH CONDITIONS；round 3 已完成 P3-01/02/03 最小修复，等待复审与人工验收，仅人工验收后置 `Done`） | `docs/issues/M02_STORAGE.md:44`；docs/issues/README.md 第 2 节状态定义 |
| 当前分支（当前事实） | **`feat/issue-009`** @ `e852508`（＝ `main` ＝ `origin/main`）；ISSUE-009 本身尚无提交，未 commit/push/merge | `git symbolic-ref --short HEAD`、`git status --porcelain` |

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：
`D:\博士任务\无人机软件\UAV-GPR`（Git Bash 视角 `/d/博士任务/无人机软件/UAV-GPR`）。两个参考项目路径
`E:\钢筋仪软件开发`、`E:\UVA_GPR_system` 不在本机挂载范围内，且 ISSUE-009 无迁移需求，不触碰。

## 2. 已读文档清单（强制阅读完成）

AGENTS.md（完整）、CLAUDE.md、CONTRIBUTING.md、docs/INDEX.md、docs/DATA_MODEL.md、docs/DATA_FORMAT.md（第 5 节）、
docs/ROADMAP.md、docs/issues/README.md、docs/issues/M02_STORAGE.md、
docs/adr/0002-rcscan-v2-dual-copies.md、docs/TESTING.md、docs/ISSUE_REVIEW_STANDARD.md、
docs/plans/README.md、docs/reports/README.md、docs/PRODUCT_REQUIREMENTS.md（FR-008/009/019）。

## 3. 依赖核对（实际代码与测试证据）

基线 commit：`e852508`（Merge feat/issue-008；`main` ＝ `origin/main` ＝ 当前分支 `feat/issue-009` 的 HEAD）。

| 依赖 | 交付物（main 中实际存在） | 测试证据 | 相关提交 |
|---|---|---|---|
| 004 不可变通道与频域数据模型 | `src/uav_gpr/core/channels.py`、`frequency.py`（bytes 后备不可变数组、`channel × frequency` / `trace × channel × frequency`） | `tests/unit/test_core_frequency.py` | `45c5657` |
| 005 GNSS、道元数据与质量状态模型 | `src/uav_gpr/core/gnss.py`、`metadata.py`、`enums.py`（GnssFix/GnssMatch、TraceMetadata 全字段含 raw hash 字段契约） | `test_core_gnss.py`、`test_core_metadata.py` | `952883e` + `b11e741` |
| 006 MissionConfig、时窗推导与配置摘要 | `src/uav_gpr/core/config.py`（冻结配置、canonical JSON、`config_sha256`、版本 fail-closed） | `test_core_config.py` | `22b0b0f` + `bcef87c` |
| 008（已合入，非依赖） | `src/uav_gpr/storage/rcscan_v2.py`（`/trace_metadata/raw_trace_sha256` 64 ASCII 列；`trace_metadata_to_cells` 写入） | `tests/contract/test_storage_schema.py` | `496f6cd` + `ccbdfbf` |

接口兼容性抽查结论（实现只读引用，不改 core）：

- `MissionId.to_json()` / `TraceUid.to_json()` 返回规范小写 UUID 字符串 → raw hash 的 identity 输入；
- `ChannelSpec.channel_id`（`^[A-Za-z0-9_]+$`）→ 有序 channel IDs 输入；
- `FrequencySweep.frequencies_hz`（float64）与 `.data`（complex128，`channel × frequency`）→ 数值输入；
- `TraceMetadata.raw_trace_sha256`（64 位小写 hex 字段契约）→ 哈希输出兼容落点；
- 输入数组只读不可变，哈希实现只读，绝不修改领域数组。

## 4. 工作树与环境检查

当前工作树（round 3 开工前实测）：

```text
branch           feat/issue-009 @ e852508（main / origin/main 同 commit；本 Issue 无提交）
git status       3 个 tracked 修改（DATA_FORMAT.md、M02_STORAGE.md、core/__init__.py）
                 + untracked 项目文件（raw_hash.py、契约测试、黄金 manifest、本计划与 3 份报告）
用户修改         无（上述均为本 Issue 产物）
缓存/生成物      __pycache__、src/uav_gpr.egg-info、.mypy_cache/、.pytest_cache/、.ruff_cache/
                 与 .venv/ 均已被 .gitignore 忽略
```

环境说明（当前事实）：本地执行环境为 **Windows 11 Enterprise LTSC 2024 / Git Bash**，
解释器为仓库内 **`.venv/Scripts/python.exe` = Python 3.13.14**（实测；任务声明写 3.13.12，
补丁版本差异，记录为环境事实差异）。测试栈 numpy 2.5.2、h5py 3.16.0、pytest 8.4.2、
ruff 0.16.5、mypy 1.20.2，`uav_gpr` 以 editable 安装。**WSL 已被安全策略禁用，`python3`/`wsl`
命令不可用**，所有 Python 命令一律使用 `.venv/Scripts/python.exe`。未安装、也不需要
PySide6 / pyqtgraph（ISSUE-009 无 UI）。

当前门禁基线（进入实现前复测，Windows `.venv`）：

```text
.venv/Scripts/python.exe tools/quality/verify.py
  pytest (non-hardware) ok    372 passed, 1 deselected（hardware 双重 opt-in sentinel）
  ruff                   ok
  mypy                   ok（strict, 30 files）
  package import         ok
```

定向契约测试（round 3 前）：`.venv/Scripts/python.exe -m pytest tests/contract/test_raw_trace_hash.py -q`
→ **71 passed**；round 3 补 3 项 P3-01 测试 + 1 项 P3-03 测试后为 **75 passed**。
ISSUE-008 回归 `tests/contract/test_storage_schema.py` → 59 passed；
core 隔离守卫 `tests/unit/test_core_isolation.py` → 1 passed。

## 5. ISSUE-009 范围确认

### 5.1 目标（M02_STORAGE.md 原文摘录）

冻结无歧义、跨空地一致的 `raw_trace_sha256` framing 与实现。

### 5.2 范围（in scope）

- 哈希版本、长度前缀/字段 framing、ID、通道、有序频率轴和 C-order little-endian complex128；
- 输入规范化但不修改领域数组；
- 合成黄金向量（含 expected digest）和 hash 元数据校验；
- 明确 GNSS 不进入 raw hash。

### 5.3 排除项（out of scope）

- HDF5 写入（008/010）、整文件 hash 比较（014 明示禁止）、transport（037+）；
- v1 迁移、处理算法、UI；
- 重构范围外模块；修改两个参考项目。

### 5.4 验收标准（M02_STORAGE.md 原文）

1. 等价内存布局/本机字节序得到相同 digest；任一身份/axis/channel/raw 改变会变化；
2. 简单拼接歧义被长度 framing 消除；
3. 非规范 shape/dtype/ID fail-closed。

### 5.5 执行协议补充（docs/issues/README.md 第 3 节）

- 只使用现有依赖（numpy 已在 pyproject 内，hashlib 为标准库，无新增依赖）；
- 先写能失败的契约测试再写最小实现；
- 运行目标测试、相关回归、全部非硬件测试、Ruff 与 mypy；
- 默认不 commit、不 push；报告后停止，不自动进入下一 Issue。

## 6. 精确 framing 预案（写入 DATA_FORMAT 第 5 节）

以下 framing 是 DATA_FORMAT.md 第 5 节明示留给实现冻结的事项（“哈希函数的确切 framing
必须在实现前写成契约样本”）。实现时以契约测试与黄金向量固化；若与逻辑设计语义冲突，
先改 DATA_FORMAT/ADR 再动代码：

### 6.1 字节流结构（一次 `hashlib.sha256` 的输入，完全确定）

```text
raw_trace_sha256 = sha256( b"UAVGPR-RAW-SHA256"
                         + uint64be(version)                       # 恒 1
                         + uint64be(mission_id_len) + mission_id_utf8
                         + uint64be(trace_uid_len)  + trace_uid_utf8
                         + uint64be(trace_index)                  # uint64 固定宽度
                         + uint64be(channel_count)
                         + 每通道: uint64be(channel_id_len) + channel_id_utf8（有序）
                         + uint64be(frequency_count)
                         + float64le(频率轴)                       # 连续字节
                         + complex128le(raw, C 序)                 # 连续字节
                       )
```

### 6.2 编码决策

| 项 | 决策 | 依据 |
|---|---|---|
| 哈希版本 | `uint64be(1)`，常量 `RAW_HASH_VERSION = 1` | versioned hash，未来可演进 |
| 长度前缀 | 每个变长 UTF-8 字段（mission_id、trace_uid、channel_id）前加 `uint64be(len)` | 消除简单拼接歧义（验收 2）；固定宽度整数（trace_index、counts）天然无歧义 |
| 固定宽度整数 | `uint64be`：version、trace_index、channel_count、frequency_count、各长度 | 与长度前缀共用大端整数编码，字节流自描述 |
| identity | `mission_id` 与 `trace_uid` 规范小写 UUID 字符串 UTF-8；两者都纳入 | 单道身份（DATA_MODEL 第 2 节） |
| channel IDs | 按 `channels` 元组顺序，每通道 `uint64be(len)+id_utf8` | 通道顺序由显式 channels 给出，禁止字典/窗口推断 |
| 频率轴 | `float64le` 连续字节（`frequencies_hz.astype("<f8", copy=False)` 的 `.tobytes()`） | 与 HDF5 `<f8` 一致；显式字节序 |
| raw 数组 | `data.astype("<c16", copy=False).tobytes()`，C 序，`channel × frequency` | 与 HDF5 `<c16` 一致；C-order 固定 |
| GNSS | 不进入 raw hash | DATA_FORMAT 第 5 节末尾明示；定位字段补正不改变 raw 身份 |
| 输入修改 | 绝不修改领域数组；`astype(copy=False)` 仅当 dtype 已匹配时零拷贝，否则临时副本 | AGENTS.md 不可变 raw 规则 |
| 字节序 | 数值一律 little-endian（`<f8`/`<c16`）；框架整数用 big-endian 便于长度解析（与数值字节序解耦） | 空地一致 + 长度 framing 可读性 |

### 6.3 合法输入前置校验（fail-closed，验收 3）

- `mission_id`：`MissionId` 实例（或规范化 UUID 字符串，经严格校验）；
- `trace_index`：`int`，非负；
- `trace_uid`：`TraceUid` 实例（或规范化 UUID 字符串，经严格校验）；
- `channels`：非空 `Sequence[ChannelSpec]`，channel_id 合法且无重复；
- `frequencies_hz`：一维、有限、严格递增、非空；
- `data`：二维 `channel × frequency`、复数数值（complex128 存储）、shape 与 channels/频率轴匹配；
- 任何不合法输入 → `DomainError`（复用既有 `ErrorCode`：`INVALID_ARGUMENT`/`SHAPE_MISMATCH`/`AXIS_MISMATCH`/`DTYPE_MISMATCH`/`DUPLICATE_CHANNEL`/`NON_INCREASING_AXIS`/`NON_FINITE_AXIS`/`INVALID_UUID`）。

### 6.4 输出与元数据校验

- 输出：64 位小写 hex 字符串（匹配 `TraceMetadata.raw_trace_sha256` 字段契约与 008 的 64 ASCII 列）；
- 提供 `validate_raw_hash(value)`：严格 64 位小写 hex 校验（供元数据校验复用）；
- 提供 `RawHashSpec`（frozen dataclass）承载一次哈希所需的全部输入，独立可校验、可 JSON 序列化。

## 7. 测试矩阵计划

### 7.1 黄金向量（tests/contract/raw_trace_hash_golden.json）

- 多个合成黄金向量，每项含：`mission_id`、`trace_index`、`trace_uid`、channels（channel_id 列表）、
  `frequencies_hz`（列表）、`data`（列表，channel-major）、`expected_sha256`、`version`；
- 数据全部为合成值（无实测轨迹/坐标）；
- 覆盖：单通道/双通道、不同频点数、非平凡复数（负实部、负虚部、零、整数边界）。
- 黄金向量以实现前手工计算的独立参考值（Python 脚本按 framing 计算后人工核对）冻结；
  契约测试只读 manifest，绝不自动重写。

### 7.2 契约测试（tests/contract/test_raw_trace_hash.py，`@pytest.mark.contract`）

实现状态（round 3 后实测）：定向 `75 passed`。
其中「变更敏感」中的 **channel_id 内容变化**、**频率点数变化**、**raw 虚部变化** 三项在 round 2
结束时缺失（round-2 复审 P3-01），round 3 已在 `TestFieldSensitivity` 中补齐为 3 个独立测试；
`_validate_channels` 对非 `ChannelSpec` 元素的结构化错误路径（round-2 复审 P3-03）也已由
`TestFailClosed::test_non_channelspec_element_rejected` 覆盖，断言精确 `INVALID_ARGUMENT` 码。

正常路径：
- 黄金向量逐条：`compute_raw_trace_sha256(...)` 输出与 expected 完全一致；
- 等价内存布局：`data` 以 `complex64` 输入（会自动转 complex128）与 complex128 输入同 digest；
- 本机字节序：`frequencies_hz`/`data` 用 big-endian 视图（`dtype.newbyteorder()`）输入，digest 不变
  （实现以显式 little-endian 编码，天然字节序无关）；
- 非 C 序数组：`np.asfortranarray(data)` 输入与 C 序同 digest（实现以 C 序 `.tobytes()`）。

变更敏感（任一改变 → digest 改变）：
- mission_id 变；trace_index 变；trace_uid 变；
- channel 顺序交换；channel_id 变；
- 频率轴任一值变；频率点数变；
- raw 数据任一值变（一个复数元素）。

歧义消除（验收 2）：
- 长度 framing：构造两个「简单拼接后字节相同但字段边界不同」的输入对，digest 不同
  （例如 mission_id/uid 长度前缀使 `"ab"+"c"` ≠ `"a"+"bc"` 不再坍缩）。

fail-closed（验收 3）：
- 非规范 mission_id（大写/缺横线/非法字符）→ `INVALID_UUID` 或 `INVALID_ARGUMENT`；
- 非规范 trace_uid 同上；
- 负 trace_index → 拒绝；
- channels 空、channel_id 重复 → 拒绝；
- 频率轴非一维/空/非有限/非递增 → 拒绝；
- data 非二维、dtype 非数值（如字符串数组）、shape 与 channel/frequency 不匹配 → 拒绝；
- 布尔值传入 trace_index → 拒绝（bool 是 int 子类，需显式拒绝）。

元数据校验：
- `validate_raw_hash`：64 位小写 hex 通过；大写/长度错/非字符串 → 拒绝；
- `RawHashSpec` 序列化往返 + 校验。

### 7.3 回归

- 全量非硬件 pytest、Ruff、mypy、package import（`tools/quality/verify.py`）；
- `git diff --check`。

## 8. 文档更新（已执行，当前事实）

- `docs/DATA_FORMAT.md`：新增 **第 5.1 节「精确 framing（ISSUE-009 冻结）」**（tracked 修改，+42 行，
  未改其他节），内容为 6.1 字节流结构 + 6.2 编码决策 + 版本常量 + GNSS 排除声明 + fail-closed 错误码表；
  第 5 节原文（「规范至少固定 1-5」）保留不动。
- `docs/issues/M02_STORAGE.md`：ISSUE-009 状态字段已由 `Planned` 置为 **`Review`**
  （`docs/issues/M02_STORAGE.md:44`，1 行 tracked 修改），与 docs/issues/README.md 第 2 节
  「Review = 实现和测试完成，等待人工审查」一致；**未**提前置 `Done`，需人工验收后再改。
- 本计划：round 3 已按仓库实测据实改写第 1、3、4、8 节与文首状态行（分支、环境、门禁基线、
  M02 状态、定向测试数）；第 0 节（round-2 纠错与真实记录）保持原样未改。

## 9. 回退方式

实现全部位于新增文件（`src/uav_gpr/core/raw_hash.py`、tests/contract）、黄金 manifest 与
DATA_FORMAT 第 5 节文档；失败即整体删除新增文件并还原 DATA_FORMAT 即可回退；
不改任何既有 core 契约文件（core 隔离守卫 `tests/unit/test_core_isolation.py` 允许
纯 stdlib/numpy 模块进入 core，hash 模块不引入任何禁用依赖）。
如确需改动 DATA_FORMAT 其他节或新增 ADR，先按 ADR 流程说明。

## 10. 交付物与停止点

- 新增：`src/uav_gpr/core/raw_hash.py`（纯计算模块：只依赖 core 类型与标准库/numpy，
  与 `MissionConfig.config_sha256` 同级，供 storage/transport/application 复用）、契约测试、
  合成黄金 manifest；`docs/DATA_FORMAT.md` 第 5 节精确 framing；M02_STORAGE.md 状态更新；
  基线确认单（已交付）与本计划。
- 报告固定含：实际改动、测试命令与结果、验收逐项对应、未完成/风险、工作树状态；
- 报告后停止交人工验收，不进入 ISSUE-010。
