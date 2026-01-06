from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict

from .diagnostics import ValidationResult

logger = logging.getLogger(__name__)


class ReleaseManager:
    """Tracks validation results and records production hand-offs."""

    def __init__(self) -> None:
        self._validated_jobs: Dict[str, ValidationResult] = {}
        self._released_jobs: Dict[str, datetime] = {}

    def record_validation(self, result: ValidationResult) -> None:
        self._validated_jobs[result.job_id] = result
        logger.info("Recorded validation for job %s", result.job_id)

    def can_release(self, job_id: str) -> bool:
        result = self._validated_jobs.get(job_id)
        return bool(result and not result.has_blockers)

    def record_release(self, job_id: str, approver: str) -> datetime:
        if not self.can_release(job_id):
            raise ValueError("Job has not been validated or still has blocking issues.")
        released_at = datetime.now(timezone.utc)
        self._released_jobs[job_id] = released_at
        logger.info("Job %s released by %s at %s", job_id, approver, released_at.isoformat())
        return released_at

    def get_validation(self, job_id: str) -> ValidationResult | None:
        return self._validated_jobs.get(job_id)
