"""EXT-01 deepen: authorized source slots + precise-⑤ claim gates.

Does NOT invent precise exchange net-flow (指标⑤). Local CEX reserves / tech
chain-flow must not be labeled as precise ⑤. Source slots default to
unauthorized/unverified — claim succeeds only after local authorize +
time/口径 checks. USDC/COIN-M pricing engines: catalog only, not ready.
Unlock/event wording: never call it a second-hand calendar; prefer
「第三方解锁网站上的时间」or「未在官网上核对」.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from snapshot_store import atomic, encoded

SCHEMA = 'ext-catalog-v1'
SOURCES_SCHEMA = 'ext-sources-v1'
DISCLAIMER = (
    '扩展能力：来源槽位默认未授权/未核验；'
    '当前⑤采用聪明钱流入；历史交易所净流独立保留；'
    '解锁时间若展示须标明来自第三方解锁网站上的时间或未在官网上核对；'
    'USDC/COIN-M 未实现计价前不开放模拟引擎。'
)

FORBIDDEN_PRECISE_SUBSTITUTES = (
    'cex-reserves',
    'chain-flows-technical',
    'approx_netflow',
    'local_reserve',
    'tech_flow',
)

# Markers that must not appear in precise⑤ slot url/口径/note/source_kind (impersonation).
PRECISE_SUBSTITUTE_MARKERS = (
    'cex-reserves', 'cex_reserves', 'cexreserves',
    'chain-flows-technical', 'chain_flows_technical',
    'approx_netflow', 'approx-netflow',
    'local_reserve', 'local-reserve',
    'tech_flow', 'tech-flow', 'techflow',
)

# Request flags that attempt to skip time/口径/hash / authorize checks → always 409.
BYPASS_FLAG_KEYS = (
    'skip_verification', 'skip_verify', 'bypass', 'bypass_verification', 'bypass_auth',
    'force', 'force_ready', 'force_authorize', 'force_claim', 'force_accept',
    'ignore_hash', 'ignore_as_of', 'ignore_caliber', 'ignore_time', 'ignore_核验',
    'skip_time_check', 'skip_hash_check', 'skip_caliber_check', 'skip_核验',
    'trust_me', 'no_verify', 'unverified_ok', 'authorize_without_verify',
)

# Allow small clock skew; as_of beyond this in the future is refused.
FUTURE_AS_OF_TOLERANCE_SEC = 300


class ExtConflict(Exception):
    pass


# Capability catalog rows (engines stay not_ready regardless of other slots).
CATALOG_ITEMS = [
    {
        'id': 'precise_indicator_5',
        'title': '历史交易所净流（兼容来源）',
        'legacy': True,
        'active_indicator': None,
        'source_slot': 'precise_indicator_5',
        'note': (
            '仅保留旧交易所净流来源的兼容接口，不参与当前⑤聪明钱流入。'
            '历史交易所净流仍须独立来源与口径核验，储备和聪明钱数据不得冒充。'
        ),
        'forbidden_substitutes': list(FORBIDDEN_PRECISE_SUBSTITUTES),
    },
    {
        'id': 'events',
        'title': '事件信息',
        'source_slot': 'events',
        'note': '事件来源槽位默认未授权；未在官网上核对前不得当作官方时间表。',
    },
    {
        'id': 'unlock',
        'title': '解锁时间信息',
        'source_slot': 'unlock',
        'note': (
            '解锁时间若来自第三方解锁网站上的时间，须明示；'
            '未在官网上核对则不得宣称官方解锁日历。'
        ),
    },
    {
        'id': 'supply',
        'title': '供应数据',
        'source_slot': 'supply',
        'note': '供应来源槽位默认未授权/未核验。',
    },
    {
        'id': 'historical_mcap',
        'title': '历史市值序列',
        'source_slot': 'historical_mcap',
        'note': '无核验历史市值源；禁止用当今市值回填过去（与 BACKTEST 一致）。',
    },
    {
        'id': 'multi_market_sim_engine',
        'title': '更多市场模拟引擎（含 USDC/COIN-M 计价）',
        'source_slot': None,  # catalog-only; no authorize path to ready
        'note': 'USDC/COIN-M 未实现计价前只允许扫描/投研展示，不开放模拟成交引擎。',
        'engine_only': True,
    },
]

SLOT_IDS = (
    'precise_indicator_5',
    'events',
    'unlock',
    'supply',
    'historical_mcap',
)

SLOT_TITLES = {
    'precise_indicator_5': '历史交易所净流（兼容来源）',
    'events': '事件信息',
    'unlock': '解锁时间信息',
    'supply': '供应数据',
    'historical_mcap': '历史市值序列',
}

# Status vocabulary: unauthorized (empty) | unverified (metadata only) | authorized | revoked
DEFAULT_SLOT_STATUS = 'unauthorized'


def _utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha(obj):
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _parse_iso(value):
    if value is None or value == '':
        return None
    s = str(value).strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f'时间无法解析: {value}') from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)



def _truthy_flag(value):
    if value is False or value is None or value == 0 or value == '':
        return False
    if isinstance(value, str) and value.strip().lower() in ('false', '0', 'no', 'off'):
        return False
    return True


def refuse_bypass_flags(body):
    """Refuse request flags that skip 核验 / authorize gates → ExtConflict (HTTP 409)."""
    if not isinstance(body, dict):
        return
    for key in BYPASS_FLAG_KEYS:
        if key in body and _truthy_flag(body.get(key)):
            raise ExtConflict(
                f'拒绝：不允许通过请求标志 {key} 跳过核验/授权（HTTP 409）'
            )


def ensure_as_of_not_future(as_of_value, *, context='as_of'):
    """Refuse future as_of (beyond skew). ExtConflict → HTTP 409."""
    if as_of_value is None or as_of_value == '':
        return
    dt = _parse_iso(as_of_value)
    if dt is None:
        return
    now = datetime.now(timezone.utc)
    if dt > now + timedelta(seconds=FUTURE_AS_OF_TOLERANCE_SEC):
        raise ExtConflict(
            f'拒绝：{context} 不可为未来时间（as_of={as_of_value}；禁止用未来口径跳过时效核验 / HTTP 409）'
        )


def refuse_precise_impersonation(slot_id, *blobs):
    """precise⑤ slot must not carry cex-reserves / tech-flow style substitute markers."""
    if slot_id != 'precise_indicator_5':
        return
    parts = []
    for blob in blobs:
        if isinstance(blob, dict):
            for k in (
                'url', '口径', 'caliber', 'note', 'source', 'source_kind',
                'backend', 'adapter', 'substitute', 'provider', 'operator_attestation',
            ):
                v = blob.get(k)
                if v is not None and str(v).strip():
                    parts.append(str(v).lower())
        elif blob is not None:
            parts.append(str(blob).lower())
    text_blob = ' '.join(parts)
    for marker in PRECISE_SUBSTITUTE_MARKERS:
        if marker in text_blob:
            raise ExtConflict(
                f'拒绝：精确⑤槽位不可用替代源标记 {marker}（cex reserves / 技术资金流不可冒充精确⑤ / HTTP 409）'
            )



def _default_slot(slot_id):
    return {
        'slot_id': slot_id,
        'title': SLOT_TITLES.get(slot_id, slot_id),
        'status': DEFAULT_SLOT_STATUS,
        'url': None,
        'content_hash': None,
        'as_of': None,
        '口径': None,
        'note': None,
        'authorized_at': None,
        'operator_attestation': None,
        'updated_at': None,
    }


class ExtStore:
    """Extension gates under runtime/ext/ — sources default unauthorized."""

    def __init__(self, root):
        self.root = Path(root)
        self.dir = self.root / 'runtime' / 'ext'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / 'state.json'
        self.sources_path = self.dir / 'sources.json'
        self._lock = threading.RLock()
        self._ensure()

    def _ensure(self):
        with self._lock:
            if not self.state_path.exists():
                atomic(self.state_path, encoded({
                    'schema': 'ext-state-v1',
                    'claims_rejected': 0,
                    'claims_accepted': 0,
                    'updated_at': _utcnow(),
                    'disclaimer': DISCLAIMER,
                }))
            if not self.sources_path.exists():
                slots = {sid: _default_slot(sid) for sid in SLOT_IDS}
                atomic(self.sources_path, encoded({
                    'schema': SOURCES_SCHEMA,
                    'slots': slots,
                    'updated_at': _utcnow(),
                    'disclaimer': DISCLAIMER,
                }))

    def _read_state(self):
        return json.loads(self.state_path.read_text(encoding='utf-8'))

    def _save_state(self, state):
        state['updated_at'] = _utcnow()
        state['disclaimer'] = DISCLAIMER
        atomic(self.state_path, encoded(state))

    def _read_sources(self):
        data = json.loads(self.sources_path.read_text(encoding='utf-8'))
        slots = data.setdefault('slots', {})
        for sid in SLOT_IDS:
            if sid not in slots:
                slots[sid] = _default_slot(sid)
            else:
                # normalize
                base = _default_slot(sid)
                base.update({k: slots[sid].get(k, base[k]) for k in base})
                slots[sid] = base
        data['slots'] = slots
        return data

    def _save_sources(self, data):
        data['schema'] = SOURCES_SCHEMA
        data['updated_at'] = _utcnow()
        data['disclaimer'] = DISCLAIMER
        atomic(self.sources_path, encoded(data))

    def _slot_ready(self, slot):
        return (
            isinstance(slot, dict)
            and slot.get('status') == 'authorized'
            and bool(slot.get('url'))
            and bool(slot.get('content_hash'))
            and bool(slot.get('as_of'))
            and bool(slot.get('口径'))
        )

    def _item_view(self, item, slots):
        out = dict(item)
        sid = item.get('source_slot')
        if item.get('engine_only') or sid is None:
            out['ready'] = False
            out['status'] = 'not_ready'
            out['source'] = None
            return out
        slot = slots.get(sid) or _default_slot(sid)
        ready = self._slot_ready(slot)
        out['ready'] = ready
        out['status'] = 'ready' if ready else (slot.get('status') or 'unauthorized')
        out['source'] = {
            'slot_id': sid,
            'status': slot.get('status'),
            'url': slot.get('url'),
            'content_hash': slot.get('content_hash'),
            'as_of': slot.get('as_of'),
            '口径': slot.get('口径'),
        }
        return out

    def catalog(self):
        with self._lock:
            sources = self._read_sources()
            slots = sources.get('slots') or {}
            items = [self._item_view(it, slots) for it in CATALOG_ITEMS]
            # engines never ready
            for it in items:
                if it.get('id') == 'multi_market_sim_engine':
                    it['ready'] = False
                    it['status'] = 'not_ready'
            any_ready = any(i.get('ready') for i in items if i.get('id') != 'multi_market_sim_engine')
            return {
                'schema': SCHEMA,
                'slice': 'EXT-01-precise-flow-deepen',
                'items': items,
                'any_ready': any_ready,
                'source_slots': self.list_sources(locked=False)['slots'],
                'disclaimer': DISCLAIMER,
                'policy': {
                    'precise_5': 'refuse_substitutes_until_authorized_slot',
                    'unlock_wording': '第三方解锁网站上的时间 / 未在官网上核对',
                    'no_today_mcap_backfill': True,
                    'usdc_coinm_engine': 'catalog_only_not_ready',
                    'claim_requires': ['status=authorized', 'url', 'content_hash', 'as_of', '口径', 'expected_as_of/口径/hash echo', 'no_bypass_flags', 'as_of_not_future'],
                },
            }

    def list_sources(self, locked=True):
        def _build():
            sources = self._read_sources()
            slots = []
            for sid in SLOT_IDS:
                slot = sources['slots'][sid]
                slots.append({
                    **slot,
                    'claim_ready': self._slot_ready(slot),
                })
            return {
                'schema': SOURCES_SCHEMA,
                'slots': slots,
                'default_status': DEFAULT_SLOT_STATUS,
                'disclaimer': DISCLAIMER,
            }
        if locked:
            with self._lock:
                return _build()
        return _build()

    def get_source(self, slot_id):
        slot_id = str(slot_id or '').strip()
        if slot_id not in SLOT_IDS:
            raise ValueError(f'未知来源槽位: {slot_id}；可选 {list(SLOT_IDS)}')
        with self._lock:
            sources = self._read_sources()
            slot = sources['slots'][slot_id]
            return {**slot, 'claim_ready': self._slot_ready(slot), 'disclaimer': DISCLAIMER}

    def upsert_source(self, slot_id, body):
        """Register or authorize a source slot. Does not fetch remote content.

        Without authorize=true → status stays unverified (or unauthorized if empty).
        With authorize=true → requires url, content_hash, as_of, 口径, operator_attestation.
        Never invents authorized sources; operator must attest local verification.
        Refuse: bypass flags, future as_of, precise⑤ substitute impersonation.
        """
        slot_id = str(slot_id or '').strip()
        if slot_id not in SLOT_IDS:
            raise ValueError(f'未知来源槽位: {slot_id}；可选 {list(SLOT_IDS)}')
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')
        refuse_bypass_flags(body)
        refuse_precise_impersonation(slot_id, body)
        authorize = bool(body.get('authorize'))
        url = body.get('url')
        content_hash = body.get('content_hash')
        as_of = body.get('as_of')
        caliber = body.get('口径') or body.get('caliber')
        note = body.get('note')
        attestation = body.get('operator_attestation')

        if url is not None:
            url = str(url).strip() or None
        if content_hash is not None:
            content_hash = str(content_hash).strip().lower() or None
            if content_hash and len(content_hash) < 8:
                raise ValueError('content_hash 过短')
        if as_of is not None and as_of != '':
            _parse_iso(as_of)  # validate
            as_of = str(as_of).strip()
            ensure_as_of_not_future(as_of, context=f'槽位 {slot_id} 登记')
        else:
            as_of = None if as_of == '' or as_of is None else as_of
        if caliber is not None:
            caliber = str(caliber).strip() or None
        if note is not None:
            note = str(note).strip() or None
            # refuse banned phrase
            if note and '二手日历' in note:
                raise ValueError('说明勿使用「二手日历」；请写「第三方解锁网站上的时间」或「未在官网上核对」')
        if attestation is not None:
            attestation = str(attestation).strip() or None

        with self._lock:
            sources = self._read_sources()
            slot = dict(sources['slots'][slot_id])
            if url is not None:
                slot['url'] = url
            if content_hash is not None:
                slot['content_hash'] = content_hash
            if as_of is not None:
                slot['as_of'] = as_of
            if caliber is not None:
                slot['口径'] = caliber
            if note is not None:
                slot['note'] = note
            if attestation is not None:
                slot['operator_attestation'] = attestation

            if authorize:
                missing = [k for k, v in (
                    ('url', slot.get('url')),
                    ('content_hash', slot.get('content_hash')),
                    ('as_of', slot.get('as_of')),
                    ('口径', slot.get('口径')),
                    ('operator_attestation', slot.get('operator_attestation')),
                ) if not v]
                if missing:
                    raise ValueError(
                        '授权须同时提供 url / content_hash / as_of / 口径 / operator_attestation；'
                        f'缺少: {", ".join(missing)}'
                    )
                ensure_as_of_not_future(slot.get('as_of'), context=f'授权槽位 {slot_id}')
                refuse_precise_impersonation(slot_id, body, slot)
                # unlock-specific wording hint in note if empty
                if slot_id == 'unlock' and not slot.get('note'):
                    slot['note'] = '第三方解锁网站上的时间；未在官网上核对前勿当官方时间表'
                slot['status'] = 'authorized'
                slot['authorized_at'] = _utcnow()
            else:
                # metadata-only path
                if slot.get('status') == 'authorized':
                    # updating fields without re-authorize → drop to unverified
                    slot['status'] = 'unverified'
                    slot['authorized_at'] = None
                elif slot.get('url') or slot.get('content_hash') or slot.get('as_of') or slot.get('口径'):
                    if slot.get('status') in (None, 'unauthorized', 'revoked'):
                        slot['status'] = 'unverified'
                else:
                    slot['status'] = 'unauthorized'
                    slot['authorized_at'] = None

            slot['updated_at'] = _utcnow()
            sources['slots'][slot_id] = slot
            self._save_sources(sources)
            return {**slot, 'claim_ready': self._slot_ready(slot), 'disclaimer': DISCLAIMER}

    def revoke_source(self, slot_id, body=None):
        slot_id = str(slot_id or '').strip()
        if slot_id not in SLOT_IDS:
            raise ValueError(f'未知来源槽位: {slot_id}')
        with self._lock:
            sources = self._read_sources()
            slot = dict(sources['slots'][slot_id])
            slot['status'] = 'revoked'
            slot['authorized_at'] = None
            slot['updated_at'] = _utcnow()
            if isinstance(body, dict) and body.get('note'):
                note = str(body['note']).strip()
                if '二手日历' in note:
                    raise ValueError('说明勿使用「二手日历」')
                slot['note'] = note
            sources['slots'][slot_id] = slot
            self._save_sources(sources)
            return {**slot, 'claim_ready': False, 'disclaimer': DISCLAIMER}

    def precise_flow_status(self):
        with self._lock:
            sources = self._read_sources()
            slot = sources['slots']['precise_indicator_5']
            ready = self._slot_ready(slot)
            return {
                'id': 'precise_indicator_5',
                'ready': ready,
                'status': 'ready' if ready else (slot.get('status') or 'unauthorized'),
                'note': CATALOG_ITEMS[0]['note'],
                'forbidden_substitutes': list(FORBIDDEN_PRECISE_SUBSTITUTES),
                'source': {
                    'slot_id': 'precise_indicator_5',
                    'status': slot.get('status'),
                    'url': slot.get('url'),
                    'content_hash': slot.get('content_hash'),
                    'as_of': slot.get('as_of'),
                    '口径': slot.get('口径'),
                },
                'disclaimer': DISCLAIMER,
            }

    def _reject(self, reason, **extra):
        with self._lock:
            st = self._read_state()
            st['claims_rejected'] = int(st.get('claims_rejected') or 0) + 1
            st['last_reject'] = {'at': _utcnow(), 'reason': reason, **extra}
            self._save_state(st)
        raise ExtConflict(reason)

    def _accept(self, note, **extra):
        with self._lock:
            st = self._read_state()
            st['claims_accepted'] = int(st.get('claims_accepted') or 0) + 1
            st['last_accept'] = {'at': _utcnow(), 'note': note, **extra}
            self._save_state(st)
        return {
            'accepted': True,
            'ready': True,
            'note': note,
            'disclaimer': DISCLAIMER,
            **extra,
        }

    def _check_slot_gates(self, slot, body, capability, *, require_echo=True):
        """Raise ExtConflict on failure. Claim path requires echoed as_of/口径/hash (不可省略核验)."""
        body = body if isinstance(body, dict) else {}
        refuse_bypass_flags(body)
        if not self._slot_ready(slot):
            status = slot.get('status') or 'unauthorized'
            self._reject(
                f'拒绝：来源槽位 {slot.get("slot_id")} 状态为 {status}（须 authorized 且具备 url/hash/as_of/口径）；'
                f'能力 {capability} 尚未可声明就绪',
                slot_id=slot.get('slot_id'),
                capability=capability,
                slot_status=status,
            )
        # Slot itself must not carry a future as_of (stale authorize race / clock skew abuse)
        try:
            ensure_as_of_not_future(slot.get('as_of'), context=f'槽位 {slot.get("slot_id")}')
        except ExtConflict as exc:
            self._reject(str(exc), slot_id=slot.get('slot_id'), capability=capability)

        expected_as_of = body.get('expected_as_of') or body.get('as_of')
        expected_caliber = body.get('expected_口径') or body.get('expected_caliber') or body.get('口径')
        expected_hash = body.get('expected_content_hash') or body.get('content_hash')

        if require_echo:
            missing_echo = []
            if not expected_as_of:
                missing_echo.append('expected_as_of')
            if not expected_caliber:
                missing_echo.append('expected_口径')
            if not expected_hash:
                missing_echo.append('expected_content_hash')
            if missing_echo:
                self._reject(
                    '拒绝：claim 须提供 expected_as_of / expected_口径 / expected_content_hash 与授权槽位核对；'
                    f'不可省略核验（缺少: {", ".join(missing_echo)} / HTTP 409）',
                    slot_id=slot.get('slot_id'),
                    capability=capability,
                    missing_echo=missing_echo,
                )

        # time check
        if expected_as_of:
            try:
                ensure_as_of_not_future(expected_as_of, context='claim expected_as_of')
                exp = _parse_iso(expected_as_of)
                got = _parse_iso(slot.get('as_of'))
            except ValueError as exc:
                self._reject(str(exc), slot_id=slot.get('slot_id'), capability=capability)
            except ExtConflict as exc:
                self._reject(str(exc), slot_id=slot.get('slot_id'), capability=capability)
            if exp != got:
                self._reject(
                    f'拒绝：时间核验失败（请求 as_of={expected_as_of} ≠ 槽位 as_of={slot.get("as_of")}）',
                    slot_id=slot.get('slot_id'),
                    capability=capability,
                )
        # 口径 check
        if expected_caliber and str(expected_caliber).strip() != str(slot.get('口径') or '').strip():
            self._reject(
                f'拒绝：口径核验失败（请求={expected_caliber} ≠ 槽位={slot.get("口径")}）',
                slot_id=slot.get('slot_id'),
                capability=capability,
            )
        # hash check
        if expected_hash and str(expected_hash).strip().lower() != str(slot.get('content_hash') or '').strip().lower():
            self._reject(
                f'拒绝：内容哈希核验失败（请求与槽位 content_hash 不一致）',
                slot_id=slot.get('slot_id'),
                capability=capability,
            )
        # max age optional (hours)
        max_age_hours = body.get('max_age_hours')
        if max_age_hours is not None:
            try:
                max_age_hours = float(max_age_hours)
            except (TypeError, ValueError) as exc:
                raise ValueError('max_age_hours 须为数字') from exc
            as_of = _parse_iso(slot.get('as_of'))
            age_h = (datetime.now(timezone.utc) - as_of).total_seconds() / 3600.0
            if age_h > max_age_hours:
                self._reject(
                    f'拒绝：来源 as_of 过旧（年龄 {age_h:.1f}h > max_age_hours={max_age_hours}）',
                    slot_id=slot.get('slot_id'),
                    capability=capability,
                )

    def claim_precise_flow(self, body):
        """Claim precise ⑤ readiness. Substitutes / bypass flags / omit-echo always 409."""
        body = body if isinstance(body, dict) else {}
        refuse_bypass_flags(body)
        refuse_precise_impersonation('precise_indicator_5', body)
        source = str(body.get('source') or body.get('substitute') or body.get('source_kind') or '').strip().lower()
        label_as = str(body.get('label_as') or '').strip().lower()
        forbidden = {x.lower() for x in FORBIDDEN_PRECISE_SUBSTITUTES}
        # also treat hyphen/underscore variants
        source_norm = source.replace('_', '-').replace(' ', '')
        forbidden_norm = {x.replace('_', '-').replace(' ', '') for x in forbidden}
        wants_precise = bool(body.get('as_precise_5')) or label_as in (
            'precise_5', '精确⑤', 'indicator_5', 'precise_indicator_5', '5',
        )

        if source in forbidden or source_norm in forbidden_norm:
            self._reject(
                f'拒绝替代源 {source or source_norm} 作为精确⑤（储备/技术资金流不可冒充）',
                source=source,
                label_as=label_as,
            )

        # Even without explicit substitute name, as_precise_5 with forbidden-ish source
        if wants_precise or body.get('claim_ready') or body.get('declare_ready'):
            if source in forbidden or source_norm in forbidden_norm:
                self._reject(f'拒绝替代源 {source} 作为精确⑤', source=source)
            with self._lock:
                slot = self._read_sources()['slots']['precise_indicator_5']
            refuse_precise_impersonation('precise_indicator_5', body, slot)
            self._check_slot_gates(slot, body, 'precise_indicator_5', require_echo=True)
            return self._accept(
                '精确⑤来源槽位已授权且时间/口径/哈希核验通过',
                capability='precise_indicator_5',
                source_slot={
                    'slot_id': 'precise_indicator_5',
                    'status': slot.get('status'),
                    'as_of': slot.get('as_of'),
                    '口径': slot.get('口径'),
                    'content_hash': slot.get('content_hash'),
                    'url': slot.get('url'),
                },
            )

        # Non-claim probe: still refuse if they only pass a forbidden source
        return {
            'accepted': False,
            'ready': False,
            'note': (
                '未请求声明精确⑤就绪；来源槽位默认未授权。'
                '传入 as_precise_5/claim_ready，且槽位 authorized，'
                '并提供 expected_as_of/expected_口径/expected_content_hash 后才可能接受。'
            ),
            'disclaimer': DISCLAIMER,
        }

    def claim_capability(self, body):
        """Generic claim for events/unlock/supply/historical_mcap (not engines)."""
        body = body if isinstance(body, dict) else {}
        refuse_bypass_flags(body)
        capability = str(body.get('capability') or body.get('slot_id') or '').strip()
        if capability in ('precise_indicator_5', 'precise_5', '精确⑤'):
            return self.claim_precise_flow({**body, 'as_precise_5': True})
        if capability == 'multi_market_sim_engine' or capability in ('usdc', 'coin-m', 'coinm'):
            self._reject(
                '拒绝：USDC/COIN-M 计价引擎仍为 catalog-only not_ready，不可通过 claim 声明就绪',
                capability=capability,
            )
        if capability not in SLOT_IDS:
            raise ValueError(f'未知能力/槽位: {capability}；可选 {list(SLOT_IDS)}')

        # unlock/events: refuse if note tries banned wording via claim
        claim_note = str(body.get('note') or '')
        if '二手日历' in claim_note:
            raise ValueError('勿使用「二手日历」；请写「第三方解锁网站上的时间」或「未在官网上核对」')

        # historical_mcap: refuse today-backfill impersonation flags
        if capability == 'historical_mcap':
            for bad in ('backfill_from_today', 'use_today_mcap', 'use_current_mcap', 'today_as_history'):
                if bad in body and _truthy_flag(body.get(bad)):
                    self._reject(
                        f'拒绝：历史市值不可用 {bad} 冒充（禁止当今市值回填 / HTTP 409）',
                        capability=capability,
                    )

        # Non-precise slots still cannot be labeled as precise⑤ via alternate claim
        label_as = str(body.get('label_as') or '').strip().lower()
        if label_as in ('precise_5', '精确⑤', 'indicator_5', 'precise_indicator_5', '5') or body.get('as_precise_5'):
            self._reject(
                f'拒绝：槽位 {capability} 不可冒充精确⑤；请走 precise_indicator_5 授权槽位',
                capability=capability,
                label_as=label_as,
            )

        with self._lock:
            slot = self._read_sources()['slots'][capability]
        self._check_slot_gates(slot, body, capability, require_echo=True)
        note = '来源槽位已授权且时间/口径核验通过'
        if capability == 'unlock':
            note += '（解锁时间须按第三方解锁网站上的时间或未在官网上核对如实标注）'
        return self._accept(note, capability=capability, source_slot={
            'slot_id': capability,
            'status': slot.get('status'),
            'as_of': slot.get('as_of'),
            '口径': slot.get('口径'),
            'content_hash': slot.get('content_hash'),
            'url': slot.get('url'),
        })
