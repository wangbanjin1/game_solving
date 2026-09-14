# 个人用户双向带宽模拟与博弈求解

Python 3.10+，运行时仅依赖标准库。以基站覆盖区域为小区，一人一个当前业务，上下行分别拥有 120 Mbps 容量。根据 `docs` 中新版 MOS 公式生成个人会话数据，执行有预算限制的资源博弈求解，并输出可行性证据、标签和指标。

## 快速运行

在仓库根目录执行，输出目录需为空或不存在：

```sh
python -m game_solving run --config configs/default.json --output outputs/demo
```

默认生成 5 个场景，每场景 30 人。生成和求解也可分开：

```sh
python -m game_solving generate --config configs/default.json --output outputs/data
python -m game_solving solve --config configs/default.json --input outputs/data/solver_inputs.jsonl --output outputs/solve
```

限制迭代轮数、总工作量和搜索时间：

```sh
python -m game_solving run --max-iterations 10 --max-total-steps 100000 --time-budget-ms 1000 --output outputs/bounded
```

三个上限先到先停，返回已有的最佳可行方案。`--max-steps` 仅是 `--max-iterations` 的兼容别名；总工作量请使用 `--max-total-steps`。最终校验使用预留工作量，时间预算属于协作式搜索期限，并非整个命令的硬实时期限。

## 配置与示例

`configs/default.json` 是完整配置；其他配置只覆盖差异。套餐、业务、MOS 档位概率表整体替换，其余普通对象递归合并；未知键报错。每次运行保存完整的 `resolved_config.json`。

| 配置 | 场景用途 |
| --- | --- |
| `default.json` | 5 个混合业务场景，每场景 30 人 |
| `tiny.json` | 2 个三人会议场景，开启独立离散枚举参考 |
| `history.json` | 2 个三人会议场景，模拟历史 MOS 并启用已有欠账补偿 |
| `medium.json` | 100 人短视频，验证总工作量耗尽后的可行结果 |
| `large.json` | 1000 人固定定额游戏，验证规模和计数；不是复杂优化性能基准 |
| `strict_failure.json` | 故意要求游戏达到不可达 MOS 档位，预期生成失败、退出码 2 |

```sh
python -m game_solving run --config configs/tiny.json --output outputs/tiny
python -m unittest discover -s tests -v
```

可选安装：`python -m pip install .`，之后可使用 `game-solving run --output outputs/installed`。统一使用 `python -m game_solving` 或安装后的 `game-solving` 命令；旧版兼容包已移除，旧版单带宽配置和数据需要重新生成。

## 结果阅读顺序

如需验证长期公平补偿，运行：

```sh
python -m game_solving run --config configs/history.json --output outputs/history_demo
```

`generation.history.enabled` 默认为 `false`；开启后默认生成 5 段、每段 60 秒的历史，通过 `utility.history_window_seconds` 指定的窗口按覆盖时长加权，写入用户的 `history_mos`。求解器已有的欠账补偿会自动使用该值。完整配置和建模边界见[历史数据模拟说明](docs/历史数据模拟说明.md)。

开启历史时额外输出 `history.jsonl`，记录每段上下行带宽、MOS 和模型版本；`scenes.jsonl` 的生成审计中也保留相同记录。生成流程会重新读取文件，校验历史分配约束、复算 MOS 与历史均值，验证数量写入 `validation_report.json` 的 `history_observations_validated`。

先看 `summary.json`，再看 `solve_results.jsonl` 的分配与停止原因，最后看 `run_labels.jsonl`、`metrics.jsonl` 和 `reference_results.jsonl`。`solver_inputs.jsonl` 是可直接复用的求解输入；`scenes.jsonl` 额外保留生成审计信息。仓库的 `examples/tiny` 提供完整新版样例。

退出码：0 表示处理完成并有可行方案（只生成时表示生成完成）；2 表示参数、输入或生成失败；3 表示至少一个场景求解失败（未收敛或无可行方案）。迭代上限、循环和超时本身不等于无解。`allow_partial=true` 允许继续处理生成成功的部分，失败记录仍会保留。

## 代码与设计说明

- [代码架构与配置说明](docs/代码架构与配置说明.md)：整体架构、模块与每个文件职责、全部配置字段、扩展方法和结果语义。
- [重构验证记录](docs/重构验证记录_v2.md)：测试覆盖及实测场景结果。
- [核心算法方案](docs/个人用户双向带宽_数据模拟与博弈求解方案.md)：业务建模与公式。

求解是有限候选上的价格迭代、资源修复与局部交换，不承诺一般场景的全局最优。所有输出是离线模拟建议，`executable=false`，不直接控制基站。固定 KQI 下无法达到的 MOS 会明确记录，增加带宽不会被假设为能够消除时延、丢包或卡顿。

## 均衡人口批量实验

`configs/balanced.json`：100 个场景，每场景 100 人；十类业务各占 10%，普通/VIP/超级 VIP 约各三分之一，GBR/Non-GBR 各一半，上下行仍各 120 Mbps。这里均衡的是人口边际分布，不保证 MOS 档位均衡。

先生成，再读取同一批数据测试：

```sh
python -m game_solving generate --config configs/balanced.json --output outputs/balanced_my_data
python -m game_solving solve --config configs/balanced.json --input outputs/balanced_my_data/solver_inputs.jsonl --output outputs/balanced_my_solve
```

输出目录需为空或不存在。生成阶段摘要中的 feasible_scenes=0 表示尚未求解；生成是否成功请看 generation_report.json 和 validation_report.json。默认 INFO 日志实时显示场景进度、求解状态与耗时。

## 运行日志

使用标准 logging，默认 INFO。日志写入 stderr，最终 JSON 摘要保留在 stdout。配置 `logging.level` 或命令行 `--log-level WARNING` 可减少输出；DEBUG 可查看生成重试原因。直接使用 Python API 时由调用方配置 logging。

生成场景后 INFO 日志还会输出业务/套餐人数、个人会话 MOS 均值与范围、实际 MOS 档位及目标达标人数、上下行当前占用/可用容量/占用率/余量。MOS 只统计有 MOS 的用户，双向会话仅计一次；目标达标包含超额达标。占用率以扣除预留和未管理占用后的可用容量为分母，仅描述当前资源占用，不代表目标保障可行性证明。

单样本检查时使用 `solve --log-level DEBUG`：输出每个用户的输入、三级可行性证据、候选数量、每轮协调前需求与协调后分配、价格更新及最终用户前后对照。详细日志会增加墙钟耗时，可能影响时间预算触发点，不改变总工作量计数规则。

## 求解前后对比与运行成功标准

默认 INFO 输出场景进度、最终状态及对比表；逐用户、每轮计算和生成特点详情使用 DEBUG。输出 `comparison.md`（可读表格）及 `comparison.jsonl`（结构化数据），包括基本保障不足人数、目标达标人数、MOS均值、加权MOS、双向占用，以及改善/恶化/不变人数和最大降幅。

`run_status=SUCCESS` 要求返回合法方案且局部收敛；轮数、总工作量、时间预算耗尽、循环停止或无可行解统一标记 FAILED。失败仍保留已有合法方案供检查，`solution_status` 单独描述其可行性，不能把未收敛理解为数学无解。只要有失败场景，solve/run 退出码为 3；summary 的 successful_scenes/failed_scenes 统计运行结果，feasible_scenes 仅统计保留方案是否可行。旧实验结果不自动改写。
