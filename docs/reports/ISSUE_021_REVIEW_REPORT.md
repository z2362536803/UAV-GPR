# ISSUE-021 独立复审报告（S11 生产采集后端）

日期：2026-09-02
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-021-librevna-backend`（审查器 reviewer，任务 t3，attempt cf516253-d250-4c75-a236-1491f030f758）
依据：[docs/ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) v1.0（§13 固定格式）
性质：独立只读复审。除本报告外未修改任何文件，未 commit/push/merge/clean；变异探针在系统临时目录（工作区外 `D:\tmp\issue021_probes`）运行并已删除清理（项目内零残留）；审查前后工作树逐字节一致（复核见第 6 节）。

## 1. 审查结论

**VERDICT: PASS**

ISSUE-021 实现真实、完整、合规：3 条验收标准逐条 PASS（第 4 节），提示词必测项全部落实（第 4 节），无 P0/P1/P2 问题（第 3 节，仅 6 个 P3 观察项），t2 声称的测试命令与数字全部独立复现（第 6 节，含 WSL python3 与 venv python3 双解释器），Git/交付检查干净（第 5 节），报告与事实无实质性差异（第 7 节）。可进入自动化合并流程（合并建议见第 9 节）。

## 2. 自动识别的审查范围

| 项 | 结论 | 证据 |
|---|---|---|
| Issue | ISSUE-021「S11 生产采集后端」（M04 L79–114；FR-003/004；ACQUISITION.md §1–§5）；直接依赖 ISSUE-017、020 | `docs/issues/M04_LIBREVNA.md`；`docs/issues/README.md` L88 |
| 基线/分支 | `main` @ `def2c28d759c92c443ad81354227e39bb5a7ca11`（HEAD == origin/main，0/0）；t2 未创建分支、未 commit/push/merge（自动化授权流程在审查 PASS 后由 captain 合并） | `git rev-parse HEAD origin/main`；`git branch -a`（无 feat/issue-021）；`git log --oneline -5`；`git reflog -5`（仅 commit/merge/checkout，无 reset/rebase/amend/强推） |
| 直接依赖 | ISSUE-017 Done（`1ceca4e`+`b8712c5`+`9406b60`，controller.py 88 测试）；ISSUE-020 Done（`893f800`+`0d465e6`+`def2c28`，stream.py 61 测试）；消费面接口全部实测存在：`AcquisitionBackend`（`_do_*` 四 hook、`_lock/_generation/_cancel_event/acquire_started`）、`LibreVnaUsbTransport`/`PacketStream`/`encode_packet`、`StrictSweepAssembler`/`parse_vna_datapoint`/`S11_RECEIVER_PLAN` | `git log`；`src/uav_gpr/acquisition/backend.py` 全文；`librevna/transport.py` 全文；`librevna/stream.py` 全文 |
| 改动文件（工作树实测） | ① `src/uav_gpr/acquisition/librevna/backend.py`（1078 行，新）② `tests/contract/test_librevna_backend.py`（975 行，39 测试，新）③ `docs/plans/2026-09-02-issue-021-librevna-backend.md`（238 行，新）④ `docs/issues/M04_LIBREVNA.md`（`git diff` 证实仅 L81 状态行 `Planned → Review`，1 行改动）——**changedPaths 与 inScope 4 路径逐一相等** | `git status --porcelain=v1 -b`（5 条目：上述 4 项 + t1 基线单 `docs/reports/ISSUE_021_BASELINE_CONFIRMATION.md`，后者为 t1 交付物）；`git diff -- docs/issues/M04_LIBREVNA.md` |
| 排除项确认 | 未改 `core/**`、`acquisition/backend.py`、`librevna/transport.py`、`librevna/stream.py`、`librevna/__init__.py`、两个参考仓库；无 S22（0x1241 硬拒）、无校准/IFFT/HDF5/UI、无 TCP/SCPI/LibreVNA-GUI 第二路径（backend.py 全文件 grep `SCPI|tcp|socket|subprocess|GUI` 零命中）、未进入 ISSUE-022（M04 L116–124 仍 Planned） | 工作树范围 + grep + M04 全文 |
| 参考源哈希对拍 | 4 个 ISSUE-021 迁移源 SHA-256 与 t1 基线单/计划 §4 逐一相等（本次独立实测）：`librevna_protocol.py 6a41c4b1…`、`librevna_usb.py a73adc1c…`、`backend.py f05da35c…`、`sweep_config.py 9877b761…`；黄金夹具源 `tests/test_librevna_usb_backend.py 2d4db313…` 相等；本地副本 `main @ 7c522d2` | 本地只读副本 `/mnt/d/博士任务/rebar-inspector` `sha256sum` 实测；参考 `decode_device_info`（补 2 字节解包）与 `encode_sweep_settings`（config=0x0C、cdbm 出现两次）语义与 UAV 适配逐项比对一致 |

审查期间必读资料全部完成：AGENTS.md、docs/INDEX.md、docs/issues/README.md、M04_LIBREVNA.md（ISSUE-021 条目全文）、docs/ACQUISITION.md 全文、ISSUE-019/020 迁移记录（transport.py/stream.py 全文 + `docs/reports/ISSUE_020_REVIEW_REPORT.md` P3 清单）、t1 基线确认单、t2 计划文档、ISSUE_REVIEW_STANDARD.md。

## 3. 主要问题（P0→P3）

无 P0 / P1 / P2。以下均为 P3（低风险，不阻止合并）：

- **P3-A**（防御性死分支，`backend.py:935–976`）：`_verify_contract_tolerance` 的频率/功率容差检查对合法 `MissionConfig` 浮点值**不可达**——`int()` 量化偏差恒 < 1.0 Hz、功率 0.01 dBm 量化偏差恒 ≤ 0.005 dBm，均在容差内；channels/points 为 `replace()` 原样复制永不变。「requested/applied 超差在第一道前拒绝」的实际执行面是能力范围校验（`_validate_config` 870–908）+ 量化塌缩检查（916–926）+ **首道轴门禁**（`_verify_first_axis` 999–1029），语义完整且 D4 已记录该设计。无功能缺失，仅为不可达防御代码；建议（可选）后续合并两段检查或加注释说明其防御定位。
- **P3-B**（命令期断开映射缺口，`backend.py:670–730`）：open/configure 命令等待期设备断开只抛 `LibreVnaDisconnectedError`（不映射 `BackendDisconnectedError`、不递增 generation）；acquire 期映射 + generation+1 已实现并有测试（612–618，`test_disconnect_during_acquire_bumps_generation`）。命令期断开的完整重连语义归 ISSUE-023（计划 §3 排除项已声明），当前行为为控制器通用 fail-closed，无数据风险。
- **P3-C**（编码边界为裸 `struct.error`，`backend.py:258–288`）：`encode_sweep_settings` 对超范围值抛未结构化 `struct.error`（如 `LibreVnaUsbSettings(dwell_us>65535)`；points/freq/power 经 `MissionConfig` 校验 + `DeviceInfo` 字段宽度封顶，常规路径不可达）。configure 的 `except Exception → _enter_fail_closed → re-raise` 保证 fail-closed，但建议（可选）在 `SweepSettings.__post_init__` 增加 `dwell_us ≤ 0xFFFF`、`points ≤ 0xFFFF`、`start/stop ≤ 2^64-1` 上界并抛 `ValueError`。
- **P3-D**（ISSUE-020 复审 P3 项承接，与 M04 L44「5 项 P3 建议随 ISSUE-021 顺带关闭」措辞略有出入）：P3-3 已落实（`backend.py:630–638` 显式捕获 `LibreVnaSweepTimeoutError` 映射 `BackendTimeoutError`，catch 兼容在消费面完成）；P3-2（`ReceiverSlot` 构造校验）需改 stream.py（out of scope）**显式延后**（D11 记录）；P3-1/P3-5 为 ISSUE-020 文档措辞项不随本任务改；P3-4 可选留 ISSUE-023。ISSUE-020 复审原文对 P3-3 允许「显式记录捕获两者」、对其余项标「可选/文档」，t2 的「落实或显式记录决策」满足 t1 约束 9 口径。
- **P3-E**（继承基类设计，非本 Issue 引入）：`_do_open` 失败后基类状态停在 OPEN（基类 `open()` 先置状态再调 hook）；调用方 `close()` 后即可重试，`test_open_failure_closes_transport`/`test_open_device_info_timeout` 已覆盖恢复路径。
- **P3-F**（计划测试矩阵命名漂移，纯文档）：计划 §7 中部分测试名为规划名，与实现文件最终名有小幅差异（如 `test_metadata_utc_monotonic_ordered` → 实为 `test_metadata_trace_identity_raw_hash_and_gnss` 合并覆盖；`test_axis_mismatch_no_trace_allocated` → `test_first_sweep_axis_mismatch_rejected_no_trace`）。逐行映射核对：每个必测项均有至少一个实际测试对应，无缺失。

## 4. 逐 Issue 验收矩阵（M04 L92–96 三条 + 提示词必测项）

| # | 验收标准 | 状态 | 代码证据 | 测试证据 |
|---|---|---|---|---|
| 1 | 无硬件协议 simulator 下符合 backend 契约 | **PASS** | `backend.py:392–529`：四 hook 接线；基类状态机/`connection_generation`/取消信号全部由基类持有（`acquisition/backend.py:159–385` 只读消费）；`ScriptedAdapter` 实现 ISSUE-019 `UsbAdapter` Protocol 经 `LibreVnaUsbTransport` 注入（`test_librevna_backend.py:155–193`）；本模块不创建线程（AGENTS.md §7 / ACQUISITION.md §1） | 定向 39 passed（复现见第 6 节）；生命周期测试 `test_open_requests_device_info_and_set_idle`、`test_lifecycle_illegal_transitions_structured`、`test_reopen_after_close`；依赖回归 266 passed（含 ISSUE-017 controller 88） |
| 2a | axis/config 超差在第一道前拒绝 | **PASS** | `backend.py:797–798`（`_finalize_sweep` 在 `trace_index==0` 时先 `_verify_first_axis` 再计算 uid/hash/元数据——拒绝时零 trace 副作用）；`backend.py:999–1029`（实际轴 vs applied `frequency_axis_hz`，形状严格 + 每点偏差 > `AXIS_TOLERANCE_HZ=1.0` → `BackendConfigRejectedError`）；配置期能力范围/量化塌缩校验 `870–926`；重配置后 trace_index 复位 → 门禁随新任务重新生效 | `test_first_sweep_axis_mismatch_rejected_no_trace`（+10 kHz 全轴偏移，traces==0）、`test_axis_within_tolerance_accepted`；**变异探针 P1**（单点 +2 Hz 即拒绝，traces==0）；**P6**（requested/applied 量化 + `ConfigDiff` 契约）；`test_configure_rejects_out_of_device_range`（频率/IFBW/点数/功率 5 项越界拒绝） |
| 2b | 不完整 sweep 不分配正式 trace | **PASS** | 只消费 ISSUE-020 `StrictSweepAssembler` 的完整 `AssembledSweep`（`backend.py:734–786`）；`trace_index` 仅在 `_finalize_sweep` 成功后递增（860）；部分/坏 sweep 只进 `session_stats`（464–487），不零填不输出；`check_timeout` 超时→`BackendTimeoutError`+统计（630–646）；malformed datapoint/NACK fail-closed（750–756、780–781） | `test_incomplete_sweep_timeout_no_trace`（50/101 点，timeouts/incomplete/dropped 各 1、traces==0）、`test_no_data_sweep_timeout`、`test_duplicate_point_no_fake_trace_then_complete`、`test_out_of_range_point_no_fake_trace`、`test_zero_reference_no_fake_trace`、`test_malformed_datapoint_fail_closed`、`test_acquire_caller_timeout_cap`；**变异探针 P2**（部分道+完整道混流，仅完整道分配 trace_index 0） |
| 3 | close/cancel 无泄漏，不自动启动 LibreVNA-GUI | **PASS** | `backend.py:655–666`：`_do_close` 幂等、尽力 fire-and-forget `SET_IDLE` 后 `transport.close()`；`_do_open` 失败路径尽力关闭后重抛（503–509）；无 GUI/subprocess/SCPI/TCP 引用（grep 零命中，无第二条路径）；基类 cancel/close 唤醒阻塞 acquire | `test_close_interrupts_acquire`（线程内 acquire 被 close 唤醒 → `BackendClosedError`、adapter.closed）、`test_cancel_interrupts_acquire`（→ `BackendCancelledError`）、`test_close_idempotent_no_leak_set_idle`（双 close 幂等、SET_IDLE 恰好 3 次）、`test_open_failure_closes_transport`/`test_open_device_info_timeout`（open 失败资源释放）；**变异探针 P4**（configure 等待期 cancel → OPEN 可重配）、**P5**（open 等待期 close → 释放 + CLOSED） |
| 4 | 提示词：sweep settings 发送/回读、实际频率轴、Port1÷Reference S11 | **PASS** | `_do_configure` 发送 `SET_IDLE`+`SWEEP_SETTINGS`（543–570）；applied = int/0.01 dBm 量化 requested（D4：protocol v14 无设置回读命令，实际轴以首道 datapoint 观测兜底）；`_compute_s11`（1031–1052）= stage-0 Port1 ÷ stage-0 Reference 复数比值 | 黄金编解码对拍 `test_golden_device_info_decode`/`test_golden_sweep_settings_encode`（本复审独立 struct 重推导逐字节相等，见第 6 节）；`test_acquire_complete_sweep_values_and_metadata`（S11 数值、1×101 shape、轴一致）；**变异探针 P3**（接收机顺序反转 + 无关 stage-1 接收机存在时 S11 不变） |
| 5 | 提示词：真实 UTC+monotonic sweep 边界、device identity、connection_generation | **PASS** | 道起点 = 最后 plan-valid point 0 被主机接收的 `utc_now/monotonic_ns`（757–763）；完成时刻 = 组装完成（822–823）；midpoint = 中点（826–829）；`connection_generation` 快照进元数据（838–839、854）；DeviceInfo 回读进 Capabilities（498–528） | `test_acquire_complete_sweep_values_and_metadata`（UTC+monotonic 有序、generation==1、raw hash 与 `RawHashSpec` 重算相等）、`test_acquire_two_sweeps_trace_index_and_interval`（trace_index 0→1、actual/schedule interval）、`test_disconnect_during_acquire_bumps_generation`（1→2）、`test_metadata_trace_identity_raw_hash_and_gnss`（uid/hash/GNSS_MISSING） |
| 6 | 提示词：USB 线程边界、超时和安全停止 | **PASS** | 模块零线程创建（由 controller 工作线程调用）；短读超时 tick（50 ms）+ 注入 mono clock 双保险（D7）；整体 deadline + 组装器 timeout + 调用方 `timeout_s` 三重超时（592–653）；无固定 sleep | `test_sweep_timeout_no_data_no_trace`/`test_sweep_timeout_partial_sweep_stats`（注入 TickClock，0 sleep）、`test_acquire_caller_timeout_cap`、cancel/close 线程测试；全测试文件无 `time.sleep` 固定时序（仅 cancel/close 测试用真实时钟事件驱动） |
| 7 | 提示词：禁止第二条 SCPI/GUI 路径、不做 S22、默认不枚举设备 | **PASS** | `ALLOWED_STAGES_BITMAPS=(0x1240,)`（124–128）、`_validate_stages_bitmap` 硬拒 0x1241（236–255）；`_validate_config` 通道恰为 `(hh_s11,)`（870–877）；无 `import usb`/socket/SCPI/subprocess（AST 守卫扫描全部默认测试，`tests/unit/test_no_external_access.py`） | `test_sweep_settings_validation`（0x1241 → ValueError）、`test_configure_rejects_unsupported_channels`（S22 通道拒绝）；**变异探针 P7**（`encode_sweep_settings(stages_bitmap=0x1241)` 硬拒）；全量 892 passed 含 AST 守卫 |
| 8 | 提示词：NACK/协议错误 fail-closed（ISSUE-020 复审剩余风险 2） | **PASS** | `_route_packet` 自路由控制包：NACK → `LibreVnaNackError`（780–781）；命令期 NACK 立即失败（683–686、715–718）；malformed datapoint → `LibreVnaProtocolError`（750–756）；ACK/其他包可观测计数（782–785） | `test_open_nack_fails_closed`、`test_nack_during_configure`、`test_nack_during_acquire_fails_closed`、`test_configure_nack_fail_closed`（NACK 后可重配恢复）、`test_malformed_datapoint_fail_closed`、`test_unexpected_ack_and_ignored_packets_stats` |

8/8 PASS；无 FAIL/PARTIAL/BLOCKED；NOT APPLICABLE：真机 opt-in smoke（本机无授权设备，ISSUE-023 负责硬件验收，符合 M04 L178「没有指定真机时 Issue 保持 Blocked」对 ISSUE-023 的约束）。

## 5. Git 与交付检查

| 检查项 | 结论 | 证据 |
|---|---|---|
| 分支/基线 | main @ `def2c28`，HEAD == origin/main，0/0；审查前后一致 | `git rev-parse HEAD origin/main`；`git status -b` |
| 提交历史 | 本轮 t2/t3 零提交、零推送；reflog 仅 commit/merge/checkout，无 reset/rebase/amend/强推 | `git log --oneline -5`；`git reflog -5`；`git branch -a`（无 feat/issue-021） |
| 未提交/未跟踪 | 恰好 5 条目 = t2 inScope 4 路径 + t1 基线单；无缓存/日志/密钥/实测数据/构建产物混入（`.pytest_cache`/`.mypy_cache`/`.ruff_cache`/`.venv` 均 `git check-ignore` 命中） | `git status --porcelain=v1 -b`（审查前后逐字节一致）；`git ls-files --others --exclude-standard` 仅 4 个新文件 |
| 单 Issue 粒度 | 全部改动同属 ISSUE-021（实现+测试+计划+M04 一行）；无混入其他 Issue | 工作树范围 + `git diff` 逐文件核对 |
| 公共契约变更 | 零：未改 core/transport/stream/backend 基类；新模块只消费冻结接口；无新依赖 | 工作树范围；backend.py import 清单 |
| diff 检查 | `git diff --check` clean | exit 0 |

## 6. 测试与验证结果

环境：WSL Ubuntu；python3 = **Python 3.12.3**；venv = `./.venv/Scripts/python.exe` = **Python 3.13.14**（Windows，经 WSL interop）；两解释器均 numpy 2.5.2 / pytest 8.4.2（与 t1/t2 口径一致）。所有命令在项目根执行，测试只读、无残留。

| # | 命令（实际执行） | 解释器 | 退出码 | 实测结果 | t2 声称 |
|---|---|---|---|---|---|
| 1 | `python -m pytest tests/contract/test_librevna_backend.py -q` | venv 3.13.14 | 0 | **39 passed in 0.11s** | 39 passed in 0.15s ✓ |
| 2 | `python -m pytest tests/contract/test_librevna_transport.py tests/contract/test_librevna_stream.py tests/contract/test_acquisition_backend.py tests/contract/test_acquisition_controller.py tests/contract/test_librevna_backend.py -q` | venv 3.13.14 | 0 | **266 passed in 4.20s**（50+61+28+88+39） | 266 passed in 4.36s ✓ |
| 3 | `python tools/quality/verify.py`（独立复跑 2 次） | venv 3.13.14 | 0（`verify.py` 四门禁全过才 return 0） | **892 passed, 1 deselected**；ruff All checks passed!；mypy Success: no issues found in **42 source files**；package import ok；[quality] all gates passed | exit 0；892 passed/1 deselected；42 文件 ✓ |
| 4 | `python -m pytest -m "not hardware and not slow" -q`（t2 计数口径） | WSL python3 3.12.3 | 0 | **892 passed, 1 deselected in 133.12s** | 892 passed, 1 deselected in 135.08s ✓ |
| 5 | `python -m ruff check src tests` | venv 3.13.14 | 0 | All checks passed! | ✓ |
| 6 | `python -m mypy src` | venv 3.13.14 | 0 | Success: no issues found in 42 source files | ✓ |
| 7 | `git diff --check` | — | 0 | clean | ✓ |

**黄金向量独立对拍（WSL python3，不依赖被测代码）**：按 `_SWEEP_SETTINGS_FORMAT="<QQHIhBHhH"` 独立 `struct.pack(100e6, 1e9, 101, 100e3, -1000, 0x0C, 0x1240, -1000, 0)` 逐字节等于测试黄金向量 `00e1f505…18fc0000`（31 字节）；按 `_DEVICE_INFO_FORMAT`（57 字节）独立解包黄金 payload，逐字段等于测试断言（protocol 14 / fw 1.2.3 / hw 5/A / 100 MHz–6 GHz / IFBW 1–1 MHz / 10001 points / −30..+10 dBm / 2 ports）。S11 黄金值 `(0.5−0.2j)/1.0` 独立复算一致。

**变异探针（t2 未覆盖的关键反例，工作区外 `D:\tmp\issue021_probes` 运行后已删除，7/7 通过，项目内零残留）**：
- P1 单点轴偏离 +2 Hz（整体轴不偏）→ 首道前 `BackendConfigRejectedError`，traces==0；
- P2 部分 sweep（5/11 点）+ 完整 sweep 同流 → 仅完整道分配 trace_index 0，dropped/incomplete 各 1；
- P3 S11 计算对接收机顺序与无关 stage-1 接收机（desc 0x21）不敏感；
- P4 configure 命令等待期 cancel → `BackendCancelledError`，状态 OPEN 可重配；
- P5 open 的 DEVICE_INFO 等待期 close → `BackendClosedError`，adapter 已释放，状态 CLOSED；
- P6 requested/applied 量化与 `ConfigDiff` 契约（含 0.01 dBm 功率量化）；
- P7 `stages_bitmap=0x1241` 编码硬拒（无 S22 第二路径入口）。

**审查前后工作树状态**：`git status --porcelain=v1 -b` 逐字节一致（`## main...origin/main` + M04 1 行 M + 4 个 ?? 其中 3 个为 t2 inScope 新文件、1 个为 t1 基线单）；无新增缓存/日志/实测数据（全部 git-ignored）。审查期间唯一新增文件为本报告（审查交付物，与 ISSUE-019/020 复审同口径）。

## 7. 报告与事实差异

| 项 | 结论 |
|---|---|
| 测试数字 | 全部复现一致（39 / 266 / 892+1 deselected / ruff / mypy 42 文件 / diff-check）；耗时为机器负载差异（0.11s vs 0.15s、4.20s vs 4.36s、venv 全量 287.62s vs python3 全量 133.12s vs 声称 135.08s——后两者同解释器同口径，几乎一致） |
| 红灯过程声明 | 「首轮 32 passed/7 failed」为过程声明，无保留产物，**无法独立验证**；执行日志记录了 7 项具体修复（adapter is_open 属性、reopen 复位、量化用例数据、reconfigure 轴更新、cancel/close 用真实时钟、check_timeout 时钟单位、pending 弹出），与最终代码形态一致，未发现反证 |
| 范围声明 | 「changedPaths 与 inScope 4 路径逐一相等」实测属实；「不 commit/push」实测属实；「M04 仅状态行一行」`git diff` 实测属实 |
| P3 承接声明 | t2 声称 P3-3 落实、P3-2 显式延后：实测 `backend.py:630–638` 捕获映射落实；stream.py 未动、D11 记录延后——属实。M04 L44「5 项 P3 建议随 ISSUE-021 顺带关闭」措辞比实际（关闭 P3-3 + 记录性延后其余）略强，已列 P3-D，不构成事实矛盾（ISSUE-020 复审原文允许「显式记录」口径） |
| 参考源哈希 | t1 记录的 4 个源哈希 + 1 个黄金夹具哈希本次独立实测全部相等（第 2 节） |

## 8. 剩余风险

1. **真机未验证**：无硬件 simulator 已覆盖契约；真实 LibreVNA 数值/时序/固件行为归 ISSUE-023 硬件基准（M04 L170–178 已声明无指定真机时保持 Blocked 的约束属于 ISSUE-023）。
2. **轴容差 1.0 Hz 为保守门禁**：真机实际轴量化行为需 ISSUE-023 复核；当前 fail-closed 方向安全。
3. **VNA_DATAPOINT 跳 CRC** 为参考既有协议行为（结构校验兜底），未"修复"，保持 ISSUE-020 D2 口径。
4. **断开语义不完整**：命令期断开映射/generation、重连退避、配置重确认均归 ISSUE-023（P3-B）；ISSUE-021 已保证 fail-closed 与 acquire 期 generation 递增。
5. **帧层噪声丢弃 O(n²)**（ISSUE-020 P3-4）：内存有界、验收满足；CPU 硬化留 ISSUE-023 前评估。
6. **部分统计口径**：completed-but-inconsistent sweep（非单调轴，`LibreVnaSweepError`）不计入 dropped/incomplete 统计（ISSUE-020 冻结行为），仅影响可观测性不影响数据安全。

## 9. 合并建议

**建议合并（PASS）**：按 ISSUE-017/018/019/020 的自动化授权流程，由 captain 将以下文件一次提交并推送 `main`（origin/main 当前与 HEAD 同步 0/0，无冲突）：

1. `src/uav_gpr/acquisition/librevna/backend.py`
2. `tests/contract/test_librevna_backend.py`
3. `docs/plans/2026-09-02-issue-021-librevna-backend.md`
4. `docs/issues/M04_LIBREVNA.md`（合并时把 L81 状态行更新为 `Done` 并附本报告链接，沿用 ISSUE-019/020 状态行写法）
5. `docs/reports/ISSUE_021_BASELINE_CONFIRMATION.md`（t1 交付物，随批入档）
6. `docs/reports/ISSUE_021_REVIEW_REPORT.md`（本报告）

合并后停止，不进入 ISSUE-022。提交信息建议沿用先例：`feat(acquisition): LibreVNA S11 production backend (ISSUE-021)`。

## 10. 最小修复清单

无阻止合并项（0 P0/P1/P2）。可选 P3 硬化（均可留 ISSUE-022/023 顺带处理）：

1. （P3-A，可选）合并/注释 `_verify_contract_tolerance` 的防御定位，避免误导后续读者以为配置期容差检查是主执行面。
2. （P3-C，可选）`SweepSettings.__post_init__` 增加 `dwell_us ≤ 0xFFFF`、`points ≤ 0xFFFF`、`start/stop ≤ 2^64−1` 上界，将裸 `struct.error` 前移为 `ValueError`。
3. （P3-B，归 ISSUE-023）命令期断开映射 `BackendDisconnectedError` + generation 递增，与 acquire 期一致。
4. （P3-D，归 ISSUE-022/023）`ReceiverSlot` 构造校验（stage ∈ 0..7、mask ≠ 0）+ 2 测试；M04 L44 措辞可在 ISSUE-022 合并时顺带修正为「P3-3 已随 ISSUE-021 关闭，其余按记录延后」。
5. （P3-F，可选）计划 §7 测试矩阵名与实现最终名对齐。

> 复审结束：审查者立即停止，未修改任何实现/测试/计划/文档（本报告除外），等待项目负责人/自动化流水线决策。
