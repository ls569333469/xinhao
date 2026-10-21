# V2.8 RULE-01 加深刀 · 市场只读指纹 + 报告导出 fingerprint

## 范围

闭合 RULE-01 剩余两环：

1. **市场页 UI** 强制只读消费 `GET /api/strategies/bindings/market/filter`
2. **报告导出/保存** 强制写入策略 fingerprint；未绑定则拒绝

## 行为

| 动作 | 硬闸 |
|---|---|
| 市场页加载 | 拉取 filter；展示只读 `strategy_id@version` + hash；锁定 unified_key |
| 绑定变更 | 重新锁定；提示指纹变更（防静默漂移） |
| 下载 Markdown / 导出 CSV | 须 bound；正文/CSV 头写入 fingerprint |
| `POST /api/reports/local` | 服务端 `stamp_export_payload`；未绑定 → 409 |

优先页面：`reports` → 回退 `market`。

## API

- `GET /api/strategies/bindings/market/filter`（既有）
- `GET /api/strategies/bindings/export-fingerprint`（新增）
- `POST /api/reports/local` 附带 `strategy_fingerprint` / `unified_key`

## 文件

- `rule_export_gate.py`、`design/rule-market-runtime.js`、`test_rule_export_fingerprint.py`
- 改 `rule_bindings.py`（可选 re-export）、`server.py`、`ai_research.py`、`index.html`、`prototype.html`、`reports-runtime.js`

## 验收

- [x] 未绑定导出/保存 → 拒绝
- [x] 绑定后导出带 unified_key / content_hash
- [x] 市场横幅只读展示指纹
- [ ] 观察层六阈值与冻结规则结构化参数自动对齐（策略规则仍为文本，未做数值镜像）

## 不做宣称

不宣称六历史指标 / ATH / OI·市值·账户历史齐全。
