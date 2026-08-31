# ISSUE-014 独立复审报告

- 日期：2026-08-30
- 审查者：reviewer（AgentTeams `uav-gpr-issue-014-inventory`，任务 t5，承接原 t3 契约，reviewedTask=t4）
- 审查对象：t4（t2b 精确 inScope 契约版）交付的 ISSUE-014「空地 inventory 与逐道一致性服务」
- 审查标准：docs/ISSUE_REVIEW_STANDARD.md v1.0（固定格式 §13）
- 审查性质：全程只读——未修改实现/测试/计划/M02/Git 状态，未 commit/push/merge；变异探针在系统临时目录复制树（`D:\tmp\issue014_probe`）中运行，项目内零残留（审查前后 `git status --porcelain=v1 -b` 逐字节一致）

## 1. 审查结论

**PASS WITH CONDITIONS**（无 P0/P1/P2 问题；全部验收标准 PASS；剩余 6 项 P3 文档/可维护性观察与依赖侧剩余风险，不阻止合并，交项目负责人人工验收）

- 单 Issue 结论：ISSUE-014 **通过**。
- 依赖影响：无。ISSUE-014 未改动任何既有模块公共语义（ISSUE-009/011/012 回归全绿）。
- 合并建议：可将 `src/uav_gpr/storage/inventory/__init__.py`、`tests/integration/test_inventory.py`、`docs/plans/2026-08-30-issue-014-inventory.md` 与 M02 的 014 状态行合入目标分支；P3 项在后续 Issue（041/042/043/048/056）或下次触碰时顺手处理即可。

## 2. 自动识别的审查范围

从 t4 完成报告、t1 基线单、Git 与工作树交叉识别：

| 项 | 识别结果 | 证据 |
|---|---|---|
| Issue | ISSUE-014（M02_STORAGE.md L227–261，状态行已置 `Review`） | docs/issues/M02_STORAGE.md L229；docs/issues/README.md L81 |
| 依赖 | ISSUE-009/011/012（均 `Done` 合入 main） | M02 L44/L118/L155；t1 基线单 §3 |
| 分支/基线 | `feat/issue-013` @ `0903749`（= main；共同祖先=HEAD，无新增提交） | git log/reflog 实测 |
| 目标分支 | main | t1 基线单 §3.1 |
| 改动文件（ISSUE-014 范围） | ① `src/uav_gpr/storage/inventory/__init__.py`（666 行，新）② `tests/integration/test_inventory.py`（913 行，新）③ `docs/plans/2026-08-30-issue-014-inventory.md`（95 行，新）④ `docs/issues/M02_STORAGE.md` 014 状态行 `Planned→Review`（L229，1 行） | git status/diff 实测 |
| 工作树既有的非本 Issue 产物 | ISSUE-013 在制文件 7 项（`rcscan_v1.py`、`test_rcscan_v1.py`、golden、013 计划/两份报告、M02 L192 状态行改动）——核查前后均存在，t4 未触碰 | t1 基线单 §3.5；git diff M02 仅 2 处状态行（192 行改动先于 014 开工即存在） |
| 声明状态 | 实现+测试完成，等独立复审；不 commit/push/merge | t4 完成报告；git reflog 无新提交 |

## 3. 主要问题（P0→P3）

**P0：无。P1：无。P2：无。**

P3（低风险，不阻止合并）：

- **P3-01（依赖侧剩余风险）** ISSUE-011 reader 在首次校验时急切解码全部行元数据：10 万道×两侧实测保留 200,000 个 `TraceMetadata` 对象，工作集 ≈ 512 MB（100k 对账总内存 ≈ 640 MB）。ISSUE-014 自身只加 ~15 MB（两侧索引集合），但消费方必须接受 reader 的这一既有成本。属 ISSUE-011 设计（`rcscan_reader.py` 分类即解码），非 014 缺陷；建议在 ISSUE-042/048 规划内存预算。
- **P3-02（计划文档内部不一致）** `docs/plans/2026-08-30-issue-014-inventory.md` L84 执行日志写「回归：依赖套件 305 passed（…/v1）」，与其自身 L66 的回归清单及 t4 报告「回归 269 passed」不符；实测按 L66 清单复跑 = 269 passed，305 = 269 + ISSUE-013 的 `test_rcscan_v1.py`（36 例）。仅文档数字笔误，不影响门禁事实。
- **P3-03（测试缺口已由复审补证）** 计划/基线承诺「十万条分页（有界内存断言）」，但 `test_inventory.py:781-826` 只断言页界与确定性，未直接断言内存。本次复审以独立探针补证（见 §6.5）：inventory 增量 14.8 MB、GC 后存活 ndarray 字节 = 0、翻页峰值不随页号增长。建议后续 Issue 在测试中固化 1 条 tracemalloc/RSS 断言。
- **P3-04（翻页复杂度提示）** `page(k)` 每次从头重放，且 `RcScanReader.iter_logical` 每次调用重建全量 served 位置列表（10 万道 ≈ 3.6 MB 瞬时、O(n) 时间）。协议对账场景（ISSUE-042/043）逐页调用为 O(n·页数)，100k 级可接受；若需高频随机翻页再考虑 reader 侧缓存（属 011 范围，不在 014 内修）。
- **P3-05（API 严格性说明）** `_require_page_size`/`_require_page_index`（inventory/__init__.py:192-209）拒绝 numpy 整数（`np.int64` 非 `int`）与 bool，fail-closed 严格但偏窄；若后续协议层传入 numpy 标量需显式转换。行为已由测试钉死，属设计选择。
- **P3-06（文档引用旧尝试号）** 计划文档头部与执行日志引用已取消的 t2 尝试 `d2a7996e-0552-4412-a19d-04277c8e8a9d`；t4 承接后未改写。仅记录层信息，不影响产物。

## 4. 逐 Issue 验收矩阵

口径：M02_STORAGE.md L250–252 三条验收 + L237–246 范围/排除 + 提示词必测项（M02 L259–261）。代码证据精确到 `文件:行号`。

| # | 验收标准 | 状态 | 代码证据 | 测试证据 |
|---|---|---|---|---|
| A1 | 乱序物理记录不影响结果 | **PASS** | `src/uav_gpr/storage/inventory/__init__.py:412-494`：合并流按 `iter_logical`（reader 按显式 `trace_index` 排序，`rcscan_reader.py:953-975`）升序 merge-join，物理行序不参与 | `tests/integration/test_inventory.py:500-518`；独立探针 P1（乱序+冲突+缺道+extra+GNSS 差异+文件内冲突混合，乱序结果与有序逐项相等） |
| A1b | 同 hash 重复与不同 hash 冲突区分 | **PASS** | 同 hash 重复由 reader 逻辑视图折叠（`rcscan_reader.py:964-968`）并计入 `ValidationReport.duplicates`；跨侧不同 hash/uid 走 `_pair_item`（inventory/__init__.py:357-398）判 `conflict` 且保留两侧 hash 证据、绝不选赢家；文件内冲突索引经 `_side_index_sets`（:260-282）显式上报不误判 missing/extra | `test_inventory.py:563-581`（duplicate≠conflict）、`:584-611`（hash 冲突）、`:614-633`（uid 冲突）、`:636-655`（文件内冲突上报）；探针 P1b（uid 交叉互换 → 2 条 fail-closed 冲突，绝不静默、绝不 missing/extra） |
| A2 | ground 独有 processed/transport 字段不造成 raw 不一致 | **PASS** | inventory 只消费逻辑视图的 metadata/hash/GNSS，从不读取 `/frequency/calibrated`、`/time_*`、`/transport` 组；raw 判据唯一为 ISSUE-009 规范 `raw_trace_sha256` 字符串比较（:369-378），GNSS 差异走 `_gnss_diff_detail`（:224-251）独立分类 | `test_inventory.py:698-717`（ground 带合法 processed 组 → 全 consistent）；`:663-690`（GNSS 缺失/数值差异 → gnss_diff、conflicts=0）；探针 P3（直接重算两侧规范 hash 证明 GNSS 改动不改变 raw 身份） |
| A3 | 大任务可分页/流式处理，内存有界 | **PASS** | `page()`（:608-639）按 `page_size` 返回有界页+`has_more`，kind 过滤后再分页；`iter_items()`（:597-606）流式；比较仅物化两侧索引整数集合（`_SideSets`，:183-189），`to_dict()` 只内联异常项（:641-666） | `test_inventory.py:781-826`（100k×99,950：summary 精确、页 ≤ page_size、has_more 正确、重复翻页逐字节一致）；独立探针 P2（§6.5）：inventory 增量 14.8 MB@100k、GC 后 0 字节 ndarray 存活、page(0)/page(last) 峰值 2.457/2.466 MB 不随页号增长 |
| S1 | 范围：MissionInventory + 分页/区间摘要 + missing/extra/重复/冲突分类 | **PASS** | `MissionInventory`（:315-666）：`summary()`/`page()`/`iter_items()`/`contract()`；分类 `InventoryItemKind`（:52-59）覆盖 missing/extra/consistent/conflict/gnss_diff；重复/冲突明细由两侧 `validation_report` 载入报告 | `test_inventory.py:460-492、526-655` |
| S2 | 范围：空地任务/config/axis/channel 契约检查 | **PASS** | `contract()`（:498-557）逐项比对 mission_id/channels/频率轴/config digest，report-only 不阻断比较 | `test_inventory.py:725-773`（mission_id 不同仍完成比较并产出 hash 冲突——fail-closed 语义正确） |
| S3 | 范围：逐道 raw hash 主一致性，GNSS 差异单独报告 | **PASS** | :369-378（先 uid 后 hash）、:379-389（GNSS 差异独立 kind） | `test_inventory.py:663-690`；探针 P3 |
| S4 | 范围：稳定可序列化 report | **PASS** | `REPORT_FORMAT`/`REPORT_VERSION`（:46-47）；`to_dict()` 固定键序（:641-666）；JSON 安全类型（dataclass + StrEnum） | `test_inventory.py:834-856`（两次 to_dict 相等、json 往返一致、含 format/version） |
| E1 | 排除：不发网络消息/不补传/不修复删除改写文件 | **PASS** | 模块无任何 socket/网络/写文件代码；输入为已打开的 `RcScanReader`（"r" 模式，`rcscan_reader.py:633` 附近只读保证） | 审查前后 git status 一致、无新缓存/数据文件；全量门禁通过 |
| E2 | 排除：不改既有模块公共语义、不重构范围外 | **PASS** | diff 仅新增 4 项 + M02 状态行；回归 269 passed（009/011/012/schema/writer 契约套件） | 见 §6.2 |
| E3 | 失败测试优先 + 最小实现 + 门禁 + 报告 | **PASS** | 红灯证据记录于计划 L82（`ModuleNotFoundError`）；最小实现单文件子包布局 | 15 例全绿；门禁数字全部复现（§6） |

依赖接口兼容性（按标准 §7）：reader 被用接口 `iter_logical/iter_physical/validation_report/mission_id/channels/frequencies_hz/config/path` 均实测存在且语义与 011 契约一致（`rcscan_reader.py:633-660、942-1023`）；hash 仅作字符串身份比较，不重算——与 009 契约（DATA_FORMAT §5.1）一致。

## 5. Git 与交付检查

| 检查项 | 结果 |
|---|---|
| 当前分支/基线 | `feat/issue-013` @ `0903749`，`main` 同指（merge-base == HEAD）；无新提交（`git log`/`git reflog -5` 实测：最后一条为基线提交，reflog 仅 checkout 记录，无 reset/rebase/amend/强推） |
| 提交混入/拆分 | 无提交产生；交付物为未跟踪新文件 + 1 行状态行，单 Issue 可独立理解 |
| 范围外修改 | 无。M02 diff 仅 2 处状态行：L192（ISSUE-013，先于 014 开工已存在，见 t1 基线单 §3.5）与 L229（ISSUE-014）；其余未跟踪文件均为 ISSUE-013 在制产物（核查前即存在） |
| 缓存/日志/密钥/实测数据 | 无。`.pytest_cache/.mypy_cache/.ruff_cache/__pycache__` 均在 .gitignore；审查结束 git status 与审查前逐字节一致（10 条目） |
| 契约变更与 ADR | 未改任何冻结契约/schema/协议；无新 ADR 需求（计划 §3 D1–D9 均沿用已冻结契约） |
| 依赖顺序 | 014 不依赖 013（M02 L230、README L81）；013 在制文件与 014 交付零交集，可独立拆分合并 |

## 6. 测试与验证结果

环境：Windows venv `.venv\Scripts\python.exe`（Python **3.13.14**，与 t4 声称一致，WSL interop 调用实测；numpy/h5py/pytest 同 venv）。工作目录 = 工作区根。

| # | 命令（实际执行） | 实际结果 | t4 声称 | 判定 |
|---|---|---|---|---|
| 6.1 | `python -m pytest tests/integration/test_inventory.py -q` | **15 passed in 213.24s**（exit 0） | 15 passed（210.85s） | 一致（时长属正常波动） |
| 6.2 | `python -m pytest tests/contract/test_raw_trace_hash.py tests/contract/test_rcscan_reader.py tests/integration/test_partial_recovery.py tests/contract/test_storage_schema.py tests/integration/test_incremental_writer.py -q`（计划 L66 回归清单） | **269 passed in 20.50s**（exit 0） | 回归 269 passed（22.80s） | 一致 |
| 6.3 | `python tools/quality/verify.py`（pytest 全量非硬件 + ruff + mypy + import） | **exit 0，[quality] all gates passed** | all gates passed | 一致 |
| 6.4 | `python -m pytest -m "not hardware and not slow" -q` | **562 passed, 1 deselected in 258.08s** | 562 passed/1 deselected（226.91s） | 一致 |
| 6.5 | `python -m ruff check .` / `python -m mypy src` | **All checks passed!** / **Success: no issues found in 35 source files** | 同 | 一致 |
| 6.6 | `git diff --check` | clean（exit 0） | clean | 一致 |
| 6.7 | 变异探针（`D:\tmp\issue014_probe\review_probe.py`，复制树 + meta-path 强制复制树内导入 + 全部夹具写入复制树 `.probe_out`，运行后整树删除） | **P1/P1b/P2/P3 全 PASS** | t4 未做（任务要求复审补查） | 通过 |

### 6.5 独立变异探针明细（系统临时目录复制树，零残留）

- **P1 乱序+冲突混合**：air 侧物理乱序（seed 42），ground 侧同时注入 3 缺道、1 extra（index 1000）、33 号 hash 冲突、44 号 GNSS 差异、66 号文件内冲突。summary = {matched:75, missing:3, extra:1, conflicts:2, gnss_diffs:1, ground_conflicts:1}；乱序与有序两份 air 的完整 item 流逐字节相等；kind 过滤分页 [9,55,71] has_more 正确。
- **P1b uid 交叉互换**：ground 4/5 号 uid 互换 → 2 条 `conflict`（detail="trace_uid mismatch"，两侧 uid 均保留为证据），missing=extra=0——fail-closed，绝不静默选赢家。
- **P2 十万条内存**（100k×2，psapi + tracemalloc 双通道）：
  - 打开两侧 reader 后 WS 114 MB → reader 校验后 **624 MB**（reader 急切解码 200,000 个 TraceMetadata，ISSUE-011 依赖侧既有行为）→ inventory `summary()` 后 **639 MB（inventory 自身增量 14.8 MB，仅两侧索引集合）**；
  - 分析后 `gc.collect()`，**存活 ndarray 总字节 = 0**（绝不持有 raw 数组，与设计 D8 一致）；
  - `page(0)`/`page(最后页)` tracemalloc 峰值 2.457/2.466 MB（10k 规模为 618/608 KB）——峰值不随页号/已翻页数增长；全量 `iter_items()` 峰值 2.46 MB，无累积。
- **P3 GNSS 不污染 raw 判定**：ground 侧 3 号改纬度、5 号去 GNSS，两侧 raw hash 字符串直接比对相等（GNSS 不进入 ISSUE-009 framing），inventory 报 gnss_diffs=2、conflicts=0、matched=8。

## 7. 报告与事实差异

- t4 声称的数字与命令**全部独立复现**（§6）；时长差异为运行环境波动，非不一致。
- 计划文档 L84「305 passed」与其 L66 清单及 t4「269 passed」不符：实测 269 = 计划清单五文件；305 = 269 + ISSUE-013 `test_rcscan_v1.py`（36 例）。判定为计划执行日志笔误（P3-02），不影响门禁事实与复审结论。
- t4 声称「未触碰 013 L192」属实：该行改动在 t1 基线快照（§3.1/§3.5）中已存在，早于 014 开工。
- t4 声称「git status 无残留」属实：复审全程（门禁+探针）前后工作树快照逐字节一致。
- 无法独立验证的过程声明（红灯复现、执行顺序）标为「未发现反证」：计划 L82 红灯证据（ModuleNotFoundError）与 15 例绿灯对应关系自洽，且失败测试优先的证据链（计划 §4 测试矩阵先行）完整。

## 8. 剩余风险

1. **R1（低）**：10 万道级空地对账总内存 ~640 MB，主因 reader 元数据急切解码（依赖侧）。ISSUE-042/043/048 复用 inventory 时需按此预算；若目标平台内存受限，需在 ISSUE-011/042 层面引入懒解码（需 ADR，超出 014 范围）。
2. **R2（低）**：report JSON 契约（format/version v1）为 014 单方冻结，ISSUE-037/042 协议 codec 冻结时需做版本对拍测试（既有版本字段已预留）。
3. **R3（低）**：inventory 输入为已打开 reader 对，调用方负责生命周期（D1）；reader 打开失败/损坏文件 fail-closed 由 011 保证，014 未重复实现（符合最小实现原则，但消费方需知悉）。
4. **R4（低）**：文件内冲突索引若另一侧也缺失该索引，则仅出现在两侧 validation 明细（`air_validation/ground_validation`）而不在跨侧 items——语义正确（计划 D6），但诊断工具（ISSUE-056）展示时需同时读两处。

## 9. 合并建议

- **可以合并**：无 P0/P1/P2，验收矩阵全 PASS，门禁与探针全部复现通过。
- 建议合并单元：`src/uav_gpr/storage/inventory/__init__.py` + `tests/integration/test_inventory.py` + `docs/plans/2026-08-30-issue-014-inventory.md` + M02 014 状态行（保持 `Review`，人工验收后置 `Done`）。
- 与 ISSUE-013 在制文件严格分离（不同路径、零交集），可独立合并；013 的合并时点不影响 014。
- 项目负责人人工验收通过后将 014 状态行置 `Done` 并授权合入 main；本复审不执行任何 Git 写操作。

## 10. 最小修复清单

按 §14「修复提示词只包含阻止合并和明确要求处理的最小问题」：**无必须修复项**。以下为可选顺手项（全部 P3，可在人工验收时决定是否处理）：

- P3-02：修正计划文档 L84 回归数字为 269（或注明含 v1 的 305 口径）。
- P3-03：在 `test_inventory.py` 增加一条轻量内存断言（如 GC 后存活 ndarray 字节数上界），固化复审探针结论。
- P3-06：计划文档头部注明已由 t4 承接复核（可选）。
- P3-01/P3-04/P3-05：分别属于 ISSUE-011/042/037 范围，记录到对应 Issue 计划即可，不在 014 内修改。

> 复审结束。按标准 §13 立即停止，不修改代码，等待项目负责人决定。
