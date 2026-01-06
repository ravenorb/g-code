from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from uuid import uuid4

from .settings import AppSettings

logger = logging.getLogger(__name__)


MACRO_PATTERN = re.compile(r"^(?:N(?P<label>\d+)\s+)?(?P<cmd>HK[A-Z]+)\((?P<body>.*)\)$", re.IGNORECASE)
GCODE_PATTERN = re.compile(r"^(?:N\d+\s+)?(?P<cmd>G\d+)\s+(?P<params>.*)$", re.IGNORECASE)
M_PATTERN = re.compile(r"^(?:N\d+\s+)?(?P<cmd>M\d+)")


@dataclass
class Point:
    x: float
    y: float
    z: float = 0.0

    def to_dict(self) -> Dict[str, float]:
        return {"x": self.x, "y": self.y, "z": self.z}

    def translated(self, dx: float, dy: float) -> "Point":
        return Point(x=self.x + dx, y=self.y + dy, z=self.z)


@dataclass
class Motion:
    cmd: str
    params: Dict[str, float]

    def to_dict(self) -> Dict[str, float]:
        return {"cmd": self.cmd, **self.params}

    def translated(self, dx: float, dy: float) -> "Motion":
        updated = {k: v for k, v in self.params.items()}
        if "X" in updated:
            updated["X"] += dx
        if "Y" in updated:
            updated["Y"] += dy
        return Motion(cmd=self.cmd, params=updated)


@dataclass
class Operation:
    operation_id: int
    anchor: Point
    technology: Optional[int] = None
    cut_type: str = "contour"
    kerf_mode: str = "compensated"
    start: Optional[Point] = None
    lead_target: Optional[Point] = None
    motions: List[Motion] = field(default_factory=list)
    sequence: List[str] = field(default_factory=list)
    status: str = "registered"
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    saw_hksto: bool = False

    def to_meta(self) -> Dict[str, object]:
        return {
            "operationId": self.operation_id,
            "technology": self.technology,
            "anchor": self.anchor.to_dict(),
            "cut": {
                "type": self.cut_type,
                "kerfMode": self.kerf_mode,
                "start": self.start.to_dict() if self.start else None,
                "leadTarget": self.lead_target.to_dict() if self.lead_target else None,
                "sequence": self.sequence,
                "motion": [motion.to_dict() for motion in self.motions],
            },
            "status": self.status,
            "errors": self.errors,
            "warnings": self.warnings,
        }


@dataclass
class ParseSummary:
    errors: List[str]
    warnings: List[str]
    parts: int
    setups: List[Dict[str, float]]

    def to_meta(self) -> Dict[str, object]:
        return {
            "parts": self.parts,
            "setups": self.setups,
            "warnings": self.warnings,
            "errors": self.errors,
        }


@dataclass
class ParseResult:
    job: Dict[str, object]
    operations: List[Operation]
    summary: ParseSummary

    def to_meta(self, *, description: str, file_id: str, original_name: str, stored_path: Path, meta_path: Path) -> Dict[str, object]:
        return {
            "id": file_id,
            "description": description,
            "originalName": original_name,
            "paths": {"original": str(stored_path), "meta": str(meta_path)},
            "job": self.job,
            "operations": [op.to_meta() for op in self.operations],
            "summary": self.summary.to_meta(),
        }


class HKMetaParser:
    def __init__(self, settings: AppSettings):
        self.settings = settings

    def parse_text(self, text: str) -> ParseResult:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        errors: List[str] = []
        warnings: List[str] = []
        material = {"library": None, "name": None, "processClass": None}
        init = {"mode": None, "sheetX": None, "sheetY": None}
        operations: List[Operation] = []
        op_index: Dict[int, Operation] = {}
        current_op: Optional[Operation] = None
        hkend_seen = False
        m30_seen = False
        cut_started = False

        for raw in lines:
            macro_match = MACRO_PATTERN.match(raw)
            gcode_match = GCODE_PATTERN.match(raw) if not macro_match else None
            m_match = M_PATTERN.match(raw) if not macro_match else None

            if macro_match:
                cmd = macro_match.group("cmd").upper()
                params = self._parse_macro_params(macro_match.group("body"))
                if cmd == "HKLDB":
                    material = self._parse_material(params, errors)
                elif cmd == "HKINI":
                    init = self._parse_init(params, errors)
                elif cmd == "HKOST":
                    current_op, cut_started = self._handle_hkost(params, operations, op_index, errors)
                elif cmd == "HKPPP":
                    if current_op:
                        current_op.sequence.append("HKPPP")
                elif cmd == "HKSTR":
                    if current_op:
                        current_op.sequence.append("HKSTR")
                        start, lead = self._parse_start_and_lead(params)
                        current_op.start = start
                        current_op.lead_target = lead
                        current_op.status = "cutting"
                    else:
                        errors.append("HKSTR encountered before HKOST")
                elif cmd == "HKPIE":
                    if current_op:
                        current_op.sequence.append("HKPIE")
                elif cmd == "HKLEA":
                    if current_op:
                        current_op.sequence.append("HKLEA")
                elif cmd == "HKCUT":
                    if current_op:
                        current_op.sequence.append("HKCUT")
                        cut_started = True
                elif cmd == "HKSTO":
                    if current_op:
                        current_op.sequence.append("HKSTO")
                        cut_started = False
                        current_op.saw_hksto = True
                elif cmd == "HKPED":
                    if current_op:
                        if not current_op.saw_hksto:
                            current_op.errors.append("HKPED encountered before HKSTO")
                        current_op.sequence.append("HKPED")
                        current_op.status = "complete"
                    else:
                        errors.append("HKPED encountered before HKOST")
                elif cmd == "HKEND":
                    hkend_seen = True
                else:
                    logger.debug("Unhandled macro: %s", cmd)
                continue

            if gcode_match and current_op:
                cmd = gcode_match.group("cmd").upper()
                params = self._parse_gcode_params(gcode_match.group("params"))
                if not cut_started:
                    current_op.errors.append("Cutting motion encountered before HKCUT")
                current_op.motions.append(Motion(cmd=cmd, params=params))
                continue

            if m_match:
                cmd = m_match.group("cmd").upper()
                if cmd == "M30":
                    if not hkend_seen:
                        errors.append("M30 encountered before HKEND")
                    m30_seen = True
                continue

        if current_op and current_op.status != "complete":
            warnings.append(f"Operation {current_op.operation_id} did not reach HKPED")

        if not hkend_seen:
            errors.append("HKEND was not found before program end")

        self._apply_technology(operations, material, init, errors)

        summarized_errors = errors + [f"Operation {op.operation_id}: {err}" for op in operations for err in op.errors]
        summarized_warnings = warnings + [f"Operation {op.operation_id}: {warn}" for op in operations for warn in op.warnings]

        job = {"material": material, "init": init, "operations": [op.to_meta() for op in operations]}
        summary = ParseSummary(errors=summarized_errors, warnings=summarized_warnings, parts=len(operations), setups=self._build_setups(init))
        return ParseResult(job=job, operations=operations, summary=summary)

    def save_upload(self, *, filename: str, content: bytes, description: str) -> Dict[str, object]:
        if len(content) > self.settings.max_upload_bytes:
            raise ValueError("Upload exceeds size limit")
        safe_name = Path(filename).name
        if Path(safe_name).suffix.lower() not in self.settings.allowed_extensions:
            raise ValueError("File extension not allowed")

        file_id = uuid4().hex
        stored_name = f"{file_id}-{safe_name}"
        stored_path = self.settings.nas_path / stored_name
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        stored_path.write_bytes(content)

        parsed = self.parse_text(content.decode("utf-8", errors="ignore"))
        meta_path = stored_path.with_suffix(stored_path.suffix + ".meta.json")
        meta = parsed.to_meta(description=description, file_id=file_id, original_name=safe_name, stored_path=stored_path, meta_path=meta_path)
        meta_path.write_text(json.dumps(meta, indent=2))
        logger.info("Saved upload %s to %s", file_id, stored_path)
        return meta

    def list_files(self) -> List[Dict[str, object]]:
        if not self.settings.nas_path.exists():
            return []
        items: List[Dict[str, object]] = []
        for meta_path in sorted(self.settings.nas_path.glob("*.meta.json")):
            try:
                meta = json.loads(meta_path.read_text())
                items.append(
                    {
                        "id": meta.get("id"),
                        "description": meta.get("description"),
                        "summary": meta.get("summary"),
                        "originalName": meta.get("originalName"),
                        "paths": meta.get("paths"),
                    }
                )
            except json.JSONDecodeError:
                logger.warning("Skipping invalid meta file %s", meta_path)
        return items

    def extract_part(self, *, file_id: str, part_id: str | int) -> Dict[str, object]:
        meta = self._load_meta(file_id)
        operations = meta.get("operations", [])
        operation = self._select_operation(operations, part_id)
        if not operation:
            raise ValueError("Requested part not found")
        job = meta.get("job", {})
        material = job.get("material", {})
        init = job.get("init", {})
        motions = [self._motion_from_dict(m) for m in operation.get("cut", {}).get("motion", [])]
        start = self._point_from_dict(operation.get("cut", {}).get("start"))
        lead = self._point_from_dict(operation.get("cut", {}).get("leadTarget"))
        anchor = self._point_from_dict(operation.get("anchor"))
        dx, dy, bbox = self._compute_translation(motions=motions, start=start, lead=lead)
        translated_motions = [m.translated(dx, dy) for m in motions]
        translated_start = start.translated(dx, dy) if start else None
        translated_lead = lead.translated(dx, dy) if lead else None
        translated_anchor = anchor.translated(dx, dy) if anchor else anchor
        span_x = bbox[2] - bbox[0]
        span_y = bbox[3] - bbox[1]
        sheet_x = span_x + (self.settings.sheet_margin * 2)
        sheet_y = span_y + (self.settings.sheet_margin * 2)
        program_lines = self._build_program(
            material=material,
            init_mode=init.get("mode"),
            sheet_x=sheet_x,
            sheet_y=sheet_y,
            anchor=translated_anchor,
            operation=operation,
            motions=translated_motions,
            start=translated_start,
            lead=translated_lead,
        )

        original_path = Path(meta["paths"]["original"])
        extracted_name = f"{original_path.stem}-part-{operation['operationId']}-{uuid4().hex[:8]}{original_path.suffix}"
        extracted_path = original_path.with_name(extracted_name)
        extracted_path.write_text("\n".join(program_lines) + "\n")

        extracted_meta = {
            **meta,
            "id": f"{meta['id']}-part-{operation['operationId']}",
            "paths": {"original": str(extracted_path), "meta": str(extracted_path) + ".meta.json"},
            "job": {
                "material": material,
                "init": {"mode": init.get("mode"), "sheetX": sheet_x, "sheetY": sheet_y},
                "operations": [operation],
            },
            "summary": {
                "parts": 1,
                "setups": [{"mode": init.get("mode"), "sheetX": sheet_x, "sheetY": sheet_y}],
                "warnings": [],
                "errors": [],
            },
        }
        Path(extracted_meta["paths"]["meta"]).write_text(json.dumps(extracted_meta, indent=2))
        logger.info("Extracted part %s from %s", part_id, file_id)
        return {
            "file": str(extracted_path),
            "meta": extracted_meta,
            "downloadUrl": f"/nas/{extracted_path.name}",
        }

    def _parse_macro_params(self, body: str) -> List[str]:
        parts = []
        current = ""
        in_string = False
        for char in body:
            if char == '"':
                in_string = not in_string
                current += char
                continue
            if char == "," and not in_string:
                parts.append(current.strip())
                current = ""
            else:
                current += char
        if current:
            parts.append(current.strip())
        return parts

    def _parse_material(self, params: List[str], errors: List[str]) -> Dict[str, object]:
        material = {"library": None, "name": None, "processClass": None}
        try:
            material["library"] = int(float(params[0]))
            material["name"] = params[1].strip('"')
            material["processClass"] = int(float(params[2]))
        except (IndexError, ValueError) as exc:
            errors.append(f"Invalid HKLDB parameters: {exc}")
        return material

    def _parse_init(self, params: List[str], errors: List[str]) -> Dict[str, object]:
        init = {"mode": None, "sheetX": None, "sheetY": None}
        try:
            init["mode"] = int(float(params[0]))
            init["sheetX"] = float(params[1])
            init["sheetY"] = float(params[2])
        except (IndexError, ValueError) as exc:
            errors.append(f"Invalid HKINI parameters: {exc}")
        return init

    def _handle_hkost(self, params: List[str], operations: List[Operation], op_index: Dict[int, Operation], errors: List[str]) -> Tuple[Optional[Operation], bool]:
        try:
            anchor = Point(float(params[0]), float(params[1]), float(params[2]))
            op_id = int(float(params[3]))
            tech = int(float(params[4])) if len(params) > 4 and params[4] else None
        except (IndexError, ValueError) as exc:
            errors.append(f"Invalid HKOST parameters: {exc}")
            return None, False
        if op_id in op_index:
            errors.append(f"Duplicate operationId detected: {op_id}")
            return op_index[op_id], False
        op = Operation(operation_id=op_id, anchor=anchor, technology=tech)
        op.sequence.append("HKOST")
        operations.append(op)
        op_index[op_id] = op
        return op, False

    def _parse_start_and_lead(self, params: List[str]) -> Tuple[Point, Point]:
        # HKSTR(a,b,startX,startY,?,leadX,leadY,?)
        start_x = float(params[2]) if len(params) > 2 else 0.0
        start_y = float(params[3]) if len(params) > 3 else 0.0
        lead_x = float(params[5]) if len(params) > 5 else start_x
        lead_y = float(params[6]) if len(params) > 6 else start_y
        return Point(start_x, start_y, 0.0), Point(lead_x, lead_y, 0.0)

    def _parse_gcode_params(self, text: str) -> Dict[str, float]:
        params: Dict[str, float] = {}
        for token in text.split():
            if len(token) < 2:
                continue
            name = token[0].upper()
            try:
                value = float(token[1:])
                params[name] = value
            except ValueError:
                continue
        return params

    def _build_setups(self, init: Dict[str, object]) -> List[Dict[str, float]]:
        if init.get("sheetX") is None or init.get("sheetY") is None:
            return []
        return [
            {
                "mode": init.get("mode"),  # type: ignore[arg-type]
                "sheetX": init["sheetX"],  # type: ignore[index]
                "sheetY": init["sheetY"],  # type: ignore[index]
            }
        ]

    def _apply_technology(self, operations: List[Operation], material: Dict[str, object], init: Dict[str, object], errors: List[str]) -> None:
        material_name = material.get("name") or "unknown"
        material_table = self.settings.technology.table.get(material_name, {})
        thickness_key = f"mode-{init.get('mode')}" if init.get("mode") is not None else "default"
        for op in operations:
            op.sequence = list(dict.fromkeys(op.sequence))  # ensure ordering uniqueness
            lookup = material_table.get(thickness_key) or material_table.get("default") or {}
            tech = lookup.get(op.cut_type) if lookup else None
            if tech is None:
                op.errors.append(f"Technology mapping not found for {material_name}/{thickness_key}/{op.cut_type}")
                errors.append(f"Technology mapping missing for operation {op.operation_id}")
            else:
                op.technology = tech

    def _load_meta(self, file_id: str) -> Dict[str, object]:
        if not self.settings.nas_path.exists():
            raise ValueError("No uploads have been stored yet")  # pragma: no cover - defensive
        for meta_path in self.settings.nas_path.glob("*.meta.json"):
            try:
                meta = json.loads(meta_path.read_text())
            except json.JSONDecodeError:
                continue
            if meta.get("id") == file_id:
                return meta
        raise ValueError("Meta file not found for requested id")

    def _select_operation(self, operations: List[Dict[str, object]], part_id: str | int) -> Optional[Dict[str, object]]:
        try:
            op_id = int(part_id)
        except (ValueError, TypeError):
            op_id = None
        if op_id is not None:
            for op in operations:
                if op.get("operationId") == op_id:
                    return op
        # Fallback to index
        if isinstance(part_id, int) and 0 <= part_id < len(operations):
            return operations[part_id]
        return None

    def _motion_from_dict(self, payload: Dict[str, object]) -> Motion:
        params = {k.upper(): float(v) for k, v in payload.items() if k not in {"cmd"} and v is not None}
        return Motion(cmd=str(payload.get("cmd", "G1")), params=params)

    def _point_from_dict(self, payload: Optional[Dict[str, object]]) -> Point:
        payload = payload or {}
        return Point(float(payload.get("x", 0.0)), float(payload.get("y", 0.0)), float(payload.get("z", 0.0)))

    def _compute_translation(self, motions: List[Motion], start: Optional[Point], lead: Optional[Point]) -> Tuple[float, float, Tuple[float, float, float, float]]:
        xs: List[float] = []
        ys: List[float] = []
        if start:
            xs.append(start.x)
            ys.append(start.y)
        if lead:
            xs.append(lead.x)
            ys.append(lead.y)
        for motion in motions:
            if "X" in motion.params:
                xs.append(motion.params["X"])
            if "Y" in motion.params:
                ys.append(motion.params["Y"])
        min_x = min(xs) if xs else 0.0
        min_y = min(ys) if ys else 0.0
        max_x = max(xs) if xs else min_x
        max_y = max(ys) if ys else min_y
        return -min_x, -min_y, (min_x, min_y, max_x, max_y)

    def _build_program(self, *, material: Dict[str, object], init_mode: object, sheet_x: float, sheet_y: float, anchor: Point, operation: Dict[str, object], motions: List[Motion], start: Optional[Point], lead: Optional[Point]) -> List[str]:
        lines = [
            "; Extracted HK program",
            "N1",
            f"HKLDB({material.get('library', 0)},\"{material.get('name', 'UNKNOWN')}\",{material.get('processClass', 0)},0,0,0)",
            f"HKINI({init_mode or 0},{sheet_x:.4f},{sheet_y:.4f},0,0,0)",
            f"N{operation['operationId']} HKOST({anchor.x:.4f},{anchor.y:.4f},{anchor.z:.4f},{operation['operationId']},{operation.get('technology') or 0},0,0,0)",
            "HKPPP",
        ]
        if start and lead:
            lines.append(f"N{operation['operationId'] + 1} HKSTR(0,1,{start.x:.4f},{start.y:.4f},0,{lead.x:.4f},{lead.y:.4f},0)")
        lines.extend(["HKPIE(0,0,0)", "HKLEA(0,0,0)", "HKCUT(0,0,0)"])
        for motion in motions:
            param_text = " ".join(f"{k}{v:.4f}" for k, v in sorted(motion.params.items()))
            lines.append(f"{motion.cmd} {param_text}".strip())
        lines.extend(["HKSTO(0,0,0)", "HKPED(0,0,0)", "HKEND(0,0,0)", "M30"])
        return lines
