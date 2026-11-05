"""Named sockets with ownership markers; never guess the default server."""
import json
import os
import shlex
import subprocess

from . import environment
from .util import atomic, launcher, lock


def call(config, profile, *args, check=True, env=None):
    return subprocess.run([config.tmux_bin, "-L", profile.socket, *args], capture_output=True, text=True, check=check, env=env)


def exists(config, profile):
    result = call(config, profile, "list-sessions", check=False)
    if result.returncode == 0:
        return True
    if any(text in result.stderr.lower() for text in ("no server running", "error connecting", "failed to connect")):
        return False
    raise RuntimeError("Unable to inspect tmux server")


def owned(config, profile):
    if call(config, profile, "show-options", "-gqv", "@cws-owner").stdout.strip() != config.identity + ":" + profile.name:
        raise RuntimeError("Refusing to manage an unowned tmux server: " + profile.socket)
    value = call(config, profile, "show-environment", "-g", "CODEX_HOME").stdout.strip()
    if value != "CODEX_HOME=" + str(profile.codex_home):
        raise RuntimeError("Existing server has a different CODEX_HOME")


def scroll_text(history):
    return (f"set -g mouse on\nset -g history-limit {history}\n"
            "bind -n WheelUpPane if-shell -F -t = '#{pane_in_mode}' 'send-keys -M' 'copy-mode -e -t =; send-keys -X -t = -N 5 scroll-up'\n"
            "bind -n WheelDownPane if-shell -F -t = '#{pane_in_mode}' 'send-keys -M'\n")


def render(config, profile):
    command = shlex.join([config.bash_bin, "--rcfile", str(profile.state / "shellrc.bash"), "-i"])
    # JSON quoting is compatible with tmux's quoted configuration strings.
    return ("set -g default-shell " + json.dumps(config.bash_bin) + "\n"
            + "set -g default-command " + json.dumps("exec " + command) + "\n"
            + "set -g update-environment ''\n"
            + "set -g @cws-owner " + json.dumps(config.identity + ":" + profile.name) + "\n"
            + "set -g status-left " + json.dumps("[" + profile.name + "] ") + "\n"
            + "set -g status-right '#S | %H:%M'\n" + scroll_text(config.history_limit))


def start(config, profile):
    with lock(config.identity + "-tmux-" + profile.name):
        environment.prepare(config, profile)
        if not profile.cwd.is_dir():
            raise ValueError("Configured working directory does not exist")
        if exists(config, profile):
            owned(config, profile)
        else:
            path = profile.state / "tmux.conf"
            atomic(path, render(config, profile))
            call(config, profile, "-f", str(path), "new-session", "-d", "-s", profile.session,
                 "-c", str(profile.cwd), env=environment.build(config, profile))
            owned(config, profile)
        if call(config, profile, "has-session", "-t", "=" + profile.session, check=False).returncode:
            call(config, profile, "new-session", "-d", "-s", profile.session, "-c", str(profile.cwd),
                 env=environment.build(config, profile))


def windows(config, profile):
    result = call(config, profile, "list-windows", "-t", "=" + profile.session,
                  "-F", "#{window_id}|#{window_name}|#{pane_dead}|#{@cws-service}|#{pane_pid}")
    return [line.split("|", 4) for line in result.stdout.splitlines()]


def status(config, profile):
    if not exists(config, profile):
        return {"running": False, "socket": profile.socket}
    owned(config, profile)
    panes = call(config, profile, "list-panes", "-a", "-F",
                 "#{session_name}:#{window_index}.#{pane_index}|#{pane_id}|#{pane_pid}|#{history_size}|#{history_limit}")
    return {"running": True, "socket": profile.socket, "session": profile.session,
            "windows": windows(config, profile), "panes": [row.split("|") for row in panes.stdout.splitlines()]}


def attach(config, profile):
    if not os.isatty(0):
        raise RuntimeError("attach requires an interactive terminal")
    start(config, profile)
    os.execvpe(config.tmux_bin, [config.tmux_bin, "-L", profile.socket, "attach-session", "-E", "-t", "=" + profile.session],
               environment.build(config, profile))


def apply_scroll(config, socket, persist=None):
    """Explicit opt-in for an existing/default server; changes scroll options only."""
    if not socket or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in socket):
        raise ValueError("Invalid target socket")
    path = config.state / "scroll.conf"
    atomic(path, scroll_text(config.history_limit))
    subprocess.run([config.tmux_bin, "-L", socket, "source-file", str(path)], check=True)
    sessions = subprocess.check_output([config.tmux_bin, "-L", socket, "list-sessions", "-F", "#{session_id}"], text=True)
    for session in sessions.splitlines():
        for option, value in (("mouse", "on"), ("history-limit", str(config.history_limit))):
            subprocess.run([config.tmux_bin, "-L", socket, "set", "-t", session, option, value], check=True)
    if persist:
        from pathlib import Path
        destination = Path(persist).expanduser()
        include = "source-file " + json.dumps(str(path))
        text = destination.read_text() if destination.exists() else ""
        if include not in text:
            atomic(destination, text.rstrip() + "\n# codex-workspaces: scroll settings only\n" + include + "\n")
