from __future__ import annotations

from pathlib import Path
from typing import Dict, Set

from pydantic import BaseModel, Field, validator
from pydantic_settings import BaseSettings


DEFAULT_TECH_MAPPING: Dict[str, Dict[str, Dict[str, int]]] = {
    "S304": {
        # Default mapping used when no thickness-specific entry is present.
        "default": {
            "contour": 5,
            "slot": 3,
            "pierce-only": 9,
        },
        # Common sheet modes observed in samples.
        "mode-2": {"contour": 5, "slot": 3},
        "mode-15": {"contour": 1},
        "mode-68": {"contour": 5},
    }
}


class TechnologyConfig(BaseModel):
    table: Dict[str, Dict[str, Dict[str, int]]] = Field(
        default_factory=lambda: DEFAULT_TECH_MAPPING,
        description="Material/thickness/opType → technology number mapping.",
    )


class AppSettings(BaseSettings):
    nas_path: Path = Field(default=Path("/mnt/nas/gcode/uploads"), env="NAS_PATH")
    max_upload_mb: int = Field(default=15, env="MAX_UPLOAD_MB")
    allowed_extensions: Set[str] = Field(default_factory=lambda: {".mpf", ".gcode", ".txt"})
    technology: TechnologyConfig = Field(default_factory=TechnologyConfig)
    sheet_margin: float = Field(default=1.0, description="Margin added around extracted parts (mm).")

    @validator("nas_path", pre=True)
    def _expand_path(cls, value: str | Path) -> Path:
        return Path(value).expanduser()

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


settings = AppSettings()
