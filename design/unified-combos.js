'use strict';
const COMBO_VERSION='unified-combos-v33r5-v32-1011';
const COMBO_GUIDES=[
  {
      "id": "a_confirm_v32",
      "name": "①②④ EMA中/强确认",
      "signals": [
        "i1",
        "i2",
        "i4"
      ],
      "mode": "and",
      "minimum": 1,
      "aStage": "strongmedium",
      "bStage": "complete",
      "cStage": "confirm",
      "thresholds": {
        "tOi": 30,
        "tLsr": 0.8,
        "tApr": -50,
        "tVol": 3,
        "tQuiet": 2
      },
      "purpose": "周期：A=4H。 组合只筛选列表；旧胜率数字已撤回。 关注当前A已生成的中/强确认；对应早期S1的观察意图。",
      "steps": [
        "在当前①②范围内，④选择中/强点火，查看事件归档与确认根。",
        "强：当前EMA7>EMA25>EMA99且上一根EMA25≤EMA99；中：EMA7上穿EMA25且收盘>EMA99。EMA沿用当前窗口首个收盘初始化及递推，至少120根连续闭合，交叉前后根均须有效。",
        "量能按当根或紧接下一根≥2×处理；下一根只需维持对应结构，不要求再次交叉。",
        "参考时序和EMA99退出按A手册，不使用旧固定百分比包或S1收益排名。"
      ],
      "risk": "组合是当前列表筛选，不是新的A入池规则；当前①②不能回填为历史确认时资格。不使用旧稿⑤编号，量能已属于A确认。",
      "source": "币安AI早期S1意图；执行口径以A附录D与v3.2 r2为准",
      "group": "推荐",
      "available": true
    },
  {
      "id": "a_candidate_v32",
      "name": "①②④ EMA候选观察（当前A）",
      "signals": [
        "i1",
        "i2",
        "i4"
      ],
      "mode": "and",
      "minimum": 1,
      "aStage": "active",
      "bStage": "complete",
      "cStage": "confirm",
      "thresholds": {
        "tOi": 30,
        "tLsr": 0.8,
        "tApr": -50,
        "tVol": 3,
        "tQuiet": 2
      },
      "purpose": "周期：A=4H。 组合只筛选列表；旧胜率数字已撤回。 查看当前A活跃候选（含已确认），继续跟踪确认进度。",
      "steps": [
        "④明确选“活跃候选”，使用当前A归档状态，不以单次三线下方判定代替完整入池。",
        "A仍为4H：原RSI 30–55等入池条件、约30天寿命、入池固定OI基数及突破保留规则不变。",
        "到详情区分弱/中/强与确认时间；弱信号只观察，不能当作入场。",
        "未入场候选按现行到期/新低/OI失效规则处理；研究退出依当前EMA99收盘追踪模式。"
      ],
      "risk": "此组合还勾选当前快照①②，可能缩小已在A池中的名单；筛选隐藏不等于候选出池。旧①②④仅看三线下方的组合不自动迁移到本组合。",
      "source": "A现行手册附录D + v3.2 r2；替代旧修复组合的当前版本",
      "group": "推荐",
      "available": true
    },
  {
      "id": "leverage",
      "name": "①② 杠杆＋账户拥挤",
      "signals": [
        "i1",
        "i2"
      ],
      "mode": "and",
      "minimum": 1,
      "aStage": "strongmedium",
      "bStage": "complete",
      "cStage": "confirm",
      "thresholds": {
        "tOi": 30,
        "tLsr": 0.8,
        "tApr": -50,
        "tVol": 3,
        "tQuiet": 2
      },
      "purpose": "组合只筛选列表；旧胜率数字已撤回。 宽范围观察杠杆与账户结构，保留早期S5的等待点火意图。",
      "steps": [
        "筛选 OI/市值 >30%、账户比 <0.80。",
        "核对币种映射与数据时间；再按需要查看A、B、C各自状态。",
        "没有技术确认时继续观察；不得由①②直接生成成交或仓位。"
      ],
      "risk": "不推断庄家持仓，不将其作为B/C必须先入A池的要求。",
      "source": "现有注册表指导；按当前规则整理",
      "group": "推荐",
      "available": true
    },
  {
      "id": "b_confirmed_v32",
      "name": "放量价稳 · 已确认（吸筹背离）",
      "signals": [
        "b"
      ],
      "mode": "and",
      "minimum": 1,
      "aStage": "strongmedium",
      "bStage": "complete",
      "cStage": "confirm",
      "thresholds": {
        "tOi": 30,
        "tLsr": 0.8,
        "tApr": -50,
        "tVol": 3,
        "tQuiet": 2
      },
      "purpose": "周期：B=1H。 组合只筛选列表；旧胜率数字已撤回。 查看技术与同期资格均通过的B确认。",
      "steps": [
        "直接按B完整资格状态筛选，不要求先有A信号。",
        "查看事件h、确认h+1、两份4H背景与证据时间；已知失败优先，缺失是未知。",
        "h+2真实开盘是研究参考；h+1收盘边界正是h+2开盘边界，不额外等一小时。另存实际发现时间和价格。",
        "风险资格P>S0单列；首批仅观察归档，完整确认不自动建立模拟/真实仓位。"
      ],
      "risk": "缺同期证据不能称完整；理论价不冒充成交价。",
      "source": "v3.2 r2 第4章；当前B观察规则",
      "group": "推荐",
      "available": true
    },
  {
      "id": "b_pending_v32",
      "name": "放量价稳 · 待确认（吸筹背离）",
      "signals": [
        "b"
      ],
      "mode": "and",
      "minimum": 1,
      "aStage": "strongmedium",
      "bStage": "pending",
      "cStage": "confirm",
      "thresholds": {
        "tOi": 30,
        "tLsr": 0.8,
        "tApr": -50,
        "tVol": 3,
        "tQuiet": 2
      },
      "purpose": "周期：B=1H。 组合只筛选列表；旧胜率数字已撤回。 等待B事件根h后的真实紧接下一根1H确认。",
      "steps": [
        "B独立扫描：h前168根连续有效；事件量比≥8，同根|C/O−1|<3%，绑定h时已闭合4H背景EMA7>EMA25。",
        "只等待真实h+1，量比≥2，分母含h；确认时重新绑定最近已闭合4H背景。",
        "分别核对h/h+1的OI/市值>30%、账户比<0.80与同期门禁；未知不标完整资格。"
      ],
      "risk": "不从A候选池预过滤；没有h+1确认不能当作完整B信号。",
      "source": "v3.2 r2；对应币安AI旧稿1H背离意图",
      "group": "推荐",
      "available": true
    },
  {
      "id": "b_early_structure",
      "name": "①②＋放量价稳待确认",
      "signals": [
        "i1",
        "i2",
        "b"
      ],
      "mode": "and",
      "minimum": 1,
      "aStage": "strongmedium",
      "bStage": "pending",
      "cStage": "confirm",
      "thresholds": {
        "tOi": 30,
        "tLsr": 0.8,
        "tApr": -50,
        "tVol": 3,
        "tQuiet": 2
      },
      "purpose": "周期：B=1H。 组合只筛选列表；旧胜率数字已撤回。 保留币安AI早期S3的“结构＋1H背离早期观察”意图。",
      "steps": [
        "使用B待确认事件，不把工作台⑥24H量增价平替代旧稿1H背离。",
        "①②在本页面是当前快照筛选；仍须独立检查B事件时的同期资格。",
        "紧接h+1确认成立后，可切换到“B完整确认”继续观察。"
      ],
      "risk": "当前快照①②不能证明h时或h+1时资格；此组合是待确认观察，不声称已证实提前8–20小时。",
      "source": "币安AI v3.0 S3意图；按v3.2改用B身份",
      "group": "推荐",
      "available": true
    },
  {
      "id": "squeeze",
      "name": "①②③ 空头拥挤观察",
      "signals": [
        "i1",
        "i2",
        "i3"
      ],
      "mode": "and",
      "minimum": 1,
      "aStage": "strongmedium",
      "bStage": "complete",
      "cStage": "confirm",
      "thresholds": {
        "tOi": 30,
        "tLsr": 0.8,
        "tApr": -50,
        "tVol": 3,
        "tQuiet": 2
      },
      "purpose": "组合只筛选列表；旧胜率数字已撤回。 寻找杠杆密度高、空头账户比例偏高并伴随负费率的观察标的。",
      "steps": [
        "先筛选 OI/市值 >30%、账户多空比 <0.80、已核验周期简单年化 <-50%。",
        "检查数据来源、观测时间与缺失项；进入币种详情看4H结构。",
        "后续关注A中/强点火和量能确认；只命中①②③仍属观察。"
      ],
      "risk": "负费率可能持续伴随下跌；账户数比不代表持仓金额。③在A基线中是辅助项，本组合主动勾选③不改变A规则。",
      "source": "现有注册表指导；按当前规则整理",
      "group": "更多观察",
      "available": true
    },
  {
      "id": "fundingAccounts",
      "name": "②③ 账户＋费率",
      "signals": [
        "i2",
        "i3"
      ],
      "mode": "and",
      "minimum": 1,
      "aStage": "strongmedium",
      "bStage": "complete",
      "cStage": "confirm",
      "thresholds": {
        "tOi": 30,
        "tLsr": 0.8,
        "tApr": -50,
        "tVol": 3,
        "tQuiet": 2
      },
      "purpose": "组合只筛选列表；旧胜率数字已撤回。 仅观察账户结构与负费率重合的标的。",
      "steps": [
        "查看账户比 <0.80及年化 <-50%的确定命中项。",
        "再看量价、技术结构及缺失证据；不把账户与费率重合作为入场。"
      ],
      "risk": "旧稿“所有费率组合废弃”不是当前规则；此项保留为筛选观察，没有收益结论。",
      "source": "现有注册表指导；按当前规则整理",
      "group": "更多观察",
      "available": true
    },
  {
      "id": "fundingVolume",
      "name": "③⑥ 负费率＋量增价平",
      "signals": [
        "i3",
        "i6"
      ],
      "mode": "and",
      "minimum": 1,
      "aStage": "strongmedium",
      "bStage": "complete",
      "cStage": "confirm",
      "thresholds": {
        "tOi": 30,
        "tLsr": 0.8,
        "tApr": -50,
        "tVol": 3,
        "tQuiet": 2
      },
      "purpose": "⑥周期：24H。 组合只筛选列表；旧胜率数字已撤回。 寻找负费率与24H量增价平同时出现的观察标的。",
      "steps": [
        "③按实际结算周期计算年化；⑥是24H量倍数 >3且同窗绝对价变 <2%。",
        "若要研究区间突破，先固定触发前区间，再核验突破与主动买盘/现货证据。",
        "缺少固定区间、确认或退出参数时，只记录组合命中，不称T3完整触发。"
      ],
      "risk": "高成交量也可能来自对赌或派发；⑥不是B的1H量比。区间与退出参数待独立冻结。 ⑥是24H量增价平，不是1H吸筹背离。",
      "source": "现有注册表指导；按当前规则整理",
      "group": "更多观察",
      "available": true
    },
  {
      "id": "a_quiet_v32",
      "name": "④⑥ EMA确认＋量增价平",
      "signals": [
        "i4",
        "i6"
      ],
      "mode": "and",
      "minimum": 1,
      "aStage": "strongmedium",
      "bStage": "complete",
      "cStage": "confirm",
      "thresholds": {
        "tOi": 30,
        "tLsr": 0.8,
        "tApr": -50,
        "tVol": 3,
        "tQuiet": 2
      },
      "purpose": "⑥周期：24H。 组合只筛选列表；旧胜率数字已撤回。 在当前A中/强确认范围内叠加24H量价观察。",
      "steps": [
        "④使用当前A中/强放量确认；候选观察另有专用组合，不以三线下方快照冒充金叉。",
        "⑥使用独立24H观察；分别查看两项数据时间。",
        "继续等待A中/强确认；此组合既不等于T4持续吸筹，也不等于B的1H事件。"
      ],
      "risk": "新版本有独立ID；旧quiet组合保留历史说明，不静默迁移。 ⑥是24H量增价平，不是1H吸筹背离。",
      "source": "原④⑥组合意图；④按当前A金叉分级更新，独立新ID",
      "group": "更多观察",
      "available": true
    },
  {
      "id": "b_smart",
      "name": "聪明钱＋放量价稳确认",
      "signals": [
        "i5",
        "b"
      ],
      "mode": "and",
      "minimum": 1,
      "aStage": "strongmedium",
      "bStage": "complete",
      "cStage": "confirm",
      "thresholds": {
        "tOi": 30,
        "tLsr": 0.8,
        "tApr": -50,
        "tVol": 3,
        "tQuiet": 2
      },
      "purpose": "周期：B=1H。 组合只筛选列表；旧胜率数字已撤回。 在独立B完整确认中增加聪明钱辅助观察。",
      "steps": [
        "先有B完整资格，再查看聪明钱已核验4H净流入>0。",
        "未覆盖聪明钱时显示未知；也可以移除⑤查看完整B名单。"
      ],
      "risk": "这是额外的列表交集示例，不将聪明钱升格为B共同硬门槛。 ⑤聪明钱是辅助，不是A的量能确认；本组合无收益验证。",
      "source": "本轮界面组合示例；无收益验证",
      "group": "更多观察",
      "available": true
    },
  {
      "id": "all6_v32",
      "name": "六项同时满足（当前④）",
      "signals": [
        "i1",
        "i2",
        "i3",
        "i4",
        "i5",
        "i6"
      ],
      "mode": "and",
      "minimum": 1,
      "aStage": "strongmedium",
      "bStage": "complete",
      "cStage": "confirm",
      "thresholds": {
        "tOi": 30,
        "tLsr": 0.8,
        "tApr": -50,
        "tVol": 3,
        "tQuiet": 2
      },
      "purpose": "组合只筛选列表；旧胜率数字已撤回。 查看原六类信号全部明确命中的交集。",
      "steps": [
        "本组合④采用当前A中/强放量确认；⑤始终是聪明钱4H净流入，不是旧稿量能确认。",
        "六项全部命中才通过；未覆盖聪明钱等缺失项为未知，不能凑成6/6。",
        "逐项查看周期和时间；不把跨周期快照交集称为同一时刻的交易确认。"
      ],
      "risk": "条件严不代表更有效；该组合不建立评分到仓位的映射。 条件极严，仅研究用，不作推荐主列。",
      "source": "原六项组合；④按当前金叉分级，独立新ID",
      "group": "更多观察",
      "available": true
    },
  {
      "id": "none",
      "name": "不限制信号",
      "signals": [],
      "mode": "and",
      "minimum": 1,
      "aStage": "strongmedium",
      "bStage": "complete",
      "cStage": "confirm",
      "thresholds": {
        "tOi": 30,
        "tLsr": 0.8,
        "tApr": -50,
        "tVol": 3,
        "tQuiet": 2
      },
      "purpose": "组合只筛选列表；旧胜率数字已撤回。 只按顶部公共范围查看合约。",
      "steps": [
        "不勾选信号；搜索、市场、市值等条件继续生效。",
        "需要进一步观察时，手动勾选任意信号，选择全部/任一/至少N项。"
      ],
      "risk": "不限制信号不代表全部策略合格或可执行。",
      "source": "现有注册表指导；按当前规则整理",
      "group": "更多观察",
      "available": true
    },
  {
      "id": "c_waiting_v32",
      "name": "超卖反弹候选（C）",
      "signals": [
        "c"
      ],
      "mode": "and",
      "minimum": 1,
      "aStage": "strongmedium",
      "bStage": "complete",
      "cStage": "waiting",
      "thresholds": {
        "tOi": 30,
        "tLsr": 0.8,
        "tApr": -50,
        "tVol": 3,
        "tQuiet": 2
      },
      "purpose": "C独立影子追踪。 组合只筛选列表；旧胜率数字已撤回。 预览独立C候选的等待确认过程。",
      "steps": [
        "候选：已收盘4H Wilder RSI(14)<25、收盘低于三线、OI/市值>30%、LSR<1.5、最近14根严格收盘新低≤2。独立于A/B。",
        "T0固定；确认只看T+1～T+10含边界，q/前7根4H均值>3且阳线。不要求确认时RSI仍超卖或仍在三线下方；先处理明确失效。",
        "LSR连续两根≥1.5失效，非连续不累计；缺失阻断确认但寿命不暂停。",
        "T+10先判断有效确认，再到期；到期/失效/确认后，先观察完整资格明确不满足再重新满足才重武装。",
        "未来C模拟退出才使用入场后4H收盘<入场价×0.85或4H量比<1，次开退出；等号不触发。本工作台不实施模拟。"
      ],
      "risk": "C只有追踪启用、同期完整来源及当前边界资格均满足时才作为当前筛选命中；历史观察另列，生产验收状态独立。",
      "source": "v3.2 r2；币安AI旧稿S4只作意图参考",
      "group": "更多观察",
      "available": true
    },
  {
      "id": "abc_any",
      "name": "EMA／B／C任一",
      "signals": [
        "i4",
        "b",
        "c"
      ],
      "mode": "or",
      "minimum": 1,
      "aStage": "strongmedium",
      "bStage": "complete",
      "cStage": "confirm",
      "thresholds": {
        "tOi": 30,
        "tLsr": 0.8,
        "tApr": -50,
        "tVol": 3,
        "tQuiet": 2
      },
      "purpose": "组合只筛选列表；旧胜率数字已撤回。 在同一列表查看任一中/强A确认、完整B确认或C确认（按当前边界核验）。",
      "steps": [
        "选择任一命中，命中其中一个即可；一行一个合约保留各自状态。",
        "进详情核对各策略周期、时间与资格；B不依赖A；C独立影子计算，启用和数据就绪见各记录。"
      ],
      "risk": "此组合无共同入场与退出规则；不合并A/B/C事件ID。 界面研究示例，无收益验证；追踪未启用或数据过期时不生成C当前命中。",
      "source": "本轮界面组合示例；无收益验证",
      "group": "更多观察",
      "available": true
    },
  {
      "id": "s2_history",
      "name": "S2 金叉已形成、量待确认（待字段）",
      "signals": [],
      "mode": "and",
      "minimum": 1,
      "aStage": "strongmedium",
      "bStage": "complete",
      "cStage": "confirm",
      "thresholds": {
        "tOi": 30,
        "tLsr": 0.8,
        "tApr": -50,
        "tVol": 3,
        "tQuiet": 2
      },
      "purpose": "组合只筛选列表；旧胜率数字已撤回。 保留币安AI早期S2意图，避免把候选或弱信号错误替代量待确认。",
      "steps": [
        "当前工作台未提供“结构成立且量待确认”的独立归档字段。",
        "接入前先绑定A最新版对应事件与仅下一根确认窗口；不能使用所有活跃候选充数。",
        "本条仅保留说明，暂不提供一键筛选。"
      ],
      "risk": "不照搬旧金叉算法、SMA首值初始化、低量放宽或收益排名。",
      "source": "币安AI v3.0 S2；尚未形成本工作台可用字段",
      "group": "历史对照",
      "available": false
    }
];

if(typeof module!=='undefined')module.exports={COMBO_VERSION,COMBO_GUIDES};
