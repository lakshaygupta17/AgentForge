"""Local-only HTTP interface for observable AgentForge benchmark runs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict

from agentforge.evaluation.repair import run_repair_benchmark_suite
from agentforge.service.dashboard import DASHBOARD_CSS, DASHBOARD_HTML, DASHBOARD_JS
from agentforge.service.manager import (
    RunCapacityError,
    RunEventPublisher,
    RunManager,
    RunNotFoundError,
    ServiceRunSnapshot,
)
from agentforge.telemetry.trajectory import TrajectoryEvent


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["benchmark-suite"] = "benchmark-suite"


class AcceptedRun(BaseModel):
    run_id: str
    status: str
    status_url: str
    events_url: str


def _default_runner() -> dict[str, Any]:
    return asdict(run_repair_benchmark_suite())


def _default_progress_runner(publish: RunEventPublisher) -> dict[str, Any]:
    def observe(case_name: str, event: TrajectoryEvent) -> None:
        publish(
            "agent_progress",
            {
                "case_name": case_name,
                "event_type": event.event_type,
                "actor": event.actor,
                "data": event.data,
                "occurred_at": event.occurred_at,
            },
        )

    return asdict(run_repair_benchmark_suite(observe))


def create_app(manager: RunManager | None = None) -> FastAPI:
    run_manager = manager or RunManager(
        _default_runner, progress_runner=_default_progress_runner
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.run_manager = run_manager
        yield
        run_manager.close()

    app = FastAPI(
        title="AgentForge Local Service",
        version="0.1.0",
        description="Local-only API for bounded, observable AgentForge runs.",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )

    @app.middleware("http")
    async def enforce_local_boundary(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        allowed_hosts = {"127.0.0.1", "localhost", "::1"}
        if request.url.hostname not in allowed_hosts:
            return Response("Invalid host", status_code=status.HTTP_400_BAD_REQUEST)
        origin = request.headers.get("origin")
        if origin and urlparse(origin).hostname not in allowed_hosts:
            return Response("Invalid origin", status_code=status.HTTP_403_FORBIDDEN)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return DASHBOARD_HTML

    @app.get("/assets/dashboard.css")
    def dashboard_css() -> Response:
        return Response(DASHBOARD_CSS, media_type="text/css")

    @app.get("/assets/dashboard.js")
    def dashboard_js() -> Response:
        return Response(DASHBOARD_JS, media_type="text/javascript")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "scope": "local-only"}

    @app.post("/runs", response_model=AcceptedRun, status_code=status.HTTP_202_ACCEPTED)
    def create_run(request: CreateRunRequest) -> AcceptedRun:
        try:
            run = run_manager.submit(request.kind)
        except RunCapacityError:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "Local run capacity is full"
            ) from None
        return AcceptedRun(
            run_id=run.run_id,
            status=run.status.value,
            status_url=f"/runs/{run.run_id}",
            events_url=f"/runs/{run.run_id}/events",
        )

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        return _snapshot_document(_get_or_404(run_manager, run_id))

    @app.delete("/runs/{run_id}")
    def cancel_run(run_id: str) -> dict[str, Any]:
        try:
            return _snapshot_document(run_manager.cancel(run_id))
        except RunNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found") from None

    @app.get("/runs/{run_id}/events")
    async def stream_events(
        run_id: str,
        request: Request,
        after: Annotated[int, Query(ge=0)] = 0,
    ) -> StreamingResponse:
        _get_or_404(run_manager, run_id)

        async def event_stream() -> AsyncIterator[str]:
            cursor = after
            while True:
                if await request.is_disconnected():
                    return
                events = run_manager.events_after(run_id, cursor)
                for event in events:
                    cursor = event.sequence
                    yield (
                        f"id: {event.sequence}\n"
                        f"event: {event.event_type}\n"
                        f"data: {json.dumps(asdict(event), separators=(',', ':'))}\n\n"
                    )
                if run_manager.get(run_id).status.terminal:
                    return
                await asyncio.sleep(0.05)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _get_or_404(manager: RunManager, run_id: str) -> ServiceRunSnapshot:
    try:
        return manager.get(run_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found") from None


def _snapshot_document(snapshot: ServiceRunSnapshot) -> dict[str, Any]:
    document = asdict(snapshot)
    document["status"] = snapshot.status.value
    return document
