# V2.8 AI 报告指纹 / 证据 / schema·缓存 贯通检查

日期：2026-10-14 00:10 Asia/Shanghai。结论：**已由既有 F09 + 报告指纹硬闸覆盖，本刀核实通过，无新增产品修复**（不做 busywork）。

**不做**：不标 AI/RULE/DATA 完成；不宣称真实 schema/retrieval 能力已验收；不宣称六项证据齐全；不做 ATH/宇宙/多市场；不改 Codex HTML；无「二手日历」。

## 贯通结论（扫描→报告）

| 契约 | 现状 | 证据 |
|---|---|---|
| 证据字段（F09） | 只用真实扫描字段；禁虚构 fundingRate/changePct；`six_evidence_complete=false` | `ai_research._slim_evidence_rows` / `EVIDENCE_ROW_FIELDS`；`test_f09_evidence_uses_real_fields_not_fiction` |
| evidence_hash | prepare 由 slim rows 重算；入 cache_material | `test_f09_cache_identity_includes_baseurl_evidence_prompt` |
| 缓存身份 | `request_hash` ← baseUrl + config_version + evidence_hash + prompt_hash（编辑后提示词+schema，不仅 version 标签） | 同上 + `test_f09_edited_prompt_changes_cache_key` |
| run 配置绑定 | prepare 冻结 model/protocol/baseUrl/configId；与当前不一致且未 force → ResearchError | `test_f09_run_rejects_mismatched_current_config` |
| schema 校验 | 可解析 JSON 对照模板；markdown/不可解析 → `schema_validation.status=unverified`，不升格 capability success | `test_f09_schema_validation_unverified_on_markdown` / `_success_on_structured_json` |
| retrieval | 报告与返回固定 `unverified`；鉴权/模型成功不升格 | run() claims / retrieval 块 |
| 报告策略指纹 | `POST /api/reports` 与 `/api/reports/local` 均 `stamp_export_payload`；未绑定 → BindingConflict **409** | `server.py`；`test_reports_fingerprint_gate` |
| scan_and_research | 扫描失败/覆盖闸失败 **不**回落旧快照 | `task_service` scan_and_research 分支 |

## 本刀核验

```
unittest test_ai_research test_reports_fingerprint_gate
→ 15 OK（含 7× F09 + 2× fingerprint gate）
```

未发现「未核验证据仍可落盘」或「付费/本地报告路径缺 fingerprint」的可利用缺口；故 **不改代码**。

## 仍明确未验收（文档口径）

- 供应商侧 schema / retrieval **capabilities** 仍多为 unverified（预期）
- 真实全市场 + 计费须页面点验（等 Codex HTML）
- 字段级报告 schema 未做全字段穷尽校验

## 下一刀

**Idle：等待 Codex HTML** 后做 V1 页面点验（扫描→报告→冻结→纸面→结算），不改设计 HTML。
