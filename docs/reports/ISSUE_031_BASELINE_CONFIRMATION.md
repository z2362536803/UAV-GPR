# ISSUE-031 开工基线确认单（2026-09-05）

## 1. 目标 Issue 与依据

- **Issue**：ISSUE-031「IFFT、物理时间轴与显示时窗」（`docs/issues/M06_CALIBRATION_PROCESSING.md` L153-188）
- **状态行核查**：M06 L155 ISSUE-031 状态 = `Planned`；映射 FR-004、012、016；直接依赖 ISSUE-030。
- **工作区根替换声明**：Issue 提示词中 `E:\wurenjiruanjian` 一律替换为当前工作区根 `D:\博士任务\无人机软件\UAV-GPR`。
- **流程依据**：`docs/issues/README.md`（通用执行协议、一次一个 Issue）、`docs/ISSUE_REVIEW_STANDARD.md` v1.0（t1 基线 → t2 实现 → t3 独立复审）。

## 2. 只读核查证据（全部实测）

| 项 | 结果 |
|---|---|
| HEAD / 分支 | `main @ 2c0abdc`（`docs(issues): mark ISSUE-030 Done after automated merge`）；`git rev-list --left-right --count origin/main...HEAD` = `0 3`，本地 main ahead of origin/main by 3 commits（030 合并链推送在网络重试中，属计划内情况，以 captain 后台 push 为准，不在本单断言远端状态） |
| 历史链 | `git log --oneline`：2c0abdc → 89fd9bb（Merge feat/issue-030）→ 41f5503（feat 030）→ b4d4b0f（mark 029 Done）；无 reset/rebase/amend 痕迹；工作树干净（`git status --porcelain` 除本单外无输出），`git diff --check` clean |
| ISSUE-030 Done 证据 | M06 L118 状态行 = Done（2026-09-05 自动化轮 t3 复审 VERDICT=PASS 后自动合并，见 `docs/reports/ISSUE_030_REVIEW_REPORT.md`）；合并提交 `89fd9bb` 在 HEAD 历史中；tracked 交付物齐全：`git ls-files src/uav_gpr/processing/` → `__init__.py` + `bandpass.py`（460 行，含 `ProcessingStage` Protocol、`BandpassStage`、`build_bandpass_window`、sin² 四频点窗）；`tests/contract/test_processing_bandpass.py` collect = **32 tests** ✓ |
| 依赖定向 | `.venv/Scripts/python.exe -m pytest tests/contract/test_processing_bandpass.py tests/unit/test_core_time_domain.py tests/unit/test_core_frequency.py -q` → **101 passed in 0.13s**（exit 0） |
| 可执行性 | `grep -iE "ifft" src/uav_gpr/processing/` 无任何 stage 实现；`processing/` 包仅 bandpass；`core/enums.py` 已定义 `TIME_BASE`/`TIME_PROCESSED` 域与 `TimeDomainKind`；`core/time_domain.py` 提供不可变 `TimeDomainScan`（trace × channel × time、`time_axis_s` 秒轴严格递增、history kind 匹配 fail-closed、`with_history` 返回新对象）→ **ISSUE-031 为下一个可执行 Issue**，无重复实现风险 |
| 参考源可达性 | `E:\钢筋仪软件开发` 本机不可达，沿用 027-030 先例使用本地只读副本 `D:\博士任务\rebar-inspector`（WSL 路径 `/mnt/d/博士任务/rebar-inspector`，只读使用，不修改） |

### 参考源哈希实测（与 manifest.md 冻结值逐一相等）

| 用途 | 参考文件 | 实测 SHA-256 | manifest 冻结值 |
|---|---|---|---|
| IFFT 数学 | `src/rebar_inspector/processing/ifft.py`（9388 B） | `9496288e9e918f788b88f41945ea5e43889cfb3c298cccf7543a33b5a41d297a` | 相同 ✓ |
| 时域 stage 共性 | `src/rebar_inspector/processing/_time_stage_common.py`（9224 B） | `e0c201b55acbaece0edb1546bbb8a00492874bb79fb9caf789d5ba416d333c81` | 相同 ✓ |
| stage 骨架 | `src/rebar_inspector/processing/_stage_common.py` | `5ee8f31c709a9873e9e62579f0f4b9a75049e2c201ca14d0f4c2b37721a197a4` | 相同 ✓ |
| 带通前置（可选链） | `src/rebar_inspector/processing/bandpass.py` | `3ee559e33e95c71702b04fe19eb9a24d2f676206d0b5471ec1e5038e17c38d51` | 相同 ✓ |

t2 迁移契约参考源 = `ifft.py`（DC→起频补零 + FFT length/插值 + time axis 数学）+ `_time_stage_common.py`（时域 stage 骨架），黄金样本从这两者的输入/输出对派生并在测试内显式记录源哈希。

关键本项目契约文件哈希（SHA-256 前 16 位，供 t3 交叉核对）：

```text
cfa8271f5ebd545c  src/uav_gpr/core/time_domain.py
386c8bdf21fdf2c3  src/uav_gpr/core/frequency.py
4e906f159b1c8599  src/uav_gpr/core/enums.py
f707839674ceb5e1  src/uav_gpr/processing/bandpass.py
9d730ec7a0f7c223  docs/PROCESSING.md
ade352882e9e54c6  docs/ACQUISITION.md
2bd2cd1a40e5f096  docs/DATA_MODEL.md
```

## 3. 契约要点（对 t2 实现有约束）

来源：M06 ISSUE-031 条目 + `docs/PROCESSING.md` §4 + `docs/ACQUISITION.md` §6 + `docs/DATA_MODEL.md` §8（L135-160）+ AGENTS.md §3/§8/§9/§10。

1. **PROCESSING.md §4（五条）**：按实际等间隔频率轴构建 DC→最高频对齐网格；起始频率以上实测数据放正确 bin、缺低频按明确策略补零；FFT 长度/插值倍数显式记录、补零只插值不宣称提高物理分辨率；输出完整物理时窗 `time_base`、显示裁剪不改存档基础结果；非等间隔轴/重复频点/容差外错位必须拒绝或走独立算法，不能悄悄套普通 IFFT。
2. **ACQUISITION.md §6**：`physical_unambiguous_window_s ≈ 1/Δf`；带宽影响时间分辨能力，补零只改善显示采样；物理时窗 vs 显示时窗是两个独立概念——显示时窗在物理时窗范围内裁剪，不修改 `frequency_raw`；本 Issue 无 UI，UI 双值展示要求归 M09。
3. **域转换链**（DATA_MODEL L152-153）：`frequency_* → time_base` 只由 IFFT 阶段产生；不允许 time 域返回 frequency 域；`TimeDomainScan(kind=time_base)` 必须携带完整非空 history 且末条输出域 = `time_base`（core 已 fail-closed 强制）。输入域允许集合按 §2 派生链定（`frequency_raw`/`frequency_calibrated`/`frequency_background_applied`/`frequency_filtered`），带通保持可选独立前置。
4. **复用 030 stage 契约**：`FrequencyToTimeStage` 实现 `processing/stage.py`… 实际 030 将 `ProcessingStage` Protocol、`StageResult`、`_record_for` 放在 `bandpass.py` 内——t2 沿用同一模式（稳定 `stage_name`/`stage_version`/规范参数 Hz 与秒单位/显式输入输出域/`ProcessingHistory.append` 追加、同 history 重复 stage 拒绝），不新建平行 history 类型。
5. **向量化**：输入 `trace × channel × frequency`（`FrequencyScan`）与 `channel × frequency`（单 sweep）都要支持，沿 frequency 轴 ifft 向量化，输出 `trace × channel × time`；complex dtype 保持；时间轴单位秒显式保存。
6. **raw 不变**：输入 bytes-backed 不可写；输出必须新对象；display crop 只生成只读 view/config，绝不截断/改写存档 `time_base` 数组。
7. **黄金样本对拍**：与参考 `ifft.py` 输出数值一致（容差与 dtype 在测试内显式记录，源哈希写入测试注释）。
8. **排除项**（M06 ISSUE-031）：不宣称补零提高物理分辨率、不计算深度、不做 UI、不把 bandpass 内置进 IFFT stage。
9. **分层纪律**（AGENTS.md §9）：新代码落 `src/uav_gpr/processing/`，只依赖 core（numpy），不 import UI/storage/hardware。
10. **inScope 硬约束（团队计划）**：t2 改动路径精确为 4 个——预期为
    - `src/uav_gpr/processing/__init__.py`（导出）
    - `src/uav_gpr/processing/ifft.py`（FrequencyToTimeStage：轴校验/补零/FFT length/time axis/crop view）
    - `src/uav_gpr/processing/display.py`（或同类名——display crop 只读 view/config 独立文件）
    - `tests/contract/test_processing_ifft.py`（契约+黄金+拒绝路径+crop 边界测试）
    changedPaths 必须与该清单逐一相等；文件名如需调整必须在 t2 开始时声明并保持总数 4。
11. **测试纪律**（AGENTS.md §10）：失败测试优先；覆盖正常、错误（非均匀轴/重复频点/错 bin 超容差/错误域/重复 stage/crop 越界）、dtype/history/raw 不变、单/双通道、多 trace 向量化、1/Δf 物理周期、参考对拍；禁固定 sleep；完成后跑 verify 全量门禁。

## 4. 门禁基线（verify.py 复跑）

- 全量非硬件 pytest：**1169 passed / 4 deselected in 277.17s**（4 deselected = 1 硬件哨兵 + 3 opt-in LibreVNA 硬件测试），与团队计划声明的门禁数字 1169 及 030 审查报告口径（基线 1137+32）一致。
- `tools/quality/verify.py`（pytest → ruff → mypy → package import）**exit 0，`[quality] all gates passed`**；ruff `All checks passed!`；mypy `Success: no issues found in 50 source files`；import ok。
- 定向依赖：101 passed（见第 2 节）。
- 核查前后 `git status --porcelain` 行数一致（本单为唯一新增未跟踪文件）；无重定向产物遗留。

## 5. 实施计划摘要（供 t2）

**范围**：独立 `FrequencyToTimeStage`——均匀 frequency axis 校验（等间隔、单调、无重复、bin 对齐容差 fail-closed）、DC→起频补零（明确策略并记录于 parameters）、显式 FFT length/插值倍数（power-of-two 或显式指定，记录换算）、`np.fft.ifft` 向量化（trace×channel 批量）、完整 `time_base`（`TimeDomainScan(kind=TIME_BASE)` + 秒 `time_axis_s`，周期 ≈ 1/Δf）、history 经 `ProcessingHistory.append` 追加、独立 display crop 只读 view/config（不改存档数组，越界拒绝）。
**排除**：bandpass 内置、深度计算、分辨率提升宣称、UI、存储写盘。
**验收映射测试矩阵**：1/Δf 物理周期断言；直接 `np.fft.ifft` 对拍；参考 `ifft.py` 黄金样本 bit/容差对拍（源哈希登记）；单通道/双通道；多 trace 向量化 == 逐道循环；crop 边界（含端点、越界拒绝、view 只读）；raw 输入不变（id+内容前后一致）；非均匀/重复/错 bin/超容差拒绝；错误输入域与重复 stage 拒绝；dtype complex 与 shape `trace×channel×time` 断言；history 参数（Hz/秒/FFT length）序列化可还原。

## 6. 结论

直接依赖 ISSUE-030 有实测 Done 证据（合并提交 + tracked `bandpass.py` 460 行 + 32 collected 测试 + 复审 PASS 报告）；核心模型 `TimeDomainScan`/`DataDomain.TIME_BASE` 就绪且 fail-closed；参考源 `ifft.py`/`_time_stage_common.py` 哈希与冻结 manifest 一致、本地只读副本可达；`processing` 包无 IFFT 先行实现；门禁基线 1169 passed 与计划口径一致。**ISSUE-031 可以开工（Ready）**，t2 按第 3 节契约与 inScope 4 路径执行。
