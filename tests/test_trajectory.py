import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from agentforge.telemetry.trajectory import (
    InMemoryTrajectoryRecorder,
    JsonlTrajectoryRecorder,
    TrajectoryEvent,
    contains_potential_secret,
    redact_sensitive_text,
)


class JsonlTrajectoryRecorderTests(TestCase):
    def test_shared_text_redaction_detects_credentials_for_external_boundaries(self) -> None:
        text = "Request failed with HF_TOKEN=hf_private and Bearer abc123"

        self.assertTrue(contains_potential_secret(text))
        redacted = redact_sensitive_text(text)
        self.assertNotIn("hf_private", redacted)
        self.assertNotIn("abc123", redacted)
        self.assertFalse(contains_potential_secret("Fix the parser edge case"))

    def test_appends_one_json_object_per_event(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "nested" / "run.jsonl"
            recorder = JsonlTrajectoryRecorder(path)
            recorder.record(TrajectoryEvent("run-1", "started", "planner"))
            recorder.record(
                TrajectoryEvent("run-1", "completed", "planner", {"steps": 2})
            )

            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_type"], "started")
        self.assertEqual(events[1]["data"], {"steps": 2})

    def test_redacts_nested_and_inline_credentials_before_storage(self) -> None:
        event = TrajectoryEvent(
            "run-1",
            "failed",
            "tester",
            {
                "authorization": "Bearer top-secret",
                "message": "HF_TOKEN=hf_private Bearer bearer-private",
                "nested": {"password": "hunter2", "total_tokens": 42},
            },
        )
        memory = InMemoryTrajectoryRecorder()
        memory.record(event)

        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "run.jsonl"
            JsonlTrajectoryRecorder(path).record(event)
            serialized = path.read_text(encoding="utf-8")

        stored = memory.events[0]
        self.assertEqual(stored.data["authorization"], "[REDACTED]")
        self.assertEqual(stored.data["nested"]["password"], "[REDACTED]")
        self.assertEqual(stored.data["nested"]["total_tokens"], 42)
        self.assertNotIn("hf_private", serialized)
        self.assertNotIn("bearer-private", serialized)
        self.assertNotIn("hunter2", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_in_memory_observer_receives_only_the_sanitized_event(self) -> None:
        observed: list[TrajectoryEvent] = []
        recorder = InMemoryTrajectoryRecorder(on_record=observed.append)

        recorder.record(
            TrajectoryEvent(
                "run-1",
                "agent_completed",
                "planner",
                {"summary": "HF_TOKEN=hf_private", "token": "top-secret"},
            )
        )

        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0], recorder.events[0])
        self.assertNotIn("hf_private", str(observed[0].data))
        self.assertEqual(observed[0].data["token"], "[REDACTED]")
