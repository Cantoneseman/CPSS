# GridFlux + CPSS 文件级/块级去重项目开发书

## 第一阶段：外置 Staging 验证版

版本：v1.0  
日期：2026-07-27  
定位：项目交付版第一阶段，用外置 staging 方式验证“文件级去重 + 块级去重 + CPSS 压缩 + GridFlux 传输”的正确性、稳定性和正向优化价值。

## 1. 背景与目标

当前科学数据压缩项目已经具备 CPSS 压缩能力，并且已经与 GridFlux 做过跨域传输实验整合。项目新要求是在压缩之前增加文件级去重和块级去重。第一阶段不直接改 GridFlux 传输热路径，而是在 GridFlux 传输之前增加外置 staging 预处理流程。

第一阶段目标是证明：

1. 文件级去重可以识别输入目录内完全重复文件。
2. 块级去重可以识别不同文件之间或同一文件内部的重复固定大小 block。
3. CPSS 只需要压缩 unique block。
4. GridFlux 只需要传输 unique compressed block 和 manifest。
5. 上海端可以根据 manifest 正确重建原始目录。
6. 所有重建文件 SHA-256 与源端一致。
7. 在存在重复内容的数据上，`dedup + CPSS + GridFlux` 相比 `CPSS only + GridFlux` 具有正向传输优化价值。

第一阶段不追求生产级吞吐，不引入持久 cache、热度分层、cache 淘汰、CDC 内容定义分块，也不改 GridFlux C++ 主流程。

## 2. 总体范围

### 2.1 本阶段必须实现

1. 外置 staging runner。
2. 输入目录扫描。
3. 文件级 SHA-256 去重。
4. 固定大小块级 SHA-256 去重。
5. unique block 的 CPSS 压缩。
6. staging bundle 生成。
7. GridFlux tree upload 传输 staging bundle。
8. 上海端 staging bundle 导入。
9. 上海端按 manifest 重建原始目录。
10. 源端与上海端文件级 SHA-256 校验。
11. 结果记录：CSV、JSON、中文总结。
12. 自动清理临时现场，只保留实验记录。

### 2.2 本阶段不实现

1. 不做 cold/warm cache 实验口径。
2. 不做跨实验持久 cache。
3. 不做热度分层算法。
4. 不做 LRU/LFU cache 淘汰。
5. 不做 CDC 内容定义分块。
6. 不改 GridFlux C++ 传输热路径。
7. 不做多节点分布式去重索引。
8. 不做长期 daemon 服务。

### 2.3 去重范围

第一阶段去重范围是单次 staging run 内部。

也就是说：

1. 输入目录内重复文件可以命中文件级去重。
2. 输入目录内重复 block 可以命中块级去重。
3. 该 run 结束后临时 dedup index 可以清理。
4. 下一次 run 不复用上一轮 dedup index。

这个口径适合作为项目交付验证，因为它公平、独立、容易解释。

## 3. 现有项目基础

深圳服务器已有两个项目目录：

```text
/root/projects/science-compressor
/root/projects/GridFlux-Beta
```

`science-compressor` 中已有能力：

```text
Compressor/cli.py                    # CPSS compress/decompress/inspect/check-env
Compressor/selector.py               # CPSS selector
Script/sample_dataset_preserve_format.py
Script/run_ratio3_gridflux_experiment.py
```

`GridFlux-Beta` 中已有能力：

```text
build/gridflux-file-client
build/gridflux-file-server
build/gridflux-tree-upload-client
build/gridflux-tree-download-client
event-log
checksum
resume
tree transfer
```

第一阶段应优先复用这些能力，而不是重写传输或压缩底层。

## 4. 总体流程

第一阶段完整流水如下：

```text
输入目录
  -> staging 扫描
  -> 文件级 SHA-256 去重
  -> 非重复文件固定大小 block 切分
  -> block SHA-256 去重
  -> unique block 写入 raw block staging
  -> unique block 调用 CPSS 压缩
  -> 生成 staging bundle
       - manifest.json
       - blocks/*.cpss
       - metadata/*.json
  -> GridFlux tree upload 传输 bundle 到上海
  -> 上海端校验 bundle
  -> 上海端解压 unique block
  -> 上海端按 manifest 重建原始目录
  -> 上海端计算文件 SHA-256
  -> 对比源端 SHA-256
  -> 输出结果
  -> 清理临时文件
```

## 5. 推荐新增文件

建议新增：

```text
Compressor/dedup.py
Compressor/dedup_manifest.py
Script/gridflux_cpss_dedup_staging.py
tests/test_dedup.py
tests/test_dedup_manifest.py
tests/test_gridflux_cpss_dedup_staging.py
```

如果希望第一阶段更快落地，也可以先把核心逻辑集中在 `Script/gridflux_cpss_dedup_staging.py`，但 manifest schema 和 hash/分块逻辑应尽量独立出来，便于第二阶段迁移到 GridFlux。

## 6. 模块设计

### 6.1 `Compressor/dedup.py`

职责：

1. 计算文件 SHA-256。
2. 计算 block SHA-256。
3. 固定大小切分文件。
4. 维护单次 run 内部的文件级去重表。
5. 维护单次 run 内部的 block 级去重表。
6. 输出 dedup 统计。

建议核心数据结构：

```python
@dataclass
class FileRecord:
    file_id: str
    rel_path: str
    size_bytes: int
    sha256: str
    is_file_duplicate: bool
    duplicate_of_file_id: str | None
    blocks: list[BlockRef]

@dataclass
class BlockRecord:
    block_id: str
    block_hash: str
    file_id: str
    block_index: int
    offset: int
    size_bytes: int
    is_block_duplicate: bool
    duplicate_of_block_id: str | None

@dataclass
class DedupStats:
    raw_bytes: int
    file_count: int
    unique_file_count: int
    duplicate_file_count: int
    file_dedup_saved_bytes: int
    block_total_count: int
    block_unique_count: int
    block_duplicate_count: int
    block_dedup_saved_bytes: int
    unique_raw_bytes: int
```

### 6.2 `Compressor/dedup_manifest.py`

职责：

1. 定义 staging manifest schema。
2. 生成 manifest。
3. 读取并校验 manifest。
4. 为上海端重建提供确定顺序。

manifest 必须是 JSON，便于调试和验收。

### 6.3 `Script/gridflux_cpss_dedup_staging.py`

职责：

1. 命令行入口。
2. 调用 dedup 扫描。
3. 生成 raw unique blocks。
4. 调用 CPSS CLI 压缩 unique blocks。
5. 生成 staging bundle。
6. 调用 GridFlux tree upload。
7. SSH 到上海端执行 import/rebuild/verify。
8. 记录指标。
9. 清理现场。

建议命令：

```bash
python Script/gridflux_cpss_dedup_staging.py \
  --input-dir <dataset_dir> \
  --remote-host gridflux-beta-shanghai \
  --remote-root /tmp/gridflux_cpss_dedup_stage1 \
  --block-mib 8 \
  --selector hybrid \
  --top-k 3 \
  --gridflux-tree-upload /root/projects/GridFlux-Beta/build/gridflux-tree-upload-client \
  --gridflux-port 25252 \
  --result-dir ExperimentRecords/dedup_stage1_<timestamp>
```

## 7. 分块策略

第一阶段只做固定大小分块。

默认：

```text
block_size = 8 MiB
```

支持参数：

```text
--block-mib 1
--block-mib 4
--block-mib 8
--block-mib 16
--block-mib 32
```

分块规则：

1. 每个文件从 offset 0 开始顺序切分。
2. 非尾块大小等于 `block_size`。
3. 尾块可以小于 `block_size`。
4. block hash 使用原始 block 内容 SHA-256。
5. 空文件作为特殊文件记录，不生成 block。

不做 CDC 的原因：

1. 固定分块速度快。
2. 实现简单稳定。
3. 不会在传输前堆积过多计算。
4. 指标容易解释。
5. 满足项目阶段要求。

## 8. 文件级去重策略

文件级去重使用完整文件 SHA-256。

判断规则：

```text
same file_sha256 + same size_bytes => duplicate file
```

文件级重复命中后：

1. 不再切 block。
2. 不进入 CPSS 压缩。
3. 不传输该文件内容。
4. manifest 中记录该文件引用首次出现文件的 block list。
5. 上海端重建时按引用关系输出重复文件。

注意：

1. 相同文件内容但路径不同，应保留不同目标路径。
2. 文件权限、mtime 第一阶段只记录，不参与 SHA 校验。
3. 如果要保留权限/mtime，可在 manifest metadata 中记录，重建时尽力恢复。

## 9. 块级去重策略

对非文件级重复的文件进行 block 切分。

判断规则：

```text
same block_sha256 + same block_size => duplicate block
```

block 级重复命中后：

1. 不重复写 raw block staging。
2. 不重复 CPSS 压缩。
3. 不重复 GridFlux 传输。
4. manifest 中记录该 file block 引用已有 unique block。

unique block 处理：

```text
raw block -> CPSS compress -> <block_hash>.cpss -> staging bundle
```

## 10. CPSS 压缩策略

第一阶段只压缩 unique block。

默认参数：

```text
selector = hybrid
top_k = 3
```

支持：

```text
--selector model|hybrid|exhaustive
--top-k <N>
--pipelines <comma-separated whitelist>
--model-dir <path>
```

压缩输出命名：

```text
blocks/<block_hash>.cpss
```

每个 compressed block 需要记录：

```text
block_hash
raw_size
compressed_size
pipeline_name
pipeline_id
compress_time_s
cpss_sha256
```

压缩失败策略：

第一阶段默认：

```text
--on-compress-fail fail
```

可选扩展：

```text
--on-compress-fail raw
--on-compress-fail lz4
```

项目验证阶段建议默认 `fail`，避免结果口径变复杂。

## 11. Staging Bundle 格式

本地生成：

```text
<run_staging_dir>/
  manifest.json
  blocks/
    <block_hash>.cpss
  logs/
    compress_commands.log
    sha256.log
```

`manifest.json` 建议结构：

```json
{
  "schema_version": 1,
  "run_id": "dedup_stage1_20260727_120000",
  "created_at": "2026-07-27T12:00:00+08:00",
  "block_size_bytes": 8388608,
  "source_root": "/path/to/input",
  "files": [
    {
      "file_id": "file_000001",
      "rel_path": "a/example.bin",
      "size_bytes": 123456,
      "sha256": "...",
      "is_duplicate_file": false,
      "duplicate_of_file_id": null,
      "blocks": [
        {
          "block_index": 0,
          "offset": 0,
          "size_bytes": 8388608,
          "block_hash": "...",
          "is_duplicate_block": false,
          "unique_block_id": "block_000001"
        }
      ]
    }
  ],
  "unique_blocks": [
    {
      "unique_block_id": "block_000001",
      "block_hash": "...",
      "raw_size": 8388608,
      "cpss_path": "blocks/<block_hash>.cpss",
      "compressed_size": 1234567,
      "pipeline_name": "xz_lzma2",
      "pipeline_id": 1,
      "cpss_sha256": "..."
    }
  ],
  "stats": {
    "raw_bytes": 0,
    "file_dedup_saved_bytes": 0,
    "block_dedup_saved_bytes": 0,
    "unique_raw_bytes": 0,
    "compressed_unique_bytes": 0,
    "manifest_bytes": 0
  }
}
```

## 12. GridFlux 传输策略

第一阶段推荐使用 `gridflux-tree-upload-client` 传输整个 staging bundle。

原因：

1. manifest 和 blocks 可以作为一个目录整体传输。
2. 减少每个 block 单独启动传输的开销。
3. 方便复用 GridFlux 目录传输、并行文件传输和 event-log。
4. 接近未来原生 staging 的形态。

传输内容：

```text
manifest.json
blocks/*.cpss
必要 logs/metadata
```

不传输：

```text
duplicate files
duplicate blocks
raw blocks
```

## 13. 上海端导入和重建

上海端收到 staging bundle 后执行：

```text
1. 校验 manifest 存在且 schema_version 支持。
2. 校验每个 .cpss 文件 SHA-256。
3. 解压每个 unique .cpss block。
4. 计算 raw block SHA-256，必须等于 block_hash。
5. 按 files[].blocks 顺序拼接每个文件。
6. 对重建文件计算 SHA-256。
7. 与 manifest 中 file sha256 对比。
8. 输出 remote_verify.json。
```

远端临时目录：

```text
/tmp/gridflux_cpss_dedup_stage1/<run_id>/
  incoming/
  raw_blocks/
  rebuild/
  logs/
```

第一阶段不保留远端持久 cache。校验完成后清理 `incoming/raw_blocks/rebuild`，只将必要验证结果拉回本地记录目录。

## 14. 指标设计

`results.csv` 至少包含：

```text
run_id
dataset
input_dir
block_size_bytes
file_count
raw_bytes
file_dedup_hit_files
file_dedup_saved_bytes
block_total_count
block_unique_count
block_duplicate_count
block_dedup_saved_bytes
unique_raw_bytes
compressed_unique_bytes
manifest_bytes
transferred_bytes
raw_gridflux_transfer_time_s
cpss_only_compressed_bytes
cpss_only_transfer_time_s
dedup_scan_time_s
file_hash_time_s
block_hash_time_s
compress_time_s
gridflux_transfer_time_s
remote_decompress_time_s
remote_rebuild_time_s
remote_verify_time_s
end_to_end_time_s
compression_ratio_unique
dedup_ratio
logical_bandwidth_gain_vs_raw
logical_bandwidth_gain_vs_cpss_only
e2e_gain_vs_raw
e2e_gain_vs_cpss_only
sha256_ok
roundtrip_ok
status
error
```

关键公式：

```text
raw_bytes = 输入目录所有文件原始字节数
dedup_saved_bytes = file_dedup_saved_bytes + block_dedup_saved_bytes
unique_raw_bytes = raw_bytes - dedup_saved_bytes
transferred_bytes = compressed_unique_bytes + manifest_bytes
compression_ratio_unique = unique_raw_bytes / compressed_unique_bytes
dedup_ratio = raw_bytes / unique_raw_bytes
logical_bandwidth_gain_vs_raw = raw_bytes / transferred_bytes
logical_bandwidth_gain_vs_cpss_only = cpss_only_compressed_bytes / transferred_bytes
end_to_end_time_s = dedup_scan + hash + compress + transfer + remote_decompress + remote_rebuild + verify
```

## 15. 实验对比口径

第一阶段建议做三组对比：

### 15.1 Raw GridFlux

```text
原始输入目录 -> GridFlux tree upload
```

记录：

```text
raw_bytes
raw_gridflux_transfer_time_s
sha256_ok
```

### 15.2 CPSS Only + GridFlux

```text
原始输入目录按 block 压缩，不做去重 -> GridFlux 传输 compressed blocks -> 上海重建
```

记录：

```text
cpss_only_compressed_bytes
cpss_only_transfer_time_s
cpss_only_e2e_time_s
```

### 15.3 Dedup + CPSS + GridFlux

```text
文件级去重 + 块级去重 -> unique block CPSS 压缩 -> GridFlux 传输 -> 上海重建
```

记录：

```text
dedup_saved_bytes
compressed_unique_bytes
transferred_bytes
logical_bandwidth_gain
e2e_gain
```

## 16. 测试设计

### 16.1 单元测试

1. 文件 SHA-256 正确。
2. block SHA-256 正确。
3. 固定分块边界正确。
4. 空文件处理正确。
5. 小于 block size 的文件处理正确。
6. 正好等于 block size 的文件处理正确。
7. 大于 block size 且有尾块的文件处理正确。
8. 完全重复文件命中文件级去重。
9. 不同文件相同 block 命中块级去重。
10. manifest 生成和读取一致。

### 16.2 Smoke 测试

构造数据集：

```text
dataset_smoke/
  file_a.bin       # random block A + random block B
  file_b.bin       # file_a.bin 的完整复制
  file_c.bin       # random block A + random block C
  file_empty.txt
```

预期：

1. `file_b.bin` 命中文件级去重。
2. `file_c.bin` 的第一个 block 命中块级去重。
3. 只压缩 unique blocks。
4. 上海端四个文件都能重建。
5. SHA-256 全通过。

### 16.3 小规模真实数据测试

选择 1-2 个已有实验数据集，每个 100MB-1GB。

目标：

1. 真实数据能跑通。
2. 不残留现场。
3. 指标记录完整。
4. 即使真实数据重复率低，也能正确记录低命中情况。

## 17. 验收标准

第一阶段完成标准：

1. `py_compile` 通过。
2. 单元测试通过。
3. smoke 端到端通过。
4. 至少一个真实数据集端到端通过。
5. 文件级去重命中准确。
6. 块级去重命中准确。
7. CPSS 只处理 unique block。
8. GridFlux 只传输 staging bundle 中的 unique `.cpss` block 和 manifest。
9. 上海端重建 SHA-256 全通过。
10. 输出 `results.csv`、`summary.json`、`summary_zh.md`。
11. 失败时输出 `blocker_report.md`。
12. 本地和远端临时目录清理干净。

第一阶段正向优化判定：

```text
在构造重复 smoke 数据集上：
dedup + CPSS transferred_bytes < CPSS only transferred_bytes
dedup + CPSS logical_bandwidth_gain_vs_raw > CPSS only logical_bandwidth_gain_vs_raw
```

真实数据集如果重复率低，可以记录为低命中，不视为功能失败。

## 18. 风险和处理

### 18.1 真实数据重复率低

风险：真实科学数据可能重复 block 较少，去重收益不明显。

处理：

1. smoke 数据集用于证明功能有效。
2. 真实数据用于证明稳定性。
3. 报告中拆分 dedup 收益和 compression 收益，不混淆结论。

### 18.2 block size 影响命中率

风险：8MiB 固定块可能错过小粒度重复。

处理：

1. 第一阶段默认 8MiB。
2. 支持 1/4/8/16/32MiB 参数。
3. 实验可做 block size sensitivity。

### 18.3 CPSS block 压缩变慢

风险：block 数量多时 CPSS 压缩时间上升。

处理：

1. 第一阶段先串行或小并发。
2. 记录 compress_time_s。
3. 第二阶段再做并行流水。

### 18.4 manifest 重建错误

风险：重复引用关系错误导致重建 SHA 不一致。

处理：

1. manifest schema 强校验。
2. 每个 block 解压后校验 block_hash。
3. 每个文件重建后校验 file_sha256。
4. 任一失败即 run failed。

## 19. 推荐开发顺序

1. 实现 `Compressor/dedup.py`。
2. 实现 `Compressor/dedup_manifest.py`。
3. 实现本地 smoke dedup 扫描。
4. 实现 staging bundle 生成。
5. 实现本地 rebuild 验证。
6. 接入 CPSS 压缩 unique block。
7. 接入 GridFlux tree upload。
8. 实现上海端 remote rebuild/verify。
9. 实现 CSV/JSON/Markdown 结果输出。
10. 实现 cleanup 和 blocker report。
11. 跑 smoke。
12. 跑小规模真实数据。

## 20. 第一阶段最终交付物

代码：

```text
Compressor/dedup.py
Compressor/dedup_manifest.py
Script/gridflux_cpss_dedup_staging.py
tests/test_dedup.py
tests/test_dedup_manifest.py
tests/test_gridflux_cpss_dedup_staging.py
```

实验记录：

```text
ExperimentRecords/dedup_stage1_<timestamp>/
  results.csv
  summary.json
  summary_zh.md
  manifest.json
  command_log.txt
  sha256_log.txt
  cleanup_report.md
  blocker_report.md
```

结论材料：

```text
文件级去重命中统计
块级去重命中统计
CPSS 压缩收益
GridFlux 传输字节减少
上海端重建 SHA 校验结果
```

## 21. 一句话总结

第一阶段是外置 staging 验证版：不改 GridFlux 热路径，不做持久 cache，不做热度分层，只在单次输入目录内部完成文件级去重、固定块级去重、CPSS 压缩和 GridFlux 传输闭环，用实验数据证明该机制正确、稳定，并对传输字节数有正向优化价值。
