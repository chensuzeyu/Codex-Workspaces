"""Conservative source-release checks; never traverse deployment state by default."""
import ipaddress
from pathlib import Path
import re
import subprocess

RULES = {
    "private-key": re.compile(r"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----"),
    "api-key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{25,}\.[A-Za-z0-9_-]{25,}\.[A-Za-z0-9_-]{20,}"),
    "credential-url": re.compile(r"https?://[^\s\"']*(?:token|password|secret|apikey|api_key)=[^\s\"']+", re.I),
    "public-key": re.compile(r"\bssh-(?:ed25519|rsa)\s+[A-Za-z0-9+/=]{40,}"),
}
FORBIDDEN = {"auth.json", "authorized_keys", "ssh-public-keys.json", ".env"}
DEPLOYMENT_DIRS = {"runtime", "evidence", "private", ".private", "backups", ".storage", "__pycache__"}


def files(root, staged=False):
    root = Path(root).resolve()
    if staged:
        names = subprocess.check_output(["git", "-C", str(root), "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"]).decode().split("\0")
        return [root / name for name in names if name]
    return [p for p in root.rglob("*") if p.is_file() and not any(part in {".git", *DEPLOYMENT_DIRS} for part in p.relative_to(root).parts)]


def check(root, staged=False, deny=()):
    root = Path(root).resolve(); findings = []
    for path in files(root, staged):
        rel = path.relative_to(root)
        if path.is_symlink():
            findings.append({"file": str(rel), "rule": "symlink"}); continue
        if path.name in FORBIDDEN or any(part in DEPLOYMENT_DIRS for part in rel.parts):
            findings.append({"file": str(rel), "rule": "deployment-file"})
        if path.stat().st_size > 2 * 1024 * 1024:
            findings.append({"file": str(rel), "rule": "unexpected-large-file"}); continue
        raw = subprocess.check_output(["git", "-C", str(root), "show", ":" + rel.as_posix()]) if staged else path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        for name, pattern in RULES.items():
            if pattern.search(text):
                findings.append({"file": str(rel), "rule": name})
        for value in deny:
            if value and value in text:
                findings.append({"file": str(rel), "rule": "deployment-marker"})
        for address in re.findall(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", text):
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                continue
            if ip.is_loopback or ip.is_unspecified or any(ip in ipaddress.ip_network(net) for net in ["192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"]):
                continue
            findings.append({"file": str(rel), "rule": "non-example-ip"})
    return {"passed": not findings, "findings": findings}
