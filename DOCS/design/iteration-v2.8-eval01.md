# V2.8 EVAL-01 第一刀 · 前向观察与结算

日期：2026-10-13。状态：**部分实现**（非 EVAL-01 全验收）。

## 交付

- `eval_store.py`：`runtime/eval/` 下观察 jsonl + 不可变结算 jsonl + state；观察期与持仓期限分字段存储。
- 歧义规则（代码+本文档固定）：
  - 同根 TP&SL → `worse_for_trader`（按止损）
  - 跳空穿过 → `fill_at_open`；无后续可得 K → `unverifiable`
  - 断档/无 K → `data_gap`，不计假赢
- 成绩：`sample_count==0` 时胜率标签为「无样本」，`win_rate=null`，不填 0%。
- API：`GET /api/eval/rules|observations|settlements|grades`，`POST /api/eval/observations/start`，`POST .../settle|close`。
- UI：验证与复盘页「前向观察与结算」面板（prototype /eval-runtime.js）。
- 测试：`test_eval_store.py`（期限分离、无假胜率、同根/跳空、重启回放）。
- `price_index.merge_series(..., include_bars=True)` 供规则撮合取 OHLC。

## 仍缺（诚实）

- 自动按冻结规则扫市场开观察 / 观察期调度停新入场。
- 持仓区间补查任务联动（DATA-01 backfill 触发）。
- 资金费按持仓真实结算、与 SIM 账本自动开平联动。
- unverifiable 在「穿过后无下一根」路径的更细用例；短线 interval 自适应。
- 历史回测入口（BACKTEST-01）；AI/数据复盘首刀见 iteration-v2.8-review01.md（REVIEW-01 部分实现）。

备份目录：`runtime/code-backups/eval01-*`。
