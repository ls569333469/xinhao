# iteration-v2.8 · EXT-01 精确流加深（来源槽位闸门）

- **时间**：2026-10-13 23:10 Asia/Shanghai
- **实现ID**：EXT-01（加深：precise-flow / source slots）
- **状态**：部分实现（**不标 EXT 完成**）
- **来源槽位**：`precise_indicator_5` · `events` · `unlock` · `supply` · `historical_mcap`
  - 字段：url / content_hash / as_of / 口径 / status
  - 默认 **unauthorized**；仅登记元数据 → **unverified**；完整授权 + operator_attestation → **authorized**
- **Claim**：仅当槽位 authorized 且时间/口径/哈希核验通过才接受；否则 409
- **硬拒绝**：cex-reserves / chain-flows-technical 等冒充精确⑤；USDC/COIN-M 引擎不可 claim 就绪
- **措辞**：禁用「二手日历」；解锁用「第三方解锁网站上的时间」或「未在官网上核对」
- **API**：
  - `GET /api/ext/sources` · `GET/PUT /api/ext/sources/{slot}` · `POST .../revoke`
  - `POST /api/ext/precise-flow/claim` · `POST /api/ext/claim`
- **验证**：`unittest test_ext_store`（7）
- **备份**：`runtime/code-backups/ext-precise-flow-*`
- **下一刀建议**：页面点验 / §11.8 复验收益，或 DATA 覆盖闸加深

---

**后续收口**（2026-10-14 00:05 Asia/Shanghai）：见 [`iteration-v2.8-ext01-auth-bypass.md`](8031-iteration-v2.8-ext01-auth-bypass.md) — claim 不可省略核验 / 未来 as_of / bypass 旗 / 替代源冒充 → 409。仍不标 EXT 完成。
