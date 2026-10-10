#!/usr/bin/env python3
"""Run a paired CPSS/common-codec-pool comparison on selected DCC workloads.

The CPSS probe is given the same four codec families used by the MLcomp-like
pilot (Zstandard, LZ4, zlib, and XZ).  Fixed codec rows are retained so the
experiment measures end-to-end effects rather than only compression ratio.
This is a compatibility-pool comparison, not a reimplementation of MLcomp's
SFS+1NN selector; the distinction is recorded in the result directory.
"""

from __future__ import annotations

import argparse

from dcc_experiment_driver import add_common_arguments, execute


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.set_defaults(
        datasets="cesm_atm,miranda,noaa_gsod_csv",
        methods="raw,gzip-6,lz4,zstd-3,xz-6,cpss-adaptive",
        probe_candidates="zstd,lz4,zlib,xz_lzma2",
        repeats=3,
        bandwidth_mbps=100.0,
        adaptive_max_samples=2,
    )
    args = parser.parse_args()
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())

