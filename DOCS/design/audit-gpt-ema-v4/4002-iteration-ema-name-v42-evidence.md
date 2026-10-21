# iteration-ema-name-v42 证据（仅第1节：统一命名）

整理时间：2026-10-16 01:16:08（北京时间 / Asia/Shanghai）。

本轮只做 **用户可见名称 → EMA三线金叉**，以及 **名称与状态/强弱分开展示**。  
**未做** UI01–UI04（池/保存/模拟/回测），**未改** I4 计算、`below_ema`、T2 工程身份、历史冻结包。

## 备份

`runtime/code-backups/ema-name-v42-20261016-011341/`（改前拷贝：index.html、strategy-registry.json、design/strategy-registry.js、template、market-evidence.js、pro-table-runtime.js、prototype.html、formal/prototype-runtime-check、策略手册）。

## 改动文件

| 文件 | 改什么 |
|---|---|
| `strategy-registry.json` | I4.title、T2.title、quiet 组合名、I4b 展示标题/备注；T2 trigger/risk/missing 去掉工程腔（轻量 UI07） |
| `design/strategy-registry.js` | 由 `python design/integrate-evidence.py` 自 JSON+template 重生 |
| `index.html` | 左侧④、表头④、预设、指南卡片、内嵌 registry、阶段/级别文案 |
| `design/pro-table-runtime.js` | 列名④、阶段标签 helper、点火筛选旁注 |
| `design/market-evidence.js` | 列名④、阶段/级别分开展示 |
| `design/prototype.html` | 指标说明 tip ④ |
| `design/formal-runtime-check.js` / `prototype-runtime-check.js` | 与展示名对齐，防检查脚本把旧名加回来 |
| `DOCS/design/strategies/01-EMA三线金叉与合约Squeeze.md` | 用户可见 H1 → EMA三线金叉；保留 I4/I4b/T2 工程叙述 |

生成命令：`python design/integrate-evidence.py`（同步两页内嵌/发布 registry，**未重启服务**）。

当前 registry 指纹（进程外新读）：`8a74ca81212f…`（全长 `8a74ca81212fe5a10673c64365162fb240903ef38822772ec7f31d58ec00c0e6`）。

## before → after（样例）

| 位置 | 改前 | 改后 |
|---|---|---|
| 左侧第④项 | ④ 蓄力三线下方 | ④ EMA三线金叉 |
| 扫描表第④列 | ④ 蓄力/点火 或 ④ 蓄力三线 / 点火 | ④ EMA三线金叉 |
| I4.title（registry） | 蓄力三线下方 | EMA三线金叉 |
| T2.title（策略卡片/指导） | 蓄力后等I4b点火 | EMA三线金叉 |
| 快捷预设 | 仅④蓄力三线下方 | 仅④ EMA三线金叉 |
| 组合 quiet | ④⑥ 蓄力放量价平 | ④⑥ EMA三线金叉＋量增价平 |
| 指南主标题 | ④ 蓄力三线下方（非入场） | ④ EMA三线金叉；「已收盘4H低于EMA7/25/99」放进展开的入池规则说明 |
| 单元格阶段 | 常把 belowAllEma 直接写成「蓄力中/非蓄力」当主状态 | 有 `emaSqueezeStage` 才映射：蓄力中 / 等待金叉 / 等待放量 / 点火已确认 / 已失效；缺字段 → **未记录**；强弱单独「级别」行 |
| I4.rule.op | below_ema | below_ema（未改） |
| I4 / I4b / T2 id | I4 / I4b / T2 | 同左（未改） |

## 明确未改

- I4 计算与六指标第④项语义：仍是 `below_ema`（三线下方入池筛选），**没有**改成金叉布尔。
- T2 / I4 / I4b 工程 id；历史冻结包、旧快照、scanner 里已落盘的 `state` 文案（prototype 内嵌快照里的「蓄力观察」等保留）。
- UI01–UI04 池/保存/模拟/回测链路；未宣称 A 档 01/02/03 完成。
- 未重启 Python 服务。

## 第1节勾选对照（源码层）

- [x] 主要加载入口统一名称（8767 index、8768 prototype+pro-table、registry JSON/生成 JS、market-evidence、生成源 JSON）。
- [x] 主标题去掉「蓄力三线下方」「蓄力后等I4b点火」等；工程 id 未覆盖。
- [x] 名称与状态分开；缺字段「未记录」；不单凭三线位置编造完整生命周期（无 stage 时不把 belowAllEma 当阶段）。
- [x] 强/中/弱与阶段分行。
- [x] 「已收盘4H低于EMA7/25/99」进展开入池说明；计算未改。

## 刷新说明

- **前端静态资源**：硬刷新即可看到新文案（是）。
- **API / 进程内 registry 哈希**：若 `server.py` 仍在跑，内存里旧 hash 要等用户确认后的 Python 重启才更新（是，重启前 API 仍可能报旧指纹）。
