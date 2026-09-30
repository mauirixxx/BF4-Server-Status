from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .database_facts import get_database_facts
from .db import close_pool, open_pool
from .repository import (
    get_cluster_health,
    get_database_nodes,
    get_dns_nodes,
    get_leadership,
    get_platforms,
    get_population_history,
    get_snapshot_health,
    get_summary,
    get_workers,
)

BASE_DIR = Path(__file__).resolve().parent
APP_VERSION = (BASE_DIR.parent / "VERSION").read_text().strip()
settings = get_settings()
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["comma"] = lambda value: f"{int(value):,}" if value is not None else "—"


@asynccontextmanager
async def lifespan(app: FastAPI):
    open_pool()
    try:
        yield
    finally:
        close_pool()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/health")
def health():
    return {"status": "ok", "service": "bf4-status-web-dashboard"}


@app.api_route("/", methods=["GET", "HEAD"])
def dashboard(request: Request):
    summary = get_summary()
    platforms = get_platforms()
    snapshot_health = get_snapshot_health()
    workers = get_workers()
    leadership = get_leadership()
    cluster_health = get_cluster_health(workers, leadership)
    database_nodes = get_database_nodes() if settings.public_infrastructure_panel else []
    dns_nodes = get_dns_nodes() if settings.public_infrastructure_panel else []
    database_facts = get_database_facts() if settings.public_infrastructure_panel else {"available": False}
    current_total = sum(int(p.get("players") or 0) for p in platforms)
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "summary": summary, "platforms": platforms, "snapshot_health": snapshot_health,
            "workers": workers if settings.public_operator_panel else [],
            "leadership": leadership if settings.public_operator_panel else [],
            "cluster_health": cluster_health, "current_total": current_total,
            "operator_enabled": settings.public_operator_panel,
            "infrastructure_enabled": settings.public_infrastructure_panel,
            "database_nodes": database_nodes, "dns_nodes": dns_nodes, "database_facts": database_facts,
            "fresh_minutes": settings.snapshot_fresh_seconds // 60,
            "adaptive_seconds": settings.snapshot_adaptive_seconds,
            "adaptive_minutes": round(settings.snapshot_adaptive_seconds / 60),
            "presence_healthy_coverage_pct": settings.presence_healthy_coverage_pct,
            "worker_healthy_seconds": settings.worker_healthy_seconds,
            "worker_warning_seconds": settings.worker_warning_seconds,
            "asset_version": APP_VERSION,
        },
    )


@app.get("/api/summary")
def api_summary():
    summary = get_summary()
    return {key: {"value": metric.value, "available": metric.available, "note": metric.note} for key, metric in summary.items()}


@app.get("/api/platforms")
def api_platforms(): return get_platforms()


@app.get("/api/cluster-health")
def api_cluster_health():
    workers = get_workers(); leadership = get_leadership()
    return get_cluster_health(workers, leadership)


@app.get("/api/workers")
def api_workers():
    if not settings.public_operator_panel:
        return JSONResponse(status_code=404, content={"detail": "Operator panel is not exposed by this deployment."})
    return get_workers()


@app.get("/api/leadership")
def api_leadership():
    if not settings.public_operator_panel:
        return JSONResponse(status_code=404, content={"detail": "Operator panel is not exposed by this deployment."})
    return get_leadership()


@app.get("/api/snapshot-health")
def api_snapshot_health(): return get_snapshot_health()


@app.get("/api/population-history")
def api_population_history(range: str = "24h"): return get_population_history(range)


@app.get("/api/population")
def api_population(range: str = "24h"):
    return get_population_history(range)
