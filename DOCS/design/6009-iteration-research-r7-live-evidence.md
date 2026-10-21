# 投研迭代 R7：真实 API 核验取证

- 时间：2026-10-14 约 22:30（Asia/Shanghai）
- 路径：真实工作台 HTTP（Origin 同源）→ 任务队列 → 真实供应商调用。授权范围：至多约 6 次；本轮实际 **5 次**供应商请求（2 成功、3 超时未知，均未盲重试）。
- **无用量工作**。**不**标记 A 档 01/02/03 完成。硬刷新仍属用户。

## 1. 真实调用一览

| # | 任务 | 币 | 结果 | report / attempt | 耗时 |
|---|---|---|---|---|---|
| 1 | task-8839ac… | STRKUSDT | 成功 | report-ea143a0e18f64edab09852417e06c7e5 / attempt-c896c99a… | ~188s |
| 2 | task-b3bc05… | SOLUSDT | 成功 | report-e56f26e4bd5d4475afe14752e7965535 / attempt-e6b1a170… | ~144s |
| 3 | task-8123bb… | STRKUSDT（同币第二次） | 超时未知 | attempt-af2e4910…；未建报告；未自动重试 | >300s |
| 4 | batch 组0 | ETHUSDT+ARBUSDT | 超时未知 | attempt-c44ed2e4… | >300s |
| 5 | batch 组1 | OPUSDT | 超时未知 | （同批） | >300s |

批次：batch-a17a5c2d9af04de8abaf31c9ee7ea1c2 / task-493dc1a9… / sub-aaa0a08e…（临时 `WORKBENCH_RESEARCH_BATCH_SIZE=2`，验完已清环境并重启，恢复默认 5）。

## 2. 标准档单币（STRK #1）

| 检查 | 结果 |
|---|---|
| 深度 / 提示词 / body_template | standard / research-prompts-v1.1-template-b / B |
| 八节 Markdown | 正文有 ## 1–8 八节；正文后有 JSON 附件 |
| schema_validation（落盘时） | 写成 partial（旧解析器不认「Markdown+JSON 附件」） |
| schema 修正后重算 | **success，八节齐全**（见 §6 小修） |
| RES-14 自动校验 | 落盘时 passed（引用/合约/依赖）；修正「当前价格」规则后重算为 **failed**（开头写「当前价格」同一行未注截至） |
| REV-07 数据时间 | 有；行情/技术面均为 stale（快照约 26–30 小时前）；新闻 as_retrieved |
| REV-09 三块 | automatic / content_review=pending / human=not_reviewed |
| 检索 | fixture success，online_verified=false，allowed=[ev-fixture-ok-catalyst] |
| vendor_response.json | 已保存 |

SOL 报告同样：八节齐全（修正后）、cutoffs/checks/fixture/vendor_response 齐全；「当前价格」开头同样触发修正后的自动失败。

## 3. CON-01 / 02 / 03（三次单币排队）

- 三个任务几乎同时提交，全部 202 入队，各自 submission_id / task_id。
- **执行是串行排队，不是真并行**：started 时间互不重叠（STRK1 14:00→14:03 → SOL 14:03→14:05 → STRK2 14:05→14:10）。max simultaneous running = 1。文档口径：**排队已支持，真并行待验证**。
- CON-01 隔离：两份成功报告 submission_id、contracts、attempt_id、冻结目录互不串；STRK#2 的 unknown 记录不出现在 STRK#1 冻结目录。
- CON-02 同币两次：STRK#1 成功报告保留；STRK#2 超时未知、未覆盖 #1 的 report.json。
- CON-04（顺带）：超时 → result_unknown_may_be_billed，未自动重试。

## 4. CON-08 真实批次

- 3 币、临时 batch_size=2 → 两组（ETH+ARB / OP），目标集合冻结。
- 两组均超时未知 → 批次 failed，无成功报告；**因此本轮未能用成功组验证「只重试失败、不重跑成功」**。
- 接口与状态机可用：失败提示含 batch_id；默认重试跳过 unknown（需 include_unknown）；为守住约 6 次上限，**未再付费重试 unknown**。
- 临时 batch_size 已恢复默认 5（清环境后重启）。

## 5. RES-09 / RES-12（对照新报告正文）

| ID | 结果 | 说明 |
|---|---|---|
| RES-09 | **部分通过** | 两份报告均标明 USDⓈ-M 身份、未串 USDC；价格写了可得时间「不是本次报告日期的实时价格」。但标题行仍写「当前价格：$…」未在同行写截至（已纳入自动校验）。未覆盖倍数合约/同名币对照（本轮未选这类币）。 |
| RES-12 | **部分通过** | 未输出「精确胜率」；SOL 明确「…不能证明主力仓位」。未做无对照 OI/量的专项负例（需刻意缺数据夹具/实况）。 |

## 6. 本轮小修（非新付费）

1. `research_review._parse`：支持扫描行里的毫秒时间戳（否则技术面截止一直 unknown）+ 测试。
2. schema：Markdown 八节 + 文末 JSON 附件时按全文计八节（否则假 partial）。
3. RES-14：开头「当前价格/当前价/现价」同一行无「截至/快照/可得于」→ 自动失败（REV-07 文案要求落地）。

旧报告文件未改写；上述 2/3 对已落盘报告是「重算验证」。offline 测试：test_r6_review 13 过；全量 374 过 / 8 失败（仍是既有冻结闸，未动）。

## 7. 服务

中间为加载小修与临时 batch_size=2 重启过（PID 13436→30824→**39472**）。验完清环境再重启，默认 batch_size=5。health 正常。

## 8. 还需用户

1. 硬刷新看报告页时间/校验行与任务板。
2. CON-08「只重试失败且保留成功组」需至少一组成功；可另授权小批重试（或提高超时）。
3. CON-03 真并行需改执行器为多 worker。
4. live 检索仍需检索接口。
5. RES-09 倍数/同名币、RES-12 缺对照数据负例待专项。

## 9. 关键文件 SHA-256

| 文件 | SHA-256 |
|---|---|
| research_review.py | 0003ba4bbd190f1406c2ce720f7202809b09bbf798c16c5df16520e9b650e9f1 |
| ai_research.py | b041429d9c2047084ac49858b4fa6c6356103718078a3e37cb3e16aaa37a18cc |
| test_r6_review.py | 907010f758279e7c29b177d19bec6a00e643a91a01e5d4c0bd9dbfc126ad3f4f |

备份：`reserve-history/r7-live-20261014T2200/`。