# ISSUE-019 开工基线确认单（只读核查 + 实施计划）

日期：2026-09-01（ISSUE-019 开工前置件）
会话：DeepSeek Harness AgentTeams `uav-gpr-issue-019-librevna-transport`（执行器 engineer，任务 t1，attempt 3da1b002-4ba3-4332-8853-33d91d3d6958）
性质：只读核查产出；不含任何实现代码；未 commit、未 push、未切换/创建分支、未修改实现与测试/计划文件。
配套文件：本单为 t2（迁移 LibreVNA USB 传输层）与 t3（独立复审）的权威基线件；独立复审按 [ISSUE_REVIEW_STANDARD.md](../ISSUE_REVIEW_STANDARD.md) 执行。t2 完整实施契约见 t2 计划文档（`docs/plans/2026-08-30-issue-019-librevna-transport.md`，由 t2 先落盘）。

## 1. 锁定的目标 Issue 与依据

**ISSUE-019：迁移 LibreVNA USB 传输层**（`docs/issues/M04_LIBREVNA.md` 第 1 个条目，状态 `Planned`，L5–40）

| 项 | 结论 | 证据 |
|---|---|---|
| 目标 Issue | ISSUE-019（M04_LIBREVNA.md L5–40） | docs/issues/README.md 依赖顺序主表 L86 |
| 直接依赖 | ISSUE-001（冻结参考项目基线与迁移清单）、ISSUE-015（AcquisitionBackend 契约与确定性模拟器） | M04 L8「直接依赖：ISSUE-001、015」；README.md L86 |
| 依赖状态 | 均已合入 `main`，tracked 代码/测试/合并提交为权威证据（见第 3.2 节）：ISSUE-001 经 `0ddbd81`（feat/m01-issues-001-005 PR）合入、复审总结 `docs/reports/ISSUE_001_005_REVIEW_SUMMARY.md` 结论 PASS（原始提交 `2c5b379` + 修复 `6b74dcf`）；ISSUE-015 经 `2f11cd9` 合入、`579f92b` 标记 Done（R2 复审 PASS WITH CONDITIONS，见 `docs/reports/ISSUE_015_REVIEW_REPORT.md`） | git log/ls-files；docs/reports/ISSUE_001_005_REVIEW_SUMMARY.md、ISSUE_015_REVIEW_REPORT.md |
| 功能映射 | FR-003、ADR-0005 | M04 L10 |
| 提示词工作区替换 | `E:\wurenjiruanjian` → 工作区根（见第 2 节） | docs/issues/README.md 第 3 节通用协议 |
| 一次一 Issue | 本轮只处理 ISSUE-019；不进入 ISSUE-020 | docs/issues/README.md 第 1/3 节；团队目标 |

## 2. 工作区根替换声明

Issue 提示词中出现的 `E:\wurenjiruanjian` 一律替换为当前工作区根：

> **D:\博士任务\无人机软件\UAV-GPR**（WSL 视角 `/mnt/d/博士任务/无人机软件/UAV-GPR`）

参考项目原始路径 `E:\钢筋仪软件开发`、`E:\UVA_GPR_system` 不在本机挂载范围内（`cmd.exe /c "if exist E:\...` 实测 `E_MISSING`，WSL `/mnt/e` 不存在，仅挂载 C/D）；**ISSUE-019 的参考源以 ISSUE-001 manifest 冻结的内容哈希为可移植事实**，本机对拍依据是本地只读副本 `D:\博士任务\rebar-inspector`（WSL `/mnt/d/博士任务/rebar-inspector`，见第 3.3 节，全部 11 个 librevna 候选源文件哈希与 manifest 逐一相等）。

## 3. 只读核查证据（Git 事实，全部为命令实测复现）

### 3.1 Git 基线

```text
branch      main（当前；工作树完全干净）
HEAD        c0cd067  docs(issues): mark ISSUE-018 Done after authorized merge
分支关系    main...origin/main = 0/0（完全同步；`git log main..origin/main` 与 `origin/main..main` 均为 0）
git status --porcelain=v1 -b
    ## main...origin/main
    （无其他条目——无未跟踪/未提交文件）
git diff --check    # clean（exit 0）
```

依赖合并提交（`git log --oneline main` 实测，全部在 main 历史中）：

| 提交 | 内容 |
|---|---|
| `2c5b379` | `chore: freeze reference project baselines`（ISSUE-001 原始提交） |
| `6b74dcf` | `fix(issue-001): make reference manifests fail closed on path decoding`（ISSUE-001 复审修复） |
| `0ddbd81` | `Merge pull request #1 from z2362536803/feat/m01-issues-006-007`（M01 批合并；ISSUE-001～005 证据另见 ISSUE_001_005_REVIEW_SUMMARY.md） |
| `0b69e6d` | `feat(acquisition): backend contract and deterministic simulated backend (ISSUE-015)`（含 P1-01 修复） |
| `2f11cd9` | `Merge feat/issue-015: ISSUE-015 acquisition backend contract and deterministic simulator` |
| `579f92b` | `docs(issues): mark ISSUE-013/014/015 Done after authorized merges` |
| `c0cd067` | `docs(issues): mark ISSUE-018 Done after authorized merge`（当前 HEAD） |

reflog 实测仅 commit/merge/checkout 记录，**无 reset/rebase/amend/强推迹象**。`git ls-files` 确认 ISSUE-001/015 交付模块与测试全部 tracked 于 main（见 3.2）。

### 3.2 依赖交付物（main 内实测；行数为 `wc -l`、测试数为 `pytest --collect-only` 实测）

| 依赖 | 交付物（main，tracked） | 测试数 | ISSUE-019 复用/依据点 |
|---|---|---|---|
| ISSUE-001 | `docs/reference-baselines/README.md`、`manifest.json`、`manifest.md`、`spec.json`；`tools/migration/reference_manifest.py` + `tools/migration/README.md`；`tests/unit/test_reference_manifest.py` | 13 | manifest schema 1.0：钢筋仪仓库 `feat/issue-16-pause-resume` @ `938875234a99b47d78cfec940671005b63e9d15c`、worktree dirty=True；11 个 `librevna` 角色候选源全部 `tracked_status=committed` 并带 SHA-256——**ISSUE-019 迁移清单的源哈希权威依据**；manifest 工具只读 + fail-closed 语义与迁移规则见 `REFERENCE_MIGRATION.md` |
| ISSUE-015 | `src/uav_gpr/acquisition/backend.py`；`tests/contract/test_acquisition_backend.py` | 28 | `AcquisitionBackend` 严格生命周期、`_wait_cancellable`（可取消/可超时阻塞等待原语）、`BackendCancelledError/BackendClosedError/BackendTimeoutError` 结构化错误分类——USB transport 的取消/超时语义须与 backend 消费方式对齐（ISSUE-021 才接线，ISSUE-019 只定义传输层可取消 I/O） |
| 相关 | `src/uav_gpr/core/errors.py`（`ErrorCode(StrEnum)` + `DomainError`，ISSUE-003） | — | 业务分支按稳定错误码、message 为 ASCII 安全展示串、上下文 JSON 安全——transport 结构化错误映射必须遵循该契约，不靠中文字符串分支 |
| 相关 | `tests/unit/test_no_external_access.py`（ISSUE-002） | 1 | AST 扫描：**默认测试模块不得 import `serial`/`usb`/网络根**——ISSUE-019「默认测试不得枚举 USB」的硬门禁：transport 测试只能经 adapter/假模块注入，不得 import 真 pyusb |
| 相关 | `pyproject.toml` | — | `pyusb>=1.2.1,<2` 已在主依赖（transport 真机路径惰性加载所需）；hardware marker 双重 opt-in；ruff `E,F,I,B,UP,RUF`；mypy strict |
| 相关 | `src/uav_gpr/acquisition/librevna/__init__.py` | — | 占位文件（docstring「LibreVNA transport and acquisition backend boundary.」），ISSUE-019 的 `transport.py` 落点；`grep -rn "transport\|librevna" src/uav_gpr/acquisition/librevna/` 仅此 docstring，**无任何传输实现** |

### 3.3 reference-baselines manifest 与本地钢筋仪只读副本哈希对拍（本单核心核查）

- manifest 冻结事实（`docs/reference-baselines/manifest.json` 实测解析）：`rebar-inspector` 仓库 branch `feat/issue-16-pause-resume`、HEAD `938875234a99b47d78cfec940671005b63e9d15c`、worktree_dirty=True；11 个 `librevna` 角色文件（`src/rebar_inspector/acquisition/` 下 `__init__.py`、`acquired.py`、`aggregation.py`、`backend.py`、`errors.py`、`file_replay.py`、`librevna_protocol.py`、`librevna_usb.py`、`librevna_usb_transport.py`、`simulated.py`、`sweep_config.py`）全部 `committed`。
- 本地只读副本：`D:\博士任务\rebar-inspector`（GitHub 克隆 `z2362536803/rebar-inspector`）实测 branch `main`、HEAD `7c522d2aebe6a835acb969e8012565715f64a238`（与 ISSUE-013 基线单记录一致）；worktree 对 `src/rebar_inspector/acquisition/*.py` 显示 `M`（修改），但 `git diff --ignore-all-space HEAD -- src/rebar_inspector/acquisition/` 实测**零差异**——即 worktree 差异仅为行尾（CRLF vs LF，`file` 实测 worktree 为 CRLF、HEAD 为 LF；`core.autocrlf` 未设置），**内容与 manifest 冻结内容逐字节一致**。
- **SHA-256 对拍结果（`sha256sum` 实测，11/11 全等）**：

| 源文件 | manifest SHA-256 | 本地副本实测 |
|---|---|---|
| `src/rebar_inspector/acquisition/__init__.py` | `838cbdc857d6e9f73b4dfb5ed461b7ba541768643a0e0c53e872734e7c31bcf7` | ✅ 相等 |
| `acquired.py` | `44bf8c6adc76cfe0326048bf300942a67d8fdb49e7d0026bc5c78ed01a309626` | ✅ 相等 |
| `aggregation.py` | `c8b64176f461f75a72809f0d072c09a31c752a3ede49a5d81543bfbf026126d1` | ✅ 相等 |
| `backend.py` | `f05da35cdee84604d43945da8c30854a289fb7de36a90a3c46c110cf8ab3340f` | ✅ 相等 |
| `errors.py` | `c3dfbfcaf4a6a5aea38f8ad79c4ecbbf546e69be2c7051dcf89ea1883aac2502` | ✅ 相等 |
| `file_replay.py` | `96e4b1f57b5e400b29b91ea1820fc6883ec264a9be05c994b18a6ffa77cd29be` | ✅ 相等 |
| `librevna_protocol.py` | `6a41c4b11cfa8272c1687893a53219ea0465987a378700a0b74ab3973353d8ce` | ✅ 相等 |
| `librevna_usb.py` | `a73adc1c41f186ee51ab938c4c245809bf36dea0bc2769ad1115fffa99b22d87` | ✅ 相等 |
| `librevna_usb_transport.py` | `7a2a1f87f81567d8955aa414e801b10a4fdb8e5bba79a7e9048e6b471095bb18` | ✅ 相等 |
| `simulated.py` | `73749aa8a2435d193b8068dc9a3771f5021312a11589da19648cfedcb83a5af9` | ✅ 相等 |
| `sweep_config.py` | `9877b7619747c07aeb7657ba3667322c2687396040bb00193afd5d8508c44801` | ✅ 相等 |

结论：**参考源哈希可用性成立**——ISSUE-019 迁移审计可直接以本地只读副本 worktree 内容为源（内容哈希 == manifest == E:\ 冻结基线），无需 E: 盘可达。

### 3.4 参考源审计（只读，为 t2 迁移清单提供结构依据）

- **`librevna_usb_transport.py`（240 行，本次迁移主源）**：
  - `LibreVnaTransportProtocol`：`is_open` / `open` / `read(max_length, timeout_ms)` / `write(data)` / `close`——**就是依赖注入的 adapter 契约**（参考后端靠该 Protocol 注入 fake transport）。
  - `LibreVnaUsbTransport`：VID `0x1209`/PID `0x4121`、EP_OUT `0x01`/EP_IN `0x81`（常量来自 protocol 模块）；构造不加载 pyusb（惰性 `_load_usb_modules`，缺依赖 → `LibreVnaMissingDependencyError` 带安装提示）；`open` 幂等（已开为空操作）→ `usb.core.find`（`USBError` → Busy、未找到 → DeviceNotFound）→ `set_configuration`（Windows 失败仅 `warnings.warn` 继续）→ 内核驱动 detach（失败忽略）→ `claim_interface`（失败先 `dispose_resources` 再抛 `LibreVnaBusyError`）；`read`：未开 → `LibreVnaError`、`USBTimeoutError` → `LibreVnaTimeoutError`、其他 `USBError` → `LibreVnaDisconnectedError`；`write`：未开 → `LibreVnaError`、任何异常 → `LibreVnaDisconnectedError`（写超时固定 1000 ms）；`close` 幂等：先清 `_dev/_interface_number` 再 `release_interface`（失败 → `LibreVnaReleaseError` 但**仍继续** `dispose_resources` 尽力清理）；`_build_libusb1_backend`：Windows 用 `libusb_package` 附带 DLL 定位 libusb-1.0。
  - 注意点（迁移须适配，不能原样照搬）：参考 `read` 只有 timeout 没有显式 cancel；write timeout 硬编码；`set_configuration` 用 warning 而非结构化错误；open 的 `find` 阶段 `USBError` 被归类为 Busy。
- **`librevna_protocol.py`（530 行，本次仅采用 frame/CRC 部分）**：常量（`HEADER=0x5A`、packet 类型 ACK=7/NACK=10/REQUEST_DEVICE_INFO=15/SET_IDLE=20/DEVICE_STATUS=25/VNA_DATAPOINT=27 等）；`Packet` frozen dataclass；`crc32`（IEEE 802.3，`binascii.crc32 & 0xFFFFFFFF`）；`encode_packet`（HEADER + 长度(2) + type(1) + payload + CRC32(4)，length 含整包）；`PacketStream.feed`：丢弃 HEADER 前噪声、长度越界（<8 或 >4096）丢弃当前字节重对齐、半包等待、**非 VNA_DATAPOINT 校验 CRC、VNA_DATAPOINT(type 27) 跳过 CRC（参考实现既有行为，无设备证据不得"修复"）**——严格长度上限 4096 已具备。VNADatapoint 解析/S11 计算/`SweepSettings` 编码/`DeviceInfo` 解码**不属于本 Issue**（ISSUE-020/021）。
- **`errors.py`（144 行）**：`AcquisitionError` → `LibreVnaError` 层级（MissingDependency / DeviceNotFound / Busy / Timeout / Nack / Protocol / Disconnected / UnsupportedConfig / Sweep / Release）——错误映射的参考层级，新项目按 `core/errors.py` 的 `ErrorCode`/`DomainError` 契约重构。
- **参考测试（manifest 明确排除 `tests/**`，但作为黄金夹具来源须在 t2 迁移清单中记录 provenance）**：
  - `tests/test_librevna_usb_transport.py`（291 行，13+ 用例）：fake usb core/util 模块经 `mock.patch.object(transport_module, "importlib", fake_importlib)` 注入，覆盖导入不加载 usb、缺依赖友好错误、未找到设备、find/claim 失败 → Busy、open/read/write/close 正常链路、open/close 幂等、read 超时/断开映射、未开读拒绝、release 失败报告——**无硬件测试的既有范式**。
  - `tests/test_librevna_protocol.py`（455 行）：**黄金固定字节向量** `ACK_PACKET_HEX="5a080007c1f48315"`、`REQ_DEV_INFO_HEX="5a08000ff37c581b"`、`SET_IDLE_HEX="5a0800141fb53d91"`、`crc32(b"123456789")=0xCBF43926`（zlib 已知向量）、`crc32(b"")=0`、分块 feed/噪声前缀/非法长度/CRC 错误/VNA_DATAPOINT 跳过 CRC 等 framing 用例。

### 3.5 对 ISSUE-019 有约束的契约要点（读自 M04 L5–40、REFERENCE_MIGRATION.md、ACQUISITION.md §3、ADR-0005、ISSUE_REVIEW_STANDARD.md）

**ISSUE-019 范围（M04 L16–20）+ 提示词**：

1. 按 I001 manifest 精确记录钢筋仪源文件/哈希与迁移清单（源 branch/HEAD/worktree status、文件 SHA-256、采用/排除行为、目标模块、黄金样本来源）于计划文档；只从本地只读副本审计迁移，**绝不读取/迁移 UAV-GPR 旧采集代码**（含其 `librevna/`、`reference_code/`，ADR-0005/REFERENCE_MIGRATION.md §4 禁止）。
2. VID/PID、endpoint 发现、claim/release、bulk read/write、协议 frame/CRC、严格长度上限、timeout/cancel、可取消 I/O、幂等关闭、结构化错误映射，形成**不含 sweep 业务**的传输层。
3. 依赖注入 USB adapter（无硬件黄金帧测试）；错误映射结构化、不吞异常；USB 失败各阶段（open/claim/read/write/close）都 release 资源。
4. 迁移清单模板（REFERENCE_MIGRATION.md §5）：target issue/reference repository/branch+HEAD/worktree status/source file SHA256/trusted behavior/excluded behavior/new target module(s)/UAV-specific adaptations/tests+golden fixtures/new tests/license review。

**排除项（M04 L22–24 + 提示词）**：不组装 VNADatapoint/sweep（ISSUE-020）、不实现 backend（ISSUE-021）、不从 UAV-GPR 复制代码；不新增 inScope 之外文件（确需拆分先报告暂停）。

**验收标准（M04 L26–30 原文，t2 不得削弱）**：

1. 黄金字节帧与参考对拍；拆包/CRC/长度/timeout 测试无需真机。
2. USB 失败各阶段都 release 资源，不吞异常。
3. 普通测试不枚举真机（`test_no_external_access.py` AST 守卫 + 不 import pyusb 的 adapter 注入测试）。

**其他约束**：`AGENTS.md` §9 依赖方向（transport → core；core 仅标准库+numpy）；`AGENTS.md` §10 每能力覆盖正常/错误/取消/恢复路径、禁删测试/降断言/吞异常、禁固定 sleep 猜时序；`docs/TESTING.md` 硬件双重 opt-in；`ISSUE_REVIEW_STANDARD.md` §7 验收矩阵状态定义。本 Issue 不改变强制数据规则/空地职责/持久化语义（纯传输层、无存储、无网络），**无需新增 ADR**。

### 3.6 发现的事实（非阻塞，供 t2 注意）

1. **工作树完全干净且与远端同步**：`git status --porcelain=v1 -b` 仅 `## main...origin/main`（0/0）；t2 交付物将是唯一新改动。
2. **落点为空**：`src/uav_gpr/acquisition/librevna/` 仅占位 `__init__.py`；无 transport 实现、无 019 计划文档、无 019 报告（`ls docs/plans|grep 019`、`ls docs/reports|grep 019` 均为空）——t2 新增 `transport.py` 与契约测试（精确路径见第 5 节）。
3. **黄金帧夹具来源已定位**：参考 `tests/test_librevna_protocol.py` 的固定字节向量（ACK/REQ_DEV_INFO/SET_IDLE/CRC 向量）与 `test_librevna_usb_transport.py` 的 fake-usb 注入范式；这些文件不在 manifest 白名单（`tests/**` 排除），t2 迁移清单必须把「黄金样本来源」记录为该路径 + 读取时点哈希（只读，不进交付）。
4. **「可取消 I/O」是参考源没有的增量**：参考 `read` 仅有 timeout；ISSUE-019 要求 timeout/cancel——t2 需在 adapter 契约/传输层设计可取消语义（如 cancel 标志 + 唤醒/结构化取消错误），并保证 cancel 不遗留句柄；backend 的 `_wait_cancellable` 是后续消费方参照（ISSUE-021 才接线）。
5. **测试注入口径**：transport 测试不得 import 真 `usb`/`pyusb`（AST 守卫）；可仿参考 fake-importlib 注入或定义 adapter Protocol + 内存假设备；默认测试不得枚举 USB。
6. **错误契约**：新错误按 `core/errors.py` 的 `ErrorCode`/`DomainError` 结构化（稳定码 + ASCII message + JSON 安全上下文），不靠中文异常文本分支；同时保持与参考错误语义的映射可审计。
7. M04 状态行：ISSUE-019～023 均为 `Planned`（L7/44/81/118/155），ISSUE-019 为 M04 第一个可执行 Issue；依赖表（README.md L86）无计划冲突。

## 4. 门禁基线（核查时实测复跑，2026-09-01）

环境：WSL Ubuntu / Python 3.12.3；numpy 2.5.2、pytest 8.4.2、h5py 3.16.0、ruff 0.16.4、mypy 1.20.2；`uav_gpr` editable 可导入（`src/`）；`.venv/Scripts/python.exe` 为 Windows venv，WSL 侧以等价 `python3` 执行（与 ISSUE-018 基线单同口径）。

```text
$ python3 -m pytest tests/unit/test_reference_manifest.py tests/contract/test_acquisition_backend.py -q
41 passed in 0.93s                            # 依赖定向（ISSUE-001：13 + ISSUE-015：28）

$ python3 tools/quality/verify.py
742 passed, 1 deselected in 130.39s (0:02:10) # 全量非硬件 pytest
All checks passed!                             # ruff
Success: no issues found in 39 source files    # mypy
package import ok                              # import 检查
[quality] all gates passed
VERIFY_EXIT=0                                  # verify.py 仅在全部通过时 exit 0
```

核查前后 `git status --porcelain=v1 -b` **逐字节一致**（仅 `## main...origin/main` 一行）；`git diff --check` clean；无新缓存/日志/实测数据残留（`.pytest_cache/`/`.mypy_cache/`/`.ruff_cache/` 已忽略，`git check-ignore` 确认；`.agent-teams/` 已忽略，非交付物）。

## 5. ISSUE-019 实施计划摘要（t2 执行契约，完整版见 t2 计划文档）

### 范围（M04 L16–20 原文口径 + 提示词）

1. 单新模块 `src/uav_gpr/acquisition/librevna/transport.py`：LibreVNA USB 传输层——VID/PID、endpoint 发现、claim/release、bulk read/write、协议 frame/CRC（`HEADER=0x5A` 帧、`crc32` IEEE、`encode_packet`、粘包拆包 `feed`，严格长度上限 8..4096，VNA_DATAPOINT 跳过 CRC 的参考行为保留在帧层语义中但不解析 datapoint）、严格长度上限、timeout/cancel、可取消 I/O、幂等关闭、结构化错误映射。
2. USB adapter 依赖注入（Protocol：`is_open/open/read(max_length, timeout_ms)/write/close` + 可取消扩展），无硬件黄金帧测试；fake adapter 注入，默认测试不枚举 USB、不 import pyusb。
3. 迁移清单按 REFERENCE_MIGRATION.md §5 模板写入计划文档：源 branch/HEAD/worktree status/文件 SHA-256（11 个 librevna 文件哈希与 manifest 对拍，见 3.3）、采用/排除行为（transport+frame/CRC 采用；VNADatapoint/SweepSettings/DeviceInfo/S11 排除）、目标模块、黄金样本来源（参考 tests 固定向量路径 + 读取时点哈希）、UAV 适配（可取消 I/O、错误契约对齐 `core/errors.py`）。

### 排除项（M04 L22–24 + 提示词，t2 不得越界）

不组装 VNADatapoint/sweep、不实现 backend（ISSUE-020/021）；不读取/迁移 UAV-GPR 旧采集代码；不改 `core/` 既有公共语义、不改 `backend.py` 已冻结契约；不改参考仓库；不 commit/push/merge、不创建/切换分支；不进入 ISSUE-020。

### 验收标准（M04 L26–30 原文，t2 不得削弱）

1. 黄金字节帧与参考对拍（固定向量：`5a080007c1f48315` ACK、`5a08000ff37c581b` REQ_DEV_INFO、`5a0800141fb53d91` SET_IDLE、`crc32("123456789")=0xCBF43926`）；拆包/CRC/长度/timeout 测试无需真机。
2. USB 失败各阶段（open/claim/read/write/close）都 release 资源、不吞异常（断言 release/dispose 被调用、异常被结构化包装）。
3. 普通测试不枚举真机（AST 守卫 + adapter 注入，测试文件不 import usb 根）。

### 测试矩阵要点（提示词必测项 + 依赖回归）

- 黄金帧编解码对拍（encode_packet/crc32/固定向量）、粘包拆包（分块 feed、噪声前缀、非法长度重对齐、半包等待、CRC 错丢弃）、长度上限（<8/>4096）；
- adapter 注入正常链路（open/read/write/close）、open/close 幂等、每个失败阶段（find 错、claim 错、read 超时、read 断开、write 断开、release 错）结构化错误 + 资源释放断言；
- cancel/timeout 不遗留句柄（cancel 中断在途 read → 结构化取消错误；close 后句柄清空）；
- 缺依赖友好错误（惰性加载语义）与「导入模块不加载 pyusb」；
- 回归：依赖定向 41 passed（ISSUE-001：13 + ISSUE-015：28）不被破坏；门禁复跑（定向新测试 + 全量 verify.py 742 passed/1 deselected 基线 + ruff + mypy + import + `git diff --check` + 工作树检查）；**测试禁固定 sleep**（事件/标志驱动）。

### inScope 精确路径（以任务契约 t2 inScope 为准；t2 完成登记 changedPaths 必须与此逐一相等；沿用 ISSUE-014～018 教训：精确文件路径，非 glob）

1. `src/uav_gpr/acquisition/librevna/transport.py`（新模块：传输层 + adapter 契约 + 结构化错误）
2. `tests/contract/test_librevna_transport.py`（新测试文件，失败测试优先）
3. `docs/plans/2026-08-30-issue-019-librevna-transport.md`（计划文档，t2 先落盘，含迁移清单/执行日志/设计决策/门禁数字）
4. `docs/issues/M04_LIBREVNA.md`（仅 ISSUE-019 状态行：`Planned → In progress → Review`，勿动其他条目）

（t1 基线单 `docs/reports/ISSUE_019_BASELINE_CONFIRMATION.md` 为 t1 交付物，不计入 t2 inScope；t2 outOfScope：`docs/reports/**`、`docs/REFERENCE_MIGRATION.md`、`docs/ACQUISITION.md`、`docs/reference-baselines/**`、`docs/TESTING.md`、`docs/adr/**`、`tools/**`、参考仓库（只读）、`src/uav_gpr/core/**`（只读消费）、`src/uav_gpr/storage/**`、`src/uav_gpr/acquisition/backend.py`（只读消费）。）

t2 验证命令按任务契约执行：`./.venv/Scripts/python.exe -m pytest tests/contract/test_librevna_transport.py -q`（定向，先红灯后绿灯）、`./.venv/Scripts/python.exe tools/quality/verify.py`（全量）、`-m ruff check src tests`、`-m mypy src`、`git diff --check && git status --porcelain=v1 -b`；本机 WSL 侧 `.venv/Scripts/python.exe`（Windows venv）不可直接执行，以等价 `python3`（3.12.3，editable src）执行并在执行日志注明解释器路径（同 ISSUE-018 基线单口径）。

若确需拆分模块/新增文件，t2 必须先停止并向 captain 报告，不得自行新增范围外文件。

## 6. 结论

ISSUE-019 开工基线已锁定：`main`/HEAD @ `c0cd067`（工作树完全干净、与 origin/main 同步 0/0）；两项直接依赖（ISSUE-001/015）的 tracked 代码、契约测试、合并提交与复审报告证据全部实测复现（001 经 `0ddbd81` PR 合入、复审总结 PASS；015 经 `2f11cd9` 合入、R2 PASS WITH CONDITIONS 后经 `579f92b` 标记 Done）；**ISSUE-019 是下一个可执行 Issue**（M04 状态行 `Planned`、无 transport 实现/测试/计划存在、依赖全绿）；reference-baselines manifest 冻结的 11 个钢筋仪 LibreVNA 候选源文件 SHA-256 与本地只读副本 `D:\博士任务\rebar-inspector` 实测**逐一相等**（哈希可用性成立，E: 盘不可达不影响迁移）；契约要点（黄金帧对拍、adapter 注入无硬件测试、默认不枚举 USB、失败各阶段资源释放不吞异常、严格长度上限、timeout/cancel、结构化错误、迁移清单模板、M04 状态行、精确 inScope 路径）已固化于第 3.5/5 节；门禁基线全绿（全量 742 passed / 1 deselected、ruff/mypy(39 文件)/import 全过、依赖定向 41 passed），核查前后 git 状态一致、无残留。**无阻塞项，可开工。** 本确认单即为 t2 实现与 t3 独立复审的权威基线件；t2 按第 5 节契约执行（先落盘计划文档 → 失败测试优先 → 最小实现 → M04 状态行 → 门禁 → 报告），完成后停止，不进入 ISSUE-020。

> 后续记录：本单为开工时点的基线快照，不随 t2 改动；t2 实际完成记录见其完成报告与 `docs/plans/2026-08-30-issue-019-librevna-transport.md`，t3 复审报告独立输出。
