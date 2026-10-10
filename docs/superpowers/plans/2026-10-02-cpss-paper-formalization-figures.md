# CPSS paper formalization and figure revision

## Goal
Revise the DCC paper so the model-based selector in the Li thesis is presented as an earlier version of the same project, formalize the current probe-guided end-to-end Raw/compression decision without exceeding what the implementation supports, replace Figure 4's two-point lines with grouped bars, and produce an IEEE-style monochrome Figure 1.

## Scope and non-goals
- Update the English and Chinese manuscript, Figure 4 generator/output, and a separate editable monochrome Figure 1 source/output.
- Preserve the user's existing colored `Figures/cpss_architecture.drawio` and its geometry.
- Do not claim a new codec, a reproduced MLcomp result, general superiority, or unsupported oracle regret.
- Do not change experimental data or aggregation rules.

## Acceptance criteria
- Project lineage is explicit: same evolving system; current runtime policy is distinguished from the earlier learned-model implementation.
- Equations state the shared probe cost, estimated action cost, current Raw-bypass guard, and measured cold-path outcome consistently with code.
- Figure 4 shows only the two measured bandwidth categories, with no connecting trend lines or interpolation.
- Figure 1 is monochrome, print-legible, editable in draw.io, and retains existing user-adjusted geometry and decision/data paths.
- English and Chinese LaTeX builds succeed; generated PDFs and both figures are rendered and visually inspected; no unsupported claims or stale captions remain.

## Inputs and validation
- Current manuscript: `docs/dcc_latex/cpss_submission/main.tex`, `main_zh.tex`.
- Figure generator/data: `Script/plot_dcc_two_figures.py` and the measured 10/100 Mbps CSV under `ExperimentRecords/dcc_analysis_20260930/ExperimentRecords/`.
- Runtime guard: `Script/run_cpss_sampling_dedup_matrix.py`.
- Validate formulas against runtime code and measured CSVs; build both LaTeX manuscripts; inspect rasterized final pages and figure assets.

## Dependencies and unresolved conditions
- Monochrome export requires the installed draw.io exporter or an equivalent renderer. If unavailable, preserve the editable source and report the export limitation rather than replacing the existing figure.
- The decision model must retain the distinction between probe cost (common to CPSS branches but charged against direct baselines) and the conditional compression-vs-Raw ranking.
