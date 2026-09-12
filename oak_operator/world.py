"""Client for the optional native Fabric bridge; never falls back to game commands."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import stat
from typing import Any, Iterator
import uuid

from .client import MAX_MESSAGE_BYTES, MAX_REQUEST_BYTES, METHOD, OperatorError, decode_response

DEFAULT_SOCKET = "/run/oak-telemetry/operator.sock"


class World:
    def __init__(self, socket_path: str | None = None, *, timeout: float = 35):
        self.socket_path = socket_path or os.environ.get("OAK_OPERATOR_WORLD_SOCKET", DEFAULT_SOCKET)
        self.timeout = timeout

    def available(self) -> bool:
        try:
            return hasattr(socket, "AF_UNIX") and stat.S_ISSOCK(Path(self.socket_path).stat().st_mode)
        except OSError:
            return False

    def call(self, method: str, data: dict[str, Any] | None = None, *, idempotency: str | None = None) -> dict[str, Any]:
        if not isinstance(method, str) or not METHOD.fullmatch(method):
            raise ValueError("Invalid native method name")
        if data is not None and not isinstance(data, dict):
            raise ValueError("Native data must be an object")
        request = {"method": method, "data": data or {}}
        if idempotency is not None:
            request["idempotency"] = idempotency
        payload = (json.dumps(request, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(payload) > MAX_REQUEST_BYTES:
            raise ValueError("Native request exceeds 2 MiB")
        if not hasattr(socket, "AF_UNIX"):
            raise OperatorError({"code": "native_unavailable", "message": "Native bridge requires Oracle Unix access"})
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout)
                connection.connect(self.socket_path)
                connection.sendall(payload)
                with connection.makefile("rb") as reader:
                    response = reader.readline(MAX_MESSAGE_BYTES + 1)
            if not response.endswith(b"\n"):
                raise OperatorError({"code": "incomplete_response", "message": "Native result incomplete; inspect receipt before retrying effects"})
            return decode_response(response)
        except (OSError, TimeoutError) as exc:
            raise OperatorError({"code": "native_unavailable", "message": f"Native bridge unavailable or result uncertain: {exc}"}) from exc

    def inspect(self, minimum: list[int], maximum: list[int], *, dimension: str = "minecraft:overworld",
                limit: int = 256) -> Iterator[dict[str, Any]]:
        """Yield bounded pages; each page reflects its own observation tick."""
        cursor, epoch = 0, None
        while True:
            result = self.call("region.inspect", {"dimension": dimension, "min": minimum, "max": maximum,
                                                 "cursor": cursor, "limit": limit})
            if epoch is not None and result.get("epoch") != epoch:
                raise OperatorError("Minecraft restarted during inspection; restart the query")
            epoch = result.get("epoch")
            yield result
            if not result.get("has_more"):
                return
            next_cursor = result.get("next_cursor", cursor)
            if next_cursor <= cursor:
                raise OperatorError("Native inspection made no cursor progress")
            cursor = next_cursor

    def apply(self, blocks: list[dict[str, Any]], *, dimension: str = "minecraft:overworld", flags: int = 3,
              idempotency: str | None = None) -> Iterator[dict[str, Any]]:
        """Yield partial mutation receipts. Per-item errors remain visible to callers."""
        key = idempotency or str(uuid.uuid4())
        cursor, epoch = 0, None
        while True:
            data = {"dimension": dimension, "blocks": blocks, "cursor": cursor, "flags": flags}
            if epoch is not None:
                data["expected_epoch"] = epoch
            result = self.call("blocks.apply", data, idempotency=f"{key}:{cursor}")
            if epoch is not None and result.get("epoch") != epoch:
                raise OperatorError("Minecraft restarted during mutation; inspect existing effects before continuing")
            epoch = result.get("epoch")
            yield result
            if not result.get("has_more"):
                return
            next_cursor = result.get("next_cursor", cursor)
            if next_cursor <= cursor:
                raise OperatorError("Native mutation made no cursor progress")
            cursor = next_cursor
