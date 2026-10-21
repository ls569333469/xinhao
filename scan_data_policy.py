"""Explicit user exclusions and live market-cap response validation."""
import datetime as dt
import math
from collections import Counter

VERSION = 'scan-data-policy-20261015-v1'
CAP_EXCLUSIONS = {('USDⓈ-M', '1000RATSUSDT'): '用户排除流通市值统计'}
USD_EXCLUSIONS = {('USDⓈ-M', symbol): '用户排除美元金额换算；原报价值保留'
                  for symbol in ('ETHBTC', 'ETHU', 'BTCU', 'ETHUSD1', 'BTCUSD1')}


def cap_required(row):
    return row.get('underlyingType') == 'COIN' and (row.get('market'), row.get('symbol')) not in CAP_EXCLUSIONS


def usd_exempt(row):
    return (row.get('market'), row.get('symbol')) in USD_EXCLUSIONS


def exclusions(row):
    key = (row.get('market'), row.get('symbol'))
    result = {}
    if key in CAP_EXCLUSIONS:
        result['市值'] = CAP_EXCLUSIONS[key]
    if key in USD_EXCLUSIONS:
        result['美元换算'] = USD_EXCLUSIONS[key]
    return result


def positive(value):
    try:
        return not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def fresh_source_time(value, reference):
    try:
        observed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        checked = dt.datetime.fromisoformat(reference.replace('Z', '+00:00'))
        return observed.tzinfo is not None and checked.tzinfo is not None and -120 <= (checked - observed).total_seconds() <= 3600
    except (TypeError, ValueError, AttributeError, OverflowError):
        return False


def validated_markets(data, requested, reference):
    """Reject malformed, duplicate, wrong-ID, stale and inconsistent live data.

    Source times remain source times. Never relabel old observations as new.
    """
    if not isinstance(data, list):
        return [], ['市值响应不是列表']
    allowed = set(requested)
    counts = Counter(c.get('id') for c in data if isinstance(c, dict) and isinstance(c.get('id'), str))
    result, errors = [], []
    for c in data:
        if not isinstance(c, dict) or not isinstance(c.get('id'), str):
            errors.append('市值条目缺少ID或结构无效')
            continue
        identity = c['id']
        if identity not in allowed:
            errors.append(identity + ': 非请求ID')
            continue
        if counts[identity] != 1:
            errors.append(identity + ': 重复ID')
            continue
        if not all(positive(c.get(k)) for k in ('market_cap', 'current_price', 'circulating_supply')):
            errors.append(identity + ': 市值/价格/流通量无效')
            continue
        computed = float(c['current_price']) * float(c['circulating_supply'])
        if not math.isfinite(computed) or computed <= 0 or abs(float(c['market_cap']) / computed - 1) > .05:
            errors.append(identity + ': 市值与价格乘流通量不一致')
            continue
        if not fresh_source_time(c.get('last_updated'), reference):
            errors.append(identity + ': 源时间缺失、过期超过60分钟或未来时间异常')
            continue
        result.append(dict(c, capRetrievedAt=reference))
    returned = {c['id'] for c in result}
    for identity in sorted(allowed - returned - set(counts)):
        errors.append(identity + ': 响应未返回')
    return result, errors
