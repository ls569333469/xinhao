# V2.8 DATA-01 第一刀 · 只读价格索引

日期：2026-10-13。状态：**部分实现**（非 DATA-01 全验收）。

## 交付

- `price_index.py`：合并扫描窗口与区间补查；哈希校验、冲突排除、缺口与连续性；快照 revision 列表。
- API：`GET /api/price-index`、`GET /api/price-index/series`、`GET /api/snapshots`；预检改为含扫描窗口。
- UI：数据与任务 · 区间行情补查页增加价格索引与快照表；文案区分「当前可得」与「历史当时可得」。
- 测试：`test_price_index.py`（离线，不打币安）。

## 仍缺

- 逐字段（OI/市值/账户比等）覆盖矩阵与策略声明预热闸门。
- 全生命周期/可信 ATH / 六历史指标。
- 持仓自动补查与 RULE-01。

备份目录：`runtime/code-backups/data01-*`。


## 加深（多字段覆盖闸门）

2026-10-13 21:40 CST：见 [iteration-v2.8-data01-coverage.md](iteration-v2.8-data01-coverage.md)。
