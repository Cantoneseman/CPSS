# CPSS DCC related work and citation map

This file records the citation set for the DCC paper. The LaTeX manuscript using these entries is:

`docs/dcc_latex/cpss_submission/main.tex`

## What can be written now

The following sections are independent of final aggregate numbers and can be written now:

1. **Motivation.** Scientific data movement is a joint compute/communication problem; a smaller payload is not automatically a shorter end-to-end transfer.
2. **Problem definition.** Define Raw and compression as competing actions and charge scan/probe, encode, transfer, decode, and reconstruction to one-shot `T_ready`.
3. **System design.** Describe CPSS sampling, candidate evaluation, Raw bypass, block bundle, GridFlux, remote tree reconstruction, and integrity checks.
4. **Related work.** Explain how prior work established total-delay and network-aware compression, while scientific compressors exploit type/layout information.
5. **Evaluation protocol.** Describe datasets, codecs, measured link, repetitions, metrics, and failure handling.
6. **Limitations and hypotheses.** State that the paper tests workload heterogeneity, Raw-vs-compression decisions, and probe cost; do not state final improvements yet.

The following sections need the completed/frozen run before they can be written as claims:

- final numerical results and aggregate speedup;
- CPSS versus best fixed codec;
- sampling-budget/oracle regret curve;
- bandwidth crossover conclusions;
- superiority over specialized scientific compressors.

## Core citations

| Key | Paper | Use in CPSS paper | Primary source |
|---|---|---|---|
| `krintz2001` | Chandra Krintz and Brad Calder, “Reducing Transfer Delay with Dynamic Selection of Compression Formats,” HPDC 2001 | Historical precedent for defining total delay as transfer plus decode and selecting formats dynamically | [author PDF](https://sites.cs.ucsb.edu/~ckrintz/papers/HPDC-01-DynComp.pdf) |
| `wiseman2004` | Yair Wiseman, Karsten Schwan, Patrick M. Widener, “Efficient End to End Data Exchange Using Configurable Compression,” ICDCS 2004 | Closest older systems precedent: scientific data, middleware, resource monitoring, method choice, and dynamic sampling | [DOI record](https://doi.org/10.1109/ICDCS.2004.1281587), [author PDF](https://u.cs.biu.ac.il/~wisemay/icdcs2004.pdf) |
| `knutsson1999` | Björn Knutsson and Mats Björkman, “Adaptive End-to-End Compression for Variable-Bandwidth Communication,” Computer Networks 31(7), 1999 | Supports the compute/network/resource tradeoff and bandwidth-aware adaptation | [DOI](https://doi.org/10.1016/S0169-7552(98)00299-2) |
| `burtscher2009` | Martin Burtscher and Paruj Ratanaworabhan, “FPC: A High-Speed Compressor for Double-Precision Floating-Point Data,” IEEE TC 58(1), 2009 | Specialized lossless floating-point baseline and motivation for future dtype-aware evaluation | [author PDF](https://userweb.cs.txstate.edu/~mb92/papers/tc09.pdf) |
| `lindstrom2006` | Peter Lindstrom and Martin Isenburg, “Fast and Efficient Compression of Floating-Point Data,” IEEE TVCG 12(5), 2006 | FPZIP as a representation-aware floating-point compressor; scope boundary for current byte-stream path | [FPZIP project](https://github.com/llnl/fpzip) |
| `unat2009` | Didem Unat, Theodore Hromadka III, Scott B. Baden, “An Adaptive Sub-Sampling Method for In-memory Compression of Scientific Data,” DCC 2009 | Distinguish adaptive scientific representation/subsampling from CPSS transfer-action selection | [DCC PDF](https://cseweb.ucsd.edu/groups/hpcl/scg/papers/2009/dcc09.pdf), [DOI](https://doi.org/10.1109/DCC.2009.65) |
| `middlezong2026` | Michael Middlezong, “Striking the Balance Between Speed and Compression Ratio: A Fast Bit-Grouping Algorithm and Adaptive Compressor Selection for Scientific Data,” FGCS 180, 2026 | Closest recent comparator; cite explicitly and differentiate CPSS as Raw-vs-codec, remote, end-to-end lossless transfer system | [DOI](https://doi.org/10.1016/j.future.2026.108370), [article page](https://www.sciencedirect.com/science/article/abs/pii/S0167739X2600004X) |
| `azami2025` | Noushin Azami, Alex Fallin, Martin Burtscher, “Efficient Lossless Compression of Scientific Floating-Point Data on CPUs and GPUs,” ASPLOS 2025 | Modern specialized lossless scientific-compression baseline; do not claim general superiority without running one | [author PDF](https://userweb.cs.txstate.edu/~burtscher/papers/asplos25.pdf) |

## Recommended related-work paragraph

> Existing work has shown that compression should be evaluated as a system tradeoff rather than by compressed size alone. Dynamic compression-format selection minimizes a transfer-delay objective that includes post-transfer decompression [Krintz and Calder]. Configurable middleware has further combined network/CPU monitoring and data sampling to choose among lossless methods for distributed scientific and commercial data [Wiseman et al.], while adaptive end-to-end compression has studied the same compute-versus-bandwidth tradeoff under changing links [Knutsson and Björkman]. Scientific floating-point compressors such as FPC and FPZIP exploit representation structure unavailable to generic byte-stream codecs. More recent work uses sampling to select scientific compressors using compression and I/O efficiency [Middlezong]. CPSS is complementary: it treats direct Raw transfer and compression as competing actions, charges decision and restoration overhead in one-shot `T_ready`, and evaluates exact directory-tree transfer over a cross-cloud GridFlux path.

## Important novelty warning

The 2026 Middlezong paper is close enough that a reviewer may ask what is new. The paper must not claim “first adaptive sampling-based compressor selection.” The defensible distinction is:

- CPSS is a **transfer system**, not a new compressor;
- its action space explicitly includes **Raw**;
- its objective is **one-shot sender–network–receiver time**, including selection, remote decoding, and reconstruction;
- its main experiment is **lossless exact transfer** over a real cross-cloud path;
- sampling is a secondary mechanism whose cost is measured, not the headline contribution.

If the final paper instead includes a representation-aware scientific codec or a physical bandwidth sweep, update this scope statement and the baseline list.
