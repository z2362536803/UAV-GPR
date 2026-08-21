# M11：诊断、性能与部署（ISSUE-056～058）

工具默认只读或 dry-run。产品包可以被工具调用，产品代码不得反向依赖 `tools/`。

## ISSUE-056：数据一致性、partial 恢复与迁移诊断工具

- 状态：Planned
- 直接依赖：ISSUE-012～014、043
- 映射：FR-016、019

### 目标

提供操作员/研发可运行的 CLI：检查单文件、恢复计划、空地对拍、缺道/冲突报告和 v1 显式迁移。

### 范围

- `inspect`, `recover --dry-run/--execute`, `compare-air-ground`, `migrate-v1` 子命令。
- 稳定 human/JSON 输出、退出码、进度和取消。
- 默认只读/dry-run，输出新文件，源数据字节不变。
- 大任务流式、路径/目标覆盖保护和诊断证据目录。

### 排除项

- 不自动删/覆盖/修复原文件，不发网络补传，不做 Qt。

### 验收标准

- 每类文件/冲突有明确退出码；脚本可在 PowerShell 使用。
- 恢复/迁移要求显式 execute 和新目标。
- 合成大文件内存有界，敏感 GNSS 默认不打印完整值。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-056。确认 ISSUE-012～014/043 完成；阅读 AGENTS.md、docs/issues/README.md、docs/DATA_FORMAT.md、tools/README.md、docs/TESTING.md。

在 tools/diagnostics 和 tools/migration 提供统一 CLI：inspect、recover（默认 dry-run，显式 --execute）、compare-air-ground、migrate-v1。复用 storage/inventory API，不复制算法；输出稳定 human/JSON、退出码、进度/取消，流式处理大文件。任何写操作输出新目标，拒绝覆盖，源字节不变；完整 GNSS 默认脱敏。

禁止自动删除/原地修复、网络补传或 Qt。用损坏 partial、v1、乱序/缺道/冲突和十万道夹具做 CLI 集成测试。运行门禁并报告，停止，不 commit/push。
```

## ISSUE-057：可观测指标、性能基准与脱敏诊断包

- 状态：Planned
- 直接依赖：ISSUE-023、026、036、043、050、054
- 映射：性能与现场诊断门禁

### 目标

统一收集空地关键路径 metrics，提供可重复 benchmark 矩阵和不泄露 raw/完整轨迹/密钥的诊断包。

### 范围

- bounded histogram/counter/gauge：sweep/write/hash/outbox/link/ingest/ACK/process/UI/map。
- p50/p95/p99、队列水位、磁盘、错误和 task/thread 状态快照。
- benchmark CLI：storage/processing/transport/UI/map；环境/commit/config 记录。
- diagnostic bundle allowlist、路径/秘密/GNSS 脱敏和大小上限。

### 排除项

- 不上传云端、不加入遥测账号、不把 benchmark 放 UI 热路径。

### 验收标准

- metrics 自身有界低开销；同输入报告可比较。
- bundle 不含 `.rcscan` raw、token、完整坐标或本地配置秘密。
- 10 万道/长任务指标不线性保留每次样本。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-057。确认 ISSUE-023/026/036/043/050/054 完成；阅读 AGENTS.md、docs/issues/README.md、docs/PERFORMANCE.md、docs/DEPLOYMENT_HM30.md。

实现共享但有界的 metrics 抽象与快照：空中 sweep/write/flush/hash/outbox/GNSS，地面 receive/validate/write/ACK/process/backlog/UI/map；计算 p50/p95/p99/最大值而不保存所有样本。提供 storage/processing/transport/UI/map benchmark CLI，报告 commit/Python/硬件/config。实现 allowlist diagnostic bundle，脱敏 token/path/完整 GNSS，不包含 rcscan raw，限制大小。

禁止云上传/账号遥测或把基准扫描放 UI 热路径。测试指标有界、并发、低开销、报告确定性和 bundle 泄密反例；运行基准 smoke 与门禁。报告并停止，不 commit/push。
```

## ISSUE-058：配置加载、Windows 打包/启动器与 HM30 诊断

- 状态：Planned
- 直接依赖：ISSUE-045、046、049、056、057
- 映射：FR-001、017、部署门禁

### 目标

形成可在两台 Windows 电脑部署的 ground/air 构建、严格配置和只读 HM30 连通诊断，不自动更改网络/防火墙。

### 范围

- ground config loader 与 air config 对齐；分发 example/schema 和秘密策略。
- `uav-gpr-ground`、`uav-gpr-air`、诊断 CLI 正式 entry points。
- 选择/记录 Windows 打包工具，构建脚本、版本信息、资源和 smoke test。
- HM30 ping/端口/hello/heartbeat/test-record 只读诊断；PowerShell 启动器。

### 排除项

- 不自动改 IP、网卡、网关、防火墙或删除数据；不宣称 20 km 性能。

### 验收标准

- 干净目标机/隔离环境可启动 ground/air/diagnostics，默认不连真机。
- 配置错误/端口占用/缺资源有可读错误。
- HM30 工具只检测并给人工步骤，测试 trace 写入隔离测试任务。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-058。确认 ISSUE-045/046/049/056/057 完成；阅读 AGENTS.md、docs/issues/README.md、docs/DEPLOYMENT_HM30.md、config/*.example.toml、docs/adr/README.md。

完善 ground/air 严格配置 loader/schema/脱敏，添加正式 uav-gpr-ground、uav-gpr-air 和 diagnostics entry points；用 ADR/报告选择 Windows 打包方式并提供可重复构建、版本资源、PowerShell 启动器和隔离 smoke。实现只读 HM30 diagnostics：本地配置检查、ping/端口、protocol hello/heartbeat 和隔离 test-record。工具只能报告/建议，绝不自动改 IP/网关/网卡/防火墙或删除数据。

不得声称 20 km 或未经实测规格。测试坏配置、端口占用、缺资源、默认 backend disabled、打包启动和 fake/loopback HM30；若目标 Windows 构建环境不可用，明确 Blocked，不伪造打包通过。报告并停止，不 commit/push。
```
