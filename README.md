# game_solving

依据 `docs/` 中两份设计文档实现的离线数据模拟与小区资源博弈求解。Python 3.10+，只使用标准库，无需 API、数据库或模型服务。

## 一条命令跑通

在仓库根目录执行：

```sh
python -m simulation_generator run --config configs/default.json --output outputs/demo
```

默认生成 5 个场景，每场景 30 个用户，包含六类重点业务及浏览、下载。生成的数据直接送入求解器，输出预测决策、逐步轨迹、容量校验和指标。输出目录必须为空，避免混入上一次结果。

```sh
# 数据生成和求解可以分开执行
python -m simulation_generator generate --config configs/default.json --output outputs/data
python -m simulation_generator solve --config configs/default.json --input outputs/data/solver_inputs.jsonl --output outputs/solve

# 最大迭代步数：配置文件或命令行均可控制
python -m simulation_generator run --max-steps 10 --output outputs/steps10

# 小规模联合穷举参考、千用户测试
python -m simulation_generator run --config configs/tiny.json --output outputs/tiny
python -m simulation_generator run --config configs/large.json --output outputs/large

# 验证不可达配额明确失败；预期退出码 2
python -m simulation_generator generate --config configs/strict_failure.json --output outputs/failure

# 回归测试
python -m unittest discover -s tests -v
```

退出码：0 为完成；2 为配置/生成失败；3 为至少一个场景没有可行求解方案。`allow_partial=true` 可保留部分生成成功的场景，缺口仍写入报告。合法的超时、循环、步数上限结果只要具有已验证可行解，就视为运行完成。

## 统一配置

`configs/default.json` 是完整参数表。其他配置文件只写需要覆盖的参数；运行时完整配置会保存为 `resolved_config.json`。不识别的配置项会报错，不会静默忽略。

| 参数 | 用途 |
| --- | --- |
| `seed` / `num_scenes` | 随机种子、场景数 |
| `population.users_per_cell` | 每小区用户数，一人一业务 |
| `population.*_probs` | 套餐、业务、位置、容忍度分布；最大余数法保证整数人数 |
| `cell.capacity_mode` | `derived` 根据当前分配推导容量；`fixed` 使用固定容量 |
| `cell.capacity_mbps` | 固定模式总预算，Mbps |
| `cell.unmanaged_mbps` / `safety_mbps` | 未建模占用、预留预算 |
| `generation.quota_mode` | `reachable` 从合法可达池生成；`specified` 按 MOS 配额生成 |
| `generation.strict_quotas` | 严格模式失败报错；宽松模式保留实际档位和缺口 |
| `generation.*_max_attempts` | 每角色和每场景重试上限 |
| `mos.compliance_probs` | 超额、达标、不达标、严重不达标比例 |
| `mos.target_by_package` / `baseline_by_package` | 体验目标与硬底线，语义独立 |
| `mos.weights` / `formula_groups` | 临时总聚合权重、P1～P5 参数组 |
| `bandwidth` / `profiles` | 带宽正反模型、profile 合法切换边和环境参数 |
| `policy` / `utility` | 保障、媒体控制、用户权重、历史补偿和稳定性成本 |
| `solver.max_iterations` | 最大内部迭代步数，默认 50 |
| `solver.time_budget_ms` | 求解时间预算，默认 2000 ms |
| `solver.margin_grid_mbps` | 有限资源余量候选网格 |
| `solver.*_candidates_per_role` | 候选评估及保留预算 |
| `reference` | 独立参考网格及穷举组合数上限 |

`--max-steps` 优先于配置文件，并写入展开后的配置。时间限制是软预算：初始化必须先构造并验证可行底线，最后也必须完成约束校验，因此可能略超时；步数严格不超过设定值。耗时受设备负载影响。

默认 `reachable` 模式不声称满足 `compliance_probs`。使用 `specified` 才按“套餐×业务”分配 MOS 配额。原模型并非所有档都可达，例如普通用户严重不达标档可能没有候选；会报告“当前搜索未发现候选”，不将有限搜索失败宣称为数学不可达证明。固定容量冲突也会限次失败，绝不缩带宽后保留旧 MOS。

## 数据和算法

1. 分阶段稳定散列随机流生成身份、业务、位置、套餐和画像；VIP 容忍度固定为不容忍。
2. 扫描合法媒体和余量网格，记录可达性；在条件池中采样并加独立微扰，再进行 inverse → forward → MOS 校验。
3. 全小区容量验证后输出完整状态和隔离私有字段的求解器视图。浏览、下载 MOS 为 null，资源照常计入。
4. 固定当前观测、历史和业务阶段，保留锚点；首轮从当前配置邻域选择，随后扩展至合法相邻 profile、可控媒体和有限资源档。
5. 以 `H - lambda × B / B_reference` 独立选择；Non-GBR 优先于 GBR 回收底线以上资源，按 H 损失率修复，再按 H 增益率升级和有限交换。
6. 价格使用修复前的原始需求更新。校验物理容量、合同下限、角色上限、可选用户/流聚合上限，保留最佳可行解。
7. 按策略、资源和 H 稳定性判断局部收敛；循环、超时、最大步数分别返回原因。

`sample_count / inclusion_probability` 统一放大资源与效用；当前生成入口使用全量视图。模型生成和评估共用同一套可替换接口，结果标记 `source=synthetic`、`evaluation_environment=matched_model`、`executable=false`。

状态包括 `CONVERGED_LOCAL`、`STOPPED_CYCLE`、`MAX_ITERATIONS`、`TIME_BUDGET`、`INFEASIBLE_HARD_FLOOR`。底线无解时不给出伪可行决策。软降级仅在角色明确允许时开放较低 MOS 候选，最终最佳解优先减少高套餐等级的失保人数；暂停和新请求拒绝执行尚未接入。

## 输出文件

| 文件 | 内容 |
| --- | --- |
| `manifest.json` | 配置、源代码文件及输入哈希，版本、种子和 Python 环境 |
| `resolved_config.json` | 实际使用的完整配置 |
| `source_catalog.json` | 附件范围、边界和原始异常说明 |
| `scenes.jsonl` | 全量合成状态、私有真值及提议/最终 KQI |
| `solver_inputs.jsonl` | 可直接求解的快照，不含参考解或隐藏真值 |
| `labels.jsonl` | 容量、目标压力、可达性标签 |
| `generation_report.json` | 请求/成功/失败数、配额缺口、可达范围和重复率 |
| `validation_report.json` | 对序列化重载数据检查单位、容量、模型闭合和标签隔离 |
| `solve_results.jsonl` | 每个角色的预测决策、终止原因及逐步 trace |
| `references.jsonl` | 独立联合参考结果，未求解时明确标记 |
| `candidate_sets.jsonl` | 开启参考评测时的共享有限动作宇宙 |
| `metrics.jsonl` | 改善/恶化/不变人数、达标变化、加权 MOS 增益与有效时的 WGR |
| `summary.json` | 本次运行摘要 |

每个 JSONL 文件一行一个场景。`scene_id` / `role_id` 贯通数据、预测和指标。JSON 禁止 NaN/Infinity，单文件先写临时文件再替换。

## 参考解与验证边界

`tiny.json` 在独立网格与算法候选的并集上联合枚举所有角色动作，并同时检查全小区资源，分别求纯加权 MOS 和固定 H 的参考最优。算法也从这一有限宇宙选择，确保可比。超过组合预算时标记 `not_computed`，不冒充最优。

只有参考精确、当前基线有效、分母为正且算法可行时才输出 `WGR_exact`。其余情况下输出 null 和原因；不把异常 WGR 裁到 [0,1]。

此版本交付单快照全量生成与离线求解核心。设计中的时间序列、噪声/缺失注入、模型失配、采样映射回全量、现网执行和反馈适配属于后续扩展，未通过配置假装支持。候选采用有限余量网格闭合搜索，不是完整连续 KQI 笛卡尔积或连续空间最优；profile 只按显式邻接展开。启发式不承诺纳什均衡或全局最优。

MOS 单项公式按设计保留，Q/I/V 总聚合仍为演示模型；上下行混合默认使用抽象方向预算。所有性能和收益结果只说明该合成环境下的行为，不是现网效果证明。
