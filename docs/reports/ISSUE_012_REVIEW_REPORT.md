# ISSUE-012 独立复审报告（round 1）

日期：2026-08-30
审查者：DeepSeek Harness AgentTeams `uav-gpr-issue-012-recovery`（reviewer，任务 t3，全程只读）
审查对象：ISSUE-012「partial 检查与非破坏恢复」任务 t2 交付（`src/uav_gpr/storage/partial_recovery.py`、`tests/integration/test_partial_recovery.py`、`docs/issues/M02_STORAGE.md` 状态行、`docs/plans/2026-08-30-issue-012-recovery.md`、`docs/reports/ISSUE_012_BASELINE_CONFIRMATION.md`）
审查依据：`docs/ISSUE_REVIEW_STANDARD.md` v1.0、`AGENTS.md`、`CONTRIBUTING.md`、`docs/issues/README.md`、`docs/issues/M02_STORAGE.md`、`docs/DATA_FORMAT.md`、`docs/TESTING.md`、ISSUE-010 R2 / ISSUE-011 审查报告移交事项

## 1. 审查结论

**PASS WITH CONDITIONS**

全部 5 条 ISSUE-012 验收标准（M02_STORAGE.md L176–178 + 提示词验收）逐条独立复验 PASS；回归门禁全绿；独立变异探针（绕过目标存在守卫、跳过源 SHA256 记录、放宽恢复点）证明定向测试真实可杀；Git 与交付边界合规；无 P0/P1/P2 问题。剩余 4 项 P3（均为文档性收尾/低风险观察，见第 3 节），其中 2 项为项目负责人合并时决定事项（DATA_FORMAT 4.1 条文入文——与 ISSUE-011 P3-2 同类先例），不阻止合并。

## 2. 自动识别的审查范围

从 t2 完成报告提取并逐项与 Git 事实对拍：

| 项 | 报告声称 | Git/仓库实测 | 一致 |
|---|---|---|---|
| 分支 | `feat/issue-012` 自 `main @ db95817` | `git branch --show-current` = feat/issue-012；`HEAD` = `db95817cf490b4e410c54de001ce196ff04ae980` == `main`；`git log main..feat/issue-012` = 0 提交 | ✔ |
| 提交 | 0 commit/push/merge | reflog 仅 `db95817 HEAD@{0}: checkout: moving from main to feat/issue-012`，无 reset/rebase/amend/强推迹象 | ✔ |
| 改动文件 | 新增 2 + 状态行 1 行 | `git status --porcelain=v1 -b`：`M docs/issues/M02_STORAGE.md`（仅状态行 Planned→Review）；未跟踪 4 件：`docs/plans/2026-08-30-issue-012-recovery.md`、`docs/reports/ISSUE_012_BASELINE_CONFIRMATION.md`、`src/uav_gpr/storage/partial_recovery.py`（775 行）、`tests/integration/test_partial_recovery.py`（1529 行） | ✔ |
| 范围外零改动 | core/rcscan_v2/incremental_writer/rcscan_reader/DATA_FORMAT/adr/tools 零改动 | `git diff main --stat` = 仅 M02_STORAGE.md（2 +-/1）；`git status` 无其它文件 | ✔ |
| 测试数字 | 37 / 232 / 511+1 deselected / ruff / mypy(33) / diff-check | 全部复现（见第 6 节） | ✔ |
| 状态行 | Planned → Review | M02_STORAGE.md L155 `Review（实现与测试完成，等待独立复审；仅人工验收后置 Done）`，与 `docs/issues/README.md` 第 2 节 `Review` 定义一致 | ✔ |

审查范围 = 上述 5 个文件；探针与变异产物全部位于系统临时目录（`D:\tmp\issue012-probe`、`%TEMP%`），项目内零残留（终检见第 5 节）。

## 3. 主要问题（按 P0 → P3 排序）

无 P0 / P1 / P2。4 项 P3：

- **P3-1（文档性，负责人决定）**：`docs/DATA_FORMAT.md` 无 4.1「非破坏恢复」契约小节——recovered 文件属性、3 个 mission provenance attrs（`recovery_source_sha256`/`recovery_source_file_id`/`recovery_tool_version`）、inspect/plan/execute 与失败清理语义目前只存在于计划附录 A（plans 文档 L193–229）、模块 docstring（partial_recovery.py L1–38）与 37 项测试。t2 任务契约将 DATA_FORMAT.md 列 out-of-scope（plans L29），与 ISSUE-011 P3-2 同类先例一致。**建议**：合并时由负责人决定附录 A 条文入文；provenance attrs 为附加属性，已实测对 ISSUE-008 probe/ISSUE-011 严格 reader 透明。
- **P3-2（可维护性，建议 ISSUE-056 前处理）**：`InspectReport` 未显式携带 `rename_pending` 字段；awaiting_rename 文件通过 `lifecycle_state="finalized"`+`completion_kind` 呈现，`plan_recovery` blocked_reasons 只提 lifecycle（partial_recovery.py L450–454）。ISSUE-011 移交要求「恢复必须显式识别 awaiting_rename 状态」已通过 plan 阻断正确实现（test L742–792 钉死），但诊断工具消费方需自行从 lifecycle+文件名推导。**建议**：`InspectReport` 增加 `rename_pending: bool`（reader 已有现成属性，rcscan_reader.py L679–682）。
- **P3-3（理论竞态，与既有实现同模式）**：`execute_recovery` 的目标存在检查（partial_recovery.py L624–629、L731–736）与 `os.replace`（L738）之间非原子——`os.replace` 无条件覆盖，检查与改名间存在 TOCTOU 窗口。与 ISSUE-010 writer 的 finalize（incremental_writer.py L946–953）同模式，属项目既有实践；恢复工具单所有者使用且目标名含随机 UUID，实际风险极低。**建议**：如需彻底收口，与 writer 一起在后续 Issue 统一评估独占创建（`os.link`）/rename 回退。
- **P3-4（理论观察）**：rename 后的 `RcScanValidator.validate(target)`（partial_recovery.py L743）位于 try/except 之外——若该验证抛错，目标已发布且不清理。步骤 6（L713–728）已对字节相同的暂存文件通过严格 reader 验证，该分支实际不可达；保留现状（已发布文件不删除）是合理选择，仅记录。

## 4. 逐 Issue 验收矩阵

M02_STORAGE.md L176–178 三条 + 提示词验收逐条独立复验：

| # | 验收标准 | 状态 | 代码证据（精确行号） | 测试/实测证据 |
|---|---|---|---|---|
| 1 | 任意写入故障夹具都能生成稳定报告 | **PASS** | `inspect_partial`（partial_recovery.py L320–378）：schema 级 fail-closed 复用 `RcScanReader` 打开校验（L330）；报告冻结 dataclass + 稳定键序 `to_dict()`（L251–317）；源 SHA256 流式计算（L183–189、L329）；半写尾部 `tail_rows = max(列长) − committed`（L369–371）；`physical_record_count = 最短列`（L369，与 reader L509–513 口径一致） | 测试 L584–651：ISSUE-010 全 10 相位崩溃矩阵参数化（含 4 个 close 期故障→finalized 分类），两次 `inspect_partial` `to_dict()` 逐字节一致 + 源 SHA 前后对拍；L679–739：真实 `OSError(28)` ENOSPC flush 夹具（data-flush 丢提交留尾 / commit-flush 文件内已提交）；L653–677 空 partial。**独立探针 probe0**：ISSUE-010 故障夹具独立生成 4 个相位 partial，inspect 稳定且源字节不变（4/4 通过） |
| 2 | 恢复文件可被严格 reader 读取 | **PASS** | 复制 `[0, committed_record_count)` 物理行字节（L546–565、L686–687，分块 512，L94）；checkpoint 写 committed/last_trace_index/updated_utc（L691–694）；`lifecycle_state=recovered`、`completion_kind=recovered`（L698–710）；发布前严格 reader 验证（L713–728） | 测试 L1016–1100 往返：目标 `RcScanReader` 打开通过、committed/物理视图逐道对拍（trace_index 顺序、hash_verified、raw 数值、GNSS）、逻辑视图排序、`rename_pending=False`；L1103–1136 半写尾部不复制；L1344–1395 重复/冲突/缺 hash 分类与源逐字节一致；L1398–1421 乱序提交顺序保留；L1424–1445 ground 无 /transport；L1448–1473 空 partial；L1476–1500 2000 行分块（32 块、块 ≤64）。**探针 probe0** 独立往返对拍通过 |
| 3 | 原 partial 字节不变 | **PASS** | 源文件全模块仅以 `"r"` 打开（L228–229、L333、L556）；inspect/plan 无写路径；execute 源 SHA256 重校验（L618–623） | 测试：inspect 前后 SHA 对拍 L626/647/649；plan 前后 L867；execute 成功后 L1050；目标碰撞/源变更失败后 L1166/1184–1189；7 相位失败清理后 L1239；删除失败残留后 L1301；awaiting_rename 全程 L792。probe0 独立 SHA 对拍通过 |
| 4 | 恢复失败不留下伪 finalized 文件 | **PASS** | 7 个 `RecoveryPhase` 注入点（L101–110、L640/684/688/695/698/710/737）；`except BaseException` → 关闭句柄 → best-effort 删除暂存（L739–741、L756–775）；删除也失败 → 显式错误携带残留路径（L768–775）；暂存名恒为 `<file_id>.partial.rcscan`（L630） | 测试 L1197–1246：7 相位全参数化——抛错、目标与暂存均不存在、源 SHA 不变、随后无注入重试成功；L1262–1336：remove 也失败 → 残留恒为 partial 命名且 `lifecycle_state=writing`（L1299，绝非伪 finalized）；AFTER_FINAL_MARK 之后删除失败同理（L1304–1336） |
| 5 | 未经确认只 dry-run | **PASS** | `plan_recovery` 只读、绝不写文件（docstring L436–441；无任何写调用）；`execute_recovery(plan)` 是唯一写路径（docstring L577–579；`recoverable=False` 拒绝 L599–604） | 测试 L832–869：plan 两次确定性一致、目录条目不变、源 SHA 不变、无目标出现；L872–919 非 partial/finalized/rename-pending/misnamed 全部 `recoverable=False` 且不写文件 |
| P-1 | 目标已存在必须拒绝 | **PASS** | plan 阻断（L494–495）；execute 前置守卫（L624–629）+ 改名前再查（L731–736） | 测试 L922–944（plan 阻断）、L1139–1167（execute fail-closed，既有目标字节不变、无暂存残留）。**变异 M1**（删除两处守卫）→ 测试 DID NOT RAISE 失败，证明守卫为载荷承担 |
| P-2 | 新 file_id + `completion_kind=recovered` + 源文件 SHA256/provenance | **PASS** | 新 file_id 按 role 生成/校验（L460–476、L667–668）；`completion_kind=recovered`（L704）；provenance 三 attrs（L705–707）；目标名 `<new_file_id>.rcscan`（L478） | 测试 L1016–1100（L1048–1100 逐项断言 provenance/L1091–1095 h5py 直读）；L1503–1529（显式 file_id、目标命名、自动生成、role 不符拒绝 L947–969） |
| P-3 | 恢复中断不得留下看似 finalized 的结果 | **PASS** | 同 #4 | 同 #4（含残留文件恒 partial 命名 + writing 生命周期） |
| P-4 | awaiting_rename 显式分类（ISSUE-010 R2 P3-7 / ISSUE-011 移交衔接） | **PASS** | 复用 reader probe 判定（不另起识别）：`lifecycle_state != "writing"` → blocked（L450–454）；撒谎门面 rename 失败夹具 → reader `rename_pending=True`（rcscan_reader.py L624–628） | 测试 L742–792：`_FlakyRenameFacade` 制造 finalized+partial 名文件 → reader rename_pending=True、completion_kind=completed、committed 3 道全读；inspect 呈现 finalized；plan blocked；execute 拒绝；源字节不变。分类显式性备注见 P3-2 |
| P-5 | 数据级问题（缺道/重复/冲突/缺 hash）证据保留 | **PASS** | 物理行字节复制、不重新解码/不重算 hash（L546–554 docstring + 实现）；warnings 呈现计数（L480–493） | 测试 L1344–1395：重复 1/冲突 1/缺道 1/缺 hash 1 恢复后分类与源 `to_dict()["summary"]` 逐字节一致；逻辑视图仍排除冲突（与 ISSUE-011 钉死语义一致） |

## 5. Git 与交付检查

- 分支 `feat/issue-012`，HEAD == main == `db95817`，`main..feat/issue-012` 为 0 提交；reflog 仅一次 checkout，无 reset/rebase/amend/强推。
- 工作树：1 处修改（M02 状态行，diff 已核：仅 Planned→Review 一行）+ 4 个未跟踪文件（全部在范围内）。审查结束时复查 `git status --porcelain=v1 -b` 与第 2 节一致；`git diff --check` 干净。
- `.agent-teams/` 已忽略（`.gitignore:58`，`git check-ignore -v` 实测命中）；`git ls-files --others --exclude-standard` 仅上述 4 件，无缓存/日志/构建物/密钥/实测数据。
- M02 ISSUE-012 状态行 `Review` 与 `docs/issues/README.md` 第 2 节定义（实现和测试完成，等待人工审查）一致。
- 无新增第三方依赖（partial_recovery.py 仅用 h5py/numpy/stdlib/既有 uav_gpr 模块）；pyproject 未改。
- 审查者全程只读：除本报告外未修改任何项目文件；探针/变异产物全部位于系统临时目录并已清理（终检零残留）。

## 6. 测试与验证结果

环境：Windows `.venv` Python 3.13.14（与 t2 契约一致）；命令在仓库根执行：

```text
$ .venv/Scripts/python.exe -m pytest tests/integration/test_partial_recovery.py -q
37 passed in 4.57s                                  # 退出码 0（t2 声称 37 passed ✔）

$ .venv/Scripts/python.exe -m pytest tests/contract/test_rcscan_reader.py tests/contract/test_storage_schema.py \
    tests/contract/test_raw_trace_hash.py tests/integration/test_incremental_writer.py -q
232 passed in 9.58s                                 # 退出码 0（reader 39 + schema 59 + raw hash 75 + writer 59 零回归）

$ .venv/Scripts/python.exe tools/quality/verify.py
511 passed, 1 deselected in 22.97s                  # 474 基线 + 37 新增；[quality] ruff/mypy/import 全绿，退出码 0

$ .venv/Scripts/python.exe -m ruff check src tests  # All checks passed!
$ .venv/Scripts/python.exe -m mypy src              # Success: no issues found in 33 source files
$ git diff --check                                   # 干净
```

- 新文件禁用标记检查：`xfail|skip|TODO|FIXME` 命中 0；`sleep` 仅出现在两处 docstring/注释（"no sleep"声明），无真实调用；pytest 标记仅 `integration`（pytestmark）+ `parametrize`，无 skip/xfail 标记。
- **独立探针 probe0**（系统临时目录独立脚本，非 pytest）：用 ISSUE-010 writer + `PhaseFaultHook` 独立生成 AFTER_RAW_WRITE/AFTER_TRACE_COLUMNS/AFTER_DATA_FLUSH/AFTER_CHECKPOINT_WRITE 四个崩溃 partial → inspect 两次 to_dict 一致、源 SHA 不变；plan 零写入；execute 往返 → 目标可被严格 reader 读取、lifecycle=recovered、物理顺序 [5,1,3,0] 保留、源字节不变。全部通过。
- **独立变异探针（证明定向测试真实可杀）**：将源码树复制到 `D:\tmp\issue012-probe`（系统临时目录），经 `sys.path.insert(0, ...)` 遮蔽后运行仓库原测试：
  - **M1 绕过目标存在守卫**（删除 partial_recovery.py 两处 `fs.exists(target)` 守卫）→ `test_execute_fails_closed_on_target_collision_and_source_change` **FAILED**（L1162 `DID NOT RAISE DomainError`，已占用目标被 `os.replace` 覆盖）——守卫是载荷承担，测试真实。
  - **M2 跳过源文件 SHA256 记录/重校验**（删除 `_file_sha256(source) != plan.source_sha256` 检查）→ 同一测试 **FAILED**（L1186 `DID NOT RAISE DomainError`，plan 后源被换仍被恢复）——源不变保证被测试钉死。
  - **M3 放宽恢复点选择**（复制 `physical_record_count` 行而非 `committed` 行）→ `test_recovery_roundtrip_matches_source_committed_rows` **FAILED**（L1058 `assert 7 == 6`，半写尾行泄漏进 recovered 文件）——committed 恢复点被测试钉死。
  - 每次变异后恢复原文件再施下一变异；探针区使用完毕整体删除。

## 7. 报告与事实差异

1. t2 声称「无 sleep/xfail/skip/TODO/FIXME（grep 实测命中 0）」：xfail/skip/TODO/FIXME 命中 0 属实；「sleep」字符串存在于两处注释（test L18、模块 L117，均为"不使用 sleep"的声明）。实质无任何真实 `sleep` 调用，属措辞层面小出入，不影响结论。
2. t2 §8.2 测试数字全部复现（37 / 232 / 511+1 deselected / ruff / mypy 33 files / diff-check），计时差异（4.57s vs 5.01s 等）为环境噪声。
3. t2 §8.3 工作树清单、§8.4 矩阵→用例映射、分支/HEAD/0 提交声明与仓库事实逐项一致；未发现隐藏失败、跳过、占位或范围偏离。
4. 「红灯→绿灯」过程声明（ModuleNotFoundError → 27 passed/2 failed → 37 passed）属过程性声明，无法事后独立复现，未发现反证（最终态全部可复现）。

## 8. 剩余风险

1. **DATA_FORMAT 4.1 契约小节未入文**（P3-1）：恢复契约/provenance attrs 目前只被计划附录 A、模块 docstring 与测试钉死。ISSUE-013/014/056 消费方须以 `hash_verified`/`report.issues` 为权威（DATA_FORMAT 3.1 已有该口径，L151），并按恢复文件的 `lifecycle_state=recovered`/`completion_kind=recovered` 视作有效终端文件。
2. **与 ISSUE-011 reader 语义衔接**：无冲突——恢复模块复用 `RcScanReader` 打开校验与 probe（未另起识别）；`rename_pending=True` 文件按已完成任务 blocked（不进入恢复）；缺 hash 行原样保留并重新报告。ISSUE-011 P3-1（镜像校验重复）按计划决策 8 继续移交，本 Issue 未新增第三份镜像。
3. **与 ISSUE-014 消费方口径**：恢复报告的缺道/重复/冲突/逐行 issue 分类与 ISSUE-011 `ValidationReport` 完全一致（测试以 `summary` 逐字节对拍钉死），ISSUE-014 inventory 可直接复用；ISSUE-014 对账须将 recovered 文件视为正常终端文件并识别 provenance attrs。
4. **真实掉电的文件级损坏**：HDF5 本身不可打开时 `inspect_partial` fail-closed 抛 `DomainError`（复用 reader 严格校验），不做伪恢复；flush 持久化保证最终由 M12 掉电演练确认（计划风险 3 已声明）。
5. **TOCTOU 与 rename 后验证位置**（P3-3/P3-4）：理论窗口，与 ISSUE-010 writer 同模式，单所有者工具场景实际风险极低。

## 9. 合并建议

可合并。工作树 0 提交、分支 `feat/issue-012` 干净立于 `main @ db95817`，全部验收 PASS、门禁全绿、探针/变异实证有效、无 P0/P1/P2。建议项目负责人：人工验收后 (a) 按项目惯例提交/合并（审查者不执行）；(b) 一并决定 DATA_FORMAT 4.1 条文（计划附录 A）是否入文；(c) 合并后将 M02 ISSUE-012 状态置 `Done`。不阻塞 ISSUE-013/014 开工（其依赖的 strict reader、committed 语义、rename_pending 与分类口径均已落盘并被测试钉死）。

## 10. 最小修复清单

本轮无阻断项。剩余事项（均为低风险、可后置，不阻止本次合并）：

| # | 等级 | 事项 | 位置 | 处理 |
|---|---|---|---|---|
| 1 | P3 | DATA_FORMAT 4.1 恢复契约小节未入文（provenance attrs、dry-run、失败清理、源不变语义） | `docs/DATA_FORMAT.md`（草案在 plans 附录 A L193–229） | 合并时由负责人决定入文 |
| 2 | P3 | `InspectReport` 未显式携带 `rename_pending` 标志 | `src/uav_gpr/storage/partial_recovery.py` L251–285（reader 属性已在 rcscan_reader.py L679–682） | 建议 ISSUE-056 诊断工具前补字段 + 测试 |
| 3 | P3 | 目标存在检查与 `os.replace` 间 TOCTOU | `partial_recovery.py` L624–629/L731–738（writer 同模式 L946–953） | 可选：后续 Issue 统一评估独占创建/回退 |
| 4 | P3 | rename 后 `RcScanValidator.validate` 位于 try 外 | `partial_recovery.py` L743 | 记录即可（步骤 6 已对同字节验证，分支实际不可达） |

审查结束，立即停止，不修改代码，等待项目负责人决定。
