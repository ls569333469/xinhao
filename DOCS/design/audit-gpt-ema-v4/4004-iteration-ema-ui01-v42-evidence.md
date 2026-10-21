# iteration-ema-ui01-v42 证据（仅 UI01 / POOL）

整理时间：2026-10-16 01:22:18（北京时间）。

本轮只做 **持续观察池** 与 **确认点火事件** 独立视图，以及 store 摘要挂上本周期确认事件列表。  
**未做** UI02/UI03/UI04；**未改** 交易撮合/止损数学；**未重启** 服务；**未宣称** A 档完成。

## 备份

`runtime/code-backups/ema-ui01-v42-20261016-011800/`

## 改了什么

| 文件 | 作用 |
|---|---|
| `ema_squeeze_store.py` | `evaluate_row` 摘要增加 `confirmedEvents` / `latestConfirm` / `brokeAboveThree`；新增 `hydrate_rows_from_store` |
| `server.py` | 读 `/api/scan` 与原型页嵌入快照时，用 store 合并池/确认字段（不改本根 `ignitionGrade`） |
| `design/market-evidence.js` | 共用 `cycleConfirmEvents` / `isActivePoolRow` / `hasCycleConfirm` / `confirmEventDetailsHtml`；扩展列区分「本根分级」与「周期确认」 |
| `design/pro-table-runtime.js` | 结果视图三选一：普通六指标 / 持续观察池 / 确认点火事件；详情可展开确认事件字段 |
| `index.html` | 同上结果视图与详情块；`integrate-evidence` 已同步内嵌 MarketEvidence |
| `test_ema_ui01_pool_filters.py` | 离线复现：勾④会丢、池/确认视图仍保留 |

清单文档已归档：`DOCS/design/audit-gpt-ema-v4/给GROK-EMA三线金叉统一命名与前端收口清单-v4.2.md`

## before → after

| 点 | 改前 | 改后 |
|---|---|---|
| 勾④ + 已突破三线 | 普通 AND 把币丢掉（`belowAllEma=false`） | **普通六指标**仍如此；切到 **持续观察池** 按 `candidateStatus=active` 保留 |
| 本根 `ignitionGrade=none` 但周期已有 strong 确认 | 勾「强」点火筛 false | **确认点火事件**视图按本周期 `event_id` 事件的 `level` 过滤 |
| 行上 stage | 往往只有当轮 `crossConfirm` | 挂上本周期 `confirmedEvents`（含 cross_time / confirm_time / event_id / candidate_cycle_id / rules_version / cross_ema99） |
| 详情 | 难看到完整确认证据 | 可展开「EMA三线金叉 · 池与确认」 |

## 离线验收

```text
python -m unittest test_ema_ui01_pool_filters.py test_ema_squeeze_v4.py -v
```

关键断言：`test_repro_i4_drops_but_pool_and_confirm_keep`、`test_store_attaches_confirmed_events_after_confirm` 已通过。

## 怎么点开看（硬刷新）

1. 浏览器硬刷新 8767（初版）或 8768（工作台）。
2. 左侧找到 **结果视图** 下拉：
   - **普通六指标**：行为与原来一致；勾④仍要求当根三线下方。
   - **持续观察池**：只看活跃候选；不要求当根仍低于三线。
   - **确认点火事件**：看本周期确认；勾强/中/弱按**事件级别**，不是本根分级。
3. 点开合约详情，看「池与确认」展开项：event_id、周期 id、规则版本、金叉根 EMA99 等。

## 已知缺口 / 阻塞

- 当前磁盘 `scan.json` **行上还没有** `emaSqueezeStage`（旧快照）；`runtime/ema-squeeze-candidates.json` 也可能尚未生成。  
  → 硬刷新后池/确认视图可能先是空的或大量「未记录」，这是数据缺口，不是筛选逻辑没写。
- **Python 进程要重启** 后，`/api/scan` 的 hydrate 与新 store 字段才会进正在跑的服务；再 **跑一轮正式扫描** 才会把 stage 写进新快照。  
  （本轮按要求未重启、未替你点扫描。）
- UI02 指导保存、UI03 模拟、UI04 回测未做。
- 出池规则（30 天 / OI70% / 新低）后端原本已有；本轮 FE 只消费 `candidateStatus`，未新造生命周期。

## 刷新结论

- 前端脚本：硬刷新即可加载新控件（是）。
- 池/确认 **实况数据**：仍需用户确认后的 **Python 重启 + 新扫描**（或至少重启以启用 `/api/scan` hydrate；若 store 里已有活跃周期，重启后旧 scan 行也能补上池字段）（是，重启仍需要才能在现网看到 store 字段）。
