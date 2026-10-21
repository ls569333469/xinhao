# 投研迭代 R6：RES-16 / REV-07 / REV-09 / RES-14 取证

- 时间：2026-10-14 约 22:15（Asia/Shanghai）
- 依据：Codex 文档 v1.0 §6（生成后核验、原始输出与最终报告分存）、v1.1 REV-07 / REV-09、v1.0 RES-14 / RES-16。
- **本轮无收费模型调用，无用量相关工作**（用户确认不需要）。**不**标记 A 档 01/02/03 完成。
- 新模块 `research_review.py`（纯函数 + 小文件存储，不调用模型）；`ai_research.py` 在生成报告时写入新字段；旧报告照常读取（无新字段时页面不显示）。

## 1. RES-14 无效引用 / 错误策略依赖（自动校验）

报告新增 `checks.automatic`（版本 post-gen-checks-v1，checker=program）：
- 非空正文；
- 引用存在性：正文里的 `ev-…` 必须在本次 allowed_evidence_ids 内，否则列出无效引用；
- 选中合约覆盖：正文没提到的选中币列出；
- 策略依赖：草案 depends_on 字段须在本次冻结证据中出现；草案币种须在选中合约内。
- 说明写明：引用存在 ≠ 来源支持结论，不代表内容充分或人工已核验。

## 2. REV-07 行情 / 技术面 / 新闻分开的截止时间

报告新增 `data_cutoffs`（时效策略版本 freshness-v1）：
- 模式“本次知识时点研究（行情截至 …）”，不写成当前价；
- 行情：快照时间，超过 8 小时标已过期；技术面：K 线收盘时间，超过 8 小时（4h K 线 + 一个周期）标已过期；未提供则“未知”，不当作最新；
- 新闻：分别记录最新发布时间和检索时间，状态“按检索时间”，发布日期旧**不**直接判失效；
- 阈值写在字段里；用固定时钟测了边界（正好 8 小时不过期，多 1 分钟过期）。
- 未新建“历史 as-of”模式（v1.1 写明本轮不强制）。

## 3. REV-09 自动 / 内容待办 / 人工分开

`checks` 三块互不合并：automatic（程序结果）、content_review（内容核验待办清单，始终 pending）、human（默认“未人工核验（可阅读，但不等于人工通过）”）。
人工记录：`POST /api/reports/<id>/human-reviews`，须写核验人，结果只能是 通过/不通过/部分通过，可指定版本（0=原始）；写入 human_reviews.json，不改自动结果。

## 4. RES-16 生成后修订追溯

`POST /api/reports/<id>/revisions`：须写修改人、修改原因、正文；可带生成后补充证据，自动编号 `ev-post-…`、标“生成后补充；原模型未看过”。
存为 revisions.json，**不改** report.json 原始模型输出和 prompt_input.json（测试按字节比对）。读取时返回 revisions 列表、current_version、final_content，原 content 仍是模型原文。草案新增 report_version=0，绑定原始版本。

## 5. 页面

报告详情顶部新增一行小字：知识时点与行情/技术面/新闻时间、自动校验结果（列出无效引用/未覆盖/依赖问题）、内容核验待办、人工核验状态、修订版号。node --check 通过。**页面上还没有填写修订/人工核验的表单**，目前通过接口提交；页面效果待用户硬刷新核对。

## 6. 测试

- 新增 test_r6_review.py：11 个用例全过（无效引用、漏币、空正文、错误依赖、固定时钟截止与边界、修订不改原文、人工与自动分开、非法 ID）。
- 全量：380 个用例 372 过、8 个失败，与之前相同（test_rule_bindings 5、test_scan_contract 1、test_strategy_store 2，“覆盖硬闸拒绝冻结”），与本轮无关，未改动。

## 7. 仍需用户 / 未完成

1. 硬刷新报告页，看新报告顶部的时间/校验/核验行（旧报告如 STRK R3 报告没有这些字段，不显示，属正常）。
2. 新字段只在新生成的报告里有，要真实看到需要一次新的收费调用（需授权）。
3. 修订和人工核验的页面表单未做（接口已可用）。
4. 其余：RES-09、RES-12 需真实数据；RES-10/11 只有文案；CON-03 真并行待验证；live 检索需用户提供接口。

## 8. 服务

无排队/运行任务，停 PID 38108，重启后新 PID **44836**，8767/8768 监听，health 正常；旧 STRK 报告接口读取正常，新脚本已生效。

## 9. 文件与 SHA-256

| 文件 | SHA-256 |
|---|---|
| research_review.py | bdc4a1b99d1553eec4b3b52ef46b3054858de3bbfa6d2914cee49593e2ab4bb4 |
| ai_research.py | f947c46115a1fdd26f5d080c79976b473af663a67a60730bf45d0d92704c15d5 |
| server.py | 3d7bbdb875923efa943bf6a0d77d4859a9b99e8ba2a83e7fbb9e4d6d678de1a0 |
| design/reports-runtime.js | 73884bc62cb3ad9c9b2e6ce5048b485a8d5e57ac6a55af553405bf4e0b3e0a21 |
| test_r6_review.py | ccca72ab300bc865d5bca1951662d896ee61d8dd635485fda4595a0668c677fa |

备份：`reserve-history/r6-res16-rev07-rev09-res14-20261014T2155/`（改前 ai_research.py、server.py、reports-runtime.js）。