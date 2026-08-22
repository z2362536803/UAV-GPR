# 数据处理

版本：0.1
状态：目标设计，尚未实现

## 1. 原则

- 算法优先迁移钢筋仪项目中已有、经过测试的实现。
- 每个阶段独立、可测试、无 UI/硬件依赖。
- 输入不可变，输出新对象；完整记录参数、版本、输入/输出域和历史。
- `frequency_raw` 只允许作为处理输入；任何阶段输出 `frequency_raw`（含 raw→raw 恒等）都拒绝。
- 每个阶段用稳定 `stage_name` 标识，同一处理历史内不得重复应用同一阶段；修改 `stage_version` 不能绕过，重新处理必须开始新的 history/revision。
- 阶段携带的校准/背景引用必须与其输入/输出域兼容（域匹配时显式继承）：输出 `frequency_calibrated` 必须带校准引用，输出 `frequency_background_applied` 必须带空采引用，时域阶段不得携带频域引用。后续记录显式携带的引用必须与产生其对应域输入的上一记录相同；省略重复引用合法。
- 数据处理历史当前必须从 `frequency_raw` 开始；从已严格验证的派生频域快照开始需要未来的独立 provenance anchor，当前不允许。
- 本契约不声称代码验证任意 stage_name 的真实数学含义；算法数学由对应阶段实现任务验证（尚未实现）。
- 实时预览和任务后重处理复用同一算法，不复制两套数学实现。
- 处理失败不能破坏已保存的 `frequency_raw`。

## 2. 推荐流水线

```text
frequency_raw
  -> OSL calibration (optional)                       -> frequency_calibrated
  -> air background subtraction (optional)            -> frequency_background_applied
  -> frequency bandpass (optional)                    -> frequency_filtered
  -> frequency-to-time / IFFT                         -> time_base
  -> dewow (optional)                                 -> time_processed
  -> flat reflection filter (optional)                -> time_processed
  -> time zero (future, optional)                     -> time_processed
  -> continuous background (future, optional)         -> time_processed
```

数据域转换规则（`DataDomain`）：

- 频域派生链：`frequency_raw` → `frequency_calibrated` → `frequency_background_applied` → `frequency_filtered` → `time_base` → `time_processed`；`frequency_filtered` 只由带通阶段产生。
- 不允许 time 域返回 frequency 域；不允许 `time_processed → time_base`；不允许跳过 `time_base` 直接产生 `time_processed`。
- history 第一项输入域必须是 `frequency_raw`；从派生频域快照开始需要未来引入独立、不可变且可验证的 provenance anchor（当前未实现，不允许）。`time_base` 与 `time_processed` 的 `TimeDomainScan` 都必须携带完整、非空、最后输出域与 kind 匹配的历史。

没有启用时域后处理时可以不存在 `time_processed`，UI 使用 `time_base`。不得把处理后的数组写回 `time_base`。

## 3. 频域带通

首版计划迁移钢筋仪项目的 sin² 四频点窗。参数单位为 Hz，必须满足频点有序并与采集频段相交。带通与 IFFT 分离，便于关闭、测试和记录。

## 4. IFFT 与时窗

- 根据实际等间隔频率轴构建从 DC 到最高频率的对齐网格。
- 起始频率以上的实测数据放入正确 bin，缺少的低频按明确策略补零。
- FFT 长度/插值倍数显式记录；补零只插值，不宣称提高物理分辨率。
- 输出完整物理时窗的 `time_base`；显示裁剪不改变存档基础结果。
- 非等间隔轴、重复频点或容差外错位必须拒绝或走独立算法，不能悄悄套普通 IFFT。

## 5. Dewow

沿时间轴减去中心滑动平均以削弱直流/慢漂移。窗口以物理时间配置并转换为样本数；边界策略、奇偶处理和 complex dtype 必须固定测试。

## 6. Flat Reflection

沿 trace 轴减局部滑动平均，减少近似水平背景。该阶段可能削弱连续层状反射或与测线方向一致的目标，因此默认可选，UI 必须说明影响。实时单道到达时不应反复重算全部历史；可以采用增量预览或在任务后批处理，但两者的语义必须明确。

## 7. 实时显示与重处理

- 实时路径可以只处理最新道和有界显示窗口。
- 会依赖邻道的阶段必须定义延迟、边界和“暂定结果”状态。
- 参数变更产生新的 processing revision；过期 worker 结果按 revision 丢弃。
- 任务后重处理当前只能从 `frequency_raw` 开始；未来只有在独立、不可变、可验证的 provenance anchor 实现后，才允许从经严格验证的 calibrated 快照开始，且记录方式按当时文档/ADR 明确。
- UI 的显示增益、色图和动态范围不是数据处理历史，除非它们被真正写入导出图像配置。

## 8. 性能规则

- 对数组轴进行显式向量化，避免在 UI 刷新中全历史复制。
- 长 B-scan 使用分块/窗口化处理，保留可重建的完整原始数据。
- 每个 stage 建立 trace 数、channel 数、frequency/time 数的基准矩阵。
- 数值优化不得改变 dtype、轴或边界结果而无版本升级和回归样本。

## 9. 验证

- 从钢筋仪项目冻结算法输入/输出黄金样本及参考文件哈希。
- 覆盖零输入、脉冲、常量、复数、双通道、短数组、非法轴和非有限值。
- 验证数据域转换合法性（raw 永不出现在输出）、reference 兼容性、按 `stage_name` 的重复阶段拒绝，以及 `time_base`/`time_processed` 的完整非空 provenance。
- 验证处理历史顺序、参数序列化、重复阶段拒绝和 raw 不变。
- 保存/加载后对拍处理结果；回放不重复 OSL 或背景。
- 性能报告同时给出算法耗时、内存峰值和数据规模。
