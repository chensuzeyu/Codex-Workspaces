"""Optional real-engine test using only local fake nodes, never real subscriptions."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import unittest

import yaml

from codex_workspaces import network, tmux
from codex_workspaces.proxy.provider import write_profile, source_config, save_bytes, dump
from helpers import PrivateFixture


def unused_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(os.environ.get("CWS_TEST_MIHOMO"), "Set CWS_TEST_MIHOMO to run an isolated real-engine test")
class RealMihomoIntegrationTests(PrivateFixture):
    def setUp(self):
        super().setUp()
        self.servers = []
        self.health = {"fast": True, "backup": True}
        self.subscription = {"content": b""}
        def server(kind):
            health = self.health
            subscription = self.subscription
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_): pass
                def do_GET(self):
                    if kind == "subscription":
                        data = subscription["content"]
                        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
                        self.wfile.write(data); return
                    time.sleep(0.005 if kind == "fast" else 0.06)
                    self.send_response(405 if health[kind] else 503)
                    self.send_header("Content-Length", "0"); self.end_headers()
                do_HEAD = do_GET
                def do_CONNECT(self):
                    self.send_response(200); self.end_headers(); self.wfile.flush()
                    if not self.rfile.readline(): return
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""): pass
                    time.sleep(0.005 if kind == "fast" else 0.06)
                    status = b"405 Method Not Allowed" if health[kind] else b"503 Service Unavailable"
                    self.wfile.write(b"HTTP/1.1 " + status + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    self.wfile.flush()
            result = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=result.serve_forever, daemon=True).start(); self.servers.append(result)
            return result.server_port
        fast, backup, subscription = server("fast"), server("backup"), server("subscription")
        raw = self.data["profiles"]["alice"]
        mixed, controller = unused_port(), unused_port()
        while mixed == controller: controller = unused_port()
        raw["proxy"] = {"enabled": True, "mixed_port": mixed, "controller_port": controller,
                        "mihomo_bin": os.environ["CWS_TEST_MIHOMO"], "health_url": "http://127.0.0.1:" + str(fast) + "/health",
                        "health_interval": 1, "health_timeout_ms": 1000, "download_route": "DIRECT"}
        self.save(); self.profile = self.config.get("alice")
        own = network.settings(self.config, self.profile)
        source = {"proxies": [{"name": name, "type": "http", "server": "127.0.0.1", "port": port}
                              for name, port in [("fast", fast), ("backup", backup), ("香港-test", fast)]]}
        self.subscription["content"] = yaml.safe_dump(source).encode()
        write_profile(own, "local", "http://127.0.0.1:" + str(subscription) + "/subscription", self.subscription["content"], "DIRECT")
        result = subprocess.run([sys.executable, "-m", "codex_workspaces", "--config", str(self.path), "proxy", "alice", "provider", "use", "local"],
                                capture_output=True, text=True, timeout=20)
        if result.returncode: raise RuntimeError("Offline provider activation failed: " + result.stderr)

    def tearDown(self):
        for profile in self.config.profiles.values():
            tmux.call(self.config, profile, "kill-server", check=False)
        time.sleep(.3)
        for server in self.servers: server.shutdown(); server.server_close()

    def wait_for(self, condition, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if condition(): return
            except (OSError, ValueError): pass
            time.sleep(.25)
        self.fail("Real Mihomo condition did not become true")

    def test_real_engine_filtering_failover_restart_and_owned_stop(self):
        p = self.profile
        try:
            network.start(self.config, p)
        except RuntimeError as exc:
            logs = list(p.proxy_home.glob('service-*.log'))
            panes = tmux.windows(self.config, p)
            details = {str(log.name): log.read_text(errors='replace')[-3000:] for log in logs}
            for row in panes:
                details[row[1]] = tmux.call(self.config, p, 'capture-pane', '-p', '-t', row[0], '-S', '-20', check=False).stdout
            raise RuntimeError(str(exc) + ': ' + repr(details)) from exc
        self.wait_for(lambda: network.status(self.config, p)["ready"])
        group = network.api(p, "/proxies/OUTBOUND")
        self.assertEqual(set(group["all"]), {"fast", "backup"})
        self.wait_for(lambda: network.api(p, "/proxies/OUTBOUND")["now"] == "fast")
        self.health["fast"] = False
        self.wait_for(lambda: network.api(p, "/proxies/OUTBOUND")["now"] == "backup")
        self.health["fast"] = True
        # Restart just the child; the tagged runner and work pane must survive.
        row = next(row for row in tmux.windows(self.config, p) if row[1] == "net-mihomo")
        runner = row[4]
        def children(): return (Path("/proc") / runner / "task" / runner / "children").read_text().split()
        old = children(); self.assertEqual(len(old), 1); os.kill(int(old[0]), signal.SIGTERM)
        self.wait_for(lambda: bool(children()) and children() != old)
        self.wait_for(lambda: network.status(self.config, p)["ready"])
        before = tmux.call(self.config, p, "list-panes", "-t", "=" + p.session + ":0", "-F", "#{pane_pid}").stdout
        network.stop(self.config, p)
        self.wait_for(lambda: not network.status(self.config, p)["ready"])
        self.assertEqual(before, tmux.call(self.config, p, "list-panes", "-t", "=" + p.session + ":0", "-F", "#{pane_pid}").stdout)
        self.wait_for(lambda: not any(network.status(self.config, p)["ports"].values()))
