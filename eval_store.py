"""EVAL-01 first slice: forward observation, TP/SL settle rules, grades, replay.

Research/simulation only. No live orders. No paid AI.
Depends on SIM-01 (mark via scan.json) and RULE-01 (enabled frozen strategy version).

Ambiguity rules (fixed, documented):
- same_bar_tp_sl = worse_for_trader
  同根K线同时触及止盈与止损 → 按对交易者不利成交（多空均按止损价/逻辑出场）。
- gap_through_level = fill_at_open
  跳空穿过止盈/止损价位 → 以该根可得K线开盘价成交；若穿过后无任何可得K线 → unverifiable。
- missing_bars = data_gap
  持仓窗口内无足够K线/断档导致无法核价 → data_gap，不得记为假赢；不计入胜率样本。

Observation horizon vs max hold are separate fields:
- observation_horizon_days: 前向观察期（实验停止新入场的日历跨度，首刀仅记录）
- max_hold_days: 单笔持仓期限上限（撮合/结算行走窗口）
"""
from __future__ import annotations

from durable_io import protected
import json
import math
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from snapshot_store import atomic, encoded

# --- Fixed ambiguity rules (do not change silently) ---
SAME_BAR_TP_SL = 'worse_for_trader'
GAP_THROUGH_LEVEL = 'fill_at_open'
MISSING_BARS = 'data_gap'

AMBIGUITY_RULES = {
    'same_bar_tp_sl': SAME_BAR_TP_SL,
    'same_bar_tp_sl_note': (
        '同根同时触及止盈止损 → worse_for_trader：'
        '多头与空头均按止损出场（对交易者不利），不按止盈记赢。'
    ),
    'gap_through_level': GAP_THROUGH_LEVEL,
    'gap_through_level_note': (
        '跳空穿过价位 → 以穿过该价位的那根可得K线开盘价成交；'
        '若判定需成交但之后序列不存在 → unverifiable。'
    ),
    'missing_bars': MISSING_BARS,
    'missing_bars_note': (
        '持仓已到期且期限内无K线或关键断档无法核价 → data_gap/unverifiable；'
        '未到期且未触退出 → 继续 open / pending_backfill；'
        '不计胜率样本，禁止用局部末价假装到期成交。'
    ),
    'pending_backfill_note': (
        '持仓期限未到，或到期边界/连续性未覆盖完整 → 待补查；不得结算为 max_hold/win。'
    ),
}

DISCLAIMER = '研究/模拟前向观察与结算，非实盘交易；不会向交易所下单。'


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


def _parse_iso(ts):
    if not ts or not isinstance(ts, str):
        raise ValueError('时间须为 ISO8601 字符串')
    s = ts.strip().replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f'无法解析时间: {ts}') from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ms(dt):
    return int(dt.timestamp() * 1000)


class EvalConflict(Exception):
    """Business conflict (duplicate settle, not open, etc.)."""


class EvalStore:
    """Forward observations + immutable settlements under runtime/eval/."""

    def __init__(self, root, strategy_store=None, sim_store=None, review_store=None, ranges=None, task_service=None):
        self.root = Path(root)
        self.dir = self.root / 'runtime' / 'eval'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / 'state.json'
        self.observations_path = self.dir / 'observations.jsonl'
        self.settlements_path = self.dir / 'settlements.jsonl'
        self.backfill_links_path = self.dir / 'backfill_links.jsonl'
        self.strategy_store = strategy_store
        self.sim_store = sim_store
        self.review_store = review_store
        self.ranges = ranges  # CandleRanges optional; backfill linkage still records requests
        self.task_service = task_service  # TaskService optional; auto-enqueue via submit_range
        self._scheduler_bound = False
        self._lock = threading.RLock()
        self._ensure_bootstrapped()

    def _ensure_bootstrapped(self):
        with self._lock:
            if not self.state_path.exists():
                state = self._blank_state()
                if self.observations_path.exists() or self.settlements_path.exists():
                    state = self._rebuild_from_disk()
                atomic(self.state_path, encoded(state))
            if not self.observations_path.exists():
                self.observations_path.write_text('', encoding='utf-8')
            if not self.settlements_path.exists():
                self.settlements_path.write_text('', encoding='utf-8')
            if not self.backfill_links_path.exists():
                self.backfill_links_path.write_text('', encoding='utf-8')

    def _blank_state(self):
        return {
            'schema': 'eval-state-v1',
            'open_ids': [],
            'observation_count': 0,
            'settlement_count': 0,
            'plans': {},
            'backfill_link_count': 0,
            'updated_at': _utcnow(),
            'ambiguity_rules': {
                'same_bar_tp_sl': SAME_BAR_TP_SL,
                'gap_through_level': GAP_THROUGH_LEVEL,
                'missing_bars': MISSING_BARS,
            },
            'server_effect': self._effect_from_state_static(False, [], False),
            'disclaimer': DISCLAIMER,
        }

    @staticmethod
    def _effect_from_state_static(scheduler_bound, enabled_ids, pause_active):
        return {
            'scheduler_bound': bool(scheduler_bound),
            'enabled_plan_ids': list(enabled_ids),
            'enabled_plan_count': len(enabled_ids),
            'pause_new_entries_active': bool(pause_active),
            'note': (
                'EVAL 计划已持久化；默认 enabled=false。'
                + ('调度已绑定 TaskService.tick。' if scheduler_bound else '调度未绑定。')
                + ('暂停新入场生效中，tick 不新开观察。' if pause_active else '')
            ),
        }

    def _effect_from_state(self, state):
        plans = state.get('plans') or {}
        enabled_ids = sorted(pid for pid, p in plans.items() if p.get('enabled') is True)
        pause = False
        if self.review_store is not None:
            try:
                pause = bool(self.review_store.get_pause_new_entries())
            except Exception:
                pause = False
        return self._effect_from_state_static(self._scheduler_bound, enabled_ids, pause)

    def bind_scheduler(self, bound=True):
        """Mark that TaskService (or equivalent) will call scheduler_tick."""
        with self._lock:
            self._scheduler_bound = bool(bound)
            state = self._load_state()
            state['server_effect'] = self._effect_from_state(state)
            self._save_state(state)
            return state['server_effect']

    def _read_json(self, path):
        return json.loads(path.read_text(encoding='utf-8'))

    def _load_state(self):
        return self._read_json(self.state_path)

    def _save_state(self, state):
        state['updated_at'] = _utcnow()
        state['server_effect'] = self._effect_from_state(state)
        atomic(self.state_path, encoded(state))

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

    def rules(self):
        return dict(AMBIGUITY_RULES)

    @protected
    def start_observation(self, body):
        """Start a forward observation on an enabled frozen strategy version + symbol.

        Required: strategy_version_id, symbol
        Optional: market, side, observation_horizon_days (default 30), max_hold_days (default 7),
                  tp_pct, sl_pct, interval, entry_price/mark, qty/notional_u, start_at
        """
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')
        svid = body.get('strategy_version_id')
        if not svid or not isinstance(svid, str) or not svid.startswith('ver-'):
            raise ValueError('须提供已冻结的 strategy_version_id（ver-…）')
        symbol = str(body.get('symbol') or '').strip().upper()
        if not symbol:
            raise ValueError('须指定 symbol')
        interval = body.get('interval') or '4h'
        if interval not in ('4h', '1d'):
            raise ValueError('interval 仅支持 4h / 1d')

        # V1: load enabled frozen version FIRST — exec params only from freeze
        version_meta = None
        if self.strategy_store is None:
            raise EvalConflict('未接入 strategy_store；拒绝无冻结版本开观察（HTTP 409）')
        try:
            version_meta = self.strategy_store.get_version(svid)
        except KeyError as exc:
            raise ValueError(f'冻结版本不存在: {svid}') from exc
        if not version_meta.get('enabled'):
            raise EvalConflict(f'策略版本未启用，拒绝开观察: {svid}')

        from frozen_exec import require_frozen_exec_params, FrozenExecConflict
        try:
            frozen = require_frozen_exec_params(version_meta, body, context='eval')
        except FrozenExecConflict as exc:
            raise EvalConflict(str(exc)) from exc

        exec_mode = frozen.get('execution_mode') or 'pct_touch'
        tp_pct = frozen.get('tp_pct')
        sl_pct = frozen.get('sl_pct')
        max_hold = frozen.get('max_hold_days')
        # side: frozen wins; body default long only if frozen omits side
        if frozen.get('side'):
            side = frozen['side']
        else:
            side = str(body.get('side') or 'long').strip().lower()
            if side not in ('long', 'short'):
                raise ValueError('side 仅支持 long / short')

        horizon = body.get('observation_horizon_days', 30)
        horizon = int(_finite('observation_horizon_days', horizon))
        if horizon < 1 or horizon > 3650:
            raise ValueError('observation_horizon_days 须在 1–3650')
        # Explicit separation: horizon != max_hold allowed; hold comes from freeze only
        # ema99：候选30天 ≠ 持仓期限；未声明 max_hold_days 时不得发明天数

        # Entry mark
        market = body.get('market')
        if body.get('entry_price') is not None:
            entry = _finite('entry_price', body['entry_price'])
            if entry <= 0:
                raise ValueError('entry_price 须 > 0')
            price_source = 'client'
            mark_as_of = body.get('start_at') or _utcnow()
        elif body.get('mark') is not None:
            entry = _finite('mark', body['mark'])
            if entry <= 0:
                raise ValueError('mark 须 > 0')
            price_source = 'client'
            mark_as_of = body.get('start_at') or _utcnow()
        elif self.sim_store is not None:
            info = self.sim_store.resolve_mark(symbol, market)
            entry = float(info['mark'])
            price_source = info.get('price_field') or 'scan'
            mark_as_of = info.get('as_of') or _utcnow()
            market = market or info.get('market')
        else:
            raise ValueError('缺少入场价：请提供 entry_price/mark，或接入 scan.json（SIM-01）')

        qty = None
        notional_u = None
        if body.get('qty') is not None:
            qty = _finite('qty', body['qty'])
            if qty <= 0:
                raise ValueError('qty 须 > 0')
            notional_u = qty * entry
        elif body.get('notional_u') is not None:
            notional_u = _finite('notional_u', body['notional_u'])
            if notional_u <= 0:
                raise ValueError('notional_u 须 > 0')
            qty = notional_u / entry
        elif frozen.get('notional_u') is not None:
            notional_u = float(frozen['notional_u'])
            qty = notional_u / entry
        else:
            raise EvalConflict(
                '冻结版本未声明 notional_u，且请求未提供 qty/notional_u；'
                '拒绝发明默认 100U（HTTP 409）'
            )

        start_at = body.get('start_at') or _utcnow()
        start_dt = _parse_iso(start_at)

        # 分支：pct_touch 固定止盈止损价；ema99_close_trail 用金叉根 EMA99 初始止损+收盘追踪
        trail_state = None
        pending_exit = False
        pending_exit_reason = None
        tp_price = None
        sl_price = None
        if exec_mode == 'ema99_close_trail':
            # 前端放行 ≠ 引擎通过：缺确认事件 / 金叉根 EMA99 明确拒绝，不发明 3% 触价
            confirm = (
                body.get('confirm_event_id')
                or body.get('trigger_evidence')
                or body.get('signal_id')
                or body.get('confirmed_signal')
            )
            if not confirm:
                raise EvalConflict(
                    'ema99_close_trail 开观察缺少确认事件/触发证据'
                    '（confirm_event_id / trigger_evidence）；拒绝假装已可入场（HTTP 409）'
                )
            cross_ema99 = body.get('cross_ema99')
            if cross_ema99 is None:
                cross_ema99 = body.get('initial_stop_price')
            if cross_ema99 is None:
                raise EvalConflict(
                    'ema99_close_trail 开观察须提供 cross_ema99（金叉根EMA99）作为初始止损；'
                    '禁止用固定百分比发明止损（HTTP 409）'
                )
            from ema_squeeze_rules import initial_stop_state
            trail_state = initial_stop_state(
                cross_ema99=cross_ema99,
                actual_entry_price=entry,
                candidate_cycle_id=body.get('candidate_cycle_id'),
                cross_time=body.get('cross_time'),
                confirm_time=body.get('confirm_time'),
                entry_time=start_at,
            )
            sl_price = float(trail_state['stop'])  # 展示用：当前有效止损=初始金叉根
        else:
            if tp_pct is None or sl_pct is None:
                raise EvalConflict('pct_touch 模式冻结包缺少 tp_pct/sl_pct')
            if max_hold is None:
                raise EvalConflict('pct_touch 模式冻结包缺少 max_hold_days')
            if side == 'long':
                tp_price = entry * (1 + float(tp_pct))
                sl_price = entry * (1 - float(sl_pct))
            else:
                tp_price = entry * (1 - float(tp_pct))
                sl_price = entry * (1 + float(sl_pct))

        obs_id = body.get('observation_id') or _new_id('obs')
        if not isinstance(obs_id, str) or not obs_id.startswith('obs-'):
            raise ValueError('observation_id 须为 obs-…')

        with self._lock:
            state = self._load_state()
            existing_ids = {r.get('observation_id') for r in self._read_jsonl(self.observations_path)}
            if obs_id in existing_ids:
                raise EvalConflict(f'重复观察已拒绝: {obs_id}')

            obs_ends = (start_dt + timedelta(days=horizon)).replace(microsecond=0).isoformat()
            if max_hold is not None:
                hold_ends = (start_dt + timedelta(days=int(max_hold))).replace(microsecond=0).isoformat()
            else:
                hold_ends = None  # ema99：无固定持仓天；结算窗用观察期

            obs = {
                'observation_id': obs_id,
                'schema': 'eval-observation-v1',
                'status': 'open',
                'strategy_version_id': svid,
                'version_content_hash': version_meta.get('content_hash'),
                'execution_mode': exec_mode,
                'frozen_exec': {
                    'execution_mode': exec_mode,
                    'tp_pct': tp_pct, 'sl_pct': sl_pct,
                    'max_hold_days': max_hold, 'side': side,
                    'entry_rule': frozen.get('entry_rule'),
                    'initial_stop': frozen.get('initial_stop'),
                    'trail_activation': frozen.get('trail_activation'),
                    'intrabar_stop': frozen.get('intrabar_stop'),
                    'stop_style': frozen.get('stop_style'),
                    'candidate_max_days': frozen.get('candidate_max_days'),
                    'params_source': 'frozen_version.rules.params',
                },
                'strategy_title': (version_meta or {}).get('title'),
                'symbol': symbol,
                'market': market,
                'side': side,
                'interval': interval,
                'entry_price': _round_u(entry),
                'qty': _round_u(qty),
                'notional_u': _round_u(notional_u),
                'tp_pct': tp_pct,
                'sl_pct': sl_pct,
                'tp_price': _round_u(tp_price) if tp_price is not None else None,
                'sl_price': _round_u(sl_price) if sl_price is not None else None,
                'initial_stop': _round_u(trail_state['stop']) if trail_state else (_round_u(sl_price) if sl_price is not None else None),
                'current_stop': _round_u(trail_state['stop']) if trail_state else (_round_u(sl_price) if sl_price is not None else None),
                'trail_state': trail_state,
                'trailing_activated': False if trail_state else None,
                'pending_exit': pending_exit,
                'pending_exit_reason': pending_exit_reason,
                'confirm_event_id': body.get('confirm_event_id') or body.get('trigger_evidence') or body.get('signal_id'),
                'cross_ema99': body.get('cross_ema99') or body.get('initial_stop_price'),
                # Separated horizons — do not conflate
                'observation_horizon_days': horizon,
                'max_hold_days': max_hold,
                'observation_ends_at': obs_ends,
                'max_hold_ends_at': hold_ends,
                'started_at': start_dt.replace(microsecond=0).isoformat(),
                'start_snapshot_time': mark_as_of,
                'price_source': price_source,
                'ambiguity_rules': {
                    'same_bar_tp_sl': SAME_BAR_TP_SL,
                    'gap_through_level': GAP_THROUGH_LEVEL,
                    'missing_bars': MISSING_BARS,
                    'ema99_exit': 'close_below_stop_then_next_open' if exec_mode == 'ema99_close_trail' else None,
                },
                'disclaimer': DISCLAIMER,
                'created_at': _utcnow(),
            }
            self._append_jsonl(self.observations_path, {
                'event': 'start',
                **obs,
            })
            open_ids = list(state.get('open_ids') or [])
            open_ids.append(obs_id)
            state['open_ids'] = open_ids
            state['observation_count'] = int(state.get('observation_count') or 0) + 1
            self._save_state(state)
            return obs

    def list_observations(self, status=None, limit=100):
        limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self._read_jsonl(self.observations_path)
            # Latest record per observation_id (start or settle update)
            by_id = {}
            for r in rows:
                oid = r.get('observation_id')
                if oid:
                    by_id[oid] = r
            items = list(by_id.values())
            if status:
                items = [x for x in items if x.get('status') == status]
            items.sort(key=lambda x: x.get('created_at') or x.get('started_at') or '', reverse=True)
            return {
                'observations': items[:limit],
                'count': len(items[:limit]),
                'total_tracked': len(items),
                'open_count': sum(1 for x in items if x.get('status') == 'open'),
                'ambiguity_rules': self.rules(),
                'disclaimer': DISCLAIMER,
            }

    def get_observation(self, observation_id):
        with self._lock:
            rows = self._read_jsonl(self.observations_path)
            found = None
            for r in rows:
                if r.get('observation_id') == observation_id:
                    found = r
            if not found:
                raise KeyError(observation_id)
            return found

    def list_settlements(self, limit=100):
        limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self._read_jsonl(self.settlements_path)
            rows = rows[-limit:]
            rows.reverse()
            return {
                'settlements': rows,
                'count': len(rows),
                'disclaimer': DISCLAIMER,
            }

    def grades(self, strategy_version_id=None):
        """Honest grades: win_rate only when sample_count > 0; else 无样本."""
        with self._lock:
            rows = self._read_jsonl(self.settlements_path)
            if strategy_version_id:
                rows = [r for r in rows if r.get('strategy_version_id') == strategy_version_id]

            # Samples for win rate: only verified settled with clear win/loss (not data_gap/unverifiable)
            wins = 0
            losses = 0
            flats = 0
            data_gap = 0
            unverifiable = 0
            settled = 0
            for r in rows:
                settled += 1
                outcome = r.get('outcome')
                if outcome == 'win':
                    wins += 1
                elif outcome == 'loss':
                    losses += 1
                elif outcome == 'flat':
                    flats += 1
                elif outcome == 'data_gap':
                    data_gap += 1
                elif outcome == 'unverifiable':
                    unverifiable += 1

            sample_count = wins + losses + flats  # verifiable PnL samples only
            if sample_count > 0:
                win_rate = wins / sample_count
                win_rate_label = f'{win_rate * 100:.2f}%'
            else:
                win_rate = None
                win_rate_label = '无样本'

            return {
                'strategy_version_id': strategy_version_id,
                'settlement_count': settled,
                'sample_count': sample_count,
                'wins': wins,
                'losses': losses,
                'flats': flats,
                'data_gap_count': data_gap,
                'unverifiable_count': unverifiable,
                'win_rate': win_rate,
                'win_rate_label': win_rate_label,
                'note': (
                    '胜率仅统计 outcome 为 win/loss/flat 的可核价样本；'
                    'data_gap / unverifiable 不计入分母，也不记假赢。'
                    if sample_count > 0
                    else '尚无可用样本，显示「无样本」，不填 0% 胜率。'
                ),
                'disclaimer': DISCLAIMER,
            }

    def settle(self, observation_id, body=None):
        """Close observation: manual mark, or rules walk with bars / price_index.

        body.mode: 'manual' | 'rules' (default rules if bars provided or loadable)
        body.bars: optional list of OHLCV raw bars [[t,o,h,l,c,...], ...]
        body.exit_price / mark: for manual
        body.exit_reason: optional override label for manual
        """
        body = body if isinstance(body, dict) else {}
        with self._lock:
            obs = self.get_observation(observation_id)
            if obs.get('status') != 'open':
                raise EvalConflict(f'观察非 open 状态，拒绝重复结算: {obs.get("status")}')

            mode = body.get('mode') or ('manual' if (body.get('exit_price') is not None or body.get('mark') is not None) and body.get('bars') is None else 'rules')
            if mode == 'manual':
                settlement = self._settle_manual(obs, body)
            elif mode == 'rules':
                settlement = self._settle_rules(obs, body)
            else:
                raise ValueError('mode 仅支持 manual / rules')

            # F02: not yet expired / incomplete window → keep open; do not fake max_hold
            if settlement.get('pending') or settlement.get('observation_status') == 'open':
                pending_view = dict(obs)
                pending_view['status'] = 'open'
                pending_view['pending_reason'] = settlement.get('exit_reason') or 'pending_backfill'
                pending_view['pending_detail'] = settlement.get('detail')
                pending_view['event'] = 'settle_check'
                pending_view['checked_at'] = settlement.get('created_at') or _utcnow()
                detail = settlement.get('detail') or {}
                if isinstance(detail.get('trail_state'), dict):
                    pending_view['trail_state'] = detail['trail_state']
                    pending_view['current_stop'] = detail.get('current_stop')
                    pending_view['trailing_activated'] = detail.get('trailing_activated')
                    pending_view['pending_exit'] = detail.get('pending_exit')
                    pending_view['pending_exit_reason'] = detail.get('pending_exit_reason')
                self._append_jsonl(self.observations_path, pending_view)
                backfill_link = None
                reason = settlement.get('exit_reason') or ''
                if reason in ('pending_backfill', 'unverifiable', 'data_gap'):
                    backfill_link = self._link_backfill(obs, reason=reason, detail=settlement.get('detail'))
                    if backfill_link:
                        pending_view['backfill_link_id'] = backfill_link.get('link_id')
                return {
                    'settlement': None,
                    'pending': settlement,
                    'observation': pending_view,
                    'backfill_link': backfill_link,
                    'note': settlement.get('detail', {}).get('note') or '未到期或覆盖不完整；观察保持 open',
                }

            # Append immutable settlement
            if settlement.get('settlement_id') in {r.get('settlement_id') for r in self._read_jsonl(self.settlements_path)}:
                raise EvalConflict(f'重复结算已拒绝: {settlement["settlement_id"]}')

            self._append_jsonl(self.settlements_path, settlement)

            # Append observation status update (latest wins in list)
            closed = dict(obs)
            closed['status'] = settlement.get('observation_status') or settlement.get('outcome') or 'settled'
            if closed['status'] in ('win', 'loss', 'flat'):
                closed['status'] = 'settled'
            closed['settlement_id'] = settlement['settlement_id']
            closed['settled_at'] = settlement['created_at']
            closed['exit_reason'] = settlement.get('exit_reason')
            closed['outcome'] = settlement.get('outcome')
            closed['event'] = 'settle'
            self._append_jsonl(self.observations_path, closed)

            state = self._load_state()
            open_ids = [x for x in (state.get('open_ids') or []) if x != observation_id]
            state['open_ids'] = open_ids
            state['settlement_count'] = int(state.get('settlement_count') or 0) + 1
            self._save_state(state)
            backfill_link = None
            if settlement.get('outcome') in ('unverifiable', 'data_gap') or settlement.get('exit_reason') in ('unverifiable', 'data_gap'):
                backfill_link = self._link_backfill(obs, reason=settlement.get('exit_reason') or settlement.get('outcome'), detail=settlement.get('detail'))
            out = {'settlement': settlement, 'observation': closed}
            if backfill_link:
                out['backfill_link'] = backfill_link
            return out

    def _settle_manual(self, obs, body):
        if body.get('exit_price') is not None:
            exit_price = _finite('exit_price', body['exit_price'])
        elif body.get('mark') is not None:
            exit_price = _finite('mark', body['mark'])
        elif self.sim_store is not None:
            info = self.sim_store.resolve_mark(obs['symbol'], obs.get('market'))
            exit_price = float(info['mark'])
        else:
            raise ValueError('手动结算须提供 exit_price 或 mark')
        if exit_price <= 0:
            raise ValueError('exit_price 须 > 0')
        reason = body.get('exit_reason') or 'manual'
        return self._build_settlement(obs, exit_price=exit_price, exit_reason=reason, fill_rule='manual', detail={'mode': 'manual'})


    @staticmethod
    def _continuity_through(window, through_index=None):
        """True if bar open times are continuous up to through_index (inclusive).

        Gaps in the hold window must not allow a silent win/loss (断档判赢).
        """
        if not window or len(window) < 2:
            return True
        end = len(window) - 1 if through_index is None else int(through_index)
        end = max(0, min(end, len(window) - 1))
        if end < 1:
            return True
        step = int(window[1][0]) - int(window[0][0])
        if step <= 0:
            step = 4 * 3600 * 1000
        for i in range(end):
            dt = int(window[i + 1][0]) - int(window[i][0])
            if dt > step * 1.5:
                return False
        return True

    def _settle_rules(self, obs, body):
        mode = (obs.get('execution_mode')
                or (obs.get('frozen_exec') or {}).get('execution_mode')
                or 'pct_touch')
        if mode == 'ema99_close_trail':
            return self._settle_ema99_trail(obs, body)

        bars = body.get('bars')
        bars_source = 'client'
        if bars is None:
            bars, bars_source = self._load_bars_for(obs)
        if not isinstance(bars, list):
            raise ValueError('bars 须为数组')

        start_ms = _ms(_parse_iso(obs['started_at']))
        if not obs.get('max_hold_ends_at'):
            raise EvalConflict('pct_touch 观察缺少 max_hold_ends_at；拒绝结算')
        hold_end_ms = _ms(_parse_iso(obs['max_hold_ends_at']))
        # Filter bars whose open time is within (start, max_hold_end]
        # Entry is at started_at; first actionable bar is open_time >= start (or > start)
        window = []
        for bar in bars:
            if not isinstance(bar, (list, tuple)) or len(bar) < 5:
                continue
            t = bar[0]
            if not isinstance(t, (int, float)):
                continue
            t = int(t)
            if t < start_ms:
                continue
            if t > hold_end_ms:
                continue
            window.append(bar)

        as_of_raw = body.get('as_of')
        if as_of_raw:
            as_of_dt = _parse_iso(as_of_raw)
        else:
            as_of_dt = datetime.now(timezone.utc)
        as_of_ms = _ms(as_of_dt)
        hold_reached = as_of_ms >= hold_end_ms

        if not window:
            if not hold_reached:
                # Still inside hold horizon — keep open; wait for bars / backfill
                return self._build_settlement(
                    obs,
                    exit_price=None,
                    exit_reason='pending_backfill',
                    outcome='pending_backfill',
                    fill_rule='hold_not_reached',
                    detail={
                        'mode': 'rules',
                        'bars_source': bars_source,
                        'bars_in_window': 0,
                        'as_of': as_of_dt.isoformat(),
                        'hold_reached': False,
                        'note': '持仓期限未到且暂无K线；保持 open，不得 data_gap/max_hold 假结算',
                    },
                    observation_status='open',
                    pending=True,
                )
            # Past hold end with zero bars → data_gap (not a fake win)
            return self._build_settlement(
                obs,
                exit_price=None,
                exit_reason='data_gap',
                outcome='data_gap',
                fill_rule='missing_bars',
                detail={
                    'mode': 'rules',
                    'bars_source': bars_source,
                    'bars_in_window': 0,
                    'as_of': as_of_dt.isoformat(),
                    'hold_reached': True,
                    'note': '持仓已到期且期限内无可得K线，标记 data_gap，不计胜率',
                },
            )

        side = obs['side']
        tp = float(obs['tp_price'])
        sl = float(obs['sl_price'])
        entry = float(obs['entry_price'])

        hit = None  # (exit_price, reason, fill_rule, bar_time, extra, bar_index)
        for bar_index, bar in enumerate(window):
            t, o, h, l, c = int(bar[0]), float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4])
            # Gap-through at open
            if side == 'long':
                gap_sl = o <= sl and o < entry  # opened at/through SL
                gap_tp = o >= tp and o > entry
                touch_sl = l <= sl
                touch_tp = h >= tp
            else:
                gap_sl = o >= sl and o > entry
                gap_tp = o <= tp and o < entry
                touch_sl = h >= sl
                touch_tp = l <= tp

            # Prefer gap fill at open when open already through level
            if gap_sl and gap_tp:
                # Pathological open through both — worse for trader → SL at open
                hit = (o, 'stop_loss', 'gap_through_level+same_bar_worse', t, {'open': o, 'gap': True}, bar_index)
                break
            if gap_sl:
                hit = (o, 'stop_loss', 'gap_through_level', t, {'open': o, 'gap': True}, bar_index)
                break
            if gap_tp:
                hit = (o, 'take_profit', 'gap_through_level', t, {'open': o, 'gap': True}, bar_index)
                break

            if touch_sl and touch_tp:
                # Same bar both → worse_for_trader → stop loss
                # Fill at SL price (level), not the favorable TP
                hit = (sl, 'stop_loss', 'same_bar_tp_sl:worse_for_trader', t, {'high': h, 'low': l}, bar_index)
                break
            if touch_sl:
                hit = (sl, 'stop_loss', 'intrabar_level', t, {'low': l, 'high': h}, bar_index)
                break
            if touch_tp:
                hit = (tp, 'take_profit', 'intrabar_level', t, {'low': l, 'high': h}, bar_index)
                break

        if hit is None:
            last = window[-1]
            last_t = int(last[0])
            # Continuity: detect large gaps inside window (if >=2 bars)
            continuity_ok = True
            gap_ms = None
            if len(window) >= 2:
                # Infer step from first delta; allow 1.5x
                step = int(window[1][0]) - int(window[0][0])
                if step <= 0:
                    step = 4 * 3600 * 1000
                for a, b in zip(window, window[1:]):
                    dt = int(b[0]) - int(a[0])
                    if dt > step * 1.5:
                        continuity_ok = False
                        gap_ms = dt
                        break

            # Coverage of hold end: last bar must reach near max_hold_ends_at
            # (within one inferred step). Partial local window must not fake expiry.
            step_guess = 4 * 3600 * 1000
            if len(window) >= 2:
                step_guess = max(1, int(window[1][0]) - int(window[0][0]))
            covers_hold_end = last_t + step_guess >= hold_end_ms

            if not hold_reached:
                return self._build_settlement(
                    obs,
                    exit_price=None,
                    exit_reason='pending_backfill',
                    outcome='pending_backfill',
                    fill_rule='hold_not_reached',
                    detail={
                        'mode': 'rules',
                        'bars_source': bars_source,
                        'bars_in_window': len(window),
                        'last_bar_time': last_t,
                        'as_of': as_of_dt.isoformat(),
                        'hold_reached': False,
                        'note': '持仓期限未到且未触 TP/SL；保持 open，禁止用局部末价假装到期',
                    },
                    observation_status='open',
                    pending=True,
                )

            if (not covers_hold_end) or (not continuity_ok):
                return self._build_settlement(
                    obs,
                    exit_price=None,
                    exit_reason='unverifiable',
                    outcome='unverifiable',
                    fill_rule='incomplete_hold_window',
                    detail={
                        'mode': 'rules',
                        'bars_source': bars_source,
                        'bars_in_window': len(window),
                        'last_bar_time': last_t,
                        'as_of': as_of_dt.isoformat(),
                        'hold_reached': True,
                        'covers_hold_end': covers_hold_end,
                        'continuity_ok': continuity_ok,
                        'gap_ms': gap_ms,
                        'note': '已过持仓期限但窗口未覆盖到期边界或存在断档；标不可验证/待补查，不得假 max_hold/win',
                    },
                )

            # Verified max hold: full window + continuity, no TP/SL
            exit_price = float(last[4])
            return self._build_settlement(
                obs,
                exit_price=exit_price,
                exit_reason='max_hold',
                fill_rule='max_hold_last_close',
                detail={
                    'mode': 'rules',
                    'bars_source': bars_source,
                    'bars_in_window': len(window),
                    'last_bar_time': last_t,
                    'as_of': as_of_dt.isoformat(),
                    'hold_reached': True,
                    'covers_hold_end': True,
                    'continuity_ok': True,
                    'note': '持仓到期未触 TP/SL，窗口完整可核验，按最后一根收盘价结算',
                },
            )

        exit_price, reason, fill_rule, bar_t, extra, hit_index = hit
        # 断档不得判赢/判亏：触价前窗口须连续；否则 pending 或 unverifiable
        if not self._continuity_through(window, hit_index):
            if not hold_reached:
                return self._build_settlement(
                    obs,
                    exit_price=None,
                    exit_reason='pending_backfill',
                    outcome='pending_backfill',
                    fill_rule='gap_before_hit',
                    detail={
                        'mode': 'rules',
                        'bars_source': bars_source,
                        'bars_in_window': len(window),
                        'hit_bar_time': bar_t,
                        'hit_index': hit_index,
                        'as_of': as_of_dt.isoformat(),
                        'hold_reached': False,
                        'continuity_ok': False,
                        'suppressed_exit_reason': reason,
                        'note': '触价前存在断档；保持 open 待补查，禁止断档判赢/判亏（F02）',
                    },
                    observation_status='open',
                    pending=True,
                )
            return self._build_settlement(
                obs,
                exit_price=None,
                exit_reason='unverifiable',
                outcome='unverifiable',
                fill_rule='gap_before_hit',
                detail={
                    'mode': 'rules',
                    'bars_source': bars_source,
                    'bars_in_window': len(window),
                    'hit_bar_time': bar_t,
                    'hit_index': hit_index,
                    'as_of': as_of_dt.isoformat(),
                    'hold_reached': True,
                    'continuity_ok': False,
                    'suppressed_exit_reason': reason,
                    'note': '已到期但触价前断档；标不可验证，不计胜率、不得假赢',
                },
            )
        return self._build_settlement(
            obs,
            exit_price=exit_price,
            exit_reason=reason,
            fill_rule=fill_rule,
            detail={
                'mode': 'rules',
                'bars_source': bars_source,
                'bars_in_window': len(window),
                'hit_bar_time': bar_t,
                'continuity_ok': True,
                **extra,
            },
        )


    def _settle_ema99_trail(self, obs, body):
        """EMA99 收盘追踪：不按固定 tp/sl 触价；收盘跌破止损 → 下一开盘出场。"""
        from backtest_store import resolve_ema99_trail_bar
        body = body if isinstance(body, dict) else {}
        bars = body.get('bars')
        bars_source = 'client'
        if bars is None:
            bars, bars_source = self._load_bars_for(obs)
        if not isinstance(bars, list):
            raise ValueError('bars 须为数组')

        start_ms = _ms(_parse_iso(obs['started_at']))
        end_iso = obs.get('observation_ends_at') or obs.get('max_hold_ends_at')
        if not end_iso:
            raise EvalConflict('ema99 观察缺少 observation_ends_at')
        hold_end_ms = _ms(_parse_iso(end_iso))

        as_of_raw = body.get('as_of')
        as_of_dt = _parse_iso(as_of_raw) if as_of_raw else None
        if as_of_dt is None:
            from datetime import datetime, timezone
            as_of_dt = datetime.now(timezone.utc)
        as_of_ms = _ms(as_of_dt)
        hold_reached = as_of_ms >= hold_end_ms

        window = []
        for bar in bars:
            if not isinstance(bar, (list, tuple)) or len(bar) < 5:
                continue
            t = bar[0]
            if not isinstance(t, (int, float)):
                continue
            t = int(t)
            if t < start_ms:
                continue
            if t > hold_end_ms:
                continue
            window.append(bar)

        ema_map = body.get('ema99_by_time') if isinstance(body.get('ema99_by_time'), dict) else {}
        ema_series = body.get('ema99_series') if isinstance(body.get('ema99_series'), list) else None

        def ema_for(bar, idx):
            t = int(bar[0])
            if str(t) in ema_map:
                return ema_map[str(t)]
            if t in ema_map:
                return ema_map[t]
            if ema_series is not None and idx < len(ema_series):
                return ema_series[idx]
            if len(bar) >= 6 and bar[5] is not None:
                return bar[5]
            return None

        if not window:
            if not hold_reached:
                return self._build_settlement(
                    obs, exit_price=None, exit_reason='pending_backfill', outcome='pending_backfill',
                    fill_rule='hold_not_reached',
                    detail={'mode': 'ema99_close_trail', 'bars_source': bars_source, 'bars_in_window': 0,
                            'note': '观察期未到且无K线；保持 open，不发明触价退出'},
                    observation_status='open', pending=True,
                )
            return self._build_settlement(
                obs, exit_price=None, exit_reason='data_gap', outcome='data_gap',
                fill_rule='missing_bars',
                detail={'mode': 'ema99_close_trail', 'bars_source': bars_source, 'bars_in_window': 0,
                        'note': '观察期已到且无K线；data_gap，不计假赢'},
            )

        trail_state = obs.get('trail_state')
        if not isinstance(trail_state, dict):
            raise EvalConflict('ema99 观察缺少 trail_state；拒绝用百分比止损顶替')

        pos = {
            'trail_state': dict(trail_state),
            'pending_exit': bool(obs.get('pending_exit')),
            'pending_exit_reason': obs.get('pending_exit_reason'),
            'stop': (trail_state or {}).get('stop'),
            'trailing_activated': bool((trail_state or {}).get('trailing_activated')),
        }

        hit = None
        missing_ema = False
        for idx, bar in enumerate(window):
            e99 = ema_for(bar, idx)
            if e99 is None and not pos.get('pending_exit'):
                missing_ema = True
                break
            pos, hit = resolve_ema99_trail_bar(pos, bar, e99)
            if hit is not None:
                break

        detail_base = {
            'mode': 'ema99_close_trail',
            'bars_source': bars_source,
            'bars_in_window': len(window),
            'execution_mode': 'ema99_close_trail',
            'initial_stop': obs.get('initial_stop'),
            'current_stop': pos.get('stop'),
            'trailing_activated': pos.get('trailing_activated'),
            'pending_exit': pos.get('pending_exit'),
            'pending_exit_reason': pos.get('pending_exit_reason'),
            'trail_state': pos.get('trail_state'),
        }

        if missing_ema:
            return self._build_settlement(
                obs, exit_price=None, exit_reason='pending_backfill', outcome='pending_backfill',
                fill_rule='missing_ema99',
                detail={**detail_base, 'note': '缺少逐根 ema99；保持 open，前端放行≠引擎发明止损',
                        'as_of': as_of_dt.isoformat()},
                observation_status='open', pending=True,
            )

        if hit is not None:
            return self._build_settlement(
                obs,
                exit_price=hit['exit_price'],
                exit_reason=hit.get('exit_reason') or 'ema99_close_trail',
                fill_rule=hit.get('fill_rule') or 'next_open_after_close_exit_signal',
                detail={**detail_base, 'hit_bar_time': hit.get('bar_time'), 'as_of': as_of_dt.isoformat(),
                        'note': '收盘跌破止损后于下一开盘出场；非盘中3%触价'},
            )

        if pos.get('pending_exit'):
            return self._build_settlement(
                obs, exit_price=None, exit_reason='pending_next_open_exit', outcome='pending_next_open_exit',
                fill_rule='await_next_open',
                detail={**detail_base, 'as_of': as_of_dt.isoformat(),
                        'note': '已收盘跌破止损，等待下一可执行开盘出场'},
                observation_status='open', pending=True,
            )

        if not hold_reached:
            return self._build_settlement(
                obs, exit_price=None, exit_reason='pending_backfill', outcome='pending_backfill',
                fill_rule='hold_not_reached',
                detail={**detail_base, 'as_of': as_of_dt.isoformat(),
                        'note': '观察期未到且未触发收盘止损；保持 open'},
                observation_status='open', pending=True,
            )

        last = window[-1]
        return self._build_settlement(
            obs,
            exit_price=float(last[4]),
            exit_reason='observation_horizon_end',
            fill_rule='horizon_last_close',
            detail={**detail_base, 'last_bar_time': int(last[0]), 'as_of': as_of_dt.isoformat(),
                    'note': '观察期结束未触发 EMA99 收盘止损；按最后收盘结束，非3%触价'},
        )


    def _load_bars_for(self, obs):
        """Load merged OHLC bars from price_index for the hold window."""
        try:
            from price_index import merge_series
        except ImportError:
            return [], 'unavailable'
        market = obs.get('market') or 'USDⓈ-M'
        symbol = obs['symbol']
        interval = obs.get('interval') or '4h'
        start = obs['started_at']
        # extend end slightly past max_hold for inclusivity
        end = obs['max_hold_ends_at']
        try:
            detail = merge_series(
                self.root, market, symbol, interval,
                start=start, end=end, include_bars=True,
            )
        except ValueError:
            return [], 'price_index_error'
        bars = detail.get('bars') or []
        if not bars and not detail.get('current_available'):
            return [], 'empty'
        return bars, 'price_index'

    def _build_settlement(self, obs, exit_price, exit_reason, fill_rule, detail=None, outcome=None,
                          observation_status=None, pending=False):
        entry = float(obs['entry_price'])
        qty = float(obs['qty'])
        side = obs['side']
        sid = _new_id('set')

        non_pnl = ('data_gap', 'unverifiable', 'pending_backfill')
        if outcome in non_pnl or (exit_price is None and outcome is not None):
            outcome = outcome or 'data_gap'
            pnl_gross = None
            pnl_pct = None
            exit_price_out = None
        elif exit_price is None:
            outcome = outcome or 'data_gap'
            pnl_gross = None
            pnl_pct = None
            exit_price_out = None
        else:
            exit_price_out = _round_u(exit_price)
            if side == 'long':
                pnl_gross = (exit_price - entry) * qty
            else:
                pnl_gross = (entry - exit_price) * qty
            pnl_gross = _round_u(pnl_gross)
            pnl_pct = _round_u((exit_price - entry) / entry if side == 'long' else (entry - exit_price) / entry)
            if pnl_gross > 1e-12:
                outcome = 'win'
            elif pnl_gross < -1e-12:
                outcome = 'loss'
            else:
                outcome = 'flat'

        if observation_status is None:
            if pending or outcome == 'pending_backfill':
                observation_status = 'open'
            elif outcome in ('win', 'loss', 'flat'):
                observation_status = 'settled'
            else:
                observation_status = outcome

        return {
            'settlement_id': sid,
            'schema': 'eval-settlement-v1',
            'immutable': not pending,
            'pending': bool(pending),
            'observation_id': obs['observation_id'],
            'strategy_version_id': obs.get('strategy_version_id'),
            'symbol': obs['symbol'],
            'market': obs.get('market'),
            'side': side,
            'entry_price': _round_u(entry),
            'exit_price': exit_price_out,
            'qty': _round_u(qty),
            'realized_pnl_gross': pnl_gross,
            'pnl_pct': pnl_pct,
            'outcome': outcome,
            'exit_reason': exit_reason,
            'fill_rule': fill_rule,
            'observation_horizon_days': obs.get('observation_horizon_days'),
            'max_hold_days': obs.get('max_hold_days'),
            'ambiguity_rules': {
                'same_bar_tp_sl': SAME_BAR_TP_SL,
                'gap_through_level': GAP_THROUGH_LEVEL,
                'missing_bars': MISSING_BARS,
            },
            'detail': detail or {},
            'observation_status': observation_status,
            'created_at': _utcnow(),
            'disclaimer': DISCLAIMER,
            'note': '不可变结算记录；研究/模拟非实盘' if not pending else '未决检查；观察仍 open',
        }


    # --- plans / backfill / scheduler (EVAL-01 deepen) ---

    def status(self):
        with self._lock:
            state = self._load_state()
            effect = self._effect_from_state(state)
            return {
                'open_count': len(state.get('open_ids') or []),
                'observation_count': int(state.get('observation_count') or 0),
                'settlement_count': int(state.get('settlement_count') or 0),
                'backfill_link_count': int(state.get('backfill_link_count') or 0),
                'plans': list((state.get('plans') or {}).values()),
                'server_effect': effect,
                'ambiguity_rules': AMBIGUITY_RULES,
                'disclaimer': DISCLAIMER,
            }

    def list_plans(self):
        with self._lock:
            state = self._load_state()
            plans = list((state.get('plans') or {}).values())
            plans.sort(key=lambda p: p.get('created_at') or '')
            return {
                'plans': plans,
                'server_effect': self._effect_from_state(state),
                'disclaimer': DISCLAIMER,
            }

    @protected
    def save_plan(self, body):
        """Persist forward-observation schedule. Default enabled=false (must explicit enable)."""
        if not isinstance(body, dict):
            raise ValueError('请求体须为对象')
        svid = body.get('strategy_version_id')
        if not svid or not isinstance(svid, str) or not svid.startswith('ver-'):
            raise ValueError('须提供 strategy_version_id（ver-…）')
        symbols = body.get('symbols') or []
        if isinstance(symbols, str):
            symbols = [symbols]
        if not isinstance(symbols, list) or not symbols:
            raise ValueError('须提供 symbols 列表（至少 1 个）')
        cleaned_syms = []
        for s in symbols:
            if not isinstance(s, str) or not s.strip():
                raise ValueError('symbol 须为非空字符串')
            cleaned_syms.append(s.strip().upper())
        if len(cleaned_syms) > 20:
            raise ValueError('单计划最多 20 个合约')
        interval = body.get('interval') or '4h'
        if interval not in ('4h', '1d'):
            raise ValueError('interval 仅支持 4h / 1d')
        check_minutes = int(body.get('check_interval_minutes') or 60)
        if check_minutes < 1 or check_minutes > 24 * 60:
            raise ValueError('check_interval_minutes 须在 1–1440')
        enabled = body.get('enabled')
        if enabled is None:
            enabled = False
        if not isinstance(enabled, bool):
            raise ValueError('enabled 须为布尔；默认 false')
        side = body.get('side') or 'long'
        if side not in ('long', 'short'):
            raise ValueError('side 仅支持 long / short')
        horizon = int(body.get('observation_horizon_days') or 30)
        max_hold = int(body.get('max_hold_days') or 7)
        market = body.get('market') or 'USDⓈ-M'
        auto_backfill = body.get('auto_backfill_on_gap')
        if auto_backfill is None:
            auto_backfill = True
        if not isinstance(auto_backfill, bool):
            raise ValueError('auto_backfill_on_gap 须为布尔')
        auto_enqueue = body.get('auto_enqueue_backfill')
        if auto_enqueue is None:
            auto_enqueue = False  # default OFF — must set true on plan explicitly
        if not isinstance(auto_enqueue, bool):
            raise ValueError('auto_enqueue_backfill 须为布尔；默认 false')

        if self.strategy_store is not None:
            try:
                ver = self.strategy_store.get_version(svid)
            except KeyError as exc:
                raise ValueError(f'冻结版本不存在: {svid}') from exc
            if not ver.get('enabled'):
                raise ValueError(f'策略版本未启用，拒绝写入观察计划: {svid}')

        with self._lock:
            state = self._load_state()
            plans = state.setdefault('plans', {})
            plan_id = body.get('plan_id')
            if plan_id:
                if plan_id not in plans:
                    raise KeyError(plan_id)
                plan = dict(plans[plan_id])
            else:
                plan_id = _new_id('eplan')
                plan = {'plan_id': plan_id, 'created_at': _utcnow()}
            now = datetime.now(timezone.utc).replace(microsecond=0)
            plan.update({
                'strategy_version_id': svid,
                'symbols': cleaned_syms,
                'market': market,
                'side': side,
                'interval': interval,
                'observation_horizon_days': horizon,
                'max_hold_days': max_hold,
                'check_interval_minutes': check_minutes,
                'auto_backfill_on_gap': auto_backfill,
                'auto_enqueue_backfill': auto_enqueue,
                'enabled': enabled,
                'label': (body.get('label') or '').strip() or f'前向观察 {svid}',
                'updated_at': _utcnow(),
                'tp_pct': body.get('tp_pct', 0.10),
                'sl_pct': body.get('sl_pct', 0.05),
                'notional_u': body.get('notional_u', 100.0),
            })
            if enabled:
                plan['next_run_at'] = (now + timedelta(minutes=check_minutes)).isoformat()
                plan['last_enabled_at'] = _utcnow()
            else:
                plan['next_run_at'] = None
            plans[plan_id] = plan
            state['plans'] = plans
            self._save_state(state)
            return {'plan': plan, 'server_effect': state['server_effect']}

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
            plan['updated_at'] = _utcnow()
            now = datetime.now(timezone.utc).replace(microsecond=0)
            if enabled:
                mins = int(plan.get('check_interval_minutes') or 60)
                plan['next_run_at'] = (now + timedelta(minutes=mins)).isoformat()
                plan['last_enabled_at'] = _utcnow()
            else:
                plan['next_run_at'] = None
            plans[plan_id] = plan
            state['plans'] = plans
            self._save_state(state)
            return {'plan': plan, 'server_effect': state['server_effect']}

    def list_backfill_links(self, limit=50):
        limit = max(1, min(int(limit), 200))
        with self._lock:
            rows = self._read_jsonl(self.backfill_links_path)
            rows = list(reversed(rows[-limit:]))
            return {'links': rows[:limit], 'count': len(rows[:limit]), 'disclaimer': DISCLAIMER}

    def _plan_flags_for_obs(self, obs):
        """Resolve auto_backfill_on_gap / auto_enqueue_backfill from matching plan.

        auto_backfill_on_gap defaults True (link+prepare only).
        auto_enqueue_backfill defaults False (must be explicit on plan).
        Prefer an enabled plan when several share the same strategy_version_id.
        """
        auto_link = True
        auto_enqueue = False
        matched = None
        state = self._load_state()
        candidates = []
        for plan in (state.get('plans') or {}).values():
            if plan.get('strategy_version_id') == obs.get('strategy_version_id'):
                candidates.append(plan)
        if candidates:
            enabled = [p for p in candidates if p.get('enabled')]
            matched = (enabled or candidates)[0]
            if matched.get('auto_backfill_on_gap') is False:
                auto_link = False
            if matched.get('auto_enqueue_backfill') is True:
                auto_enqueue = True
        return auto_link, auto_enqueue, matched

    def _find_existing_backfill_link(self, obs, symbol, market, interval, start, end):
        """Idempotent lookup: same observation window already linked."""
        rows = self._read_jsonl(self.backfill_links_path)
        oid = obs.get('observation_id')
        for row in reversed(rows):
            if (
                row.get('observation_id') == oid
                and row.get('symbol') == symbol
                and row.get('market') == market
                and row.get('interval') == interval
                and row.get('start') == start
                and row.get('end') == end
            ):
                return row
        return None

    def _enqueue_candle_backfill(self, link, spec):
        """Submit candle_range via TaskService when available; never invent prices (F02)."""
        out = {
            'enqueue_attempted': True,
            'enqueue_status': None,
            'task_id': None,
            'reused': False,
            'enqueue_error': None,
        }
        if self.task_service is None or not hasattr(self.task_service, 'submit_range'):
            out['enqueue_status'] = 'skipped_no_task_service'
            out['enqueue_error'] = 'TaskService 未绑定；仅记录联动，未入队'
            return out
        try:
            result = self.task_service.submit_range(spec)
            task = (result or {}).get('task') or {}
            reused = bool((result or {}).get('reused'))
            tid = task.get('id') or task.get('task_id')
            out['task_id'] = tid
            out['reused'] = reused
            out['enqueue_status'] = 'reused_existing_task' if reused else 'enqueued'
            out['task_status'] = task.get('status')
        except Exception as exc:  # noqa: BLE001
            out['enqueue_status'] = 'enqueue_failed'
            out['enqueue_error'] = str(exc)[:240]
        return out

    def _link_backfill(self, obs, reason, detail=None):
        """Record DATA-01 backfill linkage; optionally auto-enqueue candle_range (F02).

        - auto_backfill_on_gap (default True): write backfill_links + optional prepare
        - auto_enqueue_backfill (default False): call TaskService.submit_range /api path
        Idempotent: same observation window reuses prior link/task; no duplicate enqueue spam.
        """
        if not isinstance(obs, dict):
            return None
        auto_link, auto_enqueue, matched_plan = self._plan_flags_for_obs(obs)
        if not auto_link:
            return None
        symbol = obs.get('symbol')
        market = obs.get('market') or 'USDⓈ-M'
        interval = obs.get('interval') or '4h'
        start = obs.get('started_at')
        end = obs.get('max_hold_ends_at')

        existing = self._find_existing_backfill_link(obs, symbol, market, interval, start, end)
        if existing is not None:
            # Already enqueued → return as-is (idempotent)
            if existing.get('task_id') and existing.get('enqueue_status') in (
                'enqueued', 'reused_existing_task', 'queued', 'running'
            ):
                reused = dict(existing)
                reused['idempotent_hit'] = True
                return reused
            # Link exists, enqueue not yet done, and plan now asks for enqueue
            if auto_enqueue and not existing.get('task_id'):
                spec = existing.get('backfill_spec') or {
                    'contracts': [{'market': market, 'symbol': symbol}],
                    'interval': interval,
                    'start': start,
                    'end': end,
                }
                enq = self._enqueue_candle_backfill(existing, spec)
                updated = dict(existing)
                updated.update(enq)
                if enq.get('enqueue_status') in ('enqueued', 'reused_existing_task'):
                    updated['status'] = 'enqueued'
                elif enq.get('enqueue_status') == 'enqueue_failed':
                    updated['status'] = 'linked_enqueue_failed'
                updated['updated_at'] = _utcnow()
                updated['idempotent_hit'] = True
                self._append_jsonl(self.backfill_links_path, updated)
                return updated
            hit = dict(existing)
            hit['idempotent_hit'] = True
            return hit

        link_id = _new_id('bfl')
        spec = {
            'contracts': [{'market': market, 'symbol': symbol}],
            'interval': interval,
            'start': start,
            'end': end,
        }
        prepared = None
        prepare_error = None
        if self.ranges is not None:
            try:
                prepared = self.ranges.prepare(spec)
            except Exception as exc:  # noqa: BLE001
                prepare_error = str(exc)[:240]
        link = {
            'link_id': link_id,
            'created_at': _utcnow(),
            'observation_id': obs.get('observation_id'),
            'strategy_version_id': obs.get('strategy_version_id'),
            'plan_id': (matched_plan or {}).get('plan_id'),
            'symbol': symbol,
            'market': market,
            'interval': interval,
            'start': start,
            'end': end,
            'reason': reason,
            'detail': detail or {},
            'backfill_spec': spec,
            'prepared_range_id': (prepared or {}).get('range_id') if isinstance(prepared, dict) else None,
            'prepare_error': prepare_error,
            'auto_enqueue_backfill': auto_enqueue,
            'enqueue_attempted': False,
            'enqueue_status': 'not_requested',
            'task_id': None,
            'reused': False,
            'enqueue_error': None,
            'status': 'linked' if prepare_error is None else 'linked_prepare_failed',
            'note': 'pending_backfill/unverifiable/data_gap → 持仓区间补查联动；未用局部末价假结算（F02）',
            'disclaimer': DISCLAIMER,
        }
        if auto_enqueue:
            enq = self._enqueue_candle_backfill(link, spec)
            link.update(enq)
            if enq.get('enqueue_status') in ('enqueued', 'reused_existing_task'):
                link['status'] = 'enqueued'
            elif enq.get('enqueue_status') == 'enqueue_failed':
                link['status'] = 'linked_enqueue_failed'
            elif enq.get('enqueue_status') == 'skipped_no_task_service':
                link['status'] = 'linked_enqueue_skipped'
        self._append_jsonl(self.backfill_links_path, link)
        state = self._load_state()
        state['backfill_link_count'] = int(state.get('backfill_link_count') or 0) + 1
        self._save_state(state)
        return link


    def on_candle_range_task_finished(self, task_id, status, result=None):
        """V1: after candle_range tied to open obs completes → auto re-settle.

        Default-safe: never invents prices; settle() itself keeps F02 / no fake win on gaps.
        partial_success still triggers re-settle (coverage may improve); failed → mark link only.
        """
        if not task_id:
            return {'ok': False, 'reason': 'missing task_id', 'resettles': []}
        status = str(status or '')
        resettles = []
        with self._lock:
            links = self._read_jsonl(self.backfill_links_path)
        # Latest link record per link_id / observation for this task
        by_obs = {}
        for row in links:
            if row.get('task_id') != task_id:
                continue
            oid = row.get('observation_id')
            if oid:
                by_obs[oid] = row
        if not by_obs:
            return {
                'ok': True,
                'task_id': task_id,
                'status': status,
                'resettles': [],
                'note': '无关联 backfill_link；跳过自动再 settle',
            }
        terminal_ok = status in ('success', 'partial_success')
        for oid, link in by_obs.items():
            entry = {
                'observation_id': oid,
                'link_id': link.get('link_id'),
                'task_id': task_id,
                'task_status': status,
            }
            # Append link status update
            updated = dict(link)
            updated['task_finished_status'] = status
            updated['task_finished_at'] = _utcnow()
            updated['task_result'] = {
                k: (result or {}).get(k)
                for k in ('range_id', 'known', 'expected', 'status', 'available_at')
                if isinstance(result, dict)
            }
            if not terminal_ok:
                updated['status'] = 'backfill_failed'
                updated['resettle_status'] = 'skipped_task_failed'
                self._append_jsonl(self.backfill_links_path, updated)
                entry['resettle_status'] = 'skipped_task_failed'
                resettles.append(entry)
                continue
            updated['status'] = 'backfill_done'
            try:
                # as_of = now; settle enforces F02 (no early max_hold) and gap rules
                out = self.settle(oid, {'mode': 'rules', 'trigger': 'backfill_complete', 'task_id': task_id})
            except EvalConflict as exc:
                updated['resettle_status'] = 'conflict'
                updated['resettle_error'] = str(exc)[:240]
                self._append_jsonl(self.backfill_links_path, updated)
                entry['resettle_status'] = 'conflict'
                entry['error'] = str(exc)[:200]
                resettles.append(entry)
                continue
            except Exception as exc:  # noqa: BLE001
                updated['resettle_status'] = 'error'
                updated['resettle_error'] = str(exc)[:240]
                self._append_jsonl(self.backfill_links_path, updated)
                entry['resettle_status'] = 'error'
                entry['error'] = str(exc)[:200]
                resettles.append(entry)
                continue
            pending = out.get('pending')
            settlement = out.get('settlement')
            if pending:
                updated['resettle_status'] = 'still_pending'
                updated['resettle_outcome'] = pending.get('outcome') or pending.get('exit_reason')
                entry['resettle_status'] = 'still_pending'
                entry['outcome'] = updated['resettle_outcome']
                entry['pending'] = True
            elif settlement:
                updated['resettle_status'] = 'settled'
                updated['resettle_outcome'] = settlement.get('outcome')
                updated['settlement_id'] = settlement.get('settlement_id')
                entry['resettle_status'] = 'settled'
                entry['outcome'] = settlement.get('outcome')
                entry['settlement_id'] = settlement.get('settlement_id')
            else:
                updated['resettle_status'] = 'unknown'
                entry['resettle_status'] = 'unknown'
            updated['status'] = 'resettle_' + str(updated.get('resettle_status'))
            self._append_jsonl(self.backfill_links_path, updated)
            resettles.append(entry)
        return {
            'ok': True,
            'task_id': task_id,
            'status': status,
            'resettles': resettles,
            'note': '补查完成后自动再 settle；缺口/未到期不假赢（F02）',
        }

    def poll_completed_backfills_and_resettle(self):
        """Scheduler path: look up finished candle_range tasks for open linked obs."""
        if self.task_service is None or not hasattr(self.task_service, 'task'):
            return {'ok': False, 'reason': 'no_task_service', 'resettles': []}
        with self._lock:
            links = self._read_jsonl(self.backfill_links_path)
            open_ids = set(self._load_state().get('open_ids') or [])
        # Latest link per observation
        latest = {}
        for row in links:
            oid = row.get('observation_id')
            tid = row.get('task_id')
            if not oid or not tid:
                continue
            if oid not in open_ids:
                continue
            # Skip if already resettled after this task
            if row.get('resettle_status') in ('settled', 'still_pending', 'conflict', 'error') and row.get('task_finished_status'):
                # still_pending may need another poll if more data arrived — only skip settled
                if row.get('resettle_status') == 'settled':
                    continue
            latest[oid] = row
        # Dedupe by task_id
        by_task = {}
        for oid, row in latest.items():
            tid = row['task_id']
            by_task.setdefault(tid, []).append(oid)
        all_resettles = []
        for tid, oids in by_task.items():
            try:
                task = self.task_service.task(tid)
            except Exception:
                continue
            st = (task or {}).get('status')
            if st not in ('success', 'partial_success', 'failed'):
                continue
            # Avoid double-fire if link already recorded same finished status + settled
            already = False
            for row in links:
                if row.get('task_id') == tid and row.get('task_finished_status') == st and row.get('resettle_status') == 'settled':
                    already = True
                    break
            if already:
                continue
            result = (task or {}).get('result')
            if isinstance(result, str):
                try:
                    import json as _json
                    result = _json.loads(result)
                except Exception:
                    result = {'raw': result}
            out = self.on_candle_range_task_finished(tid, st, result)
            all_resettles.extend(out.get('resettles') or [])
        return {'ok': True, 'resettles': all_resettles}

    def scheduler_tick(self, now=None):
        """Due plans: open observations (unless pause); check open settles; link backfills.

        Default plans are off — only enabled plans run. Does not fake early max_hold (F02).
        """
        now = now or datetime.now(timezone.utc).replace(microsecond=0)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        results = {
            'started': [],
            'settle_checks': [],
            'skipped_pause': False,
            'plans_due': 0,
            'at': now.isoformat(),
        }
        pause = False
        if self.review_store is not None:
            try:
                pause = bool(self.review_store.get_pause_new_entries())
            except Exception:
                pause = False

        with self._lock:
            state = self._load_state()
            plans = state.get('plans') or {}

        for plan_id, plan in list(plans.items()):
            if not plan.get('enabled'):
                continue
            next_run = plan.get('next_run_at')
            if not next_run:
                continue
            try:
                due_at = _parse_iso(next_run)
            except ValueError:
                continue
            if due_at > now:
                continue
            results['plans_due'] += 1
            if pause:
                results['skipped_pause'] = True
            else:
                for sym in plan.get('symbols') or []:
                    try:
                        # V1: do not pass silent tp/sl/hold defaults — freeze params only
                        start_body = {
                            'strategy_version_id': plan['strategy_version_id'],
                            'symbol': sym,
                            'market': plan.get('market') or 'USDⓈ-M',
                            'interval': plan.get('interval') or '4h',
                            'observation_horizon_days': plan.get('observation_horizon_days') or 30,
                            'start_at': now.isoformat(),
                        }
                        # Only forward plan fields when explicitly set (must match freeze)
                        for k in ('side', 'max_hold_days', 'tp_pct', 'sl_pct', 'notional_u'):
                            if plan.get(k) is not None:
                                start_body[k] = plan[k]
                        obs = self.start_observation(start_body)
                        results['started'].append({
                            'plan_id': plan_id,
                            'observation_id': obs.get('observation_id'),
                            'symbol': sym,
                        })
                    except Exception as exc:  # noqa: BLE001
                        results['started'].append({
                            'plan_id': plan_id,
                            'symbol': sym,
                            'error': str(exc)[:200],
                        })
            # advance next_run even if pause (avoid spin); pause only blocks new opens
            mins = int(plan.get('check_interval_minutes') or 60)
            with self._lock:
                state = self._load_state()
                p = dict((state.get('plans') or {}).get(plan_id) or plan)
                p['next_run_at'] = (now + timedelta(minutes=mins)).isoformat()
                p['last_run_at'] = now.isoformat()
                state.setdefault('plans', {})[plan_id] = p
                self._save_state(state)

        # V1: completed candle_range → auto re-settle linked open observations
        try:
            results['backfill_resettles'] = self.poll_completed_backfills_and_resettle().get('resettles') or []
        except Exception as exc:  # noqa: BLE001
            results['backfill_resettles'] = [{'error': str(exc)[:200]}]

        # Settle-check all open observations (rules; F02 keeps pending without fake win)
        with self._lock:
            open_ids = list(self._load_state().get('open_ids') or [])
        for oid in open_ids:
            try:
                out = self.settle(oid, {'mode': 'rules', 'as_of': now.isoformat()})
                results['settle_checks'].append({
                    'observation_id': oid,
                    'pending': bool(out.get('pending')),
                    'outcome': (out.get('settlement') or out.get('pending') or {}).get('outcome'),
                    'backfill_link_id': (out.get('backfill_link') or {}).get('link_id'),
                })
            except Exception as exc:  # noqa: BLE001
                results['settle_checks'].append({'observation_id': oid, 'error': str(exc)[:200]})
        return results

    def reload(self):
        """Re-read from disk (restart replay for tests)."""
        with self._lock:
            self._ensure_bootstrapped()
            state = self._load_state()
            # Rebuild open_ids from observations if needed
            obs = self.list_observations(limit=500)
            open_ids = [o['observation_id'] for o in obs['observations'] if o.get('status') == 'open']
            state['open_ids'] = open_ids
            state['observation_count'] = obs['total_tracked']
            settlements = self._read_jsonl(self.settlements_path)
            state['settlement_count'] = len(settlements)
            self._save_state(state)
            return {
                'open_count': len(open_ids),
                'observation_count': state['observation_count'],
                'settlement_count': state['settlement_count'],
                'grades': self.grades(),
            }

    def _rebuild_from_disk(self):
        state = self._blank_state()
        obs_rows = self._read_jsonl(self.observations_path)
        by_id = {}
        for r in obs_rows:
            oid = r.get('observation_id')
            if oid:
                by_id[oid] = r
        open_ids = [oid for oid, r in by_id.items() if r.get('status') == 'open']
        state['open_ids'] = open_ids
        state['observation_count'] = len(by_id)
        state['settlement_count'] = len(self._read_jsonl(self.settlements_path))
        state['rebuild_note'] = '从 jsonl 重建 open_ids；结算记录保持追加不可变'
        return state


# Alias
Conflict = EvalConflict
