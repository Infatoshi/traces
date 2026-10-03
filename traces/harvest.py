"""Collect literal secret values present on this machine, for exact-value scrubbing.

Standalone (stdlib only) so it can run on any host with ``ssh host python3 - < harvest.py``.
Writes ``LABEL<TAB>VALUE`` lines to stdout and a count to stderr. Never print the
output to a terminal or log; pipe it straight into a 0600 file.

Sources: ``~/.env_vars``-style files (every value), ``.env`` files under the
given project roots (secret-named keys only), and CLI credential files (JSON or
``key: value`` with secret-named keys).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

SECRETY = re.compile(
    r"(?i)(key|token|secret|pass|cookie|auth|credential|session|refresh|access|bearer|private|webhook|dsn)"
)
ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$")
CRED_NAMES = re.compile(
    r"(?i)(auth|token|credential|secret|hosts\.yml|oauth|session|cookie|keys?\.json|config\.json|\.env$)"
)
SKIP_DIRS = {
    "projects", "sessions", "node_modules", "Cache", "cache", "plugins", "skills",
    "file-history", "todos", "shell-snapshots", "debug", "tmp", "logs", "storage", "versions",
}
DEFAULTS = {
    "all_values": ["~/.env_vars"],
    "env_roots": ["~/dev"],
    "cred_paths": [
        "~/.config", "~/.codex", "~/.grok", "~/.kimi-code", "~/.kimi", "~/.omp", "~/.claude",
        "~/.cache/huggingface", "~/.gemini", "~/.local/share/opencode", "~/.docker", "~/.aws",
        "~/.netrc", "~/.git-credentials", "~/.npmrc", "~/.pypirc",
    ],
}


def plausible(v: str) -> bool:
    v = v.strip()
    if len(v) < 16 or len(v) > 4096 or " " in v or "\n" in v:
        return False
    if v.startswith(("/", "~", "$", "{", "http://", "https://")) and "@" not in v:
        return False
    mixed = any(c.isupper() for c in v) and any(c.islower() for c in v)
    return len(set(v)) >= 10 and (any(c.isdigit() for c in v) or (mixed and len(v) >= 20))


class Harvest:
    def __init__(self, host: str) -> None:
        self.host = host
        self.values: dict[str, str] = {}

    def add(self, label: str, value: str) -> None:
        value = value.strip().strip("'\"")
        if plausible(value):
            self.values.setdefault(value, f"{self.host}:{label}")

    def env_file(self, path: Path, secret_named_only: bool) -> None:
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            return
        for line in text.splitlines():
            m = ASSIGN.match(line)
            if m and (not secret_named_only or SECRETY.search(m.group(1))):
                self.add(f"{path.name}:{m.group(1)}", m.group(2).split(" #")[0])

    def _walk_json(self, obj, path: str, src: str) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                self._walk_json(v, f"{path}.{k}", src)
        elif isinstance(obj, list):
            for v in obj:
                self._walk_json(v, path, src)
        elif isinstance(obj, str) and SECRETY.search(path):
            self.add(f"{src}{path}", obj)

    def cred_file(self, path: Path) -> None:
        try:
            if path.stat().st_size > 2_000_000:
                return
            text = path.read_text(errors="ignore")
        except OSError:
            return
        try:
            self._walk_json(json.loads(text), "", path.name)
            return
        except ValueError:
            pass
        for line in text.splitlines():
            m = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*[:=]\s*(.+?)\s*$", line)
            if m and SECRETY.search(m.group(1)):
                self.add(f"{path.name}:{m.group(1)}", m.group(2))
        if path.name == "token":
            self.add(path.parent.name + "/token", text)

    def run(self, all_values: list[str], env_roots: list[str], cred_paths: list[str]) -> None:
        for f in all_values:
            self.env_file(Path(f).expanduser(), secret_named_only=False)
        for root in env_roots:
            base = Path(root).expanduser()
            for dirpath, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", ".venv")
                           and len(Path(dirpath).relative_to(base).parts) < 5]
                for f in files:
                    if f.startswith(".env"):
                        self.env_file(Path(dirpath) / f, secret_named_only=True)
        for entry in cred_paths:
            base = Path(entry).expanduser()
            if base.is_file():
                self.cred_file(base)
                continue
            for dirpath, dirs, files in os.walk(base):
                depth = len(Path(dirpath).relative_to(base).parts)
                dirs[:] = [d for d in dirs if depth < 3 and d not in SKIP_DIRS]
                for f in files:
                    if CRED_NAMES.search(f):
                        self.cred_file(Path(dirpath) / f)


def main() -> None:
    opts = dict(DEFAULTS)
    if len(sys.argv) > 1:
        opts.update(json.loads(sys.argv[1]))
    harvest = Harvest(os.uname().nodename.split(".")[0])
    harvest.run(opts["all_values"], opts["env_roots"], opts["cred_paths"])
    for value, label in harvest.values.items():
        sys.stdout.write(f"{label}\t{value}\n")
    print(f"{harvest.host}: {len(harvest.values)} values", file=sys.stderr)


if __name__ == "__main__":
    main()
