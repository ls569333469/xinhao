# V2.8 A 档阶段 S2 证据：01 扫描 / 02 投研离线回归

时间：2026-10-14 15:10（北京时间 / Asia/Shanghai）  
执行：GROK 执行助手（S2）；未启动真实 AI、未打外部付费模型、未打开会触发外部行情请求的页面作只读验收。  
依据：`DOCS/design/V2.8-A档迭代需求-审计修复与01-02-03收口.md` §4/§5；`iteration-v2.8-frontend-a-fix-0607.md`。  
**本文件不是完工证明；不宣称 01/02/03「全部完成」。**

## 受验版本指纹（SHA256 前 16）

| 文件 | mtime (北京) | hash 前缀 |
|---|---|---|
| `design/pro-table-runtime.js` | 2026-10-14 14:58:17 | `0DC16EC5FB8A16F8` |
| `design/ai-runtime.js` | 2026-10-14 13:57:36 | `7DFA8BF1DC43F8B6` |
| `design/reports-runtime.js` | 2026-10-14 14:03:59 | `642A663EA5DB9191` |
| `design/a-shell.css` | 2026-10-14 14:03:59 | `88ADFABB3B4886F6` |
| `ai_research.py` | 2026-10-14 14:03:59 | `362462C146CC5E56` |
| `task_service.py` | 2026-10-13 23:34:34 | `FE62516B86B44ED5` |
| `coverage_gate.py` | （既有） | `B6FCBBA69C84A7B2` |
| `scanner.py` | （既有） | `30C5D53561F7F954` |

S1 备份（未重做）：`runtime/code-backups/frontend-a-fix-0607-20261014T145817/`  
运行服务：`127.0.0.1:8767/8768`（PID 35388，`server.py`，health `version=2.8`，`scan.enabled=false`）。

## 离线套件摘要（本轮）

| 套件 | 结果 | 备注 |
|---|---|---|
| `test_ai_research` | 13/13 OK | FakeAi；含 preview / bind / schema / cache |
| `test_coverage_gate` | 16/16 OK | 未知不冒充、硬闸、F07 partial 拒绝 |
| `test_scan_contract` | 14/14 OK | fake 行情；缺失 OI 不为 0 |
| `test_reports_fingerprint_gate` | 2/2 OK | |
| `test_task_service` | 18/20；2 FAIL | 见下方「残留」；**失败跳过模型**用例 OK |
| `node --check` pro-table / ai / reports | OK | |
| A-FIX-06/07 节点模拟 | 过期清缓存、histMeta 顶层 | 与 S1 一致 |
| `POST /api/reports/preview`（仅预览） | 200，`billing=preview_only_no_model_call` | 未 POST `/api/reports` 确认 |

---

## 01 市场扫描

### A-01-01 应有/实收/字段有效可追溯；缺失为未知不冒充 0

- **验证步骤/输入**：① `unittest test_scan_contract…test_missing_asset_oi_is_not_zero_or_complete`、`test_coverage_gate…test_all_missing_unknown_not_fake`；② 只读 `scan.json`：`scan-3fc0b6f494394e3c95ffdfe7b345f1cb` rev1，total=received=588；字段有效例：`capUsd 515/588`、`volumeRatio 587/588`；signal 细胞 true/false/nullish = 557/2308/663。③ 前端 `unknownWhy` / `present` / `realUnknownCount`（`pro-table-runtime.js`）。
- **预期**：缺失显示未知；不把缺字段填 0 当完整。
- **实际**：单测通过；快照字段有效数可分列统计；表格缺值走「未知」文案。
- **证据**：单测 OK；scan.json 计数；代码 `unknownWhy`。
- **状态**：**离线通过**；运行全市场重扫 **未授权跳过**（会打外部行情）。
- **残留**：现网用户界面硬刷点验待做。

### A-01-02 筛选：市值 / 六指标 / 满足·待核验·已选；未知不当 false

- **验证步骤/输入**：抽取 `combo` 三态逻辑；`[true,null]` mode=all → `null`（非 false）；`[false,null]` mode=any → `null`。市值缺 →「进入待核验」文案；`capMin/capMax` 提示「缺市值单独进待核验」。
- **预期**：未知不参与「明确不满足」。
- **实际**：与预期一致。
- **证据**：节点模拟输出；`pro-table-runtime.js` ~356–357、176、517。
- **状态**：**代码落地 + 离线通过**；用户 UI 点验 **用户待验**。
- **残留**：六指标组合在实际勾选组合下的肉眼点验。

### A-01-03 默认/全 30 列、刷新保存、升降序、缺失置后、覆盖提示

- **验证步骤/输入**：`ALL_COLS.length===30`；`DEFAULT_COLS.length===11`；列含 `reserveDay/Week/Streak/histFlow` 与 `flow`（精确净流）分列；`STORAGE_COLS=workbench-a-pro-columns-v2`；`hintCount` 显示 `cols/ALL`。
- **预期**：可选 30；默认与全量分开。
- **实际**：30/11 计数正确。
- **证据**：node 解析 ALL_COLS；HEADERS 文案。
- **状态**：**代码落地 + 离线通过**；localStorage 刷新保存与排序交互 **用户待验**。
- **残留**：无有效值排序的「无数据」提示需用户界面确认。

### A-01-04 选择一致 / 跨筛选保留 / 不混同 market

- **验证步骤/输入**：托盘文案「跨筛选保留；同币不同市场不合并」；`rebuildTable`/`activePageRows` 读 `window.pageRows`；`selectedTokens` + `trayHiddenNote`「当前筛选隐藏 N 份已选」。承接 A-FIX-01 / Codex 审计修复离线记录。
- **预期**：标题/列表/内部对象一致；选币进 02 不换对象、不静默截断。
- **实际**：代码路径符合；本轮未再改选择逻辑。
- **证据**：`pro-table-runtime.js` 630–732；`iteration-v2.8-frontend-codex-audit-fix.md`。
- **状态**：**代码落地**；完整 UI **用户待验**。
- **残留**：用户硬刷后勾选→切筛选→看托盘/已选页签。

### A-01-05 扫描失败可留历史快照查看，但「扫描并投研」须失败停止，不偷用旧快照

- **验证步骤/输入**：`test_scan_and_research_scan_failure_skips_model`：旧 `scan-old` 在盘，fail_scan → task `failed`，`research_calls=0`，phase 含「未继续投研」。`task_service.py` 注释 `# NEVER fall back to old snapshot for research`。
- **预期**：失败不调用模型；旧文件可仍在。
- **实际**：单测 OK。
- **证据**：unittest OK；`task_service.py` ~371–416。
- **状态**：**离线通过**；对照实扫失败路径 **运行待验**（勿在未授权时触发真扫描）。
- **残留**：无阻断逻辑问题。

### A-01-06 硬闸不因空依赖/部分字段绕过；目录存在≠覆盖完整

- **验证步骤/输入**：`test_coverage_gate` 全过（empty deps baseline、F07 partial 拒绝、warmup 不可下调）。手工复现：partial 快照（received 9/total 10）触发 `assert_scan_research_gate` → 「基线覆盖硬闸未通过…已停止投研，未使用旧快照」，`research_calls=0`。补全覆盖夹具后 success + `scan_id=scan-new` rev=7。
- **预期**：partial/expected>received 不得继续投研。
- **实际**：硬闸生效。
- **证据**：coverage 单测；诊断脚本输出。
- **状态**：**离线通过**。
- **残留**：见「测试夹具漂移」（非生产阻断）。

### A-01-07 布局 / sticky / 横滚；不分页不得标分页已验

- **验证步骤/输入**：`a-shell.css` 表头 `position:sticky`、首两列 sticky、`.a-pro-tablewrap overflow-x:auto`、Codex P2 盖过 `max-height:none`；当前无分页控件（历史「去分页」记录）。
- **预期**：代码层可达横滚、纵滚表头固定；**不**宣称分页已验。
- **实际**：CSS/结构具备；用户缩放未测。
- **证据**：`a-shell.css` ~271–313、490–525；requirements 日志。
- **状态**：**代码落地**；用户原缩放环境 **用户待验**。
- **残留**：常用宽度肉眼点验。

### A-01-08 设计备注清场；储备时效与历史区间；三种口径分开

- **验证步骤/输入**：① 指定过程文案「完整专业表格/净流占位/近空建议隐藏」在 design 关键文件 **0 hits**。② A-FIX-06：过期 48h → reserve 日/周/连减缓存清除；新鲜 → 连减=5。③ A-FIX-07：`histMeta()` 读顶层 `chainHistory.provider/windowFrom/windowTo`（Santiment · 2026-09-05–12）。④ 单元格副文案：储备「币安局部储备·非精确净流」；历史「非当前⑤」；`flow` 为精确净流。
- **预期**：三种口径分列；过期不假有效。
- **实际**：与 S1 修复一致，本轮复验通过。
- **证据**：`iteration-v2.8-frontend-a-fix-0607.md`；本轮 node 模拟；`pro-table-runtime.js` histMeta / ensureSupplementalFields。
- **状态**：**代码落地 + 离线通过**；用户硬刷 **用户待验**。
- **残留**：无。

---

## 02 投研与报告

### A-02-01 档位与模式 / 显式对象数量与身份

- **验证步骤/输入**：depth `quick/standard/deep`；mode `snapshot|scan_and_research`（`ai_research.prepare`）。预览 API：`selected_count=1`，contracts 含 `BTCUSDT`+`USDⓈ-M`。
- **预期**：模式按契约；对象可计数。
- **实际**：预览 200；FakeAi prepare/preview OK。
- **证据**：`POST …/preview` 响应；`test_ai_research`。
- **状态**：**代码落地 + 离线/预览通过**；页面点验 **用户待验**。
- **残留**：观察池等 scope 的 UI 点验。

### A-02-02 空对象拒绝；超上限明确告知；不静默前 20；不扩成全市场

- **验证步骤/输入**：`prepare(contracts=[])` → `ResearchError: 请提供contracts列表（1–20）`；前端 `buildResearchBody` 空选抛「不会再静默用筛选前 20 份顶替」；默认路径**不**传 `allowFilteredTop`。
- **预期**：拒绝空选；无静默顶 N。
- **实际**：符合。
- **证据**：FakeAi；`ai-runtime.js` 288–304。
- **状态**：**离线通过**。
- **残留**：UI 勾选上限提示肉眼确认。

### A-02-03 预览冻结展示；不调用收费模型；改关键字段须重预览

- **验证步骤/输入**：`preview` 返回 `billing=preview_only_no_model_call`（本机 8768 + FakeAi 单测）。前端 `researchSignatureOf`；确认时签名变 →「请重新预览」。
- **预期**：预览不计费；变更作废。
- **实际**：符合；**未**执行确认 POST。
- **证据**：预览 JSON；`ai-runtime.js` 397–417；`test_preview_and_cache`。
- **状态**：**离线通过 + 预览 API 通过**；确认按钮 UI **用户待验**。
- **残留**：无。

### A-02-04 明确确认才建任务；重复确认不重复；取消不建任务

- **验证步骤/输入**：代码：预览只写 `pendingResearch`；确认才 `POST /api/reports`；`researchBusy` 防重入；取消丢弃预览或 `POST /api/tasks/{id}/cancel`。`test_task_service` cancel/reuse 相关 OK。
- **预期**：无确认不计费；取消安全。
- **实际**：代码路径正确；本轮**未**对运行服务做确认写操作。
- **证据**：`ai-runtime.js` confirm/cancel；task 单测。
- **状态**：**代码落地**；端到端确认 **用户待验**（且需授权，可能计费）。
- **残留**：在途取消与服务端一致需一次授权联调。

### A-02-05 编辑模板/关注点进真实输入；bind_snapshot 保留；requested_search 仅意图

- **验证步骤/输入**：`_effective_prompt_bits`：自定义模板覆盖 instruction；`requested_search=True` → 文案含「检索适配器未实现前不得标 retrieval success」。`bind_snapshot` 保留 `depth_template/user_focus/requested_search`，绑定新 `scan_id/revision`。覆盖夹具 success：`scan-new`/rev7，report request 含上述字段。
- **预期**：编辑进入 payload；绑定新快照。
- **实际**：符合。
- **证据**：节点/ FakeAi；`test_f09_edited_prompt_changes_cache_key`；成功 bind 诊断。
- **状态**：**离线通过**；真实供应商模型 **未授权**（见 A-02-11）。
- **残留**：无逻辑阻断。

### A-02-06 缓存身份含关键输入；陈旧绑定拒绝/重预览

- **验证步骤/输入**：`test_f09_cache_identity_includes_baseurl_evidence_prompt`、`test_f09_edited_prompt_changes_cache_key`、`test_f09_run_rejects_mismatched_current_config` OK。前端签名作废。
- **预期**：改输入不复用错报告。
- **实际**：单测通过。
- **证据**：unittest。
- **状态**：**离线通过**；完整缓存矩阵未穷举 → 残留。
- **残留**：完整 schema/缓存矩阵未全验（文档基线已记）。

### A-02-07 连接 / 结构化 / 检索分列状态；自称联网≠检索成功

- **验证步骤/输入**：health `ai.capabilities`：auth=success，model=success，schema=unverified，retrieval=unverified。UI `statusLabel` 映射成功/失败/不支持/未验证。requested_search 文案禁止标 retrieval success。
- **预期**：三分列；未验证不得冒充成功。
- **实际**：符合。
- **证据**：`/api/health`；`ai-runtime.js` 22–93。
- **状态**：**代码落地 + 运行只读通过**。
- **残留**：显式 capability 测试会触发小请求，本轮未跑。

### A-02-08 列表/详情 §11.8：代币+投研/数据报告+时间；多币可切换；数据报告不冒充 AI

- **验证步骤/输入**：`GET /api/reports`：AI 条 `MOVRUSDT` source=ai；本地条 `TESTUSDT` source=local。`displayTitle`：`投研报告`/`数据报告`。详情 meta/导出按钮在 `reports-runtime.js`。
- **预期**：标题口径正确。
- **实际**：列表 API 与代码符合。
- **证据**：reports API；`displayTitle`。
- **状态**：**代码落地 + 只读 API**；页面阅读 **用户待验**。
- **残留**：多币切换 UI。

### A-02-09 空正文/failed/partial/读失败明确；不回退旧正文；导出无凭证

- **验证步骤/输入**：空壳 →「报告暂无正文」「失败或空返回不会用旧报告顶替」；失败/正文缺失分支。抽查既有 report JSON：无 `sk-`/Authorization/password；`apiKey` 仅出现在 host `api.apikey.fan`。
- **预期**：空态明确；导出无密钥。
- **实际**：符合。
- **证据**：`reports-runtime.js` 372–407；报告文件抽查。
- **状态**：**代码落地 + 离线抽查**；用户打开空/失败报告 **用户待验**。
- **残留**：partial 任务态一次联调。

### A-02-10 schema/证据未通过不得用连接成功代替质量通过

- **验证步骤/输入**：`test_f09_schema_validation_unverified_on_markdown` / `…success_on_structured_json` OK。health schema=unverified 与 model=success 并存。
- **预期**：质量与连接分离。
- **实际**：符合。
- **证据**：unittest；health。
- **状态**：**离线通过**。
- **残留**：真实结构化输出联调属 A-02-11。

### A-02-11 真实 AI 一次完整链路

- **验证步骤/输入**：本轮 Codex/用户约束禁止真实 AI / 外部付费调用。
- **预期**：未授权不执行。
- **实际**：**未执行**。
- **证据**：本文件声明；未 POST 确认建任务；未调用 capability 计费测试。
- **状态**：**未授权，未执行**。
- **残留**：需用户另行授权后单独记录。

---

## 阻断 A 链路的新 bug？

**未发现需改业务逻辑的生产硬 bug。**

发现 **测试夹具漂移**（非生产阻断）：

| 项 | 位置 | 现象 | 结论 |
|---|---|---|---|
| `test_scan_and_research_success_binds_new_snapshot` | `test_task_service.py` ~351–358；`run_scan` 返回 `quality.status=partial_success`、received=9/total=10 | 任务 `failed`，硬闸文案「expected_gt_received…未使用旧快照」 | **硬闸正确**；夹具仍用 partial，与 V1 硬闸加深后行为不一致。补全覆盖夹具后 success+bind 新快照 **可复现通过**。 |
| `test_bad_json_shape_and_unsupported_api` | 同文件 ~241 | 空 body POST `/api/reports` 得 **409** 而非断言的 **400** | 校验/冲突码变化；与 A-01/02 主路径无直接阻断。 |

**不在本轮改业务逻辑**（非 A 链路硬 bug）；建议 S3/后续只读审计时更新夹具期望或夹具数据。

---

## 总表（一句话）

| ID | 状态 |
|---|---|
| A-01-01 | 离线通过（重扫未授权） |
| A-01-02 | 离线通过 / UI 用户待验 |
| A-01-03 | 离线通过（30 列）/ UI 用户待验 |
| A-01-04 | 代码落地 / 用户待验 |
| A-01-05 | 离线通过 |
| A-01-06 | 离线通过 |
| A-01-07 | 代码落地 / 用户缩放待验（不分页） |
| A-01-08 | 离线通过（A-FIX-06/07）/ 硬刷用户待验 |
| A-02-01 | 离线+预览通过 / UI 用户待验 |
| A-02-02 | 离线通过 |
| A-02-03 | 离线+预览通过 |
| A-02-04 | 代码落地 / 确认联调用户待验 |
| A-02-05 | 离线通过 |
| A-02-06 | 离线通过（矩阵未穷举） |
| A-02-07 | 代码+health 只读通过 |
| A-02-08 | 代码+列表 API / UI 用户待验 |
| A-02-09 | 代码+抽查 / UI 用户待验 |
| A-02-10 | 离线通过 |
| A-02-11 | **未授权，未执行** |

## 未做 / 明确跳过

- 真实模型完整链路（A-02-11）
- 触发外部行情的页面打开或真扫描
- 确认提交 `/api/reports`（可能计费）
- 03 策略（属 S3）
- 修改 Codex 审计正文；未动 5 分钟审计安排
- 不宣称 01/02/03 全部完成