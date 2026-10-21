# iteration-v2.8 · CONFIG-01 设置页全字段双向绑定（首刀加深）

- **时间**：2026-10-13 23:05 Asia/Shanghai
- **实现ID**：CONFIG-01（加深：settings bidirectional bind）
- **状态**：部分实现（**不标 CONFIG 完成**）
- **聚合 API**：`GET /api/config/settings-bind`（`settings-bind-v1`）
- **双向字段**：
  - 观察池 / 提示词（`expected_content_hash`）
  - 扫描调度（`PUT /api/settings` + `version`）
  - AI 非密钥摘要（只读；永不回显 Key；DPAPI 路径不变）
  - SIM 费用 / T1–T3 预算（`expected_revision` → 409）
  - EVAL 计划 `enabled` + `auto_enqueue_backfill`
  - REVIEW 计划 `enabled`
  - 既有备份向导保留
- **冲突**：HTTP 409 明示，拒绝静默覆盖
- **UI**：`design/config-runtime.js` 设置页绑定面板
- **验证**：`unittest test_config_store` + `SimConfigBindConflictTests`
- **备份**：`runtime/code-backups/config-settings-bind-*`
- **未做**：EXT 精确流深化；CONFIG 不标完成
- **下一刀建议**：EXT 精确流深化（precise-flow）
