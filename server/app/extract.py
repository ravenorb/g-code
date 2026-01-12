from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Tuple

from .parser import extract_part_contour_block


HKOST_PATTERN = re.compile(r"HKOST\((?P<params>[^)]*)\)", re.IGNORECASE)
HKSTR_PATTERN = re.compile(r"HKSTR\((?P<params>[^)]*)\)", re.IGNORECASE)
HKINI_PATTERN = re.compile(r"HKINI\((?P<params>[^)]*)\)", re.IGNORECASE)
HKPPP_PATTERN = re.compile(r"HKPPP", re.IGNORECASE)
COORD_PATTERN = re.compile(r"([XY])([-+]?\d*\.?\d+)")
LINE_LABEL_PATTERN = re.compile(r"^N(\d+)", re.IGNORECASE)


@dataclass
class PartExtractionResult:
    lines: List[str]
    width: float
    height: float


def extract_part_program(
    content: str,
    part_label: int,
    margin: float = 0.0,
    extra_contours: List[tuple[int, int]] | None = None,
) -> PartExtractionResult:
    """Create a standalone program that contains only the requested part definition."""
    lines = [line.rstrip() for line in content.splitlines() if line.strip()]
    label_to_index = _index_labels(lines)
    if part_label not in label_to_index:
        raise ValueError(f"Part label {part_label} not found.")

    hkost_idx = label_to_index[part_label]
    hkost_line = lines[hkost_idx]
    target_anchor = _extract_hkost_anchor(hkost_line)
    profile_line = _extract_profile_line(hkost_line)
    if profile_line is None:
        raise ValueError("HKOST line missing profile reference.")
    if profile_line not in label_to_index:
        raise ValueError(f"Profile line {profile_line} not found for part {part_label}.")

    block_start = label_to_index[profile_line]
    block_end = _find_block_end(lines, block_start)
    if block_end is None:
        raise ValueError("Unable to find HKPED terminator for part.")

    part_lines = lines[block_start : block_end + 1]
    if extra_contours:
        extra_blocks: List[List[str]] = []
        for part_line, contour_index in extra_contours:
            block = extract_part_contour_block(lines, part_line, contour_index)
            if not block:
                continue
            if target_anchor:
                source_anchor = _extract_part_anchor(lines, label_to_index, part_line)
                if source_anchor:
                    dx = target_anchor[0] - source_anchor[0]
                    dy = target_anchor[1] - source_anchor[1]
                    block = [_offset_block_line(line, dx, dy) for line in block]
            extra_blocks.append(block)
        if extra_blocks:
            part_lines = _insert_extra_contours(part_lines, extra_blocks)
    trailer_lines, _ = _collect_until_hkppp(lines, hkost_idx)
    min_x, min_y, max_x, max_y = _bounds_for_block(part_lines)
    width = (max_x - min_x) + margin
    height = (max_y - min_y) + margin
    hkini_width = width + 1.0
    hkini_height = height + 1.0

    header_lines = _header_lines(lines)
    footer_lines = _footer_lines(lines, strip_contours=True)

    output: List[str] = []
    output.extend(header_lines)
    output.append(hkost_line)
    output.extend(trailer_lines)
    output.extend(part_lines)
    output.extend(footer_lines)
    output = [_translate_hkini(line, hkini_width, hkini_height) for line in output]

    return PartExtractionResult(lines=output, width=width, height=height)


def extract_part_profile_program(content: str, part_line: int, margin: float = 0.0) -> PartExtractionResult:
    """Create a part profile block that includes HKOST + contours + HKPPP."""
    lines = [line.rstrip() for line in content.splitlines() if line.strip()]
    label_to_index = _index_labels(lines)
    if part_line not in label_to_index:
        raise ValueError(f"Part line {part_line} not found.")

    hkost_idx = label_to_index[part_line]
    hkost_line = lines[hkost_idx]
    profile_line = _extract_profile_line(hkost_line)
    if profile_line is None:
        raise ValueError("HKOST line missing profile reference.")
    if profile_line not in label_to_index:
        raise ValueError(f"Profile line {profile_line} not found for part {part_line}.")

    block_start = label_to_index[profile_line]
    block_end = _find_block_end(lines, block_start)
    if block_end is None:
        raise ValueError("Unable to find HKPED terminator for part.")

    part_lines = lines[block_start : block_end + 1]
    trailer_lines, _ = _collect_until_hkppp(lines, hkost_idx)
    min_x, min_y, max_x, max_y = _bounds_for_block(part_lines)
    width = (max_x - min_x) + margin
    height = (max_y - min_y) + margin

    output: List[str] = []
    output.append(hkost_line)
    output.extend(part_lines)
    output.extend(trailer_lines)

    return PartExtractionResult(lines=output, width=width, height=height)


def _index_labels(lines: List[str]) -> dict[int, int]:
    mapping = {}
    for idx, line in enumerate(lines):
        match = LINE_LABEL_PATTERN.match(line)
        if match:
            mapping[int(match.group(1))] = idx
    return mapping


def _strip_label(line: str) -> tuple[int | None, str]:
    match = LINE_LABEL_PATTERN.match(line)
    if not match:
        return None, line.lstrip()
    return int(match.group(1)), line[match.end() :].lstrip()


def _insert_extra_contours(part_lines: List[str], extra_blocks: List[List[str]]) -> List[str]:
    hkped_index = None
    for idx, line in enumerate(part_lines):
        if "HKPED" in line.upper():
            hkped_index = idx
            break

    if hkped_index is None:
        return part_lines

    labels = [label for line in part_lines if (label := _strip_label(line)[0]) is not None]
    next_label = (max(labels) if labels else 0) + 1

    updated_lines = part_lines[:hkped_index]
    for block in extra_blocks:
        for line in block:
            _, content = _strip_label(line)
            updated_lines.append(f"N{next_label} {content}".rstrip())
            next_label += 1

    _, hkped_content = _strip_label(part_lines[hkped_index])
    updated_lines.append(f"N{next_label} {hkped_content}".rstrip())
    updated_lines.extend(part_lines[hkped_index + 1 :])
    return updated_lines


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


def _extract_hkost_anchor(hkost_line: str) -> tuple[float, float] | None:
    match = HKOST_PATTERN.search(hkost_line)
    if not match:
        return None
    params = [p.strip() for p in match.group("params").split(",") if p.strip()]
    if len(params) < 2:
        return None
    try:
        return float(params[0]), float(params[1])
    except ValueError:
        return None


def _extract_part_anchor(
    lines: List[str],
    label_to_index: dict[int, int],
    part_line: int,
) -> tuple[float, float] | None:
    hkost_idx = label_to_index.get(part_line)
    if hkost_idx is None:
        return None
    return _extract_hkost_anchor(lines[hkost_idx])


def _first_match(lines: List[str], pattern: str) -> str | None:
    compiled = re.compile(pattern, re.IGNORECASE)
    for line in lines:
        if compiled.search(line):
            return line
    return None


def _collect_until_hkppp(lines: List[str], start_idx: int) -> tuple[List[str], int | None]:
    for idx in range(start_idx + 1, len(lines)):
        if HKPPP_PATTERN.search(lines[idx]):
            return lines[start_idx + 1 : idx + 1], idx
    return lines[start_idx + 1 :], None


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


def _offset_block_line(line: str, dx: float, dy: float) -> str:
    if HKSTR_PATTERN.search(line):
        return _offset_hkstr(line, dx, dy)

    def replace(match: re.Match[str]) -> str:
        axis, raw_value = match.groups()
        try:
            value = float(raw_value)
        except ValueError:
            return match.group(0)
        shifted = value + (dx if axis.upper() == "X" else dy)
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


def _offset_hkstr(line: str, dx: float, dy: float) -> str:
    match = HKSTR_PATTERN.search(line)
    if not match:
        return line
    params = [p.strip() for p in match.group("params").split(",")]
    if len(params) >= 4:
        params[2] = _format_offset(params[2], dx)
        params[3] = _format_offset(params[3], dy)
    if len(params) >= 7:
        params[5] = _format_offset(params[5], dx)
        params[6] = _format_offset(params[6], dy)
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


def _header_lines(lines: List[str]) -> List[str]:
    first_hkost = _find_first_hkost_index(lines)
    if first_hkost is None:
        return list(lines)
    return lines[:first_hkost]


def _footer_lines(lines: List[str], strip_contours: bool = False) -> List[str]:
    last_hkppp = _find_last_hkppp_index(lines)
    if last_hkppp is None:
        return []
    footer = lines[last_hkppp + 1 :]
    if not strip_contours:
        return footer
    return _remove_contour_blocks(footer)


def _find_first_hkost_index(lines: List[str]) -> int | None:
    for idx, line in enumerate(lines):
        if HKOST_PATTERN.search(line):
            return idx
    return None


def _find_last_hkppp_index(lines: List[str]) -> int | None:
    last_idx = None
    for idx, line in enumerate(lines):
        if HKPPP_PATTERN.search(line):
            last_idx = idx
    return last_idx


def _remove_contour_blocks(lines: List[str]) -> List[str]:
    filtered: List[str] = []
    in_block = False
    for line in lines:
        normalized = line.strip()
        if HKSTR_PATTERN.search(normalized):
            in_block = True
        if not in_block:
            filtered.append(line)
        if in_block and "HKPED" in normalized.upper():
            in_block = False
    return filtered


def _format(raw_value: str, delta: float) -> str:
    try:
        as_float = float(raw_value)
    except ValueError:
        return raw_value
    return _format_float(as_float - delta)


def _format_offset(raw_value: str, delta: float) -> str:
    try:
        as_float = float(raw_value)
    except ValueError:
        return raw_value
    return _format_float(as_float + delta)


def _format_float(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")
