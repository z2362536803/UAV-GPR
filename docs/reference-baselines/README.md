# 参考项目基线（Reference Baselines）

版本：1.0
状态：已建立首份只读基线；迁移白名单见 `../REFERENCE_MIGRATION.md` 与 `../adr/0005-reference-authority.md`

本目录以只读、可重复的方式冻结两个参考仓库的候选源：

- `E:\钢筋仪软件开发`（`rebar-inspector`）：core、LibreVNA、校准、处理、storage、B-scan UI。
- `E:\UVA_GPR_system`（`uav-gpr`）：GNSS parser/reader/matcher 契约、HM30 部署文档。

**两个参考目录永远只读。** 本机制不修改、不格式化、不暂存、不提交、不复制参考仓库的任何内容；manifest 只记录路径与 SHA-256 等事实。

## 文件清单

| 文件 | 内容 |
|---|---|
| `spec.json` | 白名单规格：仓库路径、branch/HEAD 采集方式、来源角色、候选文件、排除项、排除 glob 守卫。由人工审查维护。 |
| `manifest.json` | 由工具生成的当前基线记录（规范 JSON，`sort_keys`，条目排序稳定）。 |
| `manifest.md` | 同一记录的 Markdown 可读版本，便于人工审查。 |
| `../REFERENCE_MIGRATION.md` | 迁移规则与权威矩阵（本体另有版本）。 |

## 生成与验证

```powershell
# 重新生成（只读访问参考仓库；任何失败都不会写出文件）
python tools\migration\reference_manifest.py `
  --spec docs\reference-baselines\spec.json `
  --out-json docs\reference-baselines\manifest.json `
  --out-md docs\reference-baselines\manifest.md
```

工具只执行只读 `git` 命令与文件 SHA-256 计算。只要参考仓库的 branch、HEAD、worktree 状态和候选文件内容不变，两次运行的结果除 `generated_at` 外完全一致（由 `tests/unit/test_reference_manifest.py` 的合成仓库测试证明）。

## JSON 格式（schema_version = "1.0"）

顶层：

| 字段 | 含义 |
|---|---|
| `schema_version` | 清单格式版本；不兼容变更必须升级并显式迁移。 |
| `tool.name` / `tool.version` / `tool.python` | 生成工具与解释器版本。 |
| `generated_at` | 生成时间，UTC，ISO 8601（`...Z`）。 |
| `repositories[]` | 每个参考仓库一条记录。 |

每个仓库记录：

| 字段 | 含义 |
|---|---|
| `id` / `name` | 稳定标识与显示名。 |
| `path` | 参考仓库绝对路径（本机开发路径；内容哈希才是可移植事实）。 |
| `branch` | 生成时的 `git rev-parse --abbrev-ref HEAD`；detached 时为 `(detached)`。 |
| `head_sha` | 完整 40 位 HEAD commit SHA-256。 |
| `worktree_dirty` | 是否存在未提交/未跟踪内容的事实。 |
| `worktree_status` | 排序后的 `git status --porcelain=v1` 行。 |
| `exclusions` | 明确排除内容（人读说明）。 |
| `notes` | 该仓库的来源角色与授权边界。 |
| `files[]` | 实际候选源文件：`role`、`path`（相对仓库根，POSIX 分隔）、`tracked_status`、`sha256`。 |

`tracked_status` 取值：`committed`（与 HEAD 一致）、`worktree_modified`、`staged`、`staged_and_modified`、`untracked`。该字段使 manifest 能区分已提交与未提交的源内容。

## 排序与等价

- `files[]` 按 `(role, path)` 排序；`worktree_status` 按行排序；JSON 顶层键按字典序输出。
- 连续运行对未改变输入产出的 manifest，除 `generated_at` 外逐字节等价。
- `generated_at` 是记录事实（何时冻结），不属于内容等价性判断。

## Fail-closed 规则

以下情况工具以非零退出并**不写入任何输出文件**：

- 仓库路径不存在或不是 Git 仓库、HEAD 无法解析；
- 任一候选文件缺失或读取失败；
- spec 结构/版本非法、条目路径重复或含 `..`/绝对路径；
- 候选文件命中 `excluded_globs`（避免白名单误把禁止内容纳入）。

合成临时 Git 仓库测试不需要参考目录写权限，也不触碰真实参考仓库。
