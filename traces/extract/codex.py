"""Codex extractor.

Vendored from 0xSero/ai-data-extraction/extract_codex.py (MIT).
Refactored to the Extractor protocol; discovery + parsing logic preserved.
"""
from __future__ import annotations

import io
import json
import os
import platform
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO

from traces.extract.base import Conversation


def _open_rollout(session_file: Path) -> TextIO:
    """Open a rollout; Codex 0.157+ compresses finished sessions to ``rollout-*.jsonl.zst``."""
    if session_file.suffix != '.zst':
        return open(session_file, 'r')
    try:
        from compression import zstd  # Python 3.14+
    except ImportError:
        import zstandard

        # The returned wrapper owns and closes the file; zstandard.open() would stop after one frame.
        fh = open(session_file, 'rb')  # noqa: SIM115
        raw = zstandard.ZstdDecompressor().stream_reader(fh, read_across_frames=True)
        return io.TextIOWrapper(raw, encoding='utf-8')
    return zstd.open(session_file, 'rt', encoding='utf-8')


def find_codex_installations(home: Path | None = None) -> list[Path]:
    """Find all Codex installation directories."""
    home = home or Path.home()

    locations: list[Path] = []

    # Search patterns for Codex directories
    codex_patterns = [
        'codex', 'codex-local', '.codex', '.codex-local'
    ]

    base_dirs = [
        home / "Library/Application Support",
        home / ".config",
        home / ".local/share",
        home,
    ]
    if platform.system() == "Windows" and home == Path.home():
        base_dirs.extend([
            Path(os.environ.get('APPDATA', home / 'AppData/Roaming')),
            Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local')),
        ])

    for base_dir in base_dirs:
        if not base_dir.exists():
            continue

        for pattern in codex_patterns:
            codex_dir = base_dir / pattern
            if codex_dir.exists():
                locations.append(codex_dir)

    return list({p.resolve() for p in locations})


def extract_codex_session(session_file: Path) -> Conversation | None:
    """Extract conversation from a Codex rollout file with full context."""
    messages: list[dict] = []
    session_meta: dict = {}
    model: str | None = None
    status: str | None = None
    updated_at: str | None = None
    diff_count = 0
    tool_events: list[dict] = []
    call_names: dict[str, str] = {}
    token_usage: dict[str, int | float] = {}
    message_origins: dict[tuple[str, str], set[str]] = defaultdict(set)
    message_indexes: dict[tuple[str, str], int] = {}

    def append_message(
        role: str, content: object, timestamp: object, *, origin: str,
        message_model: object = None,
    ) -> None:
        if isinstance(content, list):
            content = "\n".join(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict)
                and item.get("type") in {"input_text", "output_text", "text"}
            )
        text = str(content or "").strip()
        if not text:
            return
        key = (role, text)
        if message_origins[key] and origin not in message_origins[key]:
            message_origins[key].add(origin)
            existing = messages[message_indexes[key]]
            if message_model and not existing.get("model"):
                existing["model"] = message_model
            return
        message_origins[key].add(origin)
        message = {"role": role, "content": text, "timestamp": timestamp}
        if message_model:
            message["model"] = message_model
        message_indexes[key] = len(messages)
        messages.append(message)

    def append_tool_call(call: dict, timestamp: object) -> None:
        call_id = call.get('id')
        if isinstance(call_id, str) and isinstance(call.get('name'), str):
            call_names[call_id] = call['name']
        if (
            messages
            and messages[-1].get('role') == 'assistant'
            and not messages[-1].get('content')
            and messages[-1].get('tool_calls')
        ):
            messages[-1]['tool_calls'].append(call)
            return
        messages.append({
            'role': 'assistant',
            'content': '',
            'model': model,
            'timestamp': timestamp,
            'tool_calls': [call],
        })

    def append_tool_result(result: dict, timestamp: object) -> None:
        call_id = result.get('tool_call_id')
        if (
            isinstance(call_id, str)
            and call_id in call_names
            and not result.get('tool')
        ):
            result['tool'] = call_names[call_id]
        messages.append({
            'role': 'tool',
            'content': '',
            'timestamp': timestamp,
            'tool_result': result,
        })

    with _open_rollout(session_file) as f:
        for line in f:
            try:
                obj = json.loads(line)
                event_type = obj.get('type')
                if isinstance(obj.get('timestamp'), str):
                    updated_at = obj['timestamp']

                if event_type == 'session_meta':
                    # Subagent rollout files can embed copied parent headers
                    # after their own header. The first header identifies the
                    # file being extracted; later headers are conversation data.
                    if not session_meta:
                        session_meta = obj.get('payload', {})

                elif event_type == 'turn_context':
                    payload = obj.get('payload', {})
                    if isinstance(payload.get('model'), str):
                        model = payload['model']

                elif event_type == 'event_msg':
                    payload = obj.get('payload', {})
                    payload_type = payload.get('type')

                    if payload_type == 'user_message':
                        append_message(
                            'user', payload.get('message'), obj.get('timestamp'),
                            origin='event_msg',
                        )

                    elif payload_type == 'agent_message':
                        append_message(
                            'assistant', payload.get('message'), obj.get('timestamp'),
                            origin='event_msg',
                            message_model=payload.get('model') or model,
                        )

                    elif payload_type == 'tool_use':
                        event = {
                            'type': 'tool_use',
                            'tool': payload.get('tool'),
                            'input': payload.get('input'),
                            'timestamp': obj.get('timestamp'),
                        }
                        tool_events.append(event)
                        append_tool_call({
                            'id': payload.get('call_id') or payload.get('id'),
                            'name': payload.get('tool'),
                            'input': payload.get('input'),
                            'kind': 'function',
                        }, obj.get('timestamp'))

                    elif payload_type == 'tool_result':
                        event = {
                            'type': 'tool_result',
                            'tool': payload.get('tool'),
                            'output': payload.get('output'),
                            'timestamp': obj.get('timestamp'),
                        }
                        tool_events.append(event)
                        append_tool_result({
                            'tool_call_id': payload.get('call_id') or payload.get('id'),
                            'tool': payload.get('tool'),
                            'output': payload.get('output'),
                            'status': payload.get('status'),
                        }, obj.get('timestamp'))

                    elif payload_type == 'diff':
                        diff_count += 1
                        tool_events.append({
                            'type': 'diff',
                            'file': payload.get('file'),
                            'diff': payload.get('diff'),
                            'timestamp': obj.get('timestamp'),
                        })

                    elif payload_type == 'patch_apply_end':
                        changes = payload.get('changes')
                        if isinstance(changes, (dict, list)):
                            diff_count += len(changes)
                            tool_events.append({
                                'type': 'patch_apply_end',
                                'changes': changes,
                                'timestamp': obj.get('timestamp'),
                            })

                    elif payload_type == 'task_complete':
                        status = 'completed'

                    elif payload_type == 'turn_aborted':
                        status = 'aborted'

                    elif payload_type == 'token_count':
                        info = payload.get('info')
                        totals = info.get('total_token_usage') if isinstance(info, dict) else None
                        if isinstance(totals, dict):
                            for source_key, target_key in (
                                ('input_tokens', 'input'),
                                ('cached_input_tokens', 'cached_input'),
                                ('output_tokens', 'output'),
                                ('reasoning_output_tokens', 'reasoning'),
                                ('total_tokens', 'total'),
                            ):
                                value = totals.get(source_key)
                                if isinstance(value, (int, float)):
                                    token_usage[target_key] = value

                elif event_type == 'response_item':
                    payload = obj.get('payload', {})
                    payload_type = payload.get('type')
                    if payload_type == 'message' and payload.get('role') in {
                        'user', 'assistant', 'system', 'developer'
                    }:
                        role = payload['role']
                        append_message(
                            role, payload.get('content'), obj.get('timestamp'),
                            origin='response_item',
                            message_model=model if role == 'assistant' else None,
                        )
                    elif payload_type in {'function_call', 'custom_tool_call'}:
                        raw_input = (
                            payload.get('arguments')
                            if payload_type == 'function_call'
                            else payload.get('input')
                        )
                        parsed_input = raw_input
                        if payload_type == 'function_call' and isinstance(raw_input, str):
                            try:
                                parsed_input = json.loads(raw_input)
                            except json.JSONDecodeError:
                                pass
                        append_tool_call({
                            'id': payload.get('call_id'),
                            'name': payload.get('name'),
                            'input': parsed_input,
                            'kind': (
                                'function'
                                if payload_type == 'function_call'
                                else 'custom'
                            ),
                        }, obj.get('timestamp'))
                    elif payload_type in {'function_call_output', 'custom_tool_call_output'}:
                        append_tool_result({
                            'tool_call_id': payload.get('call_id'),
                            'output': payload.get('output'),
                            'status': payload.get('status'),
                        }, obj.get('timestamp'))

            except json.JSONDecodeError:
                continue

    if messages:
        conv: Conversation = {
            'messages': messages,
            'session_id': session_meta.get('id'),
            'cwd': session_meta.get('cwd'),
            'project_path': session_meta.get('cwd'),
            'source': 'codex',
            'session_file': str(session_file),
            'timestamp': session_meta.get('timestamp'),
            'created_at': session_meta.get('timestamp'),
            'updated_at': updated_at,
            'version': session_meta.get('cli_version'),
        }

        if model:
            conv['model'] = model
        if status:
            conv['status'] = status
        if token_usage:
            conv['token_usage'] = token_usage
        if diff_count:
            conv['diff_count'] = diff_count
        if tool_events:
            conv['tool_results'] = tool_events

        return conv

    return None


def find_all_codex_sessions(installation: Path) -> list[Path]:
    """Find all Codex session files in an installation."""
    session_files: list[Path] = []

    # Check for sessions directory
    sessions_dir = installation / 'sessions'
    if sessions_dir.exists():
        # Sessions are organized by date: YYYY/MM/DD/rollout-*.jsonl, or
        # rollout-*.jsonl.zst once Codex compresses them. A staged store can
        # hold both forms of one session; read the newer, the .zst on a tie.
        newest: dict[Path, Path] = {}
        for path in [*sessions_dir.rglob('rollout-*.jsonl'), *sessions_dir.rglob('rollout-*.jsonl.zst')]:
            key = path.with_suffix('') if path.suffix == '.zst' else path
            rank = (path.stat().st_mtime, path.suffix == '.zst')
            if key not in newest or rank > (newest[key].stat().st_mtime, newest[key].suffix == '.zst'):
                newest[key] = path
        session_files.extend(sorted(newest.values()))

    # Also check for project-based structure
    projects_dir = installation / 'projects'
    if projects_dir.exists():
        session_files.extend(list(projects_dir.rglob('*.jsonl')))

    return session_files


def extract_codex_conversations(installation: Path) -> list[Conversation]:
    """Extract all conversations from a Codex installation."""
    conversations: list[Conversation] = []
    for session_file in find_all_codex_sessions(installation):
        conv = extract_codex_session(session_file)
        if conv:
            conv['installation'] = str(installation)
            conversations.append(conv)
    return conversations


class CodexExtractor:
    source = "codex"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    def find_installations(self) -> list[Path]:
        return find_codex_installations(self.root)

    def extract(self, install_dir: Path) -> Iterator[Conversation]:
        for conv in extract_codex_conversations(install_dir):
            yield conv
