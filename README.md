# traces

Personal fork and evolution of the TraceSmith pipeline, owned at [Infatoshi/traces](https://github.com/Infatoshi/traces). MIT; extractor/redaction provenance in `ATTRIBUTION.md`.

Private dataset: [Infatoshi/traces](https://huggingface.co/datasets/Infatoshi/traces).

traces is a command-line tool that turns the local conversation logs left
behind by AI coding agents into clean, redacted, fine-tuning-ready datasets. It
runs a four-stage pipeline — **extract → redact → export → manifest** — over
agent history from nine supported sources, replaces every secret, path, email,
and identifier with a stable placeholder, and emits DistillKit-ready JSONL plus a
provenance manifest. Nothing about your identity is ever baked into the tool; the
output is safe to publish straight to the HuggingFace Hub.

The pipeline is reproducible and privacy-first: the rule-based redaction layer is
always on, with optional privacy-filter, gitleaks, and LLM residue passes layered
on top. Each source contributes its own file at every stage, so datasets can be
sliced or dropped per-source.

---

## Status

**v0.1.0** — extract (9 sources, incl. grok_build), layered redaction, two export variants,
MANIFEST provenance, verify, stats, sample, publish. The first publishable
milestone.

---

## Install

Requires Python ≥ 3.10.

```bash
# Core (click only).
pip install -e .

# Optional extras:
pip install -e ".[publish]"          # huggingface_hub, for `traces publish`
pip install -e ".[privacy-filter]"   # transformers + torch, for the optional privacy filter
pip install -e ".[dev]"              # pytest + pytest-cov, for running the suite
```

The optional redaction layers have extra, non-Python dependencies:

- **gitleaks** — the external `gitleaks` binary (the `--gitleaks` / `--gitleaks-fix`
  flags). Install separately, e.g. `brew install gitleaks`.
- **LLM residue pass** — any OpenAI-compatible endpoint, passed via
  `--llm-residue http://localhost:8000`.

These are all opt-in. The default redaction path needs nothing but `click`.

---

## Quick start

One command runs the whole pipeline end to end:

```bash
# Extract from ~, redact, export both variants, write MANIFEST.json.
traces run --out ./output --user "$USER"
```

This produces:

```
output/
├── raw_extracted/<source>.jsonl   # normalized conversations, one per source
├── redacted/<source>.jsonl        # PII/secrets replaced with placeholders
├── REDACTION_REPORT.json          # per-source redaction counts + config snapshot
├── export/
│   ├── messages.jsonl             # DistillKit "messages" variant
│   └── sharegpt.jsonl             # DistillKit "ShareGPT" variant
└── MANIFEST.json                  # provenance: counts, config, file hashes
```

Then inspect before you publish:

```bash
traces verify --in ./output/redacted     # scan for leftover PII (exits 1 on findings)
traces stats  --in ./output/export       # corpus metrics as JSON
traces sample --in ./output/export -n 5 --out ./preview   # random 5-row preview
```

Happy? Publish:

```bash
traces publish --repo youruser/my-traces --in ./output/export --variant both
```

---

## Supported sources

Eight extractors, all auto-discovered from the local home directory:

| Source | Key |
|---|---|
| Claude Code | `claude_code` |
| Codex (OpenAI) | `codex` |
| Continue | `continue` |
| Cursor | `cursor` |
| Gemini CLI | `gemini` |
| OpenCode | `opencode` |
| Trae | `trae` |
| Windsurf | `windsurf` |

Each source contributes its own `<source>.jsonl` in every stage, so datasets can
be sliced or dropped per-source (see `--drop-sources` under `export`).

---

## CLI reference

All eight commands. Defaults are shown.

### `traces run` — the whole pipeline

```
traces run [--sources a,b] [--root ~] [--out ./output]
               [--variant messages|sharegpt|both] [--user NAME] [--home DIR]
```

Chains **extract → redact → export → MANIFEST.json**, calling the stage
functions directly so the per-stage counts feed the manifest. Use `--user` and
`--home` so the redactor knows which username/home path to scrub as private
identifiers (defaults to `$USER` / `$HOME`).

### `traces extract`

```
traces extract [--sources a,b] [--root ~] [--out ./output]
```

Scan `--root` for installed agents and write `raw_extracted/<source>.jsonl`.
The root is treated as a user home directory; extractor discovery checks both
macOS-style (`Library/Application Support`) and Linux-style (`.config`,
`.local/share`) layouts under that root.
Sources with no installation contribute an empty file (downstream stages always
find a file). With no `--sources`, all 8 are tried.

### `traces redact`

```
traces redact [--in ./output/raw_extracted] [--out ./output/redacted]
                  [--allow-public-urls] [--allow-domain D]... [--private-term T]...
                  [--private-domain D]... [--user NAME] [--home DIR]
                  [--privacy-filter] [--privacy-filter-device auto|cpu|cuda|cuda:N]
                  [--gitleaks] [--gitleaks-fix]
                  [--llm-residue URL]
```

Applies the layered redactor (rules → optional privacy filter → optional
gitleaks fix → optional LLM residue → optional final gitleaks scan) and writes
`redacted/<source>.jsonl` plus a sibling `REDACTION_REPORT.json`.

### `traces export`

```
traces export [--in ./output/redacted] [--out ./output/export]
                  [--variant messages|sharegpt|both]
                  [--min-turns N] [--max-turns N] [--min-assistant-chars N]
                  [--drop-sources a,b] [--dedup]
```

Flatten redacted conversations into DistillKit-ready JSONL (see *Output format*
below). Quality filters drop conversations with no assistant turn, apply
turn-count bounds, require a minimum assistant message length, drop whole
sources, and optionally deduplicate.

### `traces verify`

```
traces verify [--in ./output/redacted]
```

Scan the redacted tree for leftover PII/secrets that survived the pipeline.
Exits `0` if clean, `1` if anything is found (prints up to 20 findings).

### `traces publish`

```
traces publish --repo USER/DATASET [--in ./output/export]
                   [--variant messages|sharegpt|both] [--private]
```

Upload the chosen variant(s) to the HuggingFace Hub with an auto-generated
dataset card (provenance, redaction summary, attribution). Requires the
`[publish]` extra and a logged-in `huggingface_hub` token.

### `traces stats`

```
traces stats [--in ./output/export]
```

Print corpus metrics (conversation counts, token/char totals, per-source and
per-variant breakdowns) as JSON.

### `traces sample`

```
traces sample [--in ./output/export] [-n 10] [--seed 0] [--out DIR]
```

Sample `n` random conversations for pre-publish inspection. With `--out`, also
writes `sample.jsonl`. Seeded for reproducibility.

---

## Output format

traces writes **two interchangeable variants** of the same flattened data,
so you can point DistillKit at whichever one your recipe expects.

### `messages` variant — `export/messages.jsonl`

One JSON object per conversation, DistillKit's flat `messages` schema. Each
message's rich fields (`tool_use`, `tool_results`, `suggested_diffs`, …) are
flattened into a single string `content`, and the original `role` is preserved.

```json
{"messages": [
  {"role": "user", "content": "add a greeting function to the project"},
  {"role": "assistant", "content": "I'll create it.\n\n<tool_use name=\"write_file\">\n{...}\n</tool_use>"}
]}
```

### `sharegpt` variant — `export/sharegpt.jsonl`

One JSON object per **user→assistant instruction pair** (independent pairs, no
rolling context). A leading `system` message, if present, is attached to the
first pair. Uses the classic `from`/`value` shape:

```json
{"conversations": [
  {"from": "human", "value": "add a greeting function to the project"},
  {"from": "gpt",   "value": "I'll create it.\n\n<tool_use name=\"write_file\">\n{...}\n</tool_use>"}
]}
```

Worked, redacted examples of both variants live in
[`examples/`](examples/) — `sample_input.jsonl` (raw) plus the golden
`messages_expected.jsonl` and `sharegpt_expected.jsonl` the pipeline produces
from it.

### `MANIFEST.json`

Provenance for reproducibility: a UTC `created_at`, the `tool_version`, the
`config` snapshot used, a per-`sources` block (raw record counts, redaction
counts, exported message counts), and a `files` block with `sha256`/`bytes`/
`rows` for each export artifact. Generated automatically by `traces run`.

---

## DistillKit integration

Point DistillKit at the exported JSONL and pick the matching variant. Minimal
config snippet:

```yaml
# distillkit.yaml
dataset:
  # Use exactly one of the two below.
  path_messages: ./output/export/messages.jsonl   # flat messages variant
  path_sharegpt: ./output/export/sharegpt.jsonl   # from/value pairs

  # Quality knobs that mirror `traces export` so train/eval stay consistent.
  min_turns: 2
  min_assistant_chars: 50
  dedup: true

output:
  dir: ./distilled
```

The two variants are drop-in for DistillKit's expected schemas: `messages.jsonl`
→ the `{"messages": [{"role","content"}, ...]}` chat format; `sharegpt.jsonl`
→ the `{"conversations": [{"from","value"}, ...]}` instruction-pair format.

---

## Privacy model

Redaction is the core safety boundary. It is **layered and opt-in**, with the
rule layer always on:

1. **Rule layer (always on).** Regex-driven, ported from the upstream
   `agent-trace-redaction-methodology` and split by family — identifiers,
   secrets, paths, URLs, plus your private terms/domains. Every match becomes a
   stable, reversible placeholder like `[GENERIC_HOME_PATH:0001:58c69070ae]`
   (family + index + short hash) so structure is preserved but identity is not.
2. **Privacy filter (optional, `--privacy-filter`).** A transformers NER model
   that catches names/PII the regex layer misses. Needs the `[privacy-filter]`
   extra.
3. **gitleaks fix (optional, `--gitleaks-fix`).** Iteratively redacts findings
   from a live gitleaks scan, in place, before the final pass.
4. **LLM residue pass (optional, `--llm-residue URL`).** An OpenAI-compatible
   model call that re-reads the redacted text for anything still leaking.
5. **Final gitleaks scan (optional, `--gitleaks`).** A read-only last line of
   defense that fails loudly if anything remains.

Always run `traces verify` on the redacted tree before publishing — it's a
cheap, dependency-free safety net that exits non-zero on any leftover.

You control your own scope: `--user` and `--home` tell the redactor which
username and home directory to treat as yours; `--private-term` /
`--private-domain` add custom scrub targets; `--allow-public-urls` /
`--allow-domain` loosen URL scrubbing for domains you've decided are fine.

---

## Known limitations (v0.1.0)

These are documented gaps in the initial release. None affects redaction safety
or the DistillKit output shape; they're coverage/attribution/convenience items
tracked for a follow-up.

- **Timezone-city strings are not auto-discovered.** `discover_private_terms`
  derives private terms from the username, home dir, hostname, git identity,
  SSH config, and Pi session paths — but it does not scan for timezone-city
  strings (e.g. `Europe/Zurich`, `America/New_York`) the way the upstream
  redactor does. If your locale city appears in traces, add it explicitly via
  `--private-term` (e.g. `--private-term Zurich`).
- **`--purge-raw` is deferred to v0.1.1.** Raw extraction is always retained
  under `<out>/raw_extracted/` so the redaction step can be re-run with
  different config without re-extracting. There is no flag to auto-delete it
  yet; delete the directory manually if you don't need it.
- **MANIFEST does not report per-source export attribution.** The export
  pipeline does not thread source tags through to the emitted rows, so a
  per-source "exported messages / pairs" count would be misleading (an earlier
  draft stamped the GLOBAL row count onto every source — see Fix 1 in the
  pre-publish touch-ups). v0.1.0 carries global counts in the top-level
  `export_summary` block of `MANIFEST.json`; the per-source block reports only
  `raw_records` and `redaction_counts`.
- **`traces stats` / `traces sample` source breakdown works best on
  `redacted/`.** Exported rows (`export/messages.jsonl`, `export/sharegpt.jsonl`)
  lose the top-level `source` field during export, so a source breakdown run
  against `export/` cannot attribute rows back to a source. Point these
  commands at `<out>/redacted/` for an accurate per-source view.
- **Cursor inline-storage branch doesn't capture `toolResults`.** The Cursor
  extractor's inline-storage path omits tool-result bubbles. This is an
  upstream bug ported verbatim (see `traces/extract/cursor.py`);
  conversations extracted from Cursor's inline storage may be missing tool
  outputs. The standard Cursor storage path is unaffected.

---

## Attribution

This project builds on two upstream MIT-licensed projects, vendored and
refactored in place:

- **0xSero/ai-data-extraction** — the eight source extractors.
- **RodriMora/agent-trace-redaction-methodology** — the layered redactor and
  canary test suite.

File-by-file provenance (upstream path, commit SHA, license, and a summary of
changes) is recorded in [`ATTRIBUTION.md`](ATTRIBUTION.md). Licensed under the
[MIT License](LICENSE).
