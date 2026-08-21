# ISSUE-NNN：标题

- 状态：Planned
- 里程碑：Mxx
- 直接依赖：ISSUE-xxx
- 映射需求：FR-xxx

## 目标

一句话描述可验证结果。

## 范围

- 明确交付物。

## 排除项

- 明确本 Issue 不做的内容。

## 计划落点

- 预计模块、测试和文档；执行者需根据实际仓库小幅调整，但不能越层。

## 验收标准

- 可观察、可测试、无主观歧义。

## 必测场景

- 正常、边界、错误、取消/恢复和回归。

## DeepSeek Harness 提示词

```text
你在 E:\wurenjiruanjian 中只执行 ISSUE-NNN。
先完整阅读 AGENTS.md、docs/issues/README.md 的通用执行协议和本 Issue 指定文档；检查依赖和 git status。

任务：...
必须交付：...
禁止：...
验收与测试：...

完成后按通用协议报告并停止。不要执行后续 Issue；不要 commit/push，除非调用者明确授权。
```
