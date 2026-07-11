# traces

Extract, redact, and export AI coding-agent sessions into DistillKit-ready JSONL.

Exports retain a privacy-safe metadata envelope with opaque trace/project IDs,
source, model, day-bucketed lifecycle data, token totals, and tool/diff counts.
Filtering is based only on fields explicitly normalized by each source adapter,
never regex guesses over conversation text. Set `TRACES_METADATA_KEY` to keep
opaque identities stable across separate runs.

- **Repo:** [Infatoshi/traces](https://github.com/Infatoshi/traces)
- **Dataset:** [Infatoshi/traces](https://huggingface.co/datasets/Infatoshi/traces) (private)
- **How to run / develop:** see [AGENTS.md](./AGENTS.md) (same as `CLAUDE.md`)

MIT. Upstream extractor/redaction provenance: [ATTRIBUTION.md](./ATTRIBUTION.md).
