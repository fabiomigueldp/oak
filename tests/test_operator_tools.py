"""Isolated CLI, SDK and MCP tests. No production game or SSH calls."""
from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oak_operator.__main__ import main
from oak_operator.client import Client, OperatorError, SSHClient, SocketClient, decode_response, request_payload
from oak_operator.mcp import BearerAuth, HTTP_ACTOR, build_server, load_tokens
from oak_operator.sdk import Oak


class FakeClient:
    def __init__(self):
        self.calls = []
        self.actor = "test"

    def call(self, method, data=None):
        self.calls.append((method, data or {}, self.actor))
        if method == "fail":
            raise OperatorError({"code": "test_failure", "message": "Fixture error"})
        if method == "discover":
            return {"name": "fixture", "methods": ["discover", "jobs.submit"], "demo": True}
        if method == "jobs.submit":
            return {"id": "job-fixture", "status": "queued", "spec": data}
        return {"method": method, "data": data or {}}


class ClientTests(unittest.TestCase):
    def test_protocol_preserves_unicode_and_plain_source(self):
        source = "print('ação'); $(this is data)\nsecond line"
        request = json.loads(request_payload("jobs.submit", {"source": source}, "ssh:fixture"))
        self.assertEqual(request["data"]["source"], source)
        self.assertEqual(request["actor"], "ssh:fixture")
        self.assertEqual(decode_response(b'{"ok":true,"result":{"id":"abc"}}'), {"id": "abc"})

    def test_invalid_envelope_and_errors(self):
        for response in (b'[]', b'{"ok":1,"result":null}', b'{"ok":true}', b'invalid'):
            with self.assertRaises(OperatorError):
                decode_response(response)
        with self.assertRaises(OperatorError) as raised:
            decode_response(b'{"ok":false,"error":{"code":"conflict","message":"Revision changed"}}')
        self.assertEqual(raised.exception.code, "conflict")
        for method in ("; rm", "-oProxyCommand", "jobs.submit\n", ""):
            with self.assertRaises(OperatorError):
                request_payload(method, {}, "test")

    def test_ssh_source_never_enters_remote_shell_command(self):
        source = "echo `secret`; $(arbitrary)\n'ç'"
        completed = subprocess.CompletedProcess([], 0, b'{"ok":true,"result":{"id":"j"}}', b'')
        with patch("oak_operator.client.subprocess.run", return_value=completed) as run:
            result = SSHClient("oracle", actor="test ' label").call("jobs.submit", {"source": source})
        arguments = run.call_args.args[0]
        self.assertNotIn(source, " ".join(arguments))
        self.assertEqual(json.loads(run.call_args.kwargs["input"])["source"], source)
        self.assertIn("BatchMode=yes", arguments)
        self.assertIn("sudo -n", arguments[-1])
        self.assertEqual(result["id"], "j")
        with self.assertRaises(ValueError):
            SSHClient("-oProxyCommand=bad")

    def test_portal_request_actor_does_not_mutate_shared_client(self):
        client = Client(socket_path="/fixture", actor="base")
        with patch.object(SocketClient, "call", return_value={}) as call:
            client.call("discover", actor="portal:alice")
        call.assert_called_once_with("discover", None)
        self.assertEqual(client.actor, "base")

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Unix sockets unavailable")
    def test_real_socket_line_framing(self):
        with tempfile.TemporaryDirectory(prefix="oak-rpc-") as folder:
            path = str(Path(folder) / "rpc.sock")
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(path)
            except OSError:
                listener.close()
                self.skipTest("Platform cannot bind AF_UNIX")
            listener.listen(1)
            seen = []
            def respond():
                connection, _ = listener.accept()
                with connection, connection.makefile("rb") as reader:
                    seen.append(json.loads(reader.readline()))
                    connection.sendall(b'{"ok":true,"result":{"fixture":true}}\n')
            thread = threading.Thread(target=respond, daemon=True)
            thread.start()
            try:
                self.assertEqual(SocketClient(path, actor="fixture").call("discover"), {"fixture": True})
                thread.join(2)
                self.assertEqual(seen[0]["actor"], "fixture")
            finally:
                listener.close()


class SDKTests(unittest.TestCase):
    def test_raw_command_and_future_methods_remain_available(self):
        client = FakeClient()
        oak = Oak(client)
        oak.command("schedule function oak:example 20t", idempotency="unique")
        self.assertEqual(client.calls[-1][1]["source"], "schedule function oak:example 20t")
        oak.call("future.extension", value=42)
        self.assertEqual(client.calls[-1][0], "future.extension")
        oak.publish("arena.ready", {"region": "test"})
        self.assertEqual(client.calls[-1][1], {"name": "arena.ready", "data": {"region": "test"}})

    def test_wait_drains_terminal_output_and_does_not_resubmit(self):
        oak = Oak(FakeClient())
        pages = [{"status": "running", "output": "one", "next_offset": 3, "output_size": 3},
                 {"status": "succeeded", "output": "two", "next_offset": 6, "output_size": 9},
                 {"status": "succeeded", "output": "end", "next_offset": 9, "output_size": 9}]
        lines = []
        with patch.object(oak, "job", side_effect=pages) as job, patch("oak_operator.sdk.time.sleep"):
            result = oak.wait("j", on_output=lines.append)
        self.assertEqual(lines, ["one", "two", "end"])
        self.assertEqual([call.kwargs["offset"] for call in job.call_args_list], [0, 3, 6])
        self.assertEqual(result["status"], "succeeded")

    def test_wait_timeout_never_cancels(self):
        oak = Oak(FakeClient())
        with patch.object(oak, "job", return_value={"status": "running"}), patch.object(oak, "cancel") as cancel:
            with self.assertRaises(TimeoutError):
                oak.wait("job", timeout=0)
        cancel.assert_not_called()

    def test_cli_reads_multiline_source_from_stdin_and_outputs_envelope(self):
        client = FakeClient()
        stdout = io.StringIO()
        with patch("oak_operator.__main__.SocketClient", return_value=client), patch("sys.stdin", io.StringIO("print('ação')\n")), redirect_stdout(stdout):
            code = main(["run", "--kind", "python", "--no-timeout", "--env", "COLOR=green=1"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["result"]["id"], "job-fixture")
        spec = client.calls[0][1]
        self.assertEqual(spec["source"], "print('ação')\n")
        self.assertIsNone(spec["timeout_seconds"])
        self.assertEqual(spec["environment"], {"COLOR": "green=1"})

    def test_cli_failure_is_machine_readable(self):
        stdout = io.StringIO()
        with patch("oak_operator.__main__.SocketClient", return_value=FakeClient()), redirect_stdout(stdout):
            code = main(["call", "fail"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], "test_failure")


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_wrong_expired_and_revoked_tokens_never_reach_mcp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tokens.json"
            token = "a-long-fixture-secret-that-is-never-a-real-credential"
            entry = {"name": "fixture-agent", "sha256": hashlib.sha256(token.encode()).hexdigest()}
            path.write_text(json.dumps({"tokens": [entry]}), encoding="utf-8")
            path.chmod(0o600)
            identities = []
            async def downstream(scope, receive, send):
                identities.append(HTTP_ACTOR.get())
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"ok"})
            app = BearerAuth(downstream, path)
            async def request(headers):
                messages = []
                async def send(message):
                    messages.append(message)
                await app({"type": "http", "headers": headers}, None, send)
                self.assertIsNone(HTTP_ACTOR.get())
                return messages[0]["status"]
            good = [(b"authorization", b"Bearer " + token.encode())]
            self.assertEqual(await request([]), 401)
            self.assertEqual(await request([(b"authorization", b"Bearer wrong")]), 401)
            self.assertEqual(await request(good + good), 401)
            self.assertEqual(await request(good), 200)
            self.assertEqual(identities, ["mcp:fixture-agent"])
            entry["expires_at"] = time.time() - 1
            path.write_text(json.dumps({"tokens": [entry]}), encoding="utf-8")
            self.assertEqual(await request(good), 401)
            path.write_text('{"tokens":[]}', encoding="utf-8")
            self.assertEqual(await request(good), 401)
            self.assertEqual(len(identities), 1)

    async def test_token_file_rejects_plaintext_or_empty_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tokens.json"
            path.write_text('{"tokens":[{"name":"test","token":"plaintext"}]}', encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaises(ValueError):
                load_tokens(path)


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Optional MCP SDK not installed")
class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_http_authentication_and_protocol(self):
        import uvicorn
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tokens.json"
            secret = "fixture-only-http-token"
            path.write_text(json.dumps({"tokens": [{"name": "http-fixture", "sha256": hashlib.sha256(secret.encode()).hexdigest()}]}), encoding="utf-8")
            path.chmod(0o600)
            fake = FakeClient()
            app = BearerAuth(build_server(fake).streamable_http_app(stateless_http=True, json_response=True), path)
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(10)
            listener.setblocking(False)
            url = f"http://127.0.0.1:{listener.getsockname()[1]}/mcp"
            server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
            task = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                for _ in range(100):
                    if server.started:
                        break
                    await asyncio.sleep(.02)
                self.assertTrue(server.started)
                def post(payload, authorized=True):
                    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
                    if authorized:
                        headers["Authorization"] = f"Bearer {secret}"
                    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
                    with urllib.request.urlopen(request, timeout=5) as response:
                        return json.load(response)
                payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "fixture", "version": "1"}}}
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    await asyncio.to_thread(post, payload, False)
                self.assertEqual(raised.exception.code, 401)
                self.assertEqual((await asyncio.to_thread(post, payload))["result"]["serverInfo"]["name"], "Oak")
                result = await asyncio.to_thread(post, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "oak_discover", "arguments": {}}})
                self.assertEqual(result["result"]["structuredContent"]["name"], "fixture")
                self.assertEqual(fake.calls[-1][2], "mcp:http-fixture")
            finally:
                server.should_exit = True
                await asyncio.wait_for(task, timeout=5)
                listener.close()

    async def test_official_sdk_discovery_execution_error_and_resources(self):
        from mcp import Client as MCPClient
        fake = FakeClient()
        async with MCPClient(build_server(fake)) as client:
            tools = await client.list_tools()
            catalog = {tool.name: tool for tool in tools.tools}
            self.assertEqual(len(catalog), 9)
            self.assertTrue(catalog["oak_discover"].annotations.read_only_hint)
            self.assertFalse(catalog["oak_execute"].annotations.read_only_hint)
            discovery = await client.call_tool("oak_discover", {})
            self.assertEqual(discovery.structured_content["name"], "fixture")
            submitted = await client.call_tool("oak_execute", {"kind": "command", "source": "function oak:test", "idempotency": "mcp-fixture"})
            self.assertEqual(submitted.structured_content["id"], "job-fixture")
            self.assertEqual(fake.calls[-1][1]["source"], "function oak:test")
            failure = await client.call_tool("oak_call", {"method": "fail"})
            self.assertTrue(failure.is_error)
            self.assertIn("test_failure", failure.content[0].text)
            resources = await client.list_resources()
            self.assertEqual({str(item.uri) for item in resources.resources}, {"oak://guide", "oak://runtime"})

    async def test_real_stdio_negotiation_and_tool_call(self):
        from mcp import Client as MCPClient, StdioServerParameters
        parameters = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).resolve()), "--mcp-fixture"])
        async with MCPClient(parameters) as client:
            result = await client.call_tool("oak_discover", {})
            self.assertEqual(result.structured_content["name"], "fixture")
            self.assertEqual(len((await client.list_tools()).tools), 9)


if __name__ == "__main__":
    if "--mcp-fixture" in sys.argv:
        build_server(FakeClient()).run(transport="stdio")
    else:
        unittest.main()
