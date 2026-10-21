# EMA-Squeeze v4 增量实施证据

日期：2026-10-16（Asia/Shanghai）。依据：`DOCS/design/audit-gpt-ema-v4/给GROK-EMA-Squeeze增量实施规则-v4.md` 与《审计报告.md》。

**本轮未宣称 A 档完成。** 未重启服务（PID 40900 忙，8767/8768 有连接）。未发起全市场扫描、真实交易或外调验收。

## 复用（不重做）

- I4 三线下方六项下标；I4b 旁路 `ignitionGrade`
- `barVolumeRatio7` 与旧前20 `barVolumeRatio` 分离
- RSI14/Wilder、闭合连续 4H、最长 1500、emaQuality
- ③费率 classify 加分（非硬门槛）
- 两套前端已有点火展示/筛选控件（在此上增量阶段字段）

## 本轮新增 / 替换

| 缺口 | 落地 |
|---|---|
| 完全金叉定义 | `ema_squeeze_rules.full_cross`；`ignition_fields` 强信号改为 EMA25 首次上穿99，不再用 `not emaStackPrev` |
| 下一根放量确认 | `ema_squeeze_store` pending_cross，只等 1 根；第三根放量不能兑现 |
| 30天候选历史 | `runtime/ema-squeeze-candidates.json`；`entered_at` 不因重扫刷新 |
| 入池 OI 基准 | `entry_oi_usd` 固定；`<70%` 出池；等于 70% 保留 |
| 新低 / 振幅 | `death_trend_new_lows` 10根/≥3次；`amplitude_ok` 20根 high/low&lt;1.35；写入 `sidewaysOk` |
| EMA99 收盘追踪 | `process_close_for_trail` / `resolve_ema99_trail_bar`；盘中触价不退出 |
| 独立执行模式 | `frozen_exec`：`execution_mode=ema99_close_trail`；旧 `pct_touch` 行为保留；禁止为新模式补默认 tp/sl |
| 候选统计分离 | `candidateCount`/`candidateCount12`=①②；`candidateCount123`=旧①②③；报告文案分开 |
| 指纹换行归一 | `registry_fingerprint` LF 归一；`integrate-evidence.py` 同口径；旧冻结 hash 不重算 |

## 备份

`runtime/code-backups/ema-squeeze-v4-20261016-004236/`

## 测试

```text
pytest test_ema_ignition.py test_ema_squeeze_v4.py test_frozen_exec.py -q
→ 36 passed
```

覆盖：完全金叉反例、下一根确认/第三根作废、10/3新低与用户5次样例、振幅边界、OI 70%边界、30天到期、追踪开关注册与棘轮、旧触价仍盘中止损、新模式次开盘成交、冻结 schema 分派、CRLF/LF 同 hash。既有 12 个 ignition 用例仍通过。

## 未做 / 用户需核对

1. **服务未重启**：磁盘已更新，进程 40900 仍可能加载旧 `scanner`/`strategy_registry` 模块；空闲后重启再核 `registryHash`。
2. **未跑全市场扫描**：候选池文件在首次带新代码的扫描后才会填充。
3. **未做浏览器 DOM 隔离验收**；8767/8768 静态资源需硬刷新。
4. **无完整历史 OI/流通市值回测**；`ema_squeeze_confirmed` 入场路径需预计算确认事件，不能用 OHLC 突破顶替。
5. **未新建已启用的冻结执行包**；T2 仍 `executable=false`，文案已指向 v4，不冒充已可自动开仓。
6. **不标记 A 档完成**。

## 关键文件

- `ema_squeeze_rules.py`、`ema_squeeze_store.py`
- `test_ema_squeeze_v4.py`
- 修改：`scanner.py`、`frozen_exec.py`、`strategy_registry.py`、`strategy-registry.json`、`backtest_store.py`、`design/integrate-evidence.py`、`design/strategy-registry.js`、`design/pro-table-runtime.js`、`index.html`
