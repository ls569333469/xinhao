"""Candidate-bound event timing. No experimental entry filters.

Called under the store lock. Bar fields are UTC milliseconds; system fields UTC
ISO. New schema keeps legacy confirm_time as system discovery time.
"""
from ema_observations import BAR_MS, finite, time_ms, states, observation, archive_raw, bind_identity
from ema_squeeze_rules import RULES_VERSION, VOL_CONFIRM_RATIO, structure_still_holds_medium, structure_still_holds_strong


def event_id(cycle, bar, level):
    return '|'.join(map(str, (cycle['market'], cycle['symbol'], '4h', RULES_VERSION,
                             cycle['candidate_cycle_id'], bar, level)))


def process(cur, row, now_s):
    out = dict(pendingCross=None, crossConfirm=None, eventTransitions=[])
    bar, now = time_ms(row.get('candleAsOf')), time_ms(now_s)
    entered = time_ms(cur.get('entered_at'))
    if bar is None or now is None or bar > now:
        out['crossConfirm'] = dict(blocked=True, reason='closed_bar_time_unknown_or_future')
        return out
    last = time_ms(cur.get('last_processed_bar_time'))
    if last is not None and bar < last:
        out.update(pendingCross=cur.get('pending_cross'), ignoredReason='older_bar')
        return out
    terminal = cur.setdefault('terminal_events', {})
    confirmations = cur.setdefault('confirmed_events', [])
    emas, close = row.get('ema4h') or [], row.get('closed4h')
    valid_emas = len(emas) == 3 and all(finite(e, True) is not None for e in emas)
    vol = finite(row.get('barVolumeRatio7'))
    vol = vol if vol is not None and vol >= 0 else None
    historical = row.get('_historicalReplay') is True
    oi_valid = finite(row.get('oiUsd'), True) is not None
    quality_ok = row.get('emaQuality') not in ('insufficient', 'gap') and row.get('errors', {}).get('K线连续性') is None

    def finish(p, reason=None):
        eid = p['event_id']
        evidence = row.get('emaObservation')
        if reason:
            ev = dict(p, void=True, status='void', reason=reason, resolved_bar_time=bar, resolved_at=now_s)
            cur['last_void'] = ev
        else:
            next_open = row.get('_nextOpenReference')
            # References are factual, never a fill. An unclosed following bar may supply ONLY its open.
            if not isinstance(next_open, dict) or next_open.get('open_time') != bar+1 or finite(next_open.get('price'), True) is None:
                next_open = None
            ev = dict(p, schema='ema-cross-event-v2', status='confirmed', level=p['level'],
                      cross_time=p['cross_bar_time'], confirm_time=now_s,
                      confirm_bar_time=bar, confirmed_at=now_s, observed_at=now_s,
                      confirm_vol_ratio=vol, confirm_observation=evidence,
                      next_open_reference=next_open, discovery='historical_replay' if historical else 'scan',
                      historical=historical, entry_eligibility='unverified_historical' if historical else 'candidate_at_cross',
                      automatic_fill=False, execution_thresholds_enabled=False)
            confirmations.append(ev)
        terminal[eid] = dict(status=ev['status'], first_seen_at=p['first_seen_at'], resolved_at=now_s)
        out['eventTransitions'].append(ev)
        out['crossConfirm'] = ev
        cur['pending_cross'] = None

    pending = cur.get('pending_cross')
    if pending:
        pending = dict(pending)
        cross_bar = time_ms(pending.get('cross_bar_time', pending.get('cross_bar_as_of', pending.get('cross_time'))))
        if pending.get('candidate_cycle_id') != cur['candidate_cycle_id'] or cross_bar is None:
            cur['last_void'] = dict(pending, void=True, reason='legacy_pending_time_or_cycle_unknown', resolved_at=now_s)
            cur['pending_cross'] = None
            out['crossConfirm'] = dict(blocked=True, reason='legacy_pending_time_or_cycle_unknown')
        elif bar <= cross_bar:
            out['pendingCross'] = pending
            return out
        else:
            pending.setdefault('event_id', event_id(cur, cross_bar, pending['level']))
            pending.setdefault('first_seen_at', pending.get('observed_at'))
            pending.setdefault('cross_bar_time', cross_bar)
            pending.setdefault('cross_bar_as_of', cross_bar)
            if bar-cross_bar != BAR_MS:
                finish(pending, 'gap_missing_next_bar')
            elif not quality_ok or not valid_emas or finite(close, True) is None:
                finish(pending, 'next_bar_structure_unknown')
            else:
                hold = structure_still_holds_strong(*emas) if pending['level'] == 'strong' else structure_still_holds_medium(*emas, close)
                if hold is not True:
                    finish(pending, 'structure_broken')
                elif vol is None:
                    finish(pending, 'next_bar_volume_unknown')
                elif not oi_valid and not historical:
                    finish(pending, 'next_bar_oi_unknown')
                elif vol < VOL_CONFIRM_RATIO:
                    finish(pending, 'next_bar_volume_fail')
                else:
                    finish(pending)

    level = 'strong' if row.get('fullCross') is True else 'medium' if row.get('mediumCrossEvent') is True else None
    if level:
        eid = event_id(cur, bar, level)
        # Recognise v1 IDs too; never duplicate an old confirmed event on rescan.
        existing = next((ev for ev in confirmations if ev.get('level') == level and time_ms(ev.get('cross_bar_time', ev.get('cross_time'))) == bar), None)
        last_void = cur.get('last_void') or {}
        legacy_void = last_void.get('level') == level and time_ms(last_void.get('cross_bar_time', last_void.get('cross_bar_as_of', last_void.get('cross_time')))) == bar
        if eid not in terminal and existing is None and not legacy_void:
            if entered is None or bar <= entered:
                out['crossConfirm'] = dict(blocked=True, reason='cross_precedes_candidate_qualification')
            elif historical:
                # Archives only contain candles, no historical OI/cap/account eligibility.
                out.setdefault('technicalOnly', []).append(dict(bar_time=bar, level=level, reason='historical_qualification_missing'))
            elif not quality_ok or not valid_emas or finite(close, True) is None or not oi_valid or (structure_still_holds_strong(*emas) if level == 'strong' else structure_still_holds_medium(*emas, close)) is not True:
                out['crossConfirm'] = dict(blocked=True, reason='cross_evidence_unknown')
            else:
                p = dict(schema='ema-cross-event-v2', event_id=eid, level=level, cross_time=bar,
                         cross_bar_time=bar, cross_bar_as_of=bar, timeframe='4h', timestamp_unit='utc_ms',
                         cross_ema99=emas[2], bars_waited=0, candidate_cycle_id=cur['candidate_cycle_id'],
                         rules_version=RULES_VERSION, first_seen_at=now_s, observed_at=now_s,
                         cross_observation=row.get('emaObservation'), status='waiting_next_bar_volume')
                if vol is not None and vol >= VOL_CONFIRM_RATIO:
                    finish(p)
                else:
                    # Unknown on T may be resolved by an actual T+1, never by a fabricated zero.
                    p['volume_at_cross'] = vol
                    cur['pending_cross'] = p
    cur['last_processed_bar_time'] = max(bar, last or bar)
    cur['last_seen_at'] = now_s
    out['pendingCross'] = cur.get('pending_cross')
    return out


def recover_pending(root, cur, row, now_s):
    """Replay only the real archived T+1 of an existing pending candidate event.

    Missing historical OI makes this a historical technical confirmation, never
    an entry permit. No recovery from today's newly created candidate cycle.
    """
    p = cur.get('pending_cross')
    cross = time_ms((p or {}).get('cross_bar_time', (p or {}).get('cross_bar_as_of')))
    current = time_ms(row.get('candleAsOf'))
    if cross is None or current is None or current-cross <= BAR_MS:
        return None
    try:
        raw = archive_raw(root, row)
        if not raw:
            return None
        # COIN-M contractSize is not always present on old scan rows. Unknown units block replay.
        size = finite(row.get('contractSize'), True)
        if row.get('market') == 'COIN-M' and size is None:
            return None
        volumes = [finite(k[5])*size if finite(k[5]) is not None else None for k in raw] if size else None
        ss = states(raw, volumes)
        index = next((i for i, s in enumerate(ss) if s and s['bar_time'] == cross+BAR_MS), None)
        if index is None or index == 0 or not ss[index]['valid'] or not ss[index-1] or ss[index-1]['bar_time'] != cross:
            return None
        s = ss[index]
        replay = dict(row, candleAsOf=s['bar_time'], ema4h=s['ema'], closed4h=s['close'],
                      fullCross=False, mediumCrossEvent=False, barVolumeRatio7=s['volume_ratio'],
                      emaObservation=bind_identity(observation(ss[:index+1]), row.get('market'), row.get('symbol')), _historicalReplay=True,
                      emaQuality='archive_replay', errors={}, oiUsd=None, _nextOpenReference=None)
        if index+1 < len(ss) and ss[index+1] and ss[index+1]['open_time'] == s['bar_time']+1:
            replay['_nextOpenReference'] = dict(open_time=ss[index+1]['open_time'], price=ss[index+1]['open'],
                                                 source='archived_4h_open', retrieved_at=now_s)
        return process(cur, replay, now_s)
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None
