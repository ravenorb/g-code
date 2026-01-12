from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple


HKOST_PATTERN = re.compile(r"HKOST\((?P<params>[^)]*)\)", re.IGNORECASE)
HKSTR_PATTERN = re.compile(r"HKSTR\((?P<params>[^)]*)\)", re.IGNORECASE)
HKINI_PATTERN = re.compile(r"HKINI\((?P<params>[^)]*)\)", re.IGNORECASE)
HKPPP_PATTERN = re.compile(r"HKPPP", re.IGNORECASE)
HKEND_PATTERN = re.compile(r"HKEND", re.IGNORECASE)
COORD_PATTERN = re.compile(r"([XY])([-+]?\d*\.?\d+)")
LINE_LABEL_PATTERN = re.compile(r"^N(\d+)", re.IGNORECASE)


@dataclass
class PartExtractionResult:
    lines: List[str]
    width: float
    height: float


def extract_part_program(content: str, part_label: int, margin: float = 0.0) -> PartExtractionResult:
    """Create a standalone program that contains only the requested part placed at origin."""
    lines = [line.rstrip() for line in content.splitlines() if line.strip()]
    label_to_index = _index_labels(lines)
    if part_label not in label_to_index:
        raise ValueError(f"Part label {part_label} not found.")

    hkost_idx = label_to_index[part_label]
    hkost_line = lines[hkost_idx]
    profile_line = _extract_profile_line(hkost_line)
    if profile_line is None:
        raise ValueError("HKOST line missing profile reference.")
    if profile_line not in label_to_index:
        raise ValueError(f"Profile line {profile_line} not found for part {part_label}.")

    block_start = label_to_index[profile_line]
    block_end = _find_block_end(lines, block_start)
    if block_end is None:
        raise ValueError("Unable to find HKPED terminator for part.")

    # Collect supporting header lines
    hkldb_line = _first_match(lines, r"HKLDB")
    hkini_line = _first_match(lines, r"HKINI")
    hkppp_line = _find_hkppp_after(lines, hkost_idx) or "HKPPP"
    hkend_line = "HKEND(0,0,0)"

    part_lines = lines[block_start : block_end + 1]
    min_x, min_y, max_x, max_y = _bounds_for_block(part_lines)
    dx, dy = min_x, min_y
    width = (max_x - min_x) + margin
    height = (max_y - min_y) + margin

    translated_block = [_translate_block_line(line, dx, dy) for line in part_lines]
    translated_hkost = _translate_hkost(hkost_line, dx, dy)
    translated_hkini = _translate_hkini(hkini_line, width, height) if hkini_line else None

    output: List[str] = []
    output.append(f"; Extracted part {part_label} from source program")
    if hkldb_line:
        output.append(hkldb_line)
    if translated_hkini:
        output.append(translated_hkini)
    else:
        output.append(f"HKINI(0,{_format_float(width)},{_format_float(height)},0,0,0)")
    output.append(translated_hkost)
    output.append(hkppp_line)
    output.extend(translated_block)
    output.append(hkend_line)
    output.append("M30")

    return PartExtractionResult(lines=output, width=width, height=height)


def extract_part_profile_program(content: str, part_line: int, margin: float = 0.0) -> PartExtractionResult:
    """Create a standalone program that contains only the HKSTR -> HKSTO block."""
    lines = [line.rstrip() for line in content.splitlines() if line.strip()]
    label_to_index = _index_labels(lines)
    if part_line in label_to_index:
        start_idx = label_to_index[part_line]
    else:
        if 1 <= part_line <= len(lines):
            start_idx = part_line - 1
        else:
            raise ValueError(f"Part line {part_line} not found.")
    if not HKSTR_PATTERN.search(lines[start_idx]):
        raise ValueError(f"Line {part_line} is not an HKSTR declaration.")

    end_idx = _find_profile_end(lines, start_idx)
    if end_idx is None:
        raise ValueError("Unable to find HKSTO terminator for part.")

    hkldb_line = _first_match(lines, r"HKLDB")
    hkini_line = _first_match(lines, r"HKINI")
    hkppp_line = _find_hkppp_after(lines, start_idx) or "HKPPP"
    hkend_line = "HKEND(0,0,0)"

    part_lines = lines[start_idx : end_idx + 1]
    min_x, min_y, max_x, max_y = _bounds_for_block(part_lines)
    dx, dy = min_x, min_y
    width = (max_x - min_x) + margin
    height = (max_y - min_y) + margin

    translated_block = [_translate_block_line(line, dx, dy) for line in part_lines]
    translated_hkini = _translate_hkini(hkini_line, width, height) if hkini_line else None

    output: List[str] = []
    output.append(f"; Extracted HKSTR part {part_line} from source program")
    if hkldb_line:
        output.append(hkldb_line)
    if translated_hkini:
        output.append(translated_hkini)
    else:
        output.append(f"HKINI(0,{_format_float(width)},{_format_float(height)},0,0,0)")
    output.append(hkppp_line)
    output.extend(translated_block)
    output.append(hkend_line)
    output.append("M30")

    return PartExtractionResult(lines=output, width=width, height=height)


def _index_labels(lines: List[str]) -> dict[int, int]:
    mapping = {}
    for idx, line in enumerate(lines):
        match = LINE_LABEL_PATTERN.match(line)
        if match:
            mapping[int(match.group(1))] = idx
    return mapping


def _extract_profile_line(hkost_line: str) -> int | None:
    match = HKOST_PATTERN.search(hkost_line)
    if not match:
        return None
    parts = [p.strip() for p in match.group("params").split(",") if p.strip()]
    if len(parts) < 4:
        return None
    try:
        return int(float(parts[3]))
    except ValueError:
        return None


def _find_block_end(lines: List[str], start_idx: int) -> int | None:
    for idx in range(start_idx, len(lines)):
        if "HKPED" in lines[idx].upper():
            return idx
    return None


def _find_profile_end(lines: List[str], start_idx: int) -> int | None:
    for idx in range(start_idx, len(lines)):
        if "HKSTO" in lines[idx].upper():
            return idx
    return None


def _first_match(lines: List[str], pattern: str) -> str | None:
    compiled = re.compile(pattern, re.IGNORECASE)
    for line in lines:
        if compiled.search(line):
            return line
    return None


def _find_hkppp_after(lines: List[str], start_idx: int) -> str | None:
    for idx in range(start_idx, min(start_idx + 3, len(lines))):
        if HKPPP_PATTERN.search(lines[idx]):
            return lines[idx]
    return _first_match(lines, r"HKPPP")


def _bounds_for_block(lines: List[str]) -> Tuple[float, float, float, float]:
    xs: List[float] = []
    ys: List[float] = []
    for line in lines:
        # HKSTR contains start + lead target
        match = HKSTR_PATTERN.search(line)
        if match:
            params = [p.strip() for p in match.group("params").split(",") if p.strip()]
            if len(params) >= 7:
                try:
                    xs.append(float(params[2]))
                    ys.append(float(params[3]))
                    xs.append(float(params[5]))
                    ys.append(float(params[6]))
                except ValueError:
                    pass
        for coord, value in COORD_PATTERN.findall(line):
            try:
                if coord.upper() == "X":
                    xs.append(float(value))
                else:
                    ys.append(float(value))
            except ValueError:
                continue
    if not xs or not ys:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


def _translate_block_line(line: str, dx: float, dy: float) -> str:
    if HKSTR_PATTERN.search(line):
        return _translate_hkstr(line, dx, dy)

    def replace(match: re.Match[str]) -> str:
        axis, raw_value = match.groups()
        try:
            value = float(raw_value)
        except ValueError:
            return match.group(0)
        shifted = value - (dx if axis.upper() == "X" else dy)
        return f"{axis}{shifted:.4f}"

    return COORD_PATTERN.sub(replace, line)


def _translate_hkstr(line: str, dx: float, dy: float) -> str:
    match = HKSTR_PATTERN.search(line)
    if not match:
        return line
    params = [p.strip() for p in match.group("params").split(",")]
    if len(params) >= 4:
        params[2] = _format(params[2], dx)
        params[3] = _format(params[3], dy)
    if len(params) >= 7:
        params[5] = _format(params[5], dx)
        params[6] = _format(params[6], dy)
    updated = ",".join(params)
    return HKSTR_PATTERN.sub(f"HKSTR({updated})", line)


def _translate_hkost(line: str, dx: float, dy: float) -> str:
    match = HKOST_PATTERN.search(line)
    if not match:
        return line
    params = [p.strip() for p in match.group("params").split(",")]
    if len(params) >= 2:
        params[0] = _format(params[0], dx)
        params[1] = _format(params[1], dy)
    updated = ",".join(params)
    return HKOST_PATTERN.sub(f"HKOST({updated})", line)


def _translate_hkini(line: str, width: float, height: float) -> str:
    match = HKINI_PATTERN.search(line)
    if not match:
        return line
    params = [p.strip() for p in match.group("params").split(",")]
    if len(params) >= 3:
        params[1] = _format_float(width)
        params[2] = _format_float(height)
    updated = ",".join(params)
    return HKINI_PATTERN.sub(f"HKINI({updated})", line)


def _format(raw_value: str, delta: float) -> str:
    try:
        as_float = float(raw_value)
    except ValueError:
        return raw_value
    return _format_float(as_float - delta)


def _format_float(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")
