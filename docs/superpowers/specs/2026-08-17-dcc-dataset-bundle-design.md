# DCC Full-v1 Dataset Bundle Design

## Goal

Create one reproducible OSS dataset collection for the DCC system-paper experiments. The collection contains seven typed scientific datasets and five non-duplicated byte-stream datasets. Every dataset is independently downloadable and extracts into the same directory layout.

## Scope

The collection contains CESM, Hurricane Isabel, Miranda, NYX, HACC, SCALE-LETKF, EXAFEL, RCSB mmCIF, NCBI GRCh38 FASTA, RedPajama JSONL, The Stack JSONL, and NOAA GSOD CSV.

OpenOrca, SlimOrca, WikiText, metadata-training artifacts, duplicate AlphaFold samples, duplicate RCSB samples, download caches, and source archives that are not selected for the paper are excluded.

## OSS And Local Layout

The local staging root is `E:\Dataset\DCC_upload\full-v1`. It maps directly to `oss://science-compressor-datasets/DCC/full-v1/`.

```text
full-v1/
  archives/<slug>.tar.zst
  metadata/datasets.json
  metadata/datasets.csv
  metadata/SHA256SUMS
  tools/prepare_datasets.py
  requirements.txt
  README.md
```

Every dataset archive contains:

```text
<slug>/metadata.json
<slug>/raw/<original-payload-name>
```

Only raw payload files are stored below `raw/`. Source container directories and source compression wrappers are not exposed to experiments.

## Metadata Contract

Each root-manifest dataset record contains a stable slug, display name, domain, category, source filename, output object key, archive SHA-256, archive byte size, raw byte size, raw file count, and member-level SHA-256 values.

Typed numerical records additionally contain `dtype`, `shape`, and `endian`. Shapes describe each raw field, not the aggregate archive. Byte-stream datasets set those fields to null and must not be passed to typed-only codecs.

## Preparation And Verification

`tools/prepare_datasets.py` lists datasets, selects one or more slugs, verifies the archive SHA-256, safely extracts the archive, and verifies each raw member's byte size and SHA-256. Unsafe absolute or parent-traversal archive members are rejected.

The upload is intentionally not started by the build process. The deliverable includes one resumable `ossutil cp` command so the user controls the long-running external mutation.

## Failure Handling

The builder writes every archive to a `.partial` file and atomically renames it only after success. Existing complete outputs with matching manifest hashes may be reused; invalid or incomplete outputs fail closed. Numerical members whose size does not equal `product(shape) * dtype_size` abort the build.

## Verification

Automated tests cover safe path handling, typed-size validation, standardized archive construction, extraction, and checksum verification. Final verification checks all 12 archive hashes, root-manifest consistency, expected dtype/shape values, and a smoke extraction from one numerical and one byte-stream dataset.
