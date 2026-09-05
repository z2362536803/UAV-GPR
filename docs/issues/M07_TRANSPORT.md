# M07：空地协议与可靠传输（ISSUE-037～043）

本里程碑重新实现协议，不迁移旧 UAV-GPR 的 RemoteSender/Receiver 或 NPZ payload。ACK 只代表地面已验证并持久化。

## ISSUE-037：协议 ADR、消息模型与二进制 framing

- 状态：Done（2026-09-05 自动化轮：t3 复审 needs_revision（P2-1 trace 域错误逃逸）→ t4 repair 闭合 P2-1+P3×3 → t5 round-2 VERDICT=PASS 后自动合并；ADR-0006 转 Accepted。变更全集=5 inScope+测试文件+计划日志（t4 登记口径差异已在 t5 复审核验），见 [docs/reports/ISSUE_037_REVIEW_REPORT.md](../reports/ISSUE_037_REVIEW_REPORT.md)）
- 直接依赖：ISSUE-003、005、006、009
- 映射：FR-001、002、008、009

### 目标

用 ADR 和黄金帧冻结 protocol v1 的版本协商、消息 envelope、长度上限、canonical JSON/header 和 binary raw framing。

### 范围

- hello/status/command/mission/trace/ack/inventory/error 消息的不可变模型。
- major/minor、magic、type、flags、header/payload length、上限和增量 frame parser。
- UTF-8 canonical header、无 pickle/NPZ、binary trace payload 引用 raw hash。
- 任意粘包/拆包、截断/未知类型的契约测试和黄金 bytes。

### 排除项

- 不打开 socket、不实现 heartbeat/outbox/业务状态机。

### 验收标准

- ADR 记录候选、选择与后果；codec 跨进程确定。
- parser 在读 payload 前验证上限，损坏不无界分配。
- unknown minor 能按能力策略处理，major 不兼容拒绝。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-037。确认 ISSUE-003/005/006/009 完成；阅读 AGENTS.md、docs/issues/README.md、docs/TRANSPORT_PROTOCOL.md、docs/adr/README.md。

先写并用测试证据完成 protocol framing ADR，再实现 protocol v1 的不可变消息/envelope 和增量 codec：magic、major/minor、type、flags、canonical UTF-8 header、binary payload length、严格上限和 golden bytes。覆盖 hello/status/command/mission/trace/ACK/inventory/error 的字段契约。禁止 pickle、NPZ 和重复发送 display/time 派生数据；trace payload 只承载规范 raw 并引用 ISSUE-009 hash。

不要打开 socket、实现 heartbeat/outbox/应用状态。测试任意 chunk、粘包、截断、恶意长度、未知 type/version、非 canonical header 和黄金帧。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-038：TCP 连接、握手、能力协商与心跳

- 状态：Planned
- 直接依赖：ISSUE-037
- 映射：FR-001

### 目标

实现地面监听、空中主动连接的可取消 TCP 会话，完成身份/版本/能力协商和不阻塞业务数据的心跳。

### 范围

- socket adapter、增量 codec 接线、读写 worker、连接 generation 和安全关闭。
- hello/ack：device ID、软件/协议、能力、当前 mission/outbox 摘要。
- heartbeat interval/timeout、RTT/last-seen 状态。
- 控制与 trace 有界优先队列/发送窗口基础。

### 排除项

- 不实现具体命令、trace 持久 ACK、补传或 UI。

### 验收标准

- 任意拆包/断开可安全重连，旧会话消息不污染新 generation。
- trace backlog 不永久饿死 heartbeat/control。
- incompatible major/device mismatch 明确拒绝。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-038。确认 ISSUE-037 完成；阅读 AGENTS.md、docs/issues/README.md、docs/TRANSPORT_PROTOCOL.md 第 3/7/9 节和 docs/ARCHITECTURE.md。

实现可注入 socket 的 ground listener / air outbound TCP session，复用 codec 完成 hello/ack、device ID、protocol/capability、connection_generation、当前 mission/outbox 摘要和 heartbeat/timeout/RTT 状态。读写可取消、队列有界，控制/心跳优先于 trace，关闭不遗留线程；旧 session 的迟到数据不得进入新 generation。

不要实现业务命令、trace ACK/补传/UI。用 loopback/fake socket 测试任意拆包、慢写、半开、timeout、重连、版本/设备不匹配和队列优先级，不用固定 sleep。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-039：幂等远程命令与命令状态机

- 状态：Planned
- 直接依赖：ISSUE-038
- 映射：FR-002、005、009

### 目标

实现带 `command_id` 的 start/pause/resume/stop/status 命令和 received/accepted/executing/succeeded/failed 状态，重复命令不重复副作用。

### 范围

- command model、schema/config digest、deadline 和调用者可见结果。
- 空中 command registry/幂等结果缓存，地面 pending tracker。
- 命令与 mission 状态前置条件、拒绝原因、重连后查询。
- 有界持久/恢复策略接口（具体任务副作用后续接 air runner）。

### 排除项

- 不实际启动采集、不做 UI、不把“收到”当“完成”。

### 验收标准

- 同 command ID 同内容返回旧结果；同 ID 不同内容冲突拒绝。
- 状态严格单向且可在重连后重放/查询。
- 非法 mission/state/config digest 不执行 handler。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-039。确认 ISSUE-038 完成；阅读 AGENTS.md、docs/issues/README.md、docs/TRANSPORT_PROTOCOL.md 第 4 节、docs/ARCHITECTURE.md 的命令/任务状态。

实现 versioned Command/CommandStatus、ground pending tracker 和 air idempotent command registry。状态必须区分 received/accepted/executing/succeeded/failed；同 command_id+同内容返回先前结果，不重复调用 handler；同 ID 不同内容 fail-closed。验证 mission/state/config digest/deadline，支持重连后结果查询。handler 用 fake 注入，本 Issue 不实际启动采集。

不要做 UI 或把 ack 接收当执行完成。测试重复、冲突、乱序状态、断线重查、非法前置状态和缓存上限。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-040：持久化 outbox 与重启恢复

- 状态：Planned
- 直接依赖：ISSUE-009～011、037
- 映射：FR-007、009、ADR-0004

### 目标

用事务型 manifest 保存每道 stored/pending/in-flight/ack/conflict 状态，使进程崩溃后仍能从 air rcscan 重发。

### 范围

- 评估并用 ADR/记录冻结 SQLite 等实现；schema migration/version。
- mission/trace/file reference/hash/status/retry/timestamps/error。
- `stored` 只能在 air HDF5 checkpoint 后创建；ack 幂等事务更新。
- startup reconcile 与 rcscan reader 校验，bounded dispatcher 通知可重建。

### 排除项

- 不保存 per-trace NPZ，不发 socket，不清理 air rcscan。

### 验收标准

- 任意事务点崩溃/重启后状态不倒退成假 ACK，也不丢 pending。
- manifest 与 air file 不一致 fail-closed/报告。
- 内存通知丢失后扫描可恢复发送。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-040。确认 ISSUE-009～011/037 完成；阅读 AGENTS.md、docs/issues/README.md、docs/TRANSPORT_PROTOCOL.md 第 7 节、ADR-0004、docs/DATA_FORMAT.md。

先记录 persistent outbox 的实现选择/事务语义，再实现 versioned transactional outbox（优先 SQLite，若改变需 ADR）：保存 mission/trace UID/index、air file reference、raw hash、stored/pending/in_flight/ack/conflict、retry/timestamps/error。只有 air rcscan checkpoint 成功后才能 insert stored；ACK 幂等更新；启动时与 reader reconcile。内存队列只是有界通知，丢失后可扫描重建。

禁止 per-trace NPZ、socket 发送或删除 air 文件。用进程/事务故障注入测试每个边界、重复 ACK、manifest/file 不一致、schema migration 和重启恢复。运行门禁并报告，停止，不 commit/push。
```

## ISSUE-041：trace 传输、地面持久 ACK、重复与冲突

- 状态：Planned
- 直接依赖：ISSUE-014、037、040
- 映射：FR-007～009、019

### 目标

完成单道 raw 从 outbox 到地面验证/writer/ACK 的可靠路径，并修复旧项目“文件已存在仍错误 ACK”的数据红线。

### 范围

- 从 air rcscan/当前 immutable sweep 构建 trace record，不重复派生数据。
- ground 验证任务/config/shape/hash/ID，追加 writer 并 flush。
- duplicate-same 直接回既有 ACK；同索引/UID不同 hash 保存冲突证据并拒绝。
- ACK 关联 mission/trace/hash/ground persistence result，更新 outbox。

### 排除项

- 不实现 inventory 补传/mission 结束，不在 UI 解码 payload。

### 验收标准

- 地面磁盘/flush 失败绝不 ACK 成功。
- ACK 丢失导致重发不会重复写；冲突不覆盖旧数据。
- ground 物理记录可乱序，逻辑 identity 保持。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-041。确认 ISSUE-014/037/040 完成；阅读 AGENTS.md、docs/issues/README.md、docs/TRANSPORT_PROTOCOL.md 第 5/6 节、docs/DATA_FORMAT.md、ADR-0004。

实现 trace sender/ground ingest/TracePersistedAck 路径：从 air rcscan 或当前不可变 sweep 发送一次规范 raw+metadata，ground 在非 UI worker 验证 mission/config/shape/ID/raw hash，append+flush ground writer 后才 ACK 并事务更新 outbox。重复且 hash 一致不重复写可 ACK；同 index/UID 不同 hash 必须保存诊断证据、fail-closed、不覆盖、不成功 ACK。磁盘/flush 失败绝不 ACK。

不要发送 display/time/NPZ，不做 inventory/结束/UI。故障注入覆盖 ACK 丢失、重复、乱序、坏 hash、错误任务、地面磁盘失败和历史数据冲突。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-042：断线重连、inventory 对账与缺道补传

- 状态：Planned
- 直接依赖：ISSUE-014、038、040、041
- 映射：FR-009、019

### 目标

在 TCP 重连后分页核对 ground/air inventory，按缺道请求从 air `.rcscan` 重发，并持续暴露冲突/积压。

### 范围

- inventory summary/page/range、missing request/response、对账 session ID。
- bounded page/window、重试/去重、任务身份和 config digest。
- 从 air finalized/partial 的 committed records 随机读取补传。
- 冲突阻止完全同步，网络再断可续对账。

### 排除项

- 不清理空中副本、不 finalize mission、不做多空中端。

### 验收标准

- 10 万道不发送巨大单消息或全驻内存。
- 多次断线后最终补齐；已 ACK 道不无限重发。
- 同 index 不同 hash 进入 conflict，不被 missing 逻辑覆盖。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-042。确认 ISSUE-014/038/040/041 完成；阅读 AGENTS.md、docs/issues/README.md、docs/TRANSPORT_PROTOCOL.md 第 8 节、docs/PERFORMANCE.md。

实现重连 inventory reconciliation：按 mission/config digest 建立对账 session，分页/区间交换 trace index/UID/hash 摘要，ground 发送 missing_request，air 从 rcscan committed records 重发并复用 ISSUE-041 ACK。页面/window 有界，支持对账中再次断线续跑；conflict 独立保留并阻止 sync。不得一次构造十万条巨大消息或依赖无界内存。

不要清理 air 文件、finalize mission 或支持多设备。测试多轮断线、缺道/额外/重复/冲突、乱序、页面重放、十万道内存和已 ACK 不无限重发。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-043：任务 manifest、结束状态与 sync-complete

- 状态：Planned
- 直接依赖：ISSUE-014、039、042
- 映射：FR-009、010、019

### 目标

把正常完成、用户停止、故障停止和恢复任务的最终 manifest 与空地完全同步状态明确化。

### 范围

- air mission manifest：终态、trace count/index ranges/hash summary、file ID/config digest、错误。
- ground 对账结果、missing/conflict、ground file ID 和 sync complete。
- end 命令状态与 data sync 状态分离；幂等重放。
- 清理资格只是显式状态，不自动删除 air 副本。

### 排除项

- 不实现 air runner/UI/实际清理，不声称整个 HDF5 hash 相同。

### 验收标准

- 四类终态可区分；manifest 缺道/冲突不能 sync complete。
- 重连/重复 manifest 结果稳定。
- sync complete 只在逐道一致性通过后产生。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-043。确认 ISSUE-014/039/042 完成；阅读 AGENTS.md、docs/issues/README.md、docs/TRANSPORT_PROTOCOL.md、docs/DATA_FORMAT.md 第 6 节。

实现 MissionManifest/ground reconciliation result/sync-complete 状态：区分 completed/stopped_by_user/failed/recovered，记录 mission/config/file IDs、trace inventory/hash 摘要和错误；ground 只有在逐道 raw/axis/channel/GNSS 对账通过且无 missing/conflict 后产生 sync_complete。命令执行完成与数据同步完成分离，消息幂等可重放。清理资格只输出显式状态，不删除 air 副本。

不要比较整 HDF5 hash、实现 runner/UI 或实际清理。测试四类终态、缺道、冲突、重复/乱序 manifest 和重连。运行门禁，报告并停止，不 commit/push。
```
