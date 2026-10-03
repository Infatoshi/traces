"""Machine config shared by every command.

Search order (first hit wins): ``--config PATH``, ``traces.local.toml`` in the
repo root (gitignored), ``~/.config/traces/config.toml``. CLI flags override
config values. See ``traces.example.toml`` for every key.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

REPO_CONFIG = Path(__file__).resolve().parent.parent / "traces.local.toml"
USER_CONFIG = Path("~/.config/traces/config.toml").expanduser()


def config_path(explicit: str | None = None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    for candidate in (REPO_CONFIG, USER_CONFIG):
        if candidate.is_file():
            return candidate
    return None


def load(explicit: str | None = None) -> dict[str, Any]:
    path = config_path(explicit)
    if path is None:
        return {}
    with path.open("rb") as f:
        data = tomllib.load(f)
    data["_path"] = str(path)
    return data


def get(settings: dict[str, Any], dotted: str, default: Any = None) -> Any:
    node: Any = settings
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def metadata_key(settings: dict[str, Any]) -> str | None:
    """``TRACES_METADATA_KEY`` wins, then ``identity.metadata_key_file``."""
    if os.environ.get("TRACES_METADATA_KEY"):
        return os.environ["TRACES_METADATA_KEY"]
    key_file = get(settings, "identity.metadata_key_file")
    if key_file:
        path = Path(key_file).expanduser()
        if path.is_file():
            return path.read_text().strip() or None
    return None


def private_terms(settings: dict[str, Any]) -> list[str]:
    terms = list(get(settings, "identity.private_terms", []) or [])
    terms_file = get(settings, "identity.private_terms_file")
    if terms_file:
        path = Path(terms_file).expanduser()
        if path.is_file():
            terms += [t.strip() for t in path.read_text().splitlines() if t.strip()]
    return list(dict.fromkeys(terms))


def jobs(settings: dict[str, Any], flag: int | None) -> int:
    from traces.parallel import default_jobs

    return flag or int(get(settings, "run.jobs", 0) or 0) or default_jobs()


def cache_dir(settings: dict[str, Any], flag: str | None) -> str | None:
    if flag:
        return None if flag == "none" else flag
    value = get(settings, "run.cache_dir")
    return str(Path(value).expanduser()) if value else None
