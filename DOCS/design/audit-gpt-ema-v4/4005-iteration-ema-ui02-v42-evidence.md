# iteration-ema-ui02-v42 证据（仅 UI02）

整理时间：2026-10-16 01:26:31（北京时间）。

本轮只做 **指导 → 编辑 → 保存 → 载入 → 冻结** 的模板身份与结构化参数对应。  
**未做** UI03/UI04；**未重启** 服务；**未宣称** A 档完成。展示名仍为 **EMA三线金叉**，工程 id 仍为 T2 / I4。

## 备份

`runtime/code-backups/ema-ui02-v42-20261016-012342/`

## 改了什么

| 文件 | 作用 |
|---|---|
| `design/strategy-runtime.js` | T2 专用 `#emaModeForm`；`applyTemplate` / `setEditorMode`；`readEditorForm` 按模式读写；`loadDraft` 回填完整 params；禁用 24h/前20×1.5/R2·72h |
| `design/prototype.html` | `openGuidedStrategy` 调用 `StrategyStoreUI.applyTemplate`；`showFullEditor` 回到 T1 通用模式 |
| `strategy_store.py` | `_normalize_rules` 保留 `editor_mode` / `registry_*` / `implementation_status`（executable 强制 false） |
| `test_ema_ui02_roundtrip.py` | 离线证明 save/load/freeze 保住 ema99 字段、不是 T1 三字段 |

## before → after

| 点 | 改前 | 改后 |
|---|---|---|
| 点 T2 指导 | 标题改成 EMA三线金叉，隐藏通用表单 | 同左，并挂上 **T2 模板身份 + ema99 机器字段表单** |
| 保存 | 仍读通用表单 → params 只有 oi/账户/费率 三旧字段 | T2 模式读 `#emaModeForm`：`execution_mode/entry_rule/candidate_max_days/…` |
| 载入 | 只回填名称/idea | 识别 `base_template_id=T2` 或 `execution_mode=ema99_close_trail`，回填全部结构化字段并切回 EMA 表单 |
| 不适用旧默认 | 24h 候选、前20×1.5、R2、72h 仍可被误用 | T2 模式下这些控件 **disabled** 并标注不适用 |
| 可执行声明 | 文案易被当成已可跑 | `implementation_status.executable=false`；sim/eval=未完成；冻结后改规则走开新草案 |

## 离线验收

```text
python -m unittest test_ema_ui02_roundtrip.py test_strategy_store.py -v
```

关键：`test_save_load_keeps_ema99_not_t1_triple`、`test_update_then_freeze_preserves_structured_params` 已通过。

## 页面怎么点（硬刷新 8768）

1. 硬刷新工作台。
2. **指标与策略 → 策略模板**，找到 **EMA三线金叉（T2）**，点指导/采用进入编辑。
3. 应看到指导文案下方的 **EMA三线金叉专用参数**（execution_mode=ema99_close_trail 等）；通用步骤表单隐藏；24h/前20×1.5/R2/72h 若仍在 DOM 里则禁用。
4. 点 **保存草案到本地** → 再到 **已存草案** 点 **载入编辑**：参数应仍是 ema99 一套，不是 30/0.8/-50。
5. **冻结当前草案**（版本号自填）→ 冻结后改规则用「开新草案」，不要指望原地改冻结包。

## 已知缺口

- SIM/EVAL 仍未完成；冻结包 `executable` 仍为 false（故意）。
- 表单声明的更宽依赖（oi/account/ema）在现网覆盖不足时，冻结可能被硬闸拒绝——需缺什么补什么，不会偷渡。
- UI03 模拟 / UI04 回测未做。
- **未重启 Python**：静态 JS 硬刷新即可；策略 API 用的是已有 `strategy_store`，本次 normalize 小改若进程已加载旧模块，**仍建议之后重启一次**才稳（本轮未重启）。
