# 前端第三步：报告阅读与设置（Grok 迭代）

时间：2026-10-14 上午（上海）

## 改动
1. `design/reports-runtime.js`
   - 列表区分 AI投研 / 本地数据，展示深度、模型、合约摘要
   - 详情 meta：来源、模型、协议、深度、快照、缓存、耗时、错误
   - 导出正文 / 导出 JSON
   - 打开失败、正文缺失、error 字段单独展示，不装成成功
2. 设置页预算区本就无 Token 上限输入（仅候选上限/复用有效期）；保留 AI 凭证与能力测试逻辑不动

## 备份
`runtime/code-backups/frontend-reports-step3-*`

## 验证
- `/reports-runtime.js` 现网包含 `exportCurrent`
- 需用户在投研报告页点历史报告看 meta/导出（现网点验未完成）

## 请 Codex 顺带看
- meta 字段是否与 `/api/reports`、`/api/reports/{id}` 契约一致
- 导出是否泄漏密钥（不应：报告对象无 key）
