"""Single declarative source of candidate rules. Never execute model code."""
import hashlib
import json
import math
from pathlib import Path

PATH = Path(__file__).with_name('strategy-registry.json')

def registry_fingerprint(raw: bytes) -> str:
    """Canonical registry identity: LF-normalized UTF-8 bytes SHA256.

    Windows CRLF vs LF must not create two identities for the same logical file.
    Old frozen snapshot hashes are historical and must not be recomputed.
    """
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    crlf = bytes([13, 10])
    lf = bytes([10])
    cr = bytes([13])
    normalized = raw.replace(crlf, lf).replace(cr, lf)
    return hashlib.sha256(normalized).hexdigest()

_RAW = PATH.read_bytes()
_CRLF = bytes([13, 10])
_LF = bytes([10])
_CR = bytes([13])
REGISTRY = json.loads(_RAW.replace(_CRLF, _LF).replace(_CR, _LF).decode("utf-8"))
VERSION_HASH = registry_fingerprint(_RAW)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def all_states(values):
    return False if False in values else None if None in values else True


def evaluate(rule, row):
    op = rule['op']
    if op == 'unknown':
        return None
    if op == 'smart_inflow':
        evidence = row.get('smartMoneyEvidence') or {}
        value = row.get('smartMoneyNetInflowUsd')
        if (row.get('smartMoneyStatus') != 'covered' or not isinstance(evidence, dict)
                or evidence.get('schema') != rule['schema'] or evidence.get('period') != rule['period']
                or evidence.get('tagType') != 2 or not finite(value)
                or evidence.get('netInflowUsd') != value):
            return None
        return value > rule['value']
    if op == 'all':
        return all_states([evaluate(r, row) for r in rule['args']])
    if op == 'signals':
        signals = indicator_states(row)
        return all_states([signals[i] for i in rule['indices']])
    if op in ('below_ema', 'trend'):
        close, values = row.get('closed4h'), row.get('ema4h')
        if not finite(close) or not isinstance(values, list) or len(values) != 3 or not all(finite(v) for v in values):
            return None
        return all(close < v for v in values) if op == 'below_ema' else values[0] > values[1] > values[2] and all(close > v for v in values)
    if op == 'is_true':
        value = row.get(rule['field'])
        if value is None:
            return None
        return value is True
    if op == 'ignition_grade':
        grade = row.get(rule.get('field', 'ignitionGrade'))
        if grade not in ('strong', 'medium', 'weak', 'none'):
            return None
        wanted = rule['value']
        if isinstance(wanted, list):
            return grade in wanted
        return grade == wanted
    value = row.get(rule['field'])
    if not finite(value):
        return None
    if rule['field'] == 'fundingAprPct' and row.get('fundingIntervalStatus') in ('estimated', 'unknown'):
        return None
    if op == 'gt': return value > rule['value']
    if op == 'lt': return value < rule['value']
    if op == 'abs_lt': return abs(value) < rule['value']
    raise ValueError('Unsupported rule operation: ' + op)


def indicator_states(row):
    return [evaluate(i['rule'], row) for i in REGISTRY['indicators']]


def strategy_results(row):
    return {s['id']: {'candidate': evaluate(s['candidate'], row), 'version': REGISTRY['version'],
                     'registryHash': VERSION_HASH, 'executable': False, 'missing': s['missing']} for s in REGISTRY['strategies']}
