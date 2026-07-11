Home: Mac-native — canonical at macbook:/Users/infatoshi/dev/tools/traces. GitHub Infatoshi/traces. HF private dataset Infatoshi/traces. Anvil is extract target only (rsync tool + pull output); do not treat anvil as source of truth for this repo.

# traces — commands

Always `uv`. CLI entrypoint: `traces`.

## Install / test

```bash
cd /Users/infatoshi/dev/tools/traces
uv sync --extra dev --extra publish   # or: uv pip install -e ".[dev,publish]"
uv run pytest -q
uv run ruff check . --fix
```

Optional: `gitleaks` binary for `--gitleaks` / `--gitleaks-fix`. Privacy filter: `--extra privacy-filter`.

## One-host pipeline

```bash
uv run traces run --sources cursor,claude_code,codex,grok_build \
  --out /tmp/traces-out --variant both \
  --user infatoshi --home "$HOME"

uv run traces verify --in /tmp/traces-out/redacted
uv run traces stats  --in /tmp/traces-out/export
uv run traces sample --in /tmp/traces-out/export -n 5 --out /tmp/traces-preview
```

Output layout: `raw_extracted/`, `redacted/`, `export/{messages,sharegpt}.jsonl`, `MANIFEST.json`.

Stage commands if needed: `traces extract|redact|clean|export|verify|stats|sample|publish`.

Filtered export example:

```bash
TRACES_METADATA_KEY='<stable local key>' \
uv run traces export --in /tmp/traces-out/redacted --out /tmp/traces-out/filtered \
  --include-source 'grok_build' --model '*grok*' \
  --reasoning-effort high --session-kind subagent \
  --since 2026-07-01T00:00:00Z --require-tools --dedup
```

Available export filters: `--include-source`, `--drop-sources`, `--project`,
`--model`, `--status`, `--reasoning-effort`, `--session-kind`, `--since`,
`--until`, `--min-turns`, `--max-turns`,
`--min-assistant-chars`, `--require-tools`, `--require-diffs`, and `--dedup`.
Shell globs are accepted for source, project, and model. Export summaries report
the first drop reason for each excluded conversation.

Every exported row has metadata schema `1.0`: an opaque trace ID, exact source,
day-bucketed session fields, an opaque project ID, model names, message/tool/
diff counts, and normalized token totals when the adapter provides them. Raw
session IDs, paths, titles, message IDs, tool payloads, and source files are not
published. Set `TRACES_METADATA_KEY` for stable trace/project IDs across runs;
without it, one random in-process key is used.

## Fleet sync (macbook + anvil)

```bash
cd /Users/infatoshi/dev/tools/traces
OUT=/tmp/traces-sync
SOURCES=cursor,claude_code,codex,grok_build

uv run traces run --sources $SOURCES \
  --out $OUT/mac --variant both --user infatoshi --home /Users/infatoshi

rsync -a --delete --exclude .git --exclude .venv --exclude .pytest_cache \
  --exclude .ruff_cache --exclude '*.egg-info' \
  ./ anvil:/tmp/traces-repo/
ssh anvil 'bash -lc "cd /tmp/traces-repo && uv run traces run \
  --sources cursor,claude_code,codex,grok_build \
  --out /tmp/traces-sync/anvil --variant both \
  --user infatoshi --home /home/infatoshi"'
mkdir -p $OUT/anvil
rsync -a anvil:/tmp/traces-sync/anvil/ $OUT/anvil/
```

Combine redacted, verify, clean if needed, publish:

```bash
mkdir -p $OUT/combined/redacted $OUT/combined/export
for src in cursor claude_code codex grok_build; do
  : > $OUT/combined/redacted/$src.jsonl
  [ -f $OUT/mac/redacted/$src.jsonl ] && cat $OUT/mac/redacted/$src.jsonl >> $OUT/combined/redacted/$src.jsonl
  [ -f $OUT/anvil/redacted/$src.jsonl ] && cat $OUT/anvil/redacted/$src.jsonl >> $OUT/combined/redacted/$src.jsonl
done

uv run traces verify --in $OUT/combined/redacted
# If FAIL, make a conservative clean subset by dropping complete flagged rows:
uv run traces clean --in $OUT/combined/redacted --out $OUT/combined/clean_redacted
uv run traces verify --in $OUT/combined/clean_redacted

uv run traces export --in $OUT/combined/clean_redacted --out $OUT/combined/export --variant both
# or export from redacted if verify already OK:
# uv run traces export --in $OUT/combined/redacted --out $OUT/combined/export --variant both

uv run --extra publish traces publish \
  --repo Infatoshi/traces \
  --in $OUT/combined/export --variant both --private
hf upload Infatoshi/traces $OUT/combined/MANIFEST.json MANIFEST.json --repo-type dataset
```

Never publish without a clean `traces verify`.
The `publish` command independently rescans the exact export directory and
refuses the upload if any leftover pattern remains.

## Sources

| Key | Store |
|-----|--------|
| `claude_code` | `~/.claude/projects/**` |
| `codex` | `~/.codex/sessions/**/rollout-*.jsonl` |
| `cursor` | Cursor GUI SQLite |
| `grok_build` | `~/.grok/sessions/**/chat_history.jsonl` (+ `summary.json`) |
| `continue` / `gemini` / `opencode` / `trae` / `windsurf` | as implemented |

No live `cursor_cli` extractor (`~/.cursor/chats`). No gamer host re-extract.

Grok: maps `system|user|assistant|reasoning|tool_result`; assistant `tool_calls`
to `tool_uses`; reasoning summary wrapped in `<reasoning>`. The observed local
summary schema also normalizes model, created/updated times, chat format
version, reasoning effort, session kind, and sandbox profile.

## Constraints

- Identity is CLI flags (`--user`, `--home`), never hardcoded in library defaults for publish.
- Canonical HF dataset name is `Infatoshi/traces` only (old `ai-coding-sessions-tracesmith` retired).
- Tool remote is `Infatoshi/traces`. Ignore `RESMP-DEV/tracesmith` and `~/dev/tools/tracesmith` unless explicitly asked.
- KernelBench traces stay on native `kb push-runs` — do not route through this tool.
