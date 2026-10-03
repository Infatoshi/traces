# traces — agent / developer commands

CLI entrypoint: `traces`. Always prefer `uv`.

Package name on PyPI: `coding-agent-traces`. Import path and console script stay
`traces`.

## Install / test

```bash
uv sync --extra dev --extra publish
uv run pytest -q
uv run ruff check . --fix
```

Optional: `gitleaks` for `--gitleaks` / `--gitleaks-fix`. Privacy filter:
`--extra privacy-filter`.

`redact`, `verify`, `clean`, `run`, `scrub-values`, and `publish` split JSONL
into newline-aligned byte ranges across `--jobs` processes (default: all
cores); output is identical for any job count. Rule matching goes through a
Hyperscan prefilter (`traces/fastmatch.py`): one SIMD pass finds which rules can
match a string, and only those run as Python regexes, so output is identical
to running every rule. Without the `hyperscan` wheel it falls back to running
every rule.

## Config and monthly sync

Machine config lives in `traces.local.toml` in the repo root (gitignored; the
repo is public) or `~/.config/traces/config.toml`; `--config PATH` overrides.
`traces.example.toml` documents every key. Commands take defaults from it
(user, private terms, metadata key file, jobs, cache dir, publish repo); CLI
flags win.

```bash
uv run traces sync               # stage every host, harvest secrets, extract, redact, merge, scrub, clean
uv run traces sync --publish     # same, then upload after a clean verify
uv run traces sync --no-merge    # fresh export only, no merge with the published dataset
```

`sync` runs from the machine holding the config and does the heavy work on
`sync.work_host` over one multiplexed SSH connection. Output lands in
`<work_dir>/run/final` with `MANIFEST.json` (published) and `SUMMARY.json`
(work host only: per-host extracted vs published counts, cache stats, scrub and
gitleaks results). Harvested secret values go to a 0700 tmpfs dir on the work
host and are deleted after the run; staged raw stores are deleted unless
`sync.keep_stores = true` (then the next rsync is incremental).

What a run checks and records:
- Published rows are re-redacted only when `config.redaction_rules_version` in
  the published MANIFEST differs from the current rules; gitleaks scans the
  fresh export every run and published rows only when not yet scanned under the
  current rules. Findings inside decoded base64/percent content drop the whole
  trace (they cannot be replaced in place).
- `export_summary.tokens` in MANIFEST: o200k_base token totals by model and by
  source (`traces tokens --in DIR` for any export). The dataset card shows them.
- Before publishing, read SUMMARY.json: `verify_ok`, `scrub_known_values.left_after_recheck`
  (must be 0), `gitleaks`, and `hosts`.

Redaction caches each redacted message block under `run.cache_dir`, keyed by
the block content plus a rules fingerprint (redaction source code, user, home,
private terms and domains, URL allowlist, placeholder key). Unchanged messages
are reused across runs; any rule or code change starts a new cache file.
Placeholders are `[CATEGORY:hmac10]`, an HMAC of the value under the metadata
key, so they are stable across runs and cannot be reversed by hashing guesses.
Exports before that used `[CATEGORY:NNNN:sha10]` (plain SHA-256, guessable).
`sync` rewrites those once in published rows, when the published MANIFEST lacks
`config.placeholder_format = "hmac10"`, to `[CATEGORY:hmac10]` keyed on the old
digest (`traces/rekey.py`; standalone `traces rekey-placeholders --in DIR [--check]`).

Building blocks, usable on their own: `traces pull-published`, `traces merge`
(by trace_id, longest version wins, old-only traces kept), `traces redact
--export-rows` (re-redact merged export rows, content lists only),
`traces scrub-values` (exact-value scrub from `traces/harvest.py` output).

## One-host pipeline

```bash
uv run traces run --sources cursor,claude_code,codex,grok_build \
  --out /tmp/traces-out --variant all \
  --user "$USER" --home "$HOME"

uv run traces verify --in /tmp/traces-out/redacted
uv run traces stats  --in /tmp/traces-out/export
uv run traces sample --in /tmp/traces-out/export -n 5 --out /tmp/traces-preview
```

Output layout: `raw_extracted/`, `redacted/`, 
`export/{messages,sharegpt,execution,assistant_steps}.jsonl`, `MANIFEST.json`.

`--variant both` means `messages` + `sharegpt`. Use `--variant all` for native
execution exports as well. `execution.jsonl` splits at real user turns and
preserves native call/result IDs, arguments, outputs, statuses, and structured
result metadata plus `assistant_steps[].message_index`. `assistant_steps.jsonl`
joins to execution rows by `execution_id`. For index `i`, `messages[:i]` is
context and `messages[i]` is the target. System/user/tool messages are never
targets. `--variant steps` also writes `execution.jsonl`.

Stage commands: `traces extract|redact|clean|export|verify|stats|sample|publish`.

Filtered export:

```bash
TRACES_METADATA_KEY='<stable local key>' \
uv run traces export --in /tmp/traces-out/redacted --out /tmp/traces-out/filtered \
  --include-source 'grok_build' --model '*grok*' \
  --reasoning-effort high --session-kind subagent \
  --since 2026-07-01T00:00:00Z --require-tools \
  --require-complete-tools --require-final-assistant --dedup \
  --variant all
```

Filters: `--include-source`, `--drop-sources`, `--project`, `--model`,
`--status`, `--reasoning-effort`, `--session-kind`, `--since`, `--until`,
`--min-turns`, `--max-turns`, `--min-assistant-chars`, `--require-tools`,
`--require-diffs`, `--require-complete-tools`, `--require-final-assistant`,
`--dedup`. Shell globs accepted for source, project, and model.

Structural completeness is not a success label. Prefer explicit test exit codes
from tool-result metadata or join traces to benchmark/run artifacts. Never infer
success merely from final assistant prose.

Every exported row has metadata schema `1.0`: opaque trace ID, exact source,
day-bucketed session fields, opaque project ID, model names, message/tool/diff
counts, and normalized token totals when the adapter provides them. Raw session
IDs, paths, titles, message IDs, tool payloads, and source files are not
published. Set `TRACES_METADATA_KEY` for stable IDs across runs; without it, one
random in-process key is used.

Multi-host extract/combine: [docs/multi-host.md](./docs/multi-host.md).

## Sources

| Key | Store |
|-----|--------|
| `claude_code` | `~/.claude/projects/**` |
| `codex` | `~/.codex/sessions/**/rollout-*.jsonl` |
| `cursor` | Cursor GUI SQLite |
| `grok_build` | `~/.grok/sessions/**/chat_history.jsonl` (+ `summary.json`) |
| `continue` / `gemini` / `opencode` / `trae` / `windsurf` | as implemented |

No live `cursor_cli` extractor (`~/.cursor/chats`).

Grok Build: maps `system|user|assistant|reasoning|tool_result`; assistant
`tool_calls` to `tool_uses`; reasoning summary wrapped in `<reasoning>`. Summary
schema also normalizes model, created/updated times, chat format version,
reasoning effort, session kind, and sandbox profile.

Current-format notes:

- Claude main and `agent-*` subagent sessions are retained; wrapped tool-result
  content and structured `toolUseResult` details are preserved.
- Codex modern `function_call` / `custom_tool_call` and output records are
  emitted as correlated messages rather than counts only.
- OpenCode reads both legacy JSON storage and current read-only `opencode.db`;
  ordered parts preserve call/result causality and shell exit metadata.
- Gemini preserves the full recorded `toolCalls[].result` payload.
- Native/step exports omit provider reasoning records from assistant targets.

## Constraints

- Identity is CLI flags (`--user`, `--home`), never hardcoded library defaults
  for publish.
- Never publish without a clean `traces verify`. The `publish` command rescans
  the export directory and refuses upload on leftovers.
- Do not commit `out/`, raw extracts, or redacted personal corpora.
- KernelBench published runs use native project tooling, not this CLI, unless a
  project explicitly routes through it.
