"""Gemini CLI extractor.

Vendored from 0xSero/ai-data-extraction/extract_gemini.py (MIT).
Refactored to the Extractor protocol; discovery + parsing logic preserved.
"""
from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Iterator

from traces.extract.base import Conversation


def find_gemini_installations(home: Path | None = None) -> list[Path]:
    """Find all Gemini CLI installation directories."""
    home = home or Path.home()

    locations: list[Path] = []

    # Search patterns for Gemini directories
    gemini_patterns = [
        'gemini', '.gemini'
    ]

    base_dirs = [
        home,
        home / ".gemini",
        home / ".config",
        home / ".config/gemini",
        home / ".local/share/gemini",
    ]
    if platform.system() == "Windows" and home == Path.home():
        base_dirs.extend([
            Path(os.environ.get('USERPROFILE', home)) / ".gemini",
            Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local')) / "gemini",
        ])

    for base_dir in base_dirs:
        if not base_dir.exists():
            continue

        for pattern in gemini_patterns:
            gemini_dir = base_dir / pattern
            if gemini_dir.exists():
                locations.append(gemini_dir)

    return list({p.resolve() for p in locations})


def extract_gemini_session(session_file: Path) -> Conversation | None:
    """Extract conversation from a Gemini CLI session file."""
    try:
        with open(session_file, 'r') as f:
            data = json.load(f)

        if 'messages' not in data or not data['messages']:
            return None

        messages = []

        for msg in data['messages']:
            msg_type = msg.get('type')
            content = msg.get('content', '')

            if msg_type == 'user':
                normalized_msg = {
                    'role': 'user',
                    'content': content,
                    'timestamp': msg.get('timestamp')
                }
                messages.append(normalized_msg)

            elif msg_type == 'gemini':
                normalized_msg = {
                    'role': 'assistant',
                    'content': content,
                    'timestamp': msg.get('timestamp')
                }

                # Preserve Gemini-specific features
                if 'model' in msg:
                    normalized_msg['model'] = msg['model']

                if 'thoughts' in msg and msg['thoughts']:
                    normalized_msg['thoughts'] = msg['thoughts']

                if 'tokens' in msg and msg['tokens']:
                    normalized_msg['tokens'] = msg['tokens']

                messages.append(normalized_msg)

        if not messages:
            return None

        conv: Conversation = {
            'messages': messages,
            'source': 'gemini-cli',
            'session_id': data.get('sessionId'),
            'project_hash': data.get('projectHash'),
            'start_time': data.get('startTime'),
            'last_updated': data.get('lastUpdated'),
            'source_file': str(session_file)
        }

        return conv

    except (json.JSONDecodeError, KeyError, Exception):  # noqa: BLE001 - upstream swallows errors
        return None


def find_all_gemini_sessions(installation: Path) -> list[Path]:
    """Find all Gemini CLI session files in an installation."""
    session_files: list[Path] = []

    # Search for session files in tmp/[hash]/chats/session-*.json pattern
    tmp_dir = installation / 'tmp'
    if tmp_dir.exists():
        # Find all session-*.json files under tmp/*/chats/
        session_files.extend(list(tmp_dir.rglob('chats/session-*.json')))

    return session_files


def extract_gemini_conversations(installation: Path) -> list[Conversation]:
    """Extract all conversations from a Gemini CLI installation."""
    conversations: list[Conversation] = []
    for session_file in find_all_gemini_sessions(installation):
        conv = extract_gemini_session(session_file)
        if conv:
            conv['installation'] = str(installation)
            conversations.append(conv)
    return conversations


class GeminiExtractor:
    source = "gemini"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    def find_installations(self) -> list[Path]:
        return find_gemini_installations(self.root)

    def extract(self, install_dir: Path) -> Iterator[Conversation]:
        for conv in extract_gemini_conversations(install_dir):
            yield conv
