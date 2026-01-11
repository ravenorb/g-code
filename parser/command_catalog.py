"""Command metadata for HK MPF files.

This module centralizes short descriptions and argument hints for the
commands we encounter in sample HK MPF programs. The goal is to provide a
consistent source of metadata that other tooling (for example command
summaries) can use when surfacing information to users.

Because several HK macros are undocumented, placeholder descriptions are
included so the generated tables clearly indicate where more research is
required.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List


@dataclass(frozen=True)
class CommandMetadata:
    """Describes a single command's intent and accepted arguments."""

    description: str
    arguments: List[str]


PLACEHOLDER_DESCRIPTION = (
    "Vendor-specific macro observed in sample MPF files. More documentation "
    "is needed for an authoritative description."
)


COMMAND_CATALOG: Dict[str, CommandMetadata] = {
    "G0": CommandMetadata(
        description="Rapid positioning move",
        arguments=["X", "Y", "Z", "F"],
    ),
    "G1": CommandMetadata(
        description="Linear interpolation move",
        arguments=["X", "Y", "Z", "F"],
    ),
    "G2": CommandMetadata(
        description="Clockwise circular interpolation",
        arguments=["X", "Y", "Z", "I", "J", "K", "F", "R"],
    ),
    "G3": CommandMetadata(
        description="Counter-clockwise circular interpolation",
        arguments=["X", "Y", "Z", "I", "J", "K", "F", "R"],
    ),
    "M3": CommandMetadata(
        description="Spindle/laser on (clockwise)", arguments=["S"],
    ),
    "M5": CommandMetadata(description="Spindle/laser stop", arguments=[]),
    "M30": CommandMetadata(description="Program end and rewind", arguments=[]),
    "VS": CommandMetadata(description="Vendor speed setpoint", arguments=["P"]),
    "VE": CommandMetadata(description="Vendor engrave power", arguments=["P"]),
    "FM": CommandMetadata(description="Firmware metadata record", arguments=[]),
    "BP": CommandMetadata(description="Backlash compensation parameter", arguments=["P"]),
    "RD": CommandMetadata(description="Raster density parameter", arguments=["P"]),
    "WHEN": CommandMetadata(
        description=(
            "Conditional command (usually to signal piercing complete or cutting start – timing/condition based)."
        ),
        arguments=["condition", "action"],
    ),
    "HKLDB": CommandMetadata(
        description="Load database / machine configuration (usually at program start).",
        arguments=[
            "technology",
            "material_name",
            "material_type",
            "reserved_1",
            "reserved_2",
            "reserved_3",
        ],
    ),
    "HKINI": CommandMetadata(
        description="Initialize machine (set raw material size).",
        arguments=[
            "mode",
            "material_x",
            "material_y",
            "reserved_1",
            "reserved_2",
            "reserved_3",
        ],
    ),
    "HKOST": CommandMetadata(
        description=(
            "Operation start (x offset, y offset, z offset, sub line number, "
            "technology, 0,0,0)."
        ),
        arguments=[
            "offset_x",
            "offset_y",
            "offset_z",
            "sub_id",
            "technology",
            "reserved_1",
            "reserved_2",
            "reserved_3",
        ],
    ),
    "HKPPP": CommandMetadata(
        description="End of operation registration block.",
        arguments=[],
    ),
    "HKSTR": CommandMetadata(
        description=(
            "Start contour (type: 0=outer/chain, 1=inner hole; leadX/Y = approach vector)."
        ),
        arguments=[
            "contour_type",
            "kerf_mode",
            "pierce_x",
            "pierce_y",
            "reserved_1",
            "lead_x",
            "lead_y",
            "reserved_2",
        ],
    ),
    "HKPIE": CommandMetadata(
        description="Pierce start (pierce sequence begins).",
        arguments=["reserved_1", "reserved_2", "reserved_3"],
    ),
    "HKLEA": CommandMetadata(
        description="Lead-in start (approach move – laser/plasma off).",
        arguments=["reserved_1", "reserved_2", "reserved_3"],
    ),
    "HKCUT": CommandMetadata(
        description="Start cutting (laser/plasma on).",
        arguments=["reserved_1", "reserved_2", "reserved_3"],
    ),
    "HKSTO": CommandMetadata(
        description="Stop contour (end of cutting path).",
        arguments=["reserved_1", "reserved_2", "reserved_3"],
    ),
    "HKEND": CommandMetadata(
        description="End of program (after all contours).",
        arguments=["reserved_1", "reserved_2", "reserved_3"],
    ),
    "HKPED": CommandMetadata(
        description="Program end (optional – sometimes used for final cleanup or machine reset).",
        arguments=["reserved_1", "reserved_2", "reserved_3"],
    ),
}


def describe_command(code: str) -> CommandMetadata:
    """Return metadata for a command, falling back to a placeholder."""

    return COMMAND_CATALOG.get(
        code,
        CommandMetadata(
            description=PLACEHOLDER_DESCRIPTION,
            arguments=["vendor_params"],
        ),
    )


__all__: Iterable[str] = [
    "COMMAND_CATALOG",
    "CommandMetadata",
    "PLACEHOLDER_DESCRIPTION",
    "describe_command",
]
