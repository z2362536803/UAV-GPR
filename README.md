# UAV-GPR

面向无人机平台的新一代探地雷达软件。系统采用“地面端为主、空中端为轻量执行器”的双端架构：地面端负责任务配置、校准、处理、实时显示、GNSS 地图、主存档和数据检查；空中端仅负责 LibreVNA/GNSS 接入、冻结任务执行、本地可靠存储和数据回传。

## 当前状态

项目处于**初始化阶段**。当前仓库只包含目录骨架、工程配置和设计/开发指导文档，不包含可运行的采集、通信、处理、存储或 UI 功能。

## 设计基线

- 主要参考：`E:\钢筋仪软件开发` 的分层、数据模型、LibreVNA 采集、校准、处理、`.rcscan` 数据格式和 B-scan-first UI。
- 受限参考：`E:\UVA_GPR_system` 仅用于 GNSS 读取与匹配思路，以及 HM30/IP/供电等部署事实。
- 禁止直接迁移旧 UAV-GPR 的主窗口、NPZ 逐道协议、每道 CSV/NPZ 存储、Matplotlib B-scan、在线 Leaflet 地图和校准/处理实现。

## 目录导航

- `src/uav_gpr/`：产品代码包骨架。
- `config/`：地面端和空中端示例配置。
- `docs/`：产品、架构、数据、协议、UI、测试和部署文档。
- `docs/issues/`：完整 Issue 依赖图、逐项验收标准和 DeepSeek Harness 提示词。
- `tests/`：单元、集成、契约、UI、硬件和测试夹具目录。
- `tools/`：基准、诊断和迁移工具的预留位置。
- `resources/`：可随软件分发的非代码资源说明。

首次参与项目时，按顺序阅读：

1. [AGENTS.md](AGENTS.md)
2. [文档索引](docs/INDEX.md)
3. [产品需求](docs/PRODUCT_REQUIREMENTS.md)
4. [总体架构](docs/ARCHITECTURE.md)
5. 当前任务涉及的专题文档
6. [Issue 执行总表](docs/issues/README.md) 中的目标 Issue 与直接依赖

## 开发环境（预留）

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest
```

目前没有应用启动命令；入口点应在对应实现任务完成并通过测试后再加入 `pyproject.toml`。

## 数据与安全

实测数据、校准文件、运行日志、离线地图缓存、密钥和本地配置均不得提交。正式数据以 `.rcscan` HDF5 家族保存；空中端和地面端均保留完整原始数据副本，以逐道原始数据哈希进行一致性核验。
