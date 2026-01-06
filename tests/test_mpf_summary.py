import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parser.mpf_summary import (
    build_command_table,
    collect_unique_commands,
    extract_command_code,
)


def _sample_paths() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[1]
    return list(repo_root.glob("*.MPF")) + list((repo_root / "samples").glob("*.MPF"))


def test_extract_command_skips_line_numbers_and_comments():
    assert extract_command_code("N10 G1 X0 Y0 ; rapid") == "G1"
    assert extract_command_code("N20 ; comment only") is None
    assert extract_command_code("HKLDB(2,\"S304\",3,0,0,0)") == "HKLDB"
    assert extract_command_code("WHEN ($COND) DO $A=1") == "WHEN"


def test_collect_unique_commands_from_samples():
    commands = collect_unique_commands(_sample_paths())
    assert commands == [
        "G1",
        "G2",
        "G3",
        "HKCUT",
        "HKEND",
        "HKINI",
        "HKLDB",
        "HKLEA",
        "HKOST",
        "HKPED",
        "HKPIE",
        "HKPPP",
        "HKSTO",
        "HKSTR",
        "M30",
        "WHEN",
    ]


def test_build_command_table_includes_descriptions():
    table = build_command_table(_sample_paths())
    by_command = {row["command"]: row for row in table}

    assert by_command["G1"]["description"].startswith("Linear interpolation")
    assert by_command["WHEN"]["arguments"] == ["condition", "action"]
    assert "vendor-specific" in by_command["HKCUT"]["description"].lower()


def test_compiled_command_table_matches_generated():
    repo_root = Path(__file__).resolve().parents[1]
    compiled_path = repo_root / "parser" / "compiled_command_table.json"
    compiled = json.loads(compiled_path.read_text())

    assert compiled == build_command_table(_sample_paths())
