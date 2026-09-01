# ISSUE-022 实施计划：同 sweep S11/S22 双反射采集

日期：2026-09-02
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-022-librevna-dual`（执行器 engineer，任务 t2，attempt 78135fbc-6fa0-437d-bb55-46f68eb3201a）
基线：`main` @ `9d55533f56edffb3906b764b0d414db06d5667cb`（工作树干净、origin/main 同步 0/0）；权威基线件：[docs/reports/ISSUE_022_BASELINE_CONFIRMATION.md](../reports/ISSUE_022_BASELINE_CONFIRMATION.md)（t1）
配套：本计划为 t2 执行契约与 t3 复审依据；迁移/夹具 provenance（第 4 节）按 REFERENCE_MIGRATION.md §5 模板；执行日志随执行过程追加（第 10 节）。

## 1. 目标与用户价值

在 ISSUE-021 已合入的 `LibreVnaUsbBackend`（`src/uav_gpr/acquisition/librevna/backend.py`，只读消费 transport/stream/core）上扩展同 sweep 双反射采集：同一完整 sweep 内从同一 `VNADatapoint` 集合按冻结 `ChannelSpec` 输出 S11/S22 双通道（Port1÷Reference=S11、Port2÷Reference=S22 严格映射 + capability 检查 + 任一通道缺点/坏分母/不支持整道拒绝），共享同一 `TraceMetadata`/`trace_uid`/raw hash（对 `2 × frequency` 整体计算）；默认绑定 `HH:S11`/`VV:S22` 是配置（ChannelSpec）而非数组硬编码；单 S11 路径（0x1240/`S11_RECEIVER_PLAN`/`1 × N`）不回归。价值：M04 门禁「单一真机路径、严格组装和硬件基准完成」的第四步——生产后端双通道能力（ISSUE-023 真机基准的直接基座），落实 ACQUISITION.md §1（通道齐全才入存储）/§3（同 sweep S11/S22 解析）/§5（每通道频点数和顺序、reference 分母）与 DATA_MODEL.md §3（ChannelSpec 绑定）/§5（`channel × frequency` shape、channels 顺序）。

## 2. 范围（M04 L124–130 + 提示词 L143–151 + t1 基线单 §5）

1. 扩展 `src/uav_gpr/acquisition/librevna/backend.py`（唯一实现文件，只读消费冻结层）：
   - **协议面**：`S11_S22_STAGES_BITMAP = 0x1241` 加入 `ALLOWED_STAGES_BITMAPS`（0x1240↔S11-only、0x1241↔双通道；`_validate_stages_bitmap` 同步放行，未验证组合仍硬拒）；`S22_CHANNEL = ChannelSpec("vv_s22", VV, S22, "VV S22")`（与 `S11_CHANNEL` 同构）；`S11S22_RECEIVER_PLAN = S11_RECEIVER_PLAN + (ReceiverSlot(1, REF), ReceiverSlot(1, PORT2))`（ISSUE-020 计划 D5 固定形态，stream.py 只读消费）。
   - **capability**：`_do_open` 返回 `Capabilities(channels=(S11_CHANNEL, S22_CHANNEL), …)`（`supports_dual_channel` 随之成立）。
   - **configure**：`_validate_config` 接受 `(S11,)`、`(S11, S22)`、`(S22, S11)` 三种通道集（S22-only 及其它 S 参数/组合拒绝——0x1241 双反射无单 S22 真机验证值）；按通道数选 stages_bitmap 与 receiver plan；`_compute_sweep_timeout` 按 stage 数（1 或 2）推导。
   - **acquire/finalize**：`_route_packet` 的 `starts_sweep` 判定改用 `assembler.receiver_plan`（不再硬编码 `S11_RECEIVER_PLAN`）；`_finalize_sweep` 按 `config.channels` 逐通道经 `s_parameter → (stage, port_mask)` 绑定（`_S_PARAMETER_SLOTS = {S11: (0, PORT1), S22: (1, PORT2)}`）计算行向量，`data` 形状严格 `channel × frequency`、通道顺序来自配置；单个 sweep 产出单条 trace（一个 `trace_index`/`trace_uid`/时间戳组/raw hash）。
2. 扩展 `tests/contract/test_librevna_backend.py`：双通道黄金夹具（stage-0 desc 0x10/0x01 + stage-1 desc 0x30/0x22，BLOCKED 布局）、数值/顺序/capability/部分通道失败/S11 回归/双 sweep 吞吐。
3. `docs/plans/2026-09-02-issue-022-librevna-dual.md`（本计划文档，t2 先落盘）。
4. `docs/issues/M04_LIBREVNA.md` 仅 ISSUE-022 状态行（L118）：`Planned → In progress → Review`，勿动其他条目。

## 3. 明确排除项（M04 L133–135 + 提示词 + 任务契约）

- 不实现 S21/S12；**禁止连续执行两个独立 sweep 冒充同步双通道**（双通道只来自同一 sweep 的同一 datapoint 集合）；不做校准；
- 不改 `core/**`、`acquisition/backend.py`（基类）、`librevna/transport.py`、`librevna/stream.py`、`librevna/__init__.py`（全部只读消费——双 plan 组合所需 `ReceiverSlot`/`DESC_MASK_REFERENCE`/`DESC_MASK_PORT2`/`S11_RECEIVER_PLAN` 均已由 stream.py 导出）；不改两个参考仓库（本地副本只读）；
- 不分配 `trace_index`/不输出 `FrequencySweep` 给不完整/坏 sweep；不零填；不枚举 USB（AST 守卫）；不新增固定 sleep（注入时钟）；
- 不在 `src/uav_gpr/acquisition/librevna/backend.py`、`tests/contract/test_librevna_backend.py`、`docs/plans/2026-09-02-issue-022-librevna-dual.md`、`docs/issues/M04_LIBREVNA.md` 之外新增任何文件（确需拆分先停止向 captain 报告）；
- 不 commit/push/merge、不创建/切换分支；不进入 ISSUE-023。

## 4. 关联需求/ADR/文档与参考源哈希（迁移清单，REFERENCE_MIGRATION.md §5 模板）

```text
target issue/task:        ISSUE-022 同 sweep S11/S22 双反射采集（M04，FR-003/013、
                          ACQUISITION.md §1/§3/§5、DATA_MODEL.md §3/§5）
reference repository:     钢筋仪软件开发（E:\钢筋仪软件开发；本机不可达）
                          + 本地只读副本 D:\博士任务\rebar-inspector（main @ 7c522d2…）
reference branch + HEAD:  manifest 冻结：feat/issue-16-pause-resume @
                          938875234a99b47d78cfec940671005b63e9d15c（ISSUE-001 冻结时点）
                          本地副本：main @ 7c522d2aebe6a835acb969e8012565715f64a238
reference worktree status:manifest 记录 worktree_dirty=True；ISSUE-021 迁移源哈希已在
                          t1 基线单/复审中对拍（4/4 相等，仅 CRLF 行尾差）
source file(s) + SHA256（ISSUE-021 t1 实测，本次不迁移新源文件）:
  librevna_protocol.py   6a41c4b11cfa8272c1687893a53219ea0465987a378700a0b74ab3973353d8ce
                          （stages_bitmap 0x1240/0x1241 语义 L93–97、
                          datapoint_to_s11/parse_s11_point/parse_s11_s22_point L386–437——
                          S11/S22 比值与双 stage 语义已由 ISSUE-020 计划 §4 D5 冻结）
  librevna_usb.py        a73adc1c41f186ee51ab938c4c245809bf36dea0bc2769ad1115fffa99b22d87
                          （双 stage 下发/接收语义参考）
  tests/test_librevna_usb_backend.py 2d4db31333ef58d586b0f024531ae6f593ea8c38be351708792306272a43bc38
                          （_s11_point_payload 范式；UAV 侧已适配为 BLOCKED 布局夹具）
trusted behavior/contract（采用）:
  - 双反射 stages_bitmap = 0x1241（stage 0 = S11 输入集、stage 1 = S22 输入集；
    每个 VNADatapoint 同时携带两 stage 的接收机，desc 高 3 位区分 stage）；
  - S11 = stage-0 Port1 ÷ stage-0 Reference；S22 = stage-1 Port2 ÷ stage-1 Reference
    （复数除法；任一槽缺失/重复/非有限/reference 幅度 0 → 该 datapoint plan 无效
    → 整道不产出，由冻结的 `datapoint_matches_plan` + `StrictSweepAssembler` 承接）；
  - 同 sweep 双通道共享真实时刻与 trace identity（M04 L124–125）；
  - 通道绑定（HH↔S11、VV↔S22）由 MissionConfig.channels 的 ChannelSpec 表达，
    后端按 s_parameter 字段推导比值，不在数组行号硬编码（DATA_MODEL.md §3/§5）。
excluded behavior（排除）:
  - S21/S12（其余 S 参数组合）；两个顺序 sweep 冒充同步双通道；校准；
  - 暂停/恢复、断线重连/退避/配置重确认、真机数值/吞吐基准 → ISSUE-023；
  - UAV-GPR 全部旧采集代码（含 legacy/continuous 双路径）。
new target module(s):     无新模块——仅扩展 src/uav_gpr/acquisition/librevna/backend.py
UAV-specific adaptations:
  - stages_bitmap 与通道集绑定在 configure 校验层强制：单通道→0x1240、
    双通道→0x1241（codec 层两值均合法，绑定由 backend 保证）；
  - 双 stage 超时推导：expected_s = n_points × n_stages / ifbw × 5（n_stages =
    len(channels) 推导，1 或 2；可注入覆盖，公式与 ISSUE-021 一致只是 stage 数可变）；
  - 输出顺序完全来自 config.channels（允许 S11/S22 任意相对顺序，行值按
    s_parameter 推导，与 Capabilities 顺序无关）；
  - 单 sweep = 单 trace：双通道共享 metadata/uid/raw hash（RawHashSpec 对
    2×N data 整体计算），session_stats["traces"] 按完整 sweep 计数（吞吐口径不变）。
tests/golden fixtures migrated:
  - SweepSettings 0x1241 黄金字节（独立 struct.pack 计算：
    "<QQHIhBHhH" 100e6/1e9/101/100e3/−1000/0x0C/0x1241/−1000/0 →
    `00e1f5050000000000ca9a3b000000006500a086010018fc0c411218fc0000`；
    与 ISSUE-021 S11 黄金向量仅 stages 字段 0x1240→0x1241 不同）；
  - 双通道 datapoint BLOCKED 布局构造范式（头 + reals(ref1,port1,ref2,port2) +
    imags + descs[0x10,0x01,0x30,0x22]），S11/S22 数值断言；
  - 沿用 ScriptedAdapter/TickClock/ManualClock 夹具（ISSUE-021 范式）。
new tests added:          tests/contract/test_librevna_backend.py（扩展，失败测试优先）
numeric or performance comparison: 不适用——无真机、无性能声明（ISSUE-023 负责硬件基准；
                          参考历史数字不得写成新结果）
license/provenance review:参考项目为内部 proprietary；本扩展为既有 UAV 实现的契约级
                          扩展（双 stage 语义源自 ISSUE-020/021 已审计的迁移记录），
                          新代码为独立实现（非逐行复制），docstring 声明来源。
```

## 5. 设计决策（ADR 级，含备选与理由）

| # | 决策 | 理由 | 备选（否决理由） |
|---|---|---|---|
| D1 | 双通道全部扩展落在已合入的 `backend.py` 内；`transport.py`/`stream.py`/`core/**` 只读消费 | t2 契约 inScope 仅 backend.py/test/M04/计划 4 路径；双 plan 组合所需的 `ReceiverSlot`/`DESC_MASK_*`/`S11_RECEIVER_PLAN` 均已导出；组装器 `receiver_plan` 参数在 ISSUE-020 已冻结（D5） | 改 stream.py 加 `S11S22_RECEIVER_PLAN`（违反 inScope/changedPaths 门禁，需先报告 captain） |
| D2 | `S11S22_RECEIVER_PLAN = S11_RECEIVER_PLAN + (ReceiverSlot(1, REF), ReceiverSlot(1, PORT2))` 定义在 backend.py；configure 按通道数选 plan；`_route_packet` 的 `starts_sweep` 改用 `assembler.receiver_plan` | plan 是 backend 的配置面（随 config 冻结），路由判定必须与 configure 选用的 plan 一致；避免两处硬编码漂移 | 路由仍硬编码 S11_RECEIVER_PLAN（双通道下 point 0 永远不匹配 → 起始边界/超时语义错误） |
| D3 | `stages_bitmap` 与通道集绑定在 `_validate_config`/`_build_sweep_settings` 强制：`(S11,)`→0x1240+`S11_RECEIVER_PLAN`；`(S11,S22)`/`(S22,S11)`→0x1241+`S11S22_RECEIVER_PLAN`；S22-only 与其它组合拒绝 | 0x1241 是双反射位图（无单 S22 真机验证值，沿用 ISSUE-021 D9 的「未验证组合不得下发」原则）；codec 层两值均合法，绑定由 backend 保证 | codec 按通道数自动选位图（隐式行为，配置面不可见）；允许 S22-only（无生产验证值） |
| D4 | 输出行值按 `config.channels` 逐通道 `s_parameter → (stage, port_mask)` 绑定计算（`_S_PARAMETER_SLOTS`），允许双通道任意相对顺序；`data = np.asarray(rows)` 形状 `(len(channels), N)` | 「HH:S11/VV:S22 默认绑定是配置不是数组硬编码」验收直接落地：行语义来自 ChannelSpec.s_parameter，与行号无关；DATA_MODEL.md §3「数组通道顺序由 channels 明确给出」 | 固定行0=S11、行1=S22（数组硬编码，违反验收）；只允许 (S11,S22) 顺序（弱化「顺序来自配置」语义） |
| D5 | 单 sweep = 单 trace：双通道共享 `TraceMetadata`/`trace_uid`/UTC+monotonic 边界/`connection_generation`/raw hash（`RawHashSpec` 对 2×N 整体计算）；`session_stats["traces"]` 仍按完整 sweep 计数（双通道吞吐计数口径 = traces 递增，与 ISSUE-021 一致，无新字段） | M04 L124–125「两个通道共享真实时刻与 trace identity」；AGENTS.md 逐道 raw hash 契约；`FrequencyScan` 连续 shape `trace × channel × frequency` 语义一致 | 每通道单独 trace（破坏共享 identity 验收）；新增 per-channel 统计字段（无消费方，超最小实现） |
| D6 | `_compute_sweep_timeout` 按 stage 数推导：`expected_s = points × n_stages / ifbw`，`n_stages = 2 if len(channels) >= 2 else 1`（5x 安全系数与 ISSUE-021 一致） | 双 stage 测量时间约为单 stage 2 倍；超时过短会在真机上误杀合法双通道 sweep（t1 契约要点 9） | 双通道沿用单 stage 超时（真机双 sweep 可能误超时）；直接乘 2 硬编码（与通道数解耦不彻底，但语义等价——选用 len(channels) 推导更可读） |
| D7 | capability 面：`_do_open` 返回 `(S11_CHANNEL, S22_CHANNEL)`（`supports_dual_channel=True`）；现有 open 测试断言随能力扩展更新（同一 inScope 测试文件） | Capabilities.channels 语义是「设备支持的通道绑定集合」（backend.py L69–84）；ISSUE-022 后设备支持双通道 | 保持 `(S11,)`（与实现能力不符，consumers 无法感知双通道） |
| D8 | 既有测试的三处语义更新（同一 inScope 测试文件，非削弱）：①`test_open_requests_device_info_and_set_idle` 的 capabilities 断言更新为双通道；②`test_sweep_settings_validation` 的 0x1241→ValueError 改为 0x1242→ValueError（0x1241 现为合法值）；③`test_configure_rejects_unsupported_channels` 改用 S21 通道（vv_s22 单通道配置改由新 `test_configure_rejects_s22_only` 覆盖拒绝语义） | 断言必须跟随冻结契约的合法值集合演变；「S11-only 不回归」指行为/数值路径不回归，非法值集合变化是本 Issue 的预期契约变化 | 保留 0x1241 拒绝断言（与实现矛盾，测试必红）；删除这些测试（禁删测试） |
| D9 | ISSUE-021 复审 P3-C（`SweepSettings` 上界前移为 ValueError）与 ISSUE-020 P3-2（`ReceiverSlot` 构造校验，需改 stream.py）**本任务不实施**：P3-C 属可选硬化（P3 级，不阻止合并，超最小实现）；P3-2 需改 stream.py（out of scope）——两者均在计划中显式记录延后 | ISSUE-021 复审 §10 明确「均可留 ISSUE-022/023 顺带处理」且标注可选；任务契约 inScope 精确 4 路径 | 顺带实施（超 inScope 或需改冻结文件，违反 changedPaths 门禁） |

## 6. 文件改动（inScope 精确路径，changedPaths 必须与此逐一相等）

| 路径 | 内容 |
|---|---|
| `src/uav_gpr/acquisition/librevna/backend.py` | 扩展：`S11_S22_STAGES_BITMAP`/`ALLOWED_STAGES_BITMAPS` 放行 0x1241、`S22_CHANNEL`、`S11S22_RECEIVER_PLAN`、`_S_PARAMETER_SLOTS`、`_validate_config` 双通道集、configure 按通道数选 plan/bitmap、`_compute_sweep_timeout` 双 stage、`_route_packet` 用 `assembler.receiver_plan`、`_finalize_sweep` 按 config 通道计算 2×N 输出、`_do_open` capabilities 双通道、`_compute_s11` → `_compute_s_parameter(stage, port_mask)` |
| `tests/contract/test_librevna_backend.py` | 扩展：双通道黄金/数值/顺序/capability/部分通道失败/坏分母/S11 回归/双 sweep 吞吐 + 3 处既有断言按 D8 更新 |
| `docs/plans/2026-09-02-issue-022-librevna-dual.md` | 本计划文档（t2 先落盘；执行日志第 10 节随执行追加） |
| `docs/issues/M04_LIBREVNA.md` | 仅 ISSUE-022 状态行（L118）：`Planned → In progress → Review`（勿动其它条目） |

## 7. 测试矩阵（提示词必测项 → 测试名，与实现逐一对应）

| 必测项 | 测试 | 手段 |
|---|---|---|
| 黄金协议夹具 | `test_golden_dual_sweep_settings_encode`（0x1241 字节向量）、`test_sweep_settings_validation`（0x1242 拒） | 独立 struct.pack 计算的固定字节（第 4 节 provenance） |
| 双通道 configure | `test_dual_configure_sends_dual_stages_bitmap`（SWEEP_SETTINGS 载荷含 0x1241；S11-only 仍 0x1240）、`test_configure_rejects_s22_only`、`test_configure_rejects_unsupported_channels`（S21） | 命令脚本 + 载荷断言 |
| capability | `test_open_requests_device_info_and_set_idle`（更新：channels==(S11,S22)、supports_dual_channel） | Capabilities 断言 |
| 双通道 acquire 数值/shape | `test_dual_acquire_values_shape_and_metadata`（2×N、S11/S22 数值、共享 metadata/uid/raw hash） | 双通道 sweep 字节夹具 |
| 通道顺序来自配置 | `test_dual_channel_order_from_config`（(S22,S11) 配置 → 行序对调） | 双通道夹具 + 反序配置 |
| 部分通道失败/坏分母 | `test_dual_partial_channel_failure_no_trace`（缺 stage-1 槽 → 整道拒绝）、`test_dual_s22_zero_reference_no_trace`（stage-1 ref=0 → 整道拒绝） | 统计断言（invalid_points）+ traces==0 |
| 双通道吞吐计数 | `test_dual_two_sweeps_in_one_read`（单 read 两个双 sweep → traces 0/1） | 多 sweep 单 chunk |
| 轴门禁 | `test_dual_first_sweep_axis_mismatch_rejected`（双通道轴偏离 → 首道前拒绝） | shift_hz 夹具 |
| S11-only 回归 | 既有 39 测试全绿（含更新后的 3 处断言）；`test_acquire_complete_sweep_values_and_metadata` 等 1×N 数值不变 | 全量定向复跑 |
| 回归 | 依赖定向 266（含 ISSUE-021 39）；全量 verify.py 892+新增 passed/1 deselected；ruff/mypy/import/`git diff --check` | — |

## 8. 性能/数据风险

- 无性能声明：无真机基准（ISSUE-023 负责）；不把参考历史速度写成新结果。
- 有界性：无新缓存/队列；组装器半道缓冲 ≤ expected_points（冻结）；`_MAX_PENDING_SWEEPS` 不变；双通道每点接收机数（4 槽）由 plan 固定，无长度派生分配路径。
- 数据风险：不落盘、不联网、不修改 raw；`FrequencySweep` 2×N 与 `TraceMetadata` 全走 core 冻结契约（不可变、UTC+monotonic 有序、raw hash 对整体 2×N）；失败路径不分配 trace；S11-only 输出 shape/数值与 ISSUE-021 完全一致。
- 线程风险：本模块不创建线程（由 controller 工作线程调用，AGENTS.md §7/ACQUISITION.md §1）；取消经短读超时 tick + 事件检查；不引入固定 sleep。
- 行为风险：VNA_DATAPOINT 跳 CRC 为参考既有协议行为（结构/点序/轴门禁兜底，不"修复"）；0x1241 的真机验证归 ISSUE-023（模拟器已按 desc stage 语义构造，无硬件时只承诺契约正确）；首道轴容差 1.0 Hz 与 ISSUE-021 一致。

## 9. 完成定义与回退

- 完成定义（全部满足才可登记 completed）：验收标准（M04 L137–141 + 任务契约 7 条）逐条 PASS；定向测试红灯→绿灯记录于执行日志；全量 verify.py + ruff + mypy + import + `git diff --check` 全绿；`git status` 仅 4 个 inScope 路径改动（changedPaths 与 inScope 逐一相等）+ t1 基线单（t1 交付物，不计入 t2 inScope）；M04 状态行更新为 Review；不 commit/push/merge、不创建分支。
- 回退方式：实现为对已合入文件的扩展 + 一份新计划文档 + M04 状态行一行；异常时删除未登记文件并还原 M04 状态行与 backend.py/test 的 diff 即可回到 `main @ 9d55533` 干净基线；无破坏性操作。

## 10. 执行日志（随执行追加）

```text
[2026-09-02] t2 开工：claim t2（attempt 78135fbc-6fa0-437d-bb55-46f68eb3201a）→ in_progress。
[2026-09-02] 参考审计（只读）：stream.py（ReceiverSlot/S11_RECEIVER_PLAN/StrictSweepAssembler
              receiver_plan 参数化/datapoint_matches_plan 逐槽校验）、core enums
              （SParameter.S11/S22、LogicalPolarization.HH/VV）、base backend.py
              （Capabilities/supports_dual_channel、基类 configure 不校验通道）、
              ISSUE-020 计划 D5（双 plan 形态冻结）、ISSUE-021 计划 D9（0x1241 归属）。
[2026-09-02] 计划文档落盘（本文件第 1–9 节）。
[2026-09-02] M04 状态行 L118：Planned → In progress。
[2026-09-02] 失败测试优先（红灯，实现前）：扩展 test_librevna_backend.py 双通道测试
              （黄金 0x1241/configure 位图/capability/数值 shape/顺序/部分通道失败/
              坏分母/双 sweep 吞吐/轴门禁 + 3 处既有断言按 D8 更新）后：
              $ ./.venv/Scripts/python.exe -m pytest tests/contract/test_librevna_backend.py -q
              -> ERROR collection（1 error）：ImportError: cannot import name
                 'S11_S22_STAGES_BITMAP' from 'uav_gpr.acquisition.librevna.backend'
                 ——红灯成立（实现前定向测试失败）。
[2026-09-02] 最小实现：backend.py 双通道扩展（D1–D7 落实）：
              S11_S22_STAGES_BITMAP=0x1241 + ALLOWED_STAGES_BITMAPS 放行、
              S22_CHANNEL(vv_s22)、S11S22_RECEIVER_PLAN（*S11_RECEIVER_PLAN 展开 +
              ReceiverSlot(1,REF)/ReceiverSlot(1,PORT2)）、_S_PARAMETER_SLOTS
              （s_parameter→(stage,port_mask)，S11=(0,PORT1)、S22=(1,PORT2)）、
              _validate_config 通道集（(S11,)/(S11,S22)/(S22,S11)，S22-only 与
              S21/S12 拒绝）、configure 按通道数选 plan/bitmap、_route_packet 改用
              assembler.receiver_plan、_finalize_sweep 按 config 通道逐行计算
              2×N 输出、_compute_s11 → _compute_s_parameter(stage, port_mask)、
              _compute_sweep_timeout 按 stage 数（1/2）、capabilities 双通道。
[2026-09-02] 定向测试（绿灯）：
              $ ./.venv/Scripts/python.exe -m pytest tests/contract/test_librevna_backend.py -q
              -> 48 passed in 0.12s（39 既有含 3 处 D8 更新 + 9 新增双通道）
              ——绿灯成立。（过程：首轮 ruff RUF005 提示 S11S22_RECEIVER_PLAN
              用迭代展开替代元组拼接，修正后 ruff/mypy/48 全绿。）
[2026-09-02] 依赖回归：
              $ ./.venv/Scripts/python.exe -m pytest tests/contract/test_librevna_transport.py \
                  tests/contract/test_librevna_stream.py \
                  tests/contract/test_acquisition_backend.py \
                  tests/contract/test_acquisition_controller.py \
                  tests/contract/test_librevna_backend.py -q
              -> 275 passed in 4.23s（ISSUE-019：50 + ISSUE-020：61 + ISSUE-015：28
                 + ISSUE-017：88 + ISSUE-021/022：48）。
[2026-09-02] 静态检查：
              $ ./.venv/Scripts/python.exe -m ruff check src tests
              -> All checks passed!
              $ ./.venv/Scripts/python.exe -m mypy src
              -> Success: no issues found in 42 source files。
[2026-09-02] 门禁（全量，tools/quality/verify.py）：数字见第 11 节。
[2026-09-02] 工作树/交付检查：git diff --check / git status，数字见第 11 节。
[2026-09-02] M04 状态行：In progress → Review（最终态，2026-09-02）。
```

## 11. 门禁数字（随执行追加）

```text
$ ./.venv/Scripts/python.exe -m pytest tests/contract/test_librevna_backend.py -q
-> 红灯（实现前）：ERROR collection 1 error（ImportError: cannot import name
   'S11_S22_STAGES_BITMAP' from 'uav_gpr.acquisition.librevna.backend'）
-> 绿灯（实现后）：48 passed in 0.12s（39 既有含 3 处 D8 更新 + 9 新增双通道）

$ ./.venv/Scripts/python.exe -m pytest tests/contract/test_librevna_transport.py \
    tests/contract/test_librevna_stream.py \
    tests/contract/test_acquisition_backend.py \
    tests/contract/test_acquisition_controller.py \
    tests/contract/test_librevna_backend.py -q
-> 275 passed in 4.23s（50+61+28+88+48）

$ ./.venv/Scripts/python.exe -m ruff check src tests
-> All checks passed!
$ ./.venv/Scripts/python.exe -m mypy src
-> Success: no issues found in 42 source files

$ ./.venv/Scripts/python.exe tools/quality/verify.py
-> exit 0：[quality] all gates passed
   pytest: 901 passed, 1 deselected in 285.32s（892 基线 + 9 新增双通道）
   ruff: All checks passed!；mypy: 42 source files；import: package import ok

$ git diff --check && git status --porcelain=v1 -b
-> clean（exit 0）；git status 仅 4 个 t2 inScope 路径
   （src/uav_gpr/acquisition/librevna/backend.py、tests/contract/test_librevna_backend.py、
     docs/plans/2026-09-02-issue-022-librevna-dual.md、docs/issues/M04_LIBREVNA.md）
   + t1 基线单 docs/reports/ISSUE_022_BASELINE_CONFIRMATION.md（t1 交付物，不计入 t2）；
   无缓存/日志/实测数据残留（.pytest_cache/.mypy_cache/.ruff_cache git check-ignore 命中）。
```

> 后续记录：本计划的执行日志只记录事实与数字；t3 复审报告独立输出。
