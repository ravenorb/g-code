from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .config import ServiceConfig
from .diagnostics import ValidationService, hash_payload
from .parser import load_from_bytes
from .storage import extract_sheet_setup


MATCH_THRESHOLD = 0.72
MATCH_STORE_NAME = "correct-matches.json"


def load_sample_index(config: ServiceConfig, validator: ValidationService) -> Dict[str, Any]:
    sample_root = _get_sample_root()
    mpf_dir = sample_root / "mpf"
    pdf_dir = sample_root / "pdfs"
    mpf_files = _list_files(mpf_dir, {".mpf"})
    pdf_files = _list_files(pdf_dir, {".pdf"})
    signature = _build_signature(mpf_files, pdf_files)

    overrides = _load_match_overrides(config)

    cache_dir = config.storage_root / "samples"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "sample-index.json"
    cached = _load_cache(cache_path)
    if cached and cached.get("signature") == signature:
        return cached

    pdf_index = _build_pdf_index(pdf_files)
    pdf_catalog = [
        {
            **_file_info(path),
            "normalized": pdf_index[path.name]["normalized"],
            "tokens": pdf_index[path.name]["tokens"],
        }
        for path in pdf_files
    ]
    matches: List[Dict[str, Any]] = []
    matched_pdf_names: set[str] = set()
    for mpf_path in mpf_files:
        parsed = _parse_mpf(mpf_path, validator)
        best_pdf, suggestions = _match_pdf(mpf_path.name, pdf_index)
        matched_pdf = None
        match_score = None
        match_source = "auto"
        auto_match = None
        if best_pdf:
            matched_pdf = _file_info(best_pdf)
            match_score = suggestions[0]["score"] if suggestions else None
            if match_score is not None and match_score >= MATCH_THRESHOLD:
                matched_pdf_names.add(best_pdf.name)
            auto_match = {
                **matched_pdf,
                "normalized": pdf_index[best_pdf.name]["normalized"],
                "tokens": pdf_index[best_pdf.name]["tokens"],
                "score": match_score,
            }
        correct_match = _resolve_correct_match(mpf_path.name, overrides, pdf_index)
        if correct_match:
            match_source = "manual"
            matched_pdf = correct_match.get("file_info")
            match_score = correct_match.get("score")
            if matched_pdf and matched_pdf.get("filename") in pdf_index:
                matched_pdf_names.add(matched_pdf["filename"])
        matches.append(
            {
                "mpf": _file_info(mpf_path),
                "mpf_normalized": _normalize_name(mpf_path.name),
                "mpf_tokens": _tokenize(mpf_path.name),
                "pdf": matched_pdf,
                "auto_match": auto_match,
                "match_score": match_score,
                "match_threshold": MATCH_THRESHOLD,
                "match_source": match_source,
                "correct_match": correct_match,
                "suggestions": suggestions,
                "summary": parsed["summary"],
                "parts": parsed["parts"],
                "setup": parsed["setup"],
                "job_id": parsed["job_id"],
                "pair_insights": _build_pair_insights(mpf_path.name, matched_pdf),
            }
        )

    unmatched_pdfs = [
        _file_info(path) for path in pdf_files if path.name not in matched_pdf_names
    ]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "signature": signature,
        "sample_root": str(sample_root),
        "matches": matches,
        "pdfs": pdf_catalog,
        "unmatched_pdfs": unmatched_pdfs,
    }
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _get_sample_root() -> Path:
    return Path(__file__).resolve().parents[2] / "samples"


def _list_files(directory: Path, extensions: set[str]) -> List[Path]:
    if not directory.exists():
        return []
    return sorted(
        [path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in extensions],
        key=lambda path: path.name.lower(),
    )


def _build_signature(mpf_files: List[Path], pdf_files: List[Path]) -> str:
    entries: List[str] = []
    for path in sorted(mpf_files + pdf_files, key=lambda p: p.name.lower()):
        stat = path.stat()
        entries.append(f"{path.name}:{stat.st_size}:{int(stat.st_mtime)}")
    return hashlib.sha1("|".join(entries).encode("utf-8")).hexdigest()


def _load_cache(cache_path: Path) -> Dict[str, Any] | None:
    if not cache_path.exists():
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _file_info(path: Path) -> Dict[str, Any]:
    stat = path.stat()
    return {
        "filename": path.name,
        "relative_path": str(path.relative_to(_get_sample_root())),
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def _normalize_name(name: str) -> str:
    base = Path(name).stem.lower()
    base = re.sub(r"[^a-z0-9]+", " ", base)
    return " ".join(base.split())


def _tokenize(name: str) -> List[str]:
    return _normalize_name(name).split()


def _score_pair(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    left_norm = _normalize_name(left)
    right_norm = _normalize_name(right)
    if left_norm == right_norm:
        return 1.0
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    token_overlap = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    sequence_ratio = _sequence_ratio(left_norm, right_norm)
    prefix_boost = 0.1 if left_norm.startswith(right_norm) or right_norm.startswith(left_norm) else 0.0
    score = (0.6 * sequence_ratio) + (0.3 * token_overlap) + prefix_boost
    return min(score, 1.0)


def _sequence_ratio(left: str, right: str) -> float:
    import difflib

    return difflib.SequenceMatcher(None, left, right).ratio()


def _build_pdf_index(pdf_files: List[Path]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for path in pdf_files:
        index[path.name] = {
            "path": path,
            "normalized": _normalize_name(path.name),
            "tokens": _tokenize(path.name),
        }
    return index


def _match_pdf(mpf_name: str, pdf_index: Dict[str, Dict[str, Any]]) -> Tuple[Path | None, List[Dict[str, Any]]]:
    scored: List[Tuple[str, float]] = []
    for pdf_name, info in pdf_index.items():
        score = _score_pair(mpf_name, pdf_name)
        scored.append((pdf_name, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    suggestions = [
        {"filename": pdf_name, "score": float(f"{score:.3f}")}
        for pdf_name, score in scored[:3]
    ]
    best = scored[0][0] if scored else None
    best_path = pdf_index[best]["path"] if best else None
    return best_path, suggestions


def _parse_mpf(path: Path, validator: ValidationService) -> Dict[str, Any]:
    content = path.read_bytes()
    job_id = hash_payload(content)[:12]
    lines = load_from_bytes(content)
    result = validator.validate_lines(job_id=job_id, lines=lines)
    setup = extract_sheet_setup(lines)
    parts = [
        {
            "part_number": part.part_number,
            "part_line": part.part_line,
            "hkost_line": part.hkost_line,
            "profile_line": part.profile_line,
            "start_line": part.start_line,
            "end_line": part.end_line,
            "contours": part.contours,
            "anchor_x": part.anchor_x,
            "anchor_y": part.anchor_y,
        }
        for part in result.parts
    ]
    summary = {
        "errors": result.summary["errors"],
        "warnings": result.summary["warnings"],
        "lines": result.summary["lines"],
        "parts": len(result.parts),
    }
    if setup:
        summary["sheetX"] = setup.get("sheetX")
        summary["sheetY"] = setup.get("sheetY")
    return {
        "job_id": job_id,
        "summary": summary,
        "parts": parts,
        "setup": setup,
    }


def _matches_path(config: ServiceConfig) -> Path:
    return config.storage_root / "samples" / MATCH_STORE_NAME


def _load_match_overrides(config: ServiceConfig) -> Dict[str, Any]:
    match_path = _matches_path(config)
    if not match_path.exists():
        return {"matches": {}}
    try:
        payload = json.loads(match_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"matches": {}}
    if not isinstance(payload, dict):
        return {"matches": {}}
    return payload


def save_match_override(
    config: ServiceConfig,
    mpf_filename: str,
    pdf_filename: str | None,
) -> Dict[str, Any]:
    safe_mpf = Path(mpf_filename).name
    safe_pdf = Path(pdf_filename).name if pdf_filename else None
    match_path = _matches_path(config)
    match_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _load_match_overrides(config)
    matches = payload.setdefault("matches", {})
    if safe_pdf:
        matches[safe_mpf] = {
            "pdf": safe_pdf,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    else:
        matches.pop(safe_mpf, None)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    match_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _resolve_correct_match(
    mpf_name: str,
    overrides: Dict[str, Any],
    pdf_index: Dict[str, Dict[str, Any]],
) -> Dict[str, Any] | None:
    matches = overrides.get("matches") if isinstance(overrides, dict) else None
    if not isinstance(matches, dict):
        return None
    entry = matches.get(mpf_name)
    if not isinstance(entry, dict):
        return None
    pdf_name = entry.get("pdf")
    if not pdf_name:
        return None
    pdf_info = None
    if pdf_name in pdf_index:
        pdf_path = pdf_index[pdf_name]["path"]
        pdf_info = {
            **_file_info(pdf_path),
            "normalized": pdf_index[pdf_name]["normalized"],
            "tokens": pdf_index[pdf_name]["tokens"],
        }
    score = _score_pair(mpf_name, pdf_name) if pdf_name else None
    return {
        "filename": pdf_name,
        "updated_at": entry.get("updated_at"),
        "missing": pdf_info is None,
        "score": score,
        "file_info": pdf_info,
    }


def _build_pair_insights(mpf_name: str, pdf_info: Dict[str, Any] | None) -> Dict[str, Any]:
    mpf_norm = _normalize_name(mpf_name)
    mpf_tokens = mpf_norm.split()
    pdf_name = pdf_info.get("filename") if pdf_info else ""
    pdf_norm = _normalize_name(pdf_name) if pdf_name else ""
    pdf_tokens = pdf_norm.split() if pdf_norm else []
    shared = sorted(set(mpf_tokens) & set(pdf_tokens))
    token_overlap = len(shared) / max(len(set(mpf_tokens) | set(pdf_tokens)), 1)
    sequence_ratio = _sequence_ratio(mpf_norm, pdf_norm) if pdf_norm else None
    return {
        "mpf_normalized": mpf_norm,
        "mpf_tokens": mpf_tokens,
        "pdf_normalized": pdf_norm,
        "pdf_tokens": pdf_tokens,
        "shared_tokens": shared,
        "token_overlap": token_overlap,
        "sequence_ratio": sequence_ratio,
    }
