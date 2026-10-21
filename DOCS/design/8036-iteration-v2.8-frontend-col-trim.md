# 列裁剪（用户验收指定 apply-full）

时间：2026-10-14（北京时间）

## 改动
- `design/pro-table-runtime.js`；备份 `runtime/code-backups/frontend-col-trim-20261014T171039/`
- 删除显示列：大户账户比、大户持仓比、24h最高、24h最低
- 改名：币安余额日变 / 7日变 / 连降天数；历史净流样本（非当前⑤）
- 扩展列仍在「全部数据列」末尾，不进专业默认 11 列
- localStorage 键 `workbench-a-pro-columns-v3`，加载时剔除已删列
