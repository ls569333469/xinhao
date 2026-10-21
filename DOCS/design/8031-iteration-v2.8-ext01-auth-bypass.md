# V2.8 EXT auth bypass 收口

日期：2026-10-14 00:05 Asia/Shanghai。状态：**部分实现**（**不标 EXT 完成**；不做 ATH/宇宙/多市场引擎；无「二手日历」；不改 Codex HTML）。

## 问题（审计）

EXT claim / sources / precise-flow 可能通过：

1. 省略 `expected_as_of` / `expected_口径` / `expected_content_hash` 跳过时间/口径/哈希核验  
2. 登记/授权 **未来 as_of**  
3. 请求标志 `skip_verification` / `force` / `bypass` / `ignore_hash` 等跳过核验  
4. 用 cex-reserves / tech_flow 等替代源登记精确⑤槽位或 claim 冒充  

却仍宣称精确⑤就绪。

## 收口行为

| 闸 | 行为 |
|---|---|
| bypass 标志 | `refuse_bypass_flags` → ExtConflict **409** |
| 未来 as_of | 登记/授权/claim echo → **409**（容差 300s） |
| claim 缺 echo | 须三字段与槽位核对；省略 → **409** |
| 替代源冒充 | 精确⑤槽位 url/口径/source_kind 含储备/技术流标记 → **409**；claim `source=cex-reserves` 等仍拒 |
| 异槽冒充 | events 等 `as_precise_5` → **409** |
| 市值回填旗 | historical_mcap `use_today_mcap` 等 → **409** |

Claim 仍须槽位 **authorized** + url/hash/as_of/口径 + operator_attestation（授权时）。

## 验证

`unittest test_ext_store`：**17**（含 `ExtAuthBypassHardeningTests` 10）

## 备份

`runtime/code-backups/ext-auth-bypass-*`

## 仍缺

- 精确⑤真源未预置；页面点验待 Codex HTML  
- 不标 EXT 完成  

## 下一刀建议

等 Codex HTML 做 V1 页面点验，或从「未完成需求」取下一审计正确性项。
