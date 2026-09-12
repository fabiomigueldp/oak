"""JSON CLI. MCP dependencies are imported only for the mcp subcommand."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from .client import DEFAULT_SOCKET, MAX_MESSAGE_BYTES, OperatorError, SocketClient, SSHClient
from .sdk import Oak


def json_object(value: str) -> dict[str, Any]:
    if value == "-":
        value = sys.stdin.read(MAX_MESSAGE_BYTES + 1)
    elif value.startswith("@"):
        value = Path(value[1:]).read_text(encoding="utf-8-sig")
    if len(value.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ValueError("Input exceeds 8 MiB")
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError("Input JSON must be an object")
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="oak-operator", description="Operate Oak through its persistent kernel.")
    result.add_argument("--socket", default=os.environ.get("OAK_OPERATOR_SOCKET", DEFAULT_SOCKET))
    result.add_argument("--ssh", metavar="ALIAS", help="Use SSH and sudo with an existing identity")
    result.add_argument("--actor", default=os.environ.get("OAK_OPERATOR_ACTOR", "cli"), help="Audit label; does not grant authority")
    result.add_argument("--rpc-timeout", type=float, default=60)
    result.add_argument("--pretty", action="store_true")
    commands = result.add_subparsers(dest="action", required=True)
    commands.add_parser("discover", help="Capabilities, status and environment")
    call = commands.add_parser("call", help="Call any discovered method; JSON, @file or - for stdin")
    call.add_argument("method")
    call.add_argument("data", nargs="?", default="{}")
    run = commands.add_parser("run", help="Submit durable code or Minecraft commands")
    run.add_argument("source", nargs="?", default="-", help="Code, @UTF8-file or - for stdin")
    run.add_argument("--kind", choices=("shell", "python", "javascript", "command"), default="shell")
    run.add_argument("--label", default="CLI execution")
    run.add_argument("--cwd")
    run.add_argument("--timeout", type=float, help="Execution timeout in seconds; omitted means kernel default")
    run.add_argument("--no-timeout", action="store_true", help="Explicitly request no execution deadline")
    run.add_argument("--env", action="append", default=[], metavar="KEY=VALUE")
    run.add_argument("--resource", action="append", default=[])
    run.add_argument("--idempotency")
    run.add_argument("--wait", action="store_true")
    run.add_argument("--wait-timeout", type=float, help="Local wait only; expiry does not cancel the job")
    jobs = commands.add_parser("jobs", help="List jobs or retrieve one with an output byte offset")
    jobs.add_argument("id", nargs="?")
    jobs.add_argument("--offset", type=int, default=0)
    jobs.add_argument("--limit", type=int, default=50, help="List count (use call for output page size)")
    cancel = commands.add_parser("cancel")
    cancel.add_argument("id")
    wait = commands.add_parser("wait")
    wait.add_argument("id")
    wait.add_argument("--timeout", type=float)
    for name in ("routines", "notebooks", "events"):
        commands.add_parser(name, help=f"List {name}; use call for changes")
    mcp = commands.add_parser("mcp", help="Serve the official MCP protocol; requires requirements-operator.txt")
    mcp.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    mcp.add_argument("--host", default="127.0.0.1")
    mcp.add_argument("--port", type=int, default=8094)
    mcp.add_argument("--token-file", help="Private JSON mapping token hashes to operator identities")
    mcp.add_argument("--allowed-host", action="append", default=[], help="Additional exact HTTP Host (include port)")
    mcp.add_argument("--allowed-origin", action="append", default=[], help="Additional exact browser Origin")
    return result


def output(value: Any, *, pretty: bool = False) -> None:
    print(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2 if pretty else None), flush=True)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        client = (SSHClient(args.ssh, socket_path=args.socket, actor=args.actor, timeout=args.rpc_timeout)
                  if args.ssh else SocketClient(args.socket, actor=args.actor, timeout=args.rpc_timeout))
        oak = Oak(client)
        if args.action == "mcp":
            from .mcp import serve
            serve(client, transport=args.transport, host=args.host, port=args.port,
                  token_file=args.token_file, allowed_hosts=args.allowed_host,
                  allowed_origins=args.allowed_origin)
            return 0
        if args.action == "discover":
            result = oak.discover()
        elif args.action == "call":
            result = oak.call(args.method, json_object(args.data))
        elif args.action == "run":
            source = args.source
            if source == "-":
                source = sys.stdin.read(MAX_MESSAGE_BYTES + 1)
            elif source.startswith("@"):
                source = Path(source[1:]).read_text(encoding="utf-8-sig")
            environment = {}
            for item in args.env:
                if "=" not in item:
                    raise ValueError("--env requires KEY=VALUE")
                name, value = item.split("=", 1)
                environment[name] = value
            options = {"label": args.label, "environment": environment, "resources": args.resource}
            if args.cwd is not None:
                options["cwd"] = args.cwd
            if args.no_timeout and args.timeout is not None:
                raise ValueError("Choose either --timeout or --no-timeout")
            if args.no_timeout or args.timeout is not None:
                options["timeout_seconds"] = None if args.no_timeout else args.timeout
            if args.idempotency:
                options["idempotency"] = args.idempotency
            result = oak.execute(source, kind=args.kind, **options)
            if args.wait:
                result = oak.wait(result["id"], timeout=args.wait_timeout, on_output=lambda line: print(line, end="", file=sys.stderr, flush=True))
        elif args.action == "jobs":
            result = oak.job(args.id, offset=args.offset) if args.id else oak.call("jobs.list", limit=args.limit)
        elif args.action == "cancel":
            result = oak.cancel(args.id)
        elif args.action == "wait":
            result = oak.wait(args.id, timeout=args.timeout, on_output=lambda line: print(line, end="", file=sys.stderr, flush=True))
        else:
            result = oak.call(f"{args.action}.list")
        output({"ok": True, "result": result}, pretty=args.pretty)
        return 0
    except (OperatorError, OSError, ValueError, TimeoutError, ImportError) as exc:
        error = exc.error if isinstance(exc, OperatorError) else {"code": "client_error", "message": str(exc)}
        if args.action == "mcp":
            print(f"Oak MCP: {error.get('message', error)}", file=sys.stderr)
        else:
            output({"ok": False, "error": error}, pretty=args.pretty)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
