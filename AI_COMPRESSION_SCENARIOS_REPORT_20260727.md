# AI Compression Scenario Experiment

- Status: `completed`
- Started: `2026-07-27T16:54:44+0800`
- Finished: `2026-07-27T17:06:09+0800`
- Interpretation: `llm_text` compresses LLM training corpora, not model weights.

## Environment

| dependency | version/status |
|---|---|
| Python | `3.10.12` |
| torch | `2.13.0+cu130`, CUDA unavailable, CPU run |
| numpy | `2.2.6` |
| zstandard | `0.25.0` |
| brotli | `1.2.0` |
| python-snappy | `0.7.3` |
| ossutil | `v1.7.19` |
| zstd CLI | `1.4.8` |
| GNU tar | `1.34` |
| unzip | `6.00` |

## OSS Objects

| name | OSS URI | size bytes | Last-Modified | ETag | SHA256 |
|---|---|---:|---|---|---|
| `openorca` | `oss://science-compressor-datasets/AI Dataset/model/ai_openorca_1m_gpt4_augmented.tar.zst` | 589550375 | `2026-07-27 15:23:43 +0800 CST` | `9276FE6D9BD760EA5239A2A822379B49-141` | `8afe31c8641675e9c970b9a9e93134bcc0fc175bcb0131bb1673aa4c0f800887` |
| `slimorca` | `oss://science-compressor-datasets/AI Dataset/model/ai_slimorca_oo_labeled_correct_gpt4.tar.zst` | 282158874 | `2026-07-27 15:24:21 +0800 CST` | `630E7B81FDF69AC63EF512F2D90DFE63-68` | `71b4e1c2167ccb659448687296bfa4e558238a295f2ffa53d83e3b29733de3b7` |
| `wikitext` | `oss://science-compressor-datasets/AI Dataset/model/ai_wikitext103_raw_v1.tar.zst` | 191991093 | `2026-07-27 15:26:49 +0800 CST` | `18F0AA85C1E2D4EB309473AF1ED5918A-46` | `68a929a2032fc85c43d2377e425a1665c0b15ed9db2222da4d6227a5b7ef5890` |
| `metadata` | `oss://science-compressor-datasets/AI Dataset/Metadata_Training/metadata_training_20260727.tar.zst` | 8999331143 | `2026-07-27 15:36:12 +0800 CST` | `06A7064D093F1CC3A7EBDFA4FCB3768A-391` | `98fe8ddeed81d7d6ce5dfb70630955ca36544505c31e5870c210927ace64eb80` |

## Data Preparation

- All OSS objects were downloaded, verified with `zstd -t`, extracted with `tar --zstd -xf`, and WikiText ZIP was unpacked before chunking.
- Outer `.tar.zst` files were treated only as packaging. Compression comparisons used extracted file chunks only.
- The CPU-only run uses bounded representative chunks from each extracted file; chunk offsets and SHA256 values were generated in the temporary manifests before cleanup.
- LLM external validation used WikiText only. Metadata external validation used `12_embedding_metadata` only, held out from training.
- Chunk target: `4194304` bytes; sampled max chunks per extracted file: `2`; fixed split seed: `20260727`; labeling and training workers: `1`.

| dataset | chunks | sampled bytes | groups |
|---|---:|---:|---|
| `llm_text_train_internal` | 4 | 16771835 | openorca_1m:2, slimorca:2 |
| `llm_text_wikitext_external` | 4 | 10824680 | wikitext:4 |
| `metadata_train_internal` | 12 | 50331335 | laion:6, openimages:6 |
| `metadata_embedding_external` | 24 | 100663296 | embedding:24 |

## Candidate Pipelines

- available candidate count: `8`
- candidates: `brotli`, `bzip2`, `delta_length_byte_array->xz_lzma2`, `libdeflate`, `snappy`, `xz_lzma2`, `zlib`, `zstd`

### Preflight Failures

- `shuffle->xz_lzma2`: filter 'shuffle' failed: typesize must be >= 2
- `tdt->brotli`: filter 'tdt' failed: typesize must be >= 2
- `tdt->xz_lzma2`: filter 'tdt' failed: typesize must be >= 2

## Training

### `llm_text`

- status: `trained`
- train rows: `2`
- internal validation rows: `2`
- internal accuracy: `1.0`
- internal relative compression: `1.0`
- model directory: `/root/projects/science-compressor/Model/scenarios/llm_text`

### `metadata`

- status: `trained`
- train rows: `10`
- internal validation rows: `2`
- internal accuracy: `1.0`
- internal relative compression: `1.0`
- model directory: `/root/projects/science-compressor/Model/scenarios/metadata`

## External Validation

### `llm_text`

| method | chunks | ratio | relative/oracle | lossless |
|---|---:|---:|---:|---:|
| `model` | 4 | 3.5842 | 0.9822 | 1.0000 |
| `hybrid` | 4 | 3.5842 | 0.9822 | 1.0000 |
| `exhaustive` | 4 | 3.6139 | 1.0000 | 1.0000 |
| `zstd` | 4 | 2.8758 | 0.7979 | 1.0000 |
| `xz_lzma2` | 4 | 3.5843 | 0.9822 | 1.0000 |
| `brotli` | 4 | 3.1501 | 0.8732 | 1.0000 |
| `existing_model` | 4 | 3.5842 | 0.9822 | 1.0000 |

### `metadata`

| method | chunks | ratio | relative/oracle | lossless |
|---|---:|---:|---:|---:|
| `model` | 24 | 1.3488 | 0.9839 | 1.0000 |
| `hybrid` | 24 | 1.3488 | 0.9839 | 1.0000 |
| `exhaustive` | 24 | 1.3711 | 1.0000 | 1.0000 |
| `zstd` | 24 | 1.1936 | 0.8706 | 1.0000 |
| `xz_lzma2` | 24 | 1.3488 | 0.9839 | 1.0000 |
| `brotli` | 24 | 1.2011 | 0.8760 | 1.0000 |
| `existing_model` | 24 | 1.3488 | 0.9839 | 1.0000 |

## Acceptance

- `llm_text`: `完成`; model external lossless rate `1.0000`, model/oracle efficiency `0.9822`.
- `llm_text` vs fixed `zstd`: `有改善`, compression ratio `3.5842` vs `2.8758` (about `24.6%` higher).
- `metadata`: `完成`; model external lossless rate `1.0000`, model/oracle efficiency `0.9839`.
- `metadata` vs fixed `zstd`: `有改善`, compression ratio `1.3488` vs `1.1936` (about `13.0%` higher).
- Both scenario models match the pre-existing generic model on this sampled external set; no additional improvement over `existing_model` was observed.
- Successful external validation chunks restored byte-for-byte. Training and external validation sources are disjoint by construction.
- Resource limitation: this was a bounded CPU validation using at most two chunks per extracted file, not a full-corpus performance sweep.
- Full repository test suite: `15 passed, 1 failed`; the failure is an unrelated missing historical `ExperimentRecords/.../SHENZHEN_CODEX_PROMPT.md`. New scenario tests: `4 passed`.

## Cleanup

- cleanup status: `pass`
- disposable run directory, downloaded archives, extracted datasets, intermediate manifests, caches, logs, temporary CLI files, and temporary virtual environment are removed.
- final audit found no `science-compressor-ai-*` temporary directory, `.part` file, `.lock` file, or experiment process.
- OSS source objects and the pre-existing generic model are not modified.
