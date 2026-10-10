# CPSS

CPSS is a research prototype for cost-aware, lossless compression decisions in one-shot scientific data transfer. It treats compression as an end-to-end transfer action: bounded probing estimates candidate local work and wire bytes, then the runtime chooses a codec or a direct Raw path before the receiver reconstructs the original directory tree.

## Repository layout

- `Compressor/`: executable `.cpss` container, codec selection, compression, decompression, and reconstruction logic.
- `Script/`: dataset preparation, candidate-pipeline evaluation, CPSS experiment drivers, analysis, and figure-generation scripts.
- `tests/`: unit and integration tests for containers, reversible transforms, selectors, manifests, sampling, and pipeline execution.
- `docs/`: design notes, experiment protocols, and the DCC manuscript sources.
- `requirements.txt`: Python dependencies used by the prototype and analysis scripts.

Large datasets, experiment records, caches, and compiled artifacts are intentionally excluded from this source repository. The DCC manuscript's editable draw.io figures and LaTeX sources are kept under `docs/dcc_latex/cpss_submission/`.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m Compressor.cli check-env
python -m Compressor.cli compress .\example.bin
python -m Compressor.cli inspect .\example.bin.cpss
python -m Compressor.cli decompress .\example.bin.cpss
```

For a typed scientific array, provide metadata when available:

```powershell
python -m Compressor.cli compress .\field.f32 --dtype float32 --shape 100,500,500
```

The runtime falls back to an executable exhaustive selector when optional model or codec dependencies are unavailable. All supported paths preserve the original bytes; the test suite verifies round-trip reconstruction.

## Reproducibility

The scripts under `Script/` expect datasets to be supplied separately. Follow the preparation and experiment protocols in `DCC_FINAL_EXPERIMENT_PROTOCOL.md` and the design notes under `docs/`. Do not commit local datasets or generated experiment directories.

## License and status

This repository is a research prototype accompanying the CPSS work. It is provided for reproducibility and review while the project is under active development. A formal open-source license can be added before public release.
