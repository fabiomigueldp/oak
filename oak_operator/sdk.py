"""Small composable Python SDK; use call() for newly discovered capabilities."""
from __future__ import annotations

import time
from typing import Any, Callable

from .client import OperatorError, SocketClient, SSHClient, Transport

TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "timed_out", "interrupted"})


class Oak:
    def __init__(self, client: Transport | None = None):
        self.client = client if client is not None else SocketClient(actor="python-sdk")

    @classmethod
    def ssh(cls, destination: str = "oracle", *, actor: str = "python-sdk", **options: Any) -> Oak:
        return cls(SSHClient(destination, actor=actor, **options))

    def call(self, method: str, data: dict[str, Any] | None = None, **values: Any) -> Any:
        return self.client.call(method, {**(data or {}), **values})

    def discover(self) -> dict[str, Any]:
        return self.call("discover")

    def execute(self, source: str, *, kind: str = "shell", **options: Any) -> dict[str, Any]:
        """Submit once and return its durable ID; disconnecting does not cancel it."""
        return self.call("jobs.submit", source=source, kind=kind, **options)

    def command(self, source: str, **options: Any) -> dict[str, Any]:
        return self.execute(source, kind="command", **options)

    def job(self, job_id: str, *, offset: int = 0, limit: int = 65536) -> dict[str, Any]:
        return self.call("jobs.get", id=job_id, offset=offset, limit=limit)

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self.call("jobs.cancel", id=job_id)

    def wait(self, job_id: str, *, timeout: float | None = None, interval: float = 1,
             on_output: Callable[[str], None] | None = None) -> dict[str, Any]:
        """Follow byte offsets until terminal. A local timeout leaves the job running."""
        if interval <= 0 or (timeout is not None and timeout < 0):
            raise ValueError("interval must be positive and timeout non-negative")
        deadline = None if timeout is None else time.monotonic() + timeout
        offset = 0
        while True:
            job = self.job(job_id, offset=offset)
            output = job.get("output", "")
            if output and on_output:
                on_output(output)
            next_offset = int(job.get("next_offset", offset))
            if next_offset < offset:
                raise OperatorError("Job output offset moved backwards")
            offset = next_offset
            if job.get("status") in TERMINAL_STATES:
                if offset >= int(job.get("output_size", offset)):
                    return job
                if not output:
                    raise OperatorError("Job output pagination made no progress")
                continue
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError(f"Job {job_id} is still running; it was not cancelled")
            time.sleep(interval if remaining is None else min(interval, remaining))

    def routine(self, name: str, trigger: dict[str, Any], job: dict[str, Any], **options: Any) -> dict[str, Any]:
        return self.call("routines.upsert", name=name, trigger=trigger, job=job, **options)

    def notebook(self, title: str, content: str, **options: Any) -> dict[str, Any]:
        return self.call("notebooks.save", title=title, content=content, **options)

    def publish(self, name: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.call("events.publish", {"name": name, "data": data or {}})
