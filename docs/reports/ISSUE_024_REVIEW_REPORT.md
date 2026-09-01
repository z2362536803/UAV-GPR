# ISSUE-024 独立复审报告：GGA/RMC NMEA 解析器

- 审查日期：2026-09-02
- 审查者：AgentTeams `uav-gpr-issue-024-nmea` 成员 reviewer（任务 t3，attempt c55dc9af-b7c1-4346-a058-4ebe636cb5c8）
- 审查依据：docs/ISSUE_REVIEW_STANDARD.md v1.0、AGENTS.md、docs/issues/README.md、docs/issues/M05_GNSS.md（ISSUE-024）、docs/GNSS.md、docs/REFERENCE_MIGRATION.md、ADR-0005、t1 基线确认单（docs/reports/ISSUE_024_BASELINE_CONFIRMATION.md）、t2 计划文档（docs/plans/2026-09-02-issue-024-nmea.md）
- 审查性质：全程只读；未修改实现/测试/计划/M05/Git 状态；未 commit/push/merge；变异探针以无落盘内联脚本执行，项目内零残留；本报告为唯一新增文件。

## 1. 审查结论

**VERDICT: PASS**

ISSUE-024 的 3 条验收标准（南/西半球、跨午夜、坏 checksum、无 fix、越界字段结果明确；GGA MSL 不标为 AGL、空字段保持空而非 0；parser 无 Qt/serial 依赖）全部由代码与测试证据满足；t2 声称的测试命令与数字全部独立复现；无 P0/P1/P2 问题；发现 1 项 P3（非阻塞，见 §3/§10）。可进入自动合并。

## 2. 自动识别的审查范围

| 项 | 事实 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-024 GGA/RMC NMEA 解析器（M05 L5–40） | docs/issues/M05_GNSS.md |
| 基线 | `main` @ `0b477ab99654a0b0916e1fea74aaf117a59f4701`（t1 锁定，复核时 HEAD 未变） | `git rev-parse HEAD`；git reflog |
| 分支/提交 | 当前 `main`，与 origin/main 同步 0/0；t2 未 commit/push（工作树交付） | `git status --porcelain=v1 -b` = `## main...origin/main`；reflog 无 reset/rebase/amend/强推 |
| 改动文件（t2） | 4 个精确路径：`src/uav_gpr/positioning/nmea.py`（700 行，新）、`tests/contract/test_nmea.py`（497 行，新）、`docs/plans/2026-09-02-issue-024-nmea.md`（新）、`docs/issues/M05_GNSS.md`（仅 L7 状态行 Planned→Review） | git status；`git diff docs/issues/M05_GNSS.md`（仅状态行 1 行改动） |
| 契约一致性 | t2 登记 changedPaths 与 inScope 为同一 4 个精确路径、逐一相等（无 glob） | .agent-teams team.json t2 记录；工作树实测仅有上述 4 项 + t1 交付物 `docs/reports/ISSUE_024_BASELINE_CONFIRMATION.md` |
| 依赖 | ISSUE-001/005 已合入 main（`2c5b379`/`6b74dcf`、`952883e`/`b11e741`），依赖测试本次定向复跑通过 | 本次复跑 120 passed（见 §6） |
| 排除项核查 | 无串口/缓存/trace 匹配/地图代码；`src/uav_gpr/positioning/` 仅 `__init__.py`（未改动）+ `nmea.py`；core/** 零改动；未进入 ISSUE-025/026 | grep 与 git status 实测 |
| 参考源审计 | 旧 UAV-GPR `uav_gpr_qt.py` SHA-256 `b94d3bfc…`（ISSUE-001 manifest 白名单冻结）；本环境不可达（E: 盘未挂载），t2 按契约重实现 + 匿名/合成夹具，计划文档 §7 已记录哈希与排除内容 | 计划文档 L75–79；t1 基线单 §3.5-2 |

## 3. 主要问题（P0 → P3）

- **P0 / P1 / P2：无。**
- **P3-1 纬度/经度半球字母未按轴区分校验**（ISSUE-024，`src/uav_gpr/positioning/nmea.py:534-539`、`nmea.py:500-509`）：`_parse_coordinate` 仅校验半球字母 ∈ {N,S,E,W}，未要求纬度只用 N/S、经度只用 E/W。变异探针实测：`$GPGGA,120000,4807.038,E,01131.000,N,1,…`（checksum 正确）被接受并产出正坐标（lat=48.1173, lon=11.5166）。真实接收机不会发出此类语句，验收标准不受影响，故为非阻塞改进项。
  - 触发条件：手工篡改/损坏的语句把 E 用于纬度或 N/S 用于经度。
  - 实际影响：本应拒绝的语义非法语句被静默接受；无数据破坏风险。
  - 违反的要求：M05「校验 checksum/长度/范围」的严格解析精神（半球字母合法性已校验，仅缺轴向限定）。
  - 最小修复方向：`_parse_coordinate` 增加 per-axis 合法半球集合参数（lat→{N,S}，lon→{E,W}），非法组合抛 `malformed_field`；补一条失败测试后实现（≤10 行）。

## 4. 逐 Issue 验收矩阵（ISSUE-024）

| # | 验收标准 | 状态 | 代码证据 | 测试证据 |
|---|---|---|---|---|
| 1 | 南/西半球结果明确 | PASS | `nmea.py:571-572`（S/W 取负）；`nmea.py:512-573` | `tests/contract/test_nmea.py:151-159`（GGA 西经、GGA 南纬）、`test_nmea.py:210-216`（RMC 南纬+西经） |
| 2 | 跨午夜组合策略明确 | PASS | `nmea.py:263-306`（12h 容差：delta<−12h→+1 天，>+12h→−1 天）；计划 D6 | `test_nmea.py:338-349`（前后双向翻转）、`test_nmea.py:435-437`（组装层） |
| 3 | 坏 checksum 结果明确 | PASS | `nmea.py:223-236`（缺失→missing_checksum，非 hex→malformed_field，不符→bad_checksum） | `test_nmea.py:238-249`；变异探针：单 hex/3 hex/非 hex 均 `malformed_field` |
| 4 | 无 fix 结果明确 | PASS | `nmea.py:329-345`（quality 0 → valid=False、invalid_reason=NO_FIX、坐标丢弃、sats/hdop/MSL 保留）；`nmea.py:398-403`（quality 有效但无坐标→拒绝） | `test_nmea.py:185-190`、`407-427` |
| 5 | 越界字段结果明确 | PASS | `nmea.py:120-137`（time）、`nmea.py:558-570`（坐标分钟<60、|lat|≤90、|lon|≤180）、`nmea.py:601-655`（sats 0–99、HDOP≤99.9、course<360）、`nmea.py:658-679`（非法日历日） | `test_nmea.py:289-325`（参数化 5 项 + course 360 + 非法日期等）；变异探针：lon 分钟 60、quality 9、lat 90.001、29-Feb-2001/30-Feb-2000 均拒绝；lat 90/lon 180 边界接受 |
| 6 | GGA MSL 不标为 AGL | PASS | `nmea.py:336/352`（仅写入 `GnssFix.altitude_msl_m`/`geoid_separation_m`，模型 `gnss.py:79-80` 无 AGL 字段；`nmea.py:15` 仅文档性说明） | `test_nmea.py:453-457`（`not hasattr(fix,"altitude_agl_m")`）；grep：src 无 AGL 字段定义 |
| 7 | 空字段保持空而非 0 | PASS | `nmea.py:601-655`、`nmea.py:658-660`（空串→None）；`nmea.py:471`（speed None 不乘系数） | `test_nmea.py:178-183`、`192-195`、`221-225`、`407-416` |
| 8 | parser 无 Qt/serial 依赖 | PASS | `nmea.py:27-36` import 仅 stdlib + `uav_gpr.core`；无 serial/readline/Thread/sleep 代码（grep 实测） | `test_nmea.py:466-484`（AST 扫描 nmea.py）；`tests/unit/test_no_external_access.py` AST/路径守卫覆盖全部默认测试（含新 contract 测试，全量门禁通过） |
| 9 | talker-independent GGA/RMC | PASS | `nmea.py:47`（`[A-Z0-9]{2}`）、`nmea.py:239-255`（分派） | `test_nmea.py:161-169`（GP/GL/GN/GA） |
| 10 | 最大行长与非 ASCII 保护 | PASS | `nmea.py:38`（256）、`nmea.py:199-210` | `test_nmea.py:251-259` |
| 11 | RMC 日期+GGA 时间组合、无日期策略 | PASS | `nmea.py:263-306`（RMC status=A 才提供日期；否则 trusted_date；皆无→None，不伪造） | `test_nmea.py:351-373` |
| 12 | knots→m/s、course、fix 质量语义映射 | PASS | `nmea.py:41`（1852/3600）、`nmea.py:471`；`nmea.py:576-598`（0/1/2/4/5/6/8 映射，3/7→unsupported_fix_quality，其他→out_of_range） | `test_nmea.py:199-229`、`311-315`；计划 D4 决策（3/7 拒绝而非改枚举） |
| 13 | 结构化 parse result/error 可序列化 | PASS | `nmea.py:59-102`（NmeaErrorReason/NmeaError，code=invalid_argument，context JSON-safe、消息 ASCII） | `test_nmea.py:490-497`（to_dict 往返） |
| 14 | 失败测试优先（先红灯） | PASS（一致性验证；无法事后重放） | 计划文档 §8 L85 记录红灯 `ModuleNotFoundError`；测试文件 import nmea 模块（L34），模块缺失时 collection 必然失败，与记录一致 | 与 t2 记录交叉核对一致 |
| 15 | 不 commit/push/merge、不建分支、范围外零改动 | PASS | reflog/HEAD 未变（仍 `0b477ab`）；git status 仅 4 声明路径 + t1 基线单 | 复核实测 |

## 5. Git 与交付检查

- 分支/基线：`main` @ `0b477ab`，与 origin/main 0/0；无新提交、无 merge、无 push（t2 按契约只留工作树改动）。
- 改动文件：4 个精确路径，与 t2 inScope/changedPaths 逐一相等（team.json 记录与工作树实测一致）；无范围外修改、无遗漏、无缓存/日志/密钥/实测数据（`__pycache__/.pytest_cache` 均 git-ignored，审查前后状态逐字节一致）。
- 单 Issue 原子性：全部改动只属 ISSUE-024；M05 仅改 ISSUE-024 状态行（L7，Planned→Review，1 行 diff），未动其他条目；未触碰 ISSUE-025/026 范围。
- reflog：仅 commit/merge/checkout 记录，无 reset/rebase/amend/强推迹象（复核与 t1 一致）。
- 公共契约变更：无 schema/协议/ADR 变更；`GnssFix`/枚举只读复用（core 零改动）。
- 依赖顺序：ISSUE-001/005 合入证据齐备（t1 §3.2），ISSUE-024 可独立合并。

## 6. 测试与验证结果

环境：`./.venv/Scripts/python.exe` = Python 3.13.14；pytest 8.4.2；ruff 0.16.5；mypy 1.20.2（compiled）；WSL 侧 `python3` 3.12.3 仅用于只读探针。

| 命令（复核实际执行） | 退出码 | 结果 |
|---|---|---|
| `./.venv/Scripts/python.exe -m pytest tests/contract/test_nmea.py -q` | 0 | **60 passed in 0.07s**（与 t2 声称 60 passed 一致） |
| `./.venv/Scripts/python.exe -m pytest tests/contract/test_nmea.py tests/unit/test_core_gnss.py tests/unit/test_core_metadata.py tests/unit/test_reference_manifest.py -q` | 0 | **120 passed in 2.72s**（60 新增 + 60 依赖回归） |
| `./.venv/Scripts/python.exe -m pytest -m "not hardware and not slow" -q`（全量非硬件） | 0 | **974 passed, 4 deselected in 253.58s**（978 collected；914 基线 + 60 新增，与 t2 声称 974/4 一致） |
| `./.venv/Scripts/python.exe tools/quality/verify.py` | 0 | 全量 pytest + ruff + mypy + import 四门禁全绿：`[quality] all gates passed`（与 t2 声称一致） |
| `./.venv/Scripts/python.exe -m ruff check src tests` | 0 | `All checks passed!` |
| `./.venv/Scripts/python.exe -m mypy src` | 0 | `Success: no issues found in 44 source files`（43→44 含 nmea.py，与 t2 声称一致） |
| `git diff --check` | 0 | clean |
| 夹具 checksum 独立对拍 | — | **35/35 全部声明 checksum 与独立 XOR 重算一致，0 mismatch**（含经典样例 `*47` 交叉验证） |

变异探针（内联执行、零落盘、项目零残留），关键结果：

- 越界/格式：lon 分钟 60、quality 9、lat 90.001、HDOP 负值、RMC 负速度、course 360、29-Feb-2001、30-Feb-2000、非法日历日 → 全部结构化拒绝；lat=90、lon=180、29-Feb-2000、负高程（合法）→ 接受。
- checksum：单 hex、3 hex、非 hex → `malformed_field`（非 `bad_checksum`，正确）。
- 组合：12h 容差边界（恰好 12h）→ 同日（确定性，计划记录口径）；V 态 RMC + trusted_date → 采用 trusted_date；V 态不提供日期/速度/航向。
- 发现项（P3-1）：lat 配 E / lon 配 N 半球被接受（见 §3）。

## 7. 报告与事实差异

- t2 执行日志（计划文档 §8）声称的数字（60 passed；974 passed/4 deselected；ruff/mypy 44 文件；diff-check 干净；工作树仅 4 声明路径 + t1 基线单）**全部独立复现，无差异**。
- t2 红灯记录（实现前 `ModuleNotFoundError: No module named 'uav_gpr.positioning.nmea'`）为事后不可重放的过程声明：与测试文件结构一致（测试 import nmea，模块缺失必致 collection 失败），标为「未发现反证、无法事后独立复现」，不作为否定依据。
- t2 声称「B017 盲异常断言修复 2 处」「首轮 36 failed→修复→全绿」为过程声明，无法事后复现，但最终代码（`test_nmea.py:439-442` 精确 `DomainError`/`FrozenInstanceError` 断言）与 ruff 0.16.5 全绿一致。
- t2 未声称任何超出实测范围的事项；参考源哈希 `b94d3bfc…` 与 ISSUE-001 manifest（t1 §3.5-2 复核）一致。

## 8. 剩余风险

- P3-1：纬度/经度半球字母轴向未交叉校验（低风险，见 §3）。
- RMC 地速字段无上界（`nmea.py:460-462`，计划 D4 未要求，NMEA 规范无界，GnssFix 接受任意非负有限值）——按计划口径属于设计选择，仅作记录。
- 闰秒 `ss=60` 按计划 D4 拒绝（fail-closed）——真实接收机偶发闰秒语句会被拒绝并计数，属 ISSUE-025 指标范畴，不影响本 Issue。
- 12h 容差边界（恰好 ±12h）与「跨午夜恰好 12h」的歧义输入映射为同日——确定性策略已记录于计划，无数据风险。
- 参考源本地不可达：本 Issue 为按契约重实现 + 匿名/合成夹具，与旧代码无直接对拍可能；契约来源于 M05/GNSS.md/ADR-0005，此风险 t1 已判定非阻塞，维持。

## 9. 合并建议

**建议合并（PASS，无需人工验收阻塞项）。** 依据：全部 15 项验收 PASS；测试数字与门禁全量独立复现；Git/交付检查无异常；changedPaths==inScope 逐一相等；无 P0/P1/P2；唯一 P3 不阻止合并。合并方式：按既有流水线由 captain 将工作树 4 个 t2 交付物（`src/uav_gpr/positioning/nmea.py`、`tests/contract/test_nmea.py`、`docs/plans/2026-09-02-issue-024-nmea.md`、`docs/issues/M05_GNSS.md` 状态行）提交合入 main 并推送；t1 基线单可一并提交（文档交付物）。合并后由 captain/负责人将 M05 L7 标 `Done`。不进入 ISSUE-025。

## 10. 最小修复清单

1. （可选，非阻塞，P3-1）`src/uav_gpr/positioning/nmea.py` `_parse_coordinate` 增加 per-axis 合法半球集合（lat→{N,S}，lon→{E,W}），非法组合抛 `malformed_field`；先在 `tests/contract/test_nmea.py` 补 1 条失败测试（lat/E 与 lon/N 各一）再实现。约 10 行改动。
2. 无其他修复项。

---

> 审查结束。审查者未修改任何实现/测试/计划/M05/Git 状态；本报告为审查唯一产出物；审查前后工作树除本报告外逐字节一致。
