# ISSUE-026 独立复审报告（sweep midpoint GNSS 匹配器）

- 审查日期：2026-09-02
- 审查者：AgentTeams `uav-gpr-issue-026-gnss-matcher` 成员 reviewer（任务 t3，attempt 5bd6ed54-9c61-4dfd-b508-66a6ec75a109）
- 被审交付：t2 完成报告（attempt 44a67806-f854-4912-a480-71b811b00526）+ 工作树 4 个 inScope 路径 + 计划文档 + M05 状态行
- 审查标准：[docs/ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) v1.0；基线件：docs/reports/ISSUE_026_BASELINE_CONFIRMATION.md（t1）
- 审查全程只读：未修改任何实现/测试/计划/文档/M05/Git 状态；本报告为唯一新增文件；变异探针在系统临时目录复制树（`D:\tmp\issue-026-probe`）中运行并已清理，项目内零残留。

## 1. 审查结论

**VERDICT: PASS**

ISSUE-026 的 3 条验收标准全部满足，未发现 P0/P1/P2 级问题。t2 交付真实、完整、合规，可进入人工验收（staged 流程：由项目负责人授权合并后标记 Done）。发现 3 项 P3 级非阻塞建议（见 §3），不阻止合并。

## 2. 自动识别的审查范围

从 t2 完成报告、Git 工作树与文档交叉识别（证据见 §5）：

| 项 | 识别结果 | 证据 |
|---|---|---|
| Issue | ISSUE-026「sweep midpoint GNSS 匹配器」（M05 L79–113，状态行 `Review`） | docs/issues/M05_GNSS.md L81；docs/issues/README.md L93 |
| 基线/分支 | 无独立分支；工作树直接基于 `main` @ `010ff32`（`010ff323e2ba61b9e3188a6a35dbade2bc004dc2`），HEAD == origin/main，未 commit/push/merge | `git rev-parse HEAD origin/main` 相等；`git reflog` 无 reset/rebase/amend/filter 记录 |
| 审查范围 | 工作树全部改动 = t1 交付物（`docs/reports/ISSUE_026_BASELINE_CONFIRMATION.md`，??）+ t2 的 4 个 inScope 路径：`src/uav_gpr/positioning/matcher.py`（??，219 行）、`tests/contract/test_gnss_matcher.py`（??，510 行）、`docs/plans/2026-09-02-issue-026-gnss-matcher.md`（??，110 行）、`docs/issues/M05_GNSS.md`（M，diff 仅 L81 状态行 `Planned → Review`） | `git status --porcelain=v1 -b`；`git diff docs/issues/M05_GNSS.md` 仅 1 行 |
| inScope/changedPaths 一致性 | t2 登记 changedPaths 4 条与 inScope 4 条**逐一相等**，与工作树实测一致（基线单为 t1 交付物，正确排除在 t2 inScope 外） | 任务契约 inScope vs git status |
| 直接依赖 | ISSUE-005（GnssFix/GnssMatch/TraceMetadata）、ISSUE-025（GnssReader/GnssFixCache）已合入 main @ 010ff32，代码/接口实际存在且可消费 | t1 基线单 §3.2；本次复读 `src/uav_gpr/core/gnss.py` L246–375、`src/uav_gpr/core/timeutil.py`、`src/uav_gpr/positioning/reader.py` L254–299 |
| 测试声明 | 定向 38 passed；定向+依赖 187 passed；全量 1054 passed/4 deselected；ruff 全绿；mypy 46 文件；import ok；diff-check clean | 见 §6 独立复跑 |

## 3. 主要问题（按 P0→P3）

无 P0/P1/P2。P3（非阻塞）如下：

- **P3-1 [docs/plans/2026-09-02-issue-026-gnss-matcher.md L94] 测试函数计数笔误**：执行日志写「38 passed（23 个测试函数，参数化展开 38 项）」，实际文件含 **29** 个测试函数（2 个 parametrize 展开 2+9 项 → 38 用例）。用例总数 38 正确，仅函数计数少算。建议改为「29 个测试函数，参数化展开 38 项」。
- **P3-2 [src/uav_gpr/positioning/matcher.py L109–118] `shared_monotonic_domain` 未做 bool 类型校验**：非 bool 真值（如 `1`）会被当 True 接受。域声明属于 fail-closed 契约面，建议补 `isinstance(shared_monotonic_domain, bool)` 校验（TypeError），与 thresholds 的严格校验风格一致。当前无功能影响。
- **P3-3 [src/uav_gpr/positioning/matcher.py L85–97] 浮点阈值经 `int(x * 1e9)` 取整为 ns**：语义为「向零截断」，与 reader 的 `_max_age_ns` 约定一致且确定性（探针验证 3 次构造结果一致），但文档未显式说明「配置 0.1s 实际阈值 = floor(0.1*1e9) ns」。建议在模块 docstring 补一句截断语义说明，避免未来与浮点比较实现混用。

## 4. 逐 Issue 验收矩阵（ISSUE-026，M05 L100–104 原文）

| # | 验收标准 | 状态 | 代码证据 | 测试证据 |
|---|---|---|---|---|
| 1 | midpoint 前后、正负 age、等距和阈值边界确定 | **PASS** | matcher.py L153–155 midpoint 双域公式（与 acquisition/librevna/backend.py L1072–1075 逐式一致，整数 ns 下取整 + datetime 除法）；L192–206 `_nearest_fix` 以 `(dist_ns, fix_ns)` 严格小于选最近/较早；L184–190 窗口→有效→阈值优先级；阈值比较全程整数 ns（L96–97） | test_gnss_matcher.py L165–190（中点零龄）、L211–241（前后最近邻+正负符号断言+双侧非负 age）、L249–263（等距取较早、参数化偶/奇 ns、同 ns 按快照序）、L271–315（阈值/窗口双侧恰界+越界）；审查探针补查前侧 out_of_range/前侧窗口边界/退化等宽恰界（50 passed，见 §6） |
| 2 | stale fix 不 `usable_for_map`，但原因/历史仍可保存 | **PASS** | matcher.py L188–189 超阈值 → `reason=STALE`，L176 `usable_for_map=reason is None` → False；fix 在场保留证据（L171–178 全程携带 `nearest`）；core 校验矩阵 gnss.py L298–330 允许 fix+STALE | test_gnss_matcher.py L277–290（+1ns 超阈 → STALE + fix 保留 + age 精确、前侧对称）；L193–198 TraceMetadata 挂载后 `GNSS_STALE` 入 quality_reasons；探针：GnssMatch.to_dict/from_dict 六态（含 STALE）往返相等（storage 兼容） |
| 3 | 没有共同时间基准不伪匹配 | **PASS** | matcher.py L157–162 `shared_monotonic_domain=False` → `clock_unavailable`、fix=None、age_s=None，**先于**空缓存判定；模块 docstring L49–50 明示 UTC 仅审计、无 fallback；匹配距离只使用 `received_monotonic_ns.ns`（L199），全程不引用 fix.received_utc | test_gnss_matcher.py L352–365（UTC 零距离 fix 仍拒绝 + 优先于空缓存）；探针：域声明优先于 out_of_range（fix 超窗时仍 clock_unavailable） |

提示词附加必测项（M05 L113）逐项：midpoint 前后（✓ L211–241）、正负 age（✓ L211–241）、等距（✓ L249–263）、阈值边界（✓ L271–315）、跨 generation（✓ L386–395，纯距离、generation 盲）、无共同时钟（✓ L352–365）、空缓存（✓ L373–378 → `no_fix`）。

排除项合规（M05 L96–98）：无插值轨迹、无 AGL 推算、无地图、无硬编码接收器延迟校正（`grep -iE "agl|interpolat|delay|map|leaflet"` 零命中）；不改写 fix/trace metadata（matcher 纯函数，挂载由 `TraceMetadata.with_gnss_match` 完成，测试 L193–203 证明不触发 `GNSS_MIDPOINT_MISMATCH`）；不改 core/reader/nmea/acquisition（git status 仅 4 路径）；无 Qt/serial/config/Clock/线程/I/O 依赖（import 面仅 core 三模块 + math/datetime）。

D1 有符号差语义（M05 L87「保存有符号匹配差」）：按 captain 裁决走 DATA_MODEL §7「定义清楚的绝对年龄」路径——`age_s = |signed|/1e9` 非负，有符号差 = `fix.received_monotonic_ns.ns − midpoint_ns` 可由两个已持久化分量重建（rcscan_v2 L297/L660–661 持久化 `sweep_midpoint_monotonic_ns`，GnssFix.to_dict L168 持久化 `received_monotonic_ns`，gnss_match 整体入档 L694/L882–918）。**PASS**（重建端到端可达，非仅声明）。

## 5. Git 与交付检查

```text
branch                 main；HEAD 010ff323e2ba61b9e3188a6a35dbade2bc004dc2 == origin/main（0/0）
reflog（12 条顶层）     全部 commit/merge/checkout；无 reset/rebase/amend/filter —— 无破坏性历史操作
工作树（审查前后一致）  ## main...origin/main + M M05_GNSS.md + ?? 4 文件（基线单/matcher.py/测试/计划文档）
                        —— 审查复跑前后逐项一致；.pytest_cache/.mypy_cache/.ruff_cache/__pycache__ 均 git-ignored
git diff --check       clean（exit 0）
M05 diff               仅 L81 一行：- 状态：Planned → + 状态：Review（与 t2 声称一致）
inScope 精确路径       4 条与 changedPaths 逐一相等；无范围外文件、无 glob、无缓存/日志/密钥/实测数据混入
依赖顺序               ISSUE-005/025 已合入 main 且复审 PASS（ISSUE_024/025_REVIEW_REPORT 存在）；026 未越入 ISSUE-027
```

## 6. 测试与验证结果（独立复跑实录）

环境 A：WSL Ubuntu，`/usr/bin/python3` Python 3.12.3，pytest 8.4.2，ruff 0.16.4，mypy 1.20.2（compiled）。
环境 B（探针交叉）：工作区 `.venv` Windows Python 3.13.14（复制树中运行，已清理）。

| 命令 | 环境 | 退出码 | 实际结果 | 对照 t2 声称 |
|---|---|---|---|---|
| `python3 -m pytest tests/contract/test_gnss_matcher.py -q` | A | 0 | **38 passed in 0.25s**（collect 38 项） | 38 passed / 0.26s ✓ |
| `python3 -m pytest tests/contract/test_gnss_matcher.py tests/contract/test_gnss_reader.py tests/contract/test_nmea.py tests/unit/test_core_gnss.py tests/unit/test_core_metadata.py -q` | A | 0 | **187 passed in 1.09s** | 187 passed / 0.82s ✓ |
| `python3 tools/quality/verify.py`（两次独立运行） | A | 0 | pytest **1054 passed, 4 deselected**（=1016 基线+38，算术一致；142.03s）；ruff `All checks passed!`；mypy `Success: no issues found in 46 source files`；`package import ok`；`[quality] all gates passed` | 1054/4 ✓、46 文件 ✓、全绿 ✓（首次后台运行 exit 0 通知 + 第二次落盘日志 142.03s；t2 声称 WSL 138.17s/Windows 269.38s，量级一致，Windows 侧时间未经本审查者复跑） |
| `python3 -m ruff check src tests` | A | 0 | `All checks passed!` | ✓ |
| `python3 -m mypy src` | A | 0 | `Success: no issues found in 46 source files`（45→46） | ✓ |
| `python3 -c "import uav_gpr.positioning.matcher"` | A | 0 | 导入成功（模块落点 workspace src） | ✓ |
| `git diff --check && git status --porcelain=v1 -b` | A | 0 | diff-check clean；工作树与审查开始时一致 | ✓ |
| 红灯证据 | — | — | 无法事后复现（实现已存在）；测试文件 L47 `from uav_gpr.positioning.matcher import GnssTraceMatcher` 在实现前必然 ModuleNotFoundError，与声称 exit 4 collection error 相符——**未发现反证** | 合理可信 |

**独立变异探针（t2 未覆盖的关键反例，审查者新增，环境 B 临时复制树 `D:\tmp\issue-026-probe`，已删除）**：新增 `tests/probe_review_026.py` 12 项探针 + conftest 强制从复制树导入（editable meta-path finder 劫持已被绕过，assert 验证 `uav_gpr.__file__` 位于复制树）：

- 前侧 out_of_range（`MID−WINDOW−1ns` → OUT_OF_RANGE 且 fix 保留）；
- 前侧窗口恰界（`MID−WINDOW` → STALE，含边界侧语义对称）；
- 优先级：invalid 优先于 stale（窗口内超阈 invalid fix → INVALID）；
- 优先级：clock_unavailable 优先于 out_of_range（域声明 False + 超窗 fix）；
- 零时长 sweep（start==finish 双域 → 中点=起点，age 0.0 可用）；
- 退化窗口==阈值恰界（距离恰等于阈值 → usable；+1ns → OUT_OF_RANGE，stale 带为空）；
- 浮点阈值（0.1s）截断语义确定性（3 次独立构造结果一致）；
- bool/int 阈值输入 → TypeError（fail-closed）；
- 等距 valid vs invalid：窗口内等距对——较早者胜（valid 早 → usable；invalid 早 → INVALID）；窗口内超阈等距对 → STALE（D2/D4 组合）；
- 六态 GnssMatch `to_dict/from_dict` 往返相等（storage 兼容面）；
- `(a+b)//2 == a+(b−a)//2` 恒等抽验（D6 与 acquisition 公式一致性，含奇偶差）；
- 输出冻结（FrozenInstanceError）+ 重复调用相等 + 输入不变。

结果：执行器套件 38 + 探针 12 = **50 passed in 0.06s**（探针初稿曾因作者窗口参数设计错误暴露 1 例「伪失败」，实为探针自身把 5s 等距对放在 1s 窗口外导致 out_of_range 属正确行为；修正探针后全绿——执行器实现未被任何探针击穿）。复制树已 `rm -rf` 删除，`find` 复查 `/tmp`、`D:\tmp` 无 `issue-026-probe` 残留。

## 7. 报告与事实差异

1. **P3-1**：t2 执行日志「23 个测试函数」与实际 **29** 个不符（38 用例数正确）。→ 与 §3 P3-1 相同。
2. 门禁耗时：t2 声称 WSL 全量 138.17s，本审查者实测 142.03s——同一数量级、机器负载噪声，不计差异；Windows 侧 269.38s 未独立复跑（探针仅在 Windows 解释器上跑了定向+探针 50 项），标为「未独立复验」。
3. 红灯证据（ModuleNotFoundError/exit 4）：无法事后复现，标为「未发现反证」。
4. 其余声明（行数 219/510/110、mypy 46、ruff 全绿、187/38/1054 数字、M05 单行 diff、未 commit/push/建分支、inScope 逐一相等）**全部与仓库事实一致**。

## 8. 剩余风险

- 真实 GNSS 串口/接收器行为不在本 Issue 范围（reader 侧 ISSUE-025 已覆盖；ROADMAP Phase 5 门禁中的「真实 GNSS 记录回放」留待系统验收 ISSUE-060）。
- `shared_monotonic_domain` 为调用方声明，同进程内由 ISSUE-044 编排接线；若调用方误声明 True（跨设备数据），匹配器无法从值检测——此为设计内契约（D5），已文档化，需在 ISSUE-044 集成测试中固化。
- 浮点秒→ns 截断语义（P3-3）与 reader 同约定，行为确定；若未来引入非整数阈值亚 ns 精度要求需另行决策。
- 空缓存与 no_fix 语义合并（快照空 → `no_fix`）；「连接断开但缓存有旧 fix」由窗口/stale 判定覆盖——与 ISSUE-025 设计一致，无新增风险。

## 9. 合并建议

- **可合并**：建议按 staged 流程由项目负责人授权将 t2 的 4 个 inScope 路径（含 t1 基线单）合入 main，随后将 M05 L81 状态行标记 `Done`（参照 ISSUE-024/025 的「复审 PASS 后负责人授权合并再标 Done」先例）。
- 合并范围 = 工作树现有 5 个未提交/未跟踪条目；无拆分必要（单一新模块+单一新测试文件，可整体回退）。
- P3-1/P3-2/P3-3 为可选清理项，可随合并提交一并修正（建议）或按 ISSUE-024/025 先例在后续 P3 批次关闭；不构成合并阻塞。
- 合并后不进入 ISSUE-027；等待项目负责人决定。

## 10. 最小修复清单

1. （P3-1）`docs/plans/2026-09-02-issue-026-gnss-matcher.md` L94：将「23 个测试函数」更正为「29 个测试函数」。
2. （P3-2）`src/uav_gpr/positioning/matcher.py` L157：增加 `if not isinstance(shared_monotonic_domain, bool): raise TypeError(...)`（配一条失败测试，位于 L448 参数校验区）。
3. （P3-3）`src/uav_gpr/positioning/matcher.py` L94–97：在 docstring 补充阈值/窗口「秒→整数 ns 截断（int(x*1e9)，与 reader 同约定）」的语义说明。

（以上均不涉及语义变更；若选择暂缓，不影响本次 PASS 结论。）
