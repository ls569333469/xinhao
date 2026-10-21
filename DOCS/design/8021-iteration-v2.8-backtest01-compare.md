# V2.8 BACKTEST-01 加深 · 多合约/多版本公平对照（首刀）

日期：2026-10-13。状态：**部分实现**（仍非 BACKTEST-01 全验收）。

## 本刀交付

- `POST /api/backtest/compare`：同批多 `symbols` × 多 `strategy_version_ids`
- **共享**：费用/滑点、EVAL 歧义规则、训/验/测比例 → **绝对时间切点**（intersection 窗口，或请求 start/end）
- **分格报告**：status / trades / win_rate_label / exclusions（insufficient、gap、conflicted）/ reject_reason
- 生存者偏差与 `historical_listed_universe_rebuild` **显式缺口**（不重建历史上市宇宙）
- 保持 F01 入场根可出场；F04 无 entry_rule 的 T* 版本不静默套通用规则（对照格 `rejected`）
- 禁止当今市值回填
- `GET /api/backtest/compares`、`GET /api/backtest/compares/btc-…`
- UI：对照合约/多选版本 →「公平对照批次」+ 矩阵表
- 测试：`BacktestCompareTests`

## 仍缺（诚实）

- 历史上市/退市币池全量重建与当时可得宇宙
- STRUCT 过滤接入公平矩阵
- 多币资金费/账户级组合回测

备份：`runtime/code-backups/backtest-compare-*`

## 加深（STRUCT 过滤闸）

2026-10-13 22:49 Asia/Shanghai：对照/单跑可传 `struct_filter`；见 [iteration-v2.8-struct-backtest-gate.md](8055-iteration-v2.8-struct-backtest-gate.md)。
