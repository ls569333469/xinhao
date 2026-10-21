"""One presentation model for table, detail, report and CSV (read-only)."""
import csv,io,json
from pathlib import Path
from collections import Counter
from b_accumulation_store import BAccumulationStore
CSV_COLS=['event_id','market','symbol','trigger_open_time','historical','stage','technical_event','technical_confirmation','structure_gate','risk_theory','risk_observed','event_ratio','confirm_ratio','P_theory','P_observed','S0','event_revision','first_seen_at','confirmed_first_seen_at','last_evaluated_at','rule_version','evidence_profile','structure_h','structure_confirmation','reasons','event_volume','confirmation_volume','theory_open_time','expected_confirmation_open_time','bg4h','bg4h_h1','input_fingerprint','migration_status']
def present(r):
    r=dict(r);tc=r.get('technical_confirmation');sg=r.get('structure_gate','unknown')
    code=('legacy' if r.get('migration_status')=='legacy_unverified' or r.get('schema_version',0)<2 else 'corrected' if r.get('technical_event')=='fail' else 'fail' if sg=='fail' else 'unknown' if r.get('technical_event')!='pass' else 'pending' if tc=='pending' else 'confirmation_failed' if tc=='fail' else 'unknown' if tc!='pass' else 'complete' if sg=='pass' else 'unknown')
    labels=dict(legacy='旧版待复核',corrected='已更正',pending='待确认',confirmation_failed='确认未通过',complete='完整确认',fail='结构不满足',unknown='技术或资格待核验')
    r['stage']=labels[code]
    r['presentation']=dict(version='b-state-v1',code=code,label=labels[code],technical_event=r.get('technical_event','unknown'),technical_confirmation=tc or 'unknown',structure_gate=sg,revision=r.get('event_revision'),qualification_confirmed=code=='complete')
    from b_evidence_repair import repair_presentation
    r['evidence_repair']=repair_presentation(r)
    r['coverage_status']='not_verified'
    r['coverage']='旧版证据不足，保留原记录等待按r2重新核验' if code=='legacy' else '完整复算尚未核验；资格状态与证据完整性分别查看'
    return r
def events_payload(root):
    store=BAccumulationStore(root);events=[present(r) for r in store.list_events()];p=Path(root)/'runtime'/'b-observe-last-round.json'
    from b_repair_store import RepairStore
    from b_evidence_repair import repair_presentation
    repairs=RepairStore(root).summary();jobs={j['event_id']:j for j in repairs['jobs']}
    for event in events:event['evidence_repair']=repair_presentation(event,jobs.get(event['event_id']))
    last=json.loads(p.read_text(encoding='utf-8')) if p.exists() else None
    return dict(**store.meta(),events=events,evidence_repair=repairs,pool='B_ACCUMULATION_1H',counts=dict(Counter(r['stage'] for r in events)),forward_count=sum(not r.get('historical') for r in events),historical_count=sum(bool(r.get('historical')) for r in events),last_round=last,units={'oi_cap_pct':'percent:30=30%','q':'USD名义成交额','times':'UTC毫秒 / ISO UTC；页面北京时间'},simulation_enabled=False)
def events_csv(root):
    buf=io.StringIO();cols=CSV_COLS+['coverage','coverage_status','presentation','event_payload'];w=csv.DictWriter(buf,fieldnames=cols,extrasaction='ignore');w.writeheader()
    for r in events_payload(root)['events']:
        r['event_payload']=dict(r)
        w.writerow({k:json.dumps(r.get(k),ensure_ascii=False) if isinstance(r.get(k),(dict,list)) else r.get(k) for k in cols})
    return buf.getvalue()
def events_report(root):
    p=events_payload(root)
    return '# 庄家吸筹背离 · 1H观察报告\n\n'+json.dumps({k:v for k,v in p.items() if k!='events'},ensure_ascii=False,indent=2)+'\n\n'+events_csv(root)
