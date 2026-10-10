# DCC 论文正式实验协议

版本：v1.0-frozen  
状态：实验设计已冻结，后续只允许修复实现与补充执行记录，不得擅自改变实验口径  
更新日期：2026-09-14  
项目：CPSS / Science Compressor  论文定位：系统型科学数据跨域传输压缩系统

## 0. 文档用途和优先级

本文档是当前 DCC 投稿实验的唯一正式实验协议（single source of truth）。它记录已经确认的实验范围、指标、基线、执行规模、统计方式、排除项以及每项选择背后的理由。

项目目录中的旧实验设计、旧实验报告和旧脚本说明仅用于追溯历史，不得覆盖本文档。例如：

- `experiment_design_fixed_dataset_baseline.md` 是早期 gzip/LZ4 设计；
- `CPSS_SAMPLING_DEDUP_EXPERIMENT_REPORT_20260729.md` 是包含去重和旧口径的历史结果；
- 旧脚本中的默认 block 大小、连接数和策略集合不自动代表正式实验参数。

如果实现代码与本文档不一致，应修改代码或实验配置；不能为了迁就旧代码而改变已冻结的论文实验定义。

---

## 1. 论文主问题和定位

### 1.1 系统定位

CPSS 被定位为一个面向科学/HPC 数据跨域传输的系统，而不是单独提出一个新的压缩编码器。系统的核心功能是：

1. 对输入数据进行低成本探测；
2. 估计多个候选动作的端到端代价；
3. 自适应选择压缩动作或 Raw；
4. 通过 GridFlux 传输；
5. 在接收端完成解压、重建和可用性确认。

### 1.2 主要科学问题

主问题不是“CPSS 的压缩比是否在所有数据上最高”，而是：

> 在真实跨域传输中，CPSS 能否通过低成本探测，在不同科学数据类型和不同网络带宽下选择合适的系统动作，从而降低端到端完成时间；当压缩不划算时，系统能否安全地退化为 Raw 传输？

这个表述比单纯追求压缩比更符合系统论文标准，因为传输系统真正付出的代价包括计算、网络和接收端重建，而不仅是压缩后字节数。

### 1.3 论文中的边界表述

论文不声称“压缩对所有数据类型都有正收益”。正式表述为：

> Compression is not assumed to be universally beneficial. CPSS treats raw transmission as a legal candidate action and bypasses compression when the predicted end-to-end cost does not justify probing and compression.

中文含义是：压缩收益依赖数据特征、压缩器和链路带宽；系统的目标不是强行压缩，而是避免在压缩不划算时产生不必要的退化。

---

## 2. 已冻结的数据集范围

### 2.1 主论文数据集

主论文固定使用 `E:\Dataset\DCC_upload\full-v1` 中的 10 个数据集：

| 数据集 | 类型 | 主作用 |
|---|---|---|
| `cesm_atm` | scientific/HPC numeric | 科学数值数据代表 |
| `hurricane_isabel` | scientific/HPC numeric | 大规模科学场数据 |
| `miranda` | scientific/HPC numeric | 采样和 Raw fallback 代表 |
| `nyx` | scientific/HPC numeric | 科学数值多样性 |
| `hacc` | scientific/HPC numeric | 大型科学数据 |
| `scale_letkf` | scientific/HPC numeric | 大型科学数据 |
| `exafel` | scientific/HPC numeric | 较小科学数据 |
| `rcsb_mmcif` | byte/text-like scientific metadata | 结构化文本/元数据 |
| `ncbi_grch38_fasta` | byte/text-like bioinformatics | 长文本/生物信息数据 |
| `noaa_gsod_csv` | byte/text-like tabular text | 对 CPSS 不一定友好的数据 |

这些数据合计约 26.10 GB（十进制，约 24.31 GiB），是主论文的科学/HPC 主体。

### 2.2 为什么加入不友好数据

如果只使用容易压缩的科学数值数据，论文可能被质疑为选择性展示。加入 `noaa_gsod_csv`、`rcsb_mmcif` 和 `ncbi_grch38_fasta` 的目的不是宣称 CPSS 在文本上一定领先，而是验证系统的安全边界：

- 数据可能没有足够结构可供候选管线利用；
- 压缩计算时间可能超过网络节省时间；
- CPSS 应当选择 Raw，而不是强行压缩；
- 最差情况下，系统应接近 Raw 基线，而不是出现不可接受的系统退化。

### 2.3 附录数据

RedPajama、The Stack 等数据不进入主论文核心结论，只可作为附录或补充材料。原因是主论文需要保持科学/HPC 数据主线，避免数据领域过度扩散。

### 2.4 数据完整性

正式执行前固定保存：

- 数据集清单；
- 原始文件相对路径；
- 文件大小；
- 文件 SHA-256；
- 数据集版本和来源元数据。

任何实验结果必须能通过该清单确认接收端文件树与原始数据一致。

---

## 3. 传输任务和文件组织

### 3.1 主实验任务粒度

主实验以一个数据集目录树作为一个完整传输任务，而不是以单个 block 或单个文件作为论文主样本。

原因：

- 系统论文关心的是一次真实数据集传输的端到端体验；
- 单 block 结果容易放大局部压缩比，掩盖 manifest、文件数和重建成本；
- 数据集级任务更符合跨域科学数据传输场景。

文件级和 block 级数据仍需记录，用于解释性能和附录分析。

### 3.2 原始目录结构

主实验不预先将原始目录打成 tar 包。Raw 保持原始目录树；压缩方法传输压缩 bundle、manifest 和必要的路径信息；接收端恢复原始目录树。

原因：

- tar 预打包会把打包时间和 I/O 影响混入或隐藏；
- 预打包可能改变压缩器看到的数据边界；
- 原始目录树更接近真实 GridFlux 文件传输任务；
- manifest 和封装开销必须被真实计入传输字节数。

### 3.3 去重边界

主论文暂不讨论文件级或 block 级重删收益。主实验固定 `no-dedup`。

原因：

- 重删会引入第二个独立变量；
- 重删收益与压缩收益难以在主结果中分离；
- 当前论文的核心问题是“采样驱动的端到端动作选择”，不是重复数据消除。

去重代码和旧矩阵可以保留用于未来工作或附录，但不得混入主实验的 CPSS 收益。

---

## 4. 方法矩阵

### 4.1 主方法

全部 10 个主数据集都运行：

```text
Raw
gzip-6
LZ4-default
Zstd-3
XZ-6
CPSS-adaptive
```

所有方法必须满足：

- 严格无损；
- 单线程 codec；
- 相同输入数据和目录结构；
- 相同 GridFlux 参数；
- 相同接收端恢复和完整性要求。

### 4.2 数值压缩器

对数据类型适用的科学数值数据，增加：

```text
Blosc2-bitshuffle + Zstd
FPZIP-lossless
ZFP-reversible
```

这些方法只在其格式和数据类型适用时运行。对文本或通用 byte stream 强行使用数值压缩器是不公平的，因此不适用时标记为 `ineligible`，而不是标记为性能失败。

### 4.3 参数原则

主实验固定建议参数：

| 方法 | 参数 |
|---|---|
| gzip | level 6 |
| LZ4 | default/fast |
| Zstd | level 3 |
| XZ | preset 6 |
| CPSS | 固定部署配置，单线程 |

选择中等或默认参数的原因：

- 避免用极端压缩级别制造不现实的 baseline；
- 兼顾压缩时间与压缩率；
- 参数清晰、可复现；
- 与系统端到端目标一致。

### 4.4 CPSS 候选动作集合

CPSS 的搜索空间在实验开始前固定为：

```text
raw
zstd
lz4
zlib
xz_lzma2
brotli
bzip2
shuffle -> zstd
bitshuffle -> zstd
delta -> zstd
byte_stream_split -> zstd
higher_order_delta -> xz_lzma2
```

重要区分：候选集合固定，不等于最终动作固定。CPSS 每次任务根据采样结果自适应选择动作。

每个候选动作必须显式记录：

- `eligible`：当前数据类型是否适用；
- `available`：运行环境是否安装；
- `failed`：运行是否报错；
- `selected`：是否被系统选中。

不能把不可用或失败的动作静默删除，否则 oracle 和 CPSS 的比较会失去可解释性。

---

## 5. CPSS 自适应决策

### 5.1 系统动作

CPSS 的最终动作只能是：

```text
raw
或
某个可用的压缩候选动作
```

因此 Raw 是系统决策空间的一部分，而不是仅仅一个事后 baseline。

### 5.2 采样预算

正式采样预算固定为：

```text
1, 2, 5, 10, 20 个完整 block
```

采样方式：

- 固定位置采样：运行 1 次；
- 随机采样：使用预先登记的 5 个随机种子；
- 默认随机采样种子必须写入结果文件。

### 5.3 采样的实验问题

采样实验不作为“CPSS 压缩比实验”，而是回答：

> 为了做出接近 oracle 的系统决策，需要付出多大的探测成本？

因此要同时报告：

- 采样时间；
- 采样后选择的动作；
- 完整数据 oracle 动作；
- decision regret；
- probe-inclusive regret；
- oracle 动作命中率；
- 探测成本占总完成时间的比例。

### 5.4 采样决策规则

对每个候选动作，根据样本估计：

```text
estimated cost = estimated compression time
               + estimated transfer time
               + estimated decompression/rebuild time
```

样本估计按完整 block 的采样结果外推到整个数据集。Raw 的估计成本为：

```text
raw bytes / configured bandwidth
```

最终选择最小预测端到端代价的动作；如果压缩预测不优于 Raw，则选择 `raw`。

---

## 6. Oracle 定义

### 6.1 Full-data global system oracle

oracle 使用完整数据统计信息，在固定候选集合中选择完整数据集端到端估计代价最小的动作。

oracle 必须包含 `raw` 候选。对压缩动作，代价包括：

```text
full-data compression time
+ compressed transfer bytes / bandwidth
+ receiver decompression/rebuild time
```

Raw 的代价包括：

```text
raw bytes / bandwidth
```

oracle 只用于评价 CPSS 的决策质量，不能把完整数据结果泄漏给采样决策过程。

### 6.2 为什么 oracle 不是“压缩比 oracle”

只按压缩后大小选择动作会忽略：

- 压缩计算开销；
- 接收端解压开销；
- 网络带宽；
- manifest 和封装字节；
- Raw 在高带宽或不友好数据上的优势。

因此本论文使用系统 oracle，而不是单纯的压缩比 oracle。

---

## 7. 端到端时间定义

### 7.1 主指标：`T_ready`

正式主指标定义为：

```text
T_ready = decision/probe
        + compression or raw preparation
        + GridFlux transfer
        + remote decompression/reconstruction
```

当接收端输出目录已经恢复并可供应用使用时，`T_ready` 结束。

GridFlux 内部 CRC32C 校验属于传输完成过程，因此包含在传输时间内。

### 7.2 辅助指标：`T_verified`

```text
T_verified = T_ready + independent SHA-256 verification
```

SHA-256 用于审计和确认端到端数据正确性，但不作为主性能指标，以免把完整性审计成本与传输完成时间混为一谈。

### 7.3 采样相关指标

```text
decision regret =
(T_ready(selected action) - T_ready(oracle)) / T_ready(oracle)
```

```text
probe-inclusive regret =
(T_probe + T_ready(selected action) - T_ready(oracle))
/ T_ready(oracle)
```

`probe-inclusive regret` 是正式名称，不再使用旧名称 `cold regret`。

### 7.4 Raw fallback 分类

必须区分：

- `raw_fallback_predicted`：采样后预测压缩不划算，主动选择 Raw；
- `raw_fallback_after_failure`：压缩运行失败后回退 Raw。

前者是系统正常决策，后者是容错行为，不能合并统计。

---

## 8. 网络和 GridFlux 参数

### 8.1 主链路

真实主实验链路固定为 100 Mbps。

原因：这是当前实际可用的网络条件，主论文应以真实系统环境为中心，而不是以无法稳定提供的高带宽环境为中心。

### 8.2 带宽敏感性

10 Mbps 和 1 Gbps 不进行真实高成本网络重复实验，而使用事后模型：

```text
T_model(B) = T_local + 8 * transferred_bytes / B
```

其中 `T_local` 包括真实测得的决策、压缩和解压/重建时间，`B` 分别取 10 Mbps、100 Mbps 和 1 Gbps。

100 Mbps 同时有真实实测结果和模型结果；模型仅用于检查趋势和外推，不替代真实主结果。

### 8.3 GridFlux 固定参数

所有方法统一：

```text
connections = 2
file-parallelism = 2
checksum = crc32c
```

原因：不同方法使用不同传输并发会把网络调度差异误认为压缩收益。旧脚本中存在 4 连接和 2 连接的历史差异，正式实验必须统一为本协议参数。

### 8.4 独立传输工具交叉验证

附录使用 `scp`（OpenSSH 默认 SFTP backend，`Compression=no`）做小规模交叉验证：

- 数据集：`cesm_atm`、`miranda`、`noaa_gsod_csv`；
- 方法：Raw、Zstd、CPSS；
- 工具：GridFlux、scp；
- 每种组合 3 次。

总计 54 个运行样本。比较重点是每个传输工具内部相对 Raw 的归一化 speedup，而不是把 GridFlux 的绝对时间和 scp 的绝对时间直接混为一个结论。

目的：排除“CPSS 收益只是 GridFlux 特定实现行为”的质疑。

---

## 9. 主实验执行规模

### 9.1 普通主实验

全部 10 个数据集运行：

- Raw；
- gzip；
- LZ4；
- Zstd；
- XZ；
- CPSS-adaptive。

每种组合正式运行 3 次。

### 9.2 高重复数据集

以下 3 个数据集增加到 10 次正式重复：

```text
cesm_atm
miranda
noaa_gsod_csv
```

原因：它们分别代表大型科学数据、采样决策代表和不一定适合压缩的数据，能够覆盖系统结论中最重要的三类情况。

### 9.3 消融实验

正式保留四组：

1. `CPSS-adaptive`：主系统，采样后自适应选择；
2. `CPSS-oracle`：完整信息系统 oracle；
3. `CPSS-static`：固定使用一种动作，但使用 CPSS 的分块、manifest 和传输协议；
4. forced-compression stress test：关闭 Raw fallback，强制压缩，只在 3 个代表性数据集上运行。

`CPSS-static` 的作用是区分收益来自自适应决策，还是仅来自 CPSS 封装和分块。forced-compression 实验的作用是展示关闭 Raw fallback 后的不利数据退化，不作为 CPSS 主结果。

### 9.4 数值压缩器和线程扩展

数值专用压缩器以及 1/4/8 线程扩展放在附录或扩展实验，不改变主实验的单线程主结论。

---

## 10. 统计规则

### 10.1 原始记录

每个“数据集 × 方法 × 重复”保留一行完整记录，至少包括：

- 数据集和方法；
- repeat 编号；
- 原始字节数；
- 实际传输字节数；
- selected action；
- selected pipeline；
- probe、compress、transfer、decompress/rebuild 时间；
- `T_ready`；
- `T_verified`；
- fallback 类型；
- checksum 状态；
- status 和 error。

### 10.2 数据集内汇总

每个数据集和方法使用正式重复运行的中位数作为主汇总值。原因是 WAN 传输和远端 I/O 可能产生长尾，中位数能避免单次网络异常支配结论。

同时保留平均值、标准差、最小值和最大值，便于审计。

### 10.3 跨数据集汇总

不使用算术平均 speedup 作为主跨数据集结论。正式报告：

#### 总时间加速比

```text
Total-time speedup =
Σ median(T_ready(Raw, d))
/ Σ median(T_ready(Method, d))
```

它回答一次性完成整个数据集合时节省多少时间。

#### 几何平均 speedup

```text
speedup_d = T_ready(Raw, d) / T_ready(Method, d)
```

```text
Geomean speedup = exp(mean(log(speedup_d)))
```

它回答方法在不同数据集上的典型相对收益，避免大数据集完全支配结果。

同时给出逐数据集 speedup、胜率和 Raw fallback 比例。

### 10.4 失败样本

状态必须区分：

```text
success
infrastructure_failure
method_failure
timeout
checksum_failure
```

规则：

- CPSS 按设计选择 Raw：计为成功的 `raw_fallback_predicted`；
- 压缩过程失败后成功回退 Raw：记录为 `raw_fallback_after_failure`；
- 基础压缩器失败且没有回退：记为方法失败，不能伪造 speedup；
- checksum 或重建失败：记为失败；
- 允许重跑的基础设施失败必须保留原始失败记录和重跑关系；
- 不得手工删除最慢样本。

几何平均只在比较方法均有效完成的数据集交集上计算；有效覆盖率和失败率必须单独报告。

---

## 11. Pilot 和正式执行顺序

### 11.1 Pilot

正式实验前先运行：

```text
miranda
noaa_gsod_csv
```

Pilot 不计入论文统计，只验证：

- Raw、Zstd、CPSS 是否成功；
- 100 Mbps GridFlux 链路和 CRC32C 是否正常；
- 目录树是否正确恢复；
- 独立 SHA-256 是否通过；
- Raw fallback 是否能触发；
- `T_ready` 和 `T_verified` 是否能正确记录；
- `connections=2`、`file-parallelism=2` 是否真正进入命令；
- 结果文件是否包含完整字段。

### 11.2 正式执行顺序

正式方法执行顺序固定为：

```text
Raw

gzip
LZ4
Zstd
XZ
CPSS-cold
CPSS-warm
```

这里的 cold/warm 指 CPSS 是否包含本次采样探测成本的统计口径，而不是操作系统页缓存实验：

- CPSS-cold：包含 probe/calibration 成本；
- CPSS-warm：不重复计入已可复用的 calibration 成本。

主论文使用包含一次性探测成本的 `probe-inclusive` 口径；warm 结果用于说明长期重复传输时的摊销效果。

### 11.3 Pilot 后锁定

Pilot 通过后：

- 锁定代码 commit；
- 锁定配置文件；
- 锁定候选集合；
- 锁定数据清单；
- 锁定随机种子；
- 再开始正式结果采集。

如果 pilot 发现实现缺陷，应修复代码并重新做 pilot；不得直接把 pilot 失败或修改前后混合的数据作为正式结果。

---

## 12. 主文和附录结果组织

### 12.1 主文

主文建议包含：

1. 系统流程图：sampling → action prediction → adaptive compression/raw → GridFlux → remote reconstruction；
2. 10 个数据集的 Raw、最佳固定压缩器和 CPSS 主表；
3. 100 Mbps 下的 `T_ready`、总时间加速比和几何平均 speedup；
4. 端到端时间分解：probe、compression、transfer、decompression/reconstruction；
5. 10/100/1000 Mbps 带宽敏感性曲线；
6. 采样预算与 decision regret、probe-inclusive regret；
7. Raw fallback 和不友好数据分析。

### 12.2 附录

附录包含：

- 完整 baseline 结果；
- Blosc2、FPZIP、ZFP；
- GridFlux 与 scp 交叉验证；
- 1/4/8 线程扩展；
- `T_verified` 与 SHA-256 校验开销；
- forced-compression stress test；
- 完整 per-run 原始记录；
- 所有失败样本和异常说明。

---

## 13. 当前明确排除的内容

以下内容不进入主论文主结论：

- 重删收益；
- 模型训练效果；
- 只展示压缩比、不报告端到端时间；
- 只在容易压缩的数据上报告平均值；
- 真实 10 Mbps/1 Gbps 大规模重复网络实验；
- CPU 绑核、页缓存清理、冷暖缓存专门实验；
- 用旧脚本默认参数替代冻结协议；
- 将不可用候选动作静默删除；
- 手工删除慢样本或失败样本。

CPU、磁盘和缓存只做基本可复现性控制：相同主机、相同软件、单线程、无并发 benchmark、独立输出目录，并记录版本信息。它们不是论文贡献，不增加独立敏感性实验。

---

## 14. 实现一致性检查清单

正式运行前必须逐项打勾：

- [ ] 主 runner 支持真实 GridFlux，而不是只支持本地 copy；
- [ ] 主实验 block size 固定为 128 MiB；
- [ ] 主实验 dedup 固定为 off；
- [ ] 所有方法使用 GridFlux `connections=2`、`file-parallelism=2`；
- [ ] Raw 被加入 full-data oracle；
- [ ] CPSS 候选集合固定并记录 eligibility/availability/failure；
- [ ] CPSS-adaptive 选择来自采样结果，而不是预先固定 pipeline；
- [ ] 采样预算支持 1/2/5/10/20 block；
- [ ] 随机采样使用 5 个预登记 seed；
- [ ] `T_ready` 和 `T_verified` 分开记录；
- [ ] `probe-inclusive regret` 使用正式公式；
- [ ] predicted Raw fallback 与 failure fallback 分开；
- [ ] 主结果生成总时间加速比和几何平均 speedup；
- [ ] 失败覆盖率和失败原因单独汇总；
- [ ] pilot 结果不混入正式统计；
- [ ] 100 Mbps 为真实主链路；
- [ ] 10 Mbps/1 Gbps 为事后带宽模型；
- [ ] 运行前保存数据集和环境 manifest；
- [ ] 结果目录包含 command log、原始 CSV、校验记录和 cleanup audit。

---

## 15. 版本变更规则

以下变化必须增加协议版本号，并在论文实验章节中说明：

- 数据集增删或替换；
- 主指标定义变化；
- block 大小变化；
- dedup 开关变化；
- baseline 集合变化；
- CPSS 候选集合变化；
- GridFlux 并发参数变化；
- 重复次数或随机种子变化；
- 真实链路带宽变化；
- failure/drop/retry 规则变化。

仅修改 bug、补充日志字段、补充测试、修复输出格式而不改变上述实验语义时，不需要改变协议版本，但必须记录代码 commit。

---

## 16. 最终实验主张

正式实验最终应支持或否定以下三个可证伪主张，而不是预设一定支持：

1. **端到端收益主张**：在 100 Mbps 真实链路上，CPSS 在科学/HPC 数据集合上能够降低总传输完成时间，或至少在不利数据上不显著劣于 Raw。
2. **自适应决策主张**：CPSS 通过有限采样能够接近 full-data global system oracle，且 probe-inclusive regret 随合理采样预算下降。
3. **安全退化主张**：当压缩计算和传输节省不匹配时，CPSS 能选择 Raw，避免把压缩失败或负收益隐藏在平均值中。

如果数据不支持其中任何主张，论文应如实收缩结论，而不是通过删除数据集、失败样本或不利带宽来维持预设故事。
