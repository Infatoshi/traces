"""Auto-discovery of private identity terms."""
from __future__ import annotations

import os
import re
import socket
import subprocess
from pathlib import Path

from traces.redact.rules.constants import PRIVATE_TERM_DENYLIST


def run_text(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def add_private_term(terms: set[str], value: str) -> None:
    clean = value.strip().strip("-_.")
    if len(clean) >= 3 and clean.lower() not in PRIVATE_TERM_DENYLIST and clean.lower() != "root":
        terms.add(clean)


def discover_private_terms(root: Path | None, user_name: str, home_dir: Path) -> list[str]:
    terms: set[str] = set()
    for value in {user_name, home_dir.name, os.environ.get("USER", ""), os.environ.get("LOGNAME", "")}:
        if value:
            add_private_term(terms, value)
    hostname = socket.gethostname().split(".", 1)[0]
    if hostname and hostname not in {"localhost"}:
        add_private_term(terms, hostname)
    git_name = run_text(["git", "config", "--global", "user.name"])
    if git_name:
        add_private_term(terms, git_name)
        name_parts = [p for p in re.split(r"[^A-Za-z0-9]+", git_name) if len(p) >= 3]
        for p in name_parts:
            add_private_term(terms, p)
        if len(name_parts) >= 2:
            add_private_term(terms, "".join(name_parts))
    git_email = run_text(["git", "config", "--global", "user.email"])
    if git_email:
        local, _, domain = git_email.partition("@")
        add_private_term(terms, local)
        if domain and any(t.lower() in domain.lower() for t in terms if len(t) >= 4):
            add_private_term(terms, domain)
    # Pi encoded session path probing
    if root is not None:
        pi_sessions = root / ".pi" / "agent" / "sessions"
        if pi_sessions.exists():
            for child in pi_sessions.iterdir():
                if not child.is_dir():
                    continue
                for token in re.split(r"[^A-Za-z0-9]+", child.name.strip("-")):
                    if len(token) >= 4:
                        add_private_term(terms, token)
        ssh_config = root / ".ssh" / "config"
        if ssh_config.exists():
            for line in ssh_config.read_text(encoding="utf-8", errors="ignore").splitlines():
                stripped = line.strip()
                if not stripped.lower().startswith("host "):
                    continue
                for alias in stripped.split()[1:]:
                    if "*" not in alias and "?" not in alias:
                        add_private_term(terms, alias)
    return sorted(terms, key=lambda item: (-len(item), item.lower()))
