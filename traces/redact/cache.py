"""Content-addressed cache of redacted message blocks.

A block is one element of a record's ``messages`` list. Its key is
``sha256(rules fingerprint + block JSON)``, so an unchanged message is redacted
once and reused by every later run, and a session that grew only pays for its
new messages. The rules fingerprint covers the redaction source code, the rule
context (user, home, private terms and domains, URL allowlist), and the
placeholder key, so changing any of them starts a fresh cache file instead of
reusing output produced under different rules.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import zlib
from collections import Counter
from pathlib import Path
from typing import Any

import traces
from traces.redact.rules import RuleContext

_FLUSH_BYTES = 64 << 20
_BATCH = 500


def rules_version(user_name: str | None, private_terms: list[str],
                  private_domains: list[str], allowed_domains: set[str] | list[str]) -> str:
    """Hash of everything that decides redaction output except the home dir:
    rule and schema source, tool version, user, private terms and domains, URL
    allowlist. Published in MANIFEST so a later run knows whether published rows
    were redacted under the current rules."""
    h = hashlib.sha256()
    h.update(traces.__version__.encode())
    package = Path(__file__).parent
    for source in [*sorted((package / "rules").rglob("*.py")), package / "schema.py"]:
        h.update(source.relative_to(package).as_posix().encode())
        h.update(source.read_bytes())
    h.update(json.dumps([user_name, sorted(private_terms), sorted(private_domains),
                         sorted(allowed_domains)]).encode())
    return h.hexdigest()


def rules_fingerprint(ctx: RuleContext, key: bytes) -> str:
    """Cache namespace: ``rules_version`` plus home dir and placeholder key."""
    h = hashlib.sha256()
    h.update(rules_version(ctx.user_name, ctx.private_terms, ctx.private_domains,
                           ctx.allowed_domains).encode())
    h.update(ctx.home_dir.encode())
    h.update(hmac.new(key, b"traces-cache-fingerprint", hashlib.sha256).digest())
    return h.hexdigest()


_SCHEMA = "CREATE TABLE IF NOT EXISTS blocks (k BLOB PRIMARY KEY, v BLOB NOT NULL) WITHOUT ROWID"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=600, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(_SCHEMA)
    return conn


class RedactionCache:
    """Reads from the main cache file; writes go to it directly, or, for a
    worker process (``shard`` set), to a private shard file that the parent
    merges with ``absorb_shards`` after the pool exits. Many processes writing
    one SQLite file while others read starves WAL checkpoints and the WAL
    grows without bound, so workers never write the main file."""

    def __init__(self, cache_dir: Path, fingerprint: str, shard: str | None = None) -> None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.path = cache_dir / f"redact-{fingerprint[:16]}.sqlite"
        self._shard_dir = cache_dir / f".shards-{fingerprint[:16]}"
        self._salt = bytes.fromhex(fingerprint)
        self._conn = _connect(self.path)
        self._write = self._conn
        if shard is not None:
            self._shard_dir.mkdir(exist_ok=True)
            self._write = _connect(self._shard_dir / f"{shard}.sqlite")
        self._pending: list[tuple[bytes, bytes]] = []
        self._pending_bytes = 0
        self.hits = 0
        self.misses = 0

    def key(self, block: bytes) -> bytes:
        return hashlib.sha256(self._salt + block).digest()

    def get_many(self, keys: list[bytes]) -> dict[bytes, tuple[Any, dict[str, int]]]:
        found: dict[bytes, tuple[Any, dict[str, int]]] = {}
        unique = list(dict.fromkeys(keys))
        for i in range(0, len(unique), _BATCH):
            batch = unique[i:i + _BATCH]
            marks = ",".join("?" * len(batch))
            for k, v in self._conn.execute(f"SELECT k, v FROM blocks WHERE k IN ({marks})", batch):
                value, counts = json.loads(zlib.decompress(v))
                found[k] = (value, counts)
        return found

    def put(self, key: bytes, value: Any, counts: Counter) -> None:
        blob = zlib.compress(json.dumps([value, dict(counts)], ensure_ascii=False).encode(), 1)
        self._pending.append((key, blob))
        self._pending_bytes += len(blob)
        if self._pending_bytes >= _FLUSH_BYTES:
            self.flush()

    def flush(self) -> None:
        if not self._pending:
            return
        self._write.execute("BEGIN IMMEDIATE")
        self._write.executemany("INSERT OR IGNORE INTO blocks (k, v) VALUES (?, ?)", self._pending)
        self._write.execute("COMMIT")
        self._pending.clear()
        self._pending_bytes = 0

    def absorb_shards(self) -> int:
        """Merge worker shard files into the main file and delete them."""
        merged = 0
        if not self._shard_dir.is_dir():
            return merged
        for shard in sorted(self._shard_dir.glob("*.sqlite")):
            self._conn.execute("ATTACH DATABASE ? AS shard", (str(shard),))
            self._conn.execute("BEGIN IMMEDIATE")
            cur = self._conn.execute("INSERT OR IGNORE INTO blocks SELECT k, v FROM shard.blocks")
            merged += max(cur.rowcount, 0)
            self._conn.execute("COMMIT")
            self._conn.execute("DETACH DATABASE shard")
        for leftover in self._shard_dir.iterdir():
            leftover.unlink()
        self._shard_dir.rmdir()
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return merged

    def close(self) -> None:
        self.flush()
        if self._write is not self._conn:
            self._write.close()
        self._conn.close()
