# M08：轻量空中端（ISSUE-044～046）

空中端只执行冻结任务、可靠保存和回传。所有分析型 UI 和大部分业务操作留在地面端。

## ISSUE-044：空中端任务运行器与 store-then-forward 编排

- 状态：Planned
- 直接依赖：ISSUE-010、017、023、026、040～043
- 映射：FR-002～010、ADR-0001、0004

### 目标

实现空中 mission 状态机，把远程命令、采集、GNSS 匹配、air writer、raw hash、outbox 和发送按可靠顺序组合。

### 范围

- PREPARING→READY→RUNNING/PAUSED→FINALIZING→终态。
- start 前校验 config digest、device/capability、磁盘和新 mission ID。
- 每道：完整 sweep→GNSS match→air append/flush→hash/outbox→发送通知。
- pause/resume/stop/failure、网络断开继续、manifest/finalize。

### 排除项

- 空中端不做 OSL/处理/B-scan/地图；不实现 UI。

### 验收标准

- outbox 永不早于 air checkpoint；地面显示失败不影响本地事实。
- 网络失败继续采集，磁盘失败安全停止。
- 重复命令/重启不复用 mission ID 或 trace index，不 ACK 冲突。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-044。确认 ISSUE-010/017/023/026/040～043 完成；阅读 AGENTS.md、docs/issues/README.md、docs/ARCHITECTURE.md、docs/ACQUISITION.md、ADR-0001/0004。

在 application/air 中实现集中 AirMissionRunner：验证冻结 MissionConfig/device/capability/disk，新任务生成唯一 mission/file ID；编排 controller 和 GNSS matcher。每个完整 sweep 必须严格按 air writer append+flush/checkpoint -> canonical hash/persistent outbox -> sender notification；支持 pause/resume/stop/failure/finalize/manifest。断网只积压继续采集，磁盘/契约失败安全停止；命令幂等。

禁止在空中端做 OSL/处理/B-scan/地图或 UI。用 simulator/fake disk/fake link 故障注入验证每个边界、重启、重复命令和无数据倒挂。运行全量门禁，报告并停止，不 commit/push。
```

## ISSUE-045：空中端配置、无头服务、日志与磁盘保护

- 状态：Planned
- 直接依赖：ISSUE-044
- 映射：FR-017、部署门禁

### 目标

提供默认不连真机的严格 TOML 配置、无头运行入口、结构化状态/日志和磁盘低水位/停止保护。

### 范围

- air config loader、环境覆盖白名单、路径解析、秘密脱敏和配置摘要。
- headless service 生命周期/信号关闭、健康状态和诊断快照。
- JSON structured logging、rotation、敏感 GNSS/密钥最小化。
- 磁盘可写/预计容量/低水位告警/停止阈值；不会自动删数据。

### 排除项

- 不做 Windows 打包/服务安装（ISSUE-058），不做 UI。

### 验收标准

- example config 不自动连接硬件；坏配置 fail-fast。
- 断网、磁盘告警/停止和 shutdown 日志可审计。
- 日志/诊断不泄露 token、完整轨迹或 raw。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-045。确认 ISSUE-044 完成；阅读 AGENTS.md、docs/issues/README.md、config/air.example.toml、docs/DEPLOYMENT_HM30.md、docs/PERFORMANCE.md。

实现严格 AirConfig loader（TOML、明确环境覆盖白名单、规范路径、脱敏摘要）、无头 air entry/service 生命周期、结构化轮转日志与只读诊断快照，以及磁盘可写/容量预测/低水位告警/安全停止阈值。示例配置必须保持 backend=disabled，不能默认连真机；磁盘保护不得自动删 rcscan/outbox。

不要打包 Windows 服务或做 UI。测试坏配置、秘密脱敏、信号关闭、日志轮转、低水位到停止、断网状态和路径边界。运行门禁并报告，停止，不 commit/push。
```

## ISSUE-046：最小化空中端状态 UI

- 状态：Planned
- 直接依赖：ISSUE-044、045
- 映射：FR-017、ADR-0001

### 目标

实现只消费 air status snapshot 的小型 PySide6 状态页，不包含任何数据分析和任务设计功能。

### 范围

- 链路/心跳、VNA、GNSS、mission/trace、文件/磁盘、outbox、最后错误。
- 安全停止、打开诊断位置、最小托盘/窗口行为。
- 状态颜色+文字，UI 不直接访问设备/文件/socket。
- 无头模式仍完全可运行。

### 排除项

- 禁止 B-scan、A-scan、地图、校准、处理、扫频设置和本地开始任务。

### 验收标准

- UI 只调用 application command/status 接口，关闭无遗留线程。
- 1280×720 内紧凑可用；状态更新有界且不阻塞。
- 被禁止控件/依赖不存在。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-046。确认 ISSUE-044/045 完成；阅读 AGENTS.md、docs/issues/README.md、docs/UI.md 第 8 节、ADR-0001。

实现最小 PySide6 air status UI，只订阅不可变 snapshot，显示 link/heartbeat、LibreVNA、GNSS、mission/trace、local file/disk、outbox pending、last error，并提供安全停止和打开诊断位置。所有动作经 AirMissionRunner/service 接口；UI 不直接访问 USB/serial/socket/HDF5。无头入口继续独立可用。

严禁 B-scan/A-scan/地图/校准/处理/扫频设置/本地开始任务。用 pytest-qt 验证状态映射、错误、关闭、线程和 1280×720；检查禁止依赖不存在。运行门禁并报告，停止，不 commit/push。
```
