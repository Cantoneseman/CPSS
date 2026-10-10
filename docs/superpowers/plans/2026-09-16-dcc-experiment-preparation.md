# DCC Experiment Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the frozen DCC experiment reproducible and auditable before formal dataset execution.

**Architecture:** Keep the existing GridFlux runner as the single data-path implementation. Add a thin matrix scheduler and a pure result summarizer around it; store independent cell directories under one matrix root. Add runner-owned environment and cleanup audit records rather than relying on shell history.

**Tech Stack:** Python 3.11 runtime, existing CPSS/GridFlux Python/C++ binaries, pytest 9.1.1, CSV/JSON result artifacts, SSH/rsync deployment.

## Global Constraints

- Shenzhen is the controller/sender; Shanghai is the receiver.
- Keep protocol `v1.0-frozen`, 128 MiB block, dedup off, CRC32C, GridFlux connections=2, file-parallelism=2.
- Keep 100 Mbps as the declared protocol model; record network measurements separately.
- Do not delete failed results or mix preflight/pilot output into the formal matrix root.
- Use `pytest tests` for CPSS regression tests; do not collect unrelated GridFlux benchmark tests.

### Task 1: Make formal CPSS sampling seed-correct and auditable

**Files:**
- Modify: `Script/run_dcc_formal_gridflux.py`
- Modify: `tests/test_dcc_formal_runner.py`
- Modify: `docs/DCC_FORMAL_RUNNER.md`

**Interfaces:**
- `calibrate_fixed_pipeline(..., sampling_mode="random_sampling", seed=self.args.random_seed, ...)` is the only CPSS calibration call in the formal runner.
- `method_metrics.csv` gains `sampling_mode`, `sampling_seed`, and `sample_count` fields.
- Each result directory gains `environment_manifest.json` and `cleanup_audit.json`.

- [x] Write tests that assert the runner source uses random calibration and that audit/metadata helpers produce the required fields.
- [x] Run the focused tests and observe the expected failure against the current fixed-sampling implementation.
- [x] Implement the smallest runner change: pass the seed, copy calibration metadata into the row, capture environment hashes/versions, and append cleanup verification records.
- [x] Run `pytest tests/test_dcc_formal_runner.py tests/test_cpss_sampling_matrix.py` and then `pytest tests` (`64 passed`).

### Task 2: Add deterministic sampling-matrix launcher

**Files:**
- Create: `Script/run_dcc_formal_matrix.py`
- Create: `tests/test_dcc_formal_matrix.py`
- Create: `requirements-dcc-experiment.txt`

**Interfaces:**
- `build_matrix_cells(sample_counts, seeds, run_prefix) -> list[dict[str, object]]` returns ordered unique cells with `sample_count`, `seed`, and `run_id`.
- CLI accepts repeatable `--dataset`, comma-separated `--sample-counts`, comma-separated `--seeds`, `--methods`, `--repeats`, `--result-root`, `--dry-run`, and `--continue-on-error`.
- A dry run writes/prints a deterministic `matrix_manifest.json`; execution writes `matrix_status.json` and one child result directory per cell.

- [x] Write tests for exact 25-cell coverage, stable ordering, duplicate rejection, and dry-run manifest shape.
- [x] Run focused tests and observe failure because the launcher does not exist.
- [x] Implement sequential subprocess dispatch; default methods are `cpss-adaptive` (the runner always adds Raw), while explicit methods remain supported.
- [x] Run the launcher in dry-run mode against the smoke source and validate the manifest (`25` cells).

### Task 3: Add protocol-aware matrix summarizer

**Files:**
- Create: `Script/summarize_dcc_formal_matrix.py`
- Create: `tests/test_dcc_matrix_summary.py`
- Modify: `docs/DCC_FORMAL_RUNNER.md`

**Interfaces:**
- `summarize_matrix(result_root) -> dict[str, object]` reads child `method_metrics.csv` and returns grouped summaries.
- Group keys are `sample_count`, `seed`, and `method`.
- Each group reports `total_time_speedup`, `geomean_speedup`, `eligible_rows`, `failed_rows`, `raw_fallback_rate`, and `mean_probe_inclusive_regret`.
- CLI writes `matrix_summary.csv`, `matrix_summary.json`, and `matrix_failures.csv`.

- [x] Write fixture-based tests proving ratio-of-sums and geometric mean formulas, failed-row exclusion, and fallback counting.
- [x] Run tests and observe failure because the summarizer does not exist.
- [x] Implement pure CSV/JSON aggregation with explicit empty-group and invalid-value handling.
- [x] Run the summarizer against the preflight directory and synthetic fixtures; final preflight produced `3` groups and `0` failures.

### Task 4: Deploy, normalize test tooling, and run final preflight

**Files:**
- Modify remotely: `/root/projects/CPSS(DCC)/` through the Shenzhen sync script.
- Record: `ExperimentRecords/dcc_preflight_<date>/` and environment manifests.

**Interfaces:**
- Shanghai has `pytest==9.1.1` in `/opt/gridflux-cpss-venv`.
- Source and GridFlux binary hashes match after sync.

- [x] Install the pinned pytest version on Shanghai and run `pip check` on both nodes.
- [x] Run Shenzhen and Shanghai `pytest tests` (`64 passed` on each) plus compilation/import checks.
- [x] Run matrix launcher dry-run and summarizer tests on Shenzhen.
- [x] Run a real cross-node preflight with Raw, Zstd, and CPSS; all rows completed with roundtrip verification, cleanup audit, environment manifest, and no listener residue.
- [x] Record the measured two-flow `iperf3` evidence separately from the frozen 100 Mbps protocol parameter.
