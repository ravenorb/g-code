"""Parser for HK laser G-code files.

The parser is intentionally strict about malformed tokens, but tolerant about
numeric noise that HK controllers accept. Round-trip serialization is supported
for well-formed commands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Dict, Iterable, List, Optional, Tuple


DEFAULT_TOLERANCE = 1e-4


# Commonly observed HK-specific commands. Each entry lists required parameters.
HK_COMMAND_SPECS: Dict[str, Iterable[str]] = {
    "G0": (),
    "G1": (),
    "M3": ("S",),  # Spindle/laser power
    "M5": (),
    "VS": ("P",),  # Vendor speed setpoint
    "VE": ("P",),  # Vendor engrave power
    "FM": (),  # Firmware metadata line
    "BP": ("P",),  # Backlash parameter
    "RD": ("P",),  # Raster density
}


COMMAND_PATTERN = re.compile(r"^[A-Z]+[0-9]*[A-Z]?$")
PARAM_PATTERN = re.compile(
    r"^(?P<name>[A-Z])(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)$"
)


@dataclass
class Command:
    code: str
    parameters: Dict[str, float] = field(default_factory=dict)
    comment: Optional[str] = None
    line_number: Optional[int] = None
    raw: Optional[str] = None

    def to_line(self, *, tolerance: float = DEFAULT_TOLERANCE) -> str:
        """Serialize the command back to a G-code line."""
        param_text = " ".join(
            f"{name}{_format_number(value, tolerance)}"
            for name, value in sorted(self.parameters.items())
        )
        parts = [self.code]
        if param_text:
            parts.append(param_text)
        line = " ".join(parts)
        if self.comment:
            line = f"{line} ; {self.comment}"
        return line


@dataclass
class ParseError(Exception):
    message: str
    line_number: Optional[int] = None
    column: Optional[int] = None
    line_text: Optional[str] = None

    def __str__(self) -> str:  # pragma: no cover - dataclass provides repr
        return self.message


@dataclass
class Program:
    commands: List[Command] = field(default_factory=list)
    metadata: Dict[str, List[str]] = field(
        default_factory=lambda: {"comments": [], "unparsed": []}
    )
    errors: List[ParseError] = field(default_factory=list)

    def to_lines(self) -> List[str]:
        """Serialize the program back to text lines."""
        return [command.to_line() for command in self.commands]


def parse_program(source: str) -> Program:
    """Parse HK laser G-code text into a structured program."""
    program = Program()
    for idx, line in enumerate(source.splitlines(), start=1):
        raw_line = line.rstrip("\n")
        try:
            stripped, trailing_comment, paren_comment = _strip_comments(
                raw_line, line_number=idx
            )
            combined_comment = _combine_comments(trailing_comment, paren_comment)
        except ParseError as error:
            error.line_number = error.line_number or idx
            error.line_text = error.line_text or raw_line
            program.errors.append(error)
            program.metadata.setdefault("unparsed", []).append(raw_line)
            continue

        if not stripped.strip():
            if combined_comment:
                program.metadata.setdefault("comments", []).append(combined_comment)
            continue

        try:
            command = _parse_command_tokens(
                stripped, idx, combined_comment, raw_line
            )
            _validate_required_parameters(command)
            program.commands.append(command)
        except ParseError as error:
            program.errors.append(error)
            program.metadata.setdefault("unparsed", []).append(raw_line)
    return program


def _parse_command_tokens(
    token_string: str, line_number: int, comment: Optional[str], raw: str
) -> Command:
    tokens = token_string.split()
    if not tokens:
        raise ParseError("Empty command segment", line_number=line_number, line_text=raw)

    code = tokens[0].upper()
    if not COMMAND_PATTERN.match(code):
        raise ParseError(
            f"Invalid command '{code}'", line_number=line_number, line_text=raw
        )

    parameters: Dict[str, float] = {}
    for token in tokens[1:]:
        match = PARAM_PATTERN.match(token)
        if not match:
            raise ParseError(
                f"Malformed parameter '{token}'",
                line_number=line_number,
                line_text=raw,
            )
        name = match.group("name")
        try:
            value = float(match.group("value"))
        except ValueError:
            raise ParseError(
                f"Invalid numeric value in '{token}'",
                line_number=line_number,
                line_text=raw,
            )
        parameters[name] = _normalize_value(value)

    return Command(
        code=code, parameters=parameters, comment=comment, line_number=line_number, raw=raw
    )


def _strip_comments(
    line: str, line_number: Optional[int] = None
) -> Tuple[str, Optional[str], Optional[str]]:
    """Return (content, trailing_semicolon_comment, parenthetical_comment)."""
    trailing_comment = None
    content = line
    if ";" in line:
        content, trailing_comment = line.split(";", 1)
        content = content.rstrip()
        trailing_comment = trailing_comment.strip() or None

    paren_comment = None
    if "(" in content:
        start = content.find("(")
        end = content.find(")", start + 1)
        if end == -1:
            raise ParseError(
                "Unclosed parenthetical comment",
                line_number=line_number,
                line_text=line,
            )
        paren_comment = content[start + 1 : end].strip() or None
        content = f"{content[:start]} {content[end+1:]}".strip()
    return content, trailing_comment, paren_comment


def _combine_comments(*comments: Optional[str]) -> Optional[str]:
    combined = [comment for comment in comments if comment]
    if not combined:
        return None
    return " | ".join(combined)


def _normalize_value(value: float, tolerance: float = DEFAULT_TOLERANCE) -> float:
    """Round values that are effectively integers to avoid tiny noise."""
    nearest_int = round(value)
    if abs(value - nearest_int) < tolerance:
        return float(nearest_int)
    return value


def _validate_required_parameters(command: Command) -> None:
    required = HK_COMMAND_SPECS.get(command.code, ())
    missing = [name for name in required if name not in command.parameters]
    if missing:
        raise ParseError(
            f"Missing required parameter(s) {', '.join(sorted(missing))} for {command.code}",
            line_number=command.line_number,
            line_text=command.raw,
        )


def _format_number(value: float, tolerance: float) -> str:
    normalized = _normalize_value(value, tolerance)
    if normalized.is_integer():
        return str(int(normalized))
    text = f"{normalized:.6f}".rstrip("0").rstrip(".")
    return text or "0"


__all__ = [
    "Command",
    "Program",
    "ParseError",
    "parse_program",
    "DEFAULT_TOLERANCE",
]
