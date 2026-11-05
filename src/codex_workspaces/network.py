"""Per-profile services in owned tmux windows, private logs and status APIs."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import urllib.request

from . import environment, tmux
from .util import atomic, launcher, listening, lock


def settings(config, profile):
    from .proxy.settings import Settings, DEFAULT_EXCLUDE
    if not profile.proxy["enabled"]:
        raise ValueError("Proxy is disabled for this profile")
    values = profile.proxy
    return Settings(home=profile.proxy_home, mihomo_bin=Path(values.get("mihomo_bin", profile.proxy_home / "mihomo")),
                    config_path=config.path, mixed_port=int(values["mixed_port"]), controller_port=int(values["controller_port"]),
                    health_url=values.get("health_url", "https://chatgpt.com/backend-api/codex/responses"),
                    expected_status=int(values.get("expected_status", 405)), health_interval=int(values.get("health_interval", 10)),
                    health_timeout_ms=int(values.get("health_timeout_ms", 5000)),
                    include_filter=values.get("include_filter", ""), exclude_filter=values.get("exclude_filter", DEFAULT_EXCLUDE),
                    default_download_route=values.get("download_route", "BOOTSTRAP"),
                    subscription_interval=int(values.get("subscription_interval", 3600)),
                    codex_bin=profile.codex_bin, codex_home=str(profile.codex_home), service_name=config.namespace + "-" + profile.name)


def api(profile, path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:" + str(profile.proxy["controller_port"]) + path, timeout=4) as response:
        return json.load(response)


def command(config, profile, service):
    if service in profile.commands:
        return profile.commands[service]
    own = settings(config, profile)
    if service == "mihomo":
        return [str(own.mihomo_bin), "-d", str(own.home)]
    path = profile.state / "run-selector"
    atomic(path, launcher(config, ["_selector", profile.name]), 0o755)
    return [str(path)]


def preflight(config, profile):
    own = settings(config, profile)
    for pattern in (own.include_filter, own.exclude_filter):
        re.compile(pattern)
    if own.health_interval < 1 or own.health_timeout_ms < 1:
        raise ValueError("Health intervals and timeout must be positive")
    if "mihomo" not in profile.commands:
        if not own.mihomo_bin.is_file() or not os.access(own.mihomo_bin, os.X_OK):
            raise ValueError("Configure an executable mihomo_bin")
        from .proxy.provider import validate
        import yaml
        active = own.home / "config.yaml"
        if not active.is_file():
            raise ValueError("Register and use a provider before starting this proxy")
        content = yaml.safe_load(active.read_text())
        if content.get("mixed-port") != own.mixed_port or content.get("external-controller") != own.controller:
            raise ValueError("Private Mihomo configuration ports differ from the profile")
        if content.get("bind-address") != "127.0.0.1" or content.get("allow-lan") is not False:
            raise ValueError("Mihomo listeners must be loopback-only")
        validate(own, content)


def start(config, profile):
    tmux.start(config, profile)
    if not profile.proxy["enabled"]:
        return
    preflight(config, profile)
    with lock(config.identity + "-network-" + profile.name):
        for service in ("mihomo", "health"):
            name = "net-" + service
            rows = [row for row in tmux.windows(config, profile) if row[1] == name]
            if len(rows) > 1 or (rows and rows[0][3] != config.identity + ":" + profile.name + ":" + service):
                raise RuntimeError("Refusing to replace an unowned service window")
            if not rows or rows[0][2] == "1":
                if service == "mihomo" and any(listening(profile.proxy[key]) for key in ("mixed_port", "controller_port")):
                    raise RuntimeError("Proxy ports already occupied; refusing to take over")
                path = profile.state / ("run-" + service)
                atomic(path, launcher(config, ["_service", profile.name, service]), 0o755)
                shell_command = "exec " + shlex.quote(str(path))
                if rows:
                    wid = rows[0][0]
                    tmux.call(config, profile, "respawn-window", "-t", wid, "-c", str(profile.cwd), shell_command)
                else:
                    wid = tmux.call(config, profile, "new-window", "-d", "-P", "-F", "#{window_id}", "-t", "=" + profile.session,
                                    "-n", name, "-c", str(profile.cwd), shell_command,
                                    env=environment.build(config, profile)).stdout.strip()
                    tmux.call(config, profile, "set", "-w", "-t", wid, "@cws-service", config.identity + ":" + profile.name + ":" + service)
                    tmux.call(config, profile, "set", "-w", "-t", wid, "remain-on-exit", "on")
            if service == "mihomo":
                for _ in range(40):
                    try:
                        api(profile, "/version")
                        if listening(profile.proxy["mixed_port"]):
                            break
                    except (OSError, ValueError):
                        pass
                    time.sleep(0.5)
                else:
                    raise RuntimeError("Proxy not ready; inspect the private service log")


def status(config, profile):
    if not profile.proxy["enabled"]:
        return {"enabled": False, "ready": True}
    result = {"enabled": True, "ports": {key: listening(profile.proxy[key]) for key in ("mixed_port", "controller_port")}, "ready": False}
    try:
        own = settings(config, profile)
        result["version"] = api(profile, "/version").get("version")
        group = api(profile, "/proxies/OUTBOUND")
        excluded = re.compile(own.exclude_filter)
        result["candidate_count"] = len(group.get("all", []))
        result["excluded_candidates"] = sum(bool(excluded.search(name)) for name in group.get("all", []))
        result["selected_excluded"] = bool(excluded.search(group.get("now", "")))
        included = re.compile(own.include_filter) if own.include_filter else None
        result["outside_include_filter"] = sum(not included.search(name) for name in group.get("all", [])) if included else 0
        state_file = profile.proxy_home / "selector-state.json"
        state = json.loads(state_file.read_text())
        result["state_age_seconds"] = round(time.time() - state_file.stat().st_mtime, 1)
        result["eligible_count"] = sum(bool(value.get("ready")) for value in state.get("nodes", {}).values())
        result["mode"] = "pinned-with-failover" if (profile.proxy_home / "selector-pin.json").exists() else "auto"
        live = tmux.windows(config, profile)
        services_live = all(any(row[1] == "net-" + service and row[2] == "0" and row[3] == config.identity + ":" + profile.name + ":" + service for row in live) for service in ("mihomo", "health"))
        result["ready"] = (all(result["ports"].values()) and services_live and 0 <= result["state_age_seconds"] < max(30, own.health_interval * 3)
                           and result["eligible_count"] > 0 and result["excluded_candidates"] == 0 and result["outside_include_filter"] == 0 and not result["selected_excluded"])
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        result["error_type"] = type(error).__name__
    return result


def stop(config, profile):
    """Stop only tagged service windows. Work panes and other sockets stay alive."""
    if not tmux.exists(config, profile):
        return
    tmux.owned(config, profile)
    for row in tmux.windows(config, profile):
        if row[3] in {config.identity + ":" + profile.name + ":" + service for service in ("mihomo", "health")}:
            tmux.call(config, profile, "kill-window", "-t", row[0])
    if profile.proxy["enabled"]:
        for _ in range(24):
            if not any(listening(profile.proxy[key]) for key in ("mixed_port", "controller_port")):
                return
            time.sleep(0.5)
        raise RuntimeError("Service shutdown is incomplete; inspect the private log")
