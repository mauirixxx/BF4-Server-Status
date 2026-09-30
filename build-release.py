#!/usr/bin/env python3
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import hashlib, shutil, subprocess

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'dist'
VERSION='v3.1.3'
NORMAL=f'BF4_Server_Watcher_{VERSION}.zip'
HA=f'BF4_Server_Watcher_{VERSION}-postgresql-ha.zip'
DOCS=f'BF4_Server_Watcher_{VERSION}-docs.zip'

normal_files=['.env.example','Dockerfile','docker-compose.yml','docker-compose.worker-agent.yml','entrypoint.sh','requirements.txt','alembic.ini','serverwatcher.py','worker_agent.py','discord_leader.py','control_plane.py','operator_notifications.py','migrate_with_lock.py','db.py','models.py','LICENSE','THIRD_PARTY.md','README.md','CHANGELOG.md','RELEASE-v3.1.3.md']
normal_files += [str(p.relative_to(ROOT)) for p in sorted((ROOT/'alembic').rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc','.pyo'}]
ha_files=[str(p.relative_to(ROOT/'postgresql-ha')) for p in sorted((ROOT/'postgresql-ha').rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc','.pyo'}]
doc_files=['README.md','CHANGELOG.md','HF1_CHANGELOG.md','HF2_CHANGELOG.md','RELEASE-v3.1.3.md','THIRD_PARTY.md','postgresql-ha/README.md','postgresql-ha/RELEASE-NOTES.md','postgresql-ha/LIVE-VALIDATION-20260929.md','postgresql-ha/CHANGELOG.md','postgresql-ha/BUILD-INFO.txt']

def make(name, files, base=ROOT):
    with ZipFile(OUT/name,'w',ZIP_DEFLATED) as z:
        for rel in files:
            p=base/rel
            if not p.is_file(): raise SystemExit(f'missing release input: {p}')
            z.write(p,rel)

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

shutil.rmtree(OUT,ignore_errors=True); OUT.mkdir()
make(NORMAL,normal_files)
make(HA,ha_files,ROOT/'postgresql-ha')
make(DOCS,doc_files)
lines=[f'{sha(OUT/n)}  {n}' for n in (NORMAL,HA,DOCS)]
(OUT/f'BF4_Server_Watcher_{VERSION}-SHA256SUMS.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
