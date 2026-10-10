# CPSS/DCC 后续实验启动清单

以下命令假定深圳服务器上的项目目录为 `/root/projects/CPSS(DCC)`，虚拟环境解释器为 `/opt/gridflux-cpss-venv/bin/python3`。先把本目录 `Script/` 下的 5 个新文件复制到服务器项目的 `Script/` 目录：

```text
dcc_experiment_driver.py
run_dcc_codec_pool_experiment.py
run_cpss_sampling_budget_experiment.py
run_cpss_raw_fallback_ablation.py
run_cpss_bandwidth_experiment.py
```

## 0. 先做 dry-run

```bash
cd '/root/projects/CPSS(DCC)'
P='/root/projects/CPSS(DCC)'
PY='/opt/gridflux-cpss-venv/bin/python3'
DROOT="$P/DCCData/full-v1/datasets"

$PY Script/run_dcc_codec_pool_experiment.py \
  --project-root "$P" \
  --dataset-root "$DROOT" \
  --datasets cesm_atm,miranda,noaa_gsod_csv \
  --result-dir "$P/ExperimentRecords/_dryrun_codec_pool" \
  --run-id dryrun_codec_pool \
  --dry-run
```

输出应包含 3 个数据集、6 个方法和 100 Mbps；确认无误后再启动正式命令。所有脚本都拒绝覆盖非空结果目录。

## 1. 与 MLcomp 兼容的共同候选池

这是端到端对照：固定 Raw/Gzip/LZ4/Zstd/XZ 与 CPSS 同时测量，CPSS 探测池固定为 `zstd,lz4,zlib,xz_lzma2`。它回答“候选池扩大到 MLcomp-like 的四种 codec 后，系统级决策是否仍有收益”。它不是 MLcomp 的 SFS+1NN 重实现；已有的机制级 1NN 小实验仍由 `Script/run_mlcomp_like_small.py` 负责。

```bash
cd '/root/projects/CPSS(DCC)'
P='/root/projects/CPSS(DCC)'
PY='/opt/gridflux-cpss-venv/bin/python3'
DROOT="$P/DCCData/full-v1/datasets"
OUT="$P/ExperimentRecords/dcc_codec_pool_20261002"

nohup $PY Script/run_dcc_codec_pool_experiment.py \
  --project-root "$P" \
  --dataset-root "$DROOT" \
  --datasets cesm_atm,miranda,noaa_gsod_csv \
  --result-dir "$OUT" \
  --run-id dcc_codec_pool_20261002 \
  --bandwidth-mbps 100 \
  --repeats 3 \
  --methods raw,gzip-6,lz4,zstd-3,xz-6,cpss-adaptive \
  --probe-candidates zstd,lz4,zlib,xz_lzma2 \
  > "$OUT.nohup.log" 2>&1 &
echo $! > "$OUT.pid"
```

规模为 54 行（3 数据集 × 6 方法 × 3 重复）。沿用现有全量数据时预计约 **2--4 小时**；XZ 的编码时间可能使上界增大。

## 2. CPSS 采样预算曲线

当前服务器 runner 明确支持采样上限 1、2、5；脚本拒绝 10/20，避免把未实现的预算写进论文。推荐先在相同的 128 MiB 子集上运行。先准备子集（只做一次）：

```bash
cd '/root/projects/CPSS(DCC)'
P='/root/projects/CPSS(DCC)'
PY='/opt/gridflux-cpss-venv/bin/python3'
DROOT="$P/DCCData/full-v1/datasets"
SUB="$P/ExperimentRecords/dcc_128m_common_subset_20261002"

$PY Script/run_dcc_10mbps_subset.py \
  --source-root "$DROOT" \
  --datasets cesm_atm,miranda,noaa_gsod_csv \
  --max-mib 128 \
  --repeats 3 \
  --bandwidth-mbps 10 \
  --result-dir "$SUB" \
  --prepare-only
```

准备完成后，把同一份 `input_subset` 交给预算矩阵：

```bash
cd '/root/projects/CPSS(DCC)'
P='/root/projects/CPSS(DCC)'
PY='/opt/gridflux-cpss-venv/bin/python3'
S="$P/ExperimentRecords/dcc_128m_common_subset_20261002/input_subset"
OUT="$P/ExperimentRecords/dcc_sampling_budget_20261002"

nohup $PY Script/run_cpss_sampling_budget_experiment.py \
  --project-root "$P" \
  --dataset "cesm_atm=$S/cesm_atm/raw" \
  --dataset "miranda=$S/miranda/raw" \
  --dataset "noaa_gsod_csv=$S/noaa_gsod_csv/raw" \
  --result-dir "$OUT" \
  --run-id dcc_sampling_budget_20261002 \
  --budgets 1,2,5 \
  --bandwidth-mbps 100 \
  --repeats 3 \
  --methods raw,zstd-3,cpss-adaptive \
  --probe-candidates fast \
  > "$OUT.nohup.log" 2>&1 &
echo $! > "$OUT.pid"
```

规模为 81 行（3 预算 × 3 数据集 × 3 方法 × 3 重复），总输入约 384 MiB，预计 **45--120 分钟**。每个预算写入 `budget_1/`、`budget_2/`、`budget_5/`，中断后只需补跑缺失预算。

## 3. Raw fallback 消融

默认使用 HACC 和 NYX；旧实验中 CPSS 在这两个工作负载上选择过 Raw，合计约 9.3 GiB，仍在 10 GiB 的补实验预算内。固定 `zstd-3` 行是“始终走压缩路径”的压力基线，不把它命名成另一种 CPSS 策略。

```bash
cd '/root/projects/CPSS(DCC)'
P='/root/projects/CPSS(DCC)'
PY='/opt/gridflux-cpss-venv/bin/python3'
DROOT="$P/DCCData/full-v1/datasets"
OUT="$P/ExperimentRecords/dcc_raw_fallback_ablation_20261002"

nohup $PY Script/run_cpss_raw_fallback_ablation.py \
  --project-root "$P" \
  --dataset "hacc=$DROOT/hacc/raw" \
  --dataset "nyx=$DROOT/nyx/raw" \
  --result-dir "$OUT" \
  --run-id dcc_raw_fallback_ablation_20261002 \
  --bandwidth-mbps 100 \
  --repeats 3 \
  --methods raw,zstd-3,cpss-adaptive \
  --probe-candidates fast \
  > "$OUT.nohup.log" 2>&1 &
echo $! > "$OUT.pid"
```

规模为 18 行，预计 **2--4 小时**；如果只想快速验证逻辑，可将两个 `--dataset` 换成同一数据集的已准备小子集。

## 4. 10/100 Mbps 敏感度

此项直接复用仓库已有的 `run_dcc_10mbps_subset.py`，保证两个带宽使用同一份 128 MiB 输入。先跑 10 Mbps，它会创建子集；完成后再用 `--reuse-subset-from` 跑 100 Mbps。

```bash
cd '/root/projects/CPSS(DCC)'
P='/root/projects/CPSS(DCC)'
PY='/opt/gridflux-cpss-venv/bin/python3'
DROOT="$P/DCCData/full-v1/datasets"
B10="$P/ExperimentRecords/dcc_bandwidth_10mbps_20261002"

nohup $PY Script/run_dcc_10mbps_subset.py \
  --source-root "$DROOT" \
  --datasets cesm_atm,miranda,noaa_gsod_csv \
  --max-mib 128 \
  --bandwidth-mbps 10 \
  --repeats 3 \
  --methods raw,zstd-3,cpss-adaptive \
  --result-dir "$B10" \
  --run-id dcc_bandwidth_10mbps_20261002 \
  > "$B10.nohup.log" 2>&1 &
echo $! > "$B10.pid"
```

10 Mbps 完成后：

```bash
cd '/root/projects/CPSS(DCC)'
P='/root/projects/CPSS(DCC)'
PY='/opt/gridflux-cpss-venv/bin/python3'
DROOT="$P/DCCData/full-v1/datasets"
B10="$P/ExperimentRecords/dcc_bandwidth_10mbps_20261002"
B100="$P/ExperimentRecords/dcc_bandwidth_100mbps_20261002"

nohup $PY Script/run_dcc_10mbps_subset.py \
  --source-root "$DROOT" \
  --datasets cesm_atm,miranda,noaa_gsod_csv \
  --max-mib 128 \
  --bandwidth-mbps 100 \
  --repeats 3 \
  --methods raw,zstd-3,cpss-adaptive \
  --reuse-subset-from "$B10" \
  --result-dir "$B100" \
  --run-id dcc_bandwidth_100mbps_20261002 \
  > "$B100.nohup.log" 2>&1 &
echo $! > "$B100.pid"
```

每个带宽 27 行，单次预计 **15--40 分钟**，两次合计 **30--80 分钟**。这部分与已有 10/100 Mbps CSV 的口径一致，不引入 1 Gbps。

## 5. MLcomp-like 机制级小实验（可选）

已有脚本不走 GridFlux，只验证共同候选池上的 SFS+1NN 机制和交叉划分；它适合作为方法动机/附录证据：

```bash
cd '/root/projects/CPSS(DCC)'
P='/root/projects/CPSS(DCC)'
PY='/opt/gridflux-cpss-venv/bin/python3'
DROOT="$P/DCCData/full-v1/datasets"

$PY Script/run_mlcomp_like_small.py \
  --dataset-root "$DROOT" \
  --object-mib 32 \
  --probe-mib 4 \
  --bandwidth-mbps 100 \
  --output "$P/ExperimentRecords/dcc_mlcomp_like_20261002/mlcomp_like_small.json"
```

通常需要 **5--20 分钟**，具体取决于本地 CPU 和 XZ 编码速度。

## 结果检查

每个正式运行目录都应至少有 `method_metrics.csv`、`runner_config.json`、`run_status.json`；矩阵实验还会有各自的 `budget_*` 或带宽目录。启动后先看：

```bash
tail -f '/root/projects/CPSS(DCC)/ExperimentRecords/dcc_sampling_budget_20261002.nohup.log'
```

完成后不要手工改 CSV；把结果目录整体打包/复制回本地，论文分析脚本再统一生成汇总表和图。
