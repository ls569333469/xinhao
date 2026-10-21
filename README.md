# 异动观察工作台

项目根目录：`D:\Axiangmu\异动观察工作台`。全部项目文档集中在 [DOCS文档索引](DOCS/INDEX.md)。

- [统一需求](DOCS/design/requirements.md)
- [独立审计原文](DOCS/独立审计报告.md)
- [核实与整改清单](DOCS/整改清单.md)
- [完整使用说明](DOCS/README.md)

双击 `start-pages.cmd`，或在项目根目录运行 `python -X utf8 start-pages.py`。初版：http://127.0.0.1:8767/；新版：http://127.0.0.1:8768/prototype.html 。首次扫描计划默认关闭；已开启并保存的计划在重启后恢复，过期最多补一次。不会调用AI。

当前维护入口见 [维护与清理说明](DOCS/维护与清理说明.md)。旧补丁、旧页面、审计源码副本和代码备份已外置归档；逐文件路径与SHA256见 [清理归档清单](cleanup-archive-manifest.json)。业务数据与历史报告保留。

V2.7：[区间行情交付](DOCS/design/iteration-v2.7.md)。新版“数据与任务→区间行情补查”可选合约/4h或日线/UTC区间，检查本地覆盖并提交手动补查。公开K线不消耗AI额度；每任务1–20份、最长366天。此段为V2.7交付摘要；后续模拟、前向观察及回测能力以当前需求、实际依赖检查和执行结果为准。
