# ISSUE-025 开工基线确认单（只读核查 + 实施计划摘要）

日期：2026-09-02（ISSUE-025 开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-025-gnss-reader`（执行器 engineer，任务 t1，attempt a42c467e-1ba4-47b0-8829-0de5a7c35be8）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支、未修改实现/测试/计划文件（本单为唯一新增文件）。
配套文件：本单为 t2（GNSS reader、重连与有界 fix 缓存）与 t3（独立复审）的权威基线件；独立复审按 [ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) 执行。t2 完整实施契约见 t2 计划文档（建议 `docs/plans/2026-09-02-issue-025-gnss-reader.md`，由 t2 先落盘）。

## 1. 锁定的目标 Issue 与依据

**ISSUE-025：GNSS reader、重连与有界 fix 缓存**（`docs/issues/M05_GNSS.md` 第 2 个条目，状态 `Planned`，L42–77）

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-025（M05_GNSS.md L42–77） | docs/issues/README.md 依赖顺序主表 L92 |
| 直接依赖 | ISSUE-005（GNSS、道元数据与质量状态模型）、ISSUE-024（GGA/RMC NMEA 解析器） | M05 L45「直接依赖：ISSUE-005、024」；README.md L92 |
| 依赖状态 | **均已完成并合入 main**：ISSUE-005（`952883e`+`b11e741`，复审总结 PASS）；ISSUE-024（`f22affd` feat + `315a7a8` merge + `ddf2a1a` Done 标记，独立复审 VERDICT=PASS，见 [docs/reports/ISSUE_024_REVIEW_REPORT.md](../reports/ISSUE_024_REVIEW_REPORT.md)；M05 L7 状态行 Done，注明「1 项 P3 非阻塞，可随 ISSUE-025 顺手关闭」） | git log 实测（见 3.2）；M05 L7；两份复审报告 |
| 功能映射 | FR-006、FR-018；`GNSS.md` §3（读取和重连：独立 worker、增量按行、最大行长、错误计数继续、退避重连、generation、线程安全有界 fix 缓存、关闭可取消）、§4（六态有效性：disconnected/no_sentence/no_fix/valid/stale/invalid）、§8 测试矩阵（断开/重连/停止/长行攻击/高频输入有界缓存、串口拆行）；`ROADMAP.md` Phase 5（交付：parser、reader/reconnect、fix cache、midpoint matcher；门禁：跨午夜/stale/断线/真实 GNSS 记录回放）；ADR-0005 | M05 L46；GNSS.md/ROADMAP.md/ADR-0005 实测（见 3.4） |
| 提示词工作区替换 | `E:\wurenjiruanjian` → 工作区根（见第 2 节） | docs/issues/README.md 第 3 节通用协议 |
| 一次一 Issue | 本轮只处理 ISSUE-025；不进入 ISSUE-026（sweep midpoint 匹配器，M05 L79–113 仍 Planned） | docs/issues/README.md 第 1/3 节；团队目标；M05 L76「报告并停止，不 commit/push」 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

与 ISSUE-024 相同：旧 UAV-GPR 参考源 `E:\UVA_GPR_system` 在本环境**不可达**（E: 盘未挂载、D: 盘无副本，ISSUE-024 基线单 §3.5-2 已实测）；冻结哈希取自 ISSUE-001 manifest 白名单（`docs/reference-baselines/manifest.json/manifest.md`，`uav_gpr_qt.py` SHA-256 `b94d3bfc…`，白名单含「GGA/RMC 解析、GnssFixCache、GnssReader/Thread 与 sweep 匹配」）。t2 按契约重实现 + 合成 NMEA 夹具，不做直接代码迁移（本单未重复执行盘扫描，沿用 ISSUE-024 基线单实测结论）。

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      main（当前；工作树完全干净）
HEAD        ddf2a1a docs(issues): mark ISSUE-024 Done after automated authorized merge
            完整哈希 ddf2a1af8b37b6eb9749bed18b99ce785334c3ed（2026-09-02）
分支关系    HEAD == origin/main == ddf2a1af…（`git rev-parse HEAD origin/main` 相等，0/0）
git status --porcelain=v1 -b
    ## main...origin/main
    （无其他条目——无未跟踪/未提交文件）
git diff --check    # clean（exit 0）
```

reflog 实测仅 commit/merge/checkout 记录（顶层 `ddf2a1a commit` ← `315a7a8 merge` ← `0b477ab checkout` ← `f22affd commit` ← `0b477ab checkout` ← `0b477ab commit` …），`git reflog | grep -iE "reset|rebase|amend|filter"` **为空——无破坏性操作迹象**。本地历史 feat/issue-009…024 分支均为已合入 main 后的开发痕迹（与 ISSUE-024 基线单口径一致），当前分支 `main`，不影响 t2。

### 3.2 直接依赖 ISSUE-005/024 的合入证据（main 内实测）

| 提交 | 内容 |
|---|---|
| `952883e` | `feat(core): add GNSS and trace metadata models`（ISSUE-005：`core/gnss.py`、`core/enums.py`、`tests/unit/test_core_gnss.py`、`tests/unit/test_core_metadata.py`） |
| `b11e741` | `fix(issue-005): enforce trace metadata integrity and GNSS consistency`（+1127/−33：raw hash 绑定/冲突规则、GNSS match 与 sweep 中点一致性、反序列化校验） |
| `f22affd` | `feat(positioning): GGA/RMC NMEA parser (ISSUE-024)`（在 `feat/issue-024` 分支） |
| `315a7a8` | `Merge feat/issue-024: ISSUE-024 GGA/RMC NMEA parser`（'ort' 策略合入 main） |
| `ddf2a1a` | `docs(issues): mark ISSUE-024 Done after automated authorized merge`（M05 L7 状态行 → Done） |

tracked 交付物（main，实测复现）：

| 交付物 | 实测事实 | ISSUE-025 复用/依据点 |
|---|---|---|
| `src/uav_gpr/core/gnss.py`（ISSUE-005，375 行） | `GnssFix`（frozen dataclass slots，L70–211）：`received_utc`/`nmea_utc`/`received_monotonic_ns`/WGS84 经纬度/`altitude_msl_m`/`geoid_separation_m`/`fix_quality`/`satellites`/`hdop`/`ground_speed_mps`/`course_deg`/`valid`/`invalid_reason`，`__post_init__` 全量 fail-closed 校验；`GnssMatch`（L246–375） | **reader/缓存发布的不可变对象**：缓存只能存 `GnssFix`（frozen，天然 snapshot 安全）；reader 注入接收侧事实后经 parser `assemble_gnss_fix` 构造 |
| `src/uav_gpr/core/enums.py`（ISSUE-005） | `GnssStatus`（L70–79：**disconnected/no_sentence/no_fix/valid/stale/invalid 六态与 M05 L55、GNSS.md §4 完全一致，reader 状态发布面已冻结**）；`GnssFixQuality`（L82–91）；`GnssMatchMethod`（L94）；`GnssUnavailableReason`（L100–107：no_fix/stale/invalid/clock_unavailable/out_of_range）；`TraceQualityStatus/Reason` | reader 状态发布直接用 `GnssStatus`；**core 只读消费，t2 不改 enums**（六态已齐，无需新增） |
| `src/uav_gpr/core/timeutil.py`（ISSUE-003/005，132 行） | `MonotonicNs`（frozen，L24–53）；`Clock` Protocol（L90–100：utc_now+monotonic_ns）；`SystemClock`（L103）；`ManualClock`（L113，advance_utc/advance_monotonic） | **reader 时钟注入点已存在**：reader 构造注入 `Clock`，测试注入虚拟时钟，杜绝假时间 |
| `src/uav_gpr/core/metadata.py`（ISSUE-005） | `TraceMetadata` 含 `connection_generation`、`gnss_match: GnssMatch \| None` | 只读消费；generation 语义与采集侧对齐（ISSUE-026 匹配时挂载） |
| `src/uav_gpr/positioning/nmea.py`（ISSUE-024，700 行） | 纯函数 `parse_nmea`（L188–260：checksum/行长 256/ASCII/范围校验，结构化 `NmeaError`/`NmeaErrorReason`）；`combine_nmea_utc`（L263–306，12h 跨午夜容差）；`assemble_gnss_fix`（L309–361，**docstring L20–22 明示 received_utc/monotonic 由调用方（ISSUE-025 reader）注入**）；`MAX_NMEA_LINE_LEN=256`（L38） | **parser 集成面已冻结**：reader 增量拆行→`parse_nmea`→GGA/RMC 配对→注入接收时刻→`assemble_gnss_fix`；行长上限复用 256 |
| `tests/contract/test_nmea.py`（497 行） | ISSUE-024 定向 60 tests | 回归基线；合成 NMEA 夹具风格延续 |
| `tests/unit/test_no_external_access.py`（86 行） | AST 守卫：默认测试禁 import `serial/usb/socket/requests/urllib/http/websocket` 根（L20–65）；`tests/hardware/` 唯一豁免（L77–86） | **t2 硬约束**：reader 测试不得 import serial——fake serial adapter 注入；真 serial 适配器只能在 `src/`（pyserial 3.5 已安装可导入，`[project.dependencies]` L25 已声明） |
| `tests/conftest.py` | `VirtualClock`/`virtual_clock`（确定性 UTC+单调时钟）、确定性随机种子、TZ=UTC/offscreen | 时序测试基础设施就绪（no fixed sleep） |
| `src/uav_gpr/acquisition/librevna/reconnect.py`（ISSUE-023，186 行） | `LibreVnaReconnectPolicy`：确定性指数退避 `initial_delay_s * factor^(n-1)` 封顶 `max_delay_s`、无抖动、`frozen` dataclass；backoff 期间可被 close/stop 打断 | **重连/退避仓内先例**：ISSUE-025 退避策略沿用同风格（确定性、可注入、可取消），generation 语义参照 `AcquisitionController.connection_generation`（`acquisition/controller.py` L290/L389） |
| `docs/reports/ISSUE_024_REVIEW_REPORT.md` | VERDICT=PASS（L10–12）；P3-1（L30–34）：`_parse_coordinate` 未按轴限定半球字母（lat 应限 N/S、lon 应限 E/W），非阻塞，最小修复 ≤10 行+1 条失败测试 | **P3-1 随 ISSUE-025 顺手关闭的可选决策点**（M05 L7 已预留）：是否纳入 t2 inScope 由 captain 在 t2 契约中明确 |

### 3.3 ISSUE-025 为下一个可执行 Issue

- M05 L42–45：ISSUE-025 状态 `Planned`（L44）；`直接依赖：ISSUE-005、024`（L45）——两项依赖均已合入 main 且复审 PASS（见 3.2）。
- docs/issues/README.md L92：`025 | GNSS reader、重连与有界 fix 缓存 | 005, 024`——依赖表无计划冲突；**ISSUE-025 是当前唯一可执行 Issue**（ISSUE-024 Done、ISSUE-026 依赖 025 未开工），一次只执行一个。
- 落点核查：`src/uav_gpr/positioning/` 仅 `__init__.py`（占位 docstring「GNSS parsing, fix models, readers, and trace-position matching.」）+ `nmea.py`；src/tests 全树 grep `GnssFixCache|fix_cache|GnssReader|SerialAdapter` **零命中——无 reader/重连/缓存实现**；`docs/plans/` 无 issue-025 文件（计划文档到 issue-024 为止）；`docs/reports/` 无 `ISSUE_025_*` 文件——t2 是首个引入 GNSS reader/缓存的实现点。
- ROADMAP Phase 5（L35–38）交付四件套中 parser 已完成（ISSUE-024），reader/reconnect + fix cache 即本 Issue，midpoint matcher 属 ISSUE-026。

### 3.4 对 ISSUE-025 有约束的契约要点（读自 M05 L42–77、GNSS.md §3/§4/§8、ARCHITECTURE §3/§6、ROADMAP Phase 5、REFERENCE_MIGRATION §7、ADR-0005、ISSUE_REVIEW_STANDARD.md、AGENTS.md、团队目标）

**ISSUE-025 范围（M05 L52–57 原文 + 提示词 L69–77）**：

1. **Serial adapter 注入 + 增量按行读取**——可注入 `SerialAdapter` 的 GnssReader worker；增量拆行（任意拆行/合行都要正确）、行长限制（parser `MAX_NMEA_LINE_LEN=256`）、timeout、调用 ISSUE-024 parser；默认测试不打开真实 COM 口。
2. **六态状态发布**——`disconnected`/`no_sentence`/`no_fix`/`valid`/`stale`/`invalid`（`GnssStatus` 已冻结六态；发布不可变状态对象；GNSS.md §4：地图只用 valid，其他状态原样保存原因）。
3. **退避重连 + generation + 结构化指标 + 幂等 stop/close**——I/O 错误按有界退避重连并每次重连增加 generation（确定性退避，仓内先例 `LibreVnaReconnectPolicy`；解析错误计数并继续，连续 I/O 错误才触发重连——GNSS.md §3）；stop 能取消阻塞读取、幂等释放端口、线程退出。
4. **按时间/容量双上限的 thread-safe snapshot 缓存**——不可变 fix snapshot；snapshot 不暴露可写内部状态（返回 frozen `GnssFix` 的只读副本/元组）；超限淘汰有明确策略。

**排除项（M05 L59–61，t2 不得越界）**：不做 sweep 匹配（ISSUE-026）、不渲染地图、不让串口错误停止雷达采集（GNSS 错误只上报）。

**验收标准（M05 L63–67 原文，t2 不得削弱）**：

1. 拆行/合行、高频输入、断开重连和关闭无死锁/泄漏。
2. 缓存有界且 snapshot 不暴露可写内部状态。
3. 默认测试不打开真实 COM 口。

**其他约束**：

5. ARCHITECTURE §6 并发边界：**GNSS worker 独立线程**（串口读取、解析、fix 发布）；线程间只传不可变对象或所有权清晰的缓冲；关闭顺序保证取消阻塞读取→端口释放→线程退出。
6. ARCHITECTURE §3：`positioning` 层定义 GNSS fix、NMEA 解析、独立读取、重连、缓存和道位置匹配，**不渲染地图**；reader 无 Qt 依赖（core 同源规则：positioning 不依赖 UI/硬件窗口）。
7. GNSS.md §3：解析错误计数并继续；连续 I/O 错误触发有退避的重连；每次重连增加 generation；发布状态不阻塞雷达采集。
8. GNSS.md §8 测试矩阵中 ISSUE-025 相关项：串口拆行、断开、重连、停止、长行攻击、高频输入下的有界缓存。
9. AGENTS.md §5/§7：串口读取不得阻塞采集或 UI；队列/缓存必须有上限与背压；MSL/椭球高/AGL 不混用（reader 只透传 parser 产出的 MSL 字段，无推算）。
10. 团队目标沿用教训：t2 inScope 一律精确文件路径（非 glob），完成登记 changedPaths 必须与 inScope 逐一相等；测试用 fake serial、默认不打开真实 COM 口、不用固定 sleep（用 `threading.Event`/虚拟时钟/注入调度）；GNSS 错误只上报不停止雷达采集；缓存双上限有界且 snapshot 不可写。
11. 文档权威顺序（AGENTS.md §12）：当前任务验收条件 > AGENTS.md > 已接受 ADR > 专题设计文档 > 参考项目代码/文档。若 reader 状态/stale 判定与 GNSS.md 有实现歧义，先更新计划或 ADR，不能只在代码中形成事实。
12. `DATA_MODEL.md` §7 GNSS 模型：`GnssFix` 与 `GnssMatch` 分开——fix 携带 `received_utc`、可选 `nmea_utc`、**接收单调时钟**、经纬度、MSL/水准面、fix_type、sats、HDOP、可选速度/航向、`valid` 与结构化 `invalid_reason`、可选原始 NMEA 引用（reader 发布的每个 fix 必须是此形状，接收时刻为接收侧事实）；§6（L54）：MissionConfig 契约字段含「**GNSS 最大年龄和无 fix 策略**」——stale 阈值的配置来源有既定挂载面（t2 设计点 3 的候选权威来源）；L189：GNSS 缺失、无效和过期**通常是数据状态，不一定抛异常**——reader 六态发布+计数指标与该口径一致，禁止把解析/串口错误升级为采集停止异常。

### 3.5 发现的事实（非阻塞，供 t2/captain 注意）

1. 工作树完全干净且与远端同步（`## main...origin/main` 0/0，HEAD `ddf2a1a`）；t2 交付物将是唯一新改动。
2. **AST 守卫与 serial 注入设计**（`tests/unit/test_no_external_access.py` L20–65）：守卫只扫描测试模块自身 AST import，不扫描传递 import——reader 测试 import `uav_gpr.positioning.<reader 模块>` 合规；真 serial 适配器在 `src/` 内 import pyserial（依赖已装）。**t2 设计点**：`src` 内 serial import 采用独立模块或惰性 import，避免非硬件路径在模块加载时强依赖串口设备探测（pyserial import 本身无设备副作用，风险低）；fake serial 适配器实现与真适配器同一 Protocol。
3. **stale 判定语义**（GNSS.md §4/§5）：§4 的 `stale` 是「存在旧 fix 但相对**道时刻**超过阈值」，而道匹配属 ISSUE-026；reader 侧的 stale 是「连接着但已 `stale_after_s` 未收到新 fix」的观测态。阈值来源：`DATA_MODEL.md` §6 MissionConfig 契约已含「GNSS 最大年龄和无 fix 策略」字段挂载面（reader 构造参数 vs MissionConfig 字段）与 reader 侧 stale/last-fix-age 指标的具体形状是 **t2 设计点（建议在 t2 计划文档 D 决策中固化，避免与 ISSUE-026 匹配阈值冲突）**。
4. **ISSUE-024 P3-1 顺手关闭的可选窗口**：M05 L7 明示 P3-1「可随 ISSUE-025 顺手关闭」（`nmea.py` `_parse_coordinate` 增加按轴半球集合参数 + `tests/contract/test_nmea.py` 1 条失败测试，≤10 行）。**是否纳入 ISSUE-025 inScope 由 captain 在 t2 契约中明确**（若纳入，inScope 增加 `src/uav_gpr/positioning/nmea.py` 与 `tests/contract/test_nmea.py` 两个精确路径，changedPaths 相应增加；若不纳入，P3-1 保持 open，ISSUE-025 复审时仍按已知非阻塞项记录）。
5. **GGA/RMC 配对窗口**（parser `combine_nmea_utc` 依赖最近一次有效 RMC 日期）：reader 维护「最近有效 RMC + 最近 GGA」的配对状态（含跨午夜 12h 容差已由 parser 处理），配对缓冲的容量/时效边界是 t2 设计点；仅 GGA 或仅 RMC 的接收机降级路径需明确（`assemble_gnss_fix` 仅接受 GGA）。
6. **门禁基线数字**（第 4 章）：全量 **974 passed / 4 deselected**（978 collected；ISSUE-024 合入后 914→974，+60 非硬件测试），ruff 全绿、mypy **44 文件**全绿、import 全绿；t2 完成后新基线 = 974 + 新增非硬件测试数，不得出现既有测试失败。
7. t2 验证解释器沿用基线同口径：WSL `python3` 3.12.3（pytest 8.4.2、ruff 0.16.4、mypy 1.20.2、pyserial 3.5）；ISSUE-024 复审曾用 Windows `.venv/Scripts/python.exe` 3.13.14 交叉复核，两者门禁均全绿，t3 可任选口径独立复跑。

## 4. 门禁基线（核查时实测复跑，2026-09-02）

环境：WSL Ubuntu / Python 3.12.3；numpy 2.5.2、pytest 8.4.2、h5py 3.16.0、ruff 0.16.4、mypy 1.20.2、pyserial 3.5；`uav_gpr` editable 可导入（`src/`）；解释器路径 `python3`（与 ISSUE-019～024 基线单同口径）。

```text
$ python3 -m pytest tests/contract/test_nmea.py tests/unit/test_core_gnss.py tests/unit/test_core_metadata.py tests/unit/test_reference_manifest.py -q
120 passed in 1.56s                                # ISSUE-024 parser 60 + ISSUE-005 GNSS/metadata + ISSUE-001 manifest 定向依赖回归

$ python3 tools/quality/verify.py
974 passed, 4 deselected in 146.98s (0:02:26)      # 全量非硬件 pytest（预期 974 达成；978 collected）
All checks passed!                                 # ruff（check .）
Success: no issues found in 44 source files        # mypy（44 文件）
package import ok                                  # import 检查（含 uav_gpr.positioning）
[quality] all gates passed                         # verify.py 全部通过（exit 0）

$ python3 -m ruff check src tests                  # 补充显式复跑
All checks passed!                                 # exit 0
$ python3 -m mypy src                              # 补充显式复跑
Success: no issues found in 44 source files        # exit 0
$ python3 -c "import uav_gpr"                      # exit 0
$ git diff --check                                 # exit 0

$ python3 -m pytest --collect-only -q -m "not hardware and not slow" --no-header
974/978 tests collected (4 deselected) in 1.17s    # deselected = hardware/slow 标记项
```

核查前后 `git status --porcelain=v1 -b` **逐字节一致**（仅 `## main...origin/main` 一行；untracked-count=0）；`git diff --check` clean；`.pytest_cache/`/`.mypy_cache/`/`.ruff_cache/` 均 git-ignored，无新缓存/日志/实测数据残留（本基线单为唯一新增未跟踪文件）。

## 5. ISSUE-025 实施计划摘要（供 t2 契约；完整版见 t2 计划文档，不含实现代码）

### 范围（M05 L52–57 原文口径 + 提示词）

1. 新建 `src/uav_gpr/positioning/reader.py`（或 t2 契约定名的模块）：可注入 `SerialAdapter` Protocol 的 GnssReader worker——独立线程增量按行读取（任意拆行/合行、行长上限 256、timeout）、调用 `parse_nmea` + `assemble_gnss_fix`（注入接收侧 UTC/monotonic）、发布不可变状态（`GnssStatus` 六态 + 结构化指标/计数）、解析错误计数继续、连续 I/O 错误确定性有界退避重连并递增 generation、stop 可取消阻塞读取且幂等释放端口、无 Qt 依赖；**GNSS 错误只上报，不停止雷达采集**。
2. 新建 `src/uav_gpr/positioning/fix_cache.py`（或与 reader 同模块，t2 契约定名）：按时间窗口/容量双上限的 thread-safe 不可变 fix snapshot 缓存（frozen `GnssFix`），淘汰策略明确，snapshot 返回只读副本、不暴露可写内部状态。
3. 新建 reader/cache 契约测试（失败测试优先，先红灯后绿灯；fake serial adapter + `threading.Event`/注入时钟调度，**不用固定 sleep**）：任意拆行/合行、坏行后恢复、高频输入、无语句（no_sentence）、无 fix、坏 checksum/invalid 计数继续、断开重连、退避计划、generation 递增、stale 观测态、stop 取消阻塞读取/幂等 close/线程退出无死锁、缓存时间+容量淘汰、snapshot 不可写、AST 守卫兼容（默认测试不 import serial、不开真实 COM 口）。
4. `docs/plans/2026-09-02-issue-025-gnss-reader.md`（计划文档，t2 先落盘：设计决策（SerialAdapter Protocol 形状、退避参数与注入方式、generation/指标语义、stale 阈值来源、GGA/RMC 配对窗口、缓存淘汰与 snapshot 形状、模块拆分与命名）、参考源哈希与排除记录、执行日志、门禁数字）。
5. `docs/issues/M05_GNSS.md` 仅 ISSUE-025 状态行（L44）：`Planned → In progress → Review → Done`（合入后由 captain/负责人标记），勿动其他条目。
6. （可选，captain 契约决定）ISSUE-024 P3-1 顺手关闭：`src/uav_gpr/positioning/nmea.py`（`_parse_coordinate` 按轴半球集合）+ `tests/contract/test_nmea.py`（先补 1 条失败测试）。

### 排除项（M05 L59–61 + 提示词，t2 不得越界）

不做 sweep 匹配（ISSUE-026）、不渲染地图、不让串口错误停止雷达采集；不改 `core/**`（`GnssFix`/`GnssMatch`/`GnssStatus`/enums/`Clock` 只读消费，六态已齐无需新增枚举；若确需改 core 必须先向 captain 报告并考虑 ADR）；不改 `acquisition/**`（重连先例只借鉴不改）；不打开真实 COM 口；不改两个参考仓库；不 commit/push/merge、不创建/切换分支；不新增范围外文件（若确需拆分/新增，t2 先停止向 captain 报告）。

### 验收标准（M05 L63–67 原文，t2 不得削弱）

1. 拆行/合行、高频输入、断开重连和关闭无死锁/泄漏。
2. 缓存有界且 snapshot 不暴露可写内部状态。
3. 默认测试不打开真实 COM 口。

### 测试矩阵要点（提示词必测项 + 依赖回归）

- 拆行/合行：一次 read 返回半句、多句、跨 read 边界拼接；行长上限与超长行处置（长行攻击不崩溃、有计数）；
- 正常/降级路径：valid 发布、GGA quality 0 → no_fix、坏 checksum/非 ASCII/不支持语句 → invalid 计数继续、连接但无语句 → no_sentence；
- 断开/重连：I/O 异常 → disconnected → 有界确定性退避重连 → generation 递增 → 恢复后状态/指标正确；重连失败退避封顶；stop 可打断退避与阻塞读取；
- 高频输入：连续大量语句不丢状态、缓存按容量/时间淘汰正确、无无界内存；
- stop/close：幂等（二次 stop 无异常）、阻塞读取被取消、端口释放（fake 记录 close 调用）、线程退出（join 有界、无死锁）；
- 缓存：双上限各自触发淘汰、snapshot 内容为 frozen `GnssFix` 且修改尝试抛 FrozenInstanceError/返回副本隔离、时间窗过期剔除、空缓存/单元素边界；
- 时序：全部通过注入时钟/Event/队列深度驱动，无固定 sleep；
- 回归：ISSUE-024 parser 定向（60）+ core gnss/metadata/manifest 定向 + 全量 `python3 tools/quality/verify.py`（974 + 新增数 passed / 4 deselected）+ ruff + mypy（44+新增文件）+ import + `git diff --check` + 工作树检查。

### inScope 精确路径建议（以任务契约 t2 inScope 为准；t2 完成登记 changedPaths 必须与此逐一相等；沿用 ISSUE-021/022/023/024 教训：精确文件路径，非 glob）

1. `src/uav_gpr/positioning/reader.py`（新模块：GnssReader worker + SerialAdapter Protocol + fake 友好设计；若含缓存则此路径并入第 2 项）
2. `src/uav_gpr/positioning/fix_cache.py`（新模块：有界线程安全 snapshot 缓存；若与 reader 同模块则从 inScope 移除）
3. `tests/contract/test_gnss_reader.py`（新文件：reader/cache 契约测试 + fake serial adapter；若测试拆分/更名以 t2 契约为准；若夹具独立成文件须在契约中列出）
4. `docs/plans/2026-09-02-issue-025-gnss-reader.md`（新计划文档，t2 先落盘，含设计决策与门禁数字）
5. `docs/issues/M05_GNSS.md`（仅 ISSUE-025 状态行 L44，勿动其他条目）
6. （可选）`src/uav_gpr/positioning/nmea.py` + `tests/contract/test_nmea.py`（仅当 captain 决定 P3-1 随 ISSUE-025 关闭时纳入）

（t1 基线单 `docs/reports/ISSUE_025_BASELINE_CONFIRMATION.md` 为 t1 交付物，不计入 t2 inScope；t2 outOfScope：`docs/reports/**`、`docs/GNSS.md`、`docs/REFERENCE_MIGRATION.md`、`docs/reference-baselines/**`、`docs/adr/**`、`src/uav_gpr/core/**`（只读消费）、`src/uav_gpr/acquisition/**`（只借鉴先例）、`src/uav_gpr/positioning/__init__.py`（如需扩展 docstring 须先向 captain 报告）、参考仓库（只读）。）

t2 验证命令按任务契约执行：`python3 -m pytest <定向测试> -q`（先红灯后绿灯）、`python3 tools/quality/verify.py`（全量）、`python3 -m ruff check src tests`、`python3 -m mypy src`、`git diff --check && git status --porcelain=v1 -b`（同 ISSUE-018～024 口径，WSL 侧 `python3`）。

若确需拆分模块/新增文件，t2 必须先停止并向 captain 报告，不得自行新增范围外文件。

## 6. 结论

ISSUE-025 开工基线已锁定：`main`/HEAD @ `ddf2a1a`（工作树完全干净、与 origin/main 同步 0/0、reflog 无破坏性操作）；直接依赖 **ISSUE-005/024 均已合入 main**（`952883e`+`b11e741`、`f22affd`+`315a7a8`+`ddf2a1a`；两份复审 VERDICT=PASS；`GnssFix`/`GnssStatus` 六态/`Clock`/`nmea.py` parser 及测试实测复现，定向 120 passed）；**ISSUE-025 是当前唯一可执行 Issue**（M05 状态行 `Planned`、`positioning/` 无 reader/缓存实现、无实现/测试/计划存在、依赖全绿）；契约要点（可注入 SerialAdapter、增量拆行/行长/timeout、六态发布、确定性有界退避 + generation、幂等 stop/close、时间/容量双上限线程安全不可变 snapshot 缓存、GNSS 错误只上报不停止采集、fake serial + 无固定 sleep、AST 守卫禁测试 import serial、精确 inScope 路径建议）已固化于第 3.4/3.5/5 节；**t2 需 captain 明确的设计/契约决策点**：①缓存与 reader 模块拆分及文件命名、②reader 测试落点（contract/integration）与文件名、③stale 阈值来源与指标形状、④GGA/RMC 配对窗口边界、⑤ISSUE-024 P3-1 是否随本 Issue 关闭；门禁基线全绿（全量 **974 passed / 4 deselected**（978 collected）、ruff/mypy（44 文件）/import 全过、依赖定向 120 passed），核查前后 git 状态一致、无残留。**无阻塞项，可开工。** 本确认单即为 t2 实现与 t3 独立复审的权威基线件；t2 按第 5 节契约执行（先落盘计划文档 → 失败测试优先 → 最小实现 → M05 状态行 → 门禁 → 报告），完成后停止，不进入 ISSUE-026。

> 后续记录：本单为开工时点的基线快照，不随 t2 改动；t2 实际完成记录见其完成报告与 `docs/plans/2026-09-02-issue-025-gnss-reader.md`，t3 复审报告独立输出。
