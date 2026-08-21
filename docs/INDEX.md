# 项目文档索引

本目录描述目标架构和后续开发约束。当前仓库处于初始化阶段；除目录结构和工程配置外，文档中的产品能力均为待实现设计。

## 必读顺序

1. 根目录 `AGENTS.md`：不可违反的长期规则。
2. [PRODUCT_REQUIREMENTS.md](PRODUCT_REQUIREMENTS.md)：产品目标、用户流程和范围。
3. [PRODUCT_SPECIFICATIONS.md](PRODUCT_SPECIFICATIONS.md)：初始可验证规格。
4. [ARCHITECTURE.md](ARCHITECTURE.md)：双端分层和数据流。
5. 与任务相关的专题文档。

## 专题文档

| 文档 | 内容 |
|---|---|
| [DATA_MODEL.md](DATA_MODEL.md) | 核心对象、数组形状、标识、时间和 GNSS 模型 |
| [DATA_FORMAT.md](DATA_FORMAT.md) | `.rcscan` v2、`.rcal/.rcbg` 与崩溃恢复 |
| [ACQUISITION.md](ACQUISITION.md) | LibreVNA 采集、任务冻结、间隔与时窗 |
| [CALIBRATION.md](CALIBRATION.md) | OSL、空采背景和兼容性规则 |
| [PROCESSING.md](PROCESSING.md) | 独立处理阶段、顺序和 provenance |
| [GNSS.md](GNSS.md) | NMEA、fix、时间匹配和失效语义 |
| [TRANSPORT_PROTOCOL.md](TRANSPORT_PROTOCOL.md) | 空地协议、状态机、ACK、补传和冲突 |
| [UI.md](UI.md) | 地面端菜单式 UI、双 B-scan 与右侧地图 |
| [OFFLINE_MAP.md](OFFLINE_MAP.md) | 离线地图、无底图降级和轨迹性能 |
| [PERFORMANCE.md](PERFORMANCE.md) | 基准方法、预算和长时稳定性 |
| [TESTING.md](TESTING.md) | 测试分层、矩阵和硬件验收 |
| [DEPLOYMENT_HM30.md](DEPLOYMENT_HM30.md) | HM30 网络、供电和干扰验证计划 |
| [REFERENCE_MIGRATION.md](REFERENCE_MIGRATION.md) | 两个参考项目的迁移白名单与审计方法 |
| [ROADMAP.md](ROADMAP.md) | 阶段顺序、门禁和里程碑，不是具体 Issue 清单 |
| [issues/README.md](issues/README.md) | 60 个完整 Issue、依赖顺序、验收标准与 DeepSeek Harness 提示词 |

## 决策、计划与报告

- [adr/README.md](adr/README.md)：架构决策记录及已冻结决策。
- [plans/README.md](plans/README.md)：后续任务实施计划的存放规则。
- [reports/README.md](reports/README.md)：基准、硬件和现场验证报告的存放规则。
- [issues/README.md](issues/README.md)：逐 Issue 执行总表；每次 Harness 会话只执行一个 Issue。

## 文档状态词

- **已决定**：已由需求或 ADR 冻结，开发必须遵守。
- **计划**：目标明确但尚未实现。
- **待验证**：需要基准、真机或现场数据后才能定稿。
- **已实现**：必须同时有代码与自动化测试证据；当前初始化阶段不使用此标签描述产品能力。
