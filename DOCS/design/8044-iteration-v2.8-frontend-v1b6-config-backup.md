# V1-B6：配置备份向导页接线

时间：2026-10-14 13:48 Asia/Shanghai

## 做了什么
- 导出/载入后展示 package_hash 与各段 payload_hash 摘要
- 审阅报告带上完整性摘要；缺 hash/篡改仍走后端 409
- 支持选本地 JSON 文件载入后再审阅/导入
- 存储页「导出非敏感配置」改为跳到全量备份向导；页脚改 v2.8 口径

## 备份
`runtime/code-backups/frontend-v1b6-config-backup-20261014T134729`

## 验收（待硬刷）
1. 设置 → 滚到 CONFIG 面板 → ②导出，报告应出现 package_hash / 分段 payload_hash
2. 改掉某段 payload_hash 再④导入应失败
3. 存储页点「打开全量备份向导」应滚到该面板

## 口径
不标 CONFIG-01 完成；凭证永不进包。
