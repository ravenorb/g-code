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
        description=PLACEHOLDER_DESCRIPTION,
        arguments=["vendor_params"],
    ),
    "HKINI": CommandMetadata(
        description=PLACEHOLDER_DESCRIPTION,
        arguments=["vendor_params"],
    ),
    "HKOST": CommandMetadata(
        description=PLACEHOLDER_DESCRIPTION,
        arguments=["vendor_params"],
    ),
    "HKPPP": CommandMetadata(
        description=PLACEHOLDER_DESCRIPTION,
        arguments=["vendor_params"],
    ),
    "HKSTR": CommandMetadata(
        description=PLACEHOLDER_DESCRIPTION,
        arguments=["vendor_params"],
    ),
    "HKPIE": CommandMetadata(
        description=PLACEHOLDER_DESCRIPTION,
        arguments=["vendor_params"],
    ),
    "HKLEA": CommandMetadata(
        description=PLACEHOLDER_DESCRIPTION,
        arguments=["vendor_params"],
    ),
    "HKCUT": CommandMetadata(
        description=PLACEHOLDER_DESCRIPTION,
        arguments=["vendor_params"],
    ),
    "HKSTO": CommandMetadata(
        description=PLACEHOLDER_DESCRIPTION,
        arguments=["vendor_params"],
    ),
    "HKEND": CommandMetadata(
        description=PLACEHOLDER_DESCRIPTION,
        arguments=["vendor_params"],
    ),
    "HKPED": CommandMetadata(
        description=PLACEHOLDER_DESCRIPTION,
        arguments=["vendor_params"],
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
