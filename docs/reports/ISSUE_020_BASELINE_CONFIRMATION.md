# ISSUE-020 开工基线确认单（只读核查 + 实施计划摘要）

日期：2026-09-02（ISSUE-020 开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-020-librevna-stream`（执行器 engineer，任务 t1，attempt e6d5e2ab-1b28-4a48-ba68-925160f7e3aa）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支、未修改实现/测试/计划文件。
配套文件：本单为 t2（LibreVNA 包流与严格 sweep 组装器）与 t3（独立复审）的权威基线件；独立复审按 [ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) 执行。t2 完整实施契约见 t2 计划文档（`docs/plans/2026-08-30-issue-020-sweep-assembler.md`，由 t2 先落盘）。

## 1. 锁定的目标 Issue 与依据

**ISSUE-020：LibreVNA 包流与严格 sweep 组装器**（`docs/issues/M04_LIBREVNA.md` 第 2 个条目，状态 `Planned`，L42–77）

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-020（M04_LIBREVNA.md L42–77） | docs/issues/README.md 依赖顺序主表 L87 |
| 直接依赖 | 仅 ISSUE-019（迁移 LibreVNA USB 传输层） | M04 L45「直接依赖：ISSUE-019」；README.md L87 |
| 依赖状态 | **Done**（2026-09-02 经自动化授权合并；M04 L7 状态行实测为 Done，附复审报告链接） | M04 L7；docs/reports/ISSUE_019_REVIEW_REPORT.md（VERDICT=PASS）；git log（见 3.2） |
| 功能映射 | FR-003、`ACQUISITION.md` 第 5 节 | M04 L46 |
| 提示词工作区替换 | `E:\wurenjiruanjian` → 工作区根（见第 2 节） | docs/issues/README.md 第 3 节通用协议 |
| 一次一 Issue | 本轮只处理 ISSUE-020；不进入 ISSUE-021 | docs/issues/README.md 第 1/3 节；团队目标 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

参考项目原始路径 `E:\钢筋仪软件开发`、`E:\UVA_GPR_system` 不在本机挂载范围内；**ISSUE-020 的参考源以 ISSUE-001 manifest 冻结的内容哈希为可移植事实**，本机对拍依据是本地只读副本 `D:\博士任务\rebar-inspector`（WSL `/mnt/d/博士任务/rebar-inspector`；`librevna_protocol.py` 实测 SHA-256 `6a41c4b11cfa8272c1687893a53219ea0465987a378700a0b74ab3973353d8ce` 与 ISSUE-019 迁移记录及 manifest 逐一相等，`tests/test_librevna_protocol.py` 实测 `f3019795c6906ae62479532b755ac73dd375d1452a5e4c5eaca31451a7cef5c7` 与 ISSUE-019 执行日志一致）。

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      main（当前；工作树完全干净）
HEAD        2c3941d  docs(issues): mark ISSUE-019 Done after automated authorized merge
            完整哈希 2c3941d2fb2c025a8e485917aa34b11ee4311028（2026-09-02 01:25:39 +0800）
分支关系    main...origin/main = 0/0（`git rev-parse HEAD` == `git rev-parse origin/main`）
git status --porcelain=v1 -b
    ## main...origin/main
    （无其他条目——无未跟踪/未提交文件，untracked-count=0）
git diff --check    # clean（exit 0）
```

reflog 实测仅 commit/merge/checkout 记录（顶层 `2c3941d commit` ← `042dce6 merge` ← `c0cd067 checkout`），**无 reset/rebase/amend/强推迹象**。

### 3.2 直接依赖 ISSUE-019 的合入证据（main 内实测）

| 提交 | 内容 |
|---|---|
| `c063dce` | `feat(acquisition): migrate LibreVNA USB transport layer (ISSUE-019)` |
| `042dce6` | `Merge feat/issue-019: ISSUE-019 LibreVNA USB transport migration` |
| `2c3941d` | `docs(issues): mark ISSUE-019 Done after automated authorized merge`（当前 HEAD） |

tracked 交付物（main，`git ls-files` 确认）：

| 交付物 | 实测事实 | ISSUE-020 复用/依据点 |
|---|---|---|
| `src/uav_gpr/acquisition/librevna/transport.py`（501 行） | 协议常量（VID `0x1209`/PID `0x4121`/EP_OUT `0x01`/EP_IN `0x81`/HEADER `0x5A`、packet 类型含 `VNA_DATAPOINT=27`）、`MIN_PACKET_LENGTH=8`/`MAX_PACKET_LENGTH=4096`、`crc32`/`encode_packet`/`Packet`/`PacketStream`、`LibreVnaTransportError` 家族（8 个子类，`DomainError`+`ErrorCode.INVALID_ARGUMENT`+类级 `_reason`）、`UsbAdapter` Protocol、`PyUsbAdapter`（惰性加载 pyusb）、`LibreVnaUsbTransport`（幂等/cancel） | **ISSUE-020 直接消费**：`PacketStream.feed` 已产出帧级 `Packet(packet_type, payload)`（含 VNA_DATAPOINT 载荷），ISSUE-020 在其上解析 VNADatapoint payload 并严格组装 sweep；帧/CRC/长度上限/噪声处理契约见 3.4 |
| `tests/contract/test_librevna_transport.py`（668 行，**50 测试**） | 实测 `50 passed in 0.48s`；黄金向量（ACK `5a080007c1f48315`、REQ_DEV_INFO `5a08000ff37c581b`、SET_IDLE `5a0800141fb53d91`、`crc32(b"123456789")=0xCBF43926`）、framing 11 用例、会话/取消/adapter 失败映射/资源释放/惰性加载 | 测试范式（fake adapter / fake-usb importlib 注入、子进程惰性加载断言）与黄金向量沿用；ISSUE-020 新增测试不得 import `usb`/`serial`/网络根（AST 守卫，见 3.4） |
| `docs/plans/2026-08-30-issue-019-librevna-transport.md`（199 行） | 迁移清单（REFERENCE_MIGRATION.md §5 模板 15 字段）、设计决策 D1–D8、执行日志（792 passed 基线） | 迁移记录口径；VNADatapoint 解析与 S11 计算被 ISSUE-019 明确排除并指向 **ISSUE-020**（§4 excluded behavior） |
| `docs/reports/ISSUE_019_BASELINE_CONFIRMATION.md` / `ISSUE_019_REVIEW_REPORT.md` | 复审 VERDICT=PASS（10/10 验收 PASS，0 P0/P1/P2，仅 4 个 P3）；合并建议落实于 `2c3941d` | 本单结构模板；P3 观察项（见 3.4 约束 9/10） |
| `docs/issues/M04_LIBREVNA.md` L7 | 状态行实测：`ISSUE-019 … 状态：Done（2026-09-02 独立复审 VERDICT=PASS 后经项目负责人自动化授权合并…）` | ISSUE-020 直接依赖已完成 |

### 3.3 ISSUE-020 为下一个可执行 Issue

- M04 L42–77：ISSUE-020 状态 `Planned`；L44 `直接依赖：ISSUE-019`（唯一依赖，已 Done）。
- docs/issues/README.md L87：`020 | LibreVNA 包流与严格 sweep 组装器 | 019`——依赖表无计划冲突；M04 其余条目（021/022/023）均 `Planned` 且依赖 ISSUE-020，**ISSUE-020 是 M04 当前唯一可执行项**。
- 落点核查：`docs/plans/` 与 `docs/reports/` 无任何 ISSUE-020 文件（grep `020` 仅命中 M04 与 README 的 Issue 定义）；`src/uav_gpr/acquisition/librevna/` 仅 `__init__.py` 占位 + `transport.py`，无 assembler/stream 实现——t2 交付物将是唯一新改动。

### 3.4 对 ISSUE-020 有约束的契约要点（读自 ACQUISITION.md §5、M04 L48–64、ISSUE-019 迁移记录/复审报告、ISSUE_REVIEW_STANDARD.md、AGENTS.md）

**ISSUE-020 范围（M04 L50–56）+ 提示词**：

1. 增量 packet stream：粘包、拆包、噪声/损坏同步策略和上限——帧层 `PacketStream`（ISSUE-019）已具备；ISSUE-020 在其上定义 datapoint 级流/组装语义，任意 byte chunking 必须得到同一包序列（生成式切分测试）。
2. sweep 边界、点索引、重复/缺失/乱序、通道/receiver 字段——VNADatapoint payload 布局（参考 `librevna_protocol.py` 实测，哈希见第 2 节）：头 `<QhH` = frequency(8,u64) + cdbm(2,i16) + point_number(2,u16)，头长 12；每接收机分组 `real(4,f32)+imag(4,f32)+desc(1)` = 9 字节；desc 位掩码（来源 Device_protocol_v13.tex）：bits7–5=stage（0=端口 1 激励，1=端口 2 激励）、bit4=reference、bit3..0=Port4..Port1；**payload 内无显式 sweep 序号字段**——sweep 边界判定（如 point_number 回绕/重置）与点序校验是 t2 设计决策，须用契约测试固定语义。
3. reference 分母、有限值、超时和丢弃统计——reference 幅度为 0、必需 receiver 缺失/重复、S 值实/虚部非有限均判无效（参考 `parse_s11_point`/`parse_s11_s22_point` 语义：缺失/重复**不静默采用首/末**）；丢弃统计是显式输出。
4. 只在完整一致时输出**中间 assembled sweep**（不含最终 S11/S22 backend metadata——ISSUE-021）。

**排除项（M04 L58–60 + 提示词）**：不配置设备（不发 SweepSettings/DEVICE_INFO 等控制包）、不计算最终 S11/S22 backend metadata、不零填缺点（禁止零填或部分道输出）；不实现 backend（ISSUE-021）、不进入 ISSUE-021。

**验收标准（M04 L62–66 原文，t2 不得削弱）**：

1. 任意 byte chunking 得到同一包序列。
2. 缺点/重复/跨 sweep/坏 CRC/非法分母均不产出假完整 sweep。
3. 有界缓存，恶意长度不能分配无限内存。

**其他约束**：

4. `ACQUISITION.md` §5（sweep 完整性）逐项：帧 CRC/消息长度/协议类型（ISSUE-019 帧层已验）、sweep 序号/边界、频点索引范围/重复/缺失、每通道频点数和顺序、reference 分母有效性、非有限值和异常设备状态；「超时或缺点的 sweep 不能用零填充后冒充完整道；可以记录失败统计，但 `trace_index` 只在完整 sweep 被任务接受时分配」——ISSUE-020 只产出完整一致 sweep 与丢弃统计，不分配 trace_index（那是 ISSUE-021 backend 职责）。
5. 帧层契约继承（ISSUE-019 迁移记录 + P3 观察）：严格长度上限 8..4096 已内建于 `PacketStream`；`VNA_DATAPOINT`（type 27）**跳过 CRC 校验是参考既有协议行为，不得"修复"**——组装器必须显式承接该语义（完整性靠结构校验/点序校验兜底）；`encode_packet` 无调用侧长度上限（P3-1，ISSUE-020 不发送控制包故不触发，若 t2 确需发送须调用侧校验并先报告）。
6. 有界缓存（验收 3 + P3-4）：`PacketStream` 噪声丢弃逐字节 `del buffer[0]` 最坏 O(n²)；组装器侧缓存（半 sweep 缓冲）必须按预期点数有界，恶意/无限字节流不得导致无限内存分配；t2 可评估噪声丢弃索引指针硬化（P3-4 建议，非强制）。
7. 结构化错误：沿用 transport.py 既有模式（`DomainError` + `ErrorCode.INVALID_ARGUMENT` + 类级 `_reason` + 类型化子类，core `ErrorCode` 枚举只读）；sweep 组装/解析错误与丢弃统计区分开（丢弃是统计不是异常）。
8. 默认测试不得枚举真机：`tests/unit/test_no_external_access.py` AST 守卫（禁 `serial/usb/socket/requests/urllib/http/websocket(s)` 根导入）——新测试文件不得 import 真 pyusb；无硬件，全部经合成字节流/黄金夹具。
9. AGENTS.md §10：每能力覆盖正常/错误/取消/恢复路径；禁删测试/降断言/吞异常；禁固定 `sleep` 猜时序（事件/标志驱动）。依赖方向：acquisition → core 合规；不新增依赖（numpy 已在）。
10. 黄金夹具来源（只读，tests/** 不在 manifest 白名单，provenance 须在 t2 计划记录）：参考 `tests/test_librevna_protocol.py`（`f3019795…`，含 VNADatapoint 解析用例与固定向量）与 `tests/test_librevna_usb_transport.py`（`6ee3f7a6…`，fake-usb 注入范式）；参考 `parse_vna_datapoint`/`parse_s11_point`/`parse_s11_s22_point` 为行为 oracle（哈希见第 2 节）。
11. 本 Issue 为纯协议组装：不落盘、不联网、不改变强制数据规则/空地职责/持久化语义，**无需新增 ADR**（ADR-0005 reference authority 适用）。

### 3.5 发现的事实（非阻塞，供 t2 注意）

1. 工作树完全干净且与远端同步（`## main...origin/main` 0/0）；t2 交付物将是唯一新改动。
2. 落点为空：`src/uav_gpr/acquisition/librevna/` 仅占位 `__init__.py` + `transport.py`；无 assembler/stream 实现、无 020 计划文档、无 020 报告——t2 新增组装器模块与契约测试（精确路径建议见第 5 节）。
3. 帧层 `PacketStream` 已交付并测试（50 测试全绿）——ISSUE-020 的「增量 packet stream」是**消费**该帧层并增加 datapoint 级语义，不重写帧解析；「同一包序列」验收以 `PacketStream` 输出为准。
4. VNADatapoint payload 无显式 sweep 序号：sweep 边界/点序语义须由 t2 契约测试固定（如 point_number 0 起始 + 连续递增 + 预期点数，越界/回跳即跨 sweep 或乱序）。
5. 丢弃统计须显式：t2 应设计可观测的丢弃/拒绝计数（坏 CRC、坏结构、分母、非有限、重复、乱序、跨 sweep、超时），供 ISSUE-021 backend 消费。
6. 「只在完整一致时输出」包含**完整一致先于输出**的语义：半 sweep 必须缓冲（有界），不得边收边出。

## 4. 门禁基线（核查时实测复跑，2026-09-02）

环境：WSL Ubuntu / Python 3.12.3；numpy 2.5.2、pytest 8.4.2、h5py 3.16.0、ruff 0.16.4、mypy 1.20.2；`uav_gpr` editable 可导入（`src/`）；解释器路径 `python3`（与 ISSUE-018/019 基线单同口径）。

```text
$ python3 -m pytest tests/contract/test_librevna_transport.py -q
50 passed in 0.48s                            # ISSUE-019 定向（直接依赖）

$ python3 -m pytest tests/unit/test_reference_manifest.py tests/contract/test_acquisition_backend.py -q
41 passed in 0.95s                            # 依赖回归（ISSUE-001：13 + ISSUE-015：28）

$ python3 tools/quality/verify.py
792 passed, 1 deselected in 133.70s (0:02:13) # 全量非硬件 pytest（预期 792 达成）
All checks passed!                            # ruff（check .）
Success: no issues found in 40 source files   # mypy
package import ok                             # import 检查
[quality] all gates passed
VERIFY_EXIT=0                                 # verify.py 仅在全部通过时 exit 0

$ python3 -m ruff check src tests              # 补充显式复跑
All checks passed!
$ python3 -m mypy src                          # 补充显式复跑
Success: no issues found in 40 source files
```

核查前后 `git status --porcelain=v1 -b` **逐字节一致**（仅 `## main...origin/main` 一行；untracked-count=0）；`git diff --check` clean；`.pytest_cache/`/`.mypy_cache/`/`.ruff_cache/` 均 `git check-ignore` 确认已忽略，无新缓存/日志/实测数据残留。

## 5. ISSUE-020 实施计划摘要（供 t2 契约；完整版见 t2 计划文档，不含实现代码）

### 范围（M04 L50–56 原文口径 + 提示词）

1. 新模块（建议单一模块 `src/uav_gpr/acquisition/librevna/assembler.py`，t2 契约 inScope 为准）：消费 `transport.PacketStream`/`Packet`，实现 VNADatapoint payload 解析（头 `<QhH` + 9 字节接收机组，desc 位掩码 stage/reference/port）与 `StrictSweepAssembler`——sweep 边界判定、点索引范围/重复/缺失/乱序/跨 sweep 检测、通道/receiver 字段校验（每通道频点数和顺序）、reference 分母（幅度 0/缺失/重复拒绝）与非有限值校验、超时与丢弃统计（显式计数）、有界半 sweep 缓存（按预期点数上限，恶意长度不分配无限内存）、只在完整一致时输出中间 assembled sweep。
2. 生成式 chunk 切分测试：任意 byte chunking（含 1 字节粒度、随机切分、跨包/跨点/跨 sweep 边界）得到同一包序列/同一 assembled sweep。
3. 结构化错误沿用 `LibreVnaTransportError` 家族模式（新错误类型若需扩展，按 transport.py 既有先例定义在组装器模块内或与 captain 确认落点）；丢弃走统计不走异常。
4. 黄金夹具：参考固定向量 + `parse_*` 行为 oracle（provenance 记录路径 + 读取时点哈希于 t2 计划）。

### 排除项（M04 L58–60 + 提示词，t2 不得越界）

不配置设备（不发送控制包）、不计算最终 S11/S22 backend metadata（ISSUE-021）、不零填缺点/不部分道输出、不实现 backend、不改 transport.py 已冻结帧层契约（只消费）、不改 core/、不改参考仓库、不 commit/push/merge、不创建/切换分支、不进入 ISSUE-021。

### 验收标准（M04 L62–66 原文，t2 不得削弱）

1. 任意 byte chunking 得到同一包序列（生成式切分测试覆盖）。
2. 缺点/重复/跨 sweep/坏 CRC/非法分母均不产出假完整 sweep（丢弃统计断言 + 无输出断言）。
3. 有界缓存：恶意长度/无限流不分配无限内存（长度上限 + 预期点数上限断言）。

### 测试矩阵要点（提示词必测项 + 依赖回归）

- VNADatapoint 解析黄金对拍（固定向量、多接收机、desc 位掩码语义、截断 payload 拒绝）；
- 生成式 chunk 切分（1 字节粒度/随机切分/跨包跨点跨 sweep）→ 同一包序列；
- 严格组装：完整 sweep 输出；缺点、重复点、乱序、跨 sweep 混入、坏 CRC（非 datapoint 帧层已丢，datapoint 结构坏）、reference 分母 0、非有限值 → 不产出假完整 sweep + 丢弃统计正确；
- 有界缓存：恶意长度（帧层 8..4096 已拒，组装层预期点数上限）、无限半 sweep 流内存有界；
- 超时/半 sweep 滞留：超时产生统计/结构化错误，无部分道输出；
- 回归：ISSUE-019 定向 50 passed、依赖 41 passed、全量 verify.py 792 passed/1 deselected 基线、ruff、mypy、import、`git diff --check`、工作树检查；测试禁固定 sleep、不 import usb 根（AST 守卫）。

### inScope 精确路径建议（以任务契约 t2 inScope 为准；t2 完成登记 changedPaths 必须与此逐一相等；沿用 ISSUE-014～019 教训：精确文件路径，非 glob）

1. `src/uav_gpr/acquisition/librevna/assembler.py`（新模块：VNADatapoint 解析 + StrictSweepAssembler + 丢弃统计 + 结构化错误）
2. `tests/contract/test_librevna_assembler.py`（新测试文件，失败测试优先）
3. `docs/plans/2026-08-30-issue-020-sweep-assembler.md`（计划文档，t2 先落盘，含迁移/夹具 provenance、设计决策、执行日志、门禁数字）
4. `docs/issues/M04_LIBREVNA.md`（仅 ISSUE-020 状态行：`Planned → In progress → Review`，勿动其他条目）

（t1 基线单 `docs/reports/ISSUE_020_BASELINE_CONFIRMATION.md` 为 t1 交付物，不计入 t2 inScope；t2 outOfScope：`docs/reports/**`、`docs/REFERENCE_MIGRATION.md`、`docs/ACQUISITION.md`、`docs/reference-baselines/**`、`docs/TESTING.md`、`docs/adr/**`、`tools/**`、参考仓库（只读）、`src/uav_gpr/core/**`（只读消费）、`src/uav_gpr/acquisition/backend.py`（只读消费）、`src/uav_gpr/acquisition/librevna/transport.py`（只读消费）。）

t2 验证命令按任务契约执行：`python3 -m pytest tests/contract/test_librevna_assembler.py -q`（定向，先红灯后绿灯）、`python3 tools/quality/verify.py`（全量）、`python3 -m ruff check src tests`、`python3 -m mypy src`、`git diff --check && git status --porcelain=v1 -b`（同 ISSUE-018/019 口径，WSL 侧 `python3`）。

若确需拆分模块/新增文件，t2 必须先停止并向 captain 报告，不得自行新增范围外文件。

## 6. 结论

ISSUE-020 开工基线已锁定：`main`/HEAD @ `2c3941d`（工作树完全干净、与 origin/main 同步 0/0、reflog 无破坏性操作）；唯一直接依赖 ISSUE-019 已 **Done**（`c063dce`+`042dce6` 合入、`2c3941d` 标记 Done，复审 VERDICT=PASS；transport.py/50 测试/迁移记录/复审报告全部 tracked 并实测复现，M04 L7 状态行 Done）；**ISSUE-020 是 M04 当前唯一可执行 Issue**（状态 `Planned`、无实现/测试/计划存在、依赖全绿）；契约要点（帧层继承：长度上限 8..4096、VNA_DATAPOINT 跳 CRC 不"修复"、噪声/损坏同步；VNADatapoint payload 布局与 desc 位掩码、reference 分母/有限值校验语义；有界缓存防恶意长度；禁止零填/部分道输出；任意 byte chunking 同一序列；结构化错误模式；AST 守卫；黄金夹具来源与哈希）已固化于第 3.4/5 节；门禁基线全绿（全量 792 passed / 1 deselected、ruff/mypy(40 文件)/import 全过、依赖定向 50+41 passed），核查前后 git 状态一致、无残留。**无阻塞项，可开工。** 本确认单即为 t2 实现与 t3 独立复审的权威基线件；t2 按第 5 节契约执行（先落盘计划文档 → 失败测试优先 → 最小实现 → M04 状态行 → 门禁 → 报告），完成后停止，不进入 ISSUE-021。

> 后续记录：本单为开工时点的基线快照，不随 t2 改动；t2 实际完成记录见其完成报告与 `docs/plans/2026-08-30-issue-020-sweep-assembler.md`，t3 复审报告独立输出。
