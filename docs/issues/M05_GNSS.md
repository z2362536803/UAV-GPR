# M05：GNSS 读取与道匹配（ISSUE-024～026）

这是旧 UAV-GPR 唯一允许参考生产代码的领域，但只能迁移 parser/reader/matcher，不迁移地图和窗口。

## ISSUE-024：GGA/RMC NMEA 解析器

- 状态：Done（2026-09-02 独立复审 VERDICT=PASS 后经项目负责人自动化授权合并，见 [docs/reports/ISSUE_024_REVIEW_REPORT.md](../reports/ISSUE_024_REVIEW_REPORT.md)；1 项 P3 非阻塞，可随 ISSUE-025 顺手关闭）
- 直接依赖：ISSUE-001、005
- 映射：FR-006、ADR-0005

### 目标

实现纯函数式、严格 checksum/范围/单位的 GGA/RMC 解析，并正确组合 UTC 日期、半球、MSL 和速度。

### 范围

- 按 I001 manifest 审计旧 UAV-GPR GNSS 来源并冻结匿名 NMEA 夹具。
- talker-independent GGA/RMC、checksum、lat/lon、fix、sats、HDOP、MSL/geoid、knots→m/s、course。
- RMC 日期与 GGA 日内时间组合、跨午夜策略和结构化 parse result/error。
- 最大行长和非 ASCII/空字段保护。

### 排除项

- 不读串口、不缓存、不做 trace 匹配、不迁移地图。

### 验收标准

- 南/西半球、跨午夜、坏 checksum、无 fix、越界字段结果明确。
- GGA MSL 不标为 AGL；无字段保持空而非 0。
- parser 无 Qt/serial 依赖。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-024。确认 ISSUE-001/005 完成；阅读 AGENTS.md、docs/issues/README.md、docs/GNSS.md、docs/REFERENCE_MIGRATION.md、ADR-0005。

只参考 baseline 白名单中的 UAV-GPR GNSS parser/测试，记录源 SHA256 和排除内容；实现纯 NMEA GGA/RMC parser：校验 checksum/长度/范围，支持不同 talker、南西半球、fix/sats/HDOP、MSL/geoid、RMC 日期、knots->m/s、course 和跨午夜组合策略。输出不可变 GnssFix/结构化错误；空字段不能变 0，MSL 不能称 AGL。

不得读取串口、缓存、匹配 trace 或复制旧地图/UI。用匿名/合成 NMEA 覆盖正常、坏 checksum、无 fix、空字段、越界、跨午夜和非 ASCII。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-025：GNSS reader、重连与有界 fix 缓存

- 状态：Done（2026-09-02 独立复审 VERDICT=PASS 后经项目负责人授权合并，见 [docs/reports/ISSUE_025_REVIEW_REPORT.md](../reports/ISSUE_025_REVIEW_REPORT.md)；4 项 P3 由合并后小修批次关闭，见执行记录）
- 直接依赖：ISSUE-005、024
- 映射：FR-006、018

### 目标

在独立 worker 中可取消地读取串口、解析、重连，并发布不可变状态与有界时间序列 fix 缓存。

### 范围

- Serial adapter 注入、增量按行、超时/最大行、parser 集成。
- disconnected/no-sentence/no-fix/valid/stale/invalid 状态。
- 退避重连、generation、结构化指标和幂等 stop/close。
- 按时间/容量双上限的 thread-safe snapshot 缓存。

### 排除项

- 不匹配 sweep、不渲染地图、不让串口错误停止雷达采集。

### 验收标准

- 拆行/合行、高频输入、断开重连和关闭无死锁/泄漏。
- 缓存有界且 snapshot 不暴露可写内部状态。
- 默认测试不打开真实 COM 口。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-025。确认 ISSUE-005/024 完成；阅读 AGENTS.md、docs/issues/README.md、docs/GNSS.md 第 3/4 节和 docs/ARCHITECTURE.md 的线程边界。

实现可注入 SerialAdapter 的 GnssReader worker：增量拆行、长度/timeout、调用 parser、发布 disconnected/no_sentence/no_fix/valid/stale/invalid 状态，I/O 错误按有界退避重连并增加 generation。实现按时间和容量有界、线程安全的不可变 fix snapshot cache；stop 能取消阻塞读取并幂等释放端口。GNSS 错误只上报，不停止雷达采集。

默认测试使用 fake serial，覆盖任意拆行、坏行后恢复、高频输入、断开/重连、退避、停止和缓存淘汰，不用固定 sleep。不要做 sweep 匹配或地图。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-026：sweep midpoint GNSS 匹配器

- 状态：Planned
- 直接依赖：ISSUE-005、025
- 映射：FR-006、014

### 目标

用 sweep midpoint 在同一单调时间域选择最近 fix，保存有符号匹配差、stale 与不可用原因。

### 范围

- midpoint 计算、最近邻、等距 tie-break、缓存窗口和配置 stale 阈值。
- 优先 monotonic，UTC 仅审计/无共同域时按明确策略拒绝或降级。
- no-fix/stale/invalid/clock-unavailable/out-of-range。
- 输出 `GnssMatch`，不改写 fix/trace metadata。

### 排除项

- 不做插值轨迹、AGL 推算、地图或固定接收器延迟校正。

### 验收标准

- midpoint 前后、正负 age、等距和阈值边界确定。
- stale fix 不 `usable_for_map`，但原因/历史仍可保存。
- 没有共同时间基准不伪匹配。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-026。确认 ISSUE-005/025 完成；阅读 AGENTS.md、docs/issues/README.md、docs/GNSS.md 第 5 节、docs/DATA_MODEL.md。

实现纯 GnssTraceMatcher：根据 sweep start/end 计算 midpoint，从有界 cache 选择同一 monotonic 域最近 fix，定义等距 tie-break，输出有符号 match age、method、usable_for_map 与 no_fix/stale/invalid/clock_unavailable/out_of_range 原因。stale 阈值来自 MissionConfig；UTC 仅用于审计或有明确策略的 fallback，不得伪造共同时间域。

不要插值轨迹、推算 AGL、硬编码接收器延迟或做地图。测试 midpoint 前后、正负 age、等距、阈值边界、跨 generation、无共同时钟和空缓存。运行门禁，报告并停止，不 commit/push。
```
