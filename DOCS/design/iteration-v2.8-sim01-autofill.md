# V2.8 SIM-01 加深刀 · 确认信号自动填仓

## 范围

在已有纸面开仓账本上，增加「确认信号 → 预览/填仓」闭环：

- `plan_autofill`：只估算名义/保证金/费用，**不写账本**
- `autofill(confirm=true)`：复用 `open_position` 落真实事件；同币仍 **reject**
- 仓位名义默认按 `risk_per_trade_pct`×可用现金；可显式 `notional_u`
- 取价仍走 `scan.json` mark；缺价报错，不填 0
- 可选冻结版本白名单（allow/deny）；可接 RULE `experiment` 绑定版本
- **不编造余额**；研究/模拟非实盘

## API

- `POST /api/sim/autofill/preview` — 预览
- `POST /api/sim/autofill` — 确认填仓（须 `confirm=true`）

## UI

模拟篮子页：合约/方向/版本 → 预览 → 确认填仓。

## 明确不做

- 不自动扫描全市场下单
- 不排队同币仓
- 不接资金费周期结算（仍属后续）
- 不把启用策略当成实盘

## 验收对照

- [x] 预览不改账本
- [x] 确认后走真实 open 事件与守恒
- [x] 同币第二次 reject
- [x] 白名单 allow 外拒绝
- [x] T1/T2/T3 分档预算硬闸（见 iteration-v2.8-sim01-tier-budget.md）
- [ ] 观察期自动调度触发
