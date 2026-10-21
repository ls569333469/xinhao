# 固定版本与服务器运行

当前默认保持原目录运行；`ABC_DATA_ROOT` 未设置时不移动数据。设置时必须指向已准备的持久目录，其 `runtime/data-root.json` 内容为 `{"schema":"abc-data-root-v1"}`。缺标记直接退出，避免换代码后连接空库。静态页面、规则注册表和模块从代码目录读取，行情、归档、SQLite、配置从数据目录读取。宿主除原端口占用保护外还持有 OS 文件锁；同一数据目录不能由两个新版本同时写入。

迁移只在采集任务空闲并停止旧宿主后进行：一致备份所有 runtime SQLite 以及 scan-history、runtime 的引用文件、scan.json、根目录 tracking-control.json 与动态 DOCS/report.md；复制到持久目录，完整对账1H每个键及历史版本载荷hash，补上标记。所有持久开关一并迁移，不用示例配置覆盖现有配置。Windows DPAPI 的 AI 密钥不能直接当作 Linux 可用凭据；另行配置且不启用 AI。不要只复制 SQLite 主文件而漏 WAL/外部证据。旧版本没有文件锁，首次迁移必须明确停止旧宿主。

Python 3.11+；项目运行依赖以标准库为主，服务器需可用 IANA 时区数据（Linux tzdata / Windows Python tzdata）。`python server.py` 仍只绑定127.0.0.1:8767/8768。通过 SSH 隧道访问8768；如配置反向代理，代理须提供认证/TLS并转发白名单 Host=127.0.0.1:8768，不开放未认证公网写接口。见 systemd 示例；首次保存前检查 scan、observe、collection、AI/模拟计划，安装不自动开启它们。

升级流程：代码基线 commit → 固定候选差异清单/manifest → 必要集中验证 → 空闲停宿主 → 数据保护快照 → 同manifest发布记录 → 安装源文件 → 重启 → GET health/settings/tasks/scan及ABC主表点验。`tracking.release` 保留原CLI，发布记录允许本项目授权的 Codex；必须精确匹配 manifest SHA，设置 authorization=user_authorized_1019_plan、checks_passed=true 并据实列允许范围。不得伪造外部独立复核。发布manifest还应包含 git_commit，并与加载身份对应。

回退只恢复兼容的代码，不覆盖运行中新数据库。schema不兼容必须先停写并处理兼容迁移，不能直接git reset。每次安装保留源码备份、数据库保护摘要、加载身份与恢复方法。Git远端由用户指定；本方案不创建公开仓库、不上传数据、凭据或备份。真实网络补采、长时间持续运行、远程迁移未执行时单列NOT_RUN。

案例 CLI：`python case_tool.py --data-root <持久目录> list` 分页查看；`review CASE_ID --expected <JSON> --basis <规则依据>` 固定人工核对的逐根预期；`replay CASE_ID` 在临时根运行原始生产器。未核对样本不能自动变为通过。steps按boundary_ms递增，as_of_ms明确当次证据可得时间；真实晚到不能倒填为历史当根可知。日常收集不外呼，容量满保留旧例并停止新增，不影响行情扫描。

`seed-reference` 可显式导入4个固定合成规则案例（A负例、B技术确认但资格缺失、C候选至T+1确认、C缺OI）；重复导入复用输入。它们标明 synthetic_reference，不能当作真实历史或收益案例。逐根预期可使用完整 result 或精确字段 assertions，未断言的等级/DTO字段不计为已验。真实日常样本仍先 pending_review，独立核对后进入回放集。
