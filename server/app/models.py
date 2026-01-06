from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class DiagnosticModel(BaseModel):
    severity: str
    message: str
    line: Optional[int] = Field(default=None, description="Line number associated with diagnostic.")
    code: Optional[str] = Field(default=None, description="Machine-readable diagnostic code.")


class ValidationSummary(BaseModel):
    errors: int
    warnings: int
    lines: int


class ValidationResponse(BaseModel):
    job_id: str
    diagnostics: List[DiagnosticModel]
    summary: ValidationSummary


class ReleaseRequest(BaseModel):
    job_id: str
    approver: str


class ReleaseResponse(BaseModel):
    job_id: str
    status: str
    approved_by: str
    released_at: datetime
    notes: Optional[str] = None


class ValidateRequest(BaseModel):
    job_id: Optional[str] = Field(default=None, description="Identifier for the g-code job.")
    gcode: str = Field(description="Raw g-code content to validate.")
