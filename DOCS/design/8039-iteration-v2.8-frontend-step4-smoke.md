# 前端第四步：联调冒烟（部分）

时间：2026-10-14 11:34 上海

## 已做
- `8768 /api/health` → anomaly-workbench 2.8
- `prototype.html` 引用的 17 个 script + 1 个 css 全部 HTTP 200，无 404
  （strategy-registry / market-evidence / tasks|ranges|reports|ai|strategy|rule-market|sim|eval|review|struct|backtest|ext|coverage|config|pro-table-runtime、a-shell.css）

## 未做（需用户/浏览器）
- 320–1920 布局点验、键盘/Escape、刷新偏好
- 控制台重复绑定 / 无付费调用确认
- 现网硬刷新扫描页验收（§11.8）
- Codex 对 step2/step3 审计闭环

状态：**部分实现 / 静态资源联调通过 / 现网交互验收未完成**
