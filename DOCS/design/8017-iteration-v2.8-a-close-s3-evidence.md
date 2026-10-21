# V2.8 A 档阶段 S3 证据：03 指标与策略本地服务链路 + 失败矩阵

时间：2026-10-14 15:12（北京时间 / Asia/Shanghai）  
执行：GROK 执行助手（S3）；未启动真实 AI、未打外部付费模型、未宣称 03 或 A 档全部完成。  
依据：`DOCS/design/V2.8-A档迭代需求-审计修复与01-02-03收口.md` §6 A-03-01～07、§7 S3；`iteration-v2.8-a-close-s2-evidence.md`（S2 不重复宣称完成）；`iteration-v2.8-frontend-v1b2-strategy-export.md`。  
**本文件不是完工证明。**

## 受验版本指纹（SHA256 前 16）

| 文件 | mtime (北京) | hash 前缀 |
|---|---|---|
| `design/strategy-runtime.js` | 2026-10-14 13:35:30 | `5F2A4FEDD8CBF4C5` |
| `strategy_store.py` | 2026-10-13 23:28:40 | `8F859C7FCBAA0CE8` |
| `rule_bindings.py` | 2026-10-13 23:28:40 | `9C6443389B0012F2` |
| `rule_export_gate.py` | 2026-10-13 23:28:40 | `EA0287656EB2B712` |
| `rule_coverage_gate.py` | 2026-10-13 23:31:38 | `E76F3FDEFD43B446` |
| `struct_indicators.py` | 2026-10-13 23:52:47 | `519531F4FF65A2C7` |
| `frozen_exec.py` | 2026-10-13 23:43:07 | `876F5EC7B47A529E` |
| `config_store.py` | 2026-10-13 23:47:55 | `2411DA1409DC75B3` |

运行服务：`127.0.0.1:8767`（health `version=2.8`，`scan.enabled=false`，`strategies.strategy_export_import=true`，`struct.ath_policy=区间高点（无全历史核验源）`）。  
写操作同源闸：`server.py` ~548–551 — 非本机页面同源 POST 返回 **403「写操作要求本机页面同源请求」**；故 **导出/导入失败矩阵在 tempfile 隔离 StrategyStore 执行**，线上 8767 以 **GET 只读**为主。测完已 `shutil.rmtree` 清理隔离目录，**未向生产 strategies 写入新版本**。

## 离线套件摘要（本轮）

| 套件 | 结果 | 备注 |
|---|---|---|
| `test_rule_export_fingerprint` | 20/20 OK | 含 P1 篡改 rules 保留 hash→409、缺/陈旧 coverage_fp、诚实导入 |
| `test_struct_indicators` | 全过（含 Lookahead/Gate） | ATH 禁止、预热不足、区间高点≠ATH |
| `test_frozen_exec` | 全过 | 冻结参数不可草案覆盖 |
| `test_rule_coverage_gate` | 全过 | ATH/OI partial 拒绝 |
| `test_strategy_store` + `test_rule_bindings` | **7 ERROR** | 夹具未写 `scan.json` 覆盖 → freeze 硬闸；**非生产阻断**（见残留） |
| 隔离矩阵脚本 `s3_verify.py` | A-03-01～07 均记录通过 | tempfile；产物 `failure-matrix.json` / `honest-export.json` |
| 线上 GET | registry/bindings/export-fingerprint/struct/catalog/coverage-gate/map | 200 |
| 线上 POST export（脚本） | 403 同源闸 | 预期；不绕过 |

合计通过单测（不含夹具 ERROR 套件）：**58 OK**（export_fp+struct+frozen+coverage）。

---

## A-03-01 指标公式 / 周期 / 未知；非可信 ATH；未齐不伪数

- **验证步骤/输入**：① `GET /api/struct/catalog`；② `struct_catalog()` 离线；③ `assert_deps_covered(..., ["ath"])`；④ `compute_from_bars` 短序列（5 根）。⑤ `test_struct_indicators`（`test_range_high_never_ath`、`test_atr_insufficient_history`、`test_gate_ath_forbidden` 等）。
- **预期**：公式/周期/预热明确；`full_history_verified_source=false`；ATH 依赖拒绝；预热不足不给伪数。
- **实际**：catalog `params_version=struct-params-v1`，`lookahead_guard=true`，`formulas=5`，`full_history_verified_source=False`；ath 硬闸拒绝；短 bars 返回 metrics/insufficient 路径（非伪造完整值）。单测全过。
- **证据**：health.struct；catalog API；unittest；隔离脚本 A-03-01。
- **状态**：**代码落地 + 离线通过 + 运行只读通过**；用户页面点验 **用户待验**。
- **残留**：无阻断。不宣称可信 ATH / 全历史宇宙。

## A-03-02 草案保存 / 读取；来源可辨；不直接启用执行

- **验证步骤/输入**：隔离 `StrategyStore.create_draft` → `get_draft` → `update_draft` → `list_all`；`create_draft_from_report`；`executable=False`。
- **预期**：刷新可读；建议/报告摘录不直接 executable。
- **实际**：`draft-4c655f3cecd6` 标题更新为 `S3-draft-updated`；`executable=False`；list 可见；from_report 成功。
- **证据**：隔离脚本；线上 `GET /api/strategies` 可见既有 draft 结构（只读）。
- **状态**：**离线通过**；UI 硬刷 **用户待验**。
- **残留**：无。

## A-03-03 冻结不可原位改；改规则出新版本；ID/version/hash

- **验证步骤/输入**：freeze `1.0.0` → 改 draft `tp_pct` → freeze `1.1.0` → 回读 v1 hash；`create_draft_from_version` fork。
- **预期**：v1 不变；v2 新 id/hash；方向/触发/退出/依赖保留在 rules。
- **实际**：v1=`ver-72ec077cd1b4` hash=`1799da2c140c…` 不变；v2=`ver-cbe5aa8f84c8` 新 hash；`rule_fingerprint` 携带；fork 新草案挂 `parent_version_id`。
- **证据**：隔离脚本；`strategy_store.freeze` 注释「Never mutates an existing frozen file」。
- **状态**：**离线通过**。
- **残留**：用户 UI「冻结/新版本」点验待做。

## A-03-04 跨页绑定同一冻结版本；陈旧拒绝；启用≠自动下单

- **验证步骤/输入**：① 隔离 `bind_all` / 五面 `bind_surface`；② `validate_ref` 错 hash；③ `set_enabled`；④ `bindings.catalog().disclaimer`；⑤ 线上 `GET /api/strategies/bindings`（market 已绑 `S-smoke@0.0.1`）；⑥ `strategy-runtime.js` 文案「已启用（仅可引用，无自动下单）」。
- **预期**：五面同一 unified_key；错指纹拒绝；启用不宣称实盘下单。
- **实际**：`surfaces_bound=5`，`unique_keys=1`，`mismatch_rejected=True`，`enable_ok=True`；disclaimer「仅引用，不自动下单」。线上 bindings 与 export-fingerprint 一致指向 `ver-7a0f59f2da35`。
- **证据**：隔离脚本；GET bindings/catalog/export-fingerprint；runtime.js:210–230、278、416。
- **状态**：**代码落地 + 离线通过 + 运行只读**；跨页 UI **用户待验**。
- **残留**：观察层六条文镜像后置（见 A-03-07）。

## A-03-05 导出=当前冻结；篡改/缺字段拒绝；失败不留半成品

- **验证步骤/输入**（隔离 tempfile）：export → ① 改 rules 保留旧 `content_hash`；② 删 `coverage_fingerprint`；③ 陈旧 coverage_fp；④ 诚实包导入。每次后比对 version_id 集合。另：`test_rule_export_fingerprint` P1/缺/陈旧/诚实用例。
- **预期**：拒绝且 version 集合不变；诚实可导入新冻结版。
- **实际**：
  - 篡改：`导入拒绝：规则内容与声明 content_hash 不一致…禁止篡改 rules 后沿用旧指纹（RULE-IMPORT-HASH）`；`no_half=true`
  - 缺字段：`导出包缺少 coverage_fingerprint…`；`no_half=true`
  - 陈旧：`coverage_fingerprint 已过期或不匹配…`；`no_half=true`
  - 诚实：导入 `ver-371bd171e14a`（仅存在于 tempfile，随后清理）
- **证据**：`failure-matrix.json`；unittest 20/20；线上脚本 POST export **403 同源**（未写入）。
- **状态**：**离线通过**；浏览器同源导出/导入 **用户待验**。
- **残留**：无生产半成品（隔离已清理；生产 version 数测前测后均为 6）。

## A-03-06 导出无密钥；普通策略包 ≠ CONFIG 整站备份

- **验证步骤/输入**：扫描诚实导出 JSON 无 `sk-` / apiKey 值 / password；对照 `ConfigStore.backup_export`（sections 向导、显式拒绝凭证）。策略包键：`schema/version/rule_fingerprint/coverage_fingerprint/content_hash/…`。
- **预期**：无密钥；入口与 schema 分离。
- **实际**：`secret_hits=[]`；策略包无 credentials 段；CONFIG 有 `backup_export/backup_import/backup_manifest` 与 `sections`，且 `credentials_included` 强制 False。
- **证据**：隔离导出；`config_store.py` backup_*；health `config.slice=CONFIG-01-settings-bind`。
- **状态**：**离线通过**。普通策略导入通过 **不能** 替代 CONFIG 备份恢复验收（本轮未做 CONFIG 恢复，明确后置/B 档相关）。
- **残留**：CONFIG 整站备份恢复仍属 B/后置。

## A-03-07 跨页操作有证据；后置不扩写

- **验证步骤/输入**：隔离 bind market+reports → 新 `RuleBindings` 实例读回同一 `version_id`；静态核对 `strategy-runtime.js` 导出/导入/指纹/冻结/启用文案；明确后置范围。
- **预期**：跨页读写有证据；不把 SIM/EVAL/REVIEW/观察层六条标完成。
- **实际**：`persist_same_version=True`；runtime 含 export/import/export-fingerprint/freeze；启用文案「无自动下单」。**后置**：观察层六条文镜像、SIM/EVAL/REVIEW 完整闭环、自动实盘下单 — **不扩写为 RULE-01 全部完成**。
- **证据**：隔离脚本；runtime 行号见上；本文件声明。
- **状态**：**代码落地 + 离线通过**；用户跨页点验 **用户待验**。
- **残留**：后置项按范围保留。

---

## 线上只读快照（8767）

| 端点 | 结果 |
|---|---|
| `/api/health` | version=2.8；strategies.export_fingerprint_v2=true |
| `/api/strategies/registry` | T1–T8 / I1–I6；hash `45ee1dfc…` |
| `/api/strategies/bindings` | market 等已绑 `S-smoke` / `ver-7a0f59f2da35` |
| `/api/strategies/bindings/export-fingerprint` | bound=true；unified_key 一致 |
| `/api/struct/catalog` | struct-params-v1；full_history_verified_source=false |
| `/api/strategies/coverage-gate/map` | ath/netflow ∈ unsupported |
| `POST …/export`（非页面同源） | **403** 同源闸（预期） |

生产 `versions` 计数：测前 **6** / 测后 **6**（无半成品残留）。

---

## 阻断 A 的新 bug？

**未发现需改业务逻辑的生产硬 bug。** 本轮 **未改代码**。

| 项 | 位置 | 现象 | 结论 |
|---|---|---|---|
| `test_strategy_store` freeze 相关 2 ERROR；`test_rule_bindings` setUp 5 ERROR | `test_strategy_store.py` ~55/97；`test_rule_bindings.py` ~31；`strategy_store.py:391` | 夹具未准备 covered 的 `scan.json`（缺 `price_4h`/`snapshot` 等）→ 覆盖硬闸拒绝 freeze | **硬闸正确**；夹具落后于 RULE-01 加深。对照 `test_rule_export_fingerprint.setUp` 已写 scan.json。建议后续只更新测试夹具，**非 A 链路生产阻断**。 |

---

## 总表

| ID | 状态 |
|---|---|
| A-03-01 | 离线+只读通过 / UI 用户待验 |
| A-03-02 | 离线通过 / UI 用户待验 |
| A-03-03 | 离线通过 / UI 用户待验 |
| A-03-04 | 离线+只读通过 / 跨页 UI 用户待验 |
| A-03-05 | 离线失败矩阵通过 / 浏览器同源导出导入用户待验 |
| A-03-06 | 离线通过（策略≠CONFIG；CONFIG 恢复未验） |
| A-03-07 | 代码+离线通过；后置不扩写 / 用户点验待验 |

## 未做 / 明确跳过

- 真实 AI / 外部付费
- 浏览器同源 UI 硬刷点验（导出下载、导入面板）
- SIM/EVAL/REVIEW 完整闭环、观察层六条文、自动实盘下单
- CONFIG 整站备份恢复验收
- 修改 Codex 收口文档正文；不宣称 03 或 A 档全部完成
- 未向用户发消息

## 附属产物（本机临时，可删）

- 隔离脚本输出：`%USERPROFILE%\s3-docs\s3-matrix-results.json`、`failure-matrix.json`、`honest-export.json`
- 验证脚本：`%USERPROFILE%\s3-docs\s3_verify.py`（不入库）