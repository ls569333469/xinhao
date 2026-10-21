"""One-attempt live transports for explicitly granted recovery only.

No imports start HTTP. Fixed provider endpoints, TLS verification, no redirects,
no hidden retries. Native COIN OI and contemporaneous conversion remain in each
immutable derived sample. Registry metadata must cover the requested interval.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timezone,datetime
from email.utils import parsedate_to_datetime

from .recovery import SourceFailure
from .source_fetch import HistoricalAdapter, number, H, H4

VERSION = 'recovery-live-v3'
CONVERSION = 'coin-base-times-index-close-5m-v1'
BINANCE = {'fapi.binance.com', 'dapi.binance.com'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def retry_after_ms(value):
    try:
        return max(0, int(float(value) * 1000))
    except (ValueError, TypeError, OverflowError):
        try:
            at = parsedate_to_datetime(str(value))
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            return max(0, int((at.timestamp() - time.time()) * 1000))
        except (ValueError, TypeError, OverflowError):
            return None


class LiveTransport:
    def __init__(self, *, before_send=None):
        self.opener = urllib.request.build_opener(NoRedirect())
        self.before_send = before_send

    def __call__(self, url, params, *, timeout_seconds):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise SourceFailure('adapter_contract_missing')
        host = parsed.hostname
        binance = host in BINANCE and bool(re.fullmatch(
            r'/(?:fapi|dapi)/v1/(?:klines|indexPriceKlines)|/futures/data/(?:openInterestHist|globalLongShortAccountRatio)', parsed.path))
        cap = host in {'api.coingecko.com', 'pro-api.coingecko.com'} and bool(re.fullmatch(
            r'/api/v3/coins/[a-zA-Z0-9_-]+/market_chart/range', parsed.path))
        paprika=host=='api.coinpaprika.com' and bool(re.fullmatch(r'/v1/tickers/[a-zA-Z0-9_-]+/historical',parsed.path))
        if not (binance or cap or paprika) or parsed.port not in (None, 443):
            raise SourceFailure('adapter_contract_missing')
        started = time.monotonic()
        # Reuse the original collector's process-wide limiter, never its retrying requester.
        from scanner import BINANCE_PACER, COINGECKO_PACER
        pacer = BINANCE_PACER if binance else COINGECKO_PACER
        pacer.wait(max_wait=max(0, timeout_seconds - .05))
        if self.before_send:
            self.before_send()
        remaining = timeout_seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError('recovery_timeout_before_send')
        headers = {'User-Agent': 'ABCRecovery/3', 'Accept': 'application/json', 'Accept-Encoding': 'identity'}
        env, key_header = ('COINGECKO_PRO_API_KEY', 'x-cg-pro-api-key') if host == 'pro-api.coingecko.com' else ('COINGECKO_DEMO_API_KEY', 'x-cg-demo-api-key')
        if cap and os.environ.get(env):
            headers[key_header] = os.environ[env]
        request = urllib.request.Request(url + '?' + urllib.parse.urlencode(params), headers=headers)
        try:
            response = self.opener.open(request, timeout=remaining)
        except urllib.error.HTTPError as exc:
            status, retry = exc.code, retry_after_ms(exc.headers.get('Retry-After'))
            exc.close()
            if status in (429, 418):
                pacer.rate_limited(str(max(60, (retry or 0) / 1000)))
            return dict(status=status, data=None, retry_after_ms=retry, http_attempts=1, retries=0)
        with response:
            status = response.status
            # Bounded memory AND total body deadline; no successful partial JSON.
            chunks, size = [], 0
            while True:
                if time.monotonic() - started >= timeout_seconds:
                    raise TimeoutError('recovery_body_deadline')
                block = response.read(65536)
                if not block:
                    break
                size += len(block)
                if size > 8 * 1024 * 1024:
                    raise SourceFailure('source_response_too_large')
                chunks.append(block)
            try:
                data = json.loads(b''.join(chunks))
            except (ValueError, UnicodeError):
                # Preserve HTTP status even when its body is malformed.
                return dict(status=status, data=None, malformed_json=True, http_attempts=1, retries=0)
            return dict(status=status, data=data, http_attempts=1, retries=0)


def cap_endpoint(cfg):
    coin_id=cfg.get('id')
    if not isinstance(coin_id,str) or not re.fullmatch('[a-zA-Z0-9_-]+',coin_id):raise SourceFailure('cap_history_provider_not_configured')
    provider=cfg.get('provider','coingecko')
    if provider=='coingecko':
        host='pro-api.coingecko.com' if cfg.get('plan')=='pro' else 'api.coingecko.com'
        return provider,'https://'+host+'/api/v3/coins/'+coin_id+'/market_chart/range'
    if provider=='coinpaprika':return provider,'https://api.coinpaprika.com/v1/tickers/'+coin_id+'/historical'
    raise SourceFailure('cap_history_provider_not_configured')


def contract_metadata(registry, contract, start, end):
    meta = registry.get(contract)
    if not isinstance(meta, dict) or meta.get('verified') is not True or not meta.get('identity_evidence'):
        raise SourceFailure('contract_identity_unverified')
    if type(meta.get('valid_from_ms')) is not int or type(meta.get('valid_to_ms')) is not int or not meta['valid_from_ms'] <= start <= end <= meta['valid_to_ms']:
        raise SourceFailure('contract_metadata_outside_history')
    market, symbol = contract.split('|', 1)
    if not re.fullmatch(r'[A-Z0-9_]{2,50}', symbol) or market not in ('USDT-M', 'COIN-M'):
        raise SourceFailure('contract_identity_unverified')
    if market == 'USDT-M' and meta.get('quote_asset') != 'USDT':
        raise SourceFailure('unsupported_quote_conversion')
    if market == 'COIN-M' and (not re.fullmatch(r'[A-Z0-9]{2,40}', str(meta.get('pair', ''))) or meta.get('oi_conversion') != CONVERSION or number(meta.get('contract_size')) <= 0):
        raise SourceFailure('coin_multiplier_unknown')
    return market, symbol, meta


class LiveHistoricalAdapter:
    def __init__(self, registry, *, clock, before_send=None):
        self.registry, self.clock = registry, clock
        self._guard = before_send
        self.request = LiveTransport(before_send=before_send)

    def __call__(self, job, *, start_ms, end_ms, limit, attempt):
        if job['source']=='cap':
            meta=self.registry.get(job['contract']) or {};market,symbol=job['contract'].split('|',1)
            if meta.get('verified') is not True or not meta.get('identity_evidence'):raise SourceFailure('contract_identity_unverified')
            if type(meta.get('valid_from_ms')) is not int or type(meta.get('valid_to_ms')) is not int or not meta['valid_from_ms']<=start_ms<=end_ms<=meta['valid_to_ms']:raise SourceFailure('contract_metadata_outside_history')
        else:market,symbol,meta=contract_metadata(self.registry,job['contract'],start_ms,end_ms)
        if self._guard is not None:
            self.request.before_send = lambda: self._guard(job)
        if job['adapter_version'] != VERSION:
            raise SourceFailure('adapter_contract_missing')
        if job['source'] == 'cap':
            return self._cap(job, meta, start_ms, attempt)
        # Exact frozen adapter contract, including explicit provider retention policy.
        policy = meta.get('history_policy')
        if not isinstance(policy, dict) or type(policy.get('window_ms')) is not int or not policy.get('version'):
            raise SourceFailure('adapter_contract_missing')
        inverse = market == 'COIN-M' and job['source'] == 'oi'
        native_job = dict(job)
        if inverse:
            native_job['unit_identity'] = 'base_asset'
        adapter = HistoricalAdapter(request=self.request, clock=self.clock,
            contracts={(market, symbol): meta}, history_window_ms=policy['window_ms'], history_policy_version=policy['version'])
        rows = adapter(native_job, start_ms=start_ms, end_ms=end_ms, limit=limit, attempt=attempt)
        if not inverse:
            return rows
        # Each conversion request consumes the SAME grant/round HTTP budget.
        converted = []
        for sample in rows:
            t = sample['effective_at_ms']
            response = attempt(lambda **kw: self.request('https://dapi.binance.com/dapi/v1/indexPriceKlines',
                dict(pair=meta['pair'], interval='5m', startTime=t-300000, endTime=t-1, limit=1), **kw))
            if response['status'] != 200:
                raise SourceFailure(str(response['status']))
            data = response.get('data')
            if not isinstance(data, list) or len(data) != 1 or len(data[0]) < 7 or int(data[0][0]) != t-300000 or int(data[0][6]) != t-1:
                raise SourceFailure('coin_conversion_price_unavailable')
            price = number(data[0][4])
            if price <= 0:
                raise SourceFailure('coin_conversion_price_unavailable')
            converted.append(dict(sample, value=sample['value'] * price, unit='USD',
                native_value=sample['value'], native_unit='base_asset', conversion_price=price,
                conversion_effective_at_ms=t-1, conversion_version=CONVERSION,
                conversion_pair=meta['pair'], conversion_raw=data[0], available_at_ms=self.clock()))
        return converted

    def _cap(self, job, meta, boundary, attempt):
        cfg = meta.get('cap') or {}
        coin_id = cfg.get('id')
        if cfg.get('verified') is not True or not cfg.get('identity_evidence') or not isinstance(coin_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', coin_id):
            raise SourceFailure('cap_history_provider_not_configured')
        provider,url=cap_endpoint(cfg)
        if job['provider'] != provider or job['endpoint'] != url or job['time_semantics'] != 'asof_snapshot' or job['start_ms'] != job['target_ms']:
            raise SourceFailure('adapter_contract_missing')
        stamp=lambda t:datetime.fromtimestamp(t/1000,timezone.utc).isoformat().replace('+00:00','Z')
        params=dict(vs_currency='usd',**{'from':(boundary-H)//1000,'to':boundary//1000}) if provider=='coingecko' else dict(start=stamp(boundary-H),end=stamp(boundary+1000),interval='1h')
        response=attempt(lambda **kw:self.request(url,params,**kw))
        if response['status'] != 200:
            raise SourceFailure(str(response['status']))
        data = response.get('data')
        if provider=='coinpaprika':
            from b_evidence import ms
            points=[(ms(x.get('timestamp')),x.get('market_cap')) for x in data if isinstance(x,dict)] if isinstance(data,list) else None
        else:points=data.get('market_caps') if isinstance(data,dict) else None
        if not isinstance(points, list):
            raise SourceFailure('invalid_cap_source')
        points = [(t, number(v)) for t, v in points if type(t) is int and 0 <= boundary-t <= H]
        if not points:
            raise SourceFailure('empty_response')
        observed, value = max(points)
        if value <= 0:
            raise SourceFailure('invalid_cap_source')
        # Acquisition identity uses the requested as-of, but the economic sample
        # retains its ACTUAL provider timestamp and the original one-hour gate.
        return [dict(effective_at_ms=observed, requested_boundary_ms=boundary, available_at_ms=self.clock(),
            value=value, unit='USD', trusted=True, provider=provider, asset_id=coin_id,
            identity_evidence=cfg['identity_evidence'], source_endpoint=url)]

    def page_limit(self, job, remaining_attempts):
        return max(1, min(500, remaining_attempts-1)) if job['contract'].startswith('COIN-M|') and job['source']=='oi' else 500

