from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from .config import DEFAULT_CONFIG, ServiceConfig
from .diagnostics import ValidationService, hash_payload
from .models import (
    DiagnosticModel,
    ParsedFieldModel,
    ParsedLineModel,
    ReleaseRequest,
    ReleaseResponse,
    ValidateRequest,
    ValidationResponse,
    ValidationSummary,
)
from .release import ReleaseManager

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


@app.post("/upload", response_model=ValidationResponse)
async def upload_file(
    file: UploadFile = File(...),
    validator: ValidationService = Depends(get_validation_service),
    release_manager: ReleaseManager = Depends(get_release_manager),
) -> ValidationResponse:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    job_id = hash_payload(content)[:12]
    result = validator.validate_bytes(job_id=job_id, content=content)
    release_manager.record_validation(result)
    logger.info("Upload validated for job %s with %d diagnostics", job_id, len(result.diagnostics))
    return _build_validation_response(result)


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
    return _build_validation_response(result)


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


def _build_validation_response(result) -> ValidationResponse:
    _record_audit(
        {
            "event": "validate",
            "job_id": result.job_id,
            "errors": result.summary["errors"],
            "warnings": result.summary["warnings"],
        }
    )
    return ValidationResponse(
        job_id=result.job_id,
        diagnostics=[DiagnosticModel(**diag.__dict__) for diag in result.diagnostics],
        summary=ValidationSummary(**result.summary),
        parsed_lines=[
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
    )


def _record_audit(entry: dict) -> None:
    with open(DEFAULT_CONFIG.audit_log_name, "a", encoding="utf-8") as audit_log:
        audit_log.write(f"{datetime.now(timezone.utc).isoformat()} {entry}\n")
