# V2.8 DATA-01 加深刀 · 多字段覆盖闸门

## 范围

在只读价格索引之上，增加 **expected / received / known** 多字段覆盖闸门：

- 仅统计扫描行与本地 K 线归档里**真实存在**的字段
- 部分可得 → `partial`；全缺 → `unknown`（禁止伪装确定）
- **绝不**宣称 OI / 市值 / 账户历史齐全（即便当前行 100% 有值）

## API

- `GET /api/coverage/catalog`
- `GET /api/coverage` — 扫描字段 + 本地 OHLCV 目录闸门
- `GET /api/coverage/scan?revision=` — 仅扫描字段

## UI

数据与任务 / 区间补查旁注入覆盖闸门面板。

## 明确不做

- 不联网补全字段
- 不把价格覆盖当六历史指标齐全
- 不声称可信 ATH / 全生命周期

## 验收对照

- [x] expected/received/known 三分法
- [x] partial / unknown 语义
- [x] OI/市值/账户禁止 history_complete
- [ ] 策略声明预热硬闸（启用前强制）
- [ ] 逐策略依赖矩阵 UI
