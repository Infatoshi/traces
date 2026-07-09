"""Codex extractor.

Vendored from 0xSero/ai-data-extraction/extract_codex.py (MIT).
Refactored to the Extractor protocol; discovery + parsing logic preserved.
"""
from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Iterator

from traces.extract.base import Conversation


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
    tool_results: list[dict] = []

    with open(session_file, 'r') as f:
        for line in f:
            try:
                obj = json.loads(line)
                event_type = obj.get('type')

                if event_type == 'session_meta':
                    session_meta = obj.get('payload', {})

                elif event_type == 'event_msg':
                    payload = obj.get('payload', {})
                    payload_type = payload.get('type')

                    if payload_type == 'user_message':
                        message_text = payload.get('message', '').strip()
                        if message_text:
                            msg = {
                                'role': 'user',
                                'content': message_text,
                                'timestamp': obj.get('timestamp')
                            }

                            # Add context if available
                            if 'context' in payload:
                                msg['context'] = payload['context']

                            messages.append(msg)

                    elif payload_type == 'agent_message':
                        message_text = payload.get('message', '').strip()
                        if message_text:
                            msg = {
                                'role': 'assistant',
                                'content': message_text,
                                'timestamp': obj.get('timestamp')
                            }

                            # Add model info if available
                            if 'model' in payload:
                                msg['model'] = payload['model']

                            messages.append(msg)

                    elif payload_type == 'tool_use':
                        # Code execution, file edits, etc.
                        tool_use = {
                            'type': 'tool_use',
                            'tool': payload.get('tool'),
                            'input': payload.get('input'),
                            'timestamp': obj.get('timestamp')
                        }
                        tool_results.append(tool_use)

                    elif payload_type == 'tool_result':
                        # Results from tool execution (diffs, outputs, etc.)
                        tool_result = {
                            'type': 'tool_result',
                            'tool': payload.get('tool'),
                            'output': payload.get('output'),
                            'timestamp': obj.get('timestamp')
                        }
                        tool_results.append(tool_result)

                    elif payload_type == 'diff':
                        # Code diffs
                        diff = {
                            'type': 'diff',
                            'file': payload.get('file'),
                            'diff': payload.get('diff'),
                            'timestamp': obj.get('timestamp')
                        }
                        tool_results.append(diff)

            except json.JSONDecodeError:
                continue

    if messages:
        conv: Conversation = {
            'messages': messages,
            'session_id': session_meta.get('id'),
            'cwd': session_meta.get('cwd'),
            'source': 'codex',
            'session_file': str(session_file),
            'timestamp': session_meta.get('timestamp')
        }

        if tool_results:
            conv['tool_results'] = tool_results

        return conv

    return None


def find_all_codex_sessions(installation: Path) -> list[Path]:
    """Find all Codex session files in an installation."""
    session_files: list[Path] = []

    # Check for sessions directory
    sessions_dir = installation / 'sessions'
    if sessions_dir.exists():
        # Sessions are organized by date: YYYY/MM/DD/rollout-*.jsonl
        session_files.extend(list(sessions_dir.rglob('rollout-*.jsonl')))

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
