# V2.8 · AI-01/AI-02 代码交付（进行中）

日期：2026-10-13。对应需求 [§11](requirements.md) AI-01、AI-02。**服务版本仍为 2.7**；本轮未宣称 AI-03 完成。

## 已实现

- `ai_credentials.py`：Windows DPAPI 保护 Key；文件在 `runtime/credentials/`，不进静态白名单。
- `ai_service.py`：独立 AI 配置版本表；保留/替换/清除 Key；GET 脱敏；连接测试与能力分项（model/schema/retrieval）；未实现协议拒绝保存。
- `server.py`：`GET/PUT /api/ai/config`，`POST /api/ai/test/connection`，`POST /api/ai/test/capabilities`；扫描设置接口不变，不会清掉 AI 配置。
- `design/ai-runtime.js`：设置页 AI 面板接入真实保存/测试/清除；初版与新版页面均已引用。

## 验证

- `python -B -X utf8 -m unittest discover -p test_*.py -q`：66 项通过（含新增 AI 单测）。
- 真实供应商/鉴权/检索：**未验收**。请用户重启本地服务后自行填 Key 并点测试。

## 未完成

- AI-03 三档投研/报告任务/缓存/策略草案。
- 独立检索适配器；Responses/Anthropic 协议。
- 健康接口版本号仍 2.7，待 A 包闭环后再升。

## 使用

重启 `start-pages.cmd` 后打开 8768 → 设置 → AI API：保存配置（空 Key 保留）→ 测试连接。不要把 Key 发到聊天里。


## AI-03 状态（2026-10-13）
- 后端：`ai_research.py` + `POST/GET /api/reports` + 任务 kind=`ai_research`
- 前端：`#runAiResearch` → `/api/reports`；任务列表显示「AI投研报告」
- 测例：空 `POST /api/reports` 现为 400；`test_ai_research.py` 覆盖 prepare/run（不打真实计费）
- 注意：首次需在设置页重新保存 API Key（`keyConfigured` 曾为 false）


## AI-03 补强（同日晚间）
- `POST /api/reports/preview`：只预览不调模型（不计费）
- 同 `request_hash` 缓存复用，调用记录写入 `runtime/ai-calls/`
- 报告可附只读策略草案摘录（`drafts`，不可执行）
- `GET /api/ai/calls` 查看近期调用/缓存命中
- 真实供应商连接/能力：**仍需你在页面点「测试」验收**（本轮未自动付费请求）


## 扫描进度与超时（2026-10-13 晚）
- design/tasks-runtime.js：顶部进度条显示 完成/总数、已用时间、预计剩余；扫描进度不被排队投研文案覆盖。
- 投研调用超时下限提高到 180 秒（上限 300）；超时错误改为明确中文提示。
- 当日全市场扫描已完成发布（588 合约）。真实 AI 投研一次因读超时失败，提高限时后需用户重试。


## 待做：报告区展示（用户 2026-10-13）
- 问题：多报告/多合约堆在同一预览区，易混乱；数据报告与 AI 报告易混淆。
- 已写入统一需求 §11.8 与整改清单；代码尚未改。

## AI-03 扫描并投研（2026-10-13）
- 后端：`mode=scan_and_research` → 任务 kind=`scan_and_research`；先跑与手工扫描相同的 runner，仅 success/partial_success 后 `bind_snapshot` 绑定新 `scan.json` 再 `AiResearch.run`；失败保留错误、不调模型、不改用旧快照。
- 前端：报告页 `#runScanAndResearch`「扫描并投研」；提示将先完整扫描。
- 测例：`test_ai_research` prepare/bind；`test_task_service` 成功链与扫描失败短路（无真实付费）。
