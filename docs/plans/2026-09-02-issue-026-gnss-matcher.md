# ISSUE-026 实施计划：sweep midpoint GNSS 匹配器

日期：2026-09-02
执行器：AgentTeams `uav-gpr-issue-026-gnss-matcher` 成员 engineer（任务 t2，attempt 44a67806-f854-4912-a480-71b811b00526）
基线件：[docs/reports/ISSUE_026_BASELINE_CONFIRMATION.md](../reports/ISSUE_026_BASELINE_CONFIRMATION.md)（main @ `010ff32`，工作树干净，门禁 1016 passed / 4 deselected）
目标 Issue：ISSUE-026（`docs/issues/M05_GNSS.md` L79–113）；约束文档：`AGENTS.md` §5/§7/§9/§10/§12、`docs/GNSS.md` §4/§5/§8、`docs/DATA_MODEL.md` §6/§7/§9/§10、`docs/ARCHITECTURE.md` §3/§4.2/§9、`docs/REFERENCE_MIGRATION.md` §7、ADR-0005。

## 1. 目标与用户价值

在 `positioning` 层交付纯函数式 sweep midpoint GNSS 匹配器：由 sweep start/end 计算 midpoint（UTC+单调域，与 acquisition 公式逐式一致），从有界 fix 缓存快照中在同一单调时钟域选择最近 fix，输出冻结的 `GnssMatch`（`method=nearest_midpoint`、有符号匹配差按既定符号约定可重建、`usable_for_map` 与 `no_fix/stale/invalid/clock_unavailable/out_of_range` 结构化原因）。匹配器无 I/O、无线程、无时钟依赖，是 ISSUE-044 采集编排（ARCHITECTURE §4.2「match nearest valid GNSS fix at sweep midpoint」）与后续地图联动（ISSUE-053/054/055）的唯一匹配入口。

## 2. 范围（任务契约 inScope = 4 个精确路径，changedPaths 与此逐一相等）

1. `src/uav_gpr/positioning/matcher.py`（新模块：`GnssTraceMatcher` 纯匹配器）
2. `tests/contract/test_gnss_matcher.py`（新文件：契约测试，纯确定性、无 sleep/线程/串口）
3. `docs/plans/2026-09-02-issue-026-gnss-matcher.md`（本计划文档）
4. `docs/issues/M05_GNSS.md`（仅 ISSUE-026 状态行 L81：`Planned → In progress → Review`，勿动其他条目）

## 3. 明确排除项（M05 L96–98 + 提示词 + 任务契约）

不插值轨迹、不推算 AGL、不做地图、不硬编码接收器延迟校正；不改写 fix/trace metadata（matcher 无副作用，挂载由调用方经 `TraceMetadata.with_gnss_match` 完成）；不改 `core/**`（`GnssFix`/`GnssMatch`/`GnssMatchMethod`/`GnssUnavailableReason`/`MissionConfig` 只读消费——五原因与 `nearest_midpoint` 方法枚举已冻结，无需新增）；不改 `src/uav_gpr/positioning/{reader,nmea,__init__}.py`（只读消费 snapshot）；不改 `acquisition/**`（midpoint 公式只对齐不改）；不打开真实 COM 口；不 commit/push/merge、不创建分支；不新增 inScope 之外文件。

## 4. 设计决策（D1–D8，captain 裁决 2026-09-02 全部定案，避免 ISSUE-025 式事后修订）

- **D1 有符号匹配差（裁决：按基线建议，不改 core）**：`GnssMatch.age_s` 保持 core 冻结契约——**非负绝对年龄**（|fix − midpoint|，秒）。有符号匹配差的符号约定在 matcher API 文档中显式定义并可由已持久化字段重建：`signed = fix.received_monotonic_ns.ns − midpoint_ns`（正=fix 晚于 midpoint，负=fix 早于 midpoint）；`age_s = |signed| / 1e9`。`midpoint_ns` 与 `sweep_midpoint_monotonic_ns` 同式计算（D6），二者均已逐道持久化，故「保存有符号匹配差」（M05 L87/GNSS.md §5）由持久化分量重建满足，不伪造、不丢信息。测试断言 midpoint 前后选择行为与绝对 age，并按公式数值断言符号。满足 DATA_MODEL §7「有符号匹配差或定义清楚的绝对年龄」的后者路径。
- **D2 invalid 最近邻（裁决：nearest-over-all + reason=invalid）**：最近邻在快照**全部** fix 上选取（不先按 valid 过滤）——诚实反映接收机状态（ISSUE-025 计划 D9：缓存同时容纳 valid/invalid fix，匹配器可区分「没收到」与「收到但无效」）；选中 invalid fix（无坐标）→ match 携带该 fix、`age_s=|diff|`、`usable_for_map=False`、`reason=invalid`；更远的 valid fix **不得静默顶替**。
- **D3 窗口与阈值（裁决：均必填 + 构造校验）**：`stale_after_s`（stale 阈值，调用方自 MissionConfig `gnss_max_age_s` 接线，ISSUE-025 裁决 3 先例）与 `window_s`（匹配窗口，GNSS.md §5「缓存覆盖未来/过去合理窗口」）均为必填构造参数、无静默默认、必须为正有限 float；**`window_s >= stale_after_s` 构造校验，违反即 `ValueError`**（否则 stale 语义不可达，无法区分 stale 与 out_of_range，ISSUE-025 计划 §9）。边界语义（整数 ns 比较，reader 先例同口径）：`|diff_ns| <= stale_after_ns` → 阈值内；`stale_after_ns < |diff| <= window_ns` → stale 带；`|diff| > window_ns` → **out_of_range（几何外门优先于有效性判定）**——选中的 fix 超窗即 `out_of_range`（fix 在场留证据，age_s=|diff|），即使该 fix 为 invalid（原因优先级 out_of_range > invalid > stale，测试固化）。
- **D4 等距 tie-break（裁决：取较早 fix；整数纳秒判定）**：距离相等时取 `received_monotonic_ns.ns` 较小（较早）的 fix；实现为对 `(dist_ns, fix_ns)` 二元组取严格小于的最小值（快照乱序输入也确定）；`dist_ns` 与 `fix_ns` 全等（同一 ns 的重复 fix）时取快照中先出现者。距离判定全程整数纳秒，避免 float 相等陷阱。
- **D5 时间域（裁决：显式声明，无 UTC 隐式 fallback）**：`match()` 必填 `shared_monotonic_domain: bool`——调用方显式声明 sweep 单调值与 fix `received_monotonic_ns` 是否同一单调时钟域（`MonotonicNs` 无域标识，无法从值验证；同进程采集为 True，跨设备回放/对账可为 False）。**False → `reason=clock_unavailable`、fix=None、age_s=None，即使 UTC 上存在零距离 fix 也不匹配**（M05 L92「UTC 仅审计/无共同域时按明确策略拒绝或降级」；ISSUE-026 最小实现取「拒绝」侧，不做 UTC fallback）。域声明优先于空缓存判定（无共同域时根本不匹配）。
- **D6 midpoint 公式（裁决：与 acquisition 逐式一致）**：`midpoint_ns = (started_ns + finished_ns) // 2`（整数下取整；与 acquisition/librevna/backend.py L1073–1075 的 `start + (finish-start)//2` 对全部非负整数恒等，因 `(start+finish)` = `2*start + d`）；`midpoint_utc = started_utc + (finished_utc − started_utc) / 2`（datetime 除法微秒 half-to-even 舍入，与 L1072 同式）。保证 match 的 `trace_midpoint_utc` 与 `TraceMetadata.sweep_midpoint_utc` 挂载口径完全相等（metadata.py L197–209 `GNSS_MIDPOINT_MISMATCH` 防线），测试含 TraceMetadata 端到端挂载用例。sweep 时间有序性 fail-closed：`started > finished`（UTC 或 ns 任一域）→ `ValueError`。
- **D7 测试落点与命名（按基线建议）**：`tests/contract/test_gnss_matcher.py`（与 positioning 既有 `test_nmea.py`/`test_gnss_reader.py` 同层）；模块 `matcher.py`、类名 `GnssTraceMatcher`（提示词命名）；测试纯确定性——直接构造 `GnssFix`/时间值，无 Clock、无线程、无串口、无 sleep、无随机。
- **D8 缓存消费（裁决：snapshot 元组）**：`match(fixes=...)` 接受 `tuple[GnssFix, ...]`（`GnssFixCache.snapshot()` 形态，frozen 元素、升序）；非 tuple 容器或含非 `GnssFix` 元素 → `TypeError`（fail-closed）。matcher 不接受 `GnssReader`/`GnssFixCache` 活对象、不注入 Clock——线程安全由调用方快照保证，匹配器对「now」零依赖。

**原因判定优先级（match 内部固定顺序，全部测试固化）**：
①`shared_monotonic_domain=False` → `clock_unavailable`（fix 缺席）；②快照为空 → `no_fix`（fix 缺席）；③选最近 fix（D4）；④`|diff| > window_ns` → `out_of_range`（fix 在场）；⑤fix 无效 → `invalid`（fix 在场）；⑥`|diff| > stale_after_ns` → `stale`（fix 在场）；⑦否则 → 可用（`usable_for_map=True`、reason=None，需 fix.valid——⑤已保证）。

## 5. 文件改动（精确路径，无 glob）

| 路径 | 动作 | 内容 |
|---|---|---|
| `src/uav_gpr/positioning/matcher.py` | 新增 | `GnssTraceMatcher`：构造校验（D3）+ `match()` 纯函数（midpoint 计算 D6、最近邻+等距 D4、原因优先级 D2/D3/D5、输出冻结 `GnssMatch`）；模块 docstring 含符号约定与重建公式（D1）、契约引用 |
| `tests/contract/test_gnss_matcher.py` | 新增 | ~24 个契约测试：midpoint 公式与 TraceMetadata 挂载一致性、前后最近邻+符号约定、等距 tie-break、阈值/窗口双侧边界、stale/invalid/out_of_range/no_fix/clock_unavailable、跨 generation 间隙、纯函数性/不可变性、参数校验、status 派生 |
| `docs/plans/2026-09-02-issue-026-gnss-matcher.md` | 新增 | 本文档（含决策记录与执行日志） |
| `docs/issues/M05_GNSS.md` | 修改 | 仅 L81 状态行 Planned → In progress → Review |

## 6. 测试矩阵（失败测试优先，先红灯后绿灯；纯确定性，无固定 sleep）

1. midpoint 公式：恰在 `midpoint_ns` 的 fix → age 0.0 可用；`midpoint_utc` 与 `(start+finish)//2`、`start+(finish-start)/2` 逐式断言；奇数 sweep 时长下取整语义；与 `TraceMetadata.sweep_midpoint_*` 端到端挂载（`with_gnss_match` 不触发 `GNSS_MIDPOINT_MISMATCH`）。
2. 前后最近邻：midpoint 后 5s vs 前 10s → 选后者；反向 → 选前者；D1 符号约定数值断言（selected.ns − mid_ns 的正负与绝对 age）。
3. 等距 tie-break：`midpoint±d` 等距 → 选较早；参数化偶/奇数 ns（整数判定）。
4. 阈值边界：恰 `stale_after_ns` → 可用（含边界）；+1 ns → stale（fix 在场）；前侧对称。
5. 窗口边界：恰 `window_ns` → stale（含边界）；+1 ns → out_of_range（fix 在场留证据）。
6. invalid 最近邻：invalid 更近 → 选 invalid、reason=invalid、fix 在场；更远 valid 不顶替；invalid 且超窗 → out_of_range（优先级固化）。
7. no_fix：空快照 → fix=None、age_s=None、reason=no_fix。
8. clock_unavailable：`shared_monotonic_domain=False` → 不匹配（即使 UTC 零距离也不 fallback——验收 3）；域声明优先于空缓存。
9. 跨 generation：重连式单调间隙两侧 fix 纯按距离匹配（generation 盲，GnssFix 无 generation 字段）。
10. 纯函数性：输入 tuple 不变、输出冻结（FrozenInstanceError）、重复调用相等、matcher 无可变状态。
11. 参数与类型校验：阈值/窗口非正/非有限、window<stale → ValueError；window==stale 退化允许（stale 带为空）；sweep 时间倒序 → ValueError；非 tuple/非 GnssFix 元素 → TypeError；naive datetime → DomainError。
12. method/status：恒 `nearest_midpoint`；`status` 派生（usable→VALID、stale→STALE、invalid→INVALID、其余→NO_FIX）；age_s 恒非负有限。
13. 回归：依赖定向（core gnss/metadata 47 + reader 40 + nmea 62）+ 全量 verify.py（1016 + 新增数 passed / 4 deselected）+ ruff + mypy（45 → 46 文件）+ import + `git diff --check` + 工作树检查。

## 7. 门禁命令（t2 完成时全绿）

```text
python3 -m pytest tests/contract/test_gnss_matcher.py -q                   # 定向（先红灯后绿灯）
python3 -m pytest tests/contract/test_gnss_matcher.py tests/contract/test_gnss_reader.py \
    tests/contract/test_nmea.py tests/unit/test_core_gnss.py tests/unit/test_core_metadata.py -q
python3 tools/quality/verify.py                                            # 全量非硬件 + ruff + mypy + import
python3 -m ruff check src tests
python3 -m mypy src
git diff --check && git status --porcelain=v1 -b
```

新基线预期：1016 + 新增非硬件测试数 passed / 4 deselected；mypy 45 → 46 文件。

## 8. 参考源审计与排除记录（ISSUE-001 manifest 白名单）

旧 UAV-GPR `uav_gpr_qt.py`（role `gnss_parser_reader_matcher`，含「sweep 匹配」白名单语义）SHA-256 = `b94d3bfc42e8bf9d30247065e7b3bef52ae52cb6d4111cf8cac52e68b1cc13d2`（ISSUE-001 manifest 冻结，权威记录）。该源在本环境不可达（E: 盘未挂载、D: 盘无副本，ISSUE-024 基线单 §3.5-2 实测；本单未重复盘扫）——本 Issue 按契约**重实现**：仅参考白名单行为语义（「GnssFixCache、GnssReader/Thread 与 sweep 匹配」中的 sweep 匹配思路：按 sweep 中点取最近 fix），已排除：地图 UI/在线 Leaflet/CDN、主窗口、NPZ/线上协议、每道 CSV/NPZ 存储、Matplotlib 实时绘图、legacy/continuous 双采集路径、接收线程协议实现。测试夹具为合成时间值/合成 fix（无参考仓库文件、无实测 NMEA）。

## 9. 性能/数据风险

- 纯函数 O(n) 单遍扫描（n=快照容量 ≤ 数百），无分配热点；无锁/无线程/无 I/O——不触碰采集或 UI 线程边界（AGENTS.md §7 不适用风险面）。
- 整数 ns 距离/阈值比较消除 float 边界歧义；`age_s` 由 ns 精确除法导出（秒级范围无精度问题）。
- 有符号差不新增持久化字段——由两个已持久化的 `MonotonicNs` 分量重建（D1），无 schema 变更；不改写 fix/metadata，无 raw/immutable 风险面。
- 回退方式：单一新模块 + 新测试文件，整体回退即删除两文件、还原 M05 L81；无 schema/协议/公共契约变更。

## 10. 执行日志（实际数字，2026-09-02 实测）

环境：WSL `python3` 3.12.3（pytest 8.4.2、ruff 0.16.4、mypy 1.20.2、numpy 2.5.2、h5py 3.16.0、pyserial 3.5），与 t1 基线单同口径。

| 步骤 | 命令 | 退出码 | 结果 |
|---|---|---|---|
| 红灯（实现前） | `python3 -m pytest tests/contract/test_gnss_matcher.py -q` | 4 | `ModuleNotFoundError: No module named 'uav_gpr.positioning.matcher'`，collection error——失败测试先行证据 |
| 绿灯（实现后首轮） | 同上 | 0 | **38 passed in 0.26s**（29 个测试函数，参数化展开 38 项；纯确定性，无 sleep/线程/串口/随机） |
| 定向+依赖回归 | `python3 -m pytest tests/contract/test_gnss_matcher.py tests/contract/test_gnss_reader.py tests/contract/test_nmea.py tests/unit/test_core_gnss.py tests/unit/test_core_metadata.py -q` | 0 | **187 passed in 0.82s**（38 新增 + 40 reader + 62 nmea + 47 core gnss/metadata） |
| 全量门禁 | `python3 tools/quality/verify.py` | 0 | pytest **1054 passed, 4 deselected in 138.17s**（= 1016 基线 + 38 新增，算术核对一致）→ ruff `All checks passed!` → mypy `Success: no issues found in 46 source files`（45 → 46，含 matcher.py）→ `package import ok` → `[quality] all gates passed` |
| ruff 显式 | `python3 -m ruff check src tests` | 0 | `All checks passed!`（首轮 2 处 RUF002 docstring EN DASH，已改 ASCII 连字符后全绿） |
| mypy 显式 | `python3 -m mypy src` | 0 | `Success: no issues found in 46 source files` |
| Git | `git diff --check && git status --porcelain=v1 -b` | 0 | diff-check clean；工作树仅含 inScope 4 路径（M05 M + matcher/tests/plan ??）+ t1 交付物基线单（??，非 t2 改动） |

执行偏差记录：

1. 首轮绿灯即通过（0 failed），实现一次成型；唯一静态检查返工为 2 处 docstring EN DASH（RUF002，ASCII 风格约定），修正后 ruff/mypy 全绿——非语义改动。
2. 门禁基线推进：1016 → **1054 passed / 4 deselected**；mypy 45 → **46 文件**；ruff/mypy/import 全绿。
3. M05 L81 状态行按流程 `Planned → In progress → Review`（两步编辑，diff 仅该行）。

## 11. D1 符号约定的使用说明（给 ISSUE-044/消费方）

- 匹配后调用方持有：`match.fix.received_monotonic_ns`（fix 接收单调时刻）与 `TraceMetadata.sweep_midpoint_monotonic_ns`（同一公式计算的 midpoint，D6）。有符号匹配差 = `fix.received_monotonic_ns.ns - sweep_midpoint_monotonic_ns.ns`；`match.age_s = |差| / 1e9`（core 冻结的非负绝对年龄）。
- 两个分量均已逐道持久化（fix 入 `.rcscan` gnss_match 嵌套对象、midpoint 入 trace_metadata），因此「保存有符号匹配差」（M05 L87/GNSS.md §5）由持久化分量重建满足——不新增 schema 字段、不改 core 契约。
