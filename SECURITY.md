# Security boundaries

Profiles sharing one Linux user do not provide permission isolation. Treat private configuration, account credentials, proxy subscriptions, public-key mappings and logs as deployment data. Use separate Linux accounts where access control is required.

The SSH installer requires explicit target paths and an explicit `--apply`. Review its plan and backups. It does not alter sshd configuration or restart sshd. The release audit detects common patterns, but cannot prove that every sensitive value has been removed.
