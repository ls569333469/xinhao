"""Binance labelled native-chain balance changes: a proxy, never transfer netflows."""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import urllib.request

ROOT=Path(__file__).resolve().parent
URL='https://api.llama.fi/protocol/binance-cex'
# Restrict to identifiable native assets on their own chain; never aggregate ticker collisions.
NATIVE={
    'Bitcoin':('BTC','BTC'), 'Litecoin':('LTC','LTC'), 'Doge':('DOGE','DOGE'),
    'Ripple':('XRP','XRP'), 'Polkadot':('DOT','DOT'), 'Near':('NEAR','NEAR'),
    'Solana':('SOL','SOL'), 'Tron':('TRX','TRON'), 'Binance':('BNB','BNB'),
    'Avalanche':('AVAX','AVAX'), 'Aptos':('APT','APT'), 'Sui':('SUI','SUI'),
    'Stellar':('XLM','XLM'), 'Algorand':('ALGO','ALGO'), 'Hedera':('HBAR','HBAR'),
    'Chiliz':('CHZ','CHZ'), 'Sonic':('S','S'), 'Ronin':('RONIN','RON'),
    'Celo':('CELO','CELO'),
}


def derive(source, scan, at):
    records=[]
    failures=[]
    assets={r['asset'] for r in scan['rows']}
    for chain,(asset,symbol) in NATIVE.items():
        if asset not in assets:
            continue
        series=source.get('chainTvls',{}).get(chain,{}).get('tokens',[])
        points = [p for p in series if isinstance(p,dict) and type(p.get('date')) is int
                  and p['date']%86400==0 and p['date']<=at.timestamp()]
        if len({p['date'] for p in points}) != len(points):
            failures.append({'asset':asset,'reason':'Duplicate daily balance; ambiguous evidence'});continue
        daily={p['date']:p.get('tokens',{}).get(symbol) if isinstance(p.get('tokens'),dict) else None for p in points}
        if not daily:
            failures.append({'asset':asset,'reason':'No daily native balance'});continue
        end=max(daily)
        stamps=[end-86400*i for i in range(7,-1,-1)]
        values=[daily.get(stamp) for stamp in stamps]
        if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or v<=0 for v in values):
            failures.append({'asset':asset,'reason':'Missing/nonpositive daily balance; no zero filling'});continue
        pct=lambda old,new:(new/old-1)*100
        days=[{'from':stamps[i-1],'to':stamps[i],'startTokens':values[i-1],
               'endTokens':values[i],'changeTokens':values[i]-values[i-1],
               'changePct':pct(values[i-1],values[i])} for i in range(1,8)]
        streak=0
        for day in reversed(days):
            if day['changeTokens']>=0:break
            streak+=1
        age=(at.timestamp()-end)/3600
        records.append({'asset':asset,'chain':chain,'providerSymbol':symbol,
                        'mapping':'explicit native asset on native chain; no wrapped/token ticker merge',
                        'asOf':end,'ageHoursAtRetrieval':age,'freshAtRetrieval':0<=age<=36,
                        'reserveTokens':values[-1],'dayChangeTokens':days[-1]['changeTokens'],
                        'dayChangePct':days[-1]['changePct'],'weekChangeTokens':values[-1]-values[0],
                        'weekChangePct':pct(values[0],values[-1]),'decreasingDaysInWeek':sum(d['changeTokens']<0 for d in days),
                        'consecutiveDecreaseDays':streak,'days':days,
                        'eligibleForExactNetflow':False,'evidenceType':'labelled_reserve_change_proxy'})
    covered={r['asset'] for r in records if r['freshAtRetrieval']}
    return {'source':'DefiLlama Binance CEX','endpoint':URL,'retrievedAt':at.isoformat(),
            'methodology':source.get('methodology'), 'adapter':source.get('tvlCodePath'),
            'scope':'Native-chain assets only. Binance PoR + pegged-token backing wallet set; not audited stable wallet cohort.',
            'meaning':'change in token quantities of labelled reserves; not USD TVL delta and not transfer inflow/outflow',
            'limits':['Wallet set changes, omitted wallets, backing/treasury transfers and provider errors can change balances.',
                      'Reserve decline alone does not prove buying, accumulation or a forthcoming price rise.',
                      'Current balance evidence and older derivatives snapshot keep separate timestamps.'],
            'freshAssets':len(covered),'contractReferences':sum(r['asset'] in covered for r in scan['rows']),
            'exactNetflowCoverage':0,'records':sorted(records,key=lambda r:r['dayChangePct']), 'failures':failures}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cached',action='store_true');args=parser.parse_args()
    if args.cached:
        source=json.loads((ROOT/'cex-reserves-source.json').read_text(encoding='utf-8'))
    else:
        with urllib.request.urlopen(urllib.request.Request(URL,headers={'User-Agent':'EvidencePanel/1.0'}),timeout=40) as r:source=json.load(r)
        if source.get('name')!='Binance CEX':raise ValueError('Unexpected provider identity')
        (ROOT/'cex-reserves-source.json').write_text(json.dumps(source,ensure_ascii=False),encoding='utf-8')
    scan=json.loads((ROOT/'scan.json').read_text(encoding='utf-8'))
    result=derive(source,scan,dt.datetime.now(dt.timezone.utc))
    path=ROOT/'cex-reserves.json';temp=path.with_suffix('.tmp.json')
    temp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
    archive=ROOT/'reserve-history';archive.mkdir(exist_ok=True)
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    (archive/(stamp+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['freshAssets','contractReferences','exactNetflowCoverage','failures']},ensure_ascii=False))


if __name__=='__main__':main()
