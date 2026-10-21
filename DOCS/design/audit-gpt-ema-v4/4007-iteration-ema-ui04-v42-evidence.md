# UI04 证据 — 回测单跑/公平对照不提交旧替代规则（v4.2）

- 时间：2026-10-16 01:38:56 Asia/Shanghai
- 切片：UI04 only（未重启 server；未开 P2）
- 备份：`runtime/code-backups/ema-ui04-v42-20261016-013430/`

## 目标对照

| 清单项 | 结果 |
|---|---|
| 从冻结读取 mode/entry_rule；EMA99 不强塞旧突破/5%/3%/42 | 通过（FE `buildRunBodyFromFrozen` + frozen_exec 冲突） |
| 单跑与对照都按模式生成；展示完整冻结规则+版本；不覆盖冻结 | 通过（`#btFrozenNote`；compare 不传统一旧 tp/sl/entry） |
| 缺历史 OI/市值/账户 → 完整策略明确不足；技术切片单独命名 | 通过（`backtest_scope=full_strategy` → insufficient + missing_evidence；`ema_tech_slice` → unimplemented/insufficient + `not_full_strategy_pnl`） |

## 改动文件

### 前端 `design/backtest-runtime.js`
- 移除手填入场下拉与默认 5/3/42 输入提交。
- 新增：`versionParams` / `findVersion` / `executionModeFromParams` / `renderFrozenNote` / `buildRunBodyFromFrozen`。
- 冻结注：展示 title@version、version_id、指纹、mode、entry_rule、止损/追踪或 tp/sl。
- ema99 显示「回测范围」：`full_strategy` | `ema_tech_slice`。
- `startRun` / `startCompare`：**不再**传 `allow_generic_price_rule: true`、不再默认 `close_above_prev_high` / 5% / 3% / 42；参数只读冻结。

### 后端 `backtest_store.py`
- `_normalize_backtest_scope` / `_historical_evidence_gaps`。
- `run()`：`execution_mode==ema99_close_trail` 早退分支——完整策略缺证据 → `insufficient`；技术切片单独命名且 `not_full_strategy_pnl=true`。
- pct_touch：缺冻结 tp/sl/max_hold_bars 明确拒绝，不发明 5/3/42。
- `_persist_run`；compare `shared` 透传 `backtest_scope`。

### 测试
- `test_ema_ui04_backtest_mode.py` → **7/7 OK**

## 离线测试

```
python -m unittest test_ema_ui04_backtest_mode -v
Ran 7 tests OK
```

## 点击核验（硬刷，不重启）

1. 回测页选 **EMA99 冻结版**：冻结注显示 mode/entry_rule，无旧 5/3/42 输入；范围默认「完整策略」。
2. 启动回测（无历史 OI 证据）→ 状态 insufficient，status_note 含「完整策略」「historical_oi」，详情 `not_full_strategy_pnl=true`。
3. 范围改「EMA技术切片」→ 状态 unimplemented/insufficient，文案含「技术切片」「非完整策略」。
4. 若仍用旧客户端塞 close_above_prev_high+5/3/42 → 引擎 409/ValueError 拒绝。
5. 公平对照多选 ema99+pct_touch：不统一塞旧参数；ema 格计入 insufficient。

## 未做

- **未重启 server**；**未做 P2（UI05–UI08/SCREEN）**。
- ema99 完整撮合引擎 / 真实历史 OI 序列接入仍为后续；本刀保证不顶替、不足时诚实命名。
