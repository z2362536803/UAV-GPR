# 采集设计

版本：0.1
状态：目标契约，尚未实现

## 1. 采集原则

- 生产环境只有一条 LibreVNA USB 采集路径。
- 真实后端、模拟后端和文件回放实现同一接口。
- 设备 I/O、sweep 组装和调度不在 UI 主线程。
- 只有完整、校验通过且通道齐全的 sweep 才能进入存储。
- 任务开始后配置冻结；不允许静默改变频率轴、点数或通道。
- 采集成功的定义首先是空中端本地持久化成功，而不是地面端已经显示。

## 2. 后端接口

计划的 `AcquisitionBackend` 生命周期：

```text
open -> configure(frozen config) -> acquire sweeps -> pause/resume -> stop -> close
```

接口需要提供：

- 设备身份、固件/协议能力和支持通道；
- 配置校验与实际生效配置回读；
- 完整 `FrequencySweep` 输出；
- 可取消的阻塞等待；
- 可分类错误和设备重连代数；
- 幂等、安全的 stop/close。

`SimulatedBackend` 能产生确定性多通道复数数据、GNSS/无 GNSS 场景和可注入错误。`FileReplayBackend` 原样保留文件元数据，不为缺失字段伪造当前时间或位置。

## 3. LibreVNA 迁移策略

从 `E:\钢筋仪软件开发` 迁移并适配：

- USB bulk 传输与帧解析；
- 严格粘包/拆包和 CRC 校验；
- sweep 设置与回读；
- VNADatapoint 组装；
- S11 以及同 sweep 的 S11/S22 解析；
- 持久 USB 会话、暂停/恢复和错误关闭；
- 生产后端接口与测试夹具。

不得从旧 UAV-GPR 迁移 legacy/continuous 双路径。迁移后必须重新验证目标 LibreVNA 固件、频率范围、点数、IFBW、USB 超时和吞吐，不直接继承历史数值。

## 4. 配置与回读

任务请求与设备实际配置必须分别记录：

- `requested_config`：地面端冻结配置；
- `applied_config`：空中端硬件回读/确认配置；
- `config_diff`：设备量化或拒绝原因。

版本契约与摘要：

- `MissionConfig` 携带 `software_version`、`protocol_version`、`config_schema_version`；当前支持的 config schema 与 protocol 版本由 `SUPPORTED_CONFIG_SCHEMA_VERSIONS`/`SUPPORTED_PROTOCOL_VERSIONS` 常量定义，未知版本在构造/反序列化时 fail-closed（`unsupported_schema_version`/`unsupported_protocol_version`）。`protocol_version` 只是任务配置携带的兼容性契约；air/ground 传输协议本身仍未实现。
- 配置摘要：规范化 JSON（键排序、紧凑分隔符、列表保序）的 SHA256；浮点字段统一规范化（signed zero → `0.0`；NaN/Inf 拒绝）。摘要覆盖任务契约字段；`created_utc` 与 `note` 是描述性字段，随配置保存但不进入摘要，因此摘要相等不代表描述性字段一致。
- `config_diff`（`ConfigDiff`）：只包含契约字段、字段唯一、按契约字段规范排序、每个条目必须是实际变化；反序列化校验完整载荷（缺失字段、畸形 JSON 值、`changed` 与实际比较结果的矛盾一律拒绝）；值与嵌套结构深拷贝隔离，外部无法回改。

频率轴以设备实际输出/确认值为准。若实际轴与任务契约超出允许差异，任务在第一道前拒绝；不得采到一半才改变 axis。

## 5. sweep 完整性

严格组装器至少验证：

- 帧 CRC、消息长度和协议类型；
- sweep 序号/边界；
- 频点索引范围、重复和缺失；
- 每个通道的频点数和顺序；
- reference 接收机分母有效性；
- 非有限值和异常设备状态。

超时或缺点的 sweep 不能用零填充后冒充完整道。可以记录失败统计，但 `trace_index` 只在完整 sweep 被任务接受时分配；具体策略需契约测试固定。

## 6. 物理时窗与显示时窗

均匀频率步进 `Δf` 决定无模糊时间周期，近似：

```text
physical_unambiguous_window_s = 1 / Δf
```

带宽主要影响时间分辨能力；FFT 补零只改善显示采样，不创造新的物理分辨率。

系统必须区分：

- **物理时窗**：由频率轴/步进推导，修改它通常意味着修改频点数或步进并重新配置硬件。
- **显示时窗**：在物理时窗范围内对时域结果裁剪，可在地面端实时显示或回放中调整，不修改 `frequency_raw`。

UI 对话框要同时展示推导后的 `Δf`、物理时窗、带宽和预计采集开销，禁止只提供一个含糊的“时窗”输入框。

## 7. 采集间隔调度

目标间隔由地面端配置，空中端执行：

- 使用单调时钟和绝对 deadline，避免每轮 `sleep(interval)` 累积漂移；
- 调度间隔按 sweep 开始时刻或明确的固定基准定义；
- 若一次 sweep 已超过目标间隔，下一道立即或按策略开始，并记录 overrun；
- 不并发驱动同一 LibreVNA 获取多个 sweep；
- 暂停期间不累计“补采债务”，恢复时建立新的调度锚点；
- 每道保存目标间隔、实际间隔和 schedule error。

允许的最小间隔必须来自“采集 + 空中写盘 + 哈希 + 安全余量”的实测，不只使用 USB 平均吞吐。

## 8. 空中端采集流水线

```text
schedule deadline
  -> acquire complete sweep
  -> construct immutable model
  -> match GNSS at midpoint
  -> append+flush air rcscan
  -> canonical raw hash/outbox commit
  -> publish bounded status/display notification
```

磁盘写入失败、文件契约冲突或无法确认完整 sweep 时 fail-closed。网络发送失败只产生积压和告警，不自动停止采集，除非预先配置且明确显示磁盘容量保护策略。

## 9. 暂停、停止与故障

- `pause`：停止发起新 sweep，等待当前 sweep 处理到安全边界并 flush；任务和文件保持打开。
- `resume`：重新检查设备/磁盘，增加必要连接代数，从新调度锚点继续。
- `stop`：不再发起新 sweep，drain 已完整 sweep，finalize 为 `stopped_by_user`。
- `failure stop`：尽量 flush 并 finalize/保留 partial，终态含结构化错误。
- `emergency stop`：优先停止硬件 I/O，但仍尽可能保存已完成 sweep；不得承诺未完成 sweep。

所有操作必须幂等，重复远程命令返回已有结果。

## 10. 采集验收

- 合成数据证明数组形状、通道顺序和时间戳正确。
- 缺点、重复点、错序、CRC 错和超时被拒绝。
- 暂停/恢复不重复 `trace_index`，不制造巨大调度误差补偿。
- 设备重连后 `connection_generation` 增加且配置重新确认。
- 磁盘失败时停止任务，网络失败时本地数据继续完整保存。
- 真机基准覆盖目标频段、点数、IFBW、S11 和计划的双通道配置。
