# ISSUE-014 开工基线确认单（只读核查 + 实施计划）

日期：2026-08-30（ISSUE-014 开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-014-inventory`（执行器 engineer，任务 t1）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支、未修改实现与测试/计划文件。
配套文件：本单为 t2（实现空地 inventory 与逐道一致性服务）与 t3（独立复审）的权威基线件；独立复审按 [ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) 执行。t2 完整实施契约见 t2 计划文档（`docs/plans/2026-08-30-issue-014-inventory.md`，由 t2 先落盘）。

## 1. 锁定的目标 Issue 与依据

**ISSUE-014：空地 inventory 与逐道一致性服务**（`docs/issues/M02_STORAGE.md` 第 7 个条目，状态 `Planned`）

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-014（M02_STORAGE.md L227–261） | docs/issues/README.md 依赖顺序主表 L81 |
| 直接依赖 | ISSUE-009（规范逐道 raw 哈希与黄金向量）、ISSUE-011（reader/严格校验/逻辑排序）、ISSUE-012（partial 检查与非破坏恢复） | M02_STORAGE.md L230「直接依赖：ISSUE-009、011、012」；README.md L81 |
| 依赖状态 | 三者均已合入 `main` 且状态行 `Done`（见第 3 节 Git 与报告证据） | M02 L44/L118/L155；git log；git ls-files |
| 功能映射 | FR-009（断线重连自动对账补传、哈希冲突不得静默覆盖）、FR-019（空地副本/缺道/重复/哈希/完整性诊断） | M02 L231；PRODUCT_REQUIREMENTS.md L53/L63 |
| 提示词工作区替换 | `E:\wurenjiruanjian` → 工作区根（见第 2 节） | docs/issues/README.md 第 3 节通用协议 |
| 一次一 Issue | 本轮只处理 ISSUE-014；不进入 ISSUE-015 | docs/issues/README.md 第 1/3 节 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

参考项目 `E:\钢筋仪软件开发`、`E:\UVA_GPR_system` 的原始路径不在本机挂载范围内（WSL 仅挂载 C/D 盘）；本 Issue 为纯应用/存储一致性服务，不新增参考源依赖（沿用 ISSUE-001 manifest 与 ISSUE-009/011/012 已冻结契约）。

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      feat/issue-013（当前；工作树非干净，含 ISSUE-013 在制未提交产物，见 3.5）
HEAD        09037496435eea0e6842ccb71b920b743fafd217  docs(data-format): freeze ISSUE-012 non-destructive recovery contract (4.1)
分支关系    feat/issue-013 与 main 均指向 0903749（merge-base == HEAD）；main 相对 origin/main ahead 1（该提交未推送）
git status --porcelain=v1 -b
    ## feat/issue-013
     M docs/issues/M02_STORAGE.md
    ?? docs/plans/2026-08-30-issue-013-v1-migration.md
    ?? docs/reports/ISSUE_013_BASELINE_CONFIRMATION.md
    ?? docs/reports/ISSUE_013_REVIEW_REPORT.md
    ?? src/uav_gpr/storage/rcscan_v1.py
    ?? tests/contract/rcscan_v1_golden.json
    ?? tests/contract/test_rcscan_v1.py
```

依赖合并提交（`git log` / `git branch -vv` / `git ls-files` 实测）：

| 提交 | 内容 |
|---|---|
| `ee41360` | `feat(core): add canonical raw trace hash and golden vectors (ISSUE-009)`（feature 提交） |
| `c10693f` | `Merge feat/issue-009: ...`——ISSUE-009 合入 main |
| `aab502c` | `docs(issues): mark ISSUE-009/010 Done after authorized merges` |
| `097a20e` | `feat(storage): read-only rcscan reader, strict validation and logical ordering (ISSUE-011)`（feature 提交） |
| `57c4966` | `Merge feat/issue-011: ...`——ISSUE-011 合入 main |
| `db95817` | `docs(issues): mark ISSUE-011 Done after authorized merge` |
| `0bde602` | `feat(storage): partial inspect and non-destructive recovery (ISSUE-012)`（feature 提交） |
| `24d3505` | `Merge feat/issue-012: ...`——ISSUE-012 合入 main |
| `59c4dda` | `docs(issues): mark ISSUE-012 Done after authorized merge` |
| `0903749` | `docs(data-format): freeze ISSUE-012 non-destructive recovery contract (4.1)`（当前 HEAD，= main） |

合并历史：008/009/010/011/012 链（`e852508`→`c10693f`→`4ec7d0e`→`57c4966`→`24d3505`→`0903749`），无 reset/rebase/强推迹象。`git ls-files` 确认 ISSUE-009/011/012 交付物全部 tracked 于 main。

### 3.2 依赖交付物（main 内实测，行数为 `wc -l` 实测）

| 依赖 | 交付物（main，tracked） | 行数 | ISSUE-014 复用点 |
|---|---|---|---|
| ISSUE-009 | `src/uav_gpr/core/raw_hash.py` | 686 | `compute_raw_trace_sha256` 与 `RawHashSpec` 为逐道一致性主判据；framing 排除 GNSS → GNSS 差异必须独立分类 |
| ISSUE-009 | `tests/contract/test_raw_trace_hash.py` | 1123 | 黄金对拍/等价布局/变化敏感/歧义消除/fail-closed/GNSS 排除契约测试 |
| ISSUE-009 | `tests/contract/raw_trace_hash_golden.json` | — | 独立黄金向量 |
| ISSUE-011 | `src/uav_gpr/storage/rcscan_reader.py` | 1070 | `RcScanReader`/`RcScanValidator`（严格打开校验、committed/物理视图、逻辑道排序、`ValidationReport`）作为 inventory 数据来源与发布前复验器 |
| ISSUE-011 | `tests/contract/test_rcscan_reader.py` | 1325 | reader 契约回归 |
| ISSUE-012 | `src/uav_gpr/storage/partial_recovery.py` | 775 | 非破坏恢复/检查语义（ISSUE-014 不写文件，仅参照其报告稳定序列化模式） |
| ISSUE-012 | `tests/integration/test_partial_recovery.py` | 1529 | 恢复契约回归 |

### 3.3 复审报告与状态行证据

- `docs/reports/ISSUE_009_REVIEW_REPORT_R3.md`：**PASS WITH CONDITIONS**（round-2 三条 P3 全部关闭，新 P3-04 文档精度）→ 授权合并（M02 L44 状态行 `Done`）；
- `docs/reports/ISSUE_011_REVIEW_REPORT.md`：**PASS WITH CONDITIONS**（无 P0/P1/P2）→ 授权合并（M02 L118 状态行 `Done`）；
- `docs/reports/ISSUE_012_REVIEW_REPORT.md`：**PASS WITH CONDITIONS**（无 P0/P1/P2，4 项 P3 文档性）→ 授权合并（M02 L155 状态行 `Done`）；
- M02 状态行实测（`sed -n` 逐行）：ISSUE-009 `Done`（L44）、ISSUE-011 `Done`（L118）、ISSUE-012 `Done`（L155）、ISSUE-013 `Review`（L192）、ISSUE-014 `Planned`（L229）。

**ISSUE-014 为下一个可执行 Issue 的判定**：直接依赖 ISSUE-009/011/012 均 `Done`（合并提交 + 复审报告 + 状态行三源一致）；ISSUE-013 为 `Review`（实现/测试完成、round-1 复审 PASS WITH CONDITIONS，等待人工验收），且按 M02 L230 与 README L81 **不是 ISSUE-014 的依赖**——ISSUE-014 开工不依赖 013 合入。

### 3.4 对 ISSUE-014 有约束的契约要点（读自 DATA_FORMAT.md §5/6 与 TRANSPORT_PROTOCOL.md）

**DATA_FORMAT.md §5 逐道原始哈希（ISSUE-009 冻结，L189–241）**：

1. 空地一致性以规范化 `raw_trace_sha256` 为准；规范至少固定哈希版本标记、`mission_id`/`trace_index`/`trace_uid` 的 UTF-8 规范形式、有序 channel ID、little-endian float64 频率轴、C-order little-endian complex128 原始数组。
2. 精确 framing（§5.1）：`"UAVGPR-RAW-SHA256"` 魔数 + `uint64be(1)` 版本 + 长度前缀 mission_id/trace_uid + 定宽 trace_index + channel_count + 逐通道长度前缀 channel_id + frequency_count + float64le 频率轴 + complex128le raw。
3. **GNSS 永不进入 raw hash**（定位字段补正不改变雷达原始身份）→ 一致性服务必须把 GNSS 差异独立分类，不得因此判 raw 冲突。
4. fail-closed 校验清单（非规范 UUID、负/越界 trace_index、空/重复 channel、非严格递增频率轴、dtype/shape 不匹配 → 结构化 `DomainError`）。
5. 权威契约：`src/uav_gpr/core/raw_hash.py` + `tests/contract/raw_trace_hash_golden.json` + `tests/contract/test_raw_trace_hash.py`。

**DATA_FORMAT.md §6 空地文件差异（L243–251）**：

1. 两端文件**不要求整文件相同**；一致性工具**逐字段/逐道比较，不比较 HDF5 文件 SHA256**（本 Issue 明确排除整文件 hash 比较）。
2. 两端必须相同：任务 ID、道索引/UID、频率轴、通道、原始数组、逐道 raw hash、接收到的 GNSS 记录。
3. 地面端可以增加：校准频域、时域数据、处理历史、人工注释、完整性报告——**ground 独有 processed/transport 字段不得造成 raw 不一致**（对应验收标准第 2 条）。
4. transport 状态在两端含义不同（不参与一致性比较）。

**TRANSPORT_PROTOCOL.md 对账语义（§4 消息族 + §8 重连与对账）**：

1. 对账消息族：`inventory_summary`、`missing_request`、`conflict_report`、`sync_complete`（§4 L36）。
2. §8 流程：空中端声明任务与 outbox 摘要 → 地面端声明已持久化索引/哈希摘要 → **双方分页或区间对账，避免一次发送巨大列表** → 地面端请求缺失道 → 冲突单独报告并阻止任务标记完全同步 → 全部道与 manifest 核对后产生 `sync_complete`。
3. 乱序到达可以接受；**物理 HDF5 行顺序不作为 `trace_index`**，读取/显示必须依据显式索引排序（对应验收标准第 1 条）。
4. 对 ISSUE-014 的约束映射：inventory 摘要必须**稳定、可序列化、可分页/区间化**（供后续协议 ISSUE-042/043 直接复用）；缺失/重复/冲突分类语义必须与协议 `missing_request`/`conflict_report` 一致（同 hash 重复 ≠ 不同 hash 冲突）；ISSUE-014 本身**不发送网络消息**（排除项），只产出可复用的 report。

### 3.5 发现的事实（非阻塞，供 t2 注意）

1. **工作树非干净且含 ISSUE-013 在制产物**：`docs/issues/M02_STORAGE.md` 已被 ISSUE-013 修改（仅状态行 `Planned → Review`，`git diff` 实测为单行变更）；另有 6 个 ISSUE-013 未提交文件（计划、基线单、复审报告、`src/uav_gpr/storage/rcscan_v1.py`、`tests/contract/rcscan_v1_golden.json`、`tests/contract/test_rcscan_v1.py`）。这些是**既有用户/前一 Issue 工作**：ISSUE-014 交付物必须与其文件隔离（不触碰、不覆盖、不删除）；t2 更新 M02 状态行（014 `Planned → In progress`）时只做最小编辑并与 013 的状态行改动共存。
2. `main` 相对 `origin/main` ahead 1（`0903749` 未推送）——历史遗留，非本次引入，不影响 014 开工。
3. 门禁扫描天然包含 ISSUE-013 未提交文件（ruff/mypy 扫描 34 个源文件 = 33 tracked + `rcscan_v1.py`；pytest 全量 547 含 `test_rcscan_v1.py`），本单门禁数字为含 013 在制产物的实际现状，属预期。
4. 协议二进制 codec 未冻结（TRANSPORT_PROTOCOL §11 候选 framing），但 ISSUE-014 不发送网络消息，只产出稳定可序列化 report，**不构成阻塞**；report 的 JSON 契约将在 t2 计划文档中冻结。

## 4. 门禁基线（核查时实测复跑，2026-08-30）

环境：WSL Ubuntu 24.04 / Python 3.12.3；numpy 2.5.2、h5py 3.16.0、pytest 8.4.2、ruff 0.16.4、mypy 1.20.2；`uav_gpr` editable 可导入（`src/`）。

```text
$ python3 tools/quality/verify.py
547 passed, 1 deselected in 29.91s
All checks passed!                      # ruff
Success: no issues found in 34 source files   # mypy
package import ok                       # import 检查
[quality] all gates passed

$ python3 -m pytest tests/contract/test_raw_trace_hash.py \
    tests/contract/test_rcscan_reader.py \
    tests/integration/test_partial_recovery.py -q
151 passed in 11.59s                    # 依赖定向回归（ISSUE-009/011/012）
```

核查前后 `git status --porcelain=v1 -b` **逐字节一致**（同一 7 条目快照：1 个 M02 状态行修改 + 6 个 ISSUE-013 未提交文件，均核查前已存在）；无新缓存/日志/实测数据残留（`git check-ignore` 确认 `.agent-teams/`、`*.rcscan`、`*.partial.rcscan`、`*.log` 等已忽略）；`git diff --check` clean。

## 5. ISSUE-014 实施计划摘要（t2 执行契约，完整版见 t2 计划文档）

### 范围（M02 L237–242 原文口径 + 提示词）

1. 纯应用/存储一致性服务（**无网络、无 UI**）：从 ISSUE-011 reader 生成可分页 `MissionInventory`。
2. 比较任务级契约：`mission_id`、mission/config 摘要、axis、channels。
3. 逐道按 `trace_index`/`trace_uid`/`raw_trace_sha256` 分类：**missing、extra、duplicate-same（同 hash 重复）、conflict（不同 hash 冲突）**；`trace_uid` 与 `trace_index` 交叉不匹配单独处理（fail-closed，不静默覆盖）。
4. GNSS 差异**另列**（raw hash 不含 GNSS，见 DATA_FORMAT §5.1），不因 GNSS 差异判 raw 冲突。
5. 稳定、可序列化、版本化 report（冻结 JSON 契约），供协议（ISSUE-042/043）与诊断工具（ISSUE-056）复用；大任务**分页/流式、内存有界**（十万道级别）。

### 排除项（M02 L244–246 + 提示词，t2 不得越界）

不发送网络消息、不自动补传、不修复/删除/改写任何文件；不改 `raw_hash.py`/`rcscan_reader.py`/`partial_recovery.py`/`rcscan_v2.py` 既有公共语义；不改两个参考项目；不做 GUI；不 commit、不 push、不创建/切换分支；不进入 ISSUE-015。

### 验收标准（M02 L250–252 原文，t2 不得削弱）

1. 乱序物理记录不影响结果；同 hash 重复与不同 hash 冲突区分。
2. ground 独有 processed/transport 字段不造成 raw 不一致。
3. 大任务可分页/流式处理，内存有界。

### 测试矩阵要点（提示词必测项 + 依赖回归）

- 乱序物理记录、缺道（missing）、extra、同 hash 重复（duplicate-same）、不同 hash 冲突（conflict）、GNSS 差异独立分类、十万条分页（有界内存断言）；
- ground 独有 processed/transport 字段场景；空任务/单道/双通道边界；损坏输入 fail-closed；
- 回归：ISSUE-009 黄金对拍（`test_raw_trace_hash.py`）、ISSUE-011 reader 契约、ISSUE-012 partial 契约；
- 门禁复跑：定向新测试 + 全量非硬件 pytest（`tools/quality/verify.py`）+ ruff + mypy + import + 工作树/diff 检查。

## 6. 结论

ISSUE-014 开工基线已锁定：`main`/HEAD @ `0903749`（工作树非干净——含 ISSUE-013 在制未提交产物，交付须隔离，非阻塞）；三项依赖（ISSUE-009/011/012）的代码、契约测试、独立复审报告（均 PASS WITH CONDITIONS）与 `Done` 状态行证据全部实测复现；ISSUE-013 为 `Review` 且非 014 依赖，**ISSUE-014 是下一个可执行 Issue**；契约要点（逐道 raw hash 主判据、GNSS 排除、逐字段一致性而非整文件 hash、分页对账与四类对账消息语义）已固化于第 3.4 节；门禁基线全绿（全量 547 passed / 1 deselected、ruff/mypy/import 全过、依赖定向 151 passed），核查前后 git 状态一致、无残留。本确认单即为 t2 实现与 t3 独立复审的权威基线件；t2 按本节契约执行（先落盘计划文档 → 失败测试优先 → 最小实现 → M02 状态行 → 门禁 → 报告），完成后停止，不进入 ISSUE-015。

> 后续记录：本单为开工时点的基线快照，不随 t2 改动；t2 实际完成记录见其完成报告与 `docs/plans/2026-08-30-issue-014-inventory.md`，t3 复审报告独立输出。
