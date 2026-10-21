# EMA观察与事件字段契约 v1

更新：2026-10-16；对应策略手册附录 D.10。

本次保持 `ema-squeeze-v4` 正式判定和 registry 指纹。观测 schema 为 `ema-observation-v1`；新候选交叉事件 schema 为 `ema-cross-event-v2`。本文件不更改冻结策略与止损执行。

## 数据来源与质量

- 输入仅用 Binance 已收盘4H K线；至少120根连续有效窗口。首根收盘初始化EMA，同一序列递推。缺口或无效OHLC重置预热。
- 历史根也要求至少120根有效前缀。当前120根可计算当前状态，不意味着上一根119根可作为有效历史状态。
- 旧快照可从其 `candleArchives.4h` 指向的原始归档只读派生；校验市场/合约/周期/最后收盘时间。`derived_at_read=true` 明示派生；不写回历史scan.json，也不伪造新扫描。
- 缺来源、时间不匹配、预热不足保持未知。旧CTUSDT仅90根且缺对应截止时间，不能补造有效观察。

## emaObservation

| 字段 | 含义 |
|---|---|
| bar_time | 当前已闭合4H收盘时刻，UTC毫秒 |
| quality / valid_bars | 质量及当前连续有效段长度 |
| weak_new_cross | 前一根C≤该根EMA99，本根C>该根EMA99；前后均通过预热；本字段本身不等于正式弱级 |
| above99_count / above99_count_exact | 连续C>各自EMA99根数；遇等号中断；历史耗尽为下限，exact=false |
| history60 | 当前根之前60根 `[t-60,t-1]` 的多头排列证据；coverage、是否出现、已观察时间和可信最近时间 |
| drawdown60_pct | max(0,(历史60根最高收盘-当前收盘)/历史最高收盘×100)；历史覆盖不足为null |
| distance_ema99_pct / distance_ema7_pct | (C-EMA)/EMA×100，有符号 |
| candle_return_pct / return5_pct | 单根(C/O-1)×100；五期(C[t]/C[t-5]-1)×100，需要6个连续收盘点 |
| atr14 / atr_method | 复用共享 `struct_indicators.compute_atr`，ATR_WILDER_v1 |
| distance_ema99_atr / distance_ema7_atr | (C-EMA)/ATR14；ATR≤0或未知为null |
| candle_shape | OHLC、实体、上影、阳线布尔值；零实体的上影/实体比为null |
| research_path_b | 路径B结构观察，不进入正式strong/candidate/entry计数 |
| technical_crosses / technical_events | 本根技术交叉及稳定ID；ID=市场/合约/4h/规则版本/收盘根/种类，不依赖将来才创建的候选周期；technical_only=true，非入场许可 |
| execution_thresholds_enabled | 固定false；本次未启用4H时效、5%/10%价格偏离等闸门 |

`history60.stack_seen=true` 可由部分窗口的有效正证据支持；只有全60根有效才能给false；不足且无正证据为null。历史窗口不包含当前根。

## 候选交叉事件与恢复

- 事件ID包含市场、合约、周期、正式规则、候选周期、交叉根、等级。强/中是正式路径A/中交叉；不启用路径B正式强级。
- `cross_bar_time`、`confirm_bar_time` 为UTC毫秒闭合根身份；`first_seen_at`、`observed_at`、`confirmed_at` 为系统实际发现/处理时刻UTC ISO。
- 兼容 `confirm_time`：新记录仍为系统处理时间，旧记录语义不重解释。旧记录无确认根时间就显示“确认根时间未知”。
- 当根量比≥2确认；未满足最多等待真实T+1。T+2不能替代T+1。未知结构、OI、量能与实际不满足分别记录原因。
- 同根重扫不推进、不重复；更旧根不倒退；终结事件不复活；旧pending处理后仍处理当前独立新交叉。
- 停机恢复仅重放既存候选pending的真实T+1归档。缺历史资格不得补造入场，标 `historical=true / entry_eligibility=unverified_historical / automatic_fill=false`。
- `next_open_reference` 仅保存真实紧接确认根的下一根开盘价和来源/取得时间，不是成交价。开盘参考不参加尚未闭合根的EMA计算。
- COIN-M量能需contractSize换算为USD合约名义额；旧数据缺contractSize时量能为未知，不能按币数量伪装USD。
- 持久化使用仓库唯一临时文件/fsync/原子替换；损坏池失败关闭，不清空重建。换周期保留旧周期及事件。

## 展示和下游

B方案主表最多两行，新穿/持续/未知分开。详情、CSV `ema-evidence-csv-v4.3`、数据报告和投研证据保留同一观察对象及候选事件；确认根时间和处理时间分别显示。旧事件不冒充本根新强级。所有新字段为可选，缺失显示未知。

投研模板沿用标准B八节，新增当前规则说明及EMA观察证据；不执行模型测试，不更改用户自定义关注点。规则、观察与交易执行分层，恢复确认不自动补历史成交。

## 验证边界

源码与118项离线用例通过不等于新一轮真实扫描闭环已验收。运行与页面结果见策略手册底部 D.11 及项目外实施报告。后续真实扫描自然写入新字段，不需要为旧归档展示额外发起扫描。


## 2026-10-16 v1.1修正

本次真实扫描暴露：观察档案的 `medium_ema7_25_cross` 曾额外要求EMA25<EMA99，导致NEARUSDT、NEARUSDC、NEARUSD_PERP、ZROUSDT、CGPTUSDT共5份技术事件未记入。正式 `mediumCrossEvent` 和弱/中/强计数不受影响。

观察schema升级为 `ema-observation-v1.1`，技术中/强交叉直接复用既有 `ema_squeeze_rules.medium_cross/full_cross`，不再维护额外条件副本。不修改正式策略、阈值或冻结ID。旧scan.json与归档保留原样；读取时从匹配原始归档派生v1.1，显式标记derived_at_read。旧观察版本不静默冒充新版本。
