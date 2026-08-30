# ISSUE-009 round-3 聚焦复审报告

日期：2026-08-28（round 3 聚焦复审）
审查者：独立 reviewer（非本轮执行者；**只读审查**，除本报告外未修改任何项目文件）
审查对象：ISSUE-009「规范逐道 raw 哈希与黄金向量」round-3 最小修复轮（P3-01 / P3-02 / P3-03）
审查标准：`docs/ISSUE_REVIEW_STANDARD.md` v1.0
上一轮：[ISSUE_009_REVIEW_REPORT_R2.md](ISSUE_009_REVIEW_REPORT_R2.md)（round 2，**PASS WITH CONDITIONS**，3 条 P3）
本轮依据：R2 报告第 3 节 P3-01 / P3-02 / P3-03 与第 10 节最小修复清单

仓库根：`D:\博士任务\无人机软件\UAV-GPR`（历史文档中的 `E:\wurenjiruanjian` 按约定替换）。
**未使用 WSL**；所有 Python 命令均使用 `.venv/Scripts/python.exe`。

---

## 1. 审查结论

**整批结论：PASS WITH CONDITIONS**
**单 Issue 结论：ISSUE-009 → PASS WITH CONDITIONS（round-2 的三条 P3 条件全部关闭）**

| 判定项 | 结论 |
|---|---|
| ISSUE-009 三条验收标准（`docs/issues/M02_STORAGE.md:63-67`） | **全部 PASS**（本轮未触碰 framing，独立复算与 75 项契约测试复验通过） |
| round-1 六条修复项（P1-01/02、P2-01/02、P1-03、P3-01） | **无回退**（独立回归探针 21/21 PASS） |
| round-2 三条 P3（P3-01 测试覆盖 / P3-02 计划正文失实 / P3-03 错误类型不一致） | **P3-01 关闭、P3-02 关闭、P3-03 关闭** |
| 新增问题 | **P0 无 / P1 无 / P2 无 / 新增 1 条 P3**（P3-04，计划文档一处数字未标注基线口径，极低风险、不阻塞） |
| 4 个黄金 expected digest | **一字未改**（mtime 早于 R2 报告；4/4 由独立 reference builder 复算一致） |
| 改动文件集 | **恰好 3 个**（`raw_hash.py`、契约测试、计划文档）；tracked diff 与 R2 记录逐项一致 |
| 是否可交人工验收 | **可以** |
| 是否可放行 ISSUE-010 | **契约与代码层面可以**，但须先完成流程前置（见第 8 节） |

判为 `PASS WITH CONDITIONS` 而非 `PASS` 的唯一原因：本轮新发现 1 条 P3 级文档精度问题（P3-04）。
该问题不落入验收标准、不影响任何运行行为、不阻止合并，但按审查标准第 12 节，存在未关闭的条件项时不得判 `PASS`。

---

## 2. 本轮审查范围

### 2.1 范围锁定

| 项 | 事实（Git / 仓库实测） |
|---|---|
| Issue | ISSUE-009「规范逐道 raw 哈希与黄金向量」 |
| 当前分支 / HEAD | `feat/issue-009` @ `e8525080fc9b5aa00ff24c252ba972809d09b604`（＝ `main` ＝ `origin/main` ＝ merge-base） |
| ISSUE-009 提交 | **无**；未 commit / 未 push / 未 merge / 无 PR |
| 追踪的修改（tracked） | 3 个，与 R2 记录一致：`docs/DATA_FORMAT.md`（+42）、`docs/issues/M02_STORAGE.md`（1 行）、`src/uav_gpr/core/__init__.py`（+12） |
| 未跟踪项目文件 | 与 R2 记录一致，无新增、无减少 |

本轮为**聚焦复审**：只验证 R2 第 3 节的 3 条 P3 是否关闭，以及本轮修复是否引入回归或范围外改动；
不重复 R2 已完成的全部验收矩阵（R2 结论在无反证前提下沿用，本轮对关键项做了独立复验，见第 4 节）。

### 2.2 本轮实际改动文件集（实测，非采信声明）

以 R2 报告的写入时刻（mtime `2026-08-28 19:21:06`）为基准，全仓库 `src/ tests/ docs/ tools/` 下
`*.py / *.md / *.json / *.toml` 中**更新于该时刻之后的文件恰好 3 个**：

```text
2026-08-28 19:26:46  src/uav_gpr/core/raw_hash.py
2026-08-28 19:27:02  tests/contract/test_raw_trace_hash.py
2026-08-28 19:29:59  docs/plans/2026-08-28-issue-009-raw-hash.md
```

对照文件（**均早于 R2 报告，本轮未动**）：

```text
2026-08-28 14:03:05  tests/contract/raw_trace_hash_golden.json   ← 黄金 manifest
2026-08-28 14:27:18  src/uav_gpr/core/__init__.py
2026-08-28 17:57:36  docs/DATA_FORMAT.md
2026-08-28 17:57:41  docs/issues/M02_STORAGE.md
2026-08-27 14:29:16  src/uav_gpr/core/errors.py                 ← 未新增错误码的结构性保证
```

结论：改动边界与执行者声明**完全一致**，未触碰 core 其他模块、ISSUE-008 存储契约与 ISSUE-010 范围。

---

## 3. 三条 P3 逐条关闭判定

### 3.1 P3-01：`TestFieldSensitivity` 缺失的 3 类变化敏感测试 —— **关闭**

#### 3.1.1 存在性与断言形态（读码确认）

| 测试 | 行号 | 变化点 | 断言 |
|---|---|---|---|
| `test_channel_id_content_change_changes_digest` | `tests/contract/test_raw_trace_hash.py:320-339` | 通道**顺序不变**，仅第二通道 `channel_id` `vv_s22` → `vv_s21` | `digest != base_digest` |
| `test_frequency_point_count_change_changes_digest` | `:341-353` | 频率点数 8 → 9，raw shape 同步 `(2,8)` → `(2,9)` | `digest != base_digest` |
| `test_raw_imaginary_value_change_changes_digest` | `:355-369` | `raw[0, 0] += 1.0j`（仅虚部） | `digest != base_digest` |

三项均复用了既有 `base_digest` fixture（`:237-248`，2 通道 × 8 频点），只改动单一维度，断言与既有 6 项同形。

#### 3.1.2 独立变异验证（不修改项目文件）

方法：在系统临时目录 `%TEMP%\uav-gpr-r3-probe\` 编写 pytest 插件 `r3_mutate.py`，在**测试模块被导入之前**替换
`uav_gpr.core.raw_hash` 的模块级内部函数（`_frame_text` / `_frame_c128`）或重绑定 `compute_raw_trace_sha256`，
再用项目自身的契约测试文件运行（`PYTHONPATH` 指向临时目录，`-p r3_mutate`）。生产代码零改动，插件不落在项目内。

| 变异 | 实现方式 | 定向测试文件实测结果 | 被杀死的测试 |
|---|---|---|---|
| **M0 自检** `R3_MUT=none` | 不启用变异 | **75 passed** | —（证明探针本身不改变行为） |
| **M1** 破坏 channel_id 编入 | `_frame_text` 对非 UUID 文本（即 channel_id）返回 `b""` | **7 failed, 68 passed** | ✅ `TestFieldSensitivity::test_channel_id_content_change_changes_digest`（`:339` 断言失败）<br>以及 `test_channel_order_change`、`test_channel_split_ambiguity`、4 项黄金对拍 |
| **M2** 只哈希实部 | `_frame_c128` 改为 `.real.astype("<f8").tobytes()` | **5 failed, 70 passed** | ✅ `TestFieldSensitivity::test_raw_imaginary_value_change_changes_digest`（`:369` 断言失败）<br>以及 4 项黄金对拍 |
| **M3** 冻结频点数 framing | 重建 `compute_raw_trace_sha256`，去掉 `uint64be(frequency_count)` 字段 | 全文件 **4 failed, 71 passed**；`-k "channel_id_content or frequency_point_count or raw_imaginary"` → **3 passed** | ❌ 三项新测试**均未**杀死该变异；由 **4 项黄金对拍**杀死 |
| **M4** 去掉 channel_count framing（对照） | 同上，去掉 `uint64be(channel_count)` | **4 failed, 71 passed** | 仅 4 项黄金对拍杀死 |

#### 3.1.3 对 M3 结果的判定（重要，如实记录）

`test_frequency_point_count_change_changes_digest` **不能**杀死「去掉 `frequency_count` framing 字段」这一变异。
原因不是测试写得不对，而是该字段在结构上**冗余**：对任意两个合法输入，只要 `frequency_count` 不同，
频率轴字节（`8 × count` 字节）或 raw 字节（`16 × channel_count × count` 字节）必然不同，
因此即使不编码该字段，digest 也必然改变——**任何黑盒测试都无法区分**，不存在可构造的反例。
R2 第 3 节 P3-01 的担忧原文是「若未来有人错改 channel_id 或 frequency_count 的 framing，现有测试不会捕获」；
M3 实测证明：`frequency_count` framing 一旦被改，**4 个黄金向量立即失败**，即该担忧已不成立。

因此：

- P3-01 的三项测试**真实存在、断言非空泛**（M1 / M2 均被对应测试杀死）；
- 计划 §7.2 自定的测试矩阵原文「频率点数变 → digest 变」已被满足；
- `frequency_count` framing 的回归保护由黄金向量承担（M3 实证），**不构成本轮缺陷**。

**判定：P3-01 关闭。**

### 3.2 P3-02：计划文档正文失实 —— **关闭**

#### 3.2.1 逐项事实核对（以仓库实测为准）

| 计划文档声明 | 位置 | 仓库实测 | 结论 |
|---|---|---|---|
| 分支 `feat/issue-009` @ `e852508` | `:62`、`:99` | `git branch --show-current` = `feat/issue-009`；`git rev-parse HEAD` = `e8525080…` | ✅ 一致 |
| 环境 Windows `.venv` / Python 3.13、**WSL 已禁用** | `:107-112` | Windows 11 LTSC 2024；`.venv/Scripts/python.exe` = **3.13.14**；numpy 2.5.2 / pytest 8.4.2 / ruff 0.16.5 / mypy 1.20.2 / h5py 3.16.0；`wsl` 未使用 | ✅ 一致（补丁版本见第 6 节差异 1） |
| 门禁 372 passed / 1 deselected | `:11`、`:114-122` | §4 明确标注为「**当前门禁基线（进入实现前复测）**」；本轮实测全量 **376 passed / 1 deselected**（＝ 372 + 本轮新增 4 项） | ⚠️ 见 P3-04 |
| M02 中 ISSUE-009 状态 `Review` | `:61`、`:273-275` | `docs/issues/M02_STORAGE.md:44` = `状态：Review（round 2 最小修复完成，等待独立复审；仅人工验收后置 Done）` | ✅ 一致 |
| 定向契约测试 71 → **75** | `:11`、`:124-125`、`:227` | 实测 `75 passed`，`grep -c "    def test_"` = 75 | ✅ 一致 |
| `docs/DATA_FORMAT.md` 新增 5.1 节（+42 行，未改其他节） | `:270-272` | `git diff --stat` = 42 insertions, 0 deletions；diff 内容仅 `@@ -171,6 +171,48 @@` 一处在第 5 节之后 | ✅ 一致 |
| ISSUE-008 回归 59 passed；core 隔离 1 passed | `:126-127` | 实测 `test_storage_schema.py` **59 passed**；`test_core_isolation.py` **1 passed** | ✅ 一致 |
| §7.2 声称的 4 项新增测试 | `:228-231` | 全部存在（`:320`、`:341`、`:355`、`:534`） | ✅ 一致 |
| §0.4 黄金向量未变 | `:45-49` | 见第 4.1 节 | ✅ 一致 |

#### 3.2.2 第 0 节是否未改

- 结构核对：第 0 节仍为 **0.1 纠错说明 / 0.2 真实修复记录 / 0.3 决策记录 / 0.4 黄金向量不变声明** 四小节，
  与 round-2 执行者提交给 captain 的完成报告所述结构（「0.1 纠错说明原虚假日志→0.2 真实修复记录→0.3 决策→0.4 黄金不变声明」，
  `.agent-teams/uav-gpr-issue-009-r2/inbox/captain.jsonl` 第 3 条）一致；
- §0.2 表中列出的全部测试名与错误码经本轮核对**仍然成立**（详见第 4.3 节行号漂移分析）；
- **限制**：计划文档从未入库，仓库内不存在 round-2 时刻的副本（`.agent-teams/` 日志中也无 §0 原文），
  因此**无法做逐字节比对**。按审查标准第 10 节记为「**未发现反证 / 不可独立验证**」，不作为缺陷。
- §8 已明确声明「第 0 节保持原样未改」（`:276-277`），与上述证据无矛盾。

**判定：P3-02 关闭**（附 1 条 P3-04 精度建议，见第 5 节）。

### 3.3 P3-03：`_validate_channels` 非 `ChannelSpec` 元素的错误类型 —— **关闭**

#### 3.3.1 代码证据

`src/uav_gpr/core/raw_hash.py:96-104`：

```python
for channel in result:
    if not isinstance(channel, ChannelSpec):
        # Structured DomainError (not a bare TypeError): callers must be
        # able to branch on ErrorCode for every validation path.
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "channels must contain ChannelSpec",
            {"channel_type": type(channel).__name__},
        )
```

- 全文件 `grep TypeError` 仅命中 `:98` 的**注释**（`Structured DomainError (not a bare TypeError)`），无 `raise TypeError` 残留；
- 未新增错误码：`src/uav_gpr/core/errors.py` 为 tracked 且 `git diff` 为空，`ErrorCode` 仍为 **21 个成员**（探针实测枚举），复用既有 `INVALID_ARGUMENT`；
- 影响面：`_validate_channels`（`raw_hash.py` 私有副本）仅被 `compute_raw_trace_sha256`（`:294`）与 `RawHashSpec.__post_init__`（`:386`）调用，与执行者声明一致。

#### 3.3.2 一次性探针实测（`%TEMP%\uav-gpr-r3-probe\r3_probe_p3_03.py`，**15 项 15 PASS，exit 0**）

| 探针 | 结果 |
|---|---|
| `compute_raw_trace_sha256(channels=(x,))`，`x` ∈ `str / int / None / object() / tuple / bytes` | 6/6 抛 `DomainError`，`code is INVALID_ARGUMENT`，`context["channel_type"]` 精确等于 `type(x).__name__` |
| 混合列表（合法 `ChannelSpec` 在前、`"bad"` 在后） | 抛 `DomainError(INVALID_ARGUMENT)`，`channel_type == "str"` |
| `RawHashSpec(channels=(x,))`，`x` ∈ `str / int / object()` | 3/3 抛 `DomainError(INVALID_ARGUMENT)`，上下文保留（P3-03 影响的**两条**路径均已覆盖） |
| 空 channels → `invalid_argument`、重复 channel_id → `duplicate_channel` | 均未降级、未误伤 |
| 正常路径仍产出 64 位小写 hex | PASS |
| `ErrorCode` 成员枚举（21 个，无新增） | 记录留存 |

#### 3.3.3 无旧行为依赖方（静态搜索）

- `grep -rn "compute_raw_trace_sha256\|RawHashSpec\|validate_raw_hash" --include=*.py src tests tools`：
  除 `raw_hash.py` 自身与 `tests/contract/test_raw_trace_hash.py` 外，**唯一命中是 `src/uav_gpr/core/__init__.py:36-41,90,100,105` 的再导出**，无任何真实调用者。
- `grep -rn "except TypeError\|except (.*TypeError" --include=*.py src tests`：命中 2 处，
  `src/uav_gpr/core/config.py:842`（`_require_json_safe` JSON 安全）与 `src/uav_gpr/storage/rcscan_v2.py:1181`
  （`_build_mission_config` 重建），**均与 channels 校验无关**，不依赖被改掉的 `TypeError`。
- mypy strict：`Success: no issues found in 30 source files`（`mypy 1.20.2`，配置来自 `pyproject.toml`）。

**判定：P3-03 关闭**（仍 fail-closed、上下文保留、无新增错误码、无调用方回退、类型检查通过）。

> **跨 Issue 观察（非本轮引入，不评级为缺陷）**：`src/uav_gpr/core/frequency.py:41-51` 存在同名私有
> `_validate_channels`，对非 `ChannelSpec` 元素**仍抛 `TypeError`**（被 `config.py:311`、`frequency.py:225/293`、
> `time_domain.py:675` 使用）。该处属 ISSUE-004～006 既有实现，本轮未改动（`errors.py`/`frequency.py`/`config.py`/
> `time_domain.py` 均无 diff），**不构成回归**，但 P3-03 提出的「结构化错误一致性」在该路径上仍然不一致。
> 建议后续 Issue 或独立小修统一，已列入第 7 节剩余风险。

---

## 4. 回归与范围检查

### 4.1 黄金 manifest 未变（**核心回归项**）

| 检查 | 证据 |
|---|---|
| 文件未被本轮触碰 | mtime `2026-08-28 14:03:05`，**早于** R2 报告 `19:21:06`；`find -newer` 命中 0 |
| 行数 | 309 行，与 R2 记录（309）一致 |
| 文件字节摘要 | `sha256(...) = 6c8760493d9bf1125fa6984c7b96f456702456c805c99131390cef6a880df4be` |
| 4 个 expected digest | `1a519053…c0abfa57`、`9c2081f4…f5a99f0`、`8c103dda…e2365d7`、`b1a60d2b…51ccd51`，与 R1/R2 报告记录前缀逐一相符 |
| **独立复算** | 临时目录 `r3_probe_golden.py`，**只用 `hashlib` + `struct` + `numpy` 按 DATA_FORMAT 5.1 重实现，不 import 生产 framing**：**4/4 与 manifest 逐字符一致**（exit 0） |
| manifest 自描述 | `format_name=rcscan-raw-trace-hash`、`algorithm=sha256`、`magic=UAVGPR-RAW-SHA256`、`hash_version=1`、`spec_version=1` 均未变 |

### 4.2 门禁实测（工作目录 = 仓库根，解释器 = `.venv/Scripts/python.exe`）

| 命令 | 退出码 | 实测结果 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest tests/contract/test_raw_trace_hash.py -q` | 0 | **75 passed**，0 failed / 0 skipped |
| `.venv/Scripts/python.exe tools/quality/verify.py` | 0 | pytest 非硬件 **376 passed, 1 deselected** → ruff ok → mypy ok → package import ok → **`all gates passed`** |
| `.venv/Scripts/python.exe -m ruff check .`（ruff 0.16.5） | 0 | `All checks passed!` |
| `.venv/Scripts/python.exe -m mypy src`（mypy 1.20.2 compiled） | 0 | `Success: no issues found in 30 source files`（strict） |
| `.venv/Scripts/python.exe -m pytest tests/contract/test_storage_schema.py -q` | 0 | **59 passed**（ISSUE-008 无回归） |
| `.venv/Scripts/python.exe -m pytest tests/unit/test_core_isolation.py -q` | 0 | **1 passed**（core 隔离守卫） |
| `git diff --check` | 0 | 无 tracked whitespace 错误 |

- `376 = 372 + 4`：与「本轮新增 4 个测试」完全自洽；
- `1 deselected` 为 hardware 双重 opt-in sentinel（`docs/TESTING.md:54-71`），属预期；
- 变异自检 `R3_MUT=none` 下定向测试同为 **75 passed**，证明探针机制本身不污染结果。

### 4.3 round-1 / round-2 修复项无回退（独立探针 21/21 PASS）

临时目录 `r3_probe_regression.py`（不复用生产测试代码），**21 项全 PASS，exit 0**：

| 组 | 覆盖 |
|---|---|
| round-1 反例 1（P1-01） | `np.uint64([2,1])` → `non_increasing_axis` |
| round-1 反例 2（P2-01） | `trace_index=2**64` → `out_of_range`；反向验证 `2**63-1` **仍被接受**（无过度拒绝） |
| round-1 反例 3（P1-02） | `shares_memory=False`、`writeable=False`（axis 与 data）、改源后 `compute()`/`__hash__` 稳定 |
| round-1 反例 4（P2-02） | `from_dict` 拒绝未知键 `unexpected`/`future_field` → `invalid_argument`；`spec_version=2` → `unsupported_schema_version` |
| fail-closed 抽验 | 非规范/大写 UUID → `invalid_uuid`；`trace_uid` 类型错 / bool index / 空 channels / 空轴 → `invalid_argument`；2-D 轴 → `axis_mismatch`；NaN 轴 → `non_finite_axis`；字符串 raw → `dtype_mismatch`；1-D raw / shape 不符 → `shape_mismatch` |
| 正常路径 | 仍产出 64 位小写 hex |

### 4.4 「未删除 / 未削弱既有测试」的字节级证据（本轮最强证据）

`raw_hash.py` 与契约测试均为 untracked，无法用 `git diff` 证明改动边界。审查者改用两条独立取证：

**(a) 字节码头部取证（PEP 552 timestamp pyc）**

仓库内存在 **CPython 3.12** 时代遗留的 `raw_hash.cpython-312.pyc`（编译于 `2026-08-28 17:57:25`，
早于 R2 报告），其头部记录的源 mtime / 源大小即为 round-2 时刻的源文件状态：

| 文件 | round-2 源大小（py312 pyc 头部） | 当前源大小 | 增量 |
|---|---:|---:|---:|
| `src/uav_gpr/core/raw_hash.py` | 26 590 B（源 mtime 17:57:22） | 26 810 B / 686 行 | **+220 B / +4 行** |
| `tests/contract/test_raw_trace_hash.py` | 40 776 B（源 mtime 17:55:44） | 43 555 B / 1123 行 | **+2 779 B / +67 行** |

**(b) 增量逐字节可完全解释**

- `raw_hash.py`：把旧的 `raise TypeError(...)` 三行替换为新的 `DomainError(...)` 七行（含 2 行注释），
  按 LF 计算的长度差 = **346 − 126 = 220 B**，与实测增量 **220 B 精确相等**
  ⇒ 该文件除 P3-03 这一处外**没有任何其他改动**，无范围外修改。
- 契约测试：新增块 `320-370`（3 个敏感性测试 + 1 个空行）= 1 972 B，`534-549`（1 个 fail-closed 测试 + 1 个空行）= 807 B，
  合计 **2 779 B**，与实测增量 **2 779 B 精确相等**
  ⇒ 该文件为**纯插入**，其余字节一字未改，不可能存在删除、改名或断言放宽。

**(c) 行号漂移一致性（交叉验证 (b)）**

R2 报告记录的 10 处测试行号，到当前文件的位移全部落在 `{+51, +67}` 两个值上，且与两次插入的位置、长度严格自洽：

| R2 记录 | 当前 | 位移 |
|---|---|---|
| `test_mission_id_wrong_type_rejected` 412 | 463 | +51 |
| `test_trace_uid_wrong_type_rejected` 424 | 475 | +51 |
| `test_unsigned_descending_axis_rejected` 530 | 597 | +67（= +51 +16） |
| `test_unsigned_extreme_descending_axis_rejected` 547 | 614 | +67 |
| `test_signed_overflow_descending_axis_rejected` 561 | 628 | +67 |
| `test_conversion_collapse_axis_rejected` 575 | 642 | +67 |
| `test_trace_index_int64_bound_rejected` 589 | 656 | +67 |
| `test_trace_index_uint64_overflow_rejected` 603 | 670 | +67 |
| `TestRawHashSpecImmutability` 741-802 | 808-869 | +67 |
| `TestHashMetadata` 936-1056 | 1003-1123 | +67 |

⇒ 无一条 R2 记录的测试消失或被移位到不一致的位置；`48 → 44 → 71 → 75` 的历史演进在本轮段（71 → 75）**可完整解释**。

**(d) 弱化扫描**：`xfail` / `skip` / `pytest.raises((…,…))` / `TODO` / `FIXME` 在 `raw_hash.py` 与契约测试中**命中 0**。

### 4.5 范围外改动核查

| 检查 | 结果 |
|---|---|
| 未提前实现 ISSUE-010 | ✅ `grep -rn "def write_trace\|def checkpoint\|def finalize" --include=*.py src` → **无命中**；`M02_STORAGE.md:80` ISSUE-010 状态仍为 `Planned`；`raw_hash.py` 无 h5py / 文件 I/O |
| 未触碰 ISSUE-004～006 core 模块 | ✅ `channels.py` / `frequency.py` / `metadata.py` / `gnss.py` / `config.py` / `errors.py` / `time_domain.py` 均不在本轮 mtime 名单内，`git status --porcelain` 无输出 |
| 未触碰 ISSUE-008 存储契约 | ✅ `storage/rcscan_v2.py` 未改；`test_storage_schema.py` 59 passed |
| M02 状态未被回退 | ✅ 仍为 `Review`，未提前置 `Done` |
| Git 状态未被改动 | ✅ HEAD 仍 `e852508`，无 commit / push / merge / reset；`git status --porcelain` 与 R2 记录逐项一致，无新增未跟踪文件 |
| 缓存污染 | 仅既有 ignored 目录（`.pytest_cache/`、`.ruff_cache/`、`.mypy_cache/`、`__pycache__/`）被更新；探针脚本与 `--basetemp` 全部落在系统临时目录 |
| 审查者改动 | **除本报告外未修改项目任何文件** |

---

## 5. 发现的新问题

### P0 / P1 / P2

无。

### P3-04（新增）：计划文档文首把「round-3 开工前基线 372」与「当前门禁」并列表述，未标注口径

- **所属 Issue**：ISSUE-009（文档精度）
- **文件与行号**：`docs/plans/2026-08-28-issue-009-raw-hash.md:9-12`（文首说明块第 3 行）：
  「round 3 已按仓库实测据实改写：当前分支 `feat/issue-009`、Windows `.venv` / Python 3.13、
  **门禁 372 passed / 1 deselected**、M02 中 ISSUE-009 状态 `Review`、定向契约测试 **71 → 75**。」
- **触发条件**：同一份文档的 §4（`:114-122`）已明确标注 372 是「**当前门禁基线（进入实现前复测）**」，
  而文首块把它与「定向 71 → 75」并列且未加口径限定；本轮新增 4 项测试后，**当前**全量门禁实为 **376 passed / 1 deselected**。
- **实际影响**：极低。计划文档非交付契约（权威契约是 `DATA_FORMAT.md` 5.1 + 代码 + 测试 + 黄金 manifest），
  且 §4 内部表述自洽；仅只读文首的读者可能误以为当前门禁仍是 372。不影响任何验收标准与运行行为。
- **违反要求**：`docs/ISSUE_REVIEW_STANDARD.md` 第 10 节完成报告真实性核对（轻微）。
- **最小修复方向**：把文首该行改为「门禁 372 passed / 1 deselected（round-3 开工前基线；补 4 项测试后为 376）」，
  一行即可；不得改动第 0 节，不得改动其他已核实数字。

### 不评级观察（非缺陷，仅记录）

1. `frequency_count` framing 字段结构上冗余（M3 无法被任何黑盒测试杀死），其回归保护由 4 个黄金向量承担——实测有效。
2. `src/uav_gpr/core/frequency.py:41-51` 的同名 `_validate_channels` 仍抛 `TypeError`，与 P3-03 的修复方向不一致；
   属 ISSUE-004～006 既有实现，本轮未触碰，非回归。

---

## 6. 报告与事实差异

1. **可复现（与执行者声明一致）**：定向 75 passed；全量 376 passed / 1 deselected；ruff 0.16.5 通过；
   mypy strict 30 files 通过；package import ok；`verify.py` all gates passed；`git diff --check` exit 0；
   ISSUE-008 回归 59 passed；core 隔离 1 passed；M02 状态 `Review`；分支 `feat/issue-009` @ `e852508` 无 commit/push；
   改动文件恰为 3 个；黄金 manifest 未改且 4/4 独立复算一致。
2. **环境事实差异（沿用 R2 记录，非本轮引入）**：任务声明写 Python **3.13.12**，实测
   `.venv/Scripts/python.exe` = **Python 3.13.14**（MSC v.1944 64 bit）。补丁版本差异，不影响结论；
   计划文档 §4（`:108-109`）已如实写明 3.13.14 并记录该差异，**处置得当**。
3. **执行者声明「第 0 节未改」——不可独立验证**：计划文档从未入库，仓库与 `.agent-teams/` 日志中均无
   round-2 时刻的 §0 文本副本，无法逐字节比对。现有证据（四小节结构一致、§0.2 全部声明经本轮复核仍成立、
   §8 自述未改）**未发现反证**，按审查标准第 10 节记为「不可独立验证」，不作缺陷处理。
4. **执行者声明「因无生产改动故无红灯，改用临时目录探针（7/7）证明非空泛」——部分不可复现**：
   修复后代码已就位，审查者无法在不修改代码的前提下复现红灯。审查者以**等价且更强**的方式独立验证：
   用临时目录 pytest 插件对生产函数做变异（M1/M2/M3/M4 + M0 自检），确认目标测试确实会被杀死（见 3.1.2）。
   执行者所述「7/7 探针」本身记为「未发现反证」。
5. **R2 遗留的测试数量差异（48 → 44）**：本轮段（71 → 75）已由第 4.4 节字节级证据完整解释；
   48 → 44 段仍不可追溯（R2 第 7 节第 4 条已记录），本轮无新证据，维持原判。
6. **报告与仓库冲突**：无。本轮未发现执行者声明与仓库事实相冲突的条目（除第 4 条不可复现项与 P3-04 精度项）。

---

## 7. 剩余风险

1. **未提交工作树（中低，沿用 R2）**：所有交付仍在 `feat/issue-009` 上**零提交**。建议在人工验收前
   形成一次只含 ISSUE-009 文件的提交并入 `main`，使后续演进可审计、ISSUE-010 有基线可用。
2. **计划文档 §0 不可字节比对（低）**：见第 6 节第 3 条。若需彻底消除，应在提交时保留 round-2 版本作为基线。
3. **黄金向量生成过程不可完全追溯（低，沿用 R2）**：manifest 记录 generator 名称/版本/seed，生成器源码未入库；
   4 个 digest 已由本轮独立 builder **4/4 复算**，数值风险已消除，生成过程仍只能记为「未发现反证」。
4. **raw 复数 NaN/Inf 语义未冻结（低，沿用 R2）**：`_validate_data`（`raw_hash.py:165-190`）只校验 dtype/shape，
   非有限复数值会被接受并进入规范字节；若后续允许非有限 raw，须**递增 `RAW_HASH_VERSION`** 并同步文档。
5. **numpy 整数 `trace_index` 会被拒绝（低，跨 Issue 提示，沿用 R2）**：`np.int64/np.int32/np.uint64` 一律
   `invalid_argument`（与 `core/metadata.py` 既有契约一致，非本 Issue 引入）。**ISSUE-010/011 从 HDF5 `<i8` 列
   回读 `trace_index` 后重算哈希时必须显式 `int()` 转换。**
6. **`frequency_count` framing 无黑盒回归保护（低，本轮新观察）**：该字段被误删只能由黄金向量捕获（已实证有效），
   任何黑盒用例都无法覆盖。若未来希望显式钉住，可考虑增加一条**字节流结构断言**测试（不属于本轮范围）。
7. **`core/frequency.py::_validate_channels` 结构化错误不一致（低，跨 Issue）**：见第 3.3.3 节观察；
   建议后续 Issue 或独立小修统一为 `DomainError(INVALID_ARGUMENT)`，不在 ISSUE-009 内处理。
8. **`RawHashSpec` 内存开销（低，沿用 R2）**：`_immutable_array` 对 axis 与 data 各做一份副本；
   ISSUE-014 若对十万道逐道构造 spec，应评估改为流式即时计算。
9. **P3-04 文档精度（极低）**：见第 5 节，一行可修，不阻塞。

---

## 8. 合并建议

**可以合并；ISSUE-009 可以交人工验收；ISSUE-010 契约与代码层面可以放行。**

- **质量层面**：三条验收标准全部 PASS，round-1 六条修复项无回退（独立探针 21/21），
  round-2 三条 P3 全部关闭，黄金 manifest 一字未改且 4/4 独立复算一致，无 P0/P1/P2，
  仅余 1 条 P3 文档精度项（P3-04）。
- **流程层面（放行 ISSUE-010 的前置条件）**：
  1. 项目负责人确认本报告；
  2. 处理或明确接受 P3-04（一行修订）；
  3. 将 ISSUE-009 的 6 个项目文件（+ 本报告等文档）形成**一次只含 ISSUE-009 的提交**并 merge 进 `main`
     ——否则 ISSUE-010 的分支基线上不存在 `raw_hash.py`，无法开工；
  4. 人工验收通过后方可把 M02 中 ISSUE-009 状态由 `Review` 置 `Done`（**当前不得提前置 `Done`**）。
- **ISSUE-010 开工提示**：`trace_index` 从 HDF5 列回读为 numpy 整数，调用哈希前必须 `int()` 转换（第 7 节第 5 条）。
- **不建议拆分合并**：生产模块、黄金 manifest、测试与 `DATA_FORMAT` 5.1 文档构成同一契约，不可分。

### 最小修复清单（仅供项目负责人决定，审查者不执行）

1. **（P3-04，可选）** 修订 `docs/plans/2026-08-28-issue-009-raw-hash.md:11`：
   把「门禁 372 passed / 1 deselected」标注为 round-3 开工前基线，并补「补 4 项测试后为 376」。不得改第 0 节。
2. **（流程，建议）** 人工验收前形成一次 ISSUE-009 独立提交并入 `main`。
3. **（跨 Issue，可选）** 统一 `core/frequency.py:41-51` 的 `_validate_channels` 错误类型（不在 ISSUE-009 内）。

---

审查结束后立即停止。除本报告 `docs/reports/ISSUE_009_REVIEW_REPORT_R3.md` 外，
**审查者未修改项目任何文件**（未改实现、测试、黄金 manifest、计划、M02 状态、Git 状态；
未 commit / push / merge / 切分支 / reset）。
审查期间运行的 pytest / ruff / mypy 仅更新既有 ignored 缓存目录
（`.pytest_cache/`、`.ruff_cache/`、`.mypy_cache/`、`__pycache__/`），均已被 `.gitignore` 覆盖，不得进入提交。
变异插件、P3-03 探针、黄金独立复算脚本与回归探针均位于系统临时目录
`%TEMP%\uav-gpr-r3-probe\`（`r3_mutate.py`、`r3_probe_p3_03.py`、`r3_probe_golden.py`、`r3_probe_regression.py`），
pytest `--basetemp` 亦指向该目录，**未在项目内留下任何文件**。
未尝试 `wsl`，未安装任何依赖，未改动 `.venv/`。
