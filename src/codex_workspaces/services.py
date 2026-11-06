"""Detached, per-profile supervisors. PID records are checked against Linux start time."""
import json
import os
from pathlib import Path
import subprocess
import time

from . import environment
from .util import atomic, launcher, runtime_root


def record(config, profile, service):
    return runtime_root() / (config.identity + "-" + profile.name + "-" + service + ".json")


def birth(pid):
    parts = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    if parts[0] == "Z":
        raise ProcessLookupError(pid)
    return parts[19]


def pid(config, profile, service):
    try:
        data = json.loads(record(config, profile, service).read_text())
        value = int(data["pid"])
        if value <= 1 or birth(value) != data["birth"]:
            return None
        args = Path(f"/proc/{value}/cmdline").read_bytes().split(b"\0")
        expected = ["--config", str(config.path), "_service", profile.name, service]
        decoded = [v.decode() for v in args]
        if any(decoded[i:i + len(expected)] == expected for i in range(len(decoded))):
            return value
    except (OSError, ValueError, KeyError):
        pass
    return None


def start(config, profile, service):
    if pid(config, profile, service):
        return
    script = profile.state / ("supervise-" + service)
    atomic(script, launcher(config, ["_service", profile.name, service]), 0o700)
    log = profile.state / ("supervisor-" + service + ".log")
    with log.open("ab") as output:
        child = subprocess.Popen([str(script)], cwd=profile.cwd, env=environment.build(config, profile),
                                 stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                 start_new_session=True, close_fds=True)
    import threading
    threading.Thread(target=child.wait, daemon=True).start()
    for _ in range(100):
        if pid(config, profile, service):
            return
        if child.poll() is not None:
            break
        time.sleep(.05)
    raise RuntimeError("Supervisor failed to start; inspect private supervisor log")


def stop(config, profile, service):
    value = pid(config, profile, service)
    if not value:
        return
    # Request graceful shutdown using a token tied to this exact process lifetime.
    token = birth(value)
    atomic(record(config, profile, service).with_suffix(".stop"), token)
    for _ in range(300):
        if pid(config, profile, service) != value:
            return
        time.sleep(.05)
    raise RuntimeError("Supervisor shutdown incomplete; inspect private logs")
