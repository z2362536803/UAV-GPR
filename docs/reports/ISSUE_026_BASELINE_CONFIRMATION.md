# ISSUE-026 开工基线确认单（只读核查 + 实施计划摘要）

日期：2026-09-02（ISSUE-026 开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-026-gnss-matcher`（执行器 engineer，任务 t1，attempt 155a7eb2-e814-4adb-94a9-3f698f51d0d3）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支、未修改实现/测试/计划文件（本单为唯一新增文件）。
配套文件：本单为 t2（sweep midpoint GNSS 匹配器）与 t3（独立复审）的权威基线件；独立复审按 [ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) 执行。t2 完整实施契约见 t2 计划文档（建议 `docs/plans/2026-09-02-issue-026-gnss-matcher.md`，由 t2 先落盘）。

## 1. 锁定的目标 Issue 与依据

**ISSUE-026：sweep midpoint GNSS 匹配器**（`docs/issues/M05_GNSS.md` 第 3 个条目，状态 `Planned`，L79–113）

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-026（M05_GNSS.md L79–113） | docs/issues/README.md 依赖顺序主表 L93 |
| 直接依赖 | ISSUE-005（GNSS、道元数据与质量状态模型）、ISSUE-025（GNSS reader、重连与有界 fix 缓存） | M05 L82「直接依赖：ISSUE-005、025」；README.md L93 |
| 依赖状态 | **均已完成并合入 main**：ISSUE-005（`952883e`+`b11e741`，复审总结 PASS，见 docs/reports/ISSUE_001_005_REVIEW_SUMMARY.md）；ISSUE-025（`7894326` feat + `2ed9b5a` merge + `9f98cca` Done 标记 + `010ff32` P3 关闭批次，独立复审 VERDICT=PASS，见 docs/reports/ISSUE_025_REVIEW_REPORT.md；M05 L44 状态行 Done） | git log/show 实测（见 3.2）；M05 L7/L44；两份复审报告 |
| 功能映射 | FR-006（「GNSS 独立读取，保存定位质量，并按 sweep 中点匹配每一道」，PRODUCT_REQUIREMENTS.md L50）、FR-014（PRODUCT_REQUIREMENTS.md L58）；`GNSS.md` §5（与雷达道匹配：midpoint、同单调域最近邻、有符号时间差/年龄、stale_after_s、合理窗口/out_of_range、固定延迟仅经测量校正）；§4（地图只使用 valid）；§8 测试矩阵（sweep midpoint 前后 fix、正负年龄、stale 阈值和无共同时间基准）；`ROADMAP.md` Phase 5（L35–38 四件套收官：NMEA parser、reader/reconnect、fix cache 已交付，**midpoint matcher 即本 Issue**；门禁：跨午夜/stale/断线/真实 GNSS 记录回放）；ADR-0005（参考权威边界） | M05 L83；PRODUCT_REQUIREMENTS/GNSS/ROADMAP/ADR 实测（见 3.4） |
| 提示词工作区替换 | `E:\wurenjiruanjian` → 工作区根（见第 2 节） | docs/issues/README.md 第 3 节通用协议 |
| 一次一 Issue | 本轮只处理 ISSUE-026；不进入 ISSUE-027（OSL 校准模型与求解器，M06，依赖 001/004/007 与本 Issue 无关但顺序在后）；ISSUE-026 完成后停止交人工验收 | docs/issues/README.md 第 1/3 节；团队目标；M05 L113「报告并停止，不 commit/push」 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

与 ISSUE-024/025 相同：旧 UAV-GPR 参考源 `E:\UVA_GPR_system` 在本环境**不可达**（E: 盘未挂载、D: 盘无副本，ISSUE-024 基线单 §3.5-2 已实测）；冻结哈希取自 ISSUE-001 manifest 白名单（`docs/reference-baselines/manifest.json/manifest.md`，`uav_gpr_qt.py` SHA-256 `b94d3bfc…`，白名单 role 明确含「GGA/RMC 解析、GnssFixCache、GnssReader/Thread 与 **sweep 匹配**」；REFERENCE_MIGRATION §7「只迁移纯 parser/reader/matcher 行为，不带 Qt map/widget；新模型显式区分 fix 与 trace match、UTC 与单调时间、MSL 与 AGL」）。t2 按契约重实现 + 合成夹具，不做直接代码迁移（本单未重复执行盘扫描，沿用 ISSUE-024 基线单实测结论）。

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      main（当前；工作树完全干净，核查门禁前后逐字节一致）
HEAD        010ff32 chore(positioning): close ISSUE-024/025 review P3 items (hemisphere axis validation, reader hardening)
            完整哈希 010ff323e2ba61b9e3188a6a35dbade2bc004dc2（2026-09-02）
分支关系    HEAD == origin/main == 010ff323…（git rev-parse HEAD origin/main 相等）
git status --porcelain=v1 -b
    ## main...origin/main
    （无其他条目——无未跟踪/未提交文件；本基线单落盘后为唯一 ?? 条目）
git diff --check    # clean
```

reflog 顶层实测仅 commit/merge/checkout 记录（`010ff32 commit` ← `9f98cca commit` ← `2ed9b5a merge` ← `ddf2a1a checkout` ← `7894326 commit` ← `ddf2a1a checkout` …），`git reflog | grep -iE "reset|rebase|amend|filter"` **为空——无破坏性操作迹象**。本地历史 feat/issue-024…025 分支均为已合入 main 后的开发痕迹（与 ISSUE-024/025 基线单口径一致），当前分支 `main`，不影响 t2。

### 3.2 直接依赖 ISSUE-005/025 的合入证据（main 内实测）

提交链（`git log --oneline` + `git show --stat` 实测）：

| 提交 | 内容 |
|---|---|
| `952883e` | `feat(core): add GNSS and trace metadata models`（ISSUE-005：`core/gnss.py`、`core/enums.py` 扩展、`tests/unit/test_core_gnss.py`、`tests/unit/test_core_metadata.py`、`tests/unit/test_core_enums.py`） |
| `b11e741` | `fix(issue-005): enforce trace metadata integrity and GNSS consistency`（+1127/−33：raw hash 绑定/冲突规则、GNSS match 与 sweep 中点一致性、反序列化校验） |
| `7894326` | `feat(positioning): GNSS reader, reconnect and bounded fix cache (ISSUE-025)`（在 `feat/issue-025` 分支，reader.py 679 行 + 测试 851 行） |
| `2ed9b5a` | `Merge feat/issue-025: ISSUE-025 GNSS reader reconnect and bounded fix cache`（'ort' 策略合入 main，5 文件 +1964 行，含 t1 基线单/复审报告/计划文档） |
| `9f98cca` | `docs(issues): mark ISSUE-025 Done after authorized merge`（M05 L44 状态行 → Done） |
| `010ff32` | `chore(positioning): close ISSUE-024/025 review P3 items`（ISSUE-024 P3-1 半球按轴校验 `nmea.py` +2 tests；ISSUE-025 P3-1/2/4 reader 加固 +1 test；计划文档 §10/§12 更新；**当前 HEAD**） |

tracked 交付物（main @ 010ff32，实测复现）与 ISSUE-026 复用/依据点：

| 交付物 | 实测事实 | ISSUE-026 复用/依据点 |
|---|---|---|
| `src/uav_gpr/core/gnss.py`（ISSUE-005，375 行） | `GnssFix`（frozen slots，L70–211）：`received_utc`/`nmea_utc`/`received_monotonic_ns`/WGS84 经纬度/MSL/水准面/质量/卫星/HDOP/速度/航向/`valid`/`invalid_reason`，`__post_init__` fail-closed（invalid fix 禁止携带坐标 L153–157；invalid_reason ∈ {no_fix, invalid} L158–162）；`GnssMatch`（L246–375，frozen slots）：`fix \| None`/`trace_midpoint_utc`/`age_s`/`method`/`usable_for_map`/`reason`，校验矩阵 L257–330 | **matcher 输出契约已冻结**：①`age_s` 必须**非负**（L305–310「age_s must be non-negative」）；②fix 缺席时必须有 reason、禁止 STALE（L283–287「a fix-less match cannot be stale」）、age_s 必须 None、禁止 usable（L277–297）；③fix 在场时 age_s 必填有限、usable 需 fix.valid 且无 reason、非 usable 必须有 reason（L298–325）；④`reason=no_fix` 禁止 fix 在场（L326–330）；⑤`status` property（L332–341）按 usable/STALE/INVALID→`GnssStatus` 派生。**t2 只消费不改 core** |
| `src/uav_gpr/core/enums.py`（ISSUE-005/007） | `GnssStatus` 六态（L71–79）；`GnssFixQuality`（L82–91）；`GnssMatchMethod`（L94–97，**仅 `NEAREST_MIDPOINT="nearest_midpoint"`**，与 DATA_MODEL §7 一致）；`GnssUnavailableReason`（L100–107：**no_fix/stale/invalid/clock_unavailable/out_of_range 五值齐全**） | matcher 输出面枚举**全部已冻结，无需新增枚举**；M05 L93 列举的四类原因与五值一一对应 |
| `src/uav_gpr/core/timeutil.py`（ISSUE-003/005，132 行） | `MonotonicNs`（frozen，L24–53：仅 `ns: int` 非负、`__sub__` 返回 int 差，**无域/来源标识**）；`Clock` Protocol（L90–100）；`SystemClock`/`ManualClock` | matcher 的匹配键是 `MonotonicNs.ns` 整数差（等距判定应在整数 ns 上做，避免 float 相等陷阱）；**「同一单调时钟域」无法从值本身验证**——t2 需显式域声明机制（3.5-4/D5） |
| `src/uav_gpr/core/metadata.py`（ISSUE-005，428 行） | `TraceMetadata`（L92–113）：`sweep_started/midpoint/finished_utc` + 对应三个 `MonotonicNs`（**UTC 与单调域分开保存**），start≤midpoint≤finish 双域有序校验（L136–145）；**`gnss_match.trace_midpoint_utc` 必须与 `sweep_midpoint_utc` 完全相等**，否则 `GNSS_MIDPOINT_MISMATCH`（L197–209）；quality reason 映射（L52–58：CLOCK_UNAVAILABLE/OUT_OF_RANGE→`gnss_no_fix`，STALE→`gnss_stale`，INVALID→`gnss_invalid`）；`with_gnss_match` 挂载辅助（L308–339，caller 侧使用） | matcher 产出的 match 必须携带**道记录的 sweep_midpoint_utc 原值**（不得重算出不同舍入的 UTC）；挂载由采集编排（ISSUE-044）经 `with_gnss_match` 完成，matcher 不改写 metadata |
| `src/uav_gpr/core/config.py`（ISSUE-006） | `MissionConfig.gnss_max_age_s`（float，必须正有限，L271/L339/L434 规范化）+ `gnss_no_fix_policy`（L272，`record_without_position`/`abort_task`）；均为契约字段（L126–127 进摘要/ConfigDiff） | **stale 阈值挂载面已冻结**：调用方（应用层/ISSUE-044）从 `gnss_max_age_s` 接线；ISSUE-025 裁决 3 先例=阈值作必填构造参数、模块不 import config（reader.py L311–316 docstring 明示该接线留待 ISSUE-026/044） |
| `src/uav_gpr/positioning/reader.py`（ISSUE-025 + 010ff32 加固，685 行） | `GnssFixCache`（L254–299）：`max_items≥1`/`max_age_s>0`/注入 `Clock` 构造；`add` 线程安全、先按单调龄剪过期再按容量淘汰最旧；**`snapshot() -> tuple[GnssFix, …]` 按 `received_monotonic_ns` 升序返回新 tuple（frozen 元素，内部状态不外泄）**；剪枝相对「now」（注入时钟），**不是相对道 midpoint**；`GnssReader.fixes()`（L426–428）为快照入口；**valid 与 invalid fix 都入缓存**（metrics docstring L238–239、ISSUE-025 计划 D9：匹配器可区分「没收到 fix」与「收到但无效」）；`stale_after_s` 必填无静默默认（L324/L332–333）、`cache_max_age_s` 独立默认 120.0（L329） | **matcher 的唯一数据源形态**：消费 `snapshot()` 元组（纯函数、无需 Clock/锁）；必须定义 invalid 最近邻行为（3.5-6/D2）；**必须自带匹配窗口概念区分 stale 与 out_of_range**（ISSUE-025 计划 §9 明示「ISSUE-026 需要窗口 > 阈值才能区分 stale 与 out_of_range」） |
| `src/uav_gpr/acquisition/librevna/backend.py`（ISSUE-021/022） | L1068–1075：`midpoint_utc = start_utc + (finished_utc - start_utc) / 2`（datetime 除法，微秒舍入）；`midpoint_mono = MonotonicNs(start_mono.ns + (finished_mono.ns - start_mono.ns) // 2)`（**整数下取整**） | **midpoint 公式的仓内权威口径**：t2 若从 sweep start/end 计算 midpoint（M05 L91「midpoint 计算」），必须逐式复用同款公式与舍入，否则与 `TraceMetadata.sweep_midpoint_*` 挂载时触发 `GNSS_MIDPOINT_MISMATCH`/有序性不一致（3.5-7/D6） |
| `src/uav_gpr/acquisition/backend.py`（ISSUE-015 模拟器） | L695–725：`_build_gnss_match` 为**模拟器占位**（确定性 valid fix 恰在 midpoint、age_s=0.0、NEAREST_MIDPOINT），仅服务合成 metadata | **非真实 matcher**：ISSUE-026 落点为 positioning 新模块；不触碰 acquisition/**（真实接线留 ISSUE-044，ARCHITECTURE §4.2「match nearest valid GNSS fix at sweep midpoint」编排位） |
| `tests/unit/test_core_gnss.py` + `tests/unit/test_core_metadata.py`（ISSUE-005） | **47 passed**（实测）；GnssMatch 校验矩阵测试 L280–339（fix-less 无 reason、usable 带 reason、非 usable 无 reason、no_fix 带 fix、fix-less STALE、非有限 age_s 全部拒绝） | matcher 输出合法域的回归基线；t2 测试不得与 core 校验冲突 |
| `tests/contract/test_gnss_reader.py`（ISSUE-025 + 010ff32） | **40 passed**（实测）；`ScriptedClock`（L44–63）/`ScriptedSerialAdapter`（L66–113）事件驱动、无固定 sleep；L837–851 AST 守卫禁模块顶层 serial import | t2 测试风格先例（matcher 为纯函数，直接构造 `GnssFix`/中点值即可，无需线程/串口替身）；AST 守卫与 `tests/unit/test_no_external_access.py` 继续适用 |
| `docs/plans/2026-09-02-issue-025-gnss-reader.md` | §9/§11 captain 裁决 3/4（**阈值必填、无静默默认**；`cache_max_age_s` 独立于 stale 阈值并明示 ISSUE-026 窗口语义）；§12 P3 关闭批次记录（010ff32，终态门禁 1016/4） | t2 计划文档的裁决先例与门禁口径来源 |

### 3.3 ISSUE-026 为下一个可执行 Issue

- M05 L79–82：ISSUE-026 状态 `Planned`（L81）；`直接依赖：ISSUE-005、025`（L82）——两项依赖均已合入 main 且复审 PASS（见 3.2）。
- docs/issues/README.md L93：`026 | sweep midpoint GNSS 匹配器 | 005, 025`——依赖表无计划冲突；M05 内 024/025 均 Done、026 是 M05 收官项（里程碑门禁「NMEA、重连、stale 和 sweep midpoint 匹配通过」的最后一项）；**ISSUE-026 是当前唯一可执行 Issue**（后续依赖 026 的 ISSUE-044/053/054/057/060 均未解锁），一次只执行一个。
- 落点核查：`src/uav_gpr/positioning/` 仅 `__init__.py`（占位 docstring「GNSS parsing, fix models, readers, and trace-position matching.」）+ `nmea.py`（ISSUE-024）+ `reader.py`（ISSUE-025，含缓存）；src 全树 grep `matcher|nearest_midpoint` 仅命中 core 模型（enums/gnss/metadata）、acquisition 模拟器占位（backend.py L695–725，非真实 matcher）、storage 序列化（rcscan_v2 读写 `gnss_match`/midpoint 字段）；tests 全树 grep matcher **零命中**；`docs/plans/` 无 issue-026 文件（计划文档到 issue-025 为止）；`docs/reports/` 无 `ISSUE_026_*` 文件——**t2 是首个引入真实 sweep midpoint 匹配器的实现点**。
- ROADMAP Phase 5（L35–38）四件套中 parser（ISSUE-024）、reader/reconnect + fix cache（ISSUE-025）已完成，**trace midpoint matcher 即本 Issue**。

### 3.4 对 ISSUE-026 有约束的契约要点（读自 M05 L79–113、GNSS.md §4/§5/§8、DATA_MODEL §4/§6/§7/§9/§10、ARCHITECTURE §3/§4.2/§6/§9、ROADMAP Phase 5、REFERENCE_MIGRATION §7、AGENTS.md §5/§7/§9/§10/§12、ISSUE_REVIEW_STANDARD.md、团队目标）

**ISSUE-026 范围（M05 L89–94 原文 + 提示词 L106–113）**：

1. **midpoint 计算**——根据 sweep start/end 计算 midpoint（提示词「根据 sweep start/end 计算 midpoint」）；UTC 与单调域都要（GNSS.md §5「在 sweep 开始/结束记录 UTC 与单调时钟，计算 midpoint」）；**公式必须与 acquisition 仓内口径一致**（3.2/3.5-7/D6）。
2. **同一单调域最近邻**——从有界 cache（`GnssFixCache.snapshot()` 形态）选择与 midpoint 最近的 fix；**优先在同一单调时钟域比较**（GNSS.md §5），UTC 仅跨端持久化与审计。
3. **等距 tie-break 明确定义**——M05 L91「等距 tie-break」、提示词「定义等距 tie-break」：文档未规定方向，由 t2 定义并在计划文档固化（建议取较早 fix；整数 ns 距离精确相等判定；边界测试覆盖）。
4. **缓存窗口与配置 stale 阈值**——`stale_after_s` 语义阈值来自 MissionConfig `gnss_max_age_s`（调用方接线，matcher 不 import config——ISSUE-025 裁决 3 先例）；「缓存覆盖未来/过去合理窗口；超界后明确 `out_of_range`」（GNSS.md §5）——匹配窗口是**独立于缓存淘汰窗口**的 matcher 参数（ISSUE-025 计划 §9 明示需窗口 > 阈值才能区分 stale 与 out_of_range）。
5. **无共同单调域不伪匹配**——M05 L92「优先 monotonic，UTC 仅审计/无共同域时按明确策略拒绝或降级」；提示词「不得伪造共同时间域」。最小实现：显式域声明机制（3.5-4/D5），未声明共同域→`clock_unavailable` 拒绝匹配，**不落 UTC 隐式 fallback**。
6. **输出 `GnssMatch`，不改写 fix/trace metadata**——输出冻结 core 契约对象（method=`nearest_midpoint`；五类 reason；usable_for_map；age_s 语义见 3.5-2/D1）；matcher 为纯函数，无副作用。
7. **原因分类**——`no_fix`（空缓存/无 fix）/`stale`（fix 在场但超阈值）/`invalid`（最近 fix 无效）/`clock_unavailable`（无共同单调域）/`out_of_range`（超出合理窗口）——五值枚举已冻结（enums.py L100–107），映射规则见 3.2 GnssMatch 校验矩阵。

**排除项（M05 L96–98 + 提示词，t2 不得越界）**：不做插值轨迹、不推算 AGL、不做地图、不硬编码接收器延迟校正（GNSS.md §5「固定延迟只能通过测量获得的配置/校正应用并记录 provenance」——本 Issue 不实现该校正）；不改写 fix/trace metadata；不改 `core/**`；不改 reader/nmea/acquisition。

**验收标准（M05 L100–104 原文，t2 不得削弱）**：

1. midpoint 前后、正负 age、等距和阈值边界确定。
2. stale fix 不 `usable_for_map`，但原因/历史仍可保存。
3. 没有共同时间基准不伪匹配。

**其他约束**：

- ARCHITECTURE §3：`positioning` 层定义「GNSS fix、NMEA 解析、独立读取、重连、缓存和**道位置匹配**」，不渲染地图；新模块放 positioning，无 Qt/serial/config 依赖（positioning→core 依赖方向，AGENTS.md §9）。
- ARCHITECTURE §4.2：每道数据流「match nearest valid GNSS fix at sweep midpoint」发生在 append raw+metadata 之前——matcher 是采集编排（ISSUE-044）调用的纯服务，本 Issue 只交付匹配器本身。
- ARCHITECTURE §9（已知架构风险）：「GNSS 和 sweep 来自不同时间基准，必须同时保存 UTC 和单调时钟并**定义匹配误差**」——age_s/有符号差语义必须在 t2 计划文档中定义清楚（D1）。
- AGENTS.md §5：雷达道与 GNSS fix 以 sweep 中点时刻匹配，保存匹配误差和 fix 年龄；没有有效定位时位置必须为空并带原因，禁止复用过期 fix 冒充当前实测位置（stale→`usable_for_map=False` 的直接依据）；MSL/椭球高/AGL 不混用（matcher 不触碰高程语义）。
- AGENTS.md §10/团队教训：先写能失败的测试再最小实现；测试禁固定 sleep（matcher 为纯函数，注入构造值即可确定性测试）；t2 inScope 一律精确文件路径（非 glob），完成登记 changedPaths 必须与 inScope 逐一相等。
- DATA_MODEL §10：GNSS 缺失、无效和过期**通常是数据状态，不一定抛异常**——matcher 以结构化 reason 表达不可用，不抛领域异常中断采集。
- 文档权威顺序（AGENTS.md §12）：当前任务验收条件 > AGENTS.md > 已接受 ADR > 专题设计文档 > 参考项目代码/文档。若 match 语义（如 age_s 有符号性）与 core/data-model 文档存在歧义，先向 captain 提出并落计划/ADR 决策，不能只在代码中形成事实。

### 3.5 发现的事实（非阻塞，供 t2/captain 注意）

1. 工作树完全干净且与远端同步（`## main...origin/main` 0/0，HEAD `010ff32`）；t2 交付物将是唯一新改动（本基线单为 t1 交付物，不计入 t2 inScope）。
2. **D1（关键决策点）`age_s` 有符号差 vs core 非负约束**：ISSUE-026 目标行（M05 L87）与 GNSS.md §5 要求「保存有符号匹配差/时间差」，提示词要求「输出有符号 match age」；但冻结的 core `GnssMatch.age_s` 强制**非负**（gnss.py L305–310），DATA_MODEL §7 的原文是「age_s（**有符号匹配差或定义清楚的绝对年龄**）」。可行路径：(a) `GnssMatch.age_s` 采用「定义清楚的绝对年龄」（|midpoint−fix|），有符号差由**已逐道持久化的** `sweep_midpoint_monotonic_ns` 与 `fix.received_monotonic_ns` 可重建（二者均入档），matcher 结果对象可另携有符号差字段（core 外）；(b) 改 core + ADR（破坏 ISSUE-005 冻结契约，半径大，不建议 t2 承担）。**建议 (a)**，由 captain 裁决后在 t2 计划文档固化。
3. **D2 invalid 最近邻行为**：缓存同时容纳 valid 与 invalid fix（ISSUE-025 D9），invalid fix 无坐标（gnss.py L153–157）。建议：最近邻在**全部**快照 fix 上选取（诚实反映接收机状态），最近 fix invalid→fix 在场、`usable_for_map=False`、`reason=invalid`（core 校验允许；与「stale fix 不 usable 但原因/历史仍可保存」同精神）；更远的 valid fix 不得静默顶替。
4. **D5 无共同单调域的检测机制**：`MonotonicNs` 无域标识（timeutil.py L24–53），域同一性只能靠调用方声明（同进程单调时钟）。建议 matcher 必填域声明参数（如显式 bool/token）；未声明共同域→`clock_unavailable`、fix 缺席、不做 UTC 隐式 fallback（M05 L92「UTC 仅审计/无共同域时按明确策略拒绝或降级」；若 captain 要求 fallback，需明确策略字段并不得伪造共同域）。回放/地面侧跨设备数据天然不共享单调域，正是该机制的用例。
5. **D4 等距 tie-break 未有文档规定**：建议取**较早**的 fix（时间上先于/等于 midpoint 侧），距离相等判定在**整数 ns**上进行；t2 计划文档固化并配双向等距边界测试。
6. **D3 匹配窗口（out_of_range 边界）**：缓存剪枝相对 now、非相对 midpoint，快照窗口与道时刻无必然关系；建议 matcher 增加必填「合理窗口」参数（past/future 对称或分别配置，无静默默认），验证窗口 ≥ stale 阈值（否则 stale 语义不可达）；|差| ≤ 阈值→可用（需 valid）、阈值 < |差| ≤ 窗口→`stale`（fix 在场）、|差| > 窗口→`out_of_range`（fix 在场或缺席按裁决固化，建议 fix 在场留证据）；边界（恰等于阈值/窗口）行为必须测试确定。
7. **D6 midpoint 公式一致性**：matcher 从 start/end 计算 midpoint 必须与 acquisition 公式逐式相同（UTC：`start + (finish-start)/2` datetime 微秒舍入；monotonic：`start.ns + (finish-start.ns)//2` 整数下取整，librevna/backend.py L1072–1075）；否则 match.midpoint_utc ≠ TraceMetadata.sweep_midpoint_utc，挂载即触发 `GNSS_MIDPOINT_MISMATCH`（metadata.py L197–209）。测试应断言 matcher 中点与 TraceMetadata 挂载口径完全相等。
8. **D8 缓存输入形态**：建议 matcher 消费 `GnssFixCache.snapshot()` 返回的 `tuple[GnssFix, …]`（纯函数、无 Clock/锁依赖、线程安全由调用方快照保证），不接受 reader/cache 活对象；是否提供 cache 便捷重载由 t2 契约定。
9. **门禁基线数字**（第 4 章）：全量 **1016 passed / 4 deselected**（1020 collected；ISSUE-025 合并后 1013 → 010ff32 P3 批次 +3 = 1016），ruff 全绿、mypy **45 文件**全绿、import 全绿；t2 完成后新基线 = 1016 + 新增非硬件测试数，不得出现既有测试失败；mypy 预期 45 → 46 文件（matcher.py）。
10. 解释器口径：WSL `python3` 3.12.3（pytest 8.4.2、ruff 0.16.4、mypy 1.20.2、numpy 2.5.2、h5py 3.16.0、pyserial 3.5），`uav_gpr` editable 可导入；ISSUE-024/025 曾用 Windows `.venv`（3.13.14）交叉复核，t3 可任选口径独立复跑。
11. 会话事件透明记录：t1 任务在本轮执行中途被重新分配一次（attempt 1 `235d0e8c…` → attempt 2 `155a7eb2…`）；重分配前后为同一只读核查流程，无工作树变更，本报告在 attempt 2 下完成并登记。

## 4. 门禁基线（核查时实测复跑，2026-09-02）

环境：WSL Ubuntu / Python 3.12.3；numpy 2.5.2、pytest 8.4.2、h5py 3.16.0、ruff 0.16.4、mypy 1.20.2、pyserial 3.5；`uav_gpr` editable 可导入（`src/`）；解释器路径 `python3`（与 ISSUE-019～025 基线单同口径）。

```text
$ python3 -m pytest tests/contract/test_gnss_reader.py tests/contract/test_nmea.py tests/unit/test_core_gnss.py tests/unit/test_core_metadata.py tests/unit/test_core_enums.py tests/unit/test_reference_manifest.py -q
166 passed in 1.54s                                 # 依赖定向回归（reader 40 + nmea 62 + core gnss/metadata 47 + enums/manifest 17）

$ python3 -m pytest tests/contract/test_gnss_reader.py -q
40 passed in 0.43s                                  # ISSUE-025 reader/cache 契约（39 + P3 批次 1）
$ python3 -m pytest tests/contract/test_nmea.py -q
62 passed in 0.40s                                  # ISSUE-024 parser（60 + P3 批次 2）
$ python3 -m pytest tests/unit/test_core_gnss.py tests/unit/test_core_metadata.py -q
47 passed in 0.28s                                  # ISSUE-005 模型契约

$ python3 -m pytest --collect-only -q -m "not hardware and not slow" --no-header
1016/1020 tests collected (4 deselected) in 0.91s   # deselected = hardware/slow 标记项

$ python3 -m pytest -m "not hardware and not slow" -q
1016 passed, 4 deselected in 138.53s (0:02:18)      # 全量非硬件 pytest 独立复跑（预期 1016 达成）

$ python3 tools/quality/verify.py
1016 passed, 4 deselected in 140.55s (0:02:20)      # pytest 门
All checks passed!                                  # ruff（check .）
Success: no issues found in 45 source files         # mypy（45 文件）
package import ok                                   # import 检查（含 uav_gpr.positioning）
[quality] all gates passed                          # verify.py 全部通过（exit 0 实测捕获）

$ python3 -m ruff check .                           # All checks passed!（exit 0）
$ python3 -m mypy src                               # Success: no issues found in 45 source files（exit 0）
$ python3 -c "import uav_gpr, uav_gpr.core, uav_gpr.positioning, uav_gpr.storage"   # exit 0
$ git diff --check                                  # clean（exit 0）
```

核查前后 `git status --porcelain=v1 -b` **逐字节一致**（`## main...origin/main` 单行；untracked-count=0）；`git diff --check` clean；`.pytest_cache/`/`.mypy_cache/`/`.ruff_cache/` 均 git-ignored，无新缓存/日志/实测数据残留（本基线单为唯一新增未跟踪文件，落盘后复查确认）。另：核查早段曾在后台完整跑过一次 `python3 tools/quality/verify.py`（exit 0 通知确认，输出因会话重启未能回读），随后以前台复跑两次捕获上述数字——两轮全量结果一致。

## 5. ISSUE-026 实施计划摘要（供 t2 契约；完整版见 t2 计划文档，不含实现代码）

### 范围（M05 L89–94 原文口径 + 提示词）

1. 新建 `src/uav_gpr/positioning/matcher.py`（或 t2 契约定名的模块）：**纯 GnssTraceMatcher**——根据 sweep start/end 计算 midpoint（UTC+单调，公式与 acquisition 口径一致，D6）；从缓存快照在**同一单调域**选最近邻（整数 ns 距离）；等距 tie-break 明确定义（D4）；`stale_after_s`（自 MissionConfig `gnss_max_age_s` 接线）与匹配窗口为**必填参数、无静默默认**、窗口 ≥ 阈值校验（D3）；输出冻结 `GnssMatch`（fix 或空、age_s 语义按 D1 裁决、`method=nearest_midpoint`、`usable_for_map`、`no_fix/stale/invalid/clock_unavailable/out_of_range` 结构化原因）；无共同单调域→`clock_unavailable` 不伪匹配（D5）；不改写 fix/trace metadata；无 I/O、无线程、无 Clock 依赖、无 Qt/serial/config import。
2. 新建 `tests/contract/test_gnss_matcher.py`（失败测试优先，先红灯后绿灯；纯确定性——构造 `GnssFix`/中点值，无 sleep、无线程、无串口、无随机）。
3. `docs/plans/2026-09-02-issue-026-gnss-matcher.md`（计划文档，t2 先落盘：D1–Dn 设计决策及建议方案、参考源哈希与排除记录（沿用 ISSUE-001 manifest + 不可达实测结论）、执行日志、门禁数字）。
4. `docs/issues/M05_GNSS.md` 仅 ISSUE-026 状态行（L81）：`Planned → In progress → Review`（`Done` 合入后由负责人标记），勿动其他条目。

### 排除项（M05 L96–98 + 提示词，t2 不得越界）

不做插值轨迹、不推算 AGL、不做地图、不硬编码接收器延迟校正；不改写 fix/trace metadata（matcher 无副作用，挂载由 caller 完成）；不改 `core/**`（`GnssFix`/`GnssMatch`/`GnssMatchMethod`/`GnssUnavailableReason`/`MissionConfig` 只读消费——五原因与方法枚举已齐，无需新增；若确需改 core 必须先向 captain 报告并考虑 ADR）；不改 `src/uav_gpr/positioning/{reader,nmea,__init__}.py`（只读消费 snapshot）；不改 `acquisition/**`（midpoint 公式只对齐不改）；不打开真实 COM 口；不 commit/push/merge、不创建/切换分支；不新增范围外文件（若确需拆分/新增，t2 先停止向 captain 报告）。

### 验收标准（M05 L100–104 原文，t2 不得削弱）

1. midpoint 前后、正负 age、等距和阈值边界确定。
2. stale fix 不 `usable_for_map`，但原因/历史仍可保存。
3. 没有共同时间基准不伪匹配。

### 测试矩阵要点（提示词必测项 + 验收口径 + 依赖回归）

- midpoint 前后：最近 fix 在 midpoint 之前/之后/恰在中点（age 0.0）三种定位正确；
- 正负 age：fix 早于 midpoint（有符号差为负）与晚于 midpoint（为正）行为确定，`GnssMatch.age_s` 语义按 D1 裁决落测试（绝对年龄 + 有符号差可重建性/结果字段）；
- 等距 tie-break：两 fix 与 midpoint 距离在整数 ns 上精确相等→按 D4 规则确定性选择，早/晚两侧双向覆盖；
- 阈值边界：|差| 恰等于 `stale_after_s` 与恰等于匹配窗口的两侧行为确定（含/不含边界按 D3 裁决）；阈值内→可用（需 valid fix），超阈值未超窗口→`stale`（fix 在场、`usable_for_map=False`、原因可查——验收 2），超窗口→`out_of_range`；
- no_fix：空缓存快照→fix=None、age_s=None、reason=no_fix、不可用；
- invalid：最近 fix 为 invalid fix（无坐标）→按 D2 裁决（建议 reason=invalid、fix 在场留证据）；更远 valid fix 不静默顶替；
- clock_unavailable：无共同单调域声明→不伪匹配、不落 UTC 隐式 fallback（验收 3）；
- 跨 generation：快照含 reader 重连前后的 fix（单调时间间隙）→纯距离匹配不受影响，选距 midpoint 最近者；
- 纯函数性：同输入同输出；不改写输入（frozen tuple/frozen fix 原样）；输出为冻结 `GnssMatch`；无 Clock/随机/全局状态；
- 参数校验：阈值与窗口必填、有限正值、窗口 ≥ 阈值（D3）、start ≤ finish 双域校验、非法参数拒绝；
- 一致性：matcher 计算 midpoint 与 acquisition 公式逐式一致（D6），match.trace_midpoint_utc 可无损挂载 `TraceMetadata`（与 `sweep_midpoint_utc` 完全相等，复用 core 校验断言）；
- 回归：依赖定向（core gnss/metadata 47 + reader 40 + nmea 62）+ 全量 `python3 tools/quality/verify.py`（1016 + 新增数 passed / 4 deselected）+ ruff + mypy（45 → 46 文件）+ import + `git diff --check` + 工作树检查。

### inScope 精确路径建议（以任务契约 t2 inScope 为准；t2 完成登记 changedPaths 必须与此逐一相等；沿用 ISSUE-018～025 教训：精确文件路径，非 glob）

1. `src/uav_gpr/positioning/matcher.py`（新模块：GnssTraceMatcher 纯函数/不可变结果）
2. `tests/contract/test_gnss_matcher.py`（新文件：契约测试；若测试拆分/更名以 t2 契约为准）
3. `docs/plans/2026-09-02-issue-026-gnss-matcher.md`（新计划文档，t2 先落盘，含设计决策与门禁数字）
4. `docs/issues/M05_GNSS.md`（仅 ISSUE-026 状态行 L81，勿动其他条目）

（t1 基线单 `docs/reports/ISSUE_026_BASELINE_CONFIRMATION.md` 为 t1 交付物，不计入 t2 inScope；t2 outOfScope：`docs/reports/**`、`docs/GNSS.md`、`docs/DATA_MODEL.md`、`docs/adr/**`、`src/uav_gpr/core/**`（只读消费）、`src/uav_gpr/positioning/{reader,nmea,__init__}.py`（只读消费；如需扩展 `__init__.py` docstring 须先向 captain 报告）、`src/uav_gpr/acquisition/**`（公式只对齐不改）、参考仓库（只读）。）

t2 验证命令按任务契约执行：`python3 -m pytest tests/contract/test_gnss_matcher.py -q`（先红灯后绿灯）、`python3 -m pytest tests/contract/test_gnss_matcher.py tests/contract/test_gnss_reader.py tests/contract/test_nmea.py tests/unit/test_core_gnss.py tests/unit/test_core_metadata.py -q`（定向+依赖）、`python3 tools/quality/verify.py`（全量）、`python3 -m ruff check src tests`、`python3 -m mypy src`、`git diff --check && git status --porcelain=v1 -b`（同 ISSUE-018～025 口径，WSL 侧 `python3`）。

若确需拆分模块/新增文件，t2 必须先停止并向 captain 报告，不得自行新增范围外文件。

## 6. 结论

ISSUE-026 开工基线已锁定：`main`/HEAD @ `010ff32`（工作树完全干净、与 origin/main 同步 0/0、reflog 无破坏性操作）；直接依赖 **ISSUE-005/025 均已合入 main**（`952883e`+`b11e741`、`7894326`+`2ed9b5a`+`9f98cca`+`010ff32`；复审 VERDICT=PASS/总结 PASS；`GnssFix`/`GnssMatch` 冻结校验矩阵、五值 `GnssUnavailableReason`、`GnssFixCache.snapshot()` 形态、`MissionConfig.gnss_max_age_s` 挂载面、acquisition midpoint 公式及测试实测复现，依赖定向 166 passed）；**ISSUE-026 是当前唯一可执行 Issue**（M05 状态行 `Planned`、positioning 无 matcher 实现、无实现/测试/计划存在、依赖全绿）；契约要点（纯函数 matcher、同单调域最近邻、等距 tie-break、必填阈值+窗口、五类结构化原因、输出冻结 GnssMatch 不改写 metadata、无共同时钟不伪匹配、不插值/不 AGL/不做地图/不硬编码延迟校正、精确 inScope 路径建议）已固化于第 3.4/3.5/5 节；**t2 需 captain 明确的设计/契约决策点**：①age_s 有符号差 vs core 非负约束的落地语义（D1，建议绝对年龄+有符号差可重建）、②invalid 最近邻行为（D2，建议 nearest-over-all + reason=invalid）、③匹配窗口/out_of_range 边界与 fix 在场形态（D3）、④等距 tie-break 方向（D4，建议取较早 fix、整数 ns 判定）、⑤单调域声明机制（D5，建议必填声明、clock_unavailable 拒绝、无 UTC 隐式 fallback）、⑥midpoint 公式与 acquisition 一致性口径（D6，建议逐式复用+挂载一致性测试）、⑦缓存输入形态（D8，建议消费 snapshot 元组）、⑧测试落点与模块命名（建议 `tests/contract/test_gnss_matcher.py` + `positioning/matcher.py`）；门禁基线全绿（全量 **1016 passed / 4 deselected**（1020 collected）、ruff/mypy（45 文件）/import 全过、依赖定向 166 passed），核查前后 git 状态一致、无残留。**无阻塞项，可开工。** 本确认单即为 t2 实现与 t3 独立复审的权威基线件；t2 按第 5 节契约执行（先落盘计划文档 → 失败测试优先 → 最小实现 → M05 状态行 → 门禁 → 报告），完成后停止，不进入 ISSUE-027。

> 后续记录：本单为开工时点的基线快照，不随 t2 改动；t2 实际完成记录见其完成报告与 `docs/plans/2026-09-02-issue-026-gnss-matcher.md`，t3 复审报告独立输出。
