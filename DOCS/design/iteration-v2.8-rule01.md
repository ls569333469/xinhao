# V2.8 RULE-01 第一刀 · 策略草案与冻结版本

日期：2026-10-13。状态：**部分实现**（非 RULE-01 全验收）。

## 交付

- `strategy_store.py`：草案可改；冻结生成不可变版本（content hash）；改冻结规则须 fork 新草案再冻新版；白名单触发/退出结构落库；启用前软闸门检查声明依赖。
- API：`GET /api/strategies`、`GET /api/strategies/registry`、草案 CRUD、`POST .../freeze`、`POST .../fork`、`POST .../enable`（缺依赖返回 409 + `missing_dependencies`）。
- UI：指标与策略 ·「已存草案与版本」；编辑器可保存/冻结；中文文案；注明 SIM/EVAL 未接线。
- 测试：`test_strategy_store.py`（离线，不打币安/付费 AI）。
- 沿用共用 `strategy-registry.json` / StrategyRegistry 的 version+hash 引用。

## 仍缺

- 市场/报告/篮子/实验页统一按 ID/version/hash 筛选与引用（本刀只提供存储与列表）。
- 逐字段覆盖矩阵与完整预热闸门（DATA-01 仍部分实现；声明 oi/ema 等会拒绝启用）。
- SIM-01 模拟撮合账本、EVAL-01 前向成绩、自动按白名单触发/退出执行。

备份目录：`runtime/code-backups/rule01-*`。


## 加深（跨页统一引用）

2026-10-13 21:34 CST：见 [iteration-v2.8-rule01-bindings.md](iteration-v2.8-rule01-bindings.md)。市场/报告/篮子/实验/验证可绑定同一冻结版本指纹；错 hash 拒绝。


## 加深（覆盖硬闸）

2026-10-13 21:45 CST：见 [iteration-v2.8-rule01-coverage-gate.md](iteration-v2.8-rule01-coverage-gate.md)。
