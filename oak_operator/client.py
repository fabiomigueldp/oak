"""Authenticated Unix-socket and SSH transports for the Oak operator daemon."""
from __future__ import annotations

import json
import os
import re
import shlex
import socket
import subprocess
from typing import Any, Protocol

DEFAULT_SOCKET = "/run/oak-operator/operator.sock"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
METHOD = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\Z")


class OperatorError(RuntimeError):
    """A daemon or transport failure, including its machine-readable details."""

    def __init__(self, error: str | dict[str, Any]):
        self.error = error if isinstance(error, dict) else {"message": str(error)}
        self.code = str(self.error.get("code", "operator_error"))
        super().__init__(str(self.error.get("message", self.error)))


class Transport(Protocol):
    def call(self, method: str, data: dict[str, Any] | None = None) -> Any: ...


def request_payload(method: str, data: dict[str, Any] | None, actor: str) -> bytes:
    if not isinstance(method, str) or not METHOD.fullmatch(method):
        raise OperatorError({"code": "invalid_method", "message": "Invalid method name"})
    if data is not None and not isinstance(data, dict):
        raise OperatorError({"code": "invalid_data", "message": "Request data must be an object"})
    payload = json.dumps({"method": method, "data": data or {}, "actor": actor},
                         ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
    if len(payload) > MAX_REQUEST_BYTES:
        raise OperatorError({"code": "request_too_large", "message": "Request exceeds 2 MiB"})
    return payload


def decode_response(raw: bytes | str) -> Any:
    if len(raw) > MAX_MESSAGE_BYTES:
        raise OperatorError({"code": "response_too_large", "message": "Response exceeds 8 MiB"})
    try:
        response = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise OperatorError({"code": "invalid_response", "message": "Invalid daemon JSON response"}) from exc
    if not isinstance(response, dict) or type(response.get("ok")) is not bool:
        raise OperatorError({"code": "invalid_response", "message": "Invalid daemon response envelope"})
    if not response["ok"]:
        error = response.get("error", {"message": "Daemon operation failed"})
        if isinstance(error, str):
            codes = {"ValueError": "invalid_input", "KeyError": "not_found", "FileNotFoundError": "not_found", "PermissionError": "permission_denied"}
            code = "conflict" if "conflict" in error.lower() else codes.get(response.get("type"), "operator_error")
            error = {"message": error, "code": code, "type": response.get("type")}
        raise OperatorError(error if isinstance(error, (str, dict)) else str(error))
    if "result" not in response:
        raise OperatorError({"code": "invalid_response", "message": "Daemon response has no result"})
    return response["result"]


class SocketClient:
    """The kernel authorizes SO_PEERCRED; actor is an audit label, never a role."""

    def __init__(self, path: str | None = None, *, actor: str = "cli", timeout: float = 60):
        self.path = path or os.environ.get("OAK_OPERATOR_SOCKET", DEFAULT_SOCKET)
        self.actor, self.timeout = actor, timeout

    def call(self, method: str, data: dict[str, Any] | None = None) -> Any:
        payload = request_payload(method, data, self.actor)
        if not hasattr(socket, "AF_UNIX"):
            raise OperatorError("Unix sockets unavailable; use SSHClient or --ssh oracle")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout)
                connection.connect(self.path)
                connection.sendall(payload)
                with connection.makefile("rb") as reader:
                    raw = reader.readline(MAX_MESSAGE_BYTES + 1)
            if not raw.endswith(b"\n"):
                raise OperatorError({"code": "incomplete_response", "message": "Incomplete daemon response"})
            return decode_response(raw)
        except (OSError, TimeoutError) as exc:
            raise OperatorError({"code": "transport_error", "message": f"Operator socket unavailable: {exc}"}) from exc


class SSHClient:
    """Transmit JSON on stdin, using the existing SSH identity and sudo policy."""

    def __init__(self, destination: str = "oracle", *, actor: str = "ssh-agent",
                 socket_path: str = DEFAULT_SOCKET, timeout: float = 60,
                 executable: str = "/usr/local/bin/oak-operator"):
        if not destination or destination.startswith("-") or any(c.isspace() for c in destination):
            raise ValueError("SSH destination must be a hostname or SSH alias")
        self.destination, self.actor = destination, actor
        self.socket_path, self.timeout, self.executable = socket_path, timeout, executable

    def call(self, method: str, data: dict[str, Any] | None = None) -> Any:
        request_payload(method, data, self.actor)
        command = ["sudo", "-n", self.executable, "--socket", self.socket_path,
                   "--actor", self.actor, "call", method, "-"]
        arguments = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                     "--", self.destination, shlex.join(command)]
        try:
            completed = subprocess.run(arguments, input=json.dumps(data or {}, ensure_ascii=False,
                                       allow_nan=False).encode("utf-8"), capture_output=True,
                                       timeout=self.timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OperatorError({"code": "transport_error", "message": f"SSH operator call failed: {exc}"}) from exc
        if completed.stdout.strip():
            return decode_response(completed.stdout)
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[-2000:]
        raise OperatorError({"code": "ssh_error", "message": detail or f"SSH exited {completed.returncode}"})


class Client(SocketClient):
    """Portal compatibility facade with a request-scoped audit label."""

    def __init__(self, socket_path: str = DEFAULT_SOCKET, *, actor: str = "cli", timeout: float = 60):
        super().__init__(socket_path, actor=actor, timeout=timeout)

    def call(self, method: str, data: dict[str, Any] | None = None, *, actor: str | None = None) -> Any:
        if actor is None:
            return super().call(method, data)
        return SocketClient(self.path, actor=actor, timeout=self.timeout).call(method, data)
