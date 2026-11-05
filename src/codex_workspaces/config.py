"""Private configuration schema and collision checks; no deployment constants."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil

from .util import digest, json_write

NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")


def real_tool(name):
    """Avoid re-discovering a profile's own generated command wrapper."""
    for directory in os.environ.get("PATH", os.defpath).split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            with candidate.open("rb") as stream:
                if b"codex_workspaces" not in stream.read(512):
                    return str(candidate.resolve())
    return name


def default_path():
    return Path(os.environ.get("CWS_CONFIG", Path.home() / ".config/codex-workspaces/config.json")).expanduser()


@dataclass(frozen=True)
class Profile:
    name: str
    codex_home: Path
    cwd: Path
    codex_bin: str
    socket: str
    session: str
    state: Path
    proxy: dict
    ssh: dict
    commands: dict
    inline: bool

    @property
    def proxy_home(self):
        return Path(self.proxy["runtime_dir"])

    @property
    def proxy_url(self):
        return "http://127.0.0.1:" + str(self.proxy["mixed_port"])


@dataclass(frozen=True)
class Config:
    path: Path
    state: Path
    namespace: str
    history_limit: int
    tmux_bin: str
    bash_bin: str
    profiles: dict[str, Profile]

    @property
    def identity(self):
        return digest(self.path)

    def get(self, name):
        if name not in self.profiles:
            raise ValueError("Unknown profile: " + str(name))
        return self.profiles[name]


def load(path=None):
    path = Path(path or default_path()).expanduser().resolve()
    data = json.loads(path.read_text())
    if set(data) - {"version", "namespace", "state_dir", "history_limit", "tmux_bin", "bash_bin", "profiles"}:
        raise ValueError("Unknown top-level configuration field")
    if path.stat().st_mode & 0o022:
        raise ValueError("Configuration must not be group/world writable")
    if data.get("version") != 1:
        raise ValueError("Unsupported configuration version")
    def expand(value):
        item = Path(os.path.expandvars(str(value))).expanduser()
        return (path.parent / item).resolve() if not item.is_absolute() else item.resolve()
    namespace = data.get("namespace", "cws")
    if not NAME.fullmatch(namespace):
        raise ValueError("Invalid namespace")
    state = expand(data.get("state_dir", "~/.local/state/codex-workspaces"))
    source = Path(__file__).resolve().parents[2]
    if state.is_relative_to(source) or path.is_relative_to(source):
        raise ValueError("Keep private configuration and runtime state outside the source checkout")
    history = int(data.get("history_limit", 50000))
    if not 1 <= history <= 1_000_000:
        raise ValueError("history_limit must be between 1 and 1000000")
    profiles = {}
    homes, sockets, ports, runtimes = set(), set(), set(), set()
    for name, raw in data.get("profiles", {}).items():
        if not NAME.fullmatch(name):
            raise ValueError("Invalid profile name")
        home = expand(raw["codex_home"])
        if home.is_relative_to(source):
            raise ValueError("Keep CODEX_HOME outside the public source checkout")
        if set(raw) - {"codex_home", "cwd", "codex_bin", "socket", "session", "proxy", "ssh", "service_commands", "inline"}:
            raise ValueError("Unknown profile configuration field")
        cwd = expand(raw.get("cwd", "~"))
        socket = raw.get("socket", namespace + "-" + name)
        session = raw.get("session", name + "-1")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", socket) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session):
            raise ValueError("Invalid tmux socket/session name")
        if home in homes or socket in sockets:
            raise ValueError("Profiles must not share CODEX_HOME or tmux sockets")
        homes.add(home); sockets.add(socket)
        own = state / "profiles" / name
        proxy = dict(raw.get("proxy", {}))
        proxy.setdefault("enabled", False)
        proxy["runtime_dir"] = str(expand(proxy.get("runtime_dir", own / "proxy")))
        if proxy["enabled"]:
            values = [int(proxy[key]) for key in ["mixed_port", "controller_port"]]
            if any(not 1 <= p <= 65535 or p in ports for p in values) or len(set(values)) != 2:
                raise ValueError("Proxy and controller ports must be valid and unique across profiles")
            ports.update(values)
            runtime = Path(proxy["runtime_dir"])
            if runtime in runtimes or runtime.is_relative_to(source):
                raise ValueError("Proxy runtime directories must be private and distinct")
            runtimes.add(runtime)
            if "mihomo_bin" in proxy:
                proxy["mihomo_bin"] = str(expand(proxy["mihomo_bin"]))
        ssh = dict(raw.get("ssh", {}))
        if ssh.get("public_key_file"):
            ssh["public_key_file"] = str(expand(ssh["public_key_file"]))
        commands = raw.get("service_commands", {})
        for key, command in commands.items():
            if key not in ("mihomo", "health") or not isinstance(command, list) or not command or not all(isinstance(s, str) and s for s in command):
                raise ValueError("service_commands must contain nonempty argument arrays")
        binary = raw.get("codex_bin", "codex")
        if "/" in binary:
            binary = str(expand(binary))
        profiles[name] = Profile(name, home, cwd, binary, socket, session, own, proxy, ssh, commands, raw.get("inline", True))
    if not profiles:
        raise ValueError("Configure at least one profile")
    return Config(path, state, namespace, history, data.get("tmux_bin", real_tool("tmux")),
                  data.get("bash_bin", shutil.which("bash") or "bash"), profiles)


def initialize(path=None):
    path = Path(path or default_path()).expanduser()
    if path.exists():
        raise ValueError("Configuration already exists")
    data = {"version": 1, "namespace": "cws", "state_dir": "~/.local/state/codex-workspaces", "history_limit": 50000,
            "profiles": {name: {"codex_home": "~/.local/share/codex-workspaces/" + name + "/codex", "cwd": "~", "codex_bin": "codex",
             "proxy": {"enabled": False}, "ssh": {}} for name in ("alice", "bob")}}
    json_write(path, data)
    return path
