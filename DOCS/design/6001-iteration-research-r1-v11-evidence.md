# 投研迭代 R1（v1.1 P1）离线取证 · 身份/两阶段冻结/去重

- **时间**：2026-10-14 19:54 Asia/Shanghai
- **切片**：R1 only（offline，无付费 AI）
- **对应**：v1.0 R1 + v1.1 REV-01 / REV-02 / REV-04（部分）
- **状态**：**离线通过**；**未**宣称完整投研完成；**未**标 01/02/03 A 档完成；**未**跑 R2a/R2b/R3
- **备份目录**：`reserve-history/r1-v11-backup-20261014-194900/`（含改前源码与 `workbench.sqlite3.pre-r1`）

## 1. 本轮做了什么（前后逻辑）

### 1.1 去重身份：`request_hash` → `submission_id`（REV-02）

| 之前 | 之后 |
|---|---|
| `one_active_research` / `one_active_scan_research` 按 `request_hash` 唯一，相同内容的第二次独立确认会被挡住/合并 | **DROP** 上述索引（含**现存库升级路径**）；新建 `one_submission_research` 按 `submission_id` 唯一 |
| `submit_research` 按 kind+request_hash 查活跃任务并 reuse | 按 `submission_id`：同意图 → 同任务；不同意图 → `Conflict`（HTTP 409）；新 ID → 新任务 |
| 内容哈希可决定是否跳过模型返回旧报告 | `run()` 默认 **不** 因内容哈希复用旧 AI 正文；仅显式 `reuse_completed_report=true` 可复用；独立确认写 `research_cache_ignored` 日志 |

### 1.2 两阶段冻结（REV-01）

1. **意图冻结（确认时）**：`submission_id`、明确合约列表（`scope_semantics=explicit_contracts`）、mode、档位、用户编辑、模型/配置/模板版本、`retrieval_strategy`、预算摘要；快照模式绑定具体 revision；扫描并投研标记 revision=`TBD` 并绑定 `settings_version`。预览行情**不**当作执行证据（`preview_only_market`）。
2. **证据冻结（本轮扫描/准备模型输入后）**：绑定真实 `scan_id`/`revision`、evidence_hash、缺口、模型输入版本；写入 `runtime/task-freeze/{task_id}/evidence_freeze.json`。扫描产生 revision B 时确认 ID 不变。

同币池仅屏幕重排：`intent_hash` 对合约排序后计算，**不**无意义失效预览。

### 1.3 持久化与恢复（REV-02）

- 任务行新增：`submission_id` / `intent_hash` / `freeze_phase`
- 冻结包：`runtime/task-freeze/{task_id}/{intent,evidence,meta,attempts}.json(l)` — 非敏感；凭证仍走既有安全存储
- Worker 重启可从磁盘恢复 evidence，不再只依赖进程内 `_research_evidence`

### 1.4 调用边界（REV-04 部分）

- 同确认事务性创建一个逻辑任务；每个供应商请求有 `attempt_id`
- 本地超时记 `result_unknown_may_be_billed`，**不**自动无限重试
- 缺凭证在模型 I/O 前终止
- 成功独立投研不复用旧 AI 正文

### 1.5 与 v1.2 不冲突的隔离底线（本轮已落实，并发本身延后）

| 点 | R1 现状 |
|---|---|
| 每任务冻结输入 | `task-freeze/{task_id}/` + 任务行 `request` |
| 同币报告不覆盖 | `report-{uuid}/` 目录，双独立确认各写各的 |
| 费用归属句柄 | `attempt_id` + `submission_id`/`task_id` 写入 call log / attempts.jsonl |
| 进度 | 任务行 `progress` 按 task 更新；**进程内** `AiResearch.progress` 仍是单例 — 真正多任务并行隔离见 v1.2 |

## 2. 验收对照（离线）

| ID | 结果 | 说明 |
|---|---|---|
| RES-01 | 离线通过 | 两独立确认同内容 → 两 task/两 report/两次模型桩调用 |
| RES-02 | 离线通过 | 两不同 `submission_id` 可同时 queued，不按内容哈希合并 |
| RES-03 | 离线通过 | 同 ID 重传 reuse；同 ID 改意图 Conflict |
| RES-04 | 离线通过 | preview `billing=preview_only_no_model_call`；取消路径未改 |
| RES-05 | 离线通过 | `preview_intent_hash` 与当前不一致 → Conflict；focus 变更改 intent_hash |
| RES-06 | 离线通过 | scan_and_research 成功绑定 revision=7 / scan-new；失败不投研 |
| REV-01 | 离线通过 | 意图/证据两阶段字段与 freeze 落盘；B 不绑预览 A |
| REV-02 | 离线通过 | 旧库副本升级：丢 research 的 request_hash 唯一索引、加 submission 列/索引；历史行保留（探针 TASK_ROWS=4） |
| REV-04 | 部分离线通过 | attempt_id / 未知超时语义 / 确认前终止；**未**做真实供应商超时崩溃恢复（待授权 R3） |

**未做**：RES-07+ 检索、模板 B UI、真实付费、经验库页。

## 3. 文件与 SHA-256（本轮交付）

| 文件 | SHA-256（改后） | 改前（备份） |
|---|---|---|
| ai_research.py | `95a55b5a6ed691d116662b10406fcb6c26f860ed5dc0c63ee4e919066ce09686` | `362462c146cc5e562862551941e8a33cc31b5ed5b582a0a20a600f2f948cf0e3` |
| task_service.py | `89a49ba8e6f1937018d8dbf141ac5da85aecda848f8c1043c77826946879e368` | `fe62516b86b44ed52b4ab7ed4d14330c552d037568316d2ee573cf014238080c` |
| test_ai_research.py | `783fed0a91d270f48dd0101bf421ed6f667fba515aa7cdb49f9327ccb34818f0` | （测试更新） |
| test_task_service.py | `d892dd9befb547c9c2ca1c40ca12f04318d88a44769c1efa568ecf047f74d986` | （测试更新） |

## 4. 验证命令与结果

```text
python -m pytest test_ai_research.py test_task_service.py -q
→ 43 passed, 6 subtests passed
```

旧库副本升级探针：`runtime/_r1_upgrade_probe_root` — `one_active_research`/`one_active_scan_research` 已移除，`one_submission_research` 与 `submission_id` 列存在，历史任务未删。

## 5. 检索路线备注（R2b 未实现）

推荐 **先检索后生成（retrieve_then_generate）**，便于接现有 `evidence_id` 与证据冻结时序（v1.1 REV-03）。代码中 `RETRIEVAL_STRATEGY_NOTE = "retrieve_then_generate"` 仅文档/意图字段，**无**真实检索适配器。

## 6. v1.2 延后（多报告并发）

依据 `给GROK-多报告并发补充-v1.2.md` §7 末段：

> 优先随 R1 做身份/隔离，随 R2 接实际请求/快照/页面，R3 再在已有授权范围验证真实并发。

**本轮明确延后（CON-01..09 全部）**：

- 全局并发上限 / 排队调度 / 公平批次
- 多 worker 认领、真正并行生成
- 扫描交错时不可变快照副本（R1 已在绑定后写入 task-freeze 的 scan_id/revision，但未做全量快照拷贝）
- 页面多任务进度 UI、不抢阅读位置
- 多币共享 usage 分摊规则的完整实现

R1 已保证：独立确认不合并、每任务冻结目录、唯一 report 路径、attempt_id — **不阻塞**后续 v1.2 接入。

## 7. 残留与下一步

| 项 | 状态 |
|---|---|
| R2a 模板 B UI/schema/旧报告兼容 | 下一步 |
| R2b 真实检索（先检索后生成） | R2a 后 |
| R3 授权范围内真实调用与并发 | 后置 |
| 前端把 preview 的 `submission_id`/`intent_hash` 带回 confirm | 建议随 R2a 接线（后端已支持；缺省则每次新 ID=独立确认） |
| 进程内 `AiResearch.progress` 单例 | v1.2 隔离时改为 per-task |
| 直播库升级 | 已在**副本**验证；进程重启加载新代码时会自动 ALTER/DROP；未在本轮对正在服务的直播库做破坏性操作之外的手工迁移 |

## 8. 开放风险

1. 前端若未传 `submission_id`，连点可能变成多次独立确认（各新 ID）— 需 UI 带回 preview 的 ID 才能幂等。
2. `scan.json` 仍是全局最新文件；并发扫描交错的不可变副本属 v1.2 CON-05，R1 单任务路径在 bind 后落盘 revision，但未复制全快照。
3. 供应商超时时“可能计费”仅本地记账，无供应商侧幂等查询（REV-04 已知边界）。
4. 未跑页面点验 / 未跑真实模型。
