# 投研迭代 R2b UI：检索/证据状态只读展示证据

- **时间**：2026-10-14 20:15 Asia/Shanghai
- **切片**：R2b 后续 UI only（只读展示报告/任务进度上的 retrieval 字段）
- **不做**：R3 付费调用、CON 套件、经验银行、A 档完结、默认开启 live 检索

## 1. 背景

R2b 后端已将 `retrieval` 写入 `report.json` / `task-freeze/.../retrieval.json` / `evidence_freeze.json`（含 `requested_search`、`status`、`gaps`、`online_verified`、`attempt_id` 等）。此前页面未展示这些字段；硬刷新无法肉眼核对。

## 2. 改动

| 文件 | 改动 |
|---|---|
| `design/reports-runtime.js` | 新增 `formatRetrievalPanel`：报告详情标题下只读展示「外部检索与证据」；无 `retrieval` 的旧报告不渲染面板 |
| `design/ai-runtime.js` | `pollReport` 完成后用 `retrievalBriefFromReport` 把检索摘要写到 `#aiResearchStatus` |
| `runtime/reports/report-r2b-ui-smoke-retrieval/report.json` | 离线烟雾报告（未调付费模型），便于硬刷新核对 UI |

备份：`reserve-history/r2b-ui-retrieval-display-backup-20261014-201442/`

## 3. 用户硬刷新应看到

1. 打开原型 → **报告**列表，选中 **SMOKE检索状态投研报告**（或任意带 `retrieval` 的 AI 报告）。
2. 详情区标题下方出现灰色卡片 **外部检索与证据**，含：
   - 是否请求联网检索：是/否
   - 检索状态：成功 / 部分成功 / 失败 / 未启用 / 未配置 …
   - 外部证据：N 条 · 缺口 M 项 + 缺口摘要
   - 在线核验：**仅当** `online_verified===true` 时显示「已在线核验」；否则「未在线核验」（烟雾报告为未在线核验）
   - 检索尝试编号：若有则显示
3. 投研任务跑完时，`#aiResearchStatus` 一行摘要含检索状态/证据数/是否在线核验（不调用新接口）。

## 4. 约束核对

- 未调用付费 AI；烟雾报告为本地写入。
- 未改默认检索模式（仍 fixture；live 需显式环境变量）。
- `online_verified` 失败/fixture/partial 不宣称已在线核验。
- 中文标签，无「二手日历」类黑话。

## 5. 验证

```text
node --check design/reports-runtime.js
node --check design/ai-runtime.js
# 均 OK（2026-10-14）
```

## 6. 未做

R3 live 实网、CON-01..09、经验页、A 档 01/02/03 完结标记。
