from unittest import TestCase

from agentforge.sandbox.startup import measure_sandbox_startup


class FakeSandbox:
    def __init__(self) -> None:
        self.started = False
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SandboxStartupTests(TestCase):
    def test_measures_each_cold_start_and_always_closes(self) -> None:
        sandboxes: list[FakeSandbox] = []
        times = iter((0.0, 0.2, 1.0, 1.4, 2.0, 2.6))

        def factory() -> FakeSandbox:
            sandbox = FakeSandbox()
            sandboxes.append(sandbox)
            return sandbox

        def start(sandbox: FakeSandbox) -> None:
            sandbox.started = True

        result = measure_sandbox_startup(
            factory, start, iterations=3, clock=lambda: next(times)
        )

        for actual, expected in zip(
            result.durations_seconds, (0.2, 0.4, 0.6), strict=True
        ):
            self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(result.average_seconds, 0.4)
        self.assertAlmostEqual(result.maximum_seconds, 0.6)
        self.assertTrue(all(sandbox.started and sandbox.closed for sandbox in sandboxes))

    def test_closes_sandbox_when_start_fails(self) -> None:
        sandbox = FakeSandbox()

        def fail(candidate: FakeSandbox) -> None:
            raise RuntimeError("start failed")

        with self.assertRaisesRegex(RuntimeError, "start failed"):
            measure_sandbox_startup(lambda: sandbox, fail, iterations=1)

        self.assertTrue(sandbox.closed)

    def test_rejects_unbounded_iteration_counts(self) -> None:
        for iterations in (0, 11):
            with self.subTest(iterations=iterations), self.assertRaises(ValueError):
                measure_sandbox_startup(
                    FakeSandbox, lambda sandbox: None, iterations=iterations
                )
