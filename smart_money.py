"""Binance-labelled smart-money net inflow; separate from exchange deposits.

Four public rankings per scan, no credentials or model calls. A ranking miss
is unknown, never zero. Chain/address identity is required; tickers are ignored.
"""
import concurrent.futures
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import urllib.request

URL = 'https://web3.binance.com/bapi/defi/v1/public/wallet-direct/tracker/wallet/token/inflow/rank/query/ai'
CHAINS = ('56', 'CT_501', '8453', '1')
PERIOD = '4h'
SCHEMA = 'binance-smart-money-v1'
TTL_SECONDS = 900


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except ValueError:
        return None


def address_key(chain, address):
    if not isinstance(address, str):
        return None
    if chain == 'CT_501':
        return (chain, address) if re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{32,44}', address) else None
    return (chain, address.lower()) if chain in CHAINS and re.fullmatch(r'0x[0-9a-fA-F]{40}', address) else None


def identity_index(root, mappings):
    raw = (Path(root) / 'smart-money-identities.json').read_bytes()
    doc = json.loads(raw)
    if doc.get('schema') != 'smart-money-identities-v1':
        raise ValueError('聪明钱资产地址表版本无效')
    index = {}
    for item in doc['entries']:
        entry = mappings.get(item.get('asset'), {})
        if not entry.get('trusted') or item.get('coingecko_id') != entry.get('coingecko_id'):
            continue
        key = address_key(item.get('chain'), item.get('address'))
        if key:
            index.setdefault(key, set()).add(item['asset'])
    # Reviewed Alpha bindings can be refreshed without trusting a ticker match.
    for asset, entry in mappings.items():
        alpha = entry.get('alpha')
        if entry.get('trusted') and alpha:
            key = address_key(alpha['chain_id'], alpha['address'])
            if key:
                index.setdefault(key, set()).add(asset)
    return index, hashlib.sha256(raw).hexdigest()


def fetch(chain):
    body = {'chainId': chain, 'period': PERIOD, 'tagType': 2}
    request = urllib.request.Request(URL, data=json.dumps(body).encode(), method='POST',
                                    headers={'User-Agent': 'binance-web3/3.0 (Skill)',
                                             'Accept-Encoding': 'identity', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.load(response)


def collect(root, *, read=None, evidence_dir=None):
    """One bounded pass; a source failure never reuses a previous scan's values."""
    from asset_identity import MAPPINGS
    index, identity_hash = identity_index(root, MAPPINGS)
    read = read or fetch

    def one(chain):
        record = {'chain': chain, 'period': PERIOD, 'tagType': 2, 'source': URL, 'schema': SCHEMA}
        try:
            data = read(chain)
            record['retrieved_at'] = now()
            if (not isinstance(data, dict) or data.get('code') != '000000'
                    or data.get('success') is not True or not isinstance(data.get('data'), list)):
                raise ValueError('聪明钱榜单业务状态或响应结构无效')
            record.update(status='ok', response=data)
        except Exception as exc:
            record.update(status='source_error', error=type(exc).__name__, retrieved_at=now())
        if evidence_dir is not None:
            directory = Path(evidence_dir)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / (chain + '-4h.json')).write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
        return record

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(one, CHAINS))
    return {'schema': SCHEMA, 'period': PERIOD, 'identity_hash': identity_hash,
            'index': index, 'records': records, 'finished_at': now()}


def apply(rows, context, *, at=None):
    at = at or dt.datetime.now(dt.timezone.utc)
    index = context['index']
    by_asset = {}
    errors, successful = [], []
    for record in context['records']:
        chain = record['chain']
        if record['status'] != 'ok':
            errors.append(chain)
            continue
        retrieved = dt.datetime.fromisoformat(record['retrieved_at'])
        if not 0 <= (at - retrieved).total_seconds() <= TTL_SECONDS:
            errors.append(chain)
            continue
        successful.append(chain)
        seen = {}
        for token in record['response']['data']:
            if isinstance(token, dict):
                key = address_key(chain, token.get('ca'))
                if key:
                    seen.setdefault(key, []).append(token)
        for key, tokens in seen.items():
            assets = index.get(key, set())
            if len(assets) != 1:
                continue
            asset = next(iter(assets))
            # Duplicate records are a source inconsistency, not two flows.
            if len(tokens) != 1:
                by_asset.setdefault(asset, []).append(None)
                continue
            token = tokens[0]
            inflow, traders = number(token.get('inflow')), number(token.get('traders'))
            if inflow is None or traders is None or traders < 0 or int(traders) != traders:
                by_asset.setdefault(asset, []).append(None)
                continue
            by_asset.setdefault(asset, []).append({
                'schema': SCHEMA, 'chainId': chain, 'address': key[1], 'period': PERIOD,
                'tagType': 2, 'netInflowUsd': inflow, 'traders': int(traders),
                'retrieved_at': record['retrieved_at'], 'sourceObservedAt': None,
                'windowStart': None, 'windowEnd': None, 'source': URL,
                'identityHash': context['identity_hash'],
                'scope': 'Binance-labelled smart-money wallets on the returned chain',
            })
    for row in rows:
        # Keep exchange-flow history separate. Never relabel chainFlowUsd.
        row.update(smartMoneyNetInflowUsd=None, smartMoneyTraders=None, smartMoneyEvidence=None,
                   smartMoneyStatus='not_covered', smartMoneyStatusNote='本轮榜单无可核验的链与地址匹配；不视为零')
        matches = by_asset.get(row['asset'], []) if row.get('underlyingType') == 'COIN' else []
        if len(matches) == 1 and matches[0] is not None:
            evidence = matches[0]
            row.update(smartMoneyNetInflowUsd=evidence['netInflowUsd'], smartMoneyTraders=evidence['traders'],
                       smartMoneyEvidence=evidence, smartMoneyStatus='covered',
                       smartMoneyStatusNote='币安标记聪明钱4h净流；正值流入，负值流出；非全市场资金流')
        elif matches:
            row.update(smartMoneyStatus='ambiguous', smartMoneyStatusNote='多链、多地址或重复记录；不合并为资产总流入')
        elif errors:
            row.update(smartMoneyStatus='source_error', smartMoneyStatusNote='部分聪明钱来源不可用，本币无有效匹配；不沿用旧值')
        for field in ('smartMoneyNetInflowUsd', 'smartMoneyTraders', 'smartMoneyEvidence', 'smartMoneyStatus', 'smartMoneyStatusNote'):
            row.setdefault('fieldAvailableAt', {})[field] = now()
    covered = [r for r in rows if r['smartMoneyStatus'] == 'covered']
    return {'schema': SCHEMA, 'period': PERIOD, 'source': URL, 'finished_at': context['finished_at'],
            'request_count': len(context['records']), 'successful_chains': successful, 'failed_chains': errors,
            'covered_contracts': len(covered), 'covered_assets': len({r['asset'] for r in covered}),
            'total_contracts': len(rows), 'identity_hash': context['identity_hash'],
            'note': '资产级数据关联多份合约，不得重复累加；抓取时间不等于精确窗口截止时间'}
