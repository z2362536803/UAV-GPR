# ISSUE-025 独立复审报告：GNSS reader、重连与有界 fix 缓存

- 审查日期：2026-09-02
- 审查者：AgentTeams `uav-gpr-issue-025-gnss-reader` 成员 reviewer（任务 t3，attempt e2e26d25-f405-4e30-ba1d-2e48243e9e6c）
- 审查依据：docs/ISSUE_REVIEW_STANDARD.md v1.0、AGENTS.md、docs/INDEX.md、docs/issues/README.md、docs/issues/M05_GNSS.md（ISSUE-025 L42–77）、docs/GNSS.md §3/§4/§8、docs/ARCHITECTURE.md §3/§6、docs/TESTING.md、t1 基线确认单（docs/reports/ISSUE_025_BASELINE_CONFIRMATION.md）、t2 计划文档（docs/plans/2026-09-02-issue-025-gnss-reader.md，含 §11 captain 裁决修订记录）
- 审查性质：全程只读；未修改实现/测试/计划/M05/Git 状态；未 commit/push/merge/clean；变异探针在仓库外临时目录（`~/gnss_probe`，已清理）运行，项目内零残留；本报告为唯一新增文件。

## 1. 审查结论

**VERDICT: PASS**

ISSUE-025 的 3 条验收标准（拆行/合行、高频输入、断开重连和关闭无死锁/泄漏；缓存有界且 snapshot 不暴露可写内部状态；默认测试不打开真实 COM 口）全部由代码与测试证据满足；范围项（可注入 SerialAdapter、增量按行、长度/timeout、parser 集成、六态发布、退避重连+generation、结构化指标、幂等 stop/close、时间/容量双上限线程安全 snapshot 缓存）与排除项（无 sweep 匹配/地图/AGL、GNSS 错误只上报不停止雷达采集）全部落实；t2 声称的测试命令与数字经独立复跑确认，且 captain 裁决 3/4 的修订（阈值必填无静默默认、rmc_pair_window_s ∈ [0.0, 2.0]）已正确合入并有红绿证据与边界测试。无 P0/P1/P2 问题；发现 4 项 P3（非阻塞，见 §3/§10）。可进入人工验收，合并由项目负责人授权执行。

## 2. 自动识别的审查范围

| 项 | 事实 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-025 GNSS reader、重连与有界 fix 缓存（M05 L42–77，状态行 Review） | docs/issues/M05_GNSS.md；docs/issues/README.md L92（依赖 005、024） |
| 基线 | `main` @ `ddf2a1af8b37b6eb9749bed18b99ce785334c3ed`（t1 锁定；审查全程 HEAD 未变、无新提交） | `git rev-parse HEAD`；`git reflog`（顶层 8 条无 reset/rebase/amend） |
| 分支/提交 | 当前 `main`，与 origin/main 同步 `## main...origin/main`；t2 未 commit/push/merge、未创建分支（工作树交付，沿用 ISSUE-018～024 既定 staged 流程） | `git status --porcelain=v1 -b`；reflog 无破坏性操作 |
| 改动文件（t2） | 4 个精确路径：`src/uav_gpr/positioning/reader.py`（新，679 行）、`tests/contract/test_gnss_reader.py`（新，851 行，36 测试函数/39 用例）、`docs/plans/2026-09-02-issue-025-gnss-reader.md`（新）、`docs/issues/M05_GNSS.md`（仅 L44 状态行 1 行 diff） | git status；`git diff docs/issues/M05_GNSS.md`（仅状态行） |
| 契约一致性 | t2 登记 inScope 与 changedPaths 为同一 4 个精确路径、逐一相等（无 glob）；工作树实测仅有上述 4 项 + t1 交付物 `docs/reports/ISSUE_025_BASELINE_CONFIRMATION.md` | .agent-teams team.json t2 记录；工作树实测 |
| 依赖 | ISSUE-005（`952883e`+`b11e741`）、ISSUE-024（`f22affd`+`315a7a8`+`ddf2a1a`）已合入 main；`GnssStatus` 六态（core/enums.py L71–79）、`GnssFix`（core/gnss.py）、`Clock`（core/timeutil.py）、`parse_nmea`/`assemble_gnss_fix`（nmea.py L188/L309，接收侧事实由调用方注入）接口与 reader 集成点逐一核对一致 | 本次复跑定向+依赖回归 146 passed（见 §6） |
| 排除项核查 | reader.py 无 sweep/midpoint/map 代码（仅注释）、无 acquisition import（仅注释）、无 Qt、无顶层 serial import（惰性 import 于 PyserialSerialFactory.__call__）；core/** 与 nmea.py 零改动（ISSUE-024 P3-1 按 captain 裁决保持 open）；未进入 ISSUE-026 | grep 与 git status 实测；tests/contract/test_gnss_reader.py L837–851 AST 守卫 |
| 审查中途变异说明 | t2 完成登记后（16:23–16:26）工作树按 captain 裁决 3/4 修订（reader.py 668→679 行、测试 818→851 行、计划文档 §9/§11 更新），16:25:57 后稳定；已向 captain 报告并按其「审查最新实现」口径复审最终稳定状态 | 文件 mtime 实测；计划文档 §11；§7 差异记录 |

## 3. 主要问题（P0 → P3）

- **P0 / P1 / P2：无。**
- **P3-1 `_stopped` 死状态 + stop-before-start 可再 start**（ISSUE-025，`src/uav_gpr/positioning/reader.py:349` 仅初始化 `_stopped = False` 后从未置 True；`:385` 的 `_stopped` 检查为死分支）。变异探针 P1 实测：`stop()`（未 start）后再 `start()` 不抛 `RuntimeError`，线程随即因 stop_event 已置立即退出、generation 保持 0——与类 docstring「a stopped reader cannot be restarted」（`:297-298`）不符。
  - 触发条件：调用方先 stop 后 start 的误用顺序。
  - 实际影响：无数据/线程安全风险（worker 立即退出、端口不打开），仅生命周期语义与文档不一致。
  - 违反的要求：M05「幂等 stop/close」的完整生命周期语义（低危）。
  - 最小修复方向：`stop()` 中置 `self._stopped = True`（并在 `start()` 检查时统一报错），或删除死字段并把 start 的守卫改为检查 `_stop_event`；补 1 条失败测试（stop-before-start 后 start 抛 RuntimeError）。
- **P3-2 `PyserialSerialConfig.read_timeout_s` 允许 0.0**（ISSUE-025，`reader.py:139` 校验「non-negative finite」）：pyserial `timeout=0` 为非阻塞读，worker 将进入「空读→`_on_read_timeout_tick()`→notify_all」的紧密空转循环（`reader.py:488-489`）。
  - 触发条件：调用方显式配置 `read_timeout_s=0.0`。
  - 实际影响：高 CPU 空转；状态语义仍正确。
  - 违反的要求：AGENTS.md §7 性能与背压精神（低危；默认 2.0s 不受影响）。
  - 最小修复方向：`PyserialSerialConfig` 校验改为 `read_timeout_s > 0`（阻塞适配器契约要求），或文档明示 0 的后果；1 条参数校验测试即可。
- **P3-3 计划文档 §10 门禁表未随裁决修订更新**（ISSUE-025，`docs/plans/2026-09-02-issue-025-gnss-reader.md:89-96` 仍为修订前数字 37/144/1011，与 §11 L116「39 passed」及 L117「全量门禁复跑数字见下表更新行」矛盾）。
  - 实际影响：文档数字与实现状态不一致，误导后续审计。
  - 最小修复方向：把 §10 表更新为修订后实测数字（39 passed / 146 / 1013 passed 4 deselected / mypy 45 文件），或删除 L117 指引句。
- **P3-4 适配器 `close()` 异常无兜底**（ISSUE-025，`reader.py:179-183`、`518-527`）：`PyserialSerialAdapter.close` 中 `self._port.close()` 抛异常会穿透 `stop()`（调用方可见）或 worker `finally`（线程静默死亡）。
  - 触发条件：底层串口句柄关闭异常（真实场景罕见）。
  - 实际影响：低概率下 stop 抛异常/线程退出路径异常；无数据破坏。
  - 最小修复方向：`_close_adapter` 对 `adapter.close()` 包 try/except 并计入 io_error_count（或记录 last_invalid_reason），保持「GNSS 错误只上报」的一致性。

## 4. 逐 Issue 验收矩阵（ISSUE-025）

| # | 验收/范围项 | 状态 | 代码证据（reader.py 为 679 行版） | 测试证据 |
|---|---|---|---|---|
| 1 | 验收 1a：任意拆行/合行 | PASS | `_ingest` 增量 bytearray 缓冲、`\n` 分行、剥 `\r`（`:531-554`） | test_gnss_reader.py:250–263（3 字节分块跨 read 拼行）、:266–279（参数化 1/2/7/4096）；探针 P4（逐字节喂入 2 GGA+1 坏行，gga=2 invalid=1） |
| 2 | 验收 1b：高频输入 | PASS | 单 worker 循环 + 缓存容量上限 + overflow 同步（`:531-548`） | test_gnss_reader.py:606–611（2000 句→gga=2000、缓存 64）；探针 P9（500 句 + 8 线程并发快照，0 异常、缓存 ≤128） |
| 3 | 验收 1c：断开重连 | PASS | read 异常→close→退避重连→open 成功 generation++（`:444-516`） | test_gnss_reader.py:490–493（io_error→generation 2、close 恰 1 次）、:511–507（open 失败退避后连接）；探针 P5（1.2s 内仅 6 次 open 尝试，无紧密循环） |
| 4 | 验收 1d：关闭无死锁/泄漏 | PASS | stop 置 stop_event→close 解除阻塞 read→join 有界；幂等 close（`:391-399`、`:518-527`）；`thread is not current_thread()` 防自 join | test_gnss_reader.py:546–543（阻塞 read 被取消、close=1、二次 stop 无副作用）、:562–577（退避中 stop 中止）、:637–646（with 退出）；探针 P6（stop 0.000s 返回、线程死） |
| 5 | 验收 2a：缓存有界 | PASS | `GnssFixCache` 插入先按单调龄剪过期、再按容量 pop(0)（`:266-274`）；构造校验 max_items≥1/max_age_s>0（`:255-259`） | test_gnss_reader.py:697–713（容量淘汰最旧、时间窗剪过期）；探针 P3（1000 次 add 容量恒 ≤16，时间推进后剪空） |
| 6 | 验收 2b：snapshot 不暴露可写内部状态 | PASS | `snapshot()` 锁内返回新 tuple（排序副本），元素为 frozen `GnssFix`（`:276-282`）；内部 list 不外泄 | test_gnss_reader.py:723–735（tuple、两次 snapshot 不同对象、FrozenInstanceError）、:680–689（status/metrics frozen）；探针 P2（tuple 赋值 TypeError + FrozenInstanceError） |
| 7 | 验收 3：默认测试不打开真实 COM 口 | PASS | 模块顶层无 serial import；`PyserialSerialFactory.__call__` 惰性 import（`:197`）；测试只用 fake adapter | test_gnss_reader.py:837–851（AST 守卫）；tests/unit/test_no_external_access.py（全量门禁内通过）；探针 P7（导入模块并构造 factory 后 sys.modules 无 serial） |
| 8 | 范围：SerialAdapter 注入 + parser 集成 | PASS | `SerialAdapter` Protocol（`:71-85`）、工厂 `Callable[[], SerialAdapter]`（`:88`）；`parse_nmea`→GGA/RMC 分派→`assemble_gnss_fix(received_utc=clock.utc_now(), received_monotonic_ns=now, rmc=…)`（`:556-585`） | test_gnss_reader.py:380–401（fix 逐字段 + 接收侧事实来自注入时钟）、:403–418（RMC 配对日期/速度/航向） |
| 9 | 范围：六态状态发布 | PASS | `_recompute_status_locked` 六态确定性优先级（`:645-658`）；`GnssReaderStatus`/`GnssReaderMetrics` frozen（`:207-242`） | test_gnss_reader.py:234–248（DISCONNECTED→NO_SENTENCE）、:314–324（INVALID）、:360–378（NO_FIX+invalid fix 入缓存）、:466–467（STALE 后恢复 VALID）；探针 P10（0.2s 边界 == 仍 VALID、0.201s 转 STALE，确定性） |
| 10 | 范围：退避重连 + generation + 指标 + 幂等 stop | PASS | `GnssReconnectPolicy` 确定性公式+封顶+参数校验（`:92-121`）；重连循环用可取消 `Event.wait`（`:495-499`）；幂等 `_close_adapter` 锁内摘除+锁外 close 恰一次（`:518-527`） | test_gnss_reader.py:526–543（公式/封顶/非法参数）、:495–507、:546–567、:663–677（必填参数 TypeError/ValueError）；探针 P8（工厂抛非 SerialAdapterError 也被收容） |
| 11 | 范围：时间/容量双上限线程安全缓存 | PASS | `threading.Lock` 保护全部操作（`:263`、`:270`、`:278`）；valid 与 invalid fix 均入缓存 | 见 #5/#6；test_gnss_reader.py:750–757（缓存跨重连保留且时间窗自然淘汰） |
| 12 | 排除项：不停止雷达采集、无 sweep/地图/AGL、不改 core/nmea/acquisition | PASS | 全部异常在 worker 内消化（`:467-484` 兜底 except Exception）；grep：无 sweep/midpoint/map 代码、无 acquisition import；git：core/**、nmea.py、acquisition/** 零改动 | test_gnss_reader.py:585–603（unexpected error 只上报，reader 存活并重连）；git status 实测 |
| 13 | captain 裁决 3：stale 阈值必填、无静默默认 | PASS | `stale_after_s` 为必填关键字参数（`:315`），校验正有限（`:323-324`）；缓存历史窗口 `cache_max_age_s` 独立（`:320`） | test_gnss_reader.py:663–677；探针 P1b（缺参 TypeError） |
| 14 | captain 裁决 4：rmc_pair_window_s 必填且 ∈ [0.0, 2.0] | PASS | 必填参数 + 范围校验（`:316`、`:325-330`）；配对判定 `<=` 窗口（`:573`） | test_gnss_reader.py:420–434（恰 2.0s 仍配对）、:436–449（2.5s 不配对）、:663–677 |
| 15 | 无固定 sleep；假串口事件驱动 | PASS | reader.py 无 `time.sleep`（grep 实测仅注释提及）；等待全部为 Event/Condition | 全测试文件 grep 无 sleep；39 用例 0.29s 完成 |
| 16 | 不 commit/push/merge、不建分支、范围外零改动 | PASS | HEAD 保持 ddf2a1a、无新提交、reflog 无破坏性操作；工作树仅 4 声明路径 + t1 交付物 | git 实测（§5） |

## 5. Git 与交付检查

- 分支/基线：`main` @ `ddf2a1a`（完整哈希 `ddf2a1af8b37b6eb9749bed18b99ce785334c3ed`），与 origin/main 0/0；审查全程 HEAD 未变、无新提交/merge/push；reflog 顶层无 reset/rebase/amend/强推迹象。t2 按契约只留工作树改动（未 commit——与 ISSUE-018～024 既定 staged 流程一致，合并由项目负责人授权）。
- 改动文件：4 个精确路径与 t2 登记 inScope/changedPaths 逐一相等（team.json 实测）；`git diff docs/issues/M05_GNSS.md` 仅 L44 状态行 1 行改动（Planned→Review）；`git diff --check` clean；无缓存/日志/密钥/实测数据/参考仓库文件（`__pycache__`/`.pytest_cache` 等 git-ignored）。
- 单 Issue 原子性：全部改动只属 ISSUE-025；未触碰 ISSUE-026 范围、core/**、nmea.py、acquisition/**（ISSUE-024 P3-1 按 captain 裁决保持 open）。
- 审查前后工作树一致：除本报告新增外逐字节一致（审查开始与结束时 `git status --porcelain=v1 -b` 相同，均为 M05(M)+4 untracked+t1 基线单）。
- 中途变异核查：16:23:39–16:25:57 间 reader.py/测试/计划文档按 captain 裁决修订（t2 登记完成后），已向 captain 报告；16:25:57 起至本报告落盘文件未再变化，全部门禁按最终状态复跑。

## 6. 测试与验证结果

解释器：Windows `.venv` Python 3.13.14（pytest 8.4.2、ruff 0.16.5、mypy 1.20.2、pyserial 3.5；pwsh 承载，`$LASTEXITCODE` 实测）；变异探针另经 WSL python3 3.12.3（editable install 指向同一 src）交叉执行。

| # | 命令 | 退出码 | 实际结果（独立复跑） |
|---|---|---|---|
| 1 | `./.venv/Scripts/python.exe -m pytest tests/contract/test_gnss_reader.py -q` | 0 | **39 passed in 0.29s**（36 测试函数，参数化展开 39 用例） |
| 2 | `./.venv/Scripts/python.exe -m pytest tests/contract/test_gnss_reader.py tests/contract/test_nmea.py tests/unit/test_core_gnss.py tests/unit/test_core_metadata.py -q` | 0 | **146 passed in 0.38s**（39 新增 + 60 nmea + 47 core gnss/metadata） |
| 3 | `./.venv/Scripts/python.exe -m pytest -m "not hardware and not slow" -q` | 0 | **1013 passed, 4 deselected in 149.78s**（974 基线 + 39 新增） |
| 4 | `./.venv/Scripts/python.exe tools/quality/verify.py` | 0 | pytest 1013/4 → ruff `All checks passed!` → mypy `Success: no issues found in 45 source files` → `package import ok` → `[quality] all gates passed` |
| 5 | `./.venv/Scripts/python.exe -m ruff check src tests` | 0 | `All checks passed!` |
| 6 | `./.venv/Scripts/python.exe -m mypy src` | 0 | `Success: no issues found in 45 source files` |
| 7 | import `uav_gpr` + reader 符号 | 0 | `import ok` |
| 8 | `git diff --check` / `git status --porcelain=v1 -b` | 0 | clean；工作树仅声明路径 |
| 9 | WSL 交叉：`python3 -m pytest tests/contract/test_nmea.py -q` | 0 | 60 passed（parser 回归，WSL 3.12.3） |

变异/反例探针（仓库外 `~/gnss_probe` 临时目录执行，已清理；11 项全部通过）：

- P1/P1b stop-before-start 生命周期与必填参数（P1 记录 P3-1 行为）；P2 snapshot 不可变（tuple 赋值 TypeError、元素 FrozenInstanceError、两次快照不同对象）；P3 缓存双上限对抗（1000 次 add 容量恒 ≤16、时间推进剪空）；P4 逐字节拆行对抗（2 GGA+1 坏行 → gga=2/invalid=1/fixes=2/VALID）；P5 open 失败退避有界（1.2s 内 6 次尝试，无紧密循环）；P6 阻塞读 stop 立即返回且 close 恰一次；P7 惰性 pyserial（导入+构造工厂后 sys.modules 无 serial）；P8 工厂非 SerialAdapterError 被收容并重连成功（generation=1）；P9 并发烟测（500 句 + 8 线程并发 status/fixes，0 异常、缓存 ≤128）；P10 stale 阈值边界确定性（==0.2s 保持 VALID，0.201s 转 STALE）。

t2 声称数字核对：修订后定向 39、定向+依赖 146、全量 1013/4、mypy 45 文件——全部独立复现；t2 完成登记时的数字（37/144/1011）为裁决修订前状态，修订证据见计划文档 §11（红 28f/9p → 绿 39 passed）。

## 7. 报告与事实差异

1. **t2 完成报告（task 输出）为裁决修订前状态**：声称 reader.py 668 行、`stale_after_s`/`rmc_pair_window_s` 带默认 10.0、37 用例、1011 全量。修订后实际为 679 行、两阈值必填（rmc 窗口 ≤2.0）、39 用例、1013 全量。差异已由计划文档 §11 显式记录（captain 裁决 3/4，t2 完成登记后到达并由执行器修订），属流程内变更而非隐瞒；本报告按最终稳定状态验收。
2. **计划文档内部数字不一致**（P3-3）：§10 表 L89–96 未随修订更新（仍 37/144/1011），与 §11 L116–117 冲突；修订后真实数字以本报告 §6 为准。
3. **t2 完成登记的时间线声明**「HEAD 仍 ddf2a1a、工作树 4 路径」经复核始终成立，无差异。
4. 无隐藏失败/跳过：全量 4 deselected 为既有 hardware/slow 标记项（与 t1 基线口径一致），非本轮引入。

## 8. 剩余风险

- 真实串口行为（断线时序、驱动异常、pyserial 实际缓冲语义）未在真机验证——按 Issue 契约使用 fake serial，真机验证留待 ISSUE-060 真机/现场验收；非阻塞合并因素。
- `stale_after_s` 与 MissionConfig「GNSS 最大年龄」字段的接线留待 ISSUE-026/044（captain 裁决 3 明示，本 Issue 不反向耦合 config）；reader 自包含、无 schema/协议变更，不影响后续扩展点。
- worker 为 daemon 线程且 `stop(join_timeout_s=5.0)` join 有界：极端情况下 join 超时后线程可能仍在退出中（daemon 保证进程可退出）；测试与探针未观察到该路径。
- P3-1/P3-2/P3-4 三个低危健壮性边界（见 §3），不影响本 Issue 验收，建议随后续 Issue 顺手关闭。

## 9. 合并建议

**可合并（PASS）**：在项目负责人授权下将 4 个交付路径（`src/uav_gpr/positioning/reader.py`、`tests/contract/test_gnss_reader.py`、`docs/plans/2026-09-02-issue-025-gnss-reader.md`、`docs/issues/M05_GNSS.md`）与 t1 基线单、本复审报告一并合入 main；合入后由负责人将 M05 L44 状态行标记为 Done。4 项 P3 均为非阻塞项，不要求在本 Issue 内修复（如负责人决定顺手修复，按 §10 清单执行并重跑门禁）。ISSUE-024 P3-1（nmea.py 半球轴向校验）按 captain 裁决保持 open，不随本 Issue 关闭。合并后不得自动进入 ISSUE-026，交人工验收。

## 10. 最小修复清单（全部为非阻塞 P3，可延后）

1. **P3-1**：`reader.py` `stop()` 内置 `_stopped = True`（或删除死字段、`start()` 改查 `_stop_event`），使 stop-before-start 后再 start 抛 `RuntimeError`；先补 1 条失败测试再实现（≤5 行）。
2. **P3-2**：`PyserialSerialConfig.__post_init__` 将 `read_timeout_s` 校验改为 `> 0`（或文档明示 0 的后果）；补 1 条参数校验测试（≤3 行）。
3. **P3-3**：计划文档 §10 门禁表更新为修订后实测数字（39/146/1013、mypy 45 文件）或删除 L117 指引句（纯文档，≤5 行）。
4. **P3-4**：`_close_adapter` 对 `adapter.close()` 包 try/except 计入 io_error_count（保持「GNSS 错误只上报」），≤5 行 + 1 条失败测试。
