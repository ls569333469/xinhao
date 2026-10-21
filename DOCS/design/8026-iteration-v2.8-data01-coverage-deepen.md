# iteration-v2.8 · DATA-01 覆盖闸加深（缺口/补查/指纹）

- **时间**：2026-10-13 23:15 Asia/Shanghai
- **实现ID**：DATA-01（加深：coverage gaps + path hard gates）
- **状态**：部分实现（**不标 DATA 完成**）
- **报告**：per-field / 样本 per-symbol 缺口 + `backfill_hint`；`coverage_fingerprint`
- **硬闸收紧**：
  - 空 `data_dependencies` 仍要求基线扫描覆盖（symbol/price）
  - `scan_and_research`：扫描后 expected/received + 基线闸，失败则停投研
  - SIM autofill：目标合约 warmup 覆盖（他币不能放行）
- **快照浏览**：`expected` / `received` / `expected_vs_received` / `coverage_fingerprint`
- **API**：既有 `/api/coverage` 增 gaps+fingerprint；新增 `GET /api/coverage/gaps`
- **不做**：全历史宇宙重建；不宣称 OI/市值/账户历史齐全；不用「二手日历」
- **验证**：`unittest test_coverage_gate`（11）
- **备份**：`runtime/code-backups/data-coverage-deepen-*`
- **下一刀建议**：页面点验 / §11.8 复验收益，或 RULE 导出指纹联调
