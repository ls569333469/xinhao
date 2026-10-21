# Codex 审计修复（P1/P2）

时间：2026-10-14 14:05 Asia/Shanghai

## 修了什么
1. **已选页签**：`rebuildTable` 改用 `window.pageRows`，勾选视图与标题行数一致。
2. **编辑提示词**：`_effective_prompt_bits`；证据与模型 payload 使用编辑模板/关注点；`requested_search` 覆盖 search；`bind_snapshot` 传入冻结字段。
3. **遗漏列**：可选「储备日变化 / 7日 / 连减天数 / 历史净流」，与⑤精确净流分列；列配置显示有效数。
4. **设计备注**：去掉「完整专业表格」「净流占位」「近空·建议隐藏」等。
5. **表头 sticky**：更高特异性 + `!important` 盖过 `max-height:none`。
6. **空 AI 正文**：可见正文为空时提示「报告暂无正文」。
7. **已选页签按钮**：横向 padding 10px，避免挤字。

## 备份
`runtime/code-backups/frontend-codex-audit-fix-20261014T140254`

## 未宣称完成
扫描/投研/策略仍待现网点验与真 AI；不标模块完成。
