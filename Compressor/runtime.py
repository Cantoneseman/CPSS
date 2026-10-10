from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT_DIR / "Script"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pipeline_registry import (  # noqa: E402
    ComponentUnavailable,
    COMPRESSOR_FUNCS,
    FILTER_FUNCS,
    PipelineContext,
    canonical_name,
    element_size,
    guess_dtype,
    guess_shape,
    normalize_context,
    parse_pipeline_name,
)


KNOWN_TYPED_DTYPES = {
    "uint8",
    "uint16",
    "uint32",
    "uint64",
    "int8",
    "int16",
    "int32",
    "int64",
    "float32",
    "float64",
}


def canonical_pipeline_name(name: str) -> str:
    parts = [canonical_name(part) for part in str(name).split("->") if part.strip()]
    return "->".join(parts)


def build_context_for_file(
    file_path: Path,
    raw: bytes,
    *,
    dtype_name: str | None = None,
    shape: tuple[int, ...] | None = None,
    endian: str = "little",
    scenario: str = "science",
    source_group: str = "",
) -> tuple[PipelineContext, dict]:
    dtype_name = canonical_name(dtype_name) if dtype_name else guess_dtype(file_path.suffix)
    typesize = element_size(dtype_name)
    used_size = len(raw)
    shape_source = "user_override" if shape else ("byte_fallback" if dtype_name is None else "1d_fallback")

    if typesize and typesize > 1:
        used_size = len(raw) - (len(raw) % typesize)
    if shape is None and dtype_name is not None:
        inferred_shape, guessed_source = guess_shape(
            file_path=file_path,
            dtype_name=dtype_name,
            dataset_name=file_path.parent.name or "unknown",
            used_size=used_size,
        )
        if inferred_shape is not None:
            shape = tuple(int(item) for item in inferred_shape)
            shape_source = guessed_source
    if shape is None:
        shape = (used_size if used_size > 0 else len(raw),)

    ctx = PipelineContext(
        dtype_name=dtype_name,
        shape=tuple(shape),
        typesize=typesize or 1,
        endian=endian,
        metadata={"scenario": scenario, "source_group": source_group},
    )
    ctx = normalize_context(ctx, raw)
    metadata = {
        "dtype_name": ctx.dtype_name,
        "shape": list(ctx.shape) if ctx.shape is not None else None,
        "typesize": ctx.typesize,
        "endian": ctx.endian,
        "shape_source": shape_source,
        "original_suffix": file_path.suffix,
        "aligned_size": used_size,
        "element_count": used_size // max(1, ctx.typesize or 1),
        "scenario": scenario,
        "source_group": source_group,
        "format": file_path.suffix.lower().lstrip("."),
    }
    return ctx, metadata


def build_context_for_block(
    raw: bytes,
    *,
    dtype_name: str | None = None,
    full_shape: tuple[int, ...] | None = None,
    endian: str = "little",
    block_offset: int = 0,
    scenario: str = "science",
    source_group: str = "",
) -> tuple[PipelineContext, dict]:
    """Build a context whose shape describes only this contiguous block."""
    dtype_name = canonical_name(dtype_name) if dtype_name else None
    if dtype_name not in KNOWN_TYPED_DTYPES:
        dtype_name = None
    typesize = element_size(dtype_name) or 1
    aligned_size = len(raw) - (len(raw) % typesize) if typesize > 1 else len(raw)
    typed_eligible = bool(
        dtype_name
        and typesize > 1
        and int(block_offset) % typesize == 0
    )
    if not typed_eligible:
        context = PipelineContext(
            dtype_name=None,
            shape=(len(raw),),
            typesize=1,
            endian="little",
            metadata={"scenario": scenario, "format": "bin", "source_group": source_group},
        )
        return context, {
            "dtype_name": None,
            "source_dtype": dtype_name,
            "source_shape": list(full_shape) if full_shape else None,
            "shape": [len(raw)],
            "typesize": 1,
            "endian": "little",
            "shape_source": "byte_fallback",
            "block_offset_bytes": int(block_offset),
            "aligned_size": len(raw),
            "element_count": len(raw),
            "scenario": scenario,
            "source_group": source_group,
            "format": "bin",
        }

    element_count = aligned_size // typesize
    shape: tuple[int, ...] = (element_count,)
    shape_source = "block_1d"
    if full_shape and len(full_shape) > 1:
        row_elements = 1
        for dimension in full_shape[1:]:
            row_elements *= int(dimension)
        row_bytes = row_elements * typesize
        if row_bytes > 0 and int(block_offset) % row_bytes == 0 and aligned_size % row_bytes == 0:
            shape = (aligned_size // row_bytes, *tuple(int(item) for item in full_shape[1:]))
            shape_source = "block_slice"

    context = PipelineContext(
        dtype_name=dtype_name,
        shape=shape,
        typesize=typesize,
        endian=endian if endian in {"little", "big"} else "little",
        metadata={"scenario": scenario, "format": "bin", "source_group": source_group},
    )
    context = normalize_context(context, raw)
    return context, {
        "dtype_name": context.dtype_name,
        "source_dtype": context.dtype_name,
        "source_shape": list(full_shape) if full_shape else None,
        "shape": list(context.shape) if context.shape is not None else None,
        "typesize": context.typesize,
        "endian": context.endian,
        "shape_source": shape_source,
        "block_offset_bytes": int(block_offset),
        "aligned_size": aligned_size,
        "element_count": element_count,
        "scenario": scenario,
        "source_group": source_group,
        "format": "bin",
    }
