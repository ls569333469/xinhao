# V2.8 BACKTEST-01 首刀（部分实现）

## 范围

在本地 `price_index` 合并 OHLCV 上，对**一个冻结策略版本 × 一个合约 × 请求时间窗**做 bar-walk 历史回测：

- 入场规则（首刀）：`close_above_prev_high` / `close_below_prev_low` / `flat_reenter`（测试用）
- 出场：沿用 EVAL-01 同根不利、跳空开盘价、断档 data_gap
- 费用/滑点：沿用 SIM-01 `fee_rate` / `slip_rate`
- 可选训/验/测：**按时间切分**，禁止打乱泄漏
- 样本不足 → `status=insufficient`，胜率标「无样本」
- **禁止**当今市值回填；报告生存者偏差 / 仅本地可得K线

参数哈希写入 run 记录；落盘 `runtime/backtests/`。

## API

- `GET /api/backtest/catalog` — 入场规则、歧义规则、覆盖警告、诚实边界
- `POST /api/backtest/run` — 启动回测（可传 `bars` 仅供离线测试）
- `GET /api/backtest/runs` — 列表
- `GET /api/backtest/runs/{run_id}` — 详情

## UI

原型「验证与复盘」注入 `backtest-runtime.js`：启动回测、列表、覆盖警告（生存者偏差 / 仅本地K线）。

## 明确不做

- 不宣称全市场/全历史宇宙回测
- 不联网币安批量拉盘；不付费 AI
- 不重建历史币池/退市名单（仅警告）
- STRUCT 过滤钩子 `struct_hook.ready=false`
- ~~多策略公平比较矩阵属后续~~ → 见 [iteration-v2.8-backtest01-compare.md](8021-iteration-v2.8-backtest01-compare.md)（矩阵批次已接；历史宇宙重建仍缺）

## 验收对照（首刀）

- [x] 无未来数据入场（信号只用当前及以前K线）
- [x] 无当今市值回填
- [x] 报告生存者偏差 / 仅本地可得K线 / 费用与排除数量
- [x] 训/验/测时序切分；不足样本 clear status
- [ ] 历史币池/退市完整覆盖重建
- [ ] 多币/多版本公平比较
- [ ] 结构策略强制 STRUCT-01 过滤

## 离线测试

`test_backtest_store.py`：未来泄漏、空样本、同根不利、跳空、费用滑点、切分持久化、市值拒绝。

## 加深（公平对照）

2026-10-13 22:45 Asia/Shanghai：见 [iteration-v2.8-backtest01-compare.md](8021-iteration-v2.8-backtest01-compare.md)。
