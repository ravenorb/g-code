import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from parser.hk_gcode_parser import DEFAULT_TOLERANCE, Command, ParseError, parse_program


def test_parses_line_with_comments_and_parameters():
    program = parse_program("G1 X10.0 Y-5.5 F1200 ; cut pass")
    assert not program.errors
    assert len(program.commands) == 1
    command = program.commands[0]
    assert command.code == "G1"
    assert command.parameters == {"X": 10.0, "Y": -5.5, "F": 1200.0}
    assert command.comment == "cut pass"


def test_parenthetical_comment_is_captured():
    program = parse_program("G0 X0 Y0 (rapid move)")
    assert len(program.commands) == 1
    assert program.commands[0].comment == "rapid move"


def test_required_hk_parameter_enforced():
    program = parse_program("VS P1.2\nVS")
    assert len(program.commands) == 1
    assert program.commands[0].code == "VS"
    assert program.commands[0].parameters == {"P": 1.2}
    assert len(program.errors) == 1
    assert "Missing required parameter" in program.errors[0].message


def test_malformed_parameter_and_number_are_reported():
    malformed = parse_program("G1 X")
    assert isinstance(malformed.errors[0], ParseError)
    assert "Malformed parameter" in malformed.errors[0].message

    bad_number = parse_program("G1 X10..3")
    assert "Malformed parameter" in bad_number.errors[0].message


def test_round_trip_serialization_respects_tolerance():
    program = parse_program("G1 X1.00009 Y2.5")
    command = program.commands[0]
    serialized = command.to_line(tolerance=DEFAULT_TOLERANCE)
    reparsed = parse_program(serialized)
    reparsed_command = reparsed.commands[0]
    assert reparsed_command.parameters["X"] == pytest.approx(1.0)
    assert reparsed_command.parameters["Y"] == pytest.approx(2.5)


def test_unclosed_parenthetical_comment_is_error():
    program = parse_program("G1 X1 (oops")
    assert program.errors
    assert "Unclosed parenthetical comment" in program.errors[0].message


def test_comment_only_lines_are_preserved_as_metadata():
    program = parse_program("; header\nG0 X0\n(another)\n")
    assert "header" in program.metadata["comments"][0]
    assert "another" in program.metadata["comments"][1]
    assert program.commands[0].code == "G0"


def test_function_style_command_with_block_number_and_string_arg():
    program = parse_program('N10000 HKOST(0.3,0.26,"S304",0)')
    command = program.commands[0]
    assert command.block_number == 10000
    assert command.code == "HKOST"
    assert command.args[:3] == [0.3, 0.26, "S304"]
    assert command.args[-1] == 0


def test_when_command_payload_is_preserved():
    text = "WHEN ($AC_TIME>0.005)AND($R71<$R72) DO $A_DBB[10]=1"
    program = parse_program(text)
    command = program.commands[0]
    assert command.code == "WHEN"
    assert "$A_DBB" in (command.payload or "")


def test_block_only_line_round_trips():
    program = parse_program("N42")
    assert program.commands[0].code == "BLOCK"
    assert program.commands[0].to_line() == "N42"


def test_sample_file_parses_without_errors():
    sample_path = Path(__file__).resolve().parents[1] / "samples" / "12GA Temp2.MPF"
    content = sample_path.read_text(encoding="utf-8")
    program = parse_program(content)
    assert not program.errors
    assert any(cmd.code == "HKOST" for cmd in program.commands)
    assert any(cmd.code == "HKSTR" for cmd in program.commands)
