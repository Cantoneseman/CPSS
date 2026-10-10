# CPSS 决策记忆：第一个版本和第二个版本

## 做了什么

决策记忆只保存“这个块应该选哪条管线”和候选排名，不保存压缩数据。每次真正输出压缩块时，仍会重新执行被选中的管线并做正常校验。

- **v1 精确命中**：块 SHA-256、块大小、数据类型/形状/端序、候选管线集合、目标函数、带宽、接收端解压配置和运行时版本全部一致，才直接复用上次选择。
- **v2 相似命中**：精确命中失败后，计算低成本字节特征并查找同一兼容条件下的近邻。至少 3 个近邻、且它们对同一管线的一致率至少 80%，才复用；否则继续完整候选探测。

SQLite 文件是持久存储，可以跨进程和跨运行重用。数据库损坏、记录格式错误、候选集变化或相似块意见不一致，都会自动回退到完整探测。

## 旧矩阵入口

给 `native_exhaustive` 增加一个数据库路径即可启用：

```powershell
python Script/run_cpss_sampling_dedup_matrix.py `
  --input-dir noaa=path\to\dataset `
  --strategies native_exhaustive `
  --block-mib 4 `
  --bandwidth-Bps 12500000 `
  --decision-memory ExperimentRecords\decision-memory.sqlite3
```

第一次运行是冷启动；第二次用同一个 `--decision-memory` 和相同选择条件运行，就是热启动。结果文件中的 `block_metrics.csv` 会记录：

- `decision_source`: `exhaustive`、`exact_memory` 或 `similar_memory`
- `candidate_calls`: 该块实际执行了多少次候选管线
- `memory_hit`: 是否命中记忆

`method_metrics.csv` 还会汇总命中块数、未命中块数和候选调用总数。

## 正式 DCC 入口

正式入口的 CPSS 自适应校准也支持同一个 SQLite 文件：

```powershell
python .remote_staging\Script\run_dcc_formal_gridflux.py `
  --dataset noaa=path\to\dataset `
  --methods cpss-adaptive `
  --sample-count 2 `
  --decision-memory ExperimentRecords\dcc-decision-memory.sqlite3
```

`method_metrics.csv` 中的 `candidate_pipeline_calls` 可直接比较冷启动和热启动探测开销。

## 验证命令

```powershell
cd science-compressor
python -m pytest -q

cd ..\.remote_staging
python -m pytest -q --ignore=tests/test_oss_dataset_staging.py
```

Windows 下被忽略的 OSS 测试只因为代码依赖 Unix 的 `fcntl`，与决策记忆无关。

## 当前实测

在 4 MiB NOAA 风格块、11 条候选管线、100 Mbps 等价带宽条件下：

- v1 冷启动：11 次候选调用，约 1.63 秒；热启动：0 次候选比较，约 0.047 秒。
- v2 使用 3 个相似历史块给一个留出块决策：0 次候选调用，近邻一致率 100%。

这些数字证明了“跳过探测”的机制有效；正式数据集上是否值得启用 v2，还要看留出块的命中率和误选后的系统时间损失。
