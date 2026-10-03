# Multi-host extract

Use this when agent history lives on more than one machine (laptop + workstation).
The pattern is: run extract+redact on each host, pull redacted JSONL to one
machine, combine, verify, export, optionally publish.

Replace hostnames, users, and paths with your own.

## 1. Extract on each host

On host A:

```bash
uv run traces run \
  --sources cursor,claude_code,codex,grok_build,gemini,opencode \
  --out /tmp/traces-sync/host-a --variant all \
  --user "$USER" --home "$HOME"
```

On host B (example: copy the repo, then run):

```bash
rsync -a --delete \
  --exclude .git --exclude .venv --exclude .pytest_cache \
  --exclude .ruff_cache --exclude '*.egg-info' --exclude out \
  ./ host-b:/tmp/traces-repo/

ssh host-b 'bash -lc "cd /tmp/traces-repo && uv run traces run \
  --sources cursor,claude_code,codex,grok_build,gemini,opencode \
  --out /tmp/traces-sync/host-b --variant all \
  --user \"$USER\" --home \"$HOME\""'

mkdir -p /tmp/traces-sync/host-b
rsync -a host-b:/tmp/traces-sync/host-b/ /tmp/traces-sync/host-b/
```

## 2. Combine redacted sources

```bash
OUT=/tmp/traces-sync
mkdir -p "$OUT/combined/redacted" "$OUT/combined/export"

for src in cursor claude_code codex grok_build gemini opencode; do
  : > "$OUT/combined/redacted/$src.jsonl"
  for host in host-a host-b; do
    f="$OUT/$host/redacted/$src.jsonl"
    [ -f "$f" ] && cat "$f" >> "$OUT/combined/redacted/$src.jsonl"
  done
done
```

## 3. Verify, clean if needed, export

```bash
uv run traces verify --in "$OUT/combined/redacted"

# If FAIL, drop complete flagged rows into a clean tree:
uv run traces clean --in "$OUT/combined/redacted" --out "$OUT/combined/clean_redacted"
uv run traces verify --in "$OUT/combined/clean_redacted"

export TRACES_METADATA_KEY="${TRACES_METADATA_KEY:-$(openssl rand -hex 32)}"
uv run traces export \
  --in "$OUT/combined/clean_redacted" \
  --out "$OUT/combined/export" \
  --variant all --dedup
```

## 4. Publish (optional)

```bash
uv run --extra publish traces publish \
  --repo YOUR_HF_USER/your-dataset \
  --in "$OUT/combined/export" --variant all --private

# Optional provenance sidecar
hf upload YOUR_HF_USER/your-dataset \
  "$OUT/combined/MANIFEST.json" MANIFEST.json --repo-type dataset
```

Use the same `TRACES_METADATA_KEY` whenever you need stable opaque trace/project
IDs across re-runs. Store that key outside the repo.

## 5. Appending to an existing dataset

`traces publish` replaces the files on the Hub. Local agent stores get pruned
over time, so a fresh extract is not a superset of what was published. Use
`traces sync --publish` (see AGENTS.md), or by hand:

```bash
uv run --extra publish traces pull-published --repo YOUR_HF_USER/your-dataset --out published
uv run traces merge --out merged export published      # newest first
uv run traces redact --export-rows --in merged --out rr
uv run traces clean --in rr --out final
```

Merging is by `metadata.trace_id`, so every export must use the same
`TRACES_METADATA_KEY`. Zero ID overlap between a fresh export and the published
rows means the key differs (or was unset, which falls back to a random
per-process key) and ID-based merging will duplicate traces.
