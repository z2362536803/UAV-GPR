# 测试目录

- `unit/`：纯模型、算法和状态机；不得需要 Qt 事件循环、网络或硬件。
- `integration/`：多个内部层的组合，如采集到存储、断线后补传、文件恢复。
- `contract/`：协议消息和 `.rcscan/.rcal/.rcbg` 的跨版本契约与黄金样本。
- `ui/`：Qt 控件、菜单、状态映射和线程边界测试，默认使用离屏平台。
- `hardware/`：需要 LibreVNA、GNSS、HM30 或现场装置的显式测试。
- `fixtures/`：小型、匿名、可审计的合成数据；不得放现场实测数据。

测试分层、标记和验收矩阵见 `docs/TESTING.md`。

硬件测试（`hardware` 目录）默认被跳过，需要双重 opt-in：`--hardware` 命令行参数且环境变量 `UAV_GPR_HARDWARE_OPTIN=1`，见 `docs/TESTING.md` 2.1 节。共享 fixture（随机 seed、`scratch_dir`、`virtual_clock`、Qt offscreen）在 `tests/conftest.py`。
