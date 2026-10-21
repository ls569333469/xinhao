"""RULE-01: strategy drafts + frozen immutable versions. No SIM/EVAL execution."""
from __future__ import annotations

from durable_io import protected
import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from snapshot_store import atomic, encoded
from strategy_registry import PATH as REGISTRY_PATH, REGISTRY, VERSION_HASH

DRAFT_ID_RE = re.compile(r'^draft-[a-f0-9]{12}$')
VERSION_ID_RE = re.compile(r'^ver-[a-f0-9]{12}$')
STRATEGY_ID_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')

# Declared dependency keys. Only price_* / snapshot can be soft-satisfied by DATA-01 today.
KNOWN_DEPENDENCIES = (
    'price_4h',
    'price_1d',
    'snapshot',
    'oi',
    'account_ratio',
    'funding',
    'ema',
    'netflow',  # Legacy exchange-flow dependency; not an alias for smart_money.
    'smart_money',
    'volume_price',
    'structure_ohlcv',
    'ath',
)

EXECUTION_NOTE = (
    '白名单触发/退出字段已持久化；SIM-01 纸面账户/事件账本已可手动开平仓引用版本，'
    '模拟撮合与前向观察需冻结执行参数及行情依赖检查；启用不代表自动下单或实盘。'
)


def _utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_id(prefix):
    return f'{prefix}-{uuid.uuid4().hex[:12]}'


def _canonical(value):
    """Stable JSON for hashing: sort keys, no NaN, compact."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def content_hash(rules, data_dependencies, meta=None):
    payload = {
        'rules': rules,
        'data_dependencies': list(data_dependencies or []),
        'meta': meta or {},
    }
    return hashlib.sha256(_canonical(payload).encode('utf-8')).hexdigest()


def _normalize_deps(deps):
    if deps is None:
        return []
    if not isinstance(deps, list):
        raise ValueError('data_dependencies 须为字符串列表')
    out = []
    for d in deps:
        if not isinstance(d, str) or not d.strip():
            raise ValueError('依赖名须为非空字符串')
        name = d.strip()
        if name not in KNOWN_DEPENDENCIES:
            raise ValueError(f'未知数据依赖: {name}；可选: {", ".join(KNOWN_DEPENDENCIES)}')
        if name not in out:
            out.append(name)
    return out


def _normalize_whitelist(block, label):
    """Persist trigger/exit whitelist structure even without an execution engine."""
    if block is None:
        return {
            'mode': 'unset',
            'symbols': [],
            'note': f'{label}尚未配置；结构已预留，执行引擎未接线',
        }
    if not isinstance(block, dict):
        raise ValueError(f'{label}须为对象')
    mode = block.get('mode') or 'unset'
    if mode not in ('unset', 'allow', 'deny', 'disabled'):
        raise ValueError(f'{label}.mode 仅支持 unset/allow/deny/disabled')
    symbols = block.get('symbols') or []
    if not isinstance(symbols, list) or not all(isinstance(s, str) and s.strip() for s in symbols):
        raise ValueError(f'{label}.symbols 须为非空字符串列表')
    return {
        'mode': mode,
        'symbols': [s.strip().upper() for s in symbols],
        'rules_text': (block.get('rules_text') or block.get('text') or '')[:2000],
        'note': (block.get('note') or '')[:500] or (
            '结构已保存；未绑定白名单自动执行，不会按白名单自动触发或退出'
        ),
    }


def _normalize_rules(rules):
    if rules is None:
        rules = {}
    if not isinstance(rules, dict):
        raise ValueError('rules 须为对象')
    out = {
        'universe': rules.get('universe') if isinstance(rules.get('universe'), dict) else {
            'market': 'USDⓈ-M',
            'interval': (rules.get('interval') or '4h'),
            'pool': rules.get('pool') or 'current_filter',
        },
        'candidate': rules.get('candidate') if isinstance(rules.get('candidate'), (dict, str)) else {},
        'params': rules.get('params') if isinstance(rules.get('params'), dict) else {},
        'whitelist_trigger': _normalize_whitelist(rules.get('whitelist_trigger'), 'whitelist_trigger'),
        'whitelist_exit': _normalize_whitelist(rules.get('whitelist_exit'), 'whitelist_exit'),
        'entry_text': (rules.get('entry_text') or rules.get('trigger') or rules.get('entry') or '')[:2000],
        'exit_text': (rules.get('exit_text') or rules.get('exit') or '')[:2000],
        'idea': (rules.get('idea') or rules.get('notes') or '')[:4000],
        'base_template_id': rules.get('base_template_id'),
    }
    # UI02：模板身份 / 编辑模式 / 未完成声明随草案与冻结包保存（不参与假装 executable）
    for key in ('registry_version', 'registry_hash', 'editor_mode'):
        if rules.get(key) not in (None, ''):
            out[key] = str(rules.get(key))[:200]
    if isinstance(rules.get('implementation_status'), dict):
        st = dict(rules['implementation_status'])
        st['executable'] = False  # 禁止纯文本抬成可执行
        if 'unfinished' in st and not isinstance(st['unfinished'], list):
            st['unfinished'] = [str(st['unfinished'])]
        out['implementation_status'] = st
    # Optional provenance participates in rule hashes and survives freeze/export.
    # Omission keeps existing frozen hashes unchanged.
    if 'source_report_version' in rules:
        if type(rules['source_report_version']) is not int or rules['source_report_version'] != 0:
            raise ValueError('仅支持已复核的原始报告版本0')
        out['source_report_version'] = 0
    return out


class StrategyStore:
    """JSON store under runtime/strategies: mutable drafts + immutable frozen versions."""

    def __init__(self, root):
        self.root = Path(root)
        self.dir = self.root / 'runtime' / 'strategies'
        self.drafts_dir = self.dir / 'drafts'
        self.versions_dir = self.dir / 'versions'
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def registry_ref(self):
        return {
            'version': REGISTRY['version'],
            'hash': VERSION_HASH,
            'path': REGISTRY_PATH.name,
            'strategy_ids': [s['id'] for s in REGISTRY['strategies']],
            'indicator_ids': [i['id'] for i in REGISTRY['indicators']],
        }

    def _draft_path(self, draft_id):
        return self.drafts_dir / f'{draft_id}.json'

    def _version_path(self, version_id):
        return self.versions_dir / f'{version_id}.json'

    def _read_json(self, path):
        return json.loads(path.read_text(encoding='utf-8'))

    def _write_json(self, path, value):
        atomic(path, encoded(value))

    def list_all(self):
        with self._lock:
            drafts = []
            for path in sorted(self.drafts_dir.glob('draft-*.json')):
                try:
                    drafts.append(self._public_draft(self._read_json(path)))
                except (OSError, ValueError, KeyError):
                    continue
            versions = []
            for path in sorted(self.versions_dir.glob('ver-*.json')):
                try:
                    versions.append(self._public_version(self._read_json(path)))
                except (OSError, ValueError, KeyError):
                    continue
            # newest activity first
            drafts.sort(key=lambda d: d.get('updated_at') or '', reverse=True)
            versions.sort(key=lambda v: v.get('frozen_at') or '', reverse=True)
            return {
                'drafts': drafts,
                'versions': versions,
                'registry': self.registry_ref(),
                'execution_note': EXECUTION_NOTE,
                'known_dependencies': list(KNOWN_DEPENDENCIES),
            }

    def get_draft(self, draft_id):
        with self._lock:
            path = self._draft_path(draft_id)
            if not path.exists():
                raise KeyError('草案不存在')
            return self._public_draft(self._read_json(path))

    def get_version(self, version_id):
        with self._lock:
            path = self._version_path(version_id)
            if not path.exists():
                raise KeyError('冻结版本不存在')
            return self._public_version(self._read_json(path))

    def find_by_strategy(self, strategy_id, version=None):
        """Lookup frozen version by strategy_id (+ optional version string)."""
        with self._lock:
            matches = []
            for path in self.versions_dir.glob('ver-*.json'):
                try:
                    row = self._read_json(path)
                except (OSError, ValueError):
                    continue
                if row.get('strategy_id') != strategy_id:
                    continue
                if version is not None and row.get('version') != version:
                    continue
                matches.append(self._public_version(row))
            matches.sort(key=lambda v: v.get('frozen_at') or '', reverse=True)
            return matches

    def _check_report_source(self, report_id, rules):
        research = getattr(self, 'research', None)
        if report_id and research is not None:
            research.assert_strategy_eligible(research.get_report(report_id), rules.get('source_report_version', 0))
            rules['source_report_version'] = 0

    @protected
    def create_draft(self, body):
        with self._lock:
            if not isinstance(body, dict):
                raise ValueError('请求体须为对象')
            title = (body.get('title') or '').strip()
            if not title:
                raise ValueError('须提供策略名称 title')
            strategy_id = (body.get('strategy_id') or '').strip() or _new_id('S')
            # normalize S prefix style
            if not STRATEGY_ID_RE.fullmatch(strategy_id):
                raise ValueError('strategy_id 格式无效')
            rules = _normalize_rules(body.get('rules'))
            self._check_report_source(body.get('source_report_id'), rules)
            if body.get('base_template_id'):
                rules['base_template_id'] = str(body['base_template_id'])[:16]
            deps = _normalize_deps(body.get('data_dependencies'))
            if not deps:
                # default soft deps for price-based work; OI etc. must be declared explicitly
                deps = ['price_4h', 'snapshot']
            draft_id = _new_id('draft')
            now = _utcnow()
            meta = {
                'title': title,
                'strategy_id': strategy_id,
                'source_report_id': body.get('source_report_id'),
                'source_excerpt': (body.get('source_excerpt') or '')[:1500] or None,
            }
            draft = {
                'draft_id': draft_id,
                'strategy_id': strategy_id,
                'title': title,
                'status': 'draft',
                'rules': rules,
                'data_dependencies': deps,
                'registry_ref': self.registry_ref(),
                'source_report_id': body.get('source_report_id'),
                'source_excerpt': (body.get('source_excerpt') or '')[:1500] or None,
                'parent_version_id': body.get('parent_version_id'),
                'content_hash': content_hash(rules, deps, meta),
                'created_at': now,
                'updated_at': now,
                'executable': False,
                'note': '草案可改；冻结后生成不可变版本。保存草案不会启用模拟或前向观察。',
            }
            self._write_json(self._draft_path(draft_id), draft)
            return self._public_draft(draft)

    @protected
    def update_draft(self, draft_id, body):
        with self._lock:
            path = self._draft_path(draft_id)
            if not path.exists():
                raise KeyError('草案不存在')
            if not isinstance(body, dict):
                raise ValueError('请求体须为对象')
            draft = self._read_json(path)
            if draft.get('status') != 'draft':
                raise ValueError('仅可修改草案状态的记录；冻结规则请基于版本另开新草案')
            if 'title' in body:
                title = (body.get('title') or '').strip()
                if not title:
                    raise ValueError('title 不能为空')
                draft['title'] = title
            if 'rules' in body:
                draft['rules'] = _normalize_rules(body.get('rules'))
            if 'data_dependencies' in body:
                draft['data_dependencies'] = _normalize_deps(body.get('data_dependencies'))
            if 'source_report_id' in body:
                draft['source_report_id'] = body.get('source_report_id')
            if 'source_excerpt' in body:
                draft['source_excerpt'] = (body.get('source_excerpt') or '')[:1500] or None
            self._check_report_source(draft.get('source_report_id'), draft['rules'])
            meta = {
                'title': draft['title'],
                'strategy_id': draft['strategy_id'],
                'source_report_id': draft.get('source_report_id'),
                'source_excerpt': draft.get('source_excerpt'),
            }
            draft['content_hash'] = content_hash(draft['rules'], draft['data_dependencies'], meta)
            draft['registry_ref'] = self.registry_ref()
            draft['updated_at'] = _utcnow()
            self._write_json(path, draft)
            return self._public_draft(draft)

    @protected
    def create_draft_from_report(self, body, research=None):
        """Create a draft from AI report draft excerpt (read-only extract → editable draft)."""
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')
        report_id = body.get('report_id')
        excerpt = body.get('excerpt') or body.get('source_excerpt')
        title = (body.get('title') or '').strip()
        entry = body.get('entry') or body.get('入场')
        exit_ = body.get('exit') or body.get('退出')
        params = body.get('params') or body.get('参数')
        # Check even when an excerpt is supplied: client text must not bypass report quality.
        if report_id:
            if research is None:
                raise ValueError('无法核验来源报告，拒绝转策略草案')
            report = research.get_report(report_id)
            if hasattr(research, 'assert_strategy_eligible'):
                research.assert_strategy_eligible(report, body.get('report_version', 0))
            elif (report.get('checks') or {}).get('automatic', {}).get('status') == 'failed':
                raise ValueError('来源报告未通过校验，不能转策略草案')
        if report_id and research is not None and not excerpt:
            try:
                report = research.get_report(report_id)
            except KeyError as exc:
                raise KeyError('报告不存在') from exc
            drafts = report.get('drafts') or []
            pick = None
            want = body.get('draft_id')
            if want:
                pick = next((d for d in drafts if d.get('draft_id') == want), None)
            elif drafts:
                pick = drafts[0]
            if pick:
                title = title or pick.get('title') or '报告策略草案'
                entry = entry or pick.get('entry')
                exit_ = exit_ or pick.get('exit')
                params = params or pick.get('params')
                excerpt = pick.get('excerpt') or excerpt
                report_id = pick.get('source_report_id') or report_id
        if not title:
            title = '报告策略草案'
        rules = {
            'entry_text': entry or '',
            'exit_text': exit_ or '',
            'idea': excerpt or '',
            'params': params if isinstance(params, dict) else {},
            'whitelist_trigger': body.get('whitelist_trigger'),
            'whitelist_exit': body.get('whitelist_exit'),
            'universe': body.get('universe') if isinstance(body.get('universe'), dict) else {},
        }
        if report_id:
            rules['source_report_version'] = 0
        return self.create_draft({
            'title': title,
            'strategy_id': body.get('strategy_id'),
            'rules': rules,
            'data_dependencies': body.get('data_dependencies'),
            'source_report_id': report_id,
            'source_excerpt': (excerpt or '')[:1500] or None,
        })

    @protected
    def create_draft_from_version(self, version_id, body=None):
        """Editing a frozen rule = new draft cloned from the immutable version."""
        body = body or {}
        with self._lock:
            ver = self.get_version(version_id)
            payload = {
                'title': body.get('title') or (ver['title'] + '（改版草案）'),
                'strategy_id': ver['strategy_id'],
                'rules': ver['rules'],
                'data_dependencies': body.get('data_dependencies') or ver['data_dependencies'],
                'parent_version_id': version_id,
                'source_report_id': ver.get('source_report_id'),
            }
            return self.create_draft(payload)

    @protected
    def freeze(self, draft_id, body=None):
        """Freeze draft → new immutable version. Never mutates an existing frozen file."""
        body = body or {}
        with self._lock:
            path = self._draft_path(draft_id)
            if not path.exists():
                raise KeyError('草案不存在')
            draft = self._read_json(path)
            version_label = (body.get('version') or '').strip()
            if not version_label:
                # auto bump: count existing versions for this strategy_id
                existing = self.find_by_strategy(draft['strategy_id'])
                version_label = f"1.{len(existing)}.0"
            if not re.fullmatch(r'[A-Za-z0-9._-]{1,32}', version_label):
                raise ValueError('version 标签格式无效')
            # refuse duplicate strategy_id+version
            for v in self.find_by_strategy(draft['strategy_id'], version_label):
                raise ValueError(f'已存在冻结版本 {draft["strategy_id"]}@{version_label}（{v["version_id"]}）；请换版本号')
            change_note = (body.get('change_note') or body.get('变更说明') or '')[:1000]
            meta = {
                'title': draft['title'],
                'strategy_id': draft['strategy_id'],
                'version': version_label,
                'source_report_id': draft.get('source_report_id'),
            }
            chash = content_hash(draft['rules'], draft['data_dependencies'], meta)
            # RULE-01 hard gate: refuse freeze when declared deps not covered
            gate = self.check_dependencies(draft.get('data_dependencies') or [])
            if not gate.get('ok'):
                raise ValueError(
                    '覆盖硬闸拒绝冻结: 缺 '
                    + ', '.join(gate.get('missing_dependencies') or [])
                    + '（须 coverage covered；不宣称 ATH/六指标齐全）'
                )
            version_id = _new_id('ver')
            now = _utcnow()
            version = {
                'version_id': version_id,
                'strategy_id': draft['strategy_id'],
                'version': version_label,
                'title': draft['title'],
                'content_hash': chash,
                'rules': draft['rules'],  # frozen copy
                'data_dependencies': list(draft['data_dependencies']),
                'registry_ref': draft.get('registry_ref') or self.registry_ref(),
                'from_draft_id': draft_id,
                'parent_version_id': draft.get('parent_version_id'),
                'source_report_id': draft.get('source_report_id'),
                'change_note': change_note,
                'frozen_at': now,
                'enabled': False,
                'enabled_at': None,
                'missing_dependencies': [],
                'enable_block_reason': None,
                'immutable': True,
                'executable': False,
                'execution_note': EXECUTION_NOTE,
            }
            # RULE-01 deepen: freeze carries strategy hash + coverage fingerprint
            try:
                from rule_export_gate import capture_coverage_slice, joint_from_version
                cov = capture_coverage_slice(self.root)
                if not cov.get('ok'):
                    raise ValueError(
                        '冻结硬闸拒绝：无法采集 coverage_fingerprint（'
                        + (cov.get('reason') or '')
                        + '）'
                    )
                version['rule_fingerprint'] = joint_from_version(version, cov, surface='freeze')
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(f'冻结硬闸拒绝：指纹采集失败: {exc}') from exc
            # write version first (immutable); draft stays editable for further experiments
            # but mark last_frozen pointer on draft
            self._write_json(self._version_path(version_id), version)
            draft['last_frozen_version_id'] = version_id
            draft['last_frozen_at'] = now
            draft['updated_at'] = now
            self._write_json(path, draft)
            return self._public_version(version)

    def check_dependencies(self, data_dependencies):
        """Hard coverage gate via coverage_gate field statuses (RULE-01 deepen).

        Declared deps map to coverage_gate fields; require status=covered.
        partial/unknown/unmapped → missing. Never claims ATH/六指标/历史齐全.
        """
        try:
            from rule_coverage_gate import assert_deps_covered
        except Exception as exc:  # pragma: no cover
            deps = list(data_dependencies or [])
            return {
                'ok': False,
                'missing_dependencies': deps,
                'details': {'error': f'覆盖硬闸不可用: {exc}'},
            }
        gate = assert_deps_covered(self.root, data_dependencies, require_status='covered')
        return gate

    def set_enabled(self, version_id, enabled, body=None):
        body = body if isinstance(body, dict) else {}
        with self._lock:
            path = self._version_path(version_id)
            if not path.exists():
                raise KeyError('冻结版本不存在')
            version = self._read_json(path)
            if not version.get('immutable'):
                raise ValueError('只能启停已冻结版本')
            if enabled:
                # RULE-01 deepen: refuse stale/mismatched export fingerprints
                try:
                    from rule_export_gate import verify_enable_or_import
                    from rule_bindings import BindingConflict
                    if not version.get('rule_fingerprint') and not body.get('acknowledge_coverage_drift'):
                        raise BindingConflict(
                            '启用硬闸拒绝：冻结版本缺少 rule_fingerprint（coverage+strategy hash）；'
                            '请重新冻结后再启用，或显式 acknowledge_coverage_drift=true'
                        )
                    fp_gate = verify_enable_or_import(
                        self.root, version, body, for_enable=True,
                    )
                except BindingConflict as exc:
                    version['enabled'] = False
                    version['enable_block_reason'] = str(exc)
                    self._write_json(path, version)
                    pub = self._public_version(version)
                    pub['enable_refused'] = True
                    pub['fingerprint_refusal'] = str(exc)
                    return pub
                gate = self.check_dependencies(version.get('data_dependencies') or [])
                if not gate['ok']:
                    version['enabled'] = False
                    version['missing_dependencies'] = gate['missing_dependencies']
                    version['enable_block_reason'] = (
                        '覆盖硬闸禁止启用: ' + ', '.join(gate['missing_dependencies'])
                        + '（须 covered；partial/unknown 拒绝）'
                    )
                    version['dependency_check'] = gate
                    self._write_json(path, version)
                    pub = self._public_version(version)
                    pub['enable_refused'] = True
                    pub['dependency_check'] = gate
                    return pub
                version['enabled'] = True
                version['enabled_at'] = _utcnow()
                version['missing_dependencies'] = []
                version['enable_block_reason'] = None
                version['dependency_check'] = gate
                version['last_enable_fingerprint'] = fp_gate.get('live_joint')
            else:
                version['enabled'] = False
                version['enabled_at'] = None
                version['enable_block_reason'] = None
            self._write_json(path, version)
            return self._public_version(version)

    def _public_draft(self, draft):
        return {
            'draft_id': draft['draft_id'],
            'strategy_id': draft['strategy_id'],
            'title': draft['title'],
            'status': draft.get('status', 'draft'),
            'version': None,
            'content_hash': draft['content_hash'],
            'rules': draft['rules'],
            'data_dependencies': draft.get('data_dependencies') or [],
            'registry_ref': draft.get('registry_ref'),
            'source_report_id': draft.get('source_report_id'),
            'source_excerpt': draft.get('source_excerpt'),
            'parent_version_id': draft.get('parent_version_id'),
            'last_frozen_version_id': draft.get('last_frozen_version_id'),
            'last_frozen_at': draft.get('last_frozen_at'),
            'created_at': draft.get('created_at'),
            'updated_at': draft.get('updated_at'),
            'executable': False,
            'note': draft.get('note') or EXECUTION_NOTE,
        }

    def _public_version(self, version):
        return {
            'version_id': version['version_id'],
            'strategy_id': version['strategy_id'],
            'version': version['version'],
            'title': version['title'],
            'content_hash': version['content_hash'],
            'id_version_hash': f"{version['strategy_id']}@{version['version']}#{version['content_hash'][:12]}",
            'rules': version['rules'],
            'data_dependencies': version.get('data_dependencies') or [],
            'registry_ref': version.get('registry_ref'),
            'from_draft_id': version.get('from_draft_id'),
            'parent_version_id': version.get('parent_version_id'),
            'source_report_id': version.get('source_report_id'),
            'change_note': version.get('change_note'),
            'frozen_at': version.get('frozen_at'),
            'enabled': bool(version.get('enabled')),
            'enabled_at': version.get('enabled_at'),
            'missing_dependencies': version.get('missing_dependencies') or [],
            'enable_block_reason': version.get('enable_block_reason'),
            'immutable': True,
            'executable': False,
            'execution_note': version.get('execution_note') or EXECUTION_NOTE,
            'rule_fingerprint': version.get('rule_fingerprint'),
            'last_enable_fingerprint': version.get('last_enable_fingerprint'),
        }


    def export_version(self, version_id):
        """Export frozen version with joint strategy+coverage fingerprint."""
        with self._lock:
            path = self._version_path(version_id)
            if not path.exists():
                raise KeyError('冻结版本不存在')
            version = self._read_json(path)
        from rule_export_gate import capture_coverage_slice, joint_from_version, JOINT_SCHEMA
        cov = capture_coverage_slice(self.root)
        if not cov.get('ok'):
            raise ValueError('导出拒绝：无法采集 coverage_fingerprint：' + (cov.get('reason') or ''))
        joint = joint_from_version(version, cov, surface='export')
        pub = self._public_version(version)
        return {
            'schema': 'strategy-export-v1',
            'exported_at': _utcnow(),
            'version': pub,
            'rule_fingerprint': joint,
            'joint_fingerprint': joint,
            'coverage_fingerprint': joint.get('coverage_fingerprint'),
            'content_hash': pub.get('content_hash'),
            'joint_hash': joint.get('joint_hash'),
            'fingerprint_schema': JOINT_SCHEMA,
            'note': '导入/启用须核验 content_hash + coverage_fingerprint；陈旧指纹→409',
            'disclaimer': '研究用策略导出；不自动下单；不宣称六指标/ATH 齐全',
        }

    @protected
    def import_version(self, body):
        with self._lock:
            return self._import_version_locked(body)

    def _import_version_locked(self, body):
        """Import a previously exported version package; refuse fingerprint mismatch.

        P1 RULE-IMPORT-HASH: recompute content_hash from actual rules+deps+meta;
        declared hashes alone are never trusted. Tampered rules with stale hash → 409.
        """
        from rule_bindings import BindingConflict
        from rule_export_gate import verify_enable_or_import
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')
        ver_in = body.get('version') or body.get('package') or body
        if not isinstance(ver_in, dict):
            raise ValueError('须提供 version 对象')
        # Prefer nested version from export_version
        if 'version' in body and isinstance(body['version'], dict):
            ver_in = body['version']
        required = ('strategy_id', 'version', 'content_hash', 'rules')
        missing = [k for k in required if not ver_in.get(k)]
        if missing:
            raise ValueError('导入缺少字段: ' + ', '.join(missing))

        rules = ver_in['rules']
        if not isinstance(rules, dict):
            raise ValueError('version.rules 须为对象')
        deps = list(ver_in.get('data_dependencies') or [])
        # Same meta shape as freeze() so recomputed hash matches honest packages
        meta = {
            'title': ver_in.get('title') or ver_in['strategy_id'],
            'strategy_id': ver_in['strategy_id'],
            'version': ver_in['version'],
            'source_report_id': ver_in.get('source_report_id'),
        }
        recomputed = content_hash(rules, deps, meta)
        declared = str(ver_in.get('content_hash') or '')
        source_content_hash = declared

        fp_blob = (
            body.get('rule_fingerprint')
            or body.get('joint_fingerprint')
            or ver_in.get('rule_fingerprint')
            or {}
        )
        if not isinstance(fp_blob, dict):
            fp_blob = {}
        pkg_hash = (
            fp_blob.get('content_hash')
            or body.get('content_hash')
            or declared
        )

        # Hard gate: actual rules must hash to declared content_hash
        if recomputed != declared:
            raise BindingConflict(
                '导入拒绝：规则内容与声明 content_hash 不一致 '
                f'(recomputed={recomputed[:16]}… ≠ declared={declared[:16]}…)；'
                f'禁止篡改 rules 后沿用旧指纹（RULE-IMPORT-HASH）'
            )
        if pkg_hash and str(pkg_hash) != recomputed:
            raise BindingConflict(
                '导入拒绝：rule_fingerprint.content_hash 与规则重算哈希不一致 '
                f'(pkg={str(pkg_hash)[:16]}… ≠ recomputed={recomputed[:16]}…)；'
                f'禁止 fingerprint_verified 放过篡改包'
            )

        probe = {
            'version_id': ver_in.get('version_id') or 'ver-import',
            'strategy_id': ver_in['strategy_id'],
            'version': ver_in['version'],
            'content_hash': recomputed,
            'registry_ref': ver_in.get('registry_ref'),
            'data_dependencies': deps,
            'rule_fingerprint': fp_blob or None,
            'immutable': True,
        }
        expected = {
            'expected_content_hash': recomputed,
            'expected_coverage_fingerprint': (
                body.get('expected_coverage_fingerprint')
                or body.get('coverage_fingerprint')
                or fp_blob.get('coverage_fingerprint')
            ),
            'expected_joint_hash': body.get('expected_joint_hash'),
            'acknowledge_coverage_drift': bool(body.get('acknowledge_coverage_drift')),
        }
        if not expected['expected_coverage_fingerprint']:
            raise BindingConflict(
                '导入拒绝：导出包缺少 coverage_fingerprint；'
                '须携带 rule-export-fingerprint-v2 联合指纹（禁止无覆盖指纹导入）'
            )
        try:
            verify_enable_or_import(self.root, probe, expected, for_enable=False)
        except BindingConflict:
            raise

        # Create as new frozen version via draft→freeze path to keep immutability discipline
        draft = self.create_draft({
            'strategy_id': ver_in['strategy_id'],
            'title': ver_in.get('title') or ver_in['strategy_id'],
            'data_dependencies': deps,
            'rules': rules,
            'source_report_id': ver_in.get('source_report_id'),
        })
        label = ver_in['version']
        relabeled = False
        try:
            frozen = self.freeze(draft['draft_id'], {
                'version': label,
                'change_note': (ver_in.get('change_note') or '') + ' [import]',
            })
        except Exception as exc:
            try:
                if isinstance(exc, ValueError) and '已存在冻结版本' in str(exc):
                    frozen = self.freeze(draft['draft_id'], {
                        'version': f"{label}-imp",
                        'change_note': (ver_in.get('change_note') or '') + ' [import-relabel]',
                    })
                    relabeled = True
                else:
                    raise
            except Exception:
                # Only remove the unpublished draft created by this import attempt.
                # Never remove a draft already referenced by a published version.
                if not any(v.get('from_draft_id') == draft['draft_id'] for v in self.find_by_strategy(draft['strategy_id'])):
                    self._draft_path(draft['draft_id']).unlink(missing_ok=True)
                raise
        result_hash = frozen.get('content_hash')
        return {
            'status': 'imported',
            'version': frozen,
            'fingerprint_verified': True,
            'source_content_hash': source_content_hash,
            'result_content_hash': result_hash,
            'content_hash_unchanged': source_content_hash == result_hash,
            'relabeled': relabeled,
            'note': (
                '已重算并核验 rules→content_hash 与 coverage_fingerprint 后写入新冻结版本'
                + (f'；改名后 source={source_content_hash[:12]}… result={str(result_hash)[:12]}…'
                   if source_content_hash != result_hash else '')
            ),
        }


# Module-level helper used by tests without constructing thrice
def mutate_frozen_file_forbidden(store, version_id, patch):
    """Test helper documenting that callers must not rewrite frozen payloads in place."""
    raise RuntimeError('禁止原地改写冻结版本；请 create_draft_from_version 后 freeze')
