# UI03 证据 — 前向观察及模拟按冻结模式分支（v4.2）

- 时间：2026-10-16 01:33:32 Asia/Shanghai
- 切片：UI03 only（未做 UI04；未重启 server）
- 备份：`runtime/code-backups/ema-ui03-v42-20261016-012838/`

## 目标对照清单

| 清单项 | 结果 |
|---|---|
| EMA99 不强制旧固定 tp/sl/持仓天；pct_touch 仍旧校验 | 通过（frozen_exec + eval start） |
| UI 不发明缺失参数（含不默认 3% 触价） | 通过（FE doStart / frozen note；引擎 409） |
| 模拟/观察展示执行模式、初/现止损、追踪激活、待下开盘出场+原因 | 通过（eval 开仓表列 + sim 冻结注） |
| 后端结算/撮合按模式分支；FE 放行 ≠ 引擎通过 | 通过（`_settle_rules` → `_settle_ema99_trail`；缺 confirm/cross → EvalConflict） |

## 改动文件

### 后端
- `frozen_exec.py`：`require_frozen_exec_params` 按 `ema99_close_trail` / `pct_touch` 分支；`_require_ema99_trail` 不要求 tp/sl，拒绝请求体补旧默认；pct_touch 仍强校验 tp/sl(/hold)。
- `eval_store.py`：
  - `start_observation`：ema99 强制 `confirm_event_id` + `cross_ema99`，用 `initial_stop_state` 建 trail；不发明 tp/sl/max_hold_days。
  - `_settle_rules`：`execution_mode == ema99_close_trail` → `_settle_ema99_trail`（收盘破止损 → 下一开盘出场），否则旧 pct_touch。
  - pending_view 合并 `trail_state` / `current_stop` / `trailing_activated` / `pending_exit(+reason)`。

### 前端
- `design/eval-runtime.js`：
  - `executionModeFromParams` + `syncEvalModeFields`。
  - `renderFrozenNote`：ema99 显示初始止损/追踪/盘中触价/候选期，明确「不使用固定tp/sl/持仓天」。
  - 开观察表单：`#evalEmaFields`（确认事件ID、金叉根EMA99），仅 ema99 显示。
  - `doStart`：pct_touch 仍校验冻结 tp/sl/hold；ema99 要求确认+金叉根，**不向 body 塞 tp/sl/hold**。
  - 开仓表：模式 / 止损·追踪（初/现、追踪已开、待下开盘出·原因）。
- `design/sim-runtime.js`：冻结注按模式分支；ema99 注「无固定tp/sl；缺确认/预热由引擎拒绝」。

### 测试
- `test_ema_ui03_mode_branch.py`（离线，10/10 OK）

## 离线测试结果

```
python -m unittest test_ema_ui03_mode_branch -v
Ran 10 tests in ~0.14s
OK
```

覆盖：
1. frozen：ema99 不要求 tp/sl；拒绝 body 发明 sl_pct；pct_touch 缺参仍拒。
2. eval start：缺确认 / 缺 cross_ema99 → 409；成功开观察无发明 tp/sl，initial_stop=金叉根（≠97 即 3%）。
3. settle 路由：ema99 走 trail，不报 tp。
4. FE 静态：门控字符串 / 不存在旧「必须 tp/sl/hold」一元判断。

## 点击核验（不重启 server；硬刷页面即可）

1. 策略库选已启用 **EMA99 / ema99_close_trail** 冻结版 → 前向观察：
   - 冻结注应写「执行模式 ema99_close_trail」「不使用固定tp/sl/持仓天」。
   - 出现「确认事件ID / 金叉根EMA99」输入；不填点开观察 → toast 拒绝（且服务端也会 409）。
   - 填确认+金叉根后开观察 → 开仓表「模式」列=ema99_close_trail，「止损/追踪」显示初/现止损，无 3% 默认止损价。
2. 选 **pct_touch** 冻结版：仍要求冻结有止盈/止损/持仓天；表仍可走旧触价。
3. 模拟页选 ema99 冻结版：冻结注显示无固定 tp/sl，不诱导填 3%。

## 未做 / 剩余阻塞

- **未做 UI04**（回测单栏模式分支）— 按约束跳过。
- **未重启 server** — 若页面仍跑旧 JS，硬刷新或等静态缓存失效；本切片不重启进程。
- 引擎「完整结构 K / 预热不足」在真实无 K 线场景仍会 409（符合「缺预热/依赖明确拒绝」）；离线测试对 coverage gate 用了旁路，与生产硬闸行为一致方向、不替代真实覆盖验收。
- sim 自动填仓后端若另有路径，本切片只收口 UI 注记 + frozen_exec；深度 sim 撮合 trail 与 UI04/后续可并行核。
- checklist 勾选需人工在清单文件确认（本证据视为 UI03 可勾）。

## 语法

- `node --check design/eval-runtime.js` / `sim-runtime.js` → 0
- `py_compile frozen_exec.py eval_store.py` → ok
