"""CLI entrypoint: traces."""
from __future__ import annotations

from pathlib import Path

import click

EXPORT_VARIANTS = [
    "messages", "sharegpt", "execution", "steps", "both", "all"
]


@click.group()
def cli() -> None:
    """traces: extract, redact, export AI coding-agent traces."""


@cli.command("extract")
@click.option("--sources", default=None, help="Comma-separated source names. Default: all available.")
@click.option("--root", default="~", help="Home root to scan.")
@click.option("--out", default="./output", type=click.Path(), help="Output directory.")
def extract_cmd(sources: str | None, root: str, out: str) -> None:
    """Extract raw conversations from installed agents."""
    from traces.extract.discovery import run_extract
    src_list = sources.split(",") if sources else None
    counts = run_extract(src_list, Path(root).expanduser(), Path(out))
    for k, v in counts.items():
        click.echo(f"{k}: {v} records")


@cli.command("redact")
@click.option("--in", "in_dir", default="./output/raw_extracted", type=click.Path())
@click.option("--out", "out_dir", default="./output/redacted", type=click.Path())
@click.option("--allow-public-urls", is_flag=True)
@click.option("--allow-domain", "allowed_domains", multiple=True)
@click.option("--private-term", "private_terms", multiple=True)
@click.option("--private-domain", "private_domains", multiple=True)
@click.option("--user", "user_name", default=None)
@click.option("--home", "home_dir", default=None)
@click.option("--privacy-filter", is_flag=True,
              help="Enable the transformers-based privacy filter (requires torch+transformers).")
@click.option("--privacy-filter-device", "privacy_filter_device", default="auto",
              help="Device for the privacy filter: auto, cpu, cuda, cuda:N.")
@click.option("--gitleaks", is_flag=True,
              help="Run a final gitleaks scan over the redacted tree (requires gitleaks).")
@click.option("--gitleaks-fix", is_flag=True,
              help="Iteratively redact gitleaks findings in place before the final scan.")
@click.option("--llm-residue", "llm_residue_url", default=None,
              help="OpenAI-compatible base URL for the LLM residue pass, e.g. http://localhost:8000.")
def redact_cmd(in_dir: str, out_dir: str, allow_public_urls: bool,
               allowed_domains: tuple, private_terms: tuple, private_domains: tuple,
               user_name: str | None, home_dir: str | None,
               privacy_filter: bool, privacy_filter_device: str,
               gitleaks: bool, gitleaks_fix: bool,
               llm_residue_url: str | None) -> None:
    """Redact PII/secrets from extracted conversations."""
    from traces.config import RedactorConfig
    from traces.redact.pipeline import run_redact
    config = RedactorConfig(
        user_name=user_name, home_dir=home_dir,
        allow_public_urls=allow_public_urls,
        allowed_domains=list(allowed_domains),
        private_terms=list(private_terms),
        private_domains=list(private_domains),
        privacy_filter=privacy_filter,
        privacy_filter_device=privacy_filter_device,
        gitleaks=gitleaks,
        gitleaks_fix=gitleaks_fix,
        llm_residue_url=llm_residue_url,
    )
    report = run_redact(Path(in_dir), Path(out_dir), config)
    click.echo(f"Redacted. Counts: {dict(report['counts'])}")


@cli.command("export")
@click.option("--in", "in_dir", default="./output/redacted", type=click.Path())
@click.option("--out", "out_dir", default="./output/export", type=click.Path())
@click.option("--variant", default="both", type=click.Choice(EXPORT_VARIANTS))
@click.option("--min-turns", type=int, default=None)
@click.option("--max-turns", type=int, default=None)
@click.option("--min-assistant-chars", type=int, default=None)
@click.option("--include-source", "include_sources", multiple=True,
              help="Include source names; shell globs are accepted.")
@click.option("--drop-sources", default=None, help="Comma-separated source names to drop")
@click.option("--project", "projects", multiple=True,
              help="Include normalized project IDs or paths; shell globs are accepted.")
@click.option("--model", "models", multiple=True,
              help="Include model names; shell globs are accepted.")
@click.option("--status", "statuses", multiple=True,
              help="Include explicit normalized statuses.")
@click.option("--reasoning-effort", "reasoning_efforts", multiple=True,
              help="Include explicit normalized reasoning-effort levels.")
@click.option("--session-kind", "session_kinds", multiple=True,
              help="Include explicit normalized session kinds.")
@click.option("--since", default=None, help="Include traces created at or after this ISO timestamp.")
@click.option("--until", default=None, help="Include traces created at or before this ISO timestamp.")
@click.option("--require-tools", is_flag=True)
@click.option("--require-diffs", is_flag=True)
@click.option("--require-complete-tools", is_flag=True,
              help="Require every native tool call/result ID to be matched.")
@click.option("--require-final-assistant", is_flag=True,
              help="Require the execution to end with assistant text.")
@click.option("--dedup", is_flag=True)
def export_cmd(in_dir: str, out_dir: str, variant: str, min_turns: int | None,
               max_turns: int | None, min_assistant_chars: int | None,
               include_sources: tuple[str, ...], drop_sources: str | None,
               projects: tuple[str, ...], models: tuple[str, ...],
               statuses: tuple[str, ...], reasoning_efforts: tuple[str, ...],
               session_kinds: tuple[str, ...], since: str | None, until: str | None,
               require_tools: bool, require_diffs: bool,
               require_complete_tools: bool, require_final_assistant: bool,
               dedup: bool) -> None:
    """Export redacted conversations to DistillKit-ready formats."""
    from traces.config import ExportConfig
    from traces.export.execution import export_assistant_steps, export_execution
    from traces.export.messages import export_messages
    from traces.export.sharegpt import export_sharegpt
    config = ExportConfig(
        variant=variant, min_turns=min_turns, max_turns=max_turns,
        min_assistant_chars=min_assistant_chars,
        include_sources=list(include_sources),
        drop_sources=[item.strip() for item in drop_sources.split(",") if item.strip()]
        if drop_sources else [],
        projects=list(projects), models=list(models), statuses=list(statuses),
        reasoning_efforts=list(reasoning_efforts), session_kinds=list(session_kinds),
        since=since, until=until, require_tools=require_tools,
        require_diffs=require_diffs,
        require_complete_tools=require_complete_tools,
        require_final_assistant=require_final_assistant,
        dedup=dedup,
    )
    if variant in ("messages", "both", "all"):
        s = export_messages(Path(in_dir), Path(out_dir) / "messages.jsonl", config)
        click.echo(f"messages.jsonl: {s}")
    if variant in ("sharegpt", "both", "all"):
        s = export_sharegpt(Path(in_dir), Path(out_dir) / "sharegpt.jsonl", config)
        click.echo(f"sharegpt.jsonl: {s}")
    if variant in ("execution", "steps", "all"):
        s = export_execution(
            Path(in_dir), Path(out_dir) / "execution.jsonl", config
        )
        click.echo(f"execution.jsonl: {s}")
    if variant in ("steps", "all"):
        s = export_assistant_steps(
            Path(in_dir), Path(out_dir) / "assistant_steps.jsonl", config
        )
        click.echo(f"assistant_steps.jsonl: {s}")


@cli.command("verify")
@click.option("--in", "in_dir", default="./output/redacted", type=click.Path())
def verify_cmd(in_dir: str) -> None:
    """Scan for leftover PII/secrets that survived redaction."""
    from traces.verify.scanner import scan
    report = scan(Path(in_dir))
    if report.ok:
        click.echo(f"OK: {report.files_scanned} files scanned, no leftovers.")
    else:
        click.echo(f"FAIL: {len(report.leftovers)} leftover(s) found:")
        for f in report.leftovers[:20]:
            click.echo(f"  {f.path}:{f.line_no} [{f.category}] {f.snippet}")
        raise click.exceptions.Exit(1)


@cli.command("clean")
@click.option("--in", "in_dir", default="./output/redacted", type=click.Path())
@click.option("--out", "out_dir", default="./output/clean_redacted", type=click.Path())
def clean_cmd(in_dir: str, out_dir: str) -> None:
    """Drop complete JSONL rows that fail the independent verifier."""
    import json as _json

    from traces.verify.scanner import clean_tree, scan

    output = Path(out_dir)
    report = clean_tree(Path(in_dir), output)
    verification = scan(output)
    if not verification.ok:
        raise click.ClickException(
            f"clean output still has {len(verification.leftovers)} leftover(s)"
        )
    click.echo(_json.dumps(report, indent=2))


@cli.command("publish")
@click.option("--repo", "repo_id", required=True,
              help="HF Hub dataset repo id, e.g. user/my-traces.")
@click.option("--in", "in_dir", default="./output/export", type=click.Path())
@click.option("--variant", default="both", type=click.Choice(EXPORT_VARIANTS))
@click.option("--private", is_flag=True, help="Create the dataset as private.")
def publish_cmd(repo_id: str, in_dir: str, variant: str, private: bool) -> None:
    """Upload exported variants + an auto-generated dataset card to HuggingFace Hub."""
    from traces.verify.scanner import scan

    export_dir = Path(in_dir)
    report = scan(export_dir)
    if not report.ok:
        click.echo(
            f"Refusing publish: {len(report.leftovers)} leftover(s) found "
            f"in {report.files_scanned} file(s)."
        )
        for finding in report.leftovers[:20]:
            click.echo(
                f"  {finding.path}:{finding.line_no} "
                f"[{finding.category}] {finding.snippet}"
            )
        raise click.exceptions.Exit(1)

    from traces.publish.huggingface import upload_variant
    url = upload_variant(export_dir, repo_id, variant=variant, private=private)
    click.echo(f"Published: {url}")


@cli.command("stats")
@click.option("--in", "in_dir", default="./output/export", type=click.Path())
def stats_cmd(in_dir: str) -> None:
    """Report corpus metrics across the exported *.jsonl files."""
    import json as _json

    from traces.stats import corpus_stats
    report = corpus_stats(Path(in_dir))
    click.echo(_json.dumps(report, indent=2))


@cli.command("sample")
@click.option("--in", "in_dir", default="./output/export", type=click.Path())
@click.option("--n", "n", default=10, type=int, help="Number of conversations to sample.")
@click.option("--seed", default=0, type=int, help="RNG seed for reproducibility.")
@click.option("--out", "out_dir", default=None, type=click.Path(),
              help="Optional output directory; writes sample.jsonl when provided.")
def sample_cmd(in_dir: str, n: int, seed: int, out_dir: str | None) -> None:
    """Sample N random conversations for pre-publish inspection."""
    from traces.sample import sample_conversations
    out_path = Path(out_dir) if out_dir else None
    rows = sample_conversations(Path(in_dir), n, seed=seed, out_dir=out_path)
    click.echo(f"Sampled {len(rows)} conversation(s).")
    if out_path is not None:
        click.echo(f"Wrote {out_path / 'sample.jsonl'}")


@cli.command("run")
@click.option("--sources", default=None)
@click.option("--root", default="~")
@click.option("--out", default="./output", type=click.Path())
@click.option("--variant", default="both", type=click.Choice(EXPORT_VARIANTS))
@click.option("--user", "user_name", default=None)
@click.option("--home", "home_dir", default=None)
@click.pass_context
def run_cmd(ctx, sources: str | None, root: str, out: str, variant: str,
            user_name: str | None, home_dir: str | None) -> None:
    """Chain extract -> redact -> export -> MANIFEST.json.

    Calls the underlying stage functions directly (rather than ctx.invoke on the
    subcommands) so the per-stage return values (extract counts, redaction
    report, export summaries) are available to feed into the MANIFEST writer.
    """
    from traces.config import ExportConfig, RedactorConfig
    from traces.export.execution import export_assistant_steps, export_execution
    from traces.export.messages import export_messages
    from traces.export.sharegpt import export_sharegpt
    from traces.extract.discovery import run_extract
    from traces.manifest import write_manifest
    from traces.redact.pipeline import run_redact

    out_path = Path(out)
    src_list = sources.split(",") if sources else None
    root_path = Path(root).expanduser()

    # Stage 1: extract.
    extract_counts = run_extract(src_list, root_path, out_path)
    for k, v in extract_counts.items():
        click.echo(f"extract {k}: {v} records")

    # Stage 2: redact.
    redact_config = RedactorConfig(user_name=user_name, home_dir=home_dir)
    redact_report = run_redact(
        out_path / "raw_extracted", out_path / "redacted", redact_config
    )
    click.echo(f"redact: {dict(redact_report['counts'])}")

    # Stage 3: export.
    export_config = ExportConfig(variant=variant)
    export_summaries: dict[str, dict] = {}
    export_dir = out_path / "export"
    if variant in ("messages", "both", "all"):
        export_summaries["messages"] = export_messages(
            out_path / "redacted", export_dir / "messages.jsonl", export_config
        )
        click.echo(f"export messages: {export_summaries['messages']['rows']} rows")
    if variant in ("sharegpt", "both", "all"):
        export_summaries["sharegpt"] = export_sharegpt(
            out_path / "redacted", export_dir / "sharegpt.jsonl", export_config
        )
        click.echo(f"export sharegpt: {export_summaries['sharegpt']['pairs']} pairs")
    if variant in ("execution", "steps", "all"):
        export_summaries["execution"] = export_execution(
            out_path / "redacted", export_dir / "execution.jsonl", export_config
        )
        click.echo(
            f"export execution: {export_summaries['execution']['rows']} rows"
        )
    if variant in ("steps", "all"):
        export_summaries["steps"] = export_assistant_steps(
            out_path / "redacted",
            export_dir / "assistant_steps.jsonl",
            export_config,
        )
        click.echo(f"export steps: {export_summaries['steps']['rows']} rows")

    # Stage 4: MANIFEST.json.
    config_snapshot = {
        "variant": variant,
        "redaction": redact_report["config"],
        "export": {"min_turns": None, "max_turns": None,
                   "min_assistant_chars": None, "include_sources": [],
                   "drop_sources": [], "projects": [], "models": [],
                   "statuses": [], "reasoning_efforts": [], "session_kinds": [],
                   "since": None, "until": None,
                   "require_tools": False, "require_diffs": False,
                   "require_complete_tools": False,
                   "require_final_assistant": False,
                   "dedup": False},
    }
    manifest_path = write_manifest(
        out_path, extract_counts, redact_report, export_summaries, config_snapshot
    )
    click.echo(f"Wrote {manifest_path}")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
