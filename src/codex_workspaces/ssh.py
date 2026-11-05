"""SSH key to profile binding with explicit plans, ownership and rollback."""
import base64
import fnmatch
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time

from .util import atomic, launcher, lock, json_write

SELECTOR = "CWS_SSH_PROFILE"


def public_key(path):
    value = Path(path).expanduser().read_text().strip().split()
    if len(value) < 2 or not re.fullmatch(r"ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)", value[0]):
        raise ValueError("Provide an OpenSSH public key file, never a private key")
    raw = base64.b64decode(value[1], validate=True)
    length = int.from_bytes(raw[:4], "big")
    if raw[4:4 + length].decode() != value[0] or len(raw) < 32:
        raise ValueError("Malformed public key")
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
    return " ".join(value[:2]), fingerprint


def authorized_text(config, original):
    bindings = {}
    for profile in config.profiles.values():
        if profile.ssh.get("public_key_file"):
            key, fingerprint = public_key(profile.ssh["public_key_file"])
            if key in bindings:
                raise ValueError("Two profiles cannot share an SSH key")
            bindings[key] = (profile.name, fingerprint)
    if not bindings:
        raise ValueError("No public_key_file configured")
    kept, prefixes = [], {key: set() for key in bindings}
    for line in original.splitlines():
        matches = [(key, re.search(r"(?<!\S)" + re.escape(key.split()[0]) + r"\s+" + re.escape(key.split()[1]) + r"(?=\s|$)", line)) for key in bindings]
        found = next(((key, value) for key, value in matches if value), None)
        if found is None:
            kept.append(line); continue
        match, position = found
        prefix = line[:position.start()].strip()
        expected = 'environment="' + SELECTOR + "=" + bindings[match][0] + '"'
        prefix = prefix.replace(expected, "").strip(",")
        if "command=" in prefix or SELECTOR in prefix:
            raise ValueError("Existing key options conflict; manual review required")
        prefixes[match].add(prefix)
    for key, (name, _) in bindings.items():
        if len(prefixes[key]) > 1:
            raise ValueError("Conflicting duplicate key options")
        prefix = next(iter(prefixes[key]), "")
        kept.append('environment="' + SELECTOR + "=" + name + '"' + ("," + prefix if prefix else "") + " " + key + " cws-" + name)
    return "\n".join(kept) + "\n", {name: fingerprint for name, fingerprint in bindings.values()}


def install(config, authorized_keys, dispatcher, apply=False, sshd_bin=None, sshd_config=None):
    auth = Path(authorized_keys).expanduser()
    dest = Path(dispatcher).expanduser()
    if auth.is_symlink() or dest.is_symlink():
        raise ValueError("Refusing to replace a symlink; choose the intended real file explicitly")
    original = auth.read_text() if auth.exists() else ""
    desired, fingerprints = authorized_text(config, original)
    shared_env = auth.parent / "environment"
    if shared_env.exists() and any(line.strip().startswith(SELECTOR + "=") for line in shared_env.read_text().splitlines()):
        raise ValueError("Shared SSH environment overrides the per-key selector")
    command = [sshd_bin or shutil.which("sshd") or "sshd", "-T"] + (["-f", str(sshd_config)] if sshd_config else [])
    effective = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    policy = next((line.split(None, 1)[1] for line in effective.splitlines() if line.startswith("permituserenvironment ")), "no")
    if policy != "yes" and not any(fnmatch.fnmatchcase(SELECTOR, p) for p in policy.split(",") if p != "no"):
        raise ValueError("Enable PermitUserEnvironment for CWS_SSH_PROFILE before installing; no daemon reload is performed here")
    marker = "# CWS-DISPATCHER " + config.identity + "\n"
    content = "#!/bin/sh\n" + marker + launcher(config, ["run", "@ssh", "--"]).split("\n", 1)[1]
    old_dest = dest.read_bytes() if dest.exists() else None
    if old_dest is not None and marker.encode() not in old_dest:
        raise ValueError("Dispatcher path belongs to another installation")
    result = {"applied": False, "identities": fingerprints, "authorized_keys": str(auth), "dispatcher": str(dest), "sshd_restarted": False}
    if not apply:
        return result
    with lock(config.identity + "-ssh-install"):
        if (auth.read_text() if auth.exists() else "") != original:
            raise RuntimeError("authorized_keys changed concurrently; retry")
        backup = config.state / "ssh-backups" / str(time.time_ns())
        backup.mkdir(parents=True, mode=0o700)
        atomic(backup / "authorized_keys", original)
        if old_dest is not None:
            atomic(backup / "dispatcher", old_dest)
        auth_mode = auth.stat().st_mode & 0o777 if auth.exists() else 0o600
        dest_mode = dest.stat().st_mode & 0o777 if dest.exists() else 0o755
        try:
            atomic(dest, content, 0o755)
            atomic(auth, desired, auth_mode)
        except BaseException:
            atomic(auth, original, auth_mode)
            if old_dest is None:
                dest.unlink(missing_ok=True)
            else:
                atomic(dest, old_dest, dest_mode)
            raise
        result.update(applied=True, backup=str(backup))
        json_write(backup / "operation.json", result)
    return result


def client_config(profile, host, port=22, user=None, identity="~/.ssh/id_ed25519"):
    for value in (host, user or "", identity):
        if any(c in value for c in ("\r", "\n", '"')):
            raise ValueError("Invalid SSH config value")
    if not 1 <= int(port) <= 65535:
        raise ValueError("Invalid SSH port")
    return ("Host codex-" + profile.name + "\n    HostName " + host + "\n    Port " + str(port) + "\n"
            + ("    User " + user + "\n" if user else "")
            + '    IdentityFile "' + identity + '"\n    IdentitiesOnly yes\n    ControlMaster no\n    ControlPath none\n')
