# CPSS 采样策略与去重流水线实验报告

- 报告日期：2026-07-29
- 实验目录：`ExperimentRecords/cpss_sampling_dedup_matrix_20260728_175454/`
- 默认采样策略：`random_sampling`
- 默认随机种子：`20260727`
- calibration block 数：`5`
- block size：`8 MiB`
- 正式传输通道：GridFlux，上海端 `47.116.174.181:25252`
- 结果状态：21 个方法全部 `completed`，所有重建文件 `roundtrip_ok=True`

## 1. 本次默认策略变更

本项目的 CPSS 采样默认模式已设置为 `random_sampling`。含义是：

1. 正式传输前按固定随机种子从任务 block 集合中抽取 5 个 calibration block。
2. calibration 阶段只对这些采样 block 跑 exhaustive，选择总压缩后字节数最小的全局固定管线。
3. 正式传输阶段所有 block 直接走该固定管线，不再逐块遍历全部候选管线。

本次实验的默认随机种子为 `20260727`。`fixed_sampling` 和 `native_exhaustive` 继续保留为对照：前者用于观察位置采样偏差，后者作为最高压缩比 oracle，不作为生产快路径。

代码变更位置：

- `Compressor/cpss_sampling_matrix.py`：新增 `DEFAULT_SAMPLING_MODE = "random_sampling"` 和默认 calibration 选择函数。
- `Script/run_cpss_sampling_dedup_matrix.py`：新增 `--sampling-mode`，默认 `random_sampling`；结果摘要记录默认采样策略。
- `experiment_design_fixed_dataset_baseline.md`：更新实验设计中的默认 CPSS 采样策略。

## 2. 实验数据与方法

真实数据来自 OSS 解压后的科学数据，不使用外层 `.tar.zst` 做压缩对比：

| 数据集 | 类型 | 原始大小 | block 数 | 说明 |
|---|---:|---:|---:|---|
| `alphafold_mmcif` | 真实 mmCIF | 1024 MiB | 128 | AlphaFold mmCIF 样本 |
| `rcsb_mmcif` | 真实 mmCIF | 1024 MiB | 128 | RCSB mmCIF 样本 |
| `dedup_positive_constructed` | 构造去重正例 | 48 MiB | 4 | 包含完整重复文件和跨文件重复 block |

对比方法：

| 方法 | 说明 |
|---|---|
| `raw_direct` | 裸传，不压缩、不去重 |
| `cpss_native_exhaustive` | 每个 block 遍历全部可部署管线，选择最高压缩比 |
| `cpss_fixed_sampling` | 固定位置采样一次，选全局固定管线 |
| `cpss_random_sampling` | 随机采样一次，选全局固定管线，现为默认模式 |
| `no_dedup` | 不做文件级和块级去重 |
| `dedup` | 文件级 SHA256 去重 + 固定块 SHA256 去重，只传 unique block + manifest |

## 3. 核心结果

以下表格使用 `dedup` 口径；真实数据没有自然重复，因此这里的 `dedup` 主要表示走完整去重流水线但没有实际命中。

| 数据集 | 方法 | 选中管线 | 传输字节 | 字节减少 | 压缩比 | 压缩时间 | GridFlux 时间 | 端到端，不含 calibration | 端到端，含 calibration |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| AlphaFold | raw | - | 1024.00 MiB | 0.00% | - | 0.00s | 821.79s | 824.04s | 824.04s |
| AlphaFold | native exhaustive | per-block oracle | 116.31 MiB | 88.64% | 8.812x | 5419.98s | 89.92s | 5559.48s | 5559.48s |
| AlphaFold | fixed sampling | `delta_length_byte_array->xz_lzma2` | 116.63 MiB | 88.61% | 8.788x | 541.55s | 90.42s | 686.88s | 901.74s |
| AlphaFold | random sampling | `xz_lzma2` | 116.48 MiB | 88.63% | 8.800x | 539.02s | 90.15s | 675.64s | 890.01s |
| RCSB | raw | - | 1024.00 MiB | 0.00% | - | 0.00s | 794.13s | 796.69s | 796.69s |
| RCSB | native exhaustive | per-block oracle | 118.43 MiB | 88.43% | 8.654x | 5141.93s | 91.83s | 5285.16s | 5285.16s |
| RCSB | fixed sampling | `delta_byte_array->xz_lzma2` | 121.87 MiB | 88.10% | 8.410x | 468.79s | 94.16s | 618.69s | 824.14s |
| RCSB | random sampling | `xz_lzma2` | 120.42 MiB | 88.24% | 8.511x | 508.15s | 93.43s | 648.81s | 850.59s |
| 去重正例 | raw | - | 48.00 MiB | 0.00% | - | 0.00s | 36.53s | 38.38s | 38.38s |
| 去重正例 | native exhaustive | per-block oracle | 2.40 MiB | 95.00% | 20.054x | 120.90s | 1.31s | 122.96s | 122.96s |
| 去重正例 | fixed sampling | `xz_lzma2` | 2.40 MiB | 94.99% | 20.020x | 12.27s | 1.40s | 14.34s | 214.26s |
| 去重正例 | random sampling | `xz_lzma2` | 2.40 MiB | 94.99% | 20.020x | 12.24s | 1.28s | 14.17s | 213.65s |

结论：

- `native_exhaustive` 压缩后字节最少，但压缩时间过长。两个 1GiB 真实数据集上端到端时间约 5285-5559s，比 raw 慢约 6.6-6.7 倍，因此只能作为 oracle。
- `random_sampling` 在两个真实数据集上保留了接近 oracle 的压缩比：AlphaFold 相对 oracle 损失 0.141%，RCSB 损失 1.652%。
- `random_sampling` 相对 oracle 节省约 90% 压缩时间：AlphaFold 节省 90.05%，RCSB 节省 90.12%。
- 不含 calibration 的稳态传输中，`random_sampling + dedup` 相对 raw：AlphaFold 端到端快 21.96%，RCSB 快 22.79%。
- 含 calibration 的一次性端到端口径中，`random_sampling + dedup` 仍比 raw 慢：AlphaFold 慢约 7.41%，RCSB 慢约 6.34%。这说明 calibration 必须在大任务、多文件批次或重复同类数据传输中摊销。

## 4. 采样策略影响

`fixed_sampling` 和 `random_sampling` 相对 `native_exhaustive` 的压缩比损失与压缩时间节省如下，表内为 `dedup` 口径：

| 数据集 | 策略 | 压缩比损失 | 压缩时间节省 |
|---|---|---:|---:|
| AlphaFold | fixed sampling | 0.271% | 90.01% |
| AlphaFold | random sampling | 0.141% | 90.05% |
| RCSB | fixed sampling | 2.827% | 90.88% |
| RCSB | random sampling | 1.652% | 90.12% |
| 去重正例 | fixed sampling | 0.167% | 89.85% |
| 去重正例 | random sampling | 0.168% | 89.88% |

本次结果支持把 `random_sampling` 设为默认：

- 两个真实数据集上，随机采样的压缩比损失都小于固定位置采样。
- AlphaFold 上随机采样端到端时间也更短。
- RCSB 上固定采样稳态端到端略快，但压缩比损失更大；随机采样的综合稳定性更适合作为默认策略。
- 固定位置采样容易受文件结构位置偏差影响，尤其在头部、中部、尾部结构差异较大的科学文本数据上风险更高。

需要注意的是，随机采样并不是完全无波动。calibration 结果显示不同 seed 会选择不同管线：

| 数据集 | fixed winner | random 20260727 | random 20260728 | random 20260729 |
|---|---|---|---|---|
| AlphaFold | `delta_length_byte_array->xz_lzma2` | `xz_lzma2` | `delta_length_byte_array->xz_lzma2` | `xz_lzma2` |
| RCSB | `delta_byte_array->xz_lzma2` | `xz_lzma2` | `delta_byte_array->xz_lzma2` | `xz_lzma2` |
| 去重正例 | `xz_lzma2` | `xz_lzma2` | `delta_length_byte_array->xz_lzma2` | `xz_lzma2` |

因此生产建议是：默认使用 `random_sampling(seed=20260727, sample_count=5)`，但在批量任务中保留多 seed 或增加采样 block 数作为稳定性增强项。

## 5. 去重流水线收益

真实 AlphaFold 和 RCSB 数据没有文件级或块级重复：

| 数据集 | 文件级命中 | 块级重复 | unique raw bytes |
|---|---:|---:|---:|
| AlphaFold | 0 | 0 | 1024 MiB |
| RCSB | 0 | 0 | 1024 MiB |

所以真实数据上的去重收益接近 0，`dedup` 和 `no_dedup` 的传输字节差异只有约 1.6 KiB，属于 manifest/bundle 结构级微小差异，不应解释为真实去重收益。

构造去重正例中命中明确：

| 指标 | 数值 |
|---|---:|
| 文件级重复文件 | 1 |
| 文件级节省 | 16 MiB |
| 跨文件重复 block | 1 |
| 块级节省 | 8 MiB |
| 原始数据 | 48 MiB |
| unique raw | 24 MiB |

在构造正例上，`random_sampling` 下 dedup 相对 no_dedup：

- 传输字节从 4.813 MiB 降到 2.403 MiB，减少 50.07%。
- 不含 calibration 的端到端时间从 28.40s 降到 14.17s，约 2.00x。
- 所有文件 SHA256 重建校验通过。

这说明去重流水线功能正确，且在存在完整重复文件或重复 block 的场景下收益显著；真实 mmCIF 本轮样本重复率低，所以没有体现去重收益。

## 6. 流水线相对裸传的收益

以默认 `random_sampling + dedup` 为准：

| 数据集 | raw 端到端 | 默认流水线，不含 calibration | 稳态端到端收益 | 默认流水线，含 calibration | 一次性端到端收益 |
|---|---:|---:|---:|---:|---:|
| AlphaFold | 824.04s | 675.64s | 1.220x | 890.01s | 0.926x |
| RCSB | 796.69s | 648.81s | 1.228x | 850.59s | 0.937x |
| 去重正例 | 38.38s | 14.17s | 2.708x | 213.65s | 0.180x |

核心判断：

- 如果 calibration 成本能被摊销，默认流水线在真实 mmCIF 数据上已经比裸传快约 22%。
- 如果每次小任务都重新 calibration，一次性口径会被 calibration 拖慢。
- 去重在正例数据上能把端到端时间再压低约一半，但这依赖数据重复率。
- 下一阶段真正扩大收益的关键不是再跑 exhaustive，而是并行压缩、压缩与传输流水化、以及 calibration 复用。

## 7. 异常与风险分析

### 7.1 raw 出现 final-status timeout

AlphaFold 和 RCSB 的 raw GridFlux 传输摘要中存在 `recovered_after_data_final_status_timeout=1`。这不是数据损坏：

- GridFlux 数据传输完成。
- 远端文件级 SHA256 校验通过。
- `roundtrip_ok=True`。

该现象应归类为 GridFlux 控制面最终状态等待超时或恢复逻辑触发，建议后续单独优化客户端 final-status 等待和日志判定。

### 7.2 exhaustive 过慢

`native_exhaustive` 的每个正式 block 都遍历候选管线，导致 1GiB 数据压缩时间超过 5100s。它的价值是提供最高压缩比上限，不适合作为正式传输路径。

### 7.3 真实数据去重率为 0

AlphaFold 和 RCSB 本轮 8MiB 固定块粒度下没有文件级重复和块级重复。去重流水线没有负收益，但也没有实际节省内容字节。不能用这两个真实样本否定去重方案，只能说明这类样本自然重复率低。

### 7.4 calibration 成本需要摊销

默认随机采样每个数据集的 calibration 约 200s。对 1GiB 单次任务，这个成本足以让“含 calibration”端到端慢于 raw；对更大批次、同类数据多次传输、或长期服务中的可复用 calibration，这个成本才会被摊薄。

### 7.5 随机 seed 有选择波动

不同随机 seed 有时会选择 `xz_lzma2`，有时选择 delta + xz 管线。当前默认 seed `20260727` 在两个真实数据集上压缩比损失更低，因此本轮可作为默认；但后续应保留 seed 稳定性分析，避免少量样本误选。

## 8. 验收状态

| 验收项 | 状态 |
|---|---|
| OSS 数据 stat、下载、zstd 校验、解压 | 完成 |
| raw、native exhaustive、fixed sampling、random sampling 矩阵 | 完成 |
| no_dedup 与 dedup 对比 | 完成 |
| 构造去重正例 | 完成 |
| GridFlux 正式传输 | 完成 |
| 上海端重建 SHA256 校验 | 全部通过 |
| cleanup audit | 通过，本地和远端临时目录均已删除 |
| 默认采样策略设置为 random | 完成 |

结构化结果文件：

- `method_metrics.csv`
- `block_metrics.csv`
- `calibration.csv`
- `dedup_comparison.csv`
- `cpss_mode_comparison.csv`
- `summary.json`
- `summary_zh.md`
- `cleanup_audit.json`

## 9. 最终结论

1. 本项目默认采样策略应采用 `random_sampling(seed=20260727, sample_count=5)`。它在本轮真实数据上比固定位置采样压缩比更稳，相对 oracle 只损失 0.141%-1.652% 压缩比，同时节省约 90% 压缩时间。
2. `native_exhaustive` 不是部署方法。它能给最高压缩比，但端到端时间显著慢于裸传，只适合作为 oracle 或离线标定工具。
3. 默认随机采样流水线在真实 1GiB mmCIF 数据上，稳态端到端比 raw 快约 22%；但如果每次任务都重新 calibration，一次性口径会慢于 raw。
4. 去重流水线在真实 mmCIF 样本上没有实际内容收益，因为重复率为 0；在构造正例上能减少约 50% 传输字节，并把稳态端到端提升到约 2.00x，功能正确。
5. 下一阶段优先级应放在并行压缩、压缩传输重叠、calibration 复用和更大数据集验证上。这些方向比继续使用 per-block exhaustive 更能改善生产端到端效果。
