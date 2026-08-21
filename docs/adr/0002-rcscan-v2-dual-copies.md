# ADR-0002：采用 `.rcscan` v2 和空地双原始副本

- 状态：Accepted
- 日期：2026-08-21

## 背景

钢筋仪 `.rcscan` 已提供合适的 HDF5 格式家族和 raw/calibrated/time/history 分层。无人机场景额外需要 GNSS、任务/道唯一标识、增量写入、崩溃恢复、transport 状态和空地副本核对。旧 UAV-GPR 的逐道 CSV/NPZ 会产生大量小文件且难以形成稳定 schema。

## 决策

继续使用 `format_name=rcscan`，新 UAV profile 使用 `schema_version=2`。空中端和地面端都保存完整 `frequency_raw`、任务元数据和 GNSS；地面端可增加校准/时域/处理结果。

采集中使用 `.partial.rcscan` 增量写入、flush 和 checkpoint，结束后原子 finalize。地面端兼容读取钢筋仪 v1，但不伪装写 v1。

空地数据以逐道规范 raw hash、ID、axis 和通道核对，不要求完整文件哈希相同。

## 后果

- 需要新 schema 契约、reader/writer、恢复和 v1 兼容测试。
- HDF5 单 writer 和多数据集提交需要专门设计。
- 双副本占用更多磁盘，但提供飞行链路故障兜底和独立回放。
- 旧 UAV-GPR CSV/NPZ 只能通过显式迁移器导入。
