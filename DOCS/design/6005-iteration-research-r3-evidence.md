# 投研迭代 R3（真实供应商单币标准档闭环）取证

- **时间**：2026-10-14 20:35 Asia/Shanghai
- **切片**：R3 only（授权范围内 **一次成功** 真实模型调用；检索保持 fixture；无 CON；无经验库页；**不**标 A 档 01/02/03 完成）
- **对应**：模板 B 需求 §10 R3；复核补充 v1.1 REV-04 尝试记录；A-02-11 门禁在群聊明确授权后视为 OPEN（本轮单币标准档）
- **状态**：**真实供应商调用成功**（第 2 次；第 1 次本地读超时记为可能计费/结果未知）
- **未改前端**：本轮未触碰 i-runtime.js（档位→深度模板面板绑定由并行修复处理）

## 1. 凭证与门禁（做了什么）

| 项 | 结果 |
|---|---|
| 凭证位置 | 
untime/credentials/ai-key.dpapi（Windows DPAPI）；元数据 i-config.json |
| Key | **已配置**（keyConfigured=true）；本证据 **不**记录密钥 |
| 供应商 | provider=apikey，aseUrl=https://api.apikey.fan/v1，protocol=chat，modelId=gpt-6.1-sol |
| 任务开关 | 	asks.research=true（本轮未改任务开关） |
| A-02-11 | 此前文档门禁为「未授权不执行」；群聊用户明确授权后本轮执行 **单币标准档** 闭环 |
| 本地阻断旗标 | **未发现** 额外 llow_paid / mock-only 软件开关；阻断主要是文档授权 + 写接口同源 Origin |
| 本轮配置变更 | 将 	imeoutSec **90→300**（AI config ersion 6→7，keyAction=keep）。原因：标准档真实调用曾卡在有效超时 180s（代码 max(timeoutSec,180) 且上限 300） |

探测（无密钥落盘）：/v1/models 约 0.4s 200；极短 chat 探针约 3.9s 200（非投研任务，仅确认通路）。

## 2. 服务加载

| 项 | 值 |
|---|---|
| 旧进程 | PID 35388，启动 2026-10-14 14:04（早于 R2b 的 i_research.py/
esearch_retrieval.py 20:09 更新） |
| 操作 | 仅结束该 server.py PID，再拉起新进程 |
| 新进程 | PID **22716**，启动 2026-10-14 20:21 Asia/Shanghai；监听 127.0.0.1:8767 / 8768 |
| health | nomaly-workbench version 2.8 |
| 检索模式 | 默认 / 显式 **fixture**（未配 live base URL；R3 不因检索夹具失败） |

## 3. 真实调用轨迹

### 3.1 第一次（失败 / 可能计费）

| 字段 | 值 |
|---|---|
| symbol | STRKUSDT（USDⓈ-M） |
| depth | standard |
| submission_id | sub-aa5c4e90068e47ef8f47d0f70c6b350b |
| task_id | 	ask-ec6739e0f3cd46dfb0ab394e121508df |
| attempt_id | ttempt-e6c8e5304c7b4b1faad760959e5cc47b |
| 检索 | fixture success，online_verified=false，
etrieval-attempt-8ce3032ff06c44ce9c25a67195110628 |
| 结果 | **失败**：本地读超时 >180s；endor_attempt_unknown / 
esult_unknown_may_be_billed；**未自动重试**（符合 REV-04） |
| 起止（UTC） | 12:22:39 → 12:25:39 |

### 3.2 第二次（成功 · 计本轮 R3 主证据）

| 字段 | 值 |
|---|---|
| symbol | STRKUSDT |
| depth | standard · profile_label=标准投研（模板 B） |
| prompt_version | 
esearch-prompts-v1.1-template-b |
| submission_id | sub-140604470518417cb00d2c4f5eea1636（**新任务**，非复用旧 AI 正文） |
| task_id | 	ask-243e323fcac4406eae99834732c85e84 |
| intent_hash | 8d515b207b6e36e47ed383ec06db8b01483b092c987b5559bbf1639c2f1f1308 |
| vendor attempt_id | ttempt-45dc9bae8f69430d953c1ab1acfa7e3a |
| HTTP | 200 completed |
| duration_ms | **136438**（约 136s；落在 300s 超时内） |
| report_id | **
eport-4dbefb0c984d40879ee9161e80edc9f2** |
| 报告路径 | 
untime/reports/report-4dbefb0c984d40879ee9161e80edc9f2/report.json |
| 冻结目录 | 
untime/task-freeze/task-243e323fcac4406eae99834732c85e84/ |
| usage tokens | 记录为 **unknown**（供应商响应未经本链路解析出 usage；非密钥问题） |
| 检索 | 
etrieve_then_generate + fixture success；online_verified=false；llowed_evidence_ids=[ev-fixture-ok-catalyst]；attempt 
etrieval-attempt-1af10076eb3f478295a24f9a75a3ef4e |
| 模板 B | schema_validation.ok=true；**八节命中 8/8**：核心催化剂 / 价格与市场 / 风险与机会 / 技术面 / 合约结构 / 基本面 / 支撑压力 / 综合判断；ody_template=B，ody_kind=template_b_markdown |
| scan 绑定 | scan-3fc0b6f494394e3c95ffdfe7b345f1cb revision 1 |

调用日志（无密钥）：
untime/ai-calls/calls-20261014.jsonl。

## 4. RES / R3 对照

| ID / 项 | 结果 | 说明 |
|---|---|---|
| R3 真实闭环 | **通过（有条件）** | 真实供应商 HTTP 200 + 报告落盘；第 1 次超时已按 REV-04 记「可能计费」 |
| REV-04 尝试记录 | **通过** | ttempt_id 写入 i-calls 与 task-freeze ttempts.jsonl；超时未知态未盲重试 |
| 新 submission 任务 | **通过** | 成功任务 sub-140604… 与失败任务不同 id |
| 模板 B 章节 | **通过（后端校验）** | 8/8；页面档位文案绑定另案修复，见下 |
| 检索 | **fixture 成功** | 非 live 全文核验；online_verified=false（符合 R2b 约定） |
| 费用/usage | ~~部分~~ 用户确认不需要 | duration 已知；token usage=unknown（用户在中转站查看用量，2026-10-14 R5 起不再列为待办） |
| 用户硬刷新 UI | **仍待用户** | 见 §6 |
| A 档 01/02/03 | **不宣称完成** | — |

## 5. 失败与配置教训

1. **预览 intent 对齐**：确认请求须带与预览相同的 settings_version，否则 preview_intent_hash 会 409「预览已失效」。
2. **写接口同源**：Origin: http://127.0.0.1:8767 + Sec-Fetch-Site: same-origin，否则 403。
3. **超时**：标准档真实生成可超过 180s；本轮将 	imeoutSec 提到 300 后第二次成功（耗时 ~136s，接近历史 MOVR ~140s）。
4. **预算**：本轮 2 次投研模型尝试（1 超时未知 + 1 成功）；另有极短通路探针，非投研任务。

## 6. 仍需用户硬刷新点验（清单）

请在浏览器对 http://127.0.0.1:8767/（或 8768 prototype）**硬刷新**后核对：

1. 打开报告 **
eport-4dbefb0c984d40879ee9161e80edc9f2**（STRKUSDT / standard / 模板 B）。
2. 投研进度/报告详情中检索字段：status=success、dapter_mode=fixture、online_verified=false（勿当成已联网全文核验）。
3. 模板 B 八节在详情中的展示是否完整。
4. **已知并行问题（勿与 R3 后端成败混淆）**：快捷/标准档 UI 可能都显示「深度模板」文案或错模板正文——由独立修复处理档位→模板面板绑定；本轮证据以服务端 depth=standard + prompt_version=…template-b + 八节校验为准。
5. 确认页费用/耗时展示（若有）与 duration_ms≈136438、usage=unknown 一致或可解释。

## 7. 未宣称

完整投研产品完成、live 检索全文核验、~~token 费用精确账单~~（用户确认不需要）、A 档 01/02/03 完成、CON 套件、经验库页、二手日历类措辞。

## 8. 关键产物路径

| 路径 | 用途 |
|---|---|
| DOCS/design/iteration-research-r3-evidence.md | 本文件 |
| DOCS/design/requirements-r3-progress.md | 进度短注 |
| 
untime/reports/report-4dbefb0c984d40879ee9161e80edc9f2/ | 报告 JSON + prompt_input |
| 
untime/task-freeze/task-243e323fcac4406eae99834732c85e84/ | intent/evidence/retrieval/attempts |
| 
untime/ai-calls/calls-20261014.jsonl | 尝试与研究日志 |
| 
untime/_r3_*.json | 预览/提交/任务原始抓取（辅助） |

## 9. SHA-256（落盘后）

| 文件 | SHA-256 |
|---|---|
| DOCS/design/iteration-research-r3-evidence.md | 12cd69b6f62df321b7d02d3810a60631e8a06c9685e986fd5e2ff7af8b0d853b（写入后若再追加本节会变） |
| DOCS/design/requirements-r3-progress.md | d7d57ec125187902fc674536dbd2c8cc913af09dd3c3aaf5fb799361722bd5d8 |
| 
untime/reports/report-4dbefb0c984d40879ee9161e80edc9f2/report.json | 4b913da3ef9e83bd21ecb90576d14ea95d9c633652a9a9954e0cc069c0e0dad0 |
