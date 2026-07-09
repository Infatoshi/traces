# NOTICE

This project incorporates code from the following upstream projects:

## ai-data-extraction
- Source: https://github.com/0xSero/ai-data-extraction
- License: MIT
- Used: extractors for Claude Code, Codex, Cursor, Trae, Windsurf, Continue, Gemini, OpenCode (in `traces/extract/`).

## agent-trace-redaction-methodology
- Source: https://github.com/RodriMora/agent-trace-redaction-methodology
- License: see ATTRIBUTION.md (MIT-compatible; preserved verbatim under `traces/redact/`).
- Used: deterministic redaction rules, schema-aware redaction, layered privacy passes, scanner, canary tests.

## DistillKit
- Source: https://github.com/arcee-ai/DistillKit
- License: Apache-2.0
- Used: as a data consumer only. No code from DistillKit is incorporated; we emit datasets in formats DistillKit's `_format_row` accepts.
