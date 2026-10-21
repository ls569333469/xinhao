# 异动观察工作台 · 统一需求文档





需求版本：V2.8修订（2026-10-13，待实施计划）；当前代码基线：V2.7。扫描任务、扫描设置、手动区间补查与覆盖预检已接入；API凭证/真实AI投研、策略执行与收益回测闭环待开发。本次仅更新文档，后续代码由GROK实施。本文统一此前讨论、数据审计和V2.0–V2.7交付事实；§11给出最新需求ID、依赖和验收，旧文件保留为历史记录，冲突时以本文为准。





项目根目录：`D:\Axiangmu\异动观察工作台`。代码、文档、存量数据、原型、历史证据和日志在此维护；所有运行/发布路径以脚本所在目录为基准。根目录 `start-pages.cmd` / `start-pages.py` 启动同一后台托管的两个本地页面，地址仍为8767与8768。首次调度默认关闭；若用户已保存开启的扫描计划，重启按计划恢复，过期最多补一次。启动不调用AI。





## 1. 目标与范围





把币安永续研究流程连成：全市场扫描 → 独立指标/策略筛选 → 选币投研 → 多策略草案 → 冻结规则 → 前向模拟/历史回测 → 定期复盘。





初始总资金为用户确认的1000 USDT，首版用于研究与模拟，不含实盘交易。全部活跃USDⓈ-M与COIN-M永续进入扫描目录，按合约计数；同币不同合约可分开观察，但链上流量与资产市值不重复相加。模拟引擎先支持USDT线性永续，USDC与COIN-M保留扫描、报告能力，待独立计价/盈亏引擎完成后开放模拟。





“AI驱动”的产品含义：AI用于证据解读、公开信息检索、报告、策略草拟与复盘；公开接口采集、指标运算、信号判定、撮合和统计由程序执行。不能把搜索摘要当成完整历史行情。无需每1–5分钟调用AI；默认每4小时更新并补查区间行情。





## 2. 当前实现基线





| 模块 | 当前实际能力 | 未完成部分 |


|---|---|---|


| 采集 | 网页/CLI扫描；持久任务/扫描计划；手动与定时去重、恢复只补一次；注册表分类；90%关键覆盖门槛；独立观测/不可变revision/字段可得时间；下次扫描归档实际4h/日线窗口；手动区间补查/独立归档/本地覆盖 | 全生命周期历史、扫描窗口统一索引、逐字段覆盖及快照浏览待开发；旧样本未实际补行情 |


| 初版8767 | GET /api/scan读取最新本地快照；筛选、可调阈值、排序、完整数据列、详情、CSV、模板报告 | 读取按钮不启动采集；真实扫描独立按钮；策略/AI写接口未接入 |


| 新版8768 | 七工作区；存量数据筛选/排序、跨页会话选币、指定币检索、数据报告、请求预览、提示词编辑、篮子资金预算及复盘配置预览 | 扫描任务/扫描设置/手动区间行情补查与预检为真实后台；AI、其他设置、策略冻结、模拟、回测仍未接入 |


| 六指标展示 | 命中/已知/未知项数、具体编号、六项数值和三态；范围内0–6项分布 | 不把命中数量换成胜率或做多概率 |


| 扩展证据 | 趋势/成交、大户/结算、储备、历史流、来源质量五组可选列；本地CSV75字段（含版本/可得时间/OI覆盖） | 非全币、非全字段覆盖，缺失继续标未知 |


| 辅助链上 | Santiment真实历史交易所流；DefiLlama近期局部储备数量 | 当前⑤精确净流仍未知；地址集合/网络/内部迁移口径未完全审计 |


| 策略 | 共用JSON注册表version/hash；两页/采集规则一致；T1–T8独立候选/指导，T8两层可算性；报告证据与导出带规则标识 | 策略编辑持久化/冻结版本、触发执行、账本与成绩 |


| 多策略篮子 | 默认T1/T2/T3预算40/30/30；可选模板、独立/共享模式、预算校验 | 尚未运行；会话草案不是已启用策略 |


| 设置 | APIKEY.FAN地址、协议、模型ID/Key输入设计；扫描与复盘时间配置预览 | 扫描计划已真实保存；没有保存Key、真实小请求、模型列表或检索能力确认 |





当前已有数据：588合约、525资产；市值518，EMA/量价587；⑤同期精确净流0覆盖。扫描2026-10-12北京时间20:03:26–20:07:37，另有补核版本enrichedAt=20:11:58。GTC市值源20:09:54晚于扫描结束，因此相关补核信息不得视为20:07之前已经可得。





observations.jsonl目前每合约一条，没有连续行情观测。曾拉取最多300根4hK线用于派生值，旧扫描完整K线没有归档；V2.5从下次CLI扫描开始归档实际返回窗口，并不补造旧数据。检查数字是当前历史样本的计数，不是实时扫描结果或收益回测。





## 3. 页面与统一交互





七工作区：市场扫描、投研报告、指标与策略、模拟篮子、验证与复盘、数据与任务、设置。页面内一处集中查询/配置区，一条主要任务操作栏；批量、导出、次要操作放在“更多”。“01–07”只作导航序号，不与分散的流程按钮混淆。





通用状态分别标明：已实现、流程预览、缺数据、未配置、运行中、部分成功、失败。所有页面显示所用快照/报告/策略版本；旧快照可重算候选，但不得标实时触发。区分证据来源时间、采集时间、补核时间和系统可得时间。





宽表在表格内横向滚动，合约列可固定；侧栏与筛选不能被撑出视口。保留键盘操作、清楚的焦点、抽屉关闭和空态。页面显示列选择本地保存；扫描计划/任务持久保存；选币/提示词/策略草案当前仅会话有效，正式版应明确恢复和持久化范围。





### 3.1 市场扫描





- 名称/合约、USDT/USDC/COIN-M、市值预设及自定义上下限、明确映射、六指标与组合、独立储备辅助条件集中设置。市值下限含、上限不含，单位明确为万美元。


- 三态规则：AND有明确false即false，全部true才true，其余unknown；OR有true即true，全部false才false，其余unknown；至少N项按确定true和可能true判断。无所选指标时不限制指标。


- 六卡片及命中分布按确定市场/市值/映射范围独立统计，不被其他六指标勾选改变；范围待核验另列。储备条件属于额外范围AND，不计入六项命中。


- 命中项数始终按全部原六项的确定命中统计；显示具体命中编号、已知数与未知数。组合通过不等于六项全通过。


- 默认显示合约/价格、市值、命中项数和六项完整数据列。一个“显示数据”入口控制五组扩展列；可一键全部展开/仅核心，不需四处分散开关。


- 支持命中数、市值、OI/市值、账户比、费率、量倍数、涨跌、成交额、名义OI、本根4h量、距EMA99、储备变化等排序；缺失始终排最后，同值固定二级顺序。


- CSV导出当前视图全部结果，保留排序、筛选与阈值、未显示扩展字段、三态、单位、来源时间及未知/过期状态，不只导出本页。


- 跨页/跨筛选选币保留，明确已选数量；区分选择本页、全部筛选结果与清空。合约详情可生成该币报告/加入观察池。


- 后续提供“指标组合筛选”和“按策略筛选”两种语义。组合预设不能冒充完整策略，按策略筛选必须与策略库同ID/版本/候选表达式。





### 3.2 投研报告





研究对象：市场勾选、当前筛选、指定代币搜索与批量输入、观察池、全市场快照。输入代码须精确匹配资产/合约，歧义显示可选项，不擅自选择同名项目。提交前展示币池、排序、数量、分析上限及未提交数量。





三档提示词保留默认模板及可查看/编辑能力：





| 深度 | 目标 | 内容 |


|---|---|---|


| 快速摘要 | 筛研究优先级 | 关键异动、位置、反证、缺口和下一步确认 |


| 标准投研 | 形成策略草案依据 | 六项事实与口径、结构/链上/事件、做多确认/失效、最多2策略建议 |


| 深度研究 | 核验多种假设 | 独立连续窗口、现货/主动买盘、箱体/ATH/供应事件、情景与最多3独立策略建议 |





共同证据规则只读；每档任务模板和用户关注点可编辑。每次调用冻结prompt_version/hash、实际模型、输入证据与输出schema。深度不等于资料完整，无法检索时明确降级。





数据报告和AI报告分别标记。AI报告要求事实/假设/反证/缺口、六项指标、趋势/箱体/ATH可得范围、吸筹假设、事件/供应、确认/失效、策略建议和资产共同风险；重要结论引用evidence_id，外部资料带URL、发布时间、检索时间。不得根据文字叙事编造支撑压力或历史收益。





不要求用户填写Token消耗预算、输入输出Token上限或单次计费限制；使用第三方节点/模型默认配置。保留候选上限、报告有效期、仅变化重研、调用记录、实际用量与网关错误。不同币池/证据/提示词变化应使旧预览和报告失效；复用时显示原时间和新证据差异。





### 3.3 指标、策略及候选判定





指标库可逐步扩至30项以上，但每项必须声明公式/单位/周期/预热/版本、来源、时间对齐、覆盖与unknown原因。可用项、部分可算和需补数据分开展示，不为凑数量填值。





六原始指标：





| ID | 默认规则 | 必须保留的口径 |


|---|---|---|


| I1 | 单合约名义OI / 流通市值 >30% | 非保证金/强平规模；同资产币安汇总另列；身份和乘数须核验 |


| I2 | 全体账户数多空比 <0.8 | 大户账户、大户持仓分栏，不互相替代 |


| I3 | 单期百分比×24/实际结算小时×365 <−50% | 简单年化；当前显示和已结算现金流分开，周期未知时标估计 |


| I4 | 蓄力：已收盘4h低于EMA7/25/99（非入场） | Stage1观察；横盘/RSI/7日新低为旁路字段；入场看I4b强/中；弱穿勿入场 |


| I5 | 聪明钱流入 | 币安标记聪明钱钱包4h净流入USD>0；按链与合约地址匹配；未上榜/歧义/来源失败保持unknown；独立于历史交易所净流 |


| I6 | 已结束24h量 / 此前7独立24h窗均量 >3，且同窗绝对价变 <2% | 窗口对齐不重叠；异常换手不等于净买入或吸筹 |





补充指标分层：量价趋势（EMA排列/上穿、ATR、RSI、MACD、ADX、波动/回撤）、主动成交（Taker买入、现货/合约、OBV/CMF/VWAP）、合约变化（OI/账户比变化、费率持续、基差）、箱体、ATH、独立窗口吸筹假设、流动性/公告/解锁/供应和数据质量。OBV/CMF等技术指标不得命名成精确链上资金流。





统一策略定义必须包含strategy_id/version、名称、候选AST、入场触发、候选有效期、失效/退出、资金/费用、所需字段及周期、未知和陈旧处理、固定选币排序、来源报告/证据、参数状态。只运行白名单规则，不执行AI生成代码。





各策略独立对同一币池计算；T4–T8不必先通过I1/I2/I3，避免漏掉结构型行情。单币结果展示每条条件的实际值/阈值/三态、数据缺口、版本和时间。





| 策略 | 基线候选 | 入场与退出方向 | 当前可计算性 |


|---|---|---|---|


| T1 空头拥挤突破 | ①②③ | 后续突破/回踩和量确认，结构/时间退出 | 候选可算；快照1合约GTC；不是已触发交易 |


| T2 均线受压后修复 | ①②④ | 后续收复EMA99与回踩，修复失败退出 | 候选可算；1合约ENSO，市值映射待核验 |


| T3 负费率量价突破 | ③⑥ | 固定短区间突破、主动买盘/现货确认 | 候选可算；0个明确命中，不能强行推荐 |


| T4 疑似吸筹后突破 | 独立连续量价/波动及现货/买盘证据 | 结构突破/回踩，保留放量下跌等反证 | 部分：⑥异常3个HANA/HMSTR/KGEN；持续性与突破结构不足 |


| T5 日线箱体突破 | 满足箱体宽度、触碰与驻留 | 日收盘固定上沿突破/后续回踩，回箱体失效 | 缺日线及箱体结构 |


| T6 接近ATH观察 | 距此前对应口径高点阈值＋趋势/量 | 真正突破前保持观察，无入场收益 | 缺可信ATH，阈值未验证 |


| T7 ATH突破/回踩 | 固定此前ATH及完整口径 | 后续突破/回踩、ATR/结构退出 | 缺ATH、价格序列和ATR |


| T8 趋势延续 | EMA7>25>99且4h收盘在三线上，量/ADX等可选 | 后续回踩恢复或前高突破，趋势/时间退出 | 简化趋势67；加本根量>1.5倍及24h上涨30；完整触发与退出不足 |





表中数字来自已有历史快照。小市值1000万（含）–5000万（不含）范围177合约，T8两层候选16/9，部分市值身份未核验。详见data-capability-audit.md/json。





T4持续性不能把重叠24h窗口重复算独立样本；局部储备下降只能作辅助，不等于买入。T5优先以触发前20个已收盘日线构造（20/30/60为待验证选项），不含突破判定当日，并固定宽度、触碰容差、驻留阈值和突破缓冲。T6/T7区分合约上市高点和代币全市场ATH；只有局部历史称区间高点，不能用24h高点替代，不能用未来ATH回填历史。





状态链：候选 → 等确认 → 触发 → 模拟持仓 → 退出/失效。数据不足独立标注；草案/冻结/启用/暂停是规则生命周期。候选与入场条件不得混在同一瞬间要求既“三线下方”又“上穿EMA99”。I4=蓄力观察；I4b=点火分级（强/中可观察入场，弱=勿入场）；旧「收盘突破线索」已降级。数据源不齐可以给观察线索，不能自动执行不完整规则。





### 3.4 模拟篮子





共享1000U篮子与每策略独立1000U对照分开，独立账户曲线不相加当1000U收益。可同时启用多个已冻结策略，数量由配置和后台限制；原型1–8模板选择只是界面上限，不是已经具备8策略运行能力。





> SIM-01 状态（2026-10-13）：纸面共享/独立账户与事件账本已部分实现；同币冲突策略为 reject；EVAL-01 前向结算首刀已落地（见 iteration-v2.8-eval01.md）。研究/模拟，非实盘。








默认建议：T1/T2/T3预算400/300/300U（保证金预算上限），最多3仓、最高2倍杠杆、单笔计划止损风险0.3%、整篮子开放止损风险1%。除了1000U总本金外，其余是待验证可编辑参数，不保证最大实际损失。





同币共享篮子按冻结优先级只取一份仓位；独立实验可各自持仓。无合格触发可以空仓，不为用完预算开仓。仓位同时受风险预算、保证金、最小名义、精度和并发约束；费用、资金结算与权益可核对。设置中的账户参数、篮子和实验必须引用同一配置版本，当前原型尚未绑定账户持久配置；篮子模板名称/版本来自注册表。





记录候选/触发/拒单/成交/持仓/成本/资金结算/退出/缺口事件，追加账本，可重启去重。明确“暂停新入场”不停止原仓跟踪，观察期到期停止新入场但按冻结规则处理未平仓。





### 3.5 验证与复盘





历史回测、前向模拟、记录回放独立入口和成绩。历史模式指定区间/预热/当时币池/历史市值口径；前向模式从启用或未来指定时间记录；回放重现已有规则与账本，修改规则新建实验。





前向观察7/30/90天或1–3650自定义天数，区分观察期和单笔持仓期限。账本更新默认每4小时，可8/24小时，不调用AI；AI复盘手动、每日时刻、每周星期/时刻、每1–720小时，默认每日21:00北京时间、重要变化触发。周汇总与每日复盘重合只一次；所有开关须显示实际服务端是否生效。





每次更新批量补查前次至今区间K线及实际结算。旧规则期间触价事件区分event_at（发生）与observed_at（发现）；本轮生成规则不能回填此前盈利入场。收盘确认后用下一可执行价格；跳空按可执行价。区间内止盈止损同根先细化数据，仍无法排序默认止损优先并标歧义；断档保持未知，不用末价假装成交。





历史①②③⑤、市值/币池必须当时可得；没有来源不以今日值回填。实际资金费用按持仓方向、名义价值、结算时刻计。手续费/滑点按单边计；0.05%只是初值，清算模型不完整时明确范围。





成绩展示净收益、权益/回撤、已平仓胜率及分母、盈亏比/期望值、持仓时长、成本、资金结算、歧义/排除/缺口、基准和版本。无样本为“暂无”，不是0%胜率；未平仓不入胜率。比较使用相同币池/区间/资金/费用/覆盖，训练/验证/测试分离，参数探索不能伪装独立测试结果。





### 3.6 数据与任务





observations按scan_id隔离，记录合约键、observed_at和证据版本；同一次扫描内重试应幂等，不按全局JSONL累计行数计算覆盖。多次扫描的同币观测是合法历史，缺scan_id的混合追加需迁移标记。





任务状态排队/运行/成功/部分成功/失败/取消，记录阶段/进度/时间/源错误/关联输出。手动与定时重合复用在途扫描；币池目录失败不得发布空快照；部分源失败保留unknown，显示覆盖下降。成功快照原子发布且不可变归档，失败保留上次成功。





数据覆盖按字段×合约×区间呈现：缺失、过期、未核验、权限受限分开。保留原始响应或可追踪摘要、标准化字段与版本；K线、费率、报告、策略和账本分别归档。保护实验依赖数据，清理普通缓存不删未结/已完成实验所依赖记录。





现有链上两种数据单列：





- Santiment：10资产、70资产日、21合约关联；2026-09-05至09-12（UTC，结束不含）。供应商交易所汇总，非只币安，近期权限受限；历史正净流为流入、负为流出，原生数量与供应商USD分别保留。


- DefiLlama：18核验原生链资产、40合约关联，8日余额形成7日变化窗；边界2026-10-13北京时间08:00。币安PoR/挂钩代币backing钱包的局部数量变化；36小时过期检查，不是逐笔精确净流。缺币/断档不填0，多合约不重复计算数量。





### 3.7 设置与AI API





六设置页：更新与调度、AI API、检索与数据源、报告复用、模拟与成本、存储与展示。复盘时间只在验证与复盘维护，避免两处配置冲突。扫描设置已显示真实配置版本、生效/失败；其他设置按钮仍是预览，不共用扫描保存接口。





默认供应商APIKEY.FAN，候选Base URL https://api.apikey.fan/v1，候选协议Chat Completions；模型ID由服务商真实列表或用户准确填写。未提供真实Key，尚未验证实际模型、结构化输出与检索，不把URL可达或401当接入成功。





协议显式标明Chat Completions/Responses/Messages的实际支持状态；首轮先实现经测试的一种，其余禁用并标未实现。Base URL只含一次/v1，保存/测试分开。后端小请求分别验证鉴权、准确模型ID、结构化输出、超时与错误；检索作为独立能力确认。自动降级/备用配置只能用用户显式配置的目标，页面展示实际模型。





Key仅通过本地后端受保护存储/Windows DPAPI等保存，页面只回配置ID和是否存在；不回显、不进浏览器持久存储/URL/日志/CSV。保留/替换/清除明确，空输入不删旧Key，不读取其他项目凭证。公开数据不需交易所交易Key。第三方网关限流/额度不足只暂停AI，采集与账本继续。





时间内部存UTC，显示/调度时区显式记录，首版页面用北京时间。服务必须运行、联网且未休眠；恢复后只补一次过期任务/区间缺口，不堆积全部错过扫描。所有真实任务默认关闭。





## 4. 后台数据与接口要求





核心实体：scan/snapshot_revision、observations、candles、asset_mappings、indicator_versions、strategy_versions、reports、experiments、accounts、events/positions、funding_cashflows、tasks/schedules、model_profiles、credentials、usage/source_metadata。





字段至少保留source_time、retrieved_at、available_at、source_id、数据/算法版本、单位、资产/合约乘数、质量与未知原因。快照补核创建revision并保留原版本，available_at不能早于实际补核完成。事件/报告/实验引用固定数据、规则、引擎、成本版本或hash。





推荐SQLite保存元数据/账本，行情与原始证据按时间分区；写入事务/原子替换、重启恢复、任务去重和事件幂等。公开REST存在不同保留期限与权限，先确认来源覆盖再开放完整策略验证。





| 设计端点 | 用途 | 当前状态 |


|---|---|---|


| GET /api/scan | 最新保存的成功行情快照 | 两页同一后台已实现 |


| POST /api/scans、GET /api/tasks、GET /api/tasks/:id | 新扫描、最近50任务与状态 | 已实现；仅排队任务可POST /api/tasks/:id/cancel |


| GET/PUT /api/settings | 版本化扫描计划 | 已实现，仅scan范围；凭证与其他设置未实现 |


| GET /api/health | 本地服务/调度状态与能力 | 已实现，不表示上游行情源可用 |


| POST /api/candles/preflight、/api/candles/backfills、GET /api/candles/history | 本地价格覆盖预检、区间补查及最近50归档 | V2.7已实现，手动、每任务1–20份/最长366天；非账本自动补查 |


| POST /api/ai/test、模型列表/检索能力接口 | 真实小请求和能力验证 | 未实现 |


| POST /api/reports | 固定证据/提示词的报告任务 | 未实现 |


| 策略注册表查询、草案、保存版本/冻结接口 | 市场、报告、模板、篮子共用规则 | 未实现 |


| POST /api/experiments/preflight、/api/experiments | 覆盖预检与实验 | 未实现 |


| 账户/事件/成绩/覆盖/调用记录查询 | 可审计模拟与投研记录 | 未实现 |





继续本机绑定；新增写接口须同源保护、配置目标校验及凭证目录隔离。开发预览服务只提供原型，不作为放置真实凭证或持久业务数据的目录。模型和外部网页内容是证据，不能覆盖系统规则；返回HTML清理、策略运算白名单。





## 5. 迭代顺序





| 阶段 | 交付 | 完成条件 |


|---|---|---|


| R0 原型一致性 | 共用策略/指标定义，T1–T8各自指导/可计算性，文档与按钮状态一致 | 选择任一策略后名称、规则、依赖、候选计数和草案一致；未知/预览不冒充可运行 |


| R1 数据与更新 | 手动扫描、4h调度、目录/源质量门槛、不可变快照revision、完整4h/日线及区间行情归档 | 并发去重、失败不覆盖成功、原始值可重算、恢复补查、补核不回填可得时间 |


| R2 策略与前向账本 | 冻结规则、USDT共享篮子/独立对照、撮合/费用/结算、时间计划、复盘数据报告 | 从启用后记录，事件幂等，资金守恒，区间歧义/同币/预算/断档处理可重放 |


| R3 AI投研闭环 | 第三方模型配置、检索与来源、报告缓存、报告→多个白名单策略草案、AI复盘 | 真实鉴权/模型/schema/检索分别验证；凭证不泄露；失败/限流不影响数值记录 |


| R4 历史与结构策略 | 日线箱体、可信ATH、ATR/ADX/主动成交、量价历史回测、逐策略覆盖扩展 | 当时数据与币池完整度明确，候选/触发分开，参数测试独立，不回填当前指标 |


| R5 进一步扩展 | 可靠精确净流、事件/供应/历史市值、更多指标、USDC/COIN-M引擎 | 来源/单位/时间/标签审计通过，再启用对应规则 |





R1归档应尽早补日线/全历史价格，以支持箱体和ATH；R3供应商适配可与R1/R2并行，但不因等待AI阻塞记录。阶段不承诺未经评估工期，不自动开启定时/模型调用或实盘。





## 6. 核心验收清单





1. 六独立统计、AND/OR/N、未知、范围边界、映射、排序/导出一致；清楚区分范围分布与组合结果。


2. 共用注册表确保同一策略版本在市场/模板/报告/篮子/实验里的规则完全一致。箱体/ATH独立扫描不被前三指标预过滤。


3. T8简化候选允许已有EMA重算，完整回踩/退出缺数据不能启用；T5/T7无历史不能用24h高点替代。


4. 新采集按钮真采集、读取按钮只读；同时触发不重复；部分失败/空目录不静默覆盖成功数据；重启保留计划并只补一次。


5. 补核revision与来源可得时间正确；今天的市值/ATH不能回填历史。本轮新策略不能回填过去成交。


6. 指定币歧义处理、跨页选币、提示词版本、输入预览和报告对象一致；数据报告与AI调用状态区分，缓存不会换新时间冒充重研。


7. Token限额不要求手工填写；网关错误展示真实原因；密钥不回显/导出/记录在日志，能力未经测试不标成功。


8. 多策略共享总资金与独立对照不混合；同币优先级/仓位风险/保证金/费用/资金结算可核账；无信号可空仓。


9. 固定规则止损/止盈同根、跳空、断档、资金不足、重复事件/结算、恢复/暂停均有必要的确定性验证。


10. 前向观察期与持仓期限分开，复盘时间范围校验；历史/回放不等待真实观察天数；无样本不报胜率。


11. 相同数据/规则/成本/引擎版本可复现；回测给生存者偏差、历史不足、歧义与排除数量，不把筛选检查当收益验证。


12. 宽表仅内部滚动，七工作区及六设置页可切换；操作和状态对得上，文档有唯一当前入口。





## 7. 文档与代码索引





项目文档统一存放在DOCS；代码和数据仍在项目根目录与design。独立审计原文见 [独立审计报告](../独立审计报告.md)，核实与依赖顺序见 [整改清单](../整改清单.md)。





- 本文：统一当前需求与验收；后续功能变更先更新对应需求与实际状态。


- [GROK实施交接](../GROK实施交接.md)：V2.8待实现需求、阶段拆分、代码地图与验收边界。


- [V2.7交付](iteration-v2.7.md)：当前区间行情补查与覆盖预检。


- [V2.6交付](iteration-v2.6.md)：扫描任务/计划与验证。


- [V2.5交付](iteration-v2.5.md)：上一轮注册表及扫描契约。


- design-code-review.md：整体检查、已验证项、尚未修复问题及优先级。


- iteration-v2.md、iteration-v2.1.md至iteration-v2.4.md：历史决策及阶段交付，不作为孤立最新版。


- ../implementation-plan.md：早期方案保留，最新需求与工程顺序转到本文。


- ../data-capability-audit.md、../../data-capability-audit.json、../strategy-test-report.md：存量数据可计算性与候选校验。


- ../../design/prototype.html：新版交互；../../index.html与../../server.py：双页面与扫描任务统一服务。


- ../../design/market-evidence.js：共享证据展示；../../design/evidence-runtime.js：新版接入；../../design/integrate-evidence.py：发布完整存量证据。


- ../../scanner.py、../../chain_flow.py、../../cex_reserves.py：独立采集脚本；../../start-pages.py：恢复本地服务与已保存扫描计划；首次默认关闭，不调用AI。





当前检查结果可以支持继续后台迭代，不能据此宣称整套投研、模拟或回测系统已经完成。





## 8. 审计整改代码迭代 · V2.5





V2.5阶段交付范围：R0共用策略注册表和独立模板指导，R1采集/发布契约，以及新扫描的4h/日线返回窗口归档。当时任务后台未接入；当前扫描任务/计划见§9。账本、ATH来源核验、历史收益引擎与AI配置仍是后续依赖。





### 8.1 单一规则源





项目根目录strategy-registry.json为六指标/T1–T8声明式规则源，包含version；SHA256为实际规则内容标识。Python采集分类与浏览器模板/组合/指导使用同一注册表，浏览器文件由发布脚本重新生成，不单独维护一套规则。





T1①②③、T2①②④、T3③⑥；T4只提供⑥单次线索，完整候选unknown；T5/T6/T7缺完整结构定义时unknown；T8分别展示简化趋势候选与加量候选。每策略独立计算全币池，不用前三指标预过滤。任何候选都不是成交信号，页面尚不能冻结/启用或模拟。





每模板指导必须带自己的ID/version/hash、候选计数、触发/退出、风险与缺口；T4–T8不再跳前三推荐。报告请求证据和数据导出应保留注册表版本/哈希，防止文案与规则漂移。





### 8.2 扫描发布契约





- scan-history/<scan_id>保存每扫描状态、幂等合约观测、K线返回窗口与不可变revision。每扫描合约键为market|symbol，重试同键覆盖单次观测，不混合不同扫描。


- 顶层observations.jsonl作为最新已发布扫描兼容视图；历史观测在独立scan目录，不继续无ID全局追加。现有历史样本不追溯填造scan_id/可得时间。


- 发布默认门槛：返回合约数、名义OI、全体账户比、已收盘4h覆盖分别≥目录数90%。满覆盖为success，过门槛但未满为partial_success；市值/精确净流缺失不填0。不允许空目录、重复合约或收到数超过应查数。


- 目录请求异常、空目录、逐合约/核心字段覆盖下降不得覆盖上次成功scan.json；失败保存扫描状态和已获取证据。finally复位进度，跨进程锁拒绝重复扫描；孤立锁不自动抢占，必须核实所属进程。


- 发布前归档原有最新快照字节；新revision使用独占写入并原子发布最新文件。补核生成新revision，不修改旧版；采样截止、来源时间与字段available_at分别记录，不把补核提前到原扫描时刻。


- 数值拒绝NaN/Infinity；同资产OI带expected/received/known/complete，部分和单列，全缺/不完整不能作为确定完整OI。


- fundingInfo成功且无调整配置才按供应商标准8h默认；请求失败或缺有效配置时周期/年化unknown。源默认/显式配置标识分开，结算历史只作独立证据。





### 8.3 行情窗口归档与边界





从下一次手动CLI扫描开始，归档实际返回的已收盘4h（最多300根）与日线（最多1000根）原始数组、来源、区间、retrieved_at/available_at及覆盖。4h窗口断档不继续计算EMA/量窗；日线失败保留错误，不能冒充已取得。





这不是全历史下载，不是全市场ATH核验；当前旧快照仍无完整K线，不因代码新增归档能力改成已覆盖。箱体/ATH具体参数与入场退出、历史OI/账户比/费率/市值的覆盖仍须独立预检。





### 8.4 验收





使用离线模拟源与临时目录验证：目录失败/空目录、低覆盖、缺关键字段、重复观测、两次扫描隔离、不可变补核与可得时间、非有限数、部分/全缺资产OI、扫描锁及异常复位、报告路径/新K线归档。已有真实快照重算T1/T2/T3/T8为1/1/0/67，加量T8为30；不能换成收益结论。不为验收自动启动全市场采集或模型调用。





## 9. 数据与更新迭代 · V2.6





### 9.1 实际交付与存储





同一个server.py进程绑定8767/8768，两个页面访问各自同源/api，使用同一个TaskService。runtime/workbench.sqlite3保存任务、扫描设置版本、下一次到期UTC时间。网页与CLI仍共用ScanLease和ScanArchive；CLI在途时网页扫描显示锁失败，绝不抢占孤立锁。扫描返回success/partial_success才登记发布输出；失败保留旧快照。两端口绑定成功后才初始化恢复，防止重复启动改写在途任务。





扫描按钮POST /api/scans返回202与task_id；同时手动/定时请求复用queued/running任务。任务阶段、进度、错误和scan_id/revision可查。仅排队可取消，运行不强杀；关闭计划不取消在途扫描。页面启动/读取快照/查询任务不会创建新扫描。扫描完成后页面读取最新快照并重算候选，旧报告/请求预览失效；仍须区分已保存数据与实时行情。





### 9.2 扫描计划与恢复语义





首次enabled=false，间隔默认4小时；支持1/4/8/24小时。保存使用version乐观并发校验，旧页面返回409不覆盖新配置。开启或修改间隔从保存时起计算下一次到期，不立即扫描。内部按UTC经过时长调度，timezone为Asia/Shanghai、America/Los_Angeles、UTC之一供显示，不是每日墙上时钟或AI复盘计划。





计划到期按当前时间重新锚定下一间隔，不积压所有错过次数。重启将中断任务标失败，保留已发布文件；recoverMissed=true时过期计划只补一次，false时跳过该次。设备休眠恢复也只处理一次已过期扫描。没有自动清孤立scan.lock；应先核实其进程和归档。计划在服务未启动、断网或休眠时不能按时执行。





### 9.3 页面与接口边界





新版顶部集中真实扫描和读取按钮，数据与任务显示服务端任务表及网页扫描历史；更新与调度页独立保存扫描设置。无效配置、断连、任务失败、配置冲突显示真实原因。任务查询只访问本地后端，不调用AI。原型其余设置仍显式预览；持仓区间补查开关禁用，不能把重启补扫当成账本行情补查。





HTTP仅绑定127.0.0.1，严格Host校验；POST/PUT要求Origin与本页同源、JSON及大小限制，无CORS放行。两个页面改为明确静态白名单，HEAD同样不能下载runtime、源码、凭证或审计压缩包；不再托管整个design目录。当前尚未保存任何AI凭证，这不等于DPAPI与供应商保护已完成。





### 9.4 V2.6时的剩余依赖与验收





本节保留V2.6阶段记录；区间手动补查的后续交付见§10。





R1仍缺完整历史/区间补查、动态逐字段覆盖和历史快照浏览；R2缺账户/策略持久配置与冻结、模拟账本及资金结算；R3缺凭证保护/模型/检索/报告；R4/R5缺完整箱体/ATH/回测/精确净流等。后台扫描任务接通不代表整个R1/R2完成。





本轮用临时SQLite、可控时钟及模拟源验证并发、计划保存/冲突、失败、取消、重启/补扫和HTTP同源/静态隔离；既有扫描与策略回归继续通过。交付验收不自动启动真实全市场采集、定时计划或AI请求，旧行情样本与独立审计原文校验不变。





## 10. 区间行情迭代 · V2.7





### 10.1 范围与页面





数据与任务新增“区间行情补查”。独立选择当前快照内1–20份合约，可使用市场已选；4h或日线，最长366天。UTC开始含、结束不含，只纳入完全包含的已收盘UTC对齐K线，显示有效边界。先本地覆盖预检，再明确提交联网任务；不调用AI、不需要交易Key。支持USDⓈ-M/COIN-M价格证据，不等于两类模拟引擎均可运行。





### 10.2 持久与可得时间





固定公开币安klines端点按最多500根、最长200天分页；空页继续，源错误/限流停止后续请求。OHLC、有限值、时间边界和重复校验；相同重复去重，冲突排除。缺失按应有UTC槽位统计，上市前/断档不填0，不自动认定全历史完整。





candle-history/range-<id>独立保存冻结请求及输入快照SHA256/版本、原始分页响应、已校验合约数组、来源/源时间/retrieved_at/available_at及SHA256，最终manifest只创建一次。再次补查新建归档，不覆盖旧序列或scan.json。补查可得时间是实际归档时间，禁止历史回填。





### 10.3 任务、覆盖与限制





扫描/补查串行共享工作队列与CLI扫描锁；同参数同输入快照在途补查复用；不同请求排队，只有queued可取消。SQLite增量增加request/request_hash，保持原计划配置。中断标失败、保留证据，不自动重跑区间任务；持仓自动补查/资金结算仍禁用，没有新增自动计划。





预检仅合并区间补查归档，校验文件SHA256，重叠按开盘时间去重、矛盾排除、损坏不计覆盖，显示应有/取得/缺失、连续缺口与证据可得时间。旧扫描窗口暂未纳入此索引；不把局部价格覆盖等同于六历史指标、T5箱体、可信ATH或收益回测。success全部齐且无错误、partial_success取得部分但有缺口/排除、failed无可用行情；归档结果与任务输出关联。





详见[V2.7交付](iteration-v2.7.md)。R1完整历史/逐字段覆盖/快照浏览、R2账户与策略冻结/账本结算、R3凭证与AI、R4结构与真实收益仍待开发。本轮验证只使用离线模拟源与本地HTTP，未对旧真实样本补查，也未开启定时或模型调用。





## 11. V2.8需求修订与GROK实施清单 · 2026-10-13





本节为V2.8需求与实施对照。§8–10保留阶段交付事实；AI/DATA/RULE/SIM 已有部分代码交付，详见各 iteration-v2.8-*.md；未标注部分实现/未开始的条目仍待实施。实施入口见[GROK实施交接](../GROK实施交接.md)。本文中的“默认”是建议初值，必须经过用户保存/启用后才生效；填Key、打开页面或升级不能自行产生AI请求。





### 11.1 优先级、依赖与交付边界

当前v3.2 r2执行状态（2026-10-16 UTC）：Codex接手完成首批P0～P4源码与离线/隔离页面验证，正式发布与加载证据见[接手交付说明](strategies/v3.2-Codex接手交付.md)及项目外deployment.json。A仍4H；B独立1H；小时计划迁移保留关闭。P5连续真实运行、P6 C及辅助、P7模拟分别验收，不混称全部完成。





| 需求ID | 工作及最小可交付结果 | 依赖 | 验收条件 |


|---|---|---|---|


| AI-01 | 真实保存/读取脱敏配置、保留/替换/清除Key、显式模型连接测试 | 现有同源写保护；凭证隔离 | 重启可恢复；GET不含Key；空输入保留；401/403/429/超时/模型不存在分别展示；失败不得标已接通 |


| AI-02 | 模型ID、结构化输出与联网检索独立能力测试 | AI-01 | 每项显示成功/失败/不支持/未验证及测试时间；模型自称联网不算检索成功 |


| AI-03 | 三档投研、保存快照投研、扫描并投研、证据引用、报告任务/缓存/历史 | AI-01；需要外部研究时依赖AI-02检索通过 | 币池、快照revision、证据、模板/模型版本冻结；真实调用和本地数据报告区分；扫描失败不偷用旧数据 |


| DATA-01 | 合并扫描窗口与区间补查的只读价格索引、字段覆盖、快照revision浏览 | V2.7归档 | 哈希校验、冲突排除、逐周期连续性及预热；能区分当前可得与历史当时可得 · **状态：部分实现**（**多字段覆盖+缺口补查提示+指纹加深已接**；路径硬闸收紧；RULE 硬闸已接；六指标/ATH未齐；不宣称OI/市值/账户历史齐全；DATA 未标完成） |


| RULE-01 | 策略草案持久化、冻结不可变版本、白名单触发/退出、按策略筛选 | 共用注册表；DATA-01覆盖检查 | 市场/报告/篮子/实验引用同ID/version/hash；缺依赖禁止启用；修改冻结规则创建新版 · **状态：部分实现**（草案+冻结+覆盖硬闸+跨页绑定+市场只读指纹；**导出/冻结/绑定联合指纹 v2（strategy hash + coverage FP）已联调**；导入/启用陈旧指纹→409；观察层六阈值尚未镜像冻结规则文本；**RULE 未标完成**） |


| SIM-01 | 1000U共享账户与独立对照、仓位与成本配置、事件账本 | RULE-01、DATA-01 | 同币冲突、预算、资金守恒、重复事件/结算、重启恢复可验证；无触发可空仓 · **状态：部分实现**（纸面账本+autofill；**T1/T2/T3 保证金预算硬闸已接**；全自动撮合/最小名义未齐） |


| EVAL-01 | 前向观察、持仓区间补查、撮合结算、成绩与记录回放 | SIM-01 | 观察期与持仓期限分离；止盈止损同根/跳空/断档有固定规则；无样本不填胜率 · **状态：部分实现（2026-10-13：首刀+计划调度；auto_enqueue_backfill 默认关、显式 true 入队 candle_range；F02 保持；全市场自动开观察与补查后自动再 settle 未齐）** |


| REVIEW-01 | 数据复盘、手动/每日/每周AI复盘及持久计划 | EVAL-01；AI复盘依赖AI-03 | 数值更新不调用AI；重合复盘去重；暂停新入场不停止原仓跟踪；显示服务端生效状态 · **状态：部分实现（2026-10-13：计划默认关+调度绑定数值复盘；use_ai 门禁默认不付费；付费叙述未齐）** |


| STRUCT-01 | ATR/ADX/主动成交等指标、持续吸筹证据、日线箱体、区间高点/可信ATH | DATA-01及对应来源；全历史高点另需来源核验 | 每项公式/周期/预热/覆盖/版本明确；不满足全历史条件只能标区间高点；规则参数显式冻结 · **状态：部分实现（指标首刀+**回测/对照过滤闸**；可信ATH/全历史未齐）** |


| BACKTEST-01 | 按策略覆盖开放历史回测、训练/验证/测试及公平比较 | SIM-01、EVAL-01；结构策略另需STRUCT-01 | 无未来数据/当今市值回填；报告历史币池/退市覆盖、生存者偏差、费用/歧义/排除数量 · **状态：部分实现（对照批次+**STRUCT过滤闸**；F01/F04；历史宇宙重建未齐）** |


| CONFIG-01 | 观察池/选币/提示词/草案/账户成本持久配置与备份恢复 | 对应实体及版本化存储 | 刷新/重启恢复；旧配置冲突拒绝覆盖；普通缓存清理不破坏实验依赖；导出默认排除凭证 · **状态：部分实现（2026-10-13 首刀：观察池/提示词/无凭证导出；全量备份向导 + 设置页双向绑定（池/提示词/扫描/SIM预算/EVAL·REVIEW开关）；CONFIG 仍部分实现未标完成）** |


| EXT-01 | 精确⑤、事件/解锁/供应/历史市值、更多市场模拟引擎 | 来源授权、口径/时间核验 | 不用局部储备或技术资金流代替精确⑤；USDC/COIN-M未实现计价前只扫描/投研 · **状态：部分实现（2026-10-13 目录+拒冒充 + 来源槽位授权闸门；真源未预置、多市场计价引擎仍未齐，EXT 未标完成）** |





下一交付说明：AI-01→AI-03 与 DATA/RULE/SIM/EVAL/REVIEW/STRUCT/BACKTEST/CONFIG/EXT **首刀均已落地（部分实现）**；2026-10-13 晚间审查 F01–F09 可信度修复已记入文末日志。接下来优先复验收益与页面点验（含 §11.8），再按缺口加深自动调度/补查联动/多币回测等，不要把部分实现写成已验收。





### 11.2 API配置与能力验证





设置页集中为供应商配置、连接与能力、检索来源三组；默认展示APIKEY.FAN及候选Base URL `https://api.apikey.fan/v1`。协议先实现经过测试的一种（优先Chat Completions），其他选项标未实现并禁用，不用空壳下拉暗示兼容。模型从服务商真实列表选择或准确填写；列表接口不可用时允许手填并测试，不伪造模型目录。地址归一化，禁止重复拼接`/v1`。





“保存配置”只保存；“测试模型连接”和“验证检索能力”明确告知将发送小请求并可能产生费用。Key输入提供保留/替换/清除，空值默认保留旧Key；只显示是否已配置及配置ID，不返回Key片段。Key由后端Windows DPAPI等受保护方式保存，凭证文件与数据库/静态目录隔离；不得进入localStorage、URL、日志、错误回显、提示词、报告、CSV、普通备份。不得读取其他项目的Key。保存失败必须保留上次有效配置，配置带版本并处理并发冲突。





测试至少分别记录：鉴权、精确模型ID、结构化输出/schema、检索来源/引用。一次普通对话成功只证明该请求可用，不能代替schema或检索验收。供应商不支持JSON schema时，明确标为文本生成加本地结构校验；无效结果不得当成有效报告或策略草案。





联网检索必须有实际检索适配器/工具结果及可核验来源，记录URL、来源身份、发布时间（没有则未知）、retrieved_at、evidence_id。若网关只有文本推理，显示“不支持联网检索”；可以在用户显式配置独立检索源后使用该源，不能静默换供应商、模型或伪造URL。网页内容只作证据，清理HTML并防止其覆盖系统约束。





外部AI目标限制为显式配置的HTTPS供应商，校验URL/重定向目标，不允许任意内网或本机服务成为凭证发送目标。设超时、有界重试/退避，429服从限流信息；输出脱敏错误。网关不可用只使AI任务失败/暂停，不改行情数值，不阻塞数值账本。真实供应商尚未验收前，能力保持“未验证”。





### 11.3 手动扫描、投研与自动更新的完整流程





集中操作栏保留三种不同动作：





1. **开始全市场扫描**：仅采集公开行情、发布快照和重算候选，无需AI Key。


2. **使用当前快照投研**：用户选择已保存revision及市场已选/筛选结果/指定代码/观察池/全市场币池，不重新采行情；预览时显示快照时间、陈旧/缺口与待核验资产。


3. **扫描并投研**：冻结扫描参数、币池选择规则和分析深度，创建关联任务；新扫描成功或明确允许的部分成功后，绑定该输出revision再生成报告。扫描失败停止后续AI，保留失败原因；不自动转用旧快照。





投研选择区集中放币池来源、搜索/批量输入、分析深度、排序和候选上限。建议默认上限20，可编辑并受后端上限约束；展示总范围/提交研究/未提交数量以及未提交原因。全市场扫描588份但AI研究20份时，分别显示行情覆盖588、AI投研20，不能称全市场逐币深研完成。一个资产多合约的资产研究可复用，但每份合约信号/计价仍分开，不重复累加市值或链上数量。





自动扫描与自动AI投研是两个独立开关和持久计划；自动AI首次关闭。可以配置“扫描成功后仅对变化候选投研”或独立投研时刻，展示币池规则、上限、深度及下一次执行时间。先保存，用户再显式启用；停用只阻止后续任务，运行中任务按已实现取消语义处理，不强杀。计划只在服务运行且联网时执行，休眠/重启错过任务按既定最多补一次规则处理，不积压调用。





AI任务和扫描/补查任务使用明确任务类型及关联ID，进度展示检索/生成/校验/归档；AI完成不得误触发行情刷新或发布新scan.json。避免长AI请求堵塞行情工作队列；设置受控并发、去重和取消边界。重复提交相同在途请求复用任务，不能重复付费。无AI配置时禁用AI提交并给配置入口，本地数据报告仍可用。





### 11.4 提示词、报告、缓存与策略草案





三档提示词按§3.2细化，页面可查看默认模板、编辑关注点、恢复默认并保存版本；共同事实/来源/安全约束只读。任务预览显示实际模型/协议、检索源、币池、证据时间、深度、提示词版本及输入缺口，无需填写Token预算。实际用量/耗时/网关返回错误写调用记录，供应商未返回用量时显示未知，不能填估算成真实计费。





报告按币保留：事实、六指标及其三态、位置/趋势/箱体/ATH口径、吸筹假设与反证、事件/供应、确认与失效、共同风险、数据缺口、策略建议。重要结论引用evidence_id；数值源可校验，AI不能改写已知字段。根据证据只给观察线索也属于有效报告；缺连续数据不能确认持续吸筹，任何异动不能判定“老鼠仓”。





缓存键至少含有序币池/合约身份、snapshot_revision及证据hash、规则/提示词/schema版本、实际模型/检索配置版本、深度、用户关注点。同版本证据在有效期内可以复用；新增证据、配置或模板变更使对应缓存失效。复用保留原生成时间并标注来源，不把查看时间当研究时间；历史报告仍可查，不在新快照发布时删除。





报告允许生成多个独立策略草案，明确来源报告、依赖证据、候选与入场/退出差异、参数及待确认项。草案必须经白名单/schema和覆盖校验；不能生成可执行代码或自动冻结/启用/开仓。用户审阅后保存新版、冻结，再建立模拟实验；报告“推荐”不得等同于已经启用。





### 11.5 数据、结构与回测必须补齐的细节





统一索引可以读取scan-history窗口和candle-history补查归档，但保留各来源hash/归档时间；冲突数据排除而非择有利值。覆盖查询区分“现在取得的历史价格”与“当时系统可得证据”；价格可用于明确标注的历史价格回测，但以后补核的市值、OI或报告不能反向作为当时入场依据。





历史预检按策略声明逐字段、周期、预热及交易区间检查，展示可测/不可测规则和排除原因。T1/T2/T3历史回测还需要当时OI、账户比、费率等；只有K线时可测价格策略或做从启用开始的前向实验，不能用今天候选币池声称历史选币收益。ATH区分本合约上市高点、所取区间高点、代币全市场ATH；全市场ATH需资产映射、价格口径、历史覆盖和来源核验。新上市/断档/拆分合并/币种迁移须独立标注。





持续吸筹采用独立连续窗口、波动收缩、量价、主动买盘/现货及可靠链上证据的组合假设，各项贡献和反证可见；模板只在参数、窗口数量和覆盖齐备后进入完整候选。箱体前20日（可选30/60）不包含突破日；触碰、宽度、驻留、缓冲与回踩规则先冻结再判定。ATR/ADX等增加之前先规定算法、周期、预热及跨语言一致性。





前向从冻结启用时刻起记录候选/确认/成交。每4小时默认批量补查持仓区间及结算，无需1–5分钟AI；价格撮合/账本不调用模型。每日/每周AI只读取确定账本生成解释，不能重写原成交或收益。无可用历史时显示“尚不可测”，不展示虚构曲线、胜率或模拟成绩。





### 11.6 本次文档修订验收与后续交付要求





本次只更新统一需求、入口、整改状态和GROK实施交接，不修改业务代码、配置、快照、独立审计原文或运行服务，不执行行情/AI/模拟任务。V2.8需求修订不能出现在健康接口中冒充已发布代码版本。





GROK每个代码阶段交付必须同步：实现ID/未完成ID、迁移及恢复说明、真实按钮与预览状态、验证命令和结果、真实源/模型是否实际验证、凭证脱敏、存量证据保护及下一依赖。模拟源验证与真实供应商验证分别记录，不能用本地HTTP或离线测试宣布AI和全市场上游已正常。新交付另写迭代记录，并更新§2、§4和本节状态，保留旧阶段事实。








### 11.7 本轮代码落地备注 · 2026-10-13（GROK）





本节补充当日已实现代码状态，不替代上方验收条件；真实供应商仍以页面显式测试为准。





| 项 | 状态 |


|---|---|


| AI-01 凭证 DPAPI / 脱敏配置 / 连接测试 | 已实现；用户测连接：鉴权成功、模型成功 |


| AI-02 能力分项 | 接口已实现；结构化/检索仍显示未验证（需用户点能力验证） |


| AI-03 当前快照投研 / 预览 / 缓存 / 调用记录 / 只读草案摘录 / **扫描并投研** | 已实现：`mode=scan_and_research` 先跑同款扫描，仅 success/partial_success 后绑定新 revision 再投研；扫描失败停住且不改用旧快照。残留：真实全市场扫描+计费投研需用户点验；服务版本仍 2.7 |


| 扫描进度 UI | 顶部显示完成/总数、已用时间与预计剩余；扫描任务优先于排队投研的进度文案 |


| 服务版本号 | 仍为 2.7，待 A/B 包用户侧验收后再升 |


| 报告区多报告展示 | 已实现：列表标题仅 SYMBOL+报告类型+时间；详情可读渲染，见 §11.8 |





页面验收提示：刷新后看顶部进度条；投研前可先预览（不计费）；报告页可点「扫描并投研」（先扫再研，失败不改用旧快照）；超时后可在设置把 timeoutSec 提到 180–300 再重试。








### 11.8 报告区展示迭代（2026-10-13）





目标：报告区按「列表 + 详情」拆开；列表标题只看币名，详情正文可读，不堆原始代码。





| 需求点 | 验收 |


|---|---|


| 报告列表标题 | 只显示「代币名+投研报告/数据报告」与生成时间，例如 `MOVRUSDT投研报告`；不显示模型、深度、份数 pill、报告ID |


| 详情标题 | 与列表一致：代币名+报告类型，下一行仅时间；不显示模型/协议/长ID |


| 正文可读 | AI 结构化 JSON（research-report-v1）渲染为结论/事实/假设/反证/未知/确认与失效/策略建议等区块；Markdown 渲染为标题与列表；本地 HTML 原样展示。禁止整页原始 JSON/`<pre>` 代码墙作为默认阅读态 |


| 单份详情 | 点击列表项进入详情；多合约用页签切换，默认当前合约 |


| 类型区分 | AI 用「投研报告」、本地用「数据报告」；禁止把未调模型的数据报告显示成 AI 成功 |


| 历史不丢 | 新报告不覆盖旧报告；可从列表打开任意历史份 |


| 操作入口 | 预览提交、生成数据报告、使用当前快照投研 结果写入列表并自动选中最新一份 |


| 空态/失败 | 失败任务在列表可见失败原因；详情区不残留上一次成功正文冒充本次结果 |





实现进度（2026-10-13）：列表+详情壳已接；标题改为「SYMBOL投研报告/数据报告 + 时间」；AI JSON/Markdown 可读渲染已接。页面强刷后点开历史报告验收。











---





## 实现进度日志





> 本节追加在文档末尾，记录各切片落地事实；不替代上方验收表原文。时间均为 Asia/Shanghai。





### 实现进度日志 · BACKTEST-01 首刀 · 2026-10-13 21:25 CST





| 项 | 内容 |


|---|---|


| 实现ID | BACKTEST-01 |


| 状态 | **部分实现（首刀）** |


| 时间 | 2026-10-13 21:25 CST |


| 文件 | `backtest_store.py`、`test_backtest_store.py`、`design/backtest-runtime.js`、`DOCS/design/iteration-v2.8-backtest01.md`；改 `server.py`、`design/prototype.html` |


| API | `GET /api/backtest/catalog`、`POST /api/backtest/run`、`GET /api/backtest/runs`、`GET /api/backtest/runs/{id}` |


| UI | 验证与复盘页：启动回测 / 列表 / 生存者偏差与仅本地K线警告 |


| 能力 | 单冻结版本×单合约×本地合并OHLCV bar-walk；训/验/测时序切分；EVAL同根/跳空/断档；SIM费用滑点；params_hash；不足样本→insufficient+无样本；禁止市值回填 |


| 未做（缺口） | 历史币池/退市重建；多币多版本公平比较；STRUCT过滤强制接入；全市场回测；联网批量拉盘 |


| 验证 | `python -m unittest test_backtest_store -v`；健康接口 `capabilities.backtest=true` |


| 依赖 | SIM-01、EVAL-01、RULE-01；STRUCT-01 钩子 `ready=false` |


| 备注 | 研究/模拟非实盘；不宣称全市场；中文界面；无「二手日历」 |








### 实现进度日志 · CONFIG-01 首刀 · 2026-10-13 21:29 CST





| 项 | 内容 |


|---|---|


| 实现ID | CONFIG-01 |


| 状态 | **部分实现（首刀）** |


| 时间 | 2026-10-13 21:29 CST |


| 文件 | `config_store.py`、`test_config_store.py`、`design/config-runtime.js`、`DOCS/design/iteration-v2.8-config01.md`；改 `server.py`、`prototype.html` |


| API | `GET /api/config`、`/api/config/catalog`；`PUT /api/config/observation-pool`、`/api/config/prompt-edits`；`POST /api/config/export`、`/api/config/import` |


| UI | 设置页：观察池/提示词覆写/无凭证导出 |


| 能力 | 刷新重启恢复；导出强制排除凭证；哈希/分叉冲突拒绝静默覆盖；存储 `runtime/config/` |


| 未做 | 全量备份恢复向导；设置页全字段双向绑定；SIM账本纳入导出 |


| 验证 | `unittest test_config_store`；`capabilities.config=true` |





### 实现进度日志 · EXT-01 首刀 · 2026-10-13 21:29 CST





| 项 | 内容 |


|---|---|


| 实现ID | EXT-01 |


| 状态 | **部分实现（首刀）** |


| 时间 | 2026-10-13 21:29 CST |


| 文件 | `ext_store.py`、`test_ext_store.py`、`design/ext-runtime.js`、`DOCS/design/iteration-v2.8-ext01.md` |


| API | `GET /api/ext/catalog`、`GET /api/ext/precise-flow`、`POST /api/ext/precise-flow/claim`（冒充→409） |


| UI | 设置页只读扩展目录 |


| 能力 | 精确⑤/事件解锁/历史市值/多市场引擎均标未就绪；拒绝储备/技术流冒充精确⑤；不做二手日历 |


| 未做 | 精确⑤真源授权接入；事件日历；历史市值源；USDC/COIN-M 计价模拟引擎 |


| 验证 | `unittest test_ext_store`；`capabilities.ext=true` |





### 实现进度日志 · 文档状态同步 · 2026-10-13 21:34 CST





| 项 | 内容 |


|---|---|


| 动作 | 同步整改清单 V2.8 表与 GROK §4，DATA/RULE/SIM/EVAL/REVIEW/STRUCT/BACKTEST/CONFIG/EXT 均标 **部分实现（首刀）**（非未实现） |


| 时间 | 2026-10-13 21:34 CST |


| 备注 | 不改独立审计原文；服务 version 仍 2.8 |





### 实现进度日志 · RULE-01 跨页绑定加深 · 2026-10-13 21:34 CST





| 项 | 内容 |


|---|---|


| 实现ID | RULE-01（加深刀） |


| 状态 | **部分实现**（统一引用绑定已接；强制筛选/导出 fingerprint 未齐） |


| 时间 | 2026-10-13 21:34 CST |


| 文件 | `rule_bindings.py`、`test_rule_bindings.py`、`DOCS/design/iteration-v2.8-rule01-bindings.md`；改 `server.py`、`design/strategy-runtime.js` |


| API | `GET /api/strategies/bindings`、`/bindings/catalog`、`/ref/{ver}`；`POST /ref/validate`、`/bindings/bind-all`；`PUT/DELETE /bindings/{surface}` |


| UI | 指标与策略：跨页统一引用面板 |


| 能力 | 五页可对齐同一 `unified_key`；错 hash/错 ID 拒绝；重启恢复 `runtime/strategies/bindings.json` |


| 未做 | 市场页强制只读绑定版本过滤；报告导出强制写入 fingerprint；白名单自动触发 |


| 验证 | `unittest test_rule_bindings` |





### 实现进度日志 · SIM-01 自动填仓加深 · 2026-10-13 21:37 CST





| 项 | 内容 |


|---|---|


| 实现ID | SIM-01（加深刀） |


| 状态 | **部分实现**（确认信号预览/填仓已接；全自动撮合与分档预算硬闸未齐） |


| 时间 | 2026-10-13 21:37 CST |


| 文件 | `sim_store.py`（plan_autofill/autofill）、`test_sim_autofill.py`、`DOCS/design/iteration-v2.8-sim01-autofill.md`；改 `server.py`、`design/sim-runtime.js` |


| API | `POST /api/sim/autofill/preview`、`POST /api/sim/autofill`（须 confirm=true） |


| UI | 模拟篮子：预览自动填仓 / 确认自动填仓 |


| 能力 | 按 risk_per_trade_pct 或显式名义估仓；scan mark；复用 open+守恒+同币 reject；白名单 allow/deny；不编造余额 |


| 未做 | T1/T2/T3 分档硬闸；观察期自动调度；资金费周期结算 |


| 验证 | `unittest test_sim_autofill` |





### 实现进度日志 · DATA-01 覆盖闸门加深 · 2026-10-13 21:40 CST





| 项 | 内容 |


|---|---|


| 实现ID | DATA-01（加深刀） |


| 状态 | **部分实现**（多字段 expected/received/known 闸门已接；策略预热硬闸/六指标未齐） |


| 时间 | 2026-10-13 21:40 CST |


| 文件 | `coverage_gate.py`、`test_coverage_gate.py`、`design/coverage-runtime.js`、`DOCS/design/iteration-v2.8-data01-coverage.md`；改 `server.py`、`prototype.html` |


| API | `GET /api/coverage/catalog`、`GET /api/coverage`、`GET /api/coverage/scan` |


| UI | 数据与任务：覆盖闸门表（partial/unknown；禁止 OI/市值/账户历史齐全宣称） |


| 能力 | 扫描行真实字段统计；本地 OHLCV 目录 known；全缺→unknown；部分→partial；claims 恒为 false（oi/mcap/account history） |


| 未做 | 启用前策略依赖硬闸；六历史指标；可信 ATH；联网补全 |


| 验证 | `unittest test_coverage_gate` |





### 实现进度日志 · RULE-01 覆盖硬闸加深 · 2026-10-13 21:45 CST





| 项 | 内容 |


|---|---|


| 实现ID | RULE-01（加深刀） |


| 状态 | **部分实现**（启用/冻结/绑定前 coverage covered 硬闸已接；市场 UI 强制消费与报告导出 fingerprint 未齐） |


| 时间 | 2026-10-13 21:45 CST |


| 文件 | `rule_coverage_gate.py`、`test_rule_coverage_gate.py`、`DOCS/design/iteration-v2.8-rule01-coverage-gate.md`；改 `strategy_store.py`、`rule_bindings.py`、`coverage_gate.py`、`server.py`、`strategy-runtime.js` |


| API | `GET /api/strategies/coverage-gate`、`/coverage-gate/map`、`/bindings/market/filter`；enable/freeze/bind 走硬闸 |


| 能力 | data_dependencies→coverage_gate 字段；须 covered；partial/unknown/ath/netflow 拒绝；不宣称六指标/ATH齐全；市场只读指纹 |


| 未做 | 市场页强制只读消费 filter；报告导出强制 fingerprint |


| 验证 | `unittest test_rule_coverage_gate` |





### 实现进度日志 · RULE-01 市场指纹与导出 · 2026-10-13 21:50 CST





| 项 | 内容 |


|---|---|


| 实现ID | RULE-01（加深刀 · 闭合剩余两环） |


| 状态 | **部分实现**（市场 UI 强制只读消费 filter；报告 local 保存/MD/CSV 导出强制 fingerprint；观察层阈值≠冻结规则结构化镜像） |


| 时间 | 2026-10-13 21:50 CST |


| 文件 | `rule_export_gate.py`、`design/rule-market-runtime.js`、`test_rule_export_fingerprint.py`、`DOCS/design/iteration-v2.8-rule01-export-fingerprint.md`；改 `server.py`、`ai_research.py`、`index.html`、`prototype.html` |


| API | `GET /api/strategies/bindings/export-fingerprint`；`POST /api/reports/local` 服务端 stamp；既有 `.../market/filter` |


| UI | 市场横幅只读指纹；下载 MD / 导出 CSV 走硬闸；未绑定拒绝 |


| 能力 | reports→market 回退取指纹；写入 unified_key/content_hash；防静默漂移重锁 |


| 未做 | 六阈值自动对齐冻结规则文本；AI 付费报告路径同等硬闸（local 已接） |


| 验证 | `unittest test_rule_export_fingerprint` |





---





## 实现进度日志 · 2026-10-13 22:01 Asia/Shanghai





### 本轮：只读审查 credibility 修复（F01–F08；F09 延后）





**不做**：STRUCT/CONFIG/EXT 加深；不扩模块数量替代闭环。





#### Batch A（启动/静态）


- **F06** 已修：start-pages.py health 版本接受 2.7/2.8；


eview_store Asia/Shanghai 懒加载，缺 tzdata 时给出可操作错误（当前本机 Python314 已有 tzdata）。


- **F08** 已修：两端口白名单增加 coverage-runtime.js / ext-runtime.js / evidence-runtime.js；重启后 8767/8768 均 200。





#### Batch B（SIM/EVAL/BACKTEST 可信）


- **F01** 已修：acktest_store.walk_segment 入场后留在成交当根评估高低；同根多事件走既有 worse_for_trader。单测：入场根仅止损/仅止盈/同时/跳空。


- **F02** 已修：未到期未触退出 → 保持 open/pending_backfill；到期但窗口/连续性不全 → unverifiable；禁止局部末价假装 max_hold/win。


- **F03** 已修：autofill 写账前硬闸 enabled + coverage + pause_new_entries + 触发证据；预览/确认稳定幂等键；停用策略不得开仓。


- **F04** 已修：冻结策略无 entry_rule → 不可测/未实现；禁止静默套用 close_above_prev_high；通用价格规则须显式 llow_generic_price_rule / experiment_kind=generic_price_rule。


- **F05** 已修：事件账本与 state 按 event_count 对齐；缺失/不一致时确定性重放 open/close/fee；禁止有孤儿事件时静默恢复 1000U。





#### Batch C


- **F07** 已修：扫描字段 expected≠received（total 应有 vs 实收）；EMA 须三条有效；目录存在≠完整覆盖；新增 evaluate_symbol_coverage（他币有数据不能放行缺目标）。





#### 延后


- **F09** AI 证据/缓存契约：下一批（非本轮）。


- STRUCT/CONFIG/EXT 加深：不扩。





#### 测试


- 聚焦 unittest：	est_backtest_store / 	est_eval_store / 	est_sim_store / 	est_sim_autofill / 	est_coverage_gate → **53 passed**。


- 未宣称全量回归绿：未重跑 verify-filters.cjs / 全量包；运行中服务已按修复代码重启验证 health+JS。





#### 备份


- 


untime/code-backups/credibility-fix-20261013-215556/





---





## 实现进度日志 · 2026-10-13 22:04 Asia/Shanghai





### 本轮：F09 AI 证据/缓存契约（P2）





**文件**：i_research.py、	est_ai_research.py  


**备份**：


untime/code-backups/f09-ai-research-20261013-220247/





#### 已修


- **证据字段**：去掉不存在的 undingRate/changePct；改用真实扫描字段 undingPct/undingAprPct、priceChangeWindowPct（同窗涨跌）、ema4h/closed4h、ieldAvailableAt/sOf/observed_at 等。声明 six_evidence_complete=false。


- **缓存身份**：


equest_hash/cache_material 纳入 aseUrl、config_version、evidence_hash、prompt_hash（编辑后的提示词+schema 内容哈希，不仅是 version 标签）。


- **run 配置绑定**：调用使用 prepare 冻结的 model/protocol/baseUrl/configId；与当前配置不一致且未 force → 拒绝静默重跑。


- **schema 校验**：尽量解析模型 JSON 并对照报告模板分节；markdown/不可解析 → schema_validation.status=unverified，不假装 success。


- **retrieval**：报告与返回固定 unverified（适配器未实现）；不因鉴权/模型成功升格。





#### 测试


- unittest test_ai_research：**13 passed**（含证据映射、缓存身份漂移、配置不一致拒绝、schema unverified/success、编辑提示词改缓存键）。





#### 残余


- 供应商侧 schema/retrieval **capabilities** 仍为 unverified（预期）；字段级报告 schema 未做全字段穷尽校验。


- 未宣称「六项证据齐全」。








### 实现进度日志 · §11.1 状态戳对齐 · 2026-10-13 22:16 Asia/Shanghai





| 项 | 内容 |


|---|---|


| 实现ID | 文档同步（非新代码） |


| 状态 | §11.1 为 STRUCT/BACKTEST/CONFIG/EXT 补上「部分实现（首刀）」；刷新过时的下一交付建议段 |


| 说明 | 与文末既有首刀日志及 F01–F09 修复一致；不把部分实现写成已验收 |





---





## 实现进度日志 · 2026-10-13 22:30 Asia/Shanghai





### EVAL-01 / REVIEW-01 调度加深 + AI 报告指纹硬闸





**不做**：STRUCT/BACKTEST/CONFIG/EXT 不标完成。





#### EVAL-01


- 观察计划默认 **enabled=false**；server_effect；scheduler_tick（暂停不新开）


- pending_backfill/unverifiable → ackfill_links 联动（F02 不假到期赢）


- API：/api/eval/status|plans|backfill-links





#### REVIEW-01


- 日/周计划默认关；调度绑定后到期跑**数值复盘**


- use_ai 仅门禁+快照，默认不付费调用





#### AI reports


- POST /api/reports 与 local 一样 stamp_export_payload 硬闸





#### 测试


- 	est_eval_store + 	est_review_store + 	est_reports_fingerprint_gate → **28 passed**





#### 备份


- 


untime/code-backups/eval-review-sched-*








---





## 实现进度日志 · 2026-10-13 22:35 Asia/Shanghai





### EVAL-01 补查自动入队（auto_enqueue_backfill）





**不做**：STRUCT/BACKTEST/CONFIG/EXT 不标完成。





| 项 | 内容 |


|---|---|


| 实现ID | EVAL-01（加深：自动入队） |


| 状态 | **部分实现** |


| 时间 | 2026-10-13 22:35 Asia/Shanghai |


| 文件 | `eval_store.py`、`task_service.py`、`design/eval-runtime.js`、`test_eval_store.py`；`DOCS/design/iteration-v2.8-eval01-schedule.md` |


| 计划字段 | `auto_enqueue_backfill` **默认 false**；`auto_backfill_on_gap` 仍默认 true（仅联动/prepare） |


| 行为 | settle → pending_backfill/unverifiable/data_gap 时写 backfill_links；仅 flag=true 调 TaskService.submit_range |


| 幂等 | 同 observation 窗口复用已有 link/task；不重复 spam |


| F02 | 不假价格/假 max_hold；入队失败只记 enqueue_error |


| API/UI | GET `/api/eval/backfill-links` 含 status/task_id；验证页补查联动表 |


| 验证 | `python -B -X utf8 -m unittest test_eval_store -v` |


| 备份 | `runtime/code-backups/eval-auto-enqueue-*` |


| 下一刀建议 | SIM-01 T1/T2/T3 档位预算硬闸（若本刀已收） |








---





## 实现进度日志 · 2026-10-13 22:39 Asia/Shanghai





### SIM-01 T1/T2/T3 分档保证金预算硬闸





**不做**：STRUCT/BACKTEST/CONFIG/EXT 不标完成。





| 项 | 内容 |


|---|---|


| 实现ID | SIM-01（加深：分档预算硬闸） |


| 状态 | **部分实现** |


| 时间 | 2026-10-13 22:39 Asia/Shanghai |


| 文件 | `sim_store.py`、`test_sim_store.py`、`test_sim_autofill.py`、`server.py`；`DOCS/design/iteration-v2.8-sim01-tier-budget.md` |


| 配置 | `budget_t1_u/t2/t3` 可 `PUT /api/sim/config`；`tier_budgets` 摘要；默认 400/300/300 |


| 行为 | open/autofill 前硬校验档位 margin；超限 Conflict 明文拒绝；at 允许；autofill 缺档位拒绝 |


| F03/同币/守恒 | 保持 |


| API | 既有 `/api/sim/config`、`/events/open`、`/autofill*`；响应可含 `tier_budget` |


| 验证 | `unittest test_sim_store test_sim_autofill` |


| 备份 | `runtime/code-backups/sim-tier-budget-*` |








---





## 实现进度日志 · 2026-10-13 22:45 Asia/Shanghai





### BACKTEST-01 多合约/多版本公平对照（首刀）





**不做**：不标 BACKTEST/STRUCT/CONFIG/EXT 完成；不重建历史上市宇宙。





| 项 | 内容 |


|---|---|


| 实现ID | BACKTEST-01（加深：fair compare） |


| 状态 | **部分实现** |


| 时间 | 2026-10-13 22:45 Asia/Shanghai |


| 文件 | `backtest_store.py`、`test_backtest_store.py`、`server.py`、`design/backtest-runtime.js`；`DOCS/design/iteration-v2.8-backtest01-compare.md` |


| API | `POST /api/backtest/compare`；`GET /api/backtest/compares`；`GET /api/backtest/compares/btc-…` |


| 能力 | 同批共享费用/歧义/绝对时间训验测切点；矩阵格报告 insufficient/排除/拒绝；生存者偏差与宇宙重建缺口显式 |


| F01/F04 | 保持 |


| 验证 | `unittest test_backtest_store` |


| 备份 | `runtime/code-backups/backtest-compare-*` |








---





## 实现进度日志 · 2026-10-13 22:49 Asia/Shanghai





### STRUCT×BACKTEST 过滤闸（首刀）





**不做**：不标 STRUCT/BACKTEST/CONFIG/EXT 完成；不宣称可信ATH。





| 项 | 内容 |


|---|---|


| 实现ID | STRUCT-01 / BACKTEST-01（加深：filter gate） |


| 状态 | **部分实现** |


| 时间 | 2026-10-13 22:49 Asia/Shanghai |


| 文件 | `struct_indicators.py`、`backtest_store.py`、测试与 `iteration-v2.8-struct-backtest-gate.md` |


| 行为 | `struct_filter` off/optional/required；失败格明文 exclusions；缺参→unimplemented |


| API | 既有 run/compare 增加 `struct_filter`；catalog hooks ready |


| 验证 | `unittest test_struct_indicators test_backtest_store` |


| 备份 | `runtime/code-backups/struct-backtest-gate-*` |


| 下一刀建议 | CONFIG 全量备份/恢复向导（若本刀已收） |





## 实现进度日志 · 2026-10-13 22:55 Asia/Shanghai





### CONFIG-01 全量备份/恢复向导（首刀加深）





**不做**：不标 CONFIG/EXT 完成；设置页全字段双向绑定留给下一刀；不导出凭证/AI Key。





| 项 | 内容 |


|---|---|


| 实现ID | CONFIG-01（加深：backup wizard） |


| 状态 | **部分实现** |


| 时间 | 2026-10-13 22:55 Asia/Shanghai |


| 文件 | `config_store.py`、`test_config_store.py`、`server.py`、`config-runtime.js`；`DOCS/design/iteration-v2.8-config01-backup-wizard.md` |


| schema | `workbench-backup-v1` |


| API | `POST /api/config/backup/{manifest,export,review,import}` |


| 覆盖 | observation_pool / prompts / strategy drafts+version refs / sim cost·tier budgets / eval·review plans |


| 约束 | 凭证永远排除；SIM 账本默认排除（`include_sim_ledger` / `import_sim_ledger`）；hash/fork → 409 拒绝静默覆盖 |


| 验证 | `unittest test_config_store`（12） |


| 备份 | `runtime/code-backups/config-backup-wizard-*` |


| 下一刀建议 | 设置页全字段双向绑定（CONFIG）或 EXT 精确流深化 |





## 实现进度日志 · 2026-10-13 23:05 Asia/Shanghai





### CONFIG-01 设置页全字段双向绑定（首刀加深）





**不做**：不标 CONFIG/EXT 完成；不回显 AI Key；不改 DPAPI 路径。





| 项 | 内容 |


|---|---|


| 实现ID | CONFIG-01（加深：settings bind） |


| 状态 | **部分实现** |


| 时间 | 2026-10-13 23:05 Asia/Shanghai |


| 文件 | `config_store.py`、`sim_store.py`、`server.py`、`design/config-runtime.js`、测试；`DOCS/design/iteration-v2.8-config01-settings-bind.md` |


| API | `GET /api/config/settings-bind`；既有 PUT settings / config / sim / eval·review enable |


| 行为 | 打开加载；保存带 version/hash/revision；409 冲突明示；AI 仅非密钥摘要 |


| 验证 | `unittest test_config_store` + `SimConfigBindConflictTests` |


| 备份 | `runtime/code-backups/config-settings-bind-*` |


| 下一刀建议 | EXT 精确流深化（precise-flow） |





## 实现进度日志 · 2026-10-13 23:10 Asia/Shanghai





### EXT-01 精确流加深（来源槽位闸门）





**不做**：不标 EXT 完成；不预置真源；不开放 USDC/COIN-M 模拟引擎；不用「二手日历」措辞。





| 项 | 内容 |


|---|---|


| 实现ID | EXT-01（加深：source slots / precise-flow） |


| 状态 | **部分实现** |


| 时间 | 2026-10-13 23:10 Asia/Shanghai |


| 文件 | `ext_store.py`、`test_ext_store.py`、`server.py`、`design/ext-runtime.js`；`DOCS/design/iteration-v2.8-ext01-precise-flow.md` |


| 槽位 | precise⑤ / events / unlock / supply / historical_mcap（默认 unauthorized） |


| API | `GET/PUT /api/ext/sources…`；`POST /api/ext/claim`；既有 precise-flow/claim |


| 行为 | 授权+时间/口径/哈希通过才 accept；替代源与引擎 claim → 409 |


| 验证 | `unittest test_ext_store`（7） |


| 备份 | `runtime/code-backups/ext-precise-flow-*` |


| 下一刀建议 | 页面点验 / §11.8 复验，或 DATA 覆盖闸加深 |





## 实现进度日志 · 2026-10-13 23:15 Asia/Shanghai





### DATA-01 覆盖闸加深（缺口 / 补查提示 / 指纹）





**不做**：不标 DATA/STRUCT/BACKTEST/CONFIG/EXT 完成；不重建全历史宇宙；不用「二手日历」。





| 项 | 内容 |


|---|---|


| 实现ID | DATA-01（加深：coverage deepen） |


| 状态 | **部分实现** |


| 时间 | 2026-10-13 23:15 Asia/Shanghai |


| 文件 | `coverage_gate.py`、`rule_coverage_gate.py`、`task_service.py`、`sim_store.py`、`price_index.py`、`server.py`、`design/coverage-runtime.js`、测试；`DOCS/design/iteration-v2.8-data01-coverage-deepen.md` |


| 行为 | 字段/合约缺口+补查提示；指纹；空依赖基线闸；scan_and_research/autofill 加硬闸；快照 expected vs received |


| API | `GET /api/coverage`（gaps+fingerprint）；`GET /api/coverage/gaps`；快照列表增指纹 |


| 验证 | `unittest test_coverage_gate`（11） |


| 备份 | `runtime/code-backups/data-coverage-deepen-*` |


| 下一刀建议 | 页面点验 / §11.8 复验，或 RULE 联调 |





---





## 实现进度日志 · 2026-10-13 23:20 Asia/Shanghai





### RULE-01 导出指纹联调加深（coverage + strategy hash）





**不做**：不标 RULE/DATA/STRUCT/BACKTEST/CONFIG/EXT 完成；不宣称六指标/ATH 齐全；不用「二手日历」。





| 项 | 内容 |


|---|---|


| 实现ID | RULE-01（加深：export fingerprint 联调） |


| 状态 | **部分实现** |


| 时间 | 2026-10-13 23:20 Asia/Shanghai |


| 文件 | `rule_export_gate.py`、`strategy_store.py`、`rule_bindings.py`、`server.py`、`test_rule_export_fingerprint.py`；`DOCS/design/iteration-v2.8-rule01-export-fingerprint-deepen.md` |


| schema | `rule-export-fingerprint-v2`（content_hash + coverage_fingerprint + expected/received + joint_hash） |


| 行为 | freeze/bind/export 携带联合指纹；import/enable 拒绝陈旧/不匹配 → 409；可 `acknowledge_coverage_drift` |


| API | `POST …/export`、`POST /api/strategies/import`；enable 扩指纹字段；export-fingerprint 联调 root |


| 验证 | `unittest test_rule_export_fingerprint`（18） |


| 备份 | `runtime/code-backups/rule-export-fingerprint-20261013-231614/` |


| 下一刀建议 | 页面点验 / §11.8 复验收益，或另一 deepen |





---





> **未完成缺口待审（给 GPT）**：见 [`DOCS/未完成需求-v2.8-待审.md`](../8069-未完成需求-v2.8-待审.md)（ASCII 同文 [`open-gaps-v2.8-for-gpt.md`](../8068-open-gaps-v2.8-for-gpt.md)）。主需求仍以本文为准；该文只整理未完成/部分实现缺口。





---





## 实现进度日志 · 2026-10-13 23:30 Asia/Shanghai





### 审计修复 · P1 RULE-IMPORT-HASH + P2 RULE-JOINT-KEY





**不做**：不标 RULE/DATA/SIM/EVAL/… 完成；不停 V1 路径外的零散 deepen；不用「二手日历」。





| 项 | 内容 |


|---|---|


| 实现ID | RULE-01（审计缺陷修复，非新 deepen） |


| 状态 | **部分实现**（P1/P2 已修；模块未标完成） |


| 时间 | 2026-10-13 23:30 Asia/Shanghai |


| P1 | 导入按 rules+deps+meta **重算** content_hash；与声明不符 → 409；禁止篡改后 `fingerprint_verified=true`；改名记录 source/result hash |


| P2 | `canonical_unified_key` 全长 hash；freeze/export 与 bindings 同版本同覆盖 → 同 joint_hash |


| 验证 | `unittest test_rule_export_fingerprint`（20，含 P1/P2 回归） |


| 备份 | `runtime/code-backups/rule-p1-p2-audit-*` |


| V1 路径 | 扫描→报告→冻结策略→USDT纸面模拟→定期结算复盘（见 `DOCS/未完成需求-v2.8-待审.md`） |





---





## 实现进度日志 · 2026-10-13 23:35 Asia/Shanghai





### V1 修复 · 预热闸门可绕过（WARMUP-BYPASS / F07）





**不做**：不标 DATA/RULE/SIM/EVAL 完成；不做 ATH/宇宙/多市场；不停 V1 路径外的 deepen。





| 项 | 内容 |


|---|---|


| 实现ID | DATA/RULE/SIM 硬闸收紧（V1 验收路径） |


| 状态 | **部分实现**（绕过已修；模块未标完成） |


| 时间 | 2026-10-13 23:35 Asia/Shanghai |


| 绕过 | 请求 `warmup_bars` 可降到 1 跳过断档；F07 partial 可过基线 |


| 修复 | `resolve_warmup_bars` 底线 20+冻结只升不降→409；critical 须 covered；`require_status` 不可降为 partial；断档拒 autofill |


| 验证 | `unittest test_coverage_gate`（16，含 WarmupBypassHardeningTests 5） |


| 备份 | `runtime/code-backups/warmup-bypass-*` |


| 下一刀 | 补查完成后自动再 settle（EVAL） |





---





## 实现进度日志 · 2026-10-13 23:40 Asia/Shanghai





### V1 · EVAL 补查完成后自动再 settle





**不做**：不标 EVAL 完成；不做 ATH/宇宙/多市场；不撒 deepen。





| 项 | 内容 |


|---|---|


| 实现ID | EVAL-01（V1 闭环：backfill → resettle） |


| 状态 | **部分实现** |


| 时间 | 2026-10-13 23:40 Asia/Shanghai |


| 行为 | `candle_range` 成功/部分成功 → `on_candle_range_task_finished` 对关联 open 观察再 settle；调度 `poll_completed_backfills_and_resettle` |


| F02 | 未到期不结算；失败补查不假结算 |


| 断档 | 触价前窗口断档 → pending_backfill / unverifiable，禁止假赢 |


| 验证 | `unittest test_eval_store`（24，含 EvalBackfillResettleTests 4） |


| 备份 | `runtime/code-backups/eval-resettle-*` |


| 下一刀 | 冻结参数执行遵守 / CONFIG 备份 hash / V1 页面点验 |








---





## 实现进度日志 · 2026-10-13 23:41 Asia/Shanghai





### V1 · 冻结参数执行遵守（FROZEN-EXEC）





**不做**：不标 SIM/EVAL/BACKTEST/RULE 完成；不做 ATH/宇宙/多市场；不撒 deepen；不改前端 HTML；不用「二手日历」。





| 项 | 内容 |


|---|---|


| 实现ID | SIM/EVAL/BACKTEST 执行闸（V1：只服从冻结版本） |


| 状态 | **部分实现**（后端闸已挂；模块未标完成；页面点验未做） |


| 时间 | 2026-10-13 23:41 Asia/Shanghai |


| 行为 | `frozen_exec.py`：`require_frozen_exec_params` 从冻结 `rules.params` 取 entry/exit/SL/TP/hold/budget；缺参/歧义 → FrozenExecConflict→409；禁止请求体覆盖不一致值；禁止静默默认 entry_rule（F04） |


| 接线 | `sim_store` autofill；`eval_store` start_observation；`backtest_store` run；`server` 映射 409 |


| 测试 | 草案改后仍用冻结；body 发散 TP 拒绝；缺 params 拒绝；无 entry_rule 拒绝 F04 替代 |


| 验证 | `unittest test_frozen_exec test_sim_autofill test_eval_store test_backtest_store`（68 OK） |


| 备份 | `runtime/code-backups/frozen-exec-*` |


| 下一刀建议 | CONFIG 备份内容 hash 点验，或 V1 页面冻结→autofill/settle 点验 |








---





## 实现进度日志 · 2026-10-13 23:46 Asia/Shanghai





### V1 · CONFIG 备份 content hash 校验





**不做**：不标 CONFIG 完成；不做 ATH/宇宙/多市场；不撒 deepen；不改前端 HTML；不用「二手日历」。





| 项 | 内容 |


|---|---|


| 实现ID | CONFIG-01（备份完整性 / 恢复安全） |


| 状态 | **部分实现**（hash+逃逸+并发闸已挂；模块未标完成；页面点验未做） |


| 时间 | 2026-10-13 23:46 Asia/Shanghai |


| 行为 | export 写 `payload_hash` + `manifest.package_hash`；import/review `_verify_backup_integrity`；缺 hash/篡改载荷→409；draft/version 恢复 `_safe_json_path` 禁逃逸；pause_conflict；`expected_package_hash` / `expected_local_revision` 并发闸 |


| 验证 | `unittest test_config_store`（22，含 BackupIntegrityHardeningTests 8） |


| 备份 | `runtime/code-backups/config-backup-hash-*` |


| 下一刀建议 | V1 页面点验（冻结→autofill/settle / 备份向导），或下一审计正确性项 |





---





## 实现进度日志 · 2026-10-13 23:52 Asia/Shanghai





### V1 · BACKTEST/STRUCT 未来数据泄漏收口





**不做**：不标 BACKTEST/STRUCT 完成；不做 ATH/宇宙/多市场；不撒 deepen；不改前端 HTML；不用「二手日历」。





| 项 | 内容 |


|---|---|


| 实现ID | BACKTEST-01 / STRUCT-01（lookahead 收口） |


| 状态 | **部分实现**（as-of + 公平窗裁剪已挂；模块未标完成；页面点验未做） |


| 时间 | 2026-10-13 23:52 Asia/Shanghai |


| 行为 | `evaluate_struct_gate_as_of`：入场仅用 bars[0..i]；单元格 STRUCT 预检 `value_thresholds=False`（禁全窗 min_adx 泄漏）；`crop_bars_to_ms` + compare `fair_window_ms` 统一裁剪；不足 as-of → 排除计数不静默填未来；仍拒市值回填 |


| 验证 | `unittest test_struct_indicators test_backtest_store`（56，含 LookaheadGuard/LookaheadCrop） |


| 备份 | `runtime/code-backups/lookahead-guard-*` |


| 下一刀建议 | V1 API smoke（扫描→报告→冻结→纸面→结算），或 EXT auth bypass（若仍开） |





---





## 实现进度日志 · 2026-10-13 23:58 Asia/Shanghai





### V1 · API smoke（扫描→报告→冻结→纸面→结算→备份）





**不做**：不标任一模块完成；不做 ATH/宇宙/多市场；不撒 deepen；不改 Codex 前端 HTML；不用「二手日历」；不花真实 AI 钱（只用既有 scan 快照）。





| 项 | 内容 |


|---|---|


| 实现ID | V1-API-SMOKE（live 8767/8768） |


| 状态 | **部分点验通过**（API 路径绿；页面点验未做；模块未标完成） |


| 时间 | 2026-10-13 23:58 Asia/Shanghai |


| 矩阵 | Health 2.8 / scan / coverage / draft→freeze→enable / autofill dry-run+发散409 / obs start / settle F02 early / rules TP / 断档不假赢 / backup manifest+payload_hash+篡改409 / frozen_exec 发散 TP 409 |


| 结果 | **34/34 PASS**（`v1_api_smoke_result.json` stamp=20261013235745） |


| 文档 | `DOCS/design/iteration-v2.8-v1-api-smoke.md` |


| 产品修复 | 无（首轮 403/400/409 为脚本缺 Origin 或 ID/参数发散；产品闸正确） |


| 下一刀建议 | EXT auth bypass（若仍开），或等 Codex HTML 做 V1 页面点验 |





---





## 实现进度日志 · 2026-10-14 00:05 Asia/Shanghai





### V1 · EXT auth bypass 收口





**不做**：不标 EXT 完成；不预置精确⑤真源；不做 ATH/宇宙/多市场引擎；不改 Codex HTML；不用「二手日历」。





| 项 | 内容 |


|---|---|


| 实现ID | EXT-01（auth bypass / claim 核验硬化） |


| 状态 | **部分实现**（闸已挂；真源未预置；页面点验未做；模块未标完成） |


| 时间 | 2026-10-14 00:05 Asia/Shanghai |


| 行为 | `refuse_bypass_flags`；未来 as_of→409；claim 须 echo as_of/口径/hash；精确⑤拒替代源登记/claim；异槽不可冒充精确⑤；historical_mcap 拒当今回填旗 |


| 验证 | `unittest test_ext_store`（17，含 ExtAuthBypassHardeningTests 10） |


| 备份 | `runtime/code-backups/ext-auth-bypass-*` |


| 文档 | `DOCS/design/iteration-v2.8-ext01-auth-bypass.md` |


| 下一刀建议 | 等 Codex HTML 做 V1 页面点验，或下一审计正确性项 |





---





## 实现进度日志 · 2026-10-14 00:10 Asia/Shanghai





### V1 · AI 报告指纹/证据/schema·缓存 贯通检查（核实）





**不做**：不标 AI 完成；不宣称供应商 schema/retrieval 已验收；不宣称六项证据齐全；不改 Codex HTML；不做 ATH/宇宙/多市场；不用「二手日历」。





| 项 | 内容 |


|---|---|


| 实现ID | AI-03 / F09 贯通复核 |


| 状态 | **核实通过 · 无新增代码**（既有 F09 + stamp_export_payload 已覆盖） |


| 时间 | 2026-10-14 00:10 Asia/Shanghai |


| 核验 | `unittest test_ai_research test_reports_fingerprint_gate`（15 OK） |


| 文档 | `DOCS/design/iteration-v2.8-ai-f09-fingerprint-check.md` |


| 下一刀 | **idle：等待 Codex HTML** 做 V1 页面点验 |





---





## 进度追加 · A布局+C专业表 · 第一步（2026-10-14）





状态：**代码已落地**（外壳/专业表/字段绑定）；**离线静态检查通过**（8768 脚本 200、无 404）；**现网浏览器点验未完整跑完** → 标「现网加载未验证」中的交互细节（托盘遮挡、表头排序手感等需人工点验）。





### 改动文件


- `design/pro-table-runtime.js`（新建）：默认 11 + 可配 26 列、固定选择/合约列、选币托盘、列配置对话框、证据抽屉字段档案、市值百万美元边界、AND/OR 三态、命中数优先 `hitCount`/`knownCount`


- `design/prototype.html`：引入上述脚本；市值单位改为百万美元；`capRanges` 同步；增加「已选」结果页签


- `server.py`：`PROTOTYPE_FILES` 增加 `pro-table-runtime.js`


- `DOCS/design/GROK-前端实施方案-A布局-C专业表格.md`（归档）


- `DOCS/design/field-audit-a-pro-step1.md`（字段对账）


- `DOCS/INDEX.md`（索引条目）





备份：`runtime/code-backups/frontend-a-pro-step1-20261014-003608/`





### 行为（第一步范围）


- 左侧七项导航保留；策略/模拟/复盘/数据任务/设置等旧模块未替换为预览占位页


- 扫描表接 `/api/scan` 真实快照字段；未把预览 mock 数字（588/181/73）写成生产验收常量


- 市值：下限含、上限不含；自定义单位百万美元；非法范围阻止筛选


- 市值未知 → 待核验；六项统计在基础筛选范围内独立计数


- 组合预设仍从 `StrategyRegistry.presets` 读取


- 选择键 `market|symbol`；本页全选只作用于当前页；托盘展示已选并提示被筛选隐藏数量


- 打开页面 / 切菜单 / 自动刷新不触发扫描或付费 AI；本轮重启服务时 `scan.enabled=false`





### 验证方法与结果


- 静态：`GET http://127.0.0.1:8768/pro-table-runtime.js` → 200；`prototype.html` 含脚本引用与「百万美元」「已选」


- 只读：`GET /api/scan` 核对默认 11 + 15 可配列 + 抽屉字段均存在于行对象；⑤无精确净流，前端显示未知而非 0


- 未做：完整 Playwright/手工点验固定列滚动、托盘与分页几何、跨页选币全路径 → **现网加载未验证**（服务已加载新脚本，交互几何未截图验收）





### 剩余缺口


- 第二步投研对象/预览确认、第三步报告阅读未做（按方案停在第一步）


- `ai-runtime.js` 对象回退与 preview 自动提交问题留待第二步


- 部分后端辅助字段（如 `fundingDailyPct`、`belowAllEma`、`capPriceGapPct`）未进默认 26 列，见字段对账「可选后续」


- 建议下一步：现网点验扫描页布局与选币托盘；通过后再开第二步投研链路








### 正式现有功能 vs 预览对照（保留约束 · 2026-10-14）





原则：只借鉴 version-a 的外壳/专业表交互；**禁止**用预览替换或阉割正式已有能力。预览没有的控件 ≠ 要删除。





| 能力 | 正式 prototype | 预览 version-a-pro | 本轮处置 |


|---|---|---|---|


| AI 服务商 (#provider) | 有 | 无（仅演示对话框） | **保留** |


| 接口协议/版本 (#protocol：chat/responses/messages) | 有 | 无 | **保留** |


| 模型版本下拉 (#modelSelect，ai-runtime 动态注入「选择已知模型版本…」) | 有（运行时） | 无 | **保留** |


| Base URL / API Key / modelId / 拉取模型 / 测试 / 保存 / 清 Key | 有 | 无完整绑定 | **保留** |


| 能力分项状态（鉴权/模型/结构化/检索） | 有（ai-runtime render） | 无 | **保留** |


| 提示词版本与投研预览/扫描并投研 | 有 | 仅本地模板 | **保留** |


| 定时扫描真实设置 | 有（tasks-runtime） | 演示开关 | **保留** |


| 策略/模拟/复盘/数据任务/设置七模块真实页 | 有 + 各 runtime | 多为概览占位 | **保留**，未改成预览占位 |


| 专业表 11+26 列 / 托盘 / 筛选侧栏 | 本轮增强 | 预览交互来源 | **仅借鉴交互**，数据走 `/api/scan` |





核对：备份 `frontend-a-pro-step1-20261014-003608` 与当前 HTML 中 `#provider/#protocol/#baseUrl/#apiKey/#modelId/#loadProviderModels/#testApi/#saveAiConfig` 均仍存在且数量一致；`ai-runtime.js` / `config-runtime.js` 未改。`#modelSelect` 等由 `ensureModelSelect()` 在设置页注入，不依赖预览。





配置面板部分 `cfg*` DOM 由 `config-runtime` 运行时挂载（备份中亦无静态 id），非本轮回归。





状态：旧功能 **代码仍在 / 绑定未删**；本轮未做设置页现网点验点击 → 设置交互标「现网加载未验证」。


- 加固：为设置页已有「超时/重试/任务绑定」控件补上 `#aiTimeout/#aiRetries/#aiTaskResearch/#aiTaskDraft/#aiTaskReview`，与 `ai-runtime` 读取一致（改前备份亦缺这些 id，属补绑而非预览引入）。





---





## 前端 A 外壳落地（收紧大统计/六指标大卡 · 2026-10-14）





状态：**代码已落地**；现网静态资源已加载（8768）；**交互点验/截图未做** → 现网点验待用户确认。





### 问题


上一步仅加 `pro-table-runtime.js` 增强专业表，**未把扫描页外壳改成 version-a**。现网仍是巨大「快照覆盖 588/588」统计卡 + 六张大指标卡占满首屏，与 Codex 预览 version-a（A布局+C专业表）差很多。





### 根因


`ensureShell` 只把筛选与结果并排，仍保留旧 `.stats` 大卡与 `#indicators.grid3` 大卡样式；缺少 A 的 `snapshot-line` / `filterbox` / `scanlayout` 信息密度。





### 改动文件


- `design/a-shell.css`（新建）：A 布局样式（snapshot-line、scanlayout、filterbox、侧栏指标密排、结果工具条、深色侧栏对齐）


- `design/pro-table-runtime.js`：增强 `ensureShell`——stats→snapshot-line、workbench+scanlayout、filterbox、indicator-list、结果区工具条/显示列/托盘；版本标记 `a-pro-shell-fix`


- `design/prototype.html`：引入 `a-shell.css`；market 区静态标记 `a-shell-market` / `snapshot-line` / `filterbox` / `indicator-list` / scanlayout 数据属性


- `server.py`：`PROTOTYPE_FILES` 增加 `a-shell.css`


- `DOCS/design/requirements.md`（本条）





备份：`runtime/code-backups/frontend-a-pro-shell-fix-20261014-004722/`





### 行为（本轮范围）


- 扫描首屏：一行 snapshot 摘要 + 左侧统一筛选（含收紧的六指标勾选）+ 右侧专业表；大统计卡不再占满首屏


- 七项导航与策略/模拟/复盘/设置/AI（`#protocol` 等）真实页保留；未用预览 `app.js` 替换生产 runtime


- 数据仍走 `/api/scan` 与既有 renderMarket/筛选；缺失显示未知


- 打开页面/切菜单不触发扫描或付费 AI（health 中 `scan.enabled=false`）





### 验证方法与结果


- 重启 8767/8768 后：`GET http://127.0.0.1:8768/a-shell.css` → 200（7208）


- `GET .../pro-table-runtime.js` → 200，含 `a-shell-market` / `scanlayout` / `a-pro-shell-fix`


- `GET .../prototype.html` → 含 `a-shell.css`、`snapshot-line`、`filterbox`、`scanlayout`、`a-shell-market`、`indicator-list`；`#indicators` 不再是 `grid3`


- `GET /api/health` → 200，scan.enabled=false


- 脚本无 404（a-shell.css / pro-table-runtime.js / strategy-registry.js）


- 未做：浏览器截图对照 version-a、托盘/固定列滚动人工点验 → **现网点验待确认**





### 剩余缺口


- 用户浏览器硬刷新后目视确认首屏已是「侧栏筛选 + 专业表」而非六张大卡


- 第二步投研对象/预览确认、第三步报告阅读未做


- `ai-runtime.js` 对象回退与 preview 自动提交问题仍待第二步


- 入口说明：正式扫描页为 `http://127.0.0.1:8768/prototype.html`（8767 为总览入口）





## 2026-10-14 扫描侧栏清理与结果表视口适配





- 扫描筛选区移除「数据、用途与策略」跳转按钮；移除 `#reserveFilterHelp` 大段灰字（储备辅助数据/六指标⑤仍需/储备截止…）；移除「筛选说明」折叠块与 `#querySummary` 市值·排序·hitCount 文案。


- **保留**「⑤扩展 · 储备辅助筛选」下拉（`#reserveFilter`）及减少阈值（`#reserveThreshold`）；后端储备/六指标⑤能力与设置页入口不动。


- 六指标列表改为竖排三行（标题 / 条件 / 命中·未知），避免窄侧栏叠字。


- 专业结果表：去掉 `min-width:1280` 与表容器双轴滚动；`table-layout:fixed` + 收紧字号行高适配视口；选择列与合约列固定；多余行靠分页，优先单层页面滚动。








## 2026-10-14 扫描结果区取消分页 + 表头排序 + 显示列收敛





状态：**代码已落地**；备份 `runtime/code-backups/frontend-no-pager-20261014-010104/`。现网点验需用户硬刷新 `http://127.0.0.1:8768/prototype.html`。





### 行为


- **取消分页**：`pageRows` = 当前筛选全部行；隐藏「每页N份 / 上一页 / 下一页 / 页码」。


- **取消表内滚动**：`.tablewrap` / `.a-pro-tablewrap` `overflow:visible; max-height:none`；表格自然撑开，只留浏览器窗口滚动。


- **选币文案**：「跨页」改为「跨筛选保留」。


- **表头排序**：合约/价格 + 所有可见数据列可点表头切换升/降序；与工具条 `#sort`/`#direction` 共用状态；缺失值正反序都置后；同值按 `market+symbol` 稳定次序。


- **⑤净流列**：保留在默认 11 列与可选列表；当前无精确覆盖时仍显示「未知 · 无精确覆盖」（后续文档已规划接入）。





### 显示列分析（基于根目录 `scan.json` 抽样 N=588，只读，未触发新扫描）





| 类别 | 列 | 依据 |


|------|----|------|


| **建议默认保留** | 流通市值、命中/可算/未知、本合约OI、①OI/市值、②全体账户比、③费率/年化、④已收盘4h/EMA、**⑤净流入**、⑥观察窗量倍数、24h涨跌、24h成交额 | 除⑤外 present≥87%；⑤虽 100% 未知仍默认保留占位 |


| **可选保留（明细/冗余）** | 标记/指数/24h高/低、单根量倍数、观察窗/基准窗成交额、EMA7/25/99、已收盘4h、同资产总OI、观测时间 | 有数据，但与默认列部分重复，按需勾选 |


| **建议隐藏或降级** | 大户账户比、大户持仓比 | present≈0.2%，近空；仍留在「全部」可选且标注，默认不勾 |





默认勾选仍为上述 11 列（未为凑 26 硬开近空列）。弹窗可展开到 26 个数据列；选择列与合约列固定不计入 26。





### 改动文件


- `design/prototype.html`：去切片分页、隐藏 pager、tablewrap overflow、sortRows 升级


- `design/pro-table-runtime.js`：强制全量渲染、表头全列可排、SORT_KEYS 补 ema/flow/ema7/25/99、列标注、跨筛选文案


- `design/a-shell.css`：隐藏 pagination、禁止表内容器滚动、略收列宽


- `DOCS/design/requirements.md`（本条）





### 验证


- 只读 `GET http://127.0.0.1:8768/prototype.html`：源码不应再输出可见「上一页/下一页/每页20份」控件文案（节点可 hidden）。


- 硬刷新后：结果区一次显示全部筛选行；无表内竖向滚条；表头可排序；⑤列仍在。


- 未触发扫描/付费（health `scan.enabled=false`；分析用本地 `scan.json`）。





## 2026-10-14 扫描结果表横向溢出修复 + P2 EMA 表头排序





状态：**代码已落地**；备份 `runtime/code-backups/frontend-overflow-fix-20261014-011106/`。请硬刷新 `http://127.0.0.1:8768/prototype.html`。





### 溢出（主目标）


- 上轮为取消表内双滚动设 `overflow:visible`，导致⑤净流/⑥量价等画出 results 白卡片圆角，整页横撑。


- 本轮：容器 `width/max-width:100%; min-width:0; overflow-x:clip`（results-panel / tablewrap）；`table { width:100%; table-layout:fixed; min-width:0 }`；收紧字号/padding；长文案换行+ellipsis；选择列 28px、合约列 ≤96px，sticky 不额外撑总宽；**未恢复** `min-width:1280`。


- 仍保留：无分页、全量行、表头排序、⑤列、扩展下拉；无表内「又横又竖」双滚动条（竖向靠页面滚动）。





### P2 EMA7/25/99 表头排序


- 根因：`pro-table-runtime.js` 的 `sortValue` 只读行直接属性；EMA 在 `ema4h[]`，SORT_KEYS 映射的 `ema7/25/99` 恒为缺失。


- 修复：`ema7→ema4h[0]`、`ema25→ema4h[1]`、`ema99→ema4h[2]`；⑤ `flowSignal` 从 `signals/flags[4]` 派生；缺失 null 置后。隔离断言通过（A=[1,2,3] B=[9,8,7] 降序 → B,A）。


- `comboExplanation`：改为隐藏节点（不再 remove），避免 baseRender 写 textContent 抛错。





### 改动文件


- `design/a-shell.css`、`design/pro-table-runtime.js`、`design/prototype.html`、`DOCS/design/requirements.md`





### 专业表字号恢复（2026-10-14）


- 上轮 overflow-fix 将主字号压到约 10–10.5px / 副文 9.5px，难读。


- 本轮：在保留 `overflow-x:clip`、`table-layout:fixed`、无 `min-width:1280` 前提下，将 font-size / line-height / 单元格 padding 恢复到压小前（对照 `frontend-overflow-fix-20261014-011106` / `frontend-no-pager`）：主数值与单元格 11px、表头 10.5px、coin-sub/small 10px、hitval 12px、padding 5px 4px。


- 未恢复分页；未改业务逻辑。备份：`runtime/code-backups/frontend-font-size-20261014-011607/`。








## Grok 前端实施记录追加（2026-10-14）





- 第二步投研：预览确认、去静默顶20 → `iteration-v2.8-frontend-research-step2.md`（代码已落地，现网点验待用户）


- 第三步报告：列表类型/详情 meta/导出/错误态 → `iteration-v2.8-frontend-reports-step3.md`（代码已落地，现网点验待用户）


- 第四步联调冒烟：17 script+css 全 200 → `iteration-v2.8-frontend-step4-smoke.md`（静态通过，交互验收待用户）





## Grok 前端实施记录追加（2026-10-14 13:27）





- P1 选币/预览辅助/提示词签名：见 `iteration-v2.8-frontend-p1-research-select.md`


- P2 托盘 fixed + 表区 max-height 内滚保表头 sticky + 命中/未知分色；报告列表/详情标题按 §11.8 只留币名+类型+时间


- 备份：`runtime/code-backups/frontend-p2-tray-report118-*`


- Codex 继续排查；本刀不标模块完成





### 实现进度日志 · V1-B2 策略导出/导入页接线 · 2026-10-14 13:35 Asia/Shanghai





| 项 | 说明 |


|---|---|


| 实现ID | V1-B2（页面：冻结版本导出包 + 导入 JSON；不标 RULE 完成） |


| 改动 | `design/strategy-runtime.js`：冻结卡「导出包」；导入面板（粘贴/选文件）；导出指纹只读条；横幅口径更新 |


| 备份 | `runtime/code-backups/frontend-v1b2-strategy-export-20261014T133455` |


| 未做 | 现网点验；观察层六条文镜像；不把启用当成自动下单 |


| 下一刀建议 | 纸面模拟页与冻结版本联调（V1-B3），或等 Codex 排查结论 |





### 实现进度日志 · V1-B3 纸面模拟↔冻结版本联调 · 2026-10-14 13:42 Asia/Shanghai





| 项 | 说明 |


|---|---|


| 实现ID | V1-B3（页面：自动填仓强制冻结版 + 冻结 params 只读提示；不标 SIM 完成） |


| 改动 | `design/sim-runtime.js`：版本列表含未启用标记；默认跟篮子绑定；填仓不传手填名义覆盖；展示冻结 params；顶栏启用数同步 |


| 备份 | `runtime/code-backups/frontend-v1b3-sim-freeze-20261014T134010` |


| 未做 | 全自动撮合；现网点验；EVAL 结算页加深 |


| 下一刀建议 | V1-B4 定期结算页点验，或等 Codex 排查结论 |





### 实现进度日志 · V1-B4 前向结算↔冻结参数联调 · 2026-10-14 13:46 Asia/Shanghai





| 项 | 说明 |


|---|---|


| 实现ID | V1-B4（页面：开观察不再提交手填止盈止损/持仓；只读冻结 params；不标 EVAL 完成） |


| 改动 | `design/eval-runtime.js`：去掉可改 TP/SL/持仓输入；展示冻结摘要；默认验证页绑定；状态含调度/暂停；断档/不可核标「不计假赢」 |


| 备份 | `runtime/code-backups/frontend-v1b4-eval-freeze-20261014T134515` |


| 未做 | 全市场自动观察；现网点验；付费复盘叙述 |


| 下一刀建议 | V1-B5 复盘页，或 V1-B6 配置备份页，或等 Codex 排查结论 |





### 实现进度日志 · V1-B6 配置备份向导页接线 · 2026-10-14 13:48 Asia/Shanghai





| 项 | 说明 |


|---|---|


| 实现ID | V1-B6（页面：导出/审阅露出 package_hash 与分段 payload_hash；选文件载入；不标 CONFIG 完成） |


| 改动 | `config-runtime.js` 完整性摘要；存储页入口；页脚 v2.8 口径 |


| 备份 | `runtime/code-backups/frontend-v1b6-config-backup-20261014T134729` |


| 未做 | 现网点验；全字段旧演示表单清场 |


| 下一刀建议 | V1-B5 复盘页，或等 Codex 排查/用户硬刷验收 |





### 实现进度日志 · V1-B1 扫描→报告覆盖提示 · 2026-10-14 13:57 Asia/Shanghai





| 项 | 说明 |


|---|---|


| 实现ID | V1-B1（投研预览旁可读覆盖提示；不标 DATA-01 完成） |


| 改动 | `design/ai-runtime.js`：预览/状态刷新拉 `/api/coverage`，展示聚合状态与缺口字段样例；明确不宣称历史齐全 |


| 备份 | `runtime/code-backups/frontend-v1b1-coverage-tip-20261014T135736` |


| 排期 | 按雪球：优先收口 01扫描 / 02投研 / 03策略（对齐 A 档） |


| 未做 | 现网点验签字；Codex FE-A1/A2 闭环；真 AI 连接 |





### 实现进度日志 · V1-B5 复盘↔结算联调 · 2026-10-14 13:55 Asia/Shanghai





| 项 | 说明 |


|---|---|


| 实现ID | V1-B5（页面：复盘拉 EVAL 结算/成绩；可选冻结版汇总；AI 门禁确认；不标 REVIEW 完成） |


| 改动 | `design/review-runtime.js`：最近结算只读表；grades 统计；数据/AI 复盘可带 version；去掉「二手日历」 |


| 备份 | `runtime/code-backups/frontend-v1b5-review-20261014T135459` |


| 口径 | 已让位于 01/02/03 优先；不挡主链路验收 |





### 实现进度日志 · Codex审计修复 P1/P2 · 2026-10-14 14:05 Asia/Shanghai





| 项 | 说明 |


|---|---|


| 来源 | Codex 给GROK-遮挡排序数据与全链路排查 + 审计报告（已归档 DOCS/design/codex-audit-20261014/） |


| P1 | 已选页签改读 `window.pageRows`；`ai_research` 编辑模板/关注点/检索进证据与模型 payload；`bind_snapshot` 保留字段 |


| P2 | 恢复储备日/7日/连减 + 历史净流可选列；清设计备注；sticky 特异性；空正文「报告暂无正文」；页签按钮左右内边距 |


| 备份 | `runtime/code-backups/frontend-codex-audit-fix-20261014T140254` |


| 口径 | **不标** 01/02/03 完成；欠硬刷验收；真 AI 未点验；服务需加载最新 `ai_research.py` |











### 进度 · 2026-10-14 14:58 · A-FIX-06/07（S1 残留）


- 按 Codex《V2.8-A档迭代需求-审计修复与01-02-03收口》执行。


- 已修：储备 36h 时效清缓存 + 连减不再绕过；历史净流改读 `chainHistory` 顶层元数据。


- 备份：`runtime/code-backups/frontend-a-fix-0607-20261014T145817/`


- 离线复验通过；**现网硬刷待用户验收**。真 AI 未授权不跑。不标 01/02/03 完成。






### 进度 · 2026-10-14 15:12 · A 档 S3（03 策略链路取证，未标完成）

- 证据：`DOCS/design/iteration-v2.8-a-close-s3-evidence.md`（A-03-01～07 隔离失败矩阵 + 8767 只读）。

- 离线：export 指纹/结构指标/冻结执行/覆盖硬闸单测通过；篡改 rules、缺/陈旧 coverage_fp 拒绝且无半成品。

- 线上写需页面同源（脚本 POST 403）；未改业务代码；夹具漂移见证据「残留」。

- **不宣称** 03 或 A 档全部完成；SIM/EVAL/REVIEW/观察层六条/实盘下单后置；未向用户发消息。





### 进度 · 2026-10-14 15:09 · S3 完 / S4 待验


- S3 证据：`iteration-v2.8-a-close-s3-evidence.md`（A-03 离线过，UI 待验）。


- S4 清单：`iteration-v2.8-a-close-s4-pending.md`。真 AI 未授权。不标完成。




### ���� �� 2026-10-14 �� �вü� apply-full

- ɾ������+24h�ߵͣ�����/��ʷ������Ĭ��11�в��䡣Ӳˢ�������á�







### 进度 · 列删量细项三列


- 投研确认后删 barVol/windowVol/baseVol；⑥保留。硬刷验列配置。





### 进度 · 2026-10-14 17:36 · 用户点验


- 已选过；表横滚/sticky/页签过。待：列裁剪确认、投研、策略、真AI授权。






### 进度 · 2026-10-14 19:54 Asia/Shanghai · 投研 R1（v1.1 P1 离线）

- 范围：submission_id 幂等、两阶段冻结、DB 升级路径（DROP research 的 request_hash 活跃唯一索引）、独立确认不复用旧 AI 正文、attempt_id；无付费调用。

- 证据：DOCS/design/iteration-research-r1-v11-evidence.md（RES-01..06 + REV-01/02/04 离线；SHA-256 见该文）。

- 验证：pytest test_ai_research.py test_task_service.py → 43 passed。

- 检索路线备注：R2b 推荐先检索后生成；本轮未实现检索。

- v1.2 CON-01..09 并发/队列/UI **延后**（身份/隔离底线已随 R1 铺好）。

- 未标：完整投研完成、01/02/03 A 档、R2a/R2b/R3、经验库页。



### 进度 · 2026-10-14 20:04 Asia/Shanghai · 投研 R2a（模板 B）

- 范围：标准档模板 B（结构/提示词/schema v2）、旧报告兼容（REV-08）、UI/导出八节（RES-13）、preview→confirm 回传 submission_id/intent_hash；无付费、无 R2b 检索、无 CON 并发。

- 措辞：163m 分类仍 TBD（REV-05）；ATR=0 不自动无效（REV-06）；禁「二手日历」。

- 证据：DOCS/design/iteration-research-r2a-evidence.md；备份 reserve-history/r2a-template-b-backup-20261014-200326/。

- 验证：pytest test_r2a_template_b.py test_ai_research.py → 26 passed。

- UI 需硬刷新（ai-runtime/reports-runtime/prompt-templates/prototype#prompt-data）；服务需重启加载 ai_research/config_store。

- 未标：完整投研、01/02/03 A 档、R2b/R3、经验库页。








### 进度 · 2026-10-14 · 投研 R2b（先检索后生成）

- 范围：REV-03 选定 **先检索后生成**；`research_retrieval.py` 适配器 + `ai_research.run` 先检索登记/冻结再生成输入；fixture 离线证明；live 可选环境变量启用。

- RES-07/08：timeout/404/partial/unimplemented/无新闻 → 真实状态与 gaps；失败不标 online-verified；勾选检索≠已检索。

- 证据：DOCS/design/iteration-research-r2b-evidence.md；备份 `r2b-retrieve-backup-20261014-201009/`。

- 验证：`python -m unittest test_r2b_retrieve_then_generate test_ai_research -q`。

- 启用 live：`RESEARCH_RETRIEVAL_MODE=live` + `RESEARCH_RETRIEVAL_BASE_URL`（可选 API_KEY）；默认 fixture。

- R3 门禁：授权真实供应商调用 + 用户硬刷新点验仍待；未标 A 档完成；无付费 LLM；无经验库页；无 CON 套件。





# requirements 进度追加 · R3（UTF-8 干净副本）



### 进度 · 2026-10-14 · 投研 R3（真实单币标准档）

- 范围：群聊授权后执行 **STRKUSDT / standard / 模板 B** 真实供应商闭环；检索保持 fixture；未跑 CON/经验库。

- 配置：	imeoutSec 90→300（version 7）；服务重启 PID 22716 加载 R2b 后代码。

- 成功：

eport-4dbefb0c984d40879ee9161e80edc9f2，attempt ttempt-45dc9bae8f69430d953c1ab1acfa7e3a，duration_ms=136438，usage=unknown；模板 B 八节 8/8；submission sub-140604470518417cb00d2c4f5eea1636。

- 失败一次：attempt ttempt-e6c8e5304c7b4b1faad760959e5cc47b 本地 >180s 超时 → result_unknown_may_be_billed（未盲重试）。

- 证据：DOCS/design/iteration-research-r3-evidence.md。

- 仍待：用户硬刷新 UI 点验；档位→「深度模板」文案绑定另案；未标 A 档完成；live 检索未启用。







### 进度追加 · 2026-10-14T16:38:44.240804+00:00 · Codex 投研 R9（源码/离线，未标整链验收）


- 按用户授权接手：新投研仅标准 B，保留旧报告/自定义，去除 STRK 固定案例数值，提示词保存接真实配置 hash 冲突检查。


- 现有 gpt-6.1-sol/Key/全局配置不变；同模型 Responses web_search 取证后按已配置协议成文，保存真实工具/来源及分阶段用量，不再默认读取 fixture；供应商真实兼容性尚未验。


- 不合格输出保留查看但任务失败；拒绝策略摘录绕过和未知版本混用；不自动重跑不合格/超时未知结果；补页面预览失效与乱序保护。


- 证据与剩余项：`DOCS/design/iteration-research-r9-standard-native-search.md`，项目外 `C:\Users\ls569\.codex\visualizations\2026\10\12\01a12f09-8e85-72b1-885d-668752d27af6\codex-handover-20261014T160011Z`。


- 本轮无真实 AI/行情调用、无生产数据改写、无服务重启；不标 01/02/03 A 档完成。运行加载、用户页面和真实联网质量分别待验。




### 用户范围调整：用量、经验库与验收分工



本条以用户最新要求为准，覆盖前文冲突的待办范围；既有交付与验收记录保留。



- 用量：用户在中转站查看，不开发项目内用量统计、任务/批次账单页面，也不将其作为收口阻塞。已有原始响应/调用记录保留，供故障定位；未知用量不得虚构为 0，不为补用量另发收费请求。

- 经验库：调整为本轮应完成的软件功能，不再等待积累 3–5 个项目后才开发。最小闭环为报告反馈、候选经验、人工审核/修改、采用/停用、版本追溯，以及新任务显式选择相关经验并冻结所用 ID/版本。先交付代码，后续逐步积累案例；3–5 个项目用于评估效果，不作为编码前置条件。未经审阅的候选经验不得自动改变生产规则或默认提示词，历史数字不得作为新任务当前事实。本条是需求调整，不代表经验库代码已完成。

- 验收分工：Codex 负责源码、离线复现、接口与页面交互、混合批次重试、策略冻结/导入及数据覆盖检查，保留证据和限制。用户不必重复执行整套手动清单；用户主要确认最终报告的可读性和实际用途。真实模型/付费或服务操作按既有授权范围执行，本文不新增调用、重启、扫描或交易授权。

- 完成状态仍区分代码实现、离线通过、实际服务验证与用户体验反馈；不能将模拟检索或模拟页面结果记为真实联网验收。



### 进度追加 · 2026-10-15T03:45:46.975396+00:00 · Codex R10恢复与收尾

- 见 `iteration-research-r10-resume-experience-concurrency.md`。已实现昨夜覆盖/重复导入修复、经验库最小闭环、两个投研槽+一个数据槽；隔离UI和69项最终定向检查通过。

- 新发现并修复HTTP预览/确认扫描设置版本不一致；旧预览仍受配置变化门禁。同ID重传保持幂等。

- 不恢复五分钟自动化；不开发用量页面；生产经验起始空，后续逐步积累。

- 代码/离线/运行加载/真实AI质量分开记录；实际推广、新进程和真实调用结果以项目外本轮审计记录及后续追加为准，不把模拟检索当现网验收。



### R10运行与真实调用结果追加 · 2026-10-15T03:56:03.282777+00:00

- 新服务已加载经验库/并行代码，接口及静态脚本验证通过；任务板与隔离页面链路通过。

- 一次STRK真实新任务在Responses检索返回HTTP400后停止；实际1次模型请求，0次成文、0重试，无新报告，不标真实联网或A档整链验收。错误正文丢失问题已补脱敏留存并离线44项回归；首个失败正文不可追回，计费以中转站为准。后续继续定位供应商真实兼容性。





### 扫描增量 · 2026-10-15 · CoinGecko独立限速

- 见 `iteration-scan-coingecko-pacing.md`。原公共0.125秒节流改为CoinGecko独立预算；保守基准5次/分钟×90%=4.5次/分钟（约13.34秒/次），不是已核实账户额度。

- 429/418按Retry-After至少冷却60秒、后续速率减半，不立即重发；分页中断保留已有结果并继续明确来源的CoinPaprika回退。其他源速率不变。

- 项目外38项相关离线回归通过；本轮不额外扫描，修改后外部429消除尚未现网验证。进度条漂移另案待统一修复。





### 扫描增量 · 2026-10-15 · 待核验自动补查

- 见 `iteration-scan-auto-verification.md`。扫描初轮采集结束后自动补查一轮，仅请求缺失字段；OI/账户比、失败K线/日线、费率周期、市值定向ID查询分别处理。最多16次请求/约180秒，沿用来源限速，429后不追打同来源。

- 数据不足/资产身份歧义/无精确净流来源/历史不足保留明确原因，不降低预热门槛或伪造数据。详情展示补查时间、恢复项、剩余原因。读取页面不会触发补查或AI。

- 69项相关定向离线检查通过；原urllib HTTP拒绝用例出现Windows10053，另行原始HTTP重放5种拒绝情形返回预期403/415。当前快照真实补查及运行加载记录见本轮项目外交付记录；不据此宣称整链验收。





### 扫描增量 · 2026-10-15 · 显式资产映射与指数口径

- 见 `iteration-asset-identity-mapping.md`。带来源的47个项目ID映射落地，可信与候选独立；固定ID无有效值不回退同名项目，指数单币市值/OI市值口径显示不适用。

- 当前扫描r3补回47份，市值528→575/588；4份新候选仍未升级身份。余13份无市值含2指数不适用、11加密合约缺值/缺身份依据。原行情、原已有市值及r1/r2均保留。

- 52项定向与JS语法通过，r3幂等、旧版本保护、本地API和静态资源一致性通过；PID18352运行，扫描关闭，无AI或交易。新版全量扫描和浏览器点验未做，不写整链验收。



### 扫描增量 · 2026-10-15 · 币安Alpha市值来源补齐
- ALLO是Allora，与ALL指数不同。CT/DATAIP在币安Alpha有正有效市值与供应量，先前仅接CG/Paprika导致漏值；现按已核对链/地址独立回退，显示Binance Alpha来源及获取时间，FDV不冒充市值。
- CC经币安指数→Gate/KuCoin官方Canton Network身份链确认。当前r4新增CT、CC、DATAIP两份及STAR共5份，575→580/588；剩6份加密合约缺市值、2份指数不适用。原已有市值/原行情/r1-r3保留。
- 63项定向+JS语法通过，API/静态资源一致，PID4568加载；计划关闭，无AI/交易；未做新版全扫描或浏览器点验。


### 扫描增量 · 2026-10-15 · 全量市值身份与报价口径
- 见 `iteration-market-cap-full-coverage.md`。全量现有资产身份绑定、明确来源ID/链地址；CG改为完整ID分批请求。BTR修正Bitrue Coin错配为Bitlayer，固定Ronin原生身份，修正1MBABYDOGE乘数及非美元报价误标USD。
- r5目标585/586份COIN合约有来源市值，2指数不适用，RATS缺可核验流通量口径仍未完成；capCoverage.all_applicable_trusted=false，不宣称全覆盖。72项离线测试、真实r4副本重放通过；生产接口验证单独记录项目外交付报告。
- 市值源数据时间与原扫描观测时间分别保留；旧revision/行情/K线原归档保留，五份原报价金额纠正美元标记并保留*Quote值。不新增AI调用，不开自动计划，不重跑全扫描。


### 扫描增量 · 2026-10-15 · 每轮市值稳定获取与用户排除
- 用户排除1000RATSUSDT市值缺口及五份指定合约美元换算，原单位值保留；其余585份市值每轮按已核对ID/地址重新采集。范围依当轮交易目录重算，不写死585；实际全部COIN仍保留原分母。
- 见 `iteration-scan-stability-and-exclusions.md`：取消已绑定ID的无用目录请求；独立批次错误隔离、429同轮停来源、市值优先补查且无前120ID截断；校验schema/ID唯一性/流通量与市值/源时点，不以旧值或FDV补齐。缺口标部分完成，任务和页面显示必需覆盖及排除。
- 109项相关离线测试、588行隔离重放通过；09:51 UTC项目外真实市值采集585/585（CG578、Paprika3、Alpha4，约41秒）。这不是全新行情扫描；CTUSDT原4h预热缺口与精确净流未接入仍保留，不宣称整链完成。部署/加载验证见项目外scan-stability-20261015T094526Z，计划保持关闭，无AI/交易。


### 扫描增量 · 2026-10-15 · 储备辅助随扫描自动更新
- 方案见 `iteration-scan-reserves-refresh.md`。扫描发布前采集/检查DefiLlama完整日储备，原生链显式映射、连续8日、36h时效；当天完整证据复用，缺口/失败最多每小时重试，跨UTC日重检。
- 证据与更新失败状态进入不可变扫描revision；页面首次加载/扫描后刷新、筛选排序、详情/数据报告及后端投研hash和输入绑定同一份证据，拒绝旧HTML样本冒充新扫描。
- 已完成相关离线验证及一次项目外公共来源实测：18资产、40合约；LTC缺连续有效余额。Santiment历史样本保留独立日期，精确净流仍未接源，不充当585份全覆盖。
- 已部署并加载PID31988，双端口健康接口确认辅助刷新版本；首轮89项及后续13项定向复核通过（90个不同案例），页面接口绑定通过。真实全市场新扫描联动待下一轮扫描确认；不以离线与来源探测冒充生产全链验收。无AI/交易，不新增计划。部署与详细验证记录见该方案尾部及项目外交付目录。


### 实现进度日志 · EMA D.10 观察与事件时序 · 2026-10-16

本次代码按策略手册D.10落地；字段契约：`DOCS/design/ema-observation-contract-v1.md`。弱级增加新穿/持续根数及历史60根证据；B布局最多两行；两页、详情、CSV、数据报告及投研证据同步。修复跨过T+1误确认、终结复活、周期串联和确认时间混用；恢复仅产生历史证据，不补成交。正式v4/registry/冻结指纹及既有EMA99止损追踪不改。118项禁网离线验证通过；实际运行与页面验收详见策略手册底部D.11及项目外实施报告。未调用真实AI、未发起真实扫描、未交易或开启计划。

运行补记：PID15500；双端口健康版本标记、静态JS hash与两页实际展示已核对。原快照、候选池、registry hash未变化。当前588行中587行有匹配归档观察；未重新扫描或调用AI。详细证据见策略手册D.11末尾。


### 2026-10-16 ⑤指标口径更正与剩余问题修复

用户确认：旧“精确净流”作为第五项属于设计错误，当前⑤改为“聪明钱流入”。此决策覆盖本文旧版本关于当前⑤精确交易所净流的要求；历史日志保留，不能作为当前需求。旧交易所净流来源槽位仅兼容历史，不再列为当前⑤的待完成事项。

- 当前⑤：币安标记聪明钱钱包的4h净流入（USD），正数流入、负数流出、来源明确零值为已知但不命中；默认命中规则>0只表示方向，无异常强度或买入含义。
- 每轮扫描公开取数4次：BSC、Solana、Base、Ethereum各一份4h榜单；最多2并发、12秒单请求超时、无自动重发，不使用API Key或AI。链+地址严格匹配，不按同名代码连接，不假设未公开分页或指定币查询有效。
- 新字段smartMoneyNetInflowUsd / smartMoneyTraders / smartMoneyStatus / smartMoneyStatusNote / smartMoneyEvidence；保留链、地址、period、tagType、抓取时间及身份表hash。提供方未给精确统计边界，windowStart/windowEnd/sourceObservedAt保持null，不拿抓取时间伪造截止时间。
- 未上榜、未核验地址、重复记录、多链多地址歧义、接口失败或采集超过15分钟均为unknown，不填0、不沿用上轮金额、不合并不明链数据。同币多个合约仅关联同一证据，不重复加总。
- 标准注册表升版1.1.0-smart-money-i5；Python与JS同源。表头/筛选/详情/数据报告/CSV/覆盖页同步；旧快照不回填，页面提示重新扫描，旧冻结策略及历史报告不自动迁移。
- netflow保留旧交易所净流依赖；smart_money是新独立依赖，覆盖闸门不会把两者互相替代。兼容旧EXT来源槽位，不把来源登记当真实取数。
- 启动：项目自带IANA tzdata离线后备，系统库优先。直接server.py、start-pages.py及复盘模块统一，不依赖Codex缓存或临时PYTHONTZPATH；缺库在端口绑定和计划恢复前报错。
- 市值：每个来源替换后立即校验价格，再决定下一备用来源；保留20%差异闸门，换源清理旧cap元数据。身份已核验但价格冲突标“市值价格一致性”，不误报重名。
- CT：EMA保持120根门槛；⑥8个独立24h窗只需48根连续已收盘4hK线。新增历史状态、剩余预热根数和预计完成时间；仅有上市元数据吻合时标“上市预热”。预计日期以持续新增有效连续K线为前提，不补造缺失历史。
- 验收：2026-10-15真实聪明钱响应可离线重放；本轮最新公开接口连接超时，实时覆盖数量与当前SIREN备用来源金额尚未验收。不可把旧响应重放写成今天实时成功；既有扫描历史/报告/冻结包保持不改。


### 实现进度日志 · 2026-10-16 UTC · v3.2 r2 Codex接手

用户授权接手GROK中断工作。补齐真实B适配、固定目标调度、同期三态/修订、共享缓存与保护式清理、独立页面同源导出。测试与单币真实只读样例、容量和回退边界见接手交付说明。未开启扫描、B自动观察、AI或交易；未将旧B记录升级成r2完整资格。P5仍须真实连续运行验收。


## 2026-10-17 统一信号工作台 v3.3 r5

实施依据为策略目录03-统一信号工作台-最终优化方案-v3.3-r5.md；实施、兼容、验证及回退见iteration-v3.3-r5-delivery.md。统一公共范围和单一三态组合器，原六项与独立1H B同页；A仍按现行4H，不分页；完整结果选择、导出与组合用法同源。C和新增辅助真实计算仍属后续，明确未接入。发布状态以实际进程指纹与项目外publication/live-verification证据为准，不由文件落地或HTTP200推定。
