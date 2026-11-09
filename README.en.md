# Codex Workspaces

Independent Codex accounts on a shared Linux host: SSH key routing, separate CODEX_HOME directories, file credentials and optional per-profile background proxies. Sharing a Unix user is a usage convention, not a security boundary.

Requires Linux, Python 3.11+, Bash and Codex CLI. Mihomo and tmux are optional. Install with `pip install .`, run `cws init`, edit the private configuration, then `cws run alice --`. Both CLI and App Server ensure personal network services are started. `cws start alice` starts only background services; `cws status all` reports readiness.

Version 0.2 removes tmux from the network lifecycle. Supervisors survive terminal disconnection, restart children, rotate logs and stop only verified owned processes. No boot service is installed. Before upgrading a running 0.1 deployment, use the old version to stop its network windows; the new version refuses occupied ports.

For optional manually created tmux sessions, `cws tmux-config` prints mouse and SSH identity propagation settings. Inherited SSH identity takes priority; old panes fall back only to their own session environment. Never share one session across accounts. Legacy explicit profile attach commands remain available.

See [Chinese usage guide](README.md) and [architecture](docs/architecture.md). Private configuration, keys, credentials and runtime data stay outside this checkout. Tests never make model requests unless explicitly opted in. MIT licensed.

## Platform-managed SSH keys

The current open-source installer uses per-key `CWS_SSH_PROFILE` options; platform rewrites of `authorized_keys` can remove them. A separate deployment can resolve the authenticated key through OpenSSH `ExposeAuthInfo` / `SSH_USER_AUTH`, but **this package does not implement or install that method**. See [platform SSH identity guidance](docs/platform-ssh-identity.md).
