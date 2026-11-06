"""Per-profile detached services, private logs and status APIs."""
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request

from . import services
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
    if not profile.proxy["enabled"]:
        return
    preflight(config, profile)
    with lock(config.identity + "-network-" + profile.name):
        for service in ("mihomo", "health"):
            if service == "mihomo" and not services.pid(config, profile, service):
                if any(listening(profile.proxy[key]) for key in ("mixed_port", "controller_port")):
                    raise RuntimeError("Proxy ports already occupied; refusing to take over")
            services.start(config, profile, service)
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
        result["supervisors"] = {name: services.pid(config, profile, name) for name in ("mihomo", "health")}
        services_live = all(result["supervisors"].values())
        result["ready"] = (all(result["ports"].values()) and services_live and 0 <= result["state_age_seconds"] < max(30, own.health_interval * 3)
                           and result["eligible_count"] > 0 and result["excluded_candidates"] == 0 and result["outside_include_filter"] == 0 and not result["selected_excluded"])
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        result["error_type"] = type(error).__name__
    return result


def stop(config, profile):
    """Stop only this configuration/profile's verified supervisors."""
    with lock(config.identity + "-network-" + profile.name):
        for service in ("health", "mihomo"):
            services.stop(config, profile, service)
