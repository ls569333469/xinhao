# 扫描：CoinGecko 独立节流与限流冷却

日期：2026-10-15（Asia/Shanghai）。承接用户本次扫描遇到 CoinGecko 第5页HTTP429，按限制90%运行的要求。

## 原因与额度边界

原scanner.request对CoinGecko、CoinPaprika、Binance共用0.125秒间隔，不能满足CoinGecko较低速率限制。当前代码没有为CoinGecko配置认证Key。本次官方文档连接超时，不能确认账户/IP实际额度；5次/分钟只是保守本地规划基准，绝非已核实套餐额度。源码COINGECKO_LIMIT_PER_MINUTE可在取得明确额度后调整，利用率COINGECKO_UTILIZATION固定0.9。

## 实现

- CoinGecko独立进程级RequestPacer：5 × 90% = 4.5次/分钟，间隔约13.34秒。coins/list、所有markets分页和5xx等重试都计入，空闲不积累突发额度。
- 其他数据源保留原节流，CoinGecko冷却不占其速率槽。
- HTTP429/418不立即重试；解析Retry-After秒数或HTTP日期，至少冷却60秒，并把本进程后续CoinGecko速率减半。等待期间发生的冷却扩展须重新核对，不按过期预约放行。
- CoinGecko列表或分页被限流时结束本轮该源请求；保留已取得分页并用现有CoinPaprika回退，数据来源及缺失语义不变，不伪造满覆盖。
- 全量7次CoinGecko请求本身有约80秒节流跨度，扫描前段会比之前慢；这不是任务卡死。不修改用户已记录的进度区域布局问题。

## 验证与生效

项目外副本完成12项专门测试及26项任务/HTTP回归，共38项通过。模拟时间验证7请求跨度80秒、空闲不突发、Retry-After秒/日期/异常值、并发等待、429不重发、其他源隔离、分页保留和回退。无新增真实行情或AI调用。

代码推广/进程重载及快照保护结果见项目外同轮记录。未额外触发扫描，因此修改后实际CoinGecko是否再429尚未现网验证；共享IP、动态服务端额度、其他进程不受此进程限流器控制，不能保证完全避免429。自适应降速/冷却为进程内状态，不跨服务重启保留。

证据目录：`C:\Users\ls569\.codex\visualizations\2026\10\12\01a12f09-8e85-72b1-885d-668752d27af6\coingecko-pacing-20261015T052730Z`。
