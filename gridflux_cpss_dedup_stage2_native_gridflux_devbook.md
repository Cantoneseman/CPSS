# GridFlux + CPSS 文件级/块级去重项目开发书

## 第二阶段：GridFlux 原生 Staging 集成版

版本：v1.0  
日期：2026-07-27  
定位：项目交付版第二阶段。在第一阶段外置 staging 验证稳定并证明正向优化价值之后，将文件级去重、固定块级去重和 CPSS 压缩能力原生集成到 GridFlux 的目录传输 staging 阶段。

## 1. 背景与目标

第一阶段采用外置 Python staging runner，在 GridFlux 传输热路径之外完成：

```text
文件级去重 -> 固定块级去重 -> unique block CPSS 压缩 -> GridFlux bundle 传输 -> 上海端重建校验
```

第二阶段目标是在第一阶段证明机制正确、稳定、有正向优化价值后，将该机制集成到 GridFlux 原生目录传输流程中，使其成为 GridFlux 的可选 staging 能力。

第二阶段不是立即实现生产级持久 cache，也不是一上来做热度分层。第二阶段重点是：

1. GridFlux 原生支持 dedup staging mode。
2. GridFlux 原生目录传输流程可以识别重复文件和重复 block。
3. 只传输 unique compressed block 和 manifest。
4. 接收端由 GridFlux staging import/rebuild 逻辑完成目录重建。
5. 保持可开关，默认不影响原有 GridFlux raw tree transfer。
6. 为后续生产版 persistent cache、热度分层和长期服务化打好接口基础。

## 2. 阶段关系

### 2.1 第一阶段和第二阶段的区别

第一阶段：

```text
science-compressor 外置 runner
GridFlux 只负责传输 staging bundle
不进入 GridFlux 热路径
用于验证 correctness 和收益
```

第二阶段：

```text
GridFlux 原生 tree transfer 增加 staging mode
GridFlux 负责扫描、manifest、去重调度和传输编排
CPSS 作为压缩 adapter/sidecar 被 GridFlux 调用
用于项目级集成展示
```

### 2.2 第二阶段和生产版的区别

第二阶段仍属于项目版，不是最终生产版。

第二阶段不做：

1. 跨任务持久 cache。
2. 热度分层。
3. cache eviction。
4. 多租户隔离。
5. 长期 daemon 服务。
6. RocksDB/Raft 等复杂状态系统。
7. 全链路高并发调度优化。

第二阶段要做的是 GridFlux 原生 run-local dedup staging。

## 3. 总体设计

GridFlux 目录传输增加一个可选模式：

```text
--staging-mode cpss-dedup
```

启用后，GridFlux tree upload 不直接发送原始文件，而是执行：

```text
tree scan
  -> file hash
  -> file-level dedup
  -> fixed-size block planning
  -> block hash
  -> block-level dedup
  -> unique block compression via CPSS adapter
  -> transfer staging manifest + unique compressed blocks
  -> receiver import/decompress/rebuild
  -> receiver verify
```

未启用该模式时，GridFlux 原有目录传输行为不变。

## 4. 核心命令形态

建议在 `gridflux-tree-upload-client` 增加参数：

```bash
gridflux-tree-upload-client \
  --host <server> \
  --port <port> \
  --source-dir <dataset_dir> \
  --dest-dir <remote_dest> \
  --staging-mode cpss-dedup \
  --dedup-block-size 8388608 \
  --cpss-command "python -m Compressor.cli compress" \
  --cpss-selector hybrid \
  --cpss-top-k 3 \
  --json-summary <summary.json> \
  --event-log <events.jsonl>
```

建议在 server 或接收端增加对应参数：

```bash
gridflux-file-server \
  --output-dir <root> \
  --enable-staging-import cpss-dedup \
  --cpss-decompress-command "python -m Compressor.cli decompress"
```

如果当前 GridFlux server 形态不适合直接承载 import/rebuild，可以先增加单独工具：

```text
gridflux-staging-import
```

但第二阶段目标仍应归入 GridFlux-Beta 项目，而不是继续完全留在 science-compressor 外置脚本中。

## 5. 推荐新增或修改模块

基于现有 GridFlux-Beta 结构，建议新增：

```text
src/staging/
  staging_manifest.h
  staging_manifest.cpp
  cpss_dedup_planner.h
  cpss_dedup_planner.cpp
  fixed_block_hasher.h
  fixed_block_hasher.cpp
  cpss_adapter.h
  cpss_adapter.cpp
  staging_importer.h
  staging_importer.cpp
  staging_rebuilder.h
  staging_rebuilder.cpp
```

测试：

```text
tests/unit/staging_manifest_test.cpp
tests/unit/fixed_block_hasher_test.cpp
tests/unit/cpss_dedup_planner_test.cpp
tests/unit/staging_rebuilder_test.cpp
tests/integration/cpss_dedup_tree_transfer_test.cpp
```

配置：

```text
src/config/staging_options.h
src/config/staging_options.cpp
tests/unit/staging_options_test.cpp
```

文档：

```text
docs/CPSS_DEDUP_STAGING.md
docs/perf/CPSS_DEDUP_STAGE2_VALIDATION.md
```

## 6. 模块职责

### 6.1 `staging_options`

负责解析 CLI 参数。

新增参数建议：

```text
--staging-mode off|cpss-dedup
--dedup-block-size <bytes>
--cpss-command <command>
--cpss-decompress-command <command>
--cpss-selector model|hybrid|exhaustive
--cpss-top-k <N>
--cpss-model-dir <path>
--cpss-pipelines <whitelist>
--staging-work-dir <path>
--keep-staging
--verify-rebuild
--on-compress-fail fail|raw
```

默认：

```text
--staging-mode off
--dedup-block-size 8388608
--cpss-selector hybrid
--cpss-top-k 3
--verify-rebuild true
--on-compress-fail fail
```

### 6.2 `fixed_block_hasher`

负责文件级和块级 hash。

功能：

1. 顺序读取文件。
2. 计算 file SHA-256。
3. 按固定大小切 block。
4. 计算 block SHA-256。
5. 输出 file/block planning 记录。

第一版可以同步实现，后续再并行化。

### 6.3 `cpss_dedup_planner`

负责根据 tree scan 结果生成去重传输计划。

输入：

```text
source_dir
tree manifest
block_size
```

输出：

```text
staging manifest
unique block list
duplicate file refs
duplicate block refs
dedup stats
```

去重范围：

```text
本次 GridFlux tree upload 内部
```

不查询跨 run cache。

### 6.4 `cpss_adapter`

负责调用 CPSS。

第二阶段不建议把 CPSS Python 压缩算法重写到 C++，而是先使用 adapter：

```text
GridFlux C++ -> cpss_adapter -> python -m Compressor.cli compress/decompress
```

adapter 职责：

1. 构造 CPSS compress 命令。
2. 执行 unique block 压缩。
3. 解析 CPSS 输出。
4. 记录 pipeline、compressed_size、compress_time。
5. 失败时返回明确错误。

后续生产版可将 CPSS 包装成长期 sidecar 服务，避免频繁拉起 Python 进程。

### 6.5 `staging_manifest`

负责 manifest schema。

要求：

1. JSON 可读。
2. schema version 明确。
3. 支持文件级重复引用。
4. 支持 block 级重复引用。
5. 支持 CPSS block 元数据。
6. 支持接收端重建。
7. 支持结果统计。

manifest schema 应尽量与第一阶段保持一致，减少迁移成本。

### 6.6 `staging_importer`

接收端导入 staging bundle。

职责：

1. 读取 manifest。
2. 校验 bundle 完整性。
3. 校验 `.cpss` 文件 SHA-256。
4. 调用 CPSS decompress。
5. 校验 raw block hash。
6. 准备 rebuild 输入。

### 6.7 `staging_rebuilder`

接收端重建目标目录。

职责：

1. 按 manifest 的 file/block 顺序重建文件。
2. 处理重复文件引用。
3. 处理重复 block 引用。
4. 写入目标路径。
5. 计算文件 SHA-256。
6. 输出 verify report。

## 7. 数据流设计

### 7.1 发送端

```text
source_dir
  -> tree scan
  -> file/block hash
  -> dedup plan
  -> CPSS compress unique blocks
  -> local staging bundle
  -> GridFlux transfer bundle
```

本地 staging bundle：

```text
<staging_work_dir>/<transfer_id>/
  manifest.json
  blocks/
    <block_hash>.cpss
  summary.json
  logs/
```

### 7.2 传输层

传输层可以复用已有 tree transfer。

第二阶段初版可将 staging bundle 当作目录传输：

```text
gridflux-tree-upload-client transfers staging bundle
```

更深入的后续版本再把 manifest 和 block payload 映射到 GridFlux 原生 frame/chunk。

### 7.3 接收端

```text
incoming staging bundle
  -> manifest validation
  -> decompress unique blocks
  -> rebuild destination tree
  -> file SHA verify
  -> final summary
```

目标输出：

```text
<dest-dir>/
  原始目录结构
```

临时目录：

```text
/tmp/gridflux-native-staging/<transfer_id>/
  incoming/
  raw_blocks/
  rebuild_tmp/
  logs/
```

校验完成后删除临时目录，除非指定：

```text
--keep-staging
```

## 8. Manifest 设计要求

第二阶段 manifest 应保持与第一阶段兼容，字段可扩展。

建议字段：

```json
{
  "schema_version": 1,
  "staging_mode": "cpss-dedup",
  "transfer_id": "...",
  "source_dir": "...",
  "dest_dir": "...",
  "block_size_bytes": 8388608,
  "files": [],
  "unique_blocks": [],
  "stats": {},
  "cpss": {
    "selector": "hybrid",
    "top_k": 3,
    "model_dir": "",
    "pipelines": ""
  },
  "gridflux": {
    "connections": 2,
    "chunk_size": 1048576,
    "checksum": "crc32c"
  }
}
```

必须保证：

1. manifest 能单独解释目录如何重建。
2. manifest 不依赖发送端临时路径。
3. manifest 不依赖持久 cache。
4. manifest 中所有 block 引用都有唯一来源。
5. manifest 中所有 file 都有 SHA-256。

## 9. 指标与事件日志

GridFlux event-log 应增加 staging 事件。

建议事件：

```text
staging_start
staging_tree_scan_complete
staging_file_hash_complete
staging_block_hash_complete
staging_dedup_plan_complete
staging_cpss_compress_start
staging_cpss_compress_complete
staging_bundle_ready
staging_transfer_start
staging_transfer_complete
staging_import_start
staging_import_complete
staging_rebuild_start
staging_rebuild_complete
staging_verify_complete
```

每个事件至少记录：

```text
timestamp
transfer_id
stage
elapsed_seconds
bytes
status
error_code
message
```

summary 指标：

```text
raw_bytes
file_count
duplicate_file_count
file_dedup_saved_bytes
block_total_count
block_unique_count
block_duplicate_count
block_dedup_saved_bytes
unique_raw_bytes
compressed_unique_bytes
manifest_bytes
transferred_bytes
staging_time_s
hash_time_s
compress_time_s
gridflux_transfer_time_s
remote_import_time_s
remote_rebuild_time_s
remote_verify_time_s
end_to_end_time_s
logical_bandwidth_gain
sha256_ok
roundtrip_ok
```

## 10. 与原有 GridFlux 行为的兼容

第二阶段必须保证：

1. 默认 `--staging-mode off`。
2. 不影响原有 file upload。
3. 不影响原有 tree upload。
4. 不影响 resume/checksum/event-log 的已有功能。
5. 不改变未开启 staging 时的 CLI 行为。
6. 失败时错误信息清晰，不吞掉原有 GridFlux 错误。

如果启用 `--staging-mode cpss-dedup`，但 CPSS 不可用：

```text
直接失败并提示 check-env
```

不要自动退回 raw transfer，除非用户显式指定：

```text
--on-compress-fail raw
```

## 11. 错误处理

### 11.1 发送端错误

1. 文件读取失败：transfer failed。
2. file hash 失败：transfer failed。
3. block hash 失败：transfer failed。
4. CPSS 压缩失败：默认 transfer failed。
5. staging bundle 写入失败：transfer failed。
6. manifest 校验失败：transfer failed。

### 11.2 传输错误

复用 GridFlux 原有错误处理：

1. 连接失败。
2. checksum 失败。
3. 超时。
4. resume 失败。

staging mode 必须把这些错误透传到 summary。

### 11.3 接收端错误

1. manifest 缺失：import failed。
2. `.cpss` 缺失：import failed。
3. `.cpss` sha256 不一致：import failed。
4. CPSS 解压失败：import failed。
5. raw block hash 不一致：import failed。
6. 文件重建 SHA 不一致：verify failed。

任一校验失败，目标目录不能标记为成功。

## 12. 验证策略

### 12.1 单元测试

新增：

```text
staging_options_test
fixed_block_hasher_test
cpss_dedup_planner_test
staging_manifest_test
staging_rebuilder_test
```

覆盖：

1. 参数解析。
2. manifest schema。
3. 空文件。
4. 小文件。
5. 正好一个 block。
6. 多 block。
7. 重复文件。
8. 重复 block。
9. 缺失 block。
10. hash mismatch。

### 12.2 集成测试

构造目录：

```text
tree/
  a.bin
  b_duplicate_of_a.bin
  c_share_first_block.bin
  empty.txt
```

运行：

```text
gridflux-tree-upload-client --staging-mode cpss-dedup ...
```

预期：

1. 文件级重复命中。
2. block 级重复命中。
3. 只传 unique `.cpss` block。
4. 接收端目录完整重建。
5. SHA-256 全通过。
6. summary 指标正确。

### 12.3 回归测试

必须保留原有 GridFlux 测试：

```text
gridflux_unit_tests
tree transfer tests
file transfer tests
checksum tests
manifest tests
resume tests
```

并新增断言：

```text
--staging-mode off 时行为不变
```

## 13. 性能要求

第二阶段仍不追求生产级极限性能，但必须避免明显不可接受的设计。

最低要求：

1. hash 阶段顺序读文件，不重复扫描过多次。
2. CPSS 只处理 unique block。
3. staging bundle 不写重复 raw block。
4. 传输阶段只传 compressed unique block。
5. manifest 不应膨胀到不可接受大小。

建议初始并发：

```text
--staging-hash-workers 1
--staging-compress-workers 1
```

后续可扩展：

```text
--staging-hash-workers <N>
--staging-compress-workers <N>
```

但第二阶段第一版可以先串行，保证 correctness。

## 14. 与 CPSS 的集成方式

第二阶段推荐使用 CPSS adapter，而不是把 CPSS 算法改写到 GridFlux C++。

原因：

1. CPSS 当前主要是 Python 栈。
2. CPSS selector/model/管线逻辑已经成熟。
3. 直接 C++ 重写风险大。
4. adapter 方式能快速原生接入 GridFlux 编排。

adapter 初版：

```text
fork/exec python -m Compressor.cli compress
fork/exec python -m Compressor.cli decompress
```

后续生产版：

```text
CPSS sidecar service
GridFlux 通过本地 IPC/gRPC 调用
```

第二阶段只需要把 adapter 接口设计清楚：

```text
compress(raw_block_path, cpss_output_path, options) -> CpssResult
decompress(cpss_path, raw_output_path) -> CpssResult
```

## 15. 开发顺序

建议按以下顺序：

1. 补充 GridFlux staging options。
2. 新增 staging manifest 数据结构。
3. 新增 fixed block hasher。
4. 新增 cpss dedup planner。
5. 新增本地 staging bundle 生成。
6. 新增 CPSS adapter。
7. 修改 tree upload client，支持 `--staging-mode cpss-dedup`。
8. 新增接收端 staging importer。
9. 新增接收端 rebuilder。
10. 接入 event-log 和 json-summary。
11. 写单元测试。
12. 写本地 loopback 集成测试。
13. 在深圳->上海环境跑小规模验证。
14. 编写 `docs/CPSS_DEDUP_STAGING.md`。

## 16. 里程碑

### Milestone 2A：GridFlux 原生 run-local dedup

目标：

```text
GridFlux tree upload 原生支持本次传输内部的文件级/块级去重。
```

验收：

1. `--staging-mode cpss-dedup` 可用。
2. 重复文件/重复 block 命中。
3. 接收端重建 SHA 全通过。
4. 原有 tree upload 不受影响。

### Milestone 2B：CPSS adapter 稳定化

目标：

```text
CPSS compress/decompress 作为 GridFlux staging adapter 稳定运行。
```

验收：

1. CPSS 压缩失败能准确返回错误。
2. CPSS 解压失败能准确返回错误。
3. pipeline 信息进入 summary。
4. `check-env` 能在实验前检测。

### Milestone 2C：小规模跨域验证

目标：

```text
深圳 -> 上海真实 GridFlux 环境跑通。
```

验收：

1. smoke 数据集通过。
2. 真实小样本通过。
3. event-log 完整。
4. summary 完整。
5. cleanup 干净。

## 17. 不纳入第二阶段初版的内容

以下内容留给后续生产版：

1. persistent cache。
2. cold/warm cache。
3. hot/warm/cold 热度分层。
4. cache eviction。
5. cache GC。
6. 跨任务 block 复用。
7. 多用户隔离。
8. sidecar 常驻服务。
9. 高并发压缩/传输流水。
10. GridFlux frame 级 payload 去重。

## 18. 预留扩展点

虽然第二阶段不实现 cache，但可以预留字段：

```text
cache_mode: "run-local"
cache_namespace: transfer_id
block_ref_type: "inline-transfer"
access_count: 1
saved_bytes
```

这些字段不参与第二阶段逻辑，只为后续生产版迁移减少 schema 变更。

未来生产版可以扩展为：

```text
cache_mode: run-local|persistent
cache_policy: none|lru|lfu|hybrid
cache_tier: hot|warm|cold
```

## 19. 项目交付表达口径

第二阶段可以这样描述：

```text
在第一阶段外置 staging 验证文件级去重和块级去重机制有效后，项目进一步将该机制集成到 GridFlux 目录传输的原生 staging 阶段。启用 cpss-dedup staging 模式后，GridFlux 在传输前完成目录扫描、文件级去重、固定块级去重和 CPSS 压缩，仅传输唯一压缩块和重建 manifest，接收端根据 manifest 完成目录重建和 SHA-256 校验。该设计保持默认传输路径不变，通过可选模式提供去重压缩能力。
```

这个表述强调：

1. 去重是 GridFlux 原生能力。
2. 仍然可选，不破坏原传输。
3. 文件级/块级去重满足项目要求。
4. CPSS 与 GridFlux 完成实质集成。
5. 没有把未完成的 cache/热度分层硬塞进项目版。

## 20. 风险与处理

### 20.1 C++ 集成复杂度上升

风险：GridFlux C++ 主项目改动比外置 Python runner 更复杂。

处理：

1. 保持第一阶段 manifest schema。
2. 先做 run-local dedup。
3. CPSS 通过 adapter 接入。
4. 不改底层传输协议。

### 20.2 CPSS Python 进程启动开销

风险：每个 block 调一次 Python CLI 会慢。

处理：

1. 第二阶段先接受该开销，用于项目集成。
2. 指标中记录 compress_time。
3. 生产版改为 CPSS sidecar。

### 20.3 影响原有 GridFlux 稳定性

风险：新 staging mode 引入回归。

处理：

1. 默认关闭。
2. 原有测试必须通过。
3. `--staging-mode off` 行为不变。
4. 新功能独立模块化。

### 20.4 manifest 体积过大

风险：大量小 block 导致 manifest 很大。

处理：

1. 默认 8MiB block。
2. manifest 只记录必要字段。
3. 小文件可按完整文件作为单 block。
4. 后续生产版再做二进制 manifest 或压缩 manifest。

## 21. 第二阶段最终交付物

代码：

```text
GridFlux-Beta/src/staging/*
GridFlux-Beta/src/config/staging_options.*
GridFlux-Beta/tests/unit/*staging*
GridFlux-Beta/tests/integration/cpss_dedup_tree_transfer_test.cpp
```

文档：

```text
GridFlux-Beta/docs/CPSS_DEDUP_STAGING.md
GridFlux-Beta/docs/perf/CPSS_DEDUP_STAGE2_VALIDATION.md
```

实验记录：

```text
ExperimentRecords/gridflux_native_cpss_dedup_<timestamp>/
  results.csv
  summary.json
  summary_zh.md
  event_log.jsonl
  command_log.txt
  cleanup_report.md
```

## 22. 一句话总结

第二阶段是在第一阶段外置 staging 证明稳定和有效之后，将文件级去重、固定块级去重和 CPSS 压缩作为可选 native staging mode 集成进 GridFlux 目录传输流程。该阶段仍不引入复杂持久 cache 和热度分层，而是先让 GridFlux 原生具备本次传输内部的去重压缩闭环能力，为后续算力网生产版打基础。
