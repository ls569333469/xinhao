# V2.8 V1 API smoke · 扫描→报告→冻结→USDT纸面→定期结算复盘

日期：2026-10-13 23:58 Asia/Shanghai。状态：**部分点验通过**（不宣称模块完成；不做 ATH/宇宙/多市场；无「二手日历」；不改 Codex 前端 HTML）。

## 范围

对 live `8767` / `8768`（同机共享 `D:\Axiangmu\异动观察工作台`）做 V1 主链路 API smoke：

```
Health 2.8 → scan snapshot → coverage gate → draft→freeze(frozen_exec)→enable
→ autofill preview（含发散拒绝）→ start observation → settle（F02 / 规则终态 / 断档不假赢）
→ config backup manifest+payload_hash / 篡改 409 → frozen_exec 发散 TP 409
```

- **不花真实 AI 钱**：只用已有 `GET /api/scan` 快照；无 `scan_and_research` / 付费叙述。
- 隔离 ID：`strategy_id` / `version` / `observation_id` 带端口后缀，避免双端口共享 store 碰撞。
- 写接口须带 `Host` + `Origin: http://127.0.0.1:{port}`（`write_api` 同源闸）；缺 Origin → 403。

脚本：`C:\Users\ls569\yidong_stage\v1_api_smoke.py`  
结果：`C:\Users\ls569\yidong_stage\v1_api_smoke_result.json`（stamp=`20261013235745`）

## 通过矩阵（34/34）

| 步骤 | 8767 | 8768 | 口径 |
|---|---|---|---|
| 1_health_2.8 | PASS | PASS | `version==2.8` |
| 2_scan_snapshot | PASS | PASS | rows=588；既有 scan_id（无新扫） |
| 3a_coverage_catalog | PASS | PASS | `coverage-gate-catalog-v1` |
| 3b_coverage_state | PASS | PASS | slice/scan/candles/fields/aggregate |
| 4a_create_draft | PASS | PASS | draft-… |
| 4b_freeze_frozen_exec_params | PASS | PASS | ver-… 含 tp/sl/hold/notional/entry_rule |
| 4c_enable_version | PASS | PASS | enabled=True |
| 5a_autofill_preview | PASS | PASS | dry_run notional=25 tier=T1 |
| 5b_autofill_divergent_or_gate_409 | PASS | PASS | notional 999≠冻结 25 → 409 |
| 6a_start_observation | PASS | PASS | obs-… |
| 6b_settle_F02_early_no_fake | PASS | PASS | settlement=None；pending_backfill（未到期不假结算） |
| 6c_settle_rules_terminal | PASS | PASS | 连续窗触 TP → take_profit/win |
| 6d_settle_no_gap_win | PASS | PASS | 断档后「像 TP」→ unverifiable / gap_before_hit，**不计胜** |
| 7a_backup_manifest_hashes | PASS | PASS | integrity=manifest+payload_hash |
| 7b_backup_export | PASS | PASS | workbench-backup-v1 |
| 7c_backup_tamper_refuse_409 | PASS | PASS | 改 config_core → payload_hash 不一致 409 |
| 8_frozen_exec_divergent_tp_409 | PASS | PASS | tp_pct 0.99≠冻结 0.1 → 409 |

**汇总**：pass=34 fail=0 total=34。

## 首轮失败（已归因 · 非产品阻塞）

| 现象 | 根因 | 处理 |
|---|---|---|
| POST 403「写操作要求本机页面同源请求」 | smoke 缺 `Origin`/`Host` | 脚本补同源头后复跑绿 |
| 8768 freeze 400 | 双端口共享 store，同 `strategy_id`+version 标签重复 | 端口后缀 ID |
| 8767 6c start_obs2 409 | smoke 用 `max_hold_days=3` 与冻结 7 发散 → frozen_exec 正确拒 | smoke 改为服从冻结 7 |

**本刀无产品代码修复**（行为符合设计）；无 `runtime/code-backups` 新包。

## 仍缺 / 不宣称

- 未标 SIM / EVAL / RULE / CONFIG / DATA 等模块「已完成」
- 页面点验仍待 Codex HTML（本刀不改设计 HTML）
- 全自动撮合、全市场自动观察、付费 AI 叙述、ATH/宇宙/多市场：**不在本刀**
- EXT 本机 auth bypass 若仍开：下一刀候选

## 下一刀建议

1. **EXT auth bypass**（若仍开）收口；或  
2. **等 Codex HTML** 后做 V1 页面点验（冻结→autofill/settle / 备份向导），不改设计 HTML。
