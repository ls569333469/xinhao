"""Process-load identity, captured once by the host, without writing data."""
import hashlib
import json
from pathlib import Path


def capture(root,*,source_root=None):
    root=Path(root).resolve();source_root=Path(source_root or root).resolve();path=root/'runtime/tracking-installed-manifest.json'
    if not path.is_file():return dict(installed=False,verification='no_install_identity')
    if path.stat().st_size>2*1024*1024:raise ValueError('installed_identity_size')
    obj=json.loads(path.read_text(encoding='utf-8-sig'))
    files=obj.get('files')
    if not isinstance(files,list) or not 1<=len(files)<=1000:raise ValueError('installed_identity_files')
    mismatches=[]
    for item in files:
        rel=item.get('path')
        if not isinstance(rel,str):raise ValueError('installed_identity_path')
        target=(source_root/rel).resolve()
        if not target.is_relative_to(source_root):raise ValueError('installed_identity_path')
        actual=hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        if actual!=item.get('sha256'):mismatches.append(rel)
    return dict(installed=True,candidate_version=obj.get('candidate_version'),
        manifest_sha256=obj.get('manifest_sha256'),checked_files=len(files),mismatches=mismatches,
        identity_matches_disk_at_process_load=not mismatches,
        scope='file_bytes_at_host_import_not_all_modules_exercised',code_root=str(source_root),data_root=str(root),git_commit=obj.get('git_commit'),acceptance_authorization=obj.get('authorization','historical_record'))
