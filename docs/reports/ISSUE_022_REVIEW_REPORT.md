# ISSUE-022 独立复审报告（同 sweep S11/S22 双反射采集）

日期：2026-09-02
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-022-librevna-dual`（审查器 reviewer，任务 t3，attempt 9b76e883-3da4-4c7d-be8a-01d2c9f05650）
依据：[docs/ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) v1.0（§13 固定格式）
性质：独立只读复审。除本报告外未修改任何项目文件，未 commit/push/merge/clean/创建分支；变异探针在系统临时目录 `/tmp/iss022_probe_tree`（WSL 侧）与 `D:\tmp`（Windows 侧脚本）中运行并已全部删除清理（项目内零残留）；审查前后工作树逐字节一致（复核见第 5 节）。

## 1. 审查结论

**VERDICT: PASS**

ISSUE-022 实现真实、完整、合规：3 条验收标准逐条 PASS（第 4 节），提示词必测项全部落实，无 P0/P1/P2 问题（第 3 节，仅 3 个 P3 观察项），t2 声称的测试命令与数字全部独立复现（第 6 节），Git/交付检查干净（第 5 节），报告与事实无实质性差异（第 7 节），另补 6 项 t2 未覆盖的反例/变异探针（双顺序 S11 sweep、S11+S22 顺序 sweep、NaN 分母、S11 侧零分母、双 stage 超时公式、正向控制）全部 PASS（第 6.4 节）。可进入自动化合并流程（合并建议见第 9 节）。

## 2. 自动识别的审查范围

| 项 | 结论 | 证据 |
|---|---|---|
| Issue | ISSUE-022「同 sweep S11/S22 双反射采集」（M04 L116–151；映射 FR-003/013；ACQUISITION.md §1/§3/§5、DATA_MODEL.md §3/§5）；直接依赖 ISSUE-021 | `docs/issues/M04_LIBREVNA.md`；`docs/issues/README.md` L89；`docs/PRODUCT_REQUIREMENTS.md` L47（FR-003 含「为 S11/S22 双反射通道保留契约」） |
| 基线/分支 | `main` @ `9d55533f56edffb3906b764b0d414db06d5667cb`（HEAD == origin/main，0/0）；t2 未创建分支、未 commit/push/merge（自动化授权流程在审查 PASS 后由 captain 合并） | `git rev-parse HEAD origin/main`（均为 9d55533）；`git branch -a`（无 feat/issue-022）；`git log --oneline -3`；`git reflog -5`（仅 commit/merge/checkout，无 reset/rebase/amend/强推） |
| 直接依赖 | ISSUE-021 Done（`82d1c3b`+`7af5403`+`9d55533`（HEAD），backend.py 1078 行/39 测试，复审 VERDICT=PASS）；ISSUE-020 冻结层 stream.py 的 `ReceiverSlot`/`StrictSweepAssembler(receiver_plan=…)`/`datapoint_matches_plan` 实测存在且参数化（stream.py L276–304、L368–401） | `git log`；`src/uav_gpr/acquisition/librevna/stream.py` 全文；`docs/reports/ISSUE_021_REVIEW_REPORT.md`；M04 L81 状态行 Done |
| 改动文件（工作树实测） | ① `src/uav_gpr/acquisition/librevna/backend.py`（1078→1184 行，扩展）② `tests/contract/test_librevna_backend.py`（975→1223 行，39→48 测试）③ `docs/plans/2026-09-02-issue-022-librevna-dual.md`（226 行，新）④ `docs/issues/M04_LIBREVNA.md`（`git diff` 证实仅 L118 状态行 `Planned → Review`，1 行改动）——**changedPaths 与 inScope 4 路径逐一相等** | `git status --porcelain=v1 -b`（5 条目：上述 4 项 + t1 基线单 `docs/reports/ISSUE_022_BASELINE_CONFIRMATION.md`，后者为 t1 交付物）；`git diff --stat`（3 tracked 文件、403+/49−）；`git diff -- docs/issues/M04_LIBREVNA.md` |
| 排除项确认 | 未改 `core/**`、`acquisition/backend.py`（基类）、`librevna/transport.py`、`librevna/stream.py`、`librevna/__init__.py`、两个参考仓库；无 S21/S12（0x1241 仅双反射位图，S21 通道配置拒绝，backend.py L951–965）、无校准、未进入 ISSUE-023（M04 L153–188 仍 Planned） | 工作树范围 + `git diff --stat` + M04 全文 |
| 参考源 | ISSUE-022 不迁移新源文件：双通道语义（`parse_s11_s22_point` 的 S11/S22 比值、stage-1 接收机扩展机制）已由 ISSUE-020/021 迁移记录冻结（t1 基线单 §2 引用的 4 源 + 1 夹具 SHA-256 已在 ISSUE-021 复审对拍）；0x1241 SweepSettings 黄金字节由 t2 独立 struct.pack 推导并固定于计划 §4（本复审逐字节复核见第 6.3 节） | t1 基线单 §2；计划文档 §4 |

审查期间必读资料全部完成：AGENTS.md（系统注入全文）、CONTRIBUTING.md、README.md、docs/INDEX.md、docs/issues/README.md、M04_LIBREVNA.md（ISSUE-022 条目全文）、docs/ACQUISITION.md 全文、docs/DATA_MODEL.md 全文、t1 基线确认单、t2 计划文档、ISSUE_REVIEW_STANDARD.md、ISSUE-021 复审报告（格式模板与 P3 承接清单）。

## 3. 主要问题（P0→P3）

无 P0 / P1 / P2。以下均为 P3（低风险，不阻止合并）：

- **P3-A**（防御性死分支，`backend.py:1147–1160`）：`_compute_s_parameter` 的「plan-valid datapoint 缺槽位/零分母」异常分支对合法路径不可达——所有进入 `_finalize_sweep` 的 datapoint 都已被 `datapoint_matches_plan`（stream.py L284–304）逐槽验证（恰一个匹配、有限、reference 幅值非零）。与 ISSUE-021 的 `_compute_s11` 同款防御 fail-closed 设计，非功能缺失；建议（可选）保持现状或加注释说明防御定位。
- **P3-B**（双 stage 超时公式无独立断言测试，`backend.py:1085–1097`）：`_compute_sweep_timeout` 的 `stages = 2 if len(config.channels) >= 2 else 1` 推导未被测试套件以数值断言固定（默认 min 2.0 s 掩盖差异）。本复审变异探针 F 实测 `dual = 2 × single`（0.0101 s vs 0.00505 s，1e-12 精度相等）；建议（可选）后续加一条 min 下调后的数值断言。
- **P3-C**（承接记录，文档级）：ISSUE-021 复审 P3-C（`SweepSettings` 上界前移 ValueError）与 ISSUE-020 P3-2（`ReceiverSlot` 构造校验，需改 stream.py）本任务显式延后（计划 D9 记录），符合「P3 可选、超 inScope 需先记录」口径；M04 L44 措辞修正建议（ISSUE-021 复审 §10-4）同样未做——M04 改动保持「仅状态行一行」纪律，正确。

## 4. 逐 Issue 验收矩阵（M04 L137–141 三条 + 提示词必测项）

| # | 验收标准 | 状态 | 代码证据 | 测试证据 |
|---|---|---|---|---|
| 1 | 形状严格 `2 × frequency`，通道顺序来自配置 | **PASS** | `backend.py:852–947`（`_finalize_sweep` 按 `config.channels` 逐通道 `s_parameter → (stage, port_mask)`（`_S_PARAMETER_SLOTS` L196–199）计算行向量，`np.asarray(rows).reshape((len(channels), freqs.size))` L885–887，`channels=config.channels` L943）；`_validate_config` 接受 `(S11,)`/`(S11,S22)`/`(S22,S11)`（L951–965），S22-only/S21/S12/其它组合拒绝 | `test_dual_acquire_values_shape_and_metadata`（L1099：`data.shape == (2, 101)`、`channels == (S11_CHANNEL, S22_CHANNEL)`）、`test_dual_channel_order_from_config`（L1139：`(S22, S11)` 配置 → 行序对调、行值按 s_parameter 推导）；正向变异探针 E 复现 2×11 与数值 |
| 2 | 任一通道缺点/坏分母则整道拒绝 | **PASS** | 冻结层 `datapoint_matches_plan`（stream.py L284–304）对双 plan 的每个槽位要求恰一个匹配、值有限、reference 幅值非零；plan-invalid datapoint 使 active sweep 失效 → 不产出 `AssembledSweep` → `trace_index` 不分配（backend.py L859、L941 仅成功后递增）；双 plan `(0,REF)+(0,P1)+(1,REF)+(1,P2)`（backend.py L186–190） | `test_dual_partial_channel_failure_no_trace`（L1155：S11-only 数据流喂双配置，traces==0、invalid_points==101）、`test_dual_s22_zero_reference_no_trace`（L1174：stage-1 ref=0 → 整道拒绝）；**变异探针 A/B/C/D**（见 6.4 节） |
| 3 | S11-only 行为不回归 | **PASS** | S11-only 路径保持 0x1240（`_build_sweep_settings` L1066–1082：`len(channels)==1 → S11_STAGES_BITMAP`）、`S11_RECEIVER_PLAN`（`_do_configure` L614–618）、`(1, N)` shape（L885 reshape 对单通道退化为 1×N）、单 stage 超时（L1089） | 既有 39 测试（含 3 处按 D8 更新的断言）全部保持通过（48 测试定向复跑 = 39 既有 + 9 新增）；`test_acquire_complete_sweep_values_and_metadata` 等 1×N 数值/轴/hash 断言未变 |
| 4 | Port1/Reference→S11、Port2/Reference→S22 严格映射 + capability 检查 | **PASS** | `_compute_s_parameter(stage, port_mask)`（L1132–1160）：stage 过滤 `desc >> DESC_STAGE_SHIFT`、reference 位优先、端口位按 mask 取，`port / reference` 复数比值；`_S_PARAMETER_SLOTS = {S11: (0, PORT1), S22: (1, PORT2)}`（L196–199）；capability 面 `_do_open` 返回 `channels=(S11_CHANNEL, S22_CHANNEL)`（L578）→ `supports_dual_channel=True`；不支持通道集在 configure 期拒绝（L951–965） | `test_dual_acquire_values_shape_and_metadata`（S11=(0.5−0.2j)/1.0、S22=(0.3+0.1j)/1.5 数值对拍）、`test_dual_channel_order_from_config`（反序行值对拍）、`test_open_requests_device_info_and_set_idle`（L416：channels/dual 能力断言，D8 更新）、`test_configure_rejects_unsupported_channels`（L517：S21 通道拒绝）、`test_configure_rejects_s22_only`（L531） |
| 5 | HH:S11/VV:S22 默认绑定经 ChannelSpec 配置，非数组硬编码 | **PASS** | `S22_CHANNEL = ChannelSpec(channel_id="vv_s22", logical_polarization=VV, s_parameter=S22, display_name="VV S22")`（L177–183）与 `S11_CHANNEL` 同构；输出行语义来自 `config.channels` 逐通道的 `s_parameter` 字段（L870–884），与行号无关；核心枚举 `SParameter.S22`/`LogicalPolarization.VV` 只读消费（core/enums.py） | `test_dual_channel_order_from_config`（`(S22, S11)` 配置 → data[0] 为 S22 行）——直接反证「行0=HH S11」硬编码假设 |
| 6 | 同 sweep 点完整性与 shared metadata（单 sweep 单 trace identity） | **PASS** | 双通道来自同一 `VNADatapoint` 集合（同一 `AssembledSweep`，`_finalize_sweep` 单次调用）；单条 `TraceMetadata`（L921–940）：一个 `trace_index`/`trace_uid`（L888–894）/一组 UTC+monotonic 边界/`connection_generation` 快照；raw hash 对 2×N 整体计算（`RawHashSpec(channels=config.channels, data=data)` L895–902） | `test_dual_acquire_values_shape_and_metadata`（metadata 唯一、trace_index==0、hash 与 `RawHashSpec` 独立重算相等、UTC/monotonic 有序、traces==1）、`test_dual_two_sweeps_in_one_read`（L1194：单 read 两个双 sweep → trace_index 0→1、traces==2，双通道吞吐计数口径） |
| 7 | 协议黄金夹具（0x1241） | **PASS** | `SweepSettings` 0x1241 黄金字节（计划 §4 独立 struct.pack 推导：`<QQHIhBHhH` 100e6/1e9/101/100e3/−1000/0x0C/0x1241/−1000/0）；`encode_sweep_settings`/`SweepSettings.__post_init__` 对 0x1241 放行、未验证位图（0x1242）仍 `ValueError`（backend.py L286–306）；`_build_sweep_settings` 按通道数选 0x1240/0x1241 | `test_golden_dual_sweep_settings_encode`（L1069：双向对拍 `DUAL_SWEEP_SETTINGS_PAYLOAD_HEX`）、`test_dual_configure_sends_dual_stages_bitmap`（L1085：下发载荷含 0x1241，S11-only 仍 0x1240）、`test_sweep_settings_validation`（L397：0x1242 拒）；本复审对黄金 hex 逐字节独立复核（见 6.3 节） |
| 8 | 首道前轴门禁在双通道下保持 | **PASS** | `_verify_first_axis` 复用不变（L1099+），`_finalize_sweep` 在 `trace_index==0` 时先门禁后分配（L859–860） | `test_dual_first_sweep_axis_mismatch_rejected`（L1214：+10 kHz 双通道轴偏移 → `BackendConfigRejectedError`、traces==0） |
| 9 | 排除项：禁止两个顺序 sweep 冒充同步双通道；无 S21/S12/校准 | **PASS** | 双通道只能由满足 4 槽 plan 的同一 datapoint 集合产出（`S11S22_RECEIVER_PLAN` L186–190 + `datapoint_matches_plan` 逐点强制）；任何 S11-only/S22-only datapoint 在双 plan 下 plan-invalid → 整道不产出；0x1241 为双反射位图（无单 S22 语义），S22-only 配置拒绝 | **变异探针 A**（两个顺序 S11-only sweep 喂双配置 → timeout、traces==0、invalid_points==202）、**B**（S11 sweep + S22-only sweep 顺序喂入 → traces==0、invalid_points==202）；`test_configure_rejects_s22_only`、`test_configure_rejects_unsupported_channels` |

9/9 PASS；无 FAIL/PARTIAL/BLOCKED；NOT APPLICABLE：真机 opt-in smoke（本机无授权设备；0x1241 真机验证归 ISSUE-023，符合 M04 L178 约束；t2 计划 §8 已如实声明「0x1241 的真机验证归 ISSUE-023，无硬件时只承诺契约正确」）。

## 5. Git 与交付检查

| 检查项 | 结论 | 证据 |
|---|---|---|
| 分支/基线 | main @ `9d55533`，HEAD == origin/main，0/0；审查前后一致 | `git rev-parse HEAD origin/main`（均 9d55533）；`git status -b` |
| 提交历史 | 本轮 t2/t3 零提交、零推送、零分支；reflog 仅 commit/merge/checkout，无 reset/rebase/amend/强推 | `git log --oneline -3`；`git reflog -5`；`git branch -a`（无 feat/issue-022） |
| 未提交/未跟踪 | 恰好 5 条目 = t2 inScope 4 路径 + t1 基线单；无缓存/日志/密钥/实测数据/构建产物混入（`.pytest_cache`/`.mypy_cache`/`.ruff_cache`/`.venv` 均 `git check-ignore` 命中） | `git status --porcelain=v1 -b`（审查前后逐字节一致）；`git diff --stat`（3 tracked 文件，无范围外改动） |
| 范围纪律 | M04 仅状态行 1 行改动；stream/transport/core/基类零改动（双 plan 为 backend.py 内组合，stream.py 只读消费成立） | `git diff -- docs/issues/M04_LIBREVNA.md`（仅 L118）；`git diff --stat` |
| 白空格/冲突 | `git diff --check` clean（exit 0） | 实测 |
| 计划文档 | 226 行，含 D1–D9 设计决策、§4 迁移 provenance、§10 执行日志（红灯 ImportError → 绿灯 48 passed）、§11 门禁数字；数字与实测一致 | `docs/plans/2026-09-02-issue-022-librevna-dual.md` 全文 |

## 6. 测试与验证结果

### 6.1 环境

- 解释器 1（t2 同口径）：`./.venv/Scripts/python.exe`（Windows venv）Python 3.13.14（实测 `--version`）。
- 解释器 2（变异探针）：WSL `python3` Python 3.12.3；探针经 `PYTHONPATH` 指向临时树 `/tmp/iss022_probe_tree`，并实测 `backend.__file__ == /tmp/iss022_probe_tree/src/uav_gpr/acquisition/librevna/backend.py` 证实执行的是临时树副本（非项目 editable 安装）。

### 6.2 t2 声称命令与数字独立复现

| t2 声称 | 复审实测 | 结论 |
|---|---|---|
| 定向 `pytest tests/contract/test_librevna_backend.py -q` → 48 passed in 0.12s | **48 passed in 0.13s**，exit 0 | 复现 ✓ |
| 依赖定向（transport+stream+backend+controller+librevna_backend）→ 275 passed in 4.23s | **275 passed in 4.19s**（50+61+28+88+48），exit 0 | 复现 ✓ |
| `ruff check src tests` → All checks passed | **All checks passed!**，exit 0 | 复现 ✓ |
| `mypy src` → 42 source files | **Success: no issues found in 42 source files**，exit 0 | 复现 ✓ |
| `tools/quality/verify.py` → 901 passed / 1 deselected，exit 0 | **901 passed, 1 deselected in 283.55s**（892 基线 + 9 新增），ruff/mypy/import 全过，exit 0 | 复现 ✓ |
| AST 守卫（默认测试不枚举 USB/网络） | `tests/unit/test_no_external_access.py` → **3 passed**，exit 0 | 复现 ✓ |
| `git diff --check` clean | exit 0 | 复现 ✓ |
| 红灯（实现前 collection ImportError） | 无法事后重放（实现已落地），但计划 §10 日志与 §11 数字一致、9 个新测试名/断言与最终文件逐一对应，标为「未发现反证」 | 采信 ✓ |

### 6.3 黄金字节独立复核

`DUAL_SWEEP_SETTINGS_PAYLOAD_HEX = "00e1f5050000000000ca9a3b000000006500a086010018fc0c411218fc0000"`（31 字节，test L112）：本复审用独立脚本按 `<QQHIhBHhH` 字段重排：start 100e6=`00e1f50500000000`、stop 1e9=`00ca9a3b00000000`、points 101=`6500`、ifbw 100000=`a0860100`、power −1000=`18fc`、config 0x0C=`0c`、stages 0x1241=`4112`、cdbm −1000=`18fc`、dwell 0=`0000`——与常量逐字节相等；与 S11 黄金向量仅 stages 字段 `4012→4112` 之差。✓

### 6.4 变异探针（t2 未覆盖的反例，临时树运行后已清理，项目零残留）

| 探针 | 内容 | 结果 |
|---|---|---|
| A | 双配置 + **两个顺序 S11-only sweep**（101+101 点，单 read）→ 必须不产出双通道 trace | **PASS**：`BackendTimeoutError`、traces==0、invalid_points==202（禁止顺序 sweep 冒充直接反证） |
| B | 双配置 + **S11 sweep 后接 S22-only sweep**（顺序喂入）→ 不得拼成一道 | **PASS**：timeout、traces==0、invalid_points==202 |
| C | 双配置 + stage-1 reference = **NaN**（非有限分母） | **PASS**：timeout、traces==0、invalid_points==101 |
| D | 双配置 + stage-0 reference = **0+0j**（S11 侧坏分母，t2 只覆盖 S22 侧） | **PASS**：timeout、traces==0、invalid_points==101 |
| E | 正向控制：双配置 + 一个合法双 sweep | **PASS**：恰 1 trace、shape (2,11)、S11/S22 数值精确 |
| F | `_compute_sweep_timeout` 双 stage 预算 = 单 stage × 2（min 下调至 0.001 以区分） | **PASS**：dual=0.010100000000000001、single=0.005050000000000001（1e-12 精度） |

### 6.5 测试后清洁

审查全部命令（pytest/ruff/mypy/verify）产生的缓存均 `git check-ignore` 命中；探针树/脚本已删除（`ls /tmp | grep -i iss022` 与 `ls /mnt/d/tmp | grep -i iss022` 均空）；审查前后 `git status --porcelain=v1 -b` 逐字节一致（仅 t2 5 条目 + 本报告新增）。

## 7. 报告与事实差异

- t2 完成报告全部关键数字（48/275/901 passed、1 deselected、42 文件、diff-check、5 条目工作树）与复审实测一致，无隐藏失败/跳过/占位。
- 计划 §10 执行日志中的「首轮 ruff RUF005 提示后修正」为过程性声明，无法事后独立验证，标为「未发现反证」（最终代码 ruff 全绿）。
- 无范围偏离：改动面与 inScope 4 路径逐一相等；排除项（stream/transport/core/基类/参考仓库）零改动。
- 无报告遗漏的已知限制：0x1241 真机验证归属 ISSUE-023、P3 承接项（D9）均在计划中显式记录。

## 8. 剩余风险

1. **0x1241 未经真机验证**（ISSUE-023 职责）：desc stage 位布局（stage-1 ref 0x30/port2 0x22）来自已审计迁移记录（ISSUE-020 计划 D5、参考 `parse_s11_s22_point` 语义），模拟器按该语义构造并通过全部契约测试；真机数值/时序待 ISSUE-023 基准确认。合并风险可控：双通道路径仅由显式双通道配置触发，S11-only 生产路径不接触 0x1241。
2. 双通道超时公式（2× 单 stage）为推导值（计划 D6），无真机实测锚定；本复审数值探针 F 证实公式按设计生效，真机确认归 ISSUE-023。
3. 防御性死分支（P3-A）与探针暴露的 plan 机制在畸形 desc（如同时带 ref+port 位）下的行为——`_slot_matches`（stream.py L276–281）对 port 槽要求「非 reference 位」，语义安全，非本 Issue 引入。

## 9. 合并建议

**建议合并**（自动化流水线：captain 直接合并提交推送）。建议操作：

1. 将 3 个 tracked 改动 + 2 个未跟踪交付物（计划文档、t1 基线单）纳入同一提交（或按流水线惯例分提交），提交信息建议：`feat(acquisition): same-sweep S11/S22 dual reflection backend (ISSUE-022)`，并在提交信息或 M04 状态行注明复审报告链接；
2. 合并后 M04 L118 状态行由 `Review` 更新为 `Done`（可随同一流程提交，保持「状态行一行」口径）；
3. 不进入 ISSUE-023；P3-A/P3-B/P3-C 均为可选改进，留 ISSUE-023 或后续顺带处理。

## 10. 最小修复清单

**无必需修复项**（0 个 P0/P1/P2；3 条验收标准全部 PASS）。可选（P3，不阻止合并）：

| 项 | 内容 | 建议 |
|---|---|---|
| P3-A | `_compute_s_parameter` 防御分支不可达 | 保持或加注释说明防御定位 |
| P3-B | 双 stage 超时公式无数值断言 | 可选：加一条 `sweep_timeout_min_s` 下调后的 `dual == 2 × single` 断言 |
| P3-C | ISSUE-021 P3-C / ISSUE-020 P3-2 承接项 | 已在计划 D9 显式延后，留后续任务 |
