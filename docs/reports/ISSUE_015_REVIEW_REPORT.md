# ISSUE-015 独立复审报告

- 日期：2026-08-30
- 审查者：reviewer（AgentTeams `uav-gpr-issue-015-simulated-backend`，任务 t3，reviewedTask=t2，attempt bd74e9b5）
- 审查对象：t2 交付的 ISSUE-015「AcquisitionBackend 契约与确定性模拟器」
- 审查标准：docs/ISSUE_REVIEW_STANDARD.md v1.0（固定格式 §13）
- 审查性质：全程只读——未修改实现/测试/计划/M03/Git 状态，未 commit/push/merge；变异探针在系统临时目录复制树（`/tmp/iss015_probes`，运行后已被系统清理）中执行，项目内零残留（除本报告外，审查前后 `git status --porcelain=v1 -b` 逐字节一致）

## 1. 审查结论

**FAIL**（存在 1 项 P1 阻塞问题：`configure()` 未拒绝 acquire 在途时的并发重配，实测可静默破坏数据契约与确定性；另有 3 项 P3 观察。按标准 §12，存在 P1 时整批不得判定 PASS，需最小修复后复审）。

- 单 Issue 结论：ISSUE-015 **不通过**（主体功能完成且测试/门禁全部复现，但生命周期状态机存在一处 fail-open 竞态）。
- 依赖影响：无。ISSUE-015 未改动任何既有模块公共语义（ISSUE-004/005/006 回归全绿）。
- 阻断范围：仅 P1-01 一项（backend 基类 `configure()` 增加 busy 守卫 + 1 条回归测试），修复面极小、完全落在本 Issue inScope 内；修复后可按 §14 复审。

## 2. 自动识别的审查范围

从 t2 完成报告、t1 基线单、Git 与工作树交叉识别：

| 项 | 识别结果 | 证据 |
|---|---|---|
| Issue | ISSUE-015（M03_ACQUISITION.md L5–40，状态行已置 `Review`） | docs/issues/M03_ACQUISITION.md L7；docs/issues/README.md L82 |
| 依赖 | ISSUE-004/005/006（tracked 于 main；M01 状态行滞后为已知事实） | t1 基线单 §3.1/3.2/3.3；git log main（45c5657/952883e/b11e741f/22b0b0f/bcef87c/0ddbd81） |
| 分支/基线 | `feat/issue-013` @ `0903749`（= main；merge-base == HEAD == main；无新增提交） | git rev-parse/merge-base/reflog 实测 |
| 改动文件（ISSUE-015 范围） | ① `src/uav_gpr/acquisition/backend.py`（695 行，新）② `tests/contract/test_acquisition_backend.py`（595 行/27 用例，新）③ `docs/plans/2026-08-30-issue-015-simulated-backend.md`（155 行，新）④ `docs/issues/M03_ACQUISITION.md` 仅 015 状态行 L7（1 行 diff） | git status/diff 实测 |
| 工作树既有的非本 Issue 产物 | ISSUE-013/014 在制文件 12 项（`rcscan_v1.py`、`test_rcscan_v1.py`、golden、`inventory/`、`test_inventory.py`、013/014 计划与报告 4 份、M02 两处状态行、t1 的 015 基线单）——核查前后均存在，t2 未触碰 | t1 基线单 §3.5；git diff M02 仅 2 处状态行（013/014，先于 015 开工即存在） |
| 声明状态 | 实现+测试完成，等独立复审；不 commit/push/merge；changedPaths == inScope 4 路径 | t2 完成报告；git reflog 无新提交；工作树与 inScope 对照一致 |

## 3. 主要问题（P0→P3）

**P0：无。P2：无。**

**P1-01（阻止合并）：`configure()` 未拒绝 acquire 在途时的并发重配——实测静默破坏数据契约与确定性**

- 位置：`src/uav_gpr/acquisition/backend.py:221-241`（configure 无 busy 检查；对比同文件 acquire 的 busy 结构化拒绝 `:258-264`）。
- 触发条件：`SimulatedBackend` 带 `delay_s`/`block_until_cancelled` 故障时，一线程在 `acquire()` 阻塞等待期间，另一线程调用 `configure(new_config)`（当前状态机静默接受，无任何异常）。
- 实际影响（变异探针 B 实测，全部可复现）：
  1. 在途 acquire 捕获的是旧 config（`applied` 快照），但产出 sweep 时读取的是**新 rng** 与**重置后的 `_trace_index`**（`_do_acquire` `:529-555` → `_produce_sweep` `:580-591`）——旧 config（11 点）的 sweep 数据取自新 config 的随机流，与干净基线逐值不等（确定性验收被打破）；
  2. `_do_configure` 重置尝试计数（`:524-526`）→ attempt-0 故障计划在“新任务”上**再次触发**（探针实测 delay_s={0:0.3} 重放 0.30s）——“错误按计划在确定道触发”被破坏；
  3. 新任务首道 `trace_index` 为 **1** 而非 0（索引 0 被在途旧 sweep 盗用）——任务内单调序号契约被静默破坏。
- 违反的要求：M03 L29「非法生命周期被结构化拒绝」（并发驱动设备属非法）；ACQUISITION.md §4「任务开始后配置冻结；不允许静默改变频率轴/点数/通道」、§7「不并发驱动同一设备」；AGENTS.md §8「配置在任务开始后冻结」；M03 L28 确定性验收（该交叠下不成立）。状态机已对 acquire 实现同型守卫（busy=True），configure 缺位属设计缺口。
- 最小修复方向：`configure()` 持锁段内增加 `if self._acquiring: raise BackendStateError(..., busy=True)`（与 `:258-264` 同型）；补 1 条回归测试（`block_until_cancelled`/`delay_s` + 线程 + `acquire_started` 事件：在途 configure 被结构化拒绝、在途 acquire 不受影响）；计划文档 §5.2 状态表同步标注「configure 与 acquire 互斥」。

**P3-02（低）：close() 后立即 open()（重开）会使被唤醒的阻塞 acquire 误报为 cancelled**

- 位置：`src/uav_gpr/acquisition/backend.py:306-314`（`_raise_interrupted` 按唤醒时刻的当前状态分类）。
- 触发条件：`block_until_cancelled` 故障下 acquire 阻塞 → `close()` → 立即 `open()`（合法：close 后允许重开）→ 旧 acquire 被唤醒时 state 已是 `OPEN`。
- 实际影响：变异探针 C 实测 10/10 次抛 `BackendCancelledError` 而非契约承诺的 `BackendClosedError`（计划 §5.6「close 置位 cancel 事件并置 CLOSED；被阻塞的 acquire 被唤醒后检查状态抛 BackendClosedError」）。线程不残留、不产出 sweep（无数据风险），仅错误分类失真；且重开先于 worker drain 属调用方误用路径（ISSUE-017 应保证 drain 后重开）。
- 最小修复方向：记录并文档化「重开必须在在途 acquire 全部终止后进行」，或在 `_wait_cancellable`/`_raise_interrupted` 引入 close 纪元（epoch）判别；不阻止合并。

**P3-03（低）：同一 mission_id 重配会再生出相同 `trace_uid`（index 0 复用）**

- 位置：`src/uav_gpr/acquisition/backend.py:612-617`（`uuid5(NAMESPACE_URL, mission:index)` 确定性生成）与 `:523-526`（重配重置 `_trace_index`）。
- 触发条件：对同一 `SimulatedBackend`（构造期固定 `mission_id`）再次 `configure` 后采集。
- 实际影响：变异探针 H 实测两次「任务」的 index-0 sweep `trace_uid` 完全相同——与 AGENTS.md §4「trace_uid 全局唯一」及 DATA_MODEL §2「mission_id + trace_index 必须唯一」的预期冲突。属确定性设计（uid5）与重配语义（新任务）的固有张力；实际生产流程中每个任务应有新 `mission_id`（由未来控制器保证），当前模拟器无法自行强制。
- 最小修复方向：在 backend.py 文档/计划中显式声明「reconfigure 视为新任务，调用方必须更换 mission_id（ISSUE-017/043 契约）」，必要时在 `configure()` 校验「同一 mission_id 已产出过道时重配需显式参数」；不阻止合并。

**P3-04（低）：`open()` 先置 OPEN 再执行 `_do_open`，钩子失败不回滚**

- 位置：`src/uav_gpr/acquisition/backend.py:206-219`。
- 触发条件：真实后端（未来 ISSUE-019）`_do_open` 抛异常时 state 停留在 OPEN。
- 实际影响：模拟器 `_do_open` 不抛异常，当前不可达；属基类契约硬化建议（try/except 回滚 CLOSED 或文档化钩子失败语义），留给真实后端 Issue 处理；不阻止合并。

## 4. 逐 Issue 验收矩阵

口径：M03_ACQUISITION.md L26–30 三条验收 + L17–20 范围/排除 + 提示词必测项 + t1 基线单 §3.4 契约要点。代码证据精确到 `文件:行号`。

| # | 验收标准 | 状态 | 代码证据 | 测试证据 | 问题或限制 |
|---|---|---|---|---|---|
| A1 | 相同 seed/config/虚拟 clock 产生相同 raw | **PASS** | `backend.py:429-438`（seed+config_sha256 派生 rng）、`:523`（configure 时重置 rng）、`:586-590`（纯 rng 数据生成） | `test_acquisition_backend.py:340-347`（同 seed 逐值相等含 metadata/哈希）；变异探针 A（独立 clock 实例下 raw/哈希仍逐值相等） | 仅在串行使用契约下成立；并发 configure 竞态破坏（见 P1-01/A4） |
| A2 | 错误按计划在确定道触发 | **PASS** | `backend.py:533-548`（attempt 序号精确匹配；timeout→half→disconnect 顺序）；`:534`（attempt 只随 acquire 调用递增，失败尝试也计入） | `test_acquisition_backend.py:418-454`（timeout@1、half@0、disconnect@1 精确触发、不消耗 trace_index）；探针 E（timeout 后 attempt 已消耗） | 竞态下 attempt-0 故障可重放（P1-01 同源） |
| A3 | 单/双通道共用接口 | **PASS** | `backend.py:562-578`（设备支持通道的有序子集校验）、`:583-590`（shape=len(channels)×n_f） | `test_acquisition_backend.py:211-241`（单道 (1,11)、双道 (2,11)、双设备单道子集）；探针 G（逆序通道结构化拒绝且状态可恢复） | 无 |
| A4 | 非法生命周期被结构化拒绝 | **FAIL** | acquire 在途 busy 拒绝 `:258-264` ✓；但 configure 无同型守卫 `:221-241` ✗ | 既有用例仅覆盖 open/configure/acquire 的非法状态组合（`test:154-177`、`:532-543`）；**变异探针 B 实测在途 configure 被静默接受（ACCEPTED）** | **P1-01**：并发重配静默破坏 rng 流/trace_index/故障计划 |
| A5 | cancel/close 不遗留线程或等待 | **PASS** | `backend.py:275-286`（幂等 cancel/close；close 置位唤醒）、`:316-341`（等待不持锁、可取消、超时上界） | `test_acquisition_backend.py:477-543`（cancel/close/超时/并发 busy 全部 join 且 not alive）；探针 D（5 轮 cancel/close 风暴后线程数 1→1、无残留） | P3-02（close→open 竞态下错误分类失真，线程仍不残留） |
| A6 | 范围：open/configure/acquire/cancel/close、capabilities、requested/applied config | **PASS** | `backend.py:159-359`（ABC 状态机+可观测属性）、`:68-92`（Capabilities/AppliedConfig） | `test:134-203`（happy path、重开、重配重置）、`:369-388`（diff 缺省一致/IFBW 量化非空） | 无 |
| A7 | 范围：确定性多通道 S11/S22、真实 shape/axis/UTC+monotonic metadata | **PASS** | `backend.py:580-663`（envelope×复数噪声、config 频率轴、Clock 读 UTC+monotonic、首道间隔 None、后续 start-to-start） | `test:249-322`（shape/dtype/轴/时间/间隔/uid 唯一）、探针 A/F | 无 |
| A8 | 范围：故障注入 timeout/半道/配置拒绝/断开/延迟 + 可取消等待 | **PASS** | `backend.py:362-412`（SimulationFaults 严格校验）、`:529-555`（故障序→阻塞/延迟→生成）、断开 generation+1（`:543-546`） | `test:418-454`（三类故障）、`:391-410`（reject_config/不支持通道可恢复）、`:477-491`（延迟取消）、探针 E（延迟+超时 0.05s 上界） | 无 |
| A9 | 范围：资源所有权和幂等 close | **PASS** | `backend.py:279-286`（CLOSED 幂等 no-op、唤醒在途）；close/cancel 均不创建线程（模拟器无自有线程） | `test:180-190`（双 close、重开）、`:506-520`（close 唤醒+幂等） | 无 |
| A10 | 排除：不实现 scheduler/controller/Qt/HDF5/GNSS reader/LibreVNA | **PASS** | backend.py 仅 import stdlib+numpy+`uav_gpr.core`；无 serial/usb/socket/Qt/h5py 引用（grep 实测） | 无 hardware 标记；全量门禁通过（含 no_external_access 守卫） | 无 |
| A11 | 排除：不改 core 公共语义、不触碰 ISSUE-013/014 在制产物 | **PASS** | git diff：`src/uav_gpr/core/**` 零改动；M02 diff 仅 013/014 两处状态行（先于 015 开工即存在） | 审查前后 git status 一致（16 条目）；依赖定向回归 179 passed（t1 基线口径，全量 589 内） | 无 |
| A12 | 排除：不 commit/push/merge、不创建分支 | **PASS** | HEAD==main==merge-base==`0903749`；reflog 仅 checkout 记录，无 reset/rebase/amend/强推 | git log/reflog 实测 | 无 |
| A13 | 测试禁固定 sleep，用事件/屏障/join | **PASS** | 测试文件无 `time.sleep`（grep 实测，仅文档字符串提及） | `acquire_started.wait(2.0)`/`thread.join(2.0)` 有界等待；`test:523-529` 用 0.05s 真实超时路径 | 无 |
| A14 | GNSS/无 GNSS 场景元数据真实性 | **PASS** | `backend.py:629-636`（关→gnss_match=None+GNSS_MISSING+DEGRADED；开→确定性 fix/match+NOMINAL）、`:665-695`（midpoint 精确匹配、age_s=0、usable_for_map=True） | `test:551-577`；探针 F（同 seed 独立 clock 下 GNSS 坐标逐值相等、异 seed 不同） | 无 |
| A15 | 错误使用 core 结构化错误（DomainError+code+reason） | **PASS** | `backend.py:95-157`（BackendError 族：code=INVALID_ARGUMENT+稳定 reason 判别键+类型化子类） | `test:585-595`（code/reason/to_dict 校验） | 无 |

依赖接口兼容性（标准 §7）：`MissionConfig.frequency_axis_hz`/`config_sha256`、`ConfigDiff.compute/is_identical/changed_fields/field`、`Clock`（runtime_checkable）、`TraceMetadata` 全部字段名、`RawHashSpec.compute`、`GnssMatch`/`GnssFix` 构造参数、`DomainError.to_dict` 均实测存在且语义与 ISSUE-003～006/009 契约一致（grep + 全量门禁 589 passed 复证）。

## 5. Git 与交付检查

| 检查项 | 结果 |
|---|---|
| 当前分支/基线 | `feat/issue-013` @ `0903749`，`main` 同指（merge-base == HEAD）；无新提交；reflog 仅 checkout，无 reset/rebase/amend/强推 |
| 提交混入/拆分 | 无提交产生；交付物为 3 个未跟踪新文件 + 1 行状态行，单 Issue 可独立理解 |
| 范围外修改 | 无。M03 diff 仅 ISSUE-015 状态行 L7（`Planned→Review`）；M02 两处状态行为 ISSUE-013/014 在制改动（先于 015 开工）；其余未跟踪文件均为 013/014/t1 既有产物 |
| 缓存/日志/密钥/实测数据 | 无。`.pytest_cache/.mypy_cache/.ruff_cache/__pycache__` 均在 .gitignore（git check-ignore 实测）；无数据文件/密钥/日志残留 |
| 契约变更与 ADR | 未改任何冻结契约/schema/协议；无新 ADR 需求（t1 基线单 §3.5-5 判定，合理） |
| 依赖顺序 | 015 依赖 004–006（均合入 main）；013/014 非 015 依赖（M03 L8、README L82），交付物与 013/014 在制文件零交集，可独立拆分合并 |

## 6. 测试与验证结果

环境：① WSL Ubuntu 24.04 / Python **3.12.3**（numpy 2.5.2、pytest 8.4.2）；② t2 同款 Windows venv `.venv\Scripts\python.exe`（Python **3.13.14**，WSL interop 调用）。工作目录 = 工作区根。两环境均复现 t2 声称数字。

| # | 命令（实际执行） | 实际结果 | t2 声称 | 判定 |
|---|---|---|---|---|
| 6.1 | `.venv/Scripts/python.exe -m pytest tests/contract/test_acquisition_backend.py -q` | **27 passed in 0.23s**（exit 0） | 27 passed（0.13s/0.19s） | 一致（时长属环境波动） |
| 6.2 | `python3 -m pytest tests/contract/test_acquisition_backend.py -q`（WSL 3.12.3） | **27 passed in 0.29s**（exit 0） | —（复审补充） | 通过 |
| 6.3 | `.venv/Scripts/python.exe tools/quality/verify.py` | **589 passed, 1 deselected in 241.46s**；ruff All checks passed；mypy 36 files；import ok；exit 0 | 589 passed/1 deselected（253.79s）；同 | 一致 |
| 6.4 | `python3 tools/quality/verify.py`（WSL 3.12.3） | **589 passed, 1 deselected in 112.45s**；全部通过；exit 0 | 基线 562+27=589 ✓ | 一致 |
| 6.5 | `python3 -m ruff check .` / `python3 -m mypy src` | **All checks passed!**（exit 0）/ **Success: no issues found in 36 source files**（exit 0；基线 35+backend.py=36 ✓） | 同 | 一致 |
| 6.6 | `python3 -c "import uav_gpr"` | import ok（exit 0） | package import ok | 一致 |
| 6.7 | `git diff --check` | clean（exit 0） | clean | 一致 |
| 6.8 | 变异探针（`/tmp/iss015_probes`：复制树 + `python3 -B probe.py`，8 项；运行后临时目录已被系统清理，项目内零残留） | A/D/E/F/G PASS；**B FAIL（P1-01 实证）**；C FAIL（10/10 误分类，P3-02）；H FAIL（trace_uid 重复，P3-03） | t2 未覆盖（任务要求复审补查） | 3 项发现见 §3 |

### 6.5 独立变异探针明细（系统临时目录复制树，零残留）

- **A 独立时钟确定性**：同 seed/config、两个不同初始时刻的独立 `ManualClock` → raw 数据与哈希逐值相等，metadata 时刻各随其钟——确定性不依赖共享时钟实例。
- **B 在途 configure 竞态（P1-01）**：`delay_s={0:0.3}` + 线程 acquire 阻塞期间调用 `configure(13点配置)` → **被静默接受**；旧 11 点 sweep 数据取自新 rng 流（与干净基线逐值不等）；attempt-0 延迟故障在新任务**重放 0.30s**；新任务首道 `trace_index=1`（索引 0 被盗用）。
- **C close→open 误分类（P3-02）**：`block_until_cancelled` + close 后立即 open，10/10 次唤醒抛 `BackendCancelledError`（契约承诺 `BackendClosedError`）；线程均正常终止、无残留、无 sweep 产出。
- **D 线程残留**：5 轮 cancel/close 风暴后 `threading.active_count()` 1→1，无存活 worker。
- **E 延迟+超时上界**：`delay_s={0:5.0}` + `acquire(timeout_s=0.05)` → `BackendTimeoutError`、实测 0.05s、attempt 已消耗、`acquiring` 清除。
- **F GNSS 确定性**：gnss_enabled + 同 seed 独立时钟 → GnssMatch 逐值相等（36.759964,114.407647 双端一致）；异 seed → 坐标不同。
- **G 逆序通道**：设备 (HH,VV) 配 (VV,HH) → `BackendConfigRejectedError`（channel_id=hh_s11），状态保持 OPEN 可恢复。
- **H 重配 trace_uid（P3-03）**：同 mission_id 重配后 index-0 的 `trace_uid` 与上一任务完全相同（`0cf05775-752c...` == `0cf05775-752c...`）。

## 7. 报告与事实差异

- t2 声称的测试数字与命令**全部独立复现**（§6）：27 定向、589 全量、ruff、mypy 36 文件、import、diff-check 均一致；时长差异（0.13s/0.19s/253.79s vs 0.23s/0.29s/241.46s）为运行环境波动，非不一致。
- t2 声称「ISSUE-013/014 在制产物未触碰」**属实**：工作树 16 条目与开工快照一致，M02 两处状态行改动先于 015 开工即存在（t1 基线单 §3.5）。
- t2 声称「M03 仅 015 状态行改动」**属实**：`git diff` 仅 L7 一行。
- t2 声称「未 commit/push/merge、分支不变」**属实**：HEAD==main==`0903749`，reflog 无新提交。
- 计划 E2「实现期修正两处核心 API 用法（frequency_axis_hz/is_identical 为属性）」与代码实测一致（`backend.py:584`、`test:375` 均按属性使用）。
- 计划 E1 红灯证据（实现前 `ModuleNotFoundError`）无法事后独立重放，标为「未发现反证」：backend.py 为新增未跟踪文件、测试先于实现存在，证据链自洽。
- **t2 未报告、复审新发现的缺陷**：P1-01（在途 configure 竞态）、P3-02（close→open 误分类）、P3-03（重配 trace_uid 重复）、P3-04（open 失败不回滚）——见 §3，均为 t2 测试矩阵未覆盖的交叠/边界路径。

## 8. 剩余风险

1. **R1（P1-01 同源，修复前存在）**：任何未来调用方（ISSUE-017 控制器、028 校准参考采集）若在 sweep 在途时重配，将静默产生混合契约数据；修复（busy 守卫）后消除。
2. **R2（低）**：`trace_uid` 的确定性（uuid5(mission,index)）与全局唯一性依赖「每任务新 mission_id」这一未在 015 内强制的外部契约（P3-03）；ISSUE-017/043 需明确承接。
3. **R3（低）**：close→open 竞态的错误分类失真（P3-02）需 ISSUE-017 保证「drain 后重开」，或后端引入纪元判别。
4. **R4（低）**：基类钩子失败不回滚（P3-04）在当前模拟器不可达，ISSUE-019 真机后端须在 open/configure 钩子失败语义上补强。
5. **R5（低）**：`SimulationFaults.delay_s` 为可变 Mapping 引用（frozen dataclass 不深冻结），调用方构造后修改 dict 会改变故障计划；建议测试/文档约束「构造后不修改」。

## 9. 合并建议

- **暂不合并**：存在 P1-01（阻止合并）。修复清单见 §10，修复后按标准 §14 复审，复审 PASS 再交项目负责人人工验收。
- 合并单元（修复后）：`src/uav_gpr/acquisition/backend.py` + `tests/contract/test_acquisition_backend.py` + `docs/plans/2026-08-30-issue-015-simulated-backend.md` + M03 的 015 状态行（保持 `Review`，人工验收后置 `Done`）。
- 与 ISSUE-013/014 在制文件严格分离（不同路径、零交集），可独立合并；013/014 的合并时点不影响 015。
- 本复审不执行任何 Git 写操作。

## 10. 最小修复清单

按 §14「修复提示词只包含阻止合并和明确要求处理的最小问题」：

**必须修复（P1-01，阻塞合并）**：

1. `src/uav_gpr/acquisition/backend.py` `configure()`（`:221-241`）持锁段增加与 acquire 同型的 busy 守卫：`if self._acquiring: raise BackendStateError("configure rejected: acquire in progress", operation="configure", state=self._state.value, busy=True)`。
2. `tests/contract/test_acquisition_backend.py` 增加 1 条失败测试先行：`block_until_cancelled`（或 `delay_s`）+ 线程 + `acquire_started` 事件——在途 configure 抛 `BackendStateError`（context busy=True/reason=illegal_state）、状态保持 CONFIGURED、在途 acquire 可被 cancel 正常唤醒且不受影响。
3. `docs/plans/2026-08-30-issue-015-simulated-backend.md` §5.2 状态表补充「configure 与在途 acquire 互斥（busy 结构化拒绝）」，执行日志追加修复记录与复审后门禁数字。
4. 复跑：定向测试 + `tools/quality/verify.py` 全量 + ruff + mypy + `git diff --check`，工作树快照与 inScope 核对，更新完成报告。

**可选顺手项（P3，不阻止合并，可在人工验收时决定）**：

- P3-02：文档化「重开必须在在途 acquire 全部终止后」（或实现 close 纪元判别）。
- P3-03：在 backend.py 模块文档/计划中显式声明「reconfigure 视为新任务，调用方必须更换 mission_id」。
- P3-04：留给 ISSUE-019 真机后端硬化钩子失败回滚语义。
- R5：SimulationFaults 文档注明「delay_s 构造后不得修改」。

> 复审结束。按标准 §13 立即停止，不修改代码，等待项目负责人决定修复、拆分或合并。

---

# Round-2 复审（P1-01 修复后，2026-08-30，t5 / repair round-2 登记期）

前置说明：t4（repair r2）的实际修复已落入工作树（4 文件，见 R2-2），但 t4 任务登记被 completion 门禁拒绝（inScope 退化清单与 changedPaths 不符）而处于 failed。本复审按 captain 指示以**实际工作树**为审查对象，不采信 t4 的 changedPaths 登记。审查时点：HEAD `0903749` 不变、无新提交，工作树 18 条目（与 round-1 审查后集合一致，修复全部落在既有 4 个未跟踪/已修改文件内）。本复审仅追加本节，未修改实现/测试/计划/M03。

## R2-1 审查结论

**PASS WITH CONDITIONS**（P1-01 已关闭；M03 三条验收全部满足；无 P0/P1/P2 剩余问题；仅 2 项非阻止合并的 P3 文档条件）

- 单 Issue 结论：ISSUE-015 **通过**（修复后，可进入人工验收）。
- 条件：C1——计划 E4 门禁数字「589」修正为「590」（执行日志笔误，与实测不符，不阻止合并）；C2——`SimulationFaults` docstring 注明「`delay_s` 构造后不得修改」（可选顺手项）。
- round-1 三项 P3 均已按 §14 最小修复原则文档化落位（不改代码），见 R2-3。

## R2-2 审查范围（修复集，以工作树实测为准）

| 文件 | round-1 → round-2 | 改动内容 |
|---|---|---|
| `src/uav_gpr/acquisition/backend.py` | 695 → 721 行 | `configure()` 在途守卫（L252-258，`busy=True` 结构化拒绝）+ 3 处 docstring（configure 互斥与 mission_id 轮换 L229-237；open 重开须 drain L206-212；close 重开须 drain L300-306） |
| `tests/contract/test_acquisition_backend.py` | 595 → 625 行 | +1 回归用例 `test_configure_rejected_while_acquire_in_flight`（L506-533，28 用例） |
| `docs/plans/2026-08-30-issue-015-simulated-backend.md` | 155 → 168 行 | §5.2 三条标注（reopen 须 drain / configure-acquire 互斥 / 钩子回滚留给 ISSUE-019，L61-66）+ E4 执行日志（L158-168） |
| `docs/issues/M03_ACQUISITION.md` | 状态行追加「round-2 修复完成」标注 | 状态保持 `Review`（人工验收后置 Done） |

改动最小且严格限定在修复面内；无范围外代码/测试改动（源码行差均为上述 docstring/守卫，逐段实测核对）。

## R2-3 问题复查（round-1 §3 逐项）

| round-1 编号 | 状态 | 证据 |
|---|---|---|
| P1-01（configure 在途竞态） | **CLOSED** | 守卫 `backend.py:252-258`；回归测试 `test:506-533`（28 号用例：block_until_cancelled + 线程 + acquire_started + try/finally cancel/join，无挂死路径）；探针 B2 核心断言全 PASS（详见 R2-6） |
| P3-02（close→open 误分类） | **DOCUMENTED**（不修代码，符合 §14） | `backend.py:206-212`（open）、`:300-306`（close）、计划 §5.2 L61；探针 C2 记录性复证（仍为 CancelledError，无数据风险、无线程残留） |
| P3-03（同 mission 重配 trace_uid 重复） | **DOCUMENTED** | `backend.py:229-237`（configure docstring：调用方必须轮换 mission_id）、计划 §5.2 L62；探针 H2 记录性复证 |
| P3-04（open 钩子失败不回滚） | **DOCUMENTED** | 计划 §5.2 L66（留给 ISSUE-019 LibreVNA 传输层处理，本 Issue 不实现） |
| R5（delay_s 可变引用） | **未处理**（→ C2） | `SimulationFaults` docstring（L386-392）未注明构造后不修改；非阻止合并 |

## R2-4 验收矩阵变化项（其余 11 行同 round-1 §4，全量回归复证）

| # | 标准 | round-1 | round-2 | 证据 |
|---|---|---|---|---|
| A4 | 非法生命周期被结构化拒绝 | FAIL | **PASS** | `backend.py:252-258` + `test:506-533` + 探针 B2（reason=illegal_state/busy=True/operation=configure；在途 acquire 不受扰；新任务首道 index=0；cfg_a 确定性完好） |
| A1 | 确定性 raw | PASS | PASS（复证） | 探针 A2：同 seed/config 独立时钟 → raw/哈希逐值相等 |
| A5 | cancel/close 无残留 | PASS | PASS（复证） | 探针 D2：5 轮「拒绝→cancel」路径线程数 1→1 |

探针 B2 说明：唯一非 PASS 子项「fault plan 不再重放」（实测 0.301s）为**文档化新任务语义**——终止后重配合法地重置尝试计数（计划 §5.2 L62「重配重置道计数/尝试计数/rng（新任务语义）」），attempt-0 故障在新任务重放属既定契约且无任何数据损坏（新任务 index 从 0 起、cfg_a 确定性完好）；与 round-1 缺陷（**在途**中途重置导致混合契约）本质不同，不构成问题。

## R2-5 Git 与交付检查

- HEAD == main == merge-base == `0903749` 不变；无新提交；reflog 无 reset/rebase/amend。
- 工作树 18 条目与 round-1 审查后集合一致（修复未新增范围外文件）；`git diff --check` clean；缓存均在 .gitignore。
- t4 的 changedPaths 登记失败属任务系统登记契约问题（inScope 退化清单），与代码交付无关；实际修复集经实测与 E4 描述一致。

## R2-6 测试与验证结果（独立复现，exit 0）

| # | 命令（实际执行） | 实际结果 | 修复方声称 | 判定 |
|---|---|---|---|---|
| R2.1 | `.venv/Scripts/python.exe -m pytest tests/contract/test_acquisition_backend.py -q`（Python 3.13.14） | **28 passed in 0.21s** | 28 passed（0.26s/0.23s/0.13s） | 一致（时长波动） |
| R2.2 | `.venv/Scripts/python.exe tools/quality/verify.py` | **590 passed, 1 deselected in 269.53s**；ruff All checks passed；mypy 36 files；import ok；exit 0 | 计划 E4 误记 589（→C1）；captain 预期 590 ✓ | 590 为准 |
| R2.3 | `python3 -m ruff check .` / `python3 -m mypy src`（WSL 3.12.3） | exit 0 / exit 0（36 files） | — | 通过 |
| R2.4 | `git diff --check` | clean（exit 0） | clean | 一致 |
| R2.5 | 探针（`/mnt/d/tmp/iss015-review/probe2`，复制树 `python3 -B probe2.py`，项目内零残留） | B2 核心 12 断言 11 PASS（1 项文档化语义见 R2-4）；D2 PASS；C2/H2 记录性确认；A2 PASS | —（复审补查） | 通过 |

红灯证据「修复前 `Failed: DID NOT RAISE BackendStateError`」无法在修复后代码上重放，判定为**未发现反证**：round-1 探针 B 在修复前代码上实测 configure 被静默接受（ACCEPTED），与新测试断言方向完全一致，红灯声明自洽；E4 记录的早期挂死（harness 120s 超时）与 try/finally 根除措施与现测试结构（`test:521-523` finally 必达 cancel/join）相符。

## R2-7 报告与事实差异（round-2 新增）

- 计划 E4「verify.py 全量 589 passed」与实测 **590** 不符（+1 回归用例未计入执行日志数字，疑为 E3 数字复制）→ C1。
- 其余修复方声称（定向 28、ruff/mypy/import 全绿、diff-check clean、工作树无残留）全部独立复现。

## R2-8 剩余风险（round-2 更新）

1. R1（低）：三项 P3 文档化契约（reopen 须 drain、mission_id 轮换、钩子回滚留给 ISSUE-019）依赖调用方遵守，无运行时强制；ISSUE-017/043/019 须承接。
2. R2（低）：`delay_s` 为可变 Mapping 引用（C2 未文档化）。
3. round-1 R4（在途 configure 竞态）已随 P1-01 修复消除。

## R2-9 合并建议

**可以合并**（人工验收通过后授权）：`src/uav_gpr/acquisition/backend.py` + `tests/contract/test_acquisition_backend.py` + `docs/plans/2026-08-30-issue-015-simulated-backend.md` + M03 状态行（保持 `Review`，人工验收后置 `Done`）。无阻止合并问题；C1/C2 为可选顺手项，可由人工验收决定是否随本轮一并处理。与 ISSUE-013/014 在制文件零交集，可独立合并。本复审不执行任何 Git 写操作。

## R2-10 最小修复清单（round-2）

**无必须修复项**。可选顺手项（P3，不阻止合并）：

- C1：计划 E4 执行日志全量数字 `589` → `590`（一行）。
- C2：`SimulationFaults` docstring 增加一句「`delay_s` 为构造期快照语义，构造后不得修改」。

> Round-2 复审结束。按标准 §13 立即停止，不修改代码，等待项目负责人人工验收决定。
