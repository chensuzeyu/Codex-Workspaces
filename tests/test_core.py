import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from codex_workspaces import app_probe, audit, environment, probe, ssh
from codex_workspaces.config import load
from codex_workspaces.migration import import_legacy
from helpers import PrivateFixture


def key_bytes(seed):
    kind = b"ssh-ed25519"
    return b"\0\0\0\x0b" + kind + b"\0\0\0\x20" + bytes([seed]) * 32


class ConfigurationTests(PrivateFixture):
    def test_duplicate_account_home_is_rejected(self):
        self.data["profiles"]["bob"]["codex_home"] = self.data["profiles"]["alice"]["codex_home"]
        with self.assertRaises(ValueError): self.save()

    def test_duplicate_socket_is_rejected(self):
        for p in self.data["profiles"].values(): p["socket"] = "same"
        with self.assertRaises(ValueError): self.save()

    def test_ports_cannot_collide_across_profiles(self):
        for p in self.data["profiles"].values(): p["proxy"] = {"enabled": True, "mixed_port": 18080, "controller_port": 18081}
        with self.assertRaises(ValueError): self.save()

    def test_proxy_and_control_cannot_share_a_port(self):
        self.data["profiles"]["alice"]["proxy"] = {"enabled": True, "mixed_port": 18080, "controller_port": 18080}
        with self.assertRaises(ValueError): self.save()

    def test_unknown_fields_are_rejected(self):
        self.data["profiles"]["alice"]["account_token"] = "not-a-credential"
        with self.assertRaises(ValueError): self.save()

    def test_invalid_profile_cannot_inject_shell(self):
        self.data["profiles"]["bad;name"] = self.data["profiles"].pop("alice")
        with self.assertRaises(ValueError): self.save()

    def test_relative_paths_are_relative_to_private_configuration(self):
        self.data["profiles"]["alice"]["codex_home"] = "relative/account"
        self.assertEqual(self.save().get("alice").codex_home, self.path.parent / "relative/account")

    def test_group_writable_config_is_rejected(self):
        self.path.chmod(0o666)
        with self.assertRaises(ValueError): load(self.path)


class EnvironmentTests(PrivateFixture):
    def test_ambient_account_keys_and_proxy_are_not_inherited(self):
        p = self.config.get("alice")
        env = environment.build(self.config, p, {"PATH": "/usr/bin", "CODEX_HOME": "/wrong", "TMUX": "wrong", "OPENAI_API_KEY": "fake", "HTTPS_PROXY": "wrong"})
        self.assertEqual(env["CODEX_HOME"], str(p.codex_home))
        self.assertNotIn("OPENAI_API_KEY", env); self.assertNotIn("HTTPS_PROXY", env); self.assertNotIn("TMUX", env)

    def test_each_proxy_routes_to_its_own_port(self):
        for index, raw in enumerate(self.data["profiles"].values()):
            raw["proxy"] = {"enabled": True, "mixed_port": 18080 + 2 * index, "controller_port": 18081 + 2 * index}
        self.save()
        for name, port in [("alice", 18080), ("bob", 18082)]:
            env = environment.build(self.config, self.config.get(name))
            self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:" + str(port))

    def test_generated_entry_handles_paths_with_spaces(self):
        self.data["state_dir"] = str(self.root / "state with spaces"); self.save()
        p = self.config.get("alice"); environment.prepare(self.config, p)
        r = subprocess.run([str(p.state / "bin/codex"), "--capture-env"], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(r.stdout)["CODEX_HOME"], str(p.codex_home))

    def test_codex_arguments_keep_file_auth_and_inline_mode(self):
        args = environment.codex_argv(self.config, self.config.get("alice"), ["exec", "--help"])
        self.assertIn("--no-alt-screen", args); self.assertIn('cli_auth_credentials_store="file"', args)

    def test_explicit_dispatcher_cannot_recurse(self):
        dispatcher = self.root / "dispatcher"
        dispatcher.write_text("#!/bin/sh\n# CWS-DISPATCHER\n"); dispatcher.chmod(0o755)
        self.data["profiles"]["alice"]["codex_bin"] = str(dispatcher); self.save()
        with self.assertRaises(ValueError): environment.binary(self.config, self.config.get("alice"))


class SSHTests(PrivateFixture):
    def configure_keys(self):
        for index, name in enumerate(self.config.profiles):
            path = self.root / (name + ".pub")
            path.write_text("ssh-ed25519 " + base64.b64encode(key_bytes(index + 1)).decode() + " user@example.invalid\n")
            self.data["profiles"][name]["ssh"] = {"public_key_file": str(path)}
        self.save()

    def test_distinct_keys_bind_profiles_and_preserve_existing_access(self):
        self.configure_keys()
        original = "# existing access\nssh-ed25519 unrelated-existing-key comment\n"
        text, fingerprints = ssh.authorized_text(self.config, original)
        self.assertIn(original, text); self.assertEqual(len(fingerprints), 2)
        again, _ = ssh.authorized_text(self.config, text); self.assertEqual(text, again)

    def test_duplicate_key_cannot_select_two_accounts(self):
        self.configure_keys()
        self.data["profiles"]["bob"]["ssh"] = self.data["profiles"]["alice"]["ssh"]
        self.save()
        with self.assertRaises(ValueError): ssh.authorized_text(self.config, "")

    def test_forced_command_and_conflicting_owner_are_not_overwritten(self):
        self.configure_keys(); key, _ = ssh.public_key(self.data["profiles"]["alice"]["ssh"]["public_key_file"])
        for prefix in ['command="restricted"', 'environment="CWS_SSH_PROFILE=bob"']:
            with self.assertRaises(ValueError): ssh.authorized_text(self.config, prefix + " " + key)

    def test_install_is_dry_by_default_and_idempotent_when_applied(self):
        self.configure_keys(); auth = self.root / "ssh/authorized_keys"; auth.parent.mkdir(); auth.write_text("# original\n")
        target = self.root / "bin/codex"
        with patch("codex_workspaces.ssh.subprocess.run") as run:
            run.return_value.stdout = "permituserenvironment yes\n"
            self.assertFalse(ssh.install(self.config, auth, target)["applied"]); self.assertFalse(target.exists())
            ssh.install(self.config, auth, target, apply=True); original = auth.read_text()
            ssh.install(self.config, auth, target, apply=True); self.assertEqual(auth.read_text(), original)
        self.assertIn("@ssh", target.read_text())

    def test_unowned_dispatcher_is_never_replaced(self):
        self.configure_keys(); target = self.root / "codex"; target.write_text("other installation")
        with patch("codex_workspaces.ssh.subprocess.run") as run:
            run.return_value.stdout = "permituserenvironment yes\n"
            with self.assertRaises(ValueError): ssh.install(self.config, self.root / "auth", target, True)
        self.assertEqual(target.read_text(), "other installation")

    def test_install_rolls_back_if_authorized_keys_write_fails(self):
        self.configure_keys(); auth = self.root / "auth"; auth.write_text("# original\n"); target = self.root / "codex"
        from codex_workspaces.util import atomic
        count = [0]
        def failing(path, data, mode=0o600):
            if Path(path) == auth:
                count[0] += 1
                if count[0] == 1: raise OSError("simulated write failure")
            atomic(path, data, mode)
        with patch("codex_workspaces.ssh.subprocess.run") as run, patch("codex_workspaces.ssh.atomic", side_effect=failing):
            run.return_value.stdout = "permituserenvironment yes\n"
            with self.assertRaises(OSError): ssh.install(self.config, auth, target, True)
        self.assertEqual(auth.read_text(), "# original\n"); self.assertFalse(target.exists())

    def test_client_config_rejects_newline_injection(self):
        with self.assertRaises(ValueError): ssh.client_config(self.config.get("alice"), "host\nProxyCommand malicious")


class ProbeAndMigrationTests(PrivateFixture):
    def test_cli_probe_requires_completion_and_expected_reply(self):
        result = probe.run(self.config, self.config.get("alice"), 5)
        self.assertTrue(result["passed"]); self.assertTrue(result["turn_completed"])

    def test_cli_probe_times_out_and_returns_failure(self):
        with patch.dict(os.environ, {"CWS_FIXTURE_TIMEOUT": "1"}):
            result = probe.run(self.config, self.config.get("alice"), 1)
        self.assertTrue(result["timed_out"]); self.assertFalse(result["passed"])

    def test_app_server_probe_preserves_protocol_and_completes(self):
        result = app_probe.run(self.config, self.config.get("alice"), 5)
        self.assertTrue(result["passed"]); self.assertTrue(result["protocol_clean"])

    def test_legacy_import_does_not_execute_legacy_code(self):
        legacy = self.root / "legacy"; legacy.mkdir()
        (legacy / "profiles.json").write_text(json.dumps({"alice": {"home": str(self.root / "old-home"), "cwd": str(self.root), "binary": str(self.fake), "session": "old-1"}}))
        output = self.root / "imported/config.json"
        result = import_legacy(legacy, output, self.root / "new-state")
        self.assertFalse(result["live_services_changed"]); self.assertEqual(load(output).get("alice").session, "old-1")

    def test_legacy_import_refuses_to_replace_existing_key(self):
        legacy = self.root / "legacy"; legacy.mkdir()
        (legacy / "profiles.json").write_text(json.dumps({"alice": {"home": str(self.root / "old"), "cwd": str(self.root), "binary": str(self.fake), "session": "old-1"}}))
        (legacy / "ssh-public-keys.json").write_text(json.dumps({"alice": "new key"}))
        output = self.root / "imported/config.json"
        existing = output.parent / "config-keys/alice.pub"; existing.parent.mkdir(parents=True); existing.write_text("original key")
        with self.assertRaises(ValueError): import_legacy(legacy, output, self.root / "state")
        self.assertEqual(existing.read_text(), "original key")

    def test_audit_reports_rules_without_printing_secret_values(self):
        source = self.root / "release"; source.mkdir()
        secret = "sk-" + "x" * 35; (source / "bad.txt").write_text(secret)
        report = audit.check(source)
        self.assertFalse(report["passed"]); self.assertNotIn(secret, json.dumps(report))
