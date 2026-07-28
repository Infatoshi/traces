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
