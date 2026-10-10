# CPSS P0 Repair Report

## Modified Files

- `Compressor/pipeline_runner.py`
- `Compressor/cpss_sampling_matrix.py`
- `Compressor/runtime.py`
- `Compressor/reversible.py`
- `Script/pipeline_registry.py`
- `Script/run_cpss_pipeline.py`
- `Script/run_cpss_sampling_dedup_matrix.py`
- `tests/test_cpss_sampling_matrix.py`
- `tests/test_pipeline_runner.py`

## New Metric Definitions

- `transfer_goodput_Bps = original_bytes / transfer_wall_clock_s`
- `e2e_goodput_Bps = original_bytes / e2e_wall_clock_s`
- `e2e_speedup_vs_raw = raw_e2e_wall_clock_s / method_e2e_wall_clock_s`
- `byte_reduction_ratio = original_bytes / transferred_bytes`

Cold E2E includes sampling/calibration. Warm E2E reuses the decision and excludes probe cost.

## Oracle Definitions

- `full_data_global_system_oracle`: every candidate pipeline is evaluated on the full evaluation set, but one global fixed method is selected by estimated system cost.
- `per_block_exhaustive_oracle`: block-wise exhaustive selection, kept only as a theoretical upper bound and labeled explicitly as such.

System cost:

`T(m) = T_compress(m) + compressed_bytes(m) / bandwidth_Bps + T_decompress(m)`

`bandwidth_Bps` is never hard-coded. It is taken from CLI/configuration or measured from the raw reference path.

## Raw Bypass Logic

- Raw is a legal action.
- If the sampled path is not predicted to beat raw under the configured guard, the task bypasses CPSS and uses the raw GridFlux path.
- Recorded fields:
  - `selected_action`
  - `predicted_raw_e2e`
  - `predicted_compressed_e2e`
  - `bypass_reason`
  - `probe_time_s`
  - `fallback_overhead_s`

The bypass still pays probe cost and does not claim to be strictly faster than raw.

## dtype / shape / endian Propagation

Propagation path:

`E:\Dataset\DCC_upload\full-v1\metadata\datasets.json` or CLI overrides -> `PipelineRunConfig` -> `_effective_data_metadata()` -> `_block_context()` / `build_context_for_block()` -> `compress_with_pipeline()` -> artifact metadata -> `.cpss` bundle metadata -> `rebuild_from_bundle()`

Rules implemented:

- `float32`, `float64`, `uint16`, and other typed scientific inputs keep their `typesize` and `endian`.
- Block shapes are block-local and legal for the block byte count.
- Non-aligned tail bytes stay in the CPSS tail field unchanged.
- Text / unknown inputs fall back to byte-stream context.
- Zero-length aligned prefixes are handled explicitly so tail-only blocks still roundtrip.

## Tests

Full pytest:

- `57 passed, 1 skipped`

Targeted additions:

- system-cost calibration selection
- wall-clock metric semantics
- raw bypass selection
- typed metadata propagation
- float32 / float64 / uint16 / byte-stream / non-aligned tail roundtrip

## Smoke Test

Local smoke directory:

`ExperimentRecords/p0_smoke_20260818_144650`

Cases:

- compressible numeric `float32`: `selected_action=compress`
- high-entropy data: `selected_action=raw_bypass`
- text data: `selected_action=compress`

All three roundtripped successfully.

## Remaining Risks

- Legacy GridFlux matrix now matches the new field vocabulary, but it still needs a real remote-scale run to validate wall-clock estimation on the full network path.
- Receiver-side decompression profiling is still configurable rather than fully auto-discovered.
- The measured bandwidth proxy in smoke tests is local and not a substitute for the formal GridFlux deployment path.

## Formal Experiment Readiness

Yes, the system is now ready to design the formal DCC experiment protocol.
