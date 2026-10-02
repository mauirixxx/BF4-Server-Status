from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from datetime import datetime, timezone
import logging
import socket
import psycopg
from . import queries
from .config import get_settings
from .db import db_connection
settings = get_settings(); logger = logging.getLogger(__name__)
SITE_NAMES = {"hnl":"Honolulu","kah":"Kahului","mak":"Makawao","rnt":"Rental"}

def _human_duration(seconds: int | None, future: bool=False)->str:
    if seconds is None: return "—"
    seconds=max(0,int(seconds)); days,rem=divmod(seconds,86400); hours,rem=divmod(rem,3600); minutes,secs=divmod(rem,60)
    value=f"{days}d {hours}h" if days else f"{hours}h {minutes}m" if hours else f"{minutes}m {secs}s" if minutes else f"{secs}s"
    return f"{value} from now" if future else f"{value} ago"

def _uptime_text(started_at):
    if not started_at: return "—"
    seconds=max(0,int((datetime.now(timezone.utc)-started_at).total_seconds())); days,rem=divmod(seconds,86400); hours,rem=divmod(rem,3600); minutes,_=divmod(rem,60)
    return f"{days}d {hours}h" if days else f"{hours}h {minutes}m" if hours else f"{minutes}m"

@dataclass
class Metric:
    value:int|None; available:bool=False; note:str|None=None

def _scalar(sql:str,*,optional:bool=False)->Metric:
    try:
        with db_connection() as conn: row=conn.execute(sql).fetchone()
    except psycopg.Error as exc:
        if not optional: raise
        logger.warning("Optional dashboard metric unavailable: %s",exc); return Metric(None,False,"Temporarily unavailable")
    if not row: return Metric(None if optional else 0,not optional,"Not initialized" if optional else None)
    return Metric(int(next(iter(row.values())) or 0),True)

def _freshness_params(): return {"strict_seconds":settings.snapshot_fresh_seconds,"adaptive_seconds":settings.snapshot_adaptive_seconds}
def get_summary(): return {"player_names":_scalar(queries.PLAYER_NAMES_OBSERVED_SQL,optional=True),"unique_servers":_scalar(queries.UNIQUE_SERVERS_SQL),"default_refs":_scalar(queries.DEFAULT_SERVER_REFERENCES_SQL),"default_servers":_scalar(queries.DEFAULT_PHYSICAL_SERVERS_SQL),"lifecycle_confirmed":_scalar(queries.LIFECYCLE_CONFIRMED_SQL),"lifecycle_discovered":_scalar(queries.LIFECYCLE_DISCOVERED_SQL),"lifecycle_grace":_scalar(queries.LIFECYCLE_GRACE_SQL),"lifecycle_retired":_scalar(queries.LIFECYCLE_RETIRED_SQL)}

def get_platforms():
    with db_connection() as conn: rows=conn.execute(queries.CURRENT_PLATFORM_PLAYERS_SQL,_freshness_params()).fetchall()
    result=[]
    for row in rows:
        item=dict(row); coverage=item.pop("coverage_pct",None); strict=item.pop("strict_coverage_pct",None)
        item["coverage_pct"]=float(coverage) if isinstance(coverage,Decimal) else coverage; item["strict_coverage_pct"]=float(strict) if isinstance(strict,Decimal) else strict; item["available"]=True; result.append(item)
    return result

def _worker_health_state(worker):
    if not worker.get("enabled",True): return "disabled"
    if worker.get("draining"): return "draining"
    age=worker.get("heartbeat_age_seconds")
    if age is None:return "unknown"
    if age<=settings.worker_healthy_seconds:return "healthy"
    if age<=settings.worker_warning_seconds:return "warning"
    return "stale"

def get_workers():
    with db_connection() as conn: rows=[dict(r) for r in conn.execute(queries.WORKER_HEALTH_SQL).fetchall()]
    for w in rows: w["health_state"]=_worker_health_state(w); w["location_name"]=SITE_NAMES.get(w.get("site_code"),w.get("site_code") or "Unknown"); w["uptime_text"]=_uptime_text(w.get("started_at"))
    return rows

def get_leadership():
    with db_connection() as conn: rows=[dict(r) for r in conn.execute(queries.LEADERSHIP_SQL).fetchall()]
    now=datetime.now(timezone.utc)
    for lease in rows:
        site=lease.get("owner_site_code"); lease["location_name"]=SITE_NAMES.get(site,site or "Unknown"); t=str(lease.get("lease_type") or "").replace("_"," ").strip(); lease["lease_type_display"]=t.title() if t else "Lease"; acquired=lease.get("acquired_at"); lease["acquired_text"]=_human_duration(int((now-acquired).total_seconds()) if acquired else None); lease["expires_text"]=_human_duration(lease.get("expires_in_seconds"),future=True)
    return rows

def get_cluster_health(workers,leadership):
    counts={k:0 for k in ("healthy","warning","stale","draining","disabled","unknown")}
    for w in workers: counts[w.get("health_state","unknown")]=counts.get(w.get("health_state","unknown"),0)+1
    total=len(workers); active=total-counts["disabled"]; problem=counts["warning"]+counts["stale"]+counts["unknown"]
    overall="unknown" if active==0 else "degraded" if counts["stale"] or counts["unknown"] else "warning" if counts["warning"] else "healthy"
    return {"total":total,"active":active,"healthy":counts["healthy"],"warning":counts["warning"],"stale":counts["stale"],"draining":counts["draining"],"disabled":counts["disabled"],"unknown":counts["unknown"],"problem":problem,"overall":overall,"active_leases":len(leadership)}

def get_snapshot_health():
    with db_connection() as conn: row=conn.execute(queries.SNAPSHOT_HEALTH_SQL,_freshness_params()).fetchone() or {}
    result=dict(row)
    for k in ("fresh_coverage_pct","adaptive_coverage_pct"):
        if isinstance(result.get(k),Decimal): result[k]=float(result[k])
    result["adaptive_health_state"]="healthy" if float(result.get("adaptive_coverage_pct") or 0)>=settings.presence_healthy_coverage_pct else "below-target"; result["available"]=True; return result

def get_population_history(range_name="24h"):
    from .history_queries import HISTORY_SQL,HISTORY_STARTED_SQL
    ranges={"24h":("24 hours","5 minutes"),"7d":("7 days","30 minutes"),"30d":("30 days","1 hour")}; range_name=range_name if range_name in ranges else "24h"; duration,bucket=ranges[range_name]
    try:
        with db_connection() as conn: started=conn.execute(HISTORY_STARTED_SQL).fetchone(); rows=[dict(r) for r in conn.execute(HISTORY_SQL,{"duration":duration,"bucket":bucket}).fetchall()]
    except Exception as exc: return {"available":False,"range":range_name,"bucket":bucket,"collection_started_at":None,"adaptive_started_at":None,"series":[],"reason":f"Population history unavailable: {type(exc).__name__}"}
    platforms={name:[] for name in ("PC","Xbox","PlayStation")}
    for row in rows:
        cov=row.get("coverage_pct"); strict=row.get("strict_coverage_pct"); ap=row.get("adaptive_player_count"); ass=row.get("adaptive_snapshot_count")
        point={"sampled_at":row["sampled_at"].isoformat(),"players":int(row.get("display_player_count") or 0),"raw_players":int(row.get("display_player_count") or 0),"server_count":int(row.get("server_count") or 0),"snapshot_count":int(row.get("snapshot_count") or 0),"usable_snapshot_count":int(row.get("display_snapshot_count") or 0),"fresh_snapshot_count":int(row.get("strict_snapshot_count") or 0),"strict_players":int(row.get("strict_player_count") or 0),"adaptive_players":int(ap) if ap is not None else None,"adaptive_snapshot_count":int(ass) if ass is not None else None,"coverage_pct":float(cov) if isinstance(cov,Decimal) else cov,"strict_coverage_pct":float(strict) if isinstance(strict,Decimal) else strict,"population_mode":row.get("population_mode") or "strict","adaptive_samples":int(row.get("adaptive_samples") or 0),"total_samples":int(row.get("total_samples") or 0)}; platforms.setdefault(row["platform"],[]).append(point)
    started_at=started.get("collection_started_at") if started else None; adaptive_started_at=started.get("adaptive_started_at") if started else None
    return {"available":True,"range":range_name,"bucket":bucket,"collection_started_at":started_at.isoformat() if started_at else None,"adaptive_started_at":adaptive_started_at.isoformat() if adaptive_started_at else None,"smoothing_samples":3 if range_name=="24h" else 1,"series":[{"platform":p,"points":platforms.get(p,[])} for p in ("PC","Xbox","PlayStation")]}

def _configured_nodes(value):
    result=[]
    for item in (value or "").split(","):
        item=item.strip()
        if not item: continue
        label,sep,host=item.partition("=")
        host=(host if sep else label).strip(); label=(label if sep else host).strip()
        if host: result.append((label,host))
    return result

def get_database_nodes():
    nodes=[]
    for label,host in _configured_nodes(settings.database_nodes):
        item={"label":label,"host":host,"online":False,"role":"unknown"}
        try:
            with psycopg.connect(settings.database_url, host=host, connect_timeout=max(1,int(settings.infrastructure_probe_timeout_seconds)), autocommit=True, row_factory=psycopg.rows.dict_row) as conn:
                row=conn.execute("SELECT pg_is_in_recovery() AS recovery").fetchone()
                item["online"]=True; item["role"]="replica" if row and row["recovery"] else "primary"
        except Exception as exc:
            logger.info("Database node probe failed for %s: %s",label,type(exc).__name__)
        nodes.append(item)
    return nodes

def get_dns_nodes():
    nodes=[]
    timeout=float(settings.infrastructure_probe_timeout_seconds)
    for label,host in _configured_nodes(settings.dns_nodes):
        online=False
        try:
            with socket.create_connection((host,53),timeout=timeout): online=True
        except OSError: pass
        nodes.append({"label":label,"host":host,"online":online})
    return nodes

def get_database_facts():
    try:
        with db_connection() as conn:
            row=conn.execute("""SELECT pg_database_size(current_database()) AS size_bytes, d.xact_commit+d.xact_rollback AS transactions, d.stats_reset FROM pg_stat_database d WHERE d.datname=current_database()""").fetchone()
        if not row: return {"available":False}
        size=int(row["size_bytes"] or 0); tx=int(row["transactions"] or 0); reset=row.get("stats_reset")
        per_day=per_hour=per_minute=None
        if reset:
            elapsed_seconds=max((datetime.now(timezone.utc)-reset).total_seconds(),1)
            per_second=tx/elapsed_seconds
            per_day=round(per_second*86400); per_hour=round(per_second*3600); per_minute=round(per_second*60)
        return {"available":True,"size_bytes":size,"size_text":f"{size/(1024**3):.2f} GiB" if size>=1024**3 else f"{size/(1024**2):.1f} MiB","transactions":tx,"transactions_per_day":per_day,"transactions_per_hour":per_hour,"transactions_per_minute":per_minute,"stats_reset":reset}
    except Exception as exc:
        logger.info("Database facts unavailable: %s",type(exc).__name__); return {"available":False}
