from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
import re
from typing import Iterable, List, Optional

from .config import ServiceConfig
from .parser import HKParser, ParsedLine, PartSummary, _strip_line_label, load_from_bytes

logger = logging.getLogger(__name__)


def _normalize_when(raw: str) -> str:
    stripped = _strip_line_label(raw.strip()).upper()
    return re.sub(r"\s+", "", stripped)


def _has_executable_content(raw: str) -> bool:
    stripped = raw.strip()
    if not stripped or stripped.startswith(";"):
        return False
    return bool(_strip_line_label(stripped).strip())


@dataclass
class Diagnostic:
    severity: str
    message: str
    line: Optional[int] = None
    code: Optional[str] = None


@dataclass
class ValidationResult:
    job_id: str
    diagnostics: List[Diagnostic]
    parsed: List[ParsedLine]
    parts: List[PartSummary]
    raw_lines: List[str]

    @property
    def has_blockers(self) -> bool:
        return any(d.severity == "error" for d in self.diagnostics)

    @property
    def summary(self) -> dict:
        return {
            "errors": len([d for d in self.diagnostics if d.severity == "error"]),
            "warnings": len([d for d in self.diagnostics if d.severity == "warning"]),
            "lines": len(self.parsed),
        }


class ValidationService:
    def __init__(self, config: ServiceConfig):
        self._config = config
        self._parser = HKParser()

    def validate_lines(self, job_id: str, lines: Iterable[str]) -> ValidationResult:
        diagnostics: List[Diagnostic] = []
        parsed: List[ParsedLine] = []
        line_buffer = list(lines)
        parts = self._parser.summarize_parts(line_buffer)
        try:
            parsed = self._parser.parse(line_buffer)
        except ValueError as exc:
            diagnostics.append(Diagnostic(severity="error", message=str(exc)))
            return ValidationResult(job_id=job_id, diagnostics=diagnostics, parsed=parsed, parts=parts, raw_lines=line_buffer)

        for line in parsed:
            command = line.command.upper()
            if command in self._config.rules.blacklist:
                diagnostics.append(
                    Diagnostic(
                        severity="error",
                        message=f"Command {command} is not allowed (blacklisted)",
                        line=line.line_number,
                        code="blacklisted_command",
                    )
                )
            elif command not in self._config.rules.whitelist:
                diagnostics.append(
                    Diagnostic(
                        severity="warning",
                        message=f"Command {command} is outside the approved whitelist",
                        line=line.line_number,
                        code="nonwhitelisted_command",
                    )
                )

            feed = line.params.get("F")
            if feed is not None:
                if feed > self._config.limits.max_feed_rate:
                    diagnostics.append(
                        Diagnostic(
                            severity="error",
                            message=f"Feed rate {feed} exceeds limit {self._config.limits.max_feed_rate}",
                            line=line.line_number,
                            code="feed_rate_high",
                        )
                    )
                elif feed < self._config.limits.min_feed_rate:
                    diagnostics.append(
                        Diagnostic(
                            severity="warning",
                            message=f"Feed rate {feed} is below minimum {self._config.limits.min_feed_rate}",
                            line=line.line_number,
                            code="feed_rate_low",
                        )
                    )

            power = line.params.get("S") or line.params.get("P")
            if power is not None:
                if power > self._config.limits.max_power:
                    diagnostics.append(
                        Diagnostic(
                            severity="error",
                            message=f"Power {power} exceeds limit {self._config.limits.max_power}",
                            line=line.line_number,
                            code="power_high",
                        )
                    )
                elif power < self._config.limits.min_power:
                    diagnostics.append(
                        Diagnostic(
                            severity="warning",
                            message=f"Power {power} is below minimum {self._config.limits.min_power}",
                            line=line.line_number,
                            code="power_low",
                        )
                    )

        termination_line = None
        termination_command = None
        for line in parsed:
            if line.command.upper() == "M30":
                termination_line = line.line_number
                termination_command = line.command.upper()
                break

        if termination_line is not None:
            for line_number, raw in enumerate(line_buffer[termination_line:], start=termination_line + 1):
                if _has_executable_content(raw):
                    diagnostics.append(
                        Diagnostic(
                            severity="error",
                            message=f"Command found after {termination_command} on line {termination_line}.",
                            line=line_number,
                            code="content_after_end",
                        )
                    )
                    break

        previous_line: Optional[ParsedLine] = None
        for line in parsed:
            if line.command.upper() == "WHEN" and previous_line and previous_line.command.upper() == "WHEN":
                if _normalize_when(line.raw) == _normalize_when(previous_line.raw):
                    diagnostics.append(
                        Diagnostic(
                            severity="error",
                            message="Duplicate WHEN command repeated on consecutive lines.",
                            line=line.line_number,
                            code="duplicate_when",
                        )
                    )
            previous_line = line

        return ValidationResult(job_id=job_id, diagnostics=diagnostics, parsed=parsed, parts=parts, raw_lines=line_buffer)

    def validate_bytes(self, job_id: str, content: bytes) -> ValidationResult:
        return self.validate_lines(job_id=job_id, lines=load_from_bytes(content))


def hash_payload(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
