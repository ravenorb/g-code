import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.app.parser import HKParser


def test_parse_handles_vendor_macros():
    parser = HKParser()
    parsed = parser.parse(['; header', 'N1', 'HKLDB(2,"S304",3,0,0,0)', "HKPPP"])

    assert [line.command for line in parsed] == ["HKLDB", "HKPPP"]
    assert parsed[0].params["S"] == 304.0


def test_parse_strips_line_numbers_before_command():
    parser = HKParser()
    parsed = parser.parse(
        [
            "N10000 HKOST(0.25,0.25,0.00,10001,2,0,0,0)",
            "N200 G1 X1.5 Y-2.0 F1200",
        ]
    )

    assert parsed[0].command == "HKOST"
    assert parsed[1].command == "G1"
    assert parsed[1].params == {"X": 1.5, "Y": -2.0, "F": 1200.0}


def test_parse_accepts_conditional_lines_without_numeric_params():
    parser = HKParser()
    parsed = parser.parse(
        ["WHEN ($AC_TIME>0.005)AND($R71<$R72)AND($R3==1) DO $A_DBB[10]=1"]
    )

    assert parsed[0].command == "WHEN"
    assert parsed[0].params == {}
