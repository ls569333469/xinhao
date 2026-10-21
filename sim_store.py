"""SIM-01 first slice: paper accounts + append-only event ledger.

Research/simulation only. No live exchange orders. No paid AI.
Depends on RULE-01 (optional strategy_version_id) and DATA-01 (scan.json mark).
"""
from __future__ import annotations

from durable_io import protected
import hashlib
import json
import math
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from snapshot_store import atomic, encoded

EVENT_TYPES = ('open', 'close', 'adjust', 'funding', 'fee', 'settlement')
# Shared basket: second open on same symbol is rejected (not queued).
SAME_SYMBOL_POLICY = 'reject'
SAME_SYMBOL_POLICY_NOTE = (
    '共享篮子同币冲突策略：拒绝第二次开仓（reject），不排队。'
    '独立对照账户互不影响，可各自持有同币。'
)

# T1/T2/T3 margin budget hard gate (SIM-01). Keys map to config budget_t*_u.
TIER_BUDGET_KEYS = {
    'T1': 'budget_t1_u',
    'T2': 'budget_t2_u',
    'T3': 'budget_t3_u',
}
TIER_BUDGET_NOTE = (
    '分档保证金预算硬闸：T1/T2/T3 各自占用 margin 上限（默认 400/300/300U）；'
    '超限明确拒绝，不静默截断；未解析档位的手动开仓不计入分档（仍受现金预算约束）。'
)

DEFAULT_CONFIG = {
    'schema': 'sim-config-v1',
    'mode': 'shared',  # shared | independent (config preference; both account types can exist)
    'initial_cash_u': 1000.0,
    'fee_rate': 0.0005,  # 单边 0.05% 初值
    'slip_rate': 0.0,    # 首刀：滑点记入配置，开平仓可选用；默认 0 便于守恒测试
    'max_leverage': 2.0,
    'max_positions_shared': 3,
    'budget_t1_u': 400.0,
    'budget_t2_u': 300.0,
    'budget_t3_u': 300.0,
    'risk_per_trade_pct': 0.3,
    'basket_open_risk_pct': 1.0,
    'same_symbol_policy': SAME_SYMBOL_POLICY,
    'same_symbol_policy_note': SAME_SYMBOL_POLICY_NOTE,
    'tier_budget_hard_gate': True,
    'tier_budget_note': TIER_BUDGET_NOTE,
    'disclaimer': '研究/模拟账本，非实盘交易；不会向币安或其他交易所下单。',
}


def _utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_id(prefix):
    return f'{prefix}-{uuid.uuid4().hex[:12]}'


def _finite(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name}须为有限数字')
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f'{name}须为有限数字')
    return v


def _round_u(v):
    return round(float(v), 8)


class SimStore:
    """Paper accounts under runtime/sim with append-only events.jsonl."""

    def __init__(self, root):
        self.root = Path(root)
        self.dir = self.root / 'runtime' / 'sim'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.dir / 'config.json'
        self.state_path = self.dir / 'state.json'
        self.events_path = self.dir / 'events.jsonl'
        self._lock = threading.RLock()
        self._ensure_bootstrapped()

    def _ensure_bootstrapped(self):
        with self._lock:
            if not self.config_path.exists():
                atomic(self.config_path, encoded(dict(DEFAULT_CONFIG)))
            if not self.events_path.exists():
                self.events_path.write_text('', encoding='utf-8')
            cfg = self._read_json(self.config_path)
            n_events = self._count_events()
            if not self.state_path.exists():
                # Missing state → deterministic replay (never silent 1000U with orphan events)
                state = self._rebuild_from_events(cfg) if n_events else self._blank_state(cfg)
                atomic(self.state_path, encoded(state))
            else:
                state = self._read_json(self.state_path)
                stored = int(state.get('event_count') or 0)
                if stored != n_events:
                    # Crash window: events ahead of / behind state → rebuild from ledger
                    state = self._rebuild_from_events(cfg)
                    atomic(self.state_path, encoded(state))

    def _read_json(self, path):
        return json.loads(path.read_text(encoding='utf-8'))

    def _blank_state(self, cfg):
        initial = float(cfg.get('initial_cash_u', 1000.0))
        shared = {
            'account_id': 'acc-shared',
            'kind': 'shared',
            'label': '共享1000U篮子',
            'strategy_version_id': None,
            'initial_cash': initial,
            'cash': initial,
            'margin_locked': 0.0,
            'fees_paid': 0.0,
            'realized_pnl_gross': 0.0,
            'positions': {},  # symbol -> position
            'updated_at': _utcnow(),
        }
        return {
            'schema': 'sim-state-v1',
            'accounts': {'acc-shared': shared},
            'event_count': 0,
            'updated_at': _utcnow(),
        }

    def get_config(self):
        with self._lock:
            cfg = self._read_json(self.config_path)
            cfg.setdefault('same_symbol_policy', SAME_SYMBOL_POLICY)
            cfg.setdefault('same_symbol_policy_note', SAME_SYMBOL_POLICY_NOTE)
            cfg.setdefault('disclaimer', DEFAULT_CONFIG['disclaimer'])
            cfg.setdefault('tier_budget_hard_gate', True)
            cfg.setdefault('tier_budget_note', TIER_BUDGET_NOTE)
            cfg.setdefault('revision', 1)
            for k, default in (
                ('budget_t1_u', DEFAULT_CONFIG['budget_t1_u']),
                ('budget_t2_u', DEFAULT_CONFIG['budget_t2_u']),
                ('budget_t3_u', DEFAULT_CONFIG['budget_t3_u']),
            ):
                cfg.setdefault(k, default)
            cfg['tier_budgets'] = {
                'T1': float(cfg.get('budget_t1_u') or 0),
                'T2': float(cfg.get('budget_t2_u') or 0),
                'T3': float(cfg.get('budget_t3_u') or 0),
                'unit': 'margin_u',
                'hard_gate': bool(cfg.get('tier_budget_hard_gate', True)),
                'note': cfg.get('tier_budget_note') or TIER_BUDGET_NOTE,
            }
            return cfg

    def save_config(self, patch):
        if not isinstance(patch, dict):
            raise ValueError('配置须为对象')
        with self._lock:
            cfg = self.get_config()
            # Optimistic concurrency for settings bind: refuse silent overwrite.
            expected_rev = patch.get('expected_revision')
            if expected_rev is not None:
                local_rev = int(cfg.get('revision') or 1)
                if int(expected_rev) != local_rev:
                    raise Conflict(
                        f'SIM 配置已被其他页面更新（revision local={local_rev} ≠ expected={expected_rev}），'
                        '请重新读取后保存；拒绝静默覆盖'
                    )
            expected = patch.get('expected_updated_at')
            if expected is not None:
                local = cfg.get('updated_at')
                if local and str(expected) != str(local):
                    raise Conflict(
                        f'SIM 配置已被其他页面更新（local={local} ≠ expected={expected}），'
                        '请重新读取后保存；拒绝静默覆盖'
                    )
            allowed = {
                'mode', 'fee_rate', 'slip_rate', 'max_leverage', 'max_positions_shared',
                'budget_t1_u', 'budget_t2_u', 'budget_t3_u',
                'risk_per_trade_pct', 'basket_open_risk_pct',
                'expected_updated_at', 'expected_revision',  # tokens only; not persisted as input fields
            }
            unknown = set(patch) - allowed
            if unknown:
                raise ValueError(f'不支持修改字段: {", ".join(sorted(unknown))}')
            if 'mode' in patch:
                if patch['mode'] not in ('shared', 'independent'):
                    raise ValueError('mode 仅支持 shared / independent')
                cfg['mode'] = patch['mode']
            for key in (
                'fee_rate', 'slip_rate', 'max_leverage', 'max_positions_shared',
                'budget_t1_u', 'budget_t2_u', 'budget_t3_u',
                'risk_per_trade_pct', 'basket_open_risk_pct',
            ):
                if key in patch:
                    v = _finite(key, patch[key])
                    if v < 0:
                        raise ValueError(f'{key}不可为负')
                    cfg[key] = v
            if cfg['max_leverage'] < 1:
                raise ValueError('max_leverage 须 >= 1')
            if cfg['fee_rate'] > 0.05:
                raise ValueError('fee_rate 过大（>5%），请确认')
            cfg['same_symbol_policy'] = SAME_SYMBOL_POLICY
            cfg['same_symbol_policy_note'] = SAME_SYMBOL_POLICY_NOTE
            cfg['revision'] = int(cfg.get('revision') or 1) + 1
            cfg['updated_at'] = _utcnow()
            # strip any accidental token keys
            cfg.pop('expected_updated_at', None)
            cfg.pop('expected_revision', None)
            atomic(self.config_path, encoded(cfg))
            return self.get_config()

    def _load_state(self):
        return self._read_json(self.state_path)

    def _save_state(self, state):
        state['updated_at'] = _utcnow()
        atomic(self.state_path, encoded(state))

    def _event_ids(self):
        ids = set()
        if not self.events_path.exists():
            return ids
        with self.events_path.open('r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                eid = ev.get('event_id')
                if eid:
                    ids.add(eid)
        return ids

    def _count_events(self):
        if not self.events_path.exists():
            return 0
        n = 0
        with self.events_path.open('r', encoding='utf-8') as fh:
            for line in fh:
                if line.strip():
                    n += 1
        return n

    def _read_events(self):
        rows = []
        if not self.events_path.exists():
            return rows
        with self.events_path.open('r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows


    def list_accounts(self):
        with self._lock:
            state = self._load_state()
            cfg = self.get_config()
            accounts = []
            for acc in state['accounts'].values():
                accounts.append(self._account_view(acc, cfg))
            accounts.sort(key=lambda a: (0 if a['kind'] == 'shared' else 1, a['account_id']))
            return {
                'config': {
                    'mode': cfg.get('mode'),
                    'fee_rate': cfg.get('fee_rate'),
                    'slip_rate': cfg.get('slip_rate'),
                    'max_leverage': cfg.get('max_leverage'),
                    'max_positions_shared': cfg.get('max_positions_shared'),
                    'same_symbol_policy': cfg.get('same_symbol_policy'),
                    'same_symbol_policy_note': cfg.get('same_symbol_policy_note'),
                    'disclaimer': cfg.get('disclaimer'),
                },
                'accounts': accounts,
                'event_count': state.get('event_count', 0),
                'conservation_ok': all(self._check_conservation(a)[0] for a in state['accounts'].values()),
            }

    def get_account(self, account_id):
        with self._lock:
            state = self._load_state()
            acc = state['accounts'].get(account_id)
            if not acc:
                raise KeyError(account_id)
            return self._account_view(acc, self.get_config())

    def _account_view(self, acc, cfg):
        ok, detail = self._check_conservation(acc)
        positions = []
        for sym, pos in sorted((acc.get('positions') or {}).items()):
            positions.append(dict(pos))
        cash = float(acc['cash'])
        margin = float(acc['margin_locked'])
        return {
            'account_id': acc['account_id'],
            'kind': acc['kind'],
            'label': acc.get('label') or acc['account_id'],
            'strategy_version_id': acc.get('strategy_version_id'),
            'initial_cash': acc['initial_cash'],
            'cash': _round_u(cash),
            'margin_locked': _round_u(margin),
            'equity_book': _round_u(cash + margin),
            'fees_paid': _round_u(acc.get('fees_paid', 0)),
            'realized_pnl_gross': _round_u(acc.get('realized_pnl_gross', 0)),
            'free_equity': _round_u(cash),
            'positions': positions,
            'position_count': len(positions),
            'conservation_ok': ok,
            'conservation_detail': detail,
            'updated_at': acc.get('updated_at'),
            'disclaimer': cfg.get('disclaimer'),
        }

    def _check_conservation(self, acc):
        """cash + margin + fees_paid - realized_pnl_gross == initial_cash"""
        left = (
            float(acc['cash'])
            + float(acc.get('margin_locked', 0))
            + float(acc.get('fees_paid', 0))
            - float(acc.get('realized_pnl_gross', 0))
        )
        initial = float(acc['initial_cash'])
        ok = abs(left - initial) <= 1e-6
        return ok, {
            'left': _round_u(left),
            'initial_cash': initial,
            'delta': _round_u(left - initial),
            'formula': 'cash + margin_locked + fees_paid - realized_pnl_gross == initial_cash',
        }

    def list_events(self, limit=50, account_id=None):
        limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = []
            if self.events_path.exists():
                with self.events_path.open('r', encoding='utf-8') as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if account_id and ev.get('account_id') != account_id:
                            continue
                        rows.append(ev)
            rows = rows[-limit:]
            rows.reverse()  # newest first
            return {'events': rows, 'count': len(rows), 'same_symbol_policy': SAME_SYMBOL_POLICY}

    def ensure_independent_account(self, strategy_version_id, label=None):
        """Create or return independent 1000U contrast account for a frozen version."""
        if not strategy_version_id or not isinstance(strategy_version_id, str):
            raise ValueError('须提供 strategy_version_id')
        if not strategy_version_id.startswith('ver-'):
            raise ValueError('strategy_version_id 须为 ver-… 冻结版本')
        with self._lock:
            state = self._load_state()
            cfg = self.get_config()
            for acc in state['accounts'].values():
                if acc.get('kind') == 'independent' and acc.get('strategy_version_id') == strategy_version_id:
                    return self._account_view(acc, cfg)
            aid = _new_id('acc')
            initial = float(cfg.get('initial_cash_u', 1000.0))
            acc = {
                'account_id': aid,
                'kind': 'independent',
                'label': label or f'独立对照·{strategy_version_id}',
                'strategy_version_id': strategy_version_id,
                'initial_cash': initial,
                'cash': initial,
                'margin_locked': 0.0,
                'fees_paid': 0.0,
                'realized_pnl_gross': 0.0,
                'positions': {},
                'updated_at': _utcnow(),
            }
            state['accounts'][aid] = acc
            self._save_state(state)
            return self._account_view(acc, cfg)

    def resolve_mark(self, symbol, market=None):
        """Last known mark from scan.json. Missing → clear error (no silent 0)."""
        if not symbol or not isinstance(symbol, str):
            raise ValueError('须指定 symbol')
        symbol = symbol.strip().upper()
        scan_path = self.root / 'scan.json'
        if not scan_path.exists():
            raise ValueError('缺少 scan.json，无法取标记价格；请先完成扫描（DATA-01）')
        try:
            scan = json.loads(scan_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f'scan.json 不可读: {exc}') from exc
        rows = scan.get('rows') or []
        matches = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            if str(r.get('symbol') or '').upper() != symbol:
                continue
            if market and str(r.get('market') or '') != market:
                continue
            matches.append(r)
        if not matches:
            raise ValueError(f'扫描快照中无合约 {symbol}' + (f' / {market}' if market else '') + '，无法取价')
        row = matches[0]
        price = row.get('markPrice')
        if price is None:
            price = row.get('price')
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(float(price)) or float(price) <= 0:
            raise ValueError(f'{symbol} 标记价格缺失或无效（拒绝静默按 0 成交）')
        return {
            'symbol': symbol,
            'market': row.get('market'),
            'mark': float(price),
            'price_field': 'markPrice' if row.get('markPrice') is not None else 'price',
            'as_of': row.get('asOf') or scan.get('available_at') or scan.get('finishedAt'),
            'scan_id': scan.get('scan_id') or scan.get('id'),
        }


    @staticmethod
    def normalize_tier(value):
        """Return T1/T2/T3 or None. Accepts t1, Tier-2, base_template_id, etc."""
        if value is None:
            return None
        s = str(value).strip().upper()
        if not s:
            return None
        if s in TIER_BUDGET_KEYS:
            return s
        # Strip common prefixes: TIER- / TIER_ / TIER
        if s.startswith('TIER'):
            rest = s[4:].lstrip('-_ ')
            if rest[:1] in ('1', '2', '3'):
                return 'T' + rest[0]
        if s[0] == 'T' and len(s) >= 2 and s[1] in ('1', '2', '3'):
            # T1 / T1-xxx / T2_foo
            if len(s) == 2 or s[2] in ('-', '_', ' ', '.'):
                return 'T' + s[1]
            if s.startswith('T1'):
                return 'T1'
            if s.startswith('T2'):
                return 'T2'
            if s.startswith('T3'):
                return 'T3'
        return None

    def resolve_strategy_tier(self, body=None, version_meta=None):
        """Resolve tier from request body and/or frozen version metadata."""
        body = body or {}
        for key in ('tier', 'strategy_tier'):
            t = self.normalize_tier(body.get(key))
            if t:
                return t
        meta = version_meta or {}
        for key in ('tier', 'strategy_tier'):
            t = self.normalize_tier(meta.get(key))
            if t:
                return t
        rules = meta.get('rules') if isinstance(meta.get('rules'), dict) else {}
        for key in ('tier', 'strategy_tier', 'base_template_id'):
            t = self.normalize_tier(rules.get(key))
            if t:
                return t
        t = self.normalize_tier(meta.get('base_template_id'))
        if t:
            return t
        t = self.normalize_tier(meta.get('strategy_id'))
        if t:
            return t
        t = self.normalize_tier(body.get('base_template_id') or body.get('strategy_id'))
        if t:
            return t
        return None

    def tier_budget_u(self, cfg, tier):
        key = TIER_BUDGET_KEYS.get(tier)
        if not key:
            raise ValueError(f'无效档位: {tier}')
        return float(cfg.get(key) if cfg.get(key) is not None else DEFAULT_CONFIG[key])

    def tier_margin_used(self, acc, tier):
        """Sum margin of open positions tagged with this strategy_tier."""
        used = 0.0
        for pos in (acc.get('positions') or {}).values():
            if self.normalize_tier(pos.get('strategy_tier')) == tier:
                used += float(pos.get('margin') or 0)
        return _round_u(used)

    def assert_tier_budget(self, acc, cfg, tier, new_margin, *, context='开仓'):
        """Hard gate: used + new margin must not exceed tier budget. Clear Conflict on over."""
        if not cfg.get('tier_budget_hard_gate', True):
            return {
                'strategy_tier': tier,
                'tier_budget_u': self.tier_budget_u(cfg, tier),
                'tier_margin_used': self.tier_margin_used(acc, tier),
                'tier_margin_new': _round_u(new_margin),
                'hard_gate': False,
                'ok': True,
            }
        budget = self.tier_budget_u(cfg, tier)
        used = self.tier_margin_used(acc, tier)
        new_m = float(new_margin)
        projected = used + new_m
        info = {
            'strategy_tier': tier,
            'tier_budget_u': _round_u(budget),
            'tier_margin_used': _round_u(used),
            'tier_margin_new': _round_u(new_m),
            'tier_margin_projected': _round_u(projected),
            'tier_budget_remaining': _round_u(max(0.0, budget - used)),
            'hard_gate': True,
            'ok': projected <= budget + 1e-9,
        }
        if projected > budget + 1e-9:
            raise Conflict(
                f'{tier} 档位保证金预算超限（{context}）：'
                f'已用 {used:.6f}U + 本次 {new_m:.6f}U = {projected:.6f}U，'
                f'上限 {budget:.6f}U（config.{TIER_BUDGET_KEYS[tier]}）；拒绝，不静默截断'
            )
        return info

    @protected
    def open_position(self, body):
        """Paper open. body: event_id?, account_id?, symbol, side, qty|notional_u, leverage?, strategy_version_id?, market?"""
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')
        return self._apply_open(body)

    def close_position(self, body):
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')
        return self._apply_close(body)

    def _append_event(self, event):
        line = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        with self.events_path.open('a', encoding='utf-8') as fh:
            fh.write(line + '\n')
            fh.flush()

    def _apply_open(self, body):
        with self._lock:
            cfg = self.get_config()
            state = self._load_state()
            event_id = body.get('event_id') or _new_id('evt')
            if not isinstance(event_id, str) or not event_id.strip():
                raise ValueError('event_id 须为非空字符串')
            event_id = event_id.strip()
            if event_id in self._event_ids():
                raise Conflict(f'重复事件已拒绝: {event_id}')

            account_id = body.get('account_id') or 'acc-shared'
            # Independent path: ensure account for strategy version
            if body.get('account_kind') == 'independent' or (
                cfg.get('mode') == 'independent' and body.get('strategy_version_id') and not body.get('account_id')
            ):
                svid = body.get('strategy_version_id')
                if not svid:
                    raise ValueError('独立对照开仓须提供 strategy_version_id')
                # create outside nested lock? we already hold lock — inline ensure
                found = None
                for acc in state['accounts'].values():
                    if acc.get('kind') == 'independent' and acc.get('strategy_version_id') == svid:
                        found = acc['account_id']
                        break
                if not found:
                    aid = _new_id('acc')
                    initial = float(cfg.get('initial_cash_u', 1000.0))
                    state['accounts'][aid] = {
                        'account_id': aid,
                        'kind': 'independent',
                        'label': f'独立对照·{svid}',
                        'strategy_version_id': svid,
                        'initial_cash': initial,
                        'cash': initial,
                        'margin_locked': 0.0,
                        'fees_paid': 0.0,
                        'realized_pnl_gross': 0.0,
                        'positions': {},
                        'updated_at': _utcnow(),
                    }
                    account_id = aid
                else:
                    account_id = found

            acc = state['accounts'].get(account_id)
            if not acc:
                raise KeyError(f'账户不存在: {account_id}')

            symbol = str(body.get('symbol') or '').strip().upper()
            if not symbol:
                raise ValueError('须指定 symbol')
            side = str(body.get('side') or 'long').strip().lower()
            if side not in ('long', 'short'):
                raise ValueError('side 仅支持 long / short')

            mark_info = None
            if body.get('mark') is not None:
                mark = _finite('mark', body['mark'])
                if mark <= 0:
                    raise ValueError('mark 须 > 0')
                mark_info = {'symbol': symbol, 'mark': mark, 'price_field': 'client', 'market': body.get('market')}
            else:
                mark_info = self.resolve_mark(symbol, body.get('market'))
                mark = mark_info['mark']

            slip = float(cfg.get('slip_rate') or 0)
            # Adverse slip: long pays more, short sells lower
            fill = mark * (1 + slip) if side == 'long' else mark * (1 - slip)
            if fill <= 0:
                raise ValueError('成交价无效')

            leverage = body.get('leverage')
            if leverage is None:
                leverage = 1.0
            leverage = _finite('leverage', leverage)
            max_lev = float(cfg.get('max_leverage') or 2)
            if leverage < 1 or leverage > max_lev:
                raise ValueError(f'杠杆须在 1–{max_lev} 之间')

            if body.get('qty') is not None:
                qty = _finite('qty', body['qty'])
            elif body.get('notional_u') is not None:
                notional_u = _finite('notional_u', body['notional_u'])
                if notional_u <= 0:
                    raise ValueError('notional_u 须 > 0')
                qty = notional_u / fill
            else:
                raise ValueError('须提供 qty 或 notional_u')
            if qty <= 0:
                raise ValueError('qty 须 > 0')

            notional = qty * fill
            margin = notional / leverage
            fee_rate = float(cfg.get('fee_rate') or 0)
            fee = notional * fee_rate
            need = margin + fee

            # Same-symbol conflict on shared basket
            positions = acc.setdefault('positions', {})
            if symbol in positions:
                if acc.get('kind') == 'shared' and SAME_SYMBOL_POLICY == 'reject':
                    raise Conflict(
                        f'同币冲突：共享篮子已持有 {symbol}，策略为拒绝第二次开仓（reject）'
                    )
                if acc.get('kind') == 'independent':
                    raise Conflict(f'账户已持有 {symbol}，请先平仓再开（首刀不支持加仓）')

            if acc.get('kind') == 'shared':
                max_pos = int(cfg.get('max_positions_shared') or 3)
                if len(positions) >= max_pos:
                    raise Conflict(f'共享篮子最多 {max_pos} 仓，当前已满')

            # SIM-01 T1/T2/T3 tier margin hard gate (when tier known)
            strategy_tier = self.resolve_strategy_tier(body)
            tier_info = None
            if strategy_tier:
                tier_info = self.assert_tier_budget(
                    acc, cfg, strategy_tier, margin, context='开仓'
                )

            free = float(acc['cash'])
            if need > free + 1e-9:
                raise Conflict(
                    f'预算不足：需要保证金+费用 {need:.6f}U，可用现金 {free:.6f}U'
                )

            acc['cash'] = _round_u(free - need)
            acc['margin_locked'] = _round_u(float(acc['margin_locked']) + margin)
            acc['fees_paid'] = _round_u(float(acc.get('fees_paid', 0)) + fee)
            pos = {
                'symbol': symbol,
                'market': mark_info.get('market') or body.get('market'),
                'side': side,
                'qty': _round_u(qty),
                'entry_price': _round_u(fill),
                'mark_at_entry': _round_u(mark),
                'leverage': leverage,
                'margin': _round_u(margin),
                'notional': _round_u(notional),
                'strategy_version_id': body.get('strategy_version_id') or acc.get('strategy_version_id'),
                'strategy_tier': strategy_tier,
                'opened_at': _utcnow(),
                'open_event_id': event_id,
            }
            positions[symbol] = pos
            acc['updated_at'] = _utcnow()

            ok, detail = self._check_conservation(acc)
            if not ok:
                raise RuntimeError(f'资金守恒失败，已中止写入: {detail}')

            event = {
                'event_id': event_id,
                'type': 'open',
                'account_id': account_id,
                'symbol': symbol,
                'side': side,
                'qty': pos['qty'],
                'price': pos['entry_price'],
                'mark': _round_u(mark),
                'leverage': leverage,
                'margin': pos['margin'],
                'fee': _round_u(fee),
                'notional': pos['notional'],
                'strategy_version_id': pos.get('strategy_version_id'),
                'strategy_tier': strategy_tier,
                'market': pos.get('market'),
                'price_source': mark_info.get('price_field'),
                'scan_id': mark_info.get('scan_id'),
                'status': 'accepted',
                'created_at': _utcnow(),
                'conservation': detail,
                'note': '模拟开仓；非实盘',
            }
            self._append_event(event)
            state['event_count'] = int(state.get('event_count') or 0) + 1
            state['accounts'][account_id] = acc
            self._save_state(state)
            out = {
                'event': event,
                'account': self._account_view(acc, cfg),
            }
            if tier_info:
                out['tier_budget'] = tier_info
            return out

    def _apply_close(self, body):
        with self._lock:
            cfg = self.get_config()
            state = self._load_state()
            event_id = body.get('event_id') or _new_id('evt')
            if not isinstance(event_id, str) or not event_id.strip():
                raise ValueError('event_id 须为非空字符串')
            event_id = event_id.strip()
            if event_id in self._event_ids():
                raise Conflict(f'重复事件已拒绝: {event_id}')

            account_id = body.get('account_id') or 'acc-shared'
            acc = state['accounts'].get(account_id)
            if not acc:
                raise KeyError(f'账户不存在: {account_id}')

            symbol = str(body.get('symbol') or '').strip().upper()
            if not symbol:
                raise ValueError('须指定 symbol')
            positions = acc.setdefault('positions', {})
            pos = positions.get(symbol)
            if not pos:
                raise Conflict(f'账户无 {symbol} 持仓可平')

            if body.get('mark') is not None:
                mark = _finite('mark', body['mark'])
                if mark <= 0:
                    raise ValueError('mark 须 > 0')
                mark_info = {'mark': mark, 'price_field': 'client', 'scan_id': None}
            else:
                mark_info = self.resolve_mark(symbol, body.get('market') or pos.get('market'))
                mark = mark_info['mark']

            slip = float(cfg.get('slip_rate') or 0)
            side = pos['side']
            # Close slip adverse: long sells lower, short buys higher
            fill = mark * (1 - slip) if side == 'long' else mark * (1 + slip)
            qty = float(pos['qty'])
            entry = float(pos['entry_price'])
            margin = float(pos['margin'])
            if side == 'long':
                gross_pnl = (fill - entry) * qty
            else:
                gross_pnl = (entry - fill) * qty
            fee_rate = float(cfg.get('fee_rate') or 0)
            exit_notional = qty * fill
            fee = exit_notional * fee_rate

            acc['cash'] = _round_u(float(acc['cash']) + margin + gross_pnl - fee)
            acc['margin_locked'] = _round_u(float(acc['margin_locked']) - margin)
            acc['fees_paid'] = _round_u(float(acc.get('fees_paid', 0)) + fee)
            acc['realized_pnl_gross'] = _round_u(float(acc.get('realized_pnl_gross', 0)) + gross_pnl)
            del positions[symbol]
            acc['updated_at'] = _utcnow()

            ok, detail = self._check_conservation(acc)
            if not ok:
                raise RuntimeError(f'资金守恒失败，已中止写入: {detail}')

            event = {
                'event_id': event_id,
                'type': 'close',
                'account_id': account_id,
                'symbol': symbol,
                'side': side,
                'qty': _round_u(qty),
                'price': _round_u(fill),
                'mark': _round_u(mark),
                'entry_price': _round_u(entry),
                'margin_released': _round_u(margin),
                'fee': _round_u(fee),
                'realized_pnl_gross': _round_u(gross_pnl),
                'strategy_version_id': pos.get('strategy_version_id'),
                'open_event_id': pos.get('open_event_id'),
                'price_source': mark_info.get('price_field'),
                'scan_id': mark_info.get('scan_id'),
                'status': 'accepted',
                'created_at': _utcnow(),
                'conservation': detail,
                'note': '模拟平仓；非实盘',
            }
            self._append_event(event)
            state['event_count'] = int(state.get('event_count') or 0) + 1
            state['accounts'][account_id] = acc
            self._save_state(state)
            return {
                'event': event,
                'account': self._account_view(acc, cfg),
            }

    def stub_event(self, body):
        """Append adjust/funding/fee/settlement stub without changing balances (ledger only).

        First slice: records intent for EVAL-01; does not mutate cash unless type=fee with amount.
        """
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')
        etype = body.get('type')
        if etype not in ('adjust', 'funding', 'fee', 'settlement'):
            raise ValueError('stub 仅支持 adjust/funding/fee/settlement')
        with self._lock:
            cfg = self.get_config()
            state = self._load_state()
            event_id = body.get('event_id') or _new_id('evt')
            if event_id in self._event_ids():
                raise Conflict(f'重复事件已拒绝: {event_id}')
            account_id = body.get('account_id') or 'acc-shared'
            acc = state['accounts'].get(account_id)
            if not acc:
                raise KeyError(f'账户不存在: {account_id}')

            amount = 0.0
            if body.get('amount') is not None:
                amount = _finite('amount', body['amount'])

            # Minimal fee stub can debit cash; funding/settlement stubs are ledger markers only in this slice.
            if etype == 'fee' and amount != 0:
                if amount > float(acc['cash']) + 1e-9:
                    raise Conflict('费用超过可用现金')
                acc['cash'] = _round_u(float(acc['cash']) - amount)
                acc['fees_paid'] = _round_u(float(acc.get('fees_paid', 0)) + amount)
                acc['updated_at'] = _utcnow()
                ok, detail = self._check_conservation(acc)
                if not ok:
                    raise RuntimeError(f'资金守恒失败: {detail}')
            else:
                ok, detail = self._check_conservation(acc)

            event = {
                'event_id': event_id,
                'type': etype,
                'account_id': account_id,
                'symbol': (body.get('symbol') or '').upper() or None,
                'amount': _round_u(amount) if amount else 0.0,
                'status': 'accepted' if etype == 'fee' and amount else 'stub',
                'created_at': _utcnow(),
                'conservation': detail,
                'note': body.get('note') or f'{etype} 占位；EVAL-01 前向结算未实现',
            }
            self._append_event(event)
            state['event_count'] = int(state.get('event_count') or 0) + 1
            state['accounts'][account_id] = acc
            self._save_state(state)
            return {'event': event, 'account': self._account_view(acc, cfg)}

    def plan_autofill(self, body, strategy_store=None, rule_bindings=None, review_store=None):
        """Dry-run size a paper fill from a confirmed signal. Never writes ledger."""
        plan = self._build_autofill_plan(
            body, strategy_store=strategy_store, rule_bindings=rule_bindings,
            review_store=review_store, for_write=False,
        )
        plan['dry_run'] = True
        plan['will_write'] = False
        plan['note'] = '仅预览；未写入账本。确认后调用 autofill(confirm=true)。'
        return plan

    @protected
    def autofill(self, body, strategy_store=None, rule_bindings=None, review_store=None):
        """Fill paper position from confirmed signal. Reuses open_position; no invented balances."""
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')
        if not body.get('confirm'):
            raise ValueError('自动填仓须显式 confirm=true；可先用 preview/plan 查看计划')
        plan = self._build_autofill_plan(
            body, strategy_store=strategy_store, rule_bindings=rule_bindings,
            review_store=review_store, for_write=True,
        )
        if plan.get('same_symbol_blocked'):
            raise Conflict(plan.get('block_reason') or '同币冲突；拒绝自动填仓')
        open_body = {
            'event_id': body.get('event_id') or plan['planned_event_id'],
            'account_id': plan['account_id'],
            'symbol': plan['symbol'],
            'side': plan['side'],
            'market': plan.get('market'),
            'notional_u': plan['notional_u'],
            'leverage': plan['leverage'],
            'strategy_version_id': plan.get('strategy_version_id'),
            'strategy_tier': plan.get('strategy_tier'),
            'tier': plan.get('strategy_tier'),
            'mark': plan['mark'],
        }
        if body.get('account_kind'):
            open_body['account_kind'] = body['account_kind']
        result = self.open_position(open_body)
        result['autofill'] = {
            'schema': 'sim-autofill-v1',
            'plan': plan,
            'source': plan.get('source'),
            'disclaimer': plan.get('disclaimer'),
        }
        # Tag event note via response only (ledger event already written by open)
        if result.get('event'):
            result['event'] = dict(result['event'])
            result['event']['autofill'] = True
            result['event']['signal_source'] = plan.get('source')
        return result

    def _build_autofill_plan(self, body, strategy_store=None, rule_bindings=None, review_store=None, for_write=False):
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')
        symbol = str(body.get('symbol') or '').strip().upper()
        if not symbol:
            raise ValueError('须指定 symbol（确认信号合约）')
        side = str(body.get('side') or 'long').strip().lower()
        if side not in ('long', 'short'):
            raise ValueError('side 仅支持 long / short')

        cfg = self.get_config()
        svid = body.get('strategy_version_id')
        source = 'client_signal'

        # Prefer RULE experiment binding when version omitted
        if not svid and rule_bindings is not None:
            try:
                bindings = rule_bindings.list_bindings()
                slot = (bindings.get('surfaces') or {}).get('experiment') or {}
                ref = slot.get('ref') or {}
                if ref.get('version_id'):
                    svid = ref['version_id']
                    source = 'rule_binding:experiment'
            except Exception:
                pass

        # F03: pause_new_entries hard gate (preview + write)
        if review_store is not None:
            try:
                paused = bool(review_store.get_pause_new_entries())
            except Exception:
                paused = False
            if paused:
                raise Conflict('暂停新入场（pause_new_entries=true）；拒绝自动填仓')

        # F03: require strategy version for autofill path
        if not svid:
            raise Conflict('自动填仓须绑定 strategy_version_id（或 experiment 绑定）；拒绝无版本开仓')

        version_meta = None
        frozen_exec = None
        if strategy_store is not None:
            try:
                version_meta = strategy_store.get_version(svid)
            except KeyError as exc:
                raise ValueError(f'冻结版本不存在: {svid}') from exc
            # F03: enabled hard gate — disabled must NOT open
            if not version_meta.get('enabled'):
                raise Conflict(
                    f'策略版本未启用（enabled=false）；拒绝自动填仓: {svid}'
                )
            # V1: paper exec params only from frozen rules.params (F04 no silent defaults)
            from frozen_exec import require_frozen_exec_params, FrozenExecConflict
            try:
                frozen_exec = require_frozen_exec_params(version_meta, body, context='autofill')
            except FrozenExecConflict as exc:
                raise Conflict(str(exc)) from exc
            if frozen_exec.get('side'):
                side = frozen_exec['side']
            # F03: coverage hard gate on declared deps
            deps = version_meta.get('data_dependencies') or []
            if hasattr(strategy_store, 'check_dependencies'):
                gate = strategy_store.check_dependencies(deps)
                if not gate.get('ok'):
                    missing = gate.get('missing_dependencies') or []
                    raise Conflict(
                        f'覆盖硬闸未通过，拒绝自动填仓；缺失依赖: {missing}'
                    )
            # V1: per-symbol warmup hard gate — request cannot lower frozen/floor warmup
            try:
                from coverage_gate import assert_autofill_symbol_gate, resolve_warmup_bars, MIN_WARMUP_BARS
                rules = version_meta.get('rules') or {}
                params = rules.get('params') if isinstance(rules.get('params'), dict) else {}
                frozen_min = (
                    params.get('min_warmup_bars')
                    or params.get('warmup_bars')
                    or version_meta.get('min_warmup_bars')
                    or MIN_WARMUP_BARS
                )
                # Refuse explicit undercut before gate (clear 409 reason)
                if 'warmup_bars' in body and body.get('warmup_bars') is not None:
                    _eff, _meta = resolve_warmup_bars(body.get('warmup_bars'), frozen_min)
                    if _meta.get('refused_lower'):
                        raise Conflict(
                            f'预热硬闸拒绝：请求 warmup_bars={_meta.get("requested")} '
                            f'低于冻结/底线 {_meta.get("frozen_min")}；'
                            f'禁止请求降低预热要求（HTTP 409）'
                        )
                sym_gate = assert_autofill_symbol_gate(
                    self.root, symbol,
                    interval=str(body.get('interval') or '4h'),
                    warmup_bars=body.get('warmup_bars'),
                    frozen_min_warmup=frozen_min,
                    market=body.get('market') or 'USDⓈ-M',
                )
                if not sym_gate.get('ok'):
                    raise Conflict(
                        (sym_gate.get('reason') or '目标合约覆盖不足')
                        + '；' + (sym_gate.get('backfill_hint') or '请先区间补查')
                        + '（HTTP 409）'
                    )
            except Conflict:
                raise
            except Exception as exc:
                raise Conflict(f'合约覆盖闸门不可用，拒绝自动填仓: {exc}') from exc
            # Whitelist gate when mode=allow
            rules = version_meta.get('rules') or {}
            wl = rules.get('whitelist_trigger') or {}
            mode = (wl.get('mode') or 'unset').lower()
            symbols = [str(x).upper() for x in (wl.get('symbols') or []) if x]
            if mode == 'allow' and symbols and symbol not in symbols:
                raise Conflict(
                    f'白名单触发未覆盖 {symbol}（allow 列表 {len(symbols)} 项）；拒绝自动填仓'
                )
            if mode == 'deny' and symbol in symbols:
                raise Conflict(f'白名单触发拒绝 {symbol}（deny）；拒绝自动填仓')
            if mode == 'disabled':
                raise Conflict('该版本白名单触发为 disabled；拒绝自动填仓')
        elif for_write:
            raise Conflict('未接入 strategy_store；拒绝自动填仓写账')

        # F03: trigger evidence required before write (and surfaced in preview)
        trigger = body.get('trigger_evidence') or body.get('signal_id') or body.get('confirmed_signal')
        if not trigger:
            raise Conflict(
                '缺少触发/确认信号证据（trigger_evidence / signal_id / confirmed_signal）；拒绝自动填仓'
            )

        # Mark: client override or scan.json
        if body.get('mark') is not None:
            mark = _finite('mark', body['mark'])
            if mark <= 0:
                raise ValueError('mark 须 > 0')
            mark_info = {'mark': mark, 'price_field': 'client', 'market': body.get('market'), 'scan_id': None}
        else:
            mark_info = self.resolve_mark(symbol, body.get('market'))
            mark = float(mark_info['mark'])

        slip = float(cfg.get('slip_rate') or 0)
        fill = mark * (1 + slip) if side == 'long' else mark * (1 - slip)
        leverage = body.get('leverage')
        if frozen_exec and frozen_exec.get('leverage') is not None:
            leverage = frozen_exec['leverage']
        if leverage is None:
            leverage = 1.0
        leverage = _finite('leverage', leverage)
        max_lev = float(cfg.get('max_leverage') or 2)
        if leverage < 1 or leverage > max_lev:
            raise ValueError(f'杠杆须在 1–{max_lev} 之间')

        account_id = body.get('account_id') or 'acc-shared'
        state = self._load_state()
        acc = state['accounts'].get(account_id)
        if not acc:
            raise KeyError(f'账户不存在: {account_id}')
        free = float(acc['cash'])

        # Size: frozen notional_u wins; else explicit body; else account risk pct
        if frozen_exec and frozen_exec.get('notional_u') is not None:
            notional_u = float(frozen_exec['notional_u'])
            size_rule = 'frozen_notional_u'
        elif body.get('notional_u') is not None:
            notional_u = _finite('notional_u', body['notional_u'])
            size_rule = 'client_notional'
        else:
            risk_pct = float(cfg.get('risk_per_trade_pct') or 0.3)
            if risk_pct <= 0 or risk_pct > 100:
                raise ValueError('risk_per_trade_pct 无效')
            notional_u = free * (risk_pct / 100.0)
            size_rule = f'risk_per_trade_pct={risk_pct}'
        if notional_u <= 0:
            raise Conflict('自动名义为 0：可用现金不足或风险比例过小')

        # Cap by free cash considering margin+fee
        fee_rate = float(cfg.get('fee_rate') or 0)
        # Solve approx: notional/leverage + notional*fee <= free
        # notional * (1/leverage + fee) <= free
        denom = (1.0 / leverage) + fee_rate
        max_notional = free / denom if denom > 0 else 0
        capped = False
        if notional_u > max_notional + 1e-12:
            notional_u = max_notional
            capped = True
        if notional_u <= 0:
            raise Conflict(f'预算不足：可用现金 {free:.6f}U 无法开仓')

        qty = notional_u / fill if fill else 0
        margin = notional_u / leverage
        fee = notional_u * fee_rate
        need = margin + fee

        # SIM-01: autofill requires resolvable T1/T2/T3 + hard budget gate
        strategy_tier = self.resolve_strategy_tier(body, version_meta)
        if not strategy_tier:
            raise Conflict(
                '自动填仓须指定策略档位 T1/T2/T3（body.tier / 版本 rules.base_template_id|tier）；拒绝'
            )
        tier_info = self.assert_tier_budget(
            acc, cfg, strategy_tier, margin, context='自动填仓'
        )

        # Same-symbol preview check (actual reject still in open)
        positions = acc.get('positions') or {}
        same_symbol_blocked = False
        block_reason = None
        if symbol in positions:
            same_symbol_blocked = True
            block_reason = f'同币冲突：已持有 {symbol}（策略 reject）'

        # F03: stable idempotency key shared by preview/confirm
        idem = body.get('event_id') or body.get('idempotency_key')
        if not idem:
            material = {
                'strategy_version_id': svid,
                'version_content_hash': (version_meta or {}).get('content_hash'),
                'symbol': symbol,
                'side': side,
                'mark': _round_u(mark),
                'trigger': trigger if isinstance(trigger, (str, int, float)) else json.dumps(trigger, sort_keys=True, ensure_ascii=False, default=str),
            }
            digest = hashlib.sha256(
                json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            ).hexdigest()[:20]
            idem = f'evt-{digest}'
        if not str(idem).startswith('evt-'):
            idem = f'evt-{idem}'

        return {
            'schema': 'sim-autofill-plan-v1',
            'planned_event_id': idem,
            'frozen_exec': frozen_exec,
            'version_content_hash': (version_meta or {}).get('content_hash'),
            'idempotency_key': idem,
            'trigger_evidence': trigger if isinstance(trigger, (str, int, float, dict, list)) else str(trigger),
            'account_id': account_id,
            'symbol': symbol,
            'side': side,
            'market': mark_info.get('market') or body.get('market'),
            'strategy_version_id': svid,
            'strategy_title': (version_meta or {}).get('title') if version_meta else None,
            'version_content_hash': (version_meta or {}).get('content_hash'),
            'strategy_tier': strategy_tier,
            'tier_budget': tier_info,
            'enabled_checked': True,
            'coverage_gate_checked': True,
            'mark': _round_u(mark),
            'fill_price': _round_u(fill),
            'slip_rate': slip,
            'leverage': leverage,
            'notional_u': _round_u(notional_u),
            'qty': _round_u(qty),
            'margin': _round_u(margin),
            'fee': _round_u(fee),
            'need_cash': _round_u(need),
            'free_cash': _round_u(free),
            'size_rule': size_rule,
            'capped_to_budget': capped,
            'price_source': mark_info.get('price_field'),
            'scan_id': mark_info.get('scan_id'),
            'source': source,
            'same_symbol_blocked': same_symbol_blocked,
            'block_reason': block_reason,
            'same_symbol_policy': SAME_SYMBOL_POLICY,
            'disclaimer': cfg.get('disclaimer') or DEFAULT_CONFIG['disclaimer'],
            'inventory_fiction': False,
            'note': '按确认信号估算纸面仓位；不编造余额；非实盘',
        }

    def reload(self):
        """Force re-read from disk (simulates process restart for tests)."""
        with self._lock:
            self._ensure_bootstrapped()
            state = self._load_state()
            # Verify conservation on all accounts
            for acc in state['accounts'].values():
                ok, detail = self._check_conservation(acc)
                if not ok:
                    raise RuntimeError(f'重启校验守恒失败 {acc["account_id"]}: {detail}')
            return self.list_accounts()

    def _rebuild_from_events(self, cfg):
        """Deterministic replay of events.jsonl into account state.

        Covers: crash after event append before state write, state missing,
        event_count mismatch. Never silently reset to initial 1000U while
        orphan open events remain in the ledger.
        """
        state = self._blank_state(cfg)
        events = self._read_events()
        for ev in events:
            if not isinstance(ev, dict):
                continue
            et = ev.get('type')
            aid = ev.get('account_id') or 'acc-shared'
            if aid not in state['accounts']:
                # Independent account created implicitly by first open
                initial = float(cfg.get('initial_cash_u', 1000.0))
                state['accounts'][aid] = {
                    'account_id': aid,
                    'kind': ev.get('account_kind') or ('independent' if aid != 'acc-shared' else 'shared'),
                    'label': f'重放账户·{aid}',
                    'strategy_version_id': ev.get('strategy_version_id'),
                    'initial_cash': initial,
                    'cash': initial,
                    'margin_locked': 0.0,
                    'fees_paid': 0.0,
                    'realized_pnl_gross': 0.0,
                    'positions': {},
                    'updated_at': ev.get('created_at') or _utcnow(),
                }
            acc = state['accounts'][aid]
            positions = acc.setdefault('positions', {})
            if et == 'open' and ev.get('status') == 'accepted':
                symbol = str(ev.get('symbol') or '').upper()
                margin = float(ev.get('margin') or 0)
                fee = float(ev.get('fee') or 0)
                acc['cash'] = _round_u(float(acc['cash']) - margin - fee)
                acc['margin_locked'] = _round_u(float(acc.get('margin_locked', 0)) + margin)
                acc['fees_paid'] = _round_u(float(acc.get('fees_paid', 0)) + fee)
                positions[symbol] = {
                    'symbol': symbol,
                    'market': ev.get('market'),
                    'side': ev.get('side'),
                    'qty': float(ev.get('qty') or 0),
                    'entry_price': float(ev.get('price') or 0),
                    'leverage': float(ev.get('leverage') or 1),
                    'margin': margin,
                    'notional': float(ev.get('notional') or 0),
                    'strategy_version_id': ev.get('strategy_version_id'),
                    'opened_at': ev.get('created_at'),
                    'open_event_id': ev.get('event_id'),
                }
                acc['updated_at'] = ev.get('created_at') or _utcnow()
            elif et == 'close' and ev.get('status') == 'accepted':
                symbol = str(ev.get('symbol') or '').upper()
                pos = positions.pop(symbol, None)
                margin = float(ev.get('margin_released') or (pos or {}).get('margin') or 0)
                fee = float(ev.get('fee') or 0)
                gross = float(ev.get('realized_pnl_gross') or 0)
                acc['cash'] = _round_u(float(acc['cash']) + margin + gross - fee)
                acc['margin_locked'] = _round_u(float(acc.get('margin_locked', 0)) - margin)
                acc['fees_paid'] = _round_u(float(acc.get('fees_paid', 0)) + fee)
                acc['realized_pnl_gross'] = _round_u(float(acc.get('realized_pnl_gross', 0)) + gross)
                acc['updated_at'] = ev.get('created_at') or _utcnow()
            elif et == 'fee' and ev.get('status') == 'accepted':
                amount = float(ev.get('amount') or 0)
                if amount:
                    acc['cash'] = _round_u(float(acc['cash']) - amount)
                    acc['fees_paid'] = _round_u(float(acc.get('fees_paid', 0)) + amount)
                    acc['updated_at'] = ev.get('created_at') or _utcnow()
            # adjust/funding/settlement stubs: ledger only
        state['event_count'] = len(events)
        state['rebuild_note'] = (
            f'已从 events.jsonl 确定性重放 {len(events)} 条事件；'
            '禁止在有孤儿事件时静默恢复初始资金继续实验'
        )
        state['updated_at'] = _utcnow()
        return state


class Conflict(Exception):
    """Business rule conflict (duplicate, budget, same-symbol)."""


# Alias for server imports
SimConflict = Conflict
