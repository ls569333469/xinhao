"""Explicit, versioned revalidation of every cap against captured source data.

No network calls. This is a cap revision, never a fresh price/candle scan.
"""
import json
from pathlib import Path

from asset_identity import MAPPINGS, MAPPING_REVISION, cap_coverage, resolve_alpha_cap
from scan_verification import refresh_derived, valid, apply_cap
from snapshot_store import ScanArchive, ScanLease


def refresh_market_caps(root, *, cap_context, alpha_context, expected_scan_id,
                        expected_revision, mapping_revision):
    import scanner
    if mapping_revision != MAPPING_REVISION:
        raise ValueError('映射版本与当前源码不一致')
    coins, markets, paprika = cap_context
    if not isinstance(markets, list) or not markets:
        raise ValueError('缺少本轮市值响应，不能将来源整体故障当作纠错')
    with ScanLease(root):
        root = Path(root)
        previous = json.loads((root/'scan.json').read_bytes())
        if (previous.get('scan_id'), previous.get('revision')) != (expected_scan_id, expected_revision):
            raise ValueError('扫描版本漂移，停止全量市值核验')
        last = previous.get('verification', {})
        if last.get('scope') == 'all_market_cap_identities' and last.get('mapping_revision') == mapping_revision:
            return previous
        archive = ScanArchive.open(root, expected_scan_id)
        if archive.revision != expected_revision:
            raise ValueError('快照与归档版本不一致')
        patches = []
        corrected = []
        for old in previous['rows']:
            row = json.loads(json.dumps(old))
            entry = MAPPINGS.get(row['asset'], {})
            cap = scanner.resolve_cap(row, coins, markets, paprika)
            apply_cap(row, cap)
            if not valid(row.get('capUsd'), True) or row.get('capTrusted') is not True:
                candidate = resolve_alpha_cap(row, alpha_context)
                if candidate is not None:
                    cap = candidate
            if row.get('underlyingType') == 'COIN' and not entry.get('trusted'):
                # Candidate ticker matches never enter the verified cap revision.
                cap.update(capUsd=None, capSource=None, capId=None, capTrusted=False,
                           capIdentityVerified=False, capStatus='资产身份无明确证据，保留未知')
            # Clear all old provider metadata before switching identities/sources.
            for field in list(row):
                if field.startswith('cap'):
                    row[field] = None
            row.update(cap)
            quote_fields = ()
            if row.get('quote', 'USDT') not in ('USD','USDT','USDC'):
                quote_fields = ('oiUsd','volume24Usd','volumeWindowUsd','volumeBaselineUsd')
                for field in quote_fields:
                    row[field.replace('Usd', 'Quote')] = old.get(field)
                    row[field] = None
                row['quoteUnitStatus'] = '原始报价为'+row['quote']+'；无同期美元转换证据，不标作USD'
            refresh_derived(row)
            state = row.setdefault('dataVerification', {})
            unresolved = state.setdefault('unresolved', {})
            for label in ('市值', '市值口径', '资产身份', '市值价格一致性'):
                unresolved.pop(label, None)
            if quote_fields:
                unresolved['报价单位'] = row['quoteUnitStatus']
            if row.get('underlyingType') == 'INDEX':
                unresolved['市值口径'] = '指数：单币流通市值不适用'
            elif not valid(row.get('capUsd'), True):
                unresolved['市值'] = row.get('capStatus') or '无可验证的流通市值'
            elif row.get('capTrusted') is not True:
                label = '市值价格一致性' if row.get('capIdentityVerified') else '资产身份'
                unresolved[label] = row.get('capStatus')
            state['status'] = 'pending' if unresolved else 'complete'
            state['cap_identity_checked_at'] = scanner.now()
            fields = {key for key in row if key.startswith('cap')}
            fields.update(('oiCapPct','oiAgeMinutes','signals','knownCount','hitCount',
                           'strategyCandidates','riskFlags','state','dataVerification'))
            fields.update(quote_fields)
            if quote_fields:
                fields.update(field.replace('Usd','Quote') for field in quote_fields)
                fields.add('quoteUnitStatus')
            patch = {field:row.get(field) for field in fields if row.get(field) != old.get(field)}
            if patch:
                patch.update(market=row['market'], symbol=row['symbol'])
                patches.append(patch)
            if old.get('capId') and old.get('capId') != row.get('capId'):
                corrected.append({'symbol':row['symbol'],'market':row['market'],
                                  'before_source':old.get('capSource'),'before_id':old.get('capId'),
                                  'after_source':row.get('capSource'),'after_id':row.get('capId')})
        result = archive._revise(patches, verification={
            'schema':'cap-identity-verification-v1','scope':'all_market_cap_identities',
            'mapping_revision':mapping_revision,'checked_contracts':len(previous['rows']),
            'source':'captured_public_responses','finished_at':scanner.now(),
            'changed_contracts':len(patches),'provider_id_changes':corrected,
            'previous_verification':last})
        assert result['capCoverage'] == cap_coverage(result['rows'])
        return result
