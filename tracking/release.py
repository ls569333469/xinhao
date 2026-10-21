"""Explicit source-only release/rollback tool. Never starts/stops any process.

Prepare captures a NEW baseline under quiescence. Apply requires a scope-authorized release record
for the pinned manifest and a stopped host. Rollback restores SOURCE
ONLY: no database overwrite, no loss of post-release rows. All entrypoints are
operator invoked; this package never runs release operations from startup/GET.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def load(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_new(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.flush();os.fsync(stream.fileno())


def safe(root,rel):
    if not isinstance(rel,str) or not rel or Path(rel).is_absolute() or ':' in rel:
        raise ValueError('release_relative_path_required')
    path=(root/rel).resolve()
    if not path.is_relative_to(root) or path==root:raise ValueError('release_path_escape')
    current=root
    for part in Path(rel).parts:
        current=current/part
        if current.is_symlink() or (hasattr(current,'is_junction') and current.is_junction()):raise ValueError('release_link_path')
    return path


def host_stopped():
    import socket
    for port in (8767,8768):
        with socket.socket() as sock:
            sock.settimeout(.5)
            if sock.connect_ex(('127.0.0.1',port))==0:raise ValueError('stop_original_host_before_install_or_rollback')


def approval(path,manifest_sha):
    obj=load(path)
    if obj.get('manifest_sha256')!=manifest_sha or not obj.get('reviewer'):
        raise ValueError('same_manifest_release_record_required')
    if obj.get('reviewer') in ('Codex','codex','implementer') and (obj.get('authorization')!='user_authorized_1019_plan' or obj.get('checks_passed') is not True):
        raise ValueError('user_authorization_and_checks_required')
    if obj.get('verdict')!='PASS' and not (obj.get('verdict')=='CONDITIONAL' and obj.get('approved_for_release') is True):
        raise ValueError('engineering_release_not_approved')
    return dict(path=str(Path(path).resolve()),sha256=sha(path),reviewer=obj['reviewer'],verdict=obj['verdict'])


def snapshot_db(source,destination):
    destination.parent.mkdir(parents=True,exist_ok=True)
    src=sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
    dst=sqlite3.connect(destination,timeout=3)
    deadline=time.monotonic()+120
    def progress(status,remaining,total):
        if time.monotonic()>deadline:raise TimeoutError('bounded_release_backup_deadline')
    try:src.backup(dst,pages=256,progress=progress,sleep=.01)
    finally:dst.close();src.close()
    return dict(sha256=sha(destination),bytes=destination.stat().st_size)


def table_hashes(path):
    # All declared logical primary keys and normalized complete payloads, including
    # every 1H revision. No row-count-only claim. Per-database snapshot boundary.
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
    try:
        result=[]
        for (name,ddl) in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            quoted='"'+name.replace('"','""')+'"'
            columns=list(db.execute('PRAGMA table_info('+quoted+')'))
            keys=[r[1] for r in sorted(columns,key=lambda r:r[5]) if r[5]]
            names=[r[1] for r in columns]
            order=keys or names
            def q(s):return '"'+s.replace('"','""')+'"'
            payload=hashlib.sha256();keyhash=hashlib.sha256();count=0
            for row in db.execute('SELECT * FROM '+quoted+' ORDER BY '+','.join(q(c) for c in order)):
                values=[dict(blob_sha256=hashlib.sha256(v).hexdigest(),bytes=len(v)) if isinstance(v,bytes) else v for v in row]
                data=json.dumps(values,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
                payload.update(len(data).to_bytes(8,'big'));payload.update(data)
                kval=[values[names.index(k)] for k in keys] if keys else values
                key=json.dumps(kval,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
                keyhash.update(len(key).to_bytes(8,'big'));keyhash.update(key);count+=1
            result.append(dict(table=name,rows=count,primary_key=keys,columns=names,ddl=ddl,
                logical_keys_sha256=keyhash.hexdigest(),ordered_payload_sha256=payload.hexdigest(),
                key_scope='declared_primary_key' if keys else 'entire_row_fallback_no_declared_key'))
        return result
    finally:db.close()


def check_bundle(manifest_path):
    manifest_path=Path(manifest_path).resolve();manifest=load(manifest_path)
    candidate=Path(manifest['candidate_root']).resolve()
    seen=set()
    for row in manifest['dependencies']:
        if row['path'] in seen:raise ValueError('manifest_duplicate_dependency')
        seen.add(row['path'])
        if sha(safe(candidate,row['path']))!=row['sha256']:raise ValueError('candidate_manifest_hash_mismatch:'+row['path'])
    if any(row['path'] not in seen for row in manifest['files']):raise ValueError('install_outside_dependencies')
    return manifest,candidate,sha(manifest_path)


def prepare(root,manifest_path,approval_path,output):
    manifest,candidate,digest=check_bundle(manifest_path);review=approval(approval_path,digest)
    output=Path(output).resolve()
    if output.is_relative_to(root) or root.is_relative_to(output):raise ValueError('release_backup_must_be_outside_project')
    # Require stopped host for an exact NEW baseline; this tool never kills it.
    host_stopped()
    if output.exists():raise ValueError('release_backup_directory_must_be_new')
    output.mkdir(parents=True)
    files=[]
    for item in manifest['files']:
        target=safe(root,item['path']);expected=item.get('production_readonly_sha256')
        actual=sha(target) if target.is_file() else None
        if actual!=expected:raise ValueError('production_source_drift:'+item['path'])
        backup=None
        if target.is_file():
            backup=safe(output/'source',item['path']);backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,backup)
        files.append(dict(path=item['path'],before_sha256=actual,installed_sha256=item['sha256']))
    databases=[]
    for source in sorted((root/'runtime').rglob('*')):
        if not source.is_file() or source.suffix not in ('.sqlite3','.sqlite','.db'):continue
        relative=source.relative_to(root).as_posix();dst=safe(output/'databases',relative)
        entry=dict(path=relative,**snapshot_db(source,dst),tables=table_hashes(dst))
        databases.append(entry)
    # Immutable candle chunks/publication inputs are part of recovery, not just DB indexes.
    payload_files=[]
    for rel in ('runtime/candle-cache/blocks','runtime/shared-candles','runtime/b-source','scan-history'):
        folder=root/rel
        if not folder.is_dir():continue
        for source in sorted(folder.rglob('*')):
            if not source.is_file():continue
            relative=source.relative_to(root).as_posix();destination=safe(output/'payloads',relative)
            destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
            payload_files.append(dict(path=relative,sha256=sha(destination),bytes=destination.stat().st_size))
    for rel in ('scan.json','runtime/tracking-installed-manifest.json'):
        source=safe(root,rel)
        if source.is_file():
            destination=safe(output/'payloads',rel);destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
            payload_files.append(dict(path=rel,sha256=sha(destination),bytes=destination.stat().st_size))
    # Keep switches, saved schedules and metadata; secrets are not copied here.
    switches=[]
    for rel in ('tracking-control.json','runtime/tracking-runtime.json','runtime/tracking-source-registry.json'):
        target=safe(root,rel)
        if target.is_file():
            dst=safe(output/'controls',rel);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,dst)
            switches.append(dict(path=rel,sha256=sha(target)))
    record=dict(schema='tracking-release-v3',prepared_at_ms=int(time.time()*1000),root=str(root),
        manifest_path=str(Path(manifest_path).resolve()),manifest_sha256=digest,review=review,files=files,
        databases=databases,payload_files=payload_files,switches=switches,state='PREPARED_NOT_INSTALLED',cross_database_atomic=False)
    write_new(output/'release.json',record)
    return record


def replace_bytes(path,data):
    from durable_io import replace_atomic
    path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name(path.name+'.tracking-install.tmp')
    with temp.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
    replace_atomic(temp,path)


def apply(root,manifest_path,approval_path,backup):
    manifest,candidate,digest=check_bundle(manifest_path);approval(approval_path,digest);host_stopped()
    backup=Path(backup).resolve();record=load(backup/'release.json')
    if record['root']!=str(root) or record['manifest_sha256']!=digest:raise ValueError('release_preparation_identity_mismatch')
    for switch in record['switches']:
        if sha(safe(root,switch['path']))!=switch['sha256']:raise ValueError('runtime_control_changed_since_prepare')
    # New write capabilities must remain closed during source installation.
    from .runtime import config
    if config(root)['enabled'] or config(root)['network_enabled']:raise ValueError('disable_new_runtime_before_install')
    # Backup must still correspond to the immediate before-install state.
    for item in record['databases']:
        if sha(safe(root,item['path']))!=item['sha256']:
            # SQLite backup may differ bytewise. Compare complete logical tables.
            if table_hashes(safe(root,item['path']))!=item['tables']:raise ValueError('release_database_changed_since_prepare')
    for item in record['files']:
        target=safe(root,item['path']);actual=sha(target) if target.is_file() else None
        if actual!=item['before_sha256']:raise ValueError('source_changed_since_prepare:'+item['path'])
    changed=[]
    try:
        for item in manifest['files']:
            target=safe(root,item['path']);replace_bytes(target,safe(candidate,item['path']).read_bytes());changed.append(item['path'])
        # New ability defaults off; preserve existing switches exactly.
        identity=dict(candidate_version=manifest['candidate_version'],manifest_sha256=digest,git_commit=manifest.get('git_commit'),authorization='user_authorized_1019_plan',installed_at_ms=int(time.time()*1000),
            files=[dict(path=i['path'],sha256=i['sha256']) for i in manifest['dependencies']],new_features_started=False)
        from durable_io import atomic_json
        atomic_json(root/'runtime/tracking-installed-manifest.json',identity)
        write_new(backup/'installed.json',dict(manifest_sha256=digest,changed=changed,state='INSTALLED_HOST_NOT_STARTED'))
        return identity
    except BaseException as exc:
        write_new(backup/'install-error.json',dict(changed=changed,error=type(exc).__name__+':'+str(exc),host_must_remain_stopped=True))
        raise


def rollback(root,backup):
    host_stopped();backup=Path(backup).resolve();record=load(backup/'release.json')
    if record['root']!=str(root):raise ValueError('rollback_root_mismatch')
    restored=[];retained_new=[]
    for item in record['files']:
        target=safe(root,item['path']);actual=sha(target) if target.is_file() else None
        if actual not in (item['before_sha256'],item['installed_sha256']):raise ValueError('rollback_would_overwrite_unrelated_edit:'+item['path'])
    for item in record['files']:
        if item['before_sha256'] is None:
            # Additive unused modules remain; no recursive delete or DB rollback.
            retained_new.append(item['path']);continue
        original=safe(backup/'source',item['path'])
        if sha(original)!=item['before_sha256']:raise ValueError('source_backup_corrupt')
        replace_bytes(safe(root,item['path']),original.read_bytes());restored.append(item['path'])
    identity=root/'runtime/tracking-installed-manifest.json'
    if identity.is_file():
        # Keep its historical identity in backup; do not advertise rolled-back source as loaded v3.
        archived=backup/'rolled-back-installed-identity.json'
        if not archived.exists():shutil.copy2(identity,archived)
        identity.unlink()
    # New-schema tracking remains in place. Old host ignores additive tables.
    from durable_io import atomic_json
    atomic_json(root/'runtime/tracking-rollback.json',dict(at_ms=int(time.time()*1000),source_restored=restored,
        additive_modules_retained=retained_new,database_restored=False,new_rows_preserved=True))
    return dict(restored=restored,additive_modules_retained=retained_new,database_restored=False,host_started=False)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Source-only release preparation/install/rollback; never restart host')
    parser.add_argument('--root',required=True);sub=parser.add_subparsers(dest='action',required=True)
    for name in ('prepare','apply'):
        command=sub.add_parser(name);command.add_argument('--manifest',required=True);command.add_argument('--approval',required=True)
        command.add_argument('--backup',required=True)
    command=sub.add_parser('rollback');command.add_argument('--backup',required=True)
    args=parser.parse_args(argv);root=Path(args.root).resolve()
    if not root.is_dir():raise ValueError('release_root_missing')
    if args.action=='prepare':result=prepare(root,args.manifest,args.approval,args.backup)
    elif args.action=='apply':result=apply(root,args.manifest,args.approval,args.backup)
    else:result=rollback(root,args.backup)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

