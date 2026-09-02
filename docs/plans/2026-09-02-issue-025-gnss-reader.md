# ISSUE-025 实施计划：GNSS reader、重连与有界 fix 缓存

日期：2026-09-02
执行器：AgentTeams `uav-gpr-issue-025-gnss-reader` 成员 engineer（任务 t2，attempt 0bd1b449-4703-418e-bfb0-abf3b9137ea1）
基线件：[docs/reports/ISSUE_025_BASELINE_CONFIRMATION.md](../reports/ISSUE_025_BASELINE_CONFIRMATION.md)（main @ `ddf2a1a`，工作树干净，门禁 974 passed / 4 deselected）
目标 Issue：ISSUE-025（`docs/issues/M05_GNSS.md` L42–77）；约束文档：`AGENTS.md` §5/§7/§10、`docs/GNSS.md` §3/§4/§8、`docs/ARCHITECTURE.md` §3/§6、`docs/DATA_MODEL.md` §6/§7、`docs/REFERENCE_MIGRATION.md` §7、ADR-0005。

## 1. 目标与用户价值

在 `positioning` 层提供与雷达采集完全解耦的 GNSS 读取 worker：独立线程串口读取→增量拆行→调用 ISSUE-024 parser→组装不可变 `GnssFix`→发布六态状态与有界缓存；I/O 故障自动有界退避重连，GNSS 任何错误只上报、绝不停止雷达采集。为 ISSUE-026（sweep midpoint 匹配）提供唯一数据源，为空中端最小状态 UI（ISSUE-046）提供状态快照面。

## 2. 范围（任务契约 inScope = 4 个精确路径，changedPaths 与此逐一相等）

1. `src/uav_gpr/positioning/reader.py`（新模块，单一模块：`SerialAdapter` Protocol + 异常 + `GnssReconnectPolicy` + `GnssFixCache` + 状态/指标 frozen dataclass + `GnssReader` worker + pyserial 薄适配工厂）
2. `tests/contract/test_gnss_reader.py`（新文件：契约测试 + fake serial，默认不打开真实 COM 口）
3. `docs/plans/2026-09-02-issue-025-gnss-reader.md`（本计划文档）
4. `docs/issues/M05_GNSS.md`（仅 ISSUE-025 状态行 L44：`Planned → In progress → Review`，勿动其他条目）

## 3. 明确排除项（M05 L59–61 + 提示词 + 任务契约）

不做 sweep 匹配（ISSUE-026）、不渲染地图、不实现 AGL 推算；GNSS 错误只上报不停止雷达采集；不改 `core/**`（`GnssFix`/`GnssStatus` 六态/`Clock` 只读消费）、不改 `nmea.py`（只读消费；ISSUE-024 P3-1 按任务契约不在本 Issue 范围，保持 open）、不改 `acquisition/**`（重连先例只借鉴不导入——依赖方向 positioning → core，不反向依赖 acquisition）、不打开真实 COM 口、不 commit/push/merge、不创建分支、不新增 inScope 之外文件。

## 4. 设计决策（D1–D11）

- **D1 SerialAdapter 注入面**：`SerialAdapter` Protocol（`read(max_bytes: int) -> bytes`：阻塞至有数据或适配器超时，返回 `b""` 表示超时；`close() -> None`：幂等、释放端口、解除阻塞 read）。异常族：`SerialAdapterError`（I/O 故障→重连路径）、`SerialAdapterClosedError(SerialAdapterError)`（close 引发的读中断→stop/断开路径）。工厂 `Callable[[], SerialAdapter]` 注入，open 失败抛 `SerialAdapterError`。`PyserialSerialAdapter` 只包已打开端口对象（结构化 duck-type `_SerialPortLike` Protocol，包装层零 pyserial import）；`PyserialSerialFactory`/`PyserialSerialConfig` 内部**惰性 import serial**（`type: ignore[import-untyped]`，pyserial 无 stubs），模块顶层无 serial import——默认测试路径永不触碰 pyserial、绝不打开 COM 口（验收 3）。
- **D2 增量拆行**：worker 内 `bytearray` 缓冲；以 `b"\n"` 分行、剥 `\r`；跨 read 边界任意拆行/合行正确（参数化 1/2/3/7/4096 字节分块）；纯空行静默跳过（非错误）；ASCII 严格解码，解码失败计 invalid（`last_invalid_reason="non_ascii"`）。
- **D3 长度/timeout 上限**：行长上限复用 parser `MAX_NMEA_LINE_LEN=256`——缓冲超 256 字节无换行即进入 overflow：丢弃至下一换行重新同步，`overlong_line_count++`（含于 `invalid_count`），防长行攻击无界内存；read 块大小 `read_chunk_size=1024`（构造参数）；timeout 由适配器契约承载（read 阻塞上限=适配器超时）。
- **D4 六态状态机（D5 优先级，事件+年龄驱动，全确定性）**：`_recompute_status` 每事件与每次 read 空转（适配器超时）时重算——①未连接→`DISCONNECTED`；②有 valid fix 且年龄 ≤ `stale_after_s`→`VALID`；③有 valid fix 但超龄→`STALE`；④无 valid fix 且最后事件=解析/解码错误→`INVALID`；⑤无 valid fix 且已解析过语句（GGA quality 0 或任意 RMC）→`NO_FIX`；⑥已连接未解析过任何语句→`NO_SENTENCE`。状态语义映射 GNSS.md §4 原文；`INVALID` 表示「无有效定位、最后信息为坏语句」，不覆盖新鲜 valid fix（新鲜定位对地图更相关）。RMC-only 接收机落 `NO_FIX`（语句有效、无定位），`gga_count=0` 可诊断。
- **D5 GGA/RMC 配对窗口**：worker 保存「最近 status-valid RMC + 其接收单调时刻」；组装 GGA fix 时仅当 RMC 接收单调龄 ≤ `rmc_pair_window_s` 才传给 `assemble_gnss_fix`（提供日期/速度/航向），否则 `rmc=None`（parser 策略：无日期源→`nmea_utc=None`，绝不伪造）。**`rmc_pair_window_s` 为必填构造参数、无静默默认，验证 `0.0 ≤ w ≤ 2.0`**（captain 裁决 4：同秒或 ≤2s 窗口内配对；边界 2.0s 含——有专门边界测试）。跨午夜 12h 容差由 parser 承担（ISSUE-024 已测）。降级路径：仅 GGA 接收机正常发布部分字段 fix（速度/航向/nmea_utc 保持 None）；仅 RMC 接收机发布 `NO_FIX` 状态 + `rmc_count` 指标、不伪造 fix（`assemble_gnss_fix` 契约要求 GGA 才能组装 fix；RMC-only 无坐标可发布，绝不虚构 quality/HDOP/MSL）。
- **D6 接收侧事实**：`received_utc=clock.utc_now()`、`received_monotonic_ns=clock.monotonic_ns()`，`Clock` 构造注入（默认 `SystemClock`）——reader 永不伪造时间（nmea.py L20–22 契约）。
- **D7 退避重连 + generation**：`GnssReconnectPolicy` frozen dataclass（`initial_delay_s=0.5, backoff_factor=2.0, max_delay_s=8.0`；`delay(n)=min(initial*factor^(n-1), max)`，与 `acquisition/librevna/reconnect.py` 同款确定性无抖动公式，**复制而非导入**保持 positioning→core 依赖方向）。read I/O 错误→close、`io_error_count++`、状态 `DISCONNECTED`→重连循环：open 失败→`open_error_count++`→`stop_event.wait(policy.delay(n))`（**可被 stop 取消的等待，无 sleep**）→重试；open 成功→`generation++`（首次=1，0=从未连接）、状态回 `NO_SENTENCE`、失败计数清零。缓存跨重连保留（时间窗淘汰自然处理年龄）。
- **D8 幂等 stop/close**：`stop(join_timeout_s=5.0)` 幂等——置 stop_event、close 当前适配器（解除阻塞 read）、join 线程；二次调用无副作用（close 恰一次，fake 记录验证）；worker 收到 `SerialAdapterClosedError` 且 stop_event 已置→退出；若 stop 未置则视为设备断开走重连。线程 daemon=True + join 有界，退出后状态终值 `DISCONNECTED`。`start()` 二次调用抛 `RuntimeError`（单生命周期）；支持 `with` 上下文管理。
- **D9 有界缓存 `GnssFixCache`**：`threading.Lock` 保护；构造校验 `max_items>=1`、`max_age_s>0`、只收 `GnssFix`；插入时先按单调龄（`max_age_s`，基于注入 Clock）淘汰过期、再按容量淘汰最旧（双上限同时成立）；`snapshot() -> tuple[GnssFix, ...]` 先剪过期再按 `received_monotonic_ns` 升序返回**新 tuple**——内部 list 不外泄、元素为 frozen dataclass（修改抛 `FrozenInstanceError`），不暴露可写内部状态；valid 与 invalid fix 都入缓存（匹配器可区分「没收到 fix」与「收到但无效」，消费方按 `fix.valid` 过滤）。
- **D10 状态发布（pull 模型）**：`GnssReaderStatus`（frozen：`status`/`generation`/`metrics`/`last_valid_fix_age_s`/`last_invalid_reason`）+ `GnssReaderMetrics`（frozen 计数：`gga_count/rmc_count/invalid_count/overlong_line_count/io_error_count/open_error_count/fixes_published`）。`status()`/`fixes()` 线程安全快照（AGENTS.md §7：UI 只消费有界快照）；`threading.Condition` 广播 + `wait_for(pred, timeout_s)`/`wait_for_status(target, timeout_s)`——测试事件驱动同步，无固定 sleep、无 busy-wait。
- **D11 测试无固定 sleep**：`ScriptedSerialAdapter`（测试内 fake）——无界队列剧本（bytes 分块 / 抛异常 / 可调用钩子），`close()` 入队哨兵即时解除阻塞 read；read 超时经 `Queue.get(timeout=read_timeout)` 事件化；staleness 用「钩子在 worker 线程 read() 内推进注入 Clock」实现零竞态确定性推进；所有等待走 `wait_for*`（Condition）+ 有界 join，全程无 `time.sleep`。

## 5. 文件改动（精确路径，无 glob）

| 路径 | 动作 | 内容 |
|---|---|---|
| `src/uav_gpr/positioning/reader.py` | 新增 | ~430 行：异常族、Protocol、`GnssReconnectPolicy`、`PyserialSerialConfig`/`PyserialSerialAdapter`/`PyserialSerialFactory`、`GnssReaderMetrics`/`GnssReaderStatus`、`GnssFixCache`、`GnssReader` |
| `tests/contract/test_gnss_reader.py` | 新增 | ~560 行：27 个契约测试 + `ScriptedSerialAdapter`/`ScriptedClock` fake + NMEA 夹具（动态校验和构造，匿名合成语句） |
| `docs/plans/2026-09-02-issue-025-gnss-reader.md` | 新增 | 本文档（含执行日志与门禁数字） |
| `docs/issues/M05_GNSS.md` | 修改 | 仅 L44 状态行 Planned → In progress → Review |

## 6. 测试矩阵（失败测试优先，先红灯后绿灯；fake serial、事件/屏障驱动、无固定 sleep、不开 COM 口）

1. 拆行/合行：参数化分块 {1,2,3,7,4096} 跨 read 边界拼出完整语句→正确解析计数与 fix；空行静默跳过。
2. 坏行恢复：坏 checksum→`invalid_count`、状态 `INVALID` 事件→后续好语句恢复 `VALID`；非 ASCII 行→`non_ascii` 拒绝并恢复；超长行（>256 无换行）→overflow 丢弃至换行重新同步、`overlong_line_count`、内存有界、后续语句正常。
3. 状态机：启动前 `DISCONNECTED`；连接后无语句 `NO_SENTENCE`；GGA quality 0→`NO_FIX` + 缓存 invalid fix；quality 1→`VALID`（经纬度/MSL/sats/HDOP 逐字段断言）；RMC 配对→速度/航向/日期组合；RMC-only→`NO_FIX`、`gga_count=0`；注入时钟推进→`STALE`；新 fix 后回 `VALID`。
4. 断开/重连：read 抛 `SerialAdapterError`→重连、generation 2、`io_error_count`；open 连续失败→退避（`GnssReconnectPolicy` 纯函数单独断言公式与封顶）后成功连接；错误计数清零。
5. 停止：阻塞 read 被 close 解除、线程 join 有界退出、close 恰一次、`stop()` 幂等、stop 后 factory 不再被调用；退避等待中被 stop 立即中止。
6. 高频输入：2000 句连发→计数精确、缓存按容量淘汰至 `max_items`、无异常。
7. 缓存：容量淘汰（最旧先出）、时间窗淘汰（snapshot 剪过期）、snapshot 不可变（tuple、frozen 元素、内部状态不外泄）、非 `GnssFix`/非法参数拒绝。
8. 附加守卫：reader.py 模块顶层无 `serial` import（AST 扫描，呼应 `test_no_external_access.py`）；pyserial 包装层对 duck-type 端口委托 + close 幂等；config 校验不开口。
9. 回归：ISSUE-024 parser 定向 + core gnss/metadata/manifest 定向 + 全量非硬件 verify.py + ruff + mypy + import。

## 7. 门禁命令（t2 完成时全绿）

```text
./.venv/Scripts/python.exe -m pytest tests/contract/test_gnss_reader.py -q   # 定向（先红灯后绿灯）
./.venv/Scripts/python.exe tools/quality/verify.py                          # 全量非硬件 + ruff + mypy + import
./.venv/Scripts/python.exe -m ruff check src tests
./.venv/Scripts/python.exe -m mypy src
git diff --check && git status --porcelain=v1 -b
```

新基线预期：974 + 新增非硬件测试数 passed / 4 deselected；mypy 44 → 45 文件。

## 8. 参考源审计与排除记录（ISSUE-001 manifest 白名单）

旧 UAV-GPR `uav_gpr_qt.py`（role `gnss_parser_reader_matcher`，branch `my-modifications`，HEAD `194963a0472d1369a0911c24a6dacad5456158c8`，worktree `staged_and_modified`）SHA-256 = `b94d3bfc42e8bf9d30247065e7b3bef52ae52cb6d4111cf8cac52e68b1cc13d2`（ISSUE-001 manifest 冻结，权威记录）。该源在本环境不可达（E: 盘未挂载、D: 盘无副本，ISSUE-024 基线单 §3.5-2 实测）——本 Issue 按契约**重实现**：白名单内可参考「GnssReader/Thread 与 GnssFixCache」行为语义（独立线程、重连、最近 fix 缓存），已排除：地图 UI/在线 Leaflet/CDN、主窗口、NPZ/线上协议、每道 CSV/NPZ 存储、Matplotlib 实时绘图、legacy/continuous 双采集路径、接收线程协议实现。测试夹具为匿名/合成 NMEA（动态 checksum 构造），无参考仓库文件。

## 9. 性能/数据风险

- worker 单线程解析 + 双小锁（状态 Condition 锁、缓存锁，无嵌套）——不阻塞采集/UI；高频输入由容量/时间双上限 + overflow 同步保证内存有界。
- `received_monotonic_ns` 单调域：generation 重连不重置时钟域；缓存跨重连保留由时间窗自然淘汰（D7）。
- **阈值参数（captain 裁决 3，2026-09-02 修订）**：`stale_after_s` 与 `rmc_pair_window_s` 均为**必填构造参数、无静默默认**——调用方（后续应用层）从 MissionConfig 的 GNSS 最大年龄（gnss_max_age_s）提供 stale 阈值，ISSUE-025 不反向耦合 config；本 Issue 仅在状态发布（stale 判定）与缓存淘汰（cache_max_age_s）中使用此类参数。**缓存历史窗口 `cache_max_age_s` 独立于 stale 阈值**（默认 120.0s，显式传入可覆盖）：GNSS.md §5 明示「超过 stale_after_s 的 fix 不可用于地图当前点，但可作为历史诊断记录」，且 ISSUE-026 需要窗口 > 阈值才能区分 stale 与 out_of_range——二者共用单一参数会破坏该语义，故 stale_after_s 只管状态发布、cache_max_age_s 只管缓存淘汰。缓存容量 `cache_max_items`（默认 256）与 `read_chunk_size`（默认 1024）、退避 `backoff` 属内存/传输策略参数，保留显式文档化默认值。
- 回退方式：单一新模块 + 新测试文件，整体回退即删除两文件并还原 M05 L44；无 schema/协议/公共契约变更。

## 10. 执行日志（实际数字，2026-09-02 实测）

环境：Windows `.venv` = `./.venv/Scripts/python.exe`（Python 3.13.14，pytest 8.4.2，ruff 0.16.5，mypy 1.20.2，pyserial 3.5）；执行会话经 WSL→Windows 互操作驱动（pwsh 承载，退出码实测可靠；WSL 直跑 Windows exe 的退出码在本环境不传播，故全部门禁以 pwsh `$LASTEXITCODE` 记录）。

| 步骤 | 命令 | 退出码 | 结果 |
|---|---|---|---|
| 红灯（实现前） | `./.venv/Scripts/python.exe -m pytest tests/contract/test_gnss_reader.py -q` | 2 | `ModuleNotFoundError: No module named 'uav_gpr.positioning.reader'`，collection error——失败测试先行证据 |
| 实现后首轮 | 同上 | 1 | 17 failed / 20 passed（85.6s）——发现 fake serial 缺陷：脚本适配器 `read` 按 `item[:max_bytes]` 截断并**丢弃余量**（真实串口 OS 缓冲会保留），>64 字节语句被截断无法成行；修复 fake 为 pending 缓冲排空语义（非产品代码缺陷，测试替身修复）；另修复 ruff UP035/B905 |
| 绿灯 | `./.venv/Scripts/python.exe -m pytest tests/contract/test_gnss_reader.py -q` | 0 | **37 passed in 0.33s**（27 个测试函数，参数化展开 37 项；无固定 sleep，全部事件/Condition 驱动） |
| 定向+依赖回归 | `./.venv/Scripts/python.exe -m pytest tests/contract/test_gnss_reader.py tests/contract/test_nmea.py tests/unit/test_core_gnss.py tests/unit/test_core_metadata.py -q` | 0 | **144 passed in 0.43s**（37 新增 + 60 nmea + 47 core gnss/metadata） |
| 全量 pytest | `./.venv/Scripts/python.exe -m pytest -m "not hardware and not slow" -q` | 0 | **1011 passed, 4 deselected in 127.61s**（978→1015 collected；974 基线 + 37 新增，与预期一致） |
| 全量门禁 | `./.venv/Scripts/python.exe tools/quality/verify.py` | 0 | pytest 1011/4 → ruff `All checks passed!` → mypy `Success: no issues found in 45 source files`（44→45 含 reader.py）→ `package import ok`；`[quality] all gates passed` |
| ruff 显式 | `./.venv/Scripts/python.exe -m ruff check src tests` | 0 | `All checks passed!` |
| mypy 显式 | `./.venv/Scripts/python.exe -m mypy src` | 0 | `Success: no issues found in 45 source files` |
| import | `./.venv/Scripts/python.exe -c "import uav_gpr; from uav_gpr.positioning.reader import GnssReader; ..."` | 0 | `import ok` |
| Git | `git diff --check` | 0 | clean；`git status --porcelain=v1 -b` = `## main...origin/main` + M05（M）+ 计划/reader/测试（??）+ t1 基线单（??，t1 交付物，非 t2 改动） |

执行偏差记录：

1. 首轮绿灯失败根因是**测试替身**（ScriptedSerialAdapter 截断丢弃余量），非 reader.py 缺陷；修复方式为把 fake 的 read 语义对齐真实串口缓冲（pending 字节缓冲跨 read 保留），并同步把参数化拆行测试的 reader `read_chunk_size` 对齐喂入分块（1/2/7/4096 字节逐次读取，强化跨 read 边界拼行验证）。产品代码未因此改动。
2. 实现内部自检修正（均在红灯后、绿灯前完成，属最小实现的一次成型迭代）：`__init__` 中 `_status` 初始化顺序、移除未用 import、`read` I/O 错误重连路径补退避等待（防连续 I/O 错误紧密循环，GNSS.md §3「连续 I/O 错误触发有退避的重连」）、`import serial` 仅保留 `import-untyped` ignore（45 文件 mypy strict 实测通过）。
3. 门禁基线推进：974 → **1011 passed / 4 deselected**（+37）；mypy 44 → **45 文件**；ruff/mypy/import 全绿；工作树仅含 inScope 4 路径 + t1 基线单，无缓存/日志/实测数据残留（`.pytest_cache` 等 git-ignored）。

## 11. captain 裁决修订记录（2026-09-02，完成登记后到达）

captain 对 t1 提出的 5 个决策点作出裁决（单模块 ✓、测试落点 ✓、P3-1 不扩 scope ✓ 均与已交付实现一致；两处实质修订立即执行）：

| 裁决点 | 原实现 | 修订 |
|---|---|---|
| stale 阈值来源（裁决 3） | `stale_after_s: float = 10.0`（静默默认） | **必填关键字参数、无静默默认**；调用方后续从 MissionConfig gnss_max_age_s 提供，本 Issue 不反向耦合 config；仅在状态发布与缓存淘汰中使用（缓存历史窗口独立，见 §9） |
| GGA/RMC 配对窗口（裁决 4） | `rmc_pair_window_s: float = 10.0` | **必填关键字参数、无静默默认，验证 `0.0 ≤ w ≤ 2.0`**（同秒或 ≤2s 窗口内配对，边界 2.0s 含）；RMC-only 降级路径明示（`NO_FIX` 状态 + 指标，不伪造 fix） |

修订红绿证据：

- 修订红灯：改签名+验证后跑定向 → `28 failed, 9 passed in 0.46s`，exit 1（make_reader 旧配置 `rmc_pair_window_s=10.0` 触发新验证 ValueError——失败测试先行证据）。
- 修订绿灯：make_reader 窗口改 2.0、原 11s 过龄 RMC 测试改为 2.5s、新增「恰好 2.0s 边界仍配对」与「必填阈值验证（TypeError/ValueError）」两测试 → **39 passed in 0.33s，exit 0**（34→36 测试函数，参数化后 37→39 用例）。
- 全量门禁复跑数字见下表更新行。
