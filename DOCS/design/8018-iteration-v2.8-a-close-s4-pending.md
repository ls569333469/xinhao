# iteration-v2.8-a-close-s4-pending

时间：2026-10-14 15:09（北京时间）

## 阶段位置
S1 残留 A-FIX-06/07 已落地并离线过；S2/S3 离线证据已写。**S4 待用户环境与授权**，本文件只列待验项，不勾完成。

## 运行进程核对（代码侧）
- `pro-table-runtime.js` mtime：2026-10-14 14:58:17
- 含 `histMeta`：True；含 `clearReserveCache`：True
- health：{"service": "anomaly-workbench", "version": "2.8", "settings": {"version": 3, "scan": {"enabled": false, "intervalHours": 4, "timezone": "Asia/Shanghai", "recoverMissed": true}, "next_due": null, "updated_at": "2026-10-13T10:18:47.807364+00:00", "scheduler_running": true, "scheduler_error": null, "capabilities": {"scan": true, "schedule": true, "ai": true, "ledger": false, "rangeBackfill": true, "settingsScope": ["scan", "ai"], "strategies": true, "sim": true, "eval": true, "review": true, "stru
- 说明：静态 JS 硬刷即可加载；Python 改动需确认 8767 进程已加载受验版本。

## 用户待验（硬刷 8768/prototype）
- [ ] 已选页签与列表一致（A-FIX-01 / A-01-04）
- [ ] 列配置 30 项、储备/历史净流副文案（来源·区间·非当前⑤）（A-01-03/08）
- [ ] 过期储备不显示连减假值（A-FIX-06）
- [ ] 纵滚表头固定、页签不裁切（A-01-07）
- [ ] 投研预览/确认 UI（A-02-03/04）
- [ ] 策略跨页绑定与浏览器导出导入（A-03-04/05）

## 另行授权后
- [ ] A-02-11 真实 AI 一次完整链路（未授权不执行）

## 证据索引
- S2：`iteration-v2.8-a-close-s2-evidence.md`
- S3：`iteration-v2.8-a-close-s3-evidence.md`
- 收口需求：`V2.8-A档迭代需求-审计修复与01-02-03收口.md`

**不宣称 01/02/03 或 A 档全部完成。**

## 用户点验记录 · 2026-10-14 17:36
- [x] A-01-04 已选勾1过（用户）
- [x] A-01-07 横滚/表头sticky/页签不裁 过（用户）
- [ ] 列裁剪后硬刷确认（改名+删列）
- [ ] A-02 投研预览/确认
- [ ] A-03 策略冻结/导出导入
- [ ] A-02-11 真AI（未授权）
