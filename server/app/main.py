from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from .config import DEFAULT_CONFIG, ServiceConfig
from .diagnostics import ValidationService, hash_payload
from .extract import extract_part_program
from .models import (
    DiagnosticModel,
    ExtractRequest,
    ExtractResponse,
    JobListing,
    ParsedFieldModel,
    ParsedLineModel,
    PartSummaryModel,
    ReleaseRequest,
    ReleaseResponse,
    UploadResponse,
    ValidateRequest,
    ValidationResponse,
    ValidationSummary,
)
from .release import ReleaseManager
from .storage import StorageManager, extract_sheet_setup
from .parser import load_from_bytes

app = FastAPI(title="HK Parser Service", version="0.1.0")


def configure_logging(config: ServiceConfig) -> None:
    Path(config.app_log_name).parent.mkdir(parents=True, exist_ok=True)
    Path(config.audit_log_name).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(config.app_log_name),
        ],
    )


def get_config() -> ServiceConfig:
    return DEFAULT_CONFIG


def get_validation_service(config: Annotated[ServiceConfig, Depends(get_config)]) -> ValidationService:
    return ValidationService(config=config)


def get_release_manager() -> ReleaseManager:
    # Singleton-style default
    if not hasattr(get_release_manager, "_instance"):
        get_release_manager._instance = ReleaseManager()  # type: ignore[attr-defined]
    return get_release_manager._instance  # type: ignore[attr-defined]


def get_storage_manager(config: Annotated[ServiceConfig, Depends(get_config)]) -> StorageManager:
    return StorageManager(root=config.storage_root)


configure_logging(DEFAULT_CONFIG)
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    template_path = Path(__file__).parent / "templates" / "index.html"
    if not template_path.exists():
        raise HTTPException(status_code=500, detail="Template not found")
    return HTMLResponse(template_path.read_text(encoding="utf-8"))


@app.post("/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    description: Annotated[Optional[str], Form()] = None,
    validator: ValidationService = Depends(get_validation_service),
    release_manager: ReleaseManager = Depends(get_release_manager),
    storage_manager: StorageManager = Depends(get_storage_manager),
) -> UploadResponse:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    job_id = hash_payload(content)[:12]
    lines = load_from_bytes(content)
    result = validator.validate_lines(job_id=job_id, lines=lines)
    release_manager.record_validation(result)
    setup = extract_sheet_setup(lines)
    stored = storage_manager.save_upload(
        job_id=job_id,
        filename=file.filename or f"{job_id}.mpf",
        content=content,
        description=description or "",
        validation=result,
        setup=setup,
    )
    logger.info("Upload validated for job %s with %d diagnostics", job_id, len(result.diagnostics))
    payload = _build_validation_payload(result)
    return UploadResponse(
        **payload,
        stored_path=str(stored.stored_path),
        meta_path=str(stored.meta_path),
        description=description,
        uploaded_at=stored.uploaded_at,
    )


@app.post("/validate", response_model=ValidationResponse)
async def validate(
    request: ValidateRequest,
    validator: ValidationService = Depends(get_validation_service),
    release_manager: ReleaseManager = Depends(get_release_manager),
) -> ValidationResponse:
    content_bytes = request.gcode.encode("utf-8")
    job_id = request.job_id or hash_payload(content_bytes)[:12]
    result = validator.validate_bytes(job_id=job_id, content=content_bytes)
    release_manager.record_validation(result)
    logger.info("Manual validation for job %s completed", job_id)
    payload = _build_validation_payload(result)
    return ValidationResponse(**payload)


@app.get("/jobs", response_model=list[JobListing])
async def list_jobs(storage_manager: StorageManager = Depends(get_storage_manager)) -> list[JobListing]:
    jobs = storage_manager.list_jobs()
    return [JobListing(**job) for job in jobs]


@app.post("/extract", response_model=ExtractResponse)
async def extract_part(
    request: ExtractRequest,
    storage_manager: StorageManager = Depends(get_storage_manager),
) -> ExtractResponse:
    meta = storage_manager.load_job(request.job_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Job metadata not found.")
    stored_path = meta.get("storedPath")
    if not stored_path or not Path(stored_path).exists():
        raise HTTPException(status_code=404, detail="Stored program not found.")

    content = Path(stored_path).read_text(encoding="utf-8")
    try:
        extraction = extract_part_program(content, request.part_label, margin=request.margin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    description = request.description or f"Extracted part {request.part_label}"
    stored = storage_manager.save_part_extraction(
        source_job_id=request.job_id,
        part_label=request.part_label,
        lines=extraction.lines,
        width=extraction.width,
        height=extraction.height,
        description=description,
        base_filename=meta.get("originalFile", f"{request.job_id}.mpf"),
    )

    return ExtractResponse(
        job_id=stored.job_id,
        part_label=request.part_label,
        stored_path=str(stored.stored_path),
        meta_path=str(stored.meta_path),
        width=extraction.width,
        height=extraction.height,
        filename=stored.filename,
    )


@app.post("/release", response_model=ReleaseResponse)
async def release(
    request: ReleaseRequest,
    release_manager: ReleaseManager = Depends(get_release_manager),
) -> ReleaseResponse:
    if not release_manager.can_release(request.job_id):
        logger.warning("Release attempt rejected for job %s", request.job_id)
        raise HTTPException(status_code=409, detail="Job is not ready for production release")

    released_at = release_manager.record_release(job_id=request.job_id, approver=request.approver)
    _record_audit(
        {"event": "release", "job_id": request.job_id, "approved_by": request.approver, "released_at": released_at.isoformat()}
    )
    return ReleaseResponse(
        job_id=request.job_id,
        status="released",
        approved_by=request.approver,
        released_at=released_at,
        notes="Production release recorded.",
    )


def _build_validation_payload(result) -> dict:
    _record_audit(
        {
            "event": "validate",
            "job_id": result.job_id,
            "errors": result.summary["errors"],
            "warnings": result.summary["warnings"],
        }
    )
    return {
        "job_id": result.job_id,
        "diagnostics": [DiagnosticModel(**diag.__dict__) for diag in result.diagnostics],
        "summary": ValidationSummary(**result.summary),
        "parsed_lines": [
            ParsedLineModel(
                line_number=line.line_number,
                raw=line.raw,
                fields=[
                    ParsedFieldModel(name="command", value=line.command),
                    *[ParsedFieldModel(name=name, value=value) for name, value in line.params.items()],
                ],
            )
            for line in result.parsed
        ],
        "parts": [_to_part_model(part) for part in result.parts],
    }


def _record_audit(entry: dict) -> None:
    with open(DEFAULT_CONFIG.audit_log_name, "a", encoding="utf-8") as audit_log:
        audit_log.write(f"{datetime.now(timezone.utc).isoformat()} {entry}\n")


def _to_part_model(part) -> PartSummaryModel:
    return PartSummaryModel(
        hkost_line=part.hkost_line,
        profile_line=part.profile_line,
        contours=part.contours,
    )
