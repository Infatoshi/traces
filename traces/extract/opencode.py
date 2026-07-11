"""OpenCode extractor.

Vendored from 0xSero/ai-data-extraction/extract_opencode.py (MIT).
Refactored to the Extractor protocol; discovery + parsing logic preserved.

Supports two storage backends, both kept:
- CLI: JSON files under ``<install>/storage/{message,part,session}/...``
- Desktop: Tauri ``.dat`` store files (length-prefixed key/value)

Upstream ``find_opencode_installations`` returned ``(type, dir)`` tuples; the
``Extractor`` protocol yields bare ``Path``s, so we return the install dir
and re-dispatch on directory contents in ``extract`` (a CLI install contains
a ``storage/`` subdir; a desktop install contains ``.dat`` files).
"""
from __future__ import annotations

import json
import os
import platform
import struct
from pathlib import Path
from typing import Iterator

from traces.extract.base import Conversation


def find_opencode_installations(home: Path | None = None) -> list[Path]:
    """Find all OpenCode installation directories (CLI + Desktop).

    Returns bare installation dirs (per the Extractor protocol). ``extract``
    re-derives the install type from directory contents.
    """
    home = home or Path.home()

    locations: list[Path] = []

    cli_dirs = [
        home / "Library/Application Support/opencode",
        home / ".local/share/opencode",
    ]
    if home == Path.home():
        cli_dirs.append(Path(os.environ.get('XDG_DATA_HOME', home / '.local/share')) / 'opencode')
    if platform.system() == "Windows" and home == Path.home():
        cli_dirs.append(Path(os.environ.get('APPDATA', home / 'AppData/Roaming')) / 'opencode')

    for cli_dir in cli_dirs:
        if cli_dir.exists():
            locations.append(cli_dir)

    desktop_dirs = [
        home / "Library/Application Support/ai.opencode.app",
        home / ".local/share/ai.opencode.app",
    ]
    if platform.system() == "Windows" and home == Path.home():
        desktop_dirs.append(Path(os.environ.get('APPDATA', home / 'AppData/Roaming')) / 'ai.opencode.app')

    for desktop_dir in desktop_dirs:
        if desktop_dir.exists():
            locations.append(desktop_dir)

    return list({p.resolve() for p in locations})


def read_tauri_store(dat_file):
    """
    Parse Tauri store .dat files
    Format: Simple key-value pairs with length prefixes
    """
    try:
        with open(dat_file, 'rb') as f:
            data = f.read()

        store = {}
        offset = 0

        while offset < len(data):
            # Try to read key length (4 bytes, little-endian)
            if offset + 4 > len(data):
                break

            key_len = struct.unpack('<I', data[offset:offset+4])[0]
            offset += 4

            # Sanity check
            if key_len > 10000 or offset + key_len > len(data):
                break

            # Read key
            key = data[offset:offset+key_len].decode('utf-8', errors='ignore')
            offset += key_len

            # Read value length
            if offset + 4 > len(data):
                break

            value_len = struct.unpack('<I', data[offset:offset+4])[0]
            offset += 4

            # Sanity check
            if value_len > 1000000 or offset + value_len > len(data):
                break

            # Read value
            try:
                value_bytes = data[offset:offset+value_len]
                value = json.loads(value_bytes.decode('utf-8'))
                store[key] = value
            except Exception:  # noqa: BLE001 - upstream swallows per-entry errors
                pass

            offset += value_len

        return store

    except Exception as e:  # noqa: BLE001 - upstream prints but returns {}
        print(f"Error reading Tauri store {dat_file}: {e}")
        return {}


def extract_cli_conversations(storage_dir: Path) -> list[Conversation]:
    """
    Extract conversations from CLI JSON storage.

    Handles sessions both WITH and WITHOUT session metadata files.
    For sessions without metadata, reconstructs session info from messages/parts.
    """
    conversations: list[Conversation] = []

    message_dir = storage_dir / 'storage' / 'message'
    part_dir = storage_dir / 'storage' / 'part'

    if not message_dir.exists():
        return conversations

    # Find all session directories (each is a directory named ses_xxx)
    session_dirs = [d for d in message_dir.iterdir() if d.is_dir() and d.name.startswith('ses_')]

    processed_sessions = set()

    for session_dir_path in session_dirs:
        try:
            session_id = session_dir_path.name

            # Skip if already processed (deduplication)
            if session_id in processed_sessions:
                continue
            processed_sessions.add(session_id)

            # Try to load session metadata if available
            session_data = None
            session_file = storage_dir / 'storage' / 'session' / 'global' / f'{session_id}.json'

            if session_file.exists():
                with open(session_file) as f:
                    session_data = json.load(f)

            # Collect all messages for this session
            message_files = sorted(session_dir_path.glob('msg_*.json'))

            if not message_files:
                continue

            messages = []
            first_message_time = None
            last_message_time = None
            project_path = None
            token_usage = {
                'input': 0,
                'output': 0,
                'reasoning': 0,
                'cached_input': 0,
                'cache_write': 0,
            }

            for msg_file in message_files:
                try:
                    with open(msg_file) as f:
                        msg_data = json.load(f)

                    message_id = msg_data.get('id')
                    role = msg_data.get('role', 'assistant')
                    msg_time = msg_data.get('time', {}).get('created')
                    structured_path = msg_data.get('path')
                    if isinstance(structured_path, dict) and structured_path.get('cwd'):
                        project_path = structured_path['cwd']

                    # Track timestamps
                    if msg_time:
                        if not first_message_time or msg_time < first_message_time:
                            first_message_time = msg_time
                        if not last_message_time or msg_time > last_message_time:
                            last_message_time = msg_time

                    # Build the message
                    message = {
                        'role': role,
                        'content': '',
                        'timestamp': msg_time
                    }

                    # Add metadata
                    if 'modelID' in msg_data:
                        message['model'] = msg_data['modelID']
                    if 'providerID' in msg_data:
                        message['provider'] = msg_data['providerID']
                    if 'agent' in msg_data:
                        message['agent'] = msg_data['agent']
                    if 'mode' in msg_data:
                        message['mode'] = msg_data['mode']

                    # Add token usage
                    if 'tokens' in msg_data:
                        message['tokens'] = msg_data['tokens']
                        tokens = msg_data['tokens']
                        if isinstance(tokens, dict):
                            for source_key, target_key in (
                                ('input', 'input'),
                                ('output', 'output'),
                                ('reasoning', 'reasoning'),
                            ):
                                value = tokens.get(source_key)
                                if isinstance(value, (int, float)):
                                    token_usage[target_key] += value
                            cache = tokens.get('cache')
                            if isinstance(cache, dict):
                                if isinstance(cache.get('read'), (int, float)):
                                    token_usage['cached_input'] += cache['read']
                                if isinstance(cache.get('write'), (int, float)):
                                    token_usage['cache_write'] += cache['write']
                    if 'cost' in msg_data:
                        message['cost'] = msg_data['cost']

                    # Find all parts for this message
                    message_part_dir = part_dir / message_id

                    if message_part_dir.exists():
                        part_files = sorted(message_part_dir.glob('prt_*.json'))
                        content_parts = []
                        tool_calls = []
                        tool_results = []
                        reasoning_parts = []

                        for part_file in part_files:
                            try:
                                with open(part_file) as f:
                                    part_data = json.load(f)

                                part_type = part_data.get('type')
                                part_text = part_data.get('text', '')

                                if part_type == 'text':
                                    content_parts.append(part_text)
                                elif part_type == 'tool' or part_type == 'tool-call':
                                    # OpenCode uses 'tool' type with state containing input/output
                                    state = part_data.get('state', {})
                                    tool_name = part_data.get('tool', part_data.get('name'))

                                    tool_call = {
                                        'id': part_data.get('callID', part_data.get('id')),
                                        'name': tool_name,
                                        'input': state.get('input', part_data.get('input'))
                                    }

                                    # If completed, also add to tool_results
                                    if state.get('status') == 'completed' and 'output' in state:
                                        tool_results.append({
                                            'tool_call_id': part_data.get('callID'),
                                            'tool': tool_name,
                                            'output': state['output']
                                        })

                                    tool_calls.append(tool_call)
                                elif part_type == 'tool-result':
                                    tool_results.append({
                                        'tool_call_id': part_data.get('toolCallID'),
                                        'output': part_data.get('output')
                                    })
                                elif part_type == 'code':
                                    # Code blocks
                                    code_text = part_data.get('text', '')
                                    language = part_data.get('language', '')
                                    content_parts.append(f"```{language}\n{code_text}\n```")
                                elif part_type == 'reasoning':
                                    # Reasoning/thinking content
                                    reasoning_text = part_data.get('text', '')
                                    if reasoning_text:
                                        reasoning_parts.append(reasoning_text)

                            except Exception as e:  # noqa: BLE001 - upstream swallows per-part errors
                                print(f"    Error reading part {part_file}: {e}")
                                continue

                        message['content'] = '\n'.join(content_parts)

                        if tool_calls:
                            message['tool_calls'] = tool_calls
                        if tool_results:
                            message['tool_results'] = tool_results
                        if reasoning_parts:
                            message['reasoning'] = '\n'.join(reasoning_parts)

                    messages.append(message)

                except Exception as e:  # noqa: BLE001 - upstream swallows per-msg errors
                    print(f"    Error reading message {msg_file}: {e}")
                    continue

            if not messages:
                continue

            conversation: Conversation = {
                'messages': messages,
                'source': 'opencode-cli',
                'session_id': session_id,
                'created_at': first_message_time,
                'updated_at': last_message_time,
            }
            nonzero_usage = {key: value for key, value in token_usage.items() if value}
            if nonzero_usage:
                conversation['token_usage'] = nonzero_usage

            if session_data:
                # Use metadata from session file
                conversation['title'] = session_data.get('title')
                conversation['created_at'] = session_data.get('time', {}).get('created')
                conversation['updated_at'] = session_data.get('time', {}).get('updated')
                conversation['project_id'] = session_data.get('projectID')
                conversation['directory'] = session_data.get('directory')
                conversation['project_path'] = session_data.get('directory') or project_path
                conversation['version'] = session_data.get('version')

                # Add summary stats if available
                if 'summary' in session_data:
                    conversation['summary'] = session_data['summary']

                # Add parent session if it's a child session
                if 'parentID' in session_data:
                    conversation['parent_session_id'] = session_data['parentID']
            else:
                # Current OpenCode message records carry a structured path.cwd.
                # If it is absent, leave project metadata unknown rather than
                # interpreting commands or prose as metadata.
                conversation['project_path'] = project_path

            conversations.append(conversation)

        except Exception as e:  # noqa: BLE001 - upstream swallows per-session errors
            print(f"  Error processing session {session_dir_path}: {e}")
            continue

    return conversations


def extract_desktop_conversations(desktop_dir: Path) -> list[Conversation]:
    """Extract conversations from Desktop Tauri store files"""
    conversations: list[Conversation] = []

    # Look for .dat files
    dat_files = list(desktop_dir.rglob('*.dat'))

    if not dat_files:
        return conversations

    for dat_file in dat_files:
        store = read_tauri_store(dat_file)

        if not store:
            continue

        # Look for session/conversation data in the store
        # Keys might be like "session:ses_xxxxx" or similar
        for key, value in store.items():
            if not isinstance(value, dict):
                continue

            # Check if this looks like a conversation/session
            if 'messages' in value or 'history' in value:
                try:
                    messages = value.get('messages', value.get('history', []))

                    if not messages:
                        continue

                    conversation: Conversation = {
                        'messages': messages,
                        'source': 'opencode-desktop',
                        'store_key': key,
                        'store_file': str(dat_file.name)
                    }

                    # Add any additional metadata
                    for meta_key in ['session_id', 'title', 'created_at', 'workspace']:
                        if meta_key in value:
                            conversation[meta_key] = value[meta_key]

                    conversations.append(conversation)

                except Exception:  # noqa: BLE001 - upstream swallows per-entry errors
                    continue

    return conversations


def extract_opencode_conversations(installation: Path) -> list[Conversation]:
    """Extract from one OpenCode installation, dispatching on its layout.

    A CLI install contains a ``storage/`` subdir; a desktop install contains
    ``.dat`` files. We run whichever path matches (both if both happen to).
    """
    conversations: list[Conversation] = []

    if (installation / 'storage').exists():
        conversations.extend(extract_cli_conversations(installation))

    if list(installation.rglob('*.dat')):
        conversations.extend(extract_desktop_conversations(installation))

    for conv in conversations:
        conv['installation'] = str(installation)

    return conversations


class OpenCodeExtractor:
    source = "opencode"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    def find_installations(self) -> list[Path]:
        return find_opencode_installations(self.root)

    def extract(self, install_dir: Path) -> Iterator[Conversation]:
        for conv in extract_opencode_conversations(install_dir):
            yield conv
