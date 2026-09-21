# QoE 用户双向带宽模拟与博弈求解

Python 3.10+，运行时仅依赖标准库。生成、求解分开运行；求解返回已找到的最高效用合法方案，并单独记录停止时方案及参考答案。

## 当前业务范围

只使用短视频、长视频、会议、云游戏、语音、观看直播、游戏、开直播。原始样本表中的 **sa、sta 均排除**，其余 594,370 条 QoE 样本重新归一化。

常规回归配置仍保留 100 人分布。新增 `congestion_qoe_9_20_1.json` 专项配置为 300 人，仅含普通/VIP，覆盖归并后的 17 个应用类型，并为开直播预留 10 人；资源余量只取 0%、1%、3%、5%。手游按 60/80/100 ms 三档归并，其他业务按相同或相近质差规则归并。

专项求解第一轮保留每位用户不同的初始状态；第二轮起按“套餐、容忍度、位置、业务大类、App、初始 MOS 分档”选择共同 MOS 起跑线，再映射为各用户可执行动作。VIP 同时检查 App 级 `avgQoe`、时延、1～6 卡顿档位和上下行最低速率，普通用户只使用剩余资源。逐轮证据保存预测 KQI 及其相对初始值的变化；专项配置还启用码率驱动的分辨率、时延、丢包和卡顿响应模型，参数是未标定的仿真假设。

## 生成一批数据

在仓库根目录执行：

```powershell
python -m game_solving generate --config configs/congestion_qoe.json --output outputs/my_qoe_data
```

新增需求专项批次：

```powershell
python -m game_solving generate --config configs/congestion_qoe_9_20_1.json --output outputs/qoe_app_300_data
python -m game_solving solve --config configs/congestion_qoe_9_20_1.json --input outputs/qoe_app_300_data/solver_inputs.jsonl --output outputs/qoe_app_300_solve
python -m game_solving.visualization --input outputs/qoe_app_300_solve --output outputs/qoe_app_300_report.html
```

VIP 初始未达标比例专项场景（63 个场景、累计 18,900 人）只需先执行生成：

```powershell
python -m game_solving generate --config configs/vip_initial_scenarios_9_21.json --output outputs/vip_initial_scenarios_9_21_v2
```

初始统计写入 `initial_distribution.jsonl` 和 `initial_distribution.csv`；本阶段不生成专项 HTML。

默认批次包含 5 个独立的 100 人基础样本；每个基础样本生成相同用户状态的 0%、5%、10%、15%、20% 带宽余量版本，共 25 个场景。上下行容量分别按当前占用计算并写入样本。

## 单独求解

```powershell
python -m game_solving solve --config configs/congestion_qoe.json --input outputs/my_qoe_data/solver_inputs.jsonl --output outputs/my_qoe_solve
```

输出目录必须为空或不存在。`generate` 的 `feasible_scenes=0` 表示尚未求解；生成是否完成看 `generation_report.json` 与 `validation_report.json`。

## 查看结果

```powershell
python -m game_solving.visualization --input outputs/my_qoe_solve --output outputs/my_qoe_report.html
```

HTML 离线展示分配前后指标、价格、当轮/历史最好/参考效用，以及初始套餐统计。

报告还提供完整策略对照（初始、算法返回、停止时、暴搜参考）、逐轮用户请求和实际分配、候选动作的 H−影子成本评分、资源协调步骤与收敛状态。选择场景后，可用“上一轮/下一轮”回放，并下载本场景证据 JSON。`trace_users=true` 时求解会保存这些详细记录；旧输出需重新求解才能补齐候选评分。

“用户体验筛选与逐轮轨迹”支持按 VIP/普通用户、MOS 上升/下降/不变、指定 KQI 的上升/下降/不变联合筛选。选择或点击用户后，可查看该用户从初始状态、每轮求解到算法返回方案的 MOS 与 KQI 折线；KQI 可切换 avgQoe、码率、分辨率、时延、丢包率、卡顿率、卡顿档位、首缓和抖动，并可指定上行、下行或会话方向。

- `initial_distribution.jsonl`：套餐和业务、MOS/KQI、VIP 达标分布；普通用户不评价达标。
- `iteration_trace.jsonl`：逐轮请求、分配、效用分项和交换原因。
- 逐轮用户明细还包含 App、分组键、预测 KQI、相对初始 KQI 变化、VIP 质差保障是否满足及未满足原因。
- `solve_results.jsonl`：最终返回方案、停止时方案、停止原因。
- `reference_results.jsonl`：参考分配、H 差距和搜索证明状态。
- `comparison.md`、`summary.json`：前后效果与运行摘要。

## 仅保留的实验配置

| 文件 | 用途 |
| --- | --- |
| `configs/default.json` | 完整参数源，默认业务比例也仅含 QoE |
| `configs/congestion_exact.json` | 4 人短视频精确参考实验，五档余量；不代表总体业务分布 |
| `configs/congestion_qoe.json` | 100 人仅 QoE 业务实验，五档余量 |

小样本可将生成、求解命令的配置换为 `congestion_exact.json` 并使用新目录。旧实验数据集、示例报告和配置已清理；`tests/fixtures` 仅为自动测试的最小输入条件。

## 结果语义和边界

初始负载支持参考“5 台手机 iperf 合计上下行各约 120 Mbps”的压测量级。百人 QoE 配置增加按业务区分的高负载初始提案；这不是固定小区上限，也不按人数乘以 24 Mbps。具体参数、假设及人数与容量的关系见 [实测吞吐参考与初始负载](docs/实测吞吐参考与初始负载.md)。

新实验按总 H 优化；影子价格引导请求，不作为跨轮比较的固定收益。算法保留历史最好合法方案，它可能不同于停止时方案。局部收敛不等于全局最优；参考状态仅在 `exact_discrete` 时证明声明的有限候选空间搜索完成。百人参考预算有限，`best_known` 不能作为最优证明。

当前套餐仅保留普通/VIP，专项样本为普通 210 人、VIP 90 人。达标与质差保障只评价 VIP；普通用户没有质差保障状态，仅使用剩余资源并统计 MOS/KQI 变化。套餐→业务→位置→容忍度权重顺序属于实验设置，尚非现网标定。历史是模拟序列，不是实际执行反馈。

退出码：0 为完成，2 为输入或生成错误，3 为求解至少一个场景未收敛或失败；失败仍可能保留合法方案。

## 测试与说明

```powershell
python -m unittest discover -s tests -v
```

- [项目文档索引](docs/README.md)
- [拥塞实验设计与验证](docs/拥塞实验设计与验证.md)
- [核心算法与已确认业务分布](docs/个人用户双向带宽_数据模拟与博弈求解方案.md)
- [代码架构与配置说明](docs/代码架构与配置说明.md)
- [历史模拟说明](docs/历史数据模拟说明.md)
