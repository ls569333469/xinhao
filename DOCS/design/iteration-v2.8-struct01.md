# V2.8 STRUCT-01 首刀（部分实现）

## 范围

在 DATA-01 `price_index` 合并 OHLCV 之上，提供只读结构指标计算：

| 项 | 公式 ID | 周期/预热 | 说明 |
|---|---|---|---|
| ATR | `ATR_WILDER_v1` | period=14；预热 period+1 | Wilder；不足 → `insufficient_history`，不给伪数 |
| ADX | `ADX_WILDER_v1` | period=14；预热 2×period+1 | Wilder +DI/−DI/ADX |
| 区间高点 | `RANGE_HIGH_WINDOW_v1` | 可得窗口内 max(high) | **永不**标可信ATH（无全历史核验源） |
| 日线箱体 | `DAY_BOX_LOOKBACK_v1` | lookback=20（仅 1d） | 可得窗口末 lookback 根；4h → `wrong_interval` |
| 量增价平 | `ABSORPTION_VOL_PRICE_WEAK_v1` | 弱证据 | 合约量启发式；`confirmed_absorption=false` |

参数版本：`struct-params-v1`（显式冻结，接口拒绝覆盖）。

## API

- `GET /api/struct/catalog` — 公式目录、冻结参数、ATH 政策
- `GET /api/struct/list?interval=1d&limit=40` — 目录级预热提示
- `GET /api/struct/compute?market=&symbol=&interval=` — 合并后计算；拒绝静默 NaN

## UI

原型「指标与策略」页注入 `struct-runtime.js` 面板：覆盖/预热不足提示、区间高点标签、弱证据标记。

## 明确不做

- ~~不启动 BACKTEST-01 全引擎~~ → 过滤闸已挂接（`backtest_hook.filter_gate=true`）；完整结构策略引擎仍未齐
- 不联网 Binance 拉盘；不付费 AI
- 不宣称持续吸筹或可信ATH

## 验收对照（首刀）

- [x] 每项公式/周期/预热/覆盖/版本明确
- [x] 不满足全历史条件只标区间高点
- [x] 规则参数显式冻结
- [ ] 主动成交（taker）真实源 — 未接，仅弱量价启发
- [ ] 持续吸筹证据链 — 未验证，仅弱证据
- [ ] 可信ATH 来源核验 — 未接

## 离线测试

`test_struct_indicators.py`：合成K线覆盖预热不足、ATR/ADX 有限数、区间高点标签、冻结参数、箱体周期校验。

## 加深（回测过滤闸）

2026-10-13 22:49 Asia/Shanghai：见 [iteration-v2.8-struct-backtest-gate.md](8055-iteration-v2.8-struct-backtest-gate.md)。
