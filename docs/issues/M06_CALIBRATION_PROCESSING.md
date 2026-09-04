# M06：校准与处理（ISSUE-027～036）

本里程碑以钢筋仪算法和黄金样本为主参考。各 stage 保持独立，任何优化前先保证数值、数据域和 provenance 对拍。

## ISSUE-027：OSL 校准模型与求解器

- 状态：Done（2026-09-02 独立复审 VERDICT=PASS 后经项目负责人授权合并，见 [docs/reports/ISSUE_027_REVIEW_REPORT.md](../reports/ISSUE_027_REVIEW_REPORT.md)；3 项 P3 非阻塞挂账）
- 直接依赖：ISSUE-001、004、007
- 映射：FR-011、ADR-0005

### 目标

迁移并验证一端口 Open/Short/Load 三项误差模型，支持按反射通道独立求解且不依赖 UI/硬件。

### 范围

- 校准 profile/标准件/通道/频率轴不可变模型。
- OSL 复数求解、应用核心数学、退化/奇异检测和数值质量指标。
- S11/S22 分别建 profile；多通道容器保持有序绑定。
- 钢筋仪源哈希与黄金向量对拍。

### 排除项

- 不采标准件、不保存 `.rcal`、不做 UI 或空采。

### 验收标准

- 理想 OSL 可恢复已知 DUT；带噪/奇异/轴不匹配结果明确。
- 输入 raw 不变，通道/profile 不可误用。
- 与冻结参考黄金样本在明确容差内一致。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-027。确认 ISSUE-001/004/007 完成；阅读 AGENTS.md、docs/issues/README.md、docs/CALIBRATION.md、docs/REFERENCE_MIGRATION.md、ADR-0005。

按 baseline 只从钢筋仪项目迁移一端口 OSL 三项误差模型：先记录源文件 SHA256/黄金样本/采用与排除内容；实现不可变标准件/profile/通道/频率模型、复数求解和应用数学、奇异/退化检测及质量指标。S11/S22 各自独立 profile，可由有序多通道容器组合；绝不修改 raw。

不要采硬件、写 .rcal、做 UI/空采。测试理想 DUT 恢复、带噪、奇异、非有限、axis/channel/profile 错配和参考数值对拍。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-028：OSL/空采无 UI 参考采集服务

- 状态：Done（2026-09-02 团队复审 VERDICT=PASS + 第二意见独立复审 5 项发现全部关闭后经项目负责人授权合并，见 [docs/reports/ISSUE_028_REVIEW_REPORT.md](../reports/ISSUE_028_REVIEW_REPORT.md)；修复批次见计划 §7 修复 8-12）
- 直接依赖：ISSUE-015、027
- 映射：FR-011、018

### 目标

实现可由模拟器/真机 controller 驱动的无 UI OSL 六步和空采采集会话，不复制硬件采集循环。

### 范围

- OSL Open/Short/Load × S11/S22 状态机、步骤冻结配置、目标道数和重试/取消。
- 空采采集会话、raw 或 OSL-calibrated 域声明。
- `accept_sweep` 严格检查 axis/channel/config，聚合统计并委托 I027 构建。
- 会话不拥有窗口；可选 controller adapter 只编排。

### 排除项

- 不保存参考文件、不做 Qt wizard、不自动切换物理标准件。

### 验收标准

- 状态机不允许跳步/混配置；步骤失败可按规则重试/保留前序。
- 目标道数收齐后先关接受门，再安全停止 controller。
- 取消/设备错误无线程泄漏，不伪造标准件。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-028。确认 ISSUE-015/027 完成；阅读 AGENTS.md、docs/issues/README.md、docs/CALIBRATION.md 第 3/4 节和 docs/ACQUISITION.md。

实现无 UI ReferenceCaptureSession：OSL 按 S11/S22 各 Open/Short/Load 的显式状态机（物理六步）和 AirBackground 会话；冻结 sweep config/channel/axis/目标道数，通过 accept_sweep 严格聚合，委托 ISSUE-027 求解；支持重试、取消、错误、步骤保留策略。若接 controller，复用现有采集循环，收齐后先关闭接受门再 stop。

不得保存 .rcal/.rcbg、做 Qt wizard、控制自动标准件或伪造数据。用 SimulatedBackend 覆盖跳步、混配置、in-flight、重试、取消和资源关闭。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-029：`.rcal/.rcbg`、兼容性与质量报告

- 状态：Done（2026-09-05 自动化轮：t3 复审 PASS WITH CONDITIONS + F1(P2) 修复闭合（t4，captain 接管）并经 reviewer 定向复验 F1 CLOSED 后自动合并，见 [docs/reports/ISSUE_029_REVIEW_REPORT.md](../reports/ISSUE_029_REVIEW_REPORT.md)；P3 F2-F5 挂账）
- 直接依赖：ISSUE-011、027、028
- 映射：FR-011

### 目标

实现版本化参考文件、内容摘要、严格兼容性和可审计质量报告。

### 范围

- `.rcal/.rcbg` JSON schema、复数编码、读写和 digest。
- profile/reference ID、axis/channel/config、数据域、创建时间/软件版本、采集统计。
- compatible / compatible-with-warnings / incompatible 字段级结果。
- OSL 残差/退化、空采稳定性/离群等质量报告框架。

### 排除项

- 不应用校准/背景、不做 UI、不因用户选中文件自动启用。

### 验收标准

- 往返数值/metadata/digest 稳定，未知 schema/损坏摘要拒绝。
- axis/channel/domain/profile 硬错配拒绝；软警告单独列明。
- reference 文件不依赖原临时对象即可审计。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-029。确认 ISSUE-011/027/028 完成；阅读 AGENTS.md、docs/issues/README.md、docs/CALIBRATION.md、docs/DATA_FORMAT.md 第 8 节。

实现 versioned .rcal/.rcbg JSON schema、复数无损编码、reader/writer、内容 digest、profile/reference ID、完整 axis/channel/config/domain/provenance 和质量统计。实现字段级 compatibility result：硬性 axis/channel/S 参数/domain/profile 错配为 incompatible，环境/时间等软差异为 warning。用户选择文件本身不能等于启用。

不要应用 OSL/背景或做 UI。测试往返、摘要篡改、未知版本、双通道顺序、频率微差、raw/calibrated domain 和质量异常。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-030：ProcessingStage 框架与频域带通

- 状态：Planned
- 直接依赖：ISSUE-001、004、007
- 映射：FR-012

### 目标

建立独立 stage 接口/域检查/history，并迁移钢筋仪 sin² 四频点频域带通。

### 范围

- ProcessingStage 协议、参数规范化、输入/输出域和 history 追加。
- sin² 四频点窗、Hz 参数、通道向量化、complex 保持。
- reference 源哈希和黄金输入/输出。
- 重复 stage/非法参数/不支持域拒绝。

### 排除项

- 不实现 IFFT、OSL、背景或 UI pipeline。

### 验收标准

- raw 输入对象/数组不变；输出 history 精确记录版本/参数。
- 单/双通道和频率边界与参考对拍。
- 带通与 IFFT 无隐式耦合。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-030。确认 ISSUE-001/004/007 完成；阅读 AGENTS.md、docs/issues/README.md、docs/PROCESSING.md、docs/REFERENCE_MIGRATION.md。

建立无 UI 的 ProcessingStage 契约、domain 检查、规范参数和不可变 history 追加；按 baseline 从钢筋仪迁移 sin² 四频点 frequency bandpass，参数用 Hz，沿 frequency 轴向量化并保持 complex/multi-channel。记录源 SHA256 与黄金样本；重复 stage、非法四频点、频段不相交和错误域必须拒绝。

不要实现 IFFT/OSL/背景/UI，也不要把带通塞入其他函数。测试 raw 不变、单/双通道、边界、dtype/history 和参考对拍。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-031：IFFT、物理时间轴与显示时窗

- 状态：Planned
- 直接依赖：ISSUE-030
- 映射：FR-004、012、016

### 目标

迁移并验证从均匀频率轴到完整 `time_base` 的补零/IFFT，正确呈现物理无模糊时窗与可调显示裁剪。

### 范围

- axis 对齐、DC→起频补零、FFT 长度/插值、time axis 秒。
- 非等间隔/重复/错 bin 拒绝和容差。
- 完整 time_base 与独立 display crop view。
- stage history、黄金样本和多通道/多道向量化。

### 排除项

- 不宣称补零提高物理分辨率，不计算深度，不做 UI。

### 验收标准

- `time period≈1/Δf`、axis/shape 与直接参考对拍。
- display crop 不修改/截断存档 time_base。
- 带通可选且仍是独立前置 stage。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-031。确认 ISSUE-030 完成；阅读 AGENTS.md、docs/issues/README.md、docs/PROCESSING.md 第 4 节、docs/ACQUISITION.md 第 6 节。

按钢筋仪黄金样本实现独立 FrequencyToTimeStage：验证均匀 frequency axis，按网格从 DC 到起频补零，显式 FFT length/interpolation，输出完整 time_base 和秒单位 time axis；display crop 作为独立只读 view/config，不改变存档基础结果。记录 history，支持 trace×channel×frequency。非均匀/重复/错 bin fail-closed。

不得把 bandpass 内置、计算深度或宣称补零提高分辨率。测试 1/Δf 物理周期、直接 IFFT、单/双通道、crop 边界、raw 不变和参考对拍。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-032：OSL 处理阶段与 calibrated provenance

- 状态：Planned
- 直接依赖：ISSUE-027、030
- 映射：FR-011、012

### 目标

把 OSL 求解结果封装为严格域转换 stage，产生可单独保存的 `frequency_calibrated` 并防止二次校准。

### 范围

- raw→osl_calibrated 的 stage、profile/channel/axis 兼容检查。
- 多通道分别应用对应 profile。
- history/provenance/profile digest；重复 OSL 检测。
- safe reuse 判定接口。

### 排除项

- 不采 OSL、不保存文件、不应用空采或 IFFT。

### 验收标准

- raw 永不修改；calibrated 是 OSL 后、空采前。
- 错 profile/axis/channel 或已有 OSL history fail-closed。
- safe reuse 只接受严格相同 profile provenance。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-032。确认 ISSUE-027/030 完成；阅读 AGENTS.md、docs/issues/README.md、docs/CALIBRATION.md 第 5 节、docs/PROCESSING.md。

实现 OslCalibrationStage，把 frequency_raw 严格转换为 OSL_CALIBRATED 新对象；逐通道验证 S 参数、axis、profile ID/digest，追加完整 history，并提供 safe reuse provenance 校验。frequency_calibrated 的语义固定为 OSL 后、空采前；已有 OSL 或错 profile 必须拒绝，raw 绝不修改。

不要采集/保存 OSL，不做背景/IFFT/UI。测试双通道 profile、错序/错轴、二次校准、safe reuse 相同/不同 profile、history 和数值对拍。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-033：空采背景处理阶段与数据域保护

- 状态：Planned
- 直接依赖：ISSUE-029、030、032
- 映射：FR-011、012

### 目标

实现复数频域空采减除，严格匹配 raw/OSL-calibrated 域和 calibration profile。

### 范围

- AirBackgroundSubtractionStage、channel/axis/domain/reference 校验。
- 参考复数向量按多道/通道广播但不修改输入。
- calibrated 域要求 profile ID/digest 相同。
- history 和重复背景检测。

### 排除项

- 不实现沿测线 Flat、连续背景、参考采集或 UI。

### 验收标准

- raw reference 不能用于 calibrated 数据，反之亦然。
- 多通道顺序和 profile 错配拒绝。
- 数值/history 对拍且与 Flat 明确区分。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-033。确认 ISSUE-029/030/032 完成；阅读 AGENTS.md、docs/issues/README.md、docs/CALIBRATION.md、docs/PROCESSING.md。

实现 AirBackgroundSubtractionStage：在复数频域按通道/频率减 reference，严格验证 axis/channel/reference ID、RAW 或 OSL_CALIBRATED domain；校准域必须匹配 calibration profile ID/digest。输入不可变，history 记录完整，重复应用拒绝。支持单道和 scan 广播但不得依赖 UI。

不要实现沿 trace 的 Flat、连续背景或参考采集。测试 raw/calibrated 域错配、多通道顺序、profile 错配、非有限/shape、重复应用和数值/history。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-034：Dewow 时域阶段

- 状态：Planned
- 直接依赖：ISSUE-030、031
- 映射：FR-012

### 目标

迁移沿 time 轴中心滑动平均 Dewow，保持 complex、多通道、边界和 history 语义。

### 范围

- 时间窗口秒→样本数的明确舍入/奇数策略。
- reflect 或冻结参考边界策略、O(N) 算法。
- time_base/time_processed 输入规则、重复 stage 保护。
- 黄金样本和性能 smoke。

### 排除项

- 不实现 Flat、实时 UI 或参数对话框。

### 验收标准

- complex 等价于 real/imag 独立处理，shape/axis 不变。
- 短数组/超大窗口/边界行为固定。
- 输入 time_base 不变，输出 history 正确。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-034。确认 ISSUE-030/031 完成；阅读 AGENTS.md、docs/issues/README.md、docs/PROCESSING.md 第 5 节和迁移规则。

按钢筋仪 baseline 迁移 DewowStage：沿最后 time 轴减中心滑动平均，窗口以秒配置并用明确规则转样本，固定边界策略，O(N) 或同等级实现，完整保留 complex、trace/channel/time、axis/metadata，输出新的 time_processed/history。禁止重复 Dewow 和非法 history 顺序。

不要实现 Flat 或 UI。用常量、脉冲、复数、短数组、多通道、窗口边界和黄金样本测试，并做小型性能 smoke；输入必须不变。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-035：Flat Reflection 时域阶段

- 状态：Planned
- 直接依赖：ISSUE-030、031
- 映射：FR-012

### 目标

迁移沿 trace 轴局部滑动平均减除的 Flat Reflection stage，并固定它与空采背景的不同语义。

### 范围

- trace 窗口、edge 策略、O(N) complex、多通道/time。
- time_processed/history 和推荐 Dewow→Flat 顺序。
- 短测线、窗口边界、重复/错误顺序保护。
- 黄金样本与对连续层反射风险的文档。

### 排除项

- 不实现实时增量近似或 UI 默认启用。

### 验收标准

- 沿 trace 而非 frequency/time 轴运算。
- 不与 AirBackground 混名/混 history。
- 对拍、输入不变、潜在目标削弱明确记录。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-035。确认 ISSUE-030/031 完成；阅读 AGENTS.md、docs/issues/README.md、docs/PROCESSING.md 第 6 节和 docs/CALIBRATION.md 的概念边界。

按钢筋仪 baseline 迁移 FlatReflectionFilterStage：沿第 0 维 trace 轴减局部滑动平均，窗口/edge 策略固定，O(N)，保持 complex/channel/time/metadata，输出新 time_processed/history；推荐顺序 Dewow->Flat，重复和错误顺序拒绝。文档说明它可能削弱连续层反射，且绝不等同频域空采背景。

不要做实时增量近似或 UI 默认启用。测试水平背景、局部目标、复数、多通道、短测线、窗口边界、顺序/history 和黄金对拍。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-036：完整处理编排、revision 与安全回放

- 状态：Planned
- 直接依赖：ISSUE-011、018、029～035
- 映射：FR-011、012、016

### 目标

在 application 层编排唯一处理链，保留 raw/calibrated/time_base/time_processed，并支持参数 revision 和不二次处理的回放。

### 范围

- 顺序：OSL→calibrated snapshot→air background→bandpass→IFFT→Dewow→Flat。
- fresh processing 与 safe replay reuse 两条严格入口。
- processing revision/cancellation，过期结果可丢弃但 raw 存储不受影响。
- 结果写回/附加到 ground rcscan 的受控 storage 接口。

### 排除项

- 不实现 UI、零时/连续背景等未完成 stage。

### 验收标准

- 所有组合保持 raw，数据域/history 顺序正确。
- 相同 profile 回放可安全复用 calibrated；错误/非空 raw history 拒绝。
- time_base 总是 IFFT 基础，time_processed 仅在时域 stage 开启时存在。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-036。确认 ISSUE-011/018/029～035 全部完成；阅读 AGENTS.md、docs/issues/README.md、docs/PROCESSING.md、docs/CALIBRATION.md、docs/DATA_FORMAT.md。

在 application 层实现唯一处理编排：可选 OSL -> 保存 calibrated snapshot -> 可选 air background -> 可选 bandpass -> IFFT/time_base -> 可选 Dewow -> 可选 Flat/time_processed。区分 fresh raw（history 必须为空）和 safe replay reuse（严格相同 profile/provenance）；加入 processing revision/cancel，过期显示结果可丢弃但不得影响 raw 存储。通过受控 storage 接口附加派生数据/history。

不要实现 UI、零时或连续背景。测试所有关键组合、二次 OSL/背景拒绝、错 profile、revision 竞争、保存加载回放对拍和 raw byte 不变。运行全量门禁并报告，停止，不 commit/push。
```
