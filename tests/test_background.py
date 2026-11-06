import os
import signal
import subprocess
import sys
import time
from unittest.mock import patch

from codex_workspaces import environment, network, services
from helpers import PrivateFixture


class BackgroundTests(PrivateFixture):
    def test_disabled_proxy_does_not_need_tmux(self):
        self.data["tmux_bin"] = "/missing/tmux"
        self.save()
        network.start(self.config, self.config.get("alice"))
        self.assertTrue(network.status(self.config, self.config.get("alice"))["ready"])

    def test_ssh_binding_precedence_missing_and_session_fallback(self):
        self.assertEqual(environment.ssh_profile(self.config, {"CWS_SSH_PROFILE": "alice"}), "alice")
        with self.assertRaises(ValueError): environment.ssh_profile(self.config, {})
        with patch("subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "CWS_SSH_PROFILE=bob\n", "")
            self.assertEqual(environment.ssh_profile(self.config, {"TMUX": "socket", "TMUX_PANE": "%1"}), "bob")
            self.assertIn("%1", run.call_args.args[0])
            self.assertNotIn("-g", run.call_args.args[0])
            self.assertEqual(environment.ssh_profile(self.config, {"CWS_SSH_PROFILE": "alice", "TMUX": "socket", "TMUX_PANE": "%1"}), "alice")
            self.assertEqual(run.call_count, 1)
        with self.assertRaises(ValueError): environment.ssh_profile(self.config, {"CWS_SSH_PROFILE": "unknown"})

    def test_detached_restart_idempotence_and_profile_stop(self):
        for name in ("alice", "bob"):
            self.data["profiles"][name]["service_commands"] = {"health": [sys.executable, "-c", "import time; time.sleep(120)"]}
        self.save()
        self.addCleanup(lambda: [network.stop(self.config, p) for p in self.config.profiles.values()])
        a, b = self.config.get("alice"), self.config.get("bob")
        for p in (a, b): services.start(self.config, p, "health")
        pid = services.pid(self.config, a, "health")
        self.assertEqual(os.getsid(pid), pid)
        services.start(self.config, a, "health")
        self.assertEqual(services.pid(self.config, a, "health"), pid)
        from pathlib import Path
        children = Path(f"/proc/{pid}/task/{pid}/children")
        deadline = time.monotonic() + 5
        while not children.read_text().split() and time.monotonic() < deadline: time.sleep(.05)
        old = children.read_text().split()
        self.assertTrue(old)
        os.kill(int(old[0]), signal.SIGTERM)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            new = children.read_text().split()
            if new and new != old: break
            time.sleep(.1)
        self.assertTrue(new and new != old)
        network.stop(self.config, a)
        self.assertIsNone(services.pid(self.config, a, "health"))
        self.assertIsNotNone(services.pid(self.config, b, "health"))
