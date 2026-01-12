from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from .config import DEFAULT_CONFIG, ServiceConfig
from .diagnostics import ValidationService, hash_payload
from .extract import extract_part_profile_program, extract_part_program
from .models import (
    ContourPlotModel,
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
    extra_contour_refs = _parse_extra_contours(extra_contours, validation.parts)
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
        if not extra_points:
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
                points=_translate_contour_points(extra_points[0], offset_x, offset_y),
            )
        )
    content = "\n".join(validation.raw_lines)
    profile_block = extract_part_profile_program(content, part.part_line).lines
    part_program = extract_part_program(
        content,
        part.part_line,
        extra_contours=[(ref.part_line, ref.contour_index) for ref in extra_contour_refs],
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
    )


@app.get("/jobs/{job_id}/parts/{part_number}/program")
async def part_program_download(
    job_id: str,
    part_number: int,
    extra_contours: Optional[str] = None,
    release_manager: ReleaseManager = Depends(get_release_manager),
    storage_manager: StorageManager = Depends(get_storage_manager),
) -> Response:
    validation = release_manager.get_validation(job_id)
    if validation is None:
        raise HTTPException(status_code=404, detail="Job not found")

    part = next((p for p in validation.parts if p.part_number == part_number), None)
    if part is None:
        raise HTTPException(status_code=404, detail="Part not found")

    extra_contour_refs = _parse_extra_contours(extra_contours, validation.parts)
    content = "\n".join(validation.raw_lines)
    part_program = extract_part_program(
        content,
        part.part_line,
        extra_contours=[(ref.part_line, ref.contour_index) for ref in extra_contour_refs],
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
          <a href="/?job_id={job_id}">← Return to sheet</a>
          <span>|</span>
          <a href="/">Upload new file</a>
        </nav>
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

          function getExtraContours() {{
            return contourInputs.map((input) => input.value.trim()).filter(Boolean);
          }}

          function buildQueryString(entries) {{
            if (!entries.length) return "";
            const query = new URLSearchParams();
            query.set("extra_contours", entries.join(","));
            return `?${{query.toString()}}`;
          }}

          function updateDownloadLink(entries) {{
            const queryString = buildQueryString(entries);
            downloadLink.href = `/jobs/{job_id}/parts/{part_number}/program${{queryString}}`;
          }}

          async function loadPart(entries = getExtraContours()) {{
            const queryString = buildQueryString(entries);
            updateDownloadLink(entries);
            const resp = await fetch(`/jobs/{job_id}/parts/{part_number}${{queryString}}`);
            if (!resp.ok) {{
              plotInfo.textContent = "Unable to load part details.";
              return;
            }}
            const data = await resp.json();
            profileCode.textContent = (data.profile_block || []).join("\\n");
            partProgram.textContent = (data.part_program || []).join("\\n");
            renderPlot(data.plot_contours || data.plot_points || []);
            contourStatus.textContent = entries.length
              ? `Including extra contours: ${{entries.join(", ")}}`
              : "No extra contours selected.";
          }}

          function renderPlot(contours) {{
            const ctx = plotCanvas.getContext("2d");
            ctx.clearRect(0, 0, plotCanvas.width, plotCanvas.height);
            const normalizedContours = Array.isArray(contours[0]?.points)
              ? contours
              : contours.map((points, index) => ({{ label: String(index + 1), points }}));
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
            normalizedContours.forEach((contour, contourIndex) => {{
              if (!contour.points.length) return;
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
              ctx.fillText(contour.label || String(contourIndex + 1), labelX + 4, labelY - 4);
            }});
            plotInfo.textContent =
              "Distance: X " +
              rangeX +
              ", Y " +
              rangeY;
          }}

          function syncInputsFromUrl() {{
            const params = new URLSearchParams(window.location.search);
            const raw = params.get("extra_contours");
            if (!raw) return;
            raw.split(",").slice(0, contourInputs.length).forEach((value, index) => {{
              contourInputs[index].value = value.trim();
            }});
          }}

          applyContours.addEventListener("click", () => {{
            const entries = getExtraContours();
            const queryString = buildQueryString(entries);
            const url = new URL(window.location.href);
            url.search = queryString ? queryString.slice(1) : "";
            window.history.replaceState({{}}, "", url);
            loadPart(entries);
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
        if len(refs) >= 5:
            break
    return refs


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
