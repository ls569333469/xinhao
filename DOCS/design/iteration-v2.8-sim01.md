# V2.8 SIM-01 第一刀 · 纸面账户与事件账本

日期：2026-10-13。状态：**部分实现**（非 SIM-01 全验收）。

## 交付

- `sim_store.py`：共享 1000U 篮子账户 + 可按冻结版本创建独立对照账户；费用/滑点/杠杆上限等持久配置；追加写入 `runtime/sim/events.jsonl`；`state.json` 原子保存余额与持仓。
- 同币冲突策略：**reject**（共享篮子拒绝第二次开仓，不排队）；独立对照互不影响。
- 开平仓取价：优先 `scan.json` 的 `markPrice`/`price`；缺失则明确报错，**不静默按 0**。
- 预算：保证金+费用不得超过可用现金。
- 资金守恒：`cash + margin_locked + fees_paid - realized_pnl_gross == initial_cash`（每次成交后校验）。
- API：`GET /api/sim/accounts|config|events`，`PUT /api/sim/config`，`POST /api/sim/events/open|close|stub`，`POST /api/sim/accounts/independent`。
- UI：模拟篮子页「纸面账户与事件账本」；可手动模拟开/平仓；中文；标明研究/模拟非实盘。
- 测试：`test_sim_store.py`（重启恢复、重复事件、超预算、守恒、同币冲突、缺价）。

## 仍缺（诚实）

- 自动按冻结规则触发/候选排队/观察期（仍无执行引擎）。
- 资金费按持仓周期真实结算、跳空/同根止盈止损歧义处理 → **EVAL-01 首刀已落地**（见 iteration-v2.8-eval01.md）。
- ~~T1/T2/T3 分档预算硬约束~~（见 iteration-v2.8-sim01-tier-budget.md）；最小名义与精度、暂停新入场仍跟踪旧仓。
- 独立对照曲线与共享篮子成绩的完整对比视图；前向观察调度。
- 历史回测 / 记录回放入口成绩分离。

备份目录：`runtime/code-backups/sim01-*`。


## 加深（确认信号自动填仓）

2026-10-13 21:37 CST：见 [iteration-v2.8-sim01-autofill.md](iteration-v2.8-sim01-autofill.md)。

## 加深（分档预算硬闸）

2026-10-13 22:39 Asia/Shanghai：见 [iteration-v2.8-sim01-tier-budget.md](8052-iteration-v2.8-sim01-tier-budget.md)。
