# retrieval-fixtures（R2b 离线夹具）

- **用途**：在无 live 检索 API/Key 时证明 retrieve→freeze→generate-input 路径。
- **不是** live 全文核验；`content_level=excerpt`，不得冒充已联网核验。
- **场景文件**：`ok.json` / `partial.json` / `empty_news.json`；另有代码内建 `timeout` / `http_404` / `unimplemented`。
- **启用 live**：见 `iteration-research-r2b-evidence.md`（`RESEARCH_RETRIEVAL_MODE=live` + `RESEARCH_RETRIEVAL_BASE_URL`）。
