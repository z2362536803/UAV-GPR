# 架构决策记录（ADR）

ADR 记录跨模块、难以逆转或影响数据契约的决定。状态使用 `Proposed / Accepted / Superseded / Rejected`。已接受 ADR 不能直接改写结论；需要改变时新增 ADR 并标明替代关系。

当前决策：

| ADR | 状态 | 决策 |
|---|---|---|
| [0001](0001-ground-centric-two-end-architecture.md) | Accepted | 地面端为主、空中端轻量执行 |
| [0002](0002-rcscan-v2-dual-copies.md) | Accepted | `.rcscan` v2 与空地完整原始副本 |
| [0003](0003-menu-driven-ground-ui-with-gnss-map.md) | Accepted | 菜单式地面 UI、左双 B-scan、右 GNSS 地图 |
| [0004](0004-air-store-then-forward.md) | Accepted | 空中端 store-then-forward 与持久 ACK |
| [0005](0005-reference-authority.md) | Accepted | 钢筋仪主参考、UAV-GPR 仅 GNSS/部署参考 |

尚未冻结的选择（例如地图渲染技术、协议二进制 codec、outbox 具体数据库）应先建立 Proposed ADR，不得在临时代码中隐式决定。
