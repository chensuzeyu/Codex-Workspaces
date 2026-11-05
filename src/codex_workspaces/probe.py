"""An opt-in authenticated CLI probe, distinct from proxy reachability."""
from datetime import datetime, timezone
import json
import os
import subprocess
import tempfile
import time

from . import environment
from .util import json_write, stop_group


def run(config, profile, timeout=150):
    if timeout < 1:
        raise ValueError("Probe timeout must be positive")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    folder = profile.state / "evidence" / stamp
    folder.mkdir(parents=True, mode=0o700)
    expected = "CODEX_WORKSPACE_OK"
    args = ["exec", "--ephemeral", "--sandbox", "read-only", "--skip-git-repo-check", "--json",
            "Only reply exactly " + expected + ". Do not use tools, read files or execute commands."]
    started = time.monotonic()
    timed_out = False
    with tempfile.TemporaryDirectory(prefix="cws-probe-") as cwd:
        with (folder / "events.jsonl").open("wb") as output, (folder / "stderr.log").open("wb") as errors:
            os.chmod(folder / "events.jsonl", 0o600); os.chmod(folder / "stderr.log", 0o600)
            process = subprocess.Popen(environment.codex_argv(config, profile, args), cwd=cwd,
                                       env=environment.build(config, profile), stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=errors, start_new_session=True)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                stop_group(process)
    messages, types = [], []
    for line in (folder / "events.jsonl").read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        types.append(event.get("type"))
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            messages.append(item.get("text", "").strip())
    result = {"profile": profile.name, "passed": process.returncode == 0 and not timed_out and "turn.completed" in types and expected in messages,
              "turn_completed": "turn.completed" in types, "timed_out": timed_out, "returncode": process.returncode,
              "elapsed_seconds": round(time.monotonic() - started, 2), "evidence": str(folder)}
    json_write(folder / "result.json", result)
    return result
