# 正式功能 vs 预览能力对照（第一步）

> 预览（version-a / a-pro）只提供外壳与专业表交互参考；**不能**作为功能删减依据。

## 预览缺失、正式已有（必须保留）

1. **AI API 协议/版本选择**：`#protocol`（Chat Completions / Responses / Anthropic Messages）
2. **模型版本选择**：`ai-runtime.ensureModelSelect()` → `#modelSelect`（「选择已知模型版本…」）
3. 服务商预设 `#provider`、Base URL、Key、modelId、拉取模型、测试连接、保存、清 Key
4. 能力验证分项展示（鉴权/模型/结构化/检索）
5. 提示词版本、投研预览、扫描并投研、真实任务状态
6. 定时扫描与 tasks-runtime 真接口
7. 策略 / 模拟篮子 / 验证复盘 / 数据与任务 / 设置 — 真实模块与 runtime，而非预览概览占位
8. coverage / ext / backtest / struct / config / reports 等正式脚本

## 本轮第一步实际改动边界

- 新增：`design/pro-table-runtime.js`（仅扫描表外壳增强）
- 调整：`prototype.html` 市值百万美元、已选页签、引入专业表脚本
- `server.py` 静态白名单增加该脚本
- **未修改**：`ai-runtime.js`、`config-runtime.js`、各业务 runtime、设置区 DOM 结构

## 绑定核对（2026-10-14）

- 设置 AI 区关键 id 与改前备份一致
- 七项 `data-page` / `data-view` 齐全
- 脚本顺序：业务 runtime 与内联逻辑之后加载 `pro-table-runtime.js`，避免抢先覆盖
