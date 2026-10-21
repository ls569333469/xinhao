# iteration-v2.8 · RULE-01 导出指纹联调加深

- **时间**：2026-10-13 23:20 Asia/Shanghai
- **实现ID**：RULE-01（加深：export fingerprint 联调 / rule-export-fingerprint-v2）
- **状态**：部分实现（**不标 RULE/DATA/STRUCT/BACKTEST/CONFIG/EXT 完成**）
- **对齐**：DATA-01 `coverage_gate` expected/received + `coverage_fingerprint`

## 行为

| 路径 | 硬闸 |
|---|---|
| freeze | 写入 `rule_fingerprint`（strategy hash + coverage FP + joint_hash） |
| bind / bind-all | 落盘 `coverage_fingerprint` / `joint_hash` / `rule_fingerprint` |
| export_version | 包带 `rule-export-fingerprint-v2` 联合指纹 |
| import_version | 缺 coverage FP / hash 不匹配 → BindingConflict→409 |
| enable | 冻结缺指纹 / 覆盖漂移 / expected 不匹配 → 409；`acknowledge_coverage_drift=true` 可显式越过覆盖漂移 |
| 报告 stamp / export-fingerprint GET | 带 coverage expected/received + joint |

## API

- `POST /api/strategies/versions/ver-*/export`
- `POST /api/strategies/import`
- `POST /api/strategies/versions/ver-*/enable` 接受指纹核验字段 + `acknowledge_coverage_drift`
- `GET /api/strategies/bindings/export-fingerprint`（root 联调 coverage）

## 文件

- `rule_export_gate.py`、`strategy_store.py`、`rule_bindings.py`、`server.py`
- `test_rule_export_fingerprint.py`（18）
- `DOCS/design/iteration-v2.8-rule01-export-fingerprint-deepen.md`

## 不做

- 不标 RULE/DATA/STRUCT/BACKTEST/CONFIG/EXT 完成
- 不宣称六指标/ATH/OI·市值·账户历史齐全
- 不用「二手日历」

## 验证

- `unittest test_rule_export_fingerprint`（18）通过

## 备份

- `runtime/code-backups/rule-export-fingerprint-20261013-231614/`

## 下一刀建议

- 页面点验 §11.8 复验收益，或另一加深刀（SIM/EVAL 联动 / 观察层规则镜像）
