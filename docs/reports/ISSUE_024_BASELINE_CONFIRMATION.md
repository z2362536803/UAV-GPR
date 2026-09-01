# ISSUE-024 开工基线确认单（只读核查 + 实施计划摘要）

日期：2026-09-02（ISSUE-024 开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-024-nmea`（执行器 engineer，任务 t1，attempt 4aae83ff-0bef-4373-af5c-ef3df17c5339）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支、未修改实现/测试/计划文件。
配套文件：本单为 t2（GGA/RMC NMEA 解析器）与 t3（独立复审）的权威基线件；独立复审按 [ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) 执行。t2 完整实施契约见 t2 计划文档（`docs/plans/2026-09-02-issue-024-nmea.md`，由 t2 先落盘）。

## 1. 锁定的目标 Issue 与依据

**ISSUE-024：GGA/RMC NMEA 解析器**（`docs/issues/M05_GNSS.md` 第 1 个条目，状态 `Planned`，L5–40）

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-024（M05_GNSS.md L5–40） | docs/issues/README.md 依赖顺序主表 L91 |
| 直接依赖 | ISSUE-001（冻结参考项目基线与迁移清单）、ISSUE-005（GNSS、道元数据与质量状态模型） | M05 L8「直接依赖：ISSUE-001、005」；README.md L91 |
| 依赖状态 | **均已完成**：ISSUE-001/005 已合入 `main`（线性历史提交 `2c5b379`+`6b74dcf` / `952883e`+`b11e741`），复审总结 [docs/reports/ISSUE_001_005_REVIEW_SUMMARY.md](../reports/ISSUE_001_005_REVIEW_SUMMARY.md) 结论 **PASS**（L15–21）；M01 状态行仍为 `Planned` 属规划源文件口径（同文档 L63 明示，与 ISSUE-019 基线单同口径：以 tracked 代码/测试/合并提交为权威证据） | docs/reports/ISSUE_001_005_REVIEW_SUMMARY.md；git log（见 3.2） |
| 功能映射 | FR-006、ADR-0005；`GNSS.md` §2（GGA/RMC 输入与解析、checksum 验证、结构化拒绝原因）、§6（WGS84、MSL 与水准面分离、不称 AGL）、§8（测试矩阵）；`REFERENCE_MIGRATION.md` §7（GNSS 迁移：只迁纯 parser 行为、先建 NMEA 夹具、显式区分 fix/match、UTC/单调、MSL/AGL） | M05 L9；GNSS.md/REFERENCE_MIGRATION.md/ADR-0005 实测（见 3.4） |
| 提示词工作区替换 | `E:\wurenjiruanjian` → 工作区根（见第 2 节） | docs/issues/README.md 第 3 节通用协议 |
| 一次一 Issue | 本轮只处理 ISSUE-024；不进入 ISSUE-025（GNSS reader） | docs/issues/README.md 第 1/3 节；团队目标；M05 L40「报告并停止，不 commit/push」 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

ISSUE-024 参考源核查结论见 3.5-2：旧 UAV-GPR `E:\UVA_GPR_system` 在本环境**不可达**（E: 盘未挂载、D: 盘无副本），冻结哈希取自 ISSUE-001 manifest 白名单（`docs/reference-baselines/manifest.json/manifest.md`）；t2 按契约重实现 + 匿名/合成 NMEA 夹具，不做直接代码迁移。

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      main（当前；工作树完全干净）
HEAD        0b477ab  fix(acquisition): controller closing/STOPPING guard for reconnect hook (ISSUE-023 P2-1)
            完整哈希 0b477ab99654a0b0916e1fea74aaf117a59f4701（2026-09-02）
分支关系    main...origin/main = 0/0（`git rev-parse HEAD` == `git rev-parse origin/main`）
git status --porcelain=v1 -b
    ## main...origin/main
    （无其他条目——无未跟踪/未提交文件，untracked-count=0）
git diff --check    # clean（exit 0）
```

reflog 实测仅 commit/merge/checkout 记录（顶层 `0b477ab commit` ← `ab1911e commit` ← `0ec741e merge` ← `8d795d5 checkout` ← `0accd7b commit` ← `8d795d5 checkout` …），**无 reset/rebase/amend/强推迹象**。本地存在历史遗留 feature 分支（`git branch -a` 实测 `feat/issue-0xx`，均已合入 main 后的开发痕迹），当前分支为 `main`，不影响 t2。

### 3.2 直接依赖 ISSUE-001/005 的合入证据（main 内实测）

| 提交 | 内容 |
|---|---|
| `2c5b379` | `chore: freeze reference project baselines`（ISSUE-001：`docs/reference-baselines/README.md`+`manifest.json`+`manifest.md`+`spec.json`、`tools/migration/reference_manifest.py` 375 行、`tests/unit/test_reference_manifest.py` 316 行、docs 索引） |
| `6b74dcf` | `fix(issue-001): make reference manifests fail closed on path decoding`（+102/−7：严格 UTF-8 解码、不可解码 fail-closed） |
| `952883e` | `feat(core): add GNSS and trace metadata models`（ISSUE-005：`core/gnss.py`、`core/enums.py` +45、`tests/unit/test_core_gnss.py` 305 行、`tests/unit/test_core_metadata.py` 258 行） |
| `b11e741` | `fix(issue-005): enforce trace metadata integrity and GNSS consistency`（+1127/−33：raw hash 绑定/冲突规则、GNSS match 与 sweep 中点一致性、反序列化校验） |
| `e46f102` | `docs(review): summarize issues 001-005`（复审总结 PASS） |

合入方式：ISSUE-001～005 经 `feat/m01-issues-001-005` 开发后直接进入 `main` 线性历史（无独立 merge commit；后续 `0ddbd81` 为 006–007 的 PR merge），复审总结记录「已合入 main 并推送至 origin/main」（ISSUE_001_005_REVIEW_SUMMARY.md L5、L56）。

tracked 交付物（main，`git ls-files` + 实测复现）：

| 交付物 | 实测事实 | ISSUE-024 复用/依据点 |
|---|---|---|
| `tools/migration/reference_manifest.py`（375 行，ISSUE-001） | `tests/unit/test_reference_manifest.py` 实测通过；manifest 含旧 UAV-GPR 白名单（见 3.5-2） | **ISSUE-024 参考源审计依据**：按 manifest 白名单确认可参考的 GNSS parser 源文件与排除内容 |
| `docs/reference-baselines/manifest.json`（ISSUE-001） | `uav_gpr_qt.py`（role `gnss_parser_reader_matcher`，branch `my-modifications`，HEAD `194963a0472d1369a0911c24a6dacad5456158c8`，worktree dirty，`staged_and_modified`）SHA256 = `b94d3bfc42e8bf9d30247065e7b3bef52ae52cb6d4111cf8cac52e68b1cc13d2` | **冻结哈希权威记录**（本地不可达，见 3.5-2）；排除内容见 3.5-3 |
| `src/uav_gpr/core/gnss.py`（ISSUE-005） | `GnssFix`（frozen dataclass，L71）：`received_utc`/`nmea_utc`/`received_monotonic_ns`/WGS84 经纬度/`altitude_msl_m`/`geoid_separation_m`/`fix_quality`/`satellites`/`hdop`/`ground_speed_mps`/`course_deg`/`valid`/`invalid_reason`；`GnssMatch`（L247）；无 NMEA 解析、无串口、无匹配算法（模块 docstring L1–8）；`tests/unit/test_core_gnss.py` 305+47 行 | **parser 输出目标模型**：t2 parser 产出结构化 parse result 并在调用方注入接收时刻后映射 `GnssFix`（received_utc/monotonic 为接收侧事实，属 ISSUE-025 reader 职责；t2 设计点见 3.5-6） |
| `src/uav_gpr/core/metadata.py`（ISSUE-005） | `TraceMetadata`（frozen dataclass，L93）：任务/道身份、sweep 三时刻 UTC+monotonic、间隔/误差、`connection_generation`、`raw_trace_sha256`、`gnss_match: GnssMatch \| None`、质量状态/原因 | 只读消费；道级 GNSS 挂载面已冻结（ISSUE-026 才做匹配） |
| `src/uav_gpr/core/enums.py`（ISSUE-005） | `GnssStatus`（L71–79：disconnected/no_sentence/no_fix/valid/stale/invalid）、`GnssFixQuality`（L82–91：invalid/gps_fix/dgps/rtk_fixed/rtk_float/estimated/simulated）、`GnssMatchMethod`（L94）、`GnssUnavailableReason`（L100–107）、`TraceQualityStatus/Reason`（L110–130） | **GGA fix quality 整数 → 语义枚举映射面**（semantic, not raw integer，L83 注释） |
| `tests/unit/test_core_gnss.py` + `test_core_metadata.py` + `test_reference_manifest.py` | 定向复跑实测 **60 passed**（见第 4 章） | ISSUE-001/005 测试证据复现 |
| `docs/issues/M01_GOVERNANCE_CORE.md` L5/L153 + `docs/reports/ISSUE_001_005_REVIEW_SUMMARY.md` | 状态行 `Planned`（规划源文件口径，L63 明示）；复审总结 ISSUE-001/005 结论 `PASS`（L17/L21） | 依赖完成证据（与 ISSUE-019 基线单同口径） |

### 3.3 ISSUE-024 为下一个可执行 Issue

- M05 L5–8：ISSUE-024 状态 `Planned`（L7）；`直接依赖：ISSUE-001、005`（L8）——两项依赖均已合入 main（见 3.2）。
- docs/issues/README.md L91：`024 | GGA/RMC NMEA 解析器 | 001, 005`——依赖表无计划冲突；ISSUE-023 已 Blocked（等待真机）合入模拟部分（`ab1911e`，M04 L155），ISSUE-024 不依赖 023；**ISSUE-024 是当前唯一可执行 Issue**，一次只执行一个。
- 落点核查：`src/uav_gpr/positioning/` 仅 `__init__.py`（docstring「GNSS parsing, fix models, readers, and trace-position matching.」占位）+ `__pycache__`，**无 parser 实现**；`src/` 全树 grep `NMEA|GGA|RMC|nmea|checksum` 无 parser（仅 core 模型字段 `nmea_utc`、storage 布局 `/gnss/nmea_utc_ns`、`/gnss/raw_nmea`、enums 注释、`acquisition/backend.py` L704 占位 `nmea_utc=None`）；`docs/plans/` 无任何 issue-024 文件（计划文档只到 issue-023）；`docs/reports/` 无 `ISSUE_024_*` 文件——t2 是首个引入 NMEA parser 的实现点。

### 3.4 对 ISSUE-024 有约束的契约要点（读自 M05 L5–40、GNSS.md、REFERENCE_MIGRATION.md §7、ADR-0005、ISSUE_001_005_REVIEW_SUMMARY.md、ISSUE_REVIEW_STANDARD.md、AGENTS.md、团队目标）

**ISSUE-024 范围（M05 L15–21）+ 提示词（L34–40）**：

1. **参考源审计 + 匿名夹具**——按 I001 manifest 审计旧 UAV-GPR GNSS 来源并**冻结匿名 NMEA 夹具**；只参考白名单中的 UAV-GPR GNSS parser/测试思路，记录源 SHA256 和排除内容（本环境源不可达，见 3.5-2，夹具用匿名/合成语句）。
2. **talker-independent GGA/RMC**——支持不同 talker（GP/GL/GA/GN 等），校验 checksum/长度/范围；解析 lat/lon（南/西半球）、fix、sats、HDOP、**MSL/geoid 分开**、**knots→m/s**、course。
3. **RMC 日期与 GGA 日内时间组合**——跨午夜策略、无日期处理和结构化 parse result/error（GNSS.md §2：不能把本机接收时间冒充 NMEA 时间；跨午夜、无日期和系统钟跳变必须测试）。
4. **最大行长和非 ASCII/空字段保护**——空字段保持空而非 0（M05 L29；GNSS.md §8 L76「空字段不生成 0 坐标」）。

**排除项（M05 L23–24，t2 不得越界）**：不读串口、不缓存、不做 trace 匹配、不迁移地图。

**验收标准（M05 L26–30 原文，t2 不得削弱）**：

1. 南/西半球、跨午夜、坏 checksum、无 fix、越界字段结果明确。
2. GGA MSL 不标为 AGL；无字段保持空而非 0。
3. parser 无 Qt/serial 依赖。

**其他约束**：

5. GNSS.md §6：经纬度为 WGS84 十进制度；GGA 高程按 NMEA 定义保存为 **MSL**，水准面分离单独保存；**不把 MSL 高程称为无人机离地高度（AGL）**；未来 AGL 需单独来源和时间匹配模型。
6. GNSS.md §8 测试矩阵：正常、南/西半球、空字段、坏 checksum、越界、不同 talker ID；跨午夜、RMC 日期缺失、乱序语句；MSL/AGL 不混用，空字段不生成 0 坐标（串口拆行/高频/重连为 ISSUE-025 范围，ISSUE-024 不做）。
7. REFERENCE_MIGRATION.md §7：只迁移纯 parser/reader/matcher 行为，不带 Qt map/widget；先为旧行为建立 NMEA 夹具，再修复已知时区、过期或关闭问题；新模型显式区分 fix 与 trace match、UTC 与单调时间、MSL 与 AGL；不因「旧项目现场用过」省略错误测试。
8. ADR-0005：旧 UAV-GPR 生产代码只对白名单 GNSS parser/reader/matcher 开放；每次迁移记录 branch/HEAD/status、源文件 SHA256、目标模块和测试；**两个参考仓库的脏工作树不能被当作稳定版本，必须按文件冻结**；本项目绝不修改两个参考目录。
9. 团队目标沿用教训：纯函数式 parser（不读串口/不缓存/不匹配 trace/不迁移地图）；**空字段保持空而非 0**；**GGA MSL 不得标为 AGL**；parser 无 Qt/serial 依赖；t2 inScope 一律用精确文件路径（非 glob），**changedPaths 与 inScope 逐一相等**；一次只执行一个 Issue，不进入 ISSUE-025。
10. 测试纪律：先写能失败的测试再最小实现（AGENTS.md §10；README.md 第 3 节协议 4）；每项能力覆盖正常、错误、取消/恢复路径；禁删测试/降断言/吞异常；禁固定 sleep 猜时序；`tests/unit/test_no_external_access.py` AST 守卫禁止默认测试 import `serial/usb/socket/websocket` 根——parser 无串口依赖与此守卫互相印证。
11. 文档权威顺序（AGENTS.md §12）：当前任务验收条件 > AGENTS.md > 已接受 ADR > 专题设计文档 > 参考项目代码/文档。若组合/跨午夜策略与 GNSS.md 冲突，先更新计划或写 ADR，不能只在代码中形成事实。

### 3.5 发现的事实（非阻塞，供 t2 注意）

1. 工作树完全干净且与远端同步（`## main...origin/main` 0/0，HEAD `0b477ab`）；t2 交付物将是唯一新改动。
2. **参考源本地不可达**：`E:\UVA_GPR_system` 在 WSL 未挂载（`/mnt/e` 为空）、Windows 侧不可验证；`/mnt/d/博士任务/` 全树 find 实测**无** `uav_gpr_qt.py` 或 UVA_GPR_system 副本（与 ISSUE-019/023 基线单「E: 盘不可达不影响迁移」一致）。冻结哈希 `b94d3bfc…` 来自 ISSUE-001 manifest（fail-closed 工具生成，权威记录）。→ **t2 无直接代码迁移来源**：按 M05/GNSS.md 契约重实现 + 匿名/合成 NMEA 夹具（与 M05 提示词「用匿名/合成 NMEA 覆盖正常、坏 checksum、无 fix、空字段、越界、跨午夜和非 ASCII」完全一致），并在计划文档记录源哈希与排除内容。
3. **manifest 白名单与排除内容**（`docs/reference-baselines/manifest.md` L100–172 / `manifest.json` L371–385）：`uav_gpr_qt.py` 白名单仅开放「GGA/RMC 解析、GnssFixCache、GnssReader/Thread 与 sweep 匹配」；**排除**：`uav_gpr_qt.py` 内其余全部代码、`gnss_map_widget.py`（在线 Leaflet/CDN 地图，禁止迁移）、`uav_gpr_ground_station.py`/`uav_gpr_receiver.py`（主窗口与 NPZ/线上协议）、`bscan_view.py`/`gpr_processing.py`、`acquisition_scheduler.py`、`librevna/`、现场实测数据、tests/（迁移时另行记录夹具来源）、run_*.bat/日志/缓存、数据处理函数/、非 HM30 docs。
4. M01 状态行 ISSUE-001/005 仍为 `Planned`：规划源文件口径（ISSUE_001_005_REVIEW_SUMMARY.md L63），实际交付证据以复审总结 + main 内提交/代码/测试为准——与 ISSUE-019 基线单处理方式一致。
5. 门禁基线数字（第 4 章）：全量 **914 passed / 4 deselected**（918 collected；ISSUE-023 合入后新增非硬件测试 + 硬件 opt-in 项），t2 完成后新基线 = 914 + 新增非硬件测试数，不得出现既有测试失败。
6. **t2 设计点（不在此决策）**：`GnssFix` 强制要求 `received_utc`/`received_monotonic_ns`（接收侧事实）与 `valid`/`invalid_reason` 一致性——纯 parser 宜输出独立结构化 parse result（含 `nmea_utc`、lat/lon、fix quality 语义、sats、HDOP、MSL、geoid、速度/航向），由调用方（ISSUE-025 reader）注入接收时刻映射为 `GnssFix`；parser 模块不得自行伪造接收时刻。

## 4. 门禁基线（核查时实测复跑，2026-09-02）

环境：WSL Ubuntu / Python 3.12.3；numpy 2.5.2、pytest 8.4.2、h5py 3.16.0、ruff 0.16.4、mypy 1.20.2；`uav_gpr` editable 可导入（`src/`）；解释器路径 `python3`（与 ISSUE-019/020/021/022/023 基线单同口径）。

```text
$ python3 -m pytest tests/unit/test_reference_manifest.py tests/unit/test_core_gnss.py tests/unit/test_core_metadata.py -q
60 passed in 0.90s                                  # ISSUE-001 manifest + ISSUE-005 GNSS/metadata 定向

$ python3 tools/quality/verify.py
914 passed, 4 deselected in 134.78s (0:02:14)   # 全量非硬件 pytest（预期 914 达成）
All checks passed!                              # ruff（check .）
Success: no issues found in 43 source files     # mypy（43 文件）
package import ok                               # import 检查
[quality] all gates passed                      # verify.py 全部通过（exit 0）

$ python3 -m ruff check src tests                # 补充显式复跑
All checks passed!                               # exit 0
$ python3 -m mypy src                            # 补充显式复跑
Success: no issues found in 43 source files      # exit 0
$ python3 -c "import uav_gpr"                    # exit 0（package import ok）
$ git diff --check                               # exit 0

$ python3 -m pytest --collect-only -q -m "not hardware and not slow" --no-header
914/918 tests collected (4 deselected) in 0.79s   # deselected = hardware/slow 标记项
```

核查前后 `git status --porcelain=v1 -b` **逐字节一致**（仅 `## main...origin/main` 一行；untracked-count=0）；`git diff --check` clean；`.pytest_cache/`/`.mypy_cache/`/`.ruff_cache/` 均 `git check-ignore` 确认已忽略，无新缓存/日志/实测数据残留（本基线单为唯一新增未跟踪文件）。

## 5. ISSUE-024 实施计划摘要（供 t2 契约；完整版见 t2 计划文档，不含实现代码）

### 范围（M05 L15–21 原文口径 + 提示词）

1. 新建 `src/uav_gpr/positioning/nmea.py`：纯函数式 GGA/RMC parser——talker-independent、checksum/长度/范围校验、南/西半球 lat/lon、fix quality 语义映射（→ `GnssFixQuality`）、sats/HDOP、**MSL/geoid 分开**、knots→m/s、course、RMC 日期 + GGA 日内时间组合与跨午夜策略、结构化 parse result/error、最大行长与非 ASCII/空字段保护（空字段保持 None/空）；**无 Qt/serial 依赖、不读串口/不缓存/不匹配 trace/不迁移地图**；产出在调用方注入接收时刻后映射 `GnssFix`（core 只读消费）。
2. 新建 parser 契约测试（失败测试优先，先红灯后绿灯）：正常/南西半球/跨午夜/坏 checksum/无 fix/空字段/越界/不同 talker/非 ASCII/超长行/结构化错误 + AST 守卫兼容（默认测试不 import serial/usb/socket）。
3. 匿名/合成 NMEA 夹具（内联于测试或独立 fixtures 文件，不引入参考仓库文件；若独立成文件须列入 t2 契约 inScope）。
4. `docs/plans/2026-09-02-issue-024-nmea.md`（计划文档，t2 先落盘：设计决策（组合/跨午夜策略、结构化错误码、parse result 形状、GnssFix 映射边界）、参考源哈希与排除记录、执行日志、门禁数字）。
5. `docs/issues/M05_GNSS.md` 仅 ISSUE-024 状态行（L7）：`Planned → In progress → Review → Done`（合入后由 captain/负责人标记），勿动其他条目。

### 排除项（M05 L23–24 + 提示词，t2 不得越界）

不读串口、不缓存、不做 trace 匹配、不迁移地图；不实现 GNSS reader/重连/缓存（ISSUE-025）与 sweep 匹配（ISSUE-026）；不改 `core/**`（`GnssFix`/`GnssMatch`/`TraceMetadata`/enums 只读消费；若确需改 core 必须先向 captain 报告并考虑 ADR）；不改两个参考仓库；不 commit/push/merge、不创建/切换分支；不新增范围外文件（若确需拆分/新增，t2 先停止向 captain 报告）。

### 验收标准（M05 L26–30 原文，t2 不得削弱）

1. 南/西半球、跨午夜、坏 checksum、无 fix、越界字段结果明确。
2. GGA MSL 不标为 AGL；无字段保持空而非 0。
3. parser 无 Qt/serial 依赖。

### 测试矩阵要点（提示词必测项 + 依赖回归）

- GGA/RMC 正常路径：UTC/经纬度/fix quality/sats/HDOP/MSL/geoid；RMC 日期/状态/经纬度/knots→m/s/course；
- 南纬/西经半球符号；不同 talker ID（GP/GL/GA/GN 等）talker-independent；
- 跨午夜（RMC 日期 + GGA 日内时间组合、日期翻转）、RMC 日期缺失、乱序语句；
- 坏 checksum（结构化拒绝原因）；无 fix（GGA fix quality 0 → 无效 fix + `no_fix` 原因语义）；空字段保持 None/空（不产生 0 坐标/0 速度）；越界 lat/lon/sats/HDOP/course；
- 非 ASCII/超长行保护（最大行长）；结构化 parse result/error 形状与可序列化性；
- MSL ≠ AGL 断言（`altitude_msl_m` 语义）；parser 模块及测试不 import Qt/serial（AST 守卫）；
- 回归：core gnss/metadata 定向 + 全量 `python3 tools/quality/verify.py`（914 + 新增数 passed / 4 deselected）+ ruff + mypy（43 文件）+ import + `git diff --check` + 工作树检查；测试禁固定 sleep（纯函数无时序）。

### inScope 精确路径建议（以任务契约 t2 inScope 为准；t2 完成登记 changedPaths 必须与此逐一相等；沿用 ISSUE-021/022/023 教训：精确文件路径，非 glob）

1. `src/uav_gpr/positioning/nmea.py`（新模块：纯函数式 GGA/RMC parser）
2. `tests/unit/test_nmea_parser.py`（新文件：parser 契约测试 + 匿名 NMEA 夹具；若夹具独立成文件须在契约中列出）
3. `docs/plans/2026-09-02-issue-024-nmea.md`（新计划文档，t2 先落盘，含设计决策与门禁数字）
4. `docs/issues/M05_GNSS.md`（仅 ISSUE-024 状态行 L7，勿动其他条目）

（t1 基线单 `docs/reports/ISSUE_024_BASELINE_CONFIRMATION.md` 为 t1 交付物，不计入 t2 inScope；t2 outOfScope：`docs/reports/**`、`docs/GNSS.md`、`docs/REFERENCE_MIGRATION.md`、`docs/reference-baselines/**`、`docs/adr/**`、`src/uav_gpr/core/**`（只读消费）、`src/uav_gpr/positioning/__init__.py`（如需扩展须先向 captain 报告）、参考仓库（只读）。）

t2 验证命令按任务契约执行：`python3 -m pytest <定向测试> -q`（先红灯后绿灯）、`python3 tools/quality/verify.py`（全量）、`python3 -m ruff check src tests`、`python3 -m mypy src`、`git diff --check && git status --porcelain=v1 -b`（同 ISSUE-018/019/020/021/022/023 口径，WSL 侧 `python3`）。

若确需拆分模块/新增文件，t2 必须先停止并向 captain 报告，不得自行新增范围外文件。

## 6. 结论

ISSUE-024 开工基线已锁定：`main`/HEAD @ `0b477ab`（工作树完全干净、与 origin/main 同步 0/0、reflog 无破坏性操作）；直接依赖 **ISSUE-001/005 均已合入 main**（`2c5b379`+`6b74dcf`、`952883e`+`b11e741`；复审总结 VERDICT=PASS；manifest 工具 + `GnssFix`/`GnssMatch`/`TraceMetadata`/enums 代码与测试实测复现，定向 60 passed）；**ISSUE-024 是当前唯一可执行 Issue**（M05 状态行 `Planned`、`positioning/` 无 parser 实现、无实现/测试/计划存在、依赖全绿、不依赖已 Blocked 的 ISSUE-023）；**参考源核查：旧 UAV-GPR GNSS parser 源（`uav_gpr_qt.py`）本环境不可达**（E: 盘未挂载、D: 盘无副本），冻结 SHA-256 `b94d3bfc42e8bf9d30247065e7b3bef52ae52cb6d4111cf8cac52e68b1cc13d2` 与排除内容（仅 GGA/RMC 解析、GnssFixCache、GnssReader/Thread、sweep 匹配白名单；地图/主窗口/协议/存储/处理全部禁止）取自 ISSUE-001 manifest——t2 按契约重实现 + 匿名/合成 NMEA 夹具，不做直接代码迁移；契约要点（talker-independent、checksum/长度/范围校验、南西半球、knots→m/s、RMC 日期 + GGA 时间组合与跨午夜策略、MSL/geoid 分开且 MSL 不称 AGL、空字段保持空而非 0、最大行长与非 ASCII 保护、结构化 parse result/error、无 Qt/serial 依赖、4 个精确 inScope 路径建议）已固化于第 3.4/3.5/5 节；门禁基线全绿（全量 **914 passed / 4 deselected**（918 collected）、ruff/mypy（43 文件）/import 全过、依赖定向 60 passed），核查前后 git 状态一致、无残留。**无阻塞项，可开工。** 本确认单即为 t2 实现与 t3 独立复审的权威基线件；t2 按第 5 节契约执行（先落盘计划文档 → 失败测试优先 → 最小实现 → M05 状态行 → 门禁 → 报告），完成后停止，不进入 ISSUE-025。

> 后续记录：本单为开工时点的基线快照，不随 t2 改动；t2 实际完成记录见其完成报告与 `docs/plans/2026-09-02-issue-024-nmea.md`，t3 复审报告独立输出。
