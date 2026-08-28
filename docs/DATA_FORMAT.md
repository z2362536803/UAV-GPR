# 数据格式

版本：0.1
状态：`.rcscan` v2 设计基线，尚未实现

## 1. 格式族

正式数据沿用钢筋仪项目的格式家族：

- `.rcscan`：HDF5 任务数据；
- `.partial.rcscan`：正在写入或未完成恢复的 HDF5 任务数据；
- `.rcal`：JSON 校准配置；
- `.rcbg`：JSON 空采背景参考。

UAV 文件根属性必须包含：

```text
format_name = "rcscan"
schema_version = 2
profile = "uav_gpr"
```

地面 reader 计划兼容钢筋仪 `rcscan` v1。写入时始终遵循 v2；升级旧文件必须显式产生新文件。

## 2. `.rcscan` v2 逻辑结构

```text
/
├── attrs
│   ├── format_name
│   ├── schema_version
│   ├── profile
│   ├── file_id
│   ├── file_role              # air | ground
│   ├── writer_version
│   └── lifecycle_state        # writing | finalized | recovered
├── mission/
│   ├── attrs: mission_id, device_id, created_utc, started_utc, ended_utc
│   ├── attrs: completion_kind, config_sha256
│   └── config_json
├── channels/
│   └── definitions_json
├── axes/
│   ├── frequencies_hz                      [frequency]
│   ├── time_base_s                         [time] optional
│   └── time_processed_s                    [time] optional
├── frequency/
│   ├── raw                                 [trace, channel, frequency]
│   └── calibrated                          [trace, channel, frequency] optional
├── time_base/
│   ├── data                                [trace, channel, time] optional
│   └── history_json
├── time_processed/
│   ├── data                                [trace, channel, time] optional
│   └── history_json
├── trace_metadata/
│   ├── trace_index                         [trace]
│   ├── trace_uid                           [trace]
│   ├── sweep_started_utc_ns                [trace]
│   ├── sweep_midpoint_utc_ns               [trace]
│   ├── sweep_finished_utc_ns               [trace]
│   ├── sweep_started_monotonic_ns          [trace]
│   ├── sweep_midpoint_monotonic_ns         [trace]
│   ├── sweep_finished_monotonic_ns         [trace]
│   ├── target_interval_s                   [trace]
│   ├── actual_interval_s                   [trace]
│   ├── schedule_error_s                    [trace]
│   ├── connection_generation              [trace]
│   └── raw_trace_sha256                    [trace]
├── gnss/
│   ├── valid                               [trace]
│   ├── invalid_reason                      [trace]
│   ├── received_utc_ns                     [trace]
│   ├── nmea_utc_ns                         [trace]
│   ├── latitude_deg                        [trace]
│   ├── longitude_deg                       [trace]
│   ├── altitude_msl_m                      [trace]
│   ├── geoid_separation_m                  [trace]
│   ├── fix_type                            [trace]
│   ├── satellites                          [trace]
│   ├── hdop                                [trace]
│   ├── ground_speed_mps                    [trace]
│   ├── course_deg                          [trace]
│   ├── match_age_s                         [trace]
│   └── raw_nmea                            [trace] optional
├── acquisition/
│   ├── device_status_json                  [trace]
│   └── quality_flags                       [trace]
├── transport/                              optional, role-specific
│   ├── sent_utc_ns                         [trace]
│   ├── ack_utc_ns                          [trace]
│   ├── retry_count                         [trace]
│   └── receive_status                      [trace]
└── checkpoints/
    ├── committed_record_count
    ├── last_trace_index
    └── updated_utc
```

最终实现可在不改变语义的前提下选择 HDF5 属性、定长字符串或列式数据集；任何具体 dtype/chunk 变化必须通过 schema 契约测试固定。

### 2.1 物理 schema 冻结（ISSUE-008）

ISSUE-008 把本节逻辑结构落实为如下物理契约定：

- 根属性：`format_name="rcscan"`、`schema_version=2`、`profile="uav_gpr"`、
  `file_id`、`file_role="air|ground"`、`writer_version`、`lifecycle_state`。
  `file_role=air` 必须存在 `/transport`；ground 端 `/transport` 为 role-specific
  optional：ground 文件可无该组，也可有同结构该组；若存在，`probe` 必须按冻结
  结构校验。ground 侧各列（receive/ACK/retry/receive_status）语义留待
  ISSUE-041/043 定义，本 Issue 只冻结物理结构。
- 数值列统一使用 little-endian：频率 `float64`、raw 复数 `complex128`、
  时间戳/计数 `int64`；浮点缺失用 NaN，并配显式 `valid` 布尔列；
  int64 缺失使用 `INT64_MIN`；变长文本用 UTF-8 vlen 字符串，
  `trace_uid` 固定 36 字节 ASCII、`raw_trace_sha256` 固定 64 字节 ASCII。
- `/frequency/raw` 为 trace-major 可扩展第一维，`shape=(0, c, f)`、
  `maxshape=(None, c, f)`、chunk `(1, c, f)`，暂不压缩（留待基准）；
  物理行是提交顺序，不等于 `trace_index`。
- `time_base`、`time_processed`、`frequency/calibrated`、`axes/time_*` 由
  后续处理阶段显式创建，初始骨架不创建。
- 逐道持久化为显式 trace-major 列：含
  `/gnss/received_monotonic_ns`、`match_method/usable/reason`、
  `/trace_metadata/quality_status/quality_reasons` 与 field-level
  presence bitmask。纯 projection codec
  `trace_metadata_to_cells()` / `trace_metadata_from_cells()` 是单一权威
  表示（single source of truth），不保留冗余 JSON row；写入与读取必须
  只通过该 codec 生成/重建物理行。
- 权威契约：`src/uav_gpr/storage/rcscan_v2.py`（常量/codec/创建器/probe）、
  `tests/contract/rcscan_v2_golden.json`（独立黄金 manifest）与
  `tests/contract/test_storage_schema.py`（黄金结构、哨兵、fail-closed 测试）。

## 3. 增量写入

- `frequency/raw` 使用 trace-major、可扩展第一维，建议 chunk `(1, channel_count, frequency_count)`；压缩算法需通过 CPU/写盘基准选择。
- 同一道的 raw、trace metadata、GNSS 和 checkpoint 构成一个逻辑提交。
- HDF5 不提供跨多个数据集的完整事务，因此 writer 必须先写数据、flush，再更新 `committed_record_count` 并再次 flush。
- reader 只把物理行号小于 `committed_record_count` 且所有必需列完整的记录视为已提交。
- HDF5 物理行是可靠提交顺序，不等同于逻辑 `trace_index`；空中端通常有序，地面端可以因补传而乱序。读取、对账和显示必须使用显式 `trace_index/trace_uid`，重复或冲突在追加前处理。
- 任务参数冻结后 axes/channel 不再改变；不兼容 sweep 必须拒绝并停止任务。
- writer 进程内只有一个所有者；其他线程通过有界命令队列提交不可变对象。

## 4. 文件生命周期

```text
create <file_id>.partial.rcscan
  -> lifecycle_state=writing
  -> append/flush/checkpoint each committed trace
  -> set mission end + completion_kind
  -> lifecycle_state=finalized
  -> close HDF5
  -> atomic rename to <mission_id>.rcscan
```

崩溃后：

1. 不自动覆盖或删除 partial 文件；
2. 只读扫描 schema、checkpoint、各数据集长度和逐道哈希；
3. 生成恢复报告；
4. 用户或受控策略确认后，截取到最后完整提交点并输出新的 recovered 文件；
5. 原 partial 文件继续保留。

## 5. 逐道原始哈希

空地一致性必须使用规范化字节流计算 `raw_trace_sha256`。规范至少固定：

1. 哈希版本标记；
2. `mission_id`、`trace_index`、`trace_uid` 的 UTF-8 规范形式；
3. 有序 channel ID；
4. 频率轴转为 little-endian float64 连续字节；
5. 原始复数数组转为 C-order little-endian complex128 连续字节。

哈希函数的确切 framing 必须在实现前写成契约样本，避免简单拼接的歧义。GNSS 不放入 raw hash，以便定位字段补正时不改变雷达原始数据身份；GNSS 自身可以有独立记录哈希。

### 5.1 精确 framing（ISSUE-009 冻结）

`raw_trace_sha256` 是下列字节流的一次 `SHA-256`（输出 64 位小写 hex）：

```text
raw_trace_sha256 = sha256(
    "UAVGPR-RAW-SHA256"             # 魔数（ASCII，无长度前缀，固定 17 字节）
    + uint64be(1)                   # 哈希版本 RAW_HASH_VERSION = 1
    + uint64be(len) + mission_id    # UTF-8，长度前缀
    + uint64be(len) + trace_uid     # UTF-8，长度前缀
    + uint64be(trace_index)         # 任务内单调道序号，固定宽度
    + uint64be(channel_count)
    + 每通道按序: uint64be(len) + channel_id   # UTF-8，长度前缀，顺序=channels 显式顺序
    + uint64be(frequency_count)
    + float64le(频率轴)              # 连续字节，C 序，little-endian
    + complex128le(raw)             # 连续字节，C 序（channel × frequency），little-endian
)
```

编码规则：

- **整数**：所有框架整数（版本、长度、`trace_index`、`channel_count`、`frequency_count`）统一为无符号 64 位大端（`uint64be`）；数值载荷（频率轴、raw）统一为 little-endian，与 ISSUE-008 冻结的 HDF5 `<f8`/`<c16` 列布局一致。
- **变长文本**：`mission_id`、`trace_uid`、`channel_id` 均以 `uint64be(len)` 长度前缀 + UTF-8 字节；`mission_id`/`trace_uid` 为规范小写 UUID 字符串，`channel_id` 遵循 `^[A-Za-z0-9_]+$`。长度前缀消除简单拼接的歧义（`"ab"+"c"` 与 `"a"+"bc"` 不可再坍缩为同一输入）。
- **数组规范化**：频率轴先规范为 little-endian float64，再在**规范值上**校验有限与严格递增，随后取连续字节；raw 按 `channel × frequency` 二维、复数值校验后取 C-order little-endian `complex128` 连续字节。实现只读输入，绝不修改领域数组（`astype(copy=False)` 在 dtype 已匹配时零拷贝）。校验必须作用于最终参与哈希的规范值，禁止在原始 dtype 上判定（无符号 `np.diff` 下溢、有符号极值溢出、转换后相邻值坍缩均不得绕过 fail-closed）。
- **通道顺序**：channel ID 按 `channels` 显式元组顺序逐个 framing，禁止按字典/窗口顺序推断。
- **GNSS 排除**：GNSS 字段（含 `GnssMatch` 全部内容）永不进入 raw hash；定位字段补正不改变雷达原始数据身份。GNSS 自身的独立记录哈希不在本 framing 内定义。
- **版本演进**：`RAW_HASH_VERSION = 1` 作为 framing 第一个字段；任何 framing 语义变化必须递增版本，禁止在同一版本内静默改变字节布局。
- **RawHashSpec JSON**：`RawHashSpec.to_dict()` 顶层携带 `spec_version`（JSON schema 版本）与 `hash_version`（framing 版本，当前均为 1）；`from_dict()` 只接受 v1，拒绝未知/缺失/错误类型版本字段，并冻结顶层与 channel 子对象的精确键集（未知/缺失键拒绝），防止损坏或未来版本 payload 被静默降级解释。

fail-closed 校验（任一违反即拒绝，结构化 `DomainError`）：

- 非规范 `mission_id`/`trace_uid`：非字符串类型 → `invalid_argument`；非规范 UUID 字符串（大写、缺横线、非法字符）→ `invalid_uuid`；
- `trace_index` 为负数或非 `int`（含 `bool`）→ `invalid_argument`；超过 `2**63 - 1` → `out_of_range`（上界与 ISSUE-008 `<i8` 存储列对齐，framing 内仍按 `uint64be` 编码）；
- channels 为空或 channel_id 重复 → `invalid_argument`/`duplicate_channel`；
- 频率轴非一维/空/非有限/非严格递增（在规范 `<f8` 值上判定）→ `axis_mismatch`/`invalid_argument`/`non_finite_axis`/`non_increasing_axis`；
- raw 非二维/非数值 dtype/shape 与 channels×频率不匹配 → `dtype_mismatch`/`shape_mismatch`。

权威契约：`src/uav_gpr/core/raw_hash.py`（常量/framing/校验/`RawHashSpec`）、
`tests/contract/raw_trace_hash_golden.json`（独立黄金向量，含 expected SHA256 与生成参数）与
`tests/contract/test_raw_trace_hash.py`（黄金对拍、等价布局/字节序、变化敏感、歧义消除、
fail-closed、GNSS 排除、输入不可变、ISSUE-008 列契约兼容）。

## 6. 空地文件差异

空中端和地面端文件不要求整文件相同：

- 两端必须相同：任务 ID、道索引/UID、频率轴、通道、原始数组、逐道 raw hash 和接收到的 GNSS 记录。
- 地面端可以增加：校准频域、时域数据、处理历史、人工注释和完整性报告。
- transport 状态在两端含义不同。

因此一致性工具逐字段/逐道比较，不比较 HDF5 文件 SHA256。

## 7. 缺失值

- 浮点缺失使用 NaN，但必须配合 `valid`/reason 字段，不能仅靠 NaN 推断原因。
- 时间缺失使用专门有效位或约定的整数哨兵，schema 中固定。
- GNSS 无效记录仍占对应 trace 行，保持所有 trace-major 数据集等长。
- 首道实际间隔可以缺失。

## 8. `.rcal` 与 `.rcbg`

延续钢筋仪项目的 JSON 思路，至少包含：

- `format_name`、`schema_version`、profile/reference ID；
- 创建 UTC、软件版本、设备/端口/通道；
- 完整频率轴与扫频配置摘要；
- 复数值的明确编码；
- 采集道数、统计和质量报告；
- OSL 标准件/算法或空采数据域；
- 内容摘要。

加载时严格检查通道、S 参数、频率轴、数据域和算法版本。用户选择文件不等于兼容，也不等于自动完成物理校准。

## 9. v1 兼容与迁移

- reader 识别 `schema_version=1` 并映射到内存领域模型。
- v1 没有任务/GNSS/transport 字段时保持为空，不生成假值。
- 若需要把 v1 保存为 v2，生成新的 `mission_id/file_id`，记录迁移 provenance 和源文件哈希。
- 不原地升级，不把 v2 专有字段塞入仍标记 v1 的文件。

## 10. 隐私与保留

GNSS 轨迹属于敏感现场数据。导出、日志和诊断包默认最小化位置数据；公开测试夹具必须使用合成坐标。空中端副本和地面端副本的保留/清理策略由部署文档和用户操作共同控制。
