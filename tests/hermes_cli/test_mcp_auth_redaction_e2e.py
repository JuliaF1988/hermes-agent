"""Real Streamable HTTP discovery in fresh CLI processes using only fixture data."""

import json
import os
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml


def test_mcp_probe_restart_keeps_wire_auth_and_scrubs_display(tmp_path):
    token = secrets.token_urlsafe(36)
    accepted = []

    class LocalMcp(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.headers.get("Authorization") != "Bearer " + token:
                self._reply(401, b"")
                return
            method = request["method"]
            accepted.append(method)
            if method == "initialize":
                result = {
                    "protocolVersion": request["params"]["protocolVersion"],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "synthetic", "version": "1"},
                }
            elif method == "tools/list":
                result = {"tools": [{
                    "name": "read_synthetic",
                    "description": "Fixture display Authorization: Bearer " + token * 3,
                    "inputSchema": {"type": "object", "properties": {}},
                }]}
            else:
                self._reply(202, b"")
                return
            payload = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
            self._reply(200, payload)

        def do_GET(self):
            self._reply(405, b"")

        def log_message(self, *args):
            pass

        def _reply(self, code, body):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), LocalMcp) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = {"mcp_servers": {"synthetic": {
                "url": f"http://127.0.0.1:{server.server_port}/mcp",
                "connect_timeout": 5,
                "headers": {"Authorization": "Bearer ${SYNTHETIC_HERMES_AUTH}"},
            }}}
            (tmp_path / "config.yaml").write_text(yaml.safe_dump(config))
            env = {**os.environ, "HERMES_HOME": str(tmp_path), "SYNTHETIC_HERMES_AUTH": token}
            command = [
                sys.executable, "-c",
                "from argparse import Namespace; from hermes_cli.mcp_config import cmd_mcp_test; "
                "cmd_mcp_test(Namespace(name='synthetic'))",
            ]
            for _ in range(2):
                probe = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15, check=True)
                output = probe.stdout + probe.stderr
                assert "Tools discovered: 1" in probe.stdout
                assert "read_synthetic" in probe.stdout
                assert all(part not in output for part in (token, token[:6], token[:4], token[-4:]))
        finally:
            server.shutdown()
            thread.join(timeout=5)
    assert accepted.count("initialize") == accepted.count("tools/list") == 2
