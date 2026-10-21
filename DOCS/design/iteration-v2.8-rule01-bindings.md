# V2.8 RULE-01 加深刀 · 跨页统一引用绑定

## 范围

补齐首刀未做的「市场/报告/篮子/实验/验证引用同 ID/version/hash」：

- 规范引用：`strategy_id` + `version` + `content_hash` + `registry_hash` → `unified_key`
- 页面绑定持久化于 `runtime/strategies/bindings.json`
- 指纹不一致拒绝绑定；一键 `bind_all` 五页对齐

## API

- `GET /api/strategies/bindings`
- `GET /api/strategies/bindings/catalog`
- `GET /api/strategies/ref/{version_id}`
- `POST /api/strategies/ref/validate`
- `PUT /api/strategies/bindings/{surface}`
- `POST /api/strategies/bindings/bind-all`
- `DELETE /api/strategies/bindings/{surface}`

## UI

指标与策略页：统一绑定面板（选冻结版本 → 全页对齐 / 看指纹一致性）。

## 明确不做

- 不自动按白名单下单
- 不替代 DATA-01 逐字段覆盖闸门
- 不把启用当成可执行

## 验收对照（本刀）

- [x] 五页可绑定同一冻结版本指纹
- [x] 错 hash / 错 strategy_id 拒绝
- [x] 重启恢复 bindings
- [ ] 市场筛选 UI 强制只读绑定版本过滤结果（后续）
- [ ] 报告导出强制写入 unified_key（后续）


## 加深（覆盖硬闸）

2026-10-13 21:45 CST：见 [iteration-v2.8-rule01-coverage-gate.md](iteration-v2.8-rule01-coverage-gate.md)。
