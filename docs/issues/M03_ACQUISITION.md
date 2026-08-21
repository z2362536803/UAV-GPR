# M03：通用采集、模拟器与回放（ISSUE-015～018）

本里程碑建立与具体 LibreVNA 协议无关的采集骨架。所有真机能力必须先能由模拟器和回放驱动应用测试。

## ISSUE-015：AcquisitionBackend 契约与确定性模拟器

- 状态：Planned
- 直接依赖：ISSUE-004～006
- 映射：FR-003、018

### 目标

定义统一 backend 生命周期/能力/错误契约，并提供可重复的多通道模拟 sweep 与故障注入。

### 范围

- open/configure/acquire/cancel/close、capabilities、requested/applied config。
- 确定性模拟 S11/S22 数据、真实 shape/axis/UTC+monotonic metadata。
- 注入 timeout、半道、配置拒绝、断开和延迟；可取消阻塞等待。
- 资源所有权和幂等 close。

### 排除项

- 不实现调度循环、Qt、HDF5、GNSS reader 或 LibreVNA USB。

### 验收标准

- 相同 seed/config/虚拟 clock 产生相同 raw；错误按计划在确定道触发。
- 单/双通道共用接口，非法生命周期被结构化拒绝。
- cancel/close 不遗留线程或等待。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-015。确认 ISSUE-004～006 完成；阅读 AGENTS.md、docs/issues/README.md、docs/ACQUISITION.md、docs/DATA_MODEL.md、docs/TESTING.md。

定义 AcquisitionBackend/Capabilities/AppliedConfig 契约和严格生命周期，并实现确定性 SimulatedBackend：按 seed/config/可注入 Clock 生成多通道 FrequencySweep，支持 timeout、半道、配置拒绝、设备断开、延迟和可取消等待。close/cancel 必须幂等；错误使用 core 结构化错误。不要实现 scheduler/controller、Qt、HDF5、GNSS 串口或 LibreVNA。

先写生命周期、确定性、单/双通道、requested/applied diff、故障点、cancel/close 资源测试。运行门禁并报告，停止，不 commit/push。
```

## ISSUE-016：单调时钟采集间隔调度器

- 状态：Planned
- 直接依赖：ISSUE-006、015
- 映射：FR-004、005

### 目标

使用绝对单调 deadline 调度 sweep，准确记录目标/实际间隔、误差、overrun 与暂停恢复锚点。

### 范围

- 可注入 clock/waiter 的无硬件 scheduler。
- 绝对 deadline、无累计漂移、单 sweep 串行。
- overrun、取消、暂停和恢复重新锚定；不补偿暂停期间“欠债”。
- 调度观测值传给 metadata 构建，不伪造墙钟。

### 排除项

- 不启动线程、不调用 HDF5/网络，不决定硬件最小间隔。

### 验收标准

- 虚拟时间下长期 deadline 无漂移；耗时超过间隔有明确 overrun。
- 取消即时生效，暂停恢复没有 burst。
- 系统 UTC 跳变不影响调度。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-016。确认 ISSUE-006/015 完成；阅读 AGENTS.md、docs/issues/README.md、docs/ACQUISITION.md 第 7 节和 docs/PERFORMANCE.md。

实现纯逻辑 MonotonicAcquisitionScheduler，使用可注入 monotonic Clock/Waiter 和绝对 deadline，输出目标间隔、实际间隔、schedule error、overrun。暂停/恢复建立新锚点，不追赶暂停期间次数；cancel 可中断等待；UTC 变化不得影响。不要创建业务线程、调用 backend/HDF5/网络，也不要硬编码最小间隔。

用虚拟时间测试数万周期无累计漂移、采集耗时小于/大于间隔、首道、暂停恢复、取消和墙钟跳变；禁止 sleep-based 测试。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-017：采集控制器与暂停/停止状态机

- 状态：Planned
- 直接依赖：ISSUE-015、016
- 映射：FR-002、003、005、018

### 目标

实现与 UI 无关的单 backend 采集控制器，集中管理 PREPARING/RUNNING/PAUSED/STOPPING/FAILED 等转换。

### 范围

- worker 所有权、start/pause/resume/stop/emergency-stop/close。
- 完整 sweep 发布、有界 consumer 接口和背压策略。
- 当前 sweep 安全边界、幂等命令、错误分类和资源关闭顺序。
- 设备重连 hook 与 connection generation，不在此实现具体 USB 重连。

### 排除项

- 不落盘、不发送网络、不做 Qt controller、不实现 LibreVNA。

### 验收标准

- 状态转换表全覆盖，非法/重复命令结果确定。
- pause 不接受新 sweep，stop drain 已完成 sweep，close 无遗留 worker。
- 有界队列不会无限增长，消费慢有明确策略/指标。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-017。确认 ISSUE-015/016 完成；阅读 AGENTS.md、docs/issues/README.md、docs/ACQUISITION.md 第 8/9 节和 docs/ARCHITECTURE.md 的状态/并发边界。

实现无 Qt 的 AcquisitionController 和集中状态机，唯一拥有 backend worker，编排 configure/scheduler/acquire，提供 start/pause/resume/stop/emergency-stop/close、完整 sweep 有界发布、背压指标和 connection_generation hook。pause 在安全边界停止新 sweep；stop drain 已完成 sweep；重复命令幂等；错误转结构化 FAILED 并按顺序释放资源。

不要写 HDF5、网络或 LibreVNA 实现。用 SimulatedBackend/事件/barrier 覆盖全部状态边、慢 consumer、错误、取消和无残留线程；不用固定 sleep。运行门禁并报告，停止，不 commit/push。
```

## ISSUE-018：`.rcscan` 文件回放后端

- 状态：Planned
- 直接依赖：ISSUE-011、015、017
- 映射：FR-016、018

### 目标

让严格 reader 通过同一 `AcquisitionBackend` 接口回放 raw，原样保留已有元数据和缺失信息。

### 范围

- 回放 air/ground v2 和经 adapter 的 v1 raw。
- 顺序/原始时间比例/加速/逐道模式，使用可取消等待。
- 原样输出 trace identity/UTC/GNSS；文件缺失即保持缺失。
- 不重复应用文件已有校准/处理。

### 排除项

- 不实现处理 revision、UI 播放条或文件迁移。

### 验收标准

- 回放 raw 与 reader 数值/axis/channel/metadata 对拍。
- pause/resume/stop 与 controller 配合，无伪当前时间/位置。
- 损坏/无 raw 文件明确拒绝。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-018。确认 ISSUE-011/015/017 完成；阅读 AGENTS.md、docs/issues/README.md、docs/ACQUISITION.md、docs/DATA_FORMAT.md 和 docs/PROCESSING.md 的安全回放规则。

实现 FileReplayBackend，基于严格 RcScanReader 按逻辑 trace 顺序输出原始 FrequencySweep，支持逐道、原始时间比例和显式加速，等待可取消并可由 AcquisitionController 暂停/恢复/停止。必须原样保留 mission/trace ID、UTC/GNSS/缺失字段，不用当前时间或 0 坐标补齐；不得自动应用已保存 calibrated/time 结果或重复处理。

测试 v2 air/ground、v1 adapter、乱序物理记录、无 GNSS、加速/取消、损坏/无 raw 和数值对拍。不要做 UI 或迁移。运行门禁，报告并停止，不 commit/push。
```
