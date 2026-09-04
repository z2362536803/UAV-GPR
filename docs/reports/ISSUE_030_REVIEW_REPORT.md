# ISSUE-030 独立复审报告（t3）

日期：2026-09-05
审查者：AgentTeams `uav-gpr-issue-030-bandpass` 成员 reviewer（只读）
标准：`docs/ISSUE_REVIEW_STANDARD.md` v1.0（§13 固定格式）
被审对象：t2 交付的 ISSUE-030「ProcessingStage 框架与频域带通」（`docs/issues/M06_CALIBRATION_PROCESSING.md` L116–151）
审查性质：独立只读复审——未修改任何项目文件、未 commit/push/merge；全部探针在系统临时目录运行并已清理；审查前后 `git status --porcelain` 一致（仅 t2 遗留的 5 项交付物 + 本报告）。

---

## 1. 审查结论

**PASS**（可合并）。

- M06 L137–141 三条验收标准全部 **PASS**，均有代码行号 + 独立复现测试证据。
- 独立复跑门禁全绿：定向 32 passed；全量 `verify.py` exit 0（1169 passed / 4 deselected + ruff + mypy(50 files) + import）。
- 窗数学与冻结参考源逐位对拍：24 组轴×边频组合输出 **bit-exact 全等**（含黄金 6 频点窗值、双通道 24 复数输出字面量独立重算全等）。
- 无 P0/P1/P2 问题；仅 4 项 P3 级观察/文档精度问题（见第 3 节），均不阻止合并。
- 依自动化轮约定：PASS 后 captain 可执行合并（feat/issue-030 → --no-ff → M06 L118 Done → push）。

## 2. 自动识别的审查范围

从 t2 完成报告、t1 基线单与仓库事实提取：

- **Issue**：ISSUE-030「ProcessingStage 框架与频域带通」，M06 L116–151，映射 FR-012，状态行 L118 = `Review`（实测一致）。
- **分支/基线**：`main @ b4d4b0f`（与 t1 基线一致）；工作树未提交交付（符合提示词「报告并停止，不 commit/push」）；reflog 自 029 合并后无 reset/rebase/amend。
- **交付物（inScope 4 路径，逐一实测存在）**：
  1. `src/uav_gpr/processing/bandpass.py`（460 行，实测 460 行）
  2. `tests/contract/test_processing_bandpass.py`（674 行，实测 674 行，32 个测试）
  3. `docs/plans/2026-09-05-issue-030-bandpass.md`（含 D1–D9、迁移清单 M1–M6、执行日志）
  4. `docs/issues/M06_CALIBRATION_PROCESSING.md`（仅 L118 状态行 `Planned → Review`，实测 diff 仅 1 行）
- **参考源**（本地只读副本 `D:\博士任务\rebar-inspector`）：`bandpass.py` SHA-256 `3ee559e33e95c71702b04fe19eb9a24d2f676206d0b5471ec1e5038e17c38d51`、`_stage_common.py` `5ee8f31c…a197a4`、`core/frequency.py` `8164a641…badb`、`core/history.py` `077c8b29…ba69`——实测全部与 `docs/reference-baselines/manifest.md` 冻结值相等（4/4）。
- **t1 基线单核心契约哈希**：`core/time_domain.py` cfa8271f…、`core/frequency.py` 386c8bdf…、`core/enums.py` 4e906f15…、`docs/PROCESSING.md` 9d730ec7…、`docs/DATA_MODEL.md` 2bd2cd1a…、`docs/REFERENCE_MIGRATION.md` 371fcba2…——实测 6/6 与基线单记录的前 16 位一致（证明 t2 只读消费 core 契约未改动）。
- **范围边界核对**：`src/uav_gpr/processing/` 仅 `__init__.py`（docstring 占位，未改）+ `bandpass.py`；无 IFFT/OSL/背景/UI 实现；`grep -rln bandpass src/` 仅命中 `core/enums.py`（docstring 提及 frequency_filtered 语义）、实现文件本身与测试——带通未塞入其他函数。

## 3. 主要问题（P0 → P3）

无 P0 / P1 / P2。P3 共 4 项：

| # | 等级 | 标题 | 位置 | 触发条件 | 实际影响 | 违反要求 | 最小修复方向 |
|---|---|---|---|---|---|---|---|
| P3-1 | P3 | 第三方 Butterworth 对照字面量的生成口径不可精确复现 | `tests/contract/test_processing_bandpass.py` L106–118 | 按注释口径（butter N=4、边缘 0.25/0.75·fs）重算 | 注释的生成配方与字面量不完全对应（最接近变体仍差 ~4.6e-4）；断言意图（sin² ≠ Butterworth 族，差 >0.5）已由审查者独立证实成立，不影响判定 | REFERENCE_MIGRATION §6「黄金样本口径可复现」的精神 | 在计划文档内保存生成脚本片段，或下次触碰该测试时重新生成字面量并把精确口径写入注释 |
| P3-2 | P3 | 「同名二次应用」在现行 core 下无合法链可达，唯一性门不可直达 | `src/uav_gpr/processing/bandpass.py` L399–414 + `core/time_domain.py` L116 | 构造第二条诚实 bandpass 记录（输入/输出域与真实阶段一致） | `frequency_filtered` 只能转向 `time_base`（core L116），第二条同签名记录自身即非法，链检查先生效；实际防重复由 stage 域门（输入域 ∉ 合法前驱集 → PROCESSING_DOMAIN_MISMATCH）执行，分层防御经探针证实有效 | 无（行为 fail-closed，方向正确） | 无需本 Issue 修复；后续 stage 家族出现合法同签名重复链时再补直达测试 |
| P3-3 | P3 | source 与 history 的配对由调用方保证（core 频域模型无域字段） | `bandpass.py` L291–295、计划 D2 | 调用方传入与 history 末域不符的 source 数组 | stage 无法检测该错配（设计已知限制，D2 已声明；provenance 全在 history） | 无（受限于 ISSUE-004 模型契约，非本 Issue 引入） | 由 ISSUE-036 编排/revision 阶段把模型与 history 绑定收口 |
| P3-4 | P3 | inScope 路径名与 t1 基线单 §3.9「预期」不同（声明式偏差） | 计划 §2/§3 | stage 契约并入 `bandpass.py`、`__init__.py` 不动（沿用 027 先例） | 总数仍为 4，且在 t2 开始时按基线单「如需调整必须声明并保持总数 4」条款在计划中声明 | 无（条款已满足） | 无 |

> P3-2 的探针证据：穷举 6×6×6×6 域组合，仅 2 条「谎报 stage_name 的异域转换」链能触达唯一性门（INVALID_ARGUMENT + context.stage_name/version 回显）；对诚实重复链，域门先拒。用与 t2 测试相同的临时放宽（`_validate_transition`/`pairwise` monkeypatch，finally 恢复）验证唯一性规则独立生效。

## 4. 逐 Issue 验收矩阵（M06 L137–141）

| 验收标准 | 状态 | 代码证据 | 实际测试证据 | 问题/限制 |
|---|---|---|---|---|
| raw 输入对象/数组不变；输出 history 精确记录版本/参数（L139） | **PASS** | 输入为 core bytes-backed 快照（`core/frequency.py` L34–38 `_immutable_array`，`setflags` 攻击被 NumPy 拒绝）；输出经新乘积数组重建容器（`bandpass.py` L430–455）；history 经冻结 `ProcessingHistory.append`（L440）；record 参数 JSON-safe 规范化（L360–368、L432–439） | 复跑 `test_raw_input_never_mutated_and_output_is_fresh`、`test_history_argument_never_mutated`；探针 m1–m7（输入 array_equal/id 不变、writeable=False、`may_share_memory=False`、写输出抛 ValueError、history 长度 0 不变、stage 快照调用方边频表）；h1–h3（record stage_name/version/参数/域/UTC 逐项、`to_dict` 往返相等、canonical JSON 精确串） | 无 |
| 单/双通道和频率边界与参考对拍（L140） | **PASS** | sin² 分段公式逐式迁移（`bandpass.py` L277–288 ↔ 参考 `bandpass.py` L64–74）；默认四边频 Hz 一致（L79–84 ↔ 参考 L25–30）；边界闭区间归属一致 | ①黄金 6 频点窗值/双通道 24 复数输出字面量由审查者**独立重算全等**（probe1：window/ch0/ch1 == 字面量均 True）；②**双实现并排对拍**：导入 UAV 与参考 `build_bandpass_window`，24 组轴×边频组合（含塌缩通带 f2==f3、窄裙边、单点轴、裙边+阻带）输出 **bit-exact 全等 0 失配**（probe3）；③边界测试 L256–272（f1/f2/f3/f4 闭区间、裙边中点、部分相交合法）；④通道一致性 L385–402（逐通道 sweep 拼接 == scan 广播积，探针 y5 复证）；⑤dtype/complex 保持（y1–y4，complex128、shape (1,2,6)、容器/通道保持） | 无 |
| 带通与 IFFT 无隐式耦合（L141） | **PASS** | `bandpass.py` 导入仅 stdlib+numpy+`uav_gpr` core（L44–59，grep 无 fft/scipy/Qt/storage）；`processing/` 无 IFFT 模块；排除项全部未实现（M06 L135） | ①模块导入清单实测（grep）；②`test_window_differs_from_third_party_filter_family` 复跑通过（max|sin²−Butterworth| > 0.5）；③审查者独立重算 Butterworth 响应（scipy 1.18.1，探针）：与 sin² 窗最大差 ~1.0，进一步证实无滤波器族偷换；④测试文件无 scipy 运行时依赖（grep 仅注释） | P3-1：对照字面量生成口径不可精确复现（意图独立证实成立） |

附带契约项（M06 L128–131 范围行）：ProcessingStage 协议（runtime_checkable，L97–128）、参数规范化（Hz/JSON-safe，L360–368）、输入/输出域（L352–358）、history 追加（L440）、重复 stage/非法参数/不支持域拒绝（L178–235、L399–414、L485–556）——均 PASS（探针 e1×11、e2×4、s1–s7、d1–d2、c1、t1–t2 全绿）。

## 5. Git 与交付检查

- **分支/基线**：`main @ b4d4b0f`，审查期间无新提交（t2 按提示词未 commit/push，报告属实）。reflog 最近 8 条仅 029 合并链（commit/merge/checkout），无 reset/rebase/amend 痕迹。
- **工作树**：`git status --porcelain` = 1 M（M06，仅 L118 一行 diff）+ 4 ??（t2 三新文件 + t1 基线单）；无缓存/日志/构建物/实测数据/密钥/参考仓库文件进入交付（`__pycache__` 为 gitignored 测试缓存）。
- **`git diff --check`**：exit 0（无空白错误）。
- **范围外修改**：未发现——core/**、docs/PROCESSING.md、reference-baselines、tools/、参考目录全部哈希/状态不变（见 §2）。
- **一个 Issue 拆分/混入**：无——本批工作树变更全部属于 ISSUE-030（t1 报告为其流程产物）。
- **schema/协议/架构契约变更**：无——消费 ISSUE-007 冻结契约（append/唯一性/域链全部走 core），无新错误码、无平行 history 类型；无需新 ADR。
- **commit/merge/push**：本轮均未发生，符合「报告并停止」。合并动作（feat/issue-030 分支创建、--no-ff、M06 Done、push）留给 captain 依自动化轮约定执行。

## 6. 测试与验证结果

解释器：`.venv/Scripts/python.exe`（Python 3.13.14，Windows）；pytest 8.4.2；numpy 2.5.2；scipy 1.18.1（仅探针用，非产品依赖）。

| # | 命令 | 退出码 | 结果 |
|---|---|---|---|
| 1 | `python -m pytest tests/contract/test_processing_bandpass.py -q` | 0 | **32 passed** in 0.08s |
| 2 | `python tools/quality/verify.py`（全量门禁，跑 2 次均 exit 0） | 0 | pytest **1169 passed / 4 deselected** in 272.38s（基线 1137+32 吻合）+ ruff `All checks passed!` + mypy `Success: no issues found in 50 source files` + package import ok，`[quality] all gates passed` |
| 3 | `python -m ruff check src/uav_gpr/processing/bandpass.py tests/contract/test_processing_bandpass.py` | 0 | All checks passed! |
| 4 | `python -m mypy src/uav_gpr/processing/bandpass.py` | 0 | Success: no issues found in 1 source file |
| 5 | `git diff --check` | 0 | clean |
| 6 | 参考源 SHA-256 复核（4 文件）+ manifest 对拍 | 0 | 4/4 相等 |
| 7 | t1 契约文件 SHA-256 复核（6 文件前 16 位） | 0 | 6/6 相等 |

**变异/反例探针**（系统临时目录 `…\temp\issue030_review`，运行后已删除，工作树零残留）：

- **probe1 黄金独立重算**：参考公式逐式重算窗值 [0, 0.09549150…, 0.5…, 1.0, 0.79389263…, 0.0] 与测试字面量 **全等**；双通道 24 复数输出全等（探针 `==` 精确比较 True）。第三方 Butterworth 重算与 sin² 最大差 1.0。
- **probe2 契约反例套件 52/53 通过**（唯一"FAIL"为审查者自建断言口径错误，见 probe2b/2c 诊断）：域门（空/calibrated/background/raw+background 合法 4 路全过；filtered/time_base/time_processed 末域 → PROCESSING_DOMAIN_MISMATCH）；非法四频点 11 组（3/5 个、乱序、f1=f2、负值、NaN、Inf、bool、str、None、f4≤f3 → INVALID_ARGUMENT + context 回显）；不相交 4 组（全高/全低/f1 贴轴顶/f4 贴轴底 → OUT_OF_RANGE）+ 部分相交合法；轴反例（2-D/空/NaN/递减 → AXIS_MISMATCH/INVALID_ARGUMENT/NON_FINITE_AXIS/NON_INCREASING_AXIS；单点轴合法）；raw 不变 7 项；参数快照不可变 3 项；重复 stage（真实路径域门拒 + 唯一性门独立生效）；record 精确性/往返/canonical JSON；naive datetime → NAIVE_DATETIME；非模型/非 history → TypeError；dtype/shape/容器/通道 5 项；协议 isinstance。
- **probe3 双实现并排对拍**：UAV `build_bandpass_window` vs 参考 `rebar_inspector.processing.bandpass.build_bandpass_window`，24 组轴×边频（含塌缩通带、窄裙、单点）**bit-exact 0 失配**；参考对不相交频段静默产出全零窗、UAV 以 OUT_OF_RANGE 拒绝（计划 M2 声明的收紧项，行为符合声明）。

## 7. 报告与事实差异

| t2 报告声明 | 核验结果 |
|---|---|
| bandpass.py 460 行 / 测试 674 行 32 个 | 一致（实测 460/674/32 passed） |
| 定向 32 passed | 一致 |
| verify.py exit 0，1169 passed/4 deselected + ruff + mypy(50) + import | 一致（复跑 2 次） |
| changedPaths = inScope 4 路径逐一相等 | 一致（git status 实测 4 交付物 + t1 报告） |
| 参考源 SHA-256 与 manifest 一致 | 一致（4/4 实测） |
| M06 L118 `Planned → Review` | 一致（diff 仅此一行） |
| 未 commit/push | 一致 |
| 无 sleep / 无 scipy 运行时依赖 | 一致（grep 仅注释） |
| 黄金字面量「按参考逐式计算并内嵌」 | **证实**：审查者独立重算全等 |
| THIRD_PARTY_BUTTERWORTH「Produced once with scipy.signal in the project venv」 | **无法精确复现**（按注释口径重算不等于字面量，见 P3-1）——声明无从证实亦无反证其数值合理性（形态与量级均吻合 Butterworth 族；断言意图独立证实成立） |
| 执行日志三条修正口径（references fail-closed / 唯一性门不可直达 / NaN 回显 JSON-safe） | 交叉核实：`core/time_domain.py` L199–256（引用域校验）、L94–126（转换表）、`core/errors.py` L48–69（JSON-safe）与其描述一致 |

过程性声明（「红灯先行」）无法事后独立证明，标为「未发现反证」。

## 8. 剩余风险

1. **P3-1 字面量口径**：第三方对照字面量不可由注释配方精确复现；若未来有人按注释重算会发现不一致（数值影响仅限该对照断言，sin² 黄金不受影响）。
2. **P3-3 配对信任**：source/history 错配在 stage 层不可检测，需 ISSUE-036 收口前在调用侧自律。
3. **状态行滞后**：M01/M06 各状态行未完全反映实际进度（t1 已注明；本 Issue 仅按范围更新 L118），对后续自动化轮的依赖读取无实质影响。
4. **远端同步**：本地 main 相对 origin 的推送状态以 captain 后台 push 为准（t1 已如实声明，本审查不重复断言）。

## 9. 合并建议

**建议合并（PASS）**。三条验收全部满足、门禁全绿、对拍 bit-exact、交付边界与声明一致、无 P0/P1/P2。P3 项均为文档/口径精度问题，不构成合并条件。按自动化轮约定由 captain 执行：feat/issue-030 → `--no-ff` 合入 main → M06 L118 `Review → Done` → push，随后进入 ISSUE-031。

## 10. 最小修复清单

阻止合并项：**无**。

非阻止（建议随后续 Issue 顺手处理）：

1. （P3-1）为 `tests/contract/test_processing_bandpass.py` L106–118 的 `THIRD_PARTY_BUTTERWORTH` 补充精确生成脚本（或重新生成字面量），消除口径不可复现问题。
2. （P3-2）当后续出现可合法重复签名的 stage 时，为「同名二次应用直达唯一性门」补充直达契约测试。

---

*审查者声明：本审查全程只读；探针仅运行于系统临时目录且已清理；审查结束时工作树与本审查开始时一致（除本报告为唯一新增文件）。审查后停止，等待 captain 决定合并或修复。*
