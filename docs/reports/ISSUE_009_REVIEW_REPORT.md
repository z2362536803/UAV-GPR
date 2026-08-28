# ISSUE-009 独立审查报告

日期：2026-08-28（round 1）
审查者：DeepSeek Harness AgentTeams `reviewer`（非本轮执行者）
审查对象：ISSUE-009「规范逐道 raw 哈希与黄金向量」
审查标准：`docs/ISSUE_REVIEW_STANDARD.md` v1.0

## 1. 审查结论

**FAIL（round 1；需要最小修复后重新独立审查，不得合并或进入 ISSUE-010）**

主 framing、4 个黄金向量、常规布局/字节序规范化、长度 framing、字段变化敏感性、GNSS 排除和
64 位小写 hex 输出均有代码与独立实测证据；执行者声称的 48 个定向测试、349 个全量非硬件测试、
Ruff、mypy、package import、`verify.py` 和 `git diff --check` 也均可复现。

但独立反例发现两个阻止验收的数据契约缺陷：

1. `np.uint64([2, 1])` 形式的下降频率轴因无符号 `np.diff` 下溢而被错误接受，直接违反
   “频率轴严格递增、违规 fail-closed”的冻结契约；
2. `RawHashSpec` 没有拥有不可变的频率轴快照，调用方修改源数组或直接修改公开数组会改变
   已构造对象的 digest 和 `__hash__`。

此外，`uint64be` 边界与结构化错误契约、`from_dict` 严格性存在 P2 缺陷；实现也没有按最高级项目规则
和已写计划切到独立分支，而是把未提交实现直接留在 `main`。因此至少存在 P1、关键数据验收失败，
依据审查标准第 12 节不得判 `PASS` 或 `PASS WITH CONDITIONS`。

## 2. 自动识别的审查范围

| 项 | Git / 文档事实 |
|---|---|
| Issue | ISSUE-009「规范逐道 raw 哈希与黄金向量」；`docs/issues/M02_STORAGE.md:42-77` |
| 直接依赖 | ISSUE-004～006；实际接口见 `identifiers.py`、`channels.py`、`frequency.py`、`metadata.py`；ISSUE-008 另提供 64 ASCII 存储列兼容性 |
| 声称状态 | t2 声称实现完成、门禁全绿、未 commit/push，等待独立审查 |
| 当前分支 | **`main`**；未创建计划中的 `feat/issue-009` |
| 目标分支 | `main`（计划与项目规则均要求从独立 Issue 分支交付到该目标分支） |
| 审查基线 | HEAD = 本地 `main` = 本地 remote-tracking `origin/main` = merge-base = `e8525080fc9b5aa00ff24c252ba972809d09b604` |
| ISSUE-009 提交 | 无；HEAD 仍是 `e852508`（ISSUE-008 merge），因此无可列出的 ISSUE-009 提交作者、时间或父子关系 |
| t2 修改文件 | `docs/DATA_FORMAT.md`（tracked M，+41）；`src/uav_gpr/core/__init__.py`（tracked M，+12）；`src/uav_gpr/core/raw_hash.py`（untracked，444 行）；`tests/contract/test_raw_trace_hash.py`（untracked，780 行）；`tests/contract/raw_trace_hash_golden.json`（untracked，309 行） |
| t1 附带文件 | `docs/plans/2026-08-28-issue-009-raw-hash.md`（untracked，226 行）；`docs/reports/ISSUE_009_BASELINE_CONFIRMATION.md`（untracked，110 行） |
| 运行时目录 | `.agent-teams/`（untracked，AgentTeams 状态；不是项目交付物，不得提交） |
| 声称测试 | 定向 48；全量非硬件 349/1 deselected；Ruff；mypy strict 30 files；package import；`verify.py`；`git diff --check` |
| 排除项 | HDF5 writer/reader、整文件 hash、transport、v1、处理、UI；实际 diff 未发现这些范围的实现 |

范围判定依据为 t1/t2 完成报告、基线确认单、实施计划、Git HEAD/状态/完整工作树差异和实际文件内容。
虽然没有 ISSUE-009 提交，但基线固定为 `e852508`，工作树只有上述 7 个项目文件和团队运行时目录，
因此本轮审查范围可唯一确定，不构成 `BLOCKED`。

## 3. 主要问题（P0 → P3）

### P0

无。

### P1-01：下降的无符号频率轴可绕过严格递增校验

- **所属 Issue**：ISSUE-009
- **文件与行号**：`src/uav_gpr/core/raw_hash.py:106-134`，核心条件在 `:129`；契约见
  `docs/DATA_FORMAT.md:197,202-208`、`docs/issues/M02_STORAGE.md:63-67`。
- **触发条件**：传入 `np.array([2, 1], dtype=np.uint64)` 及 shape 匹配的 raw。代码先在原 dtype 上执行
  `np.diff(raw) > 0`；无符号减法下溢为大正数，条件错误通过，之后才转换为 `<f8`。
- **实际证据**：独立探针没有收到 `NON_INCREASING_AXIS`，反而得到 digest
  `aa7180b39134beda86c792d1f919cdc843c11c7134735626bfd841bf64d28bd2`。
- **实际影响**：非严格递增 axis 被当成规范输入并形成可传播的 raw 身份，破坏 fail-closed、axis 契约和
  后续空地对账前提；同类风险也包括有符号整数 `np.diff` 溢出和转换后精度坍缩未复验。
- **违反要求**：DATA_FORMAT 5.1 明确要求“按一维 float64、严格递增校验”且
  “非严格递增 → `non_increasing_axis`”；这是关键验收路径。
- **最小修复方向**：先构造规范 `<f8` 临时视图，再在规范值上检查有限性与严格递增；补充 unsigned 下降、
  signed 极值溢出及转换后相邻值坍缩的失败测试；保持输入数组不变。

### P1-02：`RawHashSpec` 的频率轴不是不可变自有快照

- **所属 Issue**：ISSUE-009
- **文件与行号**：`src/uav_gpr/core/raw_hash.py:278-313`（尤其 `:305`）；
  digest/哈希使用处 `:315-348`。
- **触发条件**：用可写 C-contiguous `<f8` 数组构造 `RawHashSpec`。`np.ascontiguousarray` 会直接返回该数组；
  构造后修改调用方源数组，或执行 `spec.frequencies_hz[...] = ...`。
- **实际证据**：独立探针得到 `spec.frequencies_hz.flags.writeable == True`；修改源 axis 后
  `spec.compute()` 的 digest 改变。
- **实际影响**：标注为 frozen、self-contained 的 hash 输入载体在验证后仍可改变身份；`__hash__` 也会随数组
  内容变化，作为 dict/set key 时会破坏 Python 哈希不变量。这与原始数据/轴由对象拥有且不可恢复为可写的
  项目数据规则冲突。
- **违反要求**：`AGENTS.md:51-63`、`docs/DATA_MODEL.md:6-12`；模块自身在
  `raw_hash.py:280-285` 声称 immutable carrier，t2 完成报告声称“不可变快照”。
- **最小修复方向**：对频率轴采用与 raw data 相同的 bytes-backed owned snapshot，确保源数组别名隔离且
  `writeable=False`；增加源数组修改、公开数组直接写、`setflags(write=True)`、digest/hash 稳定性测试。

### P1-03：ISSUE-009 直接在 `main` 工作树开发，未使用独立分支

- **所属 Issue**：ISSUE-009（Git/交付）
- **文件与行号**：`AGENTS.md:170-176`、`CONTRIBUTING.md:10-16`；执行者自己的计划
  `docs/plans/2026-08-28-issue-009-raw-hash.md:48-55` 也明确写“实现阶段迁移至 feat/issue-009，main 保持干净”。
- **触发条件/证据**：`git symbolic-ref --short HEAD` 返回 `main`；`git branch -vv` 仅有
  `main` 与 `feat/issue-008`；所有 ISSUE-009 文件作为 `main` 的未提交工作树改动存在。
- **实际影响**：违反最高级项目规则，交付边界不独立；在人工处理前 `main` 始终为脏工作树，增加误混入、
  覆盖或无法安全拆分的风险。
- **最小修复方向**：在不丢失当前改动、不 commit/push 的前提下创建/切换到 `feat/issue-009`，复核基线仍为
  `e852508`、范围仅包含本 Issue 文件；由执行者处理，审查者不代为修改 Git 状态。

### P2-01：固定 `uint64be`/结构化错误边界没有完整 fail-closed

- **所属 Issue**：ISSUE-009
- **文件与行号**：`src/uav_gpr/core/raw_hash.py:78-80,165-199,232-255`；文档契约
  `docs/DATA_FORMAT.md:193-205`。
- **触发条件与事实**：
  - `trace_index=2**64` 通过 `_require_trace_index`，到 `struct.pack(">Q", value)` 才抛
    `struct.error`，不是稳定 `DomainError`；
  - 直接向公共 API 传非规范 UUID 字符串会由 `MissionId`/`TraceUid` 抛 `ValueError`，不是文档列出的
    `DomainError(INVALID_UUID/INVALID_ARGUMENT)`。
- **影响**：调用层无法只按稳定错误码分支，且 framing 的 uint64 上界没有在领域边界显式表达。
- **最小修复方向**：在 `_require_trace_index` 检查 `0 <= value <= 2**64-1`（如还需与 ISSUE-008 `<i8`
  对齐，应先明确并测试更窄上界）；将字符串 ID 解析错误转换为带稳定 code 的 `DomainError`，补精确错误码测试。

### P2-02：`RawHashSpec.from_dict()` 并非完成报告声称的“严格校验”

- **所属 Issue**：ISSUE-009
- **文件与行号**：`src/uav_gpr/core/raw_hash.py:350-444`，尤其 `:374-396` 使用 `.get()` 读取已知字段，
  从未核对精确键集合。
- **触发条件/证据**：向合法 payload 增加 `"unexpected": "silently ignored"`，
  `RawHashSpec.from_dict()` 成功返回对象；未知字段被静默忽略。`data_shape` 元素也先 `int(...)` 强制转换，
  没有先严格限制为非 bool 整数和合法边界。
- **影响**：损坏或未来版本 payload 可被当前版本静默降级解释，不符合 versioned/fail-closed 可持久化契约；
  也使“严格校验”完成声明失实。
- **最小修复方向**：冻结顶层和 channel 子对象的精确键集合；拒绝未知/缺失键；严格校验 shape 类型、非负/范围、
  乘积与 channel/frequency 契约；为 JSON 载荷加入显式 spec/hash version 或明确 v1-only 解析规则并测试未知版本拒绝。

### P3-01：Issue 状态文档仍写 `Planned`

- **所属 Issue**：ISSUE-009
- **文件与行号**：`docs/issues/M02_STORAGE.md:42-45`；计划更新承诺见
  `docs/plans/2026-08-28-issue-009-raw-hash.md:205-210`。
- **事实与影响**：实现已完成且 round 1 已审查，但状态仍为 `Planned`，与实际流程不符。因本轮结论 FAIL，
  应按负责人流程保持/恢复 `In progress`，修复通过后再置 `Review`；不能直接标 `Done`。
- **最小修复方向**：由执行者/项目负责人按真实阶段更新，不由只读审查者代改。

## 4. 逐 Issue 验收矩阵

### ISSUE-009：规范逐道 raw 哈希与黄金向量

| 验收标准 | 状态 | 精确代码证据 | 独立实测证据 | 问题或限制 |
|---|---|---|---|---|
| 等价内存布局得到相同 digest | PASS | `raw_hash.py:207-215` 以 `np.ascontiguousarray(..., dtype="<f8"/"<c16")` 固定连续布局；`test_raw_trace_hash.py:212-263` | 独立探针对同一 raw 的 C-order、Fortran-order 对照相等；定向测试 48/48 通过 | 只证明相同规范数值的布局等价，不覆盖 P1-01 的非法 axis |
| 本机/输入字节序不同但规范值相同，digest 相同 | PASS | `raw_hash.py:106-134,137-162,207-215` 显式转 `<f8`/`<c16` | 独立 little-endian vs big-endian axis/raw 对照相等；提交测试 `:238-248` 通过 | 无 |
| 任一合法 identity/axis/channel/raw 字段变化会改变 digest | PASS | framing 拼装 `raw_hash.py:239-255`；变化测试 `test_raw_trace_hash.py:279-381` | 独立修改 mission_id、trace_index、trace_uid、channel_id、axis 值、raw 值，6/6 digest 均变化 | SHA-256 的数学碰撞不在测试证明范围；此处指契约样本敏感性 |
| 简单拼接歧义由长度 framing 消除 | PASS | `_frame_text()` `raw_hash.py:202-205`；channel count/逐项长度 `:245-250`；文档 `DATA_FORMAT.md:178-200` | 独立构造 naive channel bytes 同为 `abc` 的 `["ab","c"]` / `["a","bc"]`，framed digest 不同 | 提交测试的 channel 反例真实覆盖；mission UUID 反例本身不是同字节重分割，但不影响该项通过 |
| 非规范 shape/dtype/ID fail-closed | PARTIAL | shape/dtype `raw_hash.py:106-162`；ID `:180-199`；提交测试 `test_raw_trace_hash.py:469-615` | 字符 raw 与错误 shape 被 `DomainError` 拒绝；坏 UUID 被拒绝；但坏 UUID 为 `ValueError`，`2**64` index 为 `struct.error` | 拒绝动作存在，但不满足 DATA_FORMAT 5.1 声称的结构化 DomainError/uint64 边界，见 P2-01 |
| 频率轴必须有限且严格递增，违规 fail-closed | **FAIL** | `raw_hash.py:124-134` 试图校验，但在 canonical float64 转换前对原 dtype `np.diff` | 独立 `np.uint64([2,1])` 反例被接受并产出 digest | P1-01，关键数据验收失败 |
| 输入规范化不修改调用方数组；hash carrier 保持不可变 | **FAIL** | 即时 compute 只读 `raw_hash.py:232-255`；`RawHashSpec` 快照 `:294-313` | 原始 `compute_raw_trace_sha256` 不改输入（提交测试 `:760-780` 通过）；但独立 alias 探针证明 spec axis 可写且源修改改变 digest | 即时函数部分满足；公共 `RawHashSpec` 不满足，见 P1-02 |
| GNSS 明确不进入 raw hash | PASS | 公共签名/拼装 `raw_hash.py:218-255` 没有 GNSS；文档 `DATA_FORMAT.md:199` | 独立构造无 GNSS/有有效 GNSS 的两份 metadata，取相同 identity/axis/channel/raw 后 digest 相同；函数签名无 `gnss` | 无 |
| versioned framing、4 个合成黄金向量及 expected digest 一致 | PASS | 常量 `raw_hash.py:68-75`；拼装 `:239-255`；manifest 4 vectors `raw_trace_hash_golden.json:13-307` | 不 import 生产 hash 函数的独立 `hashlib+struct+numpy` builder 对 4 个 expected digest 复算 4/4 一致 | 仓库没有保留完成报告所称独立生成器源码，因此“生成过程”只能记为未发现反证；数值本身已独立验证 |
| 输出 64 位小写 hex 且与 ISSUE-008 列兼容 | PASS | `hashlib.hexdigest()` `raw_hash.py:255`；校验 `:258-270`；存储列 `rcscan_v2.py:304,679-681` | 提交兼容测试 `test_raw_trace_hash.py:623-669` 通过；独立 import/probe 成功 | 无 |
| core 依赖边界与排除项 | PASS | `raw_hash.py:49-62` 仅 stdlib、NumPy 和 core；core isolation 守卫 `test_core_isolation.py:8-42` | 全量非硬件测试、Ruff、mypy、import 均通过；Git diff 未见 HDF5 writer/reader、transport、UI、处理实现 | Git 分支交付边界另见 P1-03 |

**ISSUE-009 单项结论：FAIL。** 只有一个 Issue，不存在可拆分合并的其他合格 Issue；黄金/文档不能与存在
关键 fail-closed 缺陷的生产 API 分开作为完整 ISSUE-009 合并。

## 5. Git 与交付检查

- 当前仓库根：`/mnt/d/博士任务/无人机软件/UAV-GPR`；工作区根替换与任务声明一致。
- 当前分支是 `main`，HEAD/本地 `origin/main`/merge-base 均为 `e852508`；没有 ISSUE-009 commit、staged diff、
  merge 或 PR。本地 remote-tracking ref 不能代替联网证明远端实时状态，但未发现 push/merge 反证。
- `git branch -vv` 没有 `feat/issue-009`，与 `AGENTS.md`、`CONTRIBUTING.md` 和实施计划冲突（P1-03）。
- tracked diff 只有 `DATA_FORMAT.md` 与 `core/__init__.py`；新增生产模块、测试、manifest、计划和基线单均为
  untracked。t2 声称的 5 个实现文件与 Git 事实一致，t1 的 2 个文档需作为同 Issue 附带件管理。
- `git diff --check` exit 0，但该命令本身不检查 untracked 新文件；审查者另对全部 7 个项目文件执行 UTF-8、
  trailing whitespace、final newline 探针，全部通过。
- 测试后没有新增 tracked/untracked 项目文件；仅 `.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`、
  `__pycache__/`、`src/uav_gpr.egg-info/` 等既有 ignored 生成物可见，不得进入提交。
- 未发现日志、密钥、本地配置、实测数据、参考仓库文件或硬件产物；黄金数据为合成值且不含 GNSS。
- reflog 最近 ISSUE-009 期间没有 checkout 到新分支、commit、rebase 或 amend；较早存在
  `reset: moving to origin/main`（2026-08-27），只能按字面报告，不能据此断言 `reset --hard` 或强推。
- `docs/issues/M02_STORAGE.md` 未在 diff 中，状态仍为 `Planned`（P3-01）。
- 本审查唯一允许写入的项目文件是本报告 `docs/reports/ISSUE_009_REVIEW_REPORT.md`；未修改实现、测试、
  manifest、计划、基线或 Git 历史。

## 6. 测试与验证结果

环境：WSL2 Linux `6.18.33.2-microsoft-standard-WSL2`，Python 3.12.3（`/usr/bin/python3`），
NumPy 2.5.2，pytest 8.4.2，mypy 1.20.2，Ruff 0.16.4。

### 6.1 执行者声称的门禁独立复跑

| 实际命令 | 退出码 | 实际结果 |
|---|---:|---|
| `python3 -m pytest tests/contract/test_raw_trace_hash.py -q` | 0 | **48 passed**，0 failed，0 skipped，0 deselected；0.26 s |
| `python3 -m pytest -m "not hardware and not slow" -q` | 0 | **349 passed, 1 deselected**，0 failed，0 skipped；3.96 s |
| `python3 -m ruff check .` | 0 | `All checks passed!` |
| `python3 -m mypy src` | 0 | `Success: no issues found in 30 source files`（strict 配置来自 `pyproject.toml:69-75`） |
| package/hash import probe（`uav_gpr`, `core`, `positioning`, `storage` 及 5 个新导出） | 0 | Python 3.12.3；`package import ok 1` |
| `python3 tools/quality/verify.py` | 0 | pytest 349/1 deselected + Ruff + mypy + package import，`all gates passed` |
| `git diff --check` | 0 | 无 tracked diff whitespace 错误 |
| 7 个 scoped 文件 UTF-8/trailing-whitespace/final-newline 独立探针 | 0 | 7/7 通过（补足 untracked 文件不被 `git diff --check` 覆盖的限制） |

常规门禁数字与 t2 完成报告一致；门禁全绿不替代第 6.2 节明确需求反例。

### 6.2 独立 framing/反例探针（未复用生产 builder）

单个合成探针脚本最终 exit **1**，因为准确发现 4 个失败条件；脚本在退出前完成全部项目并逐项打印：

| 探针 | 结果 |
|---|---|
| 独立 `hashlib + struct + numpy` reference builder 复算 4 个黄金 expected SHA256 | PASS，4/4 与 manifest 一致（`1a519053...`、`9c2081f4...`、`8c103dda...`、`b1a60d2b...`） |
| C/Fortran 内存布局等价 | PASS |
| little/big endian 输入等价 | PASS |
| naive bytes 同为 `abc` 的 `["ab","c"]` / `["a","bc"]` framing 歧义反例 | PASS，digest 不同 |
| mission_id/index/trace_uid/channel_id/axis/raw 六类字段篡改 | PASS，6/6 digest 改变 |
| 非法 UUID、字符串 raw dtype、错误 raw shape | PASS，均拒绝（UUID 错误类型限制见 P2-01） |
| GNSS absent vs valid GNSS metadata 对照 | PASS，digest 相同且 API 无 GNSS 参数 |
| `np.uint64([2,1])` 下降轴 | **FAIL**，错误接受并生成 digest（P1-01） |
| `trace_index=2**64` | **FAIL**，抛 `struct.error` 而非结构化 `DomainError`（P2-01） |
| `RawHashSpec` 频率轴别名/只读性 | **FAIL**，`writeable=True` 且源修改改变 digest（P1-02） |
| `RawHashSpec.from_dict` 增加未知顶层字段 | **FAIL**，未知字段被接受（P2-02） |

## 7. 报告与事实差异

1. **可复现**：t2 声称的 5 个实现文件、行数、4 个黄金向量、主要 framing 公式、定向 48、全量 349/1、
   Ruff、mypy 30 files、import、`verify.py` 和 tracked `git diff --check` 均与仓库/实测一致。
2. **可复现**：4 个 expected digest 由审查者的独立 reference builder 4/4 重算一致；因此黄金数值不是仅靠生产函数
   自证。执行者所称“另一个独立生成脚本”的过程因脚本未入库无法事后完全证明，记为未发现反证。
3. **不真实**：完成报告称 `RawHashSpec` 为“严格校验+不可变快照”；独立反例证明频率轴可写、存在源别名，
   且 `from_dict` 静默接受未知字段（P1-02/P2-02）。
4. **不真实**：完成报告称 9/9 验收全部 PASS、无风险；unsigned 下降 axis 被接受，关键 fail-closed 验收失败。
5. **不一致**：基线单/实施计划声称实现阶段切换 `feat/issue-009`、main 保持干净；Git 事实是直接在 `main`
   留下所有未提交实现（P1-03）。
6. **有范围限制的真实声明**：`git diff --check` 的确 exit 0，但因新增文件仍 untracked，该命令没有覆盖主要新增
   production/test/manifest 文件；审查者补充文本卫生探针后这些文件也通过。
7. **未发现反证但无法完全独立验证**：“先写失败测试再实现”“未 push”“两个参考项目未动”等过程声明。
   本地确无 ISSUE-009 commit/merge，当前 remote-tracking ref 未变化，且 Issue 无参考迁移代码。
8. **遗漏计划项**：计划要求把 M02 状态从 `Planned` 更新到真实阶段，但实际未修改（P3-01）。

## 8. 剩余风险

- 本 Issue 是纯 core/契约能力，真实硬件不适用；无硬件不阻止结论。
- manifest 中记录了 generator 名称/版本/seed，但没有纳入可执行的独立生成器源码；expected digest 已由本审查
  独立重算，数值风险已显著降低，生成过程声明仍只能记为不可完全追溯。
- `RawHashSpec` JSON 没有显式携带 hash/spec version；若未来 `RAW_HASH_VERSION` 递增，旧 payload 的解析策略需在
  本次严格反序列化修复中冻结，避免用新 framing 静默重算旧载荷。
- raw complex NaN/Inf 的接受/规范字节语义没有在 ISSUE-009 文档或测试中明确。当前 M02 验收只明确 dtype/shape，
  本轮不把它提升为额外阻断项；后续若允许非有限 raw，应冻结 NaN payload/signed-zero 的跨实现语义。
- 所有交付仍未提交且位于脏 `main`；修复前存在工作树误操作/丢失风险。

## 9. 合并建议

**不得合并、不得标记 ISSUE-009 Done、不得开始 ISSUE-010。**

建议执行者仅按第 10 节完成最小修复，保留现有 v1 framing 和 4 个 expected digest（除非修复证明当前合法向量
framing 有误；目前没有该证据），随后重新运行定向/全量/静态/import/diff 门禁，并由非执行者进行 round 2
独立审查。一个 Issue 内不存在可安全拆分为“先合并生产模块、以后修复”的部分；基线/计划文档可保留，但不构成
功能验收通过。

## 10. 最小修复清单

1. **关闭 P1-01**：在 `<f8` 规范化之后验证 finite/strictly increasing；新增 unsigned 下降、signed 极值溢出、
   转换后相邻值坍缩反例，均须稳定 `DomainError(NON_INCREASING_AXIS/NON_FINITE_AXIS)`。
2. **关闭 P1-02**：把 `RawHashSpec.frequencies_hz` 变为 bytes-backed 自有只读快照；增加源别名修改、直接写入、
   `setflags(write=True)` 和 `__hash__/compute` 稳定性测试。不得改变即时 compute 的合法 v1 digest。
3. **关闭 P2-01**：显式校验 framing 整数的 uint64 上下界；把公共 API 的非规范 UUID 转换为文档冻结的结构化
   `DomainError`，测试精确 error code，不只测试“抛了某个异常”。
4. **关闭 P2-02**：让 `RawHashSpec.from_dict` 拒绝未知/缺失/错误类型字段，严格校验 nested channels 与 shape；
   明确并测试 spec/hash version 解析策略。
5. **关闭 P1-03/P3-01**：不丢失改动地切到 `feat/issue-009` 独立分支；按真实状态更新 M02（修复中为
   `In progress`，复审前为 `Review`，只有人工验收后才 `Done`）。不 commit/push，除非项目负责人另行明确授权。
6. 复跑并报告：定向契约测试、全部非硬件 pytest、Ruff、mypy strict、package import、`verify.py`、
   tracked + untracked 文本/diff 检查，以及本报告第 6.2 节全部反例。

审查结束后立即停止；除本报告外未修改项目内容，等待项目负责人/自动修复流程决定。
