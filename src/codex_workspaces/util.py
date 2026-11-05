"""Small filesystem, process and locking primitives shared by adapters."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import tempfile


def atomic(path: Path, data: str | bytes, mode: int = 0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".cws-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data.encode() if isinstance(data, str) else data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def json_write(path, value):
    atomic(Path(path), json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def runtime_root():
    root = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / f"codex-workspaces-{os.getuid()}"
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != os.getuid():
        raise RuntimeError("Untrusted runtime lock directory")
    os.chmod(root, 0o700)
    return root


@contextmanager
def lock(name, blocking=True):
    with (runtime_root() / (name + ".lock")).open("a") as stream:
        os.chmod(stream.name, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


def listening(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def launcher(config, command):
    """Generated launchers remain usable after editable or wheel installation."""
    import sys
    package_parent = Path(__file__).resolve().parent.parent
    invocation = shlex.join([sys.executable, "-P", "-m", "codex_workspaces", "--config", str(config.path), *command])
    return ("#!/bin/sh\nset -eu\nexport PYTHONPATH=" + shlex.quote(str(package_parent))
            + ':"${PYTHONPATH:-}"\nexec ' + invocation + ' "$@"\n')


def stop_group(process, timeout=8):
    import signal
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def public_error(error):
    """Never include request URLs, private tokens, or arbitrary child stderr."""
    text = str(error)
    if any(item in text for item in ("http://", "https://", "Bearer ", "access_token", "refresh_token")):
        return type(error).__name__ + ": request failed; inspect the private log"
    if isinstance(error, subprocess.CalledProcessError):
        return "Child process failed; inspect private diagnostics"
    return text[:240]
