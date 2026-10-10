# DCC Experiment Preparation Design

**Date:** 2026-09-16  
**Scope:** Prepare the frozen DCC experiment without changing the scientific protocol.

## Goal

Make the Shenzhen-controlled DCC experiment reproducible before any formal dataset is downloaded: CPSS sampling must use the registered random seed, the full sampling matrix must be runnable and resumable, results must be aggregated using the frozen metrics, and both nodes must expose an auditable environment.

## Invariants

- Shenzhen remains the controller and sender; Shanghai remains the receiver.
- The frozen protocol remains `v1.0-frozen`: 128 MiB blocks, no deduplication, CRC32C, two GridFlux connections, two file workers, and 100 Mbps as the declared main model bandwidth.
- The 100 Mbps value is not silently replaced by a short `iperf3` measurement; measured throughput is recorded as link evidence only.
- Raw is always retained as a legal action and an explicit reference row.
- Sampling experiments use `random_sampling`; the CLI seed must reach `calibrate_fixed_pipeline` and be recorded in every CPSS row.
- Formal results never mix pilot/preflight output with the matrix result root.
- A failed cell or method row remains visible and is never deleted or converted into a speedup.

## Components

1. `Script/run_dcc_formal_gridflux.py`: pass `--random-seed` into random calibration, persist sampling metadata, capture local/remote environment manifests, and write cleanup audit records.
2. `Script/run_dcc_formal_matrix.py`: deterministic sequential launcher for `sample-count in {1,2,5,10,20}` and the five preregistered seeds. Each cell has an independent run id and can be rerun without overwriting another cell.
3. `Script/summarize_dcc_formal_matrix.py`: read only the selected matrix root, emit per-cell and grouped CSV/JSON summaries, report total-time speedup and geometric-mean speedup, Raw fallback, failure coverage, and probe-inclusive regret.
4. `requirements-dcc-experiment.txt`: freeze the test dependency (`pytest==9.1.1`) separately from runtime codec requirements.

## Error and cleanup behavior

- The launcher records the command, return code, and cell status; it continues to the next cell only when `--continue-on-error` is explicitly supplied. The default stops on the first failed cell so a partial matrix cannot be mistaken for a complete experiment.
- The runner writes `environment_manifest.json` before the first dataset, and `cleanup_audit.json` after every remote server/verification directory cleanup.
- Aggregation excludes failed rows from speedup denominators, reports their counts separately, and refuses to label an empty group as a successful result.

## Acceptance tests

- Different seeds produce different random sample indices when enough blocks exist; the runner's preflight record contains the requested seed and `sampling_mode=random_sampling`.
- Matrix cell generation is deterministic, unique, and covers all 25 budget/seed combinations.
- Aggregation computes total-time speedup as a ratio of sums and geometric speedup as `exp(mean(log(speedup)))`, not arithmetic mean speedup.
- Shenzhen `pytest tests` passes; Shanghai can compile/import the same source and has the same pinned pytest version.
- A small real GridFlux Shenzhen-to-Shanghai preflight completes Raw, fixed compression, and CPSS with successful roundtrip verification and no remote residue.
