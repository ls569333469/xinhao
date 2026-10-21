"""Reviewed provider identities, with evidence separate from trust decisions."""
import hashlib
import json
import math
import datetime as dt
import re
from pathlib import Path


def load_registry(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('schema') != 'asset-identity-v1' or not isinstance(data.get('entries'), dict):
        raise ValueError('资产映射目录结构无效')
    for asset, entry in data['entries'].items():
        if (not isinstance(asset, str) or asset != asset.upper() or not asset
                or not isinstance(entry, dict)
                or (not (isinstance(entry.get('coingecko_id'), str) and entry['coingecko_id'])
                    and not (isinstance(entry.get('alpha'), dict) and entry['alpha'].get('chain_id') and entry['alpha'].get('address')))
                or type(entry.get('trusted')) is not bool
                or not isinstance(entry.get('evidence'), list) or not entry['evidence']
                or not all(isinstance(url, str) and url.startswith('https://') for url in entry['evidence'])):
            raise ValueError('资产映射条目缺少有效ID、信任状态或来源证据')
        if 'alpha' in entry:
            binding = entry['alpha']
            if (not isinstance(binding, dict) or not isinstance(binding.get('chain_id'), str)
                    or not binding['chain_id'] or not isinstance(binding.get('address'), str) or not binding['address']):
                raise ValueError('币安Alpha映射必须明确链和地址')
    return data['entries']


MAPPINGS = load_registry(Path(__file__).with_name('asset-mappings.json'))
MAPPING_REVISION = hashlib.sha256(json.dumps(MAPPINGS, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def price_multiplier(symbol):
    # Binance's 1M ticker is an explicit contract denomination, not a token.
    if symbol.startswith('1MBABYDOGE'):
        return 1_000_000
    match = re.match(r'^(1000000|100000|10000|1000)', symbol)
    return int(match.group()) if match else 1


def cap_coverage(rows, expected=None):
    applicable = [r for r in rows if r.get('underlyingType') == 'COIN']
    has_value = lambda r: type(r.get('capUsd')) in (int, float) and math.isfinite(r['capUsd']) and r['capUsd'] > 0
    from scan_data_policy import cap_required, CAP_EXCLUSIONS, USD_EXCLUSIONS, VERSION
    required = [r for r in applicable if cap_required(r)]
    received_keys = {r['market'] + '|' + r['symbol'] for r in required}
    expected_keys = set(expected) if expected is not None else received_keys
    absent = sorted(expected_keys - received_keys)
    return {'policy_version': VERSION,
            'required_contracts': len(expected_keys | received_keys),
            'required_with_value': sum(has_value(r) for r in required),
            'required_trusted_value': sum(has_value(r) and r.get('capTrusted') is True for r in required),
            'required_missing': [r['market'] + '|' + r['symbol'] for r in required if not has_value(r)] + absent,
            'required_untrusted': [r['market'] + '|' + r['symbol'] for r in required if has_value(r) and r.get('capTrusted') is not True],
            'all_required_trusted': bool(required) and not absent and all(has_value(r) and r.get('capTrusted') is True for r in required),
            'excluded_contracts': [dict(market=r['market'], symbol=r['symbol'], reason=CAP_EXCLUSIONS[(r['market'],r['symbol'])]) for r in applicable if not cap_required(r)],
            'usd_conversion_exclusions': [dict(market=r['market'], symbol=r['symbol'], reason=USD_EXCLUSIONS[(r['market'],r['symbol'])]) for r in rows if (r.get('market'),r.get('symbol')) in USD_EXCLUSIONS],
            'applicable_contracts': len(applicable),
            'not_applicable_contracts': sum(r.get('underlyingType') == 'INDEX' for r in rows),
            'with_value': sum(has_value(r) for r in applicable),
            'identity_verified': sum(r.get('capIdentityVerified') is True for r in applicable),
            'trusted_value': sum(has_value(r) and r.get('capTrusted') is True for r in applicable),
            'missing': [r['market'] + '|' + r['symbol'] for r in applicable if not has_value(r)],
            'untrusted': [r['market'] + '|' + r['symbol'] for r in applicable if has_value(r) and r.get('capTrusted') is not True],
            'all_applicable_trusted': bool(applicable) and all(has_value(r) and r.get('capTrusted') is True for r in applicable)}

ALPHA_URL = 'https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list'


def resolve_alpha_cap(row, context):
    """Match reviewed chain/address, never a ticker-only fallback."""
    if row.get('underlyingType') != 'COIN':
        return None
    entry = MAPPINGS.get(row.get('asset'), {})
    binding = entry.get('alpha')
    if not binding or not isinstance(context, dict):
        return None
    payload = context.get('data')
    if (not isinstance(payload, dict) or payload.get('code') != '000000'
            or payload.get('success') is not True or not isinstance(payload.get('data'), list)):
        return None
    retrieved = context.get('retrieved_at')
    try:
        if dt.datetime.fromisoformat(retrieved.replace('Z', '+00:00')).tzinfo is None:
            return None
    except (ValueError, TypeError, AttributeError):
        return None

    def address_equal(address):
        if not isinstance(address, str):
            return False
        return address.lower() == binding['address'].lower() if binding['chain_id'] in ('1', '56', '8453') else address == binding['address']

    matches = [t for t in payload['data'] if isinstance(t, dict)
               and t.get('chainId') == binding['chain_id'] and address_equal(t.get('contractAddress'))]
    if len(matches) != 1:
        return None
    token = matches[0]
    try:
        cap, price, supply = (float(token[key]) for key in ('marketCap', 'price', 'circulatingSupply'))
        if not all(math.isfinite(v) and v > 0 for v in (cap, price, supply)):
            return None
        if abs(cap / (price * supply) - 1) > .05:
            return None
    except (TypeError, ValueError, KeyError, OverflowError, ZeroDivisionError):
        return None
    return {'capUsd': cap, 'capSource': 'Binance Alpha', 'capId': binding['chain_id'] + ':' + binding['address'],
            'capName': token.get('name') or entry.get('name'), 'capPrice': price,
            'capTrusted': entry['trusted'], 'capApplicable': True,
            'capStatus': '币安Alpha按已核对链/地址匹配' if entry['trusted'] else '币安Alpha地址匹配，期货资产绑定待核对',
            'capAsOf': retrieved, 'capRetrievedAt': retrieved, 'capObservedAt': None, 'capTimestampKind': 'retrieved_at',
            'capTimeNote': '来源未提供市值观察时间，此处记录获取时间',
            'capCirculatingSupply': supply, 'capMappingRevision': MAPPING_REVISION,
            'capIdentityBasis': entry.get('basis'), 'capSourceUrl': ALPHA_URL,
            'capIdentityVerified': entry['trusted'], 'capQualityStatus': '来源市值与流通量乘价格一致'}
