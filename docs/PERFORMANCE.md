# 性能与稳定性

版本：0.1
状态：基准方法已决定，数值需按目标硬件验证

## 1. 原则

性能优化不能牺牲数据完整性、错误可见性或处理 provenance。平均速度不是唯一指标；所有关键链路记录 p50/p95/p99、最大值、吞吐、内存和错误数。

## 2. 关键预算链

单道空中关键路径：

```text
LibreVNA sweep
  + model/validation
  + GNSS match
  + air HDF5 append/flush
  + raw hash/outbox commit
```

网络发送和地面处理不得阻塞下一道采集。最小允许间隔必须大于关键路径 p99 加安全余量。

地面关键路径分开测量：接收/校验、写盘/ACK、实时处理、B-scan 更新、地图更新。只有写盘/ACK 是接收可靠性的同步门槛，显示可以丢中间刷新但不能丢数据。

## 3. 基准矩阵

### 采集

- 频率范围 × 点数 × IFBW × S11/双通道；
- 持久连接、暂停/恢复、设备重连；
- sweep/s、USB 错误、缺点和 CPU。

### 存储

- trace shape × dtype × chunk/compression × flush 策略；
- 本地 SSD、目标空中磁盘和低余量；
- append、hash、finalize、恢复和随机补传读取。

### 处理

- trace/channel/frequency/time 规模；
- 各 stage 单独和完整流水线；
- 实时单道、有界窗口和任务后全量。

### 传输

- 正常带宽、限速、延迟、抖动、丢连接和重连；
- ACK window、outbox backlog、补传和控制消息延迟。

### UI/地图

- 1k、10k、100k trace；
- 1440×900、1920×1080、不同 DPI；
- 双 B-scan + 地图、缩放、光标、跟随和参数 revision。

## 4. 初始化阶段性能门槛

- UI 主线程不执行设备 I/O、网络 payload 解码、HDF5、全任务处理或哈希。
- 队列长度和内存上限可配置并监控。
- 图元在刷新间复用，不反复创建 colorbar/axes。
- 历史数组不在每帧被全量 `column_stack` 或复制。
- 10 万道视图的 UI 资源有界；原始全量数据可以在文件中，不要求全部常驻内存。
- 8 小时合成任务结束时没有未解释的数据缺失、死线程或持续线性内存增长。

这些是架构门槛；具体 FPS、最小间隔和磁盘余量在硬件报告中冻结。

## 5. 观测指标

空中端至少发布：sweep rate、最近/p95 sweep 时长、write/flush/hash 时长、磁盘余量、outbox pending、重试、GNSS 年龄、线程/任务状态。

地面端至少发布：接收速率、验证/写入时长、ACK 延迟、缺失/重复/冲突、处理 backlog、display revision、UI update 时长和地图点规模。

指标写结构化日志并可导出诊断报告；禁止为显示指标在主线程扫描完整历史。

## 6. 回归策略

- 基准输入和环境信息固定并记录 commit、Python/依赖、CPU、磁盘和配置。
- 普通 CI 运行小规模 smoke benchmark，只检查明显数量级退化。
- 目标硬件运行完整矩阵，报告进入 `docs/reports/`。
- 优化变更同时提交数值一致性测试和前后基准；不能只用主观“更顺滑”。
