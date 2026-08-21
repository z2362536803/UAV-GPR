# 数据格式

版本：0.1
状态：`.rcscan` v2 设计基线，尚未实现

## 1. 格式族

正式数据沿用钢筋仪项目的格式家族：

- `.rcscan`：HDF5 任务数据；
- `.partial.rcscan`：正在写入或未完成恢复的 HDF5 任务数据；
- `.rcal`：JSON 校准配置；
- `.rcbg`：JSON 空采背景参考。

UAV 文件根属性必须包含：

```text
format_name = "rcscan"
schema_version = 2
profile = "uav_gpr"
```

地面 reader 计划兼容钢筋仪 `rcscan` v1。写入时始终遵循 v2；升级旧文件必须显式产生新文件。

## 2. `.rcscan` v2 逻辑结构

```text
/
├── attrs
│   ├── format_name
│   ├── schema_version
│   ├── profile
│   ├── file_id
│   ├── file_role              # air | ground
│   ├── writer_version
│   └── lifecycle_state        # writing | finalized | recovered
├── mission/
│   ├── attrs: mission_id, device_id, created_utc, started_utc, ended_utc
│   ├── attrs: completion_kind, config_sha256
│   └── config_json
├── channels/
│   └── definitions_json
├── axes/
│   ├── frequencies_hz                      [frequency]
│   ├── time_base_s                         [time] optional
│   └── time_processed_s                    [time] optional
├── frequency/
│   ├── raw                                 [trace, channel, frequency]
│   └── calibrated                          [trace, channel, frequency] optional
├── time_base/
│   ├── data                                [trace, channel, time] optional
│   └── history_json
├── time_processed/
│   ├── data                                [trace, channel, time] optional
│   └── history_json
├── trace_metadata/
│   ├── trace_index                         [trace]
│   ├── trace_uid                           [trace]
│   ├── sweep_started_utc_ns                [trace]
│   ├── sweep_midpoint_utc_ns               [trace]
│   ├── sweep_finished_utc_ns               [trace]
│   ├── sweep_started_monotonic_ns          [trace]
│   ├── sweep_midpoint_monotonic_ns         [trace]
│   ├── sweep_finished_monotonic_ns         [trace]
│   ├── target_interval_s                   [trace]
│   ├── actual_interval_s                   [trace]
│   ├── schedule_error_s                    [trace]
│   ├── connection_generation              [trace]
│   └── raw_trace_sha256                    [trace]
├── gnss/
│   ├── valid                               [trace]
│   ├── invalid_reason                      [trace]
│   ├── received_utc_ns                     [trace]
│   ├── nmea_utc_ns                         [trace]
│   ├── latitude_deg                        [trace]
│   ├── longitude_deg                       [trace]
│   ├── altitude_msl_m                      [trace]
│   ├── geoid_separation_m                  [trace]
│   ├── fix_type                            [trace]
│   ├── satellites                          [trace]
│   ├── hdop                                [trace]
│   ├── ground_speed_mps                    [trace]
│   ├── course_deg                          [trace]
│   ├── match_age_s                         [trace]
│   └── raw_nmea                            [trace] optional
├── acquisition/
│   ├── device_status_json                  [trace]
│   └── quality_flags                       [trace]
├── transport/                              optional, role-specific
│   ├── sent_utc_ns                         [trace]
│   ├── ack_utc_ns                          [trace]
│   ├── retry_count                         [trace]
│   └── receive_status                      [trace]
└── checkpoints/
    ├── committed_record_count
    ├── last_trace_index
    └── updated_utc
```

最终实现可在不改变语义的前提下选择 HDF5 属性、定长字符串或列式数据集；任何具体 dtype/chunk 变化必须通过 schema 契约测试固定。

## 3. 增量写入

- `frequency/raw` 使用 trace-major、可扩展第一维，建议 chunk `(1, channel_count, frequency_count)`；压缩算法需通过 CPU/写盘基准选择。
- 同一道的 raw、trace metadata、GNSS 和 checkpoint 构成一个逻辑提交。
- HDF5 不提供跨多个数据集的完整事务，因此 writer 必须先写数据、flush，再更新 `committed_record_count` 并再次 flush。
- reader 只把物理行号小于 `committed_record_count` 且所有必需列完整的记录视为已提交。
- HDF5 物理行是可靠提交顺序，不等同于逻辑 `trace_index`；空中端通常有序，地面端可以因补传而乱序。读取、对账和显示必须使用显式 `trace_index/trace_uid`，重复或冲突在追加前处理。
- 任务参数冻结后 axes/channel 不再改变；不兼容 sweep 必须拒绝并停止任务。
- writer 进程内只有一个所有者；其他线程通过有界命令队列提交不可变对象。

## 4. 文件生命周期

```text
create <file_id>.partial.rcscan
  -> lifecycle_state=writing
  -> append/flush/checkpoint each committed trace
  -> set mission end + completion_kind
  -> lifecycle_state=finalized
  -> close HDF5
  -> atomic rename to <mission_id>.rcscan
```

崩溃后：

1. 不自动覆盖或删除 partial 文件；
2. 只读扫描 schema、checkpoint、各数据集长度和逐道哈希；
3. 生成恢复报告；
4. 用户或受控策略确认后，截取到最后完整提交点并输出新的 recovered 文件；
5. 原 partial 文件继续保留。

## 5. 逐道原始哈希

空地一致性必须使用规范化字节流计算 `raw_trace_sha256`。规范至少固定：

1. 哈希版本标记；
2. `mission_id`、`trace_index`、`trace_uid` 的 UTF-8 规范形式；
3. 有序 channel ID；
4. 频率轴转为 little-endian float64 连续字节；
5. 原始复数数组转为 C-order little-endian complex128 连续字节。

哈希函数的确切 framing 必须在实现前写成契约样本，避免简单拼接的歧义。GNSS 不放入 raw hash，以便定位字段补正时不改变雷达原始数据身份；GNSS 自身可以有独立记录哈希。

## 6. 空地文件差异

空中端和地面端文件不要求整文件相同：

- 两端必须相同：任务 ID、道索引/UID、频率轴、通道、原始数组、逐道 raw hash 和接收到的 GNSS 记录。
- 地面端可以增加：校准频域、时域数据、处理历史、人工注释和完整性报告。
- transport 状态在两端含义不同。

因此一致性工具逐字段/逐道比较，不比较 HDF5 文件 SHA256。

## 7. 缺失值

- 浮点缺失使用 NaN，但必须配合 `valid`/reason 字段，不能仅靠 NaN 推断原因。
- 时间缺失使用专门有效位或约定的整数哨兵，schema 中固定。
- GNSS 无效记录仍占对应 trace 行，保持所有 trace-major 数据集等长。
- 首道实际间隔可以缺失。

## 8. `.rcal` 与 `.rcbg`

延续钢筋仪项目的 JSON 思路，至少包含：

- `format_name`、`schema_version`、profile/reference ID；
- 创建 UTC、软件版本、设备/端口/通道；
- 完整频率轴与扫频配置摘要；
- 复数值的明确编码；
- 采集道数、统计和质量报告；
- OSL 标准件/算法或空采数据域；
- 内容摘要。

加载时严格检查通道、S 参数、频率轴、数据域和算法版本。用户选择文件不等于兼容，也不等于自动完成物理校准。

## 9. v1 兼容与迁移

- reader 识别 `schema_version=1` 并映射到内存领域模型。
- v1 没有任务/GNSS/transport 字段时保持为空，不生成假值。
- 若需要把 v1 保存为 v2，生成新的 `mission_id/file_id`，记录迁移 provenance 和源文件哈希。
- 不原地升级，不把 v2 专有字段塞入仍标记 v1 的文件。

## 10. 隐私与保留

GNSS 轨迹属于敏感现场数据。导出、日志和诊断包默认最小化位置数据；公开测试夹具必须使用合成坐标。空中端副本和地面端副本的保留/清理策略由部署文档和用户操作共同控制。
