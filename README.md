# 个人用户双向带宽模拟与博弈求解

Python 3.10+，运行时仅依赖标准库。以基站覆盖区域为小区，一人一个当前业务，上下行分别拥有120 Mbps容量。支持独立生成、读取固定数据求解、生成后立即求解，并输出日志、前后对比表和成功/失败标签。

## 直接使用仓库中的100场景数据，只跑求解

仓库已包含 [100场景数据集及基准结果](datasets/complex_100/README.md)，不需要先生成。所有命令在仓库根目录执行：

```powershell
python -m game_solving solve --config configs/complex_100.json --input datasets/complex_100/solver_inputs.jsonl --output outputs/complex_100_recheck
```

求解后的表格在 `outputs/complex_100_recheck/comparison.md`，汇总在 `summary.json`。输出目录必须为空或不存在；重复运行请换目录名。

这批数据包含100个场景、每场景100人、十类业务、三档套餐和两种QoS类别；每人10段30秒历史。已有基准为91个成功、9个循环停止失败，全部保留合法分配。基准文件保存在 `datasets/complex_100`，新实验写入 `outputs`。

## 三种命令

| 命令 | 用途 | 是否需要已有输入 |
| --- | --- | --- |
| `generate` | 只生成模拟数据，校验后保存 | 不需要 |
| `solve` | 读取固定数据求解，不重新生成 | 必须提供 `--input` |
| `run` | 生成数据后立即求解 | 不需要 |

### 只生成一批新数据

```powershell
python -m game_solving generate --config configs/complex_100.json --output outputs/complex_100_new_data
```

生成阶段 `feasible_scenes=0` 表示尚未求解；生成是否成功看 `generation_report.json` 和 `validation_report.json`。

### 对刚生成的数据单独求解

```powershell
python -m game_solving solve --config configs/complex_100.json --input outputs/complex_100_new_data/solver_inputs.jsonl --output outputs/complex_100_new_solve
```

### 一次完成生成和求解

```powershell
python -m game_solving run --config configs/complex_100.json --output outputs/complex_100_new_run
```

### 小规模快速检查

```powershell
python -m game_solving run --config configs/tiny.json --output outputs/tiny_check
python -m game_solving run --config configs/history.json --output outputs/history_check
```

## CLI 参数

```powershell
python -m game_solving --help
```

| 参数 | 说明 |
| --- | --- |
| `--config PATH` | JSON覆盖配置；省略则加载默认配置 |
| `--input PATH` | JSONL场景文件，每行一个场景；solve必填。没有单场景筛选参数，需要时提供只含一行的文件 |
| `--output PATH` | 结果目录，默认 outputs/demo；需为空或不存在 |
| `--max-iterations N` | 每场景最大外层迭代轮数 |
| `--max-steps N` | max-iterations的兼容别名，不是总工作量 |
| `--max-total-steps N` | 每场景共享工作量上限，包含校验、候选、模型和协调等 |
| `--time-budget-ms N` | 每场景搜索时间预算，单位ms |
| `--log-level LEVEL` | DEBUG、INFO、WARNING、ERROR、CRITICAL；覆盖配置日志级别 |

对同一批数据调整求解预算：

```powershell
python -m game_solving solve --config configs/complex_100.json --input datasets/complex_100/solver_inputs.jsonl --output outputs/complex_100_budget_test --max-iterations 20 --max-total-steps 300000 --time-budget-ms 3000
```

预算先到先停。搜索预留最终校验工作量；时间期限是协作式的，整个命令还包含读取、评估和输出，不能将其视为硬实时总耗时上限。

## 日志与前后对比表

默认INFO：场景进度、完成摘要及对比表。DEBUG：额外显示生成特点、逐用户输入、可行性证据、候选数量、每轮需求/分配/价格和最终用户对照。详细日志可能影响时间预算触发点。

```powershell
python -m game_solving solve --config configs/complex_100.json --input datasets/complex_100/solver_inputs.jsonl --output outputs/complex_100_debug --log-level DEBUG
```

日志写入stderr，最后的JSON摘要写入stdout。PowerShell可分别保存：

```powershell
python -m game_solving solve --config configs/complex_100.json --input datasets/complex_100/solver_inputs.jsonl --output outputs/complex_100_logged 2> complex_100.log 1> complex_100_summary.json
```

`comparison.md` 提供基本保障不足人数、目标达标人数、会话MOS均值、加权MOS总和及双向占用的前后对照，并列出改善/恶化/不变人数和最大MOS降幅；机器读取使用 `comparison.jsonl`。MOS只统计有MOS模型的用户，双向会话只计一次；浏览和下载按带宽保障处理。

## 成功、失败与退出码

- `run_status=SUCCESS`：返回合法分配且局部收敛。
- `run_status=FAILED`：预算耗尽、循环停止或未找到可行方案。失败时仍保留已有合法分配，对比表注明它未收敛。
- `solution_status`：单独表示分配是否可行、是否满足全员基本保障。FEASIBLE_DEGRADED表示合法但部分基本保障不足，可与SUCCESS同时出现。
- `successful_scenes` / `failed_scenes`：运行成功失败数；`feasible_scenes`：有合法分配的场景数。

| 退出码 | 含义 |
| --- | --- |
| 0 | generate按配置完成，或solve/run全部场景成功 |
| 2 | 参数、输入、文件错误，或不允许部分成功时生成失败 |
| 3 | solve/run至少一个场景失败，完整结果仍保存 |

`allow_partial=true` 允许继续处理成功生成的部分，缺失场景仍记录在生成报告。未收敛不等于数学无解；局部稳定不等于全局最优或纳什均衡。

## 配置和数据一致性

`configs/default.json` 是完整参数表，其他文件只覆盖差异；命令行覆盖优先级最高。套餐、业务、MOS档位概率表整体替换，其他普通对象递归合并，数组整体替换；未知键报错。运行时保存完整 `resolved_config.json`。

| 配置 | 用途 |
| --- | --- |
| `default.json` | 5个混合场景，每场景30人 |
| `tiny.json` | 2个三人会议场景，启用离散参考 |
| `history.json` | 2个三人会议场景，开启历史MOS |
| `balanced.json` | 100个百人均衡人口场景，不开启历史 |
| `complex_100.json` | 100个百人混合场景，10段历史；50轮、500000工作量、5000ms预算 |
| `medium.json` | 百人短视频工作量限制验证 |
| `large.json` | 千人固定定额游戏，验证规模与计数 |
| `strict_failure.json` | 故意不可达的严格配额，预期生成失败 |

修改预算或价格参数可以复用输入。修改公式、业务定义、码率量化等导致模型指纹变化，需重新生成匹配数据。历史开启后，`history.jsonl` 保存逐时段审计，求解输入只携带 `history_mos`；更改历史窗口需重新生成或外部重新聚合，solve不会重算历史均值。历史只模拟带宽变化，非码率KQI固定，不能视为真实网络轨迹。

## 结果文件和安装测试

优先阅读 `summary.json`、`comparison.md`，再看 `solve_results.jsonl` 的分配、证据和轨迹。生成质量看 `generation_report.json`、`validation_report.json`；标签与指标见 `run_labels.jsonl`、`static_labels.jsonl`、`metrics.jsonl`。完整产物说明见[数据集说明](datasets/complex_100/README.md)。

```powershell
python -m unittest discover -s tests -v
python -m pip install .
game-solving --help
```

核心代码在 `game_solving`；旧兼容包已移除。普通实验输出被Git忽略，本次用户指定发布的数据集单独保存在 `datasets/complex_100`。所有分配均为离线建议，不直接执行基站控制。

- [代码架构与完整配置说明](docs/代码架构与配置说明.md)
- [核心算法方案](docs/个人用户双向带宽_数据模拟与博弈求解方案.md)
- [历史数据模拟说明](docs/历史数据模拟说明.md)
- [重构验证记录](docs/重构验证记录_v2.md)

## 从已有结果生成离线HTML可视化

无需重新生成数据或求解：

```powershell
python -m game_solving.visualization --input datasets/complex_100 --output outputs/complex_100_dashboard.html
```

用浏览器打开HTML即可。支持成功/失败筛选、场景切换、前后指标表、每轮上下行需求/分配/容量曲线、收益与价格曲线。页面自包含，不访问CDN。

`--input` 是结果目录，必须包含 solve_results.jsonl；comparison.jsonl 用于前后对比，solver_inputs.jsonl 或 resolved_config.json 用于容量线。可选文件缺失时明确显示缺失，不伪造数值。输出HTML必须为新文件。图中是当轮协调方案，并非历史最佳；失败时保留方案不算收敛成功。

Python接口：`from game_solving.visualization import generate_report`，调用 `generate_report(input_dir, output_path)`。

仓库提供已生成的 [100场景HTML示例](examples/complex_100_report.html)，下载后用浏览器打开即可；GitHub文件页面只显示源码。
