from __future__ import annotations

import re
from dataclasses import dataclass
import math
from typing import Dict, Iterable, List, Optional, Union

from parser.command_catalog import describe_command

LINE_RE = re.compile(r"^(?P<command>[A-Z]+[0-9]*[A-Z]?)(?P<rest>.*)$")
PARAM_RE = re.compile(r"(?<![A-Za-z0-9_$])([A-Za-z])([-+]?(?:\d+(?:\.\d*)?|\.\d+))")
HKSTR_RE = re.compile(r"HKSTR\((?P<params>[^)]*)\)", re.IGNORECASE)
HKSTO_RE = re.compile(r"HKSTO\((?P<params>[^)]*)\)", re.IGNORECASE)
HKOST_RE = re.compile(r"HKOST\((?P<params>[^)]*)\)", re.IGNORECASE)
HKPED_RE = re.compile(r"HKPED\((?P<params>[^)]*)\)", re.IGNORECASE)
LINE_LABEL_RE = re.compile(r"^N(?P<label>\d+)", re.IGNORECASE)
COORD_RE = re.compile(r"([XY])([-+]?\d*\.?\d+)")


@dataclass
class ParsedLine:
    line_number: int
    raw: str
    command: str
    params: Dict[str, Union[str, float]]
    description: str
    arguments: List[str]


@dataclass
class PartSummary:
    """Describes a part definition and its contour count."""

    part_number: int
    part_line: int
    hkost_line: int
    profile_line: Optional[int]
    start_line: int
    end_line: int
    contours: int
    anchor_x: Optional[float]
    anchor_y: Optional[float]


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
            metadata = describe_command(command)
            params: Dict[str, Union[str, float]] = {}
            if command.startswith("HK"):
                hk_params = _parse_hk_params(params_str)
                if hk_params:
                    for idx, arg_name in enumerate(metadata.arguments):
                        if idx >= len(hk_params):
                            break
                        params[arg_name] = _coerce_param(hk_params[idx])
            else:
                for param_match in PARAM_RE.finditer(params_str):
                    key, value = param_match.groups()
                    try:
                        params[key.upper()] = float(value)
                    except ValueError as exc:  # pragma: no cover - defensive
                        raise ValueError(f"Invalid numeric value on line {idx}") from exc

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
        """Identify HKOST parts and contour counts.

        Each part starts at a ``HKOST`` line and references a contour block that
        begins at the labeled ``HKSTR`` line. Contours are counted as the number
        of ``HKSTR`` lines between the referenced start and the closing
        ``HKPED`` line.
        """

        normalized_lines = [line.strip() for line in lines]
        label_to_index = _index_labels(normalized_lines)
        parts: List[PartSummary] = []
        for idx, line in enumerate(normalized_lines):
            if not HKOST_RE.search(line):
                continue

            label_match = LINE_LABEL_RE.match(line)
            part_line = int(label_match.group("label")) if label_match else idx + 1
            part_number = (
                _part_number_from_label(part_line) if label_match else len(parts) + 1
            )
            anchor_x, anchor_y, profile_line = _extract_hkost_details(line)
            start_index = label_to_index.get(profile_line) if profile_line else None
            if start_index is not None:
                end_index = _find_profile_end(normalized_lines, start_index)
                contours = _count_contours(normalized_lines, start_index, end_index)
                start_line = start_index + 1
                end_line = end_index + 1
            else:
                start_line = idx + 1
                end_line = idx + 1
                contours = 0
            parts.append(
                PartSummary(
                    part_number=part_number,
                    part_line=part_line,
                    hkost_line=idx + 1,
                    profile_line=profile_line,
                    start_line=start_line,
                    end_line=end_line,
                    contours=contours,
                    anchor_x=anchor_x,
                    anchor_y=anchor_y,
                )
            )

        return parts


def load_from_bytes(content: bytes) -> List[str]:
    text = content.decode("utf-8", errors="ignore")
    return text.splitlines()


def _strip_line_label(line: str) -> str:
    match = LINE_LABEL_RE.match(line)
    if not match:
        return line
    return line[match.end() :].lstrip()


def extract_part_block(lines: List[str], part_line: int) -> List[str]:
    label_prefix = f"N{part_line}".upper()
    hkost_index: Optional[int] = None
    for idx, line in enumerate(lines):
        cleaned = line.strip().upper()
        if cleaned.startswith(label_prefix) and HKOST_RE.search(cleaned):
            hkost_index = idx
            break

    if hkost_index is None:
        return []

    profile_line = _extract_profile_line(lines[hkost_index])
    if profile_line is None:
        return []

    label_to_index = _index_labels([line.strip() for line in lines])
    start_index = label_to_index.get(profile_line)
    if start_index is None:
        return []

    end_index = _find_profile_end([line.strip() for line in lines], start_index)
    return lines[start_index : end_index + 1]


def extract_part_contour_blocks(lines: List[str], part_line: int) -> List[List[str]]:
    part_block = extract_part_block(lines, part_line)
    if not part_block:
        return []
    return _split_contour_blocks(part_block)


def extract_part_contour_block(lines: List[str], part_line: int, contour_index: int) -> List[str]:
    if contour_index < 1:
        return []
    contour_blocks = extract_part_contour_blocks(lines, part_line)
    if contour_index > len(contour_blocks):
        return []
    return contour_blocks[contour_index - 1]


def build_part_plot_points(lines: List[str]) -> List[List[tuple[float, float]]]:
    contour_blocks = _split_contour_blocks(lines)
    contours: List[List[tuple[float, float]]] = []
    for block in contour_blocks:
        points = _build_contour_plot_points(block)
        if points:
            contours.append(points)
    return contours


def _build_contour_plot_points(lines: List[str]) -> List[tuple[float, float]]:
    points: List[tuple[float, float]] = []
    current_x: Optional[float] = None
    current_y: Optional[float] = None
    cut_started = False

    for line in lines:
        normalized = line.strip()
        if not normalized:
            continue

        upper_line = normalized.upper()
        if "HKSTR" in upper_line:
            params = _parse_hk_params(upper_line)
            if len(params) >= 4:
                pierce_x = _coerce_float(params[2])
                pierce_y = _coerce_float(params[3])
                if pierce_x is not None and pierce_y is not None:
                    current_x = pierce_x
                    current_y = pierce_y
            continue
        if "HKSTO" in upper_line or "HKPED" in upper_line:
            break
        if "HKCUT" in upper_line:
            cut_started = True
            if current_x is not None and current_y is not None:
                points.append((current_x, current_y))
            continue

        content = _strip_line_label(normalized)
        if not content:
            continue

        match = LINE_RE.match(content)
        if not match:
            continue

        command = match.group("command").upper()
        params_text = match.group("rest") or ""
        if command not in {"G0", "G00", "G1", "G01", "G2", "G02", "G3", "G03"}:
            continue

        coords = {axis.upper(): float(value) for axis, value in COORD_RE.findall(params_text)}
        next_x = coords.get("X", current_x)
        next_y = coords.get("Y", current_y)
        if next_x is None or next_y is None:
            continue

        if command in {"G2", "G02", "G3", "G03"} and cut_started:
            arc_params = {key.upper(): float(value) for key, value in PARAM_RE.findall(params_text)}
            arc_points = _interpolate_arc_points(
                current_x,
                current_y,
                next_x,
                next_y,
                arc_params.get("I"),
                arc_params.get("J"),
                command in {"G2", "G02"},
            )
            if arc_points:
                points.extend(arc_points)
            elif (next_x, next_y) != (current_x, current_y):
                points.append((next_x, next_y))
        elif cut_started and (current_x, current_y) != (next_x, next_y):
            points.append((next_x, next_y))

        current_x, current_y = next_x, next_y

    return points


def _interpolate_arc_points(
    start_x: Optional[float],
    start_y: Optional[float],
    end_x: float,
    end_y: float,
    offset_i: Optional[float],
    offset_j: Optional[float],
    clockwise: bool,
) -> List[tuple[float, float]]:
    if start_x is None or start_y is None or offset_i is None or offset_j is None:
        return []

    center_x = start_x + offset_i
    center_y = start_y + offset_j
    radius = math.hypot(offset_i, offset_j)
    if radius == 0:
        return []

    start_angle = math.atan2(start_y - center_y, start_x - center_x)
    end_angle = math.atan2(end_y - center_y, end_x - center_x)
    sweep = end_angle - start_angle
    if clockwise and sweep >= 0:
        sweep -= 2 * math.pi
    elif not clockwise and sweep <= 0:
        sweep += 2 * math.pi

    segment_angle = math.radians(10)
    segments = max(2, int(abs(sweep) / segment_angle))
    points: List[tuple[float, float]] = []
    for step in range(1, segments + 1):
        angle = start_angle + (sweep * step / segments)
        x = center_x + radius * math.cos(angle)
        y = center_y + radius * math.sin(angle)
        points.append((x, y))
    return points


def _parse_hk_params(params_text: str) -> List[str]:
    start = params_text.find("(")
    end = params_text.rfind(")")
    if start == -1 or end == -1 or end <= start:
        return []
    return _split_params(params_text[start + 1 : end])


def _split_params(param_text: str) -> List[str]:
    parts: List[str] = []
    current: List[str] = []
    in_quotes = False
    for char in param_text:
        if char == '"':
            in_quotes = not in_quotes
            current.append(char)
            continue
        if char == "," and not in_quotes:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    parts.append("".join(current).strip())
    return [part for part in parts if part != ""]


def _coerce_param(raw_value: str) -> Union[str, float]:
    cleaned = raw_value.strip()
    if not cleaned:
        return ""
    if cleaned.startswith('"') and cleaned.endswith('"') and len(cleaned) >= 2:
        return cleaned[1:-1]
    try:
        return float(cleaned)
    except ValueError:
        return cleaned


def _index_labels(lines: List[str]) -> Dict[int, int]:
    mapping: Dict[int, int] = {}
    for idx, line in enumerate(lines):
        match = LINE_LABEL_RE.match(line)
        if match:
            mapping[int(match.group("label"))] = idx
    return mapping


def _extract_hkost_details(line: str) -> tuple[Optional[float], Optional[float], Optional[int]]:
    match = HKOST_RE.search(line)
    if not match:
        return None, None, None
    parts = _split_params(match.group("params"))
    anchor_x = _coerce_float(parts[0]) if len(parts) >= 1 else None
    anchor_y = _coerce_float(parts[1]) if len(parts) >= 2 else None
    profile_line = None
    if len(parts) >= 4:
        try:
            profile_line = int(float(parts[3]))
        except ValueError:
            profile_line = None
    return anchor_x, anchor_y, profile_line


def _extract_profile_line(line: str) -> Optional[int]:
    _, _, profile_line = _extract_hkost_details(line)
    return profile_line


def _find_profile_end(lines: List[str], start_index: int) -> int:
    for idx in range(start_index, len(lines)):
        if HKPED_RE.search(lines[idx]):
            return idx
    return len(lines) - 1


def _count_contours(lines: List[str], start_index: int, end_index: int) -> int:
    if start_index < 0 or end_index < start_index:
        return 0
    contours = 0
    for line in lines[start_index : end_index + 1]:
        if HKSTR_RE.search(line):
            contours += 1
    return contours


def _split_contour_blocks(lines: List[str]) -> List[List[str]]:
    blocks: List[List[str]] = []
    current: List[str] = []
    in_block = False

    for line in lines:
        normalized = line.strip()
        if HKSTR_RE.search(normalized):
            if current:
                blocks.append(current)
            current = [line]
            in_block = True
            continue

        if in_block:
            current.append(line)
            if HKSTO_RE.search(normalized):
                blocks.append(current)
                current = []
                in_block = False
                continue

        if HKPED_RE.search(normalized):
            if current:
                blocks.append(current)
            break

    if current:
        blocks.append(current)

    return blocks


def _coerce_float(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _part_number_from_label(label: int) -> int:
    label_str = str(abs(label))
    if not label_str:
        return 0
    prefix_len = _label_prefix_length(label_str)
    return int(label_str[:prefix_len])


def _label_prefix_length(label_str: str) -> int:
    digits = len(label_str)
    if digits <= 4:
        return 1
    return min(4, digits - 4)
