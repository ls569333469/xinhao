// BEGIN COMPLETE EVIDENCE RUNTIME
const marketEvidenceStyle=document.createElement('style');marketEvidenceStyle.textContent=MarketEvidence.style;document.head.append(marketEvidenceStyle);
const marketEvidencePanel=$('[data-view="market"] .results-panel');
marketEvidencePanel.querySelector('.panel-head').insertAdjacentHTML('afterend',MarketEvidence.controls('marketColumns'));
const marketColumnGroups=()=>MarketEvidence.selectedGroups($('#marketColumns'));
const historicalFlowsByAsset=new Map(chainHistory.records.map(r=>[r.asset,r]));
const marketEvidenceContext=r=>({reserve:reservesByAsset.get(r.asset),history:historicalFlowsByAsset.get(r.asset),exportContext:JSON.stringify({registryVersion:snapshot.registryVersion||null,registryHash:snapshot.registryHash||null,evaluatedRegistryVersion:StrategyRegistry.data.version,evaluatedRegistryHash:StrategyRegistry.hash,snapshotAt:snapshot.finishedAt,query:$('#querySummary').textContent,selectedIndicators:$$('#indicators input[value]:checked').map(e=>Number(e.value)+1),combination:$('#combine').value,minimum:Number($('#n').value),result:$('#result').value,reserveFilter:$('#reserveFilter').value,reserveThreshold:Number($('#reserveThreshold').value)})});
snapshot.rows.forEach(r=>{r.ema99DistancePct=present(r.closed4h)&&present(r.ema4h?.[2])&&r.ema4h[2]>0?(r.closed4h/r.ema4h[2]-1)*100:null;});
[['oiUsd','单合约名义OI'],['barVolumeRatio','本根4h量倍数'],['ema99DistancePct','4h收盘距EMA99'],['fundingDailyPct','费率每日外推'],['capPriceGapPct','市值价格差']].forEach(([value,label])=>$('#sort').insertAdjacentHTML('beforeend',`<option value="${value}">${label}</option>`));
const originalEvidenceRender=renderMarket;
renderMarket=function(){
 originalEvidenceRender();
 const groups=marketColumnGroups(),head=marketEvidencePanel.querySelector('thead tr');
 // Reuse the original checkbox node so its selection handler survives header rebuilds.
 const selector=$('#selectPage');
 head.innerHTML='<th></th>'+MarketEvidence.headers(groups);head.firstElementChild.append(selector);
 marketEvidencePanel.querySelector('table').classList.add('evidence-table');
 const error=filterValidation();
 $('#marketRows').innerHTML=error?`<tr><td colspan="${MarketEvidence.columns(groups).length+1}">${safe(error)}</td></tr>`:pageRows.map(r=>`<tr><td><input type="checkbox" data-select-token="${snapshot.rows.indexOf(r)}" aria-label="选择 ${safe(r.symbol)} ${r.market}" ${selectedTokens.has(key(r))?'checked':''}></td>`+MarketEvidence.tableCells(r,flags(r),groups,marketEvidenceContext(r),`<button class="link" data-detail="${snapshot.rows.indexOf(r)}">${safe(r.symbol)}</button>`)+ '</tr>').join('')||`<tr><td colspan="${MarketEvidence.columns(groups).length+1}">没有结果。可切换待核验或调整条件。</td></tr>`;
 $('#marketColumnsHistogram').innerHTML=MarketEvidence.distribution(error?[]:snapshot.rows.filter(r=>scope(r)===true),flags);
};
MarketEvidence.bindControls($('#marketColumns'),renderMarket,'squeeze-evidence-columns-v1');
const originalEvidenceDetail=openDetail;
openDetail=function(index,trigger){originalEvidenceDetail(index,trigger);const r=snapshot.rows[index];$('#detailBody').innerHTML=`<span class="pill gray">历史样本 · ${safe(r.market)}</span><p>快照价格 $${fmt(r.price,6)} · 24h ${fmt(r.priceChange24Pct)}%</p>`+MarketEvidence.detail(r,flags(r),marketEvidenceContext(r));};
const originalEvidenceReport=$('#dataReport').onclick;
$('#dataReport').onclick=()=>{originalEvidenceReport();$$('#generatedReport .report-item').forEach((article,i)=>{const r=analysisRows()[i];if(!r)return;const c=MarketEvidence.cells(r,flags(r),marketEvidenceContext(r));const body=article.querySelector('tbody');if(body)body.innerHTML=MarketEvidence.columns().filter(([k])=>!['symbol','cap','hits'].includes(k)).map(([k,t])=>`<tr><td>${t}</td><td>${c[k]}</td></tr>`).join('');article.insertAdjacentHTML('beforeend',MarketEvidence.detail(r,flags(r),marketEvidenceContext(r),true));});};
$('#exportMarketEvidence').onclick=()=>{
 if(filterValidation())return toast('请先修正筛选条件。');
 const csv=MarketEvidence.csv(currentRows,flags,marketEvidenceContext),link=document.createElement('a');
 const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));link.href=url;link.download='perpetual-full-evidence.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 toast(`已导出当前视图${currentRows.length}份合约的完整证据，包含未显示的数据列。`);
};
// END COMPLETE EVIDENCE RUNTIME
