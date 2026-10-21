# V2.8 EXT-01 首刀（部分实现）

## 范围

扩展能力目录与诚实拒绝口径：

- 精确⑤：未授权前 `not_ready`；拒绝用 cex-reserves / 技术资金流冒充
- 事件/解锁/供应：未接权威源；不做二手日历
- 历史市值：未接；禁止当今回填
- 多市场模拟引擎（USDC/COIN-M 计价）：未实现

## API

- `GET /api/ext/catalog`
- `GET /api/ext/precise-flow`
- `POST /api/ext/precise-flow/claim` — 冒充请求返回 409

## UI

设置页 `ext-runtime.js` 只读目录。

## 明确不做

- 不接付费数据源
- 不伪造精确净流
- 不开放未计价市场的模拟成交
