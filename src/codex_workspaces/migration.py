"""Read-only legacy import: write private configuration, never adopt live servers."""
import ast
import json
from pathlib import Path
import re

from .config import load
from .util import atomic, json_write


def literal_path(source, variable):
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == variable for t in node.targets):
            value = node.value
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "Path" and len(value.args) == 1 and isinstance(value.args[0], ast.Constant):
                return Path(str(value.args[0].value))
    raise ValueError("Legacy runtime path could not be read safely")


def import_legacy(source, output, state):
    source, output = Path(source).expanduser(), Path(output).expanduser()
    if output.exists():
        raise ValueError("Private import destination already exists")
    raw = json.loads((source / "profiles.json").read_text())
    keys_path = source / "ssh-public-keys.json"
    keys = json.loads(keys_path.read_text()) if keys_path.exists() else {}
    data = {"version": 1, "namespace": "cws", "state_dir": str(Path(state).expanduser()), "history_limit": 50000, "profiles": {}}
    for name, values in raw.items():
        proxy = {"enabled": False}
        env_file = source / "profiles" / name / "env.sh"
        network_file = source / (name + "-network.py")
        if env_file.exists() and network_file.exists():
            env = env_file.read_text(); code = network_file.read_text()
            mixed = re.search(r"HTTP_PROXY=http://127\.0\.0\.1:(\d+)", env)
            controller = re.search(r"API\s*=\s*['\"]http://127\.0\.0\.1:(\d+)", code)
            if mixed and controller:
                home = literal_path(code, "HOME")
                proxy = {"enabled": True, "runtime_dir": str(home), "mihomo_bin": str(home / "mihomo"),
                         "mixed_port": int(mixed[1]), "controller_port": int(controller[1])}
        ssh = {}
        if name in keys:
            key_file = output.parent / (output.stem + "-keys") / (name + ".pub")
            content = keys[name].strip() + "\n"
            if key_file.exists() and key_file.read_text() != content:
                raise ValueError("Existing public-key file differs; choose another destination")
            if not key_file.exists():
                atomic(key_file, content)
            ssh["public_key_file"] = str(key_file)
        data["profiles"][name] = {"codex_home": values["home"], "cwd": values["cwd"], "codex_bin": values["binary"],
                                  "session": values["session"], "proxy": proxy, "ssh": ssh}
    json_write(output, data)
    try:
        load(output)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return {"written": str(output), "profiles": len(raw), "live_services_changed": False,
            "note": "New sockets are not adopted. Existing ports may be busy; plan a separate cutover."}
