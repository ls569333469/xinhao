# 前端第二步：对象与投研链路（Grok 迭代）

时间：2026-10-14 上午（上海）

## 改动摘要
1. `design/ai-runtime.js`
   - 投研按钮改为先走 `/api/reports/preview`，写入待确认预览；**不再预览后自动 POST `/api/reports`**。
   - 新增「确认提交」「取消」：确认才真正建任务；取消可丢弃预览或请求 `POST /api/tasks/{id}/cancel`。
   - 去掉静默 `analysisRows().slice(0,20)` 顶替；未勾选/未指定直接报错。
   - 确认前按页面现选对象与深度重算签名，变了则作废预览。
   - 进行中禁止重复提交。
2. `design/prototype.html`
   - 命令栏增加 `#confirmAiResearch` / `#cancelAiResearch`。
   - `selectedResearchContracts` 改为跟随 `analysisRows()`（尊重研究对象范围与「本次分析上限」），不再硬截 20。

## 备份
`runtime/code-backups/frontend-research-step2-*`

## 请 Codex 审计
- 预览→确认→提交是否可能绕过确认直接计费调用
- 空选/筛选/指定/观察池/全市场 scope 是否还会静默顶 N
- 参数变更后旧预览是否仍能提交
- 取消预览与取消在途任务是否安全
- 与正式 API（preview / reports / tasks cancel）字段契约
- 扫描并投研失败是否仍不会回退旧快照（后端既有行为核对）

## 未做（留给后续）
- 报告阅读页第三步深化
- 浏览器端到端点验（需用户硬刷新 + 勾选后点一次预览，勿点确认以免真实计费）
