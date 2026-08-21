# M01：工程治理与共享核心（ISSUE-001～007）

本里程碑先固定可复现来源、质量门禁和不可变领域语言。未完成本里程碑，不允许开发 HDF5、USB、网络或 UI 业务。

## ISSUE-001：冻结参考项目基线与迁移清单

- 状态：Planned
- 直接依赖：无
- 映射：ADR-0005、`REFERENCE_MIGRATION.md`

### 目标

建立只读、可复现的参考基线清单和生成工具，使之后每次从钢筋仪/UAV-GPR 迁移都能精确指向 branch、HEAD、工作树状态和源文件 SHA256。

### 范围

- 增加 `docs/reference-baselines/` 说明和首份基线 manifest。
- 增加只读快照/哈希工具，输出稳定 JSON/Markdown；不复制大数据或整个仓库。
- 钢筋仪列入 core、LibreVNA、校准、处理、storage、UI 候选源；UAV-GPR 只列 GNSS 与 HM30 文档。
- 记录脏工作树事实、来源时间和明确禁止迁移项。

### 排除项

- 不迁移任何生产代码，不修改两个参考目录，不创建 Git commit。

### 验收标准

- 连续运行工具对未改变文件产生相同哈希/排序。
- manifest 能区分已提交与未提交源，路径缺失时 fail-closed。
- 自动测试使用临时合成仓库，不依赖参考目录写权限。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-001。先完整阅读 AGENTS.md、docs/issues/README.md、docs/REFERENCE_MIGRATION.md 和 docs/adr/0005-reference-authority.md，检查 git status。

建立可复现的参考基线机制：在 tools/migration 下实现只读 manifest/哈希工具，在 docs/reference-baselines 下写格式说明并生成一份当前钢筋仪与 UAV-GPR 的基线记录。只记录实际使用候选文件、branch、HEAD、worktree status、SHA256、来源角色和排除项；路径不存在或读取失败必须明确报错。不得修改、格式化、提交或复制两个参考项目，不得开始代码迁移。

为排序、哈希稳定性、脏状态和缺失路径写合成测试；运行相关测试、全部非硬件测试、Ruff、mypy，并检查参考目录未被改动。完成后按通用协议报告并停止。不要执行 ISSUE-002；不要 commit/push，除非调用者明确授权。
```

## ISSUE-002：建立本地质量门禁与测试基础设施

- 状态：Planned
- 直接依赖：无
- 映射：`TESTING.md`、`CONTRIBUTING.md`

### 目标

让后续 Issue 有统一、可重复的 Python 3.12 测试、静态检查、Qt offscreen 和硬件 opt-in 基线。

### 范围

- 完善 pytest markers、共享 fixture、虚拟时钟/临时目录基础设施。
- 增加本地一键验证入口，依次运行非硬件测试、Ruff、mypy 和包导入检查。
- 硬件测试默认跳过且必须显式设备 opt-in；禁止自动连真机。
- 固定测试随机 seed、时区和 Qt offscreen 环境策略。

### 排除项

- 不配置特定云 CI 平台，不实现产品模型，不为了“绿灯”加入空洞测试。

### 验收标准

- 全新环境按 README 命令可执行质量门禁。
- 默认测试不会访问 USB、串口、网络外部地址或参考仓库。
- 失败步骤返回非零并保留清晰输出。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-002。阅读 AGENTS.md、docs/issues/README.md、docs/TESTING.md、CONTRIBUTING.md 和 pyproject.toml；先检查 git status 与 Python 版本。

实现本地质量门禁与测试基础设施：完善 pytest 配置/markers、tests/conftest.py、确定性随机与虚拟时钟基础 fixture、Qt offscreen 策略，并提供一个 Windows 可运行的一键验证入口，覆盖非硬件 pytest、Ruff、mypy 和包导入。硬件测试必须双重 opt-in，默认不得枚举或连接设备。不要接入 GitHub/GitLab 等远程 CI，不实现任何产品功能。

用测试证明默认运行不会访问真实 USB/串口/外网，失败会返回非零。运行门禁自身并记录命令与结果；不得通过降低 pyproject 严格度换取通过。完成后报告并停止，不执行 ISSUE-003，不 commit/push。
```

## ISSUE-003：稳定 ID、枚举、结构化错误与时间工具

- 状态：Planned
- 直接依赖：ISSUE-002
- 映射：`DATA_MODEL.md` 第 1、2、10 节

### 目标

建立不依赖 Qt/硬件/文件的核心标识、稳定枚举、结构化错误以及 UTC/单调时钟值对象。

### 范围

- UUID 型 mission/trace/device/file/command/reference ID，规范解析与字符串化。
- 稳定字符串枚举：角色、S 参数、逻辑极化、任务终态、位置/错误状态等基础集合。
- 结构化错误码、上下文与可安全展示消息，不靠中文字符串分支。
- timezone-aware UTC 工具和明确的 monotonic ns 值；提供可注入 Clock 协议。

### 排除项

- 不实现任务配置、数组模型、协议消息或线程。

### 验收标准

- ID 往返稳定，拒绝非规范/错误类型；枚举持久值与成员顺序无关。
- naive datetime 被拒；UTC 和单调值不混算。
- core 只依赖标准库（本 Issue 不需要 NumPy）。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-003。确认 ISSUE-002 的质量门禁已存在并通过；阅读 AGENTS.md、docs/issues/README.md、docs/DATA_MODEL.md 和 docs/ARCHITECTURE.md。

在 src/uav_gpr/core 中实现稳定 UUID 标识、基础字符串枚举、结构化领域错误和可注入 UTC/monotonic Clock 契约。对象应不可变、可 JSON 表达、严格拒绝 naive datetime、错误 UUID 和混用时间域；业务分支不得依赖中文异常文本。不要实现 MissionConfig、NumPy 数据数组、transport 或 UI。

先写单元测试，覆盖生成/解析/往返、错误输入、UTC 边界、虚拟 clock 和错误上下文序列化；运行全部门禁。检查 core 没有 Qt/h5py/serial/USB 依赖。完成后报告并停止，不执行 ISSUE-004，不 commit/push。
```

## ISSUE-004：不可变通道与频域数据模型

- 状态：Planned
- 直接依赖：ISSUE-003
- 映射：FR-003、`DATA_MODEL.md` 第 3、5 节

### 目标

实现从第一天支持多通道的不可变 `ChannelSpec`、`FrequencySweep` 和 `FrequencyScan`。

### 范围

- 有序 channel ID/极化/S 参数绑定和唯一性校验。
- 单道 `channel × frequency`、连续 `trace × channel × frequency`。
- 严格递增有限频率轴、复数 dtype 规范和不可恢复写权限的数组封装。
- 追加/堆叠采用新对象，不修改输入；明确内存拷贝所有权。

### 排除项

- 不实现 TraceMetadata、采集 backend、HDF5 或处理算法。

### 验收标准

- shape/channel/frequency/dtype 不匹配均有结构化错误。
- 构造后修改输入数组不会改变模型；外部 view 不能重新设为可写后篡改底层。
- 单通道与双通道使用同一结构，无特殊字段分叉。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-004。确认 ISSUE-003 完成；阅读 AGENTS.md、docs/issues/README.md、docs/DATA_MODEL.md、docs/ARCHITECTURE.md。

在 core 中实现不可变 ChannelSpec、FrequencySweep、FrequencyScan 及必要的只读 NumPy 边界。严格固定 single sweep=channel×frequency、scan=trace×channel×frequency，通道有序且唯一，频率轴有限/严格递增，数据为规范复数 dtype。模型必须拥有自己的不可变数据，不能让调用方通过原数组、property view 或 setflags 篡改；所有组合返回新对象。不要加入采集、处理、HDF5 或 UI。

测试正常单/双通道、错误 shape、重复通道、非递增/NaN 轴、错误 dtype、输入数组后改和 view 写权限攻击。运行全部门禁并报告，停止在本 Issue，不 commit/push。
```

## ISSUE-005：GNSS、道元数据与质量状态模型

- 状态：Planned
- 直接依赖：ISSUE-003、004
- 映射：FR-006、`DATA_MODEL.md` 第 6、7、9 节

### 目标

建立 `GnssFix`、`GnssMatch`、`TraceMetadata` 和质量标志的不可变领域契约，明确无位置、stale 与不同时间域。

### 范围

- GNSS 接收/NMEA UTC、单调时间、WGS84、MSL、fix/sats/HDOP/速度/航向。
- fix 有效性和匹配不可用原因；无 fix 不生成 0 坐标。
- trace ID/index、sweep 三时刻、目标/实际间隔、调度误差、连接代数和 raw hash 字段契约。
- acquired 到 integrity-attached 使用复制生成新对象，不后改冻结实例。

### 排除项

- 不解析 NMEA、不读取串口、不实现匹配算法或哈希算法。

### 验收标准

- 纬经度/HDOP/sats/时间顺序等严格校验。
- 首道允许缺实际间隔；GNSS 缺失/stale 是显式状态。
- MSL 与 AGL 字段不可混用，naive datetime 被拒。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-005。确认 ISSUE-003/004 已完成；阅读 AGENTS.md、docs/issues/README.md、docs/DATA_MODEL.md 和 docs/GNSS.md。

实现不可变 GnssFix、GnssMatch、TraceMetadata 与质量状态模型。分别保存 receive UTC、NMEA UTC、monotonic、WGS84、MSL、fix/sats/HDOP/速度/航向和 match age/reason；无 GNSS 必须是空值+原因，不能用 0 或旧 fix 伪装。TraceMetadata 包含任务/道身份、sweep start/mid/end 的 UTC+monotonic、间隔/误差、连接代数和可验证的 raw hash 字段；需要后附完整性信息时返回新对象。

不要实现 NMEA parser、串口、匹配或哈希。测试范围/时间顺序、首道缺失值、stale/no-fix、MSL 语义和 JSON 往返。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-006：MissionConfig、时窗推导与配置摘要

- 状态：Planned
- 直接依赖：ISSUE-003、004、005
- 映射：FR-002、004、005

### 目标

实现可冻结、可规范化摘要的任务配置，并严格区分物理无模糊时窗与显示裁剪时窗。

### 范围

- 扫频起止/点数/IFBW/功率、通道、道数/连续、目标间隔、GNSS 策略和参考 ID。
- 频率步进与 `1/Δf` 物理时窗推导；显示时窗必须在物理范围内。
- requested/applied config 和字段级 diff 值对象。
- canonical JSON 与 SHA256 config digest；字段单位稳定。

### 排除项

- 不配置 LibreVNA、不做 UI、不判断真机最小间隔。

### 验收标准

- 等价配置产生同一 digest，字段或通道顺序变化按契约反映。
- 非均匀轴、非法 IFBW/间隔/显示时窗得到明确错误。
- 任务配置不可修改，关键改变只能产生新对象/新任务。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-006。确认 ISSUE-003～005 完成；阅读 AGENTS.md、docs/issues/README.md、docs/DATA_MODEL.md、docs/ACQUISITION.md 和 docs/PRODUCT_SPECIFICATIONS.md。

实现不可变 MissionConfig、requested/applied config 与字段级 diff、规范 JSON 和 config SHA256。配置覆盖扫频、通道、IFBW/功率、道数/连续、目标间隔、GNSS 策略、参考 ID；从均匀频率轴推导 Δf 和 physical window=1/Δf，并把 display crop 作为独立且不得越界的值。所有持久字段使用 Hz/s/m 等规定单位。

不要连接硬件、实现 UI 或声称已确定最小采集间隔。测试 digest 确定性、通道顺序、单位/范围、非均匀轴、时窗边界和不可变性。运行门禁并报告，停止，不 commit/push。
```

## ISSUE-007：处理历史与时域数据模型

- 状态：Planned
- 直接依赖：ISSUE-003、004
- 映射：FR-012、`DATA_MODEL.md` 第 8 节

### 目标

建立处理 stage provenance、频域数据域和 `TimeDomainScan` 的不可变模型，为算法与 storage 提供稳定契约。

### 范围

- `ProcessingRecord`、有序不可变 history、stage name/version/params/input/output domain。
- raw/calibrated/background-applied 等频域域标识和 profile/reference 关联。
- `TimeDomainScan` 的 `trace × channel × time`、time_base/time_processed、严格时间轴。
- history 参数 JSON 安全性和追加复制。

### 排除项

- 不实现任何校准或处理数学，不写文件。

### 验收标准

- 非 JSON 参数、错误历史顺序/域、shape/axis 不匹配被拒。
- history 追加不修改旧对象；time 数据具备与频域相同的强不可变性。
- 深度字段不进入未标定模型。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-007。确认 ISSUE-003/004 完成；阅读 AGENTS.md、docs/issues/README.md、docs/DATA_MODEL.md、docs/PROCESSING.md。

在 core 中实现 ProcessingRecord、不可变 ProcessingHistory、频域数据域/provenance 值对象和 TimeDomainScan。时间数据固定 trace×channel×time，time axis 严格递增，kind 明确区分 time_base/time_processed；stage 参数必须规范 JSON，history 追加返回新对象并保留 profile/reference ID。不要实现带通、IFFT、OSL、背景或深度模型。

测试不可变性、shape/axis、非 JSON 参数、域转换、history 追加/往返和禁止未标定深度。运行门禁并报告，停止，不 commit/push。
```
