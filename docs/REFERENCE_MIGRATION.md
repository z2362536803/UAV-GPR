# 参考项目迁移规则

版本：0.1
状态：迁移白名单已决定，尚未执行迁移

## 1. 目的

本项目不是把两个旧项目拼接在一起。每次迁移必须知道为什么信任该来源、迁移了什么契约、做了哪些 UAV 适配，并以新项目测试证明。

## 2. 参考基线风险

在项目初始化分析时，两个参考工作树均包含未提交/未跟踪内容，尤其 `E:\UVA_GPR_system` 的当前代码明显领先于部分早期文档。因此：

- 不用“某目录当前最新版”作为可复现来源；
- 每次任务开始冻结 `git status`、branch、HEAD 和所有实际参考文件 SHA256；
- 当前冻结基线清单位于 `docs/reference-baselines/`，由 `tools/migration/reference_manifest.py` 只读生成（见该目录 README）；
- 未提交文件以逐文件哈希记录，必要时创建只读快照；
- 文档陈述必须与实际代码/测试交叉验证；
- 本项目绝不修改两个参考目录。

## 3. 权威矩阵

| 新项目领域 | 主要来源 | 迁移策略 |
|---|---|---|
| Core 模型/不可变约束 | 钢筋仪 | 提取契约、重命名为 UAV 领域并补任务/GNSS ID |
| LibreVNA transport/backend | 钢筋仪 | 迁移分层与测试，按目标固件/频段重测 |
| 调度、暂停/恢复 | 钢筋仪 | 迁移单一生产路径，补 store-first 事务 |
| OSL/空采/参考文件 | 钢筋仪 | 迁移算法和状态机，扩展双端 provenance |
| 带通/IFFT/Dewow/Flat | 钢筋仪 | 保持独立 stage 和黄金样本 |
| `.rcscan` | 钢筋仪 v1 | 保持格式族，显式设计 UAV v2 与增量恢复 |
| Ground B-scan UI | 钢筋仪 | 迁移 pyqtgraph 思路，不迁移巨型窗口 |
| GNSS parser/reader/matcher | UAV-GPR | 唯一生产代码白名单；审查后分模块迁移 |
| HM30 事实 | UAV-GPR docs/手册 | 只作部署起点，实物复核 |
| Transport application semantics | 新实现 | 参考旧问题，不迁移旧 sender/receiver |
| Offline map | 新实现 | 不迁移在线 Leaflet/CDN |

## 4. 明确禁止

- 复制 `uav_gpr_qt.py` 或 `uav_gpr_ground_station.py` 后继续拆分。
- 将逐道 CSV/NPZ 作为正式数据或协议。
- 迁移旧 UAV-GPR Matplotlib B-scan、在线地图、校准或处理代码。
- 复制钢筋仪超大 `main_window.py`/wizard 后只改名称。
- 同时保留两条 LibreVNA 生产采集路径。
- 为了兼容旧代码而破坏 `AGENTS.md` 的 ID、数据或 store-then-forward 规则。

## 5. 迁移清单模板

每个迁移任务在 `docs/plans/` 记录：

```text
target issue/task:
reference repository:
reference branch + HEAD:
reference worktree status:
source file(s) + SHA256:
trusted behavior/contract:
excluded behavior:
new target module(s):
UAV-specific adaptations:
tests/golden fixtures migrated:
new tests added:
numeric or performance comparison:
license/provenance review:
```

只记录真正阅读和使用的文件，避免把整个脏工作树笼统称为来源。

## 6. 算法迁移

- 先冻结小型输入/输出黄金样本和参数。
- 在新项目按正确层级实现，不复制 UI/脚本入口。
- 验证 dtype、shape、axis、边界和处理 history。
- 结果差异必须解释；性能优化在数值对拍通过之后。
- MATLAB 或实验脚本仅作算法来源时记录编码、外部依赖和不可直接运行条件。

## 7. GNSS 迁移

- 只迁移纯 parser/reader/matcher 行为，不带 Qt map/widget。
- 先为旧行为建立 NMEA 夹具，再修复已知时区、过期或关闭问题。
- 新模型显式区分 fix 与 trace match、UTC 与单调时间、MSL 与 AGL。
- 不因“旧项目现场用过”省略错误和重连测试。

## 8. 数据迁移

- v1 reader 优先于批量转换器，使旧钢筋仪文件可只读打开。
- 转换生成新 v2 文件并记录源文件 SHA256 和迁移工具版本。
- 原文件不原地覆盖。
- 旧 UAV-GPR CSV/NPZ 若未来需要导入，必须作为独立、一次性的 migration adapter，不进入生产采集路径。

## 9. 完成证据

迁移完成至少需要：源哈希清单、目标模块、自动化测试、黄金样本对拍、文档更新和未迁移内容说明。只有代码相似或能运行不算完成。
