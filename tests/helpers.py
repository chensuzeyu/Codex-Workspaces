import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

from codex_workspaces.config import load
from codex_workspaces.util import json_write


class PrivateFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cws-tests-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "private/config.json"
        self.fake = self.root / "fake-codex"
        self.fake.write_bytes((Path(__file__).parent / "fixtures/fake_codex.py").read_bytes())
        self.fake.chmod(0o755)
        self.data = {"version": 1, "namespace": "t" + uuid.uuid4().hex[:10], "state_dir": str(self.root / "state"),
                     "history_limit": 50000, "profiles": {name: {"codex_home": str(self.root / name / "codex"),
                     "cwd": str(self.root), "codex_bin": str(self.fake), "proxy": {"enabled": False}} for name in ("alice", "bob")}}
        self.save()

    def save(self):
        json_write(self.path, self.data)
        self.config = load(self.path)
        return self.config
