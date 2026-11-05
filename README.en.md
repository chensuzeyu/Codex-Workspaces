# Codex Workspaces

Manage multiple Codex CLI accounts on one Linux host with independent `CODEX_HOME` directories, named tmux servers, optional loopback Mihomo proxies, health-based selection, and SSH desktop entry points.

Install in a virtual environment with `python -m pip install .`, run `codex-workspace init`, edit the private configuration in `~/.config/codex-workspaces/config.json`, and use `codex-workspace attach alice`. See [README.md](README.md) for configuration, proxy setup, SSH, migration, probes and release checks.

Private configuration, credentials, public keys, subscriptions, logs and runtime state belong outside the source checkout. Profiles under the same Linux account are operationally separated but are not an OS security boundary.
