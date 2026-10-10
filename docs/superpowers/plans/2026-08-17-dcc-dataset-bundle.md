# DCC Full-v1 Dataset Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify a standardized 12-dataset staging tree ready for upload to `oss://science-compressor-datasets/DCC/full-v1/`.

**Architecture:** A Python builder streams each source tar archive into a deterministic Zstandard-compressed tar with a common internal layout and records content hashes while copying. A standalone preparation tool verifies and extracts selected archives according to the generated root manifest.

**Tech Stack:** Python 3.12, standard-library `tarfile`/`hashlib`/`csv`/`json`, and `zstandard`.

## Global Constraints

- Do not upload, overwrite, copy, or delete OSS payload objects during bundle generation.
- Preserve raw numerical bytes exactly; do not cast, reshape, or concatenate typed fields.
- Use little-endian metadata for all seven numerical sources.
- Store one independently downloadable `.tar.zst` per dataset.
- Reject unsafe archive paths and typed payload-size mismatches.

---

### Task 1: Archive Contract

**Files:**
- Create: `tests/dcc_bundle/test_dcc_bundle.py`
- Create: `Script/build_dcc_dataset_bundle.py`

**Interfaces:**
- Produces: `DatasetSpec`, `safe_member_basename()`, `expected_field_bytes()`, and `build_dataset_archive()`.

- [ ] Write tests that specify safe path handling, typed-size validation, and the standardized archive layout.
- [ ] Run `python -m pytest tests/dcc_bundle/test_dcc_bundle.py -q` and confirm import failure before implementation.
- [ ] Implement the minimum streaming archive builder.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Preparation Tool

**Files:**
- Create: `Script/prepare_dcc_datasets.py`
- Modify: `tests/dcc_bundle/test_dcc_bundle.py`

**Interfaces:**
- Consumes: root `metadata/datasets.json` and `archives/<slug>.tar.zst`.
- Produces: verified `datasets/<slug>/metadata.json` and `datasets/<slug>/raw/*`.

- [ ] Add a failing round-trip extraction and checksum test.
- [ ] Implement safe extraction and member verification.
- [ ] Run the focused test and confirm it passes.

### Task 3: Full Bundle Generation

**Files:**
- Create: `E:\Dataset\DCC_upload\full-v1\archives\*.tar.zst`
- Create: `E:\Dataset\DCC_upload\full-v1\metadata\datasets.json`
- Create: `E:\Dataset\DCC_upload\full-v1\metadata\datasets.csv`
- Create: `E:\Dataset\DCC_upload\full-v1\metadata\SHA256SUMS`
- Create: `E:\Dataset\DCC_upload\full-v1\tools\prepare_datasets.py`
- Create: `E:\Dataset\DCC_upload\full-v1\README.md`

**Interfaces:**
- Consumes: the seven numeric archives under `E:\Dataset\HPC_Dataset` and five frozen text archives under `E:\Dataset\实验数据集`.
- Produces: a directly uploadable directory tree.

- [ ] Run the builder with `--output E:\Dataset\DCC_upload\full-v1`.
- [ ] Confirm all 12 dataset records report `status=complete`.
- [ ] Confirm every typed member matches its declared dtype and shape.

### Task 4: End-to-End Verification

**Files:**
- Read: all generated files under `E:\Dataset\DCC_upload\full-v1`.

- [ ] Run the complete focused test suite.
- [ ] Run the preparation tool with `--list`.
- [ ] Smoke-extract one numeric and one byte-stream archive into a temporary directory.
- [ ] Verify archive and raw-member SHA-256 values, then remove only the temporary smoke directory.
- [ ] Print the exact resumable `ossutil cp` command without starting the upload.
