from __future__ import annotations
from datetime import timedelta
from sqlalchemy import func, or_, select, text
from db import SessionLocal
from models import ClusterOperatorDestination, ClusterOperatorEvent, ClusterOperatorEventDelivery, ClusterLease, ClusterWorker, ClusterWorkerCapability

RECOVERY_TYPES={"worker_recovered","capability_recovered"}
ALERT_TYPES={"worker_stale","capability_unavailable","handoff_failed","leadership_authority_expired"}

def delivery_class(event):
    if event.event_type in RECOVERY_TYPES: return "recovery"
    if event.event_type in ALERT_TYPES or event.severity in {"warning","critical","error"}: return "alert"
    return "info"

def bootstrap_primary_operator(user_id:int):
    if not user_id: return
    with SessionLocal.begin() as s:
        s.execute(text("SELECT pg_advisory_xact_lock(hashtext('bf4:primary-operator-bootstrap'))"))
        now=s.scalar(select(func.now()))
        rows=list(s.scalars(select(ClusterOperatorDestination).where(ClusterOperatorDestination.is_primary.is_(True)).with_for_update()))
        for row in rows:
            if row.discord_user_id != user_id:
                raise RuntimeError(f"PRIMARY_OPERATOR_DISCORD_USER_ID mismatch: database={row.discord_user_id} local={user_id}")
        row=s.scalar(select(ClusterOperatorDestination).where(ClusterOperatorDestination.destination_type=="dm",ClusterOperatorDestination.discord_user_id==user_id))
        if row is None:
            s.add(ClusterOperatorDestination(destination_type="dm",discord_user_id=user_id,enabled=True,is_primary=True,description="Primary cluster operator",created_at=now,updated_at=now))
        else:
            row.enabled=True; row.is_primary=True; row.updated_at=now

def is_operator(user_id:int)->bool:
    with SessionLocal() as s:
        return s.scalar(select(ClusterOperatorDestination.id).where(ClusterOperatorDestination.destination_type=="dm",ClusterOperatorDestination.discord_user_id==user_id,ClusterOperatorDestination.enabled.is_(True)).limit(1)) is not None

def list_destinations():
    with SessionLocal() as s:
        return list(s.scalars(select(ClusterOperatorDestination).order_by(ClusterOperatorDestination.destination_type,ClusterOperatorDestination.id)))

def add_dm(user_id:int,user_name:str|None=None,description:str|None=None):
    with SessionLocal.begin() as s:
        now=s.scalar(select(func.now())); row=s.scalar(select(ClusterOperatorDestination).where(ClusterOperatorDestination.destination_type=="dm",ClusterOperatorDestination.discord_user_id==user_id))
        if row is None:
            row=ClusterOperatorDestination(destination_type="dm",discord_user_id=user_id,user_name=user_name,description=description,enabled=True,is_primary=False,created_at=now,updated_at=now); s.add(row); s.flush()
        else: row.user_name=user_name or row.user_name; row.description=description if description is not None else row.description; row.enabled=True; row.updated_at=now
        return int(row.id)

def add_channel(guild_id:int,channel_id:int,guild_name:str|None=None,channel_name:str|None=None,description:str|None=None):
    with SessionLocal.begin() as s:
        now=s.scalar(select(func.now())); row=s.scalar(select(ClusterOperatorDestination).where(ClusterOperatorDestination.destination_type=="channel",ClusterOperatorDestination.discord_guild_id==guild_id,ClusterOperatorDestination.discord_channel_id==channel_id))
        if row is None:
            row=ClusterOperatorDestination(destination_type="channel",discord_guild_id=guild_id,discord_channel_id=channel_id,guild_name=guild_name,channel_name=channel_name,description=description,enabled=True,is_primary=False,created_at=now,updated_at=now); s.add(row); s.flush()
        else: row.guild_name=guild_name or row.guild_name; row.channel_name=channel_name or row.channel_name; row.description=description if description is not None else row.description; row.enabled=True; row.updated_at=now
        return int(row.id)

def set_destination_enabled(destination_id:int,enabled:bool):
    with SessionLocal.begin() as s:
        row=s.get(ClusterOperatorDestination,destination_id)
        if not row: raise ValueError("destination not found")
        if row.is_primary and not enabled: raise ValueError("primary operator cannot be disabled")
        if row.destination_type=="dm" and not enabled:
            count=s.scalar(select(func.count()).select_from(ClusterOperatorDestination).where(ClusterOperatorDestination.destination_type=="dm",ClusterOperatorDestination.enabled.is_(True)))
            if int(count or 0)<=1: raise ValueError("cannot disable the last enabled operator")
        row.enabled=enabled; row.updated_at=s.scalar(select(func.now()))

def remove_destination(destination_id:int):
    with SessionLocal.begin() as s:
        row=s.get(ClusterOperatorDestination,destination_id)
        if not row: raise ValueError("destination not found")
        if row.is_primary: raise ValueError("primary operator cannot be removed")
        if row.destination_type=="dm":
            count=s.scalar(select(func.count()).select_from(ClusterOperatorDestination).where(ClusterOperatorDestination.destination_type=="dm",ClusterOperatorDestination.enabled.is_(True)))
            if row.enabled and int(count or 0)<=1: raise ValueError("cannot remove the last enabled operator")
        s.delete(row)

def ensure_delivery_rows():
    with SessionLocal.begin() as s:
        now=s.scalar(select(func.now())); events=list(s.scalars(select(ClusterOperatorEvent).order_by(ClusterOperatorEvent.id)))
        dests=list(s.scalars(select(ClusterOperatorDestination).where(ClusterOperatorDestination.enabled.is_(True))))
        for e in events:
            cls=delivery_class(e)
            for d in dests:
                if e.created_at < d.created_at: continue
                if d.destination_type=="dm" and cls=="info": continue
                exists=s.scalar(select(ClusterOperatorEventDelivery.id).where(ClusterOperatorEventDelivery.event_id==e.id,ClusterOperatorEventDelivery.destination_id==d.id))
                if not exists: s.add(ClusterOperatorEventDelivery(event_id=e.id,destination_id=d.id,status="pending",attempt_count=0,next_attempt_at=now,created_at=now,updated_at=now))

def due_deliveries(limit=25):
    with SessionLocal() as s:
        now=s.scalar(select(func.now()))
        rows=s.execute(select(ClusterOperatorEventDelivery,ClusterOperatorEvent,ClusterOperatorDestination).join(ClusterOperatorEvent,ClusterOperatorEvent.id==ClusterOperatorEventDelivery.event_id).join(ClusterOperatorDestination,ClusterOperatorDestination.id==ClusterOperatorEventDelivery.destination_id).where(ClusterOperatorDestination.enabled.is_(True),ClusterOperatorEventDelivery.status.in_(("pending","retry")),or_(ClusterOperatorEventDelivery.next_attempt_at.is_(None),ClusterOperatorEventDelivery.next_attempt_at<=now)).order_by(ClusterOperatorEventDelivery.id).limit(limit)).all()
        return [(d.id,e.id,e.event_type,e.severity,e.message,e.first_seen_at,x.id,x.destination_type,x.discord_user_id,x.discord_guild_id,x.discord_channel_id) for d,e,x in rows]

def mark_delivery_success(delivery_id:int):
    with SessionLocal.begin() as s:
        now=s.scalar(select(func.now())); d=s.get(ClusterOperatorEventDelivery,delivery_id)
        if not d:return
        d.status="delivered"; d.attempt_count+=1; d.last_attempt_at=now; d.delivered_at=now; d.last_error=None; d.updated_at=now
        x=s.get(ClusterOperatorDestination,d.destination_id); x.last_success_at=now; x.last_failure_reason=None; x.updated_at=now

def mark_delivery_failure(delivery_id:int,error:str,permanent:bool,initial:int,max_seconds:int,permanent_seconds:int):
    with SessionLocal.begin() as s:
        now=s.scalar(select(func.now())); d=s.get(ClusterOperatorEventDelivery,delivery_id)
        if not d:return
        d.attempt_count+=1; delay=permanent_seconds if permanent else min(max_seconds,initial*(2**max(0,d.attempt_count-1)))
        d.status="retry"; d.last_attempt_at=now; d.next_attempt_at=now+timedelta(seconds=delay); d.last_error=error[:255]; d.updated_at=now
        x=s.get(ClusterOperatorDestination,d.destination_id); x.last_failure_at=now; x.last_failure_reason=error[:255]; x.updated_at=now

def cluster_status_snapshot():
    with SessionLocal() as s:
        lease=s.get(ClusterLease,"discord:leader")
        workers=list(s.scalars(select(ClusterWorker).order_by(ClusterWorker.worker_id)))
        caps={c.worker_id:c for c in s.scalars(select(ClusterWorkerCapability).where(ClusterWorkerCapability.capability_name=="discord"))}
        dests=list(s.scalars(select(ClusterOperatorDestination).order_by(ClusterOperatorDestination.destination_type,ClusterOperatorDestination.id)))
        pending=int(s.scalar(select(func.count()).select_from(ClusterOperatorEventDelivery).where(ClusterOperatorEventDelivery.status=="pending")) or 0)
        retry=int(s.scalar(select(func.count()).select_from(ClusterOperatorEventDelivery).where(ClusterOperatorEventDelivery.status=="retry")) or 0)
        problems=list(s.scalars(select(ClusterOperatorEvent).where(ClusterOperatorEvent.active.is_(True)).order_by(ClusterOperatorEvent.id)))
        return lease,workers,caps,dests,pending,retry,problems

def set_notifications_enabled(enabled:bool,updated_by:str):
    from models import ClusterRuntimeSetting
    with SessionLocal.begin() as s:
        row=s.scalar(select(ClusterRuntimeSetting).where(ClusterRuntimeSetting.setting_key=="operator.notifications_enabled",ClusterRuntimeSetting.scope_type=="global",ClusterRuntimeSetting.scope_name==""))
        if not row: raise ValueError("operator.notifications_enabled runtime setting missing")
        row.setting_value="true" if enabled else "false"; row.updated_by=updated_by[:255]; row.updated_at=s.scalar(select(func.now()))
