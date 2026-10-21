# 投研迭代 R8：开头截止时间 + CON-08 空失败重试 取证

- 时间：2026-10-14 约 22:45（Asia/Shanghai）
- **付费调用 2 次**（ARB、OP 各一次）；另有一次空失败重试任务，**0 次**供应商调用。未发明供应商失败。
- **无用量工作**。**不**标记 A 档 01/02/03 完成。

## 1. 开头「当前价格」修正

根因：`design/prompt-templates.json` 标准档 instruction 原先写死「开头：只留标题 + 当前价格：$X | 24h ±X%」，与 REV-07 冲突。

改动：
- 标准档 instruction：禁止无截止的「当前价格/当前价/现价」；要求「快照价格（截至 …）：$X | 24h ±X%」或同义（同一行含截至/快照/可得于）。
- common：补充同一规则。
- notes：增加 REV-07-opening / R8 说明。
- 离线测试：`test_r8_opening_prompt.py`（指令文案）；`test_r6_review` 开头样例（无截止失败 / 有截止通过）。15 过。

## 2. 真实新报告（验证开头）

| report_id | 币 | 开头 | RES-14 opening | schema 八节 |
|---|---|---|---|---|
| report-d1369e17b9ef4b2cb30fbb44fed405f8 | ARBUSDT | 快照价格（截至 2026-10-13 11:58 UTC）：$0.18274，24h -5.536% | passed | success 8/8 |
| report-7290424a52a74da89e030974797a50c6 | OPUSDT | 快照价格（截至 2026-10-13 11:58:46 UTC）：$0.13585，24h -2.512% | passed | success 8/8 |

两份均有 data_cutoffs、checks 三块、fixture 检索、vendor_response.json。批次 batch-831cfbf95a124e58ae0c6bfe85efaacd / task-1db22fb2… / sub-53f0f0de…。

## 3. CON-08

安排：临时 `WORKBENCH_RESEARCH_BATCH_SIZE=1`、`WORKBENCH_RESEARCH_TIMEOUT_SEC=480`、`CAP=600`；2 币 → 两组各 1 币。

结果：**两组都成功**（ARB ~2.2min，OP ~3.5min），没有自然的部分失败。未伪造供应商失败，也未改写成功组状态（改写会不诚实且会再触发付费重跑）。

空失败重试（诚实路径）：
- `POST .../retry-failed` → task-16178a1f… success
- **retried_groups=0**；retry_rounds 仍为 0；两组 runs 仍为 1
- 两份 report.json 字节哈希不变

「有成功组 + 只重跑失败组」的路径：离线 `test_r5_con_batch` 已覆盖；本轮真实未再出现失败组，故未再付费制造失败。

## 4. 配置恢复

- 已清除临时环境变量并重启服务；默认 timeout cap=300、batch_size=5。
- `ai_research.py` 保留可选环境覆盖（未设则行为与恢复前一致：max(timeoutSec,180) 且 cap 300）。
- 当前 PID **34964**，health 正常。

## 5. 文件 SHA-256

| 文件 | SHA-256 |
|---|---|
| design/prompt-templates.json | 5591fb30beb014f34baaa56158dfbbdc7cc2f97da86609ed162c00f54726dbfe |
| research_review.py | 0003ba4bbd190f1406c2ce720f7202809b09bbf798c16c5df16520e9b650e9f1 |
| ai_research.py | dec1e131ca43280cf9097118e3073085e58274ed36d2ef01b1f78bb70517ab0e |
| test_r6_review.py | 15103099507e67ad390c285b9c17fbae57490a29a48534722600b1afc88b5714 |
| test_r8_opening_prompt.py | 47a26dca3987c43aa16dbfe7bd8219df9890b970ca663c2681451393ebc43ab2 |
| runtime/reports/report-d1369e17b9ef4b2cb30fbb44fed405f8/report.json | ddabeeadc32b3d70ce7b5835fb897931a0a909beacc730c673fde94678c52d63 |
| runtime/reports/report-7290424a52a74da89e030974797a50c6/report.json | dc2f5444e500f82f3c649ed7d43b1d8dbeac4541264b924704c76120680fe035 |

备份：`reserve-history/r8-opening-con08-20261014T2230/`。
