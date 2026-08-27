# ISSUE-008 独立审查报告

日期：2026-08-27（复审会话）
审查者：DeepSeek Harness 独立审查会话（非本轮执行者）
审查对象：ISSUE-008「冻结 `.rcscan` v2 物理 schema 与 codec」
审查标准：`docs/ISSUE_REVIEW_STANDARD.md` v1.0

## 1. 审查结论

**PASS WITH CONDITIONS（可交人工验收，尚不得自动合并）**

ISSUE-008 的三条验收标准全部有代码与测试证据支撑；全部门禁由本审查会话独立复跑通过；
内部复审（round-4 FAIL → round-6 PASS）遗留的唯一 P1 已被最小修复并有独立反例验证。
剩余条件为流程性事项（见第 9 节），不涉及代码缺陷。

## 2. 自动识别的审查范围

| 项 | 事实 |
|---|---|
| Issue | ISSUE-008（`docs/issues/M02_STORAGE.md` 第一个条目） |
| 依赖 | ISSUE-004~007，均已合入 `main`（`0ddbd81`）并有测试证据 |
| 分支 | `feat/issue-008`（独立分支，符合"main 不直接开发"规则） |
| 基线 | HEAD = main = origin/main = merge-base = `0ddbd81709a32d870a790a322526ca18042dba1d`；`main...HEAD` 提交数为 0 |
| 交付物（未提交） | `src/uav_gpr/storage/rcscan_v2.py`（新增，1556 行）、`tests/contract/test_storage_schema.py`（新增，1341 行）、`tests/contract/rcscan_v2_golden.json`（新增，581 行）、`docs/DATA_FORMAT.md`（修改，+30 行，仅第 2.1 节） |
| 附带文件 | `docs/plans/2026-08-27-issue-008-schema-codec.md`、`docs/reports/ISSUE_008_BASELINE_CONFIRMATION.md`（基线确认件，untracked） |
| 运行时残留 | `.agent-teams/`（AgentTeams 团队状态，非项目内容，按基线单声明不提交） |
| reflog | 仅 `reset: moving to origin/main` 与 `checkout` 两条，无 rebase/force-push 证据 |

范围判定依据：Git 事实 + 基线确认单 + 团队任务记录（t1–t15）相互印证；无范围外修改，core 模块零改动。

## 3. 主要问题（P0→P3）

- P0：无。
- P1：无（round-4 的 P1-01/P1-02 已分别由 row_json 移除 + 单一投影 codec、presence-mask 双向校验关闭）。
- P2：无。
- P3-01：`docs/issues/M02_STORAGE.md` 中 ISSUE-008「状态」字段仍为 `Planned`，与实际进度（实现+复审完成，待人工验收）不符。按 `docs/issues/README.md` 第 2 节状态定义应置 `Review`。属状态管理滞后，非代码缺陷。
- P3-02：交付物未 commit。执行协议本身允许"默认不 commit，人工验收后决定"，但若长期停留在工作树，存在丢失风险。建议人工验收后按 Issue 边界提交。

## 4. 逐 Issue 验收矩阵

### ISSUE-008：冻结 `.rcscan` v2 物理 schema 与 codec

| 验收标准 | 状态 | 代码证据 | 实测证据 | 问题/限制 |
|---|---|---|---|---|
| schema 创建后 HDF5 结构/dtype 与契约完全对拍 | PASS | `rcscan_v2.py:253-377`（`dataset_contracts()` 冻结 53 个数据集契约）；`rcscan_v2.py:1108-1299`（`create_rcscan_v2()` 严格按契约创建） | `test_storage_schema.py` TestDatasetContracts/TestRootAttributes：黄金 manifest（`rcscan_v2_golden.json`）与真实 HDF5 文件逐条对拍，含 dtype/maxshape/chunks/compression/required_for；独立 manifest ↔ 生产契约交叉验证（本审查 P7 探针复现） | manifest 为合成黄金文件，非真机产物（本 Issue 不涉及真机） |
| 缺失 GNSS/时间有有效位或固定哨兵，不靠猜 NaN 原因 | PASS | `rcscan_v2.py:77-114`（`MISSING_INT64` 哨兵、TIMING/GNSS presence bitmask 常量）；`rcscan_v2.py:632-806`（`trace_metadata_to_cells()` 投影）；`rcscan_v2.py:809-969`（`trace_metadata_from_cells()` 以 `validate_presence_mask` 双向校验 mask↔payload，`831-867` 行） | 四类 GNSS 域对象（完整 fix/invalid fix/fix=None/match=None）无损往返测试；本审查独立反例探针 P1–P3：未知 mask 位、present+NaN、absent+真值均被拒绝；round-6 复审的六个畸形行反例亦全部 fail-closed | `raw_nmea` 列在本 Issue 恒写空串（隐私默认最小化，列存在性由 schema 保证） |
| 不支持版本 fail-closed，air/ground 所需组明确 | PASS | `rcscan_v2.py:1378-1556`（`probe_rcscan_v2()`：format_name/profile/role/lifecycle/file_id/writer_version 逐项校验；air 缺 `/transport` 拒绝；ground 有则按冻结结构校验）；`rcscan_v2.py:1289-1290`（创建时 ground 不建 transport 组） | TestFailClosedDetection：schema_version=3/1/2.5、未知 profile、错误 format_name、非 HDF5 payload、air 缺 transport、ground 半个 transport 组均被 `UNSUPPORTED_SCHEMA_VERSION`/`INVALID_ARGUMENT` 拒绝 | minor 版本探测策略当前等价于 major 严格（SUPPORTED 集合仅 {2}），符合"未知即拒绝" |

附加核对（超出验收字面，按审查标准 8.3）：

- 物理行 ≠ trace_index：`/frequency/raw` 可扩展第一维 + chunk `(1,c,f)`，文档与代码一致（`rcscan_v2.py:284-290`）。
- 依赖方向：storage → core 单向，无 UI/网络/硬件依赖（AGENTS.md 第 9 节合规）。
- 排除项合规：无增量 writer/reader/恢复/v1 迁移代码；`create_rcscan_v2` 为一次性骨架创建器，`probe` 只读。
- 黄金样本合成性：manifest 用合成双通道/16 频点，无实测数据、无密钥、无参考仓库文件。

## 5. Git 与交付检查

- 独立分支开发，基线 `0ddbd81`，与 `main`/`origin/main` 三点一致；无 ISSUE-008 提交（符合"默认不 commit"协议）。
- `docs/DATA_FORMAT.md` 唯一修改为新增 2.1 节"物理 schema 冻结（ISSUE-008）"，与 AGENTS.md 第 12 节"持久化语义变更先文档"一致：文档先行记录冻结契约，再以代码/测试/manifest 三方固化。
- 无缓存、日志、构建产物、实测数据、密钥或参考项目文件进入交付；`.agent-teams/` 为运行时状态，按声明不提交。
- reflog 无历史重建证据；远端操作无法从本地完全证明，记为"未发现反证"。
- 团队执行轨迹（t1→t15）：初版实现 → 内部复审 FAIL（row_json 冗余、mask 脱节、哨兵边界）→ 修复 → 复审 FAIL（from_cells 缺校验）→ 最小修复 → 最终复审 PASS。问题收敛轨迹与代码现状吻合。

## 6. 测试与验证结果

环境：WSL Ubuntu 24.04，Python 3.12.3，h5py 3.16.0，NumPy 2.5.2，pytest 8.4.2。

本审查会话独立复跑（非引用执行者声明）：

| 命令 | 结果 |
|---|---|
| `python3 -m pytest tests/contract -q` | exit 0，**59 passed** |
| `python3 -m pytest -m "not hardware and not slow" -q` | exit 0，**301 passed, 1 deselected**（hardware 双重 opt-in sentinel） |
| `python3 tools/quality/verify.py` | exit 0，pytest/Ruff/mypy(strict, 29 files)/package import **all gates passed** |
| `git diff --check` | exit 0，无空白错误 |

本审查补充的独立反例探针（执行者测试之外）：

| 探针 | 结果 |
|---|---|
| P1 未知 GNSS presence 位（bit 7=128） | 被拒绝 ✓ |
| P2 timing present 位 + NaN payload | 被拒绝 ✓ |
| P3 timing absent 位 + 真实值 payload | 被拒绝 ✓ |
| P4 完整行 codec 无损往返 | 相等 ✓ |
| P5 行投影覆盖全部 trace-major 必需列（不多不少） | 通过 ✓ |
| P6 UTC ns 编码在 1/123456/654321/999999 微秒精确往返 | 通过 ✓ |
| P7 黄金 manifest ↔ 生产契约路径集合一致 | 通过 ✓ |

## 7. 报告与事实差异

- t14 完成报告声称"59 contract + 301 全量 + Ruff + mypy + import + diff-check 全绿"：本会话独立复跑**可复现**，数字一致。
- t15 复审声称的六类畸形行 fail-closed：本会话以独立构造的同类反例（P1–P3）**部分复现**并全部通过；t15 的 usable=2、GNSS absent+hdop=0 两类未在本会话重复，但等价代码路径（`rcscan_v2.py:831-867`）已被 P1–P3 覆盖。
- 执行环境声明（WSL、Python 3.12.3、依赖版本）与实际探测一致。
- "先写失败测试再实现""未 push""参考项目未动"等过程性声明：本地无反证，记为**未发现反证**，无法完全独立验证。
- `docs/issues/M02_STORAGE.md` 状态字段（`Planned`）与团队任务记录（已完成实现与复审）不一致：列入 P3-01。

## 8. 剩余风险

- 无真实硬件参与（本 Issue 为纯 schema 契约，不要求硬件）。
- chunk `(1,)` 逐道元数据列的吞吐/空间基准未做（DATA_FORMAT 2.1 明示留待基准数据后决定压缩；属 ISSUE-010+ 范围）。
- ground 侧 transport 各列语义（receive/ACK/retry/status）按文档明示留待 ISSUE-041/043。
- 长期未提交的工作树存在意外丢失风险（见 P3-02）。

## 9. 合并建议

**不建议自动合并；交项目负责人人工验收。** 验收通过后：

1. 将 `docs/issues/M02_STORAGE.md` ISSUE-008 状态改为 `Review` → 验收后 `Done`；
2. 按 Issue 边界提交四个交付文件（schema 模块、契约测试、黄金 manifest、DATA_FORMAT 2.1 节）与两份基线/计划文档；`.agent-teams/` 不提交；
3. 合并后 ISSUE-009 方可开工。

## 10. 最小修复清单

无阻止合并的代码修复项。仅流程事项：

1. 更新 `M02_STORAGE.md` ISSUE-008 状态字段（P3-01）。
2. 人工验收后按 Issue 边界 commit（P3-02）。

审查结束，立即停止，不修改代码，等待项目负责人决定。
