# 测试策略

版本：0.1
状态：项目门禁，测试尚未创建

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
