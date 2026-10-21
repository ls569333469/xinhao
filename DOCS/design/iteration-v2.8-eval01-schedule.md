# V2.8 EVAL-01 加深 · 观察计划调度与补查联动

日期：2026-10-13。状态：**部分实现**（仍非 EVAL-01 全验收）。

## 本刀交付

- eval_store 观察计划：save_plan / set_plan_enabled / list_plans / status
  - **默认 enabled=false**；须显式启用
  - server_effect：scheduler_bound、enabled_plan_ids、pause_new_entries_active
- scheduler_tick：到期开观察（尊重 REVIEW pause_new_entries）；对 open 观察做 rules settle 检查
- **F02 保持**：pending_backfill / unverifiable 不假 max_hold/win
- 补查联动：pending_backfill/unverifiable/data_gap → runtime/eval/backfill_links.jsonl（可选 CandleRanges.prepare）
- **自动入队（本刀）**：计划字段 `auto_enqueue_backfill`（**默认 false**）
  - true 时经 TaskService.submit_range（同 `/api/candles/backfills` 路径）入队 candle_range
  - 幂等：同观察窗口不重复入队；TaskService request_hash 复用 queued/running
  - 链接状态：linked / enqueued / linked_enqueue_failed / linked_enqueue_skipped；API/UI 可见
- TaskService 绑定：bind_eval_review → scheduler_bound=true + eval_store.task_service=self
- API：GET /api/eval/status|plans|backfill-links，PUT /api/eval/plans，POST .../enable
- UI：验证页「补查联动状态」表（状态/任务 id）
- 测试：计划默认关、暂停不新开、pending 出 backfill_link、enqueue 默认关/显式开/幂等/data_gap

## 仍缺

- 按冻结规则自动扫全市场开观察
- 资金费真实结算、与 SIM 自动开平
- 补查完成后自动再 settle（本刀只入队，不闭环）

备份：runtime/code-backups/eval-auto-enqueue-*
