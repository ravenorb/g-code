from __future__ import annotations

from dataclasses import dataclass
from typing import List, Set


@dataclass(frozen=True)
class ParserLimits:
    max_feed_rate: float = 5000.0
    max_power: float = 100.0
    min_feed_rate: float = 1.0
    min_power: float = 0.0


@dataclass(frozen=True)
class CommandRules:
    whitelist: Set[str]
    blacklist: Set[str]


@dataclass(frozen=True)
class ServiceConfig:
    limits: ParserLimits
    rules: CommandRules
    audit_log_name: str = "logs/audit.log"
    app_log_name: str = "logs/app.log"


DEFAULT_RULES = CommandRules(
    whitelist={
        "G0",
        "G1",
        "G2",
        "G3",
        "G4",
        "G28",
        "G90",
        "G91",
        "M3",
        "M4",
        "M5",
        "M30",
    },
    blacklist={
        "M0",
        "M1",
        "M112",
        "M410",
        "G92",
    },
)

DEFAULT_LIMITS = ParserLimits()

DEFAULT_CONFIG = ServiceConfig(limits=DEFAULT_LIMITS, rules=DEFAULT_RULES)
