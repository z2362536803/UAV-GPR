# ISSUE-035 实施计划：Flat Reflection 时域阶段

日期：2026-09-05
执行器：AgentTeams `uav-gpr-issue-035-flat-reflection` 成员 engineer（任务 t2）
基线件：[docs/reports/ISSUE_035_BASELINE_CONFIRMATION.md](../reports/ISSUE_035_BASELINE_CONFIRMATION.md)（main @ `8accb76` ahead 3——034 推送链后台重试中如实记录；工作树干净；门禁基线 1342 passed / 4 deselected + ruff + mypy(54) + import 全绿；依赖 030/031 Done 实测证据齐全；参考源本地只读副本哈希与 manifest 冻结值逐一相等）
目标 Issue：ISSUE-035（`docs/issues/M06_CALIBRATION_PROCESSING.md` L301–336）；约束文档：`AGENTS.md` §3/§9/§10、`docs/PROCESSING.md` §2/§6、`docs/CALIBRATION.md` L9–10 概念边界、`docs/REFERENCE_MIGRATION.md` L29、t1 基线确认单 §3。

## 1. 目标与用户价值

在 `processing` 层交付**独立的 FlatReflectionFilterStage**：沿 `TimeDomainScan` 第 0 维（trace 轴，测线方向）减去局部滑动平均（edge 填充），压制近似水平的层状背景，凸显倾斜/双曲的局部目标响应，把 `time_base`（或链上合法的 `time_processed` 前驱，如 034 dewow 输出）转换为全新 `time_processed` 对象并追加完整可复现 provenance。它与 033 空采背景减除（频域全局复数参考）**语义正交且绝不等同**（CALIBRATION.md L9–10），并可能削弱连续层状反射或与测线方向一致的目标——该风险说明随模块 docstring 落盘（PROCESSING.md §6）。窗口单位是**道数**（奇数 ≥3，构造期钉死），无秒→样本换算；O(N) 累积和、complex128 全程保留。它是 ISSUE-036（编排/安全回放）的直接前置。

## 2. 范围（任务契约 inScope = 4 个精确路径，changedPaths 与此逐一相等；captain 锁定口径，无 __init__.py）

1. `src/uav_gpr/processing/flat_reflection.py`（唯一实现模块：`FlatReflectionFilterStage` + 纯函数面 `validate_window_traces` / `centered_moving_mean_along_axis`——stage 契约复用 030 的 `ProcessingStage`/`_record_for`/`_input_domain_of`，返回类型复用 031 的 `TimeDomainStageResult`，均 import 不复制）
2. `tests/contract/test_processing_flat_reflection.py`（唯一测试文件：契约 + 黄金三方对拍 + 拒绝矩阵 + 输入不变性 + 顺序/history + 性能 smoke）
3. `docs/plans/2026-09-05-issue-035-flat-reflection.md`（本计划文档，含迁移清单 D/M 节与执行日志）
4. `docs/issues/M06_CALIBRATION_PROCESSING.md`（仅 ISSUE-035 状态行 L303：`Planned → In progress → Review`，勿动其他条目）

注：沿用 031/033/034 先例——测试直接 import `uav_gpr.processing.flat_reflection`，**不改** `processing/__init__.py`（不在 inScope）。

## 3. 明确排除项（M06 L325 + 提示词 + 任务契约 nonGoals）

不做实时增量近似（PROCESSING.md §6 的增量预览/批处理语义属未来 UI/编排议题）、不做 UI 默认启用（默认可选性由 stage 不被任何链路自动挂载体现）；不与 AirBackground 混名/混 history（`air_background_subtraction` 属频域链，token/域/轴三重区分，见 D7）；不改 `core/**`（`TimeDomainScan`/`ProcessingRecord`/`ProcessingHistory`/`DataDomain`/`TimeDomainKind`/`ErrorCode` 只读消费，不新增错误码——复用 `INVALID_ARGUMENT`/`DTYPE_MISMATCH`/`NON_FINITE_AXIS`/`SHAPE_MISMATCH`/`PROCESSING_DOMAIN_MISMATCH`）；不改既有 stage 公共语义（`bandpass.py`/`time_domain.py`/`osl_calibration.py`/`background_subtraction.py`/`dewow.py`/`__init__.py` 零字节，import 复用）；不 import 参考项目（`rebar_inspector` 零引用，参考文件只读不改）；不改 `docs/reports/**`、`docs/PROCESSING.md`、`docs/CALIBRATION.md`、`docs/TESTING.md`、`docs/adr/**`、`docs/reference-baselines/**`、`tools/**`、`src/uav_gpr/storage/**`；输入数组绝不修改；不 commit/push/merge；完成后停止，不进入 ISSUE-036。

## 4. 设计决策（D1–D9）

- **D1 stage 契约与域映射**：`FlatReflectionFilterStage` 结构化实现 030 冻结的 `ProcessingStage` Protocol（尾部 `assert isinstance(...)` 静态防漂移，031/034 先例）：`stage_name="flat_reflection_filter"`（**与 034 dewow.py L102 `_FLAT_STAGE_NAME` 反向顺序守卫 token 逐字一致**，也是参考与 034 测试 fixture `_flat_record` 的既有名称）、`stage_version="1.0"`、`input_domain=frozenset{TIME_BASE, TIME_PROCESSED}`、`output_domain=TIME_PROCESSED`。空 history ⇒ `_input_domain_of` 给 FREQUENCY_RAW ⇒ 不在允许集 ⇒ `PROCESSING_DOMAIN_MISMATCH` 拒；频域中间域末域同样被域门拒。追加走 `ProcessingHistory.append`（同 history `stage_name` 唯一由 core fail-closed 强制，bump version 不绕过 ⇒ "重复 flat 拒绝"第二道门）。输入容器限定 `TimeDomainScan`（参考同款：`TimeDomainData` 无测线方向必须拒——本项目模型即 scan-only，TypeError 守卫保留该语义）。
- **D2 窗口参数（与 dewow 的本质差异，钉死）**：`window_traces: int`＝沿 trace 轴的**道数**（非物理秒，无 dt 换算链——trace 间隔由飞控/位置决定且不规则，统计意义只在道序上成立，t1 §3.3-2）。构造签名 `FlatReflectionFilterStage(window_traces: int = DEFAULT_FLAT_REFLECTION_WINDOW_TRACES)`；`DEFAULT_FLAT_REFLECTION_WINDOW_TRACES: Final = 101`（沿用参考默认值并在 record parameters 显式落盘）。校验 `validate_window_traces`：bool/非 int ⇒ TypeError 语义的结构化拒绝（`INVALID_ARGUMENT`，对齐 UAV DomainError 纪律——参考侧抛裸 TypeError 属其自身风格，不迁移异常类型）；`< 3` 拒（消息含"window=1 会让输出全零"安全性加固说明，参考 verbatim 语义）；偶数拒（奇数保证当前 trace 居窗心）。`≤ n_traces` 依赖实际输入，apply 期检查（D6③）。
- **D3 数值核心（`centered_moving_mean_along_axis(data, *, axis, window, padding) -> ndarray`）**：参考 `_time_stage_common._centered_moving_mean`（SHA-256 `e0c201b5…`）verbatim 迁移：moveaxis(target→0) → pad `(half,half)` 沿第 0 维（mode 由参数固定传入）→ `np.cumsum(dtype=complex128)` 前置零切片 → `cum[window:] - cum[:-window]` → `/window` → moveaxis 复原。O(N)、real/imag 各自累加。**本模块契约调用恒为 `axis=0, padding="edge"`**（boundary=首尾道值平铺）；核保持 axis/padding 泛化是为黄金对拍独立转写与未来复用，但对外常量 `FLAT_PADDING: Final = "edge"`、`FLAT_AXIS: Final = 0` 是契约不是选项。守卫：data 必须 complex128 且 ndim≥1（`DTYPE_MISMATCH`，与 034 核同姿势）；window 奇数 ≥3（`INVALID_ARGUMENT`）；目标轴长度 ≥ window（`INVALID_ARGUMENT`）。034 的 `centered_moving_mean`（沿最后轴、reflect）零改动——两核并存于各自模块（inScope 无 common 模块路径；扁平化裁决记录于此，036 编排时再评估抽公共）。
- **D4 顺序守卫（推荐 Dewow→Flat，双向闭环）**：正向已由 034 落地（dewow 拒含 flat 的 history）。本 Issue 侧：①`_DEWOW_STAGE_NAME="dewow"` 存在于 history ⇒ 合法（推荐顺序，直放通过）；②history 已含 `flat_reflection_filter` ⇒ 本 stage 显式第一道门拒（`INVALID_ARGUMENT`，消息"dewow may be applied only once…"同款结构化姿势：`flat reflection filter may be applied only once per history`）；③**错序的另一半**：flat 之后不得再有任何 time 域后处理挂错 predecessor 的既成事实由 core 域链+唯一性兜底，本 stage 无需重复实现反向守卫（034 guard 3 已挡）。测试以真 `DewowStage` 串联证明 dewow→flat 全链合法（D8 场景）。
- **D5 入场守卫序列（仿 034 guard 1-6，全部 fail-closed）**：①`source` 非 `TimeDomainScan` ⇒ TypeError；`history` 显式传入须 `ProcessingHistory` 否则 TypeError；`executed_utc` naive ⇒ `ensure_utc` 拒（NAIVE_DATETIME），缺省注入 clock（默认 SystemClock 一次读取，无 sleep）；②guard 1 末域 ∈ {TIME_BASE, TIME_PROCESSED} ⇒ 否则 `PROCESSING_DOMAIN_MISMATCH`（context 含 stage_name/input_domain/allowed）；③guard 2 重复 flat（stage 级第一闸，records 扫描）；④guard 3 kind/predecessor 一致性（`TimeDomainKind(input_domain.value) is source.kind`，defense-in-depth，core `_validate_history_kind` 兜底）；⑤guard 4 shape 复核 `ndim==3` 且 `time_axis_s.size == data.shape[-1]`（`SHAPE_MISMATCH`）；⑥guard 5 复数有限性入场闸（`NON_FINITE_AXIS` + 首坏位 flat_index——core readonly 不查有限性）。
- **D6 输出重建与不可变**：`local_mean = centered_moving_mean_along_axis(source.data, axis=0, window=self._window_traces, padding="edge")`；`filtered = source.data − local_mean`（新数组）→ 新 `TimeDomainScan(channels/time_axis_s/metadata 全量透传, kind=TIME_PROCESSED, history=new_history)`（core 构造防御拷贝置 never-writable）；返回 `TimeDomainStageResult(source, history, domain=TIME_PROCESSED)`。输入 source/data bytes 前后全等（测试断言）。短测线：`window_traces > n_traces` ⇒ `INVALID_ARGUMENT`（消息含总道数与减小窗口指引）。
- **D7 与 AirBackground 语义区分（验收"不混名/混 history"+风险文档）**：类名 `FlatReflectionFilterStage` vs 033 `AirBackgroundSubtractionStage`；stage token `flat_reflection_filter` vs `air_background_subtraction`；运算域 `time_base/time_processed → time_processed` vs `frequency_* → frequency_background_applied`；运算对象 = 沿测线方向的**数据内部**局部均值 vs 一个**外部空采复数参考向量**。模块 docstring 与类 docstring 双处写明 CALIBRATION.md L9-10 边界原文语义，并登记 PROCESSING.md §6 风险：**可能削弱连续层状反射或与测线方向一致的目标，因此默认可选**。两者共存于一条 history 合法（链序：…→air_background(频域)→bandpass→IFFT→dewow→flat），测试专列"不混名"用例。
- **D8 record parameters（JSON-safe、可复现）**：经 `_record_for(stage_name="flat_reflection_filter", stage_version="1.0", input_domain=末域, output_domain=TIME_PROCESSED, executed_utc=stamp)`；parameters = `{operation:"subtract_local_trace_mean", axis:"trace_first", padding:"edge", window_traces, trace_sample_count, reference_source_sha256:{flat_reflection.py:"89e3c01b…87df0", _time_stage_common.py:"e0c201b5…"}}`（参考 record params 键结构 verbatim 迁移 + UAV 增补 `trace_sample_count` 与冻结源哈希入 provenance，034 D8/D9① 同姿势）。
- **D9 黄金口径（三方交叉，031/034 先例）**：①冻结参考源 SHA-256：`processing/flat_reflection.py = 89e3c01b3ce4135fd96495b27a67ff69760224bdc80c9144fd9aeeaf4ca87df0`、`processing/_time_stage_common.py = e0c201b55acbaece0edb1546bbb8a00492874bb79fb9caf789d5ba416d333c81`（t1 §2 与 manifest.md 实测对拍一致；写入测试模块 docstring 与 record parameters）；②参考核 verbatim 移植体 2026-09-05 在本 venv 独立重算的内嵌字面量（场景 A：5×1×4 单通道小缓冲 W=3 edge；场景 B：3×2×4 全缓冲 mean/out 展平，输入 float64 字面量钉死杜绝生成器漂移；场景 C：4×1×2 W=3 首尾平铺边界），canonical golden JSON SHA-256 由测试逐位钉死；③独立转写比对：朴素 O(N·W) edge-index 循环（`idx = clip(i+j-half, 0, N-1)`）在场景 B 缓冲整缓冲 **bit-exact**（dyadic 网格探针，求和顺序无关域），一般缓冲 allclose(rtol=1e-12)。生成脚本置于工作区外（/tmp/flat_gold），仓库零污染。

## 5. 文件改动（精确路径，无 glob）

| 路径 | 动作 | 内容 |
|---|---|---|
| `src/uav_gpr/processing/flat_reflection.py` | 新增 | D1–D8：常量 + 纯函数面 + FlatReflectionFilterStage + conformance assert |
| `tests/contract/test_processing_flat_reflection.py` | 新增 | §7 测试矩阵（含黄金字面量与性能 smoke），纯确定性、无 sleep、无硬件、无文件 IO |
| `docs/plans/2026-09-05-issue-035-flat-reflection.md` | 新增 | 本文档 |
| `docs/issues/M06_CALIBRATION_PROCESSING.md` | 修改 | 仅 L303 状态行 `Planned → In progress → Review` |

## 6. 迁移清单（提取契约，非复制；参考 = 本地只读副本 `D:\博士任务\rebar-inspector`，零修改）

| # | 参考文件（SHA-256 见 D9①） | 迁移内容 | 适配点 |
|---|---|---|---|
| M1 | `processing/flat_reflection.py` `_validate_window_traces` | int/奇数/≥3 三判 + window=1 全零拒绝理由（安全性加固） | bool/非 int 从裸 TypeError 改 `DomainError(INVALID_ARGUMENT)` 结构化（UAV 错误纪律）；`<=n_traces` 仍在 apply 期 |
| M2 | 同上 `FlatReflectionFilterStage.apply` 主体 | TimeDomainData 拒绝语义（scan-only）→ `_reject_duplicate_stage("flat_reflection_filter")` → 有限性 → `window>n_traces` 拒 → 沿 axis=0 edge 减均值 → record `{window_traces,padding,operation,axis}` → 输出 TIME_PROCESSED | UAV `ProcessingStage` 协议（history/executed_utc/clock 注入）、`_record_for`/`_input_domain_of`、`TimeDomainStageResult`、DomainError 全链、reference 哈希入 parameters、不 import 参考 |
| M3 | 同上模块 docstring | "沿测线方向局部背景减除 ≠ 空采背景减除（后者沿频率轴减全局空采复数向量）；可能削弱连续层状反射/平行测线钢筋响应，必须可选" | 对齐 CALIBRATION.md L9-10 与 PROCESSING.md §6 原文口径；"钢筋"措辞泛化为"与测线方向一致的目标"（UAV 场景） |
| M4 | `_time_stage_common.py` `_centered_moving_mean` | moveaxis/pad(half,half)/cumsum(complex128)/前置零切/差分/window/moveaxis 复原，O(N)，实虚独立 | 收纳于 `flat_reflection.py`（D3 扁平化裁决）；对外契约固定 axis=0+edge，泛化签名仅供核内与黄金转写 |
| M5 | 同上 `_check_complex_finite` | NaN/inf 复数入场补闸 | DomainError NON_FINITE_AXIS + 首坏位 flat index（034 姿势） |
| M6 | 同上 `_reject_duplicate_stage` | 稳定名唯一性第一道门 | records 显式扫描 + core 唯一性第二道门（bump version 不绕过，测试隔离证明） |
| M7 | 同上 `_extract_time_input` / `_rebuild_time_output` | 输入归一 + 输出统一 TIME_PROCESSED、元数据全量沿用 | UAV 只有 TimeDomainScan 一种容器（031 D2），无双形态分支；`_record_for` 代 ProcessingRecord 裸构造 |
| 不迁移 | `数据处理函数/flat_refelction_filter.py`（参考仓库未跟踪原型）；`[time, trace]` 布局与 float64 假设（正式模型 trace×channel×time complex128，参考 docstring 自己声明不照搬）；`FlatReflectionConfig` 值对象（034 M4 同理：stage 持校验后配置 + `parameters` property 已是序列化配置面，避免平行类型）；裸 TypeError/ValueError 异常面；`TimeDomainData` 支持 | — | — |

## 7. 测试矩阵（失败测试优先；覆盖 captain 指派全部维度）

1. 协议合规：isinstance ProcessingStage；名称/版本/输入域 {time_base,time_processed}/输出域 time_processed 精确；`TimeDomainStageResult.domain=TIME_PROCESSED`；非 scan 输入 TypeError；window_traces 非法矩阵（bool/str/float/NaN/负/<3/偶数）INVALID_ARGUMENT；恰 ==n_traces 奇数窗通过。
2. **水平背景**：沿 trace 轴恒定（各道同值）场 ⇒ 输出 ≈ 0（abs max < 1e-12 相对界）——flat 的核心去除对象；常数含复平面偏移（4.0−2.5j 型）残差实测 0。
3. **局部目标**：单 trace δ 脉冲 ⇒ 输出 = δ − (1/W)·edge_pad(δ)（t=0/首尾平铺手算钉死）；大窗下脉冲道近保留（衰减 ≤ 1/W 系数）；水平层状背景分量被消、局部目标残差显著大于背景残差（定性对比断言）。
4. **复数等价**：`flat(a+bj).real == flat(a)` 且 `.imag == flat(b)` 逐位；线性 skew 探针 dcm（z − z ≡ 0 全零指纹，证 real/imag 独立）。
5. **多通道/多 time**：双通道逐 slice 与整缓冲一致（互不串扰）；shape/dtype complex128 不变；metadata per-trace 透传（有/无两种）；channels/time_axis 全等。
6. **短测线**：n_traces < window_traces ⇒ INVALID_ARGUMENT（消息含 n_traces 与指引）；n_traces == window_traces 通过（全缓冲一窗，输出 = data − tile(global_mean)）。
7. **窗口边界**：edge 填充手算小例黄金字面量（首道窗 = [x0,x0,x1,…] 平铺语义）；W=3 最小窗逐位核对；偶数/<3/bool/非 int 构造期全拒矩阵。
8. **顺序与 history**：空 history 拒；频域末域前驱拒；重复 flat 双门（真实输出再进 INVALID_ARGUMENT + core 唯一性隔离探针 bump version 仍拒）；**真 DewowStage 串联**：ifft→dewow→flat 全链合法、flat 输入 kind=TIME_PROCESSED、history 两条时间记录顺序/域链正确；legacy_dcgate→flat 合法（TIME_PROCESSED 直前驱不限 dewow）；record 字段全检（parameters 键集合含 operation/axis/padding/window_traces/trace_sample_count/reference hashes）；to_dict/from_dict JSON-safe 往返全等；**与 air_background_subtraction 不混名**：同一长链（raw→air_bg→bandpass→ifft→dewow→flat）成立且两 token/两域各自独立。
9. 输入不变性：source.data/time_axis bytes 前后全等 + writeable False；输出新对象、write-protected、写入抛 ValueError；history 旧对象不动、append 返回新实例。
10. 黄金三方（D9）：场景 A/B/C 内嵌字面量 exact equality；朴素 edge-index O(N·W) 转写在 dyadic 缓冲 bit-exact；golden canonical SHA-256 复核。
11. 性能 smoke：512×2×1024 complex128（≈16.8 MB）W=101 单次 apply 墙钟 < 10 s（宽松界，证明 O(N) 累积和无逐窗 Python 循环）。
12. 排除守卫：AST/源码级——模块无 `rebar_inspector`/`storage`/`acquisition`/Qt import；无 `"reflect"` padding 计算路径（仅注释/docstring 提及对照）、无 UI/display/depth 符号；不做增量状态的属性缓存（每次 apply 纯函数）。

## 8. 完成定义与回退

完成 = §7 全绿 + verify.py 全量 ≥1342+新增 passed + ruff + mypy(55 files，预期 +1 模块) + import ok + changedPaths 与 §2 四路径逐一相等 + M06 L303 → Review。回退 = 删除两个新增文件与本计划 + 还原 M06 一行（无跨文件耦合，未 commit 状态下 `git checkout -- docs/issues/...` 即可净退）。

## 9. 执行日志

- 2026-09-05 t1 完成（基线 Ready，attempt f54961f6，verdict=pass）；captain 锁定 t2 inScope 4 路径；t2 认领（attempt 44e08818）。
- 计划先行：本文件 §2–§7 于实现前落盘（迁移清单 M1–M7 + 决策 D1–D9 + 测试矩阵）。
- 黄金字面量生成脚本置于仓库外（`D:\博士任务\flat_gold\gen.py` + `golden.json`），verbatim 移植参考核（`_time_stage_common._centered_moving_mean`）重算场景 A/B/C；朴素 edge-index O(N·W) 转写在 dyadic 场景 B 缓冲 **bit-exact**；canonical golden JSON SHA-256 = `060f8342ce756b4e548ef00ff3c884f86b561efe5dba0881caa99cdd78985c86` 由测试逐位钉死。红灯确认：首轮定向 pytest collection error（`ModuleNotFoundError: uav_gpr.processing.flat_reflection`）——失败在先。
- 实现期口径修正（记录备 t3，均为契约细节，不改数学）：
  1. **DomainError 消息 ASCII 纪律**：core/errors.py L99 强制 message.isascii()——首版"≥/—"类字符触发 ValueError，改 `>=`/`--`（守卫语义不变）；
  2. **window==n_traces 期望修正**：edge 平铺下每行窗口是 clip 多重集 `{x[i-2..i+2] clamp}` 而非固定权重全局均值——断言改为逐行显式 clip-sum 模型（首轮误用加权常数均值）；
  3. **skew 线性探针语义修正**：线性坡的局部均值 = 窗心值 ⇒ interior 输出恒 0（非"透传"），边行按 tiling bias 手算钉死；广播修复 `(7,)→(7,1,3)` 加 `[:, None, None]`；
  4. **history 链构造合规**：frequency_calibrated 生产者必须携带 calibration_profile_id、frequency_background_applied 生产者必须携带 background_reference_id（core provenance 规则）——fixture `_osl_record`/`_air_bg_record` 补齐 id；`gnss_match=None` 元数据必须带 `gnss_missing` 质量原因且第 2+ trace 要求 actual/schedule 字段（metadata passthrough 测试按 034 fixture 姿势修正）；
  5. **顺序守卫双向闭环确认**：错序拒绝由 034 guard 3 承担（dewow 拒含 flat history），flat 侧以真 DewowStage 端到端串联测试证明 dewow→flat 合法链（names=[ifft,dewow,flat]，域链正确）；
  6. **mypy np.pad 重载**：泛化 `padding: str` 不匹配 `_ModeKind` Literal 联合——以守卫后 if/else 绑定 `Literal["edge","reflect"]` 局部变量消解（零 ignore）；ruff F401 清理 2 个未用 import。
- 最终矩阵：**54 passed**（tests/contract/test_processing_flat_reflection.py，无 sleep、纯确定性、性能 smoke 实测远低于阈值）。
- 定向回归：dewow+bandpass+time_domain(processing)+core.time_domain+background_subtraction 五文件 **206 passed**。
- 定向静态：ruff `All checks passed!`；mypy `Success: no issues found in 55 source files`（+1 模块，计划口径 54→55 为预期变化）。
- 全量门禁 verify.py：**1396 passed / 4 deselected in 273.61s**（基线 1342 + 新增 54）、ruff `All checks passed!`、mypy `Success: no issues found in 55 source files`、package import ok，exit 0 `[quality] all gates passed`。日志重定向工作区外 `/tmp/verify_035_t2.log`，仓库零遗留产物（中途一次误生成的仓内 `C:/Users/Public/...` 目录已删除复原）。
- `git diff --check` 干净；M06 L303 状态行 `Planned → Review`（In progress 中间态合并记录于同一编辑，见备注）。changedPaths = inScope 4 路径逐一相等。未 commit/push。
