# M09：地面端应用与数据工作区（ISSUE-047～052）

地面端承载主要功能，但主窗口只显示数据与关键状态。所有阻塞任务通过 application/worker 边界执行。

## ISSUE-047：地面连接与任务控制应用服务

- 状态：Planned
- 直接依赖：ISSUE-006、039、043
- 映射：FR-001、002、004、005

### 目标

实现无 UI 的地面 link/mission facade，创建任务草稿、冻结摘要、下发幂等命令并聚合远端真实状态。

### 范围

- connect/disconnect/status、single-air identity 和 capability。
- draft validation→new mission ID→frozen config/digest→start command。
- pause/resume/stop、command vs mission vs sync 状态分离。
- 可订阅不可变 GroundStateSnapshot 和结构化错误。

### 排除项

- 不接收 trace、不写文件、不做 Qt widgets。

### 验收标准

- 未连接/能力不兼容/磁盘拒绝等状态不能误报运行。
- 重复点击不重复创建任务/副作用。
- 状态 snapshot 不泄露 transport 内部线程对象。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-047。确认 ISSUE-006/039/043 完成；阅读 AGENTS.md、docs/issues/README.md、docs/ARCHITECTURE.md、docs/UI.md 的状态语义。

在 application/ground 实现 GroundMissionService：管理单 air 连接/身份/能力，校验 draft，生成新 mission_id 和冻结 config/digest，发送 start/pause/resume/stop 幂等命令；分别聚合 command、remote mission、data sync 状态到不可变 GroundStateSnapshot。结构化拒绝不能被压成一个“成功/失败”布尔。

不要接收 trace、写 HDF5 或做 Qt。用 fake transport 覆盖断线、重复点击、能力不兼容、accepted/executing/succeeded 分离、远端仍运行但链路断开和重连查询。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-048：地面 ingest、处理、存储与回放应用服务

- 状态：Planned
- 直接依赖：ISSUE-014、018、036、041、043
- 映射：FR-008、012、016、018、019

### 目标

建立地面非 UI 数据流水线：可靠 ingest/ACK 优先，处理与显示异步，支持打开/回放/重处理和完整性报告。

### 范围

- ground ingest worker 接 ISSUE-041，writer 成功独立于 processing/display。
- 有界 processing queue、revision、最新/窗口 snapshot 和过期结果丢弃。
- `.rcscan` 打开、逻辑排序、FileReplayBackend、重处理和 inventory report。
- 生命周期、取消、磁盘/处理错误状态分离。

### 排除项

- 不做 Qt、地图或导出格式扩展。

### 验收标准

- 处理失败/慢 UI 不阻止 raw 持久 ACK。
- payload 解码/HDF5/全量处理不在 UI 主线程（通过接口和测试证明）。
- replay 不伪元数据或二次校准。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-048。确认 ISSUE-014/018/036/041/043 完成；阅读 AGENTS.md、docs/issues/README.md、docs/ARCHITECTURE.md、docs/PROCESSING.md、docs/PERFORMANCE.md。

实现 GroundDataService：可靠 ingest/validate/ground writer/ACK 是独立优先路径；processing 在有界 worker queue 中按 revision 运行，过期显示结果可丢弃但 raw 事实不丢。提供打开 rcscan、逻辑排序、FileReplayBackend、重处理、取消和 inventory report；发布不可变有界 display snapshots。磁盘错误、处理错误、显示滞后分别建模。

不要做 Qt/地图/新导出。故障测试证明慢处理/失败/UI 不影响持久 ACK，解码/HDF5/全量处理不在调用线程，回放不二次校准。运行门禁并报告，停止，不 commit/push。
```

## ISSUE-049：地面主窗口、菜单、splitter 与状态栏

- 状态：Planned
- 直接依赖：ISSUE-047、048
- 映射：FR-013、015、ADR-0003

### 目标

建立 1440×900/1920×1080 菜单式主窗口：左侧双数据区、右侧地图占位、底部单行状态，不塞常驻设置面板。

### 范围

- File/Connection/Acquisition/Calibration/Processing/View/Tools/Help 菜单和 action 状态绑定。
- horizontal splitter 68:32、左 vertical dual B-scan placeholder、右 map placeholder 可折叠。
- 单行状态栏：link/air/VNA/GNSS/mission/traces/missing/outbox。
- MainWindow 只组装 view/presenter，不访问硬件/文件/socket。

### 排除项

- 不实现真实 B-scan、地图或各对话框业务。

### 验收标准

- 1280×720、1440×900、1920×1080 及 100/125/150% DPI 不丢关键入口。
- action enabled/checked 由 GroundStateSnapshot 驱动。
- 默认无常驻日志/参数矩阵，地图可隐藏后 B-scan 占满。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-049。确认 ISSUE-047/048 完成；阅读 AGENTS.md、docs/issues/README.md、docs/UI.md、ADR-0003。

实现 PySide6 GroundMainWindow 壳和 presenter：顶部八组菜单，中央 horizontal splitter 默认 68:32，左侧 vertical 双 B-scan placeholder，右侧 GNSS map placeholder 可从 View 折叠，底部单行八类状态。action enabled/checked 完全由 GroundStateSnapshot 映射；MainWindow 不导入 USB/serial/socket/h5py，不做阻塞工作。

不要实现真实 B-scan/地图/对话框业务，不加常驻日志或多行参数面板。pytest-qt 覆盖菜单、状态映射、splitter/折叠、关闭和 1280/1440/1920+DPI 布局。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-050：有界双 B-scan 缓冲与 pyqtgraph 控件

- 状态：Planned
- 直接依赖：ISSUE-036、048、049
- 映射：FR-013、016

### 目标

实现不随任务长度全量重建的双通道 B-scan 数据缓冲、ImageItem 更新和 trace 光标选择。

### 范围

- append/ring/window display buffer，完整数据留在 storage。
- pyqtgraph stable ImageItem/colorbar/axis，显示刷新限频。
- S11/HH、S22/VV 共用 trace selection/time range；单通道布局。
- 时间 ns 轴、trace index、无深度伪装；动态范围/色图为显示配置。

### 排除项

- 不做 GNSS 地图联动、不把 Flat 等处理写入 widget。

### 验收标准

- 刷新不重建 figure/axes/colorbar，不每道全量 column_stack。
- 10 万道输入视图内存有界，当前窗口选择准确。
- 处理 revision 过期 snapshot 不覆盖新图。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-050。确认 ISSUE-036/048/049 完成；阅读 AGENTS.md、docs/issues/README.md、docs/UI.md 第 4 节、docs/PERFORMANCE.md。

实现有界 BScanDisplayBuffer 和 pyqtgraph BScanWidget：追加/环形或窗口 snapshot，不为每道重建完整历史；复用 ImageItem、axis 和 colorbar，刷新限频。双视图按 ChannelSpec 显示 S11/HH 与 S22/VV，共享 trace UID selection/time range，单通道优雅降级。纵轴默认 time ns，未标定不显示 depth；display gain/colormap 不写 processing history。

禁止 Matplotlib、每帧 column_stack、widget 内处理算法或地图联动。pytest-qt/性能测试覆盖 1k/10k/100k、revision 竞争、色阶、单/双通道、选择和资源复用。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-051：采集设置、冻结摘要与任务控制对话框

- 状态：Planned
- 直接依赖：ISSUE-006、047、049
- 映射：FR-002、004、005、015

### 目标

通过菜单对话框配置扫频/时窗/间隔/GNSS/存储，展示推导值并在开始前冻结任务摘要。

### 范围

- 约 900×650 可缩放设置 dialog，单位/验证/预计数据量。
- 同时显示 Δf、physical window、display crop、目标 interval 与未验证最小值警告。
- start confirmation、mission notes、pause/resume/stop action 接 service。
- 采集中关键字段只读，改变需新任务。

### 排除项

- 不连接设备、不实现 calibration/processing dialog，不把设置常驻主界面。

### 验收标准

- 非法 config 无法下发；摘要/digest 与 application 完全一致。
- “命令已发送/接受/运行”分别显示。
- DPI/键盘/单位测试通过。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-051。确认 ISSUE-006/047/049 完成；阅读 AGENTS.md、docs/issues/README.md、docs/UI.md 第 3/6 节、docs/ACQUISITION.md 第 6/7 节。

实现从 Acquisition 菜单打开的设置/冻结摘要/任务控制对话框：配置 frequency/points/IFBW/channels/count-or-continuous/target interval/GNSS/storage，实时显示 Δf、physical window=1/Δf、display crop 和预计数据量；所有值交 MissionConfig 校验/digest。开始前展示不可变摘要；运行中关键字段只读，改变引导新任务。命令 sent/accepted/running 分开。

不要访问硬件或实现 calibration/processing，也不把设置放主界面。pytest-qt 覆盖单位/非法值、摘要一致、状态、键盘和 DPI。运行门禁，报告并停止，不 commit/push。
```

## ISSUE-052：校准、处理、检查与回放 UI

- 状态：Planned
- 直接依赖：ISSUE-028、029、036、048、049
- 映射：FR-011、012、016

### 目标

通过顶部菜单提供 OSL/空采向导、参考兼容性、处理参数、文件检查和回放控制，主界面不常驻复杂控件。

### 范围

- OSL 六步/空采 wizard 绑定无 UI session/controller，人工标准件提示。
- `.rcal/.rcbg` load/unload/enable 分离，兼容性/质量报告。
- bandpass/IFFT display crop/Dewow/Flat revision 对话框。
- open/replay/reprocess/partial inspect 只调用 application/tools API。

### 排除项

- 不实现新算法、不在 UI 线程采集/处理/HDF5，不加入未实现零时假开关。

### 验收标准

- wizard 状态复用 application 状态机，取消/错误关闭资源。
- profile 选择不自动启用，硬不兼容不能开始。
- 参数 revision 过期结果不覆盖；回放不二次处理。

### DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-052。确认 ISSUE-028/029/036/048/049 完成；阅读 AGENTS.md、docs/issues/README.md、docs/UI.md、docs/CALIBRATION.md、docs/PROCESSING.md。

实现菜单驱动的 OSL 六步/空采 Qt wizard（复用 ReferenceCaptureSession/Controller）、.rcal/.rcbg load/unload/enable 与兼容性/质量报告、bandpass/display crop/Dewow/Flat processing revision 对话框，以及 open/replay/reprocess/partial inspect 控制。人工切换标准件必须明确；选中文件不自动启用；硬不兼容阻止任务。所有采集/处理/HDF5 经 worker/application。

不要实现新算法、零时假开关或常驻主面板。pytest-qt 覆盖取消/重试/错误、参考状态、revision 竞争、回放安全和无线程泄漏。运行门禁，报告并停止，不 commit/push。
```
