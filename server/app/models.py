from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

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


class ParsedFieldModel(BaseModel):
    name: str
    value: Any


class ParsedLineModel(BaseModel):
    line_number: int
    raw: str
    command: str
    description: str
    arguments: List[str]
    fields: List[ParsedFieldModel]


class PartSummaryModel(BaseModel):
    hkost_line: int = Field(description="Line label for the HKOST declaration (e.g., 10000).")
    profile_line: Optional[int] = Field(default=None, description="Profile line label referenced by HKOST.")
    contours: int = Field(description="Number of G-code contour lines between HKCUT and HKSTO.")
    x: Optional[float] = Field(default=None, description="X coordinate from the HKOST declaration.")
    y: Optional[float] = Field(default=None, description="Y coordinate from the HKOST declaration.")


class PartDetailModel(PartSummaryModel):
    profile_block: List[str] = Field(default_factory=list, description="Profile block lines for the selected part.")


class ValidationResponse(BaseModel):
    job_id: str
    diagnostics: List[DiagnosticModel]
    summary: ValidationSummary
    parsed_lines: List[ParsedLineModel] = Field(default_factory=list)
    parts: List[PartSummaryModel] = Field(default_factory=list)


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


class UploadResponse(ValidationResponse):
    stored_path: Optional[str] = Field(default=None, description="Filesystem path of the uploaded program.")
    meta_path: Optional[str] = Field(default=None, description="Path to the generated metadata file.")
    description: Optional[str] = Field(default=None, description="User-provided description of the upload.")
    uploaded_at: Optional[datetime] = Field(default=None, description="Timestamp of upload.")


class ExtractRequest(BaseModel):
    job_id: str = Field(description="Existing job identifier (hash) to extract from.")
    part_label: int = Field(description="HKOST label of the part to extract.")
    margin: float = Field(default=0.0, description="Additional margin to add around the part when sizing the sheet.")
    description: Optional[str] = Field(default=None, description="Optional description to store with the extracted part.")


class ExtractResponse(BaseModel):
    job_id: str
    part_label: int
    stored_path: str
    meta_path: str
    width: float
    height: float
    filename: str


class JobListing(BaseModel):
    jobId: str
    originalFile: str
    storedPath: str
    description: Optional[str] = None
    uploadedAt: Optional[str] = None
    summary: Optional[Any] = None
    parts: Optional[Any] = None
