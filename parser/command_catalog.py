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
        description="Conditional execution of a subsequent action",
        arguments=["condition", "action"],
    ),
    "HKLDB": CommandMetadata(
        description="Load material database and technology table for the job.",
        arguments=[
            "material_index",
            "material_grade",
            "thickness_mm",
            "db_subindex_1",
            "db_subindex_2",
            "db_subindex_3",
        ],
    ),
    "HKINI": CommandMetadata(
        description="Initialize laser setup parameters for the sheet.",
        arguments=[
            "nozzle_index",
            "focus_position",
            "gas_pressure",
            "reserved_1",
            "reserved_2",
            "reserved_3",
        ],
    ),
    "HKOST": CommandMetadata(
        description="Start piercing/cutting cycle for a contour profile.",
        arguments=[
            "pierce_time_s",
            "pierce_height",
            "angle_deg",
            "profile_line",
            "gas_index",
            "reserved_1",
            "reserved_2",
            "reserved_3",
        ],
    ),
    "HKPPP": CommandMetadata(
        description="Program point stop between contour groups.",
        arguments=[],
    ),
    "HKSTR": CommandMetadata(
        description="Move to contour start and prepare height sensing.",
        arguments=[
            "type_flag",
            "kerf_flag",
            "start_x",
            "start_y",
            "reserved_1",
            "lead_x",
            "lead_y",
            "reserved_2",
        ],
    ),
    "HKPIE": CommandMetadata(
        description="Trigger piercing cycle (vendor-specific defaults if zeros).",
        arguments=["override_1", "override_2", "override_3"],
    ),
    "HKLEA": CommandMetadata(
        description="Execute lead-in path (vendor-specific defaults if zeros).",
        arguments=["override_1", "override_2", "override_3"],
    ),
    "HKCUT": CommandMetadata(
        description="Vendor-specific cut activation (uses defaults when zeros).",
        arguments=["override_1", "override_2", "override_3"],
    ),
    "HKSTO": CommandMetadata(
        description="Stop cutting cycle and close gas flow.",
        arguments=["override_1", "override_2", "override_3"],
    ),
    "HKEND": CommandMetadata(
        description="End of contour block; return to travel height.",
        arguments=["override_1", "override_2", "override_3"],
    ),
    "HKPED": CommandMetadata(
        description="End of section; reset height control for travel.",
        arguments=["override_1", "override_2", "override_3"],
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
