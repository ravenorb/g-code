"""Parser for HK laser G-code files.

The parser is intentionally strict about malformed tokens, but tolerant about
numeric noise that HK controllers accept. Round-trip serialization is supported
for well-formed commands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Dict, Iterable, List, Optional, Tuple, Union


DEFAULT_TOLERANCE = 1e-4
ParameterValue = Union[float, int, str]


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
    parameters: Dict[str, ParameterValue] = field(default_factory=dict)
    args: List[ParameterValue] = field(default_factory=list)
    payload: Optional[str] = None
    comment: Optional[str] = None
    block_number: Optional[int] = None
    line_number: Optional[int] = None
    raw: Optional[str] = None

    def to_line(self, *, tolerance: float = DEFAULT_TOLERANCE) -> str:
        """Serialize the command back to a G-code line."""
        if (
            self.code == "BLOCK"
            and self.block_number is not None
            and not (self.parameters or self.args or self.payload)
        ):
            line = f"N{self.block_number}"
            if self.comment:
                line = f"{line} ; {self.comment}"
            return line

        prefix = f"N{self.block_number} " if self.block_number is not None else ""

        if self.args:
            args_text = ",".join(_format_value(arg, tolerance) for arg in self.args)
            base = f"{self.code}({args_text})"
        elif self.parameters:
            param_text = " ".join(
                f"{name}{_format_number(value, tolerance)}"
                for name, value in sorted(self.parameters.items())
            )
            base = f"{self.code} {param_text}".rstrip()
        else:
            base = self.code

        if self.payload:
            if base == self.code:
                base = f"{base} {self.payload}".rstrip()
            else:
                base = f"{base} {self.payload}".rstrip()

        line = prefix + base
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
        raw_line = line.rstrip("\r\n")
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
            command = _parse_line_content(stripped, idx, combined_comment, raw_line)
            if command.parameters or command.code in HK_COMMAND_SPECS:
                _validate_required_parameters(command)
            program.commands.append(command)
        except ParseError as error:
            error.line_number = error.line_number or idx
            error.line_text = error.line_text or raw_line
            program.errors.append(error)
            program.metadata.setdefault("unparsed", []).append(raw_line)
    return program


def _parse_line_content(
    content: str, line_number: int, comment: Optional[str], raw: str
) -> Command:
    block_number, remainder = _extract_block_number(content)
    if remainder is None:
        return Command(
            code="BLOCK",
            block_number=block_number,
            comment=comment,
            line_number=line_number,
            raw=raw,
        )

    match = re.match(r"(?P<code>[A-Za-z][A-Za-z0-9]*)\s*(?P<rest>.*)", remainder)
    if not match:
        raise ParseError(
            "Unable to identify command code", line_number=line_number, line_text=raw
        )
    code = match.group("code").upper()
    rest = match.group("rest").strip()

    if code == "WHEN" and rest:
        return Command(
            code=code,
            payload=rest,
            block_number=block_number,
            comment=comment,
            line_number=line_number,
            raw=raw,
        )

    if rest.startswith("("):
        args_text, trailing = _split_parenthetical(rest, raw, line_number)
        args = _parse_arguments(args_text, line_number, raw)
        payload = trailing.strip() if trailing else None
        return Command(
            code=code,
            args=args,
            payload=payload,
            block_number=block_number,
            comment=comment,
            line_number=line_number,
            raw=raw,
        )

    if not rest:
        return Command(
            code=code,
            block_number=block_number,
            comment=comment,
            line_number=line_number,
            raw=raw,
        )

    word_tokens = [code] + rest.split()
    parameters = _parse_word_parameters(word_tokens, line_number, raw)
    return Command(
        code=code,
        parameters=parameters,
        block_number=block_number,
        comment=comment,
        line_number=line_number,
        raw=raw,
    )


def _extract_block_number(content: str) -> Tuple[Optional[int], Optional[str]]:
    """Return (block_number, remaining_content)."""
    match = re.match(r"\s*N(?P<num>\d+)\s*(?P<rest>.*)", content)
    if not match:
        return None, content.strip() or None
    remainder = match.group("rest").strip()
    return int(match.group("num")), remainder or None


def _split_parenthetical(rest: str, raw: str, line_number: int) -> Tuple[str, Optional[str]]:
    """Split '(... )' returning inner text and trailing remainder."""
    depth = 0
    for idx, char in enumerate(rest):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                inner = rest[1:idx]
                trailing = rest[idx + 1 :]
                return inner, trailing
    raise ParseError(
        "Unclosed parenthetical command",
        line_number=line_number,
        line_text=raw,
    )


def _parse_arguments(text: str, line_number: int, raw: str) -> List[ParameterValue]:
    """Parse comma-separated positional arguments, handling quoted strings."""
    args: List[ParameterValue] = []
    buffer = []
    in_quotes = False
    idx = 0
    while idx < len(text):
        char = text[idx]
        if char == '"' and (idx == 0 or text[idx - 1] != "\\"):
            in_quotes = not in_quotes
            idx += 1
            continue
        if char == "," and not in_quotes:
            args.append(_convert_argument("".join(buffer).strip(), line_number, raw))
            buffer = []
            idx += 1
            continue
        buffer.append(char)
        idx += 1

    if in_quotes:
        raise ParseError(
            "Unterminated quoted argument",
            line_number=line_number,
            line_text=raw,
        )

    if buffer or text.endswith(","):
        args.append(_convert_argument("".join(buffer).strip(), line_number, raw))
    return args


def _convert_argument(text: str, line_number: int, raw: str) -> ParameterValue:
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return text[1:-1]
    if text == "":
        raise ParseError("Empty argument", line_number=line_number, line_text=raw)
    try:
        numeric = float(text)
    except ValueError:
        return text
    normalized = _normalize_value(numeric)
    if normalized.is_integer():
        return int(normalized)
    return normalized


def _parse_word_parameters(
    tokens: List[str], line_number: int, raw: str
) -> Dict[str, ParameterValue]:
    if not tokens:
        raise ParseError("Empty command segment", line_number=line_number, line_text=raw)

    code = tokens[0].upper()
    if not COMMAND_PATTERN.match(code):
        raise ParseError(
            f"Invalid command '{code}'", line_number=line_number, line_text=raw
        )

    parameters: Dict[str, ParameterValue] = {}
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
    return parameters


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
    stripped_content = content.strip()
    paren_index = content.find("(")
    if stripped_content.startswith("(") or (
        paren_index != -1 and paren_index > 0 and content[paren_index - 1].isspace()
    ):
        start = paren_index if paren_index != -1 else content.find("(")
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


def _normalize_value(value: ParameterValue, tolerance: float = DEFAULT_TOLERANCE) -> float:
    """Round values that are effectively integers to avoid tiny noise."""
    numeric = float(value)
    nearest_int = round(numeric)
    if abs(numeric - nearest_int) < tolerance:
        return float(nearest_int)
    return numeric


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
    normalized = _normalize_value(float(value), tolerance)
    if normalized.is_integer():
        return str(int(normalized))
    text = f"{normalized:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _format_value(value: ParameterValue, tolerance: float) -> str:
    if isinstance(value, str):
        return f'"{value}"'
    return _format_number(float(value), tolerance)


__all__ = [
    "Command",
    "Program",
    "ParseError",
    "ParameterValue",
    "parse_program",
    "DEFAULT_TOLERANCE",
]
