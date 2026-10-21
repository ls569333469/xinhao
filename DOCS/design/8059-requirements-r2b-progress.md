# requirements 进度追加 · R2b（UTF-8 干净副本）

因 `requirements.md` 历史正文存在混合编码字节，本轮进度以二进制追加写入该文件末尾，并保留本干净副本。

### 进度 · 2026-10-14 · 投研 R2b（先检索后生成）
- 范围：REV-03 选定 **先检索后生成**；`research_retrieval.py` 适配器 + `ai_research.run` 先检索登记/冻结再生成输入；fixture 离线证明；live 可选环境变量启用。
- RES-07/08：timeout/404/partial/unimplemented/无新闻 → 真实状态与 gaps；失败不标 online-verified；勾选检索≠已检索。
- 证据：DOCS/design/iteration-research-r2b-evidence.md；备份见 reserve-history/r2b-retrieve-backup-*。
- 验证：`python -m unittest test_r2b_retrieve_then_generate test_ai_research -q`。
- 启用 live：`RESEARCH_RETRIEVAL_MODE=live` + `RESEARCH_RETRIEVAL_BASE_URL`（可选 API_KEY）；默认 fixture。
- R3 门禁：授权真实供应商调用 + 用户硬刷新点验仍待；未标 A 档完成；无付费 LLM；无经验库页；无 CON 套件。
