# 体验Agent博弈求解开发实施方案 v1

> 依据：用户给出的状态定义、资源拆分、三级动作、效用函数、全局资源检查、协调、价格更新、收敛和初始化流程，以及已上传的KQI评分公式表。
>
> 交付目标：开发者能够据此实现离线可运行、可单元测试、可替换模型的求解器。本文不是现网模型精度或全局最优性的证明。
>
> 带宽模型按用户要求自行构造，版本为 `analytic_bandwidth_v1`。所有示例参数均为开发与仿真默认值，尚未经现网标定。用户提供的KQI评分函数保留；附件缺少最终MOS聚合与完整业务标签，分别以可配置适配器及明确标注的离线默认值补齐，不阻塞开发。
>
> 公式统一使用纯文本代码块，避免公式图片丢失或Markdown数学渲染差异。

## 1. 实现范围和算法主线

第一版实现单小区、单方向等效速率预算下的离散候选优化。上下行分别调用求解器；如果同一动作必须同时占用上下行，需升级为多资源约束，不能把两次独立结果直接拼成联合保障承诺。

严格按以下顺序执行：

1. 读取当前小区和用户业务状态，固定输入快照。
2. 拆分非重点业务、Non-GBR、GBR的底线与可回收资源。
3. 以当前真实状态为锚点构造三级候选动作。
4. KQI评分、MOS校验、带宽估计、可执行性检查、Pareto剪枝。
5. 在当前影子价格下计算角色效用，独立选择最优动作。
6. 按代表人数汇总全小区资源，检查是否超限。
7. 资源空闲时按增益率升级；超限时按损失率分阶段降级。
8. 根据协调前需求更新非负影子价格。
9. 同时检查策略、资源、效用稳定性与资源可行性。
10. 输出最佳已知可行方案；实际执行与反馈由独立模块完成。

本方案使用“有限候选＋价格协调＋贪婪可行性修复”，不宣称得到纳什均衡或全局最优。

## 2. 必须统一的语义

### 2.1 角色r

`r` 表示一个用户业务实例，或一组同质用户业务的代表角色，不表示一个整个小区。

- 精确用户：一个角色代表一个实际业务实例。
- 采样代表：一个角色代表若干同类业务，具有 `population_weight`。
- 同一用户有多个业务时使用不同 `role_id`，共享 `user_id`，执行前需校验用户聚合上限。
- 同一QoS Flow内不可独立执行的多个业务，必须合并为一个可控角色，或由执行适配器做联合约束；不能输出互相矛盾的流配置。

### 2.2 状态与目标分开

```text
S_r = (K_observed_r, MOS_observed_r, B_current_r, w_r, G_current_r)

候选a对应：
K_target_r(a)     目标KQI
K_predicted_r(a)  带宽模型正向校验后的预测KQI
MOS_predicted_r(a)= f(K_predicted_r(a))
B_required_r(a)   预测所需资源
```

当前观测、目标、预测不能覆盖同一字段。内部迭代不能修改真实观测。

### 2.3 三种时间

| 字段 | 含义 |
| --- | --- |
| `control_epoch` | 真实网络控制周期t |
| `solver_iteration` | 同一周期内部迭代k |
| `stream_phase` | `initial`或`steady`，供初始缓冲评分使用 |

MOS稳定性惩罚以周期开始时的上一有效实测/执行状态为参照；不能以内部上一轮候选MOS为参照。`stream_phase`在一次求解内固定，不因为内部迭代变为2就改用RTT评分。

### 2.4 单位

| 字段 | 第一版输入单位 |
| --- | --- |
| 带宽、预算、余量 | Mbps |
| MOS公式输入码率 | kbps |
| `resolution` | 垂直像素，如720、1080；离线假设 |
| RTT、初始缓冲 | ms |
| 丢包率、卡顿率 | 0～1比例，1%输入0.01 |
| `burst_mbit` | Mbit |
| 历史窗口、冷却期 | 秒 |

以上MOS单位是为了开发固定的假设，附件未标明全部单位。输入须携带 `unit_profile=demo_v1` 或已确认的配置版本，不能静默猜单位。

## 3. 对原流程做的必要实现澄清

| 原表达 | 实现约定 |
| --- | --- |
| 非重点业务分最低资源 | 与GBR分类交叉，先按业务分组；非重点先计算固定底线，避免重复扣资源 |
| 预留超限时尽力而为 | 保留物理容量约束，返回明确降级/不可行状态及未满足清单 |
| 公平惩罚加入权重 | 实际是历史欠账提升当前收益权重，不是候选无关的常数扣分 |
| 低于总资源进入收敛 | 仅进入可行分支，仍可升级且仍需检查稳定性 |
| ΔU用于资源协调 | 第一版用剔除影子价格后的净体验效用变化，防止与价格重复计费 |
| lambda更新 | 使用协调前的独立需求，且投影到非负区间 |
| Pareto剪枝 | 同时考虑稳定性和切换成本，保留锚点、底线、降级恢复候选 |
| 当前5QI升级 | 按合法profile邻接表切换，不对5QI编号做加减 |

这些约定不改变用户的流程顺序，而是使每一步具有唯一实现含义。

## 4. MOS模型实现

### 4.1 单项评分：使用附件公式

```python
from math import exp

def clip(x, lo=1.0, hi=5.0):
    return max(lo, min(x, hi))

def bitrate_score(bitrate_kbps):
    return 5.0 / (1.0 + exp(-bitrate_kbps / 928.9840))

def resolution_score(resolution):
    return 5.0 / (1.0 + exp(-resolution / 410.0))

def rtt_score(rtt_ms):
    return 4.0 * exp(-0.0035 * rtt_ms) + 1.0

def loss_score(loss_ratio):
    return 4.0 * exp(-180.94 * loss_ratio) + 1.0

def stall_score(stall_ratio):
    return 5.0 - 4.0 * stall_ratio

def initial_buffer_score(buffer_ms):
    return 4.0 * exp(-0.0003 * buffer_ms) + 1.0
```

计算前拒绝负值、NaN、无穷大及超范围比例。码率为0时原式仍给2.5分，因此业务无流量或已拒绝的状态单独标记 `unserved`，不要用该公式冒充可用体验。

### 4.2 维度评分

```text
Q = clip(4 × [1-w1×(5-s_bitrate)-w2×(5-s_resolution)] + 1)
V = clip(4 × [1-g1×(5-s_loss)-g2×(5-s_stall)] + 1)
I = s_initial_buffer，适用业务且stream_phase=initial
I = s_RTT，其他情况
```

保留附件回退规则：

- 无分辨率：`Q=s_bitrate`。
- 手游：`Q=4.5`。
- 无卡顿率：`V=s_loss`。
- 无丢包率：支持该回退的公式组使用 `V=s_stall`。
- 两者都缺失：不设为满分；返回 `INSUFFICIENT_KQI`，使用仍有效的缓存值或保持现状。
- RTT缺失允许配置默认值，但输出 `imputed=true`、默认值来源和低置信度标记，不能与实测混记。

### 4.3 五组公式参数

附件按出现顺序命名，不把推测的业务名称当成已确认信息。

| 公式组 | w1 | w2 | g1 | g2 | 交互模式 |
| --- | ---: | ---: | ---: | ---: | --- |
| P1 | 0.25 | 0.05 | 0.25 | 0.10 | RTT |
| P2 | 0.25 | 0.05 | 0.05 | 0.25 | RTT |
| P3 | 0.04 | 0.25 | 0.04 | 0.25 | 初始缓冲→RTT |
| P4 | 0.25 | 0.05 | 0.05 | 0.25 | 入会初始缓冲→RTT |
| P5 | 固定Q=4.5 | — | 0.25 | 0.04 | RTT |

离线默认业务映射：视频电话/云游戏→P1，开播/看直播→P2，点播/短视频→P3，会议→P4，手游→P5。该表标记 `demo_mapping=true`，正式接入时使用已确认映射配置替换。

### 4.4 最终MOS聚合适配器

附件提供Q/I/V，但未给出最终合成函数。为使第一版可运行，离线默认：

```text
MOS = clip(alpha_Q×Q + alpha_I×I + alpha_V×V)
alpha_Q = alpha_I = alpha_V = 1/3
```

这是临时聚合，不属于用户原公式。实现接口 `MosModel.evaluate(kqi, profile, phase)`，总聚合可替换为正式函数，其他模块不能写死加权平均。

MOS模型版本必须同时记录：单项公式、公式组映射、聚合函数和单位配置。目标MOS=5不强制生成无限码率；离散候选不可达时返回目标不可达。

## 5. 自定义带宽模型：analytic_bandwidth_v1

### 5.1 设计意图

在没有训练好的预测器时，用一个透明、可逆、可测试的模型估计：达到给定码率、时延、丢包和卡顿目标需要多少Mbps。

模型把需求拆为：

```text
业务载荷速率 + 为降低时延/丢包/卡顿需要的服务余量
→ 加上协议开销并考虑有效利用比例
→ 得到带宽预算
```

这是人为构造的局部模型。它不证明增加带宽必然改善非拥塞丢包、服务器时延或无线覆盖问题；这些不能改善的部分用不可突破的底噪表示。

### 5.2 接口

```text
predict_required_bandwidth(MOS_target, KQI_target, G, context)
→ feasible, bandwidth_mbps, predicted_kqi, predicted_mos,
  reason_codes, confidence, model_version
```

`MOS_target`用于目标校验，不再额外乘一次“高MOS带宽系数”。MOS已经由KQI产生，重复相乘会双重计入体验要求。

实际签名需要 `G` 和环境条件；文档简写 `B=g(MOS,K)` 时，视这些为条件输入，而非假设其不存在。

### 5.3 中间变量

```text
R = bitrate_kbps / 1000 + audio_mbps              # 业务总载荷Mbps
C = B × efficiency / (1 + overhead_ratio)         # 有效服务速率Mbps
x = C - R                                        # 服务余量Mbps
```

模型只允许 `x >= margin_min_mbps`。对手游不开放任意码率优化，`R`使用固定的观测需求或业务默认需求；不能增加虚构视频码率提高手游评分。

### 5.4 正向响应模型

在第一版冻结环境条件下：

```text
RTT_ms     = rtt_floor_ms + rtt_coeff_ms_mbps / x
loss_ratio = loss_floor + loss_amp × exp(-x / loss_scale_mbps)
stall_ratio= stall_floor + stall_amp × exp(-x / stall_scale_mbps)
buffer_ms  = buffer_floor_ms + 1000 × burst_mbit / C
```

分辨率和业务码率来自固定业务状态或具备执行权限的媒体档位，不由上面四个式子凭空改变。

这些函数满足：在底噪和业务载荷不变时，更多服务余量使预测时延、丢包、卡顿不增。GBR本身不获得虚构的“免费资源”，其影响只来自profile绑定的环境参数。

### 5.5 反向带宽计算

给定目标KQI，分别计算需要的余量：

```text
x_RTT = rtt_coeff_ms_mbps / (RTT_target - rtt_floor_ms)

x_loss = max(0, loss_scale_mbps
                 × ln(loss_amp / (loss_target-loss_floor)))

x_stall = max(0, stall_scale_mbps
                  × ln(stall_amp / (stall_target-stall_floor)))

C_buffer = 1000 × burst_mbit / (buffer_target_ms-buffer_floor_ms)

x_required = max(margin_min_mbps, x_RTT, x_loss, x_stall)
C_required = max(R + x_required, C_buffer)
B_required = C_required × (1+overhead_ratio) / efficiency
```

缺少目标约束的项不参与最大值，但正向输出仍须产生对应有效预测值供MOS计算。处于steady阶段时不使用初始缓冲项。

边界处理：

1. 所需目标小于或等于对应底噪，且该项系数为正：不可达，不除零。
2. `loss_amp`或`stall_amp`为0：目标不低于底噪即可，不计算对数。
3. `burst_mbit=0`：缓冲项只检查底噪。
4. `0<efficiency<=1`，尺度参数和RTT系数必须为正。
5. 预测带宽超业务上限：拒绝候选，不能直接截断带宽后仍声称MOS满足。
6. 计算B后用正向模型重算KQI和MOS。后续收益使用正向预测MOS，不使用用户填写的期望值。
7. 对正向MOS高于候选L区间上限的结果重新归档至实际区间，并去重；低于要求下限则淘汰。

### 5.6 离线默认参数

以下均为人为示例，配置应标记 `calibrated=false`：

| 参数 | 默认值 | 说明 |
| --- | ---: | --- |
| `overhead_ratio` | 0.08 | 开销比例 |
| `efficiency` | 0.85 | 预算转为有效服务速率的比例 |
| `margin_min_mbps` | 0.05 | 最小服务余量 |
| `rtt_floor_ms` | 30 | 不可由本模型带宽动作消除的时延 |
| `rtt_coeff_ms_mbps` | 15 | 时延随余量变化系数 |
| `loss_floor` | 0.0001 | 丢包底噪 |
| `loss_amp` | 0.01 | 可改善部分 |
| `loss_scale_mbps` | 0.25 | 衰减尺度 |
| `stall_floor` | 0 | 卡顿底噪 |
| `stall_amp` | 0.10 | 可改善部分 |
| `stall_scale_mbps` | 0.30 | 衰减尺度 |
| `buffer_floor_ms` | 100 | 初始缓冲底噪 |
| `burst_mbit` | 1 | 首次需要传输的数据量 |
| `audio_mbps` | 0.128 | 示例附加载荷 |

默认所有QoS profile使用同一组参数，所以模型不会自动认定切GBR有效。离线实验可显式给另一个profile设置不同底噪，但必须标记为仿真假设。正式profile只填写经观测或标定的参数。

第一版 `efficiency` 不再按远中近重复折算；位置已经进入用户权重。后续如加入真实信道效率，应重新评估位置权重，避免重复偏置。

### 5.7 可复算样例

使用默认参数，steady阶段：

```text
业务码率=2000 kbps，音频=0.128 Mbps
目标RTT<=80 ms，丢包<=0.001，卡顿<=0.01

R       = 2.128 Mbps
x_RTT   = 15/(80-30) = 0.300000 Mbps
x_loss  = 0.25×ln(0.01/0.0009) ≈ 0.601986 Mbps
x_stall = 0.30×ln(0.10/0.01)   ≈ 0.690776 Mbps

C = 2.128 + 0.690776 = 2.818776 Mbps
B = 2.818776×1.08/0.85 ≈ 3.581503 Mbps
```

正向校验预测RTT约51.71 ms、丢包约0.000731、卡顿0.01，均满足目标。最终MOS再由对应公式组和聚合函数计算。该样例没有声称MOS一定达到4分。

### 5.8 码率和分辨率一致性

默认 `media_control=false`：候选码率、分辨率固定为当前有效值，优化网络质量目标和profile。

只有应用侧或测试环境支持媒体档位控制时，才开放 `media_profiles` 枚举，例如 `(720p,1500kbps)`。档位来自配置，不能把任意高分辨率与任意低码率拼接以骗取高分。

新意图但未开始业务时，从明确的业务需求档位初始化；不能拿空码率输入计算出可用MOS。

## 6. 用户权重和历史补偿

### 6.1 基础权重

来源保留业务优先级、套餐、位置、容忍度。配置为查表，避免固定套餐高位导致VIP下载压过普通实时业务。

离线组合基础值示例：

| 组合 | base_weight |
| --- | ---: |
| VIP实时 | 6 |
| VIP浏览 | 5 |
| 普通实时 | 4 |
| 普通浏览 | 3 |
| VIP下载 | 2 |
| 普通下载 | 1 |

其他等级在配置中显式添加，未定义组合拒绝配置加载，不默默映射。实时细分、容忍度和位置修正系数均可配；演示默认1以避免未经确认的层级反转。资源回收的严格顺序不依赖这些数值倍数。

```text
w_raw = base_weight × business_factor × tolerance_factor × position_factor
w = w_raw / weight_reference
```

`weight_reference`为固定配置，默认6，不使用本轮样本权重和归一化，避免抽样数量变化改变lambda与beta的意义。

### 6.2 历史MOS

窗口默认300秒，按有效观测时长加权。新用户无历史时令 `MOS_history=MOS_target`，不给虚构历史欠账。只用真实观测更新；一轮求解内固定。

```text
m(a)       = (MOS_predicted(a)-1)/4
m_previous = (MOS_previous_observed-1)/4
debt       = clip((MOS_target-MOS_history)/4, 0, debt_cap)
w_effective= w + eta×debt
```

`debt_cap=1`为离线默认。只补偿低于目标的情况，避免历史高分产生负权重。

此处保留用户的滑动历史方案，不新增虚拟队列。它是历史公平启发式，不宣称具备长期公平的理论保证。

## 7. 小区资源拆分

### 7.1 预算口径

```text
B_available = B_total - B_unmanaged - B_safety
```

所有字段均为同一方向、同一时刻、同一等效速率口径。`B_unmanaged`仅包含未进入角色集合的占用；不得重复扣除已建模普通用户。

### 7.2 先按业务，再按资源类型

1. 浏览/下载：在本版固定最低有效服务预算，不参与普通体验升级；其现有硬保障仍必须保留。
2. 重点Non-GBR：求候选集合中满足基本MOS/KQI的最小资源动作。
3. 重点GBR：底线同时满足基本MOS/KQI与已有不可突破的保障预算。

```text
B_floor_r = max(B_model_min_for_baseline,
                B_contract_protected,
                B_business_min)

B_reclaimable_r = max(0, B_current_r-B_floor_r)
```

当合同下限使最终分配B高于模型最小B时，必须用最终B重新调用forward并计算MOS/H，而不是沿用最小B处的收益。下限和资源类型分别存储。GBR多余资源不是“所有GBR承诺都可以回收”，而是超过仍有效保障的可回收部分。

### 7.3 浏览/下载最低模型

不使用视频MOS。下载最低预算 `max(policy_min, contract_protected)`；浏览可用：

```text
B_browse_min = page_mbit / transfer_budget_seconds
              × (1+overhead_ratio)/efficiency
```

示例默认下载0.10 Mbps、浏览页面0.4 MB、传输预算2秒。仅作仿真配置。浏览渲染和服务器等待不包括在传输预算中。

非重点业务的 `MOS=null`，正常固定底线阶段效用视为常数，不纳入MOS平均值。若紧急降级，以分配比例 `e=min(B/B_floor,1)`构造明确标注的服务效用，不能称作MOS。

### 7.4 预留超过预算：尽力而为模式

```text
B_floor_sum = Σ population_weight_r × B_floor_r
```

若超限，正常底线集合无解，进入 `DEGRADED`：

1. 先回收底线以上的资源。
2. 对 `allow_soft_degrade=true` 的业务，按保障层级从低到高开放低等级候选。
3. 对未准入的新请求提供 `not_admitted` 动作，B=0，状态明确为拒绝，不计算正常MOS。
4. 对已运行且允许暂停的软业务提供 `suspended`动作；记录影响时长。
5. 仍不能容纳受保护配置时返回 `INFEASIBLE_HARD_FLOOR`，输出容量缺口与受影响列表，不生成虚假的可执行保障成功结果。

真实网络无法满足已承诺底线时也需记录违约事实，不能以“尽力而为”隐藏。物理容量检查始终保留。

## 8. 三级动作空间

```text
a_r = (G_r, L_r, theta_r)
```

### 8.1 G：QoS profile

`G`是完整profile标识，包含资源类型、5QI及必要配套参数。使用配置表和允许切换的有向边。5QI是特征标识，不是可直接加减的线性级别；候选映射应核对[3GPP TS 23.501第5.7节](https://www.etsi.org/deliver/etsi_ts/123500_123599/123501/18.05.00_60/ts_123501v180500p.pdf)。

开发模式提供 `demo_non_gbr`、`demo_gbr`，`five_qi=null`、`executable=false`，用于离线求解。现网执行必须提供合法映射，不能自动给虚构5QI。

### 8.2 L：MOS区间

默认演示区间：

| L | 区间 |
| --- | --- |
| L0 | [1,2.5) |
| L1 | [2.5,3.5) |
| L2 | [3.5,4.0) |
| L3 | [4.0,4.5) |
| L4 | [4.5,5.0] |

同一区间内通过theta优化。底线约束可以位于区间内部，例如目标4.2时，L3中4.0的候选仍不可用。不能通过只改L标签提高收益。

### 8.3 theta：目标KQI与媒体档位

允许项包括RTT上限、丢包上限、卡顿上限、初始缓冲上限，以及有执行权限的媒体档位。

连续目标关键点：当前观测、明确约束、业务配置网格；离散档位显式枚举。默认每个维度最多5个值，目标去重并按数值排序。

例如：RTT `[40,60,80,120]` ms，丢包 `[0.0005,0.001,0.005]`，卡顿 `[0.001,0.01,0.05]`。这些为离线采样点，不是业务标准。

### 8.4 候选流程

```text
选择当前及邻接G
→ 枚举可控媒体档位
→ 组合KQI关键点
→ 计算目标KQI的初步MOS与约束
→ 带宽模型反解B
→ 正向重算KQI和MOS
→ 校验硬约束及实际L
→ 计算固定参照的稳定性代价
→ 合并等价候选并Pareto剪枝
```

初步MOS低于底线但可能被正向模型改善时，不能仅依据初步值过早淘汰；只有明确不可达或违反硬性媒体条件时提前拒绝。

### 8.5 控制组合规模

每角色默认原始评估上限2048、保留上限128。实现确定性beam：

1. 先加入锚点、底线搜索点、每维边界和意图点。
2. 在当前theta附近逐维展开，计算可行候选。
3. 每轮保留多样化的非支配候选，再展开其他维度。
4. 达到预算则停止，输出 `candidate_truncated=true`。

底线必须是“已评估候选中的最小可行资源”，不能在截断后宣称连续空间真实最小值。

### 8.6 安全的Pareto规则

在同一合法切换类别、同一软约束状态下，若A满足：

```text
MOS_A >= MOS_B
B_A <= B_B
stability_cost_A <= stability_cost_B
switch_cost_A <= switch_cost_B
```

且至少一个严格优，则A支配B。若附加软KQI目标不同，须把相应缺口也纳入比较。

锚点、底线、紧急候选和profile桥接候选始终保留。不能只用“高MOS、低B”删掉最稳定的当前动作，因为稳定性惩罚可能使当前动作最优。

## 9. 角色效用

### 9.1 原结构的归一化实现

```text
H_r(a) = w_effective_r × m_r(a)
         - beta × abs(m_r(a)-m_previous_r)
         - switch_penalty_r(a)

U_r(a;lambda) = H_r(a) - lambda × B_r(a)/B_reference
```

`B_reference=1 Mbps`为固定默认，lambda为每个参考带宽的效用价格。`switch_penalty=0`保留用户原式；演示可设profile切换0.02，并注明扩展项。

固定默认：`eta=0.5`、`beta=0.1`。数值属于调参起点，权重、MOS和带宽归一化口径均持久化到配置版本。

### 9.2 相比原式的解释

`eta×(MOS_target-MOS_history)`在一次求解中为常数，若直接作为减项，对候选排序没有作用。按用户最终式把它加入MOS系数后，才会提高欠账用户升级的价值。

MOS历史超过目标时本版不负向惩罚，只令补偿为0。

### 9.3 角色最优动作

```text
a_raw_r = argmax[a属于当前可达候选集] U_r(a;lambda)
```

精确并列时依次选择：当前动作、较少profile切换、较低带宽、字典序action_id。确保同一快照与配置下可复现。

权重放大倍数对同一角色所有候选相同，因此独立argmax可以用单人U；全局收益和协调必须乘代表人数。

## 10. 采样人数和全局资源检查

### 10.1 将n/p固定为一种定义

为保留用户式子，本版定义：

```text
n_r = 代表桶中实际被抽中的业务实例数
p_r = 该层样本纳入概率，0<p_r<=1
population_weight_r = n_r/p_r
```

精确用户 `n=1,p=1`；每个样本独立成角色 `n=1,p=该层采样率`。如果某类100人抽10人并聚成一个角色，则 `n=10,p=0.1`，代表100人。

`n_r`不能再解释为总体人数，否则除p会重复放大。同一真实群体不可在多个桶中重复代表。VIP全量使用p=1。

### 10.2 汇总

```text
B_sum = Σ_r population_weight_r × B_r
H_sum = Σ_r population_weight_r × H_r
```

normal模式的非重点角色固定B，仍加入汇总；未建模占用已在预算中扣除，不再加一次。

基础检查：`B_sum <= B_available + numerical_tolerance`。同时检查已提供的每用户聚合上限、每执行流上限、profile准入条件和候选硬约束；它们在独立选择后修复、每次协调动作及最终输出时均需验证，不能留到下发时才检查。同一用户多业务的上限超限时先在该用户内部按相同损失规则降级；底线仍超限则标记不可行。采样聚桶只允许已能保证这些个体约束的同质群体，否则拆桶或禁止聚合。低于预算表示可行，不代表最优或已经收敛。

## 11. 资源协调

### 11.1 定义相邻动作

合法邻接包括：同profile内相邻有效资源档、同profile的相邻L、profile图中的一条允许边。候选表中缺少相邻L时可跳到最近可行L并记录跳过原因。

动作按预测资源B判断升降，不按L编号猜资源方向。若邻接集中没有足够降级动作但全候选有可行解，允许回退到已验证底线候选，避免因邻接过窄产生假不可行。

### 11.2 为什么协调使用H

角色独立选择已经使用lambda。协调阶段需要判断真实体验与稳定性得失，使用 `H`，避免资源已经释放却又被价格项当成额外体验收益。

因此本文将用户协调式中的 `ΔU`具体实现为 `ΔH`。需要调试时同时输出含价和不含价两个差值。

### 11.3 空闲升级

```text
delta_B_total = population_weight × (B_new-B_old)
delta_H_total = population_weight × (H_new-H_old)
ImproveRate = delta_H_total/delta_B_total
```

- 仅接受 `delta_B_total>0`、`delta_H_total>epsilon_gain` 且装得下的动作。
- 先处理不增加资源但H改善的合法动作，避免除0。
- 选择最大ImproveRate，应用后刷新该角色邻接动作。
- 没有正收益可行动作即停，不要求占满资源。
- 同质角色不允许随意取分数人数。允许拆桶时先拆成可执行子群，再重新计算资源，不能虚构“0.3个用户升级”。

### 11.4 超限降级

```text
released_B_total = population_weight × (B_old-B_new)
lost_H_total = population_weight × (H_old-H_new)
LossRate = lost_H_total/released_B_total
```

回收顺序严格分阶段：

1. 非重点业务已高于固定底线的多余资源。
2. 当前周期开始时Non-GBR角色的底线以上资源。
3. 当前周期开始时GBR角色的底线以上资源。
4. 仅仍超限时，进入第7.4节软降级或不可行处置。

在每阶段选择最小LossRate；负损失优先，表示回收还改善了H。阶段资格以真实锚点资源类型固定，避免内部先改成Non-GBR再绕开保护。

底线以上回收不按MOS简单排序。若明确业务优先级要求先牺牲某等级，则先按保护层级筛选，在同层内再按损失率选择。

### 11.5 离散资源满载的补充

若资源已满但存在更优的资源交换，允许在同一回收规则下先降一个角色，再升另一个角色：总H必须增加、总资源不超限、底线不破坏。每轮最多评估64个高收益交换对，耗时纳入预算。这是对纯增减步骤的有限补充，不保证全局最优。

## 12. 影子价格更新

记录两个资源量：

```text
B_raw_k      独立最优动作的总需求，协调前
B_feasible_k 协调后的可行总分配
```

使用：

```text
lambda_next = max(0,
    lambda + gamma_k × (B_raw_k-B_available)/B_reference)

gamma_k = gamma0/sqrt(k+1)
```

默认 `gamma0=0.05`，初始lambda0.1，可用上一有效周期价格热启动。归一化后lambda不会因Mbps与kbps单位变化改变意义。

必须使用 `B_raw_k` 更新；若使用修复到预算以下的 `B_feasible_k`，需求再紧张也无法正确涨价。

价格更新可以导致下一轮原始需求振荡，因此仍保留最佳可行解、迭代上限和循环检测。不能只凭这个更新式声称收敛保证。

## 13. 收敛与终止

### 13.1 三项稳定性

比较本轮与上轮“协调后”的动作：

```text
strategy_change = count(action_id_new != action_id_old)/R
resource_change = abs(B_feasible_new-B_feasible_old)/max(B_available,1)
utility_change  = abs(H_sum_new-H_sum_old)/max(1,abs(H_sum_old))
```

动作是结构体，指示函数使用“不等于”，不计算向量相减后再含糊取1。效用收敛使用H，而不是随lambda变化的U。

默认 `epsilon_strategy=0.01`、`epsilon_resource=1e-4`、`epsilon_utility=1e-4`，连续3轮满足。

### 13.2 必须同时成立的条件

- 三项稳定性满足。
- 资源不超限。
- 有效硬约束通过。
- 已评估邻接中没有可行正收益升级或已发现的正收益交换。

此时返回 `CONVERGED_LOCAL`，表示启发式局部稳定，不表示全局最优或对偶最优。

### 13.3 强制终止

默认最多50轮、求解时间预算2000ms（目标值，需压测，不是性能承诺）。出现非连续往复的动作向量（例如A→B→A）可返回 `STOPPED_CYCLE`。连续保持相同动作是稳定候选，应等待连续3轮判定，不触发循环退出。

超时或到上限返回最佳已知可行解，状态为 `TIME_BUDGET` 或 `MAX_ITERATIONS`。若根本没找到可行解，返回对应不可行状态及原因，禁止用超预算raw结果代替。

## 14. 第一轮初始化

### 14.1 已有业务

冻结当前G、媒体档位和观测KQI，确定当前L。首轮仅在当前G、当前L及当前KQI邻域搜索，得到可行Pareto候选，不直接跳到全空间最高MOS。

维护两个锚点：

- `observed_anchor`：真实配置与真实观测，用于稳定性参照和回退。
- `modeled_anchor`：同一实际配置在本模型中的预测，用于候选之间公平比较。

真实锚点即使未处于Pareto前沿也不删除。其建模版本固定使用实际B调用forward，不能用inverse重新估计B后仍称为“保持原配置”；若实际B不足以满足模型的最小余量，标记预测不可用并保留为诊断/有条件回退记录，不把它当成通过模型约束的候选。模型预测误差较大时降低可调整幅度并记录差异；不能把实测当前MOS与过度乐观的候选预测无说明混合比较。

### 14.2 当前已经不达标

仍从当前附近开始，但允许为了满足基本约束扩展到相邻L/G。必须给出 `baseline_unmet=true`，不能因“只在当前区间”永久禁止修复。

### 14.3 首次意图

其他业务全部保持真实状态；意图只增加本业务目标和约束。已有该业务则保留其真实锚点；全新业务使用configured媒体需求和 `not_admitted`作为初始状态。

新请求未准入前不计为已经满足保障，执行成功并获得观测后才进入历史统计。

## 15. 数据契约

### 15.1 CellSnapshot

```json
{
  "schema_version": "1.0",
  "snapshot_id": "cell_A_epoch_001",
  "cell_id": "cell_A",
  "direction": "downlink",
  "control_epoch": 1,
  "observed_at": "2026-09-09T13:00:00Z",
  "bandwidth_total_mbps": 20.0,
  "unmanaged_mbps": 1.0,
  "safety_mbps": 0.5,
  "config_version": "demo_v1",
  "model_version": "analytic_bandwidth_v1",
  "roles": []
}
```

### 15.2 RoleState

```json
{
  "role_id": "user_001_meeting_01",
  "user_id": "user_001",
  "business_id": "meeting",
  "is_key_business": true,
  "sample_count": 1,
  "inclusion_probability": 1.0,
  "formula_profile": "P4",
  "stream_phase": "steady",
  "current_profile_id": "demo_non_gbr",
  "current_bandwidth_mbps": 4.0,
  "kqi_observed": {
    "bitrate_kbps": 2000,
    "resolution": 720,
    "rtt_ms": 80,
    "loss_ratio": 0.001,
    "stall_ratio": 0.01
  },
  "mos_observed": 3.8,
  "mos_observation_source": "example_external_measurement",
  "mos_history": 3.7,
  "mos_target": 4.0,
  "mos_baseline": 4.0,
  "weight_raw": 6.0,
  "contract_protected_mbps": 0.0,
  "bandwidth_max_mbps": 10.0,
  "allow_soft_degrade": false,
  "media_control": false,
  "intent_version": 1,
  "history_valid_seconds": 300
}
```

示例MOS是外部观测占位值，不声称等于表内KQI经临时聚合后的计算结果。真实输入需保存来源；若服务使用统一公式重算MOS，应另存 `mos_recomputed`并标记版本。

### 15.3 CandidateAction

必填字段：

```text
role_id, action_id, profile_id, level_id, theta_target,
media_profile_id, predicted_kqi, predicted_mos,
bandwidth_mbps, baseline_satisfied, hard_constraints_satisfied,
stability_cost, switch_cost, H, U,
executable, model_confidence, model_version, rejection_reasons
```

action_id由规范化profile、媒体档位、目标值生成稳定散列。浮点目标以配置精度量化后再生成ID，不能每次序列化产生不同动作。

### 15.4 SolveResult

```text
solve_id, snapshot_id, status, best_iteration, elapsed_ms,
raw_demand_mbps, allocated_mbps, available_mbps, lambda_final,
objective_H_total, constraint_violations, unmet_targets,
candidate_truncated, convergence_metrics,
role_decisions[], model_versions, executable
```

角色决策包含代表人数、旧/新profile、旧/新预算、预测KQI/MOS、目标缺口、动作原因和执行映射标识。所有结果必须区分 `predicted` 与 `observed`。

## 16. 模块接口与代码结构

```text
experience_solver/
  schemas.py              输入输出类型与单位校验
  config.py               配置版本、profile和业务映射
  mos_model.py            用户KQI公式及可替换总聚合
  bandwidth_model.py      解析反解与正向复核
  priority.py             用户权重和保护层级
  history.py              滑动窗口MOS，只读快照
  baseline.py             底线及尽力而为处置
  candidates.py           三级动作、邻接、beam
  pareto.py               带稳定性成本的剪枝
  utility.py              H和U的计算
  coordinator.py          升级、分阶段回收、有限交换
  solver.py               价格迭代和终止
  execution_adapter.py    配置差分、版本校验及反馈接口
  tests/                  公式与约束验收
```

关键接口：

```python
class MosModel:
    def evaluate(self, kqi, formula_profile, stream_phase): ...

class BandwidthModel:
    def inverse(self, target_kqi, qos_profile, context): ...
    def forward(self, bandwidth_mbps, media_profile, qos_profile, context): ...

class CandidateBuilder:
    def build(self, role, snapshot, expansion_scope): ...

class ResourceCoordinator:
    def repair_and_upgrade(self, raw_actions, candidates, snapshot): ...

class Solver:
    def solve(self, snapshot, previous_solver_state): ...
```

第一版纯函数模型和单进程确定性求解即可；不引入额外记忆框架或复杂分布式依赖。库版本由开发项目锁定。

## 17. 完整调度伪代码

```text
solve(snapshot, previous_state):
    validate_snapshot_units_versions_and_population(snapshot)
    freeze_current_observations_and_history()
    budget = total - unmanaged - safety
    if budget < 0:
        return INVALID_CAPACITY_INPUT

    policies = build_weights_and_protection_order()
    candidate_pool = build_anchor_and_baseline_candidates()
    baseline_result = find_best_known_baseline(candidate_pool)

    if baseline_result is infeasible:
        candidate_pool = enable_authorized_emergency_candidates()
        baseline_result = attempt_best_effort()
        if still infeasible:
            return INFEASIBLE_HARD_FLOOR with gap and affected roles

    best_feasible = baseline_result
    lambda = previous_valid_lambda_or_default()
    previous_repaired = none

    for k in range(max_iterations):
        if time_budget_exceeded():
            return best_feasible with TIME_BUDGET

        scope = anchor_neighborhood if k == 0 else next_allowed_neighborhood
        expand_candidates_within_budget(scope)
        validate_model_forward_and_prune_safely()

        raw = independently_argmax_U(candidate_pool, lambda)
        B_raw = population_weighted_bandwidth(raw)

        repaired = repair_overload_in_protection_order(raw)
        if repaired is infeasible:
            record_reason()
            repaired = best_feasible
        else:
            repaired = accept_positive_gain_upgrades_that_fit(repaired)
            repaired = try_bounded_positive_gain_exchanges(repaired)

        assert resource_and_hard_constraints_hold(repaired)
        best_feasible = select_better_by_H(best_feasible, repaired)

        lambda_next = max(0, lambda + gamma(k)*(B_raw-budget)/B_reference)

        metrics = compare_repaired_with_previous(repaired, previous_repaired)
        if stable_for_required_rounds(metrics) and no_evaluated_profitable_move():
            return best_feasible with CONVERGED_LOCAL
        if nonconsecutive_action_cycle_detected():
            return best_feasible with STOPPED_CYCLE

        previous_repaired = repaired
        lambda = lambda_next

    return best_feasible with MAX_ITERATIONS
```

`best_feasible`按固定H比较，不能按随lambda变化的U比较。紧急状态采用先最少高保护层级违约、再最大H的字典序比较，避免高收益掩盖更多高等级失保。

## 18. 执行与反馈

### 18.1 只输出可执行动作

KQI是目标或预测，不直接作为网络指令下发。执行适配器将预算和profile映射为真实系统支持的参数；无媒体控制接口时不得下发分辨率/码率动作。

离线demo结果 `executable=false`，仍可以完整验证优化流程。

### 18.2 提交顺序与并发

- 一个小区—方向—有效时间窗口只允许一个决策版本提交。
- 求解使用快照，不全程锁住用户意图。
- 提交前校验意图版本与网络配置版本，冲突则重算。
- 需要回收后升级时，先确认回收成功再释放预算供升级；部分执行失败重新核算，不盲目执行后续动作。
- profile切换及媒体切换遵守冷却期，默认30秒为演示值。

### 18.3 反馈

记录命令确认、实际生效时间、观测覆盖、实测KQI/MOS、预测误差。历史窗口只在实际观测到达后更新。

带宽模型更换或MOS聚合版本改变时，旧历史不能直接混入新评分尺度；重算可重算的观测或分版本维护。

## 19. 开发验收用例

| 编号 | 输入/场景 | 必须满足 |
| --- | --- | --- |
| T01 | bitrate=0 | 单项原式=2.5；unserved不得计为正常达标 |
| T02 | RTT=0，loss=0，stall=0 | 对应评分为5 |
| T03 | 负值、NaN、丢包率=2 | 输入校验拒绝 |
| T04 | P1～P5参数 | 与附件各组公式逐项一致 |
| T05 | 初始流内部迭代多轮 | 交互评分不因k改变而切换 |
| T06 | 第5.7节目标 | B约3.581503 Mbps；正向全部满足 |
| T07 | RTT目标<=底噪 | 不可达，不出现除零或负B |
| T08 | loss目标趋近底噪 | 所需B不减；超上限拒绝 |
| T09 | 同一固定业务增加B | 解析模型RTT/loss/stall不增 |
| T10 | 不支持媒体控制 | 不能生成分辨率或码率切换 |
| T11 | 高MOS低B但切换成本大 | 不错误剪掉稳定锚点 |
| T12 | 当前G/L首轮 | 首轮不跳全空间；不达标修复例外可追踪 |
| T13 | 总体100抽10聚1桶 | multiplier=100，而非1000 |
| T14 | 样本升级1Mbps、代表100人 | 扣100Mbps，不扣1Mbps |
| T15 | Non-GBR余量够 | 不回收GBR余量 |
| T16 | Non-GBR余量不够 | 再考虑GBR底线以上部分 |
| T17 | 所有受保护底线超预算 | 返回明确不可行，不报成功 |
| T18 | 没有正收益升级 | 允许预算剩余，不强行填满 |
| T19 | raw超限、修复后可行 | lambda仍按raw需求上涨 |
| T20 | raw低需求、lambda接近0 | 更新不变负 |
| T21 | H固定但lambda变化 | 不因U价格变化误判体验不收敛 |
| T22 | 超时或动作循环 | 返回已验证的最佳可行方案 |
| T23 | 新请求与历史欠账 | 无历史初始补偿为0 |
| T24 | 重复运行相同输入配置 | action_id、选择顺序和结果一致 |
| T25 | 部分回收执行失败 | 不超量继续升级，触发重新核算 |
| T26 | 非重点GBR业务 | 只计一次资源，合同保护仍有效 |
| T27 | 媒体档位与KQI组合 | 不生成无效高分辨率低码率组合 |
| T28 | 群体不可拆、剩余容量不足 | 不做分数用户升级 |

对小规模角色数（例如3角色、每角色≤5候选）使用穷举作为离线对照：检查可行性和收益差距，不要求启发式总能等于最优。目标是发现剪枝、资源权重及协调实现错误。

## 20. 开发任务拆分与完成定义

### 阶段A：模型与数据

实现schema、单位校验、公式组、临时聚合、带宽正反模型；完成T01～T10。产物应能给单一候选输出B、KQI、MOS及不可达原因。

### 阶段B：候选与角色效用

实现锚点初始化、三级动作、受限展开、Pareto、固定历史补偿、H/U。完成稳定性参照和采样倍率测试。

### 阶段C：全局协调

实现底线拆分、Normal/Degraded两种模式、分阶段回收、升级、价格更新和三项终止。每次返回前自动执行资源与硬约束校验。

### 阶段D：反馈接口与对照

实现配置差分、版本校验、执行结果记录；通过小规模穷举对照和代表业务仿真，输出收益、预算、目标缺口与耗时。

第一版完成条件：同一快照能够稳定输出可解释的候选选择，容量从不被成功结果突破，非法目标显式拒绝，所有人为模型参数和临时MOS聚合均可配置替换。

## 21. 默认配置汇总

```yaml
mode: offline_demo
mos:
  unit_profile: demo_v1
  aggregation: weighted_mean_demo
  weights: {quality: 0.3333333333333333, interaction: 0.3333333333333333, view: 0.3333333333333333}
  demo_mapping: true
bandwidth:
  model: analytic_bandwidth_v1
  calibrated: false
  overhead_ratio: 0.08
  efficiency: 0.85
  margin_min_mbps: 0.05
  rtt_floor_ms: 30
  rtt_coeff_ms_mbps: 15
  loss_floor: 0.0001
  loss_amp: 0.01
  loss_scale_mbps: 0.25
  stall_floor: 0
  stall_amp: 0.10
  stall_scale_mbps: 0.30
  buffer_floor_ms: 100
  burst_mbit: 1
  audio_mbps: 0.128
utility:
  weight_reference: 6
  bandwidth_reference_mbps: 1
  eta: 0.5
  beta: 0.1
  switch_penalty: 0
  debt_cap: 1
  history_window_seconds: 300
solver:
  lambda_initial: 0.1
  gamma0: 0.05
  max_iterations: 50
  time_budget_ms: 2000
  raw_candidates_per_role: 2048
  kept_candidates_per_role: 128
  exchange_pairs_per_iteration: 64
  stable_rounds: 3
  epsilon_strategy: 0.01
  epsilon_resource_relative: 0.0001
  epsilon_utility_relative: 0.0001
  epsilon_gain: 0.000001
  numerical_tolerance_mbps: 0.000001
  profile_cooldown_seconds: 30
```

## 22. 与后续真实模型的替换边界

未来获得实测数据后，优先替换 `BandwidthModel.inverse/forward`，保留角色、候选、协调与终止接口。若新模型存在跨用户耦合，则单角色缓存不再可靠：每次动作后必须重新预测受影响角色，资源协调也要使用全局H差值。第一版明确采用冻结环境下的可分离近似，不隐含支持该耦合。

正式MOS聚合接入时，只替换总聚合适配器并更新模型版本、历史口径；不要修改用户已经提供的单项公式来“凑到目标分”。

该方案最终形成的闭环是：**真实状态定锚点，KQI候选经模型转成资源需求，角色效用提出偏好，全局协调保证预算，真实反馈再校正模型与历史。**
