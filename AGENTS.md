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
