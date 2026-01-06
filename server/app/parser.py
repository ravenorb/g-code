from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

LINE_RE = re.compile(r"^(?P<command>[A-Z]\d+)(?P<params>(?:\s+[A-Za-z][-+]?\d*\.?\d*)*)")
PARAM_RE = re.compile(r"([A-Za-z])([-+]?\d*\.?\d*)")


@dataclass
class ParsedLine:
    line_number: int
    raw: str
    command: str
    params: Dict[str, float]


class HKParser:
    """Lightweight G-code parser tailored for HK toolpaths."""

    def parse(self, lines: Iterable[str]) -> List[ParsedLine]:
        parsed: List[ParsedLine] = []
        for idx, raw_line in enumerate(lines, start=1):
            normalized = raw_line.strip()
            if not normalized or normalized.startswith(";"):
                continue

            match = LINE_RE.match(normalized)
            if not match:
                raise ValueError(f"Unable to parse line {idx}: {normalized}")

            command = match.group("command")
            params_str = match.group("params") or ""
            params: Dict[str, float] = {}
            for param_match in PARAM_RE.finditer(params_str):
                key, value = param_match.groups()
                try:
                    params[key.upper()] = float(value)
                except ValueError as exc:  # pragma: no cover - defensive
                    raise ValueError(f"Invalid numeric value on line {idx}") from exc

            parsed.append(ParsedLine(line_number=idx, raw=normalized, command=command, params=params))
        return parsed


def load_from_bytes(content: bytes) -> List[str]:
    text = content.decode("utf-8", errors="ignore")
    return text.splitlines()
