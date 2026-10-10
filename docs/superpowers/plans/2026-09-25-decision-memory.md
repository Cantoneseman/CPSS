# CPSS decision memory implementation

## Goal

Add a persistent decision memory around the per-block exhaustive CPSS path, then add a conservative similar-block lookup only after the exact-memory path is verified.

## Scope

- Target `Compressor/cpss_sampling_matrix.py` and `Script/run_cpss_sampling_dedup_matrix.py`, where `native_exhaustive` evaluates every candidate for every block.
- Preserve exhaustive selection as the correctness fallback.
- Use SQLite as the durable source of truth.
- Version 1: exact block-hash lookup only.
- Version 2: feature-bucket lookup with a confidence threshold; no ANN dependency in the first implementation.

## Non-goals

- Do not cache compressed payloads.
- Do not alter the frozen candidate set, bandwidth, or experiment definitions.
- Do not change fixed/random sampling semantics.
- Do not claim similar-block benefit until a held-out experiment measures it.

## Acceptance criteria

1. Empty memory produces byte-identical decompression and the same selected pipeline as exhaustive.
2. An exact hit skips candidate compression trials and still roundtrips successfully.
3. Memory entries are invalidated by block size, candidate set, objective, codec/runtime schema, and typed context changes.
4. SQLite failures or malformed entries fall back to exhaustive.
5. Version 1 records cold/warm hit counts, candidate calls, and timings in an experiment report.
6. Version 2 only reuses a similar decision when the compatible neighbor set agrees above a threshold; otherwise it abstains and runs exhaustive.
7. All new behavior has focused tests and the existing test suite remains green.

## Implementation order

1. Add `Compressor/decision_memory.py` with schema, key fingerprinting, exact lookup, and atomic writes.
2. Add failing tests for persistence, invalidation, fallback, and exact-hit behavior.
3. Integrate the exact layer into `make_native_exhaustive_block_compressor` and expose a memory path option in the legacy matrix runner.
4. Run focused tests, then the full suite and a local repeated-block benchmark.
5. Add version-2 feature extraction, compatibility buckets, consensus lookup, and counters.
6. Run held-out similar-block tests; only retain the feature path if it is correct and measurably reduces exhaustive calls.

