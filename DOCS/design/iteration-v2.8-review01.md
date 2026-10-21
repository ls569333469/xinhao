# V2.8 REVIEW-01 首刀 · 数据复盘与持久计划

日期：2026-10-13。状态：**部分实现**（非 REVIEW-01 全验收）。

## 本刀交付

- `review_store.py`：`runtime/reviews/` 下落盘
  - **数据复盘**：拉取 EVAL 成绩/结算尾部 + SIM 账户快照，保存复盘记录；**绝不调用模型**
  - **手动 AI 复盘**：显式触发；未配置 Key → 清晰中文错误；已配置 Key → 落门禁通过记录 + 数值快照，**本刀不发起付费模型调用**（`invoke_ai=true` 明确拒绝）
  - **计划**：每日/每周（`time_of_day`、`weekday`、`timezone`、`enabled`、`next_run_at`）；同 kind 重合计划去重；同 `period_key` 重合复盘去重
  - **`pause_new_entries`**：持久标志，EVAL/SIM 可只读；暂停**不**关闭原观察/仓位
  - **服务端生效状态**：`server_effect`（暂停是否生效、启用计划列表、调度是否绑定）
- API：`GET/POST` 见下
- UI：验证与复盘页「数据复盘与计划」面板（`design/review-runtime.js`）
- 离线测试：`test_review_store.py`

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/review/status` | 暂停标志、计划摘要、server_effect |
| GET | `/api/review/reviews` | 复盘列表（瘦身摘要） |
| GET | `/api/review/reviews/rev-…` | 单条完整复盘 |
| POST | `/api/review/data` | 创建数据复盘（无 AI） |
| POST | `/api/review/ai` | 手动 AI 门禁路径 |
| GET | `/api/review/plans` | 计划列表 + 生效状态 |
| PUT | `/api/review/plans` | 保存计划（默认 enabled=false） |
| POST | `/api/review/plans/plan-…/enable` | `{"enabled": true\|false}` |
| PUT | `/api/review/pause-new-entries` | `{"pause_new_entries": true\|false}` |

## 未做（后续刀）

- 按 `next_run_at` 自动触发复盘调度（`scheduler_bound=false`）
- 接通 AI-03 复盘叙述模板的真实付费调用
- EVAL/SIM 写入路径硬读 `pause_new_entries`（本刀仅提供只读 getter）
- 每 N 小时复盘频率、重要变化触发
- STRUCT-01 / BACKTEST-01 / EXT-01

## 验收对照（首刀）

| 条件 | 本刀 |
|---|---|
| 数值更新不调用 AI | ✅ 数据复盘 `ai_called=false` |
| 重合复盘去重 | ✅ `period_key` + 同 kind 计划 |
| 暂停新入场不停止原仓跟踪 | ✅ 仅标志；文案与 API 明确 |
| 显示服务端生效状态 | ✅ `server_effect` + UI |
| AI 无 Key 清晰错误 / 不自动计费 | ✅ |

