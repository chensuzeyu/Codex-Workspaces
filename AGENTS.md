# Development boundaries

- Public source contains no real accounts, hosts, keys, subscriptions, credentials or runtime evidence.
- Keep private configuration and all runtime state outside the source checkout.
- Use explicit profile names and owned tmux sockets. Never adopt or kill an unowned server.
- Tests use temporary configurations, sockets and loopback fake nodes. Real model probes are explicit opt-in operations.
- Do not alter an existing deployment while developing this package.
- Run `python -m unittest discover -s tests -v` and the release audit before committing.
- Commit messages use a title, a blank line, and a bullet-point body.
