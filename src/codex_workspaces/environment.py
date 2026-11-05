"""Explicit per-process account and proxy routing."""
import os
from pathlib import Path
import shlex
import shutil

from .util import atomic, launcher

AUTH = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_ACCESS_TOKEN", "OPENAI_IDENTITY_TOKEN_FILE", "CODEX_BIN")
PROXIES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def build(config, profile, ambient=None):
    env = dict(os.environ if ambient is None else ambient)
    for key in (*AUTH, *PROXIES, "TMUX", "TMUX_PANE"):
        env.pop(key, None)
    env.update(CODEX_HOME=str(profile.codex_home), CWS_CONFIG=str(config.path), CWS_PROFILE=profile.name)
    env["PATH"] = str(profile.state / "bin") + os.pathsep + env.get("PATH", os.defpath)
    if profile.proxy["enabled"]:
        for key in PROXIES:
            env[key] = profile.proxy_url
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost,::1"
    return env


def binary(config, profile):
    """Skip generated dispatchers when resolving a bare codex command."""
    if "/" in profile.codex_bin:
        found = profile.codex_bin
    else:
        found = None
        for part in os.environ.get("PATH", os.defpath).split(os.pathsep):
            candidate = Path(part) / profile.codex_bin
            if not candidate.is_file() or not os.access(candidate, os.X_OK):
                continue
            try:
                prefix = candidate.open("rb").read(512)
            except OSError:
                continue
            if any(marker in prefix for marker in (b"codex_workspaces", b"CWS-DISPATCHER", b"CODEX_HOME", b"ssh-codex-entry")):
                continue
            found = str(candidate.resolve()); break
    if not found or not os.path.isfile(found) or not os.access(found, os.X_OK):
        raise ValueError("Configure an executable real codex_bin for " + profile.name)
    with open(found, "rb") as stream:
        prefix = stream.read(512)
        if any(marker in prefix for marker in (b"codex_workspaces", b"CWS-DISPATCHER", b"ssh-codex-entry")):
            raise ValueError("codex_bin must be the actual CLI, not a dispatcher")
    return found


def codex_argv(config, profile, args):
    return [binary(config, profile), *(["--no-alt-screen"] if profile.inline else []), "-c", 'cli_auth_credentials_store="file"', *args]


def prepare(config, profile):
    profile.state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(profile.state, 0o700)
    for name, command in [("codex", ["run", profile.name, "--"]), ("tmux", ["tmux", profile.name, "--"])]:
        atomic(profile.state / "bin" / name, launcher(config, command), 0o755)
    values = build(config, profile)
    exports = "\n".join("export " + key + "=" + shlex.quote(value) for key, value in values.items()
                        if key in ("CODEX_HOME", "CWS_CONFIG", "CWS_PROFILE", "PATH", "NO_PROXY", "no_proxy", *PROXIES))
    exports = "unset " + " ".join((*AUTH, *PROXIES)) + "\n" + exports + "\n"
    atomic(profile.state / "env.sh", exports)
    rc = ('if [[ -r "$HOME/.bashrc" ]]; then source "$HOME/.bashrc"; fi\n'
          'unalias codex tmux 2>/dev/null || true\nunset -f codex tmux 2>/dev/null || true\n'
          "source " + shlex.quote(str(profile.state / "env.sh")) + "\nhash -r\n"
          + "PS1=" + shlex.quote("[" + profile.name + "] \\u@\\h:\\w\\$ ") + "\n")
    atomic(profile.state / "shellrc.bash", rc)
