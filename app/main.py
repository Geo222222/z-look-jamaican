from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .kernel import AutonomousKernel, init_db, settings

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
log = logging.getLogger("zlook")
kernel = AutonomousKernel()


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


app = FastAPI(title="Z Look Jamaican Autonomous Kernel", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "root-agent-kernel"}


@app.get("/v1/state")
def state() -> dict:
    return kernel.snapshot()


@app.post("/v1/cycle")
def run_cycle() -> dict:
    result = kernel.run_cycle()
    return result.__dict__
