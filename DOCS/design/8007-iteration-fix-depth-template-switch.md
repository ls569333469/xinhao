# 异动观察工作台 · 档位模板切换 UI 修复取证

- **时间**：2026-10-14 20:35 Asia/Shanghai
- **切片**：UI / 提示词档位切换（快速摘要 / 标准投研 / 深度）
- **状态**：离线修复完成；需用户硬刷新后点验三档正文与标签
- **备份**：`reserve-history/fix-depth-template-switch-backup-20261014-203248/`

## 1. 根因

| 假设 | 结论 |
|---|---|
| R2a 把 quick/deep instruction 写成模板 B，三者相同 | **否**。`design/prompt-templates.json` 与 `#prompt-data` 中三档正文本来就不同（len quick=140 / standard=877 / deep=251）；与 R2a 前备份对比 quick/deep 未变，仅 standard 换为模板 B |
| 标签写死「当前深度模板」 | **是**。文案与「恢复当前深度默认模板」、公式行「深度模板」不随 radio 变化，切到快速/标准仍显示「深度」，易误判为正文错了 |
| `loadPrompt` 未按 profile 加载 | **否**。内联脚本已有 `promptEdits[profile]??p.instruction`；缺的是同步刷新标签 |
| `config_store` 迁移把 B 拷进 quick/deep | **否**。`migrate_unmodified_standard_prompt` 只改 `overrides['standard']`；本机 `runtime/config/state.json` overrides 为空 |
| 实际运行脚本路径 | **`design/prototype.html` 内联脚本 + `#prompt-data`**。页面未引用 `prototype-v21.js` / `prototype-runtime-check.js`（仍顺带同步修补以免日后误用） |

用户截图徽章 `research-prompts-v1.1-template-b/standard` 与标准档一致；主要体验问题是**档位文案始终写「深度」**，叠加未硬刷新时的缓存误解。

## 2. 修复内容

1. 公式行：`深度模板` → **`档位模板`**
2. 标签：`当前深度模板…` → 带 `id="depthPromptLabel"` 的 **`当前档位模板（快速/标准/深度，…）`**，随 radio 更新
3. 重置按钮：随档位变为 **`恢复当前快速/标准/深度默认模板`**
4. `loadPrompt()` 增加 `updateDepthTemplateLabels()`；未知 profile 时 warn 并 return
5. **未改**三档默认 instruction（保持 quick 精简摘要、standard=模板 B 八节、deep=更深核验提纲）；**未改** R1 submission_id / R2a B / R2b 检索路径
6. 新增回归测试 `test_depth_template_switch.py`（三档互异、#prompt-data 同步、迁移不碰 quick/deep）

## 3. 文件与 SHA-256（改后）

| 文件 | SHA-256 | 字节 |
|---|---|---|
| `design/prototype.html` | `4475db39f5184b2cb0bc55c586cd8fa2d2d0c468f189f64de7fbf3ae33df79da` | 1424123 |
| `design/prototype-v21.js` | `a55924d8355de496db1300fe3b5009a4166592d5d8978ef102a44f1b136f7448` | 17550 |
| `design/prototype-runtime-check.js` | `30ad306969b2fbd6719a73b78f0c7380e4af0c000573ed414d34d706cf24ddb8` | 80652 |
| `design/prompt-templates.json` | `1ccb2f2334d69abf8e9c7942a3f0d9990b913c9fbad226345e116ccddb180d58` | 7371 |
| `test_depth_template_switch.py` | `fa692c94f0b7bc46bcb37297fefba2592c95bc7adf294ab51a1d77477b86e623` | 3419 |

`config_store.py` **未改**（备份仅作对照）。

## 4. 验证命令

```text
python -m pytest test_depth_template_switch.py test_r2a_template_b.py -q
→ 12 passed
```

## 5. 用户硬刷新点验（必做）

1. 打开 `http://127.0.0.1:8768/prototype.html`（或当前 prototype 端口），**Ctrl+F5 硬刷新**
2. 展开「查看 / 编辑提示词模板」
3. 依次点选 **快速摘要 / 标准投研 / 深度**：
   - 徽章应变为 `research-prompts-v1.1-template-b/quick|standard|deep`
   - 标签应变为「当前档位模板（快速|标准|深度，…）」
   - 重置按钮文案同步变档位名
   - **depthPrompt 正文三份明显不同**：快速最短精简；标准含「模板 B」与八节；深度为更长核验/情景提纲（不是 B 八节拷贝）
4. 公式行应为「固定证据规则 ＋ **档位模板** ＋ …」

## 6. 未做 / 边界

- 未把 deep 改写成「加长版模板 B」（需求写明快速/深度不因 R2a 自动换成 B；且备份显示未被覆盖）
- 未重启服务进程（本轮只改静态 HTML/JS；硬刷新即可）
- 正式页 `index.html` 仍非主投研 UI
