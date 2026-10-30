"""``traces sync``: the whole multi-host run from one config.

Control side (``sync``), run on the machine holding the config:
  1. ship this repo and the config to ``sync.work_host:sync.work_dir``
  2. stage each host's agent stores there with filtered rsync (only the files
     extractors read); the work host's own home is read in place
  3. harvest literal secret values on every host straight into a 0700 dir on
     the work host (tmpfs when available); values never touch this machine
  4. run ``traces sync-local`` on the work host, then delete the secrets dir

Work side (``sync_local``): extract -> cached parallel redact -> combine ->
export with the stable metadata key -> pull the published dataset -> merge by
trace_id -> re-redact merged rows (content only, cached) -> scrub harvested
values -> optional gitleaks scrub -> clean -> MANIFEST + card -> optional publish.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from traces import settings

REPO = Path(__file__).resolve().parent.parent
HARVEST = Path(__file__).resolve().parent / "harvest.py"

# (path under a home, rsync include globs or None for the whole dir)
STORE_FILTERS: list[tuple[str, list[str] | None]] = [
    (".claude/projects", ["*.jsonl"]),
    (".codex/sessions", ["rollout-*.jsonl", "rollout-*.jsonl.zst"]),
    (".grok/sessions", ["chat_history.jsonl", "summary.json", "updates.jsonl", "compaction_checkpoints/*.json"]),
    (".gemini/tmp", ["session-*.json"]),
    (".omp/agent/sessions", ["*.jsonl"]),
    (".kimi-code", None),
    (".local/share/opencode", None),
    ("Library/Application Support/Cursor/User", ["state.vscdb", "state.vscdb-wal", "workspace.json"]),
]
REPO_EXCLUDES = [".git", ".venv", ".pytest_cache", ".ruff_cache", "*.egg-info", "out", "dist",
                 "__pycache__", "traces.local.toml", "MERGED_*.md"]


PLACEHOLDER_FORMAT = "hmac10"


def log(msg: str) -> None:
    print(f"[{datetime.now().astimezone().strftime('%H:%M:%S')}] {msg}", flush=True)


class Shell:
    """Run commands on one host over a single multiplexed SSH connection."""

    def __init__(self, host: str, control_dir: Path) -> None:
        self.host = host
        self.ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ControlMaster=auto",
                    "-o", f"ControlPath={control_dir}/%C", "-o", "ControlPersist=30m"]

    @property
    def local(self) -> bool:
        return not self.host

    def argv(self, cmd: str) -> list[str]:
        return ["bash", "-c", cmd] if self.local else [*self.ssh, self.host, f"bash -c {shlex.quote(cmd)}"]

    def run(self, cmd: str, check: bool = True, capture: bool = False, stdin=None) -> str:
        proc = subprocess.run(self.argv(cmd), check=check, text=True, stdin=stdin,
                              stdout=subprocess.PIPE if capture else None)
        return proc.stdout.strip() if capture else ""

    def rsh(self) -> str:
        return " ".join(shlex.quote(a) for a in self.ssh)

    def target(self, path: str) -> str:
        return path if self.local else f"{self.host}:{path}"


def _filters(includes: list[str] | None) -> list[str]:
    if includes is None:
        return []
    return ["--include=*/", *[f"--include={g}" for g in includes], "--exclude=*", "--prune-empty-dirs"]


def _stage(host: dict, root: str, dest: str, work: Shell, control: Path) -> None:
    """Copy the extractor-visible part of ``root`` on ``host`` to ``dest`` on the work host."""
    src_host = host.get("ssh", "")
    for rel, includes in STORE_FILTERS:
        args = ["rsync", "-a", "--mkpath", *_filters(includes)]
        src, dst = f"{root}/{rel}/", f"{dest}/{rel}/"
        if src_host == work.host:
            cmd = " ".join(shlex.quote(a) for a in [*args, src, dst])
            work.run(f"[ -d {shlex.quote(src)} ] && {cmd} || true", check=False)
        elif not src_host:
            if Path(src).is_dir():
                subprocess.run([*args, "-e", work.rsh(), src, work.target(dst)], check=False)
        else:
            # work host pulls from the source host
            cmd = " ".join(shlex.quote(a) for a in [*args, f"{src_host}:{src}", dst])
            work.run(cmd + " 2>/dev/null || true", check=False)


def sync(cfg: dict[str, Any], publish: bool, merge: bool = True) -> None:
    work_host = settings.get(cfg, "sync.work_host", "")
    wdir = settings.get(cfg, "sync.work_dir")
    uv = settings.get(cfg, "sync.uv", "uv")
    hosts = settings.get(cfg, "hosts", [])
    if not wdir or not hosts:
        raise SystemExit("config needs sync.work_dir and at least one [[hosts]] entry")
    # Short dir: the control socket path must fit in 104 bytes (macOS $TMPDIR is too long).
    control = Path(tempfile.mkdtemp(prefix="trs-", dir="/tmp" if Path("/tmp").is_dir() else None))
    work = Shell(work_host, control)
    try:
        log(f"work host {work_host or 'local'}:{wdir}")
        work.run(f"mkdir -p {wdir}/stores && chmod 700 {wdir}")
        subprocess.run(["rsync", "-a", "--delete", *[f"--exclude={e}" for e in REPO_EXCLUDES],
                        "-e", work.rsh(), f"{REPO}/", work.target(f"{wdir}/repo/")], check=True)
        with open(cfg["_path"], "rb") as f:
            work.run(f"umask 077; cat > {wdir}/config.toml", stdin=f)

        stores = []
        for host in hosts:
            roots = [host["home"], *host.get("roots", [])]
            for i, root in enumerate(roots):
                label = host["name"] if i == 0 else f"{host['name']}-{i}"
                if host.get("ssh", "") == work_host and i == 0:
                    stores.append({"label": label, "root": root, "home": host["home"]})
                    continue
                dest = f"{wdir}/stores/{label}"
                log(f"stage {label}")
                _stage(host, root, dest, work, control)
                stores.append({"label": label, "root": dest, "home": host["home"]})
        with tempfile.TemporaryFile("w+") as f:
            json.dump(stores, f)
            f.seek(0)
            work.run(f"cat > {wdir}/stores.json", stdin=f)

        secrets_dir = work.run(
            f"d=/dev/shm; [ -d $d ] || d={wdir}; umask 077; mktemp -d $d/traces-secrets-XXXXXX",
            capture=True,
        )
        try:
            opts = json.dumps({k: v for k, v in (settings.get(cfg, "secrets", {}) or {}).items()
                               if k in ("all_values", "env_roots", "cred_paths")})
            for host in hosts:
                log(f"harvest {host['name']}")
                out = f"{secrets_dir}/{host['name']}.tsv"
                src = Shell(host.get("ssh", ""), control)
                with HARVEST.open("rb") as script:
                    if src.host == work.host:
                        work.run(f"umask 077; python3 - {shlex.quote(opts)} > {out}", stdin=script)
                        continue
                    producer = subprocess.Popen(src.argv(f"python3 - {shlex.quote(opts)}"),
                                                stdin=script, stdout=subprocess.PIPE)
                    work.run(f"umask 077; cat > {out}", stdin=producer.stdout)
                    producer.wait()
            flag = (" --publish" if publish else "") + ("" if merge else " --no-merge")
            env_file = settings.get(cfg, "sync.env_file")
            env = f"set -a; . {env_file} >/dev/null 2>&1; set +a; " if env_file else ""
            work.run(f"{env}cd {wdir}/repo && {uv} run --extra publish traces --config ../config.toml "
                     f"sync-local --work {wdir} --secrets {secrets_dir}{flag}")
        finally:
            work.run(f"rm -rf {shlex.quote(secrets_dir)}", check=False)
    finally:
        if work_host:
            subprocess.run([*work.ssh, "-O", "exit", work_host], capture_output=True)
        shutil.rmtree(control, ignore_errors=True)


# ---------------------------------------------------------------- work side


def _export_variant(task: tuple[str, str, str, str | None]) -> dict:
    variant, src, out, key = task
    from traces.config import ExportConfig
    from traces.export.execution import export_assistant_steps, export_execution
    from traces.export.messages import export_messages
    from traces.export.sharegpt import export_sharegpt

    config = ExportConfig(variant="all", dedup=True, metadata_key=key)
    s, o = Path(src), Path(out)
    if variant == "messages":
        return {"messages": export_messages(s, o / "messages.jsonl", config)}
    if variant == "sharegpt":
        return {"sharegpt": export_sharegpt(s, o / "sharegpt.jsonl", config)}
    return {
        "execution": export_execution(s, o / "execution.jsonl", config),
        "steps": export_assistant_steps(s, o / "assistant_steps.jsonl", config),
    }


def _gitleaks_shard(task: tuple[str, str, str]) -> list[dict]:
    binary, shard_dir, report = task
    # One core per process: the pool already fans out across cores.
    subprocess.run([binary, "dir", shard_dir, "--redact=0", "--no-banner", "--log-level", "error",
                    "--report-format", "json", "--report-path", report], check=False,
                   env={**os.environ, "GOMAXPROCS": "1"})
    path = Path(report)
    findings = json.loads(path.read_text() or "[]") if path.exists() else []
    path.unlink(missing_ok=True)
    return findings


def _gitleaks_values(binary: str, tree: Path, work: Path,
                     jobs: int = 1) -> tuple[list[tuple[str, str]], Counter, set[str]]:
    """Run gitleaks over ``tree`` as ``jobs`` processes on 100 MB line-aligned shards.

    Returns literal values to scrub, finding counts by rule, and the trace IDs of
    findings gitleaks made inside decoded (base64/percent) content, which cannot be
    replaced in place and must be dropped.
    """
    from traces.merge import tail_metadata
    from traces.parallel import iter_range, line_ranges

    root = work / "gitleaks-shards"
    shutil.rmtree(root, ignore_errors=True)
    dirs = [root / f"{i:03d}" for i in range(max(1, jobs))]
    for d in dirs:
        d.mkdir(parents=True)
    n = 0
    for path in sorted(tree.glob("*.jsonl")):
        for i, (start, end) in enumerate(line_ranges(path, 100_000_000)):
            with (dirs[n % len(dirs)] / f"{path.stem}.{i:05d}.jsonl").open("wb") as out:
                for line in iter_range(path, start, end):
                    out.write(line)
            n += 1
    tasks = [(binary, str(d), str(root / f"report-{d.name}.json")) for d in dirs]
    findings: list[dict] = []
    with ProcessPoolExecutor(len(tasks)) as pool:
        for part in pool.map(_gitleaks_shard, tasks):
            findings += part
    drop: set[str] = set()
    for f in findings:
        if any(str(tag).startswith("decoded:") for tag in f.get("Tags") or []):
            shard, line_no = Path(f.get("File", "")), int(f.get("StartLine") or 0)
            with shard.open("rb") as fh:
                for k, line in enumerate(fh, 1):
                    if k == line_no:
                        drop.add(tail_metadata(line)["trace_id"])
                        break
    shutil.rmtree(root)
    rules = Counter(f.get("RuleID", "unknown") for f in findings)
    values = {f["Secret"]: f.get("RuleID", "unknown") for f in findings
              if f.get("Secret") and "\n" not in f["Secret"] and "\t" not in f["Secret"]}
    return [(f"gitleaks:{rule}", value) for value, rule in values.items()], rules, drop


def _ids_range(task: tuple[str, int, int, str]) -> list[str]:
    from traces.export.metadata import trace_id

    path, start, end, key = task
    from traces.parallel import iter_range

    return [trace_id(json.loads(line), key) for line in iter_range(Path(path), start, end)]


def _store_ids(redacted: Path, key: str, jobs: int) -> dict[str, set[str]]:
    """source file stem -> trace IDs of the records a store produced."""
    from traces.parallel import line_ranges

    out: dict[str, set[str]] = {}
    tasks = [(str(p), s, e, key) for p in sorted(redacted.glob("*.jsonl")) for s, e in line_ranges(p)]
    if not tasks:
        return out
    with ProcessPoolExecutor(max(1, min(jobs, len(tasks)))) as pool:
        for (path, *_rest), ids in zip(tasks, pool.map(_ids_range, tasks)):
            out.setdefault(Path(path).stem, set()).update(ids)
    return out


def _manifest(run: Path, final: Path, stores: list[dict], extract_counts: dict, extra: dict) -> dict:
    from traces import __version__
    from traces.export.metadata import SCHEMA_VERSION
    from traces.merge import iter_rows

    files, by_source, pairs_by_source, targets = {}, Counter(), Counter(), 0
    for name in ("messages.jsonl", "sharegpt.jsonl", "execution.jsonl", "assistant_steps.jsonl"):
        path = final / name
        h, rows = hashlib.sha256(), 0
        with path.open("rb") as f:
            while chunk := f.read(1 << 24):
                h.update(chunk)
        for line, md in iter_rows(path):
            rows += 1
            if name == "messages.jsonl":
                by_source[md.get("source")] += 1
            elif name == "sharegpt.jsonl":
                pairs_by_source[md.get("source")] += 1
            elif name == "assistant_steps.jsonl":
                targets += len(json.loads(line).get("assistant_steps") or [])
        files[name] = {"sha256": h.hexdigest(), "bytes": path.stat().st_size, "rows": rows}
    sources: dict[str, dict] = {}
    for counts in extract_counts.values():
        for src, n in counts.items():
            sources.setdefault(src, {"raw_records": 0})["raw_records"] += n
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tool_version": __version__,
        "metadata_schema_version": SCHEMA_VERSION,
        "config": {"hosts": [f"host-{i + 1}" for i in range(len(stores))], "variant": "all",
                   "dedup": True, "metadata_key_present": True, **extra},
        "sources": sources,
        "export_summary": {
            "messages_rows": files["messages.jsonl"]["rows"],
            "sharegpt_pairs": files["sharegpt.jsonl"]["rows"],
            "execution_rows": files["execution.jsonl"]["rows"],
            "assistant_step_rows": files["assistant_steps.jsonl"]["rows"],
            "assistant_targets": targets,
            "messages_by_source": dict(sorted(by_source.items())),
            "sharegpt_pairs_by_source": dict(sorted(pairs_by_source.items())),
        },
        "files": files,
    }


def restore_published(published: Path, final: Path, scratch: Path,
                      values: list[tuple[str, str]], leak_values: list[tuple[str, str]], jobs: int,
                      exclude: set[str] = frozenset()) -> dict:
    """Put back the published version of any trace the new run dropped.

    A longer new version wins the merge, but gitleaks or clean can then drop
    that row, which would delete a trace the dataset already had. The published
    version passed the same gates when it went out; it is scrubbed and cleaned
    again here and appended to ``final`` only if it still passes. ``exclude``
    holds published traces that gitleaks flagged this run (encoded findings
    cannot be scrubbed in place), which stay out.
    """
    from traces.merge import index_messages, iter_rows, keep_traces
    from traces.scrub import scrub_tree
    from traces.verify.scanner import clean_tree

    lost = set(index_messages(published)) - set(index_messages(final)) - exclude
    report: dict[str, Any] = {"lost": len(lost), "restored": 0, "by_source": {}}
    if not lost:
        return report
    picked, cleaned = scratch / "picked", scratch / "clean"
    keep_traces(published, lost, picked)
    if values:
        scrub_tree(picked, values, True, jobs)
        report["left_after_recheck"] = scrub_tree(picked, values, False, jobs)["occurrences"]
    if leak_values:
        scrub_tree(picked, leak_values, True, jobs)
    report["clean"] = clean_tree(picked, cleaned, jobs=jobs)
    by_source: Counter[str] = Counter()
    for part in sorted(cleaned.glob("*.jsonl")):
        with (final / part.name).open("ab") as target:
            for line, md in iter_rows(part):
                target.write(line if line.endswith(b"\n") else line + b"\n")
                if part.name == "messages.jsonl":
                    by_source[md.get("source") or "unknown"] += 1
    report["restored"] = sum(by_source.values())
    report["by_source"] = dict(sorted(by_source.items()))
    shutil.rmtree(scratch)
    return report


def sync_local(cfg: dict[str, Any], work: Path, secrets_dir: Path | None, publish: bool,
               merge: bool = True) -> dict:
    from traces.config import RedactorConfig
    from traces.extract.discovery import run_extract
    from traces.merge import drop_traces, merge_exports, pull_published
    from traces.redact.cache import rules_version
    from traces.redact.pipeline import run_redact
    from traces.scrub import load_values, scrub_tree
    from traces.tokens import token_stats
    from traces.verify.scanner import clean_tree, scan

    key = settings.metadata_key(cfg)
    if not key:
        raise SystemExit("sync needs a stable metadata key (identity.metadata_key_file)")
    jobs = settings.jobs(cfg, None)
    cache = settings.cache_dir(cfg, None)
    user = settings.get(cfg, "identity.user")
    terms = settings.private_terms(cfg)
    stores = json.loads((work / "stores.json").read_text())
    run = work / "run"
    shutil.rmtree(run, ignore_errors=True)
    combined = run / "combined"
    combined.mkdir(parents=True)
    extract_counts: dict[str, dict] = {}
    store_ids: dict[str, dict[str, set[str]]] = {}
    summary: dict[str, Any] = {}
    gitleaks = settings.get(cfg, "secrets.gitleaks")
    gitleaks = str(Path(gitleaks).expanduser()) if gitleaks else None
    if gitleaks and not shutil.which(gitleaks):
        gitleaks = None
    leak_values: list[tuple[str, str]] = []
    leak_drop: set[str] = set()
    published_leaks: set[str] = set()  # published rows gitleaks wants gone; never restored

    def gitleaks_pass(tree: Path, tag: str) -> None:
        log(f"gitleaks {tag}")
        found, rules, drop = _gitleaks_values(gitleaks, tree, run, jobs)
        leak_values.extend(found)
        leak_drop.update(drop)
        if tag == "published":
            published_leaks.update(drop)
        summary.setdefault("gitleaks", {})[tag] = {
            "findings_by_rule": dict(rules.most_common()), "unique_values": len(found),
            "encoded_traces": len(drop)}

    for store in stores:
        label, out = store["label"], run / store["label"]
        log(f"extract {label}")
        extract_counts[label] = run_extract(settings.get(cfg, "sync.sources") or None,
                                            Path(store["root"]).expanduser(), out)
        log(f"redact {label}")
        report = run_redact(out / "raw_extracted", out / "redacted", RedactorConfig(
            user_name=user, home_dir=store["home"], private_terms=terms,
            placeholder_key=key, cache_dir=cache), jobs=jobs)
        summary.setdefault("redact_cache", {})[label] = report.get("cache")
        store_ids[label] = _store_ids(out / "redacted", key, jobs)
        shutil.rmtree(out / "raw_extracted")
        for part in sorted((out / "redacted").glob("*.jsonl")):
            with (combined / part.name).open("ab") as target, part.open("rb") as source:
                shutil.copyfileobj(source, target, 1 << 24)
        shutil.rmtree(out)

    log("export")
    new = run / "new"
    with ProcessPoolExecutor(3) as pool:
        tasks = [(v, str(combined), str(new), key) for v in ("messages", "sharegpt", "steps")]
        for result in pool.map(_export_variant, tasks):
            summary.setdefault("export", {}).update(result)
    shutil.rmtree(combined)
    if gitleaks:
        gitleaks_pass(new, "fresh")

    repo = settings.get(cfg, "publish.repo")
    version = rules_version(user, terms, [], set())
    inputs = [new]
    if merge and repo and settings.get(cfg, "publish.merge_existing", True):
        log(f"pull {repo}")
        published = run / "published"
        summary["published"] = pull_published(repo, published)
        prior = {}
        if (published / "MANIFEST.json").exists():
            prior = json.loads((published / "MANIFEST.json").read_text())
        rules_match = settings.get(prior, "config.redaction_rules_version") == version
        if rules_match:
            summary["published"]["reredacted"] = False
        else:
            # Published rows predate the current rules: re-redact them once (content only).
            log("re-redact published rows under current rules")
            report = run_redact(published, run / "published-rr", RedactorConfig(
                user_name=user, home_dir=str(Path.home()), private_terms=terms,
                placeholder_key=key, export_rows=True), jobs=jobs)
            summary["published"]["reredacted"] = sum(report["counts"].values())
            shutil.rmtree(published)
            published = run / "published-rr"
        if settings.get(prior, "config.placeholder_format") != PLACEHOLDER_FORMAT:
            # One-time: legacy [CAT:NNNN:sha10] placeholders are guessable; key them.
            log("rekey legacy placeholders in published rows")
            from traces.rekey import rekey_tree
            summary["published"]["legacy_placeholders_rekeyed"] = rekey_tree(
                published, key, fix=True, jobs=jobs)["occurrences"]
        if gitleaks and not (rules_match and settings.get(prior, "config.gitleaks_scanned")):
            gitleaks_pass(published, "published")
        inputs.append(published)
    log("merge")
    rr = run / "merged"
    summary["merge"] = merge_exports(rr, inputs)
    shutil.rmtree(new)
    fallback = inputs[1] if len(inputs) > 1 else None  # published rows, kept until after clean

    values: list[tuple[str, str]] = []
    if secrets_dir and any(secrets_dir.glob("*.tsv")):
        log("scrub harvested values")
        values = load_values(sorted(secrets_dir.glob("*.tsv")))
        fixed = scrub_tree(rr, values, True, jobs)
        recheck = scrub_tree(rr, values, False, jobs)
        summary["scrub_known_values"] = {
            "values": fixed["values"], "replaced": fixed["occurrences"],
            "by_file": fixed["by_file"], "labels_hit": sorted(fixed["by_label"]),
            "left_after_recheck": recheck["occurrences"],
        }
    if leak_values:
        log("scrub gitleaks values")
        summary.setdefault("gitleaks", {})["scrubbed"] = scrub_tree(rr, leak_values, True, jobs)["occurrences"]
    if leak_drop:
        summary.setdefault("gitleaks", {})["dropped_rows"] = drop_traces(rr, leak_drop)

    log("clean")
    final = run / "final"
    summary["clean"] = clean_tree(rr, final, jobs=jobs)
    shutil.rmtree(rr)
    if fallback is not None:
        log("restore published versions of dropped traces")
        summary["fallback"] = restore_published(fallback, final, run / "fallback", values, leak_values, jobs,
                                                exclude=published_leaks)
        if "scrub_known_values" in summary:
            summary["scrub_known_values"]["left_after_recheck"] += summary["fallback"].get("left_after_recheck", 0)
        shutil.rmtree(fallback)
    verification = scan(final, jobs=jobs)
    summary["verify_ok"] = verification.ok

    log("tokens")
    tokens, final_ids = token_stats(final, jobs)
    summary["hosts"] = {
        label: {src: {"extracted": len(ids), "published": len(ids & final_ids)}
                for src, ids in sorted(by_src.items()) if ids}
        for label, by_src in store_ids.items()
    }
    manifest = _manifest(run, final, stores, extract_counts, {
        "redaction_rules_version": version,
        "placeholder_format": PLACEHOLDER_FORMAT,
        "gitleaks_scanned": bool(gitleaks),
        "merge": {k: v for k, v in summary["merge"].items() if k != "inputs"},
        "clean": summary["clean"],
        "fallback": {k: v for k, v in (summary.get("fallback") or {}).items() if k != "clean"},
    })
    manifest["export_summary"]["tokens"] = tokens
    (run / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    shutil.copy(run / "MANIFEST.json", final / "MANIFEST.json")
    summary["export_summary"] = manifest["export_summary"]

    if publish:
        if not verification.ok:
            raise SystemExit(f"refusing publish: {len(verification.leftovers)} leftover(s)")
        from traces.publish.huggingface import upload_variant

        log(f"publish {repo}")
        summary["published_url"] = upload_variant(
            final, repo, variant="all", private=bool(settings.get(cfg, "publish.private", True)))
    if not settings.get(cfg, "sync.keep_stores", False):
        shutil.rmtree(work / "stores", ignore_errors=True)
    json.dump(summary, sys.stdout, indent=2)
    print()
    (run / "SUMMARY.json").write_text(json.dumps(summary, indent=2))
    return summary
