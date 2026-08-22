# ISSUE-001～005 审查与修复总结

版本：1.0  
日期：2026-08-22  
结论：`PASS`，实现与复审修复已进入 `main` 并推送至 `origin/main`。

## 1. 范围与基线

- 初始基线：`fee386678740dd027c0cfd229090100eb160472b`
- 开发分支：`feat/m01-issues-001-005`
- 目标分支：`main`
- 最终实现提交：`b11e741fab958d1ac6769f48cde9124da6d37a02`
- 总范围：M01 的 ISSUE-001～005；未实施 ISSUE-006 及后续 Issue。

| Issue | 目标摘要 | 原始提交 | 复审修复 | 结论 |
|---|---|---|---|---|
| ISSUE-001 | 参考项目只读基线与可复现 manifest | `2c5b3790b0ad86c825f3a62e1b2a6151c871a732` | `6b74dcfcaf916985b344d211f591b285a21e29e4` | `PASS` |
| ISSUE-002 | Python 3.12 本地质量门禁与测试基础设施 | `341aa22c643be8b9e131ea4a95889bcc35c9cdf3` | `0786d8e43613fd288c12fc3bc0492d0510912d01` | `PASS` |
| ISSUE-003 | ID、枚举、结构化错误与时间工具 | `ac531fca7848a2a50c0f4b7d5270f87a1e0a7199` | `5fd6013d0e6e04b43de2fc1ec34b7bb5fee1819f` | `PASS` |
| ISSUE-004 | 不可变多通道频域数据模型 | `45c565727ccb53ea2b37fe882b86e5fa0f0e6b54` | — | `PASS` |
| ISSUE-005 | GNSS、道元数据与质量状态模型 | `952883e59411ac141e39b58f8ca2d0e0a902f73e` | `b11e741fab958d1ac6769f48cde9124da6d37a02` | `PASS` |

## 2. 审查发现与关闭情况

初审后进行了三轮最小修复和反例复核，最终关闭以下问题：

- ISSUE-001：Git 路径输出改为严格 UTF-8 解码；不可解码内容 fail-closed，中文路径和输出编码有回归测试。
- ISSUE-002：硬件测试改为 `--hardware` 与环境变量双重授权；单独 marker、参数或环境变量均不能触发；默认测试增加外部 I/O 与参考仓库访问守卫。
- ISSUE-003：`DomainError` 的嵌套上下文在输入、属性访问和序列化边界均隔离复制，调用方无法回改错误对象。
- ISSUE-005：补齐 raw hash 首次绑定、幂等、降级和冲突规则；同一道的身份与 11 项采集事实不可静默改变；sweep/scan 共用同一演进校验。
- ISSUE-005：GNSS match、sweep 中点和质量原因保持双向一致；无 match 时只允许 `gnss_missing` 与非 GNSS 原因共存，反序列化和复制更新不能绕过校验。
- 文档：`docs/TESTING.md` 明确硬件目录目前只有授权 sentinel，未声称存在真实硬件能力测试。

未发现 ISSUE 混提、范围外产品实现、参考项目写入、实测数据、密钥、日志或构建产物进入交付。

## 3. 最终验证证据

最终修复与 Git 交付阶段记录的 Python 版本为 3.12.3，结果如下：

| 检查 | 结果 |
|---|---|
| 核心 metadata/frequency 定向测试 | `63 passed` |
| 质量门禁自检 | `12 passed`；同时复核父环境带硬件 opt-in 的反例 |
| 全量非硬件测试 | `162 passed, 1 deselected` |
| Ruff | `All checks passed!` |
| mypy | `Success: no issues found in 26 source files` |
| `python tools/quality/verify.py` | pytest、Ruff、mypy、导入四门禁全部通过 |
| `git diff --check` | 通过 |

测试结果证明离线核心契约与本地门禁通过，不代表真实 LibreVNA、GNSS、HM30 或现场环境已验收。

## 4. Git 交付记录

- 原始 5 个 Issue 提交之后追加 4 个按 Issue 边界拆分的 fix 提交，共 9 个实现提交。
- 审查规范另由 `3963957b8c983ecdbf351f7cdabfeffc91b95c54` 和 `5c4cdb6af161902454dc510311679370afe1adc2` 建立并整理。
- ISSUE-001～005 已合入 `main`；本总结创建前，本地与远程 `main` 均指向 `5c4cdb6af161902454dc510311679370afe1adc2`。
- 后续 Issue 应以开工时实际的 `origin/main` 为基线，不再以旧功能分支为基线。

## 5. 遗留限制与后续交接

- `tests/hardware` 只有授权 sentinel；未连接或枚举 USB、串口、网络和真实设备。
- `FrequencyScan` 的 `None` 占位条目不参与 UID、顺序和任务一致性校验；其持久化语义留给后续 storage Issue。
- `docs/issues/M01_GOVERNANCE_CORE.md` 仍是规划源文件，ISSUE 状态文字仍为 `Planned`；本总结记录实际交付证据，但不替代正式状态管理。
- 开始新 Issue 前仍须按 `AGENTS.md` 和 `docs/ISSUE_REVIEW_STANDARD.md` 重新识别基线、依赖、范围和验收标准。

