"""Fixed SYNTHETIC specimens. Rule-derived expectations, never market history."""
H=3600000;Q=4*H;BASE=1699999200000//Q*Q

def specimens():
    def bar(i,c=100,q=1):
        return dict(open_ms=BASE+i*Q,o=c-.1,h=c+1,l=c-1,c=c,q=q,q_unit='USD nominal quote')
    def step(n,**assertions):
        return dict(boundary_ms=BASE+n*Q,as_of_ms=BASE+n*Q+1,assertions=assertions)
    raw=[bar(i,100 if i<119 else 90 if i==119 else 91,4 if i==120 else 1) for i in range(122)]
    def samples(unit,v):
        return [dict(effective_at_ms=b['open_ms']+Q,available_at_ms=b['open_ms']+Q+1,unit=unit,value=v,trusted=True) for b in raw]
    inp=dict(raw_bars=raw,oi_samples=samples('USD',50),lsr_samples=samples('ratio',1),caps=samples('USD',100))
    yield dict(name='C-synthetic-C0-T1',strategy='C',boundary=BASE+121*Q,inputs=inp,
        expected=dict(steps=[step(120,**{'events.0.technical_status':'waiting','events.0.qualification_status':'pass'}),
                             step(121,**{'events.0.technical_status':'confirmed','events.0.qualification_status':'pass','sessions.0.observation_status':'observing'})]),
        basis='Synthetic: T0 RSI below 25 and 14-root low; OI/cap=50%, LSR=1. T1 bullish quote volume=4 versus T0=1. Frozen 1015 v1.1.')
    yield dict(name='C-synthetic-missing-OI',strategy='C',boundary=BASE+120*Q,inputs=dict(inp,oi_samples=[]),
        expected=dict(steps=[step(120,**{'events':[]})]),basis='Synthetic: absent OI cannot satisfy the C0 fuel gate; no confirmed event.')
    yield dict(name='A-synthetic-uptrend-negative',strategy='A',boundary=BASE+122*Q,
        inputs=dict(raw_bars=[bar(i,100+i*.2,10) for i in range(122)]),
        expected=dict(steps=[step(122,**{'events':[],'candidate.confirmation_status':None})]),
        basis='Synthetic monotonic uptrend is not the frozen sideways A candidate/cross; no pre-seeded confirmation.')
    t=BASE+171*H;end=t//Q*Q
    bars=[dict(open_time=BASE+i*H,o=100,h=101,l=99,c=100.5,q=80 if i==168 else 25 if i==169 else 10) for i in range(171)]
    four=[[end-(1499-i)*Q,100+i*.1,102+i*.1,99+i*.1,100+i*.1,1,end-(1499-i-1)*Q-1,10] for i in range(1499)]
    yield dict(name='B-synthetic-h-h1-missing-qualification',strategy='B',boundary=t,
        inputs=dict(bars_asc=bars,raw4h=four,evidence={}),
        expected=dict(steps=[dict(boundary_ms=t,as_of_ms=t+1,assertions={'events.0.technical_status':'confirmed','events.0.qualification_status':'unknown'})]),
        basis='Synthetic 168-root baseline=10; h=80 (8x), true h+1=25 (>2x), return=0.5%; two closed rising 4H backgrounds. Missing OI/LSR/cap must remain unknown.')

def seed(root):
    from datetime import datetime,timezone
    from .case_library import capture,review
    output=[]
    for case in specimens():
        row=capture(root,strategy=case['strategy'],market='USDT-M',symbol='TESTUSDT',boundary_ms=case['boundary'],
            inputs=case['inputs'],provenance=dict(kind='synthetic_reference',name=case['name'],not_real_market_history=True),
            clock_iso=datetime.now(timezone.utc).isoformat())
        if not row.get('case_id'):raise ValueError('reference_capture_disabled_or_full')
        review(root,row['case_id'],expected=case['expected'],basis=case['basis'])
        output.append(dict(name=case['name'],**row))
    return output
