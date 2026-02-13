from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from .config import DEFAULT_CONFIG, ServiceConfig
from .diagnostics import ValidationService, hash_payload
from .extract import build_reordered_program, extract_part_profile_program, extract_part_program
from .models import (
    ContourPlotModel,
    CutOrderRequest,
    DiagnosticModel,
    ExtractRequest,
    ExtractResponse,
    JobListing,
    ParsedFieldModel,
    ParsedLineModel,
    PartDetailModel,
    PartSummaryModel,
    ReleaseRequest,
    ReleaseResponse,
    UploadResponse,
    ValidateRequest,
    ValidationResponse,
    ValidationSummary,
)
from .release import ReleaseManager
from .parser import build_part_plot_points, extract_part_block, extract_part_contour_block, load_from_bytes
from .samples import load_sample_index, save_match_override
from .storage import StorageManager, extract_sheet_setup

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

CONTOUR_CLOSE_EPSILON = 0.001
COMMON_NEIGHBOR_DISTANCE_TOLERANCE = 0.1
MAX_EXTRA_CONTOURS = 5

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


def _render_template(name: str) -> HTMLResponse:
    template_path = Path(__file__).parent / "templates" / name
    if not template_path.exists():
        raise HTTPException(status_code=500, detail="Template not found")
    return HTMLResponse(template_path.read_text(encoding="utf-8"))


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return _render_template("index.html")


@app.get("/match", response_class=HTMLResponse)
async def match_page() -> HTMLResponse:
    return _render_template("match.html")


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_page(job_id: str) -> HTMLResponse:
    return _render_template("job.html")


@app.get("/data-files")
async def list_data_files(config: Annotated[ServiceConfig, Depends(get_config)]) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    root = Path(config.storage_root)
    if not root.exists():
        return results
    for file_path in sorted(root.glob("**/*")):
        if not file_path.is_file() or file_path.suffix.lower() != ".mpf":
            continue
        job_id = file_path.parent.name
        results.append({"jobId": job_id, "filename": file_path.name})
    return results


@app.post("/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    description: Annotated[Optional[str], Form()] = None,
    attachment: Annotated[Optional[UploadFile], File()] = None,
    validator: ValidationService = Depends(get_validation_service),
    release_manager: ReleaseManager = Depends(get_release_manager),
    storage_manager: StorageManager = Depends(get_storage_manager),
) -> UploadResponse:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    linked_files = []
    if attachment is not None:
        attachment_content = await attachment.read()
        if attachment_content:
            linked_files.append(
                {
                    "filename": attachment.filename,
                    "media_type": attachment.content_type,
                    "content": attachment_content,
                }
            )

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
        linked_files=linked_files,
    )
    logger.info("Upload validated for job %s with %d diagnostics", job_id, len(result.diagnostics))
    payload = _build_validation_payload(result, setup=setup)
    return UploadResponse(
        **payload,
        stored_path=str(stored.stored_path),
        meta_path=str(stored.meta_path),
        link_meta_path=str(stored.link_meta_path) if stored.link_meta_path else None,
        linked_files=stored.linked_files,
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


@app.get("/samples")
async def list_samples(
    config: Annotated[ServiceConfig, Depends(get_config)],
    validator: ValidationService = Depends(get_validation_service),
) -> dict:
    return load_sample_index(config=config, validator=validator)


@app.post("/samples/matches")
async def store_sample_match(
    payload: dict,
    config: Annotated[ServiceConfig, Depends(get_config)],
) -> dict:
    mpf_filename = payload.get("mpf_filename")
    if not mpf_filename:
        raise HTTPException(status_code=400, detail="mpf_filename is required")
    pdf_filename = payload.get("pdf_filename")
    updated = save_match_override(
        config=config,
        mpf_filename=str(mpf_filename),
        pdf_filename=str(pdf_filename) if pdf_filename else None,
    )
    return {"status": "ok", "matches": updated.get("matches", {})}


@app.get("/samples/files/{kind}/{filename}")
async def sample_file(kind: str, filename: str) -> Response:
    if kind not in {"mpf", "pdfs"}:
        raise HTTPException(status_code=404, detail="Unknown sample category")
    sample_root = Path(__file__).resolve().parents[2] / "samples"
    safe_name = Path(filename).name
    sample_path = sample_root / kind / safe_name
    if not sample_path.exists():
        raise HTTPException(status_code=404, detail="Sample file not found")
    return FileResponse(sample_path)


@app.get("/jobs/{job_id}/analysis", response_model=ValidationResponse)
async def job_analysis(
    job_id: str,
    release_manager: ReleaseManager = Depends(get_release_manager),
) -> ValidationResponse:
    validation = release_manager.get_validation(job_id)
    if validation is None:
        raise HTTPException(status_code=404, detail="Job not found")
    setup = extract_sheet_setup(validation.raw_lines)
    payload = _build_validation_payload(validation, setup=setup)
    return ValidationResponse(**payload)


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


def _build_validation_payload(result, setup: Optional[dict] = None) -> dict:
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
        "parsed_lines": _build_display_lines(result),
        "parts": [_to_part_model(part, result.raw_lines) for part in result.parts],
        "setup": setup,
        "raw_program": list(result.raw_lines),
    }


def _record_audit(entry: dict) -> None:
    with open(DEFAULT_CONFIG.audit_log_name, "a", encoding="utf-8") as audit_log:
        audit_log.write(f"{datetime.now(timezone.utc).isoformat()} {entry}\n")


def _to_part_model(part, raw_lines: list[str]) -> PartSummaryModel:
    contour_block = extract_part_block(raw_lines, part.part_line)
    plot_points = build_part_plot_points(contour_block)
    return PartSummaryModel(
        part_number=part.part_number,
        part_line=part.part_line,
        hkost_line=part.hkost_line,
        profile_line=part.profile_line,
        start_line=part.start_line,
        end_line=part.end_line,
        contours=part.contours,
        anchor_x=part.anchor_x,
        anchor_y=part.anchor_y,
        plot_points=[[list(point) for point in contour] for contour in plot_points],
    )


def _translate_contour_points(
    points: List[tuple[float, float]],
    offset_x: float,
    offset_y: float,
) -> List[List[float]]:
    return [[point[0] + offset_x, point[1] + offset_y] for point in points]


@app.get("/jobs/{job_id}/parts/{part_number}", response_model=PartDetailModel)
async def part_detail(
    job_id: str,
    part_number: int,
    extra_contours: Optional[str] = None,
    contour_order: Optional[str] = None,
    release_manager: ReleaseManager = Depends(get_release_manager),
) -> PartDetailModel:
    validation = release_manager.get_validation(job_id)
    if validation is None:
        raise HTTPException(status_code=404, detail="Job not found")

    part = next((p for p in validation.parts if p.part_number == part_number), None)
    if part is None:
        raise HTTPException(status_code=404, detail="Part not found")

    contour_block = extract_part_block(validation.raw_lines, part.part_line)
    plot_points = build_part_plot_points(contour_block)
    extra_contour_refs = _resolve_extra_contours(
        raw=extra_contours,
        parts=validation.parts,
        raw_lines=validation.raw_lines,
        target_part=part,
    )
    extra_contour_blocks = []
    for ref in extra_contour_refs:
        block = extract_part_contour_block(validation.raw_lines, ref.part_line, ref.contour_index)
        extra_contour_blocks.append(
            (
                ref.part_number,
                ref.contour_index,
                block,
                next((p for p in validation.parts if p.part_number == ref.part_number), None),
            )
        )
    plot_contours = [
        ContourPlotModel(label=str(idx + 1), points=[[float(point[0]), float(point[1])] for point in contour])
        for idx, contour in enumerate(plot_points)
    ]
    for part_number_ref, contour_index, block, source_part in extra_contour_blocks:
        if not block:
            continue
        extra_points = build_part_plot_points(block)
        points_for_preview: list[tuple[float, float]]
        if extra_points and extra_points[0]:
            points_for_preview = extra_points[0]
        else:
            points_for_preview = _build_contour_points_from_block(block)
        if not points_for_preview:
            continue
        target_anchor_x = part.anchor_x or 0.0
        target_anchor_y = part.anchor_y or 0.0
        source_anchor_x = source_part.anchor_x if source_part and source_part.anchor_x is not None else 0.0
        source_anchor_y = source_part.anchor_y if source_part and source_part.anchor_y is not None else 0.0
        offset_x = source_anchor_x - target_anchor_x
        offset_y = source_anchor_y - target_anchor_y
        plot_contours.append(
            ContourPlotModel(
                label=f"{part_number_ref}.{contour_index}",
                points=_translate_contour_points(points_for_preview, offset_x, offset_y),
            )
        )
    contour_labels = [str(idx + 1) for idx in range(part.contours)] + [f"{ref.part_number}.{ref.contour_index}" for ref in extra_contour_refs]
    contour_order_values = _parse_contour_order(contour_order, contour_labels)
    content = "\n".join(validation.raw_lines)
    profile_block = extract_part_profile_program(content, part.part_line).lines
    part_program = extract_part_program(
        content,
        part.part_line,
        extra_contours=[(ref.part_line, ref.contour_index) for ref in extra_contour_refs],
        contour_order=contour_order_values,
        contour_labels=contour_labels,
    ).lines
    return PartDetailModel(
        part_number=part.part_number,
        part_line=part.part_line,
        hkost_line=part.hkost_line,
        profile_line=part.profile_line,
        start_line=part.start_line,
        end_line=part.end_line,
        contours=part.contours,
        anchor_x=part.anchor_x,
        anchor_y=part.anchor_y,
        profile_block=profile_block,
        plot_points=[[list(point) for point in contour] for contour in plot_points],
        plot_contours=plot_contours,
        part_program=part_program,
        auto_extra_contours=_detect_common_neighbor_contour_labels(
            parts=validation.parts,
            raw_lines=validation.raw_lines,
            target_part=part,
        ),
    )


@app.get("/jobs/{job_id}/parts/{part_number}/program")
async def part_program_download(
    job_id: str,
    part_number: int,
    extra_contours: Optional[str] = None,
    contour_order: Optional[str] = None,
    release_manager: ReleaseManager = Depends(get_release_manager),
    storage_manager: StorageManager = Depends(get_storage_manager),
) -> Response:
    validation = release_manager.get_validation(job_id)
    if validation is None:
        raise HTTPException(status_code=404, detail="Job not found")

    part = next((p for p in validation.parts if p.part_number == part_number), None)
    if part is None:
        raise HTTPException(status_code=404, detail="Part not found")

    extra_contour_refs = _resolve_extra_contours(
        raw=extra_contours,
        parts=validation.parts,
        raw_lines=validation.raw_lines,
        target_part=part,
    )
    contour_labels = [str(idx + 1) for idx in range(part.contours)] + [f"{ref.part_number}.{ref.contour_index}" for ref in extra_contour_refs]
    contour_order_values = _parse_contour_order(contour_order, contour_labels)
    content = "\n".join(validation.raw_lines)
    part_program = extract_part_program(
        content,
        part.part_line,
        extra_contours=[(ref.part_line, ref.contour_index) for ref in extra_contour_refs],
        contour_order=contour_order_values,
        contour_labels=contour_labels,
    ).lines
    meta = storage_manager.load_job(job_id) or {}
    original_name = meta.get("originalFile", f"{job_id}.mpf")
    filename = _build_part_filename(original_name, part_number)
    payload = "\n".join(part_program) + "\n"
    return Response(
        content=payload,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/jobs/{job_id}/cut-order/program")
async def cut_order_program(
    job_id: str,
    request: CutOrderRequest,
    release_manager: ReleaseManager = Depends(get_release_manager),
    storage_manager: StorageManager = Depends(get_storage_manager),
) -> Response:
    validation = release_manager.get_validation(job_id)
    if validation is None:
        raise HTTPException(status_code=404, detail="Job not found")

    reordered_lines = build_reordered_program(
        validation.raw_lines,
        validation.parts,
        request.order,
        request.contour_orders,
    )
    meta = storage_manager.load_job(job_id) or {}
    original_name = meta.get("originalFile", f"{job_id}.mpf")
    filename = _build_cut_order_filename(original_name)
    payload = "\n".join(reordered_lines) + "\n"
    return Response(
        content=payload,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/jobs/{job_id}/parts/{part_number}/view", response_class=HTMLResponse)
async def part_view(job_id: str, part_number: int) -> HTMLResponse:
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
      <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>Part {part_number}</title>
        <style>
          body {{
            font-family: Arial, sans-serif;
            margin: 2rem;
            background: #f8fafc;
            color: #0f172a;
          }}
          .row {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem;
            align-items: center;
          }}
          .row label {{
            font-weight: 600;
          }}
          .row input {{
            padding: 0.35rem 0.5rem;
            border: 1px solid #cbd5e1;
            border-radius: 4px;
            min-width: 120px;
          }}
          .row button {{
            padding: 0.4rem 0.75rem;
            border-radius: 4px;
            border: 1px solid #1e293b;
            background: #1e293b;
            color: #ffffff;
            cursor: pointer;
          }}
          .card {{
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 1.5rem;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
          }}
          .order-list {{
            list-style: decimal;
            padding-left: 1.5rem;
            margin: 0.75rem 0 0;
          }}
          .order-item {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0.5rem 0.75rem;
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            background: #ffffff;
            margin-bottom: 0.5rem;
            cursor: grab;
          }}
          .order-item.dragging {{
            opacity: 0.6;
            background: #f1f5f9;
          }}
          .order-hint {{
            font-size: 0.9rem;
            color: #475569;
            margin-top: 0.5rem;
          }}
          .order-actions {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
            margin-top: 0.75rem;
          }}
          .action-button {{
            padding: 0.4rem 0.85rem;
            border-radius: 6px;
            border: 1px solid #1e293b;
            background: #1e293b;
            color: #ffffff;
            cursor: pointer;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            justify-content: center;
          }}
          .action-button:disabled {{
            cursor: not-allowed;
            opacity: 0.6;
          }}
          .order-status {{
            font-size: 0.9rem;
            color: #475569;
          }}
          canvas {{
            border: 1px solid #cbd5e1;
            border-radius: 6px;
            background: #ffffff;
          }}
          pre {{
            background: #0f172a;
            color: #e2e8f0;
            padding: 0.75rem;
            border-radius: 6px;
            overflow-x: auto;
          }}
        </style>
      </head>
      <body>
        <nav class="row">
          <button id="reset-contour-order" class="action-button" type="button">Reset</button>
          <button id="save-contour-order" class="action-button" type="button" disabled>Save Cut Order</button>
          <a href="/jobs/{job_id}" class="action-button">Back to Sheet View</a>
        </nav>
        <p id="contour-order-status" class="order-status"></p>
        <h1>Part {part_number}</h1>
        <div class="card">
          <h2>Geometry</h2>
          <canvas id="plot" width="1440" height="840"></canvas>
          <p id="plot-info"></p>
        </div>
        <div class="card">
          <h2>Additional Contours</h2>
          <p>Enter up to 5 extra contours as <strong>part.contour</strong> (example: <code>2.4</code>).</p>
          <div class="row">
            <label for="extra-contour-1">Extra contour</label>
            <input id="extra-contour-1" class="extra-contour" placeholder="2.4" />
            <input id="extra-contour-2" class="extra-contour" placeholder="3.1" />
            <input id="extra-contour-3" class="extra-contour" placeholder="5.2" />
            <input id="extra-contour-4" class="extra-contour" placeholder="6.1" />
            <input id="extra-contour-5" class="extra-contour" placeholder="7.3" />
            <button id="apply-contours" type="button">Apply</button>
          </div>
          <p id="contour-status"></p>
        </div>
        <div class="card">
          <h2>Contour Cut Order</h2>
          <p class="order-hint">
            Drag contours to reorder the cut sequence, then click save. The order is stored locally in your browser
            for this part. Saved order updates the preview and the generated standalone part program.
          </p>
          <ol id="contour-order" class="order-list"></ol>
        </div>
        <div class="card">
          <h2>Part Profile Code</h2>
          <pre id="profile-code"></pre>
        </div>
        <div class="card">
          <h2>Standalone Part Program</h2>
          <div class="row">
            <a id="download-program" href="#" download>Download Standalone Part Program</a>
          </div>
          <pre id="part-program"></pre>
        </div>
        <script>
          const plotCanvas = document.getElementById("plot");
          const plotInfo = document.getElementById("plot-info");
          const profileCode = document.getElementById("profile-code");
          const partProgram = document.getElementById("part-program");
          const contourStatus = document.getElementById("contour-status");
          const contourInputs = Array.from(document.querySelectorAll(".extra-contour"));
          const applyContours = document.getElementById("apply-contours");
          const downloadLink = document.getElementById("download-program");
          const contourOrderList = document.getElementById("contour-order");
          const saveContourOrderButton = document.getElementById("save-contour-order");
          const contourOrderStatus = document.getElementById("contour-order-status");
          const resetContourOrderButton = document.getElementById("reset-contour-order");
          const contourOrderKey = "contourOrder:{job_id}:{part_number}";
          let contourState = {{
            normalizedContours: [],
            savedOrder: [],
            pendingOrder: [],
          }};

          function getExtraContours() {{
            return contourInputs.map((input) => input.value.trim()).filter(Boolean);
          }}

          function buildQueryString(entries, contourOrder = contourState.pendingOrder) {{
            const query = new URLSearchParams();
            if (entries.length) {{
              query.set("extra_contours", entries.join(","));
            }}
            if (Array.isArray(contourOrder) && contourOrder.length) {{
              query.set("contour_order", contourOrder.join(","));
            }}
            const queryString = query.toString();
            return queryString ? `?${{queryString}}` : "";
          }}

          function updateDownloadLink(entries, contourOrder = contourState.pendingOrder) {{
            const queryString = buildQueryString(entries, contourOrder);
            downloadLink.href = `/jobs/{job_id}/parts/{part_number}/program${{queryString}}`;
          }}

          function normalizeContours(rawContours) {{
            return Array.isArray(rawContours[0]?.points)
              ? rawContours
              : rawContours.map((points, index) => ({{ label: String(index + 1), points }}));
          }}

          function readContourOrder(contours) {{
            const raw = localStorage.getItem(contourOrderKey);
            if (!raw) return contours.map((contour) => contour.label);
            try {{
              const parsed = JSON.parse(raw);
              if (!Array.isArray(parsed)) return contours.map((contour) => contour.label);
              const labels = new Set(contours.map((contour) => contour.label));
              const ordered = parsed.filter((label) => labels.has(label));
              const missing = contours
                .map((contour) => contour.label)
                .filter((label) => !ordered.includes(label));
              return [...ordered, ...missing];
            }} catch (err) {{
              return contours.map((contour) => contour.label);
            }}
          }}

          function saveContourOrder(order) {{
            localStorage.setItem(contourOrderKey, JSON.stringify(order));
          }}

          function applyContourOrder(contours, order = readContourOrder(contours)) {{
            const contourMap = new Map(contours.map((contour) => [contour.label, contour]));
            return order
              .map((label) => contourMap.get(label))
              .filter(Boolean)
              .map((contour, index) => ({{ ...contour, displayLabel: String(index + 1) }}));
          }}

          function ordersMatch(left, right) {{
            if (left.length !== right.length) return false;
            return left.every((value, index) => value === right[index]);
          }}

          function updateSaveState() {{
            const hasChanges = !ordersMatch(contourState.pendingOrder, contourState.savedOrder);
            saveContourOrderButton.disabled = !contourState.pendingOrder.length || !hasChanges;
            if (!contourState.pendingOrder.length) {{
              contourOrderStatus.textContent = "";
              return;
            }}
            contourOrderStatus.textContent = hasChanges ? "Unsaved contour order changes." : "Contour order saved.";
          }}

          function updateContourListLabels(order) {{
            const orderedContours = applyContourOrder(contourState.normalizedContours, order);
            const items = Array.from(contourOrderList.querySelectorAll(".order-item"));
            items.forEach((item, index) => {{
              const contour = orderedContours[index];
              if (!contour) return;
              const displayLabel = contour.displayLabel ?? contour.label;
              const suffix =
                contour.label && displayLabel && contour.label !== displayLabel
                  ? ` (was ${{contour.label}})`
                  : "";
              item.textContent = `Contour ${{displayLabel}}${{suffix}}`;
            }});
          }}

          function renderContourOrder(contours, onUpdate) {{
            contourOrderList.innerHTML = "";
            if (!contours.length) {{
              contourOrderList.innerHTML = "<li>No contours available.</li>";
              return;
            }}
            contours.forEach((contour) => {{
              const item = document.createElement("li");
              item.className = "order-item";
              item.setAttribute("draggable", "true");
              item.dataset.label = contour.label;
              const displayLabel = contour.displayLabel ?? contour.label;
              const isBorrowedContour = typeof contour.label === "string" && contour.label.includes(".");
              const suffix =
                contour.label && displayLabel && contour.label !== displayLabel
                  ? ` (was ${{contour.label}})`
                  : "";
              const borrowedSuffix = isBorrowedContour ? " • borrowed common cut" : "";
              item.textContent = `Contour ${{displayLabel}}${{suffix}}${{borrowedSuffix}}`;
              contourOrderList.appendChild(item);
            }});
            enableDragSorting(contourOrderList, () => {{
              const newOrder = Array.from(contourOrderList.querySelectorAll(".order-item")).map(
                (item) => item.dataset.label
              );
              contourState.pendingOrder = newOrder;
              updateContourListLabels(newOrder);
              updateSaveState();
              if (typeof onUpdate === "function") {{
                onUpdate(newOrder);
              }}
            }});
          }}

          function enableDragSorting(listEl, onUpdate) {{
            let dragging = null;
            listEl.addEventListener("dragstart", (event) => {{
              const item = event.target.closest(".order-item");
              if (!item) return;
              dragging = item;
              item.classList.add("dragging");
              event.dataTransfer.effectAllowed = "move";
            }});
            listEl.addEventListener("dragend", () => {{
              if (dragging) {{
                dragging.classList.remove("dragging");
                dragging = null;
              }}
            }});
            listEl.addEventListener("dragover", (event) => {{
              event.preventDefault();
              if (!dragging) return;
              const target = event.target.closest(".order-item");
              if (!target || target === dragging) return;
              const rect = target.getBoundingClientRect();
              const shouldInsertAfter = event.clientY - rect.top > rect.height / 2;
              if (shouldInsertAfter) {{
                target.after(dragging);
              }} else {{
                target.before(dragging);
              }}
            }});
            listEl.addEventListener("drop", (event) => {{
              event.preventDefault();
              if (typeof onUpdate === "function") {{
                onUpdate();
              }}
            }});
          }}

          async function loadPart(entries = getExtraContours()) {{
            const queryString = buildQueryString(entries, contourState.pendingOrder);
            const resp = await fetch(`/jobs/{job_id}/parts/{part_number}${{queryString}}`);
            if (!resp.ok) {{
              plotInfo.textContent = "Unable to load part details.";
              return;
            }}
            const data = await resp.json();
            profileCode.textContent = (data.profile_block || []).join("\\n");
            partProgram.textContent = (data.part_program || []).join("\\n");
            const normalizedContours = normalizeContours(data.plot_contours || data.plot_points || []);
            const savedOrder = readContourOrder(normalizedContours);
            contourState = {{
              normalizedContours,
              savedOrder,
              pendingOrder: [...savedOrder],
            }};
            const orderedContours = applyContourOrder(normalizedContours, savedOrder);
            renderPlot(orderedContours);
            updateDownloadLink(entries, savedOrder);
            updateDownloadLink(entries, savedOrder);
            renderContourOrder(orderedContours, (order) => {{
              renderPlot(applyContourOrder(normalizedContours, order));
              updateDownloadLink(entries, order);
            }});
            updateSaveState();
            const autoEntries = Array.isArray(data.auto_extra_contours) ? data.auto_extra_contours : [];
            if (entries.length && autoEntries.length) {{
              contourStatus.textContent = `Including manual contours: ${{entries.join(", ")}} • Auto detected: ${{autoEntries.join(", ")}}`;
            }} else if (entries.length) {{
              contourStatus.textContent = `Including manual contours: ${{entries.join(", ")}}`;
            }} else if (autoEntries.length) {{
              contourStatus.textContent = `Auto-detected neighboring contours: ${{autoEntries.join(", ")}}`;
            }} else {{
              contourStatus.textContent = "No extra contours selected.";
            }}
          }}

          function renderPlot(contours) {{
            const ctx = plotCanvas.getContext("2d");
            ctx.clearRect(0, 0, plotCanvas.width, plotCanvas.height);
            const normalizedContours = normalizeContours(contours);
            if (!normalizedContours.length) {{
              plotInfo.textContent = "No plot data found for this part.";
              return;
            }}
            const flatPoints = normalizedContours.flatMap((contour) => contour.points);
            if (!flatPoints.length) {{
              plotInfo.textContent = "No plot data found for this part.";
              return;
            }}
            const xs = flatPoints.map((p) => p[0]);
            const ys = flatPoints.map((p) => p[1]);
            const minX = Math.min(...xs);
            const maxX = Math.max(...xs);
            const minY = Math.min(...ys);
            const maxY = Math.max(...ys);
            const flipPlot180 = true;
            const transformPoint = (point) => {{
              if (!flipPlot180) {{
                return point;
              }}
              return [minX + maxX - point[0], minY + maxY - point[1]];
            }};
            const padding = 24;
            const rangeX = maxX - minX || 1;
            const rangeY = maxY - minY || 1;
            const scale = Math.min(
              (plotCanvas.width - padding * 2) / rangeX,
              (plotCanvas.height - padding * 2) / rangeY
            );
            ctx.strokeStyle = "#2563eb";
            ctx.lineWidth = 2;
            ctx.font = "13px Arial";
            ctx.fillStyle = "#0f172a";
            let borrowedContourCount = 0;
            normalizedContours.forEach((contour, contourIndex) => {{
              if (!contour.points.length) return;
              const isBorrowedContour = typeof contour.label === "string" && contour.label.includes(".");
              if (isBorrowedContour) {{
                borrowedContourCount += 1;
                ctx.strokeStyle = "#ea580c";
                ctx.setLineDash([8, 4]);
              }} else {{
                ctx.strokeStyle = "#2563eb";
                ctx.setLineDash([]);
              }}
              ctx.beginPath();
              contour.points.forEach((point, index) => {{
                const transformed = transformPoint(point);
                const x = (transformed[0] - minX) * scale + padding;
                const y = (maxY - transformed[1]) * scale + padding;
                if (index === 0) {{
                  ctx.moveTo(x, y);
                }} else {{
                  ctx.lineTo(x, y);
                }}
              }});
              ctx.stroke();
              const centroid = contour.points.reduce(
                (acc, point) => {{
                  acc.x += point[0];
                  acc.y += point[1];
                  return acc;
                }},
                {{ x: 0, y: 0 }}
              );
              const count = contour.points.length || 1;
              const transformedLabel = transformPoint([centroid.x / count, centroid.y / count]);
              const labelX = (transformedLabel[0] - minX) * scale + padding;
              const labelY = (maxY - transformedLabel[1]) * scale + padding;
              const label = contour.displayLabel || contour.label || String(contourIndex + 1);
              ctx.fillText(label, labelX + 4, labelY - 4);
            }});
            ctx.setLineDash([]);
            plotInfo.textContent =
              "Distance: X " +
              rangeX +
              ", Y " +
              rangeY +
              ` • Borrowed common-cut contours: ${{borrowedContourCount}}`;
          }}

          function syncInputsFromUrl() {{
            const params = new URLSearchParams(window.location.search);
            const raw = params.get("extra_contours");
            if (raw) {{
              raw.split(",").slice(0, contourInputs.length).forEach((value, index) => {{
                contourInputs[index].value = value.trim();
              }});
            }}
            const contourOrderRaw = params.get("contour_order");
            if (contourOrderRaw) {{
              contourState.pendingOrder = contourOrderRaw
                .split(",")
                .map((entry) => entry.trim())
                .filter(Boolean);
            }}
          }}

          applyContours.addEventListener("click", () => {{
            const entries = getExtraContours();
            const queryString = buildQueryString(entries, contourState.pendingOrder);
            const url = new URL(window.location.href);
            url.search = queryString ? queryString.slice(1) : "";
            window.history.replaceState({{}}, "", url);
            loadPart(entries);
          }});

          saveContourOrderButton.addEventListener("click", () => {{
            if (!contourState.pendingOrder.length) return;
            saveContourOrder(contourState.pendingOrder);
            contourState.savedOrder = [...contourState.pendingOrder];
            const entries = getExtraContours();
            const queryString = buildQueryString(entries, contourState.pendingOrder);
            const url = new URL(window.location.href);
            url.search = queryString ? queryString.slice(1) : "";
            window.history.replaceState({{}}, "", url);
            updateDownloadLink(entries, contourState.pendingOrder);
            loadPart(entries);
          }});

          resetContourOrderButton.addEventListener("click", () => {{
            localStorage.removeItem(contourOrderKey);
            loadPart(getExtraContours());
          }});

          syncInputsFromUrl();
          loadPart();
        </script>
      </body>
    </html>
    """
    return HTMLResponse(html)


@dataclass
class ExtraContourRef:
    part_number: int
    part_line: int
    contour_index: int


def _build_part_filename(original_name: str, part_number: int) -> str:
    base = Path(original_name).stem or "part"
    suffix = Path(original_name).suffix
    candidate = f"{base}_p{part_number}{suffix}"
    return re.sub(r"[^A-Za-z0-9._-]", "_", candidate)


def _build_cut_order_filename(original_name: str) -> str:
    base = Path(original_name).stem or "cut-order"
    suffix = Path(original_name).suffix or ".mpf"
    candidate = f"{base}_cut_order{suffix}"
    return re.sub(r"[^A-Za-z0-9._-]", "_", candidate)


def _parse_extra_contours(raw: Optional[str], parts: list[PartSummaryModel]) -> list[ExtraContourRef]:
    if not raw:
        return []
    tokens = [token.strip() for token in raw.split(",") if token.strip()]
    if not tokens:
        return []
    refs: list[ExtraContourRef] = []
    pattern = re.compile(r"^(?P<part>\d+)\s*\.\s*(?P<contour>\d+)$")
    for token in tokens:
        match = pattern.match(token)
        if not match:
            continue
        part_number = int(match.group("part"))
        contour_index = int(match.group("contour"))
        part = next((p for p in parts if p.part_number == part_number), None)
        if part is None:
            continue
        if contour_index < 1 or contour_index > part.contours:
            continue
        refs.append(
            ExtraContourRef(
                part_number=part_number,
                part_line=part.part_line,
                contour_index=contour_index,
            )
        )
        if len(refs) >= MAX_EXTRA_CONTOURS:
            break
    return refs


def _resolve_extra_contours(
    raw: Optional[str],
    parts: list[PartSummaryModel],
    raw_lines: list[str],
    target_part: PartSummaryModel,
) -> list[ExtraContourRef]:
    explicit_refs = _parse_extra_contours(raw, parts)
    auto_refs = _detect_common_neighbor_contours(parts=parts, raw_lines=raw_lines, target_part=target_part)
    merged: list[ExtraContourRef] = []
    seen: set[tuple[int, int]] = set()
    for ref in [*explicit_refs, *auto_refs]:
        key = (ref.part_number, ref.contour_index)
        if key in seen:
            continue
        merged.append(ref)
        seen.add(key)
        if len(merged) >= MAX_EXTRA_CONTOURS:
            break
    return merged


def _detect_common_neighbor_contour_labels(
    parts: list[PartSummaryModel],
    raw_lines: list[str],
    target_part: PartSummaryModel,
) -> list[str]:
    refs = _detect_common_neighbor_contours(parts=parts, raw_lines=raw_lines, target_part=target_part)
    return [f"{ref.part_number}.{ref.contour_index}" for ref in refs]


def _detect_common_neighbor_contours(
    parts: list[PartSummaryModel],
    raw_lines: list[str],
    target_part: PartSummaryModel,
) -> list[ExtraContourRef]:
    absolute_contours = _build_absolute_part_contours(parts, raw_lines)
    target_contours = absolute_contours.get(target_part.part_number, [])
    target_boundary_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for contour in target_contours:
        if _contour_is_closed(contour):
            target_boundary_segments.extend(_contour_segments(contour))

    if not target_boundary_segments:
        return []

    refs: list[ExtraContourRef] = []
    for candidate in sorted(parts, key=lambda part: part.part_number):
        if candidate.part_number == target_part.part_number:
            continue
        candidate_contours = absolute_contours.get(candidate.part_number, [])
        for contour_index, contour in enumerate(candidate_contours, start=1):
            if _contour_is_closed(contour):
                continue
            if not _contour_is_common_neighbor(contour, target_boundary_segments):
                continue
            refs.append(
                ExtraContourRef(
                    part_number=candidate.part_number,
                    part_line=candidate.part_line,
                    contour_index=contour_index,
                )
            )
            if len(refs) >= MAX_EXTRA_CONTOURS:
                return refs
    return refs


def _build_absolute_part_contours(
    parts: list[PartSummaryModel],
    raw_lines: list[str],
) -> dict[int, list[list[tuple[float, float]]]]:
    contours_by_part: dict[int, list[list[tuple[float, float]]]] = {}
    for part in parts:
        anchor_x = float(part.anchor_x or 0.0)
        anchor_y = float(part.anchor_y or 0.0)
        absolute_contours: list[list[tuple[float, float]]] = []
        for contour_index in range(1, int(part.contours) + 1):
            contour_block = extract_part_contour_block(raw_lines, part.part_line, contour_index)
            local_points = _build_contour_points_from_block(contour_block)
            if not local_points:
                continue
            absolute_contours.append([(point[0] + anchor_x, point[1] + anchor_y) for point in local_points])
        contours_by_part[part.part_number] = absolute_contours
    return contours_by_part




def _build_contour_points_from_block(contour_block: list[str]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    current_x: float | None = None
    current_y: float | None = None

    for raw_line in contour_block:
        line = raw_line.strip()
        if not line:
            continue
        if "HKSTR(" in line.upper():
            parsed = _parse_hkstr_start_xy(line)
            if parsed is not None:
                current_x, current_y = parsed
                points.append((current_x, current_y))
            continue

        if not line.upper().startswith("G"):
            continue

        maybe_x = _extract_axis_value(line, "X")
        maybe_y = _extract_axis_value(line, "Y")
        if maybe_x is None and maybe_y is None:
            continue
        if maybe_x is not None:
            current_x = maybe_x
        if maybe_y is not None:
            current_y = maybe_y
        if current_x is None or current_y is None:
            continue
        points.append((current_x, current_y))

    return points


def _parse_hkstr_start_xy(line: str) -> tuple[float, float] | None:
    match = re.search(r"HKSTR\(([^)]*)\)", line, flags=re.IGNORECASE)
    if not match:
        return None
    args = [item.strip() for item in match.group(1).split(",")]
    if len(args) < 4:
        return None
    try:
        return float(args[2]), float(args[3])
    except ValueError:
        return None


def _extract_axis_value(line: str, axis: str) -> float | None:
    match = re.search(rf"{axis}\s*([-+]?\d*\.?\d+)", line, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None
def _contour_is_common_neighbor(
    contour: list[tuple[float, float]],
    target_boundary_segments: list[tuple[tuple[float, float], tuple[float, float]]],
) -> bool:
    if len(contour) < 2:
        return False
    contour_segments = _contour_segments(contour)
    if not contour_segments:
        return False
    for segment in contour_segments:
        for boundary in target_boundary_segments:
            if _segment_to_segment_distance(segment[0], segment[1], boundary[0], boundary[1]) <= COMMON_NEIGHBOR_DISTANCE_TOLERANCE:
                return True
    return False


def _contour_is_closed(contour: list[tuple[float, float]]) -> bool:
    if len(contour) < 2:
        return False
    first = contour[0]
    last = contour[-1]
    return ((first[0] - last[0]) ** 2 + (first[1] - last[1]) ** 2) ** 0.5 <= CONTOUR_CLOSE_EPSILON


def _contour_segments(contour: list[tuple[float, float]]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    return [(contour[idx], contour[idx + 1]) for idx in range(len(contour) - 1)]


def _segment_to_segment_distance(
    p1: tuple[float, float],
    p2: tuple[float, float],
    q1: tuple[float, float],
    q2: tuple[float, float],
) -> float:
    return min(
        _point_to_segment_distance(p1, q1, q2),
        _point_to_segment_distance(p2, q1, q2),
        _point_to_segment_distance(q1, p1, p2),
        _point_to_segment_distance(q2, p1, p2),
    )


def _point_to_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if dx == 0.0 and dy == 0.0:
        return ((point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2) ** 0.5
    t = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    proj_x = start[0] + t * dx
    proj_y = start[1] + t * dy
    return ((point[0] - proj_x) ** 2 + (point[1] - proj_y) ** 2) ** 0.5




def _parse_contour_order(raw: Optional[str], allowed_labels: list[str]) -> list[str]:
    if not raw:
        return []
    allowed = {label.strip() for label in allowed_labels if str(label).strip()}
    ordered: list[str] = []
    seen: set[str] = set()
    for token in raw.split(','):
        label = token.strip()
        if not label or label not in allowed or label in seen:
            continue
        ordered.append(label)
        seen.add(label)
    return ordered


def _build_display_lines(result) -> list[ParsedLineModel]:
    if not result.parts:
        return [
            ParsedLineModel(
                line_number=line.line_number,
                raw=line.raw,
                command=line.command,
                description=line.description,
                arguments=line.arguments,
                fields=[
                    ParsedFieldModel(name="command", value=line.command),
                    *[ParsedFieldModel(name=name, value=value) for name, value in line.params.items()],
                ],
            )
            for line in result.parsed
        ]

    parts_by_hkost = {part.hkost_line: part for part in result.parts}
    first_hkost = min(parts_by_hkost.keys())
    last_hkppp = _find_last_hkppp_line(result.raw_lines, first_hkost)
    cutoff_end = last_hkppp if last_hkppp is not None else first_hkost

    display_lines: list[ParsedLineModel] = []
    for line in result.parsed:
        if line.line_number in parts_by_hkost:
            part = parts_by_hkost[line.line_number]
            display_lines.append(
                ParsedLineModel(
                    line_number=line.line_number,
                    raw=f"N{part.part_line} PART",
                    command="PART",
                    description="Part definition",
                    arguments=[],
                    fields=[
                        ParsedFieldModel(name="command", value="PART"),
                        ParsedFieldModel(name="part_line", value=part.part_line),
                        ParsedFieldModel(name="profile_line", value=part.profile_line),
                        ParsedFieldModel(name="start_line", value=part.start_line),
                        ParsedFieldModel(name="end_line", value=part.end_line),
                    ],
                )
            )
            continue

        if first_hkost <= line.line_number <= cutoff_end:
            continue

        display_lines.append(
            ParsedLineModel(
                line_number=line.line_number,
                raw=line.raw,
                command=line.command,
                description=line.description,
                arguments=line.arguments,
                fields=[
                    ParsedFieldModel(name="command", value=line.command),
                    *[ParsedFieldModel(name=name, value=value) for name, value in line.params.items()],
                ],
            )
        )

    return display_lines


def _find_last_hkppp_line(lines: list[str], start_line: int) -> int | None:
    last = None
    for idx, line in enumerate(lines, start=1):
        if idx < start_line:
            continue
        if "HKPPP" in line.upper():
            last = idx
    return last
