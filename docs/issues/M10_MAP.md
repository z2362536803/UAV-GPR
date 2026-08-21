# M10：离线 GNSS 地图与联动（ISSUE-053～055）

地图是主界面的数据视图，但不能依赖互联网，也不能让渲染故障影响采集和地面存储。

## ISSUE-053：离线地图技术原型与 ADR

- 状态：Planned
- 直接依赖：ISSUE-002、005、026
- 映射：FR-013、014、ADR-0003

### 目标

用可运行原型和同一 10 万点数据集比较 Qt 原生/自绘与本地 WebEngine 等候选，冻结离线地图技术 ADR 和展示接口。

### 范围

- 完全断网启动、WGS84→显示投影、轨迹批量、current/selected 点、click hit test。
- 10 万点 CPU/GPU/内存/响应、DPI、Windows 打包/许可/崩溃隔离比较。
- `MapTrackSegment/CurrentPosition/SelectedTrace/ViewportCommand/MapSourceStatus` 接口。
- ADR 选择、后果和无底图 fallback；原型放实验/工具位置，不混进产品 UI。

### 排除项

- 不迁移旧 Leaflet/CDN，不在此交付最终 widget/瓦片包。

### 验收标准

- 禁网测试无 DNS/CDN 请求；同一基准数据有量化报告。
- 选择有许可/打包证据，无法证明则保守选择无底图原生方案。
- ADR 接受后生产实现才开始。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-053。确认 ISSUE-002/005/026 完成；阅读 AGENTS.md、docs/issues/README.md、docs/OFFLINE_MAP.md、docs/UI.md、ADR-0003。

创建隔离原型，使用同一合成 WGS84 10 万点比较 Qt 原生/自绘、local-only WebEngine 等实际可部署候选：完全禁网启动、投影、批量轨迹、current/selected、click hit、CPU/内存/DPI、Windows 包体/许可/崩溃隔离。定义展示接口 MapTrackSegment/CurrentPosition/SelectedTrace/ViewportCommand/MapSourceStatus，并基于证据写 Accepted ADR；无法证明离线/许可时选择无底图原生保守方案。

禁止迁移 UAV-GPR Leaflet、unpkg/在线瓦片，也不要把原型直接塞进 MainWindow 或交付最终 map widget。测试禁网且不发 DNS/HTTP，提交量化报告。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-054：离线/无底图 GNSS 地图与长轨迹

- 状态：Planned
- 直接依赖：ISSUE-026、049、053
- 映射：FR-013、014

### 目标

按 ADR 实现右侧地图控件，断网时显示轨迹/当前位置/选中道，并通过分段与显示层抽稀保持长时性能。

### 范围

- WGS84 输入、投影显示、无底图网格/比例尺/北向/fit/follow/zoom。
- 有效轨迹、current、selected；no-fix/stale 不冒充实时点。
- 分段缓存、viewport decimation、刷新限频；原始 GNSS 不删除。
- map source offline/blank/error 状态和故障隔离。

### 排除项

- 不做航线规划、飞控、默认联网下载或 B-scan 反向联动。

### 验收标准

- 完全断网可用；底图错误自动 blank fallback，不阻断应用。
- 10 万道内存有界，选中点保持精确。
- stale 时停止实时跟随并文字告警。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-054。确认 ISSUE-026/049/053 完成；阅读 AGENTS.md、docs/issues/README.md、docs/OFFLINE_MAP.md、ISSUE-053 ADR/report、docs/UI.md 第 5 节。

按已接受 ADR 实现 Ground GNSS MapWidget/展示模型：WGS84 轨迹、current、selected、fit/follow/zoom，无底图时仍有经纬网/比例尺/北向；no_fix/stale/invalid 明确显示且旧点不冒充 current。使用批量折线、分段缓存、viewport/display-only decimation 和刷新限频，保留原始点；map source/error 与采集/存储隔离。

禁止航线规划/飞控/默认联网下载/B-scan 联动。pytest-qt/禁网/10 万点测试覆盖内存、点击、stale、底图失败和 widget 异常不传播。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-055：B-scan 与地图 trace UID 双向联动

- 状态：Planned
- 直接依赖：ISSUE-048、050、054
- 映射：FR-014、019

### 目标

用共享 `TraceSelectionController` 按 `trace_uid` 双向同步 B-scan 列、地图点和检查器，不用像素或物理行号当业务 ID。

### 范围

- selection state/revision/source，防止双向递归回声。
- B-scan trace UID↔逻辑 index，地图 trace UID↔位置。
- 无 GNSS 道、被显示抽稀道和超出当前窗口道的提示/定位。
- 当前采集道与用户选中道分开。

### 排除项

- 不改写 GNSS/raw，不做最近坐标推测或航线选择。

### 验收标准

- 任一视图选择在另一视图精确高亮且只触发一次。
- 无定位道仍可选，地图明确提示；抽稀不丢选择精度。
- 乱序/回放/revision 更新保持同一 UID。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-055。确认 ISSUE-048/050/054 完成；阅读 AGENTS.md、docs/issues/README.md、docs/UI.md 第 4/5 节、docs/DATA_MODEL.md 第 9 节。

实现共享 TraceSelectionController，以 trace_uid 为唯一主键，维护 selection revision/source/current-live 与 user-selected；连接 BScanWidget、MapWidget 和 trace inspector，防止双向信号回声。处理无 GNSS、display decimation、超出 B-scan window、乱序补传和 replay；无位置道仍能选并由地图明确提示，不能用像素/物理 HDF5 行或最近坐标伪匹配。

不要修改 raw/GNSS 或加入航线逻辑。pytest-qt 覆盖双向单次触发、无定位、抽稀精确选择、窗口滚动、乱序和 revision。运行门禁，报告并停止，不 commit/push。
```
