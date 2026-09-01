# ISSUE-022 开工基线确认单（只读核查 + 实施计划摘要）

日期：2026-09-02（ISSUE-022 开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-022-librevna-dual`（执行器 engineer，任务 t1，attempt 9275cc29-65fd-4ad4-aeb5-320f5e5e7a5e）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支、未修改实现/测试/计划文件。
配套文件：本单为 t2（同 sweep S11/S22 双反射采集）与 t3（独立复审）的权威基线件；独立复审按 [ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) 执行。t2 完整实施契约见 t2 计划文档（`docs/plans/2026-09-02-issue-022-librevna-dual.md`，由 t2 先落盘）。

## 1. 锁定的目标 Issue 与依据

**ISSUE-022：同 sweep S11/S22 双反射采集**（`docs/issues/M04_LIBREVNA.md` 第 4 个条目，状态 `Planned`，L116–151）

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-022（M04_LIBREVNA.md L116–151） | docs/issues/README.md 依赖顺序主表 L89 |
| 直接依赖 | ISSUE-021（S11 生产采集后端） | M04 L119「直接依赖：ISSUE-021」；README.md L89 |
| 依赖状态 | **Done**：ISSUE-021（2026-09-02 独立复审 VERDICT=PASS 后自动化授权合并，M04 L81 状态行实测 Done，注明「6 项 P3 观察不阻止合并，P3-2 延后记录」） | M04 L81；docs/reports/ISSUE_021_REVIEW_REPORT.md；git log（见 3.2） |
| 功能映射 | FR-003、FR-013；`ACQUISITION.md` §3（LibreVNA 迁移含「S11 以及同 sweep 的 S11/S22 解析」）、§5（sweep 完整性含每通道频点数和顺序、reference 分母）；`DATA_MODEL.md` §3（ChannelSpec）/§5（`channel × frequency` shape、channels 顺序） | M04 L121 |
| 提示词工作区替换 | `E:\wurenjiruanjian` → 工作区根（见第 2 节） | docs/issues/README.md 第 3 节通用协议 |
| 一次一 Issue | 本轮只处理 ISSUE-022；不进入 ISSUE-023 | docs/issues/README.md 第 1/3 节；团队目标 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

ISSUE-022 不迁移新的参考源文件：双通道语义（`parse_s11_s22_point` 的 S11/S22 比值、stage-1 接收机扩展机制）已由 ISSUE-020 迁移记录（`docs/plans/2026-09-02-issue-020-librevna-stream.md` 第 4 节 D5：**「ISSUE-022 以 (0,REF)+(0,P1)+(1,REF)+(1,P2) 扩展同一机制」**）与 ISSUE-021 计划（D9：`S11_S22_STAGES_BITMAP=0x1241` 归 ISSUE-022）冻结；ISSUE-021 的 4 个迁移源 + 1 个黄金夹具源 SHA-256 已在 ISSUE-021 基线单/复审报告中逐一对拍（`librevna_protocol.py 6a41c4b1…`、`librevna_usb.py a73adc1c…`、`backend.py f05da35c…`、`sweep_config.py 9877b761…`、`tests/test_librevna_usb_backend.py 2d4db313…`；本地只读副本 `/mnt/d/博士任务/rebar-inspector` `main @ 7c522d2`）。

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      main（当前；工作树完全干净）
HEAD        9d55533  docs(issues): mark ISSUE-021 Done after automated authorized merge
            完整哈希 9d55533f56edffb3906b764b0d414db06d5667cb（2026-09-02）
分支关系    main...origin/main = 0/0（`git rev-parse HEAD` == `git rev-parse origin/main`）
git status --porcelain=v1 -b
    ## main...origin/main
    （无其他条目——无未跟踪/未提交文件，untracked-count=0）
git diff --check    # clean（exit 0）
```

reflog 实测仅 commit/merge/checkout 记录（顶层 `9d55533 commit` ← `7af5403 merge` ← `def2c28 checkout` ← `82d1c3b commit` ← `def2c28 checkout` …），**无 reset/rebase/amend/强推迹象**。本地存在历史遗留 feature 分支 `feat/issue-009 … feat/issue-021`（均已合入 main 后的开发痕迹，`git branch -a` 实测），当前分支为 `main`，不影响 t2。

### 3.2 直接依赖 ISSUE-021 的合入证据（main 内实测）

| 提交 | 内容 |
|---|---|
| `82d1c3b` | `feat(acquisition): LibreVNA S11 production acquisition backend (ISSUE-021)` |
| `7af5403` | `Merge feat/issue-021: ISSUE-021 LibreVNA S11 production backend` |
| `9d55533` | `docs(issues): mark ISSUE-021 Done after automated authorized merge`（当前 HEAD） |

tracked 交付物（main，`git ls-files` + 实测复现）：

| 交付物 | 实测事实 | ISSUE-022 复用/依据点 |
|---|---|---|
| `src/uav_gpr/acquisition/librevna/backend.py`（1078 行） | `tests/contract/test_librevna_backend.py` 实测 **39 passed**；M04 L81 状态行 Done | **ISSUE-022 直接扩展**：`S11_STAGES_BITMAP=0x1240` / `ALLOWED_STAGES_BITMAPS=(0x1240,)`（L122–128，注释明示「dual-reflection 0x1241 belongs to ISSUE-022」）；`S11_CHANNEL=ChannelSpec(hh_s11, HH, S11)`（L144–149）；`_validate_stages_bitmap` 硬拒 0x1241（L236–255）；`_do_configure` 以 `S11_RECEIVER_PLAN` 构造组装器（L557–565）；`_validate_config` 仅接受 `(hh_s11,)`（L870–877）；`_finalize_sweep` 单通道 `data = s11.reshape((1, n))`（L799–806）；`_compute_sweep_timeout` 单 stage 假设（L989–997）；`_compute_s11` stage-0 Port1÷Reference（L1031–1052） |
| `tests/contract/test_librevna_backend.py`（975 行，39 测试） | 实测 39 passed；`ScriptedAdapter`/`TickClock`/`ManualClock`/`_point_payload`（BLOCKED 布局）/`_sweep_bytes` 黄金夹具范式 | **ISSUE-022 直接扩展**：同一夹具加 stage-1 接收机（desc stage 位 bits7-5，stage1 → 0x20 基；reference 0x10 → 0x30；Port2 0x02 → 0x22）与双通道断言；S11-only 现有 39 测试即回归网 |
| `src/uav_gpr/acquisition/librevna/stream.py`（548 行，ISSUE-020） | 实测 **61 passed**；`ReceiverSlot(stage, mask)`、`S11_RECEIVER_PLAN=((0,REF),(0,P1))`（L270–273，注释明示「ISSUE-022 extends this with stage-1 slots for S22」）、`StrictSweepAssembler(receiver_plan=…)` 已参数化（L368–401）、`datapoint_matches_plan` 逐槽校验（恰一个匹配、有限、reference 非零，L284–304） | **ISSUE-022 只读消费**：双 plan 可在 backend.py 内组合 `S11_RECEIVER_PLAN + (ReceiverSlot(1, DESC_MASK_REFERENCE), ReceiverSlot(1, DESC_MASK_PORT2))`，无需改 stream.py；任一槽缺失/坏分母 → datapoint plan-invalid → active sweep 失效 → 整道不产出（「任一通道缺点/坏分母则整道拒绝」的机制已在冻结层） |
| `docs/plans/2026-09-02-issue-021-librevna-backend.md`（238 行） | 设计决策 D9（S22/双通道/0x1241 一律拒绝，归 ISSUE-022）、D5 引用（0x1241、`parse_s11_s22_point` → ISSUE-022） | 迁移记录口径；双通道边界与 0x1241 归属的权威记录 |
| `docs/reports/ISSUE_021_REVIEW_REPORT.md` | VERDICT=PASS（8/8 验收 PASS，0 P0/P1/P2，6 个 P3）；合并建议落实于 `9d55533` | 本单结构模板；P3 观察项（见 3.4 约束 13） |
| `docs/issues/M04_LIBREVNA.md` L81 / `docs/issues/README.md` L89 | 状态行实测 Done（含复审报告链接）；依赖表 `022 | 同 sweep S11/S22 双反射采集 | 021` | ISSUE-022 直接依赖已完成 |

### 3.3 ISSUE-022 为下一个可执行 Issue

- M04 L116–151：ISSUE-022 状态 `Planned`（L118）；L119 `直接依赖：ISSUE-021`（已 Done）。
- docs/issues/README.md L89：`022 | 同 sweep S11/S22 双反射采集 | 021`——依赖表无计划冲突；M04 其余条目（021 已 Done、023 `Planned` 且依赖 ISSUE-022）——**ISSUE-022 是 M04 当前唯一可执行项**。
- 落点核查：`docs/plans/` 与 `docs/reports/` 无任何 ISSUE-022 文件；`backend.py` 当前 `ALLOWED_STAGES_BITMAPS` 仅 `(0x1240,)`、`_validate_config` 仅接受单 S11 通道——t2 是首个引入双通道语义的改动点。

### 3.4 对 ISSUE-022 有约束的契约要点（读自 ACQUISITION.md、DATA_MODEL.md、M04 L116–151、ISSUE-020/021 迁移记录与复审报告、ISSUE_REVIEW_STANDARD.md、AGENTS.md、实测源码）

**ISSUE-022 范围（M04 L124–130）+ 提示词（L143–151）**：

1. **Port1/Reference→S11、Port2/Reference→S22 严格映射和 capability 检查**——扩展 `_compute_s11`（现 stage-0 固定，backend.py L1031–1052）为按 stage 计算：S11 = stage-0 Port1 ÷ stage-0 Reference，S22 = stage-1 Port2 ÷ stage-1 Reference；接收机 plan 用双通道 plan `(0,REF)+(0,P1)+(1,REF)+(1,P2)`（ISSUE-020 计划 D5 已固定该扩展形态）；`StrictSweepAssembler(receiver_plan=…)` 已参数化（stream.py L368–401），backend 构造时传入。capability 面：`Capabilities.channels` 由 `(S11_CHANNEL,)` 扩展为含 S22 通道；`supports_dual_channel`（len≥2 推导，ISSUE-015 计划 L45）随之成立。
2. **`HH:S11`、`VV:S22` 默认绑定通过 ChannelSpec 配置，不在数组字段硬编码**——DATA_MODEL.md §3：`ChannelSpec(channel_id, logical_polarization, s_parameter, display_name, 可选 antenna_note)`，**数组通道顺序由 `channels` 明确给出，禁止仅根据字典遍历或窗口顺序推断**；core enums 已支持 `SParameter.S11/S22`、`LogicalPolarization.HH/VV`（enums.py L44–59）。t2 须以同构 `ChannelSpec(channel_id="vv_s22", VV, S22, "VV S22")` 建模 S22，并从 `config.channels` 逐通道的 `s_parameter` 字段推导比值（S11 → stage-0 P1/REF，S22 → stage-1 P2/REF），禁止按行号硬编码「行0=HH S11、行1=VV S22」。
3. **同 sweep 点完整性和 shared metadata**——ACQUISITION.md §1「只有完整、校验通过且**通道齐全**的 sweep 才能进入存储」、§5「每个通道的频点数和顺序」「reference 接收机分母有效性」；同一完整 sweep 产出**单条 trace**：一个 `trace_index`/`trace_uid`/一组 UTC+monotonic 边界/一个 raw hash（对 `2 × frequency` 数据整体计算），S11/S22 共享 sweep 起点与 identity（M04 L124–125「两个通道共享真实时刻与 trace identity」）。
4. **单 S11 继续工作（S11-only 不回归）**——`(S11_CHANNEL,)` 配置路径保持 0x1240 + `S11_RECEIVER_PLAN` + `(1, N)` shape；现有 39 个 backend 测试必须全部保持通过。
5. **协议黄金夹具与双通道吞吐计数**——复用 `ScriptedAdapter`/`_point_payload`（BLOCKED 布局）范式，新增 stage-1 接收机；双通道吞吐计数（单 read 内多 sweep、双通道 sweep 计数）须由 t2 契约测试固定。

**硬件/协议语义**：

6. `stages_bitmap=0x1241`（双反射）是 ISSUE-021 显式延后的值（backend.py L122–128 注释、L236–255 硬拒、ISSUE-021 计划 D9）；t2 将 `ALLOWED_STAGES_BITMAPS` 扩展为 `(0x1240, 0x1241)`——0x1240 只允许 S11-only 配置，0x1241 只允许双通道配置（未验证组合不得下发，沿用 `_validate_stages_bitmap` 原则）。
7. **任一通道缺点/坏分母则整道拒绝**——机制已冻结在 stream.py：`datapoint_matches_plan` 对 plan 每个槽位要求恰一个匹配、值有限、reference 幅度非零（L284–304），plan-invalid datapoint 使 active sweep 立即失效（feed_datapoint L454–458）→ 不产出 `AssembledSweep` → 不分配 trace；`trace_index` 只在完整 sweep 被任务接受时分配（backend.py L860）。t2 须补契约测试：stage-1 槽缺失、S22 分母为零/非有限 → 整道拒绝、traces==0。
8. requested/applied 语义、首道前轴门禁（`AXIS_TOLERANCE_HZ=1.0`、`_verify_first_axis` L999–1029）与「不完整 sweep 不分配正式 trace」在双通道下保持 ISSUE-021 语义，不得削弱。
9. 采集超时：`_compute_sweep_timeout`（L989–997）现为单 stage 假设（`points × 1/IFBW × 5`）；双 stage 测量时间约为单 stage 2 倍，超时计算须按通道数扩展（t2 计划文档须固定决策）。
10. NACK fail-closed、控制包自路由、cancel/close 幂等、`connection_generation`、`_MAX_PENDING_SWEEPS` 有界缓冲——全部复用不变。

**其他约束**：

11. 排除项（M04 L133–135 + 提示词 L150）：不实现 S21/S12；**禁止连续执行两个独立 sweep 冒充同步双通道**；不做校准；不改 `core/**`、`acquisition/backend.py`（基类）、`librevna/transport.py`、`librevna/stream.py`、`librevna/__init__.py`（只读消费；双 plan 组合所需 `ReceiverSlot`/`DESC_MASK_*`/`S11_RECEIVER_PLAN` 均已由 stream.py 导出）；不进入 ISSUE-023。
12. 测试纪律：失败测试优先（先红灯后绿灯）；协议模拟器/合成字节流夹具（无硬件），**默认测试不枚举 USB**——`tests/unit/test_no_external_access.py` AST 守卫禁 `serial/usb/socket/requests/urllib/http/websocket(s)` 根导入；禁止固定 `sleep` 猜时序（`ManualClock`/`TickClock` 注入）；AGENTS.md §10 每能力覆盖正常/错误/取消/恢复路径；禁删测试/降断言/吞异常。
13. 遗留项承接（可选项，t2 计划须显式记录决策）：ISSUE-021 复审 P3-C（`SweepSettings` 上界 `dwell_us ≤ 0xFFFF`/`points ≤ 0xFFFF`/`start/stop ≤ 2^64−1` 前移为 `ValueError`，可选）；ISSUE-020 P3-2（`ReceiverSlot` 构造校验 stage ∈ 0..7、mask ≠ 0 + 2 测试——需改 stream.py，若采纳则超 inScope，须先与 captain 确认或显式记录延后）；ISSUE-021 复审 §10-4 建议 M04 L44 措辞顺带修正（可选，M04 状态行改动须保持「仅状态行一行」口径或由 captain 决策）。

### 3.5 发现的事实（非阻塞，供 t2 注意）

1. 工作树完全干净且与远端同步（`## main...origin/main` 0/0，HEAD `9d55533`）；t2 交付物将是唯一新改动。
2. backend.py 中 ISSUE-022 扩展点全部有显式注释/硬拒标记（见 3.2 表），t2 改动面清晰：0x1241 放行、S22_CHANNEL、双 plan、按 config 通道计算、`2 × N` data、超时×2、capability channels。
3. stream.py **无需改动**：组装器已参数化、plan 机制已冻结、双 plan 形态已由 ISSUE-020 D5 固定——t2 只需在 backend.py 组合 plan（保持「只读消费」约束成立）。
4. core 模型已就绪：`SParameter.S22`/`LogicalPolarization.VV` 存在；`FrequencySweep.data` 契约 `channel × frequency`、`channels` 长度等于 channel 轴且顺序稳定（DATA_MODEL.md §5）——`(2, N)` 双通道输出无需任何 core 改动。
5. 本地存在历史遗留 feature 分支 `feat/issue-009 … feat/issue-021`（均已合并回 main 的开发痕迹，当前 `main` 干净），与 t2 无关，不得触碰。
6. 门禁基线数字（本节第 4 章）：全量 892 passed/1 deselected（ISSUE-021 合入后口径），t2 完成后新基线 = 892 + 新增测试数，不得出现既有测试失败。

## 4. 门禁基线（核查时实测复跑，2026-09-02）

环境：WSL Ubuntu / Python 3.12.3；numpy 2.5.2、pytest 8.4.2、h5py 3.16.0、ruff 0.16.4、mypy 1.20.2；`uav_gpr` editable 可导入（`src/`）；解释器路径 `python3`（与 ISSUE-019/020/021 基线单同口径）。

```text
$ python3 -m pytest tests/contract/test_librevna_backend.py -q
39 passed in 0.28s                                  # ISSUE-021 定向（S11 回归网现状）

$ python3 -m pytest tests/contract/test_librevna_transport.py \
    tests/contract/test_librevna_stream.py \
    tests/contract/test_acquisition_backend.py \
    tests/contract/test_acquisition_controller.py \
    tests/contract/test_librevna_backend.py -q
266 passed in 4.70s        # 依赖定向：ISSUE-019：50 + ISSUE-020：61 + ISSUE-015：28 + ISSUE-017：88 + ISSUE-021：39

$ python3 tools/quality/verify.py
892 passed, 1 deselected in 134.27s (0:02:14)   # 全量非硬件 pytest（预期 892 达成）
All checks passed!                               # ruff（check .）
Success: no issues found in 42 source files      # mypy（42 文件，ISSUE-021 合入后）
package import ok                                # import 检查
[quality] all gates passed                       # verify.py 全部通过时 exit 0（无 [exit code] 标记）

$ python3 -m ruff check src tests                # 补充显式复跑
All checks passed!
$ python3 -m mypy src                            # 补充显式复跑
Success: no issues found in 42 source files
```

核查前后 `git status --porcelain=v1 -b` **逐字节一致**（仅 `## main...origin/main` 一行；untracked-count=0）；`git diff --check` clean；`.pytest_cache/`/`.mypy_cache/`/`.ruff_cache/` 均 `git check-ignore` 确认已忽略，无新缓存/日志/实测数据残留（本基线单为唯一新增未跟踪文件）。

## 5. ISSUE-022 实施计划摘要（供 t2 契约；完整版见 t2 计划文档，不含实现代码）

### 范围（M04 L124–130 原文口径 + 提示词）

1. 扩展 `src/uav_gpr/acquisition/librevna/backend.py`（已合入 main，只读消费 transport/stream/core）：`S11_S22_STAGES_BITMAP=0x1241` 加入 `ALLOWED_STAGES_BITMAPS`（0x1240↔S11-only、0x1241↔双通道的绑定校验）；`S22_CHANNEL = ChannelSpec("vv_s22", VV, S22, "VV S22")`；`Capabilities.channels` 含双通道；configure 按 `config.channels` 选择 stages_bitmap 与接收机 plan（双 plan = `S11_RECEIVER_PLAN + (ReceiverSlot(1, DESC_MASK_REFERENCE), ReceiverSlot(1, DESC_MASK_PORT2))`）；acquire 按每通道 `s_parameter` 推导比值（S11→stage-0 P1/REF、S22→stage-1 P2/REF），输出 `data` 形状严格 `2 × frequency`、`channels` 顺序来自配置；同一 sweep 共享 `TraceMetadata`/`trace_uid`/raw hash（对 2 通道整体）；双 stage 超时计算；S11-only 路径（0x1240/单 plan/(1,N)）保持原样。
2. 扩展 `tests/contract/test_librevna_backend.py`（失败测试优先）：双通道黄金夹具（stage-1 desc 0x30/0x22）、数值/顺序（通道顺序来自 config）、capability、部分通道失败（stage-1 槽缺失/坏分母 → 整道拒绝、traces==0）、双 sweep 吞吐、S11-only 全量回归（现有 39 测试不动）。
3. `docs/plans/2026-09-02-issue-022-librevna-dual.md`（计划文档，t2 先落盘，含设计决策、执行日志、门禁数字）。
4. `docs/issues/M04_LIBREVNA.md` 仅 ISSUE-022 状态行（L118）：`Planned → In progress → Review`，勿动其他条目（含 ISSUE-021 的 Done 状态行）。

### 排除项（M04 L133–135 + 提示词，t2 不得越界）

不实现 S21/S12；**禁止两个顺序 sweep 冒充同步双通道**；不做校准；不进入 ISSUE-023（重连/暂停恢复/硬件基准）；不改 `core/**`、`acquisition/backend.py`、`librevna/transport.py`、`librevna/stream.py`、`librevna/__init__.py`（只读消费）；不改两个参考仓库；不 commit/push/merge、不创建/切换分支；不新增范围外文件（若确需拆分/新增，t2 先停止向 captain 报告）。

### 验收标准（M04 L137–141 原文，t2 不得削弱）

1. 形状严格 `2 × frequency`，通道顺序来自配置。
2. 任一通道缺点/坏分母则整道拒绝。
3. S11-only 行为不回归。

### 测试矩阵要点（提示词必测项 + 依赖回归）

- 双通道：黄金协议夹具数值对拍（S11=stage-0 P1/REF、S22=stage-1 P2/REF，含参考 `parse_s11_s22_point` 语义）、`2 × N` shape、`channels == config.channels` 顺序（构造不同顺序的配置验证顺序来自配置而非硬编码）、capability（`Capabilities.channels` 双通道/`supports_dual_channel`）；
- 失败路径：stage-1 槽缺失、S22 reference 为零/非有限、任一 datapoint plan-invalid → 整道拒绝 + traces==0 + 统计可观测；半道/坏点沿用 ISSUE-021 拒绝语义；
- 共享 identity：单 sweep 单 trace（一个 trace_index/uid/时间戳组/raw hash 对 2×N 整体）；双 sweep 连续采集 trace_index 0→1、actual/schedule interval；单 read 多 sweep（吞吐计数）；
- S11-only 回归：现有 39 测试全绿（定向全量复跑）；`_validate_config` 对 `(S11_CHANNEL,)` 与 `(S11_CHANNEL, S22_CHANNEL)` 的绑定关系（0x1240/0x1241 与通道数的交叉校验，非法组合拒绝）；
- 门禁：依赖定向 266 → 新基线（含 ISSUE-021 39）；全量 `python3 tools/quality/verify.py`（892 + 新增数 passed/1 deselected）+ ruff + mypy（42 文件）+ import + `git diff --check` + 工作树检查；测试禁固定 sleep、不 import usb/serial/网络根（AST 守卫）。

### inScope 精确路径建议（以任务契约 t2 inScope 为准；t2 完成登记 changedPaths 必须与此逐一相等；沿用 ISSUE-021 教训：精确文件路径，非 glob）

1. `src/uav_gpr/acquisition/librevna/backend.py`（扩展：0x1241 放行 + 双 plan + S22_CHANNEL + 按 config 通道计算 + `2 × N` 输出 + 双 stage 超时 + capability）
2. `tests/contract/test_librevna_backend.py`（扩展：双通道契约测试 + S11 回归）
3. `docs/plans/2026-09-02-issue-022-librevna-dual.md`（新计划文档，t2 先落盘，含设计决策与门禁数字）
4. `docs/issues/M04_LIBREVNA.md`（仅 ISSUE-022 状态行 L118，勿动其他条目）

（t1 基线单 `docs/reports/ISSUE_022_BASELINE_CONFIRMATION.md` 为 t1 交付物，不计入 t2 inScope；t2 outOfScope：`docs/reports/**`、`docs/ACQUISITION.md`、`docs/DATA_MODEL.md`、`docs/ISSUE_REVIEW_STANDARD.md`、`docs/REFERENCE_MIGRATION.md`、`docs/reference-baselines/**`、`docs/TESTING.md`、`docs/adr/**`、`tools/**`、参考仓库（只读）、`src/uav_gpr/core/**`（只读消费）、`src/uav_gpr/acquisition/backend.py`（只读消费）、`src/uav_gpr/acquisition/librevna/transport.py`（只读消费）、`src/uav_gpr/acquisition/librevna/stream.py`（只读消费）、`src/uav_gpr/acquisition/librevna/__init__.py`（只读消费）。）

t2 验证命令按任务契约执行：`python3 -m pytest tests/contract/test_librevna_backend.py -q`（定向，先红灯后绿灯）、`python3 tools/quality/verify.py`（全量）、`python3 -m ruff check src tests`、`python3 -m mypy src`、`git diff --check && git status --porcelain=v1 -b`（同 ISSUE-018/019/020/021 口径，WSL 侧 `python3`）。

若确需拆分模块/新增文件，t2 必须先停止并向 captain 报告，不得自行新增范围外文件。

## 6. 结论

ISSUE-022 开工基线已锁定：`main`/HEAD @ `9d55533`（工作树完全干净、与 origin/main 同步 0/0、reflog 无破坏性操作）；直接依赖 **ISSUE-021 已 Done** 合入 main（`82d1c3b`+`7af5403`+`9d55533`（HEAD），backend.py 1078 行 + 39 测试实测复现，M04 L81 状态行 Done、复审 VERDICT=PASS、0 P0/P1/P2）；**ISSUE-022 是 M04 当前唯一可执行 Issue**（状态 `Planned`、无实现/测试/计划存在、依赖全绿）；契约要点（同 sweep 双通道 plan 形态 `(0,REF)+(0,P1)+(1,REF)+(1,P2)`、0x1241 放行与 0x1240↔S11-only 绑定、HH:S11/VV:S22 经 ChannelSpec 配置而非数组硬编码、单 sweep 共享 trace identity 与 2×N 整体 raw hash、任一通道缺点/坏分母整道拒绝、S11-only 不回归、双 stage 超时、AST 守卫、只读消费 transport/stream/core）已固化于第 3.4/3.5/5 节；门禁基线全绿（全量 892 passed / 1 deselected、ruff/mypy(42 文件)/import 全过、依赖定向 266 passed、ISSUE-021 定向 39 passed），核查前后 git 状态一致、无残留。**无阻塞项，可开工。** 本确认单即为 t2 实现与 t3 独立复审的权威基线件；t2 按第 5 节契约执行（先落盘计划文档 → 失败测试优先 → 最小实现 → M04 状态行 → 门禁 → 报告），完成后停止，不进入 ISSUE-023。

> 后续记录：本单为开工时点的基线快照，不随 t2 改动；t2 实际完成记录见其完成报告与 `docs/plans/2026-09-02-issue-022-librevna-dual.md`，t3 复审报告独立输出。
