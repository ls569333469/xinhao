# 投研迭代 R2b（v1.1 REV-03）离线取证 · 先检索后生成

- **时间**：2026-10-14 20:12 Asia/Shanghai
- **切片**：R2b only（offline fixtures + 路径实现；无付费 LLM；无 R3 真实供应商；无 CON 并发；无经验库页）
- **对应**：模板 B 需求 §5 / RES-07 / RES-08；复核补充 v1.1 **REV-03**（选定 **先检索后生成**）
- **状态**：**离线路径通过**；**未**宣称 live 联网全文核验；**未**标 01/02/03 A 档完成；**未**跑 R3
- **备份目录**：见 apply 脚本创建的 `reserve-history/r2b-retrieve-backup-*/`

## 1. 路线选择（REV-03）

| 选项 | 本轮 |
|---|---|
| **先检索、后生成**（接 evidence_id / 两阶段冻结） | **已选定并实现** |
| 模型原生工具检索 | 未实现（封装仍以字符串生成为主；R3 若授权再评估） |

时序：执行时 **先**跑检索适配器 → 登记/冻结证据（id、hash、时间、coverage/gaps）→ **再**构建模型输入（仅允许引用 `allowed_evidence_ids` + 本轮扫描行）。  
**勾选检索 ≠ 已检索**：`requested_search=true` 只表示意图；`retrieval.status` 必须反映实际结果（success / partial_success / failed / disabled / not_configured / unsupported）。

## 2. 本轮做了什么（前后逻辑）

### 2.1 适配器接入（非「请联网」提示词）

| 之前 | 之后 |
|---|---|
| `run()` 内 `retrieval.status=unverified`、`adapter=None`、`adapter_implemented=False` | 接入 `research_retrieval.py`；`AiResearch(..., retrieval_adapter=)` 可注入；默认 `resolve_adapter` |
| 证据冻结 `retrieval_status=not_run_in_r1` | 冻结写入真实 `retrieval_status` + bundle hash + external ids + gaps |
| 模型 payload 无外部证据 ID 约束 | `external_evidence` + `allowed_evidence_ids` + `citation_policy` |

### 2.2 模式：fixture（默认）vs live（可选）

| 模式 | 如何启用 | 含义 |
|---|---|---|
| **fixture**（默认） | `RESEARCH_RETRIEVAL_MODE` 未设或 `=fixture` | 离线夹具证明登记/冻结/状态；**不是** live 全文核验；`online_verified=false` |
| **live** | `RESEARCH_RETRIEVAL_MODE=live` + `RESEARCH_RETRIEVAL_BASE_URL`（可选 `RESEARCH_RETRIEVAL_API_KEY`） | HTTP GET 搜索/摘录；仅登记实际返回；`content_level=excerpt`，**不**把 URL/摘要冒充全文核验 |
| live 缺 base | mode=live 但无 URL | `status=not_configured`，`online_verified=false` |

夹具目录：`design/retrieval-fixtures/`（`ok` / `partial` / `empty_news`；代码内建 `timeout` / `http_404` / `unimplemented`）。

### 2.3 失败态（RES-07 / RES-08）

| 场景 | status | online_verified | 说明 |
|---|---|---|---|
| 未勾选检索 | `disabled` | false | 路径存在但未跑 |
| fixture ok | `success` | **false** | 成功登记，但是 fixture 非 live |
| live 200 摘录 | `success` | true（仅 live+ok） | 仍 `content_level=excerpt` |
| timeout | `failed` | false | 保存错误/attempt |
| HTTP 404 | `failed` | false | 条目 `fetch_status=http_404` |
| 部分取得 / 动态未加载 | `partial_success` | false | gaps 必有 |
| 来源未实现 | `failed` | false | `unimplemented_source` |
| 无新闻命中 | `failed` + gaps | false | **不得**当「无事件已核验」 |
| live 未配置 | `not_configured` | false | |

### 2.4 attempt / usage（REV-04）

- 每次检索有 `retrieval-attempt-*` 的 `attempt_id`，写入 `runtime/ai-calls` 与 `task-freeze/{task_id}/attempts.jsonl`
- `retrieval.usage`：`requests` / `bytes`（HTTP 或 fixture 计数）；**无 LLM token**（本轮不调用付费模型做检索）
- 完整检索结果落盘：`task-freeze/{task_id}/retrieval.json` + `evidence_freeze.json`

## 3. 验收对照（离线）

| ID | 结果 | 说明 |
|---|---|---|
| REV-03 路线 | 已选定 | 先检索后生成；证据见本文件与代码 |
| RES-07 | 离线通过 | success / 未实现 / timeout / 404 / partial 均保存真实状态；失败不标已联网 |
| RES-08 | 离线通过 | 无新闻、动态未加载 → gaps；不当无事件/已核验 |
| 勾选≠已检索 | 离线通过 | `requested_search=false` → `disabled` |
| evidence 冻结 | 离线通过 | id / bundle hash / time / coverage / gaps |
| 模型输入约束 | 离线通过 | 仅 `allowed_evidence_ids` |
| 付费 LLM | **未调用** | 测试用 mock model |
| live 供应商 | **未真实外呼** | opener 桩证明路径；R3 待授权 |

## 4. 文件与 SHA-256

（apply 后由脚本回填；下列为箱内构建哈希。）

| 文件 | 角色 |
|---|---|
| `research_retrieval.py` | 新增：适配器 / fixture / live 骨架 |
| `ai_research.py` | 改：`run()` 先检索后冻结再生成 |
| `design/retrieval-fixtures/*` | 离线夹具 |
| `test_r2b_retrieve_then_generate.py` | 离线验收 |
| `test_ai_research.py` | 回归：无检索时 status=`disabled` |
| `DOCS/design/iteration-research-r2b-evidence.md` | 本证据 |
| `DOCS/design/requirements.md` | 短追加 |

## 5. 验证命令与结果

```text
python -m unittest test_r2b_retrieve_then_generate test_ai_research -q
→ 27 tests OK（箱内 2026-10-14；Windows 应用后复跑）
```

## 6. 如何启用 live 检索（R3 前自助）

1. 设置环境变量（进程级，服务重启后生效）：
   - `RESEARCH_RETRIEVAL_MODE=live`
   - `RESEARCH_RETRIEVAL_BASE_URL=https://你的搜索或资料API根`
   - 可选：`RESEARCH_RETRIEVAL_API_KEY=...`（勿写入报告/导出）
2. 确认标准档勾选检索（`requested_search=true`）。
3. 观察 `task-freeze/{task_id}/retrieval.json` 与报告 `retrieval.status` / `online_verified`。
4. **不要**在未取得正文时把 URL 摘要标成全文核验。

当前默认保持 **fixture**，避免无 Key 时误报联网成功。

## 7. 残留与 R3 门禁

| 项 | 状态 |
|---|---|
| R3 授权范围内真实供应商调用 | **仍需** |
| 用户硬刷新 + 页面点验检索状态展示 | **仍需**（若 UI 尚未展示新 retrieval 字段，需后续接线；本切片后端已写入报告/冻结） |
| 经验库页 | 后置 |
| CON-01..09 并发套件 | 后置（身份隔离已在 R1） |
| 原生工具检索路线 | 未选；若供应商强制 tool-calling 再开切片 |
| A 档 01/02/03 | **不**因本轮标完成 |

## 8. 未宣称

完整投研完成、最新资料已 live 核验、付费闭环、A 档完成、二手日历类措辞、URL 即全文。

## 4b. Apply 后 SHA-256

- 备份：`D:/Axiangmu/异动观察工作台/reserve-history/r2b-retrieve-backup-20261014-201009`
- 时间戳：2026-10-14 20:10:57 Asia/Shanghai

| 文件 | SHA-256 |
|---|---|
| `ai_research.py` | `5c175bb6e42b8f125152d6fe76cc0c8c7b832b8193b5dd87a8042d2d1ebc8f21` |
| `research_retrieval.py` | `e5f785a2aa33d3c354a286408da0f298c6303383fef962c1a962264c1864472e` |
| `test_r2b_retrieve_then_generate.py` | `0e27e14c6f361ffcc80e57be6f4fbd283aa65543f1fb700e12e0715fdcde7598` |
| `test_ai_research.py` | `4e74a374efa836fdca1fbd748cb34fcbe65ebeef5a53f8b3507f67f1b484b592` |
| `DOCS/design/iteration-research-r2b-evidence.md` | `628d83e6b9d65b0d0ff4d7278e6a0e10515c59504198c2b4f090bae720edd524` |
| `DOCS/design/requirements.md` | `aba6efa5a0ff83c8c98e9e4c8a32fbd3afddfe0d9b118983ad1d2d26f0cba7bf` |
| `DOCS/design/requirements-r2b-progress.md` | `4c5cd4c534e7b2517057a3e67420f5e1bc19bcadac4d47e0c4de1d3984db98a9` |
| `design/retrieval-fixtures/empty_news.json` | `b84bcb85ebed39718e9a97dc90ba45e1806549a48706a6f8705a5b2ad64c5c42` |
| `design/retrieval-fixtures/ok.json` | `f3847cf11b3de2ead06d85e3dc25839da3aaba438721ecebcb8f1b2b1074bc6d` |
| `design/retrieval-fixtures/partial.json` | `3ee58622b206511812cfde01334521a9491f4d2af3d3a33c076985c4fdda8d75` |
| `design/retrieval-fixtures/README.md` | `3d7f179cb03d2ef8bcf72c49f2959a127e181bb973fb09b50e6f4c52927bcbca` |

## 9. UI 只读展示（后续）

- 2026-10-14：报告详情 / 投研进度只读展示 retrieval 字段；证据见 iteration-research-r2b-ui-evidence.md。
