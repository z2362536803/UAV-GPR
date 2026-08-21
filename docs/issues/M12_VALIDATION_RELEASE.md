# M12：系统验收与首个现场发布（ISSUE-059～060）

这两个 Issue 是发布门禁，不是普通功能开发。任何未解决的数据冲突、假 ACK、磁盘安全或硬件干扰问题都会阻止 RC1。

## ISSUE-059：端到端故障注入与 8 小时模拟耐久

- 状态：Planned
- 直接依赖：ISSUE-046、052、055～058
- 映射：FR-001～019、发布门禁

### 目标

用两进程模拟器完成完整任务和系统性故障矩阵，并实际运行 8 小时耐久，输出可复核数据一致性/资源报告。

### 范围

- ground/air loopback/HM30-like fault proxy、确定性 trace/GNSS。
- USB timeout/半道、GNSS 断开、air/ground 磁盘失败、TCP 各字节阶段断开、ACK 丢失、慢 ingest/processing/UI、进程重启。
- start/pause/resume/stop/completed/failed、补传/冲突/partial recovery。
- 8 小时：内存/线程/队列/磁盘/哈希/缺道/重复和 UI 响应报告。

### 排除项

- 不连接真机、不用缩短测试冒充 8 小时、不因失败自动改验收阈值。

### 验收标准

- 已完整 sweep 不丢，地面 ACK 均有持久证据，空地 raw 对拍。
- 网络断开继续 air 存储；磁盘失败安全停止。
- 无未解释线性内存增长/死线程/无界积压；报告包含 commit/config/原始指标位置。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-059。确认 ISSUE-046/052/055～058 完成；阅读 AGENTS.md、docs/issues/README.md、docs/TESTING.md、docs/PERFORMANCE.md、docs/ARCHITECTURE.md。

建立两进程端到端 fault harness，使用确定性 simulated LibreVNA/GNSS 和可控链路/文件故障。自动覆盖 start/pause/resume/stop/四类终态、USB timeout/半道、GNSS 断开、air/ground 磁盘失败、TCP 任意阶段断开、ACK 丢失、重复/乱序/冲突、慢处理/UI、air/ground 重启、partial recovery 和补传。随后实际运行连续 8 小时耐久，记录 commit/config、内存/线程/队列/磁盘/延迟/哈希/缺道及诊断原始位置。

不得连接真机、缩短后声称 8 小时或放宽阈值。若环境无法持续 8 小时，Issue 保持 Blocked 并提供可继续的命令/中间证据，不声称 Done。运行全部门禁，报告并停止，不 commit/push。
```

## ISSUE-060：真机/现场验收、操作手册与 RC1 门禁

- 状态：Planned
- 直接依赖：ISSUE-023、026、052、055、058、059
- 映射：全部首版需求与发布门禁

### 目标

在目标 LibreVNA、GNSS、HM30、空中电脑和地面笔记本上完成真机/现场矩阵、四组电磁干扰实验、操作/恢复手册和 RC1 决策。

### 范围

- 冻结硬件/固件/手册/供电/IP/安装，复核旧 HM30 资料。
- 真实 S11/S11+S22、目标频点/IFBW/间隔、GNSS 匹配和双电脑 HM30 回传/重连/补传。
- HM30 A关/B开空闲/C正常传/D高负载四组 GPR/GNSS 干扰与温升/供电证据。
- UI 分辨率/DPI/现场流程、空地双副本对拍、掉电恢复。
- 操作手册、安装/启停/应急/取数/恢复、已知限制、版本化 RC1 验收报告。

### 排除项

- 不伪造设备结果、不把厂商标称当实测、不带未通过数据红线发布。

### 验收标准

- 所有发布门禁有签名/日期/配置/原始证据；未通过项明确阻止 RC1。
- 操作手册按发布构建逐步走查成功。
- 未解决数据丢失、hash 冲突误 ACK、磁盘安全或显著电磁干扰为零。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-060。确认 ISSUE-023/026/052/055/058/059 完成；阅读 AGENTS.md、docs/issues/README.md、docs/PRODUCT_SPECIFICATIONS.md、docs/TESTING.md、docs/DEPLOYMENT_HM30.md、docs/PERFORMANCE.md。

这是人工+真机发布门禁。先冻结目标 LibreVNA/GNSS/HM30/两台电脑/固件/手册/供电/IP/安装和软件 commit。按 approved checklist 运行真实 S11/S11+S22、频点/IFBW/间隔、GNSS midpoint、双电脑 HM30 断线重连/补传、空地逐道对拍、掉电恢复、UI 1280/1440/1920+DPI。完成 HM30 A关闭、B开启空闲、C正常 trace、D高负载四组 GPR/GNSS/温升/供电干扰实验。用发布构建逐步走查并完成安装、启停、应急、取数、恢复操作手册、已知限制和 RC1 报告。

绝不把厂商标称或旧报告冒充本次实测。没有明确授权/硬件/安全条件就停止并标 Blocked；任何数据丢失、冲突误 ACK、磁盘安全或显著未处置干扰都阻止 RC1。不要擅自飞行、改网络/供电或发布。最终报告证据与阻塞项后停止，不 commit/push，除非调用者明确授权。
```
