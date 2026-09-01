# ISSUE-019 实施计划：迁移 LibreVNA USB 传输层

日期：2026-09-01
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-019-librevna-transport`（执行器 engineer，任务 t2，attempt 604e047c-9d08-432c-9b57-c9c995abb109）
基线：`main` @ `c0cd067`（工作树干净、origin/main 同步 0/0）；权威基线件：[docs/reports/ISSUE_019_BASELINE_CONFIRMATION.md](../reports/ISSUE_019_BASELINE_CONFIRMATION.md)（t1）
配套：本计划为 t2 执行契约与 t3 复审依据；迁移清单（第 4 节）按 REFERENCE_MIGRATION.md §5 模板；执行日志随执行过程追加（第 10 节）。

## 1. 目标与用户价值

迁移并隔离钢筋仪项目的 LibreVNA USB bulk 传输层（VID/PID、endpoint 发现、claim/release、bulk read/write、协议 frame/CRC、严格长度上限、timeout/cancel、可取消 I/O、幂等关闭、结构化错误映射），形成**不含 sweep 业务**的传输层模块 `src/uav_gpr/acquisition/librevna/transport.py`；通过 USB adapter 依赖注入实现无硬件黄金帧测试，默认测试不枚举 USB。价值：为 ISSUE-020（包流与严格 sweep 组装器）与 ISSUE-021（S11 生产采集后端）提供审计迁移、可无硬件验证的传输基础（M04 门禁「单一真机路径、严格组装和硬件基准完成」的第一步），并满足 AGENTS.md「硬件代码必须有模拟器或协议夹具测试」与「只从钢筋仪项目迁移经过审计的 LibreVNA 分层」。

## 2. 范围（M04 L16–20 + 提示词）

1. `src/uav_gpr/acquisition/librevna/transport.py`（**单一新模块**，承载四层内容）：
   - **协议常量**：VID `0x1209`/PID `0x4121`、EP_OUT `0x01`/EP_IN `0x81`、`HEADER=0x5A`、packet 类型（SWEEP_SETTINGS=2/DEVICE_INFO=5/ACK=7/NACK=10/REQUEST_DEVICE_INFO=15/SET_IDLE=20/DEVICE_STATUS=25/VNA_DATAPOINT=27）。
   - **帧编解码**：`crc32`（IEEE 802.3，`binascii.crc32 & 0xFFFFFFFF`）、`encode_packet(packet_type, payload)`（HEADER + 长度(2) + type(1) + payload + CRC32(4)，长度含整包）、`Packet` frozen dataclass、`PacketStream.feed(data) -> list[Packet]`（丢弃 HEADER 前噪声；长度越界 `<8` 或 `>4096` 丢弃当前字节重对齐；半包等待；**非 VNA_DATAPOINT 校验 CRC，VNA_DATAPOINT(type 27) 跳过 CRC——参考实现既有协议行为，不"修复"**）。
   - **结构化错误**：`LibreVnaTransportError(DomainError)` 家族——照搬 backend.py 既有模式（`ErrorCode.INVALID_ARGUMENT` + 类级 `_reason` + `reason` property + 类型化子类，core `ErrorCode` 枚举只读不扩展）：`LibreVnaMissingDependencyError`(missing_dependency)、`LibreVnaDeviceNotFoundError`(device_not_found)、`LibreVnaBusyError`(busy)、`LibreVnaTimeoutError`(timeout)、`LibreVnaDisconnectedError`(disconnected)、`LibreVnaReleaseError`(release_failed)、`LibreVnaCancelledError`(cancelled)、`LibreVnaNotOpenError`(not_open)。
   - **adapter 分层**：`UsbAdapter` Protocol（`is_open`/`open`/`read(max_length, timeout_ms)`/`write(data)`/`close`，错误即传输层结构化错误语义，失败阶段资源释放是 adapter 职责）；`PyUsbAdapter`（真实实现：**惰性加载 pyusb**，构造/导入不加载 `usb`/`libusb_package`；`find` 失败→Busy、未找到→DeviceNotFound；`set_configuration` Windows 失败仅 warn 继续；内核驱动 detach 失败忽略；claim 失败先 `dispose_resources` 再抛 Busy；read：USBTimeoutError→Timeout、USBError→Disconnected、未开→NotOpen；write：任何异常→Disconnected；close 幂等：先清状态，release 失败仍 dispose 且抛 ReleaseError）；`LibreVnaUsbTransport`（会话门面：持有 adapter + cancel 标志，`open` 幂等（已开 no-op，成功时清除 cancel 标志）、`read`/`write`（未开→NotOpen、已 cancel→Cancelled、否则转 adapter）、`cancel()`（置标志，未开/已关安全 no-op）、`close()` 幂等（未开 no-op，转 adapter.close））。
2. 无硬件黄金帧测试：黄金固定字节向量（ACK `5a080007c1f48315`、REQ_DEV_INFO `5a08000ff37c581b`、SET_IDLE `5a0800141fb53d91`、`crc32(b"123456789")=0xCBF43926`、`crc32(b"")=0`）与 framing 行为对拍（分块 feed、噪声前缀、多包一次 read、跨 read 半包、缓冲跨 read 保留、非法长度重对齐、CRC 错丢弃、VNA_DATAPOINT 跳 CRC）。
3. 测试分层（tests/contract/test_librevna_transport.py，单一新测试文件）：
   - transport 层：FakeAdapter 注入（实现 `UsbAdapter` Protocol），覆盖会话/取消/幂等/错误传播/资源释放断言；
   - adapter 层：fake-usb 注入（替换模块内 `importlib`，参考测试范式），覆盖 find/claim/read/write/release 各失败阶段映射与资源释放；
   - 惰性加载：子进程断言导入 transport 模块不加载 `usb`/`libusb_package`（默认测试不枚举 USB；AST 守卫 `tests/unit/test_no_external_access.py` 同步生效）。

## 3. 明确排除项（M04 L22–24 + 提示词 + 任务契约）

- 不组装 VNADatapoint/sweep、不实现 backend（ISSUE-020/021）；不解析 VNADatapoint/SweepSettings/DeviceInfo、不算 S11；
- 不读取/迁移 UAV-GPR 旧采集代码（含其 `librevna/`、`reference_code/`，ADR-0005/REFERENCE_MIGRATION.md §4 禁止）；
- 不改 `core/**`（含 errors.py 的 ErrorCode 枚举——只读消费 `DomainError`）、不改 `acquisition/backend.py`（只读消费 `BackendError` 模式）、不改 `acquisition/librevna/__init__.py`；
- 不改两个参考项目（本地副本只读）；不 commit/push/merge、不创建/切换分支；不进入 ISSUE-020；
- 不在 `src/uav_gpr/acquisition/librevna/transport.py`、`tests/contract/test_librevna_transport.py`、`docs/plans/2026-08-30-issue-019-librevna-transport.md`、`docs/issues/M04_LIBREVNA.md` 之外新增任何文件（确需拆分先停止向 captain 报告）。

## 4. 关联需求/ADR/文档与参考源哈希（迁移清单，REFERENCE_MIGRATION.md §5 模板）

```text
target issue/task:        ISSUE-019 迁移 LibreVNA USB 传输层（M04，FR-003、ADR-0005）
reference repository:     钢筋仪软件开发（E:\钢筋仪软件开发；本机不可达，WSL 仅挂载 C/D）
                          + 本地只读副本 D:\博士任务\rebar-inspector（GitHub 克隆
                          z2362536803/rebar-inspector，来源记录见 t1 基线单 §3.3）
reference branch + HEAD:  manifest 冻结：feat/issue-16-pause-resume @
                          938875234a99b47d78cfec940671005b63e9d15c（ISSUE-001 冻结时点）
                          本地副本：main @ 7c522d2aebe6a835acb969e8012565715f64a238
reference worktree status:manifest 记录 worktree_dirty=True（未跟踪项与本次迁移无关）；
                          本地副本 src/rebar_inspector/acquisition/*.py 显示 M，但
                          git diff --ignore-all-space 零差异（仅 CRLF vs LF 行尾），
                          内容哈希与 manifest 冻结值逐字节一致（t1 实测 11/11）
source file(s) + SHA256: 11 个 librevna 候选源（manifest.json tracked_status=committed）：
  __init__.py            838cbdc857d6e9f73b4dfb5ed461b7ba541768643a0e0c53e872734e7c31bcf7
  acquired.py            44bf8c6adc76cfe0326048bf300942a67d8fdb49e7d0026bc5c78ed01a309626
  aggregation.py         c8b64176f461f75a72809f0d072c09a31c752a3ede49a5d81543bfbf026126d1
  backend.py             f05da35cdee84604d43945da8c30854a289fb7de36a90a3c46c110cf8ab3340f
  errors.py              c3dfbfcaf4a6a5aea38f8ad79c4ecbbf546e69be2c7051dcf89ea1883aac2502
  file_replay.py         96e4b1f57b5e400b29b91ea1820fc6883ec264a9be05c994b18a6ffa77cd29be
  librevna_protocol.py   6a41c4b11cfa8272c1687893a53219ea0465987a378700a0b74ab3973353d8ce
  librevna_usb.py        a73adc1c41f186ee51ab938c4c245809bf36dea0bc2769ad1115fffa99b22d87
  librevna_usb_transport.py 7a2a1f87f81567d8955aa414e801b10a4fdb8e5bba79a7e9048e6b471095bb18
  simulated.py           73749aa8a2435d193b8068dc9a3771f5021312a11589da19648cfedcb83a5af9
  sweep_config.py        9877b7619747c07aeb7657ba3667322c2687396040bb00193afd5d8508c44801
  （t1 实测 sha256sum 与 manifest 逐一相等）
  实际阅读并采用：librevna_usb_transport.py、librevna_protocol.py（仅帧/CRC 部分）、errors.py（错误层级参考）
trusted behavior/contract（采用）:
  - VID/PID/endpoint 常量与设备查找（find 失败→Busy、未找到→DeviceNotFound）；
  - open 幂等；set_configuration Windows 失败 warn-only 继续；内核驱动 detach 失败忽略；
    claim 失败先 dispose_resources 再抛 Busy；
  - read 错误映射（USBTimeoutError→Timeout、USBError→Disconnected）；write 任何异常→Disconnected；
  - close 幂等：先清状态再释放；release 失败仍 dispose 且抛 ReleaseError；
  - pyusb/libusb 惰性加载，缺依赖抛带安装提示的结构化错误；
  - 帧编解码：HEADER 0x5A、crc32 IEEE、encode_packet 布局、PacketStream 粘包拆包
    （长度 8..4096、噪声丢弃、非法长度重对齐、VNA_DATAPOINT 跳过 CRC）；
  - 黄金字节向量与 framing 行为（来源见下）。
excluded behavior（排除）:
  - VNADatapoint 解析与 S11 计算（datapoint_to_s11/parse_s11_point/parse_vna_datapoint，ISSUE-020）；
  - SweepSettings 编码/校验与 stages_bitmap（ISSUE-021）；
  - DeviceInfo 解码（ISSUE-021 设备能力）；
  - LibreVnaUsbBackend/连续采集/暂停恢复会话（ISSUE-021/023）；
  - acquired/aggregation/file_replay/simulated/backend/sweep_config 模块整体；
  - errors 中 Nack/Protocol/UnsupportedConfig/Sweep 错误类（sweep/backend 层，ISSUE-020/021 用）；
  - UAV-GPR 全部采集代码（含 legacy/continuous 双路径）。
new target module(s):     src/uav_gpr/acquisition/librevna/transport.py（唯一新模块）
UAV-specific adaptations:
  - 结构化错误照搬 backend.py 既有先例：DomainError + ErrorCode.INVALID_ARGUMENT +
    类级 reason + 类型化子类（core ErrorCode 枚举只读，不扩展）；消息 ASCII；
  - 可取消 I/O：LibreVnaUsbTransport.cancel() 置标志，其后 read/write 立即抛
    LibreVnaCancelledError；open() 清除标志；cancel 未开/已关安全 no-op；
    close() 不受 cancel 影响且幂等——确定、可测、不遗留句柄；
  - adapter 依赖注入分层（UsbAdapter Protocol + PyUsbAdapter + 会话门面），
    无硬件测试注入 FakeAdapter；adapter 层用 fake-usb 替换 importlib（参考范式）；
  - 默认测试不枚举 USB：惰性加载 + 子进程断言 + AST 守卫（ISSUE-002）。
tests/golden fixtures migrated:
  - 黄金字节向量：ACK_PACKET_HEX=5a080007c1f48315、REQ_DEV_INFO_HEX=5a08000ff37c581b、
    SET_IDLE_HEX=5a0800141fb53d91、crc32(b"123456789")=0xCBF43926、crc32(b"")=0；
  - framing 行为用例：分块 feed、噪声前缀、多包一次 read、跨 read 半包、缓冲保留、
    非法长度重对齐、CRC 错丢弃、VNA_DATAPOINT 跳 CRC；
  - 来源（只读，不在 manifest 白名单——tests/** 排除，provenance 记录于此）：
    D:\博士任务\rebar-inspector\tests\test_librevna_protocol.py（455 行，
    固定向量定义于 L43–66；读取时点 SHA-256 见执行日志）与
    D:\博士任务\rebar-inspector\tests\test_librevna_usb_transport.py（291 行，
    fake-usb 注入范式 L43–143）；两者内容哈希在执行日志记录后归档于 t1 基线单 §3.4。
new tests added:          tests/contract/test_librevna_transport.py（新契约测试，失败测试优先）
numeric or performance comparison: 不适用——无真机、无性能声明（参考项目历史速度数字
                          不得写成新结果，AGENTS.md/ISSUE-023 口径）
license/provenance review:参考项目为内部 proprietary；本迁移为契约提取与适配（行为级），
                          新实现为独立代码（非逐行复制），docstring 声明来源与既有协议行为
                          （含 VNA_DATAPOINT 跳 CRC 的不"修复"说明）；未复制大模块。
```

## 5. 设计决策（ADR 级，含备选与理由）

| # | 决策 | 理由 | 备选（否决理由） |
|---|---|---|---|
| D1 | 常量+帧编解码+错误+adapter 全部放入 `transport.py` 单一新模块 | inScope 唯一模块约束（t2 契约）；ISSUE-019 范围明确含 frame/CRC；参考的 protocol/transport 两文件分层在传输层合并，sweep 相关解析不进入本模块 | 拆分 protocol.py 单独文件（超出 inScope，需 captain 批准；frame 属传输层范围） |
| D2 | 错误家族照搬 backend.py 既有模式：`LibreVnaTransportError(DomainError)` + `ErrorCode.INVALID_ARGUMENT` + 类级 `_reason` + `reason` property + 类型化子类 | core `ErrorCode` 枚举只读（修改 core/errors.py 越界）；backend.py L95–156 已确立该模式（`BackendError`），transport 与 backend 同构便于 ISSUE-021 直接对接；`to_dict/from_dict` 序列化免费获得 | 自建非 DomainError 错误基类（与仓库模式不一致，复审风险）；扩展 ErrorCode 枚举（修改 core，越界） |
| D3 | adapter 分层：`UsbAdapter` Protocol（is_open/open/read/write/close）→ `PyUsbAdapter`（真实 pyusb，惰性加载）→ `LibreVnaUsbTransport`（会话门面：幂等/cancel/错误门控） | 「依赖注入 USB adapter 以便无硬件测试」的直接落地：transport 测试注入 FakeAdapter，adapter 测试注入 fake-usb；单一错误层（结构化错误贯穿），无双重错误包装 | 参考式单类（transport 直接调 pyusb + importlib 注入）：错误映射与资源释放混在会话类，无法独立测 adapter 资源释放 |
| D4 | 取消语义：`cancel()` 置标志；`read/write` 检查标志立即抛 `LibreVnaCancelledError`；`open()` 清除标志；未开/已关时 cancel 为安全 no-op；close 不受 cancel 影响 | ISSUE-019 要求 timeout/cancel 可取消 I/O 且不遗留句柄：标志式取消确定、可测、无线程；阻塞中 read 的抢占属真机断开语义（ISSUE-023） | 线程级中断 read（引入线程与竞态，超出传输层职责）；adapter 级 cancel 回调（过度设计，ISSUE-023 再议） |
| D5 | 帧行为逐字对齐参考：长度上限 8..4096、噪声丢弃、非法长度逐字节重对齐、CRC 错丢弃、**VNA_DATAPOINT 跳过 CRC** | 参考实现既有协议行为（librevna_protocol.py L14–16 明文：没有设备证据不得自行"修复"）；黄金向量对拍可证 | "修复" VNA_DATAPOINT CRC 校验（无真机证据，违反参考迁移规则） |
| D6 | 黄金向量直接采用参考固定字节（来源记录于第 4 节） | 对拍要求「黄金字节帧与参考对拍」；参考测试固定向量已由参考 venv 计算并硬编码 | 自造向量（无法对拍参考） |
| D7 | 资源释放语义：open 幂等（已开 no-op）；claim 失败先 dispose 再抛 Busy；close 幂等（未开 no-op；先清状态；release 失败仍 dispose 且抛 ReleaseError） | 「USB 失败各阶段都 release 资源，不吞异常」验收：每个失败阶段都有 dispose/release 断言；参考行为逐条保留 | 失败时跳过 dispose（泄漏句柄）；release 失败吞掉（违反不吞异常） |
| D8 | 测试两层：transport 层 FakeAdapter（会话/取消/幂等/错误传播/资源断言）+ adapter 层 fake-usb 替换 importlib（参考范式）+ 子进程惰性加载断言 | 「普通测试不枚举真机」：测试文件不 import `usb` 根（AST 守卫强制）；fake-usb 范式已由参考验证 | 用真 pyusb mock 库（新增依赖）；把 usb 根写进测试（触发 AST 守卫失败） |

## 6. 文件改动（inScope 精确路径，changedPaths 必须与此逐一相等）

| 路径 | 内容 |
|---|---|
| `src/uav_gpr/acquisition/librevna/transport.py` | 新模块：协议常量、`crc32`/`encode_packet`/`Packet`/`PacketStream`、`LibreVnaTransportError` 家族、`UsbAdapter` Protocol、`PyUsbAdapter`、`LibreVnaUsbTransport` |
| `tests/contract/test_librevna_transport.py` | 新契约测试（失败测试优先；黄金向量/帧行为/transport 会话/cancel/adapter 失败映射/资源释放/惰性加载） |
| `docs/plans/2026-08-30-issue-019-librevna-transport.md` | 本计划文档（t2 先落盘；执行日志第 10 节随执行追加） |
| `docs/issues/M04_LIBREVNA.md` | 仅 ISSUE-019 状态行：`Planned → In progress → Review`（勿动其它条目） |

## 7. 测试矩阵（提示词必测项 → 测试名）

| 必测项 | 测试 | 手段 |
|---|---|---|
| 黄金字节帧对拍 | `test_encode_packet_ack_fixed_bytes`、`test_encode_packet_req_dev_info_fixed_bytes`、`test_encode_packet_set_idle_fixed_bytes`、`test_crc32_known_vector`、`test_crc32_empty`、`test_packet_length_field`、`test_crc_covers_body` | 固定十六进制向量（第 4 节）直接断言 |
| 拆包（PacketStream） | `test_single_packet`、`test_noise_byte_prefix`、`test_multiple_packets_one_read`、`test_split_across_reads`、`test_buffer_persists_across_reads`、`test_invalid_length_drops_byte`、`test_length_upper_bound_resync`、`test_length_lower_bound_resync`、`test_crc_error_drops_non_datapoint`、`test_vna_datapoint_crc_is_skipped`、`test_reset_clears_buffer` | 参考 framing 行为逐字对齐 |
| transport 会话（FakeAdapter 注入） | `test_open_read_write_close_roundtrip`、`test_open_is_idempotent`、`test_close_is_idempotent`、`test_read_before_open_rejected`、`test_write_before_open_rejected` | FakeAdapter 记录 open/read/write/close/release/dispose 调用并断言 |
| 各失败阶段资源释放 | `test_claim_failure_disposes_and_busy`、`test_find_failure_busy`、`test_device_not_found`、`test_read_timeout_maps`、`test_read_disconnect_maps`、`test_write_disconnect_maps`、`test_release_error_reported_and_state_cleared`、`test_missing_dependency_friendly_error` | adapter 层 fake-usb 注入（替换模块内 importlib）；每个失败断言 dispose/release 被调用、状态清空、异常为结构化类型 |
| timeout/cancel | `test_cancel_makes_read_raise_cancelled`、`test_cancel_makes_write_raise_cancelled`、`test_cancel_before_open_safe`、`test_cancel_after_close_safe`、`test_open_clears_cancel`、`test_close_after_cancel_releases` | cancel 标志语义；close 后无句柄残留（FakeAdapter.closed 断言） |
| 惰性加载/不枚举 USB | `test_import_does_not_load_usb` | 子进程导入 transport 模块并断言 `sys.modules` 无 `usb`/`libusb_package`（参考范式）；AST 守卫（ISSUE-002）同步生效 |
| 回归 | 依赖定向 41 passed（ISSUE-001：13 + ISSUE-015：28）不被破坏；全量 verify.py（基线 742 passed/1 deselected） | — |

## 8. 性能/数据风险

- 无性能声明：本 Issue 无真机基准（ISSUE-023 负责）；不把参考项目历史速度写成新结果。
- 帧缓冲有界性：`PacketStream.feed` 每轮循环至少消耗 1 字节（噪声/非法长度逐字节丢弃），单包长度上限 4096；`read(max_length)` 由调用方给定上限——无恶意长度导致无限分配路径（与 ISSUE-020 的有界缓存要求一致）。
- 无数据风险：不落盘、不联网、不迁移数据；不修改两个参考项目；默认测试不枚举 USB。
- 线程风险：本模块无线程（取消为标志式，阻塞中 read 抢占属 ISSUE-023）；不引入固定 sleep。

## 9. 完成定义与回退

- 完成定义（全部满足才可登记 completed）：验收标准 3 条（M04 L26–30）逐条 PASS——黄金字节帧与参考对拍；USB 失败各阶段 release 资源不吞异常；普通测试不枚举真机；定向测试红灯→绿灯记录于执行日志；全量 verify.py + ruff + mypy + import + `git diff --check` 全绿；`git status` 仅 4 个 inScope 路径改动；M04 状态行更新为 Review；不 commit/push/merge、不创建分支。
- 回退方式：实现为新增文件（transport.py + 测试 + 两份文档），不修改既有模块；异常时删除未登记文件即可回到 `main @ c0cd067` 干净基线；无破坏性操作。

## 10. 执行日志（随执行追加）

```text
[2026-09-01] t2 开工：claim t2（attempt 604e047c-9d08-432c-9b57-c9c995abb109）→ in_progress。
[2026-09-01] 计划文档落盘（本文件第 1–9 节）。
[2026-09-01] 迁移清单：第 4 节；黄金夹具来源读取时点 SHA-256（只读，不进交付）：
              D:\博士任务\rebar-inspector\tests\test_librevna_protocol.py
                f3019795c6906ae62479532b755ac73dd375d1452a5e4c5eaca31451a7cef5c7
              D:\博士任务\rebar-inspector\tests\test_librevna_usb_transport.py
                6ee3f7a64b8f75c4cbcefa68743cf2d4fd3d9dddb0cb5e3a9f594e7259bdb466
[2026-09-01] 失败测试优先（红灯，实现前）：
              $ python3 -m pytest tests/contract/test_librevna_transport.py -q
              -> ERROR: ModuleNotFoundError: No module named
                 'uav_gpr.acquisition.librevna.transport'（collection 1 error）——红灯成立。
[2026-09-01] 最小实现：src/uav_gpr/acquisition/librevna/transport.py 落盘
              （协议常量/帧编解码/结构化错误/UsbAdapter Protocol/PyUsbAdapter/
               LibreVnaUsbTransport 会话门面）。
[2026-09-01] 定向测试（绿灯）：第 1 轮 49 passed / 1 failed——失败为测试自身字节序笔误
              （长度字段 little-endian，`\x5a\x00\x07` 实为 0x0700=1792 合法长度），
              修正测试字节为 `\x5a\x07\x00`（length=7）后：
              $ python3 -m pytest tests/contract/test_librevna_transport.py -q
              -> 50 passed in 0.51s——绿灯成立（实现未改动）。
[2026-09-01] 依赖回归：
              $ python3 -m pytest tests/unit/test_reference_manifest.py \
                  tests/contract/test_acquisition_backend.py -q
              -> 41 passed in 0.93s（ISSUE-001：13 + ISSUE-015：28）。
[2026-09-01] 静态检查修复轮：ruff 初跑 7 错误（B028 stacklevel、RUF100 未启用 noqa×4、
              I001 导入排序、UP031 printf 风格格式化）→ 全部修复后
              `python3 -m ruff check src tests` -> All checks passed!；
              mypy 初跑 14 错误（惰性 pyusb 句柄 object 类型过严）→ 改为 Any +
              `__file__` None 守卫后 `python3 -m mypy src`
              -> Success: no issues found in 40 source files。
[2026-09-01] 门禁（全量，tools/quality/verify.py）：
              $ python3 tools/quality/verify.py
              792 passed, 1 deselected in 129.20s (0:02:09)   # 742 基线 + 50 新测试
              All checks passed!                               # ruff
              Success: no issues found in 40 source files      # mypy
              package import ok                                # import 检查
              [quality] all gates passed
              VERIFY_EXIT=0
[2026-09-01] 工作树/交付检查：`git diff --check` clean；`git status --porcelain=v1 -b`
              仅 4 个 inScope 路径（1 modified + 3 untracked，见第 6 节）+ 基线单
              （t1 交付物，不计入 t2 inScope）；无缓存/日志/实测数据残留。
[2026-09-01] M04 状态行：Planned → In progress → Review（最终态，2026-09-01）。
```

> 后续记录：本计划的执行日志只记录事实与数字；t3 复审报告独立输出。
