"""Opt-in stdio App Server probe, locally or through an existing SSH alias."""
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
import queue
import subprocess
import tempfile
import threading
import time

from . import environment
from .util import json_write, stop_group


def account_digest(profile):
    try:
        auth = json.loads((profile.codex_home / "auth.json").read_text())
        payload = auth["tokens"]["id_token"].split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return hashlib.sha256(data["email"].encode()).hexdigest()
    except (OSError, ValueError, KeyError, IndexError):
        return None


def run(config, profile, timeout=150, ssh_alias=None):
    if timeout < 1:
        raise ValueError("Probe timeout must be positive")
    folder = profile.state / "evidence" / ("app-server-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"))
    folder.mkdir(parents=True, mode=0o700)
    command = (["ssh", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes", "-o", "ConnectTimeout=15", ssh_alias, "codex app-server"]
               if ssh_alias else environment.codex_argv(config, profile, ["app-server"]))
    if ssh_alias and (ssh_alias.startswith("-") or any(c.isspace() for c in ssh_alias)):
        raise ValueError("Use a concrete SSH config alias")
    events = queue.Queue()
    expected = "CODEX_WORKSPACE_APP_SERVER_OK"
    result = {"profile": profile.name, "transport": "ssh" if ssh_alias else "app-server", "initialized": False,
              "account_present": False, "turn_completed": False, "protocol_clean": True, "passed": False}
    identity = account_digest(profile)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="cws-app-probe-") as cwd, (folder / "stderr.log").open("wb") as errors:
        os.chmod(folder / "stderr.log", 0o600)
        process = subprocess.Popen(command, cwd=cwd, env=environment.build(config, profile), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=errors, text=True, start_new_session=True)
        def reader():
            for line in process.stdout:
                events.put(line)
            events.put(None)
        threading.Thread(target=reader, daemon=True).start()
        def send(value):
            process.stdin.write(json.dumps(value) + "\n"); process.stdin.flush()
        try:
            send({"id": 0, "method": "initialize", "params": {"clientInfo": {"name": "codex_workspaces_probe", "version": "0.1.0"}, "capabilities": {"experimentalApi": True}}})
            deadline = started + timeout
            while time.monotonic() < deadline:
                try:
                    line = events.get(timeout=min(1, max(0.01, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                if line is None:
                    break
                try:
                    message = json.loads(line)
                except ValueError:
                    result["protocol_clean"] = False; continue
                if message.get("error"):
                    result["rpc_error_code"] = message["error"].get("code"); break
                if message.get("id") == 0:
                    result["initialized"] = True
                    send({"method": "initialized", "params": {}})
                    send({"id": 1, "method": "account/read", "params": {"refreshToken": False}})
                elif message.get("id") == 1:
                    account = message.get("result", {}).get("account")
                    result["account_present"] = bool(account)
                    if not account:
                        break
                    result["account_type"] = account.get("type")
                    if identity:
                        result["account_matches_profile"] = identity == hashlib.sha256((account.get("email") or "").encode()).hexdigest()
                        if not result["account_matches_profile"]:
                            break
                    send({"id": 2, "method": "thread/start", "params": {"cwd": cwd if not ssh_alias else "/tmp", "approvalPolicy": "never", "sandbox": "read-only", "ephemeral": True}})
                elif message.get("id") == 2:
                    send({"id": 3, "method": "turn/start", "params": {"threadId": message["result"]["thread"]["id"], "input": [{"type": "text", "text": "Only reply exactly " + expected + ". Do not use tools, read files or execute commands."}]}})
                elif message.get("method") == "item/completed":
                    item = message.get("params", {}).get("item", {})
                    if item.get("type") == "agentMessage":
                        result["response_matches"] = item.get("text", "").strip() == expected
                elif message.get("method") == "turn/completed":
                    result["turn_completed"] = message.get("params", {}).get("turn", {}).get("status") == "completed"
                    break
                elif message.get("method") and "id" in message:
                    result["unexpected_server_request"] = True
                    send({"id": message["id"], "error": {"code": -32601, "message": "No tools for a connectivity probe"}})
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                stop_group(process)
            process.stdout.close()
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    result["passed"] = all(result.get(k) for k in ["initialized", "account_present", "turn_completed", "protocol_clean", "response_matches"]) and not result.get("unexpected_server_request")
    result["evidence"] = str(folder)
    json_write(folder / "result.json", result)
    return result
