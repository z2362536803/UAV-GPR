# 数据模型

版本：0.1
状态：领域契约，尚未实现

## 1. 通用原则

- 领域对象默认不可变。
- NumPy 数组由对象拥有并设为只读，外部只能得到不能恢复为可写的视图。
- 所有 ID 使用规范字符串表示的 UUID；解析时严格校验。
- 所有枚举持久化为稳定的小写字符串，不依赖 Python 枚举序号。
- 所有可持久化参数必须能够无损转换为 JSON 基本类型。

## 2. 标识

| 名称 | 含义 |
|---|---|
| `mission_id` | 一次冻结采集任务的全局唯一 ID |
| `trace_index` | 任务内从 0 开始、严格单调且不复用的道序号 |
| `trace_uid` | 每一道全局唯一 ID；空地传输和 UI 联动主键 |
| `device_id` | 空中端设备身份；由部署配置分配 |
| `air_file_id` | 空中端 `.rcscan` 文件实例 ID |
| `ground_file_id` | 地面端 `.rcscan` 文件实例 ID |
| `command_id` | 远程命令幂等和追踪 ID |
| `calibration_profile_id` | `.rcal` 校准配置唯一 ID |
| `background_reference_id` | `.rcbg` 空采参考唯一 ID |

`mission_id + trace_index` 必须唯一。若重复消息的 `trace_uid` 或哈希不同，视为数据冲突。

## 3. 通道模型

`ChannelSpec` 至少包含：

- `channel_id`：文件内稳定 ID，例如 `hh_s11`、`vv_s22`；
- `logical_polarization`：如 `HH`、`VV`；
- `s_parameter`：`S11/S21/S12/S22`；
- `display_name`；
- 可选天线端口/方向备注。

数组通道顺序由 `channels` 明确给出，禁止仅根据字典遍历或窗口顺序推断。

## 4. 任务配置

`MissionConfig` 是任务开始时冻结的值对象，至少包含：

- 频率起止、频点数或显式频率轴；
- IFBW、功率和 LibreVNA 必需设置；
- 有序 `ChannelSpec` 集合；
- 计划道数或连续模式；
- 目标采集间隔；
- 物理时窗配置及其推导值；
- 默认显示时窗；
- 校准/空采引用及是否应用；
- GNSS 最大年龄和无 fix 策略；
- 软件/协议/schema 版本；
- 用户说明、创建 UTC 和规范化配置摘要。

任何影响频率轴、通道、dtype 或物理时窗的改变都必须创建新任务。

## 5. 单道频域模型

`FrequencySweep`：

| 字段 | 规则 |
|---|---|
| `data` | 复数只读数组，形状 `channel × frequency` |
| `frequencies_hz` | 一维严格递增，只读，长度等于 frequency 轴 |
| `channels` | 长度等于 channel 轴，顺序稳定 |
| `metadata` | 完整 `TraceMetadata` |
| `history` | 原始采集对象必须为空；处理输出另建对象 |

`FrequencyScan` 用于连续任务：`trace × channel × frequency`，所有道频率轴和通道契约必须一致。

## 6. 道元数据

`TraceMetadata` 至少包含：

- `mission_id`、`trace_index`、`trace_uid`、`device_id`；
- `sweep_started_utc`、`sweep_midpoint_utc`、`sweep_finished_utc`；
- 对应单调时钟纳秒值；
- `target_interval_s`、`actual_interval_s`、`schedule_error_s`；
- `connection_generation`（设备重连代数）；
- `raw_trace_sha256`；
- `gnss_match`（可以是无效/缺失对象，不能伪造）；
- 数据质量和设备状态摘要。

首道没有前一道，因此 `actual_interval_s` 和 `schedule_error_s` 可以为空。

## 7. GNSS 模型

`GnssFix` 与 `GnssMatch` 分开：

`GnssFix` 表示接收器产生的一次 fix：

- `received_utc`、可选 `nmea_utc`、接收单调时钟；
- `latitude_deg`、`longitude_deg`；
- `altitude_msl_m`、可选 `geoid_separation_m`；
- `fix_type`、`satellites`、`hdop`；
- 可选 `ground_speed_mps`、`course_deg`；
- `valid` 与结构化 `invalid_reason`；
- 可选原始 NMEA 引用。

`GnssMatch` 表示一道如何匹配 fix：

- `fix` 或空；
- `trace_midpoint_utc`；
- `age_s`（有符号匹配差或定义清楚的绝对年龄）；
- `method = nearest_midpoint`；
- `usable_for_map`；
- 不可用原因：`no_fix/stale/invalid/clock_unavailable/out_of_range` 等。

## 8. 时域与处理模型

`TimeDomainScan`：

- `data`：`trace × channel × time` 复数只读数组；
- `time_axis_s`：一维严格递增；
- `kind`：`time_base` 或 `time_processed`；
- `history`：有序 `ProcessingRecord`；
- 保留输入道元数据和位置关联。

`ProcessingRecord` 至少包含：

- 稳定 `stage_name` 和 `stage_version`；
- 完整规范化参数；
- 输入/输出域；
- 执行软件版本；
- 执行 UTC；
- 可选校准/背景引用 ID。

## 9. 位置与显示

地图位置只来自可用 `GnssMatch`。没有定位的道仍存在于 B-scan，不创建虚假坐标。时间估计距离如果未来引入，必须使用不同的 `position_source=time_estimated`，不得写入 GNSS 字段。

UI 选择模型使用 `trace_uid`，再由只读索引映射到 B-scan 列和地图点。UI 不把像素坐标作为业务标识。

## 10. 校验错误

领域校验至少区分：

- shape/dtype/axis 不兼容；
- 通道或频率契约不兼容；
- 标识重复或冲突；
- 配置摘要不匹配；
- 校准/背景域不匹配；
- GNSS 缺失、无效和过期（通常是数据状态，不一定抛异常）；
- 不支持的 schema/protocol 版本。

错误必须携带结构化代码和上下文，供协议、日志和 UI 映射；不得只依赖中文异常字符串判断流程。
