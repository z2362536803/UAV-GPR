# 开发路线图

版本：0.1
状态：阶段级顺序；不是具体 Issue 清单

路线图遵循“先冻结不可变契约和可靠存储，再接真机与网络，最后组合 UI”的顺序。阶段已经拆分为 [60 个可执行 Issue 与 DeepSeek Harness 提示词](issues/README.md)；开工时仍需确认依赖和验收口径没有失效。

## Phase 0：项目初始化

交付：`src` 结构、工程配置、长期规则、产品/架构/数据/UI/测试/部署文档。

门禁：结构与文档链接自检通过，不包含伪功能。
状态：本次初始化目标。

## Phase 1：共享核心和契约

交付：不可变 ID、通道、任务、frequency/time、metadata、GNSS 和 processing history 模型。
门禁：shape/immutability/序列化/无假位置的单元测试。

## Phase 2：`.rcscan` v2 与可靠增量存储

交付：schema、reader/writer、checkpoint、partial recovery、逐道 raw hash、v1 reader。
门禁：崩溃注入、空地字段对拍、未知版本拒绝。

## Phase 3：采集接口、模拟器和回放

交付：backend 契约、controller、单调 scheduler、simulator、replay。
门禁：暂停/恢复/停止/故障和长时合成采集。

## Phase 4：LibreVNA 生产后端

交付：从钢筋仪项目审计迁移的 USB transport、严格 assembler、S11/计划双通道。
门禁：协议夹具、目标固件真机矩阵和吞吐报告。

## Phase 5：GNSS

交付：NMEA parser、reader/reconnect、fix cache、trace midpoint matcher。
门禁：跨午夜/stale/断线/真实 GNSS 记录回放。

## Phase 6：校准与处理

交付：`.rcal/.rcbg`、OSL/空采、带通/IFFT/Dewow/Flat 和应用编排。
门禁：参考黄金样本、双通道、provenance 和回放不二次处理。

## Phase 7：空地协议与 outbox

交付：协议 codec/state、心跳/命令、store-then-forward、ACK、重连、补传和冲突。
门禁：网络/磁盘/进程故障注入，ACK 语义与重启恢复。

## Phase 8：轻量空中端

交付：air application 状态机、配置、最小状态 UI/无头模式、诊断。
门禁：断网继续本地采集、磁盘失败安全停止和 8 小时耐久。

## Phase 9：地面端数据工作区

交付：菜单式主窗口、双 B-scan、状态栏、任务对话框、回放/保存。
门禁：目标分辨率/DPI、UI 线程边界、长历史有界刷新。

## Phase 10：GNSS 地图与双向联动

交付：离线/无底图地图、轨迹、当前位置、trace UID 联动。
门禁：完全断网、十万道、地图失败不影响采集。

## Phase 11：诊断、部署和系统验收

交付：一致性工具、HM30 部署、打包、现场操作文档和报告。
门禁：真实双电脑、LibreVNA/GNSS/HM30、断线补传、双副本对拍和干扰四组实验。

## Phase 12：首个现场发布

交付：版本化安装包、已知限制、恢复手册和经过签署的验收报告。
门禁：所有发布门禁通过，未解决的数据红线为零。

## 跨阶段红线

- 不以 UI 演示替代本地可靠存储。
- 不在 storage/schema 未冻结前堆积正式采集数据。
- 不接真机而没有模拟器/协议测试。
- 不在协议缺少幂等、ACK 和冲突规则时进行正式飞行。
- 不把待验证性能或 HM30 标称值写成产品保证。
