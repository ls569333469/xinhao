/* A布局 + C专业表格 · 第一步扫描表增强。接真实快照字段；不触发扫描/AI。 */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  if (!$('[data-view="market"]') || typeof renderMarket !== 'function') return;

  const STORAGE_COLS = 'workbench-a-pro-columns-v4';
  const DEFAULT_COLS = ['cap', 'hits', 'oiUsd', 'oi', 'ratio', 'funding', 'ema', 'flow', 'vol', 'change', 'volume'];
  const COL_WIDTHS = {
    select: 40, contract: 160,
    cap: 130, hits: 140, oiUsd: 130, oi: 110, ratio: 110, funding: 170,
    ema: 200, flow: 170, vol: 120, change: 110, volume: 130,
    reserveDay: 120, reserveWeek: 120, reserveStreak: 120, histFlow: 150,
    mark: 110, index: 110,
     ema7: 110, ema25: 110, ema99: 110,
    closed: 120, assetOi: 130, time: 200
  };
  const ALL_COLS = [
    'cap', 'hits', 'oiUsd', 'oi', 'ratio', 'funding', 'ema', 'flow', 'vol', 'change', 'volume',
    'mark', 'index',
    'ema7', 'ema25', 'ema99', 'closed', 'assetOi', 'time',
    'reserveDay', 'reserveWeek', 'reserveStreak', 'histFlow'
  ];
  const REMOVED_COLS = new Set(['topAccount', 'topPosition', 'high', 'low', 'barVol', 'windowVol', 'baseVol']);
function emaLifecycleLabel(r){
  const st=r&&r.emaSqueezeStage;
  if(!st||typeof st!=='object')return '未记录';
  if(!Object.prototype.hasOwnProperty.call(st,'candidateStatus')||st.candidateStatus==null)return '未记录';
  const conf=st.crossConfirm;
  if(cycleConfirms(r).length)return '点火已确认';
  if(conf&&conf.void)return '已失效';
  if(st.candidateStatus==='exited')return '已失效';
  if(st.pendingCross)return '等待放量';
  if(st.candidateStatus==='active'){
    // 已有真实入池状态时，用位置细分阶段；无入池状态时绝不单凭三线位置编造生命周期
    if(r.belowAllEma===true)return '蓄力中';
    return '等待金叉';
  }
  return '未记录';
}
function emaGradeLabel(g){
  return g==='strong'?'强🟢':g==='medium'?'中🟡':g==='weak'?'弱🔴勿入场':g==='none'?'无':(g?'未知':'未记录');
}

  function sanitizeCols(arr) {
    const allow = new Set(ALL_COLS);
    const out = [];
    (arr || []).forEach(function (k) {
      if (REMOVED_COLS.has(k)) return;
      if (allow.has(k) && out.indexOf(k) < 0) out.push(k);
    });
    return out.length ? out : DEFAULT_COLS.slice();
  }
  const HEADERS = {
    cap: '流通市值', hits: '命中 / 可算 / 未知', oiUsd: '本合约 OI', oi: '① OI / 市值',
    ratio: '② 全体账户比', funding: '③ 当前费率 / 年化', ema: '④ EMA三线金叉', flow: '⑤ 聪明钱流入',
    vol: '⑥ 观察窗量倍数', change: '24h 涨跌', volume: '24h 成交额',
    reserveDay: '币安余额日变', reserveWeek: '币安余额7日变', reserveStreak: '币安余额连降天数', histFlow: '历史净流样本（非当前⑤）',
    mark: '标记价格', index: '指数价格', ema7: 'EMA7 · 4h', ema25: 'EMA25 · 4h', ema99: 'EMA99 · 4h',
    closed: '已收盘4h', assetOi: '同资产总 OI', time: '观测时间'
  };
  const SORT_KEYS = {
    cap: 'capUsd', hits: 'hits', oiUsd: 'oiUsd', oi: 'oiCapPct', ratio: 'accountRatio',
    funding: 'fundingAprPct', ema: 'closed4h', flow: 'flowSignal', vol: 'volumeRatio', barVolumeRatio7: 'barVolumeRatio7', barVolumeRatio: 'barVolumeRatio',
    change: 'priceChange24Pct', volume: 'volume24Usd',
    reserveDay: 'reserveDayPct', reserveWeek: 'reserveWeekPct', reserveStreak: 'reserveStreakDays', histFlow: 'histFlowUsd',
    mark: 'markPrice',
    index: 'indexPrice',
    ema7: 'ema7', ema25: 'ema25', ema99: 'ema99', closed: 'closed4h',
    assetOi: 'binanceAssetOiUsd', time: 'asOf'
  };
  const FIELD_GROUPS = [
    ['合约行情', ['price', 'markPrice', 'indexPrice', 'priceChange24Pct', 'high24', 'low24', 'volume24Usd']],
    ['衍生品数据', ['oiUsd', 'oiCapPct', 'accountRatio', 'topAccountRatio', 'topPositionRatio', 'fundingPct', 'fundingHours', 'fundingAprPct', 'fundingIntervalSource']],
    ['量价与均线', ['volumeRatio', 'priceChangeWindowPct', 'barVolumeRatio', 'barVolumeRatio7', 'volumeWindowUsd', 'volumeBaselineUsd', 'closed4h', 'ema4h', 'emaMethod', 'ignitionGrade', 'rsi4h', 'sidewaysOk', 'emaQuality', 'candleHistoryStatus', 'emaWarmupRemaining', 'emaWarmupExpectedAt', 'candleFirstOpenAt', 'contractOnboardAt']],
    ['市值与身份', ['capUsd', 'capName', 'capId', 'capSource', 'capStatus', 'capTrusted', 'capIdentityVerified', 'capPriceCheckStatus', 'capQualityStatus', 'capMappingRevision', 'capIdentityBasis', 'capAsOf', 'capTimeNote', 'capCirculatingSupply']],
    ['同资产持仓（独立口径）', ['binanceAssetOiUsd', 'binanceAssetOiPartialUsd', 'binanceAssetOiCapPct', 'assetOiCoverage']],
    ['数据补查', ['dataVerification', 'quoteUnitStatus', 'oiQuote', 'volume24Quote', 'volumeWindowQuote', 'volumeBaselineQuote']],
    ['EMA观察档案', ['emaObservation']],
    ['聪明钱流入', ['smartMoneyNetInflowUsd', 'smartMoneyTraders', 'smartMoneyStatus', 'smartMoneyStatusNote', 'smartMoneyEvidence']],
    ['观测时间与限制', ['asOf', 'oiAsOf', 'accountAsOf', 'candleAsOf', 'source', 'chainStatus', 'tradeStatus', 'observed_at']]
  ];
  const FIELD_NAMES = {
    emaObservation: '三线金叉观察数据（含来源、质量与时间）',
    quoteUnitStatus: '报价单位说明', oiQuote: '原报价单位OI', volume24Quote: '原报价24h成交额', volumeWindowQuote: '原报价观察窗成交额', volumeBaselineQuote: '原报价基准成交额', price: '快照价格', markPrice: '标记价格', indexPrice: '指数价格', priceChange24Pct: '24h涨跌（%）',
    high24: '24h最高价', low24: '24h最低价', volume24Usd: '24h成交额（美元）', oiUsd: '本合约OI（美元）',
    oiCapPct: 'OI/市值（%）', accountRatio: '全体账户多空比', topAccountRatio: '大户账户多空比',
    topPositionRatio: '大户持仓量多空比', fundingPct: '当前资金费率（%）', fundingHours: '结算周期（h）',
    fundingAprPct: '简单年化（%）', fundingIntervalSource: '周期核验来源', volumeRatio: '观察窗量倍数(24h对齐窗)',
    priceChangeWindowPct: '同窗价变（%）', barVolumeRatio: '前20根单根量倍数', barVolumeRatio7: '前7根点火量倍数', ignitionGrade: 'I4b点火分级', rsi4h: '4h RSI', sidewaysOk: '横盘可观察', emaQuality: 'EMA窗质量', volumeWindowUsd: '观察窗成交额（美元）',
    volumeBaselineUsd: '基准窗成交额（美元）', closed4h: '已收盘4h价格', ema4h: 'EMA7/25/99',
    candleHistoryStatus:'K线历史状态', emaWarmupRemaining:'EMA预热仍需4h根数', emaWarmupExpectedAt:'EMA预计预热完成时间（毫秒）', candleFirstOpenAt:'首根4h开盘时间（毫秒）', contractOnboardAt:'合约上市时间（毫秒）', emaMethod: '均线计算口径', capUsd: '流通市值（美元）', capName: '市值项目名称', capId: '市值项目ID',
    capIdentityVerified: '项目身份已核对', capPriceCheckStatus: '价格校验状态', capQualityStatus: '市值口径说明', capSource: '市值来源', capStatus: '映射状态', capTrusted: '资产映射已核对', capMappingRevision: '映射版本', capIdentityBasis: '身份依据', capTimeNote: '市值时间说明', capCirculatingSupply: '流通供应量', capAsOf: '市值时间',
    binanceAssetOiUsd: '完整资产总OI（美元）', binanceAssetOiPartialUsd: '已收部分OI（美元）',
    binanceAssetOiCapPct: '完整资产OI/市值（%）', assetOiCoverage: '同资产OI覆盖', asOf: '合约观测时间',
    oiAsOf: 'OI时点', accountAsOf: '账户比时点', candleAsOf: '已收盘K线时间', source: '合约数据源',
    smartMoneyNetInflowUsd: '聪明钱4h净流入USD', smartMoneyTraders: '聪明钱交易地址数', smartMoneyStatus: '聪明钱覆盖状态', smartMoneyStatusNote: '聪明钱来源说明', smartMoneyEvidence: '聪明钱链、地址、窗口与来源证据', chainStatus: '历史交易所净流来源状态', tradeStatus: '执行状态', observed_at: '行观测时间', dataVerification: '补查时间、恢复项与未解决原因'
  };

  const present = v => typeof v === 'number' && Number.isFinite(v);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (v, n = 2) => present(v) ? v.toLocaleString('en-US', { maximumFractionDigits: n }) : '未知';
  const money = v => {
    if (!present(v)) return '未知';
    if (Math.abs(v) >= 1e8) return '$' + fmt(v / 1e8) + '亿';
    if (Math.abs(v) >= 1e4) return '$' + fmt(v / 1e4) + '万';
    return '$' + fmt(v);
  };
  const price = (v, quote) => present(v) ? (typeof quote === 'string' && !['USD', 'USDT', 'USDC'].includes(quote) ? Number(v).toLocaleString('en-US', { maximumSignificantDigits: 6 }) + ' ' + esc(quote) : '$' + Number(v).toLocaleString('en-US', { maximumSignificantDigits: 6 })) : '未知';
  const pct = (v, n = 2) => present(v) ? fmt(v, n) + '%' : '未知';
  const unknownWhy = (label, why) => `<span class="cell-unknown">${esc(label)}</span><div class="coin-sub">${esc(why || '快照无有效数值')}</div>`;

  function loadColumns() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_COLS) || 'null');
      if (Array.isArray(saved) && saved.length) {
        const cleaned = sanitizeCols(saved);
        if (cleaned.length && cleaned.every(k => HEADERS[k])) return new Set(cleaned);
      }
    } catch (_) {}
    return new Set(DEFAULT_COLS);
  }
  let visibleCols = loadColumns();
  const saveColumns = () => { try { localStorage.setItem(STORAGE_COLS, JSON.stringify([...visibleCols])); } catch (_) {} };

  function signalFlags(r) {
    if (typeof flags === 'function') return flags(r);
    if (Array.isArray(r.signals)) return r.signals.map(v => v === true ? true : v === false ? false : null);
    return StrategyRegistry.signals(r);
  }
  function realHitCount(r) {
    return signalFlags(r).filter(v => v === true).length;
  }
  function realKnownCount(r) {
    return signalFlags(r).filter(v => v !== null).length;
  }
  function realUnknownCount(r) {
    return 6 - realKnownCount(r);
  }


  function reserveRec(r) {
    try {
      if (typeof reservesByAsset !== 'undefined' && reservesByAsset && typeof reservesByAsset.get === 'function') {
        return reservesByAsset.get(r.asset) || null;
      }
    } catch (_) {}
    return null;
  }
  function histRec(r) {
    try {
      if (typeof historicalFlowsByAsset !== 'undefined' && historicalFlowsByAsset && typeof historicalFlowsByAsset.get === 'function') {
        return historicalFlowsByAsset.get(r.asset) || null;
      }
    } catch (_) {}
    return null;
  }
  function clearReserveCache(r) {
    if (!r) return;
    delete r.reserveDayPct;
    delete r.reserveWeekPct;
    delete r.reserveStreakDays;
  }
  function histMeta() {
    const top = (typeof chainHistory !== 'undefined' && chainHistory) ? chainHistory : null;
    if (!top || typeof top !== 'object') return { provider: '', windowFrom: '', windowTo: '' };
    return {
      provider: top.provider ? String(top.provider) : '',
      windowFrom: top.windowFrom ? String(top.windowFrom) : '',
      windowTo: top.windowTo ? String(top.windowTo) : ''
    };
  }
  function ensureSupplementalFields(r) {
    if (!r) return r;
    const p = reserveRec(r);
    if (p) {
      const fresh = (typeof reserveFresh === 'function') ? !!reserveFresh(p) : true;
      if (fresh) {
        if (present(p.dayChangePct)) r.reserveDayPct = p.dayChangePct; else delete r.reserveDayPct;
        if (present(p.weekChangePct)) r.reserveWeekPct = p.weekChangePct; else delete r.reserveWeekPct;
        if (present(p.consecutiveDecreaseDays)) r.reserveStreakDays = p.consecutiveDecreaseDays; else delete r.reserveStreakDays;
      } else {
        // 过期/未来/不新鲜：清掉旧缓存，避免假有效值
        clearReserveCache(r);
      }
    } else {
      clearReserveCache(r);
    }
    const h = histRec(r);
    if (h && Array.isArray(h.days) && h.days.length) {
      // 历史净流累计：供应商口径样本，不是当前精确⑤；每次重算，不保留旧缓存
      let sum = 0; let any = false;
      h.days.forEach(function (d) {
        if (present(d.netInflowUsd)) { sum += d.netInflowUsd; any = true; }
        else if (present(d.netInflowTokens)) { /* token 单位：不混为美元，留给展示 */ }
      });
      if (any) r.histFlowUsd = sum; else delete r.histFlowUsd;
    } else {
      delete r.histFlowUsd;
    }
    return r;
  }

  function metric(r, k) {
    const f = signalFlags(r);
    if (k === 'cap') {
      if (r.underlyingType === 'INDEX') return unknownWhy('不适用', '指数不采用单币流通市值');
      if (!present(r.capUsd)) return unknownWhy('未知', r.capStatus || '市值缺失，进入待核验');
      const capSourceNote = r.capSource === 'Binance Alpha' ? '<div class="coin-sub" title="来源未提供观察时间，详情记录获取时间">币安 Alpha</div>' : '';
      return `${money(r.capUsd)}${capSourceNote}${r.capTrusted ? '' : '<div class="coin-sub">映射待核对</div>'}`;
    }
    if (k === 'hits') {
      const dots = f.map(x => `<i class="dot ${x === true ? 'on' : x == null ? 'unknown' : ''}"></i>`).join('');
      return `<span class="hitval">${realHitCount(r)}<small> / 6</small></span><div class="coin-sub">${realKnownCount(r)}项可算 · ${realUnknownCount(r)}项未知</div><div class="hitdots" aria-hidden="true">${dots}</div>`;
    }
    if (k === 'oiUsd') return present(r.oiUsd) ? money(r.oiUsd) : unknownWhy('未知', '本合约OI缺失');
    if (k === 'oi' && r.underlyingType === 'INDEX') return unknownWhy('不适用', '指数不采用单币OI/市值');
    if (k === 'oi') return present(r.oiCapPct) ? `<span class="${f[0] === true ? 'cell-hit' : ''}">${pct(r.oiCapPct, 1)}</span>` : unknownWhy('未知', '无法计算OI/市值');
    if (k === 'ratio') return present(r.accountRatio) ? `<span class="${f[1] === true ? 'cell-hit' : ''}">${fmt(r.accountRatio, 3)}</span>` : unknownWhy('未知', '账户比缺失');
    if (k === 'funding') {
      if (!present(r.fundingPct) && !present(r.fundingAprPct)) return unknownWhy('未知', r.fundingIntervalStatus || '费率缺失');
      const hours = present(r.fundingHours) ? (fmt(r.fundingHours) + 'h') : '';
      return `<span class="${f[2] === true ? 'cell-hit' : ''}">${pct(r.fundingAprPct, 1)}<small> 年化</small></span><div class="coin-sub">${pct(r.fundingPct, 4)}${hours ? (' · ' + hours) : ''}</div>`;
    }
    if (k === 'ema') return MarketEvidence.emaSummaryHtml(r);
    if (k === 'flow') {
      if (f[4] == null) return unknownWhy('未覆盖', r.smartMoneyStatusNote || '旧快照未采集聪明钱数据');
      return `<span class="${f[4] ? 'cell-hit' : ''}">${money(r.smartMoneyNetInflowUsd)}</span><div class="coin-sub">4h · ${r.smartMoneyTraders}个地址</div>`;
    }
    if (k === 'vol') {
      if (!present(r.volumeRatio)) return unknownWhy('未知', '观察窗量倍数缺失');
      return `<span class="${f[5] === true ? 'cell-hit' : ''}">${fmt(r.volumeRatio)}×</span><div class="coin-sub">同窗 ${pct(r.priceChangeWindowPct)}</div>`;
    }
    if (k === 'change') {
      if (!present(r.priceChange24Pct)) return unknownWhy('未知', '24h涨跌缺失');
      const cls = r.priceChange24Pct >= 0 ? 'positive' : 'negative';
      return `<span class="${cls}">${r.priceChange24Pct > 0 ? '+' : ''}${pct(r.priceChange24Pct)}</span>`;
    }
    if (k === 'volume') return present(r.volume24Usd) ? money(r.volume24Usd) : unknownWhy('未知', '24h成交额缺失');
    if (k === 'mark') return price(r.markPrice, r.quote);
    if (k === 'index') return price(r.indexPrice, r.quote);
    if (k === 'ema7') return price(r.ema4h?.[0], r.quote);
    if (k === 'ema25') return price(r.ema4h?.[1], r.quote);
    if (k === 'ema99') return price(r.ema4h?.[2], r.quote);
    if (k === 'closed') return price(r.closed4h, r.quote);
    if (k === 'assetOi') {
      if (present(r.binanceAssetOiUsd)) return money(r.binanceAssetOiUsd);
      return unknownWhy('完整总量未知', '已收部分 ' + money(r.binanceAssetOiPartialUsd));
    }
    if (k === 'reserveDay') {
      ensureSupplementalFields(r);
      if (!present(r.reserveDayPct)) return unknownWhy('未知', '无新鲜储备映射或不在36h内');
      return pct(r.reserveDayPct, 3) + '<div class="coin-sub">币安局部储备·非精确净流</div>';
    }
    if (k === 'reserveWeek') {
      ensureSupplementalFields(r);
      if (!present(r.reserveWeekPct)) return unknownWhy('未知', '无新鲜储备映射或不在36h内');
      return pct(r.reserveWeekPct, 3) + '<div class="coin-sub">7日数量变化</div>';
    }
    if (k === 'reserveStreak') {
      ensureSupplementalFields(r);
      if (!present(r.reserveStreakDays)) return unknownWhy('未知', '储备时效外或无连减统计（36h内）');
      return fmt(r.reserveStreakDays, 0) + '日<div class="coin-sub">连续下降 · 36h内有效</div>';
    }
    if (k === 'histFlow') {
      ensureSupplementalFields(r);
      const h = histRec(r);
      if (!h || !Array.isArray(h.days) || !h.days.length) return unknownWhy('未知', '无历史净流样本');
      const meta = histMeta();
      const from = meta.windowFrom || '';
      const to = meta.windowTo || '';
      const src = meta.provider || '';
      const range = (from || to)
        ? (esc(String(from).slice(0, 10)) + '～' + esc(String(to).slice(0, 10)))
        : '区间未知';
      const srcLabel = src ? esc(src) : '来源未知';
      let usdN = 0; let tokN = 0; let tok = 0;
      h.days.forEach(function (d) {
        if (present(d.netInflowUsd)) usdN++;
        if (present(d.netInflowTokens)) { tok += d.netInflowTokens; tokN++; }
      });
      const total = h.days.length;
      const gapNote = (usdN && usdN < total)
        ? ('有效' + usdN + '/' + total + '日')
        : ((tokN && tokN < total) ? ('有效' + tokN + '/' + total + '日') : (total + '日样本'));
      const sub = srcLabel + ' · ' + range + ' · ' + gapNote + ' · 累计口径 · 非当前⑤';
      if (present(r.histFlowUsd)) {
        return money(r.histFlowUsd) + '<div class="coin-sub">' + sub + '</div>';
      }
      if (!tokN) return unknownWhy('未知', '历史净流无有效日值');
      return fmt(tok, 2) + ' ' + esc(r.asset || '') + '<div class="coin-sub">' + sub + '</div>';
    }
    if (k === 'time') return r.asOf ? esc(String(r.asOf).replace('T', ' ').replace('+00:00', ' UTC')) : unknownWhy('未知', '观测时间缺失');
    return '—';
  }

  function sortValue(r, sortKey) {
    if (sortKey === 'reserveDayPct' || sortKey === 'reserveWeekPct' || sortKey === 'reserveStreakDays' || sortKey === 'histFlowUsd') {
      ensureSupplementalFields(r);
    }
    if (sortKey === 'hits') return realHitCount(r);
    // EMA7/25/99 live in ema4h[]; row has no ema7/25/99 props
    if (sortKey === 'ema7') {
      const v = Array.isArray(r.ema4h) ? r.ema4h[0] : null;
      return present(v) ? v : null;
    }
    if (sortKey === 'ema25') {
      const v = Array.isArray(r.ema4h) ? r.ema4h[1] : null;
      return present(v) ? v : null;
    }
    if (sortKey === 'ema99') {
      const v = Array.isArray(r.ema4h) ? r.ema4h[2] : null;
      return present(v) ? v : null;
    }
    // flowSignal: prefer field, else derive from signals/flags[4]
    if (sortKey === 'flowSignal') {
      if (typeof r.flowSignal === 'number' && Number.isFinite(r.flowSignal)) return r.flowSignal;
      if (typeof r.flowSignal === 'boolean') return r.flowSignal ? 1 : 0;
      const f = signalFlags(r);
      const v = f[4];
      return v === true ? 1 : v === false ? 0 : null;
    }
    if (Object.prototype.hasOwnProperty.call(r, sortKey)) {
      const v = r[sortKey];
      if (v == null || v === '') return null;
      return v;
    }
    return null;
  }

  // —— 市值单位：百万美元（下限含、上限不含）——
  const capRangesMillion = {
    all: ['', ''], micro: ['', '10'], small: ['10', '50'], mid: ['50', '100'],
    large: ['100', '500'], mega: ['500', '']
  };
  if (typeof filterValidation === 'function') {
    const prevValidate = filterValidation;
    window.filterValidation = function () {
      for (const id of ['capMin', 'capMax']) {
        const e = $('#' + id);
        if (!e) continue;
        if (e.value !== '' && (!e.validity.valid || !Number.isFinite(Number(e.value)) || Number(e.value) < 0)) {
          return '请填写非负、有效市值范围，单位为百万美元。';
        }
      }
      if ($('#capMin') && $('#capMax') && $('#capMin').value !== '' && $('#capMax').value !== '' && Number($('#capMin').value) >= Number($('#capMax').value)) {
        return '最低市值必须小于最高市值：下限含、上限不含（百万美元）。';
      }
      const selected = $$('#indicators input[value]:checked').length;
      if ($('#combine') && $('#combine').value === 'n' && selected && (!$('#n').validity.valid || !Number.isInteger(Number($('#n').value)) || Number($('#n').value) < 1 || Number($('#n').value) > selected)) {
        return '至少N项必须在1至已勾选指标数之间。';
      }
      return '';
    };
    void prevValidate;
  }
  if (typeof scope === 'function') {
    window.scope = function (r) {
      const states = [];
      const term = ($('#search')?.value || '').trim().toUpperCase();
      states.push(!term || r.symbol.toUpperCase().includes(term) || r.asset.toUpperCase().includes(term));
      const m = $('#market')?.value || 'all';
      states.push(m === 'all' || (m === 'coin' ? r.market === 'COIN-M' : r.market !== 'COIN-M' && r.quote === (m === 'usdc' ? 'USDC' : 'USDT')));
      if ($('#trusted')?.checked) states.push(r.capTrusted === true);
      const min = $('#capMin')?.value ?? '', max = $('#capMax')?.value ?? '';
      if (min !== '' || max !== '') {
        states.push(present(r.capUsd)
          ? (min === '' || r.capUsd >= Number(min) * 1e6) && (max === '' || r.capUsd < Number(max) * 1e6)
          : null);
      }
      return threshold(states, states.length);
    };
  }
  function poolViewMode() {
    return $('#poolViewMode')?.value || 'six';
  }
  function cycleConfirms(r) {
    if (window.MarketEvidence && typeof MarketEvidence.cycleConfirmEvents === 'function') {
      return MarketEvidence.cycleConfirmEvents(r);
    }
    const st = r && r.emaSqueezeStage; const out = []; const seen = new Set();
    if (!st || typeof st !== 'object') return out;
    const push = (ev) => {
      if (!ev || typeof ev !== 'object' || !ev.event_id || ev.void || ev.duplicate || ev.blocked) return;
      if (seen.has(ev.event_id)) return; seen.add(ev.event_id); out.push(ev);
    };
    (st.confirmedEvents || []).forEach(push); push(st.crossConfirm); push(st.latestConfirm);
    return out;
  }
  function emaParent(){return $('#indicators input[value="3"]');}
  function selectedEmaLevels(){
    if(!emaParent()?.checked)return [];
    return ['strong','medium','weak'].filter(g=>$('#ignition'+g[0].toUpperCase()+g.slice(1))?.checked);
  }
  function syncEmaOptions(){
    const parent=emaParent(), active=!!parent?.checked, box=$('#ignitionFilters');
    if(box)box.setAttribute('aria-disabled',String(!active));
    ['ignitionStrong','ignitionMedium','ignitionWeak'].forEach(id=>{
      const option=$('#'+id);if(!option)return;option.disabled=!active;if(!active)option.checked=false;
    });
  }
  function ignitionStatus(r) {
    const want=selectedEmaLevels();
    if(poolViewMode()==='confirm'){
      const levels=cycleConfirms(r).map(ev=>ev.level);
      return !want.length?levels.length>0:levels.some(lv=>want.includes(lv));
    }
    return MarketEvidence.emaGradeStatus(r,want.length?want:undefined);
  }
  function mergeTri(a, b) {
    if (a === false || b === false) return false;
    if (a === true && b === true) return true;
    return null;
  }
  if (typeof combined === 'function') {
    window.combined = function (r) {
      const view = poolViewMode();
      if (view === 'pool') {
        const st = r.emaSqueezeStage;
        if (!st || st.candidateStatus == null) return null; // 旧快照无字段
        const inPool = st.candidateStatus === 'active';
        return mergeTri(inPool, selectedEmaLevels().length?ignitionStatus(r):true);
      }
      if (view === 'confirm') {
        const st = r.emaSqueezeStage;
        if (!st || (!Object.prototype.hasOwnProperty.call(st, 'confirmedEvents')
          && !Object.prototype.hasOwnProperty.call(st, 'crossConfirm')
          && st.candidateStatus == null)) return null;
        const ok = cycleConfirms(r).length > 0;
        return mergeTri(ok, ignitionStatus(r));
      }
      // I4 and its level options are one term in AND/OR/N, not a second global AND.
      const picked=$$('#indicators input[value]:checked').map(x=>Number(x.value));
      if(!picked.length)return true;
      const signals=signalFlags(r);
      const vals=picked.map(i=>i===3?ignitionStatus(r):signals[i]);
      const mode=$('#combine')?.value||'all';
      return threshold(vals,mode==='all'?vals.length:mode==='any'?1:Number($('#n')?.value||1));
    };
  }

  // 注入外壳 DOM：筛选侧栏布局、已选页签、显示列、托盘、列配置对话框
  function ensureShell() {
    const market = $('[data-view="market"]');
    if (!market || market.dataset.aProShell === '1') return;
    market.dataset.aProShell = '1';
    market.classList.add('a-pro-market', 'a-shell-market');
    document.body.classList.add('a-shell-ready', 'has-a-pro-tray');
    document.documentElement.classList.add('a-shell-ready');

    const heading = market.querySelector('.page-heading');
    if (heading && !heading.querySelector('.a-shell-top-actions')) {
      const actions = document.createElement('div');
      actions.className = 'a-shell-top-actions top-actions';
      actions.innerHTML = '<span id="aShellScheduleStatus" class="tag">定时见设置</span>';
      const b1 = document.createElement('button');
      b1.type = 'button';
      b1.textContent = '定时设置';
      b1.addEventListener('click', () => {
        if (typeof page === 'function') page('settings');
        else document.querySelector('[data-page="settings"]')?.click();
        setTimeout(() => {
          document.querySelector('[data-set="scan"], [data-setting-view="scan"]')?.click();
        }, 0);
      });
      const b2 = document.createElement('button');
      b2.type = 'button';
      b2.className = 'primary';
      b2.textContent = '手动扫描';
      b2.addEventListener('click', () => {
        const real = document.querySelector('#runScan, #manualScan, [data-action="scan"]');
        if (real) { real.click(); return; }
        const demo = document.querySelector('button[data-demo]');
        if (demo) { demo.click(); return; }
        if (typeof page === 'function') page('tasks');
        else if (typeof toast === 'function') toast('请到数据与任务页启动正式扫描');
      });
      actions.append(b1, b2);
      heading.append(actions);
      const lead = heading.querySelector('.lead p');
      if (lead) lead.textContent = '筛选合约，查看各指标与数据覆盖。';
    }

    const stats = market.querySelector('.stats');
    if (stats && !stats.classList.contains('snapshot-line')) {
      stats.classList.add('snapshot-line');
      stats.setAttribute('aria-label', '快照摘要');
    }

    const query = market.querySelector('.query-panel');
    const results = market.querySelector('.results-panel');
    if (query && results && !market.querySelector('.a-pro-workbench')) {
      const wrap = document.createElement('div');
      wrap.className = 'a-pro-workbench scanlayout';
      query.parentNode.insertBefore(wrap, query);
      wrap.append(query, results);
      query.classList.add('a-pro-filter-rail', 'filterbox');
      results.classList.add('results');
    } else {
      market.querySelector('.a-pro-workbench')?.classList.add('scanlayout');
      query?.classList.add('a-pro-filter-rail', 'filterbox');
      results?.classList.add('results');
    }

    const ind = $('#indicators');
    if (ind) {
      ind.classList.remove('grid3');
      ind.classList.add('indicator-list');
      if (!$('#ignitionFilters')) {
        const box = document.createElement('div');
        box.id = 'ignitionFilters';
        box.className = 'a-pro-ignition-filters';
        box.setAttribute('role', 'group');
        box.setAttribute('aria-label', 'EMA三线金叉等级');
        box.innerHTML = '<div class="muted" style="margin:8px 0 4px">金叉等级（可多选；不选包含全部）</div>'
          + '<label class="ema-level-option" style="display:flex;align-items:center;gap:6px;margin:6px 0"><input id="ignitionStrong" type="checkbox">强🟢</label> '
          + '<label class="ema-level-option" style="display:flex;align-items:center;gap:6px;margin:6px 0"><input id="ignitionMedium" type="checkbox">中🟡</label> '
          + '<label class="ema-level-option" style="display:flex;align-items:center;gap:6px;margin:6px 0"><input id="ignitionWeak" type="checkbox">弱🔴（勿入场）</label>';
        const parent=emaParent()?.closest('.indicator');
        (parent||ind).appendChild(box);
      }
      if (!$('#poolViewMode')) {
        const wrap = document.createElement('div');
        wrap.className = 'a-pro-pool-view';
        wrap.style.cssText = 'margin:10px 0 6px';
        wrap.innerHTML = '<label style="display:block;font-size:13px">结果视图 '
          + '<select id="poolViewMode">'
          + '<option value="six">普通六指标</option>'
          + '<option value="pool">持续观察池</option>'
          + '<option value="confirm">确认点火事件</option>'
          + '</select></label>'
          + '<div id="poolViewHint" class="muted" style="font-size:12px;margin-top:4px;line-height:1.5">'
          + '④按金叉等级筛选；弱仅作观察，中／强仍需核对确认事件。</div>';
        const after = ind;
        after.insertAdjacentElement('afterend', wrap);
        wrap.addEventListener('change', () => {
          const hint = $('#poolViewHint');
          const v = poolViewMode();
          if (hint) {
            hint.textContent = v === 'pool'
              ? '持续观察池：入池后突破三线仍保留；可用④的等级选项细分。'
              : v === 'confirm'
                ? '确认点火事件：按本候选周期的已确认事件及所选等级筛选。'
                : '④按金叉等级筛选；弱仅作观察，中／强仍需核对确认事件。';
          }
          if (typeof renderMarket === 'function') renderMarket();
          else if (typeof render === 'function') render();
        });
      }
    }

    if (results && !$('.a-shell-results-toolbar', results)) {
      const toolbar = market.querySelector('.query-toolbar');
      const bar = document.createElement('div');
      bar.className = 'a-shell-results-toolbar results-toolbar';
      if (toolbar) {
        const seg = toolbar.querySelector('.segmented[aria-label="结果状态"]');
        const sortWrap = toolbar.querySelector('label');
        const dirBtn = toolbar.querySelector('#toggleDirection');
        const trusted = toolbar.querySelector('label.check');
        const left = document.createElement('div');
        left.className = 'result-tabs';
        if (seg) left.append(seg);
        const right = document.createElement('div');
        right.className = 'a-shell-sortbar sortbar';
        if (sortWrap) right.append(sortWrap);
        if (dirBtn) right.append(dirBtn);
        const colBtn = document.createElement('button');
        colBtn.type = 'button';
        colBtn.id = 'openColumns';
        colBtn.textContent = '显示列';
        right.append(colBtn);
        const more = toolbar.querySelector('.more-actions');
        if (more) right.append(more);
        bar.append(left, right);
        // 仅明确映射靠近筛选条件；已选计数/投研留在底部托盘，不塞进排序组
        if (trusted) {
          let meta = results.querySelector('.a-shell-filter-meta');
          if (!meta) {
            meta = document.createElement('div');
            meta.className = 'a-shell-filter-meta';
            bar.prepend(meta);
          }
          meta.append(trusted);
        }
        const research = toolbar.querySelector('#marketResearch');
        if (research) {
          const trayActions = document.querySelector('#mainTray .a-pro-tray-actions, #mainTray .tray-actions, #mainTray');
          const trayResearch = document.querySelector('#trayResearch');
          if (trayResearch) research.style.display = 'none';
          else if (trayActions && research.parentElement !== trayActions) {
            research.classList.add('primary');
            trayActions.append(research);
          }
        }
        const selCount = toolbar.querySelector('#selectedCount');
        if (selCount) selCount.style.display = 'none';
      } else {
        bar.innerHTML = '<div class="result-tabs"></div><div class="a-shell-sortbar"><button type="button" id="openColumns">显示列</button></div>';
      }
      const head = results.querySelector('.panel-head');
      if (head) head.insertAdjacentElement('afterend', bar);
      else results.insertAdjacentElement('afterbegin', bar);
    }

    if (!$('#openColumns')) {
      const sortbar = market.querySelector('.a-shell-sortbar');
      if (sortbar) sortbar.insertAdjacentHTML('beforeend', '<button type="button" id="openColumns">显示列</button>');
    }

    const segmented = market.querySelector('.segmented[aria-label="结果状态"]');
    if (segmented && !segmented.querySelector('[data-result="selected"]')) {
      segmented.insertAdjacentHTML('beforeend', '<button type="button" data-result="selected">已选 <span id="selectedTabCount">0</span></button>');
      const sel = $('#result');
      if (sel && ![...sel.options].some(o => o.value === 'selected')) {
        sel.insertAdjacentHTML('beforeend', '<option value="selected">已选</option>');
      }
    }

    const custom = $('#customCapRow');
    if (custom) {
      const min = $('#capMin'), max = $('#capMax');
      if (min && max) {
        const minLabel = min.closest('label');
        const maxLabel = max.closest('label');
        if (minLabel && !minLabel.textContent.includes('百万美元')) {
          minLabel.childNodes[0].textContent = '最低（百万美元，含） ';
        }
        if (maxLabel && !maxLabel.textContent.includes('百万美元')) {
          maxLabel.childNodes[0].textContent = '最高（百万美元，不含） ';
        }
        let hint = $('#rangeUnitHint');
        if (!hint) {
          hint = document.createElement('small');
          hint.id = 'rangeUnitHint';
          custom.append(hint);
        }
        hint.textContent = '单位：百万美元；下限含、上限不含。缺市值单独进待核验。';
      }
    }
    const oldCols = $('#marketColumns');
    if (oldCols) oldCols.hidden = true;
    const hist = $('#marketColumnsHistogram');
    if (hist) hist.hidden = true;

    // keep #comboExplanation node (hidden) so baseRender textContent does not throw
    const combo = $('#comboExplanation');
    if (combo) {
      const wrap = combo.closest('details.a-shell-help');
      if (wrap) {
        wrap.hidden = true;
        wrap.style.display = 'none';
      } else {
        combo.hidden = true;
        combo.style.display = 'none';
      }
    }
    const qsum = $('#querySummary');
    if (qsum) qsum.hidden = true;

    if (!$('#mainTray')) {
      document.body.insertAdjacentHTML('beforeend', `
<aside id="mainTray" class="a-pro-tray tray" aria-label="已选合约托盘">
  <div class="a-pro-tray-inner">
    <strong class="tray-title">已选 <span id="trayCount">0</span></strong>
    <div id="trayPills" class="a-pro-tray-pills chosen-pills"><span class="muted">勾选合约可带入投研；跨筛选保留</span></div>
    <div class="a-pro-tray-actions tray-actions">
      <span id="trayHiddenNote" class="muted" hidden></span>
      <button type="button" id="trayClear" disabled>清空</button>
      <button type="button" id="trayResearch" class="primary" disabled>带入投研</button>
    </div>
  </div>
</aside>`);
      /* hide toolbar research once tray owns it */
      const _mr = document.querySelector('#marketResearch');
      if (_mr) _mr.style.display = 'none';
    }

    if (!$('#columnsDialog')) {
      document.body.insertAdjacentHTML('beforeend', `
<dialog id="columnsDialog" class="a-pro-columns-dialog">
  <form method="dialog" class="a-pro-columns-form">
    <div class="row between"><h3>显示列配置</h3><button type="submit" value="cancel" aria-label="关闭">×</button></div>
    <p class="muted">选择要显示的数据列；合约列固定。⑤为币安标记聪明钱4h净流入（与储备变化、历史交易所净流分列）。</p>
    <div class="row" style="margin:12px 0;gap:8px">
      <button type="button" id="colsProfessional">专业默认列</button>
      <button type="button" id="colsAll">全部数据列</button>
    </div>
    <div id="columnOptions" class="a-pro-column-options"></div>
    <div class="row" style="justify-content:flex-end;gap:8px;margin-top:14px">
      <button type="submit" value="cancel">取消</button>
      <button type="button" id="colsApply" class="primary">应用</button>
    </div>
  </form>
</dialog>`);
    }

    if (!$('#a-shell-inline-fallback')) {
      const style = document.createElement('style');
      style.id = 'a-shell-inline-fallback';
      style.textContent = `
/* runtime fallback only — visual density lives in a-shell.css */
.a-shell-market .filterbox .query-toolbar{display:none}
.a-shell-market .a-pro-workbench.scanlayout{display:grid;grid-template-columns:minmax(260px,280px) minmax(0,1fr);gap:20px;align-items:start}
.a-pro-hint{padding:10px 14px;background:var(--soft,#eef7f3);color:var(--accent,#167a72);border-radius:8px;font-size:13px;margin-bottom:12px;line-height:1.45}
.a-pro-tray-inner{display:flex;gap:12px;align-items:center;padding:12px 18px;flex-wrap:wrap}
.a-pro-tray-pills{display:flex;gap:6px;flex-wrap:wrap;flex:1;min-width:180px;max-height:64px;overflow:auto}
.a-pro-tray-actions{display:flex;gap:8px;align-items:center;margin-left:auto}
.a-pro-columns-dialog{border:1px solid var(--line,#dce5e9);border-radius:12px;padding:18px;max-width:560px;width:calc(100% - 32px)}
.a-pro-column-options{display:grid;grid-template-columns:1fr 1fr;gap:4px 14px;max-height:360px;overflow:auto}
.a-pro-column-options label{display:flex;gap:8px;align-items:center;font-size:13px;padding:4px 0}
@media(max-width:1150px){
  .a-shell-market .a-pro-workbench.scanlayout{grid-template-columns:1fr;gap:18px}
}
`;
      document.head.append(style);
    }
  }

  function renderColumnOptions() {
    const box = $('#columnOptions');
    if (!box) return;
    const rows = (typeof snapshot !== 'undefined' && snapshot && Array.isArray(snapshot.rows)) ? snapshot.rows : [];
    const total = rows.length || 0;
    function coverageTag(pred) {
      if (!total) return '';
      let n = 0;
      rows.forEach(function (r) { if (pred(r)) n++; });
      return ' <small class="muted">（有效 ' + n + '/' + total + '）</small>';
    }
    box.innerHTML = ALL_COLS.map(k => {
      let tag = '';
      if (k === 'flow') tag = coverageTag(function (r) { return r.flowSignal != null; }) || ' <small class="muted">（暂无同期数据）</small>';
      else if (k === 'reserveDay' || k === 'reserveWeek' || k === 'reserveStreak') {
        tag = coverageTag(function (r) {
          ensureSupplementalFields(r);
          if (k === 'reserveDay') return present(r.reserveDayPct);
          if (k === 'reserveWeek') return present(r.reserveWeekPct);
          return present(r.reserveStreakDays);
        });
      } else if (k === 'histFlow') {
        tag = coverageTag(function (r) { return !!histRec(r); });
      }
      return `<label><input type="checkbox" data-col="${k}" ${visibleCols.has(k) ? 'checked' : ''}>${esc(HEADERS[k])}${tag}</label>`;
    }).join('');
  }

  function renderTray() {
    const tray = $('#mainTray');
    if (!tray || typeof selectedTokens === 'undefined' || typeof snapshot === 'undefined') return;
    const unified = window.UnifiedWorkbench?.selection();
    const selected = unified ? unified.selected : snapshot.rows.filter(r => selectedTokens.has(key(r)));
    const hidden = selected.filter(r => {
      if (unified) return !unified.visibleKeys.has(key(r));
      try {
        const st = threshold([scope(r), combined(r)], 2);
        const view = $('#result')?.value || 'hit';
        if (view === 'selected') return false;
        if (view === 'hit') return st !== true;
        if (view === 'unknown') return st !== null;
      } catch (_) { return false; }
      return false;
    }).length;
    $('#trayCount').textContent = String(selected.length);
    const pills = $('#trayPills');
    pills.innerHTML = selected.length
      ? selected.slice(0, 40).map(r => `<button type="button" data-tray-remove="${esc(key(r))}" aria-label="移除 ${esc(r.symbol)}">${esc(r.symbol)} ×</button>`).join('') + (selected.length > 40 ? `<span class="muted">…共${selected.length}</span>` : '')
      : '<span class="muted">勾选合约后可带入投研；跨筛选保留；同币不同市场不合并</span>';
    $('#trayClear').disabled = !selected.length;
    $('#trayResearch').disabled = !selected.length;
    const note = $('#trayHiddenNote');
    if (hidden) { note.hidden = false; note.textContent = `当前筛选隐藏 ${hidden} 份已选`; }
    else { note.hidden = true; note.textContent = ''; }
    const tab = $('#selectedTabCount');
    if (tab) tab.textContent = String(selected.length);
    const sc = $('#selectedCount');
    if (sc) sc.textContent = `已选${selected.length}份`;
  }

  window.addEventListener('workbench-selection-changed', renderTray);

  function formatFieldValue(r, k) {
    let v = r[k];
    if (v == null) return '未知';
    if (typeof v === 'boolean') return v ? '是 / 已核对' : '否 / 未核对';
    if ((k.endsWith('AsOf') || k === 'candleAsOf') && typeof v === 'number') {
      const d = new Date(v);
      return (Number.isFinite(d.valueOf()) ? d.toISOString() : String(v)) + '（原值 ' + v + '）';
    }
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  }

  function archiveHTML(r) {
    return `<div class="a-pro-field-archive"><h3 style="margin-top:18px">完整字段档案</h3><p class="muted">原值与来源保留；同资产OI不冒充本合约OI；缺失显示未知，不写为0/false。命中 ${realHitCount(r)} / 可算 ${realKnownCount(r)} / 未知 ${realUnknownCount(r)}（后端 hitCount/knownCount 优先）。</p>${FIELD_GROUPS.map(([title, keys]) => `<details><summary>${esc(title)}</summary><table>${keys.map(k => `<tr><th>${esc(FIELD_NAMES[k] || k)}</th><td>${esc(formatFieldValue(r, k))}</td></tr>`).join('')}</table></details>`).join('')}</div>`;
  }

  function activePageRows() {
    if (Array.isArray(window.pageRows)) return window.pageRows;
    if (typeof pageRows !== 'undefined' && Array.isArray(pageRows)) return pageRows;
    return [];
  }
  function rebuildTable() {
    const panel = $('[data-view="market"] .results-panel');
    const pageRowsLocal = activePageRows();
    if (!panel) return;
    let wrap = panel.querySelector('.a-pro-tablewrap');
    const oldWrap = panel.querySelector('.tablewrap');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.className = 'a-pro-tablewrap tablewrap';
      if (oldWrap) oldWrap.replaceWith(wrap);
      else panel.querySelector('.panel-head')?.insertAdjacentElement('afterend', wrap);
    }
    if (!$('.a-pro-hint', panel)) {
      wrap.insertAdjacentHTML('beforebegin', '<div class="a-pro-hint muted" style="background:transparent;color:var(--muted,#647d84);padding:0 0 8px">显示 <span id="aProColHint">11/30</span> 项 · 表可横向滚动</div>');
    }
    const cols = [...visibleCols];
    const error = typeof filterValidation === 'function' ? filterValidation() : '';
    const headSort = (k) => {
      const sk = SORT_KEYS[k] || k;
      const cur = $('#sort')?.value;
      const dir = $('#direction')?.value || 'desc';
      const mark = cur === sk || (k === 'hits' && cur === 'hits') ? (dir === 'desc' ? ' ↓' : ' ↑') : ' ↕';
      return `<button type="button" data-pro-sort="${esc(sk)}">${esc(HEADERS[k] || k)}${mark}</button>`;
    };
    const contractSort = () => {
      const cur = $('#sort')?.value;
      const dir = $('#direction')?.value || 'desc';
      const mark = cur === 'price' ? (dir === 'desc' ? ' ↓' : ' ↑') : ' ↕';
      return `<button type="button" data-pro-sort="price">合约 / 价格${mark}</button>`;
    };
    const selector = $('#selectPage') || (() => {
      const el = document.createElement('input');
      el.type = 'checkbox'; el.id = 'selectPage'; el.setAttribute('aria-label', '选择当前列表合约');
      return el;
    })();
    wrap.innerHTML = `<table class="a-pro-table evidence-table"><thead><tr><th></th><th>${contractSort()}</th>${cols.map(k => `<th>${headSort(k)}</th>`).join('')}</tr></thead><tbody id="marketRows"></tbody></table>`;
    wrap.querySelector('thead th').append(selector);

    const body = $('#marketRows');
    if (error) {
      body.innerHTML = `<tr><td colspan="${cols.length + 2}">${esc(error)}</td></tr>`;
    } else if (!pageRowsLocal.length) {
      body.innerHTML = `<tr><td colspan="${cols.length + 2}">没有结果。可切换待核验 / 已选，或放宽条件。未勾选指标时「明确满足」仅表示基础筛选，不等于命中六项。</td></tr>`;
    } else {
      body.innerHTML = pageRowsLocal.map(r => {
        const idx = snapshot.rows.indexOf(r);
        const checked = selectedTokens.has(key(r));
        return `<tr class="${checked ? 'selected' : ''}" data-contract="${esc(key(r))}"><td><input type="checkbox" data-select-token="${idx}" aria-label="选择 ${esc(r.symbol)} ${esc(r.market)}" ${checked ? 'checked' : ''}></td><td><button class="link" data-detail="${idx}">${esc(r.symbol)}</button><div class="coin-sub">${price(r.price, r.quote)}</div></td>${cols.map(k => `<td>${metric(r, k)}</td>`).join('')}</tr>`;
      }).join('');
    }

    const page = pageRowsLocal;
    selector.checked = page.length > 0 && page.every(r => selectedTokens.has(key(r)));
    selector.indeterminate = page.some(r => selectedTokens.has(key(r))) && !selector.checked;
    selector.disabled = !page.length || !!error;


    const tbl = wrap.querySelector('table');
    if (tbl && !tbl.querySelector('colgroup')) {
      const cg = document.createElement('colgroup');
      const wSelect = COL_WIDTHS.select || 40;
      const wContract = COL_WIDTHS.contract || 160;
      let total = wSelect + wContract;
      cg.innerHTML = `<col style="width:${wSelect}px"><col style="width:${wContract}px">` +
        cols.map(k => { const w = COL_WIDTHS[k] || 120; total += w; return `<col style="width:${w}px">`; }).join('');
      tbl.insertBefore(cg, tbl.firstChild);
      tbl.style.width = total + 'px';
      tbl.style.minWidth = Math.max(total, wrap.clientWidth || 0) + 'px';
    }
    const hintCount = panel.querySelector('#aProColHint');
    if (hintCount) hintCount.textContent = cols.length + '/' + ALL_COLS.length;

    const th0 = wrap.querySelector('th');
    if (th0) wrap.querySelector('table').style.setProperty('--select-col-width', '40px');
  }

  const baseRender = renderMarket;
  window.renderMarket = function () {
    // 同步百万美元预设
    if (window.capRanges) Object.assign(window.capRanges, capRangesMillion);
    else window.capRanges = capRangesMillion;

    const resultEl = $('#result');
    const view = resultEl?.value || 'hit';

    // 先让原链路算 scoped / hit / unknown；再关闭分页，当前筛选一次渲染全部行
    baseRender();
    if (typeof currentRows !== 'undefined') {
      window.currentRows = currentRows;
      window.pageRows = currentRows.slice();
      window.marketPage = 1;
    } else if (Array.isArray(window.currentRows)) {
      window.pageRows = window.currentRows.slice();
      window.marketPage = 1;
    }
    const _pager = document.querySelector('[data-view="market"] .pagination') || document.querySelector('.pagination');
    if (_pager) _pager.hidden = true;
    if ($('#pageNumber')) $('#pageNumber').textContent = '';
    if ($('#previousPage')) { $('#previousPage').disabled = true; $('#previousPage').hidden = true; }
    if ($('#nextPage')) { $('#nextPage').disabled = true; $('#nextPage').hidden = true; }
    if ($('#pageNote') && typeof currentRows !== 'undefined' && typeof selectedTokens !== 'undefined') {
      $('#pageNote').textContent = `共${currentRows.length}份 · 已选${selectedTokens.size}份（跨筛选保留）`;
    }

    if (view === 'selected' && typeof snapshot !== 'undefined') {
      const rows = snapshot.rows.filter(r => selectedTokens.has(key(r)));
      const sorted = typeof sortRows === 'function' ? sortRows(rows.slice()) : rows.slice();
      window.currentRows = sorted;
      window.marketPage = 1;
      window.pageRows = sorted.slice();
      try { currentRows = sorted; } catch (_) {}
      try { pageRows = sorted.slice(); } catch (_) {}
      try { marketPage = 1; } catch (_) {}
      if ($('#tableTitle')) $('#tableTitle').textContent = `${sorted.length}份合约 · 已选`;
      if ($('#pageNumber')) $('#pageNumber').textContent = '';
      if ($('#previousPage')) $('#previousPage').disabled = true;
      if ($('#nextPage')) $('#nextPage').disabled = true;
      if ($('#pageNote')) $('#pageNote').textContent = `共${sorted.length}份 · 已选${selectedTokens.size}份（跨筛选保留；同币不同市场不合并）`;
      const _pg = document.querySelector('.pagination'); if (_pg) _pg.hidden = true;
    }

    const sortRowsNow = typeof currentRows !== 'undefined' ? currentRows : [];
    let sortNote = $('#sortAvailability');
    if (!sortNote && $('#sort')) {
      sortNote = document.createElement('span'); sortNote.id = 'sortAvailability';
      sortNote.className = 'muted'; sortNote.setAttribute('role', 'status');
      $('#sort').parentNode.appendChild(sortNote);
    }
    if (sortNote) {
      const k = $('#sort')?.value || 'hits';
      const count = sortRowsNow.filter(r => { const v = sortValue(r, k); return v != null && (typeof v !== 'number' || Number.isFinite(v)); }).length;
      sortNote.textContent = sortRowsNow.length && !count ? '当前范围无有效值可排序，按合约名称显示' : '';
      const toggle = $('#toggleDirection');
      if (toggle) toggle.disabled = !count;
    }
    // Market hit counts use screen-grade I4; stored pool-entry counts stay unchanged.
    syncEmaOptions();
    rebuildTable();
    renderTray();

    // 扫描侧栏不再展示 querySummary（市值/排序/hitCount）文案
    const summary = $('#querySummary');
    if (summary) summary.hidden = true;

    // 未勾选指标说明
    const picked = $$('#indicators input[value]:checked').length;
    const expl = $('#comboExplanation');
    if (expl && !picked) {
      expl.textContent = '未勾选指标：「明确满足」只表示满足搜索/市场/市值/映射基础筛选，不能解释为命中六项。六卡仍按基础范围独立统计。';
    }
  };

  const baseDetail = typeof openDetail === 'function' ? openDetail : null;
  if (baseDetail) {
    window.openDetail = function (index, trigger) {
      baseDetail(index, trigger);
      const r = snapshot.rows[index];
      if (!r || !$('#detailBody')) return;
      if (r.quote && !['USD', 'USDT', 'USDC'].includes(r.quote)) {
        const summary = $('#detailBody > p');
        if (summary) summary.textContent = '快照价格 ' + Number(r.price).toLocaleString('en-US', { maximumSignificantDigits: 6 }) + ' ' + r.quote + ' · 24h ' + pct(r.priceChange24Pct);
      }
      if ($('#detailBody .a-pro-field-archive')) return;
      if (!$('#detailBody .ema-confirm-block')) {
        const confHtml = !$('#detailBody .ema-confirm-event') && (window.MarketEvidence && MarketEvidence.confirmEventDetailsHtml)
          ? MarketEvidence.confirmEventDetailsHtml(r)
          : '';
        const st = r.emaSqueezeStage || {};
        const poolLine = st.candidateStatus == null
          ? '候选状态：未记录（旧快照无字段）'
          : ('候选状态：' + st.candidateStatus
            + (st.candidateEnteredAt ? (' · 入池' + String(st.candidateEnteredAt).slice(0, 19)) : '')
            + (st.entryOiUsd != null ? (' · OI基数 ' + st.entryOiUsd) : '')
            + (st.brokeAboveThree ? ' · 已突破三线仍留池' : ''));
        $('#detailBody').insertAdjacentHTML('beforeend',
          '<div class="ema-confirm-block" style="margin-top:14px"><h3>EMA三线金叉 · 池与确认</h3>'
          + '<p class="muted">' + poolLine + '</p>'
          + '<p class="muted">本根分级：' + (r.ignitionGrade || '未记录') + '（≠周期确认）</p>'
          + confHtml + '</div>');
        if (!$('#detailBody .ema-observation-detail')) $('#detailBody').insertAdjacentHTML('beforeend', MarketEvidence.observationDetailsHtml(r));
      }
      $('#detailBody').insertAdjacentHTML('beforeend', archiveHTML(r));
    };
  }

  function wire() {
    ensureShell();
    renderColumnOptions();
    syncEmaOptions();
    emaParent()?.addEventListener('change',syncEmaOptions);
    ['ignitionStrong', 'ignitionMedium', 'ignitionWeak'].forEach(id => {
      const el = document.getElementById(id);
      if (el && !el.dataset.i4bBound) {
        el.dataset.i4bBound = '1';
        el.addEventListener('change', () => { marketPage = 1; renderMarket(); });
      }
    });

    // 预设范围改为百万美元数值
    const prevCap = $('#cap')?.onchange;
    if ($('#cap')) {
      $('#cap').onchange = () => {
        const range = capRangesMillion[$('#cap').value];
        if (range) { $('#capMin').value = range[0]; $('#capMax').value = range[1]; }
        marketPage = 1;
        renderMarket();
      };
    }
    // 初始化 small 默认 10–50 百万
    if ($('#cap')?.value === 'small' && $('#capMin') && ($('#capMin').value === '1000' || Number($('#capMin').value) > 200)) {
      $('#capMin').value = '10';
      $('#capMax').value = '50';
    }

    $('#openColumns')?.addEventListener('click', () => {
      renderColumnOptions();
      $('#columnsDialog')?.showModal();
    });
    $('#colsProfessional')?.addEventListener('click', () => {
      visibleCols = new Set(DEFAULT_COLS);
      renderColumnOptions();
    });
    $('#colsAll')?.addEventListener('click', () => {
      visibleCols = new Set(ALL_COLS);
      renderColumnOptions();
    });
    $('#colsApply')?.addEventListener('click', () => {
      const picked = $$('#columnOptions [data-col]:checked').map(e => e.dataset.col);
      visibleCols = new Set(picked.length ? picked : DEFAULT_COLS);
      saveColumns();
      $('#columnsDialog')?.close();
      renderMarket();
    });

    document.addEventListener('click', e => {
      const sortBtn = e.target.closest('[data-pro-sort]');
      if (sortBtn) {
        const sk = sortBtn.dataset.proSort;
        const sortEl = $('#sort');
        const dirEl = $('#direction');
        if (sortEl && ![...sortEl.options].some(o => o.value === sk) && sk !== 'hits') {
          sortEl.insertAdjacentHTML('beforeend', `<option value="${esc(sk)}">${esc(HEADERS[Object.keys(SORT_KEYS).find(k => SORT_KEYS[k] === sk)] || sk)}</option>`);
        }
        if (sortEl?.value === sk) {
          dirEl.value = dirEl.value === 'desc' ? 'asc' : 'desc';
        } else {
          sortEl.value = sk;
          dirEl.value = 'desc';
        }
        const td = $('#toggleDirection');
        if (td) td.textContent = dirEl.value === 'desc' ? '↓ 从高到低' : '↑ 从低到高';
        marketPage = 1;
        renderMarket();
        return;
      }
      const rem = e.target.closest('[data-tray-remove]');
      if (rem) {
        selectedTokens.delete(rem.dataset.trayRemove);
        renderMarket();
        return;
      }
      const res = e.target.closest('[data-result]');
      if (res && res.dataset.result) {
        $$('.segmented[aria-label="结果状态"] button').forEach(b => b.classList.toggle('active', b === res));
        if ($('#result')) $('#result').value = res.dataset.result;
        marketPage = 1;
        renderMarket();
      }
    });

    $('#trayClear')?.addEventListener('click', () => {
      selectedTokens.clear();
      renderMarket();
      if (typeof toast === 'function') toast('已清空选币');
    });
    $('#trayResearch')?.addEventListener('click', () => {
      if (typeof setReportScope === 'function') setReportScope('selected');
      if (typeof page === 'function') page('reports');
      else $('#marketResearch')?.click();
    });

    // 当前列表全选：无分页后 pageRows=筛选全部（沿用 #selectPage；不绑成未筛选全市场）
    document.addEventListener('change', e => {
      if (e.target?.id === 'selectPage') {
        pageRows.forEach(r => e.target.checked ? selectedTokens.add(key(r)) : selectedTokens.delete(key(r)));
        renderMarket();
      }
      if (e.target?.matches?.('[data-select-token]')) {
        const idx = Number(e.target.dataset.selectToken);
        const row = (typeof snapshot !== 'undefined' && snapshot.rows) ? snapshot.rows[idx] : null;
        if (row && typeof selectedTokens !== 'undefined' && typeof key === 'function') {
          if (e.target.checked) selectedTokens.add(key(row));
          else selectedTokens.delete(key(row));
        }
        if (typeof renderMarket === 'function') renderMarket();
        else renderTray();
      }
    });

    // 排序键补充
    const sortEl = $('#sort');
    if (sortEl) {
      [['oiUsd', '本合约OI'], ['volumeRatio', '观察窗量倍数(24h对齐窗)'], ['barVolumeRatio7', '前7根点火量倍数'], ['barVolumeRatio', '前20根单根量倍数'], ['markPrice', '标记价格'], ['binanceAssetOiUsd', '同资产总OI']].forEach(([v, t]) => {
        if (![...sortEl.options].some(o => o.value === v)) sortEl.insertAdjacentHTML('beforeend', `<option value="${v}">${t}</option>`);
      });
      // rename existing options to match field captions
      const sortRename = { volumeRatio: '观察窗量倍数(24h对齐窗)', barVolumeRatio7: '前7根点火量倍数', barVolumeRatio: '前20根单根量倍数' };
      [...sortEl.options].forEach(o => { if (sortRename[o.value]) o.textContent = sortRename[o.value]; });
    }

    window.addEventListener('workbench-snapshot', () => renderMarket());
    window.addEventListener('resize', () => {
      const th0 = document.querySelector('.a-pro-table th');
      const table = document.querySelector('.a-pro-table');
      if (th0 && table) table.style.setProperty('--select-col-width', th0.getBoundingClientRect().width + 'px');
    });
  }

  // 覆盖 hitCount 显示用真实后端值（筛选逻辑仍可用 flags）
  const prevHitCount = typeof hitCount === 'function' ? hitCount : null;
  window.hitCount = function (r) { return realHitCount(r); };
  void prevHitCount;

  // 缺失值排序置后：增强 sortRows
  if (typeof sortRows === 'function') {
    window.sortRows = function (rows) {
      const k = $('#sort')?.value || 'hits';
      const dir = $('#direction')?.value === 'desc' ? -1 : 1;
      return rows.slice().sort((a, b) => {
        const av = sortValue(a, k), bv = sortValue(b, k);
        const aMiss = av == null || (typeof av === 'number' && !Number.isFinite(av));
        const bMiss = bv == null || (typeof bv === 'number' && !Number.isFinite(bv));
        if (aMiss && bMiss) return key(a).localeCompare(key(b));
        if (aMiss) return 1;
        if (bMiss) return -1;
        if (typeof av === 'string' || typeof bv === 'string') return String(av).localeCompare(String(bv)) * dir || key(a).localeCompare(key(b));
        return (av - bv) * dir || key(a).localeCompare(key(b));
      });
    };
  }

  wire();
  // 初次用新外壳重绘
  try { renderMarket(); } catch (err) { console.warn('pro-table initial render', err); }

  window.ProTableRuntime = {
    DEFAULT_COLS, ALL_COLS, HEADERS, FIELD_GROUPS, FIELD_NAMES,
    realHitCount, realKnownCount, metric, version: 'a-pro-ema-d10-v1'
  };
})();
