# M02：`.rcscan` v2 与可靠存储（ISSUE-008～014）

本里程碑先让数据可靠落盘、可恢复、可对拍。未通过本门禁，不允许把真实飞行数据托付给新软件。

## ISSUE-008：冻结 `.rcscan` v2 物理 schema 与 codec

- 状态：Done（2026-08-27 独立审查 PASS WITH CONDITIONS 后经项目负责人授权合并，见 [docs/reports/ISSUE_008_REVIEW_REPORT.md](../reports/ISSUE_008_REVIEW_REPORT.md)）
- 直接依赖：ISSUE-004～007
- 映射：FR-010、ADR-0002

### 目标

把 `DATA_FORMAT.md` 的逻辑结构落实为精确 HDF5 dtype、shape、缺失值、属性和 schema codec 契约。

### 范围

- 根/mission/channels/axes/frequency/trace_metadata/gnss/acquisition/transport/checkpoint 的物理 schema。
- air/ground role 差异、定长/变长字符串策略、JSON/complex/time 编码。
- trace-major 可扩展数据集、chunk/compression 默认值和严格 schema version 探测。
- 小型 schema 黄金文件/manifest；未知 major/profile 拒绝。

### 排除项

- 不实现增量业务 writer、恢复、v1 迁移或处理算法。

### 验收标准

- schema 创建后 HDF5 结构/dtype 与契约完全对拍。
- 缺失 GNSS/时间有有效位或固定哨兵，不靠猜 NaN 原因。
- 不支持版本 fail-closed，air/ground 所需组明确。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-008。确认 ISSUE-004～007 完成；阅读 AGENTS.md、docs/issues/README.md、docs/DATA_FORMAT.md、docs/adr/0002-rcscan-v2-dual-copies.md、docs/TESTING.md。

把 rcscan v2 逻辑设计落实为 storage 内的精确 schema 常量/codec/创建器：固定根属性、各 group/dataset、dtype、shape、maxshape、字符串/JSON/complex/UTC/缺失值编码、air/ground role 和版本探测。创建小型合成黄金 schema 及契约测试。未知 schema/profile 必须拒绝。物理行是提交顺序，不等于 trace_index。

不要实现业务 append writer、恢复、v1 迁移或处理。若具体选择改变已有设计，先更新 DATA_FORMAT/ADR 并说明，不得隐式决定。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-009：规范逐道 raw 哈希与黄金向量

- 状态：Done（2026-08-28 独立复审 PASS WITH CONDITIONS 后经项目负责人授权合并，见 [docs/reports/ISSUE_009_REVIEW_REPORT_R3.md](../reports/ISSUE_009_REVIEW_REPORT_R3.md)）
- 直接依赖：ISSUE-004～006
- 映射：FR-008、009、019

### 目标

冻结无歧义、跨空地一致的 `raw_trace_sha256` framing 与实现。

### 范围

- 哈希版本、长度前缀/字段 framing、ID、通道、有序频率轴和 C-order little-endian complex128。
- 输入规范化但不修改领域数组。
- 合成黄金向量（含 expected digest）和 hash 元数据校验。
- 明确 GNSS 不进入 raw hash。

### 排除项

- 不写 HDF5、不比较整文件 hash、不做 transport。

### 验收标准

- 等价内存布局/本机字节序得到相同 digest；任一身份/axis/channel/raw 改变会变化。
- 简单拼接歧义被长度 framing 消除。
- 非规范 shape/dtype/ID fail-closed。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-009。确认 ISSUE-004～006 完成；阅读 AGENTS.md、docs/issues/README.md、docs/DATA_FORMAT.md 第 5 节和 docs/DATA_MODEL.md。

定义并实现 versioned canonical raw trace hash：使用无歧义长度 framing，纳入 mission_id、trace_index、trace_uid、有序 channel IDs、little-endian float64 frequency axis、C-order little-endian complex128 raw。不得纳入 GNSS，不得修改输入，不得用完整 HDF5 文件 hash 代替。把精确 framing 写入 DATA_FORMAT，并提交多个合成黄金向量及 expected SHA256。

测试不同内存布局/字节序等价、任一字段变化、非法 dtype/shape/ID 和 framing 歧义。运行门禁并报告，停止，不执行 writer，不 commit/push。
```

## ISSUE-010：增量 writer、checkpoint 与原子 finalize

- 状态：Done（2026-08-28 独立复审 PASS WITH CONDITIONS 后经项目负责人授权合并，见 [docs/reports/ISSUE_010_REVIEW_REPORT_R2.md](../reports/ISSUE_010_REVIEW_REPORT_R2.md)）
- 直接依赖：ISSUE-008、009
- 映射：FR-007、008、010、ADR-0004

### 目标

实现单所有者 `.partial.rcscan` 增量写入，使完整道先数据/元数据 flush，再提交 checkpoint，最后安全 finalize。

### 范围

- air/ground writer 生命周期、创建、append、flush、checkpoint、终态和原子改名。
- 同一道 raw/metadata/GNSS/hash 的逻辑提交；配置/axis/channel 冻结。
- 物理记录顺序与逻辑 trace index 分离，追加前重复/冲突接口。
- 可注入文件系统/HDF5 故障点和幂等 close/finalize。

### 排除项

- 不实现恢复工具、网络 ACK、outbox 或 UI。

### 验收标准

- 每个故障点后 reader 最多看到最后完整 checkpoint，不看到半道。
- 不兼容 sweep、重复冲突、磁盘/flush 失败不推进 checkpoint。
- finalized 文件不可继续 append，原 partial 不被无意覆盖。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-010。确认 ISSUE-008/009 完成；阅读 AGENTS.md、docs/issues/README.md、docs/DATA_FORMAT.md、docs/ARCHITECTURE.md 的 store-then-forward 流程和 ADR-0004。

实现单 writer 所有权的 RcScanIncrementalWriter：创建 .partial.rcscan，冻结 mission/config/axis/channels，按“写 raw+metadata+GNSS+hash -> flush -> 更新 committed_record_count -> 再 flush”提交，支持 air/ground role、明确 completion_kind、幂等 close 和关闭后的原子 rename。提供可控故障注入点；物理行不得被当作 trace_index。

不要实现 reader 恢复、网络 ACK/outbox/UI。测试每个写入/flush/checkpoint/finalize 故障、重复/冲突、不兼容 axis、正常/用户停止/失败终态和不覆盖已有目标。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-011：reader、严格校验与逻辑道排序

- 状态：Done（2026-08-30 独立复审 PASS WITH CONDITIONS 后经项目负责人授权合并，见 [docs/reports/ISSUE_011_REVIEW_REPORT.md](../reports/ISSUE_011_REVIEW_REPORT.md)）
- 直接依赖：ISSUE-008～010
- 映射：FR-010、016

### 目标

实现只读 reader/validator，只暴露完整提交记录，并按显式 `trace_index/trace_uid` 提供逻辑视图。

### 范围

- schema/profile/role/lifecycle 检查，读取 mission/axis/channel/raw/metadata/GNSS/processed optional。
- 只读取 checkpoint 以内且列完整记录；检测 dataset 长度、dtype、hash、ID 和重复/冲突。
- 物理记录视图与按 trace index 排序的逻辑视图；缺道清单。
- lazy/分块读取，避免强制全文件驻内存。

### 排除项

- 不修复文件、不写 migration、不运行处理。

### 验收标准

- 尾部半写记录不可见；乱序补传可正确排序。
- 未知版本、损坏 checkpoint、重复索引不同 hash 被明确拒绝/报告。
- 大合成文件可分块读取。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-011。确认 ISSUE-008～010 完成；阅读 AGENTS.md、docs/issues/README.md、docs/DATA_FORMAT.md 和 docs/TESTING.md。

实现只读 RcScanReader/Validator：严格验证 schema/profile/role/lifecycle/dtype/长度/checkpoint，只暴露 committed_record_count 以内且必需列完整的记录；分别提供物理提交顺序和按显式 trace_index/trace_uid 排序的逻辑迭代/分块读取，报告缺道、重复和冲突。可选 processed 组缺失必须合法，未知版本 fail-closed。

不要修复或改写文件，不自动迁移，不运行处理。测试半写尾部、乱序、缺道、重复同 hash、冲突 hash、损坏长度/checkpoint、缺 GNSS 和大文件 lazy 读取。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-012：partial 检查与非破坏恢复

- 状态：Planned
- 直接依赖：ISSUE-010、011
- 映射：FR-016、019

### 目标

在不改写原 partial 的前提下检查崩溃文件，并把最后完整提交点恢复为新文件和审计报告。

### 范围

- 只读 inspect report：schema、checkpoint、各列长度、尾部状态、hash/ID 问题。
- recovery plan 与显式执行：复制已提交完整记录到新的 recovered `.rcscan`。
- 新 file ID、`completion_kind=recovered`、源文件 SHA256/provenance。
- dry-run 默认、目标存在保护和中途失败清理策略。

### 排除项

- 不原地 truncate/修复，不自动删除 partial，不做 GUI。

### 验收标准

- 任意写入故障夹具都能生成稳定报告；恢复文件可被严格 reader 读取。
- 原 partial 字节不变；恢复失败不留下伪 finalized 文件。
- 未经确认只 dry-run。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-012。确认 ISSUE-010/011 完成；阅读 AGENTS.md、docs/issues/README.md、docs/DATA_FORMAT.md 第 4 节和 docs/TESTING.md。

实现 partial 只读检查与非破坏恢复 API：先生成结构化 report/plan，默认 dry-run；显式执行时把最后完整 committed 记录复制到新的 recovered 文件，生成新 file_id，记录源文件 SHA256、恢复工具版本和 completion_kind。绝不原地 truncate/改写/删除源 partial，目标已存在必须拒绝，恢复中断不得留下看似 finalized 的结果。

用 ISSUE-010 的各故障点生成夹具，验证报告确定性、源字节不变、恢复往返、目标冲突和恢复过程失败。不要做 GUI。运行门禁并报告，停止，不 commit/push。
```

## ISSUE-013：`.rcscan` v1 兼容读取与显式迁移

- 状态：Planned
- 直接依赖：ISSUE-001、011、012
- 映射：FR-010、016

### 目标

让地面端安全读取钢筋仪 `.rcscan` v1，并可选择生成带完整 provenance 的新 v2 文件。

### 范围

- 冻结钢筋仪 v1 schema 来源/哈希和匿名黄金夹具。
- v1 adapter 映射 raw/calibrated/time/channels/axes/history；缺任务/GNSS 保持空。
- 显式 v1→v2 工具/API，生成新 mission/file ID 和源文件 hash。
- 不支持/损坏 v1 的字段级报告。

### 排除项

- 不导入旧 UAV-GPR CSV/NPZ，不原地升级，不伪造 UTC/GNSS。

### 验收标准

- 真实结构的匿名 v1 fixture 可读取；往返迁移保持数值/axis/channel/history。
- 缺字段不生成当前时间或 0 坐标。
- 源 v1 文件不变，v2 明确记录 migration provenance。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-013。确认 ISSUE-001/011/012 完成；阅读 AGENTS.md、docs/issues/README.md、docs/DATA_FORMAT.md 第 9 节和 docs/REFERENCE_MIGRATION.md。

按 ISSUE-001 manifest 冻结钢筋仪 rcscan v1 实际 schema/reader 来源和 SHA256，建立不含现场隐私的小型黄金 fixture。实现 v1 只读 adapter，把已有 raw/calibrated/time/channels/axes/history 映射到新领域模型；v1 缺 mission/GNSS/UTC 时保持 None。实现显式 v1->v2 新文件迁移，记录源文件 hash、工具版本和 provenance。

禁止原地升级、伪造时间/GNSS、导入旧 UAV-GPR CSV/NPZ。测试数值/axis/channel/history 对拍、损坏/未知 v1、源字节不变和重复迁移确定性。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-014：空地 inventory 与逐道一致性服务

- 状态：Planned
- 直接依赖：ISSUE-009、011、012
- 映射：FR-009、019

### 目标

在不比较整文件 SHA 的情况下，生成可分页的任务 inventory 并核对空地逐道身份、axis、channel、raw hash 和 GNSS。

### 范围

- `MissionInventory`、分页/区间摘要、缺失/额外/重复/冲突分类。
- 空地任务/config/axis/channel 契约检查。
- 逐道 raw hash 主一致性，GNSS 字段差异单独报告。
- 稳定、可序列化 report，供协议与诊断工具复用。

### 排除项

- 不发送网络消息、不自动补传、不删除/改写文件。

### 验收标准

- 乱序物理记录不影响结果；同 hash 重复与不同 hash 冲突区分。
- ground 独有 processed/transport 字段不造成 raw 不一致。
- 大任务可分页/流式处理，内存有界。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-014。确认 ISSUE-009/011/012 完成；阅读 AGENTS.md、docs/issues/README.md、docs/DATA_FORMAT.md 第 5/6 节和 docs/TRANSPORT_PROTOCOL.md 的对账语义。

实现纯应用/存储一致性服务：从 reader 生成可分页 MissionInventory，比较 mission/config/axis/channels，并按 trace_index/trace_uid/raw hash 分类 missing、extra、duplicate-same、conflict；GNSS 差异另列，不比较完整 HDF5 hash，也不因 ground 的 processed/transport 组不同报 raw 冲突。结果稳定序列化且大任务流式有界。

不要发网络消息、补传、修复或删除文件。测试乱序、缺道、同 hash 重复、不同 hash 冲突、GNSS 差异和十万条分页。运行门禁，报告并停止，不 commit/push。
```
