# Contributing

Thanks for helping keep multi-harness extractors honest. Session formats change
without notice; adapters are the highest-value contributions.

## Dev setup

```bash
uv sync --extra dev --extra publish
uv run pytest -q
uv run ruff check . --fix
```

## Adapter status

| Source | Priority when formats break | What "good" looks like |
|--------|----------------------------|-------------------------|
| `claude_code` | High | Main + subagent sessions; tool call/result correlation; usage/lifecycle fields |
| `codex` | High | Function/custom tool calls + outputs as messages; rollout headers; tokens |
| `cursor` | High | SQLite multi-format; do not regress older composers |
| `grok_build` | Medium | `chat_history.jsonl` + `summary.json` model/effort/kind fields |
| `opencode` | Medium | Legacy JSON + `opencode.db`; ordered parts; shell exit metadata |
| `gemini` | Medium | Full `toolCalls[].result`; progressive cache consolidation |
| `continue` / `trae` / `windsurf` | Lower | Keep extracting; improve metadata when observed |

When updating an extractor:

1. Prefer structured fields from the source over regex on message text.
2. Extend tests with a minimal fixture that mirrors the new on-disk shape.
3. Preserve correlated tool call/result IDs when the product records them.
4. Document observed schema notes in `AGENTS.md` if behavior changes.
5. Update [ATTRIBUTION.md](./ATTRIBUTION.md) if you re-sync from upstream extractors.

## Redaction changes

Redaction rules come from an upstream methodology (see ATTRIBUTION). Keep rule
order stable unless you have a clear reason and canary tests. Add canaries for
any new secret/PII class.

## Export / metadata

- Metadata schema is `1.0`. Additive fields are fine; renames need a version bump.
- Filters must use normalized metadata only, not content heuristics.
- Native `execution` / `assistant_steps` must never make tool results the training
  target.

## PR checklist

- [ ] `uv run pytest -q` passes
- [ ] `uv run ruff check . --fix` passes (or equivalent clean)
- [ ] No personal `out/`, raw extracts, tokens, or home-directory dumps
- [ ] Fixtures use fake users/paths (`alice`, `/home/alice/...`)
- [ ] README/AGENTS updated if CLI or formats change

## Scope

This project is offline extract → redact → export. Live OpenTelemetry harness
instrumentation belongs elsewhere.
