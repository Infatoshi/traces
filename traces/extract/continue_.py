"""Continue extractor.

Vendored from 0xSero/ai-data-extraction/extract_continue.py (MIT).
Refactored to the Extractor protocol; discovery + parsing logic preserved.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from traces.extract.base import Conversation


def find_continue_installations(home: Path | None = None) -> list[Path]:
    """Find all Continue installation directories.

    Continue stores sessions under ``~/.continue/sessions``. The install
    directory we hand to ``extract`` is ``~/.continue``.
    """
    home = home or Path.home()
    continue_dir = home / ".continue"
    return [continue_dir] if continue_dir.exists() else []


def extract_continue_sessions(install_dir: Path) -> list[Conversation]:
    """Extract all Continue sessions from ``<install_dir>/sessions``."""
    sessions_dir = install_dir / "sessions"
    if not sessions_dir.exists():
        return []

    conversations: list[Conversation] = []

    for session_file in sessions_dir.glob("*.json"):
        if session_file.name == "sessions.json":
            continue

        import json

        try:
            with open(session_file) as f:
                data = json.loads(f.read())

            if 'history' not in data:
                continue

            messages = []
            for item in data['history']:
                if 'message' not in item:
                    continue

                msg = item['message']
                role = msg.get('role')

                # Extract content
                content_parts = msg.get('content', [])
                if isinstance(content_parts, str):
                    content = content_parts
                elif isinstance(content_parts, list):
                    content = '\n'.join([
                        c.get('text', '') for c in content_parts
                        if isinstance(c, dict) and c.get('type') == 'text'
                    ])
                else:
                    content = ''

                message = {
                    'role': role,
                    'content': content
                }

                # Add tool calls for assistant messages
                if 'toolCalls' in msg:
                    message['tool_calls'] = msg['toolCalls']

                # Add reasoning
                if 'reasoning' in item and item['reasoning']:
                    message['reasoning'] = item['reasoning'].get('text', '')

                # Add context items
                if 'contextItems' in item and item['contextItems']:
                    message['context_items'] = item['contextItems']

                # Add tool results
                if 'toolCallStates' in item:
                    tool_results = []
                    for state in item['toolCallStates']:
                        if state.get('status') == 'done' and 'output' in state:
                            tool_results.append({
                                'tool': state.get('tool', {}).get('function', {}).get('name'),
                                'output': state['output']
                            })
                    if tool_results:
                        message['tool_results'] = tool_results

                messages.append(message)

            if messages:
                conversations.append({
                    'messages': messages,
                    'source': 'continue',
                    'session_id': data.get('sessionId'),
                    'title': data.get('title'),
                    'workspace': data.get('workspaceDirectory'),
                    'project_path': data.get('workspaceDirectory'),
                    'source_file': str(session_file),
                    'installation': str(install_dir),
                })

        except Exception as e:  # noqa: BLE001 - upstream swallows per-file errors
            print(f"Error processing {session_file}: {e}")
            continue

    return conversations


class ContinueExtractor:
    source = "continue"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    def find_installations(self) -> list[Path]:
        return find_continue_installations(self.root)

    def extract(self, install_dir: Path) -> Iterator[Conversation]:
        for conv in extract_continue_sessions(install_dir):
            yield conv
