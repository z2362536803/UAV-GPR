# ISSUE-018 独立复审报告（t3）

日期：2026-08-31
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-018-replay-backend`（审查器 reviewer，任务 t3，attempt 2de039b2-2972-4b0a-bc04-89486b49630b）
依据：docs/ISSUE_REVIEW_STANDARD.md v1.0（固定输出格式 §13）
被审交付：t2（engineer，attempt 8e57debb-d454-4244-b3fb-10db241787b9）的 ISSUE-018 `.rcscan` 文件回放后端实现
权威基线件：docs/reports/ISSUE_018_BASELINE_CONFIRMATION.md（t1）；实施计划：docs/plans/2026-08-30-issue-018-replay-backend.md
性质：审查者全程只读；未修改任何实现/测试/计划/M03/文档；未 commit/push/merge/clean；变异探针在系统临时目录复制树中执行并已清理。

---

## 1. 审查结论

**VERDICT: PASS WITH CONDITIONS**

- ISSUE-018 三条验收标准（M03 L137–141）逐项 **PASS**（证据见 §4 矩阵），提示词必测项全覆盖。
- 未发现 **P0 / P1 / P2** 问题；发现 **4 项 P3**（§3）：三项为低风险加固建议（失败 open 后的状态残留、v1 频率轴拒绝缺测试、close 与在途读取的窄竞态），一项为节奏验证固有风险（真实时间下限断言），均**不阻止合并**。
- 门禁全部独立复现：定向 37 passed、依赖定向 155 passed、全量 740 passed / 1 deselected、ruff/mypy(39 文件)/import 全绿、`git diff --check` 干净。
- 审查前后工作树状态逐字节一致；探针在 `/tmp` 复制树执行后已删除，项目内零残留。
- 按标准 §12：无 P0/P1、无验收失败、无必要测试失败 → 整批不判 FAIL；P3 条件明确且低风险 → **PASS WITH CONDITIONS**（与 ISSUE-011/015 合并先例同口径）。条件清单即 §10 最小修复清单，均为可选、不阻断本次合并。

---

## 2. 自动识别的审查范围

### 2.1 从完成报告提取（t2 输出）

| 项 | 声明 |
|---|---|
| Issue | ISSUE-018 `.rcscan` 文件回放后端（M03，依赖 011/015/017，均 Done） |
| 基线 | `main` @ `9406b60` |
| 改动文件 | `src/uav_gpr/acquisition/replay.py`（443 行）、`tests/contract/test_acquisition_replay.py`（1201 行，37 测试）、`docs/plans/2026-08-30-issue-018-replay-backend.md`（96 行）、`docs/issues/M03_ACQUISITION.md`（仅 1 行状态行） |
| 测试声明 | 定向 37 passed in 4.66s；全量 verify.py 740 passed / 1 deselected in 187.97s；ruff/mypy(39 文件)/import 全绿；`git diff --check` clean |
| Git 声明 | 未 commit/push/merge、未创建分支 |

### 2.2 Git 事实（本次复审实测）

```text
branch      main（当前）
HEAD        9406b60  docs(issues): mark ISSUE-017 Done after authorized merge
origin       ## main...origin/main（0/0，无 ahead/behind）
git status --porcelain=v1 -b  仅 5 行：
   M docs/issues/M03_ACQUISITION.md
  ?? docs/plans/2026-08-30-issue-018-replay-backend.md
  ?? docs/reports/ISSUE_018_BASELINE_CONFIRMATION.md   （t1 交付物，非 t2 inScope）
  ?? src/uav_gpr/acquisition/replay.py
  ?? tests/contract/test_acquisition_replay.py
reflog      HEAD@{0} 仍是 9406b60 的 commit 记录；复审前后无新增 reflog 条目；无 reset/rebase/amend
git diff --stat HEAD   仅 M03 1 文件 1 行（+1/-1）
```

### 2.3 范围确定

- 报告列出的 4 个 inScope 路径与工作树实测**逐一相等**（t2 changedPaths 登记 4 项与 team.json t2 inScope 逐一相等，实测于 `.agent-teams/uav-gpr-issue-018-replay-backend/team.json`）；`docs/reports/ISSUE_018_BASELINE_CONFIRMATION.md` 为 t1 交付物，按基线单 §5 明确不计入 t2 inScope。
- 无分支可依赖（未创建分支），范围 = 基线后工作树差异（1 行修改 + 4 个未跟踪文件）。
- 报告与 Git 一致，无需按标准 §4 采用 Git 事实覆盖报告。

---

## 3. 主要问题（P0 → P3）

**P0：无。P1：无。P2：无。**

### P3-01 失败 open 后后端停留在 OPEN 状态（无 reader）

- 文件：`src/uav_gpr/acquisition/backend.py:222`（基类 `open()` 先置 OPEN）+ `src/uav_gpr/acquisition/replay.py:241-306`（`_do_open` 抛出时 `self._reader` 未赋值）
- 触发条件：对损坏/无 raw/不支持的 `FileReplayBackend.open()` 抛错后，未调用 `close()` 直接 `configure()/acquire()`。
- 实际影响：`configure` 得到 `ReplayError("replay backend is not open")`（`replay.py:391-396`）而非生命周期结构化错误；必须显式 `close()` 才能重试 open。现有测试全部在失败 open 后调用 `backend.close()`（如 `tests/contract/test_acquisition_replay.py:1009`），功能可恢复，无数据风险。
- 违反要求：ISSUE-015 生命周期契约的失败原子性（轻）。
- 最小修复方向：`_do_open`/`_open_v1` 失败路径在 re-raise 前调用 `self.close()`（CLOSED 恢复），或基类 `open()` 在 `_do_open` 抛错时回滚状态。

### P3-02 v1 频率轴不匹配拒绝缺少定向测试

- 文件：`src/uav_gpr/acquisition/replay.py:374-377`（实现存在）、`tests/contract/test_acquisition_replay.py:744-752`（仅测 channel 不匹配）
- 触发条件：v1 文件 + 请求配置频率轴与文件不一致 → `BackendConfigRejectedError` 分支无测试覆盖。
- 实际影响：仅覆盖率缺口（已实现分支未被契约测试钉住），不影响功能。
- 最小修复方向：补一个 `test_v1_configure_rejects_axis_mismatch`（用 `MissionConfig.from_frequency_axis` 构造不同点数/起止的轴）。

### P3-03 close 与在途读取之间的窄竞态（未结构化异常）

- 文件：`src/uav_gpr/acquisition/replay.py:410-421`（等待返回后未复查状态即读）与 `replay.py:437-443`（`_do_close` 关闭 h5 句柄）
- 触发条件：`_wait_cancellable` 恰好以“完整等待结束”（非事件唤醒）返回后、`trace_by_index`/`raw_row` 执行前的微秒级窗口内，另一线程调用 `close()` 关闭 reader 句柄。
- 实际影响：在途 acquire 抛出 h5py 原生异常（非 `BackendClosedError`/`ReplayError` 结构化错误）；controller 的 `_tick` 只分类 `BackendError`（`controller.py:791-798`），原生异常路径不受控。概率极低（每个节奏等待尾部窗口），无数据破坏，无内存残留。
- 最小修复方向：`_do_acquire` 在等待返回后加锁复查 `state is CLOSED → raise BackendClosedError`，或等待前后各复查一次关闭标志。

### P3-04 节奏验证依赖真实时间下限/上限断言（接受性风险说明）

- 文件：`tests/contract/test_acquisition_replay.py:844-893`（下限断言 + `test_per_trace_mode_never_waits` 的 5.0s 上限）
- 说明：节奏等待无注入时钟（计划 D1 已否决 Waiter 注入），真实小等待 + 宽松下限/上限是验证节奏的合理手段，**不是**“固定 sleep 猜并发时序”（TESTING.md L135 禁止项）。现有边界充裕（下限留 20% 裕度、上限 5.0s vs 0.15s 实际耗时）。仅在极端慢 CI 上有理论 flake 可能。
- 处置：接受为固有风险，不改；若未来出现 flake 再引入可注入 sleeper。

---

## 4. 逐 Issue 验收矩阵

验收标准取自 M03_ACQUISITION.md ISSUE-018（L137–141 原文）+ 提示词必测项。

| # | 验收标准 | 状态 | 代码证据（文件:行） | 测试证据（实测复跑） |
|---|---|---|---|---|
| A1 | 回放 raw 与 reader 数值/axis/channel/metadata 对拍 | **PASS** | `replay.py:418-435`（v2 经 `trace_by_index` 输出 `record.frequency_raw`+`record.metadata` 原对象；v1 经 `raw_row`，metadata=None）；`_assert_sweep_matches_record` 逐字段断言 `test_acquisition_replay.py:490-532` | `test_v2_air/ground_replay_matches_reader_logical`（L540/L564）、乱序（L582）、重复折叠（L634）、v1 对拍（L769）全过；**审查者独立探针 PROBE1**：绕开 reader、直接以 h5py 读 `/frequency/raw` 与 `/trace_metadata/*`/`/gnss/*` 单元格逐字节比对 3 道（data/UTC-ns/mono/sha/uid/lat/lon）全部相等 |
| A2 | pause/resume/stop 与 controller 配合，无伪当前时间/位置 | **PASS** | `replay.py:410-415`（节奏等待复用基类 `_wait_cancellable`，`backend.py:342-367`，honor cancel/close/timeout_s）；`replay.py` 全文无 datetime/time 导入（grep 实测），物理上不可能伪造当前时间；v2 metadata 为 reader 解码原对象、v1 metadata=None | `test_controller_pause_resume_replay`（L1112，pause 安全边界：在途道发布、暂停期无新道）、`test_controller_stop_drains_replay`（L1144）、`test_controller_emergency_stop_interrupts_paced_wait`（L1164，在途不发布）、`test_controller_close_no_leaked_worker`（L1188，join 无残留线程）全过；**探针 P2c**：configure-while-acquiring 结构化 busy=True 拒绝 |
| A3 | 损坏/无 raw 文件明确拒绝 | **PASS** | `replay.py:258-280`（conflicts / HASH_MISMATCH / MISSING_HASH → `ReplayCorruptFileError`；`committed_record_count==0` → `ReplayNoRawError`；非 HDF5/未知 schema → `ReplayUnsupportedFileError`）；打开期即 fail-closed，不进入 acquire | 6 项定向测试全过：hash 不符（L1002）、缺存储 hash（L1012）、身份冲突（L1023）、零 committed（L1034）、非 HDF5（L1045）、schema_version=3（L1055）；**探针 P2e**：v1 时间戳数≠道数在 open 即被拒（fail-closed） |
| S1 | v2 air/ground + v1 adapter 回放 | **PASS** | `replay.py:241-306`（v2 双角色同路径）、`replay.py:308-345`（v1 回落仅凭 UNSUPPORTED_SCHEMA_VERSION，D2） | air（L540）、ground（L564）、v1（L769）、v1 无时间戳逐道模式（L799）、v1 探测（L1065）全过 |
| S2 | 逐道/原始时间比例/显式加速，可取消等待 | **PASS** | `replay.py:80-128`（ReplayMode×3 + ReplayConfig 校验）+ `replay.py:161-171`（gap 由文件单调起点差值、负差钳 0） | 三节奏测试（L844/L860/L877）、cancel/close 中断（L940/L961）、timeout_s（L907）、cancel 幂等不污染（L980）全过；**探针 P2a**（反向时钟 gap 钳 0 不崩溃）、**P2b**（timeout 不吞道、重试等满 gap、EOF 后 ReplayEndedError） |
| S3 | 原样保留 identity/UTC/GNSS/缺失字段，不伪补 | **PASS** | `replay.py:421-435`（metadata 原对象直出）；无任何 `now()`/0 坐标补齐代码（grep 实测） | 无 GNSS 行保持缺失（L606）、v1 缺失保持 metadata=None（L769/L799）、能力 gnss=False（L578/L779）；**探针 PROBE1** 确认 UTC=文件 2026-08-28 记录值而非当前时间 |
| S4 | 不重复应用文件已有校准/处理 | **PASS** | `replay.py:418-427`（v2 仅服务 `record.frequency_raw`）；v1 仅 `raw_row`（L430-435），从不触碰 calibrated/time 组 | `test_calibrated_group_ignored_raw_served`（L621，写有 +1000 偏移的 `/frequency/calibrated`，回放 data==raw）全过 |
| S5 | 排除项遵守：无 revision/UI/迁移/范围外改动 | **PASS** | 工作树仅 4 个 inScope 路径 + t1 基线单（§2.2 实测）；`core/**`、`storage/**`、`backend.py/scheduler.py/controller.py`、`docs/ACQUISITION.md`、`DATA_FORMAT.md`、`PROCESSING.md`、`TESTING.md`、`docs/adr/**`、`tools/**` 全部未改 | `git status`/`git diff --stat` 实测 |
| S6 | 测试禁固定 sleep；inScope 精确路径 | **PASS** | `grep -n sleep` 于测试与实现仅命中 docstring 两处（L17/L28），无 `time.sleep` 调用 | 节奏测试用事件（`acquire_started.wait`）+ `join(timeout)` + 下限计时（计划 §7 声明同口径）；changedPaths==inScope 4 项逐一相等（team.json 实测） |
| S7 | 依赖 011/015/017 回归不破坏 | **PASS** | 依赖模块未改（§S5） | 依赖定向 `155 passed in 9.04s`（011:39 + 015:28 + 017:88，实测） |

---

## 5. Git 与交付检查

| 检查项 | 结果 | 证据 |
|---|---|---|
| 当前分支 / 目标 / 基线 | main / main / `9406b60` | `git branch --show-current`、`git log --oneline -1` |
| 提交历史 | 无新提交（本轮无 commit，符合“默认不 commit”协议） | `git reflog -5`：HEAD@{0} 仍为 9406b60 commit 记录 |
| reflog 改写迹象 | 无 reset/rebase/amend/强推 | reflog 仅 commit/merge/checkout |
| 范围外修改 / 遗漏提交 / 未跟踪文件 | 无范围外修改；4 个未跟踪文件全部在 inScope；无遗漏（t2 不提交，交付物在工作树） | `git status --porcelain=v1 -b`（§2.2） |
| 缓存/日志/密钥/实测数据混入 | 无（`.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/` 已忽略；无实测数据文件） | porcelain 5 行固定 |
| 公共契约变更缺文档 | 无：纯新增模块，未改 core/storage/backend 既有契约；`Capabilities/AppliedConfig` 语义与 ACQUISITION.md §4 一致（config digest 覆盖契约字段、created_utc/note 不入摘要，`config.py:542`） | `git diff --stat HEAD` 仅 M03 1 行 |
| M03 状态行 | 仅 1 行 diff（`Planned` → `Review`，含正确链接），其它条目未动 | `git diff HEAD -- docs/issues/M03_ACQUISITION.md` 实测 |
| changedPaths == inScope | 4 项逐一相等（team.json t2 inScope L152-155 vs changedPaths L204-207） | team.json 实测 |
| origin 同步 | 0/0，无本地领先提交 | porcelain `## main...origin/main` |

---

## 6. 测试与验证结果

环境：WSL2 Ubuntu（kernel 6.18.33.2-microsoft-standard-WSL2）；`/usr/bin/python3` = Python 3.12.3；numpy 2.5.2、pytest 8.4.2、h5py 3.16.0、ruff 0.16.4、mypy 1.20.2；`uav_gpr` 以 `src/` editable 导入。

| 命令（实测） | 退出码 | 结果 | 对照 t2 声明 |
|---|---|---|---|
| `python3 -m pytest tests/contract/test_acquisition_replay.py -q` | 0 | **37 passed in 4.51s** | 37 passed in 4.66s ✓（数字一致，耗时属机器抖动） |
| `python3 -m pytest tests/contract/test_rcscan_reader.py tests/contract/test_acquisition_backend.py tests/contract/test_acquisition_controller.py -q` | 0 | **155 passed in 9.04s** | 与 t1 基线 155 ✓ |
| `python3 tools/quality/verify.py` | 0 | pytest(非硬件) 全绿 → ruff `All checks passed!` → mypy `Success: no issues found in 39 source files` → `package import ok` → `[quality] all gates passed`；总耗时 2m21s | t2 声明全绿 ✓ |
| `python3 -m pytest -m "not hardware and not slow" -q`（verify.py 同口径单跑） | 0 | **740 passed, 1 deselected in 139.23s**（基线 703 + 新增 37 = 740 ✓） | 740 passed/1 deselected in 187.97s ✓（数字一致） |
| `python3 -m ruff check src tests` | 0 | All checks passed! | ✓ |
| `python3 -m mypy src` | 0 | Success: no issues found in 39 source files | ✓ |
| `git diff --check` | 0 | clean | ✓ |
| 审查前后 `git status --porcelain=v1 -b` | — | 逐字节一致（§2.2 同一 5 行） | — |

**审查者独立变异/反例探针**（在 `/tmp/t3probe` 复制树执行，`src+tests` 复制件；执行后整树删除，项目零残留）：

| 探针 | 目的（t2 未覆盖项） | 结果 |
|---|---|---|
| PROBE1 独立 HDF5 解码对拍 | 绕开 `RcScanReader`，直接以 h5py 读原始单元格比对回放输出的 data/UTC-ns/monotonic/sha/uid/经纬度；并验证 UTC 为文件记录值（2026-08）而非当前时间 | **PASS**（3 道全字段 verbatim） |
| P2a 反向单调时钟 | 文件内相邻道 `sweep_started_monotonic_ns` 递减 → gap 钳 0、无负等待、无崩溃 | **PASS**（elapsed 0.000s） |
| P2b timeout 不吞道 | `acquire(timeout_s=0.1)` 于 0.3s gap 超时 → 重试等满 0.3s 且服务同一道 → 末道后再 acquire 抛 `ReplayEndedError` | **PASS**（retry 0.301s） |
| P2c acquire 期间 configure | 在途节奏等待时 `configure` 结构化 `busy=True` 拒绝（生命周期契约） | **PASS** |
| P2d writing 态 partial 回放 | `lifecycle="writing"` + 2 道 committed → 仅回放已提交窗口 | **PASS** |
| P2e v1 时间戳数≠道数 | 损坏 v1（1 时间戳/2 道）在 open 即拒（fail-closed） | **PASS**（`unsupported_file`） |

硬件/网络：本 Issue 纯软件回放，无硬件依赖，`NOT APPLICABLE`；Qt offscreen smoke：本 Issue 无 UI，`NOT APPLICABLE`。

---

## 7. 报告与事实差异

| t2 声明 | 实测 | 结论 |
|---|---|---|
| 4 个交付文件与行数（443/1201/96） | `wc` 与读文件实测一致 | 一致 ✓ |
| 37 项定向测试全绿 | 37 passed 复现 | 一致 ✓ |
| 全量 740 passed / 1 deselected | 740 passed / 1 deselected 复现（139.23s vs 187.97s，纯耗时差异） | 一致 ✓ |
| ruff / mypy(39 文件) / import 全绿 | 复现全绿 | 一致 ✓ |
| M03 状态行仅 1 行 diff | `git diff` 实测 1 行 | 一致 ✓ |
| 未 commit/push/merge、未建分支 | reflog/HEAD/origin 实测无任何新操作 | 一致 ✓ |
| 工作树仅含声明路径（+ t1 基线单） | porcelain 实测同 | 一致 ✓ |
| “红灯 ModuleNotFoundError” 先于实现 | 无法事后独立复现（过程声明） | 按标准 §10 记为“未发现反证”（计划 §10 执行日志记载） |

无发现任何隐藏失败、跳过、占位或范围偏离。**报告与事实差异：无实质差异。**

---

## 8. 剩余风险

1. **close/在途读取窄竞态**（P3-03）：节奏等待结束后到读取前微秒级窗口，close 可让在途 acquire 抛 h5py 原生异常而非结构化错误；概率极低、无数据破坏；controller 正常路径（cancel 唤醒→BackendClosedError→分类）不受影响。
2. **真实时间计时断言**（P3-04）：三节奏测试用下限断言 + 5.0s 上限，极端慢环境有理论 flake；现行裕度充分（实测耗时约为边界的 1/10–1/25）。
3. **v1 节奏用 UTC 差值**（计划 D1 已文档化）：v1 无单调时钟，UTC 跳变理论上影响 v1 ORIGINAL_TIME 节奏；与 ISSUE-013 迁移口径一致，接受。
4. **EOF 后需消费方按 `trace_count` 停止**：`ReplayEndedError` 经 controller 会进入结构化 FAILED（计划 D6 已文档化）；任务层自动停止属 ISSUE-043/048，本 Issue 边界正确。
5. 失败 open 后状态残留（P3-01）：可恢复但提示不友好。

以上均不构成数据破坏、安全或不可用风险。

---

## 9. 合并建议

- **可以合并**：无 P0/P1/P2；验收 3/3 PASS；门禁与探针全部复现通过；交付物为纯新增模块+测试+计划+1 行状态行，回退=删除 4 个 inScope 改动（计划 §9）。
- 合并边界建议：将 4 个 inScope 路径作为一次提交（或与历史先例一致分 2–3 个逻辑提交：模块、测试、状态行/计划），不要混入 t1 基线单之外的任何文件。
- P3 清单（§10）可选：其中 P3-01/P3-02/P3-03 修复量小（合计 <30 行 + 1 测试），建议在合并前顺手处理或合并后开小修任务；P3-04 无需处理。
- 因工作树尚未提交，由项目负责人决定：先修复 P3 再提交，或原样提交后跟进。两者均不改变本复审结论（PASS WITH CONDITIONS）。
- 不进入 ISSUE-019（协议要求）。

---

## 10. 最小修复清单

| 编号 | 问题 | 最小修复 | 是否阻断合并 |
|---|---|---|---|
| F-01 | P3-01 失败 open 状态残留 | `replay.py` `_do_open`/`_open_v1` 异常路径 re-raise 前调用 `self.close()`（或基类 `open()` 失败回滚状态），并补测试：失败 open 后 `state is CLOSED` | 否 |
| F-02 | P3-02 v1 频率轴拒绝无测试 | 新增 `test_v1_configure_rejects_axis_mismatch`：不同点数/起止的 `MissionConfig.from_frequency_axis` → `BackendConfigRejectedError` | 否 |
| F-03 | P3-03 close 窄竞态 | `_do_acquire` 等待返回后加锁复查 `state is BackendState.CLOSED` → 抛 `BackendClosedError`（在 `trace_by_index`/`raw_row` 之前） | 否 |
| F-04 | P3-04 计时断言固有风险 | 接受（无修复）；若 CI 出现 flake 再引入可注入 sleeper | 否 |

---

> 审查结束。按 ISSUE_REVIEW_STANDARD.md §13：立即停止，不修改代码，等待项目负责人决定修复、拆分或合并。
