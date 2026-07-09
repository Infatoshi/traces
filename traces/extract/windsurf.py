"""Windsurf extractor.

Vendored from 0xSero/ai-data-extraction/extract_windsurf.py (MIT).
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


def find_windsurf_installations(home: Path | None = None) -> list[Path]:
    """Find all Windsurf installation directories"""
    home = home or Path.home()

    locations: list[Path] = []

    # Windsurf patterns
    windsurf_patterns = ['Windsurf', 'windsurf', '.windsurf']

    base_dirs = [
        home / "Library/Application Support",
        home / ".config",
        home / ".local/share",
    ]
    if platform.system() == "Windows" and home == Path.home():
        base_dirs.extend([
            Path(os.environ.get('APPDATA', home / 'AppData/Roaming')),
            Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local')),
        ])

    for base_dir in base_dirs:
        if not base_dir.exists():
            continue

        for pattern in windsurf_patterns:
            windsurf_dir = base_dir / pattern
            if windsurf_dir.exists():
                locations.append(windsurf_dir)

    return list({p.resolve() for p in locations})


def extract_windsurf_chat(db_path, workspace_id) -> list[Conversation]:
    """Extract Windsurf chat conversations (similar to VSCode/Cursor format)"""
    conversations: list[Conversation] = []

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Try different key patterns
        keys_to_try = [
            'workbench.panel.aichat.view.aichat.chatdata',
            'aiChat.chatdata',
            'chat.data',
            'cascade.chatdata'  # Windsurf might use Cascade branding
        ]

        for key in keys_to_try:
            try:
                cursor.execute("SELECT value FROM ItemTable WHERE key = ?", (key,))
                result = cursor.fetchone()

                if result:
                    data = json.loads(result[0])

                    if 'tabs' in data:
                        for tab in data['tabs']:
                            if 'bubbles' in tab and len(tab['bubbles']) > 0:
                                messages = []
                                code_context = []

                                for bubble in tab['bubbles']:
                                    bubble_type = bubble.get('type')
                                    content = bubble.get('rawText', bubble.get('text', ''))

                                    msg = {
                                        'role': 'user' if bubble_type == 'user' else 'assistant',
                                        'content': content
                                    }

                                    # Extract code context
                                    if 'selections' in bubble and bubble['selections']:
                                        ctx = []
                                        for sel in bubble['selections']:
                                            if 'uri' in sel and 'fsPath' in sel['uri']:
                                                ctx.append({
                                                    'file': sel['uri']['fsPath'],
                                                    'code': sel.get('text', sel.get('rawText', '')),
                                                    'range': sel.get('range')
                                                })
                                        if ctx:
                                            msg['code_context'] = ctx
                                            code_context.extend(ctx)

                                    # Extract diffs
                                    if 'suggestedDiffs' in bubble:
                                        msg['suggested_diffs'] = bubble['suggestedDiffs']

                                    messages.append(msg)

                                if messages:
                                    conversations.append({
                                        'messages': messages,
                                        'source': 'windsurf-chat',
                                        'chat_title': tab.get('chatTitle'),
                                        'tab_id': tab.get('tabId'),
                                        'workspace_id': workspace_id,
                                        'has_code_context': len(code_context) > 0
                                    })

                    break  # Found data, no need to try other keys

            except Exception:  # noqa: BLE001 - upstream swallows per-key errors
                continue

        conn.close()

    except Exception:  # noqa: BLE001 - upstream swallows DB-level errors
        pass

    return conversations


def extract_agent_conversation(data, key) -> Conversation | None:
    """Extract agent conversation from data object"""
    if not isinstance(data, dict):
        return None

    messages = []

    # Try different conversation formats
    if 'conversation' in data and isinstance(data['conversation'], list):
        for bubble in data['conversation']:
            bubble_type = bubble.get('type')
            text = bubble.get('text', '')

            if bubble_type == 1 or bubble.get('role') == 'user':
                msg = {
                    'role': 'user',
                    'content': text
                }

                # Add context
                if 'context' in bubble:
                    context = bubble['context']
                    if 'selections' in context:
                        ctx = []
                        for sel in context['selections']:
                            if 'uri' in sel and 'fsPath' in sel['uri']:
                                ctx.append({
                                    'file': sel['uri']['fsPath'],
                                    'code': sel.get('text', sel.get('rawText', '')),
                                    'range': sel.get('range')
                                })
                        if ctx:
                            msg['code_context'] = ctx

                messages.append(msg)

            elif bubble_type == 2 or bubble.get('role') == 'assistant':
                msg = {
                    'role': 'assistant',
                    'content': text
                }

                # Add diffs
                if 'suggestedCodeBlocks' in bubble:
                    msg['suggested_code_blocks'] = bubble['suggestedCodeBlocks']
                if 'diffHistories' in bubble:
                    msg['diff_histories'] = bubble['diffHistories']

                messages.append(msg)

    if messages:
        return {
            'messages': messages,
            'source': 'windsurf-agent',
            'name': data.get('name', 'Untitled'),
            'status': data.get('status'),
            'created_at': data.get('createdAt'),
            'updated_at': data.get('lastUpdatedAt')
        }

    return None


def extract_windsurf_agent(global_db_path) -> list[Conversation]:
    """Extract Windsurf agent/flow conversations"""
    conversations: list[Conversation] = []

    try:
        conn = sqlite3.connect(global_db_path)
        cursor = conn.cursor()

        # Try different table formats
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        # Check for cursorDiskKV (if Windsurf uses similar format to Cursor)
        if 'cursorDiskKV' in tables:
            cursor.execute("SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%' OR key LIKE 'agentData:%' OR key LIKE 'flowData:%'")
            results = cursor.fetchall()

            for key, value in results:
                if not value:
                    continue

                try:
                    data = json.loads(value)
                    conv = extract_agent_conversation(data, key)
                    if conv:
                        conversations.append(conv)
                except Exception:  # noqa: BLE001 - upstream swallows per-row errors
                    continue

        # Also check ItemTable
        if 'ItemTable' in tables:
            cursor.execute("SELECT key, value FROM ItemTable WHERE key LIKE '%agent%' OR key LIKE '%flow%' OR key LIKE '%cascade%'")
            results = cursor.fetchall()

            for key, value in results:
                if not value:
                    continue

                try:
                    data = json.loads(value)
                    conv = extract_agent_conversation(data, key)
                    if conv:
                        conversations.append(conv)
                except Exception:  # noqa: BLE001 - upstream swallows per-row errors
                    continue

        conn.close()

    except Exception:  # noqa: BLE001 - upstream swallows DB-level errors
        pass

    return conversations


def extract_windsurf_conversations(installation: Path) -> list[Conversation]:
    """Extract conversations from one Windsurf installation directory.

    Mirrors upstream ``main()``'s per-installation walk: workspace-storage
    chat DBs then the global-storage agent DB.
    """
    all_conversations: list[Conversation] = []

    # Extract Chat mode (workspace storage)
    workspace_storage = installation / 'User/workspaceStorage'
    if workspace_storage.exists():
        for workspace in workspace_storage.iterdir():
            if workspace.is_dir():
                db_file = workspace / 'state.vscdb'
                if db_file.exists():
                    all_conversations.extend(
                        extract_windsurf_chat(db_file, workspace.name))

    # Extract Agent/Flow mode (global storage)
    global_storage = installation / 'User/globalStorage/state.vscdb'
    if global_storage.exists():
        convs = extract_windsurf_agent(global_storage)
        for conv in convs:
            conv['installation'] = str(installation)
        all_conversations.extend(convs)

    return all_conversations


class WindsurfExtractor:
    source = "windsurf"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    def find_installations(self) -> list[Path]:
        return find_windsurf_installations(self.root)

    def extract(self, install_dir: Path) -> Iterator[Conversation]:
        for conv in extract_windsurf_conversations(install_dir):
            yield conv
