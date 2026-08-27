# ISSUE-008 基线确认单

日期：2026-08-27
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-next-ready`（执行器 engineer）
性质：只读核查产出；不含任何实现代码；未 commit、未 push。
配套文件：实施计划见 [docs/plans/2026-08-27-issue-008-schema-codec.md](../plans/2026-08-27-issue-008-schema-codec.md)（范围/编码预案/测试矩阵细节，本确认单为其权威基线件）。

## 1. 锁定的下一个 Ready Issue

**ISSUE-008：冻结 `.rcscan` v2 物理 schema 与 codec**

| 项 | 结论 | 证据 |
|---|---|---|
| Issue 目录 | [docs/issues/M02_STORAGE.md](../issues/M02_STORAGE.md) 第一个条目 | 依赖顺序主表 [docs/issues/README.md](../issues/README.md) |
| 直接依赖 | ISSUE-004～007 | M02_STORAGE.md「直接依赖」字段 |
| 依赖状态 | 全部已合入 `main` 并通过本地门禁（详见第 3 节） | Git 历史 + 实际测试复跑 |
| 执行前状态 | Planned → 本次会话进入 In progress；一次只执行一个 Issue | docs/issues/README.md 第 2 节 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

两个参考项目 `E:\钢筋仪软件开发` 与 `E:\UVA_GPR_system` 不在本机挂载范围内；
ISSUE-008 属纯 schema 契约冻结任务，无参考迁移需求，不涉及对它们的任何读取或修改。

## 3. 只读核查证据

### 3.1 Git 基线

```text
branch      main（后续修复切换至 feat/issue-008 独立分支，main 保持干净）
HEAD        0ddbd81 Merge pull request #1 from z2362536803/feat/m01-issues-006-007
工作树      clean（检查时唯一未跟踪项为团队运行时目录 .agent-teams/，
            非项目内容、不入库；另有本次会话新增的配套计划文档与
            docs/reports/ISSUE_008_BASELINE_CONFIRMATION.md 本身）
历史        reflog 显示一次 `reset: moving to origin/main`（只能证明 reset 操作，
            不得推断 --hard；未见 rebase/force-push 记录）
```

### 3.2 依赖 Issue 逐项核对（实际代码与测试证据）

基线 commit `main` @ `0ddbd81`：

| 依赖 | main 中实际交付物 | 测试证据 | 对应提交 |
|---|---|---|---|
| ISSUE-004 不可变通道与频域数据模型 | `src/uav_gpr/core/channels.py`、`frequency.py`：bytes 后备不可变数组；`channel × frequency` / `trace × channel × frequency` 固定 shape | `tests/unit/test_core_frequency.py` 等 | `45c5657` |
| ISSUE-005 GNSS、道元数据与质量状态模型 | `core/gnss.py`、`core/metadata.py`、`core/enums.py`：GnssFix/GnssMatch 结构化缺失语义；TraceMetadata 全字段含首道间隔豁免 | `test_core_gnss.py`、`test_core_metadata.py` | `952883e`、修复 `b11e741` |
| ISSUE-006 MissionConfig、时窗推导与配置摘要 | `core/config.py`：冻结配置值对象、canonical JSON、`config_sha256`、schema/protocol 版本 fail-closed | `test_core_config.py` | `22b0b0f`、修复 `bcef87c` |
| ISSUE-007 处理历史与时域数据模型 | `core/time_domain.py`：ProcessingRecord/History/TimeDomainScan、域转换与 provenance fail-closed | `test_core_time_domain.py` | `6403e42`、修复 `633b762` |

接口挂钩点抽查结论（后续实现只读引用，不改 core）：

- `MissionConfig.to_canonical_json()` / `.config_sha256` → `mission/config_json` 文本与
  `mission attrs.config_sha256` 的规范来源；
- `ChannelSpec` 五元组 → `channels/definitions_json` 序列化；
- `TraceMetadata.to_dict()/from_dict()` 已覆盖 trace_metadata 组与 gnss 组全部标量字段；
- `MonotonicNs.ns` 为非负 int64 可存整型数据集；UTC 表示（ISO-8601 微秒字符串 vs int64 UTC ns）
  待实现契约测试固定；
- 枚举均以稳定小写字符串持久化（`.value`），不落序号；
- `raw_trace_sha256` 字段契约为 64 位小写 hex 字符串；哈希算法本身由 ISSUE-009 实现。

### 3.3 门禁基线（核查时复跑）

```text
$ python tools/quality/verify.py        # scripts\verify.ps1 的等价入口
[quality] ok: pytest (non-hardware)     # 242 passed, 1 skipped（hardware 双重 opt-in sentinel）
[quality] ok: ruff
[quality] ok: mypy                      # strict, 28 files
[quality] ok: package import            # uav_gpr 及 core/positioning/storage 子包
all gates passed
```

环境说明（记录差异，非项目缺陷）：本机执行环境为 WSL Ubuntu 24.04 Python 3.12.3，
系统无 pip/ensurepip/sudo，Windows `py`/.venv 形态不可用。测试栈（numpy 2.5.2、h5py 3.16.0、
pytest 8.4.2、ruff、mypy）经官方 get-pip 引导安装至用户站点 `~/.local`，`uav_gpr` 以
`pip install -e . --no-deps` 提供——全部位于工作区之外或被 .gitignore 覆盖，Git 工作树不受影响。
ISSUE-008 所需 h5py/numpy 即来自此栈，pyproject 内既有依赖、无需新增。

## 4. 范围确认（M02_STORAGE.md 摘录）

**目标**：把 DATA_FORMAT.md 的逻辑结构落实为精确 HDF5 dtype、shape、缺失值、属性和 schema codec 契约。

**范围**：
- 根/mission/channels/axes/frequency/trace_metadata/gnss/acquisition/transport/checkpoint 物理 schema；
- air/ground role 差异、定长/变长字符串策略、JSON/complex/time 编码；
- trace-major 可扩展数据集、chunk/compression 默认值、严格 schema version 探测；
- 小型 schema 黄金文件/manifest；未知 major/profile 拒绝。

**排除项**：业务增量 writer、恢复（010/012）、v1 兼容迁移（013）、空地一致性服务（014）、处理算法、transport 网络、UI；范围外重构；两个参考项目。

**验收标准**：
1. schema 创建后 HDF5 结构/dtype 与契约完全对拍；
2. 缺失 GNSS/时间有有效位或固定哨兵，不靠猜 NaN 原因；
3. 不支持版本 fail-closed，air/ground 所需组明确。

## 5. 冲突与风险

- 无设计冲突：DATA_FORMAT.md 明示「最终实现可在不改变语义的前提下选择 HDF5 属性、定长字符串或列式数据集」，物理编码选择空间合法；具体预案与依据见配套计划文档第 6 节。
- 环境风险：Windows 一键脚本 `scripts\verify.ps1` 在本机不可运行（无 .venv）；以等价入口 `tools/quality/verify.py` 覆盖同一门禁序列，已在第 3.3 节留证。
- 未完成事项：无（本确认单范围内）。实现阶段（t2）尚未开始，由调度器按 t2 契约派发。

## 6. 结论

ISSUE-008 具备开工条件：依赖完整合入且门禁全绿、工作树干净、范围/排除项/验收标准明确。
本文档即为交付基线记录；下一步进入「先写失败契约测试，再最小实现」的实现阶段，
完成后停止并交独立审查，不自动进入下一 Issue。
