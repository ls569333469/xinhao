# V2.8 SIM-01 加深 · T1/T2/T3 分档保证金预算硬闸

日期：2026-10-13。状态：**部分实现**（仍非 SIM-01 全验收）。

## 本刀交付

- 配置：`budget_t1_u` / `budget_t2_u` / `budget_t3_u`（默认 400/300/300，可 `PUT /api/sim/config` 持久化）
- `get_config().tier_budgets`：分档上限摘要 + `hard_gate` + 说明
- **硬闸**：开仓 / 自动填仓在写入前校验「该档已占用 margin + 本次 margin ≤ 档位上限」
  - 超限 → `Conflict`，文案含档位、已用、本次、上限、config 键；**不静默截断**
  - 恰等于上限 → 允许（at）
- 档位解析：`body.tier` / `strategy_tier` → 版本 `rules.tier` / `base_template_id` → `strategy_id`（T1/T2/T3）
- 自动填仓：**必须**能解析档位，否则拒绝
- 手动开仓无档位：跳过硬闸（仍受现金预算/同币 reject/守恒）
- 持仓与事件写入 `strategy_tier`；F03 门禁与同币 reject / 守恒不变
- 健康：`capabilities.sim.tier_budget_hard_gate=true`
- 测试：`test_sim_store` 分档 under/at/over + 档间独立；`test_sim_autofill` 解析/超限/缺档位

## 仍缺

- 最小名义与精度硬闸
- 暂停新入场仍跟踪旧仓的完整调度闭环
- 分档 UI 编辑器与 CONFIG 导出双向同步（本刀仅 SIM config，CONFIG-friendly 字段已可持久）

备份：`runtime/code-backups/sim-tier-budget-*`
