# ATTRIBUTION

File-by-file provenance. Each entry links to the upstream path and (once vendored) the commit SHA.

| File | Upstream | Upstream path | Upstream commit | License | Changes |
|---|---|---|---|---|---|
| `traces/extract/claude_code.py` | 0xSero/ai-data-extraction | `extract_claude_code.py` | `b7520c48b2bb46d5a0d3257e80ca1a59670d5e37` | MIT | Refactored to `Extractor` protocol; logic preserved |
| `traces/extract/codex.py` | 0xSero/ai-data-extraction | `extract_codex.py` | `b7520c48b2bb46d5a0d3257e80ca1a59670d5e37` | MIT | Refactored to `Extractor` protocol; logic preserved |
| `traces/extract/continue_.py` | 0xSero/ai-data-extraction | `extract_continue.py` | `b7520c48b2bb46d5a0d3257e80ca1a59670d5e37` | MIT | Refactored to `Extractor` protocol; added `find_continue_installations` (upstream had none); logic preserved |
| `traces/extract/cursor.py` | 0xSero/ai-data-extraction | `extract_cursor.py` | `b7520c48b2bb46d5a0d3257e80ca1a59670d5e37` | MIT | Refactored to `Extractor` protocol; multi-format dispatch logic (v0.2–v2.0+) preserved verbatim |
| `traces/extract/gemini.py` | 0xSero/ai-data-extraction | `extract_gemini.py` | `b7520c48b2bb46d5a0d3257e80ca1a59670d5e37` | MIT | Refactored to `Extractor` protocol; logic preserved |
| `traces/extract/opencode.py` | 0xSero/ai-data-extraction | `extract_opencode.py` | `b7520c48b2bb46d5a0d3257e80ca1a59670d5e37` | MIT | Refactored to `Extractor` protocol; both CLI + Tauri paths kept; `find_installations` returns bare `Path`s (protocol) and `extract` re-dispatches on layout |
| `traces/extract/trae.py` | 0xSero/ai-data-extraction | `extract_trae.py` | `b7520c48b2bb46d5a0d3257e80ca1a59670d5e37` | MIT | Refactored to `Extractor` protocol; logic preserved |
| `traces/extract/windsurf.py` | 0xSero/ai-data-extraction | `extract_windsurf.py` | `b7520c48b2bb46d5a0d3257e80ca1a59670d5e37` | MIT | Refactored to `Extractor` protocol; logic preserved |
| `traces/redact/rules/*.py` | RodriMora/agent-trace-redaction-methodology | `scripts/export_redacted_traces.py` (`Redactor` class, lines 261–408) | `c696bc72ad4c18f3d5b79fe4d54af00a2fd1c6b6` | MIT | Split into modules by rule family; regex/category/replacement ported verbatim, composed in upstream interleaved order via `UPSTREAM_ORDER` |
| `traces/redact/schema.py` | RodriMora/agent-trace-redaction-methodology | `scripts/export_redacted_traces.py` (`redact_sensitive_field`) | `<pending>` | MIT | Extracted as standalone recursive walker |
| `tests/test_canary_redaction.py` | RodriMora/agent-trace-redaction-methodology | `tests/test_canary_redaction.py` | `<pending>` | MIT | Adapted to new pipeline shape |
| `traces/extract/grok_build.py` | Infatoshi/traces | (original) | — | MIT | New Grok Build extractor for `~/.grok/sessions/**/chat_history.jsonl` |

