# API Key 本机配置

统一文件：项目根目录 `.env`。在 `CLAWBY_API_KEY=` 后粘贴 Key 并保存即可，不要发到聊天。

- `AI_API_KEY`：现有 AI Key 已在本机迁入。页面保留/替换/清除 Key 同步作用于该文件，接口不回传明文。AI 服务商地址、模型与协议仍在设置页面管理，避免双份配置冲突。
- `SANTIMENT_API_KEY`：现有 Santiment 历史数据采集读取此项；套餐和覆盖限制仍然有效。
- Clawby、CryptoQuant、Glassnode 是待验证数据来源；填写 Key 不等于⑤已接入或全覆盖。
- `RESEARCH_RETRIEVAL_BASE_URL` / `RESEARCH_RETRIEVAL_API_KEY` 仅用于旧版独立 HTTP 检索适配器。当前原生模型联网检索无需填写。

`.env` 中已写出的变量优先于进程环境变量，空值代表明确留空；缺少变量时兼容原有环境变量。Key 按原文读取，不执行命令，也不展开 `$`。修改 Key 在下一次读取时生效；首次部署新加载代码后，既有后台服务需要重启才能使用新代码。

`.env` 为本机明文配置，已加入 `.gitignore`，服务静态白名单不提供下载，配置备份不包含它。勿发送整个项目目录或把它复制到审计附件；`.env.example` 仅含空模板。原 DPAPI 文件保留兼容，存在 `.env` 时不回退读取旧 AI Key，清空时会清除旧副本。
