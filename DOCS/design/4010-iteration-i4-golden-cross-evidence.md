# 迭代证据：I4 蓄力 + I4b 金叉点火（对齐新手册）

> 时间：2026-10-15 23:41 Asia/Shanghai  
> 状态：**已实现代码与 UI 对齐**；**未**标 A 档 01/02/03 完成；**未**声称回测盈亏。  
> 手册：`DOCS/design/strategies/01-EMA三线金叉与合约Squeeze.md`  
> 方案：`DOCS/design/给雪球-I4均线压制更新为金叉方案-Grok-v1.md`

## 1. 改了什么

### 后端 `scanner.py`
- 合约 4h K 线 `limit`：300 → **1500**；`emaQuality`：`insufficient` / `short_window` / `adequate` / `long_window`。
- 新增：`rsi`、`sustained_new_lows`、`ignition_fields`。
- 新字段：`barVolumeRatio7`、`emaStack`、`emaStackPrev`、`ema7Cross25`、`priceAboveEma99`、`ema7Below25`、`ignitionGrade`、`rsi4h`、`sidewaysOk`、`deathTrend`、`emaQuality`。
- 保留 `barVolumeRatio`（前 20 根）给 T8；点火用前 7 根。
- `classify`：
  - Stage1 硬门槛改为 **I1+I2**；**I3 费率=加分**（命中时后缀「费率加分」）。
  - 废弃升格路径「收盘突破线索」（旧 `crossEma99`+`barVolumeRatio>1.5`）。
  - 点火强/中 → `点火强信号·观察` / `点火中信号·观察`；弱 → riskFlags「勿当入场」。
  - 蓄力旁路：`belowAllEma`+`sidewaysOk` 可标「蓄力观察」。

### 注册表
- I4 标题/公式改为「蓄力三线下方…非入场」。
- `extraIndicators.I4b` 描述强/中/弱；`evaluate` 增 `ignition_grade` / `is_true`。
- T1/T2 触发文案对齐 I4b；组合「quiet」文案改为蓄力。

### UI
- ④ 勾选：**蓄力三线下方**（说明非入场）。
- 新增 I4b 筛选：强🟢 / 中🟡 / 弱🔴（勿入场）。
- ④ 列展示蓄力/横盘/点火分级/前7根量/RSI/质量。
- 规则页④改为蓄力 vs 点火分层说明。

### 文档
- `requirements.md`、①–⑫方案时机层、strategies/01 附录 C 已同步「蓄力≠整段④故事」。

## 2. UI 长什么样（硬刷新后看）

1. 自定义筛选里④文案是「蓄力三线下方」，不再叫「均线压制」。
2. ④下方有一行 **I4b 点火** 三个勾选（强/中/弱）。
3. 表格④列：价格 + EMA7/25/99 +「蓄力中/非蓄力」+ 横盘提示 +「蓄力≠入场」+ 点火强/中/弱 + 前7根量。
4. 规则页④写明 Stage1 蓄力 vs Stage2 点火。

## 3. 测试

- 新文件 `test_ema_ignition.py`：12 项（含强/中/弱确定性用例、classify 加分/降级、registry I4b）。
- 连跑 `test_scan_contract.py`：通过（并修了 Windows 下 `observations.jsonl` 默认 gbk 读崩的 encoding）。
- 合计本轮相关：**26 passed**。

## 4. 备份

- `reserve-history/i4-golden-cross-20261015-233657/`

## 5. 服务进程

- 已重启：旧 PID 31988 → 新 **PID 40900**（server.py，空闲重启）。
- 健康检查：8767 version 2.8；registry hash 1fc03a459bf9d1b3；扫描未在跑。

## 6. 用户如何验证

1. 浏览器对 `http://127.0.0.1:8767/` **强制刷新**（Ctrl+F5）。
2. 看④文案与 I4b 勾选；展开证据列看点火字段。
3. **现有 `scan.json` 可能仍缺新字段**，需再跑一次扫描后 `ignitionGrade` / `barVolumeRatio7` 才齐全。
4. 不要把④勾选当成入场；入场观察用 I4b 强/中。

## 7. 明确未做

- 未标 A 档 01/02/03 完成。
- 未做实盘/自动下单。
- 未静默改坏 `barVolumeRatio`(20)。
- 未把 I3 从指标列表删除（仍可勾选；classify 仅作加分）。
