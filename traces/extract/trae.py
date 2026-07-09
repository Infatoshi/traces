"""Trae extractor.

Vendored from 0xSero/ai-data-extraction/extract_trae.py (MIT).
Refactored to the Extractor protocol; discovery + parsing logic preserved.
"""
from __future__ import annotations

import json
import os
import platform
import sqlite3
from pathlib import Path
from typing import Iterator

from traces.extract.base import Conversation


def find_trae_installations(home: Path | None = None) -> list[Path]:
    """Find all Trae installation directories"""
    home = home or Path.home()

    locations: list[Path] = []

    # Search patterns for Trae directories
    trae_patterns = ['trae', '.trae', 'Trae']

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

        for pattern in trae_patterns:
            trae_dir = base_dir / pattern
            if trae_dir.exists():
                locations.append(trae_dir)

    return list({p.resolve() for p in locations})


def extract_conversation_from_data(data, source, source_file) -> Conversation | None:
    """Extract conversation from data object"""
    if not isinstance(data, dict):
        return None

    messages = []

    # Try different formats
    if 'messages' in data:
        messages = data['messages']
    elif 'conversation' in data:
        conv_data = data['conversation']
        if isinstance(conv_data, list):
            for item in conv_data:
                if isinstance(item, dict) and 'role' in item:
                    messages.append(item)

    if messages:
        return {
            'messages': messages,
            'source': source,
            'source_file': source_file,
            **{k: v for k, v in data.items() if k not in ['messages', 'conversation']}
        }

    return None


def extract_from_jsonl(jsonl_file, source) -> list[Conversation]:
    """Extract conversations from JSONL format"""
    conversations: list[Conversation] = []

    try:
        messages = []
        metadata = {}

        with open(jsonl_file, 'r') as f:
            for line in f:
                if not line.strip():
                    continue

                try:
                    obj = json.loads(line)

                    # Handle different JSONL formats
                    msg_type = obj.get('type', obj.get('role'))

                    if msg_type in ['user', 'user_message']:
                        content = obj.get('message', obj.get('content', ''))
                        msg = {
                            'role': 'user',
                            'content': content,
                            'timestamp': obj.get('timestamp')
                        }

                        # Add context
                        if 'context' in obj:
                            msg['context'] = obj['context']
                        if 'files' in obj:
                            msg['files'] = obj['files']

                        messages.append(msg)

                    elif msg_type in ['assistant', 'agent', 'agent_message']:
                        content = obj.get('message', obj.get('content', ''))
                        msg = {
                            'role': 'assistant',
                            'content': content,
                            'timestamp': obj.get('timestamp')
                        }

                        # Add tool use / diffs
                        if 'tool_use' in obj:
                            msg['tool_use'] = obj['tool_use']
                        if 'diffs' in obj:
                            msg['diffs'] = obj['diffs']
                        if 'edits' in obj:
                            msg['edits'] = obj['edits']

                        messages.append(msg)

                    elif msg_type == 'metadata':
                        metadata.update(obj.get('data', {}))

                except json.JSONDecodeError:
                    continue

        if messages:
            conversations.append({
                'messages': messages,
                'source': source,
                'source_file': str(jsonl_file),
                **metadata
            })

    except Exception as e:  # noqa: BLE001 - upstream prints but continues
        print(f"Error processing {jsonl_file}: {e}")

    return conversations


def extract_from_sqlite(db_file, source) -> list[Conversation]:
    """Extract conversations from SQLite database"""
    conversations: list[Conversation] = []

    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()

        # Try common table/key patterns
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]

            # Check for common patterns
            if 'ItemTable' in tables:
                cursor.execute("SELECT key, value FROM ItemTable WHERE key LIKE '%chat%' OR key LIKE '%conversation%' OR key LIKE '%agent%'")
                results = cursor.fetchall()

                for key, value in results:
                    if not value:
                        continue

                    try:
                        data = json.loads(value)
                        conv = extract_conversation_from_data(data, source, str(db_file))
                        if conv:
                            conversations.append(conv)
                    except Exception:  # noqa: BLE001 - upstream swallows per-row errors
                        continue

        except Exception:  # noqa: BLE001 - upstream swallows schema errors
            pass

        conn.close()

    except Exception:  # noqa: BLE001 - upstream swallows DB-level errors
        pass

    return conversations


def extract_trae_data(installation: Path) -> list[Conversation]:
    """Extract Trae conversations from various storage formats"""
    conversations: list[Conversation] = []

    # Trae might use different storage formats
    # Check for common patterns

    # 1. Check for JSONL files in projects directory
    projects_dir = installation / 'projects'
    if projects_dir.exists():
        for project in projects_dir.iterdir():
            if project.is_dir():
                for jsonl_file in project.glob('*.jsonl'):
                    convs = extract_from_jsonl(jsonl_file, 'trae')
                    conversations.extend(convs)

    # 2. Check for SQLite databases
    for db_file in installation.rglob('*.db'):
        convs = extract_from_sqlite(db_file, 'trae')
        conversations.extend(convs)

    for vscdb_file in installation.rglob('*.vscdb'):
        convs = extract_from_sqlite(vscdb_file, 'trae')
        conversations.extend(convs)

    # 3. Check for sessions directory
    sessions_dir = installation / 'sessions'
    if sessions_dir.exists():
        for jsonl_file in sessions_dir.rglob('*.jsonl'):
            convs = extract_from_jsonl(jsonl_file, 'trae')
            conversations.extend(convs)

    for conv in conversations:
        conv['installation'] = str(installation)

    return conversations


class TraeExtractor:
    source = "trae"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    def find_installations(self) -> list[Path]:
        return find_trae_installations(self.root)

    def extract(self, install_dir: Path) -> Iterator[Conversation]:
        for conv in extract_trae_data(install_dir):
            yield conv
