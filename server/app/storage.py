from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .diagnostics import ValidationResult


def _clean_filename(name: str) -> str:
    candidate = Path(name).name  # drop any directory traversal
    if not candidate:
        return "upload.mpf"
    # Replace anything non filename-safe with underscores
    return re.sub(r"[^A-Za-z0-9._-]", "_", candidate)


def _format_number(value: float) -> float:
    # Keep storage JSON tidy and predictable
    return float(f"{value:.6f}")


@dataclass
class StoredFile:
    job_id: str
    filename: str
    stored_path: Path
    meta_path: Path
    metadata: Dict[str, Any]
    uploaded_at: datetime


class StorageManager:
    """Handles persistence of uploaded programs and their metadata."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save_upload(
        self,
        job_id: str,
        filename: str,
        content: bytes,
        description: str,
        validation: ValidationResult,
        setup: Optional[dict] = None,
    ) -> StoredFile:
        uploaded_at = datetime.now(timezone.utc)
        job_dir = self.root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        safe_name = _clean_filename(filename)
        stored_path = job_dir / safe_name
        stored_path.write_bytes(content)

        meta_path = job_dir / f"{Path(safe_name).stem}.meta.json"
        metadata = self._build_metadata(
            job_id=job_id,
            filename=safe_name,
            description=description,
            validation=validation,
            setup=setup or {},
            stored_path=stored_path,
            uploaded_at=uploaded_at,
        )
        meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return StoredFile(
            job_id=job_id,
            filename=safe_name,
            stored_path=stored_path,
            meta_path=meta_path,
            metadata=metadata,
            uploaded_at=uploaded_at,
        )

    def list_jobs(self) -> List[Dict[str, Any]]:
        jobs: List[Dict[str, Any]] = []
        if not self.root.exists():
            return jobs
        for meta_path in self.root.glob("**/*.meta.json"):
            try:
                jobs.append(json.loads(meta_path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        return jobs

    def load_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        job_dir = self.root / job_id
        if not job_dir.exists():
            return None
        meta_files = list(job_dir.glob("*.meta.json"))
        if not meta_files:
            return None
        try:
            return json.loads(meta_files[0].read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def save_part_extraction(
        self,
        source_job_id: str,
        part_label: int,
        lines: Iterable[str],
        width: float,
        height: float,
        description: str,
        base_filename: str,
    ) -> StoredFile:
        line_buffer = list(lines)
        uploaded_at = datetime.now(timezone.utc)
        job_dir = self.root / source_job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(base_filename).stem or "part"
        safe_name = _clean_filename(f"{stem}-part-{part_label}.mpf")
        stored_path = job_dir / safe_name
        stored_path.write_text("\n".join(line_buffer) + "\n", encoding="utf-8")

        meta_path = job_dir / f"{Path(safe_name).stem}.meta.json"
        metadata = {
            "jobId": f"{source_job_id}-part-{part_label}",
            "sourceJobId": source_job_id,
            "originalFile": safe_name,
            "storedPath": str(stored_path),
            "description": description,
            "uploadedAt": uploaded_at.isoformat(),
            "summary": {"errors": 0, "warnings": 0, "lines": len(line_buffer)},
            "parts": [
                {
                    "partLine": part_label,
                    "hkostLine": part_label,
                    "profileLine": None,
                    "contours": None,
                }
            ],
            "setup": {"sheetX": _format_number(width), "sheetY": _format_number(height)},
        }
        meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        return StoredFile(
            job_id=f"{source_job_id}-part-{part_label}",
            filename=safe_name,
            stored_path=stored_path,
            meta_path=meta_path,
            metadata=metadata,
            uploaded_at=uploaded_at,
        )

    def _build_metadata(
        self,
        job_id: str,
        filename: str,
        description: str,
        validation: ValidationResult,
        setup: Dict[str, Any],
        stored_path: Path,
        uploaded_at: datetime,
    ) -> Dict[str, Any]:
        return {
            "jobId": job_id,
            "originalFile": filename,
            "storedPath": str(stored_path),
            "uploadedAt": uploaded_at.isoformat(),
            "description": description,
            "summary": validation.summary,
            "parts": [
                {
                    "partNumber": part.part_number,
                    "partLine": part.part_line,
                    "hkostLine": part.hkost_line,
                    "profileLine": part.profile_line,
                    "startLine": part.start_line,
                    "endLine": part.end_line,
                    "contours": part.contours,
                    "anchorX": part.anchor_x,
                    "anchorY": part.anchor_y,
                }
                for part in validation.parts
            ],
            "setup": setup,
        }


def extract_sheet_setup(lines: Iterable[str]) -> Dict[str, Any]:
    """Pull basic setup info from HKINI blocks."""
    setup = {}
    for raw in lines:
        normalized = raw.strip()
        match = re.search(r"HKINI\((?P<params>[^)]*)\)", normalized, re.IGNORECASE)
        if not match:
            continue
        params = [p.strip() for p in match.group("params").split(",") if p.strip()]
        if len(params) >= 3:
            try:
                setup["sheetX"] = _format_number(float(params[1]))
                setup["sheetY"] = _format_number(float(params[2]))
            except ValueError:
                continue
        break
    return setup
