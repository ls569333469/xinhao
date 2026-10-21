"""CONFIG-01: persist workbench config + backup/restore wizard (no credentials).

Research workbench only. Never exports credentials or DPAPI blobs.
Backup package covers pool/prompts/strategy drafts+version refs/sim budgets/
eval+review plans; SIM ledger excluded by default. Import refuses silent overwrite; manifest+payload hashes verified; path escape refused on restore.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from snapshot_store import atomic, encoded

SCHEMA = 'workbench-config-v1'
BACKUP_SCHEMA = 'workbench-backup-v1'
BACKUP_SECTIONS = (
    'config_core', 'strategies', 'sim', 'eval_plans', 'review_plans',
)
DISCLAIMER = (
    '工作台持久配置：观察池/提示词/备份恢复向导；'
    '导出与备份包强制排除凭证；SIM 账本默认不进包；冲突拒绝静默覆盖。'
)

SECRET_KEY_RE = re.compile(
    r'(api[_-]?key|secret|password|token|credential|dpapi|private[_-]?key|authorization)',
    re.I,
)

DEFAULT_STATE = {
    'schema': SCHEMA,
    'observation_pool': {
        'symbols': [],  # [{symbol, market, note, added_at}]
        'note': '观察池：自选合约列表，刷新后保留',
    },
    'prompt_edits': {
        # overlay on design/prompt-templates.json — only user deltas
        'base_version': None,
        'overrides': {},  # path -> text, e.g. "common" or "profiles.X.system"
        'updated_at': None,
    },
    'account_cost': {
        # non-secret prefs; live fee/slip still owned by SIM config — this is a bookmark/exportable copy
        'fee_rate_pref': None,
        'slip_rate_pref': None,
        'note': '仅偏好快照；实盘模拟费用以 SIM-01 config 为准',
    },
    'meta': {
        'updated_at': None,
        'revision': 0,
        'content_hash': None,
    },
    'disclaimer': DISCLAIMER,
}


def _utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha(obj):
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


# Meta / boolean flags that mention credentials but are not secret values.
_STRIP_ALLOWLIST = {
    'credentials_included', 'secrets_stripped_paths', 'export_excludes_credentials',
}


def _strip_secrets(obj, path=''):
    """Recursively drop secret-looking keys; return (cleaned, removed_paths)."""
    removed = []
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            pth = f'{path}.{k}' if path else k
            key_s = str(k)
            if key_s not in _STRIP_ALLOWLIST and SECRET_KEY_RE.search(key_s):
                # Keep non-string meta flags? No — drop actual secret-bearing keys.
                # Allowlist handles boolean policy fields.
                removed.append(pth)
                continue
            cv, rr = _strip_secrets(v, pth)
            out[k] = cv
            removed.extend(rr)
        return out, removed
    if isinstance(obj, list):
        out = []
        for i, v in enumerate(obj):
            cv, rr = _strip_secrets(v, f'{path}[{i}]')
            out.append(cv)
            removed.extend(rr)
        return out, removed
    return obj, removed


class ConfigConflict(Exception):
    """Maps to HTTP 409 — refuse silent overwrite / tampered backup."""

    def __init__(self, message, *, http_status=409):
        super().__init__(message)
        self.http_status = http_status


# Safe restore IDs: no path separators / traversal
_SAFE_ENTITY_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
_DRAFT_ID_RE = re.compile(r'^draft-[A-Za-z0-9][A-Za-z0-9._-]{0,120}$')
_VERSION_ID_RE = re.compile(r'^ver-[A-Za-z0-9][A-Za-z0-9._-]{0,120}$')


def _payload_hash(data):
    """Canonical content hash of a backup section payload (secrets stripped)."""
    cleaned, _ = _strip_secrets(data)
    return _sha(cleaned)


def _assert_safe_entity_id(entity_id, *, kind):
    """Refuse path escape / odd IDs before any filesystem join."""
    if not isinstance(entity_id, str) or not entity_id.strip():
        raise ConfigConflict(f'{kind} id 无效；拒绝恢复（HTTP 409）')
    eid = entity_id.strip()
    bad = ('/', '\\', '\x00')
    if any(ch in eid for ch in bad) or '..' in eid or eid.startswith('.'):
        raise ConfigConflict(
            f'{kind} id 含路径成分 {eid!r}；拒绝路径逃逸恢复（HTTP 409）'
        )
    if kind == 'draft':
        if not _DRAFT_ID_RE.match(eid):
            raise ConfigConflict(
                f'draft_id 须匹配 draft-… 安全字符集；拒绝 {eid!r}（HTTP 409）'
            )
    elif kind == 'version':
        if not _VERSION_ID_RE.match(eid):
            raise ConfigConflict(
                f'version_id 须匹配 ver-… 安全字符集；拒绝 {eid!r}（HTTP 409）'
            )
    elif not _SAFE_ENTITY_ID_RE.match(eid):
        raise ConfigConflict(f'{kind} id 非法：{eid!r}（HTTP 409）')
    return eid


def _safe_json_path(base_dir, entity_id, *, kind):
    """Join base_dir / f'{id}.json' and ensure result stays under base_dir."""
    eid = _assert_safe_entity_id(entity_id, kind=kind)
    base = Path(base_dir).resolve()
    target = (base / f'{eid}.json').resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ConfigConflict(
            f'{kind} 恢复路径逃逸已拒绝: {entity_id!r}（HTTP 409）'
        ) from exc
    return target


class ConfigStore:
    def __init__(self, root):
        self.root = Path(root)
        self.dir = self.root / 'runtime' / 'config'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / 'state.json'
        self.exports_dir = self.dir / 'exports'
        self.backups_dir = self.dir / 'backups'
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        self.strategy_store = None
        self.sim_store = None
        self.eval_store = None
        self.review_store = None
        self._lock = threading.RLock()
        self._ensure()

    def bind_stores(self, strategy_store=None, sim_store=None, eval_store=None, review_store=None):
        """Optional live stores for backup gather/restore (server wires these)."""
        if strategy_store is not None:
            self.strategy_store = strategy_store
        if sim_store is not None:
            self.sim_store = sim_store
        if eval_store is not None:
            self.eval_store = eval_store
        if review_store is not None:
            self.review_store = review_store

    def _ensure(self):
        with self._lock:
            if not self.state_path.exists():
                state = copy.deepcopy(DEFAULT_STATE)
                state['meta']['updated_at'] = _utcnow()
                state['meta']['revision'] = 1
                state['meta']['content_hash'] = self._hash_payload(state)
                atomic(self.state_path, encoded(state))

    def _read(self):
        return json.loads(self.state_path.read_text(encoding='utf-8'))

    def _hash_payload(self, state):
        payload = {
            'observation_pool': state.get('observation_pool'),
            'prompt_edits': state.get('prompt_edits'),
            'account_cost': state.get('account_cost'),
        }
        return _sha(payload)

    def _save(self, state):
        state = copy.deepcopy(state)
        state['schema'] = SCHEMA
        state['disclaimer'] = DISCLAIMER
        meta = state.setdefault('meta', {})
        meta['updated_at'] = _utcnow()
        meta['revision'] = int(meta.get('revision') or 0) + 1
        meta['content_hash'] = self._hash_payload(state)
        atomic(self.state_path, encoded(state))
        return state

    def get(self):
        with self._lock:
            state = self._read()
            state.setdefault('disclaimer', DISCLAIMER)
            return state

    def get_observation_pool(self):
        return self.get()['observation_pool']

    def set_observation_pool(self, body):
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')
        symbols = body.get('symbols')
        if symbols is None:
            raise ValueError('须提供 symbols 数组')
        if not isinstance(symbols, list):
            raise ValueError('symbols 须为数组')
        cleaned = []
        seen = set()
        for item in symbols:
            if isinstance(item, str):
                sym = item.strip().upper()
                market = 'USDⓈ-M'
                note = ''
            elif isinstance(item, dict):
                sym = str(item.get('symbol') or '').strip().upper()
                market = item.get('market') or 'USDⓈ-M'
                note = str(item.get('note') or '')[:200]
            else:
                raise ValueError('symbols 项须为字符串或对象')
            if not sym:
                continue
            key = (market, sym)
            if key in seen:
                continue
            seen.add(key)
            cleaned.append({
                'symbol': sym,
                'market': market,
                'note': note,
                'added_at': (item.get('added_at') if isinstance(item, dict) else None) or _utcnow(),
            })
            if len(cleaned) > 500:
                raise ValueError('观察池最多 500 项')
        with self._lock:
            state = self._read()
            state['observation_pool'] = {
                'symbols': cleaned,
                'note': body.get('note') or state['observation_pool'].get('note') or DEFAULT_STATE['observation_pool']['note'],
                'updated_at': _utcnow(),
            }
            return self._save(state)['observation_pool']

    def get_prompt_edits(self):
        pe = self.get()['prompt_edits']
        # R2a REV-08: expose migration hints without silently wiping custom text
        pe = dict(pe or {})
        overrides = dict(pe.get('overrides') or {})
        std = overrides.get('standard')
        legacy_sha = "c5eb5efd17439942372d821f3d8d9befb6401c2d867b0e407cf8495b2e4f1bb0"
        if isinstance(std, str) and std.strip():
            digest = __import__('hashlib').sha256(std.strip().encode('utf-8')).hexdigest()
            if digest == legacy_sha:
                pe['migration_hint'] = {
                    'profile': 'standard',
                    'action': 'unmodified_default_may_migrate_to_template_b',
                    'from_sha256': legacy_sha,
                    'base_version': pe.get('base_version') or 'research-prompts-v1.0',
                    'target_base_version': 'research-prompts-v1.1-template-b/standard',
                    'note': '未改过的默认 standard 可迁移到模板 B；调用方应换用新目录 instruction，勿静默覆盖用户自定义',
                }
            else:
                pe['migration_hint'] = {
                    'profile': 'standard',
                    'action': 'keep_user_custom',
                    'base_version': pe.get('base_version') or 'custom-over-research-prompts-v1.0',
                    'note': '用户自定义提示词保留；UI 须显示基于版本',
                }
        elif pe.get('base_version') in (None, '', 'research-prompts-v1.0'):
            pe = dict(pe)
            if not pe.get('base_version'):
                pe['base_version'] = 'research-prompts-v1.1-template-b'
        return pe

    def migrate_unmodified_standard_prompt(self, new_instruction, *, base_version='research-prompts-v1.1-template-b/standard'):
        """If stored standard override equals legacy default, replace with B and stamp base_version.

        Custom overrides are left untouched. Returns apply result dict.
        """
        if not isinstance(new_instruction, str) or not new_instruction.strip():
            raise ValueError('new_instruction 须为非空字符串')
        legacy_sha = "c5eb5efd17439942372d821f3d8d9befb6401c2d867b0e407cf8495b2e4f1bb0"
        with self._lock:
            state = self._read()
            pe = state.setdefault('prompt_edits', {'base_version': None, 'overrides': {}, 'updated_at': None})
            overrides = pe.setdefault('overrides', {})
            std = overrides.get('standard')
            if not isinstance(std, str) or not std.strip():
                pe['base_version'] = base_version
                pe['updated_at'] = _utcnow()
                state['prompt_edits'] = pe
                self._save(state)
                return {'action': 'no_override', 'base_version': base_version}
            digest = __import__('hashlib').sha256(std.strip().encode('utf-8')).hexdigest()
            if digest != legacy_sha:
                if not pe.get('base_version'):
                    pe['base_version'] = 'custom-over-research-prompts-v1.0'
                    pe['updated_at'] = _utcnow()
                    state['prompt_edits'] = pe
                    self._save(state)
                return {'action': 'keep_user_custom', 'base_version': pe.get('base_version')}
            overrides['standard'] = new_instruction
            pe['base_version'] = base_version
            pe['updated_at'] = _utcnow()
            pe['migrated_from_sha256'] = legacy_sha
            state['prompt_edits'] = pe
            self._save(state)
            return {'action': 'migrated_default_to_template_b', 'base_version': base_version}

    def set_prompt_edits(self, body):
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')
        overrides = body.get('overrides')
        if overrides is None:
            raise ValueError('须提供 overrides 对象')
        if not isinstance(overrides, dict):
            raise ValueError('overrides 须为对象')
        # Reject secret-looking override keys
        clean = {}
        for k, v in overrides.items():
            if not isinstance(k, str) or not k.strip():
                raise ValueError('override 键须为非空字符串')
            if SECRET_KEY_RE.search(k):
                raise ValueError(f'拒绝疑似密钥字段: {k}')
            if v is not None and not isinstance(v, str):
                raise ValueError(f'override 值须为字符串: {k}')
            if v is not None and len(v) > 50000:
                raise ValueError(f'override 过长: {k}')
            if v is not None:
                clean[k.strip()] = v
        with self._lock:
            state = self._read()
            # Optional expected hash for conflict detection
            expected = body.get('expected_content_hash') or body.get('if_match_hash')
            if expected and state['meta'].get('content_hash') != expected:
                raise ConfigConflict(
                    f'配置已变更（期望 {expected[:12]}…，当前 {state["meta"].get("content_hash","")[:12]}…），拒绝覆盖'
                )
            pe = state.setdefault('prompt_edits', copy.deepcopy(DEFAULT_STATE['prompt_edits']))
            if body.get('replace'):
                pe['overrides'] = clean
            else:
                pe.setdefault('overrides', {}).update(clean)
                # allow null to delete via body.delete_keys
            for dk in body.get('delete_keys') or []:
                pe['overrides'].pop(str(dk), None)
            pe['base_version'] = body.get('base_version', pe.get('base_version'))
            pe['updated_at'] = _utcnow()
            state['prompt_edits'] = pe
            if 'account_cost' in body and isinstance(body['account_cost'], dict):
                ac = state.setdefault('account_cost', {})
                for key in ('fee_rate_pref', 'slip_rate_pref'):
                    if key in body['account_cost']:
                        val = body['account_cost'][key]
                        if val is not None:
                            val = float(val)
                            if val < 0 or val > 0.05:
                                raise ValueError(f'{key} 须在 0–0.05')
                        ac[key] = val
            return self._save(state)

    def export_bundle(self, include_credentials=False):
        """Export config JSON. Credentials NEVER included unless explicitly forced — and even then refused."""
        if include_credentials:
            raise ValueError('导出拒绝包含凭证（CONFIG-01：默认且强制排除密钥）')
        with self._lock:
            state = self._read()
            # Never read runtime/credentials
            bundle = {
                'schema': 'workbench-config-export-v1',
                'exported_at': _utcnow(),
                'source_revision': state.get('meta', {}).get('revision'),
                'source_content_hash': state.get('meta', {}).get('content_hash'),
                'observation_pool': state.get('observation_pool'),
                'prompt_edits': state.get('prompt_edits'),
                'account_cost': state.get('account_cost'),
                'credentials_included': False,
                'disclaimer': DISCLAIMER,
            }
            cleaned, removed = _strip_secrets(bundle)
            cleaned['secrets_stripped_paths'] = removed
            # Persist a copy under exports for audit trail (still no secrets)
            name = f'export-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}-{uuid.uuid4().hex[:8]}.json'
            atomic(self.exports_dir / name, encoded(cleaned))
            cleaned['export_file'] = f'runtime/config/exports/{name}'
            return cleaned

    def import_bundle(self, body):
        if not isinstance(body, dict):
            raise ValueError('导入体须为对象')
        if body.get('credentials_included'):
            raise ValueError('拒绝导入含凭证标记的包')
        # Strip secrets again
        cleaned, removed = _strip_secrets(body)
        force = bool(body.get('force'))
        expected = body.get('expected_content_hash') or body.get('if_match_hash')
        with self._lock:
            state = self._read()
            current_hash = state['meta'].get('content_hash')
            if not force:
                if expected and expected != current_hash:
                    raise ConfigConflict('当前配置与 expected_content_hash 不一致，拒绝覆盖（可 force=true）')
                # If incoming has source_content_hash and local has been edited differently
                # and caller didn't pass expected — still protect when local revision>1 and hashes differ
                incoming_hash = cleaned.get('source_content_hash')
                if incoming_hash and current_hash and incoming_hash != current_hash and int(state['meta'].get('revision') or 0) > 1:
                    if not body.get('allow_divergent'):
                        raise ConfigConflict(
                            '本地配置已相对导出源分叉；拒绝静默覆盖。传 allow_divergent=true 或 force=true 明确确认'
                        )
            if 'observation_pool' in cleaned and isinstance(cleaned['observation_pool'], dict):
                state['observation_pool'] = cleaned['observation_pool']
            if 'prompt_edits' in cleaned and isinstance(cleaned['prompt_edits'], dict):
                state['prompt_edits'] = cleaned['prompt_edits']
            if 'account_cost' in cleaned and isinstance(cleaned['account_cost'], dict):
                state['account_cost'] = cleaned['account_cost']
            saved = self._save(state)
            return {
                'status': 'imported',
                'revision': saved['meta']['revision'],
                'content_hash': saved['meta']['content_hash'],
                'secrets_stripped_paths': removed,
                'disclaimer': DISCLAIMER,
            }


    # --- Backup / restore wizard (CONFIG-01 deepen) ---

    def _options(self, body=None):
        body = body if isinstance(body, dict) else {}
        include_sim_ledger = bool(body.get('include_sim_ledger'))
        if body.get('include_credentials'):
            raise ValueError('备份/导出拒绝包含凭证（永远排除 runtime/credentials 与密钥字段）')
        sections = body.get('sections')
        if sections is None:
            sections = list(BACKUP_SECTIONS)
        if isinstance(sections, str):
            sections = [sections]
        if not isinstance(sections, list) or not sections:
            raise ValueError('sections 须为非空列表')
        cleaned = []
        for s in sections:
            s = str(s).strip()
            if s not in BACKUP_SECTIONS:
                raise ValueError(f'未知备份段: {s}；可选 {list(BACKUP_SECTIONS)}')
            if s not in cleaned:
                cleaned.append(s)
        return {
            'include_sim_ledger': include_sim_ledger,
            'sections': cleaned,
            'include_strategy_bodies': body.get('include_strategy_bodies', True) is not False,
        }

    def _gather_config_core(self):
        state = self._read()
        payload = {
            'observation_pool': state.get('observation_pool'),
            'prompt_edits': state.get('prompt_edits'),
            'account_cost': state.get('account_cost'),
            'source_revision': (state.get('meta') or {}).get('revision'),
            'source_content_hash': (state.get('meta') or {}).get('content_hash'),
        }
        return payload, _sha(payload)

    def _gather_strategies(self, include_bodies=True):
        drafts_dir = self.root / 'runtime' / 'strategies' / 'drafts'
        versions_dir = self.root / 'runtime' / 'strategies' / 'versions'
        drafts, versions, refs = [], [], []
        if drafts_dir.exists():
            for path in sorted(drafts_dir.glob('draft-*.json')):
                try:
                    raw = json.loads(path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                cleaned, _ = _strip_secrets(raw)
                refs.append({
                    'kind': 'draft',
                    'draft_id': cleaned.get('draft_id'),
                    'strategy_id': cleaned.get('strategy_id'),
                    'title': cleaned.get('title'),
                    'updated_at': cleaned.get('updated_at'),
                })
                if include_bodies:
                    drafts.append(cleaned)
        if versions_dir.exists():
            for path in sorted(versions_dir.glob('ver-*.json')):
                try:
                    raw = json.loads(path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                cleaned, _ = _strip_secrets(raw)
                ref = {
                    'kind': 'version',
                    'version_id': cleaned.get('version_id'),
                    'strategy_id': cleaned.get('strategy_id'),
                    'version': cleaned.get('version'),
                    'title': cleaned.get('title'),
                    'content_hash': cleaned.get('content_hash'),
                    'enabled': cleaned.get('enabled'),
                    'frozen_at': cleaned.get('frozen_at'),
                }
                refs.append(ref)
                if include_bodies:
                    versions.append(cleaned)
        payload = {
            'refs': refs,
            'drafts': drafts if include_bodies else [],
            'versions': versions if include_bodies else [],
            'bodies_included': bool(include_bodies),
            'note': '冻结版本按 content_hash 核验；凭证字段已剥离',
        }
        return payload, _sha({
            'refs': refs,
            'draft_ids': [d.get('draft_id') for d in drafts],
            'version_hashes': [v.get('content_hash') for v in versions],
        })

    def _gather_sim(self, include_ledger=False):
        cfg = None
        if self.sim_store is not None and hasattr(self.sim_store, 'get_config'):
            try:
                cfg = self.sim_store.get_config()
            except Exception:
                cfg = None
        cfg_path = self.root / 'runtime' / 'sim' / 'config.json'
        if cfg is None and cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                cfg = None
        # Drop volatile tier_budgets mirror if present — keep budget_t*_u
        if isinstance(cfg, dict):
            cfg = {k: v for k, v in cfg.items() if k != 'tier_budgets'}
            cfg, _ = _strip_secrets(cfg)
        ledger = None
        ledger_meta = {'included': False, 'event_count': 0}
        events_path = self.root / 'runtime' / 'sim' / 'events.jsonl'
        if include_ledger and events_path.exists():
            events = []
            with events_path.open('r', encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        continue
                    if len(events) > 5000:
                        break
            events, _ = _strip_secrets(events)
            ledger = {'events': events, 'truncated': len(events) >= 5000}
            ledger_meta = {'included': True, 'event_count': len(events), 'truncated': ledger['truncated']}
        elif events_path.exists():
            # count only
            n = 0
            with events_path.open('r', encoding='utf-8') as fh:
                for line in fh:
                    if line.strip():
                        n += 1
            ledger_meta = {'included': False, 'event_count': n, 'note': '默认排除账本；传 include_sim_ledger=true 才打包'}
        payload = {
            'config': cfg,
            'ledger': ledger,
            'ledger_meta': ledger_meta,
        }
        return payload, _sha({'config': cfg, 'ledger_included': include_ledger, 'event_count': ledger_meta.get('event_count')})

    def _gather_eval_plans(self):
        plans = {}
        if self.eval_store is not None and hasattr(self.eval_store, 'list_plans'):
            try:
                listed = self.eval_store.list_plans()
                for p in (listed.get('plans') or []):
                    if isinstance(p, dict) and p.get('plan_id'):
                        plans[p['plan_id']] = p
            except Exception:
                plans = {}
        state_path = self.root / 'runtime' / 'eval' / 'state.json'
        if not plans and state_path.exists():
            try:
                st = json.loads(state_path.read_text(encoding='utf-8'))
                plans = st.get('plans') or {}
            except (OSError, ValueError):
                plans = {}
        plans, _ = _strip_secrets(plans)
        payload = {
            'plans': plans,
            'plan_count': len(plans),
            'note': '仅观察计划；不含 observations/settlements 流水',
        }
        return payload, _sha(plans)

    def _gather_review_plans(self):
        plans = {}
        pause = None
        if self.review_store is not None:
            try:
                if hasattr(self.review_store, 'list_plans'):
                    listed = self.review_store.list_plans()
                    for p in (listed.get('plans') or []):
                        if isinstance(p, dict) and p.get('plan_id'):
                            plans[p['plan_id']] = p
                if hasattr(self.review_store, 'get_pause_new_entries'):
                    pause = bool(self.review_store.get_pause_new_entries())
            except Exception:
                pass
        state_path = self.root / 'runtime' / 'reviews' / 'state.json'
        if not plans and state_path.exists():
            try:
                st = json.loads(state_path.read_text(encoding='utf-8'))
                plans = st.get('plans') or {}
                if pause is None:
                    pause = st.get('pause_new_entries')
            except (OSError, ValueError):
                plans = {}
        plans, _ = _strip_secrets(plans)
        payload = {
            'plans': plans,
            'pause_new_entries': pause,
            'plan_count': len(plans),
            'note': '仅复盘计划与暂停开关；不含 reviews 流水',
        }
        return payload, _sha({'plans': plans, 'pause_new_entries': pause})

    def _gather_section(self, section_id, opts):
        if section_id == 'config_core':
            data, h = self._gather_config_core()
            title = '观察池 / 提示词 / 成本偏好'
        elif section_id == 'strategies':
            data, h = self._gather_strategies(include_bodies=opts.get('include_strategy_bodies', True))
            title = '策略草案与冻结版本'
        elif section_id == 'sim':
            data, h = self._gather_sim(include_ledger=opts.get('include_sim_ledger', False))
            title = 'SIM 费用与分档预算' + ('（含账本）' if opts.get('include_sim_ledger') else '（不含账本）')
        elif section_id == 'eval_plans':
            data, h = self._gather_eval_plans()
            title = 'EVAL 观察计划'
        elif section_id == 'review_plans':
            data, h = self._gather_review_plans()
            title = 'REVIEW 复盘计划'
        else:
            raise ValueError(f'未知段: {section_id}')
        return {
            'id': section_id,
            'title': title,
            'content_hash': h,
            'item_count': self._section_item_count(section_id, data),
            'data': data,
        }

    def _section_item_count(self, section_id, data):
        if section_id == 'config_core':
            return len((data.get('observation_pool') or {}).get('symbols') or [])
        if section_id == 'strategies':
            return len(data.get('refs') or [])
        if section_id == 'sim':
            return 1 if data.get('config') else 0
        if section_id in ('eval_plans', 'review_plans'):
            return int(data.get('plan_count') or 0)
        return 0

    def backup_manifest(self, body=None):
        """Wizard step 1: preview sections/hashes without writing a package file."""
        opts = self._options(body)
        with self._lock:
            sections = []
            for sid in opts['sections']:
                gathered = self._gather_section(sid, opts)
                sections.append({
                    'id': gathered['id'],
                    'title': gathered['title'],
                    'content_hash': gathered['content_hash'],
                    'payload_hash': _payload_hash(gathered['data']),
                    'item_count': gathered['item_count'],
                    'ledger_included': (
                        (gathered['data'].get('ledger_meta') or {}).get('included')
                        if sid == 'sim' else None
                    ),
                })
            manifest = {
                'schema': 'workbench-backup-manifest-v1',
                'created_at': _utcnow(),
                'sections': sections,
                'include_sim_ledger': opts['include_sim_ledger'],
                'credentials_included': False,
                'package_hash': _sha(sections),
                'integrity': 'manifest+payload_hash',
                'disclaimer': DISCLAIMER,
                'wizard_step': 'manifest',
            }
            return manifest

    def backup_export(self, body=None):
        """Wizard step 2: build full backup package (no credentials)."""
        opts = self._options(body)
        with self._lock:
            section_payloads = []
            section_data = {}
            for sid in opts['sections']:
                gathered = self._gather_section(sid, opts)
                section_payloads.append({
                    'id': gathered['id'],
                    'title': gathered['title'],
                    'content_hash': gathered['content_hash'],
                    'payload_hash': _payload_hash(gathered['data']),
                    'item_count': gathered['item_count'],
                })
                section_data[sid] = gathered['data']
            package = {
                'schema': BACKUP_SCHEMA,
                'exported_at': _utcnow(),
                'credentials_included': False,
                'include_sim_ledger': opts['include_sim_ledger'],
                'manifest': {
                    'schema': 'workbench-backup-manifest-v1',
                    'sections': section_payloads,
                    'package_hash': _sha(section_payloads),
                    'integrity': 'manifest+payload_hash',
                    'wizard_step': 'export',
                },
                'sections': section_data,
                'disclaimer': DISCLAIMER,
            }
            cleaned, removed = _strip_secrets(package)
            cleaned['secrets_stripped_paths'] = removed
            if cleaned.get('credentials_included'):
                raise ValueError('内部错误：备份包不得含凭证标记为 true')
            name = f'backup-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}-{uuid.uuid4().hex[:8]}.json'
            atomic(self.backups_dir / name, encoded(cleaned))
            cleaned['backup_file'] = f'runtime/config/backups/{name}'
            cleaned['wizard_step'] = 'export'
            return cleaned

    def _verify_backup_integrity(self, package):
        """Refuse packages with missing/mismatched manifest or payload hashes.

        Each section payload_hash must equal _payload_hash(data);
        manifest.package_hash must equal _sha(manifest.sections).
        Omitting hashes or mutating content → ConfigConflict (HTTP 409).
        """
        if not isinstance(package, dict):
            raise ConfigConflict('备份包无效；拒绝导入（HTTP 409）')
        if package.get('schema') != BACKUP_SCHEMA:
            return {
                'verified': False,
                'legacy': True,
                'note': '非 workbench-backup-v1，跳过 payload 联校验',
            }
        manifest = package.get('manifest')
        if not isinstance(manifest, dict):
            raise ConfigConflict('备份包缺少 manifest；拒绝跳过内容校验（HTTP 409）')
        meta_sections = manifest.get('sections')
        if not isinstance(meta_sections, list) or not meta_sections:
            raise ConfigConflict('manifest.sections 为空；拒绝跳过内容校验（HTTP 409）')
        section_data = package.get('sections')
        if not isinstance(section_data, dict):
            raise ConfigConflict('备份包缺少 sections 载荷；拒绝导入（HTTP 409）')
        pkg_hash = manifest.get('package_hash')
        if not pkg_hash:
            raise ConfigConflict('缺少 manifest.package_hash；拒绝跳过内容校验（HTTP 409）')
        recomputed_pkg = _sha(meta_sections)
        if recomputed_pkg != pkg_hash:
            raise ConfigConflict(
                f'manifest.package_hash 不匹配（期望 {pkg_hash[:16]}…，重算 {recomputed_pkg[:16]}…）；'
                '包元数据已篡改或损坏（HTTP 409）'
            )
        verified = []
        for meta in meta_sections:
            if not isinstance(meta, dict):
                raise ConfigConflict('manifest.sections 项无效（HTTP 409）')
            sid = meta.get('id')
            if sid not in BACKUP_SECTIONS:
                raise ConfigConflict(f'未知备份段 {sid!r}（HTTP 409）')
            if not meta.get('payload_hash'):
                raise ConfigConflict(
                    f'段 {sid} 缺少 payload_hash；拒绝跳过内容校验（HTTP 409）'
                )
            if sid not in section_data:
                raise ConfigConflict(
                    f'manifest 声明段 {sid} 但 sections 无对应载荷（HTTP 409）'
                )
            actual = _payload_hash(section_data[sid])
            declared = str(meta['payload_hash'])
            if actual != declared:
                raise ConfigConflict(
                    f'段 {sid} 载荷与 payload_hash 不一致（包内容已篡改）；'
                    f'declared={declared[:16]}… actual={actual[:16]}…（HTTP 409）'
                )
            verified.append({'id': sid, 'payload_hash': declared})
        extra = set(section_data.keys()) - {m.get('id') for m in meta_sections}
        if extra:
            raise ConfigConflict(
                f'sections 含未在 manifest 声明的段 {sorted(extra)}；拒绝导入（HTTP 409）'
            )
        return {
            'verified': True,
            'legacy': False,
            'package_hash': pkg_hash,
            'sections': verified,
            'integrity': 'manifest+payload_hash',
        }

    def backup_review(self, body):
        """Wizard step 3: dry-run conflict report vs current workspace (no write)."""
        if not isinstance(body, dict):
            raise ValueError('须提供备份包对象')
        if body.get('credentials_included'):
            raise ValueError('拒绝审阅含凭证标记的包')
        cleaned, removed = _strip_secrets(body)
        package = cleaned.get('package') if isinstance(cleaned.get('package'), dict) else cleaned
        if package.get('schema') not in (BACKUP_SCHEMA, 'workbench-config-export-v1'):
            if 'observation_pool' not in package and 'sections' not in package:
                raise ValueError(f'无法识别备份 schema: {package.get("schema")}')
        integrity = self._verify_backup_integrity(package)
        conflicts = []
        applies = []
        unchanged = []
        with self._lock:
            if package.get('schema') == BACKUP_SCHEMA:
                sections = package.get('sections') or {}
                for sid, data in sections.items():
                    report = self._review_section(sid, data)
                    conflicts.extend(report['conflicts'])
                    applies.extend(report['applies'])
                    unchanged.extend(report['unchanged'])
            else:
                report = self._review_section('config_core', {
                    'observation_pool': package.get('observation_pool'),
                    'prompt_edits': package.get('prompt_edits'),
                    'account_cost': package.get('account_cost'),
                    'source_content_hash': package.get('source_content_hash'),
                })
                conflicts.extend(report['conflicts'])
                applies.extend(report['applies'])
                unchanged.extend(report['unchanged'])
        return {
            'schema': 'workbench-backup-review-v1',
            'wizard_step': 'review',
            'package_schema': package.get('schema'),
            'package_hash': (package.get('manifest') or {}).get('package_hash'),
            'integrity': integrity,
            'conflicts': conflicts,
            'would_apply': applies,
            'unchanged': unchanged,
            'conflict_count': len(conflicts),
            'can_import_without_force': len(conflicts) == 0,
            'secrets_stripped_paths': removed,
            'credentials_included': False,
            'disclaimer': DISCLAIMER,
            'note': '本步不写盘；有冲突时须 force=true 或逐项确认后导入；篡改包在完整性校验阶段已拒绝',
        }

    def _review_section(self, section_id, data):
        conflicts, applies, unchanged = [], [], []
        data = data or {}
        if section_id == 'config_core':
            cur, cur_h = self._gather_config_core()
            incoming_core = {
                'observation_pool': data.get('observation_pool'),
                'prompt_edits': data.get('prompt_edits'),
                'account_cost': data.get('account_cost'),
            }
            local_core = {
                'observation_pool': cur.get('observation_pool'),
                'prompt_edits': cur.get('prompt_edits'),
                'account_cost': cur.get('account_cost'),
            }
            if incoming_core == local_core:
                unchanged.append({'section': section_id, 'reason': '与本地 config_core 一致'})
            elif int((self._read().get('meta') or {}).get('revision') or 0) > 1 and cur_h:
                conflicts.append({
                    'section': section_id,
                    'code': 'config_hash_fork',
                    'reason': (
                        f'本地配置已相对备份分叉（local={cur_h[:12]}…）；'
                        '拒绝静默覆盖'
                    ),
                    'local_hash': cur_h,
                    'package_source_hash': data.get('source_content_hash'),
                })
            else:
                applies.append({'section': section_id, 'action': 'replace_config_core'})
        elif section_id == 'strategies':
            local_versions = {}
            vdir = self.root / 'runtime' / 'strategies' / 'versions'
            if vdir.exists():
                for path in vdir.glob('ver-*.json'):
                    try:
                        raw = json.loads(path.read_text(encoding='utf-8'))
                        local_versions[raw.get('version_id')] = raw.get('content_hash')
                    except (OSError, ValueError, TypeError):
                        continue
            for ver in data.get('versions') or []:
                vid = ver.get('version_id')
                ph = ver.get('content_hash')
                if not vid:
                    continue
                if vid in local_versions and local_versions[vid] and ph and local_versions[vid] != ph:
                    conflicts.append({
                        'section': section_id,
                        'code': 'version_hash_conflict',
                        'version_id': vid,
                        'reason': f'冻结版本 {vid} 本地 hash 与备份不一致，拒绝静默覆盖不可变版本',
                        'local_hash': local_versions[vid],
                        'package_hash': ph,
                    })
                elif vid in local_versions and local_versions[vid] == ph:
                    unchanged.append({'section': section_id, 'version_id': vid, 'reason': '版本 hash 相同，跳过'})
                else:
                    applies.append({'section': section_id, 'action': 'restore_version', 'version_id': vid})
            for draft in data.get('drafts') or []:
                did = draft.get('draft_id')
                if not did:
                    continue
                dpath = self.root / 'runtime' / 'strategies' / 'drafts' / f'{did}.json'
                if dpath.exists():
                    try:
                        local = json.loads(dpath.read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        local = None
                    cleaned_draft, _ = _strip_secrets(draft)
                    if local is not None and _sha(local) == _sha(cleaned_draft):
                        unchanged.append({'section': section_id, 'draft_id': did, 'reason': '草案内容相同'})
                    else:
                        conflicts.append({
                            'section': section_id,
                            'code': 'draft_exists',
                            'draft_id': did,
                            'reason': f'草案 {did} 已存在且内容不同；导入须 overwrite_drafts 或 force',
                        })
                else:
                    applies.append({'section': section_id, 'action': 'restore_draft', 'draft_id': did})
        elif section_id == 'sim':
            cur, cur_h = self._gather_sim(include_ledger=False)
            incoming = data.get('config')
            if incoming is None:
                unchanged.append({'section': section_id, 'reason': '包内无 SIM config'})
            elif cur.get('config') == incoming:
                unchanged.append({'section': section_id, 'reason': 'SIM config 一致'})
            elif cur.get('config'):
                conflicts.append({
                    'section': section_id,
                    'code': 'sim_config_differs',
                    'reason': '本地 SIM 费用/分档预算与备份不同；拒绝静默覆盖（可 force）',
                })
            else:
                applies.append({'section': section_id, 'action': 'replace_sim_config'})
            if (data.get('ledger_meta') or {}).get('included') or data.get('ledger'):
                applies.append({
                    'section': section_id,
                    'action': 'note_ledger',
                    'reason': '包内含账本；默认导入仍跳过账本除非 import_sim_ledger=true',
                })
        elif section_id == 'eval_plans':
            cur, _ = self._gather_eval_plans()
            incoming = data.get('plans') or {}
            for pid, plan in incoming.items():
                if pid in (cur.get('plans') or {}) and (cur['plans'][pid] != plan):
                    conflicts.append({
                        'section': section_id,
                        'code': 'eval_plan_conflict',
                        'plan_id': pid,
                        'reason': f'EVAL 计划 {pid} 与本地不一致',
                    })
                elif pid in (cur.get('plans') or {}):
                    unchanged.append({'section': section_id, 'plan_id': pid})
                else:
                    applies.append({'section': section_id, 'action': 'add_eval_plan', 'plan_id': pid})
        elif section_id == 'review_plans':
            cur, _ = self._gather_review_plans()
            incoming = data.get('plans') or {}
            for pid, plan in incoming.items():
                if pid in (cur.get('plans') or {}) and (cur['plans'][pid] != plan):
                    conflicts.append({
                        'section': section_id,
                        'code': 'review_plan_conflict',
                        'plan_id': pid,
                        'reason': f'REVIEW 计划 {pid} 与本地不一致',
                    })
                elif pid in (cur.get('plans') or {}):
                    unchanged.append({'section': section_id, 'plan_id': pid})
                else:
                    applies.append({'section': section_id, 'action': 'add_review_plan', 'plan_id': pid})
            # pause switch: refuse silent flip under concurrency / divergent local
            if 'pause_new_entries' in data and data.get('pause_new_entries') is not None:
                local_pause = cur.get('pause_new_entries')
                incoming_pause = bool(data.get('pause_new_entries'))
                if local_pause is not None and bool(local_pause) != incoming_pause:
                    conflicts.append({
                        'section': section_id,
                        'code': 'pause_conflict',
                        'reason': (
                            f'本地 pause_new_entries={local_pause} 与备份 {incoming_pause} 不一致；'
                            '拒绝静默覆盖暂停开关（可 force）'
                        ),
                        'local': local_pause,
                        'package': incoming_pause,
                    })
                elif local_pause is not None and bool(local_pause) == incoming_pause:
                    unchanged.append({'section': section_id, 'reason': 'pause_new_entries 一致'})
                else:
                    applies.append({
                        'section': section_id,
                        'action': 'set_pause_new_entries',
                        'value': incoming_pause,
                    })
        return {'conflicts': conflicts, 'applies': applies, 'unchanged': unchanged}

    def backup_import(self, body):
        """Wizard step 4: apply package; refuse silent overwrite / tampered content."""
        if not isinstance(body, dict):
            raise ValueError('须提供备份包对象')
        if body.get('credentials_included'):
            raise ValueError('拒绝导入含凭证标记的包')
        force = bool(body.get('force'))
        allow_divergent = bool(body.get('allow_divergent'))
        import_sim_ledger = bool(body.get('import_sim_ledger'))
        overwrite_drafts = bool(body.get('overwrite_drafts') or force)
        cleaned, removed = _strip_secrets(body)
        package = cleaned.get('package') if isinstance(cleaned.get('package'), dict) else cleaned

        # Concurrency guards (caller may pass after review)
        expected_pkg_hash = body.get('expected_package_hash') or body.get('if_match_package_hash')
        expected_local_rev = body.get('expected_local_revision') or body.get('if_match_local_revision')

        applied = []
        skipped = []
        with self._lock:
            # Integrity MUST run under the same lock as apply (no TOCTOU skip)
            integrity = self._verify_backup_integrity(package)
            if expected_pkg_hash:
                actual_pkg = (package.get('manifest') or {}).get('package_hash')
                if actual_pkg != expected_pkg_hash:
                    raise ConfigConflict(
                        f'expected_package_hash 与包内 manifest.package_hash 不一致；'
                        '拒绝并发错包导入（HTTP 409）'
                    )
            if expected_local_rev is not None:
                cur_rev = int((self._read().get('meta') or {}).get('revision') or 0)
                try:
                    want = int(expected_local_rev)
                except (TypeError, ValueError) as exc:
                    raise ConfigConflict('expected_local_revision 无效（HTTP 409）') from exc
                if cur_rev != want:
                    raise ConfigConflict(
                        f'本地 revision={cur_rev} 与 expected_local_revision={want} 不一致；'
                        '并发修改后拒绝覆盖（HTTP 409）'
                    )

            # Recompute conflicts under lock (workspace may have changed)
            conflicts = []
            if package.get('schema') == BACKUP_SCHEMA:
                for sid, data in (package.get('sections') or {}).items():
                    conflicts.extend(self._review_section(sid, data)['conflicts'])
            else:
                conflicts.extend(self._review_section('config_core', {
                    'observation_pool': package.get('observation_pool'),
                    'prompt_edits': package.get('prompt_edits'),
                    'account_cost': package.get('account_cost'),
                    'source_content_hash': package.get('source_content_hash'),
                })['conflicts'])

            if conflicts and not (force or allow_divergent):
                raise ConfigConflict(
                    f'备份导入存在 {len(conflicts)} 处冲突，拒绝静默覆盖；'
                    '先调用 /api/config/backup/review 查看，或传 force=true / allow_divergent=true'
                )

            if package.get('schema') == BACKUP_SCHEMA:
                sections = package.get('sections') or {}
                if 'config_core' in sections:
                    applied.extend(self._import_config_core(sections['config_core'], force=force or allow_divergent))
                if 'strategies' in sections:
                    a, s = self._import_strategies(
                        sections['strategies'], overwrite_drafts=overwrite_drafts, force=force,
                    )
                    applied.extend(a)
                    skipped.extend(s)
                if 'sim' in sections:
                    a, s = self._import_sim(
                        sections['sim'], force=force or allow_divergent, import_ledger=import_sim_ledger,
                    )
                    applied.extend(a)
                    skipped.extend(s)
                if 'eval_plans' in sections:
                    applied.extend(self._import_eval_plans(sections['eval_plans'], force=force or allow_divergent))
                if 'review_plans' in sections:
                    applied.extend(self._import_review_plans(
                        sections['review_plans'], force=force or allow_divergent,
                    ))
            else:
                applied.extend(self._import_config_core({
                    'observation_pool': package.get('observation_pool'),
                    'prompt_edits': package.get('prompt_edits'),
                    'account_cost': package.get('account_cost'),
                }, force=True))

            state = self._read()
            return {
                'status': 'imported',
                'wizard_step': 'import',
                'revision': state['meta']['revision'],
                'content_hash': state['meta']['content_hash'],
                'applied': applied,
                'skipped': skipped,
                'conflicts_bypassed': len(conflicts) if (force or allow_divergent) else 0,
                'integrity': integrity,
                'secrets_stripped_paths': removed,
                'credentials_included': False,
                'sim_ledger_imported': import_sim_ledger,
                'disclaimer': DISCLAIMER,
            }

    def _import_config_core(self, data, force=False):
        state = self._read()
        applied = []
        if isinstance(data.get('observation_pool'), dict):
            state['observation_pool'] = data['observation_pool']
            applied.append('observation_pool')
        if isinstance(data.get('prompt_edits'), dict):
            state['prompt_edits'] = data['prompt_edits']
            applied.append('prompt_edits')
        if isinstance(data.get('account_cost'), dict):
            state['account_cost'] = data['account_cost']
            applied.append('account_cost')
        if applied:
            self._save(state)
        return applied

    def _import_strategies(self, data, overwrite_drafts=False, force=False):
        applied, skipped = [], []
        drafts_dir = self.root / 'runtime' / 'strategies' / 'drafts'
        versions_dir = self.root / 'runtime' / 'strategies' / 'versions'
        drafts_dir.mkdir(parents=True, exist_ok=True)
        versions_dir.mkdir(parents=True, exist_ok=True)
        for draft in data.get('drafts') or []:
            did = draft.get('draft_id')
            if not did:
                continue
            path = _safe_json_path(drafts_dir, did, kind='draft')
            if path.exists() and not overwrite_drafts:
                skipped.append({'draft_id': did, 'reason': '已存在且未 overwrite_drafts'})
                continue
            cleaned, _ = _strip_secrets(draft)
            atomic(path, encoded(cleaned))
            applied.append({'draft_id': did, 'action': 'restored_draft'})
        for ver in data.get('versions') or []:
            vid = ver.get('version_id')
            ph = ver.get('content_hash')
            if not vid:
                continue
            path = _safe_json_path(versions_dir, vid, kind='version')
            if path.exists():
                try:
                    local = json.loads(path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    local = {}
                if local.get('content_hash') == ph:
                    skipped.append({'version_id': vid, 'reason': 'hash 相同'})
                    continue
                if local.get('content_hash') and ph and local.get('content_hash') != ph and not force:
                    skipped.append({'version_id': vid, 'reason': 'hash 冲突已跳过（不可变）'})
                    continue
                if not force:
                    skipped.append({'version_id': vid, 'reason': '已存在，未 force'})
                    continue
            cleaned, _ = _strip_secrets(ver)
            atomic(path, encoded(cleaned))
            applied.append({'version_id': vid, 'action': 'restored_version'})
        return applied, skipped

    def _import_sim(self, data, force=False, import_ledger=False):
        applied, skipped = [], []
        cfg = data.get('config')
        if isinstance(cfg, dict):
            # Prefer live store save_config for validation
            safe = {k: cfg[k] for k in (
                'mode', 'fee_rate', 'slip_rate', 'max_leverage', 'max_positions_shared',
                'budget_t1_u', 'budget_t2_u', 'budget_t3_u',
                'risk_per_trade_pct', 'basket_open_risk_pct',
            ) if k in cfg}
            if self.sim_store is not None and hasattr(self.sim_store, 'save_config') and safe:
                try:
                    self.sim_store.save_config(safe)
                    applied.append({'action': 'sim_config_saved', 'keys': list(safe.keys())})
                except Exception as exc:
                    if not force:
                        raise
                    skipped.append({'action': 'sim_config', 'reason': str(exc)[:200]})
            elif safe:
                cfg_path = self.root / 'runtime' / 'sim' / 'config.json'
                cfg_path.parent.mkdir(parents=True, exist_ok=True)
                existing = {}
                if cfg_path.exists():
                    try:
                        existing = json.loads(cfg_path.read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        existing = {}
                existing.update(safe)
                atomic(cfg_path, encoded(existing))
                applied.append({'action': 'sim_config_file', 'keys': list(safe.keys())})
        if import_ledger and data.get('ledger') and isinstance(data['ledger'].get('events'), list):
            events_path = self.root / 'runtime' / 'sim' / 'events.jsonl'
            if events_path.exists() and not force:
                skipped.append({'action': 'sim_ledger', 'reason': '本地已有账本；未 force 跳过'})
            else:
                events_path.parent.mkdir(parents=True, exist_ok=True)
                lines = []
                for ev in data['ledger']['events']:
                    cleaned, _ = _strip_secrets(ev)
                    lines.append(json.dumps(cleaned, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
                events_path.write_text('\n'.join(lines) + ('\n' if lines else ''), encoding='utf-8')
                applied.append({'action': 'sim_ledger_restored', 'events': len(lines)})
        elif data.get('ledger') or (data.get('ledger_meta') or {}).get('included'):
            skipped.append({'action': 'sim_ledger', 'reason': '默认不导入账本；传 import_sim_ledger=true'})
        return applied, skipped

    def _import_eval_plans(self, data, force=False):
        applied = []
        plans = data.get('plans') or {}
        if not isinstance(plans, dict) or not plans:
            return applied
        state_path = self.root / 'runtime' / 'eval' / 'state.json'
        state_path.parent.mkdir(parents=True, exist_ok=True)
        if state_path.exists():
            st = json.loads(state_path.read_text(encoding='utf-8'))
        else:
            st = {'plans': {}, 'observation_count': 0, 'settlement_count': 0, 'open_ids': []}
        local_plans = st.setdefault('plans', {})
        for pid, plan in plans.items():
            if pid in local_plans and local_plans[pid] != plan and not force:
                continue
            cleaned, _ = _strip_secrets(plan)
            local_plans[pid] = cleaned
            applied.append({'plan_id': pid, 'action': 'eval_plan_upsert'})
        st['plans'] = local_plans
        atomic(state_path, encoded(st))
        return applied

    def _import_review_plans(self, data, force=False):
        applied = []
        plans = data.get('plans') or {}
        state_path = self.root / 'runtime' / 'reviews' / 'state.json'
        state_path.parent.mkdir(parents=True, exist_ok=True)
        if state_path.exists():
            st = json.loads(state_path.read_text(encoding='utf-8'))
        else:
            st = {'plans': {}}
        local_plans = st.setdefault('plans', {})
        for pid, plan in (plans if isinstance(plans, dict) else {}).items():
            if pid in local_plans and local_plans[pid] != plan and not force:
                continue
            cleaned, _ = _strip_secrets(plan)
            local_plans[pid] = cleaned
            applied.append({'plan_id': pid, 'action': 'review_plan_upsert'})
        if 'pause_new_entries' in data and data['pause_new_entries'] is not None:
            incoming_pause = bool(data['pause_new_entries'])
            local_pause = st.get('pause_new_entries')
            if local_pause is not None and bool(local_pause) != incoming_pause and not force:
                # Mirror review pause_conflict — never silent flip
                raise ConfigConflict(
                    f'pause_new_entries 本地={local_pause} 备份={incoming_pause}；'
                    '拒绝静默覆盖（HTTP 409）'
                )
            st['pause_new_entries'] = incoming_pause
            applied.append({'action': 'pause_new_entries', 'value': st['pause_new_entries']})
        st['plans'] = local_plans
        atomic(state_path, encoded(st))
        return applied

    def settings_bind_snapshot(self, extras=None):
        """Aggregate settings-page bind payload (no credentials / no AI keys).

        extras: optional dict with already-fetched non-secret slices from server:
          scan_settings, ai_public, sim_config, eval_plans, review_plans
        Bound stores used as fallbacks when extras omit a slice.
        """
        extras = extras if isinstance(extras, dict) else {}
        core = self.get()
        core, _ = _strip_secrets(core)

        scan = extras.get('scan_settings')
        ai_public = extras.get('ai_public')
        sim_cfg = extras.get('sim_config')
        eval_plans = extras.get('eval_plans')
        review_plans = extras.get('review_plans')

        if sim_cfg is None and self.sim_store is not None and hasattr(self.sim_store, 'get_config'):
            try:
                sim_cfg = self.sim_store.get_config()
            except Exception:
                sim_cfg = None
        if isinstance(sim_cfg, dict):
            sim_cfg, _ = _strip_secrets(sim_cfg)

        if eval_plans is None and self.eval_store is not None and hasattr(self.eval_store, 'list_plans'):
            try:
                eval_plans = self.eval_store.list_plans()
            except Exception:
                eval_plans = None
        if isinstance(eval_plans, dict):
            eval_plans, _ = _strip_secrets(eval_plans)

        if review_plans is None and self.review_store is not None and hasattr(self.review_store, 'list_plans'):
            try:
                review_plans = self.review_store.list_plans()
            except Exception:
                review_plans = None
        if isinstance(review_plans, dict):
            review_plans, _ = _strip_secrets(review_plans)

        # AI: only non-secret public fields; never raw keys
        if isinstance(ai_public, dict):
            ai_public, _ = _strip_secrets(ai_public)
            for bad in ('api_key', 'apiKey', 'key', 'secret', 'token', 'authorization'):
                ai_public.pop(bad, None)

        fields = [
            {'id': 'observation_pool', 'api': 'PUT /api/config/observation-pool', 'conflict': 'content_hash'},
            {'id': 'prompt_edits', 'api': 'PUT /api/config/prompt-edits', 'conflict': 'expected_content_hash'},
            {'id': 'scan_schedule', 'api': 'PUT /api/settings', 'conflict': 'version'},
            {'id': 'ai_public', 'api': 'PUT /api/ai/config', 'conflict': 'config version/keyAction', 'keys': 'never_returned'},
            {'id': 'sim_costs_budgets', 'api': 'PUT /api/sim/config', 'conflict': 'expected_updated_at'},
            {'id': 'eval_plan_toggles', 'api': 'POST /api/eval/plans/.../enable + PUT /api/eval/plans', 'conflict': '409 on concurrent'},
            {'id': 'review_plan_toggles', 'api': 'POST /api/review/plans/.../enable', 'conflict': '409 on concurrent'},
            {'id': 'backup_wizard', 'api': 'POST /api/config/backup/*', 'conflict': 'hash/fork refuse silent'},
        ]
        return {
            'schema': 'settings-bind-v1',
            'slice': 'CONFIG-01-settings-bind',
            'loaded_at': _utcnow(),
            'config': core,
            'scan': scan,
            'ai': ai_public,
            'sim': sim_cfg,
            'eval_plans': eval_plans,
            'review_plans': review_plans,
            'bind': {
                'ready': True,
                'bidirectional': True,
                'load_on_open': True,
                'conflict_http': 409,
                'silent_overwrite': False,
                'credentials_surfaced': False,
                'fields': fields,
            },
            'disclaimer': DISCLAIMER,
        }

    def catalog(self):
        st = self.get()
        return {
            'schema': 'config-catalog-v1',
            'slice': 'CONFIG-01-settings-bind',
            'persists': ['observation_pool', 'prompt_edits', 'account_cost'],
            'backup_sections': list(BACKUP_SECTIONS),
            'backup_schema': BACKUP_SCHEMA,
            'wizard': {
                'ready': True,
                'steps': ['manifest', 'export', 'review', 'import'],
                'apis': [
                    'POST /api/config/backup/manifest',
                    'POST /api/config/backup/export',
                    'POST /api/config/backup/review',
                    'POST /api/config/backup/import',
                ],
                'include_sim_ledger_default': False,
                'credentials_always_excluded': True,
                'integrity': 'manifest+payload_hash',
                'path_escape_refused': True,
                'concurrency_guards': ['expected_package_hash', 'expected_local_revision'],
            },
            'settings_bind': {
                'ready': True,
                'api': 'GET /api/config/settings-bind',
                'bidirectional': True,
                'conflict_http': 409,
                'credentials_surfaced': False,
            },
            'export_excludes_credentials': True,
            'storage': 'runtime/config/state.json',
            'backups_dir': 'runtime/config/backups/',
            'cache_note': '普通缓存/临时目录清理不应删除 runtime/config/',
            'revision': st['meta'].get('revision'),
            'content_hash': st['meta'].get('content_hash'),
            'disclaimer': DISCLAIMER,
        }
