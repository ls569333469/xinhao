"""Explicit operator CLI. Importing or reading status never enables runtime."""
import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from .runtime import config,registry,read_status,tick,DEFAULT
from .ledger import TrackingLedger
from .recovery import Recovery
from .recovery_control import create_grant
from .recovery_bridge import enqueue_verified,bridge_page,register_b_round
from .recovery_replay import reopen_revision


def load(path, maximum=2*1024*1024):
    path=Path(path)
    if path.stat().st_size>maximum:raise ValueError('operator_input_size')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def main(argv=None):
    parser=argparse.ArgumentParser(description='ABC explicit runtime operations; no automatic scans')
    parser.add_argument('--root',required=True)
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('status')
    control=sub.add_parser('control')
    control.add_argument('--local',choices=['on','off'],required=True)
    control.add_argument('--network',choices=['on','off'],default='off')
    sources=sub.add_parser('sources');sources.add_argument('--file',required=True)
    grant=sub.add_parser('grant');grant.add_argument('--file',required=True)
    revoke=sub.add_parser('revoke');revoke.add_argument('--grant-id',required=True)
    queue=sub.add_parser('enqueue');queue.add_argument('--file',required=True)
    imported=sub.add_parser('import-b-round');imported.add_argument('--file',required=True)
    published=sub.add_parser('import-publication');published.add_argument('--file',required=True)
    retry=sub.add_parser('requeue');retry.add_argument('--job-key',required=True);retry.add_argument('--configuration-version',required=True)
    revision=sub.add_parser('revision');revision.add_argument('--job-key',required=True);revision.add_argument('--affected-ms',type=int,required=True);revision.add_argument('--configuration-version',required=True)
    sub.add_parser('tick')
    bridge=sub.add_parser('bridge');bridge.add_argument('--after',default='')
    sample=sub.add_parser('export-sample');sample.add_argument('--sample-key',required=True);sample.add_argument('--output',required=True)
    args=parser.parse_args(argv);root=Path(args.root).resolve();at=int(time.time()*1000)
    if not root.is_dir():raise ValueError('operator_root_missing')
    from durable_io import atomic_json,protection
    if args.action=='status':result=read_status(root)
    elif args.action=='control':
        with protection(root):
            value=config(root);value.update(enabled=args.local=='on',network_enabled=args.network=='on')
            if value['network_enabled'] and not value['enabled']:raise ValueError('network_requires_local_runtime')
            control_path=root/'tracking-control.json'
            tracking=load(control_path,16384) if control_path.is_file() else {}
            tracking.update(enabled=value['enabled'],profile_version='tracking-observation-v1.1.0')
            atomic_json(control_path,tracking)
            ledger=TrackingLedger.for_runtime(root)
            Recovery(ledger).set_enabled(value['network_enabled'],clock_ms=at)
            atomic_json(root/'runtime/tracking-runtime.json',value)
        result=dict(control=value,tracking=tracking,startup_note='Running host notices controls on its next scheduler tick; CLI alone never starts a host.')
    elif args.action=='sources':
        value=load(args.file)
        if value.get('schema')!='tracking-source-registry-v3' or not isinstance(value.get('contracts'),dict) or len(value['contracts'])>1000:raise ValueError('source_registry_schema')
        from .live_source import contract_metadata
        for contract,meta in value['contracts'].items():
            contract_metadata(value['contracts'],contract,meta.get('valid_from_ms'),meta.get('valid_to_ms'))
        with protection(root):atomic_json(root/'runtime/tracking-source-registry.json',value)
        result=dict(installed_contracts=len(value['contracts']),network_started=False)
    elif args.action=='export-sample':
        path=root/'runtime/tracking-real-samples.sqlite3'
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3)
        try:row=db.execute('SELECT payload_sha256,payload_json FROM samples WHERE sample_key=?',(args.sample_key,)).fetchone()
        finally:db.close()
        if not row or hashlib.sha256(row[1].encode()).hexdigest()!=row[0]:raise ValueError('original_sample_missing_or_hash_mismatch')
        destination=Path(args.output).resolve()
        if destination.is_relative_to(root):raise ValueError('sample_export_must_be_outside_runtime_root')
        with destination.open('x',encoding='utf-8') as stream:stream.write(row[1])
        result=dict(output=str(destination),sha256=row[0])
    elif args.action=='tick':result=tick(root)
    else:
        ledger=TrackingLedger.for_runtime(root);recovery=Recovery(ledger)
        if args.action=='grant':
            value=load(args.file,16384)
            if set(value)!={'operation_id','scope','starts_ms','expires_ms','max_attempts'}:raise ValueError('grant_fields')
            # Grant creation never enables network; max eight attached grants.
            with protection(root):
                cfg=config(root)
                if len(cfg['grant_ids'])>=8:raise ValueError('grant_slots_full_revoke_first')
                key=create_grant(recovery,**value)
                cfg['grant_ids'].append(key);atomic_json(root/'runtime/tracking-runtime.json',cfg)
            result=dict(grant_id=key,network_started=False)
        elif args.action=='revoke':
            recovery.revoke(args.grant_id,clock_ms=at)
            with protection(root):
                cfg=config(root);cfg['grant_ids']=[g for g in cfg['grant_ids'] if g!=args.grant_id]
                atomic_json(root/'runtime/tracking-runtime.json',cfg)
            result=dict(revoked=args.grant_id)
        elif args.action=='enqueue':
            value=load(args.file,16384)
            if set(value)!={'contract','source','start_ms','target_ms'}:raise ValueError('acquisition_fields')
            key=enqueue_verified(recovery,registry(root),**value,clock_ms=at)
            result=dict(job_key=key,network_started=False)
        elif args.action=='import-b-round':
            path=Path(args.file).resolve()
            if path.parent!=root/'runtime/b-rounds':raise ValueError('immutable_B_round_required')
            value=load(path,32*1024*1024);register_b_round(ledger,value)
            result=dict(registered=value['round_id'],network_started=False)
        elif args.action=='import-publication':
            import tracking_service
            path=Path(args.file).resolve()
            if path.parent.parent!=root/'scan-history' or not path.name.startswith('revision-') or path.suffix!='.json':raise ValueError('immutable_publication_required')
            value=load(path,64*1024*1024);status=load(path.parent/'status.json',1024*1024)
            if status.get('status') not in ('success','partial_success') or status.get('revision',0)<value['revision'] or (status.get('quality') or {}).get('accepted') is not True:
                raise ValueError('original_publication_not_committed')
            if value.get('scan_id')!=path.parent.name or path.name!='revision-%04d.json'%value['revision']:
                raise ValueError('original_publication_identity')
            tracking_service.prepare_publication(root,value,path.read_bytes())
            result=tracking_service.resume_publications(root,limit=20)
        elif args.action=='bridge':result=bridge_page(ledger,registry(root),after=args.after,clock_ms=at)
        elif args.action=='requeue':
            recovery.requeue(args.job_key,configuration_version=args.configuration_version,clock_ms=at)
            result=dict(requeued=args.job_key)
        elif args.action=='revision':
            reopen_revision(recovery,args.job_key,affected_ms=args.affected_ms,configuration_version=args.configuration_version,clock_ms=at)
            result=dict(reopened=args.job_key,old_evidence_preserved=True)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
