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
from .extract import extract_part_profile_program, extract_part_program
from .models import (
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
from .parser import build_part_plot_points, extract_part_block, load_from_bytes
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
    payload = _build_validation_payload(result, setup=setup)
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


@app.get("/jobs/{job_id}/parts/{part_number}", response_model=PartDetailModel)
async def part_detail(
    job_id: str,
    part_number: int,
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
    content = "\n".join(validation.raw_lines)
    profile_block = extract_part_profile_program(content, part.part_line).lines
    part_program = extract_part_program(content, part.part_line).lines
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
        part_program=part_program,
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
          .card {{
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 1.5rem;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
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
        <a href="/">← Back to upload</a>
        <h1>Part {part_number}</h1>
        <div class="card">
          <h2>Geometry</h2>
          <canvas id="plot" width="720" height="420"></canvas>
          <p id="plot-info"></p>
        </div>
        <div class="card">
          <h2>Part Profile Code</h2>
          <pre id="profile-code"></pre>
        </div>
        <div class="card">
          <h2>Standalone Part Program</h2>
          <pre id="part-program"></pre>
        </div>
        <script>
          const plotCanvas = document.getElementById("plot");
          const plotInfo = document.getElementById("plot-info");
          const profileCode = document.getElementById("profile-code");
          const partProgram = document.getElementById("part-program");

          async function loadPart() {{
            const resp = await fetch("/jobs/{job_id}/parts/{part_number}");
            if (!resp.ok) {{
              plotInfo.textContent = "Unable to load part details.";
              return;
            }}
            const data = await resp.json();
            profileCode.textContent = (data.profile_block || []).join("\\n");
            partProgram.textContent = (data.part_program || []).join("\\n");
            renderPlot(data.plot_points || []);
          }}

          function renderPlot(points) {{
            const ctx = plotCanvas.getContext("2d");
            ctx.clearRect(0, 0, plotCanvas.width, plotCanvas.height);
            if (!points.length) {{
              plotInfo.textContent = "No plot data found for this part.";
              return;
            }}
            const flatPoints = points.flat();
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
            points.forEach((contour, contourIndex) => {{
              if (!contour.length) return;
              ctx.beginPath();
              contour.forEach((point, index) => {{
                const x = (point[0] - minX) * scale + padding;
                const y = (maxY - point[1]) * scale + padding;
                if (index === 0) {{
                  ctx.moveTo(x, y);
                }} else {{
                  ctx.lineTo(x, y);
                }}
              }});
              ctx.stroke();
              const centroid = contour.reduce(
                (acc, point) => {{
                  acc.x += point[0];
                  acc.y += point[1];
                  return acc;
                }},
                {{ x: 0, y: 0 }}
              );
              const count = contour.length || 1;
              const labelX = ((centroid.x / count) - minX) * scale + padding;
              const labelY = (maxY - centroid.y / count) * scale + padding;
              ctx.fillText(String(contourIndex + 1), labelX + 4, labelY - 4);
            }});
            plotInfo.textContent =
              "Bounds: X " +
              minX +
              "→" +
              maxX +
              ", Y " +
              minY +
              "→" +
              maxY;
          }}

          loadPart();
        </script>
      </body>
    </html>
    """
    return HTMLResponse(html)


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
