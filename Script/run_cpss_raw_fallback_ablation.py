#!/usr/bin/env python3
"""Run the paired raw/fixed-Zstd/CPSS fallback ablation.

The default workloads are two large datasets for which the existing CPSS run
selected Raw (HACC and NYX); together they stay below the 10-GiB follow-up
budget while still exercising the fallback branch.  The fixed ``zstd-3``
row is the forced-compression stress baseline: it always pays the compression
path, whereas CPSS can take the direct Raw path.  This script does not label
the fixed row as a new CPSS policy.
"""

from __future__ import annotations

import argparse

from dcc_experiment_driver import add_common_arguments, execute


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.set_defaults(
        datasets="hacc,nyx",
        methods="raw,zstd-3,cpss-adaptive",
        probe_candidates="fast",
        repeats=3,
        bandwidth_mbps=100.0,
        adaptive_max_samples=2,
    )
    args = parser.parse_args()
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
