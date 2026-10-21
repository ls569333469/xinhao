"""REVIEW-01 first slice: data review, manual AI gate, persistent plans, pause flag.

Research/simulation only. Numerical (data) reviews never call the model.
Manual AI path is user-triggered only; without Key → clear error; never auto-bill.
Plans: daily/weekly with cron-like fields + enabled + next_run_at; overlapping
period keys dedupe. pause_new_entries is readable by EVAL/SIM later — pausing
does NOT close or stop tracking existing positions.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
try:
    from timezone_support import ZoneInfo
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        '缺少 zoneinfo；请使用 Python 3.9+。'
    ) from exc

from snapshot_store import atomic, encoded

DISCLAIMER = '研究/模拟复盘，非实盘交易；数值复盘不调用模型；AI 复盘须显式触发且已配置 Key。'


def _shanghai_tz():
    try:
        return ZoneInfo('Asia/Shanghai')
    except Exception as exc:  # ZoneInfoNotFoundError when tzdata missing on Windows
        raise RuntimeError(
            '无法加载时区 Asia/Shanghai：默认 Python 可能缺少 tzdata。'
            '请恢复项目vendor/tzdata-2026.5.zip或安装tzdata。'
            f'原始错误: {exc}'
        ) from exc


SHANGHAI = None  # lazy; avoid breaking cold import when tzdata missing


def _tz():
    global SHANGHAI
    if SHANGHAI is None:
        SHANGHAI = _shanghai_tz()
    return SHANGHAI

PLAN_KINDS = ('daily', 'weekly')
REVIEW_KINDS = ('data', 'ai_manual')


def _utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utcnow_iso():
    return _utcnow().isoformat()


def _new_id(prefix):
    return f'{prefix}-{uuid.uuid4().hex[:12]}'


def _parse_hhmm(value, field='time_of_day'):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} 须为 HH:MM（如 21:00）')
    parts = value.strip().split(':')
    if len(parts) != 2:
        raise ValueError(f'{field} 须为 HH:MM')
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError(f'{field} 须为 HH:MM') from exc
    if h < 0 or h > 23 or m < 0 or m > 59:
        raise ValueError(f'{field} 超出有效范围')
    return f'{h:02d}:{m:02d}', h, m


def _period_key(kind, when=None, tz_name='Asia/Shanghai'):
    """Dedupe key for overlapping plans of the same kind."""
    tz = ZoneInfo(tz_name) if tz_name else _tz()
    dt = when or datetime.now(tz)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(tz)
    if kind == 'daily':
        return f'daily:{local.date().isoformat()}'
    if kind == 'weekly':
        iso = local.isocalendar()
        return f'weekly:{iso.year}-W{iso.week:02d}'
    raise ValueError(f'未知计划类型: {kind}')


def _next_run_at(kind, time_of_day, weekday=None, tz_name='Asia/Shanghai', after=None):
    """Compute next run instant (UTC ISO) after `after` (default now)."""
    _, h, m = _parse_hhmm(time_of_day)
    tz = ZoneInfo(tz_name)
    base = after or datetime.now(tz)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    local = base.astimezone(tz)
    candidate = local.replace(hour=h, minute=m, second=0, microsecond=0)
    if kind == 'daily':
        if candidate <= local:
            candidate = candidate + timedelta(days=1)
    elif kind == 'weekly':
        if weekday is None:
            weekday = 1  # Monday=1 … Sunday=7 (ISO)
        weekday = int(weekday)
        if weekday < 1 or weekday > 7:
            raise ValueError('weekday 须为 1–7（周一–周日）')
        # Move to target weekday
        days_ahead = weekday - candidate.isoweekday()
        if days_ahead < 0 or (days_ahead == 0 and candidate <= local):
            days_ahead += 7
        candidate = candidate + timedelta(days=days_ahead)
        candidate = candidate.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate <= local:
            candidate = candidate + timedelta(days=7)
    else:
        raise ValueError(f'未知计划类型: {kind}')
    return candidate.astimezone(timezone.utc).replace(microsecond=0).isoformat()


class ReviewConflict(Exception):
    """Business conflict (duplicate period review, etc.)."""


class ReviewStore:
    """Reviews + plans under runtime/reviews/."""

    def __init__(self, root, eval_store=None, sim_store=None, ai_service=None):
        self.root = Path(root)
        self.dir = self.root / 'runtime' / 'reviews'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / 'state.json'
        self.reviews_path = self.dir / 'reviews.jsonl'
        self.eval_store = eval_store
        self.sim_store = sim_store
        self.ai_service = ai_service
        self._scheduler_bound = False
        self._lock = threading.RLock()
        self._ensure_bootstrapped()

    def _ensure_bootstrapped(self):
        with self._lock:
            if not self.state_path.exists():
                atomic(self.state_path, encoded(self._blank_state()))
            if not self.reviews_path.exists():
                self.reviews_path.write_text('', encoding='utf-8')

    def _blank_state(self):
        return {
            'schema': 'review-state-v1',
            'pause_new_entries': False,
            'pause_note': '暂停新入场不影响原仓跟踪与结算',
            'plans': {},
            'review_count': 0,
            'updated_at': _utcnow_iso(),
            'disclaimer': DISCLAIMER,
            'server_effect': {
                'pause_new_entries_active': False,
                'enabled_plan_ids': [],
                'ai_enabled_plan_ids': [],
                'scheduler_bound': False,
                'note': '计划已持久化；默认 enabled=false；调度绑定后按 next_run_at 触发',
            },
        }

    def _read_json(self, path):
        return json.loads(path.read_text(encoding='utf-8'))

    def _load_state(self):
        return self._read_json(self.state_path)

    def _save_state(self, state):
        state['updated_at'] = _utcnow_iso()
        state['server_effect'] = self._effect_from_state(state)
        atomic(self.state_path, encoded(state))

    def _effect_from_state(self, state):
        plans = state.get('plans') or {}
        enabled_ids = sorted(
            pid for pid, p in plans.items() if p.get('enabled') is True
        )
        ai_plan_ids = sorted(
            pid for pid, p in plans.items()
            if p.get('enabled') is True and p.get('use_ai') is True
        )
        bound = bool(self._scheduler_bound)
        if bound:
            note = (
                '暂停新入场已生效（EVAL/SIM 可读）；计划 enabled 已落盘；'
                '调度已绑定 TaskService.tick，到期触发数值复盘。'
                '付费 AI 仅当计划 use_ai=true 且显式允许时走门禁路径（默认不调用模型）。'
                if enabled_ids or state.get('pause_new_entries')
                else '当前无启用计划；暂停新入场=关。调度已绑定但无到期任务。'
            )
        else:
            note = (
                '暂停新入场已生效（EVAL/SIM 可读）；计划 enabled 已落盘。'
                '自动按 next_run_at 触发复盘调度未绑定。'
                if enabled_ids or state.get('pause_new_entries')
                else '当前无启用计划；暂停新入场=关。自动调度未绑定。'
            )
        return {
            'pause_new_entries_active': bool(state.get('pause_new_entries')),
            'enabled_plan_ids': enabled_ids,
            'enabled_plan_count': len(enabled_ids),
            'ai_enabled_plan_ids': ai_plan_ids,
            'scheduler_bound': bound,
            'note': note,
        }

    def bind_scheduler(self, bound=True):
        with self._lock:
            self._scheduler_bound = bool(bound)
            state = self._load_state()
            self._save_state(state)
            return state['server_effect']

    def _append_jsonl(self, path, row):
        line = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        with path.open('a', encoding='utf-8') as fh:
            fh.write(line + '\n')
            fh.flush()

    def _read_jsonl(self, path):
        rows = []
        if not path.exists():
            return rows
        with path.open('r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    # --- status / pause ---

    def status(self):
        with self._lock:
            state = self._load_state()
            effect = self._effect_from_state(state)
            return {
                'pause_new_entries': bool(state.get('pause_new_entries')),
                'pause_note': state.get('pause_note') or '暂停新入场不影响原仓跟踪与结算',
                'plans': list((state.get('plans') or {}).values()),
                'review_count': int(state.get('review_count') or 0),
                'server_effect': effect,
                'updated_at': state.get('updated_at'),
                'disclaimer': DISCLAIMER,
            }

    def get_pause_new_entries(self):
        """Read-only hook for EVAL/SIM: True means do not open new entries."""
        with self._lock:
            return bool(self._load_state().get('pause_new_entries'))

    def set_pause_new_entries(self, paused):
        if not isinstance(paused, bool):
            raise ValueError('pause_new_entries 须为布尔')
        with self._lock:
            state = self._load_state()
            state['pause_new_entries'] = paused
            self._save_state(state)
            return {
                'pause_new_entries': paused,
                'pause_note': '暂停新入场不影响原仓跟踪与结算',
                'server_effect': state['server_effect'],
                'updated_at': state['updated_at'],
            }

    # --- numerical snapshot (no AI) ---

    def _collect_numerical(self, strategy_version_id=None):
        grades = None
        settlements = []
        open_obs = []
        accounts = None
        if self.eval_store is not None:
            try:
                grades = self.eval_store.grades(strategy_version_id=strategy_version_id)
            except Exception as exc:  # noqa: BLE001 — surface soft failure
                grades = {'error': str(exc)[:200], 'sample_count': 0, 'win_rate_label': '无样本'}
            try:
                settlements = (self.eval_store.list_settlements(limit=50) or {}).get('settlements') or []
            except Exception:  # noqa: BLE001
                settlements = []
            try:
                open_obs = (self.eval_store.list_observations(status='open', limit=50) or {}).get('observations') or []
            except Exception:  # noqa: BLE001
                open_obs = []
        if self.sim_store is not None:
            try:
                accounts = self.sim_store.list_accounts()
            except Exception as exc:  # noqa: BLE001
                accounts = {'error': str(exc)[:200]}
        return {
            'grades': grades,
            'settlement_count': len(settlements),
            'settlements_tail': settlements[:20],
            'open_observation_count': len(open_obs),
            'open_observations': [
                {
                    'observation_id': o.get('observation_id'),
                    'symbol': o.get('symbol'),
                    'strategy_version_id': o.get('strategy_version_id'),
                    'status': o.get('status'),
                }
                for o in open_obs[:20]
            ],
            'accounts': accounts,
            'ai_called': False,
        }

    def create_data_review(self, body=None):
        """Numerical review: grades/settlements/account snapshot. Never calls AI."""
        body = body if isinstance(body, dict) else {}
        strategy_version_id = body.get('strategy_version_id')
        if strategy_version_id is not None and not isinstance(strategy_version_id, str):
            raise ValueError('strategy_version_id 须为字符串')
        note = body.get('note')
        if note is not None and not isinstance(note, str):
            raise ValueError('note 须为字符串')
        period_kind = body.get('period_kind')  # optional tag for dedupe when from plan
        if period_kind is not None and period_kind not in PLAN_KINDS:
            raise ValueError('period_kind 仅支持 daily / weekly')
        tz_name = body.get('timezone') or 'Asia/Shanghai'
        force = bool(body.get('force'))

        with self._lock:
            numerical = self._collect_numerical(strategy_version_id)
            period_key = None
            if period_kind:
                period_key = _period_key(period_kind, tz_name=tz_name)
                if not force:
                    existing = self._find_review_by_period(period_key, kind='data')
                    if existing:
                        raise ReviewConflict(
                            f'重合复盘已去重：period_key={period_key} 已有数据复盘 '
                            f'{existing.get("review_id")}'
                        )

            review_id = body.get('review_id') or _new_id('rev')
            if not isinstance(review_id, str) or not review_id.startswith('rev-'):
                raise ValueError('review_id 须为 rev-…')

            record = {
                'review_id': review_id,
                'kind': 'data',
                'created_at': _utcnow_iso(),
                'strategy_version_id': strategy_version_id,
                'period_kind': period_kind,
                'period_key': period_key,
                'timezone': tz_name,
                'note': (note or '').strip() or None,
                'numerical': numerical,
                'ai_called': False,
                'ai_status': 'not_applicable',
                'ai_note': '数据复盘仅落数值快照，不调用模型',
                'disclaimer': DISCLAIMER,
            }
            self._append_jsonl(self.reviews_path, record)
            state = self._load_state()
            state['review_count'] = int(state.get('review_count') or 0) + 1
            self._save_state(state)
            return record

    def _find_review_by_period(self, period_key, kind=None):
        rows = self._read_jsonl(self.reviews_path)
        for r in reversed(rows):
            if r.get('period_key') == period_key:
                if kind is None or r.get('kind') == kind:
                    return r
        return None

    def create_ai_manual_review(self, body=None):
        """Explicit user-triggered AI review path.

        Without Key → clear error. Never auto-bills.
        First slice: when Key configured, still does NOT call the model by default
        (no unsolicited paid batches); records gate-ok + numerical snapshot.
        Pass invoke_ai=true is refused in this slice with an honest message.
        """
        body = body if isinstance(body, dict) else {}
        if body.get('invoke_ai') is True:
            raise ValueError(
                '本首刀未接通付费 AI 复盘调用；请先用数据复盘落数值。'
                'Key 门禁已就绪，完整叙述待后续接通 AI-03 复盘模板。'
            )

        key_ok = False
        key_error = None
        if self.ai_service is None:
            key_error = 'AI 服务未注入，无法校验 Key'
        else:
            try:
                pub = self.ai_service.public_config()
                key_ok = bool(pub.get('keyConfigured'))
            except Exception as exc:  # noqa: BLE001
                key_error = str(exc)[:200]

        if key_error:
            raise ValueError(f'无法校验 API Key：{key_error}')
        if not key_ok:
            raise ValueError(
                '尚未配置 API Key，无法发起 AI 复盘。'
                '请先在设置中保存 Key；数值复盘可随时使用且不调用模型。'
            )

        strategy_version_id = body.get('strategy_version_id')
        period_kind = body.get('period_kind')
        if period_kind is not None and period_kind not in PLAN_KINDS:
            raise ValueError('period_kind 仅支持 daily / weekly')
        tz_name = body.get('timezone') or 'Asia/Shanghai'
        force = bool(body.get('force'))
        note = body.get('note')

        with self._lock:
            numerical = self._collect_numerical(strategy_version_id)
            period_key = None
            if period_kind:
                period_key = _period_key(period_kind, tz_name=tz_name)
                if not force:
                    existing = self._find_review_by_period(period_key, kind='ai_manual')
                    if existing:
                        raise ReviewConflict(
                            f'重合复盘已去重：period_key={period_key} 已有 AI 复盘 '
                            f'{existing.get("review_id")}'
                        )

            review_id = body.get('review_id') or _new_id('rev')
            record = {
                'review_id': review_id,
                'kind': 'ai_manual',
                'created_at': _utcnow_iso(),
                'strategy_version_id': strategy_version_id,
                'period_kind': period_kind,
                'period_key': period_key,
                'timezone': tz_name,
                'note': (note or '').strip() or None,
                'numerical': numerical,
                'ai_called': False,
                'ai_status': 'key_ok_not_invoked',
                'ai_note': (
                    '已确认 Key 配置；本首刀不自动发起付费模型调用。'
                    '数值快照已写入；完整 AI 叙述待后续接通。'
                ),
                'disclaimer': DISCLAIMER,
            }
            self._append_jsonl(self.reviews_path, record)
            state = self._load_state()
            state['review_count'] = int(state.get('review_count') or 0) + 1
            self._save_state(state)
            return record

    def list_reviews(self, kind=None, limit=50):
        limit = max(1, min(int(limit), 200))
        with self._lock:
            rows = self._read_jsonl(self.reviews_path)
            if kind:
                rows = [r for r in rows if r.get('kind') == kind]
            rows = list(reversed(rows[-limit:])) if limit < len(rows) else list(reversed(rows))
            # trim heavy numerical tails in list view
            slim = []
            for r in rows[:limit]:
                item = dict(r)
                num = item.get('numerical') or {}
                item['numerical_summary'] = {
                    'ai_called': num.get('ai_called', False),
                    'settlement_count': num.get('settlement_count'),
                    'open_observation_count': num.get('open_observation_count'),
                    'win_rate_label': (num.get('grades') or {}).get('win_rate_label'),
                    'sample_count': (num.get('grades') or {}).get('sample_count'),
                }
                item.pop('numerical', None)
                slim.append(item)
            return {
                'reviews': slim,
                'count': len(slim),
                'disclaimer': DISCLAIMER,
            }

    def get_review(self, review_id):
        with self._lock:
            found = None
            for r in self._read_jsonl(self.reviews_path):
                if r.get('review_id') == review_id:
                    found = r
            if not found:
                raise KeyError(review_id)
            return found

    # --- plans ---

    def list_plans(self):
        with self._lock:
            state = self._load_state()
            plans = list((state.get('plans') or {}).values())
            plans.sort(key=lambda p: p.get('created_at') or '')
            return {
                'plans': plans,
                'server_effect': state.get('server_effect') or self._effect_from_state(state),
                'pause_new_entries': bool(state.get('pause_new_entries')),
                'disclaimer': DISCLAIMER,
            }

    def save_plan(self, body):
        """Create or update a daily/weekly review plan."""
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')
        kind = body.get('kind')
        if kind not in PLAN_KINDS:
            raise ValueError('kind 仅支持 daily / weekly')
        time_of_day, _, _ = _parse_hhmm(body.get('time_of_day') or '21:00')
        tz_name = body.get('timezone') or 'Asia/Shanghai'
        try:
            ZoneInfo(tz_name)
        except Exception as exc:
            raise ValueError(f'无效时区: {tz_name}') from exc
        weekday = body.get('weekday')
        if kind == 'weekly':
            if weekday is None:
                weekday = 1
            weekday = int(weekday)
            if weekday < 1 or weekday > 7:
                raise ValueError('weekday 须为 1–7（周一–周日）')
        else:
            weekday = None

        enabled = body.get('enabled')
        if enabled is None:
            enabled = False
        if not isinstance(enabled, bool):
            raise ValueError('enabled 须为布尔')

        label = body.get('label')
        if label is not None and not isinstance(label, str):
            raise ValueError('label 须为字符串')

        with self._lock:
            state = self._load_state()
            plans = state.setdefault('plans', {})
            plan_id = body.get('plan_id')
            if plan_id:
                if not isinstance(plan_id, str) or not plan_id.startswith('plan-'):
                    raise ValueError('plan_id 须为 plan-…')
                if plan_id not in plans:
                    raise KeyError(plan_id)
                plan = dict(plans[plan_id])
            else:
                # Dedupe: one plan per kind (overlapping schedules collapse)
                for existing in plans.values():
                    if existing.get('kind') == kind:
                        raise ReviewConflict(
                            f'重合计划已去重：已有 {kind} 计划 {existing.get("plan_id")}；'
                            f'请更新该计划或先停用。'
                        )
                plan_id = _new_id('plan')
                plan = {
                    'plan_id': plan_id,
                    'created_at': _utcnow_iso(),
                }

            use_ai = body.get('use_ai')
            if use_ai is None:
                use_ai = False
            if not isinstance(use_ai, bool):
                raise ValueError('use_ai 须为布尔；默认 false（付费 AI 须显式打开）')
            allow_paid_ai = body.get('allow_paid_ai')
            if allow_paid_ai is None:
                allow_paid_ai = False
            if not isinstance(allow_paid_ai, bool):
                raise ValueError('allow_paid_ai 须为布尔；默认 false')
            if allow_paid_ai and not use_ai:
                raise ValueError('allow_paid_ai=true 时须同时 use_ai=true')

            plan.update({
                'kind': kind,
                'time_of_day': time_of_day,
                'weekday': weekday,
                'timezone': tz_name,
                'enabled': enabled,
                'use_ai': use_ai,
                'allow_paid_ai': allow_paid_ai,
                'label': (label or '').strip() or (f'每日复盘 {time_of_day}' if kind == 'daily' else f'每周复盘 周{weekday} {time_of_day}'),
                'updated_at': _utcnow_iso(),
            })
            if enabled:
                plan['next_run_at'] = _next_run_at(kind, time_of_day, weekday=weekday, tz_name=tz_name)
                plan['last_enabled_at'] = _utcnow_iso()
            else:
                plan['next_run_at'] = None

            plans[plan_id] = plan
            state['plans'] = plans
            self._save_state(state)
            return {
                'plan': plan,
                'server_effect': state['server_effect'],
            }

    def set_plan_enabled(self, plan_id, enabled):
        if not isinstance(enabled, bool):
            raise ValueError('enabled 须为布尔')
        with self._lock:
            state = self._load_state()
            plans = state.get('plans') or {}
            plan = plans.get(plan_id)
            if not plan:
                raise KeyError(plan_id)
            plan = dict(plan)
            plan['enabled'] = enabled
            plan['updated_at'] = _utcnow_iso()
            if enabled:
                plan['next_run_at'] = _next_run_at(
                    plan['kind'], plan['time_of_day'],
                    weekday=plan.get('weekday'), tz_name=plan.get('timezone') or 'Asia/Shanghai',
                )
                plan['last_enabled_at'] = _utcnow_iso()
            else:
                plan['next_run_at'] = None
                plan['last_paused_at'] = _utcnow_iso()
            plans[plan_id] = plan
            state['plans'] = plans
            self._save_state(state)
            return {
                'plan': plan,
                'server_effect': state['server_effect'],
            }

    def scheduler_tick(self, now=None):
        """Fire due daily/weekly plans. Default path = numerical data review (no AI).

        use_ai=true → AI manual gate path (Key check + numerical snapshot).
        allow_paid_ai=true is recorded but paid model invoke remains explicit-only
        (create_ai_manual_review still refuses invoke_ai=true unless future slice).
        """
        from datetime import datetime, timezone
        now = now or datetime.now(timezone.utc).replace(microsecond=0)
        if getattr(now, 'tzinfo', None) is None:
            now = now.replace(tzinfo=timezone.utc)
        # Compare in plan timezone via next_run_at ISO
        results = {'fired': [], 'plans_due': 0, 'at': now.isoformat()}
        with self._lock:
            state = self._load_state()
            plans = dict(state.get('plans') or {})

        for plan_id, plan in plans.items():
            if not plan.get('enabled'):
                continue
            next_run = plan.get('next_run_at')
            if not next_run:
                continue
            try:
                due_at = datetime.fromisoformat(next_run.replace('Z', '+00:00'))
            except ValueError:
                continue
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=timezone.utc)
            if due_at > now:
                continue
            results['plans_due'] += 1
            use_ai = bool(plan.get('use_ai'))
            try:
                if use_ai:
                    # Explicit AI path: gate only; never pass invoke_ai unless allow_paid_ai
                    # and even then first slice refuses paid call — honest.
                    body = {
                        'period_kind': plan.get('kind'),
                        'timezone': plan.get('timezone') or 'Asia/Shanghai',
                        'note': f'调度触发 AI 门禁复盘 plan={plan_id}',
                    }
                    if plan.get('allow_paid_ai'):
                        # Still do NOT set invoke_ai=true: paid call requires future wire-up
                        body['note'] += '；allow_paid_ai=true 但本切片仍不自动扣费调用'
                    record = self.create_ai_manual_review(body)
                    kind_fired = 'ai_manual'
                else:
                    record = self.create_data_review({
                        'period_kind': plan.get('kind'),
                        'timezone': plan.get('timezone') or 'Asia/Shanghai',
                        'note': f'调度触发数值复盘 plan={plan_id}（非 AI）',
                    })
                    kind_fired = 'data'
                results['fired'].append({
                    'plan_id': plan_id,
                    'review_id': record.get('review_id'),
                    'kind': kind_fired,
                    'ai_called': bool(record.get('ai_called')),
                    'use_ai': use_ai,
                    'allow_paid_ai': bool(plan.get('allow_paid_ai')),
                })
            except Exception as exc:  # noqa: BLE001
                results['fired'].append({
                    'plan_id': plan_id,
                    'error': str(exc)[:240],
                    'use_ai': use_ai,
                })
            # Advance next_run_at
            with self._lock:
                state = self._load_state()
                p = dict((state.get('plans') or {}).get(plan_id) or plan)
                p['next_run_at'] = _next_run_at(
                    p['kind'], p['time_of_day'],
                    weekday=p.get('weekday'),
                    tz_name=p.get('timezone') or 'Asia/Shanghai',
                    after=now,
                )
                p['last_run_at'] = now.isoformat()
                state.setdefault('plans', {})[plan_id] = p
                self._save_state(state)
        return results

