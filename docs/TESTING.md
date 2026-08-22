# 测试策略

版本：0.2
状态：本地质量门禁已建立；`tests/unit`（含质量门禁自检）已有自动化测试并在门禁中运行；`tests/integration`、`tests/contract`、`tests/ui` 尚无测试，随后续 Issue 建立；`tests/hardware` 仅有硬件授权 sentinel（默认跳过/双重 opt-in），尚无真实硬件能力测试

## 1. 测试分层

| 目录 | 目的 | 默认 CI |
|---|---|---|
| `tests/unit` | 纯领域模型、算法、parser、状态机 | 是 |
| `tests/integration` | 多层组合与崩溃/重连流程 | 是 |
| `tests/contract` | 文件/协议/黄金样本兼容性 | 是 |
| `tests/ui` | Qt 控件、菜单和线程状态 | 是，offscreen |
| `tests/hardware` | LibreVNA/GNSS/HM30/现场干扰 | 否，显式运行 |
| `tests/fixtures` | 小型合成/匿名夹具 | 随测试 |

## 2. 基本命令

```powershell
python -m pytest
python -m pytest -m "not hardware and not slow"
python -m pytest -m hardware
python -m ruff check .
python -m mypy src
```

硬件测试必须检查明确的设备标识并要求操作者 opt-in，不得在普通开发机上自动连真机。

## 2.1 本地一键验证（Windows）

全新环境：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
powershell -ExecutionPolicy Bypass -File scripts\verify.ps1
```

`scripts\verify.ps1` 依次运行并**失败即停**（第一个失败步骤的退出码被原样返回）：

1. 非硬件 pytest（`-m "not hardware and not slow"`）；
2. Ruff（`ruff check .`）；
3. mypy（`mypy src`）；
4. 包导入检查（`import uav_gpr` 及核心分层子包）。

等价的纯 Python 入口是 `python tools\quality\verify.py`。

### 默认测试不得触碰的设施

- 不枚举或连接 USB，不打开真实串口，不访问外网；
- 不修改两个参考项目（`E:\钢筋仪软件开发`、`E:\UVA_GPR_system`）；
- 测试只使用合成数据与临时目录；真实设备路径必须标 `hardware`。

### 硬件双重 opt-in

硬件测试需要**同时**满足两个条件才会执行：

1. 命令行显式传 `--hardware`（唯一 CLI 授权开关）；
2. 环境变量 `UAV_GPR_HARDWARE_OPTIN=1`（唯一环境授权开关）。

`-m hardware` 只负责**选择**硬件测试项，不构成硬件授权；单独使用
`-m hardware`、单独使用 `--hardware` 或单独设置
`UAV_GPR_HARDWARE_OPTIN=1` 三种情况都必须在收集阶段跳过硬件测试。两项同时
存在时硬件测试才会运行（由 `tests/unit/test_quality_gates.py` 的哨兵测试证明）。

默认（两者缺一）情况下硬件测试在收集阶段被跳过。测试内部还应根据
`UAV_GPR_DEVICE_ID` 等设备标识做进一步自检。

默认测试（非硬件）不导入 `serial`/`usb`/`socket`/网络客户端，也不引用两个
参考项目路径；该约束由 `tests/unit/test_no_external_access.py` 的 AST/文本守卫
与硬件哨兵跳过测试共同保证。

### 环境与随机性

- `tests/conftest.py` 在导入时设置 `QT_QPA_PLATFORM=offscreen` 与 `TZ=UTC`；
- 每个测试开始前用 `--seed`（默认 0）重置 `random` 与 `numpy.random`；
- 共享临时目录 fixture：`scratch_dir`；
- 虚拟时钟 fixture：`virtual_clock`（UTC + 单调 ns，可推进）。

## 3. 必测契约

### Core

- 不可变数组和属性；
- shape/axis/channel/ID 校验；
- JSON 参数序列化和 UTC 时间；
- 无位置不生成假值。

### Acquisition

- 帧拆包、CRC、重复/缺失/乱序点；
- 配置回读、暂停/恢复、取消、重连；
- 单调间隔和 overrun；
- 模拟器与回放元数据真实性。

### GNSS

- NMEA 语句、checksum、跨午夜、无 fix、stale；
- reader 断开/重连/关闭；
- sweep midpoint 匹配和 MSL 语义。

### Storage

- v2 往返、多通道、缺失 GNSS；
- 每道 append/flush/checkpoint；
- 任意写入阶段崩溃后的只读检查和恢复；
- v1 读取、未知版本拒绝；
- 空地逐道哈希一致和冲突拒绝。

### Transport

- framing 的任意拆包/粘包、截断和长度上限；
- 命令幂等、心跳、ACK 丢失、重复/乱序 trace；
- 断线重连、outbox 重启恢复、补传和结束对账；
- 地面写盘失败不 ACK。

### Processing/Calibration

- 黄金数值样本、双通道、复杂边界和非有限值；
- raw 不变、history 顺序、域/profile 匹配；
- 重放不二次校准/二次背景。

### UI

- action 可用状态来自应用状态机；
- 菜单入口、布局/DPI、地图折叠；
- B-scan/地图 trace UID 联动；
- worker 回调不在错误线程更新控件；
- 关闭/取消无遗留线程。

## 4. 故障注入

必须可确定性注入：USB timeout、半个 sweep、GNSS 断串口、磁盘满、flush 异常、TCP 中断、ACK 丢失、慢地面端、坏哈希、重复命令和进程重启。

使用事件/barrier/虚拟时钟等待条件，不使用固定 `sleep` 猜并发时序。

## 5. 夹具

- 只提交小型合成数据，不提交现场轨迹和真实项目数据。
- 夹具带生成器版本、seed、单位、shape 和内容哈希。
- 从参考项目迁移的黄金样本需记录源路径/commit/hash，并确认无敏感信息。
- 二进制协议和 HDF5 黄金样本只在契约改变时显式更新，不能由测试运行自动覆盖。

## 6. 覆盖率与质量

覆盖率是辅助指标，不替代状态和故障矩阵。核心数据、存储、协议和状态机的新代码应达到高分支覆盖；硬件薄适配的不可模拟部分由真机报告补充。

任何 flaky test 视为缺陷：先定位时钟/并发/资源所有权，不能简单重跑或扩大 sleep。

## 7. 发布验收

- 全量非硬件测试、静态检查和打包 smoke 通过。
- 目标地面/空中机器完成 8 小时模拟耐久。
- 真机完成采集矩阵、GNSS、HM30 断线补传和空地数据对拍。
- 在副本数据上完成掉电/partial 恢复演练。
- UI 在目标分辨率/DPI 下人工走查并留报告。
