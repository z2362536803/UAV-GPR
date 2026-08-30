# ISSUE-010 实施计划：增量 writer、checkpoint 与原子 finalize（收尾修复轮）

日期：2026-08-28
状态：round-1 独立审查 `PASS WITH CONDITIONS` → 收尾最小修复完成 → 等待 round-2 独立复审与人工验收
执行会话：uav-gpr-issue-010-close / engineer（t1 基线确认 → t2 修复 → t3 复审）
权威基线：本会话开工时点快照 [ISSUE_010_BASELINE_CONFIRMATION.md](../reports/ISSUE_010_BASELINE_CONFIRMATION.md)
（t1 交付物，只读核查全部实测复现）；审查依据 [ISSUE_010_REVIEW_REPORT.md](../reports/ISSUE_010_REVIEW_REPORT.md)
（第 10 节最小修复清单，P3 各项明确不纳入本轮）。

## 0. 收尾修复日志（2026-08-28，据实记录，全部为命令实测）

### 0.1 开工时点事实（与基线确认单一致，非计划声称）

- 分支 `feat/issue-010` @ HEAD `ee41360`（= ISSUE-009 提交，即审查基线）；`main`=`e852508`；本批 0 提交，改动全部未提交。
- 审查快照后工作树已被改动：实现 `incremental_writer.py` 1005→1043 行，测试 1616→1897 行、52→56 用例。
  既有改动已含：`hdf5_opener` 生产缝、`_by_position` 反向索引、`close()` best-effort 注释、`create()` docstring
  终态说明、`_FlakyRenameFacade` 重试测试、flush 计数/真实 flush 失败/rename 重试守卫测试、P2-3 预设块实现、
  相位序列测试注释修正（P2-1）。
- 门禁基线（t1 实测）：定向 56 passed；`verify.py` 432 passed/1 deselected，ruff/mypy(31 files)/import 全绿。

### 0.2 本轮真实修复记录（先失败测试 → 最小修复 → 测试通过）

| 审查问题 | 失败测试/红灯证据（修复前） | 最小修复 | 验证 |
|---|---|---|---|
| **P2-2** rename 前第二处"目标已存在"守卫零测试覆盖 | 测试已存在（`test_rename_retry_refuses_when_the_target_appeared_in_the_meantime`，L1630 起）：`_FlakyRenameFacade(fail_times=1)` → `close()` 抛 OSError → `awaiting_rename` → 外部创建 `final_path` 哨兵 → 重试 `close()` 断言 `INVALID_ARGUMENT` 且哨兵字节未变。**红灯证据（本轮变异探针）**：在 /tmp 副本中删除 `close()` 内 rename 前守卫（第二处）后该测试 **1 failed**（`DID NOT RAISE`——守卫缺失时重试会直接 `os.replace` 覆盖哨兵）；删除 `_finalize_file` 内第一处守卫则该测试仍通过而 `test_close_refuses_to_overwrite_an_existing_target` 失败 | 实现无需改动（两处守卫均已在位）；本轮以变异验证证明测试 1:1 钉住第二处守卫 | 变异体 1 failed/1 passed（守卫测试失败、另一处守卫测试通过）；真实代码该测试通过 |
| **P2-3** 预置哈希矛盾冲突绕过 `_record_conflict`、无证据 | 实现已含修复（`_append_trace` L702–715：`preset_hash != digest` → 统一走 `_record_conflict`，context 键集统一），但**测试缺失**（无任何用例预置 `raw_trace_sha256`）。本轮新增 3 测试（L865–973）：①新鲜 index 预置矛盾哈希 ②已提交 index 预置矛盾哈希 ③预置匹配 digest 幂等。**红灯证据（本轮变异探针）**：在 /tmp 副本中仅删除 L702–715 预设块（保留 `with_integrity`）→ 负向 2 用例 **2 failed**（`KeyError`：context 缺 `trace_index`/`record_position` 等键、`conflicts` 为空），匹配哈希用例通过 | 实现无需改动；本轮补测试使该路径获得 1:1 覆盖 | 真实代码 3/3 转绿；变异体 2 failed/1 passed |
| **P2-4** 子进程 `os._exit()` 硬崩溃用例建模前提不成立、注释论断与事实不符 | 注释（L1328–1340）仍声称 "no flush"、"exactly what a power loss would leave"——与 `_force_close_handle`（L1023–1033）在异常传出前已 `flush()+close()` 的事实不符 | 注释重写为事实描述（现 L1438–1457）：writer 异常传出前已应急 flush+close，子进程与进程内故障落盘状态一致，本组价值=跨进程不变式验证，flush 由 handle spy 测试钉住；6 个子进程用例行为未改、未删 | 6 用例保持通过；注释与实现一致 |
| **P2-1** 两次 flush 移除无法被测试杀死；注释/报告变异结论锁定的是相位播报 | 测试侧已含修复（既有）：`test_each_commit_performs_two_real_hdf5_flushes_around_the_checkpoint`（注入 handle 断言每提交恰 2 次真实 `flush()` 及精确事件序列）、2 个真实 flush 失败用例（ENOSPC）、相位序列测试注释已为事实口径 | 本轮无代码改动；"报告口径"在本计划文档与本会话 t2 完成报告中落实：flush 由注入 handle 观测钉住，相位测试表述为"锁定提交顺序与相位序列"，不再声称删 flush → N 例失败证明 flush 生效 | 全量门禁与定向复跑全绿（见 0.5） |

### 0.3 本轮实际改动文件清单

| 文件 | 改动 | 行数 |
|---|---|---|
| `tests/integration/test_incremental_writer.py` | +3 测试（P2-3）、崩溃模型注释重写（P2-4） | 1897 → 2014 行；56 → 59 用例 |
| `docs/issues/M02_STORAGE.md` | ISSUE-010 状态行 `In progress` → `Review（round 2 最小修复完成，等待独立复审；仅人工验收后置 Done）` | 1 行 |
| `docs/reports/ISSUE_010_BASELINE_CONFIRMATION.md` | t1 新增（基线确认单） | 124 行 |
| `docs/plans/2026-08-28-issue-010-writer-close.md` | 本文件（修复日志） | 新增 |

`src/uav_gpr/storage/incremental_writer.py` **零改动**（P2-2/P2-3 实现均已在工作树中就位且验证正确）。

### 0.4 决策记录

- **不新增/删除错误码、不改变既有公共语义**：P2-3 的 `_record_conflict` 统一 context 键集（`trace_index`/
  `record_position`/`stored_hash`/`incoming_hash`/`stored_trace_uid`/`incoming_trace_uid`/`duplicate_trace_uid`/
  `conflicting_trace_index`）与既有同 index 不同 hash 冲突路径共用同一函数，行为（不写入、不覆盖、抛
  `ID_CONFLICT`、writer 保持可用）完全一致。
- **P3-1～P3-6 不纳入本轮**：P3-1/2/3/5 已在工作树中闭合（既有改动），P3-4 建议 ISSUE-011 处理，
  P3-6（`.agent-teams/` 是否加入 `.gitignore`）由项目负责人决定。

### 0.5 门禁与验证（本轮实测，环境事实见下）

**环境事实（精确命令实测）**：WSL2（Linux 6.18.33.2-microsoft-standard-WSL2-x86_64，glibc 2.39）；解释器
`/usr/bin/python3` = **Python 3.12.3**；numpy **2.5.2** / h5py **3.16.0** / pytest **8.4.2** / ruff **0.16.4** /
mypy **1.20.2**。任务契约中的 `.venv/Scripts/python.exe`（Windows .venv 形态）在本 WSL 环境不可用（与
ISSUE-009 round-2 基线单记录的既有环境差异一致，历史审查报告中的 Windows Python 3.13.14/.venv 为另一台
形态的记录）；本轮全部命令使用等价可运行入口 `python3`，命令与输出如实记录，未伪造。

| 命令 | 退出码 | 结果 |
|---|---:|---|
| `python3 -m pytest tests/integration/test_incremental_writer.py -q -p no:cacheprovider` | 0 | **59 passed**（56 既有 + 3 新增） |
| `python3 -m pytest tests/contract/test_storage_schema.py tests/contract/test_raw_trace_hash.py -q -p no:cacheprovider` | 0 | **134 passed**（ISSUE-008/009 契约未受影响） |
| `python3 tools/quality/verify.py` | 0 | pytest 非硬件 **435 passed, 1 deselected**（deselect 为 hardware 双重 opt-in 哨兵）；ruff ok（0.16.4）；mypy strict ok（1.20.2，31 files）；package import ok；`all gates passed` |
| `python3 -m pytest tests/unit/test_core_isolation.py tests/unit/test_no_external_access.py -q` | 0 | 4 passed（core 隔离未破坏） |
| `git diff --check` | 0 | 无空白错误 |
| 变异探针 A（P2-2）：`cp -r src/uav_gpr /tmp/mut4/uav_gpr` → 仅删除 `close()` 内 rename 前第二处守卫（`exists(self._final_path)` 块，保留 `_finalize_file` 第一处）→ `PYTHONPATH=/tmp/mut4 python3 -m pytest tests/integration/test_incremental_writer.py -q -k "rename_retry_refuses or close_refuses_to_overwrite"` | 1（预期） | **1 failed**（`test_rename_retry_refuses_when_the_target_appeared_in_the_meantime`：`DID NOT RAISE`——守卫缺失时重试 `close()` 会直接 `os.replace` 覆盖哨兵）+ **1 passed**（第一处守卫测试不受影响）；探针后已 `rm -rf /tmp/mut4` |
| 变异探针 B（P2-3）：`cp -r src/uav_gpr /tmp/mut2/uav_gpr` → 仅删除 `_append_trace` 中预设哈希冲突块（L702–715，保留 `metadata.with_integrity(digest)` 行）→ `PYTHONPATH=/tmp/mut2 python3 -m pytest tests/integration/test_incremental_writer.py -q -k pre_attached` | 1（预期） | **2 failed**（`test_pre_attached_contradictory_hash_*`：`KeyError`——context 缺 `trace_index`/`record_position` 等键且 `writer.conflicts` 为空）+ **1 passed**（`test_pre_attached_matching_hash_is_accepted_idempotently`，不依赖该块）；探针后已 `rm -rf /tmp/mut2` |

### 0.6 状态行更新

`docs/issues/M02_STORAGE.md` ISSUE-010 状态：

```text
- 状态：Review（round 2 最小修复完成，等待独立复审；仅人工验收后置 Done）
```

与 `docs/issues/README.md` 第 2 节状态定义一致；复审通过后由项目负责人置 `Done`，执行器不操作。

## 1. 目标 Issue 锁定

**ISSUE-010：增量 writer、checkpoint 与原子 finalize（收尾最小修复轮）**——直接依赖 ISSUE-008/009
（均已合入/提交）；一次只处理本 Issue，不进入 ISSUE-011；默认不 commit/push。

## 2. 范围 / 排除项 / 验收标准

- **范围**：审查报告第 10 节第 1–3 行（P2-2 守卫测试、P2-3 证据统一、P2-1/P2-4 注释与报告口径）；
  `src/uav_gpr/storage/incremental_writer.py`、`tests/integration/test_incremental_writer.py`、
  `docs/issues/M02_STORAGE.md`、`docs/plans/2026-08-28-issue-010-writer-close.md`。
- **排除项**：P3-1～P3-6；`core/**`、`rcscan_v2.py`、`tests/contract/**`、`tests/unit/**`、
  `tests/hardware/**`、`docs/DATA_FORMAT.md`、`docs/adr/**`、`tools/**`、`.agent-teams/**`、两个参考项目；
  网络 ACK/outbox/UI/恢复工具（ISSUE-011/012 范围）；commit/push/merge。
- **验收标准**（M02_STORAGE.md L100–104 原文，修复未削弱，全部既有用例通过）：
  ① 每个故障点后 reader 最多看到最后完整 checkpoint，不看到半道；
  ② 不兼容 sweep、重复冲突、磁盘/flush 失败不推进 checkpoint；
  ③ finalized 文件不可继续 append，原 partial 不被无意覆盖。

## 3. 风险与未完成项

- 本环境 HDF5 写入即持久化，flush 无法行为观测：以注入 handle 计数测试为最强可重复证据（如实表述）。
- `awaiting_rename` 状态识别留给 ISSUE-012 恢复工具；P3-4（axis 校验重复）建议 ISSUE-011 处理；
  P3-6（`.agent-teams/` 忽略）由项目负责人决定。
- 修复轮未 commit/push；复审（t3）以当前代码事实为准。
