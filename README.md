# traces

Extract, redact, and export AI coding-agent sessions into privacy-safe JSONL.

`traces` turns local history from Claude Code, Codex, Cursor, Grok Build, Gemini,
OpenCode, Continue, Trae, and Windsurf into distillation-ready datasets. The
default path is privacy-first: rule-based redaction always runs; optional layers
add a privacy model, gitleaks, and LLM residue cleanup. Publish is refused if a
leftover-pattern scan fails.

**PyPI:** `coding-agent-traces` (import package and CLI remain `traces`)  
**Repo:** [Infatoshi/traces](https://github.com/Infatoshi/traces)  
**License:** MIT — extractor/redaction provenance in [ATTRIBUTION.md](./ATTRIBUTION.md)

## Why this exists

Raw agent logs are not training data. Formats differ by product, tool calls are
often incomplete or uncorrelated, and sessions are full of secrets, paths, and
identity. This tool:

1. **Normalizes** multi-harness session stores into one conversation schema
2. **Redacts** secrets, paths, emails, and identifiers to stable placeholders
3. **Exports** DistillKit-compatible chat formats plus causal agent-step formats
4. **Verifies** before you publish

Related projects (different jobs):

| Project | Role |
|---------|------|
| [0xSero/ai-data-extraction](https://github.com/0xSero/ai-data-extraction) | Source extractors this repo packages and extends |
| [RodriMora/agent-trace-redaction-methodology](https://github.com/RodriMora/agent-trace-redaction-methodology) | Redaction rule methodology vendored here |
| [RESMP-DEV/tracesmith](https://github.com/RESMP-DEV/tracesmith) | Earlier combined pipeline; this repo continues that line with native execution exports and schema-backed metadata |
| [Trace Commons](https://huggingface.co/datasets/trace-commons/agent-traces) | Public donated *datasets*, not multi-harness export tooling |
| LangSmith / Langfuse / Arize | Live observability; not offline SFT export |

## Install

Python ≥ 3.10. Prefer [uv](https://docs.astral.sh/uv/):

```bash
# From git (recommended while the package is young)
uv pip install "git+https://github.com/Infatoshi/traces"

# Editable checkout
git clone https://github.com/Infatoshi/traces
cd traces
uv sync --extra dev --extra publish
```

Optional extras:

| Extra | Adds |
|-------|------|
| `publish` | `huggingface_hub` for `traces publish` |
| `privacy-filter` | `transformers` + `torch` for the model privacy pass |
| `dev` | pytest, coverage, ruff |

Optional non-Python tools:

- **gitleaks** binary for `--gitleaks` / `--gitleaks-fix` (`brew install gitleaks`)
- Any OpenAI-compatible endpoint for `--llm-residue http://localhost:8000`

## Quick start

```bash
# Extract → redact → export (messages + sharegpt by default)
uv run traces run --out ./output --user "$USER" --home "$HOME"

# Prefer native causal formats + legacy DistillKit variants
uv run traces run --out ./output --user "$USER" --home "$HOME" --variant all

uv run traces verify --in ./output/redacted
uv run traces stats  --in ./output/export
uv run traces sample --in ./output/export -n 5 --out ./preview
```

Output layout:

```
output/
├── raw_extracted/<source>.jsonl
├── redacted/<source>.jsonl
├── REDACTION_REPORT.json
├── export/
│   ├── messages.jsonl          # DistillKit chat
│   ├── sharegpt.jsonl          # DistillKit ShareGPT pairs
│   ├── execution.jsonl         # native tool-correlated tasks
│   └── assistant_steps.jsonl   # staircase index → execution_id
└── MANIFEST.json
```

Identity is always passed as flags (`--user`, `--home`). Nothing about a
particular machine or username is baked into library defaults.

## Export formats

| Variant | Flag | Purpose |
|---------|------|---------|
| `messages` | `--variant messages` | Multi-turn OpenAI-style chat rows |
| `sharegpt` | `--variant sharegpt` | Instruction pairs for DistillKit |
| `execution` | `--variant execution` | Task-bounded windows with correlated tool calls/results |
| `steps` | `--variant steps` | Compact assistant-target index (also writes `execution.jsonl`) |
| `both` | `--variant both` | `messages` + `sharegpt` (legacy default) |
| `all` | `--variant all` | All four files |

Native export preserves correlated tool calls and real tool results in
task-bounded windows split at user turns. For assistant-step index `i`,
`messages[:i]` is context and `messages[i]` is the target. Tool outputs are
never training targets.

Structural completeness is not task success: `tools_complete` only means
call/result IDs match. Prefer explicit test exit codes or benchmark joins for
quality labels.

Exports include a privacy-safe metadata envelope (schema `1.0`): opaque
trace/project IDs, source, model, day-bucketed lifecycle fields, token totals,
and tool/diff counts. Filtering uses fields each adapter normalizes — never
regex guesses over conversation text. Set `TRACES_METADATA_KEY` to keep opaque
IDs stable across separate runs.

### Filters

```bash
TRACES_METADATA_KEY='<stable local key>' \
uv run traces export --in ./output/redacted --out ./output/filtered \
  --include-source 'claude_code' --model '*sonnet*' \
  --since 2026-01-01T00:00:00Z --require-tools \
  --require-complete-tools --require-final-assistant --dedup \
  --variant all
```

Available filters: `--include-source`, `--drop-sources`, `--project`, `--model`,
`--status`, `--reasoning-effort`, `--session-kind`, `--since`, `--until`,
`--min-turns`, `--max-turns`, `--min-assistant-chars`, `--require-tools`,
`--require-diffs`, `--require-complete-tools`, `--require-final-assistant`,
`--dedup`. Shell globs work for source, project, and model.

## Supported sources

| Key | Typical store | Notes |
|-----|---------------|--------|
| `claude_code` | `~/.claude/projects/**` | Main + `agent-*` subagents; structured tool results preserved |
| `codex` | `~/.codex/sessions/**/rollout-*.jsonl[.zst]` | Modern function/custom tool calls correlated with outputs; reads compressed rollouts |
| `cursor` | Cursor GUI SQLite | Multi-format dispatch (v0.2–v2.0+) |
| `grok_build` | `~/.grok/sessions/**/chat_history.jsonl` + `updates.jsonl` | Summary schema: model, effort, session kind, sandbox; compacted sessions rebuilt in full from the event stream |
| `gemini` | Gemini CLI cache | Full recorded `toolCalls[].result` payloads |
| `opencode` | JSON store + `opencode.db` | Ordered parts; shell exit metadata |
| `continue` | Continue install dirs | |
| `trae` | Trae local history | |
| `windsurf` | Windsurf local history | |

No live `cursor_cli` extractor (`~/.cursor/chats`) yet. Source formats change
often — adapters are the main maintenance surface (see [CONTRIBUTING.md](./CONTRIBUTING.md)).

## Redaction and publish

Always-on: deterministic rule layers (secrets, paths, identifiers, URLs, …).

Optional layers (upstream order): privacy-filter → gitleaks-fix → LLM residue → gitleaks final scan.

```bash
uv run traces verify --in ./output/redacted   # exit 1 on leftovers
# Conservative subset if verify fails:
uv run traces clean --in ./output/redacted --out ./output/clean_redacted

uv run --extra publish traces publish \
  --repo YOUR_HF_USER/your-dataset \
  --in ./output/export --variant all --private
```

`publish` rescans the export directory and refuses upload if patterns remain.
Never publish without a clean verify. Your own datasets are your responsibility;
this tool does not make residual risk zero.

## Multi-host

For extracting on several machines and combining redacted JSONL, see
[docs/multi-host.md](./docs/multi-host.md).

## Develop

```bash
uv sync --extra dev --extra publish
uv run pytest -q
uv run ruff check . --fix
```

Agent/ops command reference: [AGENTS.md](./AGENTS.md).

## Privacy note

Redaction is best-effort. Long coding sessions can still encode proprietary code,
private strategy, and identity via context that rules do not catch. Review
samples (`traces sample`) before any public upload. Prefer private Hub datasets
unless you have curated, reviewed subsets.

## License

MIT. Copyright notices and upstream licenses: [LICENSE](./LICENSE),
[NOTICE.md](./NOTICE.md), [ATTRIBUTION.md](./ATTRIBUTION.md).
