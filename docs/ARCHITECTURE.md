# 总体架构

版本：0.1（初始化基线）
状态：已决定的目标架构，尚未实现

## 1. 系统边界

```text
┌──────────────────────── 地面端笔记本 ────────────────────────┐
│ Ground UI -> Ground Application -> Processing/Storage         │
│       ↑            ↕                ↕                         │
│  双 B-scan + GNSS 地图   Transport endpoint    ground.rcscan  │
└─────────────────────────── HM30/IP ───────────────────────────┘
                              ↕
┌──────────────────────── 空中端电脑 ───────────────────────────┐
│ Minimal Status UI -> Air Application -> Acquisition/GNSS      │
│                            ↓              ↓                    │
│                     air.partial.rcscan <- 完整 sweep           │
│                            ↓                                  │
│                     Persistent Outbox -> Transport client      │
└───────────────────────────────────────────────────────────────┘
```

地面端对任务拥有控制权，空中端对“安全执行和本地数据可靠性”拥有最终否决权。地面端不能命令空中端在磁盘不可写、设备配置不兼容或任务标识冲突时继续采集。

## 2. 进程与部署

首版计划部署两个独立 Python 进程：

- `uav-gpr-ground`：地面端 GUI、任务控制、接收、处理、地图、地面存储和诊断。
- `uav-gpr-air`：空中端轻量状态 UI/服务、设备接入、采集、本地存储、outbox 和回传。

后续可增加只读诊断 CLI，但不能形成第二条生产采集路径。应用入口只有在实现并测试后才加入 `pyproject.toml`。

## 3. 分层与依赖

```text
apps
├── ground entrypoint ──> ui.ground ──> application.ground
└── air entrypoint ─────> ui.air_status -> application.air

application.ground/air
├── acquisition
├── calibration
├── processing
├── positioning
├── storage
├── transport
└── core

acquisition/calibration/processing/positioning/storage/transport -> core
core -> standard library + numpy
```

### `core`

不可变领域模型、枚举、ID、时间、通道、任务配置、频域/时域数组契约和错误类型。它不知道 Qt、USB、串口、socket、HDF5 或文件路径。

### `acquisition`

定义后端接口、控制器、单调调度器、模拟后端和文件回放。`acquisition/librevna` 隔离 USB 帧、协议组包、设备配置和生产后端。任何调用 `acquire_one` 的循环都不得存在于窗口类中。

### `positioning`

定义 GNSS fix、NMEA 解析、独立读取、重连、缓存和道位置匹配。该层不渲染地图。

### `calibration`

定义 OSL/双反射校准模型、兼容性校验、空采参考和无 UI 的参考采集会话。物理标准件切换由应用/UI 编排，算法不控制硬件。

### `processing`

每个阶段一个模块/对象，输入输出不可变。编排只组合阶段，不复制算法。频域和时域处理有清晰边界。

### `storage`

`.rcscan/.rcal/.rcbg` schema、增量 writer、reader、恢复、检查和迁移。writer 接受领域对象，不接受窗口控件。

### `transport`

协议消息、序列化、链路、心跳、命令、ACK、补传和 persistent outbox。消息契约与 TCP/socket 实现分离，便于故障注入与未来替换承载层。

### `application`

编排用例和状态机：创建任务、任务前检查、空中执行、地面接收、处理/保存、结束对账、回放和诊断。这里是唯一允许跨多个领域服务协调事务的层。

### `ui`

Qt 视图、presenter/controller 适配和可视化。UI 显示应用状态、发送意图，不直接访问 USB、串口、socket 或 HDF5。

## 4. 关键运行数据流

### 4.1 创建和启动任务

```text
Ground dialog
  -> validate draft config
  -> create mission_id + immutable MissionConfig
  -> send StartMission(command_id, config_digest, config)
  -> Air validates hardware/disk/identity
  -> Air creates air_file_id + .partial.rcscan
  -> Air returns Accepted/Rejected with reason
  -> acquisition starts only after Accepted
```

“命令已收到”和“任务已开始”是不同状态，UI 必须分别显示。

### 4.2 每道 store-then-forward

```text
LibreVNA complete sweep
  -> construct immutable FrequencySweep
  -> match nearest valid GNSS fix at sweep midpoint
  -> append raw + metadata to air.partial.rcscan
  -> flush/checkpoint
  -> calculate canonical raw_trace_sha256
  -> commit persistent outbox record
  -> enqueue bounded sender notification
  -> send trace envelope/payload
  -> ground validates identity/schema/hash/order
  -> append to ground.partial.rcscan and flush
  -> ground may process/display asynchronously
  -> ground sends TracePersisted ACK
  -> air marks outbox item acknowledged
```

任何一步失败都保留明确状态；不得因为显示处理失败而撤销已成功保存的原始数据。

### 4.3 任务结束

```text
Air stops acquisition
  -> drain completed sweeps to local writer
  -> finalize air manifest
  -> close and atomically rename air file
  -> send MissionManifest

Ground
  -> compare indices/uids/hashes
  -> request missing traces
  -> finalize ground file only when policy allows
  -> produce integrity report
```

用户停止、设备故障和正常完成都走结束流程，但终态不同。

## 5. 状态模型

建议的任务主状态：

```text
IDLE -> PREPARING -> READY -> RUNNING <-> PAUSED -> FINALIZING -> COMPLETED
                           \-> STOPPING -> STOPPED
                           \-> FAILING  -> FAILED
```

- 状态转换由应用层集中定义，不让按钮、网络回调和硬件线程各自维护布尔变量。
- 每个远程命令有 `command_id`，结果至少分 `received/accepted/executing/succeeded/failed`。
- 断线是链路状态，不自动等同于任务失败；空中端可能仍在 `RUNNING`。

## 6. 并发边界

计划的工作单元：

- UI 主线程：绘图和轻量状态映射。
- LibreVNA worker：设备 I/O 与 sweep 组装。
- GNSS worker：串口读取、解析和 fix 发布。
- Air storage worker：严格有序的增量写入。
- Transport worker：收发、心跳和重连。
- Ground ingest/storage worker：校验与地面写入。
- Processing worker/pool：可取消、按版本丢弃过期显示结果。

线程间只传不可变对象或拥有清晰所有权的缓冲。关闭顺序必须保证不再接受新 sweep 后再 drain、flush、关闭设备和退出线程。

## 7. 配置

- 本地配置描述设备地址、监听端口、存储根目录、日志和 UI 偏好。
- 任务配置描述会影响数据契约的扫频、通道、间隔、时窗和校准引用。
- 本地配置可以重载的字段与需要重启的字段必须区分。
- 任务配置开始后冻结，保存完整 JSON/TOML 规范化表示及摘要。
- 示例配置不得自动连接真机；现场配置不提交仓库。

## 8. 可扩展而不提前复杂化

- 协议模型与 TCP 承载分离，但首版不必同时实现 gRPC/MQTT。
- 领域模型支持多通道，但首版只实现经过验证的 S11 或 S11/S22 组合。
- 地图抽象支持离线资源和无底图，首版不承担航线规划。
- 处理历史预留新阶段，但未实现阶段不得出现“启用”假开关。

## 9. 已知架构风险

- HDF5 单 writer 约束要求严格串行写入和清晰关闭流程。
- 空地各自追加同一任务时文件内容不同，不能比较整文件哈希。
- GNSS 和 sweep 来自不同时间基准，必须同时保存 UTC 和单调时钟并定义匹配误差。
- 长时 B-scan/轨迹可能导致无界内存或全量重算，需从第一版使用追加和抽稀策略。
- 参考项目均存在大窗口/大模块倾向，新代码必须按 presenter、widget、use case 拆分。
