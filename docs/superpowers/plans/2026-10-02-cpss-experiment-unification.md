# CPSS DCC 实验统一与补实验计划

## 目标

在不重复整套 10 数据集网络实验的前提下，把现有实验整理成一套单一、可复现、可审稿的证据链，回答以下五个问题：

1. CPSS 是否在真实端到端 `T_ready` 上优于 Raw 和常用固定压缩器？
2. 收益来自“自适应动作选择”，还是仅来自 CPSS 的封装和传输路径？
3. 为了接近完整信息 system oracle，需要付出多少探测成本？
4. 带宽变化是否会改变 Raw/压缩决策？
5. CPSS 的结论是否只适用于通用字节流压缩器，还是能与至少一个科学数据专用无损方案公平比较？

本计划不把 CPSS 写成“压缩率最优选择器”，而将其主问题固定为：

> 在一次性严格无损数据传输中，如何选择 Raw 或压缩动作，使接收端目录尽快可用。

## 非目标

- 不重新跑已经完整且配置一致的 10 数据集主实验。
- 不把 1 GBps 模型外推写成新的物理链路实测；论文主带宽结果只使用现有 10/100 Mbps 实测，除非另行产生可审计的实测记录。
- 不把当前 24 条 MLcomp-like 记录称作 MLcomp 的完整复现。
- 不把现有消融表中的 `forced-compression-zstd` 写成强制压缩新运行；它和 `CPSS-static-zstd` 当前引用的是同一批固定 Zstd 记录。
- 不在没有严格逐字节恢复和相同计时边界的情况下声称优于科学专用压缩器。

## 当前证据与复用策略

| 记录 | 规模 | 统一后的用途 | 是否重跑 |
|---|---:|---|---|
| `analysis/main_method_metrics.csv` | 306 条，10 数据集 × 6 方法 | 主性能结果：Raw、gzip-6、LZ4、Zstd-3、XZ-6、CPSS | 否；先做配置审计 |
| `analysis/main_dataset_method_summary.csv` | 10×6 汇总 | 逐 workload 的 `T_ready`、字节数和组件时间 | 否 |
| `.../dcc_bandwidth_sensitivity_measured_10_100_same128/method_metrics_10_100mbps.csv` | 54 条，3 数据集 × 2 带宽 × 3 方法 × 3 次 | 10/100 Mbps 的同输入实测敏感度 | 否；与 306 条主实验分组报告 |
| `analysis/ablation_method_metrics.csv` | 27 条，3 数据集 × 3 方法 × 3 次 | CPSS、Raw、固定 Zstd 的小规模配对对照 | 否；禁止把同一 Zstd 记录重复解释为独立强制压缩实验 |
| `analysis/scp_metrics.csv` | 27 条 | 传输工具交叉检查；只报告工具内相对 Raw 的结果 | 否；不与 GridFlux 绝对秒数混合 |
| `.../dcc_mlcomp_like_e2e_20261002/method_metrics.csv` | 24 条，2 workload × 4 方法 × 3 次 | “压缩率导向选择与端到端选择可能不同”的先导材料 | 不直接作主结论；可作为动机/附录 |
| `.../dcc_mlcomp_like_small_20261001/mlcomp_like_small.json` | 8 个前缀，4/2/2 split | MLcomp-style 机制检查 | 不称为 MLcomp 复现；先改成统一候选集后再补正式小实验 |

现有主实验的事实边界：CPSS 共 32 次选择压缩、19 次选择 Raw，压缩动作本轮全部为 Zstd；总 `T_ready` 中位数相对 Raw 降低 9.34%，相对固定 Zstd-3 降低 8.39%。因此当前数据支持的是“Raw-vs-compression 的端到端动作选择”，还不能支持“CPSS 已经稳定选出多种压缩器并优于所有固定 codec”。

## 阶段 0：配置与数据审计（不产生网络流量）

### 0.1 建立唯一的实验配置快照

从每个结果目录读取并保存以下字段，写入新的
`ExperimentRecords/dcc_unified_YYYYMMDD/config_audit.json`：

- 代码文件哈希和远端代码哈希；
- 候选动作集合；
- `probe_candidates`、采样窗口字节数、实际采样个数；
- 带宽、GridFlux 连接数、文件并发、checksum；
- 数据集 manifest、原始字节数、文件数、block 数；
- 重复次数、随机种子、计时字段定义；
- `T_ready`、`T_verified` 和 oracle 字段的来源。

### 0.2 解决“block”和“probe window”的歧义

当前记录同时出现 128 MiB block、4 MiB sample window 和 `sample_count=2`。正式论文不再只写“采样两个 block”，而必须报告：

```text
probe unit = 4 MiB aligned window
probe budget = number of windows and total probed bytes
```

如果远端实现的 `sample_count` 实际代表 block index 而不是 window 数量，先以结果中的 `calibration_sample_indices` 和代码路径验证，再更新方法描述。未完成验证前，不运行新的采样曲线。

### 0.3 审计判据

阶段 0 通过的条件是：同一条 CSV 记录能够追溯到唯一运行目录、配置、输入 manifest 和代码哈希；所有方法的 `T_ready` 计时边界一致；完整信息 oracle 的候选集合和 CPSS 实际候选集合明确记录。若不一致，旧结果分组保留，不强行拼接。

## 阶段 1：现有数据的统一重分析（本地完成）

### 1.1 主性能结果

沿用当前主聚合口径：先对每个数据集和方法取重复运行中位数，再计算：

```text
total-time speedup = sum_d median(T_ready(Raw,d))
                     / sum_d median(T_ready(Method,d))
geomean speedup     = exp(mean_d(log(T_ready(Raw,d)/T_ready(Method,d))))
```

同时输出每个 workload 的：

- `T_ready` 中位数和 IQR；
- transferred bytes；
- probe、encode、transfer、decode/rebuild 分解；
- CPSS 的 selected action；
- 相对 Raw、固定 Zstd-3 的配对差值。

论文主图只使用 `T_ready`，字节数和组件时间放入子图或表格，避免用压缩率替代系统性能。

### 1.2 端到端 oracle 重新核对

从每条 CPSS 记录读取 `oracle_pipeline`、`oracle_e2e_s` 和 `system_regret_vs_oracle`，检查：

1. oracle 是否包含 Raw；
2. oracle 是否和当前运行使用同一候选动作集合；
3. oracle 成本是否包含编码、传输、解码/重建和封装字节；
4. CPSS 选择 Raw 时是否把探测时间计入实际 `T_ready`。

若候选集合不一致，论文将其命名为“同一部署候选池内的 full-data reference”，而不写成覆盖所有固定基线的全局 oracle。

### 1.3 现有消融和 scp 结果的处理

- `CPSS-static-zstd` 只能回答“在相同 CPSS/GridFlux 载体上，自适应策略相对固定 Zstd 的差异”。
- 当前 `forced-compression-zstd` 与固定 Zstd 使用相同记录，删除其独立柱状图；若需要“关闭 Raw fallback”的证据，阶段 3 重新产生真正强制压缩的运行。
- scp 结果的压缩准备、上传和远端提取边界不同于 GridFlux，附录仅报告每个工具内部相对 Raw 的 speedup，并说明不可比较绝对秒数。

阶段 1 的交付物：

```text
analysis/unified_main_summary.csv
analysis/unified_dataset_summary.csv
analysis/unified_oracle_audit.csv
analysis/unified_reuse_report.md
```

## 阶段 2：公平的 MLcomp-style 自适应对照（优先补跑）

### 2.1 比较对象

使用现有 CPSS 部署候选池，建立三个动作选择器：

1. `fixed-zstd`：固定 Zstandard-3；
2. `cr-1nn`：只用采样得到的候选输出大小/逆压缩率作为特征，用 SFS+1NN 或同等 1NN 规则选择 codec；
3. `cpss-cost`：当前 CPSS，使用端到端成本估计并保留 Raw 动作。

优先使用公共候选交集 `zstd, lz4, zlib, delta->zstd`，避免把 XZ 只加入某一选择器。XZ 仍可作为固定高压缩比、慢编码 baseline；若将 XZ 加入选择器池，必须同时加入三个策略和 oracle。

### 2.2 数据与规模

复用已准备的三个 128 MiB 输入：CESM、Miranda、NOAA；再复用 ExaFEL 和 RCSB 的 32 MiB MLcomp-like 输入。若本地/深圳数据路径一致，新增网络流量不超过约 10 GB。

每个 workload、每个策略、每个带宽先做 3 次；先在 100 Mbps 完成，再决定是否在 10 Mbps 补同样矩阵。Raw 和固定 Zstd 直接引用同输入、同配置的已有实测行，新增策略必须使用同 GridFlux/CRC32C/并发设置。

### 2.3 指标

分别报告两类结果：

- **CR objective**：selected payload bytes、CR regret；
- **system objective**：`T_ready`、probe-inclusive regret、相对 Raw/Zstd 的 speedup。

这样可直接说明：MLcomp-style 方法可能在压缩率目标上选到更小 payload，但不一定最早让远端目录可用。该结果是本文与 DCC 2024 MLcomp 的公平连接，而不是声称复现其 CRUSHER/103,488 pipeline 规模。[DCC 2024 MLcomp 原始论文](https://burtchell.dev/cv/pdf/DCC24.pdf)

阶段 2 的通过条件：同一输入、同一候选池、同一计时边界下，`cr-1nn` 和 `cpss-cost` 至少各有一组可审计的选择与端到端记录；否则只保留为方法分析，不放主结果表。

## 阶段 3：采样预算—决策质量实验

### 3.1 设计原则

本实验唯一问题是：

> 为了做出接近 oracle 的系统决策，需要付出多大的探测成本？

不把它写成“采样越多压缩率越高”。

### 3.2 预算与数据

在三个已有代表性 workload 上进行：

- CESM：大型数值数据、适合压缩；
- Miranda：已有 Raw 回退行为；
- NOAA：文本/表格型、作为另一种异质输入。

以 **4 MiB probe window** 为单位，预算设为 `1, 2, 5, 10, 20` 个 window，即 4、8、20、40、80 MiB；每个预算使用 5 个预登记随机种子，并保留固定位置采样作为单独条件。若代码只能稳定支持 `1,2`，先扩展 runner 的预算参数，不改变压缩算法和传输协议。

### 3.3 两级执行以节省时间

**第一级：本地/远端离线 replay。** 使用既有 full-data oracle 和固定动作组件时间，重新计算每个采样预算的预测动作、动作命中率和 regret；这一阶段不产生网络流量。

**第二级：小规模实测。** 对每个 workload 和预算选 1 个固定种子、3 次重复，实测 CPSS 的 `T_ready`；Raw/Zstd 直接复用同一 128 MiB 输入已有的 10/100 Mbps 记录。若一级结果显示某两个预算处于决策交叉点，再对这两个预算增加其余 4 个种子。

### 3.4 必报指标

- probe windows、probe bytes、probe wall time；
- selected action/pipeline；
- full-data oracle action；
- action agreement；
- decision regret；
- probe-inclusive regret；
- probe time / `T_ready`；
- 预测成本与实际成本的误差。

采样曲线以“探测成本—regret”画 Pareto 图；横轴使用 MiB 或秒，不能使用无单位的 sample count。

## 阶段 4：带宽敏感度和 Raw 回退

### 4.1 已有实测结果

直接复用 128 MiB 相同输入上的 10 和 100 Mbps 实测记录，报告 CPSS、Raw、固定 Zstd 的逐 workload 和总时间。不得将 1 GBps 旧模型外推放在主图；若保留，只放附录并显式标记为 model-based extrapolation。

### 4.2 新增小规模带宽/策略检查

在阶段 2 的三 workload 小矩阵中，若 10 Mbps 的 CPSS 仍始终只选 Zstd，记录其真实原因：候选动作的预测成本差异、Raw 传输时间和探测成本，而不是猜测“低带宽必然选择 XZ/LZ4”。若某些候选在低带宽真正胜出，报告动作变化；若仍是 Zstd，结论应写成“在当前候选池和编码开销下，Zstd 的端到端成本最低”，不能泛化为所有网络或所有 codec。

### 4.3 真正的强制压缩消融

在 CESM、Miranda、NOAA 三个已有输入上补一个 `force-zstd` 运行：关闭 Raw bypass，但沿用 CPSS 的 probe、bundle 和 GridFlux path。与 CPSS-adaptive、Raw、fixed-zstd 配对比较，报告：

- CPSS 选择 Raw 的 workload 上，强制压缩相对 Raw 的退化；
- CPSS 选择压缩的 workload 上，强制压缩与 CPSS 的差异；
- `raw_fallback_predicted` 与 `raw_fallback_after_failure` 分开计数。

## 阶段 5：科学专用无损基线（按可用性决定）

代码中已有 `bitshuffle->zstd` 等候选注册项，且 requirements 中包含 Blosc2/FPZIP/ZFP 相关依赖。先做小规模可用性和逐字节 round-trip 检查：

1. 在 CESM、HACC、NYX、SCALE-LETKF 等数值输入上运行 `bitshuffle->zstd`；
2. 若 FPZIP 或其他严格无损浮点实现能够稳定运行，再选一个作为补充；
3. ZFP 只有在明确使用 reversible/严格无损模式时才纳入，否则不进入主无损表。

每个可用基线只需在 2--4 个代表性数值 workload 上做 3 次，复用已有原始输入和 100 Mbps 链路，预计远低于 10 GB 新传输量。记录 codec 是否适用、是否逐字节恢复、压缩/解压时间、`T_ready`。不适用的文本或结构化输入标记为 N/A，不填充为失败。

如果阶段 5 无法稳定完成，论文把主结论限定为“通用严格无损 codec 的系统比较”，并在相关工作中明确没有声称优于全部科学专用压缩器。不能用缺失实验支撑普遍 SOTA 结论。科学浮点压缩器和近年的采样型科学压缩选择器应在相关工作中正式讨论；后者包含有损/误差约束目标，不能直接替代本文严格无损传输实验。[MeLLoC, NeurIPS 2024](https://proceedings.neurips.cc/paper_files/paper/2024/hash/dc2ccde7ee43e5719e08c68e848bd65a-Abstract-Conference.html)；[近年采样型科学压缩选择器](https://www.sciencedirect.com/science/article/abs/pii/S0167739X2600004X)

## 统一统计与图表

### 统计规则

- 主表单位是数据集级中位数；跨数据集同时报告总时间 speedup 和 geometric mean。
- 每个 workload 使用配对比较；报告胜率、绝对秒数差和相对百分比。
- WAN 长尾使用中位数/IQR；不删除最慢的成功记录。
- 失败分为基础设施失败、方法失败、超时、checksum/重建失败；CPSS 的预测性 Raw 回退是成功运行。
- 所有重复运行保留原始 CSV、配置和日志；分析脚本只读取规范化 CSV，不修改原始记录。

### 主文图表

1. **图 1：** 系统架构和 `T_ready` 计时边界。
2. **图 2：** 十个 workload 的 `T_ready` 相对 Raw（CPSS、Zstd、LZ4、gzip、XZ），用水平点/区间或分组条形图；附带 CPSS 的 Raw/Compress 动作标记。
3. **图 3：** 采样成本—oracle regret 的多面板图：预算、probe time、命中率和 probe-inclusive regret。
4. **图 4：** 10/100 Mbps 实测敏感度：总时间和逐 workload 动作/收益；不放旧 1 GBps 模型外推。

MLcomp-style 小实验、scp 交叉验证、科学专用 codec 详细结果放附录或补充表，只有在阶段 2/5 的证据完整后才升级到主文。

## 运行顺序与停止条件

1. 完成阶段 0 配置审计；如果候选集合或计时边界无法统一，停止合并，先建立版本分组。
2. 完成阶段 1 本地重分析；生成统一结果表和图表输入。
3. 优先完成阶段 2 MLcomp-style 对照，因为它直接回应基线落后的质疑。
4. 完成阶段 3 采样成本实验；先 replay，再只对交叉点做网络实测。
5. 完成阶段 4 强制压缩消融和 10/100 Mbps 解释。
6. 有余力再做阶段 5 科学专用基线；无法稳定运行时明确缩小结论范围。

实验停止条件：任何新结果若改变候选池、计时边界、数据 manifest 或 GridFlux 参数，必须新建结果目录并禁止覆盖旧 CSV；分析报告要写明版本差异。

## 论文可采用的结论边界

阶段 1 已支持：CPSS 在当前十个 workload、100 Mbps 实测环境下能够在 Raw 与压缩动作之间做出有益选择，并在总 `T_ready` 上优于 Raw 和固定 Zstd-3；不支持：CPSS 已在多个压缩器之间稳定选优。

阶段 2 若通过，将进一步支持：压缩率导向的 MLcomp-style 选择与端到端成本导向选择会产生不同决策，且后者更适合一次性传输目标。

阶段 3 若通过，将支持：采样收益应以 probe-inclusive regret 衡量，存在一个在探测成本和决策质量之间的折中点。

阶段 4 若通过，将支持：网络条件会改变压缩和 Raw 的相对收益；具体动作由完整成本模型决定，不能由带宽快慢单独推断。

阶段 5 若通过，才可以讨论 CPSS 面对至少一个科学专用严格无损方案的系统级表现；否则保持“通用无损 codec + 自适应传输动作”的准确表述。

## 验收标准

- 方法、代码、CSV、manifest 和论文文字对候选集、采样单位、带宽口径和计时边界完全一致。
- 新增实验总网络传输量有明确上限和记录；所有 reused rows 可追溯到原始文件哈希。
- `cr-1nn`、`cpss-cost` 和 fixed baseline 至少在一组共同候选集上完成可审计比较，或在论文中明确降级为先导分析。
- 采样实验同时报告 probe-only 与 probe-inclusive 指标。
- 所有“优于”“接近 oracle”“适用于科学专用压缩器”的句子都能追溯到相应实验表，不用缺失基线填补结论。

## 2026-10-04 执行记录

- 已核验本地实验包：174 个清单项无哈希不匹配；深圳新增实验 207 条记录唯一且均通过状态/往返检查。完整重分析见 ExperimentRecords/dcc_paper_dataset_20261004/analysis/analysis_report.md。
- 10-workload 主实验继续作为主结果；codec-pool、采样、Raw 绕过与带宽假设实验分别报告，不跨组混合中位数。
- 新带宽实验的 10/100 Mbps 是 CPSS 成本估计器输入，GridFlux 路径未做速率整形；稿件与图中均按“带宽假设敏感性”表述。
- 采样预算报告实际采样窗口、探测时间及配对实测 Raw/Zstandard 参考；该参考不称为全候选 oracle。
- 英文/中文稿已更新，新增统一黑白实验图；XeLaTeX/BibTeX 编译和 PDF 页面检查通过。英文稿 10 页，中文审阅稿 9 页。
- 尚未完成同一 GridFlux 计时边界下的 SFS+1NN-vs-CPSS 端到端选择器对比、真正关闭 Raw bypass 的强制压缩消融及科学专用严格无损 codec 实验。当前稿件不声称这些对比已经完成，也不外推优于科学专用 codec。
