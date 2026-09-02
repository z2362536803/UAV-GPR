# ISSUE-027 开工基线确认单（只读核查 + 实施计划摘要）

日期：2026-09-02（ISSUE-027 开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-027-osl-calibration`（执行器 engineer，任务 t1，attempt 0a605184-6238-4f4b-b420-3325cf8bbed7）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支、未修改实现/测试/计划文件（本单为唯一新增文件）。
配套文件：本单为 t2（OSL 校准模型与求解器）与 t3（独立复审）的权威基线件；独立复审按 [ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) 执行。t2 完整实施契约见 t2 计划文档（`docs/plans/2026-09-02-issue-027-osl-calibration.md`，由 t2 先落盘）。

## 1. 锁定的目标 Issue 与依据

**ISSUE-027：OSL 校准模型与求解器**（`docs/issues/M06_CALIBRATION_PROCESSING.md` 第 1 个条目，状态 `Planned`，L5–40）

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-027（M06_CALIBRATION_PROCESSING.md L5–40） | docs/issues/README.md 依赖顺序主表 L94 |
| 直接依赖 | ISSUE-001（冻结参考项目基线与迁移清单）、ISSUE-004（不可变通道与频域数据模型）、ISSUE-007（处理历史与时域数据模型） | M06 L8「直接依赖：ISSUE-001、004、007」；README.md L94 |
| 依赖状态 | **均已合入 main**：ISSUE-001（`2c5b379`+`6b74dcf`，复审总结 PASS，docs/reports/ISSUE_001_005_REVIEW_SUMMARY.md L17）；ISSUE-004（`45c56572`，复审总结 PASS，同上 L20）；ISSUE-007（`6403e42` feat + `633b762` fix，经 PR #1 `0ddbd81` 合入，见 git log 实测）；三者的代码与测试均可在当前 HEAD 实测（见 3.2） | git log/show 实测（见 3.2）；ISSUE_001_005_REVIEW_SUMMARY.md |
| 功能映射 | FR-011（OSL 校准，PRODUCT_REQUIREMENTS.md）、ADR-0005（钢筋仪为校准/处理主要参考） | M06 L10 |
| 提示词工作区替换 | `E:\wurenjiruanjian` → 工作区根（见第 2 节） | docs/issues/README.md 第 3 节通用协议 |
| 一次一 Issue | 本轮只处理 ISSUE-027；不进入 ISSUE-028（OSL/空采无 UI 参考采集服务，依赖 015/027，顺序在后）；ISSUE-027 完成后停止交人工验收 | docs/issues/README.md 第 1/3 节；团队目标；M06 L40「报告并停止，不 commit/push」 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

与 ISSUE-024/025/026 相同，**E: 盘在本环境不可达**（见 3.5-1）。与 GNSS 专题不同（旧 UAV-GPR `E:\UVA_GPR_system` 在 D: 盘无副本），**钢筋仪项目存在本地只读副本 `D:\博士任务\rebar-inspector`**（可读，见 3.5-2/3.5-3）：其白名单文件内容哈希与 ISSUE-001 manifest 冻结 SHA-256 的对拍结论见第 4 章（**8 个文件逐字节一致**）。冻结哈希权威记录 = `docs/reference-baselines/manifest.md/manifest.json`；t2 按「冻结哈希 + 本地只读副本审计迁移 + 独立合成黄金样本对拍」口径执行（ISSUE-024 先例的精神：内容哈希是可移植事实；参考仓库 dirty worktree 不能当作稳定版本，必须按文件冻结）。

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      main（当前；工作树完全干净，核查门禁前后逐字节一致）
HEAD        f68efff docs(issues): mark ISSUE-026 Done after authorized merge
            完整哈希 f68efff3efbbb0b71609873541f4a07160d442a0
分支关系    HEAD == origin/main == f68efff3…（git rev-list --left-right --count origin/main...HEAD = 0 0）
git status --porcelain=v1 -b
    ## main...origin/main
    （无其他条目——无未跟踪/未提交文件；本基线单落盘后为唯一 ?? 条目）
git diff --check    # clean
```

reflog 顶层仅 commit/merge/checkout 记录、`git reflog | grep -iE "reset|rebase|amend|filter"` **为空**（无破坏性操作迹象，命令输出见第 4 章）；本地历史分支均为已合入 main 后的开发痕迹（与 ISSUE-024/025/026 基线单口径一致），当前分支 `main`，不影响 t2。

### 3.2 直接依赖 ISSUE-001/004/007 的合入证据（main 内实测）

提交链（`git log --oneline` + `git show --stat` 实测）：

| 提交 | 内容 |
|---|---|
| `2c5b379` | `feat(issue-001)…`：ISSUE-001 只读参考基线 manifest 机制（tools/migration/reference_manifest.py、docs/reference-baselines/spec.json+manifest） |
| `6b74dcf` | `fix(issue-001): make reference manifests fail closed on path decoding`（中文路径解码 fail-closed，复审修复） |
| `45c56572` | ISSUE-004 不可变通道/频域数据模型原始提交（复审无修复提交，直接 PASS） |
| `6403e42` | `feat(core): add processing history and time-domain models`（ISSUE-007） |
| `633b762` | `fix(issue-007): enforce processing provenance integrity`（ISSUE-007 复审修复） |
| `0ddbd81` | `Merge pull request #1 from z2362536803/feat/m01-issues-006-007`（ISSUE-006/007 合入 main） |

复审证据：`docs/reports/ISSUE_001_005_REVIEW_SUMMARY.md`（L5 结论 `PASS`；L17 ISSUE-001 PASS；L20 ISSUE-004 PASS；L54 说明 5 个原始提交 + 4 个按 Issue 边界拆分的 fix 提交）。

tracked 交付物（main @ f68efff，实测复现）与 ISSUE-027 复用/依据点：

| 交付物 | 实测事实 | ISSUE-027 复用/依据点 |
|---|---|---|
| `docs/reference-baselines/manifest.md/manifest.json/spec.json` + `tools/migration/reference_manifest.py`（ISSUE-001） | 钢筋仪（`rebar-inspector`）仓库路径 `E:\钢筋仪软件开发`、branch `feat/issue-16-pause-resume`、HEAD `938875234a99b47d78cfec940671005b63e9d15c`、worktree dirty；role=calibration 白名单 7 文件各带冻结 SHA-256（含 `src/rebar_inspector/calibration/osl.py` = `afbc6a73dedf261c223ca2c798adccaba19894bf4627e99fcc94cc38e0e5d3d9`）；排除项含 tests/（黄金样本另行记录） | **冻结哈希权威记录**（manifest.md L39–45/L92）；t2 迁移清单的源 SHA-256 直接取自此处，并须与本地副本实测对拍（第 4 章） |
| `src/uav_gpr/core/channels.py`（ISSUE-004） | `ChannelSpec`（frozen，L14）：有序 channel 绑定（含 S 参数/极化）与唯一性校验 | OSL profile 逐通道绑定的结构先例：S11/S22 各自独立 profile，多通道容器保持有序绑定 |
| `src/uav_gpr/core/frequency.py`（ISSUE-004） | `FrequencySweep`（L209）/`FrequencyScan`（L278）：严格递增有限频率轴、复数 dtype 规范化、数组所有权防御（后改/写权限攻击拒绝） | OSL 标准件/profile 频率轴必须与核心轴规则一致（finite、strictly increasing、no NaN）；DUT 与 profile 轴错配 fail-closed |
| `src/uav_gpr/core/enums.py`（ISSUE-004/007） | `SParameter`（L44–50：`S11="s11"`/`S22="s22"`）；`DataDomain`（L156–169：`FREQUENCY_RAW`…`FREQUENCY_CALIBRATED`…） | profile 显式绑定目标反射 S 参数用冻结枚举；`frequency_calibrated` 域语义已冻结（OSL 后、空采前，见 CALIBRATION.md §5） |
| `src/uav_gpr/core/time_domain.py`（ISSUE-007） | `ProcessingRecord`（L322）/`ProcessingHistory`（L531）/`TimeDomainScan`（L656）；域转换白名单（L94–118）、provenance 连续性校验——输出 `FREQUENCY_CALIBRATED` 必须携带 `calibration_profile_id`（L202–205）、跨记录 profile ID 必须连续（L158–162） | t2 模型若产出/对接 history 语义必须携带 profile ID；ISSUE-027 本身不追加 history（032 的 stage 职责），但 profile ID 是 core 冻结标识，t2 构造 profile 时应生成/携带 `CalibrationProfileId` |
| `src/uav_gpr/core/identifiers.py`（ISSUE-003/005/007） | `CalibrationProfileId`（L132–134：`.rcal` 校准 profile 唯一 ID，稳定标识族） | profile ID 值对象可直接复用（t2 只读消费 core） |
| `src/uav_gpr/core/config.py`（ISSUE-006） | `MissionConfig.calibration_profile_id: CalibrationProfileId \| None`（L273）；`apply_calibration` 要求该字段（L354–359） | 未来接线面已冻结（ISSUE-029/032/052 消费），t2 不改 config |
| `src/uav_gpr/calibration/__init__.py` | 仅占位 docstring「Calibration models and reference-capture services.」；包内无其他模块 | **t2 落点为空位**：新建 `src/uav_gpr/calibration/osl.py` 不触碰既有公共语义 |
| `tests/unit/test_core_frequency.py`、`tests/unit/test_core_time_domain.py`、`tests/unit/test_reference_manifest.py`（ISSUE-001/004/007） | 依赖契约测试在 HEAD 存在；定向回归实测 **114 passed**（第 4 章） | t2 定向回归基线；新测试不得与 core 校验冲突 |

### 3.3 ISSUE-027 为下一个可执行 Issue

- M06 L5–8：ISSUE-027 状态 `Planned`（L7）；`直接依赖：ISSUE-001、004、007`（L8）——三项依赖均已合入 main 且复审 PASS（见 3.2）。
- docs/issues/README.md L94：`027 \| OSL 校准模型与求解器 \| 001, 004, 007`——依赖表无计划冲突；ISSUE-026 已 Done（`f68efff` 状态标记提交），ISSUE-027 为**下一个编号且依赖全绿**的可执行 Issue；后续依赖 027 的 ISSUE-028/029/032/052 均未解锁，一次只执行一个。
- 落点核查：`src/uav_gpr/calibration/` 仅 `__init__.py`（占位 docstring）；`src/uav_gpr/processing/` 仅 `__init__.py`；src 全树无 OSL/calibration 求解实现（grep `osl|calibrat` 仅命中 core 域枚举/标识、storage 序列化字段与 config 挂载面）；`docs/plans/` 无 issue-027 文件；`docs/reports/` 无 `ISSUE_027_*` 文件；`tests/contract/` 无 calibration 测试——**t2 是首个引入 OSL 求解的实现点**。
- M06 里程碑门禁（README.md L56：「黄金样本、provenance、raw 不变与安全回放通过」）首项由本 Issue 起步；ISSUE-027 只做模型与求解器，采集/保存/UI 明确排除。

### 3.4 对 ISSUE-027 有约束的契约要点（读自 M06 L5–40、CALIBRATION.md、DATA_FORMAT.md §8、REFERENCE_MIGRATION.md、ADR-0005、AGENTS.md、ISSUE_REVIEW_STANDARD.md、团队目标）

**ISSUE-027 范围（M06 L13–20 原文 + 提示词 L34–39）**：

1. **不可变模型**——校准 profile/标准件/通道/频率轴不可变模型；profile 显式绑定目标反射 S 参数（S11/S22 各自独立 profile，互不共用误差项）；多通道容器保持有序绑定；构造/查询路径只读。
2. **OSL 复数求解与应用核心数学**——一端口 Open/Short/Load 三项误差模型（measured = D + T·Γ/(1 − Γ·S) 形态；校正 x = m − D, Γ_corr = x/(T + S·x) 类代数）；奇异/退化检测与数值质量指标明确（带噪/奇异/非有限结果明确、不伪造精度）。
3. **参考对拍**——钢筋仪源哈希与黄金向量对拍（明确容差内一致）。
4. **绝不修改 raw**——输入不可变；所有组合返回新对象。

**排除项（M06 L22–24 + 提示词，t2 不得越界）**：不采标准件/不碰硬件、不保存 `.rcal`、不做 UI、不做空采；不做 ISSUE-028 的采集会话状态机、不做 ISSUE-032 的 stage/history 包装、不改 core。

**验收标准（M06 L26–30 原文，t2 不得削弱）**：

1. 理想 OSL 可恢复已知 DUT；带噪/奇异/轴不匹配结果明确。
2. 输入 raw 不变，通道/profile 不可误用。
3. 与冻结参考黄金样本在明确容差内一致。

**其他约束**：

- CALIBRATION.md §3：每反射通道 Open/Short/Load 三标准件；S11/S22 双反射可能形成六物理步骤（本 Issue 只做模型求解；六步状态机属 ISSUE-028）；质量统计与 profile ID 在构建误差模型时产出（L29）。
- CALIBRATION.md §5：固定顺序 `frequency_raw → optional OSL → frequency_calibrated`（**calibrated = OSL 后、空采前**）；不得覆盖 raw；不得对已用相同 profile 校准的数据再次校准（二次校准拒绝属 ISSUE-032 stage 语义，ISSUE-027 模型层面不提供二次应用入口）。
- CALIBRATION.md §6/§9：质量报告至少含标准件求解残差、非有限值等；测试必须覆盖理想 OSL、带噪 OSL、奇异/退化标准件、频率不匹配、双通道顺序、单通道 profile 误用。
- DATA_FORMAT.md §8（`.rcal` 契约，本 Issue 不写文件但字段语义前瞻）：profile/reference ID、完整频率轴、复数编码、质量报告、内容摘要；加载时严格检查通道/S 参数/频率轴/域/算法版本——**t2 模型字段（频率轴原样保存、S 参数绑定、profile ID、质量指标）不得与未来 .rcal schema 冲突**。
- REFERENCE_MIGRATION.md §5/§6/§9：迁移清单模板（branch+HEAD+worktree+SHA256+trusted behavior+excluded behavior+target module+adaptations+golden fixtures+new tests+numeric comparison）；算法迁移先冻结小黄金样本、验证 dtype/shape/axis/边界、差异必须解释、对拍通过后才谈优化。
- ADR-0005：钢筋仪为校准主要参考；每次迁移记录 branch/HEAD/status、源文件 SHA256、目标模块和测试；**两个参考仓库的脏工作树不能被当作稳定版本，必须按文件冻结**。
- AGENTS.md §2.1：迁移前必须记录参考文件路径与内容哈希；迁移是「提取契约并适配」，不是复制整个窗口/巨型模块；§3 单道频域 `channel × frequency`、`frequency_raw` 永不可改、多通道从第一天支持；§9 依赖方向 `calibration → core`，calibration 不依赖 Qt/存储/网络/UI；§12 文档权威顺序（当前任务验收条件 > AGENTS.md > 已接受 ADR > 专题设计文档 > 参考项目代码）。
- ISSUE_REVIEW_STANDARD.md：t3 独立只读复审将核对 t2 完成报告真实性、范围唯一性（inScope 精确路径）、测试复现与黄金对拍证据；t1 本单为范围权威件之一。

### 3.5 发现的事实（非阻塞，供 t2/captain 注意）

1. **E: 盘不可达（既有结论复现）**：`E:\钢筋仪软件开发` 在文件后端实测 `系统找不到指定的路径`（os error 3）；与 ISSUE-024/025/026 基线单「E: 盘未挂载、WSL /mnt/e 为空」口径一致。原始 E: 路径不是本环境可移植事实；**内容哈希才是**。
2. **本地只读副本存在且可读**：`D:\博士任务\rebar-inspector`（= WSL `/mnt/d/博士任务/rebar-inspector`）含 ISSUE-001 manifest 白名单全部 rebar 候选源：`src/rebar_inspector/calibration/osl.py`（765 行，生产实现：三项误差模型、校正代数、不可变模型、防御性拷贝、NaN/Inf 拒绝、逐 profile S 参数绑定 S11/S22、多 capture 复数相干平均、质量指标键含 open/short/load 残差与重复性）、`calibration/{__init__,_base,errors,interpolation,background,dual_reflection}.py`、`core/enums.py` 等，另含 `calibration_reference/osl_calibration.py`（独立参考实现，rebar docstring 声明两者数值对拍达机器精度）、`tests/test_calibration_osl.py`（661 行，合成误差模型 + 理想/带噪/奇异/形状/NaN/不可变测试）与根目录实测数据目录（`*.rcscan` 等，**禁止纳入任何交付物**）。
3. **黄金样本可用性结论**：manifest 明确排除 `tests/`（「迁移时另行记录黄金样本来源」，manifest.md L92）——rebar 测试文件可作**合成误差模型思路的只读参考**，但其数值向量**不可作为拷贝式黄金样本**。t2 口径（建议，与 ISSUE-024 先例一致）：①源文件内容哈希以 manifest 冻结值为准并本地复算对拍（t1 第 4 章提供）；②黄金样本 = t2 在计划文档中记录的、**独立生成**的合成误差模型 + 理想标准件（Open=+1/Short=−1/Load=0）解析期望与/或带噪场景统计口径，配合与参考实现数值对拍（机器精度级容差可作目标，最终容差与记录方式由 t2 计划文档固化）；③任何「无法本地重算」的数字必须显式记录。
4. **rebar 源与 UAV-GPR core 的契约映射**：rebar `core/enums.SParameter` 语义与本项目 `SParameter.S11/S22`（enums.py L44–50）一致；本项目 core 另有冻结的 `CalibrationProfileId`、`DataDomain.FREQUENCY_CALIBRATED`、`ProcessingRecord.calibration_profile_id` 连续性规则——t2 适配时优先消费本项目 core 标识/枚举，不迁移 rebar 的 `_base.py`/frozen 工具（本项目 core channels/frequency 已提供等价不可变/校验能力）。
5. **模块落点**：`src/uav_gpr/calibration/` 为空包（占位 docstring），`tests/contract/test_calibration_osl.py` 为 t2 建议命名（与 M02–M05 契约测试命名一致）；`tools/quality/verify.py` 的 import 门禁当前含 `uav_gpr/uav_gpr.core/uav_gpr.positioning/uav_gpr.storage`（不含 calibration——tools 属 t2 outOfScope，不加改动，t2 完成时另以显式 import 检查 uav_gpr.calibration.osl）。
6. **M01 里程碑文档状态行的历史惯例**：`docs/issues/M01_GOVERNANCE_CORE.md` 内 ISSUE-001/004/007 的状态行仍为 `Planned`（M02–M05 文件是从 ISSUE-008 起才采用「Done + 复审报告链接」状态行惯例）；M01 的完成证据以 3.2 的 git 合入提交 + `ISSUE_001_005_REVIEW_SUMMARY.md` PASS + HEAD 上代码/测试为准（ISSUE-024/026 基线单同口径引用）。t3 复审时请勿因 M01 状态行未更新而误判依赖状态。

## 4. 门禁基线与参考哈希对拍（阶段 3 实测复跑，2026-09-02）

环境：WSL Ubuntu / Python 3.12.3；pytest 8.4.2、ruff 0.16.4、mypy 1.20.2、numpy 2.5.2、h5py 3.16.0；`uav_gpr` editable 可导入（`src/`）；解释器路径 `python3`（与 ISSUE-019～026 基线单同口径）。

```text
$ git rev-parse HEAD origin/main
f68efff3efbbb0b71609873541f4a07160d442a0   # HEAD == origin/main
$ git rev-list --left-right --count origin/main...HEAD
0  0                                        # 与远端同步，无领先/落后
$ git status --porcelain=v1 -b
## main...origin/main
?? docs/reports/ISSUE_027_BASELINE_CONFIRMATION.md   # 唯一新增（本单）
$ git log --oneline | grep -iE 'issue-00[147]|m01|feat/m01|Merge pull'
0ddbd81 Merge pull request #1 from z2362536803/feat/m01-issues-006-007
633b762 fix(issue-007): enforce processing provenance integrity
6b74dcf fix(issue-001): make reference manifests fail closed on path decoding
$ git log --oneline | grep -iE '2c5b379|45c5657|ac531fc|6403e42'   # 补全 ISSUE-001/003/004/007 原始提交
6403e42 feat(core): add processing history and time-domain models   # ISSUE-007
ac531fc feat(core): add identifiers errors and time primitives      # ISSUE-003
2c5b379 chore: freeze reference project baselines                   # ISSUE-001
45c5657 feat(core): add immutable frequency models                  # ISSUE-004
$ git merge-base --is-ancestor 45c565727ccb53ea2b37fe882b86e5fa0f0e6b54 HEAD && echo YES   # ISSUE-004 提交在 main
YES
$ git reflog | grep -iE 'reset|rebase|amend|filter'   # 空——无破坏性操作迹象
$ git diff --check                                    # clean（exit 0）

# —— 参考源哈希对拍（本地只读副本 D:\博士任务\rebar-inspector，WSL /mnt/d/博士任务/rebar-inspector）——
# 副本自身 git 基线：main @ 7c522d2aebe6a835acb969e8012565715f64a238、worktree dirty——
# 与 ISSUE-001 manifest 记录的 E: 仓库状态（feat/issue-16-pause-resume @ 9388752…）不同；
# 白名单文件内容哈希才是可移植事实（reference-baselines/README.md「内容哈希才是可移植事实」）。
$ sha256sum src/rebar_inspector/calibration/{__init__,_base,background,dual_reflection,errors,interpolation,osl}.py src/rebar_inspector/core/enums.py   # 在副本根执行
0b5ed581…91258  calibration/__init__.py        # = manifest.md L39
8cd4fe95…0e8de  calibration/_base.py           # = manifest.md L40
db96c7ba…35c86  calibration/background.py      # = manifest.md L41
6f3725e5…0f70a8  calibration/dual_reflection.py# = manifest.md L42
b6858162…77d47  calibration/errors.py          # = manifest.md L43
9dc1b302…bf3da7  calibration/interpolation.py  # = manifest.md L44
afbc6a73dedf261c223ca2c798adccaba19894bf4627e99fcc94cc38e0e5d3d9  calibration/osl.py   # = manifest.md L45（完整 64 位一致）
08129eb7…66b19  core/enums.py                 # = manifest.md L52
# 结论：本地副本 8 个白名单文件与 ISSUE-001 manifest 冻结 SHA-256 **逐字节一致**；
# 行数实测：calibration/osl.py 765、tests/test_calibration_osl.py 661、calibration_reference/osl_calibration.py 320。
# 原始 E:\钢筋仪软件开发 不可达（glob E:\钢筋仪软件开发 → os error 3 系统找不到指定的路径）。

$ python3 -m pytest tests/unit/test_core_frequency.py tests/unit/test_core_time_domain.py \
    tests/unit/test_reference_manifest.py tests/unit/test_core_enums.py tests/unit/test_core_identifiers.py -q
114 passed in 1.08s                            # 依赖定向（ISSUE-001/004/007 依赖契约回归）

$ python3 tools/quality/verify.py
1055 passed, 4 deselected in 134.87s (0:02:14)  # pytest 门（上轮 ISSUE-026 基线 1016 + 39 matcher 测试 = 1055，与任务契约预期一致）
All checks passed!                              # ruff（check .）
Success: no issues found in 46 source files     # mypy（45 → 46：positioning/matcher.py）
package import ok                               # import 检查
[quality] all gates passed                       # verify.py 全部通过（exit 0 实测捕获）

$ python3 -m pytest -m "not hardware and not slow" -q   # 全量独立复跑（后台，日志 /tmp/gate_pytest_027.log）
1055 passed, 4 deselected in 135.03s (0:02:15)  # PYTEST_EXIT=0 实测捕获
$ python3 -m ruff check .                       # All checks passed!（exit 0）
$ python3 -m mypy src                           # Success: no issues found in 46 source files（exit 0）
$ python3 -c "import importlib; [importlib.import_module(n) for n in ('uav_gpr','uav_gpr.core','uav_gpr.calibration','uav_gpr.processing','uav_gpr.storage','uav_gpr.positioning')]; print('package import ok')"   # exit 0
$ git diff --check                              # clean（exit 0）
```

核查前后 `git status --porcelain=v1 -b` **逐字节一致**（`## main...origin/main` + 唯一 `?? docs/reports/ISSUE_027_BASELINE_CONFIRMATION.md`）；`git diff --check` clean；`.pytest_cache/`/`.mypy_cache/`/`.ruff_cache/` 均 git-ignored，无新缓存/日志/实测数据残留（本基线单为唯一新增未跟踪文件）。门禁基线结论：**1055 passed / 4 deselected（1059 collected）、ruff/mypy（46 文件）/import 全过**，与 ISSUE-026 完成后基线（1016 → +39）吻合；t2 完成后新基线 = 1055 + 新增非硬件测试数，不得出现既有测试失败。

## 5. ISSUE-027 实施计划摘要（供 t2 契约；完整版见 t2 计划文档，不含实现代码）

### 范围（M06 L13–20 原文口径 + 提示词）

1. 新建 `src/uav_gpr/calibration/osl.py`：一端口 OSL 三项误差模型与求解器——不可变标准件（理想 Open=+1/Short=−1/Load=0 + 可选频变复数 Cal Kit 模型）/校准 profile（显式绑定 `SParameter.S11` 或 `S22`，两端口互不共用误差项）/通道/频率轴模型；多通道（capture/道）复数相干平均与有序多通道容器绑定（axis/channel/profile 错配 fail-closed）；复数求解与应用核心数学（校正后 Γ）；奇异/退化检测（如分母过小、标准件线性相关、非有限输入）与数值质量指标（标准件残差/重复性等，形态对齐 CALIBRATION.md §6）；输入 raw 绝不修改、所有组合返回新对象；无 Qt/存储/网络/UI 依赖（calibration → core 方向）。
2. 新建 `tests/contract/test_calibration_osl.py`（失败测试优先，先红灯后绿灯）：理想 DUT 恢复、带噪、奇异/退化、非有限、axis/channel/profile 错配、S11/S22 独立 profile、多通道有序绑定、raw 不可变、参考数值对拍（黄金向量明确容差内一致，来源与数字记录于计划文档）。
3. `docs/plans/2026-09-02-issue-027-osl-calibration.md`（计划文档，t2 先落盘：**迁移清单**（源 branch/HEAD/SHA-256 取自 t1 第 4 章对拍结果、黄金样本来源与哈希、采用/排除行为）、设计决策、失败测试红灯→绿灯执行日志、门禁数字）。
4. `docs/issues/M06_CALIBRATION_PROCESSING.md` 仅 ISSUE-027 状态行（L7）：`Planned → In progress → Review`（`Done` 合入后由负责人标记），勿动其他条目。

### 排除项（M06 L22–24 + 提示词，t2 不得越界）

不采硬件/标准件（无采集会话、无真机）、不写 `.rcal`、不做 UI、不做空采；不做 ISSUE-028 状态机/聚合、不做 ISSUE-032 stage/history 包装与二次校准拒绝语义（模型层不提供应用入口，raw 与 OSL 应用边界由 032 stage 表达）；不改 `core/**`（SParameter/CalibrationProfileId/DataDomain/频率与通道模型只读消费）；不改 `docs/reports/**`、`docs/CALIBRATION.md`、`docs/DATA_FORMAT.md`、`docs/REFERENCE_MIGRATION.md`、`docs/reference-baselines/**`、`docs/adr/**`、`tools/**` 与参考仓库（只读）；不 commit/push/merge、不创建/切换分支；不新增 inScope 之外的文件（若确需拆分先报告暂停）。

### 验收标准（M06 L26–30 原文，t2 不得削弱）

1. 理想 OSL 可恢复已知 DUT；带噪/奇异/轴不匹配结果明确（结构化错误或明确质量指标，不伪造）。
2. 输入 raw 不变；通道/profile 不可误用（错配 fail-closed）。
3. 与冻结参考黄金样本在明确容差内一致（源哈希 + 对拍记录于迁移清单）。

### 测试矩阵要点（提示词必测项 + 验收口径 + 依赖回归）

- 理想 DUT 恢复：无误差系统/合成误差模型下 Open/Short/Load 求解出已知 D、T、S 并校正已知 DUT Γ 到容差内（单/双通道）；
- 带噪：多 capture 复数相干平均降低确定性噪声；噪声场景结果/质量指标明确；
- 奇异/退化：标准件线性相关（如 Open≈Short 或 Load≈Open）、分母过小（对齐 rebar `MIN_DENOMINATOR` 类守卫思路但按本项目契约重写）→ 结构化拒绝或明确数值质量结果；
- 非有限：NaN/Infinity 输入拒绝；
- 错配：axis（频率轴不等长/非严格递增/错频段）、channel 顺序、S 参数绑定（S11 profile 应用到 S22 DUT 等）、profile ID 错配——全部 fail-closed；
- S11/S22 独立 profile：两 profile 各自求解/校正不串扰；有序多通道容器逐通道应用对应 profile；
- raw 不可变：输入数组后改/视图写权限攻击不改变已建模型；求解不修改输入；
- 参考数值对拍：黄金向量（独立合成 + 与本地只读参考实现数值对比，容差明确记录）容差内一致；
- 回归：依赖定向（core frequency/time_domain/manifest）+ 全量 `python3 tools/quality/verify.py` + ruff + mypy + import（含 `uav_gpr.calibration.osl`）+ `git diff --check` + 工作树检查。

### inScope 精确路径（以任务契约 t2 inScope 为准；t2 完成登记 changedPaths 必须与此逐一相等；沿用 ISSUE-018～026 教训：精确文件路径，非 glob）

1. `src/uav_gpr/calibration/osl.py`（新模块）
2. `tests/contract/test_calibration_osl.py`（新文件）
3. `docs/plans/2026-09-02-issue-027-osl-calibration.md`（新计划文档）
4. `docs/issues/M06_CALIBRATION_PROCESSING.md`（仅 ISSUE-027 状态行 L7）

（t1 基线单 `docs/reports/ISSUE_027_BASELINE_CONFIRMATION.md` 为 t1 交付物，不计入 t2 inScope；t2 outOfScope 见上。t2 验证命令按任务契约执行。）

若确需拆分模块/新增文件，t2 必须先停止并向 captain 报告，不得自行新增范围外文件。

## 6. 结论

ISSUE-027 开工基线已锁定：`main`/HEAD @ `f68efff`（工作树完全干净、与 origin/main 同步 0/0、reflog 无破坏性操作）；直接依赖 **ISSUE-001/004/007 均已合入 main**（`2c5b379`+`6b74dcf`、`45c56572`、`6403e42`+`633b762`+`0ddbd81`；ISSUE_001_005_REVIEW_SUMMARY PASS；HEAD 上 `ChannelSpec`/`FrequencySweep`/`FrequencyScan`/`ProcessingRecord`/`ProcessingHistory`/`TimeDomainScan`/`SParameter`/`DataDomain`/`CalibrationProfileId` 代码与依赖测试实测复现）；**ISSUE-027 是当前唯一可执行 Issue**（M06 状态行 `Planned`、calibration 包为空位、无实现/测试/计划/报告存在、依赖全绿）；参考源：**原始 `E:\钢筋仪软件开发` 不可达**，本地只读副本 `D:\博士任务\rebar-inspector` 可读（含 765 行 `calibration/osl.py`、独立参考实现与合成测试），第 4 章实测确认 8 个白名单文件与 ISSUE-001 manifest 冻结 SHA-256 **逐字节一致**（含 `calibration/osl.py` = `afbc6a73…`）；契约要点（不可变标准件/profile/通道/频率轴模型、S11/S22 独立 profile、有序多通道绑定、OSL 复数求解与应用代数、奇异/退化检测与质量指标、raw 不可变、黄金向量容差内对拍、排除采硬件/写 .rcal/UI/空采、精确 inScope 路径建议、core 只读消费与未来 .rcal schema 前瞻）已固化于第 3.4/3.5/5 节；门禁基线实测全绿（**1055 passed / 4 deselected**（1059 collected）、ruff/mypy（46 文件）/import 全过、依赖定向 114 passed、参考哈希 8/8 对拍一致），核查前后 git 状态一致、无残留。**无阻塞项，可开工。** 本确认单即为 t2 实现与 t3 独立复审的权威基线件；t2 按第 5 节契约执行（先落盘计划文档 → 失败测试优先 → 最小实现 → M06 状态行 → 门禁 → 报告），完成后停止，不进入 ISSUE-028。

> 后续记录：本单为开工时点的基线快照，不随 t2 改动；t2 实际完成记录见其完成报告与 `docs/plans/2026-09-02-issue-027-osl-calibration.md`，t3 复审报告独立输出。
