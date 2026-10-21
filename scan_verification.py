"""One bounded missing-field recovery pass. No AI and no identity guessing."""
import math
import json
import time
from pathlib import Path
from collections import Counter, defaultdict


def valid(value, positive=False):
    return (type(value) in (int, float) and math.isfinite(value)
            and (not positive or value > 0))


def refresh_derived(row):
    import scanner
    oi, cap = row.get('oiUsd'), row.get('capUsd')
    row['oiCapPct'] = oi / cap * 100 if valid(oi) and valid(cap, True) else None
    row['oiAgeMinutes'] = scanner.timestamp_age(row.get('oiAsOf'))
    row['capAgeMinutes'] = scanner.timestamp_age(row.get('capAsOf'))
    usd_quote = row.get('quote', 'USDT') in ('USD', 'USDT', 'USDC')
    if not usd_quote:
        row['capPriceGapPct'] = None
        row['capPriceCheckStatus'] = '非美元报价，未直接与美元市值源价格比较'
    if usd_quote and valid(row.get('capPrice'), True) and valid(row.get('markPrice'), True):
        from asset_identity import price_multiplier
        multiplier = price_multiplier(row['symbol'])
        gap = abs(row['markPrice'] / multiplier / row['capPrice'] - 1) * 100
        row['capPriceGapPct'] = gap
        from datetime import datetime
        try:
            source_at = datetime.fromisoformat((row.get('capObservedAt') or row['capAsOf']).replace('Z', '+00:00'))
            scan_at = datetime.fromisoformat(row['asOf'].replace('Z', '+00:00'))
            stale_comparison = abs((source_at - scan_at).total_seconds()) > 900
        except (KeyError, TypeError, ValueError, AttributeError):
            stale_comparison = False
        row['capPriceCheckStatus'] = '价格时点相差超过15分钟，未作同刻校验' if stale_comparison else '相近时点价格比较'
        if gap > 20 and not stale_comparison:
            row['capTrusted'] = False
            row['capStatus'] = row.get('capStatus', '').split('；价格差异')[0] + '；价格差异超过20%'
    scanner.classify(row)


def apply_cap(row, cap):
    """Replace provider metadata together and validate before choosing fallbacks."""
    for key in list(row):
        if key.startswith('cap'):
            row.pop(key)
    row.update(cap)
    refresh_derived(row)


def verify_rows(rows, cap_context=None, *, universes=(), max_requests=16, max_seconds=180, read=None, evidence_dir=None, alpha_context=None, enforce_fresh_caps=False, cap_errors=(), target_close_boundary=None):
    import scanner
    started = scanner.now()
    deadline = time.monotonic() + max_seconds
    summary = {'schema': 'scan-verification-v1', 'rounds': 1, 'started_at': started,
               'request_limit': max_requests, 'time_limit_seconds': max_seconds,
               'requests': 0, 'recovered_fields': 0, 'errors': [], 'changed_contracts': []}
    from scan_data_policy import cap_required, usd_exempt, exclusions, validated_markets, fresh_source_time
    stopped_hosts = set()
    for error in cap_errors:
        if any(token in error.lower() for token in ('429', '418', 'rate limited', 'cooldown')):
            if 'CoinGecko' in error:
                stopped_hosts.add('api.coingecko.com')
            elif 'CoinPaprika' in error:
                stopped_hosts.add('api.coinpaprika.com')

    def capture(url, params, data, error):
        if evidence_dir is not None:
            target = Path(evidence_dir)
            target.mkdir(parents=True, exist_ok=True)
            filename = f"request-{summary['requests']:03d}.json"
            with (target / filename).open('x', encoding='utf-8') as output:
                json.dump({'url':url, 'params':params, 'retrieved_at':scanner.now(), 'data':data, 'error':error}, output, ensure_ascii=False)
            summary.setdefault('evidence', []).append(filename)

    def get(url, params=None):
        host = scanner.urllib.parse.urlsplit(url).hostname
        if host in stopped_hosts:
            return None, '本轮该来源限流，未继续请求'
        remaining = deadline - time.monotonic()
        if summary['requests'] >= max_requests or remaining < 16:
            return None, '本轮补查预算已用完，保留未知'
        try:
            if read is not None:
                summary['requests'] += 1
                data, error = read(url, params)
            else:
                summary['requests'] += 1
                data, error = scanner.request(url, params, attempts=1, max_wait=max(0., remaining - 16)), None
            if error:
                raise ValueError(error)
            capture(url, params, data, None)
            return data, None
        except Exception as exc:
            error = str(exc)[:180]
            summary['errors'].append({'host': host, 'reason': error})
            capture(url, params, None, error)
            if any(token in error.lower() for token in ('429', '418', 'rate limited', 'cooldown')):
                stopped_hosts.add(host)
            return None, error

    def recovered_cap(row):
        # Multiple source attempts recover the same field only once.
        if '市值' not in recovered[id(row)]:
            recovered[id(row)].append('市值')
            summary['recovered_fields'] += 1

    # Every row records distinct unresolved reasons. This does not change the
    # filter's true/false/unknown semantics or mark an unknown as a failed signal.
    pending = {id(r): {} for r in rows}
    recovered = {id(r): [] for r in rows}
    by_contract = {(u[0]['symbol'], 'COIN-M' if 'dapi' in u[1] else 'USDⓈ-M'): u for u in universes}
    groups = {
        'oi': ('oiUsd', 'oiAsOf'),
        'ratio': ('accountRatio', 'accountAsOf'),
        'candles': ('ema4h', 'emaCandleCount', 'closed4h', 'candleAsOf', 'belowAllEma',
                    'crossEma99', 'previousBelowAllEma', 'volumeRatio', 'volumeWindowUsd',
                    'volumeBaselineUsd', 'priceChangeWindowPct', 'barVolumeRatio', 'emaMethod',
                    'barVolumeRatio7', 'emaStack', 'emaStackPrev', 'ema7Cross25',
                    'priceAboveEma99', 'ema7Below25', 'ignitionGrade', 'rsi4h',
                    'sidewaysOk', 'deathTrend', 'emaQuality', 'emaObservation', 'contractSize',
                    'fullCross', 'mediumCrossEvent', 'crossEma99Value', 'newLowCount10',
                    'amplitudeRatio20', 'amplitudeOk', '_nextOpenReference',
                    'candleHistoryStatus', 'emaWarmupRemaining', 'emaWarmupExpectedAt',
                    'candleFirstOpenAt', 'contractOnboardAt'),
        'daily': (),
    }
    missing_caps = [r for r in rows if cap_required(r) and (not valid(r.get('capUsd'), True) or r.get('capTrusted') is not True)]
    coins, markets, paprika = cap_context or ([], [], [])
    if any(not scanner.CG_IDS.get(r['asset']) and not scanner.MAPPINGS.get(r['asset'], {}).get('alpha') for r in missing_caps) and not coins:
        data, error = get('https://api.coingecko.com/api/v3/coins/list')
        coins = data if not error and isinstance(data, list) else []
    coins = [c for c in (coins if isinstance(coins, list) else []) if isinstance(c, dict) and isinstance(c.get('symbol'), str) and isinstance(c.get('id'), str)]
    ids_by_symbol = defaultdict(set)
    for coin in coins:
        if isinstance(coin, dict) and isinstance(coin.get('symbol'), str) and isinstance(coin.get('id'), str):
            ids_by_symbol[coin['symbol'].upper()].add(coin['id'])
    targets = defaultdict(list)
    for row in missing_caps:
        ids = ids_by_symbol[row['asset']]
        chosen = scanner.CG_IDS.get(row['asset'])
        if not chosen and len(ids) == 1:
            chosen = next(iter(ids))
        if chosen:
            targets[chosen].append(row)
        else:
            pending[id(row)]['市值'] = '资产映射有歧义或目录未收录，需确认项目ID/合约地址'
    # A small number of targeted batches replaces another full market-cap crawl.
    target_ids = sorted(targets)
    for offset in range(0, len(target_ids), 250):
        chunk = target_ids[offset:offset+250]
        data, error = get('https://api.coingecko.com/api/v3/coins/markets',
                          {'vs_currency':'usd', 'ids':','.join(chunk), 'per_page':250, 'page':1, 'sparkline':'false'})
        fetched = [c for c in data if isinstance(c, dict) and c.get('id') in chunk] if not error and isinstance(data, list) else []
        if enforce_fresh_caps and not error:
            fetched, rejected = validated_markets(data, chunk, scanner.now())
            summary.setdefault('cap_response_rejections', []).extend(rejected)
        for coin_id in chunk:
            for row in targets[coin_id]:
                try:
                    cap = scanner.resolve_cap(row, coins, fetched, []) if fetched else {}
                except (KeyError, TypeError, ValueError):
                    cap = {}
                    pending[id(row)]['市值'] = '市值源响应结构无效，保留未知'
                if valid(cap.get('capUsd'), True) and cap.get('capId') == coin_id:
                    apply_cap(row, cap)
                    for field in cap:
                        row.setdefault('fieldAvailableAt', {})[field] = scanner.now()
                    recovered_cap(row)
                else:
                    pending[id(row)]['市值'] = error or '定向ID查询无有效市值，保留未知'
        if error and any(token in error.lower() for token in ('429', '418', 'rate limited', 'cooldown')):
            break

    # If the initial fallback request failed, give that independent provider
    # its one recovery attempt too. Do not repeatedly reload a healthy catalog.
    still_missing = [r for r in missing_caps if not valid(r.get('capUsd'), True) or r.get('capTrusted') is not True]
    if still_missing:
        data, error = (paprika, None) if paprika else get('https://api.coinpaprika.com/v1/tickers')
        if not error and isinstance(data, list):
            if enforce_fresh_caps:
                data = [dict(c, capRetrievedAt=c.get('capRetrievedAt') or scanner.now()) for c in data if isinstance(c, dict) and fresh_source_time(c.get('last_updated'), scanner.now())]
            for row in still_missing:
                try:
                    cap = scanner.resolve_cap(row, coins, [], data)
                    if valid(cap.get('capUsd'), True):
                        apply_cap(row, cap)
                        for field in cap:
                            row.setdefault('fieldAvailableAt', {})[field] = scanner.now()
                        recovered_cap(row)
                        pending[id(row)].pop('市值', None)
                except (KeyError, TypeError, ValueError):
                    pending[id(row)]['市值'] = '备用市值源响应结构无效，保留未知'

    # Independent Binance source: only pre-reviewed chain/address bindings.
    from asset_identity import MAPPINGS, ALPHA_URL, resolve_alpha_cap
    alpha_rows = [r for r in missing_caps if (not valid(r.get('capUsd'), True) or r.get('capTrusted') is not True)
                  and MAPPINGS.get(r['asset'], {}).get('alpha')]
    if alpha_rows and alpha_context is None:
        payload, error = get(ALPHA_URL)
        if not error:
            alpha_context = {'data': payload, 'retrieved_at': scanner.now()}
    if alpha_rows and alpha_context is not None:
        summary['alpha_context_retrieved_at'] = alpha_context.get('retrieved_at')
        if evidence_dir is not None:
            with (Path(evidence_dir) / 'alpha-context.json').open('x', encoding='utf-8') as output:
                json.dump(alpha_context, output, ensure_ascii=False)
    if enforce_fresh_caps and alpha_context is not None and not fresh_source_time(alpha_context.get('retrieved_at'), scanner.now()):
        summary['errors'].append({'host': 'www.binance.com', 'reason': 'Alpha获取时间无效或过期'})
        alpha_context = None
    for row in alpha_rows:
        cap = resolve_alpha_cap(row, alpha_context)
        if cap is not None:
            apply_cap(row, cap)
            for field in cap:
                row.setdefault('fieldAvailableAt', {})[field] = scanner.now()
            pending[id(row)].pop('市值', None)
            recovered_cap(row)

    for row in rows:
        wanted = set()
        if not valid(row.get('oiUsd')) and not usd_exempt(row):
            wanted.add('oi')
        if not valid(row.get('accountRatio')):
            wanted.add('ratio')
        errors = row.get('errors', {})
        # Short but successfully returned history is not a transient failure.
        if not valid(row.get('closed4h')) and ('K线' in errors or 'K线连续性' in errors):
            wanted.add('candles')
        if '日线归档' in errors:
            wanted.add('daily')
        u = by_contract.get((row['symbol'], row['market']))
        if wanted and u is not None:
            try:
                fresh = scanner.fetch_contract(*u, only=wanted, requester=get, count_progress=False, **({'target_close_boundary':target_close_boundary} if target_close_boundary is not None else {}))
                for group in sorted(wanted):
                    fields = groups[group]
                    useful = bool(fresh.get('_candles', {}).get('1d')) if group == 'daily' else valid(fresh.get(fields[0])) if group != 'candles' else valid(fresh.get('closed4h'))
                    label = {'oi': 'OI', 'ratio': '账户比', 'candles': 'K线', 'daily': '日线归档'}[group]
                    if useful:
                        for field in fields:
                            if field in fresh:
                                row[field] = fresh[field]
                                row.setdefault('fieldAvailableAt', {})[field] = scanner.now()
                        interval = {'candles':'4h', 'daily':'1d'}.get(group)
                        if interval:
                            row.setdefault('_candles', {})[interval] = fresh['_candles'][interval]
                        row.setdefault('errors', {}).pop(label, None)
                        if group == 'candles':
                            row['errors'].pop('K线连续性', None)
                        recovered[id(row)].append(label)
                        summary['recovered_fields'] += len(fields) or 1
                    else:
                        pending[id(row)][label] = fresh.get('errors', {}).get(label, '补查后仍无有效数据')
            except Exception as exc:
                pending[id(row)]['行情'] = str(exc)[:180]
        elif wanted:
            pending[id(row)]['行情'] = '缺少本轮合约目录上下文，未混用旧行情补查'

    # Retry funding configuration once per market, sharing the total budget.
    for market in ('USDⓈ-M', 'COIN-M'):
        targets = [r for r in rows if r['market'] == market and valid(r.get('fundingPct'))
                   and not valid(r.get('fundingHours'), True)]
        if not targets or not universes:
            continue
        host, prefix = ('https://dapi.binance.com', '/dapi/v1/') if market == 'COIN-M' else ('https://fapi.binance.com', '/fapi/v1/')
        configs, error = get(host + prefix + 'fundingInfo')
        if not error and isinstance(configs, list):
            intervals = {c.get('symbol'): c.get('fundingIntervalHours') for c in configs if isinstance(c, dict)}
            for row in targets:
                hours = intervals.get(row['symbol'], 8)
                if valid(hours, True):
                    row.update(fundingHours=hours, fundingIntervalStatus='configured' if row['symbol'] in intervals else 'provider_default',
                               fundingIntervalSource='自动补查fundingInfo成功；未列出调整按源契约8h',
                               fundingDailyPct=row['fundingPct'] * 24 / hours,
                               fundingAprPct=row['fundingPct'] * 24 / hours * 365)
                    for field in ('fundingHours','fundingIntervalStatus','fundingIntervalSource','fundingDailyPct','fundingAprPct'):
                        row.setdefault('fieldAvailableAt', {})[field] = scanner.now()
                    recovered[id(row)].append('费率周期')
                    summary['recovered_fields'] += 1

    reason_counts = Counter()
    for row in rows:
        issues = pending[id(row)]
        excluded = exclusions(row)
        if '市值' in excluded:
            issues.pop('市值', None)
        elif not valid(row.get('capUsd'), True):
            if row.get('underlyingType') == 'INDEX':
                issues.pop('市值', None)
                issues['市值口径'] = '指数：单币流通市值不适用'
                row.update(capStatus=issues['市值口径'], capApplicable=False, capTrusted=False)
            else:
                issues.setdefault('市值', '本轮定向查询未覆盖或未返回，保留未知')
                row['capStatus'] = issues['市值'] if row.get('underlyingType') == 'COIN' else '非普通加密资产，需专门市值口径'
        elif row.get('capTrusted') is not True:
            issues['资产身份'] = '已有数值但项目身份未确认；同代码或价格接近不等于身份核验通过'
        if row.get('smartMoneyStatus') != 'covered' or not valid(row.get('smartMoneyNetInflowUsd')):
            issues['聪明钱流入'] = row.get('smartMoneyStatusNote') or '本轮聪明钱榜单无可核验的链与地址匹配；未覆盖不视为零'
        if not valid(row.get('closed4h')):
            remaining = row.get('emaWarmupRemaining')
            note = ('新合约上市预热，' if row.get('candleHistoryStatus') == 'listing_warmup' else '')
            note += f'已有{row.get("emaCandleCount", 0)}根有效已收盘4hK线'
            if remaining is not None: note += f'，EMA仍需{remaining}根'
            issues.setdefault('4h指标', note+'；缺口不降低120根预热门槛')
        for field in ('oiUsd', 'accountRatio', 'fundingAprPct'):
            if field == 'oiUsd' and usd_exempt(row):
                continue
            if not valid(row.get(field)):
                issues.setdefault(field, '补查后仍未知或本轮预算不足')
        refresh_derived(row)
        if valid(row.get('capUsd'), True) and row.get('capTrusted') is not True:
            issues.pop('资产身份', None)
            label = '市值价格一致性' if row.get('capIdentityVerified') else '资产身份'
            issues[label] = row.get('capStatus') or '项目身份未确认'
        row['dataVerification'] = {'checked_at': scanner.now(), 'rounds': 1,
                                   'recovered': recovered[id(row)], 'unresolved': issues, 'excluded': excluded}
        row.setdefault('fieldAvailableAt', {})['dataVerification'] = row['dataVerification']['checked_at']
        # Recompute dependent fields only when the underlying observation changed.
        if recovered[id(row)]:
            for field in ('oiCapPct','oiAgeMinutes','capAgeMinutes','capPriceGapPct','signals','knownCount',
                          'hitCount','strategyCandidates','riskFlags','state'):
                row.setdefault('fieldAvailableAt', {})[field] = scanner.now()
            summary['changed_contracts'].append({'market': row['market'], 'symbol': row['symbol'], 'recovered': recovered[id(row)]})
        reason_counts.update(issues.keys())
    summary.update(finished_at=scanner.now(), unresolved_counts=dict(reason_counts),
                   excluded_counts=dict(Counter(label for r in rows for label in exclusions(r))),
                   status='recovered_some' if summary['changed_contracts'] else 'checked_no_recovery')
    return summary


def verify_latest(root, *, read=None, mapping_revision=None, cap_context=None, alpha_context=None):
    """Recover this latest snapshot once, under the same lease as full scans.

    Only market-cap acquisition is refreshed without a live contract directory;
    historical technical prices and raw candle archives remain untouched.
    """
    import scanner
    from snapshot_store import ScanArchive, ScanLease
    root = Path(root)
    with ScanLease(root):
        previous = json.loads((root / 'scan.json').read_bytes())
        if mapping_revision is not None:
            if mapping_revision != scanner.MAPPING_REVISION:
                raise ValueError('映射版本与当前源码不一致，停止补查')
            if previous.get('verification', {}).get('mapping_revision') == mapping_revision:
                return previous
        elif previous.get('verification', {}).get('rounds') == 1:
            return previous
        archive = ScanArchive.open(root, previous['scan_id'])
        if previous['revision'] != archive.revision:
            raise ValueError('最新版本与归档版本不一致，停止补核')
        evidence_name = 'verification-mapping-' + mapping_revision[:16] if mapping_revision else 'verification'
        if (archive.path / evidence_name).exists():
            raise ValueError('此扫描已有未发布的补查证据，停止自动重发，需检查中断记录')
        rows = json.loads(json.dumps(previous['rows']))
        targets = [r for r in rows if not valid(r.get('capUsd'), True)] if mapping_revision else rows
        evidence = archive.path / evidence_name
        evidence.mkdir()  # Also guards interrupted passes that sent no requests.
        summary = verify_rows(targets, cap_context, read=read, evidence_dir=evidence, alpha_context=alpha_context, enforce_fresh_caps=read is None)
        if mapping_revision:
            summary.update(mapping_revision=mapping_revision, scope='missing_market_caps',
                           previous_verification=previous.get('verification'))
        patches = []
        excluded = {'market', 'symbol', 'scan_id', 'revision', 'fieldAvailableAt', 'asset', 'observed_at', 'candleArchives'}
        for old, row in zip(previous['rows'], rows):
            patch = {key:value for key,value in row.items() if key not in excluded and value != old.get(key)}
            if patch:
                patch.update(market=row['market'], symbol=row['symbol'])
                patches.append(patch)
        return archive._revise(patches, verification=summary)
