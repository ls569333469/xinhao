# 字段对账：A布局+C专业表 · 第一步

- 对账时间：2026-10-14（Asia/Shanghai）
- 快照：`scan-3fc0b6f494394e3c95ffdfe7b345f1cb` / r1 · 588 行
- 数据源：`GET http://127.0.0.1:8768/api/scan`（只读）
- 前端绑定：`design/pro-table-runtime.js` + 既有 `StrategyRegistry.signals` / `hitCount`/`knownCount`

## 默认 11 数据列

| 列 | 后端字段 | 状态 |
|---|---|---|
| 流通市值 | `capUsd` | 一致/已接 |
| 命中数 | `hitCount` | 一致/已接 |
| 可算数 | `knownCount` | 一致/已接 |
| 六项状态 | `signals` | 一致/已接 |
| 本合约OI | `oiUsd` | 一致/已接 |
| ①OI/市值 | `oiCapPct` | 一致/已接 |
| ②账户比 | `accountRatio` | 一致/已接 |
| ③费率 | `fundingPct` | 一致/已接 |
| ③年化 | `fundingAprPct` | 一致/已接 |
| ③周期 | `fundingHours` | 一致/已接 |
| ④收盘 | `closed4h` | 一致/已接 |
| ④EMA | `ema4h` | 一致/已接 |
| ⑤净流入 | `signals[4]/chainStatus` | 一致/已接 |
| ⑥量倍数 | `volumeRatio` | 一致/已接 |
| ⑥同窗价变 | `priceChangeWindowPct` | 一致/已接 |
| 24h涨跌 | `priceChange24Pct` | 一致/已接 |
| 24h成交额 | `volume24Usd` | 一致/已接 |

## 其余 15 可配列

| 列 | 后端字段 | 状态 |
|---|---|---|
| 大户账户比 | `topAccountRatio` | 一致/已接 |
| 大户持仓比 | `topPositionRatio` | 一致/已接 |
| 标记价格 | `markPrice` | 一致/已接 |
| 指数价格 | `indexPrice` | 一致/已接 |
| 24h最高 | `high24` | 一致/已接 |
| 24h最低 | `low24` | 一致/已接 |
| 单根量倍数 | `barVolumeRatio` | 一致/已接 |
| 观察窗成交额 | `volumeWindowUsd` | 一致/已接 |
| 基准窗成交额 | `volumeBaselineUsd` | 一致/已接 |
| EMA7 | `ema4h[0]` | 一致/已接 |
| EMA25 | `ema4h[1]` | 一致/已接 |
| EMA99 | `ema4h[2]` | 一致/已接 |
| 已收盘4h | `closed4h` | 一致/已接 |
| 同资产总OI | `binanceAssetOiUsd` | 一致/已接 |
| 观测时间 | `asOf` | 一致/已接 |

## 证据抽屉扩展字段

| 项 | 后端字段 | 状态 |
|---|---|---|
| 市值项目ID | `capId` | 一致/已接 |
| 市值来源 | `capSource` | 一致/已接 |
| 映射状态 | `capStatus` | 一致/已接 |
| 市值时点 | `capAsOf` | 一致/已接 |
| OI时点 | `oiAsOf` | 一致/已接 |
| 账户时点 | `accountAsOf` | 一致/已接 |
| K线时点 | `candleAsOf` | 一致/已接 |
| EMA方法 | `emaMethod` | 一致/已接 |
| 同资产部分OI | `binanceAssetOiPartialUsd` | 一致/已接 |
| 同资产覆盖 | `assetOiCoverage` | 一致/已接 |
| 周期来源 | `fundingIntervalSource` | 一致/已接 |
| 链上状态 | `chainStatus` | 一致/已接 |
| 执行状态 | `tradeStatus` | 一致/已接 |
| 数据源 | `source` | 一致/已接 |
| 行观测 | `observed_at` | 一致/已接 |

## 缺口清单

### 前端要显示但后端没有

- （无）本轮默认11+15+抽屉核心字段在快照行上均存在；⑤判定来自 `signals[4]`（恒为未知）与 `chainStatus` 说明，不伪造净流金额。

### 后端有、未纳入默认26列/抽屉主清单（可选后续）

- `asset`
- `belowAllEma`
- `binanceAssetOiCapPct`
- `capAgeMinutes`
- `capName`
- `capPrice`
- `capPriceGapPct`
- `capTrusted`
- `chainFlowUsd`
- `crossEma99`
- `emaCandleCount`
- `fundingDailyPct`
- `fundingIntervalStatus`
- `market`
- `observedFundingHours`
- `oiAgeMinutes`
- `previousBelowAllEma`
- `price`
- `settledFunding`

## 语义核对要点

- 命中数：优先 `row.hitCount`（后端六指标），可算数优先 `row.knownCount`；展示层不用「可算数」冒充「命中数」。
- 缺失：数值列显示「未知」及原因，不把 `null` 渲染成 `0`/`false`。
- 同资产 OI：`binanceAssetOiUsd` / `binanceAssetOiPartialUsd` 与本合约 `oiUsd` 分列，不互相替代。
- ⑤：无精确覆盖时显示「未知 · 无精确覆盖」，`chainFlowUsd` 当前为 null。

## HTTP 静态检查（8768）

```json
[
  {
    "path": "/",
    "status": 200,
    "bytes": 5081210,
    "ctype": "text/html; charset=utf-8"
  },
  {
    "path": "/prototype.html",
    "status": 200,
    "bytes": 5081210,
    "ctype": "text/html; charset=utf-8"
  },
  {
    "path": "/pro-table-runtime.js",
    "error": "HTTP Error 404: Not Found"
  },
  {
    "path": "/strategy-registry.js",
    "status": 200,
    "bytes": 8308,
    "ctype": "application/javascript; charset=utf-8"
  },
  {
    "path": "/market-evidence.js",
    "status": 200,
    "bytes": 15038,
    "ctype": "application/javascript; charset=utf-8"
  },
  {
    "path": "/tasks-runtime.js",
    "status": 200,
    "bytes": 16722,
    "ctype": "application/javascript; charset=utf-8"
  },
  {
    "path": "/api/scan",
    "status": 200,
    "bytes": 6119429,
    "ctype": "application/json; charset=utf-8"
  },
  {
    "path": "/api/health",
    "status": 200,
    "bytes": 4242,
    "ctype": "application/json; charset=utf-8"
  },
  {
    "live_has_pro_script": true,
    "live_cap_million": true,
    "live_capRanges_10": true,
    "live_selected_tab": true
  }
]
```
