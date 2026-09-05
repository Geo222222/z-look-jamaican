from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .contract import (
    MonitorContractError,
    invoke_operator_command,
    overview_view,
    validate_snapshot,
)
from .read_model import (
    build_health,
    compose_operator_overview,
    monitor_snapshot_payload,
    operator_snapshot_payload,
    slice_assembly,
    slice_benjamin_handoff,
    slice_competence,
    slice_context,
    slice_experts,
    slice_intelligence,
    slice_jobs,
    slice_market,
    slice_outcomes,
    slice_overview,
    slice_questions,
    slice_research,
    slice_system,
    snapshot_digest,
)

ROOT = Path(os.getenv("ZLOOK_SOURCE_ROOT", "/zlook")).resolve()
WEB = Path(__file__).resolve().parents[1] / "web"
REFRESH_SECONDS = max(1, int(os.getenv("ZLOOK_MONITOR_REFRESH_SECONDS", "3")))

app = FastAPI(title="ZLJ Operator Console", version="3.1.0")
app.mount("/assets", StaticFiles(directory=WEB), name="assets")


@app.middleware("http")
async def operator_guard(request: Request, call_next):
    method = request.method.upper()
    if method not in {"GET", "HEAD", "OPTIONS"} and not (method == "POST" and request.url.path == "/api/control/execute"):
        return JSONResponse({"error": "unsupported_mutation_surface", "message": "Only the governed operator command endpoint may accept POST."}, status_code=405)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-ZLook-Operator-Mode"] = "enabled" if os.getenv("ZLOOK_OPERATOR_MUTATIONS_ENABLED", "").lower() in {"1", "true", "yes", "on"} else "read-only"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'"
    return response


def error_payload(exc: Exception):
    return {"status": "contract_error", "mode": "operator-console", "source_root": str(ROOT), "error": str(exc)}


@app.exception_handler(MonitorContractError)
async def contract_error_handler(_request: Request, exc: MonitorContractError):
    return JSONResponse(error_payload(exc), status_code=503)


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/health")
def health():
    payload = build_health(ROOT)
    return JSONResponse(payload, status_code=200)


def _snapshot():
    return operator_snapshot_payload(ROOT)


@app.get("/api/system")
def system():
    return slice_system(_snapshot(), build_health(ROOT))


@app.get("/api/overview")
def overview():
    try:
        monitor_overview = compose_operator_overview(ROOT)
    except MonitorContractError:
        monitor_overview = {}
    payload = slice_overview(_snapshot(), build_health(ROOT))
    payload["monitor_overview"] = monitor_overview
    return payload


@app.get("/api/market")
def market():
    return slice_market(_snapshot())


@app.get("/api/context")
def context():
    return slice_context(_snapshot())


@app.get("/api/questions")
def questions():
    return slice_questions(_snapshot())


@app.get("/api/experts")
def experts():
    return slice_experts(_snapshot())


@app.get("/api/outcomes")
def outcomes():
    return slice_outcomes(_snapshot())


@app.get("/api/competence")
def competence():
    return slice_competence(_snapshot())


@app.get("/api/assembly")
def assembly():
    return slice_assembly(_snapshot())


@app.get("/api/research")
def research():
    return slice_research(_snapshot())


@app.get("/api/jobs")
def jobs():
    return slice_jobs(_snapshot())


@app.get("/api/intelligence")
def intelligence():
    return slice_intelligence(_snapshot())


@app.get("/api/benjamin-handoff")
def benjamin_handoff():
    return slice_benjamin_handoff(_snapshot())


@app.get("/api/operator")
def operator_snapshot_endpoint():
    return _snapshot()


@app.get("/api/stages")
def stages():
    snap = _snapshot()
    return {"stages": snap.get("stages", []), "contract": snap.get("contract", {})}


@app.get("/api/certification")
def certification():
    snap = _snapshot()
    return {"certification": snap.get("certification", {}), "contract": snap.get("contract", {})}


@app.get("/api/control/catalog")
def control_catalog():
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from autonomous_kernel.operator import operator_catalog
    return operator_catalog()


@app.post("/api/control/execute")
async def control_execute(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "error": "request body must be JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"status": "error", "error": "request body must be an object"}, status_code=400)
    try:
        return invoke_operator_command(ROOT, payload)
    except MonitorContractError as exc:
        return JSONResponse(error_payload(exc), status_code=409)


def _monitor():
    return validate_snapshot(monitor_snapshot_payload(ROOT))


@app.get("/api/legacy-overview")
def legacy_overview():
    return overview_view(_monitor())


@app.get("/api/section/{name}")
def section(name: str):
    snap = _monitor()
    return {"name": name, "section": snap.section(name), "contract": snap.contract}


@app.get("/api/experiments")
def experiments():
    snap = _monitor()
    return {"active": snap.section("active_experiment"), "history": snap.section("experiment_history"), "decisions": snap.section("decisions")}


@app.get("/api/opportunities")
def opportunities():
    return _monitor().section("opportunities")


@app.get("/api/evidence")
def evidence():
    snap = _monitor()
    return {"events": snap.section("evidence_events"), "quality": snap.section("data_quality"), "reflections": snap.section("reflections")}


@app.get("/api/wallets")
def wallets():
    return _monitor().section("wallets")


@app.get("/api/treasury")
def treasury():
    return _monitor().section("treasury")


@app.get("/api/governor")
def governor():
    snap = _monitor()
    return {"governor": snap.section("governor"), "exposure": snap.section("financial_exposure"), "economics": snap.section("economics")}


@app.get("/api/deployments")
def deployments():
    snap = _monitor()
    return {"deployments": snap.section("deployments"), "model_provider_qualification": snap.section("model_provider_qualification")}


@app.get("/api/logs")
def logs():
    snap = _monitor()
    return {"runtime_logs": snap.section("runtime_logs"), "incidents": snap.section("incidents")}


@app.get("/api/provenance")
def provenance():
    snap = _monitor()
    return {"contract": snap.contract, "sections": {name: {"availability": section.get("availability"), "freshness": section.get("freshness"), "provenance": section.get("provenance")} for name, section in snap.sections.items()}}


@app.get("/api/snapshot")
def raw_snapshot():
    return _snapshot()


@app.get("/api/events")
async def events(request: Request):
    async def stream():
        last = None
        while True:
            if await request.is_disconnected():
                break
            try:
                payload: dict[str, Any] = dict(await asyncio.to_thread(_snapshot))
                payload["backend_status"] = "BACKEND_ONLINE"
                payload.pop("monitor", None)
            except Exception as exc:
                payload = error_payload(exc)
                payload["backend_status"] = "BACKEND_DEGRADED"
            digest = snapshot_digest(payload)
            if digest != last:
                encoded = json.dumps(payload, sort_keys=True, default=str)
                yield "event: snapshot\ndata: %s\n\n" % encoded
                last = digest
            else:
                yield "event: heartbeat\ndata: %s\n\n" % json.dumps({"digest": digest, "backend_status": payload.get("backend_status")})
            await asyncio.sleep(REFRESH_SECONDS)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"})
