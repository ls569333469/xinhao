# 迭代证据：I4 前端对齐（蓄力 ≠ 入场 + I4b 可见）

日期：2026-10-15（Asia/Shanghai）  
范围：用户硬刷新后仍见旧「低于三线」——根因在 **8768 原型工作台** 的 `pro-table-runtime.js`，不是 `scan.json` 缺字段。  
**未标记 A 档完成。**

## 1. scan.json 字段核验

最新 `scan.json`（588 行）行内已有：

| 字段 | 存在 | 样例分布（本快照） |
|---|---|---|
| `ignitionGrade` | 是 | none 568 / weak 16 / medium 2 / strong 1 / null-ish 1 |
| `barVolumeRatio7` | 是 | 数值 |
| `rsi4h` | 是 | 数值 |
| `sidewaysOk` | 是 | bool |

结论：数据侧已就绪；用户看到的旧 UI 是前端渲染/筛选未对齐。

## 2. 服务面与「看起来旧」的原因

| 端口 | 页面 | ④ 列渲染 | I4b 勾选 |
|---|---|---|---|
| **8767** | `index.html`（正式简页） | 早已有「蓄力中 / 强🟢… / 横盘 / 前7根量 / RSI」 | 页面内静态 `#ignitionFilters` |
| **8768** | `design/prototype.html` + `pro-table-runtime.js`（V2.8 工作台） | **此前**硬编码「低于三线」+ 4h收盘 | **此前**未注入，用户看不到 |

用户截图特征（「低于三线」+ 4h 收盘）与 `pro-table-runtime.js` 旧 `metric('ema')` 完全一致 → 实际在看 **8768**。  
Filter ④ 文案已更新，是因为指标标题/公式来自 `StrategyRegistry`（已改），与列单元格渲染脱节。

`Cache-Control: no-store`；JS 变更后硬刷新即可，**无需为本次 JS 改动重启 PID 40900**（未改 Python 服务逻辑）。

## 3. 仍旧处 → 已修

| 表面 | 旧表现 | 修复 |
|---|---|---|
| `design/pro-table-runtime.js` 列④ | 「低于三线」+ 4h收盘 | 「蓄力中/非蓄力」+ `I4b 强🟢/中🟡/弱🔴勿入场` + 横盘 + 前7根量 + RSI +「蓄力≠入场」；列头 →「④ 蓄力三线 / 点火」 |
| 8768 筛选栏 | 无 I4b 勾选 | `ensureShell` 注入 `#ignitionFilters`（强/中/弱）；`combined` 与点火 AND |
| 策略卡 T2 | 标题「蓄力后修复点火」，卡面只显示 Stage1 候选公式 | 标题 →「蓄力后等I4b点火」；卡面增加 **触发** 行（含 I4b） |
| 策略卡 T8 | candidateText「在三线上」易与 I4b 混淆 | →「已多头排列且收盘在三线上方（趋势延续，≠I4b从下方点火）…」 |
| 组合预设 `repair` | 「①②④ 蓄力后修复点火」 | 随 T2 标题自动变为「①②④ 蓄力后等I4b点火」 |
| 原型指标指南 ④ | 「均线受压」 | 「蓄力三线下方」+ I4b 说明 |
| `index.html` 快捷预设 | 「仅④均线压制」 | 「仅④蓄力三线下方」 |

未新造策略：只对齐既有 T1–T8 / 组合文案与展示。

## 4. 源文件与发布

- 源：`strategy-registry.json` + `design/strategy-registry.template.js`
- 发布：`python design/integrate-evidence.py` → `design/strategy-registry.js` + 同步 `index.html` 嵌入 registry  
- registry hash 前缀：`6606892f9695`
- 备份：`reserve-history/i4-frontend-align-20261015-235701/`
- `node --check`：`pro-table-runtime.js` / `strategy-registry.js` / template 通过

## 5. 用户请硬刷新后核对（精确标签）

对 **http://127.0.0.1:8768/**（V2.8 工作台）Ctrl+F5：

1. 筛选④旁公式：`已收盘4h低于EMA7/25/99（Stage1观察，非入场）`
2. ④列表下方新行：**I4b 点火（入场观察，≠④蓄力）：** `强🟢` / `中🟡` / `弱🔴（勿入场）`
3. 表格列头：**④ 蓄力三线 / 点火**
4. 单元格内可见：`蓄力中` 或 `非蓄力`；`I4b 强🟢|中🟡|弱🔴勿入场|无点火`；`横盘可观察|横盘不符`；`前7根量 …×`；`RSI …`；`蓄力≠入场`
5. 策略模板 T2 标题：**蓄力后等I4b点火**；卡上有 **触发** 段含 I4b
6. T8 候选文案含：**≠I4b从下方点火**
7. 组合下拉里 repair：**①②④ 蓄力后等I4b点火**

8767 简页本已基本对齐；本次顺手去掉「仅④均线压制」旧标签。

## 6. 未做 / 边界

- 未宣称 A 档完成；未改点火算法与扫描逻辑。
- 未把 I4b 并入六项勾选下标（仍为旁路筛选，符合手册）。
