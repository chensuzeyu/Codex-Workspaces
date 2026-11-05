"""CLI composition; adapters own their implementations, config owns identity."""
import argparse
import json
import os
from pathlib import Path
import sys

from . import __version__, config as configuration, environment, network, tmux
from .util import public_error


def parser():
    p = argparse.ArgumentParser(prog="codex-workspace", description="Independent Codex accounts, tmux, proxies and SSH on a shared Linux host")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--config", type=Path)
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create a private example configuration")
    commands.add_parser("validate", help="Validate paths, names and resource isolation")
    commands.add_parser("list", help="List configured profiles")
    for action in ["start", "status"]:
        cmd = commands.add_parser(action); cmd.add_argument("profile", nargs="?", default="all")
    cmd = commands.add_parser("attach"); cmd.add_argument("profile")
    cmd = commands.add_parser("env"); cmd.add_argument("profile")
    for action in ["run", "tmux"]:
        cmd = commands.add_parser(action); cmd.add_argument("profile"); cmd.add_argument("args", nargs=argparse.REMAINDER)
    cmd = commands.add_parser("network"); cmd.add_argument("action", choices=["start", "status", "stop"]); cmd.add_argument("profile")
    cmd = commands.add_parser("probe", help="Make one real, authenticated model request (uses quota)")
    cmd.add_argument("profile"); cmd.add_argument("--transport", choices=["cli", "app-server"], default="cli")
    cmd.add_argument("--ssh", help="Existing SSH config alias; implies App Server transport")
    cmd.add_argument("--timeout", type=int, default=150)
    cmd = commands.add_parser("proxy"); cmd.add_argument("profile")
    sub = cmd.add_subparsers(dest="proxy_command", required=True)
    cmd = sub.add_parser("provider"); cmd.add_argument("action", choices=["list", "register", "preview", "refresh", "check", "use"])
    cmd.add_argument("provider", nargs="?"); cmd.add_argument("--url-file"); cmd.add_argument("--source-file")
    cmd.add_argument("--download-route", choices=["DIRECT", "BOOTSTRAP"])
    cmd = sub.add_parser("control"); cmd.add_argument("action", choices=["status", "nodes", "pin", "auto", "refresh"]); cmd.add_argument("node", nargs="?")
    cmd = commands.add_parser("scroll"); cmd.add_argument("--socket", required=True); cmd.add_argument("--persist", type=Path)
    cmd = commands.add_parser("ssh"); sub = cmd.add_subparsers(dest="ssh_command", required=True)
    cmd = sub.add_parser("client-config"); cmd.add_argument("profile"); cmd.add_argument("--host", required=True)
    cmd.add_argument("--port", type=int, default=22); cmd.add_argument("--user"); cmd.add_argument("--identity", default="~/.ssh/id_ed25519")
    cmd = sub.add_parser("install"); cmd.add_argument("--authorized-keys", required=True); cmd.add_argument("--dispatcher", required=True)
    cmd.add_argument("--sshd-bin"); cmd.add_argument("--sshd-config"); cmd.add_argument("--apply", action="store_true")
    cmd = commands.add_parser("migrate"); cmd.add_argument("--from", dest="source", required=True)
    cmd.add_argument("--output", required=True); cmd.add_argument("--state-dir", required=True)
    cmd = commands.add_parser("audit"); cmd.add_argument("--source", type=Path, default=Path.cwd()); cmd.add_argument("--staged", action="store_true"); cmd.add_argument("--deny-file", type=Path)
    for action in ["_service", "_selector"]:
        cmd = commands.add_parser(action, help=argparse.SUPPRESS); cmd.add_argument("profile")
        if action == "_service": cmd.add_argument("service", choices=["mihomo", "health"])
    return p


def display(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv=None):
    os.umask(0o077)
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            print(configuration.initialize(args.config)); return 0
        if args.command == "audit":
            from .audit import check
            deny = args.deny_file.read_text().splitlines() if args.deny_file else []
            result = check(args.source, args.staged, deny); display(result); return 0 if result["passed"] else 1
        if args.command == "migrate":
            from .migration import import_legacy
            display(import_legacy(args.source, args.output, args.state_dir)); return 0
        config = configuration.load(args.config)
        if args.command == "validate":
            display({"valid": True, "profiles": list(config.profiles)}); return 0
        if args.command == "list":
            display({name: {"socket": p.socket, "session": p.session, "proxy_enabled": p.proxy["enabled"]} for name, p in config.profiles.items()}); return 0
        if args.command in ("start", "status"):
            profiles = list(config.profiles.values()) if args.profile == "all" else [config.get(args.profile)]
            if args.command == "start":
                for profile in profiles: network.start(config, profile)
            display({p.name: {"tmux": tmux.status(config, p), "network": network.status(config, p)} for p in profiles}); return 0
        if args.command == "scroll":
            tmux.apply_scroll(config, args.socket, args.persist); return 0
        if args.command == "ssh":
            from . import ssh
            if args.ssh_command == "client-config":
                print(ssh.client_config(config.get(args.profile), args.host, args.port, args.user, args.identity), end="")
            else:
                display(ssh.install(config, args.authorized_keys, args.dispatcher, args.apply, args.sshd_bin, args.sshd_config))
            return 0
        name = os.environ.get("CWS_SSH_PROFILE", "") if args.profile == "@ssh" else args.profile
        profile = config.get(name)
        if args.command == "run":
            extra = args.args[1:] if args.args[:1] == ["--"] else args.args
            if "app-server" in extra:
                network.start(config, profile)
            command = environment.codex_argv(config, profile, extra)
            os.execvpe(command[0], command, environment.build(config, profile))
        elif args.command == "tmux":
            tmux.owned(config, profile)
            extra = args.args[1:] if args.args[:1] == ["--"] else args.args
            os.execvpe(config.tmux_bin, [config.tmux_bin, "-L", profile.socket, *extra], environment.build(config, profile))
        elif args.command == "attach":
            if not sys.stdin.isatty():
                raise RuntimeError("attach requires an interactive SSH terminal")
            network.start(config, profile); tmux.attach(config, profile)
        elif args.command == "env":
            environment.prepare(config, profile); print((profile.state / "env.sh").read_text(), end="")
        elif args.command == "network":
            if args.action == "start": network.start(config, profile)
            elif args.action == "stop": network.stop(config, profile)
            display(network.status(config, profile))
        elif args.command == "probe":
            if args.transport == "app-server" or args.ssh:
                from .app_probe import run
                result = run(config, profile, args.timeout, args.ssh)
            else:
                from .probe import run
                result = run(config, profile, args.timeout)
            display(result); return 0 if result["passed"] else 1
        elif args.command == "proxy":
            from . import proxy_actions
            if args.proxy_command == "provider": proxy_actions.provider(config, profile, args)
            else: proxy_actions.control(config, profile, args.action, args.node)
        elif args.command == "_service":
            from .runner import run
            run(config, profile, args.service)
        elif args.command == "_selector":
            tmux.owned(config, profile)
            from .proxy.selector import run
            run(network.settings(config, profile))
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print("ERROR: " + public_error(error), file=sys.stderr)
        return 130 if isinstance(error, KeyboardInterrupt) else 1
