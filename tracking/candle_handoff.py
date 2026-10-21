"""Immutable 1H receipt -> ORIGINAL cache ingestor. No network or source ACK."""
import hashlib,json
from .recovery import Recovery

def deliver(root,ledger,job_key,version,*,clock_ms):
    recovery=Recovery(ledger)
    with recovery.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS recovery_candle_deliveries(job_key TEXT,version TEXT,payload_sha TEXT,delivered_ms INTEGER,PRIMARY KEY(job_key,version))')
        job=db.execute('SELECT * FROM tracking_recovery WHERE job_key=?',(job_key,)).fetchone()
        if not job or job['source']!='candles_1h':return dict(delivered=False,not_1h=True)
        if db.execute('SELECT 1 FROM recovery_candle_deliveries WHERE job_key=? AND version=?',(job_key,version)).fetchone():return dict(delivered=True,reused=True)
        chunk=db.execute('SELECT * FROM tracking_recovery_chunks WHERE job_key=? AND version=?',(job_key,version)).fetchone()
        if not chunk:raise ValueError('candle_receipt_missing')
        text=chunk['payload_json']
        if hashlib.sha256(text.encode()).hexdigest()!=version:raise ValueError('candle_receipt_hash')
        samples=json.loads(text);contract=job['contract'];target=job['target_ms']
    from b_1h_ingest import ingest_symbol_1h
    from b_1h_ingest import closed_1h_bars
    from market_candle_cache import MarketCandleCache
    market,symbol=contract.split('|',1);market='USDⓈ-M' if market=='USDT-M' else market
    if not 1<=len(samples)<=500:raise ValueError('candle_receipt_bound')
    raw=[];mult=None
    for sample in samples:
        row=sample.get('raw_kline');bar=sample.get('bar') or {}
        if not isinstance(row,list) or len(row)<8 or int(row[0])+3600000!=sample['effective_at_ms'] or bar.get('open_ms')!=int(row[0]):raise ValueError('original_candle_input_missing')
        if sample['available_at_ms']>clock_ms:raise ValueError('candle_not_available')
        raw.append(row)
        if market=='COIN-M':
            if mult is not None and mult!=sample.get('contract_size'):raise ValueError('mixed_historical_multiplier')
            mult=sample.get('contract_size')
        normalized=closed_1h_bars([row],market=market,contract_size=sample.get('contract_size'),target_close_boundary=target)
        if len(normalized)!=1 or any(normalized[0].get(k)!=bar.get(k) for k in ('o','h','l','c','q')):raise ValueError('candle_raw_normalized_disagreement')
    result=ingest_symbol_1h(MarketCandleCache(root),market=market,symbol=symbol,raw_klines=raw,target_close_boundary=target,contract_size=mult)
    if result['closed_bars']!=len({int(r[0]) for r in raw}):raise ValueError('original_candle_ingest_incomplete')
    # Crash here retries the same immutable content; original cache deduplicates.
    with recovery.transaction() as db:
        db.execute('INSERT OR IGNORE INTO recovery_candle_deliveries VALUES(?,?,?,?)',(job_key,version,version,clock_ms))
    return dict(delivered=True,receipt_only=True,**result)
