# ISSUE-023 开工基线确认单（只读核查 + 实施计划摘要）

日期：2026-09-02（ISSUE-023 开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-023-librevna-reconnect`（执行器 engineer，任务 t1，attempt 6e0ad3aa-7ee9-42ce-8c24-95a3c1984a52）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支、未修改实现/测试/计划文件。
配套文件：本单为 t2（LibreVNA 重连、暂停恢复与硬件基准）与 t3（独立复审）的权威基线件；独立复审按 [ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) 执行。t2 完整实施契约见 t2 计划文档（`docs/plans/2026-09-02-issue-023-librevna-reconnect.md`，由 t2 先落盘）。

## 1. 锁定的目标 Issue 与依据

**ISSUE-023：LibreVNA 重连、暂停恢复与硬件基准**（`docs/issues/M04_LIBREVNA.md` 第 5 个条目，状态 `Planned`，L153–188）

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-023（M04_LIBREVNA.md L153–188） | docs/issues/README.md 依赖顺序主表 L90 |
| 直接依赖 | ISSUE-017（采集控制器与暂停/停止状态机）、ISSUE-021（S11 生产采集后端）、ISSUE-022（同 sweep S11/S22 双反射采集） | M04 L156「直接依赖：ISSUE-017、021、022」；README.md L90 |
| 依赖状态 | **全部 Done**：ISSUE-017（M03 L81「Round-2 独立复审 VERDICT=PASS 后经项目负责人授权合并」）；ISSUE-021（M04 L81「独立复审 VERDICT=PASS 后经项目负责人自动化授权合并」，6 项 P3 观察不阻止合并）；ISSUE-022（M04 L118「独立复审 VERDICT=PASS 后经项目负责人自动化授权合并」） | M03 L79–81；M04 L81、L118；docs/reports/ISSUE_017/021/022_REVIEW_REPORT.md；git log（见 3.2） |
| 功能映射 | FR-003～005、性能门禁；`ACQUISITION.md` §2（可分类错误与设备重连代数）、§3（持久 USB 会话、暂停/恢复和错误关闭）、§4（requested/applied 回读）、§7（最小间隔必须实测）、§9（暂停/停止/故障语义）、§10（重连后 `connection_generation` 增加且配置重新确认）；`PERFORMANCE.md` §1/§2/§3/§6（p50/p95/p99、基准矩阵、环境与 commit 记录）；`TESTING.md`（硬件双重 opt-in） | M04 L157；ACQUISITION.md/PERFORMANCE.md/TESTING.md 实测（见 3.4） |
| 提示词工作区替换 | `E:\wurenjiruanjian` → 工作区根（见第 2 节） | docs/issues/README.md 第 3 节通用协议 |
| 一次一 Issue | 本轮只处理 ISSUE-023；不进入 ISSUE-024（GNSS reader） | docs/issues/README.md 第 1/3 节；团队目标；M04 L188「无论如何不得进入下一 Issue」 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

ISSUE-023 不迁移新的参考源文件：重连/退避/暂停恢复的控制器协作面已由 ISSUE-017 冻结（`controller.py` 的 `reconnect_hook`/`connection_generation`，见 3.4 约束 10）；真机基准方法只从参考项目 `E:\钢筋仪软件开发` 的 `LibreVNA采集速度测试`（本地只读副本 `/mnt/d/博士任务/rebar-inspector/LibreVNA采集速度测试`，含 `benchmark_point_ifbw_grid.py`、`benchmark_usb_streaming.py`、`benchmark_usb_batch_vs_point_stream.py`、`reports/`）**提取方法与报告格式，历史速度数字只能作对照、不得写成新结果**（M04 L167、L186；ACQUISITION.md L46）。

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      main（当前；工作树完全干净）
HEAD        8d795d5  docs(issues): mark ISSUE-022 Done after automated authorized merge
            完整哈希 8d795d5a40932158d68d6a47a878d26e280c1675（2026-09-02）
分支关系    main...origin/main = 0/0（`git rev-parse HEAD` == `git rev-parse origin/main`）
git status --porcelain=v1 -b
    ## main...origin/main
    （无其他条目——无未跟踪/未提交文件，untracked-count=0）
git diff --check    # clean（exit 0）
```

reflog 实测仅 commit/merge/checkout 记录（顶层 `8d795d5 commit` ← `9972a9c merge` ← `9d55533 checkout` ← `18ec076 commit` ← `9d55533 checkout` …），**无 reset/rebase/amend/强推迹象**。本地存在历史遗留 feature 分支 `feat/issue-009 … feat/issue-022`（均已合入 main 后的开发痕迹，`git branch -a` 实测），当前分支为 `main`，不影响 t2。

### 3.2 直接依赖 ISSUE-017/021/022 的合入证据（main 内实测）

| 提交 | 内容 |
|---|---|
| `1ceca4e` | `feat(acquisition): acquisition controller with pause/stop state machine (ISSUE-017)`（controller.py 949 行 + test_acquisition_controller.py 1344 行 + 计划/基线/复审文档） |
| `9406b60` | `docs(issues): mark ISSUE-017 Done after authorized merge` |
| `82d1c3b` | `feat(acquisition): LibreVNA S11 production acquisition backend (ISSUE-021)`（backend.py 1078 行 + test_librevna_backend.py 975 行） |
| `7af5403` | `Merge feat/issue-021: ISSUE-021 LibreVNA S11 production backend` |
| `9d55533` | `docs(issues): mark ISSUE-021 Done after automated authorized merge` |
| `18ec076` | `feat(acquisition): same-sweep S11/S22 dual reflection backend (ISSUE-022)`（backend.py +184/−48、test_librevna_backend.py +266/−18、计划/基线/复审文档） |
| `9972a9c` | `Merge feat/issue-022: ISSUE-022 same-sweep S11/S22 dual reflection backend` |
| `8d795d5` | `docs(issues): mark ISSUE-022 Done after automated authorized merge`（**当前 HEAD**） |

tracked 交付物（main，`git ls-files` + 实测复现）：

| 交付物 | 实测事实 | ISSUE-023 复用/依据点 |
|---|---|---|
| `src/uav_gpr/acquisition/controller.py`（949 行，ISSUE-017） | `tests/contract/test_acquisition_controller.py` 实测 **88 passed**；M03 L81 状态行 Done；`reconnect_hook` 参数（L316、L345）、`connection_generation`（L389–391）、`pause`（L518）、`resume`（L556）、`stop`（L600）、`close`（L690）、`_route_disconnect`（L821–861，含 P3-03 注释 L838–844：**「real USB reconnect 语义须在 ISSUE-019/023 实现物理重连路径时记录进 docs/ADR」**） | **ISSUE-023 直接扩展/消费**：controller 已具备「disconnect → reconnect hook → generation 校验 → 新调度锚点」协作面；t2 的 `reconnect.py` 即该 hook 的物理 USB 重连实现（退避、重新 open/configure/回读），controller 语义（暂停不重复 trace_index、重连后配置重新确认）须保持 |
| `src/uav_gpr/acquisition/librevna/backend.py`（1184 行，ISSUE-021+022） | `tests/contract/test_librevna_backend.py` 实测 **48 passed**；M04 L81/L118 状态行 Done；`LibreVnaUsbBackend`：`_do_open`（L551）、`_do_configure`（L583）、`_do_acquire`（L643）、`_do_close`（L717）、`_wait_for_device_info`（L732）、`_bump_generation`（L1166）、`_enter_fail_closed`（L1170）、`connection_generation` 写入 sweep 元数据（L935）；S11/S22 双通道（0x1240/0x1241，`_validate_stages_bitmap` L286） | **ISSUE-023 直接扩展**：backend 尚无任何重连/退避/retry 逻辑（grep 实测 reconnect/backoff/retry 无命中）——断开后 `_do_open` 失败即 fail-closed；t2 扩展 reconnect 语义（open 重试退避、重新 configure 回读、generation 递增）、暂停时 in-flight USB 安全关闭（与 controller pause/resume/stop 协作） |
| `src/uav_gpr/acquisition/librevna/transport.py`（501 行，ISSUE-019） | `tests/contract/test_librevna_transport.py` 实测 **50 passed**；`VID = 0x1209` / `PID = 0x4121`（L54–55） | 真机识别依据（VID/PID 匹配）；重连退避在 transport open 层之上实现 |
| `src/uav_gpr/acquisition/librevna/stream.py`（548 行，ISSUE-020） | `tests/contract/test_librevna_stream.py` 实测 **61 passed** | 严格组装器只读消费，重连后重建会话 |
| `src/uav_gpr/acquisition/backend.py` / `scheduler.py`（ISSUE-015/016） | `tests/contract/test_acquisition_backend.py` **28 passed**、`test_acquisition_scheduler.py` **25 passed** | 后端契约与单调调度回归网 |
| `tests/hardware/test_hardware_sentinel.py` + `tests/conftest.py` | 硬件双重 opt-in 哨兵（`--hardware` + `UAV_GPR_HARDWARE_OPTIN=1`，conftest L62–71）；默认收集 902 项中 1 项 deselected（即该哨兵） | **ISSUE-023 opt-in 硬件测试沿用同一双重授权机制**（TESTING.md L54–67） |
| `docs/issues/M03_ACQUISITION.md` L81 / `docs/issues/M04_LIBREVNA.md` L81/L118 / `docs/issues/README.md` L90 | 状态行实测 Done/Done/Done；依赖表 `023 | LibreVNA 重连、暂停恢复与硬件基准 | 017, 021, 022` | ISSUE-023 直接依赖全部完成 |

### 3.3 ISSUE-023 为下一个可执行 Issue

- M04 L153–188：ISSUE-023 状态 `Planned`（L155）；L156 `直接依赖：ISSUE-017、021、022`（全部 Done）。
- docs/issues/README.md L90：`023 | LibreVNA 重连、暂停恢复与硬件基准 | 017, 021, 022`——依赖表无计划冲突；ISSUE-024（GNSS reader）直接依赖 001/005（未完成），**ISSUE-023 是当前唯一可执行 Issue**，一次只执行一个。
- 落点核查：`docs/plans/` 无任何 ISSUE-023 文件（计划文档只到 issue-022）；`docs/reports/` 无 ISSUE_023_* 文件；`src/uav_gpr/acquisition/librevna/` 无 reconnect 模块；`tools/benchmark/` 仅有 README.md 占位（无工具文件）——t2 是首个引入重连/基准的实现点。

### 3.4 对 ISSUE-023 有约束的契约要点（读自 ACQUISITION.md、PERFORMANCE.md、TESTING.md、M04 L153–188、controller/backend/transport 实测源码、ISSUE_REVIEW_STANDARD.md、AGENTS.md）

**ISSUE-023 范围（M04 L163–168）+ 提示词（L182–188）**：

1. **设备断开/重连状态、退避、connection generation、重新 configure/回读**——`controller._route_disconnect`（L821–861）已固定协作面：worker 内路由断开 → `reconnect_hook()` → 校验 `backend.connection_generation` 递增（L847）→ 新调度锚点（L860–861）。t2 的 `reconnect.py` 实现物理重连：退避（固定/指数+抖动，计划文档须固定决策）、重新 `open`、重新 `configure` 并**回读确认 applied config**（ACQUISITION.md §4：requested/applied/config_diff；首道前轴门禁 `_verify_first_axis` 在 backend 已冻结，重连后第一道仍须过该门禁）。P3-03（controller L838–844）要求：重连使 `connection_generation` 语义在实现物理重连路径时记录——t2 计划文档须固定「每次物理重连（含失败重试序列）generation 语义」决策，若改变 controller 语义须先写 ADR（AGENTS.md §12）。
2. **controller pause/resume/stop 与 USB in-flight 的安全协作**——ACQUISITION.md §9：pause 停止发起新 sweep、当前 sweep 处理到安全边界并 flush；resume 重新检查设备/磁盘、增加必要连接代数、从新调度锚点继续；stop drain 已完成 sweep；emergency stop 优先停止硬件 I/O。AGENTS.md §7：USB I/O 不在 UI 主线程、队列有界、取消安全。backend 无线程（L446「no threads」注释），in-flight 取消依赖 base class 取消信号 + `_raise_if_interrupted`（L1162）——t2 须契约测试证明 pause/resume/stop 与模拟 in-flight 包/超时交互不泄漏、不重复 trace_index（ACQUISITION.md §10：暂停/恢复不重复 trace_index、不制造巨大调度误差补偿）。
3. **可复现 benchmark 工具 + opt-in hardware tests**——`tools/benchmark/` 新增基准工具；报告 sweep 率、写前模型开销、错误率、CPU 与目标配置（M04 L167；PERFORMANCE.md §3 采集矩阵：频率范围 × 点数 × IFBW × S11/双通道、持久连接/暂停恢复/设备重连、sweep/s、USB 错误、缺点、CPU）。基准方法参考 `E:\钢筋仪软件开发` 的 `LibreVNA采集速度测试`（point/IFBW 网格 + USB 流式对比程序），**历史数字不得写成新结果**（M04 L167、L186；ACQUISITION.md L46「不直接继承历史数值」）。
4. **真机矩阵报告格式**——包含硬件/固件/配置/commit 与 **p50/p95/p99**（M04 L177；PERFORMANCE.md §1 所有关键链路记录 p50/p95/p99、最大值、吞吐、内存、错误数；§6 基准输入和环境信息固定并记录 commit、Python/依赖、CPU、磁盘和配置；目标硬件完整矩阵报告进入 `docs/reports/`）。普通 CI 只跑小规模 smoke benchmark 检查数量级退化（PERFORMANCE.md §6）。
5. **无真机 → BLOCKED 口径**——本环境无指定真机（见 3.5-2）：硬件验收矩阵项一律标 **BLOCKED（等待真机）** 且不得伪造完成（M04 L178、L187；团队目标硬性约束）；模拟断开/重连、controller 协作、benchmark 工具与 opt-in 硬件测试仍完整交付并复审；合并后 M04 状态行标 **Blocked** 而非 Done。
6. **最小安全间隔输入**——ACQUISITION.md §7：允许的最小间隔必须来自「采集 + 空中写盘 + 哈希 + 安全余量」的实测，不只使用 USB 平均吞吐；PERFORMANCE.md §2：最小允许间隔必须大于关键路径 p99 加安全余量。ISSUE-023 产出基准输入（频段×点数×IFBW×通道），**不含 HDF5/网络关键路径最终最小间隔**（M04 L170 排除项）。
7. **失败测试优先 + 模拟器/夹具**——先用模拟 USB（`ScriptedAdapter` 等既有夹具范式）完成自动测试；默认测试不枚举 USB、不 import `serial/usb/socket/网络` 根（`tests/unit/test_no_external_access.py` AST 守卫）；禁止固定 sleep 猜时序（注入时钟）；硬件测试必须 `-m hardware` 标记 + 双重 opt-in（`--hardware` + `UAV_GPR_HARDWARE_OPTIN=1`，TESTING.md L54–67），测试内部按 `UAV_GPR_DEVICE_ID` 等标识自检（L66–67）。
8. **排除项**（M04 L170–172 + 提示词）：不含 HDF5/网络关键路径最终最小间隔；不做飞行验收；不实现 S21/S12、校准；不改两个参考仓库；不进入 ISSUE-024。
9. **验收标准原文**（M04 L174–178，t2 不得削弱）：(a) 模拟断开/重连不重复 trace、不沿用未确认配置；(b) 真机矩阵报告包含硬件/固件/配置/commit 和 p50/p95/p99（无真机时标 BLOCKED 交付工具与 opt-in 测试）；(c) 没有指定真机时 Issue 保持 Blocked，不伪造完成。
10. **既有冻结面（只读消费，不改）**：`core/**`、`acquisition/backend.py`（基类）、`librevna/transport.py`、`librevna/stream.py`、`librevna/__init__.py`、`acquisition/controller.py` 的既有状态机语义（t2 扩展 reconnect 路径时若需改 controller.py 必须先向 captain 报告——团队目标 inScope 为 7 个精确路径，不含 controller.py）。

**其他约束**：

11. 测试纪律：每项能力覆盖正常、错误、取消/恢复路径（AGENTS.md §10）；禁删测试/降断言/吞异常；`git diff --check` clean；不提交实测数据/日志/缓存（AGENTS.md §11）。
12. 文档权威顺序（AGENTS.md §12）：当前任务验收条件 > AGENTS.md > 已接受 ADR > 专题设计文档 > 参考项目代码/文档。若重连语义与 `ACQUISITION.md` §9/§10 冲突，先写 ADR 或更新计划，不能只在代码中形成事实。

### 3.5 发现的事实（非阻塞，供 t2 注意）

1. 工作树完全干净且与远端同步（`## main...origin/main` 0/0，HEAD `8d795d5`）；t2 交付物将是唯一新改动。
2. **真机核查结论：本环境无指定真机 LibreVNA**。(a) WSL 侧：无 `lsusb`、无 `/dev/bus/usb`、pyusb 不可导入；(b) Windows 宿主侧：`Get-PnpDevice -PresentOnly` 实测 159 个 present 设备，**无任何 InstanceId 匹配 `VID_1209&PID_4121` 或名称含 LibreVNA**。→ 硬件验收矩阵项必须标 BLOCKED；模拟断开/重连、controller 协作、benchmark 工具与 opt-in 硬件测试仍完整交付。
3. `controller.py` 的 `reconnect_hook`/`connection_generation` 协作面已冻结且带 P3-03 注释（L838–844）明确指向 ISSUE-019/023——t2 改动面清晰：新建 `librevna/reconnect.py` 实现物理重连（退避 + 重新 open/configure/回读 + generation 递增），backend.py 暴露重连所需的会话重建/配置重确认能力。
4. backend.py 当前**无**任何 reconnect/backoff/retry 逻辑（grep 实测无命中）——断开即 fail-closed；t2 最小实现空间明确。
5. `tools/benchmark/README.md` 为占位文件，基准工具文件待 t2 新增；参考项目 `LibreVNA采集速度测试` 方法程序齐全（point/IFBW 网格、USB 流式对比），只提取方法与报告格式。
6. 门禁基线数字（第 4 章）：全量 **901 passed / 1 deselected**（ISSUE-022 合入后口径：902 collected，1 个 hardware 哨兵 deselected），t2 完成后新基线 = 901 + 新增非硬件测试数，不得出现既有测试失败。
7. 既有硬件测试骨架已就绪：`tests/hardware/test_hardware_sentinel.py` + `tests/conftest.py` 双重 opt-in 哨兵——t2 opt-in 硬件测试文件直接复用该机制（`@pytest.mark.hardware`）。

## 4. 门禁基线（核查时实测复跑，2026-09-02）

环境：WSL Ubuntu / Python 3.12.3；numpy 2.5.2、pytest 8.4.2、h5py 3.16.0、ruff 0.16.4、mypy 1.20.2；`uav_gpr` editable 可导入（`src/`）；解释器路径 `python3`（与 ISSUE-019/020/021/022 基线单同口径）。

```text
$ python3 -m pytest tests/contract/test_acquisition_controller.py -q
88 passed in 3.63s                                  # ISSUE-017 定向（controller 状态机回归网）

$ python3 -m pytest tests/contract/test_librevna_backend.py -q
48 passed in 0.30s                                  # ISSUE-021+022 定向（backend S11/S11+S22 回归网）

$ python3 -m pytest tests/contract/test_acquisition_controller.py \
    tests/contract/test_acquisition_backend.py \
    tests/contract/test_acquisition_scheduler.py \
    tests/contract/test_librevna_transport.py \
    tests/contract/test_librevna_stream.py \
    tests/contract/test_librevna_backend.py -q
300 passed in 5.28s        # 依赖定向：ISSUE-017：88 + ISSUE-015：28 + ISSUE-016：25 + ISSUE-019：50 + ISSUE-020：61 + ISSUE-021/022：48

$ python3 tools/quality/verify.py
901 passed, 1 deselected in 134.70s (0:02:14)   # 全量非硬件 pytest（预期 901 达成）
All checks passed!                               # ruff（check .）
Success: no issues found in 42 source files      # mypy（42 文件）
package import ok                                # import 检查
[quality] all gates passed                       # verify.py 全部通过时 exit 0

$ python3 -m pytest --collect-only -q -m "not hardware and not slow" --no-header
901/902 tests collected (1 deselected) in 0.87s   # deselected = tests/hardware 硬件哨兵

$ python3 -m ruff check src tests                # 补充显式复跑
All checks passed!                               # exit 0
$ python3 -m mypy src                            # 补充显式复跑
Success: no issues found in 42 source files      # exit 0
```

核查前后 `git status --porcelain=v1 -b` **逐字节一致**（仅 `## main...origin/main` 一行；untracked-count=0）；`git diff --check` clean；`.pytest_cache/`/`.mypy_cache/`/`.ruff_cache/` 均 `git check-ignore` 确认已忽略，无新缓存/日志/实测数据残留（本基线单为唯一新增未跟踪文件）。

## 5. ISSUE-023 实施计划摘要（供 t2 契约；完整版见 t2 计划文档，不含实现代码）

### 范围（M04 L163–168 原文口径 + 提示词）

1. 新建 `src/uav_gpr/acquisition/librevna/reconnect.py`：设备断开/重连状态机 + 退避（固定/指数+抖动，计划文档固定决策）+ `connection_generation` 递增协作 + 重新 open/configure/回读确认（requested/applied 语义与首道前轴门禁沿用 ISSUE-021/022 冻结面）；作为 controller `reconnect_hook` 的物理重连实现（controller.py 已冻结协作面，只读消费；若确需改 controller.py 先停止向 captain 报告）。
2. 扩展 `src/uav_gpr/acquisition/librevna/backend.py`：重连所需能力（会话重建/配置重确认/重连后第一道轴门禁）、与 pause/resume/stop 的 in-flight 安全协作所需钩子（最小实现，不重构既有 0x1240/0x1241 与双通道语义）。
3. 扩展 `tests/contract/test_librevna_backend.py`（失败测试优先，先红灯后绿灯）：模拟断开/重连不重复 trace、不沿用未确认配置（generation 校验、重新 configure 回读）、退避行为、pause/resume/stop 与 in-flight 交互、S11/S11+S22 全量回归（现有 48 测试不动）。
4. 新建 `tests/hardware/` 下 opt-in 硬件测试（`@pytest.mark.hardware` + 双重 opt-in，`UAV_GPR_DEVICE_ID` 自检）：真机矩阵执行入口（目标频段/点数/IFBW/S11/双通道 + p50/p95/p99 + 错误/CPU + 硬件/固件/commit 记录）；默认收集跳过。
5. 新建 `tools/benchmark/` 下可复现 benchmark 工具：矩阵输入（频段×点数×IFBW×通道）、输出 p50/p95/p99/错误率/CPU/配置与 commit（方法参考钢筋仪 `LibreVNA采集速度测试`，历史数字只作对照）；smoke 模式供 CI。
6. `docs/plans/2026-09-02-issue-023-librevna-reconnect.md`（计划文档，t2 先落盘，含设计决策、P3-03 重连 generation 语义决策记录、执行日志、门禁数字）。
7. `docs/issues/M04_LIBREVNA.md` 仅 ISSUE-023 状态行（L155）：`Planned → In progress → Review`；**最终合并后标 `Blocked（等待真机）` 而非 Done**（团队目标硬性约束），勿动其他条目。

### 排除项（M04 L170–172 + 提示词，t2 不得越界）

不含 HDF5/网络关键路径最终最小间隔；不做飞行验收；不实现 S21/S12、校准；不进入 ISSUE-024；不改 `core/**`、`acquisition/backend.py` 基类、`librevna/transport.py`、`librevna/stream.py`、`librevna/__init__.py`（只读消费）；不改两个参考仓库；不 commit/push/merge、不创建/切换分支；不新增范围外文件（若确需拆分/新增，t2 先停止向 captain 报告）。

### 验收标准（M04 L174–178 原文，t2 不得削弱）

1. 模拟断开/重连不重复 trace、不沿用未确认配置。
2. 真机矩阵报告包含硬件/固件/配置/commit 和 p50/p95/p99（无真机时标 BLOCKED，交付工具与 opt-in 测试并复审）。
3. 没有指定真机时 Issue 保持 Blocked，不伪造完成。

### 测试矩阵要点（提示词必测项 + 依赖回归）

- 模拟断开/重连：断开注入 → 退避序列 → 重连成功（generation 递增、重新 configure 回读、applied 确认）→ 继续采集不重复 trace_index、trace_uid 单调；重连失败 → fail-closed 结构化错误；断开期间 pause/stop 语义；
- 配置重确认：重连后 requested/applied 重新校验，首道前轴门禁（`AXIS_TOLERANCE_HZ`）仍生效；未确认配置不得沿用；
- controller 协作：pause（安全边界停新 sweep）→ 模拟 in-flight 完成/取消 → resume（新锚点、无补采债务）→ stop drain；重复命令幂等；无残留线程/资源泄漏；
- 基准工具：smoke 模式 CI 可跑（数量级检查）、完整模式输出格式（p50/p95/p99、错误率、CPU、硬件/固件/commit）；
- 硬件 opt-in：双重授权哨兵（`--hardware` + `UAV_GPR_HARDWARE_OPTIN=1` 缺一即跳过）、`-m hardware` 只选择不授权、AST 守卫（默认测试不 import usb/serial/网络）；
- 回归：现有 48 backend + 88 controller + 300 依赖定向全绿；全量 `python3 tools/quality/verify.py`（901 + 新增数 passed/1 deselected）+ ruff + mypy（42 文件）+ import + `git diff --check` + 工作树检查；测试禁固定 sleep（注入时钟）。

### inScope 精确路径建议（以任务契约 t2 inScope 为准；t2 完成登记 changedPaths 必须与此逐一相等；沿用 ISSUE-021/022 教训：精确文件路径，非 glob；团队目标口径 = 7 个精确路径）

1. `src/uav_gpr/acquisition/librevna/reconnect.py`（新模块：重连/退避/配置重确认）
2. `src/uav_gpr/acquisition/librevna/backend.py`（扩展：重连协作能力 + in-flight 安全钩子，最小实现）
3. `tests/contract/test_librevna_backend.py`（扩展：模拟断开/重连 + controller 协作契约测试 + S11/S11+S22 回归）
4. `tests/hardware/test_librevna_hardware.py`（新文件：双重 opt-in 真机矩阵执行测试，默认跳过）
5. `tools/benchmark/benchmark_librevna.py`（新文件：可复现基准工具 + smoke 模式）
6. `docs/plans/2026-09-02-issue-023-librevna-reconnect.md`（新计划文档，t2 先落盘，含设计决策与门禁数字）
7. `docs/issues/M04_LIBREVNA.md`（仅 ISSUE-023 状态行 L155，勿动其他条目；最终 Blocked 而非 Done）

（t1 基线单 `docs/reports/ISSUE_023_BASELINE_CONFIRMATION.md` 为 t1 交付物，不计入 t2 inScope；t2 outOfScope：`docs/reports/**`、`docs/ACQUISITION.md`、`docs/PERFORMANCE.md`、`docs/TESTING.md`、`docs/ISSUE_REVIEW_STANDARD.md`、`docs/REFERENCE_MIGRATION.md`、`docs/reference-baselines/**`、`docs/adr/**`、`tools/**`（benchmark 工具文件除外）、参考仓库（只读）、`src/uav_gpr/core/**`（只读消费）、`src/uav_gpr/acquisition/backend.py`（只读消费）、`src/uav_gpr/acquisition/librevna/transport.py`（只读消费）、`src/uav_gpr/acquisition/librevna/stream.py`（只读消费）、`src/uav_gpr/acquisition/librevna/__init__.py`（只读消费）、`src/uav_gpr/acquisition/controller.py`（只读消费；确需修改必须先向 captain 报告）。）

t2 验证命令按任务契约执行：`python3 -m pytest tests/contract/test_librevna_backend.py -q`（定向，先红灯后绿灯）、`python3 tools/quality/verify.py`（全量）、`python3 -m ruff check src tests`、`python3 -m mypy src`、`git diff --check && git status --porcelain=v1 -b`（同 ISSUE-018/019/020/021/022 口径，WSL 侧 `python3`）。

若确需拆分模块/新增文件，t2 必须先停止并向 captain 报告，不得自行新增范围外文件。

## 6. 结论

ISSUE-023 开工基线已锁定：`main`/HEAD @ `8d795d5`（工作树完全干净、与 origin/main 同步 0/0、reflog 无破坏性操作）；直接依赖 **ISSUE-017/021/022 全部 Done** 合入 main（`1ceca4e`、`82d1c3b`+`7af5403`+`9d55533`、`18ec076`+`9972a9c`+`8d795d5`（HEAD）；controller.py 949 行 88 测试、backend.py 1184 行 48 测试实测复现；M03 L81 / M04 L81/L118 状态行 Done、复审 VERDICT=PASS）；**ISSUE-023 是当前唯一可执行 Issue**（状态 `Planned`、无实现/测试/计划存在、依赖全绿）；**真机核查结论：本环境无指定真机 LibreVNA**（Windows 宿主 159 个 present PnP 设备无 VID 0x1209/PID 0x4121 匹配、WSL 无 USB 栈）——硬件验收矩阵项将标 **BLOCKED（等待真机）**，模拟断开/重连、controller 协作、benchmark 工具与 opt-in 硬件测试仍完整交付并复审，合并后 M04 状态行标 Blocked 而非 Done；契约要点（controller `reconnect_hook`/`connection_generation` 冻结协作面 + P3-03 重连语义记录要求、退避与配置重确认、pause/resume/stop 与 in-flight 安全协作、基准矩阵 p50/p95/p99 + 硬件/固件/配置/commit、硬件双重 opt-in、参考项目数字不得当结果、7 个精确 inScope 路径）已固化于第 3.4/3.5/5 节；门禁基线全绿（全量 **901 passed / 1 deselected**（902 collected，1 hardware 哨兵 deselected）、ruff/mypy（42 文件）/import 全过、依赖定向 300 passed、ISSUE-017 定向 88、ISSUE-021/022 定向 48），核查前后 git 状态一致、无残留。**无阻塞项，可开工。** 本确认单即为 t2 实现与 t3 独立复审的权威基线件；t2 按第 5 节契约执行（先落盘计划文档 → 失败测试优先 → 最小实现 → M04 状态行 → 门禁 → 报告），完成后停止，不进入 ISSUE-024。

> 后续记录：本单为开工时点的基线快照，不随 t2 改动；t2 实际完成记录见其完成报告与 `docs/plans/2026-09-02-issue-023-librevna-reconnect.md`，t3 复审报告独立输出。
