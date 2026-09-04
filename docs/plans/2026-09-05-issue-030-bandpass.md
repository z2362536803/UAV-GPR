# ISSUE-030 实施计划：ProcessingStage 框架与频域带通

日期：2026-09-05
执行器：AgentTeams `uav-gpr-issue-030-bandpass` 成员 engineer（任务 t2）
基线件：[docs/reports/ISSUE_030_BASELINE_CONFIRMATION.md](../reports/ISSUE_030_BASELINE_CONFIRMATION.md)（main @ `b4d4b0f`，工作树干净，门禁 1137 passed / 4 deselected，参考源本地副本 4/4 哈希对拍一致）
目标 Issue：ISSUE-030（`docs/issues/M06_CALIBRATION_PROCESSING.md` L116–151）；约束文档：`AGENTS.md` §2.1/§3/§9/§10、`docs/PROCESSING.md` §1/§2/§3、`docs/DATA_MODEL.md` §8、`docs/REFERENCE_MIGRATION.md`、t1 基线确认单 §2/§3。

## 1. 目标与用户价值

在 `processing` 层交付**无 UI 的 ProcessingStage 契约**（稳定 stage_name/version、规范参数、输入/输出域检查、不可变 history 追加）与迁移自钢筋仪的 **sin² 四频点频域带通阶段**：Hz 参数、沿 frequency 轴（最后一维）向量化、保持 complex dtype 与 multi-channel、raw 输入绝不修改、输出新对象并精确追加一条可序列化 `ProcessingRecord`。它是 ISSUE-031（IFFT 消费 `frequency_filtered`）、ISSUE-034/035（时域后处理复用同一 stage 协议）与 UI pipeline 编排的直接依据。

## 2. 范围（任务契约 inScope = 4 个精确路径，changedPaths 与此逐一相等）

1. `src/uav_gpr/processing/bandpass.py`（唯一实现模块：`ProcessingStage` Protocol + `StageResult` + `build_bandpass_window` + `BandpassStage`，stage 契约放在本模块以避免拆分第 5 个文件）
2. `tests/contract/test_processing_bandpass.py`（唯一测试文件：契约 + 黄金 + 拒绝矩阵）
3. `docs/plans/2026-09-05-issue-030-bandpass.md`（本计划文档，含迁移清单与执行日志）
4. `docs/issues/M06_CALIBRATION_PROCESSING.md`（仅 ISSUE-030 状态行 L118：`Planned → In progress → Review`，勿动其他条目）

## 3. 明确排除项（M06 L139 + 提示词 + 任务契约）

不实现 IFFT（ISSUE-031）、OSL 应用（ISSUE-032）、空采背景（ISSUE-033）、Dewow/Flat（ISSUE-034/035）、UI pipeline（后续）；不把带通塞入其他函数；不改 `src/uav_gpr/processing/__init__.py`（占位 docstring 原样，测试直接导入 `uav_gpr.processing.bandpass`，沿用 027 先例）；不改 `core/**`（`FrequencySweep`/`FrequencyScan`/`ProcessingRecord`/`ProcessingHistory`/`DataDomain`/`DomainError`/`ErrorCode` 只读消费，不新增错误码）；不改 `docs/reports/**`、`docs/PROCESSING.md`、`docs/reference-baselines/**`、`tools/**` 与参考仓库（只读）；不 commit/push/merge；不新增 inScope 之外文件。

## 4. 设计决策（D1–D9）

- **D1 stage 契约形态**：`ProcessingStage` 为 `typing.Protocol`（runtime_checkable）——`stage_name: str`、`stage_version: str`、`input_domain: DataDomain | frozenset[DataDomain]`、`output_domain: DataDomain`、`apply(source, *, history, executed_utc=None) -> StageResult`。通用规则（域集合检查、record 构造、history 追加）由共享助手 `_run_stage` 实现，未来阶段复用同一协议；**不建平行 history 类型**，追加一律走 ISSUE-007 冻结的 `ProcessingHistory.append`（链式校验 + stage_name 唯一性由其 fail-closed 强制）。
- **D2 输入形态（适配 rebar BScan/FilteredFrequency → core 两容器）**：bandpass 接受 `FrequencySweep`（channel×frequency，对应 history 输入端）或 `FrequencyScan`（trace×channel×frequency）；窗口沿最后一维广播相乘，复数保持（core `_DATA_DTYPE=complex128` 防御拷贝）。输出的**数据域由调用方传入的 history 末条记录输出域决定**（空 history = `frequency_raw`）：这解决了 core 模型不带 domain 字段的事实——raw 与 calibrated 容器形状相同，provenance 全在 history；stage 不猜测而是从 history 读取，避免伪装语义。
- **D3 域检查**：输入域 ∈ {frequency_raw, frequency_calibrated, frequency_background_applied}（频域派生链中带通的合法前驱）；非法（来自 filtered/time 域，即重复滤波/时间域回流）→ `DomainError(PROCESSING_DOMAIN_MISMATCH)` 结构化 context；输出固定 `frequency_filtered`（DATA_MODEL L152：只由带通产生）。record 构造与 append 再经 core 双重 fail-closed（历史起点必须 raw、链断裂拒绝、同 history stage_name 重复拒绝且改 version 不绕过）。
- **D4 sin² 四频点窗（逐字迁移参考数学）**：`DEFAULT_BANDPASS_EDGES_HZ = (0.5e9, 1.0e9, 1.5e9, 2.5e9)`；边频校验 `len==4`、全有限、`0 <= f1 < f2 <= f3 < f4`（乱序/重复退化/非数值/NaN/Inf/个数错 → `INVALID_ARGUMENT`，context 带 `edges_hz`）；频段不相交（`f4 <= axis.min()` 或 `axis.max() <= f1`，必然产生全零窗）→ `OUT_OF_RANGE` 拒绝（对参考行为的显式收紧，见迁移清单 M2）；频率轴自身非严格递增/非有限由 core 轴校验挡在前面（`NON_INCREASING_AXIS`/`NON_FINITE_AXIS`）。响应：<f1 与 >f4 为 0；f1→f2 `sin²(0.5π(f−f1)/(f2−f1))`；f2→f3 通带 1；f3→f4 `sin²(0.5π(f4−f)/(f4−f3))`；边界点闭区间归属与参考一致。float64 一维窗数组。
- **D5 参数规范化**：record `parameters` 恒为 `{"edges_hz": [f1,f2,f3,f4]（Python float 列表，JSON-safe）, "window": "sin_squared"}`；`to_dict()`/`parameters_canonical_json()` 可序列化往返；测试断言精确值与类型。
- **D6 时间与版本**：`executed_utc` 缺省取注入时钟（默认 `SystemClock().utc_now()`，禁 sleep；测试用 `ManualClock` 或显式 datetime），naive datetime 由 `ensure_utc` 拒绝；`software_version` 取 `uav_gpr.__version__`（`0.1.0.dev0` token 合法）；`stage_version="1.0"`。
- **D7 raw 不变与输出容器**：输入数组 bytes-backed 永不可写（core 保证），乘法产生新数组后按输入类型重建同型容器（sweep→`FrequencySweep`、scan→`FrequencyScan`），metadata 原样传递；输出数组仍是 core 的 never-writable 快照。测试断言输入对象 id/内容前后全等、输出 data 非输入 data base、尝试写输入抛 ValueError。
- **D8 黄金样本口径（无隐式耦合证明）**：黄金 = ①冻结参考源 `bandpass.py` SHA-256（t1 对拍 manifest 一致）；②其窗公式独立重算的字面量（6 频点 [0.4,0.6,0.75,1.0,1.8,2.5] GHz → [0.0, 0.09549150281252627, 0.5000000000000001, 1.0, 0.7938926261462365, 0.0]，2026-09-05 于本 venv 按参考逐式计算并内嵌 repr 字面量）；③双通道确定性合成输入（real=i+1, imag=cos(0.7i)*2 / real=-0.5i, imag=1+0.25i²）及其乘窗输出的 24 个复数字面量。**独立第三方 Butterworth（scipy.signal.butter N=4 bandpass，边缘归一化 0.25/0.75·fs）在同一轴上明显偏离 sin² 值**（如 0.75GHz 处 butter≈0.7 vs sin²=0.5），断言二者不相等以证明带通数学未偷换成 IFFT/其它滤波器隐式耦合（验收「带通与 IFFT 无隐式耦合」的可测化）。测试文件不 import scipy（依赖声明已含，仅作口径记录说明，若跑对照脚本则放系统临时目录不留产物）。
- **D9 错误映射（不新增 code）**：非法边频/非标量边频/非 Mapping 之外的参数问题 `INVALID_ARGUMENT`；不相交 `OUT_OF_RANGE`；错误域 `PROCESSING_DOMAIN_MISMATCH`；不支持的输入类型 `TypeError`（编程错误而非数据域错误，与 core `from_sweeps` 口径一致）；消息一律 ASCII。

## 5. 文件改动（精确路径，无 glob）

| 路径 | 动作 | 内容 |
|---|---|---|
| `src/uav_gpr/processing/bandpass.py` | 新增 | D1–D9：Protocol + StageResult + build_bandpass_window + BandpassStage |
| `tests/contract/test_processing_bandpass.py` | 新增 | §6 测试矩阵（含黄金字面量），纯确定性、无 sleep |
| `docs/plans/2026-09-05-issue-030-bandpass.md` | 新增 | 本文档 |
| `docs/issues/M06_CALIBRATION_PROCESSING.md` | 修改 | 仅 L118 状态行 `Planned → In progress → Review` |

## 6. 测试矩阵（失败测试优先，先红灯后绿灯）

1. **正常路径 sweep**：单通道 FrequencySweep × 默认边频 → StageResult 输出新 FrequencySweep、shape/dtype complex128、history 恰 1 条 record（字段逐项断言：stage_name="frequency_bandpass"、version、params 精确、in/out 域、software_version、executed_utc=clock）。
2. **多通道 scan**：双通道 trace×channel×frequency 输入 → 输出 shape 保持、metadata 原样、逐通道结果等于各自 sweep 结果（通道一致性）。
3. **黄金对拍**：6 频点轴 + §D8 双通道输入 → 窗值/输出 24 复数字面量精确匹配（exact，同一 IEEE 表达式）；通带 bit-exact identity、阻带恰为 0。
4. **域链**：空 history（raw 输入）与三级合法前驱 history（calibrated / calibrated+background / raw+background）均通过且 record.input_domain 正确；time_base / frequency_filtered 末域输入 → PROCESSING_DOMAIN_MISMATCH。
5. **重复 stage**：history 已含 bandpass record → 再次 apply 被拒（append 层 INVALID_ARGUMENT）；改 stage_version 不能绕过（构造第二条不同 version 的同名 record 直接 append 亦拒）。
6. **非法四频点**：3 个/5 个边频、乱序、f1>f2、负值、NaN/Inf、bool/int 混入 → INVALID_ARGUMENT（code 断言 + context.edges_hz 回显）。
7. **频段不相交**：边频全部高于轴 / 全部低于轴 → OUT_OF_RANGE；部分相交（f1 < max 但 f4 > max）合法。
8. **raw 不变**：apply 前后输入对象 is、内容 array_equal；输入 data.flags.writeable == False；输出 data base 不指向输入。
9. **dtype/history 序列化**：record.to_dict round-trip（ProcessingRecord.from_dict）相等；parameters canonical JSON 确定序。
10. **无隐式耦合**：sin² 窗 ≠ Butterworth(zpk 模拟 butter 参数准备) 第三方对照（差值显著 > 1e-3），且不引用任何 time-domain/IFFT 符号。
11. **协议合规**：`isinstance(BandpassStage(), ProcessingStage)` runtime check；`apply` 返回类型随输入。
12. **时钟注入**：显式 executed_utc datetime 直通；naive datetime → NAIVE_DATETIME（ensure_utc 抛）；缺省 SystemClock 可用且为 aware UTC。

## 7. 迁移清单（提取契约，非复制）

| # | 参考源（D:\博士任务\rebar-inspector，只读） | 处置 |
|---|---|---|
| M1 | `src/rebar_inspector/processing/bandpass.py`（SHA-256 `3ee559e33e95c71702b04fe19eb9a24d2f676206d0b5471ec1e5038e17c38d51`，与 docs/reference-baselines/manifest.md 冻结值一致） | **迁移**：`_validate_edges` 约束（4 值/有限/0≤f1<f2≤f3<f4）、`build_bandpass_window` sin² 分段公式与边界闭区间、默认四边频 Hz、`filtered = data * window` 沿最后维广播、record params 键名（edges_hz/window=sin_squared） |
| M2 | 同上 | **收紧适配**：参考允许不相交（产出全零窗静默通过）；本项目按 M06 验收「频段不相交必须拒绝」加 `OUT_OF_RANGE` 守卫（部分相交仍合法） |
| M3 | 同上 | **排除**：`BandpassConfig` 冗余类（与 stage 构造参数重复，pipeline 组合入口属后续 Issue）；`source.history` 内嵌式输入（UAV core 频域模型无 history 字段，改为显式 `history=` 参数注入，见 D2）；再次滤波 FilteredFrequency 输入（违反 UAV「同 history stage 唯一 + filtered 不得作输入」契约） |
| M4 | `src/rebar_inspector/processing/_stage_common.py`（`5ee8f31c…`） | **排除**：`FilteredFrequency`/`ProcessedFrequency` 中间容器（UAV 用 DataDomain + 元组容器表达域，不需要新类型承载）；OSL provenance 校验族（ISSUE-032 职责）；position/trigger/BScan 字段（UAV FrequencyScan 无此语义） |
| M5 | `src/rebar_inspector/core/history.py`（`077c8b29…`） | **不迁移**：UAV `core/time_domain.py`（ISSUE-007，`cfa8271f…`）已是权威 history 实现，本 Issue 只消费 |
| M6 | `LibreVNA采集速度测试/programs/frequency_bandpass.py` 与 MATLAB `BPF_freq.m`（参考链上游，不在本机） | 仅作黄金口径文献记载；对拍基准取 M1 文件的公式字面量 |

## 8. 执行日志

- 2026-09-05 t1 完成（基线 Ready）；t2 认领（attempt 3fe71031）。
- 计划落盘（本文件）→ 契约测试红灯（`ModuleNotFoundError: uav_gpr.processing.bandpass`，collection error，exit 2）→ 实现 → 定向绿灯。
- 红灯/迭代期发现并修正的口径（记录备 t3）：
  1. fixture 合成 `frequency_calibrated`/`frequency_background_applied` 历史必须携带对应引用 ID（core `_validate_references` fail-closed），测试工厂已补 `CalibrationProfileId`/`BackgroundReferenceId`；
  2. 「重复 stage + 改 version 不绕过」在现行 core 下无法构造链合法双 bandpass 历史（第二条 filtered hop 先被 transition/chain 门拒绝）——探针改为临时放宽 `_validate_transition`/`pairwise`（finally 恢复）证明唯一性规则独立生效，真实路径断言 stage 域门 `PROCESSING_DOMAIN_MISMATCH`；
  3. NaN/Inf 边频回显进 DomainError context 会被 core JSON-safe 守卫拒为 TypeError → `_edges_context` 将非有限值转 repr 字符串，错误仍为结构化 `DomainError(INVALID_ARGUMENT)`。
- 最终矩阵：**32 passed**（tests/contract/test_processing_bandpass.py，无 sleep、无 scipy 运行时依赖、纯确定性字面量黄金对拍 exact-equality）。
- 全量门禁 verify.py：**1169 passed / 4 deselected in 269.88s**（基线 1137 + 新增 32）、ruff `All checks passed!`、mypy `Success: no issues found in 50 source files`（+1 模块）、package import ok，exit 0。
- M06 L118 状态行 `Planned → Review`。changedPaths = inScope 4 路径逐一相等。未 commit/push。
