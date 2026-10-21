# iteration-v2.8-frontend-a-fix-0607

时间：2026-10-14 14:58（北京时间）

## 范围
按《V2.8 A档迭代需求：审计修复与01/02/03收口》阶段 S1 残留：A-FIX-06、A-FIX-07。

## 修改
- 文件：`design/pro-table-runtime.js`
- 备份：`runtime/code-backups/frontend-a-fix-0607-20261014T145817/`

### A-FIX-06
- `ensureSupplementalFields`：不新鲜（>36h / 未来 / freshAtRetrieval 假）或无储备记录时，清除 `reserveDayPct` / `reserveWeekPct` / `reserveStreakDays` 旧缓存。
- `reserveStreak`：删除绕过时效的 `consecutiveDecreaseDays` 回填；过期显示未知。

### A-FIX-07
- 新增 `histMeta()`：从顶层 `chainHistory` 读 `provider` / `windowFrom` / `windowTo`（不再从资产 record 猜）。
- 历史净流副文案：来源 · 起止区间 · 有效日数/缺口 · 累计口径 · 非当前⑤。

## 离线复验
- `node --check` 通过。
- 模拟：asOf 推进 48h → 日/周/连减均为 undefined；新鲜样本连减=5；旧缓存连减被清除。

## 状态
- 代码：已落地
- 离线：已过
- 运行/用户硬刷：待验
- 不宣称 01/02/03 A 档完成
