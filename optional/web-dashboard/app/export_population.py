"""UTC monthly/yearly exports of original five-minute dashboard population samples."""
from __future__ import annotations
import argparse,csv,gzip,hashlib,json,os,tempfile
from datetime import datetime,timezone
from pathlib import Path
import psycopg

COLUMNS=('sampled_at','platform','server_count','player_count','snapshot_count','fresh_snapshot_count','adaptive_player_count','adaptive_snapshot_count')
SQL='SELECT '+','.join(COLUMNS)+' FROM dashboard_population_samples WHERE sampled_at >= %s AND sampled_at < %s ORDER BY sampled_at,platform'

def boundaries(period):
    if len(period)==4 and period.isdigit():
        year=int(period);return datetime(year,1,1,tzinfo=timezone.utc),datetime(year+1,1,1,tzinfo=timezone.utc)
    if len(period)==7 and period[4]=='-':
        year,month=map(int,period.split('-'));start=datetime(year,month,1,tzinfo=timezone.utc)
        end=datetime(year+1,1,1,tzinfo=timezone.utc) if month==12 else datetime(year,month+1,1,tzinfo=timezone.utc)
        return start,end
    raise ValueError('Use YYYY or YYYY-MM')

def export(period,destination,force=False):
    start,end=boundaries(period)
    if end>datetime.now(timezone.utc):raise ValueError('Only completed UTC months/years can be exported')
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    base='population-'+period
    targets=[destination/(base+'.'+ext+'.gz') for ext in ('csv','json')]
    if any(p.exists() for p in targets) and not force:
        if all(p.exists() for p in targets):print('Already archived:',period);return
        raise RuntimeError('Partial archive exists; inspect before retrying with --force')
    temps=[];count=0
    try:
        with tempfile.NamedTemporaryFile(dir=destination,prefix='.export-',delete=False) as a,tempfile.NamedTemporaryFile(dir=destination,prefix='.export-',delete=False) as b:
            temps=[Path(a.name),Path(b.name)]
        with gzip.open(temps[0],'wt',encoding='utf-8',newline='') as cf,gzip.open(temps[1],'wt',encoding='utf-8') as jf:
            writer=csv.writer(cf);writer.writerow(COLUMNS);jf.write('[\n');first=True
            with psycopg.connect(os.environ['DATABASE_URL'],autocommit=False) as conn:
                with conn.cursor(name='population_export') as cur:
                    cur.execute(SQL,(start,end))
                    for row in cur:
                        values=[v.isoformat() if isinstance(v,datetime) else v for v in row]
                        writer.writerow(values)
                        if not first:jf.write(',\n')
                        jf.write(json.dumps(dict(zip(COLUMNS,values)),separators=(',',':')))
                        first=False;count+=1
            jf.write('\n]\n')
        for path in temps:
            with gzip.open(path,'rb') as f:
                while f.read(1024*1024):pass
        if count==0:raise RuntimeError('No population samples in period; refusing empty archive')
        for source,target in zip(temps,targets):os.replace(source,target)
        for target in targets:
            digest=hashlib.sha256(target.read_bytes()).hexdigest()
            print(f'{target.name}: {target.stat().st_size} bytes, sha256={digest}')
        print('Rows:',count)
    finally:
        for path in temps:path.unlink(missing_ok=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--period');p.add_argument('--scheduled',action='store_true');p.add_argument('--output',default='/var/lib/bf4-status-dashboard/exports');p.add_argument('--force',action='store_true');args=p.parse_args()
    now=datetime.now(timezone.utc)
    if args.scheduled and (now.day!=1 or now.hour!=0):return
    previous=now.replace(day=1)
    previous=(previous.replace(year=previous.year-1,month=12) if previous.month==1 else previous.replace(month=previous.month-1))
    periods=[args.period] if args.period else [previous.strftime('%Y-%m')]+([str(now.year-1)] if now.month==1 else [])
    for period in periods:export(period,args.output,args.force)
if __name__=='__main__':main()
