"""Small, codec-independent features used by CPSS similar-block lookup."""

from __future__ import annotations

import math
import zlib
from typing import Any

import numpy as np


FEATURE_VERSION = 1
_WINDOW_BYTES = 64 * 1024
_WINDOW_COUNT = 4


def extract_decision_features(raw: bytes) -> dict[str, float]:
    """Summarize evenly spaced byte windows without running CPSS candidates."""
    if not raw:
        return {
            "entropy_bits": 0.0,
            "printable_fraction": 0.0,
            "zero_fraction": 0.0,
            "same_adjacent_fraction": 0.0,
            "gzip_ratio": 1.0,
        }
    if len(raw) <= _WINDOW_BYTES * _WINDOW_COUNT:
        sample = raw
    else:
        max_start = len(raw) - _WINDOW_BYTES
        offsets = [round(index * max_start / (_WINDOW_COUNT - 1)) for index in range(_WINDOW_COUNT)]
        sample = b"".join(raw[offset:offset + _WINDOW_BYTES] for offset in offsets)

    values = np.frombuffer(sample, dtype=np.uint8)
    histogram = np.bincount(values, minlength=256).astype(np.float64)
    probabilities = histogram[histogram > 0] / max(1, len(values))
    entropy = float(-(probabilities * np.log2(probabilities)).sum())
    printable = ((values >= 32) & (values <= 126)) | (values == 9) | (values == 10) | (values == 13)
    if len(values) > 1:
        same_adjacent = float(np.count_nonzero(values[1:] == values[:-1]) / (len(values) - 1))
    else:
        same_adjacent = 0.0
    compressed_size = len(zlib.compress(sample, level=1))
    return {
        "entropy_bits": entropy,
        "printable_fraction": float(np.count_nonzero(printable) / len(values)),
        "zero_fraction": float(histogram[0] / len(values)),
        "same_adjacent_fraction": same_adjacent,
        "gzip_ratio": float(len(sample) / max(1, compressed_size)),
    }


def decision_feature_bucket(features: dict[str, Any]) -> str:
    """Return a coarse, indexed bucket; lookup applies a finer distance gate."""
    entropy = max(0, min(15, int(float(features["entropy_bits"]) / 0.5)))
    printable = max(0, min(5, int(float(features["printable_fraction"]) / 0.2)))
    zero = max(0, min(10, int(float(features["zero_fraction"]) / 0.1)))
    return f"decision-features-v{FEATURE_VERSION}:e{entropy}:p{printable}:z{zero}"


def feature_distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    scales = {
        "entropy_bits": 0.5,
        "printable_fraction": 0.2,
        "zero_fraction": 0.1,
        "same_adjacent_fraction": 0.1,
        # Compression ratio naturally moves with a small amount of noise; use
        # a wider scale so one changed byte does not make a near duplicate a
        # miss, while entropy/printability still dominate the gate.
        "gzip_ratio": 10.0,
    }
    squared = [
        ((float(left[name]) - float(right[name])) / scale) ** 2
        for name, scale in scales.items()
    ]
    return math.sqrt(sum(squared) / len(squared))
