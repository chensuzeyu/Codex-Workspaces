import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time
import unittest

from codex_workspaces import environment, tmux
from helpers import PrivateFixture


@unittest.skipUnless(shutil.which("tmux"), "tmux is required for integration tests")
class TmuxIntegrationTests(PrivateFixture):
    def tearDown(self):
        for profile in self.config.profiles.values():
            tmux.call(self.config, profile, "kill-server", check=False)

    def test_accounts_new_windows_splits_and_hostile_client_environment(self):
        for profile in self.config.profiles.values():
            tmux.start(self.config, profile)
            self.assertEqual(tmux.call(self.config, profile, "show-environment", "-g", "CODEX_HOME").stdout.strip(), "CODEX_HOME=" + str(profile.codex_home))
            output = self.root / (profile.name + "-terminal.json")
            command = "exec " + shlex.join([self.config.bash_bin, "--rcfile", str(profile.state / "shellrc.bash"), "-i", "-c",
                        "codex --capture-env > " + shlex.quote(str(output)) + "; sleep 10"])
            hostile = os.environ.copy(); hostile.update(CODEX_HOME="/wrong-home", OPENAI_API_KEY="fake", HTTPS_PROXY="wrong")
            window = tmux.call(self.config, profile, "new-window", "-d", "-P", "-F", "#{window_id}", "-t", "=" + profile.session,
                               "-c", str(self.root), command, env=hostile).stdout.strip()
            for _ in range(50):
                if output.exists() and output.stat().st_size: break
                time.sleep(0.1)
            data = json.loads(output.read_text())
            self.assertEqual(data["CODEX_HOME"], str(profile.codex_home)); self.assertEqual(data["CWS_PROFILE"], profile.name)
            self.assertIsNone(data["OPENAI_API_KEY"]); self.assertIsNone(data["HTTPS_PROXY"])
            pane = tmux.call(self.config, profile, "split-window", "-d", "-P", "-F", "#{pane_id}", "-t", window).stdout.strip()
            self.assertEqual(tmux.call(self.config, profile, "display-message", "-p", "-t", pane, "#{history_limit}").stdout.strip(), "50000")

    def test_repeated_start_preserves_original_processes(self):
        p = self.config.get("alice"); tmux.start(self.config, p)
        before = tmux.call(self.config, p, "list-panes", "-a", "-F", "#{pane_id}|#{pane_pid}").stdout
        tmux.start(self.config, p); tmux.start(self.config, p)
        self.assertEqual(before, tmux.call(self.config, p, "list-panes", "-a", "-F", "#{pane_id}|#{pane_pid}").stdout)

    def test_unowned_server_is_not_adopted_or_stopped(self):
        p = self.config.get("alice")
        tmux.call(self.config, p, "new-session", "-d", "-s", p.session, "sleep", "30")
        before = tmux.call(self.config, p, "list-panes", "-F", "#{pane_pid}").stdout
        with self.assertRaises(RuntimeError): tmux.start(self.config, p)
        self.assertEqual(before, tmux.call(self.config, p, "list-panes", "-F", "#{pane_pid}").stdout)

    def test_history_and_scroll_can_be_applied_without_restarting_work(self):
        p = self.config.get("alice"); tmux.start(self.config, p)
        before = tmux.call(self.config, p, "list-panes", "-F", "#{pane_pid}").stdout
        tmux.apply_scroll(self.config, p.socket, self.root / "tmux.conf")
        self.assertEqual(before, tmux.call(self.config, p, "list-panes", "-F", "#{pane_pid}").stdout)
        self.assertEqual(tmux.call(self.config, p, "show-options", "-gv", "mouse").stdout.strip(), "on")
        code = "import time\nfor i in range(3000): print('HISTORY_%04d'%i,flush=True)\ntime.sleep(15)"
        pane = tmux.call(self.config, p, "new-window", "-d", "-P", "-F", "#{pane_id}", "-t", "=" + p.session,
                         "exec python3 -u -c " + shlex.quote(code)).stdout.strip()
        for _ in range(50):
            size = int(tmux.call(self.config, p, "display-message", "-p", "-t", pane, "#{history_size}").stdout)
            if size > 2500: break
            time.sleep(.1)
        self.assertGreater(size, 2500)
        self.assertIn("HISTORY_0000", tmux.call(self.config, p, "capture-pane", "-p", "-t", pane, "-S", "-").stdout)
        tmux.call(self.config, p, "copy-mode", "-e", "-t", pane)
        tmux.call(self.config, p, "send-keys", "-X", "-t", pane, "-N", "5", "scroll-up")
        self.assertGreater(int(tmux.call(self.config, p, "display-message", "-p", "-t", pane, "#{scroll_position}").stdout), 0)
