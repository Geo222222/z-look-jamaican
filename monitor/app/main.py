from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .contract import MonitorContractError, invoke_snapshot, overview_view

ROOT = Path(os.getenv("ZLOOK_SOURCE_ROOT", "/zlook")).resolve()
WEB = Path(__file__).resolve().parents[1] / "web"
REFRESH_SECONDS = max(1, int(os.getenv("ZLOOK_MONITOR_REFRESH_SECONDS", "3")))

app = FastAPI(title="Z Look Jamaican Command Center", version="2.0.0")
app.mount("/assets", StaticFiles(directory=WEB), name="assets")


@app.middleware("http")
async def read_only_guard(request: Request, call_next):
    if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        return JSONResponse({"error": "read_only_monitor", "message": "Mutation methods are disabled."}, status_code=405)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-ZLook-Monitor-Mode"] = "read-only"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'"
    return response


def snapshot():
    return invoke_snapshot(ROOT)


def error_payload(exc: Exception):
    return {
        "status": "contract_error",
        "mode": "read-only",
        "source_root": str(ROOT),
        "error": str(exc),
    }


@app.exception_handler(MonitorContractError)
async def contract_error_handler(_request: Request, exc: MonitorContractError):
    return JSONResponse(error_payload(exc), status_code=503)


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/health")
def health():
    snap = snapshot()
    system = snap.section("system_health")
    return {
        "status": (system.get("availability") or {}).get("state"),
        "freshness": system.get("freshness"),
        "contract": snap.contract,
        "source_root": str(ROOT),
    }


@app.get("/api/overview")
def overview():
    return overview_view(snapshot())


@app.get("/api/snapshot")
def raw_snapshot():
    return snapshot().raw


@app.get("/api/section/{name}")
def section(name: str):
    snap = snapshot()
    return {"name": name, "section": snap.section(name), "contract": snap.contract}


@app.get("/api/experiments")
def experiments():
    snap = snapshot()
    return {
        "active": snap.section("active_experiment"),
        "history": snap.section("experiment_history"),
        "decisions": snap.section("decisions"),
    }


@app.get("/api/opportunities")
def opportunities():
    return snapshot().section("opportunities")


@app.get("/api/evidence")
def evidence():
    snap = snapshot()
    return {
        "events": snap.section("evidence_events"),
        "quality": snap.section("data_quality"),
        "reflections": snap.section("reflections"),
    }


@app.get("/api/wallets")
def wallets():
    return snapshot().section("wallets")


@app.get("/api/treasury")
def treasury():
    return snapshot().section("treasury")


@app.get("/api/governor")
def governor():
    snap = snapshot()
    return {
        "governor": snap.section("governor"),
        "exposure": snap.section("financial_exposure"),
        "economics": snap.section("economics"),
    }


@app.get("/api/deployments")
def deployments():
    snap = snapshot()
    return {
        "deployments": snap.section("deployments"),
        "model_provider_qualification": snap.section("model_provider_qualification"),
    }


@app.get("/api/logs")
def logs():
    snap = snapshot()
    return {
        "runtime_logs": snap.section("runtime_logs"),
        "incidents": snap.section("incidents"),
    }


@app.get("/api/provenance")
def provenance():
    snap = snapshot()
    return {
        "contract": snap.contract,
        "sections": {
            name: {
                "availability": section.get("availability"),
                "freshness": section.get("freshness"),
                "provenance": section.get("provenance"),
            }
            for name, section in snap.sections.items()
        },
    }


@app.get("/api/events")
async def events(request: Request):
    async def stream():
        last = None
        while True:
            if await request.is_disconnected():
                break
            try:
                payload: dict[str, Any] = dict(overview_view(snapshot()))
            except Exception as exc:
                payload = error_payload(exc)
            encoded = json.dumps(payload, sort_keys=True, default=str)
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            if digest != last:
                yield f"event: snapshot\ndata: {encoded}\n\n"
                last = digest
            await asyncio.sleep(REFRESH_SECONDS)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
