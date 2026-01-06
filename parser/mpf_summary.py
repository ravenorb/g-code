"""Utilities for summarizing commands used in HK MPF files."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Dict, Iterable, List, Optional, Sequence

from parser.command_catalog import CommandMetadata, describe_command


LINE_NUMBER_PATTERN = re.compile(r"^N\d+$")
CODE_PATTERN = re.compile(r"^([A-Z]+[0-9]*[A-Z]?)")


def _strip_inline_comments(line: str) -> str:
    """Remove semicolon and parenthetical comments from a line."""

    # Remove trailing semicolon comments.
    content = line.split(";", 1)[0].strip()

    # Remove parenthetical comments. Continue stripping in case multiple exist.
    while "(" in content:
        start = content.find("(")
        end = content.find(")", start + 1)
        if end == -1:
            # Unclosed paren; treat the rest as comment.
            content = content[:start].strip()
            break
        content = f"{content[:start]} {content[end+1:]}".strip()
    return content


def extract_command_code(line: str) -> Optional[str]:
    """Return the command code from a single line, ignoring comments and labels."""

    content = _strip_inline_comments(line)
    if not content:
        return None

    tokens = content.split()
    if tokens and LINE_NUMBER_PATTERN.match(tokens[0]):
        tokens = tokens[1:]
    if not tokens:
        return None

    match = CODE_PATTERN.match(tokens[0])
    if not match:
        return None
    return match.group(1)


def collect_unique_commands(paths: Iterable[Path]) -> List[str]:
    """Collect sorted unique command codes from the provided MPF files."""

    commands = set()
    for path in paths:
        for line in path.read_text(errors="ignore").splitlines():
            code = extract_command_code(line)
            if code:
                commands.add(code)
    return sorted(commands)


def build_command_table(paths: Sequence[Path]) -> List[Dict[str, object]]:
    """Build a table of command metadata for the given MPF files."""

    table: List[Dict[str, object]] = []
    for code in collect_unique_commands(paths):
        metadata: CommandMetadata = describe_command(code)
        table.append(
            {
                "command": code,
                "description": metadata.description,
                "arguments": metadata.arguments,
            }
        )
    return table


__all__ = [
    "build_command_table",
    "collect_unique_commands",
    "extract_command_code",
]
