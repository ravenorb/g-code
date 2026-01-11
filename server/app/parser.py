from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from parser.command_catalog import describe_command

LINE_RE = re.compile(r"^(?P<command>[A-Z]+[0-9]*[A-Z]?)(?P<rest>.*)$")
PARAM_RE = re.compile(r"(?<![A-Za-z0-9_$])([A-Za-z])([-+]?(?:\d+(?:\.\d*)?|\.\d+))")
HKOST_RE = re.compile(r"^N(?P<label>\d+)\s+HKOST\((?P<params>[^)]*)\)", re.IGNORECASE)
LINE_LABEL_RE = re.compile(r"^N(?P<label>\d+)", re.IGNORECASE)


@dataclass
class ParsedLine:
    line_number: int
    raw: str
    command: str
    params: Dict[str, float]
    description: str
    arguments: List[str]


@dataclass
class PartSummary:
    """Describes a part definition and its contour count."""

    hkost_line: int
    profile_line: Optional[int]
    contours: int
    x: Optional[float] = None
    y: Optional[float] = None


class HKParser:
    """Lightweight G-code parser tailored for HK toolpaths."""

    def parse(self, lines: Iterable[str]) -> List[ParsedLine]:
        parsed: List[ParsedLine] = []
        for idx, raw_line in enumerate(lines, start=1):
            normalized = raw_line.strip()
            if not normalized or normalized.startswith(";"):
                continue

            without_label = _strip_line_label(normalized)
            if not without_label:
                continue

            match = LINE_RE.match(without_label)
            if not match:
                raise ValueError(f"Unable to parse line {idx}: {normalized}")

            command = match.group("command").upper()
            params_str = match.group("rest") or ""
            params: Dict[str, float] = {}
            if not command.startswith("HK"):
                for param_match in PARAM_RE.finditer(params_str):
                    key, value = param_match.groups()
                    try:
                        params[key.upper()] = float(value)
                    except ValueError as exc:  # pragma: no cover - defensive
                        raise ValueError(f"Invalid numeric value on line {idx}") from exc

            metadata = describe_command(command)
            parsed.append(
                ParsedLine(
                    line_number=idx,
                    raw=normalized,
                    command=command,
                    params=params,
                    description=metadata.description,
                    arguments=list(metadata.arguments),
                )
            )
        return parsed

    def summarize_parts(self, lines: Iterable[str]) -> List[PartSummary]:
        """Identify HKOST part declarations and contour counts.

        Each HKOST line references a profile line (the 4th comma-separated
        parameter) such as ``N10000 HKOST(...,10001,...)``. Starting from the
        referenced line, contours are counted as the number of ``G`` lines that
        appear between the first ``HKCUT`` and the next ``HKSTO``.
        """

        normalized_lines = [line.strip() for line in lines]
        label_to_index: Dict[int, int] = {}
        for idx, line in enumerate(normalized_lines):
            label_match = LINE_LABEL_RE.match(line)
            if label_match:
                label_to_index[int(label_match.group("label"))] = idx

        parts: List[PartSummary] = []
        for idx, line in enumerate(normalized_lines):
            match = HKOST_RE.match(line)
            if not match:
                continue

            hkost_line = int(match.group("label"))
            hkost_params = _parse_hkost_params(match.group("params"))
            profile_line = _extract_profile_line(match.group("params"))
            contours = self._count_contours(normalized_lines, label_to_index.get(profile_line))
            parts.append(
                PartSummary(
                    hkost_line=hkost_line,
                    profile_line=profile_line,
                    contours=contours,
                    x=hkost_params[0] if len(hkost_params) > 0 else None,
                    y=hkost_params[1] if len(hkost_params) > 1 else None,
                )
            )

        return parts

    @staticmethod
    def _count_contours(lines: List[str], start_index: Optional[int]) -> int:
        if start_index is None:
            return 0

        contours = 0
        cut_started = False
        for line in lines[start_index:]:
            normalized = line.strip()
            upper_line = normalized.upper()

            if not cut_started:
                if "HKCUT" in upper_line:
                    cut_started = True
                continue

            if "HKSTO" in upper_line:
                break

            content = normalized
            if content.startswith("N"):
                tokens = content.split(maxsplit=1)
                content = tokens[1] if len(tokens) > 1 else ""

            if content.upper().startswith("G"):
                contours += 1

        return contours


def load_from_bytes(content: bytes) -> List[str]:
    text = content.decode("utf-8", errors="ignore")
    return text.splitlines()


def _strip_line_label(line: str) -> str:
    match = LINE_LABEL_RE.match(line)
    if not match:
        return line
    return line[match.end() :].lstrip()


def _extract_profile_line(params_text: str) -> Optional[int]:
    params = [part.strip() for part in params_text.split(",") if part.strip()]
    if len(params) < 4:
        return None
    try:
        return int(float(params[3]))
    except ValueError:
        return None


def _parse_hkost_params(params_text: str) -> List[float]:
    parts = []
    for param in params_text.split(","):
        cleaned = param.strip()
        if not cleaned:
            continue
        try:
            parts.append(float(cleaned))
        except ValueError:
            continue
    return parts


def extract_profile_block(lines: List[str], profile_line: Optional[int]) -> List[str]:
    if profile_line is None:
        return []

    normalized_profile = f"N{profile_line}".upper()
    start_index: Optional[int] = None
    for idx, line in enumerate(lines):
        if line.strip().upper().startswith(normalized_profile):
            start_index = idx
            break

    if start_index is None:
        return []

    block: List[str] = []
    for line in lines[start_index:]:
        block.append(line)
        if "HKSTO" in line.upper():
            break
    return block
