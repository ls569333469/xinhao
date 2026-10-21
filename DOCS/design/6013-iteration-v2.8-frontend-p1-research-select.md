# 前端 P1/P2 修补：选币与投研预览（承接 Codex 审阅意见）

时间：2026-10-14 13:17 上海

## 对照审阅
DOCS/design/剩余工作方案审阅意见.md

## 已修

| ID | 改动 |
|---|---|
| UI-RESEARCH-HELPER-IGNORED | prototype.html：
anges-runtime.js 的 src 与内联 window.analysisRows / selectedResearchContracts 拆开 |
| UI-CHECKBOX-LOST | pro-table-runtime.js：勾选变更写入 selectedTokens 后再 renderMarket |
| UI-RESEARCH-PROMPT-CONFIRM | i-runtime.js：body/签名纳入 depth_template、user_focus、requested_search、prompt_version |
| 后端配套 | i_research.py：上述字段进入 prompt_hash/request；8767/8768 已重启 |
| UI-AI-CONFIG-HYDRATION | render 回填超时/重试/任务开关 |
| UI-REPORT-EMPTY-CONTENT | 空壳 HTML 视为正文缺失并提示 |

## 备份

untime/code-backups/frontend-p1-research-select-20261014T131751

## 未做
- 托盘固定定位 / 纵滚表头 / 命中未知同色（窄范围 P2）
- B 档审计阻塞项映射与复验
- 用户授权下的真实计费点验

## 请雪球
硬刷新后：勾选 → 托盘有数 → 只点投研预览（先别确认）；改提示词再确认应要求重新预览。
