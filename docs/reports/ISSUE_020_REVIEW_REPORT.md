# ISSUE-020 独立复审报告（LibreVNA 包流与严格 sweep 组装器）

日期：2026-09-02
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-020-librevna-stream`（审查器 reviewer，任务 t3，attempt 554dddd8-e63b-474b-b2ab-2150fa7cb454）
依据：[docs/ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) v1.0（§13 固定格式）
性质：独立只读复审。除本报告外未修改任何文件，未 commit/push/merge/clean；变异探针在系统临时目录复制树中运行并已清理（项目内零残留，复核见第 6 节）。

## 1. 审查结论

**VERDICT: PASS**

ISSUE-020 实现真实、完整、合规：3 条验收标准逐条 PASS（第 4 节），无 P0/P1/P2 问题（第 3 节，仅 5 个 P3 观察项），t2 声称的测试命令与数字全部独立复现（第 6 节），Git/交付检查干净（第 5 节），报告与事实无实质性差异（第 7 节）。可进入自动化合并流程。

## 2. 自动识别的审查范围

| 项 | 结论 | 证据 |
|---|---|---|
| Issue | ISSUE-020「LibreVNA 包流与严格 sweep 组装器」（M04 L42–77，FR-003 / ACQUISITION.md §5） | `docs/issues/M04_LIBREVNA.md`；`docs/issues/README.md` L87（依赖 019） |
| 基线/分支 | `main` @ `2c3941d2fb2c025a8e485917aa34b11ee4311028`（HEAD == origin/main，0/0）；t2 未创建分支、未 commit/push/merge | `git rev-parse HEAD`、`git status -b`、`git log --oneline -8`、`git reflog -6`（仅 commit/merge/checkout，无 reset/rebase/amend/强推） |
| 直接依赖 | ISSUE-019 Done（c063dce + 042dce6 合入，2c3941d 标记）；`transport.py`（501 行）帧层契约与 50 测试均在 main 内实测 | `git log`；`src/uav_gpr/acquisition/librevna/transport.py` 全文审读 |
| 改动文件（工作树实测） | ① `src/uav_gpr/acquisition/librevna/stream.py`（548 行，新）② `tests/contract/test_librevna_stream.py`（913 行，61 测试，新）③ `docs/plans/2026-09-02-issue-020-librevna-stream.md`（207 行，新）④ `docs/issues/M04_LIBREVNA.md`（仅 L44 状态行 `Planned → Review`，`git diff` 证实只改一行） | `git status --porcelain=v1 -b`（5 条目：上述 4 项 + t1 基线单 `docs/reports/ISSUE_020_BASELINE_CONFIRMATION.md`，后者为 t1 交付物） |
| 排除项确认 | 未改 transport.py/core/backend/`__init__.py`/参考仓库；无配置设备、无 S11/S22 backend metadata、无零填/部分道输出、未进入 ISSUE-021（M04 L79–120 仍 Planned） | 工作树范围 + M04 全文 + stream.py 全文 |
| 参考源哈希对拍 | 4 个参考文件 SHA-256 与计划 §4 provenance 逐一相等：`librevna_usb.py a73adc1c…`、`librevna_protocol.py 6a41c4b1…`、`tests/test_librevna_protocol.py f3019795…`、`tests/test_librevna_usb_backend.py 2d4db313…`；manifest 冻结提交 `9388752` 在本地副本内存在，冻结版 `parse_s11_point` 与本地 main 版仅 CRLF 行尾差 | 本地只读副本 `/mnt/d/博士任务/rebar-inspector`（main @ 7c522d2）`sha256sum`/`git show` 实测 |

审查期间必读资料全部完成：AGENTS.md、docs/INDEX.md、docs/issues/README.md、M04_LIBREVNA.md（ISSUE-020 条目）、ACQUISITION.md §5、ISSUE-019 迁移记录（docs/plans/2026-08-30-issue-019-librevna-transport.md 要点与 transport.py 全文）、t1 基线单、t2 计划文档、REFERENCE_MIGRATION.md §5 模板（13 字段逐一核对）。

## 3. 主要问题（P0→P3）

无 P0 / P1 / P2。以下均为 P3（低风险，不阻止合并）：

- **P3-1**（文档准确性，计划 §4 + `stream.py:40-42` + t1 基线单 §3.4(3)）：三处将「必需槽位重复 → 无效（不静默采用首/末）」表述为参考 `parse_s11_point` 语义；实测冻结参考（`librevna_protocol.py` @9388752，SHA-256 6a41c4b1…，L410–431）对重复接收机是循环覆盖**静默采用末值**。UAV 侧「重复拒绝」行为本身正确且更严格（直接满足 ISSUE-020「严格检测…重复」与 ACQUISITION.md §5「每个通道的频点数和顺序」），属合理加强；建议将迁移记录措辞改为「UAV 加强项：参考实现静默采用末值，UAV 侧按 Issue 要求拒绝重复」。修复方向：仅文档措辞，无代码改动。
- **P3-2**（构造校验完备性，`stream.py:257-265`）：`ReceiverSlot.__post_init__` 接受 `stage > 7`（desc 位掩码 stage 仅 3 位）与 `mask == 0`；此类槽位永不匹配或使所有点无效，属静默配置陷阱。建议构造时校验 `stage in 0..7` 且 `mask != 0`。修复方向：加两条校验 + 2 个测试，可随 ISSUE-021 一并做。
- **P3-3**（错误分类兼容性，`stream.py:110-113`）：`LibreVnaSweepTimeoutError` 直接继承 `LibreVnaStreamError`，而参考后端超时抛的是 `LibreVnaSweepError`（`test_missing_point_then_timeout` 断言）。ISSUE-021 消费时需同时捕获两者；可考虑让 `LibreVnaSweepTimeoutError` 继承 `LibreVnaSweepError` 以获得 catch 兼容，或在该计划中显式记录这一有意拆分。修复方向：设计确认（1 行继承关系或 1 行文档）。
- **P3-4**（性能硬化，继承 ISSUE-019 复审 P3，`transport.py:206`）：帧层噪声逐字节 `del buffer[0]` 最坏 O(n²) 时间；内存始终有界（单帧 ≤4096+头，实测 `test_garbage_flood_buffer_bounded` 缓冲 ≤ MAX_PACKET_LENGTH+8），验收 3 满足。可选：索引指针硬化，留待 ISSUE-023。
- **P3-5**（文档指针，t1 基线单 L166）：t1 建议的计划路径 `docs/plans/2026-08-30-issue-020-sweep-assembler.md` 与 t2 实际文件名 `docs/plans/2026-09-02-issue-020-librevna-stream.md`（与 ISSUE-019 命名惯例一致）不同；t1 基线单自述为开工快照不随 t2 改动，M04 L44 已链向真实文件，无断裂链接。可选：合并时顺带把 t1 单中的旧名改为实际文件名。

## 4. 逐 Issue 验收矩阵

| # | 验收标准（M04 L62–66 + 提示词 + ACQUISITION.md §5） | 状态 | 代码证据（文件:行号） | 实际测试证据 |
|---|---|---|---|---|
| 1 | 任意 byte chunking 得到同一包序列 | **PASS** | 帧层组合复用：`stream.py:200-228`（`LibreVnaPacketStream.feed` 逐 chunk 消费 `PacketStream`）；解析：`stream.py:135-166` | `test_generative_chunking_same_datapoint_sequence`（20 seed × 4 chunk 上限）、`test_generative_chunking_one_byte_granularity`、`test_generative_chunking_same_assembled_sweep`（15 seed，逐 chunk 断言 `sweeps == [ref_sweep]`）、`test_generative_chunking_across_sweep_boundary`、`test_split_across_reads`——61 passed 复现 |
| 2 | 缺点/重复/跨 sweep/坏 CRC/非法分母均不产出假完整 sweep | **PASS** | 状态机：`stream.py:444-483`（point 0 同步/严格连续/重复/前跳/回退/越界/无效作废/失同步段一次计数/绝不拼道）；分母与有限值：`stream.py:284-304`；完成校验：`stream.py:529-548`（频率严格递增，违反抛错且不输出）；坏 CRC：帧层 `transport.py:219-221`（非 datapoint 帧层丢、datapoint 跳 CRC 系参考协议行为不"修复"，结构校验兜底） | `test_duplicate_point_drops_current`、`test_never_stitches_two_sweeps`、`test_stitching_partial_a_then_b_without_zero_rejected`、`test_forward_jump_drops_current`、`test_backward_point_drops_current`、`test_out_of_range_point_drops_current`、`test_invalid_datapoint_drops_current_sweep`、`test_zero_reference_denominator_invalid`、`test_nan/inf_receiver_value_invalid`、`test_non_monotonic_frequency_raises`、`test_no_partial_output_ever`、集成 `test_corrupted_datapoint_payload_no_fake_sweep`、`test_reference_zero_mid_sweep_drops_and_resyncs`、`test_bad_crc_mid_sweep_still_assembles_reference_behavior`；补查探针 P4/P5/P6 全过 |
| 3 | 有界缓存，恶意长度不能分配无限内存 | **PASS** | 帧层长度上限 8..4096（`transport.py:72-73,210-212`，恶意 0xFFFF 逐字节重对齐）；datapoint 解析输入 ≤4088 字节（count≤452，`stream.py:143-153`）；组装器半道缓冲 ≤ expected_points（构造冻结 int，`stream.py:368-401,430`）；无长度派生分配路径 | `test_malicious_length_field_bounded_buffer`（断言内部 buffer ≤ MAX_PACKET_LENGTH+8）、`test_garbage_flood_buffer_bounded`（1MB 随机垃圾）、`test_max_length_packet_accepted_at_frame_cap`（4096 收/4097 拒）；补查探针 P1（4080B=12+9×452 最大合法 datapoint 全链解析+组装；4081B→malformed 计数；4089B 载荷→帧 4097 拒收后重同步）全过 |
| 4 | 超时产生统计/结构化错误，无部分道输出 | **PASS** | `stream.py:485-504`（`check_timeout`：过期 → timeouts/incomplete/dropped +1 后抛 `LibreVnaSweepTimeoutError`，reason=`sweep_timeout`）；注入 clock 无固定 sleep | `test_timeout_drops_and_raises_structured_error`（断言 reason/code）、`test_drop_stats_subsets_after_timeout`（timeouts≤incomplete≤dropped）、`test_timeout_disabled_when_none`、`test_timeout_before_deadline_noop`、`test_timeout_no_active_sweep_noop`；补查探针 P2（超时后旧道尾点不重复计数、新道正常组装）全过 |
| 5 | 参考状态机语义逐条对齐（点 0 同步/整道计数/绝不拼道/非单调抛错） | **PASS** | `stream.py:341-366`（docstring 状态机 6 条）与参考 `ContinuousSweepAssembler`（`librevna_usb.py` L150–409 实测逐条对照：`feed`/`_start_new_sweep`/`_drop_active`/`_complete`/`mark_invalid_datapoint` 分支顺序与计数完全一致；UAV 将 `take_completed` 双步改为 feed 内联返回，计划 D3 记录） | 参考 `ContinuousSweepAssemblerTests`（L235–441）15 用例逐一移植（`TestStrictSweepAssembler` 同名断言、同数字）；补查探针 P7（200 轮随机序列属性循环：绝不部分输出、输出恒有序完整、子集不变量恒成立）全过 |
| 6 | receiver plan/通道字段/分母/有限值/无关接收机忽略 | **PASS** | `stream.py:244-304`（ref 位优先——desc 0x11 判 reference；必需槽位恰一个、重复拒绝、reference 幅度>0、实/虚部有限；非 plan 接收机忽略） | `TestReceiverPlan` 11 用例全过；补查探针 P5（0x11 单独无效、0x11+0x01 有效，与参考 `datapoint_to_s11` 分支语义一致）全过 |
| 7 | 丢弃统计显式可观测（供 ISSUE-021 消费） | **PASS** | `stream.py:174-180`（malformed/ignored）、`stream.py:312-325`（dropped/incomplete/timeouts/duplicate/out_of_range/invalid，子集不变量写于 docstring） | `test_stream_and_assembler_stats_observable`、`test_malformed_datapoint_payload_counted`、`test_non_datapoint_packet_counted_ignored`、`test_bad_crc_non_datapoint_dropped`、`test_stats_reset`、`test_reset_keeps_stats`/`test_reset_clears_buffer_keeps_stats` |
| 8 | 结构化错误沿用 transport 模式；core ErrorCode 只读 | **PASS** | `stream.py:92-113`（`LibreVnaStreamError(LibreVnaTransportError)` + 3 类型化子类 + 类级 `_reason`；`transport.py:83-104` DomainError/INVALID_ARGUMENT 模式）；core 无改动（工作树范围证明） | `test_timeout_drops_and_raises_structured_error` 断言 `reason=="sweep_timeout"`、`code.value=="invalid_argument"`；`test_stream_and_assembler_stats_observable` 断言 MRO |
| 9 | 默认测试不枚举真机/AST 守卫/无固定 sleep | **PASS** | 新测试仅 import stdlib + uav_gpr（`test_librevna_stream.py:22-51`）；AST 守卫扫描 `tests/**/*.py`（`test_no_external_access.py`，禁 serial/usb/socket/网络根 + 参考仓库路径） | `python3 -m pytest tests/unit/test_no_external_access.py -q` → 3 passed 复现；测试用注入 `_Clock` 类，全文无 `time.sleep` |
| 10 | 依赖回归不破坏（ISSUE-019 50 测试等） | **PASS** | 工作树未触 transport.py（只读消费：`stream.py:63-67`） | `python3 -m pytest tests/contract/test_librevna_transport.py tests/unit/test_reference_manifest.py tests/contract/test_acquisition_backend.py -q` → 91 passed 复现 |

## 5. Git 与交付检查

| 检查项 | 结论 |
|---|---|
| 分支/基线 | `main` @ `2c3941d2fb2c025a8e485917aa34b11ee4311028`；`main...origin/main` = 0/0；HEAD 未变（t2 未 commit）——与 t2 报告一致 |
| reflog | 顶层仅 commit/merge/checkout（2c3941d ← 042dce6 ← c0cd067），无 reset/rebase/amend/强推迹象 |
| 提交混合 | 不适用（无新提交）；改动边界干净：仅 M04 一行 + 3 个新文件 + t1 基线单 |
| 范围外修改 | 无。`transport.py`、`core/**`、`acquisition/backend.py`、`librevna/__init__.py`、参考仓库均未动 |
| 缓存/密钥/实测数据 | 无。`.pytest_cache/.mypy_cache/.ruff_cache` 均 `git check-ignore` 命中（.gitignore L6–8）；无实测数据/日志/构建物 |
| 公共契约变更 | 无。新增模块/错误类型均为增量；未改 schema/协议常量（VNA_DATAPOINT 跳 CRC 语义保留并文档化） |
| 依赖顺序 | ISSUE-020 唯一依赖 ISSUE-019 已 Done 合入；M04 后续 021–023 状态仍 Planned，未越界 |
| 审查前后工作区一致 | 前后两次 `git status --porcelain=v1 -b` 输出逐字节一致（5 条目）；`git diff --check` clean |

## 6. 测试与验证结果

环境：WSL Ubuntu / Python 3.12.3；pytest 8.4.2、ruff 0.16.4、mypy 1.20.2（与 t1/t2 记录同口径）；`uav_gpr` editable 可导入。

| 命令（t2 声称 → 复审复现） | 声称 | 复审实测 | 退出码 |
|---|---|---|---|
| `python3 -m pytest tests/contract/test_librevna_stream.py -q` | 61 passed / 0.50s | **61 passed / 0.47s**（collect-only 亦 61） | 0 |
| `python3 -m pytest tests/contract/test_librevna_transport.py tests/unit/test_reference_manifest.py tests/contract/test_acquisition_backend.py -q` | 91 passed / 1.58s | **91 passed / 1.38s** | 0 |
| `python3 -m pytest tests/unit/test_no_external_access.py -q` | 3 passed（AST 守卫） | **3 passed / 0.55s** | 0 |
| `python3 tools/quality/verify.py` | 853 passed, 1 deselected / 132.24s + ruff + mypy(41 files) + import，VERIFY_EXIT=0 | **853 passed, 1 deselected / 133.74s（独立重跑 2m13.7s）；[quality] all gates passed** | 0 |
| `python3 -m ruff check src tests` | All checks passed! | **All checks passed!** | 0 |
| `python3 -m mypy src` | Success: 41 source files | **Success: no issues found in 41 source files** | 0 |
| `git diff --check` | clean | **clean** | 0 |

红灯证据：`stream.py` 为新增未跟踪文件，基线（2c3941d）不存在该模块，t2 日志「ModuleNotFoundError 红灯」与仓库事实自洽；中间轮次数字（26 failed→2 failed→全绿）属过程声明，**无法独立验证（未发现反证）**。

**补查变异探针（t2 未覆盖的反例，审查要求至少一项）**：7 个探针在系统临时目录复制树（`/mnt/d/tmp/issue020_probe` + WSL `/tmp/issue020_probe`，含 src 完整副本，`PYTHONPATH` 指向副本）内运行，**7 passed / 0.30s**，事后目录已删除、项目内零残留（`git status` 与探针前一致）：

1. **P1 帧上限边界**：4080B（12+9×452）最大合法 datapoint 载荷（整包恰 4088B）→ 解析 452 接收机、S11 plan 有效、2 点 sweep 正常组装；4081B 载荷（包 4089≤上限）→ malformed_datapoints+1 且不产出；4089B 载荷（12+9×453，包 4097>4096）→ 帧层拒收后重同步成功。
2. **P2 超时残尾**：超时后旧道 2/3/4 点到达 → 不重复计数（timeouts=incomplete=dropped=1）；随后新道正常组装。
3. **P3 坏 CRC 控制包插道**：坏 CRC ACK 以 1 字节粒度插在 point 0/1 之间 → sweep 照常组装、ignored_packets=0（帧层整体丢弃）。
4. **P4 无效 point 0**：空接收机 point 0 不起道、不产生 drop（invalid_points=1、dropped=0），后续完整道正常。
5. **P5 desc 0x11**：ref+port1 位按参考语义判为 reference——单独无效、配 0x01 有效。
6. **P6 等频完成**：频率相等（非严格递增）在完成时抛 `LibreVnaSweepError`、不输出。
7. **P7 属性循环**：200 轮 × 40 点随机序列（含无效点），恒满足「输出只有 0 或完整 4 点、恒严格递增、恒 0..3 有序、timeouts≤incomplete≤dropped」。

## 7. 报告与事实差异

- t2 声称「工作树仅 inScope 4 路径」：实测工作树共 5 条目——4 个 t2 inScope 路径 + t1 基线单（t1 交付物，t2 计划第 6 节已声明不计入 t2 inScope）。**一致**。
- t2 声称「changedPaths 与 inScope 逐一相等」：与工作树实测的 4 个 t2 路径一致。**一致**。
- 测试数字（61/91/853+1 deselected/ruff/mypy 41/import/diff-check）：全部独立复现。**一致**。
- P3-1：计划 §4 与 t1 基线单把「重复接收机拒绝」表述为参考既有语义，实测参考实为静默采用末值（详见第 3 节）。行为无风险，属**文档措辞差异**。
- t1 基线单引用的计划文件名与 t2 实际落盘文件名不同（P3-5）：t1 单自述为快照，M04 已链向真实文件。**非断裂差异**。
- 过程声明（红灯、中间修复轮次）无法事后复现：标为「无法独立验证（未发现反证）」，不视为差异。

## 8. 剩余风险

1. 真机行为未验证（本 Issue 设计如此）：payload 布局/desc 语义来自冻结协议文档与参考黄金向量，首轮真机对拍在 ISSUE-023 硬件基准门禁完成前，golden 向量仍是唯一权威对拍。
2. NACK 等控制包在流层仅计数（ignored_packets），参考后端在 acquire 中 fail-closed（`LibreVnaNackError`）；该路由是 ISSUE-021 backend 职责，接线时须补 NACK 中断测试（stream.py docstring 已显式声明该边界）。
3. VNA_DATAPOINT 跳 CRC 为参考既有协议行为：损坏 CRC 但结构完好的 datapoint 会被接受进 sweep（`test_bad_crc_mid_sweep_still_assembles_reference_behavior` 固化）。结构校验/点序校验/频率校验兜底，风险已文档化且不"修复"。
4. 帧层噪声丢弃 O(n²) 时间（P3-4）：内存有界、验收满足；大批量噪声场景的 CPU 表现待 ISSUE-023 基准评估。
5. 组装器输出为中间 sweep（不含 S11/S22 数值与 backend metadata）：ISSUE-021 承担比值计算、轴/配置校验与 trace 分配，接口（`feed_datapoint → AssembledSweep | None`、`check_timeout` 抛错、stats）已为其留好消费面。

## 9. 合并建议

**建议合并**（自动化授权流程）：无 P0/P1/P2，10/10 验收 PASS，门禁与探针全绿。

建议随合并提交的路径（与 ISSUE-019 先例一致，含 t1/t3 报告件）：

1. `docs/issues/M04_LIBREVNA.md`（ISSUE-020 状态行）
2. `src/uav_gpr/acquisition/librevna/stream.py`
3. `tests/contract/test_librevna_stream.py`
4. `docs/plans/2026-09-02-issue-020-librevna-stream.md`
5. `docs/reports/ISSUE_020_BASELINE_CONFIRMATION.md`（t1 交付物）
6. `docs/reports/ISSUE_020_REVIEW_REPORT.md`（本报告）

## 10. 最小修复清单

**无阻止合并的必做项。** 可选（P3，建议随 ISSUE-021 顺带关闭）：

1. P3-1：计划 §4/stream.py docstring/基线单措辞修正为「UAV 加强项（参考实现重复时静默采用末值，UAV 按 Issue 要求拒绝重复）」——纯文档。
2. P3-2：`ReceiverSlot` 构造校验 `stage ∈ 0..7`、`mask ≠ 0` + 2 个测试。
3. P3-3：确认 `LibreVnaSweepTimeoutError` 是否改继承 `LibreVnaSweepError`（catch 兼容），或在 ISSUE-021 计划显式记录捕获两者。
4. P3-4：帧层噪声丢弃索引指针硬化（可选，ISSUE-023 前评估）。
5. P3-5：合并时把 t1 基线单中的计划旧文件名改为实际文件名。

> 审查结束。审查者全程只读（仅新增本报告文件），未修改实现/测试/计划/M04/Git 状态；等待项目负责人决定合并。
