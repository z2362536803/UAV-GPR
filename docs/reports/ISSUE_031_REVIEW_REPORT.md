# ISSUE-031 独立复审报告（t3）

日期：2026-09-05
审查者：AgentTeams `uav-gpr-issue-031-ifft` 成员 reviewer（只读）
标准：`docs/ISSUE_REVIEW_STANDARD.md` v1.0（§13 固定格式）
被审对象：t2 交付的 ISSUE-031「IFFT、物理时间轴与显示时窗」（`docs/issues/M06_CALIBRATION_PROCESSING.md` L153–188）
审查性质：独立只读复审——除本报告外未修改任何项目文件、未 commit/push/merge；全部探针在系统临时目录（`D:\dsh\windows\test-0.1.2-rc.1\temp\probe_031_review.py`）运行并已于审查结束时删除；审查前后 `git status --porcelain` 一致（t2 遗留 4 项 inScope 交付物 + t1 基线报告 + 本报告）。

---

## 1. 审查结论

**PASS**（可合并）。

- M06 L174–178 三条验收标准全部 **PASS**，均有代码行号 + 独立复现测试证据。
- 独立复跑门禁全绿：定向 **46 passed**；全量 `tools/quality/verify.py` **exit 0**（pytest 1215 passed / 4 deselected + ruff + mypy(51 files) + import），解释器 `.venv/Scripts/python.exe`（Python 3.13.14）。
- **最强形式黄金对拍**：将冻结参考实现 `rebar_inspector/processing/ifft.py` 的 `IfftStage` 在本机 venv 直接导入运行（`max_time_s=None` 即参考的完整时窗行为），与 `FrequencyToTimeStage` 端到端并排对拍——5 组轴×通道×频谱形态（黄金轴/精确 bin 对齐/sub-bin 半 bin/df=25 MHz 40 点、单/双通道）+ 3-trace×2-channel 批处理，**13 组数组全部 bit-exact（0 失配）**，时间轴同样逐位相等。
- 物理语义独立验证：`T = N·dt = 1/df` 恒等式在 3 种轴上精确成立；平坦谱输出的载频经解调实数性判据落在**真实频带中心**（realness@fc ≈ 1.3e-15，机器精度），而网格中心假设被拒绝（realness = 0.33）——相位斜坡 `exp(2jπ·offset·t)` 确实恢复了真实起始频偏，非逐字照抄无验证。
- 无 P0/P1/P2 问题；仅 2 项 P3 级观察（见第 3 节），均不阻止合并。
- 依自动化轮约定：PASS 后 captain 可执行合并（feat/issue-031 → --no-ff → M06 L155 Done → push → 建 032）。

## 2. 自动识别的审查范围

从 t2 完成报告（任务 output）、t1 基线单、t2 计划与仓库事实提取：

- **Issue**：ISSUE-031「IFFT、物理时间轴与显示时窗」，M06 L153–188，映射 FR-004/012/016，直接依赖 ISSUE-030；状态行 L155 = `Review`（实测一致，diff 仅 1 行状态变更）。
- **分支/基线**：`main @ 2c0abdc`（与 t1 基线一致）；工作树未提交交付（符合提示词「报告并停止，不 commit/push」）；`origin/main...HEAD` = `0 0`（030 推送已完成，基线单中「本地领先 3 commits」的网络重试已收敛）；reflog 自 030 合并后无 reset/rebase/amend。
- **交付物（inScope 4 路径，逐一实测存在，changedPaths 与 inScope 逐一相等）**：
  1. `src/uav_gpr/processing/time_domain.py`（新增，720 行：`validate_uniform_axis`/`AxisSpan` + `compute_ifft_grid` + `FrequencyToTimeStage` + `TimeDomainStageResult` + `DisplayCropConfig` + `DisplayTimeWindowView`）
  2. `tests/contract/test_processing_time_domain.py`（新增，880 行，46 个测试）
  3. `docs/plans/2026-09-05-issue-031-ifft.md`（新增，含 D1–D9、迁移清单 M1–M6、执行日志）
  4. `docs/issues/M06_CALIBRATION_PROCESSING.md`（仅 L155 状态行 `Planned → Review`，实测 diff 仅 1 行）
  工作树第 5 项 `docs/reports/ISSUE_031_BASELINE_CONFIRMATION.md` 为 t1 交付物（t1 任务契约「落盘报告」），非 t2 范围溢出。
- **参考源**（本地只读副本 `D:\博士任务\rebar-inspector`）：实测 SHA-256 `ifft.py` = `9496288e…d297a`、`_time_stage_common.py` = `e0c201b5…3c81`、`_stage_common.py` = `5ee8f31c…97a4`、`bandpass.py` = `3ee559e3…8d51`——**4/4 与 `docs/reference-baselines/manifest.md` 冻结值相等**。
- **t1 基线单项目契约哈希**（证明 t2 只读消费、零改动 core/契约文档）：`core/time_domain.py` cfa8271f…、`core/frequency.py` 386c8bdf…、`core/enums.py` 4e906f15…、`processing/bandpass.py` f7078396…、`docs/PROCESSING.md` 9d730ec7…、`docs/ACQUISITION.md` ade35288…、`docs/DATA_MODEL.md` 2bd2cd1a…——**7/7 与基线单记录的前 16 位一致**。
- **范围边界核对**：`src/uav_gpr/processing/` 仅 `__init__.py`（未改，沿用 030 占位先例）+ `bandpass.py`（未改）+ 新增 `time_domain.py`；模块导入仅 stdlib+numpy+`uav_gpr.core`+`uav_gpr.processing.bandpass`（协议与 `_record_for` 复用），无 UI/storage/hardware/fft 库依赖；源码排除项守卫实测：`depth/velocity/distance/speed_of_light` 仅出现在否定性 docstring 与 `depth_calculation: False` 键；`sin(` 除 `np.sinh` 替换外无匹配（无内置带通窗）。

## 3. 主要问题（P0 → P3）

无 P0 / P1 / P2。P3 共 2 项：

| # | 等级 | 标题 | 位置 | 触发条件 | 实际影响 | 违反要求 | 最小修复方向 |
|---|---|---|---|---|---|---|---|
| P3-1 | P3 | sweep→scan 转换丢弃已附加的单道 TraceMetadata | `src/uav_gpr/processing/time_domain.py` L694–699（`FrequencySweep` 分支固定 `metadata = ()`） | 输入 sweep 经 `with_metadata` 附加 TraceMetadata 后进 IFFT（审查者构造探针实测：`sweep.metadata` 非 None → 输出单 trace scan 的 `metadata` 为 `()`，而自然映射应为 `(sweep.metadata,)`） | 单道频扫转时域后丢失逐道标识绑定（trace_uid/时间戳/质量位不在结果对象上）；主链路 scan 输入不受影响（metadata 逐道透传，测试 L722–728 覆盖）；DATA_MODEL L137「保留输入道元数据」的单道语义未完全兑现；计划 D2 已声明 `metadata=()` 但只论证了未附加情形 | `docs/DATA_MODEL.md` TimeDomainScan「保留输入道元数据和位置关联」 | sweep 分支改为 `metadata = (source.metadata,) if source.metadata is not None else ()`，补一条带 metadata 的回归测试 |
| P3-2 | P3 | `validate_uniform_axis` 公有命名但未导出、未被直接测试 | `src/uav_gpr/processing/time_domain.py` L72–83（`__all__`）、L161（公有函数名） | 以公有 API 身份引用（计划 D3、模块 docstring）但 `__all__` 不含它，测试仅经 `compute_ifft_grid` 间接覆盖 | API 面与文档口径不一致；无功能影响（间接覆盖完整） | 无（ISSUE_REVIEW_STANDARD §8.5 可维护性精度项） | 加入 `__all__` 并补 1 条直接契约测试，或改私有命名；可与 P3-1 同批处理 |

> 两项 P3 均为低风险、影响面有限、不阻止本次合并（标准 §12：P0/P1/关键验收失败才阻止 PASS）。

## 4. 逐 Issue 验收矩阵（M06 L174–178）

| 验收标准 | 状态 | 代码证据 | 实际测试证据 | 问题/限制 |
|---|---|---|---|---|
| L176：`time period≈1/Δf`、axis/shape 与直接参考对拍 | **PASS** | 网格数学逐式迁移：均匀轴容差 `max(1 Hz, |df|·1e-6)`（`time_domain.py` L209–221，与参考 `ifft.py` L84–90 逐字同规则）；`first_bin = max(0, round(f_start/df))` + DC→起频补零（L261–264 ↔ 参考 L93–96）；sub-bin 相位斜坡 `exp(2jπ·offset·t)`（L662–665 ↔ 参考 L193–195）；FFT 长度 `next_pow2(padded)·oversampling` 或显式 2 的幂（L264–282 ↔ 参考 L97，显式模式为本项目收紧适配）；时间轴 `arange(N)/(N·df)` 秒（L282 ↔ 参考 L98）；向量化 `np.fft.ifft(axis=-1)`（L658–661 ↔ 参考 L190–192）；输出完整物理时窗（参考的 `max_time_s` 截断**未迁移**，计划 M2 声明：存档必须完整 T） | ①测试黄金：字面量头 8 样点 + 独立转写整缓冲 bit-exact + 精确 bin 情形与直接 `np.fft.ifft` 逐位相等（测试 L407–456，复跑 46 passed）；②**审查者冻结参考实现端到端对拍**（探针 P1/P1b）：导入参考 `IfftStage(max_time_s=None)`，5 组轴×形态 + 3-trace 批处理，13 组数组/轴 **bit-exact 0 失配**；③物理恒等式（探针 P9）：3 种轴 `N·dt == 1/df`（相对误差 ≤1e-12）、末样本 `T−dt`；④载频判据（探针 P2）：平坦谱解调 realness@真实带中心 ≈1.3e-15、@网格中心 0.33（网格≠真实）；错号斜坡负对照被拒绝；延迟脉冲峰值落在 τ0；⑤变异判别（探针 P10）：错号斜坡/缺斜坡/错 FFT 长度均与 stage 输出不等（测试黄金能抓变异） | 无 |
| L177：display crop 不修改/截断存档 time_base | **PASS** | `DisplayCropConfig` 纯值对象（L305–358，构造不触碰数据）；`DisplayTimeWindowView.for_scan` 只读解析（L421–449：searchsorted、越界 OUT_OF_RANGE 不钳制、空窗 INVALID_ARGUMENT）；视图属性返回**存档缓冲本身的只读切片**（L451–459）；kind 门（L384–389 拒 time_processed）；构造视图不改 history/字节 | ①测试 L742–799：指针算术证明别名零拷贝、快照前后全等、存档 64 点完整、全窗/零宽/空窗/越界边界；②探针 P6 复证：axis/data 指针偏移算术命中、经视图写抛 ValueError、`arr_sha256` 前后不变、history `is` 同一对象；③探针 P5：raw 输入 sha256 前后一致、输出/输入 writeable=False | 无 |
| L178：带通可选且仍是独立前置 stage | **PASS** | 输入域集合 = `{raw, calibrated, background_applied, filtered}`（L103–110，含带通后继 `frequency_filtered` = 链上可选跳过带通合法）；模块无窗计算代码（排除守卫测试 L864–879 + 审查者 grep 复证）；两 stage 经各自 history record 独立追加 | ①测试 L556–574：bandpass→ifft 两记录两域跳，数值 == 手工窗×谱再补零 IFFT；②探针 P7 复证：链式 == 独立手算窗+IFFT bit-exact、无带通直进路径合法且结果不同（带通确实改变数据）；③域门：time_base/time_processed 前驱 → PROCESSING_DOMAIN_MISMATCH（测试 L479–500 + 探针 P8）；④重复 stage：真实路径域门拒 + core 唯一性隔离探针（测试 L503–553） | 无 |

附带契约项（M06 L165–168 范围行 + PROCESSING §4 五条）：轴对齐/DC→起频补零/FFT 长度与插值显式记录（parameters 键 `zero_padding_policy`/`oversampling_factor`/`fft_size_mode`/`explicit_fft_size`/`interpolation_only_no_physical_resolution_gain: true`，L572–591）、秒时间轴（`time_axis_unit: "s"` + `time_axis_s`）、非等间隔/重复/错 bin fail-closed（L161–230 + 测试拒绝矩阵 L346–401 + 探针 P3 容差边界 99.5 Hz 收/100.5 Hz 拒）、stage history 追加（`ProcessingHistory.append`，L692）、多通道/多道向量化（L658–661 广播置入 + 测试 L633–701 + 探针 P4 逐元素 bit-exact）——均 PASS。

排除项（M06 L172）：不宣称补零提分辨率（显式否定键 + 守卫测试）、不算深度（无 depth/velocity 字段，grep 仅否定语句）、不做 UI（无 Qt 导入）、不内置 bandpass（无窗代码）——均 PASS。

## 5. Git 与交付检查

- **分支/基线**：`main @ 2c0abdc`，审查基线与 t1 基线单一致；工作树承载未提交交付（4 inScope + t1 报告），无额外未跟踪/修改文件（`git status --porcelain` 实测 5 项 + 本报告）。
- **本批提交**：无新提交（t2 按提示词不 commit/push）；HEAD 历史仅 029/030 合并链，无多 Issue 混入；reflog 无 reset/rebase/amend/强推痕迹。
- **范围外修改**：无——`git diff` 仅 M06 L155 一行；`processing/__init__.py`、`core/**`、`docs/PROCESSING.md`、`docs/ACQUISITION.md`、`tools/**` 均未动（7 项契约哈希与 t1 记录一致即证明）。
- **不该提交的内容**：无缓存/日志/构建物/密钥/实测数据/参考仓库文件；`git diff --check` 干净。
- **公共契约变更**：新增独立 stage（不改公共 schema/协议/数据格式），参数经 JSON-safe 序列化并有往返测试，无需 ADR（不改变强制数据规则/空地职责/持久化语义）。
- **依赖顺序**：ISSUE-030 Done 证据链完整（合并提交 89fd9bb + tracked bandpass.py + 32 测试 + 复审 PASS）；ISSUE-031 消费其协议与 `_record_for`，接口兼容性经 46 测试 + 双实现并排运行证实（其中 bandpass 链路测试 L556–574 同时验证两个 stage 的互操作）。
- **交付流程备注**：t2 结果由 captain 以 attempt 2 代登记（engineer 3 次登记因长 payload 序列化失败，工作本身已完成且全部可复现）——流程事实，非交付缺陷。

## 6. 测试与验证结果

执行者声称（t2 计划 §8）：定向 46 passed；verify.py exit 0 = 1215 passed/4 deselected + ruff + mypy(51) + import；changedPaths == inScope。**全部独立复现**：

| 命令 | 解释器 | 退出码 | 结果 |
|---|---|---|---|
| `.venv/Scripts/python.exe -m pytest tests/contract/test_processing_time_domain.py -q` | Python 3.13.14 | 0 | **46 passed** in 0.07s |
| `.venv/Scripts/python.exe tools/quality/verify.py` | Python 3.13.14 | 0 | pytest ok + `All checks passed!`（ruff）+ `Success: no issues found in 51 source files`（mypy）+ `package import ok` + `[quality] all gates passed` |
| `.venv/Scripts/python.exe -m pytest -q`（全量非硬件，独立计次） | Python 3.13.14 | 0 | **1215 passed, 4 skipped in 270.39s**（4 = 硬件哨兵/opt-in，与 verify.py 口径的「4 deselected」为同一组硬件测试的收集方式差异） |
| `.venv/Scripts/python.exe <temp>/probe_031_review.py`（审查者探针，已删除） | Python 3.13.14 | 0 | **59 checks, 0 failures**：P1 冻结参考端到端 13 组 bit-exact、P2 载频/脉冲物理判据 + 负对照、P3 容差边界 4 例、P4 向量化逐元素 bit-exact、P5 raw sha256 不变 + 写拒、P6 crop 别名/不变性/边界、P7 bandpass 独立性、P8 域门、P9 周期恒等式、P10 变异判别、P11 离谱轴 fail-closed（MemoryError） |
| `git diff --check` | — | 0 | 干净 |

数字口径核对：基线 1169 + 新增 46 = 1215 ✓；mypy 50 → 51 files（+1 新模块）✓；ruff/import 绿 ✓。无测试被删除/降级/吞异常（工作树 diff 仅新增与状态行）。

## 7. 报告与事实差异

| # | 声称 | 事实核对 | 差异 |
|---|---|---|---|
| 1 | t2 output：46 契约测试、verify 1215/4、ruff/mypy(51)/import、changedPaths==inScope 4 路径 | 全部逐项复现相等 | 无 |
| 2 | t1/t2：参考源 4 项哈希与 manifest 一致 | 实测 4/4 一致 | 无 |
| 3 | t2 计划 §8：黄金字面量生成脚本置于 `C:/Users/Public/`，仓库零污染 | 仓库侧可证零污染（git status 仅 5 项交付物）；仓库外路径无法独立验证 | **未发现反证**（按标准 §10 记为无法独立验证项） |
| 4 | t2 计划 D2：sweep 输出单 trace scan `metadata=()` | 属实；但未覆盖「sweep 已附加 metadata」情形（见 P3-1） | 声明不完整（P3） |
| 5 | t2 计划 §8：150 MHz 起点 round-half-even 得 bin 2/offset −5e7 | 实测一致（测试 L311–319 + 探针 P1 sub-bin 组 bit-exact） | 无 |
| 6 | t2 计划 §8：验证期口径修正 4 项（单扫容器/重复 stage 可达性/round 语义/rel_tol 关键字） | 与最终测试内容一致（姊妹 result 类型、隔离探针、IEEE round 断言、`pytest.approx` 正确关键字） | 无（过程声明，结果可证） |

## 8. 剩余风险

- **P3-1 metadata 丢弃**：单道 sweep 转换丢失逐道绑定（低风险，主链路 scan 输入不受影响）；合并后由后续小修或 ISSUE-034/035 消费 time_base 前顺手关闭。
- **`TimeDomainStageResult` 姊妹类型**（计划 D2 声明）：`ProcessingStage` 为 runtime_checkable 协议，`assert isinstance`（L718–720）与 mypy 均不静态校验 apply 返回类型签名一致性——姊妹类三字段同形且文档化，与 030 模式一致；若未来协议收紧为静态签名校验需同步收口（低风险）。
- **离谱高频轴的 fail-closed 形态为 MemoryError**（探针 P11：`f_start/df` 逼近分配极限时 `np.zeros(fft_size)` 抛 MemoryError 而非 DomainError）：行为是 fail-closed（不会静默产出错误数据），但错误类型不属于 DomainError 家族；真实 LibreVNA 频段（≤GHz、步进 ≥kHz）不会触发。可接受，留作后续加 bin 数上限的改进项。
- **硬件依赖**：本 Issue 纯算法层，无硬件路径；4 项 deselected 为既有硬件哨兵，与本交付无关。
- **030 遗留 P3**（4 项挂账）不在本次范围，未复核其状态。

## 9. 合并建议

**建议合并**（自动化轮约定）：

1. t2 交付满足 M06 L174–178 全部验收标准，无 P0/P1/P2，verdict = **pass**；
2. captain 执行既定合并流程：`feat/issue-031` 承载 4 路径交付（当前在工作树）→ `--no-ff` 合并 → M06 L155 `Review → Done`（附本报告链接）→ push origin main → 建团队 ISSUE-032；
3. P3-1/P3-2 挂账（同 030 先例：不阻止合并、记入最小修复清单，可并入后续触碰该模块的 Issue）。

## 10. 最小修复清单

| # | 等级 | 修复项 | 位置 | 修复方向 | 建议时机 |
|---|---|---|---|---|---|
| 1 | P3 | sweep→scan 丢弃已附加 TraceMetadata | `src/uav_gpr/processing/time_domain.py` L697–699 | sweep 分支 `metadata = (source.metadata,) if source.metadata is not None else ()`；补回归测试（带 metadata 的 sweep 进 stage，断言输出 scan metadata 1 元组且内容相等） | ISSUE-034/035 消费 time_base 前，或下次触碰该模块 |
| 2 | P3 | `validate_uniform_axis` 未导出 | `src/uav_gpr/processing/time_domain.py` L72–83 | 加入 `__all__` + 1 条直接契约测试，或改私有命名并同步计划文档 | 同上，可与 #1 同批 |

审查结束：立即停止，不修改代码，等待 captain 决定（自动化轮：PASS → 自动合并推送 → 进入 ISSUE-032）。
