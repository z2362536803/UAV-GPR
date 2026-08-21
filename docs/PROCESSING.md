# 数据处理

版本：0.1
状态：目标设计，尚未实现

## 1. 原则

- 算法优先迁移钢筋仪项目中已有、经过测试的实现。
- 每个阶段独立、可测试、无 UI/硬件依赖。
- 输入不可变，输出新对象；完整记录参数、版本、输入/输出域和历史。
- 实时预览和任务后重处理复用同一算法，不复制两套数学实现。
- 处理失败不能破坏已保存的 `frequency_raw`。

## 2. 推荐流水线

```text
frequency_raw
  -> OSL calibration (optional)
  -> frequency_calibrated snapshot (optional)
  -> air background subtraction (optional)
  -> frequency bandpass (optional)
  -> frequency-to-time / IFFT
  -> time_base
  -> dewow (optional)
  -> flat reflection filter (optional)
  -> time zero (future, optional)
  -> continuous background (future, optional)
  -> time_processed
```

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
- 任务后重处理从 raw 或经过严格 provenance 验证的 calibrated 数据开始。
- UI 的显示增益、色图和动态范围不是数据处理历史，除非它们被真正写入导出图像配置。

## 8. 性能规则

- 对数组轴进行显式向量化，避免在 UI 刷新中全历史复制。
- 长 B-scan 使用分块/窗口化处理，保留可重建的完整原始数据。
- 每个 stage 建立 trace 数、channel 数、frequency/time 数的基准矩阵。
- 数值优化不得改变 dtype、轴或边界结果而无版本升级和回归样本。

## 9. 验证

- 从钢筋仪项目冻结算法输入/输出黄金样本及参考文件哈希。
- 覆盖零输入、脉冲、常量、复数、双通道、短数组、非法轴和非有限值。
- 验证处理历史顺序、参数序列化、重复阶段拒绝和 raw 不变。
- 保存/加载后对拍处理结果；回放不重复 OSL 或背景。
- 性能报告同时给出算法耗时、内存峰值和数据规模。
