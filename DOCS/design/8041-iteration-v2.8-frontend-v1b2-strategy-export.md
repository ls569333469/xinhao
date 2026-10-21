# V1-B2：策略冻结页导出/导入接线

时间：2026-10-14 13:35 Asia/Shanghai

## 做了什么
- 冻结版本卡片增加「导出包」→ POST /api/strategies/versions/{id}/export，浏览器下载 JSON（含联合指纹）
- 策略库存页增加导入面板：粘贴 JSON 或选文件 → POST /api/strategies/import；409 篡改指纹原样提示
- 只读展示 /api/strategies/bindings/export-fingerprint
- 顶栏文案改为：策略草案/冻结/绑定/导出已接本地接口（页面点验中）

## 备份
`runtime/code-backups/frontend-v1b2-strategy-export-20261014T133455`

## 验收（待硬刷）
1. 指标与策略 → 已存草案与冻结版本 → 「导出包」应下载 JSON
2. 改 rules 保留旧 hash 再导入应失败
3. 诚实包导入后列表出现新冻结版

## 口径
不标 RULE-01 完成；Codex 排查并行。
