# ISSUE-009 round-2 独立复审报告

日期：2026-08-28（round 2 独立复审，任务代号 t3）
审查者：DeepSeek Harness AgentTeams `reviewer`（非本轮执行者；只读审查）
审查对象：ISSUE-009「规范逐道 raw 哈希与黄金向量」round-2 最小修复轮
审查标准：`docs/ISSUE_REVIEW_STANDARD.md` v1.0（输出格式依第 13 节）
上一轮：[ISSUE_009_REVIEW_REPORT.md](ISSUE_009_REVIEW_REPORT.md)（round 1，FAIL）
基线件：[ISSUE_009_BASELINE_CONFIRMATION.md](ISSUE_009_BASELINE_CONFIRMATION.md)、
[ISSUE_009_BASELINE_CONFIRMATION_R2.md](ISSUE_009_BASELINE_CONFIRMATION_R2.md)

---

## 1. 审查结论

**整批结论：PASS WITH CONDITIONS**
**单 Issue 结论：ISSUE-009 → PASS WITH CONDITIONS**

依据审查标准第 12 节：

- ISSUE-009 的三条验收标准（`docs/issues/M02_STORAGE.md:63-67`）**全部 PASS**，且每条均由
  审查者**独立实现**的 reference builder 与合成探针交叉证明，不是生产函数自证；
- round-1 报告的 6 条修复项（P1-01、P1-02、P2-01、P2-02、P1-03、P3-01）**逐条关闭**，
  round-1 第 6.2 节四类反例全部由审查者独立复现为已修复（详见第 4/6 节）；
- 无 P0、无 P1、无 P2；仅有 3 条 P3（测试覆盖缺口、计划文档正文失实、结构化错误类型不一致），
  均为明确、低风险、不阻止本次合并的条件；
- 4 个黄金 expected digest **未被修改**，与 round-1 记录值逐字节一致，且由独立 builder 4/4 复算一致。

判为 `PASS WITH CONDITIONS` 而非 `PASS` 的原因：存在 3 条 P3 条件项，以及一处
**无法追溯**的历史测试数量差异（48 → 44，发生在 round-1 审查之后、round-2 开工之前，
详见第 7 节）。二者都不阻止合并，但需项目负责人知悉并决定是否随 ISSUE-009 最终提交一并处理。

**不阻止合并；能否放行 ISSUE-010 见第 9 节。**

---

## 2. 自动识别的审查范围

### 2.1 从完成报告 / 仓库自动提取的范围

| 项 | 事实（Git / 仓库实测） |
|---|---|
| Issue | ISSUE-009「规范逐道 raw 哈希与黄金向量」，`docs/issues/M02_STORAGE.md:42-77` |
| 直接依赖 | ISSUE-004～006（另 ISSUE-008 提供 64 ASCII 存储列契约） |
| 声称状态 | round-2 最小修复完成，M02 状态置 `Review`，等待 t3 独立复审 |
| 当前分支 | **`feat/issue-009`**（`git symbolic-ref --short HEAD`） |
| 目标分支 | `main` |
| 审查基线 | `e8525080fc9b5aa00ff24c252ba972809d09b604`（＝ HEAD ＝ 本地 `main` ＝ `origin/main` ＝ merge-base） |
| ISSUE-009 提交 | **无**（`git log --all --grep 009` 无结果；HEAD 仍是 ISSUE-008 的 merge commit） |
| 提交作者/时间/父子关系 | 不适用（无本 Issue 提交，未 commit/push/merge） |
| tracked 修改 | 3 个：`docs/DATA_FORMAT.md`（+42）、`docs/issues/M02_STORAGE.md`（1 行）、`src/uav_gpr/core/__init__.py`（+12） |
| untracked 项目文件 | `src/uav_gpr/core/raw_hash.py`（682 行）、`tests/contract/test_raw_trace_hash.py`（1056 行 / 71 个测试函数）、`tests/contract/raw_trace_hash_golden.json`（309 行 / 4 向量）、计划与 3 份报告文档 |
| 运行时目录 | `.agent-teams/`（untracked，AgentTeams 状态，项目规则明示不入库） |
| 声称测试 | 定向 71；全量 372/1 deselected；Ruff；mypy strict 30 files；package import；`verify.py`；`git diff --check` + 7 文件文本卫生；反例探针 18/18 |

范围判定：基线 `e852508` 唯一确定，工作树仅上述 6 个项目文件 + 4 份文档 + 团队运行时目录，
无其他 Issue 混入，**范围可唯一确定，不构成 `BLOCKED`**。

### 2.2 范围外改动核查（只允许 `raw_hash.py` / 契约测试 / manifest / 本 Issue 文档）

| 检查 | 结果 |
|---|---|
| `channels.py`、`frequency.py`、`metadata.py`、`gnss.py`、`config.py`（ISSUE-004～006） | `git status --porcelain` 对这些路径无输出 → **未改动** |
| `storage/rcscan_v2.py`、`tests/contract/rcscan_v2_golden.json`、`test_storage_schema.py`（ISSUE-008） | **未改动**；ISSUE-008 契约测试 59 passed |
| `tests/` 下既有测试 | 除本 Issue 新增文件外无改动 |
| 是否提前实现 ISSUE-010（writer/checkpoint/finalize/HDF5 写入） | **否**：`raw_hash.py` 导入面仅 `hashlib`/`re`/`struct`/`collections.abc`/`dataclasses`/`typing`/`numpy`/3 个 core 模块；无 h5py、无文件 I/O |
| 是否删除/削弱既有测试 | 见第 7 节；round-2 净增 27 项（44 → 71），未发现 `xfail`/`skip`/`pytest.raises((…, …))` 放宽（`grep` 命中 0） |

---

## 3. 主要问题（P0 → P3）

### P0

无。

### P1

无。round-1 的 P1-01 / P1-02 / P1-03 均已关闭（证据见第 4 节验收矩阵与第 6 节探针）。

### P2

无。round-1 的 P2-01 / P2-02 均已关闭，且审查者补做的 98 项 fail-closed 探针未发现新的
P2 级缺陷（含 uint64 上下界、UUID 结构化错误、JSON 严格性、错误码精确性）。

### P3-01：计划声明的 3 项「变化敏感」测试在当前测试文件中缺失

- **所属 Issue**：ISSUE-009
- **文件与行号**：`tests/contract/test_raw_trace_hash.py:236-318`（`TestFieldSensitivity`，6 项）；
  计划要求见 `docs/plans/2026-08-28-issue-009-raw-hash.md:215-219`（"channel 顺序交换；channel_id 变；
  频率轴任一值变；频率点数变；raw 数据任一值变"）
- **触发条件**：现行 `TestFieldSensitivity` 仅覆盖 mission_id / trace_index / trace_uid /
  channel **顺序** / axis **值** / raw **实部** 六类变化；**channel_id 内容变化（顺序不变）**、
  **频率点数变化**、**raw 虚部变化**三类无独立测试。
- **实际影响**：验收标准 1 的"任一身份/axis/channel/raw 改变会变化"在**行为上成立**
  （审查者独立探针 9/9 全部证明 digest 改变，见第 6 节），但**回归保护不完整**；
  若未来有人错改 channel_id 或 frequency_count 的 framing，现有测试不会捕获。
- **违反要求**：`docs/plans/2026-08-28-issue-009-raw-hash.md:215-219`（计划自定的测试矩阵）
  与 `docs/TESTING.md:80-87`。
- **最小修复方向**：在 `TestFieldSensitivity` 中补 3 个测试：① 同顺序、不同 `channel_id`
  （如 `("hh_s11","vv_s21")` vs `("hh_s11","vv_s22")`）；② 频率点数 8 → 9（同步改 raw shape）；
  ③ raw 单元素虚部 `+= 1.0j`。三者均断言 `digest != base`。不得改动任何黄金向量或 framing。

### P3-02：实施计划正文（第 1–10 节）仍与当前事实不符，仅第 0 节被据实改写

- **所属 Issue**：ISSUE-009（文档真实性）
- **文件与行号**：`docs/plans/2026-08-28-issue-009-raw-hash.md:33`（"下一个 Ready Issue"、
  "本次会话置 In progress"）、`:86-99`（"branch main"、"WSL Ubuntu 24.04 Python 3.12.3"、
  "301 passed"）、`:247`（"M02 状态 Planned → Review"）、`:53`（"本 Issue 状态 Planned"）
- **触发条件**：round-2 只改写了第 0 节（含 0.1 纠错说明），第 1–10 节仍是 round-1 开工前的原文。
- **实际影响**：计划文档是审查标准第 3 条证据链（"Issue、里程碑和专题设计文档"）的一环；
  正文仍声明"当前分支 main""无 .venv，WSL 环境""M02 状态 Planned""301 passed"，
  与当前 `feat/issue-009`、Windows `.venv`、M02 `Review`、372 passed 全面冲突。
  该文件已有"声称已修、实际未修"的前科（`ISSUE_009_BASELINE_CONFIRMATION_R2.md:89-102`），
  正文失实会持续误导后续执行者与审查者。
- **违反要求**：`docs/ISSUE_REVIEW_STANDARD.md` 第 3 节证据权威顺序、第 10 节完成报告真实性核对。
- **最小修复方向**：最小修订第 1、4、8 节：把分支改为 `feat/issue-009`、环境改为
  Windows `.venv` / Python 3.13、门禁基线改为 372 passed/1 deselected、M02 状态改为 `Review`；
  或在正文顶部加一行"第 1–10 节为 round-1 开工前快照，事实以第 0 节与仓库为准"。不得改写第 0 节。

### P3-03：`_validate_channels` 对非 `ChannelSpec` 元素抛 `TypeError`，与模块结构化错误契约不一致

- **所属 Issue**：ISSUE-009
- **文件与行号**：`src/uav_gpr/core/raw_hash.py:97-100`（`raise TypeError(...)`）；
  对照同文件其余校验全部为 `DomainError`（`:92-95`、`:103-108`、`:127-157`、`:196-215`、`:223-235`）
- **触发条件**：`compute_raw_trace_sha256(..., channels=("hh_s11",), ...)` 传入字符串元素；
  实测抛 `TypeError: channels must contain ChannelSpec, got str`。
- **实际影响**：仍为 **fail-closed**（拒绝而非静默接受），且类型注解已由 mypy strict 静态拦截；
  但调用层无法用统一的 `DomainError.code` 分支处理，与 `docs/DATA_FORMAT.md:203`
  "任一违反即拒绝，结构化 `DomainError`"的表述不一致。无测试覆盖。
- **违反要求**：`docs/DATA_FORMAT.md:203` 的结构化错误契约（轻微）。
- **最小修复方向**：把 `raw_hash.py:97-100` 的 `TypeError` 改为
  `DomainError(ErrorCode.INVALID_ARGUMENT, ...)`（或 `DTYPE_MISMATCH`），并补 1 个精确错误码测试。
  如项目认为"类型错误就该是 TypeError"，应反向修文档，二者取一，不能长期不一致。

---

## 4. 逐 Issue 验收矩阵

### 4.1 M02_STORAGE.md 三条验收标准（原文 `docs/issues/M02_STORAGE.md:63-67`）

| # | 验收标准 | 状态 | 精确代码证据 | 独立实测证据 | 问题或限制 |
|---|---|---|---|---|---|
| 1 | 等价内存布局/本机字节序得到相同 digest；任一身份/axis/channel/raw 改变会变化 | **PASS** | 规范化 `raw_hash.py:147`（`np.ascontiguousarray(raw, dtype="<f8")`）、`:186`（`<c16`）、`:264`/`:270`（`.tobytes()`）；framing 拼装 `:294-310` | 独立探针：C 序 / F 序 / 非连续切片 / complex64 / `>c16` / `>f8` 六类等价布局 6/6 同 digest；mission_id、trace_index、trace_uid、channel 顺序、channel_id 内容、axis 值、axis 点数、raw 实部、raw 虚部 9/9 digest 均改变（探针 PART1 `AC1*`，31 项全 PASS） | 见 P3-01：其中 3 类变化（channel_id 内容 / axis 点数 / raw 虚部）在**提交测试**中无独立用例，仅由审查者探针证明 |
| 2 | 简单拼接歧义被长度 framing 消除 | **PASS** | 变长字段长度前缀 `_frame_text() :257-259`，调用点 `:297-298`、`:302`；channel_count 与逐项长度 `:300-302`；文档 `DATA_FORMAT.md:178-196` | 独立探针：先证明 naive 拼接 `b"ab"+b"c" == b"a"+b"bc"` 字节相同，再证明 framed digest 不同（`65196e79…` vs `445aadfb…`）；独立 builder 同时复现两者；200 条随机道 production == 独立 builder（0 失配） | 无 |
| 3 | 非规范 shape/dtype/ID fail-closed | **PASS** | `raw_hash.py:125-158`（axis：非 1D/空/非有限/非严格递增强于**规范 `<f8` 值**）、`:161-186`（raw dtype/shape）、`:196-216`（trace_index 类型/非负/上界）、`:219-254`（ID）、`:90-110`（channels） | 独立探针 98 项（PART2）全部按**精确 error code** 断言通过，覆盖：uint64 下降/近 max 下降/int64 溢出/转换坍缩/`>u8` 下降/相邻相等/float32 坍缩 → `non_increasing_axis`；NaN/+inf/首元素 NaN → `non_finite_axis`；空 → `invalid_argument`；2D → `axis_mismatch`；bool/str axis → `dtype_mismatch`；`2**63`/`2**64-1`/`2**64`/`2**63+12345` → `out_of_range`，`2**63-1` 与 `2**32*3` 正常接受；`-1`/`True`/`1.0`/`"0"`/`None` → `invalid_argument`；非规范/大写/缺横线 UUID → `invalid_uuid`，非字符串 → `invalid_argument`；重复通道 → `duplicate_channel`；空通道/错 shape/1-D/字符串 raw → 对应精确码 | 见 P3-03：`channels` 元素非 `ChannelSpec` 时抛 `TypeError`（仍 fail-closed，但非结构化错误） |

### 4.2 round-1 六条修复项逐条关闭核对

| 修复项 | 状态 | 代码证据 | 独立实测证据 |
|---|---|---|---|
| **P1-01** 频率轴 finite/strictly-increasing 在规范值上校验 | **关闭** | `raw_hash.py:143-157`：`canonical = np.ascontiguousarray(raw, dtype="<f8")`（`:147`）**先**规范化，再 `np.isfinite`（`:148-152`）与 `np.diff(canonical) > 0`（`:153-157`）；dtype kind 预检 `:126-132`；模块 docstring `:40-43` 同步 | round-1 反例 `np.uint64([2,1])` 现被拒绝，错误码 `non_increasing_axis`（round-1 时错误接受并产出 `aa7180b3…`）；另补 `[2**64-2, 2**64-1]`、`[2**63-1, -2**63]`、`[2**53, 2**53+1]`、`>u8 [2,1]`、相邻相等、float32 坍缩共 7 类，全部精确码拒绝；合法升序 uint64 轴仍接受且与 float 形式同 digest；被拒输入数组未被修改 |
| **P1-02** `RawHashSpec.frequencies_hz` 不可变自有快照 | **关闭** | `_immutable_array() raw_hash.py:113-122`（`np.array(copy=True)` → `.tobytes()` → `np.frombuffer()`，bytes 后备、只读）；`__post_init__` 对 axis 与 data 同时应用 `:389-390` | `np.shares_memory(spec.frequencies_hz, src) == False`（axis 与 data 均通过）；`writeable is False`；`spec.frequencies_hz[0] = …` 抛 `ValueError`；`setflags(write=True)` 抛 `ValueError`；源数组修改后 `compute()` 与 `hash()` 均不变；只读源数组也被复制且不恢复为可写；`spec.compute()` == 即时函数；`complex64`/`>f8` 输入被规范为 `<c16`/`<f8` 快照 |
| **P2-01** uint64 上下界与 UUID 结构化错误 | **关闭** | `_TRACE_INDEX_MAX = 2**63 - 1`（`:189-193`）+ `_require_trace_index` 上界分支 `:209-215`（`OUT_OF_RANGE`）；`_require_mission_id :228-235`、`_require_trace_uid :247-254` 把 `ValueError` 转为 `DomainError(INVALID_UUID)`，非字符串转 `INVALID_ARGUMENT`（`:222-227`、`:241-246`） | `trace_index=2**64` 现为 `DomainError(out_of_range)`（round-1 时为 `struct.error`）；`2**63`、`2**64-1`、`2**63+12345` 同码；`2**63-1`、`2**32*3` 正常接受（无过度拒绝）；`"NOT-A-UUID"`/大写 UUID/缺横线 UUID → `invalid_uuid`（round-1 时为 `ValueError`）；`12345`/`None`/`object()` → `invalid_argument` |
| **P2-02** `from_dict` 严格键集/版本 | **关闭** | `_SPEC_JSON_KEYS :333-345`、`_SPEC_CHANNEL_KEYS :347-355`；`from_dict` 未知键 `:463-470`、缺失键 `:471-478`、spec/hash 版本类型与 v1-only `:479-504`、shape 严格非 bool 正整数与乘积 `:533-574`、channel 子对象键集 `:584-603`、频率/复数对逐元素 `:640-667`、`trace_index` 类型 `:669-674`；`to_dict` 携带版本 `:429-430` | 37 项 payload 探针全部按精确码拒绝：未知/缺失顶层键、未知/缺失 channel 键、channel 非对象、非法 `s_parameter`/`logical_polarization`/`channel_id`、重复 channel_id（`duplicate_channel`）、`data_shape` 浮点/bool/零/负数/乘积不符/arity 不符、`trace_index` bool/字符串/超界、频率字符串/bool/非递增/非有限、数据对非数值/长度错、非 mapping 载荷、`spec_version`/`hash_version` = 2 → `unsupported_schema_version`，类型错/缺失 → `invalid_argument`；往返与 deep-copy 后 digest 完全一致 |
| **P1-03** 切 `feat/issue-009` 独立分支 | **关闭** | `git symbolic-ref --short HEAD` = `feat/issue-009`；`git rev-parse HEAD` = `e852508`；本地 `main` 与 `origin/main` 同为 `e852508`，无本 Issue 改动 | `git branch -vv` 显示 `feat/issue-009` tracking 无上游、未 push；`git reflog` 最近一条为 `checkout: moving from main to feat/issue-009`（2026-08-28 15:07:18，`e852508` 上，未丢失改动）；无 commit/push/merge/rebase/amend |
| **P3-01** M02 状态按真实阶段更新 | **关闭** | `docs/issues/M02_STORAGE.md:44` 现为 `状态：Review（round 2 最小修复完成，等待独立复审；仅人工验收后置 Done）`（`git diff` 1 行） | 与 `docs/issues/README.md:26` 的状态定义（Review = 实现和测试完成，等待人工审查）一致；未提前标 `Done`；本轮复审通过后仍需人工验收 |

### 4.3 由契约派生的其余验收行

| 验收项 | 状态 | 代码证据 | 独立实测证据 | 限制 |
|---|---|---|---|---|
| 4 个合成黄金向量 expected digest 未变且可独立复算 | **PASS** | manifest `tests/contract/raw_trace_hash_golden.json:13-307`（309 行，与 round-1 记录一致） | 独立 builder（仅 `hashlib`+`struct`+`numpy`，不 import 生产 framing）复算 4/4 与 manifest 一致；4 个 digest 与 round-1 报告第 6.2 节记录值逐字符相等（`1a519053…`、`9c2081f4…`、`8c103dda…`、`b1a60d2b…`） | manifest 的"生成器脚本源码"仍未入库，生成**过程**记为不可完全追溯；数值已独立验证 |
| 输入规范化不修改调用方数组 | **PASS** | `raw_hash.py:147,186,264,270` 全为 `ascontiguousarray/astype` 只读路径；spec 侧 `:389-390` 复制 | 即时函数与 spec 路径均验证：调用方 axis/raw 前后逐元素相等；被拒绝的非法 axis 也未修改 | 无 |
| GNSS 明确不进入 raw hash | **PASS** | 公共签名 `raw_hash.py:273-280` 无 GNSS 参数；framing `:294-309` 无 GNSS 字段；文档 `DATA_FORMAT.md:199` | `inspect.signature` 参数列表仅 6 项、无 gnss；manifest generator note 声明 "no GNSS"，4 个向量无 gnss 字段 | 无 |
| 输出 64 位小写 hex、与 ISSUE-008 列兼容 | **PASS** | `hexdigest() :310`；`validate_raw_hash :313-325`；ISSUE-008 列 `storage/rcscan_v2.py:304`、写入 `:679-681` | 输出长度 64、等于 `.lower()`、`validate_raw_hash` 接受；ASCII 编码 64 字节；ISSUE-008 契约测试 59 passed 无回归 | 无 |
| core 依赖边界与排除项 | **PASS** | `raw_hash.py:52-65` 仅 stdlib + numpy + 3 个 core 模块 | core isolation 守卫 `tests/unit/test_core_isolation.py` 1 passed；全量非硬件 372 passed；mypy strict 30 files；无 h5py/Qt/serial/socket 导入（导入面逐行核对） | 无 |
| 不提前实现 ISSUE-010 | **PASS** | 无 writer/checkpoint/finalize/HDF5 代码；diff 仅 3 个 tracked + 本 Issue 新增文件 | `git status` 与导入面核对；ISSUE-010 状态仍为 `Planned`（`M02_STORAGE.md:80`） | 无 |

**ISSUE-009 单项结论：PASS WITH CONDITIONS。** 本批只含一个 Issue，不存在可拆分的合格子集；
黄金向量与文档不能脱离生产模块单独合并。

---

## 5. Git 与交付检查

| 检查项 | 结果 |
|---|---|
| 仓库根 | `D:\博士任务\无人机软件\UAV-GPR`（与任务声明一致；历史文档中的 `E:\wurenjiruanjian` 已按约定替换） |
| 当前分支 / HEAD | `feat/issue-009` @ `e8525080fc9b5aa00ff24c252ba972809d09b604` |
| 目标分支与共同祖先 | `main` = `origin/main` = `merge-base(HEAD, main)` = `e852508` |
| 本 Issue 提交 | 0（`git log --all --grep 009` 无命中）；因此无作者、时间、父子关系可列；未 commit / 未 push / 未 merge / 无 PR |
| 一个提交混入多个 Issue | 不适用（无提交） |
| 未提交/未跟踪文件 | tracked 3 个（DATA_FORMAT.md +42、M02_STORAGE.md 1 行、core/\_\_init\_\_.py +12）；untracked 项目文件 6 个（raw_hash.py、契约测试、黄金 manifest、计划、round-1 报告、round-1/R2 基线单）+ 团队运行时目录 `.agent-teams/` |
| 缓存/日志/实测数据/参考仓库文件 | **未发现**。未跟踪清单中无 `__pycache__`、无日志、无实测数据、无两个参考项目内容；`.mypy_cache/`、`.pytest_cache/`、`.ruff_cache/`、`.venv/`、`src/uav_gpr.egg-info/`、`__pycache__/` 均为既有 ignored 生成物 |
| 改变公共数据/schema/协议契约且缺文档 | 否。framing 与 fail-closed 规则已写入 `DATA_FORMAT.md:174-214`（tracked 修改，仅新增第 5.1 节，未改其他节）；`trace_index` 上界 2\*\*63-1 决策已写入 `:206` 与计划 §0.3 |
| reflog（HEAD，近 7 条） | `checkout: moving from main to feat/issue-009`（2026-08-28 15:07:18）← `merge feat/issue-008` ← `checkout` ← `commit`(×2 ISSUE-008) ← `checkout` ← **`reset: moving to origin/main`（2026-08-27 14:29:16，早于本 Issue）**。ISSUE-009 期间**无** reset / rebase / amend / 历史重建；只能按字面报告，不据此断言 `reset --hard` |
| 强推 / 绕过保护迹象 | 未发现；`refs/remotes/origin/main` 最近更新为 2026-08-28 12:35:47 的 push（ISSUE-008 merge），此后未变 |
| `git diff --check` | exit **0**（仅覆盖 tracked 文件）；审查者另对 6 个新增/修改的项目文件做 UTF-8 / trailing whitespace / final newline / CRLF 探针，**6/6 通过** |
| 交付边界 | 改动只落在 `src/uav_gpr/core/raw_hash.py`、`tests/contract/test_raw_trace_hash.py`、黄金 manifest 与本 Issue 文档；ISSUE-004～006/008 代码零改动 |
| 审查者改动 | **除本报告外未修改任何项目文件、未改动 `.agent-teams/` 与 `.venv/`、未 commit/push/切分支/reset**；测试产生的缓存仅落在既有 ignored 目录（见第 9 节说明） |

---

## 6. 测试与验证结果

### 6.1 环境（实测）

Windows 11 Enterprise LTSC 2024 / Git Bash；解释器 **`.venv/Scripts/python.exe` = Python 3.13.14**
（任务描述给出 3.13.12，补丁版本有差异，记录为环境事实差异，不影响结论）；numpy 2.5.2；
pytest 8.4.2；ruff 0.16.5；mypy 1.20.2（compiled）；`uav_gpr` 以 editable 安装。
未使用、也未安装 PySide6 / pyqtgraph；**未尝试 `wsl`**。

### 6.2 执行者声称的门禁复跑（全部可复现）

| 实际命令（工作目录 = 仓库根） | 退出码 | 实际结果 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest tests/contract/test_raw_trace_hash.py -q` | 0 | **71 passed**，0 failed / 0 skipped / 0 deselected |
| `.venv/Scripts/python.exe tools/quality/verify.py` | 0 | pytest 非硬件 **372 passed, 1 deselected** + ruff + mypy + package import → `all gates passed` |
| `.venv/Scripts/python.exe -m ruff check .` | 0 | `All checks passed!` |
| `.venv/Scripts/python.exe -m mypy src` | 0 | `Success: no issues found in 30 source files`（strict 来自 `pyproject.toml`） |
| package import（`verify.py` 内建步骤） | 0 | `package import ok` |
| `git diff --check` | 0 | 无 tracked whitespace 错误 |
| `.venv/Scripts/python.exe -m pytest tests/unit/test_core_isolation.py -q` | 0 | 1 passed |
| `.venv/Scripts/python.exe -m pytest tests/contract/test_storage_schema.py -q` | 0 | 59 passed（ISSUE-008 无回归） |
| 6 文件 UTF-8 / 行尾 / 末行探针 | 0 | 6/6 通过 |

`1 deselected` 为 hardware 双重 opt-in sentinel（`docs/TESTING.md:54-71`），属预期，非跳过缺陷。

### 6.3 审查者独立合成探针（未复用生产 framing，脚本置于系统临时目录）

探针目录：`%TEMP%\uav-gpr-r2-probe\`（`probe_framing.py`、`probe_failclosed.py`），**未写入项目目录**。
reference builder 仅按 `docs/DATA_FORMAT.md` 第 5.1 节公式用 `hashlib` + `struct` + `numpy` 重实现，
不 import `raw_hash._u64 / _frame_text / _frame_f64 / _frame_c128`。

**PART 1（framing / 黄金 / 验收 1-2）：31 项，31 PASS，0 FAIL，exit 0**

| 探针 | 结果 |
|---|---|
| 4 个黄金 digest 与 round-1 记录值逐字符一致 | PASS 4/4 |
| 独立 builder == manifest == 生产函数 | PASS 4/4 |
| 200 条随机道（1–3 通道、2–12 频点、`<c8`/`>c16`/F 序混排）production == 独立 builder | PASS，0 失配 |
| 等价布局：C 序 / F 序 / 非连续切片 / complex64 / `>c16` | PASS 5/5 |
| 本机字节序：`>f8` + `>c16` 输入 | PASS |
| 变化敏感：mission_id / trace_index / trace_uid / channel 顺序 / channel_id 内容 / axis 值 / axis 点数 / raw 实部 / raw 虚部 | PASS 9/9 |
| 歧义消除：先证 naive 拼接字节相同，再证 framed digest 不同，并由独立 builder 双向复现 | PASS |
| GNSS：签名无 gnss 参数 | PASS |
| 输入不可变（即时函数） | PASS |
| 输出 64 小写 hex + `validate_raw_hash` | PASS |

**PART 2（round-1 四类反例 + 四条修复的独立探针）：98 项，98 PASS，0 FAIL，exit 0**

| 探针 | 结果 |
|---|---|
| **round-1 反例 1** `np.uint64([2,1])` 下降轴 | **PASS**：`DomainError(non_increasing_axis)`（round-1 时被接受） |
| **round-1 反例 2** `trace_index=2**64` | **PASS**：`DomainError(out_of_range)`（round-1 时为 `struct.error`） |
| **round-1 反例 3** spec 频率轴别名/可写 | **PASS**：`shares_memory=False`、`writeable=False`、直接写与 `setflags(write=True)` 均 `ValueError`、源修改后 `compute()`/`__hash__` 稳定 |
| **round-1 反例 4** `from_dict` 未知顶层键 | **PASS**：`DomainError(invalid_argument)` |
| P1-01 补做（14 项）：uint64 近 max 下降 / int64 溢出 / 转换坍缩 / `>u8` 下降 / 相邻相等 / float32 坍缩 / NaN / +inf / 首元素 NaN / 空 / 2D / bool / 字符串 / 输入未修改 / 合法升序 uint64 接受 | PASS 15/15，错误码精确 |
| P1-02 补做（18 项）：axis 与 data 快照隔离、dtype 规范化为 `<c16`/`<f8`、只读、`setflags` 拒绝、直接写拒绝、`compute()` == 即时函数、`__hash__` 稳定、JSON 往返保 digest、只读源数组也复制 | PASS 18/18 |
| P2-01 补做（21 项）：上界/下界/bool/浮点/字符串/None/UUID 三型/非字符串 ID/重复通道/空通道/dtype/shape + 反向验证不误拒（`2**63-1`、`2**32*3` 接受） | PASS 21/21，错误码精确 |
| P2-02 补做（37 项）：键集、版本、shape、channel 子对象、频率/复数对、非 mapping、往返一致性、键集精确等于冻结集合 | PASS 37/37 |
| 测试弱化扫描：`pytest.raises((` / `xfail` / `skip` / `TODO` / `FIXME`（raw_hash.py + 契约测试） | 命中 **0** |

合计独立探针 **129 项，129 PASS**。

### 6.4 门禁全绿不等于验收通过（审查标准第 9 节）

门禁绿仅覆盖已提交用例；审查者另行补查了执行者测试未覆盖的反例与边界，见 6.3。
补查发现的唯一差异（P3-01 的 3 类变化敏感用例缺失、P3-03 的 `TypeError`）均不构成验收失败。

---

## 7. 报告与事实差异

1. **可复现（与 t2 完成报告一致）**：定向 71 passed、全量 372/1 deselected、ruff 0.16.5 通过、
   mypy strict 30 files 通过、package import ok、`verify.py` all gates passed、`git diff --check` exit 0、
   文本卫生通过、黄金 manifest 未改（4 个 digest 与 round-1 记录逐字符一致）、
   4 个 expected digest 由独立 builder 4/4 复算一致、M02 状态已置 `Review`、
   分支 `feat/issue-009` @ `e852508` 且无 commit/push。
2. **可复现（计划 §0.2 声称的测试）**：`test_unsigned_descending_axis_rejected`(`:530`)、
   `test_unsigned_extreme_descending_axis_rejected`(`:547`)、`test_signed_overflow_descending_axis_rejected`(`:561`)、
   `test_conversion_collapse_axis_rejected`(`:575`)、`TestRawHashSpecImmutability`(4 项，`:741-802`)、
   `test_trace_index_int64_bound_rejected`(`:589`)、`test_trace_index_uint64_overflow_rejected`(`:603`)、
   `test_mission_id_wrong_type_rejected`(`:412`)、`test_trace_uid_wrong_type_rejected`(`:424`)、
   `TestHashMetadata`(14 项，`:936-1056`)，**全部真实存在**，与计划日志一致。
3. **可复现（断言强化）**：round-1 记录的宽松断言 `pytest.raises((DomainError, ValueError))`
   （round-1 报告第 4.1 节称位于 `:389,400`）现已改为精确断言
   `excinfo.value.code is ErrorCode.INVALID_UUID`（现 `:398`、`:410`），**强化属实**。
4. **差异（无法追溯，记为未发现反证）**：测试数量 round-1 记录 48 → round-2 开工时 44 → 现在 71。
   t2 声称"本轮未删除任何测试，仅强化 2 项 + 新增 27 项"，与 44 + 27 = 71 自洽。
   但 **48 → 44 的减少发生在 round-1 审查之后、round-2 开工之前**（`ISSUE_009_BASELINE_CONFIRMATION_R2.md:56`
   已记录），且测试文件从未入库，无版本化副本。审查者从 `.pytest_cache/v/cache/nodeids`
   取到 139 个历史节点名（当前仅 71 个），其中 68 个已不存在，形如
   `TestChangeSensitivity::test_channel_id_change`、`test_frequency_point_count_change`、
   `test_raw_data_imaginary_change`、`TestLayoutEquivalence::test_complex64_input_matches_complex128` 等
   （多数可识别为改名，其中 3 项语义在当前文件中确无对应用例，已列为 **P3-01**）。
   **结论：无法证明 round-2 删除了测试，也无法证明 48→44 的减少由谁造成；记为不可追溯。**
5. **不可独立验证的过程声明**：t2 称"先红灯 28 failed（新增/强化 27 项）"——修复后代码已就位，
   审查者无法在不修改代码的前提下复现红灯；按数量关系（27 新增中 `test_trace_index_int64_max_accepted`
   修复前即通过 + 2 项强化 = 28）自洽，**记为未发现反证**。
   "未 push""未 commit""两个参考项目未动"：本地 Git 事实支持，远端实时状态不可联网核验，记为未发现反证。
6. **不真实（遗留文档，非本轮新引入）**：`docs/plans/2026-08-28-issue-009-raw-hash.md` 第 1–10 节仍写
   "branch main""WSL Ubuntu 24.04 / Python 3.12.3""301 passed""M02 状态 Planned"，
   与当前 `feat/issue-009`、Windows `.venv`、372 passed、M02 `Review` 全面冲突 → **P3-02**。
7. **一致性差异（轻微，非缺陷）**：`_validate_channels`（`raw_hash.py:97-100`）对非 `ChannelSpec`
   元素抛 `TypeError`，而 `DATA_FORMAT.md:203` 声明"任一违反即拒绝，结构化 `DomainError`" → **P3-03**。
8. **历史缺陷已关闭**：`ISSUE_009_BASELINE_CONFIRMATION_R2.md:89-102` 指出的"计划 round-2 日志与代码事实不符"
   已由计划 §0.1 纠错说明 + §0.2 据实改写处理；本轮复审以代码与实测为准，**未采信任何计划日志或完成报告的自我声明**，
   四条代码修复全部经独立探针重新证明。

---

## 8. 剩余风险

1. **测试历史不可追溯（中低）**：测试文件从未入库，`48 → 44 → 71` 的完整演进无法审计；
   若后续 Issue 继续在未提交状态下长期开发，同类问题会重复出现。建议 ISSUE-009 人工验收前
   先形成一次提交，使基线可比较。
2. **黄金向量生成过程不可完全追溯（低）**：manifest 记录了 generator 名称/版本/seed，但
   生成器源码未入库；expected digest 已由本轮独立 builder 4/4 复算，数值风险已消除，
   生成过程仍只能记为"未发现反证"。
3. **raw 复数 NaN/Inf 语义未冻结（低）**：`_validate_data`（`raw_hash.py:161-186`）只校验 dtype/shape，
   非有限复数值会被接受并进入规范字节；`DATA_FORMAT.md` 与测试均未定义 NaN payload / signed-zero 的
   跨实现语义。round-1 已列为剩余风险，本轮维持；若后续允许非有限 raw，须**递增 `RAW_HASH_VERSION`** 并同步文档。
4. **numpy 整数 `trace_index` 会被拒绝（低，跨 Issue 提示）**：`np.int64/np.int32/np.uint64` 一律
   `invalid_argument`。这与既有 `core/metadata.py:118-119` 的契约**完全一致**（非本 Issue 引入，
   不计为缺陷），但 ISSUE-011/014 从 ISSUE-008 `<i8` 列回读 `trace_index` 后重算哈希时，
   调用方必须显式 `int()` 转换。建议在 ISSUE-010/011 提示词中明示。
5. **未提交工作树（中低）**：所有交付仍在 `feat/issue-009` 上未提交；在人工验收前存在误操作/丢失风险。
6. **`RawHashSpec` 内存开销（低）**：`_immutable_array` 对 axis 与 data 各做一份副本；
   对 `channel × frequency` 的 complex128 数据而言，spec 路径会额外占用一份 raw 内存。
   当前 ISSUE-009 无大批量场景；ISSUE-014 若对十万道逐道构造 spec，应评估或改为流式即时计算
   （即时 `compute_raw_trace_sha256` 已是零拷贝路径）。
7. **P3-01 的回归缺口**：channel_id 内容 / 频率点数 / raw 虚部三类变化目前只有审查者的一次性探针覆盖，
   未进入常驻测试；合并前建议补齐。

---

## 9. 合并建议

**可以合并（在 P3 条件被项目负责人接受的前提下），但不得在人工验收前把 ISSUE-009 标记为 `Done`。**

- **质量层面**：三条验收标准全部 PASS，round-1 六条修复项全部关闭，4 个黄金 digest 未变且经独立复算，
  无 P0/P1/P2。三条 P3 均为低风险、不阻止合并。
- **流程层面**：`feat/issue-009` 目前**没有任何提交**，因此"合并"仍是一个待项目负责人执行的动作。
  建议顺序：项目负责人确认本报告 → 处理（或明确接受）第 10 节 3 条 P3 → 形成一次只含 ISSUE-009 文件的提交 →
  merge 进 `main` → 人工验收后置 `Done`。
- **能否放行 ISSUE-010**：
  - 契约与代码层面 **可以放行**：ISSUE-010 依赖的 `compute_raw_trace_sha256` / `RawHashSpec` /
    `validate_raw_hash` 接口与 v1 framing 已冻结并被独立验证，`raw_trace_sha256` 64 位小写 hex
    与 ISSUE-008 `<i8`/64 ASCII 列的兼容性已确认。
  - 但**必须先完成**：① 项目负责人确认本报告；② ISSUE-009 的变更形成提交并进入 `main`
    （否则 ISSUE-010 的分支基线上不存在 `raw_hash.py`，无法开工）；
    ③ 建议在此之前补齐第 10 节 P3-01 的 3 个测试（不阻塞开工，但应在 ISSUE-010 提交前完成）。
  - ISSUE-010 开工时须注意第 8 节第 4 条：`trace_index` 从 HDF5 列回读为 numpy 整数，
    调用哈希前必须 `int()` 转换。
- **不建议拆分合并**：生产模块、黄金 manifest、测试与 DATA_FORMAT 5.1 文档构成同一契约，不可分。

---

## 10. 最小修复清单（仅供项目负责人决定是否再修一轮；审查者不执行修复）

1. **关闭 P3-01**：在 `tests/contract/test_raw_trace_hash.py` 的 `TestFieldSensitivity` 中补 3 个测试——
   ① 同顺序不同 `channel_id`；② 频率点数变化（同步改 raw shape）；③ raw 单元素虚部变化；
   均断言 `digest != base`。**不得改动 framing 或任何黄金 expected digest。**
2. **关闭 P3-02**：最小修订 `docs/plans/2026-08-28-issue-009-raw-hash.md` 第 1、4、8 节
   （分支 `feat/issue-009`、Windows `.venv` / Python 3.13、372 passed/1 deselected、M02 `Review`），
   或在正文顶部加一行"第 1–10 节为 round-1 开工前快照，事实以第 0 节与仓库为准"。**不得改写第 0 节。**
3. **关闭 P3-03**：把 `src/uav_gpr/core/raw_hash.py:97-100` 的 `TypeError` 改为带稳定码的 `DomainError`
   （建议 `INVALID_ARGUMENT`），并补 1 个精确错误码测试；若项目决定保留 `TypeError`，则同步修订
   `docs/DATA_FORMAT.md:203` 的表述。二者取一，不能长期不一致。
4. **（流程建议，非缺陷）** 在人工验收前把 ISSUE-009 的 6 个项目文件形成一次独立提交并入 `main`，
   使测试演进可审计；ISSUE-010 提示词中明示 `trace_index` 需 `int()` 转换。
5. 复跑并留证：定向契约测试、全量非硬件 pytest、ruff、mypy strict、package import、`verify.py`、
   tracked + untracked 文本/diff 检查。

---

审查结束后立即停止。除本报告 `docs/reports/ISSUE_009_REVIEW_REPORT_R2.md` 外，
**审查者未修改项目任何文件**（未改实现、测试、manifest、计划、M02 状态、Git 状态；
未 commit / push / merge / 切分支 / reset）。
审查期间运行的 pytest / ruff / mypy 仅更新既有 ignored 缓存目录
（`.pytest_cache/`、`.ruff_cache/`、`.mypy_cache/`、`__pycache__/`），
均已被 `.gitignore` 覆盖，不得进入提交；探针脚本位于系统临时目录，未在项目内留下任何文件。
