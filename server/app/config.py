from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
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
    storage_root: Path = Path("storage")


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
        "BP",
        "FM",
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
        "RD",
        "VE",
        "VS",
        "WHEN",
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

def load_config_from_env() -> ServiceConfig:
    storage_root = Path(os.getenv("STORAGE_ROOT", "storage"))
    audit_log = os.getenv("AUDIT_LOG_PATH", "logs/audit.log")
    app_log = os.getenv("APP_LOG_PATH", "logs/app.log")
    return ServiceConfig(
        limits=DEFAULT_LIMITS,
        rules=DEFAULT_RULES,
        audit_log_name=audit_log,
        app_log_name=app_log,
        storage_root=storage_root,
    )


DEFAULT_CONFIG = load_config_from_env()
