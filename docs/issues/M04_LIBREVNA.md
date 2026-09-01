# M04：LibreVNA 生产采集（ISSUE-019～023）

本里程碑只从钢筋仪项目迁移经过审计的 LibreVNA 分层。旧 UAV-GPR 的采集实现不在白名单内。

## ISSUE-019：迁移 LibreVNA USB 传输层

- 状态：Done（2026-09-02 独立复审 VERDICT=PASS 后经项目负责人自动化授权合并，见 [docs/reports/ISSUE_019_REVIEW_REPORT.md](../reports/ISSUE_019_REVIEW_REPORT.md)）
- 直接依赖：ISSUE-001、015
- 映射：FR-003、ADR-0005

### 目标

迁移并隔离 LibreVNA USB bulk 打开/关闭、帧编解码、CRC 和可取消 I/O，形成不含 sweep 业务的传输层。

### 范围

- 按 I001 manifest 精确记录钢筋仪源文件/哈希和迁移清单。
- VID/PID、endpoint 发现、claim/release、bulk read/write、协议 frame/CRC。
- 依赖注入 USB adapter 以便无硬件测试；错误映射和幂等关闭。
- 严格长度上限、timeout/cancel 和资源清理。

### 排除项

- 不组装 VNADatapoint/sweep，不实现 backend，不从 UAV-GPR 复制代码。

### 验收标准

- 黄金字节帧与参考对拍；拆包/CRC/长度/timeout 测试无需真机。
- USB 失败各阶段都 release 资源，不吞异常。
- 普通测试不枚举真机。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-019。确认 ISSUE-001/015 完成；阅读 AGENTS.md、docs/issues/README.md、docs/REFERENCE_MIGRATION.md、docs/ACQUISITION.md 第 3 节和 ADR-0005。

依据 reference baseline 只从 E:\钢筋仪软件开发 审计迁移 LibreVNA USB transport：设备/endpoint、claim/release、bulk read/write、协议 frame、CRC、严格长度、timeout/cancel 和结构化错误。先在迁移计划记录源 branch/HEAD/status/file SHA256、采用/排除行为；通过 USB adapter 注入实现无硬件黄金帧测试。不得读取/迁移 UAV-GPR 采集代码，不实现 VNADatapoint/sweep/backend。

默认测试不得枚举 USB；覆盖打开/claim/read/write/close 每个失败阶段和资源释放。运行门禁并报告，停止，不 commit/push。
```

## ISSUE-020：LibreVNA 包流与严格 sweep 组装器

- 状态：Done（2026-09-02 独立复审 VERDICT=PASS 后经项目负责人自动化授权合并，见 [docs/reports/ISSUE_020_REVIEW_REPORT.md](../reports/ISSUE_020_REVIEW_REPORT.md)；5 项 P3 建议随 ISSUE-021 顺带关闭）
- 直接依赖：ISSUE-019
- 映射：FR-003、`ACQUISITION.md` 第 5 节

### 目标

把任意边界 USB 字节流解析为协议包，并把 VNADatapoint 严格组装成完整、有序 sweep。

### 范围

- 增量 packet stream：粘包、拆包、噪声/损坏同步策略和上限。
- sweep 边界、点索引、重复/缺失/乱序、通道/receiver 字段。
- reference 分母、有限值、超时和丢弃统计。
- 只在完整一致时输出中间 assembled sweep。

### 排除项

- 不配置设备、不计算最终 S11/S22 backend metadata、不零填缺点。

### 验收标准

- 任意 byte chunking 得到同一包序列。
- 缺点/重复/跨 sweep/坏 CRC/非法分母均不产出假完整 sweep。
- 有界缓存，恶意长度不能分配无限内存。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-020。确认 ISSUE-019 完成；阅读 AGENTS.md、docs/issues/README.md、docs/ACQUISITION.md 第 5 节和 ISSUE-019 的迁移记录。

实现 LibreVNA 增量 PacketStream 与 StrictSweepAssembler。对任意 USB chunk 边界正确处理粘包/拆包，验证 frame/长度/CRC；按 sweep/point/channel 严格检测范围、重复、缺失、乱序、跨 sweep、reference 分母和非有限值，只在完整一致时输出 assembled sweep。缓存和 frame 长度必须有界，超时产生统计/结构化错误，禁止零填或部分道输出。

使用参考黄金字节和生成式 chunk 切分测试，覆盖恶意长度与恢复同步。不要配置设备或实现 backend。运行门禁并报告，停止，不 commit/push。
```

## ISSUE-021：S11 生产采集后端

- 状态：Done（2026-09-02 独立复审 VERDICT=PASS 后经项目负责人自动化授权合并，见 [docs/reports/ISSUE_021_REVIEW_REPORT.md](../reports/ISSUE_021_REVIEW_REPORT.md)；6 项 P3 观察不阻止合并，P3-2 延后记录）
- 直接依赖：ISSUE-017、020
- 映射：FR-003、004

### 目标

用唯一 USB transport/assembler 实现 `AcquisitionBackend` 的 S11 真机路径，并严格记录 requested/applied config。

### 范围

- open/capabilities/configure/start/acquire/cancel/close。
- sweep settings 发送/回读、实际频率轴、Port1/Reference 计算 S11。
- 真实 UTC+monotonic sweep 边界、device identity、connection generation。
- USB 线程边界、超时和安全停止；协议夹具模拟。

### 排除项

- 不实现 S22、校准、IFFT、HDF5、UI 或第二条 SCPI/GUI 路径。

### 验收标准

- 无硬件协议 simulator 下符合 backend 契约。
- axis/config 超差在第一道前拒绝；不完整 sweep 不分配正式 trace。
- close/cancel 无泄漏，不自动启动 LibreVNA-GUI。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-021。确认 ISSUE-017/020 完成；阅读 AGENTS.md、docs/issues/README.md、docs/ACQUISITION.md 和 LibreVNA 迁移记录。

实现唯一生产路径 LibreVnaUsbBackend 的 S11：复用 transport/assembler，完成 capability/open/configure、SweepSettings、applied config/axis 回读、Port1÷Reference 复数 S11、真实 sweep UTC+monotonic、cancel/close 和 connection_generation。第一道前严格比较 requested/applied；部分/坏 sweep 不输出 FrequencySweep。通过协议 simulator 覆盖，无硬件默认不枚举设备。

禁止增加 TCP/SCPI/LibreVNA-GUI 第二路径，不做 S22、校准、处理、HDF5 或 UI。运行契约/错误/资源测试和门禁；如需真机只运行 opt-in smoke 并如实报告。停止，不 commit/push。
```

## ISSUE-022：同 sweep S11/S22 双反射采集

- 状态：Planned
- 直接依赖：ISSUE-021
- 映射：FR-003、013

### 目标

在同一完整 sweep 中按冻结通道顺序输出 S11/S22 双反射数据，保证两个通道共享真实时刻与 trace identity。

### 范围

- Port1/Reference→S11、Port2/Reference→S22 的严格映射和 capability 检查。
- `HH:S11`、`VV:S22` 默认绑定通过 ChannelSpec 配置，不在数组字段硬编码。
- 同 sweep 点完整性和 shared metadata；单 S11 继续工作。
- 协议黄金夹具与双通道吞吐计数。

### 排除项

- 不实现 S21/S12，不连续执行两个独立 sweep 冒充同步双通道，不做校准。

### 验收标准

- 形状严格 `2 × frequency`，通道顺序来自配置。
- 任一通道缺点/坏分母则整道拒绝。
- S11-only 行为不回归。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-022。确认 ISSUE-021 完成；阅读 AGENTS.md、docs/issues/README.md、docs/ACQUISITION.md、docs/DATA_MODEL.md。

扩展同一 LibreVNA sweep 的严格双反射采集：从同一 VNADatapoint 集合计算 Port1/Reference=S11、Port2/Reference=S22，按冻结 ChannelSpec 输出 channel×frequency 并共享 sweep metadata/trace identity。默认 HH:S11/VV:S22 是配置，不得硬编码数组语义。任一通道缺点、分母异常或 capability 不支持时整道拒绝；保留 S11-only。

禁止用两个顺序 sweep 冒充同步通道，不实现 S21/S12/校准。用黄金协议夹具测试数值、顺序、部分通道失败和 S11 回归；运行门禁并报告，停止，不 commit/push。
```

## ISSUE-023：LibreVNA 重连、暂停恢复与硬件基准

- 状态：Planned
- 直接依赖：ISSUE-017、021、022
- 映射：FR-003～005、性能门禁

### 目标

完成生产 backend 的错误重连/配置重确认，并在目标真机上冻结频段×点数×IFBW×通道基准与最小安全间隔输入。

### 范围

- 设备断开/重连状态、退避、connection generation、重新 configure/回读。
- controller pause/resume/stop 与 USB in-flight 的安全协作。
- 可复现 benchmark 工具和 opt-in hardware tests。
- 报告 sweep、写前模型开销、错误率、CPU 和目标配置；不把参考项目数字当结果。

### 排除项

- 不含 HDF5/网络关键路径最终最小间隔，不做飞行验收。

### 验收标准

- 模拟断开/重连不重复 trace、不沿用未确认配置。
- 真机矩阵报告包含硬件/固件/配置/commit 和 p50/p95/p99。
- 没有指定真机时 Issue 保持 Blocked，不伪造完成。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-023。确认 ISSUE-017/021/022 完成；阅读 AGENTS.md、docs/issues/README.md、docs/ACQUISITION.md、docs/PERFORMANCE.md、docs/TESTING.md。

实现 LibreVNA 断开/重连、退避、connection_generation、重新 configure/回读，以及 pause/resume/stop 与 in-flight USB 的安全关闭。增加可重复 benchmark 与双重 opt-in hardware tests，覆盖目标频段/点数/IFBW/S11/双通道，报告 p50/p95/p99、错误、CPU、硬件/固件/commit。参考项目历史速度只能作为对照，不能写成新结果。

先用模拟 USB 完成自动测试。如果本机没有明确授权且设备 ID 匹配的 LibreVNA，绝不连接或伪造真机报告，把硬件验收标为 Blocked 并说明已完成部分；这种情况下不要声称 Issue Done。无论如何不得进入下一 Issue，不 commit/push。
```
