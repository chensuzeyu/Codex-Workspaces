"""Public CLI adapter over the private Mihomo provider backend."""
import getpass
from pathlib import Path

from . import network, tmux
from .util import listening


def require_owned(config, profile):
    if not tmux.exists(config, profile):
        raise RuntimeError("Start this profile's owned network before controlling it")
    tmux.owned(config, profile)
    marker = config.identity + ":" + profile.name + ":mihomo"
    if not any(row[3] == marker and row[2] == "0" for row in tmux.windows(config, profile)):
        raise RuntimeError("Controller is not managed by this profile")


def provider(config, profile, args):
    from .proxy import provider as backend
    from .proxy.provider import save_bytes, dump
    own = network.settings(config, profile)
    action = args.action
    if action == "list":
        backend.show(own)
        return
    if not args.provider:
        raise ValueError("Provider name is required")
    if args.provider == "all" and action not in ("refresh", "check"):
        raise ValueError("all is supported only for refresh/check")
    names = backend.profile_names(own) if args.provider == "all" else [backend.check_name(args.provider)]
    with backend.locked(own):
        if action == "use":
            if listening(own.controller_port):
                require_owned(config, profile)
                backend.use(own, names[0])
            else:
                source, candidate = backend.source_config(own, names[0])
                backend.validate(own, candidate)
                current = own.home / "config.yaml"
                if current.exists():
                    save_bytes(own.home / "config.previous.yaml", current.read_bytes())
                cache = own.home / candidate["proxy-providers"]["subscription"]["path"]
                if not cache.exists():
                    save_bytes(cache, dump({"proxies": source["proxies"]}))
                save_bytes(current, dump(candidate))
                print("Provider prepared; start the profile to bring it online")
            return
        failures = []
        for name in names:
            try:
                if action == "check":
                    backend.check(own, name)
                    continue
                if action == "register":
                    url = Path(args.url_file).expanduser().read_text().strip() if args.url_file else getpass.getpass("Subscription URL (hidden): ")
                else:
                    url = (own.home / "profiles" / name / "url.txt").read_text().strip()
                if not url.startswith(("http://", "https://")):
                    raise ValueError("Subscription URL must use HTTP or HTTPS")
                if listening(own.mixed_port):
                    require_owned(config, profile)
                raw = Path(args.source_file).expanduser().read_bytes() if args.source_file else backend.download(url, own)
                if action == "preview":
                    import yaml
                    backend.validate(own, backend.build(yaml.safe_load(raw), url, own))
                    print("Provider preview validated; nothing activated")
                else:
                    backend.write_profile(own, name, url, raw, args.download_route)
            except (Exception, SystemExit):
                failures.append(name)
        if failures:
            raise RuntimeError("Provider operation failed; original active config retained: " + ", ".join(failures))


def control(config, profile, action, node=None):
    from .proxy import control as backend
    require_owned(config, profile)
    own = network.settings(config, profile)
    if action == "status":
        backend.show_status(own)
    elif action == "nodes":
        backend.show_nodes(own)
    elif action == "pin":
        if not node:
            raise ValueError("Choose a node ID from nodes")
        backend.pin(own, node)
    elif action == "auto":
        backend.automatic(own)
    else:
        backend.refresh(own)
