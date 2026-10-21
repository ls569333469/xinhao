# V2.8 CONFIG-01 首刀（部分实现）

## 范围

持久化工作台配置（`runtime/config/`）：

- 观察池（自选合约列表）
- 提示词覆写（overrides，不改原 `prompt-templates.json` 文件）
- 账户成本偏好快照（非密钥；SIM 仍是费用权威）
- 导出默认**排除凭证**；导入冲突拒绝静默覆盖

## API

- `GET /api/config/catalog`
- `GET /api/config`
- `PUT /api/config/observation-pool`
- `PUT /api/config/prompt-edits`
- `POST /api/config/export`
- `POST /api/config/import`

## UI

设置页注入 `config-runtime.js`。

## 明确不做

- 不导出/不备份 `runtime/credentials`
- 不在本切片做完整备份恢复向导
- 不把 SIM 账本迁入配置包

## 验收对照（首刀）

- [x] 刷新/重启恢复观察池与提示词覆写
- [x] 导出排除凭证
- [x] 旧配置/哈希冲突拒绝覆盖
- [ ] 全量备份恢复向导
- [ ] 与设置页所有字段双向绑定
