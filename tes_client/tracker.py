from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .client import TesClient
from .models import TesState, TesTask


@dataclass
class TaskTracker:
    """Polls a submitted TES task and reports state transitions.

    Usage::

        tracker = TaskTracker(client, task_id)
        final = tracker.wait()           # blocks until terminal state
        print(final.state)

    Or with a callback that fires on every state change::

        def on_change(task: TesTask) -> None:
            print(f"  → {task.state}")

        tracker = TaskTracker(client, task_id, on_state_change=on_change)
        final = tracker.wait()
    """

    client: TesClient
    task_id: str
    poll_interval: float = 10.0
    on_state_change: Callable[[TesTask], None] | None = None

    _last_state: TesState = field(default=TesState.UNKNOWN, init=False, repr=False)

    def wait(self, *, timeout: float | None = None) -> TesTask:
        """Block until the task reaches a terminal state and return the final task.

        Raises ``TimeoutError`` if ``timeout`` seconds elapse first.
        """
        deadline = time.monotonic() + timeout if timeout is not None else None

        while True:
            task = self.client.get(self.task_id, full=True)
            current = task.state or TesState.UNKNOWN

            if current != self._last_state:
                self._last_state = current
                self._log_state(task)
                if self.on_state_change:
                    self.on_state_change(task)

            if current.is_terminal():
                return task

            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Task {self.task_id} did not reach a terminal state within {timeout}s "
                    f"(last state: {current})"
                )

            time.sleep(self.poll_interval)

    def _log_state(self, task: TesTask) -> None:
        ts = task.creation_time or ""
        print(f"[TES {self.task_id[:8]}] {task.state}  {ts}".rstrip())

    def report(self) -> None:
        """Print a one-shot status report without blocking."""
        task = self.client.get(self.task_id, full=True)
        self._print_report(task)

    @staticmethod
    def _print_report(task: TesTask) -> None:
        print(f"Task ID   : {task.id}")
        print(f"State     : {task.state}")
        print(f"Name      : {task.name or '—'}")
        if task.creation_time:
            print(f"Created   : {task.creation_time}")

        if task.logs:
            for i, tlog in enumerate(task.logs):
                print(f"\nAttempt {i + 1}:")
                if tlog.start_time:
                    print(f"  Start : {tlog.start_time}")
                if tlog.end_time:
                    print(f"  End   : {tlog.end_time}")
                for j, elog in enumerate(tlog.logs):
                    print(f"  Executor {j + 1} exit code: {elog.exit_code}")
                    if elog.stderr:
                        print(f"  Stderr:\n{_indent(elog.stderr)}")
                if tlog.system_logs:
                    print("  System logs:")
                    for line in tlog.system_logs:
                        print(f"    {line}")


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(f"{prefix}{line}" for line in text.splitlines())
