# ISSUE-008 基线确认单与实施计划

日期：2026-08-27
状态：基线已确认；等待按本文件进入实现
执行会话：uav-gpr-issue-next-ready / engineer

## 1. 目标 Issue 锁定

依据 [docs/issues/README.md](../issues/README.md) 的依赖顺序与状态定义：

| 项 | 结论 | 证据 |
|---|---|---|
| 下一个 Ready Issue | **ISSUE-008：冻结 `.rcscan` v2 物理 schema 与 codec** | 目录顺序（001–007 先行）；[M02_STORAGE.md](../issues/M02_STORAGE.md) 第一个条目 |
| 直接依赖 | ISSUE-004～007 | M02_STORAGE.md「直接依赖」字段 |
| 依赖状态 | 全部已合入 `main` 并通过门禁 | 见第 3 节逐项核对 |
| 本 Issue 状态 | 在本文档之前为 Planned；本次会话置 In progress（仅一个执行者） | docs/issues/README.md 第 2 节状态定义 |

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：
`D:\博士任务\无人机软件\UAV-GPR`（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）。两个参考项目路径
`E:\钢筋仪软件开发`、`E:\UVA_GPR_system` 不在本机挂载范围内，且 ISSUE-008 无迁移需求，不触碰。

## 2. 已读文档清单（强制阅读完成）

AGENTS.md（完整）、CLAUDE.md、docs/INDEX.md、docs/DATA_MODEL.md、docs/DATA_FORMAT.md、
docs/ROADMAP.md、docs/issues/README.md、docs/issues/M02_STORAGE.md、
docs/adr/0002-rcscan-v2-dual-copies.md、docs/TESTING.md、docs/ISSUE_REVIEW_STANDARD.md、
docs/plans/README.md。

## 3. 依赖核对（实际代码与测试证据）

基线 commit：`main` @ `0ddbd81`（Merge pull request #1 feat/m01-issues-006-007）。

| 依赖 | 交付物（main 中实际存在） | 测试证据 | 相关提交 |
|---|---|---|---|
| 004 不可变通道与频域数据模型 | `src/uav_gpr/core/channels.py`、`frequency.py`（bytes 后备不可变数组、`channel × frequency` / `trace × channel × frequency`） | `tests/unit/test_core_frequency.py` 等 242 通过 | `45c5657` feat(core): add immutable frequency models |
| 005 GNSS、道元数据与质量状态模型 | `src/uav_gpr/core/gnss.py`、`metadata.py`、`enums.py`（GnssFix/GnssMatch、TraceMetadata 完整字段含首道间隔豁免） | `test_core_gnss.py`、`test_core_metadata.py` | `952883e` + `b11e741` |
| 006 MissionConfig、时窗推导与配置摘要 | `src/uav_gpr/core/config.py`（冻结配置、canonical JSON、`config_sha256`、版本 fail-closed） | `test_core_config.py` | `22b0b0f` + `bcef87c` |
| 007 处理历史与时域数据模型 | `src/uav_gpr/core/time_domain.py`（ProcessingRecord/History/TimeDomainScan，域转换 fail-closed） | `test_core_time_domain.py` | `6403e42` + `633b762` |

接口兼容性抽查结论（后续实现的挂钩点，均为只读引用）：

- `MissionConfig.to_canonical_json()` / `config_sha256` 可作为 `mission/config_json` 与
  `mission attrs.config_sha256` 的规范来源；
- `ChannelSpec` 五元组可序列化为 `channels/definitions_json`；
- `TraceMetadata.to_dict()/from_dict()` 已覆盖 trace_metadata 与 gnss 物理行的全部标量字段；
- `MonotonicNs.ns` 为非负 int，直接适配 int64 数据集；UTC 用 ISO-8601 微秒字符串或 UTC ns，
  二选一需在实现时以契约测试固定；
- `DataDomain` / `TimeDomainKind` / 各 StableStrEnum 以稳定小写字符串持久化；
- `raw_trace_sha256` 字段契约为 64 位小写 hex（具体哈希算法由 ISSUE-009 实现，schema 只固定存储位宽与格式）。

## 4. 工作树与环境检查

```text
branch           main（历史可证明事实：reflog 有一次 `reset: moving to origin/main`，
                只能证明 reset，不能推断 --hard；未见 rebase/force-push 记录。
                后续 ISSUE-008 实现迁移至 feat/issue-008 独立分支）
git status       clean；唯一未跟踪目录 .agent-teams/（团队运行时状态，非项目内容，不纳入提交）
用户修改         无
缓存/生成物      __pycache__、src/uav_gpr.egg-info 均已被 .gitignore 忽略
```

环境说明：本地执行环境为 WSL Ubuntu 24.04（Python 3.12.3），系统缺 pip/ensurepip 且无 sudo；
测试栈（numpy 2.5.2、h5py 3.16.0、pytest 8.4.2、ruff、mypy）经官方 get-pip 引导安装到用户站点，
并以 `-e . --no-deps` 可编辑方式提供 `uav_gpr` 包。上述动作全部位于 `~/.local` 与被忽略的
生成物内，Git 工作树保持干净。Windows `py`/`.venv`（scripts\verify.ps1 预期形态）在本机不可用，
属执行环境差异而非项目缺陷；等价入口 `python tools/quality/verify.py` 已验证全绿。

当前门禁基线（进入实现前复测）：

```text
python tools/quality/verify.py
  pytest (non-hardware) ok    242 passed, 1 skipped（hardware 双重 opt-in sentinel）
  ruff                   ok
  mypy                   ok（strict, 28 files）
  package import         ok
```

## 5. ISSUE-008 范围确认

### 5.1 目标（M02_STORAGE.md 原文摘录）

把 `DATA_FORMAT.md` 的逻辑结构落实为精确 HDF5 dtype、shape、缺失值、属性和 schema codec 契约。

### 5.2 范围（in scope）

- 根/mission/channels/axes/frequency/trace_metadata/gnss/acquisition/transport/checkpoint 的物理 schema；
- air/ground role 差异、定长/变长字符串策略、JSON/complex/time 编码；
- trace-major 可扩展数据集、chunk/compression 默认值和严格 schema version 探测；
- 小型 schema 黄金文件/manifest；未知 major/profile 拒绝。

### 5.3 排除项（out of scope）

- 业务增量 writer、恢复（ISSUE-010/012）、v1 兼容读取与迁移（ISSUE-013）、
  逐道一致性服务（ISSUE-014）；
- 处理算法、transport 网络、UI；
- 重构范围外模块；修改两个参考项目。

### 5.4 验收标准（M02_STORAGE.md 原文）

1. schema 创建后 HDF5 结构/dtype 与契约完全对拍；
2. 缺失 GNSS/时间有有效位或固定哨兵，不靠猜 NaN 原因；
3. 不支持版本 fail-closed，air/ground 所需组明确。

### 5.5 执行协议补充（docs/issues/README.md 第 3 节）

- 只使用现有依赖（h5py/numpy 已在 pyproject 内，无新增依赖）；
- 先写能失败的契约测试再写最小实现；
- 运行目标测试、相关回归、全部非硬件测试、Ruff 与 mypy；
- 默认不 commit、不 push；报告后停止，不自动进入下一 Issue。

## 6. 物理编码决策预案

以下选择是 DATA_FORMAT.md 明示留给实现固定的事项（“最终实现可在不改变语义的前提下选择…”），
实现时将以契约测试固化；若与逻辑设计语义冲突，先改 DATA_FORMAT/ADR 再动代码：

| 事项 | 预案 | 依据 |
|---|---|---|
| 字符串 | 定长 ASCII 用 HDF5 fixed string；变长 UTF-8（trace_uid、JSON 文本等）用 variable-length UTF-8 string dtype | trace_uid 是 UUID 36 字符可定长，JSON 天然变长 |
| JSON | 变长 UTF-8 string 存 canonical JSON 文本，配独立 codec 校验 | MissionConfig/definitions 已有 canonical 形式 |
| complex128/float64 | 小端（`<c16`/`<f8`）显式 dtype，C 序 | 空地一致性与 ISSUE-009 framing 对齐 |
| UTC 时间 | ISO-8601 微秒字符串（timeutil canonical）或 int64 UTC ns，二选一固定并测试 | DATA_MODEL 第 6 节要求显式时间；哨兵由缺失值策略给出 |
| 单调时钟/哨兵 | int64 ns；缺失哨兵采用 INT64_MIN 类固定值（避免与合法 0 冲突），伴随 valid 位 | DATA_FORMAT 第 7 节“专门有效位或整数哨兵” |
| 浮点缺失 | float64 NaN + 显式 valid/reason 列 | DATA_FORMAT 第 7 节 |
| chunk/compression | raw/calibrated：`(1, channel_count, frequency_count)`；compression 默认先 `None`，留基准数据后切换（记录在 codec 常量中） | DATA_FORMAT 第 3 节建议；压缩需 CPU/写盘基准后选型 |
| maxshape | trace-major 维度 `None`（可扩展），其余维度固定 | trace-major 可扩展数据集要求 |
| role 差异 | transport 组 air 必填四列、ground 同结构可选；其余组两端同构 | DATA_FORMAT 第 2/6 节 |
| 版本探测 | 根 attrs `format_name/schema_version/profile` 三键联合校验；未知 major/profile fail-closed |验收标准 3 |
| checkpoint | scalar/int64 数据集，committed_record_count 为权威提交边界 | DATA_FORMAT 第 2 节 checkpoints 组 |

## 7. 测试矩阵计划（tests/contract/storage_schema.py 等，名称以实现为准）

正常路径：黄金 schema 创建→结构/dtype 对拍断言；air 与 ground 两种 profile 创建。
缺失值：GNSS 无效行占位、无 GNSS 任务、哨兵 round-trip。
拒绝路径：未知 schema_version（major 与 minor 区分）、未知 profile、错误 format_name、
role 缺失/非法、channels 空、频率轴递增破坏、UTF-8 解码失败。
边界：0 道（仅骨架）、多通道 shape（1ch/2ch）、空 optional 组、字节序对拍。
回归：全量非硬件门禁 verify.py。

## 8. 回退方式

实现全部位于新增文件（storage 模块与 tests/contract），失败即整体删除新增文件即可回退；
不改任何既有 core 契约文件，如确需改动 DATA_FORMAT.md 则按 ADR 流程先行说明。

## 9. 交付物与停止点

- 新增：`src/uav_gpr/storage/` schema 常量/codec/创建器模块、对应契约测试、小型合成黄金 manifest；
- 报告固定含：实际改动、测试命令与结果、验收逐项对应、未完成/风险、工作树状态；
- 报告后停止交人工验收，不进入 ISSUE-009。
