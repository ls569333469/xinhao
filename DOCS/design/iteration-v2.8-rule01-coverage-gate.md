# V2.8 RULE-01 加深刀 · 启用前覆盖硬闸

## 范围

策略声明的 `data_dependencies` 映射到 `coverage_gate` 字段；**启用 / 冻结 / 绑定**前要求状态为 `covered`：

- `partial` / `unknown` / 无映射 → **拒绝**
- 复用 `coverage_gate`，不另维护松散字段表
- 不宣称六历史指标 / ATH / OI·市值·账户历史齐全
- 市场绑定成功后提供只读筛选指纹 `GET .../bindings/market/filter`

## 依赖映射（摘）

| data_dependencies | coverage 字段 |
|---|---|
| price_4h / price_1d | ohlcv_4h / ohlcv_1d |
| oi / account_ratio / funding | oiUsd / accountRatio / fundingAprPct |
| ema / volume_price | ema4h+closed4h / volumeRatio+price |
| snapshot | scan.json / revisions（特殊） |
| netflow / ath | 无闸门字段 → 拒绝 |

## API

- `GET /api/strategies/coverage-gate?deps=` 或 `?version_id=`
- `GET /api/strategies/coverage-gate/map`
- `GET /api/strategies/bindings/market/filter` — 只读冻结指纹
- 既有 `POST .../enable`、`.../freeze`、`.../bindings/*` 走硬闸

## 验收

- [x] 声明 oi 且 oiUsd partial → 启用 409
- [x] ath 无字段 → 拒绝
- [x] 复用 coverage_gate
- [x] 市场页强制只读消费 filter（UI 全接）
- [x] 报告导出强制写入 fingerprint


## 文件

- `rule_coverage_gate.py` — DEP_FIELD_MAP + `assert_deps_covered`
- `coverage_gate.py` — ema4h 支持 list/tuple
- `strategy_store.py` — `check_dependencies` 硬闸；`freeze`/`set_enabled` 拒绝
- `rule_bindings.py` — bind 前硬闸；`market_filter` 只读指纹
- `server.py` — coverage-gate / market/filter API
- `test_rule_coverage_gate.py`

## 不做宣称

- 不宣称六历史指标齐全
- 不宣称 ATH / OI·市值·账户历史齐全
- covered 只表示当前扫描/本地 K 线闸门字段可得，不是历史完备


## 加深（2026-10-13 21:50 CST）

见 [iteration-v2.8-rule01-export-fingerprint.md](iteration-v2.8-rule01-export-fingerprint.md)。
