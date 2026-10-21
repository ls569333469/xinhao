# 投研迭代 R5：CON-04 / CON-08+REV-10 / CON-06 页面 / 检索 live 接线 取证

- 时间：2026-10-14 约 22:05（Asia/Shanghai）
- 依据：Codex 文档 v1.0（模板B需求）/ v1.1（复核补充）/ v1.2（多报告并发）为准；用户授权继续迭代。
- **本轮无收费模型调用**，全部离线桩。**不**标记 A 档 01/02/03 完成。
- 用量（usage）：**用户确认不需要**（在中转站查看）。R4 解析器保留不动，不再扩展；R3/R4 取证里的用量待办已划掉并注明“用户确认不需要”。v1.1 REV-10 / v1.2 §6 提到的“批次总用量”同样不做，属与文档冲突、按用户决定处理。

## 1. CON-04 限流 / 超时

| 场景 | 处理 | 测试 |
|---|---|---|
| 429 | 视为供应商未处理，有上限退避重试（默认 2 次，WORKBENCH_RESEARCH_429_RETRIES 0–5）；先用 Retry-After，否则 2s、4s…，上限 60s；等待时释放在途名额（排队）；每次尝试都记 attempt_id 与 rate_limited，另记 vendor_backoff | 429→429(Retry-After 7)→成功：等待 [2, 7]，日志/冻结目录各 2 条 rate_limited，名额归零 |
| 429 用尽 | 明确失败，提示“429 表示未处理，可稍后再试”；不标“可能计费” | 3 次请求后失败 |
| 超时 | 维持 REV-04：result_unknown_may_be_billed，**不自动重发** | 只发 1 次、无等待 |
| A 限流、B 正常 | B 的冻结目录无 A 的限流记录，报告各自独立 | 通过 |

## 2. CON-08 + v1.1 REV-10 多币部分失败恢复（RES-15/RES-14 相关）

- 选币数超过 WORKBENCH_RESEARCH_BATCH_SIZE（默认 5）时建批次：目标币集合冻结，按组调用；≤5 个仍是原来的单次调用（行为不变）。
- 批次文件：untime/research-batches/<batch_id>/（batch.json 逐组/逐币状态与报告号、request.json、evidence.json，不含密钥）。
- 每组只带本组币的证据；一组失败不影响其他组；结果 success / partial_success / failed。
- 只重试失败组：etry_batch_failed，同一 submission_id（同一确认），已成功组不重跑、报告字节不变；没有失败组时不调用模型。
- 结果未知（超时）的组默认**不**重试，须用户显式 include_unknown=true。
- 后台：任务类型 i_research_batch_retry 排队执行；接口 GET /api/reports/batches/<id>、POST /api/reports/batches/<id>/retry-failed（同批已排队则复用，不重复建任务；不存在 404）。
- 空 contracts 直接拒绝、不调用模型（RES-14 一部分）。
- 测试：test_r5_con_batch.py 7 个批次用例全部通过（含 TaskService 端到端）。

## 3. CON-06 页面部分（design/reports-runtime.js、design/ai-runtime.js）

- 报告页左侧新增“投研任务（各自独立）”列表：每个任务单独一行，显示币、状态、阶段、批次成功组数和失败币，完成的有“查看”，部分失败的有“只重试失败币”。
- 后台任务完成时：如果用户正在看别的报告（自己点过列表），**不切走**，只出现“新报告已完成 … 查看 / 关闭”提示；没在看别的报告时才自动打开新报告。
- prototype.html 不用改（脚本已引用）。node --check 两个文件通过；运行中的服务已返回新脚本。
- **页面效果未经人工验证**，需用户硬刷新后核对（见 §7）。

## 4. 检索 live 接线

文档要求“先检索后生成、可选 live”。代码里 live 路径（LiveHttpRetrievalAdapter）R2b 已接好，仅在 RESEARCH_RETRIEVAL_MODE=live 且配置 RESEARCH_RETRIEVAL_BASE_URL 时使用；默认 fixture。本轮补测试：默认 fixture；live 但未配置 → live 适配器报 not_configured，不标已联网。**未配置密钥，未做 live 验证，online_verified 仍为 false。**

## 5. 其余文档条目现状

| ID | 状态 | 说明 |
|---|---|---|
| RES-01..08、REV-01/02/03/08、CON-01/02/03/05/07/09 | 已离线通过（R1–R4） | 本轮回归仍通过 |
| CON-04、CON-08、REV-10、RES-15 | 本轮离线通过 | 批次总用量按用户决定不做 |
| CON-06 | 后端 + 页面代码完成 | 页面待用户硬刷新核对 |
| REV-04 | 通过（R3 真实超时记录） | 本轮补 429 |
| RES-14 | 部分 | 空 contracts 拒绝、逐组失败已做；无效引用/错误策略依赖的校验未做 |
| RES-17 | 用户确认不需要（用量部分） | 重试不漏记已由 attempt 日志覆盖 |
| RES-09、RES-12 | 未做 | 需真实多市场/同名币数据与内容核验 |
| RES-10/REV-05、RES-11/REV-06 | 只有文案 | 需真实数据核查口径/零值 |
| RES-16、REV-07、REV-09 | 未做 | 离线可做但工作量较大（修订追溯、行情/新闻截止时间字段、自动/人工核验分开），留下一轮 |
| RES-18 | 未在本轮复核 | 草案导出指纹已有既有测试 |
| RES-19 | 部分 | 本轮重启后服务返回新脚本；人工点验待用户 |
| RES-20 | 文件阶段（R4） | 未做页面 |
| CON-03 真并行 | 排队已支持，真并行待验证 | 执行器仍单 worker |

## 6. 测试

- 新增 test_r5_con_batch.py：13 个用例全过。
- 全量：369 个用例中 361 过、8 个失败，与 R4 相同（test_rule_bindings 5、test_scan_contract 1、test_strategy_store 2，报“覆盖硬闸拒绝冻结”），与本轮无关，未改动。

## 7. 需要用户的

1. 硬刷新 http://127.0.0.1:8767/ 报告页：同时提交两份投研，看左侧任务列表是否各自一行；先点开其中一份，另一份完成时是否只出提示不跳走。
2. 真实多币部分失败恢复、真实 429：需要收费调用授权才能验证。
3. live 检索：需要用户提供检索接口地址/密钥后才能做在线核验。

## 8. 服务

确认无排队/运行任务后，停 PID 37828，经 start-pages.py 重启，新 PID **38108**，8767/8768 监听，health 正常（2.8）；/api/reports/batches/<不存在> 返回 404，/reports-runtime.js 已是新版。

## 9. 文件与 SHA-256

| 文件 | SHA-256 |
|---|---|
| ai_research.py | 2506cf7904cd238024ad4dabb764ad34705fe63166b5c713f977b4f1d90a16d8 |
| design/ai-runtime.js | fa136b527638e80523176d84cdd34efef70b036c82b6ec98455718336e1e02fb |
| design/reports-runtime.js | b5e30632a3e8871aa1cc0605a74417bb6271c312efd39d2ae73d9bbb61e46419 |
| DOCS/design/iteration-research-r3-evidence.md | 6982181850f7a0d528d38fba3a238cd5971931dc59767c4dc05b74a8c995c82d |
| DOCS/design/iteration-research-r4-con-usage-evidence.md | f21161b2b1f45a5b1d47bbaa1dd6f511403f36de385c00147419068d0291be88 |
| server.py | 3b3abff618ded73fb3db3361c7334c00b74104fef5dd366eaadb87710be3aab2 |
| task_service.py | cd3e1c9ddfd7b054b0236dfe14c02ccb34dec95bc3e2bc9ca3526ac117050d69 |
| test_r5_con_batch.py | 8430adeadc138787233627eeed62fd79a7e0190d80ba9fcfe28244dee24e02ea |

备份：`reserve-history/r5-con04-08-06-20261014T2150/`（改前 ai_research.py、task_service.py、server.py、research_retrieval.py、reports-runtime.js、ai-runtime.js、R3/R4 取证文档）。research_retrieval.py 本轮未改。