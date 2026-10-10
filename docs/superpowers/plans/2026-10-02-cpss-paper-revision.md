# CPSS paper revision

## Goal
Revise the DCC English/Chinese manuscripts, experimental figures, and Figure 1 source so the paper presents one continuous system, reads compactly, and follows DCC submission constraints.

## Scope and non-goals
- In scope: Related Work/positioning, the MLcomp-style pilot wording, methodology compression, Figure 3 spacing, Figure 4 legend/gray-scale encoding, section hierarchy, and an editable Figure 1 draw.io deliverable.
- Out of scope: changing measured results, rerunning experiments, changing the CPSS algorithm, or adding claims not supported by current measurements.

## Acceptance criteria
- Neither manuscript frames the project's earlier selector/thesis as external prior work or names its author.
- The central system framing foregrounds data-dependent codec selection, the Raw bypass, and end-to-end time-to-availability including probe cost.
- The pilot reports observed measurements without the "mechanism example / cannot prove" disclaimer and without implying a full MLcomp reproduction.
- Experimental methodology is modestly shorter while preserving workload, repetition, link, baseline, and aggregation details needed to reproduce the study.
- Figure 3 has balanced row-to-axis padding; Figure 4 has a compact, representative bar-patch legend and remains legible in grayscale.
- Figure 1 is available as an editable draw.io file matching the IEEE-style monochrome artwork.
- Headings are consistently numbered with readable major-section spacing; template-defined one-column layout, 12-pt text, margins, and the 10-page maximum remain satisfied.
- English and Chinese versions remain substantively aligned; both compile and figures render.

## Inputs and validation
- Inputs: docs/dcc_latex/cpss_submission/main.tex, main_zh.tex, refs.bib, the figure generator scripts, current experiment CSVs, and the bundled DCC template.
- Validation: regenerate the two paper figures and monochrome Figure 1 source/artwork; compile both manuscripts with the bundled DCC class and bibliography style; inspect the final PDFs for page count, heading/float placement, and figure legibility.
