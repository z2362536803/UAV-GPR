# ISSUE-019 独立复审报告

日期：2026-09-01
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-019-librevna-transport`（审查器 reviewer，任务 t3，attempt 10c6469c-b640-4c89-bcee-30f117435da6）
审查依据：docs/ISSUE_REVIEW_STANDARD.md v1.0、AGENTS.md、docs/INDEX.md、docs/issues/README.md、docs/issues/M04_LIBREVNA.md（ISSUE-019 条目）、docs/REFERENCE_MIGRATION.md、docs/ACQUISITION.md 第 3 节、docs/adr/0005-reference-authority.md、docs/reference-baselines/manifest.md、docs/reports/ISSUE_019_BASELINE_CONFIRMATION.md（t1）、docs/plans/2026-08-30-issue-019-librevna-transport.md（t2）
性质：只读复审；未修改任何实现/测试/计划/M04/Git 状态；未 commit/push/merge；唯一新增文件为本报告。

## 1. 审查结论

**PASS**

ISSUE-019（迁移 LibreVNA USB 传输层）的 t2 交付真实、完整、合规：全部验收标准逐项 PASS，t2 声称的全部测试命令与数字独立复现，迁移清单哈希与 ISSUE-001 manifest 及本地只读副本 11/11 逐一相等，黄金帧与参考对拍成立，无 P0/P1/P2 问题。仅存在 4 个 P3 级观察项（参考保真决策与文档命名微差），均不阻止合并。可进入人工验收。

## 2. 自动识别的审查范围

从 t2 完成报告、Git 与文档自动识别（非执行者声明）：

| 项 | 值 |
|---|---|
| Issue | ISSUE-019：迁移 LibreVNA USB 传输层（M04_LIBREVNA.md L5–40） |
| 直接依赖 | ISSUE-001（manifest 基线，13 测试）、ISSUE-015（backend 契约，28 测试）——均已合入 main |
| 目标分支 / 基线 | `main` @ `c0cd067`；`main...origin/main = 0/0`；工作树无新提交（HEAD 仍 `c0cd067`，reflog 顶层未变） |
| 改动文件（t2 inScope，实测 1 M + 3 ??） | `M docs/issues/M04_LIBREVNA.md`（仅 ISSUE-019 状态行 `Planned → Review`）；`?? src/uav_gpr/acquisition/librevna/transport.py`（501 行）；`?? tests/contract/test_librevna_transport.py`（668 行，50 测试）；`?? docs/plans/2026-08-30-issue-019-librevna-transport.md`（199 行，迁移清单 §4 + 设计决策 D1–D8 + 执行日志 §10） |
| t1 交付（不计 t2 inScope） | `?? docs/reports/ISSUE_019_BASELINE_CONFIRMATION.md`（193 行） |
| t3 交付（本报告） | `docs/reports/ISSUE_019_REVIEW_REPORT.md`（本次审查唯一新增文件） |
| commit/push/merge/分支 | 无（未创建分支、未提交、未推送；工作树以未提交改动承载全部交付，符合「默认不 commit」协议，待人工验收后授权合并） |
| 范围外文件 | 未发现任何范围外修改/新增 |

## 3. 主要问题（P0 → P3）

无 P0、P1、P2 问题。

### P3-1 `encode_packet` 不强制帧长上限（参考保真，非缺陷）
- 文件：`src/uav_gpr/acquisition/librevna/transport.py:172-179`
- 触发条件：payload ≥ 4088 字节时产出 >4096 字节帧，其自身 `PacketStream` 会静默丢弃（独立探针实测：4097 长度帧被拒并重对齐，无泄漏）。
- 实际影响：主机侧控制包均为小帧（SweepSettings/ACK 等），当前无触发路径；参考实现行为逐字一致（迁移保真要求）。
- 建议：ISSUE-020/021 如需发送大 payload，在调用侧加长度校验（不必改本模块）。

### P3-2 错误 context 合并顺序允许 `reason=` 关键字覆盖判别符
- 文件：`src/uav_gpr/acquisition/librevna/transport.py:94-99`
- 触发条件：调用方显式传 `reason=` 时，`{"reason": self._reason, **context}` 中 `**context` 会覆盖字面键，导致 `context["reason"]` 与 `.reason` property 不一致。
- 实际影响：当前无任何调用点传入 `reason=`；该模式与 `acquisition/backend.py` `BackendError`（L95–110）完全同构，属仓库既有先例。
- 建议：可选——未来统一在 core 层固化合并顺序（不属本 Issue 范围）。

### P3-3 计划 §7 测试矩阵有 5 处测试名与实现名轻微不一致
- 文件：`docs/plans/2026-08-30-issue-019-librevna-transport.md:136-138`
- 差异：`test_claim_failure_disposes_and_busy`→`test_claim_error_is_busy_and_disposes`、`test_find_failure_busy`→`test_find_error_is_busy`、`test_cancel_before_open_safe`→`test_cancel_before_open_is_safe`、`test_cancel_after_close_safe`→`test_cancel_after_close_is_safe`、`test_close_after_cancel_releases`→`test_close_after_cancel_releases_resources`。
- 实际影响：意图映射无歧义，50 个测试全部存在且通过；纯文档精度问题。

### P3-4 PacketStream 噪声丢弃逐字节 `del buffer[0]`，最坏 O(n²)
- 文件：`src/uav_gpr/acquisition/librevna/transport.py:205-206`
- 触发条件：超长噪声前缀（无 0x5A 的大量字节）。
- 实际影响：参考实现逐字一致；帧长上限 4096 限制单帧内存，ISSUE-020 承接有界缓存硬化。当前传输层不直接消费无界流。
- 建议：ISSUE-020 实现时若需高频丢噪声，可改索引指针。

## 4. 逐 Issue 验收矩阵（ISSUE-019）

| # | 验收标准（M04 L26–30 + 提示词派生条款） | 状态 | 精确代码/测试证据 | 实际测试证据 |
|---|---|---|---|---|
| 1 | 黄金字节帧与参考对拍 | PASS | `transport.py:159-179`（crc32/encode_packet）；`tests/contract/test_librevna_transport.py:59-102`（ACK `5a080007c1f48315`、REQ_DEV_INFO `5a08000ff37c581b`、SET_IDLE `5a0800141fb53d91`、`crc32(b"123456789")=0xCBF43926`） | 50 passed；审查独立用 zlib 重算三帧与参考测试文件（rebar `tests/test_librevna_protocol.py` L45–47）原位一致 |
| 2 | 拆包/CRC/长度/timeout 测试无需真机 | PASS | `transport.py:182-221`（PacketStream 8..4096、噪声丢弃、CRC 校验、VNA_DATAPOINT 跳 CRC）；`tests/contract:104-177`（11 个 framing 用例）；`tests/contract:488-631`（fake-usb 注入） | 50 passed；独立探针补查：嵌入 0x5A payload 单次解析、CRC 中字节损坏丢弃、4097 长度拒绝并重对齐、截断大包缓冲、lone 0x5A、空 feed 全部 PASS |
| 3 | USB 失败各阶段都 release 资源，不吞异常 | PASS | `transport.py:312-337`（claim 失败先 dispose 再抛 Busy）、`transport.py:344-372`（close 先清状态；release 失败仍 dispose 且抛 ReleaseError）；`tests/contract:526-536, 618-630`（dispose/release 断言） | 50 passed；独立探针：USBTimeoutError 子类按序映射 Timeout（非 Disconnected）、非 USBError 异常原样传播（参考行为）、close 后无句柄残留 |
| 4 | 普通测试不枚举真机 | PASS | 测试文件无 `usb/serial/socket/requests` 导入（AST 扫描实测）；`transport.py:401-412` 惰性加载；`tests/contract:638-657` 子进程断言导入不加载 usb；`tests/unit/test_no_external_access.py` AST+路径守卫（全量通过） | 792 passed 全量含守卫；独立探针：导入后 `sys.modules` 无 `usb`/`libusb_package` |
| 5 | 迁移清单精确记录源文件/哈希（I001 manifest 口径） | PASS | `docs/plans/.../2026-08-30-issue-019-librevna-transport.md` §4（REFERENCE_MIGRATION.md §5 模板 15 字段齐全：branch `feat/issue-16-pause-resume`、HEAD `938875234a99b47d78cfec940671005b63e9d15c`、dirty=True、11 源 SHA-256、采用/排除行为、黄金夹具来源路径+读取时点哈希） | 审查独立复算：manifest.json ↔ 计划文档 ↔ 本地只读副本 `D:\博士任务\rebar-inspector` 11/11 SHA-256 逐一相等；参考测试夹具哈希 `f3019795…`/`6ee3f7a6…` 与执行日志一致 |
| 6 | 范围实现：VID/PID、endpoint 发现、claim/release、bulk read/write、frame/CRC、严格长度、timeout/cancel、幂等关闭、结构化错误 | PASS | `transport.py:54-73`（VID 0x1209/PID 0x4121/EP_OUT 0x01/EP_IN 0x81/HEADER 0x5A）；`transport.py:83-151`（8 个 DomainError 子类，reason 判别符）；`transport.py:229-251`（UsbAdapter Protocol）；`transport.py:254-442`（PyUsbAdapter）；`transport.py:450-501`（会话门面 cancel/幂等） | 与参考 `librevna_usb_transport.py`（240 行）逐行为对照：open/claim/read/write/close 语义一致；UAV 适配增量（结构化错误对齐 core、标志式 cancel、adapter 注入）在设计决策 D1–D8 有记录 |
| 7 | 排除项：不组装 VNADatapoint/sweep、不实现 backend、不从 UAV-GPR 复制代码 | PASS | `transport.py` 无任何 datapoint 解析/SweepSettings 编码/S11 计算；仅 import `uav_gpr.core.errors`（依赖方向 acquisition→core 合规）；无 UAV-GPR 路径引用 | grep 实测：无 `datapoint_to_s11/parse_/SweepSettings/backend` 符号；`tests/contract` 无参考仓库路径字符串（守卫通过） |
| 8 | 依赖方向与依赖声明 | PASS | `transport.py` 仅标准库 + `uav_gpr.core.errors`；pyusb 已存在于 `pyproject.toml` 主依赖（L27 `"pyusb>=1.2.1,<2"`，ISSUE-001 前已有） | 无新增依赖；mypy 40 文件通过 |
| 9 | M04 状态行更新 | PASS | `git diff docs/issues/M04_LIBREVNA.md` 实测仅 ISSUE-019 状态行 `Planned → Review`（附基线单/计划文档链接），其余条目未动 | diff 逐行核对 |
| 10 | 测试无固定 sleep、覆盖正常/错误/取消/恢复 | PASS | 测试文件 50 个用例、0 个 `time.sleep`（grep 实测）；正常链路/幂等/各失败阶段/取消/资源释放全覆盖 | grep 无 `sleep` |

**结论：10/10 PASS，0 FAIL / 0 PARTIAL / 0 BLOCKED / 0 NOT APPLICABLE。**

## 5. Git 与交付检查

- 当前分支 `main`，HEAD `c0cd067`（与 t1 基线一致）；`main...origin/main = 0/0`。
- reflog 顶层仍为 `c0cd067 commit`，无 reset/rebase/amend/强推迹象；无新提交。
- 工作树：`M docs/issues/M04_LIBREVNA.md` + `?? transport.py / 测试 / 计划文档`（t2 inScope 4 路径逐一相等）+ `?? ISSUE_019_BASELINE_CONFIRMATION.md`（t1 交付）+ 本报告（t3 交付）。无缓存/日志/构建物/实测数据/密钥/参考仓库文件混入（`git ls-files --others --exclude-standard | wc -l = 4`）。
- 无跨 Issue 混改：M04 diff 仅状态行；未触碰 ISSUE-020 条目、core/、backend.py、参考仓库。
- 一个提交/批次内无多 Issue 混杂（尚无提交，待人工验收后授权合并）。
- 审查前后工作树一致（唯一差异为本报告文件，属审查产出）。

## 6. 测试与验证结果（全部独立复跑）

环境：WSL Ubuntu / Python 3.12.3；numpy 2.5.2、pytest 8.4.2、ruff 0.16.4、mypy 1.20.2；`uav_gpr` editable（src/）。

| 命令 | 实测结果 | 退出码 | t2 声称 | 一致性 |
|---|---|---|---|---|
| `python3 -m pytest tests/contract/test_librevna_transport.py -q` | 50 passed in 0.49s | 0 | 50 passed in 0.51s | 一致 |
| `python3 -m pytest tests/unit/test_reference_manifest.py tests/contract/test_acquisition_backend.py -q` | 41 passed in 0.96s（001:13 + 015:28） | 0 | 41 passed in 0.93s | 一致 |
| `python3 tools/quality/verify.py` | 792 passed, 1 deselected in 133.58s（742 基线 + 50 新）；All checks passed!；Success: no issues found in 40 source files；package import ok；VERIFY_EXIT=0 | 0 | 792 passed, 1 deselected in 129.20s | 一致（仅耗时差） |
| `python3 -m ruff check src tests` | All checks passed! | 0 | All checks passed! | 一致 |
| `python3 -m mypy src` | Success: no issues found in 40 source files | 0 | Success: 40 source files | 一致 |
| `git diff --check` | clean | 0 | clean | 一致 |
| 独立变异探针（系统临时目录 `/tmp/issue019_review/`，已清理，项目零残留） | 26 项检查：25 直接 PASS；1 项为探针自身期望错误（把 payload 中字节损坏当作 CRC 损坏），修正期望后 PASS | 0 | t2 未覆盖 | 补查完成 |

补查反例清单（t2 未覆盖或仅部分覆盖）：
1. 黄金帧用 zlib 独立重算（不依赖被测模块 crc32）——3 帧全对。
2. 长度边界：=8 接受、=7 拒绝、=4096 接受、=4097 拒绝并重对齐至下一包（缓冲零残留）。
3. payload 内嵌 `0x5A` 字节不影响按长度切包。
4. CRC 中字节（非末字节）损坏丢弃；VNA_DATAPOINT 跳 CRC 时 payload 中字节损坏仍解析（携带突变 payload，参考行为）。
5. 截断的 4096 最大包跨 feed 缓冲、完成时单次输出。
6. `USBTimeoutError ⊂ USBError` 时按序映射 Timeout（非 Disconnected）；非 USBError 的 read 异常原样传播（参考保真）。
7. 导入 transport 模块后 `sys.modules` 无 `usb`/`libusb_package`（不枚举 USB）。
8. cancel 生命周期全链：开前 NotOpen、开后清标志、写被拦、关后安全 no-op。
9. 迁移清单 11 哈希与 manifest.json/本地副本三方对拍（独立 sha256 复算）。

## 7. 报告与事实差异

- 无实质差异。t2 完成报告的测试数字、门禁数字、文件清单、changedPaths 与 inScope 逐一相等，均实测复现；耗时差异（0.49s vs 0.51s、133.58s vs 129.20s）为计时波动。
- 计划文档 §7 测试矩阵存在 5 处测试名微差（见 P3-3），意图无歧义、测试全部存在。
- t2「红灯→绿灯」过程声明（实现前 ModuleNotFoundError 收集错误、第 1 轮 49 passed/1 failed 为测试自身字节序笔误）无法事后独立复现原始中间态，但最终代码与测试一致且绿灯数字复现；按 §10 处理为「未发现反证」。

## 8. 剩余风险

1. **无真机验证**（预期内）：VID/PID/endpoint/固件兼容性以参考冻结契约 + 黄金帧为准；真机行为验证归 ISSUE-023 硬件基准，本 Issue 不伪造真机结果。
2. **VNA_DATAPOINT 跳 CRC 为参考既有协议行为**，无设备证据不得「修复」；ISSUE-020 组装器必须显式承接该语义（计划 D5 已记录）。
3. **取消为标志式**：阻塞中 read 的抢占依赖真机断开语义，属 ISSUE-023 范围（计划 D4 已记录）。
4. **`encode_packet` 无调用侧长度上限**与 **PacketStream 噪声丢弃 O(n²) 最坏情况**（P3-1/P3-4）：当前主机控制包为小帧、无触发路径；参考同源。
5. 本 Issue 为纯传输层：不落盘、不联网、无数据规则/空地职责变更，无需新增 ADR。

## 9. 合并建议

- 建议项目负责人人工验收后授权一次性提交：`docs/issues/M04_LIBREVNA.md`（状态行）、`src/uav_gpr/acquisition/librevna/transport.py`、`tests/contract/test_librevna_transport.py`、`docs/plans/2026-08-30-issue-019-librevna-transport.md`、`docs/reports/ISSUE_019_BASELINE_CONFIRMATION.md`、`docs/reports/ISSUE_019_REVIEW_REPORT.md`（4 交付 + 2 报告）。
- P3 项均不阻塞合并，无需合并前修复。
- 合并后由项目负责人将 M04 状态行 `Review → Done`（状态定义 docs/issues/README.md §2：Done 需人工审查完成）。
- 不进入 ISSUE-020：待项目负责人明确授权。

## 10. 最小修复清单

**强制项：无。**

可选 P3 建议（不阻塞）：
1. ISSUE-020/021 发送大 payload 前在调用侧校验长度（对应 P3-1）。
2. 未来统一 core 层错误 context 合并顺序（对应 P3-2，仓库级先例，不属本 Issue）。
3. 计划 §7 测试名与实现名对齐（对应 P3-3，文档精度）。
4. ISSUE-020 硬化 PacketStream 时评估噪声丢弃索引指针优化（对应 P3-4）。
