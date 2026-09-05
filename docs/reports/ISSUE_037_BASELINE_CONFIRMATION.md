# ISSUE-037 开工基线确认单（自动化轮 · engineer）

## 1. 目标 Issue 与依据

- **Issue**：ISSUE-037「协议 ADR、消息模型与二进制 framing」（`docs/issues/M07_TRANSPORT.md` L5-40）
- **状态行核查**：M07 L7 ISSUE-037 状态 = `Planned`；映射 FR-001、002、008、009；直接依赖声明 = ISSUE-003、005、006、009。
- **工作区根替换声明**：Issue 提示词中 `E:\wurenjiruanjian` 一律替换为当前工作区根 `/mnt/d/博士任务/无人机软件/UAV-GPR`（Windows：`D:\博士任务\无人机软件\UAV-GPR`）。
- **流程依据**：`AGENTS.md`、`docs/issues/README.md` §1（依赖是实际开工门禁、编号为主执行顺序）、§3 通用执行协议（本会话只执行 ISSUE-037，不 commit/push）、`docs/ISSUE_REVIEW_STANDARD.md`（t1 基线 → t2 实现 → t3 独立复审）。
- **契约文档**：已读 `docs/TRANSPORT_PROTOCOL.md`（全 126 行，重点 §3 版本协商 / §5 Trace 语义 / §9 长度上限 / §11 候选 framing / §12 故障注入验收）、`docs/adr/README.md`（ADR 格式与验收要求）。M07 L3 明确：**本里程碑重新实现协议，不迁移旧 UAV-GPR 的 RemoteSender/Receiver 或 NPZ payload**。

## 2. 只读核查证据（全部实测）

| 项 | 结果 |
|---|---|
| HEAD / 分支 | `main @ dc5d697`（`Merge feat/issue-036: ISSUE-036 processing orchestration (M06 milestone complete)`）；核查前 `git status --porcelain` 为空（工作树干净，除本单外无输出）；`git diff --check` 干净 |
| 远端同步 | `git rev-list --left-right --count origin/main...HEAD` = `0 0` —— 本地 main 与 origin/main 完全同步，036 合并链推送已闭合 |
| ISSUE-003 Done 证据（实质） | M01 L81 状态行为初始值 `Planned`（**M01 各条目状态行未随实际进度更新**，沿用 ISSUE_030 基线单 §依赖证据 的既有口径）；实际完成证据：`docs/reports/ISSUE_001_005_REVIEW_SUMMARY.md` 表格 ISSUE-003 = `PASS`（原始提交 `ac531fc` + 复审修复 `5fd6013` 均在 HEAD 祖先，`git merge-base --is-ancestor` 实测通过）；tracked 交付物 `src/uav_gpr/core/identifiers.py` / `enums.py` / `errors.py` / `timeutil.py` + `tests/unit/test_core_identifiers.py`(6) / `test_core_enums.py`(4) / `test_core_errors.py`(11) / `test_core_time.py`(9) ✓ |
| ISSUE-005 Done 证据（实质） | 同上报告 ISSUE-005 = `PASS`（`952883e` + `b11e741`，均在 HEAD 历史）；tracked `src/uav_gpr/core/gnss.py` / `metadata.py` + `tests/unit/test_core_gnss.py`(18) / `test_core_metadata.py`(29) ✓；`TraceMetadata.raw_trace_sha256` 64 位小写 hex 强契约已在 metadata.py L68 落地 |
| ISSUE-006 Done 证据（实质） | `22b0b0f`（feat(core): mission configuration contracts）经 PR #1 `0ddbd81` 合入 + 复审修复 `bcef87c` 在 HEAD 历史（merge-base 实测）；ISSUE_016 基线单 §依赖状态 已认定其合入 main；tracked `src/uav_gpr/core/config.py` + `tests/unit/test_core_config.py`(45) ✓；`SUPPORTED_PROTOCOL_VERSION = "1"`（config.py L95）与 canonical JSON + SHA256 digest 契约可直接被 protocol header 引用 |
| ISSUE-009 Done 证据（形式+实质） | M02 L44 状态 = `Done（2026-08-28 独立复审 PASS WITH CONDITIONS 后经授权合并，见 docs/reports/ISSUE_009_REVIEW_REPORT_R3.md）`；feature `ee41360` + merge `c10693f` + Done 标记 `aab502c` 均在 HEAD 历史；tracked `src/uav_gpr/core/raw_hash.py`（`RAW_HASH_MAGIC="UAVGPR-RAW-SHA256"`、versioned framing、`_u64` 大端编码先例）+ `tests/contract/test_raw_trace_hash.py`(75) ✓ |
| transport 包 / 协议实现现状 | `src/uav_gpr/transport/` 仅有 docstring 占位 `__init__.py`（73 字节），零实现文件；全仓 `src/uav_gpr/` 无任何 protocol envelope/framing/magic 实现（`magic` 仅命中 `core/raw_hash.py` 的哈希域分隔常量，与线上协议无关）；`config.py` L46 明示「transport itself is not implemented yet」→ **ISSUE-037 为唯一待建能力，无重复实现风险** |
| 下一可执行 Issue | M02～M06 五个里程碑文件 `状态：Planned` 计数均为 0（grep 实测，M06 已随 036 闭合）；M07 L7 ISSUE-037 是全局首个 `Planned` 且四项直接依赖实质 Done → **ISSUE-037 为下一可执行 Issue** |
| docs/adr/ 编号现状 | 已有 `0001`～`0005` 五份 Accepted + README；`docs/adr/README.md` L15：「尚未冻结的选择（例如…**协议二进制 codec**…）应先建立 Proposed ADR，不得在临时代码中隐式决定」→ **下一 ADR 编号 = 0006**（protocol framing ADR），与 TRANSPORT_PROTOCOL §11「在 ADR 接受前不得让临时 framing 成为事实标准」呼应 |

### 关键本项目契约文件哈希（SHA-256 前 16 位，供 t3 交叉核对）

```text
8d2844e325e0ad46  docs/TRANSPORT_PROTOCOL.md
ff66df939f2217a5  docs/adr/README.md
49a18bb5a4805e56  docs/issues/M07_TRANSPORT.md
852b5f4b6b03d14e  src/uav_gpr/core/raw_hash.py          (ISSUE-009 规范 raw hash，trace payload 引用锚点)
093e7661a91d8eec  src/uav_gpr/core/config.py            (ISSUE-006 MissionConfig canonical digest + SUPPORTED_PROTOCOL_VERSION="1")
9bc83d908b39bd3a  src/uav_gpr/core/identifiers.py       (MissionId/TraceUid 非 canonical 拒绝)
ee516fd463e7660d  src/uav_gpr/core/metadata.py          (TraceMetadata raw_trace_sha256 契约)
3ec52a71b1363509  src/uav_gpr/core/gnss.py
98f49e5d9018815f  src/uav_gpr/core/errors.py            (ErrorCode 结构化错误)
4e906f159b1c8599  src/uav_gpr/core/enums.py
4c1f07580f9e7d9e  src/uav_gpr/core/timeutil.py          (Clock 注入，确定性测试)
386c8bdf21fdf2c3  src/uav_gpr/core/frequency.py         (FrequencySweep/FrequencyScan shape 契约)
8436f7270cba5eda  src/uav_gpr/core/channels.py          (ChannelSpec 多通道绑定)
5eb98be41cc95100  tools/quality/verify.py
```

（参考源口径：M07 L3 声明本里程碑**重新实现**协议——旧 UAV-GPR（`E:\UVA_GPR_system`）仅作否定参考，不迁移 `RemoteSender`/`Receiver`/NPZ 线上格式（ADR-0005 与 AGENTS.md §2.2 禁止项）；钢筋仪项目亦无线上协议可迁。t2 无参考源哈希对拍要求。）

## 3. 契约要点（对 t2 实现有约束）

### 3.1 TRANSPORT_PROTOCOL.md：版本协商与 envelope

- §3：不兼容 **major 拒绝连接**；minor 能力通过协商启用；握手交换设备 ID、软件版本、协议范围、能力、当前任务和 outbox 摘要；`session_id/connection_generation` 每连接新建，`mission_id` 不随重连改变。
- §4 消息族表：hello/hello_ack/incompatible、heartbeat/air_status/error_report、command/command_status、mission_*、trace_record/trace_persisted_ack/trace_rejected、inventory_summary/missing_request/conflict_report/sync_complete。**ISSUE-037 范围是 8 类不可变消息模型**（hello/status/command/mission/trace/ack/inventory/error 的字段契约）；心跳/outbox/业务状态机属 038-041，本 Issue 只建模不驱动。
- §5：`trace_record` header 至少含 protocol/schema version、device_id/mission_id/trace_index/trace_uid、规范频率/通道配置摘要、dtype/shape/byte order/payload length、sweep 时间/间隔/连接代数、GNSS 匹配元数据、`raw_trace_sha256`；binary payload **只含一次规范原始频域数组**，频率轴/通道定义在任务握手中冻结、trace 引用 config digest（复用 ISSUE-006 `MissionConfig` digest 与 ISSUE-009 raw hash，不新造平行类型）。
- §2 非目标（红线）：不用 NPZ；不重复发送 raw/display/time trace 等可从规范 raw 重建的数据；内存队列不是可靠存储。
- §9：每个 frame 的 header/payload 长度有**严格上限，解析前先验证**，防内存分配攻击。
- §11 候选 framing（待本 ADR 冻结）：固定大端前缀 = magic、protocol major/minor、message type、flags、JSON header length、binary payload length，后接 UTF-8 header 与 payload；必须：有 magic 和严格上限、增量处理粘包/拆包、header 版本化、**不依赖 Python pickle**、同一契约生成**跨进程黄金帧样本**。
- §12：任意字节边界拆包、合包、截断、超长长度为强制验收面。

### 3.2 adr/README.md：ADR 格式与验收

- 状态用 `Proposed / Accepted / Superseded / Rejected`；已接受 ADR 不能直接改写结论，变更需新增 ADR 标明替代关系。
- 结构模板（0001～0005 先例）：标题 + `- 状态：` + `- 日期：` + `## 背景` / `## 决策` / `## 后果`。
- AGENTS.md §12：任何改变强制数据规则、空地职责或持久化语义的方案必须先写 ADR → protocol framing ADR（0006）必须在 codec 实现前落盘并以测试证据支撑（Issue 提示词：「先写并用测试证据完成 protocol framing ADR」）。

### 3.3 可复用既有资产（t2 不新建平行类型）

- `core/identifiers.py`：MissionId/TraceUid/DeviceId 非 canonical 即拒（header 字段直接引用类型）。
- `core/raw_hash.py`：64 位小写 hex 校验正则、`_u64` 大端定长编码与「magic+version+canonical text frames」的确定性序列化先例（framing 风格对齐但不共用哈希域——域分离）。
- `core/config.py`：canonical JSON + SHA256 digest 先例（header canonical UTF-8 序列化可仿照其 sort_keys/separators 口径）与 `SUPPORTED_PROTOCOL_VERSION="1"`。
- `core/errors.py`：`ErrorCode`/`DomainError`（协议解析错误走结构化错误，fail-closed）。
- `core/frequency.py`/`channels.py`：单道 `channel × frequency` shape 与 dtype 显式绑定（payload 校验依据）。

## 4. 门禁基线（verify.py 复跑，实测）

- 命令：`.venv/Scripts/python.exe tools/quality/verify.py`（gate 顺序：pytest `-m "not hardware and not slow"` → `ruff check .` → `mypy src` → package import）。输出重定向至仓库外 `/tmp/verify_037_t1.log`，仓库内零遗留产物。
- pytest (non-hardware)：**1449 passed / 4 deselected in 273.40s** ✓，与团队计划口径 1449 逐字一致（= 036 复审后的主干数字）
- ruff：`All checks passed!` ✓
- mypy：`Success: no issues found in 56 source files` ✓（与计划口径 56 一致）
- package import ok；`[quality] all gates passed`，exit 0
- 核查前后 `git status --porcelain` 均为空（本单为唯一新增未跟踪文件）；`git diff --check` 干净
- 已知挂账（非阻断，登记于 ISSUE_036_REVIEW_REPORT §6.3）：`tests/contract/test_librevna_backend.py::test_close_interrupts_acquire` 存在 ~25% flake 率（既有用例、与本 Issue 无关）；若全量首跑遇该单项失败，复跑即绿，不作为基线异常。

## 5. 实施计划摘要（供 t2）

**范围**（inScope 精确 5 路径，以 t2 任务契约原文为准，预期形如）：
1. `docs/adr/0006-protocol-v1-binary-framing.md`（Proposed→Accepted 的 framing ADR：候选、选择、后果——含 magic 字节值、major/minor 语义、type 枚举编码、flags 位义、header canonical UTF-8 规则（sort_keys、separators、禁 NaN/Infinity、键序）、payload length 上限数值、trace payload 规范 raw 数组形态（float64 LE、shape 由 config digest 冻结）、ACK/inventory/error 字段契约）
2. `src/uav_gpr/transport/<protocol 模块>`（不可变消息模型 + envelope + 增量 codec/FrameParser + golden bytes 常量）
3. `tests/contract/<protocol 测试>`（下述矩阵）
4. `docs/plans/2026-09-05-issue-037-protocol-framing.md`（计划文档，先落盘）
5. `docs/issues/M07_TRANSPORT.md`（L7 状态行 Planned → Review/Done 按流程）

**排除**（M07 Issue 明确）：不打开 socket、不实现 heartbeat/outbox/业务状态机；不做分块传输（§5 「大 payload 是否分块由基准决定」——首版单 frame + 上限，ADR 记录理由）；不引入第三方依赖；不改 core/storage 既有文件。

**测试矩阵**（失败测试优先，先红后绿）：
1. **任意 chunk 切分**：把黄金帧流按 1..N 所有字节边界（抽样 + 全量小区间）喂 parser ⇒ 重组结果恒等；
2. **粘包**：多帧拼接一次投递 ⇒ 依序产出且不串扰；半帧前缀 ⇒ 零产出、零副作用；
3. **截断**：头中途 EOF、payload 中途 EOF ⇒ 保持等待态不误判完成、报错路径可恢复；
4. **恶意长度**：header length / payload length 超上限、合法前缀+巨大长度 ⇒ 在读 payload 前拒绝，不无界分配（断言峰值缓冲受上限约束）；坏 magic ⇒ fail-closed；
5. **未知 type/version**：未知 message type、major 不兼容 ⇒ 拒绝（`incompatible` 语义建模）；unknown minor ⇒ 按能力策略处理（ADR 定策：接收侧容忍 vs 拒绝）；
6. **非 canonical header**：键乱序、多余空白、非 UTF-8、NaN/Infinity、重复键 ⇒ encode 产 canonical、decode 对非 canonical 输入拒绝或重规范化（ADR 二选一并钉死）；
7. **黄金帧**：固化 golden bytes（hex 常量入测试 + ADR 附录），跨进程确定性 = 子进程（subprocess 起独立解释器 encode）与主进程字节逐等；hash 域与协议 framing 域分离（不触碰 RAW_HASH_MAGIC 域）；
8. **字段契约**：8 类消息（hello/status/command/mission/trace/ACK/inventory/error）各自必填/可选/类型/不可变性（frozen dataclass + 序列往返恒等）；trace header 引用 ISSUE-009 hash 与 ISSUE-006 config digest，禁 pickle/NPZ/display-time 派生数据入 payload（负面测试：pickle payload 拒绝）。

**门禁**：定向测试（先红后绿）→ core 依赖定向回归 → `verify.py` 全量（基线 1449 passed + 新增用例数；mypy 56→57+ 源文件为预期变化）→ ruff + mypy + import + `git diff --check`；`changedPaths` 与 t2 契约 inScope 5 路径逐一相等；登记 acceptanceResults criterion 与契约逐字一致、payload 精简；完成后停止，不 commit/push。

## 6. 结论

四项直接依赖均有 tracked 代码 + tracked 契约测试 + HEAD 祖先中的 feature/fix/merge 提交实证（003/005/006 为 M01 状态行漏更新的既有口径，实质 PASS 证据链完整；009 另有 M02 `Done` 状态行 + R3 复审报告 + 标记提交三重形式证据）；`origin/main...HEAD = 0 0`，工作树干净。M02～M06 无剩余 Planned，**ISSUE-037 是下一可执行 Issue**；`src/uav_gpr/transport/` 为零实现占位，无重复建设风险。TRANSPORT_PROTOCOL §3/§5/§9/§11/§12 与 adr/README 约束已逐条落到本报告 §3；下一 ADR 编号锁定 **0006**（protocol v1 binary framing），M07 L3 否定参考口径（不迁移 RemoteSender/NPZ）已登记。门禁基线 **1449 passed / 4 deselected（273.40s）、ruff clean、mypy 56 source files clean、package import ok、all gates passed exit 0** 全部实测复现，核查前后 git 状态一致、无遗留产物。**ISSUE-037 可以开工（Ready）**，t2 按 §3 契约、§5 计划与 t2 任务锁定的 inScope 5 路径执行。
