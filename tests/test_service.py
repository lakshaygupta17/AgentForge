import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Event
from unittest import IsolatedAsyncioTestCase

from httpx import ASGITransport, AsyncClient

from agentforge.service.app import create_app
from agentforge.service.manager import RunManager


@asynccontextmanager
async def local_client(manager: RunManager | None = None) -> AsyncIterator[AsyncClient]:
    app = create_app(manager)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            yield client


class LocalServiceTests(IsolatedAsyncioTestCase):
    @staticmethod
    async def _wait_for_status(
        client: AsyncClient,
        run_id: str,
        expected: set[str],
        *,
        timeout_seconds: float = 3,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            response = await client.get(f"/runs/{run_id}")
            document: dict[str, object] = response.json()
            if document["status"] in expected:
                return document
            await asyncio.sleep(0.01)
        raise AssertionError(f"Run {run_id} did not reach {expected}")

    async def test_health_identifies_local_only_scope(self) -> None:
        manager = RunManager(lambda: {"succeeded": True})
        async with local_client(manager) as client:
            response = await client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "scope": "local-only"})

    async def test_dashboard_and_local_assets_are_self_contained(self) -> None:
        manager = RunManager(lambda: {"succeeded": True})
        async with local_client(manager) as client:
            dashboard = await client.get("/")
            script = await client.get("/assets/dashboard.js")
            styles = await client.get("/assets/dashboard.css")

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("AgentForge", dashboard.text)
        self.assertIn("AgentForge Studio", dashboard.text)
        self.assertIn("One task, six clear responsibilities", dashboard.text)
        self.assertIn("Repository intelligence", dashboard.text)
        self.assertIn("Start benchmark", dashboard.text)
        self.assertIn("REAL-TIME ORCHESTRATOR", dashboard.text)
        self.assertIn("agent-output-planner", dashboard.text)
        self.assertIn("The model never holds all the keys", dashboard.text)
        self.assertIn("/assets/dashboard.js", dashboard.text)
        self.assertNotIn("https://", dashboard.text)
        self.assertIn("EventSource", script.text)
        self.assertIn("agent_progress", script.text)
        self.assertIn("lastSequence", script.text)
        self.assertIn("Play walkthrough", dashboard.text)
        self.assertIn("submit_plan", script.text)
        self.assertIn("debugger", script.text)
        self.assertIn("text/javascript", script.headers["content-type"])
        self.assertIn("text/css", styles.headers["content-type"])
        self.assertEqual(dashboard.headers["x-content-type-options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", dashboard.headers["content-security-policy"])

    async def test_non_local_host_and_origin_are_rejected(self) -> None:
        manager = RunManager(lambda: {"succeeded": True})
        app = create_app(manager)
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://attacker.example"
            ) as remote_client:
                invalid_host = await remote_client.get("/health")
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as local:
                invalid_origin = await local.post(
                    "/runs", json={}, headers={"Origin": "https://attacker.example"}
                )

        self.assertEqual(invalid_host.status_code, 400)
        self.assertEqual(invalid_origin.status_code, 403)

    async def test_create_status_and_event_stream_complete_a_run(self) -> None:
        manager = RunManager(lambda: {"succeeded": True, "case_count": 3})
        async with local_client(manager) as client:
            created = await client.post("/runs", json={})
            run_id = created.json()["run_id"]
            completed = await self._wait_for_status(client, run_id, {"succeeded"})
            events = await client.get(f"/runs/{run_id}/events")

        self.assertEqual(created.status_code, 202)
        self.assertEqual(completed["result"], {"succeeded": True, "case_count": 3})
        self.assertIn("event: run_queued", events.text)
        self.assertIn("event: run_started", events.text)
        self.assertIn("event: run_succeeded", events.text)
        self.assertEqual(events.headers["content-type"], "text/event-stream; charset=utf-8")

    async def test_progress_runner_streams_agent_events(self) -> None:
        def progress_runner(publish):
            publish(
                "agent_progress",
                {
                    "case_name": "sample",
                    "event_type": "agent_started",
                    "actor": "planner",
                    "data": {},
                },
            )
            return {"succeeded": True}

        manager = RunManager(
            lambda: {"succeeded": True}, progress_runner=progress_runner
        )
        async with local_client(manager) as client:
            run_id = (await client.post("/runs", json={})).json()["run_id"]
            await self._wait_for_status(client, run_id, {"succeeded"})
            events = await client.get(f"/runs/{run_id}/events")

        self.assertIn("event: agent_progress", events.text)
        self.assertIn('"actor":"planner"', events.text)

    async def test_event_cursor_omits_earlier_events(self) -> None:
        manager = RunManager(lambda: {"succeeded": True})
        async with local_client(manager) as client:
            run_id = (await client.post("/runs", json={})).json()["run_id"]
            await self._wait_for_status(client, run_id, {"succeeded"})
            response = await client.get(f"/runs/{run_id}/events?after=2")

        self.assertNotIn("event: run_queued", response.text)
        self.assertNotIn("event: run_started", response.text)
        self.assertIn("event: run_succeeded", response.text)

    async def test_running_cancellation_is_truthful_and_eventually_terminal(
        self,
    ) -> None:
        started = Event()
        release = Event()

        def slow_runner() -> dict[str, object]:
            started.set()
            release.wait(3)
            return {"succeeded": True}

        manager = RunManager(slow_runner)
        async with local_client(manager) as client:
            run_id = (await client.post("/runs", json={})).json()["run_id"]
            self.assertTrue(started.wait(1))
            cancellation = await client.delete(f"/runs/{run_id}")
            self.assertEqual(cancellation.json()["status"], "running")
            self.assertTrue(cancellation.json()["cancel_requested"])
            release.set()
            final = await self._wait_for_status(client, run_id, {"cancelled"})

        self.assertEqual(final["status"], "cancelled")
        self.assertIsNone(final["result"])

    async def test_capacity_limit_rejects_excess_active_runs(self) -> None:
        started = Event()
        release = Event()

        def slow_runner() -> dict[str, object]:
            started.set()
            release.wait(3)
            return {"succeeded": True}

        manager = RunManager(slow_runner, maximum_retained_runs=1)
        async with local_client(manager) as client:
            first = await client.post("/runs", json={})
            self.assertTrue(started.wait(1))
            second = await client.post("/runs", json={})
            release.set()
            await self._wait_for_status(client, first.json()["run_id"], {"succeeded"})

        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["detail"], "Local run capacity is full")

    async def test_failure_reports_exception_type_without_leaking_message(self) -> None:
        def failing_runner() -> dict[str, object]:
            raise RuntimeError("secret diagnostic text")

        manager = RunManager(failing_runner)
        async with local_client(manager) as client:
            run_id = (await client.post("/runs", json={})).json()["run_id"]
            failed = await self._wait_for_status(client, run_id, {"failed"})

        self.assertEqual(failed["error"], "Run failed with RuntimeError")
        self.assertNotIn("secret", str(failed))

    async def test_unsuccessful_workflow_result_marks_service_run_failed(self) -> None:
        manager = RunManager(lambda: {"succeeded": False, "passed_cases": 2})
        async with local_client(manager) as client:
            run_id = (await client.post("/runs", json={})).json()["run_id"]
            failed = await self._wait_for_status(client, run_id, {"failed"})

        self.assertEqual(failed["result"], {"succeeded": False, "passed_cases": 2})
        self.assertEqual(failed["error"], "Run completed with an unsuccessful result")

    async def test_unknown_run_and_unknown_kind_are_rejected(self) -> None:
        manager = RunManager(lambda: {"succeeded": True})
        async with local_client(manager) as client:
            missing = await client.get("/runs/not-present")
            invalid = await client.post("/runs", json={"kind": "live-repair"})

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(invalid.status_code, 422)

    async def test_default_service_runs_real_offline_benchmark_suite(self) -> None:
        async with local_client() as client:
            run_id = (await client.post("/runs", json={})).json()["run_id"]
            completed = await self._wait_for_status(
                client, run_id, {"succeeded"}, timeout_seconds=10
            )
            events = await client.get(f"/runs/{run_id}/events")

        result = completed["result"]
        self.assertIsInstance(result, dict)
        self.assertEqual(result["case_count"], 3)
        self.assertEqual(result["passed_cases"], 3)
        self.assertEqual(result["total_repair_attempts"], 5)
        self.assertIn("event: agent_progress", events.text)
        self.assertIn('"event_type":"agent_started"', events.text)
