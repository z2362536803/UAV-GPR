# ISSUE-021 开工基线确认单（只读核查 + 实施计划摘要）

日期：2026-09-02（ISSUE-021 开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-021-librevna-backend`（执行器 engineer，任务 t1，attempt e53aa0ed-6d84-4c2e-ad32-29993a0e1340）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支、未修改实现/测试/计划文件。
配套文件：本单为 t2（S11 生产采集后端）与 t3（独立复审）的权威基线件；独立复审按 [ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) 执行。t2 完整实施契约见 t2 计划文档（`docs/plans/2026-09-02-issue-021-librevna-backend.md`，由 t2 先落盘）。

## 1. 锁定的目标 Issue 与依据

**ISSUE-021：S11 生产采集后端**（`docs/issues/M04_LIBREVNA.md` 第 3 个条目，状态 `Planned`，L79–114）

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-021（M04_LIBREVNA.md L79–114） | docs/issues/README.md 依赖顺序主表 L88 |
| 直接依赖 | ISSUE-017（采集控制器与暂停/停止状态机）、ISSUE-020（LibreVNA 包流与严格 sweep 组装器） | M04 L82「直接依赖：ISSUE-017、020」；README.md L88 |
| 依赖状态 | 均 **Done**：ISSUE-017（2026-08-31 Round-2 独立复审 VERDICT=PASS 后授权合并，M03 L81 状态行实测 Done）；ISSUE-020（2026-09-02 独立复审 VERDICT=PASS 后自动化授权合并，M04 L44 状态行实测 Done，注明「5 项 P3 建议随 ISSUE-021 顺带关闭」） | M03 L81；M04 L44；docs/reports/ISSUE_017_REVIEW_REPORT.md、ISSUE_020_REVIEW_REPORT.md；git log（见 3.2） |
| 功能映射 | FR-003、FR-004；`ACQUISITION.md` §1–§5（生产单一路径、backend 契约、配置回读、sweep 完整性） | M04 L83 |
| 提示词工作区替换 | `E:\wurenjiruanjian` → 工作区根（见第 2 节） | docs/issues/README.md 第 3 节通用协议 |
| 一次一 Issue | 本轮只处理 ISSUE-021；不进入 ISSUE-022 | docs/issues/README.md 第 1/3 节；团队目标 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

参考项目原始路径 `E:\钢筋仪软件开发`、`E:\UVA_GPR_system` 不在本机挂载范围内；**ISSUE-021 的参考源以 ISSUE-001 manifest 冻结的内容哈希为可移植事实**。本地只读副本 `D:\博士任务\rebar-inspector`（WSL `/mnt/d/博士任务/rebar-inspector`，`main @ 7c522d2aebe6a835acb969e8012565715f64a238`，工作树显示 M 均为 CRLF 行尾差异）本次实测 4 个 ISSUE-021 迁移源 SHA-256 与 manifest/迁移记录逐一相等：

| 参考文件（src/rebar_inspector/acquisition/） | 本次实测 SHA-256 | 对应 ISSUE-021 采用面 |
|---|---|---|
| `librevna_protocol.py` | `6a41c4b11cfa8272c1687893a53219ea0465987a378700a0b74ab3973353d8ce` | DeviceInfo/SweepSettings 结构、desc 位掩码、`datapoint_to_s11`（Port1/Reference 比值） |
| `librevna_usb.py` | `a73adc1c41f186ee51ab938c4c245809bf36dea0bc2769ad1115fffa99b22d87` | 后端 acquire 循环/pending/暂停恢复/超时/NACK fail-closed 语义参考 |
| `backend.py` | `f05da35cdee84604d43945da8c30854a289fb7de36a90a3c46c110cf8ab3340f` | 参考后端生命周期/能力/acquire 编排 |
| `sweep_config.py` | `9877b7619747c07aeb7657ba3667322c2687396040bb00193afd5d8508c44801` | SweepSettings 编码/校验与 stages_bitmap |

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      main（当前；工作树完全干净）
HEAD        def2c28  docs(issues): mark ISSUE-020 Done after automated authorized merge
            完整哈希 def2c28d759c92c443ad81354227e39bb5a7ca11（2026-09-02）
分支关系    main...origin/main = 0/0（`git rev-parse HEAD` == `git rev-parse origin/main`）
git status --porcelain=v1 -b
    ## main...origin/main
    （无其他条目——无未跟踪/未提交文件，untracked-count=0）
git diff --check    # clean（exit 0）
```

reflog 实测仅 commit/merge/checkout 记录（顶层 `def2c28 commit` ← `0d465e6 merge` ← `2c3941d checkout` ← `893f800 commit` …），**无 reset/rebase/amend/强推迹象**。

### 3.2 直接依赖 ISSUE-017 / ISSUE-020 的合入证据（main 内实测）

| 提交 | 内容 |
|---|---|
| `1ceca4e` | `feat(acquisition): acquisition controller with pause/stop state machine (ISSUE-017)` |
| `b8712c5` | `Merge feat/issue-017: ISSUE-017 acquisition controller` |
| `9406b60` | `docs(issues): mark ISSUE-017 Done after authorized merge` |
| `893f800` | `feat(acquisition): libreVNA packet stream and strict sweep assembler (ISSUE-020)` |
| `0d465e6` | `Merge feat/issue-020: ISSUE-020 libreVNA packet stream and strict sweep assembler` |
| `def2c28` | `docs(issues): mark ISSUE-020 Done after automated authorized merge`（当前 HEAD） |

tracked 交付物（main，`git ls-files` + 实测复现）：

| 交付物 | 实测事实 | ISSUE-021 复用/依据点 |
|---|---|---|
| `src/uav_gpr/acquisition/controller.py`（ISSUE-017） | `tests/contract/test_acquisition_controller.py` 实测 **88 passed**；M03 L81 状态行 Done（Round-2 VERDICT=PASS，P1-01 与 3 项 P3 已关闭） | ISSUE-021 消费方：`AcquisitionController` 唯一拥有 backend worker，编排 configure/acquire；backend 只实现 `_do_*` hook 即可接入 |
| `src/uav_gpr/acquisition/backend.py`（725 行，ISSUE-015） | `tests/contract/test_acquisition_backend.py` 实测 **28 passed** | **ISSUE-021 直接继承**：`AcquisitionBackend`（L159–385）拥有严格生命周期状态机（`CLOSED --open--> OPEN --configure--> CONFIGURED --acquire*--> CONFIGURED`，cancel/close 幂等并唤醒阻塞 acquire）、`connection_generation`（open 置 1，断连 +1）、`acquire(timeout_s)` 校验与 `_wait_cancellable` 辅助；`LibreVnaUsbBackend` 只需实现 `_do_open/_do_configure/_do_acquire/_do_close` 四个 hook |
| `src/uav_gpr/acquisition/librevna/transport.py`（501 行，ISSUE-019） | 实测 **50 passed**；`LibreVnaUsbTransport`（L450–498：open/read/write/cancel/close）、`UsbAdapter` Protocol + `PyUsbAdapter`（惰性加载 pyusb）、`encode_packet(packet_type, payload)`、`PacketStream`（帧/CRC/长度上限 8..4096、噪声同步）、`LibreVnaTransportError` 家族（8 类型化子类） | **ISSUE-021 直接消费**：USB 会话 + 发送 `SWEEP_SETTINGS`/`REQUEST_DEVICE_INFO`/`SET_IDLE` 控制包 + 读取 datapoint 字节流 |
| `src/uav_gpr/acquisition/librevna/stream.py`（548 行，ISSUE-020） | 实测 **61 passed**；`VNADatapoint`/`parse_vna_datapoint`、`LibreVnaPacketStream.feed(data) -> list[VNADatapoint]`（malformed/ignored 统计）、`ReceiverSlot`/`S11_RECEIVER_PLAN`/`datapoint_matches_plan`、`StrictSweepAssembler(expected_points, receiver_plan, timeout_ms, clock)` → `feed_datapoint(dp) -> AssembledSweep | None`、`check_timeout()`（抛 `LibreVnaSweepTimeoutError`）、`SweepAssemblerStats`（timeouts⊂incomplete⊂dropped）、`AssembledSweep(points, started_at)`（中间 sweep，无 S 参数数值） | **ISSUE-021 直接消费**：字节流 → datapoint → 严格组装 → S11 比值计算 → FrequencySweep + TraceMetadata |
| `docs/plans/2026-09-02-issue-020-librevna-stream.md`（207 行） | ISSUE-020 迁移记录/设计决策 D1–D8/执行日志（853 passed 基线） | 迁移记录口径；`SweepSettings` 编码/`DeviceInfo` 解码被 ISSUE-019 明确排除并指向 ISSUE-021（ISSUE-019 计划 §2/§4 L73–74） |
| `docs/reports/ISSUE_020_REVIEW_REPORT.md` | VERDICT=PASS（10/10 验收 PASS，0 P0/P1/P2，5 个 P3）；合并建议落实于 `def2c28` | 本单结构模板；P3 观察项（见 3.4 约束 8/9/10） |
| `docs/issues/M03_ACQUISITION.md` L81 / `docs/issues/M04_LIBREVNA.md` L44 | 状态行实测均为 Done（含复审报告链接） | ISSUE-021 直接依赖已完成 |

### 3.3 ISSUE-021 为下一个可执行 Issue

- M04 L79–114：ISSUE-021 状态 `Planned`（L81）；L82 `直接依赖：ISSUE-017、020`（均已 Done）。
- docs/issues/README.md L88：`021 | S11 生产采集后端 | 017, 020`——依赖表无计划冲突；M04 其余条目（022/023）均 `Planned` 且依赖 ISSUE-021，**ISSUE-021 是 M04 当前唯一可执行项**。
- 落点核查：`docs/plans/` 与 `docs/reports/` 无任何 ISSUE-021 文件；`src/uav_gpr/acquisition/librevna/` 仅 `__init__.py`（占位，1 行 docstring）、`transport.py`、`stream.py`，无 backend 模块——t2 交付物将是唯一新改动。

### 3.4 对 ISSUE-021 有约束的契约要点（读自 ACQUISITION.md、M04 L79–114、ISSUE-019/020 迁移记录与复审报告、ISSUE_REVIEW_STANDARD.md、AGENTS.md、实测源码）

**ISSUE-021 范围（M04 L85–90）+ 提示词**：

1. open/capabilities/configure/start/acquire/cancel/close——基类 `AcquisitionBackend` 已拥有生命周期状态机与 `connection_generation`（backend.py L159–385）；`LibreVnaUsbBackend` 实现四个 `_do_*` hook。capability 须含 device identity（`DeviceInfo` 回读，ISSUE-019 排除项）与支持通道（S11 最小生产面）。
2. sweep settings 发送/回读、实际频率轴、Port1/Reference 计算 S11——`SWEEP_SETTINGS`（type 2）/`REQUEST_DEVICE_INFO`（type 15）/`DEVICE_INFO`（type 5）控制包经 `transport.encode_packet` 发送；`SweepSettings` 编码/校验与 `stages_bitmap`、`DeviceInfo` 解码均为 ISSUE-019/020 显式排除并指向 ISSUE-021（ISSUE-019 计划 L73–74）；S11 = Port1 ÷ Reference 复数比值（参考 `datapoint_to_s11`，哈希见第 2 节），assembler 只做输入校验不产出 S 参数（ISSUE-020 复审 §8-5）。
3. 真实 UTC+monotonic sweep 边界、device identity、connection generation——`TraceMetadata` 契约（core/metadata.py L93–202）：`sweep_started/midpoint/finished` 的 UTC 与 monotonic 均须 `start <= midpoint <= finish`、`connection_generation` 非负、`trace_index` 从 0 递增；`AssembledSweep.started_at`（注入 clock 值）由 backend 映射为真实 sweep 边界时间（stream.py L328–338）。
4. USB 线程边界、超时和安全停止；协议夹具模拟——设备 I/O 不运行在 UI 主线程（ACQUISITION.md §1、AGENTS.md §7）；超时经 `check_timeout()`/read timeout；无硬件默认不枚举 USB（AST 守卫，见约束 11）。

**配置契约（ACQUISITION.md §4 + core/config.py）**：

5. `requested_config`（冻结 `MissionConfig`）与 `applied_config`（硬件回读/确认）分别记录，配 `config_diff`（`ConfigDiff` 只含契约字段、字段唯一、按契约字段排序、条目必须实际变化）；`AppliedConfig(config, diff)`（backend.py L87–92）。`MissionConfig` 契约字段（config.py L254–283）：`frequency_start_hz/frequency_stop_hz/frequency_points/if_bw_hz/power_dbm/channels/acquisition_mode/…`；摘要为规范化 JSON 的 SHA256。
6. **频率轴以设备实际输出/确认值为准；实际轴与任务契约超差时任务在第一道前拒绝，不得采到一半才改变 axis**（ACQUISITION.md §4 末段、M04 验收标准第 2 条）。

**sweep 完整性（ACQUISITION.md §5 + M04 验收标准）**：

7. 只有完整、校验通过且通道齐全的 sweep 才能进入存储；**`trace_index` 只在完整 sweep 被任务接受时分配**；超时或缺点的 sweep 不零填、不冒充完整道（assembler 已保证不产出假完整 sweep，backend 侧不得绕过）。部分/坏 sweep 只记统计（`SweepAssemblerStats`/`PacketStreamStats`）。

**其他约束**：

8. 排除项（M04 L88–90 + 提示词）：不实现 S22、校准、IFFT、HDF5、UI 或第二条 SCPI/GUI 路径；**禁止增加 TCP/SCPI/LibreVNA-GUI 第二路径**；不自动启动 LibreVNA-GUI。
9. ISSUE-020 复审 P3 建议（§10 最小修复清单，注明「随 ISSUE-021 顺带关闭」，t2 应落实或显式记录决策）：**P3-2** `ReceiverSlot` 构造校验 `stage ∈ 0..7`、`mask ≠ 0` + 2 个测试；**P3-3** 确认 `LibreVnaSweepTimeoutError` 是否改继承 `LibreVnaSweepError`（catch 兼容）或在 ISSUE-021 计划显式记录捕获两者；**P3-1** 文档措辞（参考重复接收机静默采用末值，UAV 加强为拒绝）；**P3-4** 帧层噪声 O(n²)（可选，ISSUE-023 前评估）；**P3-5** ISSUE-020 t1 单计划文件名指针（可选）。
10. ISSUE-020 复审剩余风险（§8）对 ISSUE-021 的承接：**NACK 等控制包在流层仅计数（ignored_packets），参考后端在 acquire 中 fail-closed（`LibreVnaNackError`）——该路由是 ISSUE-021 backend 职责，接线时须补 NACK 中断测试**（stream.py docstring 已显式声明该边界）；损坏 datapoint payload 流层计数继续（ISSUE-020 设计决策 D2），ISSUE-021 可按需升级 fail-closed；VNA_DATAPOINT 跳 CRC 为参考既有协议行为不"修复"。
11. 测试纪律：失败测试优先（先红灯后绿灯）；协议模拟器/合成字节流夹具（无硬件），**默认测试不枚举 USB**——`tests/unit/test_no_external_access.py` AST 守卫禁 `serial/usb/socket/requests/urllib/http/websocket(s)` 根导入；禁止固定 `sleep` 猜时序（事件/时钟注入驱动，`StrictSweepAssembler` 已支持注入 clock）；AGENTS.md §10 每能力覆盖正常/错误/取消/恢复路径；禁删测试/降断言/吞异常。
12. 结构化错误：沿用既有模式（`DomainError` + `ErrorCode.INVALID_ARGUMENT` + 类级 `_reason` + 类型化子类；core `ErrorCode` 枚举只读）；LibreVNA 错误家族继承链 `LibreVnaStreamError(LibreVnaTransportError)`，backend 侧错误按 `BackendError` 模式或复用现有家族，t2 计划文档须固定。
13. 依赖方向 acquisition→core 合规；不新增依赖；不改 `core/**`、`acquisition/backend.py`、`librevna/transport.py`、`librevna/stream.py`、`librevna/__init__.py`（只读消费）。

### 3.5 发现的事实（非阻塞，供 t2 注意）

1. 工作树完全干净且与远端同步（`## main...origin/main` 0/0）；t2 交付物将是唯一新改动。
2. 落点为空：`src/uav_gpr/acquisition/librevna/` 无 backend 模块、无 021 计划文档、无 021 报告——t2 新增 backend 模块与契约测试（精确路径建议见第 5 节）。
3. ISSUE-021 是三个已冻结接口的接线点：`AcquisitionBackend`（生命周期/generation）+ `LibreVnaUsbTransport`（USB 会话/控制包）+ `LibreVnaPacketStream`/`StrictSweepAssembler`（字节流→完整 sweep）；三者均有测试（28+50+61）且 main 内实测全绿。
4. `SweepSettings`/`DeviceInfo` 编解码落点（扩展 transport.py vs 新模块）是 ISSUE-019 遗留的开放决策，t2 计划文档须固定（不改已冻结 transport 契约的前提下，倾向独立于 transport.py 的编解码函数或 backend 模块内部实现，由 t2 与 captain 确认）。
5. applied axis 回读与「第一道前超差拒绝」的判定语义（允许差异阈值、如何从模拟器回读）须由 t2 契约测试固定（ACQUISITION.md §4 未给数值，参考 `ConfigDiff` 语义）。
6. 参考 `librevna_usb.py` 的 acquire/pending 队列/暂停恢复与 UAV 侧 `AcquisitionBackend` 契约的差异（ISSUE-020 已把 pending 路由定为 backend 职责）须在 t2 计划中显式记录（如 feed 内联 vs pending 队列、NACK fail-closed 位置）。

## 4. 门禁基线（核查时实测复跑，2026-09-02）

环境：WSL Ubuntu / Python 3.12.3；numpy 2.5.2、pytest 8.4.2、h5py 3.16.0、ruff 0.16.4、mypy 1.20.2；`uav_gpr` editable 可导入（`src/`）；解释器路径 `python3`（与 ISSUE-019/020 基线单同口径）。

```text
$ python3 -m pytest tests/contract/test_librevna_transport.py \
    tests/contract/test_librevna_stream.py \
    tests/contract/test_acquisition_backend.py \
    tests/contract/test_acquisition_controller.py -q
227 passed in 4.59s        # 依赖定向：ISSUE-019：50 + ISSUE-020：61 + ISSUE-015：28 + ISSUE-017：88

$ python3 tools/quality/verify.py
853 passed, 1 deselected in 132.51s (0:02:12)   # 全量非硬件 pytest（预期 853 达成）
All checks passed!                               # ruff（check src tests）
Success: no issues found in 41 source files      # mypy
package import ok                                # import 检查
[quality] all gates passed                       # verify.py 全部通过时 exit 0（无 [exit code] 标记）

$ python3 -m ruff check src tests                # 补充显式复跑
All checks passed!
$ python3 -m mypy src                            # 补充显式复跑
Success: no issues found in 41 source files
```

核查前后 `git status --porcelain=v1 -b` **逐字节一致**（仅 `## main...origin/main` 一行；untracked-count=0）；`git diff --check` clean；`.pytest_cache/`/`.mypy_cache/`/`.ruff_cache/` 均 `git check-ignore` 确认已忽略，无新缓存/日志/实测数据残留。

## 5. ISSUE-021 实施计划摘要（供 t2 契约；完整版见 t2 计划文档，不含实现代码）

### 范围（M04 L85–90 原文口径 + 提示词）

1. 新模块（建议单一模块 `src/uav_gpr/acquisition/librevna/backend.py`，t2 契约 inScope 为准）：`LibreVnaUsbBackend(AcquisitionBackend)` 实现四个 `_do_*` hook——`_do_open`（`LibreVnaUsbTransport` 打开 + `DeviceInfo` 回读 → `Capabilities(device_id, channels, …)`）；`_do_configure`（`SweepSettings` 编码发送 + applied config/axis 回读 → `AppliedConfig(config, diff)`，第一道前 requested/applied 超差拒绝）；`_do_acquire`（USB read → `LibreVnaPacketStream.feed` → `StrictSweepAssembler.feed_datapoint`/`check_timeout` → Port1÷Reference 复数 S11 → `FrequencySweep` + 真实 UTC/monotonic 边界的 `TraceMetadata`（含 connection_generation；trace_index 仅完整 sweep 分配）；NACK fail-closed 路由）；`_do_close`（幂等、无泄漏、不启动 LibreVNA-GUI）。
2. 协议模拟器夹具：FakeAdapter（复用 ISSUE-019 范式）+ 合成字节流（复用 ISSUE-020 黄金向量范式），覆盖正常/错误/取消/恢复路径，无硬件默认不枚举 USB。
3. 结构化错误沿用既有模式；P3-2/P3-3 随本 Issue 落实或显式记录决策（第 3.4 约束 9）。
4. SweepSettings/DeviceInfo 编解码与 applied 回读语义、pending 路由差异（第 3.5 事实 4/5/6）在 t2 计划文档固定。

### 排除项（M04 L88–90 + 提示词，t2 不得越界）

不实现 S22、校准、IFFT、HDF5、UI；不增加 TCP/SCPI/LibreVNA-GUI 第二路径；不自动启动 LibreVNA-GUI；不改 `core/**`、`acquisition/backend.py`、`librevna/transport.py`、`librevna/stream.py`、`librevna/__init__.py`（只读消费）；不改两个参考仓库；不 commit/push/merge、不创建/切换分支；不进入 ISSUE-022。

### 验收标准（M04 L92–96 原文，t2 不得削弱）

1. 无硬件协议 simulator 下符合 backend 契约。
2. axis/config 超差在第一道前拒绝；不完整 sweep 不分配正式 trace。
3. close/cancel 无泄漏，不自动启动 LibreVNA-GUI。

### 测试矩阵要点（提示词必测项 + 依赖回归）

- backend 生命周期全路径：open/configure/acquire/cancel/close、非法转换结构化拒绝、cancel/close 幂等并唤醒阻塞 acquire、重复 open/close 无残留（基类状态机 + `_do_*` 接线）；
- configure：SweepSettings 编码黄金对拍、applied 回读、axis/config 超差第一道前拒绝、`ConfigDiff` 正确（契约字段、条目实际变化）、重新 configure 需新 mission_id（ISSUE-017 契约）；
- acquire：完整 sweep → S11 数值对拍（Port1/Reference 复数除法，含 0x11 ref+port1 语义）、频率轴与 applied 一致、真实 UTC+monotonic 有序（start≤midpoint≤finish）、trace_index 只在完整 sweep 分配、半道/缺点/坏点/坏分母 → 不分配 + 统计可观测、超时（注入 clock）、NACK fail-closed、cancel/close 中断；
- 回归：ISSUE-019 50 + ISSUE-020 61 + ISSUE-015 28 + ISSUE-017 88；全量 verify.py 853 passed/1 deselected 基线 + ruff + mypy（41 文件）+ import + `git diff --check` + 工作树检查；测试禁固定 sleep、不 import usb/serial/网络根（AST 守卫）。

### inScope 精确路径建议（以任务契约 t2 inScope 为准；t2 完成登记 changedPaths 必须与此逐一相等；沿用 ISSUE-014～020 教训：精确文件路径，非 glob）

1. `src/uav_gpr/acquisition/librevna/backend.py`（新模块：`LibreVnaUsbBackend` + SweepSettings/DeviceInfo 编解码 + S11 计算 + 错误映射，最终拆分以 t2 契约为准）
2. `tests/contract/test_librevna_backend.py`（新测试文件，失败测试优先）
3. `docs/plans/2026-09-02-issue-021-librevna-backend.md`（计划文档，t2 先落盘，含迁移/夹具 provenance、设计决策、执行日志、门禁数字）
4. `docs/issues/M04_LIBREVNA.md`（仅 ISSUE-021 状态行：`Planned → In progress → Review`，勿动其他条目）

（t1 基线单 `docs/reports/ISSUE_021_BASELINE_CONFIRMATION.md` 为 t1 交付物，不计入 t2 inScope；t2 outOfScope：`docs/reports/**`、`docs/REFERENCE_MIGRATION.md`、`docs/ACQUISITION.md`、`docs/reference-baselines/**`、`docs/TESTING.md`、`docs/adr/**`、`tools/**`、参考仓库（只读）、`src/uav_gpr/core/**`（只读消费）、`src/uav_gpr/acquisition/backend.py`（只读消费）、`src/uav_gpr/acquisition/librevna/transport.py`（只读消费）、`src/uav_gpr/acquisition/librevna/stream.py`（只读消费）、`src/uav_gpr/acquisition/librevna/__init__.py`（只读消费）。）

t2 验证命令按任务契约执行：`python3 -m pytest tests/contract/test_librevna_backend.py -q`（定向，先红灯后绿灯）、`python3 tools/quality/verify.py`（全量）、`python3 -m ruff check src tests`、`python3 -m mypy src`、`git diff --check && git status --porcelain=v1 -b`（同 ISSUE-018/019/020 口径，WSL 侧 `python3`）。

若确需拆分模块/新增文件，t2 必须先停止并向 captain 报告，不得自行新增范围外文件。

## 6. 结论

ISSUE-021 开工基线已锁定：`main`/HEAD @ `def2c28`（工作树完全干净、与 origin/main 同步 0/0、reflog 无破坏性操作）；两个直接依赖均 **Done** 合入 main（ISSUE-017：`1ceca4e`+`b8712c5`+`9406b60`，controller.py 与 88 测试实测复现，M03 L81 状态行 Done；ISSUE-020：`893f800`+`0d465e6`+`def2c28`（HEAD），stream.py 与 61 测试实测复现，M04 L44 状态行 Done）；**ISSUE-021 是 M04 当前唯一可执行 Issue**（状态 `Planned`、无实现/测试/计划存在、依赖全绿）；契约要点（backend 生命周期 hook 与 connection_generation、transport/stream 消费面、SweepSettings/DeviceInfo 编码与 applied axis 回读、第一道前超差拒绝、完整 sweep 才分配 trace、NACK fail-closed 路由、P3-2/P3-3 落实、AST 守卫、参考源哈希逐一相等）已固化于第 3.4/3.5/5 节；门禁基线全绿（全量 853 passed / 1 deselected、ruff/mypy(41 文件)/import 全过、依赖定向 227 passed），核查前后 git 状态一致、无残留。**无阻塞项，可开工。** 本确认单即为 t2 实现与 t3 独立复审的权威基线件；t2 按第 5 节契约执行（先落盘计划文档 → 失败测试优先 → 最小实现 → M04 状态行 → 门禁 → 报告），完成后停止，不进入 ISSUE-022。

> 后续记录：本单为开工时点的基线快照，不随 t2 改动；t2 实际完成记录见其完成报告与 `docs/plans/2026-09-02-issue-021-librevna-backend.md`，t3 复审报告独立输出。
