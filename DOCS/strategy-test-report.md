# 现有数据策略筛选测试

数据：2026-10-12 北京时间20:03:26—20:07:37保存快照；GTC市值为后续补核值。本测试没有补采行情。

588份合约；图片市值范围为1000万（含）至5000万（不含）美元，共177份。每份合约分别计数，组合之间可重叠。

结果性质：横截面指标与候选筛选测试，不是历史收益回测、胜率验证或开仓指令。

执行：16条规则×2个范围=32组结果；重算588行六指标与保存值完全一致，18组页面筛选交叉核对通过。

| 规则 | 条件 | 全市场明确命中 | 全市场未知 | 图片范围明确命中 | 图片范围未知 | 图片范围明确命中标的 |
|---|---|---:|---:|---:|---:|---|
| S1 | ①②③ | 1 | 0 | 1 | 0 | GTCUSDT |
| S2 | ①②④ | 1 | 2 | 1 | 2 | ENSOUSDT |
| S3 | ⑥ | 3 | 1 | 2 | 1 | HMSTRUSDT、KGENUSDT |
| S4 | ④⑥ | 1 | 1 | 1 | 1 | HMSTRUSDT |
| S5 | ①⑥ | 1 | 1 | 0 | 1 | 无 |
| S6 | ②③ | 2 | 0 | 2 | 0 | GTCUSDT、OGNUSDT |
| S7 | ①② | 7 | 3 | 5 | 3 | GTCUSDT、ENSOUSDT、MOVRUSDT、INITUSDT、ATUSDT |
| S8 | ③④ | 3 | 0 | 1 | 1 | CTKUSDT |
| S9 | ①②③④ | 0 | 0 | 0 | 0 | 无 |
| S10 | EMA7>EMA25>EMA99且已收盘4h在三线上方 | 67 | 1 | 16 | 4 | GTCUSDT、MAGICUSDT、SKLUSDT、MANTAUSDT、METISUSDT、CYBERUSDT、SXTUSDT、HYPERUSDT、IDUSDT、TRUTHUSDT、FLUXUSDT、DIAUSDT、IOTXUSDT、CELRUSDT、WOOUSDT、CTRUSDT |
| S11 | S10且barVolumeRatio>1.5且同窗24h上涨 | 30 | 1 | 9 | 3 | GTCUSDT、MANTAUSDT、METISUSDT、CYBERUSDT、SXTUSDT、HYPERUSDT、IDUSDT、FLUXUSDT、IOTXUSDT |
| S12 | 已保存本根4h上穿EMA99标记 | 9 | 1 | 3 | 1 | MOVRUSDT、GWEIUSDT、VELVETUSDT |
| S13 | S12且barVolumeRatio>1.5 | 7 | 1 | 2 | 1 | MOVRUSDT、GWEIUSDT |
| S14 | S12且①② | 1 | 1 | 1 | 1 | MOVRUSDT |
| S15 | volumeRatio>3且同窗24h上涨>2%且非三线下方 | 19 | 1 | 6 | 4 | REDUSDT、MANTAUSDT、METISUSDT、DYMUSDT、SXTUSDT、VANAUSDT |
| R1 | 风险排除：volumeRatio>3且同窗24h跌幅<-2%且三线下方 | 2 | 1 | 2 | 1 | CTKUSDT、ZESTUSDT |

未知包含缺少市值、K线或所选字段但仍可能改变筛选结果的合约。缺市值合约不能确认是否属于图片范围，既不算明确命中，也不算失败。第五项全缺，当前不能验证链上吸筹。

## 数据覆盖

- contracts: 588
- observations: 588
- uniqueObservationContracts: 588
- fullOhlcv: 0
- chain: 0
- settledHistory: 1
- topAccount: 1
- topPosition: 1
- cap: 518
- capTrusted: 69
- ema: 587

原始观测588行对应588份不同合约，没有连续每币时间序列；完整OHLCV未保存。EMA上穿使用已保存crossEma99标记，无法用完整K线独立重算跨线过程。

## 候选解释

- GTC：①②③命中，但24h涨幅54.50%、当前极端费率与最近4次结算不等同。不是底部潜伏确认。
- ENSO：①②④命中，但当前费率为正、量倍数约1；市值映射待核验。可列为等待修复观察。
- HMSTR：④⑥命中，量3.77倍、同窗上涨1.42%；本根量0.77倍。不能确认持续吸筹。
- KGEN：⑥命中，量9.22倍、同窗上涨0.64%；本根量83.30倍。异常集中换手值得继续查，不等于买盘或吸筹。
- MOVR：①②及4h上穿EMA99，本根量2.63倍，24h对齐量0.27倍；EMA7/25/99没有形成多头排列，不能当作日线箱体/ATH突破。
- GWEI：4h上穿EMA99，本根量7.58倍；市值映射待核验。
- CTK与ZEST：放量下跌且三线压制，作为风险排除线索。

## 实际支持等级

可运行：六项中已有五项的组合筛选，4h均线排列/位置，本根量异常，上穿EMA99标记及其量能/合约结构确认，24h量价形态，放量下跌风险筛选。

尚不能验证：持续吸筹、日线箱体、完整历史ATH、OI与费率持续性、多币历史收益回测。缺少完整时间序列、日线/全历史价格、历史市值、费用/成交模拟与后续价格。

检查四份模板报告均未提供可执行支撑压力、完整基本面或已确认回踩，不能直接从现有报告推出完整交易计划。

## 复现

运行 node strategy-data-test.cjs（当前目录）。基线断言对应本次保存快照，新扫描需建立新基线。