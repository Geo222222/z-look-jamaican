from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .kernel import AutonomousKernel, init_db, settings

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
log = logging.getLogger("zlook")
kernel = AutonomousKernel()


class ClaimRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=120)
    lease_minutes: int = Field(default=30, ge=1, le=240)


class CompleteRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=120)
    result: dict[str, Any]


async def autonomy_loop() -> None:
    while True:
        try:
            result = kernel.run_cycle()
            log.info("cycle=%s state=%s action=%s", result.cycle, result.state, result.action)
        except Exception:
            log.exception("autonomy cycle failed")
        await asyncio.sleep(max(1, settings.poll_interval_seconds))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    kernel.bootstrap()
    task = asyncio.create_task(autonomy_loop(), name="autonomy-loop")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Z Look Jamaican Autonomous Kernel", version="0.2.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "root-agent-kernel"}


@app.get("/v1/state")
def state() -> dict:
    return kernel.snapshot()


@app.post("/v1/cycle")
def run_cycle() -> dict:
    return kernel.run_cycle().__dict__


@app.get("/v1/tasks")
def tasks() -> list[dict]:
    return kernel.list_tasks()


@app.post("/v1/tasks/claim")
def claim(req: ClaimRequest) -> dict:
    return {"task": kernel.claim_task(req.worker_id, req.lease_minutes)}


@app.post("/v1/tasks/{task_id}/complete")
def complete(task_id: str, req: CompleteRequest) -> dict:
    try:
        return kernel.complete_task(task_id, req.worker_id, req.result)
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found")
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
