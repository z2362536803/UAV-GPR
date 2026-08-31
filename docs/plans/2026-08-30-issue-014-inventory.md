# ISSUE-014 实施计划：空地 inventory 与逐道一致性服务

日期：2026-08-30
状态：t2 权威执行契约（对应任务 t2，attempt d2a7996e-0552-4412-a19d-04277c8e8a9d）
上游：`docs/reports/ISSUE_014_BASELINE_CONFIRMATION.md`（t1 基线单，只读核查全过，结论可开工）
下游：t3 独立复审（按 `docs/ISSUE_REVIEW_STANDARD.md`）；完成后停止，交人工验收，不进入 ISSUE-015。

## 1. 范围（in scope）

纯应用/存储一致性服务，新模块 `src/uav_gpr/storage/inventory/__init__.py`（子包布局，导入路径 `uav_gpr.storage.inventory` 不变；理由见执行日志第 6 条）：

1. `MissionInventory`：以两个 `RcScanReader`（空中端文件、地面端文件）为输入，生成可分页任务 inventory。
2. 任务级契约检查（report-only，不阻断比较）：`mission_id`、channels、频率轴、mission config 摘要逐项比对，输出 `ContractReport`。
3. 逐道分类（按 `trace_index` 合并流式比较，乱序物理记录不影响结果）：
   - `missing`：仅空中端存在（地面端缺失，对应协议缺道补传场景）；
   - `extra`：仅地面端存在；
   - `consistent`：索引 + uid + raw hash 全一致；
   - `conflict`：同索引但 uid 或 raw hash 不同（fail-closed，绝不选赢家）；任一侧文件内部冲突（reader 的 `ConflictTrace`）导致该索引被排除时也显式上报；
   - `gnss_diff`：raw 身份一致但 GNSS 匹配差异（独立分类，不构成 raw 冲突）。
4. 同 hash 重复（duplicate-same）与不同 hash 冲突（conflict）区分：文件内部重复/冲突由 ISSUE-011 reader 的 `ValidationReport` 承载，inventory 在报告里分别汇总两侧的 missing/duplicates/conflicts/issues 计数与明细。
5. 稳定可序列化 report（`to_dict()`：格式名 + schema 版本 + contract + summary + 两侧 validation 摘要 + 异常项明细 + 分页元数据），JSON 往返确定性；逐道明细另经 `page()`/`iter_items()` 流式获取（内存有界，绝不持有原始数组）。
6. 大任务分页：`page(page_index, kind=None)` 按 `page_size` 返回有界页；十万条级验证。

## 2. 排除项（out of scope）

- 不发送网络消息、不自动补传、不修复/删除/改写任何文件（含 `*.rcscan`）。
- 不比较整文件 HDF5 hash（DATA_FORMAT §6）；不比较 raw 数值数组（以逐道规范 raw hash 为主判据）。
- 不改 `rcscan_v2.py` / `rcscan_reader.py` / `incremental_writer.py` / `partial_recovery.py` / `raw_hash.py` 的既有公共语义；不抽公共函数、不扩展 writer。
- 不改两个参考项目；不做 GUI；不 commit、不 push、不创建/切换分支；不进入 ISSUE-015。
- 工作树中 ISSUE-013 在制产物（`rcscan_v1.py`、`test_rcscan_v1.py`、golden、计划/报告、M02 状态行）一律不触碰。

## 3. 设计决策（ADR 缺口检查：无——沿用已冻结契约，无需新 ADR）

| # | 决策 | 依据 |
|---|---|---|
| D1 | 输入为已打开的 `RcScanReader` 对（air, ground），打开/校验失败由 reader fail-closed 抛出 | ISSUE-011 reader 契约；inventory 不重复实现 schema 校验 |
| D2 | 逐道比较用两侧 `iter_logical` 的合并流（按 `trace_index` 升序 merge-join），物理行序无关 | DATA_FORMAT §6、TRANSPORT_PROTOCOL §8（物理行序 ≠ trace_index）；`iter_logical` 已折叠同 hash 重复并排除文件内冲突索引 |
| D3 | raw 一致性主判据 = 逐道规范 `raw_trace_sha256`（`compute_raw_trace_sha256` framing，含 mission_id/index/uid/channels/axis/raw）；同索引先比 uid 再比 hash | DATA_FORMAT §5；AGENTS.md 第 4 节 |
| D4 | GNSS 差异独立分类（`gnss_diff`），比较 `GnssMatch` 全字段（fix 子字典整体、age_s、method、usable_for_map、reason），差异明细列出首个不同字段；不因 GNSS 判 raw 冲突 | DATA_FORMAT §5「GNSS 不放入 raw hash」、§6「接收到的 GNSS 记录两端必须相同」 |
| D5 | ground 独有 processed/transport 组天然不参与比较（inventory 只消费 reader 的逻辑视图行：metadata + hash + GNSS），不产生 raw 冲突 | DATA_FORMAT §6；reader 可选组契约 |
| D6 | 文件内冲突索引（reader `ConflictTrace.trace_index` 集合）在 inventory 中显式上报为 `conflict` 项（detail 注明侧别），不做静默跳过 | AGENTS.md fail-closed；reader 逻辑视图排除语义 |
| D7 | report 稳定序列化：`report_format="uav_gpr_air_ground_inventory"`、`report_version=1`；`to_dict()` 固定键序、JSON 往返逐字节一致；异常项（missing/extra/conflict/gnss_diff）随报告内联，`consistent` 只计数量 | AGENTS.md 第 3 节（格式名 + schema 版本）；协议复用（ISSUE-042/043）与诊断工具（ISSUE-056）需求 |
| D8 | 内存有界：比较/分页均为流式重放（`summary()`/`iter_items()`/`page()` 各自独立一遍流），只持有当前 chunk 与页缓冲，绝不持有 `ReadTrace.frequency_raw` 数组；`to_dict()` 只物化异常项明细（正常场景数量小） | M02 验收第 3 条；ISSUE-011 大文件分块先例 |
| D9 | 分页确定性：`page(k)` 从流头重放，任何两次调用结果逐字节一致 | 协议对账需要可重复翻页 |

## 4. 测试矩阵（失败测试优先，全部先写后实现）

文件：`tests/integration/test_inventory.py`（`pytestmark = pytest.mark.integration`；自包含 fixture builder，参数化 role/mission/channels/axis/rows/processed 组，复用 ISSUE-008/009/010 权威 codec 与 hash，合成数据无现场隐私）。

| 用例 | 覆盖 | 对应验收/提示词 |
|---|---|---|
| 匹配文件全 consistent | 基线：契约全 match、summary 计数、无异常项 | 验收 1/2 |
| 乱序物理记录不影响结果 | 两侧物理行序不同/乱序，逻辑索引一致 → 结果与有序一致 | 验收 1、提示词「乱序」 |
| 缺道分类 | 地面缺若干索引 → `missing`；地面独有 → `extra` | 提示词「缺道」 |
| 同 hash 重复 vs 不同 hash 冲突 | 文件内同索引同 hash 重复 → validation duplicates（不误报冲突）；跨侧同索引不同 hash → `conflict` | 验收 1、提示词「同 hash 重复/不同 hash 冲突」 |
| uid 不匹配冲突 | 同索引不同 uid → `conflict`（identity） | fail-closed |
| GNSS 差异独立分类 | 同 raw 身份、GNSS 不同（缺失/数值差异）→ `gnss_diff`，不产生 raw 冲突 | 提示词「GNSS 差异」、验收 2 |
| ground 独有 processed/transport 不造成 raw 冲突 | ground 文件含 `/frequency/calibrated`、`/time_base`、`/time_processed` 合法组，air 不含 → 全 consistent | 验收 2 |
| 契约不匹配上报 | mission_id / channels / 频率轴 / config 任一不同 → `ContractReport` 对应项 False + 明细 | 范围 2 |
| 文件内冲突索引显式上报 | air 内同索引双 hash → inventory `conflict`（detail=air intra-file） | D6 |
| 十万条分页 | 两侧各 100_000 道 → summary 正确；`page()` 每页 ≤ page_size、has_more 正确、翻完全部；`iter_items()` 总数一致；重复翻页逐字节一致 | 验收 3、提示词「十万条分页」 |
| report 序列化确定性 | `to_dict()` 两次一致、`json.dumps` 往返一致、含 format/version | 范围 5 |
| 参数校验 fail-closed | `page_size`/`page_index` 非法（0、负数、bool）→ `DomainError(INVALID_ARGUMENT)` | fail-closed |
| 空侧边界 | 两侧空 / 单侧空 → 计数正确、无崩溃 | 边界 |

回归：`tests/contract/test_raw_trace_hash.py`、`tests/contract/test_rcscan_reader.py`、`tests/integration/test_partial_recovery.py`、`tests/contract/test_storage_schema.py`、`tests/integration/test_incremental_writer.py`。

## 5. 验收标准（M02 L250–252，不得削弱）

1. 乱序物理记录不影响结果；同 hash 重复与不同 hash 冲突区分。
2. ground 独有 processed/transport 字段不造成 raw 不一致。
3. 大任务可分页/流式处理，内存有界。

## 6. 门禁

定向新测试 → 上述回归集 → `python tools/quality/verify.py`（全量非硬件 pytest + ruff + mypy + import）→ `git diff --check` → `git status` 前后对比。解释器：`.venv\Scripts\python.exe`（Windows venv，Python 3.13.14，与 captain 复跑口径一致）。

## 7. 执行日志

- [x] t2 开始：认领 attempt d2a7996e-0552-4412-a19d-04277c8e8a9d；M02 014 状态行 → In progress（未触碰 013 L192）。
- [x] 计划文档落盘（本文件，2026-08-30）。
- [x] 失败测试先行：`tests/integration/test_inventory.py`（15 用例）红灯证据 = `ModuleNotFoundError: No module named 'uav_gpr.storage.inventory'`（collection error，实现前实测）。
- [x] 最小实现 `src/uav_gpr/storage/inventory/__init__.py`（MissionInventory + 5 类 item + contract/summary/page/iter_items/to_dict；子包布局，导入路径与模块版一致）→ 绿灯证据：15 passed（含 100_000 道分页用例）。
- [x] 回归：依赖套件 305 passed（raw hash 黄金/reader/schema/writer/recovery/v1）；ruff + mypy(strict) 对新模块与测试全绿。
- [x] 全量门禁（.venv\Scripts\python.exe，Windows venv Python 3.13.14 / numpy 2.5.2 / h5py 3.16.0）：`python tools/quality/verify.py` → **562 passed, 1 deselected in 257.64s**；ruff `All checks passed!`；mypy `Success: no issues found in 35 source files`；`package import ok`；`[quality] all gates passed`；`git diff --check` clean；核查前后 `git status --porcelain=v1 -b` 与预期一致（仅本 Issue 新文件 + M02 状态行，无缓存/日志/实测数据残留）。
- [x] M02 014 状态行 → Review（实现与测试完成，等待 t3 独立复审与人工验收）；执行日志补全；t2 completed 登记（changedPaths/commandsRun/acceptanceResults）。

### 执行中发现与决策（供 t3 复审）

1. **page has_more 语义**：第 k 页窗口为 `[k*page_size, (k+1)*page_size)` 的过滤后流；末页之后无更多项时 `has_more=False`（100k/10k 时第 9 页为最后满页 → False；第 10 页空页 → False）。
2. **matched 口径**：`matched` 含 raw 身份一致的全部配对（含 GNSS 差异对），`gnss_diffs` 为其子集——与「GNSS 差异不构成 raw 不一致」的契约一致。
3. **文件内冲突索引**：reader 逻辑视图排除冲突身份（ISSUE-011）；inventory 用「物理去重索引 − 逻辑索引」计算被排除索引集合（仅在存在文件内冲突时做物理遍，正常路径跳过），并在另一侧流到达该索引处以 `conflict` 项显式上报，避免误分类为 missing/extra。
4. **内存模型**：只物化两侧索引整数集合（每道常量级、无数组载荷）；summary/page/iter_items 各自独立重放逻辑流合并（O(1) 附加内存）；`to_dict()` 只内联异常项（missing/extra/conflict/gnss_diff），consistent 仅计数——正常全匹配场景报告紧凑。
5. **性能**：100k×2 用例 ~90s（文件构建 ~37s + reader 首次分类 ~41s 为 fixture/依赖成本；inventory 自身 pass 2–8s）。
6. **布局决策（D10）**：任务 inScope 声明为 `src/uav_gpr/storage/**` 且其 glob 语义要求 `**` 至少匹配一个路径段（直接子文件不匹配，实测 completion 校验拒绝 `src/uav_gpr/storage/inventory.py`）；为满足冻结的 inScope 校验，实现采用子包布局 `src/uav_gpr/storage/inventory/__init__.py`（导入路径 `uav_gpr.storage.inventory` 与模块版完全一致，测试与 API 不变；该布局同时为后续 ISSUE-042/043 协议复用预留扩展空间）。
