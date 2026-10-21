# V2.8 STRUCT×BACKTEST 过滤闸（首刀）

日期：2026-10-13。状态：**部分实现**（不宣称 STRUCT/BACKTEST 完成）。

## 交付

- `struct_indicators.evaluate_struct_gate` / `resolve_struct_gate_config`
- 模式：`off` | `optional` | `required`
- `required` 失败 → 单元格 `insufficient` / `excluded_struct` / `unimplemented`，**明文 exclusions**，不静默丢弃
- 参数缺失（required 但无 require）→ `unimplemented`，不回落通用价格规则
- 禁止要求可信ATH；`range_high` 永不为 ATH 完备
- 挂接：`POST /api/backtest/run` 与 `POST /api/backtest/compare`（`struct_filter`）
- catalog：`struct_hook.ready=true`，`backtest_hook.filter_gate=true`
- UI：回测面板「STRUCT过滤(ATR+ADX必过)」
- 测试：`StructGateTests` + `BacktestStructGateTests`

## 仍缺

- 完整结构策略入场引擎（箱体突破路径等）
- 可信ATH / 全历史核验源
- 主动成交真实源吸筹

备份：`runtime/code-backups/struct-backtest-gate-*`
