# ISSUE-009 基线确认单

日期：2026-08-28
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-009-raw-hash`（执行器 engineer）
性质：只读核查产出；不含任何实现代码；未 commit、未 push。
配套文件：实施计划见 [docs/plans/2026-08-28-issue-009-raw-hash.md](../plans/2026-08-28-issue-009-raw-hash.md)（范围/编码预案/测试矩阵细节，本确认单为其权威基线件）。

## 1. 锁定的下一个 Ready Issue

**ISSUE-009：规范逐道 raw 哈希与黄金向量**

| 项 | 结论 | 证据 |
|---|---|---|
| Issue 目录 | [docs/issues/M02_STORAGE.md](../issues/M02_STORAGE.md) 第二个条目 | 依赖顺序主表 [docs/issues/README.md](../issues/README.md) |
| 直接依赖 | ISSUE-004～006 | M02_STORAGE.md「直接依赖」字段 |
| 依赖状态 | 全部已合入 `main` 并通过本地门禁（详见第 3 节）；ISSUE-008 亦已合入（不依赖但提供 hash 存储位宽契约） | Git 历史 + 实际测试复跑 |
| 执行前状态 | Planned → 本次会话进入 In progress；一次只执行一个 Issue | docs/issues/README.md 第 2 节 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

两个参考项目 `E:\钢筋仪软件开发` 与 `E:\UVA_GPR_system` 不在本机挂载范围内；
ISSUE-009 属纯 core/storage 契约冻结任务，无参考迁移需求，不涉及对它们的任何读取或修改。

## 3. 只读核查证据

### 3.1 Git 基线

```text
branch      main（当前基线；实现阶段按协议切换至独立分支 feat/issue-009，main 保持干净）
HEAD        e852508 Merge feat/issue-008: ISSUE-008 .rcscan v2 schema and codec
工作树      clean（检查时唯一未跟踪项为团队运行时目录 .agent-teams/，非项目内容、不入库；
            另有本次会话新增的配套计划文档与
            docs/reports/ISSUE_009_BASELINE_CONFIRMATION.md 本身）
历史        reflog 未见本次会话产生 rebase/force-push 记录（本地只读核查）
```

### 3.2 依赖 Issue 逐项核对（实际代码与测试证据）

基线 commit `main` @ `e852508`：

| 依赖 | main 中实际交付物 | 测试证据 | 对应提交 |
|---|---|---|---|
| ISSUE-004 不可变通道与频域数据模型 | `src/uav_gpr/core/channels.py`（ChannelSpec 五元组、稳定 channel_id）、`frequency.py`（bytes 后备不可变数组、`channel × frequency` / `trace × channel × frequency` 固定 shape） | `tests/unit/test_core_frequency.py` 等 | `45c5657` |
| ISSUE-005 GNSS、道元数据与质量状态模型 | `core/gnss.py`、`core/metadata.py`、`core/enums.py`：GnssFix/GnssMatch 结构化缺失语义；TraceMetadata 全字段含 `raw_trace_sha256` 64 位小写 hex 字段契约 | `test_core_gnss.py`、`test_core_metadata.py` | `952883e`、修复 `b11e741` |
| ISSUE-006 MissionConfig、时窗推导与配置摘要 | `core/config.py`：冻结配置值对象、canonical JSON、`config_sha256`、schema/protocol 版本 fail-closed | `test_core_config.py` | `22b0b0f`、修复 `bcef87c` |
| ISSUE-008（已合入，非依赖但提供存储契约） | `src/uav_gpr/storage/rcscan_v2.py`：`/trace_metadata/raw_trace_sha256` 固定 64 字节 ASCII 列（`rcscan_v2.py:304`）、`trace_metadata_to_cells()` 把 `raw_trace_sha256` 写入该列（`rcscan_v2.py:679-681`） | `tests/contract/test_storage_schema.py`、`rcscan_v2_golden.json` | `496f6cd`、`ccbdfbf` |

接口挂钩点抽查结论（后续实现只读引用，不改 core）：

- `MissionId.to_json()` / `TraceUid.to_json()` → 规范小写 UUID 字符串（36 字符），可直接 UTF-8 编码；
- `ChannelSpec.channel_id` 为 `^[A-Za-z0-9_]+$` 稳定标识 → 有序 channel IDs 的规范文本来源；
- `FrequencySweep.frequencies_hz`（float64 只读数组）与 `.data`（complex128 只读数组，`channel × frequency`）→ 哈希输入；数组不可写，哈希只读不修改；
- `TraceMetadata.raw_trace_sha256` 字段契约（64 位小写 hex）与 `with_integrity()` 挂钩点 → 哈希计算结果的落点；本 Issue 产出哈希字符串，与 008 存储列（64 ASCII）直接兼容；
- `MissionConfig.to_canonical_json()` / `config_sha256` → 与逐道 hash 并列的配置摘要，不进入 raw hash（raw hash 只含 identity/axis/channel/raw 五要素）。

### 3.3 门禁基线（核查时复跑）

```text
$ python3 tools/quality/verify.py
[quality] ok: pytest (non-hardware)     # 301 passed, 1 deselected（hardware 双重 opt-in sentinel）
[quality] ok: ruff
[quality] ok: mypy                      # strict, 29 files
[quality] ok: package import            # uav_gpr 及 core/storage 子包
[quality] all gates passed
```

环境说明（记录差异，非项目缺陷）：本机执行环境为 WSL Ubuntu 24.04 Python 3.12.3，
系统无 pip/ensurepip/sudo，Windows `py`/.venv 形态不可用。测试栈（numpy 2.5.2、h5py 3.16.0、
pytest 8.4.2、ruff、mypy）经官方 get-pip 引导安装至用户站点 `~/.local`，`uav_gpr` 以
`pip install -e . --no-deps` 提供——全部位于工作区之外或被 .gitignore 覆盖，Git 工作树不受影响。
ISSUE-009 仅需 numpy/hashlib（标准库），无需新增依赖。

## 4. 范围确认（M02_STORAGE.md 摘录）

**目标**：冻结无歧义、跨空地一致的 `raw_trace_sha256` framing 与实现。

**范围**：
- 哈希版本、长度前缀/字段 framing、ID、通道、有序频率轴和 C-order little-endian complex128；
- 输入规范化但不修改领域数组；
- 合成黄金向量（含 expected digest）和 hash 元数据校验；
- 明确 GNSS 不进入 raw hash。

**排除项**：HDF5 写入（008/010）、整文件 hash 比较（014 明示禁止）、transport（037+）、
v1 迁移、处理算法、UI；范围外重构；两个参考项目。

**验收标准**：
1. 等价内存布局/本机字节序得到相同 digest；任一身份/axis/channel/raw 改变会变化；
2. 简单拼接歧义被长度 framing 消除；
3. 非规范 shape/dtype/ID fail-closed。

## 5. 冲突与风险

- 无设计冲突：DATA_FORMAT.md 第 5 节明示「哈希函数的确切 framing 必须在实现前写成契约样本」，
  本 Issue 即冻结该 framing；raw hash 五要素（version/identity/channel IDs/频率轴/raw 复数数组）
  与 DATA_FORMAT 5.1-5.5 完全一致，GNSS 不进入（DATA_FORMAT 第 5 节末尾明示）。
- 依赖 Issue 008 已合入且其 `raw_trace_sha256` 列契约（64 ASCII）与本次输出的 64 位小写 hex 兼容；
  本次不修改 008 任何代码，只在 DATA_FORMAT 第 5 节补精确 framing 文档（文档更新属本 Issue 范围）。
- 环境风险：Windows 一键脚本 `scripts\verify.ps1` 在本机不可运行（无 .venv）；以等价入口
  `tools/quality/verify.py` 覆盖同一门禁序列，已在第 3.3 节留证。
- 未完成事项：无（本确认单范围内）。实现阶段（后续任务）尚未开始，由调度器按契约派发。

## 6. 结论

ISSUE-009 具备开工条件：依赖完整合入且门禁全绿、工作树干净、范围/排除项/验收标准明确。
本文档即为交付基线记录；下一步进入「先写失败契约测试，再最小实现」的实现阶段，
完成后停止并交独立审查，不自动进入下一 Issue。
