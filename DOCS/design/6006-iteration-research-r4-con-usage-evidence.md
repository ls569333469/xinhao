# 投研迭代 R4：用量解析、多报告并发（离线）与经验文件 取证

- 时间：2026-10-14 约 21:10（Asia/Shanghai）
- 范围：RES-17、v1.2 CON-01..09（离线桩）、RES-20。**本轮没有新的收费模型调用。**
- **不**标记 A 档 01/02/03 完成。

## 1. RES-17 用量

**R3 报告为什么是 unknown**：不是供应商字段名特殊，也不是流式（请求体没有 stream）。旧代码在 `run()` 里把 `usage` 直接写死成 `"unknown"`，`_call_model` 只返回正文字符串，**原始供应商响应没有保存到任何地方**（report 目录只有 report.json、prompt_input.json；task-freeze 只有 attempts/evidence/intent/meta/retrieval；ai-calls 日志的完成记录只有 http_status）。

**补回结果**：`report-4dbefb0c984d40879ee9161e80edc9f2` 没有保存的原始响应，**无法补回**，保持 `usage=unknown`，report.json 未改动（哈希仍为 4b913da3…dad0）。没有估算、没有写 0。

**修复（ai_research.py）**：
- 新增 `parse_vendor_usage()`：支持 chat `usage.prompt_tokens/completion_tokens/total_tokens`、responses/anthropic `input_tokens/output_tokens`、gemini `usageMetadata`；状态分 known / partial / unknown；缺失为 null 不是 0；`reasoning_tokens` 只记录，OpenAI 风格已含在输出内，不重复相加（gemini 的 thoughts 单列，仅在供应商未给总量时才计入计算的总量）。
- 新增 `_call_model_result()` 返回结构化结果（text、usage、attempt_id、http_status、raw_payload）；旧 `_call_model()` 保留，仍返回字符串。
- 报告与 ai-calls 日志写入结构化 usage 和 `vendor_attempt_id`；报告目录新增 `vendor_response.json`（只存响应体，不含密钥/请求头），之后可审计。

## 2. v1.2 CON 离线桩（test_r4_con_usage.py，15 个用例全过）

| ID | 结果 | 说明 |
|---|---|---|
| CON-01 | 离线通过 | A 慢 B 快、B 先完成；正文/提交号/attempt/冻结目录互不串 |
| CON-02 | 离线通过 | 同币两次独立确认 → 两份报告、不复用、不覆盖（另有既有 RES-01/02/03 用例） |
| CON-03 | 离线通过（真并行待验证） | 新增全局在途上限，默认 2，环境变量 `WORKBENCH_RESEARCH_MAX_INFLIGHT`（1–8）；4 线程并发峰值 ≤2，全部完成。**任务执行器仍是单 worker 串行：排队已支持，真并行待验证** |
| CON-04 | 未在本轮新增 | 超时未知态沿用 R3/REV-04；未做 429 并发桩 |
| CON-05 | 离线通过 | 准备后全局 scan 换成 revision 2，执行仍用 revision 1 的数据 |
| CON-06 | 后端部分离线通过 | B 完成后 A 的 report.json 字节不变；页面阅读位置不被抢（前端）**未验** |
| CON-07 | 离线通过 | task_service 认领改为 `UPDATE … WHERE status='queued'` 且检查 rowcount；两个服务实例同时 tick 只执行一次。旧代码下该用例 5/5 失败 |
| CON-08 | 部分 | 失败币不污染成功报告、重投只调用失败币、用量不重复；**但“批次内自动只重试失败币”功能未实现**，目前靠对失败币新建确认 |
| CON-09 | 离线通过 | 3 币一次调用，用量只记一次（350），不给每币伪造分摊；日志 research 记录 1 条 |

全量回归：356 个用例中 348 过、8 个失败（test_rule_bindings / test_scan_contract / test_strategy_store，报“覆盖硬闸拒绝冻结”），这些模块不引用本轮改动文件，为既有问题，未处理。

## 3. RES-20 经验文件

`DOCS/experience/`：README（格式说明）+ EXP-0001..0004，全部“试用”。明确写了 **当前文件阶段，不是页面功能**，程序不自动读取。VELO 写作风格规则在项目内没找到，未录入；项目外 Codex 经验库不可读，未复制。

## 4. 服务

Python 改动后确认无 running/queued 任务，停旧 PID 22716，由 start-pages.py 重启，新 PID **37828**，127.0.0.1:8767/8768 监听，health 正常（version 2.8）。

## 5. 还没验证

- 真实并行供应商调用（执行器仍串行；在途上限只在桩里验过）。
- ~~下一次真实调用后 usage 是否解析为 known~~（用户确认不需要：用量在中转站查看；解析器保留不再扩展）。
- 实时检索接口未配置（仍是 fixture，online_verified=false）。
- 用户硬刷新页面核对（R3 清单仍待用户）。
- CON-04 429 桩、CON-06 前端、CON-08 批次部分重试功能。

## 6. 文件与 SHA-256

| 文件 | SHA-256 |
|---|---|
| ai_research.py | 4b5fa057f36c18c205ae95aa2ddfdd06c518b67cbae91e6ac8747a7db200ecbf |
| task_service.py | 4ea8c00372e8a91838c9df0463be20df4d001a7060aa9e853d2fb4787617fef5 |
| test_r4_con_usage.py | cf6062bdf6d68142c704f2f7fa80a021000b718f93aeeed845c7d2000c2146c3 |
| DOCS/experience/README.md | de0f6a085f0a99a91981192d33d0206da62e94546919d6a0ee23d41c3696dfd4 |
| DOCS/experience/EXP-0001-标准档超时.md | 752fdc9b2e5647a10b0a574254eb3742e3bbe3261932bfdeea7d50a68146910d |
| DOCS/experience/EXP-0002-用量未知与原始响应.md | 159cd794a0d370efba48e9f647adcd1d837dd0dfe28fbbfc2b575926462ce99d |
| DOCS/experience/EXP-0003-夹具非在线核验.md | 540feb912e6d80216ca3294d73dd9204b36afd9941be849e88be14d586526837 |
| DOCS/experience/EXP-0004-模板B八节与引用.md | 1c7c2959bd9da35d0a7439c7597f5d7f2a5c5ed42f4cb38f42fbf6c22d576a2d |
| runtime/reports/report-4dbefb0c…/report.json（未改） | 4b913da3ef9e83bd21ecb90576d14ea95d9c633652a9a9954e0cc069c0e0dad0 |

备份：`reserve-history/r4-con-usage-20261014T2100/`（改前 ai_research.py 5c175bb6…8f21、task_service.py 89a49ba8…e368、report.json）。