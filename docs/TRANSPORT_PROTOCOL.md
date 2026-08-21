# 空地传输协议

版本：0.1
状态：应用语义已决定；具体二进制 codec 在实现前用 ADR 和契约样本冻结

## 1. 目标

协议运行在 HM30 提供的普通 IP 链路上，首版计划使用单条持久 TCP 连接。协议必须支持：身份握手、心跳、命令状态、逐道原始数据、ACK、断线重连、幂等、缺道补传和任务结束对账。

HM30 是承载层，不改变协议的数据完整性要求。

## 2. 非目标

- 不用 NPZ 作为线上协议。
- 不重复发送 raw、display、time trace 等可从规范 raw 重建的数据。
- 不把网络内存队列当作可靠存储。
- 首版不处理多空中端、多订阅者、云端和视频流。

## 3. 连接角色

- 地面端监听固定端口。
- 空中端主动连接地面端，便于现场网络和重连。
- 每次 TCP 连接有新的 `session_id/connection_generation`，但任务 ID 不随重连改变。
- 握手交换设备 ID、软件版本、协议范围、能力、当前任务和 outbox 摘要。
- 不兼容 major 版本拒绝连接；minor 能力通过协商启用。

## 4. 消息族

| 类别 | 计划消息 |
|---|---|
| 握手 | `hello`, `hello_ack`, `incompatible` |
| 状态 | `heartbeat`, `air_status`, `error_report` |
| 命令 | `command`, `command_status` |
| 任务 | `mission_accepted`, `mission_rejected`, `mission_state`, `mission_manifest` |
| 数据 | `trace_record`, `trace_persisted_ack`, `trace_rejected` |
| 对账 | `inventory_summary`, `missing_request`, `conflict_report`, `sync_complete` |

每条命令携带 `command_id`；重复命令返回先前结果，不重新执行副作用。

## 5. Trace 语义

`trace_record` header 至少包含：

- protocol/schema version；
- `device_id`、`mission_id`、`trace_index`、`trace_uid`；
- 规范频率/通道配置摘要；
- dtype、shape、byte order、payload length；
- sweep 时间、间隔、连接代数；
- GNSS 匹配元数据；
- `raw_trace_sha256`。

binary payload 只包含一次规范原始频域数组。频率轴和通道定义在任务握手中冻结，trace 可引用配置摘要；恢复/独立解码所需信息必须能从任务消息和 trace header 得到。

大 payload 是否分块由基准决定；如分块，每块有 index/count/size，只有完整重组且 raw hash 通过后才能写入正式 trace。

## 6. ACK 规则

地面端只在以下条件全部满足后发送 `trace_persisted_ack`：

1. 身份、任务、schema、shape 和配置摘要有效；
2. raw hash 校验通过；
3. 重复/冲突规则已执行；
4. 原始数据和必需元数据已写入地面持久存储并 flush。

重复且哈希一致：不重复写入，可以返回相同 ACK。

同索引或 UID 但哈希不同：发送 conflict，保存诊断证据，不 ACK 成功且不覆盖。

空中端在收到有效 ACK 后只把 outbox 标记为已确认，不立即删除 `.rcscan` 原始道。

## 7. 持久化 outbox

outbox 保存：任务/道身份、air 文件引用、raw hash、状态、重试次数、最近发送/ACK 时间和错误。推荐使用 SQLite manifest 或等价事务存储；不保存每道 NPZ。具体选择需验证进程崩溃、HDF5 writer 并发和重启补传。

状态示例：

```text
stored -> pending -> in_flight -> acknowledged
                      \-> retry_wait
                      \-> conflict
```

内存队列只是对 persistent 状态的有界通知，可以丢通知后重新扫描，不能丢任务事实。

## 8. 重连与对账

1. 空中端重连并声明当前任务及 outbox 摘要。
2. 地面端声明已持久化的索引/哈希摘要。
3. 双方分页或区间对账，避免一次发送巨大列表。
4. 地面端请求缺失道；空中端从本地 `.rcscan` 重发。
5. 冲突单独报告并阻止任务标记完全同步。
6. 所有道和 manifest 核对后产生 `sync_complete`。

乱序到达可以接受，但物理 HDF5 行顺序不作为 `trace_index`；读取和显示必须依据显式索引排序。地面 ingest 必须有持久化策略，不能用无界内存等待缺道。

## 9. 流控与优先级

- 控制/心跳不能被大量 trace 永久饿死。
- 发送窗口、最大在途道数和队列阈值可配置并可观测。
- 地面端写盘变慢时通过 ACK 窗口形成背压；空中端仍以本地磁盘容量为安全缓冲。
- 每个 frame 的 header/payload 长度有严格上限，解析前先验证，防止内存分配攻击或损坏长度。

## 10. 安全与部署

首版在隔离 HM30 局域网运行，但协议仍需要设备身份和可选预共享认证设计。不得把密钥写入仓库或日志。未经认证的远程启动/停止在正式外场发布前必须有明确风险决策。

## 11. 候选 framing（未冻结）

可采用固定大端前缀：magic、protocol major/minor、message type、flags、JSON header length 和 binary payload length，后接 UTF-8 header 与 payload。最终编码必须：

- 有 magic 和严格长度上限；
- 能增量处理粘包/拆包；
- header 版本化；
- 不依赖 Python pickle；
- 为同一契约生成跨进程黄金帧样本。

在 ADR 接受前不得让临时 framing 成为事实标准。

## 12. 故障注入验收

- 任意字节边界拆包、合包、截断和超长长度。
- 连接在 payload 各阶段中断，重连后只确认完整持久化道。
- 命令重复、ACK 丢失、trace 重复、乱序和延迟。
- 同索引不同哈希、错误任务 ID、错误 config digest 和未知 schema。
- 地面磁盘满/flush 失败不 ACK；空中 outbox 在重启后可恢复。
- 心跳丢失不错误停止仍在安全本地采集的任务。
