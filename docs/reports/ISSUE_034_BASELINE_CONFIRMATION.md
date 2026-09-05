# ISSUE-034 开工基线确认单（自动化轮 · engineer）

## 1. 目标 Issue 与依据

- **Issue**：ISSUE-034「Dewow 时域阶段」（`docs/issues/M06_CALIBRATION_PROCESSING.md` L264-299）
- **状态行核查**：M06 L266 ISSUE-034 状态 = `Planned`；映射 FR-012；直接依赖 ISSUE-030、031。
- **工作区根替换声明**：Issue 提示词中 `E:\wurenjiruanjian` 一律替换为当前工作区根 `/mnt/d/博士任务/无人机软件/UAV-GPR`（Windows：`D:\博士任务\无人机软件\UAV-GPR`）。
- **流程依据**：`AGENTS.md`、`docs/issues/README.md`（编号顺序为主执行顺序、依赖为开工门禁；本会话只执行 ISSUE-034）、`docs/ISSUE_REVIEW_STANDARD.md`（t1 基线 → t2 实现 → t3 独立复审）。
- **契约文档**：已读 `docs/PROCESSING.md` §5（Dewow）+ §1/§2（原则与推荐流水线），`docs/DATA_MODEL.md` L152（频域派生链末端 time_processed 语义），迁移规则见 `docs/REFERENCE_MIGRATION.md` L29（带通/IFFT/Dewow/Flat 保持独立 stage 和黄金样本）。

## 2. 只读核查证据（全部实测）

| 项 | 结果 |
|---|---|
| HEAD / 分支 | `main @ 124fe56`（`docs(issues): mark ISSUE-033 Done after automated merge`）；`git status --porcelain` 核查前为空（工作树干净，除本单外无输出） |
| 远端同步 | `git rev-list --left-right --count origin/main...HEAD` = `0 0`：**ISSUE-033 合并链推送已完成**，origin/main 与本地 main 同指 `124fe56`（上一轮"推送后台重试中"已闭合，本单如实记录当下实测值） |
| ISSUE-030 Done 证据 | M06 L118 状态行 = Done（2026-09-05 自动化轮 t3 复审 VERDICT=PASS 后自动合并，见 `docs/reports/ISSUE_030_REVIEW_REPORT.md`）；合并提交 `89fd9bb` 在 HEAD 历史（`git log --grep issue-030`）；tracked 交付物齐全：`bandpass.py`（460 行，`ProcessingStage` Protocol / `StageResult` / `_input_domain_of` / `_record_for` 契约面）+ `tests/contract/test_processing_bandpass.py` ✓ |
| ISSUE-031 Done 证据 | M06 L155 状态行 = Done（t3 复审 VERDICT=PASS 后自动合并，见 `docs/reports/ISSUE_031_REVIEW_REPORT.md`）；合并提交 `9afdfb2` + 标记提交 `b4f6dec` 在 HEAD 历史；tracked `src/uav_gpr/processing/time_domain.py`（720 行，`FrequencyToTimeStage` / `TimeDomainStageResult` / 显示时窗层）+ `tests/contract/test_processing_time_domain.py`（880 行）✓ |
| 依赖定向回归 | `.venv/Scripts/python.exe -m pytest tests/contract/test_processing_bandpass.py tests/contract/test_processing_time_domain.py tests/unit/test_core_time_domain.py -q` 包含于全量 verify（1291 passed，exit 0，见 §4）；三个模块 import ok（package import 门通过） |
| 可执行性 | `src/uav_gpr/processing/` 现有 `bandpass.py`(030)、`time_domain.py`(031)、`osl_calibration.py`(032)、`background_subtraction.py`(033)，**无任何 dewow 先行实现**（`find . -iname "*dewow*"` 仓库内零命中）；M06 中自 034 起首个 `状态：Planned` 条目即 ISSUE-034（L266；035 Flat L303、036 编排 L340 其后）→ **ISSUE-034 为下一可执行 Issue**，无重复实现风险 |
| 参考源可达性 | `E:\钢筋仪软件开发` 本机不可达（`ls E:/` 不存在），沿用 027-033 先例使用本地只读副本 `D:\博士任务\rebar-inspector`（WSL 路径 `/mnt/d/博士任务/rebar-inspector`，存在，只读使用、不修改） |

### 参考源哈希实测（与 manifest.md 冻结值逐一相等）

| 用途 | 参考文件（本地只读副本相对路径） | 实测 SHA-256 | manifest 冻结值 |
|---|---|---|---|
| Dewow 算法主体 | `src/rebar_inspector/processing/dewow.py`（7535 B / 186 行） | `eb6690e7fabf0bc80e051831ab6264e6e6d112b6568fb6dc30556a3a7f030e2c` | 相同 ✓ |
| 时域 stage 共性 | `src/rebar_inspector/processing/_time_stage_common.py`（9224 B / 228 行） | `e0c201b55acbaece0edb1546bbb8a00492874bb79fb9caf789d5ba416d333c81` | 相同 ✓ |
| 参考侧 dewow 测试（口径参照，不迁移） | `tests/test_processing_dewow.py`（14156 B） | `1460740a1807b43548c63cf133127ab7e7f6af764b11c0638e31d95ac4643199` | 不在 manifest 候选表（非迁移对象），仅登记 |

注：manifest worktree dirty 列表中 `?? 数据处理函数/dewow.py` 是参考仓库自己的未跟踪原型脚本（算法来源的上游），本仓库不引用其内容；t2 迁移契约参考源 = 上表前两行的 committed 文件。

关键本项目契约文件哈希（SHA-256 前 16 位，供 t3 交叉核对）：

```text
f707839674ceb5e1  src/uav_gpr/processing/bandpass.py
b7da55717148645b  src/uav_gpr/processing/time_domain.py
30224c9a0091c02b  src/uav_gpr/processing/osl_calibration.py
a96d59f63289a8c8  src/uav_gpr/processing/background_subtraction.py
34f61e720305d301  src/uav_gpr/processing/__init__.py
cfa8271f5ebd545c  src/uav_gpr/core/time_domain.py
4e906f159b1c8599  src/uav_gpr/core/enums.py
9d730ec7a0f7c223  docs/PROCESSING.md
bc02a4d9e1c58083  docs/issues/M06_CALIBRATION_PROCESSING.md
```

## 3. 契约要点（对 t2 实现有约束）

### 3.1 PROCESSING.md §5 + §2 强约束

- §5 Dewow：**沿时间轴减去中心滑动平均**削弱直流/慢漂移；**窗口以物理时间配置并转换为样本数**；**边界策略、奇偶处理和 complex dtype 必须固定测试**。
- §2 推荐流水线：`… -> IFFT (time_base) -> dewow (optional) -> time_processed`；flat reflection 在 dewow 之后（035），两者都是 `time_processed` 生产者。
- DATA_MODEL L152：`time_base`（IFFT）→ `time_processed`（Dewow/Flat Reflection，**可连串**）——但同一 history 内稳定 `stage_name` 不得重复（core `ProcessingHistory.__init__`/`append` 强制，bump version 不能绕过）→ "重复 Dewow 拒绝"由 core + stage 双重 fail-closed。
- AGENTS.md §3：每阶段输入不可变、输出新对象、追加可序列化参数/版本/历史；complex/trace/channel/time 轴与 metadata 完整保留。

### 3.2 core 层既有守卫（t2 直接复用，不新建平行类型）

- `DataDomain.TIME_BASE` / `TIME_PROCESSED`（enums L172-173/L183-184）与 `TimeDomainKind` 现成。
- `_ALLOWED_TRANSITIONS`（core/time_domain.py L117-118）：`TIME_BASE → TIME_PROCESSED`、`TIME_PROCESSED → TIME_PROCESSED` 均已开——支持"dewow 之后再 dewow 被 stage 层拒、但链式到 flat（035）合法"的域拓扑。
- `TimeDomainScan`（core/time_domain.py L656+）：构造即防御拷贝 + 只读化；`trace × channel × time` 形状严格校验；`_validate_history_kind` 要求 kind 与 history 末记录 output_domain 匹配且 history 非空完整——**输入快照非法 history 顺序（如 time_base 挂错历史）在入场即被 core 拒绝**，t2 补显式入口守卫。
- 031 模式沿用：`processing/time_domain.py` 导出 `TimeDomainStageResult(source, history, domain)`（source 限定 `TimeDomainScan`，正是 DewowStage 需要的兄弟返回类型）与 `ProcessingStage` 协议面（从 `uav_gpr.processing.bandpass` import `ProcessingStage`/`_record_for` 等公共符号，同层引用符合 AGENTS.md §9）；sweep 输入无单道时域容器、由 031 归一为一 trace 的 scan——t2 输入即 `TimeDomainScan`，无需新增容器。

### 3.3 参考实现（dewow.py + _time_stage_common.py）迁移契约

1. **算法**：`output = input − centered_moving_average(input, axis=-1, window, padding="reflect")`；累积和 O(N)（moveaxis → pad half/half → cumsum(complex128) → 差分/window → moveaxis 复原），实部虚部各自滑动均值，完整保留 complex128。
2. **窗口换算（秒→样本，明确舍入/奇数策略）**：参考侧用 `window_ns`；`samples = int(round(window·1e-9/dt_s))` → `max(1,·)` → 偶数则 +1 转奇 → `==1` 拒绝（提示增大窗口）→ `> n_time` 拒绝（提示减小窗口）。dt_s 从输入时间轴计算（`median(diff)`，要求 ≥2 点、严格递增、近似等间隔 ≤1e-6 相对偏差），不由调用方传入。**注意**：本项目 `time_axis_s` 单位是**秒**（core/time_domain.py L659），团队目标口径也是"窗口秒→样本"——t2 决策点 D1：参数命名 `window_s`（float 秒）直算 `round(window_s/dt_s)`，或照参考保留 `window_ns` 外壳；无论取哪种，舍入链（round→max(1)→奇化→双端拒绝）必须逐条钉死并有测试。
3. **边界策略**：固定 `"reflect"`（时间轴首尾反射填充，`np.pad(mode="reflect")`）；不做 edge/nearest 可配置（排除项），reference 侧 `padding="edge"` 属 flat（035），本 Issue 不触碰。
4. **入场守卫**（`_time_stage_common.py`）：复数有限性检查（NaN/inf 拒绝——core readonly 不做此检查，时域阶段入场补闸）；`_reject_duplicate_stage(history,"dewow")`；`_reject_flat_before_dewow(history)`——**固定推荐顺序 dewow → flat_reflection_filter**，history 已含 flat 阶段名再 dewow 一律拒绝（非法 history 顺序拒绝项）。
5. **输出重建**：同类型 `TimeDomainScan`，`kind=TIME_PROCESSED`（无论输入 TIME_BASE 还是 TIME_PROCESSED），channels/time_axis_s/metadata/schema_version 全量沿用；新数组经 core 构造防御拷贝置只读。
6. **record parameters**（参考结构，JSON-safe）：`{window(_ns), window_samples, dt_s, padding:"reflect", operation:"subtract_moving_average", axis:"time_last"}` + stage_name/stage_version；本项目经 `_record_for` 写 `ProcessingRecord`，input/output_domain 显式。

### 3.4 团队计划硬约束（t2 inScope 表述以 t2 任务契约为准）

本单不约定文件集；t2 的 inScope 精确 4 路径（含计划文档）以 **t2 任务契约原文为准**，changedPaths 与之逐一相等。（参照 033 先例形态：新 stage 模块 + `processing/__init__.py` 导出 + 契约测试 + M06 状态行；是否含 `docs/plans/…` 计划文档由 t2 契约锁定。）

## 4. 门禁基线（verify.py 复跑，实测）

- `tools/quality/verify.py`（interpreter = `.venv/Scripts/python.exe`，本次命令：`.venv/Scripts/python.exe tools/quality/verify.py`）：
  - pytest (non-hardware)：**1291 passed / 4 deselected in 273.69s**（4 deselected = 硬件哨兵 + opt-in LibreVNA），与团队计划声明的 1291 一致 ✓
  - ruff：`All checks passed!` ✓
  - mypy：`Success: no issues found in 53 source files` ✓（计划口径 53）
  - package import ok；`[quality] all gates passed` exit 0
- 核查前后 `git status --porcelain` 均为空（本单为唯一新增未跟踪文件）；verify 输出重定向至工作区外临时文件（`/tmp/verify_034_t1.log`），仓库内无遗留产物。

## 5. 实施计划摘要（供 t2）

**范围**：`DewowStage`（新文件建议 `src/uav_gpr/processing/dewow.py`，最终以 t2 契约 inScope 为准）实现 `ProcessingStage` 协议：
- `stage_name="dewow"`（与参考及 035 flat 顺序守卫字符串对齐的稳定 snake_case token）、`input_domain={TIME_BASE, TIME_PROCESSED}`、`output_domain=TIME_PROCESSED`。
- `apply(source: TimeDomainScan, *, history, executed_utc/clock)`（签名细节按 030/031 冻结协议对齐）：
  1. 入场守卫：history 末域 ∈ {TIME_BASE, TIME_PROCESSED} 且与 `source.kind` 一致（core `_validate_history_kind` 兜底）；数据非有限 ⇒ 拒绝；`_reject_duplicate_stage("dewow")`；history 含 `flat_reflection_filter` ⇒ 拒绝（固定顺序 dewow→flat）。
  2. dt_s 从 `source.time_axis_s` 推导（≥2 点、严格递增、等间隔 1e-6 容差），窗口秒→样本按 §3.3-2 舍入链，双端拒绝给明确错误信息。
  3. 数值：沿最后 time 轴 reflect-pad 中心滑动均值（cumsum O(N)、complex128），输出 = data − local_mean；shape `(trace, channel, time)` 不变、axis/channels/metadata/schema_version 完整保留；输入零修改（readonly view + 新数组）。
  4. 输出 `TimeDomainScan(kind=TIME_PROCESSED)` + `TimeDomainStageResult`，history 经 `_record_for` 追加一条含完整可复现参数的记录。
- **排除**：不实现 Flat Reflection（035）、连续背景、实时 UI、参数对话框、显示裁剪改动；不 import 参考项目；不修改 core/storage/UI 任何字节。

**测试矩阵**（失败测试优先，`tests/contract/test_processing_dewow.py`）：
1. **常量场**：全常数道 ⇒ 输出 ≈ 0（减自身均值）；多通道各常数互不串扰。
2. **脉冲**：单位脉冲道 ⇒ 输出 = δ − (1/W)·δ_pad（边界处均值减半效应钉死 reflect 语义）。
3. **复数等价**：`dewow(a+bj) == dewow(a)+dewow(b)j` 逐位断言（real/imag 独立处理等价）。
4. **短数组**：n_time=1/2（dt 不可推/窗口>n_time）拒绝；窗口==样本1 拒绝；超大 window ⇒ >n_time 拒绝且消息含操作指引。
5. **多通道/多 trace**：channel 序、trace 广播独立；scan 与等价逐道结果一致。
6. **窗口边界**：偶数 window_ns 转奇、round 半值行为、等间隔破坏/非单调/含 NaN 轴拒绝、boundary reflect 手算小例黄金字面量。
7. **history 语义**：重复 dewow 拒绝（bump version 不绕过）；flat-after-dewow 合法预留（仅验证 dewow 不阻挡，flat 本身归 035 不测）；history 已含 flat 时 dewow 拒绝；record to_dict/from_dict JSON-safe 往返；provenance continuity（下链接 bandpass 之外的链不受破坏）。
8. **输入不变性**：source.data bytes 前后一致 + read-only；输出为新对象（not same id）。
9. **黄金样本**：三方交叉（031 先例）——① 冻结参考源 SHA-256（`eb6690e7…30e2c` + `e0c201b5…333c81`）登记进测试注释；② 参考 `_centered_moving_mean` 算法在本 venv 独立重算的小数组内嵌字面量；③ 同公式独立转写整缓冲 bit-exact 比对；生成脚本置于工作区外（C:/Users/Public 姿势，仓库零污染）。
10. **性能 smoke**：如 512 trace × 2 ch × 1024 time 随机 complex128，单次 apply 墙钟阈值宽松（< 数秒级），证明 O(N) 累积和路径无逐窗 Python 循环。

**门禁**：目标测试 → 相关定向回归（bandpass/time_domain/core.time_domain）→ verify.py 全量（基线 1291 passed + 新增用例数，mypy 53→54 源文件为预期变化）→ ruff + mypy + import；diff 检查 changedPaths 与 t2 契约 inScope 逐一相等。

## 6. 结论

两个直接依赖均有合并提交与 tracked 交付物实证：ISSUE-030（`89fd9bb`，`ProcessingStage`/`StageResult`/`_record_for` 契约就绪）、ISSUE-031（`9afdfb2`，`TimeDomainScan` 不可变模型 + `_validate_history_kind` + `TimeDomainStageResult` + IFFT 产出 `time_base` 生产端）。core `_ALLOWED_TRANSITIONS` 的 `TIME_BASE/TIME_PROCESSED → TIME_PROCESSED` 两条 hop、重复 stage 拒绝、provenance continuity 全部现成；参考源 `dewow.py`（`eb6690e7…`）与 `_time_stage_common.py`（`e0c201b5…`）在本地只读副本实测哈希与冻结 manifest 逐一相等、算法/守卫/舍入链全文可读；仓库内无 dewow 先行实现，ISSUE-034 为下一可执行 Issue。033 合并链推送已实测完成（origin/main = HEAD = `124fe56`）。**门禁基线 1291 passed / 4 deselected、ruff clean、mypy 53 files clean、package import ok 全部实测复现（exit 0），核查前后工作树干净、无遗留产物。ISSUE-034 可以开工（Ready）**，t2 按第 3 节契约与 t2 任务锁定的 inScope 路径执行。
