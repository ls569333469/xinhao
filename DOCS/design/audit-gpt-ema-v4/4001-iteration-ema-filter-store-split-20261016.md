# iteration：④筛选与点火级别拆开 + 扫完写池

落地时间：2026-10-16 12:44（北京时间）

本轮只做两件事，**未标 A 档完成**。

## 备份
`runtime/code-backups/ema-filter-store-split-20261016-124201/`

## 改了什么

| 文件 | 改动 |
|---|---|
| `design/pro-table-runtime.js` | 普通六指标视图：勾选弱/中/强时自动从 AND/OR/N 里去掉④（index 3）；更新说明文案 |
| `index.html` | `combineStatus` 同步：gradesOn 且 six 视图时 `indices.filter(i=>i!==3)` |
| `ema_squeeze_store.py` | 新增 `persist()`：无人入池也写出 `runtime/ema-squeeze-candidates.json` |
| `scanner.py` | 扫完调用 `persist()`，结果带 `emaSqueezeStore` |
| `test_ema_filter_grade_split.py` | 离线验收 3 项 |

## 根因（对照上一扫 revision-0001）

- 弱48 / 中5 / 强0 全部 `belowAllEma=false`，④=true 与级别互斥 → 以前勾④+弱/中/强必然空。
- 入池条件 ①②+横盘+三线下方：本轮 eligible=0（core12∩sideways=0），所以以前从不 `_save`，池文件不存在。
- 现已手工 `persist()` 落了空池文件；下次正式扫描会自动再写。

## 离线验收

`
python -m unittest test_ema_filter_grade_split.py -v
# 3 ok
node --check design/pro-table-runtime.js
`

## 你怎么点验

1. 硬刷新 8767 / 8768。
2. 普通六指标：只勾④ → 仍筛三线下方；只勾弱/中/强 → 应出现对应币，**不必再勾④**；④+弱 也不再被互斥清空。
3. 持续观察池：当前活跃=0 属实（上轮无人满足入池），不是筛选写坏。

## 未做

- 未改入池规则本身（仍要 ①②+横盘+下方）。
- 未自动重扫；未做 UI05+。