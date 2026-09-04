# ISSUE-030 开工基线确认单（2026-09-05）

## 1. 目标 Issue 与依据

- **Issue**：ISSUE-030「ProcessingStage 框架与频域带通」（`docs/issues/M06_CALIBRATION_PROCESSING.md` L116-151）
- **状态行核查**：M06 L118 ISSUE-030 状态 = `Planned`；映射 FR-012；直接依赖 ISSUE-001、004、007。
- **工作区根替换声明**：Issue 提示词中 `E:\wurenjiruanjian` 一律替换为当前工作区根 `D:\博士任务\无人机软件\UAV-GPR`。
- **流程依据**：`docs/issues/README.md`（通用执行协议 §3、一次一个 Issue）、`docs/ISSUE_REVIEW_STANDARD.md` v1.0（t1 基线 → t2 实现 → t3 独立复审）。

## 2. 只读核查证据（全部实测）

| 项 | 结果 |
|---|---|
| HEAD / 分支 | `main @ b4d4b0f`（`docs(issues): mark ISSUE-029 Done after automated merge (F1 closed)`）；本地 main ahead of origin/main by 3 commits（029 合并链推送在网络重试中，属计划内情况）。注：本环境 `git rev-parse origin/main` 实测亦指向 `b4d4b0f`，但远端真实同步状态以 captain 的后台 push 为准，不在本单断言 |
| 历史链 | `git log --oneline`：b4d4b0f → fb758fe（Merge feat/issue-029）→ dd5f98a（feat 029）→ 5147a15（mark 028 Done）；无 reset/rebase/amend 痕迹；工作树干净（`git status --porcelain` 除本单外无输出），`git diff --check` clean |
| ISSUE-001 证据 | M01 L7 状态行为初始值 `Planned`（M01 各条目状态行未随实际进度更新）；实际完成证据：`docs/reports/ISSUE_001_005_REVIEW_SUMMARY.md` 表格 ISSUE-001 = `PASS`，且已合入 main（该报告 §结论：ISSUE-001～005 已合入 `main`）；交付物 `docs/reference-baselines/manifest.json` + `manifest.md` tracked 存在，冻结钢筋仪候选源 SHA-256 白名单 → **实质 Done** |
| ISSUE-004 证据 | 同上报告 ISSUE-004 = `PASS` 已合入；`src/uav_gpr/core/frequency.py`（487 行）提供不可变 `FrequencySweep`（channel×frequency complex）与 `FrequencyScan`（trace×channel×frequency），数组 bytes-backed 永不可写、fail-closed shape/dtype 校验齐全 → **Done** |
| ISSUE-007 证据 | `src/uav_gpr/core/time_domain.py`（721 行）提供 `ProcessingRecord`（stage_name/version、JSON-safe canonical parameters、input/output `DataDomain`、references 域匹配 fail-closed）与 `ProcessingHistory`（append 返回新对象、域链校验、首项必须 `frequency_raw`、同 history 内 stage_name 唯一、version 变更不绕过）→ **Done**；对应 `tests/unit/test_core_time_domain.py` 含 bandpass 迁移合法性契约测试（L121/L331/L409/L424-430） |
| 依赖定向 | `.venv/Scripts/python.exe -m pytest tests/unit/test_core_time_domain.py tests/unit/test_core_frequency.py tests/contract/test_calibration_osl.py tests/contract/test_calibration_reference.py tests/contract/test_rcscan_reader.py tests/unit/test_core_identifiers.py -q` → **182 passed in 12.10s**（exit 0） |
| 可执行性 | `src/uav_gpr/processing/` 仅有 docstring 空壳 `__init__.py`（"Independent, provenance-preserving processing stages."），无任何 stage/bandpass 实现；`grep -rn bandpass src tests` 仅命中 core/enums 注释与 time_domain 契约测试 → **ISSUE-030 为下一个可执行 Issue**，无重复实现风险 |
| 参考源可达性 | `E:\钢筋仪软件开发` 本机不可达，沿用 027/028/029 先例使用本地只读副本 `D:\博士任务\rebar-inspector`（Test-Path = True，只读使用，不修改） |

### 参考源哈希实测（与 manifest.md 冻结值逐一相等）

| 用途 | 参考文件 | 实测 SHA-256 | manifest 冻结值 |
|---|---|---|---|
| 带通数学 | `src/rebar_inspector/processing/bandpass.py`（6305 B） | `3ee559e33e95c71702b04fe19eb9a24d2f676206d0b5471ec1e5038e17c38d51` | 相同 ✓ |
| stage 共性 | `src/rebar_inspector/processing/_stage_common.py` | `5ee8f31c709a9873e9e62579f0f4b9a75049e2c201ca14d0f4c2b37721a197a4` | 相同 ✓ |
| 频域模型 | `src/rebar_inspector/core/frequency.py` | `8164a64167bb223fd74523aecb6d73715cf0d36379d9a1ae4cee259c4c35badb` | 相同 ✓ |
| history 模型 | `src/rebar_inspector/core/history.py` | `077c8b2940cdfc7cf22f8c6c86ac7dc77596e3c10ca2af3afbdd00bbff23ba69` | 相同 ✓ |

t2 迁移契约参考源 = `bandpass.py`（sin² 四频点窗数学）+ `_stage_common.py`（stage 骨架），黄金样本从这两者的输入/输出对派生并在测试内显式记录源哈希。

关键本项目契约文件哈希（SHA-256 前 16 位，供 t3 交叉核对）：

```text
cfa8271f5ebd545c  src/uav_gpr/core/time_domain.py
386c8bdf21fdf2c3  src/uav_gpr/core/frequency.py
4e906f159b1c8599  src/uav_gpr/core/enums.py
9d730ec7a0f7c223  docs/PROCESSING.md
2bd2cd1a40e5f096  docs/DATA_MODEL.md
371fcba24fd13c55  docs/REFERENCE_MIGRATION.md
```

## 3. 契约要点（对 t2 实现有约束）

来源：M06 ISSUE-030 条目 + `docs/PROCESSING.md` §1/§2/§3 + `docs/DATA_MODEL.md` §8（L136-172）+ AGENTS.md §3/§9/§10。

1. **ProcessingStage 契约**：独立 stage 接口——稳定 `stage_name`、`stage_version`、规范参数（Hz 单位）、输入/输出域显式声明；复用 ISSUE-007 的 `ProcessingRecord`/`ProcessingHistory`（append 返回新对象、不可变），不得新建平行 history 类型。
2. **域检查 fail-closed**：带通输入必须是 `frequency_raw`/`frequency_calibrated`/`frequency_background_applied` 中按 §2 频域派生链合法的域；输出 `frequency_filtered` **只由带通阶段产生**（DATA_MODEL L152）；禁止 raw→raw 恒等输出；错误域输入必须拒绝。
3. **sin² 四频点窗**（PROCESSING.md §3 L43）：参数单位为 Hz、四个频点有序、必须与采集频段相交（不相交拒绝）；沿 frequency 轴向量化，保持 complex dtype 与 multi-channel（单/双通道共用同一实现，shape `channel × frequency` 或 `trace × channel × frequency`）；参数非法（乱序/重复退化/频段不相交）拒绝。
4. **重复 stage 拒绝**：同一 history 内 `stage_name` 只能应用一次，改 `stage_version` 不能绕过——该规则已由 `ProcessingHistory.append` 强制，t2 只需走正规路径并测出拒绝。
5. **raw 不变**：输入 `FrequencyScan`/数组 bytes-backed 不可写，输出必须新对象；测试须断言输入对象 id/内容前后一致。
6. **history 精确记录**：输出对象的 history 末条记录 stage_name/version/参数（Hz 值原样可序列化）/输入输出域；黄金输入/输出样本对拍（与参考 `bandpass.py` 数值一致容差内，容差与 dtype 明确记录）。
7. **排除项**（M06 ISSUE-030）：不实现 IFFT、OSL、背景减法、UI pipeline；不把带通塞进其他函数；不实现显示时窗裁剪。
8. **分层纪律**（AGENTS.md §9）：新代码落 `src/uav_gpr/processing/`，只依赖 core（numpy），不 import UI/storage/hardware。
9. **inScope 硬约束（团队计划）**：t2 改动路径精确为 4 个——预期为
   - `src/uav_gpr/processing/__init__.py`（导出）
   - `src/uav_gpr/processing/stage.py`（ProcessingStage 协议/域检查/规范参数/history 追加）
   - `src/uav_gpr/processing/bandpass.py`（sin² 四频点带通）
   - `tests/contract/test_processing_bandpass.py`（契约+黄金+拒绝路径测试）
   changedPaths 必须与该清单逐一相等；文件名如需调整必须在 t2 开始时声明并保持总数 4。
10. **测试纪律**（AGENTS.md §10）：失败测试优先；覆盖正常、错误（非法四频点/乱序/不相交/错误域/重复 stage）、dtype/history/raw 不变、单/双通道、频率边界、参考对拍；禁固定 sleep；完成后跑 verify 全量门禁。

## 4. 门禁基线（verify.py 复跑）

- 全量非硬件 pytest：**1137 passed / 4 deselected in 269.70s**（4 deselected = 1 硬件哨兵 + 3 opt-in LibreVNA 硬件测试），与团队计划声明的门禁数字 1137 一致。
- `tools/quality/verify.py`（pytest → ruff → mypy → package import）**exit 0，`[quality] all gates passed`**；ruff `All checks passed!`；mypy `Success: no issues found in 49 source files`；import ok。
- 定向依赖：182 passed（见第 2 节）。
- 核查前后 `git status` 一致（本单为唯一新增未跟踪文件）。

## 5. 结论

三个直接依赖（ISSUE-001/004/007）均有实测代码+测试+审查报告证据；参考源 `bandpass.py`/`_stage_common.py` 哈希与冻结 manifest 一致且只读副本可达；`processing` 包为空壳，无先行实现；门禁基线 1137 passed 与计划口径一致。**ISSUE-030 可以开工（Ready）**，t2 按第 3 节契约与第 2 节 inScope 4 路径执行。
