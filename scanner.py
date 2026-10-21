"""Read-only Binance perpetual evidence scanner. No trading/account endpoints."""
import argparse
import math
from ema_observations import observe, bind_identity, finite
from snapshot_store import ScanLease, ScanArchive, aggregate_oi, clean
from strategy_registry import indicator_states, strategy_results, VERSION_HASH, REGISTRY
from ema_squeeze_rules import (
    full_cross as _full_cross,
    medium_cross as _medium_cross,
    death_trend_new_lows,
    new_low_count,
    amplitude_ratio,
    amplitude_ok,
)
import concurrent.futures as cf
import datetime as dt
import json
import pathlib
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from request_pacer import RequestPacer
from asset_identity import MAPPINGS, MAPPING_REVISION

from runtime_paths import data_root
ROOT = data_root()
UTC = dt.timezone.utc
LOCK = threading.Lock()
NEXT = 0.0
BINANCE_PACER = RequestPacer(480, 1)  # 8 dispatches/s; shared by A and B, not a quota guarantee.
# Unauthenticated public API: conservative local planning baseline, not a
# verified account entitlement. Adjust only against a confirmed provider quota.
COINGECKO_LIMIT_PER_MINUTE = 5
COINGECKO_UTILIZATION = .9
COINGECKO_PACER = RequestPacer(COINGECKO_LIMIT_PER_MINUTE, COINGECKO_UTILIZATION)
PROGRESS = {"running": False, "completed": 0, "total": 0, "phase": "未开始"}
ALIASES = {"XBT": "BTC", "LUNA2": "LUNA", "LUNC": "LUNC"}
CG_IDS = {"BTC":"bitcoin","ETH":"ethereum","BNB":"binancecoin","SOL":"solana","XRP":"ripple","DOGE":"dogecoin","ADA":"cardano","TRX":"tron","LINK":"chainlink","AVAX":"avalanche-2","DOT":"polkadot","LTC":"litecoin","BCH":"bitcoin-cash","NEAR":"near","FIL":"filecoin","ICP":"internet-computer","FET":"fetch-ai","ZEC":"zcash","ZRO":"layerzero","W":"wormhole","RSR":"reserve-rights-token","S":"sonic-3","IO":"io-net","CKB":"nervos-network","METIS":"metis-token","PLUME":"plume","EIGEN":"eigenlayer","AXL":"axelar","DYM":"dymension","VTHO":"vethor-token","RONIN":"ronin","CYBER":"cyberconnect","BAT":"basic-attention-token","FLOW":"flow","ALGO":"algorand","LUMIA":"lumia","MAGIC":"magic","PUMP":"pump-fun","VELODROME":"velodrome-finance"}
CG_IDS["GTC"]="gitcoin"
CG_IDS.update({asset: entry["coingecko_id"] for asset, entry in MAPPINGS.items() if entry.get("coingecko_id")})

def now():
    return dt.datetime.now(UTC).isoformat(timespec="microseconds")

def request(url, params=None, attempts=2, max_wait=None):
    global NEXT
    hostname = urllib.parse.urlsplit(url).hostname or ''
    is_coingecko = hostname == 'api.coingecko.com'
    is_binance = hostname == 'binance.com' or hostname.endswith('.binance.com')
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(attempts):
        from request_gate import before_send
        before_send(reserve=False)
        if is_coingecko:
            COINGECKO_PACER.wait(max_wait=max_wait)
        elif is_binance:
            BINANCE_PACER.wait(max_wait=max_wait)
        else:
            with LOCK:
                at = max(time.monotonic(), NEXT)
                NEXT = at + 0.125
            pause = at - time.monotonic()
            if pause > 0:
                time.sleep(pause)
        from request_telemetry import record
        before_send()
        dispatched=time.monotonic()
        try:
            req = urllib.request.Request(url, headers={"User-Agent":"PerpetualEvidencePanel/1.0", "Accept":"application/json"})
            with urllib.request.urlopen(req, timeout=15) as response:
                data=json.load(response)
                record(url,params,attempt+1,getattr(response,'status',200),time.monotonic()-dispatched)
                return data
        except urllib.error.HTTPError as exc:
            record(url,params,attempt+1,exc.code,time.monotonic()-dispatched,retry_after=exc.headers.get('Retry-After') if exc.headers else None)
            exc.close()
            if exc.code in (418, 429):
                if is_coingecko:
                    delay = COINGECKO_PACER.rate_limited(exc.headers.get('Retry-After') if exc.headers else None)
                    raise RuntimeError(f"HTTP {exc.code}: rate limited; cooldown {delay:.0f}s; reduced rate; no immediate retry") from exc
                if is_binance:
                    delay = BINANCE_PACER.rate_limited(exc.headers.get('Retry-After') if exc.headers else None)
                    raise RuntimeError(f"HTTP {exc.code}: Binance rate limited; shared cooldown {delay:.0f}s; reduced rate; no immediate retry") from exc
                raise RuntimeError(f"HTTP {exc.code}: rate limited; no immediate retry") from exc
            if attempt + 1 == attempts:
                raise RuntimeError(f"HTTP {exc.code}") from exc
        except Exception as exc:
            record(url,params,attempt+1,type(exc).__name__,time.monotonic()-dispatched)
            if attempt + 1 == attempts:
                raise
        time.sleep(0.4)

def safe(url, params=None):
    try:
        return request(url, params), None
    except Exception as exc:
        return None, str(exc)[:180]

def number(value):
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (ValueError, TypeError, OverflowError):
        return None

def ema(values, period):
    result = values[0]
    alpha = 2 / (period + 1)
    for value in values[1:]:
        result += alpha * (value - result)
    return result


def rsi(values, period=14):
    """Wilder RSI on closes; None if insufficient bars."""
    if len(values) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def sustained_new_lows(closes, bars_per_day=6, days=7, streak=3):
    """True when recent daily lows keep making new lows (~7d window)."""
    need = bars_per_day * days
    if len(closes) < need:
        return None
    window = closes[-need:]
    daily_lows = [min(window[i:i + bars_per_day]) for i in range(0, need, bars_per_day)]
    if len(daily_lows) < streak + 1:
        return None
    return all(daily_lows[i] < daily_lows[i - 1] for i in range(len(daily_lows) - streak, len(daily_lows)))


def ignition_fields(closes, volumes, highs=None, lows=None):
    """Stage2 EMA stack / cross / vol7 / grade (v4 full_cross + 10/3 new-lows + amplitude).

    strong = full_cross(EMA25首次上穿99且完整排列) + 当根前7量比>=2。
    下一根放量确认由 ema_squeeze_store 跨扫描持久化，不在本纯函数内伪造第三根。
    """
    out = {
        "emaStack": None, "emaStackPrev": None, "ema7Cross25": None,
        "priceAboveEma99": None, "ema7Below25": None,
        "barVolumeRatio7": None, "ignitionGrade": None,
        "rsi4h": None, "sidewaysOk": None, "deathTrend": None,
        "emaQuality": None,
        "fullCross": None, "mediumCrossEvent": None,
        "newLowCount10": None, "amplitudeRatio20": None, "amplitudeOk": None,
        "crossEma99Value": None,
    }
    n = len(closes)
    if n < 120:
        out["emaQuality"] = "insufficient"
        return out
    if n < 250:
        out["emaQuality"] = "short_window"
    elif n < 500:
        out["emaQuality"] = "adequate"
    else:
        out["emaQuality"] = "long_window"
    curr = [ema(closes, p) for p in (7, 25, 99)]
    prior = [ema(closes[:-1], p) for p in (7, 25, 99)]
    out["emaStack"] = curr[0] > curr[1] > curr[2]
    out["emaStackPrev"] = prior[0] > prior[1] > prior[2]
    out["ema7Cross25"] = prior[0] <= prior[1] and curr[0] > curr[1]
    out["priceAboveEma99"] = closes[-1] > curr[2]
    out["ema7Below25"] = curr[0] < curr[1]
    out["crossEma99Value"] = curr[2]
    out["fullCross"] = _full_cross(curr[0], curr[1], curr[2], prior[0], prior[1], prior[2])
    out["mediumCrossEvent"] = _medium_cross(curr[0], curr[1], curr[2], prior[0], prior[1], closes[-1])
    if n == 120:
        # The previous state has only119 warmup bars: a new event isn't proven.
        out['fullCross'] = out['mediumCrossEvent'] = out['ema7Cross25'] = None
        out['emaStackPrev'] = None
    prior7 = volumes[-8:-1]
    if len(prior7) == 7 and all(finite(v) is not None and v >= 0 for v in volumes[-8:]) and sum(prior7) > 0:
        out["barVolumeRatio7"] = volumes[-1] / (sum(prior7) / 7)
    vol7 = out["barVolumeRatio7"]
    grade = "none"
    # v4: strong 认 EMA25 首次上穿99，不再用 not emaStackPrev
    if out["fullCross"] is True and vol7 is not None and vol7 >= 2:
        grade = "strong"
    elif out["mediumCrossEvent"] is True and vol7 is not None and vol7 >= 2:
        # 若同时满足强定义则以强为准（相同确认时点强级优先）
        grade = "medium"
    elif out["priceAboveEma99"] and out["ema7Below25"]:
        grade = "weak"
    out["ignitionGrade"] = grade
    out["rsi4h"] = rsi(closes, 14)
    # v4: 10根/至少3次逐根新低，替代旧7日分组下降
    death = death_trend_new_lows(closes)
    out["deathTrend"] = death
    out["newLowCount10"] = new_low_count(closes)
    if highs is not None and lows is not None:
        out["amplitudeRatio20"] = amplitude_ratio(highs, lows)
        out["amplitudeOk"] = amplitude_ok(highs, lows)
    else:
        out["amplitudeRatio20"] = None
        out["amplitudeOk"] = None
    amp = out["amplitudeOk"]
    if out["rsi4h"] is None or death is None or amp is None:
        out["sidewaysOk"] = None
    else:
        out["sidewaysOk"] = (30 <= out["rsi4h"] <= 55) and (death is False) and (amp is True)
    return out

def normalize(asset):
    if asset == "1MBABYDOGE": return "BABYDOGE"
    asset = re.sub(r"^(?:1000000|100000|10000|1000)", "", asset)
    return ALIASES.get(asset, asset)

def load_caps(rows=None, evidence_dir=None):
    from scan_data_policy import cap_required, validated_markets
    errors, markets = [], []
    assets = {r['asset'] for r in rows if cap_required(r)} if rows is not None else set(MAPPINGS)
    targets = sorted({CG_IDS[a] for a in assets if CG_IDS.get(a)})
    sequence = 0

    def fetch(url, params=None):
        nonlocal sequence
        data, error = safe(url, params)
        retrieved = now()
        sequence += 1
        if evidence_dir is not None:
            dest = pathlib.Path(evidence_dir)
            dest.mkdir(parents=True, exist_ok=True)
            with (dest / f'request-{sequence:03d}.json').open('x', encoding='utf-8') as output:
                json.dump(dict(url=url, params=params, retrieved_at=retrieved, data=data, error=error), output, ensure_ascii=False)
        return data, error, retrieved

    # A reviewed ID needs no directory lookup. Unmapped new assets stay pending;
    # a directory response can discover candidates but cannot establish trust.
    coin_list = []
    for offset in range(0, len(targets), 250):
        chunk = targets[offset:offset+250]
        data, error, retrieved = fetch('https://api.coingecko.com/api/v3/coins/markets', {
            'vs_currency':'usd', 'ids':','.join(chunk), 'per_page':250, 'page':1, 'sparkline':'false'})
        if error:
            errors.append(f'CoinGecko ID batch {offset//250+1}: ' + error)
            if any(token in error.lower() for token in ('429', '418', 'rate limited', 'cooldown')):
                break
            continue
        accepted, rejected = validated_markets(data, chunk, retrieved)
        markets.extend(accepted)
        errors.extend(f'CoinGecko ID batch {offset//250+1}: ' + reason for reason in rejected)
    paprika, error, retrieved = fetch('https://api.coinpaprika.com/v1/tickers')
    if error:
        errors.append('CoinPaprika: ' + error)
    from scan_data_policy import fresh_source_time
    paprika = [dict(c, capRetrievedAt=retrieved) for c in paprika
               if isinstance(c, dict) and fresh_source_time(c.get('last_updated'), retrieved)] if isinstance(paprika, list) else []
    return coin_list, markets, paprika, errors


def resolve_cap(row, coin_list, markets, paprika):
    asset = row["asset"]
    coin_list, markets, paprika = ([c for c in seq if isinstance(c, dict) and isinstance(c.get("id"), str)] if isinstance(seq, list) else [] for seq in (coin_list, markets, paprika))
    # Do not attribute stocks/commodities to a crypto token with the same ticker.
    if row.get("underlyingType") != "COIN":
        return {"capUsd":None,"capStatus":"指数：单币流通市值不适用" if row.get("underlyingType") == "INDEX" else "非普通加密资产，单币市值口径待核对","capSource":None,"capTrusted":False,"capApplicable":False}
    chosen = CG_IDS.get(asset)
    identity = MAPPINGS.get(asset)
    trusted = bool(chosen) and (identity is None or identity["trusted"])
    ids = [c["id"] for c in coin_list if (c["symbol"].upper() if isinstance(c.get("symbol"), str) else "") == asset]
    if chosen:
        matches = [c for c in markets if c["id"] == chosen]
        mapping = "明确项目ID映射，仍需定期核对" if trusted else "目录地址已匹配，期货资产绑定待核对"
    elif len(ids) == 1:
        matches = [c for c in markets if c["id"] == ids[0]]
        mapping = "CoinGecko唯一代码匹配，地址未核对"
    else:
        matches = []
        mapping = "同名代码或未收录，映射未解决"
    if len(matches) == 1 and number(matches[0].get("market_cap")) is not None and number(matches[0].get("market_cap")) > 0:
        c = matches[0]
        supply, price = number(c.get('circulating_supply')), number(c.get('current_price'))
        if 'circulating_supply' in c and (supply is None or supply <= 0 or price is None or price <= 0 or abs(float(c['market_cap']) / (price*supply) - 1) > .05):
            matches = []
        else:
            return {"capUsd":float(c["market_cap"]),"capStatus":mapping,"capSource":"CoinGecko","capId":c["id"],"capName":c.get("name") or c["id"],"capAsOf":c.get("last_updated"),"capPrice":number(c.get("current_price")),"capTrusted":trusted,"capApplicable":True,"capMappingRevision":MAPPING_REVISION if identity else None,"capIdentityBasis":identity.get("basis") if identity else None,"capIdentityVerified":trusted,"capCirculatingSupply":supply,"capObservedAt":c.get("last_updated"),"capTimestampKind":"source_observed_at","capRetrievedAt":c.get("capRetrievedAt"),"capSourceUrl":"https://api.coingecko.com/api/v3/coins/markets","capQualityStatus":"来源市值与流通量乘价格一致" if supply else "供应商公开市值；流通量未披露"}
    # An explicit independent provider ID has its own contract-address evidence.
    paprika_id = (identity or {}).get('coinpaprika_id')
    candidates = [c for c in paprika if c.get('id') == paprika_id] if paprika_id else [c for c in paprika if (c["symbol"].upper() if isinstance(c.get("symbol"), str) else "") == asset] if not chosen else []
    if len(candidates) == 1:
        c = candidates[0]; quotes = c.get("quotes"); q = quotes.get("USD") if isinstance(quotes, dict) else None
        q = q if isinstance(q, dict) else {}
        if number(q.get("price")) is not None and number(q.get("price")) > 0 and number(q.get("market_cap")) is not None and number(q.get("market_cap")) > 0:
            return {"capUsd":float(q["market_cap"]),"capStatus":"CoinPaprika已核对项目ID" if paprika_id else "CoinPaprika唯一代码匹配，项目映射待核对",
                    "capSource":"CoinPaprika","capId":c["id"],"capName":c.get("name") or c["id"],"capAsOf":c.get("last_updated"),
                    "capPrice":number(q.get("price")),"capTrusted":bool(paprika_id and identity['trusted']),
                    "capIdentityVerified":bool(paprika_id and identity['trusted']),"capApplicable":True,
                    "capMappingRevision":MAPPING_REVISION if identity else None,"capIdentityBasis":(identity or {}).get('basis'),
                    "capRetrievedAt":c.get("capRetrievedAt"),"capTimestampKind":"source_observed_at","capObservedAt":c.get("last_updated"),"capSourceUrl":"https://api.coinpaprika.com/v1/tickers", "capQualityStatus":"供应商公开市值；此响应未独立披露流通量"}
    return {"capUsd":None,"capStatus":mapping + ("；指定ID暂未取得有效市值" if chosen else ""),"capSource":None,"capTrusted":False,"capApplicable":True,
            "capIdentityVerified":bool(identity and identity['trusted']),"capMappingRevision":MAPPING_REVISION if identity else None,
            "capIdentityBasis":(identity or {}).get('basis')}


def fetch_contract(meta, host, tick, premium, intervals, only=None, requester=None, count_progress=True, target_close_boundary=None):
    symbol = meta["symbol"]
    inverse = host == "https://dapi.binance.com"
    version = "/dapi/v1/" if inverse else "/fapi/v1/"
    read = requester or safe
    wanted = lambda name: only is None or name in only
    oi, oi_error = read(host + version + "openInterest", {"symbol":symbol}) if wanted('oi') else (None, None)
    ratio_args = {"pair":meta["pair"],"period":"5m","limit":2} if inverse else {"symbol":symbol,"period":"5m","limit":2}
    ratio, ratio_error = read(host + ("/futures/data/globalLongShortAccountRatio"), ratio_args) if wanted('ratio') else (None, None)
    candle_args = dict(symbol=symbol,interval='4h',limit=1500)
    if target_close_boundary is not None:candle_args['endTime']=(target_close_boundary//14_400_000)*14_400_000
    candles, candle_error = read(host + version + "klines", candle_args) if wanted('candles') else (None, None)
    price = number(tick.get("lastPrice"))
    mark = number(premium.get("markPrice"))
    row = {"symbol":symbol,"asset":normalize(meta["baseAsset"]),"market":"COIN-M" if inverse else "USDⓈ-M","quote":meta["quoteAsset"],"pair":meta["pair"],"underlyingType":meta.get("underlyingType"),"price":price,"markPrice":mark,"indexPrice":number(premium.get("indexPrice")),"priceChange24Pct":number(tick.get("priceChangePercent")),"high24":number(tick.get("highPrice")),"low24":number(tick.get("lowPrice")),"oiUsd":None,"accountRatio":None,"topAccountRatio":None,"topPositionRatio":None,"fundingPct":number(premium.get("lastFundingRate")),"fundingHours":intervals.get(symbol),"fundingIntervalSource":"fundingInfo配置或标准8h默认值，候选再核对历史","asOf":now(),"errors":{},"source":"Binance public REST","url":"https://www.binance.com/en/futures/"+symbol}
    row['fundingIntervalStatus'] = 'configured' if symbol in intervals else 'unknown'
    row['fundingIntervalSource'] = 'fundingInfo有效配置' if symbol in intervals else 'fundingInfo未取得或配置缺失，年化未知'
    # A successful endpoint can establish the documented standard 8h fallback.
    if intervals.get('__source_ok__') and symbol not in intervals:
        row['fundingHours'] = 8
        row['fundingIntervalStatus'] = 'provider_default'
        row['fundingIntervalSource'] = 'fundingInfo成功且未列出调整，按源契约默认8h；仍可用结算历史复核'
    row['fundingAprPct'] = row['fundingDailyPct'] = None
    if row['fundingPct'] is not None: row['fundingPct'] *= 100
    if row['fundingPct'] is not None and number(row.get('fundingHours')) and row['fundingHours'] > 0:
        row["fundingDailyPct"] = row["fundingPct"] * 24 / row["fundingHours"]
        row["fundingAprPct"] = row["fundingDailyPct"] * 365
    if oi:
        quantity = number(oi.get("openInterest"))
        if quantity is not None and (inverse or mark is not None):
            row["oiUsd"] = quantity * float(meta["contractSize"]) if inverse else quantity * mark
        row["oiAsOf"] = oi.get("time")
    if oi_error: row["errors"]["OI"] = oi_error
    if ratio:
        row["accountRatio"] = number(ratio[-1].get("longShortRatio"))
        row["accountAsOf"] = ratio[-1].get("timestamp")
    if ratio_error: row["errors"]["账户比"] = ratio_error
    if inverse:
        # COIN-M quote volume is in base coin, not USD. Do not assume it is USD.
        row["volume24Usd"] = number(tick.get("volume")) * float(meta["contractSize"]) if number(tick.get("volume")) is not None else None
    else:
        row["volume24Usd"] = number(tick.get("quoteVolume"))
    row["_candles"] = {}
    if candles:
        cutoff = target_close_boundary if target_close_boundary is not None else int(time.time()*1000)
        closed = [k for k in candles if isinstance(k, list) and len(k) >= 12 and number(k[6]) is not None and int(k[6]) < cutoff]
        closed = [k for k in closed if len(k)>=12 and all(number(k[i]) is not None for i in (1,2,3,4,5,7)) and float(k[1])>0 and float(k[4])>0]
        row['_candles']['4h'] = closed
        volumes = [float(k[5])*float(meta["contractSize"]) if inverse else float(k[7]) for k in closed]
        row['emaObservation'] = bind_identity(observe(closed, volumes, 'usd_contract_notional' if inverse else 'quote_notional'), row.get('market'), row.get('symbol'))
        row['contractSize'] = float(meta['contractSize']) if inverse else None
        row['emaCandleCount'] = len(closed)
        if closed:
            row['candleAsOf'] = int(closed[-1][6])
        row['emaQuality'] = 'insufficient' if len(closed) < 120 else 'gap'
        # Only the actual next open is a reference. It is never used in EMA/grade.
        if closed:
            next_bar = next((k for k in candles if isinstance(k, list) and len(k) >= 7 and k[0] == int(closed[-1][6])+1), None)
            if next_bar and finite(next_bar[1], True) is not None:
                row['_nextOpenReference'] = dict(open_time=int(next_bar[0]), price=float(next_bar[1]), source='Binance_4h_open', retrieved_at=now())
        contiguous = all(int(b[0])-int(a[0])==14_400_000 for a,b in zip(closed,closed[1:]))
        if not contiguous: row['errors']['K线连续性']='4h窗口存在缺口，仅归档不计算EMA/量窗'
        row['contractOnboardAt'] = number(meta.get('onboardDate'))
        row['candleFirstOpenAt'] = int(closed[0][0]) if closed else None
        row['emaWarmupRemaining'] = max(0, 120-len(closed))
        onboard = row['contractOnboardAt']
        listing_history = (closed and onboard is not None and 0 <= int(closed[0][0])-onboard < 14_400_000)
        row['candleHistoryStatus'] = ('gap' if not contiguous else 'ready' if len(closed)>=120 else
                                      'listing_warmup' if listing_history else 'insufficient_history')
        if closed and contiguous and len(closed)<120:
            row['emaWarmupExpectedAt'] = int(closed[-1][6]) + row['emaWarmupRemaining']*14_400_000
        # I6 needs eight complete 24h windows, independently of EMA's 120-bar warmup.
        if len(closed) >= 48 and contiguous:
            chunks = [sum(volumes[-6*(i+1):len(volumes)-6*i if i else None]) for i in range(8)]
            baseline = sum(chunks[1:])/7
            row['volumeRatio'] = chunks[0]/baseline if baseline else None
            row['volumeWindowUsd'] = chunks[0]
            row['volumeBaselineUsd'] = baseline
            row['priceChangeWindowPct'] = (float(closed[-1][4])/float(closed[-6][1])-1)*100
        if len(closed) >= 120 and contiguous:
            closes = [float(k[4]) for k in closed]
            row["ema4h"] = [ema(closes,p) for p in (7,25,99)]
            row["emaCandleCount"] = len(closed)
            row["closed4h"] = closes[-1]
            row["candleAsOf"] = int(closed[-1][6])
            row["belowAllEma"] = all(closes[-1] < e for e in row["ema4h"])
            prior = [ema(closes[:-1],p) for p in (7,25,99)]
            row["crossEma99"] = closes[-2] <= prior[2] and closes[-1] > row["ema4h"][2]
            row["previousBelowAllEma"] = all(closes[-2] < e for e in prior)
            highs = [float(k[2]) for k in closed]
            lows = [float(k[3]) for k in closed]
            # Keep legacy 20-bar ratio for T8 / old triggers; ignition uses barVolumeRatio7.
            row["barVolumeRatio"] = volumes[-1]/(sum(volumes[-21:-1])/20) if len(volumes) >= 21 and sum(volumes[-21:-1]) else None
            row.update(ignition_fields(closes, volumes, highs, lows))
            row["emaMethod"] = f"{len(closed)}根4h已收盘合约K线，EMA(非SMA)首值初始化；窗长已提至最多1500；emaQuality={row.get('emaQuality')}；与图表全历史EMA仍可能略有差异"
    if candle_error: row['errors']['K线'] = candle_error
    daily_args=dict(symbol=symbol,interval='1d',limit=1000)
    if target_close_boundary is not None:daily_args['endTime']=(target_close_boundary//86_400_000)*86_400_000
    daily, daily_error = read(host + version + 'klines', daily_args) if wanted('daily') else (None, None)
    if daily:
        row['_candles']['1d'] = [k for k in daily if len(k)>=12 and int(k[6])<(target_close_boundary if target_close_boundary is not None else int(time.time()*1000)) and all(number(k[i]) is not None for i in (1,2,3,4,5,7))]
    if daily_error: row['errors']['日线归档']=daily_error
    if row['quote'] not in ('USD','USDT','USDC'):
        for field in ('oiUsd','volume24Usd','volumeWindowUsd','volumeBaselineUsd'):
            row[field.replace('Usd','Quote')] = row.get(field)
            row[field] = None
        row['quoteUnitStatus'] = '原始报价为'+row['quote']+'；无同期美元转换证据，不标作USD'
    available=now()
    row['observed_at']=available
    row['fieldAvailableAt']={field:available for field in row if not field.startswith('_')}
    row['candleArchiveScope']='最多1500根4h/最多1000根日线返回窗口；EMA99质量见emaQuality；不是全历史ATH'

    if count_progress:
        with LOCK:
            PROGRESS["completed"] += 1
    return row

def classify(row):
    oi_ratio = row.get("oiCapPct")
    account = row.get("accountRatio")
    annual = row.get("fundingAprPct")
    vol = row.get("volumeRatio")
    delta = row.get("priceChangeWindowPct")
    conditions = indicator_states(row)
    row["signals"] = conditions
    row["knownCount"] = sum(c is not None for c in conditions)
    row["hitCount"] = sum(c is True for c in conditions)
    row["riskFlags"] = []
    if row.get("volume24Usd") is not None and row["volume24Usd"] < 5_000_000: row["riskFlags"].append("成交额偏低，需核对盘口")
    if annual is not None and abs(number(row.get("fundingDailyPct")) or 0) > 1: row["riskFlags"].append("费率极端；每日外推超过1%，优先核查风险")
    if oi_ratio is not None and oi_ratio > 50: row["riskFlags"].append("OI/市值偏高")
    if row.get("capUsd") and not row.get("capTrusted"): row["riskFlags"].append("市值价格一致性待核对" if row.get("capIdentityVerified") else "市值项目映射待核对")
    if (row.get("oiAgeMinutes") or 0) > 10: row["riskFlags"].append("OI源记录超过10分钟")
    if (row.get("capAgeMinutes") or 0) > 60: row["riskFlags"].append("市值源记录超过60分钟")
    if row.get("deathTrend") is True: row["riskFlags"].append("近10根4H至少3次收盘新低，Stage1蓄力排除")
    if row.get("emaQuality") == "short_window": row["riskFlags"].append("EMA历史窗偏短，EMA99与全历史图可能偏差")
    if row.get("ignitionGrade") == "weak": row["riskFlags"].append("弱穿线：价上EMA99但EMA7/25仍死叉，勿当入场")
    # Stage1 watchlist hard gates: I1+I2 only；I3 funding is handbook 加分项（bonus），保留字段与 I3 命中，不挡候选。
    core12 = conditions[:2]
    if any(c is None for c in core12): row["state"] = "关键数据不足"
    elif all(core12):
        row["state"] = "杠杆异动候选"
        if conditions[2] is True:
            row["state"] += " · 费率加分"
    else:
        row["state"] = "未满足核心组合"
    # Stage2 ignition grades replace bare crossEma99「收盘突破线索」.
    grade = row.get("ignitionGrade")
    if row["state"].startswith("杠杆异动候选"):
        if grade == "strong":
            row["state"] = "点火强信号·观察"
        elif grade == "medium":
            row["state"] = "点火中信号·观察"
    if row.get("belowAllEma") and row.get("sidewaysOk") is True and not str(row.get("state","")).startswith("点火"):
        if row["state"].startswith("杠杆异动候选"):
            if "蓄力观察" not in row["state"]:
                row["state"] += " · 蓄力观察"
        elif row["state"] == "未满足核心组合":
            row["state"] = "蓄力观察"
    watch_prefixes = ("杠杆异动候选", "点火强信号·观察", "点火中信号·观察", "蓄力观察")
    if any(str(row.get("state","")).startswith(s) for s in watch_prefixes) and row["riskFlags"]:
        if "风险待核查" not in row["state"]:
            row["state"] += " · 风险待核查"
    row["strategyCandidates"] = strategy_results(row)
    row["registryHash"] = VERSION_HASH
    row["tradeStatus"] = "观察，不自动生成开多指令；蓄力≠入场；需现货、回踩、深度和事件复核"

def timestamp_age(value):
    try:
        when = dt.datetime.fromtimestamp(value/1000,UTC) if isinstance(value,(float,int)) else dt.datetime.fromisoformat(value.replace("Z","+00:00"))
        return max(0,(dt.datetime.now(UTC)-when).total_seconds()/60)
    except Exception:
        return None

def refine(row):
    host = "https://dapi.binance.com" if row["market"] == "COIN-M" else "https://fapi.binance.com"
    args = {"pair":row["pair"],"period":"5m","limit":2} if row["market"] == "COIN-M" else {"symbol":row["symbol"],"period":"5m","limit":2}
    for path, field in (("topLongShortAccountRatio","topAccountRatio"),("topLongShortPositionRatio","topPositionRatio")):
        data, error = safe(host+"/futures/data/"+path,args)
        if data: row[field]=number(data[-1].get("longShortRatio"))
        if error: row["errors"][field]=error
    prefix = "/dapi/v1/" if row["market"] == "COIN-M" else "/fapi/v1/"
    history, error = safe(host+prefix+"fundingRate", {"symbol":row["symbol"],"limit":4})
    if history:
        row["settledFunding"] = [{"time":h["fundingTime"],"pct":float(h["fundingRate"])*100} for h in history]
        if len(history)>1:
            row["observedFundingHours"] = (history[-1]["fundingTime"]-history[-2]["fundingTime"])/3_600_000
        row["negativeSettlements"] = sum(float(h["fundingRate"])<0 for h in history)
    if error: row['errors']['结算历史']=error
    retrieved=now()
    for field in ('topAccountRatio','topPositionRatio','settledFunding','observedFundingHours','negativeSettlements'):
        if field in row: row.setdefault('fieldAvailableAt',{})[field]=retrieved

def _scan(archive, target_close_boundary=None):
    PROGRESS.update(running=True,completed=0,total=0,phase="读取合约目录与市值")
    started = now()
    errors = []
    universes = []
    for host,prefix in (("https://fapi.binance.com","/fapi/v1/"),("https://dapi.binance.com","/dapi/v1/")):
        directory = request(host+prefix+"exchangeInfo")
        ticker = request(host+prefix+"ticker/24hr")
        premium = request(host+prefix+"premiumIndex")
        info,error = safe(host+prefix+"fundingInfo")
        if error: errors.append(host+" fundingInfo: "+error)
        contracts = [s for s in directory["symbols"] if s.get("contractType")=="PERPETUAL" and s.get("status",s.get("contractStatus"))=="TRADING"]
        ticks={t["symbol"]:t for t in ticker}
        marks={p["symbol"]:p for p in premium}
        if not contracts: raise ValueError('合约目录为空：'+host)
        intervals={i['symbol']:number(i.get('fundingIntervalHours')) for i in info or [] if number(i.get('fundingIntervalHours')) and float(i['fundingIntervalHours'])>0}
        intervals['__source_ok__'] = error is None and isinstance(info,list)
        universes.extend((m,host,ticks.get(m["symbol"],{}),marks.get(m["symbol"],{}),intervals) for m in contracts)
    coins,markets,paprika,cap_errors=load_caps(rows=[dict(asset=normalize(u[0]['baseAsset']), underlyingType=u[0].get('underlyingType'), market='COIN-M' if 'dapi' in u[1] else 'USDⓈ-M', symbol=u[0]['symbol']) for u in universes], evidence_dir=archive.path / 'cap-acquisition');errors.extend(cap_errors)
    PROGRESS.update(total=len(universes),phase="全量查询OI、账户比、已收盘4hK线")
    rows=[]
    # Persist raw completed observations before enrichment so partial scans remain reviewable.
    # Each run owns an immutable observation archive; no mixed global append.
    ROOT.mkdir(parents=True,exist_ok=True)
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        futures={pool.submit(fetch_contract,*u,**({'target_close_boundary':target_close_boundary} if target_close_boundary is not None else {})):u[0]["symbol"] for u in universes}
        for future in cf.as_completed(futures):
            try:
                row=clean(future.result())
                row['candleArchives']={interval:archive.candles(row,interval,raw) for interval,raw in row.pop('_candles',{}).items()}
                archive.record(row)
                rows.append(row)
            except Exception as exc:errors.append(futures[future]+": "+str(exc)[:180])
            if len(rows)%50==0:print(json.dumps(dict(PROGRESS),ensure_ascii=False),flush=True)
    for row in rows:
        cap = resolve_cap(row,coins,markets,paprika)
        row.update(cap)
        for field in cap: row['fieldAvailableAt'][field]=now()
        row["oiCapPct"] = row["oiUsd"]/row["capUsd"]*100 if row.get("oiUsd") is not None and row.get("capUsd",0) else None
        row["oiAgeMinutes"] = timestamp_age(row.get("oiAsOf"))
        row["capAgeMinutes"] = timestamp_age(row.get("capAsOf"))
        from scan_verification import refresh_derived
        refresh_derived(row)
        classify(row)
    from scan_verification import verify_rows
    PROGRESS['phase'] = '读取聪明钱4h流入榜单'
    from smart_money import collect as collect_smart_money, apply as apply_smart_money
    try:
        smart_context = collect_smart_money(ROOT, evidence_dir=archive.path / 'smart-money')
        smart_coverage = apply_smart_money(rows, smart_context)
    except Exception as exc:
        errors.append('聪明钱来源: '+type(exc).__name__)
        smart_coverage = {'schema':'binance-smart-money-v1', 'covered_contracts':0,
                          'covered_assets':0, 'status':'source_error'}
        for row in rows:
            row.update(smartMoneyNetInflowUsd=None, smartMoneyTraders=None, smartMoneyEvidence=None,
                       smartMoneyStatus='source_error', smartMoneyStatusNote='本轮聪明钱来源不可用；未沿用旧值')
    PROGRESS['phase'] = '自动补查一次：缺失行情与市值' 
    verification = verify_rows(rows, (coins, markets, paprika), universes=universes, evidence_dir=archive.path / 'verification', enforce_fresh_caps=True, cap_errors=cap_errors, target_close_boundary=target_close_boundary)
    for row in rows:
        for interval, raw in row.pop('_candles', {}).items():
            if raw:
                row.setdefault('candleArchives', {})[interval] = archive.candles(row, interval, raw)
    aggregate_oi(rows,[{'asset':normalize(u[0]['baseAsset'])} for u in universes])
    # ①② 宽候选（classify 核心硬门槛）；①②③ 旧组合统计保留但不得混称「候选」
    candidates12=sorted([r for r in rows if len(r.get("signals") or [])>=2 and r["signals"][0] is True and r["signals"][1] is True],key=lambda r:(bool(r.get("capTrusted")),r.get("volume24Usd") or 0),reverse=True)
    candidates123=sorted([r for r in rows if all(s is True for s in (r.get("signals") or [])[:3])],key=lambda r:(bool(r.get("capTrusted")),r.get("volume24Usd") or 0),reverse=True)
    candidates=candidates12  # refine 优先覆盖宽候选
    PROGRESS["phase"]="复核核心候选的大户账户、持仓及结算历史"
    with cf.ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(refine,candidates[:30]))
    # 候选生命周期 / cross 等待（v4）；失败不阻断扫描主链
    try:
        from ema_squeeze_store import EmaSqueezeStore
        _ess = EmaSqueezeStore(ROOT)
    except Exception as _ess_exc:
        _ess = None
        errors.append("ema_squeeze_store: "+str(_ess_exc)[:180])
    for row in rows:
        classify(row)
        if _ess is not None:
            try:
                row["emaSqueezeStage"] = _ess.evaluate_row(row)
            except Exception as _st_exc:
                row.setdefault("errors", {})["emaSqueezeStage"] = str(_st_exc)[:180]
        row.pop('_nextOpenReference', None)
        # Derived and enriched fields become available only after computation.
        for field in row:
            if field!='fieldAvailableAt': row['fieldAvailableAt'].setdefault(field,now())
    store_meta = {"path": None, "active": 0, "cycles": 0, "persisted": False}
    if _ess is not None:
        try:
            store_meta = _ess.persist()
            store_meta["persisted"] = True
        except Exception as _persist_exc:
            errors.append("ema_squeeze_store_persist: "+str(_persist_exc)[:180])
            store_meta["persisted"] = False
            store_meta["error"] = str(_persist_exc)[:180]
    stage1_count = sum(1 for r in rows if (r.get("emaSqueezeStage") or {}).get("candidateStatus")=="active")
    confirmed_count = sum(1 for r in rows if isinstance((r.get("emaSqueezeStage") or {}).get("crossConfirm"), dict) and (r.get("emaSqueezeStage") or {}).get("crossConfirm",{}).get("event_id") and not (r.get("emaSqueezeStage") or {}).get("crossConfirm",{}).get("historical") and not (r.get("emaSqueezeStage") or {}).get("crossConfirm",{}).get("duplicate"))
    result={"startedAt":started,"finishedAt":now(),"scope":"全部状态为TRADING的Binance USDⓈ-M与COIN-M永续合约","total":len(universes),"received":len(rows),"markets":dict(Counter(r["market"] for r in rows)),"errors":errors,"capSource":"CoinGecko；缺失时CoinPaprika或Binance Alpha（明确标注来源与时间口径）","rows":sorted(rows,key=lambda r:(len(r.get("signals") or [])>=2 and r["signals"][0] is True and r["signals"][1] is True,r["hitCount"],r.get("volume24Usd") or 0),reverse=True),"rules":{"oiCapPct":REGISTRY["indicators"][0]["rule"]["value"],"accountRatio":REGISTRY["indicators"][1]["rule"]["value"],"fundingAprPct":REGISTRY["indicators"][2]["rule"]["value"],"volumeRatio":REGISTRY["indicators"][5]["rule"]["args"][0]["value"],"quietChangePct":REGISTRY["indicators"][5]["rule"]["args"][1]["value"],"warning":"未经回测的初筛阈值，不是胜率或开多概率"},"smartMoneyCoverage":smart_coverage,"candidateCount":len(candidates12),"candidateCount12":len(candidates12),"candidateCount123":len(candidates123),"candidateCountNote":"candidateCount=①②宽候选；candidateCount123=旧①②③组合；勿混称","stage1ActiveCount":stage1_count,"ignitionConfirmedCount":confirmed_count,"emaSqueezeStore":store_meta}
    ROOT.mkdir(parents=True,exist_ok=True)
    result['expectedAssetContracts']=dict(Counter(normalize(u[0]['baseAsset']) for u in universes))
    from scan_data_policy import cap_required
    result['expectedCapContracts'] = sorted(m['market']+'|'+m['symbol'] for u in universes for m in [dict(underlyingType=u[0].get('underlyingType'), symbol=u[0]['symbol'], market='COIN-M' if 'dapi' in u[1] else 'USDⓈ-M')] if cap_required(m))
    result['target_close_boundary'] = target_close_boundary
    result['verification'] = verification
    result['registryHash']=VERSION_HASH
    PROGRESS['phase'] = '检查并更新完整日储备辅助证据'
    from reserve_refresh import refresh as refresh_reserves
    result['reserveEvidence'] = refresh_reserves(ROOT, result)
    result['finishedAt'] = now()
    result=archive.publish(result)
    try:
        write_report(result)
    except OSError as exc:
        print('快照已发布；汇总报告写入失败：'+str(exc),flush=True)
    PROGRESS.update(running=False,phase="完成" if result["quality"]["status"] == "success" else "部分完成，缺口见扫描质量",completed=len(rows))
    print(json.dumps({k:v for k,v in result.items() if k!="rows"},ensure_ascii=False),flush=True)
    return result

def scan(target_close_boundary=None):
    with ScanLease(ROOT):
        archive=ScanArchive(ROOT)
        try:
            return _scan(archive,target_close_boundary=target_close_boundary)
        except Exception as exc:
            archive.status('failed',error=str(exc)[:300])
            PROGRESS.update(phase='失败，保留上次成功快照',error=str(exc)[:300])
            raise
        finally:
            PROGRESS['running']=False


def fmt(value, digits=2):
    return "未取得" if value is None else f"{value:,.{digits}f}"

def write_report(result):
    c12 = result.get("candidateCount12", result.get("candidateCount"))
    c123 = result.get("candidateCount123")
    lines=["# 币安全市场永续异动扫描报告",f"\n采样区间：{result['startedAt']} 至 {result['finishedAt']}（UTC）",f"\n范围：{result['scope']}；应查{result['total']}份合约，取得{result['received']}份。",f"\n①②宽候选：{c12}份；旧①②③组合：{c123}份（费率③为加分，不挡①②候选）。含市值映射待核对项，不代表可开多。", f"\nStage1活跃候选周期：{result.get('stage1ActiveCount')}；本轮确认点火事件：{result.get('ignitionConfirmedCount')}。", "\n链上净流出：未接入。①②为杠杆与账户结构硬门槛，③费率为加分；④为趋势状态，⑥为成交线索；均不能证明吸筹或内幕交易。", "\n## ①②宽候选（核心硬门槛）", "\n| 合约 | OI/市值 | 全体账户比 | 当前费率/周期 | 简单年化 | 4h均线下方 | 24h量倍数 | 市值映射 | 风险 |", "|---|---:|---:|---|---:|---|---:|---|---|"]
    candidates=[r for r in result["rows"] if len(r.get("signals") or [])>=2 and r["signals"][0] is True and r["signals"][1] is True]
    for r in candidates:
        lines.append(f"| {r['symbol']} | {fmt(r.get('oiCapPct'))}% | {fmt(r.get('accountRatio'),3)} | {fmt(r.get('fundingPct'),5)}%/{r['fundingHours']}h | {fmt(r.get('fundingAprPct'))}% | {r.get('belowAllEma')} | {fmt(r.get('volumeRatio'))}× | {r['capStatus']} | {'；'.join(r['riskFlags']) or '仍需事件与盘口核验'} |")
    if not candidates:lines.append("\n没有合约同时满足①②宽候选；不强行挑选标的。")
    lines.append("\n## EMA三线金叉观察（不新增入场过滤）")
    for r in result['rows']:
        if r.get('ignitionGrade') not in ('weak', 'medium', 'strong') and not (r.get('emaSqueezeStage') or {}).get('confirmedEvents'):
            continue
        evidence = dict(grade=r.get('ignitionGrade'), candleAsOf=r.get('candleAsOf'), observation=r.get('emaObservation'), candidate=r.get('emaSqueezeStage'))
        lines.append("\n### " + r['symbol'] + " · " + r['market'] + "\n```json\n" + json.dumps(evidence, ensure_ascii=False, allow_nan=False) + "\n```")
    lines.extend(["\n## 数据与判断边界","\n- OI为名义敞口，合约每一份同时对应多头与空头；不是保证金，也不是可强平规模。","- 默认用单份合约OI/流通市值，另提供同资产币安全部合约汇总；不冒充全市场OI。","- 全体账户比、大户账户比、大户持仓比分开，账户偏空不能推断资金净做空。","- 年化=当前单期费率×24/实际周期×365，不复利，也不是未来付费预测。","- EMA与量倍数只用已收盘4hK线；24h量窗口与之前7个不重叠24h窗口对齐。","- 量价背离可能是对赌、换手或卖出；链上提币可能是内部转账、托管或做市。","- 市值供应、同名项目、合约乘数与样本时间差会影响比例；待核对项不能作为已确认候选。","- 未取得链上数据、完整深度、解锁和公告证据，不输出买卖指令。","\n## API与覆盖错误", "\n"+ ('\n'.join('- '+e for e in result['errors']) or '无目录级错误；单项错误详见scan.json。'),"\n非投资建议。"])
    (ROOT/"DOCS").mkdir(exist_ok=True)
    (ROOT/"DOCS"/"report.md").write_text('\n'.join(lines),encoding="utf-8")

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--scan",action="store_true");args=parser.parse_args()
    if args.scan:
        scan()
    else:
        parser.print_help()


def maybe_ingest_1h_for_row(row, raw_1h, *, root, target_close_boundary=None, observe=None):
    """可选：把已收盘1H写入共享缓存。observe 默认关闭时直接跳过。不改 row 的4H字段。"""
    if not observe or not observe.get("enabled"):
        return None
    try:
        from market_candle_cache import MarketCandleCache
        from b_1h_ingest import ingest_symbol_1h
        cache = MarketCandleCache(root)
        return ingest_symbol_1h(
            cache,
            market=row.get("market"),
            symbol=row.get("symbol"),
            raw_klines=raw_1h or [],
            target_close_boundary=target_close_boundary,
        )
    except Exception as exc:
        row.setdefault("errors", {})["b1hIngest"] = str(exc)[:180]
        return None
