# 固定数据集与 gzip/lz4 对照实验设计

## 目标

围绕项目指标“基于 HPC/AI 的开放多模态数据集，跨域传输逻辑带宽平均提升 200%”，下一轮实验固定数据集候选池，并加入 gzip、lz4 两种固定压缩方法作为对照，比较 CPSS 在跨域传输逻辑带宽和端到端耗时上的收益。

本轮实验不追求覆盖所有数据集，而是建立可复用的标准流程：

1. 固定候选数据集。
2. 使用 CPSS calibrated sampling；默认采用 `random_sampling`，并保留 `fixed_sampling` 作为对照。
3. 使用 raw、gzip、lz4 作为对照。
4. 记录实时 block 级逻辑带宽波动。
5. 按结果保留或淘汰数据集。

## 数据集候选池

每个数据集规模控制在 512MiB 到 5GiB，优先接近 2GiB。真实样本不足目标时不做 duplicate padding，记录 small_representative。

第一轮建议固定 6 个数据集：

| 领域 | 数据集 | 预期定位 |
| --- | --- | --- |
| 生物结构文本 | RCSB-mmCIF | 强适配，主力展示 |
| 生物结构文本 | AlphaFold mmCIF subset | 强适配，主力展示 |
| 基因组文本 | NCBI Human GRCh38 FASTA | 强适配，但可能样本偏小 |
| 气象/气候 HPC | CESM-ATM Dataset1 | 中等适配，接近目标 |
| 气象/气候 HPC | Hurricane-ISABEL | 中等适配，有局部高压缩 block |
| 物理/模拟 HPC | Miranda | 不适配边界样本 |

## 方法对照

每个数据集测试以下方法：

| 方法 | 作用 |
| --- | --- |
| raw | 原始跨域传输基线 |
| gzip | 经典通用压缩对照 |
| lz4 | 高速压缩对照 |
| CPSS random_sampling | 本项目默认方法 |
| CPSS fixed_sampling | 采样策略对照 |

CPSS 配置：

```text
sampling_mode = random_sampling
random_seed = 20260727
selector_mode = calibrated_fixed
calibration_blocks = 3
stream_blocks = true
compress_workers = 1
transfer_workers = 1
max_inflight_blocks = 2
block_mib = 128
```

如果 2 vCPU 环境不稳定，允许自动降级为 `max_inflight_blocks = 1`，但必须记录降级原因。

## 数据流

每个数据集按如下流程执行：

```text
OSS 拉取真实数据
-> 解包/准备代表样本
-> 切分 block 或流式读取 block
-> raw GridFlux 传输，记录 raw_transfer_elapsed_sec
-> gzip 压缩、传输、远端解压、SHA 校验
-> lz4 压缩、传输、远端解压、SHA 校验
-> CPSS calibrated_fixed 标定并固定管线
-> CPSS 正式传输、远端解压、SHA 校验
-> 写入 summary / live_metrics
-> 清理本地和上海远端现场
```

CPSS 的 calibration block 只用于选择固定管线，可写入 `calibration.csv`，不计入正式 KPI。

## 指标口径

主指标为跨域传输逻辑带宽提升：

```text
raw_logical_bandwidth_MBps = original_size_bytes / raw_transfer_elapsed_sec / 1048576
method_logical_bandwidth_MBps = original_size_bytes / method_transfer_elapsed_sec / 1048576
logical_bandwidth_gain_x = method_logical_bandwidth_MBps / raw_logical_bandwidth_MBps
logical_bandwidth_improvement_pct = (logical_bandwidth_gain_x - 1) * 100
```

等价地，在同一 block 上：

```text
logical_bandwidth_gain_x = raw_transfer_elapsed_sec / method_transfer_elapsed_sec
```

辅助指标为端到端收益：

```text
method_e2e_elapsed_sec =
  compress_elapsed_sec + method_transfer_elapsed_sec + remote_decompress_elapsed_sec

e2e_gain_x = raw_transfer_elapsed_sec / method_e2e_elapsed_sec
e2e_improvement_pct = (e2e_gain_x - 1) * 100
```

汇总时至少给出：

1. dataset-level average：每个数据集同权。
2. block-level average：所有 block 同权。
3. byte/time-weighted average：按传输时间或字节加权。

## 实时逻辑带宽图

每个 block 完成后追加 `live_metrics.jsonl`，字段包括：

```text
timestamp
dataset
method
block_index
original_size_bytes
compressed_size_bytes
raw_transfer_elapsed_sec
method_transfer_elapsed_sec
compress_elapsed_sec
remote_decompress_elapsed_sec
logical_bandwidth_gain_x
logical_bandwidth_improvement_pct
rolling_5block_gain_x
cumulative_gain_x
e2e_gain_x
roundtrip_pass
status
```

图中至少展示：

1. 单 block logical bandwidth gain 曲线。
2. rolling 5-block average。
3. cumulative gain 曲线。
4. 目标线 `gain_x = 3.0`，即提升 200%。
5. CPSS、gzip、lz4 三条方法曲线。

输出：

```text
logical_bandwidth_live.html
figures/logical_bandwidth_<dataset>.png
figures/logical_bandwidth_overview.png
```

## 淘汰规则

按数据集和方法结果分级：

| 等级 | 规则 | 动作 |
| --- | --- | --- |
| A 类 | CPSS byte/time-weighted logical improvement >= 200% | 保留为主力数据集 |
| B 类 | 100% <= improvement < 200%，或部分 block 明显超过 200% | 保留观察，考虑变量级筛选 |
| C 类 | improvement < 100%，且端到端无收益 | 删除或仅保留为负例 |

## 预期结论

RCSB、AlphaFold、NCBI 预计为 A 类主力；CESM、Hurricane 预计为 B 类；Miranda 预计为 C 类负例。gzip/lz4 用于说明 CPSS 相比经典通用压缩和高速压缩在逻辑带宽提升上的优势与边界。

## 结果目录

建议新建独立目录：

```text
ExperimentRecords/fixed_dataset_gzip_lz4_baseline_YYYYMMDD_HHMMSS/
```

产物包括：

```text
summary.csv
dataset_summary.csv
method_comparison.csv
calibration.csv
live_metrics.jsonl
logical_bandwidth_live.html
figures/*.png
final_summary_zh.md
cleanup_report.md
completion_audit.json
completion_audit.md
```
