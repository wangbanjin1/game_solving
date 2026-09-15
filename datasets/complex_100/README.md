# 100场景固定数据集与求解基准

2026-09-15 使用提交 c0f7181 的代码及 configs/complex_100.json 生成并求解。共100个场景、10000人、100000条用户时段历史，数据为合成数据。完整产物从 outputs/complex_100_verified 原样归档，checksums.json 提供SHA-256校验；run.log 是当次控制台日志。

## 只跑求解

在仓库根目录执行：

```sh
python -m game_solving solve --config configs/complex_100.json --input datasets/complex_100/solver_inputs.jsonl --output outputs/complex_100_recheck
```

输出目录需不存在或为空。91个场景局部收敛，9个循环停止并标记失败，100个均有合法分配。任一失败场景导致退出码3，仍保存全部结果。时间指标依赖机器与负载。

## 文件

- solver_inputs.jsonl：可以直接求解的100场景输入，包含历史MOS均值。
- scenes.jsonl、history.jsonl：完整生成审计与逐时段历史。
- resolved_config.json、source_catalog.json、manifest.json：当次配置、来源及运行指纹。
- generation_report.json、validation_report.json：生成与历史校验报告。
- solve_results.jsonl：分配、证据、轨迹与停止原因。
- comparison.md、comparison.jsonl：逐场景前后对比。
- summary.json、batch_evaluation.json：运行统计与整批效果，含失败时保留方案。
- metrics.jsonl、run_labels.jsonl、static_labels.jsonl、reference_results.jsonl：指标和标签；本批未启用独立参考。

基础数据与基准结果保持不变，新实验请写入 outputs 下的新目录。更改模型定义需重新生成；更改历史窗口需重新聚合或生成，单独 solve 不读取原始历史重新计算。
