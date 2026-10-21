# 投研迭代 R2a（v1.1 §4）离线取证 · 模板 B / 旧报告兼容 / 前端 submission 接线

- **时间**：2026-10-14 20:04 Asia/Shanghai
- **切片**：R2a only（offline；无付费 AI；无 R2b 检索适配器；无 R3/CON 并发）
- **对应**：v1.0 R2 结构部分 + v1.1 REV-05 / REV-06 / REV-08 + R1 残留（preview→confirm 带回 submission_id/intent_hash）
- **状态**：**离线通过**；**未**宣称完整投研完成；**未**标 01/02/03 A 档完成；**未**跑 R2b/R3；经验库页未做
- **备份目录**：`reserve-history/r2a-template-b-backup-20261014-200326/`（含改前源码与 prototype）

## 1. 本轮做了什么（前后逻辑）

### 1.1 标准档接入最初模板 B（结构 + 实际提示词 + schema 版本）

| 之前 | 之后 |
|---|---|
| `design/prompt-templates.json` = `research-prompts-v1.0`，standard 为旧八步证据提纲 | 版本 **`research-prompts-v1.1-template-b`**；standard 使用附录原文八节 + 项目证据约束；`template: "B"` |
| schema = `research-report-v1`（metadata/contracts/basket/sources） | schema = **`research-report-v2-template-b`**，显式 `sections` 八节 + attachments（六指标/三态/缺口）；保留 `legacy_schema_version` |
| prototype `#prompt-data` 仍嵌旧 v1.0 | 已与 `prompt-templates.json` 同步 |

八节：核心催化剂 / 价格与市场 / 风险与机会 / 技术面 / 合约结构 / 基本面 / 支撑压力 / 综合判断。quick/deep **未**自动换成 B。

### 1.2 REV-08 旧报告与自定义提示词

- **旧报告读取**：`_detect_body_kind` 识别 `research_report_v1` / `template_b_json` / `template_b_markdown`；v1 JSON 走原四顶层校验，**不会**把 B 八节标题当成 missing 失败。
- **未改过的默认 standard**：若 `depth_template` 精确等于 v1.0 standard（sha256 `c5eb5efd17439942…`）→ 迁移为当前 B instruction，并记录 `prompt_migration.action=migrated_default_to_template_b`。
- **用户自定义**：与目录默认及遗留默认均不同 → `keep_user_custom`，保留原文并标明 `base_version`；**不静默覆盖**。
- `config_store.get_prompt_edits` 增加 `migration_hint`；`migrate_unmodified_standard_prompt` 可显式迁移遗留 override。本机 `runtime/config/state.json` 无 overrides，已戳 `base_version=research-prompts-v1.1-template-b`。

### 1.3 UI / 导出八节一致（RES-13）

- `design/reports-runtime.js`：B Markdown / B `sections` JSON 渲染为统一八节卡片；六指标三态与 `sources` 进可展开附件，不因简洁丢约束。
- 导出正文可规范化为八个 `##` 标题；JSON 导出带上 `schema_version`。
- **旧 v1 结构化报告**仍走原 `renderContractReadable`，不被 B 强解析。

### 1.4 前端 submission 接线（R1 残留）

- `preview` 本就返回 `submission_id` + `intent_hash`（R1）。
- `design/ai-runtime.js`：确认时把 preview 的 `submission_id`、`preview_intent_hash`/`intent_hash` 写回 POST `/api/reports`，以匹配 `task_service` 幂等与陈旧预览拒绝。
- 预览状态行显示模板 B / 迁移动作 / sub·intent 短前缀。

### 1.5 REV-05 / REV-06 措辞

- standard instruction 与 common 写明：**163m 分类仍 TBD**（与 127m 子集口径差异待核对，不作已证实多类别断言）；**ATR=0 不自动无效**（先核精度/算法/预热）。
- 明确禁止「二手日历」生造词（按原文要求写入提示词禁令）。

## 2. 验收对照（离线）

| ID | 结果 | 说明 |
|---|---|---|
| RES-13 | 离线通过 | B 八节 UI/导出路径就位；sources/三态附件保留；旧 v1 不丢读 |
| REV-08 | 离线通过 | 旧 v1 不强制 B；默认可迁；自定义保留 |
| REV-05 | 文案落地 | 163m 分类 TBD 写入模板/common |
| REV-06 | 文案落地 | ATR=0 不自动无效 |
| R1 残留 | 离线接线 | confirm 回传 submission_id + intent_hash |
| RES-07+ 检索 | **未做** | R2b |
| 页面硬刷点验 | **未做** | 需用户硬刷新后点验 |

## 3. 文件与 SHA-256

| 文件 | SHA-256（改后） | 改前（备份） |
|---|---|---|
| design/prompt-templates.json | `1ccb2f2334d69abf8e9c7942a3f0d9990b913c9fbad226345e116ccddb180d58` | `da734e089102cee527ed9e8f2e071a61ee7850db4b184f5e8c2528b96a30ca22` |
| ai_research.py | `ec1e9264d2aebab90a857ba346f90b0643cc810be5bf89f3720fd9f6b00a1166` | `95a55b5a6ed691d116662b10406fcb6c26f860ed5dc0c63ee4e919066ce09686` |
| design/ai-runtime.js | `201882df4308087779921ddc1050190e0ea454ec3a26c338fa3a90a3a3ed665d` | `7dfa8bf1dc43f8b6e0c57047813308e430f459feb5373d3ae74cc858dff6e0a3` |
| design/reports-runtime.js | `67917c7c5e1cadcff26c56298ec05e297944dcd69d611bd17556ec2ba40f6324` | `642a663ea5db919151982623cd4ac358bcdb43445a7f2c520304dfc468dab94c` |
| config_store.py | `e6f53f5cd25bf1fbfa9f3637ab0692c7536698ca64c63a071ec440afff7027db` | `2411da1409dc75b31eaa83a0c0a17ee1abe1b47fa2666d59903b5b0752e94cfa` |
| test_r2a_template_b.py | `78f4178be3f100c43525823e270d1e2f46de67ec832b4e3aa080fd93f886e38b` | `—` |
| design/prototype.html#prompt-data | `c4107ddacdd0b05339bdf00f02dd7ba47445da5c335e4627bb9ad04dfb58275c` | `5041c7730184618f044ddbdfb04103922e039bbde8754901c97f7ad937e20e96` |

## 4. 验证命令与结果

```text
python -m pytest test_r2a_template_b.py test_ai_research.py -q
→ 26 passed
```

（含 R2a 9 项 + 既有 ai_research 17 项回归。）

## 5. UI 仍需用户硬刷新

- `design/ai-runtime.js`、`design/reports-runtime.js`、`design/prompt-templates.json`、`design/prototype.html`（`#prompt-data`）均静态加载 → **浏览器硬刷新**后才会看到：
  - 标准档深度模板正文变为模板 B；
  - 预览状态含 sub/intent / 模板B；
  - 历史 B Markdown 报告按八节卡片展示；旧 v1 JSON 仍可读。
- 若服务进程在 R2a 前已启动，需**重启**以加载新 `ai_research.py` / `config_store.py`（本轮未操作运行中的服务）。

## 6. 开放的 R2b 项（明确不做）

1. 真实检索适配器（先检索后生成）与 evidence 登记。
2. 勾选检索 ≠ 已检索的端到端供应商取证。
3. usage 从真实供应商响应记账（仍可能 unknown）。
4. 正式页 `index.html` 仍缺完整投研控件（研究 UI 主要在 prototype / 既有 runtime）；本轮未重做整页迁移。

## 7. 残留与下一步

| 项 | 状态 |
|---|---|
| R2b 真实检索 | 下一步 |
| R3 授权范围内真实调用 / 并发 CON | 后置 |
| 经验库页面 | 后置 |
| 用户硬刷 + 打开旧报告/新预览点验 | 待用户 |
| 正式 index 与 prototype 研究控件对齐 | 已知差距，非本切片强制 |

## 8. 未宣称

完整投研完成、最新资料已检索、01/02/03 A 档完成、v1.2 多报告并发、付费闭环。
