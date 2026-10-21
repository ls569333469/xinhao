# P2 证据 — UI05–UI08 + SCREEN + 排序标清窗口（v4.2）

- 时间：2026-10-16 01:44:22 Asia/Shanghai
- 备份：`runtime/code-backups/ema-p2-v42-20261016-014000/`
- 约束：未宣称第5节全量验收 / A档完成；server 由用户批准后重启（本切片 FE/docs 收口）

## 已完成

| 项 | 结果 | 位置 |
|---|---|---|
| **SCREEN** | ④列关键阶段/确认用 `.ema-stage-critical` / `.coin-sub-critical` 取消 ellipsis；左侧标签可换行；页签标题允许 wrap | `design/a-shell.css`；`design/pro-table-runtime.js` ema 单元格 |
| **UI07** | 去掉 executable/sim_engine 工程横幅；改为「未启用原因」+ 缺依赖简述 | `design/strategy-runtime.js` |
| **UI06** | 「近7日新低」→最新10根4H≥3次严格新低；刚形成=EMA25首次上穿99且完整排列；量均值排除本根、仅下一根确认 | `index.html` ④说明；`design/prototype.html` 指标指南；`design/market-evidence.js` 交叉文案；`design/strategy-registry.js` T1 触发澄清 |
| **候选人数分开** | 策略卡上方人数口径：①②④组合 / ①②宽候选 / 旧①②③ / 持久池活跃；注明范围与时点 | `design/prototype.html` `renderStrategyTemplates` |
| **状态缺失未知** | 生命周期缺字段仍「未记录」（既有 `emaLifecycleLabel`）；不写候选周期none | `pro-table-runtime.js` / `market-evidence.js` |
| **UI05（部分）** | CSV 增补 fullCross、barVolumeRatio7、newLowCount10、amplitude*、入池时点/到期/OI、pendingCross、crossConfirm、exit*、rules_version、`csvSchema=ema-evidence-csv-v4.2`；嵌套 confirm JSON 序列化；index 内嵌 MarketEvidence 同步 keys | `design/market-evidence.js`；`index.html` |
| **UI08（部分）** | 市场页头 `#scanMetaLine`：扫描ID / revision / 采样时间 / 规则注册表版本；旧快照标记 | `design/prototype.html` |
| **排序标清窗口** | 排序项与表头统一：前7根点火量倍数、前20根单根量倍数、观察窗量倍数(24h对齐窗)；未知置后（原有 comparator） | `pro-table-runtime.js` |

## 离线静态测

```
python -m unittest test_ema_p2_ui_copy -v
Ran 6 tests OK
```

## 点击核验（重启后硬刷）

1. 市场表 ④列：阶段/确认两行完整可见，不被省略号裁切。
2. 策略页模板卡：顶部四人数据口径分开；卡上未启用只写缺依赖。
3. 页头扫描行含真实 scan_id/revision/采样。
4. 导出 CSV 含新列与 `csvSchema`。
5. 排序下拉里量字段名称与列头一致。

## 推迟 / 未做全

| 项 | 原因 |
|---|---|
| UI05 主表/详情/字段档案/数据报告**全链路**同字段齐套 | 优先 SCREEN+文案+CSV；详情大块与字段档案展开未逐页改完 |
| UI08 草稿版/冻结版在页头三分栏常驻 | 仅市场页头+策略 eyebrow「规则 version」；策略页草稿/冻结分栏沿用既有，未再造第三套抬头 |
| 引擎侧「刚形成」计算改用首次上穿字段 | FE 文案已改；若行数据仍无 `fullCross`/`emaFirstCross25over99` 则显示「未知(需首次上穿字段)」——算力字段补齐属后续 |
| 第5节全量验收 / A档 | 按约束明确不宣称 |

## 语法

- `node --check`：pro-table / market-evidence / strategy-runtime → 通过
