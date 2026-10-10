# DCC 2027 paper draft v0 — CPSS

**状态：** 全文逻辑与英文草稿 v0；不是可直接提交的版本。方括号中的数字、设备信息和引用必须在正式运行结束后核实并替换。当前深圳主实验尚未完成，且有基线失败，因此本文故意不把临时结果写成最终结论。

**建议标题：** *CPSS: End-to-End Lossless Data Transfer with Cost-Aware Compression Decisions*

## DCC 投稿格式与当前实验证据边界

- DCC 2027 官方模板是**单栏、12 pt**，上边距 1 英寸、左边距 1.25 英寸、正文区域 9 × 6 英寸；最多 **10 页，包含参考文献、图、表和附录**。提交 PDF，作者及单位保留在首页（single-blind）。官方提供 Word 与 LaTeX 源文件。
- 官方入口： [Information for Authors](https://datacompressionconference.org/information-for-authors/)、[Word sample](https://datacompressionconference.org/AuthorKit/DCCTemplate.doc)、[LaTeX sample](https://datacompressionconference.org/AuthorKit/DCCTemplateLaTeX.zip)、[one-page formatted PDF](https://datacompressionconference.org/AuthorKit/DCCTemplate.pdf)。不要套用常见 IEEE 双栏模板。
- 截止时间官方写为 **2026-10-02 23:59 U.S. Pacific Time**；折算新加坡/中国标准时间为 2026-10-03 14:59。CMT 提交入口在官方 Paper Submission 页。官方 Important Dates 页面写接收通知为 11 月 22 日，但 Authors 页正文仍有“late December”旧文案，需以 CMT/最新重要日期页确认。

### 当前服务器运行快照（2026-09-29）

正式实验协议与运行配置：10 个数据集；Raw、gzip-6、LZ4、Zstd-3、XZ-6、CPSS-adaptive；100 Mbps 实际跨云 GridFlux；128 MiB 切块；2 connections、2 file-parallelism；CRC32C；无重删。CESM、Miranda、NOAA 计划各 10 次，其余 3 次。

截至 2026-09-29 10:43（服务器本地时间）的草稿快照，活动续跑 `dcc_main_v1_20260927_cont1` 仍在执行：224 行、209 条成功、15 条失败；仅开始了 10 个数据集中的 8 个，失败均为 LZ4 在当前远端注册表中不可部署。当前在跑 RCSB mmCIF 的 XZ 第 3 次重复。Hurricane 的 CPSS 记录触发 `sample_budget_exhausted_unstable_fallback_raw`，而样本聚合估计认为压缩更快；隔离副本已修复这个分支和 LZ4 注册，但修复尚未进入正在运行的任务。因此：

1. 不能报告“正式实验已完成”，不能把 15 条失败当作性能结果。
2. 不能宣称已经证明“接近 oracle”。CSV 中 `system_regret_vs_oracle` 的分母由本地完整压缩/解压时间与配置带宽估算，未包含与实测 T_ready 相同的扫描、容器、GridFlux、接收重建等边界；这是模型 regret，不是同边界的实测决策 regret。
3. 当前正式 runner 每次 CPSS 仅用固定种子 20260727、最多 2 个随机 4 MiB 窗口；协议计划的 1/2/5/10/20 样本预算和 5 种子曲线尚未在正式全数据集上完成。
4. 7 个科学数值数据上，当前初步记录中 CPSS 对 CESM、ExaFEL 选择 Zstd，对 Hurricane、Miranda、NYX、HACC、SCALE 选择 Raw。Raw 选择包含扫描/探测成本，不等于“绝不慢于直传 Raw”。ExaFEL 的 CPSS 目前比固定 Zstd 的 T_ready 中位数慢约 22%；采样成本确实可能抵消收益。
5. 已有的部分固定 Zstd 结果显示明显异质性：对 Raw 的中位 T_ready 加速比，CESM 约 1.11×、Hurricane 约 1.25×、ExaFEL 约 1.48×、RCSB mmCIF 约 2.60×；Miranda、NYX、HACC、SCALE 则约 0.89×、0.83×、0.88×、0.94×。这些是**未完实验的方向性数据**，在 LZ4 缺失、其余数据集未开始、运行顺序未随机化的情况下不应写进最终摘要。
6. 活动正式 runner 将每个 block 作为字节流传给 codec，未把数据集的 dtype/shape 传入这条压缩路径；因此当前结果是“科学/HPC 工作负载上的通用字节流压缩决策”，不是类型感知或数值专用压缩。

### 投稿故事建议

主角不是新的编码器，也不是旧训练模型，而是一个系统决策问题：**压缩能省网络字节，但编码、探测和接收端恢复会花时间；因此“何时压缩、何时直传”应按整条数据路径决定。** 论文的结论不应该是“CPSS 对所有数据都更快”，而应是“数据类型和链路决定压缩是否有净收益；我们实现并测量一个带采样成本的决策系统，并把有利与不利情形同时呈现”。这里的 Raw bypass 是采样后决定不对整份数据压缩、直接传原目录；不是压缩失败后才临时回退，也不是无开销或最坏情况性能保证。

当前结果最多支持“系统已经实现了这一决策路径，并出现了有利与不利数据的分化”，尚不能支持强泛化收益或 oracle 质量主张。论文需作为系统/实验论文定位；机器学习模型、重删和有损压缩均不列为本文贡献。

---

# Manuscript draft

## CPSS: End-to-End Lossless Data Transfer with Cost-Aware Compression Decisions

Author names and affiliations: [insert actual author list and affiliations]

**Abstract—** Scientific data transfers are often accelerated by lossless compression, but reduced payload size does not guarantee shorter time-to-availability: encoding, probing, network transfer, decoding, and reconstruction all contribute to the result. We present CPSS, a sampling-guided system that chooses between raw transfer and a lossless compression pipeline for each dataset-level transfer. CPSS probes a small number of windows, estimates end-to-end cost using the observed transfer bandwidth and receiver-side work, and bypasses full-data compression when its predicted benefit is insufficient. The system packages compressed blocks with reconstruction metadata, transfers the bundle through GridFlux, and restores the original directory tree at the receiver. We evaluate CPSS against raw transfer and commonly used general-purpose codecs on ten scientific/HPC and scientific-text datasets over a cross-cloud link. We report time-to-availability, transferred bytes, independent integrity validation, probe cost, and decision quality. Results show [FINAL aggregate result], with substantial variation across workloads: [FINAL favorable/unfavorable cases]. Our findings demonstrate that compression decisions for scientific data movement must account for the complete transfer lifecycle rather than compression ratio alone.

**Index Terms—** lossless compression, scientific data, adaptive compression, data transfer, end-to-end performance, sampling.

## 1. Introduction

Scientific simulations and instruments increasingly produce multi-gigabyte datasets that must be moved between storage and compute sites. Lossless compression is an attractive way to reduce transfer volume, but a smaller representation is not automatically faster to deliver. Compression consumes sender CPU and memory bandwidth; a receiver must decode and reconstruct the original dataset; and a calibration procedure may itself read and compress data before selecting a method. On a fast link or a weakly compressible input, these costs can exceed the time saved on the network.

This creates a system decision rather than a codec-only objective. For an input dataset, a transfer system must decide whether to send raw bytes or apply a codec, and—if compression is worthwhile—which pipeline to use. The decision depends on input structure, codec speed, receiver speed, bundle overhead, and available bandwidth. A benchmark that reports only compression ratio or encoder throughput cannot answer whether an application receives usable data sooner.

We address this problem with CPSS, a lossless compression-and-transfer system for scientific/HPC directory trees. CPSS uses low-cost random windows to compare a small set of candidate pipelines, projects the measured costs to the dataset, and selects either a compressed bundle or direct Raw transfer. Its objective includes probe and scan costs for one-shot transfers, as well as compression, network, and remote restoration costs. CPSS is designed to make compression optional: a bypass is a normal system decision, not a failed compression run.

This paper makes four contributions:

1. We formulate dataset transfer as an end-to-end action-selection problem that compares Raw and compression using sender, network, and receiver costs.
2. We implement CPSS, which probes randomized data windows, estimates pipeline costs, packages fixed-size lossless blocks with metadata, transfers them using GridFlux, and reconstructs the source tree remotely.
3. We evaluate the complete data path on a fixed suite of scientific/HPC and scientific-text workloads, against Raw, gzip, LZ4, Zstandard, and XZ, reporting time-to-availability and integrity separately.
4. We characterize when compression helps, when Raw bypass is selected, and the probe cost as a supporting factor in the system decision: [insert final results, only after the formal run and oracle-boundary audit].

Our central finding is not that compression always helps. Rather, compression benefit is workload- and link-dependent, and the probe itself has a measurable cost. For example, preliminary results show that Zstandard shortens the median T_ready on CESM and ExaFEL, while the same codec is slower than Raw on several other scientific arrays. CPSS selected Raw on those latter datasets, but its one-shot T_ready still includes the work already spent scanning and probing. The final paper will quantify these effects across all datasets and repetitions.

## 2. Background and Problem Formulation

### 2.1 End-to-end transfer actions

Let D be a dataset with raw size S and let B denote the effective link bandwidth in bytes per second. The available actions are Raw transmission or a lossless pipeline p. For a compression pipeline, the data-path cost is

T(p,D) = T_enc(p,D) + T_wire(p,D) + T_dec(p,D) + T_rebuild(p,D),

where T_wire includes the bytes actually sent—including the container and manifest—and receiver reconstruction produces the original directory tree. Raw transfer has T(Raw,D) = T_wire(Raw,D). For a one-shot adaptive decision, the system must also charge the data inspection and probe cost:

T_adaptive(a,D) = T_scan(D) + T_probe(D) + T(a,D).

The decision objective is therefore to minimize predicted time to availability, not to maximize compression ratio. The full-data oracle used for analysis is an idealized full-information reference, not a deployable selector. Its cost accounting must be aligned with the measured T_ready boundary before we report decision regret.

### 2.2 Lossless and scientific compression

Generic codecs such as gzip [2], LZ4 [3], Zstandard [1], and XZ operate on byte streams and expose different speed/ratio tradeoffs. Zstandard defines a lossless framed format suitable for streaming and file compression [1]. Scientific arrays also motivate representation-aware transforms and specialized compressors. Some widely studied floating-point compressors expose lossy or near-lossless modes (e.g., ZFP [4] and SZ [5]); because our target is bit-exact reconstruction, lossy configurations are outside this paper's main comparison. Reversible array-aware pipelines such as bit-shuffle plus a general codec remain important baselines and must be measured before claiming superiority over scientific compression systems.

Prior work on GridFTP and data movement establishes that parallel streams and system-level transfer behavior matter in wide-area scientific workflows [6]. Our focus is complementary: given a fixed transfer substrate, we study the compression-versus-Raw decision and include the full sender-to-receiver path.

## 3. CPSS System Design

### 3.1 Overview

CPSS has a sender-side preparation stage, a transfer stage, and a receiver-side restoration stage. The sender scans a directory tree, records paths and file properties, divides files into 128 MiB blocks, and prepares a fixed set of candidate pipelines. In adaptive mode, it chooses randomized 4 MiB windows from distinct blocks, compresses each window with available fast candidates, measures the resulting sizes and encode/decode costs, and aggregates an estimate. Depending on the estimated one-shot cost, CPSS either builds a compressed bundle or sends the original tree.

The transfer stage uses GridFlux with two connections and two-way file parallelism. At the receiver, a Raw action preserves the original tree directly; a compressed action decodes each block and rebuilds the directory structure from the manifest. GridFlux CRC32C is part of the transfer operation. Independent SHA-256 verification is measured separately so that T_ready (data usable) and T_verified (data independently audited) are not conflated.

### 3.2 Probe and candidate selection

The current formal configuration uses up to two randomized windows of 4 MiB each, a fixed seed recorded in the run metadata, and a configured fast candidate set containing Zstandard, LZ4, zlib, and delta followed by Zstandard. The probe samples distinct existing execution blocks and a seeded 8-byte-aligned offset. For each valid trial, CPSS records encoded size and local encode/decode time; after choosing a candidate, it measures a selected-pipeline system probe through GridFlux and the remote restoration path. The sample costs are scaled to the full dataset and compared with Raw under the configured link rate. The system may stop after stable sample winners or at the sample budget.

Let b_i be the sampled raw bytes and c_i(p) the observed cost of pipeline p on sample i. CPSS estimates a dataset-level pipeline cost from the aggregate sample rate and tests whether the predicted compression action, including scan and probe overhead, is below the Raw estimate. If the prediction does not justify compression, CPSS bypasses full-data encoding and transfers Raw. Sampling is an approximation: sample representativeness and prediction errors are measured, not assumed away.

Important implementation detail for the final version: the active Shenzhen run still uses the pre-repair stopping branch, and LZ4 is absent from the active remote deploy registry. The submitted manuscript must describe and measure one frozen implementation only; if a repaired policy is used, rerun the affected comparisons with that exact source snapshot and archive its hash.

### 3.3 Bundle and exact reconstruction

For compressed actions, CPSS serializes block payloads and per-block metadata in a self-describing bundle. Each block header records the pipeline identifier and payload/original sizes; a manifest records file paths, block offsets, and reconstruction information. This permits the receiver to select the matching decoder without retraining or an external classifier. The formal DCC evaluation disables deduplication so that the measured benefit comes from compression and transfer decisions rather than duplicate elimination.

In the current formal runner, codecs receive byte blocks; dataset dtype and multidimensional shape are not propagated into this path. Thus, our current adaptation selects among byte-stream pipelines and must not be described as type-aware numerical compression. Representation-aware reversible codecs are a separate baseline requirement for any broader scientific-compression claim.

### 3.4 Integrity and failure semantics

An action selected as Raw is a successful execution if the original tree arrives and verifies. It is distinct from a compression failure followed by retry. If sampling has already occurred, its cost remains charged to one-shot T_ready. The decision rule attempts to avoid compression when its estimated benefit is insufficient, but it cannot promise raw-baseline performance under prediction error: a mistaken compression decision or sunk probe work can make one-shot completion slower than direct Raw. We therefore report bypass frequency, regret, and failures explicitly rather than calling Raw a worst-case guarantee.

## 4. Experimental Methodology

### 4.1 Data and platform

We use ten datasets: seven scientific/HPC workloads (CESM atmospheric fields, Hurricane Isabel, Miranda, NYX, HACC, SCALE-LETKF, and ExaFEL) and three structured scientific/bioinformatics text workloads (RCSB mmCIF, NCBI GRCh38 FASTA, and NOAA GSOD CSV). The inputs retain their directory structure and are not pre-tarred. Dataset versions, raw sizes, file counts, hashes, machine specifications, codec versions, and software revision are recorded in the artifact bundle.

The main transfer path is a Shenzhen-to-Shanghai cross-cloud GridFlux deployment. The target rate is 100 Mbps; the formal runner uses two connections, file parallelism two, CRC32C, and 128 MiB compression blocks. All methods use the same transfer settings. [Insert sender/receiver CPU, memory, OS, exact GridFlux build, effective bandwidth validation, and dates from the archived environment manifest.]

### 4.2 Compared methods

We compare direct Raw with gzip level 6, LZ4 default, Zstandard level 3, XZ preset 6, and CPSS-adaptive. This set spans commonly deployed general-purpose lossless codecs. All results must be strictly lossless and pass receiver reconstruction. For numeric scientific arrays, the final paper should additionally include at least one reversible representation-aware baseline such as Blosc2 bit-shuffle plus Zstandard and, if time allows, ZFP reversible mode. If these cannot be run, we will state explicitly that the evaluation compares general-purpose codecs and does not establish superiority over scientific-specialized compressors.

The formal run plans three repetitions per dataset/method and ten repetitions for CESM, Miranda, and NOAA. Report medians and distributions, not only means. The run order is currently deterministic; before final aggregation, check for time/order effects and report this limitation or rerun a randomized/blocked order.

### 4.3 Metrics

Our primary metric is T_ready, the time from the beginning of a method's measured data path until the receiver has a usable reconstructed directory. For CPSS this includes scan, probe, full-data encoding if selected, GridFlux transfer, and remote decoding/reconstruction. For direct Raw it includes GridFlux transfer. T_verified adds independent SHA-256 verification and is reported separately. We also report actual transferred bytes, payload compression ratio, selected action/pipeline, probe time, sender encoding time, transfer time, and receiver restore time.

For each dataset d, speedup is T_ready(Raw,d) / T_ready(method,d). Across datasets we report the geometric mean and the ratio of summed median times; each answers a different question. A sampling regret comparison is reported only against an oracle constructed with the same cost components and data-path boundary. Prediction error, candidate availability, method failures, and successful Raw bypass are separate categories.

### 4.4 Questions

We evaluate four questions:

- Q1: Does a compression pipeline reduce T_ready relative to direct Raw, and how does the answer vary by dataset? Independent integrity verification is reported separately.
- Q2: Does CPSS choose Raw on workloads for which compression is not predicted to pay, while selecting a pipeline on favorable workloads?
- Q3: How much sampling is needed to approach a full-information decision, and what is the probe cost? (This requires a separate 1/2/5/10/20-window, multi-seed experiment; the current formal run's two-window setting alone cannot answer it.)
- Q4: How do the decisions change with link bandwidth? We model 10, 100, and 1000 Mbps using measured local components and bytes; modeled values are not presented as physical network measurements.

## 5. Results

**This section contains placeholders because the formal run is not complete. Do not submit until each placeholder is replaced from a validated final CSV.**

### 5.1 End-to-end results across workloads

Table 2 reports median T_ready, transferred bytes, and speedup for Raw, each fixed codec, and CPSS-adaptive. On the completed subset, general-purpose compression is not uniformly beneficial. Zstandard is faster than Raw on CESM, Hurricane, ExaFEL, and RCSB, but slower on Miranda, NYX, HACC, and SCALE-LETKF. This variation motivates an action-selection system rather than a claim that every scientific dataset should be compressed.

After the repaired run completes, CPSS [FINAL: number] of [FINAL: ten] datasets with compression and [FINAL: number] with Raw. On compressible workloads, it achieves [FINAL] geometric mean speedup relative to Raw and [FINAL] relative to the best fixed general-purpose codec. On less favorable workloads, Raw bypass incurs [FINAL] median one-shot overhead versus direct Raw, primarily from [scan/probe]. These numbers determine whether the contribution is a net system improvement or a careful characterization of its operating region.

### 5.2 Why compression can lose

Compression saves network time only when the reduction in transferred bytes is larger than the sender and receiver work it introduces. In the partial results, Zstandard's measured T_ready is approximately 11% faster than Raw on CESM and 48% faster on ExaFEL, but about 11% slower on Miranda, 17% slower on NYX, 12% slower on HACC, and 6% slower on SCALE-LETKF. These figures are provisional and will be replaced with final medians and confidence intervals. The final conclusion should report this heterogeneity directly: Raw bypass avoids full-data encoding on predicted-unfavorable inputs, but it does not erase probe costs or guarantee equality with a no-analysis Raw transfer.

### 5.3 Probe cost and decision quality

Figure 4 will plot probe bytes/time and decision regret as a function of sampling budget. This is the evidence for the question “how much probing is needed for a decision close to the oracle?” The current full experiment fixes the budget at two samples and therefore supports only a point on that curve. Before submission, run budgets 1, 2, 5, 10, and 20 on representative favorable and unfavorable workloads under multiple registered seeds; report both candidate/action agreement and end-to-end cost regret. Compute regret against a same-boundary oracle or clearly label it model regret.

The currently logged value `system_regret_vs_oracle` compares a measured T_ready against a model oracle that excludes some measured-system costs. We do not use that value as a final decision-quality result. The final table/figure must be regenerated after aligning the model and measured boundaries.

### 5.4 Bandwidth sensitivity

At low bandwidth, payload reduction becomes more valuable; at high bandwidth, compression CPU and probe overhead dominate. We recompute the action costs at [10, 100, 1000] Mbps using measured sender/receiver components. The 100 Mbps point is also compared with the real GridFlux measurement, while 10 Mbps and 1 Gbps are explicitly modeled scenarios. Figure 5 identifies the crossover bandwidth at which each workload changes from Raw to compression, if such a crossover exists.

## 6. Discussion

The results support a lifecycle view of lossless compression. A codec that is attractive in a compression-ratio chart can be a poor transfer choice when encoding or decoding is slow. Conversely, a moderate ratio can still reduce time-to-availability on a bandwidth-limited path. A sample-based selector adds another tradeoff: more probing can improve the decision but consumes time and can erase the benefit for small inputs. The most useful deployment policy may therefore depend on whether a decision can be reused across repeated transfers of similar data; that warm/reuse setting is future work unless measured in this submission.

The scope of our claims is intentionally limited. CPSS is not a new entropy coder and does not claim the best ratio for every workload. Our main codec comparison uses general-purpose lossless methods; without a completed reversible scientific-compressor baseline, we do not claim superiority over domain-specialized scientific compression. The current dataset suite is broad enough to expose substantial variation but does not cover all simulation formats, sites, links, or production access patterns. A 100 Mbps cross-cloud path is one operational point, and the modeled bandwidth sweep does not replace multiple physical links.

The active experiment also exposed practical issues that matter to reproducibility: a missing LZ4 deploy registration caused failed baseline rows, and an unstable-sample fallback branch could reject a predicted-beneficial pipeline. We preserve these failures in the audit log, repair the implementation, and rerun affected rows rather than silently treating failures as slow results. The final artifact should contain the exact tested source snapshot, configs, dataset manifest, complete per-run CSV, and all failure/retry records.

## 7. Conclusion

We presented CPSS, a sampling-guided lossless transfer system that compares Raw and compressed actions using end-to-end costs and restores data remotely. The key design implication is that compression is conditional: data structure and link speed determine whether the reduction in network bytes exceeds probing, encoding, and reconstruction costs. Our evaluation reports both successful compression and Raw bypass cases, along with the one-shot cost of deciding. After finalizing the complete ten-dataset run and sampling-budget study, we will quantify [FINAL main aggregate] and identify the workload and bandwidth regions in which adaptive compression is beneficial.

## References (verify final bibliographic format)

[1] Y. Collet and M. Kucherawy, “Zstandard Compression and the application/zstd Media Type,” RFC 8878, Internet Engineering Task Force, 2021.

[2] P. Deutsch, “GZIP File Format Specification Version 4.3,” RFC 1952, Internet Engineering Task Force, 1996.

[3] LZ4, “LZ4 Frame Format Description,” software specification, https://github.com/lz4/lz4.

[4] P. Lindstrom, “Fixed-Rate Compressed Floating-Point Arrays,” IEEE Transactions on Visualization and Computer Graphics, vol. 20, no. 12, pp. 2674–2683, 2014.

[5] S. Di and F. Cappello, “Fast Error-Bounded Lossy HPC Data Compression with SZ,” in Proc. IEEE International Parallel and Distributed Processing Symposium (IPDPS), 2016, pp. 730–739.

[6] W. Allcock, J. Bresnahan, R. Kettimuthu, and M. Link, “The Globus Striped GridFTP Framework and Server,” in Proc. ACM/IEEE Conference on Supercomputing (SC), 2005.

[7] Add the precise GridFlux system/software citation and the applicable Blosc2/bitshuffle or reversible scientific compressor citations after fixing the final comparison set.

---

## 图表规划与草图（最终正文建议 4–5 图、2 表）

| 编号 | 目的、图形 | 横/纵轴与主要编码 | 当前状态 |
|---|---|---|---|
| Fig. 1 | 系统架构：让读者一眼看懂 CPSS 是系统，而不是新 codec | 输入目录 → 扫描/manifest → 随机窗口探测 → Raw/codec 决策 → bundle 或 Raw → GridFlux → 解压/重建 → 可用/校验；在各阶段旁标注计时边界 | 可立即绘制 |
| Fig. 2 | 主结果：展示跨数据集性能异质性 | x=10 个数据集，y=相对 Raw 的 T_ready speedup；每个方法一组点/误差线；y=1 基准线；CPSS 选择 Raw 的样本用空心菱形标出 | 等完整、修复后 CSV |
| Fig. 3 | 时间分解：显示节省来自哪里、成本在哪 | 每个代表数据集做横向堆叠条：probe/scan、encode、GridFlux、decode/rebuild；Raw 作为对照；注明 T_ready 不含独立 SHA | 目前可基于有效行绘草稿 |
| Fig. 4 | 采样成本 vs oracle 决策质量（用户确认的重点次实验） | x=采样窗口数或探测字节/秒；左 y=decision regret / probe-inclusive regret，右 y=oracle action 命中率；不同曲线对应 5 个 seed 或代表数据类型 | 需补 1/2/5/10/20、多 seed 实验 |
| Fig. 5 | 带宽敏感性与动作翻转阈值 | x=带宽（对数坐标），y=模型 T_ready；Raw 与 2–3 个压缩动作曲线；交点标 Raw/压缩决策 crossover；真实 100 Mbps 点用实心符号，模型点用虚线 | 需要生成模型曲线并校准 |
| Table 1 | 数据集及透明度 | 类型、精度/格式、大小、文件数、来源、是否连续/文本、原始哈希 | 元数据已准备，需最终核验 |
| Table 2 | 系统与运行口径 | 主机/CPU/内存、OS、codec 版本、GridFlux 设置、切块、采样窗口/seed、重复次数、计时边界 | 从最终 environment manifest 填充 |

### Fig. 1 架构草图

    Sender: dataset tree
       │
       ├── scan + manifest + block map ─────────────────────────────┐
       │                                                            │
       └── random 4-MiB windows → candidate probes → cost estimate  │
                                      │                              │
                          ┌───────────┴───────────┐                  │
                          │ predicted benefit?    │                  │
                          └──────┬─────────┬──────┘                  │
                             yes│         │no                         │
                                ▼         ▼                           │
                       CPSS bundle     original tree                  │
                                └────┬────┘                           │
                                     ▼                                │
                         GridFlux + CRC32C                            │
                                     ▼                                │
                 Receiver: decode/rebuild if bundle; else keep Raw ◄──┘
                                     │
                         T_ready ────┴──── T_verified (+ SHA-256)

### Fig. 2 主结果草图

    speedup
      2.5 |                              ◆ RCSB (fixed Zstd; CPSS pending)
      2.0 |
      1.5 |       ◆ Hurricane     ◆ ExaFEL
      1.0 |------- Raw = 1.0 ------------------------------------------
      0.5 |               ◆ Miranda  ◆ NYX  ◆ HACC  ◆ SCALE
          +-----------------------------------------------------------> dataset
              CESM   Hurr.   Mir.   NYX   HACC   SCALE   ExaFEL   text

以上位置仅是阅读已完成部分的临时印象，不是制图数据；最终应画每次运行的点/中位数和 95% bootstrap CI，并统一显示全部方法，避免挑数据集。

### Fig. 4 采样实验草图

    regret / hit rate
       ^  regret ─────╲____          hit rate - - - - -╱‾‾‾‾
       |               ╲____                       __╱
       +-------------------------------------------------> probe budget
              1       2        5        10       20 windows

需要同时报告探测成本，避免只画命中率：横轴可用窗口数或探测毫秒；建议按负载类型分层（有利/临界/不利），至少 5 个固定 seed。若预算增大并不稳定降低 regret，也应如实画出而不删点。

### Fig. 3 端到端时间分解草图

    direct Raw     |---------------- GridFlux transfer ----------------|
    CPSS → Raw     |scan|probe|------------- GridFlux transfer ---------|
    CPSS → codec   |scan|probe|encode|------ GridFlux transfer -----|decode|rebuild|
                                                                         ↑
                                                       T_ready ends here; SHA is separate

图中每段必须来自同一行运行的原始计时字段；不要把独立 checksum/SHA 时间堆进 T_ready。对照中可突出某些输入的压缩传输节省小于 encode/decode 成本。

### Fig. 5 带宽敏感性草图

    T_ready
       ^     Raw  ╲
       |           ╲         CPSS/codec = fixed compute + fewer wire bytes
       |    CPSS ───╲____
       |             ×  crossover (if observed)
       +------------------------------------------------> bandwidth (log scale)
             10 Mbps      100 Mbps      1 Gbps
                           ● measured

这是示意形状而非结果：把 100 Mbps 的实测点单独标注；10 Mbps 与 1 Gbps 只能标模型预测。不同数据可能没有交点，或交点不在测量区间内。

## 截稿前必须关闭的实验漏洞（优先级）

1. **P0：** 等活动任务结束，修复并重跑 15 条 LZ4 失败及 Hurricane 受影响行；若无法全重跑，缩小可报告范围并清晰披露，不能混用未修复和修复代码结果。
2. **P0：** 跑完 NCBI、NOAA 和其余完整数据集；检查所有方法完成数、checksum/round-trip、失败审计和环境 manifest。
3. **P0：** 修正“oracle regret”口径：以相同 T_ready 分解构造 oracle，或改名为 model regret 并与实测决策质量分开。
4. **P1：** 至少在 CESM、Miranda、ExaFEL/Hurricane 等代表数据上跑 1/2/5/10/20 窗口和多 seed，才能回答探测成本问题；来不及则删掉“接近 oracle”主张，只报告两窗口点。
5. **P1：** 对科学数值输入加入 Blosc2 bit-shuffle + Zstd 等适用的严格无损专业基线；若截止前无法完成，则限制论文结论为 general-purpose codec comparison。
6. **P1：** 检查运行顺序偏差及 WAN 波动，按数据集给出配对比较、置信区间和失败覆盖率；至少保证每个被纳入主图的方法有完整相同运行数。
7. **P2：** 生成真实数据图，不把草图位置作为结果；核实所有数据集版本、样本构建方式、编码器线程数、硬件和有效链路带宽。
