"""V1: paper/eval/backtest must consume frozen version rules.params only.

No silent defaults (no generic breakout stand-in — F04).
Missing/ambiguous frozen params → FrozenExecConflict (maps to HTTP 409).
Request may not override frozen values with different numbers.
"""
from __future__ import annotations


class FrozenExecConflict(ValueError):
    """Clear refusal when frozen exec params missing or request tries to diverge."""

    def __init__(self, message, *, http_status=409):
        super().__init__(message)
        self.http_status = http_status


def _as_float(name, value):
    if isinstance(value, bool):
        raise FrozenExecConflict(f'{name} 须为数字')
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise FrozenExecConflict(f'{name} 须为数字: {value!r}') from exc


def _as_int(name, value):
    f = _as_float(name, value)
    if abs(f - int(f)) > 1e-9:
        raise FrozenExecConflict(f'{name} 须为整数: {value!r}')
    return int(f)


def _neq(a, b):
    """Compare request vs frozen; floats with tolerance."""
    try:
        fa, fb = float(a), float(b)
        return abs(fa - fb) > 1e-12
    except (TypeError, ValueError):
        return str(a).strip() != str(b).strip()


def frozen_params_from_version(version):
    if not isinstance(version, dict):
        raise FrozenExecConflict('冻结版本对象无效')
    rules = version.get('rules')
    if not isinstance(rules, dict):
        raise FrozenExecConflict(
            f'冻结版本 {version.get("version_id")} 缺少 rules；拒绝执行'
        )
    params = rules.get('params')
    if not isinstance(params, dict) or not params:
        raise FrozenExecConflict(
            f'冻结版本 {version.get("version_id")} 未声明 rules.params；'
            f'拒绝发明默认止盈止损/持仓/入场规则（F04 / HTTP 409）'
        )
    return params, rules


def _execution_mode(params):
    """Return execution mode. Default pct_touch preserves legacy frozen packages."""
    mode = params.get('execution_mode') or params.get('exit_mode') or params.get('mode')
    if mode is None:
        return 'pct_touch'
    mode = str(mode).strip().lower()
    aliases = {
        'pct': 'pct_touch',
        'percent': 'pct_touch',
        'fixed_pct': 'pct_touch',
        'tp_sl': 'pct_touch',
        'ema99': 'ema99_close_trail',
        'ema99_trail': 'ema99_close_trail',
        'ema99_close': 'ema99_close_trail',
        'close_trail': 'ema99_close_trail',
    }
    return aliases.get(mode, mode)


def require_frozen_exec_params(version, body=None, *, context='paper'):
    """Return locked exec dict from frozen version; refuse missing or divergent body.

    context:
      - paper / eval: require tp_pct, sl_pct, max_hold_days  (pct_touch)
      - backtest: require tp_pct, sl_pct, max_hold_bars, entry_rule  (pct_touch)
      - autofill: require tp_pct, sl_pct; max_hold_days optional; side locked if present
      - ema99_close_trail: require execution_mode + entry_rule(for backtest) + schema fields;
        MUST NOT invent tp_pct/sl_pct; 30d candidate life ≠ max_hold_days
    """
    body = body if isinstance(body, dict) else {}
    params, rules = frozen_params_from_version(version)
    vid = version.get('version_id') or '?'
    mode = _execution_mode(params)

    # F04: never invent entry_rule / breakout
    if body.get('entry_rule') and not params.get('entry_rule'):
        raise FrozenExecConflict(
            f'冻结版本 {vid} 未声明 params.entry_rule；'
            f'禁止请求体静默挂接 {body.get("entry_rule")!r}（F04 / HTTP 409）'
        )
    if (
        params.get('entry_rule')
        and body.get('entry_rule') is not None
        and str(body.get('entry_rule')) != str(params.get('entry_rule'))
    ):
        raise FrozenExecConflict(
            f'请求 entry_rule={body.get("entry_rule")} 与冻结 '
            f'params.entry_rule={params.get("entry_rule")} 不一致；拒绝覆盖'
        )

    if mode == 'ema99_close_trail':
        return _require_ema99_trail(version, params, rules, body, context=context)

    if mode != 'pct_touch':
        raise FrozenExecConflict(
            f'冻结版本 {vid} 未知 execution_mode={mode!r}；'
            f'拒绝回退成旧固定 sl_pct（HTTP 409）'
        )

    required = ['tp_pct', 'sl_pct']
    if context in ('paper', 'eval'):
        required.append('max_hold_days')
    elif context == 'backtest':
        required.extend(['max_hold_bars', 'entry_rule'])
    # autofill: tp/sl required; hold optional but if body sends must match

    missing = [k for k in required if params.get(k) is None or params.get(k) == '']
    if missing:
        raise FrozenExecConflict(
            f'冻结版本 {vid} 缺少可执行参数 {missing}；'
            f'拒绝用静默默认值执行（context={context} / HTTP 409）'
        )

    # Locked keys: body may omit, but must not diverge when present
    lock_keys = (
        'tp_pct', 'sl_pct', 'max_hold_days', 'max_hold_bars',
        'side', 'entry_rule', 'notional_u', 'leverage', 'cooldown_bars',
        'warmup_bars', 'min_warmup_bars', 'execution_mode',
    )
    for k in lock_keys:
        if k not in body or body.get(k) is None:
            continue
        if params.get(k) is None:
            # body supplies what frozen never declared → refuse (no invent via request)
            if k in ('tp_pct', 'sl_pct', 'max_hold_days', 'max_hold_bars', 'entry_rule', 'side', 'execution_mode'):
                raise FrozenExecConflict(
                    f'冻结版本未声明 params.{k}；禁止请求体补默认/替代（HTTP 409）'
                )
            continue
        if _neq(body.get(k), params.get(k)):
            raise FrozenExecConflict(
                f'请求 {k}={body.get(k)} 与冻结 params.{k}={params.get(k)} 不一致；'
                f'纸面/结算只服从冻结参数（HTTP 409）'
            )

    tp = _as_float('tp_pct', params['tp_pct'])
    sl = _as_float('sl_pct', params['sl_pct'])
    if tp <= 0 or sl <= 0:
        raise FrozenExecConflict('冻结 tp_pct / sl_pct 须 > 0')
    if tp > 5 or sl > 5:
        raise FrozenExecConflict('冻结 tp_pct / sl_pct 过大（>500%），请确认冻结内容')

    out = {
        'schema': 'frozen-exec-params-v1',
        'execution_mode': 'pct_touch',
        'version_id': vid,
        'content_hash': version.get('content_hash'),
        'tp_pct': tp,
        'sl_pct': sl,
        'entry_rule': params.get('entry_rule'),
        'side': None,
        'max_hold_days': None,
        'max_hold_bars': None,
        'notional_u': None,
        'leverage': None,
        'cooldown_bars': None,
        'base_template_id': rules.get('base_template_id') or params.get('tier'),
        'params_source': 'frozen_version.rules.params',
        'disclaimer': '只服从冻结版本参数；草案改动不生效；不发明默认入场规则',
    }

    if params.get('max_hold_days') is not None:
        out['max_hold_days'] = _as_int('max_hold_days', params['max_hold_days'])
        if out['max_hold_days'] < 1 or out['max_hold_days'] > 3650:
            raise FrozenExecConflict('冻结 max_hold_days 须在 1–3650')
    if params.get('max_hold_bars') is not None:
        out['max_hold_bars'] = _as_int('max_hold_bars', params['max_hold_bars'])
        if out['max_hold_bars'] < 1 or out['max_hold_bars'] > 5000:
            raise FrozenExecConflict('冻结 max_hold_bars 须在 1–5000')

    if params.get('side') is not None:
        side = str(params['side']).strip().lower()
        if side not in ('long', 'short'):
            raise FrozenExecConflict(f'冻结 side 无效: {side}')
        out['side'] = side

    if params.get('notional_u') is not None:
        out['notional_u'] = _as_float('notional_u', params['notional_u'])
        if out['notional_u'] <= 0:
            raise FrozenExecConflict('冻结 notional_u 须 > 0')
    if params.get('leverage') is not None:
        out['leverage'] = _as_float('leverage', params['leverage'])
        if out['leverage'] < 1:
            raise FrozenExecConflict('冻结 leverage 须 ≥ 1')
    if params.get('cooldown_bars') is not None:
        out['cooldown_bars'] = _as_int('cooldown_bars', params['cooldown_bars'])

    return out


def _require_ema99_trail(version, params, rules, body, *, context='paper'):
    """New independent mode: EMA99 close trailing stop. No default tp/sl pct."""
    vid = version.get('version_id') or '?'
    # Refuse inventing tp/sl via body when frozen omitted them
    for k in ('tp_pct', 'sl_pct'):
        if body.get(k) is not None and params.get(k) is None:
            raise FrozenExecConflict(
                f'ema99_close_trail 模式未声明 params.{k}；禁止请求体补旧默认（HTTP 409）'
            )
        if (
            body.get(k) is not None
            and params.get(k) is not None
            and _neq(body.get(k), params.get(k))
        ):
            raise FrozenExecConflict(
                f'请求 {k}={body.get(k)} 与冻结 params.{k}={params.get(k)} 不一致'
            )

    required = ['execution_mode']
    # Accept exit_mode/mode aliases already normalized by caller into params presence
    if params.get('execution_mode') is None and params.get('exit_mode') is None and params.get('mode') is None:
        raise FrozenExecConflict(
            f'冻结版本 {vid} 标记为 ema99 模式但缺少 execution_mode 字段'
        )

    # paper/eval/backtest/autofill：ema99 必须自带 entry_rule，禁止 UI/请求发明入场
    if context in ('backtest', 'paper', 'eval', 'autofill'):
        if not params.get('entry_rule'):
            raise FrozenExecConflict(
                f'冻结版本 {vid} ema99_close_trail 缺少 params.entry_rule；'
                f'须为候选/cross/volume 路径（如 ema_squeeze_confirmed），禁止通用突破顶替'
            )
        allowed_entries = {
            'ema_squeeze_confirmed',
            'ema_squeeze_full_cross_vol',
            'candidate_cross_vol',
        }
        if str(params.get('entry_rule')) not in allowed_entries:
            # Still allow explicit declare — but refuse silent breakout stand-ins
            if str(params.get('entry_rule')) in (
                'close_above_prev_high', 'close_below_prev_low', 'flat_reenter'
            ):
                raise FrozenExecConflict(
                    f'ema99_close_trail 不得使用通用突破 entry_rule='
                    f'{params.get("entry_rule")!r} 顶替本策略路径（HTTP 409）'
                )

    # Candidate 30d must NOT be copied into max_hold_days
    if params.get('candidate_max_days') is not None:
        cmd = _as_int('candidate_max_days', params['candidate_max_days'])
        if cmd != 30:
            raise FrozenExecConflict('本版 candidate_max_days 须为 30（入池有效期，非持仓期限）')
    if params.get('max_hold_days') is not None and context in ('paper', 'eval', 'autofill'):
        # Optional explicit position hold — separate from candidate life; validate only
        _as_int('max_hold_days', params['max_hold_days'])

    schema_ver = params.get('params_schema_version') or params.get('schema_version') or 'ema99-close-trail-v1'
    user_rules_ver = params.get('user_rules_version') or params.get('rules_version') or 'ema-squeeze-v4'

    out = {
        'schema': 'frozen-exec-params-ema99-v1',
        'execution_mode': 'ema99_close_trail',
        'params_schema_version': schema_ver,
        'user_rules_version': user_rules_ver,
        'version_id': vid,
        'content_hash': version.get('content_hash'),
        'tp_pct': None,
        'sl_pct': None,
        'entry_rule': params.get('entry_rule'),
        'side': None,
        'max_hold_days': None,
        'max_hold_bars': None,
        'candidate_max_days': 30,
        'stop_style': 'ema99_close_trail',
        'intrabar_stop': False,
        'trail_activation': 'first_close_above_entry',
        'initial_stop': 'cross_bar_ema99',
        'notional_u': None,
        'leverage': None,
        'cooldown_bars': None,
        'base_template_id': rules.get('base_template_id') or params.get('tier'),
        'params_source': 'frozen_version.rules.params',
        'disclaimer': (
            'EMA99收盘追踪止损；无固定tp/sl；盘中触价不退出；'
            '候选30天≠持仓期限；只服从冻结参数'
        ),
    }

    if params.get('side') is not None:
        side = str(params['side']).strip().lower()
        if side not in ('long', 'short'):
            raise FrozenExecConflict(f'冻结 side 无效: {side}')
        out['side'] = side
    if params.get('notional_u') is not None:
        out['notional_u'] = _as_float('notional_u', params['notional_u'])
        if out['notional_u'] <= 0:
            raise FrozenExecConflict('冻结 notional_u 须 > 0')
    if params.get('leverage') is not None:
        out['leverage'] = _as_float('leverage', params['leverage'])
        if out['leverage'] < 1:
            raise FrozenExecConflict('冻结 leverage 须 ≥ 1')
    if params.get('max_hold_days') is not None:
        out['max_hold_days'] = _as_int('max_hold_days', params['max_hold_days'])
    if params.get('max_hold_bars') is not None:
        out['max_hold_bars'] = _as_int('max_hold_bars', params['max_hold_bars'])
    if params.get('cooldown_bars') is not None:
        out['cooldown_bars'] = _as_int('cooldown_bars', params['cooldown_bars'])

    # Optional explicit tp — only if frozen declared (not default)
    if params.get('tp_pct') is not None:
        out['tp_pct'] = _as_float('tp_pct', params['tp_pct'])
    if params.get('sl_pct') is not None:
        # Explicitly refuse mixing: ema99 mode should not carry fixed sl_pct for matching
        raise FrozenExecConflict(
            f'ema99_close_trail 不得同时声明 sl_pct={params.get("sl_pct")!r}；'
            f'收盘追踪与固定触价不可混用同一撮合路径（HTTP 409）'
        )

    return out
